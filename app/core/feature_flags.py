# -*- coding: utf-8 -*-
"""
Interruptores de funcionalidades (feature flags).

Pensado sobre todo para el cliente de X (Twitter), que es NO oficial y puede romperse
cuando X cambia su API. Toda la app consulta `x_cliente_disponible()` antes de mostrar o
usar nada de X (menú, preferencias, árbol, alta de cuenta, atajos...). Así:

  * Para SACAR UNA VERSIÓN SIN EL CLIENTE DE X (porque se ha roto): poner
    `X_CLIENTE_HABILITADO = False` aquí. Es el ÚNICO cambio necesario: desaparece todo lo
    de X sin tocar nada más y sin errores.
  * Para VOLVER A ACTIVARLO cuando esté arreglado: poner `X_CLIENTE_HABILITADO = True`.

Además el usuario puede desactivarlo desde Preferencias (config `x.enabled`), sin tocar
código. Y si la librería `twikit` no está instalada, el cliente se oculta solo.
"""

# Interruptor MAESTRO del cliente de X. Cámbialo a False para desactivar TODO el cliente
# de X de un solo sitio (y volver a True para reactivarlo). Es la palanca pensada para
# lanzar una actualización sin X y, más adelante, otra con X ya arreglado.
X_CLIENTE_HABILITADO = True


def x_libreria_instalada() -> bool:
    """True si la librería no oficial de X (twikit) está disponible en el entorno."""
    try:
        import twikit  # noqa: F401
        return True
    except Exception:
        return False


def x_cliente_disponible() -> bool:
    """Punto ÚNICO de decisión: ¿debe mostrarse/usarse el cliente de X?

    Combina el interruptor maestro de código, la preferencia del usuario y que la
    librería esté instalada. Todo lo de X en la app debe pasar por aquí."""
    if not X_CLIENTE_HABILITADO:
        return False
    if not x_libreria_instalada():
        return False
    try:
        from app.core.config_manager import config
        return bool(config.get("x", "enabled", True))
    except Exception:
        # Si por lo que sea no hay config todavía, basta con el interruptor y la librería.
        return True


def irc_libreria_instalada() -> bool:
    """True si la librería de IRC (pydle) está disponible (find_spec: no la importa)."""
    try:
        import importlib.util
        return importlib.util.find_spec("pydle") is not None
    except Exception:
        return False


def matrix_libreria_instalada() -> bool:
    """True si la librería de Matrix (matrix-nio) está disponible (find_spec: no la importa)."""
    try:
        import importlib.util
        return importlib.util.find_spec("nio") is not None
    except Exception:
        return False


def xmpp_libreria_instalada() -> bool:
    """True si la librería de XMPP (slixmpp) está disponible (find_spec: no la importa)."""
    try:
        import importlib.util
        return importlib.util.find_spec("slixmpp") is not None
    except Exception:
        return False


def reddit_libreria_instalada() -> bool:
    """True si la librería de Reddit (praw) está disponible (find_spec: no la importa)."""
    try:
        import importlib.util
        return importlib.util.find_spec("praw") is not None
    except Exception:
        return False


def peertube_libreria_instalada() -> bool:
    """True si la librería HTTP de PeerTube (httpx) está disponible (find_spec: no la importa).

    httpx es una dependencia de base del proyecto, así que normalmente siempre está; la
    comprobación es defensiva (si por lo que fuera faltara, PeerTube se oculta solo)."""
    try:
        import importlib.util
        return importlib.util.find_spec("httpx") is not None
    except Exception:
        return False


# Servicios que el usuario puede activar/desactivar. X se controla aparte (kill-switch +
# librería), el resto con una simple lista de desactivados en la configuración. Reddit e IRC/
# Matrix/XMPP requieren además que su librería esté instalada (si no, se ocultan solos).
SERVICIOS = ("mastodon", "bluesky", "rss", "telegram", "x", "irc", "matrix", "xmpp", "reddit",
             "peertube")


