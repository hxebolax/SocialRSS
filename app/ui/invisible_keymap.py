# -*- coding: utf-8 -*-
"""Motor del mapa de teclas de la INTERFAZ INVISIBLE (teclas globales del sistema).

Es la ÚNICA fuente de verdad de las combinaciones de la interfaz invisible y es TOTALMENTE
INDEPENDIENTE del editor de atajos de ventana (shortcut_manager): otro catálogo, otra persistencia
(config["invisible_shortcuts"]["mappings"]) y otro editor. Aquí viven:

  - el catálogo de acciones (id, etiqueta en español, sección, tecla por defecto),
  - la conversión de una combinación ("ctrl+win+up") a (modificadores wx, código de tecla VK),
  - el registro/retirada de las teclas globales con wx.Window.RegisterHotKey (en caliente),
  - y las comprobaciones de SEGURIDAD anticolisión (4 niveles) al asignar una tecla.

Nota técnica: RegisterHotKey de Windows quiere CÓDIGOS DE TECLA VIRTUALES (VK), no los keycodes de
wx (p. ej. VK de flecha arriba = 0x26, pero wx.WXK_UP = 315). Por eso hay un mapa VK propio. Los
modificadores sí coinciden: wx.MOD_ALT/CONTROL/SHIFT/WIN == MOD_ de Win32 (1/2/4/8).
"""
import wx

from app.core.config_manager import config

try:
    _
except NameError:
    _ = lambda x: x

# --- Secciones (orden de aparición en el editor) ---
SEC_NAV = "Navegación"
SEC_ACCIONES = "Acciones sobre el elemento"
SEC_APP = "Aplicación"
SEC_NOVEDADES = "Novedades"

