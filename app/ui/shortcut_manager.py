# -*- coding: utf-8 -*-
"""
Gestor central de atajos de teclado de la aplicación.

Es la ÚNICA fuente de verdad de las combinaciones de teclas: define el catálogo de acciones
(con su tecla por defecto y la sección a la que pertenece), carga/guarda las personalizaciones
del usuario (se conservan entre sesiones en config["shortcuts"]["mappings"]) y genera las tuplas
para wx.AcceleratorEntry. La tabla de aceleradores real, las etiquetas de los menús y el listado
de "Teclas" de Ayuda se construyen a partir de aquí, de modo que personalizar una tecla se refleja
en todos esos sitios.
"""

import wx
import logging
from typing import Dict, Tuple, List, Optional
from app.core.config_manager import config

log = logging.getLogger(__name__)

# --- Secciones del editor (orden de aparición) ---
SEC_GENERALES = "Generales"
SEC_PUBLICACIONES = "Publicaciones (Mastodon, Bluesky y X)"
SEC_RSS = "RSS"
SEC_HERRAMIENTAS = "Herramientas para todas las redes"
SEC_REPRODUCTOR = "Reproductores (audio, vídeo, podcast y radio)"
SEC_MAS = "Más acciones sobre el elemento (sin tecla de fábrica)"