def wsocial_libreria_instalada() -> bool:
    """True si la biblioteca de AT Protocol (atproto) está disponible.

    W Social se apoya en la misma biblioteca que Bluesky; si no estuviera, el servicio se oculta
    solo en vez de fallar al usarlo."""
    try:
        import importlib.util
        return importlib.util.find_spec("atproto") is not None
    except Exception:
        return False


def servicio_habilitado(service_id: str) -> bool:
    """Punto ÚNICO de decisión para CUALQUIER servicio: ¿debe mostrarse/usarse?

    - X (Twitter) tiene su propia lógica (interruptor maestro + librería + preferencia).
    - IRC requiere además que su librería (pydle) esté instalada (si no, se oculta solo).
    - Mastodon, Bluesky, RSS, Telegram e IRC se controlan con la preferencia del usuario
      (`general.servicios_desactivados`, una lista con los que están apagados). Por defecto
      todos están activos.

    Todo lo que dependa de un servicio (árbol, menús, alta de cuentas, sondeo, atajos
    Alt+N...) debe pasar por aquí para que activar/desactivar sea coherente en toda la app."""
    if service_id == "x":
        return x_cliente_disponible()
    if service_id == "irc" and not irc_libreria_instalada():
        return False
    if service_id == "matrix" and not matrix_libreria_instalada():
        return False
    if service_id == "xmpp" and not xmpp_libreria_instalada():
        return False
    if service_id == "reddit" and not reddit_libreria_instalada():
        return False
    if service_id == "peertube" and not peertube_libreria_instalada():
        return False
    if service_id == "wsocial" and not wsocial_libreria_instalada():
        return False
    try:
        from app.core.config_manager import config
        desactivados = config.get("general", "servicios_desactivados", []) or []
        return service_id not in desactivados
    except Exception:
        return True


def hay_algun_servicio_activo() -> bool:
    """True si al menos uno de los cinco servicios está activo y utilizable."""
    return any(servicio_habilitado(s) for s in SERVICIOS)


def garantizar_un_servicio_activo() -> None:
    """Red de seguridad: SIEMPRE debe quedar al menos un servicio activo. Si por un config
    editado a mano (o un estado raro) quedaran todos desactivados, se reactiva Mastodon. La
    interfaz ya impide desactivar el último, así que esto solo actúa ante datos corruptos."""
    if hay_algun_servicio_activo():
        return
    try:
        from app.core.config_manager import config
        desactivados = [s for s in (config.get("general", "servicios_desactivados", []) or [])
                        if s != "mastodon"]
        config.set("general", "servicios_desactivados", desactivados)
        config.save()
    except Exception:
        pass


# Servicios que el usuario puede ENCENDER Y APAGAR desde Preferencias -> su sección. Esta
# tupla es la FUENTE ÚNICA: Preferencias la recorre tanto para validar ("debe quedar al menos
# uno activo") como para GUARDAR las casillas. Antes eran dos listas escritas a mano y W Social
# se quedó fuera de la de guardar, así que desmarcarlo no tenía ningún efecto: seguía saliendo
# al reiniciar. Al añadir una red nueva basta con ponerla AQUÍ y crear su casilla `cb_serv_<id>`.
# (X queda fuera a propósito: tiene su propio interruptor, `x.enabled`, y su casilla aparte.)
SERVICIOS_CONMUTABLES = ("mastodon", "bluesky", "rss", "telegram", "irc", "matrix",
                         "xmpp", "reddit", "peertube", "wsocial")


def fijar_servicio_habilitado(service_id: str, habilitado: bool) -> None:
    """Activa o desactiva un servicio (Mastodon, Bluesky, RSS o Telegram) en la
    configuración. No tiene efecto sobre X (que se controla con `x.enabled`)."""
    if service_id == "x":
        try:
            from app.core.config_manager import config
            config.set("x", "enabled", bool(habilitado))
        except Exception:
            pass
        return
    try:
        from app.core.config_manager import config
        desactivados = list(config.get("general", "servicios_desactivados", []) or [])
        if habilitado:
            desactivados = [s for s in desactivados if s != service_id]
        elif service_id not in desactivados:
            desactivados.append(service_id)
        config.set("general", "servicios_desactivados", desactivados)
    except Exception:
        pass
