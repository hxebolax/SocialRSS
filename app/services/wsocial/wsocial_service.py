# -*- coding: utf-8 -*-
"""
Servicio central de W Social (AT Protocol) usando la biblioteca `atproto`.

Ensamblador: `WSocialService` hereda de los mixins por dominio. Los símbolos comunes viven en
`wsocial_common` y la resolución de identidad (handle -> DID -> PDS) en `wsocial_identidad`.

W Social usa los mismos lexicons que Bluesky, así que se comparte con él lo que es lógica PURA de
AT Protocol (el analizador de texto enriquecido). Lo demás es propio: cada red tiene sus cuentas,
sus tablas, su configuración y su ciclo de vida, de modo que trabajar en una nunca puede romper
la otra.
"""
import threading

from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, Optional, HAS_ATPROTO, Client, models, WSocialServiceError,
    db, security, task_manager, config, log, json)

from app.services.wsocial.wsocial_sesion import _SesionMixin
from app.services.wsocial.wsocial_timeline import _TimelineMixin
from app.services.wsocial.wsocial_publicar import _PublicarMixin
from app.services.wsocial.wsocial_social import _SocialMixin
from app.services.wsocial.wsocial_chat import _ChatMixin
from app.services.wsocial.wsocial_moderacion import _ModeracionMixin
from app.services.wsocial.wsocial_avanzado import _AvanzadoMixin