# Catálogo de acciones EDITABLES: (accion_id, etiqueta, seccion, tecla_por_defecto).
# Las teclas son UNIFICADAS: la misma tecla actúa sobre la red enfocada (el handler despacha según
# la red activa), por eso las acciones comunes viven una sola vez en "Publicaciones".
CATALOGO: List[Tuple[str, str, str, str]] = [
    # Generales de la ventana principal
    ("actualizar_buffer", "Actualizar el buffer activo", SEC_GENERALES, "Ctrl+Shift+R"),
    ("buscar_global", "Búsqueda global (Mastodon, Bluesky y RSS)", SEC_GENERALES, "Ctrl+Shift+F"),
    ("leer_barra_estado", "Leer en voz la barra de estado", SEC_GENERALES, "Ctrl+Shift+B"),
    ("ver_descripcion", "Ver la descripción de la multimedia", SEC_GENERALES, "Ctrl+Shift+D"),
    ("cuenta_siguiente", "Cuenta siguiente", SEC_GENERALES, "Ctrl+Tab"),
    ("cuenta_anterior", "Cuenta anterior", SEC_GENERALES, "Ctrl+Shift+Tab"),
    ("dialogo_buffers", "Diálogo con todos los buffers de la cuenta", SEC_GENERALES, "Ctrl+Shift+L"),
    ("preferencias", "Abrir Preferencias", SEC_GENERALES, "Ctrl+P"),
    # Acciones sobre publicaciones (Mastodon, Bluesky, X)
    ("pub_nuevo", "Nuevo toot o post", SEC_PUBLICACIONES, "Ctrl+N"),
    ("pub_hilo", "Nuevo hilo", SEC_PUBLICACIONES, "Ctrl+Shift+N"),
    ("pub_responder", "Responder", SEC_PUBLICACIONES, "Ctrl+R"),
    ("pub_citar", "Citar", SEC_PUBLICACIONES, "Ctrl+Shift+U"),
    ("pub_boost_like", "Me gusta o quitar me gusta (favorito en Mastodon)", SEC_PUBLICACIONES, "Ctrl+B"),
    ("pub_cargar_mas", "Cargar más publicaciones antiguas", SEC_PUBLICACIONES, "Ctrl+Shift+M"),
    ("pub_ver_conv", "Ver la conversación", SEC_PUBLICACIONES, "Ctrl+Shift+V"),
    ("pub_ver_texto", "Ver el texto completo de la publicación", SEC_PUBLICACIONES, "Ctrl+Shift+T"),
    ("pub_tl_usuario", "Abrir la cronología de un usuario", SEC_PUBLICACIONES, "Ctrl+Shift+G"),
    ("pub_ver", "Ver publicación (Mastodon) o destacar (RSS)", SEC_PUBLICACIONES, "Ctrl+D"),
    ("ia_traducir", "Traducir la publicación o el artículo", SEC_PUBLICACIONES, "Ctrl+T"),
    ("ia_describir", "Describir la imagen con IA", SEC_PUBLICACIONES, "Ctrl+I"),
    ("ia_ocr", "Extraer el texto de la imagen (OCR)", SEC_PUBLICACIONES, "Ctrl+Shift+O"),
    ("consultar_caracteres", "Consultar caracteres restantes (al redactar)", SEC_PUBLICACIONES, "Ctrl+Shift+Space"),
    ("insertar_fragmento", "Insertar un fragmento de texto al redactar (en cualquier red)", SEC_PUBLICACIONES, "Ctrl+Shift+J"),
    # RSS
    ("rss_leer_tarde", "Leer más tarde", SEC_RSS, "Ctrl+L"),
    ("rss_toggle_read", "Marcar como leído o no leído", SEC_RSS, "Ctrl+M"),
    ("rss_copiar_tit", "Copiar el título", SEC_RSS, "Ctrl+Shift+C"),
    ("rss_ver_visor", "Abrir el lector limpio del artículo", SEC_RSS, "Ctrl+Shift+E"),
    ("rss_sig_no_leido", "Ir al siguiente artículo no leído", SEC_RSS, "Ctrl+K"),
    ("rss_ant_no_leido", "Ir al anterior artículo no leído", SEC_RSS, "Ctrl+Shift+K"),
    # Herramientas para todas las redes (menú Herramientas)
    ("crosspost", "Publicación cruzada (varias redes)", SEC_HERRAMIENTAS, "Ctrl+Shift+X"),
    ("centro_notificaciones", "Centro de notificaciones (todas las redes)", SEC_HERRAMIENTAS, "Ctrl+Shift+A"),
    ("ponte_al_dia", "Ponte al día (resumen con IA)", SEC_HERRAMIENTAS, "Ctrl+Shift+Y"),
    # Más acciones sobre el elemento enfocado (petición de usuarios: "faltan muchas acciones a
    # las que poner tecla"). Vienen SIN tecla de fábrica a propósito: casi todas las
    # combinaciones cómodas ya están ocupadas, así que cada cual asigna la que quiera sin pisar
    # nada. Despachan según la red activa, igual que en la interfaz invisible.
    ("pub_impulsar", "Impulsar o repost (Mastodon, Bluesky y X)", SEC_MAS, ""),
    ("pub_abrir_navegador", "Abrir la publicación o el artículo en el navegador", SEC_MAS, ""),
    ("pub_copiar_enlace", "Copiar el enlace de la publicación o del artículo", SEC_MAS, ""),
    ("pub_reproducir", "Reproducir o detener el audio o vídeo de la publicación", SEC_MAS, ""),
    ("pub_seguir_autor", "Seguir o dejar de seguir al autor (Mastodon)", SEC_MAS, ""),
    ("pub_menu_contextual", "Abrir el menú contextual del elemento", SEC_MAS, ""),
    # Reproductores. Actúan sobre lo que esté sonando (redes sociales, podcast/YouTube o radio) y
    # funcionan desde CUALQUIER ventana, también con un diálogo abierto: las captura un filtro de
    # eventos de la aplicación, no la tabla de aceleradores. Por eso solo se admiten teclas de
    # función o combinaciones con Ctrl/Alt/Mayús (ver combo_valido_reproductor): una letra suelta
    # se comería lo que escribes en cualquier campo de texto.
    ("media_retroceder", "Retroceder en la reproducción", SEC_REPRODUCTOR, "F1"),
    ("media_play_pausa", "Reproducir o pausar", SEC_REPRODUCTOR, "F2"),
    ("media_avanzar", "Avanzar en la reproducción", SEC_REPRODUCTOR, "F3"),
    ("media_detener", "Detener la reproducción", SEC_REPRODUCTOR, "F4"),
    ("media_bajar_volumen", "Bajar el volumen", SEC_REPRODUCTOR, "F5"),
    ("media_subir_volumen", "Subir el volumen", SEC_REPRODUCTOR, "F6"),
    ("media_bajar_velocidad", "Reducir la velocidad (no en la radio)", SEC_REPRODUCTOR, "F7"),
    ("media_subir_velocidad", "Aumentar la velocidad (no en la radio)", SEC_REPRODUCTOR, "F8"),
]

# Acciones de los reproductores, en el orden del catálogo. El resto del código las pide por aquí
# en vez de repetir la lista.
ACCIONES_REPRODUCTOR: List[str] = [acc for acc, _et, sec, _t in CATALOGO if sec == SEC_REPRODUCTOR]

