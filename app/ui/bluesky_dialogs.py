# -*- coding: utf-8 -*-
"""Diálogos del servicio Bluesky (login, compositor, hilos, perfil, etc.).
"""
import logging
import os

import wx

from app.core.at_lectura import texto_contexto_respuesta
from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.ui.accessible_controls import mostrar_info, confirmar, habilitar_escape_cierre
from app.ui.mastodon_compose_helpers import contar_grafemas

from app.services.bluesky import bluesky_service

log = logging.getLogger(__name__)

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s  # salvaguarda gettext para ETIQUETAS_CW en import-time

# Valores de self-label (advertencia de contenido) de Bluesky, con su etiqueta traducida.
ETIQUETAS_CW = [
    ("", _("Sin advertencia")),
    ("porn", _("Pornografía")),
    ("sexual", _("Contenido sexual")),
    ("nudity", _("Desnudez")),
    ("graphic-media", _("Contenido gráfico/violento")),
]


def _fila_botones(panel, solo_cerrar=False):
    """Crea una fila de botones estándar PARENTADOS AL PANEL (no al diálogo).

    Esto evita el wxAssertionError de SetContainingWindow: los botones de un
    sizer deben tener como padre el mismo panel que contiene el sizer. Devuelve
    (sizer, boton_principal). Etiquetas en español; el botón principal queda
    como predeterminado (responde a Intro).
    """
    fila = wx.BoxSizer(wx.HORIZONTAL)
    fila.AddStretchSpacer()
    if solo_cerrar:
        btn = wx.Button(panel, wx.ID_CLOSE, _("&Cerrar"))
        fila.Add(btn, 0, wx.ALL, 5)
        btn.SetDefault()
        return fila, btn
    btn_ok = wx.Button(panel, wx.ID_OK, _("&Aceptar"))
    btn_cancel = wx.Button(panel, wx.ID_CANCEL, _("&Cancelar"))
    fila.Add(btn_ok, 0, wx.ALL, 5)
    fila.Add(btn_cancel, 0, wx.ALL, 5)
    btn_ok.SetDefault()
    return fila, btn_ok


