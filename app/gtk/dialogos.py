# -*- coding: utf-8 -*-
"""Diálogos de la interfaz GTK 4. Todos son ventanas modales con etiquetas asociadas a sus campos
(Alt+letra), Intro acepta y Escape cierra."""

import logging
from datetime import datetime, timedelta

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)


def ejecutar(fn, callback):
    """Ejecuta `fn()` (que bloquea hablando con la red) en segundo plano; `callback(res, err)` va al hilo de la UI."""
    task_manager.start_task(lambda ctx: fn(), callback=callback, pass_context=True)


class DialogoBase(Gtk.Window):
    """Ventana modal con rejilla etiqueta/campo, botones Aceptar/Cancelar y Escape para cerrar."""

    def __init__(self, padre, titulo: str, aceptar: str = None):
        super().__init__(title=titulo, transient_for=padre, modal=True, resizable=False)
        self.padre = padre
        from .mnemonicos import vigilar
        vigilar(self)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(14)
        self.set_child(caja)
        self.rejilla = Gtk.Grid(row_spacing=8, column_spacing=10)
        caja.append(self.rejilla)
        self._fila = 0

        botones = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, halign=Gtk.Align.END)
        self.boton_cancelar = Gtk.Button(label=_("_Cancelar"), use_underline=True)
        self.boton_cancelar.connect("clicked", lambda *_a: self.close())
        self.boton_aceptar = Gtk.Button(label=aceptar or _("_Aceptar"), use_underline=True)
        self.boton_aceptar.add_css_class("suggested-action")
        self.boton_aceptar.connect("clicked", lambda *_a: self.aceptar())
        botones.append(self.boton_cancelar)
        botones.append(self.boton_aceptar)
        caja.append(botones)
        self.set_default_widget(self.boton_aceptar)

        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)

    def campo(self, etiqueta: str, widget, ayuda: str = ""):
        """Añade una fila 'etiqueta: widget' con la etiqueta enlazada al campo (para el lector)."""
        lbl = Gtk.Label(label=etiqueta, use_underline=True, xalign=0)
        lbl.set_mnemonic_widget(widget)
        widget.set_hexpand(True)
        if ayuda:
            widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [ayuda])
        self.rejilla.attach(lbl, 0, self._fila, 1, 1)
        self.rejilla.attach(widget, 1, self._fila, 1, 1)
        self._fila += 1
        return widget

    def aceptar(self):
        self.close()


class DialogoAnadirFeed(DialogoBase):
    def __init__(self, padre, account_id: int, al_terminar):
        super().__init__(padre, _("Añadir feed o podcast"), _("_Añadir"))
        self.account_id = account_id
        self.al_terminar = al_terminar
        self.url = self.campo(_("_Dirección (URL):"), Gtk.Entry(activates_default=True, width_chars=50),
                              _("Dirección del feed o de la web. Si es una web, se busca el feed."))
        self.titulo = self.campo(_("_Título (opcional):"), Gtk.Entry(activates_default=True))
        cats = rss_service.obtener_categorias(account_id)
        self._cats = [None] + [c["id"] for c in cats]
        self.combo = self.campo(_("_Carpeta:"), Gtk.DropDown.new_from_strings(
            [_("(sin carpeta)")] + [c["name"] for c in cats]))
        self.set_focus(self.url)

    def aceptar(self):
        url = self.url.get_text().strip()
        if not url:
            return
        titulo = self.titulo.get_text().strip() or None
        cat = self._cats[self.combo.get_selected()]
        self.boton_aceptar.set_sensitive(False)
        from app.core.speech import speak
        speak(_("Validando y descargando el feed…"), interrupt=True)

        def _bg(ctx):
            return rss_service.añadir_feed(account_id=self.account_id, url=url, category_id=cat,
                                           custom_title=titulo, ctx=ctx)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("Error al añadir el feed: {e}").format(e=err))
                return
            notificar(_("Feed añadido: {t}").format(t=res))
            self.close()
            self.al_terminar()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


class DialogoTeclas(Gtk.Window):
    """Lista de teclas, en un cuadro de texto de solo lectura (se lee con las flechas)."""

    def __init__(self, padre):
        super().__init__(title=_("Teclas de la aplicación"), transient_for=padre, modal=True)
        self.set_default_size(560, 520)
        tv = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD,
                          left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        tv.update_property([Gtk.AccessibleProperty.LABEL], [_("Lista de teclas")])
        tv.get_buffer().set_text(_teclas())
        self.set_child(Gtk.ScrolledWindow(child=tv))
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)