# Catálogo: (accion_id, etiqueta_es, seccion, tecla_por_defecto).
# Convención de teclas por defecto: Ctrl+Win para navegar, Alt+Win para actuar. TODAS llevan Win
# (o Ctrl+Alt las heredadas), lo que las separa del tecleo normal y de los atajos de ventana.
CATALOGO = [
    # Navegación (sobre el buffer real; contempla bandeja, varias cuentas y servicios activos)
    ("inv_item_prev",     _("Elemento anterior"),                 SEC_NAV, "ctrl+win+up"),
    ("inv_item_next",     _("Elemento siguiente"),                SEC_NAV, "ctrl+win+down"),
    ("inv_buffer_prev",   _("Buffer anterior"),                   SEC_NAV, "ctrl+win+left"),
    ("inv_buffer_next",   _("Buffer siguiente"),                  SEC_NAV, "ctrl+win+right"),
    ("inv_account_prev",  _("Cuenta anterior"),                   SEC_NAV, "ctrl+win+shift+left"),
    ("inv_account_next",  _("Cuenta siguiente"),                  SEC_NAV, "ctrl+win+shift+right"),
    ("inv_service_prev",  _("Servicio anterior (red)"),           SEC_NAV, "ctrl+win+shift+up"),
    ("inv_service_next",  _("Servicio siguiente (red)"),          SEC_NAV, "ctrl+win+shift+down"),
    ("inv_item_first",    _("Primer elemento del buffer"),        SEC_NAV, "ctrl+win+home"),
    ("inv_item_last",     _("Último elemento del buffer"),        SEC_NAV, "ctrl+win+end"),
    ("inv_item_jump_up",  _("Saltar 20 elementos hacia arriba"),  SEC_NAV, "ctrl+win+prior"),
    ("inv_item_jump_down",_("Saltar 20 elementos hacia abajo"),   SEC_NAV, "ctrl+win+next"),
    ("inv_unit_prev",     _("Cambiar a la unidad de movimiento anterior"), SEC_NAV, "ctrl+win+alt+left"),
    ("inv_unit_next",     _("Cambiar a la unidad de movimiento siguiente"), SEC_NAV, "ctrl+win+alt+right"),
    ("inv_move_unit_up",  _("Saltar por la unidad hacia arriba"), SEC_NAV, "ctrl+win+alt+up"),
    ("inv_move_unit_down",_("Saltar por la unidad hacia abajo"),  SEC_NAV, "ctrl+win+alt+down"),
    ("inv_undo_nav",      _("Deshacer el último salto de navegación"), SEC_NAV, "ctrl+win+z"),
    ("inv_repeat_item",   _("Repetir el elemento actual"),        SEC_NAV, "ctrl+win+space"),
    ("inv_say_position",  _("Decir el buffer y la posición"),     SEC_NAV, "ctrl+win+b"),
    ("inv_say_account",   _("Decir la cuenta y el servicio activos"), SEC_NAV, "ctrl+win+a"),
    # Acciones sobre el elemento (despachan según la red del buffer activo)
    ("inv_responder",     _("Responder"),                         SEC_ACCIONES, "alt+win+r"),
    ("inv_like",          _("Me gusta / favorito"),               SEC_ACCIONES, "alt+win+f"),
    ("inv_boost",         _("Impulsar / repost"),                 SEC_ACCIONES, "alt+win+shift+r"),
    ("inv_citar",         _("Citar"),                             SEC_ACCIONES, "alt+win+q"),
    ("inv_ver",           _("Ver publicación"),                   SEC_ACCIONES, "alt+win+v"),
    ("inv_ver_conv",      _("Ver la conversación"),               SEC_ACCIONES, "alt+win+c"),
    ("inv_url",           _("Abrir la URL de la publicación"),    SEC_ACCIONES, "alt+win+return"),
    ("inv_open_browser",  _("Abrir en el navegador"),             SEC_ACCIONES, "ctrl+alt+win+return"),
    ("inv_audio",         _("Reproducir / detener el audio o vídeo"), SEC_ACCIONES, "alt+win+shift+return"),
    ("inv_copiar",        _("Copiar al portapapeles"),            SEC_ACCIONES, "alt+win+shift+c"),
    ("inv_traducir",      _("Traducir"),                          SEC_ACCIONES, "alt+win+t"),
    ("inv_ocr",           _("Extraer texto de la imagen (OCR)"),  SEC_ACCIONES, "alt+win+o"),
    ("inv_describir",     _("Describir la imagen con IA"),        SEC_ACCIONES, "alt+win+i"),
    ("inv_seguir",        _("Seguir o dejar de seguir al autor"), SEC_ACCIONES, "alt+win+s"),
    ("inv_rss_leido",     _("RSS: marcar leído o no leído"),      SEC_ACCIONES, "alt+win+m"),
    ("inv_rss_lector",    _("RSS: abrir el lector limpio"),       SEC_ACCIONES, "alt+win+e"),
    ("inv_context_menu",  _("Abrir el menú contextual del elemento"), SEC_ACCIONES, "ctrl+win+alt+m"),
    # Aplicación
    ("inv_mostrar_ocultar", _("Mostrar u ocultar la ventana"),   SEC_APP, "ctrl+win+w"),
    ("inv_nuevo",         _("Nueva publicación"),                 SEC_APP, "ctrl+win+n"),
    ("inv_crosspost",     _("Publicación cruzada (varias redes)"),SEC_APP, "ctrl+win+x"),
    ("inv_actualizar",    _("Actualizar el buffer activo"),       SEC_APP, "ctrl+win+u"),
    ("inv_cargar_mas",    _("Cargar elementos más antiguos"),     SEC_APP, "alt+win+prior"),
    ("inv_preferencias",  _("Abrir Preferencias"),                SEC_APP, "ctrl+win+o"),
    ("inv_editor_teclas", _("Abrir el editor de teclas invisibles"), SEC_APP, "ctrl+win+k"),
    ("inv_salir",         _("Salir de SocialRSS"),                SEC_APP, "ctrl+win+f4"),
    # Novedades (algunas heredan las teclas Ctrl+Alt+... que ya existían)
    ("inv_ultima_mencion",_("Decir la última mención"),           SEC_NOVEDADES, "ctrl+alt+m"),
    ("inv_siguiente_no_leido", _("Decir el siguiente artículo RSS sin leer"), SEC_NOVEDADES, "ctrl+alt+n"),
    ("inv_publicar_rapido", _("Publicar rápido (traer la ventana)"), SEC_NOVEDADES, "ctrl+alt+u"),
    ("inv_que_suena",     _("Decir qué se está reproduciendo"),   SEC_NOVEDADES, "ctrl+alt+y"),
    ("inv_ponte_al_dia",  _("Ponte al día (resumen con IA)"),     SEC_NOVEDADES, "ctrl+win+shift+y"),
    ("inv_centro_notif",  _("Abrir el centro de notificaciones"), SEC_NOVEDADES, "ctrl+win+shift+a"),
    ("inv_contadores",    _("Decir los contadores globales"),     SEC_NOVEDADES, "ctrl+win+shift+b"),
    ("inv_leer_rafaga",   _("Leer los últimos elementos seguidos"), SEC_NOVEDADES, "ctrl+win+shift+space"),
]

