# -*- coding: utf-8 -*-
"""Conversaciones (hilos) de X: visor accesible y conversación abierta como buffer.

Antes, "Ver conversación" en X mostraba un volcado de texto sin foco ni acciones, y ni siquiera
llegaba: el lector de TweetDetail de twikit rompía con el formato actual de X y el de twscrape,
con una respuesta, devolvía solo el propio tuit. Ahora:

- `XThreadViewerDialog`: lista navegable en orden de lectura (lo que hay ENCIMA, el tuit y las
  respuestas con sus ramas), foco en el tuit desde el que se abrió, "en respuesta a @x" en cada
  mensaje, detalle completo y acciones sobre cualquiera de ellos (responder, me gusta, RT,
  marcador, citar, votar, perfil, cronología, abrir su propia conversación, abrir como buffer).
- `_XHilosMixin` (para XController): abrir el visor, abrir la conversación como buffer
  ("thread:<id>") y las comunidades como buffer ("community:<id>"), con sus nodos del árbol y su
  menú contextual (actualizar / quitar).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.plantillas import pulir_lectura
from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.services.x import x_service
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, habilitar_escape_cierre
from .x_lectura import describir_media_x, texto_cita_x, texto_encuesta_x, datos_encuesta_x

log = logging.getLogger(__name__)


def respuesta_a_en_hilo(d, por_id) -> str:
    """"En respuesta a @x" de un tuit dentro de la conversación cargada: el autor sale del tuit
    al que responde si está en la conversación o, si no, del propio tuit (X lo incluye). Si no
    se sabe de ninguna forma (el original está borrado o protegido) se dice, en vez de dejar la
    respuesta suelta."""
    padre = str(d.get("in_reply_to") or "")
    if not padre:
        return ""
    autor = por_id.get(padre) or d.get("in_reply_to_screen_name") or ""
    if autor:
        return _("En respuesta a @{0}").format(autor)
    return _("En respuesta a una publicación no disponible")


class XThreadViewerDialog(wx.Dialog):
    """Visor accesible de una conversación de X con acciones sobre cada mensaje."""

    def __init__(self, parent, ctrl, account_id, tweet_id):
        super().__init__(parent, title=_("Conversación de X"), size=(900, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Visor de conversación de X"))
        self.ctrl = ctrl
        self.account_id = account_id
        self.tweet_id = str(tweet_id)
        self.tuits = []            # dicts del servicio (con db_id), en orden de lectura
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Conversación en orden de lectura: lo que hay encima, la publicación y sus "
            "respuestas. Tecla Aplicaciones o botón Acciones para actuar sobre la enfocada.")),
            0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Mensajes de la conversación"))
        for i, (titulo, ancho) in enumerate(((_("Nº"), 50), (_("Autor"), 170), (_("Mensaje"), 420),
                                              (_("Fecha"), 130), (_("En respuesta a"), 180))):
            self.lista.InsertColumn(i, titulo, width=ancho)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_seleccion)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._menu())
        self.lista.Bind(wx.EVT_CONTEXT_MENU, lambda e: self._menu())
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Texto completo del mensaje:")), 0, wx.LEFT, 6)
        self.detalle = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120),
            name=_("Texto completo del mensaje seleccionado"),
            ayuda_texto=_("Contenido íntegro del mensaje enfocado, con la cita y la encuesta."))
        s.Add(self.detalle, 0, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, nombre, accion in (
                (_("&Acciones..."), _("Acciones sobre el mensaje enfocado"), lambda e: self._menu()),
                (_("&Responder"), _("Responder al mensaje enfocado"), lambda e: self._responder()),
                (_("Abrir como &buffer"), _("Abrir esta conversación como buffer"),
                 lambda e: self._abrir_como_buffer()),
                (_("Ac&tualizar"), _("Volver a cargar la conversación"), lambda e: self._cargar())):
            b = wx.Button(self, label=etiqueta, name=nombre)
            b.Bind(wx.EVT_BUTTON, accion)
            fila.Add(b, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar"), name=_("Cerrar visor")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        habilitar_escape_cierre(self, wx.ID_CANCEL)

    # ------------------------------------------------------------------ datos
    def _cargar(self):
        speak(_("Cargando la conversación..."), interrupt=True)

        def _fin(res, err):
            if not self:
                return
            if err:
                notificar(_("No se pudo cargar la conversación: {0}").format(err))
                return
            self.tuits = [d for d in (res or []) if d and d.get("tweet_id")]
            self._pintar()
            if not self.tuits:
                notificar(_("La conversación está vacía."))
                return
            speak(_("Conversación cargada: {0} mensajes.").format(len(self.tuits)), interrupt=True)
        x_service.obtener_hilo_async(self.account_id, self.tweet_id, callback=_fin)

    def _texto_completo(self, d) -> str:
        partes = [d.get("text") or ""]
        for extra in (texto_cita_x(d.get("quoted_author"), d.get("quoted_text")),
                      texto_encuesta_x(d.get("poll_json")),
                      describir_media_x(d.get("media_json"))):
            if extra:
                partes.append(extra)
        return "\n\n".join(p for p in partes if p)

    def _pintar(self):
        from app.ui.main_frame import formatear_tiempo_relativo
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        por_id = {str(d["tweet_id"]): d.get("author_screen_name") or "" for d in self.tuits}
        for i, d in enumerate(self.tuits):
            autor = d.get("author_name") or d.get("author_screen_name") or ""
            cuerpo = self._texto_completo(d).replace("\n", " ")
            fecha = formatear_tiempo_relativo(d.get("created_at")) if d.get("created_at") else ""
            respuesta = respuesta_a_en_hilo(d, por_id)
            self.lista.InsertItem(i, str(i + 1))
            self.lista.SetItem(i, 1, autor)
            self.lista.SetItem(i, 2, cuerpo)
            self.lista.SetItem(i, 3, fecha)
            self.lista.SetItem(i, 4, respuesta)
            quien = f"{autor}, {respuesta[:1].lower()}{respuesta[1:]}" if respuesta else autor
            principal = f"{quien}: {cuerpo}".rstrip()
            if fecha:
                # Sin punto doble si el texto ya acaba en signo ("those.. hace 7 horas").
                principal = principal + (" " if principal[-1:] in ".!?…" else ". ") + fecha
            self.lista.establecer_lectura(i, pulir_lectura(principal))
        if self.tuits:
            # Foco en la publicación desde la que se abrió (lo que responde queda encima).
            inicio = next((i for i, d in enumerate(self.tuits)
                           if str(d["tweet_id"]) == self.tweet_id), 0)
            self.lista.Focus(inicio)
            self.lista.Select(inicio)
            self.lista.EnsureVisible(inicio)
            self.lista.SetFocus()

    def _actual(self):
        i = self.lista.GetFocusedItem()
        return self.tuits[i] if 0 <= i < len(self.tuits) else None

    def _on_seleccion(self, event):
        d = self._actual()
        if d is not None:
            self.detalle.SetValue(self._texto_completo(d))
        event.Skip()

    # ------------------------------------------------------------------ acciones
    def _estado(self, d):
        """Estado actual (me gusta, RT, marcador) de la caché, por si cambió con una acción."""
        fila = db.query_one("SELECT liked, retweeted, bookmarked FROM x_tweets WHERE id = ?",
                            (d.get("db_id"),))
        return tuple(fila) if fila else (d.get("liked"), d.get("retweeted"), d.get("bookmarked"))

    def _menu(self):
        d = self._actual()
        if d is None:
            return
        liked, rt, bm = self._estado(d)
        menu = wx.Menu()
        opciones = [
            (_("Responder"), self._responder),
            (_("Citar"), lambda: self.ctrl.citar(d.get("db_id"))),
            (_("Quitar me gusta") if liked else _("Me gusta"),
             lambda: self._accion(d, "unlike" if liked else "like")),
            (_("Deshacer retweet") if rt else _("Retweet"),
             lambda: self._accion(d, "unretweet" if rt else "retweet")),
            (_("Quitar marcador") if bm else _("Añadir a marcadores"),
             lambda: self._accion(d, "unbookmark" if bm else "bookmark")),
        ]
        encuesta = datos_encuesta_x(d.get("poll_json"))
        if encuesta and not encuesta.get("final") and not encuesta.get("selected"):
            opciones.append((_("Votar en la encuesta..."),
                             lambda: self.ctrl.votar_encuesta(d.get("db_id"))))
        if str(d["tweet_id"]) != self.tweet_id:
            opciones.append((_("Ver la conversación de este mensaje"), self._reabrir_en_actual))
        opciones += [
            (_("Ver perfil del autor"), lambda: self.ctrl.ver_perfil(d.get("author_screen_name"))),
            (_("Ver la cronología del autor"),
             lambda: self.ctrl.ver_cronologia_autor(d.get("author_id"), d.get("author_screen_name"))),
            (_("Copiar el texto"), lambda: self._copiar(d)),
            (_("Abrir en el navegador"),
             lambda: self.ctrl._abrir_navegador(d.get("author_screen_name"), d.get("tweet_id"))),
        ]
        for etiqueta, fn in opciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, f=fn: f(), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _responder(self):
        d = self._actual()
        if d is not None:
            self.ctrl.responder(d.get("db_id"))

    def _accion(self, d, accion):
        textos = {"like": _("Me gusta."), "unlike": _("Me gusta quitado."),
                  "retweet": _("Retuiteado."), "unretweet": _("Retweet deshecho."),
                  "bookmark": _("Añadido a marcadores."), "unbookmark": _("Marcador quitado.")}

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                speak(textos.get(accion, _("Hecho.")), interrupt=True)
        x_service.accion_async(self.account_id, d["tweet_id"], accion, callback=_cb)

    def _reabrir_en_actual(self):
        d = self._actual()
        if d is None:
            return
        self.tweet_id = str(d["tweet_id"])
        self._cargar()

    def _copiar(self, d):
        copiar_al_portapapeles(self._texto_completo(d))
        speak(_("Texto copiado."), interrupt=True)

    def _abrir_como_buffer(self):
        d = next((x for x in self.tuits if str(x["tweet_id"]) == self.tweet_id), None)
        texto = (d or {}).get("text") or ""
        autor = (d or {}).get("author_name") or (d or {}).get("author_screen_name") or ""
        self.EndModal(wx.ID_CANCEL)
        self.ctrl.abrir_hilo_como_buffer(self.tweet_id, f"{autor}: {texto}".strip(": "))


class _XHilosMixin:
    """Conversaciones y comunidades de X como buffer, y el visor de conversación."""

    def ver_hilo(self, tweet_id):
        """Ctrl+Mayús+V / "Ver conversación": abre el visor accesible de la conversación."""
        acc = self._cuenta()
        if not acc or not tweet_id:
            return
        dlg = XThreadViewerDialog(self.f, self, acc, tweet_id)
        dlg.ShowModal()
        dlg.Destroy()

    # ---------------- Conversaciones como buffer ----------------
    def abrir_hilo_como_buffer(self, tweet_id, display=""):
        acc = self._cuenta()
        if not acc or not tweet_id:
            return
        guardadas = list(config.get("x", "thread_buffers", []) or [])
        if not any(isinstance(c, dict) and str(c.get("id")) == str(tweet_id)
                   and c.get("account_id") in (None, acc) for c in guardadas):
            guardadas.append({"id": str(tweet_id), "account_id": acc,
                              "display": (display or str(tweet_id)).replace("\n", " ")[:50]})
            config.set("x", "thread_buffers", guardadas)
        self.f._cargar_navegacion()
        self.f._re_seleccionar_x_timeline(acc, "thread:" + str(tweet_id))
        notificar(_("Conversación abierta como buffer."))

    def abrir_hilo_como_buffer_db(self, db_id):
        """Desde el menú contextual de una publicación de la lista."""
        fila = db.query_one("SELECT tweet_id, author_name, text FROM x_tweets WHERE id = ?", (db_id,))
        if fila:
            tid, autor, texto = fila
            self.abrir_hilo_como_buffer(tid, f"{autor or ''}: {texto or ''}")

    def _quitar_hilo(self, tweet_id, acc):
        guardadas = [c for c in (config.get("x", "thread_buffers", []) or [])
                     if not (isinstance(c, dict) and str(c.get("id")) == str(tweet_id)
                             and c.get("account_id") in (None, acc))]
        config.set("x", "thread_buffers", guardadas)
        db.execute("DELETE FROM x_timeline_tweets WHERE account_id = ? AND timeline = ?",
                   (acc, "thread:" + str(tweet_id)))
        self.f._cargar_navegacion()
        notificar(_("Conversación quitada."))

    # ---------------- Comunidades como buffer ----------------
    def abrir_comunidad_como_buffer(self, community_id, nombre=""):
        acc = self._cuenta()
        if not acc or not community_id:
            return
        guardadas = list(config.get("x", "community_buffers", []) or [])
        if not any(isinstance(c, dict) and str(c.get("id")) == str(community_id)
                   and c.get("account_id") in (None, acc) for c in guardadas):
            guardadas.append({"id": str(community_id), "account_id": acc,
                              "display": nombre or str(community_id)})
            config.set("x", "community_buffers", guardadas)
        self.f._cargar_navegacion()
        self.f._re_seleccionar_x_timeline(acc, "community:" + str(community_id))
        notificar(_("Comunidad abierta como buffer."))

    def _quitar_comunidad(self, community_id, acc):
        guardadas = [c for c in (config.get("x", "community_buffers", []) or [])
                     if not (isinstance(c, dict) and str(c.get("id")) == str(community_id)
                             and c.get("account_id") in (None, acc))]
        config.set("x", "community_buffers", guardadas)
        self.f._cargar_navegacion()
        notificar(_("Comunidad quitada del árbol."))

    # ---------------- Árbol ----------------
    def _nodos_dinamicos_extra(self, tree, n_acc, acc, ocultos):
        """Nodos de conversaciones y comunidades abiertas como buffer, bajo la cuenta."""
        for clave, prefijo, plantilla in (
                ("thread_buffers", "thread:", _("Conversación: {0}")),
                ("community_buffers", "community:", _("Comunidad: {0}"))):
            if clave in ocultos:
                continue
            for c in (config.get("x", clave, []) or []):
                if not isinstance(c, dict) or c.get("account_id") not in (None, acc):
                    continue
                etiqueta = plantilla.format(c.get("display") or c.get("id"))
                tree.AppendItem(n_acc, etiqueta, data={
                    "type": "x_timeline", "x_type": prefijo + str(c.get("id")),
                    "account_id": acc, "service": "x", "base_label": etiqueta})

    def _menu_arbol_extra(self, menu, x_type, acc, item) -> bool:
        f = self.f
        if x_type.startswith("thread:"):
            tid = x_type.split(":", 1)[1]
            it = menu.Append(wx.ID_ANY, _("Actualizar la conversación"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            it = menu.Append(wx.ID_ANY, _("Ver en el visor de conversación"))
            f.Bind(wx.EVT_MENU, lambda e: self.ver_hilo(tid), it)
            it = menu.Append(wx.ID_ANY, _("Quitar esta conversación"))
            f.Bind(wx.EVT_MENU, lambda e: self._quitar_hilo(tid, acc), it)
            return True
        if x_type.startswith("community:"):
            cid = x_type.split(":", 1)[1]
            it = menu.Append(wx.ID_ANY, _("Actualizar publicaciones"))
            f.Bind(wx.EVT_MENU, lambda e: (f.tree_nav.SelectItem(item), self.on_refresh()), it)
            it = menu.Append(wx.ID_ANY, _("Salir de la comunidad"))
            f.Bind(wx.EVT_MENU, lambda e: self.comunidad(cid, "leave"), it)
            it = menu.Append(wx.ID_ANY, _("Quitar del árbol"))
            f.Bind(wx.EVT_MENU, lambda e: self._quitar_comunidad(cid, acc), it)
            return True
        return False