def _teclas() -> str:
    return "\n".join([
        _("Moverse"),
        _("Alt+1 / Alt+2 / Alt+3: ir al árbol, a la lista o al texto"),
        _("Tab / Mayús+Tab: de un panel al siguiente o al anterior (las flechas mueven por las filas)"),
        _("Ctrl+F: buscar en la lista"),
        _("Intro: abrir el elemento (en RSS lo marca leído; pasa al texto)"),
        _("Menú o Mayús+F10: menú de acciones del elemento"),
        "",
        _("RSS"),
        _("Ctrl+M: leído / no leído"),
        _("Ctrl+D: destacar"),
        _("Ctrl+L: leer más tarde"),
        _("Supr: a la papelera"),
        _("Ctrl+K / Ctrl+Mayús+K: siguiente / anterior sin leer"),
        _("Ctrl+Intro: abrir en el navegador"),
        _("Ctrl+Mayús+C: copiar el título"),
        _("Alt+A: descargar el artículo completo"),
        _("Ctrl+Alt+N: añadir feed"),
        _("Ctrl+O: importar OPML"),
        "",
        _("Mastodon"),
        _("Ctrl+N: nueva publicación (en RSS, añade un feed)"),
        _("Ctrl+R: responder"),
        _("Ctrl+B: me gusta"),
        _("Ctrl+Mayús+P: impulsar"),
        _("Ctrl+Mayús+J: marcador"),
        _("Ctrl+Mayús+V: ver la conversación como buffer"),
        _("Ctrl+Mayús+G: publicaciones del autor"),
        _("Ctrl+Mayús+M: cargar publicaciones anteriores"),
        _("Ctrl+W: cerrar el buffer abierto"),
        _("Ctrl+Mayús+N: nuevo hilo (un texto largo dividido en mensajes)"),
        _("Ctrl+Alt+B: borradores"),
        _("Ctrl+T: traducir el elemento (usa el idioma de destino de Preferencias)"),
        _("Ctrl+Alt+R: resumir con IA"),
        _("Ctrl+I: describir con IA la imagen de la publicación"),
        _("Ctrl+Mayús+O: extraer con IA el texto de la imagen"),
        _("Ctrl+Alt+V: votar en la encuesta"),
        _("Ctrl+Alt+P: perfil del autor (seguir, silenciar, bloquear)"),
        _("Ctrl+Alt+E: editar tu publicación"),
        _("Ctrl+Mayús+U: citar la publicación"),
        _("Ctrl+Alt+L: abrir una lista"),
        _("Ctrl+Alt+H: abrir un hashtag"),
        _("Ctrl+Mayús+F: buscar en Mastodon"),
        _("Supr: borrar tu publicación"),
        _("En el editor: Ctrl+Intro publica"),
        "",
        _("Telegram"),
        _("Intro sobre un chat: abrir la conversación como buffer (Ctrl+W la cierra)"),
        _("Ctrl+N: nuevo mensaje en el chat; Ctrl+R: responder; Ctrl+Alt+E: editar tu mensaje"),
        _("Ctrl+Alt+X: reaccionar; Ctrl+Alt+T: reenviar; Ctrl+Alt+C: copiar el texto"),
        _("Ctrl+Alt+D: descargar el adjunto; Ctrl+Alt+O: abrirlo; Ctrl+Alt+U: enviar un archivo"),
        _("F2: reproducir la nota de voz o el audio"),
        _("Ctrl+Alt+P: información del chat; Supr: borrar el mensaje o salir del chat"),
        _("Ctrl+Mayús+M: cargar mensajes anteriores"),
        "",
        _("IRC"),
        _("Intro sobre un chat: abrir la conversación como buffer (Ctrl+W la cierra)"),
        _("Ctrl+N: escribir en el chat (en Avisos, en la consola del servidor); Ctrl+R: responder mencionando"),
        _("Lo que empieza por barra es un comando: /help los enseña"),
        _("Ctrl+Alt+J: unirse a un canal; Supr: eliminar el chat de la lista (y salir del canal)"),
        _("Ctrl+Alt+I: usuarios del canal; Ctrl+Alt+P: datos del usuario o del chat"),
        _("Ctrl+Alt+G: consola del servidor"),
        "",
        _("XMPP"),
        _("Intro sobre un chat: abrir la conversación; sobre una solicitud: aceptarla o rechazarla"),
        _("Ctrl+N: escribir en el chat (Ctrl+Intro envía) o agregar un contacto; Ctrl+R: responder al mensaje"),
        _("Ctrl+Alt+E: corregir tu último mensaje; Supr: retirar tu mensaje, quitar el contacto o salir del grupo"),
        _("Ctrl+Alt+X: reaccionar; Ctrl+Alt+C: copiar; Ctrl+D: destacar el mensaje"),
        _("Ctrl+Alt+D: descargar el adjunto; Ctrl+Alt+O: abrirlo; Ctrl+Alt+U: enviar un archivo; F2: reproducir"),
        _("Ctrl+Alt+J: unirse a una sala; Ctrl+Alt+I: ocupantes de la sala; Ctrl+Alt+P: información del contacto"),
        _("Ctrl+Mayús+M: cargar mensajes anteriores"),
        _("Ctrl+Alt+A: grabar una nota de voz y, pulsado otra vez, enviarla (Telegram, XMPP y Matrix); Ctrl+Alt+Mayús+A: cancelar"),
        "",
        _("Matrix"),
        _("Intro sobre una sala: abrir la conversación; sobre una invitación: aceptarla; sobre un mensaje: abrir su enlace"),
        _("Ctrl+N: escribir en la sala (Ctrl+Intro envía; «/me texto» es una acción); Ctrl+R: responder"),
        _("Ctrl+Alt+E: editar tu mensaje; Supr: borrar el mensaje, rechazar la invitación o salir de la sala"),
        _("Ctrl+Alt+X: reaccionar; Ctrl+Alt+C: copiar; Ctrl+D: fijar o desfijar el mensaje; Ctrl+Alt+V: votar en la encuesta"),
        _("Ctrl+Alt+D: descargar el adjunto; Ctrl+Alt+O: abrirlo; Ctrl+Alt+U: enviar un archivo; F2: reproducir"),
        _("Ctrl+Alt+J: unirse a una sala; Ctrl+Alt+I: miembros y moderación; Ctrl+Alt+P: datos de la sala o del usuario"),
        _("Ctrl+Mayús+M: cargar mensajes anteriores; el menú Matrix tiene hilos, fijados, ajustes, cifrado y más"),
        "",
        _("X (Twitter)"),
        _("Ctrl+N: nueva publicación (o mensaje, en una conversación); Ctrl+R: responder; Ctrl+Mayús+U: citar; Supr: borrar la tuya"),
        _("Ctrl+B: me gusta; Ctrl+Mayús+P: retweet; Ctrl+Mayús+J: marcador; Ctrl+Alt+V: votar en la encuesta"),
        _("Ctrl+Mayús+V: abrir la conversación como buffer; Ctrl+Mayús+G: publicaciones del autor; Ctrl+Alt+P: perfil"),
        _("Ctrl+Mayús+F / Ctrl+Alt+H: buscar; Ctrl+Alt+L: abrir una lista; Ctrl+Mayús+M: cargar publicaciones anteriores"),
        _("Ctrl+Mayús+R: actualizar el buffer (X no se actualiza solo salvo que lo actives en Preferencias)"),
        "",
        _("Reddit"),
        _("Intro sobre una publicación: abrir sus comentarios; Ctrl+N: publicar (o mensaje privado en la bandeja); Ctrl+R: responder"),
        _("Ctrl+B: voto positivo; Ctrl+Mayús+D: voto negativo (pulsado otra vez, lo quita); Ctrl+Mayús+J: guardar; Ctrl+Mayús+P: crosspost"),
        _("Ctrl+Alt+E: editar lo tuyo; Supr: borrarlo; Ctrl+Mayús+V: comentarios; Ctrl+Mayús+G: publicaciones del autor; Ctrl+Alt+P: perfil"),
        _("Ctrl+Alt+L: ir a un subreddit; Ctrl+Mayús+F: buscar publicaciones; Ctrl+Alt+H: buscar subreddits; Ctrl+Mayús+M: cargar anteriores"),
        _("Ctrl+M: marcar un mensaje como leído o no leído; Ctrl+Mayús+R: actualizar (el buffer abierto se actualiza solo cada pocos minutos)"),
        "",
        _("W Social"),
        _("Mismas teclas que Mastodon y Bluesky: Ctrl+N publicar, Ctrl+R responder, Ctrl+Mayús+U citar, Ctrl+B me gusta, Ctrl+Mayús+P republicar"),
        _("Ctrl+Mayús+J: marcador; Supr: borrar la tuya; Ctrl+Mayús+V: conversación; Ctrl+Mayús+G: publicaciones del autor; Ctrl+Alt+P: perfil"),
        _("Ctrl+Mayús+F: buscar; Ctrl+Alt+F: abrir un feed; Ctrl+Alt+L: abrir una lista; Ctrl+Alt+M: mensaje directo al autor"),
        "",
        _("PeerTube"),
        _("Intro sobre un vídeo: reproducirlo (suena el audio); F2: pausar o reanudar; Ctrl+N: subir un vídeo (o crear una lista o un canal)"),
        _("Ctrl+B: me gusta; Ctrl+Mayús+D: no me gusta (pulsado otra vez, lo quita); Ctrl+Mayús+J: añadir a ver más tarde"),
        _("Ctrl+Mayús+V: comentarios; Ctrl+R: comentar; Ctrl+Alt+P: datos del canal; Ctrl+Mayús+G: abrir el canal"),
        _("Ctrl+Mayús+F: buscar vídeos; Ctrl+Alt+H: buscar canales; Ctrl+Alt+L: ir a un canal; Ctrl+Mayús+M: cargar anteriores"),
        _("Ctrl+Alt+E: editar lo tuyo; Supr: borrarlo o quitarlo del historial; Ctrl+M: marcar una notificación como leída"),
        "",
        _("Actualizar y reproductor"),
        _("Ctrl+Mayús+R: actualizar (todos los feeds o el buffer actual)"),
        _("F1 / F3: retroceder / avanzar"),
        _("F2: reproducir o pausar (sobre un podcast, lo empieza)"),
        _("F4: detener"),
        _("F5 / F6: bajar / subir volumen"),
        _("F7 / F8: bajar / subir velocidad"),
        "",
        _("Árbol de feeds"),
        _("Tecla Menú o Mayús+F10 sobre un feed, una carpeta o una cuenta: actualizar, fijar, pausar, renombrar, mover, editar..."),
        _("Botonera de acciones (Preferencias): Tab desde la lista llega a los botones; Izquierda y Derecha cambian de botón"),
        _("Ctrl+Arriba / Ctrl+Abajo: mueve el elemento del árbol (servicios, cuentas, buffers, feeds y categorías)"),
        "",
        _("Bandeja de entrada"),
        _("Ver, Añadir buffers a la bandeja (o Añadir a la bandeja en el menú del buffer): reúne varias redes por fecha"),
        _("Ctrl+Alt+Z: ir al buffer de origen de la publicación enfocada"),
        "",
        _("Herramientas"),
        _("Ctrl+Mayús+A: centro de notificaciones; Ctrl+Mayús+X: publicación cruzada; Ctrl+Mayús+S: búsqueda global"),
        _("Ctrl+Mayús+Y: ponte al día con IA; Ctrl+Alt+K: descripción de la multimedia; Ctrl+Mayús+N: nuevo hilo"),
        _("Al redactar: Ctrl+Mayús+J inserta un fragmento de texto y F7 revisa la ortografía"),
        _("Herramientas, Editor de atajos de teclado: cambia la tecla de cualquier acción"),
        "",
        _("Aplicación"),
        _("Ctrl+Mayús+B: repetir la barra de estado"),
        _("Ctrl+P: preferencias"),
        _("Ctrl+Q: salir"),
    ])


