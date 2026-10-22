# -*- coding: utf-8 -*-
"""Fachada de W Social en la interfaz: el controlador ensamblado a partir de sus mixins.

Toda la lógica de W Social vive aquí (y en los mixins) para que el MainFrame solo tenga que
guardar `active_wsocial_account_id` y delegar, igual que hace con las demás redes.
"""
import builtins
if not hasattr(builtins, "_"):
    # Salvaguarda: BUFFER_LABELS usa _() al importar. En la aplicación real, i18n ya instaló la
    # traducción antes de cargar la interfaz; esto solo cubre los imports sueltos (pruebas).
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.speech import notificar
from app.services.wsocial import wsocial_service

from .wsocial_controller_carga import _CargaMixin
from .wsocial_controller_acciones import _AccionesMixin
from .wsocial_controller_social import _SocialMixin


class WSocialController(_CargaMixin, _AccionesMixin, _SocialMixin):
    """Lógica de W Social enganchada al MainFrame."""

    BUFFER_LABELS = {
        "home": _("Inicio"),
        "notifications": _("Notificaciones"),
        "mentions": _("Menciones"),
        "sent": _("Enviados"),
        "likes": _("Me gusta"),
        "bookmarks": _("Marcadores"),
        "drafts": _("Borradores"),
        "feeds": _("Feeds"),
        "lists": _("Listas"),
        "starterpacks": _("Paquetes de inicio"),
        "trending": _("Tendencias"),
        "followers": _("Seguidores"),
        "follows": _("Siguiendo"),
        "blocks": _("Bloqueados"),
        "mutes": _("Silenciados"),
        "convos": _("Mensajes directos"),
        "requests": _("Solicitudes de mensaje"),
    }
    _TODOS = ["home", "notifications", "mentions", "sent", "likes", "bookmarks", "drafts",
              "feeds", "lists", "starterpacks", "trending", "followers", "follows",
              "blocks", "mutes", "convos", "requests"]

    def __init__(self, frame):
        self.f = frame
        self._cols_posts = []
        self._tendencias = []
        self._menu_tiempo_real = None
        self._reautorizando = False
        # El servicio avisa en cuanto detecta que la sesión ya no vale (venga de donde venga:
        # una acción tuya, el sondeo o el tiempo real). Así se ofrece renovarla UNA vez, sin
        # tener que comprobarlo en cada operación por separado.
        try:
            from app.core.event_bus import event_bus
            event_bus.subscribe("wsocial_sesion_caducada", self._on_sesion_caducada)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # ALTA DE CUENTAS
    # ------------------------------------------------------------------
    def anadir_cuenta(self, parent=None):
        """Abre el asistente de alta de cuenta. Devuelve True si se añadió alguna."""
        from .wsocial_dialogs import WSocialLoginDialog
        dlg = WSocialLoginDialog(parent or self.f)
        ok = dlg.ShowModal() == wx.ID_OK and bool(dlg.account_id)
        dlg.Destroy()
        if ok:
            try:
                self.f._cargar_navegacion()
            except Exception:
                pass
        return ok

    def reautorizar_cuenta(self, account_id, parent=None):
        """Renueva la sesión de una cuenta existente sin perder sus datos locales."""
        from .wsocial_dialogs import WSocialLoginDialog
        dlg = WSocialLoginDialog(parent or self.f, account_id=account_id)
        ok = dlg.ShowModal() == wx.ID_OK
        dlg.Destroy()
        if ok:
            try:
                self.f._cargar_navegacion()
            except Exception:
                pass
        return ok

    def es_error_de_sesion(self, error, account_id=None) -> bool:
        """True si ese error es de sesión caducada o revocada. Se mira primero la marca del
        servicio (fiable) y, como respaldo, el mensaje conocido."""
        acc = account_id or getattr(self.f, "active_wsocial_account_id", None)
        try:
            if acc and wsocial_service.sesion_revocada(acc):
                return True
        except Exception:
            pass
        try:
            from app.services.wsocial.wsocial_sesion import MSG_SESION_CADUCADA
            return MSG_SESION_CADUCADA in str(error or "")
        except Exception:
            return False

    def ofrecer_reautorizar(self, error, account_id=None) -> bool:
        """Si el fallo es porque la sesión caducó, PREGUNTA si se quiere volver a autorizar la
        cuenta ahora y, si el usuario acepta, abre el mismo cuadro que usa Bluesky. Devuelve
        True si se ha ocupado del error (haya reautorizado o no), para que quien llama no
        suelte además el aviso técnico.

        La sesión caducada no es un error cualquiera: hasta que no se renueva, la cuenta no
        vuelve a funcionar. Por eso se ofrece la solución en el momento en vez de dejar un
        mensaje que obliga a buscar dónde arreglarlo."""
        acc = account_id or getattr(self.f, "active_wsocial_account_id", None)
        if not acc or not self.es_error_de_sesion(error, acc):
            return False
        # Una sola pregunta a la vez: el fallo de sesión puede llegar por varias vías casi a
        # la vez (lo que estabas haciendo + el sondeo de fondo) y no debe salir dos cuadros.
        if getattr(self, "_reautorizando", False):
            return True
        self._reautorizando = True
        try:
            from .accessible_controls import confirmar
            if confirmar(self.f,
                         _("La sesión de W Social ha caducado y hay que volver a autorizarla.\n\n"
                           "¿Quieres hacerlo ahora? Se conservan tus buffers y todos tus datos: "
                           "solo hay que escribir de nuevo tu usuario y tu contraseña de "
                           "aplicación."),
                         _("Sesión de W Social caducada")) == wx.ID_YES:
                self.reautorizar_cuenta(acc)
            else:
                notificar(_("Cuando quieras, vuelve a autorizar la cuenta desde Archivo, "
                            "Gestionar cuentas, Volver a autorizar."))
        finally:
            self._reautorizando = False
        return True

    def _on_sesion_caducada(self, account_id):
        """Aviso del servicio de que una sesión ha caducado. Llega desde un hilo de fondo (el
        sondeo, el tiempo real o cualquier operación), así que se salta a la interfaz."""
        wx.CallAfter(self.ofrecer_reautorizar, "", account_id)

    # ------------------------------------------------------------------
    # TIEMPO REAL
    # ------------------------------------------------------------------
    def iniciar_tiempo_real(self):
        """Arranca los avisos de W Social si el usuario los tiene activados.

        Cada cuenta vigila por su cuenta y con su propio intervalo, así que encender esto no
        interfiere con el tiempo real de Mastodon, Telegram, Bluesky ni ninguna otra red."""
        # El mantenimiento de la sesión va SIEMPRE, tengas o no los avisos encendidos: es lo que
        # evita tener que volver a autorizar la cuenta cuando pasas un tiempo sin usar esta red.
        try:
            wsocial_service.iniciar_mantenimiento_sesion()
        except Exception as e:
            from app.services.wsocial.wsocial_common import log
            log.debug(f"W Social: no se pudo iniciar el mantenimiento de sesión: {e}")
        if not config.get("wsocial", "tiempo_real", True):
            return
        intervalo = int(config.get("wsocial", "intervalo_tiempo_real", 60) or 60)
        for acc_id, _nombre in wsocial_service.cuentas():
            try:
                wsocial_service.iniciar_tiempo_real(
                    acc_id,
                    al_haber_novedades=lambda n, a=acc_id: wx.CallAfter(self._aviso_novedades, a, n),
                    al_llegar_mensaje=lambda ev, a=acc_id: wx.CallAfter(self._aviso_mensaje, a, ev),
                    intervalo=intervalo)
            except Exception as e:
                from app.services.wsocial.wsocial_common import log
                log.debug(f"W Social: no se pudo iniciar el tiempo real de {acc_id}: {e}")

    def detener_tiempo_real(self):
        wsocial_service.detener_tiempo_real()

    def _aviso_novedades(self, account_id, n):
        """Hay notificaciones nuevas: sonido y refresco silencioso del buffer si toca."""
        try:
            from app.core.event_bus import event_bus
            event_bus.emit("sound_trigger", "wsocial_new_notification")
        except Exception:
            pass
        f = self.f
        if f and f.active_wsocial_account_id == account_id and \
                f.active_timeline_type in ("notifications", "mentions", "home"):
            self.actualizar_incremental()

    def _aviso_mensaje(self, account_id, evento):
        """Ha llegado un mensaje directo."""
        try:
            from app.core.event_bus import event_bus
            event_bus.emit("sound_trigger", "wsocial_new_dm")
        except Exception:
            pass
        f = self.f
        if f and f.active_wsocial_account_id == account_id and \
                f.active_timeline_type in ("convos", "requests"):
            self.actualizar_incremental()
        elif config.get("wsocial", "avisar_mensajes", True):
            texto = (evento or {}).get("texto", "")
            notificar(_("Mensaje nuevo en W Social: {0}").format(texto[:80]))
