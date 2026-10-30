# -*- coding: utf-8 -*-
"""Telegram: encuestas, mensajes fijados, mensajes programados, listas de tareas, botones y comandos de bots, detalles de un
mensaje y otras acciones sobre mensajes. Los mismos servicios que usa la versión de Windows (telegram_mensajes.py y
telegram_bots.py)."""

import webbrowser
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.services.telegram.telegram_service import telegram_service

from .datos import fecha_legible
from .dialogos import DialogoBase, DialogoElegir, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_peertube_instancia import texto_en_dialogo
from .dialogos_reddit import _marco, _texto_de, _vista_texto
from .dialogos_telegram_admin import _hacer, _pedir


def _lineas(texto: str) -> list:
    return [t.strip() for t in (texto or "").splitlines() if t.strip()]


def _area(d: DialogoBase, etiqueta: str, alto: int = 110):
    tv = _vista_texto()
    lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0, yalign=0)
    lbl.set_mnemonic_widget(tv)
    d.rejilla.attach(lbl, 0, d._fila, 1, 1)
    d.rejilla.attach(_marco(tv, alto), 1, d._fila, 1, 1)
    d._fila += 1
    return tv


# --------------------------------------------------------------------------- encuestas
def crear_encuesta(padre, account_id: int, chat_id: int, titulo: str, al_crear=None):
    d = DialogoBase(padre, _("Nueva encuesta en {t}").format(t=titulo), _("_Crear"))
    pregunta = d.campo(_("_Pregunta:"), Gtk.Entry(activates_default=True, width_chars=44, max_length=255))
    opciones = _area(d, _("_Opciones (una por línea, de 2 a 10):"))
    anonima = d.campo(_("Votos _anónimos:"), Gtk.Switch(active=True, halign=Gtk.Align.START))
    multiple = d.campo(_("Permitir _varias respuestas:"), Gtk.Switch(halign=Gtk.Align.START))
    quiz = d.campo(_("Es un _concurso (con respuesta correcta):"), Gtk.Switch(halign=Gtk.Align.START))
    correcta = d.campo(_("Número de la opción co_rrecta (concurso):"), Gtk.SpinButton.new_with_range(1, 10, 1))
    explicacion = d.campo(_("_Explicación de la respuesta (concurso, opcional):"), Gtk.Entry(activates_default=True))
    d.set_focus(pregunta)

    def _ok():
        p, ops = pregunta.get_text().strip(), _lineas(_texto_de(opciones))
        if not p or not 2 <= len(ops) <= 10:
            return notificar(_("Escribe la pregunta y entre 2 y 10 opciones."))
        es_quiz = quiz.get_active()
        args = dict(anonima=anonima.get_active(), multiple=multiple.get_active(), quiz=es_quiz,
                    correcta=int(correcta.get_value()) - 1 if es_quiz else None,
                    explicacion=explicacion.get_text().strip() or None)
        d.close()
        _hacer(padre, lambda: telegram_service.crear_encuesta(account_id, chat_id, p, ops, **args), _("Encuesta creada."),
               lambda _r: al_crear and al_crear())
    d.aceptar = _ok
    d.present()


def ver_encuesta(padre, account_id: int, chat_id: int, message_id: int, al_cambiar=None):
    """Los resultados de una encuesta y lo que puedes hacer con ella: votar, retirar tu voto o cerrarla."""
    def _fin(enc, err):
        if err or not enc:
            return notificar(_("No se pudo leer la encuesta: {e}").format(e=err or _("no tiene encuesta")))
        d = DialogoLista(padre, enc["pregunta"], _("Opciones de la encuesta"))
        d.lista.poner([_("{t}: {v} votos, {p} %").format(t=o["texto"], v=o["votos"], p=o["porcentaje"])
                       + (" (" + _("tu voto") + ")" if o["indice"] in enc["elegidas"] else "") for o in enc["opciones"]])
        d.mostrar_estado(_("{n} votos").format(n=enc["total"]) + (". " + _("Cerrada.") if enc["cerrada"] else ""))
        if not enc["cerrada"]:
            d.boton(_("_Votar esta opción"), lambda: _votar(d, enc))
            d.boton(_("_Retirar mi voto"), lambda: _hacer(d, lambda: telegram_service.retirar_voto_encuesta(
                account_id, chat_id, message_id), _("Voto retirado."), lambda _r: (d.close(), al_cambiar and al_cambiar())))
            d.boton(_("_Cerrar la encuesta"), lambda: confirmar_async(
                d, _("¿Cerrar la encuesta? Ya no admitirá más votos."),
                lambda: _hacer(d, lambda: telegram_service.cerrar_encuesta(account_id, chat_id, message_id),
                               _("Encuesta cerrada."), lambda _r: d.close()), etiqueta_si=_("Cerrar")))
        d.cerrar_boton()
        d.present()

    def _votar(d, enc):
        p = d.lista.posicion()
        if 0 <= p < len(enc["opciones"]):
            _hacer(d, lambda: telegram_service.votar_encuesta(account_id, chat_id, message_id, p), _("Voto enviado."),
                   lambda _r: (d.close(), al_cambiar and al_cambiar()))
    ejecutar(lambda: telegram_service.obtener_encuesta(account_id, chat_id, message_id), _fin)


