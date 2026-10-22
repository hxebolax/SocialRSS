# -*- coding: utf-8 -*-
"""MainFrame (mixin): servicios_feeds.
Metodos: _nodo_cuenta_enfocada, _buscar_nodo_cuenta, _buffers_de_cuenta, _ir_a_buffer_nodo, _ir_a_buffer_cuenta, _mostrar_dialogo_buffers_cuenta, _servicio_tiene_nodo, _servicio_activo ...
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import logging
import json
import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak



log = logging.getLogger(__name__)


class _Mf_servicios_feeds:
    def _nodo_cuenta_enfocada(self):
        """Devuelve el nodo de CUENTA en el que estás ahora: el seleccionado en el árbol
        (subiendo hasta su cuenta) o, si el foco está en la lista, la cuenta del buffer
        activo. None si no estás dentro de ninguna cuenta."""
        try:
            sel = self.tree_nav.GetSelection()
        except Exception:
            sel = None
        nodo = sel if (sel and sel.IsOk()) else None
        while nodo and nodo.IsOk():
            data = self.tree_nav.GetItemData(nodo)
            if data and data.get("type") == "account":
                return nodo
            nodo = self.tree_nav.GetItemParent(nodo)
        # Respaldo: la cuenta activa (si el foco está en la lista, no en el árbol).
        serv = self._servicio_activo()
        acc = {"mastodon": self.active_mastodon_account_id,
               "bluesky": self.active_bluesky_account_id,
               "rss": self.active_rss_account_id,
               "telegram": self.active_telegram_account_id,
               "x": self.active_x_account_id,
               "wsocial": getattr(self, "active_wsocial_account_id", None)}.get(serv)
        if serv and acc is not None:
            return self._buscar_nodo_cuenta(serv, acc)
        return None

    def _buscar_nodo_cuenta(self, service, account_id):
        """Busca el nodo de cuenta (TreeItemId) de un servicio+id, o None."""
        root = self.tree_nav.GetRootItem()
        if not root or not root.IsOk():
            return None
        encontrado = [None]

        def recorrer(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                if encontrado[0] is None:
                    d = self.tree_nav.GetItemData(child)
                    if d and d.get("type") == "account" and d.get("service") == service \
                            and d.get("account_id") == account_id:
                        encontrado[0] = child
                    elif self.tree_nav.ItemHasChildren(child):
                        recorrer(child)
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
        recorrer(root)
        return encontrado[0]

    def _buffers_de_cuenta(self, nodo_cuenta):
        """Lista [(nodo, etiqueta)] de los buffers de una cuenta, en el orden actual del árbol
        (respeta reordenar y buffers ocultos). El nodo contenedor 'Cronologías de usuarios' no
        es un buffer en sí, así que en su lugar se incluyen sus HIJOS (cada cronología de
        usuario, ya identificada por su etiqueta 'Cronología de ...'); al ir a una de ellas se
        despliega el contenedor automáticamente."""
        buffers = []
        if not nodo_cuenta or not nodo_cuenta.IsOk():
            return buffers
        child, cookie = self.tree_nav.GetFirstChild(nodo_cuenta)
        while child.IsOk():
            d = self.tree_nav.GetItemData(child)
            # Los contenedores ('Cronologías de usuarios', 'Conversaciones') no son un buffer en
            # sí: en su lugar se incluyen sus hijos (cada cronología/hilo, ya identificados).
            if d and d.get("type") in ("user_timelines_root", "thread_buffers_root"):
                sub, subcookie = self.tree_nav.GetFirstChild(child)
                while sub.IsOk():
                    buffers.append((sub, self.tree_nav.GetItemText(sub)))
                    sub, subcookie = self.tree_nav.GetNextChild(child, subcookie)
            elif d and d.get("type") == "category_root":
                # Una rama de PASARELA (WhatsApp, Discord…) es un CONTENEDOR, no un buffer: no cuenta
                # aquí. Sus buffers se navegan aparte, al estar dentro de esa rama (ver
                # _contenedor_buffers_navegables). Así en la rama principal Ctrl+Shift+N solo recorre
                # los buffers del servidor, no la pasarela.
                pass
            elif d:
                buffers.append((child, self.tree_nav.GetItemText(child)))
            child, cookie = self.tree_nav.GetNextChild(nodo_cuenta, cookie)
        return buffers

    def _contenedor_buffers_navegables(self):
        """Nodo cuyos buffers recorren Ctrl+Shift+N y el diálogo de buffers (Ctrl+Shift+L).
        Normalmente es la CUENTA; pero en XMPP, si estás DENTRO de la rama de una pasarela (WhatsApp,
        Discord…), es el nodo de esa pasarela: así te mueves por SUS buffers (conversaciones,
        contactos, grupos, archivados, solicitudes) y no por los del servidor. Devuelve None si no
        estás dentro de ninguna cuenta."""
        try:
            sel = self.tree_nav.GetSelection()
        except Exception:
            sel = None
        nodo = sel if (sel and sel.IsOk()) else None
        puente = None
        while nodo and nodo.IsOk():
            data = self.tree_nav.GetItemData(nodo)
            if data:
                if (data.get("type") == "category_root" and data.get("service") == "xmpp"
                        and data.get("dominio")):
                    puente = nodo   # venimos de dentro de una pasarela: ese será el contenedor
                if data.get("type") == "account":
                    return puente or nodo
            nodo = self.tree_nav.GetItemParent(nodo)
        # Respaldo (foco en la lista, no en el árbol): la cuenta enfocada y, si en XMPP hay un dominio
        # de pasarela activo, su nodo de rama.
        cuenta = self._nodo_cuenta_enfocada()
        dom = getattr(self, "active_xmpp_dominio", None)
        if cuenta and cuenta.IsOk() and dom and dom != "__nativo__":
            child, cookie = self.tree_nav.GetFirstChild(cuenta)
            while child.IsOk():
                d = self.tree_nav.GetItemData(child)
                if d and d.get("type") == "category_root" and d.get("dominio") == dom:
                    return child
                child, cookie = self.tree_nav.GetNextChild(cuenta, cookie)
        return cuenta

    def _con_posicion_buffer(self, texto):
        """Añade al anuncio la posición en el buffer recién cargado, como TWBlue: "Inicio, 3 de
        120". Si la lista está vacía (o el buffer aún se está cargando de la red) se deja solo el
        nombre, para no decir "vacío" de un buffer que en un momento tendrá elementos."""
        try:
            n = self.lista_items.GetItemCount()
            idx = self.lista_items.GetFocusedItem()
        except Exception:
            return texto
        if not n or n <= 0:
            return texto
        if idx is None or idx < 0 or idx >= n:
            idx = 0
        return _("{0}, {1} de {2}").format(texto, idx + 1, n)

    def _ir_a_buffer_nodo(self, destino, etiqueta, anuncio=None, con_posicion=False):
        """Selecciona un nodo de buffer y deja al usuario DENTRO de su vista (en la lista),
        restaurando la posición donde estaba (aunque hayan entrado elementos nuevos). Si se pasa
        'anuncio', se locuta ese texto en vez del nombre del buffer (p. ej. al cambiar de cuenta,
        para incluir el nombre de la cuenta destino). Con 'con_posicion' (interfaz invisible) se
        añade el elemento enfocado y el total ("3 de 120")."""
        if not destino or not destino.IsOk():
            return
        # Recordar dónde estabas en el buffer actual antes de cambiar.
        try:
            self._guardar_foco_buffer()
        except Exception:
            pass
        self.tree_nav.EnsureVisible(destino)
        self.tree_nav.SelectItem(destino)  # dispara la carga del buffer y restaura su foco
        serv = self._servicio_activo()
        if self.lista_items.GetItemCount() > 0:
            self.lista_items.SetFocus()
            if serv:
                self._ultimo_panel_servicio[serv] = "lista"
        # Anunciar el nombre del buffer (queda como última locución, tras el foco de la lista).
        texto = anuncio if anuncio is not None else etiqueta
        if con_posicion:
            texto = self._con_posicion_buffer(texto)
        speak(texto, interrupt=True)

    def _ir_a_buffer_cuenta(self, n: int):
        """Ctrl+Shift+N: ir al N-ésimo buffer del contexto actual. Normalmente los de la cuenta; en
        XMPP, si estás dentro de una pasarela (WhatsApp…), los de esa pasarela. Si hay menos buffers
        que N, va al último."""
        nodo = self._contenedor_buffers_navegables()
        if not nodo:
            speak(_("Ponte primero en una cuenta para saltar entre sus buffers."), interrupt=True)
            return
        buffers = self._buffers_de_cuenta(nodo)
        if not buffers:
            speak(_("Esta cuenta no tiene buffers."), interrupt=True)
            return
        idx = min(n - 1, len(buffers) - 1)
        self._ir_a_buffer_nodo(buffers[idx][0], buffers[idx][1])

    def _mostrar_dialogo_buffers_cuenta(self):
        """Ctrl+Shift+L: diálogo con los buffers del contexto actual. Normalmente los de la cuenta;
        en XMPP, si estás dentro de una pasarela (WhatsApp…), los de esa pasarela. Al pulsar Intro
        en uno, salta a ese buffer y deja al usuario en su vista."""
        nodo = self._contenedor_buffers_navegables()
        if not nodo:
            speak(_("Ponte primero en una cuenta para ver sus buffers."), interrupt=True)
            return
        buffers = self._buffers_de_cuenta(nodo)
        if not buffers:
            speak(_("Esta cuenta no tiene buffers."), interrupt=True)
            return
        etiquetas = [lbl for _n, lbl in buffers]
        nombre_cuenta = self.tree_nav.GetItemText(nodo)
        dlg = wx.SingleChoiceDialog(self, _("Elige un buffer y pulsa Intro para ir:"),
                                    _("Buffers de {0}").format(nombre_cuenta), etiquetas)
        if dlg.ShowModal() == wx.ID_OK:
            i = dlg.GetSelection()
            dlg.Destroy()
            if 0 <= i < len(buffers):
                self._ir_a_buffer_nodo(buffers[i][0], buffers[i][1])
        else:
            dlg.Destroy()

    def _servicio_tiene_nodo(self, service_type: str) -> bool:
        """True si el servicio tiene un nodo raíz en el árbol (siempre se construye)."""
        try:
            root = self.tree_nav.GetRootItem()
            if not root.IsOk():
                return False
            child, cookie = self.tree_nav.GetFirstChild(root)
            while child.IsOk():
                data = self.tree_nav.GetItemData(child)
                if data and data.get("type") == "category_root" and data.get("service") == service_type:
                    return True
                child, cookie = self.tree_nav.GetNextChild(root, cookie)
        except RuntimeError:
            pass
        return False

    def _servicio_activo(self):
        """Devuelve el servicio actualmente activo ('mastodon', 'bluesky', ...) o None."""
        if self.active_mastodon_account_id:
            return "mastodon"
        if self.active_bluesky_account_id:
            return "bluesky"
        if self.active_telegram_account_id:
            return "telegram"
        if self.active_rss_account_id:
            return "rss"
        if self.active_x_account_id:
            return "x"
        if getattr(self, "active_reddit_account_id", None):
            return "reddit"
        if getattr(self, "active_peertube_account_id", None):
            return "peertube"
        if getattr(self, "active_wsocial_account_id", None):
            return "wsocial"
        return None

    def _activar_servicio_en_navegacion(self, service_type: str):
        # Si hay un nodo recordado para este servicio (la última cuenta/buffer que
        # visitaste y que sigue en el árbol), ir allí; al seleccionarlo se recarga
        # su lista y se restaura tu posición (foco) en ella. Si no, ir a la raíz.
        clave = self._ultimo_nodo_servicio.get(service_type)
        # Capturar el panel ANTES de seleccionar, porque SelectItem dispara
        # _on_nav_selection, que lo marca como "arbol".
        panel_objetivo = self._ultimo_panel_servicio.get(service_type)
        destino = self._buscar_nodo_por_clave(clave) if clave else None
        if destino is None:
            destino = self._buscar_nodo_raiz_servicio(service_type)
        if destino is None:
            return
        self.tree_nav.EnsureVisible(destino)
        self.tree_nav.SelectItem(destino)  # recarga el buffer y restaura el foco de la lista
        # Si la última vez estabas DENTRO del buffer (en la lista), volver a la
        # lista, en el item donde lo dejaste; si estabas en los buffers, al árbol.
        if panel_objetivo == "lista" and self.lista_items.GetItemCount() > 0:
            self.lista_items.SetFocus()
            # SelectItem marcó "arbol"; como hemos vuelto a la lista, lo dejamos en
            # "lista" para que el próximo Alt al servicio también caiga en la lista.
            self._ultimo_panel_servicio[service_type] = "lista"
        else:
            self.tree_nav.SetFocus()

    def _buscar_nodo_raiz_servicio(self, service_type: str):
        """Devuelve el nodo raíz (category_root) de un servicio, o None."""
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return None
        child, cookie = self.tree_nav.GetFirstChild(root)
        while child.IsOk():
            data = self.tree_nav.GetItemData(child)
            if data and data.get("service") == service_type and data.get("type") == "category_root":
                return child
            child, cookie = self.tree_nav.GetNextChild(root, cookie)
        return None

    def _buscar_nodo_por_clave(self, clave):
        """Busca en todo el árbol el nodo cuya clave de selección coincida, o None."""
        if not clave:
            return None
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return None
        encontrado = [None]

        def recorrer(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                if encontrado[0] is None and \
                        self._clave_seleccion(self.tree_nav.GetItemData(child)) == clave:
                    encontrado[0] = child
                if self.tree_nav.ItemHasChildren(child):
                    recorrer(child)
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)

        recorrer(root)
        return encontrado[0]

    def _cambiar_cuenta(self, delta, con_posicion=False):
        """Salta a la cuenta siguiente (delta=1) o anterior (delta=-1) recorriendo
        todos los nodos de cuenta del árbol, de todos los servicios. 'con_posicion': anunciar
        también "X de N" del buffer al que se llega (interfaz invisible, como TWBlue)."""
        # Recopilar todos los nodos de tipo 'account' en orden de aparición.
        cuentas = []
        def _recorrer(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                data = self.tree_nav.GetItemData(child)
                if data and data.get("type") == "account":
                    cuentas.append(child)
                if self.tree_nav.ItemHasChildren(child):
                    _recorrer(child)
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)
        _recorrer(self.tree_nav.GetRootItem())
        if not cuentas:
            speak(_("No hay cuentas configuradas."), interrupt=True)
            return
        # Determinar la cuenta actualmente seleccionada (o su ancestro cuenta).
        actual = self.tree_nav.GetSelection()
        idx_actual = -1
        for i, c in enumerate(cuentas):
            nodo = actual
            while nodo and nodo.IsOk():
                if nodo == c:
                    idx_actual = i
                    break
                nodo = self.tree_nav.GetItemParent(nodo)
            if idx_actual >= 0:
                break
        nuevo = (idx_actual + delta) % len(cuentas) if idx_actual >= 0 else 0
        destino = cuentas[nuevo]
        nombre_cuenta = self.tree_nav.GetItemText(destino)
        # Petición de usuarios: en vez de quedarnos en el NODO de la cuenta (que deja visible la lista de la
        # cuenta ANTERIOR), saltamos al primer buffer (Inicio) de la cuenta nueva y nos metemos en
        # su lista, en el primer elemento útil. Así al cambiar de cuenta ya estás leyendo la suya.
        buffers = self._buffers_de_cuenta(destino)
        if buffers:
            nodo_inicio, etiqueta_inicio = buffers[0]
            self._ir_a_buffer_nodo(
                nodo_inicio, etiqueta_inicio,
                anuncio=_("Cuenta {0}, {1}").format(nombre_cuenta, etiqueta_inicio),
                con_posicion=con_posicion)
        else:
            # Cuenta sin buffers (caso raro): comportamiento anterior, quedarse en el nodo.
            self.tree_nav.SelectItem(destino)
            self.tree_nav.EnsureVisible(destino)
            self.tree_nav.SetFocus()
            speak(_("Cuenta: {0}").format(nombre_cuenta), interrupt=True)

    def _etiqueta_con_contador(self, texto: str, no_leidos: int) -> str:
        """Devuelve la etiqueta del nodo con el número de no leídos entre paréntesis."""
        if no_leidos and no_leidos > 0:
            return f"{texto} ({no_leidos})"
        return texto

    def _actualizar_contadores_rss(self):
        """
        Recalcula y actualiza in situ los contadores de no leídos en los nodos
        RSS del árbol, sin reconstruirlo, para no mover el foco del usuario.
        """
        try:
            if not self or not bool(self.tree_nav):
                return
            root = self.tree_nav.GetRootItem()
            if not root.IsOk():
                return

            # SIN consultas N+1 (auditoría RSS-19): TODO el estado sale de DOS consultas
            # agrupadas (no leídos por feed, y feed->cuenta/categoría); los totales por
            # cuenta y por carpeta se derivan en memoria. Antes había una consulta por
            # feed con no leídos y otra por cada nodo de carpeta en cada refresco.
            no_leidos_por_feed = {}
            for fid, cnt in db.query("""
                SELECT a.feed_id, COUNT(*)
                FROM rss_articles a
                WHERE a.read = 0 AND COALESCE(a.deleted, 0) = 0
                GROUP BY a.feed_id
            """):
                no_leidos_por_feed[fid] = cnt

            cuenta_de_feed = {}
            feeds_de_categoria = {}
            for fid, acc, cat in db.query(
                    "SELECT id, account_id, category_id FROM rss_feeds"):
                cuenta_de_feed[fid] = acc
                if cat is not None:
                    feeds_de_categoria.setdefault(cat, []).append(fid)

            total_por_cuenta = {}
            for fid, cnt in no_leidos_por_feed.items():
                acc = cuenta_de_feed.get(fid)
                if acc is not None:
                    total_por_cuenta[acc] = total_por_cuenta.get(acc, 0) + cnt

            def recorrer(nodo):
                hijo, cookie = self.tree_nav.GetFirstChild(nodo)
                while hijo.IsOk():
                    data = self.tree_nav.GetItemData(hijo)
                    if data and data.get("service") == "rss" and "base_label" in data:
                        base = data["base_label"]
                        tipo = data.get("type")
                        if tipo == "rss_feed":
                            n = no_leidos_por_feed.get(data.get("feed_id"), 0)
                        elif tipo == "rss_category":
                            n = sum(no_leidos_por_feed.get(f, 0)
                                    for f in feeds_de_categoria.get(data.get("category_id"), ()))
                        elif tipo == "account" or (tipo == "rss_timeline" and data.get("rss_type") in ("all", "unread")):
                            n = total_por_cuenta.get(data.get("account_id"), 0)
                        else:
                            n = 0
                        nueva = self._etiqueta_con_contador(base, n)
                        if self.tree_nav.GetItemText(hijo) != nueva:
                            self.tree_nav.SetItemText(hijo, nueva)
                    if self.tree_nav.ItemHasChildren(hijo):
                        recorrer(hijo)
                    hijo, cookie = self.tree_nav.GetNextChild(nodo, cookie)

            recorrer(root)
        except RuntimeError:
            return
        except Exception as e:
            log.debug(f"Error al actualizar contadores RSS: {e}")

    # --- CARGA Y RENDER DEL ÁRBOL DE NAVEGACIÓN ---
    def _orden_servicios(self) -> list:
        """Devuelve el orden de los servicios en el árbol, según configuración.

        Respeta SIEMPRE el orden que dejó el usuario: solo limpia entradas inválidas o
        duplicadas y añade al FINAL cualquier servicio conocido que faltara (sin mover
        los que ya estaban). Persiste el resultado únicamente si cambió algo, para que el
        orden quede estable y no "salte" en sucesivas reconstrucciones del árbol."""
        conocidos = ["mastodon", "bluesky", "rss", "telegram"]
        # X (Twitter) entra en el orden solo si el cliente está disponible (kill-switch).
        try:
            from app.core.feature_flags import x_cliente_disponible
            if x_cliente_disponible():
                conocidos.append("x")
        except Exception:
            pass
        # IRC y Matrix entran en el orden solo si su librería está instalada y el servicio activo.
        try:
            from app.core.feature_flags import servicio_habilitado
            if servicio_habilitado("irc"):
                conocidos.append("irc")
            if servicio_habilitado("matrix"):
                conocidos.append("matrix")
            if servicio_habilitado("xmpp"):
                conocidos.append("xmpp")
            if servicio_habilitado("reddit"):
                conocidos.append("reddit")
            if servicio_habilitado("peertube"):
                conocidos.append("peertube")
            if servicio_habilitado("wsocial"):
                conocidos.append("wsocial")
        except Exception:
            pass
        # La BANDEJA DE ENTRADA, si está activada, es un "servicio" más en el orden: se reordena con
        # Ctrl+flechas y se activa con Alt+1..9 según su posición, igual que las redes.
        try:
            if self._inbox_habilitada():
                conocidos.append("inbox")
        except Exception:
            pass
        conocidos = tuple(conocidos)
        guardado = list(config.get("general", "service_order", list(conocidos)))
        # Conservar el orden del usuario, descartando valores desconocidos y duplicados.
        orden = []
        for s in guardado:
            if s in conocidos and s not in orden:
                orden.append(s)
        # Añadir al final los servicios conocidos que falten (sin alterar el orden previo).
        for s in conocidos:
            if s not in orden:
                orden.append(s)
        # Persistir solo si la lista limpia difiere de la guardada (evita reescrituras
        # innecesarias y corrige de una vez listas con duplicados o entradas inválidas).
        if orden != guardado:
            config.set("general", "service_order", orden)
        return orden

    def _clave_nodo(self, data) -> str:
        """Clave estable de un nodo contenedor para recordar su estado expandido."""
        if not data:
            return None
        t = data.get("type")
        if t == "category_root":
            return f"root:{data.get('service')}"
        if t == "account":
            return f"acc:{data.get('service')}:{data.get('account_id')}"
        if t == "rss_category":
            return f"cat:{data.get('account_id')}:{data.get('category_id')}"
        return None

    def _clave_seleccion(self, data) -> str:
        """Clave estable de CUALQUIER nodo (incluidos buffers/timelines/carpetas).

        Se usa para recordar y restaurar qué nodo estaba seleccionado al
        reconstruir el árbol, y así no perder el foco ni saltar al principio.
        """
        if not data:
            return None
        try:
            return json.dumps(data, sort_keys=True, default=str)
        except Exception:
            return None

    def _restaurar_seleccion(self, clave):
        """Vuelve a seleccionar el nodo cuya clave coincida, sin recargar la lista."""
        if not clave:
            return
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return
        encontrado = [None]

        def recorrer(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                if encontrado[0] is None and \
                        self._clave_seleccion(self.tree_nav.GetItemData(child)) == clave:
                    encontrado[0] = child
                if self.tree_nav.ItemHasChildren(child):
                    recorrer(child)
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)

        recorrer(root)
        if encontrado[0] is not None:
            # Marcar que es una restauración para que _on_nav_selection no recargue
            # la lista ni vuelva a anunciar el buffer (el contenido ya es el correcto).
            self._restaurando_arbol = True
            try:
                self.tree_nav.SelectItem(encontrado[0])
                self.tree_nav.EnsureVisible(encontrado[0])
            finally:
                wx.CallAfter(setattr, self, "_restaurando_arbol", False)

    def _capturar_expandidos(self) -> set:
        """Recoge las claves de los nodos actualmente expandidos."""
        expandidos = set()
        try:
            root = self.tree_nav.GetRootItem()
            if not root.IsOk():
                return expandidos

            def recorrer(parent):
                child, cookie = self.tree_nav.GetFirstChild(parent)
                while child.IsOk():
                    if self.tree_nav.ItemHasChildren(child) and self.tree_nav.IsExpanded(child):
                        clave = self._clave_nodo(self.tree_nav.GetItemData(child))
                        if clave:
                            expandidos.add(clave)
                    if self.tree_nav.ItemHasChildren(child):
                        recorrer(child)
                    child, cookie = self.tree_nav.GetNextChild(parent, cookie)

            recorrer(root)
        except Exception:
            pass
        return expandidos

    def _restaurar_expandidos(self, claves: set):
        """Vuelve a expandir los nodos cuyas claves estén en el conjunto."""
        if not claves:
            return
        root = self.tree_nav.GetRootItem()
        if not root.IsOk():
            return

        def recorrer(parent):
            child, cookie = self.tree_nav.GetFirstChild(parent)
            while child.IsOk():
                if self._clave_nodo(self.tree_nav.GetItemData(child)) in claves:
                    self.tree_nav.Expand(child)
                if self.tree_nav.ItemHasChildren(child):
                    recorrer(child)
                child, cookie = self.tree_nav.GetNextChild(parent, cookie)

        recorrer(root)

    def _thaw_arbol_seguro(self):
        """Descongela el árbol tras una reconstrucción (encolado con CallAfter). Cada Freeze de
        _cargar_navegacion encola exactamente un Thaw, así que el conteo de Freeze/Thaw queda
        equilibrado aunque se solapen dos reconstrucciones. Protegido por si la ventana se está
        destruyendo. Con Freeze, el coste de notificar a accesibilidad (MSAA/NVDA) se hace de UNA
        vez aquí en lugar de por cada nodo; se mide para ver ese coste batcheado en el log."""
        try:
            import time as _tmod
            _t = _tmod.perf_counter()
            self.tree_nav.Thaw()
            _ms = (_tmod.perf_counter() - _t) * 1000.0
            if _ms >= 100:
                log.info(f"Árbol de navegación: descongelado (Thaw) en {_ms:.0f} ms [hilo de UI].")
            else:
                log.debug(f"Árbol de navegación: descongelado (Thaw) en {_ms:.0f} ms.")
        except Exception:
            pass

    def _cargar_navegacion(self):
        # BLINDAJE: la reconstrucción del árbol DEBE ocurrir en el hilo de la interfaz.
        # Si se llama desde un hilo de fondo, las inserciones de nodos se intercalan con
        # las del hilo de UI y el árbol sale DESORDENADO (el síntoma de que "el Lector RSS
        # sube a la primera posición en ocasiones"). Re-despachamos al hilo de UI.
        if not wx.IsMainThread():
            wx.CallAfter(self._cargar_navegacion)
            return
        # Instrumentación de rendimiento: cuántas veces se reconstruye el árbol y cuánto tarda cada
        # vez. Reconstruir el árbol ENTERO es trabajo del hilo de UI (bloquea al lector mientras dura);
        # si se ve muchas reconstrucciones seguidas al arrancar, ahí está el tirón. Se mide y se
        # registra al final (INFO si es notable, DEBUG si no).
        import time as _tmod
        self._nav_build_count = getattr(self, "_nav_build_count", 0) + 1
        _t0 = _tmod.perf_counter()
        # BLINDAR toda la reconstrucción: mientras dura, los eventos de selección que dispara
        # borrar/crear nodos NO deben ejecutar _on_nav_selection (cargar buffers sobre un árbol a
        # medio construir podía colgar la app). El flag se baja con CallAfter (tras asentarse el
        # árbol); lo encolamos YA, para que se baje aunque la reconstrucción lance una excepción.
        self._restaurando_arbol = True
        wx.CallAfter(setattr, self, "_restaurando_arbol", False)
        # Si el usuario estaba en la LISTA de elementos (no en el árbol), recordar para
        # devolverle el foco ahí tras reconstruir: una acción del menú contextual que recrea el
        # árbol (crear/borrar cronología, guardar búsqueda...) NO debe sacarlo de la lista.
        foco_en_lista = False
        try:
            foco_en_lista = (wx.Window.FindFocus() is self.lista_items)
        except Exception:
            foco_en_lista = False
        # Recordar qué nodos estaban abiertos para restaurar el estado tras
        # reconstruir (al arrancar no hay ninguno → árboles cerrados).
        expandidos = self._capturar_expandidos()
        # Recordar el nodo seleccionado para restaurarlo y NO perder el foco ni
        # saltar al principio del árbol al reconstruir (p. ej. al pulsar F5).
        clave_sel = None
        try:
            sel = self.tree_nav.GetSelection()
            if sel and sel.IsOk():
                clave_sel = self._clave_seleccion(self.tree_nav.GetItemData(sel))
        except Exception:
            clave_sel = None
        # CONGELAR el árbol durante toda la reconstrucción: sin esto, CADA AppendItem (y son cientos:
        # 220+ nodos con muchos feeds RSS) dispara eventos de accesibilidad (MSAA/UIA) que el lector
        # procesa uno a uno, y la construcción llegaba a tardar 0,4-1 s BLOQUEANDO el hilo de la UI
        # (por eso NVDA se quedaba mudo unos segundos al arrancar y al conectar cada servicio). Con
        # Freeze se inserta todo de una vez y se notifica una sola vez. El Thaw se ENCOLA ya (CallAfter,
        # como el flag de abajo), así se descongela aunque la construcción lance una excepción, y sin
        # anidar mal el conteo de Freeze/Thaw.
        self.tree_nav.Freeze()
        wx.CallAfter(self._thaw_arbol_seguro)
        self.tree_nav.DeleteAllItems()
        root = self.tree_nav.AddRoot("Root")

        constructores = {
            "mastodon": self._construir_servicio_mastodon,
            "rss": self._construir_servicio_rss,
            "telegram": self._construir_servicio_telegram,
            "bluesky": self.bluesky_ctrl.construir_servicio,
        }
        # X (Twitter) solo si el cliente está disponible (kill-switch).
        if self.x_ctrl is not None:
            constructores["x"] = self.x_ctrl.construir_servicio
        # IRC solo si su controlador está disponible (librería instalada + servicio activo).
        if getattr(self, "irc_ctrl", None) is not None:
            constructores["irc"] = self.irc_ctrl.construir_servicio
        # Matrix, mismo criterio.
        if getattr(self, "matrix_ctrl", None) is not None:
            constructores["matrix"] = self.matrix_ctrl.construir_servicio
        # XMPP (Jabber), mismo criterio.
        if getattr(self, "xmpp_ctrl", None) is not None:
            constructores["xmpp"] = self.xmpp_ctrl.construir_servicio
        # Reddit, mismo criterio (librería praw instalada + servicio activo).
        if getattr(self, "reddit_ctrl", None) is not None:
            constructores["reddit"] = self.reddit_ctrl.construir_servicio
        # PeerTube (red de vídeo federada), mismo criterio.
        if getattr(self, "peertube_ctrl", None) is not None:
            constructores["peertube"] = self.peertube_ctrl.construir_servicio
        # W Social (red europea sobre AT Protocol), mismo criterio.
        if getattr(self, "wsocial_ctrl", None) is not None:
            constructores["wsocial"] = self.wsocial_ctrl.construir_servicio
        # Bandeja de entrada (si está activada): un nodo raíz más, colocado según el orden.
        if self._inbox_habilitada():
            constructores["inbox"] = self._construir_servicio_inbox
        orden = self._orden_servicios()
        log.debug(f"Construyendo árbol de navegación en este orden: {orden}")
        from app.core.feature_flags import servicio_habilitado
        _coste_por_servicio = {}  # ms que tarda el constructor de cada red (diagnóstico de arranque)
        for service_id in orden:
            # Un servicio DESACTIVADO por el usuario no se construye (no aparece en el árbol).
            # Así Alt+1..N y el resto de la app lo ignoran de forma coherente.
            if not servicio_habilitado(service_id):
                continue
            constructor = constructores.get(service_id)
            if not constructor:
                continue
            try:
                _ts = _tmod.perf_counter()
                constructor(root)
                _coste_por_servicio[service_id] = (_tmod.perf_counter() - _ts) * 1000.0
            except Exception as e:
                # Si un servicio falla al construirse (p. ej. Bluesky con la sesión
                # caducada), NO debe abortar el resto ni dejar el árbol a medias: se
                # registra y se construye al menos su nodo raíz para mantener el orden.
                log.error(f"Error al construir el servicio '{service_id}' en el árbol: {e}")
                try:
                    if not self._servicio_tiene_nodo(service_id):
                        etiquetas = {"mastodon": _("Mastodon"), "rss": _("Lector RSS"),
                                     "telegram": _("Telegram"), "bluesky": _("Bluesky"),
                                     "irc": _("IRC"), "matrix": _("Matrix"), "xmpp": _("XMPP"),
                                     "x": _("X"), "reddit": _("Reddit"), "peertube": _("PeerTube"),
                                     "wsocial": _("W Social")}
                        self.tree_nav.AppendItem(root, etiquetas.get(service_id, service_id),
                                                 data={"type": "category_root", "service": service_id})
                except Exception:
                    pass

        # Recordar el estado con el que se construyó el árbol, para reconstruirlo en caliente solo
        # si la opción de la bandeja cambia (ver _aplicar_preferencias_en_caliente). El nodo de la
        # bandeja, si está activada, lo añade su constructor dentro del bucle anterior (según el
        # orden), igual que cualquier red.
        self._inbox_enabled_actual = self._inbox_habilitada()

        # Restaurar el estado expandido/cerrado previo
        self._restaurar_expandidos(expandidos)
        # Restaurar la selección previa para conservar el foco donde estaba.
        self._restaurar_seleccion(clave_sel)
        # ARRANQUE: si no había selección previa (primera construcción) y el PRIMER nodo del árbol es
        # la BANDEJA DE ENTRADA, cargar su contenido YA. La bandeja es el único category_root que es
        # un buffer cargable; como la selección que hace wx al construir el árbol queda suprimida por
        # _restaurando_arbol, su lista se quedaba vacía hasta navegar (bajar/subir). El resto de raíces
        # muestran lista vacía a propósito (se entra a la cuenta/buffer), así que solo la bandeja lo
        # necesita. Se carga directamente porque la selección durante la reconstrucción no dispara
        # _on_nav_selection de forma fiable.
        if clave_sel is None and self._inbox_habilitada():
            try:
                primer, _c = self.tree_nav.GetFirstChild(root)
                d0 = self.tree_nav.GetItemData(primer) if primer.IsOk() else None
                if d0 and d0.get("service") == "inbox":
                    self.tree_nav.SelectItem(primer)
                    self.tree_nav.EnsureVisible(primer)
                    self.active_mastodon_account_id = None
                    self.active_rss_account_id = None
                    self.active_telegram_account_id = None
                    self.active_bluesky_account_id = None
                    self.active_x_account_id = None
                    self.active_timeline_type = "inbox"
                    self.lbl_buffer_titulo.SetLabel(_("Bandeja de entrada"))
                    self._cargar_listado_bandeja()
            except Exception as e:
                log.debug(f"No se pudo cargar la bandeja en el arranque: {e}")
        # Si el usuario estaba en la lista, devolverle ahí el foco (el SelectItem de la
        # restauración lo lleva al árbol). Con CallAfter, después de que el árbol se asiente.
        if foco_en_lista and self.lista_items.GetItemCount() > 0:
            wx.CallAfter(self.lista_items.SetFocus)

        # Coste de esta reconstrucción del árbol (nº de nodos y ms). >150 ms en el hilo de UI ya se
        # nota en el lector; varias seguidas al arrancar apuntan a reconstrucciones redundantes.
        try:
            _ms = (_tmod.perf_counter() - _t0) * 1000.0
            _nodos = self.tree_nav.GetCount()
            # Desglose por red (mayor coste primero), para ver QUÉ constructor se lleva los ms.
            _desglose = ", ".join(
                f"{s}={c:.0f}ms" for s, c in
                sorted(_coste_por_servicio.items(), key=lambda kv: kv[1], reverse=True))
            if _ms >= 150:
                log.info(f"Árbol de navegación reconstruido (nº {self._nav_build_count}): "
                         f"{_nodos} nodos en {_ms:.0f} ms [hilo de UI]. Por red: {_desglose}")
            else:
                log.debug(f"Árbol de navegación reconstruido (nº {self._nav_build_count}): "
                          f"{_nodos} nodos en {_ms:.0f} ms. Por red: {_desglose}")
        except Exception:
            pass

    def _construir_servicio_mastodon(self, root):
        """Construye el nodo del servicio Mastodon con sus cuentas y timelines."""
        n_mastodon = self.tree_nav.AppendItem(root, _("Mastodon"), data={"type": "category_root", "service": "mastodon"})

        rows = db.query("SELECT id, name FROM accounts WHERE service_id = 'mastodon' AND active = 1 ORDER BY position, id")
        for r_id, r_name in rows:
            n_acc = self.tree_nav.AppendItem(n_mastodon, r_name, data={"type": "account", "account_id": r_id, "service": "mastodon"})

            # Subnodos para los timelines. Normalizamos el orden para que SIEMPRE
            # contenga todos los buffers conocidos (así todos son reordenables) y
            # lo persistimos si faltaba alguno (config antigua).
            buffer_order = list(config.get("mastodon", "buffer_order", ["home", "notifications", "mentions", "bookmarks", "favourites", "sent", "direct", "local", "public"]))
            _todos = ["home", "notifications", "mentions", "bookmarks", "favourites", "sent",
                      "direct", "local", "public", "announcements", "trending",
                      "followers", "following", "blocked", "muted"]
            faltan = [b for b in _todos if b not in buffer_order]
            if faltan:
                # Insertar "mentions" justo tras "notifications" (no al final) para que aparezca
                # en un sitio natural; el resto de faltantes van al final. El usuario puede
                # reordenar con Ctrl+flechas de todas formas.
                if "mentions" in faltan and "notifications" in buffer_order:
                    buffer_order.insert(buffer_order.index("notifications") + 1, "mentions")
                    faltan = [b for b in faltan if b != "mentions"]
                buffer_order.extend(faltan)
                config.set("mastodon", "buffer_order", buffer_order)
            buffer_labels = {
                "home": _("Inicio"),
                "notifications": _("Notificaciones"),
                "mentions": _("Menciones"),
                "bookmarks": _("Marcadores"),
                "favourites": _("Favoritos"),
                "sent": _("Enviados"),
                "direct": _("Mensajes Directos"),
                "local": _("Local"),
                "public": _("Federada"),
                "announcements": _("Anuncios del servidor"),
                "trending": _("Tendencias"),
                "followers": _("Seguidores"),
                "following": _("Siguiendo"),
                "blocked": _("Bloqueados"),
                "muted": _("Silenciados"),
            }

            ocultos = config.get("mastodon", "hidden_buffers", [])
            for b_type in buffer_order:
                if b_type in ocultos:
                    continue
                label = buffer_labels.get(b_type)
                if label:
                    self.tree_nav.AppendItem(n_acc, label, data={"type": "timeline", "timeline_type": b_type, "account_id": r_id, "service": "mastodon"})

            # Listas de Mastodon (desde la caché local; se actualiza al abrir el gestor)
            for lst in config.get("mastodon", "lists_cache", []):
                self.tree_nav.AppendItem(
                    n_acc, _("Lista: {0}").format(lst.get("title", "")),
                    data={"type": "timeline", "timeline_type": "list:{0}".format(lst.get("id")),
                          "account_id": r_id, "service": "mastodon"}
                )

            # Búsquedas guardadas (hashtags o texto)
            for termino in config.get("mastodon", "saved_searches", []):
                self.tree_nav.AppendItem(
                    n_acc, _("Búsqueda: {0}").format(termino),
                    data={"type": "timeline", "timeline_type": "search:{0}".format(termino),
                          "account_id": r_id, "service": "mastodon", "search_term": termino}
                )

            # Cronologías de OTROS usuarios (estilo TWBlue): un nodo padre desplegable con un
            # sub-buffer por usuario (todas sus publicaciones). El padre se puede ocultar como
            # cualquier otro buffer (clave "user_timelines"); cada hijo se puede borrar.
            cronologias = [u for u in config.get("mastodon", "user_timelines", [])
                           if u.get("account_id") == r_id]
            if cronologias and "user_timelines" not in ocultos:
                n_cron = self.tree_nav.AppendItem(
                    n_acc, _("Cronologías de usuarios"),
                    data={"type": "user_timelines_root", "account_id": r_id, "service": "mastodon"})
                for u in cronologias:
                    acct = u.get("acct", "")
                    etiqueta = u.get("display") or acct
                    self.tree_nav.AppendItem(
                        n_cron, _("Cronología de {0}").format(etiqueta),
                        data={"type": "timeline", "timeline_type": "user:{0}".format(acct),
                              "account_id": r_id, "service": "mastodon",
                              "user_acct": acct, "user_display": etiqueta})

            # Conversaciones abiertas como buffer (hilos). Igual que las cronologías: un nodo
            # padre desplegable ("Conversaciones") con un sub-buffer por hilo; el padre se puede
            # ocultar (clave "thread_buffers") y cada hilo se puede borrar.
            conversaciones = [c for c in config.get("mastodon", "thread_buffers", [])
                              if c.get("account_id") == r_id]
            if conversaciones and "thread_buffers" not in ocultos:
                n_conv = self.tree_nav.AppendItem(
                    n_acc, _("Conversaciones"),
                    data={"type": "thread_buffers_root", "account_id": r_id, "service": "mastodon"})
                for c in conversaciones:
                    sid = str(c.get("status_id", ""))
                    etiqueta = c.get("display") or sid
                    self.tree_nav.AppendItem(
                        n_conv, etiqueta,
                        data={"type": "timeline", "timeline_type": "thread:{0}".format(sid),
                              "account_id": r_id, "service": "mastodon", "thread_status_id": sid})
        return n_mastodon

    def _construir_servicio_rss(self, root):
        """Construye el nodo del servicio RSS con sus cuentas, categorías y feeds."""
        n_rss = self.tree_nav.AppendItem(root, _("Lector RSS"), data={"type": "category_root", "service": "rss"})

        rss_accs = db.query("SELECT id, name FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id")
        for r_id, r_name in rss_accs:
            # Calcular artículos no leídos por feed para esta cuenta (una sola consulta)
            no_leidos_por_feed = {}
            for fid, cnt in db.query("""
                SELECT a.feed_id, COUNT(*)
                FROM rss_articles a
                JOIN rss_feeds f ON a.feed_id = f.id
                WHERE a.account_id = ? AND a.read = 0 AND COALESCE(a.deleted, 0) = 0
                GROUP BY a.feed_id
            """, (r_id,)):
                no_leidos_por_feed[fid] = cnt
            total_no_leidos = sum(no_leidos_por_feed.values())

            n_racc = self.tree_nav.AppendItem(n_rss, self._etiqueta_con_contador(r_name, total_no_leidos), data={"type": "account", "account_id": r_id, "service": "rss", "base_label": r_name})

            # Buffers estándar de RSS (normalizados para que todos sean reordenables)
            buffer_order = list(config.get("rss", "buffer_order", ["all", "unread", "favorites", "read_later", "today"]))
            _todos_rss = ["all", "unread", "favorites", "read_later", "today"]
            faltan_rss = [b for b in _todos_rss if b not in buffer_order]
            if faltan_rss:
                buffer_order.extend(faltan_rss)
                config.set("rss", "buffer_order", buffer_order)
            buffer_labels = {
                "all": _("Todos los artículos"),
                "unread": _("No leídos"),
                "favorites": _("Destacados"),
                "read_later": _("Leer más tarde"),
                "today": _("Hoy"),
            }
            ocultos_rss = config.get("rss", "hidden_buffers", [])
            for b in buffer_order:
                if b in ocultos_rss:
                    continue
                label = buffer_labels.get(b)
                if not label:
                    continue
                contador = total_no_leidos if b in ("all", "unread") else 0
                self.tree_nav.AppendItem(
                    n_racc, self._etiqueta_con_contador(label, contador),
                    data={"type": "rss_timeline", "rss_type": b, "account_id": r_id, "service": "rss", "base_label": label}
                )

            # CARPETAS INTELIGENTES de la cuenta (auditoría, prioridad A): consultas
            # guardadas estructuradas que se abren como un buffer más del árbol.
            try:
                for sf_id, sf_name in db.query(
                        "SELECT id, name FROM rss_smart_folders WHERE account_id = ? "
                        "ORDER BY position, name", (r_id,)):
                    self.tree_nav.AppendItem(
                        n_racc, self._etiqueta_con_contador(sf_name, 0),
                        data={"type": "rss_timeline", "rss_type": f"smart:{sf_id}",
                              "account_id": r_id, "service": "rss", "base_label": sf_name})
            except Exception as e:
                log.debug(f"No se pudieron cargar las carpetas inteligentes: {e}")

            # BÚSQUEDAS GUARDADAS como buffer (se actualizan solas con cada sondeo). Se pueden
            # ocultar del árbol en Preferencias, RSS.
            if config.get("rss", "saved_searches_in_tree", True):
                from app.services.rss.rss_buffers import busquedas_en_arbol
                for bs_id, bs_name in busquedas_en_arbol(r_id):
                    etiqueta_bs = _("Búsqueda: {0}").format(bs_name)
                    self.tree_nav.AppendItem(
                        n_racc, self._etiqueta_con_contador(etiqueta_bs, 0),
                        data={"type": "rss_timeline", "rss_type": f"search:{bs_id}",
                              "account_id": r_id, "service": "rss", "base_label": etiqueta_bs})

            # ETIQUETAS de la cuenta (auditoría, prioridad B): un buffer por etiqueta que
            # reúne los artículos etiquetados (a mano o por reglas) y los de feeds con esa
            # etiqueta. Solo se muestran las etiquetas que etiquetan algo.
            try:
                for tg_id, tg_name in db.query(
                        "SELECT t.id, t.name FROM rss_tags t WHERE t.account_id = ? "
                        "AND (EXISTS (SELECT 1 FROM rss_feed_tags ft WHERE ft.tag_id = t.id) "
                        "  OR EXISTS (SELECT 1 FROM rss_article_tags at WHERE at.tag_id = t.id)) "
                        "ORDER BY t.position, t.name", (r_id,)):
                    self.tree_nav.AppendItem(
                        n_racc, self._etiqueta_con_contador("#" + tg_name, 0),
                        data={"type": "rss_timeline", "rss_type": f"tag:{tg_id}",
                              "account_id": r_id, "service": "rss", "base_label": "#" + tg_name})
            except Exception as e:
                log.debug(f"No se pudieron cargar las etiquetas RSS: {e}")

            # Categorías (posiblemente anidadas) de ESTA cuenta (auditoría RSS-01).
            todas_cats = db.query("SELECT id, name, COALESCE(parent_id, 0) FROM rss_categories "
                                  "WHERE account_id = ? ORDER BY position, id", (r_id,))
            cats_por_padre = {}
            for c_id, c_name, c_parent in todas_cats:
                cats_por_padre.setdefault(c_parent, []).append((c_id, c_name))
            # Categorías que tienen feeds (directamente) para esta cuenta.
            cats_con_feeds = set()
            for (cf,) in db.query("SELECT DISTINCT category_id FROM rss_feeds WHERE account_id = ? AND category_id IS NOT NULL", (r_id,)):
                if cf is not None:
                    cats_con_feeds.add(cf)

            def _subtree_tiene_feeds(cat_id):
                if cat_id in cats_con_feeds:
                    return True
                return any(_subtree_tiene_feeds(h) for h, _n in cats_por_padre.get(cat_id, []))

            def _render_cats(nodo_padre, padre_id):
                for c_id, c_name in cats_por_padre.get(padre_id, []):
                    if not _subtree_tiene_feeds(c_id):
                        continue
                    feeds = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? AND category_id = ? ORDER BY position, id", (r_id, c_id))
                    total_cat = sum(no_leidos_por_feed.get(f_id, 0) for f_id, _t in feeds)
                    n_cat = self.tree_nav.AppendItem(nodo_padre, self._etiqueta_con_contador(c_name, total_cat),
                                                     data={"type": "rss_category", "category_id": c_id, "account_id": r_id, "service": "rss", "base_label": c_name})
                    # Subcategorías primero, luego los feeds propios.
                    _render_cats(n_cat, c_id)
                    for f_id, f_title in feeds:
                        self.tree_nav.AppendItem(n_cat, self._etiqueta_con_contador(f_title, no_leidos_por_feed.get(f_id, 0)),
                                                 data={"type": "rss_feed", "feed_id": f_id, "account_id": r_id, "service": "rss", "base_label": f_title})

            _render_cats(n_racc, 0)

            # Feeds sin categoría para esta cuenta
            feeds_sin_cat = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? AND (category_id IS NULL OR category_id = '') ORDER BY position, id", (r_id,))
            for f_id, f_title in feeds_sin_cat:
                self.tree_nav.AppendItem(n_racc, self._etiqueta_con_contador(f_title, no_leidos_por_feed.get(f_id, 0)), data={"type": "rss_feed", "feed_id": f_id, "account_id": r_id, "service": "rss", "base_label": f_title})

        return n_rss
