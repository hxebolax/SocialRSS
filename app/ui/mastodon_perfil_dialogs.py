# -*- coding: utf-8 -*-
"""Diálogos de ver publicación, solicitudes, perfil y publicaciones de usuario.
"""
import logging
import wx
import wx.adv

from app.core.speech import speak, notificar
from app.core.database import db
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl


log = logging.getLogger(__name__)

class VerPublicacionDialog(wx.Dialog):
    """Muestra una publicación (o mensaje directo) COMPLETA en solo lectura y permite interactuar
    con TODAS las acciones disponibles para ese tipo de publicación mediante el botón 'Acciones',
    que abre exactamente el mismo menú contextual de la lista (responder, citar, favorito,
    marcador, impulsar, ver conversación, traducir, ver detalles del autor, reportar, etc.; las
    que no aplican al tipo —p. ej. citar/impulsar en un directo— no aparecen). Las acciones las
    ejecuta el frame sobre la publicación que se está viendo (la enfocada en la lista)."""

    def __init__(self, frame, titulo, cabecera, cuerpo, db_id=None):
        super().__init__(frame, title=titulo, size=(580, 470),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self._frame = frame
        self._db_id = db_id
        self._cabecera = cabecera or ""
        self._cuerpo = cuerpo or ""
        # Línea de estadísticas del servidor: va DENTRO del cuadro de texto (entre cabecera y
        # cuerpo) para que se pueda leer navegando con el lector, no en una etiqueta aparte que casi
        # no se oye. Empieza "obteniéndose" y se actualiza cuando responde el servidor.
        self._stats = _("(obteniendo del servidor...)")
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Publicación (usa las flechas para leerla):")), 0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(self, value="",
                                      style=wx.TE_MULTILINE | wx.TE_READONLY,
                                      name=_("Contenido de la publicación"))
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_acciones = wx.Button(self, label=_("&Acciones..."))
        self.btn_acciones.Bind(wx.EVT_BUTTON, self._on_acciones)
        btns.Add(self.btn_acciones, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        # Tecla Aplicaciones / Mayús+F10 sobre el texto: abrir también el menú de acciones.
        self.txt.Bind(wx.EVT_CONTEXT_MENU, self._on_acciones)
        # Los items del menú de acciones están LIGADOS AL FRAME (es su menú contextual). Al
        # mostrarse sobre este diálogo, sus EVT_MENU llegan AQUÍ y no se ejecutaban solos (por eso
        # "Responder", etc. no hacían nada). Los reenviamos al frame, donde están los handlers.
        self.Bind(wx.EVT_MENU, self._reenviar_menu_al_frame)
        self._render()
        self.txt.SetFocus()
        self.txt.SetInsertionPoint(0)
        self.CenterOnParent()
        self._pedir_estadisticas_servidor()

    def _render(self):
        """Vuelca al cuadro de texto: cabecera + línea de estadísticas + cuerpo. Conserva el punto
        de inserción para no desorientar al lector cuando se actualizan las estadísticas."""
        if not self:
            return
        try:
            pos = self.txt.GetInsertionPoint()
        except Exception:
            pos = 0
        texto = "{cab}\n{etq} {st}\n\n{cuerpo}".format(
            cab=self._cabecera, etq=_("Estadísticas:"), st=self._stats, cuerpo=self._cuerpo)
        self.txt.SetValue(texto)
        try:
            self.txt.SetInsertionPoint(min(pos, self.txt.GetLastPosition()))
        except Exception:
            pass

    def _pedir_estadisticas_servidor(self):
        """Pide al servidor los datos al día (contadores, idioma, edición) y los integra en el
        texto. No bloquea; si falla, se indica que no se pudieron obtener."""
        acc = getattr(self._frame, "active_mastodon_account_id", None)
        if not self._db_id or not acc:
            self._stats = ""
            self._render()
            return

        def _fin(res, err):
            if not self:
                return
            if err or not res:
                self._stats = _("(no se pudieron obtener)")
            else:
                partes = [
                    _("{0} favoritos").format(res.get("favourites", 0)),
                    _("{0} impulsos").format(res.get("reblogs", 0)),
                    _("{0} respuestas").format(res.get("replies", 0)),
                ]
                if res.get("language"):
                    partes.append(_("idioma {0}").format(res["language"]))
                if res.get("edited_at"):
                    partes.append(_("editada"))
                if res.get("pinned"):
                    partes.append(_("fijada"))
                self._stats = ", ".join(partes) + "."
            self._render()

        mastodon_service.obtener_status_servidor_async(acc, self._db_id, callback=_fin)

    def _reenviar_menu_al_frame(self, event):
        """Reenvía la acción del menú (clic en una opción) al frame, donde están sus handlers."""
        try:
            if self._frame and self._frame.GetEventHandler().ProcessEvent(event):
                return
        except Exception as e:
            log.error("No se pudo reenviar la acción del menú al frame: %s", e)
        event.Skip()

    def _on_acciones(self, event):
        # Reaprovecha el menú contextual de la lista, mostrado sobre este diálogo y actuando sobre
        # ESTA publicación (resuelta por su db_id, no por el elemento enfocado en la lista).
        try:
            self._frame._abrir_menu_publicacion(self, self._db_id)
        except Exception as e:
            log.error("No se pudo abrir el menú de acciones de la publicación: %s", e)


class SolicitudesSeguimientoDialog(wx.Dialog):
    """Lista accesible de solicitudes de seguimiento pendientes (cuentas privadas), con
    botones para aceptarlas o rechazarlas (como FastSM/TWBlue)."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Solicitudes de seguimiento"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Solicitudes de seguimiento"))
        self.account_id = account_id
        self.solicitudes = []

        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Cuentas que solicitan seguirte:")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de solicitudes de seguimiento"))
        self.lista.InsertColumn(0, _("Nombre"), width=220)
        self.lista.InsertColumn(1, _("Usuario"), width=280)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_aceptar = wx.Button(self, label=_("&Aceptar solicitud"), name=_("Aceptar la solicitud seleccionada"))
        self.btn_aceptar.Bind(wx.EVT_BUTTON, lambda e: self._responder(True))
        self.btn_rechazar = wx.Button(self, label=_("&Rechazar solicitud"), name=_("Rechazar la solicitud seleccionada"))
        self.btn_rechazar.Bind(wx.EVT_BUTTON, lambda e: self._responder(False))
        self.btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"))
        for b in (self.btn_aceptar, self.btn_rechazar, self.btn_cerrar):
            btns.Add(b, 0, wx.ALL, 5)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)

        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        speak(_("Cargando solicitudes de seguimiento..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error al cargar las solicitudes: {0}").format(err))
                return
            self.solicitudes = res or []
            self.lista.DeleteAllItems()
            self.lista.limpiar_lecturas()
            for i, c in enumerate(self.solicitudes):
                pos = self.lista.InsertItem(i, c.get("display", ""))
                self.lista.SetItem(pos, 1, "@" + c.get("acct", ""))
                self.lista.establecer_lectura(pos, "{0}, @{1}".format(c.get("display", ""), c.get("acct", "")))
            if not self.solicitudes:
                speak(_("No tienes solicitudes de seguimiento pendientes."), interrupt=True)
            else:
                speak(_("{0} solicitudes de seguimiento.").format(len(self.solicitudes)), interrupt=True)
                self.lista.Focus(0)
                self.lista.Select(0)
                self.lista.SetFocus()

        mastodon_service.obtener_solicitudes_seguimiento_async(self.account_id, callback=_cb)

    def _responder(self, aceptar):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.solicitudes):
            speak(_("Seleccione una solicitud de la lista."), interrupt=True)
            return
        c = self.solicitudes[idx]

        def _cb(res, err):
            if err:
                notificar(_("No se pudo completar: {0}").format(err))
                return
            speak(_("Solicitud aceptada.") if aceptar else _("Solicitud rechazada."), interrupt=True)
            try:
                self.lista.eliminar_fila(idx)
                del self.solicitudes[idx]
            except Exception:
                pass

        mastodon_service.responder_solicitud_seguimiento_async(
            self.account_id, c.get("id"), aceptar, callback=_cb)



class PerfilMastodonDialog(wx.Dialog):
    """Editar el perfil propio de Mastodon: nombre, biografía, opciones, campos de
    metadatos y avatar/banner (account_update_credentials)."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Editar mi perfil de Mastodon"), size=(560, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Editar mi perfil de Mastodon"))
        self.account_id = account_id
        self._avatar_path = None
        self._header_path = None

        panel = wx.ScrolledWindow(self)
        panel.SetScrollRate(0, 10)
        s = wx.BoxSizer(wx.VERTICAL)

        s.Add(wx.StaticText(panel, label=_("Nombre que se muestra:")), 0, wx.ALL, 5)
        self.txt_nombre = AccessibleTextCtrl(panel, name=_("Nombre que se muestra"))
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(panel, label=_("Biografía:")), 0, wx.ALL, 5)
        self.txt_bio = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, name=_("Biografía"))
        s.Add(self.txt_bio, 1, wx.EXPAND | wx.ALL, 5)

        self.cb_locked = wx.CheckBox(panel, label=_("Cuenta privada (aprobar seguidores manualmente)"), name=_("Cuenta privada"))
        s.Add(self.cb_locked, 0, wx.ALL, 5)
        self.cb_bot = wx.CheckBox(panel, label=_("Marcar la cuenta como bot"), name=_("Cuenta de bot"))
        s.Add(self.cb_bot, 0, wx.ALL, 5)
        self.cb_disc = wx.CheckBox(panel, label=_("Aparecer en el directorio de perfiles"), name=_("Visible en el directorio"))
        s.Add(self.cb_disc, 0, wx.ALL, 5)

        self.cb_indexable = wx.CheckBox(panel, label=_("Permitir que cualquiera busque mis publicaciones públicas"),
                                        name=_("Publicaciones públicas buscables"))
        s.Add(self.cb_indexable, 0, wx.ALL, 5)
        self.cb_ocultar_red = wx.CheckBox(panel, label=_("Ocultar a quién sigo y quién me sigue"),
                                          name=_("Ocultar seguidos y seguidores"))
        s.Add(self.cb_ocultar_red, 0, wx.ALL, 5)

        # Valores por defecto al publicar (se guardan en el servidor: valen para cualquier
        # aplicación con la que publiques).
        s.Add(wx.StaticText(panel, label=_("Visibilidad por defecto de tus publicaciones:")), 0, wx.ALL, 5)
        self.choice_privacidad = wx.Choice(panel, choices=[
            _("Pública"), _("No listada (pública silenciosa)"), _("Solo seguidores"),
            _("Mención directa (solo los mencionados)")], name=_("Visibilidad por defecto"))
        s.Add(self.choice_privacidad, 0, wx.EXPAND | wx.ALL, 5)
        self.cb_sensible = wx.CheckBox(panel, label=_("Marcar siempre la multimedia como sensible"),
                                       name=_("Multimedia sensible por defecto"))
        s.Add(self.cb_sensible, 0, wx.ALL, 5)
        s.Add(wx.StaticText(panel, label=_("Idioma por defecto de tus publicaciones (código, p. ej. es):")), 0, wx.ALL, 5)
        self.txt_idioma = AccessibleTextCtrl(
            panel, name=_("Idioma por defecto"),
            ayuda_texto=_("Código de idioma de dos letras (es, en, ca...). Vacío: sin idioma por defecto."))
        s.Add(self.txt_idioma, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(panel, label=_("Quién puede citar tus publicaciones, por defecto:")), 0, wx.ALL, 5)
        self.choice_citas = wx.Choice(panel, choices=[
            _("Cualquiera"), _("Solo tus seguidores"), _("Solo tú (nadie más)")],
            name=_("Política de citas por defecto"))
        s.Add(self.choice_citas, 0, wx.EXPAND | wx.ALL, 5)

        # Campos de metadatos: tantas filas como tengas (mínimo 4) y más hasta el máximo de tu
        # servidor (configurable desde Mastodon 4.6; antes siempre 4).
        s.Add(wx.StaticText(panel, label=_("Campos de metadatos: nombre y valor")), 0, wx.ALL, 5)
        self._panel = panel
        self._max_campos = 4
        self._sizer_campos = wx.BoxSizer(wx.VERTICAL)
        s.Add(self._sizer_campos, 0, wx.EXPAND)
        self._campos = []
        for _i in range(4):
            self._anadir_fila_campo()
        self.btn_mas_campos = wx.Button(panel, label=_("Añadir otro &campo"),
                                        name=_("Añadir otro campo de metadatos"))
        self.btn_mas_campos.Bind(wx.EVT_BUTTON, self._on_mas_campos)
        s.Add(self.btn_mas_campos, 0, wx.ALL, 5)

        # Texto alternativo del avatar y de la cabecera (Mastodon 4.6): lo que oye quien usa un
        # lector de pantalla al llegar a tu perfil.
        s.Add(wx.StaticText(panel, label=_("Descripción de tu avatar (texto alternativo):")), 0, wx.ALL, 5)
        self.txt_desc_avatar = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 50),
                                                  name=_("Descripción del avatar"))
        s.Add(self.txt_desc_avatar, 0, wx.EXPAND | wx.ALL, 5)
        s.Add(wx.StaticText(panel, label=_("Descripción de tu banner (texto alternativo):")), 0, wx.ALL, 5)
        self.txt_desc_banner = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 50),
                                                  name=_("Descripción del banner"))
        s.Add(self.txt_desc_banner, 0, wx.EXPAND | wx.ALL, 5)

        btn_av = wx.Button(panel, label=_("Cambiar &avatar..."), name=_("Elegir imagen de avatar"))
        btn_av.Bind(wx.EVT_BUTTON, lambda e: self._elegir_imagen("avatar"))
        s.Add(btn_av, 0, wx.ALL, 5)
        btn_hd = wx.Button(panel, label=_("Cambiar &banner..."), name=_("Elegir imagen de banner"))
        btn_hd.Bind(wx.EVT_BUTTON, lambda e: self._elegir_imagen("header"))
        s.Add(btn_hd, 0, wx.ALL, 5)
        btn_qav = wx.Button(panel, label=_("Quitar avatar"), name=_("Quitar la foto de perfil"))
        btn_qav.Bind(wx.EVT_BUTTON, lambda e: self._quitar_imagen("avatar"))
        s.Add(btn_qav, 0, wx.ALL, 5)
        btn_qhd = wx.Button(panel, label=_("Quitar banner"), name=_("Quitar la imagen de cabecera"))
        btn_qhd.Bind(wx.EVT_BUTTON, lambda e: self._quitar_imagen("header"))
        s.Add(btn_qhd, 0, wx.ALL, 5)

        panel.SetSizer(s)

        cont = wx.BoxSizer(wx.VERTICAL)
        cont.Add(panel, 1, wx.EXPAND)
        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_guardar = wx.Button(self, wx.ID_OK, _("&Guardar"))
        self.btn_guardar.Bind(wx.EVT_BUTTON, self._on_guardar)
        self.btn_guardar.SetDefault()
        btns.Add(self.btn_guardar, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        cont.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(cont)
        self.CenterOnParent()
        self._cargar()

    _PRIVACIDADES = ("public", "unlisted", "private", "direct")
    _POLITICAS_CITA = ("public", "followers", "nobody")

    def _anadir_fila_campo(self):
        i = len(self._campos)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        n = AccessibleTextCtrl(self._panel, name=_("Nombre del campo {0}").format(i + 1))
        v = AccessibleTextCtrl(self._panel, name=_("Valor del campo {0}").format(i + 1))
        fila.Add(n, 1, wx.EXPAND | wx.RIGHT, 4)
        fila.Add(v, 2, wx.EXPAND)
        self._sizer_campos.Add(fila, 0, wx.EXPAND | wx.ALL, 3)
        self._campos.append((n, v))
        return n

    def _on_mas_campos(self, event):
        if len(self._campos) >= self._max_campos:
            speak(_("Tu servidor admite como máximo {0} campos.").format(self._max_campos), interrupt=True)
            return
        n = self._anadir_fila_campo()
        self._panel.FitInside()
        self._panel.Layout()
        self.btn_mas_campos.Enable(len(self._campos) < self._max_campos)
        n.SetFocus()

    def _cargar(self):
        speak(_("Cargando tu perfil..."), interrupt=True)

        def _cb(res, err):
            if err or not res:
                notificar(_("No se pudo cargar el perfil: {0}").format(err or ""))
                return
            self.txt_nombre.SetValue(res.get("display_name", ""))
            self.txt_bio.SetValue(res.get("note", ""))
            self.cb_locked.SetValue(res.get("locked", False))
            self.cb_bot.SetValue(res.get("bot", False))
            self.cb_disc.SetValue(res.get("discoverable", False))
            campos = res.get("fields", []) or []
            self._max_campos = max(int(res.get("max_profile_fields") or 4), len(campos), 4)
            while len(self._campos) < len(campos):
                self._anadir_fila_campo()
            for (n, v), campo in zip(self._campos, campos):
                n.SetValue(campo.get("name", ""))
                v.SetValue(campo.get("value", ""))
            self.btn_mas_campos.Enable(len(self._campos) < self._max_campos)
            self.cb_indexable.SetValue(bool(res.get("indexable")))
            self.cb_ocultar_red.SetValue(bool(res.get("hide_collections")))
            priv = res.get("default_privacy") or "public"
            self.choice_privacidad.SetSelection(
                self._PRIVACIDADES.index(priv) if priv in self._PRIVACIDADES else 0)
            self.cb_sensible.SetValue(bool(res.get("default_sensitive")))
            self.txt_idioma.SetValue(res.get("default_language") or "")
            cita = res.get("default_quote_policy") or "public"
            self.choice_citas.SetSelection(
                self._POLITICAS_CITA.index(cita) if cita in self._POLITICAS_CITA else 0)
            self._admite_citas = bool(res.get("admite_citas"))
            self.choice_citas.Enable(self._admite_citas)
            self._desc_originales = (res.get("avatar_description") or "", res.get("header_description") or "")
            self.txt_desc_avatar.SetValue(self._desc_originales[0])
            self.txt_desc_banner.SetValue(self._desc_originales[1])
            for ctrl in (self.txt_desc_avatar, self.txt_desc_banner):
                ctrl.Enable(bool(res.get("admite_descripciones")))
            self._panel.FitInside()
            self._panel.Layout()
            self.txt_nombre.SetFocus()
        mastodon_service.obtener_mi_perfil_async(self.account_id, callback=_cb)

    def _elegir_imagen(self, cual):
        dlg = wx.FileDialog(self, _("Elegir imagen"),
                            wildcard="Imágenes (*.jpg;*.jpeg;*.png;*.gif)|*.jpg;*.jpeg;*.png;*.gif",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            ruta = dlg.GetPath()
            if cual == "avatar":
                self._avatar_path = ruta
                speak(_("Avatar seleccionado."), interrupt=True)
            else:
                self._header_path = ruta
                speak(_("Banner seleccionado."), interrupt=True)
        dlg.Destroy()

    def _quitar_imagen(self, cual):
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Quitar el avatar?") if cual == "avatar" else _("¿Quitar el banner?"),
                     _("Confirmar")) != wx.ID_YES:
            return
        def _cb(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Avatar quitado.") if cual == "avatar" else _("Banner quitado."))
        if cual == "avatar":
            mastodon_service.quitar_avatar_async(self.account_id, callback=_cb)
        else:
            mastodon_service.quitar_cabecera_async(self.account_id, callback=_cb)

    def _on_guardar(self, event):
        campos = []
        for n, v in self._campos:
            nombre = n.GetValue().strip()
            valor = v.GetValue().strip()
            if nombre or valor:
                campos.append({"name": nombre, "value": valor})
        speak(_("Guardando el perfil..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("No se pudo guardar: {0}").format(err))
            else:
                notificar(_("Perfil actualizado."))
                wx.CallAfter(self.EndModal, wx.ID_OK)
        opciones = {
            "indexable": self.cb_indexable.GetValue(),
            "hide_collections": self.cb_ocultar_red.GetValue(),
            "default_privacy": self._PRIVACIDADES[max(0, self.choice_privacidad.GetSelection())],
            "default_sensitive": self.cb_sensible.GetValue(),
            "default_language": self.txt_idioma.GetValue().strip().lower() or None,
            "default_quote_policy": (self._POLITICAS_CITA[max(0, self.choice_citas.GetSelection())]
                                     if getattr(self, "_admite_citas", False) else None),
        }
        descripciones = None
        if self.txt_desc_avatar.IsEnabled():
            originales = getattr(self, "_desc_originales", ("", ""))
            nuevas = (self.txt_desc_avatar.GetValue().strip(), self.txt_desc_banner.GetValue().strip())
            if nuevas != originales or self._avatar_path or self._header_path:
                descripciones = {"avatar_description": nuevas[0], "header_description": nuevas[1]}
        mastodon_service.editar_mi_perfil_async(
            self.account_id, self.txt_nombre.GetValue().strip(), self.txt_bio.GetValue(),
            locked=self.cb_locked.GetValue(), bot=self.cb_bot.GetValue(),
            discoverable=self.cb_disc.GetValue(), fields=campos,
            avatar_path=self._avatar_path, header_path=self._header_path, callback=_cb,
            opciones=opciones, descripciones=descripciones)


class PublicacionesUsuarioDialog(wx.Dialog):
    """Muestra las publicaciones de un usuario en una LISTA navegable por columnas (como un
    buffer) y permite interactuar con la que tenga el foco, igual que en el timeline."""

    def __init__(self, parent, account_id: int, acct: str):
        super().__init__(parent, title=_("Publicaciones de {0}").format(acct),
                         size=(760, 520), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Publicaciones del usuario"))
        self.frame = parent
        self.account_id = account_id
        self.acct = acct
        self._db_ids = []
        self._pinned = set()

        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Cargando publicaciones de {0}...").format(acct))
        s.Add(self.lbl, 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Publicaciones del usuario"))
        self.lista.InsertColumn(0, _("Autor"), width=170)
        self.lista.InsertColumn(1, _("Contenido"), width=380)
        self.lista.InsertColumn(2, _("Fecha"), width=120)
        self.lista.InsertColumn(3, _("Privacidad"), width=90)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._accion("ver_conversacion"))
        self.lista.Bind(wx.EVT_LIST_ITEM_RIGHT_CLICK, lambda e: self._menu())
        self.lista.Bind(wx.EVT_KEY_DOWN, self._on_key)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b_acc = wx.Button(self, label=_("&Acciones..."))
        b_acc.Bind(wx.EVT_BUTTON, lambda e: self._menu())
        btns.Add(b_acc, 0, wx.ALL, 5)
        btns.AddStretchSpacer()
        btns.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        s.Add(btns, 0, wx.EXPAND | wx.ALL, 5)
        self.SetSizer(s)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        speak(_("Cargando publicaciones de {0}...").format(self.acct), interrupt=True)

        def _fin(res, err):
            if err or not res:
                from app.core.net import humanizar_error
                aviso = humanizar_error(err) if err else _("No se pudieron cargar las publicaciones.")
                self.lbl.SetLabel(aviso)
                speak(aviso, interrupt=True)
                return
            self.lbl.SetLabel(_("Publicaciones de {0} (@{1}):").format(res["display"], res["acct"]))
            self._db_ids = [it["db_id"] for it in res["items"]]
            self._pinned = {it["db_id"] for it in res["items"] if it.get("pinned")}
            self._aplicar_filtros_servidor()
            self._render()
            if self._db_ids:
                self.lista.SetFocus()
                self.lista.Focus(0)
                self.lista.Select(0)
            speak(_("{0} publicaciones. Pulsa la tecla de aplicación o Acciones para interactuar.").format(len(self._db_ids)), interrupt=True)
        mastodon_service.cargar_publicaciones_usuario_async(self.account_id, self.acct, callback=_fin)

    # --- Filtros del servidor (contexto "Perfiles", como la web oficial) ---
    def _ev_filtro(self, db_id):
        from app.services.mastodon import filtros_servidor as fsrv
        return fsrv.evaluar_db_id(db_id, self.account_id, "user:" + str(self.acct or ""))

    def _aplicar_filtros_servidor(self):
        """Quita las publicaciones con un filtro 'ocultar' del contexto Perfiles."""
        from app.services.mastodon import filtros_servidor as fsrv
        fsrv.asegurar_vigentes(self.account_id)
        self._db_ids = [d for d in self._db_ids if self._ev_filtro(d).get("accion") != "hide"]

    def _on_mostrar_filtrado(self):
        from app.services.mastodon import filtros_servidor as fsrv
        db_id = self._toot()
        if not db_id:
            return
        fsrv.revelar(db_id)
        idx = self.lista.GetFocusedItem()
        self._render_fila(idx, db_id)
        self.lista.reanunciar_foco()

    def _render(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, db_id in enumerate(self._db_ids):
            self._render_fila(i, db_id, insertar=True)

    def _render_fila(self, i, db_id, insertar=False):
        from .main_frame import formatear_tiempo_relativo
        from app.services.mastodon import filtros_servidor as fsrv
        vis_map = {"public": _("Público"), "unlisted": _("No listado"),
                   "private": _("Privado"), "direct": _("Mensaje directo")}
        r = db.query_one(
            "SELECT author_name, author_username, content, created_at, visibility, "
            "COALESCE(spoiler_text,''), COALESCE(reblog_by_name,'') FROM mastodon_statuses WHERE id = ?",
            (db_id,))
        if not r:
            r = ("", "", "", "", "", "", "")
        autor, user, texto, fecha, vis, spoiler, rebl_by = r
        autor_col = rebl_by or autor or ""
        cuerpo = (spoiler and _("Advertencia de contenido: {0}").format(spoiler)) or (texto or "").replace("\n", " ")
        # 'Avisar': "[Filtrado: título]" hasta "Mostrar de todos modos".
        cuerpo = fsrv.contenido_con_filtro(cuerpo, self._ev_filtro(db_id), db_id)
        if db_id in self._pinned:
            cuerpo = _("[Fijado] ") + cuerpo
        fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
        vis_t = vis_map.get(vis, vis or "")
        if insertar:
            self.lista.InsertItem(i, autor_col)
        elif not (0 <= i < self.lista.GetItemCount()):
            return
        else:
            self.lista.SetItem(i, 0, autor_col)
        self.lista.SetItem(i, 1, cuerpo)
        self.lista.SetItem(i, 2, fecha_rel)
        self.lista.SetItem(i, 3, vis_t)
        if fecha:
            self.lista.establecer_fecha(i, 2, fecha, fecha_rel)
        self.lista.establecer_lectura(i, "{0} ({1}). {2}. {3}. {4}.".format(autor_col, user or "", cuerpo, fecha_rel, vis_t))

    def _toot(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self._db_ids):
            return None
        return self._db_ids[idx]

    def _accion(self, cual):
        db_id = self._toot()
        if not db_id:
            speak(_("Enfoca primero una publicación."), interrupt=True)
            return
        f = self.frame
        if cual == "responder":
            f._on_responder_toot(None, db_id=db_id)
        elif cual == "citar":
            f._on_citar_toot(None, db_id=db_id)
        elif cual == "favorito":
            f._on_boost_fav_toot(None, db_id=db_id)
        elif cual == "impulsar":
            f._on_impulsar_toot(db_id)
        elif cual == "ver_conversacion":
            f._on_ver_detalles_toot(db_id)
        elif cual == "traducir":
            f._on_traducir_seleccionado(None, db_id=db_id)
        elif cual == "navegador":
            f._on_abrir_toot_navegador(db_id)
        elif cual == "copiar":
            f._on_copiar_enlace_toot(db_id)
        elif cual == "reportar":
            f._on_reportar_toot(db_id)
        elif cual == "autor":
            f._on_detalles_autor(db_id)

    def _menu(self):
        if not self._toot():
            speak(_("Enfoca primero una publicación."), interrupt=True)
            return
        menu = wx.Menu()
        from app.services.mastodon import filtros_servidor as fsrv
        _d = self._toot()
        if fsrv.esta_filtrado(self._ev_filtro(_d)) and not fsrv.esta_revelado(_d):
            it_f = menu.Append(wx.ID_ANY, _("Mostrar de todos modos (filtrada)"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_mostrar_filtrado(), it_f)
        opciones = [
            (_("Responder (Ctrl+R)"), "responder"),
            (_("Citar (Ctrl+Shift+U)"), "citar"),
            (_("Marcar/quitar favorito (Ctrl+B)"), "favorito"),
            (_("Impulsar / citar"), "impulsar"),
            (_("Ver conversación (Intro)"), "ver_conversacion"),
            (_("Traducir (Ctrl+T)"), "traducir"),
            (_("Abrir en el navegador"), "navegador"),
            (_("Copiar enlace"), "copiar"),
            (_("Reportar publicación..."), "reportar"),
            (_("Ver detalles del autor..."), "autor"),
        ]
        for etiqueta, clave in opciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, c=clave: self._accion(c), it)
        self.lista.PopupMenu(menu)
        menu.Destroy()

    def _on_key(self, event):
        key = event.GetKeyCode()
        mod = event.GetModifiers()
        if mod == wx.ACCEL_CTRL and key == ord('R'):
            self._accion("responder")
        elif mod == (wx.ACCEL_CTRL | wx.ACCEL_SHIFT) and key == ord('U'):
            self._accion("citar")
        elif mod == wx.ACCEL_CTRL and key == ord('B'):
            self._accion("favorito")
        elif mod == wx.ACCEL_CTRL and key == ord('T'):
            self._accion("traducir")
        elif key == wx.WXK_WINDOWS_MENU:  # tecla de aplicación
            self._menu()
        else:
            event.Skip()