# ======================================================================
# DIÁLOGO DE ALTA DE CUENTA (LOGIN)
# ======================================================================
class BlueskyLoginDialog(wx.Dialog):
    """Pide handle + contraseña de aplicación + servidor (PDS)."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Añadir cuenta de Bluesky"))
        self.handle = ""
        self.app_password = ""
        self.pds_url = config.get("bluesky", "pds_url", "https://bsky.social")

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_(
            "Handle (por ejemplo, nombre.bsky.social):")), 0, wx.ALL, 5)
        self.txt_handle = wx.TextCtrl(panel)
        s.Add(self.txt_handle, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_(
            "Contraseña de aplicación (NO tu contraseña normal):")), 0, wx.ALL, 5)
        self.txt_pass = wx.TextCtrl(panel, style=wx.TE_PASSWORD)
        s.Add(self.txt_pass, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_(
            "Crea una contraseña de aplicación en bsky.app → Configuración → "
            "Privacidad y seguridad → Contraseñas de aplicación. Es revocable.")), 0, wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("Servidor / PDS (avanzado):")), 0, wx.ALL, 5)
        self.txt_pds = wx.TextCtrl(panel, value=self.pds_url)
        s.Add(self.txt_pds, 0, wx.EXPAND | wx.ALL, 5)

        botones, _btn = _fila_botones(panel)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(sz)
        self.SetSize((520, -1))

        self.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt_handle.SetFocus()

    def _on_ok(self, event):
        self.handle = self.txt_handle.GetValue().strip()
        self.app_password = self.txt_pass.GetValue().strip()
        self.pds_url = self.txt_pds.GetValue().strip() or "https://bsky.social"
        if not self.handle or not self.app_password:
            mostrar_info(self, _("Debes indicar el handle y la contraseña de aplicación."),
                         _("Datos incompletos"))
            return
        self.EndModal(wx.ID_OK)


# ======================================================================
# COMPOSITOR DE POSTS
# ======================================================================
class BlueskyComposeDialog(wx.Dialog):
    """
    Compositor de un post de Bluesky.

    Sin selector de visibilidad (todo es público). La 'advertencia de contenido'
    se modela como self-label. Límite de 300 caracteres.
    """

    LIMITE = 300

    def __init__(self, parent, account_id, reply_to_uri=None, quote_uri=None,
                 titulo=None, texto_inicial=""):
        super().__init__(parent, title=titulo or _("Nuevo post de Bluesky"))
        self.account_id = account_id
        self.reply_to_uri = reply_to_uri
        self.quote_uri = quote_uri
        self.adjuntos = []   # lista de [ruta, alt]

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        # RESPUESTA: a quién respondes y a quién avisará Bluesky (como el "respondiendo a" de la
        # app oficial). Campo de solo lectura justo antes del texto: con Mayús+Tab se lee.
        self.txt_respondiendo = None
        if reply_to_uri:
            s.Add(wx.StaticText(panel, label=_("Respondiendo a:")), 0, wx.ALL, 5)
            self.txt_respondiendo = wx.TextCtrl(
                panel, value=self._texto_respondiendo_local(reply_to_uri),
                style=wx.TE_MULTILINE | wx.TE_READONLY, size=(480, 70),
                name=_("Respondiendo a"))
            s.Add(self.txt_respondiendo, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("Texto del post:")), 0, wx.ALL, 5)
        self.txt = wx.TextCtrl(panel, value=texto_inicial,
                               style=wx.TE_MULTILINE | wx.TE_RICH2, size=(480, 140))
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        self.lbl_contador = wx.StaticText(panel, label="")
        s.Add(self.lbl_contador, 0, wx.ALL, 5)
        self.txt.Bind(wx.EVT_TEXT, self._actualizar_contador)
        # Ctrl+Mayús+Espacio: consultar/anunciar los caracteres restantes (útil si el contador en vivo
        # está desactivado en Preferencias → Accesibilidad).
        self.txt.Bind(wx.EVT_KEY_DOWN, self._on_txt_key_contador)

        # Etiqueta de contenido (self-label)
        s.Add(wx.StaticText(panel, label=_("Advertencia de contenido:")), 0, wx.ALL, 5)
        self.cmb_cw = wx.Choice(panel, choices=[e[1] for e in ETIQUETAS_CW])
        self.cmb_cw.SetSelection(0)
        s.Add(self.cmb_cw, 0, wx.ALL, 5)

        # Idioma
        s.Add(wx.StaticText(panel, label=_("Idioma (código, p. ej. es, en):")), 0, wx.ALL, 5)
        self.txt_lang = wx.TextCtrl(panel, value=config.get("translation", "default_target", "es"))
        s.Add(self.txt_lang, 0, wx.ALL, 5)

        # Quién puede responder (threadgate) — solo aplica a posts raíz
        self._reply_vals = ["everybody", "following", "mentioned", "nobody"]
        s.Add(wx.StaticText(panel, label=_("Quién puede responder:")), 0, wx.ALL, 5)
        self.cmb_reply = wx.Choice(panel, choices=[
            _("Todos"), _("A quienes sigo"), _("Solo mencionados"), _("Nadie")])
        pred = config.get("bluesky", "default_reply_control", "everybody")
        self.cmb_reply.SetSelection(self._reply_vals.index(pred) if pred in self._reply_vals else 0)
        if reply_to_uri:
            self.cmb_reply.Disable()   # en respuestas no se puede fijar threadgate
        s.Add(self.cmb_reply, 0, wx.ALL, 5)

        # Adjuntos
        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_img = wx.Button(panel, label=_("Adjuntar &imágenes..."))
        btn_img.Bind(wx.EVT_BUTTON, self._on_adjuntar)
        fila.Add(btn_img, 0, wx.ALL, 5)
        btn_alt = wx.Button(panel, label=_("&Texto alternativo..."))
        btn_alt.Bind(wx.EVT_BUTTON, self._on_alt)
        fila.Add(btn_alt, 0, wx.ALL, 5)
        btn_quitar = wx.Button(panel, label=_("&Quitar adjunto"))
        btn_quitar.Bind(wx.EVT_BUTTON, self._on_quitar_adjunto)
        fila.Add(btn_quitar, 0, wx.ALL, 5)
        btn_video = wx.Button(panel, label=_("Adjuntar &vídeo..."))
        btn_video.Bind(wx.EVT_BUTTON, self._on_adjuntar_video)
        fila.Add(btn_video, 0, wx.ALL, 5)
        btn_mencion = wx.Button(panel, label=_("&Mencionar a alguien..."))
        btn_mencion.Bind(wx.EVT_BUTTON, self._on_mencionar)
        fila.Add(btn_mencion, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.ALL, 0)

        self.lst_adjuntos = wx.ListBox(panel, size=(480, 80))
        s.Add(self.lst_adjuntos, 0, wx.EXPAND | wx.ALL, 5)
        self.video_path = None
        self.video_alt = ""
        self.lbl_video = wx.StaticText(panel, label="")
        s.Add(self.lbl_video, 0, wx.ALL, 5)

        btn_borrador = wx.Button(panel, label=_("Guardar como &borrador"))
        btn_borrador.Bind(wx.EVT_BUTTON, self._on_guardar_borrador)
        s.Add(btn_borrador, 0, wx.ALL, 5)

        botones, _btn = _fila_botones(panel)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizerAndFit(sz)

        self.Bind(wx.EVT_BUTTON, self._on_enviar, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        # Título base para mostrar los caracteres restantes en la barra de título.
        self._titulo_base = self.GetTitle()
        self._actualizar_contador()
        # Cursor al final del texto inicial y foco en el cuadro de escritura.
        self.txt.SetInsertionPointEnd()
        self.txt.SetFocus()
        if reply_to_uri:
            self._pedir_contexto_respuesta(reply_to_uri)

    # ------------------------------------------------------------------ respuesta
    @staticmethod
    def _texto_respondiendo_local(uri):
        """Lo que se sabe sin red de la publicación a la que respondes (autor y texto)."""
        fila = db.query_one("SELECT author_name, author_handle, content FROM bluesky_posts "
                            "WHERE uri = ? LIMIT 1", (uri,))
        if not fila:
            return _("Cargando la publicación a la que respondes…")
        nombre, handle, texto = fila
        return _("{0} (@{1}): {2}").format(nombre or handle, handle,
                                          (texto or "").replace("\n", " ").strip())

    def _pedir_contexto_respuesta(self, uri):
        """Completa "Respondiendo a" con quién recibirá el aviso (consulta al servidor)."""
        from app.services.bluesky import bluesky_service

        def _fin(res, err):
            if not self or not self.txt_respondiendo or err or not res:
                return
            self.txt_respondiendo.SetValue(texto_contexto_respuesta(res))
        try:
            bluesky_service.contexto_respuesta_async(self.account_id, uri, callback=_fin)
        except Exception as e:
            log.debug(f"No se pudo pedir el contexto de la respuesta: {e}")

    def _on_txt_key_contador(self, event):
        """Consultar caracteres restantes (tecla del catálogo "consultar_caracteres", por defecto
        Ctrl+Mayús+Espacio); modo a petición."""
        from .shortcut_manager import coincide as _atajo_coincide
        if _atajo_coincide("consultar_caracteres", event):
            n = contar_grafemas(self.txt.GetValue())
            self.lbl_contador.SetLabel(_("{0} de {1} caracteres").format(n, self.LIMITE))
            speak(_("{0} caracteres restantes.").format(self.LIMITE - n), interrupt=True)
            return
        event.Skip()

    def _actualizar_contador(self, event=None):
        # Si el contador EN VIVO está desactivado (Preferencias → Accesibilidad), al escribir no se
        # recalcula nada (evita tirones); se consulta con Ctrl+Mayús+Espacio. Las llamadas
        # programáticas (event=None) sí actualizan.
        if event is not None and not config.get("accessibility", "contador_en_vivo", True):
            return
        # Bluesky cuenta por GRAFEMAS (igual que en atproto: el límite de 300 es de grapheme
        # clusters), no por caracteres sueltos; así un emoji compuesto cuenta 1.
        n = contar_grafemas(self.txt.GetValue())
        self.lbl_contador.SetLabel(_("{0} de {1} caracteres").format(n, self.LIMITE))
        restantes = self.LIMITE - n
        # El TÍTULO solo lleva el contador con el modo EN VIVO activado (y con retardo, para no saturar
        # a NVDA/JAWS). En modo «a petición» el título NO lo muestra nunca: se anuncia por voz con la
        # combinación (Ctrl+Mayús+Espacio).
        if not config.get("accessibility", "contador_en_vivo", True):
            return
        try:
            t = getattr(self, "_titulo_timer", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass
        self._titulo_timer = wx.CallLater(
            500, lambda: self.SetTitle(_("{0} ({1} caracteres restantes)").format(self._titulo_base, restantes)))

    def _on_guardar_borrador(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            mostrar_info(self, _("El borrador está vacío."), _("Borradores"))
            return
        drafts = list(config.get("bluesky", "drafts", []))
        drafts.append({"text": texto})
        config.set("bluesky", "drafts", drafts)
        notificar(_("Borrador guardado."))
        self.EndModal(wx.ID_CANCEL)

    def _refrescar_lista_adjuntos(self):
        self.lst_adjuntos.Clear()
        for ruta, alt in self.adjuntos:
            nombre = os.path.basename(ruta)
            etiqueta = nombre + (" — " + alt if alt else " — " + _("(sin descripción)"))
            self.lst_adjuntos.Append(etiqueta)

    def _on_adjuntar(self, event):
        # Bluesky no admite imágenes y vídeo en el mismo post.
        if self.video_path:
            mostrar_info(self, _("No se pueden combinar imágenes y vídeo en el mismo post. "
                                 "Quita el vídeo si quieres adjuntar imágenes."), _("Adjuntos"))
            return
        if len(self.adjuntos) >= 4:
            mostrar_info(self, _("Máximo 4 imágenes por post."), _("Límite"))
            return
        dlg = wx.FileDialog(self, _("Selecciona imágenes"), wildcard=_(
            "Imágenes (*.jpg;*.jpeg;*.png;*.gif;*.webp)|*.jpg;*.jpeg;*.png;*.gif;*.webp"),
            style=wx.FD_OPEN | wx.FD_MULTIPLE)
        if dlg.ShowModal() == wx.ID_OK:
            for ruta in dlg.GetPaths():
                if len(self.adjuntos) < 4:
                    self.adjuntos.append([ruta, ""])
            self._refrescar_lista_adjuntos()
        dlg.Destroy()

    def _on_alt(self, event):
        idx = self.lst_adjuntos.GetSelection()
        if idx < 0 or idx >= len(self.adjuntos):
            mostrar_info(self, _("Selecciona primero un adjunto."), _("Sin selección"))
            return
        from app.ui.accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Describe la imagen para personas ciegas:"),
                                    _("Texto alternativo"), value=self.adjuntos[idx][1],
                                    multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            self.adjuntos[idx][1] = dlg.GetValue().strip()
            self._refrescar_lista_adjuntos()
        dlg.Destroy()

    def _on_quitar_adjunto(self, event):
        idx = self.lst_adjuntos.GetSelection()
        if 0 <= idx < len(self.adjuntos):
            del self.adjuntos[idx]
            self._refrescar_lista_adjuntos()

    # Bluesky: 1 vídeo por post, no combinable con imágenes, máximo ~50 MB.
    _MAX_VIDEO_BYTES = 50 * 1024 * 1024

    def _on_adjuntar_video(self, event):
        if self.adjuntos:
            mostrar_info(self, _("No se pueden combinar imágenes y vídeo en el mismo post. "
                                 "Quita las imágenes si quieres adjuntar un vídeo."), _("Adjuntos"))
            return
        dlg = wx.FileDialog(self, _("Selecciona un vídeo"), wildcard=_(
            "Vídeos (*.mp4;*.mov;*.webm;*.mpeg;*.mpg)|*.mp4;*.mov;*.webm;*.mpeg;*.mpg"),
            style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            try:
                tam = os.path.getsize(ruta)
            except OSError:
                tam = 0
            if tam > self._MAX_VIDEO_BYTES:
                mostrar_info(self, _("El vídeo ocupa {0} MB y Bluesky admite como máximo 50 MB. "
                                     "Usa un vídeo más ligero o más corto.").format(round(tam / (1024 * 1024), 1)),
                             _("Vídeo demasiado grande"))
                dlg.Destroy()
                return
            self.video_path = ruta
            self.lbl_video.SetLabel(_("Vídeo: {0}").format(os.path.basename(self.video_path)))
            # Texto alternativo PROPIO del vídeo (antes se tomaba del primer adjunto de
            # imagen, que con vídeo no existe, así que siempre iba vacío; BSKY-002).
            from app.ui.accessible_controls import CustomTextEntryDialog
            d_alt = CustomTextEntryDialog(
                self, _("Describe el vídeo para personas ciegas (opcional):"),
                _("Texto alternativo del vídeo"), value=getattr(self, "video_alt", ""),
                multiline=True)
            if d_alt.ShowModal() == wx.ID_OK:
                self.video_alt = d_alt.GetValue().strip()
            d_alt.Destroy()
        dlg.Destroy()

    def _on_mencionar(self, event):
        """Busca una cuenta (autocompletado de atproto) y la inserta como @mención en el texto."""
        query = wx.GetTextFromUser(
            _("Escribe parte del nombre o del handle de la cuenta a mencionar:"),
            _("Mencionar a alguien"), parent=self)
        if not query.strip():
            return

        def _cb(res, err):
            if err or not res:
                speak(_("No se encontraron cuentas."), interrupt=True)
                return
            opciones = ["{0} (@{1})".format(a.get("display_name") or a.get("handle"), a.get("handle"))
                        for a in res]
            sel = wx.GetSingleChoice(_("Elige a quién mencionar:"), _("Mencionar"), opciones, self)
            if not sel:
                return
            handle = res[opciones.index(sel)].get("handle", "")
            if handle:
                self.txt.WriteText("@{0} ".format(handle))
                self.txt.SetFocus()
        bluesky_service.buscar_actores_typeahead_async(self.account_id, query.strip(), callback=_cb)

    def _on_enviar(self, event):
        texto = self.txt.GetValue().strip()
        # Un post SOLO con vídeo es válido (antes se rechazaba como vacío; auditoría BSKY-002).
        if not texto and not self.adjuntos and not self.video_path:
            mostrar_info(self, _("El post está vacío."), _("Nada que publicar"))
            return
        if contar_grafemas(texto) > self.LIMITE:
            mostrar_info(self, _("El post supera los 300 caracteres."), _("Demasiado largo"))
            return

        # Recordatorio de texto alternativo (accesibilidad)
        if self.adjuntos and config.get("bluesky", "alt_text_reminder", True):
            sin_alt = [a for a in self.adjuntos if not a[1].strip()]
            if sin_alt and not confirmar(
                    self, _("Hay imágenes sin texto alternativo. ¿Publicar de todos modos?"),
                    _("Accesibilidad")):
                return

        etiqueta = ETIQUETAS_CW[self.cmb_cw.GetSelection()][0]
        labels = [etiqueta] if etiqueta else None
        lang = self.txt_lang.GetValue().strip()
        langs = [lang] if lang else None
        media_paths = [a[0] for a in self.adjuntos] or None
        alt_texts = [a[1] for a in self.adjuntos] or None
        reply_control = self._reply_vals[self.cmb_reply.GetSelection()]

        def _cb(ok, info):
            if ok:
                notificar(_("Post publicado."))
            else:
                notificar(_("Error al publicar: {0}").format(info))

        # Vídeo: va por la MISMA vía completa que el resto de publicaciones (BSKY-002).
        # Antes usaba una ruta aparte que PERDÍA el aviso de contenido, el control de
        # respuestas y la cita (se publicaba sin ellos, en silencio), y el alt siempre
        # quedaba vacío. recordWithMedia admite vídeo, así que la cita también funciona.
        if self.video_path:
            if not getattr(self, "video_alt", "") and config.get("bluesky", "alt_text_reminder", True):
                if not confirmar(
                        self, _("El vídeo no tiene texto alternativo. ¿Publicar de todos modos?"),
                        _("Accesibilidad")):
                    return
            bluesky_service.publicar_post_async(
                self.account_id, texto, reply_to_uri=self.reply_to_uri,
                quote_uri=self.quote_uri, media_paths=[self.video_path],
                alt_texts=[getattr(self, "video_alt", "")], es_video=True,
                langs=langs, labels=labels, reply_control=reply_control, callback=_cb)
            speak(_("Publicando vídeo..."), interrupt=True)
            self.EndModal(wx.ID_OK)
            return

        bluesky_service.publicar_post_async(
            self.account_id, texto, reply_to_uri=self.reply_to_uri,
            quote_uri=self.quote_uri, media_paths=media_paths, alt_texts=alt_texts,
            langs=langs, labels=labels, reply_control=reply_control, callback=_cb)
        speak(_("Publicando..."), interrupt=True)
        self.EndModal(wx.ID_OK)


# ======================================================================
# VISOR DE HILO
# ======================================================================
class BlueskyThreadDialog(wx.Dialog):
    """Muestra el hilo (conversación) de un post como texto navegable."""

    def __init__(self, parent, account_id, uri):
        super().__init__(parent, title=_("Conversación de Bluesky"),
                         size=(640, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.uri = uri

        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.txt = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)
        botones, _btn = _fila_botones(panel, solo_cerrar=True)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)

        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)

        self.txt.SetValue(_("Cargando conversación..."))
        bluesky_service.obtener_hilo_async(account_id, uri, callback=self._on_hilo)

    def _on_hilo(self, posts, error):
        if error:
            self.txt.SetValue(_("Error al cargar la conversación: {0}").format(error))
            return
        plantilla = config.get("accessibility", "bluesky_conversation",
                               "Mensaje {num} de {author}: {content}")
        lineas = []
        for i, p in enumerate(posts or [], 1):
            try:
                lineas.append(plantilla.format(num=i, author=p.author_name, content=p.content))
            except Exception:
                lineas.append(f"{i}. {p.author_name}: {p.content}")
        self.txt.SetValue("\n\n".join(lineas) if lineas else _("No hay mensajes."))
        self.txt.SetInsertionPoint(0)


# ======================================================================
# CONVERSACIÓN DE MENSAJES DIRECTOS (DM)
# ======================================================================
class BlueskyConversationDialog(wx.Dialog):
    """Ventana de conversación de un DM (tipo Telegram): lista de mensajes + envío."""

    def __init__(self, parent, account_id, convo_id, titulo):
        super().__init__(parent, title=_("Conversación con {0}").format(titulo),
                         size=(720, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.convo_id = convo_id
        self.titulo = titulo
        self.msg_ids = []   # message_id por índice de fila

        from .accessible_controls import AccessibleListCtrl
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.lista = AccessibleListCtrl(
            panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Mensajes de la conversación"))
        self.lista.InsertColumn(0, _("Remitente"), width=150)
        self.lista.InsertColumn(1, _("Mensaje"), width=400)
        self.lista.InsertColumn(2, _("Fecha"), width=130)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        self.lista.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_menu_mensaje)

        # Caja de envío
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(panel, label=_("Mensaje:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_msg = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.txt_msg.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_msg)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_msg)
        fila.Add(self.txt_msg, 1, wx.EXPAND | wx.ALL, 5)
        btn_env = wx.Button(panel, label=_("&Enviar"))
        btn_env.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(btn_env, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        # Botones inferiores
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        btn_act = wx.Button(panel, label=_("&Actualizar"))
        btn_act.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila2.Add(btn_act, 0, wx.ALL, 5)
        btn_fin = wx.Button(panel, label=_("Ir al &final"))
        btn_fin.Bind(wx.EVT_BUTTON, lambda e: self._ir_al_final())
        fila2.Add(btn_fin, 0, wx.ALL, 5)
        fila2.AddStretchSpacer()
        btn_cerrar = wx.Button(panel, wx.ID_CLOSE, _("&Cerrar"))
        btn_cerrar.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        fila2.Add(btn_cerrar, 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.EXPAND)

        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)

        self._cargar()
        # Marcar como leída al abrir
        bluesky_service.marcar_convo_leido_async(account_id, convo_id)

    def _cargar(self):
        bluesky_service.cargar_mensajes_async(self.account_id, self.convo_id, callback=self._on_mensajes)

    def _on_mensajes(self, mensajes, error):
        if error:
            notificar(_("Error al cargar mensajes: {0}").format(error))
            return
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self.msg_ids = []
        from app.ui.main_frame import formatear_tiempo_relativo
        # Mostrar en orden cronológico (la API devuelve del más nuevo al más viejo).
        mensajes = list(reversed(mensajes or []))
        plantilla = config.get("accessibility", "bluesky_conversation",
                               "Mensaje {num} de {author}: {content}")
        for i, m in enumerate(mensajes):
            quien = _("Yo") if m["outgoing"] else m["sender"]
            fecha_rel = formatear_tiempo_relativo(m["sent_at"]) if m["sent_at"] else ""
            pos = self.lista.InsertItem(i, quien)
            self.lista.SetItem(pos, 1, (m["text"] or "").replace("\n", " "))
            self.lista.SetItem(pos, 2, fecha_rel)
            self.msg_ids.append(m["id"])
            try:
                lectura = plantilla.format(num=i + 1, author=quien, content=m["text"] or "")
            except Exception:
                lectura = f"{quien}: {m['text']}"
            self.lista.establecer_lectura(pos, lectura)
        self._ir_al_final()

    def _ir_al_final(self):
        n = self.lista.GetItemCount()
        if n > 0:
            self.lista.Focus(n - 1)
            self.lista.Select(n - 1)
            self.lista.EnsureVisible(n - 1)

    def _on_enviar(self, event):
        texto = self.txt_msg.GetValue().strip()
        if not texto:
            return

        def _cb(res, error):
            if error:
                notificar(_("Error al enviar: {0}").format(error))
            else:
                self.txt_msg.SetValue("")
                self._cargar()
        bluesky_service.enviar_mensaje_async(self.account_id, self.convo_id, texto, callback=_cb)
        self.txt_msg.SetFocus()

    def _on_menu_mensaje(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.msg_ids):
            return
        mid = self.msg_ids[idx]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Reaccionar..."))
        self.Bind(wx.EVT_MENU, lambda e, m=mid: self._reaccionar(m), it)
        it = menu.Append(wx.ID_ANY, _("Borrar para mí"))
        self.Bind(wx.EVT_MENU, lambda e, m=mid: self._borrar(m), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _reaccionar(self, mid):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Emoji de reacción (por ejemplo 👍):"),
                                    _("Reaccionar"), value="👍")
        if dlg.ShowModal() == wx.ID_OK:
            emoji = dlg.GetValue().strip()
            if emoji:
                bluesky_service.reaccionar_mensaje_async(
                    self.account_id, self.convo_id, mid, emoji,
                    callback=lambda r, e: self._cargar() if not e else None)
        dlg.Destroy()

    def _borrar(self, mid):
        if not confirmar(self, _("¿Borrar este mensaje para ti?"), _("Confirmar")):
            return
        bluesky_service.borrar_mensaje_async(
            self.account_id, self.convo_id, mid,
            callback=lambda r, e: self._cargar() if not e else None)


# ======================================================================
# PALABRAS SILENCIADAS (muted words): el diálogo completo vive en bluesky_dialogs_moderacion
# (dónde, excepto cuentas que sigo, duración, editar conservando el id). Se reexporta aquí
# por compatibilidad con quien lo importaba de este módulo.
# ======================================================================
from .bluesky_dialogs_moderacion import BlueskyMutedWordsDialog  # noqa: E402,F401


# ======================================================================
# EDITAR PERFIL
# ======================================================================
class BlueskyProfileDialog(wx.Dialog):
    """Edita nombre, biografía, avatar/banner y permite cambiar el handle."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Mi perfil de Bluesky"), size=(520, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.avatar_path = None
        self.banner_path = None
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.lbl_handle = wx.StaticText(panel, label=_("Handle: (cargando...)"))
        s.Add(self.lbl_handle, 0, wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("Nombre visible:")), 0, wx.ALL, 5)
        self.txt_name = wx.TextCtrl(panel)
        s.Add(self.txt_name, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("Biografía:")), 0, wx.ALL, 5)
        self.txt_bio = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(480, 100))
        s.Add(self.txt_bio, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_av = wx.Button(panel, label=_("Cambiar &avatar..."))
        btn_av.Bind(wx.EVT_BUTTON, lambda e: self._elegir("avatar"))
        fila.Add(btn_av, 0, wx.ALL, 5)
        btn_bn = wx.Button(panel, label=_("Cambiar &banner..."))
        btn_bn.Bind(wx.EVT_BUTTON, lambda e: self._elegir("banner"))
        fila.Add(btn_bn, 0, wx.ALL, 5)
        btn_handle = wx.Button(panel, label=_("Cambiar &handle..."))
        btn_handle.Bind(wx.EVT_BUTTON, self._on_handle)
        fila.Add(btn_handle, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)
        self.lbl_files = wx.StaticText(panel, label="")
        s.Add(self.lbl_files, 0, wx.ALL, 5)

        botones, _btn = _fila_botones(panel)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, self._on_guardar, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        habilitar_escape_cierre(self, wx.ID_CANCEL)

        bluesky_service.obtener_perfil_propio_async(account_id, callback=self._on_perfil)

    def _on_perfil(self, p, error):
        if error or not p:
            notificar(_("Error al cargar el perfil."))
            return
        self.lbl_handle.SetLabel(_("Handle: @{0}").format(p["handle"]))
        self.txt_name.SetValue(p["display_name"] or "")
        self.txt_bio.SetValue(p["description"] or "")

    def _elegir(self, cual):
        dlg = wx.FileDialog(self, _("Selecciona una imagen"), wildcard=_(
            "Imágenes (*.jpg;*.jpeg;*.png)|*.jpg;*.jpeg;*.png"), style=wx.FD_OPEN)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            if cual == "avatar":
                self.avatar_path = ruta
            else:
                self.banner_path = ruta
            partes = []
            if self.avatar_path:
                partes.append(_("avatar elegido"))
            if self.banner_path:
                partes.append(_("banner elegido"))
            self.lbl_files.SetLabel(", ".join(partes))
        dlg.Destroy()

    def _on_handle(self, event):
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Nuevo handle (p. ej. nombre.bsky.social):"),
                                    _("Cambiar handle"))
        if dlg.ShowModal() == wx.ID_OK:
            nuevo = dlg.GetValue().strip()
            if nuevo:
                def _cb(res, error):
                    if error:
                        notificar(_("Error al cambiar el handle: {0}").format(error))
                    else:
                        speak(_("Handle cambiado a {0}.").format(res), interrupt=True)
                        self.lbl_handle.SetLabel(_("Handle: @{0}").format(res))
                        try:
                            self.GetParent()._cargar_navegacion()
                        except Exception:
                            pass
                bluesky_service.cambiar_handle_async(self.account_id, nuevo, callback=_cb)
        dlg.Destroy()

    def _on_guardar(self, event):
        nombre = self.txt_name.GetValue().strip()
        bio = self.txt_bio.GetValue().strip()

        def _cb(res, error):
            if error:
                notificar(_("Error al guardar el perfil: {0}").format(error))
            else:
                notificar(_("Perfil actualizado."))
        bluesky_service.editar_perfil_async(self.account_id, nombre, bio,
                                            self.avatar_path, self.banner_path, callback=_cb)
        self.EndModal(wx.ID_OK)


