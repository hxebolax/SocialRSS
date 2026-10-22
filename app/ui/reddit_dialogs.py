# -*- coding: utf-8 -*-
"""Diálogos de Reddit.

- El alta de cuenta sigue el patrón de Telegram: el client id se configura en Preferencias → Reddit
  y el alta abre directamente el navegador (sin diálogo de login aquí).
- RedditComposeDialog: envío de publicaciones de todos los tipos (texto, enlace, imagen, vídeo,
  galería, encuesta) con NSFW/spoiler, aviso de respuestas y etiqueta (flair) opcional.
"""
import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleTextCtrl

# URL donde el usuario registra su aplicación de Reddit y obtiene su client id.
PREFS_APPS_URL = "https://www.reddit.com/prefs/apps"

class RedditMessageDialog(wx.Dialog):
    """Diálogo para enviar un mensaje privado. `self.resultado` = (destinatario, asunto, cuerpo)."""

    def __init__(self, parent, destinatario=""):
        super().__init__(parent, title=_("Enviar mensaje privado"), size=(520, 400),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Enviar mensaje privado"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Destinatario (usuario, sin u/):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_dest = AccessibleTextCtrl(self, value=destinatario, name=_("Destinatario"))
        s.Add(self.txt_dest, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Asunto:")), 0, wx.LEFT, 8)
        self.txt_asunto = AccessibleTextCtrl(self, name=_("Asunto"), max_length=100)
        s.Add(self.txt_asunto, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Mensaje:")), 0, wx.LEFT, 8)
        self.txt_cuerpo = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 160),
                                             name=_("Cuerpo del mensaje"))
        s.Add(self.txt_cuerpo, 0, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(self, wx.ID_OK, _("&Enviar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(btn_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.CenterOnParent()
        (self.txt_cuerpo if destinatario else self.txt_dest).SetFocus()

    def _on_ok(self, event):
        from .accessible_controls import mostrar_info
        dest = self.txt_dest.GetValue().strip().lstrip("u/").lstrip("/")
        cuerpo = self.txt_cuerpo.GetValue().strip()
        if not dest or not cuerpo:
            mostrar_info(self, _("El destinatario y el mensaje son obligatorios."), _("Faltan datos"))
            return
        self.resultado = (dest, self.txt_asunto.GetValue().strip(), cuerpo)
        self.EndModal(wx.ID_OK)


TIPOS = [
    ("texto", lambda: _("Texto (self)")),
    ("enlace", lambda: _("Enlace")),
    ("imagen", lambda: _("Imagen")),
    ("video", lambda: _("Vídeo")),
    ("galeria", lambda: _("Galería de imágenes")),
    ("encuesta", lambda: _("Encuesta")),
]


class RedditComposeDialog(wx.Dialog):
    """Diálogo de redacción/envío de una publicación de Reddit.

    Al aceptar, `self.resultado` es un dict con los datos de la publicación (o None si se cancela)."""

    def __init__(self, parent, account_id, subreddit_inicial=""):
        super().__init__(parent, title=_("Publicar en Reddit"), size=(640, 620),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Publicar en Reddit"))
        self.account_id = account_id
        self.resultado = None
        self._flairs = []
        self._gallery = []
        self._init_ui(subreddit_inicial)
        self.CenterOnParent()
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))

    def _init_ui(self, subreddit_inicial):
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(self, label=_("Comunidad (subreddit, sin r/):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_sub = AccessibleTextCtrl(self, value=subreddit_inicial,
                                          name=_("Comunidad (subreddit)"))
        s.Add(self.txt_sub, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Título:")), 0, wx.LEFT, 8)
        self.txt_titulo = AccessibleTextCtrl(self, name=_("Título de la publicación"), max_length=300)
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Tipo de publicación:")), 0, wx.LEFT, 8)
        self.choice_tipo = wx.Choice(self, choices=[t[1]() for t in TIPOS], name=_("Tipo de publicación"))
        self.choice_tipo.SetSelection(0)
        self.choice_tipo.Bind(wx.EVT_CHOICE, lambda e: self._actualizar_campos())
        s.Add(self.choice_tipo, 0, wx.EXPAND | wx.ALL, 6)

        # --- Campos dinámicos ---
        # Texto (cuerpo) / encuesta (cuerpo opcional)
        self.lbl_cuerpo = wx.StaticText(self, label=_("Texto:"))
        s.Add(self.lbl_cuerpo, 0, wx.LEFT, 8)
        self.txt_cuerpo = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 120),
                                             name=_("Texto de la publicación"))
        s.Add(self.txt_cuerpo, 0, wx.EXPAND | wx.ALL, 6)

        # Enlace (URL)
        self.lbl_url = wx.StaticText(self, label=_("Dirección (URL):"))
        s.Add(self.lbl_url, 0, wx.LEFT, 8)
        self.txt_url = AccessibleTextCtrl(self, name=_("Dirección URL del enlace"))
        s.Add(self.txt_url, 0, wx.EXPAND | wx.ALL, 6)

        # Archivo (imagen/vídeo)
        fila_arch = wx.BoxSizer(wx.HORIZONTAL)
        self.txt_archivo = AccessibleTextCtrl(self, name=_("Ruta del archivo"),
                                              style=wx.TE_READONLY)
        self.btn_archivo = wx.Button(self, label=_("&Examinar..."), name=_("Elegir archivo"))
        self.btn_archivo.Bind(wx.EVT_BUTTON, self._on_examinar)
        fila_arch.Add(self.txt_archivo, 1, wx.EXPAND | wx.RIGHT, 4)
        fila_arch.Add(self.btn_archivo, 0)
        self.sizer_arch = fila_arch
        s.Add(fila_arch, 0, wx.EXPAND | wx.ALL, 6)

        # Galería (lista de imágenes)
        self.lbl_galeria = wx.StaticText(self, label=_("Imágenes de la galería (mínimo 2):"))
        s.Add(self.lbl_galeria, 0, wx.LEFT, 8)
        self.lst_galeria = wx.ListBox(self, name=_("Imágenes de la galería"))
        s.Add(self.lst_galeria, 0, wx.EXPAND | wx.ALL, 6)
        fila_gal = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_gal_add = wx.Button(self, label=_("Añadir imagen..."), name=_("Añadir imagen a la galería"))
        self.btn_gal_add.Bind(wx.EVT_BUTTON, self._on_gal_add)
        self.btn_gal_del = wx.Button(self, label=_("Quitar imagen"), name=_("Quitar imagen de la galería"))
        self.btn_gal_del.Bind(wx.EVT_BUTTON, self._on_gal_del)
        fila_gal.Add(self.btn_gal_add, 0, wx.RIGHT, 4)
        fila_gal.Add(self.btn_gal_del, 0)
        self.sizer_gal_btns = fila_gal
        s.Add(fila_gal, 0, wx.ALL, 6)

        # Encuesta (opciones + duración)
        self.lbl_opciones = wx.StaticText(self, label=_("Opciones de la encuesta (una por línea, de 2 a 6):"))
        s.Add(self.lbl_opciones, 0, wx.LEFT, 8)
        self.txt_opciones = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 90),
                                               name=_("Opciones de la encuesta"))
        s.Add(self.txt_opciones, 0, wx.EXPAND | wx.ALL, 6)
        fila_dur = wx.BoxSizer(wx.HORIZONTAL)
        self.lbl_duracion = wx.StaticText(self, label=_("Duración (días, 1 a 7):"))
        self.spin_duracion = wx.SpinCtrl(self, min=1, max=7, initial=3, name=_("Duración de la encuesta en días"))
        fila_dur.Add(self.lbl_duracion, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        fila_dur.Add(self.spin_duracion, 0)
        self.sizer_dur = fila_dur
        s.Add(fila_dur, 0, wx.ALL, 6)

        # Opciones comunes
        self.cb_nsfw = wx.CheckBox(self, label=_("Marcar como +18 (NSFW)"), name=_("Marcar como +18"))
        self.cb_spoiler = wx.CheckBox(self, label=_("Marcar como spoiler"), name=_("Marcar como spoiler"))
        self.cb_replies = wx.CheckBox(self, label=_("Recibir respuestas en la bandeja de entrada"),
                                      name=_("Recibir respuestas en la bandeja"))
        self.cb_replies.SetValue(True)
        s.Add(self.cb_nsfw, 0, wx.ALL, 4)
        s.Add(self.cb_spoiler, 0, wx.ALL, 4)
        s.Add(self.cb_replies, 0, wx.ALL, 4)

        # Flair
        fila_flair = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_flair = wx.Button(self, label=_("Cargar etiquetas (flair)..."),
                                   name=_("Cargar etiquetas del subreddit"))
        self.btn_flair.Bind(wx.EVT_BUTTON, self._on_cargar_flairs)
        self.choice_flair = wx.Choice(self, choices=[_("(sin etiqueta)")], name=_("Etiqueta (flair)"))
        self.choice_flair.SetSelection(0)
        fila_flair.Add(self.btn_flair, 0, wx.RIGHT, 6)
        fila_flair.Add(self.choice_flair, 1, wx.EXPAND)
        s.Add(fila_flair, 0, wx.EXPAND | wx.ALL, 6)

        # Botones
        botones = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(self, wx.ID_OK, _("&Publicar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(btn_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)

        self.SetSizer(s)
        self._actualizar_campos()
        self.txt_sub.SetFocus()

    def _mostrar(self, widget, visible):
        widget.Show(visible)

    def _actualizar_campos(self):
        tipo = TIPOS[self.choice_tipo.GetSelection()][0]
        es = {t[0]: (t[0] == tipo) for t in TIPOS}
        # Cuerpo: texto y encuesta (opcional)
        self.lbl_cuerpo.Show(es["texto"] or es["encuesta"])
        self.txt_cuerpo.Show(es["texto"] or es["encuesta"])
        self.lbl_cuerpo.SetLabel(_("Texto:") if es["texto"] else _("Texto (opcional):"))
        # Enlace
        self.lbl_url.Show(es["enlace"])
        self.txt_url.Show(es["enlace"])
        # Archivo
        self.txt_archivo.Show(es["imagen"] or es["video"])
        self.btn_archivo.Show(es["imagen"] or es["video"])
        # Galería
        self.lbl_galeria.Show(es["galeria"])
        self.lst_galeria.Show(es["galeria"])
        self.btn_gal_add.Show(es["galeria"])
        self.btn_gal_del.Show(es["galeria"])
        # Encuesta
        self.lbl_opciones.Show(es["encuesta"])
        self.txt_opciones.Show(es["encuesta"])
        self.lbl_duracion.Show(es["encuesta"])
        self.spin_duracion.Show(es["encuesta"])
        self.Layout()

    # ------------------------------------------------------------------ archivos
    def _on_examinar(self, event):
        tipo = TIPOS[self.choice_tipo.GetSelection()][0]
        if tipo == "video":
            wildcard = _("Vídeos") + " (*.mp4;*.mov)|*.mp4;*.mov|" + _("Todos los archivos") + " (*.*)|*.*"
        else:
            wildcard = _("Imágenes") + " (*.jpg;*.jpeg;*.png;*.gif)|*.jpg;*.jpeg;*.png;*.gif|" + \
                       _("Todos los archivos") + " (*.*)|*.*"
        with wx.FileDialog(self, _("Elegir archivo"), wildcard=wildcard,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as fd:
            if fd.ShowModal() == wx.ID_OK:
                self.txt_archivo.SetValue(fd.GetPath())

    def _on_gal_add(self, event):
        wildcard = _("Imágenes") + " (*.jpg;*.jpeg;*.png;*.gif)|*.jpg;*.jpeg;*.png;*.gif"
        with wx.FileDialog(self, _("Añadir imagen"), wildcard=wildcard,
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE) as fd:
            if fd.ShowModal() == wx.ID_OK:
                for p in fd.GetPaths():
                    self._gallery.append(p)
                    self.lst_galeria.Append(p)

    def _on_gal_del(self, event):
        sel = self.lst_galeria.GetSelection()
        if sel != wx.NOT_FOUND:
            self.lst_galeria.Delete(sel)
            del self._gallery[sel]

    # ------------------------------------------------------------------ flairs
    def _on_cargar_flairs(self, event):
        sub = self.txt_sub.GetValue().strip()
        if not sub:
            speak(_("Escribe primero la comunidad (subreddit)."), interrupt=True)
            return
        speak(_("Cargando etiquetas..."), interrupt=True)
        from app.services.reddit import reddit_service

        def _bg(ctx):
            return reddit_service.flairs_de_subreddit(self.account_id, sub)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar las etiquetas: {0}").format(err))
                return
            self._flairs = res or []
            self.choice_flair.Set([_("(sin etiqueta)")] + [f.get("text") or _("(sin texto)")
                                                           for f in self._flairs])
            self.choice_flair.SetSelection(0)
            speak(_("{0} etiquetas disponibles.").format(len(self._flairs)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _flair_elegido(self):
        idx = self.choice_flair.GetSelection()
        if idx <= 0 or (idx - 1) >= len(self._flairs):
            return None, None
        f = self._flairs[idx - 1]
        return f.get("id"), f.get("text")

    # ------------------------------------------------------------------ aceptar
    def _on_ok(self, event):
        from .accessible_controls import mostrar_info
        sub = self.txt_sub.GetValue().strip()
        titulo = self.txt_titulo.GetValue().strip()
        if not sub or not titulo:
            mostrar_info(self, _("La comunidad y el título son obligatorios."), _("Faltan datos"))
            return
        tipo = TIPOS[self.choice_tipo.GetSelection()][0]
        flair_id, flair_text = self._flair_elegido()
        datos = {"tipo": tipo, "subreddit": sub, "title": titulo,
                 "nsfw": self.cb_nsfw.GetValue(), "spoiler": self.cb_spoiler.GetValue(),
                 "send_replies": self.cb_replies.GetValue(),
                 "flair_id": flair_id, "flair_text": flair_text}
        if tipo == "texto":
            datos["selftext"] = self.txt_cuerpo.GetValue()
        elif tipo == "enlace":
            url = self.txt_url.GetValue().strip()
            if not url:
                mostrar_info(self, _("Escribe la dirección (URL) del enlace."), _("Falta la URL"))
                return
            datos["url"] = url
        elif tipo in ("imagen", "video"):
            ruta = self.txt_archivo.GetValue().strip()
            if not ruta:
                mostrar_info(self, _("Elige un archivo para publicar."), _("Falta el archivo"))
                return
            datos["path"] = ruta
        elif tipo == "galeria":
            if len(self._gallery) < 2:
                mostrar_info(self, _("Añade al menos 2 imágenes a la galería."), _("Galería incompleta"))
                return
            datos["gallery"] = list(self._gallery)
        elif tipo == "encuesta":
            opciones = [l.strip() for l in self.txt_opciones.GetValue().splitlines() if l.strip()]
            if not (2 <= len(opciones) <= 6):
                mostrar_info(self, _("La encuesta necesita entre 2 y 6 opciones."), _("Opciones inválidas"))
                return
            datos["selftext"] = self.txt_cuerpo.GetValue()
            datos["poll_options"] = opciones
            datos["poll_duration"] = self.spin_duracion.GetValue()
        self.resultado = datos
        self.EndModal(wx.ID_OK)
