# -*- coding: utf-8 -*-
"""
Borrado de datos controlado y personalizado para mantener la base de datos a raya.

Permite eliminar, por servicio y por antigüedad, las publicaciones de Mastodon,
los artículos de RSS y los mensajes cacheados de Telegram, conservando opcional-
mente los elementos marcados (favoritos / destacados / leer más tarde).
"""

import logging
from datetime import datetime, timedelta

from app.core.database import db

log = logging.getLogger(__name__)


def _limite_iso(dias):
    return (datetime.now() - timedelta(days=max(0, int(dias)))).isoformat()


def contar():
    """Devuelve un resumen del volumen de datos y el tamaño del archivo."""
    res = {"mastodon": 0, "bluesky": 0, "rss": 0, "telegram": 0, "x": 0, "irc": 0,
           "matrix": 0, "xmpp": 0, "reddit": 0, "wsocial": 0, "tamano_mb": 0.0}
    try:
        for clave, tabla in (("mastodon", "mastodon_statuses"), ("bluesky", "bluesky_posts"),
                             ("rss", "rss_articles"), ("telegram", "telegram_messages"),
                             ("x", "x_tweets"), ("irc", "irc_messages"),
                             ("matrix", "matrix_mensajes"), ("xmpp", "xmpp_mensajes"),
                             ("reddit", "reddit_posts"), ("wsocial", "wsocial_posts")):
            try:
                fila = db.query_one(f"SELECT COUNT(*) FROM {tabla}")
                res[clave] = fila[0] if fila else 0
            except Exception:
                res[clave] = 0
    except Exception as e:
        log.debug(f"Error al contar datos: {e}")
    try:
        from app.core.paths import get_data_path
        p = get_data_path("database/social_rss.db")
        if p.exists():
            res["tamano_mb"] = round(p.stat().st_size / (1024 * 1024), 1)
    except Exception:
        pass
    return res


