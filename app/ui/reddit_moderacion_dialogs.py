# -*- coding: utf-8 -*-
"""Diálogos de moderación de subreddits (Fase 2 de Reddit).

Ventanas accesibles para moderadores: colas de moderación (modqueue/spam/reportados/sin moderar/
editados) con acciones por elemento, relaciones de usuario (baneados/silenciados/contribuidores/
moderadores), motivos de eliminación y configuración del subreddit. Todas las operaciones de red se
ejecutan en segundo plano por task_manager; el resultado se avisa por voz/notificación.

Se usan columnas fijas (no plantillas): son diálogos especializados, no buffers navegables.
"""
from datetime import datetime, timezone

import wx

from app.core.speech import speak, notificar
from app.core.privacy import abrir_url
from app.core.task_manager import task_manager
from app.services.reddit import reddit_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, AccessibleCheckListBox,
                                   CustomTextEntryDialog, mostrar_info, confirmar,
                                   habilitar_escape_cierre)

REDDIT_BASE = "https://www.reddit.com"

# Colas de moderación: clave PRAW -> etiqueta (evaluada en runtime para i18n).
_COLAS = [
    ("modqueue", lambda: _("Cola de moderación")),
    ("reports", lambda: _("Reportados")),
    ("spam", lambda: _("Spam")),
    ("unmoderated", lambda: _("Sin moderar")),
    ("edited", lambda: _("Editados")),
]

# Relaciones de usuario en el subreddit: clave -> etiqueta.
_RELACIONES = [
    ("banned", lambda: _("Usuarios baneados")),
    ("muted", lambda: _("Usuarios silenciados")),
    ("contributor", lambda: _("Contribuidores aprobados")),
    ("moderator", lambda: _("Moderadores")),
]

# Permisos de moderador (PRAW). "all" concede todos.
_PERMISOS_MOD = ["all", "access", "chat_config", "chat_operator", "config", "flair",
                 "mail", "posts", "wiki"]


def _fmt_fecha(epoch):
    """Epoch UTC -> 'dd/mm/aaaa hh:mm' (cadena vacía si no hay fecha)."""
    try:
        if not epoch:
            return ""
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def _ejecutar_mod(parent, fn, ok_msg="", tras_exito=None):
    """Ejecuta una operación de moderación en segundo plano y avisa del resultado.

    `fn` no recibe argumentos (ya lleva account_id/subreddit cerrados). `parent` se comprueba con
    truthiness para no tocar un diálogo ya destruido."""
    def _bg(ctx):
        return fn()

    def _fin(res, err):
        try:
            vivo = bool(parent)
        except Exception:
            vivo = False
        if err:
            notificar(_("No se pudo completar la acción de moderación: {0}").format(err))
            return
        if tras_exito and vivo:
            tras_exito(res)
        if ok_msg:
            speak(ok_msg, interrupt=True)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


