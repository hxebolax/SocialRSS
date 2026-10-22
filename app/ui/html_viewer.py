# -*- coding: utf-8 -*-
"""Visor genérico de resultados: si el texto es MARKDOWN lo convierte a HTML y lo muestra en un
diálogo con vista web ACCESIBLE (navegable con el lector, como una página); si es texto plano,
usa el diálogo de solo lectura de siempre.

Pensado para respuestas de IA que a veces llegan en markdown (títulos, listas, negritas, enlaces)
y a veces en texto plano. Se usa desde "Ponte al día" y sirve para cualquier otro resultado de IA.
"""
import re
import logging

import wx
import wx.html      # HtmlWindow (respaldo final)
import wx.html2     # WebView de Edge (respaldo intermedio)

from app.core.speech import speak

log = logging.getLogger(__name__)

try:
    _
except NameError:
    _ = lambda x: x


# Marcadores que delatan markdown (con al menos uno se trata como markdown).
_RE_MARKDOWN = [
    re.compile(r"^\s{0,3}#{1,6}\s+\S", re.MULTILINE),        # encabezados  # Título
    re.compile(r"^\s{0,3}[-*+]\s+\S", re.MULTILINE),         # lista con viñetas
    re.compile(r"^\s{0,3}\d+\.\s+\S", re.MULTILINE),         # lista numerada
    re.compile(r"\*\*[^\s*][^*]*\*\*"),                      # **negrita**
    re.compile(r"__[^\s_][^_]*__"),                          # __negrita__
    re.compile(r"\[[^\]]+\]\([^)]+\)"),                      # [enlace](url)
    re.compile(r"^\s{0,3}>\s+\S", re.MULTILINE),             # > cita
    re.compile(r"```"),                                       # bloque de código
    re.compile(r"^\s{0,3}(---|\*\*\*|___)\s*$", re.MULTILINE),  # regla horizontal
]


def parece_markdown(texto: str) -> bool:
    """True si el texto contiene marcas de markdown (títulos, listas, negritas, enlaces...)."""
    if not texto:
        return False
    for pat in _RE_MARKDOWN:
        if pat.search(texto):
            return True
    return False


def markdown_a_html(texto: str) -> str:
    """Convierte markdown a HTML (cuerpo). Si falta la librería 'markdown', hace un respaldo mínimo
    para que no se pierda contenido (párrafos y saltos de línea)."""
    try:
        import markdown as _md
        return _md.markdown(texto or "", extensions=["extra", "sane_lists", "nl2br"])
    except Exception as e:
        log.debug(f"No se pudo convertir markdown con la librería: {e}")
        import html as _html
        parrafos = [f"<p>{_html.escape(p).replace(chr(10), '<br>')}</p>"
                    for p in (texto or "").split("\n\n") if p.strip()]
        return "\n".join(parrafos) or "<p></p>"


def mostrar_resultado(frame, titulo: str, texto: str, mostrar_texto_largo=None):
    """Muestra un resultado (p. ej. de IA): en markdown -> visor HTML accesible; en texto plano ->
    el diálogo de solo lectura de siempre. 'frame' es la ventana principal; 'mostrar_texto_largo'
    es su método _mostrar_texto_largo (para el caso de texto plano)."""
    if parece_markdown(texto):
        try:
            dlg = HtmlViewerDialog(frame, titulo, markdown_a_html(texto), texto_plano=texto)
            dlg.ShowModal()
            dlg.Destroy()
            return
        except Exception as e:
            log.debug(f"No se pudo abrir el visor HTML; se usa texto plano: {e}")
    # Texto plano (o si el visor HTML falló): diálogo de solo lectura de siempre.
    if mostrar_texto_largo is not None:
        mostrar_texto_largo(titulo, texto)
    else:
        from app.ui.accessible_controls import mostrar_info
        mostrar_info(frame, texto, titulo)