def borrar_mastodon(dias=0, conservar_marcados=True):
    """Borra publicaciones de Mastodon anteriores a 'dias' (0 = todas).
    Si conservar_marcados, no borra favoritos ni guardados (bookmarks)."""
    cond = []
    params = []
    if dias and int(dias) > 0:
        cond.append("created_at < ?")
        params.append(_limite_iso(dias))
    if conservar_marcados:
        cond.append("favourited = 0 AND bookmarked = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM mastodon_statuses")
        # IDs a borrar (para limpiar también relaciones y FTS).
        ids = [r[0] for r in db.query(f"SELECT id FROM mastodon_statuses{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            # Los 3 DELETE de cada lote en UNA transacción (un commit por lote, no tres).
            with db.transaccion():
                db.execute(f"DELETE FROM mastodon_statuses WHERE id IN ({ph})", tuple(lote))
                try:
                    db.execute(f"DELETE FROM mastodon_timeline_statuses WHERE status_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
                try:
                    db.execute(f"DELETE FROM mastodon_statuses_fts WHERE status_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
        despues = db.query_one("SELECT COUNT(*) FROM mastodon_statuses")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de Mastodon: {e}")
        return 0


def borrar_wsocial(dias=0, conservar_marcados=True):
    """Borra publicaciones de W Social anteriores a 'dias' (0 = todas). Con
    `conservar_marcados` respeta las guardadas en marcadores."""
    cond, params = [], []
    if dias and int(dias) > 0:
        cond.append("indexed_at < ?")
        params.append(_limite_iso(dias))
    if conservar_marcados:
        cond.append("bookmarked = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM wsocial_posts")
        ids = [r[0] for r in db.query(f"SELECT id FROM wsocial_posts{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM wsocial_posts WHERE id IN ({ph})", tuple(lote))
                for tabla, columna in (("wsocial_timeline_posts", "post_db_id"),
                                       ("wsocial_posts_fts", "post_db_id")):
                    try:
                        db.execute(f"DELETE FROM {tabla} WHERE {columna} IN ({ph})", tuple(lote))
                    except Exception:
                        pass
        despues = db.query_one("SELECT COUNT(*) FROM wsocial_posts")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de W Social: {e}")
        return 0


def borrar_bluesky(dias=0, conservar_marcados=True):
    """Borra publicaciones de Bluesky anteriores a 'dias' (0 = todas).
    Si conservar_marcados, no borra los que tengan marcador (bookmarked)."""
    cond = []
    params = []
    if dias and int(dias) > 0:
        cond.append("indexed_at < ?")
        params.append(_limite_iso(dias))
    if conservar_marcados:
        cond.append("bookmarked = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM bluesky_posts")
        ids = [r[0] for r in db.query(f"SELECT id FROM bluesky_posts{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM bluesky_posts WHERE id IN ({ph})", tuple(lote))
                try:
                    db.execute(f"DELETE FROM bluesky_timeline_posts WHERE post_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
                try:
                    db.execute(f"DELETE FROM bluesky_posts_fts WHERE post_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
        despues = db.query_one("SELECT COUNT(*) FROM bluesky_posts")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de Bluesky: {e}")
        return 0


def borrar_reddit(dias=0, conservar_marcados=True):
    """Borra publicaciones de Reddit anteriores a 'dias' (0 = todas). Si conservar_marcados,
    no borra las guardadas. Limpia también sus relaciones de buffer (CASCADE)."""
    cond = []
    params = []
    if dias and int(dias) > 0:
        import time as _t
        cond.append("created_utc < ?")
        params.append(_t.time() - int(dias) * 86400)
    if conservar_marcados:
        cond.append("saved = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM reddit_posts")
        ids = [r[0] for r in db.query(f"SELECT id FROM reddit_posts{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                # La relación reddit_timeline_posts se borra en cascada al borrar el post.
                db.execute(f"DELETE FROM reddit_posts WHERE id IN ({ph})", tuple(lote))
        despues = db.query_one("SELECT COUNT(*) FROM reddit_posts")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de Reddit: {e}")
        return 0


def purgar_reddit_borrados():
    """Elimina de la caché local el contenido de Reddit borrado o eliminado (cuerpo [deleted] o
    [removed]), como exige la política de datos de Reddit. Devuelve cuántas filas se purgaron."""
    total = 0
    try:
        for tabla, campo in (("reddit_posts", "selftext"), ("reddit_comments", "body")):
            try:
                r = db.execute(
                    f"DELETE FROM {tabla} WHERE LOWER(TRIM(COALESCE({campo},''))) IN ('[deleted]','[removed]')")
                total += r or 0
            except Exception:
                pass
    except Exception as e:
        log.debug(f"Error al purgar contenido borrado de Reddit: {e}")
    return total


def borrar_rss(dias=0, conservar_marcados=True):
    """Borra artículos RSS anteriores a 'dias' (0 = todos). Si conservar_marcados,
    no borra destacados ni los de leer-más-tarde."""
    cond = []
    params = []
    if dias and int(dias) > 0:
        cond.append("published_at < ?")
        params.append(_limite_iso(dias))
    if conservar_marcados:
        cond.append("favorite = 0 AND COALESCE(read_later, 0) = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM rss_articles")
        ids = [r[0] for r in db.query(f"SELECT id FROM rss_articles{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM rss_articles WHERE id IN ({ph})", tuple(lote))
                try:
                    db.execute(f"DELETE FROM rss_articles_fts WHERE article_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
        despues = db.query_one("SELECT COUNT(*) FROM rss_articles")
        # Corte de importación: si el usuario activó "descargar solo artículos nuevos a partir de la
        # limpieza" y esto es una limpieza COMPLETA (no por antigüedad), se marca en cada feed la
        # fecha de corte = ahora. Así, al volver a descargar, los feeds NO re-importan todo su
        # histórico: solo lo publicado desde este momento. Los feeds que se añadan DESPUÉS nacen sin
        # corte, así que un feed nuevo sí trae sus artículos como siempre.
        try:
            from app.core.config_manager import config
            if (not dias or int(dias) <= 0) and config.get("rss", "corte_al_limpiar", False):
                db.execute("UPDATE rss_feeds SET import_cutoff = ?", (datetime.now().isoformat(),))
                log.info("RSS: corte de importación fijado al limpiar (solo artículos nuevos desde ahora).")
        except Exception as e:
            log.error(f"RSS: no se pudo fijar el corte de importación: {e}")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de RSS: {e}")
        return 0


def borrar_matrix(dias=0):
    """Borra mensajes de Matrix anteriores a 'dias' (0 = todos), incluido su índice FTS."""
    where = ""
    params = ()
    if dias and int(dias) > 0:
        where = " WHERE date < ?"
        params = (_limite_iso(dias),)
    try:
        antes = db.query_one("SELECT COUNT(*) FROM matrix_mensajes")
        ids = [r[0] for r in db.query(f"SELECT id FROM matrix_mensajes{where}", params)]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM matrix_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                db.execute(f"DELETE FROM matrix_mensajes WHERE id IN ({ph})", tuple(lote))
        despues = db.query_one("SELECT COUNT(*) FROM matrix_mensajes")
        # Relaciones (reacciones/votos) que ya no apuntan a ningún mensaje: se quedarían huérfanas.
        try:
            db.execute("DELETE FROM matrix_relations WHERE target_event_id NOT IN "
                       "(SELECT event_id FROM matrix_mensajes)")
        except Exception:
            pass
        # Caché de adjuntos DESCARGADOS Y DESCIFRADOS (cache/matrix): aunque la sala sea cifrada, el
        # archivo queda en claro en disco. En un borrado total se elimina toda; por días, los archivos
        # más antiguos que 'dias' por fecha de modificación (MX-002: minimizar datos en reposo).
        try:
            from app.core.paths import get_data_path
            import time as _t
            carpeta = get_data_path("cache/matrix")
            if carpeta.exists():
                limite_ts = 0 if not (dias and int(dias) > 0) else (_t.time() - int(dias) * 86400)
                for f in carpeta.iterdir():
                    try:
                        if f.is_file() and (limite_ts == 0 or f.stat().st_mtime < limite_ts):
                            f.unlink()
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"Matrix: no se pudo limpiar la caché de adjuntos: {e}")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de Matrix: {e}")
        return 0


def borrar_xmpp(dias=0):
    """Borra mensajes de XMPP anteriores a 'dias' (0 = todos), incluido su índice FTS.
    Si dias == 0 (borrar TODO), limpia además la lista local de chats/contactos/grupos (xmpp_chats):
    esa lista es solo una CACHÉ del roster del servidor y se resincroniza sola al reconectar, así que
    'empezar de cero' quita también los contactos/grupos de la sesión anterior."""
    where = ""
    params = ()
    todo = not (dias and int(dias) > 0)
    if not todo:
        where = " WHERE date < ?"
        params = (_limite_iso(dias),)
    try:
        antes = db.query_one("SELECT COUNT(*) FROM xmpp_mensajes")
        ids = [r[0] for r in db.query(f"SELECT id FROM xmpp_mensajes{where}", params)]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM xmpp_mensajes_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                db.execute(f"DELETE FROM xmpp_mensajes WHERE id IN ({ph})", tuple(lote))
        despues = db.query_one("SELECT COUNT(*) FROM xmpp_mensajes")
        borrados = (antes[0] - despues[0]) if antes and despues else 0
        if todo:
            # Limpiar la caché de chats (contactos/grupos/solicitudes) y cualquier mensaje/FTS residual.
            try:
                with db.transaccion():
                    db.execute("DELETE FROM xmpp_mensajes")
                    db.execute("DELETE FROM xmpp_mensajes_fts")
                    db.execute("DELETE FROM xmpp_chats")
            except Exception as e:
                log.error(f"Error limpiando la caché de chats de XMPP: {e}")
        # Caché de adjuntos DESCARGADOS (cache/xmpp): quedan en claro en disco aunque el chat sea
        # OMEMO. En borrado total se elimina toda; por días, los más antiguos que 'dias' (XMPP-009).
        try:
            from app.core.paths import get_data_path
            import time as _t
            carpeta = get_data_path("cache/xmpp")
            if carpeta.exists():
                limite_ts = 0 if todo else (_t.time() - int(dias) * 86400)
                for f in carpeta.iterdir():
                    try:
                        if f.is_file() and (limite_ts == 0 or f.stat().st_mtime < limite_ts):
                            f.unlink()
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"XMPP: no se pudo limpiar la caché de adjuntos: {e}")
        return borrados
    except Exception as e:
        log.error(f"Error al borrar datos de XMPP: {e}")
        return 0


def borrar_irc(dias=0):
    """Borra mensajes de IRC anteriores a 'dias' (0 = todos), incluido su índice de búsqueda."""
    where = ""
    params = ()
    if dias and int(dias) > 0:
        where = " WHERE date < ?"
        params = (_limite_iso(dias),)
    try:
        antes = db.query_one("SELECT COUNT(*) FROM irc_messages")
        ids = [r[0] for r in db.query(f"SELECT id FROM irc_messages{where}", params)]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM irc_messages_fts WHERE msg_db_id IN ({ph})", tuple(lote))
                db.execute(f"DELETE FROM irc_messages WHERE id IN ({ph})", tuple(lote))
        despues = db.query_one("SELECT COUNT(*) FROM irc_messages")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de IRC: {e}")
        return 0


def borrar_telegram(dias=0):
    """Borra mensajes de Telegram cacheados anteriores a 'dias' (0 = todos)."""
    where = ""
    params = ()
    if dias and int(dias) > 0:
        where = " WHERE date < ?"
        params = (_limite_iso(dias),)
    try:
        antes = db.query_one("SELECT COUNT(*) FROM telegram_messages")
        db.execute(f"DELETE FROM telegram_messages{where}", params)
        despues = db.query_one("SELECT COUNT(*) FROM telegram_messages")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de Telegram: {e}")
        return 0


def borrar_x(dias=0, conservar_marcados=True):
    """Borra publicaciones cacheadas de X anteriores a 'dias' (0 = todas).
    Si conservar_marcados, no borra las que tengan marcador (bookmarked)."""
    cond = []
    params = []
    if dias and int(dias) > 0:
        cond.append("created_at < ?")
        params.append(_limite_iso(dias))
    if conservar_marcados:
        cond.append("COALESCE(bookmarked, 0) = 0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    try:
        antes = db.query_one("SELECT COUNT(*) FROM x_tweets")
        ids = [r[0] for r in db.query(f"SELECT id FROM x_tweets{where}", tuple(params))]
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            with db.transaccion():
                db.execute(f"DELETE FROM x_tweets WHERE id IN ({ph})", tuple(lote))
                try:
                    db.execute(f"DELETE FROM x_timeline_tweets WHERE tweet_db_id IN ({ph})", tuple(lote))
                except Exception:
                    pass
        despues = db.query_one("SELECT COUNT(*) FROM x_tweets")
        return (antes[0] - despues[0]) if antes and despues else 0
    except Exception as e:
        log.error(f"Error al borrar datos de X: {e}")
        return 0


def limpiar_huerfanos_fts() -> int:
    """Borra filas de las tablas FTS cuyo registro principal ya no existe (huérfanos).

    Aparecen cuando un post/artículo se borra por una vía que no limpió su índice FTS. Ocupan
    espacio y pueden devolver candidatos muertos en una búsqueda antes de filtrarse con el JOIN.
    Devuelve el total de filas FTS borradas. Pensado para el mantenimiento periódico."""
    pares = [
        ("mastodon_statuses_fts", "status_db_id", "mastodon_statuses", "id"),
        ("bluesky_posts_fts", "post_db_id", "bluesky_posts", "id"),
        ("rss_articles_fts", "article_db_id", "rss_articles", "id"),
    ]
    total = 0
    for fts, col, tabla, pk in pares:
        try:
            antes = db.query_one(f"SELECT COUNT(*) FROM {fts}")
            with db.transaccion():
                db.execute(f"DELETE FROM {fts} WHERE {col} NOT IN (SELECT {pk} FROM {tabla})")
            despues = db.query_one(f"SELECT COUNT(*) FROM {fts}")
            if antes and despues:
                total += max(0, antes[0] - despues[0])
        except Exception as e:
            log.debug(f"No se pudieron limpiar huérfanos de {fts}: {e}")
    if total:
        log.info(f"Mantenimiento FTS: {total} filas huérfanas eliminadas.")
    return total


def optimizar():
    """Compacta la base de datos (VACUUM + truncado del WAL) tras borrar. Usa el método robusto del
    gestor (cierra las conexiones de lectura para que el fichero encoja de verdad)."""
    try:
        return db.compactar()
    except Exception as e:
        log.error(f"Error al optimizar la base de datos: {e}")
        return False