# --------------------------------------------------------------------------- fijados y programados
def fijar_mensaje(padre, account_id: int, chat_id: int, message_id: int, fijar: bool = True):
    fn = (lambda: telegram_service.fijar_mensaje(account_id, chat_id, message_id)) if fijar else (
        lambda: telegram_service.desfijar_mensaje(account_id, chat_id, message_id))
    _hacer(padre, fn, _("Mensaje fijado.") if fijar else _("Mensaje desfijado."))


def desfijar_todos(padre, account_id: int, chat_id: int, titulo: str):
    confirmar_async(padre, _("¿Desfijar todos los mensajes de {t}?").format(t=titulo),
                    lambda: _hacer(padre, lambda: telegram_service.desfijar_todos_mensajes(account_id, chat_id),
                                   _("Mensajes desfijados.")), etiqueta_si=_("Desfijar"))


def programar_mensaje(padre, account_id: int, chat_id: int, titulo: str):
    d = DialogoBase(padre, _("Programar un mensaje para {t}").format(t=titulo), _("_Programar"))
    texto = _area(d, _("_Mensaje:"))
    cuando = d.campo(_("_Fecha y hora (AAAA-MM-DD HH:MM):"), Gtk.Entry(activates_default=True, width_chars=20))
    d.set_focus(texto)

    def _ok():
        t = _texto_de(texto).strip()
        try:
            fecha = datetime.strptime(cuando.get_text().strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return notificar(_("Escribe la fecha como AAAA-MM-DD HH:MM."))
        if not t or fecha <= datetime.now():
            return notificar(_("Escribe el mensaje y una fecha futura."))
        d.close()
        _hacer(padre, lambda: telegram_service.enviar_mensaje(account_id, chat_id, t, schedule_date=fecha),
               _("Mensaje programado."))
    d.aceptar = _ok
    d.present()


class DialogoProgramadosTG(DialogoLista):
    def __init__(self, padre, account_id: int, chat_id: int, titulo: str):
        super().__init__(padre, _("Mensajes programados de {t}").format(t=titulo), _("Mensajes programados"))
        self.account_id, self.chat_id, self.titulo, self.items = account_id, chat_id, titulo, []
        self.boton(_("_Enviar ahora"), self._ahora)
        self.boton(_("_Cancelar el envío"), self._cancelar)
        self.boton(_("_Programar uno nuevo…"), lambda: programar_mensaje(self, account_id, chat_id, titulo))
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{fecha_legible(m['date'])}: {m['text']}" for m in self.items])
            self.mostrar_estado(_("{n} programados").format(n=len(self.items)) if self.items else _("No hay mensajes programados."))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.obtener_programados(self.account_id, self.chat_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _ahora(self):
        m = self._actual()
        if m:
            _hacer(self, lambda: telegram_service.enviar_programado_ahora(self.account_id, self.chat_id, m["message_id"]),
                   _("Mensaje enviado."), lambda _r: self._cargar())

    def _cancelar(self):
        m = self._actual()
        if m:
            _hacer(self, lambda: telegram_service.cancelar_programado(self.account_id, self.chat_id, m["message_id"]),
                   _("Envío cancelado."), lambda _r: self._cargar())


# --------------------------------------------------------------------------- listas de tareas
def crear_lista_tareas(padre, account_id: int, chat_id: int, titulo: str):
    d = DialogoBase(padre, _("Nueva lista de tareas en {t}").format(t=titulo), _("_Crear"))
    nombre = d.campo(_("_Título de la lista:"), Gtk.Entry(activates_default=True, width_chars=40))
    tareas = _area(d, _("_Tareas (una por línea):"))
    anadir = d.campo(_("Otros pueden _añadir tareas:"), Gtk.Switch(halign=Gtk.Align.START))
    marcar = d.campo(_("Otros pueden _marcarlas como hechas:"), Gtk.Switch(active=True, halign=Gtk.Align.START))
    d.set_focus(nombre)

    def _ok():
        n, ts = nombre.get_text().strip(), _lineas(_texto_de(tareas))
        if not n or not ts:
            return notificar(_("Escribe el título y al menos una tarea."))
        args = (anadir.get_active(), marcar.get_active())
        d.close()
        _hacer(padre, lambda: telegram_service.enviar_checklist(account_id, chat_id, n, ts, *args), _("Lista de tareas enviada."))
    d.aceptar = _ok
    d.present()


def ver_lista_tareas(padre, account_id: int, chat_id: int, message_id: int):
    def _fin(res, err):
        if err or not res:
            return notificar(_("Este mensaje no tiene lista de tareas."))
        d = DialogoLista(padre, res["titulo"] or _("Lista de tareas"), _("Tareas"))

        def _pintar(tareas):
            d.tareas = tareas
            d.lista.poner([(_("Hecha: ") if t[2] else _("Pendiente: ")) + t[1] for t in tareas])
        _pintar(res["tareas"])

        def _marcar(hecha: bool):
            p = d.lista.posicion()
            if 0 <= p < len(d.tareas):
                tid = d.tareas[p][0]
                _hacer(d, lambda: telegram_service.marcar_tareas_checklist(
                    account_id, chat_id, message_id, [tid] if hecha else [], [] if hecha else [tid]),
                    _("Tarea marcada como hecha.") if hecha else _("Tarea marcada como pendiente."),
                    lambda _r: (d.close(), ver_lista_tareas(padre, account_id, chat_id, message_id)))
        d.boton(_("Marcar como _hecha"), lambda: _marcar(True))
        d.boton(_("Marcar como _pendiente"), lambda: _marcar(False))
        d.boton(_("_Añadir tareas…"), lambda: _pedir(d, _("Añadir tareas"), _("_Tareas separadas por «;»:"), lambda t: t and _hacer(
            d, lambda: telegram_service.anadir_tareas_checklist(account_id, chat_id, message_id,
                                                                [x.strip() for x in t.split(";") if x.strip()],
                                                                max([x[0] for x in d.tareas] or [0])),
            _("Tareas añadidas."), lambda _r: (d.close(), ver_lista_tareas(padre, account_id, chat_id, message_id)))))
        d.cerrar_boton()
        d.mostrar_estado(_("{n} tareas").format(n=len(res["tareas"])))
        d.present()
    ejecutar(lambda: telegram_service.leer_checklist(account_id, chat_id, message_id), _fin)


# --------------------------------------------------------------------------- bots
def botones_del_mensaje(padre, account_id: int, chat_id: int, message_id: int, al_terminar=None):
    def _fin(res, err):
        if err or not res:
            return notificar(_("Este mensaje no tiene botones."))
        botones = res["botones"]

        def _pulsar(i):
            def _resultado(r, e):
                if e:
                    return notificar(_("No se pudo: {e}").format(e=e))
                tipo = (r or {}).get("tipo")
                if tipo == "callback":
                    if r.get("texto"):
                        notificar(r["texto"])
                    if r.get("url"):
                        webbrowser.open(r["url"])
                elif tipo == "url":
                    webbrowser.open(r["url"])
                elif tipo == "copiar":
                    copiar_al_portapapeles(r["texto"], _("Texto copiado"))
                elif tipo == "enviado":
                    notificar(_("Enviado: {t}").format(t=r.get("texto", "")))
                else:
                    notificar(r.get("texto") or r.get("consulta") or _("Hecho."))
                if al_terminar:
                    al_terminar()
            ejecutar(lambda: telegram_service.pulsar_boton(account_id, chat_id, message_id, i), _resultado)
        DialogoElegir(padre, _("Botones del mensaje"), _("_Botón:"), [b.get("text", "") for b in botones], _pulsar,
                      _("_Pulsar")).present()
    ejecutar(lambda: telegram_service.obtener_botones(account_id, chat_id, message_id), _fin)


def comandos_del_bot(padre, account_id: int, chat_id: int, titulo: str, al_terminar=None):
    def _fin(res, err):
        if err or not res:
            return notificar(_("Este chat no es un bot o no publica comandos."))

        def _enviar(i):
            c = res[i]["comando"]
            _hacer(padre, lambda: telegram_service.enviar_comando(account_id, chat_id, c), _("Comando enviado."),
                   lambda _r: al_terminar and al_terminar())
        DialogoElegir(padre, _("Comandos de {t}").format(t=titulo), _("_Comando:"),
                      [f"/{c['comando']}: {c['descripcion']}" for c in res], _enviar, _("_Enviar")).present()
    ejecutar(lambda: telegram_service.obtener_comandos_bot(account_id, chat_id), _fin)


def iniciar_bot(padre, account_id: int, chat_id: int, al_terminar=None):
    _hacer(padre, lambda: telegram_service.iniciar_bot(account_id, chat_id, ""), _("Bot iniciado."),
           lambda _r: al_terminar and al_terminar())


# --------------------------------------------------------------------------- otras acciones sobre un mensaje
def detalles_mensaje(padre, account_id: int, chat_id: int, message_id: int):
    def _fin(d, err):
        if err or not d:
            return notificar(_("No se pudieron leer los detalles: {e}").format(e=err or ""))
        lineas = []
        for clave, valor in d.items():
            if valor in (None, "", [], {}, 0, False):
                continue
            if isinstance(valor, list):
                valor = ", ".join(str(v) if not isinstance(v, dict) else " ".join(f"{k}={x}" for k, x in v.items()) for v in valor)
            lineas.append(f"{clave}: {valor}")
        texto_en_dialogo(padre, _("Detalles del mensaje"), "\n".join(lineas) or _("Sin datos."))
    ejecutar(lambda: telegram_service.obtener_detalles_mensaje(account_id, chat_id, message_id), _fin)


def traducir_mensaje(padre, account_id: int, chat_id: int, message_id: int):
    def _fin(res, err):
        if err or not res:
            return notificar(_("No se pudo traducir: {e}").format(e=err or ""))
        copiar_al_portapapeles(res, _("Traducción copiada"))
        texto_en_dialogo(padre, _("Traducción"), res)
    ejecutar(lambda: telegram_service.traducir_mensaje_nativo(account_id, chat_id, message_id, "es"), _fin)


def reenviar_a_guardados(padre, account_id: int, chat_id: int, message_id: int):
    _hacer(padre, lambda: telegram_service.reenviar_a_guardados(account_id, chat_id, [message_id]),
           _("Mensaje guardado en tus mensajes guardados."))


def borrar_descarga_local(padre, account_id: int, chat_id: int, message_id: int):
    _hacer(padre, lambda: telegram_service.borrar_descarga_local(account_id, chat_id, message_id),
           _("Archivo descargado borrado de este equipo."))


def marcar_menciones_leidas(padre, account_id: int, chat_id: int, titulo: str):
    _hacer(padre, lambda: telegram_service.marcar_menciones_leidas(account_id, chat_id), _("Menciones marcadas como leídas."))


def marcar_reacciones_leidas(padre, account_id: int, chat_id: int, titulo: str):
    _hacer(padre, lambda: telegram_service.marcar_reacciones_leidas(account_id, chat_id), _("Reacciones marcadas como leídas."))


def enviar_dado(padre, account_id: int, chat_id: int, titulo: str):
    emojis = ["🎲", "🎯", "🏀", "⚽", "🎰", "🎳"]
    DialogoElegir(padre, _("Enviar un dado"), _("_Juego:"), emojis,
                  lambda i: _hacer(padre, lambda: telegram_service.enviar_dado(account_id, chat_id, emojis[i]), _("Enviado.")),
                  _("_Enviar")).present()


def enviar_ubicacion(padre, account_id: int, chat_id: int, titulo: str):
    d = DialogoBase(padre, _("Enviar una ubicación a {t}").format(t=titulo), _("_Enviar"))
    lat = d.campo(_("_Latitud:"), Gtk.Entry(activates_default=True))
    lon = d.campo(_("L_ongitud:"), Gtk.Entry(activates_default=True))
    d.set_focus(lat)

    def _ok():
        try:
            a, b = float(lat.get_text().replace(",", ".")), float(lon.get_text().replace(",", "."))
        except ValueError:
            return notificar(_("Escribe la latitud y la longitud como números."))
        d.close()
        _hacer(padre, lambda: telegram_service.enviar_ubicacion(account_id, chat_id, a, b), _("Ubicación enviada."))
    d.aceptar = _ok
    d.present()


def enviar_contacto(padre, account_id: int, chat_id: int, titulo: str):
    d = DialogoBase(padre, _("Compartir un contacto con {t}").format(t=titulo), _("_Enviar"))
    tel = d.campo(_("_Teléfono:"), Gtk.Entry(activates_default=True))
    nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True))
    apellido = d.campo(_("_Apellido (opcional):"), Gtk.Entry(activates_default=True))
    d.set_focus(tel)

    def _ok():
        t, n, a = tel.get_text().strip(), nombre.get_text().strip(), apellido.get_text().strip()
        if not (t and n):
            return notificar(_("Escribe el teléfono y el nombre."))
        d.close()
        _hacer(padre, lambda: telegram_service.enviar_contacto(account_id, chat_id, t, n, a), _("Contacto enviado."))
    d.aceptar = _ok
    d.present()