# Defectos derivados del catálogo. EXTRA_DEFAULTS son atajos que NO se editan desde el editor pero
# se siguen resolviendo por nombre (compatibilidad con código que los pide).
EXTRA_DEFAULTS: Dict[str, str] = {
    "ayuda_contexto": "Ctrl+H",   # anuncia la descripción del control activo (no editable)
}
DEFAULT_SHORTCUTS: Dict[str, str] = {acc: tecla for acc, _et, _sec, tecla in CATALOGO}
DEFAULT_SHORTCUTS.update(EXTRA_DEFAULTS)

_ETIQUETAS: Dict[str, str] = {acc: et for acc, et, _sec, _tecla in CATALOGO}
_SECCION: Dict[str, str] = {acc: sec for acc, _et, sec, _tecla in CATALOGO}

# Teclas/combinaciones RESERVADAS que el editor no debe permitir reasignar (las usa el sistema
# para navegación por posición, fuera del alcance del editor).
# F1..F8 YA NO están aquí: son las teclas por defecto de los reproductores, que ahora sí se pueden
# personalizar; que no se puedan asignar a OTRA acción lo garantiza conflicto(), porque esas
# acciones están en el catálogo.
TECLAS_RESERVADAS = {
    "alt+1", "alt+2", "alt+3", "alt+4", "alt+5", "alt+6", "alt+7", "alt+8", "alt+9",
    "ctrl+shift+1", "ctrl+shift+2", "ctrl+shift+3", "ctrl+shift+4", "ctrl+shift+5",
    "ctrl+shift+6", "ctrl+shift+7", "ctrl+shift+8", "ctrl+shift+9",
    "space", "ctrl+space", "left", "right", "up", "down",
    "ctrl+q",  # Salir
}

# Traducción de modificadores y teclas para MOSTRAR la combinación en español.
_MOD_LEGIBLE = {"ctrl": "Ctrl", "shift": "Mayús", "alt": "Alt"}
_TECLA_LEGIBLE = {
    "SPACE": "Espacio", "RIGHT": "Flecha derecha", "LEFT": "Flecha izquierda",
    "UP": "Flecha arriba", "DOWN": "Flecha abajo", "ENTER": "Intro", "RETURN": "Intro",
    "ESCAPE": "Escape", "ESC": "Escape", "TAB": "Tabulador",
}


