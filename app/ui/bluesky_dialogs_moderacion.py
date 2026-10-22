# -*- coding: utf-8 -*-
"""Diálogos de MODERACIÓN de Bluesky (las mismas preferencias que la app oficial):

- BlueskyMutedWordsDialog / BlueskyMutedWordEditDialog: palabras silenciadas completas
  (dónde, excepto cuentas que sigo, duración; editar conservando el id).
- BlueskyModeracionDialog: contenido para adultos y ocultar/avisar/mostrar por etiqueta,
  global y de cada servicio de etiquetado suscrito.
- BlueskyLabelersDialog: servicios de etiquetado (Bluesky Moderation y suscritos).
- BlueskyHiddenPostsDialog: publicaciones que ocultaste ("Ocultar esta publicación para mí").
- BlueskyFeedViewDialog: preferencias del feed Siguiendo y orden de los hilos.

Todos con botones en español (Aceptar/Cancelar/Cerrar) y Escape para cerrar.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

from datetime import datetime, timedelta, timezone

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.services.bluesky import bluesky_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, CustomTextEntryDialog,
                                  mostrar_info, confirmar, elegir_opcion, habilitar_escape_cierre)

# Visibilidades de etiqueta (valor del lexicon, texto accesible).
OPCIONES_VIS = [("ignore", _("Mostrar")), ("warn", _("Avisar")), ("hide", _("Ocultar"))]
NOMBRE_VIS = dict(OPCIONES_VIS)

NOMBRES_GLOBALES = {"porn": _("Contenido para adultos (pornografía)"),
                    "sexual": _("Contenido sexual sugerente"),
                    "graphic-media": _("Contenido gráfico o violento"),
                    "nudity": _("Desnudos no sexuales (arte, etc.)")}

DURACIONES = [(_("Para siempre"), None), (_("24 horas"), timedelta(hours=24)),
              (_("7 días"), timedelta(days=7)), (_("30 días"), timedelta(days=30))]


def _fecha_local(iso):
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso or "")


def _botonera(panel, botones, cerrar_label=None, cerrar_id=wx.ID_CLOSE):
    fila = wx.BoxSizer(wx.HORIZONTAL)
    for etiqueta, handler in botones:
        b = wx.Button(panel, label=etiqueta)
        b.Bind(wx.EVT_BUTTON, handler)
        fila.Add(b, 0, wx.ALL, 4)
    if cerrar_label:
        fila.Add(wx.Button(panel, cerrar_id, cerrar_label), 0, wx.ALL, 4)
    return fila


def _vivo(win) -> bool:
    try:
        return bool(win)
    except Exception:
        return False


# ======================================================================
# PALABRAS SILENCIADAS
# ======================================================================
class BlueskyMutedWordEditDialog(wx.Dialog):
    """Alta o edición de una palabra silenciada."""

    _DONDE = [(_("En el texto y en las etiquetas"), ["content", "tag"]),
              (_("Solo en las etiquetas (#)"), ["tag"])]

    def __init__(self, parent, palabra=None):
        titulo = _("Editar palabra silenciada") if palabra else _("Añadir palabra silenciada")
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE, name=titulo)
        self.original = dict(palabra or {})
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("&Palabra, frase o etiqueta (sin #):")), 0,
              wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self.txt = AccessibleTextCtrl(self, value=self.original.get("value", ""),
                                      name=_("Palabra, frase o etiqueta"))
        s.Add(self.txt, 0, wx.EXPAND | wx.ALL, 10)
        s.Add(wx.StaticText(self, label=_("&Dónde silenciarla:")), 0, wx.LEFT | wx.RIGHT, 10)
        self.ch_donde = wx.Choice(self, choices=[t for t, _v in self._DONDE],
                                  name=_("Dónde silenciarla"))
        targets = self.original.get("targets") or ["content", "tag"]
        self.ch_donde.SetSelection(0 if "content" in targets else 1)
        s.Add(self.ch_donde, 0, wx.EXPAND | wx.ALL, 10)
        self.chk_seguidos = wx.CheckBox(self, label=_("&Excepto en las cuentas que sigo"))
        self.chk_seguidos.SetValue(self.original.get("actorTarget") == "exclude-following")
        s.Add(self.chk_seguidos, 0, wx.ALL, 10)
        s.Add(wx.StaticText(self, label=_("D&uración:")), 0, wx.LEFT | wx.RIGHT, 10)
        self._duraciones = list(DURACIONES)
        exp = self.original.get("expiresAt") or ""
        if exp:
            self._duraciones.insert(0, (_("Mantener (caduca el {0})").format(_fecha_local(exp)),
                                        "mantener"))
        self.ch_dur = wx.Choice(self, choices=[t for t, _v in self._duraciones],
                                name=_("Duración"))
        self.ch_dur.SetSelection(0)
        s.Add(self.ch_dur, 0, wx.EXPAND | wx.ALL, 10)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        b_ok.SetDefault()
        b_ok.Bind(wx.EVT_BUTTON, self._on_ok)
        fila.Add(b_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizerAndFit(s)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self.txt.SetFocus()

    def _on_ok(self, event):
        if not self.txt.GetValue().strip():
            mostrar_info(self, _("Escribe la palabra que quieres silenciar."), _("Falta la palabra"))
            self.txt.SetFocus()
            return
        self.EndModal(wx.ID_OK)

    def valor(self) -> dict:
        _t, dur = self._duraciones[self.ch_dur.GetSelection()]
        if dur == "mantener":
            exp = self.original.get("expiresAt") or ""
        elif dur is None:
            exp = ""
        else:
            exp = (datetime.now(timezone.utc) + dur).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z")
        return {"value": self.txt.GetValue().strip().lstrip("#") if
                self.ch_donde.GetSelection() == 1 else self.txt.GetValue().strip(),
                "targets": list(self._DONDE[self.ch_donde.GetSelection()][1]),
                "actorTarget": "exclude-following" if self.chk_seguidos.GetValue() else "all",
                "expiresAt": exp, "id": self.original.get("id", "")}


class BlueskyMutedWordsDialog(wx.Dialog):
    """Gestiona las palabras silenciadas de la cuenta (como la app oficial)."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Palabras silenciadas"), size=(640, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.palabras = []
        self.cambiado = False
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_(
            "Las publicaciones con estas palabras se muestran con un aviso en lugar del texto "
            "(puedes leerlas en detalles). Tus propias publicaciones nunca se silencian.")),
            0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Palabras silenciadas"))
        for i, (col, ancho) in enumerate(((_("Palabra"), 200), (_("Dónde"), 150),
                                          (_("Cuentas"), 140), (_("Caduca"), 130))):
            self.lista.InsertColumn(i, col, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_editar(None))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        from app.core.config_manager import config
        self.chk_filtrar = wx.CheckBox(panel, label=_(
            "&Quitar de las listas las publicaciones silenciadas en lugar de mostrar un aviso"))
        self.chk_filtrar.SetValue(bool(config.get("bluesky", "mutewords_filtrar", False)))
        self.chk_filtrar.Bind(wx.EVT_CHECKBOX, self._on_filtrar)
        s.Add(self.chk_filtrar, 0, wx.ALL, 6)
        s.Add(_botonera(panel, [(_("&Añadir..."), self._on_add), (_("&Editar..."), self._on_editar),
                                (_("&Quitar"), self._on_del)], _("&Cerrar")), 0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CLOSE))
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self._cargar()

    def _cargar(self):
        bluesky_service.obtener_muted_words_async(self.account_id, callback=self._on_lista)

    def _on_lista(self, palabras, error):
        if not _vivo(self):
            return
        if error:
            notificar(_("Error al cargar las palabras silenciadas: {0}").format(error))
            return
        foco = max(0, self.lista.GetFocusedItem())
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self.palabras = list(palabras or [])
        from app.services.bluesky.bluesky_mutewords import caducada
        for i, w in enumerate(self.palabras):
            donde = _("Texto y etiquetas") if "content" in (w.get("targets") or []) else _("Solo etiquetas")
            cuentas = _("Excepto las que sigo") if w.get("actorTarget") == "exclude-following" else _("Todas")
            if not w.get("expiresAt"):
                caduca = _("Nunca")
            elif caducada(w):
                caduca = _("Caducada")
            else:
                caduca = _fecha_local(w["expiresAt"])
            vals = [w.get("value", ""), donde, cuentas, caduca]
            self.lista.InsertItem(i, vals[0])
            for j in range(1, 4):
                self.lista.SetItem(i, j, vals[j])
            self.lista.establecer_lectura(i, ". ".join(vals))
        n = self.lista.GetItemCount()
        if n:
            idx = min(foco, n - 1)
            self.lista.Focus(idx)
            self.lista.Select(idx)

    def _seleccion(self):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.palabras):
            speak(_("Elige primero una palabra de la lista."), interrupt=True)
            return None
        return self.palabras[idx]

    def _tras_guardar(self, mensaje):
        def _cb(r, e):
            if e:
                notificar(_("No se pudo guardar: {0}").format(e))
                return
            self.cambiado = True
            notificar(mensaje)
            if _vivo(self):
                self._cargar()
        return _cb

    def _on_add(self, event):
        dlg = BlueskyMutedWordEditDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            bluesky_service.guardar_muted_word_async(
                self.account_id, dlg.valor(), callback=self._tras_guardar(_("Palabra silenciada.")))
        dlg.Destroy()

    def _on_editar(self, event):
        w = self._seleccion()
        if not w:
            return
        dlg = BlueskyMutedWordEditDialog(self, w)
        if dlg.ShowModal() == wx.ID_OK:
            bluesky_service.guardar_muted_word_async(
                self.account_id, dlg.valor(), original=w,
                callback=self._tras_guardar(_("Palabra silenciada actualizada.")))
        dlg.Destroy()

    def _on_del(self, event):
        w = self._seleccion()
        if not w:
            return
        if not confirmar(self, _("¿Dejar de silenciar «{0}»?").format(w.get("value", "")),
                         _("Quitar palabra silenciada")):
            return
        bluesky_service.quitar_muted_word_async(
            self.account_id, w, callback=self._tras_guardar(_("Palabra quitada.")))

    def _on_filtrar(self, event):
        bluesky_service.fijar_opcion_local_mutewords(self.account_id, self.chk_filtrar.GetValue())
        self.cambiado = True