DEFAULTS = {a: t for a, _e, _s, t in CATALOGO}
_ETIQUETAS = {a: e for a, e, _s, _t in CATALOGO}
_SECCION = {a: s for a, _e, s, _t in CATALOGO}

# --- Perfiles de mapa (Windows 10 / Windows 11) ---
# El mapa por defecto (Ctrl+Win para navegar, Alt+Win para actuar) va fino en Windows 10. En Windows
# 11 el sistema RESERVA las combinaciones Windows+Alt (Game Bar, Win+Alt+K silencia el micrófono…),
# así que el mapa "Windows 11" saca de ahí TODO: como TWBlue y FastSM, lleva la NAVEGACIÓN y las
# ACCIONES a Ctrl+Alt+Windows (3 modificadores, el espacio seguro), deja la APLICACIÓN en Ctrl+Win
# (que Windows 11 no reserva) y reubica las unidades de movimiento para que no choquen con las
# flechas de navegación. De paso, la navegación deja de usar Ctrl+Win+flechas (que en Windows 10 y 11
# cambia de escritorio virtual). Es un mapa DISTINTO de verdad, no solo un Ctrl añadido a las acciones.
PERFILES = ("win10", "win11")
PERFILES_LEGIBLES = {"win10": _("Windows 10"), "win11": _("Windows 11")}