class RedditModColaDialog(wx.Dialog):
    """Cola de moderación de un subreddit con acciones por elemento."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Moderar r/{0}").format(subreddit), size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Cola de moderación de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._items = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Cola:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_cola = wx.Choice(self, choices=[c[1]() for c in _COLAS], name=_("Cola de moderación"))
        self.choice_cola.SetSelection(0)
        self.choice_cola.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice_cola, 0, wx.RIGHT, 8)
        b_ref = wx.Button(self, label=_("&Actualizar"))
        b_ref.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        fila.Add(b_ref, 0)
        s.Add(fila, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Elementos (menú contextual o Intro para actuar):")),
              0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Elementos de la cola"))
        for i, (enc, ancho) in enumerate([(_("Tipo"), 80), (_("Autor"), 150), (_("Contenido"), 320),
                                          (_("Reportes"), 80), (_("Fecha"), 120)]):
            self.lista.InsertColumn(i, enc, width=ancho)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._menu_en(e.GetIndex()))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cola_actual(self):
        return _COLAS[self.choice_cola.GetSelection()][0]

    def _cargar(self):
        cola = self._cola_actual()
        speak(_("Cargando..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.cargar_cola(self.account_id, self.subreddit, cola)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la cola: {0}").format(err))
                return
            self._items = res or []
            self._pintar()
            speak(_("{0} elementos.").format(len(self._items)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar(self):
        self.lista.DeleteAllItems()
        try:
            self.lista.limpiar_lecturas()
        except Exception:
            pass
        for i, it in enumerate(self._items):
            tipo = _("Comentario") if it.get("tipo") == "comment" else _("Publicación")
            contenido = (it.get("titulo") or it.get("cuerpo") or "").replace("\n", " ").strip()
            vals = [tipo, it.get("author", ""), contenido[:300],
                    str(it.get("num_reports", 0)), _fmt_fecha(it.get("created_utc"))]
            pos = self.lista.InsertItem(i, vals[0])
            for c in range(1, len(vals)):
                self.lista.SetItem(pos, c, vals[c])
            lectura = _("{0} de u/{1}. {2}. {3} reportes.").format(
                tipo, it.get("author", ""), contenido[:300] or _("(sin texto)"),
                it.get("num_reports", 0))
            try:
                self.lista.establecer_lectura(pos, lectura)
            except Exception:
                pass
        if self._items:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _item_sel(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self._items):
            return None
        return self._items[idx]

    def _on_menu(self, event):
        idx = self.lista.GetFirstSelected()
        if idx >= 0:
            self._menu_en(idx)

    def _menu_en(self, idx):
        if idx < 0 or idx >= len(self._items):
            return
        it = self._items[idx]
        fn = it.get("fullname", "")
        es_comment = it.get("tipo") == "comment"
        menu = wx.Menu()

        def add(label, cb, sep=False):
            item = menu.Append(wx.ID_ANY, label)
            self.Bind(wx.EVT_MENU, lambda e: cb(), item)
            if sep:
                menu.AppendSeparator()

        add(_("Aprobar"), lambda: self._accion(idx, lambda: reddit_service.mod_aprobar(
            self.account_id, fn), _("Aprobado.")))
        add(_("Eliminar"), lambda: self._accion(idx, lambda: reddit_service.mod_eliminar(
            self.account_id, fn), _("Eliminado.")))
        add(_("Eliminar como spam"), lambda: self._accion(idx, lambda: reddit_service.mod_eliminar(
            self.account_id, fn, spam=True), _("Marcado como spam.")), sep=True)
        add(_("Bloquear comentarios"), lambda: self._accion(idx, lambda: reddit_service.mod_bloquear(
            self.account_id, fn, True), _("Bloqueado."), quitar=False))
        add(_("Desbloquear comentarios"), lambda: self._accion(idx, lambda: reddit_service.mod_bloquear(
            self.account_id, fn, False), _("Desbloqueado."), quitar=False))
        add(_("Distinguir (con placa de moderador)"),
            lambda: self._accion(idx, lambda: reddit_service.mod_distinguir(
                self.account_id, fn, how="yes"), _("Distinguido."), quitar=False))
        if not es_comment:
            add(_("Distinguir y fijar arriba"),
                lambda: self._accion(idx, lambda: reddit_service.mod_distinguir(
                    self.account_id, fn, how="yes", sticky=True), _("Distinguido y fijado."), quitar=False))
        add(_("Quitar distinción"),
            lambda: self._accion(idx, lambda: reddit_service.mod_distinguir(
                self.account_id, fn, how="no"), _("Distinción quitada."), quitar=False), sep=True)
        add(_("Ignorar reportes"), lambda: self._accion(idx, lambda: reddit_service.mod_ignorar_reportes(
            self.account_id, fn, True), _("Reportes ignorados."), quitar=False))
        add(_("Dejar de ignorar reportes"),
            lambda: self._accion(idx, lambda: reddit_service.mod_ignorar_reportes(
                self.account_id, fn, False), _("Se vuelven a tener en cuenta los reportes."), quitar=False))
        add(_("Enviar mensaje de eliminación..."), lambda: self._mensaje_eliminacion(fn), sep=True)
        permalink = it.get("permalink", "")
        if permalink:
            add(_("Abrir en el navegador"),
                lambda: abrir_url(REDDIT_BASE + permalink if permalink.startswith("/") else permalink))
        self.PopupMenu(menu)
        menu.Destroy()

    def _accion(self, idx, fn, ok_msg, quitar=True):
        """Ejecuta la acción; si `quitar`, elimina el elemento de la lista tras el éxito (aprobar/
        eliminar lo sacan de la cola). Las alternancias (bloquear/distinguir) no lo quitan."""
        def _tras(_res):
            if quitar and self and 0 <= idx < len(self._items):
                del self._items[idx]
                self._pintar()
        _ejecutar_mod(self, fn, ok_msg, tras_exito=_tras)

    def _mensaje_eliminacion(self, fullname):
        dlg = CustomTextEntryDialog(self, _("Motivo que se enviará al autor:"),
                                    _("Mensaje de eliminación"), multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        _ejecutar_mod(self, lambda: reddit_service.mod_mensaje_eliminacion(
            self.account_id, fullname, texto), _("Mensaje enviado."))


class RedditModUsuariosDialog(wx.Dialog):
    """Relaciones de usuario de un subreddit: baneados, silenciados, contribuidores y moderadores."""

    def __init__(self, parent, account_id, subreddit, relacion_inicial="banned"):
        super().__init__(parent, title=_("Usuarios de r/{0}").format(subreddit), size=(680, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Usuarios de r/{0}").format(subreddit))
        self.account_id = account_id
        self.subreddit = subreddit
        self._filas = []
        self._init_ui(relacion_inicial)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self, relacion_inicial):
        s = wx.BoxSizer(wx.VERTICAL)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Relación:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_rel = wx.Choice(self, choices=[r[1]() for r in _RELACIONES], name=_("Relación"))
        idx_ini = next((i for i, r in enumerate(_RELACIONES) if r[0] == relacion_inicial), 0)
        self.choice_rel.SetSelection(idx_ini)
        self.choice_rel.Bind(wx.EVT_CHOICE, lambda e: self._cargar())
        fila.Add(self.choice_rel, 0, wx.RIGHT, 8)
        s.Add(fila, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Usuarios:")), 0, wx.LEFT, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de usuarios"))
        for i, (enc, ancho) in enumerate([(_("Usuario"), 200), (_("Detalle"), 300), (_("Fecha"), 120)]):
            self.lista.InsertColumn(i, enc, width=ancho)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, self._on_menu)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_add = wx.Button(self, label=_("&Añadir..."))
        self.btn_add.Bind(wx.EVT_BUTTON, lambda e: self._añadir())
        self.btn_del = wx.Button(self, label=_("&Quitar"))
        self.btn_del.Bind(wx.EVT_BUTTON, lambda e: self._quitar())
        b_ref = wx.Button(self, label=_("A&ctualizar"))
        b_ref.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        botones.Add(self.btn_add, 0, wx.RIGHT, 4)
        botones.Add(self.btn_del, 0, wx.RIGHT, 4)
        botones.Add(b_ref, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _rel_actual(self):
        return _RELACIONES[self.choice_rel.GetSelection()][0]

    def _cargar(self):
        rel = self._rel_actual()
        speak(_("Cargando..."), interrupt=True)
        fns = {"banned": reddit_service.listar_baneados,
               "muted": reddit_service.listar_silenciados,
               "contributor": reddit_service.listar_contribuidores,
               "moderator": reddit_service.listar_moderadores}

        def _bg(ctx):
            return fns[rel](self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la lista: {0}").format(err))
                return
            self._filas = res or []
            self._pintar(rel)
            speak(_("{0} usuarios.").format(len(self._filas)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar(self, rel):
        self.lista.DeleteAllItems()
        try:
            self.lista.limpiar_lecturas()
        except Exception:
            pass
        for i, r in enumerate(self._filas):
            nombre = r.get("name", "")
            if rel == "moderator":
                detalle = ", ".join(r.get("permissions", [])) or _("(sin permisos)")
                fecha = ""
            else:
                detalle = r.get("note", "")
                fecha = _fmt_fecha(r.get("date"))
            pos = self.lista.InsertItem(i, _("u/{0}").format(nombre))
            self.lista.SetItem(pos, 1, detalle)
            self.lista.SetItem(pos, 2, fecha)
            try:
                self.lista.establecer_lectura(pos, _("u/{0}. {1}").format(nombre, detalle))
            except Exception:
                pass
        if self._filas:
            self.lista.Focus(0)
            self.lista.Select(0)

    def _usuario_sel(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self._filas):
            return None
        return self._filas[idx].get("name", "")

    def _on_menu(self, event):
        if self._rel_actual() != "moderator":
            return
        usuario = self._usuario_sel()
        if not usuario:
            return
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Cambiar permisos..."))
        self.Bind(wx.EVT_MENU, lambda e: self._cambiar_permisos(usuario), it)
        self.PopupMenu(menu)
        menu.Destroy()

    # ------------------------------------------------------------------ añadir/quitar
    def _añadir(self):
        rel = self._rel_actual()
        if rel == "banned":
            self._banear()
        elif rel == "moderator":
            self._invitar_moderador()
        else:
            dlg = CustomTextEntryDialog(self, _("Usuario a añadir (sin u/):"),
                                        _("Añadir usuario"))
            if dlg.ShowModal() == wx.ID_OK:
                usuario = dlg.GetValue().strip().lstrip("u/").lstrip("/")
                if usuario:
                    fn = (reddit_service.silenciar if rel == "muted" else reddit_service.contribuidor)
                    _ejecutar_mod(self, lambda: fn(self.account_id, self.subreddit, usuario),
                                  _("Usuario añadido."), tras_exito=lambda r: self._cargar())
            dlg.Destroy()

    def _quitar(self):
        usuario = self._usuario_sel()
        if not usuario:
            speak(_("Selecciona un usuario."), interrupt=True)
            return
        rel = self._rel_actual()
        if confirmar(self, _("¿Quitar a u/{0}?").format(usuario), _("Confirmar")) != wx.ID_YES:
            return
        acciones = {
            "banned": lambda: reddit_service.quitar_ban(self.account_id, self.subreddit, usuario),
            "muted": lambda: reddit_service.silenciar(self.account_id, self.subreddit, usuario, quitar=True),
            "contributor": lambda: reddit_service.contribuidor(self.account_id, self.subreddit, usuario, quitar=True),
            "moderator": lambda: reddit_service.quitar_moderador(self.account_id, self.subreddit, usuario),
        }
        _ejecutar_mod(self, acciones[rel], _("Usuario quitado."), tras_exito=lambda r: self._cargar())

    def _banear(self):
        dlg = _BanearDialog(self, self.subreddit)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            d = dlg.resultado
            _ejecutar_mod(self, lambda: reddit_service.banear(
                self.account_id, self.subreddit, d["usuario"], motivo=d["motivo"],
                mensaje=d["mensaje"], duracion=d["duracion"], nota=d["nota"]),
                _("Usuario baneado."), tras_exito=lambda r: self._cargar())
        dlg.Destroy()

    def _invitar_moderador(self):
        dlg = _PermisosModDialog(self, _("Invitar como moderador"), pedir_usuario=True)
        if dlg.ShowModal() == wx.ID_OK and dlg.usuario:
            _ejecutar_mod(self, lambda: reddit_service.invitar_moderador(
                self.account_id, self.subreddit, dlg.usuario, permisos=dlg.permisos),
                _("Invitación enviada."))
        dlg.Destroy()

    def _cambiar_permisos(self, usuario):
        dlg = _PermisosModDialog(self, _("Permisos de u/{0}").format(usuario), pedir_usuario=False)
        if dlg.ShowModal() == wx.ID_OK:
            _ejecutar_mod(self, lambda: reddit_service.actualizar_permisos_moderador(
                self.account_id, self.subreddit, usuario, dlg.permisos),
                _("Permisos actualizados."), tras_exito=lambda r: self._cargar())
        dlg.Destroy()


class _BanearDialog(wx.Dialog):
    """Datos de un baneo: usuario, motivo (visible para moderadores), mensaje al usuario, duración
    (0 = permanente) y nota interna. `self.resultado` = dict o None."""

    def __init__(self, parent, subreddit):
        super().__init__(parent, title=_("Banear en r/{0}").format(subreddit),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Banear usuario"))
        self.resultado = None
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Usuario (sin u/):")), 0, wx.LEFT | wx.TOP, 8)
        self.txt_usuario = AccessibleTextCtrl(self, name=_("Usuario"))
        s.Add(self.txt_usuario, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Motivo (para los moderadores):")), 0, wx.LEFT, 8)
        self.txt_motivo = AccessibleTextCtrl(self, name=_("Motivo"), max_length=100)
        s.Add(self.txt_motivo, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Mensaje para el usuario (opcional):")), 0, wx.LEFT, 8)
        self.txt_mensaje = AccessibleTextCtrl(self, style=wx.TE_MULTILINE, size=(-1, 90),
                                              name=_("Mensaje para el usuario"))
        s.Add(self.txt_mensaje, 0, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Duración en días (0 = permanente):")), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.spin_dur = wx.SpinCtrl(self, min=0, max=999, initial=0, name=_("Duración en días"))
        fila.Add(self.spin_dur, 0)
        s.Add(fila, 0, wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Nota interna (opcional):")), 0, wx.LEFT, 8)
        self.txt_nota = AccessibleTextCtrl(self, name=_("Nota interna"), max_length=100)
        s.Add(self.txt_nota, 0, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Banear"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(b_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.Fit()
        self.CenterOnParent()
        self.txt_usuario.SetFocus()

    def _on_ok(self, event):
        usuario = self.txt_usuario.GetValue().strip().lstrip("u/").lstrip("/")
        if not usuario:
            mostrar_info(self, _("Escribe el nombre de usuario."), _("Falta el usuario"))
            return
        dur = self.spin_dur.GetValue()
        self.resultado = {"usuario": usuario, "motivo": self.txt_motivo.GetValue().strip(),
                          "mensaje": self.txt_mensaje.GetValue().strip(),
                          "duracion": dur if dur > 0 else None,
                          "nota": self.txt_nota.GetValue().strip()}
        self.EndModal(wx.ID_OK)


class _PermisosModDialog(wx.Dialog):
    """Selección de permisos de moderador (opcionalmente pidiendo el usuario para invitar)."""

    def __init__(self, parent, titulo, pedir_usuario=True):
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
        self.usuario = ""
        self.permisos = None
        self._pedir_usuario = pedir_usuario
        s = wx.BoxSizer(wx.VERTICAL)
        if pedir_usuario:
            s.Add(wx.StaticText(self, label=_("Usuario (sin u/):")), 0, wx.LEFT | wx.TOP, 8)
            self.txt_usuario = AccessibleTextCtrl(self, name=_("Usuario"))
            s.Add(self.txt_usuario, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(self, label=_("Permisos (marca 'all' para conceder todos):")),
              0, wx.LEFT, 8)
        self.chk = AccessibleCheckListBox(self, choices=_PERMISOS_MOD, name=_("Permisos de moderador"))
        self.chk.Check(0, True)  # "all" por defecto
        s.Add(self.chk, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(b_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)
        self.SetSize((360, 340))
        self.CenterOnParent()

    def _on_ok(self, event):
        if self._pedir_usuario:
            self.usuario = self.txt_usuario.GetValue().strip().lstrip("u/").lstrip("/")
            if not self.usuario:
                mostrar_info(self, _("Escribe el nombre de usuario."), _("Falta el usuario"))
                return
        marcados = [_PERMISOS_MOD[i] for i in range(len(_PERMISOS_MOD)) if self.chk.IsChecked(i)]
        # "all" tiene prioridad; si no hay ninguno, se envía lista vacía (sin permisos).
        self.permisos = ["all"] if "all" in marcados else marcados
        self.EndModal(wx.ID_OK)


class RedditModMotivosDialog(wx.Dialog):
    """Motivos de eliminación (removal reasons) de un subreddit."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Motivos de eliminación de r/{0}").format(subreddit),
                         size=(600, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Motivos de eliminación"))
        self.account_id = account_id
        self.subreddit = subreddit
        self._motivos = []
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Motivos:")), 0, wx.LEFT | wx.TOP, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de motivos"))
        self.lista.InsertColumn(0, _("Título"), width=200)
        self.lista.InsertColumn(1, _("Mensaje"), width=340)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_add = wx.Button(self, label=_("&Añadir..."))
        b_add.Bind(wx.EVT_BUTTON, lambda e: self._añadir())
        b_del = wx.Button(self, label=_("&Borrar"))
        b_del.Bind(wx.EVT_BUTTON, lambda e: self._borrar())
        botones.Add(b_add, 0, wx.RIGHT, 4)
        botones.Add(b_del, 0, wx.RIGHT, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(botones, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)

    def _cargar(self):
        def _bg(ctx):
            return reddit_service.listar_motivos(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudieron cargar los motivos: {0}").format(err))
                return
            self._motivos = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, m in enumerate(self._motivos):
                pos = self.lista.InsertItem(i, m.get("title", ""))
                self.lista.SetItem(pos, 1, (m.get("message", "") or "").replace("\n", " "))
                try:
                    self.lista.establecer_lectura(pos, _("{0}. {1}").format(
                        m.get("title", ""), m.get("message", "")))
                except Exception:
                    pass
            if self._motivos:
                self.lista.Focus(0)
                self.lista.Select(0)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _añadir(self):
        dlg = wx.Dialog(self, title=_("Nuevo motivo"), style=wx.DEFAULT_DIALOG_STYLE,
                        name=_("Nuevo motivo de eliminación"))
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_("Título:")), 0, wx.LEFT | wx.TOP, 8)
        t_tit = AccessibleTextCtrl(dlg, name=_("Título"), max_length=100)
        s.Add(t_tit, 0, wx.EXPAND | wx.ALL, 6)
        s.Add(wx.StaticText(dlg, label=_("Mensaje:")), 0, wx.LEFT, 8)
        t_msg = AccessibleTextCtrl(dlg, style=wx.TE_MULTILINE, size=(-1, 120), name=_("Mensaje"))
        s.Add(t_msg, 0, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        bs.Add(wx.Button(dlg, wx.ID_OK, _("&Aceptar")), 0, wx.ALL, 4)
        bs.Add(wx.Button(dlg, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(bs, 0, wx.ALIGN_CENTER)
        dlg.SetSizer(s)
        dlg.Fit()
        dlg.CenterOnParent()
        t_tit.SetFocus()
        if dlg.ShowModal() == wx.ID_OK:
            titulo = t_tit.GetValue().strip()
            mensaje = t_msg.GetValue().strip()
            if titulo:
                _ejecutar_mod(self, lambda: reddit_service.añadir_motivo(
                    self.account_id, self.subreddit, titulo, mensaje),
                    _("Motivo añadido."), tras_exito=lambda r: self._cargar())
        dlg.Destroy()

    def _borrar(self):
        idx = self.lista.GetFirstSelected()
        if idx < 0 or idx >= len(self._motivos):
            speak(_("Selecciona un motivo."), interrupt=True)
            return
        m = self._motivos[idx]
        if confirmar(self, _("¿Borrar el motivo «{0}»?").format(m.get("title", "")),
                     _("Confirmar")) != wx.ID_YES:
            return
        _ejecutar_mod(self, lambda: reddit_service.borrar_motivo(
            self.account_id, self.subreddit, m.get("id", "")),
            _("Motivo borrado."), tras_exito=lambda r: self._cargar())


# Campos de configuración editables (clave PRAW -> etiqueta). Se editan como texto; el resto de la
# configuración se muestra en un cuadro de solo lectura.
_CONFIG_TEXTO = [
    ("title", lambda: _("Título del subreddit")),
    ("public_description", lambda: _("Descripción pública (subtítulo)")),
    ("description", lambda: _("Barra lateral (descripción larga)")),
    ("submit_text", lambda: _("Texto al publicar")),
]
_CONFIG_TIPOS = [("public", lambda: _("Público")), ("restricted", lambda: _("Restringido")),
                 ("private", lambda: _("Privado"))]


class RedditModConfigDialog(wx.Dialog):
    """Configuración básica de un subreddit (los campos más comunes de mod.settings)."""

    def __init__(self, parent, account_id, subreddit):
        super().__init__(parent, title=_("Configuración de r/{0}").format(subreddit),
                         size=(640, 600), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Configuración del subreddit"))
        self.account_id = account_id
        self.subreddit = subreddit
        self._settings = {}
        self._campos = {}
        self._init_ui()
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        for clave, etq in _CONFIG_TEXTO:
            s.Add(wx.StaticText(self, label=etq() + ":"), 0, wx.LEFT | wx.TOP, 6)
            multilinea = clave in ("description", "submit_text")
            ctrl = AccessibleTextCtrl(self, name=etq(),
                                      style=wx.TE_MULTILINE if multilinea else 0,
                                      size=(-1, 80) if multilinea else (-1, -1))
            self._campos[clave] = ctrl
            s.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("Tipo de acceso:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.choice_tipo = wx.Choice(self, choices=[t[1]() for t in _CONFIG_TIPOS], name=_("Tipo de acceso"))
        self.choice_tipo.SetSelection(0)
        fila.Add(self.choice_tipo, 0, wx.RIGHT, 12)
        self.cb_nsfw = wx.CheckBox(self, label=_("Comunidad +18 (NSFW)"), name=_("Comunidad +18"))
        fila.Add(self.cb_nsfw, 0, wx.ALIGN_CENTER_VERTICAL)
        s.Add(fila, 0, wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Otros ajustes (solo lectura):")), 0, wx.LEFT | wx.TOP, 6)
        self.txt_otros = AccessibleTextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY,
                                            size=(-1, 120), name=_("Otros ajustes"))
        s.Add(self.txt_otros, 1, wx.EXPAND | wx.ALL, 6)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Guardar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        botones.Add(b_ok, 0, wx.ALL, 4)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(botones, 0, wx.ALIGN_CENTER)
        self.SetSizer(s)

    def _cargar(self):
        speak(_("Cargando configuración..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.obtener_config_subreddit(self.account_id, self.subreddit)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la configuración: {0}").format(err))
                return
            self._settings = res or {}
            for clave, ctrl in self._campos.items():
                ctrl.SetValue(str(self._settings.get(clave, "") or ""))
            tipo = self._settings.get("subreddit_type", "public")
            idx = next((i for i, t in enumerate(_CONFIG_TIPOS) if t[0] == tipo), 0)
            self.choice_tipo.SetSelection(idx)
            self.cb_nsfw.SetValue(bool(self._settings.get("over_18", False)))
            self._pintar_otros()
            speak(_("Configuración cargada."), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _pintar_otros(self):
        editables = {c for c, _e in _CONFIG_TEXTO} | {"subreddit_type", "over_18"}
        lineas = []
        for k in sorted(self._settings.keys()):
            if k in editables:
                continue
            v = self._settings[k]
            if isinstance(v, (str, int, float, bool)) and str(v) != "":
                lineas.append("{0}: {1}".format(k, v))
        self.txt_otros.SetValue("\n".join(lineas))

    def _on_guardar(self, event):
        settings = {c: ctrl.GetValue() for c, ctrl in self._campos.items()}
        settings["subreddit_type"] = _CONFIG_TIPOS[self.choice_tipo.GetSelection()][0]
        settings["over_18"] = self.cb_nsfw.GetValue()
        _ejecutar_mod(self, lambda: reddit_service.actualizar_config_subreddit(
            self.account_id, self.subreddit, **settings), _("Configuración guardada."))
        self.EndModal(wx.ID_OK)