class HtmlViewerDialog(wx.Dialog):
    """Diálogo que muestra HTML en una vista ACCESIBLE y navegable (AccessibleWebView; si no está,
    wx.html2.WebView; y por último wx.html.HtmlWindow). Se cierra con Escape."""

    def __init__(self, parent, titulo: str, html_cuerpo: str, texto_plano: str = ""):
        super().__init__(parent, title=titulo, size=(760, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self._titulo = titulo
        # El HTML puede proceder de contenido remoto (o de una IA que lo resume):
        # se sanea SIEMPRE con la lista blanca antes de pintarlo en el WebView.
        try:
            from app.core.html_utils import sanear_html
            self._html = sanear_html(html_cuerpo) or "<p></p>"
        except Exception:
            self._html = "<p></p>"
        self._texto_plano = texto_plano or ""
        self._awv = None
        self._webview = None
        self._html_win = None
        self._init_ui()
        self.CenterOnParent()
        # Pintar el HTML DESPUÉS de que el diálogo se muestre: el WebView accesible necesita estar
        # realizado para renderizar (si se pinta en el constructor, puede quedar en blanco).
        self._pintado = False
        self.Bind(wx.EVT_SHOW, self._on_show)
        wx.CallAfter(self._pintar_una_vez)

    def _on_show(self, event):
        event.Skip()
        if event.IsShown():
            wx.CallAfter(self._pintar_una_vez)

    def _pintar_una_vez(self):
        if not self or self._pintado:
            return
        self._pintado = True
        self._pintar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        # Vista HTML accesible (preferente). Si no hay librería, cae a WebView y luego a HtmlWindow.
        try:
            from wx_accessible_webview import AccessibleWebView
            self._awv = AccessibleWebView(self, title=self._titulo, lang="es",
                                          live_region=False, open_links_externally=True)
            s.Add(self._awv.control, 1, wx.EXPAND | wx.ALL, 8)
        except Exception as e:
            log.debug(f"wx-accessible-webview no disponible en el visor: {e}")
            try:
                if wx.html2.WebView.IsBackendAvailable(wx.html2.WebViewBackendEdge):
                    self._webview = wx.html2.WebView.New(self, backend=wx.html2.WebViewBackendEdge, name=self._titulo)
                else:
                    self._webview = wx.html2.WebView.New(self, name=self._titulo)
                self._webview.Bind(wx.html2.EVT_WEBVIEW_NAVIGATING, self._on_navegando)
                s.Add(self._webview, 1, wx.EXPAND | wx.ALL, 8)
            except Exception as e2:
                log.debug(f"No hay WebView; se usa HtmlWindow: {e2}")
                self._html_win = wx.html.HtmlWindow(self, name=self._titulo)
                s.Add(self._html_win, 1, wx.EXPAND | wx.ALL, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_copiar = wx.Button(self, label=_("&Copiar texto"))
        b_copiar.Bind(wx.EVT_BUTTON, self._on_copiar)
        btns.Add(b_copiar, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        # Escape cierra el diálogo (también desde el WebView, que se traga las teclas): filtro global.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL), id=wx.ID_CANCEL)

    def _pintar(self):
        import html as _html
        cuerpo = f'<h1>{_html.escape(self._titulo)}</h1>{self._html}'
        if self._awv is not None:
            try:
                self._awv.set_content(cuerpo)
                # Llevar el foco a la vista para poder navegar el HTML de inmediato.
                try:
                    self._awv.control.SetFocus()
                except Exception:
                    pass
                return
            except Exception as e:
                log.debug(f"No se pudo pintar en AccessibleWebView: {e}")
        # Documento autónomo para WebView / HtmlWindow.
        try:
            from app.core.html_utils import html_a_documento
            doc = html_a_documento(self._titulo, self._html)
        except Exception:
            doc = f"<!DOCTYPE html><html><body><h1>{self._titulo}</h1>{self._html}</body></html>"
        if self._webview is not None:
            try:
                self._webview.SetPage(doc, "")
                self._webview.SetFocus()
                return
            except Exception as e:
                log.debug(f"No se pudo pintar en WebView: {e}")
        if self._html_win is not None:
            try:
                self._html_win.SetPage(f"<body><h2>{self._titulo}</h2>{self._html}</body>")
                self._html_win.SetFocus()
            except Exception:
                self._html_win.SetPage("<p>No se pudo mostrar el contenido.</p>")

    def _on_navegando(self, event):
        """Los enlaces se abren en el navegador del sistema; la carga inicial sí se permite."""
        try:
            url = event.GetURL()
            if url and not url.startswith("about:") and url not in ("", "text/html"):
                event.Veto()
                from app.core.privacy import abrir_url
                abrir_url(url)
        except Exception:
            pass

    def _on_copiar(self, event):
        from app.core.speech import copiar_al_portapapeles
        copiar_al_portapapeles(self._texto_plano or "", _("Texto copiado al portapapeles."))

    def _on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.EndModal(wx.ID_CANCEL)
            return
        event.Skip()