# Mapa EXPLÍCITO de Windows 11 (revisado para no tener colisiones; ver test_invisible_win11.py).
_WIN11_MAP = {
    # Navegación: de Ctrl+Win a Ctrl+Alt+Win (flechas y saltos).
    "inv_item_prev": "ctrl+alt+win+up", "inv_item_next": "ctrl+alt+win+down",
    "inv_buffer_prev": "ctrl+alt+win+left", "inv_buffer_next": "ctrl+alt+win+right",
    "inv_account_prev": "ctrl+alt+win+shift+left", "inv_account_next": "ctrl+alt+win+shift+right",
    "inv_service_prev": "ctrl+alt+win+shift+up", "inv_service_next": "ctrl+alt+win+shift+down",
    "inv_item_first": "ctrl+alt+win+home", "inv_item_last": "ctrl+alt+win+end",
    "inv_item_jump_up": "ctrl+alt+win+prior", "inv_item_jump_down": "ctrl+alt+win+next",
    # Unidades de movimiento: a Ctrl+Alt+Win+Shift+(Re/Av Pág, Inicio, Fin) para NO chocar con las
    # flechas, que ahora las usa la navegación.
    "inv_unit_prev": "ctrl+alt+win+shift+prior", "inv_unit_next": "ctrl+alt+win+shift+next",
    "inv_move_unit_up": "ctrl+alt+win+shift+home", "inv_move_unit_down": "ctrl+alt+win+shift+end",
    "inv_undo_nav": "ctrl+alt+win+z", "inv_repeat_item": "ctrl+alt+win+space",
    "inv_say_position": "ctrl+alt+win+b", "inv_say_account": "ctrl+alt+win+a",
    # Acciones: de Alt+Win a Ctrl+Alt+Win.
    "inv_responder": "ctrl+alt+win+r", "inv_like": "ctrl+alt+win+f",
    "inv_boost": "ctrl+alt+win+shift+r", "inv_citar": "ctrl+alt+win+q",
    "inv_ver": "ctrl+alt+win+v", "inv_ver_conv": "ctrl+alt+win+c",
    "inv_url": "ctrl+alt+win+return", "inv_open_browser": "ctrl+alt+win+g",
    "inv_audio": "ctrl+alt+win+shift+return", "inv_copiar": "ctrl+alt+win+shift+c",
    "inv_traducir": "ctrl+alt+win+t", "inv_ocr": "ctrl+alt+win+o",
    "inv_describir": "ctrl+alt+win+i", "inv_seguir": "ctrl+alt+win+s",
    "inv_rss_leido": "ctrl+alt+win+m", "inv_rss_lector": "ctrl+alt+win+e",
    "inv_context_menu": "ctrl+alt+win+j",
    # Aplicación: se queda en Ctrl+Win (Windows 11 no lo reserva). Solo 'cargar_mas' sale de Alt+Win.
    "inv_mostrar_ocultar": "ctrl+win+w", "inv_nuevo": "ctrl+win+n",
    "inv_crosspost": "ctrl+win+x", "inv_actualizar": "ctrl+win+u",
    "inv_cargar_mas": "ctrl+win+prior", "inv_preferencias": "ctrl+win+o",
    "inv_editor_teclas": "ctrl+win+k", "inv_salir": "ctrl+win+f4",
    # Novedades: sin cambios (ya usan Ctrl+Alt sin Win, o Ctrl+Win+Shift, ambos a salvo en Win11).
    "inv_ultima_mencion": "ctrl+alt+m", "inv_siguiente_no_leido": "ctrl+alt+n",
    "inv_publicar_rapido": "ctrl+alt+u", "inv_que_suena": "ctrl+alt+y",
    "inv_ponte_al_dia": "ctrl+win+shift+y", "inv_centro_notif": "ctrl+win+shift+a",
    "inv_contadores": "ctrl+win+shift+b", "inv_leer_rafaga": "ctrl+win+shift+space",
}


def defaults_perfil(perfil: str) -> dict:
    """Devuelve el mapa de fábrica para el perfil dado ('win10' = catálogo tal cual; 'win11' = el
    mapa explícito Windows 11). Para cualquier acción NUEVA que no esté en _WIN11_MAP, se cae a la
    regla genérica (subir Alt+Win a Ctrl+Alt+Win) para no dejarla en una combinación reservada."""
    if perfil != "win11":
        return dict(DEFAULTS)
    d = {}
    for a, t in DEFAULTS.items():
        if a in _WIN11_MAP:
            d[a] = _WIN11_MAP[a]
        else:
            mods, _b = _tokens(t)
            d[a] = ("ctrl+" + t) if ("win" in mods and "alt" in mods and "ctrl" not in mods) else t
    return d


def es_windows_11() -> bool:
    """True si el sistema es Windows 11 (build >= 22000). En cualquier otro caso, False."""
    try:
        import sys
        v = sys.getwindowsversion()
        return getattr(v, "major", 0) == 10 and getattr(v, "build", 0) >= 22000
    except Exception:
        return False

# --- Modificadores y teclas ---
_MODS = {"ctrl": wx.MOD_CONTROL, "control": wx.MOD_CONTROL,
         "alt": wx.MOD_ALT, "shift": wx.MOD_SHIFT,
         "win": wx.MOD_WIN, "windows": wx.MOD_WIN}
_MODS_ORDEN = ("ctrl", "alt", "shift", "win")  # orden canónico para mostrar/comparar
_MODS_LEGIBLES = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Mayús", "win": "Windows"}