# ======================================================================
# CONTENIDO PARA ADULTOS Y VISIBILIDAD POR ETIQUETA
# ======================================================================
class BlueskyModeracionDialog(wx.Dialog):
    """Contenido para adultos y qué hacer con cada etiqueta (global y por servicio)."""

    def __init__(self, parent, account_id, labeler_did=None, al_guardar=None):
        super().__init__(parent, title=_("Preferencias de moderación de Bluesky"), size=(680, 600),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.labeler_inicial = labeler_did
        self.al_guardar = al_guardar
        self.servicios = []          # vistas de labelers con sus definiciones
        self.prefs = bluesky_service.prefs_moderacion(account_id)
        self.cambios_labeler = {}    # did -> {etiqueta: visibilidad}
        panel = wx.Panel(self)
        self.panel = panel
        s = wx.BoxSizer(wx.VERTICAL)
        self.chk_adulto = wx.CheckBox(panel, label=_("Permitir contenido para &adultos"))
        self.chk_adulto.SetValue(bool(self.prefs.get("adult_enabled")))
        self.chk_adulto.Bind(wx.EVT_CHECKBOX, lambda e: self._actualizar_habilitados())
        s.Add(self.chk_adulto, 0, wx.ALL, 8)
        s.Add(wx.StaticText(panel, label=_("Etiquetas generales (qué hacer con el contenido marcado):")),
              0, wx.LEFT | wx.RIGHT, 8)
        from app.services.bluesky.bluesky_moderacion import GLOBALES_CONFIGURABLES, LABELS
        self._globales = {}
        rejilla = wx.FlexGridSizer(cols=2, vgap=4, hgap=8)
        for clave in GLOBALES_CONFIGURABLES:
            etq = NOMBRES_GLOBALES.get(clave, clave)
            rejilla.Add(wx.StaticText(panel, label=etq + ":"), 0, wx.ALIGN_CENTER_VERTICAL)
            ch = wx.Choice(panel, choices=[t for _v, t in OPCIONES_VIS], name=etq)
            actual = (self.prefs.get("labels") or {}).get(clave, LABELS[clave]["defaultSetting"])
            ch.SetSelection(next((i for i, (v, _t) in enumerate(OPCIONES_VIS) if v == actual), 1))
            self._globales[clave] = (ch, actual, "adult" in LABELS[clave]["flags"])
            rejilla.Add(ch, 0)
        s.Add(rejilla, 0, wx.ALL, 8)
        s.Add(wx.StaticText(panel, label=_("&Servicio de etiquetado:")), 0, wx.LEFT | wx.RIGHT, 8)
        self.ch_servicio = wx.Choice(panel, choices=[_("Cargando...")], name=_("Servicio de etiquetado"))
        self.ch_servicio.SetSelection(0)
        self.ch_servicio.Bind(wx.EVT_CHOICE, lambda e: self._pintar_etiquetas())
        s.Add(self.ch_servicio, 0, wx.EXPAND | wx.ALL, 8)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Etiquetas del servicio"))
        for i, (col, ancho) in enumerate(((_("Etiqueta"), 200), (_("Qué hacer"), 110),
                                          (_("Descripción"), 320))):
            self.lista.InsertColumn(i, col, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_cambiar(None))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_cambiar = wx.Button(panel, label=_("Cam&biar qué hacer con la etiqueta..."))
        b_cambiar.Bind(wx.EVT_BUTTON, self._on_cambiar)
        fila.Add(b_cambiar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        b_ok = wx.Button(panel, wx.ID_OK, _("&Aceptar"))
        b_ok.Bind(wx.EVT_BUTTON, self._on_aceptar)
        fila.Add(b_ok, 0, wx.ALL, 4)
        fila.Add(wx.Button(panel, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        habilitar_escape_cierre(self, wx.ID_CANCEL)
        self._actualizar_habilitados()
        bluesky_service.obtener_labelers_async(account_id, callback=self._on_servicios)

    def _actualizar_habilitados(self):
        adulto = self.chk_adulto.GetValue()
        for clave, (ch, _a, es_adulta) in self._globales.items():
            # Como la app oficial: sin contenido para adultos, sus etiquetas quedan ocultas y
            # no se pueden cambiar.
            ch.Enable(adulto or not es_adulta)

    def _on_servicios(self, res, error):
        if not _vivo(self):
            return
        if error:
            notificar(_("No se pudieron cargar los servicios de etiquetado: {0}").format(error))
            res = []
        self.servicios = list(res or [])
        self.prefs = bluesky_service.prefs_moderacion(self.account_id)
        self.chk_adulto.SetValue(bool(self.prefs.get("adult_enabled")))
        self._actualizar_habilitados()
        self.ch_servicio.Clear()
        if not self.servicios:
            self.ch_servicio.Append(_("(Ninguno)"))
            self.ch_servicio.SetSelection(0)
            return
        sel = 0
        for i, srv in enumerate(self.servicios):
            self.ch_servicio.Append(f"{srv.get('name') or srv.get('handle')} (@{srv.get('handle')})")
            if self.labeler_inicial and srv.get("did") == self.labeler_inicial:
                sel = i
        self.ch_servicio.SetSelection(sel)
        self._pintar_etiquetas()

    def _servicio_actual(self):
        i = self.ch_servicio.GetSelection()
        if 0 <= i < len(self.servicios):
            return self.servicios[i]
        return None

    def _vis_de(self, did, defn):
        cambio = (self.cambios_labeler.get(did) or {}).get(defn["identifier"])
        if cambio:
            return cambio
        for lab in self.prefs.get("labelers") or []:
            if lab["did"] == did and lab["labels"].get(defn["identifier"]):
                return lab["labels"][defn["identifier"]]
        return defn.get("defaultSetting") or "warn"

    def _pintar_etiquetas(self):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        srv = self._servicio_actual()
        if not srv:
            return
        self._defs = [d for d in (srv.get("etiquetas") or []) if not d["identifier"].startswith("!")]
        for i, d in enumerate(self._defs):
            vis = NOMBRE_VIS.get(self._vis_de(srv["did"], d), "")
            if d.get("adultOnly") and not self.chk_adulto.GetValue():
                vis = _("Ocultar (contenido para adultos desactivado)")
            vals = [d.get("name") or d["identifier"], vis, d.get("description") or ""]
            self.lista.InsertItem(i, vals[0])
            self.lista.SetItem(i, 1, vals[1])
            self.lista.SetItem(i, 2, vals[2])
            self.lista.establecer_lectura(i, ". ".join(v for v in vals if v))
        if not self._defs:
            speak(_("Este servicio no define etiquetas propias."), interrupt=False)

    def _on_cambiar(self, event):
        srv = self._servicio_actual()
        idx = self.lista.GetFocusedItem()
        defs = getattr(self, "_defs", [])
        if not srv or idx < 0 or idx >= len(defs):
            speak(_("Elige primero una etiqueta de la lista."), interrupt=True)
            return
        d = defs[idx]
        if d.get("adultOnly") and not self.chk_adulto.GetValue():
            mostrar_info(self, _("Es una etiqueta de contenido para adultos: activa primero "
                                 "«Permitir contenido para adultos»."), _("Contenido para adultos"))
            return
        i = elegir_opcion(self, _("¿Qué hacer con «{0}»?").format(d.get("name") or d["identifier"]),
                          _("Etiqueta"), ["&" + t for _v, t in OPCIONES_VIS])
        if i is None:
            return
        self.cambios_labeler.setdefault(srv["did"], {})[d["identifier"]] = OPCIONES_VIS[i][0]
        self._pintar_etiquetas()
        if idx < self.lista.GetItemCount():
            self.lista.Focus(idx)
            self.lista.Select(idx)

    def _on_aceptar(self, event):
        adulto = self.chk_adulto.GetValue()
        globales = {}
        for clave, (ch, actual, _es) in self._globales.items():
            nuevo = OPCIONES_VIS[ch.GetSelection()][0]
            if nuevo != actual:
                globales[clave] = nuevo
        cambio_adulto = adulto != bool(self.prefs.get("adult_enabled"))
        if not globales and not self.cambios_labeler and not cambio_adulto:
            self.EndModal(wx.ID_CANCEL)
            return
        al_guardar = self.al_guardar

        def _cb(r, e):
            if e:
                notificar(_("No se pudieron guardar las preferencias de moderación: {0}").format(e))
                return
            notificar(_("Preferencias de moderación guardadas."))
            if al_guardar:
                al_guardar()
        bluesky_service.guardar_moderacion_async(
            self.account_id, adulto=adulto if cambio_adulto else None, globales=globales,
            por_labeler=self.cambios_labeler, callback=_cb)
        self.EndModal(wx.ID_OK)


# ======================================================================
# SERVICIOS DE ETIQUETADO
# ======================================================================
class BlueskyLabelersDialog(wx.Dialog):
    """Servicios de etiquetado: Bluesky Moderation (siempre activo) y los suscritos."""

    def __init__(self, parent, account_id, ajustar=None):
        super().__init__(parent, title=_("Servicios de etiquetado"), size=(700, 460),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.ajustar = ajustar
        self.datos = []
        self.cambiado = False
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Servicios de etiquetado"))
        for i, (col, ancho) in enumerate(((_("Servicio"), 220), (_("Usuario"), 200),
                                          (_("Estado"), 170), (_("Etiquetas"), 80))):
            self.lista.InsertColumn(i, col, width=ancho)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(_botonera(panel, [(_("&Suscribirse o darse de baja"), self._on_alternar),
                                (_("&Añadir un servicio por usuario..."), self._on_anadir),
                                (_("Ajustar sus &etiquetas..."), self._on_ajustar),
                                (_("Ac&tualizar"), lambda e: self._cargar())], _("&Cerrar")),
              0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self._cargar()

    def _cargar(self):
        speak(_("Cargando servicios de etiquetado..."), interrupt=True)
        bluesky_service.obtener_labelers_async(self.account_id, callback=self._on_datos)

    def _on_datos(self, res, error):
        if not _vivo(self):
            return
        if error:
            notificar(_("No se pudieron cargar: {0}").format(error))
            return
        self.datos = list(res or [])
        self._pintar()

    def _pintar(self, foco=None):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, srv in enumerate(self.datos):
            if srv.get("oficial"):
                estado = _("Oficial (siempre activo)")
            elif srv.get("suscrito"):
                estado = _("Suscrito")
            else:
                estado = _("No suscrito")
            vals = [srv.get("name") or srv.get("handle", ""), "@" + (srv.get("handle") or ""),
                    estado, str(len([d for d in srv.get("etiquetas") or []
                                     if not d["identifier"].startswith("!")]))]
            self.lista.InsertItem(i, vals[0])
            for j in range(1, 4):
                self.lista.SetItem(i, j, vals[j])
            self.lista.establecer_lectura(
                i, ". ".join([vals[0], vals[1], estado,
                              _("{0} etiquetas").format(vals[3]), srv.get("descripcion", "")]))
        n = self.lista.GetItemCount()
        if n:
            idx = min(foco if foco is not None else 0, n - 1)
            self.lista.Focus(idx)
            self.lista.Select(idx)

    def _sel(self):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.datos):
            speak(_("Elige primero un servicio de la lista."), interrupt=True)
            return None, -1
        return self.datos[idx], idx

    def _on_alternar(self, event):
        srv, idx = self._sel()
        if not srv:
            return
        if srv.get("oficial"):
            mostrar_info(self, _("El servicio de moderación de Bluesky está siempre activo y no "
                                 "se puede quitar."), _("Servicio oficial"))
            return
        suscribir = not srv.get("suscrito")

        def _cb(r, e):
            if e:
                notificar(_("Error: {0}").format(e))
                return
            self.cambiado = True
            srv["suscrito"] = suscribir
            notificar(_("Te has suscrito a «{0}».").format(srv.get("name")) if suscribir
                      else _("Te has dado de baja de «{0}».").format(srv.get("name")))
            if _vivo(self):
                self._pintar(idx)
        bluesky_service.suscribir_labeler_async(self.account_id, srv["did"], suscribir, callback=_cb)

    def _on_anadir(self, event):
        dlg = CustomTextEntryDialog(self, _("Usuario (nombre.bsky.social) o DID del servicio de "
                                            "etiquetado:"), _("Añadir un servicio"))
        r = dlg.ShowModal()
        valor = dlg.GetValue().strip()
        dlg.Destroy()
        if r != wx.ID_OK or not valor:
            return

        def _cb(res, e):
            if e:
                notificar(_("No se pudo encontrar el servicio: {0}").format(e))
                return
            if not _vivo(self):
                return
            existente = next((i for i, d in enumerate(self.datos) if d["did"] == res["did"]), None)
            if existente is None:
                self.datos.append(res)
                existente = len(self.datos) - 1
            self._pintar(existente)
            speak(_("Servicio encontrado: {0}. Pulsa «Suscribirse» para activarlo.").format(
                res.get("name")), interrupt=True)
        bluesky_service.buscar_labeler_async(self.account_id, valor, callback=_cb)

    def _on_ajustar(self, event):
        srv, _i = self._sel()
        if not srv or not self.ajustar:
            return
        if not srv.get("suscrito"):
            mostrar_info(self, _("Suscríbete primero a este servicio para ajustar sus etiquetas."),
                         _("Servicio no suscrito"))
            return
        self.ajustar(labeler_did=srv["did"])


# ======================================================================
# PUBLICACIONES OCULTADAS
# ======================================================================
class BlueskyHiddenPostsDialog(wx.Dialog):
    """Publicaciones que ocultaste con "Ocultar esta publicación para mí"."""

    def __init__(self, parent, account_id):
        super().__init__(parent, title=_("Publicaciones ocultadas"), size=(640, 420),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.account_id = account_id
        self.cambiado = False
        panel = wx.Panel(self)
        s = wx.BoxSizer(wx.VERTICAL)
        self.lista = AccessibleListCtrl(panel, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Publicaciones ocultadas"))
        self.lista.InsertColumn(0, _("Autor"), width=180)
        self.lista.InsertColumn(1, _("Publicación"), width=420)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        s.Add(_botonera(panel, [(_("&Dejar de ocultar"), self._on_mostrar)], _("&Cerrar")),
              0, wx.ALIGN_CENTER)
        panel.SetSizer(s)
        sz = wx.BoxSizer(wx.VERTICAL)
        sz.Add(panel, 1, wx.EXPAND)
        self.SetSizer(sz)
        self.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        habilitar_escape_cierre(self, wx.ID_CLOSE)
        self.uris = []
        self._pintar()
        bluesky_service.refrescar_prefs_moderacion_async(
            account_id, callback=lambda r, e: self._pintar() if (not e and _vivo(self)) else None)

    def _pintar(self):
        self.uris = list(reversed(bluesky_service.publicaciones_ocultas(self.account_id)))
        foco = max(0, self.lista.GetFocusedItem())
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        for i, uri in enumerate(self.uris):
            fila = db.query_one("SELECT author_name, author_handle, content FROM bluesky_posts "
                                "WHERE id = ?", (f"{self.account_id}:{uri}",))
            autor = (fila[0] or fila[1]) if fila else ""
            texto = ((fila[2] or "").replace("\n", " ") if fila else "") or uri
            self.lista.InsertItem(i, autor)
            self.lista.SetItem(i, 1, texto)
            self.lista.establecer_lectura(i, f"{autor}: {texto}" if autor else texto)
        n = self.lista.GetItemCount()
        if n:
            self.lista.Focus(min(foco, n - 1))
            self.lista.Select(min(foco, n - 1))

    def _on_mostrar(self, event):
        idx = self.lista.GetFocusedItem()
        if idx < 0 or idx >= len(self.uris):
            speak(_("Elige primero una publicación de la lista."), interrupt=True)
            return
        uri = self.uris[idx]

        def _cb(r, e):
            if e:
                notificar(_("No se pudo guardar: {0}").format(e))
                return
            notificar(_("La publicación vuelve a mostrarse."))
        bluesky_service.ocultar_post_async(self.account_id, uri, False, callback=_cb)
        self.cambiado = True
        self._pintar()


# ======================================================================
# FEED SIGUIENDO E HILOS
# ======================================================================
class BlueskyFeedViewDialog(wx.Dialog):
    """Preferencias del feed Siguiendo (feedViewPref "home") y orden de los hilos."""

    _ORDENES = [("hotness", _("Destacadas (como el servidor)")),
                ("oldest", _("Más antiguas primero")),
                ("newest", _("Más recientes primero")),
                ("most-likes", _("Más «me gusta» primero")),
                ("random", _("Aleatorio"))]

    def __init__(self, parent, account_id, al_guardar=None):
        super().__init__(parent, title=_("Preferencias del feed Siguiendo y de los hilos"),
                         style=wx.DEFAULT_DIALOG_STYLE)
        self.account_id = account_id
        self.al_guardar = al_guardar
        from app.services.bluesky.bluesky_moderacion import prefs_feed_view
        prefs = bluesky_service.prefs_moderacion(account_id)
        self.fv = prefs_feed_view(prefs, "home")
        self.sort = (prefs.get("thread_view") or {}).get("sort") or "hotness"
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Feed Siguiendo (Inicio):")), 0, wx.ALL, 8)
        self.chk_resp = wx.CheckBox(self, label=_("Mostrar &respuestas"))
        self.chk_resp.SetValue(not self.fv.get("hideReplies"))
        s.Add(self.chk_resp, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.chk_seguidos = wx.CheckBox(self, label=_(
            "Solo respuestas entre cuentas que &sigo (oculta las respuestas a quien no sigues)"))
        self.chk_seguidos.SetValue(bool(self.fv.get("hideRepliesByUnfollowed")))
        s.Add(self.chk_seguidos, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        s.Add(wx.StaticText(self, label=_("&Mínimo de «me gusta» para mostrar una respuesta "
                                          "(0 = sin mínimo):")), 0, wx.LEFT | wx.RIGHT, 8)
        self.spin = wx.SpinCtrl(self, min=0, max=1000,
                                initial=int(self.fv.get("hideRepliesByLikeCount") or 0),
                                name=_("Mínimo de me gusta para mostrar una respuesta"))
        s.Add(self.spin, 0, wx.ALL, 8)
        self.chk_reposts = wx.CheckBox(self, label=_("Mostrar re&posts"))
        self.chk_reposts.SetValue(not self.fv.get("hideReposts"))
        s.Add(self.chk_reposts, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.chk_citas = wx.CheckBox(self, label=_("Mostrar publicaciones que &citan a otras"))
        self.chk_citas.SetValue(not self.fv.get("hideQuotePosts"))
        s.Add(self.chk_citas, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        s.Add(wx.StaticText(self, label=_("&Orden de las respuestas en los hilos:")), 0,
              wx.LEFT | wx.RIGHT, 8)
        self.ch_orden = wx.Choice(self, choices=[t for _v, t in self._ORDENES],
                                  name=_("Orden de las respuestas en los hilos"))
        self.ch_orden.SetSelection(next((i for i, (v, _t) in enumerate(self._ORDENES)
                                         if v == self.sort), 0))
        s.Add(self.ch_orden, 0, wx.EXPAND | wx.ALL, 8)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        b_ok.SetDefault()
        b_ok.Bind(wx.EVT_BUTTON, self._on_aceptar)
        fila.Add(b_ok, 0, wx.ALL, 5)
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0, wx.ALL, 5)
        s.Add(fila, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizerAndFit(s)
        habilitar_escape_cierre(self, wx.ID_CANCEL)

    def _on_aceptar(self, event):
        valores = {"hideReplies": not self.chk_resp.GetValue(),
                   "hideRepliesByUnfollowed": self.chk_seguidos.GetValue(),
                   "hideRepliesByLikeCount": int(self.spin.GetValue()),
                   "hideReposts": not self.chk_reposts.GetValue(),
                   "hideQuotePosts": not self.chk_citas.GetValue()}
        sort = self._ORDENES[self.ch_orden.GetSelection()][0]
        al_guardar = self.al_guardar
        cambia_fv = any(valores[k] != self.fv.get(k) for k in valores)
        cambia_sort = sort != self.sort

        def _cb(r, e):
            if e:
                notificar(_("No se pudieron guardar las preferencias: {0}").format(e))
                return
            notificar(_("Preferencias guardadas."))
            if al_guardar:
                al_guardar()
        if cambia_fv:
            bluesky_service.fijar_feed_view_async(self.account_id, valores, "home",
                                                  callback=_cb if not cambia_sort else None)
        if cambia_sort:
            bluesky_service.fijar_thread_view_async(self.account_id, sort, callback=_cb)
        self.EndModal(wx.ID_OK if (cambia_fv or cambia_sort) else wx.ID_CANCEL)
