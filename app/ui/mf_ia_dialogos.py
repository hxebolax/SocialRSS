# -*- coding: utf-8 -*-
"""MainFrame (mixin): ia_dialogos.
Metodos: _analizar_imagen_toot, _on_ver_descripcion, _on_resumir_entrada_ia, _on_traducir_seleccionado, _on_ver_historial_traducciones, _on_leer_barra_estado, _abrir_dialogo_ai, _on_abrir_visor_logs ...
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import os
import logging
import json
import wx
from app.core.privacy import abrir_url

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.mastodon.mastodon_service import mastodon_service
from app.services.translation.translation_service import translation_service
from app.services.ai.ai_service import ai_service

from .preferences_dialog import PreferencesDialog

log = logging.getLogger(__name__)


class _Mf_ia_dialogos:
    def _analizar_imagen_toot(self, modo="describir"):
        """Descarga la primera imagen del toot enfocado y la describe o le hace OCR."""
        idx = self.lista_items.GetFocusedItem()
        if idx < 0 or idx >= len(self.lista_items_db_ids):
            speak(_("Enfoque una publicación que contenga alguna imagen."), interrupt=True)
            return

        db_id = self.lista_items_db_ids[idx]
        if self.active_bluesky_account_id:
            row = db.query_one("SELECT media_json FROM bluesky_posts WHERE id = ?", (db_id,))
        elif self.active_x_account_id:
            row = db.query_one("SELECT media_json FROM x_tweets WHERE id = ?", (db_id,))
        else:
            row = db.query_one("SELECT media_json FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            notificar(_("Esta publicación no contiene archivos multimedia."))
            return

        try:
            attachments = json.loads(row[0])
        except Exception:
            attachments = []
        # En X el tipo de las fotos es "photo"; en Mastodon/Bluesky es "image".
        images = [att for att in attachments if att.get("type") in ("image", "photo")]
        if not images:
            notificar(_("No se detectaron imágenes en esta publicación."))
            return

        # Imagen difuminada por un filtro del servidor de Mastodon: confirmar antes de analizarla.
        if (self.active_mastodon_account_id and not self.active_bluesky_account_id
                and not self.active_x_account_id and not self._confirmar_media_filtrada(db_id)):
            return
        # Lo mismo con la multimedia oculta por una etiqueta de moderación de Bluesky.
        if (self.active_bluesky_account_id and getattr(self, "bluesky_ctrl", None)
                and not self.bluesky_ctrl._confirmar_media_moderada(db_id)):
            return
        img_url = images[0].get("url")
        speak(_("Extrayendo el texto de la imagen...") if modo in ("ocr", "ocr_local")
              else _("Conectando con la IA para analizar la imagen..."), interrupt=True)

        def _bg_desc(ctx):
            import requests
            from app.core.paths import get_data_path
            temp_path = get_data_path("temp/ia_download.jpg")
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            _hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            res_img = requests.get(img_url, timeout=20, headers=_hdrs)
            temp_path.write_bytes(res_img.content)
            if modo == "ocr_local":
                from app.services.ai import ocr_local
                resultado = ocr_local.ocr_imagen(str(temp_path),
                                                 config.get("general", "spell_language", "es"))
            elif modo == "ocr":
                resultado = ai_service.ocr_imagen(temp_path)
            else:
                resultado = ai_service.describir_imagen(temp_path)
            try: os.remove(temp_path)
            except Exception: pass
            return resultado

        def _finish(desc, err):
            if err:
                notificar(_("Error de la IA: {0}").format(err))
            elif modo in ("ocr", "ocr_local") and not (desc or "").strip():
                notificar(_("No se reconoció ningún texto en la imagen."))
            else:
                titulo = _("Texto de la imagen (OCR)") if modo in ("ocr", "ocr_local") else _("Descripción de IA")
                # Si la IA responde en markdown, se muestra como HTML navegable en el visor
                # accesible; si es texto plano, en el cuadro de solo lectura de siempre. No se
                # locuta aquí: el diálogo/visor ya lo lee al abrirse.
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, titulo, desc, self._mostrar_texto_largo)

        task_manager.start_task(_bg_desc, callback=_finish, pass_context=True)

    def _on_ver_descripcion(self, event):
        """Ctrl+Mayús+D: muestra la descripción (texto alternativo) que el autor puso a la
        multimedia de la publicación enfocada, en un diálogo de solo lectura como el de la
        IA o la traducción. Funciona en Mastodon y Bluesky; en Telegram se hace desde la
        conversación, sobre el mensaje."""
        idx = self.lista_items.GetFocusedItem()
        if idx < 0 or idx >= len(self.lista_items_db_ids):
            speak(_("Enfoque una publicación para ver su descripción."), interrupt=True)
            return
        db_id = self.lista_items_db_ids[idx]
        descripciones = []
        if self.active_bluesky_account_id:
            row = db.query_one("SELECT media_json FROM bluesky_posts WHERE id = ?", (db_id,))
            # Multimedia oculta por moderación: confirmar antes de leer su descripción.
            if (row and row[0] and row[0] != "[]" and getattr(self, "bluesky_ctrl", None)
                    and not self.bluesky_ctrl._confirmar_media_moderada(db_id)):
                return
            if row and row[0]:
                try:
                    for att in json.loads(row[0]):
                        alt = (att.get("alt") or "").strip()
                        if alt:
                            descripciones.append(alt)
                except Exception:
                    pass
        elif self.active_mastodon_account_id:
            row = db.query_one("SELECT media_json FROM mastodon_statuses WHERE id = ?", (db_id,))
            # Multimedia difuminada por un filtro del servidor: confirmar antes de leer el alt.
            if row and row[0] and row[0] != "[]" and not self._confirmar_media_filtrada(db_id):
                return
            if row and row[0]:
                try:
                    for att in json.loads(row[0]):
                        d = (att.get("description") or "").strip()
                        if d:
                            descripciones.append(d)
                except Exception:
                    pass
        elif self.active_telegram_account_id:
            speak(_("Para ver la descripción en Telegram, abra la conversación y pulse Control más Mayúsculas más D sobre el mensaje."), interrupt=True)
            return
        else:
            speak(_("Aquí no hay descripciones de multimedia."), interrupt=True)
            return

        if not descripciones:
            speak(_("Esta publicación no tiene descripción en su multimedia."), interrupt=True)
            return
        if len(descripciones) == 1:
            texto = descripciones[0]
        else:
            texto = "\n\n".join(_("Imagen {0}: {1}").format(i + 1, d) for i, d in enumerate(descripciones))
        # El diálogo muestra la descripción en un cuadro de solo lectura que el lector ya lee al
        # abrirse; no la locutamos aquí para no oírla dos veces (se pisaban entre sí).
        self._mostrar_texto_largo(_("Descripción de la multimedia"), texto)

    def _on_resumir_entrada_ia(self, event):
        idx = self.lista_items.GetFocusedItem()
        if idx < 0 or idx >= len(self.lista_items_db_ids):
            return
            
        db_id = self.lista_items_db_ids[idx]
        
        # Obtener contenido
        texto_resumir = ""
        if self.active_mastodon_account_id:
            row = db.query_one("SELECT content FROM mastodon_statuses WHERE id = ?", (db_id,))
            if row: texto_resumir = row[0]
        elif self.active_rss_account_id:
            row = db.query_one("SELECT content FROM rss_articles WHERE id = ?", (db_id,))
            if row and row[0]:
                # A la IA, el texto limpio (el contenido de RSS es HTML).
                from app.core.html_utils import html_a_texto
                texto_resumir = html_a_texto(row[0])
        elif getattr(self, "active_reddit_account_id", None):
            modo = str(getattr(self, "_modo_lista", ""))
            if modo == "reddit_comments":
                row = db.query_one("SELECT COALESCE(body,'') FROM reddit_comments WHERE id = ?", (db_id,))
                if row: texto_resumir = row[0]
            else:
                row = db.query_one("SELECT COALESCE(title,''), COALESCE(selftext,'') FROM reddit_posts WHERE id = ?", (db_id,))
                if row: texto_resumir = (row[0] + ". " + row[1]).strip(". ")

        if not texto_resumir:
            return
        # En RSS el resumen se GUARDA en el artículo: el lector limpio y la vista previa ya lo
        # mostraban ("Resumen con IA"), pero nada lo escribía, así que no aparecía nunca.
        articulo_rss = db_id if self.active_rss_account_id else None

        speak(_("Generando resumen con Inteligencia Artificial..."), interrupt=True)
        
        def _bg_sum(ctx):
            return ai_service.resumir_texto(texto_resumir)
            
        def _finish(res, err):
            if err:
                notificar(f"Error al generar resumen: {err}")
            else:
                if articulo_rss and res:
                    try:
                        db.execute("UPDATE rss_articles SET ai_summary = ? WHERE id = ?",
                                   (str(res), articulo_rss))
                    except Exception as e:
                        log.debug(f"No se pudo guardar el resumen IA del artículo: {e}")
                # Markdown -> visor HTML navegable; texto plano -> cuadro de solo lectura. El
                # diálogo/visor ya lee el resumen al abrirse (no se locuta aquí).
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Resumen de IA"), res, self._mostrar_texto_largo)

        task_manager.start_task(_bg_sum, callback=_finish, pass_context=True)

    # --- TRADUCCIÓN ---
    def _on_traducir_seleccionado(self, event=None, db_id=None):
        if db_id is None:
            idx = self.lista_items.GetFocusedItem()
            if idx < 0 or idx >= len(self.lista_items_db_ids):
                return
            db_id = self.lista_items_db_ids[idx]

        texto_original = ""
        idioma_origen = ""
        status_id_mastodon = None  # para la traducción NATIVA del servidor (N1)
        cuenta_mastodon = self.active_mastodon_account_id
        if self.active_mastodon_account_id:
            row = db.query_one("SELECT content, COALESCE(language,''), "
                               "COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?", (db_id,))
            if row:
                texto_original = row[0]
                idioma_origen = row[1]
                status_id_mastodon = row[2]
        elif self.active_bluesky_account_id:
            row = db.query_one("SELECT content FROM bluesky_posts WHERE id = ?", (db_id,))
            if row: texto_original = row[0]
        elif self.active_x_account_id:
            row = db.query_one("SELECT text FROM x_tweets WHERE id = ?", (db_id,))
            if row: texto_original = row[0]
        elif self.active_rss_account_id:
            row = db.query_one("SELECT content FROM rss_articles WHERE id = ?", (db_id,))
            if row: texto_original = row[0]
        elif getattr(self, "active_reddit_account_id", None):
            modo = str(getattr(self, "_modo_lista", ""))
            if modo == "reddit_comments":
                row = db.query_one("SELECT COALESCE(body,'') FROM reddit_comments WHERE id = ?", (db_id,))
                if row: texto_original = row[0]
            elif modo == "reddit_messages":
                row = db.query_one("SELECT COALESCE(body,'') FROM reddit_messages WHERE id = ?", (db_id,))
                if row: texto_original = row[0]
            else:
                row = db.query_one("SELECT COALESCE(title,''), COALESCE(selftext,'') FROM reddit_posts WHERE id = ?", (db_id,))
                if row: texto_original = (row[0] + ". " + row[1]).strip(". ")

        if not texto_original:
            speak(_("No hay texto para traducir en este elemento."), interrupt=True)
            return

        speak(_("Traduciendo texto..."), interrupt=True)

        def _bg_translate(ctx):
            from app.core.html_utils import html_a_texto
            target = config.get("translation", "default_target", "es")
            tbase = target.split("-")[0].lower()
            # El contenido viene en HTML: para detectar y traducir mejor, lo pasamos a texto plano.
            texto_plano = html_a_texto(texto_original) or texto_original
            # DOBLE COMPROBACIÓN del idioma: el DECLARADO por la red (poco fiable, a veces queda en el
            # idioma por defecto del autor) y el REAL detectado del texto. Solo se considera que "ya
            # está en tu idioma" si AMBOS coinciden; si discrepan, se traduce igualmente.
            declarado = (idioma_origen or "").split("-")[0].lower()
            detectado = ""
            try:
                detectado = (translation_service.detectar_idioma(texto_plano) or "").split("-")[0].lower()
            except Exception:
                detectado = ""
            if declarado == tbase and detectado == tbase:
                return {"skip": True, "idioma": target}
            # Traducción NATIVA de la instancia de Mastodon (auditoría N1): si tu servidor la
            # ofrece y la preferencia está activa, se usa ANTES que el motor externo (traduce
            # el proveedor configurado por tu instancia y se informa de cuál fue). Si el
            # servidor no puede (sin traducción, idioma no soportado, error), se cae al motor
            # externo elegido en Preferencias.
            if status_id_mastodon and config.get("translation", "mastodon_server", True):
                from app.services.mastodon.mastodon_service import mastodon_service
                caps = mastodon_service.capacidades(cuenta_mastodon)
                if not caps or caps.get("traduccion"):
                    try:
                        client = mastodon_service.obtener_cliente(cuenta_mastodon)
                        res_srv = client.status_translate(status_id_mastodon, lang=tbase)
                        contenido_srv = (res_srv or {}).get("content") or ""
                        if contenido_srv:
                            return {"skip": False,
                                    "texto": html_a_texto(contenido_srv) or contenido_srv,
                                    "detectado": (res_srv.get("detected_source_language")
                                                  or detectado or declarado),
                                    "proveedor": res_srv.get("provider") or "",
                                    "nativa": True}
                    except Exception as e:
                        log.debug("Traducción nativa no disponible, se usa el motor externo: %s", e)
            traducido = translation_service.traducir(texto_plano, idioma_destino=target)
            return {"skip": False, "texto": traducido, "detectado": detectado or declarado}

        def _finish(res, err):
            if err:
                notificar(_("Error de traducción: {0}").format(err))
                return
            if isinstance(res, dict) and res.get("skip"):
                speak(_("La publicación ya está en tu idioma ({0}).").format(res.get("idioma", "")), interrupt=True)
                return
            texto = res.get("texto") if isinstance(res, dict) else res
            # Con traducción nativa se informa del proveedor y el idioma de origen (N1).
            if isinstance(res, dict) and res.get("nativa"):
                detalle = _("Traducido por tu servidor")
                if res.get("proveedor"):
                    detalle = _("Traducido por tu servidor con {0}").format(res["proveedor"])
                if res.get("detectado"):
                    detalle += _(" (desde {0})").format(res["detectado"])
                speak(_("{0}: {1}").format(detalle, texto), interrupt=True)
            else:
                speak(_("Texto traducido: {0}").format(texto), interrupt=True)
            if config.get("translation", "copy_on_translate", True):
                try:
                    if wx.TheClipboard.Open():
                        wx.TheClipboard.SetData(wx.TextDataObject(texto))
                        wx.TheClipboard.Close()
                        notificar(_("Traducción copiada al portapapeles."))
                except Exception:
                    pass
            self._mostrar_texto_largo(_("Traducción"), texto)

        task_manager.start_task(_bg_translate, callback=_finish, pass_context=True)

    def _on_ver_historial_traducciones(self, event):
        from .translation_dialog import TranslationHistoryDialog
        dlg = TranslationHistoryDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_leer_barra_estado(self, event):
        """Verbaliza la información de la barra de estado."""
        try:
            partes = [self.statusbar.GetStatusText(0), self.statusbar.GetStatusText(1)]
            texto = ". ".join(p for p in partes if p)
        except Exception:
            texto = ""
        speak(texto or _("La barra de estado está vacía."), interrupt=True)

    def _abrir_dialogo_ai(self, clase: str):
        import app.ui.ai_dialogs as aid
        dlg = getattr(aid, clase)(self)
        dlg.ShowModal()
        dlg.Destroy()

    # --- VISOR DE LOGS ---
    def _on_abrir_visor_logs(self, event):
        # MODELESS: el visor no bloquea, así puedes consultar y buscar en el log mientras sigues
        # usando la app. Si ya está abierto, se trae al frente en vez de abrir otro.
        from .log_viewer import LogViewerDialog
        existente = getattr(self, "_log_viewer", None)
        if existente:
            try:
                existente.Raise()
                existente.SetFocus()
                return
            except RuntimeError:
                self._log_viewer = None  # el anterior ya se destruyó
        self._log_viewer = LogViewerDialog(self)
        self._log_viewer.Show()
        self._log_viewer.Raise()

    def _on_buscar_global(self, event):
        """Abre el diálogo de búsqueda global en texto completo (Mastodon, Bluesky, RSS, Telegram y X)."""
        from .search_dialog import GlobalSearchDialog
        dlg = GlobalSearchDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_copias_seguridad(self, event):
        """Abre el gestor de copias de seguridad."""
        from .backup_dialog import BackupDialog
        dlg = BackupDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_migrar(self, event):
        """Abre el diálogo de migración a otro ordenador (exportar/importar con contraseña)."""
        from .migration_dialog import MigracionDialog
        dlg = MigracionDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_borrar_datos(self, event):
        """Abre el diálogo de borrado de datos a medida."""
        from .cleanup_dialog import CleanupDialog
        dlg = CleanupDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_filtros_contenido(self, event):
        """Abre el gestor de filtros de contenido (Mastodon + RSS) y refresca la lista al cerrar."""
        from .filters_dialog import FiltersDialog
        dlg = FiltersDialog(self)
        dlg.ShowModal()
        dlg.Destroy()
        # Reaplicar filtros a la vista actual sin recargar de red
        if self.active_mastodon_account_id:
            self._cargar_listado_toots(cache_only=True)
        elif self.active_rss_account_id:
            self._cargar_listado_artículos()

    # --- PREFERENCIAS ---
    def _on_preferencias(self, event):
        dlg = PreferencesDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._aplicar_preferencias_en_caliente()
        dlg.Destroy()

    def _aplicar_preferencias_en_caliente(self):
        """Aplica al instante (sin reiniciar) la mayoría de las preferencias. NO reconstruye el
        árbol ni mueve el foco. Lo único que sigue necesitando reinicio es activar/desactivar
        servicios (Mastodon, Bluesky, RSS, Telegram, X) y el cambio de idioma de la interfaz."""
        # 1) Intervalo/encendido del sondeo automático y streaming (cada servicio su intervalo;
        #    Mastodon: tiempo real o intervalo, según preferencia).
        try:
            self._aplicar_servicio_activo()
        except Exception:
            pass
        # 1a-bis) Reconstruir la barra de menús EN CALIENTE: así el submenú "Administración" de
        #         Mastodon aparece o desaparece al instante al activar/desactivar su preferencia.
        try:
            self._crear_menus()
        except Exception:
            pass
        # 1b) Botonera de acciones: mostrar/ocultar al instante según la preferencia.
        try:
            self._actualizar_botonera()
        except Exception:
            pass
        # 1c) Interfaz invisible: registrar o quitar las teclas globales según la preferencia.
        try:
            self._instalar_interfaz_invisible()
        except Exception:
            pass
        # 2) Proxy de red (idempotente).
        try:
            from app.core.net import aplicar_configuracion_proxy
            aplicar_configuracion_proxy()
        except Exception:
            pass
        # 2b) Preferencias del servidor (visibilidad por defecto): si la opción está activada,
        #     recargarlas en segundo plano (force) para que ACTIVARLA surta efecto sin reiniciar.
        if config.get("mastodon", "read_preferences_from_instance", False):
            def _bg_prefs(ctx):
                for (mid,) in db.query("SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1"):
                    try:
                        mastodon_service.obtener_preferencias_servidor(mid, force=True)
                    except Exception:
                        pass
                return True
            try:
                task_manager.start_task(_bg_prefs, pass_context=True)
            except Exception:
                pass
        # 3) Refrescar las marcas de tiempo y RECONSTRUIR la lista activa para que se note de
        # inmediato lo que afecta a la presentación: ocultar emojis (contenido y nombres),
        # mostrar @usuario, plantillas de lectura, expandir CW, orden nuevo-arriba/abajo,
        # tiempos relativos, navegación por columnas, etc. (Solo la lista; el árbol no se toca.)
        try:
            self._recargar_vista_activa()
        except Exception as e:
            log.debug(f"No se pudo recargar la vista activa tras preferencias: {e}")
        # 4) Panel de detalles de Mastodon (mostrar/ocultar al instante según la casilla).
        try:
            self._aplicar_panel_detalles_mastodon()
        except Exception:
            pass
        # 5) Bandeja de entrada: si se activó/desactivó, reconstruir el árbol para que su nodo
        #    aparezca o desaparezca en caliente.
        try:
            if self._inbox_habilitada() != getattr(self, "_inbox_enabled_actual", False):
                self._cargar_navegacion()
        except Exception:
            pass
        notificar(_("Preferencias aplicadas. Solo activar o desactivar servicios y cambiar el "
                    "idioma requieren reiniciar la aplicación."))

    def _aplicar_panel_detalles_mastodon(self):
        """Aplica EN CALIENTE, para la red activa, la visibilidad del panel de detalles y de la
        botonera de acciones (ambos opcionales por red en Preferencias) y, si el panel queda
        visible, lo rellena con el detalle del elemento enfocado. Al navegar entre buffers ya lo
        gestiona _on_nav_selection; esto cubre el cambio de preferencias sin reiniciar."""
        try:
            self._aplicar_visibilidad_detalles()
        except Exception:
            pass
        try:
            self._actualizar_botonera()
        except Exception:
            pass
        if self.txt_detalles.IsShown():
            idx = self.lista_items.GetFocusedItem()
            if idx is not None and idx >= 0:
                ev = wx.ListEvent(wx.wxEVT_COMMAND_LIST_ITEM_SELECTED, self.lista_items.GetId())
                ev.SetIndex(idx)
                try:
                    self._on_item_selected(ev)
                except Exception:
                    pass

    def _recargar_vista_activa(self):
        """Vuelve a pintar la lista del buffer activo desde la caché (sin red), conservando el
        foco, para aplicar en caliente las opciones de presentación/lectura."""
        if getattr(self, "_bandeja_activa", False):
            # La bandeja recarga su lista y reaplica panel de detalles/botonera según sus preferencias.
            try:
                self._cargar_listado_bandeja()
            except Exception:
                pass
            return
        if self.active_mastodon_account_id:
            self._cargar_listado_toots(cache_only=True)
        elif self.active_rss_account_id:
            self._cargar_listado_artículos()
        elif self.active_telegram_account_id:
            try:
                self._cargar_listado_chats_telegram()
            except Exception:
                pass
        elif self.active_bluesky_account_id:
            try:
                self.bluesky_ctrl.cargar_listado()
            except Exception:
                pass
        elif self.active_x_account_id and self.x_ctrl is not None:
            try:
                self.x_ctrl.cargar_listado()
            except Exception:
                pass
        # Re-anunciar la fila enfocada YA, con la nueva lectura, para que el cambio (plantilla,
        # emojis...) se oiga al instante sin tener que moverse.
        try:
            self.lista_items.reanunciar_foco()
        except Exception:
            pass

    # --- AYUDA Y MANUAL ---
    def _abrir_documento_md(self, base_compilado, base_raiz, titulo, salida, aviso):
        """Localiza un documento markdown (manual o teclas), lo convierte a HTML y lo abre en
        el navegador. Busca: <base>_<idioma>.md y <base>_es.md en data/docs (compilado), y en
        la raíz <base_raiz>_<idioma>.md y <BASE_RAIZ>.md (código fuente)."""
        from app.core.paths import get_data_path
        lang = config.get("general", "language", "es")
        raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidatos = [
            str(get_data_path(f"docs/{base_compilado}_{lang}.md")),
            str(get_data_path(f"docs/{base_compilado}_es.md")),
            os.path.join(raiz, f"{base_compilado}_{lang}.md"),
            os.path.join(raiz, f"{base_raiz}.md"),
            os.path.join(os.getcwd(), f"{base_raiz}.md"),
        ]
        ruta_md = next((c for c in candidatos if os.path.exists(c)), None)
        if not ruta_md:
            notificar(aviso)
            return
        try:
            with open(ruta_md, "r", encoding="utf-8") as fh:
                texto_md = fh.read()
            # Convertir Markdown a HTML con un índice navegable ([TOC]).
            try:
                import markdown
                if "[TOC]" not in texto_md:
                    texto_md = "[TOC]\n\n" + texto_md
                cuerpo = markdown.markdown(
                    texto_md,
                    extensions=["tables", "toc", "fenced_code", "sane_lists", "attr_list"]
                )
            except Exception as e_md:
                log.warning(f"No se pudo convertir el documento con markdown: {e_md}")
                import html as _html
                cuerpo = "<pre>" + _html.escape(texto_md) + "</pre>"
            from app.core.html_utils import html_a_documento
            documento = html_a_documento(titulo, cuerpo)
            ruta = get_data_path("temp") / salida
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write(documento)
            abrir_url(ruta.as_uri())
            speak(_("Abriendo en el navegador."), interrupt=True)
        except Exception as e:
            log.error(f"Error al abrir el documento {salida}: {e}")
            notificar(aviso)

    def _on_ver_teclas_documento(self, event):
        """Abre el DOCUMENTO completo de teclas (teclas_<idioma>.md / TECLAS.md) en el navegador,
        según el idioma activo. Es independiente del diálogo dinámico de _on_ver_teclas."""
        self._abrir_documento_md(
            "teclas", "TECLAS", _("Teclas de la aplicación - SocialRSS"), "teclas.html",
            _("No se pudo abrir el documento de teclas."))

    def _on_ver_manual(self, event):
        self._abrir_documento_md(
            "manual", "MANUAL", _("Manual de usuario - SocialRSS"), "manual.html",
            _("No se pudo abrir el manual."))

    def _on_ver_teclas(self, event):
        """Genera el listado de teclas AL VUELO desde el catálogo (shortcut_manager), con las
        combinaciones ACTUALES (reflejando las que el usuario haya personalizado) y lo muestra en el
        diálogo de solo lectura del proyecto. Las secciones no editables se añaden como referencia."""
        from .shortcut_manager import shortcut_manager
        lineas = [_("TECLAS DE LA APLICACIÓN"), ""]
        lineas.append(_("Puedes personalizar estas teclas en Preferencias, General, "
                        "«Personalizar combinaciones de teclas». Las marcadas como «personalizada» "
                        "las has cambiado tú."))
        lineas.append("")
        for sec in shortcut_manager.secciones():
            lineas.append(f"=== {sec} ===")
            for accion in shortcut_manager.acciones_de(sec):
                etiqueta = shortcut_manager.etiqueta(accion)
                combo = shortcut_manager.combo_legible(accion)
                marca = _(" (personalizada)") if shortcut_manager.es_personalizado(accion) else ""
                lineas.append(f"{combo}: {etiqueta}{marca}")
            lineas.append("")
        # Secciones FIJAS (no editables), como referencia.
        lineas.append(_("=== Navegación (teclas fijas) ==="))
        lineas.append(_("Alt+1 a Alt+9: ir al primer..noveno servicio según el orden del árbol."))
        lineas.append(_("Ctrl+Mayús+1 a Ctrl+Mayús+9: ir al buffer N de la cuenta enfocada."))
        lineas.append(_("Ctrl+H: anunciar la descripción del control en el que estás."))
        lineas.append("")
        # Las teclas de los reproductores ya salen arriba, en su propia sección del catálogo (son
        # personalizables). Aquí solo se recuerdan las que NO se editan.
        lineas.append(_("=== Reproductores: dentro de su ventana (teclas fijas) ==="))
        lineas.append(_("Espacio y las flechas controlan la reproducción cuando estás en la "
                        "ventana del reproductor; consulta el manual para el detalle."))
        lineas.append("")
        lineas.append(_("En los diálogos: Alt+letra subrayada activa cada botón; Intro acepta y "
                        "Escape cierra."))
        self._mostrar_texto_largo(_("Teclas de la aplicación"), "\n".join(lineas))

    def _on_ver_teclas_invisibles(self, event):
        """Lista, al vuelo, las teclas de la INTERFAZ INVISIBLE con las combinaciones actuales
        (reflejando las personalizadas), agrupadas por sección, para consultarlas rápido."""
        from app.ui.invisible_keymap import invisible_keymap, combo_legible
        activa = config.get("accessibility", "interfaz_invisible", False)
        lineas = [_("TECLAS DE LA INTERFAZ INVISIBLE"), ""]
        lineas.append(_("Estado: {0}.").format(_("activada") if activa else _("desactivada")))
        lineas.append(_("Se activan y se personalizan en Preferencias, Accesibilidad. Manejan la "
                        "aplicación con teclas globales aunque esté en segundo plano."))
        lineas.append("")
        for sec in invisible_keymap.secciones():
            lineas.append(f"=== {sec} ===")
            for accion in invisible_keymap.acciones_de(sec):
                etiqueta = invisible_keymap.etiqueta(accion)
                tecla = invisible_keymap.tecla(accion)
                combo = combo_legible(tecla) if tecla else _("(sin tecla)")
                lineas.append(f"{combo}: {etiqueta}")
            lineas.append("")
        self._mostrar_texto_largo(_("Teclas de la interfaz invisible"), "\n".join(lineas))

    def _on_ver_changelog(self, event):
        """Muestra el changelog (novedades de la versión actual) en el MISMO diálogo de solo lectura
        que aparece al actualizar (con su botón Aceptar), no en el navegador."""
        from app.core.paths import get_data_path
        raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidatos = [
            str(get_data_path("docs/changelog.txt")),
            os.path.join(raiz, "changelog.txt"),
            os.path.join(os.getcwd(), "changelog.txt"),
        ]
        for cand in candidatos:
            try:
                if os.path.exists(cand):
                    with open(cand, "r", encoding="utf-8") as fh:
                        texto_cl = fh.read().strip()
                    if texto_cl:
                        self._mostrar_texto_largo(_("Novedades de esta versión"), texto_cl)
                        return
            except Exception as e:
                log.debug(f"No se pudo leer el changelog '{cand}': {e}")
        notificar(_("No se pudo abrir el changelog."))

    def _on_prueba_sonidos(self, event):
        from .sound_test_dialog import DialogoPruebaSonidos
        dlg = DialogoPruebaSonidos(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_acerca_de(self, event):
        # Diálogo propio (accesible, con botón Aceptar en español).
        try:
            from app.core.version import APP_VERSION
        except Exception:
            APP_VERSION = ""
        texto = _(
            "SocialRSS Accessible Suite\n"
            "Versión: {version}\n"
            "Autor: Héctor J. Benítez Corredera\n"
            "Copyright (C) 2026 Héctor J. Benítez Corredera\n"
            "Licencia: GPL (Licencia Pública General de GNU)\n\n"
            "Cliente accesible para Windows que reúne en un solo programa varias redes sociales, "
            "mensajería y noticias, diseñado al 100 % para el manejo por teclado y lectores de "
            "pantalla (NVDA / JAWS).\n\n"
            "Qué puedes hacer en cada red:\n\n"
            "- Mastodon: la red social descentralizada (fediverso). Lee y publica toots e hilos, "
            "responde, cita, marca favoritos y marcadores, impulsa, sigue cronologías, listas y "
            "búsquedas, y recíbelo todo en tiempo real. Su fuerte: una comunidad abierta, sin "
            "anuncios y con control fino de la privacidad de cada publicación.\n\n"
            "- Bluesky: red social abierta basada en el protocolo AT. Publica posts e hilos, "
            "responde, cita, da me gusta y repostea, y disfruta de sus feeds personalizados y "
            "listas. Su fuerte: feeds a tu medida y una red moderna en plena expansión.\n\n"
            "- X (Twitter): cliente NO oficial. Lee y publica, responde, cita y da me gusta. Su "
            "fuerte: seguir lo que ocurre en X sin salir de aquí (con los avisos de riesgo que "
            "verás al activarlo).\n\n"
            "- RSS: tu lector de noticias y podcasts. Organiza tus feeds en carpetas, marca leídos "
            "y favoritos, guarda para más tarde, aplica reglas y filtros, escucha podcasts y lee "
            "los artículos en voz alta. Su fuerte: toda tu información, de cualquier web, en un "
            "solo sitio y sin distracciones.\n\n"
            "- Telegram: mensajería completa. Chats, grupos, canales y bots, con multimedia, notas "
            "de voz y todas las acciones sobre los mensajes. Su fuerte: una mensajería potente, "
            "manejada por completo desde el teclado.\n\n"
            "- IRC: el chat en tiempo real de siempre. Conéctate a varias redes, entra en canales, "
            "habla en privado, con whois, ignorados y acciones de operador, historial y monitor de "
            "amigos. Su fuerte: las comunidades clásicas (software libre, soporte) en salas públicas, "
            "ligero y directo.\n\n"
            "- Matrix: mensajería federada y cifrada. Salas y mensajes directos con cifrado de "
            "extremo a extremo (E2EE) y verificación por emojis, adjuntos, reacciones, hilos, "
            "espacios y moderación. Su fuerte: privacidad real en una red abierta y federada, con "
            "puentes a otras redes.\n\n"
            "- XMPP (Jabber): mensajería libre y federada. Contactos con presencia, grupos, cifrado "
            "OMEMO de extremo a extremo, archivos y notas de voz, administración del servidor y "
            "pasarelas para hablar con WhatsApp y otras redes desde aquí. Su fuerte: un estándar "
            "abierto y veterano, con cifrado y puentes a redes cerradas.\n\n"
            "Y además: una Bandeja de entrada que reúne, en una sola lista ordenada por tiempo, las "
            "publicaciones de las redes que elijas."
        ).format(version=APP_VERSION)
        from .accessible_controls import mostrar_info
        mostrar_info(self, texto, _("Acerca de SocialRSS Accessible Suite"))

    def _on_invitar_cafe(self, event):
        """Abre la página de donaciones de PayPal del autor en el navegador."""
        abrir_url("https://www.paypal.com/paypalme/hjbcdonaciones?country.x=ES&locale.x=es_ES")
        notificar(_("Gracias. Se ha abierto la página de donaciones en el navegador."))

    # --- MENU CONTEXTUAL PARA LISTAS ---
