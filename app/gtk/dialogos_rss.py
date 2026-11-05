# -*- coding: utf-8 -*-
"""Gestión de feeds RSS en la interfaz GTK: editar un feed, categorías, etiquetas, historial y estadísticas de lectura,
salud de los feeds, descargas de podcast, galería de imágenes y carpetas inteligentes. Usan los mismos servicios y datos que
la versión de Windows."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.database import db
from app.core.speech import notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service

from .dialogos import DialogoBase, DialogoTexto, confirmar_async
from .dialogos_mastodon import DialogoLista

log = logging.getLogger(__name__)

IDENTIDADES = ("", "navegador", "propia")


def desplazable(dialogo, alto: int = 560, ancho: int = 600):
    """Formularios largos: el contenido del diálogo pasa a una zona con desplazamiento y la ventana se puede redimensionar."""
    caja = dialogo.get_child()
    dialogo.set_child(None)
    dialogo.set_child(Gtk.ScrolledWindow(child=caja, propagate_natural_width=True))
    dialogo.set_resizable(True)
    dialogo.set_default_size(ancho, alto)


# --------------------------------------------------------------------------- editar un feed
class DialogoEditarFeed(DialogoBase):
    """Título, dirección, carpeta, intervalo, identificación, imágenes, texto completo, pausa y qué hace Suprimir. Un botón
    comprueba la dirección y explica qué pasa. Si cambia la dirección, el feed se actualiza al guardar."""

    def __init__(self, padre, feed_id, al_guardar):
        super().__init__(padre, _("Editar feed"), _("_Guardar"))
        fila = db.query_one(
            "SELECT account_id, title, url, category_id, COALESCE(custom_interval,0), COALESCE(custom_user_agent,''), "
            "COALESCE(no_images,0), COALESCE(force_fulltext,0), COALESCE(paused,0), COALESCE(last_error,'') "
            "FROM rss_feeds WHERE id = ?", (feed_id,))
        if not fila:
            raise ValueError(_("El feed ya no existe."))
        (self.account_id, titulo, url, cat_id, intervalo, ua, sin_img, completo, pausado, ultimo_error) = fila
        self.feed_id, self.al_guardar = feed_id, al_guardar
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (self.account_id,))
        self.sugerida, self.navegador_detectado = "", False
        self.titulo = self.campo(_("_Título:"), Gtk.Entry(text=titulo or "", width_chars=50, activates_default=True))
        self.url = self.campo(_("_Dirección (URL) del feed:"), Gtk.Entry(text=url or "", width_chars=50, activates_default=True),
                              _("Puedes pegar también la dirección de una web: «Comprobar» busca sus feeds."))
        botones = Gtk.Box(spacing=8)
        self.b_comprobar = Gtk.Button(label=_("C_omprobar la dirección"), use_underline=True)
        self.b_comprobar.connect("clicked", lambda *_a: self._comprobar())
        self.b_usar = Gtk.Button(label=_("_Usar la dirección sugerida"), use_underline=True, sensitive=False)
        self.b_usar.connect("clicked", lambda *_a: self._usar_sugerida())
        botones.append(self.b_comprobar)
        botones.append(self.b_usar)
        self.rejilla.attach(botones, 1, self._fila, 1, 1)
        self._fila += 1
        self.resultado = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6,
                                      right_margin=6, top_margin=4, bottom_margin=4)
        self.resultado.get_buffer().set_text(_("Último error al actualizar: {0}").format(ultimo_error) if ultimo_error else "")
        self.campo(_("_Resultado de la comprobación:"),
                   Gtk.ScrolledWindow(child=self.resultado, min_content_height=90, min_content_width=420, has_frame=True))
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.resultado)
        self.carpeta = self.campo(_("_Carpeta:"), Gtk.DropDown.new_from_strings([_("Sin carpeta")] + [n for _i, n in self.cats]))
        ids = [None] + [i for i, _n in self.cats]
        self.carpeta.set_selected(ids.index(cat_id) if cat_id in ids else 0)
        self.intervalo = self.campo(_("_Intervalo de actualización propio en minutos (0 = el general):"),
                                    Gtk.SpinButton.new_with_range(0, 10080, 1))
        self.intervalo.set_value(int(intervalo or 0))
        self.ident = self.campo(_("_Identificarse ante el servidor como:"), Gtk.DropDown.new_from_strings([
            _("La aplicación (y como navegador si el servidor la rechaza)"), _("Un navegador (Firefox)"),
            _("Una identificación propia (User-Agent)")]))
        modo = "" if not ua else ("navegador" if ua == "navegador" else "propia")
        self.ident.set_selected(IDENTIDADES.index(modo))
        self.ident.connect("notify::selected", lambda *_a: self._ajustar_ua())
        self.ua = self.campo(_("Identificación _propia (User-Agent):"),
                             Gtk.Entry(text=ua if modo == "propia" else "", width_chars=50))
        from app.services.rss.rss_avisos import feed_silenciado
        self.sin_img = self.campo("", Gtk.CheckButton(label=_("No descargar las _imágenes de este feed"), use_underline=True,
                                                      active=bool(sin_img)))
        self.completo = self.campo("", Gtk.CheckButton(
            label=_("Descargar siempre el artículo _completo (si el feed solo trae el resumen)"), use_underline=True,
            active=bool(completo)))
        self.pausa = self.campo("", Gtk.CheckButton(label=_("Feed en _pausa (no se actualiza)"), use_underline=True,
                                                    active=bool(pausado)))
        self.silencio = self.campo("", Gtk.CheckButton(
            label=_("No _avisar de sus artículos nuevos (sin sonido ni notificación)"), use_underline=True,
            active=feed_silenciado(feed_id)))
        from app.services.rss.rss_borrados import MODOS, modo_de_feed
        self.suprimir = self.campo(_("Al pulsar _Suprimir en sus artículos:"), Gtk.DropDown.new_from_strings([
            _("Llevarlos a la papelera (se pueden recuperar)"), _("Borrarlos definitivamente (no vuelven a aparecer)")]))
        self.suprimir.set_selected(MODOS.index(modo_de_feed(feed_id)))
        self._ajustar_ua()
        desplazable(self, 640)
        self.set_focus(self.titulo)

    def _ajustar_ua(self):
        self.ua.set_sensitive(self.ident.get_selected() == 2)

    def _texto_resultado(self, texto: str):
        self.resultado.get_buffer().set_text(texto)

    def _comprobar(self):
        url = self.url.get_text().strip()
        if not url:
            notificar(_("Escribe primero la dirección."))
            return
        ident = IDENTIDADES[self.ident.get_selected()]
        ua = self.ua.get_text().strip() if ident == "propia" else ("navegador" if ident == "navegador" else "")
        self.b_comprobar.set_sensitive(False)
        self._texto_resultado(_("Comprobando la dirección…"))
        notificar(_("Comprobando la dirección…"))

        def _fin(res, err):
            self.b_comprobar.set_sensitive(True)
            if err or not res:
                self._texto_resultado(_("No se pudo comprobar: {0}").format(err))
                notificar(_("No se pudo comprobar la dirección."))
                return
            self._texto_resultado(res["texto"])
            self.sugerida = res.get("url_sugerida") or ""
            self.navegador_detectado = bool(res.get("navegador"))
            self.b_usar.set_sensitive(bool(self.sugerida) and self.sugerida.rstrip("/") != url.rstrip("/"))
            notificar(_("Comprobación terminada: {0}").format(
                _("es un feed válido") if res.get("valido") else
                (_("hay una dirección sugerida") if self.sugerida else _("no es un feed"))))
            self.resultado.grab_focus()
        task_manager.start_task(lambda ctx: rss_service.diagnosticar_url(url, ua), callback=_fin, pass_context=True)

    def _usar_sugerida(self):
        if self.sugerida:
            self.url.set_text(self.sugerida)
            notificar(_("Dirección cambiada a la sugerida. Pulsa Guardar para aplicarla."))
            self.url.grab_focus()

    def aceptar(self):
        titulo, url = self.titulo.get_text().strip(), self.url.get_text().strip()
        if not url:
            notificar(_("La dirección no puede quedar vacía."))
            return
        ident = IDENTIDADES[self.ident.get_selected()]
        if ident == "propia":
            ua = self.ua.get_text().strip()
        elif ident == "navegador" or self.navegador_detectado:
            ua = "navegador"
        else:
            ua = ""
        sel = self.carpeta.get_selected()
        cat = None if sel <= 0 else self.cats[sel - 1][0]
        try:
            cambio_url = rss_service.editar_feed(
                self.feed_id, titulo=titulo or None, url=url, category_id=cat,
                custom_interval=int(self.intervalo.get_value()), custom_user_agent=ua,
                no_images=self.sin_img.get_active(), force_fulltext=self.completo.get_active(),
                paused=self.pausa.get_active())
        except ValueError as e:
            notificar(str(e))
            return
        from app.services.rss.rss_avisos import fijar_feed_silenciado
        from app.services.rss.rss_borrados import MODOS, fijar_modo_de_feed
        fijar_feed_silenciado(self.feed_id, self.silencio.get_active())
        fijar_modo_de_feed(self.feed_id, MODOS[self.suprimir.get_selected()])
        self.close()
        self.al_guardar(bool(cambio_url))


def mostrar_articulo(ventana, article_id: str):
    """Muestra un artículo (título, texto y enlace) en un cuadro de solo lectura."""
    from app.core.html_utils import html_a_texto
    from .dialogos_mastodon_extra import DialogoTextoLargo
    art = rss_service.obtener_articulo(article_id)
    if art is None:
        notificar(_("Ese artículo ya no está disponible (se eliminó)."))
        return
    DialogoTextoLargo(ventana, art.title or _("Artículo"),
                      f"{art.title}\n\n{html_a_texto(art.content or '')}\n\n{art.link or ''}").present()


# --------------------------------------------------------------------------- categorías
class DialogoCategorias(DialogoLista):
    """Crear y eliminar las carpetas (categorías) de una cuenta de RSS."""

    def __init__(self, padre, account_id: int, al_cambiar):
        super().__init__(padre, _("Gestionar categorías de RSS"), _("Lista de categorías"))
        self.account_id, self.al_cambiar = account_id, al_cambiar
        self.cats = []
        self.boton(_("_Crear una categoría…"), self._crear)
        self.boton(_("_Eliminar"), self._eliminar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (self.account_id,))
        self.lista.poner([c[1] for c in self.cats])
        if self.cats:
            self.lista.seleccion.set_selected(min(pos, len(self.cats) - 1))
        self.mostrar_estado(_("{0} categorías").format(len(self.cats)) if self.cats else _("No hay categorías."))

    def _crear(self):
        def _nombre(texto):
            try:
                rss_service.agregar_categoria(self.account_id, texto)
            except Exception:
                notificar(_("Error al crear: la categoría podría ya existir."))
                return
            notificar(_("Categoría creada con éxito."))
            self._cargar()
            self.al_cambiar()
        DialogoTexto(self, _("Nueva categoría"), _("_Nombre de la categoría:"), _nombre, aceptar=_("_Crear")).present()

    def _eliminar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.cats):
            return
        c_id, nombre = self.cats[p]

        def _hacer():
            db.execute("DELETE FROM rss_categories WHERE id = ?", (c_id,))
            notificar(_("Categoría eliminada."))
            self._cargar(p)
            self.al_cambiar()
        confirmar_async(self, _("¿Seguro que quieres eliminar la categoría «{name}»? Los feeds dentro de ella se mantendrán "
                                "sin categoría.").format(name=nombre), _hacer, etiqueta_si=_("Eliminar"))


# --------------------------------------------------------------------------- etiquetas
class DialogoEtiquetas(DialogoLista):
    """Gestor de etiquetas de RSS: crear, ver (con cuántos feeds y artículos etiquetan) y borrar."""

    def __init__(self, padre, account_id: int, al_cambiar):
        super().__init__(padre, _("Etiquetas de RSS"), _("Lista de etiquetas"))
        self.account_id, self.al_cambiar = account_id, al_cambiar
        self.items = []
        self.boton(_("_Crear una etiqueta…"), self._crear)
        self.boton(_("_Eliminar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.items = rss_service.obtener_etiquetas(self.account_id)
        self.lista.poner([_("{0}. {1} feeds, {2} artículos.").format(i["name"], i["feeds"], i["articulos"]) for i in self.items])
        if self.items:
            self.lista.seleccion.set_selected(min(pos, len(self.items) - 1))
        self.mostrar_estado(_("{0} etiquetas").format(len(self.items)) if self.items else _("No hay etiquetas."))

    def _crear(self):
        def _nombre(texto):
            rss_service.crear_etiqueta(self.account_id, texto)
            notificar(_("Etiqueta creada."))
            self._cargar(len(self.items))
            self.al_cambiar()
        DialogoTexto(self, _("Nueva etiqueta"), _("_Nombre de la etiqueta:"), _nombre, aceptar=_("_Crear")).present()

    def _borrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        it = self.items[p]

        def _hacer():
            rss_service.borrar_etiqueta(it["id"])
            self._cargar(p)
            self.al_cambiar()
        confirmar_async(self, _("¿Eliminar la etiqueta «{0}»? Los feeds y artículos no se borran.").format(it["name"]),
                        _hacer, etiqueta_si=_("Eliminar"))


class DialogoAsignarEtiquetas(DialogoBase):
    """Marca las etiquetas de un feed o de un artículo con casillas; se puede crear una nueva sobre la marcha."""

    def __init__(self, padre, account_id: int, feed_id=None, article_id=None, al_cambiar=None):
        super().__init__(padre, _("Etiquetas"), _("_Aceptar"))
        self.account_id, self.feed_id, self.article_id, self.al_cambiar = account_id, feed_id, article_id, al_cambiar
        self.etiquetas = rss_service.obtener_etiquetas(account_id)
        self.actuales = set(rss_service.obtener_etiquetas_de_feed(feed_id) if feed_id
                            else rss_service.obtener_etiquetas_de_articulo(article_id))
        self.caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.checks = []
        self._pintar(set(self.actuales))
        objeto = _("este feed") if feed_id else _("este artículo")
        self.campo(_("_Etiquetas de {0}:").format(objeto),
                   Gtk.ScrolledWindow(child=self.caja, min_content_height=220, min_content_width=360, has_frame=True))
        self.nueva = self.campo(_("Crear y asignar una etiqueta _nueva:"), Gtk.Entry(width_chars=30, activates_default=False))
        self.nueva.connect("activate", lambda *_a: self._anadir())
        b = Gtk.Button(label=_("_Añadir"), use_underline=True)
        b.connect("clicked", lambda *_a: self._anadir())
        self.rejilla.attach(b, 1, self._fila, 1, 1)
        self._fila += 1

    def _pintar(self, marcadas: set):
        while (h := self.caja.get_first_child()) is not None:
            self.caja.remove(h)
        self.checks = []
        for t in self.etiquetas:
            cb = Gtk.CheckButton(label=t["name"], active=t["id"] in marcadas)
            self.caja.append(cb)
            self.checks.append(cb)
        if not self.etiquetas:
            self.caja.append(Gtk.Label(label=_("Todavía no hay etiquetas."), xalign=0))

    def _anadir(self):
        nombre = self.nueva.get_text().strip()
        if not nombre:
            return
        marcadas = {t["id"] for t, cb in zip(self.etiquetas, self.checks) if cb.get_active()}
        rss_service.crear_etiqueta(self.account_id, nombre)
        self.nueva.set_text("")
        self.etiquetas = rss_service.obtener_etiquetas(self.account_id)
        marcadas |= {t["id"] for t in self.etiquetas if t["name"] == nombre}
        self._pintar(marcadas)
        notificar(_("Etiqueta «{0}» creada y marcada.").format(nombre))

    def aceptar(self):
        for t, cb in zip(self.etiquetas, self.checks):
            poner = cb.get_active()
            if self.feed_id:
                rss_service.etiquetar_feed(t["id"], self.feed_id, poner)
            else:
                rss_service.etiquetar_articulo(t["id"], self.article_id, poner)
        self.close()
        if self.al_cambiar:
            self.al_cambiar()


# --------------------------------------------------------------------------- galería de imágenes
class DialogoGaleria(DialogoLista):
    """Imágenes de un artículo con su texto alternativo: abrir, copiar la dirección o describir con IA (bajo petición)."""

    def __init__(self, padre, article_id: str):
        super().__init__(padre, _("Imágenes del artículo"), _("Lista de imágenes"))
        self.items = rss_service.obtener_imagenes_articulo(article_id)
        self.lista.poner([it["alt"] or _("(sin descripción)") for it in self.items])
        self.mostrar_estado(_("{0} imágenes (con su texto alternativo).").format(len(self.items)) if self.items
                            else _("Este artículo no tiene imágenes."))
        self.boton(_("_Abrir en el navegador"), self._abrir)
        self.boton(_("_Copiar la dirección"), self._copiar)
        self.boton(_("_Describir con IA"), self._describir)
        self.cerrar_boton()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _abrir(self):
        it = self._actual()
        if it:
            from app.core.privacy import abrir_url
            abrir_url(it["url"])

    def _copiar(self):
        it = self._actual()
        if it:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(it["url"], _("Dirección copiada."))

    def _describir(self):
        it = self._actual()
        if not it:
            return
        notificar(_("Descargando y describiendo la imagen con IA…"))

        def _bg(ctx):
            # Solo aquí se descarga la imagen remota (privacidad), a un temporal acotado que se borra al terminar.
            import os
            import tempfile
            from urllib.parse import urlparse
            import requests
            from app.services.ai.ai_service import ai_service
            ext = os.path.splitext(urlparse(it["url"]).path)[1][:5] or ".jpg"
            ruta = os.path.join(tempfile.gettempdir(), f"socialrss_img_{abs(hash(it['url']))}{ext}")
            maximo = 15 * 1024 * 1024
            with requests.get(it["url"], stream=True, timeout=30, headers={"User-Agent": "SocialRSS Suite"}) as resp:
                resp.raise_for_status()
                hecho = 0
                with open(ruta, "wb") as fh:
                    for chunk in resp.iter_content(65536):
                        hecho += len(chunk or b"")
                        if hecho > maximo:
                            raise ValueError("Imagen demasiado grande.")
                        fh.write(chunk)
            try:
                return ai_service.describir_imagen(ruta)
            finally:
                try:
                    os.remove(ruta)
                except OSError:
                    pass

        def _fin(desc, err):
            if err or not desc:
                notificar(_("No se pudo describir la imagen: {0}").format(err or ""))
                return
            from .dialogos_mastodon_extra import DialogoTextoLargo
            DialogoTextoLargo(self, _("Descripción de la imagen"), desc).present()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- historial y estadísticas de lectura
class DialogoHistorialLectura(DialogoLista):
    """Los últimos artículos abiertos de una cuenta; Intro vuelve a abrir el artículo."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Historial de lectura"), _("Lista del historial de lectura"), al_activar=lambda pos: self._abrir())
        self.account_id = account_id
        self.items = []
        self.boton(_("_Abrir"), self._abrir)
        self.boton(_("_Vaciar el historial"), self._vaciar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self):
        from .datos import fecha_legible
        self.items = rss_service.obtener_historial_lectura(self.account_id)
        self.lista.poner([". ".join(x for x in (it.get("title") or _("(sin título)"), it.get("feed_title") or "",
                                                fecha_legible(it.get("opened_at", ""))) if x) for it in self.items])
        self.mostrar_estado(_("{0} artículos leídos (Intro para reabrir).").format(len(self.items)) if self.items
                            else _("El historial está vacío."))

    def _abrir(self):
        p = self.lista.posicion()
        if 0 <= p < len(self.items):
            it = self.items[p]
            if not it.get("existe"):
                notificar(_("Ese artículo ya no está disponible (se eliminó)."))
                return
            mostrar_articulo(self, it["article_db_id"])

    def _vaciar(self):
        def _hacer():
            n = rss_service.vaciar_historial_lectura(self.account_id)
            notificar(_("Historial vaciado ({0} registros).").format(n))
            self._cargar()
        confirmar_async(self, _("¿Borrar todo el historial de lectura?"), _hacer, etiqueta_si=_("Vaciar"))


