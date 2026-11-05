# -*- coding: utf-8 -*-
"""Telegram: carpetas de chats, crear grupos y canales, unirse por enlace, búsquedas (en todos los chats, dentro de un chat y de
publicaciones de canales), chats frecuentes, canales similares y los medios compartidos de un chat. Los mismos servicios que usa
la versión de Windows."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.speech import copiar_al_portapapeles, notificar
from app.services.telegram.telegram_service import telegram_service

from .datos import fecha_legible
from .dialogos import DialogoBase, DialogoElegir, DialogoTexto, confirmar_async, ejecutar
from .dialogos_mastodon import DialogoLista
from .dialogos_telegram_admin import _hacer, _pedir

CATEGORIAS = [("USERS", "Personas"), ("BOTS", "Bots"), ("GROUPS", "Grupos"), ("CHANNELS", "Canales")]
MEDIOS = [("photo", "Fotos"), ("video", "Vídeos"), ("audio", "Música"), ("voice", "Notas de voz"), ("document", "Archivos"),
          ("url", "Enlaces")]


# --------------------------------------------------------------------------- carpetas
class DialogoCarpetasTG(DialogoLista):
    """Carpetas de chats: crear, renombrar, borrar, cambiar el orden, compartir por enlace y unirse a una compartida."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Carpetas de Telegram"), _("Lista de carpetas"))
        self.account_id, self.items = account_id, []
        self.boton(_("_Crear…"), self._crear)
        self.boton(_("_Renombrar…"), self._renombrar)
        self.boton(_("_Borrar"), self._borrar)
        self.boton(_("Su_bir"), lambda: self._mover(-1))
        self.boton(_("Ba_jar"), lambda: self._mover(1))
        self.boton(_("Co_mpartir por enlace…"), self._compartir)
        self.boton(_("_Unirme por enlace…"), self._unirse)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, foco: int = 0):
        def _fin(res, err):
            if err:
                self.mostrar_estado(_("No se pudieron cargar las carpetas: {e}").format(e=err))
                return
            self.items = res or []
            self.lista.poner([c.get("title") or str(c.get("id")) for c in self.items])
            self.mostrar_estado(_("{n} carpetas").format(n=len(self.items)) if self.items else _("No tienes carpetas"))
            if self.items:
                self.lista.seleccion.set_selected(max(0, min(foco, len(self.items) - 1)))
            self.lista.enfocar()
        ejecutar(lambda: telegram_service.obtener_carpetas(self.account_id), _fin)

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _crear(self):
        _pedir(self, _("Nueva carpeta"), _("_Nombre de la carpeta:"), lambda n: n and _hacer(
            self, lambda: telegram_service.crear_carpeta(self.account_id, n, []), _("Carpeta creada."), lambda _r: self._cargar()))

    def _renombrar(self):
        c = self._actual()
        if c:
            _pedir(self, _("Renombrar la carpeta"), _("_Nombre:"), lambda n: n and _hacer(
                self, lambda: telegram_service.editar_carpeta(self.account_id, c["id"], n), _("Carpeta renombrada."),
                lambda _r: self._cargar(self.lista.posicion())), valor=c.get("title", ""))

    def _borrar(self):
        c = self._actual()
        if c:
            confirmar_async(self, _("¿Borrar la carpeta «{n}»? Los chats no se borran.").format(n=c.get("title", "")),
                            lambda: _hacer(self, lambda: telegram_service.borrar_carpeta(self.account_id, c["id"]),
                                           _("Carpeta borrada."), lambda _r: self._cargar()), etiqueta_si=_("Borrar"))

    def _mover(self, delta: int):
        p = self.lista.posicion()
        n = p + delta
        if not (0 <= p < len(self.items) and 0 <= n < len(self.items)):
            return
        self.items[p], self.items[n] = self.items[n], self.items[p]
        ids = [c["id"] for c in self.items]
        self.lista.poner([c.get("title") or str(c.get("id")) for c in self.items])
        self.lista.seleccion.set_selected(n)
        self.lista.enfocar()
        _hacer(self, lambda: telegram_service.reordenar_carpetas(self.account_id, ids), _("Orden guardado."))

    def _unirse(self):
        _pedir(self, _("Unirse a una carpeta"), _("_Enlace de la carpeta:"), lambda e: e and _hacer(
            self, lambda: telegram_service.unirse_a_carpeta(self.account_id, e), _("Te has unido a la carpeta."),
            lambda _r: self._cargar()), _("_Unirme"))

    def _compartir(self):
        c = self._actual()
        if not c:
            return

        def _chats(res, err):
            if err or not res:
                return notificar(_("Esta carpeta no tiene chats que se puedan compartir.") if not err
                                 else _("No se pudo: {e}").format(e=err))
            d = DialogoBase(self, _("Compartir la carpeta"), _("_Crear el enlace"))
            nombre = d.campo(_("_Nombre del enlace:"), Gtk.Entry(text=c.get("title", ""), activates_default=True))
            casillas = []
            for cid, titulo in res:
                chk = Gtk.CheckButton(label=titulo, active=True)
                d.rejilla.attach(chk, 1, d._fila, 1, 1)
                d._fila += 1
                casillas.append((cid, chk))
            d.set_focus(nombre)

            def _ok():
                ids = [i for i, k in casillas if k.get_active()]
                n = nombre.get_text().strip()
                d.close()

                def _fin(enlace, e2):
                    if e2:
                        return notificar(_("No se pudo: {e}").format(e=e2))
                    texto = getattr(enlace, "link", None) or getattr(enlace, "invite_link", None) or str(enlace)
                    copiar_al_portapapeles(texto, _("Enlace de la carpeta copiado"))
                ejecutar(lambda: telegram_service.crear_enlace_carpeta(self.account_id, c["id"], ids, n), _fin)
            d.aceptar = _ok
            d.present()
        ejecutar(lambda: telegram_service.chats_para_enlace_carpeta(self.account_id, c["id"]), _chats)


