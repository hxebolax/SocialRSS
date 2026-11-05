# -*- coding: utf-8 -*-
"""Acceso a datos RSS para la interfaz GTK (consultas ligeras; el trabajo pesado lo hace el servicio)."""

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.core.config_manager import config
from app.core.database import db
from app.services.rss.rss_buffers import where_buffer, consulta_fts

log = logging.getLogger(__name__)


@dataclass
class Nodo:
    """Elemento del árbol de navegación."""
    titulo: str
    tipo: str                       # all, unread, favorites, read_later, today, category, feed, trash
    category_id: Optional[int] = None
    feed_id: Optional[int] = None
    no_leidos: int = 0
    hijos: List["Nodo"] = field(default_factory=list)
    error: str = ""
    servicio: str = "rss"            # rss, mastodon
    account_id: Optional[int] = None  # cuenta de la red social (RSS usa la suya en la ventana)
    timeline: str = ""               # buffer de Mastodon: home, notifications, thread:<id>...
    cerrable: bool = False           # buffer abierto por el usuario (hilo, usuario...)

    @property
    def etiqueta(self) -> str:
        return f"{self.titulo} ({self.no_leidos})" if self.no_leidos else self.titulo

    @property
    def listable(self) -> bool:
        """Los nodos de servicio, de cuenta y de grupo solo agrupan; el resto muestra una lista."""
        return self.tipo not in ("servicio", "cuenta", "grupo")


@dataclass
class Fila:
    """Artículo para la lista."""
    id: str
    feed_id: int
    feed_titulo: str
    titulo: str
    autor: str
    fecha: str
    leido: bool
    favorito: bool
    mas_tarde: bool
    audio: bool
    enlace: str
    servicio: str = "rss"
    extra: dict = field(default_factory=dict)   # datos propios de la red (Mastodon)


def cuenta_rss(crear: bool = True) -> Optional[int]:
    """Id de la primera cuenta RSS activa; la crea si no hay ninguna."""
    fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' AND active = 1 ORDER BY position, id")
    if fila:
        return fila[0]
    if not crear:
        return None
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    pos = db.query_one("SELECT COALESCE(MAX(position), 0) + 1 FROM accounts")[0]
    db.execute("INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'rss', 1, ?)",
               (_("RSS y podcasts"), pos))
    fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'rss' ORDER BY id DESC LIMIT 1")
    return fila[0] if fila else None


def _no_leidos(account_id: int):
    por_feed = {r[0]: r[1] for r in db.query(
        "SELECT feed_id, COUNT(*) FROM rss_articles WHERE account_id = ? AND read = 0 "
        "AND COALESCE(deleted, 0) = 0 GROUP BY feed_id", (account_id,))}
    return por_feed


def _contar(account_id: int, tipo: str) -> int:
    where, params = where_buffer(tipo)
    fila = db.query_one(
        "SELECT COUNT(*) FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
        "WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0 AND a.read = 0" + where,
        (account_id, *params))
    return fila[0] if fila else 0


# En la configuración (compartida con la versión de Windows) algunos buffers se llaman distinto que aquí.
CLAVE_OCULTOS = {("bluesky", "dms"): "convos", ("wsocial", "dms"): "convos", ("telegram", "chats"): "all"}


def buffer_oculto(servicio: str, clave: str) -> bool:
    """¿Ha pedido el usuario no ver este buffer fijo en el árbol? (Herramientas → Mostrar u ocultar buffers)"""
    return CLAVE_OCULTOS.get((servicio, clave), clave) in set(config.get(servicio, "hidden_buffers", []) or [])


def _nodos_guardados(account_id: int) -> List[Nodo]:
    """Carpetas inteligentes, búsquedas guardadas y etiquetas de la cuenta, que se abren como un buffer más."""
    nodos = []
    try:
        for sf_id, nombre in db.query("SELECT id, name FROM rss_smart_folders WHERE account_id = ? ORDER BY position, name",
                                      (account_id,)):
            nodos.append(Nodo(nombre, f"smart:{sf_id}"))
    except Exception as e:
        log.debug("No se pudieron cargar las carpetas inteligentes: %s", e)
    if config.get("rss", "saved_searches_in_tree", True):
        from app.services.rss.rss_buffers import busquedas_en_arbol
        for bs_id, nombre in busquedas_en_arbol(account_id):
            nodos.append(Nodo(_("Búsqueda: {0}").format(nombre), f"search:{bs_id}"))
    try:
        for tg_id, nombre in db.query(
                "SELECT t.id, t.name FROM rss_tags t WHERE t.account_id = ? AND (EXISTS (SELECT 1 FROM rss_feed_tags ft "
                "WHERE ft.tag_id = t.id) OR EXISTS (SELECT 1 FROM rss_article_tags at WHERE at.tag_id = t.id)) "
                "ORDER BY t.position, t.name", (account_id,)):
            nodos.append(Nodo("#" + nombre, f"tag:{tg_id}"))
    except Exception as e:
        log.debug("No se pudieron cargar las etiquetas RSS: %s", e)
    return nodos