def texto_estadisticas_lectura(account_id: int) -> str:
    st = rss_service.obtener_estadisticas_lectura(account_id)
    lineas = [_("Artículos leídos hoy: {0}").format(st.get("hoy", 0)), _("Esta semana: {0}").format(st.get("semana", 0)),
              _("Total histórico: {0}").format(st.get("total", 0)), _("Sin leer pendientes: {0}").format(st.get("no_leidos", 0)),
              "", _("Feeds que más lees:")]
    top = st.get("top_feeds", [])
    lineas += [_("  {0}: {1} artículos").format(f.get("feed", ""), f.get("n", 0)) for f in top] or [
        _("  (todavía no hay datos)")]
    return "\n".join(lineas)


# --------------------------------------------------------------------------- crear un feed desde una web sin RSS
class DialogoFeedWeb(DialogoBase):
    """Analiza una web sin RSS, muestra los artículos detectados y crea un feed que se actualiza re-analizando la web."""

    def __init__(self, padre, account_id: int, al_crear):
        super().__init__(padre, _("Crear feed desde una web"), _("_Crear el feed"))
        self.account_id, self.al_crear = account_id, al_crear
        self.items, self.selector = [], ""
        self.url = self.campo(_("_Dirección de la web (página de noticias, blog, etc.):"),
                              Gtk.Entry(width_chars=50), _("Pega la dirección de una web que NO tenga RSS y pulsa Analizar."))
        self.b_analizar = Gtk.Button(label=_("_Analizar la web"), use_underline=True)
        self.b_analizar.connect("clicked", lambda *_a: self._analizar())
        self.rejilla.attach(self.b_analizar, 1, self._fila, 1, 1)
        self._fila += 1
        self.estado = Gtk.Label(xalign=0, accessible_role=Gtk.AccessibleRole.STATUS)
        self.rejilla.attach(self.estado, 0, self._fila, 2, 1)
        self._fila += 1
        self.vista = Gtk.TextView(accepts_tab=False, editable=False, cursor_visible=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=6,
                                  right_margin=6)
        self.campo(_("Artículos _detectados:"), Gtk.ScrolledWindow(child=self.vista, min_content_height=160,
                                                                   min_content_width=460, has_frame=True))
        self.rejilla.get_child_at(0, self._fila - 1).set_mnemonic_widget(self.vista)
        self.titulo = self.campo(_("_Título del feed:"), Gtk.Entry(width_chars=50))
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ?", (account_id,))
        self.carpeta = self.campo(_("C_ategoría:"), Gtk.DropDown.new_from_strings([_("Sin categoría")] + [c[1] for c in self.cats]))
        self.boton_aceptar.set_sensitive(False)
        self.set_focus(self.url)

    def _analizar(self):
        url = self.url.get_text().strip()
        if not url:
            notificar(_("Escribe la dirección de la web."))
            return
        self.estado.set_text(_("Analizando la web…"))
        self.b_analizar.set_sensitive(False)

        def _fin(res, err):
            self.b_analizar.set_sensitive(True)
            if err or not res:
                self.estado.set_text(_("No se pudo analizar la web: {0}").format(err or ""))
                return
            self.items = res.get("items", [])
            self.selector = res.get("selector", "")
            self.vista.get_buffer().set_text("\n".join(
                f"{it.get('title', '') or _('(sin título)')}. {(it.get('summary', '') or '')[:120]}" for it in self.items))
            if not self.titulo.get_text().strip():
                self.titulo.set_text(res.get("titulo_pagina", "") or url)
            self.boton_aceptar.set_sensitive(bool(self.items))
            self.estado.set_text(_("{0} artículos detectados. Revisa y pulsa Crear el feed.").format(len(self.items))
                                 if self.items else _("No se detectaron artículos en esta web. Puede que no sea compatible."))
            notificar(self.estado.get_text())
        task_manager.start_task(lambda ctx: rss_service.analizar_web(url), callback=_fin, pass_context=True)

    def aceptar(self):
        url, titulo = self.url.get_text().strip(), self.titulo.get_text().strip()
        if not url or not self.items:
            notificar(_("Primero analiza una web con artículos."))
            return
        sel = self.carpeta.get_selected()
        categoria = self.cats[sel - 1][0] if sel > 0 else None
        notificar(_("Creando el feed…"))

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear el feed: {0}").format(err))
                return
            notificar(_("Feed creado desde la web."))
            self.close()
            self.al_crear()
        task_manager.start_task(lambda ctx: rss_service.crear_feed_web(self.account_id, url, titulo, categoria, self.selector, ctx=ctx),
                                callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- salud de los feeds
class DialogoSaludFeeds(DialogoLista):
    """El estado real de cada feed (último éxito, error, fallos seguidos y próxima comprobación) con acciones: actualizar
    ahora, pausar o reactivar y reintentar todos los rotos."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Salud de los feeds"), _("Lista de salud de los feeds"))
        self.account_id = account_id
        self.items = []
        self.boton(_("_Actualizar ahora"), self._actualizar)
        self.boton(_("_Pausar o reactivar"), self._pausa)
        self.b_rotos = self.boton(_("_Reintentar todos los rotos"), self._rotos)
        self.boton(_("C_opiar detalles"), self._copiar)
        self.cerrar_boton()
        self._cargar()

    @staticmethod
    def _cuando(iso):
        if not iso:
            return _("nunca")
        from .datos import fecha_legible
        return fecha_legible(iso)

    def _cargar(self, foco_id=None):
        self.items = rss_service.obtener_salud_feeds(self.account_id)
        estados = {"ok": _("Correcto"), "fallando": _("Fallando"), "pausado": _("En pausa")}
        lineas, foco = [], 0
        for i, it in enumerate(self.items):
            estado = estados.get(it["estado"], it["estado"]) + (_(" ({0} fallos)").format(it["fail_count"]) if it["fail_count"] else "")
            prox = _("(pausado)") if it["paused"] else (it["next_update_at"][:16].replace("T", " ") if it["next_update_at"] else "")
            lineas.append("{0}. {1}. {2}: {3}. {4}: {5}. {6}".format(
                it["title"] or it["url"], estado, _("Último éxito"), self._cuando(it["last_success_at"]),
                _("Próximo intento"), prox or _("cuando toque"), it["last_error"] or "").rstrip(". "))
            if foco_id is not None and it["id"] == foco_id:
                foco = i
        self.lista.poner(lineas)
        if self.items:
            self.lista.seleccion.set_selected(foco)
        rotos = sum(1 for it in self.items if it["estado"] != "ok")
        self.mostrar_estado(_("{0} feeds con problemas de {1}.").format(rotos, len(self.items)) if rotos
                            else _("Todos los feeds están bien ({0}).").format(len(self.items)))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _actualizar(self):
        it = self._actual()
        if not it:
            return
        notificar(_("Actualizando {0}…").format(it["title"]))
        # El refresco manual se salta la programación: se borra su próximo intento.
        db.execute("UPDATE rss_feeds SET next_update_at = NULL, paused = 0 WHERE id = ?", (it["id"],))

        def _fin(n, err):
            notificar(_("Sigue fallando: {0}").format(err) if err else _("{0}: {1} artículos nuevos.").format(it["title"], n or 0))
            self._cargar(foco_id=it["id"])
        rss_service.actualizar_feed_async(it["id"], callback=_fin)

    def _pausa(self):
        it = self._actual()
        if it:
            rss_service.set_feed_paused(it["id"], not it["paused"])
            notificar(_("Feed reactivado.") if it["paused"] else _("Feed en pausa."))
            self._cargar(foco_id=it["id"])

    def _rotos(self):
        notificar(_("Reintentando los feeds con fallos…"))
        self.b_rotos.set_sensitive(False)

        def _fin(n, err):
            self.b_rotos.set_sensitive(True)
            notificar(_("Error al reintentar: {0}").format(err) if err else _("No hay feeds con fallos que reintentar.")
                      if not n else _("Reintentados {0} feeds; revisa su estado.").format(n))
            self._cargar()
        rss_service.reintentar_feeds_rotos_async(self.account_id, callback=_fin)

    def _copiar(self):
        it = self._actual()
        if it:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles("{0}\n{1}\nEstado: {2} | Fallos: {3} | HTTP: {4}\nÚltimo intento: {5}\nÚltimo éxito: {6}\n"
                                   "Próximo: {7}\nError: {8}".format(
                                       it["title"], it["url"], it["estado"], it["fail_count"], it["last_status"] or "",
                                       it["last_attempt_at"], it["last_success_at"], it["next_update_at"], it["last_error"]),
                                   _("Detalles copiados al portapapeles."))


# --------------------------------------------------------------------------- descargas de podcast
class DialogoDescargasPodcast(DialogoLista):
    """Cola de descargas de podcast de una cuenta: en curso, en cola, pausados y descargados, con estado y progreso. Se
    refresca sola mientras está abierta."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Descargas de podcast"), _("Cola de descargas de podcast"), al_activar=lambda pos: self._reproducir())
        from gi.repository import GLib
        self.account_id = account_id
        self.items = []
        for et, fn in ((_("_Reproducir"), self._reproducir), (_("_Pausar"), self._pausar), (_("R_eanudar"), self._reanudar),
                       (_("_Cancelar"), self._cancelar), (_("_Borrar descarga"), self._borrar)):
            self.boton(et, fn)
        self.cerrar_boton()
        self._cargar()
        self._temporizador = GLib.timeout_add_seconds(1, self._tic)
        self.connect("close-request", self._al_cerrar)

    def _al_cerrar(self, *_a):
        from gi.repository import GLib
        if self._temporizador:
            GLib.source_remove(self._temporizador)
            self._temporizador = 0
        return False

    def _tic(self):
        self._cargar()
        return True

    @staticmethod
    def _tam(n):
        n = float(n or 0)
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024 or u == "GB":
                return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024
        return ""

    def _combinar(self):
        cola = rss_service.obtener_cola_descargas(self.account_id)
        vistos = {c["article_id"] for c in cola}
        for d in rss_service.obtener_descargas_podcast(self.account_id):
            if d["article_id"] not in vistos:
                cola.append({"article_id": d["article_id"], "title": d["title"], "feed_title": d["feed_title"],
                             "estado": "hecho", "bytes_hechos": d["size"], "bytes_total": d["size"], "porcentaje": 100,
                             "error": "", "reproducido": rss_service._episodio_reproducido(d["path"], None)})
        return cola

    def _cargar(self):
        estados = {"descargando": _("Descargando"), "cola": _("En cola"), "pausado": _("Pausado"), "error": _("Error"),
                   "hecho": _("Descargado")}
        foco_id = self.items[self.lista.posicion()]["article_id"] if 0 <= self.lista.posicion() < len(self.items) else None
        nuevos = self._combinar()
        lineas, foco = [], 0
        for i, it in enumerate(nuevos):
            estado = estados.get(it["estado"], it["estado"])
            if it["estado"] == "hecho" and it.get("reproducido"):
                estado += ", " + _("escuchado")
            if it["estado"] == "error" and it.get("error"):
                estado = _("Error: {0}").format(it["error"][:40])
            prog = f"{it['porcentaje']}%" if it["estado"] in ("descargando", "pausado") else ""
            lineas.append("{0}. {1}. {2}. {3} {4}".format(it["title"] or _("(sin título)"), it["feed_title"] or "", estado, prog,
                                                          self._tam(it["bytes_total"] or it["bytes_hechos"])).strip())
            if it["article_id"] == foco_id:
                foco = i
        # Si nada cambió no se repinta, para no mover el foco ni hacer hablar al lector sin motivo.
        if lineas != getattr(self, "_lineas", None):
            self._lineas = lineas
            self.items = nuevos
            self.lista.poner(lineas)
            if nuevos:
                self.lista.seleccion.set_selected(foco)
        try:
            self.mostrar_estado(_("Espacio de podcasts: {0}").format(self._tam(rss_service.espacio_podcasts_bytes()))
                                if nuevos else _("No hay descargas ni episodios en cola."))
        except Exception:
            pass

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _reproducir(self):
        it = self._actual()
        if not it:
            return
        if it["estado"] != "hecho":
            notificar(_("Ese episodio aún no está descargado."))
            return
        rss_service.reproducir_articulo_audio(it["article_id"], callback=lambda ok, err=None: None if ok else notificar(
            _("No se pudo reproducir: {0}").format(err or "")))
        notificar(_("Reproduciendo: {0}").format(it["title"]))

    def _pausar(self):
        it = self._actual()
        if it and it["estado"] in ("descargando", "cola"):
            rss_service.pausar_descarga(it["article_id"])
            notificar(_("Descarga pausada."))
            self._cargar()

    def _reanudar(self):
        it = self._actual()
        if it and it["estado"] in ("pausado", "error"):
            rss_service.reanudar_descarga(it["article_id"])
            notificar(_("Descarga reanudada."))
            self._cargar()

    def _cancelar(self):
        it = self._actual()
        if it and it["estado"] in ("descargando", "cola", "pausado", "error"):
            rss_service.cancelar_descarga(it["article_id"])
            notificar(_("Descarga cancelada."))
            self._cargar()

    def _borrar(self):
        it = self._actual()
        if not it or it["estado"] != "hecho":
            return

        def _hacer():
            rss_service.borrar_descarga_podcast(it["article_id"])
            notificar(_("Descarga borrada."))
            self._cargar()
        confirmar_async(self, _("¿Borrar la descarga de «{0}»?").format(it["title"]), _hacer, etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- sincronización con un servidor
class DialogoSincronizacion(DialogoBase):
    """Sincroniza una cuenta RSS con un servidor (Google Reader: FreshRSS, Inoreader...; o Miniflux) para compartir los feeds
    y lo leído y destacado entre dispositivos. Las credenciales se guardan cifradas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Sincronización con servidor"), _("_Probar y guardar"))
        self.account_id = account_id
        cfg = rss_service.obtener_config_sync(account_id)
        self.rejilla.attach(Gtk.Label(wrap=True, xalign=0, width_chars=60, max_width_chars=60, label=_(
            "Sincroniza esta cuenta RSS con un servidor para compartir tus feeds y lo leído y destacado entre dispositivos. "
            "Tus credenciales se guardan cifradas.")), 0, 0, 2, 1)
        self._fila = 1
        self.tipo = self.campo(_("_Tipo de servidor:"), Gtk.DropDown.new_from_strings([
            _("Google Reader (FreshRSS, Inoreader, BazQux, The Old Reader...)"), _("Miniflux")]))
        self.tipo.set_selected(0 if cfg.get("tipo", "greader") == "greader" else 1)
        self.url = self.campo(_("_Dirección del servidor (URL del API):"), Gtk.Entry(text=cfg.get("base_url", ""), width_chars=50),
                              _("The Old Reader: https://theoldreader.com. FreshRSS: la dirección de su API, que suele terminar "
                                "en /api/greader.php. Miniflux: la URL base de tu instancia."))
        self.usuario = self.campo(_("_Usuario (déjalo vacío si usas un token de API):"),
                                  Gtk.Entry(text=cfg.get("usuario", ""), width_chars=40))
        self.clave = self.campo(_("_Contraseña o token de API:"), Gtk.PasswordEntry(show_peek_icon=True, width_chars=40))
        self.intervalo = self.campo(_("_Sincronizar cada (minutos):"), Gtk.SpinButton.new_with_range(5, 1440, 1))
        self.intervalo.set_value(int(cfg.get("intervalo_min", 30)))
        self.enviar = self.campo("", Gtk.CheckButton(
            label=_("_Llevar también al servidor las altas, bajas, cambios de nombre y de carpeta de los feeds"),
            use_underline=True, active=bool(cfg.get("enviar_suscripciones", False))))
        self.enviar.connect("toggled", lambda c: rss_service.fijar_enviar_suscripciones(self.account_id, c.get_active()))
        fila = Gtk.Box(spacing=8)
        for et, fn in ((_("_Sincronizar ahora"), self._sincronizar), (_("_Desactivar"), self._desactivar)):
            b = Gtk.Button(label=et, use_underline=True)
            b.connect("clicked", lambda *_a, fn=fn: fn())
            fila.append(b)
        self.rejilla.attach(fila, 1, self._fila, 1, 1)
        self._fila += 1
        self.set_focus(self.tipo)

    def aceptar(self):
        url = self.url.get_text().strip()
        if not url:
            notificar(_("Escribe la dirección del servidor."))
            return
        usuario, secreto = self.usuario.get_text().strip(), self.clave.get_text()
        tipo = "greader" if self.tipo.get_selected() == 0 else "miniflux"
        intervalo, enviar = int(self.intervalo.get_value()), self.enviar.get_active()
        token = secreto if (tipo == "miniflux" and not usuario) else ""
        password = "" if token else secreto
        notificar(_("Verificando credenciales…"))

        def _fin(res, err):
            notificar(_("No se pudo conectar: {0}").format(err) if err else _("Sincronización configurada y verificada."))
        task_manager.start_task(lambda ctx: rss_service.configurar_sync(
            self.account_id, tipo, url, usuario=usuario, password=password, token=token, intervalo_min=intervalo,
            enviar_suscripciones=enviar), callback=_fin, pass_context=True)

    def _sincronizar(self):
        notificar(_("Sincronizando…"))

        def _fin(res, err):
            if err or not res or not res.get("ok"):
                notificar(_("La sincronización falló: {0}").format((res or {}).get("error", err or "")))
            else:
                notificar(_("Sincronizado: {0} feeds nuevos, {1} leídos y {2} destacados aplicados aquí, {3} cambios enviados "
                            "al servidor.").format(res.get("feeds_nuevos", 0), res.get("leidos_aplicados", 0),
                                                   res.get("destacados_aplicados", 0), res.get("enviados", 0)))
        task_manager.start_task(lambda ctx: rss_service.sincronizar_cuenta(self.account_id), callback=_fin, pass_context=True)

    def _desactivar(self):
        rss_service.desactivar_sync(self.account_id)
        notificar(_("Sincronización desactivada."))


# --------------------------------------------------------------------------- buscar feeds y podcasts en directorios
TIPOS_BUSQUEDA = (("todo", _("Todo (podcasts, feeds y comunidades)")), ("podcasts", _("Solo podcasts")),
                  ("feeds", _("Solo feeds de noticias y comunidades")))
TIPO_LEGIBLE = {"podcast": _("Podcast"), "feed": _("Feed"), "comunidad": _("Comunidad")}


class DialogoBuscarFeeds(DialogoLista):
    """Busca feeds y podcasts en directorios públicos. La lista se va llenando a medida que responde cada directorio, sin mover
    el foco; Intro enseña la ficha completa y «Suscribirse» añade el elegido a la carpeta indicada."""

    def __init__(self, padre, account_id: int, al_suscribir, consulta: str = ""):
        super().__init__(padre, _("Buscar feeds y podcasts"), _("Resultados"), al_activar=lambda pos: self._detalle())
        from gi.repository import GLib
        self._glib = GLib
        self.account_id, self.al_suscribir = account_id, al_suscribir
        self.resultados, self.lineas, self.suscritos, self.busqueda = [], [], 0, 0
        fila = Gtk.Box(spacing=8)
        self.entrada = Gtk.SearchEntry(hexpand=True, text=consulta)
        self.entrada.update_property([Gtk.AccessibleProperty.LABEL], [_("Qué buscar")])
        self.entrada.connect("activate", lambda *_a: self._buscar())
        self.tipo = Gtk.DropDown.new_from_strings([t for _c, t in TIPOS_BUSQUEDA])
        self.tipo.update_property([Gtk.AccessibleProperty.LABEL], [_("Dónde buscar")])
        etiqueta = Gtk.Label(label=_("_Buscar:"), use_underline=True)
        etiqueta.set_mnemonic_widget(self.entrada)
        for w in (etiqueta, self.entrada, self.tipo):
            fila.append(w)
        self.get_child().insert_child_after(fila, None)
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (account_id,))
        fila2 = Gtk.Box(spacing=8)
        self.carpeta = Gtk.DropDown.new_from_strings([_("Sin carpeta")] + [n for _i, n in self.cats])
        etiqueta2 = Gtk.Label(label=_("_Carpeta donde suscribirse:"), use_underline=True)
        etiqueta2.set_mnemonic_widget(self.carpeta)
        fila2.append(etiqueta2)
        fila2.append(self.carpeta)
        self.get_child().insert_child_after(fila2, self.lista)
        self.boton(_("B_uscar"), self._buscar)
        self.boton(_("_Suscribirse"), self._suscribirse)
        self.boton(_("_Ver la ficha"), self._detalle)
        self.cerrar_boton()
        self.lista.anterior = self.entrada
        self.mostrar_estado(_("Escribe qué buscas y pulsa Intro."))
        if consulta:
            self._buscar()

    def present(self):
        Gtk.Window.present(self)
        self.entrada.grab_focus()

    def _buscar(self):
        from app.services.rss.rss_buscador import NOMBRES_FUENTE
        q = self.entrada.get_text().strip()
        if not q:
            notificar(_("Escribe qué quieres buscar."))
            return
        self.busqueda += 1
        numero = self.busqueda
        self.resultados, self.lineas = [], []
        self.lista.poner([])
        self.mostrar_estado(_("Buscando…"))
        notificar(_("Buscando en los directorios…"))

        def _al_llegar(lote):
            self._glib.idle_add(self._anadir, lote, numero)

        def _fin(res, err):
            if numero != self.busqueda:
                return
            n = len(self.resultados)
            fallos = ", ".join(NOMBRES_FUENTE.get(f, f) for f in ((res or {}).get("errores") or {}))
            texto = _("{0} resultados.").format(n) if n else _("Sin resultados.")
            if fallos:
                texto += " " + _("No respondieron: {0}.").format(fallos)
            self.mostrar_estado(texto)
            notificar(texto)
            if n:
                self.lista.enfocar()
        rss_service.buscar_feeds_async(q, TIPOS_BUSQUEDA[self.tipo.get_selected()][0], al_llegar=_al_llegar, callback=_fin)

    def _anadir(self, lote, numero):
        """Añade un lote al final sin mover el foco."""
        from app.services.rss.rss_buscador import NOMBRES_FUENTE
        if numero != self.busqueda:
            return False
        for r in lote:
            partes = [r["titulo"] or r["url"], TIPO_LEGIBLE.get(r["tipo"], r["tipo"])]
            if r.get("autor"):
                partes.append(r["autor"])
            if r["suscriptores"]:
                partes.append(_("{0} suscriptores").format(r["suscriptores"]))
            partes.append(NOMBRES_FUENTE.get(r["fuente"], r["fuente"]))
            if r["descripcion"]:
                partes.append(" ".join(r["descripcion"].split())[:200])
            self.resultados.append(r)
            self.lineas.append(". ".join(partes))
        p = self.lista.posicion()
        self.lista.modelo.splice(self.lista.modelo.get_n_items(), 0, self.lineas[self.lista.modelo.get_n_items():])
        if p < 0 and self.lineas:
            self.lista.seleccion.set_selected(0)
        self.mostrar_estado(_("{0} resultados, buscando más…").format(len(self.resultados)))
        return False

    def _actual(self):
        p = self.lista.posicion()
        return self.resultados[p] if 0 <= p < len(self.resultados) else None

    def _detalle(self):
        from app.services.rss.rss_buscador import NOMBRES_FUENTE
        from .dialogos_mastodon_extra import DialogoTextoLargo
        r = self._actual()
        if r:
            lineas = [r["titulo"], _("Tipo: {0}").format(TIPO_LEGIBLE.get(r["tipo"], r["tipo"])),
                      _("Directorio: {0}").format(NOMBRES_FUENTE.get(r["fuente"], r["fuente"]))]
            if r.get("autor"):
                lineas.append(_("Autor: {0}").format(r["autor"]))
            lineas += [_("Dirección: {0}").format(r["url"]), "", r["descripcion"] or ""]
            DialogoTextoLargo(self, _("Resultado"), "\n".join(lineas)).present()

    def _suscribirse(self):
        r = self._actual()
        if not r:
            notificar(_("Elige primero un resultado."))
            return
        sel = self.carpeta.get_selected()
        categoria = None if sel <= 0 else self.cats[sel - 1][0]
        pos = self.lista.posicion()
        notificar(_("Suscribiendo a {0}…").format(r["titulo"]))

        def _fin(res, err):
            if err:
                notificar(_("No se pudo suscribir: {0}").format(err))
                return
            self.suscritos += 1
            notificar(_("Suscrito: {0}").format(res))
            if 0 <= pos < len(self.lineas):
                self.lineas[pos] = _("{0} (suscrito)").format(self.lineas[pos])
                self.lista.modelo.splice(pos, 1, [self.lineas[pos]])
                self.lista.seleccion.set_selected(pos)
            self.al_suscribir()
        task_manager.start_task(lambda ctx: rss_service.añadir_feed(account_id=self.account_id, url=r["url"],
                                                                   category_id=categoria, ctx=ctx),
                                callback=_fin, pass_context=True)


# --------------------------------------------------------------------------- búsquedas guardadas y alertas
class DialogoBusquedaGuardada(DialogoBase):
    def __init__(self, padre, titulo: str, nombre: str, consulta: str, al_guardar):
        super().__init__(padre, titulo, _("_Guardar"))
        self.al_guardar = al_guardar
        self.consulta = self.campo(_("_Texto de la búsqueda:"), Gtk.Entry(text=consulta, width_chars=50, activates_default=True))
        self.nombre = self.campo(_("_Nombre:"), Gtk.Entry(text=nombre, width_chars=50, activates_default=True))
        self.set_focus(self.consulta)

    def aceptar(self):
        consulta, nombre = self.consulta.get_text().strip(), self.nombre.get_text().strip()
        if not consulta:
            notificar(_("Escribe el texto de la búsqueda."))
            return
        self.close()
        self.al_guardar(nombre or consulta[:40], consulta)


class DialogoBusquedasGuardadas(DialogoLista):
    """Búsquedas guardadas de RSS: se abren como un buffer del árbol y se actualizan con cada sondeo."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Búsquedas guardadas de RSS"), _("Lista de búsquedas guardadas"),
                         al_activar=lambda pos: self._buscar())
        self.account_id = account_id
        self.items = []
        for et, fn in ((_("_Nueva…"), self._nueva), (_("_Buscar ahora"), self._buscar), (_("_Editar…"), self._editar),
                       (_("_Borrar"), self._borrar)):
            self.boton(et, fn)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.items = rss_service.obtener_busquedas_guardadas(self.account_id)
        self.lista.poner([f"{b['name']}: {b['query']}" for b in self.items])
        if self.items:
            self.lista.seleccion.set_selected(max(0, min(pos, len(self.items) - 1)))
        self.mostrar_estado(_("{0} búsquedas guardadas").format(len(self.items)) if self.items
                            else _("No hay búsquedas guardadas."))

    def _tras_cambio(self):
        self.padre.cargar_arbol()

    def _nueva(self):
        def _guardar(nombre, consulta):
            rss_service.crear_busqueda_guardada(self.account_id, nombre, consulta)
            notificar(_("Búsqueda guardada."))
            self._cargar(len(self.items))
            self._tras_cambio()
        DialogoBusquedaGuardada(self, _("Nueva búsqueda guardada"), "", "", _guardar).present()

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _buscar(self):
        b = self._actual()
        if b:
            self.close()
            self.padre.herr_buscar_global(b["query"], solo_rss=True)

    def _editar(self):
        b = self._actual()
        if not b:
            return

        def _guardar(nombre, consulta):
            rss_service.editar_busqueda_guardada(b["id"], nombre, consulta)
            notificar(_("Búsqueda actualizada."))
            self._cargar(self.lista.posicion())
            self._tras_cambio()
        DialogoBusquedaGuardada(self, _("Editar búsqueda guardada"), b["name"], b["query"], _guardar).present()

    def _borrar(self):
        b = self._actual()
        if not b:
            return

        def _hacer():
            rss_service.borrar_busqueda_guardada(b["id"])
            notificar(_("Búsqueda borrada."))
            self._cargar(self.lista.posicion())
            self._tras_cambio()
        confirmar_async(self, _("¿Borrar la búsqueda guardada «{0}»?").format(b["name"]), _hacer, etiqueta_si=_("Borrar"))


