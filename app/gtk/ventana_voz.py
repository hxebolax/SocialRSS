# -*- coding: utf-8 -*-
"""Notas de voz en la ventana principal (mixin de VentanaPrincipal), para Telegram, XMPP y Matrix.

Ctrl+Alt+A empieza a grabar en el chat enfocado y, pulsado otra vez, detiene la grabación y la envía.
Ctrl+Alt+Mayús+A la cancela. Solo hay una grabación a la vez, y se envía al chat donde empezó: si pulsas
Ctrl+Alt+A en otro, avisa en vez de mandarla a quien no toca. Si te olvidas, se cancela a los 10 minutos."""

import logging

from gi.repository import GLib

from app.core.event_bus import event_bus
from app.core.task_manager import task_manager

from . import datos_matrix

log = logging.getLogger(__name__)

LIMITE_SEGUNDOS = 600


def ejecutar(fn, callback):
    """Como dialogos.ejecutar, pero sin importar GTK 4 (que no puede convivir con wx en las pruebas)."""
    task_manager.start_task(lambda ctx: fn(), callback=callback, pass_context=True)


class _VozMixin:
    _voz = None          # grabación en curso: {"servicio", "cancelar", "clave", "titulo", "enviar", "refrescar", "temporizador"}

    # ------------------------------------------------------------------ núcleo
    def _voz_alternar(self, servicio, clave, titulo, enviar, refrescar):
        """Empieza a grabar o, si ya hay una grabación para este mismo chat, la envía.

        `servicio` tiene iniciar_grabacion_voz/cancelar_grabacion_voz; `enviar()` hace detener_grabacion_y_enviar con los
        datos del chat; `refrescar()` repinta la conversación al enviar."""
        if self._voz is not None:
            if self._voz["clave"] != clave:
                self.avisar(_("Estás grabando una nota de voz para {t}. Vuelve a ese chat para enviarla o cancélala con "
                              "Ctrl+Alt+Mayús+A.").format(t=self._voz["titulo"]))
                return
            return self._voz_enviar()

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo empezar a grabar: {e}").format(e=err))
                return
            self._voz = {"servicio": servicio, "clave": clave, "titulo": titulo, "enviar": enviar,
                         "refrescar": refrescar,
                         "temporizador": GLib.timeout_add_seconds(LIMITE_SEGUNDOS, self._voz_caducada)}
            event_bus.emit("sound_trigger", "ready")
            self.avisar(_("Grabando para {t}. Ctrl+Alt+A envía la nota de voz y Ctrl+Alt+Mayús+A la cancela.").format(t=titulo))
        ejecutar(servicio.iniciar_grabacion_voz, _fin)

    def _voz_enviar(self):
        voz, self._voz = self._voz, None
        GLib.source_remove(voz["temporizador"])
        self.avisar(_("Enviando la nota de voz…"))

        def _fin(res, err):
            if err:
                self.avisar(_("No se pudo enviar la nota de voz: {e}").format(e=err))
                return
            self.avisar(_("Nota de voz enviada"))
            voz["refrescar"]()
        ejecutar(voz["enviar"], _fin)

    def voz_cancelar(self, avisar: bool = True):
        voz, self._voz = self._voz, None
        if voz is None:
            if avisar:
                self.avisar(_("No hay ninguna grabación en curso"))
            return
        GLib.source_remove(voz["temporizador"])
        try:
            voz["servicio"].cancelar_grabacion_voz()
        except Exception:
            log.debug("No se pudo cancelar la grabación", exc_info=True)
        if avisar:
            self.avisar(_("Grabación cancelada"))

    def _voz_caducada(self):
        if self._voz is not None:
            self._voz["temporizador"] = 0
            self.voz_cancelar(avisar=False)
            self.avisar(_("La grabación pasó de 10 minutos y se canceló"))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------ una acción por red
    def tg_grabar_voz(self):
        from app.services.telegram.telegram_service import telegram_service
        datos = self._tg_chat_de_fila()
        if not datos or not self._tg_disponible():
            return
        acc, cid, titulo = datos
        self._voz_alternar(telegram_service, ("telegram", acc, cid), titulo,
                           lambda: telegram_service.detener_grabacion_y_enviar(acc, cid),
                           lambda: self._tg_refrescar_chat(acc, cid, ir_al_final=True))

    def x_grabar_voz(self):
        datos = self._x_chat_de_fila()
        if not datos or not self._x_conectada(datos[0]):
            return
        acc, jid = datos
        sala = self._x_es_sala(acc, jid)
        from . import datos_xmpp
        servicio = datos_xmpp.servicio()
        self._voz_alternar(servicio, ("xmpp", acc, jid), jid,
                           lambda: servicio.detener_grabacion_y_enviar(acc, jid, es_muc=sala),
                           lambda: self._x_refrescar_chat(acc, jid, ir_al_final=True))

    def mx_grabar_voz(self):
        datos = self._mx_sala_de_fila()
        if not datos or not self._mx_conectada(datos[0]):
            return
        acc, room = datos
        servicio = datos_matrix.servicio()
        self._voz_alternar(servicio, ("matrix", acc, room), datos_matrix.titulo_sala(acc, room),
                           lambda: servicio.detener_grabacion_y_enviar(acc, room),
                           lambda: self._mx_refrescar_chat(acc, room, ir_al_final=True))

    def _registrar_acciones_voz(self, accion):
        accion("grabar-voz", self._por_red("grabar_voz"), ["<Ctrl><Alt>a"])
        accion("cancelar-voz", self.voz_cancelar, ["<Ctrl><Alt><Shift>a"])
