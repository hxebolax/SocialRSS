# -*- coding: utf-8 -*-
"""MainFrame (mixin): navegacion_arbol.
Metodos: _crear_menus, _registrar_atajos_teclado, _activar_servicio_por_posicion, _on_char_hook_buffers
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
import wx

from app.core.config_manager import config
from app.core.speech import speak


from .shortcut_manager import shortcut_manager

log = logging.getLogger(__name__)


class _Mf_navegacion_arbol:
    def _lbl_atajo(self, accion, texto):
        """Etiqueta de menú con la tecla actual como REFERENCIA en español, entre paréntesis (sin
        acelerador \\t). Se usa cuando el acelerador real ya vive en la tabla global."""
        combo = shortcut_manager.combo_legible(accion)
        return f"{texto} ({combo})" if combo else texto

    def _menu_atajo(self, accion, texto):
        """Etiqueta de menú con acelerador real de MENÚ (\\t). Se usa para los atajos cuyo acelerador
        es el propio ítem (no están en la tabla global). wx muestra y aplica el sufijo del tabulador."""
        raw = shortcut_manager.obtener_atajo(accion)
        return f"{texto}\t{raw}" if raw else texto

    def _on_ayuda_contextual(self, event):
        """Ctrl+H: anuncia la ayuda del control que tiene el foco (en vez de abrir el Manual)."""
        from .accessible_controls import describir_control_enfocado
        describir_control_enfocado()

    def _combo_coincide(self, event, accion):
        """True si la combinación pulsada (evento de teclado) coincide con la tecla configurada para
        'accion'. Se usa en el char-hook para atajos que no van por la tabla de aceleradores."""
        tpl = shortcut_manager.combo_to_wx(shortcut_manager.obtener_atajo(accion), 0)
        if not tpl:
            return False
        flags, keycode, _mid = tpl
        mods = 0
        if flags & wx.ACCEL_CTRL:
            mods |= wx.MOD_CONTROL
        if flags & wx.ACCEL_SHIFT:
            mods |= wx.MOD_SHIFT
        if flags & wx.ACCEL_ALT:
            mods |= wx.MOD_ALT
        return event.GetModifiers() == mods and event.GetKeyCode() == keycode

    def _crear_menus(self):
        menubar = wx.MenuBar()
        # Un servicio DESACTIVADO no muestra su menú (igual que el árbol y el alta de cuentas).
        from app.core.feature_flags import servicio_habilitado

        # --- MENÚ ARCHIVO ---
        menu_archivo = wx.Menu()
        
        item_cuentas = menu_archivo.Append(wx.ID_ANY, _("Gestionar Cuentas..."), _("Administrar cuentas de Mastodon, Bluesky, RSS y Telegram"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_cuentas, item_cuentas)

        menu_archivo.AppendSeparator()

        item_salir = menu_archivo.Append(wx.ID_EXIT, _("Salir\tCtrl+Q"), _("Cerrar la aplicación"))
        self.Bind(wx.EVT_MENU, self._on_salir, item_salir)
        
        menubar.Append(menu_archivo, _("&Archivo"))
        
        # --- MENÚ MASTODON ---
        menu_mastodon = wx.Menu()
        
        # Estas teclas son UNIFICADAS: con Ctrl+N, Ctrl+R, etc. se actúa sobre la
        # red enfocada (Mastodon o Bluesky), no solo sobre Mastodon. Por eso se
        # enlazan a despachadores. Al pulsar el item del menú se crea/actúa en el
        # servicio activo en ese momento.
        # Estos atajos (Ctrl+N, Ctrl+R...) son ACELERADORES GLOBALES registrados en
        # _registrar_atajos_teclado, no del menú, para que funcionen aunque este menú no se
        # muestre (Mastodon desactivado). Aquí se enseñan entre paréntesis solo como referencia.
        item_nuevo_toot = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_nuevo", _("Nuevo Toot o post...")), _("Componer una publicación en la red activa (Mastodon o Bluesky)"))
        self.Bind(wx.EVT_MENU, self._disp_nuevo, item_nuevo_toot)

        item_responder = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_responder", _("Responder...")), _("Responder a la publicación seleccionada"))
        self.Bind(wx.EVT_MENU, self._disp_responder, item_responder)

        item_citar = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_citar", _("Citar...")), _("Citar la publicación seleccionada"))
        self.Bind(wx.EVT_MENU, self._disp_citar, item_citar)

        item_boost = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_boost_like", _("Me gusta / favorito")), _("Marcar o quitar favorito (Mastodon) o me gusta (Bluesky/X). Para impulsar usa el menú contextual."))
        self.Bind(wx.EVT_MENU, self._disp_boost_like, item_boost)

        item_mas = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_cargar_mas", _("Cargar más toots antiguos")), _("Descargar una página de publicaciones más antiguas"))
        self.Bind(wx.EVT_MENU, self._on_cargar_mas_toots, item_mas)

        item_ver_conv = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_ver_conv", _("Ver conversación")), _("Abrir la conversación de la publicación enfocada (Mastodon, Bluesky o X)"))
        self.Bind(wx.EVT_MENU, self._disp_ver_conversacion, item_ver_conv)

        item_ver_texto = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_ver_texto", _("Ver el texto de la publicación")), _("Mostrar en un cuadro de solo lectura el texto completo de la publicación o artículo enfocado"))
        self.Bind(wx.EVT_MENU, self._disp_ver_texto_plano, item_ver_texto)

        item_tl_usuario = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_tl_usuario", _("Abrir cronología de un usuario...")), _("Escribe un usuario (acct) y abre sus publicaciones"))
        self.Bind(wx.EVT_MENU, self._on_abrir_timeline_usuario, item_tl_usuario)

        item_buscar_users = menu_mastodon.Append(wx.ID_ANY, _("Buscar usuarios..."), _("Buscar cuentas por nombre, @usuario o usuario@instancia (resuelve cuentas remotas)"))
        self.Bind(wx.EVT_MENU, self._on_buscar_usuarios, item_buscar_users)

        item_ver_perfil = menu_mastodon.Append(wx.ID_ANY, _("Ver perfil de una cuenta..."), _("Escribe una cuenta (@usuario o usuario@instancia, con o sin arroba) y muestra su ficha, aunque sea remota o citada sin arroba"))
        self.Bind(wx.EVT_MENU, self._on_ver_perfil_cuenta, item_ver_perfil)

        menu_mastodon.AppendSeparator()

        item_hilo_nuevo = menu_mastodon.Append(wx.ID_ANY, self._lbl_atajo("pub_hilo", _("Nuevo Hilo...")), _("Crear un nuevo hilo en la red activa (Mastodon o Bluesky)"))
        self.Bind(wx.EVT_MENU, self._disp_nuevo_hilo, item_hilo_nuevo)
        
        item_hilos_locales = menu_mastodon.Append(wx.ID_ANY, _("Ver Hilos Guardados..."), _("Abrir administrador de hilos locales"))
        self.Bind(wx.EVT_MENU, self._on_ver_hilos_guardados, item_hilos_locales)

        menu_mastodon.AppendSeparator()
        item_guardar_busqueda = menu_mastodon.Append(wx.ID_ANY, _("Guardar búsqueda como buffer..."), _("Crear un buffer con una búsqueda o hashtag"))
        self.Bind(wx.EVT_MENU, self._on_guardar_busqueda, item_guardar_busqueda)

        item_listas = menu_mastodon.Append(wx.ID_ANY, _("Listas de Mastodon..."), _("Crear, eliminar y gestionar listas"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_listas, item_listas)

        # Colecciones (Mastodon 4.6): listas PÚBLICAS de cuentas que recomiendas.
        item_colecciones = menu_mastodon.Append(wx.ID_ANY, _("Mis colecciones..."),
                                                _("Listas públicas de cuentas que recomiendas (Mastodon 4.6)"))
        self.Bind(wx.EVT_MENU, self._on_mis_colecciones, item_colecciones)
        item_col_estoy = menu_mastodon.Append(wx.ID_ANY, _("Colecciones en las que apareces..."),
                                              _("Colecciones de otras personas que te incluyen; puedes retirarte"))
        self.Bind(wx.EVT_MENU, self._on_colecciones_donde_estoy, item_col_estoy)

        item_filtros_srv = menu_mastodon.Append(wx.ID_ANY, _("Filtros del servidor..."), _("Filtros aplicados por tu instancia de Mastodon"))
        self.Bind(wx.EVT_MENU, self._on_filtros_servidor, item_filtros_srv)

        item_solicitudes = menu_mastodon.Append(wx.ID_ANY, _("Solicitudes de seguimiento..."), _("Aceptar o rechazar a quienes piden seguirte (cuentas privadas)"))
        self.Bind(wx.EVT_MENU, self._on_solicitudes_seguimiento, item_solicitudes)

        item_novedades_seg = menu_mastodon.Append(wx.ID_ANY, _("Novedades de seguidores..."), _("Comparar con la última vez: quién te ha seguido y quién te ha dejado de seguir"))
        self.Bind(wx.EVT_MENU, self._on_novedades_seguidores, item_novedades_seg)

        item_limpiar_notifs = menu_mastodon.Append(wx.ID_ANY, _("Limpiar todas las notificaciones..."), _("Descartar del servidor todas tus notificaciones de Mastodon"))
        self.Bind(wx.EVT_MENU, self._on_limpiar_notificaciones, item_limpiar_notifs)

        item_mi_perfil_mast = menu_mastodon.Append(wx.ID_ANY, _("Editar mi perfil de Mastodon..."), _("Cambiar nombre, biografía, avatar, banner y opciones de tu cuenta"))
        self.Bind(wx.EVT_MENU, self._on_editar_mi_perfil_mastodon, item_mi_perfil_mast)

        item_ht_dest = menu_mastodon.Append(wx.ID_ANY, _("Hashtags destacados de mi perfil..."), _("Gestionar los hashtags que aparecen destacados en tu perfil"))
        self.Bind(wx.EVT_MENU, self._on_hashtags_destacados, item_ht_dest)

        item_cuentas_dest = menu_mastodon.Append(wx.ID_ANY, _("Cuentas destacadas en mi perfil..."), _("Ver y quitar las cuentas que destacas en tu perfil"))
        self.Bind(wx.EVT_MENU, self._on_cuentas_destacadas, item_cuentas_dest)

        item_programados = menu_mastodon.Append(wx.ID_ANY, _("Publicaciones programadas..."), _("Ver o cancelar tus publicaciones programadas"))
        self.Bind(wx.EVT_MENU, self._on_programados, item_programados)

        item_borradores = menu_mastodon.Append(wx.ID_ANY, _("Borradores..."), _("Retomar o borrar los borradores guardados del compositor (sobreviven al cerrar la aplicación)"))
        self.Bind(wx.EVT_MENU, self._on_borradores_mastodon, item_borradores)

        item_conversaciones = menu_mastodon.Append(wx.ID_ANY, _("Conversaciones de mensajes directos..."), _("Tus conversaciones reales del servidor: participantes, último mensaje, no leídas; abrir o marcar como leída"))
        self.Bind(wx.EVT_MENU, self._on_conversaciones_directas, item_conversaciones)

        menu_mastodon.AppendSeparator()
        item_seguir_tag = menu_mastodon.Append(wx.ID_ANY, _("Seguir un hashtag..."), _("Seguir un hashtag para que sus publicaciones entren en tu Inicio"))
        self.Bind(wx.EVT_MENU, self._on_seguir_hashtag, item_seguir_tag)
        item_tags_seguidos = menu_mastodon.Append(wx.ID_ANY, _("Hashtags que sigues..."), _("Ver y dejar de seguir hashtags"))
        self.Bind(wx.EVT_MENU, self._on_hashtags_seguidos, item_tags_seguidos)

        menu_mastodon.AppendSeparator()
        item_disc_tags = menu_mastodon.Append(wx.ID_ANY, _("Descubrir: hashtags en tendencia..."), _("Hashtags en tendencia en tu servidor"))
        self.Bind(wx.EVT_MENU, self._on_descubrir_tendencias_tags, item_disc_tags)
        item_disc_links = menu_mastodon.Append(wx.ID_ANY, _("Descubrir: enlaces en tendencia..."), _("Noticias y enlaces en tendencia"))
        self.Bind(wx.EVT_MENU, self._on_descubrir_enlaces, item_disc_links)
        item_disc_dir = menu_mastodon.Append(wx.ID_ANY, _("Descubrir: directorio de perfiles..."), _("Perfiles activos del directorio del servidor"))
        self.Bind(wx.EVT_MENU, self._on_directorio, item_disc_dir)
        item_disc_sug = menu_mastodon.Append(wx.ID_ANY, _("Descubrir: cuentas sugeridas..."), _("Cuentas que el servidor sugiere seguir"))
        self.Bind(wx.EVT_MENU, self._on_sugerencias_mastodon, item_disc_sug)

        item_dominios = menu_mastodon.Append(wx.ID_ANY, _("Dominios bloqueados..."), _("Ver, añadir o quitar dominios (instancias) bloqueados"))
        self.Bind(wx.EVT_MENU, self._on_dominios_bloqueados, item_dominios)

        menu_mastodon.AppendSeparator()
        item_notif_agr = menu_mastodon.Append(wx.ID_ANY, _("Notificaciones agrupadas..."), _("Grupos reales del servidor (quién y cuántos), con ver cuentas y descartar por grupo"))
        self.Bind(wx.EVT_MENU, self._on_notificaciones_agrupadas, item_notif_agr)
        item_notif_pol = menu_mastodon.Append(wx.ID_ANY, _("Política de notificaciones..."), _("Filtrar notificaciones de desconocidos, cuentas nuevas, etc."))
        self.Bind(wx.EVT_MENU, self._on_politica_notificaciones, item_notif_pol)
        item_destacar_tag = menu_mastodon.Append(wx.ID_ANY, _("Destacar un hashtag en mi perfil..."), _("Fijar (o quitar) un hashtag destacado en tu perfil"))
        self.Bind(wx.EVT_MENU, self._on_destacar_hashtag_perfil, item_destacar_tag)

        item_solic_notif = menu_mastodon.Append(wx.ID_ANY, _("Solicitudes de notificación..."), _("Notificaciones de cuentas filtradas (desconocidos): aceptar o descartar"))
        self.Bind(wx.EVT_MENU, self._on_solicitudes_notificacion, item_solic_notif)
        item_no_leidas = menu_mastodon.Append(wx.ID_ANY, _("Notificaciones sin leer (contador)"), _("Decir cuántas notificaciones tienes sin leer"))
        self.Bind(wx.EVT_MENU, self._on_contador_no_leidas, item_no_leidas)

        menu_mastodon.AppendSeparator()
        item_instancia = menu_mastodon.Append(wx.ID_ANY, _("Acerca de esta instancia..."), _("Reglas, descripción, términos del servicio e idiomas del servidor"))
        self.Bind(wx.EVT_MENU, self._on_acerca_instancia, item_instancia)
        item_capacidades = menu_mastodon.Append(wx.ID_ANY, _("Capacidades de tu instancia..."), _("Qué admite tu servidor: límites de caracteres y adjuntos, encuestas, traducción, citas nativas..."))
        self.Bind(wx.EVT_MENU, self._on_capacidades_instancia, item_capacidades)

        # Submenú de ADMINISTRACIÓN: solo si el usuario lo activó en Preferencias -> Mastodon. Al
        # cambiar esa casilla, _crear_menus se reconstruye, así que aparece o desaparece en caliente.
        if config.get("mastodon", "admin_tools", False):
            menu_mastodon.AppendSeparator()
            submenu_admin = wx.Menu()
            it = submenu_admin.Append(wx.ID_ANY, _("Informes (reportes)..."))
            self.Bind(wx.EVT_MENU, self._on_admin_informes, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Cuentas..."))
            self.Bind(wx.EVT_MENU, self._on_admin_cuentas, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Dominios bloqueados..."))
            self.Bind(wx.EVT_MENU, self._on_admin_dominios, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Dominios permitidos (lista blanca)..."))
            self.Bind(wx.EVT_MENU, self._on_admin_dominios_permitidos, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Bloqueos de dominio de email..."))
            self.Bind(wx.EVT_MENU, self._on_admin_email_blocks, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Bloqueos de email concreto (canónico)..."))
            self.Bind(wx.EVT_MENU, self._on_admin_email_canonicos, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Bloqueos de IP..."))
            self.Bind(wx.EVT_MENU, self._on_admin_ip_blocks, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Tendencias y descubrimiento..."),
                                      _("Moderar tendencias, todos los hashtags y publicadores de enlaces"))
            self.Bind(wx.EVT_MENU, self._on_admin_tendencias, it)
            it = submenu_admin.Append(wx.ID_ANY, _("Estadísticas del servidor..."),
                                      _("Usuarios activos, registros, interacciones, informes, idiomas y retención (últimos 30 días)"))
            self.Bind(wx.EVT_MENU, self._on_admin_estadisticas, it)
            menu_mastodon.AppendSubMenu(submenu_admin, _("Administración"))

        if servicio_habilitado("mastodon"):
            menubar.Append(menu_mastodon, _("&Mastodon"))

        # --- MENÚ BLUESKY ---
        # El alta/eliminación de cuentas se hace desde "Gestionar Cuentas..." (Archivo).
        menu_bsky = wx.Menu()
        # Atajos UNIFICADOS (mismos que Mastodon). Se muestran aquí como texto entre
        # paréntesis porque una misma tecla solo puede ser acelerador real de un
        # único item de menú (lo es en el menú Mastodon), pero funciona en toda la
        # ventana: estando en Bluesky, Ctrl+N crea un post, etc. Estos items van a
        # los mismos despachadores, así que al pulsarlos actúan sobre Bluesky.
        item_bsky_post = menu_bsky.Append(wx.ID_ANY, self._lbl_atajo("pub_nuevo", _("Nuevo post de Bluesky...")), _("Componer y publicar un post"))
        self.Bind(wx.EVT_MENU, self._disp_nuevo, item_bsky_post)
        item_bsky_hilo = menu_bsky.Append(wx.ID_ANY, self._lbl_atajo("pub_hilo", _("Nuevo hilo de Bluesky...")), _("Componer y publicar un hilo encadenado"))
        self.Bind(wx.EVT_MENU, self._disp_nuevo_hilo, item_bsky_hilo)
        item_bsky_resp = menu_bsky.Append(wx.ID_ANY, self._lbl_atajo("pub_responder", _("Responder")), _("Responder a la publicación enfocada"))
        self.Bind(wx.EVT_MENU, self._disp_responder, item_bsky_resp)
        item_bsky_citar = menu_bsky.Append(wx.ID_ANY, self._lbl_atajo("pub_citar", _("Citar")), _("Citar la publicación enfocada"))
        self.Bind(wx.EVT_MENU, self._disp_citar, item_bsky_citar)
        item_bsky_like = menu_bsky.Append(wx.ID_ANY, self._lbl_atajo("pub_boost_like", _("Me gusta")), _("Dar o quitar me gusta a la publicación enfocada"))
        self.Bind(wx.EVT_MENU, self._disp_boost_like, item_bsky_like)
        item_bsky_mas = menu_bsky.Append(wx.ID_ANY, _("Cargar más publicaciones"), _("Descargar una página de publicaciones más antiguas del buffer activo"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.cargar_mas(), item_bsky_mas)
        item_bsky_borradores = menu_bsky.Append(wx.ID_ANY, _("Borradores de Bluesky..."), _("Abrir un borrador guardado"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.borradores(), item_bsky_borradores)
        item_bsky_outbox = menu_bsky.Append(wx.ID_ANY, _("Salida pendiente..."), _("Publicaciones que no llegaron a confirmarse: reintentar sin duplicados o borrar"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.salida_pendiente(), item_bsky_outbox)
        menu_bsky.AppendSeparator()
        item_bsky_perfil = menu_bsky.Append(wx.ID_ANY, _("Mi perfil..."), _("Editar nombre, biografía, avatar y handle"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.mi_perfil(), item_bsky_perfil)
        item_bsky_apppw = menu_bsky.Append(wx.ID_ANY, _("Contraseñas de aplicación..."), _("Crear o revocar contraseñas de aplicación"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.app_passwords(), item_bsky_apppw)
        menu_bsky.AppendSeparator()
        item_bsky_buscar = menu_bsky.Append(wx.ID_ANY, _("Guardar búsqueda como buffer..."), _("Crear un buffer con una búsqueda de publicaciones"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.guardar_busqueda(), item_bsky_buscar)
        item_bsky_cuentas = menu_bsky.Append(wx.ID_ANY, _("Buscar cuentas..."), _("Buscar usuarios por nombre o handle"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.buscar_cuentas(), item_bsky_cuentas)
        item_bsky_ver_perfil = menu_bsky.Append(wx.ID_ANY, _("Ver perfil de una cuenta..."), _("Escribe un handle (nombre.bsky.social) o DID y muestra su ficha, aunque sea un perfil citado que no esté en ninguna lista"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.ver_perfil_por_handle(), item_bsky_ver_perfil)
        item_bsky_sugeridas = menu_bsky.Append(wx.ID_ANY, _("Cuentas sugeridas..."), _("Descubrir cuentas sugeridas para seguir"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.cuentas_sugeridas(), item_bsky_sugeridas)
        item_bsky_feeds = menu_bsky.Append(wx.ID_ANY, _("Feeds populares..."), _("Descubrir feeds personalizados populares"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.feeds_populares(), item_bsky_feeds)
        item_bsky_feeds_sug = menu_bsky.Append(wx.ID_ANY, _("Feeds sugeridos..."), _("Feeds recomendados para ti por Bluesky"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.feeds_sugeridos(), item_bsky_feeds_sug)
        item_bsky_feeds_mis = menu_bsky.Append(wx.ID_ANY, _("Gestionar mis feeds..."), _("Fijar, reordenar o quitar tus feeds guardados"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.gestionar_feeds_guardados(), item_bsky_feeds_mis)
        item_bsky_lista = menu_bsky.Append(wx.ID_ANY, _("Crear lista..."), _("Crear una lista de curación"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.crear_lista(), item_bsky_lista)
        item_bsky_lista_gest = menu_bsky.Append(wx.ID_ANY, _("Gestionar miembros de listas..."), _("Ver, añadir o quitar cuentas de tus listas; silenciar listas"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.gestionar_miembros_lista(), item_bsky_lista_gest)
        menu_bsky.AppendSeparator()
        item_bsky_grupo = menu_bsky.Append(wx.ID_ANY, _("Crear un grupo de chat..."),
                                           _("Crear una conversación de grupo con varias cuentas"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.crear_grupo(), item_bsky_grupo)
        item_bsky_grupo_in = menu_bsky.Append(wx.ID_ANY, _("Entrar en un grupo con un enlace..."),
                                              _("Unirte a un grupo con su enlace de invitación"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.entrar_en_grupo(), item_bsky_grupo_in)
        menu_bsky.AppendSeparator()
        item_bsky_muted = menu_bsky.Append(wx.ID_ANY, _("Palabras silenciadas..."), _("Ocultar publicaciones que contengan ciertas palabras"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.palabras_silenciadas(), item_bsky_muted)
        item_bsky_labelers = menu_bsky.Append(wx.ID_ANY, _("Servicios de etiquetas..."), _("Ver, suscribirte o darte de baja de servicios de moderación (labelers)"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.servicios_etiquetas(), item_bsky_labelers)
        item_bsky_modpref = menu_bsky.Append(wx.ID_ANY, _("Preferencias de moderación..."), _("Contenido para adultos y qué hacer con cada etiqueta (mostrar, avisar u ocultar), general y por servicio de etiquetado"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.preferencias_moderacion(), item_bsky_modpref)
        item_bsky_ocultas = menu_bsky.Append(wx.ID_ANY, _("Publicaciones ocultadas..."), _("Ver las publicaciones que ocultaste para ti y volver a mostrarlas"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.publicaciones_ocultadas(), item_bsky_ocultas)
        item_bsky_feedpref = menu_bsky.Append(wx.ID_ANY, _("Preferencias del feed Siguiendo y de los hilos..."), _("Respuestas, reposts y citas en Inicio, y orden de las respuestas en los hilos"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.preferencias_feed_siguiendo(), item_bsky_feedpref)
        item_bsky_sp = menu_bsky.Append(wx.ID_ANY, _("Crear paquete de bienvenida (starter pack)..."), _("Crear un starter pack propio a partir de una de tus listas"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.crear_starter_pack(), item_bsky_sp)
        menu_bsky.AppendSeparator()
        item_bsky_fire = menu_bsky.AppendCheckItem(wx.ID_ANY, _("Monitorización en vivo (avanzado)"), _("Avisar al instante de las menciones a tu cuenta mediante el firehose"))
        self.Bind(wx.EVT_MENU, lambda e: self.bluesky_ctrl.toggle_firehose(), item_bsky_fire)
        if servicio_habilitado("bluesky"):
            menubar.Append(menu_bsky, _("&Bluesky"))

        # --- MENÚ REDDIT ---
        if servicio_habilitado("reddit") and getattr(self, "reddit_ctrl", None) is not None:
            menu_reddit = wx.Menu()
            it = menu_reddit.Append(wx.ID_ANY, self._lbl_atajo("pub_nuevo", _("Nueva publicación de Reddit...")), _("Crear una publicación (texto, enlace, imagen, vídeo, galería o encuesta)"))
            self.Bind(wx.EVT_MENU, self._disp_nuevo, it)
            it = menu_reddit.Append(wx.ID_ANY, self._lbl_atajo("pub_responder", _("Responder")), _("Responder al elemento enfocado"))
            self.Bind(wx.EVT_MENU, self._disp_responder, it)
            it = menu_reddit.Append(wx.ID_ANY, self._lbl_atajo("pub_boost_like", _("Voto positivo")), _("Votar positivo o quitar el voto del elemento enfocado"))
            self.Bind(wx.EVT_MENU, self._disp_boost_like, it)
            it = menu_reddit.Append(wx.ID_ANY, _("Cargar más publicaciones"), _("Descargar más elementos del buffer activo"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.cargar_mas(), it)
            menu_reddit.AppendSeparator()
            it = menu_reddit.Append(wx.ID_ANY, _("Enviar mensaje privado..."), _("Enviar un mensaje privado a un usuario"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.enviar_pm(), it)
            it = menu_reddit.Append(wx.ID_ANY, _("Ir a un subreddit..."), _("Abrir las publicaciones de un subreddit por su nombre"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.ir_a_subreddit(), it)
            menu_reddit.AppendSeparator()
            it = menu_reddit.Append(wx.ID_ANY, _("Buscar publicaciones..."), _("Buscar publicaciones en todo Reddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.buscar_publicaciones(), it)
            it = menu_reddit.Append(wx.ID_ANY, _("Buscar subreddits..."), _("Buscar comunidades por nombre o tema"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.buscar_subreddits_dialogo(), it)
            it = menu_reddit.Append(wx.ID_ANY, _("Buscar usuarios..."), _("Buscar usuarios por nombre"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.buscar_usuarios_dialogo(), it)
            menu_reddit.AppendSeparator()
            # Submenú de moderación (solo tiene efecto en subreddits que el usuario modera).
            menu_mod = wx.Menu()
            it = menu_mod.Append(wx.ID_ANY, _("Cola de moderación..."), _("Revisar los elementos pendientes de moderar"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_colas_moderacion(), it)
            it = menu_mod.Append(wx.ID_ANY, _("Registro de moderación..."), _("Ver las últimas acciones de moderación"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.ver_registro_moderacion(), it)
            menu_mod.AppendSeparator()
            it = menu_mod.Append(wx.ID_ANY, _("Usuarios (baneados, silenciados, moderadores)..."), _("Gestionar las relaciones de usuario del subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_usuarios_subreddit(), it)
            it = menu_mod.Append(wx.ID_ANY, _("Motivos de eliminación..."), _("Gestionar los motivos de eliminación del subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_motivos_eliminacion(), it)
            it = menu_mod.Append(wx.ID_ANY, _("Configuración del subreddit..."), _("Editar los ajustes básicos del subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_config_subreddit(), it)
            menu_mod.AppendSeparator()
            it = menu_mod.Append(wx.ID_ANY, _("Modmail (mensajería de moderación)..."), _("Conversaciones de moderación de tus comunidades"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_modmail(), it)
            it = menu_mod.Append(wx.ID_ANY, _("Colecciones..."), _("Crear y gestionar colecciones de publicaciones de un subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_collections(), it)
            menu_reddit.AppendSubMenu(menu_mod, _("&Moderación"))
            # Submenú de personalización de comunidades (wiki, etiquetas, emojis y barra lateral).
            menu_pers = wx.Menu()
            it = menu_pers.Append(wx.ID_ANY, _("Wiki..."), _("Leer y editar las páginas wiki de un subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_wiki(), it)
            it = menu_pers.Append(wx.ID_ANY, _("Etiquetas (flair)..."), _("Plantillas de etiqueta y tu propia etiqueta en un subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_flairs(), it)
            it = menu_pers.Append(wx.ID_ANY, _("Emojis..."), _("Emojis personalizados de un subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_emojis(), it)
            it = menu_pers.Append(wx.ID_ANY, _("Barra lateral (widgets)..."), _("Ver los widgets de la barra lateral de un subreddit"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_widgets(), it)
            menu_reddit.AppendSubMenu(menu_pers, _("&Personalización"))
            menu_reddit.AppendSeparator()
            it = menu_reddit.Append(wx.ID_ANY, _("Borradores..."), _("Tus borradores de publicaciones: crear, publicar y borrar"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_drafts(), it)
            it = menu_reddit.Append(wx.ID_ANY, _("Hilos en directo..."), _("Crear o seguir un hilo en directo y publicar novedades"))
            self.Bind(wx.EVT_MENU, lambda e: self.reddit_ctrl.abrir_live(), it)
            menubar.Append(menu_reddit, _("&Reddit"))

        # --- MENÚ PEERTUBE ---
        if servicio_habilitado("peertube") and getattr(self, "peertube_ctrl", None) is not None:
            menu_peertube = wx.Menu()
            it = menu_peertube.Append(wx.ID_ANY, _("Buscar vídeos..."), _("Buscar vídeos en la instancia"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.buscar_videos_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Buscar canales..."), _("Buscar canales por nombre o tema"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.buscar_canales_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Ir a un canal..."), _("Abrir los vídeos de un canal por su identificador"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.ir_a_canal_dialogo(), it)
            menu_peertube.AppendSeparator()
            it = menu_peertube.Append(wx.ID_ANY, _("Cargar más vídeos"), _("Descargar más elementos del catálogo activo"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.cargar_mas(), it)
            menu_peertube.AppendSeparator()
            it = menu_peertube.Append(wx.ID_ANY, _("Subir vídeo..."), _("Subir un vídeo desde tu equipo"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.subir_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Importar vídeo por URL..."), _("Importar un vídeo desde una URL o magnet"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.importar_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Mis importaciones..."), _("Ver el estado de tus importaciones"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.mis_importaciones_dialogo(), it)
            menu_peertube.AppendSeparator()
            it = menu_peertube.Append(wx.ID_ANY, _("Gestionar mis listas..."), _("Crear y borrar listas de reproducción"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.gestionar_listas_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Gestionar mis canales..."), _("Crear, editar y borrar tus canales"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.gestionar_canales_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Crear directo..."), _("Crear una emisión en directo (RTMP)"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.crear_directo_dialogo(), it)
            menu_peertube.AppendSeparator()
            it = menu_peertube.Append(wx.ID_ANY, _("Preferencias de notificaciones..."), _("Elegir de qué recibir avisos"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.preferencias_notif_dialogo(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Estadísticas de la instancia..."), _("Ver las estadísticas públicas de la instancia"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.estadisticas_instancia(), it)
            it = menu_peertube.Append(wx.ID_ANY, _("Mis denuncias..."), _("Ver el estado de las denuncias que has puesto"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.mis_denuncias_dialogo(), it)
            # Submenú de moderación (solo tiene efecto si eres moderador de la instancia).
            menu_pt_mod = wx.Menu()
            it = menu_pt_mod.Append(wx.ID_ANY, _("Cola de denuncias..."), _("Revisar y resolver las denuncias de la instancia"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.cola_denuncias_dialogo(), it)
            it = menu_pt_mod.Append(wx.ID_ANY, _("Vídeos bloqueados..."), _("Ver y desbloquear los vídeos bloqueados"))
            self.Bind(wx.EVT_MENU, lambda e: self.peertube_ctrl.videos_bloqueados_dialogo(), it)
            menu_peertube.AppendSubMenu(menu_pt_mod, _("&Moderación"))
            # Submenú de administración (solo tiene efecto si eres administrador de la instancia).
            menu_pt_adm = wx.Menu()
            for etiqueta, metodo, ayuda in (
                    (_("Usuarios..."), "admin_usuarios_dialogo", _("Gestionar usuarios, roles y cuotas")),
                    (_("Solicitudes de registro..."), "admin_registros_dialogo", _("Aceptar o rechazar registros")),
                    (_("Federación (instancias)..."), "admin_federacion_dialogo", _("Instancias seguidas y seguidoras")),
                    (_("Trabajos del servidor..."), "admin_jobs_dialogo", _("Ver el estado de los trabajos")),
                    (_("Plugins..."), "admin_plugins_dialogo", _("Instalar y desinstalar plugins")),
                    (_("Tokens de runners..."), "admin_runners_dialogo", _("Generar tokens para runners de transcodificación")),
                    (_("Configuración de la instancia..."), "admin_config_resumen", _("Ver un resumen de la configuración"))):
                it = menu_pt_adm.Append(wx.ID_ANY, etiqueta, ayuda)
                self.Bind(wx.EVT_MENU, lambda e, m=metodo: getattr(self.peertube_ctrl, m)(), it)
            menu_peertube.AppendSubMenu(menu_pt_adm, _("&Administración"))
            menubar.Append(menu_peertube, _("&PeerTube"))

        # --- MENÚ W SOCIAL ---
        # El menú lo construye el propio controlador (wsocial_controller_social.construir_menu),
        # igual de completo que el del resto de redes: publicar, listas y feeds, mensajes y
        # grupos, moderación y la administración de la cuenta.
        if getattr(self, "wsocial_ctrl", None) is not None and servicio_habilitado("wsocial"):
            try:
                self.wsocial_ctrl.construir_menu(menubar)
            except Exception as e:
                log.error(f"No se pudo construir el menú de W Social: {e}")

        # --- MENÚ RSS ---
        menu_rss = wx.Menu()
        
        item_add_feed = menu_rss.Append(wx.ID_ANY, _("Añadir Feed RSS..."), _("Agregar una nueva fuente de noticias RSS"))
        self.Bind(wx.EVT_MENU, self._on_add_feed, item_add_feed)
        item_buscar_feeds = menu_rss.Append(wx.ID_ANY, _("Buscar feeds y podcasts..."), _("Buscar en directorios públicos (Apple Podcasts, fyyd, gPodder, Feedly, NewsBlur, Lemmy, Mastodon) y suscribirse"))
        self.Bind(wx.EVT_MENU, self._on_buscar_feeds, item_buscar_feeds)

        item_add_web = menu_rss.Append(wx.ID_ANY, _("Crear feed desde una web..."), _("Leer una web que NO tiene RSS: se detectan sus artículos y se siguen como un feed"))
        self.Bind(wx.EVT_MENU, self._on_crear_feed_web, item_add_web)

        item_add_yt = menu_rss.Append(wx.ID_ANY, _("Añadir canal de YouTube..."), _("Suscribirte a un canal de YouTube para escuchar sus vídeos como audio"))
        self.Bind(wx.EVT_MENU, self._on_add_youtube_channel, item_add_yt)

        item_categories = menu_rss.Append(wx.ID_ANY, _("Gestionar Categorías..."), _("Administrar categorías RSS"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_categorias, item_categories)

        item_busquedas = menu_rss.Append(wx.ID_ANY, _("Búsquedas guardadas..."), _("Guardar y reutilizar búsquedas de artículos"))
        self.Bind(wx.EVT_MENU, self._on_busquedas_guardadas, item_busquedas)

        item_smart = menu_rss.Append(wx.ID_ANY, _("Carpetas inteligentes..."), _("Carpetas que se llenan solas según criterios (no leídos, texto, feed, fecha...) y viven en el árbol"))
        self.Bind(wx.EVT_MENU, self._on_carpetas_inteligentes, item_smart)

        item_etiquetas = menu_rss.Append(wx.ID_ANY, _("Etiquetas..."), _("Crear y gestionar etiquetas; un feed o artículo puede llevar varias y aparecen en el árbol"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_etiquetas, item_etiquetas)

        item_alertas = menu_rss.Append(wx.ID_ANY, _("Alertas por palabra clave..."), _("Avisarte cuando llegue un artículo con ciertas palabras"))
        self.Bind(wx.EVT_MENU, self._on_alertas_palabra, item_alertas)

        item_rss_import_opml = menu_rss.Append(wx.ID_ANY, _("Importar feeds desde OPML..."), _("Importar archivo de suscripciones OPML"))
        self.Bind(wx.EVT_MENU, self._on_importar_opml, item_rss_import_opml)

        item_takeout = menu_rss.Append(wx.ID_ANY, _("Importar suscripciones de YouTube (Google Takeout)..."), _("Suscribirse a tus canales y listas de YouTube desde el ZIP que descargas de Google Takeout"))
        self.Bind(wx.EVT_MENU, self._on_importar_takeout_youtube, item_takeout)

        item_rss_export_opml = menu_rss.Append(wx.ID_ANY, _("Exportar feeds a OPML..."), _("Exportar feeds activos a archivo OPML (con sus carpetas)"))
        self.Bind(wx.EVT_MENU, self._on_exportar_opml, item_rss_export_opml)

        item_exportar_arts = menu_rss.Append(wx.ID_ANY, _("Exportar artículos..."), _("Exportar varios artículos de una vez (vista actual, destacados o leer más tarde) a texto, HTML o Markdown"))
        self.Bind(wx.EVT_MENU, self._on_exportar_articulos_lote, item_exportar_arts)

        item_salud = menu_rss.Append(wx.ID_ANY, _("Salud de los feeds..."), _("Estado de cada feed: último éxito, errores, próxima comprobación y reintentos"))
        self.Bind(wx.EVT_MENU, self._on_salud_feeds, item_salud)

        item_rules = menu_rss.Append(wx.ID_ANY, _("Reglas y Filtros RSS..."), _("Reglas automáticas para procesar feeds"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_reglas, item_rules)

        item_wallabag = menu_rss.Append(wx.ID_ANY, _("Configurar Wallabag (leer más tarde)..."), _("Conectar con tu servidor de Wallabag para enviar artículos y leerlos en otros dispositivos"))
        self.Bind(wx.EVT_MENU, self._on_config_wallabag, item_wallabag)

        item_sync = menu_rss.Append(wx.ID_ANY, _("Sincronización con servidor..."), _("Sincronizar feeds y estado leído/destacado con FreshRSS, Inoreader, Miniflux u otro servidor compatible"))
        self.Bind(wx.EVT_MENU, self._on_sincronizacion, item_sync)

        menu_rss.AppendSeparator()
        item_limpiar = menu_rss.Append(wx.ID_ANY, _("Limpiar artículos antiguos"), _("Eliminar artículos antiguos según las preferencias (conserva destacados y leer más tarde)"))
        self.Bind(wx.EVT_MENU, self._on_limpiar_articulos_rss, item_limpiar)

        item_optimizar = menu_rss.Append(wx.ID_ANY, _("Comprobar y optimizar base de datos"), _("Verifica la integridad y compacta la base de datos"))
        self.Bind(wx.EVT_MENU, self._on_optimizar_bd, item_optimizar)

        item_papelera = menu_rss.Append(wx.ID_ANY, _("Papelera de artículos..."), _("Ver y restaurar artículos eliminados"))
        self.Bind(wx.EVT_MENU, self._on_abrir_papelera, item_papelera)

        item_descargas = menu_rss.Append(wx.ID_ANY, _("Descargas de podcast..."), _("Episodios descargados para escuchar sin conexión"))
        self.Bind(wx.EVT_MENU, self._on_descargas_podcast, item_descargas)

        menu_rss.AppendSeparator()
        item_historial = menu_rss.Append(wx.ID_ANY, _("Historial de lectura..."), _("Ver los últimos artículos que has abierto y reabrirlos"))
        self.Bind(wx.EVT_MENU, self._on_historial_lectura, item_historial)
        item_estad = menu_rss.Append(wx.ID_ANY, _("Estadísticas de lectura..."), _("Cuántos artículos lees y qué feeds lees más"))
        self.Bind(wx.EVT_MENU, self._on_estadisticas_lectura, item_estad)

        item_digest = menu_rss.Append(wx.ID_ANY, _("Resumen de artículos sin leer..."), _("Un resumen (con IA si la tienes configurada) de tus artículos pendientes por temas"))
        self.Bind(wx.EVT_MENU, self._on_resumen_no_leidos, item_digest)

        menu_rss.AppendSeparator()
        self.item_solo_no_leidos = menu_rss.AppendCheckItem(wx.ID_ANY, _("Mostrar solo no leídos"), _("Ocultar los artículos ya leídos en las listas"))
        self.item_solo_no_leidos.Check(bool(config.get("rss", "show_only_unread", False)))
        self.Bind(wx.EVT_MENU, self._on_toggle_solo_no_leidos, self.item_solo_no_leidos)

        self.item_agrupar_fecha = menu_rss.AppendCheckItem(wx.ID_ANY, _("Agrupar por fecha"), _("Mostrar una etiqueta de fecha al inicio de cada grupo de artículos"))
        self.item_agrupar_fecha.Check(bool(config.get("rss", "group_by_date", False)))
        self.Bind(wx.EVT_MENU, self._on_toggle_agrupar_fecha, self.item_agrupar_fecha)

        self.item_ocultar_futuros = menu_rss.AppendCheckItem(wx.ID_ANY, _("Ocultar artículos anclados (fecha futura)"), _("Ocultar las entradas que algunos feeds fijan con fecha futura para que el primero sea el último artículo real"))
        self.item_ocultar_futuros.Check(bool(config.get("rss", "hide_future_articles", False)))
        self.Bind(wx.EVT_MENU, self._on_toggle_ocultar_futuros, self.item_ocultar_futuros)

        # Complementos (plugins): son solo de RSS, así que viven en un submenú del menú RSS. Se
        # guarda la referencia al submenú para poder RECONSTRUIRLO en caliente al activar/desactivar
        # un complemento (sin reiniciar la app).
        menu_rss.AppendSeparator()
        self._submenu_complementos = wx.Menu()
        self._poblar_submenu_complementos()
        menu_rss.AppendSubMenu(self._submenu_complementos, _("Complementos"))

        if servicio_habilitado("rss"):
            menubar.Append(menu_rss, _("&RSS"))

        # --- MENÚ TELEGRAM ---
        menu_tg = wx.Menu()

        item_tg_perfil = menu_tg.Append(wx.ID_ANY, _("Mi perfil..."), _("Ver y editar tu perfil de Telegram"))
        self.Bind(wx.EVT_MENU, self._on_tg_perfil, item_tg_perfil)

        item_tg_sesiones = menu_tg.Append(wx.ID_ANY, _("Sesiones activas..."), _("Ver y cerrar sesiones de Telegram"))
        self.Bind(wx.EVT_MENU, self._on_tg_sesiones, item_tg_sesiones)

        item_tg_buscar_chats = menu_tg.Append(wx.ID_ANY, _("Buscar usuarios, grupos y canales..."), _("Buscar personas, grupos, supergrupos y canales públicos por nombre o @usuario"))
        self.Bind(wx.EVT_MENU, self._on_tg_buscar_chats, item_tg_buscar_chats)

        item_tg_buscar = menu_tg.Append(wx.ID_ANY, _("Búsqueda global de mensajes..."), _("Buscar mensajes en todos los chats"))
        self.Bind(wx.EVT_MENU, self._on_tg_buscar_global, item_tg_buscar)

        item_tg_buscar_canales = menu_tg.Append(wx.ID_ANY, _("Buscar publicaciones de canales..."),
                                                _("Buscar por hashtag publicaciones públicas de canales de todo Telegram, aunque no estés suscrito"))
        self.Bind(wx.EVT_MENU, self._on_tg_buscar_canales, item_tg_buscar_canales)

        item_tg_guardados = menu_tg.Append(wx.ID_ANY, _("Mensajes guardados..."), _("Abrir tu chat de Mensajes guardados"))
        self.Bind(wx.EVT_MENU, self._on_tg_guardados, item_tg_guardados)

        item_tg_priv = menu_tg.Append(wx.ID_ANY, _("Privacidad..."), _("Ajustar quién ve tu información"))
        self.Bind(wx.EVT_MENU, self._on_tg_privacidad, item_tg_priv)

        item_tg_priv_glob = menu_tg.Append(wx.ID_ANY, _("Privacidad global..."), _("Archivar/silenciar chats nuevos, ocultar la hora de lectura, etc."))
        self.Bind(wx.EVT_MENU, self._on_tg_privacidad_global, item_tg_priv_glob)

        item_tg_cuenta = menu_tg.Append(wx.ID_ANY, _("Ajustes de cuenta..."), _("Autodestrucción por inactividad, cierre de sesiones, estado en línea y cambio de número"))
        self.Bind(wx.EVT_MENU, self._on_tg_ajustes_cuenta, item_tg_cuenta)

        item_tg_block = menu_tg.Append(wx.ID_ANY, _("Usuarios bloqueados..."), _("Ver y desbloquear usuarios"))
        self.Bind(wx.EVT_MENU, self._on_tg_bloqueados, item_tg_block)

        item_tg_carpetas = menu_tg.Append(wx.ID_ANY, _("Carpetas..."), _("Crear, editar, reordenar o compartir carpetas de chats"))
        self.Bind(wx.EVT_MENU, self._on_tg_carpetas, item_tg_carpetas)

        item_tg_frecuentes = menu_tg.Append(wx.ID_ANY, _("Chats frecuentes..."), _("Tus chats más usados por categoría (personas, bots, grupos, canales)"))
        self.Bind(wx.EVT_MENU, self._on_tg_frecuentes, item_tg_frecuentes)

        item_tg_resumen = menu_tg.Append(wx.ID_ANY, _("Resumen de Telegram"), _("Número total de chats y de contactos de tu cuenta"))
        self.Bind(wx.EVT_MENU, self._on_tg_resumen, item_tg_resumen)

        item_tg_historias = menu_tg.Append(wx.ID_ANY, _("Historias..."), _("Ver, publicar, repostear y gestionar tus historias"))
        self.Bind(wx.EVT_MENU, self._on_tg_historias, item_tg_historias)

        # "Mis bots..." oculto del menú por ahora (sin utilidad clara). El diálogo y el handler siguen
        # en el código (TelegramMisBotsDialog / _on_tg_mis_bots) para reactivarlo cuando se quiera.
        # item_tg_bots = menu_tg.Append(wx.ID_ANY, _("Mis bots..."), _("Crear y gestionar los bots que posees (nombre y descripciones)"))
        # self.Bind(wx.EVT_MENU, self._on_tg_mis_bots, item_tg_bots)

        item_tg_2fa = menu_tg.Append(wx.ID_ANY, _("Verificación en dos pasos..."), _("Activar, cambiar o quitar la contraseña en la nube"))
        self.Bind(wx.EVT_MENU, self._on_tg_2fa, item_tg_2fa)

        menu_tg.AppendSeparator()

        item_tg_unir = menu_tg.Append(wx.ID_ANY, _("Unirse a grupo/canal por enlace..."), _("Unirse mediante enlace de invitación o @usuario"))
        self.Bind(wx.EVT_MENU, self._on_tg_unirse, item_tg_unir)

        item_tg_grupo = menu_tg.Append(wx.ID_ANY, _("Crear grupo..."), _("Crear un grupo nuevo"))
        self.Bind(wx.EVT_MENU, self._on_tg_crear_grupo, item_tg_grupo)

        item_tg_supergrupo = menu_tg.Append(wx.ID_ANY, _("Crear supergrupo..."), _("Crear un supergrupo (administración avanzada, opcionalmente con temas/foros)"))
        self.Bind(wx.EVT_MENU, self._on_tg_crear_supergrupo, item_tg_supergrupo)

        item_tg_canal = menu_tg.Append(wx.ID_ANY, _("Crear canal..."), _("Crear un canal nuevo"))
        self.Bind(wx.EVT_MENU, self._on_tg_crear_canal, item_tg_canal)

        menu_tg.AppendSeparator()

        item_tg_drive = menu_tg.Append(wx.ID_ANY, _("Telegram Drive (tu nube personal)..."),
                                       _("Explorador de archivos que guarda tus documentos en un canal privado: subir, descargar, carpetas, papelera..."))
        self.Bind(wx.EVT_MENU, self._on_tg_drive, item_tg_drive)

        if servicio_habilitado("telegram"):
            menubar.Append(menu_tg, _("&Telegram"))

        # --- MENÚ X (TWITTER) [solo si el cliente está disponible: kill-switch] ---
        try:
            from app.core.feature_flags import x_cliente_disponible as _x_disp
            _x_ok = _x_disp()
        except Exception:
            _x_ok = False
        if _x_ok and self.x_ctrl is not None:
            menu_x = wx.Menu()
            item_x_post = menu_x.Append(wx.ID_ANY, self._lbl_atajo("pub_nuevo", _("Nuevo post de X...")), _("Componer y publicar un post en X"))
            self.Bind(wx.EVT_MENU, self._disp_nuevo, item_x_post)
            item_x_resp = menu_x.Append(wx.ID_ANY, self._lbl_atajo("pub_responder", _("Responder")), _("Responder a la publicación enfocada"))
            self.Bind(wx.EVT_MENU, self._disp_responder, item_x_resp)
            item_x_cit = menu_x.Append(wx.ID_ANY, self._lbl_atajo("pub_citar", _("Citar")), _("Citar la publicación enfocada"))
            self.Bind(wx.EVT_MENU, self._disp_citar, item_x_cit)
            item_x_prog = menu_x.Append(wx.ID_ANY, _("Programar publicación..."), _("Publicar más tarde, en una fecha y hora"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.programar_publicacion(), item_x_prog)
            item_x_like = menu_x.Append(wx.ID_ANY, self._lbl_atajo("pub_boost_like", _("Me gusta")), _("Dar o quitar me gusta a la publicación enfocada"))
            self.Bind(wx.EVT_MENU, self._disp_boost_like, item_x_like)
            item_x_mas = menu_x.Append(wx.ID_ANY, self._lbl_atajo("pub_cargar_mas", _("Cargar publicaciones más antiguas")), _("Descargar una página de tweets más antiguos y añadirlos al final"))
            self.Bind(wx.EVT_MENU, self._on_cargar_mas_toots, item_x_mas)
            menu_x.AppendSeparator()
            item_x_buscar = menu_x.Append(wx.ID_ANY, _("Guardar búsqueda como buffer..."), _("Crear un buffer con una búsqueda de X"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.buscar(), item_x_buscar)
            item_x_cron = menu_x.Append(wx.ID_ANY, _("Abrir cronología de un usuario..."), _("Escribe un usuario de X y abre sus publicaciones como buffer (todas, con respuestas o solo multimedia)"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.abrir_cronologia_usuario(), item_x_cron)
            item_x_cuentas = menu_x.Append(wx.ID_ANY, _("Buscar cuentas..."), _("Buscar cuentas de X por nombre o @usuario y actuar sobre ellas"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.buscar_usuarios(), item_x_cuentas)
            item_x_comus = menu_x.Append(wx.ID_ANY, _("Buscar comunidades..."), _("Buscar comunidades de X, unirte o abrir sus publicaciones como buffer"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.buscar_comunidades(), item_x_comus)
            item_x_trends = menu_x.Append(wx.ID_ANY, _("Tendencias..."), _("Ver las tendencias actuales de X"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.tendencias(), item_x_trends)
            item_x_trends_lugar = menu_x.Append(wx.ID_ANY, _("Tendencias de un país o ciudad..."), _("Ver las tendencias de un país o una ciudad"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.tendencias_de_un_lugar(), item_x_trends_lugar)
            item_x_listas = menu_x.Append(wx.ID_ANY, _("Gestionar listas..."), _("Crear, renombrar o borrar tus listas de X y gestionar sus miembros"))
            self.Bind(wx.EVT_MENU, lambda e: self.x_ctrl.gestionar_listas(), item_x_listas)
            item_x_perfil = menu_x.Append(wx.ID_ANY, _("Ver mi perfil de X..."), _("Ver los datos de tu perfil"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_x_mi_perfil(), item_x_perfil)
            menu_x.AppendSeparator()
            item_x_aviso = menu_x.Append(wx.ID_ANY, _("Acerca del cliente de X (riesgos)..."), _("Recordatorio de que el cliente de X es no oficial y sus riesgos"))
            self.Bind(wx.EVT_MENU, lambda e: self._on_x_aviso(), item_x_aviso)
            menubar.Append(menu_x, _("&X (Twitter)"))

        # --- MENÚ IRC ---
        if servicio_habilitado("irc") and getattr(self, "irc_ctrl", None) is not None:
            self._crear_menu_irc(menubar)

        # --- MENÚ MATRIX ---
        if servicio_habilitado("matrix") and getattr(self, "matrix_ctrl", None) is not None:
            self._crear_menu_matrix(menubar)

        # --- MENÚ XMPP ---
        if servicio_habilitado("xmpp") and getattr(self, "xmpp_ctrl", None) is not None:
            self._crear_menu_xmpp(menubar)

        # --- MENÚ INTELIGENCIA ARTIFICIAL ---
        menu_ai = wx.Menu()
        
        item_describir = menu_ai.Append(wx.ID_ANY, self._menu_atajo("ia_describir", _("Describir Imagen con IA")), _("Describir imagen adjunta usando Gemini/Ollama"))
        self.Bind(wx.EVT_MENU, self._on_describir_imagen_ia, item_describir)

        item_ocr = menu_ai.Append(wx.ID_ANY, self._menu_atajo("ia_ocr", _("Extraer texto de imagen (OCR con IA)")), _("Transcribir el texto que aparece en la imagen adjunta usando la IA configurada"))
        self.Bind(wx.EVT_MENU, self._on_ocr_imagen_ia, item_ocr)

        item_ocr_local = menu_ai.Append(wx.ID_ANY, _("Extraer texto de imagen (OCR local, sin IA)"), _("Transcribir el texto de la imagen con el motor de Windows, sin necesidad de clave ni conexión"))
        self.Bind(wx.EVT_MENU, self._on_ocr_local_imagen, item_ocr_local)

        item_resumir = menu_ai.Append(wx.ID_ANY, _("Resumir Entrada con IA"), _("Generar resumen del texto actual con IA"))
        self.Bind(wx.EVT_MENU, self._on_resumir_entrada_ia, item_resumir)

        menu_ai.AppendSeparator()
        item_prompts = menu_ai.Append(wx.ID_ANY, _("Gestor de prompts..."), _("Editar las plantillas de prompts de IA"))
        self.Bind(wx.EVT_MENU, lambda e: self._abrir_dialogo_ai("PromptManagerDialog"), item_prompts)
        item_hist_ia = menu_ai.Append(wx.ID_ANY, _("Historial de IA..."), _("Ver el historial de operaciones de IA"))
        self.Bind(wx.EVT_MENU, lambda e: self._abrir_dialogo_ai("AIHistoryDialog"), item_hist_ia)

        menubar.Append(menu_ai, _("&IA"))
        
        # --- MENÚ TRADUCCIÓN ---
        menu_trans = wx.Menu()
        
        item_traducir_sel = menu_trans.Append(wx.ID_ANY, self._menu_atajo("ia_traducir", _("Traducir Elemento")), _("Traducir post o artículo seleccionado al español"))
        self.Bind(wx.EVT_MENU, self._on_traducir_seleccionado, item_traducir_sel)
        
        item_historial_trans = menu_trans.Append(wx.ID_ANY, _("Historial de Traducciones..."), _("Ver traducciones guardadas en base de datos"))
        self.Bind(wx.EVT_MENU, self._on_ver_historial_traducciones, item_historial_trans)
        
        menubar.Append(menu_trans, _("&Traducción"))
        
        # --- MENÚ HERRAMIENTAS ---
        menu_herr = wx.Menu()
        
        item_reproductor = menu_herr.Append(wx.ID_ANY, _("Abrir Reproductor Multimedia..."), _("Abrir panel de control de podcast/audio"))
        self.Bind(wx.EVT_MENU, self._on_abrir_reproductor, item_reproductor)

        item_radio = menu_herr.Append(wx.ID_ANY, _("Radio por internet..."), _("Buscar y escuchar emisoras de radio de todo el mundo"))
        self.Bind(wx.EVT_MENU, self._on_abrir_radio, item_radio)

        item_reproductor2 = menu_herr.Append(wx.ID_ANY, _("Reproductor de radio, podcast y YouTube..."), _("Controlar la reproducción de radio, podcast y YouTube"))
        self.Bind(wx.EVT_MENU, self._on_abrir_reproductor2, item_reproductor2)

        item_leer_estado = menu_herr.Append(wx.ID_ANY, self._menu_atajo("leer_barra_estado", _("Leer barra de estado")), _("Verbalizar la información de la barra de estado"))
        self.Bind(wx.EVT_MENU, self._on_leer_barra_estado, item_leer_estado)

        item_ver_desc = menu_herr.Append(wx.ID_ANY, self._menu_atajo("ver_descripcion", _("Ver descripción de la multimedia")), _("Mostrar el texto alternativo que el autor puso a la imagen, vídeo o audio de la publicación enfocada"))
        self.Bind(wx.EVT_MENU, self._on_ver_descripcion, item_ver_desc)

        item_buscar_global = menu_herr.Append(wx.ID_ANY, self._menu_atajo("buscar_global", _("Buscar en todo el contenido...")), _("Búsqueda en texto completo en toots y artículos guardados"))
        self.Bind(wx.EVT_MENU, self._on_buscar_global, item_buscar_global)

        item_filtros = menu_herr.Append(wx.ID_ANY, _("Filtros de contenido..."), _("Ocultar o marcar automáticamente publicaciones y artículos por palabra clave"))
        self.Bind(wx.EVT_MENU, self._on_filtros_contenido, item_filtros)

        item_crosspost = menu_herr.Append(wx.ID_ANY, self._menu_atajo("crosspost", _("Publicación cruzada (varias redes)...")), _("Publicar el mismo mensaje en Mastodon, Bluesky y X a la vez"))
        self.Bind(wx.EVT_MENU, self._on_crosspost, item_crosspost)

        item_cola = menu_herr.Append(wx.ID_ANY, _("Borradores y programados de todas las redes..."), _("Ver y gestionar tus borradores y publicaciones programadas de todas las redes en un solo sitio"))
        self.Bind(wx.EVT_MENU, self._on_cola_unificada, item_cola)

        item_notif_centro = menu_herr.Append(wx.ID_ANY, self._menu_atajo("centro_notificaciones", _("Centro de notificaciones (todas las redes)...")), _("Menciones, respuestas, me gusta, seguidores y chats sin leer de todas tus redes en una lista"))
        self.Bind(wx.EVT_MENU, self._on_centro_notificaciones, item_notif_centro)

        item_snippets = menu_herr.Append(wx.ID_ANY, _("Fragmentos de texto..."), _("Crear y gestionar textos reutilizables; insértalos al redactar con Ctrl+Mayús+J"))
        self.Bind(wx.EVT_MENU, self._on_snippets, item_snippets)

        item_ponte_dia = menu_herr.Append(wx.ID_ANY, self._menu_atajo("ponte_al_dia", _("Ponte al día (resumen con IA)...")), _("Resumen con inteligencia artificial de lo que no has leído en todas tus redes"))
        self.Bind(wx.EVT_MENU, self._on_ponte_al_dia, item_ponte_dia)

        item_stats_social = menu_herr.Append(wx.ID_ANY, _("Estadísticas de mis redes..."), _("Publicaciones por día, horas más activas e interacciones, con datos locales"))
        self.Bind(wx.EVT_MENU, self._on_estadisticas_sociales, item_stats_social)

        item_backup = menu_herr.Append(wx.ID_ANY, _("Copias de seguridad..."), _("Crear, restaurar, exportar o importar copias de seguridad"))
        self.Bind(wx.EVT_MENU, self._on_copias_seguridad, item_backup)

        item_migrar = menu_herr.Append(wx.ID_ANY, _("Migrar a otro ordenador..."), _("Exportar o importar toda la instalación (cuentas, configuración y base de datos) para llevarla a otro equipo"))
        self.Bind(wx.EVT_MENU, self._on_migrar, item_migrar)

        item_limpiar_datos = menu_herr.Append(wx.ID_ANY, _("Borrar datos / liberar espacio..."), _("Eliminar publicaciones, artículos o mensajes antiguos de la base de datos"))
        self.Bind(wx.EVT_MENU, self._on_borrar_datos, item_limpiar_datos)

        item_buffers = menu_herr.Append(wx.ID_ANY, _("Mostrar u ocultar buffers..."), _("Elegir qué buffers aparecen en el árbol de cada red"))
        self.Bind(wx.EVT_MENU, self._on_gestionar_buffers, item_buffers)

        item_plantillas = menu_herr.Append(wx.ID_ANY, _("Editor de plantillas de lectura..."), _("Configurar cómo lee el lector de pantalla cada elemento"))
        self.Bind(wx.EVT_MENU, self._on_editor_plantillas, item_plantillas)

        menu_herr.AppendSeparator()

        item_logs = menu_herr.Append(wx.ID_ANY, _("Visor de Registros (Logs)..."), _("Inspeccionar logs internos del sistema"))
        self.Bind(wx.EVT_MENU, self._on_abrir_visor_logs, item_logs)
        
        item_preferencias = menu_herr.Append(wx.ID_PREFERENCES, self._menu_atajo("preferencias", _("Preferencias...")), _("Configurar SocialRSS Accessible Suite"))
        self.Bind(wx.EVT_MENU, self._on_preferencias, item_preferencias)
        
        menubar.Append(menu_herr, _("H&erramientas"))

        # --- MENÚ AYUDA ---
        menu_ayuda = wx.Menu()
        
        item_manual = menu_ayuda.Append(wx.ID_HELP, _("Manual del Usuario"), _("Abrir el manual de usuario en el navegador"))
        self.Bind(wx.EVT_MENU, self._on_ver_manual, item_manual)

        item_teclas = menu_ayuda.Append(wx.ID_ANY, _("Teclas de la aplicación (lista actual)"),
                                        _("Ver la lista de teclas con tus combinaciones actuales (incluidas las personalizadas)"))
        self.Bind(wx.EVT_MENU, self._on_ver_teclas, item_teclas)

        item_teclas_inv = menu_ayuda.Append(wx.ID_ANY, _("Teclas de la interfaz invisible (lista actual)"),
                                            _("Ver la lista de teclas globales de la interfaz invisible con tus combinaciones actuales"))
        self.Bind(wx.EVT_MENU, self._on_ver_teclas_invisibles, item_teclas_inv)

        item_teclas_doc = menu_ayuda.Append(wx.ID_ANY, _("Documento completo de teclas"),
                                            _("Abrir en el navegador el documento de teclas en el idioma activo"))
        self.Bind(wx.EVT_MENU, self._on_ver_teclas_documento, item_teclas_doc)

        item_changelog = menu_ayuda.Append(wx.ID_ANY, _("Último changelog"),
                                           _("Ver las novedades de esta versión en un cuadro de solo lectura, como al actualizar"))
        self.Bind(wx.EVT_MENU, self._on_ver_changelog, item_changelog)

        item_prueba_sonidos = menu_ayuda.Append(wx.ID_ANY, _("Prueba de sonidos identificativos..."),
                                                _("Escuchar e identificar los sonidos de la aplicación"))
        self.Bind(wx.EVT_MENU, self._on_prueba_sonidos, item_prueba_sonidos)

        menu_ayuda.AppendSeparator()

        item_actualizar = menu_ayuda.Append(wx.ID_ANY, _("Buscar actualizaciones..."),
                                            _("Comprobar si hay una versión nueva e instalarla"))
        self.Bind(wx.EVT_MENU, self._on_buscar_actualizaciones, item_actualizar)

        item_cafe = menu_ayuda.Append(wx.ID_ANY, _("Invítame a un café si te gusta mi trabajo"),
                                      _("Abrir la página de donaciones de PayPal en el navegador"))
        self.Bind(wx.EVT_MENU, self._on_invitar_cafe, item_cafe)

        item_acerca = menu_ayuda.Append(wx.ID_ABOUT, _("Acerca de..."), _("Ver créditos e información del programa"))
        self.Bind(wx.EVT_MENU, self._on_acerca_de, item_acerca)

        menubar.Append(menu_ayuda, _("A&yuda"))

        # Retoques finales de la barra, en este ORDEN:
        #  1) la tecla de cada acción, visible en su item (respeta las personalizadas);
        #  2) el submenú de acciones comunes en el menú de cada red;
        #  3) la organización OPCIONAL en subcategorías (Preferencias -> General), que respeta
        #     los submenús ya existentes y entiende las etiquetas que llevan la tecla detrás.
        # Como _crear_menus se reconstruye al Aceptar Preferencias, todo esto es en caliente.
        try:
            from .menu_teclas import anotar_menubar, anadir_acciones_comunes
            anotar_menubar(menubar)
            anadir_acciones_comunes(menubar, self)
        except Exception as e:
            log.error(f"No se pudieron preparar las teclas y acciones comunes de los menús: {e}")
        try:
            from .menu_organizador import organizar_menubar
            organizar_menubar(menubar)
        except Exception as e:
            log.error(f"No se pudo organizar la barra de menús en subcategorías: {e}")

        self.SetMenuBar(menubar)

    def _registrar_atajos_teclado(self):
        # Crear IDs para atajos globales
        self.id_actualizar = wx.NewIdRef()
        # Ayuda contextual (Ctrl+H): id PROPIO (no wx.ID_HELP, que abre el Manual). Así Ctrl+H
        # describe el control enfocado en vez de abrir el manual.
        self.id_ayuda_ctx = wx.NewIdRef()
        # Alt+1..9 activan el servicio según su POSICIÓN en el árbol (orden actual,
        # reordenable por el usuario), no un servicio fijo. Hay hasta 9 posibles servicios
        # (Bandeja, Mastodon, Bluesky, Telegram, RSS, X y margen para más).
        self.ids_serv = [wx.NewIdRef() for _ in range(9)]
        # Acciones rápidas sobre el artículo RSS enfocado
        self.id_rss_destacar = wx.NewIdRef()
        self.id_rss_leer_tarde = wx.NewIdRef()
        self.id_rss_toggle_read = wx.NewIdRef()
        self.id_rss_copiar_titulo = wx.NewIdRef()
        self.id_rss_ver_visor = wx.NewIdRef()
        self.id_rss_sig_no_leido = wx.NewIdRef()
        self.id_rss_ant_no_leido = wx.NewIdRef()
        # Más acciones sobre el elemento (sin tecla de fábrica; se asignan en el editor).
        self.ids_mas_acciones = {acc: wx.NewIdRef() for acc in self._ACCIONES_MAS}
        # Cambio rápido de cuenta
        self.id_cuenta_sig = wx.NewIdRef()
        self.id_cuenta_ant = wx.NewIdRef()
        # Atajos UNIFICADOS de publicación (Ctrl+N, Ctrl+R...). Antes vivían como aceleradores
        # del MENÚ Mastodon; se han movido aquí (a la tabla global) para que sigan funcionando
        # aunque el servicio Mastodon esté desactivado y su menú no aparezca. Despachan según el
        # servicio activo en ese momento (Mastodon, Bluesky o X).
        self.id_u_nuevo = wx.NewIdRef()
        self.id_u_responder = wx.NewIdRef()
        self.id_u_citar = wx.NewIdRef()
        self.id_u_boost = wx.NewIdRef()
        self.id_u_mas = wx.NewIdRef()
        self.id_u_ver_conv = wx.NewIdRef()
        self.id_u_ver_texto = wx.NewIdRef()
        self.id_u_tl_usuario = wx.NewIdRef()
        self.id_u_hilo = wx.NewIdRef()

        # Enlazar eventos de comando
        self.Bind(wx.EVT_MENU, self._on_actualizar_buffer_actual, id=self.id_actualizar.GetId())
        self.Bind(wx.EVT_MENU, self._on_ayuda_contextual, id=self.id_ayuda_ctx.GetId())
        self.Bind(wx.EVT_MENU, self._disp_nuevo, id=self.id_u_nuevo.GetId())
        self.Bind(wx.EVT_MENU, self._disp_responder, id=self.id_u_responder.GetId())
        self.Bind(wx.EVT_MENU, self._disp_citar, id=self.id_u_citar.GetId())
        self.Bind(wx.EVT_MENU, self._disp_boost_like, id=self.id_u_boost.GetId())
        self.Bind(wx.EVT_MENU, self._on_cargar_mas_toots, id=self.id_u_mas.GetId())
        self.Bind(wx.EVT_MENU, self._disp_ver_conversacion, id=self.id_u_ver_conv.GetId())
        self.Bind(wx.EVT_MENU, self._disp_ver_texto_plano, id=self.id_u_ver_texto.GetId())
        self.Bind(wx.EVT_MENU, self._on_abrir_timeline_usuario, id=self.id_u_tl_usuario.GetId())
        self.Bind(wx.EVT_MENU, self._disp_nuevo_hilo, id=self.id_u_hilo.GetId())
        # Ctrl+Shift+1..9 (ir al buffer N de la cuenta enfocada) y Ctrl+Shift+L (diálogo con
        # todos los buffers) se gestionan con un CHAR_HOOK en vez de la tabla de aceleradores.
        # Se usa Ctrl+Shift+L (no Ctrl+Shift+0) porque Windows captura Ctrl+Shift+0 para cambiar
        # la distribución de teclado y la app no llegaba a recibirlo.
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook_buffers)
        for _i, _idref in enumerate(self.ids_serv, start=1):
            self.Bind(wx.EVT_MENU,
                      lambda evt, p=_i: self._activar_servicio_por_posicion(p),
                      id=_idref.GetId())
        # Ctrl+D: en Mastodon abre "Ver publicación"; en RSS destaca el artículo (despachador único).
        self.Bind(wx.EVT_MENU, self._disp_ver_publicacion, id=self.id_rss_destacar.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._atajo_rss_articulo("leer_tarde"), id=self.id_rss_leer_tarde.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._atajo_rss_articulo("toggle_read"), id=self.id_rss_toggle_read.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._on_copiar_titulo_rss(), id=self.id_rss_copiar_titulo.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._atajo_rss_ver_visor(), id=self.id_rss_ver_visor.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._atajo_rss_no_leido(1), id=self.id_rss_sig_no_leido.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._atajo_rss_no_leido(-1), id=self.id_rss_ant_no_leido.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._cambiar_cuenta(1), id=self.id_cuenta_sig.GetId())
        self.Bind(wx.EVT_MENU, lambda evt: self._cambiar_cuenta(-1), id=self.id_cuenta_ant.GetId())
        for _acc, _idref in self.ids_mas_acciones.items():
            self.Bind(wx.EVT_MENU, lambda evt, a=_acc: self._ejecutar_accion_mas(a), id=_idref.GetId())

        # La tabla de aceleradores se construye desde el CATÁLOGO de shortcut_manager (única fuente de
        # verdad), para que el editor de teclas pueda regenerarla en caliente y todo quede coherente.
        self._construir_tabla_aceleradores()

    # "Más acciones" del editor de teclas -> método que la ejecuta. Reutilizan los manejadores de
    # la interfaz invisible, que ya despachan según la red activa y funcionan igual con la
    # ventana visible.
    _ACCIONES_MAS = {
        "pub_impulsar": "_inv_boost",
        "pub_abrir_navegador": "_inv_open_browser",
        "pub_copiar_enlace": "_inv_copiar",
        "pub_reproducir": "_inv_audio",
        "pub_seguir_autor": "_inv_seguir",
        "pub_menu_contextual": "_inv_context_menu",
    }

    def _ejecutar_accion_mas(self, accion):
        metodo = getattr(self, self._ACCIONES_MAS.get(accion, ""), None)
        if metodo is not None:
            metodo()

    # Mapa accion del catálogo -> IdRef del comando ya creado y enlazado arriba. Solo las acciones que
    # viven en la TABLA global (las de menú-barra van por su \t; dialogo_buffers por el char-hook).
    def _mapa_acciones_aceleradores(self):
        return {
            "actualizar_buffer": self.id_actualizar,
            "pub_nuevo": self.id_u_nuevo,
            "pub_responder": self.id_u_responder,
            "pub_citar": self.id_u_citar,
            "pub_boost_like": self.id_u_boost,
            "pub_cargar_mas": self.id_u_mas,
            "pub_ver_conv": self.id_u_ver_conv,
            "pub_ver_texto": self.id_u_ver_texto,
            "pub_tl_usuario": self.id_u_tl_usuario,
            "pub_hilo": self.id_u_hilo,
            "pub_ver": self.id_rss_destacar,
            "rss_leer_tarde": self.id_rss_leer_tarde,
            "rss_toggle_read": self.id_rss_toggle_read,
            "rss_copiar_tit": self.id_rss_copiar_titulo,
            "rss_ver_visor": self.id_rss_ver_visor,
            "rss_sig_no_leido": self.id_rss_sig_no_leido,
            "rss_ant_no_leido": self.id_rss_ant_no_leido,
            "cuenta_siguiente": self.id_cuenta_sig,
            "cuenta_anterior": self.id_cuenta_ant,
            **getattr(self, "ids_mas_acciones", {}),
        }

    def _construir_tabla_aceleradores(self):
        """(Re)construye y asigna la tabla de aceleradores global desde shortcut_manager."""
        # Navegación por POSICIÓN (Alt+1..9): fija, no editable.
        entradas = [
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord(str(n)), self.ids_serv[n - 1].GetId())
            for n in range(1, 10)
        ]
        # Ayuda contextual (Ctrl+H): describe el control enfocado (id propio, NO el del Manual).
        tpl = shortcut_manager.parsed_to_wx_accelerator("ayuda_contexto", self.id_ayuda_ctx.GetId())
        if tpl:
            entradas.append(wx.AcceleratorEntry(*tpl))
        # Acciones editables del catálogo que viven en la tabla global.
        for accion, idref in self._mapa_acciones_aceleradores().items():
            tpl = shortcut_manager.parsed_to_wx_accelerator(accion, idref.GetId())
            if tpl:
                entradas.append(wx.AcceleratorEntry(*tpl))
        # (Ctrl+Shift+0..9 NO van aquí: se gestionan en _on_char_hook_buffers, más fiable.)
        self.SetAcceleratorTable(wx.AcceleratorTable(entradas))

    def _reaplicar_atajos(self):
        """Aplica EN CALIENTE los cambios de teclas tras usar el editor: reconstruye la tabla de
        aceleradores global y la barra de menús (etiquetas de referencia y aceleradores \t)."""
        try:
            self._construir_tabla_aceleradores()
        except Exception as e:
            log.error(f"No se pudo reconstruir la tabla de aceleradores: {e}")
        try:
            self._crear_menus()
        except Exception as e:
            log.error(f"No se pudo reconstruir la barra de menús con los nuevos atajos: {e}")

    def _activar_servicio_por_posicion(self, posicion: int):
        """Activa el servicio que ocupa esa POSICIÓN (1-based) en el orden actual del
        árbol. Como el orden es reordenable por el usuario (Ctrl+Flecha sobre el nodo
        raíz del servicio), Alt+1..9 siguen siempre el orden vigente en ese momento."""
        orden = [s for s in self._orden_servicios() if self._servicio_tiene_nodo(s)]
        idx = posicion - 1
        if 0 <= idx < len(orden):
            self._activar_servicio_en_navegacion(orden[idx])
        else:
            speak(_("No hay un servicio en la posición {0}.").format(posicion), interrupt=True)

    # --- Navegación rápida por los BUFFERS de la cuenta enfocada (Ctrl+Shift+1..9 y 0) ---
    def _on_char_hook_buffers(self, event):
        """Captura Ctrl+Shift+1..9 (ir al buffer N de la cuenta enfocada) y Ctrl+Shift+L
        (diálogo con todos los buffers). Se hace aquí, antes que la tabla de aceleradores,
        porque así funciona de forma fiable. (Ctrl+Shift+0 lo capturaba Windows para cambiar la
        distribución de teclado, por eso el diálogo usa Ctrl+Shift+L.) Cualquier otra
        combinación sigue su curso normal (event.Skip)."""
        # Diálogo de buffers de la cuenta: tecla personalizable (catálogo "dialogo_buffers").
        if self._combo_coincide(event, "dialogo_buffers"):
            self._mostrar_dialogo_buffers_cuenta()
            return
        # Ctrl+Shift+1..9: ir al buffer N de la cuenta enfocada (navegación por posición, fija).
        if event.GetModifiers() == (wx.MOD_CONTROL | wx.MOD_SHIFT):
            key = event.GetKeyCode()
            if ord('1') <= key <= ord('9'):
                self._ir_a_buffer_cuenta(key - ord('0'))
                return

        # BANDEJA DE ENTRADA: los atajos sobre la publicación enfocada usan los de la RED de esa fila
        # (Ctrl+Shift+E de RSS, Ctrl+R de Mastodon, etc.), como el menú contextual. Se intercepta
        # AQUÍ y se ejecuta el comando de FORMA SÍNCRONA (fijar contexto -> ejecutar -> restaurar),
        # en vez de delegar en la tabla de aceleradores con un CallAfter, porque el restaurado
        # diferido se ejecutaba ANTES que el handler y el contexto no llegaba fijado.
        if (getattr(self, "_modo_lista", "") == "bandeja"
                and wx.Window.FindFocus() is self.lista_items):
            # Atajos que actúan SOBRE la publicación enfocada -> método que los ejecuta. Se fija el
            # contexto de la RED de la fila, se llama al método de forma SÍNCRONA (para que vea el
            # contexto fijado) y se restaura; luego se consume el evento (no Skip) para que la tabla
            # de aceleradores/menús no lo ejecute otra vez. Cada método ya despacha según la red
            # activa; si esa red no soporta la acción, lo dice (igual que en su buffer normal).
            acciones = {
                (wx.MOD_CONTROL, ord('R')): lambda: self._disp_responder(None),
                (wx.MOD_CONTROL, ord('B')): lambda: self._disp_boost_like(None),
                (wx.MOD_CONTROL, ord('T')): lambda: self._on_traducir_seleccionado(None),
                (wx.MOD_CONTROL, ord('I')): lambda: self._on_describir_imagen_ia(None),
                (wx.MOD_CONTROL, ord('D')): lambda: self._disp_ver_publicacion(None),
                (wx.MOD_CONTROL, ord('L')): lambda: self._atajo_rss_articulo("leer_tarde"),
                (wx.MOD_CONTROL, ord('M')): lambda: self._atajo_rss_articulo("toggle_read"),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('U')): lambda: self._disp_citar(None),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('V')): lambda: self._disp_ver_conversacion(None),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('T')): lambda: self._disp_ver_texto_plano(None),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('G')): lambda: self._on_abrir_timeline_usuario(None),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('C')): lambda: self._on_copiar_titulo_rss(),
                (wx.MOD_CONTROL | wx.MOD_SHIFT, ord('E')): lambda: self._atajo_rss_ver_visor(),
            }
            accion = acciones.get((event.GetModifiers(), event.GetKeyCode()))
            if accion is None:
                # "Más acciones" con la tecla que el usuario les haya asignado.
                for _acc in self._ACCIONES_MAS:
                    if self._combo_coincide(event, _acc):
                        accion = (lambda a=_acc: self._ejecutar_accion_mas(a))
                        break
            if accion is not None and self.lista_items.GetFocusedItem() >= 0:
                guardado = self._fijar_contexto_bandeja(self.lista_items.GetFocusedItem())
                if guardado is not None:
                    try:
                        accion()
                    finally:
                        self._restaurar_contexto_bandeja(guardado)
                    return  # consumido
        event.Skip()