# Nombre de tecla -> (código de tecla VIRTUAL de Windows, etiqueta legible).
_TECLAS_VK = {
    "up": (0x26, _("Flecha arriba")), "down": (0x28, _("Flecha abajo")),
    "left": (0x25, _("Flecha izquierda")), "right": (0x27, _("Flecha derecha")),
    "home": (0x24, "Inicio"), "end": (0x23, "Fin"),
    "prior": (0x21, _("Retroceder página")), "pageup": (0x21, _("Retroceder página")),
    "next": (0x22, _("Avanzar página")), "pagedown": (0x22, _("Avanzar página")),
    "return": (0x0D, "Intro"), "enter": (0x0D, "Intro"),
    "space": (0x20, "Espacio"), "delete": (0x2E, "Suprimir"),
    "escape": (0x1B, "Escape"), "tab": (0x09, "Tabulador"),
    "insert": (0x2D, "Insert"), "back": (0x08, "Retroceso"),
}
for _n in range(1, 25):  # F1..F24 = 0x70..
    _TECLAS_VK[f"f{_n}"] = (0x6F + _n, f"F{_n}")

# --- Lista negra: combinaciones que NUNCA se permiten (reservadas del sistema o de lectores) ---
# Se comparan en forma canónica "mods+tecla" (mods en orden ctrl,alt,shift,win).
_LISTA_NEGRA = {
    "win+l", "win+d", "win+e", "win+r", "win+m", "win+x", "win+i", "win+a", "win+s",
    "win+tab", "win+up", "win+down", "win+left", "win+right", "win+home",
    "ctrl+alt+delete", "ctrl+shift+escape", "alt+tab", "alt+f4", "alt+escape",
    "alt+space", "ctrl+escape",
}
for _d in "0123456789":
    _LISTA_NEGRA.add(f"win+{_d}")  # Win+número = barra de tareas


def _tokens(tecla: str):
    """Divide una combinación en (lista de modificadores canónicos, nombre de tecla base)."""
    partes = [p.strip().lower() for p in (tecla or "").split("+") if p.strip()]
    if not partes:
        return [], ""
    base = partes[-1]
    mods = [_MODS_alias(p) for p in partes[:-1] if _MODS_alias(p)]
    return mods, base


def _MODS_alias(tok: str):
    if tok in ("ctrl", "control"):
        return "ctrl"
    if tok == "alt":
        return "alt"
    if tok == "shift":
        return "shift"
    if tok in ("win", "windows"):
        return "win"
    return None


def canonica(tecla: str) -> str:
    """Forma canónica para comparar y detectar colisiones (mods en orden fijo + tecla base)."""
    mods, base = _tokens(tecla)
    orden = [m for m in _MODS_ORDEN if m in mods]
    return "+".join(orden + [base]) if base else ""


def combo_legible(tecla: str) -> str:
    """Combinación en español para mostrar (Ctrl+Windows+Flecha arriba)."""
    mods, base = _tokens(tecla)
    orden = [_MODS_LEGIBLES[m] for m in _MODS_ORDEN if m in mods]
    if not base:
        return ""
    nombre = _TECLAS_VK.get(base, (None, base.upper() if len(base) == 1 else base))[1]
    return "+".join(orden + [nombre])


def parse_wx(tecla: str):
    """Devuelve (modificadores_wx, codigo_vk) para RegisterHotKey, o None si la tecla no es válida."""
    mods, base = _tokens(tecla)
    if not base:
        return None
    mod_flags = 0
    for m in mods:
        mod_flags |= _MODS.get(m, 0)
    if base in _TECLAS_VK:
        vk = _TECLAS_VK[base][0]
    elif len(base) == 1 and (base.isalnum()):
        vk = ord(base.upper())  # letras y dígitos: el VK coincide con el ASCII en mayúscula
    else:
        return None
    return (mod_flags, vk)