class ShortcutManager:
    """Administra el mapeo de teclado y genera tablas de aceleradores wx."""

    def __init__(self):
        self._shortcuts: Dict[str, str] = {}
        self._cache_media: Optional[Dict[Tuple[int, int], str]] = None
        self.load()

    def load(self):
        """Carga los atajos de la configuración sobre los valores por defecto."""
        self._cache_media = None
        custom = config.get("shortcuts", "mappings", {})
        self._shortcuts = dict(DEFAULT_SHORTCUTS)
        if isinstance(custom, dict):
            # Solo aplicar claves conocidas (ignorar restos de versiones antiguas).
            for k, v in custom.items():
                if k in DEFAULT_SHORTCUTS and isinstance(v, str) and v:
                    self._shortcuts[k] = v

    def save(self):
        """Guarda SOLO las personalizaciones que difieren del valor por defecto."""
        self._cache_media = None       # las teclas pueden haber cambiado
        custom = {k: v for k, v in self._shortcuts.items()
                  if DEFAULT_SHORTCUTS.get(k) != v}
        config.set("shortcuts", "mappings", custom)

    # --- Consulta ---
    def obtener_atajo(self, action: str) -> str:
        """Combinación en formato interno/wx (p. ej. 'Ctrl+Shift+R')."""
        return self._shortcuts.get(action, DEFAULT_SHORTCUTS.get(action, ""))

    def es_personalizado(self, action: str) -> bool:
        return self.obtener_atajo(action) != DEFAULT_SHORTCUTS.get(action, "")

    def etiqueta(self, action: str) -> str:
        return _ETIQUETAS.get(action, action)

    def secciones(self) -> List[str]:
        return [SEC_GENERALES, SEC_PUBLICACIONES, SEC_RSS, SEC_HERRAMIENTAS, SEC_MAS, SEC_REPRODUCTOR]

    def acciones_de(self, seccion: str) -> List[str]:
        return [acc for acc, _et, sec, _t in CATALOGO if sec == seccion]

    def combo_legible(self, action: str) -> str:
        """Combinación lista para mostrar al usuario en español (p. ej. 'Ctrl+Mayús+R')."""
        return self.combo_a_legible(self.obtener_atajo(action))

    @staticmethod
    def combo_a_legible(combo: str) -> str:
        if not combo:
            return ""
        partes = combo.split("+")
        mods = [_MOD_LEGIBLE.get(p.lower(), p) for p in partes[:-1]]
        tecla = partes[-1]
        tecla_leg = _TECLA_LEGIBLE.get(tecla.upper(), tecla.upper() if len(tecla) == 1 else tecla)
        return "+".join(mods + [tecla_leg])

    # --- Modificación ---
    def conflicto(self, action: str, key_combo: str) -> Optional[str]:
        """Devuelve la acción con la que choca 'key_combo' (distinta de 'action'), o None."""
        objetivo = key_combo.lower()
        for acc in DEFAULT_SHORTCUTS:
            if acc != action and self.obtener_atajo(acc).lower() == objetivo:
                return acc
        return None

    def es_reservada(self, key_combo: str) -> bool:
        return key_combo.lower() in TECLAS_RESERVADAS

    @staticmethod
    def es_accion_reproductor(action: str) -> bool:
        return action in ACCIONES_REPRODUCTOR

    @staticmethod
    def combo_valido_reproductor(key_combo: str) -> bool:
        """True si una combinación vale para los reproductores.

        Sus teclas las captura un filtro de eventos de TODA la aplicación (para que funcionen con
        cualquier ventana o diálogo delante), así que una tecla suelta que se pueda escribir —una
        letra, un número— dejaría de llegar a los campos de texto. Solo se admiten teclas de
        función (F1..F12) o combinaciones con Ctrl, Alt o Mayús."""
        combo = (key_combo or "").strip()
        if not combo:
            return False
        partes = combo.split("+")
        if len(partes) > 1:
            return True                                  # lleva algún modificador
        tecla = partes[0].upper()
        return tecla.startswith("F") and tecla[1:].isdigit() and 1 <= int(tecla[1:]) <= 12

    def _mapa_reproductor(self) -> Dict[Tuple[int, int], str]:
        """Mapa (modificadores, keycode) -> acción de reproductor, cacheado.

        Este mapa lo consulta el filtro de eventos en CADA pulsación de toda la aplicación, así que
        no se puede estar parseando las combinaciones cada vez (se escribe mucho y el hilo de la
        interfaz es el mismo que atiende al lector de pantalla). Se recalcula solo cuando cambian
        los atajos (load/save lo invalidan)."""
        cache = getattr(self, "_cache_media", None)
        if cache is not None:
            return cache
        cache = {}
        for accion in ACCIONES_REPRODUCTOR:
            tpl = self.combo_to_wx(self.obtener_atajo(accion), 0)
            if not tpl:
                continue
            flags, keycode, _mid = tpl
            mods = 0
            if flags & wx.ACCEL_CTRL:
                mods |= wx.MOD_CONTROL
            if flags & wx.ACCEL_SHIFT:
                mods |= wx.MOD_SHIFT
            if flags & wx.ACCEL_ALT:
                mods |= wx.MOD_ALT
            cache[(mods, keycode)] = accion
        self._cache_media = cache
        return cache

    def accion_reproductor_de_evento(self, event) -> Optional[str]:
        """Acción de reproductor cuya tecla coincide con este evento de teclado, o None.

        La usa el filtro de eventos de la aplicación para saber si una pulsación es de los
        reproductores, respetando lo que el usuario haya personalizado en el editor."""
        try:
            return self._mapa_reproductor().get((event.GetModifiers(), event.GetKeyCode()))
        except Exception:
            return None

    def establecer_atajo(self, action: str, key_combo: str) -> bool:
        """Asigna una combinación. Devuelve False si está reservada, en conflicto o no vale para
        la acción (los reproductores solo admiten teclas de función o con modificador)."""
        if self.es_reservada(key_combo):
            log.warning(f"Tecla reservada, no asignable: {key_combo}")
            return False
        if self.es_accion_reproductor(action) and not self.combo_valido_reproductor(key_combo):
            log.warning(f"Combinación no válida para un reproductor: {key_combo}")
            return False
        if self.conflicto(action, key_combo):
            log.warning(f"Conflicto de teclado: '{key_combo}' ya está asignado")
            return False
        self._shortcuts[action] = key_combo
        self.save()
        return True

    def restaurar(self, action: str):
        """Devuelve una acción a su tecla por defecto."""
        self._shortcuts[action] = DEFAULT_SHORTCUTS.get(action, "")
        self.save()

    def restaurar_seccion(self, seccion: str):
        for acc in self.acciones_de(seccion):
            self._shortcuts[acc] = DEFAULT_SHORTCUTS.get(acc, "")
        self.save()

    def restaurar_todo(self):
        self._shortcuts = dict(DEFAULT_SHORTCUTS)
        self.save()

    # --- Generación de aceleradores wx ---
    def parsed_to_wx_accelerator(self, action: str, menu_id: int) -> Optional[Tuple[int, int, int]]:
        """Convierte el atajo de 'action' en (flags, keyCode, menuId) para wx.AcceleratorEntry."""
        return self.combo_to_wx(self.obtener_atajo(action), menu_id)

    @staticmethod
    def combo_to_wx(combo: str, menu_id: int) -> Optional[Tuple[int, int, int]]:
        if not combo:
            return None
        flags = wx.ACCEL_NORMAL
        parts = combo.split("+")
        key_str = parts[-1]
        for p in parts[:-1]:
            p = p.lower()
            if p == "ctrl":
                flags |= wx.ACCEL_CTRL
            elif p == "shift":
                flags |= wx.ACCEL_SHIFT
            elif p == "alt":
                flags |= wx.ACCEL_ALT

        teclas_especiales = {
            "SPACE": wx.WXK_SPACE, "RIGHT": wx.WXK_RIGHT, "LEFT": wx.WXK_LEFT,
            "UP": wx.WXK_UP, "DOWN": wx.WXK_DOWN, "ENTER": wx.WXK_RETURN,
            "RETURN": wx.WXK_RETURN, "ESCAPE": wx.WXK_ESCAPE, "ESC": wx.WXK_ESCAPE,
            "TAB": wx.WXK_TAB,
            "F1": wx.WXK_F1, "F2": wx.WXK_F2, "F3": wx.WXK_F3, "F4": wx.WXK_F4,
            "F5": wx.WXK_F5, "F6": wx.WXK_F6, "F7": wx.WXK_F7, "F8": wx.WXK_F8,
            "F9": wx.WXK_F9, "F10": wx.WXK_F10, "F11": wx.WXK_F11, "F12": wx.WXK_F12,
        }
        key_upper = key_str.upper()
        if key_upper in teclas_especiales:
            key_code = teclas_especiales[key_upper]
        elif len(key_str) == 1:
            key_code = ord(key_upper)
        else:
            log.warning(f"Tecla no soportada para aceleradores: {key_str}")
            return None
        return (flags, key_code, menu_id)

    def evento_coincide(self, action: str, event) -> bool:
        """True si el evento de teclado coincide con la tecla configurada para 'action'. Para atajos
        que NO van por la tabla de aceleradores sino por un EVT_CHAR_HOOK (p. ej. en los campos de
        redacción), de modo que respeten la tecla que el usuario haya puesto en el editor."""
        tpl = self.combo_to_wx(self.obtener_atajo(action), 0)
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
        try:
            return event.GetModifiers() == mods and event.GetKeyCode() == keycode
        except Exception:
            return False

    def crear_tabla_aceleradores(self, parent_window: wx.Window, mapeo_id: List[Tuple[str, int]]) -> wx.AcceleratorTable:
        """Crea y asigna una wx.AcceleratorTable a una ventana desde (accion, id_menu)."""
        entries = []
        for accion, mid in mapeo_id:
            entry_tuple = self.parsed_to_wx_accelerator(accion, mid)
            if entry_tuple:
                flags, key_code, menu_id = entry_tuple
                entries.append(wx.AcceleratorEntry(flags, key_code, menu_id))
        table = wx.AcceleratorTable(entries)
        parent_window.SetAcceleratorTable(table)
        return table


# Instancia única del gestor de atajos
shortcut_manager = ShortcutManager()


def coincide(action: str, event) -> bool:
    """True si un evento de teclado (wx EVT_KEY_DOWN/CHAR_HOOK) coincide con la combinación ACTUAL
    configurada para 'action'. Pensado para atajos locales de diálogos (p. ej. el compositor), que
    así respetan en vivo lo que el usuario personalice en el editor de teclas."""
    tpl = ShortcutManager.combo_to_wx(shortcut_manager.obtener_atajo(action), 0)
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
