# -*- coding: utf-8 -*-
"""Bluesky, parte avanzada: mensajes directos, palabras silenciadas, moderación, listas, feeds, listas de
cuentas, informes y contraseñas de aplicación. Los diálogos de lista comparten `DialogoLista`."""

import logging
from datetime import datetime, timedelta, timezone

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.plataforma import copiar_texto
from app.core.speech import notificar
from app.services.bluesky.bluesky_moderacion import GLOBALES_CONFIGURABLES, NOMBRES, VISIBILIDADES
from app.services.bluesky.bluesky_service import bluesky_service

from .dialogos import DialogoBase, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista
from .fragmentos import instalar_ayudas

log = logging.getLogger(__name__)

LIMITE_MENSAJE = 1000
NOMBRES_VISIBILIDAD = {"ignore": "Mostrar", "warn": "Avisar", "hide": "Ocultar"}
MOTIVOS_INFORME = [("spam", "Spam"), ("violation", "Infringe las normas"), ("misleading", "Engañoso"),
                   ("sexual", "Contenido sexual no etiquetado"), ("rude", "Trato grosero o acoso"),
                   ("other", "Otro motivo")]


def _error(err) -> str:
    return str(err) if err else ""


# --------------------------------------------------------------------------- mensajes directos
class DialogoMensajeBsky(DialogoBase):
    """Escribir un mensaje directo. Ctrl+Intro lo envía."""

    def __init__(self, padre, account_id: int, convo_id: str, titulo: str, al_enviar=None):
        super().__init__(padre, _("Mensaje a {t}").format(t=titulo), _("_Enviar"))
        self.account_id, self.convo_id, self.al_enviar = account_id, convo_id, al_enviar
        self.set_default_size(560, -1)
        self.texto = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  left_margin=6, right_margin=6, top_margin=6, bottom_margin=6)
        instalar_ayudas(self.texto)
        marco = Gtk.ScrolledWindow(child=self.texto, min_content_height=110, hexpand=True, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Mensaje:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.texto)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.contador = Gtk.Label(xalign=0)
        self.rejilla.attach(self.contador, 1, self._fila, 1, 1)
        self.texto.get_buffer().connect("changed", lambda *_a: self._contar())
        ctl = Gtk.ShortcutController()
        ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Ctrl>Return"),
                                          Gtk.CallbackAction.new(lambda *_a: self.aceptar() or True)))
        self.add_controller(ctl)
        self._contar()
        self.set_focus(self.texto)

    def _leer(self) -> str:
        b = self.texto.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False)

    def _contar(self):
        self.contador.set_text(_("Caracteres restantes: {n}").format(n=LIMITE_MENSAJE - len(self._leer())))

    def aceptar(self):
        texto = self._leer().strip()
        if not texto:
            notificar(_("Escribe el mensaje"))
            return
        if len(texto) > LIMITE_MENSAJE:
            notificar(_("El mensaje supera los {n} caracteres").format(n=LIMITE_MENSAJE))
            return
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar: {e}").format(e=err))
                return
            notificar(_("Mensaje enviado"))
            self.close()
            if self.al_enviar:
                self.al_enviar()
        bluesky_service.enviar_mensaje_async(self.account_id, self.convo_id, texto, callback=_fin)


# --------------------------------------------------------------------------- palabras silenciadas
CADUCIDADES = [("", "Nunca"), ("1", "24 horas"), ("7", "7 días"), ("30", "30 días")]
OBJETIVOS = [(["content", "tag"], "Texto y etiquetas"), (["tag"], "Solo etiquetas (hashtags)")]
AUTORES = [("all", "Todas las cuentas"), ("exclude-following", "Excepto las que sigo")]


