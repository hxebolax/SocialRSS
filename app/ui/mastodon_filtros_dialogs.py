# -*- coding: utf-8 -*-
"""Diálogos de los FILTROS DEL SERVIDOR de Mastodon (API v2, con respaldo a la v1).

- FiltrosServidorDialog: lista de filtros (título, palabras, publicaciones sueltas, acción,
  contextos y caducidad legible, marcando los caducados) con crear, editar, eliminar y ver o
  quitar las publicaciones filtradas.
- NuevoFiltroServidorDialog: formulario de crear/editar. Al editar se puede MANTENER la
  caducidad actual o quitarla ("Nunca"), se admiten filtros sin palabras (solo publicaciones)
  y "Solo palabras completas" se aplica también a las palabras que ya tenía el filtro.
- ElegirFiltroDialog: para "Filtrar esta publicación…" (elegir un filtro o crear uno nuevo).
- PublicacionesFiltroDialog: ver y quitar las publicaciones sueltas de un filtro.

Separado de mastodon_lists.py (que sigue reexportando las dos primeras clases).
"""
import wx

from app.core.speech import speak, notificar
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon import filtros_servidor as fsrv
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, confirmar,
                                  habilitar_escape_cierre)


def _contextos():
    return [("home", _("Inicio")), ("public", _("Públicas")), ("notifications", _("Notificaciones")),
            ("thread", _("Conversaciones")), ("account", _("Perfiles"))]


def resumen_filtro(f) -> str:
    """Texto de la primera columna de la lista de filtros."""
    titulo = f.get("title") or ""
    palabras = f.get("phrase") or ""
    n_pub = len(f.get("statuses") or [])
    partes = [p for p in (palabras,) if p]
    if n_pub:
        partes.append(_("1 publicación") if n_pub == 1 else _("{0} publicaciones").format(n_pub))
    detalle = ", ".join(partes)
    if titulo and detalle:
        res = f"{titulo}: {detalle}"
    else:
        res = titulo or detalle or _("(sin palabras)")
    if f.get("filter_action") == "hide":
        res += " " + _("(oculta)")
    elif f.get("filter_action") == "blur":
        res += " " + _("(difumina la multimedia)")
    return res


