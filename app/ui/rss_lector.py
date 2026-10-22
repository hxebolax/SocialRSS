# -*- coding: utf-8 -*-
"""
Lector limpio de artículos RSS/Podcast (Ctrl+Mayús+E): vista de texto accesible y vista HTML
navegable, temas, tamaño de letra, traducción, lectura en voz alta y exportación.

Si el feed solo trae un resumen, el lector descarga de la web el ARTÍCULO COMPLETO al abrirse
(Preferencias, RSS) o con su botón "Descargar el artículo completo", y lo sustituye en cuanto
llega sin cerrar el diálogo. Separado de rss_views.py (que lo reexporta) para mantener los
archivos por debajo de 50 KB.
"""

import logging
import wx
import wx.html
from app.core.privacy import abrir_url

from app.core.speech import speak, notificar
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service
from .accessible_controls import AccessibleTextCtrl, mostrar_info
from .rss_lector_nav import _NavEstructuraMixin

log = logging.getLogger(__name__)


class CleanArticleViewer(_NavEstructuraMixin, wx.Dialog):
    """Visor accesible de lectura limpia para artículos de RSS/Podcast."""
    
    def __init__(self, parent, article_db_id: str):
        super().__init__(
            parent,
            title=_("Lector RSS Limpio"),
            size=(700, 500),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo lector limpio")
        )
        
        self.article_db_id = article_db_id
        self.link = ""
        self.content_txt = ""
        self.title = ""
        # Este visor usa Ctrl+H para ALTERNAR texto/HTML: se marca para que el filtro global
        # de ayuda contextual (Ctrl+H en cualquier diálogo) le ceda la tecla y no lo rompa.
        self._ctrl_h_propio = True
        self._descargando_completo = False

        self._init_ui()
        self._cargar_contenido()
        self.CenterOnParent()

    # Paletas de los temas de lectura (fondo, texto)
    _TEMAS = {
        "claro": ("#ffffff", "#1a1a1a"),
        "oscuro": ("#1e1e1e", "#e6e6e6"),
        "sepia": ("#f4ecd8", "#5b4636"),
    }

    def _init_ui(self):
        from app.core.config_manager import config
        sizer = wx.BoxSizer(wx.VERTICAL)

        self.modo = "texto"           # "texto" o "html"
        self._html_content = ""       # HTML original para el modo HTML
        self.tema = config.get("accessibility", "reading_theme", "claro")
        try:
            self.font_size = int(config.get("accessibility", "viewer_font_size", 14))
        except Exception:
            self.font_size = 14

        # Campo de texto accesible para lectura
        self.txt_lector = AccessibleTextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2,
            name=_("Cuerpo del artículo simplificado"),
            ayuda_texto=_("Contenido del artículo. H y los números del 1 al 6 saltan por encabezados, "
                          "T por tablas, L por listas, Q por citas y K por enlaces (con Mayúsculas, "
                          "hacia atrás); Intro sobre una tabla la muestra en una lista. Ctrl+F busca. "
                          "Ctrl+H alterna texto y HTML; Ctrl+Más y Ctrl+Menos cambian el tamaño de letra.")
        )
        sizer.Add(self.txt_lector, 1, wx.EXPAND | wx.ALL, 10)

        # Vista HTML (oculta por defecto), para alternar con Ctrl+H sin salir. La vista AVANZADA
        # (AccessibleWebView / WebView de Edge), que es navegable con NVDA/JAWS, se crea de forma
        # PEREZOSA la primera vez que pulsas Ctrl+H, NO al abrir el diálogo: así el visor se abre
        # siempre al instante en modo texto (crear un WebView en el constructor de un diálogo modal
        # podía dejarlo sin pintar). El wx.html.HtmlWindow, ligero, queda como último recurso.
        self._awv = None              # AccessibleWebView (preferente), perezoso
        self._webview = None          # wx.html2.WebView (respaldo), perezoso
        self._vista_avanzada_intentada = False
        self.html_win = wx.html.HtmlWindow(self, name=_("Vista HTML del artículo"))
        self.html_win.Hide()
        sizer.Add(self.html_win, 1, wx.EXPAND | wx.ALL, 10)

        # Botones de acción
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_nav = wx.Button(self, label=_("Abrir en &Navegador (Ctrl+Enter)"), name=_("Abrir enlace original"))
        self.btn_nav.Bind(wx.EVT_BUTTON, self._on_abrir_navegador)
        btn_sizer.Add(self.btn_nav, 0, wx.ALL, 5)

        self.btn_modo = wx.Button(self, label=_("Texto / &HTML (Ctrl+H)"), name=_("Alternar entre texto y HTML"))
        self.btn_modo.Bind(wx.EVT_BUTTON, lambda e: self._alternar_modo())
        btn_sizer.Add(self.btn_modo, 0, wx.ALL, 5)

        sizer_tema = wx.BoxSizer(wx.HORIZONTAL)
        sizer_tema.Add(wx.StaticText(self, label=_("Tema:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        self.choice_tema = wx.Choice(self, choices=[_("Claro"), _("Oscuro"), _("Sepia")], name=_("Tema de lectura"))
        self.choice_tema.SetSelection({"claro": 0, "oscuro": 1, "sepia": 2}.get(self.tema, 0))
        self.choice_tema.Bind(wx.EVT_CHOICE, self._on_cambiar_tema)
        sizer_tema.Add(self.choice_tema, 0, wx.ALL, 3)
        btn_sizer.Add(sizer_tema, 0, wx.ALIGN_CENTER_VERTICAL)

        self.btn_copiar = wx.Button(self, label=_("&Copiar contenido"), name=_("Copiar artículo completo"))
        self.btn_copiar.Bind(wx.EVT_BUTTON, self._on_copiar)
        btn_sizer.Add(self.btn_copiar, 0, wx.ALL, 5)

        self.btn_traducir = wx.Button(self, label=_("&Traducir"), name=_("Traducir artículo"))
        self.btn_traducir.Bind(wx.EVT_BUTTON, self._on_traducir)
        btn_sizer.Add(self.btn_traducir, 0, wx.ALL, 5)

        self.btn_tts = wx.Button(self, label=_("&Leer en voz alta"), name=_("Leer el artículo en voz alta (Ctrl+Shift+L)"))
        self.btn_tts.Bind(wx.EVT_BUTTON, self._on_leer_tts)
        btn_sizer.Add(self.btn_tts, 0, wx.ALL, 5)

        self.btn_completo = wx.Button(self, label=_("Descargar el &artículo completo"),
                                      name=_("Descargar de la web el artículo completo"))
        self.btn_completo.Bind(wx.EVT_BUTTON, self._on_articulo_completo)
        btn_sizer.Add(self.btn_completo, 0, wx.ALL, 5)

        self.btn_exportar = wx.Button(self, label=_("E&xportar..."), name=_("Exportar el artículo a un archivo"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        btn_sizer.Add(self.btn_exportar, 0, wx.ALL, 5)

        btn_sizer.AddStretchSpacer()
        btn_sizer.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 5)

        sizer.Add(btn_sizer, 0, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(sizer)

        self._aplicar_tema_fuente()

        # Atajos del visor. Se usa EVT_CHAR_HOOK en el diálogo para que las teclas (Ctrl+H, etc.)
        # se capturen ANTES que el control con el foco, incluso desde el cuadro de texto o la vista
        # HtmlWindow. (La vista WebView, que es nativa y se traga las teclas, usa además un puente
        # JavaScript para Ctrl+H; ver _crear_vista_html_avanzada.)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key_down)

    def _aplicar_tema_fuente(self):
        """Aplica el tema (fondo/color) y el tamaño de fuente al campo de texto."""
        bg, fg = self._TEMAS.get(self.tema, self._TEMAS["claro"])
        try:
            self.txt_lector.SetBackgroundColour(wx.Colour(bg))
            self.txt_lector.SetForegroundColour(wx.Colour(fg))
            fuente = self.txt_lector.GetFont()
            fuente.SetPointSize(max(8, min(self.font_size, 40)))
            self.txt_lector.SetFont(fuente)
            self.txt_lector.Refresh()
        except Exception:
            pass

    def _on_cambiar_tema(self, event):
        from app.core.config_manager import config
        self.tema = {0: "claro", 1: "oscuro", 2: "sepia"}.get(self.choice_tema.GetSelection(), "claro")
        config.set("accessibility", "reading_theme", self.tema)
        self._aplicar_tema_fuente()
        if self.modo == "html":
            self._render_html()

    def _cambiar_fuente(self, delta):
        from app.core.config_manager import config
        self.font_size = max(8, min(self.font_size + delta, 40))
        # Diferido: se pulsa repetidamente para ajustar el tamaño (un fsync por pulsación).
        config.set_diferido("accessibility", "viewer_font_size", self.font_size)
        self._aplicar_tema_fuente()
        speak(_("Tamaño de letra: {0}").format(self.font_size), interrupt=True)

    def _documento_html(self):
        """Construye el documento HTML COMPLETO (semántico) del artículo para la vista WebView,
        con idioma, tema y tipografía legible. Es lo que NVDA/JAWS recorren como una web."""
        import html as _html
        bg, fg = self._TEMAS.get(self.tema, self._TEMAS["claro"])
        cuerpo = self._html_content or "<p>(Sin contenido)</p>"
        titulo = _html.escape(self.title or "")
        link = _html.escape(self.link or "")
        enlace_html = f'<p><a href="{link}">{link}</a></p>' if link else ""
        return (
            "<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; "
            "style-src 'unsafe-inline'; img-src https: data:; script-src 'none'\">"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>html,body{{background:{bg};color:{fg};"
            f"font-family:Segoe UI,Arial,sans-serif;font-size:{self.font_size}px;"
            "line-height:1.5;padding:12px;}a{color:#1a6ed8;}img{max-width:100%;}</style>"
            f"<title>{titulo}</title></head><body>"
            f"<h1>{titulo}</h1>{enlace_html}<hr>{cuerpo}</body></html>")

    def _render_html(self):
        """Pinta el HTML del artículo en la vista activa (AccessibleWebView, WebView o HtmlWindow)."""
        bg, fg = self._TEMAS.get(self.tema, self._TEMAS["claro"])
        cuerpo = self._html_content or "<p>(Sin contenido)</p>"
        if self._awv is not None:
            # AccessibleWebView: set_content recibe el CUERPO; la librería lo envuelve en un
            # documento semántico accesible. El tema va en un envoltorio con estilo en línea.
            import html as _html
            link = _html.escape(self.link or "")
            titulo = _html.escape(self.title or "")
            enlace = f'<p><a href="{link}">{link}</a></p>' if link else ""
            cuerpo_html = (f'<div style="background:{bg};color:{fg};">'
                           f'<h1>{titulo}</h1>{enlace}<hr>{cuerpo}</div>')
            try:
                self._awv.set_content(cuerpo_html)
                # Inyectar (una sola vez) un listener para que Ctrl+H avise a Python y se alterne a
                # texto desde dentro del WebView. Se engancha a 'document' para sobrevivir a los
                # cambios de contenido.
                js = ("if(!window.__visorKeyH){window.__visorKeyH=1;"
                      "document.addEventListener('keydown',function(e){"
                      "if(e.ctrlKey&&!e.altKey&&(e.key==='h'||e.key==='H')){e.preventDefault();"
                      "try{window.visor.postMessage(JSON.stringify({type:'toggle'}));}catch(x){}}}"
                      ",true);}")
                try:
                    self._awv.run_js(js)
                except Exception:
                    pass
                return
            except Exception as e:
                log.debug(f"No se pudo pintar en AccessibleWebView: {e}")
        if self._webview is not None:
            try:
                self._webview.SetPage(self._documento_html(), "")
                return
            except Exception as e:
                log.debug(f"No se pudo pintar en el WebView: {e}")
        import html as _html
        doc = (f'<body bgcolor="{bg}" text="{fg}">'
               f'<h2>{_html.escape(self.title or "")}</h2>{cuerpo}</body>')
        try:
            self.html_win.SetPage(doc)
        except Exception:
            self.html_win.SetPage("<p>No se pudo mostrar el HTML.</p>")

    def _crear_vista_html_avanzada(self):
        """Crea PEREZOSAMENTE la vista HTML navegable (AccessibleWebView o, si no, WebView de Edge)
        e insértala en el diálogo. Se llama la primera vez que se pasa a HTML. Si no hay ninguna
        disponible, se usará el wx.html.HtmlWindow ya creado. Robusto: nunca rompe el visor."""
        if self._vista_avanzada_intentada:
            return
        self._vista_avanzada_intentada = True
        sizer = self.GetSizer()
        # Insertar la vista justo antes de la botonera (último elemento del sizer).
        pos = max(0, sizer.GetItemCount() - 1)
        try:
            from wx_accessible_webview import AccessibleWebView
            # handler_name + on_message: puente JS->Python para que Ctrl+H funcione DENTRO del
            # WebView (que se traga las teclas). El listener JS se inyecta al renderizar.
            self._awv = AccessibleWebView(
                self, title=_("Vista HTML del artículo"), lang="es",
                live_region=False, open_links_externally=True,
                handler_name="visor", on_message=self._on_awv_mensaje)
            self._awv.control.Hide()
            sizer.Insert(pos, self._awv.control, 1, wx.EXPAND | wx.ALL, 10)
            self.Layout()
            return
        except Exception as e:
            log.debug(f"wx-accessible-webview no disponible: {e}")
            self._awv = None
        try:
            import wx.html2
            if wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge):
                self._webview = wx.html2.WebView.New(self, backend=wx.html2.WebViewBackendEdge,
                                                     name=_("Vista HTML del artículo"))
            else:
                self._webview = wx.html2.WebView.New(self, name=_("Vista HTML del artículo"))
            self._webview.Hide()
            self._webview.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self._on_webview_navegando)
            sizer.Insert(pos, self._webview, 1, wx.EXPAND | wx.ALL, 10)
            self.Layout()
        except Exception as e:
            log.debug(f"No hay WebView; se usará HtmlWindow: {e}")
            self._webview = None

    def _vista_html_control(self):
        """Devuelve el control de la vista HTML activo (AccessibleWebView, WebView o HtmlWindow)."""
        if self._awv is not None:
            return self._awv.control
        return self._webview if self._webview is not None else self.html_win

    def _on_awv_mensaje(self, data):
        """Recibe mensajes JS del AccessibleWebView. 'toggle' (Ctrl+H dentro del WebView) alterna a
        la vista de texto."""
        try:
            if isinstance(data, dict) and data.get("type") == "toggle":
                wx.CallAfter(self._alternar_modo)
        except Exception:
            pass

    def _on_webview_navegando(self, event):
        """Cuando el usuario activa un enlace en el WebView, se abre en el navegador del sistema
        (no se navega dentro del visor). La carga inicial del propio artículo sí se permite."""
        import wx.html2
        url = event.GetURL() or ""
        # Permitir la carga del documento que ponemos nosotros (about:blank / data); abrir fuera
        # cualquier enlace http(s) real que pulse el usuario.
        if url.startswith(("http://", "https://")):
            event.Veto()
            from app.core.privacy import limpiar_url
            abrir_url(limpiar_url(url))
            speak(_("Abriendo enlace en el navegador."), interrupt=True)

    def _alternar_modo(self):
        """Alterna entre la vista de texto accesible y la vista HTML, sin cerrar."""
        if self.modo == "texto":
            self._crear_vista_html_avanzada()   # crea la vista navegable la primera vez
        ctrl_html = self._vista_html_control()
        if self.modo == "texto":
            self.modo = "html"
            self._render_html()
            self.txt_lector.Hide()
            ctrl_html.Show()
            # Dar el foco a la vista HTML para que el lector entre a navegar el documento.
            if self._awv is not None:
                wx.CallAfter(self._awv.focus)
            else:
                wx.CallAfter(ctrl_html.SetFocus)
            speak(_("Vista HTML. Navega el artículo con las flechas y los comandos del lector."), interrupt=True)
        else:
            self.modo = "texto"
            ctrl_html.Hide()
            self.txt_lector.Show()
            self.txt_lector.SetFocus()
            speak(_("Vista de texto."), interrupt=True)
        self.Layout()

    def _on_key_down(self, event):
        # Navegación por la estructura (letras, Intro, Ctrl+F, F3): rss_lector_nav.py.
        if self._tecla_estructura(event):
            return
        key = event.GetKeyCode()
        mod = event.GetModifiers()
        if key == wx.WXK_RETURN and mod == wx.ACCEL_CTRL:
            self._on_abrir_navegador(None)
        elif mod == wx.ACCEL_CTRL and key in (ord('H'),):
            self._alternar_modo()
        elif mod == wx.ACCEL_CTRL and key in (ord('+'), wx.WXK_NUMPAD_ADD, ord('=')):
            self._cambiar_fuente(1)
        elif mod == wx.ACCEL_CTRL and key in (ord('-'), wx.WXK_NUMPAD_SUBTRACT):
            self._cambiar_fuente(-1)
        elif mod == (wx.ACCEL_CTRL | wx.ACCEL_SHIFT) and key == ord('L'):
            self._on_leer_tts(None)
        else:
            event.Skip()

    def _cargar_contenido(self):
        # Intentar obtener el contenido completo guardado localmente
        row = db.query_one("""
            SELECT a.title, f.title, a.published_at, a.content, a.link, a.ai_summary,
                   COALESCE(f.no_images, 0)
            FROM rss_articles a
            JOIN rss_feeds f ON a.feed_id = f.id
            WHERE a.id = ?
        """, (self.article_db_id,))

        if not row:
            speak(_("Artículo no encontrado."), interrupt=True)
            self.EndModal(wx.ID_CANCEL)
            return

        self.title, feed_name, date, content, self.link, summary, feed_sin_imagenes = row
        
        self._feed_name, self._fecha, self._resumen_ia = feed_name, date, summary
        self._sin_imagenes = bool(feed_sin_imagenes)
        # Artículo completo de la web (Preferencias, RSS): si nunca se ha intentado y el feed
        # solo trae un resumen, se pide en segundo plano y se sustituye al llegar.
        from app.core.config_manager import config as _cfg_ft
        pedir_completo = bool(self.link) and _cfg_ft.get("rss", "fulltext_on_open", True) and \
            rss_service.texto_completo_pendiente(self.article_db_id)
        texto_limpio = self._componer_texto(
            content, _("Descargando el artículo completo de la web...") if pedir_completo else "")
        self._poner_texto(texto_limpio, self._est_compuesta)
        # Dejar el foco en el cuadro de lectura, al PRINCIPIO del texto.
        self.txt_lector.SetInsertionPoint(0)
        self.txt_lector.ShowPosition(0)
        wx.CallAfter(self.txt_lector.SetFocus)
        speak(f"Lector de artículo: {self.title}", interrupt=True)
        if pedir_completo:
            self._pedir_articulo_completo(manual=False)

        # Registrar en el historial de lectura (para historial y estadísticas), en segundo plano.
        try:
            task_manager.start_task(lambda ctx: rss_service.registrar_lectura(self.article_db_id),
                                    pass_context=True)
        except Exception:
            pass

        # Complementos: avisar de que se ha abierto un artículo.
        try:
            from app.core.plugins import plugin_manager, PluginHook
            plugin_manager.dispatch(PluginHook.ARTICLE_OPENED, article={
                "id": self.article_db_id, "title": self.title, "link": self.link})
        except Exception:
            pass

        from app.core.config_manager import config
        # Abrir directamente en HTML si así se prefiere en las opciones de lectura.
        if config.get("rss", "default_html_mode", False) and (self._webview is not None or self.html_win):
            wx.CallAfter(self._alternar_modo)
        # Autoarranque del lector TTS si está activado en las opciones.
        if config.get("rss", "tts_autostart", False):
            wx.CallAfter(self._on_leer_tts, None)

    def _componer_texto(self, content, aviso: str = "") -> str:
        """Arma el texto del lector (cabecera + cuerpo) y prepara el HTML de la vista Ctrl+H.
        `aviso` es una línea de estado bajo la cabecera (p. ej. "Descargando...")."""
        texto_limpio = f"{self.title.upper()}\n"
        texto_limpio += f"Fuente: {self._feed_name} | Fecha: {self._fecha}\n"
        texto_limpio += f"URL Original: {self.link}\n"
        # Datos del episodio (Podcast 2.0): se guardaban pero no se mostraban en ningún sitio.
        try:
            episodio = rss_service.texto_detalles_episodio(self.article_db_id)
        except Exception:
            episodio = ""
        if episodio:
            texto_limpio += episodio + "\n"
        if self._resumen_ia:
            texto_limpio += f"\n--- RESUMEN CON IA ---\n{self._resumen_ia}\n"
        texto_limpio += "\n=========================================\n\n"
        if aviso:
            texto_limpio += f"[{aviso}]\n\n"
        # Estructura para navegar con letras (encabezados, tablas, listas...): el título es un
        # encabezado de nivel 1 y las marcas del cuerpo se corren tras la cabecera.
        from app.core import texto_estructurado as te
        marca_titulo = te.Marca(te.ENCABEZADO, 0, self.title or "", "", 1, len(self.title or ""))
        self._est_compuesta = te.Estructura(texto_limpio, [marca_titulo] if self.title else [])
        if content:
            # Limpiar contenido HTML a texto plano estructurado y legible
            from app.core.html_utils import sanear_html, quitar_imagenes_html
            cuerpo = te.html_a_estructura(content)
            self.content_txt = cuerpo.texto
            desplazado = cuerpo.desplazada(len(texto_limpio), texto_limpio)
            self._est_compuesta = te.Estructura(desplazado.texto, (
                [marca_titulo] if self.title else []) + desplazado.marcas)
            # Para la vista HTML (Ctrl+H): saneado SIEMPRE (el artículo es contenido
            # remoto no confiable; sin esto podría ejecutar scripts en el WebView).
            self._html_content = sanear_html(content)
            # Imágenes remotas (auditoría RSS-06): con "Mostrar imágenes remotas"
            # desactivado (o el feed marcado "sin imágenes"), las <img> se sustituyen
            # por su texto alternativo y el visor NO pide nada al servidor de origen
            # (privacidad: sin píxeles de seguimiento).
            from app.core.config_manager import config as _cfg
            if self._sin_imagenes or not _cfg.get("rss", "download_images", False):
                self._html_content = quitar_imagenes_html(self._html_content)
            texto_limpio += self.content_txt
        else:
            texto_limpio += _("No hay contenido disponible localmente. Puede intentar abrir el enlace original en el navegador.")
        return texto_limpio

    def _contenido_guardado(self) -> str:
        fila = db.query_one("SELECT content FROM rss_articles WHERE id = ?", (self.article_db_id,))
        return (fila[0] if fila else "") or ""

    def _on_articulo_completo(self, event):
        """Botón "Descargar el artículo completo": lo pide a la web aunque ya se intentara."""
        if not self.link:
            speak(_("Este artículo no tiene enlace a la web."), interrupt=True)
            return
        if self._descargando_completo:
            speak(_("Ya se está descargando el artículo completo."), interrupt=True)
            return
        speak(_("Descargando el artículo completo..."), interrupt=True)
        self._pedir_articulo_completo(manual=True)

    def _pedir_articulo_completo(self, manual: bool):
        """Descarga en segundo plano el artículo completo y, si es mejor que lo que hay, lo
        pone en el lector SIN cerrarlo: el cursor se queda donde estaba (si leías la cabecera,
        sigues ahí) y se avisa con una frase corta."""
        self._descargando_completo = True
        self.btn_completo.Disable()

        def _fin(html_nuevo, error):
            if not self:  # el diálogo se cerró mientras se descargaba
                return
            self._descargando_completo = False
            self.btn_completo.Enable()
            if html_nuevo:
                pos = self.txt_lector.GetInsertionPoint()
                self._poner_texto(self._componer_texto(html_nuevo), self._est_compuesta)
                self.txt_lector.SetInsertionPoint(min(pos, self.txt_lector.GetLastPosition()))
                if self.modo == "html":
                    self._render_html()
                speak(_("Artículo completo cargado."), interrupt=False)
                return
            # Sin mejora: se quita la línea de estado y se explica solo si lo pidió el usuario
            # (al abrir, el fallo es silencioso: el lector ya muestra lo que trae el feed).
            if not manual:
                pos = self.txt_lector.GetInsertionPoint()
                self._poner_texto(self._componer_texto(self._contenido_guardado()), self._est_compuesta)
                self.txt_lector.SetInsertionPoint(min(pos, self.txt_lector.GetLastPosition()))
            elif error:
                notificar(_("No se pudo descargar el artículo completo: {0}").format(error))
            else:
                from app.services.rss.rss_fulltext import FullTextExtractor
                diagnostico = FullTextExtractor.diagnostico_extraccion(self.link)
                mostrar_info(self, _(
                    "{0}\n\n"
                    "Si estás suscrito a ese medio, puedes añadirlo en Preferencias -> RSS -> "
                    "«Medios de pago en los que estás suscrito» para descargarlo con tu sesión "
                    "del navegador. También puedes abrir el enlace original en el navegador con Ctrl+Intro."
                ).format(diagnostico), _("Artículo completo"))

        rss_service.descargar_articulo_completo_async(self.article_db_id, _fin, forzar_sesion=manual)

    def _on_abrir_navegador(self, event):
        if self.link:
            from app.core.privacy import limpiar_url
            abrir_url(limpiar_url(self.link))
            speak(_("Abriendo artículo en el navegador web."), interrupt=True)

    def _on_copiar(self, event):
        txt = self.txt_lector.GetValue()
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(txt))
            wx.Clipboard.Close()
            notificar(_("Contenido copiado al portapapeles."))

    def _on_exportar(self, event):
        """Exporta el artículo a un archivo de texto, HTML o Markdown."""
        import os
        sugerido = (self.title or "articulo")[:60].strip() or "articulo"
        sugerido = "".join(c for c in sugerido if c not in '<>:"/\\|?*').rstrip(". ") or "articulo"
        dlg = wx.FileDialog(
            self, message=_("Exportar artículo como"), defaultFile=sugerido + ".txt",
            wildcard="Texto (*.txt)|*.txt|HTML (*.html)|*.html|Markdown (*.md)|*.md",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()
        ext = os.path.splitext(ruta)[1].lower().lstrip(".")
        formato = ext if ext in ("txt", "html", "md") else "txt"
        try:
            from app.services.rss.rss_service import rss_service
            rss_service.exportar_articulo(self.article_db_id, ruta, formato)
            notificar(_("Artículo exportado: {0}").format(ruta))
        except Exception as e:
            notificar(_("No se pudo exportar: {0}").format(e))

    def _on_leer_tts(self, event):
        texto = self.txt_lector.GetValue().strip()
        if not texto:
            speak(_("No hay texto que leer."), interrupt=True)
            return
        from app.core.config_manager import config
        # Recordar la posición de lectura por artículo: si hay una guardada y se quiere continuar,
        # se empieza desde ahí. La posición es el índice de carácter dentro del texto.
        inicio = 0
        if config.get("rss", "remember_tts_position", True):
            posiciones = config.get("rss", "tts_positions", {}) or {}
            guardada = int(posiciones.get(str(self.article_db_id), 0) or 0)
            if 0 < guardada < len(texto):
                resp = wx.MessageBox(
                    _("¿Continuar la lectura donde la dejaste?"), _("Lector en voz alta"),
                    wx.YES_NO | wx.ICON_QUESTION, self)
                if resp == wx.YES:
                    inicio = guardada
        from .tts_dialog import TTSReaderDialog
        dlg = TTSReaderDialog(self, _("Artículo"), texto, posicion_inicial=inicio)
        dlg.ShowModal()
        # Guardar la posición alcanzada para poder continuar después.
        if config.get("rss", "remember_tts_position", True):
            pos = getattr(dlg, "posicion_actual", 0) or 0
            posiciones = dict(config.get("rss", "tts_positions", {}) or {})
            if pos and pos < len(texto):
                posiciones[str(self.article_db_id)] = pos
            else:
                posiciones.pop(str(self.article_db_id), None)  # terminó: olvidar posición
            config.set("rss", "tts_positions", posiciones)
        dlg.Destroy()

    def _on_traducir(self, event):
        if not self.content_txt:
            return

        speak(_("Traduciendo artículo..."), interrupt=True)
        # Al idioma configurado para traducir (Preferencias), como en la lista: antes iba
        # siempre al español.
        from app.core.config_manager import config as _cfg
        destino = _cfg.get("translation", "default_target", "es") or "es"

        def _bg_trans(ctx):
            from app.services.translation.translation_service import translation_service
            return translation_service.traducir(self.content_txt, idioma_destino=destino)

        def _finish(res, err):
            if err:
                notificar(f"Error al traducir: {err}")
            else:
                notificar(_("Traducción completada."))
                # Reemplazar parte del texto del lector
                cabecera = f"{self.title.upper()} " + _("(TRADUCIDO, idioma: {0})").format(destino) + "\n"
                cabecera += f"URL Original: {self.link}\n"
                cabecera += "\n=========================================\n\n"
                self._poner_texto(cabecera + res)   # traducido: sin marcas de estructura
                
        task_manager.start_task(_bg_trans, callback=_finish, pass_context=True)
