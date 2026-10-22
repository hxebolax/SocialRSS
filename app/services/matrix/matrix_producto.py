# -*- coding: utf-8 -*-
"""Producto de Matrix (tanda M5): búsqueda FTS, poda de historial, moderación con power
levels legibles, directorio público de salas y espacios.
"""
from datetime import datetime, timedelta, timezone

from app.core.config_manager import config
from app.services.matrix.matrix_common import (
    Any, Dict, Optional, List, db, log, MatrixError, db_id_sala, q_seg)


class _ProductoMixin:
    # ---------------- Reportar (moderación abierta a cualquier usuario) ----------------
    def reportar_evento(self, account_id: int, room_id: str, event_id: str, motivo: str = ""):
        """Reporta un mensaje a los administradores del servidor (spec: rooms/{id}/report/{event}).
        score -100 = lo más ofensivo. Cualquier usuario puede reportar (no solo administradores)."""
        if not event_id:
            raise MatrixError(_("Ese mensaje no se puede reportar."))
        ruta = ("/_matrix/client/v3/rooms/" + q_seg(room_id) + "/report/" + q_seg(event_id))
        self._api_http(account_id, "POST", ruta, {"reason": motivo or "", "score": -100})

    def reportar_sala(self, account_id: int, room_id: str, motivo: str = ""):
        """Reporta una SALA entera a los administradores del servidor (MSC4151, estable en Matrix
        1.13). Si el servidor no lo soporta, devuelve un error claro del propio servidor."""
        ruta = "/_matrix/client/v3/rooms/" + q_seg(room_id) + "/report"
        self._api_http(account_id, "POST", ruta, {"reason": motivo or ""})

    # ---------------- Mensajes fijados (pinned events) ----------------
    def eventos_fijados(self, account_id: int, room_id: str) -> List[str]:
        """event_ids fijados en la sala (estado m.room.pinned_events). [] si no hay o no se puede."""
        try:
            cliente = self._cliente_conectado(account_id)
            from nio.responses import RoomGetStateEventResponse
            r = self._run(cliente.room_get_state_event(room_id, "m.room.pinned_events"), timeout=20.0)
            if isinstance(r, RoomGetStateEventResponse) and isinstance(getattr(r, "content", None), dict):
                return [e for e in (r.content.get("pinned") or []) if e]
        except Exception as e:
            log.debug(f"Matrix: no se pudieron leer los mensajes fijados: {e}")
        return []

    def fijar_evento(self, account_id: int, room_id: str, event_id: str, fijar: bool = True):
        """Fija o desfija un mensaje (lee-modifica-escribe m.room.pinned_events preservando el resto).
        Requiere permiso para enviar ese estado; el servidor lo valida."""
        if not event_id:
            return
        actuales = self.eventos_fijados(account_id, room_id)
        if fijar:
            if event_id in actuales:
                return
            actuales.append(event_id)
        else:
            if event_id not in actuales:
                return
            actuales = [e for e in actuales if e != event_id]
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomPutStateResponse
        resp = self._run(cliente.room_put_state(room_id, "m.room.pinned_events",
                                                {"pinned": actuales}), timeout=30.0)
        if not isinstance(resp, RoomPutStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    def mensajes_fijados_detalle(self, account_id: int, room_id: str) -> List[Dict[str, Any]]:
        """Los mensajes fijados con su texto legible (buscando primero en la BD local; si no está,
        se muestra el event_id). Para una lista accesible de 'mensajes fijados'."""
        salida = []
        for eid in self.eventos_fijados(account_id, room_id):
            fila = db.query_one(
                "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, date "
                "FROM matrix_mensajes WHERE account_id = ? AND event_id = ?", (account_id, eid))
            if fila:
                salida.append({"event_id": eid, "quien": fila[0] or "", "texto": fila[1] or "",
                               "date": fila[2] or ""})
            else:
                salida.append({"event_id": eid, "quien": "", "texto": _("(mensaje no descargado)"),
                               "date": ""})
        return salida

    # ---------------- Avatar de sala ----------------
    def cambiar_avatar_sala(self, account_id: int, room_id: str, ruta: str):
        """Sube una imagen y la fija como avatar de la SALA (estado m.room.avatar). Requiere permiso;
        el servidor lo valida."""
        import os
        import mimetypes
        if not ruta or not os.path.exists(ruta):
            raise MatrixError(_("La imagen no existe."))
        mime = mimetypes.guess_type(ruta)[0] or ""
        if not mime.startswith("image/"):
            raise MatrixError(_("El avatar debe ser una imagen (png, jpg...)."))
        cliente = self._cliente_conectado(account_id)
        from nio import UploadResponse
        from nio.responses import RoomPutStateResponse
        tam = os.path.getsize(ruta)

        async def _subir():
            with open(ruta, "rb") as f:
                resp, _claves = await cliente.upload(f, content_type=mime,
                                                     filename=os.path.basename(ruta), filesize=tam)
            return resp

        resp = self._run(_subir(), timeout=120.0)
        if not isinstance(resp, UploadResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        r2 = self._run(cliente.room_put_state(room_id, "m.room.avatar",
                                              {"url": resp.content_uri}), timeout=30.0)
        if not isinstance(r2, RoomPutStateResponse):
            raise MatrixError(getattr(r2, "message", None) or str(r2))

    # ---------------- Búsqueda (FTS con fallback LIKE) ----------------
    def buscar_mensajes_fts(self, account_id: int, query: str, limit: int = 100) -> List[Dict[str, Any]]:
        salida = []
        try:
            filas = db.query("""
                SELECT m.id, m.room_id, COALESCE(NULLIF(m.sender_nombre,''), m.sender),
                       m.texto, m.date
                FROM matrix_mensajes_fts f JOIN matrix_mensajes m ON m.id = f.msg_db_id
                WHERE matrix_mensajes_fts MATCH ? AND m.account_id = ?
                ORDER BY m.id DESC LIMIT ?
            """, (self._consulta_fts_mx(query), account_id, limit))
        except Exception:
            filas = db.query("""
                SELECT id, room_id, COALESCE(NULLIF(sender_nombre,''), sender), texto, date
                FROM matrix_mensajes
                WHERE account_id = ? AND texto LIKE ? AND tipo IN ('msg','accion','aviso')
                ORDER BY id DESC LIMIT ?
            """, (account_id, f"%{query}%", limit))
        for mid, room_id, quien, texto, fecha in filas:
            salida.append({"db_id": mid, "room_id": room_id, "quien": quien,
                           "texto": texto, "date": fecha})
        return salida

    @staticmethod
    def _consulta_fts_mx(query: str) -> str:
        terminos = [t.replace('"', "") for t in (query or "").split() if t.strip('"')]
        return " ".join(f'"{t}"' for t in terminos) if terminos else '""'

    # ---------------- Poda ----------------
    def podar_historial(self, account_id: Optional[int] = None) -> int:
        """Borra mensajes más antiguos que config matrix.history_days (0 = siempre)."""
        try:
            dias = int(config.get("matrix", "history_days", 0) or 0)
        except Exception:
            dias = 0
        if dias <= 0:
            return 0
        limite = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        try:
            cond, params = "date < ?", [limite]
            if account_id:
                cond += " AND account_id = ?"
                params.append(account_id)
            viejos = [f[0] for f in db.query(f"SELECT id FROM matrix_mensajes WHERE {cond}", tuple(params))]
            if not viejos:
                return 0
            for i in range(0, len(viejos), 500):
                lote = viejos[i:i + 500]
                ph = ",".join("?" * len(lote))
                with db.transaccion():
                    db.execute(f"DELETE FROM matrix_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                    db.execute(f"DELETE FROM matrix_mensajes WHERE id IN ({ph})", tuple(lote))
            log.info(f"Matrix: poda de historial, {len(viejos)} mensajes de más de {dias} días.")
            return len(viejos)
        except Exception as e:
            log.error(f"Matrix: error en la poda: {e}")
            return 0

    # ---------------- Exportar ----------------
    def exportar_sala(self, account_id: int, room_id: str, ruta: str) -> int:
        """Exporta el historial local de una sala a un .txt legible. Devuelve nº de mensajes."""
        fila = db.query_one("SELECT nombre FROM matrix_salas WHERE id = ?",
                            (db_id_sala(account_id, room_id),))
        nombre = (fila[0] if fila else room_id)
        filas = db.query("""
            SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto, tipo, date,
                   COALESCE(editado,0), COALESCE(reacciones,'')
            FROM matrix_mensajes WHERE account_id = ? AND room_id = ? ORDER BY id ASC
        """, (account_id, room_id))
        lineas = [_("Historial de {0} — exportado por SocialRSS Suite").format(nombre), ""]
        for quien, texto, tipo, fecha, editado, reacciones in filas:
            from app.services.matrix.matrix_common import hora_local
            marca = hora_local(fecha, con_fecha=True)
            extra = ""
            if editado:
                extra += " " + _("(editado)")
            if reacciones:
                extra += " " + _("[Reacciones: {0}]").format(reacciones)
            if tipo == "accion":
                lineas.append(f"[{marca}] * {quien} {texto}{extra}")
            else:
                lineas.append(f"[{marca}] <{quien}> {texto}{extra}")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write("\n".join(lineas))
        return len(filas)

    # ---------------- Moderación (power levels EN PALABRAS) ----------------
    @staticmethod
    def _rango_legible(nivel: int) -> str:
        if nivel >= 100:
            return _("administrador/a")
        if nivel >= 50:
            return _("moderador/a")
        if nivel > 0:
            return _("nivel {0}").format(nivel)
        return ""

    def miembros_con_rango(self, account_id: int, room_id: str) -> List[tuple]:
        """Miembros con su rango legible: [(user_id, nombre, rango, nivel)] ordenados por
        rango y nombre. El lector oye 'administradora', nunca '100'."""
        try:
            cliente = self._cliente_conectado(account_id)
        except MatrixError:
            return []
        # Lista de miembros AUTORITATIVA (vía /joined_members; funciona aunque el sync aún no los
        # haya traído). El rango se saca de los power levels del estado local (best-effort).
        miembros = self._miembros_unidos(account_id, room_id)
        if not miembros:
            return []
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
        pl = getattr(sala, "power_levels", None) if sala else None
        usuarios_pl = dict(getattr(pl, "users", {}) or {}) if pl else {}
        defecto = 0
        try:
            defecto = int(getattr(getattr(pl, "defaults", None), "users_default", 0) or 0)
        except Exception:
            pass
        salida = []
        for user_id, nombre in miembros:
            nivel = int(usuarios_pl.get(user_id, defecto) or 0)
            salida.append((user_id, nombre, self._rango_legible(nivel), nivel))
        salida.sort(key=lambda t: (-t[3], t[1].lower()))
        return salida

    def mi_nivel_en(self, account_id: int, room_id: str) -> int:
        yo = self._user_id(account_id)
        for uid, _n, _r, nivel in self.miembros_con_rango(account_id, room_id):
            if uid == yo:
                return nivel
        return 0

    def soy_admin_de_sala(self, account_id: int, room_id: str) -> bool:
        """True si en ESTA sala tengo poder para administrarla (cambiar sus ajustes: nombre, tema,
        reglas, cifrado...). Se resuelve con los power levels que ya tiene el cliente en memoria (sin
        red), preguntando a nio si puedo enviar el evento de estado m.room.name. Sirve para mostrar
        u ocultar las opciones de administración en el menú contextual de la sala."""
        try:
            cliente = self._cliente_conectado(account_id)
        except MatrixError:
            return False
        sala = (getattr(cliente, "rooms", {}) or {}).get(room_id)
        if sala is None:
            return False
        pl = getattr(sala, "power_levels", None)
        if pl is None:
            return False
        yo = self._user_id(account_id)
        try:
            return bool(pl.can_user_send_state(yo, "m.room.name"))
        except Exception:
            # Respaldo LOCAL (sin red): comparar mi nivel con el requerido, leyendo los power levels
            # que ya tiene el cliente en memoria (no usar mi_nivel_en, que ahora pide /joined_members).
            try:
                requerido = int(pl.get_state_event_required_level("m.room.name"))
            except Exception:
                requerido = 50
            try:
                defecto = int(getattr(getattr(pl, "defaults", None), "users_default", 0) or 0)
                mi_nivel = int((getattr(pl, "users", {}) or {}).get(yo, defecto) or 0)
            except Exception:
                mi_nivel = 0
            return mi_nivel >= requerido

    def accion_moderacion(self, account_id: int, room_id: str, user_id: str,
                          accion: str, motivo: str = ""):
        """invitar / expulsar / banear / desbanear. El servidor valida los permisos."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomKickResponse, RoomBanResponse, RoomUnbanResponse, RoomInviteResponse
        if accion == "invitar":
            resp = self._run(cliente.room_invite(room_id, user_id), timeout=30.0)
            ok = isinstance(resp, RoomInviteResponse)
        elif accion == "expulsar":
            resp = self._run(cliente.room_kick(room_id, user_id, reason=motivo or None), timeout=30.0)
            ok = isinstance(resp, RoomKickResponse)
        elif accion == "banear":
            resp = self._run(cliente.room_ban(room_id, user_id, reason=motivo or None), timeout=30.0)
            ok = isinstance(resp, RoomBanResponse)
        elif accion == "desbanear":
            resp = self._run(cliente.room_unban(room_id, user_id), timeout=30.0)
            ok = isinstance(resp, RoomUnbanResponse)
        else:
            raise MatrixError(f"Acción desconocida: {accion}")
        if not ok:
            raise MatrixError(getattr(resp, "message", None) or str(resp))

    # ---------------- Directorio público de salas ----------------
    def directorio_salas(self, account_id: int, filtro: str = "", limit: int = 100,
                         server: str = "") -> List[Dict[str, Any]]:
        """Salas públicas del servidor propio o de OTRO servidor federado (parámetro server,
        M7), con filtro del lado servidor si se indica."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import PublicRoomsResponse
        resp = self._run(cliente.list_public_rooms(limit=limit, server=(server or None),
                                                   filter_generic_search_term=filtro or None),
                         timeout=60.0)
        if not isinstance(resp, PublicRoomsResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        salida = []
        for c in (getattr(resp, "public_rooms", None) or []):
            get = (lambda k, d="": (c.get(k, d) if isinstance(c, dict) else getattr(c, k, d)))
            salida.append({
                "room_id": get("room_id"),
                "nombre": get("name") or get("canonical_alias") or get("room_id"),
                "alias": get("canonical_alias") or "",
                "tema": get("topic") or "",
                "miembros": int(get("num_joined_members", 0) or 0),
            })
        salida.sort(key=lambda d: -d["miembros"])
        return salida

    # ---------------- Espacios ----------------
    def salas_de_espacio(self, account_id: int, space_room_id: str) -> List[Dict[str, Any]]:
        """Salas hijas de un espacio (eventos m.space.child de su estado)."""
        cliente = self._cliente_conectado(account_id)
        from nio.responses import RoomGetStateResponse
        resp = self._run(cliente.room_get_state(space_room_id), timeout=45.0)
        if not isinstance(resp, RoomGetStateResponse):
            raise MatrixError(getattr(resp, "message", None) or str(resp))
        salida = []
        for ev in (getattr(resp, "events", None) or []):
            get = (lambda k, d=None: (ev.get(k, d) if isinstance(ev, dict) else getattr(ev, k, d)))
            if get("type") != "m.space.child":
                continue
            hijo = get("state_key") or ""
            contenido = get("content") or {}
            if not hijo or not (contenido.get("via") if isinstance(contenido, dict) else None):
                continue  # sin 'via' el hijo se considera quitado (spec: spaces)
            fila = db.query_one("SELECT nombre FROM matrix_salas WHERE id = ?",
                                (db_id_sala(account_id, hijo),))
            salida.append({"room_id": hijo, "nombre": (fila[0] if fila else hijo)})
        return salida
