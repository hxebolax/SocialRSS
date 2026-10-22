# -*- coding: utf-8 -*-
"""Visor de CONVERSACIÓN de Bluesky con paridad con el de Mastodon (petición de usuarios).

Antes, "Ver conversación" en Bluesky mostraba un volcado de texto de solo lectura; el de
Mastodon (thread_viewer.py) ofrece lista navegable con detalle, menú de acciones sobre
cada mensaje, abrir como buffer, resumen con IA, traducción y exportación. Este diálogo
iguala todo eso delegando las acciones en el controlador de Bluesky ya existente.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import json
import logging

import wx

from app.core.speech import speak, notificar, copiar_al_portapapeles
from app.core.task_manager import task_manager
from app.services.bluesky import bluesky_service

log = logging.getLogger(__name__)


def _url_web(uri: str) -> str:
    """at://did/app.bsky.feed.post/rkey -> https://bsky.app/profile/did/post/rkey."""
    try:
        resto = uri.split("at://", 1)[1]
        did, _col, rkey = resto.split("/", 2)
        return f"https://bsky.app/profile/{did}/post/{rkey}"
    except Exception:
        return uri


class BlueskyThreadViewerDialog(wx.Dialog):
    """Visor accesible de una conversación de Bluesky: raíz, padres y respuestas en
    orden de lectura, con acciones completas sobre el mensaje enfocado."""

    def __init__(self, parent, account_id: int, uri: str):
        super().__init__(
            parent, title=_("Visor de Conversación de Bluesky"), size=(860, 620),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo visor de conversación de Bluesky"))
        self.frame = parent
        self.account_id = account_id
        self.uri = uri
        self.posts = []          # BlueskyPost en orden de lectura
        self._init_ui()
        self.CenterOnParent()
        self._cargar()

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_(
            "Cadena de mensajes (orden de lectura). Tecla Aplicaciones o botón Acciones "
            "para actuar sobre el mensaje enfocado.")), 0, wx.ALL, 6)
        from .accessible_controls import AccessibleListCtrl
        self.lista = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Mensajes de la conversación"))
        self.lista.InsertColumn(0, _("Nº"), width=50)
        self.lista.InsertColumn(1, _("Autor"), width=170)
        self.lista.InsertColumn(2, _("Mensaje"), width=430)
        self.lista.InsertColumn(3, _("Fecha"), width=140)
        self.lista.InsertColumn(4, _("En respuesta a"), width=200)
        self.lista.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_seleccion)
        self.lista.Bind(wx.EVT_CONTEXT_MENU, lambda e: self._menu())
        self.lista.Bind(wx.EVT_KEY_DOWN, self._on_key)
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)

        s.Add(wx.StaticText(self, label=_("Texto completo del mensaje:")), 0, wx.LEFT, 6)
        from .accessible_controls import AccessibleTextCtrl
        self.detalle = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120),
            name=_("Texto completo del mensaje seleccionado"),
            ayuda_texto=_("Contenido íntegro del post enfocado para lectura con el lector."))
        s.Add(self.detalle, 0, wx.EXPAND | wx.ALL, 6)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_acciones = wx.Button(self, label=_("&Acciones..."),
                                      name=_("Acciones sobre el mensaje enfocado"))
        self.btn_acciones.Bind(wx.EVT_BUTTON, lambda e: self._menu())
        fila.Add(self.btn_acciones, 0, wx.ALL, 4)
        self.btn_buffer = wx.Button(self, label=_("Abrir como &buffer"),
                                    name=_("Abrir esta conversación como buffer"))
        self.btn_buffer.Bind(wx.EVT_BUTTON, self._on_abrir_como_buffer)
        fila.Add(self.btn_buffer, 0, wx.ALL, 4)
        self.btn_resumir = wx.Button(self, label=_("&Resumir hilo con IA"),
                                     name=_("Resumir conversación"))
        self.btn_resumir.Bind(wx.EVT_BUTTON, self._on_resumir)
        fila.Add(self.btn_resumir, 0, wx.ALL, 4)
        self.btn_traducir = wx.Button(self, label=_("&Traducir mensaje"),
                                      name=_("Traducir el mensaje enfocado"))
        self.btn_traducir.Bind(wx.EVT_BUTTON, self._on_traducir)
        fila.Add(self.btn_traducir, 0, wx.ALL, 4)
        self.btn_exportar = wx.Button(self, label=_("&Exportar hilo..."),
                                      name=_("Exportar conversación"))
        self.btn_exportar.Bind(wx.EVT_BUTTON, self._on_exportar)
        fila.Add(self.btn_exportar, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        btn_cerrar = wx.Button(self, wx.ID_CANCEL, _("&Cerrar"), name=_("Cerrar visor"))
        fila.Add(btn_cerrar, 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND | wx.ALL, 4)
        self.SetSizer(s)
        self.Bind(wx.EVT_CLOSE, lambda e: self.EndModal(wx.ID_CANCEL))
        from .accessible_controls import habilitar_escape_cierre
        habilitar_escape_cierre(self, wx.ID_CANCEL)

    # ------------------------------------------------------------------ datos
    def _cargar(self):
        speak(_("Cargando la conversación desde el servidor..."), interrupt=True)

        def _fin(posts, err):
            if not self:
                return
            if err:
                # Mensaje en cristiano: antes se soltaba el repr del Response de atproto
                # (con cabeceras HTTP incluidas), ilegible con el lector de pantalla.
                try:
                    detalle = bluesky_service._mensaje_error(err)
                except Exception:
                    detalle = str(err)
                if "no se ha encontrado" in detalle.lower():
                    detalle = _("La publicación ya no está disponible: puede haberse borrado "
                                "o ser de una cuenta que te ha bloqueado.")
                notificar(_("No se pudo cargar la conversación: {0}").format(detalle))
                return
            self.posts = posts or []
            self._pintar()
            speak(_("Conversación cargada: {0} mensajes. Enfoca la lista para leer e "
                    "interactuar.").format(len(self.posts)), interrupt=True)
        bluesky_service.obtener_hilo_async(self.account_id, self.uri, callback=_fin)

    def _respuesta_a(self, p, autores):
        """Texto de a quién responde `p` dentro de esta conversación. Si el padre no ha llegado
        (borrado, de una cuenta bloqueada o que te bloquea), se dice en vez de callarlo: antes
        la respuesta aparecía sin nada encima y no había forma de saber a qué contestaba."""
        padre = getattr(p, "reply_parent_uri", None) or ""
        if not padre:
            return ""
        handle = autores.get(padre)
        if handle:
            return _("En respuesta a @{0}").format(handle)
        return _("En respuesta a una publicación no disponible")

    def _pintar(self):
        from app.ui.main_frame import formatear_tiempo_relativo
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        autores = {p.uri: (p.author_handle or p.author_name or "") for p in self.posts}
        self._mods = self._moderar_posts()
        for i, p in enumerate(self.posts):
            cuerpo = (p.content or "").replace("\n", " ")
            mod = self._mods[i] if i < len(self._mods) else None
            if mod and (mod["difuminar"] or mod["filtrar"]):
                # En el hilo no se quitan filas (rompería la conversación): lo moderado se
                # sustituye por su aviso y se puede leer en el detalle si se permite (B5).
                from app.services.bluesky.bluesky_moderacion import texto_aviso
                cuerpo = (_("[Contenido retirado por moderación]") if mod["no_override"]
                          else texto_aviso(mod, "lista"))
            autor = p.author_name or p.author_handle or ""
            fecha = formatear_tiempo_relativo(p.created_at) if p.created_at else ""
            respuesta = self._respuesta_a(p, autores)
            self.lista.InsertItem(i, str(i + 1))
            self.lista.SetItem(i, 1, autor)
            self.lista.SetItem(i, 2, cuerpo)
            self.lista.SetItem(i, 3, fecha)
            self.lista.SetItem(i, 4, respuesta)
            extras = []
            if p.labels:
                extras.append(_("Aviso de contenido: {0}").format(p.labels))
            if p.media_attachments and mod and mod["difuminar_media"]:
                from app.services.bluesky.bluesky_moderacion import texto_media_oculta
                extras.append(texto_media_oculta(mod))
            elif p.media_attachments:
                extras.append(_("{0} adjunto(s)").format(len(p.media_attachments)))
            quien = f"{autor}, {respuesta[:1].lower()}{respuesta[1:]}" if respuesta else autor
            self.lista.establecer_lectura(
                i, ". ".join(x for x in ([f"{quien}: {cuerpo}", fecha] + extras) if x))
        if self.posts:
            # Foco en la publicación desde la que se abrió la conversación (su padre queda justo
            # encima), no en la raíz: en un hilo largo había que buscarla a mano.
            inicio = next((i for i, p in enumerate(self.posts) if p.uri == self.uri), 0)
            self.lista.Focus(inicio)
            self.lista.Select(inicio)
            self.lista.EnsureVisible(inicio)

    def _moderar_posts(self):
        """Decisión de moderación de cada mensaje del hilo (etiquetas, cuenta, palabras
        silenciadas, publicaciones ocultadas)."""
        from app.services.bluesky.bluesky_moderacion import moderar_post
        from app.services.bluesky.bluesky_moddatos import de_json
        try:
            prefs = bluesky_service.prefs_moderacion(self.account_id)
        except Exception:
            prefs = {}
        salida = []
        for p in self.posts:
            try:
                salida.append(moderar_post(
                    de_json(getattr(p, "mod_json", None)), prefs,
                    contexto="detalle" if p.uri == self.uri else "lista", uri=p.uri,
                    etiquetas=p.labels, author_did=p.author_did))
            except Exception:
                salida.append(None)
        return salida

    def _post_actual(self):
        idx = self.lista.GetFocusedItem()
        if idx is None or not (0 <= idx < len(self.posts)):
            return None
        return self.posts[idx]

    def _on_seleccion(self, event):
        p = self._post_actual()
        if p is not None:
            idx = self.lista.GetFocusedItem()
            mods = getattr(self, "_mods", [])
            mod = mods[idx] if 0 <= idx < len(mods) else None
            from app.services.bluesky.bluesky_moderacion import texto_aviso, texto_media_oculta
            if mod and mod["no_override"] and (mod["difuminar"] or mod["filtrar"]):
                partes = [_("[Contenido retirado por moderación: no puede mostrarse]")]
            else:
                partes = []
                if mod and (mod["difuminar"] or mod["filtrar"]):
                    partes.append(texto_aviso(mod, "detalle"))
                partes.append(p.content or "")
                if p.media_attachments and mod and mod["difuminar_media"]:
                    partes.append(texto_media_oculta(mod))
                else:
                    for m in (p.media_attachments or []):
                        alt = (m.get("alt") or "").strip()
                        partes.append(_("[{0}: {1}]").format(m.get("type", "media"),
                                                             alt or _("sin descripción")))
            self.detalle.SetValue("\n\n".join(x for x in partes if x))
        event.Skip()

    # ------------------------------------------------------------------ acciones
    def _ctrl(self):
        return getattr(self.frame, "bluesky_ctrl", None)

    def _accion(self, cual):
        p = self._post_actual()
        ctrl = self._ctrl()
        if p is None or ctrl is None:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        # El controlador trabaja con el buffer/cuenta activos del frame: fijarlos al post.
        self.frame.active_bluesky_account_id = self.account_id
        if cual == "responder":
            ctrl.responder(p.id)
        elif cual == "citar":
            ctrl.citar(p.id)
        elif cual == "favorito":
            ctrl.accion(p.id, "unlike" if p.liked_uri else "like")
        elif cual == "repost":
            ctrl.accion(p.id, "unrepost") if p.reposted_uri else ctrl.repostear(p.id)
        elif cual == "marcador":
            ctrl.marcador(p.id, not p.bookmarked)
        elif cual == "traducir":
            ctrl.traducir(p.id)
        elif cual == "navegador":
            from app.core.privacy import abrir_url, limpiar_url
            abrir_url(limpiar_url(_url_web(p.uri)))
        elif cual == "copiar":
            copiar_al_portapapeles(_url_web(p.uri), _("Enlace copiado al portapapeles."))
        elif cual == "reportar":
            ctrl.reportar_post(p.id)
        elif cual == "autor":
            ctrl.ver_perfil(p.author_did)
        elif cual == "descripcion":
            self._ver_descripcion(p)

    def _menu(self):
        if self._post_actual() is None:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        opciones = [
            (_("Responder (Ctrl+R)"), "responder"),
            (_("Citar (Ctrl+Mayús+U)"), "citar"),
            (_("Me gusta / quitar me gusta (Ctrl+B)"), "favorito"),
            (_("Repostear / deshacer repost"), "repost"),
            (_("Añadir o quitar de marcadores"), "marcador"),
            (_("Traducir (Ctrl+T)"), "traducir"),
            (_("Ver descripción de la multimedia"), "descripcion"),
            (_("Abrir en el navegador"), "navegador"),
            (_("Copiar enlace"), "copiar"),
            (_("Reportar publicación..."), "reportar"),
            (_("Ver perfil del autor..."), "autor"),
        ]
        menu = wx.Menu()
        for etiqueta, clave in opciones:
            it = menu.Append(wx.ID_ANY, etiqueta)
            self.Bind(wx.EVT_MENU, lambda e, c=clave: self._accion(c), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _on_key(self, event):
        k = event.GetKeyCode()
        ctrl = event.ControlDown()
        shift = event.ShiftDown()
        if ctrl and not shift and k == ord("R"):
            self._accion("responder")
        elif ctrl and shift and k == ord("U"):
            self._accion("citar")
        elif ctrl and not shift and k == ord("B"):
            self._accion("favorito")
        elif ctrl and not shift and k == ord("T"):
            self._accion("traducir")
        elif ctrl and shift and k == ord("D"):
            self._accion("descripcion")
        elif k == wx.WXK_WINDOWS_MENU:
            self._menu()
        else:
            event.Skip()

    def _ver_descripcion(self, p):
        descripciones = [(m.get("alt") or "").strip()
                         for m in (p.media_attachments or []) if (m.get("alt") or "").strip()]
        from .accessible_controls import mostrar_info
        if not descripciones:
            speak(_("Esta publicación no tiene descripción en su multimedia."), interrupt=True)
            return
        texto = "\n\n".join(_("Adjunto {0}: {1}").format(i + 1, d)
                            for i, d in enumerate(descripciones))
        mostrar_info(self, texto, _("Descripción de la multimedia"))

    def _on_abrir_como_buffer(self, event):
        p = self._post_actual() or (self.posts[0] if self.posts else None)
        ctrl = self._ctrl()
        if p is None or ctrl is None:
            return
        self.frame.active_bluesky_account_id = self.account_id
        self.EndModal(wx.ID_CANCEL)
        ctrl.crear_buffer_conversacion(p.id)

    # ------------------------------------------------------------------ IA / traducción / exportar
    def _texto_hilo(self):
        lineas = []
        for i, p in enumerate(self.posts):
            autor = p.author_name or p.author_handle or ""
            lineas.append(f"Post {i + 1} (@{p.author_handle or ''} {autor}): "
                          f"{(p.content or '').strip()}")
        return "\n\n".join(lineas)

    def _on_resumir(self, event):
        if not self.posts:
            return
        texto = self._texto_hilo()
        speak(_("Resumiendo la conversación completa..."), interrupt=True)

        def _bg(ctx):
            from app.services.ai.ai_service import ai_service
            return ai_service.resumir_texto(
                "Resume este hilo de conversación de Bluesky:\n\n" + texto)

        def _fin(res, err):
            if err:
                notificar(_("Error al resumir el hilo: {0}").format(err))
            else:
                from app.ui.html_viewer import mostrar_resultado
                mostrar_resultado(self, _("Resumen de conversación con IA"), res)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_traducir(self, event):
        p = self._post_actual()
        if p is None:
            speak(_("Enfoca primero un mensaje del hilo."), interrupt=True)
            return
        texto = (p.content or "").strip()
        if not texto:
            speak(_("Este mensaje no tiene texto que traducir."), interrupt=True)
            return
        speak(_("Traduciendo el mensaje seleccionado..."), interrupt=True)

        def _bg(ctx):
            from app.services.translation.translation_service import translation_service
            return translation_service.traducir(texto, idioma_destino="es")

        def _fin(res, err):
            if err:
                notificar(_("Error de traducción: {0}").format(err))
            else:
                from .accessible_controls import mostrar_info
                mostrar_info(self, res, _("Traducción del mensaje"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_exportar(self, event):
        if not self.posts:
            return
        dlg = wx.FileDialog(
            self, message=_("Exportar conversación como"),
            wildcard="Archivo Markdown (*.md)|*.md|Archivo de texto (*.txt)|*.txt|Archivo JSON (*.json)|*.json",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            try:
                ext = path.split(".")[-1].lower()
                if ext == "json":
                    datos = [{"autor": p.author_name, "handle": p.author_handle,
                              "fecha": p.created_at, "texto": p.content,
                              "uri": p.uri, "url": _url_web(p.uri)} for p in self.posts]
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(datos, f, indent=4, ensure_ascii=False, default=str)
                elif ext == "md":
                    lineas = ["# Conversación de Bluesky\n"]
                    for i, p in enumerate(self.posts):
                        autor = p.author_name or p.author_handle
                        lineas.append(f"### {i + 1}. {autor} (@{p.author_handle}) - {p.created_at}")
                        lineas.append((p.content or "").strip())
                        lineas.append("\n---\n")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lineas))
                else:
                    lineas = []
                    for i, p in enumerate(self.posts):
                        autor = p.author_name or p.author_handle
                        lineas.append(f"[{i + 1}] {autor} ({p.created_at}):\n{(p.content or '').strip()}\n")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write("\n".join(lineas))
                notificar(_("Conversación exportada con éxito."))
            except Exception as e:
                notificar(_("Error al exportar: {0}").format(e))
        dlg.Destroy()