# ======================================================================
# CONTRASEÑAS DE APLICACIÓN
# ======================================================================
class BlueskyAppPasswordsDialog(wx.Dialog):
    """Lista, crea y revoca contraseñas de aplicación."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Contraseñas de aplicación"), size=(460, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        from .accessible_controls import AccessibleListCtrl
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Contraseñas de aplicación activas:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Contraseñas de aplicación"))
        self.lista.InsertColumn(0, _("Nombre"), width=240)
        self.lista.InsertColumn(1, _("Creada"), width=160)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        # Crear una app-password NO es posible desde una sesión con app-password (el PDS
        # responde "Bad token scope" por diseño; auditoría BSKY-003): se abre la gestión
        # de la cuenta en el navegador, donde sí se puede crear con la sesión principal.
        btn_add = wx.Button(panel, label=_("&Crear en el navegador..."))
        btn_add.Bind(wx.EVT_BUTTON, self._on_add)
        fila.Add(btn_add, 0, wx.ALL, 5)
        btn_del = wx.Button(panel, label=_("&Revocar"))
        btn_del.Bind(wx.EVT_BUTTON, self._on_del)
        fila.Add(btn_del, 0, wx.ALL, 5)
        fila.AddStretchSpacer()
        btn_cerrar = wx.Button(panel, wx.ID_CLOSE, _("&Cerrar"))
        btn_cerrar.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE))
        fila.Add(btn_cerrar, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self._cargar()

    def _cargar(self):
        bluesky_service.listar_app_passwords_async(self.account_id, callback=self._on_lista)

    def _on_lista(self, passwords, error):
        if error:
            notificar(_("Error: {0}").format(error))
            return
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self.passwords = passwords or []
        for i, p in enumerate(self.passwords):
            self.lista.InsertItem(i, p["name"])
            self.lista.SetItem(i, 1, (p["created_at"] or "")[:10])
            self.lista.establecer_lectura(i, f"{p['name']}. {(p['created_at'] or '')[:10]}")

    def _on_add(self, event):
        """El servidor NO permite crear contraseñas de aplicación desde una sesión que ya
        usa una (responde "Bad token scope" por diseño). Antes esta pantalla lo intentaba
        igualmente y siempre fallaba con un error confuso. Ahora se abre la gestión de la
        cuenta en el navegador, donde se crea con la sesión principal."""
        mostrar_info(self, _(
            "Por seguridad, Bluesky no permite crear contraseñas de aplicación desde una "
            "sesión que ya usa una (como la de SocialRSS).\n\n"
            "Se abrirá la página de contraseñas de aplicación de tu cuenta en el navegador: "
            "inicia sesión allí con tu contraseña principal, crea la contraseña (marca "
            "\"Permitir acceso a los mensajes directos\" si quieres usar el chat) y cópiala."),
            _("Crear contraseña de aplicación"))
        from app.core.privacy import abrir_url
        abrir_url("https://bsky.app/settings/app-passwords")

    def _on_del(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(getattr(self, "passwords", [])):
            return
        nombre = self.passwords[idx]["name"]
        if not confirmar(self, _("¿Revocar la contraseña '{0}'?").format(nombre), _("Confirmar")):
            return
        bluesky_service.revocar_app_password_async(
            self.account_id, nombre, callback=lambda r, e: self._cargar() if not e else
            speak(_("Error: {0}").format(e), interrupt=True))


# ======================================================================
# EDITOR DE HILOS
# ======================================================================
class BlueskyThreadEditorDialog(wx.Dialog):
    """Compone un hilo: escribe texto largo, lo divide en posts de 300 y publica encadenado."""

    LIMITE = 300

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Nuevo hilo de Bluesky"), size=(560, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.segmentos = []
        self._reply_vals = ["everybody", "following", "mentioned", "nobody"]
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_("Texto del hilo (se dividirá en posts de 300):")), 0, wx.ALL, 5)
        self.txt = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(540, 160))
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_div = wx.Button(panel, label=_("&Dividir en posts"))
        btn_div.Bind(wx.EVT_BUTTON, self._on_dividir)
        fila.Add(btn_div, 0, wx.ALL, 5)
        btn_edit = wx.Button(panel, label=_("&Editar post seleccionado"))
        btn_edit.Bind(wx.EVT_BUTTON, self._on_editar)
        fila.Add(btn_edit, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        s.Add(wx.StaticText(panel, label=_("Vista previa de los posts:")), 0, wx.ALL, 5)
        self.lst = wx.ListBox(panel, size=(540, 120))
        s.Add(self.lst, 0, wx.EXPAND | wx.ALL, 5)

        # DESTINO del hilo. Bluesky NO tiene publicaciones privadas: su registro de post no lleva
        # ningún campo de audiencia, así que todo lo publicado es público y el "quién puede
        # responder" (threadgate) solo limita las respuestas, no quién lo ve. Lo único privado de
        # la red son los mensajes directos, así que para mandar un hilo en privado se envían sus
        # trozos como mensajes seguidos de una conversación.
        s.Add(wx.StaticText(panel, label=_("&Destino del hilo:")), 0, wx.ALL, 5)
        self.cmb_destino = wx.Choice(panel, choices=[
            _("Publicarlo (hilo público)"),
            _("Enviarlo por mensaje directo (privado)")], name=_("Destino del hilo"))
        self.cmb_destino.SetSelection(0)
        self.cmb_destino.Bind(wx.EVT_CHOICE, self._on_cambio_destino)
        s.Add(self.cmb_destino, 0, wx.ALL, 5)

        self.lbl_destinatario = wx.StaticText(panel, label=_("Destinata&rio (handle o DID):"))
        s.Add(self.lbl_destinatario, 0, wx.ALL, 5)
        self.txt_destinatario = wx.TextCtrl(panel, size=(300, -1), name=_("Destinatario del hilo"))
        self.txt_destinatario.SetToolTip(_(
            "Cuenta a la que enviar el hilo por mensaje directo, por ejemplo nombre.bsky.social. "
            "Bluesky no permite publicaciones privadas: los trozos se envían como mensajes "
            "seguidos de vuestra conversación."))
        s.Add(self.txt_destinatario, 0, wx.EXPAND | wx.ALL, 5)
        self.lbl_destinatario.Hide()
        self.txt_destinatario.Hide()

        s.Add(wx.StaticText(panel, label=_("Quién puede responder:")), 0, wx.ALL, 5)
        self.cmb_reply = wx.Choice(panel, choices=[
            _("Todos"), _("A quienes sigo"), _("Solo mencionados"), _("Nadie")])
        pred = config.get("bluesky", "default_reply_control", "everybody")
        self.cmb_reply.SetSelection(self._reply_vals.index(pred) if pred in self._reply_vals else 0)
        s.Add(self.cmb_reply, 0, wx.ALL, 5)

        botones, _btn = _fila_botones(panel)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, self._on_publicar, id=wx.ID_OK)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt.SetFocus()

    def _on_dividir(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            speak(_("No hay texto para dividir."), interrupt=True)
            return
        try:
            from app.services.mastodon.mastodon_threading import (
                dividir_texto_recursivo, obtener_regiones_protegidas)
            regiones = obtener_regiones_protegidas(texto)
            # Reservar margen para la numeración " (n/m)".
            self.segmentos = dividir_texto_recursivo(texto, self.LIMITE - 8, regiones)
        except Exception:
            # Respaldo simple por longitud.
            self.segmentos = [texto[i:i + self.LIMITE] for i in range(0, len(texto), self.LIMITE)]
        self._render()
        speak(_("Dividido en {0} posts.").format(len(self.segmentos)), interrupt=True)

    def _render(self):
        self.lst.Clear()
        total = len(self.segmentos)
        for i, seg in enumerate(self.segmentos, 1):
            self.lst.Append(f"[{i}/{total}] {seg[:60]}")

    def _on_editar(self, event):
        idx = self.lst.GetSelection()
        if idx < 0 or idx >= len(self.segmentos):
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Editar post {0}:").format(idx + 1),
                                    _("Editar post"), value=self.segmentos[idx], multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            self.segmentos[idx] = dlg.GetValue().strip()
            self._render()
        dlg.Destroy()

    def _es_envio_privado(self) -> bool:
        return self.cmb_destino.GetSelection() == 1

    def _on_cambio_destino(self, event):
        """Muestra el destinatario solo cuando el hilo va por mensaje directo, y explica por qué
        en Bluesky no hay otra forma de mandarlo en privado."""
        privado = self._es_envio_privado()
        self.lbl_destinatario.Show(privado)
        self.txt_destinatario.Show(privado)
        # El threadgate solo tiene sentido en un hilo publicado.
        self.cmb_reply.Enable(not privado)
        self.Layout()
        if privado:
            speak(_("Bluesky no admite publicaciones privadas: el hilo se enviará como mensajes "
                    "seguidos de vuestra conversación. Indica el destinatario."), interrupt=True)
            self.txt_destinatario.SetFocus()

    def _on_publicar(self, event):
        if not self.segmentos:
            self._on_dividir(None)
        if not self.segmentos:
            return
        largos = [i + 1 for i, s in enumerate(self.segmentos) if len(s) > self.LIMITE]
        if largos:
            mostrar_info(self, _("Los posts {0} superan los 300 caracteres. Edítalos.").format(largos),
                         _("Demasiado largo"))
            return

        # --- Envío PRIVADO: los trozos van como mensajes de una conversación ---
        if self._es_envio_privado():
            destinatario = self.txt_destinatario.GetValue().strip().lstrip("@")
            if not destinatario:
                mostrar_info(self, _("Escribe la cuenta a la que enviar el hilo por mensaje "
                                     "directo (por ejemplo, nombre.bsky.social)."),
                             _("Falta el destinatario"))
                self.txt_destinatario.SetFocus()
                return
            n = len(self.segmentos)

            def _cb_dm(ok, info):
                if ok:
                    notificar(_("Hilo enviado a {0}: {1} mensajes.").format(
                        (info or {}).get("title") or destinatario, (info or {}).get("enviados", n)))
                else:
                    notificar(_("No se pudo enviar el hilo por mensaje directo: {0}").format(info))
            bluesky_service.enviar_hilo_dm_async(
                self.account_id, destinatario, self.segmentos, callback=_cb_dm)
            speak(_("Enviando el hilo por mensaje directo..."), interrupt=True)
            self.EndModal(wx.ID_OK)
            return

        reply_control = self._reply_vals[self.cmb_reply.GetSelection()]

        def _cb(ok, info):
            if ok:
                notificar(_("Hilo publicado: {0} posts.").format(len(info)))
            else:
                notificar(_("Error al publicar el hilo: {0}").format(info))
        bluesky_service.publicar_hilo_async(
            self.account_id, self.segmentos, reply_control=reply_control, callback=_cb)
        speak(_("Publicando hilo..."), interrupt=True)
        self.EndModal(wx.ID_OK)


# ======================================================================
# CONTROLADOR PRINCIPAL
# ======================================================================