# --------------------------------------------------------------------------- crear y unirse
def crear_chat(padre, account_id: int, al_crear):
    """Crear un grupo, un supergrupo (con temas si quieres) o un canal."""
    d = DialogoBase(padre, _("Crear un chat"), _("_Crear"))
    tipo = d.campo(_("_Tipo:"), Gtk.DropDown.new_from_strings([_("Grupo"), _("Supergrupo"), _("Foro (supergrupo con temas)"), _("Canal")]))
    titulo = d.campo(_("_Título:"), Gtk.Entry(activates_default=True, width_chars=40))
    desc = d.campo(_("_Descripción (opcional):"), Gtk.Entry(activates_default=True))
    miembros = d.campo(_("_Miembros del grupo (@usuario separados por comas):"), Gtk.Entry(activates_default=True))
    d.set_focus(titulo)

    def _ok():
        t = titulo.get_text().strip()
        k = tipo.get_selected()
        if not t:
            return notificar(_("Escribe el título."))
        ms = [m.strip() for m in miembros.get_text().replace(";", ",").split(",") if m.strip()]
        if k == 0 and not ms:
            return notificar(_("Un grupo necesita al menos un miembro."))
        ds = desc.get_text().strip()
        d.close()
        S, a = telegram_service, account_id
        fn = {0: lambda: S.crear_grupo(a, t, ms), 1: lambda: S.crear_supergrupo(a, t, ds, False),
              2: lambda: S.crear_supergrupo(a, t, ds, True), 3: lambda: S.crear_canal(a, t, ds)}[k]
        _hacer(padre, fn, _("Chat creado."), lambda cid: al_crear(cid, t))
    d.aceptar = _ok
    d.present()


def unirse_por_enlace(padre, account_id: int, al_unir):
    _pedir(padre, _("Unirse a un chat"), _("_Enlace de invitación o @usuario público:"), lambda e: e and _hacer(
        padre, lambda: telegram_service.unirse_por_enlace(account_id, e), _("Te has unido."), lambda r: al_unir(r)), _("_Unirme"))


# --------------------------------------------------------------------------- búsquedas
class DialogoResultadosTG(DialogoLista):
    """Resultados de una búsqueda: Intro abre el chat; «Copiar» deja el texto en el portapapeles."""

    def __init__(self, padre, titulo: str, resultados: list, abrir=None):
        super().__init__(padre, titulo, _("Resultados"), lambda pos: self._abrir(pos))
        self.resultados, self.abrir = resultados, abrir
        self.lista.poner([r["linea"] for r in resultados])
        if abrir:
            self.boton(_("_Abrir el chat"), lambda: self._abrir(self.lista.posicion()))
        self.boton(_("_Copiar el texto"), self._copiar)
        self.cerrar_boton()
        self.mostrar_estado(_("{n} resultados").format(n=len(resultados)) if resultados else _("Sin resultados."))

    def _abrir(self, pos):
        if self.abrir and 0 <= pos < len(self.resultados):
            self.close()
            self.abrir(self.resultados[pos])

    def _copiar(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.resultados):
            copiar_al_portapapeles(self.resultados[p].get("texto", ""), _("Texto copiado"))


