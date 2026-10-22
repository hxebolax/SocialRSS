# -*- coding: utf-8 -*-
"""MainFrame (mixin): rss_handlers.
Metodos: _auto_sincronizar, _mantenimiento_datos, _on_gestionar_cuentas, _on_gestionar_buffers, _on_editor_plantillas, _reiniciar_aplicacion, _lanzar_script_desacoplado, _on_buscar_actualizaciones ...
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
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.services.telegram.telegram_service import telegram_service

from .accessible_controls import confirmar, mostrar_info

log = logging.getLogger(__name__)
from .ui_helpers import formatear_tiempo_relativo


class _Mf_rss_handlers:
    def _on_historial_lectura(self, event):
        """Abre el diálogo del historial de lectura de artículos (de la cuenta activa)."""
        from .rss_views import HistorialLecturaDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        dlg = HistorialLecturaDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_estadisticas_lectura(self, event):
        """Abre el diálogo de estadísticas de lectura (de la cuenta activa)."""
        from .rss_views import EstadisticasLecturaDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        dlg = EstadisticasLecturaDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _auto_sincronizar(self):
        """Sincronización de fondo al arrancar, ESCALONADA para no cargar los 4
        servicios y todos los feeds a la vez (lo que provocaba un tirón al inicio).

        Fase 1 (ya): Inicio de Mastodon y Bluesky (lo que sueles mirar primero).
        Fase 2 (+2,5 s): resto de timelines de Mastodon y Bluesky.
        Fase 3 (+5 s): chats de Telegram.
        Fase 4 (+8 s): RSS (lo más pesado: muchos feeds), con el paralelismo acotado
        en el arranque para suavizar el pico.
        """
        log.info("Iniciando auto-sincronización de fondo (escalonada).")
        from app.services.bluesky import bluesky_service as _bsky
        from app.core.feature_flags import servicio_habilitado

        # Un servicio DESACTIVADO no se sondea (lista vacía = no se descarga nada de él).
        def _cuentas(serv):
            if not servicio_habilitado(serv):
                return []
            return [r[0] for r in db.query("SELECT id FROM accounts WHERE service_id = ? AND active = 1", (serv,))]
        m_accounts = _cuentas("mastodon")
        b_accounts = _cuentas("bluesky")
        rss_accounts = _cuentas("rss")
        tg_accounts = _cuentas("telegram")

        # "No conservar publicaciones entre sesiones": antes de descargar nada, vaciar la caché
        # volátil de las cuentas que tengan la opción activada. EN SEGUNDO PLANO (fallo
        # reportado "Cargando buffers no responde"): estos vaciados son la única escritura
        # grande de base de datos del arranque y corrían en el HILO DE LA INTERFAZ, así que
        # con miles de filas la ventana quedaba congelada. El resto del arranque (descargas)
        # espera a que terminen, para que el vaciado no borre lo recién descargado.
        cuentas_x = _cuentas("x")

        def _bg_vaciados(ctx):
            if config.get("mastodon", "no_conservar_entre_sesiones", False):
                for m_id in m_accounts:
                    mastodon_service.vaciar_cache_sesion(m_id)
            if config.get("bluesky", "no_conservar_entre_sesiones", False):
                for b_id in b_accounts:
                    _bsky.vaciar_cache_sesion(b_id)
            # X: protegido por el kill-switch.
            try:
                from app.core import feature_flags
                if feature_flags.x_cliente_disponible() and config.get("x", "no_conservar_entre_sesiones", False):
                    from app.services.x.x_service import x_service as _xs
                    for x_id in cuentas_x:
                        _xs.vaciar_cache_sesion(x_id)
            except Exception:
                pass
            # RSS: conserva destacados, leer-más-tarde y descargados.
            if config.get("rss", "no_conservar_entre_sesiones", False):
                for r_id in rss_accounts:
                    rss_service.vaciar_articulos_volatiles(r_id)
            # PeerTube: los vídeos siempre se pueden volver a pedir a la instancia.
            try:
                from app.core import feature_flags
                if (feature_flags.servicio_habilitado("peertube")
                        and config.get("peertube", "no_conservar_entre_sesiones", False)):
                    from app.services.peertube import peertube_service
                    for p_id in _cuentas("peertube"):
                        peertube_service.vaciar_cache_sesion(p_id)
            except Exception:
                pass
            # W Social: lo volátil (Inicio, Menciones, feeds, listas y búsquedas) se puede volver
            # a pedir al servidor; los me gusta, marcadores, enviados y mensajes se conservan.
            try:
                from app.core import feature_flags
                if (feature_flags.servicio_habilitado("wsocial")
                        and config.get("wsocial", "no_conservar_entre_sesiones", False)):
                    from app.services.wsocial import wsocial_service
                    for w_id in _cuentas("wsocial"):
                        wsocial_service.vaciar_cache_sesion(w_id)
            except Exception:
                pass
            return True

        def _tras_vaciados(_r, _e):
            if self:
                self._auto_sincronizar_descargas(m_accounts, b_accounts, rss_accounts, tg_accounts)

        task_manager.start_task(_bg_vaciados, callback=_tras_vaciados, pass_context=True)

    def _auto_sincronizar_descargas(self, m_accounts, b_accounts, rss_accounts, tg_accounts):
        """Segunda mitad del arranque (tras el vaciado en fondo): lanza las descargas
        iniciales de buffers con su barra de progreso y las fases diferidas."""
        from app.services.bluesky import bluesky_service as _bsky

        # Precargar el límite real de caracteres de cada instancia (puede ser >500), para que el
        # compositor y el editor de hilos lo tengan listo desde el principio.
        for m_id in m_accounts:
            mastodon_service.actualizar_limite_caracteres_async(m_id)

        # Repinta el buffer activo de Bluesky cuando termina su carga (si es el que estás viendo).
        # Con el diálogo de carga delante NO se repinta nada (se hace una vez al cerrarlo).
        def _cb_bsky_pintar(acc):
            if getattr(self, "_cargando_buffers", False):
                return
            if self.active_bluesky_account_id == acc:
                wx.CallAfter(self.bluesky_ctrl.cargar_listado)

        # --- Construir la lista de cargas iniciales de los buffers. SOLO de las redes que descargan
        # un buffer AL ENTRAR (Mastodon, Bluesky y Telegram): son las que, si navegas rápido sin que
        # estén cargadas, disparan cargas repetidas. RSS lee de la caché (no descarga al navegar) y
        # X va bajo demanda, así que NO entran en la barra de progreso. Cada tarea es una función que
        # recibe un 'done' y lo invoca cuando su carga termina (con éxito o error). ---
        tareas = []
        for m_id in m_accounts:
            for tl in ("home", "notifications", "bookmarks", "favourites", "sent", "direct", "local", "public"):
                tareas.append(lambda done, a=m_id, t=tl:
                              mastodon_service.cargar_timeline_async(a, t, callback=lambda r, e: done()))
        for b_id in b_accounts:
            for tipo in ("home", "notifications", "mentions", "sent", "likes"):
                tareas.append(lambda done, a=b_id, tp=tipo:
                              _bsky.cargar_timeline_async(a, timeline_type=tp,
                                  callback=lambda r, e, aa=a: (_cb_bsky_pintar(aa), done())))
            tareas.append(lambda done, a=b_id:
                          _bsky.cargar_bookmarks_async(a, callback=lambda r, e, aa=a: (_cb_bsky_pintar(aa), done())))
            tareas.append(lambda done, a=b_id:
                          _bsky.cargar_convos_async(a, callback=lambda r, e, aa=a: (_cb_bsky_pintar(aa), done())))
        # Telegram entra en la barra de progreso, pero con TOPE de tiempo: conectar por MTProto
        # (handshake + sincronización) es lento, pero es espera de RED (libera el GIL), así que NO
        # añade lag al lector. Si no termina en TG_TOPE_MS, la barra deja de esperarlo y Telegram
        # sigue conectando en SEGUNDO PLANO; la lista de chats se repinta sola al llegar. Así se ve
        # su progreso sin que su lentitud atranque el arranque (antes bloqueaba el diálogo entero).
        TG_TOPE_MS = 8000
        for t_id in tg_accounts:
            def _lanzar_tg(done, a=t_id):
                hecho = {"v": False}
                def marcar():
                    if not hecho["v"]:
                        hecho["v"] = True
                        done()
                def _bg(ctx, acc=a):
                    return telegram_service.cargar_chats(acc, limit=200)
                def _finish(res, err, acc=a):
                    # Con el diálogo de carga delante no se repinta nada (reconstruir el
                    # árbol POR CADA cuenta durante la carga saturaba interfaz y lector);
                    # el cierre del diálogo hace un único repintado de árbol y buffer.
                    if self and not err and not getattr(self, "_cargando_buffers", False):
                        self._cargar_navegacion()
                        if self.active_telegram_account_id == acc:
                            self._cargar_listado_chats_telegram()
                    marcar()
                task_manager.start_task(_bg, callback=_finish, pass_context=True,
                                        unique_key=f"telegram:chats:{a}")
                # Tope: la barra no espera a Telegram más de TG_TOPE_MS (sigue en 2.º plano).
                wx.CallLater(TG_TOPE_MS, marcar)
            tareas.append(_lanzar_tg)

        # Fases DIFERIDAS, tras cargar los buffers: RSS (lo más pesado), mantenimiento y comprobación
        # de actualizaciones. X sigue cargándose bajo demanda al entrar en sus buffers.
        def _fases_diferidas():
            if not self:
                return
            # Marca de tiempo: estas fases (sobre todo el sondeo de RSS) arrancan JUSTO cuando el
            # usuario aterriza en el árbol tras cerrarse el diálogo de carga. El parseo de feeds es
            # CPU y compite por el GIL, así que si hay un tirón del lector al llegar al árbol suele
            # verse aquí. Con esta marca en el log se fecha el inicio del posible pico.
            log.info("Fases diferidas de arranque: iniciando sondeo de RSS y mantenimiento.")

            def _arrancar_rss():
                if not self:
                    return
                for r_id in rss_accounts:
                    # Sondeo de ARRANQUE: pocos feeds a la vez (CPU), para no provocar tirones en la
                    # interfaz mientras el usuario navega (p. ej. por Preferencias). El refresco MANUAL
                    # (F5) sigue usando la concurrencia que el usuario tenga configurada.
                    rss_service.actualizar_todos_async(r_id, max_par_override=3,
                                                       unique_key=f"rss:update_all:{r_id}",
                                                       solo_vencidos=True)
            # Arrancar el sondeo de RSS con un pequeño respiro (1,5 s) DESPUÉS de aterrizar en el
            # árbol: así el usuario cae en una interfaz que responde, y el pico de CPU del parseo de
            # feeds (que compite por el GIL con el lector) llega cuando ya está situado, no encima del
            # aterrizaje. El resto de fases (mantenimiento, actualizaciones) ya iban diferidas.
            wx.CallLater(1500, _arrancar_rss)

            def _arrancar_wsocial():
                # Vigilancia de novedades de W Social. Va DIFERIDA (3 s) y en hilos de fondo con
                # prioridad baja: no compite con el aterrizaje del usuario en el arbol ni con el
                # tiempo real de las demas redes, cada una con su propio temporizador.
                if self and getattr(self, "wsocial_ctrl", None) is not None:
                    try:
                        self.wsocial_ctrl.iniciar_tiempo_real()
                    except Exception as e:
                        log.debug(f"No se pudo iniciar el tiempo real de W Social: {e}")
            wx.CallLater(3000, _arrancar_wsocial)

            def _maint():
                if self:
                    self._mantenimiento_datos()
            def _upd():
                if self:
                    self._comprobar_actualizaciones_arranque()
            wx.CallLater(4000, _maint)
            wx.CallLater(7000, _upd)

            def _rss_fondo():
                # RSS en segundo plano, diferido para no competir con el arranque: la
                # sincronización periódica con el servidor (antes solo empezaba al volver a
                # guardar su configuración) y la cola de descargas de podcasts que quedó a
                # medias al cerrar.
                if not self:
                    return
                def _bg(ctx):
                    rss_service.iniciar_sync_al_arrancar()
                    rss_service.reanudar_cola_al_arrancar()
                    return True
                task_manager.start_task(_bg, pass_context=True)
            wx.CallLater(12000, _rss_fondo)

        # Si la app arranca MINIMIZADA en la bandeja (oculta), NO mostramos el diálogo de carga
        # (saltaría un modal con la ventana escondida). OJO: "iniciar en bandeja" NO minimiza,
        # directamente no muestra la ventana (Show(False)), así que hay que comprobar también
        # IsShown(): con solo IsIconized() el modal salía con la ventana oculta y la app entera
        # aparecía como "no responde" (fallo reportado).
        try:
            minimizado = bool(self.IsIconized()) or not bool(self.IsShown())
        except Exception:
            minimizado = False

        if not tareas or minimizado:
            # Nada que precargar (solo RSS/X), o arranque oculto: cargar en segundo plano sin
            # diálogo y seguir con lo diferido y las novedades de la versión si procede.
            for lanzar in tareas:
                try:
                    lanzar(lambda: None)
                except Exception:
                    pass
            _fases_diferidas()
            try:
                self._mostrar_novedades_si_procede()
            except Exception:
                pass
            return
        self._cargar_buffers_con_progreso(tareas, _fases_diferidas)

    # Máximo de cargas iniciales SIMULTÁNEAS. El lag del lector al arrancar venía de soltar todas
    # las tareas a la vez: muchos hilos parseando HTML/cargando SDK competían por el GIL y la
    # interfaz (de la que depende NVDA/JAWS) se quedaba sin turno. Lanzándolas en GRUPOS de pocas a
    # la vez, el pico de CPU baja y el lector responde. Al terminar una, se repone con la siguiente.
    _GRUPO_CARGA = 4

    def _cargar_buffers_con_progreso(self, tareas, al_terminar):
        """Muestra un diálogo modal con barra de progreso que espera a que terminen TODAS las
        cargas iniciales de buffers, y solo entonces deja al usuario en la ventana principal (con el
        buffer activo ya repintado). Las tareas se lanzan EN GRUPOS (no todas de golpe) para no
        saturar la CPU y que el lector de pantalla no se bloquee. Tope de seguridad por si algo
        se cuelga."""
        from .loading_dialog import DialogoCargandoBuffers
        total = len(tareas)
        dlg = DialogoCargandoBuffers(self, total)
        estado = {"hechas": 0, "cerrado": False, "timeout": None, "siguiente": 0, "en_vuelo": 0}
        # Mientras el diálogo está delante, los REPINTADOS intermedios (lista de chats,
        # árbol por cada cuenta, buffer de Bluesky por cada carga) no aportan nada visible
        # y generaban ráfagas de trabajo del hilo de la interfaz y de eventos para el
        # lector de pantalla: el sistema entero se notaba pastoso durante la carga (Alt+Tab
        # lento, NVDA a trompicones; fallo reportado). Se suprimen con esta bandera y se
        # repinta UNA vez al cerrar.
        self._cargando_buffers = True

        def cerrar():
            if estado["cerrado"]:
                return
            estado["cerrado"] = True
            t = estado.get("timeout")
            if t is not None:
                try:
                    t.Stop()
                except Exception:
                    pass
            dlg.cerrar()
            # El diálogo ya no es modal: el post-procesado (rehabilitar la ventana,
            # repintar una vez, fases diferidas) se encadena aquí, no tras ShowModal.
            wx.CallAfter(finalizar)

        def lanzar_siguiente():
            # Mantener como mucho _GRUPO_CARGA tareas en vuelo a la vez.
            while (estado["en_vuelo"] < self._GRUPO_CARGA
                   and estado["siguiente"] < total and not estado["cerrado"]):
                idx = estado["siguiente"]
                estado["siguiente"] += 1
                estado["en_vuelo"] += 1
                try:
                    tareas[idx](done)
                except Exception:
                    wx.CallAfter(avanzar)  # cuenta como hecha para no atascar la barra

        def avanzar():
            if estado["cerrado"]:
                return
            estado["hechas"] += 1
            estado["en_vuelo"] = max(0, estado["en_vuelo"] - 1)
            dlg.actualizar(estado["hechas"])
            if estado["hechas"] >= total:
                cerrar()
            else:
                lanzar_siguiente()  # reponer el hueco con la siguiente tarea pendiente

        def done():
            # Lo invocan las cargas desde hilos de fondo: saltamos al hilo de la interfaz.
            wx.CallAfter(avanzar)

        def finalizar():
            """Se ejecuta al cerrarse el diálogo (todas las cargas hechas o tope): rehabilita
            la ventana, repinta UNA vez y sigue con lo diferido."""
            try:
                self.Enable()
            except Exception:
                pass
            # Fin de la carga: reactivar los repintados y hacer UNO solo de todo lo diferido
            # (árbol con contadores al día + buffer activo con lo ya descargado).
            self._cargando_buffers = False
            try:
                self._cargar_navegacion()
            except Exception:
                pass
            # Repintar el buffer seleccionado con lo ya descargado y devolverle el foco al árbol.
            try:
                self._recargar_vista_activa()
            except Exception:
                pass
            try:
                self.tree_nav.SetFocus()
            except Exception:
                pass
            al_terminar()
            # Ya en la ventana principal: mostrar las novedades de la versión si procede.
            try:
                self._mostrar_novedades_si_procede()
            except Exception:
                pass

        def arrancar():
            # Tope de seguridad: si algo se cuelga, a los 60 s se cierra igualmente.
            estado["timeout"] = wx.CallLater(60000, cerrar)
            lanzar_siguiente()

        # SIN ShowModal (fallo reportado): el bucle modal de wxWidgets en Windows retiene
        # el PRIMER PLANO e impedía cambiar a otra aplicación con Alt+Tab durante toda la
        # carga (limitación conocida de wx en MSW). El mismo efecto —que no se pueda
        # navegar antes de tiempo— se consigue DESHABILITANDO la ventana principal
        # mientras el diálogo (no modal) está delante; Alt+Tab queda libre.
        self.Disable()
        dlg.Show()
        dlg.Raise()  # solo una vez, al abrir: que el foco arranque en el diálogo
        wx.CallAfter(arrancar)

    def _mantenimiento_datos(self):
        """Poda del histórico para que las tablas no crezcan sin fin y las consultas se mantengan
        rápidas con el tiempo. Recorta los buffers de alto volumen y las notificaciones a un máximo,
        borra contenido huérfano no guardado y recorta la caché de Telegram. Conserva lo guardado
        (favoritos, marcadores) y los buffers curados. Se ejecuta al arrancar y, A PARTIR DE AHORA,
        cada 20 minutos, para que la app siga ligera por mucho tiempo que esté abierta. Va en
        segundo plano (no bloquea la interfaz)."""
        # Reprogramar la próxima poda (en el hilo de UI). Así la BD se mantiene acotada de forma
        # continua, no solo al arrancar (antes, una sesión larga la dejaba enorme y lenta).
        try:
            self._maint_timer = wx.CallLater(20 * 60 * 1000, self._mantenimiento_datos)
        except Exception:
            pass

        def _int_cfg(seccion, clave):
            try:
                return int(config.get(seccion, clave, 0))
            except Exception:
                return 0
        # Límites por servicio (cada red tiene su propia opción en Preferencias). 0 = SIN LÍMITE:
        # por petición del usuario, NINGÚN buffer se recorta a menos que él ponga un máximo. Sin
        # límite, los buffers conservan todo lo cargado (ojo: la base puede crecer en sesiones muy
        # largas; el usuario puede fijar un máximo por red en Preferencias si lo desea).
        mst_buf = _int_cfg("mastodon", "max_items_buffer")       # 0 = sin límite
        mst_notif = _int_cfg("mastodon", "max_notifications")    # 0 = sin límite
        bsky_buf = _int_cfg("bluesky", "max_items_buffer")       # 0 = sin límite
        bsky_notif = _int_cfg("bluesky", "max_notifications")    # 0 = sin límite
        max_tg = _int_cfg("telegram", "max_messages_per_chat")   # 0 = sin límite
        leer_prefs_srv = bool(config.get("mastodon", "read_preferences_from_instance", False))
        def _bg(ctx):
            # Mastodon solo se poda si el usuario fijó un máximo (>0); si no, se conserva todo.
            if mst_buf > 0 or mst_notif > 0:
                for (mid,) in db.query("SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1"):
                    mastodon_service.podar_antiguos(mid, max_buffer=mst_buf, max_notif=mst_notif)
            # Precargar en segundo plano las preferencias del servidor (visibilidad por
            # defecto) para que el compositor las tenga listas sin bloquear la interfaz.
            if leer_prefs_srv:
                for (mid,) in db.query("SELECT id FROM accounts WHERE service_id = 'mastodon' AND active = 1"):
                    try:
                        mastodon_service.obtener_preferencias_servidor(mid)
                    except Exception:
                        pass
            if bsky_buf > 0 or bsky_notif > 0:
                from app.services.bluesky import bluesky_service as _bsky
                for (bid,) in db.query("SELECT id FROM accounts WHERE service_id = 'bluesky' AND active = 1"):
                    _bsky.podar_antiguos(bid, max_buffer=bsky_buf, max_notif=bsky_notif)
            if max_tg > 0:
                for (tid,) in db.query("SELECT id FROM accounts WHERE service_id = 'telegram' AND active = 1"):
                    telegram_service.podar_mensajes_antiguos(tid, max_por_chat=max_tg)
            for (rid,) in db.query("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1"):
                try:
                    rss_service.limpieza_mantenimiento(rid)
                except Exception:
                    pass
            # Limpiar filas huérfanas de las tablas FTS (índices de búsqueda sin su registro).
            try:
                from app.core.data_cleanup import limpiar_huerfanos_fts
                limpiar_huerfanos_fts()
            except Exception as e:
                log.debug(f"No se pudieron limpiar huérfanos FTS en mantenimiento: {e}")
            return True
        task_manager.start_task(_bg, pass_context=True)

    # --- DIÁLOGOS DE ENLACE ---
    def _on_gestionar_cuentas(self, event):
        from .mastodon_views import CuentasDialog
        dlg = CuentasDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _on_gestionar_buffers(self, event):
        """Mostrar u ocultar buffers de cada red; reconstruye el árbol si cambia algo."""
        from .buffers_dialog import GestionBuffersDialog
        dlg = GestionBuffersDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            if dlg.aplicar():
                self._cargar_navegacion()
                # Cambió qué buffers están visibles: reevaluar el streaming (qué timelines se
                # streamean) y el temporizador.
                self._aplicar_servicio_activo()
                notificar(_("Buffers actualizados."))
        dlg.Destroy()

    def _on_carpetas_inteligentes(self, event):
        """Gestor de carpetas inteligentes (consultas guardadas como buffers del árbol)."""
        from .rss_views import CarpetasInteligentesDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        dlg = CarpetasInteligentesDialog(self, acc)
        dlg.ShowModal()
        cambios = dlg.hubo_cambios
        dlg.Destroy()
        if cambios:
            self._cargar_navegacion()

    def _on_resumen_no_leidos(self, event):
        """Genera un resumen (digest) de los artículos no leídos, con IA si hay proveedor."""
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        speak(_("Preparando el resumen de artículos sin leer..."), interrupt=True)

        def _bg(ctx):
            return rss_service.generar_digest(acc)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo generar el resumen: {0}").format(err or ""))
                return
            mostrar_info(self, res["texto"], _("Resumen de artículos sin leer"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_sincronizacion(self, event):
        """Configura la sincronización de RSS con un servidor externo (Google Reader / Miniflux)."""
        from .rss_views import SincronizacionDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        dlg = SincronizacionDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()
        self._cargar_navegacion()

    def _on_gestionar_etiquetas(self, event):
        """Gestor de etiquetas de RSS (crear/ver/borrar)."""
        from .rss_views import EtiquetasDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        dlg = EtiquetasDialog(self, acc)
        dlg.ShowModal()
        cambios = dlg.hubo_cambios
        dlg.Destroy()
        if cambios:
            self._cargar_navegacion()

    def _on_etiquetas_feed(self, feed_id):
        """Asigna etiquetas a un FEED (desde su menú contextual)."""
        from .rss_views import AsignarEtiquetasDialog
        fr = db.query_one("SELECT account_id FROM rss_feeds WHERE id = ?", (feed_id,))
        acc = fr[0] if fr else (self.active_rss_account_id or self._primera_cuenta_rss())
        if not acc:
            return
        dlg = AsignarEtiquetasDialog(self, acc, feed_id=feed_id)
        cambios = dlg.ShowModal() == wx.ID_OK and dlg.hubo_cambios
        dlg.Destroy()
        if cambios:
            self._cargar_navegacion()

    def _on_etiquetas_articulo(self, db_id=None):
        """Asigna etiquetas al ARTÍCULO enfocado (desde su menú contextual)."""
        from .rss_views import AsignarEtiquetasDialog
        db_id = db_id or (self._articulo_actual_rss_db_id()
                          if hasattr(self, "_articulo_actual_rss_db_id") else None)
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not db_id or not acc:
            return
        dlg = AsignarEtiquetasDialog(self, acc, article_id=db_id)
        cambios = dlg.ShowModal() == wx.ID_OK and dlg.hubo_cambios
        dlg.Destroy()
        if cambios:
            self._cargar_navegacion()

    def _on_galeria_imagenes(self, db_id=None):
        """Abre la galería accesible de imágenes del artículo enfocado."""
        from .rss_views import GaleriaImagenesDialog
        db_id = db_id or (self._articulo_actual_rss_db_id()
                          if hasattr(self, "_articulo_actual_rss_db_id") else None)
        if not db_id:
            return
        dlg = GaleriaImagenesDialog(self, db_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_salud_feeds(self, event):
        """Abre el centro de salud de los feeds RSS (estado, errores y acciones)."""
        from .rss_views import SaludFeedsDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        dlg = SaludFeedsDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()
        self._actualizar_contadores_rss()

    def _on_exportar_articulos_lote(self, event):
        """Exporta VARIOS artículos de una vez (vista actual, destacados o leer más tarde)
        a una carpeta, un archivo por artículo, en TXT/HTML/Markdown saneado."""
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            notificar(_("Active primero una cuenta RSS."))
            return
        # 1) Qué exportar.
        ambitos = [_("La vista actual (lo que hay en la lista)"),
                   _("Todos los destacados"), _("Todo lo marcado para leer más tarde")]
        d1 = wx.SingleChoiceDialog(self, _("¿Qué artículos quieres exportar?"),
                                   _("Exportar artículos"), ambitos)
        if d1.ShowModal() != wx.ID_OK:
            d1.Destroy()
            return
        ambito = d1.GetSelection()
        d1.Destroy()
        if ambito == 0:
            # La "vista actual" solo vale si el buffer activo ES de RSS (si abriste el
            # menú desde otra red, la lista mostraría publicaciones, no artículos).
            ids = list(getattr(self, "lista_items_db_ids", []) or [])
            if not ids or not self.active_rss_account_id:
                notificar(_("La lista actual no muestra artículos RSS."))
                return
        else:
            campo = "favorite" if ambito == 1 else "read_later"
            ids = [r[0] for r in db.query(
                f"SELECT id FROM rss_articles WHERE account_id = ? AND COALESCE({campo},0) = 1 "
                "AND COALESCE(deleted,0) = 0 ORDER BY published_at DESC", (acc,))]
        if not ids:
            notificar(_("No hay artículos que exportar."))
            return
        # 1-bis) Elegir con CASILLAS accesibles qué artículos exportar (todos marcados por
        # defecto), igual que la vista previa de OPML o el listado de Sonidos.
        ids = self._elegir_articulos_con_casillas(ids)
        if not ids:
            return
        # 2) Formato.
        formatos = [_("Texto (.txt)"), _("HTML (.html)"), _("Markdown (.md)")]
        d2 = wx.SingleChoiceDialog(self, _("Formato de los archivos:"),
                                   _("Exportar artículos"), formatos)
        if d2.ShowModal() != wx.ID_OK:
            d2.Destroy()
            return
        formato = ("txt", "html", "md")[d2.GetSelection()]
        d2.Destroy()
        # 3) Carpeta destino.
        d3 = wx.DirDialog(self, _("Carpeta donde guardar los artículos"))
        if d3.ShowModal() != wx.ID_OK:
            d3.Destroy()
            return
        carpeta = d3.GetPath()
        d3.Destroy()
        speak(_("Exportando {0} artículos...").format(len(ids)), interrupt=True)

        def _fin(n, err):
            if err:
                notificar(_("Error al exportar: {0}").format(err))
            else:
                notificar(_("{0} artículos exportados en {1}.").format(n or 0, carpeta))
        rss_service.exportar_articulos_lote_async(ids, carpeta, formato=formato, callback=_fin)

    def _elegir_articulos_con_casillas(self, ids):
        """Muestra los artículos candidatos con CASILLAS accesibles (todos marcados por defecto)
        para que el usuario desmarque los que no quiera exportar. Devuelve la lista de ids
        elegidos (conservando el orden), o None si se cancela o no se marca ninguno. Mismo patrón
        que la vista previa de OPML y el listado de Sonidos (AccessibleCheckListBox)."""
        from .accessible_controls import AccessibleCheckListBox, habilitar_escape_cierre
        # Títulos (y fecha) de los candidatos, conservando el orden de `ids`. Se trocea la
        # consulta IN por si hay muchos (límite de variables de SQLite).
        info = {}
        for j in range(0, len(ids), 900):
            trozo = ids[j:j + 900]
            for r in db.query(
                    "SELECT id, COALESCE(NULLIF(title,''), '(sin título)'), COALESCE(published_at,'') "
                    "FROM rss_articles WHERE id IN ({0})".format(",".join("?" * len(trozo))),
                    tuple(trozo)):
                info[r[0]] = (r[1], r[2])
        etiquetas, orden_ids = [], []
        for i in ids:
            if i not in info:
                continue
            titulo, fecha = info[i]
            fecha_corta = (fecha or "")[:16].replace("T", " ")
            etiquetas.append(_("{0} — {1}").format(titulo, fecha_corta) if fecha_corta else titulo)
            orden_ids.append(i)
        if not etiquetas:
            notificar(_("No hay artículos que exportar."))
            return None
        dlg = wx.Dialog(self, title=_("Exportar artículos: elige cuáles"), size=(640, 500),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=_(
            "{0} artículos. Marca los que quieres exportar (todos vienen marcados):").format(
                len(etiquetas))), 0, wx.ALL, 8)
        clb = AccessibleCheckListBox(dlg, choices=etiquetas, name=_("Artículos a exportar"))
        for i in range(len(etiquetas)):
            clb.Check(i, True)
        s.Add(clb, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        try:
            habilitar_escape_cierre(dlg)
        except Exception:
            pass
        clb.SetSelection(0)
        clb.SetFocus()
        elegidos = None
        if dlg.ShowModal() == wx.ID_OK:
            elegidos = [orden_ids[i] for i in clb.GetCheckedItems()]
        dlg.Destroy()
        if not elegidos:
            if elegidos is not None:
                notificar(_("No se marcó ningún artículo."))
            return None
        return elegidos

    def _on_descargas_podcast(self, event):
        """Abre el diálogo de descargas de podcast (episodios offline, de la cuenta activa)."""
        from .rss_views import DescargasPodcastDialog
        acc = self.active_rss_account_id or self._primera_cuenta_rss()
        if not acc:
            return
        dlg = DescargasPodcastDialog(self, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def _poblar_submenu_complementos(self):
        """Rellena el submenú RSS -> Complementos con 'Gestionar...' y las entradas que aporten los
        complementos ACTIVOS en este momento."""
        import wx
        sm = getattr(self, "_submenu_complementos", None)
        if sm is None:
            return
        it = sm.Append(wx.ID_ANY, _("Gestionar complementos..."), _("Activar, desactivar y configurar complementos"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_complementos, it)
        try:
            from app.core.plugins import plugin_manager
            items = plugin_manager.menu_items()
            if items:
                sm.AppendSeparator()
                for _pid, etiqueta, callback in items:
                    it_c = sm.Append(wx.ID_ANY, etiqueta)
                    self.Bind(wx.EVT_MENU, lambda e, cb=callback: cb(), it_c)
        except Exception:
            pass

    def _reconstruir_menu_complementos(self):
        """Reconstruye EN CALIENTE el submenú de complementos (al activar/desactivar uno), para que
        sus entradas de menú aparezcan o desaparezcan sin reiniciar la app."""
        sm = getattr(self, "_submenu_complementos", None)
        if sm is None:
            return
        try:
            for it in list(sm.GetMenuItems()):
                sm.Delete(it)
            self._poblar_submenu_complementos()
        except Exception as e:
            log.debug(f"No se pudo reconstruir el menú de complementos: {e}")

    def _on_gestionar_complementos(self, event):
        """Abre el diálogo de gestión de complementos (plugins)."""
        from .plugins_dialog import PluginsDialog
        dlg = PluginsDialog(self)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_editor_plantillas(self, event):
        """Abre el editor de plantillas. La plantilla define las COLUMNAS de cada buffer, y eso
        se aplica al ARRANCAR la app (afecta a la vista para quien ve y a la lectura por columnas
        del lector). Por eso, si hubo cambios, se ofrece reiniciar para aplicarlos."""
        from .plantillas_dialog import EditorPlantillasDialog
        dlg = EditorPlantillasDialog(self)
        guardado = dlg.ShowModal() == wx.ID_OK
        hubo_cambios = getattr(dlg, "hubo_cambios", False)
        finales = getattr(dlg, "_finales", None)
        dlg.Destroy()
        # Solo se avisa si el usuario guardó Y realmente cambió algo. Si solo miró y guardó sin
        # tocar nada, no sale ningún diálogo (no hizo nada).
        if not (guardado and hubo_cambios and finales):
            return
        from .accessible_controls import confirmar
        resp = confirmar(
            self,
            _("Se han cambiado las plantillas. Es NECESARIO reiniciar la aplicación para que los "
              "cambios surtan efecto (las plantillas definen las columnas de cada buffer).\n\n"
              "¿Quieres reiniciar ahora para aplicar los cambios?\n\n"
              "Sí: se aplican los cambios cuando se reinicie la aplicación.\n"
              "No: se dejan las plantillas como estaban (se descartan los cambios)."),
            _("Reiniciar para aplicar las plantillas"))
        if resp == wx.ID_YES:
            with config.batch():
                for clave, texto in finales.items():
                    config.set("accessibility", clave, texto)
            self._reiniciar_aplicacion()
        else:
            speak(_("Cambios descartados."), interrupt=True)

    def _reiniciar_aplicacion(self):
        """Reinicia la app de forma FIABLE, sirviendo tanto para código fuente (lanzada con un
        .bat / python main.py) como para la versión compilada (.exe).

        Problemas que resuelve:
        - A veces el cierre normal se colgaba (sondeos de buffers en curso) y el proceso quedaba
          vivo sin liberar el cerrojo de instancia única, y al reabrir salía "ya está en ejecución".
        - Por eso aquí: guardamos lo esencial, lanzamos un AYUDANTE desacoplado que espera a que
          este proceso muera (y lo FUERZA si tarda) y entonces arranca la instancia nueva, y entonces
          salimos de inmediato con os._exit (sin colgarnos en limpiezas; el SO libera el cerrojo)."""
        import sys, os
        self._reiniciando = True
        # 1. Guardar lo importante AHORA (después salimos de forma abrupta).
        try:
            self._persistir_posiciones_buffer()
        except Exception:
            pass
        try:
            config.save()
        except Exception:
            pass
        # 2. Programar el relanzamiento con un .bat (cmd), igual que la actualización: es lo único
        #    que funciona también en la versión COMPILADA sin consola (PowerShell desacoplado no
        #    arrancaba y la app nunca se reiniciaba). El .bat espera a que muera este PID (forzando
        #    si tarda), relanza la app y se autoelimina.
        try:
            from app.core.paths import ROOT_DIR
            from app.services.updater.update_service import comando_relanzar
            pid = os.getpid()
            bat = os.path.join(str(ROOT_DIR), "_reiniciar_socialrss.bat")
            contenido = (
                "@echo off\r\n"
                "chcp 65001 >nul 2>&1\r\n"
                "set /a intento=0\r\n"
                ":esperar\r\n"
                f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL\r\n'
                "if not errorlevel 1 (\r\n"
                '    set /a "intento+=1"\r\n'
                "    if %intento% gtr 15 (\r\n"
                f"        taskkill /F /PID {pid} >nul 2>&1\r\n"
                "    ) else (\r\n"
                "        timeout /t 1 /nobreak >nul\r\n"
                "        goto esperar\r\n"
                "    )\r\n"
                ")\r\n"
                "timeout /t 1 /nobreak >nul\r\n"
                f"{comando_relanzar()}\r\n"
                'del "%~f0" >nul 2>&1\r\n'
            )
            with open(bat, "w", encoding="utf-8") as f:
                f.write(contenido)
            self._lanzar_script_desacoplado(bat)
        except Exception as e:
            log.error(f"No se pudo programar el reinicio: {e}")
            self._reiniciando = False
            notificar(_("No se pudo reiniciar automáticamente. Cierra y vuelve a abrir la aplicación."))
            return
        # 3. Salir YA, sin colgarnos en limpiezas ni en sondeos en curso. os._exit termina el
        #    proceso de inmediato; el SO cierra los manejadores y libera el cerrojo de instancia
        #    única, de modo que el ayudante puede arrancar la nueva sin el aviso de "ya en ejecución".
        log.info("Reiniciando la aplicación para aplicar las plantillas.")
        try:
            sys.stdout.flush(); sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    # --- ACTUALIZADOR (desde el servidor propio; preparado para GitHub en el futuro) ---
    def _lanzar_script_desacoplado(self, script_path):
        """Lanza un .bat auxiliar (reinicio o actualización) de forma que SOBREVIVA a que esta app
        muera y FUNCIONE TAMBIÉN en la versión compilada (sin consola).

        Clave: se usa `cmd /c` con CREATE_NO_WINDOW (consola oculta → el .bat tiene descriptores
        estándar VÁLIDOS) y se redirige la E/S a DEVNULL. Antes se lanzaba PowerShell con
        DETACHED_PROCESS y sin redirigir la E/S: en la versión compilada (GUI sin consola) los
        handles estándar son inválidos y el proceso auxiliar fallaba al arrancar, así que el
        reinicio y la actualización no llegaban a ejecutarse."""
        import subprocess
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            ["cmd", "/c", script_path],
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    def _on_buscar_actualizaciones(self, event=None):
        """Comprueba en el servidor si hay una versión nueva y, si la hay, ofrece instalarla."""
        from app.services.updater import update_service as upd
        speak(_("Comprobando actualizaciones..."), interrupt=True)

        def _bg(ctx):
            remota = upd.obtener_version_remota()
            return remota

        def _fin(remota, err):
            if err:
                notificar(_("No se pudo comprobar si hay actualizaciones: {0}").format(err))
                return
            if not remota:
                notificar(_("No se pudo comprobar si hay actualizaciones. Revisa tu conexión."))
                return
            if upd.hay_actualizacion(remota):
                self._ofrecer_actualizacion(remota)
            else:
                notificar(_("Ya tienes la última versión ({0}).").format(upd.version_local()))

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _comprobar_actualizaciones_arranque(self):
        """Comprobación silenciosa al arrancar: si hay versión nueva, lo ofrece (sin molestar si
        no la hay ni si falla la conexión). Opcional: solo si está activado en Preferencias."""
        if not config.get("general", "check_updates_on_start", True):
            return
        from app.services.updater import update_service as upd

        def _bg(ctx):
            return upd.obtener_version_remota()

        def _fin(remota, err):
            if not err and remota and upd.hay_actualizacion(remota):
                self._ofrecer_actualizacion(remota)

        try:
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        except Exception:
            pass

        # Comprobación periódica de reglas BPC al iniciar si está activada
        if config.get("rss", "bpc_check_updates_auto", False):
            def _bg_bpc(ctx):
                from app.services.rss.bpc_updater import BPCUpdater
                info = BPCUpdater.comprobar_actualizaciones()
                if info.get("hay_actualizacion") and info.get("datos_sitios"):
                    BPCUpdater.aplicar_actualizacion(info["datos_sitios"], info.get("version_remota", ""))
                return info

            def _fin_bpc(info, err):
                if not err and info and info.get("hay_actualizacion"):
                    log.info(f"Reglas BPC actualizadas automáticamente al iniciar: {info.get('version_remota')}")

            try:
                task_manager.start_task(_bg_bpc, callback=_fin_bpc, pass_context=True)
            except Exception:
                pass

    def _ofrecer_actualizacion(self, version_remota):
        """Abre el diálogo de actualización para esa versión."""
        try:
            from .update_dialog import DialogoActualizacion
            dlg = DialogoActualizacion(self, version_remota)
            dlg.ShowModal()
            dlg.Destroy()
        except Exception as e:
            log.error(f"No se pudo abrir el diálogo de actualización: {e}")
            notificar(_("No se pudo abrir el actualizador: {0}").format(e))

    def _lanzar_y_salir_actualizacion(self, script_path):
        """Guarda lo esencial, lanza el script de actualización (que espera a que cerremos, copia
        los archivos nuevos y relanza la app) y sale de inmediato para liberar la instalación."""
        import sys as _sys, os as _os
        self._reiniciando = True  # suprime la confirmación de salida
        try:
            self._persistir_posiciones_buffer()
        except Exception:
            pass
        try:
            config.save()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        try:
            self._lanzar_script_desacoplado(script_path)
        except Exception as e:
            log.error(f"No se pudo lanzar el script de actualización: {e}")
            self._reiniciando = False
            notificar(_("No se pudo iniciar la actualización. Inténtalo de nuevo."))
            return
        log.info("Aplicando la actualización y reiniciando.")
        try:
            _sys.stdout.flush(); _sys.stderr.flush()
        except Exception:
            pass
        _os._exit(0)

    # --- DESPACHADORES DE ATAJOS UNIFICADOS (según el servicio activo) ---
    # Las mismas teclas (Ctrl+N crear, Ctrl+R responder, Ctrl+Shift+U citar,
    # Ctrl+B me gusta, Ctrl+Shift+N hilo) actúan sobre la red enfocada. Las
    # acciones de publicación solo existen en Mastodon y Bluesky; en Telegram y
    # RSS se avisa de que no aplican.
    def _bluesky_db_id_enfocado(self):
        idx = self.lista_items.GetFocusedItem()
        if 0 <= idx < len(self.lista_items_db_ids):
            return self.lista_items_db_ids[idx]
        speak(_("Enfoca primero una publicación."), interrupt=True)
        return None

    def _disp_nuevo(self, event):
        if self.active_mastodon_account_id:
            self._on_nuevo_toot(event)
        elif self.active_bluesky_account_id:
            self.bluesky_ctrl.nuevo_post()
        elif self.active_x_account_id and self.x_ctrl is not None:
            self.x_ctrl.nuevo_post()
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            self.reddit_ctrl.nuevo_post()
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            self.wsocial_ctrl.nuevo_post()
        else:
            speak(_("Crear una publicación solo está disponible en Mastodon, Bluesky, X, Reddit y W Social."), interrupt=True)

    def _disp_nuevo_hilo(self, event):
        if self.active_mastodon_account_id:
            self._on_nuevo_hilo(event)
        elif self.active_bluesky_account_id:
            self.bluesky_ctrl.nuevo_hilo()
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            self.wsocial_ctrl.nuevo_hilo()
        else:
            speak(_("Crear un hilo solo está disponible en Mastodon, Bluesky y W Social."), interrupt=True)

    def _disp_ver_publicacion(self, event=None):
        """Ctrl+D: despacha según la red de la publicación enfocada. En Mastodon abre el diálogo
        'Ver publicación'; en RSS destaca el artículo (uso histórico de Ctrl+D en RSS). Vale también
        dentro de la bandeja de entrada (con el contexto de la fila ya fijado)."""
        idx = self.lista_items.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.lista_items_db_ids):
            return
        db_id = self.lista_items_db_ids[idx]
        if self.active_mastodon_account_id:
            self._on_ver_publicacion(db_id, idx)
        elif self.active_rss_account_id:
            self._atajo_rss_articulo("destacar")

    def _disp_responder(self, event):
        if self.active_mastodon_account_id:
            self._on_responder_toot(event)
        elif self.active_bluesky_account_id:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.bluesky_ctrl.responder(db_id)
        elif self.active_x_account_id and self.x_ctrl is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.x_ctrl.responder(db_id)
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.reddit_ctrl.responder(self.reddit_ctrl._fullname_de(db_id))
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.wsocial_ctrl.responder(db_id)
        else:
            speak(_("Responder solo está disponible en Mastodon, Bluesky, X, Reddit y W Social."), interrupt=True)

    def _disp_ver_conversacion(self, event):
        """Ctrl+Shift+V: abre la conversación/hilo de la publicación enfocada en la red activa."""
        db_id = self._bluesky_db_id_enfocado()
        if not db_id:
            return
        if self.active_mastodon_account_id:
            self._on_ver_detalles_toot(db_id)
        elif self.active_bluesky_account_id:
            self.bluesky_ctrl.ver_hilo(db_id)
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            self.wsocial_ctrl.ver_hilo(db_id)
        elif self.active_x_account_id and self.x_ctrl is not None:
            tid = self.x_ctrl._tweet_id(db_id)
            if tid:
                self.x_ctrl.ver_hilo(tid)
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            idx = self.lista_items.GetFocusedItem()
            if idx is not None and idx >= 0:
                self.reddit_ctrl.on_item_activated(idx)
        else:
            speak(_("Ver la conversación solo está disponible en Mastodon, Bluesky, X y Reddit."), interrupt=True)

    def _disp_ver_texto_plano(self, event):
        """Ctrl+Shift+T: muestra el texto completo de la publicación o artículo enfocado en un
        cuadro de solo lectura (útil para leerlo con calma o copiarlo)."""
        db_id = self._bluesky_db_id_enfocado()
        if not db_id:
            return
        texto = None
        if self.active_mastodon_account_id:
            r = db.query_one("SELECT author_name, author_username, COALESCE(spoiler_text,''), COALESCE(content,'') FROM mastodon_statuses WHERE id = ?", (db_id,))
            if r:
                partes = [f"{r[0]} ({r[1]})"]
                if r[2]:
                    partes.append(_("Advertencia de contenido: {0}").format(r[2]))
                partes.append(r[3])
                texto = "\n\n".join(p for p in partes if p)
        elif self.active_bluesky_account_id:
            r = db.query_one("SELECT author_name, author_handle, COALESCE(content,'') FROM bluesky_posts WHERE id = ?", (db_id,))
            if r:
                texto = f"{r[0]} (@{r[1]})\n\n{r[2]}"
        elif self.active_x_account_id and self.x_ctrl is not None:
            r = db.query_one("SELECT author_name, author_screen_name, COALESCE(text,'') FROM x_tweets WHERE id = ?", (db_id,))
            if r:
                texto = f"{r[0]} (@{r[1]})\n\n{r[2]}"
        elif self.active_rss_account_id:
            r = db.query_one("SELECT title, COALESCE(content,'') FROM rss_articles WHERE id = ?", (db_id,))
            if r:
                texto = f"{r[0]}\n\n{r[1]}"
        elif getattr(self, "active_reddit_account_id", None):
            if self._modo_lista == "reddit_comments":
                r = db.query_one("SELECT author, COALESCE(body,'') FROM reddit_comments WHERE id = ?", (db_id,))
                if r:
                    texto = f"u/{r[0]}\n\n{r[1]}"
            else:
                r = db.query_one("SELECT COALESCE(title,''), author, COALESCE(selftext,''), COALESCE(url,''), is_self FROM reddit_posts WHERE id = ?", (db_id,))
                if r:
                    cuerpo = r[2] if r[4] else _("Enlace: {0}").format(r[3])
                    texto = f"{r[0]}\nu/{r[1]}\n\n{cuerpo}"
        if not texto or not texto.strip():
            speak(_("No hay texto que mostrar para este elemento."), interrupt=True)
            return
        self._mostrar_texto_largo(_("Texto de la publicación"), texto)

    def _on_abrir_timeline_usuario(self, event):
        """Abre las publicaciones de un usuario. Si tienes una publicación enfocada, lo primero que
        se ofrece son SUS usuarios (el autor y los que aparecen mencionados en ella): así puedes
        leer a alguien a quien solo conoces porque lo han mencionado, sin saberte su cuenta. Y
        siempre queda la opción de escribir otra cuenta a mano."""
        if not (self.active_mastodon_account_id or self.active_bluesky_account_id
                or (self.active_x_account_id and self.x_ctrl is not None)):
            speak(_("Abrir la cronología de un usuario solo está disponible en Mastodon, Bluesky y X."),
                  interrupt=True)
            return
        candidatos = self._usuarios_de_publicacion_enfocada()
        escribir = _("Escribir otra cuenta...")
        acct = ""
        if candidatos:
            opciones = ["@" + c for c in candidatos] + [escribir]
            sel = wx.GetSingleChoice(_("¿De qué usuario quieres ver las publicaciones?"),
                                     _("Abrir cronología de un usuario"), opciones, self)
            if not sel:
                return
            if sel != escribir:
                acct = sel.lstrip("@")
        if not acct:
            from .accessible_controls import CustomTextEntryDialog
            if self.active_bluesky_account_id:
                etiqueta = _("Cuenta (handle, por ejemplo nombre.bsky.social):")
            elif self.active_mastodon_account_id:
                etiqueta = _("Usuario (acct, por ejemplo nombre@instancia):")
            else:
                etiqueta = _("Usuario de X (con o sin @):")
            dlg = CustomTextEntryDialog(self, etiqueta, _("Abrir cronología de un usuario"))
            acct = dlg.GetValue().strip().lstrip("@") if dlg.ShowModal() == wx.ID_OK else ""
            dlg.Destroy()
        if not acct:
            return
        if self.active_mastodon_account_id:
            self._abrir_timeline_acct(acct)
        elif self.active_bluesky_account_id:
            self.bluesky_ctrl.crear_buffer_usuario_por_handle(acct)
        else:
            self.x_ctrl.crear_cronologia_por_nombre(acct)

    def _disp_citar(self, event):
        if self.active_mastodon_account_id:
            self._on_citar_toot(event)
        elif self.active_bluesky_account_id:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.bluesky_ctrl.citar(db_id)
        elif self.active_x_account_id and self.x_ctrl is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.x_ctrl.citar(db_id)
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            # En Reddit el equivalente a "citar" es el crosspost.
            db_id = self._bluesky_db_id_enfocado()
            if db_id and self._modo_lista == "reddit_posts":
                self.reddit_ctrl.crosspost(db_id)
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.wsocial_ctrl.citar(db_id)
        else:
            speak(_("Citar solo está disponible en Mastodon, Bluesky, X y W Social."), interrupt=True)

    def _disp_boost_like(self, event):
        if self.active_mastodon_account_id:
            self._on_boost_fav_toot(event)
        elif self.active_bluesky_account_id:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                row = db.query_one("SELECT liked_uri FROM bluesky_posts WHERE id = ?", (db_id,))
                ya = bool(row and row[0])
                self.bluesky_ctrl.accion(db_id, "unlike" if ya else "like")
        elif self.active_x_account_id and self.x_ctrl is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                row = db.query_one("SELECT tweet_id, liked FROM x_tweets WHERE id = ?", (db_id,))
                if row:
                    self.x_ctrl.accion(row[0], "unlike" if row[1] else "like")
        elif getattr(self, "active_reddit_account_id", None) and getattr(self, "reddit_ctrl", None) is not None:
            # En Reddit el "me gusta" es el voto positivo: alterna entre votar arriba y quitar el voto.
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                tabla = "reddit_comments" if self._modo_lista == "reddit_comments" else "reddit_posts"
                row = db.query_one(f"SELECT likes FROM {tabla} WHERE id = ?", (db_id,))
                ya = bool(row and row[0] == 1)
                self.reddit_ctrl.votar(self.reddit_ctrl._fullname_de(db_id), "clear" if ya else "up")
        elif getattr(self, "active_wsocial_account_id", None) and getattr(self, "wsocial_ctrl", None) is not None:
            db_id = self._bluesky_db_id_enfocado()
            if db_id:
                self.wsocial_ctrl.alternar_me_gusta(db_id)
        elif getattr(self, "active_peertube_account_id", None) and getattr(self, "peertube_ctrl", None) is not None:
            # En PeerTube el "me gusta" valora el vídeo enfocado.
            db_id = self._bluesky_db_id_enfocado()
            d = self.peertube_ctrl._datos_video(db_id) if db_id else None
            if d:
                self.peertube_ctrl.votar(db_id, d, "like")
        else:
            speak(_("Esta acción solo está disponible en Mastodon, Bluesky, X, Reddit y PeerTube."), interrupt=True)

    def _on_x_mi_perfil(self):
        if self.x_ctrl is None:
            return
        acc = self.x_ctrl._cuenta()
        if not acc:
            return
        from app.services.x import x_service
        screen = self.x_ctrl._cuenta() and x_service._yo(acc).get("screen_name")
        if screen:
            self.x_ctrl.ver_perfil(screen)

    def _on_x_aviso(self):
        from .x_views import AVISO_RIESGOS
        mostrar_info(self, AVISO_RIESGOS, _("Cliente de X (Twitter)"))

    def _on_nuevo_toot(self, event):
        if not self.active_mastodon_account_id:
            speak(_("No hay ninguna cuenta activa de Mastodon seleccionada."), interrupt=True)
            return
        from .mastodon_views import ComposeTootDialog
        dlg = ComposeTootDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_responder_toot(self, event=None, db_id=None):
        # Si no se pasa un db_id explícito (p. ej. desde el diálogo de publicaciones del
        # autor), se usa el toot enfocado en la lista principal.
        if db_id is None:
            idx = self.lista_items.GetFocusedItem()
            if idx < 0 or idx >= len(self.lista_items_db_ids):
                speak(_("Debe enfocar un toot para poder responder."), interrupt=True)
                return
            db_id = self.lista_items_db_ids[idx]

        # Obtener ID original, autor, visibilidad, menciones y CW (reglas de TWBlue para
        # responder; ver _visibilidad_respuesta y _construir_menciones_respuesta).
        row = db.query_one(
            "SELECT COALESCE(reblog_of_id, status_id), author_username, visibility, "
            "COALESCE(mentions_json, ''), COALESCE(spoiler_text, ''), COALESCE(reblog_by_username, '') "
            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return

        status_id, username, visibilidad, mentions_json, spoiler, reblog_by = row
        # Si respondes a un IMPULSO, se menciona también a QUIEN lo impulsó (primero), como TWBlue:
        # así la persona que te mostró el contenido recibe la respuesta. El autor original va después.
        menciones = self._construir_menciones_respuesta(username, mentions_json, reblog_by_username=reblog_by)
        texto_inicial = (" ".join(menciones) + " ") if menciones else ""
        from .mastodon_views import ComposeTootDialog
        dlg = ComposeTootDialog(
            self,
            self.active_mastodon_account_id,
            in_reply_to_id=status_id,
            texto_inicial=texto_inicial,
            reply_visibility=self._visibilidad_respuesta(visibilidad),
            cw_inicial=spoiler.strip()
        )
        dlg.ShowModal()
        dlg.Destroy()

    def _visibilidad_respuesta(self, visibilidad_original):
        """Privacidad PROPUESTA al responder (el usuario siempre puede cambiarla en el selector de
        privacidad). El comportamiento se elige en Preferencias -> Mastodon ('reply_visibility_mode'):

        - 'oficial' (por defecto): igual que la interfaz oficial de Mastodon
          (compose.js -> privacyPreference): se usa la MÁS PRIVADA entre la visibilidad del mensaje
          al que respondes y TU visibilidad por defecto. Orden de público a privado: público <
          no listado < privado (solo seguidores) < directo. Así, responder a un 'solo seguidores'
          sale 'solo seguidores', a un directo sale directo, y a un público/no listado usa tu
          visibilidad por defecto si es más privada.

        - 'no_listado': responder SIEMPRE como No listado, salvo los mensajes directos, que siguen
          siendo directos (para no continuar en privado una conversación). Más discreto: tus
          respuestas no aparecen en la línea pública aunque tu visibilidad por defecto sea pública.
        """
        orden = ["public", "unlisted", "private", "direct"]
        modo = config.get("mastodon", "reply_visibility_mode", "oficial")
        if modo == "no_listado":
            return "direct" if visibilidad_original == "direct" else "unlisted"
        # 'oficial': la más privada entre la del mensaje y tu visibilidad por defecto.
        base = mastodon_service.visibilidad_predeterminada(self.active_mastodon_account_id)
        i_orig = orden.index(visibilidad_original) if visibilidad_original in orden else 0
        i_base = orden.index(base) if base in orden else 0
        return orden[max(i_orig, i_base, 0)]

    def _construir_menciones_respuesta(self, author_username, mentions_json, reblog_by_username=None):
        """Lista ordenada y sin repetidos de menciones (@acct) para una respuesta.

        Orden (como TWBlue): si el toot es un IMPULSO, primero QUIEN lo impulsó (reblog_by_username);
        después el autor del toot; y luego el resto de cuentas mencionadas. Se EXCLUYE tu propia
        cuenta y no se repiten."""
        import json as _json
        # Mi cuenta, para no autocitarme. El handle se guarda como '@usuario@instancia'.
        me_local = me_domain = ""
        cuenta = db.query_one("SELECT name FROM accounts WHERE id = ?", (self.active_mastodon_account_id,))
        if cuenta and cuenta[0]:
            partes = cuenta[0].lstrip("@").split("@")
            me_local = partes[0].strip().lower()
            me_domain = partes[1].strip().lower() if len(partes) > 1 else ""

        def es_mio(acct):
            a = (acct or "").lstrip("@").strip().lower()
            if not a:
                return True
            p = a.split("@")
            if p[0] != me_local:
                return False
            # Coincide si es local (sin dominio), si el dominio coincide, o si no conozco
            # mi dominio. Un usuario con mi mismo nombre en OTRA instancia no se excluye.
            return len(p) == 1 or not me_domain or (len(p) > 1 and p[1] == me_domain)

        candidatos = []
        if reblog_by_username:
            candidatos.append(reblog_by_username.lstrip("@"))
        if author_username:
            candidatos.append(author_username.lstrip("@"))
        try:
            for m in (_json.loads(mentions_json) if mentions_json else []):
                if m:
                    candidatos.append(str(m).lstrip("@"))
        except Exception:
            pass

        vistos, salida = set(), []
        for acct in candidatos:
            clave = acct.strip().lower()
            if not clave or clave in vistos or es_mio(acct):
                continue
            vistos.add(clave)
            salida.append("@" + acct.strip())
        return salida

    def _usuario_es(self, username) -> bool:
        """True si un '@acct' de Mastodon corresponde al usuario de la cuenta activa
        (auditoría A13: sirve para saber si el IMPULSOR de una fila eres tú, no solo el
        autor original). Un acct local (sin dominio) solo puede ser tuyo si coincide con
        tu usuario; los remotos siempre llevan dominio y no coinciden."""
        if not username or not self.active_mastodon_account_id:
            return False
        cuenta = db.query_one("SELECT name FROM accounts WHERE id = ?", (self.active_mastodon_account_id,))
        if not cuenta or not cuenta[0]:
            return False
        me_local = cuenta[0].lstrip("@").split("@")[0].strip().lower()
        partes = str(username).lstrip("@").split("@")
        return len(partes) == 1 and partes[0].strip().lower() == me_local

    def _on_deshacer_impulso(self, db_id, idx=None):
        """Deshace TU impulso de una publicación (auditoría A13). Actúa sobre el estado
        original (unreblog) y, si la fila visible era tu propia entrada de impulso, la
        quita de la lista (esa entrada deja de existir en el servidor)."""
        fila = db.query_one("SELECT reblog_of_id, COALESCE(reblog_by_username,'') "
                            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        es_mi_entrada_impulso = bool(fila and fila[0] and self._usuario_es(fila[1]))

        def _cb(res, err):
            if err:
                notificar(_("No se pudo deshacer el impulso: {0}").format(err))
                return
            speak(_("Impulso deshecho."), interrupt=True)
            if es_mi_entrada_impulso:
                try:
                    self._quitar_toot_de_lista(db_id)
                except Exception:
                    self._refrescar_indicadores_toot(db_id)
            else:
                self._refrescar_indicadores_toot(db_id)
        mastodon_service.ejecutar_accion_toot_async(
            self.active_mastodon_account_id, db_id, "unreblog", callback=_cb)

    def _toot_es_mio(self, db_id) -> bool:
        """Indica si la publicación de Mastodon con ese db_id la escribiste TÚ con la
        cuenta activa. Se usa para mostrar Editar/Borrar solo en tus publicaciones.

        La cuenta guarda su handle como '@usuario@instancia'; el autor de un toot se
        guarda como '@acct' y, cuando el toot es tuyo (es local en tu instancia), el
        acct es solo tu usuario sin dominio. Así, es tuyo si el autor es exactamente
        '@tu_usuario' (sin dominio); un usuario con tu mismo nombre en otra instancia
        llevaría dominio y no coincidiría."""
        if not self.active_mastodon_account_id:
            return False
        fila = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not fila or not fila[0]:
            return False
        cuenta = db.query_one("SELECT name FROM accounts WHERE id = ?", (self.active_mastodon_account_id,))
        if not cuenta or not cuenta[0]:
            return False
        me_local = cuenta[0].lstrip("@").split("@")[0].strip().lower()
        autor = fila[0].lstrip("@").split("@")
        return len(autor) == 1 and autor[0].strip().lower() == me_local

    def _on_editar_toot(self, db_id):
        """Carga el texto original de tu toot y abre el compositor en modo edición."""
        speak(_("Cargando el texto original para editar..."), interrupt=True)

        def _finish(src, err):
            if err or not src:
                notificar(_("No se pudo cargar el toot para editar: {0}").format(err or ""))
                return
            # Adjuntos ya publicados, para poder cambiarlos/quitarlos/editar su alt.
            media_existente = []
            fila_m = db.query_one("SELECT media_json FROM mastodon_statuses WHERE id = ?", (db_id,))
            if fila_m and fila_m[0]:
                try:
                    for att in json.loads(fila_m[0]):
                        alt = att.get("description") or ""
                        tipo = att.get("type") or ""
                        # Punto focal actual del adjunto (meta.focus), para poder
                        # cambiarlo al editar y enviarlo solo si de verdad cambió.
                        foco = None
                        try:
                            f = (att.get("meta") or {}).get("focus") or {}
                            if f:
                                foco = [float(f.get("x", 0)), float(f.get("y", 0))]
                        except (TypeError, ValueError):
                            foco = None
                        media_existente.append({
                            "existing": True,
                            "id": att.get("id"),
                            "alt": alt,
                            "alt_original": alt,
                            "focus": foco,
                            "focus_original": list(foco) if foco else None,
                            "tipo": tipo,
                            "url": att.get("url") or att.get("remote_url") or "",
                            "nombre": _("{0} ya publicado").format(tipo or _("adjunto")),
                        })
                except Exception:
                    pass
            from .mastodon_views import ComposeTootDialog
            dlg = ComposeTootDialog(
                self,
                self.active_mastodon_account_id,
                texto_inicial=src.get("text", ""),
                cw_inicial=src.get("spoiler_text", ""),
                edit_status_db_id=db_id,
                media_existente=media_existente
            )
            if dlg.ShowModal() == wx.ID_OK:
                # Refrescar la fila editada en la lista (sin mover el foco del usuario).
                self._refrescar_fila_mastodon(db_id)
            dlg.Destroy()

        mastodon_service.obtener_fuente_toot_async(self.active_mastodon_account_id, db_id, _finish)

    def _on_borrar_toot(self, db_id, idx):
        """Borra tu propia publicación, con confirmación (no se puede deshacer)."""
        if confirmar(self, _("¿Borrar esta publicación? Esta acción no se puede deshacer."),
                     _("Borrar publicación")) != wx.ID_YES:
            return

        def _finish(res, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
                return
            notificar(_("Publicación borrada."))
            # Quitar la fila de la lista en su sitio, sin recargar ni mover el foco.
            try:
                if 0 <= idx < len(self.lista_items_db_ids) and self.lista_items_db_ids[idx] == db_id:
                    self.lista_items.eliminar_fila(idx)
                    del self.lista_items_db_ids[idx]
            except Exception:
                pass

        mastodon_service.ejecutar_accion_toot_async(self.active_mastodon_account_id, db_id, "delete", callback=_finish)

    def _on_borrar_y_redactar(self, db_id, idx):
        """Borra tu publicación y reabre el compositor con su texto fuente, advertencia de
        contenido y visibilidad (el 'borrar y volver a redactar' de Mastodon, auditoría N4)."""
        if confirmar(self, _("¿Borrar esta publicación y volver a redactarla? Se elimina del "
                             "servidor (sus respuestas, impulsos y favoritos se pierden) y el "
                             "compositor se abrirá con su texto para corregirlo y publicarlo "
                             "de nuevo."),
                     _("Borrar y volver a redactar")) != wx.ID_YES:
            return
        cuenta = self.active_mastodon_account_id

        def _finish(datos, err):
            if err:
                notificar(_("No se pudo borrar: {0}").format(err))
                return
            # Quitar la fila de la lista en su sitio, sin recargar ni mover el foco.
            try:
                if 0 <= idx < len(self.lista_items_db_ids) and self.lista_items_db_ids[idx] == db_id:
                    self.lista_items.eliminar_fila(idx)
                    del self.lista_items_db_ids[idx]
            except Exception:
                pass
            if datos.get("tenia_adjuntos"):
                notificar(_("La publicación tenía archivos adjuntos: no se recuperan, habría "
                            "que adjuntarlos de nuevo."))
            from .mastodon_views import ComposeTootDialog
            dlg = ComposeTootDialog(
                self, cuenta,
                in_reply_to_id=datos.get("in_reply_to_id"),
                texto_inicial=datos.get("text", ""),
                cw_inicial=datos.get("spoiler_text", ""),
                reply_visibility=datos.get("visibility"))
            dlg.ShowModal()
            dlg.Destroy()

        mastodon_service.borrar_y_redactar_async(cuenta, db_id, callback=_finish)

    def _on_alias_autor(self, db_id):
        """Alias LOCAL de una cuenta (auditoría N9, estilo TWBlue): cambia cómo se LEE el
        nombre del autor en tus listas y notificaciones, sin tocar nada en Mastodon. El
        @usuario real sigue siempre visible en su columna y en los detalles. Vacío = quitar."""
        row = db.query_one("SELECT author_username, author_name FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            notificar(_("No se pudo identificar la cuenta."))
            return
        acct = (row[0] or "").lstrip("@")
        nombre = row[1] or acct
        alias_map = dict(config.get("mastodon", "alias_local", {}) or {})
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self,
            _("Alias local para {0} (@{1}). Solo cambia cómo se lee el nombre en TUS listas; "
              "el @usuario real sigue visible. Deja el cuadro vacío para quitar el alias:").format(nombre, acct),
            _("Alias local de la cuenta"),
            value=alias_map.get(acct, ""))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        alias = dlg.GetValue().strip()
        dlg.Destroy()
        if alias:
            alias_map[acct] = alias
        else:
            alias_map.pop(acct, None)
        config.set("mastodon", "alias_local", alias_map)
        # Repintar el buffer desde la caché para que el cambio se vea ya, sin red.
        try:
            self._cargar_listado_toots(cache_only=True)
        except Exception:
            pass
        notificar(_("Alias guardado: {0} se leerá como {1}.").format(acct, alias) if alias
                  else _("Alias quitado: se vuelve a leer el nombre real."))

    def _on_fijar_toot(self, db_id, fijar):
        """Fija o quita de tu perfil tu propia publicación de Mastodon."""
        def _finish(res, err):
            if err:
                notificar(_("No se pudo cambiar la publicación fijada: {0}").format(err))
            elif fijar:
                notificar(_("Publicación fijada en tu perfil."))
            else:
                notificar(_("Publicación quitada de tu perfil."))
        mastodon_service.ejecutar_accion_toot_async(
            self.active_mastodon_account_id, db_id, "pin" if fijar else "unpin", callback=_finish)

    def _refrescar_fila_mastodon(self, db_id):
        """Vuelve a leer de la base de datos el contenido de un toot y actualiza su fila
        en la lista (columnas y lectura), en su sitio, tras editarlo."""
        try:
            idx = self.lista_items_db_ids.index(db_id)
        except ValueError:
            return
        fila = db.query_one(
            "SELECT author_name, content, created_at, visibility, application FROM mastodon_statuses WHERE id = ?",
            (db_id,))
        if not fila:
            return
        autor, contenido, fecha, vis, app = fila
        # En la base de datos el contenido ya está como texto limpio (sin HTML).
        cuerpo = (contenido or "").replace("\n", " ").strip()
        fecha_rel = formatear_tiempo_relativo(fecha) if fecha else ""
        vis_map = {"public": _("Público"), "unlisted": _("No listado"),
                   "private": _("Privado"), "direct": _("Mensaje directo")}
        valores_dict = {"author": autor or "", "content": cuerpo, "date": fecha_rel,
                        "visibility": vis_map.get(vis, vis or "")}
        valores = [valores_dict.get(c, "") for c, _e in getattr(self, "_cols_actuales", [])]
        try:
            self.lista_items.actualizar_fila(idx, valores, "")
        except Exception:
            pass
