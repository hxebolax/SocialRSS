# -*- coding: utf-8 -*-
"""MainFrame (mixin): acciones_toot.
Metodos: _on_item_selected, _on_item_activated, _on_salir, _on_iconize, _crear_tray_icon, _restaurar_desde_tray, _mostrar_novedades_si_procede, _on_actualizar_buffer_actual ...
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

import os
import sys
import logging
import json
import threading
from contextlib import contextmanager
from datetime import datetime
import wx
from app.core.privacy import abrir_url

from app.core.config_manager import config
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.audio.sound_manager import sound_manager
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.services.telegram.telegram_service import telegram_service


log = logging.getLogger(__name__)
from .ui_helpers import (formatear_tiempo_relativo, evento_sonido_media)


class _Mf_acciones_toot:
    # ------------------------------------------------------------------
    # Botonera de acciones (opcional): botones que cambian según el buffer y actúan sobre el
    # elemento enfocado. Va DESPUÉS del panel de detalles. Reutiliza los handlers/menús ya
    # existentes para no duplicar lógica. Se activa en Preferencias (general.show_action_buttons).
    # ------------------------------------------------------------------
    # --- Red activa y preferencias por red (panel de detalles + botonera) ---
    def _red_activa(self):
        """Identificador de la red del buffer activo: 'mastodon', 'bluesky', 'x', 'telegram',
        'rss', 'reddit', 'peertube' o None."""
        if self.active_mastodon_account_id:
            return "mastodon"
        if self.active_bluesky_account_id:
            return "bluesky"
        if self.active_x_account_id:
            return "x"
        if self.active_telegram_account_id:
            return "telegram"
        if self.active_rss_account_id:
            return "rss"
        if getattr(self, "active_reddit_account_id", None):
            return "reddit"
        if getattr(self, "active_peertube_account_id", None):
            return "peertube"
        return None

    def _panel_detalles_activo(self):
        """True si el panel de detalles debe verse. En la BANDEJA usa su propia preferencia
        (inbox.show_details_panel); en una red, su preferencia. Telegram no tiene panel."""
        if getattr(self, "_bandeja_activa", False):
            return bool(config.get("inbox", "show_details_panel", True))
        red = self._red_activa()
        if not red or red == "telegram":
            return False
        return bool(config.get(red, "show_details_panel", red != "mastodon"))

    def _botonera_activa(self):
        """True si la botonera de acciones está activada. En la BANDEJA usa inbox.show_action_buttons;
        en una red, la preferencia de esa red."""
        if getattr(self, "_bandeja_activa", False):
            return bool(config.get("inbox", "show_action_buttons", False))
        red = self._red_activa()
        if not red:
            return False
        return bool(config.get(red, "show_action_buttons", False))

    def _aplicar_visibilidad_detalles(self):
        """Muestra u oculta el panel de detalles según la preferencia de la red activa. Centraliza
        lo que antes hacía cada servicio por su cuenta, para que sea opcional en todas las redes."""
        if not hasattr(self, "txt_detalles"):
            return
        ver = self._panel_detalles_activo()
        if ver and not self.txt_detalles.IsShown():
            self.txt_detalles.Show()
            self.panel_der.Layout()
        elif not ver and self.txt_detalles.IsShown():
            self.txt_detalles.Hide()
            self.panel_der.Layout()

    def _idx_enfocado(self):
        """Índice de la fila enfocada en la lista, o None si no hay una válida."""
        idx = self.lista_items.GetFocusedItem()
        if idx is not None and 0 <= idx < len(self.lista_items_db_ids):
            return idx
        return None

    def _db_id_enfocado(self):
        """db_id del elemento actualmente enfocado en la lista, o None."""
        idx = self._idx_enfocado()
        return self.lista_items_db_ids[idx] if idx is not None else None

    def _abrir_menu_botonera(self):
        """Abre el menú contextual del elemento enfocado (es dinámico por buffer en cada red)."""
        try:
            self._on_context_menu(None)
        except Exception:
            pass

    def _botones_botonera(self):
        """Devuelve la lista de (etiqueta, callback) de la botonera según la red y el buffer
        activos. Cada red ofrece sus acciones más comunes sobre el elemento enfocado y un botón que
        abre el menú contextual completo ("Más acciones..." / "Acciones...")."""
        idx = self._idx_enfocado()
        if idx is None:
            return []
        db_id = self.lista_items_db_ids[idx]
        mas = (_("Más acciones..."), self._abrir_menu_botonera)
        solo = (_("Acciones..."), self._abrir_menu_botonera)

        if self.active_mastodon_account_id:
            modo = getattr(self, "_modo_lista", "toots")
            if modo in ("cuentas", "anuncios"):
                return [solo]
            # Buffers que llevan un toot (timelines, favoritos, marcadores, menciones, notif, DMs...)
            es_dm = (self.active_timeline_type == "direct")
            botones = [
                (_("Responder"), lambda: self._on_responder_toot(None)),
                (_("Favorito"), lambda: self._on_boost_fav_toot(None)),
            ]
            if not es_dm:
                botones.append((_("Impulsar"), lambda d=db_id: self._on_impulsar_toot(d)))
            botones.append((_("Conversación"), lambda d=db_id: self._on_ver_detalles_toot(d)))
            botones.append(mas)
            return botones

        if self.active_bluesky_account_id:
            base = self.bluesky_ctrl.botones_botonera(db_id)
            return base + [mas] if base else [solo]

        if self.active_x_account_id and self.x_ctrl is not None:
            base = self.x_ctrl.botones_botonera(db_id)
            return base + [mas] if base else [solo]

        if getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None):
            base = self.peertube_ctrl.botones_botonera(db_id)
            return base + [mas] if base else [solo]

        if self.active_telegram_account_id:
            return [(_("Abrir conversación"), lambda i=idx: self._abrir_conversacion_telegram(i)), solo]

        if self.active_rss_account_id:
            row = db.query_one("SELECT read, favorite FROM rss_articles WHERE id = ?", (db_id,))
            botones = []
            if row:
                leido, fav = row
                botones.append((_("Marcar no leído") if leido else _("Marcar leído"),
                                lambda i=idx, d=db_id, v=leido: self._on_toggle_read_rss(i, d, v)))
                botones.append((_("Quitar destacado") if fav else _("Destacar"),
                                lambda i=idx, d=db_id, v=fav: self._on_toggle_fav_rss(i, d, v)))
            botones.append(solo)
            return botones
        return []

    def _envolver_accion_bandeja(self, accion):
        """Envuelve la acción de un botón de la botonera EN LA BANDEJA para que, al pulsarlo, se fije
        el contexto de la RED de la fila enfocada (como el menú contextual y los atajos), se ejecute y
        se restaure. Sin esto, al pulsar el botón el contexto ya no sería el de la fila y fallaría."""
        def _wrap():
            idx = self._idx_enfocado()
            if idx is None:
                return
            g = self._fijar_contexto_bandeja(idx)
            try:
                accion()
            finally:
                if g is not None:
                    self._restaurar_contexto_bandeja(g)
        return _wrap

    def _actualizar_botonera(self):
        """Reconstruye la botonera según el buffer y el elemento enfocado. Solo si está activada en
        Preferencias para la red (o la bandeja). Si no hay nada que ofrecer, se oculta. En la bandeja,
        los botones son los de la RED de la fila enfocada (dinámicos, como el menú contextual)."""
        if not hasattr(self, "panel_botonera"):
            return
        if not self._botonera_activa():
            if self.panel_botonera.IsShown():
                self.panel_botonera.Hide()
                self.panel_der.Layout()
            return
        es_bandeja = getattr(self, "_bandeja_activa", False)
        # En la bandeja, construir los botones CON el contexto de la fila fijado (así _botones_botonera
        # despacha por la red de la fila); restaurar enseguida y envolver cada acción para el clic.
        guardado = None
        if es_bandeja:
            idx = self._idx_enfocado()
            if idx is None:
                if self.panel_botonera.IsShown():
                    self.panel_botonera.Hide()
                    self.panel_der.Layout()
                return
            guardado = self._fijar_contexto_bandeja(idx)
        try:
            botones = self._botones_botonera()
        finally:
            if guardado is not None:
                self._restaurar_contexto_bandeja(guardado)
        self.sizer_botonera.Clear(True)
        for etiqueta, accion in botones:
            fn = self._envolver_accion_bandeja(accion) if es_bandeja else accion
            b = wx.Button(self.panel_botonera, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, lambda e, f=fn: f())
            self.sizer_botonera.Add(b, 0, wx.ALL, 2)
        self.panel_botonera.Show(bool(botones))
        self.panel_der.Layout()

    def _precargar_relacion_debounce(self, db_id):
        """Programa, con ANTI-REBOTE (350 ms), consultar si sigues al autor del toot enfocado, para
        que al abrir el menú ya diga 'Seguir' o 'Dejar de seguir' en vez de la etiqueta ambigua.
        Deduplica (no consulta si ya se conoce ni si el toot es tuyo) y va en segundo plano, así al
        navegar rápido no se dispara una consulta por cada fila."""
        try:
            t = getattr(self, "_rel_precache_timer", None)
            if t is not None:
                t.Stop()
        except Exception:
            pass

        def _lanzar():
            try:
                if (self._relacion_autor_cacheada(db_id) is None
                        and not self._toot_es_mio(db_id)):
                    self._precargar_relacion_autor(db_id)
            except Exception:
                pass
        try:
            self._rel_precache_timer = wx.CallLater(350, _lanzar)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # MARCADORES DE LECTURA sincronizados de Mastodon (auditoría A11)
    # ------------------------------------------------------------------
    def _programar_marcador_lectura(self, index):
        """Programa (con debounce de 3 s) el envío al servidor de la posición de lectura
        del buffer activo de Mastodon. Reiniciado en cada movimiento: solo se envía
        cuando te detienes, y solo si AVANZA respecto al último marcador enviado."""
        try:
            timer = getattr(self, "_marker_timer", None)
            if timer is not None and timer.IsRunning():
                timer.Stop()
            acc = self.active_mastodon_account_id
            tipo = self.active_timeline_type
            self._marker_pendiente = (acc, tipo, index)
            self._marker_timer = wx.CallLater(3000, self._enviar_marcador_lectura)
        except Exception:
            pass

    def _enviar_marcador_lectura(self):
        pendiente = getattr(self, "_marker_pendiente", None)
        if not pendiente:
            return
        acc, tipo, index = pendiente
        # Verificar que sigues en el mismo buffer (el debounce pudo sobrevivir a un cambio).
        if self.active_mastodon_account_id != acc or self.active_timeline_type != tipo:
            return
        timeline_srv = "home" if tipo == "home" else "notifications"
        last_id = None
        try:
            if tipo == "home":
                if 0 <= index < len(self.lista_items_db_ids):
                    fila = db.query_one("SELECT status_id FROM mastodon_statuses WHERE id = ?",
                                        (self.lista_items_db_ids[index],))
                    last_id = fila[0] if fila else None
            else:
                # Notificaciones/Menciones: el marcador usa el ID de NOTIFICACIÓN (no el del
                # toot), consultado por la clave primaria (nunca troceando la clave interna).
                notif_ids = getattr(self, "_notif_db_ids", [])
                if 0 <= index < len(notif_ids):
                    fila = db.query_one(
                        "SELECT notification_id FROM mastodon_notifications WHERE id = ?",
                        (notif_ids[index],))
                    last_id = fila[0] if fila else None
        except Exception:
            last_id = None
        if not last_id:
            return
        # Solo avanzar: no retroceder el marcador por subir a releer algo antiguo.
        enviados = getattr(self, "_marker_enviados", None)
        if enviados is None:
            enviados = self._marker_enviados = {}
        clave = (acc, timeline_srv)
        try:
            if int(last_id) <= int(enviados.get(clave, 0)):
                return
        except (TypeError, ValueError):
            pass
        enviados[clave] = last_id
        mastodon_service.reportar_marcador_lectura(acc, timeline_srv, last_id)

    def _restaurar_marcador_lectura(self, habia_posicion_local=None):
        """Al entrar por PRIMERA vez en la sesión a Inicio/Notificaciones/Menciones (sin
        posición local recordada), restaura tu posición desde el marcador del servidor:
        retomas donde lo dejaste, también si venías de otro cliente (auditoría A11).

        'habia_posicion_local' lo calcula el llamador ANTES de _restaurar_foco_lista, que
        deja apuntada la fila enfocada (la primera, en una primera visita): mirarlo aquí
        daba siempre "ya hay posición" y el marcador del servidor no se aplicaba nunca."""
        tipo = self.active_timeline_type
        acc = self.active_mastodon_account_id
        if not acc or tipo not in ("home", "notifications", "mentions"):
            return
        clave_buffer = self._buffer_key()
        if habia_posicion_local is None:
            habia_posicion_local = clave_buffer in getattr(self, "_buffer_focus", {})
        restaurados = getattr(self, "_markers_restaurados", None)
        if restaurados is None:
            restaurados = self._markers_restaurados = set()
        if clave_buffer in restaurados:
            return
        restaurados.add(clave_buffer)
        timeline_srv = "home" if tipo == "home" else "notifications"
        gen = getattr(self, "_nav_gen", 0)
        # Fila enfocada al pedir el marcador: si te mueves antes de que llegue la respuesta,
        # no se te arrastra a otro sitio.
        foco_al_pedir = self._buffer_focus.get(clave_buffer)

        def _fin(last_id, err):
            if err or not last_id or not self:
                return
            # El marcador del servidor es el punto de partida de "solo avanzar": así, si
            # otro cliente ya lo llevó más lejos, al navegar aquí por algo anterior no se
            # retrocede (antes se partía de cero en cada sesión y se pisaba).
            enviados = getattr(self, "_marker_enviados", None)
            if enviados is None:
                enviados = self._marker_enviados = {}
            try:
                clave_m = (acc, timeline_srv)
                if int(last_id) > int(enviados.get(clave_m, 0)):
                    enviados[clave_m] = str(last_id)
            except (TypeError, ValueError):
                pass
            if habia_posicion_local:
                return  # ya hay posición local de esta sesión (o recordada): manda ella
            if not self._nav_vigente(gen):
                return  # cambiaste de buffer mientras llegaba la respuesta
            if self._buffer_focus.get(clave_buffer) != foco_al_pedir:
                return  # ya te has movido por el buffer: no arrastrarte
            idx = self._indice_de_marcador(tipo, str(last_id))
            if idx is None or idx == self.lista_items.GetFocusedItem():
                return
            try:
                self.lista_items.Focus(idx)
                self.lista_items.Select(idx)
                self.lista_items.EnsureVisible(idx)
                claves = self._claves_foco()
                if 0 <= idx < len(claves):
                    self._buffer_focus[clave_buffer] = claves[idx]
                self._buffer_focus_idx[clave_buffer] = idx
            except Exception:
                pass
        mastodon_service.obtener_marcador_async(acc, timeline_srv, callback=_fin)

    def _indice_de_marcador(self, tipo, last_id):
        """Fila que corresponde al marcador: la exacta o, si ya no está, la más reciente
        NO posterior a él (respetando el orden visual del buffer)."""
        def _num(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return None
        objetivo = _num(last_id)
        candidato = None
        candidato_id = None
        if tipo == "home":
            ids = [d.split(":", 1)[1] if ":" in d else "" for d in self.lista_items_db_ids]
        else:
            # Una sola consulta para todo el buffer (antes, una por fila en el hilo de la UI).
            externos = dict(db.query(
                "SELECT id, notification_id FROM mastodon_notifications WHERE account_id = ?",
                (self.active_mastodon_account_id,)))
            ids = [str(externos.get(ndb) or "") for ndb in getattr(self, "_notif_db_ids", [])]
        for i, sid in enumerate(ids):
            if sid == last_id:
                return i
            n = _num(sid)
            if objetivo is not None and n is not None and n <= objetivo:
                if candidato_id is None or n > candidato_id:
                    candidato, candidato_id = i, n
        return candidato

    def _on_item_selected(self, event):
        index = event.GetIndex()
        if index < 0 or index >= len(self.lista_items_db_ids):
            return

        # Recordar la posición del usuario en este buffer para restaurarla luego (por la
        # clave ÚNICA de la fila: en Notificaciones, el id de notificación, no el del toot).
        claves = self._claves_foco()
        if index < len(claves):
            self._buffer_focus[self._buffer_key()] = claves[index]
        # También el índice numérico: si el elemento desaparece del buffer (p. ej. al
        # marcarse leído en "No leídos"), se restaura esta posición en vez de ir al primero.
        self._buffer_focus_idx[self._buffer_key()] = index

        # Movimiento SILENCIOSO (entró una novedad por encima y se recoloca el foco sobre TU
        # mismo elemento): no ha cambiado el elemento, así que no se rehace nada (botonera,
        # sonidos, marcador, relación, detalles). Rehacerlo en cada novedad congelaba la
        # interfaz varios segundos (panel_der.Layout) y repetía los sonidos de tu elemento.
        # Barra de estado: reflejar la posición actual (elemento X de N) en el campo informativo.
        self._actualizar_barra_estado()
        _db_sel = self.lista_items_db_ids[index]
        if (getattr(self.lista_items, "_silencioso", False)
                and _db_sel == getattr(self, "_ultimo_db_id_seleccionado", None)):
            return
        self._ultimo_db_id_seleccionado = _db_sel
        # Recordar que en este servicio estabas DENTRO del buffer (en la lista),
        # para que Alt+1..4 te devuelva a la lista y no al árbol. La bandeja de entrada se trata
        # como un "servicio" más (no tiene cuenta activa, así que se identifica por su modo).
        _serv = "inbox" if getattr(self, "_modo_lista", "") == "bandeja" else self._servicio_activo()
        if _serv:
            self._ultimo_panel_servicio[_serv] = "lista"

        db_id = self.lista_items_db_ids[index]

        # Marcador de LECTURA sincronizado (auditoría Mastodon A11): al moverte por Inicio,
        # Notificaciones o Menciones, la posición se envía al servidor CON DEBOUNCE (no en
        # cada pulsación) para que otros clientes retomen donde lo dejaste. Antes se
        # marcaba al cargar el buffer (aunque no hubieras leído nada) y las notificaciones
        # ni siquiera llegaban a marcarse.
        if (self.active_mastodon_account_id
                and self.active_timeline_type in ("home", "notifications", "mentions")):
            self._programar_marcador_lectura(index)

        # Actualizar la botonera de acciones (si está activada) para el elemento ahora enfocado.
        self._actualizar_botonera()

        # BANDEJA DE ENTRADA: el panel de detalles se rellena según la RED de la fila enfocada
        # (dinámico, como el menú contextual y la botonera). No se sigue a las ramas por red de abajo
        # (en la bandeja no hay una red activa única).
        if getattr(self, "_bandeja_activa", False):
            if hasattr(self, "txt_detalles") and self.txt_detalles.IsShown():
                self._rellenar_detalle_bandeja(index)
            return

        # En la lista de chats de Telegram NO se reproduce sonido de multimedia;
        # los sonidos identificativos solo suenan dentro de la conversación y en
        # las propiedades del chat.
        if self.active_telegram_account_id:
            return

        if self.active_bluesky_account_id:
            self.bluesky_ctrl.on_item_selected(index)
            return

        if getattr(self, "active_reddit_account_id", None):
            if getattr(self, "reddit_ctrl", None) is not None:
                self.reddit_ctrl.on_item_selected(index)
            return

        # Mismo cinturón que el Intro y el refresco: solo si la lista mostrada es de PeerTube.
        if (getattr(self, "active_peertube_account_id", None)
                and str(getattr(self, "_modo_lista", "")).startswith("peertube_")):
            if getattr(self, "peertube_ctrl", None) is not None:
                self.peertube_ctrl.on_item_selected(index)
            return

        if self.active_x_account_id:
            if self.x_ctrl is not None:
                self.x_ctrl.on_item_selected(index)
            return

        if self.active_mastodon_account_id:
            # Saber si sigues al autor para que el menú contextual muestre la acción correcta.
            self._precargar_relacion_debounce(db_id)
            # El panel de detalles está OCULTO en Mastodon: cuando no se ve, NO construimos ni
            # volcamos su texto al navegar (ahorra trabajo por pulsación y la navegación es más
            # fluida, sobre todo tras horas abierta). Los sonidos de estado sí suenan siempre.
            mostrar_detalles = self.txt_detalles.IsShown()
            row = db.query_one("""
                SELECT author_name, author_username, content, created_at, favourited, reblogged, visibility, spoiler_text, media_json, reblog_by_name, reblog_by_username, COALESCE(has_poll, 0), filtered_by
                FROM mastodon_statuses WHERE id = ?
            """, (db_id,))
            if row:
                author, user, content, date, fav, rebl, vis, cw, media, reblog_by_name, reblog_by_username, has_poll, filtered_by = row
                # Filtro del servidor: con la multimedia protegida (difuminar, o aviso sin revelar)
                # no suena su sonido al enfocar ni se lee su texto alternativo en los detalles.
                from app.services.mastodon import filtros_servidor as _fsrv
                _tl_sel = str(self.active_timeline_type or "")
                _ev_sel = _fsrv.evaluar(filtered_by, self.active_mastodon_account_id, _tl_sel,
                                        foco_detalle=_tl_sel.startswith("thread:")
                                        and str(db_id).endswith(":" + _tl_sel.split(":", 1)[1]))
                if _fsrv.media_filtrada(_ev_sel, db_id):
                    media = None

                # Reproducir sonidos de estado (estilo TWBlue), distinguiendo el tipo de multimedia.
                # Es lo único imprescindible al navegar, así que va SIEMPRE (también con panel oculto).
                # El sonido de DIRECTO identifica los DM en las listas/buffers (Inicio, Directos…) y se
                # gobierna con su sonido en Preferencias > Sonidos. PERO dentro de una CONVERSACIÓN
                # concreta abierta como buffer (hilo, "thread:") suena en cada mensaje y molesta, así
                # que ahí solo suena si el usuario lo activa en Accesibilidad. Si se calla, deja sonar
                # el de CW/multimedia del mensaje, que sí aporta.
                _en_conversacion = str(self.active_timeline_type or "").startswith("thread:")
                _sonar_dm = (not _en_conversacion) or config.get(
                    "accessibility", "direct_sound_in_dm_buffer", False)
                if has_poll:
                    event_bus.emit("sound_trigger", "status_has_poll")
                elif vis == "direct" and _sonar_dm:
                    event_bus.emit("sound_trigger", "status_direct")
                elif cw:
                    event_bus.emit("sound_trigger", "status_has_cw")
                elif media:
                    try:
                        attachments = json.loads(media)
                        if attachments:
                            tipo_media = attachments[0].get("type", "")
                            evento = evento_sonido_media(tipo_media) or "status_has_media"
                            event_bus.emit("sound_trigger", evento)
                    except Exception:
                        pass

                # Panel de detalles: solo si está visible (en Mastodon no lo está).
                if mostrar_detalles:
                    try:
                        dt = datetime.fromisoformat(date.replace("Z", "+00:00"))
                        fecha_str = dt.strftime("%d/%m/%Y %H:%M:%S")
                    except Exception:
                        fecha_str = date.replace("T", " ")[:19]
                    vis_map = {
                        "public": _("Público"), "unlisted": _("No listado"),
                        "private": _("Privado"), "direct": _("Mensaje directo")
                    }
                    vis_str = vis_map.get(vis, vis)
                    reacc_list = []
                    if fav: reacc_list.append(_("Favorito"))
                    if rebl: reacc_list.append(_("Boosteado"))
                    reacc_str = ", ".join(reacc_list) if reacc_list else _("Ninguna")
                    detalles_txt = f"Autor: {author} ({user})\n"
                    if reblog_by_name:
                        detalles_txt += _("Impulsado por: {name} {user}\n").format(name=reblog_by_name, user=reblog_by_username or "")
                    detalles_txt += f"Fecha: {fecha_str}\n"
                    detalles_txt += f"Visibilidad: {vis_str}\n"
                    if cw:
                        detalles_txt += f"ADVERTENCIA DE CONTENIDO: {cw}\n"
                    detalles_txt += f"Reacciones: {reacc_str}\n"
                    for _lin in _fsrv.lineas_detalle(_ev_sel):
                        detalles_txt += _lin + "\n"
                    if _fsrv.oculta_texto(_ev_sel, db_id):
                        content = _("(Texto oculto por el filtro. Usa «Mostrar de todos modos» del menú contextual o Ver publicación.)")
                    if has_poll:
                        detalles_txt += _("[Contiene una encuesta. Use el menú contextual: Ver publicación y encuesta.]\n")
                    if media:
                        try:
                            for att in json.loads(media):
                                detalles_txt += f"[Adjunto ({att.get('type')}): {att.get('description') or _('Sin descripción')}]\n"
                        except Exception:
                            pass
                    detalles_txt += "\n" + content
                    self.txt_detalles.SetValue(detalles_txt)
            elif mostrar_detalles:
                # Intentar buscar como notificación sin estatus asociado (follow, follow_request, poll)
                notif_row = db.query_one("""
                    SELECT type, created_at, from_name, from_username
                    FROM mastodon_notifications WHERE id = ?
                """, (db_id,))
                if notif_row:
                    ntype, ndate, nname, nuser = notif_row
                    try:
                        dt = datetime.fromisoformat(ndate.replace("Z", "+00:00"))
                        fecha_str = dt.strftime("%d/%m/%Y %H:%M:%S")
                    except Exception:
                        fecha_str = ndate.replace("T", " ")[:19]
                    
                    type_labels = {
                        "follow": _("Seguimiento"),
                        "follow_request": _("Petición de seguimiento"),
                        "poll": _("Encuesta"),
                    }
                    ntype_label = type_labels.get(ntype, ntype.capitalize())
                    
                    detalles_txt = f"Tipo: {ntype_label}\n"
                    detalles_txt += f"De: {nname} ({nuser})\n"
                    detalles_txt += f"Fecha: {fecha_str}\n"
                    
                    if ntype == "follow":
                        detalles_txt += f"\n{nname} ({nuser}) te está siguiendo."
                    elif ntype == "follow_request":
                        detalles_txt += f"\n{nname} ({nuser}) ha solicitado seguirte."
                    elif ntype == "poll":
                        detalles_txt += f"\nUna encuesta ha finalizado."
                    else:
                        detalles_txt += f"\nNotificación de tipo: {ntype}."
                        
                    self.txt_detalles.SetValue(detalles_txt)
                
        elif self.active_rss_account_id:
            # Obtener artículo detallado
            row = db.query_one("""
                SELECT a.title, f.title, a.published_at, a.content, a.link, a.favorite, a.media_url, a.ai_summary, a.has_media
                FROM rss_articles a
                JOIN rss_feeds f ON a.feed_id = f.id
                WHERE a.id = ?
            """, (db_id,))
            if row:
                title, feed, date, content, link, fav, media_url, summary, has_media = row

                # Sonidos para RSS: suenan SIEMPRE al navegar (también con el panel oculto).
                if has_media:
                    event_bus.emit("sound_trigger", "status_has_media")

                # Si el panel de detalles está oculto (opción desactivada), no construimos ni
                # volcamos la previsualización: ahorra trabajo por pulsación y la navegación es
                # más fluida.
                if self.txt_detalles.IsShown():
                    # Vista previa COMPLETA (mf_rss_vista): todo el contenido guardado y, si el
                    # feed solo trae un resumen, el artículo entero descargado en segundo plano.
                    self._mostrar_vista_previa_rss(db_id)

                # NOTA: solo se muestra la previsualización. El artículo NO se
                # marca como leído por el simple hecho de seleccionarlo; eso ocurre
                # al abrirlo (Intro, Ver limpio o Ver HTML), como en SimpleRSS.

    def _on_item_activated(self, event):
        """Doble clic o Enter abre los enlaces del elemento (estilo TWBlue)."""
        index = event.GetIndex()
        # Respaldo: si el evento llega sin índice válido (p. ej. Intro sintetizado),
        # usar el elemento que tiene el foco en la lista.
        if index < 0:
            index = self.lista_items.GetFocusedItem()
        if index < 0 or index >= len(self.lista_items_db_ids):
            return

        # BANDEJA: Enter actúa sobre la fila con el contexto de SU red (como el menú contextual y los
        # atajos). Sin esto no hay red activa y decía "No hay enlaces en esta publicación".
        if getattr(self, "_bandeja_activa", False):
            g = self._fijar_contexto_bandeja(index)
            try:
                self._activar_item_segun_red(index)
            finally:
                if g is not None:
                    self._restaurar_contexto_bandeja(g)
            return
        self._activar_item_segun_red(index)

    def _activar_item_segun_red(self, index):
        """Abre el elemento 'index' según la red ACTIVA (enlaces del toot/artículo, conversación de
        Telegram, etc.). Lo usan el buffer normal y la bandeja (con el contexto de la fila fijado)."""
        if index < 0 or index >= len(self.lista_items_db_ids):
            return

        # En Telegram, Enter abre la ventana de conversación del chat
        if self.active_telegram_account_id:
            self._abrir_conversacion_telegram(index)
            return

        # En Bluesky, Enter abre los enlaces del post (delegado al controlador). Cinturón por modo de
        # lista: si un estado residual dejara la cuenta de Bluesky "activa" estando en otra red, el
        # Intro de esa red NO debe caer aquí (los buffers de Bluesky usan modo "bluesky_*").
        if self.active_bluesky_account_id and str(getattr(self, "_modo_lista", "")).startswith("bluesky_"):
            self.bluesky_ctrl.on_item_activated(index)
            return

        # En W Social, Enter abre lo que corresponda a la fila: la conversación de una
        # publicación, el perfil de una cuenta, el chat de una conversación... Cinturón por modo
        # de lista, igual que en las demás redes.
        if (getattr(self, "active_wsocial_account_id", None)
                and getattr(self, "wsocial_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("wsocial_")):
            self.wsocial_ctrl.abrir_elemento(self.lista_items_db_ids[index])
            return

        # En Reddit, Enter abre el visor de comentarios del hilo (Tanda 2). Cinturón por modo de
        # lista: solo si la lista mostrada es de Reddit (modo "reddit_*").
        if (getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("reddit_")):
            self.reddit_ctrl.on_item_activated(index)
            return

        if (getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("peertube_")):
            self.peertube_ctrl.on_item_activated(index)
            return

        # En X, Enter abre los enlaces del tweet (o el tweet/perfil) — delegado al controlador. Mismo
        # cinturón: solo si la lista mostrada es de X (modo "x_*"). Sin esto, un active_x_account_id
        # residual (p. ej. fijado por un sondeo de fondo) tragaba el Intro de Mastodon y no abría el
        # enlace hasta cambiar de buffer.
        if (self.active_x_account_id and self.x_ctrl is not None
                and str(getattr(self, "_modo_lista", "")).startswith("x_")):
            self.x_ctrl.on_item_activated(index)
            return

        # En IRC, Enter abre el chat (delegado al controlador). Se exige TAMBIÉN que la lista
        # mostrada sea de IRC (cinturón: si un estado residual dejara la cuenta IRC "activa"
        # estando en otra red, el Intro de esa red no debe caer aquí).
        if (self.active_irc_account_id and getattr(self, "irc_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "irc_chats"):
            self.irc_ctrl.on_item_activated(index)
            return

        # En Matrix, Enter abre la sala/invitación (mismo cinturón por modo de lista).
        if (self.active_matrix_account_id and getattr(self, "matrix_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "matrix_salas"):
            self.matrix_ctrl.on_item_activated(index)
            return

        # En XMPP, Enter abre la conversación / gestiona una solicitud (cinturón por modo de lista).
        if (self.active_xmpp_account_id and getattr(self, "xmpp_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "xmpp_chats"):
            self.xmpp_ctrl.on_item_activated(index)
            return

        db_id = self.lista_items_db_ids[index]

        urls = []
        if self.active_mastodon_account_id:
            row = db.query_one("SELECT urls_json, content, uri FROM mastodon_statuses WHERE id = ?", (db_id,))
            if row:
                if row[0]:
                    try:
                        urls = json.loads(row[0])
                    except Exception:
                        urls = []
                # Respaldo: extraer URLs del texto si urls_json está vacío (toots antiguos)
                if not urls and row[1]:
                    import re
                    urls = re.findall(r'https?://[^\s\)\]]+', row[1])
                # Último recurso: el enlace de la propia publicación
                if not urls and row[2]:
                    urls = [row[2]]
        elif self.active_rss_account_id:
            # Preferencias → RSS → "Al pulsar Intro sobre un artículo": lector limpio o
            # preguntar (en el buffer RSS y en la bandeja). Si no, lo de siempre: el enlace.
            if self._abrir_articulo_rss_intro(db_id, index):
                return
            # Al abrir con Intro, marcar el artículo como leído
            self._marcar_leido_rss(db_id, index)
            row = db.query_one("SELECT urls_json FROM rss_articles WHERE id = ?", (db_id,))
            if row and row[0]:
                try:
                    urls = json.loads(row[0])
                except Exception:
                    pass

        # Abrir URLs
        if not urls:
            speak(_("No hay enlaces en esta publicación."), interrupt=True)
            event_bus.emit("sound_trigger", "warning")
            return
            
        from app.core.privacy import limpiar_url
        if len(urls) == 1:
            url = limpiar_url(urls[0])
            speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
            abrir_url(url)
        else:
            # Mostrar diálogo modal accesible de selección
            dlg = wx.SingleChoiceDialog(
                self,
                _("Seleccione el enlace que desea abrir:"),
                _("Abrir Enlace"),
                urls
            )
            if dlg.ShowModal() == wx.ID_OK:
                url = limpiar_url(dlg.GetStringSelection())
                speak(_("Abriendo enlace: {0}").format(url), interrupt=True)
                abrir_url(url)
            dlg.Destroy()

    # --- CONTROLADOR DE ATENCION DE MENÚS Y EVENTOS ---
    def _on_salir(self, event):
        # Con "Minimizar a la bandeja del sistema al cerrar" activo, la X y Alt+F4 NO
        # salen (ni preguntan): la ventana se va a la bandeja, como en otros clientes.
        # Solo aplica al evento de CIERRE de la ventana (tiene Veto); salir desde el menú
        # Archivo → Salir o desde la bandeja sigue cerrando de verdad (con su confirmación).
        if (event is not None and hasattr(event, "Veto") and event.CanVeto()
                and config.get("general", "minimize_to_tray", False)
                and not getattr(self, "_reiniciando", False)
                and not getattr(self, "_cerrando", False)):
            event.Veto()
            self._crear_tray_icon()
            self.Hide()
            speak(_("SocialRSS minimizado a la bandeja del sistema."), interrupt=True)
            return
        # Si estamos REINICIANDO para aplicar plantillas, no preguntamos "¿seguro que quieres
        # salir?": el usuario ya confirmó en el diálogo de plantillas que quiere reiniciar.
        if config.get("general", "confirm_exit", True) and not getattr(self, "_reiniciando", False):
            dlg = wx.MessageDialog(
                self,
                _("¿Seguro que desea salir de SocialRSS Accessible Suite?"),
                _("Confirmar salida"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
            )
            confirmado = dlg.ShowModal() == wx.ID_YES
            dlg.Destroy()
            if not confirmado:
                # Si viene de cerrar la ventana (EVT_CLOSE), vetar el cierre
                if event is not None and hasattr(event, "Veto"):
                    event.Veto()
                return

        # CIERRE RÁPIDO Y SIN COLGARSE. Antes, cerrar Telegram (pyrogram/PyTgCalls) bloqueaba el
        # hilo principal hasta 15 s (fut.result + thread.join) y a veces dejaba hilos nativos vivos
        # que impedían terminar (había que matar el proceso). Eso congelaba la UI y enmudecía al
        # lector. Ahora: guardados rápidos en el hilo principal; teardown que puede colgarse en
        # segundo plano; la ventana se oculta (se percibe cerrada ya); y un vigilante fuerza la
        # salida pase lo que pase.
        self._cerrando = True
        event_bus.emit("sound_trigger", "app_exit")

        # Avisar a los complementos de que la app se cierra (limpieza propia).
        try:
            from app.core.plugins import plugin_manager, PluginHook
            plugin_manager.dispatch(PluginHook.APP_CLOSING)
        except Exception:
            pass

        # Retirar el filtro de teclas globales del reproductor.
        try:
            if getattr(self, "_filtro_teclas_reproductor", None):
                wx.GetApp().RemoveFilter(self._filtro_teclas_reproductor)
        except Exception:
            pass

        # 1) Rechazar trabajo nuevo y pedir cancelación a las tareas en curso, ANTES de
        #    empezar a desmontar nada (así ninguna tarea se encuentra la BD cerrada).
        try:
            from app.core.task_manager import task_manager
            task_manager.shutdown(plazo=0)  # el drenaje con plazo ocurre en el teardown
        except Exception:
            pass
        # 2) Guardados IMPRESCINDIBLES y rápidos (hilo principal): posiciones y configuración.
        for fn in (self._persistir_posiciones_buffer, config.save):
            try:
                fn()
            except Exception:
                pass
        # 3) Parar lo instantáneo.
        try:
            if hasattr(self, "_auto_timer") and self._auto_timer.IsRunning():
                self._auto_timer.Stop()
        except Exception:
            pass
        try:
            self.UnregisterHotKey(self._HOTKEY_PLAYER)
        except Exception:
            pass
        # 4) Ocultar la ventana y el icono de bandeja: se percibe cerrada al instante.
        try:
            self.Hide()
        except Exception:
            pass
        if self._tray_icon:
            try:
                self._tray_icon.Destroy()
            except Exception:
                pass

        # 5) Teardown ordenado en segundo plano para NO bloquear el hilo principal (ni
        #    enmudecer al lector): primero drenar tareas, después parar streaming y
        #    clientes (Telegram al final de estos, que es el que se cuelga: pyrogram) y,
        #    SOLO al final, cerrar la base de datos (checkpoint del WAL). Antes la BD se
        #    cerraba primero y un servicio a medio parar podía escribir sobre ella cerrada.
        #    `_paso_cierre` deja constancia de qué paso no respondió si hay que forzar.
        self._paso_cierre = "inicio"
        self._cierre_terminado = threading.Event()

        def _apagar_en_segundo_plano():
            def _paso(nombre, fn):
                self._paso_cierre = nombre
                try:
                    fn()
                except Exception as e:
                    log.debug(f"Cierre: el paso '{nombre}' falló: {e}")

            # Drenar las tareas de fondo con un plazo corto (ya tienen la señal de cancelar).
            def _drenar():
                from app.core.task_manager import task_manager
                task_manager.shutdown(plazo=2.0)
            _paso("tareas de fondo", _drenar)

            # X: volcar las cookies actuales (X rota ct0 en memoria) para que el login siga
            # siendo permanente entre arranques. Protegido por el kill-switch y lazy.
            def _x():
                from app.core import feature_flags
                if feature_flags.x_cliente_disponible():
                    from app.services.x.x_service import x_service
                    x_service.detener_todos_streaming_dm()
                    x_service.persistir_sesiones()
            _paso("X (cookies y streaming)", _x)

            def _mastodon():
                for sm in list(getattr(self, "_streamings_mastodon", {}).values()):
                    try:
                        sm.detener()
                    except Exception:
                        pass
            _paso("streaming de Mastodon", _mastodon)

            _paso("firehose de Bluesky", self.bluesky_ctrl.detener_firehose)

            def _wsocial():
                # Para los vigilantes de tiempo real y suelta los clientes abiertos.
                if getattr(self, "wsocial_ctrl", None) is not None:
                    from app.services.wsocial import wsocial_service
                    wsocial_service.cerrar()
            _paso("W Social", _wsocial)
            _paso("motor de audio", sound_manager.liberar)
            _paso("Telegram", telegram_service.apagar)

            # La base de datos SE CIERRA LA ÚLTIMA, cuando ya no queda nadie escribiendo.
            _paso("base de datos", db.close)

            def _temp():
                from app.core.paths import limpiar_temp
                limpiar_temp()
            _paso("limpiar temporales", _temp)

            self._paso_cierre = "terminado"
            self._cierre_terminado.set()
            # Cierre limpio: no hay que esperar al vigilante.
            try:
                sys.stdout.flush(); sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=_apagar_en_segundo_plano, daemon=True).start()

        # 6) Vigilante (ÚLTIMO RECURSO): si el apagado ordenado no termina en el plazo
        #    (hilos nativos de pyrogram que no responden), se registra el paso culpable y
        #    se fuerza la salida. Si el paso colgado es ANTERIOR a la BD, se intenta el
        #    checkpoint igualmente (WAL es seguro ante cortes, pero mejor cerrarla bien).
        def _forzar_salida():
            if self._cierre_terminado.wait(timeout=8.0):
                return  # el teardown ya salió por la vía limpia
            paso = getattr(self, "_paso_cierre", "?")
            log.error(f"Cierre forzado: el paso '{paso}' no respondió en el plazo.")
            if paso != "base de datos":
                try:
                    db.close()
                except Exception:
                    pass
            try:
                sys.stdout.flush(); sys.stderr.flush()
            except Exception:
                pass
            os._exit(0)
        threading.Thread(target=_forzar_salida, daemon=True).start()
        # No llamamos a Destroy() ni event.Skip(): la ventana ya está oculta y el proceso
        # termina cuando acaba el teardown (o lo fuerza el vigilante).

    # --- BANDEJA DEL SISTEMA (minimizar a bandeja) ---
    def _on_activate_app(self, event):
        """Al RECUPERAR el foco de la ventana (Alt+Tab), hacer un catch-up LIGERO del buffer activo,
        con anti-rebote (máx. 1 vez cada 30 s). A diferencia de antes, NO se reinicia el streaming
        ni el temporizador en cada foco (eso daba tirones): el watchdog ya mantiene vivas las
        conexiones y el timer sigue corriendo aunque la ventana no tenga el foco. La reactivación
        COMPLETA se reserva para volver desde la bandeja (la ventana estuvo oculta)."""
        try:
            if event.GetActive():
                # Al cerrar un modal (p. ej. el compositor) la ventana se reactiva: repintar lo que
                # llegó mientras estaba abierto (sin anti-rebote; ver mf_diferido_modal). CallAfter:
                # el modal termina de desmontarse antes.
                if getattr(self, "_pendiente_tras_modal", False):
                    wx.CallAfter(self._ponerse_al_dia_tras_modal)
                import time as _t
                ahora = _t.time()
                if ahora - getattr(self, "_ultima_reactivacion", 0) > 30:
                    self._ultima_reactivacion = ahora
                    self._catchup_ligero_al_enfocar()
        except Exception:
            pass
        event.Skip()

    def _catchup_ligero_al_enfocar(self):
        """Catch-up LIGERO al volver el foco: refresca en su sitio los 'hace X' y los contadores
        (barato) y dispara UN sondeo en segundo plano del buffer activo para traer lo nuevo. No
        toca el streaming ni el temporizador, así que no produce tirones."""
        try:
            self.lista_items.refrescar_todas_las_fechas()
        except Exception:
            pass
        try:
            self._refrescar_contadores_mastodon()
        except Exception:
            pass
        try:
            # Con la BANDEJA activa no sondear por red (aunque su contexto temporal tenga
            # fijada una cuenta): su propio temporizador ya la mantiene al día.
            if getattr(self, "_bandeja_activa", False):
                return
            if self.active_mastodon_account_id:
                self._actualizar_mastodon_incremental()
                self._refrescar_contadores_servidor()
            elif self.active_bluesky_account_id:
                self.bluesky_ctrl.actualizar_incremental()
            elif (self.active_x_account_id and self.x_ctrl is not None
                  and config.get("x", "auto_update", False)):
                # Solo si X está en actualización automática; en manual, se espera a F5.
                self.x_ctrl.actualizar_incremental(forzar=True)
            elif self.active_telegram_account_id:
                self._actualizar_telegram_incremental()
            elif self.active_rss_account_id:
                self._actualizar_rss_incremental()
        except Exception:
            pass

    def _on_iconize(self, event):
        """Al minimizar, ocultar a la bandeja del sistema si está configurado."""
        if event.IsIconized() and config.get("general", "minimize_to_tray", False):
            self._crear_tray_icon()
            self.Hide()
            speak(_("SocialRSS minimizado a la bandeja del sistema."), interrupt=True)
        else:
            event.Skip()

    def _crear_tray_icon(self):
        if self._tray_icon is not None:
            return
        import wx.adv

        frame = self

        def _construir_menu(tray):
            # Menú del icono de la bandeja. Se usa tanto para el clic derecho (CreatePopupMenu, que
            # wx llama solo) como para el clic izquierdo simple (lo mostramos a mano), para que abra
            # como en otros programas accesibles. "Restaurar" es la opción por defecto (negrita).
            menu = wx.Menu()
            restaurar = menu.Append(wx.ID_ANY, _("&Restaurar SocialRSS"))
            f = restaurar.GetFont()
            try:
                f.SetWeight(wx.FONTWEIGHT_BOLD)
                restaurar.SetFont(f)
            except Exception:
                pass
            tray.Bind(wx.EVT_MENU, lambda e: frame._restaurar_desde_tray(), restaurar)
            menu.AppendSeparator()
            salir = menu.Append(wx.ID_ANY, _("&Salir"))
            tray.Bind(wx.EVT_MENU, lambda e: frame._on_salir(None), salir)
            return menu

        class _Tray(wx.adv.TaskBarIcon):
            def CreatePopupMenu(self):
                # wx lo invoca con el clic DERECHO sobre el icono.
                return _construir_menu(self)

        self._tray_icon = _Tray()
        try:
            icono = wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))
            self._tray_icon.SetIcon(icono, "SocialRSS Accessible Suite")
        except Exception:
            pass
        # Un solo CLIC IZQUIERDO abre el menú (muchos programas no muestran nada con el clic izquierdo
        # y por eso "parecía" que el icono no respondía). El DOBLE clic restaura directamente.
        self._tray_icon.Bind(wx.adv.EVT_TASKBAR_LEFT_UP,
                             lambda e: self._tray_icon.PopupMenu(_construir_menu(self._tray_icon)))
        self._tray_icon.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, lambda e: self._restaurar_desde_tray())

    def mostrar_desde_otra_instancia(self):
        """Muestra la ventana a petición de una SEGUNDA instancia (idea de los usuarios: si la
        app está en la bandeja y se vuelve a lanzar el acceso directo, debe abrirse la ventana,
        no despachar con un aviso; según cómo esté configurada la bandeja de Windows, el usuario
        podía perder el acceso al programa). Si estaba en la bandeja, pasa por el MISMO código de
        restaurar (destruye el icono y reactiva el streaming); si no, simplemente se muestra. En
        ambos casos se fuerza el primer plano, porque Windows no deja a un proceso robar el foco
        por las buenas y el Raise() solo haría parpadear la barra de tareas."""
        if self._tray_icon is not None:
            self._restaurar_desde_tray()
        else:
            self.Show()
            self.Iconize(False)
            self.Raise()
        try:
            from app.core.single_instance import forzar_al_frente
            forzar_al_frente(self.GetHandle())
        except Exception:
            pass
        speak(_("SocialRSS"), interrupt=False)

    def _restaurar_desde_tray(self):
        self.Show()
        self.Iconize(False)
        self.Raise()
        if self._tray_icon:
            try:
                self._tray_icon.Destroy()
            except Exception:
                pass
            self._tray_icon = None
        # Al volver de la bandeja: reactivar el streaming del servicio activo (es idempotente) y
        # sondear el buffer YA, para recuperar lo que pudiera haber entrado o dejado de entrar
        # mientras estaba minimizado, sin que el usuario tenga que refrescar a mano.
        self._recuperar_actualizacion_al_volver()

    def _recuperar_actualizacion_al_volver(self):
        """Reactiva el tiempo real del servicio activo y dispara un sondeo del buffer. Se usa al
        restaurar la ventana (p. ej. desde la bandeja), porque el streaming puede haberse caído
        mientras estaba oculta."""
        try:
            self._aplicar_servicio_activo()   # reinicia streaming/intervalo del servicio activo
        except Exception:
            pass
        try:
            if self.active_mastodon_account_id:
                self._actualizar_mastodon_incremental()
            elif self.active_bluesky_account_id:
                self.bluesky_ctrl.actualizar_incremental()
            elif (self.active_x_account_id and self.x_ctrl is not None
                  and config.get("x", "auto_update", False)):
                # Solo si X está en actualización automática; en manual, se espera a F5.
                self.x_ctrl.actualizar_incremental(forzar=True)
        except Exception:
            pass

    def _mostrar_novedades_si_procede(self):
        """Muestra un resumen de novedades la primera vez que se abre una versión nueva."""
        version_actual = "1.0.0"
        try:
            from app.core.version import APP_VERSION as _v
            version_actual = _v
        except Exception:
            pass
        ultima = config.get("general", "last_version", "")
        if ultima == version_actual:
            return
        config.set("general", "last_version", version_actual)
        if not ultima:
            return  # Primera ejecución absoluta: no abrumar con el changelog

        # Preferir el changelog.txt si está disponible. Así basta con compartir o
        # actualizar 'data/docs/changelog.txt' (o un 'changelog.txt' junto al
        # ejecutable) para que se muestre al cambiar de versión. Si no se encuentra,
        # se muestra el resumen incrustado de siempre.
        from app.core.paths import get_data_path
        raiz = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidatos = [
            str(get_data_path("docs/changelog.txt")),
            os.path.join(raiz, "changelog.txt"),
            os.path.join(os.getcwd(), "changelog.txt"),
        ]
        for cand in candidatos:
            try:
                if os.path.exists(cand):
                    with open(cand, "r", encoding="utf-8") as fh:
                        texto_cl = fh.read().strip()
                    if texto_cl:
                        self._mostrar_texto_largo(_("Novedades de esta versión"), texto_cl)
                        return
            except Exception as e:
                log.debug(f"No se pudo leer el changelog '{cand}': {e}")

        # Respaldo: resumen incrustado (comportamiento normal si no hay changelog.txt).
        texto = _(
            "Novedades de SocialRSS {0}:\n\n"
            "• Buffers de Mastodon: Seguidores, Siguiendo, Bloqueados, Silenciados, Anuncios y Tendencias.\n"
            "• Listas, búsquedas guardadas y filtros del servidor.\n"
            "• Corrector ortográfico y autocompletar menciones al redactar.\n"
            "• Reproducir audio de YouTube/SoundCloud desde un enlace.\n"
            "• Paquetes de sonido y dispositivo de salida configurables.\n"
            "• Minimizar a la bandeja del sistema.\n"
            "Consulta el manual (F1) para más detalles."
        ).format(version_actual)
        self._mostrar_texto_largo(_("Novedades de esta versión"), texto)

    # --- SINCRONIZACIÓN ASÍNCRONA ---
    @contextmanager
    def _foco_silencioso(self):
        """Mientras está activo, las recargas de lista restauran el foco SIN que el lector relea el
        elemento (ver _restaurar_foco_lista). Se usa en las actualizaciones manuales para que no se
        cuele la lectura del elemento enfocado entre el aviso de 'Actualizando' y el resultado."""
        prev = getattr(self, "_recarga_silenciosa", False)
        self._recarga_silenciosa = True
        try:
            yield
        finally:
            self._recarga_silenciosa = prev

    def _anunciar_descarga(self, ids_antes, total_nuevos=None):
        """Anuncia cuántos elementos NUEVOS ha traído una actualización manual (o 'sin novedades').

        Si se conoce el número exacto del servicio (total_nuevos, p. ej. RSS), se usa ese; si no, se
        calcula cuántos db_id de la lista actual no estaban antes de actualizar (ids_antes)."""
        if total_nuevos is None:
            try:
                total_nuevos = sum(1 for x in (self.lista_items_db_ids or []) if x not in ids_antes)
            except Exception:
                total_nuevos = 0
        if total_nuevos and total_nuevos > 0:
            notificar(_("{0} nuevos.").format(total_nuevos))
        else:
            notificar(_("Sin novedades."))

    def _on_actualizar_buffer_actual(self, event):
        """Descarga e indexa el timeline/feeds actuales en segundo plano."""
        if self._updating:
            speak(_("El sistema está ocupado actualizando. Espere un momento."), interrupt=True)
            event_bus.emit("sound_trigger", "warning")
            return

        # Token de navegación: si cambias de buffer durante la descarga, la respuesta (tardía) ya no
        # se pinta sobre el buffer nuevo. Los datos quedan igualmente cacheados en la base de datos.
        _gen = getattr(self, "_nav_gen", 0)

        # Estado previo de la lista, para anunciar cuántos elementos NUEVOS se han descargado (o 0).
        ids_antes = set(getattr(self, "lista_items_db_ids", []) or [])

        # Bandeja de entrada: recarga la mezcla de sus fuentes (sus datos los mantiene al día el
        # sondeo de fondo de cada red).
        if getattr(self, "_modo_lista", "") == "bandeja":
            with self._foco_silencioso():
                self._cargar_listado_bandeja()
            self._anunciar_descarga(ids_antes)
            return

        if self.active_bluesky_account_id:
            self.bluesky_ctrl.on_refresh()
            return

        if getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            self.reddit_ctrl.on_refresh()
            return

        # PeerTube. Cinturón por modo de lista (como el Intro): solo si lo que se muestra es de
        # PeerTube. Sin esto, una cuenta de PeerTube residual hacía que Ctrl+Mayús+R contestara
        # "N vídeos nuevos" estando en Mastodon.
        if (getattr(self, "active_peertube_account_id", None)
                and getattr(self, "peertube_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("peertube_")):
            self.peertube_ctrl.on_refresh()
            return

        # W Social. Mismo cinturón que PeerTube: solo si lo que se muestra es suyo, para que el
        # refresco conteste siempre sobre la red que el usuario tiene delante.
        if (getattr(self, "active_wsocial_account_id", None)
                and getattr(self, "wsocial_ctrl", None) is not None
                and str(getattr(self, "_modo_lista", "")).startswith("wsocial_")):
            self.wsocial_ctrl.on_refresh()
            return

        if self.active_x_account_id and self.x_ctrl is not None:
            self.x_ctrl.on_refresh()
            return

        # IRC: la lista se recarga de la BD (los datos llegan en vivo por el protocolo). Solo si
        # la lista mostrada es de IRC (mismo cinturón que el Intro).
        if (self.active_irc_account_id and getattr(self, "irc_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") in ("irc_chats", "irc_avisos")):
            with self._foco_silencioso():
                self.irc_ctrl.cargar_listado()
            speak(_("Actualizado."), interrupt=True)
            return

        # Matrix: igual que IRC (los datos llegan en vivo por el sync).
        if (self.active_matrix_account_id and getattr(self, "matrix_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "matrix_salas"):
            with self._foco_silencioso():
                self.matrix_ctrl.cargar_listado()
            speak(_("Actualizado."), interrupt=True)
            return

        # XMPP: igual (los datos llegan en vivo por el stream).
        if (self.active_xmpp_account_id and getattr(self, "xmpp_ctrl", None) is not None
                and getattr(self, "_modo_lista", "") == "xmpp_chats"):
            with self._foco_silencioso():
                self.xmpp_ctrl.cargar_listado()
            speak(_("Actualizado."), interrupt=True)
            return

        if self.active_mastodon_account_id:
            # Buffers de cuentas o anuncios: se recargan en vivo desde el cargador.
            if self.active_timeline_type in ("followers", "following", "blocked", "muted", "announcements"):
                self._cargar_listado_toots(cache_only=False)
                return
            # Tendencias: descargar y luego mostrar.
            if self.active_timeline_type == "trending":
                speak(_("Actualizando tendencias..."), interrupt=True)
                self._updating = True

                def _cb_trend(res, error):
                    self._updating = False
                    if not self._nav_vigente(_gen):
                        return
                    if error:
                        notificar(_("Error al cargar tendencias: {0}").format(error))
                    else:
                        self._cargar_listado_toots(cache_only=True)
                        notificar(_("Tendencias actualizadas."))

                mastodon_service.cargar_trending_async(self.active_mastodon_account_id, callback=_cb_trend)
                return

            # Búsqueda guardada: re-buscar y mostrar.
            if str(self.active_timeline_type).startswith("search:"):
                termino = self.active_timeline_type.split(":", 1)[1]
                speak(_("Buscando..."), interrupt=True)
                self._updating = True

                def _cb_busq(res, error):
                    self._updating = False
                    if not self._nav_vigente(_gen):
                        return
                    if error:
                        notificar(_("Error en la búsqueda: {0}").format(error))
                    else:
                        self._cargar_listado_toots(cache_only=True)
                        notificar(_("Resultados actualizados."))

                mastodon_service.buscar_y_guardar_async(
                    self.active_mastodon_account_id, termino, self.active_timeline_type, callback=_cb_busq)
                return

            # Lista de Mastodon: descargar su timeline.
            if str(self.active_timeline_type).startswith("list:"):
                list_id = self.active_timeline_type.split(":", 1)[1]
                speak(_("Actualizando lista..."), interrupt=True)
                self._updating = True

                def _cb_lista(res, error):
                    self._updating = False
                    if not self._nav_vigente(_gen):
                        return
                    if error:
                        notificar(_("Error al cargar la lista: {0}").format(error))
                    else:
                        self._cargar_listado_toots(cache_only=True)
                        notificar(_("Lista actualizada."))

                mastodon_service.cargar_timeline_lista_async(
                    self.active_mastodon_account_id, list_id, self.active_timeline_type, callback=_cb_lista)
                return

            speak(_("Actualizando timeline de Mastodon..."), interrupt=True)
            self._updating = True

            def _callback(statuses, error):
                self._updating = False
                if not self._nav_vigente(_gen):
                    return
                if error:
                    from app.core.net import humanizar_error
                    notificar(humanizar_error(error))
                    event_bus.emit("sound_trigger", "mastodon_error")
                else:
                    with self._foco_silencioso():
                        self._cargar_listado_toots(cache_only=False)
                    self._anunciar_descarga(ids_antes)

            mastodon_service.cargar_timeline_async(
                self.active_mastodon_account_id,
                timeline_type=self.active_timeline_type,
                callback=_callback
            )
            
        elif self.active_rss_account_id:
            speak(_("Sincronizando feeds de noticias..."), interrupt=True)
            self._updating = True
            
            def _callback(total_nuevos, error):
                self._updating = False
                if error:
                    notificar(f"Error al sincronizar RSS: {error}")
                    event_bus.emit("sound_trigger", "rss_error")
                else:
                    with self._foco_silencioso():
                        self._cargar_listado_artículos()
                    self._actualizar_contadores_rss()
                    event_bus.emit("sound_trigger", "rss_new_article")
                    # RSS sí sabe el número exacto de artículos nuevos (lo devuelve el servicio).
                    self._anunciar_descarga(ids_antes, total_nuevos=total_nuevos)
                    
            rss_service.actualizar_todos_async(
                self.active_rss_account_id,
                callback=_callback
            )

        elif self.active_telegram_account_id:
            # En el buffer de contactos, F5 fuerza la descarga de contactos
            if self.active_timeline_type == "contacts":
                self._cargar_contactos_telegram(forzar=True)
                return
            speak(_("Sincronizando chats de Telegram..."), interrupt=True)
            self._updating = True
            tg_account = self.active_telegram_account_id

            def _bg_tg(ctx):
                return telegram_service.cargar_chats(tg_account, limit=200)

            def _callback(total, error):
                self._updating = False
                if error:
                    notificar(_("Error al sincronizar Telegram: {0}").format(error))
                    event_bus.emit("sound_trigger", "error")
                else:
                    # Reconstruir el árbol para reflejar las carpetas actualizadas
                    # (nuevas, renombradas o creadas en otro dispositivo); cargar_chats
                    # ya ha refrescado la tabla de carpetas desde el servidor.
                    self._cargar_navegacion()
                    with self._foco_silencioso():
                        self._cargar_listado_chats_telegram()
                    self._anunciar_descarga(ids_antes)

            task_manager.start_task(_bg_tg, callback=_callback, pass_context=True)
        else:
            speak(_("Seleccione primero una cuenta activa para actualizar."), interrupt=True)

    def _sincronizar_al_entrar_mastodon(self):
        """Pide lo nuevo del buffer de Mastodon en el que se ACABA de entrar.

        Es lo que hace que, al cambiar de cuenta o de buffer, veas lo que hay AHORA y no lo que
        había en la cache. Va con un pequeno anti-rebote POR BUFFER (5 s): al recorrer el arbol
        deprisa con las flechas no se lanza una peticion por cada nodo por el que pasas, pero al
        entrar de verdad en uno se pide siempre."""
        import time as _t
        acc = self.active_mastodon_account_id
        tipo = self.active_timeline_type
        if not acc or not tipo:
            return
        clave = (acc, tipo)
        marcas = getattr(self, "_ultima_sync_entrada", None)
        if marcas is None:
            marcas = self._ultima_sync_entrada = {}
        ahora = _t.monotonic()
        if ahora - marcas.get(clave, 0.0) < 5:
            return
        marcas[clave] = ahora
        try:
            self._actualizar_mastodon_incremental()
        except Exception as e:
            log.debug(f"No se pudo sincronizar al entrar en el buffer: {e}")

    def _actualizar_mastodon_incremental(self):
        """Sondea el timeline activo en busca de toots MÁS NUEVOS (min_id) sin mover el foco."""
        if self._updating or not self.active_mastodon_account_id:
            return

        # El sondeo automático aplica a los timelines de publicaciones, notificaciones y menciones
        # y, además, a las CRONOLOGÍAS DE USUARIO (user:) y a las CONVERSACIONES (thread:) que se
        # añaden como buffer permanente: no se streamean (el servidor no ofrece stream por cuenta),
        # así que sin esto no se actualizaban solas —ni siquiera al entrar en ellas— y solo traían
        # novedades con Ctrl+Shift+R (reporte de usuarios). Los buffers de cuentas, anuncios y
        # tendencias siguen refrescándose solo a mano.
        tipo = str(self.active_timeline_type or "")
        es_cronologia = tipo.startswith("user:")
        es_hilo = tipo.startswith("thread:")
        if (self.active_timeline_type not in (
            "home", "local", "public", "direct", "bookmarks", "favourites", "sent", "notifications", "mentions"
        ) and not es_cronologia and not es_hilo):
            return

        # Un hilo cuesta 2-3 peticiones (contexto + mensaje) porque la API no permite pedir "solo
        # lo nuevo" de una conversación: se sondea como mucho una vez por minuto.
        if es_hilo:
            import time as _t
            ahora_h = _t.monotonic()
            if ahora_h - getattr(self, "_ultimo_sondeo_hilo", 0) < 60:
                return
            self._ultimo_sondeo_hilo = ahora_h

        # Determinar el id más reciente que ya tenemos para pedir solo lo nuevo. Menciones y
        # notificaciones comparten la tabla mastodon_notifications.
        if self.active_timeline_type in ("notifications", "mentions"):
            row = db.query_one(
                "SELECT notification_id FROM mastodon_notifications WHERE account_id = ? ORDER BY created_at DESC LIMIT 1",
                (self.active_mastodon_account_id,)
            )
        elif es_cronologia or self.active_timeline_type in (
                "home", "local", "public", "direct", "bookmarks", "favourites", "sent"):
            row = db.query_one("""
                SELECT s.status_id FROM mastodon_statuses s
                JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
                WHERE t.account_id = ? AND t.timeline = ?
                ORDER BY s.created_at DESC LIMIT 1
            """, (self.active_mastodon_account_id, self.active_timeline_type))
        else:
            row = None

        min_id = row[0] if row and row[0] else None
        self._updating = True

        es_notif = self.active_timeline_type in ("notifications", "mentions")
        es_menciones = self.active_timeline_type == "mentions"

        def _callback(statuses, error):
            self._updating = False
            if error:
                log.debug(f"Sondeo incremental de Mastodon falló: {error}")
                return
            if statuses:
                # El callback llega tarde: con la BANDEJA activa no insertar aquí (sus columnas
                # son otras y desalinearía los orígenes); su propio sondeo ya lee de la BD.
                if getattr(self, "_bandeja_activa", False):
                    return
                # Insertar SILENCIOSAMENTE los nuevos en su posición (sin reconstruir
                # ni mover el foco). Solo se avisa con sonido si de verdad entraron (en su
                # buffer: en Menciones solo cuentan las menciones; en Notificaciones, el resto).
                insertados = self._insertar_items_incremental(self._filas_mastodon_actual())
                if insertados:
                    if es_menciones:
                        event_bus.emit("sound_trigger", "mastodon_new_mention")
                    elif es_notif:
                        event_bus.emit("sound_trigger", "mastodon_new_notification")
                    else:
                        event_bus.emit("sound_trigger", "mastodon_new_status")
            # Tras refrescar la BD con la página recién traída, actualizar EN SU SITIO los
            # contadores (favoritos/impulsos/respuestas) de las filas visibles que hayan cambiado,
            # sin mover el foco (igual que se actualiza el "hace X minutos").
            self._refrescar_contadores_mastodon()

        # Para los timelines de BAJO TRÁFICO no pasamos min_id: así cargar_timeline_async
        # RELLENA EL HUECO paginando desde el último guardado y no se pierde ninguna
        # publicación aunque hayan entrado más de 40 entre dos sondeos. La Local y la Federada
        # (firehose) NO se rellenan: se pide solo UNA página de lo más nuevo (min_id), para que
        # actualizar el buffer mientras lo miras no traiga ni vuelque cientos de toots de golpe
        # (eso bloqueaba la interfaz). Es como hace TWBlue.
        rellenables = ("home", "sent", "notifications", "mentions")
        usar_min_id = None if self.active_timeline_type in rellenables else min_id
        mastodon_service.cargar_timeline_async(
            self.active_mastodon_account_id,
            timeline_type=self.active_timeline_type,
            min_id=usar_min_id,
            callback=_callback
        )

    def _refrescar_contadores_mastodon(self):
        """Actualiza EN SU SITIO la columna de contadores (favoritos/impulsos/respuestas) de las
        publicaciones de Mastodon visibles cuyo valor haya cambiado, leyendo los contadores frescos
        de la base de datos (que el sondeo y las acciones mantienen al día). No mueve el foco: es,
        para los contadores, el equivalente del refresco automático del 'hace X minutos'. Solo toca
        las filas que cambiaron, así que es barato aunque se llame a menudo."""
        if not self.active_mastodon_account_id or getattr(self, "_modo_lista", "toots") != "toots":
            return
        try:
            col = self._col_de_campo(getattr(self, "_cols_actuales", []), "counts")
        except Exception:
            col = None
        if col is None:
            return  # la plantilla del buffer no muestra los contadores: nada que refrescar
        # Solo las filas VISIBLES (viewport). Antes se leían y formateaban los contadores de TODAS las
        # filas cargadas (con buffers de miles de toots: un SELECT con miles de parámetros + construir
        # miles de cadenas + recorrer toda la lista, en el hilo de la interfaz), y solo se repintaba lo
        # visible: el resto era trabajo perdido que daba un tirón al recuperar el foco. Lo de fuera de
        # pantalla se refresca al entrar en el viewport (igual que el 'hace X' y que
        # _refrescar_contadores_servidor).
        # OJO: puede llamarse como callback async DESPUÉS de cambiar de buffer/cuenta, con
        # lista_items_db_ids más larga que las filas reales del control. Acotamos SIEMPRE al nº real
        # de filas para no pasarle a wx un índice inválido (assert de wxListCtrl).
        try:
            top = self.lista_items.GetTopItem()
            por_pagina = self.lista_items.GetCountPerPage() + 1
        except Exception:
            return
        n_filas = self.lista_items.GetItemCount()
        total = len(self.lista_items_db_ids or [])
        ini = max(0, top)
        fin = min(total, n_filas, ini + max(1, por_pagina))
        ids = [self.lista_items_db_ids[i] for i in range(ini, fin) if self.lista_items_db_ids[i]]
        if not ids:
            return
        try:
            placeholders = ",".join("?" * len(ids))
            nuevos = {}
            for r in db.query(
                "SELECT id, COALESCE(favourites_count,0), COALESCE(reblogs_count,0), "
                f"COALESCE(replies_count,0) FROM mastodon_statuses WHERE id IN ({placeholders})", ids):
                nuevos[r[0]] = _("{f} favoritos, {r} impulsos, {p} respuestas").format(
                    f=r[1], r=r[2], p=r[3])
        except Exception:
            return
        # NO repintar la fila ENFOCADA: SetItem sobre ella haría que NVDA RELEYERA el elemento donde
        # estás parado, sin haberte movido (rompía la "lectura silenciosa"). Su contador se ve
        # actualizado al navegar a ella (relee con el dato fresco) o al interactuar tú.
        try:
            foco = self.lista_items.GetFocusedItem()
        except Exception:
            foco = -1
        cambios = []
        for i in range(ini, fin):
            if i == foco:
                continue
            db_id = self.lista_items_db_ids[i]
            txt = nuevos.get(db_id)
            if txt is None:
                continue
            try:
                if self.lista_items.texto_celda_completo(i, col) != txt:
                    cambios.append((i, txt))
            except Exception:
                pass
        if not cambios:
            return
        self.lista_items.Freeze()
        try:
            for i, txt in cambios:
                if 0 <= i < self.lista_items.GetItemCount():
                    try:
                        self.lista_items.SetItem(i, col, txt)
                    except Exception:
                        pass
        finally:
            self.lista_items.Thaw()

    def _refrescar_indicadores_toot(self, db_id):
        """Tras favoritear, impulsar o marcar un toot, actualiza EN SU SITIO las columnas de
        indicadores (Favorito/Boosteado/Guardado…) y de contadores de ESE toot, sin reconstruir la
        lista ni mover el foco. Así, al impulsar el toot en el que estás, te quedas en él (el boost
        propio, que es una entrada nueva, entra solo en el siguiente sondeo, en silencio). Sustituye
        a recargar todo el buffer, que movía el foco y hacía releer al lector."""
        if not self.active_mastodon_account_id or getattr(self, "_modo_lista", "toots") != "toots":
            return
        try:
            idx = self.lista_items_db_ids.index(db_id)
        except (ValueError, AttributeError):
            return
        cols = getattr(self, "_cols_actuales", [])
        col_ind = self._col_de_campo(cols, "indicators")
        col_cnt = self._col_de_campo(cols, "counts")
        if col_ind is None and col_cnt is None:
            return
        fila = db.query_one(
            "SELECT favourited, reblogged, bookmarked, COALESCE(has_poll,0), COALESCE(edited_at,''), "
            "COALESCE(in_reply_to_id,''), COALESCE(pinned,0), COALESCE(replies_count,0), "
            "COALESCE(reblogs_count,0), COALESCE(favourites_count,0) "
            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not fila:
            return
        fav, rebl, book, has_poll, edited_at, in_reply, pinned, rc, bc, fc = fila
        if idx >= self.lista_items.GetItemCount():
            return
        if col_ind is not None:
            indicadores = []
            if fav: indicadores.append(_("Favorito"))
            if rebl: indicadores.append(_("Boosteado"))
            if book: indicadores.append(_("Guardado"))
            if has_poll: indicadores.append(_("Encuesta"))
            if edited_at: indicadores.append(_("Editado"))
            if in_reply: indicadores.append(_("Respuesta"))
            if pinned: indicadores.append(_("Fijado"))
            try:
                self.lista_items.SetItem(idx, col_ind, " ".join(indicadores))
            except Exception:
                pass
        if col_cnt is not None:
            try:
                self.lista_items.SetItem(idx, col_cnt, _("{f} favoritos, {r} impulsos, {p} respuestas").format(
                    f=fc, r=bc, p=rc))
            except Exception:
                pass

    def _quitar_toot_de_lista(self, db_id):
        """Quita la fila de un toot de la lista por su db_id, sin mover el foco. Se usa cuando una
        acción hace que el toot deje de pertenecer al buffer actual (p. ej. quitar el favorito en el
        buffer Favoritos o el marcador en Marcadores)."""
        try:
            idx = self.lista_items_db_ids.index(db_id)
        except (ValueError, AttributeError):
            return
        es_notif = self._es_lista_notif_mastodon()
        try:
            self.lista_items.eliminar_fila(idx)
            del self.lista_items_db_ids[idx]
            if es_notif:
                del self._notif_db_ids[idx]  # mantener alineada la lista paralela
        except Exception:
            pass

    def _refrescar_contadores_servidor(self):
        """Pide al SERVIDOR los contadores frescos (favoritos/impulsos/respuestas) de las
        publicaciones VISIBLES en pantalla y los actualiza en su sitio, como el 'hace X minutos'.
        Así, cuando OTROS responden/favoritean/impulsan tu publicación, su contador sube solo en
        tiempo real (el streaming no actualiza el toot original). Solo mira el viewport visible
        (no todo lo cargado), así que es barato y respeta el límite de peticiones."""
        if not self.active_mastodon_account_id or getattr(self, "_modo_lista", "toots") != "toots":
            return
        try:
            col = self._col_de_campo(getattr(self, "_cols_actuales", []), "counts")
        except Exception:
            col = None
        if col is None:
            return  # la plantilla del buffer no muestra los contadores: nada que refrescar
        # Solo las filas VISIBLES (viewport): de la primera visible a una página más abajo.
        try:
            top = self.lista_items.GetTopItem()
            n = self.lista_items.GetCountPerPage() + 1
        except Exception:
            return
        ids = []
        total = len(self.lista_items_db_ids or [])
        for i in range(max(0, top), min(total, top + max(1, n))):
            d = self.lista_items_db_ids[i]
            if d:
                ids.append(d)
        if not ids:
            return

        def _cb(cambiados, err):
            if not err and cambiados:
                # Repintar EN SU SITIO desde la BD ya actualizada (no mueve el foco).
                self._refrescar_contadores_mastodon()
        mastodon_service.refrescar_contadores_async(self.active_mastodon_account_id, ids, callback=_cb)

    def _actualizar_rss_incremental(self):
        """Sincroniza los feeds de la cuenta RSS activa en segundo plano sin mover el foco."""
        if self._updating or not self.active_rss_account_id:
            return
        cuenta = self.active_rss_account_id
        self._updating = True

        def _callback(total_nuevos, error):
            self._updating = False
            if error:
                log.debug(f"Sondeo incremental de RSS falló: {error}")
                return
            # Insertar SILENCIOSAMENTE los artículos nuevos en su posición por fecha,
            # sin reconstruir la lista ni mover el foco (estilo SimpleRSS). Los
            # contadores del árbol se actualizan aparte (no tocan la lista).
            self._actualizar_contadores_rss()
            # El callback llega TARDE (la sincronización tarda): solo insertar si la lista
            # visible sigue siendo un buffer RSS. Con la bandeja activa (aunque su contexto
            # temporal fije _modo_lista="rss" para una fila), o tras cambiar de buffer, los
            # artículos ya quedaron en la base de datos y aparecerán por su propio camino;
            # insertar aquí pintaba filas RSS encima de la bandeja con sus columnas (solo
            # autor y fecha) y desalineaba la lista paralela de orígenes.
            if (getattr(self, "_bandeja_activa", False)
                    or getattr(self, "_modo_lista", "") != "rss"):
                return
            # Inserta en silencio los nuevos en su posición por fecha. (La agrupación
            # por fecha es cosmética; los insertados no llevan su etiqueta de grupo
            # hasta el próximo F5, pero entran igual sin mover el foco.)
            filas, metas_por_id = self._filas_rss_actual()
            antes = set(getattr(self, "lista_items_db_ids", ()))
            insertados = self._insertar_items_incremental(filas)
            if insertados:
                # Reconstruir el mapa de metadatos por índice tras la inserción.
                self._rss_meta = {i: metas_por_id.get(dbid, {})
                                  for i, dbid in enumerate(self.lista_items_db_ids)}
                # Sonido y aviso solo por los que se deben avisar (feeds silenciados y reglas
                # "no avisar" fuera); pulsar el aviso lleva al artículo (mf_rss_organizar.py).
                self._avisar_novedades_rss([i for i in self.lista_items_db_ids if i not in antes])

        # Sondeo AUTOMÁTICO (auto-timer estando en RSS): pocos feeds a la vez para no provocar
        # tirones en la interfaz. El refresco MANUAL (F5) usa tu concurrencia configurada.
        rss_service.actualizar_todos_async(cuenta, callback=_callback, max_par_override=3)

    def _notificar_escritorio(self, titulo, mensaje):
        """Muestra una notificación del sistema (si están habilitadas)."""
        if not config.get("rss", "notifications_enabled", True):
            return
        try:
            import wx.adv
            n = wx.adv.NotificationMessage(titulo, mensaje, parent=self)
            n.Show(timeout=8)
        except Exception as e:
            log.debug(f"No se pudo mostrar la notificación de escritorio: {e}")

    def _actualizar_telegram_incremental(self):
        """Refresca la lista de chats de Telegram en segundo plano."""
        if self._updating or not self.active_telegram_account_id:
            return
        tg_account = self.active_telegram_account_id
        self._updating = True

        def _bg(ctx):
            return telegram_service.cargar_chats(tg_account, limit=100)

        def _callback(total, error):
            self._updating = False
            if error:
                return
            # El callback llega tarde: con la BANDEJA activa no tocar la lista (sus columnas
            # son otras); los chats ya quedaron en la BD y se verán al volver a Telegram.
            if getattr(self, "_bandeja_activa", False):
                return
            # Actualiza en su sitio los chats existentes (último mensaje, no leídos) e
            # inserta los nuevos, sin mover el foco.
            self._actualizar_lista_chats(self._filas_telegram_actual())

        task_manager.start_task(_bg, callback=_callback, pass_context=True)

    def _on_cargar_mas_toots(self, event=None):
        """Descarga una página de elementos más antiguos del buffer actual (paginación hacia atrás).
        La MISMA tecla (Ctrl+Shift+M) actúa sobre la red enfocada: Mastodon, Bluesky o X."""
        # Bluesky y X tienen su propia paginación hacia atrás.
        if self.active_bluesky_account_id and getattr(self, "bluesky_ctrl", None):
            self.bluesky_ctrl.cargar_mas()
            return
        if getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None):
            self.reddit_ctrl.cargar_mas()
            return
        if getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None):
            self.peertube_ctrl.cargar_mas()
            return
        if self.active_x_account_id and getattr(self, "x_ctrl", None):
            self.x_ctrl.cargar_anteriores()
            return
        if not self.active_mastodon_account_id:
            speak(_("Esta acción solo está disponible en publicaciones de Mastodon, Bluesky o X."), interrupt=True)
            return
        # Buffers de CUENTAS (seguidores/siguiendo/bloqueados/silenciados): su propia
        # paginación por página del servidor (auditoría A15).
        if getattr(self, "_modo_lista", "") == "cuentas":
            self._cargar_mas_cuentas_mastodon()
            return
        if self._updating:
            speak(_("El sistema está ocupado actualizando. Espere un momento."), interrupt=True)
            return

        # Determinar el cursor para pedir elementos anteriores, ESPECÍFICO de cada tipo de
        # buffer (auditoría M4). OJO: la API de Mastodon pagina por ID (max_id = "id menor
        # que"), NO por fecha: los toots FEDERADOS traen el created_at original pero un id
        # local reciente, así que fecha e id no van correlacionados. Y marcadores, favoritos
        # y directos NO paginan por status_id (usan ids internos del Link header): para
        # ellos se usa el cursor real guardado por el servicio (PAG_SIGUIENTE).
        tipo = str(self.active_timeline_type)
        if tipo.startswith("thread:") or (tipo.startswith("search:")
                                          and not tipo.split(":", 1)[1].startswith("#")):
            # Un hilo se trae entero y una búsqueda de texto no pagina por max_id.
            speak(_("Este buffer no admite cargar elementos anteriores."), interrupt=True)
            return
        if tipo in ("notifications", "mentions"):
            row = db.query_one(
                "SELECT notification_id FROM mastodon_notifications WHERE account_id = ? "
                "ORDER BY CAST(notification_id AS INTEGER) ASC LIMIT 1",
                (self.active_mastodon_account_id,)
            )
            max_id = row[0] if row and row[0] else None
        elif tipo in ("bookmarks", "favourites", "direct"):
            if not mastodon_service.tiene_pag_next(self.active_mastodon_account_id, tipo):
                speak(_("No hay más elementos anteriores que cargar."), interrupt=True)
                return
            max_id = mastodon_service.PAG_SIGUIENTE
        else:
            # home, local, public, sent, cronologías user:, listas list: y búsquedas de
            # hashtag: el id mínimo DE ESTE buffer (antes, las cronologías usaban el mínimo
            # global de la cuenta y podían saltarse tramos o no avanzar).
            row = db.query_one("""
                SELECT s.status_id FROM mastodon_statuses s
                JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id
                WHERE t.account_id = ? AND t.timeline = ?
                ORDER BY CAST(s.status_id AS INTEGER) ASC LIMIT 1
            """, (self.active_mastodon_account_id, tipo))
            max_id = row[0] if row and row[0] else None

        if not max_id:
            speak(_("No hay elementos previos."), interrupt=True)
            return
        speak(_("Cargando toots más antiguos..."), interrupt=True)
        self._updating = True

        def _callback(statuses, error):
            self._updating = False
            if error:
                notificar(_("Error al cargar más toots: {0}").format(error))
                event_bus.emit("sound_trigger", "mastodon_error")
            else:
                n = len(statuses) if statuses else 0
                self._cargar_listado_toots(cache_only=True)
                notificar(_("{0} toots antiguos añadidos.").format(n))

        mastodon_service.cargar_timeline_async(
            self.active_mastodon_account_id,
            timeline_type=self.active_timeline_type,
            max_id=max_id,
            callback=_callback
        )