def confirmar_async(padre, texto: str, si, titulo: str = "", etiqueta_si: str = None, al_no=None):
    """Pregunta Sí/No sin bloquear; llama a `si()` si la respuesta es afirmativa (y a `al_no()`, si lo hay, si no)."""
    dlg = Gtk.AlertDialog()
    dlg.set_message(titulo or "SocialRSS")
    dlg.set_detail(texto)
    dlg.set_buttons([_("Cancelar"), etiqueta_si or _("Sí")])
    dlg.set_default_button(0)
    dlg.set_cancel_button(0)

    def _fin(origen, res):
        try:
            elegido = origen.choose_finish(res)
        except Exception:
            elegido = 0
        if elegido == 1:
            si()
        elif al_no is not None:
            al_no()
    dlg.choose(padre, None, _fin)


class DialogoCuentaMastodon(DialogoBase):
    """Inicio de sesión en una instancia: se registra la app, se abre el navegador para autorizar y
    se pega aquí el código que da la instancia."""

    def __init__(self, padre, al_terminar):
        super().__init__(padre, _("Añadir cuenta de Mastodon"), _("_Continuar"))
        self.al_terminar = al_terminar
        self._registro = None
        self.instancia = self.campo(_("_Instancia:"), Gtk.Entry(activates_default=True, width_chars=40),
                                    _("Por ejemplo mastodon.social. Se abrirá el navegador para autorizar."))
        self.codigo = self.campo(_("C_ódigo de autorización:"), Gtk.Entry(activates_default=True, sensitive=False),
                                 _("Pega aquí el código que te da la instancia tras autorizar."))
        self.set_focus(self.instancia)

    def aceptar(self):
        if self._registro is None:
            self._paso_autorizar()
        else:
            self._paso_codigo()

    def _paso_autorizar(self):
        from app.services.mastodon.mastodon_service import mastodon_service
        instancia = self.instancia.get_text().strip()
        if not instancia:
            return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Conectando con la instancia…"))

        def _bg(ctx):
            return mastodon_service.registrar_aplicacion(instancia)

        def _fin(res, err):
            self.boton_aceptar.set_sensitive(True)
            if err:
                notificar(_("No se pudo conectar: {e}").format(e=err))
                return
            self._registro = (instancia,) + tuple(res[:2])
            import webbrowser
            webbrowser.open(res[2])
            self.instancia.set_sensitive(False)
            self.codigo.set_sensitive(True)
            self.boton_aceptar.set_label(_("_Iniciar sesión"))
            self.codigo.grab_focus()
            notificar(_("Autoriza la aplicación en el navegador, copia el código y pégalo aquí."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _paso_codigo(self):
        from app.services.mastodon.mastodon_service import mastodon_service
        codigo = self.codigo.get_text().strip()
        if not codigo:
            return
        instancia, cid, secreto = self._registro
        self.boton_aceptar.set_sensitive(False)

        def _bg(ctx):
            return mastodon_service.completar_inicio_sesion(instancia, cid, secreto, codigo)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo iniciar sesión: {e}").format(e=err))
                return
            notificar(_("Cuenta añadida: {n}").format(n=res["username"]))
            self.close()
            self.al_terminar(res["account_id"])
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


VISIBILIDADES = [("public", "Público"), ("unlisted", "No listado"),
                 ("private", "Solo seguidores"), ("direct", "Mensaje directo")]


class DialogoPublicar(DialogoBase):
    """Redactar un toot (nuevo o respuesta). Ctrl+Intro publica."""

    def __init__(self, padre, account_id: int, texto_inicial: str = "", responde_a: str = None,
                 visibilidad: str = None, aviso: str = "", titulo: str = None, cita_id: str = None,
                 editar_db_id: str = None, borrador: dict = None, borrador_id: int = None):
        super().__init__(padre, titulo or (_("Editar publicación") if editar_db_id else
                                           _("Citar") if cita_id else
                                           _("Responder") if responde_a else _("Nueva publicación")),
                         _("_Guardar") if editar_db_id else _("_Publicar"))
        self.cita_id = cita_id
        self.editar_db_id = editar_db_id
        self.borrador_id = borrador_id
        self._terminado = False          # publicado o guardado: ya no hay nada que preguntar al cerrar
        from app.services.mastodon.mastodon_service import mastodon_service
        self.account_id = account_id
        self.responde_a = responde_a
        self.adjuntos = []   # [(ruta, descripción)]
        self.limite = mastodon_service.obtener_limite_caracteres(account_id)
        self.set_default_size(620, -1)

        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        self.buffer = self.texto.get_buffer()
        self.buffer.set_text(texto_inicial)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=140, hexpand=True)
        marco.set_has_frame(True)
        etiqueta = Gtk.Label(label=_("_Texto:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.contador = Gtk.Label(xalign=0)
        self.rejilla.attach(self.contador, 1, self._fila, 1, 1)
        self._fila += 1
        self.buffer.connect("changed", self._actualizar_contador)

        self.aviso = self.campo(_("_Aviso de contenido (opcional):"), Gtk.Entry())
        self.aviso.set_text(aviso)
        self.visibilidad = self.campo(_("_Visibilidad:"), Gtk.DropDown.new_from_strings(
            [_(n) for _c, n in VISIBILIDADES]))
        pred = visibilidad or mastodon_service.visibilidad_predeterminada(account_id)
        self.visibilidad.set_selected(next((i for i, (c, _n) in enumerate(VISIBILIDADES) if c == pred), 0))
        self.idioma = self.campo(_("_Idioma (código, opcional):"), Gtk.Entry(max_length=5, width_chars=6))
        self.encuesta = self.campo(_("_Encuesta: opciones, una por línea (vacío = sin encuesta)"), Gtk.TextView(accepts_tab=False, 
            wrap_mode=Gtk.WrapMode.WORD_CHAR, hexpand=True, top_margin=4, bottom_margin=4, left_margin=6))

        self.programar = self.campo(_("Pro_gramar para (AAAA-MM-DD HH:MM, opcional):"), Gtk.Entry(width_chars=18),
                                    _("Hora local, al menos cinco minutos en el futuro. Vacío publica ahora."))
        self.lista_adj = Gtk.Label(xalign=0, wrap=True, label=_("Sin adjuntos"))
        self.campo(_("Adjuntos:"), self.lista_adj)
        fila = Gtk.Box(spacing=8)
        b1 = Gtk.Button(label=_("Añadir _adjunto…"), use_underline=True)
        b1.connect("clicked", lambda *_a: self._anadir_adjunto())
        b2 = Gtk.Button(label=_("_Quitar el último adjunto"), use_underline=True)
        b2.connect("clicked", lambda *_a: self._quitar_adjunto())
        fila.append(b1)
        fila.append(b2)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1

        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self._actualizar_contador()
        self.set_focus(self.texto)
        self.buffer.place_cursor(self.buffer.get_end_iter())
        fila_ia = Gtk.Box(spacing=8)
        for etiqueta, que in ((_("_Mejorar la redacción con IA"), "mejorar"), (_("Sugerir _hashtags con IA"), "hashtags")):
            b = Gtk.Button(label=etiqueta, use_underline=True)
            b.connect("clicked", lambda *_a, q=que: self._ia_texto(q))
            fila_ia.append(b)
        self.rejilla.attach(fila_ia, 1, self._fila, 1, 1)
        self._fila += 1
        if not editar_db_id:
            guardar = Gtk.Button(label=_("Guardar _borrador"), use_underline=True)
            guardar.connect("clicked", lambda *_a: self._guardar_borrador())
            self.boton_cancelar.get_parent().prepend(guardar)
        if borrador:
            self._aplicar_borrador(borrador)
        self.connect("close-request", self._al_cerrar)
        if editar_db_id:
            # Al editar, Mastodon no permite cambiar visibilidad, idioma, encuesta ni (aquí) los adjuntos.
            for w in (self.visibilidad, self.idioma, self.encuesta):
                w.set_sensitive(False)

    def _texto(self) -> str:
        return self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), False)

    # --- IA ---
    def _ia_texto(self, que: str):
        from .acciones_ia import transformar_texto

        def _aplicar(resultado):
            if not resultado:
                notificar(_("La IA no devolvió nada"))
                return
            b = self.buffer
            b.begin_user_action()          # un solo paso de deshacer (Ctrl+Z)
            if que == "mejorar":
                b.delete(b.get_start_iter(), b.get_end_iter())
                b.insert(b.get_end_iter(), resultado)
                notificar(_("Redacción mejorada. Ctrl+Z deshace el cambio."))
            else:
                b.insert(b.get_end_iter(), ("\n" if self._texto() and not self._texto().endswith("\n") else "") + resultado)
                notificar(_("Hashtags añadidos al final: {h}").format(h=resultado))
            b.end_user_action()
        transformar_texto(self._texto(), que, _aplicar)

    # --- borradores ---
    def _capturar_borrador(self) -> dict:
        enc = self.encuesta.get_buffer()
        return {"formato": "gtk", "texto": self._texto(), "aviso": self.aviso.get_text(),
                "visibilidad": VISIBILIDADES[self.visibilidad.get_selected()][0], "idioma": self.idioma.get_text(),
                "encuesta": enc.get_text(enc.get_start_iter(), enc.get_end_iter(), False),
                "adjuntos": [list(a) for a in self.adjuntos], "programar": self.programar.get_text()}

    def _aplicar_borrador(self, b: dict):
        self.buffer.set_text(b.get("texto", ""))
        self.aviso.set_text(b.get("aviso", ""))
        self.idioma.set_text(b.get("idioma", ""))
        self.encuesta.get_buffer().set_text(b.get("encuesta", ""))
        self.programar.set_text(b.get("programar", ""))
        self.visibilidad.set_selected(next((i for i, (c, _n) in enumerate(VISIBILIDADES)
                                            if c == b.get("visibilidad")), self.visibilidad.get_selected()))
        self.adjuntos = [tuple(a) for a in b.get("adjuntos", []) if len(a) == 2]
        self._pintar_adjuntos(avisar=False)

    def _hay_contenido(self) -> bool:
        return bool(self._texto().strip() or self.adjuntos or self.aviso.get_text().strip())

    def _guardar_borrador(self, cerrar: bool = False):
        from app.services.mastodon import mastodon_borradores as mb
        contexto = {k: v for k, v in (("in_reply_to_id", self.responde_a), ("cita_id", self.cita_id)) if v}
        self.borrador_id = mb.guardar_borrador(self.account_id, self._texto(), self._capturar_borrador(),
                                               contexto, draft_id=self.borrador_id)
        self._terminado = True
        notificar(_("Borrador guardado"))
        if cerrar:
            self.close()

    def _al_cerrar(self, *_a):
        """Al cerrar con texto sin publicar ni guardar, pregunta qué hacer (no se pierde nada por accidente)."""
        if self._terminado or self.editar_db_id or not self._hay_contenido():
            return False
        dlg = Gtk.AlertDialog()
        dlg.set_message(_("Hay una publicación sin enviar"))
        dlg.set_detail(_("¿Quieres guardarla como borrador?"))
        dlg.set_buttons([_("Seguir editando"), _("Descartar"), _("Guardar borrador")])
        dlg.set_default_button(2)
        dlg.set_cancel_button(0)

        def _fin(origen, res):
            try:
                opcion = origen.choose_finish(res)
            except Exception:
                return
            if opcion == 2:
                self._guardar_borrador(cerrar=True)
            elif opcion == 1:
                self._terminado = True
                self.close()
        dlg.choose(self, None, _fin)
        return True

    def _actualizar_contador(self, *_a):
        resto = self.limite - len(self._texto())
        self.contador.set_text(_("Caracteres restantes: {n}").format(n=resto))

    def _anadir_adjunto(self):
        dlg = Gtk.FileDialog(title=_("Añadir adjunto"))

        def _fin(origen, res):
            try:
                f = origen.open_finish(res)
            except Exception:
                return
            self._pedir_descripcion(f.get_path())
        dlg.open(self, None, _fin)

    def _pedir_descripcion(self, ruta: str):
        d = DialogoBase(self, _("Descripción del adjunto"), _("_Aceptar"))
        entrada = d.campo(_("_Descripción para quien no lo ve:"), Gtk.Entry(activates_default=True, width_chars=50),
                          _("Describe la imagen o el audio. Se recomienda siempre."))
        if ruta.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
            ia = Gtk.Button(label=_("_Generar con IA"), use_underline=True)

            def _generar():
                from .acciones_ia import texto_alternativo
                texto_alternativo(ruta, lambda t: (entrada.set_text(t), entrada.grab_focus()))
            ia.connect("clicked", lambda *_a: _generar())
            d.rejilla.attach(ia, 1, d._fila, 1, 1)
            d._fila += 1
        d.aceptar = lambda: (self._guardar_adjunto(ruta, entrada.get_text().strip()), d.close())
        d.set_focus(entrada)
        d.present()

    def _guardar_adjunto(self, ruta: str, descripcion: str):
        self.adjuntos.append((ruta, descripcion))
        self._pintar_adjuntos()

    def _quitar_adjunto(self):
        if self.adjuntos:
            self.adjuntos.pop()
            self._pintar_adjuntos()

    def _pintar_adjuntos(self, avisar: bool = True):
        import os
        if not self.adjuntos:
            self.lista_adj.set_text(_("Sin adjuntos"))
            return
        self.lista_adj.set_text("; ".join(
            f"{os.path.basename(r)} ({d or _('sin descripción')})" for r, d in self.adjuntos))
        if avisar:
            notificar(_("{n} adjuntos").format(n=len(self.adjuntos)))

    def aceptar(self):
        from app.services.mastodon.mastodon_composer import MastodonComposer
        texto = self._texto().strip()
        if not texto and not self.adjuntos:
            notificar(_("Escribe algo o añade un adjunto"))
            return
        if len(texto) > self.limite:
            notificar(_("El texto supera el límite de {n} caracteres").format(n=self.limite))
            return
        aviso = self.aviso.get_text().strip()
        enc = self.encuesta.get_buffer()
        opciones = [o.strip() for o in enc.get_text(enc.get_start_iter(), enc.get_end_iter(), False).splitlines()
                    if o.strip()]
        if self.editar_db_id:
            from app.services.mastodon.mastodon_service import mastodon_service as _ms

            def _fin_edicion(res, err):
                if err:
                    self.boton_aceptar.set_sensitive(True)
                    notificar(_("No se pudo guardar: {e}").format(e=err))
                    return
                notificar(_("Publicación editada"))
                self.close()
                self.padre.tras_publicar()
            self.boton_aceptar.set_sensitive(False)
            _ms.editar_toot_async(self.account_id, self.editar_db_id, texto, spoiler_text=aviso or None,
                                  callback=_fin_edicion)
            return
        if opciones and (len(opciones) < 2 or self.adjuntos):
            notificar(_("Una encuesta necesita al menos dos opciones y no admite adjuntos"))
            return
        programada = None
        if self.programar.get_text().strip():
            try:
                programada = datetime.strptime(self.programar.get_text().strip(), "%Y-%m-%d %H:%M").astimezone()
            except ValueError:
                notificar(_("Escribe la fecha como AAAA-MM-DD HH:MM"))
                return
            if programada <= datetime.now().astimezone() + timedelta(minutes=5):
                notificar(_("La fecha debe ser al menos 5 minutos en el futuro"))
                return
        self.boton_aceptar.set_sensitive(False)
        notificar(_("Programando…") if programada else _("Publicando…"))

        def _fin(ok, detalle):
            # El compositor avisa con (éxito, id del toot) o (False, motivo del error).
            if not ok:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo publicar: {e}").format(e=detalle))
                return
            notificar(_("Publicación programada") if programada else _("Publicado"))
            if self.borrador_id:
                from app.services.mastodon import mastodon_borradores as mb
                mb.borrar_borrador(self.borrador_id)
            self._terminado = True
            self.close()
            self.padre.tras_publicar()
        MastodonComposer.publicar_toot_async(
            self.account_id, texto, visibility=VISIBILIDADES[self.visibilidad.get_selected()][0],
            sensitive=bool(aviso), spoiler_text=aviso or None,
            media_paths=[r for r, _d in self.adjuntos] or None,
            alt_texts=[d for _r, d in self.adjuntos] or None,
            poll_options=opciones or None, in_reply_to_id=self.responde_a, quoted_status_id=self.cita_id,
            language=self.idioma.get_text().strip() or None, scheduled_at=programada, callback=_fin)


class DialogoTexto(DialogoBase):
    """Pide una sola cadena (un hashtag, una búsqueda...) y la entrega a `al_aceptar(texto)`."""

    def __init__(self, padre, titulo: str, etiqueta: str, al_aceptar, ayuda: str = "", aceptar: str = None):
        super().__init__(padre, titulo, aceptar)
        self.al_aceptar = al_aceptar
        self.entrada = self.campo(etiqueta, Gtk.Entry(activates_default=True, width_chars=40), ayuda)
        self.set_focus(self.entrada)

    def aceptar(self):
        texto = self.entrada.get_text().strip()
        if texto:
            self.close()
            self.al_aceptar(texto)


class DialogoElegir(DialogoBase):
    """Lista de opciones con un desplegable; entrega el índice elegido."""

    def __init__(self, padre, titulo: str, etiqueta: str, opciones, al_aceptar, aceptar: str = None):
        super().__init__(padre, titulo, aceptar)
        self.al_aceptar = al_aceptar
        self.combo = self.campo(etiqueta, Gtk.DropDown.new_from_strings(list(opciones)))
        self.set_focus(self.combo)

    def aceptar(self):
        i = self.combo.get_selected()
        self.close()
        self.al_aceptar(i)


class DialogoVotar(DialogoBase):
    """Votar en una encuesta: casillas si admite varias opciones y botones de opción si solo una."""

    def __init__(self, padre, encuesta: dict, al_votar):
        super().__init__(padre, _("Votar en la encuesta"), _("_Votar"))
        self.al_votar = al_votar
        self.botones = []
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        primero = None
        for i, titulo in enumerate(encuesta["options"]):
            if encuesta.get("multiple"):
                b = Gtk.CheckButton(label=titulo)
            else:
                b = Gtk.CheckButton(label=titulo)
                if primero is None:
                    primero = b
                else:
                    b.set_group(primero)
            self.botones.append(b)
            caja.append(b)
        marco = Gtk.Frame(label=_("Elige varias opciones") if encuesta.get("multiple") else _("Elige una opción"))
        marco.set_child(caja)
        marco.update_property([Gtk.AccessibleProperty.LABEL], [_("Opciones de la encuesta")])
        self.rejilla.attach(marco, 0, 0, 2, 1)
        self.set_focus(self.botones[0])

    def aceptar(self):
        elegidas = [i for i, b in enumerate(self.botones) if b.get_active()]
        if not elegidas:
            notificar(_("Elige al menos una opción"))
            return
        self.close()
        self.al_votar(elegidas)


class DialogoPerfil(Gtk.Window):
    """Ficha de una cuenta con acciones: seguir, silenciar, bloquear y ver sus publicaciones."""

    def __init__(self, padre, account_id: int, acct: str):
        super().__init__(title=_("Perfil de {u}").format(u=acct), transient_for=padre, modal=True)
        self.padre, self.account_id, self.acct = padre, account_id, acct.lstrip("@")
        self.rel = {}
        self.set_default_size(560, 480)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for m in ("top", "bottom", "start", "end"):
            getattr(caja, f"set_margin_{m}")(12)
        self.set_child(caja)
        self.texto = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD,
                                  left_margin=8, right_margin=8, top_margin=6, bottom_margin=6)
        self.texto.update_property([Gtk.AccessibleProperty.LABEL], [_("Datos del perfil")])
        self.texto.get_buffer().set_text(_("Cargando el perfil…"))
        caja.append(Gtk.ScrolledWindow(child=self.texto, vexpand=True, has_frame=True))
        fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.b_seguir = Gtk.Button(label=_("_Seguir"), use_underline=True, sensitive=False)
        self.b_silenciar = Gtk.Button(label=_("Sile_nciar"), use_underline=True, sensitive=False)
        self.b_bloquear = Gtk.Button(label=_("_Bloquear"), use_underline=True, sensitive=False)
        self.b_publicaciones = Gtk.Button(label=_("Ver _publicaciones"), use_underline=True)
        cerrar = Gtk.Button(label=_("_Cerrar"), use_underline=True)
        self.b_seguir.connect("clicked", lambda *_a: self._accion("unfollow" if self.rel.get("following") else "follow"))
        self.b_silenciar.connect("clicked", lambda *_a: self._accion("unmute" if self.rel.get("muting") else "mute"))
        self.b_bloquear.connect("clicked", lambda *_a: self._confirmar_bloqueo())
        self.b_publicaciones.connect("clicked", lambda *_a: (self.close(), padre.abrir_cronologia(account_id, self.acct)))
        cerrar.connect("clicked", lambda *_a: self.close())
        for b in (self.b_seguir, self.b_silenciar, self.b_bloquear, self.b_publicaciones, cerrar):
            fila.append(b)
        caja.append(fila)
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                          Gtk.CallbackAction.new(lambda *_a: self.close() or True)))
        self.add_controller(ctl)
        self.set_focus(self.texto)
        self._cargar()

    def _cargar(self):
        from app.services.mastodon.mastodon_service import mastodon_service
        mastodon_service.obtener_detalles_cuenta_async(self.account_id, self.acct, callback=self._pintar)

    def _pintar(self, d, err):
        if err or not d:
            self.texto.get_buffer().set_text(_("No se pudo cargar el perfil: {e}").format(e=err))
            return
        self.rel = d.get("relacion") or {}
        r = self.rel
        lineas = [f"{d['display_name']} (@{d['acct']})"]
        if d.get("estado"):
            lineas.append(d["estado"])
        etiquetas = []
        if d.get("bot"):
            etiquetas.append(_("cuenta automatizada"))
        if r.get("es_yo"):
            etiquetas.append(_("eres tú"))
        if r.get("following"):
            etiquetas.append(_("le sigues"))
        if r.get("followed_by"):
            etiquetas.append(_("te sigue"))
        if r.get("requested"):
            etiquetas.append(_("solicitud de seguimiento pendiente"))
        if r.get("muting"):
            etiquetas.append(_("silenciada"))
        if r.get("blocking"):
            etiquetas.append(_("bloqueada"))
        if r.get("blocked_by"):
            etiquetas.append(_("te ha bloqueado"))
        if etiquetas:
            lineas.append(", ".join(etiquetas).capitalize())
        lineas.append(_("{p} publicaciones · {s} seguidores · {g} siguiendo").format(
            p=d["statuses"], s=d["followers"], g=d["following"]))
        if d.get("created_at"):
            lineas.append(_("Cuenta creada: {f}").format(f=d["created_at"][:10]))
        if d.get("bio"):
            lineas += ["", d["bio"]]
        for c in d.get("campos") or []:
            lineas.append(f"{c[0]}: {c[1]}" if isinstance(c, (list, tuple)) else str(c))
        if d.get("url"):
            lineas += ["", d["url"]]
        self.texto.get_buffer().set_text("\n".join(lineas))
        yo = bool(r.get("es_yo"))
        self.b_seguir.set_sensitive(not yo)
        self.b_silenciar.set_sensitive(not yo)
        self.b_bloquear.set_sensitive(not yo)
        self.b_seguir.set_label(_("Dejar de _seguir") if r.get("following") else _("_Seguir"))
        self.b_silenciar.set_label(_("Quitar sile_nciar") if r.get("muting") else _("Sile_nciar"))
        self.b_bloquear.set_label(_("Desb_loquear") if r.get("blocking") else _("_Bloquear"))

    def _confirmar_bloqueo(self):
        if self.rel.get("blocking"):
            self._accion("unblock")
        else:
            confirmar_async(self, _("¿Bloquear a @{u}?").format(u=self.acct), lambda: self._accion("block"),
                            etiqueta_si=_("Bloquear"))

    def _accion(self, accion: str):
        from app.services.mastodon.mastodon_service import mastodon_service
        textos = {"follow": _("Siguiendo a @{u}"), "unfollow": _("Has dejado de seguir a @{u}"),
                  "mute": _("Silenciada @{u}"), "unmute": _("Silencio quitado a @{u}"),
                  "block": _("Bloqueada @{u}"), "unblock": _("Desbloqueada @{u}")}

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {e}").format(e=err))
                return
            notificar(textos[accion].format(u=self.acct))
            self._cargar()
        mastodon_service.accion_cuenta_por_acct_async(self.account_id, self.acct, accion, callback=_fin)