class DialogoAlerta(DialogoBase):
    def __init__(self, padre, titulo: str, palabra: str, es_regex: bool, al_guardar):
        super().__init__(padre, titulo, _("_Guardar"))
        self.al_guardar = al_guardar
        self.palabra = self.campo(_("_Palabra o frase a vigilar:"), Gtk.Entry(text=palabra, width_chars=40, activates_default=True))
        self.regex = self.campo("", Gtk.CheckButton(label=_("Es una expresión _regular"), use_underline=True, active=es_regex))
        self.set_focus(self.palabra)

    def aceptar(self):
        palabra = self.palabra.get_text().strip()
        if not palabra:
            return
        self.close()
        self.al_guardar(palabra, self.regex.get_active())


class DialogoAlertas(DialogoLista):
    """Alertas por palabra clave: avisan cuando llega un artículo que las contiene."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Alertas por palabra clave"), _("Lista de alertas"), al_activar=lambda pos: self._editar())
        self.account_id = account_id
        self.items = []
        for et, fn in ((_("_Nueva…"), self._nueva), (_("_Editar…"), self._editar), (_("Activar o _desactivar"), self._alternar),
                       (_("_Borrar"), self._borrar)):
            self.boton(et, fn)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.items = rss_service.obtener_alertas(self.account_id)
        self.lista.poner([(_("{0} (regex)") if a["is_regex"] else "{0}").format(a["keyword"])
                          + ("" if a["active"] else _(" [desactivada]")) for a in self.items])
        if self.items:
            self.lista.seleccion.set_selected(max(0, min(pos, len(self.items) - 1)))
        self.mostrar_estado(_("Te avisan cuando llega un artículo que contenga la palabra. {0} alertas.").format(len(self.items)))

    def _actual(self):
        p = self.lista.posicion()
        return self.items[p] if 0 <= p < len(self.items) else None

    def _nueva(self):
        def _guardar(palabra, regex):
            rss_service.crear_alerta(self.account_id, palabra, regex)
            notificar(_("Alerta creada."))
            self._cargar(len(self.items))
        DialogoAlerta(self, _("Nueva alerta"), "", False, _guardar).present()

    def _editar(self):
        a = self._actual()
        if not a:
            return

        def _guardar(palabra, regex):
            rss_service.editar_alerta(a["id"], palabra, regex)
            notificar(_("Alerta actualizada."))
            self._cargar(self.lista.posicion())
        DialogoAlerta(self, _("Editar alerta"), a["keyword"], a["is_regex"], _guardar).present()

    def _alternar(self):
        a = self._actual()
        if a:
            rss_service.activar_alerta(a["id"], not a["active"])
            notificar(_("Alerta desactivada.") if a["active"] else _("Alerta activada."))
            self._cargar(self.lista.posicion())

    def _borrar(self):
        a = self._actual()
        if not a:
            return

        def _hacer():
            rss_service.borrar_alerta(a["id"])
            notificar(_("Alerta borrada."))
            self._cargar(self.lista.posicion())
        confirmar_async(self, _("¿Borrar la alerta «{0}»?").format(a["keyword"]), _hacer, etiqueta_si=_("Borrar"))


# --------------------------------------------------------------------------- elegir con casillas (importar y exportar)
class DialogoCasillas(DialogoBase):
    """Lista de elementos con casillas (vista previa de una importación, artículos a exportar...). Entrega los índices marcados."""

    def __init__(self, padre, titulo: str, encabezado: str, etiquetas, marcados, al_aceptar, aceptar: str = None):
        super().__init__(padre, titulo, aceptar or _("_Aceptar"))
        self.al_aceptar = al_aceptar
        self.rejilla.attach(Gtk.Label(label=encabezado, wrap=True, xalign=0, width_chars=60, max_width_chars=70), 0, 0, 2, 1)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.checks = []
        for i, et in enumerate(etiquetas):
            cb = Gtk.CheckButton(label=et, active=i in marcados)
            caja.append(cb)
            self.checks.append(cb)
        self._fila = 1
        self.campo(_("_Elementos (Espacio marca o desmarca):"),
                   Gtk.ScrolledWindow(child=caja, min_content_height=320, min_content_width=520, has_frame=True))
        b = Gtk.Button(label=_("_Marcar o desmarcar todos"), use_underline=True)
        b.connect("clicked", lambda *_a: self._todos())
        self.rejilla.attach(b, 1, self._fila, 1, 1)
        self._fila += 1
        if self.checks:
            self.set_focus(self.checks[0])
        desplazable(self, 560, 640)

    def _todos(self):
        marcar = not all(c.get_active() for c in self.checks)
        for c in self.checks:
            c.set_active(marcar)

    def aceptar(self):
        elegidos = [i for i, c in enumerate(self.checks) if c.get_active()]
        self.close()
        self.al_aceptar(elegidos)


class DialogoTextoValor(DialogoBase):
    """Pide una cadena con un valor de partida (renombrar un feed, por ejemplo)."""

    def __init__(self, padre, titulo: str, etiqueta: str, valor: str, al_aceptar, aceptar: str = None):
        super().__init__(padre, titulo, aceptar)
        self.al_aceptar = al_aceptar
        self.entrada = self.campo(etiqueta, Gtk.Entry(text=valor, width_chars=50, activates_default=True))
        self.set_focus(self.entrada)

    def aceptar(self):
        texto = self.entrada.get_text().strip()
        if texto:
            self.close()
            self.al_aceptar(texto)