def construir_arbol(account_id: int) -> List[Nodo]:
    """Buffers fijos, categorías (con sus subcategorías y feeds) y feeds sin categoría."""
    from app.services.rss.rss_service import rss_service
    por_feed = _no_leidos(account_id)
    feeds = rss_service.obtener_feeds(account_id)
    categorias = rss_service.obtener_categorias(account_id)

    def _nodo_feed(f):
        return Nodo(f.title or f.url, "feed", feed_id=f.id, no_leidos=por_feed.get(f.id, 0),
                    error=f.last_error or "")

    por_cat = {}
    for f in feeds:
        por_cat.setdefault(f.category_id, []).append(_nodo_feed(f))

    nodos_cat = {}
    for c in categorias:
        nodos_cat[c["id"]] = Nodo(c["name"], "category", category_id=c["id"])
    raiz_cat = []
    for c in categorias:
        nodo = nodos_cat[c["id"]]
        padre = c.get("parent_id")
        (nodos_cat[padre].hijos if padre in nodos_cat else raiz_cat).append(nodo)

    def _cerrar(nodo):
        for h in nodo.hijos:
            _cerrar(h)
        nodo.hijos = nodo.hijos + por_cat.get(nodo.category_id, [])
        nodo.no_leidos = sum(h.no_leidos for h in nodo.hijos)
    for n in raiz_cat:
        _cerrar(n)

    total = sum(por_feed.values())
    arbol = [
        Nodo(_("Todos los artículos"), "all", no_leidos=total),
        Nodo(_("No leídos"), "unread", no_leidos=total),
        Nodo(_("Favoritos"), "favorites"),
        Nodo(_("Leer más tarde"), "read_later"),
        Nodo(_("Hoy"), "today"),
    ]
    arbol = [n for n in arbol if not buffer_oculto("rss", n.tipo)]
    arbol.extend(_nodos_guardados(account_id))
    arbol.extend(raiz_cat)
    arbol.extend(por_cat.get(None, []))
    return arbol


def listar(account_id: int, nodo: Nodo, limite: int = 300, busqueda: str = "") -> List[Fila]:
    """Artículos del buffer `nodo` (o los que casan con `busqueda` en texto completo)."""
    where, params = where_buffer(nodo.tipo, nodo.category_id, nodo.feed_id)
    sql = ("SELECT a.id, a.feed_id, f.title, a.title, COALESCE(a.author, ''), "
           "COALESCE(a.published_at, ''), a.read, a.favorite, COALESCE(a.read_later, 0), "
           "COALESCE(a.has_media, 0), COALESCE(a.link, '') "
           "FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
           "WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0")
    args = [account_id]
    if busqueda.strip():
        sql += " AND a.id IN (SELECT article_db_id FROM rss_articles_fts WHERE rss_articles_fts MATCH ?)"
        args.append(consulta_fts(busqueda))
    sql += where + " ORDER BY a.published_at DESC LIMIT ?"
    args.extend(params)
    args.append(limite)
    try:
        filas = db.query(sql, tuple(args))
    except Exception as e:
        log.error(f"Error al listar artículos: {e}")
        return []
    from .lectura import con_plantilla
    # En el buffer de un solo feed todos los artículos son del mismo: leer su nombre con cada título es ruido.
    # En los buffers que mezclan feeds (Todos, No leídos, categorías...) sí hace falta para saber de dónde viene.
    un_feed = nodo.tipo == "feed"
    return [con_plantilla(Fila(r[0], r[1], r[2] or "", r[3] or _("(sin título)"), r[4], r[5], bool(r[6]),
                               bool(r[7]), bool(r[8]), bool(r[9]), r[10]),
                          "rss_template", {"feed": "" if un_feed else r[2] or "", "title": r[3] or _("(sin título)"), "author": r[4] or ""})
            for r in filas]


def fecha_legible(iso: str) -> str:
    """Fecha corta y hablable ('hace 3 h', 'ayer', '12/03/2026')."""
    if not iso:
        return ""
    from datetime import datetime
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone().replace(tzinfo=None)
    except ValueError:
        return iso[:16]
    delta = datetime.now() - d
    seg = int(delta.total_seconds())
    if seg < 0:
        return d.strftime("%d/%m/%Y %H:%M")
    if seg < 60:
        return _("hace un momento")
    if seg < 3600:
        return _("hace {n} min").format(n=seg // 60)
    if seg < 86400:
        return _("hace {n} h").format(n=seg // 3600)
    if seg < 172800:
        return _("ayer")
    return d.strftime("%d/%m/%Y")