class DialogoPalabrasBsky(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Palabras silenciadas de Bluesky"), _("Lista de palabras silenciadas"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Añadir palabra…"), self._anadir)
        self.boton(_("_Quitar palabra"), self._quitar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            textos = []
            for w in self.items:
                partes = [w["value"]]
                partes.append(_("solo etiquetas") if w["targets"] == ["tag"] else _("texto y etiquetas"))
                if w["actorTarget"] == "exclude-following":
                    partes.append(_("excepto cuentas que sigues"))
                if w["expiresAt"]:
                    partes.append(_("caduca el {f}").format(f=w["expiresAt"][:10]))
                textos.append(". ".join(partes))
            self.lista.poner(textos)
            self.mostrar_estado(_("{n} palabras").format(n=len(self.items)) if self.items
                                else _("No hay palabras silenciadas"))
            self.lista.enfocar()
        bluesky_service.obtener_muted_words_async(self.account_id, callback=_fin)

    def _quitar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        w = self.items[p]

        def _fin(res, err):
            notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Palabra quitada"))
            self._refrescar()
        bluesky_service.quitar_muted_word_async(self.account_id, w, callback=_fin)

    def _anadir(self):
        d = DialogoBase(self, _("Silenciar una palabra"), _("_Silenciar"))
        valor = d.campo(_("_Palabra o frase:"), Gtk.Entry(activates_default=True, width_chars=36),
                        _("No verás las publicaciones que la contengan."))
        donde = d.campo(_("_Dónde buscarla:"), Gtk.DropDown.new_from_strings([_(n) for _t, n in OBJETIVOS]))
        quien = d.campo(_("¿De _quién?"), Gtk.DropDown.new_from_strings([_(n) for _t, n in AUTORES]))
        cuando = d.campo(_("_Caduca:"), Gtk.DropDown.new_from_strings([_(n) for _t, n in CADUCIDADES]))
        d.set_focus(valor)

        def _crear():
            texto = valor.get_text().strip()
            if not texto:
                notificar(_("Escribe la palabra"))
                return
            dias = CADUCIDADES[cuando.get_selected()][0]
            caduca = ""
            if dias:
                caduca = (datetime.now(timezone.utc) + timedelta(days=int(dias))).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo guardar: {e}").format(e=err))
                    return
                notificar(_("Palabra silenciada"))
                d.close()
                self._refrescar()
            bluesky_service.guardar_muted_word_async(
                self.account_id, {"value": texto, "targets": OBJETIVOS[donde.get_selected()][0],
                                  "actorTarget": AUTORES[quien.get_selected()][0], "expiresAt": caduca},
                callback=_fin)
        d.aceptar = _crear
        d.present()


# --------------------------------------------------------------------------- moderación de contenido
class DialogoModeracionBsky(DialogoBase):
    """Contenido para adultos y qué hacer con cada tipo de contenido etiquetado."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Moderación de contenido de Bluesky"), _("_Guardar"))
        self.account_id = account_id
        self.adulto = self.campo(_("Permitir contenido para _adultos:"), Gtk.Switch(halign=Gtk.Align.START),
                                 _("Si está desactivado, el contenido adulto queda oculto."))
        self.combos = {}
        opciones = [_(NOMBRES_VISIBILIDAD[v]) for v in VISIBILIDADES]
        for et in GLOBALES_CONFIGURABLES:
            nombre = NOMBRES.get(et, et)
            self.combos[et] = self.campo(_("{n}:").format(n=_(nombre).capitalize()), Gtk.DropDown.new_from_strings(opciones))
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.adulto)
        self._cargar()

    def _cargar(self):
        def _fin(prefs, err):
            if err or not prefs:
                notificar(_("No se pudieron leer las preferencias: {e}").format(e=_error(err)))
                return
            self.adulto.set_active(bool(prefs.get("adult_enabled")))
            for et, combo in self.combos.items():
                vis = (prefs.get("labels") or {}).get(et, "warn")
                combo.set_selected(VISIBILIDADES.index(vis) if vis in VISIBILIDADES else 1)
            self.boton_aceptar.set_sensitive(True)
        bluesky_service.refrescar_prefs_moderacion_async(self.account_id, callback=_fin)

    def aceptar(self):
        globales = {et: VISIBILIDADES[c.get_selected()] for et, c in self.combos.items()}
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo guardar: {e}").format(e=err))
                return
            notificar(_("Moderación guardada"))
            self.close()
        bluesky_service.guardar_moderacion_async(self.account_id, adulto=self.adulto.get_active(),
                                                 globales=globales, callback=_fin)


# --------------------------------------------------------------------------- listas
class DialogoListasBsky(DialogoLista):
    """Tus listas: abrirlas como buffer, crearlas, borrarlas y gestionar quién está en ellas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Tus listas de Bluesky"), _("Lista de tus listas"), self._abrir_pos)
        self.account_id = account_id
        self.items = []
        self.boton(_("_Abrir"), lambda: self._abrir_pos(self.lista.posicion()))
        self.boton(_("_Miembros…"), self._miembros)
        self.boton(_("_Crear lista…"), self._crear)
        self.boton(_("_Borrar lista"), self._borrar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([i["name"] for i in self.items])
            self.mostrar_estado(_("{n} listas").format(n=len(self.items)) if self.items else _("No tienes listas"))
            self.lista.enfocar()
        bluesky_service.obtener_listas_async(self.account_id, callback=_fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir_pos(self, pos):
        if not (0 <= pos < len(self.items)):
            return
        it = self.items[pos]
        self.close()
        self.padre._abrir_buffer(self.account_id, f"list:{it['uri']}", _("Lista {t}").format(t=it["name"]),
                                 servicio="bluesky")

    def _miembros(self):
        it = self._actual()
        if it:
            DialogoMiembrosListaBsky(self, self.account_id, it).present()

    def _crear(self):
        d = DialogoBase(self, _("Crear una lista"), _("_Crear"))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(activates_default=True, width_chars=36))
        desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True, width_chars=36))
        d.set_focus(nombre)

        def _hacer():
            if not nombre.get_text().strip():
                notificar(_("Escribe un nombre"))
                return

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear: {e}").format(e=err))
                    return
                notificar(_("Lista creada"))
                d.close()
                self._refrescar()
            bluesky_service.crear_lista_async(self.account_id, nombre.get_text().strip(), desc.get_text().strip(),
                                              callback=_fin)
        d.aceptar = _hacer
        d.present()

    def _borrar(self):
        it = self._actual()
        if not it:
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo borrar: {e}").format(e=err) if err else _("Lista borrada"))
                self._refrescar()
            bluesky_service.borrar_lista_async(self.account_id, it["uri"], callback=_fin)
        confirmar_async(self, _("¿Borrar la lista «{n}»?").format(n=it["name"]), _hacer, etiqueta_si=_("Borrar"))


