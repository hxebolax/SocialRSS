# -*- coding: utf-8 -*-
"""Teclas visibles en los menús y bloque de acciones comunes a todas las redes.

Dos cosas pedidas por los usuarios, resueltas en un ÚNICO punto en vez de repartidas por los
cientos de items que hay en la aplicación:

1. **La tecla de cada acción, a la vista.** Muchos items ya la mostraban (los que se crean con
   `_lbl_atajo`), pero la mayoría de los menús contextuales de cada red no. Aquí se recorren los
   menús ya construidos y se le añade a cada item la tecla que le corresponde, buscándola por su
   ETIQUETA en el catálogo de `shortcut_manager` (la fuente única de teclas). Así vale para todas
   las redes, respeta las teclas personalizadas por el usuario y no hay que tocar cada literal.

2. **Las acciones comunes, en el menú de cada red.** Cosas como actualizar el buffer o cambiar de
   cuenta valen en cualquier red pero solo estaban en Herramientas o en ninguna parte. Se añaden
   al final del menú de cada red DENTRO DE UN SUBMENÚ, para que el menú siga siendo corto (una
   sola entrada más) y para que la organización opcional en subcategorías no se vea afectada: los
   submenús ya existentes nunca se tocan.

Ambas cosas se aplican después de construir cada menú, así que funcionan igual con los menús
planos de siempre y con los organizados en subcategorías.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging
import wx

from .shortcut_manager import shortcut_manager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1) TECLAS VISIBLES
# ---------------------------------------------------------------------------
# Etiqueta del menú (literal en español, tal como está en el código) -> acción del catálogo.
# Solo acciones cuya tecla hace EXACTAMENTE eso mismo sobre el elemento enfocado; si una etiqueta
# no está aquí, su item se queda sin tecla (mejor sin tecla que con una que engañe).
ETIQUETAS_PUBLICACION = {
    # Responder / citar / publicar
    "Responder": "pub_responder",
    "Responder...": "pub_responder",
    "Citar": "pub_citar",
    "Citar...": "pub_citar",
    "Citar toot": "pub_citar",
    "Nuevo Toot o post...": "pub_nuevo",
    "Nuevo post de Bluesky...": "pub_nuevo",
    "Nuevo post de X...": "pub_nuevo",
    "Nueva publicación de Reddit...": "pub_nuevo",
    "&Nueva publicación...": "pub_nuevo",
    "Nuevo Hilo...": "pub_hilo",
    "Nuevo hilo de Bluesky...": "pub_hilo",
    "Nuevo &hilo...": "pub_hilo",
    # Me gusta / favorito / voto
    "Me gusta": "pub_boost_like",
    "Quitar me gusta": "pub_boost_like",
    "Me gusta / favorito": "pub_boost_like",
    "Marcar como favorito": "pub_boost_like",
    "Quitar de favoritos": "pub_boost_like",
    "Voto positivo": "pub_boost_like",
    # Conversación y texto
    "Ver conversación": "pub_ver_conv",
    "Ver la conversación": "pub_ver_conv",
    "Ver conversación de mensajes directos": "pub_ver_conv",
    "Ver el texto de la publicación": "pub_ver_texto",
    # Cargar más
    "Cargar más publicaciones": "pub_cargar_mas",
    "Cargar más toots antiguos": "pub_cargar_mas",
    "Cargar publicaciones más antiguas": "pub_cargar_mas",
    "Cargar más vídeos": "pub_cargar_mas",
    # Cronología de usuario
    "Abrir cronología de un usuario...": "pub_tl_usuario",
    "Abrir cronología de un usuario…": "pub_tl_usuario",
    # Ver publicación (Mastodon) / destacar (RSS)
    "Ver publicación...": "pub_ver",
    "Ver mensaje directo...": "pub_ver",
    "Destacar": "pub_ver",
    "Quitar de destacados": "pub_ver",
    # IA y traducción
    "Traducir toot": "ia_traducir",
    "Traducir artículo": "ia_traducir",
    "Traducir con la app": "ia_traducir",
    "Traducir Elemento": "ia_traducir",
    "Describir imagen": "ia_describir",
    "Describir Imagen con IA": "ia_describir",
    "Extraer texto de imagen (OCR con IA)": "ia_ocr",
    # RSS
    "Marcar como leído": "rss_toggle_read",
    "Marcar como no leído": "rss_toggle_read",
    "Leer más tarde": "rss_leer_tarde",
    "Quitar de Leer más tarde": "rss_leer_tarde",
    "Copiar título": "rss_copiar_tit",
    "Modo de lectura limpia": "rss_ver_visor",
}

# Acciones que valen en CUALQUIER menú, también en los de chats (Telegram, IRC, Matrix, XMPP):
# no dependen de que el elemento enfocado sea una publicación.
ETIQUETAS_GLOBALES = {
    "Actualizar": "actualizar_buffer",
    "Actualizar buffer": "actualizar_buffer",
    "Actualizar el buffer activo": "actualizar_buffer",
    "Actualizar resultados": "actualizar_buffer",
    "Actualizar publicaciones": "actualizar_buffer",
    "Actualizar mensajes": "actualizar_buffer",
    "Buscar en todo el contenido...": "buscar_global",
    "Leer barra de estado": "leer_barra_estado",
    "Ver descripción de la multimedia": "ver_descripcion",
    "Preferencias...": "preferencias",
    "Publicación cruzada (varias redes)...": "crosspost",
    "Centro de notificaciones (todas las redes)...": "centro_notificaciones",
    "Ponte al día (resumen con IA)...": "ponte_al_dia",
    "Cuenta siguiente": "cuenta_siguiente",
    "Cuenta anterior": "cuenta_anterior",
}


def _accion_de(etiqueta: str, incluir_publicacion: bool):
    """Acción del catálogo que corresponde a una etiqueta de menú, o None."""
    if etiqueta in ETIQUETAS_GLOBALES:
        return ETIQUETAS_GLOBALES[etiqueta]
    if incluir_publicacion:
        return ETIQUETAS_PUBLICACION.get(etiqueta)
    return None


def anotar_menu(menu: wx.Menu, incluir_publicacion: bool = True) -> None:
    """Añade a cada item su tecla entre paréntesis, si la tiene y no la muestra ya.

    `incluir_publicacion` en False para los menús de CHATS (Telegram, IRC, Matrix, XMPP): allí
    hay items que se llaman igual ("Responder") pero cuya tecla actúa sobre publicaciones, y
    anunciar una tecla que no hace eso confundiría más que ayudar.
    """
    if menu is None:
        return
    for it in menu.GetMenuItems():
        if it.IsSeparator():
            continue
        sub = it.GetSubMenu()
        if sub is not None:
            anotar_menu(sub, incluir_publicacion)
            continue
        try:
            bruta = it.GetItemLabel()          # con & y con el acelerador \t si lo tiene
            if "\t" in bruta:
                continue                       # ya lleva acelerador real de menú: wx lo muestra
            visible = it.GetItemLabelText()    # sin & ni acelerador
            accion = _accion_de(visible, incluir_publicacion)
            if not accion:
                continue
            combo = shortcut_manager.combo_legible(accion)
            if not combo or f"({combo})" in visible:
                continue
            it.SetItemLabel(f"{bruta} ({combo})")
        except Exception as e:
            log.debug(f"No se pudo anotar la tecla de un item de menú: {e}")


# Menús de la barra que son de CHATS: en ellos no se anotan las teclas de publicación.
_MENUS_DE_CHAT = ("&Telegram", "&IRC", "Ma&trix", "&XMPP")


def anotar_menubar(menubar: wx.MenuBar) -> None:
    """Anota las teclas en todos los menús de la barra."""
    for i in range(menubar.GetMenuCount()):
        try:
            titulo = menubar.GetMenuLabelText(i)
            es_chat = any(titulo == _(t).replace("&", "") for t in _MENUS_DE_CHAT)
            anotar_menu(menubar.GetMenu(i), incluir_publicacion=not es_chat)
        except Exception as e:
            log.debug(f"No se pudieron anotar las teclas del menú {i}: {e}")


# ---------------------------------------------------------------------------
# 2) ACCIONES COMUNES A TODAS LAS REDES
# ---------------------------------------------------------------------------
# (etiqueta, acción del catálogo para la tecla, método del MainFrame, argumentos con los que se
# llama). Los manejadores de menú del frame esperan el evento (se les pasa None, como en el resto
# del código); los que no lo esperan llevan () y los que necesitan un valor fijo lo declaran aquí.
# El método se resuelve por nombre al construir el menú: si una versión no lo tuviera, ese item
# no se añade, en vez de romper el menú entero.
_COMUNES = [
    (_("Actualizar este buffer"), "actualizar_buffer", "_on_actualizar_buffer_actual", (None,)),
    (_("Ver todos los buffers de esta cuenta..."), "dialogo_buffers",
     "_mostrar_dialogo_buffers_cuenta", ()),
    (_("Cuenta siguiente"), "cuenta_siguiente", "_cambiar_cuenta", (1,)),
    (_("Cuenta anterior"), "cuenta_anterior", "_cambiar_cuenta", (-1,)),
    (None, None, None, None),   # separador
    (_("Buscar en todo el contenido..."), "buscar_global", "_on_buscar_global", (None,)),
    (_("Centro de notificaciones (todas las redes)..."), "centro_notificaciones",
     "_on_centro_notificaciones", (None,)),
    (_("Publicación cruzada (varias redes)..."), "crosspost", "_on_crosspost", (None,)),
    (_("Ponte al día (resumen con IA)..."), "ponte_al_dia", "_on_ponte_al_dia", (None,)),
    (None, None, None, None),
    (_("Leer la barra de estado"), "leer_barra_estado", "_on_leer_barra_estado", (None,)),
    (_("Ver la descripción de la multimedia"), "ver_descripcion", "_on_ver_descripcion", (None,)),
    (_("Preferencias..."), "preferencias", "_on_preferencias", (None,)),
]

# Títulos de los menús de la barra que NO son de una red (no reciben el bloque común).
_NO_SON_RED = ("&Archivo", "&IA", "&Traducción", "H&erramientas", "A&yuda")


def _submenu_comunes(frame) -> wx.Menu:
    """Crea el submenú con las acciones que valen en cualquier red."""
    sub = wx.Menu()
    for etiqueta, accion, metodo, args in _COMUNES:
        if etiqueta is None:
            sub.AppendSeparator()
            continue
        fn = getattr(frame, metodo, None)
        if fn is None:
            log.debug(f"Acciones comunes: el frame no tiene {metodo}; se omite ese item.")
            continue
        combo = shortcut_manager.combo_legible(accion)
        it = sub.Append(wx.ID_ANY, f"{etiqueta} ({combo})" if combo else etiqueta)
        frame.Bind(wx.EVT_MENU, (lambda evt, f=fn, a=args: f(*a)), it)
    return sub


def anadir_acciones_comunes(menubar: wx.MenuBar, frame) -> None:
    """Añade a cada menú DE RED un submenú con las acciones comunes a todas las redes.

    Va en un submenú a propósito: el menú de la red solo crece en una entrada, y la organización
    opcional en subcategorías lo respeta tal cual (nunca mueve submenús ya existentes)."""
    excluidos = {_(t).replace("&", "") for t in _NO_SON_RED}
    for i in range(menubar.GetMenuCount()):
        try:
            if menubar.GetMenuLabelText(i) in excluidos:
                continue
            menu = menubar.GetMenu(i)
            sub = _submenu_comunes(frame)
            if sub.GetMenuItemCount() == 0:
                continue
            menu.AppendSeparator()
            menu.AppendSubMenu(sub, _("Acciones comunes a todas las redes"))
        except Exception as e:
            log.debug(f"No se pudo añadir el bloque común al menú {i}: {e}")