class FiltrosServidorDialog(wx.Dialog):
    """Gestiona los filtros del lado del servidor de Mastodon (con caducidad)."""

    _CONTEXTOS = _contextos()
    # Al CREAR, "Nunca" = sin caducidad (None no se envía). Al EDITAR, el formulario añade
    # "Mantener" delante y convierte "Nunca" en "" (el servidor quita la caducidad).
    _DURACIONES = [(_("Nunca"), None), (_("30 minutos"), 1800), (_("1 hora"), 3600),
                   (_("6 horas"), 21600), (_("12 horas"), 43200), (_("1 día"), 86400),
                   (_("1 semana"), 604800)]

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Filtros del servidor"), size=(680, 480),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo de filtros del servidor"))
        self.cambiado = False   # el llamador recalcula el buffer si se tocó algún filtro
        self.account_id = account_id
        self.filtros = []
        self._init_ui()
        self._cargar()
        self.CenterOnParent()

    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Filtros aplicados por tu instancia (afectan a todos tus clientes):")), 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de filtros del servidor"))
        self.lista.InsertColumn(0, _("Filtro"), width=260)
        self.lista.InsertColumn(1, _("Contextos"), width=220)
        self.lista.InsertColumn(2, _("Caduca"), width=160)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        b1 = wx.Button(self, label=_("&Crear filtro..."))
        b1.Bind(wx.EVT_BUTTON, self._on_crear)
        btns.Add(b1, 0, wx.ALL, 5)
        b_ed = wx.Button(self, label=_("&Editar..."))
        b_ed.Bind(wx.EVT_BUTTON, self._on_editar)
        btns.Add(b_ed, 0, wx.ALL, 5)
        b_pub = wx.Button(self, label=_("&Publicaciones filtradas..."))
        b_pub.Bind(wx.EVT_BUTTON, self._on_publicaciones)
        btns.Add(b_pub, 0, wx.ALL, 5)
        b2 = wx.Button(self, label=_("E&liminar"))
        b2.Bind(wx.EVT_BUTTON, self._on_eliminar)
        btns.Add(b2, 0, wx.ALL, 5)
        btns.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)

    def _cargar(self):
        speak(_("Cargando filtros del servidor..."), interrupt=True)

        def _fin(res, err):
            if not self:
                return
            if err:
                speak(_("No se pudieron cargar: {0}").format(err), interrupt=True)
                return
            self.filtros = res or []
            self.lista.DeleteAllItems()
            etiquetas = dict(self._CONTEXTOS)
            for i, f in enumerate(self.filtros):
                self.lista.InsertItem(i, resumen_filtro(f))
                ctxs = ", ".join(etiquetas.get(c, c) for c in f.get("context", []))
                self.lista.SetItem(i, 1, ctxs)
                self.lista.SetItem(i, 2, fsrv.describir_caducidad(f.get("expires_at")))
            if not self.filtros:
                speak(_("No hay filtros del servidor."), interrupt=True)
        mastodon_service.obtener_filtros_servidor_async(self.account_id, callback=_fin)

    def _filtro_enfocado(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.filtros):
            speak(_("Seleccione un filtro."), interrupt=True)
            return None
        return self.filtros[idx]

    def _blur_permitido(self, filtro=None):
        # 'Difuminar la multimedia' existe desde Mastodon 4.4: se ofrece si la instancia lo
        # admite, si aún no se conoce su versión o si el filtro ya la usa.
        if filtro and filtro.get("filter_action") == "blur":
            return True
        return mastodon_service.soporta_filtro_blur(self.account_id) is not False

    def _on_crear(self, event):
        dlg = NuevoFiltroServidorDialog(self, permitir_blur=self._blur_permitido())
        if dlg.ShowModal() == wx.ID_OK:
            datos = dlg.get_datos()
            if (datos["keywords"] or datos["title"]) and datos["contexts"]:
                def _fin(res, err):
                    if err:
                        notificar(_("No se pudo crear: {0}").format(err))
                    else:
                        notificar(_("Filtro creado."))
                        self.cambiado = True
                        if self:
                            self._cargar()
                mastodon_service.crear_filtro_servidor_async(
                    self.account_id, datos["phrase"], datos["contexts"],
                    expires_in=datos["expires_in"], whole_word=datos["whole_word"],
                    title=datos["title"], keywords=datos["keywords"],
                    filter_action=datos["filter_action"], callback=_fin)
            else:
                speak(_("Indique un título o al menos una palabra, y un contexto."), interrupt=True)
        dlg.Destroy()

    def _on_editar(self, event):
        f = self._filtro_enfocado()
        if f is None:
            return
        dlg = NuevoFiltroServidorDialog(self, filtro=f, permitir_blur=self._blur_permitido(f))
        if dlg.ShowModal() == wx.ID_OK:
            datos = dlg.get_datos()
            # En la v2 un filtro puede quedarse SIN palabras (solo publicaciones sueltas).
            sin_palabras_ok = bool(f.get("v2", True)) and bool(datos["title"])
            if (datos["keywords"] or sin_palabras_ok) and datos["contexts"]:
                def _fin(res, err):
                    if err:
                        notificar(_("No se pudo editar: {0}").format(err))
                    else:
                        notificar(_("Filtro actualizado."))
                        self.cambiado = True
                        if self:
                            self._cargar()
                mastodon_service.editar_filtro_servidor_async(
                    self.account_id, f["id"], datos["phrase"], datos["contexts"],
                    expires_in=datos["expires_in"], whole_word=datos["whole_word"],
                    title=datos["title"], keywords=datos["keywords"],
                    keywords_previas=f.get("keywords") or [],
                    filter_action=datos["filter_action"], es_v2=f.get("v2"), callback=_fin)
            else:
                speak(_("Indique un título o al menos una palabra, y un contexto."), interrupt=True)
        dlg.Destroy()

    def _on_publicaciones(self, event):
        f = self._filtro_enfocado()
        if f is None:
            return
        if not f.get("v2", True):
            notificar(_("Tu servidor no admite filtrar publicaciones sueltas."))
            return
        dlg = PublicacionesFiltroDialog(self, self.account_id, f)
        dlg.ShowModal()
        if dlg.cambiado:
            self.cambiado = True
        dlg.Destroy()
        self._cargar()

    def _on_eliminar(self, event):
        f = self._filtro_enfocado()
        if f is None:
            return
        if not confirmar(self, _("¿Eliminar el filtro «{0}»?").format(resumen_filtro(f)),
                         _("Eliminar filtro")):
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo eliminar: {0}").format(err))
            else:
                notificar(_("Filtro eliminado."))
                self.cambiado = True
                if self:
                    self._cargar()
        mastodon_service.borrar_filtro_servidor_async(self.account_id, f["id"],
                                                      es_v2=f.get("v2"), callback=_fin)