class DialogoMiembrosListaBsky(DialogoLista):
    def __init__(self, padre, account_id: int, lista: dict):
        super().__init__(padre, _("Miembros de {n}").format(n=lista["name"]), _("Lista de miembros"))
        self.account_id, self.uri = account_id, lista["uri"]
        self.items = []
        self.boton(_("_Añadir cuenta…"), self._anadir)
        self.boton(_("_Quitar de la lista"), self._quitar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{m['display_name'] or m['handle']} (@{m['handle']})" for m in self.items])
            self.mostrar_estado(_("{n} miembros").format(n=len(self.items)) if self.items else _("La lista está vacía"))
            self.lista.enfocar()
        bluesky_service.obtener_miembros_lista_async(self.account_id, self.uri, callback=_fin)

    def _anadir(self):
        def _hacer(texto):
            def _fin(res, err):
                notificar(_("No se pudo añadir: {e}").format(e=err) if err else _("Cuenta añadida"))
                self._refrescar()
            bluesky_service.anadir_miembro_lista_async(self.account_id, self.uri, texto, callback=_fin)
        DialogoTexto(self, _("Añadir a la lista"), _("_Usuario (por ejemplo ana.bsky.social):"), _hacer,
                     aceptar=_("_Añadir")).present()

    def _quitar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        m = self.items[p]

        def _fin(res, err):
            notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Cuenta quitada"))
            self._refrescar()
        bluesky_service.quitar_miembro_lista_async(self.account_id, m["item_uri"], callback=_fin)


# --------------------------------------------------------------------------- feeds
class DialogoFeedsBsky(DialogoLista):
    """Tus feeds guardados y descubrimiento de otros nuevos."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Feeds de Bluesky"), _("Lista de feeds"), self._abrir_pos)
        self.account_id = account_id
        self.items = []
        self.fijados = {}
        self.modo = "guardados"
        self.boton(_("_Abrir"), lambda: self._abrir_pos(self.lista.posicion()))
        self.boton(_("_Fijar o desfijar / Guardar"), self._fijar)
        self.b_quitar = self.boton(_("_Quitar de guardados"), self._quitar)
        self.boton(_("_Buscar feeds…"), self._buscar)
        self.boton(_("_Sugeridos"), self._sugeridos)
        self.boton(_("_Mis feeds"), self._guardados)
        self.cerrar_boton()
        self._guardados()

    def _poner(self, items, textos, estado):
        self.items = items
        self.lista.poner(textos)
        self.mostrar_estado(estado)
        self.lista.enfocar()

    def _guardados(self):
        self.modo = "guardados"
        self.mostrar_estado(_("Cargando…"))
        from app.core.database import db
        self.fijados = {r[0]: bool(r[1]) for r in db.query(
            "SELECT uri, pinned FROM bluesky_feeds WHERE account_id = ? AND kind = 'feed'", (self.account_id,))}

        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.fijados = {r[0]: bool(r[1]) for r in db.query(
                "SELECT uri, pinned FROM bluesky_feeds WHERE account_id = ? AND kind = 'feed'", (self.account_id,))}
            textos = [i["name"] + (". " + _("Fijado") if self.fijados.get(i["uri"]) else "") for i in res]
            self._poner(res, textos, _("{n} feeds guardados").format(n=len(res)) if res else _("No tienes feeds guardados"))
        bluesky_service.obtener_feeds_guardados_async(self.account_id, callback=_fin)

    def _descubrir(self, res, err):
        if err:
            self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
            return
        self.modo = "descubrir"
        textos = [f"{i['name']}, de @{i['creator']}. " + _("{n} me gusta").format(n=i["likes"])
                  + (". " + i["description"] if i["description"] else "") for i in res]
        self._poner(res, textos, _("{n} feeds. Pulsa Abrir para leerlo o Guardar para añadirlo").format(n=len(res))
                    if res else _("No se encontró ningún feed"))

    def _sugeridos(self):
        self.mostrar_estado(_("Cargando…"))
        bluesky_service.obtener_feeds_sugeridos_async(self.account_id, callback=self._descubrir)

    def _buscar(self):
        def _hacer(texto):
            self.mostrar_estado(_("Buscando…"))
            bluesky_service.buscar_feeds_async(self.account_id, texto, callback=self._descubrir)
        DialogoTexto(self, _("Buscar feeds"), _("_Buscar:"), _hacer, aceptar=_("_Buscar")).present()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir_pos(self, pos):
        if not (0 <= pos < len(self.items)):
            return
        it = self.items[pos]
        self.close()
        self.padre._abrir_buffer(self.account_id, f"feed:{it['uri']}", _("Feed {t}").format(t=it["name"]),
                                 servicio="bluesky")

    def _fijar(self):
        it = self._actual()
        if not it:
            return
        if self.modo == "descubrir":
            def _fin(res, err):
                notificar(_("No se pudo guardar: {e}").format(e=err) if err else _("Feed guardado"))
            bluesky_service.guardar_feed_async(self.account_id, it["uri"], callback=_fin)
            return
        fijar = not self.fijados.get(it["uri"])

        def _fin2(res, err):
            notificar(_("No se pudo cambiar: {e}").format(e=err) if err else (_("Fijado") if fijar else _("Desfijado")))
            self._guardados()
        bluesky_service.fijar_feed_async(self.account_id, it["uri"], fijar, callback=_fin2)

    def _quitar(self):
        it = self._actual()
        if not it or self.modo != "guardados":
            return

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo quitar: {e}").format(e=err) if err else _("Feed quitado"))
                self._guardados()
            bluesky_service.quitar_feed_guardado_async(self.account_id, it["uri"], callback=_fin)
        confirmar_async(self, _("¿Quitar «{n}» de tus feeds guardados?").format(n=it["name"]), _hacer,
                        etiqueta_si=_("Quitar"))


# --------------------------------------------------------------------------- listas de cuentas
ACTORES = {
    "followers": ("Seguidores", "Lista de seguidores"),
    "follows": ("Siguiendo", "Lista de cuentas que sigues"),
    "blocks": ("Cuentas bloqueadas", "Lista de cuentas bloqueadas"),
    "mutes": ("Cuentas silenciadas", "Lista de cuentas silenciadas"),
    "likes": ("Quién ha marcado me gusta", "Lista de cuentas que dieron me gusta"),
    "reposts": ("Quién ha republicado", "Lista de cuentas que republicaron"),
    "quotes": ("Quién ha citado", "Lista de cuentas que citaron"),
}


class DialogoActoresBsky(DialogoLista):
    """Seguidores, seguidos, bloqueadas, silenciadas o quienes interactuaron con una publicación.
    Intro abre el perfil de la cuenta."""

    def __init__(self, padre, account_id: int, tipo: str, actor: str = None, uri: str = None):
        titulo, nombre = ACTORES[tipo]
        super().__init__(padre, _(titulo), _(nombre), self._perfil)
        self.account_id, self.tipo, self.actor, self.uri = account_id, tipo, actor, uri
        self.items = []
        self.hay_mas = False
        self.boton(_("_Perfil"), lambda: self._perfil(self.lista.posicion()))
        self.b_mas = self.boton(_("Cargar _más"), self._mas, False)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._cargar(False)

    def _cargar(self, mas: bool):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            if self.tipo in ("likes", "reposts", "quotes"):
                nuevos, self.hay_mas = res or [], False
            else:
                nuevos, self.hay_mas = res["actores"], res["has_more"]
            self.items = self.items + nuevos if mas else nuevos
            self.lista.poner([f"{a['display_name'] or a['handle']} (@{a['handle']})"
                              + (". " + _("Le sigues") if a["following_uri"] else "") for a in self.items])
            self.b_mas.set_sensitive(self.hay_mas)
            self.mostrar_estado(_("{n} cuentas").format(n=len(self.items)) if self.items else _("No hay cuentas"))
            self.lista.enfocar()
        if self.tipo in ("likes", "reposts", "quotes"):
            bluesky_service.obtener_interacciones_post_async(self.account_id, self.uri, self.tipo, callback=_fin)
        else:
            bluesky_service.cargar_actores_async(self.account_id, self.tipo, actor=self.actor, cargar_mas=mas,
                                                 callback=_fin)

    def _mas(self):
        self._cargar(True)

    def _perfil(self, pos):
        if 0 <= pos < len(self.items):
            from .dialogos_bluesky import DialogoPerfilBsky
            DialogoPerfilBsky(self.padre, self.account_id, self.items[pos]["did"]).present()


# --------------------------------------------------------------------------- informes
class DialogoReportarBsky(DialogoBase):
    def __init__(self, padre, account_id: int, post_db_id: str = None, did: str = None, quien: str = ""):
        super().__init__(padre, _("Denunciar {q}").format(q=quien or _("a la cuenta")), _("_Enviar denuncia"))
        self.account_id, self.post_db_id, self.did = account_id, post_db_id, did
        self.motivo = self.campo(_("_Motivo:"), Gtk.DropDown.new_from_strings([_(n) for _c, n in MOTIVOS_INFORME]))
        self.detalle = self.campo(_("_Detalles (opcional):"), Gtk.Entry(activates_default=True, width_chars=40))
        self.set_focus(self.motivo)

    def aceptar(self):
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo enviar la denuncia: {e}").format(e=err))
                return
            notificar(_("Denuncia enviada"))
            self.close()
        bluesky_service.reportar_async(self.account_id, MOTIVOS_INFORME[self.motivo.get_selected()][0],
                                       self.detalle.get_text().strip(), post_db_id=self.post_db_id, did=self.did,
                                       callback=_fin)


# --------------------------------------------------------------------------- contraseñas de aplicación
class DialogoClavesAppBsky(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Contraseñas de aplicación de Bluesky"), _("Lista de contraseñas de aplicación"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Crear contraseña…"), self._crear)
        self.boton(_("_Revocar contraseña"), self._revocar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([f"{c['name']}. " + (_("Con acceso a mensajes directos") if c["privileged"] else _("Sin acceso a mensajes directos"))
                              + ". " + _("Creada el {f}").format(f=(c["created_at"] or "")[:10]) for c in self.items])
            self.mostrar_estado(_("{n} contraseñas").format(n=len(self.items)) if self.items else _("No hay contraseñas"))
            self.lista.enfocar()
        bluesky_service.listar_app_passwords_async(self.account_id, callback=_fin)

    def _crear(self):
        def _hacer(nombre):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo crear: {e}").format(e=err))
                    return
                copiar_texto(res["password"])
                notificar(_("Contraseña creada y copiada al portapapeles: {p}. No se volverá a mostrar.")
                          .format(p=res["password"]))
                self._refrescar()
            bluesky_service.crear_app_password_async(self.account_id, nombre, callback=_fin)
        DialogoTexto(self, _("Crear una contraseña de aplicación"), _("_Nombre:"), _hacer,
                     _("Para usarla en otro programa. La que se crea aquí no permite mensajes directos."),
                     _("_Crear")).present()

    def _revocar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        c = self.items[p]

        def _hacer():
            def _fin(res, err):
                notificar(_("No se pudo revocar: {e}").format(e=err) if err else _("Contraseña revocada"))
                self._refrescar()
            bluesky_service.revocar_app_password_async(self.account_id, c["name"], callback=_fin)
        confirmar_async(self, _("¿Revocar la contraseña «{n}»? Los programas que la usen dejarán de funcionar.")
                        .format(n=c["name"]), _hacer, etiqueta_si=_("Revocar"))


# --------------------------------------------------------------------------- servicios de etiquetado
class DialogoEtiquetadoresBsky(DialogoLista):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Servicios de etiquetado de Bluesky"), _("Lista de servicios de etiquetado"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Suscribirse o darse de baja"), self._alternar)
        self.boton(_("_Buscar por usuario…"), self._buscar)
        self.cerrar_boton()
        self.mostrar_estado(_("Cargando…"))
        self._refrescar()

    def _poner(self, items):
        self.items = items
        self.lista.poner([f"{i['name']} (@{i['handle']}). " + (_("Oficial, siempre activo") if i["oficial"] else
                          _("Suscrito") if i["suscrito"] else _("No suscrito")) + ". " + i["descripcion"] for i in items])
        self.mostrar_estado(_("{n} servicios").format(n=len(items)))
        self.lista.enfocar()

    def _refrescar(self):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudo cargar: {e}").format(e=err))
                return
            self._poner(res or [])
        bluesky_service.obtener_labelers_async(self.account_id, callback=_fin)

    def _buscar(self):
        def _hacer(texto):
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo: {e}").format(e=err))
                    return
                self._poner([res])
            bluesky_service.buscar_labeler_async(self.account_id, texto, callback=_fin)
        DialogoTexto(self, _("Buscar un servicio de etiquetado"), _("_Usuario:"), _hacer, aceptar=_("_Buscar")).present()

    def _alternar(self):
        p = self.lista.posicion()
        if not (0 <= p < len(self.items)):
            return
        it = self.items[p]
        suscribir = not it["suscrito"]

        def _fin(res, err):
            notificar(_("No se pudo cambiar: {e}").format(e=err) if err else
                      (_("Suscrito a {n}") if suscribir else _("Baja de {n}")).format(n=it["name"]))
            self._refrescar()
        bluesky_service.suscribir_labeler_async(self.account_id, it["did"], suscribir, callback=_fin)


# --------------------------------------------------------------------------- perfil propio
class DialogoMiPerfilBsky(DialogoBase):
    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Mi perfil de Bluesky"), _("_Guardar"))
        self.account_id = account_id
        self.nombre = self.campo(_("_Nombre para mostrar:"), Gtk.Entry(activates_default=True, width_chars=40))
        self.bio = Gtk.TextView(accepts_tab=False, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6, right_margin=6, top_margin=6,
                                bottom_margin=6)
        marco = Gtk.ScrolledWindow(child=self.bio, min_content_height=120, min_content_width=420, has_frame=True)
        etiqueta = Gtk.Label(label=_("_Biografía:"), use_underline=True, xalign=0, yalign=0)
        etiqueta.set_mnemonic_widget(self.bio)
        self.rejilla.attach(etiqueta, 0, self._fila, 1, 1)
        self.rejilla.attach(marco, 1, self._fila, 1, 1)
        self._fila += 1
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo cargar tu perfil: {e}").format(e=_error(err)))
                return
            self.nombre.set_text(res["display_name"])
            self.bio.get_buffer().set_text(res["description"])
            self.boton_aceptar.set_sensitive(True)
        bluesky_service.obtener_perfil_propio_async(account_id, callback=_fin)
        self.set_focus(self.nombre)

    def aceptar(self):
        b = self.bio.get_buffer()
        self.boton_aceptar.set_sensitive(False)

        def _fin(res, err):
            if err:
                self.boton_aceptar.set_sensitive(True)
                notificar(_("No se pudo guardar: {e}").format(e=err))
                return
            notificar(_("Perfil guardado"))
            self.close()
        bluesky_service.editar_perfil_async(self.account_id, self.nombre.get_text().strip(),
                                            b.get_text(b.get_start_iter(), b.get_end_iter(), False).strip(),
                                            callback=_fin)