class DialogoReportar(DialogoBase):
    def __init__(self, padre, account_id: int, db_id: str, usuario: str):
        super().__init__(padre, _("Reportar a {u}").format(u=usuario), _("_Enviar informe"))
        self.account_id, self.db_id = account_id, db_id
        self.categorias = [("spam", _("Spam")), ("legal", _("Contenido ilegal")),
                           ("violation", _("Incumple las normas")), ("other", _("Otro motivo"))]
        self.combo = self.campo(_("_Motivo:"), Gtk.DropDown.new_from_strings([n for _c, n in self.categorias]))
        self.comentario = self.campo(_("_Comentario (opcional):"), Gtk.Entry(activates_default=True, width_chars=50))
        self.reenviar = self.campo(_("_Reenviar también al servidor del autor:"), Gtk.Switch(halign=Gtk.Align.START))
        self.set_focus(self.combo)

    def aceptar(self):
        from app.services.mastodon.mastodon_service import mastodon_service
        cat = self.categorias[self.combo.get_selected()][0]

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar el informe: {e}").format(e=err))
                return
            notificar(_("Informe enviado"))
            self.close()
        mastodon_service.reportar_toot_async(
            self.account_id, self.db_id, self.comentario.get_text().strip(),
            reenviar=self.reenviar.get_active(), callback=_fin, categoria=cat)
