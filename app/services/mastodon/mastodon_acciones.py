# -*- coding: utf-8 -*-
"""Acciones sobre toots (favorito, impulso, editar, etc.).

Mixin de MastodonService (parte de la refactorización modular).
"""
import logging
import json
from typing import Optional, Callable
from app.core.database import db
from app.core.task_manager import task_manager


log = logging.getLogger(__name__)

class _AccionesMixin:
    def ejecutar_accion_toot_async(self, account_id: int, toot_db_id: str, accion: str, callback: Optional[Callable] = None):
        """Ejecuta una acción (favourite, reblog, bookmark, etc.) asíncronamente en el servidor."""
        # Resolver ID original
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback: callback(None, Exception("Toot no encontrado en la base de datos local."))
            return
            
        status_id = row[0]
        
        def _bg_action(ctx):
            client = self.obtener_cliente(account_id)
            log.info(f"Ejecutando acción '{accion}' sobre toot original ID {status_id}")
            
            if accion == "favourite":
                res = client.status_favourite(status_id)
                db.execute(
                    "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, 'favourites', ?)",
                    (account_id, toot_db_id)
                )
            elif accion == "unfavourite":
                res = client.status_unfavourite(status_id)
                db.execute(
                    "DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = 'favourites' AND status_db_id = ?",
                    (account_id, toot_db_id)
                )
            elif accion == "reblog":
                res = client.status_reblog(status_id)
            elif accion == "unreblog":
                res = client.status_unreblog(status_id)
            elif accion == "bookmark":
                res = client.status_bookmark(status_id)
                db.execute(
                    "INSERT OR IGNORE INTO mastodon_timeline_statuses (account_id, timeline, status_db_id) VALUES (?, 'bookmarks', ?)",
                    (account_id, toot_db_id)
                )
            elif accion == "unbookmark":
                res = client.status_unbookmark(status_id)
                db.execute(
                    "DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = 'bookmarks' AND status_db_id = ?",
                    (account_id, toot_db_id)
                )
            elif accion == "pin":
                res = client.status_pin(status_id)
            elif accion == "unpin":
                res = client.status_unpin(status_id)
            elif accion == "delete":
                client.status_delete(status_id)
                db.execute("DELETE FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
                try:
                    db.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (toot_db_id,))
                except Exception:
                    pass
                return True
            else:
                raise ValueError(f"Acción desconocida: {accion}")

            # Reflejar el cambio en la fila EXACTA sobre la que actuó el usuario (por ejemplo
            # un impulso), para que la interfaz lo muestre al instante aunque la acción afecte
            # al toot original.
            campos = {"favourite": ("favourited", 1), "unfavourite": ("favourited", 0),
                      "reblog": ("reblogged", 1), "unreblog": ("reblogged", 0),
                      "bookmark": ("bookmarked", 1), "unbookmark": ("bookmarked", 0)}
            if accion in campos:
                col, val = campos[accion]
                db.execute(f"UPDATE mastodon_statuses SET {col} = ? WHERE id = ?", (val, toot_db_id))

            # Sincronizar también con el toot devuelto por el servidor, EXCEPTO en
            # reblog/unreblog: su respuesta es un envoltorio de impulso que, al guardarse,
            # crearía una fila de impulso huérfana (no mapeada a ningún timeline).
            if accion not in ("reblog", "unreblog") and isinstance(res, dict):
                self._procesar_y_guardar_toot(account_id, res)
            return True

        task_manager.start_task(
            _bg_action,
            callback=callback,
            success_sound="ready",
            error_sound="error",
            pass_context=True
        )

    def obtener_fuente_toot_async(self, account_id: int, toot_db_id: str, callback: Callable):
        """Obtiene del servidor el TEXTO ORIGINAL (fuente) de un toot para poder editarlo:
        el texto tal cual se escribió y su advertencia de contenido. Usa el endpoint
        status_source de Mastodon (lo mismo que hace TWBlue al editar)."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            callback(None, Exception("Toot no encontrado en la base de datos local."))
            return
        status_id = row[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            src = client.status_source(status_id)
            return {
                "text": src.get("text", "") or "",
                "spoiler_text": src.get("spoiler_text", "") or "",
            }

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def editar_toot_async(self, account_id: int, toot_db_id: str, texto: str,
                          spoiler_text: Optional[str] = None, media_items: Optional[list] = None,
                          callback: Optional[Callable] = None):
        """Edita (status_update) una publicación PROPIA: cambia el texto, la advertencia de
        contenido y los archivos multimedia (añadir, quitar y cambiar su descripción /
        texto alternativo). La visibilidad no se puede cambiar al editar (limitación de
        Mastodon). Actualiza la copia local con la respuesta.

        media_items: lista en orden de los adjuntos finales. Cada elemento es uno de:
          - existente: {"existing": True, "id": <media_id>, "alt": <alt>, "alt_original": <alt previo>,
                        "focus": [x, y] | None, "focus_original": [x, y] | None}
          - nuevo:     {"path": <ruta local>, "alt": <alt>, "focus": [x, y] | None}
        El punto focal (focus, auditoría N5) solo se envía si el usuario lo cambió.
        Si es None, se conservan tal cual los adjuntos actuales del toot."""
        row = db.query_one("SELECT status_id, media_json FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback:
                callback(None, Exception("Toot no encontrado en la base de datos local."))
            return
        status_id, media_json = row

        def _bg(ctx):
            client = self.obtener_cliente(account_id)

            # Los cambios de descripción (alt) de adjuntos YA PUBLICADOS van como
            # `media_attributes` DENTRO de la misma edición (status_update): media_update()
            # solo vale para adjuntos aún no asociados a una publicación (auditoría M5), y
            # así todo el cambio es atómico (o se edita todo, o falla y se informa del error;
            # antes el alt podía fallar en silencio y aun así se anunciaba éxito).
            #
            # OJO (auditoría M6): Mastodon.py 2.2.1 tiene un bug al COMBINAR media_ids con
            # media_attributes (TypeError: set + set). Se esquiva construyendo un atributo
            # por CADA adjunto final y omitiendo media_ids: la librería deduce entonces los
            # media_ids del propio media_attributes, conservando el orden.
            media_attrs = []
            media_ids = []
            if media_items is None:
                # Sin lista explícita: conservar los adjuntos actuales tal cual (si no se
                # pasan los media_ids, Mastodon los quitaría al editar).
                if media_json:
                    try:
                        for att in json.loads(media_json):
                            if att.get("id"):
                                media_ids.append(att["id"])
                    except Exception:
                        pass
            else:
                def _foco_tupla(valor):
                    """[x, y] -> (x, y) float, o None si no hay/está mal formado."""
                    if not valor:
                        return None
                    try:
                        return (float(valor[0]), float(valor[1]))
                    except (TypeError, ValueError, IndexError):
                        return None

                for item in media_items:
                    if item.get("existing"):
                        mid = item.get("id")
                        if not mid:
                            continue
                        attr = {"id": mid}
                        if (item.get("alt") or "") != (item.get("alt_original") or ""):
                            attr["description"] = item.get("alt") or ""
                        # Punto focal: solo si el usuario lo cambió. La API lo espera
                        # como texto "x,y" dentro de media_attributes.
                        foco = _foco_tupla(item.get("focus"))
                        if foco is not None and item.get("focus") != item.get("focus_original"):
                            attr["focus"] = "{0},{1}".format(foco[0], foco[1])
                        media_attrs.append(attr)
                    else:
                        ruta = item.get("path")
                        if not ruta:
                            continue
                        from .mastodon_composer import _adivinar_mime
                        mime = _adivinar_mime(ruta)
                        subida = client.media_post(ruta, mime_type=mime,
                                                   description=(item.get("alt") or None),
                                                   focus=_foco_tupla(item.get("focus")),
                                                   synchronous=True)
                        media_attrs.append({"id": subida["id"]})

            kwargs = {"status": texto}
            cw = (spoiler_text or "").strip()
            if cw:
                kwargs["spoiler_text"] = cw
                kwargs["sensitive"] = True
            if media_items is None:
                if media_ids:
                    kwargs["media_ids"] = media_ids
            elif media_attrs:
                kwargs["media_attributes"] = media_attrs
            else:
                # El usuario quitó TODOS los adjuntos: hay que decírselo al servidor.
                kwargs["media_ids"] = []
            res = client.status_update(status_id, **kwargs)
            self._procesar_y_guardar_toot(account_id, res)
            return True

        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def borrar_y_redactar_async(self, account_id: int, toot_db_id: str,
                                callback: Optional[Callable] = None):
        """Borra una publicación PROPIA y devuelve lo necesario para VOLVER A REDACTARLA
        (auditoría N4, el 'delete & redraft' de Mastodon): el DELETE de la API devuelve el
        toot borrado con su texto FUENTE en 'text' (tal cual se escribió, sin HTML), la
        advertencia de contenido, la visibilidad y a quién respondía. Los adjuntos no se
        recuperan (el servidor los elimina con el toot)."""
        row = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row:
            if callback:
                callback(None, Exception("Toot no encontrado en la base de datos local."))
            return
        status_id = row[0]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.status_delete(status_id)
            db.execute("DELETE FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
            try:
                db.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (toot_db_id,))
            except Exception:
                pass
            datos = {"text": "", "spoiler_text": "", "visibility": None,
                     "in_reply_to_id": None, "tenia_adjuntos": False}
            if isinstance(res, dict):
                texto = res.get("text")
                if not texto and res.get("content"):
                    # Servidor antiguo sin texto fuente en el DELETE: quitar el HTML.
                    import re as _re
                    texto = _re.sub(r"<br\s*/?>", "\n", res["content"])
                    texto = _re.sub(r"</p>\s*<p>", "\n\n", texto)
                    texto = _re.sub(r"<[^>]+>", "", texto)
                    import html as _html
                    texto = _html.unescape(texto)
                datos["text"] = texto or ""
                datos["spoiler_text"] = res.get("spoiler_text") or ""
                datos["visibility"] = res.get("visibility")
                datos["in_reply_to_id"] = res.get("in_reply_to_id")
                datos["tenia_adjuntos"] = bool(res.get("media_attachments"))
            return datos

        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def obtener_solicitudes_seguimiento_async(self, account_id: int, callback: Optional[Callable] = None):
        """Devuelve las solicitudes de seguimiento pendientes (cuentas privadas), como FastSM."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = client.follow_requests()
            solicitudes = []
            for c in (res or []):
                solicitudes.append({
                    "id": c.get("id"),
                    "acct": c.get("acct", ""),
                    "display": c.get("display_name") or c.get("username", ""),
                })
            return solicitudes
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def responder_solicitud_seguimiento_async(self, account_id: int, req_account_id, aceptar: bool,
                                               callback: Optional[Callable] = None):
        """Acepta o rechaza una solicitud de seguimiento pendiente."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if aceptar:
                client.follow_request_authorize(req_account_id)
            else:
                client.follow_request_reject(req_account_id)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def seguir_autor_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        """Alterna seguir/dejar de seguir al AUTOR del toot indicado.
        Resuelve la cuenta por su 'acct' y consulta la relación actual para decidir.
        El callback recibe un dict {following, acct, display} o una excepción."""
        row = db.query_one("SELECT author_username, author_name FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row or not row[0]:
            if callback:
                callback(None, Exception("No se encontró el autor de la publicación."))
            return
        acct = (row[0] or "").lstrip("@")
        display = row[1] or acct

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            cid = cuenta["id"]
            rel = client.account_relationships(cid)
            siguiendo = bool(rel[0].get("following")) if rel else False
            if siguiendo:
                client.account_unfollow(cid)
                return {"following": False, "acct": acct, "display": display}
            else:
                client.account_follow(cid)
                return {"following": True, "acct": acct, "display": display}

        task_manager.start_task(
            _bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True
        )

    def relacion_autor_async(self, account_id: int, toot_db_id: str, callback: Optional[Callable] = None):
        """Consulta (sin cambiarla) la relación con el AUTOR del toot: si lo sigues o no.
        El callback recibe {acct, following} o una excepción. Sirve para que el menú
        contextual muestre 'Seguir' o 'Dejar de seguir' según el estado real."""
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (toot_db_id,))
        if not row or not row[0]:
            if callback:
                callback(None, Exception("No se encontró el autor de la publicación."))
            return
        acct = (row[0] or "").lstrip("@")

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            cuenta = client.account_lookup(acct)
            rel = client.account_relationships(cuenta["id"])
            siguiendo = bool(rel[0].get("following")) if rel else False
            return {"acct": acct, "following": siguiendo}

        # unique_key: si el menú se abre varias veces seguidas sobre el mismo autor, una sola consulta.
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"mastodon:rel:{account_id}:{acct}")

    # --- RELACIONES Y BUFFERS AVANZADOS (B) ---