def buscar_global(padre, account_id: int, abrir_chat):
    def _con(texto):
        def _fin(res, err):
            if err:
                return notificar(_("No se pudo buscar: {e}").format(e=err))
            filas = [{"linea": f"{r['chat_title']}: {r['text']} — {fecha_legible(r['date'])}", "texto": r["text"], **r}
                     for r in res or []]
            DialogoResultadosTG(padre, _("Resultados de «{t}»").format(t=texto), filas,
                                lambda r: abrir_chat(account_id, r["chat_id"], r["chat_title"])).present()
        ejecutar(lambda: telegram_service.buscar_global(account_id, texto), _fin)
    DialogoTexto(padre, _("Buscar en todos los chats"), _("_Buscar:"), _con, aceptar=_("_Buscar")).present()


def buscar_en_chat(padre, account_id: int, chat_id: int, titulo: str):
    def _con(texto):
        def _fin(res, err):
            if err:
                return notificar(_("No se pudo buscar: {e}").format(e=err))
            filas = [{"linea": f"{r['autor']}: {r['texto'] or r['media']} — {fecha_legible(r['fecha'])}", "texto": r["texto"]}
                     for r in res or []]
            DialogoResultadosTG(padre, _("«{t}» en {c}").format(t=texto, c=titulo), filas).present()
        ejecutar(lambda: telegram_service.buscar_mensajes_resultados(account_id, chat_id, texto), _fin)
    DialogoTexto(padre, _("Buscar en el chat"), _("_Buscar:"), _con, aceptar=_("_Buscar")).present()


def buscar_publicaciones(padre, account_id: int):
    def _con(tag):
        def _fin(res, err):
            if err:
                return notificar(_("No se pudo buscar: {e}").format(e=err))
            filas = [{"linea": " ".join(str(v) for v in (r.get("chat_title") or r.get("canal"), r.get("text") or r.get("texto"),
                                                         r.get("date") or r.get("fecha")) if v), "texto": r.get("text") or r.get("texto", "")}
                     for r in res or []]
            DialogoResultadosTG(padre, _("Publicaciones de {t}").format(t=tag), filas).present()
        ejecutar(lambda: telegram_service.buscar_publicaciones_canales(account_id, tag.lstrip("#")), _fin)
    DialogoTexto(padre, _("Buscar publicaciones de canales"), _("_Hashtag:"), _con, aceptar=_("_Buscar")).present()


# --------------------------------------------------------------------------- sugerencias y medios
def chats_frecuentes(padre, account_id: int, abrir_chat):
    def _cat(i):
        def _fin(res, err):
            if err:
                return notificar(_("No se pudo: {e}").format(e=err))
            filas = [{"linea": f"{n} ({t})", "id": cid, "nombre": n} for cid, n, t in res or []]
            DialogoResultadosTG(padre, _("Chats frecuentes: {c}").format(c=_(CATEGORIAS[i][1])), filas,
                                lambda r: abrir_chat(account_id, r["id"], r["nombre"])).present()
        ejecutar(lambda: telegram_service.chats_frecuentes(account_id, CATEGORIAS[i][0], 20), _fin)
    DialogoElegir(padre, _("Chats frecuentes"), _("_Categoría:"), [_(n) for _c, n in CATEGORIAS], _cat, _("_Ver")).present()


def canales_similares(padre, account_id: int, chat_id: int, titulo: str, abrir_chat):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        filas = [{"linea": _("{n}, {m} miembros").format(n=n, m=m), "id": cid, "nombre": n} for cid, n, m in res or []]
        DialogoResultadosTG(padre, _("Canales parecidos a {t}").format(t=titulo), filas,
                            lambda r: abrir_chat(account_id, r["id"], r["nombre"])).present()
    ejecutar(lambda: telegram_service.canales_similares(account_id, chat_id), _fin)


def medios_del_chat(padre, account_id: int, chat_id: int, titulo: str):
    def _tipo(i):
        def _fin(res, err):
            if err:
                return notificar(_("No se pudo: {e}").format(e=err))
            filas = [{"linea": " ".join(x for x in (r.get("file_name"), r.get("texto"), fecha_legible(r.get("fecha", ""))) if x),
                      "texto": r.get("texto", "")} for r in res or []]
            DialogoResultadosTG(padre, f"{_(MEDIOS[i][1])} — {titulo}", filas).present()
        ejecutar(lambda: telegram_service.medios_del_chat(account_id, chat_id, MEDIOS[i][0]), _fin)
    DialogoElegir(padre, _("Contenido compartido"), _("_Tipo:"), [_(n) for _c, n in MEDIOS], _tipo, _("_Ver")).present()