def validar_tecla_base(base: str):
    """(True, None) si el nombre de tecla base es válido; (False, motivo) si no."""
    base = (base or "").strip().lower()
    if not base:
        return False, _("Falta la tecla.")
    if base in _MODS or base in ("windows", "control"):
        return False, _("«{0}» es un modificador, no una tecla. Elige una tecla como f, 1, flecha arriba…").format(base)
    if base in _TECLAS_VK:
        return True, None
    if len(base) == 1 and base.isalnum():
        return True, None
    return False, _("«{0}» no es una tecla reconocida. Usa una letra o número, o un nombre como "
                    "flecha arriba, inicio, intro, suprimir, f5…").format(base)


def es_combinacion_permitida(tecla: str):
    """NIVELES 1 y 2 de seguridad. Devuelve (True, None) o (False, motivo).

    Nivel 1: debe llevar Windows, o Ctrl+Alt juntos (así no choca con el tecleo ni con los atajos
    de ventana, que usan Ctrl/Alt/Mayús SIN Windows).
    Nivel 2: no puede ser una combinación reservada del sistema o de los lectores de pantalla."""
    mods, base = _tokens(tecla)
    if not base:
        return False, _("Combinación vacía.")
    tiene_win = "win" in mods
    tiene_ctrl_alt = "ctrl" in mods and "alt" in mods
    if not (tiene_win or tiene_ctrl_alt):
        return False, _("Por seguridad, la tecla debe incluir Windows (o Ctrl y Alt juntos) para "
                        "no chocar con la escritura ni con los atajos normales de la aplicación.")
    # Teclas de los lectores de pantalla: Insert y Bloq Mayús no se permiten como tecla base.
    if base in ("insert", "capslock", "numlock"):
        return False, _("Esa tecla la usan los lectores de pantalla; elige otra.")
    if canonica(tecla) in _LISTA_NEGRA:
        return False, _("Esa combinación la reserva Windows; elige otra.")
    return True, None


