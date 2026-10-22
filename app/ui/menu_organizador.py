# -*- coding: utf-8 -*-
"""Organización OPCIONAL de los menús en subcategorías (Preferencias -> General).

Cuando el usuario activa "Organizar los menús en subcategorías", los menús con muchos
items (los de la barra de menús y los menús contextuales grandes) se reagrupan en
submenús con nombres descriptivos, para encontrar antes cada acción. Con la opción
desactivada (por defecto) los menús quedan EXACTAMENTE como siempre.

Cómo funciona: los wx.MenuItem NO se recrean, solo se MUEVEN (wx.Menu.Remove conserva
el item, su id y su binding; los eventos de un submenú se propagan igual al frame).
La categoría de cada item se decide por su ETIQUETA: las claves de los esquemas son las
mismas cadenas literales del código y se comparan TRADUCIDAS con gettext, así que la
clasificación funciona también en otros idiomas del catálogo. Un item cuya etiqueta no
esté en ningún esquema se queda en el nivel superior, después de los submenús; los
items que YA tienen submenú propio (Administración, Complementos, Carpetas...) y los
de tipo radio no se tocan nunca.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from app.core.config_manager import config

log = logging.getLogger(__name__)


def activo() -> bool:
    """True si el usuario activó la organización de menús en subcategorías."""
    return bool(config.get("general", "menus_categorizados", False))


def _sin_amp(texto: str) -> str:
    return (texto or "").replace("&", "")


def _match_clave(lbl: str, clave: str) -> bool:
    """True si la etiqueta visible 'lbl' (ya sin & ni acelerador \\t) corresponde a la
    clave del esquema. Se acepta el sufijo " (Ctrl+X)" que añade _lbl_atajo, y las
    claves con {0} (etiquetas formateadas) casan por su prefijo."""
    c = _(clave)
    if "{0}" in c:
        return lbl.startswith(_sin_amp(c.split("{0}")[0]))
    c = _sin_amp(c)
    return lbl == c or lbl.startswith(c + " (")


def _limpiar_separadores(menu: wx.Menu) -> None:
    """Quita separadores duplicados, iniciales y finales que quedan al mover items."""
    anterior_sep = True  # así también se elimina un separador en la primera posición
    for it in list(menu.GetMenuItems()):
        if it.IsSeparator():
            if anterior_sep:
                menu.DestroyItem(it)
            else:
                anterior_sep = True
        else:
            anterior_sep = False
    items = list(menu.GetMenuItems())
    while items and items[-1].IsSeparator():
        menu.DestroyItem(items[-1])
        items.pop()


def _organizar_menu(menu: wx.Menu, esquema, minimo: int = 8) -> bool:
    """Reagrupa los items de primer nivel de 'menu' en submenús según 'esquema'
    (lista de (título_submenú, [claves])). Solo actúa si el menú tiene al menos
    'minimo' items reales; una categoría solo se crea si reúne 2 o más items
    (un submenú de un solo elemento estorba más de lo que ordena)."""
    items = list(menu.GetMenuItems())
    reales = [it for it in items if not it.IsSeparator()]
    if len(reales) < minimo:
        return False

    # Asignar categoría a cada item por su etiqueta (primer esquema que case gana).
    pares = []  # (wx.MenuItem, índice de categoría) en el orden original del menú
    for it in items:
        if it.IsSeparator() or it.GetSubMenu() is not None or it.GetKind() == wx.ITEM_RADIO:
            continue
        lbl = it.GetItemLabelText()  # sin mnemónicos (&) ni acelerador (\t)
        for ci, (_titulo, claves) in enumerate(esquema):
            if any(_match_clave(lbl, c) for c in claves):
                pares.append((it, ci))
                break

    por_cat = {}
    for it, ci in pares:
        por_cat.setdefault(ci, []).append(it)
    por_cat = {ci: lst for ci, lst in por_cat.items() if len(lst) >= 2}
    if not por_cat:
        return False

    # Mover los items a su submenú (conservando el estado de los items marcables).
    submenus = []
    for ci, (titulo, _claves) in enumerate(esquema):
        lst = por_cat.get(ci)
        if not lst:
            continue
        sub = wx.Menu()
        for it in lst:
            marcado = (it.GetKind() == wx.ITEM_CHECK and it.IsChecked())
            menu.Remove(it)
            sub.Append(it)
            if marcado:
                it.Check(True)
        submenus.append((titulo, sub))

    _limpiar_separadores(menu)
    # Los submenús van al principio (en el orden del esquema); lo no clasificado queda
    # debajo, tras un separador.
    if menu.GetMenuItemCount() > 0:
        menu.InsertSeparator(0)
    for pos, (titulo, sub) in enumerate(submenus):
        item_sub = wx.MenuItem(menu, wx.ID_ANY, _(titulo), "", wx.ITEM_NORMAL, sub)
        menu.Insert(pos, item_sub)
    return True


# ---------------------------------------------------------------------------
# ESQUEMAS DE LA BARRA DE MENÚS. Clave del dict = título del menú tal como está en el
# código (con su &); cada esquema es una lista de (título del submenú, [etiquetas]).
# Las etiquetas son las cadenas LITERALES con las que se crean los items.
# ---------------------------------------------------------------------------

ESQUEMAS_BARRA = {
    "&Mastodon": [
        ("Publicar y responder", [
            "Nuevo Toot o post...", "Responder...", "Citar...", "Me gusta / favorito",
            "Cargar más toots antiguos", "Nuevo Hilo...", "Ver Hilos Guardados...",
            "Publicaciones programadas...", "Borradores...",
        ]),
        ("Ver y buscar", [
            "Ver conversación", "Ver el texto de la publicación",
            "Abrir cronología de un usuario...", "Buscar usuarios...",
            "Ver perfil de una cuenta...", "Guardar búsqueda como buffer...",
        ]),
        ("Mensajes y notificaciones", [
            "Conversaciones de mensajes directos...", "Notificaciones agrupadas...",
            "Política de notificaciones...", "Solicitudes de notificación...",
            "Notificaciones sin leer (contador)", "Limpiar todas las notificaciones...",
        ]),
        ("Mi perfil y seguidores", [
            "Editar mi perfil de Mastodon...", "Hashtags destacados de mi perfil...",
            "Cuentas destacadas en mi perfil...", "Destacar un hashtag en mi perfil...",
            "Solicitudes de seguimiento...", "Novedades de seguidores...",
        ]),
        ("Hashtags", [
            "Seguir un hashtag...", "Hashtags que sigues...",
        ]),
        ("Descubrir", [
            "Descubrir: hashtags en tendencia...", "Descubrir: enlaces en tendencia...",
            "Descubrir: directorio de perfiles...", "Descubrir: cuentas sugeridas...",
        ]),
        ("Listas y filtros", [
            "Listas de Mastodon...", "Mis colecciones...", "Colecciones en las que apareces...",
            "Filtros del servidor...", "Dominios bloqueados...",
        ]),
        ("Tu instancia", [
            "Acerca de esta instancia...", "Capacidades de tu instancia...",
        ]),
    ],
    "&Bluesky": [
        ("Publicar y responder", [
            "Nuevo post de Bluesky...", "Nuevo hilo de Bluesky...", "Responder", "Citar",
            "Me gusta", "Cargar más publicaciones", "Borradores de Bluesky...",
            "Salida pendiente...",
        ]),
        ("Buscar y descubrir", [
            "Guardar búsqueda como buffer...", "Buscar cuentas...",
            "Ver perfil de una cuenta...", "Cuentas sugeridas...", "Feeds populares...",
            "Feeds sugeridos...",
        ]),
        ("Feeds y listas", [
            "Gestionar mis feeds...", "Crear lista...", "Gestionar miembros de listas...",
            "Crear paquete de bienvenida (starter pack)...",
        ]),
        ("Chats y grupos", [
            "Crear un grupo de chat...", "Entrar en un grupo con un enlace...",
        ]),
        ("Mi cuenta", [
            "Mi perfil...", "Contraseñas de aplicación...",
        ]),
        ("Moderación", [
            "Palabras silenciadas...", "Servicios de etiquetas...",
            "Preferencias de moderación...", "Publicaciones ocultadas...",
            "Preferencias del feed Siguiendo y de los hilos...",
        ]),
    ],
    "&Reddit": [
        ("Publicar y votar", [
            "Nueva publicación de Reddit...", "Responder", "Voto positivo",
            "Cargar más publicaciones", "Borradores...",
        ]),
        ("Navegar y buscar", [
            "Ir a un subreddit...", "Buscar publicaciones...", "Buscar subreddits...",
            "Buscar usuarios...",
        ]),
        ("Mensajes y directos", [
            "Enviar mensaje privado...", "Hilos en directo...",
        ]),
    ],
    "&PeerTube": [
        ("Buscar y explorar", [
            "Buscar vídeos...", "Buscar canales...", "Ir a un canal...",
            "Cargar más vídeos",
        ]),
        ("Subir e importar", [
            "Subir vídeo...", "Importar vídeo por URL...", "Mis importaciones...",
        ]),
        ("Mi biblioteca y directos", [
            "Gestionar mis listas...", "Gestionar mis canales...", "Crear directo...",
        ]),
        ("Instancia y avisos", [
            "Preferencias de notificaciones...", "Estadísticas de la instancia...",
            "Mis denuncias...",
        ]),
    ],
    "&RSS": [
        ("Suscripciones y feeds", [
            "Añadir Feed RSS...", "Buscar feeds y podcasts...", "Crear feed desde una web...",
            "Añadir canal de YouTube...", "Gestionar Categorías...",
            "Salud de los feeds...", "Importar feeds desde OPML...",
            "Importar suscripciones de YouTube (Google Takeout)...",
            "Exportar feeds a OPML...",
        ]),
        ("Organizar y filtrar", [
            "Búsquedas guardadas...", "Carpetas inteligentes...", "Etiquetas...",
            "Alertas por palabra clave...", "Reglas y Filtros RSS...",
        ]),
        ("Lectura y exportación", [
            "Exportar artículos...", "Historial de lectura...",
            "Estadísticas de lectura...", "Resumen de artículos sin leer...",
            "Papelera de artículos...", "Descargas de podcast...",
        ]),
        ("Servicios externos", [
            "Configurar Wallabag (leer más tarde)...", "Sincronización con servidor...",
        ]),
        ("Mantenimiento", [
            "Limpiar artículos antiguos", "Comprobar y optimizar base de datos",
        ]),
        ("Vista", [
            "Mostrar solo no leídos", "Agrupar por fecha",
            "Ocultar artículos anclados (fecha futura)",
        ]),
    ],
    "&Telegram": [
        ("Mi cuenta", [
            "Mi perfil...", "Sesiones activas...", "Ajustes de cuenta...",
            "Verificación en dos pasos...", "Resumen de Telegram",
        ]),
        ("Privacidad y seguridad", [
            "Privacidad...", "Privacidad global...", "Usuarios bloqueados...",
        ]),
        ("Buscar", [
            "Buscar usuarios, grupos y canales...", "Búsqueda global de mensajes...",
            "Buscar publicaciones de canales...",
        ]),
        ("Chats, carpetas e historias", [
            "Mensajes guardados...", "Carpetas...", "Chats frecuentes...", "Historias...",
        ]),
        ("Crear y unirse", [
            "Unirse a grupo/canal por enlace...", "Crear grupo...", "Crear supergrupo...",
            "Crear canal...",
        ]),
    ],
    "&X (Twitter)": [
        ("Publicar y responder", [
            "Nuevo post de X...", "Responder", "Citar", "Programar publicación...",
            "Me gusta", "Cargar publicaciones más antiguas",
        ]),
        ("Buscar y descubrir", [
            "Guardar búsqueda como buffer...", "Abrir cronología de un usuario...",
            "Buscar cuentas...", "Buscar comunidades...",
            "Tendencias...", "Tendencias de un país o ciudad...", "Ver mi perfil de X...",
        ]),
        ("Listas", [
            "Gestionar listas...",
        ]),
    ],
    "&IRC": [
        ("Conexión y redes", [
            "Conectar a la red", "Desconectar de la red", "Añadir red IRC...",
            "Editar red...",
        ]),
        ("Canales y usuarios", [
            "Consola del servidor (enviar comandos)...", "Unirse a un canal...",
            "Canales del servidor...", "Datos de un usuario (whois)...",
            "Usuarios ignorados...", "Amigos (avisos de conexión)...",
        ]),
        ("Mi estado y avanzado", [
            "Marcarme como ausente...", "Volver a estar disponible",
            "Datos de un apodo desconectado (whowas)...", "Hacerme operador de la red...",
            "Enviar wallops...",
        ]),
    ],
    "Ma&trix": [
        ("Conexión y cuentas", [
            "Conectar", "Desconectar", "Añadir cuenta de Matrix...",
            "Registrar cuenta nueva...",
        ]),
        ("Salas y mensajes", [
            "Unirse a una sala...", "Crear sala...", "Mensaje directo a un usuario...",
            "Directorio de salas...", "Llamar a la puerta de una sala...",
            "Crear espacio...",
        ]),
        ("Mi cuenta", [
            "Mi perfil y estado...", "Mis dispositivos...", "Palabras que me avisan...",
        ]),
        ("Cifrado", [
            "Configurar o restablecer la identidad de cifrado (cross-signing)...",
            "Exportar claves de cifrado...", "Importar claves de cifrado...",
            "Volver a descifrar mensajes pendientes",
        ]),
    ],
    "&XMPP": [
        ("Conexión y cuentas", [
            "Conectar", "Desconectar", "Añadir cuenta de XMPP...",
        ]),
        ("Contactos y salas", [
            "Agregar contacto...", "Unirse a una sala...", "Directorio de salas...",
            "Pasarelas del servidor...", "Buscar contacto o grupo...",
            "Buscar usuarios...",
        ]),
        ("Mi perfil y estado", [
            "Mi estado de ánimo...", "Mi actividad...", "Editar mi ficha (vCard)...",
            "Mis dispositivos de cifrado (OMEMO)...",
        ]),
    ],
    "H&erramientas": [
        ("Reproductores y radio", [
            "Abrir Reproductor Multimedia...", "Radio por internet...",
            "Reproductor de radio, podcast y YouTube...",
        ]),
        ("Publicar en varias redes", [
            "Publicación cruzada (varias redes)...",
            "Borradores y programados de todas las redes...", "Fragmentos de texto...",
        ]),
        ("Resúmenes y estadísticas", [
            "Centro de notificaciones (todas las redes)...",
            "Ponte al día (resumen con IA)...", "Estadísticas de mis redes...",
            "Buscar en todo el contenido...",
        ]),
        ("Lectura rápida", [
            "Leer barra de estado", "Ver descripción de la multimedia",
        ]),
        ("Personalización", [
            "Filtros de contenido...", "Mostrar u ocultar buffers...",
            "Editor de plantillas de lectura...",
        ]),
        ("Datos y mantenimiento", [
            "Copias de seguridad...", "Migrar a otro ordenador...",
            "Borrar datos / liberar espacio...", "Visor de Registros (Logs)...",
        ]),
    ],
}


# ---------------------------------------------------------------------------
# ESQUEMA DE LOS MENÚS CONTEXTUALES (uno global: cada red aporta sus etiquetas a
# categorías comunes). Solo se aplica a menús grandes (>= 12 items de primer nivel).
# ---------------------------------------------------------------------------

ESQUEMA_CONTEXTUAL = [
    ("Responder y compartir", [
        # Mastodon
        "Responder", "Citar toot", "Citar", "Impulsar publicación (boost)",
        "Deshacer mi impulso", "Marcar como favorito", "Quitar de favoritos",
        "Guardar en marcadores", "Quitar de marcadores",
        # Bluesky / X / W Social
        "Me gusta", "Quitar me gusta", "Repostear", "Deshacer repost",
        "Retweet", "Deshacer retweet", "Añadir a marcadores", "Quitar marcador",
        "Republicar", "Deshacer republicación",
        # Reddit
        "Voto positivo", "Voto negativo", "Quitar voto", "Guardar",
        "Quitar de guardados", "Crosspost a otra comunidad...",
        # PeerTube
        "No me gusta", "Quitar mi voto", "Comentar...",
        # RSS
        "Compartir (Mastodon o Bluesky)...", "Enviar a Wallabag (leer más tarde)",
    ]),
    ("Ver y abrir", [
        # Mastodon
        "Ver publicación...", "Ver mensaje directo...", "Ver conversación",
        "Ver conversación de mensajes directos", "Abrir conversación como buffer",
        "Abrir conversación de directos como buffer", "Ver tarjeta de enlace de la publicación...",
        "Ver historial de ediciones...", "Ver quién la impulsó...",
        "Ver quién la marcó favorita...", "Ver publicación y encuesta...",
        "Votar en la encuesta...",
        "Abrir publicación en el navegador", "Mostrar de todos modos (filtrada)",
        "Mostrar de todos modos", "Volver a ocultar el contenido con advertencia",
        # Bluesky / X / W Social
        "Abrir la conversación como buffer", "Ver quién dio me gusta",
        "Ver quién la reposteó", "Ver quién la citó", "Ver quién hizo retweet",
        "Ver la conversación", "Ver quién le ha dado me gusta",
        "Ver quién la ha republicado", "Ver quién la ha citado",
        "Abrir en el navegador",
        # Reddit / PeerTube
        "Ver comentarios", "Ver hilo", "Ver comentarios...", "Ver detalles",
        # Chats (IRC / Matrix / XMPP / Bluesky)
        "Abrir conversación", "Ver últimos mensajes", "Ver los últimos mensajes",
        "Ver las salas de este espacio...", "Ver los hilos de la sala...",
        "Ver los mensajes fijados...", "Ver mensajes destacados...",
        # RSS
        "Modo de lectura limpia", "Ver como HTML en el navegador",
        "Imágenes del artículo...",
    ]),
    ("Mis publicaciones", [
        # Mastodon
        "Editar publicación...", "Fijar publicación en tu perfil",
        "Quitar publicación fijada de tu perfil", "Borrar publicación...",
        "Borrar y volver a redactar...", "Ver quién cita esta publicación...",
        "Quién puede citar esta publicación...",
        # Bluesky / X / W Social
        "Fijar esta publicación en tu perfil", "Quitar la publicación fijada de tu perfil",
        "Borrar post", "Borrar publicación", "Fijar en mi perfil", "Dejar de fijar",
        "Borrar la publicación",
        # Reddit
        "Editar", "Borrar", "Marcar +18", "Quitar +18", "Marcar spoiler",
        "Quitar spoiler", "Etiqueta (flair)...",
        # PeerTube (mis vídeos)
        "Editar mi vídeo...", "Borrar mi vídeo", "Estadísticas del vídeo...",
        "Datos de emisión (RTMP/clave)...", "Ver sesiones y repeticiones...",
    ]),
    ("Autor y cuentas", [
        # Mastodon
        "Seguir al autor", "Dejar de seguir al autor", "Seguir o dejar de seguir al autor",
        "Opciones de seguimiento del autor...", "Añadir el autor a una lista...",
        "Ver detalles del autor...",
        "Ver publicaciones del autor o de un usuario mencionado...",
        "Crear buffer con la cronología del autor o de un usuario mencionado...",
        "Nota personal sobre el autor...", "Destacar al autor en mi perfil",
        "Dejar de destacar al autor", "Quitar al autor de mis seguidores",
        "Seguidores conocidos del autor...", "Listas en las que está el autor...",
        "Alias local para este autor...",
        # Buffers de cuentas de Mastodon (seguidores/siguiendo)
        "Seguir / dejar de seguir", "Ver detalles de la cuenta...",
        "Ver sus publicaciones...", "Crear buffer con su cronología",
        "Destacar en mi perfil", "Dejar de destacar", "Quitar de mis seguidores",
        "Seguidores conocidos...", "Hashtags destacados de esta cuenta...",
        "Listas en las que está...",
        # Bluesky
        "Ver perfil del autor", "Crear un buffer con las publicaciones de esta cuenta",
        "Crear un buffer con las publicaciones de un usuario mencionado...",
        "Mensaje directo al autor", "Seguir autor", "Dejar de seguir autor",
        "Avisarme cuando el autor publique", "Cuentas similares al autor",
        "Seguidores en común con el autor",
        # X
        "Ver la cronología de este autor", "Ver la cronología de un usuario mencionado...",
        "Ver el perfil de un usuario mencionado...", "Seguir a un usuario mencionado...",
        "Añadir al autor a una lista...",
        # W Social
        "Ver el perfil del autor", "Crear un buffer con las publicaciones del autor",
        "Escribir un mensaje directo al autor",
        # PeerTube (canal y cuenta)
        "Suscribirse al canal", "Cancelar suscripción al canal", "Abrir el canal",
        "Abrir la cuenta",
        # XMPP (contacto)
        "Ver información del contacto", "Ver ficha (vCard)...", "Información técnica...",
        "Enviar un zumbido", "Renombrar contacto...", "Quitar contacto...",
    ]),
    ("Utilidades y lectura", [
        "Traducir toot", "Traducir artículo", "Traducir con la app", "Resumir con IA",
        "Describir imagen", "Leer en voz alta (TTS)",
        "Reproducir audio o vídeo", "Detener audio o vídeo",
        "Reproducir audio del enlace (YouTube, SoundCloud...)",
        "Detener audio del enlace (YouTube, SoundCloud...)",
        "Reproducir vídeo de la publicación",
        # PeerTube
        "Reproducir", "Ver subtítulos...", "Calidad de reproducción...",
        # RSS podcast
        "Reproducir podcast / audio", "Añadir a la cola de podcasts",
        "Descargar episodio (escuchar offline)",
        "Borrar descarga (offline)",
    ]),
    ("Copiar y exportar", [
        "Copiar enlace de la publicación",
        # RSS
        "Copiar título", "Copiar artículo completo", "Copiar enlace",
        "Exportar artículo...", "Etiquetas del artículo...", "Versiones anteriores del artículo...",
        # Chats
        "Exportar conversación...",
    ]),
    ("Estado y gestión", [
        # Mastodon DM / RSS / Telegram
        "Marcar conversación como leída",
        "Marcar como leído", "Marcar como no leído", "Marcar todo el buffer como leído",
        "Destacar", "Quitar de destacados", "Leer más tarde", "Quitar de Leer más tarde",
        "Eliminar artículo (a la papelera)",
        # Reddit
        "Mostrar", "Ocultar",
        # PeerTube (historial y biblioteca)
        "Quitar del historial", "Vaciar todo el historial", "Añadir a 'ver más tarde'",
        "Añadir a una lista...", "Descargar...",
        # Chats (Matrix / XMPP / Bluesky)
        "Marcar como leída", "Fijar arriba", "Desfijar de arriba", "Archivar",
        "Desarchivar", "Vaciar conversación (borrar mensajes)...",
        "Abandonar conversación", "Salir de la conversación", "Salir de la sala",
        "Salir y OLVIDAR la sala...", "Salir del grupo",
        "Salir y olvidar el grupo (no volver a entrar solo)",
        "Ver solicitudes de mensajes...", "Aceptar la solicitud",
        "Eliminar este chat de la lista",
    ]),
    ("Grupo y sala", [
        # Bluesky (grupos de chat; ojo: algunos literales van sin tilde en el código)
        "Ver miembros de la conversación", "Anadir cuentas al grupo...",
        "Expulsar una cuenta del grupo...", "Cambiar el nombre del grupo...",
        "Enlace de invitacion...", "Solicitudes para entrar...",
        # W Social
        "Participantes del grupo...", "Enlace de invitación...", "Reabrir el grupo",
        "Cerrar el grupo",
        # Matrix
        "Miembros y moderación...", "Ajustes de la sala (administración)...",
        "Permitir llamar a la puerta (knock)", "Cambiar el avatar de la sala...",
        "Añadir a un espacio...",
        # XMPP (MUC)
        "Ver ocupantes...", "Ver o cambiar el tema...",
        # IRC
        "Ver usuarios del canal...", "Unirse al canal", "Salir del canal",
    ]),
    ("Cifrado", [
        "Dispositivos cifrados de la sala...", "Activar cifrado OMEMO",
        "Desactivar cifrado OMEMO", "Huellas de cifrado OMEMO...",
    ]),
    ("Moderación y seguridad", [
        # Mastodon
        "Silenciar al autor", "Bloquear al autor", "Bloquear todo el dominio del autor...",
        "Silenciar esta conversación", "Ignorar publicaciones de este cliente",
        "Filtrar esta publicación...", "Reportar publicación...",
        # Buffers de cuentas / XMPP
        "Silenciar", "Quitar silencio", "Bloquear", "Desbloquear",
        # Bluesky
        "Silenciar autor", "Quitar silencio al autor", "Bloquear autor",
        "Desbloquear autor", "Silenciar hilo", "Quitar silencio del hilo",
        "Ocultar esta publicación para mí", "Dejar de ocultar esta publicación",
        # W Social
        "Denunciar la publicación...", "Denunciar la cuenta...",
        # PeerTube
        "Silenciar la cuenta", "Silenciar la instancia", "Reportar el vídeo...",
        "Bloquear el vídeo (moderación)...", "Desbloquear el vídeo (moderación)",
        # Matrix
        "Reportar la sala a los administradores...",
    ]),
    ("Hashtag del buffer", [
        "Seguir el hashtag #{0}", "Dejar de seguir el hashtag #{0}",
        "Destacar el hashtag #{0} en mi perfil",
    ]),
]


def organizar_menubar(menubar: wx.MenuBar) -> None:
    """Aplica los esquemas a los menús de la barra. No hace nada si la opción está
    desactivada. Se llama al final de _crear_menus, ANTES de SetMenuBar."""
    if not activo():
        return
    for i in range(menubar.GetMenuCount()):
        try:
            titulo = menubar.GetMenuLabelText(i)  # traducido y sin &
        except Exception:
            continue
        for clave_titulo, esquema in ESQUEMAS_BARRA.items():
            if titulo == _sin_amp(_(clave_titulo)):
                try:
                    _organizar_menu(menubar.GetMenu(i), esquema, minimo=8)
                except Exception as e:
                    log.error(f"No se pudo organizar el menú '{titulo}' en subcategorías: {e}")
                break


def organizar_contextual(menu: wx.Menu) -> None:
    """Aplica el esquema global a un menú contextual GRANDE (12 items o más). Con la
    opción desactivada, o con menús pequeños, no toca nada."""
    if not activo():
        return
    try:
        _organizar_menu(menu, ESQUEMA_CONTEXTUAL, minimo=12)
    except Exception as e:
        log.error(f"No se pudo organizar el menú contextual en subcategorías: {e}")