class WSocialService(_SesionMixin, _TimelineMixin, _PublicarMixin, _SocialMixin,
                     _ChatMixin, _ModeracionMixin, _AvanzadoMixin):
    """Administrador de cuentas y operaciones de W Social."""

    def __init__(self):
        self._clients: Dict[int, Any] = {}
        self._chats: Dict[int, Any] = {}              # cliente con proxy de chat
        self._me: Dict[int, Dict[str, str]] = {}      # account_id -> {did, handle, pds}
        # Cursor de paginación por (cuenta, buffer), para "cargar más".
        self._cursors: Dict[str, Optional[str]] = {}
        # Buffers que el servidor ya declaró agotados (no hay más contenido antiguo).
        self._agotados: set = set()
        # Un lock por cuenta para crear el Client UNA sola vez: si varias tareas reanudaran la
        # sesión a la vez con el mismo token de refresco, el servidor las revocaría todas menos una.
        self._client_locks: Dict[int, Any] = {}
        self._client_locks_guard = threading.Lock()
        # Cuentas con la sesión caducada: fallan rápido sin volver a golpear el servidor.
        self._sesiones_revocadas: set = set()
        # Cuentas en pausa tras un límite de peticiones, con la hora hasta la que esperar.
        self._cooldown_429: Dict[int, float] = {}
        # Caché handle -> (did, momento) para no resolver la misma mención una y otra vez.
        self._did_cache: Dict[str, Any] = {}
        # Vigilantes de tiempo real por cuenta.
        self._tiempo_real: Dict[int, Any] = {}
        # Hilo que mantiene VIVAS las sesiones (ver iniciar_mantenimiento_sesion).
        self._mantenimiento = None
        self._parar_mantenimiento = threading.Event()

    # ------------------------------------------------------------------
    # LANZADOR COMÚN DE TAREAS
    # ------------------------------------------------------------------
    def _tarea(self, funcion: Callable, callback: Optional[Callable] = None,
               traducir: bool = True, sonidos: bool = False, silencioso: bool = False):
        """Ejecuta `funcion` en segundo plano y entrega el resultado al callback.

        Unifica lo que toda operación de red necesita: no bloquear nunca la interfaz, traducir
        los errores de la API a mensajes claros en español y, si se pide, reproducir el sonido de
        hecho o de error. El callback recibe (ok, resultado_o_error), como el resto de servicios
        de la aplicación."""
        def _fin(resultado, error):
            if callback is None:
                if error and not silencioso:
                    log.debug(f"W Social: tarea sin callback terminó con error: {error}")
                return
            if error:
                mensaje = self._mensaje_error(error) if traducir else str(error)
                callback(False, mensaje)
            else:
                callback(True, resultado)

        task_manager.start_task(
            funcion, callback=_fin, pass_context=True,
            success_sound=("ready" if sonidos else None),
            error_sound=("error" if sonidos else None))

    # ------------------------------------------------------------------
    # TIEMPO REAL
    # ------------------------------------------------------------------
    def iniciar_tiempo_real(self, account_id: int, al_haber_novedades=None,
                            al_llegar_mensaje=None, intervalo: int = 60):
        """Arranca la vigilancia de novedades de una cuenta (notificaciones y chat).

        Cada cuenta tiene su propio vigilante y su propio intervalo: encender W Social no altera
        el tiempo real de las demás redes."""
        from app.services.wsocial.wsocial_tiempo_real import WSocialTiempoReal
        vigilante = self._tiempo_real.get(account_id)
        if vigilante is None:
            vigilante = WSocialTiempoReal(self, account_id, al_haber_novedades, al_llegar_mensaje)
            self._tiempo_real[account_id] = vigilante
        vigilante.iniciar(intervalo)
        return vigilante

    def detener_tiempo_real(self, account_id: int = None):
        """Para la vigilancia de una cuenta (o de todas si no se indica ninguna)."""
        objetivos = ([account_id] if account_id is not None else list(self._tiempo_real.keys()))
        for acc in objetivos:
            vigilante = self._tiempo_real.pop(acc, None)
            if vigilante is not None:
                try:
                    vigilante.detener()
                except Exception as e:
                    log.debug(f"W Social: no se pudo detener la vigilancia de {acc}: {e}")

    def tiempo_real_activo(self, account_id: int) -> bool:
        vigilante = self._tiempo_real.get(account_id)
        return bool(vigilante is not None and vigilante.activo)

    # ------------------------------------------------------------------
    # CUENTAS
    # ------------------------------------------------------------------
    @staticmethod
    def cuentas() -> list:
        """Cuentas de W Social dadas de alta, en el orden del usuario."""
        return db.query(
            "SELECT id, name FROM accounts WHERE service_id = 'wsocial' AND active = 1 "
            "ORDER BY position, id")

    @staticmethod
    def hay_cuentas() -> bool:
        fila = db.query_one("SELECT COUNT(*) FROM accounts WHERE service_id = 'wsocial'")
        return bool(fila and fila[0])

    # ------------------------------------------------------------------
    # MANTENIMIENTO DE LA SESIÓN (para no tener que volver a autorizar nunca)
    # ------------------------------------------------------------------
    # En AT Protocol la sesión se renueva sola, pero SOLO cuando se hace alguna petición: la
    # biblioteca refresca el token de acceso (dura unas 2 horas) poco antes de que caduque, y al
    # hacerlo el servidor entrega TAMBIÉN un token de refresco nuevo, que vuelve a contar desde
    # cero. Es decir: mientras haya actividad de vez en cuando, la sesión no caduca jamás.
    #
    # El problema aparece cuando una red se usa poco: si pasan semanas sin ninguna petición, el
    # token de refresco envejece hasta caducar y hay que volver a autorizar a mano. Por eso aquí
    # se hace una petición mínima cada pocas horas: no descarga contenido ni gasta apenas datos,
    # solo obliga a renovar la sesión y a guardarla, que es lo que la mantiene viva sola.
    INTERVALO_MANTENIMIENTO = 3 * 60 * 60      # 3 horas (el token de acceso dura ~2)
    ESPERA_INICIAL_MANTENIMIENTO = 120         # 2 minutos: no competir con la carga del arranque

    def iniciar_mantenimiento_sesion(self):
        """Arranca el hilo que mantiene vivas las sesiones de todas las cuentas de W Social.

        Es independiente del tiempo real: funciona aunque tengas los avisos apagados y aunque no
        entres nunca en esta red."""
        if self._mantenimiento is not None and self._mantenimiento.is_alive():
            return
        self._parar_mantenimiento.clear()
        self._mantenimiento = threading.Thread(
            target=self._bucle_mantenimiento, name="wsocial-sesion", daemon=True)
        self._mantenimiento.start()

    def _bucle_mantenimiento(self):
        # Prioridad baja: es tarea de fondo y nunca debe quitarle turno al lector de pantalla.
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        except Exception:
            pass
        if self._parar_mantenimiento.wait(self.ESPERA_INICIAL_MANTENIMIENTO):
            return
        while not self._parar_mantenimiento.is_set():
            try:
                self.mantener_sesiones()
            except Exception as e:
                log.debug(f"W Social: fallo en el mantenimiento de sesiones: {e}")
            finally:
                try:
                    db.cerrar_lectura_hilo()
                except Exception:
                    pass
            if self._parar_mantenimiento.wait(self.INTERVALO_MANTENIMIENTO):
                return

    def mantener_sesiones(self) -> int:
        """Renueva la sesión de cada cuenta con una petición mínima. Devuelve cuántas se han
        podido mantener. Las cuentas cuya sesión ya está caducada se saltan (ahí no hay nada que
        renovar: hace falta volver a autorizar)."""
        vivas = 0
        for fila in self.cuentas():
            acc = fila[0]
            if self.sesion_revocada(acc):
                continue
            try:
                cliente = self.obtener_cliente(acc)
                # Petición mínima: devuelve solo los datos de tu propia sesión. Si el token de
                # acceso está caducado o a punto, la biblioteca lo renueva durante esta llamada
                # y el guardado automático conserva el token nuevo.
                cliente.com.atproto.server.get_session()
                vivas += 1
                log.debug(f"W Social: sesión de la cuenta {acc} renovada.")
            except Exception as e:
                log.debug(f"W Social: no se pudo renovar la sesión de la cuenta {acc}: {e}")
        return vivas

    def cerrar(self):
        """Cierre ordenado al salir de la aplicación: para los vigilantes y suelta los clientes."""
        self.detener_tiempo_real()
        self._parar_mantenimiento.set()
        self._clients.clear()
        self._chats.clear()


# Instancia única del servicio de W Social
wsocial_service = WSocialService()
