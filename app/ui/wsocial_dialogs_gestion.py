# -*- coding: utf-8 -*-
"""Diálogos de gestión de W Social: listas, grupos, moderación, preferencias, contraseñas de
aplicación, salida pendiente y búsqueda avanzada.

Van aparte de `wsocial_dialogs` para que ningún archivo se vuelva inmanejable (regla del
proyecto: módulos pequeños y con una responsabilidad clara).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.wsocial import wsocial_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                  mostrar_info, confirmar, habilitar_escape_cierre)


def _botonera(panel, botones, cerrar=True):
    """Fila de botones con la etiqueta y el manejador de cada uno."""
    fila = wx.BoxSizer(wx.HORIZONTAL)
    for etiqueta, handler in botones:
        b = wx.Button(panel, label=etiqueta)
        b.Bind(wx.EVT_BUTTON, handler)
        fila.Add(b, 0, wx.ALL, 4)
    if cerrar:
        fila.Add(wx.Button(panel, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 4)
    return fila


class _DialogoBase(wx.Dialog):
    """Base común: lista accesible arriba, botonera abajo y Escape para cerrar."""

    def __init__(self, parent, titulo, columnas, account_id, tam=(680, 480)):
        super().__init__(parent, title=titulo, size=tam,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.datos = []
        self.panel = wx.Panel(self)
        self.sizer = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self.panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
                                        name=titulo)
        for i, (titulo_col, ancho) in enumerate(columnas):
            self.lista.InsertColumn(i, titulo_col, width=ancho)
        self.sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

    def _terminar(self, botones):
        self.sizer.Add(_botonera(self.panel, botones), 0, wx.ALIGN_CENTER)
        self.panel.SetSizer(self.sizer)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(self.panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)

    def _seleccionado(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self.datos):
            speak(_("Selecciona primero un elemento de la lista."), interrupt=True)
            return None
        return self.datos[idx]

    def _pintar(self, filas):
        """filas: lista de (columnas, lectura)."""
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, (cols, lectura) in enumerate(filas):
            pos = self.lista.InsertItem(i, cols[0])
            for j, valor in enumerate(cols[1:], start=1):
                self.lista.SetItem(pos, j, valor)
            self.lista.establecer_lectura(pos, lectura)
        if filas:
            self.lista.Focus(0)
            self.lista.Select(0)


# ======================================================================
# LISTAS
# ======================================================================
class WSocialListsDialog(_DialogoBase):
    """Gestión de listas: crear, renombrar, borrar, ver miembros y abrir como buffer."""

    def __init__(self, parent, account_id):
        super().__init__(parent, _("Listas de W Social"),
                         [(_("Nombre"), 240), (_("Tipo"), 140), (_("Miembros"), 100),
                          (_("Descripción"), 300)], account_id)
        self._terminar([(_("&Nueva..."), self._on_nueva),
                        (_("&Renombrar..."), self._on_renombrar),
                        (_("&Miembros..."), self._on_miembros),
                        (_("&Borrar"), self._on_borrar),
                        (_("&Actualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        speak(_("Cargando tus listas..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _p():
                self.datos = res
                tipos = {"curatelist": _("Para leer"), "modlist": _("Moderación"),
                         "referencelist": _("Referencia")}
                filas = []
                for l in res:
                    tipo = tipos.get(str(l.get("proposito", "")).rsplit("#", 1)[-1],
                                     l.get("papel", ""))
                    filas.append((
                        [l.get("name", ""), tipo, str(l.get("miembros", 0)), l.get("description", "")],
                        f"{l.get('name')}. {tipo}. {l.get('miembros', 0)} miembros. "
                        f"{l.get('description', '')}"))
                self._pintar(filas)
            wx.CallAfter(_p)
        wsocial_service.cargar_listas_async(self.account_id, callback=_cb)

    def _on_nueva(self, event):
        nombre = self._pedir(_("Nombre de la lista:"), _("Nueva lista"))
        if not nombre:
            return
        descripcion = self._pedir(_("Descripción (opcional):"), _("Nueva lista"))
        wsocial_service.crear_lista_async(
            self.account_id, nombre, descripcion,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Lista creada.")))
            if ok else notificar(str(r)))

    def _pedir(self, mensaje, titulo, valor=""):
        dlg = CustomTextEntryDialog(self, mensaje, titulo, value=valor)
        texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        return texto

    def _on_renombrar(self, event):
        lista = self._seleccionado()
        if not lista:
            return
        nombre = self._pedir(_("Nuevo nombre:"), _("Renombrar la lista"), lista.get("name", ""))
        if not nombre:
            return
        wsocial_service.editar_lista_async(
            self.account_id, lista["uri"], nombre,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Lista renombrada.")))
            if ok else notificar(str(r)))

    def _on_borrar(self, event):
        lista = self._seleccionado()
        if not lista:
            return
        if confirmar(self, _("¿Borrar la lista «{0}»?").format(lista.get("name", "")),
                     _("Borrar la lista")) != wx.ID_YES:
            return
        wsocial_service.borrar_lista_async(
            self.account_id, lista["uri"],
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Lista borrada.")))
            if ok else notificar(str(r)))

    def _on_miembros(self, event):
        lista = self._seleccionado()
        if not lista:
            return
        dlg = WSocialListMembersDialog(self, self.account_id, lista)
        dlg.ShowModal()
        dlg.Destroy()


class WSocialListMembersDialog(_DialogoBase):
    """Cuentas de una lista: añadir y quitar."""

    def __init__(self, parent, account_id, lista):
        super().__init__(parent, _("Miembros de {0}").format(lista.get("name", "")),
                         [(_("Nombre"), 240), (_("Usuario"), 280)], account_id)
        self.lista_info = lista
        self._terminar([(_("&Añadir cuenta..."), self._on_anadir),
                        (_("&Quitar"), self._on_quitar),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _p():
                self.datos = res
                self._pintar([([m.get("display_name") or m.get("handle", ""),
                                "@" + m.get("handle", "")],
                               f"{m.get('display_name') or m.get('handle')}. @{m.get('handle')}")
                              for m in res])
            wx.CallAfter(_p)
        wsocial_service.miembros_lista_async(self.account_id, self.lista_info["uri"], callback=_cb)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Usuario a añadir (por ejemplo nombre.wsocial.eu):"),
                                    _("Añadir a la lista"))
        actor = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not actor:
            return
        wsocial_service.anadir_miembro_lista_async(
            self.account_id, self.lista_info["uri"], actor,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Cuenta añadida.")))
            if ok else notificar(str(r)))

    def _on_quitar(self, event):
        miembro = self._seleccionado()
        if not miembro:
            return
        wsocial_service.quitar_miembro_lista_async(
            self.account_id, miembro.get("item_uri", ""),
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Cuenta quitada.")))
            if ok else notificar(str(r)))


# ======================================================================
# GRUPOS
# ======================================================================
class WSocialGroupMembersDialog(_DialogoBase):
    """Participantes de un grupo: ver, añadir y expulsar."""

    def __init__(self, parent, account_id, convo_id):
        super().__init__(parent, _("Participantes del grupo"),
                         [(_("Nombre"), 240), (_("Usuario"), 240), (_("Papel"), 140)], account_id)
        self.convo_id = convo_id
        self._terminar([(_("&Añadir cuentas..."), self._on_anadir),
                        (_("&Expulsar"), self._on_expulsar),
                        (_("&Solicitudes..."), self._on_solicitudes),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar los participantes: {0}").format(res))
                return
            papeles = {"owner": _("Propietario"), "admin": _("Administrador"),
                       "member": _("Participante")}
            def _p():
                self.datos = res
                self._pintar([([m.get("nombre") or m.get("handle", ""), "@" + m.get("handle", ""),
                                papeles.get(m.get("papel", ""), m.get("papel", ""))],
                               f"{m.get('nombre') or m.get('handle')}. @{m.get('handle')}. "
                               f"{papeles.get(m.get('papel', ''), '')}") for m in res])
            wx.CallAfter(_p)
        wsocial_service.miembros_grupo_async(self.account_id, self.convo_id, callback=_cb)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Cuentas a añadir, separadas por comas:"),
                                    _("Añadir al grupo"))
        texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        actores = [c.strip() for c in texto.replace(";", ",").split(",") if c.strip()]
        if not actores:
            return
        wsocial_service.gestionar_miembros_grupo_async(
            self.account_id, self.convo_id, actores, quitar=False,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Añadidas al grupo.")))
            if ok else notificar(str(r)))

    def _on_expulsar(self, event):
        miembro = self._seleccionado()
        if not miembro:
            return
        if confirmar(self, _("¿Expulsar a @{0} del grupo?").format(miembro.get("handle", "")),
                     _("Expulsar")) != wx.ID_YES:
            return
        wsocial_service.gestionar_miembros_grupo_async(
            self.account_id, self.convo_id, [miembro.get("did", "")], quitar=True,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Cuenta expulsada.")))
            if ok else notificar(str(r)))

    def _on_solicitudes(self, event):
        frame = wx.GetApp().GetTopWindow()
        ctrl = getattr(frame, "wsocial_ctrl", None)
        if ctrl:
            ctrl.solicitudes_grupo(self.convo_id)


# ======================================================================
# MODERACIÓN Y PREFERENCIAS
# ======================================================================
class WSocialMutedWordsDialog(_DialogoBase):
    """Palabras silenciadas: se ocultan las publicaciones que las contengan."""

    def __init__(self, parent, account_id):
        super().__init__(parent, _("Palabras silenciadas"),
                         [(_("Palabra"), 220), (_("Dónde"), 200), (_("Caduca"), 160),
                          (_("A quién"), 200)], account_id)
        self._terminar([(_("&Añadir..."), self._on_anadir), (_("&Quitar"), self._on_quitar),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _p():
                self.datos = (res or {}).get("palabras", [])
                filas = []
                for p in self.datos:
                    donde = ", ".join(_("texto") if a == "content" else _("etiquetas")
                                      for a in p.get("ambitos", []))
                    quien = (_("Solo cuentas que no sigo") if p.get("solo_desconocidos")
                             else _("Cualquiera"))
                    caduca = p.get("caduca") or _("Nunca")
                    filas.append(([p.get("valor", ""), donde, caduca, quien],
                                  f"{p.get('valor')}. {donde}. {quien}. {caduca}"))
                self._pintar(filas)
            wx.CallAfter(_p)
        wsocial_service.obtener_preferencias_async(self.account_id, callback=_cb)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Palabra o frase que quieres silenciar:"),
                                    _("Silenciar una palabra"))
        palabra = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not palabra:
            return
        sel = wx.GetSingleChoice(_("¿Dónde quieres silenciarla?"), _("Silenciar una palabra"),
                                 [_("En el texto y en las etiquetas"), _("Solo en las etiquetas")],
                                 self)
        if not sel:
            return
        ambitos = ["content", "tag"] if sel == _("En el texto y en las etiquetas") else ["tag"]
        solo_desconocidos = confirmar(
            self, _("¿Aplicarla solo a cuentas que NO sigues?"),
            _("Silenciar una palabra")) == wx.ID_YES
        nuevas = list(self.datos) + [{"valor": palabra, "ambitos": ambitos,
                                      "solo_desconocidos": solo_desconocidos, "caduca": ""}]
        wsocial_service.fijar_palabras_silenciadas_async(
            self.account_id, nuevas,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Palabra silenciada.")))
            if ok else notificar(str(r)))

    def _on_quitar(self, event):
        palabra = self._seleccionado()
        if not palabra:
            return
        nuevas = [p for p in self.datos if p.get("valor") != palabra.get("valor")]
        wsocial_service.fijar_palabras_silenciadas_async(
            self.account_id, nuevas,
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Palabra quitada.")))
            if ok else notificar(str(r)))


class WSocialContentPrefsDialog(wx.Dialog):
    """Preferencias de contenido: adulto, qué hacer con cada etiqueta e idiomas."""

    _ETIQUETAS = [("porn", _("Pornografía")), ("sexual", _("Contenido sexual")),
                  ("nudity", _("Desnudos")), ("graphic-media", _("Violencia gráfica")),
                  ("spam", _("Spam")), ("impersonation", _("Suplantación"))]
    _OPCIONES = [(_("Mostrar sin más"), "ignore"), (_("Avisar antes"), "warn"),
                 (_("Ocultar del todo"), "hide")]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Preferencias de contenido de W Social"), size=(560, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.chk_adulto = wx.CheckBox(panel, label=_("Permitir contenido para adultos"),
                                      name=_("Contenido para adultos"))
        s.Add(self.chk_adulto, 0, wx.ALL, 10)
        s.Add(wx.StaticText(panel, label=_("Qué hacer con cada tipo de contenido marcado:")),
              0, wx.LEFT | wx.TOP, 10)
        self._choices = {}
        for clave, etiqueta in self._ETIQUETAS:
            fila = wx.BoxSizer(wx.HORIZONTAL)
            fila.Add(wx.StaticText(panel, label=etiqueta + ":"), 0,
                     wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            ch = wx.Choice(panel, choices=[t for t, _v in self._OPCIONES], name=etiqueta)
            ch.SetSelection(1)
            self._choices[clave] = ch
            fila.Add(ch, 0, wx.ALL, 5)
            s.Add(fila, 0, wx.LEFT, 10)
        s.Add(wx.StaticText(panel, label=_("&Idiomas que quieres ver (códigos separados por comas):")),
              0, wx.LEFT | wx.TOP, 10)
        self.txt_idiomas = AccessibleTextCtrl(panel, name=_("Idiomas de contenido"))
        s.Add(self.txt_idiomas, 0, wx.EXPAND | wx.ALL, 10)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(panel, wx.ID_OK, _("&Guardar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila.Add(btn_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(panel, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                return
            def _p():
                self.chk_adulto.SetValue(bool(res.get("adulto")))
                for clave, ch in self._choices.items():
                    valor = (res.get("etiquetas") or {}).get(clave, "warn")
                    for i, (_t, v) in enumerate(self._OPCIONES):
                        if v == valor:
                            ch.SetSelection(i)
                self.txt_idiomas.SetValue(", ".join(res.get("idiomas") or []))
                # Se cachean para que el pintado de la lista no consulte la red por cada fila.
                config.set("wsocial", "prefs_etiquetas", res.get("etiquetas") or {})
            wx.CallAfter(_p)
        wsocial_service.obtener_preferencias_async(self.account_id, callback=_cb)

    def _on_guardar(self, event):
        wsocial_service.fijar_contenido_adulto_async(self.account_id, self.chk_adulto.GetValue())
        etiquetas = {}
        for clave, ch in self._choices.items():
            valor = self._OPCIONES[ch.GetSelection()][1]
            etiquetas[clave] = valor
            wsocial_service.fijar_visibilidad_etiqueta_async(self.account_id, clave, valor)
        config.set("wsocial", "prefs_etiquetas", etiquetas)
        idiomas = [i.strip() for i in self.txt_idiomas.GetValue().replace(";", ",").split(",")
                   if i.strip()]
        if idiomas:
            wsocial_service.fijar_idiomas_async(self.account_id, idiomas)
        notificar(_("Preferencias de contenido guardadas."))
        self.EndModal(wx.ID_OK)


class WSocialLabelersDialog(_DialogoBase):
    """Servicios de etiquetado a los que estás suscrito."""

    def __init__(self, parent, account_id):
        super().__init__(parent, _("Servicios de etiquetado"),
                         [(_("Servicio"), 240), (_("Usuario"), 220), (_("Etiquetas"), 260),
                          (_("Suscrito"), 100)], account_id)
        self._terminar([(_("&Suscribirse o cancelar"), self._on_alternar),
                        (_("&Añadir por usuario..."), self._on_anadir),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _p():
                self.datos = res or []
                self._pintar([([l.get("nombre") or l.get("handle", ""), "@" + l.get("handle", ""),
                                ", ".join(l.get("etiquetas", [])[:6]),
                                _("Sí") if l.get("suscrito") else _("No")],
                               f"{l.get('nombre') or l.get('handle')}. "
                               f"{_('Suscrito') if l.get('suscrito') else _('No suscrito')}. "
                               f"{l.get('descripcion', '')}") for l in self.datos])
                if not self.datos:
                    speak(_("No estás suscrito a ningún servicio de etiquetado."), interrupt=True)
            wx.CallAfter(_p)
        wsocial_service.labelers_async(self.account_id, callback=_cb)

    def _on_alternar(self, event):
        labeler = self._seleccionado()
        if not labeler:
            return
        nuevo = not labeler.get("suscrito")
        wsocial_service.suscribir_labeler(self.account_id, labeler.get("did", ""), nuevo)
        notificar(_("Suscrito.") if nuevo else _("Suscripción cancelada."))
        self._cargar()

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Usuario o identificador del servicio de etiquetado:"),
                                    _("Añadir un servicio"))
        actor = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not actor:
            return

        def _cb(ok, res):
            if ok and res.get("did"):
                wsocial_service.suscribir_labeler(self.account_id, res["did"], True)
                notificar(_("Servicio añadido."))
                wx.CallAfter(self._cargar)
            else:
                notificar(_("No se encontró ese servicio."))
        wsocial_service.obtener_perfil_async(self.account_id, actor, callback=_cb)


# ======================================================================
# CUENTA
# ======================================================================
class WSocialAppPasswordsDialog(_DialogoBase):
    """Contraseñas de aplicación de la cuenta."""

    def __init__(self, parent, account_id):
        super().__init__(parent, _("Contraseñas de aplicación"),
                         [(_("Nombre"), 260), (_("Creada"), 200), (_("Mensajes"), 140)],
                         account_id)
        self._terminar([(_("&Crear..."), self._on_crear), (_("&Revocar"), self._on_revocar),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _p():
                self.datos = res or []
                self._pintar([([p.get("name", ""), p.get("created_at", ""),
                                _("Sí") if p.get("privileged") else _("No")],
                               f"{p.get('name')}. {p.get('created_at')}") for p in self.datos])
            wx.CallAfter(_p)
        wsocial_service.listar_app_passwords_async(self.account_id, callback=_cb)

    def _on_crear(self, event):
        dlg = CustomTextEntryDialog(self, _("Nombre para la nueva contraseña (por ejemplo, el "
                                            "nombre del dispositivo):"), _("Nueva contraseña"))
        nombre = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not nombre:
            return
        privilegiada = confirmar(
            self, _("¿Debe permitir el acceso a los mensajes directos?"),
            _("Nueva contraseña")) == wx.ID_YES

        def _cb(ok, res):
            if not ok:
                notificar(str(res))
                return
            def _mostrar():
                clave = (res or {}).get("password", "")
                try:
                    if clave and wx.TheClipboard.Open():
                        wx.TheClipboard.SetData(wx.TextDataObject(clave))
                        wx.TheClipboard.Close()
                except Exception:
                    pass
                mostrar_info(self, _("Contraseña creada (copiada al portapapeles). Apúntala ahora: "
                                     "no se puede volver a ver.\n\n{0}").format(clave),
                             _("Nueva contraseña de aplicación"))
                self._cargar()
            wx.CallAfter(_mostrar)
        wsocial_service.crear_app_password_async(self.account_id, nombre, privilegiada, callback=_cb)

    def _on_revocar(self, event):
        password = self._seleccionado()
        if not password:
            return
        if confirmar(self, _("¿Revocar la contraseña «{0}»? Lo que la use dejará de funcionar.")
                     .format(password.get("name", "")), _("Revocar")) != wx.ID_YES:
            return
        wsocial_service.revocar_app_password_async(
            self.account_id, password.get("name", ""),
            callback=lambda ok, r: (wx.CallAfter(self._cargar), notificar(_("Contraseña revocada.")))
            if ok else notificar(str(r)))


class WSocialProfileEditDialog(wx.Dialog):
    """Editar el perfil propio."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Editar mi perfil de W Social"), size=(560, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.avatar = ""
        self.banner = ""
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("&Nombre que se muestra:")), 0, wx.LEFT | wx.TOP, 10)
        self.txt_nombre = AccessibleTextCtrl(panel, name=_("Nombre del perfil"))
        s.Add(self.txt_nombre, 0, wx.EXPAND | wx.ALL, 10)
        s.Add(wx.StaticText(panel, label=_("&Descripción:")), 0, wx.LEFT, 10)
        self.txt_desc = AccessibleTextCtrl(panel, style=wx.TE_MULTILINE, size=(520, 120),
                                           name=_("Descripción del perfil"))
        s.Add(self.txt_desc, 1, wx.EXPAND | wx.ALL, 10)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(panel, label=_("Cambiar la &foto..."))
        b1.Bind(wx.EVT_BUTTON, lambda e: self._elegir("avatar"))
        fila.Add(b1, 0, wx.ALL, 5)
        b2 = wx.Button(panel, label=_("Cambiar el &encabezado..."))
        b2.Bind(wx.EVT_BUTTON, lambda e: self._elegir("banner"))
        fila.Add(b2, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.ALIGN_CENTER)
        self.lbl_imgs = wx.StaticText(panel, label=_("Sin cambios en las imágenes."))
        s.Add(self.lbl_imgs, 0, wx.ALL, 10)
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(panel, wx.ID_OK, _("&Guardar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        fila2.Add(btn_ok, 0, wx.ALL, 5)
        fila2.Add(wx.Button(panel, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._cargar()

    def _cargar(self):
        yo = wsocial_service._me.get(self.account_id) or {}

        def _cb(ok, res):
            if ok:
                wx.CallAfter(self.txt_nombre.SetValue, res.get("display_name", "") or "")
                wx.CallAfter(self.txt_desc.SetValue, res.get("description", "") or "")
        if yo.get("did"):
            wsocial_service.obtener_perfil_async(self.account_id, yo["did"], callback=_cb)

    def _elegir(self, cual):
        dlg = wx.FileDialog(self, _("Elige una imagen"),
                            wildcard=_("Imágenes") + " (*.jpg;*.jpeg;*.png)|*.jpg;*.jpeg;*.png",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            if cual == "avatar":
                self.avatar = dlg.GetPath()
            else:
                self.banner = dlg.GetPath()
            self.lbl_imgs.SetLabel(_("Foto: {0}. Encabezado: {1}.").format(
                _("nueva") if self.avatar else _("sin cambios"),
                _("nuevo") if self.banner else _("sin cambios")))
        dlg.Destroy()

    def _on_guardar(self, event):
        wsocial_service.editar_perfil_async(
            self.account_id, self.txt_nombre.GetValue().strip(),
            self.txt_desc.GetValue().strip(), self.avatar, self.banner,
            callback=lambda ok, r: notificar(_("Perfil actualizado.") if ok else str(r)))
        self.EndModal(wx.ID_OK)


class WSocialOutboxDialog(_DialogoBase):
    """Publicaciones que quedaron pendientes: reintentar o descartar."""

    def __init__(self, parent, account_id):
        super().__init__(parent, _("Salida pendiente de W Social"),
                         [(_("Texto"), 380), (_("Estado"), 120), (_("Intentos"), 90),
                          (_("Error"), 240)], account_id)
        self._terminar([(_("&Reintentar"), self._on_reintentar),
                        (_("&Descartar"), self._on_descartar),
                        (_("A&ctualizar"), lambda e: self._cargar())])
        self._cargar()

    def _cargar(self):
        self.datos = wsocial_service.listar_outbox(self.account_id)
        estados = {"pending": _("Pendiente"), "error": _("Con error")}
        self._pintar([([(o.get("texto", "") or "")[:200], estados.get(o.get("estado"), o.get("estado", "")),
                        str(o.get("intentos", 0)), (o.get("error") or "")[:200]],
                       f"{o.get('texto', '')}. {estados.get(o.get('estado'), '')}. "
                       f"{o.get('error') or ''}") for o in self.datos])
        if not self.datos:
            speak(_("No hay ninguna publicación pendiente."), interrupt=True)

    def _on_reintentar(self, event):
        entrada = self._seleccionado()
        if not entrada:
            return
        speak(_("Reintentando..."), interrupt=True)

        def _cb(ok, res):
            if ok:
                notificar(_("Publicación enviada.") if not (isinstance(res, dict) and res.get("ya_estaba"))
                          else _("Ya se había publicado; se ha marcado como enviada."))
            else:
                notificar(_("Sigue sin poder enviarse: {0}").format(res))
            wx.CallAfter(self._cargar)
        wsocial_service.reintentar_outbox_async(self.account_id, entrada["id"], callback=_cb)

    def _on_descartar(self, event):
        entrada = self._seleccionado()
        if not entrada:
            return
        if confirmar(self, _("¿Descartar esta publicación pendiente?"),
                     _("Descartar")) != wx.ID_YES:
            return
        wsocial_service.borrar_outbox(entrada["id"])
        self._cargar()


class WSocialSearchDialog(wx.Dialog):
    """Búsqueda de publicaciones con filtros."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Buscar en W Social"), size=(520, 400),
                         style=wx.DEFAULT_DIALOG_STYLE)
        self.account_id = account_id
        self.criterios = None
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("&Texto a buscar:")), 0, wx.LEFT | wx.TOP, 10)
        self.txt = AccessibleTextCtrl(panel, name=_("Texto a buscar"))
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 10)
        s.Add(wx.StaticText(panel, label=_("&Autor (opcional):")), 0, wx.LEFT, 10)
        self.txt_autor = AccessibleTextCtrl(panel, name=_("Autor"))
        s.Add(self.txt_autor, 0, wx.EXPAND | wx.ALL, 10)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(panel, label=_("&Desde (AAAA-MM-DD):")), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_desde = AccessibleTextCtrl(panel, size=(120, -1), name=_("Fecha desde"))
        fila.Add(self.txt_desde, 0, wx.ALL, 5)
        fila.Add(wx.StaticText(panel, label=_("&Hasta:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_hasta = AccessibleTextCtrl(panel, size=(120, -1), name=_("Fecha hasta"))
        fila.Add(self.txt_hasta, 0, wx.ALL, 5)
        s.Add(fila, 0, wx.LEFT, 5)
        fila2 = wx.BoxSizer(wx.HORIZONTAL)
        fila2.Add(wx.StaticText(panel, label=_("&Idioma:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.txt_idioma = AccessibleTextCtrl(panel, size=(80, -1), name=_("Idioma"))
        fila2.Add(self.txt_idioma, 0, wx.ALL, 5)
        fila2.Add(wx.StaticText(panel, label=_("&Ordenar por:")), 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self.cmb_orden = wx.Choice(panel, choices=[_("Más recientes"), _("Más populares")],
                                   name=_("Orden de los resultados"))
        self.cmb_orden.SetSelection(0)
        fila2.Add(self.cmb_orden, 0, wx.ALL, 5)
        s.Add(fila2, 0, wx.LEFT, 5)
        fila3 = wx.BoxSizer(wx.HORIZONTAL)
        btn_ok = wx.Button(panel, wx.ID_OK, _("&Buscar"))
        btn_ok.Bind(wx.EVT_BUTTON, self._on_buscar)
        fila3.Add(btn_ok, 0, wx.ALL, 5)
        fila3.Add(wx.Button(panel, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila3, 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt.SetFocus()

    def _on_buscar(self, event):
        texto = self.txt.GetValue().strip()
        if not texto:
            mostrar_info(self, _("Escribe qué quieres buscar."), _("Buscar"))
            return
        self.criterios = {
            "texto": texto,
            "autor": self.txt_autor.GetValue().strip(),
            "desde": self.txt_desde.GetValue().strip(),
            "hasta": self.txt_hasta.GetValue().strip(),
            "idioma": self.txt_idioma.GetValue().strip(),
            "orden": "top" if self.cmb_orden.GetSelection() == 1 else "latest",
        }
        self.EndModal(wx.ID_OK)
