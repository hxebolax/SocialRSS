# -*- coding: utf-8 -*-
"""Diálogos accesibles de W Social: alta de cuenta, compositor, conversaciones, perfiles,
hilos y paquetes de inicio.

Todos siguen las reglas de la aplicación: etiquetas visibles junto a cada control, botón
«Aceptar» (nunca «OK»), Escape cierra, y nada de leer dos veces lo que el lector ya anuncia.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.wsocial import wsocial_service
from app.services.wsocial.wsocial_common import LIMITE_CARACTERES, ETIQUETAS_CONTENIDO
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                  mostrar_info, confirmar, habilitar_escape_cierre)


def _fila_botones(panel, etiqueta_aceptar=None):
    """Fila estándar Aceptar / Cancelar (en español, como manda el proyecto)."""
    fila = wx.BoxSizer(wx.HORIZONTAL)
    btn_ok = wx.Button(panel, wx.ID_OK, etiqueta_aceptar or _("&Aceptar"))
    btn_cancel = wx.Button(panel, wx.ID_CANCEL, _("&Cancelar"))
    fila.AddStretchSpacer()
    fila.Add(btn_ok, 0, wx.ALL, 5)
    fila.Add(btn_cancel, 0, wx.ALL, 5)
    return fila, btn_ok


# ======================================================================
# ALTA DE CUENTA
# ======================================================================
class WSocialLoginDialog(wx.Dialog):
    """Alta (o reautorización) de una cuenta de W Social.

    Primero se RESUELVE la identidad del usuario para saber en qué servidor vive su cuenta y
    enseñárselo; solo después se pide la contraseña de aplicación. Así el cliente nunca da por
    supuesto un servidor ni manda credenciales a donde no toca."""

    def __init__(self, parent, account_id=None):
        titulo = (_("Volver a autorizar la cuenta de W Social") if account_id
                  else _("Añadir una cuenta de W Social"))
        super().__init__(parent, title=titulo, size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self._reautorizar = account_id is not None
        self._identidad = None
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_(
            "W Social usa AT Protocol, la misma tecnología abierta que Bluesky. Escribe tu nombre "
            "de usuario y una CONTRASEÑA DE APLICACIÓN (no la de tu cuenta): la creas desde los "
            "ajustes de W Social en la web.")), 0, wx.ALL, 10)

        s.Add(wx.StaticText(panel, label=_("&Usuario (por ejemplo nombre.wsocial.eu):")),
              0, wx.LEFT | wx.TOP, 10)
        self.txt_usuario = AccessibleTextCtrl(
            panel, name=_("Nombre de usuario"),
            ayuda_texto=_("Tu identificador en W Social. También vale un DID completo."))
        if account_id:
            from app.core.security import security
            self.txt_usuario.SetValue(
                security.obtener_credencial(f"wsocial:{account_id}", "handle") or "")
        s.Add(self.txt_usuario, 0, wx.EXPAND | wx.ALL, 10)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_comprobar = wx.Button(panel, label=_("Comprobar el &servidor"),
                                       name=_("Comprobar en qué servidor está la cuenta"))
        self.btn_comprobar.Bind(wx.EVT_BUTTON, self._on_comprobar)
        fila.Add(self.btn_comprobar, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.LEFT, 5)

        self.lbl_servidor = wx.StaticText(panel, label=_("Servidor: sin comprobar."))
        s.Add(self.lbl_servidor, 0, wx.ALL, 10)

        s.Add(wx.StaticText(panel, label=_("Contraseña de &aplicación:")), 0, wx.LEFT | wx.TOP, 10)
        self.txt_password = AccessibleTextCtrl(
            panel, style=wx.TE_PASSWORD, name=_("Contraseña de aplicación"),
            ayuda_texto=_("Contraseña de aplicación creada en los ajustes de W Social. Para poder "
                          "usar los mensajes directos, márcala con acceso a mensajes."))
        s.Add(self.txt_password, 0, wx.EXPAND | wx.ALL, 10)

        s.Add(wx.StaticText(panel, label=_("Servidor a mano (opcional, solo si sabes lo que haces):")),
              0, wx.LEFT | wx.TOP, 10)
        self.txt_pds = AccessibleTextCtrl(
            panel, name=_("Dirección del servidor"),
            ayuda_texto=_("Déjalo vacío para que se descubra solo a partir de tu usuario."))
        s.Add(self.txt_pds, 0, wx.EXPAND | wx.ALL, 10)

        botones, self.btn_ok = _fila_botones(panel, _("&Iniciar sesión"))
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, self._on_aceptar, id=wx.ID_OK)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt_usuario.SetFocus()

    def _on_comprobar(self, event):
        """Resuelve la identidad y muestra en qué servidor está la cuenta."""
        usuario = self.txt_usuario.GetValue().strip()
        if not usuario:
            mostrar_info(self, _("Escribe primero tu nombre de usuario."), _("Falta el usuario"))
            return
        speak(_("Buscando tu cuenta..."), interrupt=True)
        pds = self.txt_pds.GetValue().strip()

        def _cb(ok, res):
            if not ok:
                wx.CallAfter(self.lbl_servidor.SetLabel, _("Servidor: no se pudo averiguar."))
                wx.CallAfter(notificar, str(res))
                return
            self._identidad = res
            servidor = res.get("servidor") or {}
            dominios = ", ".join(servidor.get("availableUserDomains", []) or [])
            texto = _("Servidor: {0}").format(res.get("pds", ""))
            if dominios:
                texto += _(" (dominios: {0})").format(dominios)
            wx.CallAfter(self.lbl_servidor.SetLabel, texto)
            wx.CallAfter(speak, texto, False)
            wx.CallAfter(self.txt_password.SetFocus)

        from app.core.task_manager import task_manager
        task_manager.start_task(
            lambda ctx: wsocial_service.resolver_cuenta(usuario, pds),
            callback=lambda r, e: _cb(not e, r if not e else e), pass_context=True)

    def _on_aceptar(self, event):
        usuario = self.txt_usuario.GetValue().strip()
        password = self.txt_password.GetValue().strip()
        pds = self.txt_pds.GetValue().strip()
        if not usuario or not password:
            mostrar_info(self, _("Hacen falta el usuario y la contraseña de aplicación."),
                         _("Faltan datos"))
            return
        self.btn_ok.Disable()
        speak(_("Iniciando sesión..."), interrupt=True)

        def _fin(res, error):
            self.btn_ok.Enable()
            if error:
                notificar(str(error))
                return
            self.account_id = res.get("account_id", self.account_id)
            notificar(_("Cuenta @{0} lista en {1}.").format(res.get("handle", ""), res.get("pds", "")))
            self.EndModal(wx.ID_OK)

        from app.core.task_manager import task_manager
        if self._reautorizar:
            task_manager.start_task(
                lambda ctx: wsocial_service.reautenticar(self.account_id, usuario, password, pds),
                callback=_fin, pass_context=True)
        else:
            task_manager.start_task(
                lambda ctx: wsocial_service.completar_login(usuario, password, pds),
                callback=_fin, pass_context=True)


# ======================================================================
# COMPOSITOR
# ======================================================================
class WSocialComposeDialog(wx.Dialog):
    """Escribir una publicación: texto, adjuntos, aviso de contenido, idioma y quién puede
    responder o citar."""

    def __init__(self, parent, account_id, reply_uri="", quote_uri="", texto_inicial=""):
        # Al responder: a quién (autor y texto) y, cuando llega del servidor, quién recibirá el
        # aviso. Como en Bluesky: el servidor de W avisa a los de encima en el hilo sin menciones.
        respondiendo = self._respondiendo_local(reply_uri) if reply_uri else None
        if respondiendo:
            titulo = _("Responder a {0}").format(respondiendo[0])
        else:
            titulo = (_("Responder en W Social") if reply_uri else
                      _("Citar en W Social") if quote_uri else _("Nueva publicación en W Social"))
        super().__init__(parent, title=titulo, size=(640, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.reply_uri = reply_uri
        self.quote_uri = quote_uri
        self.adjuntos = []       # [(ruta, alt)]
        self.video = ""
        self._reply_vals = ["everybody", "following", "mentioned", "nobody"]
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.txt_respondiendo = None
        if reply_uri:
            s.Add(wx.StaticText(panel, label=_("Respondiendo a:")), 0, wx.ALL, 5)
            self.txt_respondiendo = wx.TextCtrl(
                panel, value=(respondiendo[1] if respondiendo
                              else _("Cargando la publicación a la que respondes…")),
                style=wx.TE_MULTILINE | wx.TE_READONLY, size=(600, 70), name=_("Respondiendo a"))
            s.Add(self.txt_respondiendo, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("&Texto de la publicación:")), 0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(
            panel, style=wx.TE_MULTILINE, size=(600, 180), name=_("Texto de la publicación"),
            ayuda_texto=_("Escribe tu publicación. Los enlaces, las menciones con arroba y las "
                          "etiquetas con almohadilla se publican como elementos reales."))
        self.txt.SetValue(texto_inicial or "")
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt)
        self.txt.Bind(wx.EVT_TEXT, self._on_texto)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        self.lbl_contador = wx.StaticText(panel, label=str(LIMITE_CARACTERES))
        s.Add(self.lbl_contador, 0, wx.LEFT, 10)

        # Adjuntos
        fila_adj = wx.BoxSizer(wx.HORIZONTAL)
        btn_img = wx.Button(panel, label=_("Añadir &imagen..."))
        btn_img.Bind(wx.EVT_BUTTON, self._on_imagen)
        fila_adj.Add(btn_img, 0, wx.ALL, 5)
        btn_video = wx.Button(panel, label=_("Añadir &vídeo..."))
        btn_video.Bind(wx.EVT_BUTTON, self._on_video)
        fila_adj.Add(btn_video, 0, wx.ALL, 5)
        btn_quitar = wx.Button(panel, label=_("&Quitar adjuntos"))
        btn_quitar.Bind(wx.EVT_BUTTON, self._on_quitar_adjuntos)
        fila_adj.Add(btn_quitar, 0, wx.ALL, 5)
        s.Add(fila_adj, 0, wx.EXPAND)
        self.lbl_adjuntos = wx.StaticText(panel, label=_("Sin adjuntos."))
        s.Add(self.lbl_adjuntos, 0, wx.LEFT | wx.BOTTOM, 10)

        # Aviso de contenido
        self.chk_aviso = wx.CheckBox(panel, label=_("Marcar como contenido sensible"),
                                     name=_("Contenido sensible"))
        self.chk_aviso.Bind(wx.EVT_CHECKBOX, self._on_aviso)
        s.Add(self.chk_aviso, 0, wx.ALL, 5)
        self.choice_aviso = wx.Choice(panel, choices=[_("Desnudos artísticos"), _("Sexual"),
                                                      _("Pornografía"), _("Violencia gráfica")],
                                      name=_("Tipo de aviso de contenido"))
        self.choice_aviso.SetSelection(0)
        self.choice_aviso.Disable()
        s.Add(self.choice_aviso, 0, wx.LEFT | wx.BOTTOM, 20)

        # Quién puede responder y citar
        fila_ctrl = wx.BoxSizer(wx.HORIZONTAL)
        fila_ctrl.Add(wx.StaticText(panel, label=_("Quién puede &responder:")),
                      0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.cmb_reply = wx.Choice(panel, choices=[_("Todos"), _("A quienes sigo"),
                                                   _("Solo mencionados"), _("Nadie")],
                                   name=_("Quién puede responder"))
        pred = config.get("wsocial", "default_reply_control", "everybody")
        self.cmb_reply.SetSelection(self._reply_vals.index(pred) if pred in self._reply_vals else 0)
        fila_ctrl.Add(self.cmb_reply, 0, wx.ALL, 5)
        self.chk_no_citar = wx.CheckBox(panel, label=_("No permitir que la citen"),
                                        name=_("Impedir citas"))
        fila_ctrl.Add(self.chk_no_citar, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        s.Add(fila_ctrl, 0, wx.EXPAND)

        # Idioma
        fila_idioma = wx.BoxSizer(wx.HORIZONTAL)
        fila_idioma.Add(wx.StaticText(panel, label=_("&Idioma:")),
                        0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self._idiomas = [("es", _("Español")), ("en", _("Inglés")), ("ca", _("Catalán")),
                         ("gl", _("Gallego")), ("eu", _("Euskera")), ("pt", _("Portugués")),
                         ("fr", _("Francés")), ("it", _("Italiano")), ("de", _("Alemán"))]
        self.choice_idioma = wx.Choice(panel, choices=[n for _c, n in self._idiomas],
                                       name=_("Idioma de la publicación"))
        idioma_def = config.get("general", "language", "es") or "es"
        codigos = [c for c, _n in self._idiomas]
        self.choice_idioma.SetSelection(codigos.index(idioma_def) if idioma_def in codigos else 0)
        fila_idioma.Add(self.choice_idioma, 0, wx.ALL, 5)
        s.Add(fila_idioma, 0, wx.EXPAND)

        botones, self.btn_ok = _fila_botones(panel, _("&Publicar"))
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, self._on_publicar, id=wx.ID_OK)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt.SetFocus()
        self.txt.SetInsertionPointEnd()
        if reply_uri:
            self._pedir_contexto_respuesta(reply_uri)

    @staticmethod
    def _respondiendo_local(uri):
        """(nombre, texto para "Respondiendo a") de la publicación a la que se responde, con lo
        que hay en la caché (sin red), o None si no está."""
        fila = db.query_one("SELECT author_name, author_handle, content FROM wsocial_posts "
                            "WHERE uri = ? LIMIT 1", (uri,))
        if not fila:
            return None
        nombre, handle, texto = fila
        return (nombre or handle,
                _("{0} (@{1}): {2}").format(nombre or handle, handle,
                                            (texto or "").replace("\n", " ").strip()))

    def _pedir_contexto_respuesta(self, uri):
        """Completa "Respondiendo a" con quién recibirá el aviso (consulta al servidor)."""
        from app.core.at_lectura import texto_contexto_respuesta

        def _fin(ok, res):
            def _poner():
                if self and self.txt_respondiendo and ok and res:
                    self.txt_respondiendo.SetValue(texto_contexto_respuesta(res))
            wx.CallAfter(_poner)
        try:
            wsocial_service.contexto_respuesta_async(self.account_id, uri, callback=_fin)
        except Exception:
            pass

    def _on_texto(self, event):
        """Contador de caracteres restantes (el límite del protocolo son 300)."""
        restantes = LIMITE_CARACTERES - len(self.txt.GetValue())
        self.lbl_contador.SetLabel(_("{0} caracteres restantes").format(restantes))
        event.Skip()

    def _on_aviso(self, event):
        self.choice_aviso.Enable(self.chk_aviso.IsChecked())

    def _on_imagen(self, event):
        dlg = wx.FileDialog(self, _("Elige una imagen"),
                            wildcard=_("Imágenes") + " (*.jpg;*.jpeg;*.png;*.gif;*.webp)|"
                                     "*.jpg;*.jpeg;*.png;*.gif;*.webp",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            if len(self.adjuntos) >= 4:
                mostrar_info(self, _("Solo se pueden adjuntar 4 imágenes."), _("Demasiadas imágenes"))
            else:
                alt = ""
                d2 = CustomTextEntryDialog(
                    self, _("Descripción de la imagen (texto alternativo). Es lo que leerá quien "
                            "no pueda verla:"), _("Describir la imagen"))
                if d2.ShowModal() == wx.ID_OK:
                    alt = d2.GetValue().strip()
                d2.Destroy()
                self.adjuntos.append((ruta, alt))
                self.video = ""
                self._actualizar_adjuntos()
        dlg.Destroy()

    def _on_video(self, event):
        dlg = wx.FileDialog(self, _("Elige un vídeo"),
                            wildcard=_("Vídeos") + " (*.mp4;*.mov;*.webm)|*.mp4;*.mov;*.webm",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.video = dlg.GetPath()
            self.adjuntos = []
            self._actualizar_adjuntos()
        dlg.Destroy()

    def _on_quitar_adjuntos(self, event):
        self.adjuntos = []
        self.video = ""
        self._actualizar_adjuntos()

    def _actualizar_adjuntos(self):
        if self.video:
            import os
            texto = _("Vídeo: {0}").format(os.path.basename(self.video))
        elif self.adjuntos:
            sin_alt = sum(1 for _r, a in self.adjuntos if not a)
            texto = _("{0} imagen(es)").format(len(self.adjuntos))
            if sin_alt:
                texto += _(", {0} sin descripción").format(sin_alt)
        else:
            texto = _("Sin adjuntos.")
        self.lbl_adjuntos.SetLabel(texto)
        speak(texto, interrupt=False)

    def _on_publicar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto and not self.adjuntos and not self.video:
            mostrar_info(self, _("Escribe algo o añade un adjunto."), _("Publicación vacía"))
            return
        if len(texto) > LIMITE_CARACTERES:
            sobran = len(texto) - LIMITE_CARACTERES
            from .accessible_controls import elegir_opcion
            r = elegir_opcion(
                self, _("El texto tiene {0} caracteres de más (el máximo son {1}). ¿Qué hacemos?")
                .format(sobran, LIMITE_CARACTERES), _("Texto demasiado largo"),
                [_("Convertirlo en un &hilo"), _("&Volver a editarlo")])
            if r != 0:
                return
            padre = self.GetParent()
            cuenta = self.account_id
            self.EndModal(wx.ID_CANCEL)

            def _abrir():
                dlg = WSocialThreadEditorDialog(padre, cuenta, texto_inicial=texto)
                dlg.ShowModal()
                dlg.Destroy()
            wx.CallAfter(_abrir)
            return

        etiquetas = []
        if self.chk_aviso.IsChecked():
            etiquetas = [ETIQUETAS_CONTENIDO[self.choice_aviso.GetSelection()]]
        idx_idioma = self.choice_idioma.GetSelection()
        idioma = self._idiomas[idx_idioma][0] if 0 <= idx_idioma < len(self._idiomas) else "es"

        def _cb(ok, res):
            if ok:
                notificar(_("Publicado en W Social."))
            else:
                notificar(_("No se pudo publicar: {0}. Queda en «Salida pendiente» del menú "
                            "W Social para reintentarlo.").format(res))
        speak(_("Publicando..."), interrupt=True)
        wsocial_service.publicar_post_async(
            self.account_id, texto,
            imagenes=[r for r, _a in self.adjuntos],
            alt_texts=[a for _r, a in self.adjuntos],
            video=self.video, reply_uri=self.reply_uri, quote_uri=self.quote_uri,
            langs=[idioma], labels=etiquetas,
            reply_control=self._reply_vals[self.cmb_reply.GetSelection()],
            quote_control=("nobody" if self.chk_no_citar.IsChecked() else ""),
            callback=_cb)
        self.EndModal(wx.ID_OK)


# ======================================================================
# EDITOR DE HILOS
# ======================================================================
class WSocialThreadEditorDialog(wx.Dialog):
    """Divide un texto largo en publicaciones encadenadas y las publica, o las envía por privado."""

    def __init__(self, parent, account_id, texto_inicial=""):
        super().__init__(parent, title=_("Nuevo hilo de W Social"), size=(600, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.segmentos = []
        self._reply_vals = ["everybody", "following", "mentioned", "nobody"]
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_("&Texto del hilo (se dividirá en publicaciones de 300):")),
              0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, size=(580, 180),
                                      name=_("Texto del hilo"))
        self.txt.SetValue(texto_inicial or "")
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_dividir = wx.Button(panel, label=_("&Dividir en publicaciones"))
        btn_dividir.Bind(wx.EVT_BUTTON, self._on_dividir)
        fila.Add(btn_dividir, 0, wx.ALL, 5)
        btn_editar = wx.Button(panel, label=_("&Editar la seleccionada"))
        btn_editar.Bind(wx.EVT_BUTTON, self._on_editar)
        fila.Add(btn_editar, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.EXPAND)

        s.Add(wx.StaticText(panel, label=_("Vista previa:")), 0, wx.ALL, 5)
        self.lst = wx.ListBox(panel, size=(580, 120), name=_("Publicaciones del hilo"))
        s.Add(self.lst, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("&Destino del hilo:")), 0, wx.ALL, 5)
        self.cmb_destino = wx.Choice(panel, choices=[_("Publicarlo (hilo público)"),
                                                     _("Enviarlo por mensaje directo (privado)")],
                                     name=_("Destino del hilo"))
        self.cmb_destino.SetSelection(0)
        self.cmb_destino.Bind(wx.EVT_CHOICE, self._on_destino)
        s.Add(self.cmb_destino, 0, wx.ALL, 5)

        self.lbl_destinatario = wx.StaticText(panel, label=_("Destinatario (usuario o DID):"))
        s.Add(self.lbl_destinatario, 0, wx.LEFT, 10)
        self.txt_destinatario = AccessibleTextCtrl(panel, name=_("Destinatario del hilo"))
        s.Add(self.txt_destinatario, 0, wx.EXPAND | wx.ALL, 5)
        self.lbl_destinatario.Hide()
        self.txt_destinatario.Hide()

        s.Add(wx.StaticText(panel, label=_("Quién puede responder:")), 0, wx.ALL, 5)
        self.cmb_reply = wx.Choice(panel, choices=[_("Todos"), _("A quienes sigo"),
                                                   _("Solo mencionados"), _("Nadie")],
                                   name=_("Quién puede responder al hilo"))
        self.cmb_reply.SetSelection(0)
        s.Add(self.cmb_reply, 0, wx.ALL, 5)

        botones, _btn = _fila_botones(panel, _("&Publicar el hilo"))
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 5)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, self._on_publicar, id=wx.ID_OK)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt.SetFocus()

    def _on_destino(self, event):
        privado = self.cmb_destino.GetSelection() == 1
        self.lbl_destinatario.Show(privado)
        self.txt_destinatario.Show(privado)
        self.cmb_reply.Enable(not privado)
        self.Layout()
        if privado:
            speak(_("En W Social no hay publicaciones privadas: el hilo se enviará como mensajes "
                    "seguidos de vuestra conversación. Indica el destinatario."), interrupt=True)
            self.txt_destinatario.SetFocus()

    def _on_dividir(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            speak(_("No hay texto que dividir."), interrupt=True)
            return
        # Se reutiliza el troceador del proyecto, que respeta palabras, enlaces y menciones.
        from app.services.mastodon.mastodon_threading import (
            dividir_texto_recursivo, obtener_regiones_protegidas)
        try:
            regiones = obtener_regiones_protegidas(texto)
            self.segmentos = dividir_texto_recursivo(texto, LIMITE_CARACTERES - 8, regiones)
        except Exception:
            self.segmentos = [texto[i:i + LIMITE_CARACTERES]
                              for i in range(0, len(texto), LIMITE_CARACTERES)]
        self._render()
        speak(_("Dividido en {0} publicaciones.").format(len(self.segmentos)), interrupt=True)

    def _render(self):
        self.lst.Clear()
        total = len(self.segmentos)
        for i, seg in enumerate(self.segmentos, 1):
            self.lst.Append(f"[{i}/{total}] {seg[:70]}")

    def _on_editar(self, event):
        idx = self.lst.GetSelection()
        if idx < 0 or idx >= len(self.segmentos):
            return
        dlg = CustomTextEntryDialog(self, _("Editar la publicación {0}:").format(idx + 1),
                                    _("Editar"), value=self.segmentos[idx], multiline=True)
        if dlg.ShowModal() == wx.ID_OK:
            self.segmentos[idx] = dlg.GetValue().strip()
            self._render()
        dlg.Destroy()

    def _on_publicar(self, event):
        if not self.segmentos:
            self._on_dividir(None)
        if not self.segmentos:
            return
        largos = [i + 1 for i, s in enumerate(self.segmentos) if len(s) > LIMITE_CARACTERES]
        if largos:
            mostrar_info(self, _("Las publicaciones {0} pasan de 300 caracteres. Edítalas.")
                         .format(largos), _("Demasiado largo"))
            return

        if self.cmb_destino.GetSelection() == 1:
            destinatario = self.txt_destinatario.GetValue().strip().lstrip("@")
            if not destinatario:
                mostrar_info(self, _("Escribe la cuenta a la que enviar el hilo."),
                             _("Falta el destinatario"))
                self.txt_destinatario.SetFocus()
                return
            n = len(self.segmentos)

            def _cb_dm(ok, res):
                if ok:
                    notificar(_("Hilo enviado a {0}: {1} mensajes.").format(
                        (res or {}).get("title") or destinatario, (res or {}).get("enviados", n)))
                else:
                    notificar(_("No se pudo enviar: {0}").format(res))
            wsocial_service.enviar_hilo_dm_async(self.account_id, destinatario, self.segmentos,
                                                 callback=_cb_dm)
            speak(_("Enviando el hilo por mensaje directo..."), interrupt=True)
            self.EndModal(wx.ID_OK)
            return

        def _cb(ok, res):
            if ok:
                notificar(_("Hilo publicado: {0} publicaciones.").format(len(res or [])))
            else:
                notificar(_("No se pudo publicar el hilo: {0}").format(res))
        wsocial_service.publicar_hilo_async(
            self.account_id, self.segmentos,
            reply_control=self._reply_vals[self.cmb_reply.GetSelection()], callback=_cb)
        speak(_("Publicando el hilo..."), interrupt=True)
        self.EndModal(wx.ID_OK)


# ======================================================================
# VISOR DE HILOS
# ======================================================================
class WSocialThreadViewerDialog(wx.Dialog):
    """Conversación completa en una lista navegable, con las acciones de cada publicación."""

    def __init__(self, parent, account_id, uri):
        super().__init__(parent, title=_("Conversación de W Social"), size=(760, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.uri = uri
        self.posts = []
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                        name=_("Publicaciones de la conversación"))
        self.lista.InsertColumn(0, _("Cuenta"), width=180)
        self.lista.InsertColumn(1, _("Contenido"), width=420)
        self.lista.InsertColumn(2, _("Fecha"), width=120)
        self.lista.InsertColumn(3, _("En respuesta a"), width=200)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, accion in ((_("&Responder"), self._on_responder),
                                 (_("&Me gusta"), self._on_like),
                                 (_("Re&publicar"), self._on_repost),
                                 (_("&Abrir como buffer"), self._on_buffer)):
            b = wx.Button(panel, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, accion)
            fila.Add(b, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.ALIGN_CENTER)

        cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerra&r"))
        s.Add(cerrar, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._cargar()

    def _cargar(self):
        speak(_("Cargando la conversación..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo cargar: {0}").format(res))
                return
            def _pintar():
                from app.ui.main_frame import formatear_tiempo_relativo
                if not self:
                    return
                self.posts = res
                self.lista.DeleteAllItems()
                self.lista.limpiar_lecturas()
                # A quién responde cada publicación, dentro de esta misma conversación. Si el
                # padre no llegó (borrado o bloqueado) se dice, en vez de dejar la respuesta suelta.
                autores = {p.uri: (p.author_handle or p.author_name or "") for p in res}
                for i, p in enumerate(res):
                    padre = getattr(p, "reply_parent_uri", None) or ""
                    if not padre:
                        respuesta = ""
                    elif autores.get(padre):
                        respuesta = _("En respuesta a @{0}").format(autores[padre])
                    else:
                        respuesta = _("En respuesta a una publicación no disponible")
                    quien = p.author_name or p.author_handle
                    pos = self.lista.InsertItem(i, quien)
                    self.lista.SetItem(pos, 1, (p.content or "").replace("\n", " "))
                    self.lista.SetItem(pos, 2, formatear_tiempo_relativo(p.indexed_at)
                                       if p.indexed_at else "")
                    self.lista.SetItem(pos, 3, respuesta)
                    if respuesta:
                        quien = f"{quien}, {respuesta[:1].lower()}{respuesta[1:]}"
                    self.lista.establecer_lectura(pos, f"{quien}. {p.content}")
                if res:
                    # Foco en la publicación desde la que se abrió (su padre queda encima).
                    inicio = next((i for i, p in enumerate(res) if p.uri == self.uri), 0)
                    self.lista.Focus(inicio)
                    self.lista.Select(inicio)
                    self.lista.EnsureVisible(inicio)
                speak(_("{0} publicaciones en la conversación.").format(len(res)), interrupt=True)
            wx.CallAfter(_pintar)
        wsocial_service.obtener_hilo_async(self.account_id, self.uri, callback=_cb)

    def _post_seleccionado(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self.posts):
            speak(_("Selecciona primero una publicación."), interrupt=True)
            return None
        return self.posts[idx]

    def _on_responder(self, event):
        p = self._post_seleccionado()
        if not p:
            return
        from .wsocial_controller_acciones import texto_inicial_respuesta
        dlg = WSocialComposeDialog(self, self.account_id, reply_uri=p.uri,
                                   texto_inicial=texto_inicial_respuesta(
                                       self.account_id, p.author_handle, p.content))
        dlg.ShowModal()
        dlg.Destroy()

    def _on_like(self, event):
        p = self._post_seleccionado()
        if p:
            wsocial_service.ejecutar_accion_post_async(
                self.account_id, p.id, "like",
                callback=lambda ok, r: speak(_("Me gusta.") if ok else str(r), interrupt=True))

    def _on_repost(self, event):
        p = self._post_seleccionado()
        if p:
            wsocial_service.ejecutar_accion_post_async(
                self.account_id, p.id, "repost",
                callback=lambda ok, r: speak(_("Republicada.") if ok else str(r), interrupt=True))

    def _on_buffer(self, event):
        p = self._post_seleccionado()
        if not p:
            return
        frame = wx.GetApp().GetTopWindow()
        if frame and getattr(frame, "wsocial_ctrl", None):
            frame.wsocial_ctrl.abrir_como_buffer(p.id)
            self.EndModal(wx.ID_CANCEL)


# ======================================================================
# CONVERSACIONES (MENSAJES DIRECTOS Y GRUPOS)
# ======================================================================
class WSocialConversationDialog(wx.Dialog):
    """Ventana de una conversación: mensajes, envío, reacciones y administración del grupo."""

    def __init__(self, parent, account_id, convo_id, titulo="", es_grupo=False):
        super().__init__(parent, title=_("Conversación: {0}").format(titulo or ""),
                         size=(720, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.convo_id = convo_id
        self.es_grupo = es_grupo
        self.mensajes = []
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)

        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                        name=_("Mensajes de la conversación"))
        self.lista.InsertColumn(0, _("De"), width=160)
        self.lista.InsertColumn(1, _("Mensaje"), width=420)
        self.lista.InsertColumn(2, _("Fecha"), width=120)
        self.lista.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, self._on_menu_mensaje)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("&Escribe tu mensaje (Ctrl+Intro envía):")),
              0, wx.LEFT | wx.TOP, 5)
        self.txt = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, size=(680, 70),
                                      name=_("Mensaje a enviar"))
        self.txt.Bind(wx.EVT_KEY_DOWN, self._on_tecla)
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_enviar = wx.Button(panel, label=_("&Enviar"))
        btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(btn_enviar, 0, wx.ALL, 4)
        btn_mas = wx.Button(panel, label=_("Cargar mensajes &anteriores"))
        btn_mas.Bind(wx.EVT_BUTTON, lambda e: self._cargar(cargar_mas=True))
        fila.Add(btn_mas, 0, wx.ALL, 4)
        if es_grupo:
            btn_grupo = wx.Button(panel, label=_("&Participantes..."))
            btn_grupo.Bind(wx.EVT_BUTTON, self._on_participantes)
            fila.Add(btn_grupo, 0, wx.ALL, 4)
        btn_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerra&r"))
        fila.Add(btn_cerrar, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.ALIGN_CENTER)

        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._cargar()
        # Al abrir la conversación se marca como leída, como en cualquier aplicación de mensajería.
        wsocial_service.accion_convo_async(account_id, convo_id, "leer")

    def _cargar(self, cargar_mas=False):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar los mensajes: {0}").format(res))
                return
            def _pintar():
                from app.ui.main_frame import formatear_tiempo_relativo
                self.mensajes = res
                self.lista.DeleteAllItems()
                self.lista.limpiar_lecturas()
                for i, m in enumerate(res):
                    quien = _("Tú") if m.get("outgoing") else (m.get("sender_name") or "")
                    pos = self.lista.InsertItem(i, quien)
                    self.lista.SetItem(pos, 1, (m.get("text") or "").replace("\n", " "))
                    self.lista.SetItem(pos, 2, formatear_tiempo_relativo(m.get("sent_at", ""))
                                       if m.get("sent_at") else "")
                    reacciones = m.get("reacciones") or []
                    lectura = f"{quien}. {m.get('text', '')}"
                    if reacciones:
                        lectura += _(". Reacciones: {0}").format(
                            " ".join(r.get("emoji", "") for r in reacciones))
                    self.lista.establecer_lectura(pos, lectura)
                n = self.lista.GetItemCount()
                if n:
                    self.lista.Focus(n - 1)
                    self.lista.Select(n - 1)
                    self.lista.EnsureVisible(n - 1)
            wx.CallAfter(_pintar)
        wsocial_service.cargar_mensajes_async(self.account_id, self.convo_id,
                                              cargar_mas=cargar_mas, callback=_cb)

    def _on_tecla(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and event.ControlDown():
            self._on_enviar(None)
            return
        event.Skip()

    def _on_enviar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            return

        def _cb(ok, res):
            if ok:
                wx.CallAfter(self.txt.Clear)
                wx.CallAfter(self._cargar)
                speak(_("Enviado."), interrupt=True)
            else:
                notificar(_("No se pudo enviar: {0}").format(res))
        wsocial_service.enviar_mensaje_async(self.account_id, self.convo_id, texto, callback=_cb)

    def _on_menu_mensaje(self, event):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self.mensajes):
            return
        mensaje = self.mensajes[idx]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Reaccionar..."))
        self.Bind(wx.EVT_MENU, lambda e: self._reaccionar(mensaje), it)
        it = menu.Append(wx.ID_ANY, _("Copiar el texto"))
        self.Bind(wx.EVT_MENU, lambda e: self._copiar(mensaje), it)
        it = menu.Append(wx.ID_ANY, _("Borrar el mensaje para mí"))
        self.Bind(wx.EVT_MENU, lambda e: self._borrar(mensaje), it)
        if not mensaje.get("outgoing"):
            it = menu.Append(wx.ID_ANY, _("Denunciar el mensaje..."))
            self.Bind(wx.EVT_MENU, lambda e: self._denunciar(mensaje), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _reaccionar(self, mensaje):
        emoji = ""
        dlg = CustomTextEntryDialog(self, _("Escribe el emoji de la reacción:"), _("Reaccionar"))
        if dlg.ShowModal() == wx.ID_OK:
            emoji = dlg.GetValue().strip()
        dlg.Destroy()
        if emoji:
            wsocial_service.reaccionar_mensaje_async(
                self.account_id, self.convo_id, mensaje["id"], emoji,
                callback=lambda ok, r: (speak(_("Reacción enviada."), interrupt=True),
                                        wx.CallAfter(self._cargar)) if ok else notificar(str(r)))

    def _copiar(self, mensaje):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(mensaje.get("text", "")))
                wx.TheClipboard.Close()
                speak(_("Texto copiado."), interrupt=True)
        except Exception:
            pass

    def _borrar(self, mensaje):
        wsocial_service.borrar_mensaje_async(
            self.account_id, self.convo_id, mensaje["id"],
            callback=lambda ok, r: wx.CallAfter(self._cargar) if ok else notificar(str(r)))

    def _denunciar(self, mensaje):
        from app.services.wsocial.wsocial_moderacion import MOTIVOS_DENUNCIA
        etiquetas = list(MOTIVOS_DENUNCIA.values())
        sel = wx.GetSingleChoice(_("¿Por qué lo denuncias?"), _("Denunciar el mensaje"),
                                 etiquetas, self)
        if not sel:
            return
        motivo = [k for k, v in MOTIVOS_DENUNCIA.items() if v == sel][0]
        wsocial_service.denunciar_mensaje_async(
            self.account_id, self.convo_id, mensaje["id"], motivo,
            callback=lambda ok, r: notificar(_("Denuncia enviada.") if ok else str(r)))

    def _on_participantes(self, event):
        dlg = WSocialGroupMembersDialog(self, self.account_id, self.convo_id)
        dlg.ShowModal()
        dlg.Destroy()


# ======================================================================
# PERFIL
# ======================================================================
class WSocialProfileDialog(wx.Dialog):
    """Ficha de una cuenta con sus acciones."""

    def __init__(self, parent, account_id, actor):
        super().__init__(parent, title=_("Perfil de W Social"), size=(620, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.actor = actor
        self.datos = {}
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.txt = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      size=(600, 280), name=_("Datos del perfil"))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_seguir = wx.Button(panel, label=_("&Seguir"))
        self.btn_seguir.Bind(wx.EVT_BUTTON, self._on_seguir)
        fila.Add(self.btn_seguir, 0, wx.ALL, 4)
        for etiqueta, accion in ((_("Ver sus &publicaciones"), self._on_publicaciones),
                                 (_("&Mensaje directo"), self._on_dm),
                                 (_("Si&lenciar"), self._on_silenciar),
                                 (_("&Bloquear"), self._on_bloquear),
                                 (_("&Denunciar..."), self._on_denunciar)):
            b = wx.Button(panel, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, accion)
            fila.Add(b, 0, wx.ALL, 4)
            if accion == self._on_silenciar:
                self.btn_silenciar = b
            elif accion == self._on_bloquear:
                self.btn_bloquear = b
        s.Add(fila, 0, wx.ALIGN_CENTER)
        cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerra&r"))
        s.Add(cerrar, 0, wx.ALIGN_CENTER | wx.ALL, 5)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._cargar()

    def _cargar(self):
        speak(_("Cargando el perfil..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo cargar el perfil: {0}").format(res))
                return
            wx.CallAfter(self._pintar, res)
        wsocial_service.obtener_perfil_async(self.account_id, self.actor, callback=_cb)

    def _pintar(self, res):
        self.datos = res
        lineas = [
            _("Nombre: {0}").format(res.get("display_name") or res.get("handle")),
            _("Usuario: @{0}").format(res.get("handle", "")),
            _("Identificador: {0}").format(res.get("did", "")),
            "",
            res.get("description", ""),
            "",
            _("Seguidores: {0}").format(res.get("followers", 0)),
            _("Siguiendo: {0}").format(res.get("following", 0)),
            _("Publicaciones: {0}").format(res.get("posts", 0)),
            _("Cuenta verificada: {0}").format(_("Sí") if res.get("verificado") else _("No")),
            _("La sigues: {0}").format(_("Sí") if res.get("following_uri") else _("No")),
            _("Te sigue: {0}").format(_("Sí") if res.get("followed_by") else _("No")),
        ]
        if res.get("etiquetas"):
            lineas.append(_("Etiquetas de moderación: {0}").format(
                ", ".join(res["etiquetas"])))
        self.txt.SetValue("\n".join(str(l) for l in lineas))
        self.btn_seguir.SetLabel(_("Dejar de &seguir") if res.get("following_uri")
                                 else _("&Seguir"))
        self.btn_silenciar.SetLabel(_("Quitar si&lencio") if res.get("muted")
                                    else _("Si&lenciar"))
        self.btn_bloquear.SetLabel(_("Des&bloquear") if res.get("blocking_uri")
                                   else _("&Bloquear"))

    def _tras_accion(self, accion):
        """Callback de seguir/silenciar/bloquear: confirma con el estado real que devuelve el
        servidor y repinta la ficha con él (sin volver a anunciar "Cargando el perfil...")."""
        def _cb(ok, res):
            if not ok:
                notificar(str(res))
                return
            from .wsocial_controller_social import mensaje_estado_cuenta
            notificar(mensaje_estado_cuenta(accion, res))
            datos = dict(self.datos)
            for clave in ("following_uri", "blocking_uri", "muted"):
                if clave in res:
                    datos[clave] = res[clave]
            wx.CallAfter(self._pintar, datos)
        return _cb

    def _ctrl(self):
        frame = wx.GetApp().GetTopWindow()
        return getattr(frame, "wsocial_ctrl", None)

    def _on_seguir(self, event):
        accion = "unfollow" if self.datos.get("following_uri") else "follow"
        wsocial_service.accion_cuenta_async(
            self.account_id, self.datos.get("did") or self.actor, accion,
            callback=self._tras_accion(accion))

    def _on_publicaciones(self, event):
        ctrl = self._ctrl()
        if ctrl:
            ctrl.crear_buffer_usuario(self.datos.get("did") or self.actor,
                                      self.datos.get("handle", ""))
            self.EndModal(wx.ID_CANCEL)

    def _on_dm(self, event):
        ctrl = self._ctrl()
        if ctrl:
            self.EndModal(wx.ID_CANCEL)
            wx.CallAfter(ctrl.mensaje_directo_a, self.datos.get("did") or self.actor)

    def _on_silenciar(self, event):
        accion = "unmute" if self.datos.get("muted") else "mute"
        wsocial_service.accion_cuenta_async(
            self.account_id, self.datos.get("did") or self.actor, accion,
            callback=self._tras_accion(accion))

    def _on_bloquear(self, event):
        bloqueada = bool(self.datos.get("blocking_uri"))
        if not bloqueada and confirmar(
                self, _("¿Seguro que quieres bloquear a @{0}? Dejaréis de veros.").format(
                    self.datos.get("handle", "")), _("Bloquear")) != wx.ID_YES:
            return
        accion = "unblock" if bloqueada else "block"
        wsocial_service.accion_cuenta_async(
            self.account_id, self.datos.get("did") or self.actor, accion,
            callback=self._tras_accion(accion))

    def _on_denunciar(self, event):
        ctrl = self._ctrl()
        if ctrl:
            ctrl.denunciar(did=self.datos.get("did") or self.actor)


# ======================================================================
# PAQUETE DE INICIO
# ======================================================================
class WSocialStarterPackDialog(wx.Dialog):
    """Contenido de un paquete de inicio, con la opción de seguir a todas sus cuentas."""

    def __init__(self, parent, account_id, datos):
        super().__init__(parent, title=_("Paquete de inicio: {0}").format(datos.get("name", "")),
                         size=(640, 480), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.datos = datos
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=datos.get("description", "") or ""), 0, wx.ALL, 10)
        s.Add(wx.StaticText(panel, label=_("Cuentas del paquete:")), 0, wx.LEFT, 10)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                        name=_("Cuentas del paquete"))
        self.lista.InsertColumn(0, _("Nombre"), width=240)
        self.lista.InsertColumn(1, _("Usuario"), width=280)
        for i, c in enumerate(datos.get("cuentas", [])):
            pos = self.lista.InsertItem(i, c.get("display_name") or c.get("handle", ""))
            self.lista.SetItem(pos, 1, "@" + c.get("handle", ""))
            self.lista.establecer_lectura(pos, f"{c.get('display_name') or c.get('handle')}. "
                                               f"@{c.get('handle')}")
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_seguir = wx.Button(panel, label=_("&Seguir a la seleccionada"))
        btn_seguir.Bind(wx.EVT_BUTTON, self._on_seguir)
        fila.Add(btn_seguir, 0, wx.ALL, 4)
        btn_todas = wx.Button(panel, label=_("Seguir a &todas"))
        btn_todas.Bind(wx.EVT_BUTTON, self._on_seguir_todas)
        fila.Add(btn_todas, 0, wx.ALL, 4)
        btn_cerrar = wx.Button(panel, wx.ID_CANCEL, _("Cerra&r"))
        fila.Add(btn_cerrar, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)

    def _on_seguir(self, event):
        idx = self.lista.GetFirstSelected()
        cuentas = self.datos.get("cuentas", [])
        if idx < 0 or idx >= len(cuentas):
            speak(_("Selecciona primero una cuenta."), interrupt=True)
            return
        wsocial_service.accion_cuenta_async(
            self.account_id, cuentas[idx].get("did"), "follow",
            callback=lambda ok, r: speak(_("Ahora la sigues.") if ok else str(r), interrupt=True))

    def _on_seguir_todas(self, event):
        cuentas = self.datos.get("cuentas", [])
        if not cuentas:
            return
        if confirmar(self, _("¿Seguir a las {0} cuentas del paquete?").format(len(cuentas)),
                     _("Seguir a todas")) != wx.ID_YES:
            return
        for c in cuentas:
            wsocial_service.accion_cuenta_async(self.account_id, c.get("did"), "follow")
        notificar(_("Siguiendo a {0} cuentas.").format(len(cuentas)))


# Los diálogos de gestión (listas, grupos, moderación, cuenta, salida pendiente y búsqueda) viven
# en su propio módulo para no tener un archivo enorme; se reexportan aquí para que el resto del
# código los importe siempre desde el mismo sitio.
from .wsocial_dialogs_gestion import (          # noqa: E402  (import al final a propósito)
    WSocialListsDialog, WSocialListMembersDialog, WSocialGroupMembersDialog,
    WSocialMutedWordsDialog, WSocialContentPrefsDialog, WSocialLabelersDialog,
    WSocialAppPasswordsDialog, WSocialProfileEditDialog, WSocialOutboxDialog,
    WSocialSearchDialog)
