# -*- coding: utf-8 -*-
"""MainFrame (mixin): funciones extra de Mastodon (solicitudes de notificación, info de instancia,
informe anual, contador de no leídas) y el área de ADMINISTRACIÓN de instancia.

El submenú "Administración" del menú Mastodon solo se construye si está activada la preferencia
mastodon.admin_tools; estos handlers son sus destinos.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import json
import logging
import wx

from app.core.speech import speak, notificar
from app.core.database import db
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import confirmar
from .mf_filtros_mastodon import _Mf_filtros_mastodon

log = logging.getLogger(__name__)


class _Mf_mastodon_extra(_Mf_filtros_mastodon):
    # (Hereda las acciones de los filtros del servidor: mf_filtros_mastodon.py.)
    # ---- helper común ----
    def _mst_cuenta_ok(self):
        if not self.active_mastodon_account_id:
            notificar(_("Activa primero una cuenta de Mastodon."))
            return False
        return True

    def _status_id_de_db(self, db_id):
        """Resuelve el status_id real de Mastodon a partir del id de la fila en la BD."""
        row = db.query_one("SELECT status_id FROM mastodon_statuses WHERE id = ?", (db_id,))
        return row[0] if row and row[0] else None

    # ===================== A · CITAS (quotes) =====================
    def _on_citas_toot(self, db_id):
        if not self._mst_cuenta_ok():
            return
        sid = self._status_id_de_db(db_id)
        if not sid:
            notificar(_("No se pudo identificar la publicación."))
            return
        cuenta = self.active_mastodon_account_id
        es_mia = bool(self._toot_es_mio(db_id)) if hasattr(self, "_toot_es_mio") else False
        speak(_("Buscando quién la cita..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("Nadie ha citado esta publicación todavía."))
                return
            opciones = ["@{0}: {1}".format(c["autor"], c["texto"]) for c in res]
            if not es_mia:
                # Publicación AJENA: se pueden ver sus citas (la API lo permite a cualquiera),
                # pero solo su autor puede retirarlas. Elegir una abre su conversación.
                sel = wx.GetSingleChoice(_("Citas (elige una para abrirla, o Cancelar):"),
                                         _("Quién cita esta publicación"), opciones, self)
                if sel:
                    self._abrir_toot_por_id(res[opciones.index(sel)]["id"])
                return
            sel = wx.GetSingleChoice(_("Citas (elige una para revocarla, o Cancelar):"),
                                     _("Quién cita esta publicación"), opciones, self)
            if not sel:
                return
            c = res[opciones.index(sel)]
            if confirmar(self, _("¿Revocar la cita de @{0}? Dejará de citar tu publicación.").format(c["autor"]),
                         _("Revocar cita")) != wx.ID_YES:
                return

            def _cb2(r2, e2):
                notificar(_("Error: {0}").format(e2) if e2 else _("Cita revocada."))
            mastodon_service.revocar_cita_async(cuenta, sid, c["id"], callback=_cb2)
        mastodon_service.citas_de_status_async(cuenta, sid, callback=_cb)

    def _abrir_toot_por_id(self, status_id):
        """Abre el visor de conversación de una publicación por su id del SERVIDOR."""
        if not status_id or not self._mst_cuenta_ok():
            return
        from .thread_viewer import ThreadViewerDialog
        dlg = ThreadViewerDialog(self, self.active_mastodon_account_id, None, status_id=str(status_id))
        dlg.ShowModal()
        dlg.Destroy()

    @staticmethod
    def id_cita_aceptada(quote_json) -> str:
        """Id de la publicación citada por un toot, si la cita está aceptada ("" si no)."""
        if not quote_json:
            return ""
        try:
            q = json.loads(quote_json) or {}
        except Exception:
            return ""
        if (q.get("state") or "accepted") != "accepted":
            return ""
        return str(q.get("id") or "")

    def _on_abrir_cita_toot(self, db_id):
        """Menú contextual: abre la publicación CITADA por el toot enfocado."""
        row = db.query_one("SELECT COALESCE(quote_json, '') FROM mastodon_statuses WHERE id = ?", (db_id,))
        sid = self.id_cita_aceptada(row[0] if row else "")
        if sid:
            self._abrir_toot_por_id(sid)
        else:
            notificar(_("Esta publicación no cita ninguna otra que se pueda abrir."))

    def _preparar_citas_pendientes_mastodon(self):
        """Una vez por sesión: escucha los cambios de estado de las citas (pendiente -> aceptada,
        que Mastodon no avisa por el streaming; ver mastodon_citas.py) y manda revisar las que
        quedaron pendientes en la caché de sesiones anteriores."""
        if getattr(self, "_citas_pendientes_preparadas", False):
            return
        self._citas_pendientes_preparadas = True
        from app.core.event_bus import event_bus
        from app.services.mastodon.mastodon_citas import EVENTO_CITA_ACTUALIZADA
        event_bus.subscribe(EVENTO_CITA_ACTUALIZADA, self._on_cita_mastodon_actualizada)
        try:
            cuentas = [r[0] for r in db.query(
                "SELECT id FROM accounts WHERE service_id='mastodon' AND active=1")]
        except Exception:
            cuentas = []
        for acc in cuentas:
            mastodon_service.revisar_citas_pendientes_guardadas(acc)

    def _on_cita_mastodon_actualizada(self, account_id, db_id):
        """(Hilo de la UI) La cita de un toot cambió de estado: rehacer su fila EN SU SITIO, sin
        mover el foco ni releerla (actualización silenciosa). Si no está a la vista, no hay nada
        que hacer: la base de datos ya está al día y saldrá bien al volver al buffer."""
        try:
            if db_id not in self.lista_items_db_ids or self._es_lista_notif_mastodon():
                return
            if (not getattr(self, "_bandeja_activa", False)
                    and self.active_mastodon_account_id != account_id):
                return
            self._actualizar_fila_toot_filtro(db_id)
        except Exception as e:
            log.debug(f"No se pudo repintar la fila de la cita {db_id}: {e}")

    # ===================== COLECCIONES (Mastodon 4.6) =====================
    def _mastodon_admite_colecciones(self):
        if not self._mst_cuenta_ok():
            return False
        acc = self.active_mastodon_account_id
        if mastodon_service.version_api(acc) and not mastodon_service.admite_colecciones(acc):
            notificar(_("Tu servidor todavía no admite colecciones (llegaron con Mastodon 4.6)."))
            return False
        return True

    def _abrir_colecciones(self, modo, acct=None):
        if not self._mastodon_admite_colecciones():
            return
        from .mastodon_colecciones_ui import ColeccionesDialog
        dlg = ColeccionesDialog(self, self.active_mastodon_account_id, modo=modo, acct=acct, frame=self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_mis_colecciones(self, event=None):
        self._abrir_colecciones("mias")

    def _on_colecciones_donde_estoy(self, event=None):
        self._abrir_colecciones("donde_estoy")

    def _acct_autor_toot(self, db_id):
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        return (row[0] or "").lstrip("@") if row else ""

    def _on_colecciones_de_autor(self, db_id):
        acct = self._acct_autor_toot(db_id)
        if acct:
            self._abrir_colecciones("de", acct=acct)

    def _on_anadir_autor_a_coleccion(self, db_id):
        acct = self._acct_autor_toot(db_id)
        if acct and self._mastodon_admite_colecciones():
            from .mastodon_colecciones_ui import elegir_mi_coleccion_y_anadir
            elegir_mi_coleccion_y_anadir(self, self.active_mastodon_account_id, acct)

    def _on_politica_citas_toot(self, db_id):
        if not self._mst_cuenta_ok():
            return
        sid = self._status_id_de_db(db_id)
        if not sid:
            notificar(_("No se pudo identificar la publicación."))
            return
        opciones = [_("Cualquiera puede citarla"), _("Solo tus seguidores"), _("Solo tú (nadie más puede citarla)")]
        mapa = {opciones[0]: "public", opciones[1]: "followers", opciones[2]: "nobody"}
        sel = wx.GetSingleChoice(_("¿Quién puede citar esta publicación?"), _("Política de citas"), opciones, self)
        if sel not in mapa:
            return

        def _cb(res, err):
            notificar(_("Error: {0}").format(err) if err else _("Política de citas actualizada."))
        mastodon_service.politica_citas_async(self.active_mastodon_account_id, sid, mapa[sel], callback=_cb)

    # ===================== C · SOLICITUDES DE NOTIFICACIÓN =====================
    def _on_solicitudes_notificacion(self, event):
        if not self._mst_cuenta_ok():
            return
        speak(_("Cargando solicitudes de notificación..."), interrupt=True)
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No tienes solicitudes de notificación pendientes."))
                return
            # Selección MÚLTIPLE (auditoría N8): marcar varias y resolverlas de una vez.
            opciones = ["@{0} ({1}) — {2} notificaciones".format(
                r["autor"], r["nombre"] or r["autor"], r["cantidad"]) for r in res]
            dlg = wx.MultiChoiceDialog(
                self, _("Marca las solicitudes a gestionar (Espacio marca y desmarca):"),
                _("Solicitudes de notificación"), opciones)
            if dlg.ShowModal() != wx.ID_OK:
                dlg.Destroy()
                return
            indices = list(dlg.GetSelections())
            dlg.Destroy()
            if not indices:
                notificar(_("No se marcó ninguna solicitud."))
                return
            elegidas = [res[i] for i in indices]
            from .accessible_controls import elegir_opcion
            r = elegir_opcion(
                self,
                _("{0} solicitud(es) marcadas. Aceptar mueve sus notificaciones a tu buffer y "
                  "permite las futuras; descartar las elimina.").format(len(elegidas)),
                _("Solicitudes de notificación"),
                [_("&Aceptar las marcadas"), _("&Descartar las marcadas")])
            if r is None:
                return
            aceptar = (r == 0)
            speak(_("Enviando..."), interrupt=True)

            def _cb2(r2, e2):
                if e2:
                    notificar(_("Error: {0}").format(e2))
                    return
                if aceptar:
                    if (r2 or {}).get("fusionadas"):
                        notificar(_("{0} solicitud(es) aceptadas; sus notificaciones ya están en "
                                    "tu buffer de Notificaciones.").format((r2 or {}).get("total", 0)))
                    else:
                        notificar(_("{0} solicitud(es) aceptadas; el servidor sigue moviendo sus "
                                    "notificaciones (aparecerán enseguida).").format((r2 or {}).get("total", 0)))
                else:
                    notificar(_("{0} solicitud(es) descartadas.").format((r2 or {}).get("total", 0)))
            mastodon_service.resolver_solicitudes_notif_lote_async(
                cuenta, [x["id"] for x in elegidas], aceptar, callback=_cb2)
        mastodon_service.solicitudes_notificacion_async(cuenta, callback=_cb)

    # ===================== G · HASHTAGS DESTACADOS DE OTRA CUENTA =====================
    def _on_hashtags_destacados_de(self, acct):
        if not self._mst_cuenta_ok():
            return
        speak(_("Consultando hashtags destacados..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("@{0} no tiene hashtags destacados.").format(acct))
                return
            texto = "\n".join("#" + t["nombre"] for t in res)
            self._mostrar_texto_largo(_("Hashtags destacados de @{0}").format(acct), texto)
        mastodon_service.hashtags_destacados_de_async(self.active_mastodon_account_id, acct, callback=_cb)

    # ===================== J · CONTADOR DE NO LEÍDAS =====================
    def _on_contador_no_leidas(self, event):
        if not self._mst_cuenta_ok():
            return
        def _cb(n, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Tienes {0} notificaciones sin leer.").format(n or 0))
        mastodon_service.contador_notif_no_leidas_async(self.active_mastodon_account_id, callback=_cb)

    # ===================== D · ACERCA DE LA INSTANCIA =====================
    def _on_borradores_mastodon(self, event):
        """Gestor de borradores persistentes (N3): retomar en el compositor o borrar."""
        if not self._mst_cuenta_ok():
            return
        from .mastodon_drafts_ui import abrir_gestor_borradores
        abrir_gestor_borradores(self, self.active_mastodon_account_id)

    def _on_conversaciones_directas(self, event):
        """Conversaciones directas de primera clase (N6): lista real del servidor."""
        if not self._mst_cuenta_ok():
            return
        from .mastodon_conversaciones_ui import abrir_conversaciones
        abrir_conversaciones(self, self.active_mastodon_account_id)

    @staticmethod
    def _duracion_legible(segundos):
        """'2592000' -> '30 días'; para los límites de expiración de encuestas."""
        try:
            s = int(segundos)
        except (TypeError, ValueError):
            return str(segundos)
        if s % 86400 == 0 and s >= 86400:
            return _("{0} día(s)").format(s // 86400)
        if s % 3600 == 0 and s >= 3600:
            return _("{0} hora(s)").format(s // 3600)
        if s % 60 == 0 and s >= 60:
            return _("{0} minuto(s)").format(s // 60)
        return _("{0} segundos").format(s)

    def _on_capacidades_instancia(self, event):
        """Centro de capacidades (N7): qué admite tu instancia, en un texto legible. Se
        refresca del servidor y, si falla la red, se muestra lo último cacheado."""
        if not self._mst_cuenta_ok():
            return
        cuenta = self.active_mastodon_account_id
        speak(_("Consultando las capacidades de tu instancia..."), interrupt=True)

        def _cb(limite, err):
            caps = mastodon_service.capacidades(cuenta)
            if not caps:
                notificar(_("No se pudieron obtener las capacidades: {0}").format(err or ""))
                return
            partes = []
            if err:
                partes.append(_("(Sin conexión: se muestran los últimos datos conocidos.)"))
            if caps.get("version"):
                partes.append(_("Versión del servidor: {0}").format(caps["version"]))
            citas = mastodon_service.soporta_citas_nativas(cuenta)
            partes.append(_("Citas nativas (Mastodon 4.5+): {0}").format(
                _("sí") if citas else (_("no (se citará mediante enlace)") if citas is False else _("desconocido"))))
            partes.append(_("Traducción del propio servidor: {0}").format(
                _("sí") if caps.get("traduccion") else _("no")))
            if caps.get("api_version"):
                partes.append(_("Versión de la API de Mastodon: {0}").format(caps["api_version"]))
            partes.append(_("Colecciones (Mastodon 4.6+): {0}").format(
                _("sí") if mastodon_service.admite_colecciones(cuenta) else _("no")))
            partes.append(_("Perfil: hasta {0} campos de metadatos, {1} etiquetas destacadas y {2} publicaciones fijadas.").format(
                caps.get("max_profile_fields", 4), caps.get("max_featured_tags", 10),
                caps.get("max_pinned_statuses", 5)))
            # Acceso a las cronologías públicas (4.5+): si están desactivadas, sus buffers no cargan.
            acceso = caps.get("timelines_access") or {}
            vivas = acceso.get("live_feeds") or {}
            if vivas:
                legible = {"public": _("abierta a todos"), "authenticated": _("solo con sesión iniciada"),
                           "disabled": _("desactivada")}
                partes.append(_("Cronología local: {0}. Cronología federada: {1}.").format(
                    legible.get(vivas.get("local"), vivas.get("local") or "?"),
                    legible.get(vivas.get("remote"), vivas.get("remote") or "?")))
            partes.append("")
            partes.append(_("Publicaciones:"))
            partes.append(_("  Hasta {0} caracteres (cada enlace cuenta como {1}).").format(
                caps.get("max_characters", 500), caps.get("characters_reserved_per_url", 23)))
            partes.append(_("Adjuntos:"))
            partes.append(_("  Hasta {0} por publicación; descripción (texto alternativo) de hasta {1} caracteres.").format(
                caps.get("max_media_attachments", 4), caps.get("description_limit", 1500)))
            mimes = caps.get("supported_mime_types") or []
            if mimes:
                partes.append(_("  Formatos admitidos ({0}): {1}").format(len(mimes), ", ".join(mimes)))
            partes.append(_("Encuestas:"))
            partes.append(_("  Hasta {0} opciones de {1} caracteres; duración de {2} a {3}.").format(
                caps.get("poll_max_options", 4), caps.get("poll_max_chars_por_opcion", 50),
                self._duracion_legible(caps.get("poll_min_expiracion", 300)),
                self._duracion_legible(caps.get("poll_max_expiracion", 2592000))))
            self._mostrar_texto_largo(_("Capacidades de tu instancia"), "\n".join(partes))

        mastodon_service.actualizar_limite_caracteres_async(cuenta, callback=_cb)

    def _on_acerca_instancia(self, event):
        if not self._mst_cuenta_ok():
            return
        speak(_("Consultando la instancia..."), interrupt=True)

        def _cb(d, err):
            if err or not d:
                notificar(_("No se pudo obtener la información: {0}").format(err or ""))
                return
            partes = []
            if d.get("software"):
                partes.append(_("Software del servidor: {0}").format(d["software"]))
            if d.get("usuarios_activos_mes"):
                partes.append(_("Usuarios activos este mes: {0}").format(d["usuarios_activos_mes"]))
            if d.get("federadas") is not None:
                partes.append(_("Instancias federadas conocidas: {0}").format(d["federadas"]))
            if d.get("streaming_ok") is not None:
                partes.append(_("Tiempo real (streaming): {0}").format(
                    _("disponible") if d["streaming_ok"] else _("no disponible")))
            if d.get("actividad"):
                partes.append(_("Actividad de las últimas semanas:"))
                for a in d["actividad"]:
                    try:
                        from datetime import datetime as _dtt
                        semana = _dtt.fromtimestamp(int(float(a["semana"]))).strftime("%d/%m/%Y")
                    except Exception:
                        semana = a["semana"][:10]
                    partes.append(_("  Semana del {0}: {1} publicaciones, {2} usuarios activos, {3} registros").format(
                        semana, a["publicaciones"], a["activos"], a["registros"]))
            if d.get("idiomas"):
                partes.append(_("Idiomas: {0}").format(", ".join(d["idiomas"])))
            if d.get("reglas"):
                partes.append(_("Reglas:"))
                for i, r in enumerate(d["reglas"], 1):
                    partes.append(f"  {i}. {r}")
            if d.get("descripcion"):
                partes.append("")
                partes.append(_("Descripción:"))
                partes.append(d["descripcion"])
            if d.get("terminos"):
                partes.append("")
                partes.append(_("Términos del servicio:"))
                partes.append(d["terminos"])
            texto = "\n".join(partes) or _("La instancia no publica información adicional.")
            self._mostrar_texto_largo(_("Acerca de esta instancia"), texto)
        mastodon_service.info_instancia_async(self.active_mastodon_account_id, callback=_cb)

    # ===================== L · ADMINISTRACIÓN =====================
    def _on_admin_estadisticas(self, event):
        """Estadísticas del servidor (solo cuentas admin): medidas, dimensiones y retención."""
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminStatsDialog
        dlg = MastodonAdminStatsDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        speak(_("Cargando estadísticas del servidor..."), interrupt=True)

        _ETIQUETAS_MEDIDA = {
            "active_users": _("Usuarios activos"), "new_users": _("Usuarios nuevos"),
            "interactions": _("Interacciones"), "opened_reports": _("Informes abiertos"),
            "resolved_reports": _("Informes resueltos"),
        }
        _ETIQUETAS_DIM = {
            "languages": _("Idiomas más usados"), "sources": _("Aplicaciones más usadas"),
            "software_versions": _("Versiones de software"),
        }

        def _cb(d, err):
            if err:
                notificar(_("Error (¿tu cuenta no es administradora?): {0}").format(err))
                return
            partes = [_("Últimos 30 días:"), ""]
            for m in (d or {}).get("medidas", []):
                partes.append("  {0}: {1}".format(_ETIQUETAS_MEDIDA.get(m["clave"], m["clave"]), m["total"]))
            for dim in (d or {}).get("dimensiones", []):
                partes.append("")
                partes.append(_ETIQUETAS_DIM.get(dim["clave"], dim["clave"]) + ":")
                for v in dim["valores"]:
                    partes.append("  " + v)
            if (d or {}).get("retencion"):
                partes.append("")
                partes.append(_("Retención de usuarios (por cohorte de alta, % que sigue activo):"))
                for r in d["retencion"]:
                    partes.append("  {0}: {1}".format(r["cohorte"], ", ".join(r["tasas"])))
            texto = "\n".join(partes).strip() or _("El servidor no devolvió estadísticas.")
            self._mostrar_texto_largo(_("Estadísticas del servidor"), texto)
        mastodon_service.admin_estadisticas_async(self.active_mastodon_account_id, callback=_cb)

    def _on_admin_informes(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminReportsDialog
        dlg = MastodonAdminReportsDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id
        speak(_("Cargando informes..."), interrupt=True)

        def _cb(res, err):
            if err:
                txt = str(err).lower()
                if "403" in txt or "forbidden" in txt or "scope" in txt or "unauthorized" in txt or "401" in txt:
                    notificar(_("El servidor rechaza la petición de moderación. Si eres moderador/admin, "
                                "vuelve a añadir esta cuenta en Gestionar cuentas marcando la casilla "
                                "'Soy moderador o administrador' para conceder los permisos."))
                else:
                    notificar(_("No se pudieron cargar los informes: {0}").format(err))
                return
            if not res:
                notificar(_("No hay informes sin resolver."))
                return
            opciones = ["@{0} — {1} — {2}".format(r["objetivo"], r["categoria"], r["comentario"]) for r in res]
            sel = wx.GetSingleChoice(_("Informes (elige uno):"), _("Informes"), opciones, self)
            if not sel:
                return
            r = res[opciones.index(sel)]
            acc = wx.GetSingleChoice(_("Acción sobre el informe:"), _("Informe"),
                                     [_("Resolver"), _("Asignármelo"), _("Reabrir"), _("Quitarme la asignación")], self)
            mapa = {_("Resolver"): "resolver", _("Asignármelo"): "asignar",
                    _("Reabrir"): "reabrir", _("Quitarme la asignación"): "desasignar"}
            if acc not in mapa:
                return

            def _cb2(r2, e2):
                notificar(_("Error: {0}").format(e2) if e2 else _("Hecho."))
            mastodon_service.admin_accion_informe_async(cuenta, r["id"], mapa[acc], callback=_cb2)
        mastodon_service.admin_listar_informes_async(cuenta, resueltos=False, callback=_cb)

    def _on_admin_cuentas(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminAccountsDialog
        dlg = MastodonAdminAccountsDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id
        estado = wx.GetSingleChoice(_("¿Qué cuentas listar?"), _("Cuentas"),
                                    [_("Activas"), _("Pendientes de aprobar"), _("Silenciadas"),
                                     _("Suspendidas"), _("Desactivadas")], self)
        mapa_est = {_("Activas"): "active", _("Pendientes de aprobar"): "pending",
                    _("Silenciadas"): "silenced", _("Suspendidas"): "suspended",
                    _("Desactivadas"): "disabled"}
        if estado not in mapa_est:
            return
        speak(_("Cargando cuentas..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No hay cuentas en ese estado."))
                return
            opciones = ["@{0} {1}".format(c["usuario"], ("- " + c["email"]) if c["email"] else "") for c in res]
            sel = wx.GetSingleChoice(_("Cuentas (elige una):"), _("Cuentas"), opciones, self)
            if not sel:
                return
            c = res[opciones.index(sel)]
            acciones = [_("Aprobar"), _("Rechazar"), _("Suspender"), _("Silenciar"), _("Desactivar"),
                        _("Reactivar"), _("Quitar suspensión"), _("Quitar silencio"), _("Marcar como no sensible")]
            mapa_acc = {_("Aprobar"): "aprobar", _("Rechazar"): "rechazar", _("Suspender"): "suspender",
                        _("Silenciar"): "silenciar", _("Desactivar"): "desactivar", _("Reactivar"): "reactivar",
                        _("Quitar suspensión"): "quitar_suspension", _("Quitar silencio"): "quitar_silencio",
                        _("Marcar como no sensible"): "marcar_no_sensible"}
            acc = wx.GetSingleChoice(_("Acción sobre @{0}:").format(c["usuario"]), _("Cuenta"), acciones, self)
            if acc not in mapa_acc:
                return
            if confirmar(self, _("¿Aplicar «{0}» a @{1}?").format(acc, c["usuario"]), _("Confirmar")) != wx.ID_YES:
                return

            def _cb2(r2, e2):
                notificar(_("Error: {0}").format(e2) if e2 else _("Hecho."))
            mastodon_service.admin_accion_cuenta_async(cuenta, c["id"], mapa_acc[acc], callback=_cb2)
        mastodon_service.admin_listar_cuentas_async(cuenta, estado=mapa_est[estado], callback=_cb)

    def _on_admin_dominios(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminDomainBlocksDialog
        dlg = MastodonAdminDomainBlocksDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = [_("[+] Bloquear un dominio nuevo...")] + \
                       ["{0} ({1})".format(d["dominio"], d["severidad"]) for d in (res or [])]
            sel = wx.GetSingleChoice(_("Dominios bloqueados:"), _("Dominios (admin)"), opciones, self)
            if not sel:
                return
            if sel == opciones[0]:
                from .accessible_controls import CustomTextEntryDialog
                dd = CustomTextEntryDialog(self, _("Dominio a bloquear (ej. ejemplo.social):"), _("Bloquear dominio"))
                dom = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not dom:
                    return
                sev = wx.GetSingleChoice(_("Severidad del bloqueo:"), _("Severidad"),
                                         [_("Silenciar"), _("Suspender"), _("Solo limitar medios")], self)
                mapa = {_("Silenciar"): "silence", _("Suspender"): "suspend", _("Solo limitar medios"): "noop"}

                def _cb2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("Dominio bloqueado."))
                mastodon_service.admin_bloquear_dominio_async(cuenta, dom, mapa.get(sev, "silence"), callback=_cb2)
            else:
                d = (res or [])[opciones.index(sel) - 1]
                if confirmar(self, _("¿Quitar el bloqueo de {0}?").format(d["dominio"]), _("Desbloquear")) != wx.ID_YES:
                    return

                def _cb3(r3, e3):
                    notificar(_("Error: {0}").format(e3) if e3 else _("Bloqueo retirado."))
                mastodon_service.admin_desbloquear_dominio_async(cuenta, d["id"], callback=_cb3)
        mastodon_service.admin_listar_dominios_bloqueados_async(cuenta, callback=_cb)

    def _on_admin_email_blocks(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminResourceDialog
        dlg = MastodonAdminResourceDialog(self, self.active_mastodon_account_id, "email_domains")
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = [_("[+] Bloquear un dominio de email...")] + [e["dominio"] for e in (res or [])]
            sel = wx.GetSingleChoice(_("Dominios de email bloqueados:"), _("Email (admin)"), opciones, self)
            if not sel:
                return
            if sel == opciones[0]:
                from .accessible_controls import CustomTextEntryDialog
                dd = CustomTextEntryDialog(self, _("Dominio de email a bloquear:"), _("Bloquear email"))
                dom = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not dom:
                    return

                def _cb2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("Bloqueado."))
                mastodon_service.admin_bloquear_email_async(cuenta, dom, callback=_cb2)
            else:
                e = (res or [])[opciones.index(sel) - 1]

                def _cb3(r3, e3):
                    notificar(_("Error: {0}").format(e3) if e3 else _("Desbloqueado."))
                mastodon_service.admin_desbloquear_email_async(cuenta, e["id"], callback=_cb3)
        mastodon_service.admin_listar_email_blocks_async(cuenta, callback=_cb)

    def _on_admin_ip_blocks(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminIpBlocksDialog
        dlg = MastodonAdminIpBlocksDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = [_("[+] Bloquear una IP...")] + \
                       ["{0} ({1})".format(b["ip"], b["severidad"]) for b in (res or [])]
            sel = wx.GetSingleChoice(_("IP bloqueadas:"), _("IP (admin)"), opciones, self)
            if not sel:
                return
            if sel == opciones[0]:
                from .accessible_controls import CustomTextEntryDialog
                dd = CustomTextEntryDialog(self, _("IP o rango a bloquear (ej. 192.0.2.0/24):"), _("Bloquear IP"))
                ip = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not ip:
                    return

                def _cb2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("IP bloqueada."))
                mastodon_service.admin_bloquear_ip_async(cuenta, ip, callback=_cb2)
            else:
                b = (res or [])[opciones.index(sel) - 1]

                def _cb3(r3, e3):
                    notificar(_("Error: {0}").format(e3) if e3 else _("Desbloqueada."))
                mastodon_service.admin_desbloquear_ip_async(cuenta, b["id"], callback=_cb3)
        mastodon_service.admin_listar_ip_blocks_async(cuenta, callback=_cb)

    def _on_admin_dominios_permitidos(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminResourceDialog
        dlg = MastodonAdminResourceDialog(self, self.active_mastodon_account_id, "domain_allows")
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = [_("[+] Permitir un dominio...")] + [d["dominio"] for d in (res or [])]
            sel = wx.GetSingleChoice(_("Dominios permitidos (federación en modo lista blanca):"),
                                     _("Dominios permitidos (admin)"), opciones, self)
            if not sel:
                return
            if sel == opciones[0]:
                from .accessible_controls import CustomTextEntryDialog
                dd = CustomTextEntryDialog(self, _("Dominio a permitir (ej. mastodon.social):"), _("Permitir dominio"))
                dom = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not dom:
                    return

                def _cb2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("Dominio permitido."))
                mastodon_service.admin_permitir_dominio_async(cuenta, dom, callback=_cb2)
            else:
                d = (res or [])[opciones.index(sel) - 1]

                def _cb3(r3, e3):
                    notificar(_("Error: {0}").format(e3) if e3 else _("Dominio quitado de la lista."))
                mastodon_service.admin_quitar_dominio_permitido_async(cuenta, d["id"], callback=_cb3)
        mastodon_service.admin_listar_dominios_permitidos_async(cuenta, callback=_cb)

    def _on_admin_email_canonicos(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminResourceDialog
        dlg = MastodonAdminResourceDialog(self, self.active_mastodon_account_id, "canonical_emails")
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = [_("[+] Bloquear un email..."), _("[?] Comprobar si un email está bloqueado...")] + \
                       [_("Bloqueo {0}").format(b["hash"]) for b in (res or [])]
            sel = wx.GetSingleChoice(_("Bloqueos de email canónico (se guardan cifrados, no en claro):"),
                                     _("Email canónico (admin)"), opciones, self)
            if not sel:
                return
            from .accessible_controls import CustomTextEntryDialog
            if sel == opciones[0]:
                dd = CustomTextEntryDialog(self, _("Email a bloquear:"), _("Bloquear email"))
                email = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not email:
                    return

                def _cb2(r2, e2):
                    notificar(_("Error: {0}").format(e2) if e2 else _("Email bloqueado."))
                mastodon_service.admin_bloquear_email_canonico_async(cuenta, email, callback=_cb2)
            elif sel == opciones[1]:
                dd = CustomTextEntryDialog(self, _("Email a comprobar:"), _("Comprobar email"))
                email = dd.GetValue().strip() if dd.ShowModal() == wx.ID_OK else ""
                dd.Destroy()
                if not email:
                    return

                def _cb2(n, e2):
                    if e2:
                        notificar(_("Error: {0}").format(e2))
                    elif n:
                        notificar(_("Ese email SÍ está bloqueado ({0} coincidencias).").format(n))
                    else:
                        notificar(_("Ese email no está bloqueado."))
                mastodon_service.admin_probar_email_canonico_async(cuenta, email, callback=_cb2)
            else:
                b = (res or [])[opciones.index(sel) - 2]

                def _cb3(r3, e3):
                    notificar(_("Error: {0}").format(e3) if e3 else _("Bloqueo quitado."))
                mastodon_service.admin_desbloquear_email_canonico_async(cuenta, b["id"], callback=_cb3)
        mastodon_service.admin_listar_email_canonicos_async(cuenta, callback=_cb)

    def _on_admin_tendencias(self, event):
        if not self._mst_cuenta_ok():
            return
        from .mastodon_admin_dialogs import MastodonAdminTrendsDialog
        dlg = MastodonAdminTrendsDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        return
        cuenta = self.active_mastodon_account_id
        tipo_sel = wx.GetSingleChoice(_("¿Qué tendencias revisar?"), _("Tendencias (admin)"),
                                      [_("Hashtags"), _("Publicaciones"), _("Enlaces")], self)
        mapa_tipo = {_("Hashtags"): "tags", _("Publicaciones"): "statuses", _("Enlaces"): "links"}
        if tipo_sel not in mapa_tipo:
            return
        tipo = mapa_tipo[tipo_sel]
        speak(_("Cargando tendencias..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No hay tendencias por revisar."))
                return
            opciones = [t["texto"] for t in res]
            sel = wx.GetSingleChoice(_("Tendencias (elige una):"), _("Tendencias"), opciones, self)
            if not sel:
                return
            t = res[opciones.index(sel)]
            acc = wx.GetSingleChoice(_("Acción:"), _("Tendencia"), [_("Aprobar"), _("Rechazar")], self)
            if acc not in (_("Aprobar"), _("Rechazar")):
                return

            def _cb2(r2, e2):
                notificar(_("Error: {0}").format(e2) if e2 else _("Hecho."))
            mastodon_service.admin_moderar_tendencia_async(cuenta, tipo, t["id"], acc == _("Aprobar"), callback=_cb2)
        mastodon_service.admin_listar_tendencias_async(cuenta, tipo, callback=_cb)