class NuevoFiltroServidorDialog(wx.Dialog):
    """Formulario para crear o editar un filtro del servidor."""

    # Acciones de filtro de la API v2, en el orden del desplegable.
    _ACCIONES_FILTRO = ("warn", "hide", "blur")

    def __init__(self, parent, filtro=None, permitir_blur=True):
        self._filtro = filtro or {}
        self._editando = bool(filtro)
        self._acciones = tuple(a for a in self._ACCIONES_FILTRO if a != "blur" or permitir_blur)
        titulo = _("Editar filtro del servidor") if filtro else _("Nuevo filtro del servidor")
        super().__init__(parent, title=titulo, size=(460, 520),
                         style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
        self._init_ui()
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.CenterOnParent()

    @staticmethod
    def duraciones(expires_at, editando):
        """Opciones del desplegable de caducidad: [(etiqueta, valor de expires_in)].
        Al editar un filtro con caducidad, la primera es MANTENERLA (None = no se envía) y
        "Nunca" envía "" para quitarla; al crear, "Nunca" es None (sin caducidad)."""
        base = list(FiltrosServidorDialog._DURACIONES)
        if not editando:
            return base
        res = [(_("Nunca (quitar la caducidad)") if expires_at else _("Nunca"), "")] + base[1:]
        if expires_at:
            fecha = fsrv.fecha_legible(expires_at)
            etiqueta = (_("Mantener (caducó el {0})") if fsrv.caducado(expires_at)
                        else _("Mantener (caduca el {0})"))
            res.insert(0, (etiqueta.format(fecha), None))
        else:
            res[0] = (_("Nunca"), None)
        return res

    def _init_ui(self):
        f = self._filtro
        s = wx.BoxSizer(wx.VERTICAL)
        # Título del filtro (API v2). En instancias antiguas (v1) se ignora sin más.
        s.Add(wx.StaticText(self, label=_("Título del filtro (obligatorio si no hay palabras):")), 0, wx.ALL, 5)
        self.txt_titulo = AccessibleTextCtrl(self, value=f.get("title", ""), name=_("Título del filtro"))
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 5)

        # VARIAS palabras por filtro (v2): una por línea. En v1 se crea un filtro por palabra.
        # Se precargan SOLO las palabras clave (nunca el título: un filtro sin palabras, p. ej.
        # solo de publicaciones, se queda vacío).
        s.Add(wx.StaticText(self, label=_("Palabras o frases a filtrar (una por línea):")), 0, wx.ALL, 5)
        _palabras_iniciales = "\n".join(k.get("keyword", "") for k in f.get("keywords", [])
                                        if k.get("keyword"))
        self.txt = AccessibleTextCtrl(self, value=_palabras_iniciales, style=wx.TE_MULTILINE,
                                      size=(-1, 70), name=_("Palabras del filtro, una por línea"))
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 5)
        n_pub = len(f.get("statuses") or [])
        if n_pub:
            s.Add(wx.StaticText(self, label=_("Este filtro también oculta {0} publicaciones sueltas "
                                              "(se gestionan con «Publicaciones filtradas»).").format(n_pub)),
                  0, wx.ALL, 5)

        # Acción del filtro (v2): avisar (se ve el aviso y se puede mostrar), ocultar del todo o
        # (4.4+) difuminar solo la multimedia.
        s.Add(wx.StaticText(self, label=_("Acción:")), 0, wx.ALL, 5)
        nombres = {"warn": _("Avisar (ocultar tras un aviso que se puede mostrar)"),
                   "hide": _("Ocultar por completo"),
                   "blur": _("Difuminar solo la multimedia (imágenes y vídeos)")}
        self.choice_accion = wx.Choice(self, choices=[nombres[a] for a in self._acciones],
                                       name=_("Acción del filtro"))
        actual = f.get("filter_action")
        self.choice_accion.SetSelection(self._acciones.index(actual) if actual in self._acciones else 0)
        s.Add(self.choice_accion, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Aplicar en:")), 0, wx.ALL, 5)
        self.checks = []
        ctx_actuales = f.get("context")
        for clave, etiqueta in FiltrosServidorDialog._CONTEXTOS:
            cb = wx.CheckBox(self, label=etiqueta, name=etiqueta)
            if ctx_actuales is not None:
                cb.SetValue(clave in ctx_actuales)
            elif clave in ("home", "public"):
                cb.SetValue(True)
            s.Add(cb, 0, wx.LEFT, 15)
            self.checks.append((clave, cb))

        # "Solo palabras completas" se aplica a TODAS las palabras, también a las que ya tenía.
        # Si las existentes no coinciden entre sí, la casilla empieza a medias (= mantener la
        # de cada palabra; las nuevas, palabra completa).
        kws = [k for k in f.get("keywords", []) if k.get("keyword")]
        mezcla = bool(kws) and len({bool(k.get("whole_word")) for k in kws}) > 1
        estilo = wx.CHK_3STATE | wx.CHK_ALLOW_3RD_STATE_FOR_USER if mezcla else wx.CHK_2STATE
        self.cb_whole = wx.CheckBox(self, label=_("Solo palabras completas"), style=estilo,
                                    name=_("Solo palabras completas"))
        if mezcla:
            self.cb_whole.Set3StateValue(wx.CHK_UNDETERMINED)
        else:
            self.cb_whole.SetValue(bool(f.get("whole_word", True)))
        s.Add(self.cb_whole, 0, wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Caducidad:")), 0, wx.ALL, 5)
        self._duraciones = self.duraciones(f.get("expires_at"), self._editando)
        self.choice_dur = wx.Choice(self, choices=[d[0] for d in self._duraciones],
                                    name=_("Caducidad del filtro"))
        self.choice_dur.SetSelection(0)
        s.Add(self.choice_dur, 0, wx.EXPAND | wx.ALL, 5)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Guardar") if f else _("&Crear"))
        ok.SetDefault()
        bs.Add(ok, 0, wx.ALL, 5)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))

    def get_datos(self):
        palabras = [l.strip() for l in self.txt.GetValue().splitlines() if l.strip()]
        if self.cb_whole.Is3State() and self.cb_whole.Get3StateValue() == wx.CHK_UNDETERMINED:
            whole = None
        else:
            whole = bool(self.cb_whole.GetValue())
        return {
            "phrase": palabras[0] if palabras else "",
            "keywords": palabras,
            "title": self.txt_titulo.GetValue().strip(),
            "filter_action": self._acciones[max(0, self.choice_accion.GetSelection())],
            "contexts": [c for c, cb in self.checks if cb.GetValue()],
            "whole_word": whole,
            "expires_in": self._duraciones[max(0, self.choice_dur.GetSelection())][1],
        }


