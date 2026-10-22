# -*- coding: utf-8 -*-
"""Diálogos de administración de PeerTube (Fase 9): usuarios, solicitudes de registro, federación,
trabajos (jobs), plugins y tokens de runners. Solo son útiles para administradores; el servidor
rechaza las acciones si la cuenta no lo es. Las acciones destructivas piden confirmación.
"""
import wx

from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.core.task_manager import task_manager
from app.services.peertube import peertube_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                   mostrar_info, confirmar, habilitar_escape_cierre)

_ROLES = [(2, lambda: _("Usuario")), (1, lambda: _("Moderador")), (0, lambda: _("Administrador"))]


def _ejec(parent, fn, ok_msg="", tras=None):
    def _bg(ctx):
        return fn()

    def _fin(res, err):
        vivo = bool(parent) if parent is not None else False
        if err:
            notificar(_("No se pudo completar la operación: {0}").format(err))
            return
        if tras and vivo:
            tras(res)
        if ok_msg:
            speak(ok_msg, interrupt=True)
    task_manager.start_task(_bg, callback=_fin, pass_context=True)


class _ListaAdminDialog(wx.Dialog):
    """Base con una lista accesible y carga asíncrona. Las subclases definen columnas, carga y filas."""

    COLUMNAS = []

    def __init__(self, parent, account_id, titulo):
        super().__init__(parent, title=titulo, size=(720, 500),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self.account_id = account_id
        self._items = []
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=titulo)
        for i, (enc, an) in enumerate(self.COLUMNAS):
            self.lista.InsertColumn(i, enc, width=an)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        self._bs = wx.BoxSizer(wx.HORIZONTAL)
        self._construir_botones()
        b_act = wx.Button(self, label=_("A&ctualizar"))
        b_act.Bind(wx.EVT_BUTTON, lambda e: self._cargar())
        self._bs.Add(b_act, 0, wx.RIGHT, 4)
        self._bs.Add(wx.Button(self, wx.ID_CANCEL, _("Cerrar")), 0)
        s.Add(self._bs, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(s)
        self.CenterOnParent()
        habilitar_escape_cierre(self)
        self._cargar()

    def _boton(self, etiqueta, cb):
        b = wx.Button(self, label=etiqueta)
        b.Bind(wx.EVT_BUTTON, lambda e: cb())
        self._bs.Add(b, 0, wx.RIGHT, 4)

    def _sel(self):
        i = self.lista.GetFirstSelected()
        return self._items[i] if 0 <= i < len(self._items) else None

    def _cargar(self):
        def _bg(ctx):
            return self._descargar()

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar: {0}").format(err))
                return
            self._items = res or []
            self.lista.DeleteAllItems()
            try:
                self.lista.limpiar_lecturas()
            except Exception:
                pass
            for i, it in enumerate(self._items):
                cols, lectura = self._fila(it)
                pos = self.lista.InsertItem(i, str(cols[0]))
                for c in range(1, len(cols)):
                    self.lista.SetItem(pos, c, str(cols[c]))
                try:
                    self.lista.establecer_lectura(pos, lectura)
                except Exception:
                    pass
            if self._items:
                self.lista.Focus(0); self.lista.Select(0)
            speak(_("{0} elementos.").format(len(self._items)), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # A implementar por las subclases:
    def _construir_botones(self): ...
    def _descargar(self): return []
    def _fila(self, it): return ([], "")


class PeerTubeUsuariosDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Usuario"), 180), (lambda: _("Correo"), 220), (lambda: _("Rol"), 120),
                (lambda: _("Estado"), 100)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubeUsuariosDialog.COLUMNAS]
        super().__init__(parent, account_id, _("Usuarios de la instancia"))

    def _construir_botones(self):
        self._boton(_("&Nuevo..."), self._nuevo)
        self._boton(_("&Rol y cuota..."), self._editar)
        self._boton(_("&Bloquear/Desbloquear"), self._bloquear)
        self._boton(_("B&orrar"), self._borrar)

    def _descargar(self):
        return peertube_service.admin_listar_usuarios(self.account_id)

    def _fila(self, u):
        estado = _("Bloqueado") if u.get("bloqueado") else _("Activo")
        return ([u.get("username", ""), u.get("email", ""), u.get("rol", ""), estado],
                _("{0}, rol {1}, {2}").format(u.get("username", ""), u.get("rol", ""), estado))

    def _nuevo(self):
        d1 = CustomTextEntryDialog(self, _("Nombre de usuario:"), _("Nuevo usuario"))
        if d1.ShowModal() != wx.ID_OK:
            d1.Destroy(); return
        usuario = d1.GetValue().strip(); d1.Destroy()
        d2 = CustomTextEntryDialog(self, _("Correo electrónico:"), _("Nuevo usuario"))
        correo = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        d3 = CustomTextEntryDialog(self, _("Contraseña:"), _("Nuevo usuario"), password=True)
        clave = d3.GetValue() if d3.ShowModal() == wx.ID_OK else ""
        d3.Destroy()
        if usuario and correo and clave:
            _ejec(self, lambda: peertube_service.admin_crear_usuario(
                self.account_id, usuario, clave, correo), _("Usuario creado."),
                tras=lambda r: self._cargar())

    def _editar(self):
        u = self._sel()
        if not u:
            return
        d = wx.SingleChoiceDialog(self, _("Rol de {0}:").format(u["username"]), _("Rol"),
                                  [r[1]() for r in _ROLES])
        idx = next((i for i, r in enumerate(_ROLES) if r[0] == u.get("rol_id")), 0)
        d.SetSelection(idx)
        if d.ShowModal() != wx.ID_OK:
            d.Destroy(); return
        rol = _ROLES[d.GetSelection()][0]
        d.Destroy()
        dq = CustomTextEntryDialog(self, _("Cuota de vídeo en GB (-1 = sin límite):"),
                                   _("Cuota"), value="-1")
        cuota = None
        if dq.ShowModal() == wx.ID_OK:
            try:
                gb = float(dq.GetValue().strip())
                cuota = -1 if gb < 0 else int(gb * 1024 * 1024 * 1024)
            except ValueError:
                cuota = None
        dq.Destroy()
        _ejec(self, lambda: peertube_service.admin_editar_usuario(
            self.account_id, u["id"], role=rol, videoQuota=cuota), _("Usuario actualizado."),
            tras=lambda r: self._cargar())

    def _bloquear(self):
        u = self._sel()
        if not u:
            return
        bloquear = not u.get("bloqueado")
        _ejec(self, lambda: peertube_service.admin_bloquear_usuario(
            self.account_id, u["id"], bloquear=bloquear),
            _("Usuario bloqueado.") if bloquear else _("Usuario desbloqueado."),
            tras=lambda r: self._cargar())

    def _borrar(self):
        u = self._sel()
        if not u:
            return
        if confirmar(self, _("¿Borrar al usuario «{0}»? Se eliminan sus vídeos y datos.").format(
                u["username"]), _("Confirmar")) != wx.ID_YES:
            return
        _ejec(self, lambda: peertube_service.admin_borrar_usuario(self.account_id, u["id"]),
              _("Usuario borrado."), tras=lambda r: self._cargar())


class PeerTubeRegistrosDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Usuario"), 180), (lambda: _("Correo"), 220), (lambda: _("Motivo"), 240)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubeRegistrosDialog.COLUMNAS]
        super().__init__(parent, account_id, _("Solicitudes de registro"))

    def _construir_botones(self):
        self._boton(_("&Aceptar"), lambda: self._resolver(True))
        self._boton(_("&Rechazar"), lambda: self._resolver(False))

    def _descargar(self):
        return peertube_service.admin_listar_registros(self.account_id)

    def _fila(self, r):
        return ([r.get("username", ""), r.get("email", ""), r.get("motivo", "")],
                _("{0}, motivo: {1}").format(r.get("username", ""), r.get("motivo", "")))

    def _resolver(self, aceptar):
        r = self._sel()
        if not r:
            return
        d = CustomTextEntryDialog(self, _("Respuesta al solicitante (opcional):"),
                                  _("Aceptar") if aceptar else _("Rechazar"), multiline=True)
        resp = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        fn = (peertube_service.admin_aceptar_registro if aceptar
              else peertube_service.admin_rechazar_registro)
        _ejec(self, lambda: fn(self.account_id, r["id"], respuesta=resp),
              _("Registro aceptado.") if aceptar else _("Registro rechazado."),
              tras=lambda r2: self._cargar())


class PeerTubeFederacionDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Instancia"), 320), (lambda: _("Estado"), 140)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubeFederacionDialog.COLUMNAS]
        self._modo = "siguiendo"
        super().__init__(parent, account_id, _("Federación de la instancia"))

    def _construir_botones(self):
        self._boton(_("Ver &siguiendo"), lambda: self._cambiar("siguiendo"))
        self._boton(_("Ver se&guidores"), lambda: self._cambiar("seguidores"))
        self._boton(_("Seguir &instancia..."), self._seguir)
        self._boton(_("&Dejar / Rechazar"), self._quitar)
        self._boton(_("&Aceptar seguidor"), self._aceptar)

    def _cambiar(self, modo):
        self._modo = modo
        self._cargar()

    def _descargar(self):
        if self._modo == "seguidores":
            return peertube_service.admin_listar_seguidores(self.account_id)
        return peertube_service.admin_listar_siguiendo(self.account_id)

    def _fila(self, f):
        return ([f.get("host", ""), f.get("estado", "")],
                _("{0}, {1}").format(f.get("host", ""), f.get("estado", "")))

    def _seguir(self):
        d = CustomTextEntryDialog(self, _("Instancia a seguir (host):"), _("Seguir instancia"))
        if d.ShowModal() == wx.ID_OK:
            host = d.GetValue().strip()
            if host:
                _ejec(self, lambda: peertube_service.admin_seguir_instancias(self.account_id, [host]),
                      _("Solicitud de seguimiento enviada."), tras=lambda r: self._cambiar("siguiendo"))
        d.Destroy()

    def _quitar(self):
        f = self._sel()
        if not f:
            return
        if self._modo == "seguidores":
            _ejec(self, lambda: peertube_service.admin_aceptar_seguidor(
                self.account_id, f["host"], aceptar=False), _("Seguidor rechazado."),
                tras=lambda r: self._cargar())
        else:
            if confirmar(self, _("¿Dejar de seguir a {0}?").format(f["host"]),
                         _("Confirmar")) != wx.ID_YES:
                return
            _ejec(self, lambda: peertube_service.admin_dejar_instancia(self.account_id, f["host"]),
                  _("Has dejado de seguir la instancia."), tras=lambda r: self._cargar())

    def _aceptar(self):
        f = self._sel()
        if not f or self._modo != "seguidores":
            speak(_("Selecciona un seguidor en la lista de seguidores."), interrupt=True)
            return
        _ejec(self, lambda: peertube_service.admin_aceptar_seguidor(self.account_id, f["host"]),
              _("Seguidor aceptado."), tras=lambda r: self._cargar())


class PeerTubeJobsDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Tipo"), 240), (lambda: _("Estado"), 140), (lambda: _("Error"), 260)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubeJobsDialog.COLUMNAS]
        self._estado = "completed"
        super().__init__(parent, account_id, _("Trabajos del servidor"))

    def _construir_botones(self):
        for est, etq in (("completed", _("Completados")), ("failed", _("Fallidos")),
                         ("waiting", _("En espera")), ("active", _("Activos"))):
            self._boton(etq, lambda e=est: self._ver(e))

    def _ver(self, estado):
        self._estado = estado
        self._cargar()

    def _descargar(self):
        return peertube_service.admin_listar_jobs(self.account_id, estado=self._estado)

    def _fila(self, j):
        return ([j.get("tipo", ""), j.get("estado", ""), (j.get("error", "") or "")[:200]],
                _("{0}, {1}").format(j.get("tipo", ""), j.get("estado", "")))


class PeerTubePluginsDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Nombre"), 240), (lambda: _("Versión"), 120), (lambda: _("Paquete npm"), 260)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubePluginsDialog.COLUMNAS]
        super().__init__(parent, account_id, _("Plugins instalados"))

    def _construir_botones(self):
        self._boton(_("&Instalar..."), self._instalar)
        self._boton(_("&Desinstalar"), self._desinstalar)

    def _descargar(self):
        return peertube_service.admin_listar_plugins(self.account_id)

    def _fila(self, p):
        return ([p.get("nombre", ""), p.get("version", ""), p.get("npm", "")],
                _("{0}, versión {1}").format(p.get("nombre", ""), p.get("version", "")))

    def _instalar(self):
        d = CustomTextEntryDialog(self, _("Nombre del paquete npm del plugin:"), _("Instalar plugin"))
        if d.ShowModal() == wx.ID_OK:
            npm = d.GetValue().strip()
            if npm:
                _ejec(self, lambda: peertube_service.admin_instalar_plugin(self.account_id, npm),
                      _("Plugin instalado."), tras=lambda r: self._cargar())
        d.Destroy()

    def _desinstalar(self):
        p = self._sel()
        if not p:
            return
        if confirmar(self, _("¿Desinstalar «{0}»?").format(p["nombre"]), _("Confirmar")) != wx.ID_YES:
            return
        _ejec(self, lambda: peertube_service.admin_desinstalar_plugin(self.account_id, p["npm"]),
              _("Plugin desinstalado."), tras=lambda r: self._cargar())


class PeerTubeRunnersDialog(_ListaAdminDialog):
    COLUMNAS = [(lambda: _("Token de registro"), 420), (lambda: _("Creado"), 180)]

    def __init__(self, parent, account_id):
        _ListaAdminDialog.COLUMNAS = [(c[0](), c[1]) for c in PeerTubeRunnersDialog.COLUMNAS]
        super().__init__(parent, account_id, _("Tokens de registro de runners"))

    def _construir_botones(self):
        self._boton(_("&Generar nuevo"), self._generar)
        self._boton(_("&Copiar token"), self._copiar)
        self._boton(_("&Borrar"), self._borrar)

    def _descargar(self):
        return peertube_service.admin_listar_tokens_runner(self.account_id)

    def _fila(self, t):
        tok = t.get("token", "")
        return ([tok, t.get("fecha", "")], _("Token de runner"))

    def _generar(self):
        _ejec(self, lambda: peertube_service.admin_generar_token_runner(self.account_id),
              _("Token generado."), tras=lambda r: self._cargar())

    def _copiar(self):
        t = self._sel()
        if t and t.get("token"):
            copiar_al_portapapeles(t["token"], _("Token copiado. Úsalo para registrar un runner."))

    def _borrar(self):
        t = self._sel()
        if not t:
            return
        if confirmar(self, _("¿Borrar este token de registro?"), _("Confirmar")) != wx.ID_YES:
            return
        _ejec(self, lambda: peertube_service.admin_borrar_token_runner(self.account_id, t["id"]),
              _("Token borrado."), tras=lambda r: self._cargar())
