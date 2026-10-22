# -*- coding: utf-8 -*-
"""Herramientas transversales del menú Herramientas: fragmentos de texto, estadísticas sociales
locales, buffer unificado de tendencias y "Ponte al día" (resumen con IA de lo no leído).

Mixin de MainFrame (parte de la refactorización modular). Todo el trabajo pesado (IA, red) va en
hilos de fondo con task_manager; la interfaz nunca se bloquea.
"""
import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.core.config_manager import config


class _Mf_herramientas_extra:
    # ---------------- FRAGMENTOS DE TEXTO (snippets) ----------------
    def _on_snippets(self, event):
        from .snippets import SnippetsDialog
        dlg = SnippetsDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- CENTRO DE NOTIFICACIONES UNIFICADO ----------------
    def _on_centro_notificaciones(self, event):
        from .notif_center_dialog import NotifCenterDialog
        dlg = NotifCenterDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- PUBLICACIÓN CRUZADA (cross-posting) ----------------
    def _on_crosspost(self, event, texto_inicial=""):
        from .crosspost_dialog import CrossPostDialog
        dlg = CrossPostDialog(self, texto_inicial=texto_inicial)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- COLA UNIFICADA DE BORRADORES Y PROGRAMADOS ----------------
    def _on_cola_unificada(self, event):
        """Reúne en un solo sitio los BORRADORES locales (Mastodon, Bluesky, X y Telegram,
        guardados en config) y las publicaciones PROGRAMADAS de Mastodon (del servidor)."""
        speak(_("Reuniendo borradores y programados..."), interrupt=True)
        items = []  # (etiqueta, tipo, red, datos)

        # Borradores locales guardados por cada compositor en config.
        for red, clave in (("Mastodon", ("mastodon", "drafts")), ("Bluesky", ("bluesky", "drafts")),
                           ("X", ("x", "drafts"))):
            datos = config.get(clave[0], clave[1], [])
            if isinstance(datos, list):
                for d in datos:
                    txt = (d.get("text", "") if isinstance(d, dict) else str(d)) or ""
                    if txt.strip():
                        items.append((_("[Borrador {0}] {1}").format(red, txt[:60]), "borrador", red.lower(), d))
        # Borradores de Telegram (dict clave->texto).
        tg_drafts = config.get("telegram", "drafts", {})
        if isinstance(tg_drafts, dict):
            for txt in tg_drafts.values():
                if (txt or "").strip():
                    items.append((_("[Borrador Telegram] {0}").format(txt[:60]), "borrador", "telegram", txt))

        # Programados de Mastodon (asíncrono; al volver, se muestra el diálogo con todo).
        acc = getattr(self, "active_mastodon_account_id", None)
        if not acc:
            # Sin cuenta de Mastodon activa: buscar la primera activa para leer sus programados.
            fila = db.query_one("SELECT id FROM accounts WHERE service_id='mastodon' AND active=1 ORDER BY position, id")
            acc = fila[0] if fila else None

        def _mostrar(programados):
            for p in (programados or []):
                cuando = (p.get("scheduled_at", "") or "")[:16].replace("T", " ")
                items.append((_("[Programado Mastodon {0}] {1}").format(cuando, (p.get("text", "") or "")[:50]),
                              "programado", "mastodon", p))
            if not items:
                notificar(_("No tienes borradores ni publicaciones programadas."))
                return
            opciones = [i[0] for i in items]
            sel = wx.GetSingleChoice(_("Borradores y programados (elige uno):"),
                                     _("Borradores y programados"), opciones, self)
            if not sel:
                return
            self._accion_cola_item(items[opciones.index(sel)], acc)

        from app.core.task_manager import task_manager
        if acc:
            from app.services.mastodon.mastodon_service import mastodon_service
            def _cb(res, err):
                _mostrar(res if not err else [])
            mastodon_service.obtener_programados_async(acc, callback=_cb)
        else:
            _mostrar([])

    def _accion_cola_item(self, item, cuenta_mastodon):
        """Acciones sobre un elemento de la cola: los borradores se abren en su compositor;
        los programados de Mastodon se pueden reprogramar o cancelar."""
        _etq, tipo, red, datos = item
        if tipo == "programado":
            acciones = [_("Reprogramar"), _("Cancelar")]
            a = wx.GetSingleChoice(_("Acción sobre la publicación programada:"), _("Programado"), acciones, self)
            if a == acciones[0]:
                self._reprogramar_programada(datos)  # de _Mf_autores_listas
            elif a == acciones[1]:
                from app.services.mastodon.mastodon_service import mastodon_service
                def _cb(r, e):
                    notificar(_("Programada cancelada.") if not e else _("Error: {0}").format(e))
                mastodon_service.cancelar_programado_async(cuenta_mastodon, datos.get("id"), callback=_cb)
            return
        # Borrador: abrir el compositor de su red con el texto.
        texto = (datos.get("text", "") if isinstance(datos, dict) else str(datos)) or ""
        try:
            if red == "mastodon" and getattr(self, "active_mastodon_account_id", None):
                from .mastodon_views import ComposeTootDialog
                d = ComposeTootDialog(self, self.active_mastodon_account_id, texto_inicial=texto)
                d.ShowModal(); d.Destroy()
            elif red == "bluesky" and getattr(self, "active_bluesky_account_id", None):
                from .bluesky_dialogs import BlueskyComposeDialog
                d = BlueskyComposeDialog(self, self.active_bluesky_account_id, texto_inicial=texto)
                d.ShowModal(); d.Destroy()
            else:
                # Como respaldo, ofrecer publicarlo por publicación cruzada.
                self._on_crosspost(None, texto_inicial=texto)
        except Exception:
            self._on_crosspost(None, texto_inicial=texto)

    # ---------------- ESTADÍSTICAS SOCIALES LOCALES ----------------
    def _on_estadisticas_sociales(self, event):
        """Estadísticas con datos LOCALES ya cacheados (sin ninguna petición de red): cuántas
        publicaciones hay por red, actividad por hora del día y autores con más presencia."""
        speak(_("Calculando estadísticas..."), interrupt=True)

        def _bg(ctx):
            # (etiqueta, tabla, columna de fecha, columna de autor)
            fuentes = [
                (_("Mastodon"), "mastodon_statuses", "created_at", "author_username"),
                (_("Bluesky"), "bluesky_posts", "created_at", "author_handle"),
                (_("RSS"), "rss_articles", "published_at", "author"),
                (_("X"), "x_tweets", "created_at", "author_screen_name"),
                (_("Telegram"), "telegram_messages", "date", "author_username"),
                (_("IRC"), "irc_messages", "date", "nick"),
                (_("Matrix"), "matrix_mensajes", "date", "sender_nombre"),
                (_("XMPP"), "xmpp_mensajes", "date", "sender_nombre"),
            ]
            totales = []
            por_hora = {h: 0 for h in range(24)}
            autores = {}
            for etiqueta, tabla, col_fecha, col_autor in fuentes:
                try:
                    n = (db.query_one(f"SELECT COUNT(*) FROM {tabla}") or [0])[0]
                except Exception:
                    continue  # la tabla puede no existir si la red nunca se usó
                totales.append((etiqueta, n))
                if not n:
                    continue
                # Actividad por hora (substr del ISO: posiciones 12-13 = hora). Barato y sin parsear.
                try:
                    for h, c in db.query(
                            f"SELECT substr({col_fecha},12,2) AS h, COUNT(*) FROM {tabla} "
                            f"WHERE length({col_fecha})>=13 GROUP BY h"):
                        try:
                            por_hora[int(h)] = por_hora.get(int(h), 0) + c
                        except (TypeError, ValueError):
                            pass
                except Exception:
                    pass
                # Autores más presentes (top por tabla, luego se combinan).
                try:
                    for autor, c in db.query(
                            f"SELECT {col_autor}, COUNT(*) c FROM {tabla} "
                            f"WHERE {col_autor} IS NOT NULL AND {col_autor} != '' "
                            f"GROUP BY {col_autor} ORDER BY c DESC LIMIT 10"):
                        autores[f"{autor} ({etiqueta})"] = c
                except Exception:
                    pass
            top_autores = sorted(autores.items(), key=lambda x: x[1], reverse=True)[:10]
            top_horas = sorted(por_hora.items(), key=lambda x: x[1], reverse=True)[:5]
            return {"totales": totales, "top_autores": top_autores, "top_horas": top_horas}

        def _cb(d, err):
            if err or not d:
                notificar(_("No se pudieron calcular las estadísticas: {0}").format(err or ""))
                return
            partes = [_("Contenido guardado por red:")]
            total_global = 0
            for etiqueta, n in d["totales"]:
                partes.append(f"  {etiqueta}: {n}")
                total_global += n
            partes.append(_("  Total: {0}").format(total_global))
            if d["top_horas"] and any(c for _h, c in d["top_horas"]):
                partes.append("")
                partes.append(_("Horas del día con más actividad:"))
                for h, c in d["top_horas"]:
                    if c:
                        partes.append(_("  {0}:00 — {1} elementos").format("%02d" % h, c))
            if d["top_autores"]:
                partes.append("")
                partes.append(_("Cuentas/fuentes con más presencia:"))
                for autor, c in d["top_autores"]:
                    partes.append(f"  {autor}: {c}")
            self._mostrar_texto_largo(_("Estadísticas de mis redes"), "\n".join(partes))
        task_manager.start_task(_bg, callback=_cb, pass_context=True)

    # ---------------- PONTE AL DÍA (resumen con IA de lo no leído) ----------------
    def _on_ponte_al_dia(self, event):
        """Resume con la IA configurada lo que tienes SIN LEER: artículos RSS no leídos y las
        últimas menciones/notificaciones. Todo con datos locales; solo la llamada a la IA es red."""
        from app.services.ai.ai_service import ai_service
        if not ai_service.obtener_proveedor_activo():
            notificar(_("Configura primero un proveedor de inteligencia artificial en Preferencias."))
            return
        speak(_("Reuniendo lo que no has leído..."), interrupt=True)

        def _bg(ctx):
            trozos = []
            # RSS no leído (título + feed).
            try:
                filas = db.query(
                    "SELECT f.title, a.title FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                    "WHERE COALESCE(a.read,0)=0 AND COALESCE(a.deleted,0)=0 "
                    "ORDER BY a.published_at DESC LIMIT 60")
                if filas:
                    trozos.append(_("Artículos RSS sin leer ({0}):").format(len(filas)))
                    for feed, titulo in filas:
                        trozos.append(f"- [{feed}] {titulo}")
            except Exception:
                pass
            # Menciones de Mastodon recientes (últimas 24 h por fecha de la notificación).
            try:
                filas = db.query(
                    "SELECT from_username, s.content FROM mastodon_notifications n "
                    "LEFT JOIN mastodon_statuses s ON s.status_id = n.status_id "
                    "WHERE n.type = 'mention' ORDER BY n.created_at DESC LIMIT 30")
                if filas:
                    trozos.append("")
                    trozos.append(_("Menciones recientes en Mastodon ({0}):").format(len(filas)))
                    from app.core.html_utils import html_a_texto
                    for user, content in filas:
                        trozos.append(f"- {user}: {html_a_texto(content or '')[:200]}")
            except Exception:
                pass
            if not trozos:
                return None
            texto = "\n".join(trozos)
            instruccion = _("Haz un resumen breve, claro y por temas de lo siguiente, que es lo que "
                            "el usuario no ha leído en sus redes. Agrupa por asuntos y destaca lo más "
                            "importante. Responde en español:\n\n")
            return ai_service.resumir_texto(instruccion + texto)

        def _cb(resumen, err):
            if err:
                notificar(_("No se pudo generar el resumen: {0}").format(err))
                return
            if not resumen:
                notificar(_("No tienes nada pendiente de leer ahora mismo."))
                return
            # La IA a veces devuelve markdown (títulos, listas...) y a veces texto plano. Si es
            # markdown, se muestra como HTML navegable en el visor accesible; si es texto plano, en
            # el diálogo de solo lectura de siempre.
            from app.ui.html_viewer import mostrar_resultado
            mostrar_resultado(self, _("Ponte al día"), resumen, self._mostrar_texto_largo)
        task_manager.start_task(_bg, callback=_cb, pass_context=True)
