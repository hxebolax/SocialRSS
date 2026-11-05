# -*- coding: utf-8 -*-
"""Hilos largos de Bluesky y W Social: se escribe un texto largo, se divide en publicaciones encadenadas (cada una responde
a la anterior) y se publica, o se envía por mensaje directo (en estas redes no hay publicaciones privadas)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar
from app.services.wsocial.wsocial_common import LIMITE_CARACTERES

from .dialogos import DialogoBase
from .dialogos_bluesky import CONTROL_RESPUESTAS
from .dialogos_mastodon import ListaSimple
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_BLUESKY = 300
RESERVA = 8         # margen para la numeración


def dividir(texto: str, limite: int) -> list:
    """Parte el texto en trozos de como mucho `limite` caracteres respetando palabras, enlaces y menciones."""
    try:
        from app.services.mastodon.mastodon_threading import dividir_texto_recursivo, obtener_regiones_protegidas
        return dividir_texto_recursivo(texto, limite - RESERVA, obtener_regiones_protegidas(texto))
    except Exception:
        return [texto[i:i + limite] for i in range(0, len(texto), limite)]


class DialogoHiloLargo(DialogoBase):
    def __init__(self, padre, account_id: int, red: str, texto_inicial: str = ""):
        nombre = "Bluesky" if red == "bluesky" else "W Social"
        super().__init__(padre, _("Nuevo hilo de {r}").format(r=nombre), _("_Publicar el hilo"))
        self.account_id, self.red = account_id, red
        self.limite = LIMITE_BLUESKY if red == "bluesky" else LIMITE_CARACTERES
        self.segmentos = []
        self.set_default_size(680, -1)
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True, left_margin=6,
                                  right_margin=6, top_margin=6, bottom_margin=6)
        self.texto.get_buffer().set_text(texto_inicial or "")
        instalar_ayudas(self.texto)
        self.campo(_("_Texto del hilo (se dividirá en publicaciones de {n}):").format(n=self.limite),
                   Gtk.ScrolledWindow(child=self.texto, min_content_height=160, has_frame=True))
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.texto)
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("_Dividir en publicaciones"), self._dividir), (_("_Editar la seleccionada"), self._editar)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        self.partes = ListaSimple(_("Publicaciones del hilo"))
        self.partes.set_min_content_height(120)
        self.campo(_("Vista _previa:"), self.partes)
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.partes.vista)
        self.destino = self.campo(_("D_estino del hilo:"), Gtk.DropDown.new_from_strings(
            [_("Publicarlo (hilo público)"), _("Enviarlo por mensaje directo (privado)")]))
        self.destino.connect("notify::selected", lambda *_a: self._cambiar_destino())
        self.destinatario = self.campo(_("Destinata_rio (usuario o DID):"), Gtk.Entry(width_chars=40))
        self.control = self.campo(_("_Quién puede responder:"), Gtk.DropDown.new_from_strings(
            [_(n) for _c, n in CONTROL_RESPUESTAS]))
        pred = config.get(red, "default_reply_control", "everybody")
        self.control.set_selected(next((i for i, (c, _n) in enumerate(CONTROL_RESPUESTAS) if c == pred), 0))
        self._cambiar_destino()
        self.set_focus(self.texto)

    def _contenido(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip()

    def _cambiar_destino(self):
        privado = self.destino.get_selected() == 1
        self.destinatario.set_sensitive(privado)
        self.control.set_sensitive(not privado)
        if privado:
            notificar(_("No hay publicaciones privadas: el hilo se enviará como mensajes seguidos de vuestra "
                        "conversación. Indica el destinatario."))
            self.destinatario.grab_focus()

    def _dividir(self):
        texto = self._contenido()
        if not texto:
            notificar(_("No hay texto que dividir."))
            return
        self.segmentos = dividir(texto, self.limite)
        self._pintar()
        notificar(_("Dividido en {0} publicaciones.").format(len(self.segmentos)))

    def _pintar(self):
        total = len(self.segmentos)
        self.partes.poner([f"[{i}/{total}] {' '.join(s.split())[:90]}" for i, s in enumerate(self.segmentos, 1)])

    def _editar(self):
        p = self.partes.posicion()
        if not 0 <= p < len(self.segmentos):
            return
        d = DialogoBase(self, _("Editar la publicación {0}").format(p + 1), _("_Aceptar"))
        tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, left_margin=6, right_margin=6,
                          top_margin=6, bottom_margin=6)
        tv.get_buffer().set_text(self.segmentos[p])
        d.campo(_("_Texto:"), Gtk.ScrolledWindow(child=tv, min_content_height=120, min_content_width=420, has_frame=True))
        d.rejilla.get_child_at(0, d._fila - 1).set_mnemonic_widget(tv)

        def _listo():
            b = tv.get_buffer()
            self.segmentos[p] = b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip()
            self._pintar()
            d.close()
        d.aceptar = _listo
        d.set_focus(tv)
        d.present()

    def aceptar(self):
        if not self.segmentos:
            self._dividir()
        if not self.segmentos:
            return
        largos = [i + 1 for i, s in enumerate(self.segmentos) if len(s) > self.limite]
        if largos:
            notificar(_("Las publicaciones {0} superan los {1} caracteres. Edítalas.").format(largos, self.limite))
            return
        servicio = self._servicio()
        n = len(self.segmentos)
        if self.destino.get_selected() == 1:
            actor = self.destinatario.get_text().strip().lstrip("@")
            if not actor:
                notificar(_("Escribe la cuenta a la que enviar el hilo por mensaje directo."))
                self.destinatario.grab_focus()
                return

            def _dm(ok, info):
                notificar(_("Hilo enviado a {0}: {1} mensajes.").format(
                    (info or {}).get("title") or actor, (info or {}).get("enviados", n)) if ok
                    else _("No se pudo enviar el hilo por mensaje directo: {0}").format(info))
            servicio.enviar_hilo_dm_async(self.account_id, actor, self.segmentos, callback=_dm)
            notificar(_("Enviando el hilo por mensaje directo…"))
            self.close()
            return

        def _fin(ok, info):
            notificar(_("Hilo publicado: {0} publicaciones.").format(len(info)) if ok
                      else _("Error al publicar el hilo: {0}").format(info))
            if ok:
                self.padre.tras_publicar()
        servicio.publicar_hilo_async(self.account_id, self.segmentos,
                                     reply_control=CONTROL_RESPUESTAS[self.control.get_selected()][0], callback=_fin)
        notificar(_("Publicando el hilo…"))
        self.close()

    def _servicio(self):
        if self.red == "bluesky":
            from app.services.bluesky import bluesky_service
            return bluesky_service
        from app.services.wsocial.wsocial_service import wsocial_service
        return wsocial_service