class InvisibleKeymap:
    """Gestiona el mapa de la interfaz invisible: persistencia y registro de teclas globales."""

    _ID_BASE = 0xB100  # rango propio de ids de hotkey, separado del reproductor (0xB001)

    def __init__(self):
        self._map = {}
        self._perfil = "win10"
        self._defaults = dict(DEFAULTS)
        self.load()
        self._registradas = False
        self._id_por_accion = {}

    def load(self):
        # El PERFIL (win10/win11) decide el mapa de fábrica; encima se aplican tus personalizaciones.
        self._perfil = config.get("invisible_shortcuts", "perfil", "win10")
        if self._perfil not in PERFILES:
            self._perfil = "win10"
        self._defaults = defaults_perfil(self._perfil)
        self._map = dict(self._defaults)
        try:
            custom = config.get("invisible_shortcuts", "mappings", {}) or {}
            for a, t in custom.items():
                if a in self._defaults and isinstance(t, str):
                    self._map[a] = t
        except Exception:
            pass

    def guardar(self):
        # Solo se guardan las que difieren del valor de fábrica DEL PERFIL (mapa pequeño y limpio).
        custom = {a: t for a, t in self._map.items() if self._defaults.get(a) != t}
        config.set("invisible_shortcuts", "mappings", custom)

    def perfil(self) -> str:
        return self._perfil

    def set_perfil(self, perfil: str):
        """Cambia el mapa base (Windows 10/11). Descarta las personalizaciones (eran del otro mapa) y
        deja el de fábrica del perfil elegido. Recuerda llamar a registrar_todas() después."""
        if perfil not in PERFILES:
            return
        self._perfil = perfil
        self._defaults = defaults_perfil(perfil)
        self._map = dict(self._defaults)
        config.set("invisible_shortcuts", "perfil", perfil)
        config.set("invisible_shortcuts", "mappings", {})

    def tecla(self, accion: str) -> str:
        return self._map.get(accion, self._defaults.get(accion, ""))

    def set_tecla(self, accion: str, tecla: str):
        self._map[accion] = tecla or ""

    def restaurar(self, accion: str):
        self._map[accion] = self._defaults.get(accion, "")

    def restaurar_todas(self):
        self._map = dict(self._defaults)

    def secciones(self):
        vistas, orden = set(), []
        for _a, _e, s, _t in CATALOGO:
            if s not in vistas:
                vistas.add(s)
                orden.append(s)
        return orden

    def acciones_de(self, seccion: str):
        return [a for a, _e, s, _t in CATALOGO if s == seccion]

    @staticmethod
    def etiqueta(accion: str) -> str:
        return _ETIQUETAS.get(accion, accion)

    def conflicto_interno(self, tecla: str, excepto: str = None):
        """Devuelve el accion_id de otra acción invisible que ya usa esa tecla, o None (nivel 3a)."""
        can = canonica(tecla)
        for a, t in self._map.items():
            if a == excepto or not t:
                continue
            if canonica(t) == can:
                return a
        return None

    @staticmethod
    def conflicto_con_app(tecla: str):
        """Devuelve la etiqueta del atajo de VENTANA que ya usa esa tecla, o None (nivel 3b).
        Solo puede colisionar si la tecla invisible NO lleva Windows (los de ventana no usan Win)."""
        if "win" in _tokens(tecla)[0]:
            return None  # los atajos de ventana nunca llevan Windows: imposible colisión
        try:
            from app.ui.shortcut_manager import shortcut_manager, _ETIQUETAS as APP_ETI
            can = canonica(tecla)
            for accion, combo in shortcut_manager._shortcuts.items():
                if combo and canonica(combo.replace("Ctrl", "ctrl").replace("Shift", "shift")
                                      .replace("Alt", "alt").replace("Win", "win")) == can:
                    return APP_ETI.get(accion, accion)
        except Exception:
            pass
        return None

    def registrar_todas(self, frame):
        """Registra TODAS las teclas válidas como hotkeys globales y las enlaza al despachador
        del frame (frame._inv_dispatch(accion_id)). Idempotente: desregistra antes."""
        self.desregistrar_todas(frame)
        self._id_por_accion = {}
        registradas = 0
        for idx, (accion, _e, _s, _t) in enumerate(CATALOGO):
            tecla = self._map.get(accion, "")
            if not tecla:
                continue
            ok, _motivo = es_combinacion_permitida(tecla)
            if not ok:
                continue
            parsed = parse_wx(tecla)
            if not parsed:
                continue
            hk_id = self._ID_BASE + idx
            try:
                if frame.RegisterHotKey(hk_id, parsed[0], parsed[1]):
                    frame.Bind(wx.EVT_HOTKEY,
                               lambda e, a=accion: frame._inv_dispatch(a), id=hk_id)
                    self._id_por_accion[accion] = hk_id
                    registradas += 1
            except Exception:
                pass
        self._registradas = registradas > 0
        return registradas

    def desregistrar_todas(self, frame):
        for accion, hk_id in list(self._id_por_accion.items()):
            try:
                frame.UnregisterHotKey(hk_id)
                frame.Unbind(wx.EVT_HOTKEY, id=hk_id)
            except Exception:
                pass
        self._id_por_accion = {}
        self._registradas = False

    @staticmethod
    def probar_registro(frame, tecla: str) -> bool:
        """NIVEL 4: registro de PRUEBA real contra Windows. True si la combinación está libre en
        TODO el sistema (ninguna otra aplicación la posee). Registra y desregistra al momento."""
        parsed = parse_wx(tecla)
        if not parsed:
            return False
        _ID_PRUEBA = 0xB1FF
        try:
            if frame.RegisterHotKey(_ID_PRUEBA, parsed[0], parsed[1]):
                frame.UnregisterHotKey(_ID_PRUEBA)
                return True
        except Exception:
            pass
        return False


# Instancia única.
invisible_keymap = InvisibleKeymap()