class ElegirFiltroDialog(wx.Dialog):
    """"Filtrar esta publicación…": elegir uno de tus filtros o crear uno nuevo."""

    def __init__(self, parent, filtros):
        super().__init__(parent, title=_("Filtrar esta publicación"), size=(460, 360),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Filtrar esta publicación"))
        self.filtros = list(filtros or [])
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Elige el filtro al que añadir la publicación:")), 0, wx.ALL, 6)
        opciones = [_("Crear un filtro nuevo...")] + [resumen_filtro(f) for f in self.filtros]
        self.lista = wx.ListBox(self, choices=opciones, name=_("Filtros"))
        self.lista.SetSelection(1 if self.filtros else 0)
        self.lista.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.EndModal(wx.ID_OK))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        ok.SetDefault()
        bs.Add(ok, 0, wx.ALL, 5)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizer(s)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.CenterOnParent()
        self.lista.SetFocus()

    def elegido(self):
        """None = crear uno nuevo; si no, el dict del filtro elegido."""
        i = self.lista.GetSelection()
        if i is None or i <= 0 or i - 1 >= len(self.filtros):
            return None
        return self.filtros[i - 1]


class PublicacionesFiltroDialog(wx.Dialog):
    """Publicaciones sueltas de un filtro (FilterStatus): verlas y quitarlas."""

    def __init__(self, parent, account_id, filtro):
        nombre = filtro.get("title") or filtro.get("phrase") or ""
        titulo = _("Publicaciones filtradas: {0}").format(nombre)
        super().__init__(parent, title=titulo, size=(620, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        self.account_id = account_id
        self.filtro = filtro
        self.items = []
        self.cambiado = False
        s = wx.BoxSizer(wx.VERTICAL)
        self.lbl = wx.StaticText(self, label=_("Cargando..."))
        s.Add(self.lbl, 0, wx.ALL, 5)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Publicaciones del filtro"))
        self.lista.InsertColumn(0, _("Publicación"), width=560)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 5)
        bs = wx.BoxSizer(wx.HORIZONTAL)
        b_q = wx.Button(self, label=_("&Quitar del filtro"))
        b_q.Bind(wx.EVT_BUTTON, self._on_quitar)
        bs.Add(b_q, 0, wx.ALL, 5)
        bs.Add(wx.Button(self, wx.ID_CLOSE, _("&Cerrar")), 0, wx.ALL, 5)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self.CenterOnParent()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if not self:
                return
            if err:
                self.lbl.SetLabel(_("No se pudieron cargar: {0}").format(err))
                notificar(_("No se pudieron cargar: {0}").format(err))
                return
            self.items = res or []
            self.lista.DeleteAllItems()
            for i, it in enumerate(self.items):
                self.lista.InsertItem(i, it.get("texto") or _("Publicación {0}").format(it.get("status_id")))
            if self.items:
                self.lbl.SetLabel(_("{0} publicaciones filtradas:").format(len(self.items)))
                self.lista.Focus(0)
                self.lista.Select(0)
            else:
                self.lbl.SetLabel(_("Este filtro no tiene publicaciones sueltas."))
        mastodon_service.publicaciones_de_filtro_async(self.account_id, self.filtro["id"], callback=_fin)

    def _on_quitar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.items):
            speak(_("Seleccione una publicación."), interrupt=True)
            return
        it = self.items[idx]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo quitar: {0}").format(err))
                return
            notificar(_("Publicación quitada del filtro."))
            self.cambiado = True
            if self:
                self._cargar()
        mastodon_service.quitar_publicacion_de_filtro_async(self.account_id, it["id"], callback=_fin)
