# -*- coding: utf-8 -*-
"""Artículos, papelera, búsquedas, alertas, audio, FTS y OPML.

Mixin de RSSService (parte de la refactorización modular).
"""
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

from app.core.database import db
from app.core.task_manager import task_manager
from app.core.models import RSSArticle
from .opml import importar_opml, exportar_opml


log = logging.getLogger(__name__)

class _ArticulosMixin:
    def obtener_articulos(
        self,
        account_id: int,
        category_id: Optional[int] = None,
        feed_id: Optional[int] = None,
        only_unread: bool = False,
        only_favorites: bool = False,
        only_read_later: bool = False,
        only_today: bool = False,
        limit: int = 200
    ) -> List[RSSArticle]:
        """Consulta los artículos cacheados aplicando filtros."""
        sql = """
        SELECT a.id, a.feed_id, a.guid, a.link, a.title, a.content, a.author, a.published_at, a.read, a.favorite, a.has_media, a.media_url, a.media_type, a.translated_content, a.ai_summary, COALESCE(a.read_later, 0)
        FROM rss_articles a
        JOIN rss_feeds f ON a.feed_id = f.id
        WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0
        """
        params = [account_id]

        if feed_id is not None:
            sql += " AND a.feed_id = ?"
            params.append(feed_id)
        elif category_id is not None:
            sql += " AND f.category_id = ?"
            params.append(category_id)

        if only_unread:
            sql += " AND a.read = 0"

        if only_favorites:
            sql += " AND a.favorite = 1"

        if only_read_later:
            sql += " AND COALESCE(a.read_later, 0) = 1"

        if only_today:
            # Artículos publicados hoy (fecha local).
            sql += " AND date(a.published_at) = date('now', 'localtime')"

        # Ocultar artículos ANCLADOS con fecha FUTURA: algunos feeds fijan entradas con una fecha
        # por delante para que se queden siempre arriba. Si el usuario lo activa, se excluyen, de
        # modo que el primero de la lista vuelve a ser el último artículo que llegó de verdad.
        try:
            from app.core.config_manager import config
            if config.get("rss", "hide_future_articles", False):
                sql += " AND datetime(a.published_at) <= datetime('now', 'localtime')"
        except Exception:
            pass

        sql += " ORDER BY a.published_at DESC LIMIT ?"
        params.append(limit)

        articulos = []
        try:
            rows = db.query(sql, tuple(params))
            for r in rows:
                articulos.append(RSSArticle(
                    id=r[0], feed_id=r[1], guid=r[2], link=r[3], title=r[4],
                    content=r[5], author=r[6], published_at=r[7], read=bool(r[8]),
                    favorite=bool(r[9]), has_media=bool(r[10]), media_url=r[11],
                    media_type=r[12], translated_content=r[13], ai_summary=r[14],
                    read_later=bool(r[15])
                ))
        except Exception as e:
            log.error(f"Error al obtener artículos RSS: {e}")
        return articulos

    def marcar_leido(self, article_id: str, read: bool = True):
        """Establece el estado de lectura de un artículo."""
        sql = "UPDATE rss_articles SET read = ? WHERE id = ?"
        try:
            db.execute(sql, (1 if read else 0, article_id))
        except Exception as e:
            log.error(f"Error al marcar leído el artículo {article_id}: {e}")

    def marcar_favorito(self, article_id: str, favorite: bool = True):
        """Establece el estado favorito (destacado) de un artículo."""
        sql = "UPDATE rss_articles SET favorite = ? WHERE id = ?"
        try:
            db.execute(sql, (1 if favorite else 0, article_id))
        except Exception as e:
            log.error(f"Error al marcar favorito el artículo {article_id}: {e}")

    # --- PAPELERA ---
    def mover_a_papelera(self, article_id: str):
        """Marca un artículo como eliminado (va a la papelera, no se borra)."""
        try:
            db.execute("UPDATE rss_articles SET deleted = 1, deleted_at = ? WHERE id = ?",
                       (datetime.now().isoformat(), article_id))
        except Exception as e:
            log.error(f"Error al mover a la papelera {article_id}: {e}")

    def restaurar_de_papelera(self, article_id: str):
        """Restaura un artículo de la papelera."""
        try:
            db.execute("UPDATE rss_articles SET deleted = 0, deleted_at = NULL WHERE id = ?", (article_id,))
        except Exception as e:
            log.error(f"Error al restaurar {article_id}: {e}")

    def listar_papelera(self, account_id: int) -> List[RSSArticle]:
        """Artículos en la papelera de una cuenta."""
        try:
            rows = db.query("""
                SELECT a.id, a.feed_id, a.guid, a.link, a.title, a.content, a.author, a.published_at,
                       a.read, a.favorite, a.has_media, a.media_url, a.media_type,
                       a.translated_content, a.ai_summary, COALESCE(a.read_later,0)
                FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
                WHERE a.account_id = ? AND COALESCE(a.deleted,0) = 1
                ORDER BY a.deleted_at DESC LIMIT 500
            """, (account_id,))
        except Exception as e:
            log.error(f"Error al listar la papelera: {e}")
            return []
        return [RSSArticle(
            id=r[0], feed_id=r[1], guid=r[2], link=r[3], title=r[4], content=r[5],
            author=r[6], published_at=r[7], read=bool(r[8]), favorite=bool(r[9]),
            has_media=bool(r[10]), media_url=r[11], media_type=r[12],
            translated_content=r[13], ai_summary=r[14], read_later=bool(r[15])
        ) for r in rows]

    def vaciar_papelera(self, account_id: Optional[int] = None) -> int:
        """Borra definitivamente los artículos de la papelera. Devuelve cuántos."""
        # Borrado DEFINITIVO que no vuelve (rss_borrados.py): antes los artículos borrados
        # reaparecían como nuevos en el siguiente sondeo si el feed los seguía sirviendo.
        try:
            from .rss_borrados import borrar_definitivo
            if account_id is not None:
                ids = [r[0] for r in db.query(
                    "SELECT a.id FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                    "WHERE f.account_id = ? AND COALESCE(a.deleted,0) = 1", (account_id,))]
            else:
                ids = [r[0] for r in db.query("SELECT id FROM rss_articles WHERE COALESCE(deleted,0) = 1")]
            return borrar_definitivo(ids)
        except Exception as e:
            log.error(f"Error al vaciar la papelera: {e}")
            return 0

    def marcar_leer_mas_tarde(self, article_id: str, read_later: bool = True):
        """Marca o desmarca un artículo como 'Leer más tarde'."""
        sql = "UPDATE rss_articles SET read_later = ? WHERE id = ?"
        try:
            db.execute(sql, (1 if read_later else 0, article_id))
        except Exception as e:
            log.error(f"Error al marcar leer-más-tarde el artículo {article_id}: {e}")

    # --- BÚSQUEDAS GUARDADAS (por cuenta; auditoría RSS-02) ---
    def obtener_busquedas_guardadas(self, account_id: int) -> List[Dict[str, Any]]:
        rows = db.query("SELECT id, name, query FROM rss_saved_searches "
                        "WHERE account_id = ? ORDER BY position, name", (account_id,))
        return [{"id": r[0], "name": r[1], "query": r[2]} for r in rows]

    def crear_busqueda_guardada(self, account_id: int, name: str, query: str) -> int:
        # Unicidad de nombre DENTRO de la cuenta: si ya existe, se actualiza su consulta.
        existente = db.query_one(
            "SELECT id FROM rss_saved_searches WHERE account_id = ? AND name = ?",
            (account_id, name))
        if existente:
            db.execute("UPDATE rss_saved_searches SET query = ? WHERE id = ?",
                       (query, existente[0]))
            return existente[0]
        return db.execute_insert(
            "INSERT INTO rss_saved_searches (account_id, name, query, position, created_at) "
            "VALUES (?,?,?,?,?)",
            (account_id, name, query, 0, datetime.now().isoformat()))

    def borrar_busqueda_guardada(self, search_id: int):
        db.execute("DELETE FROM rss_saved_searches WHERE id = ?", (search_id,))

    def editar_busqueda_guardada(self, search_id: int, name: str, query: str):
        """Cambia el nombre y el texto de una búsqueda guardada (antes solo crear y borrar)."""
        db.execute("UPDATE rss_saved_searches SET name = ?, query = ? WHERE id = ?",
                   (name, query, search_id))

    # --- ALERTAS POR PALABRA CLAVE (gestión; por cuenta, auditoría RSS-02) ---
    def obtener_alertas(self, account_id: int) -> List[Dict[str, Any]]:
        rows = db.query("SELECT id, keyword, is_regex, active FROM rss_keyword_alerts "
                        "WHERE account_id = ? ORDER BY id", (account_id,))
        return [{"id": r[0], "keyword": r[1], "is_regex": bool(r[2]), "active": bool(r[3])} for r in rows]

    def crear_alerta(self, account_id: int, keyword: str, is_regex: bool = False) -> int:
        nuevo = db.execute_insert(
            "INSERT INTO rss_keyword_alerts (account_id, keyword, is_regex, active, created_at) "
            "VALUES (?,?,?,1,?)",
            (account_id, keyword, 1 if is_regex else 0, datetime.now().isoformat()))
        self._invalidar_cache_alertas(account_id)
        return nuevo

    def borrar_alerta(self, alerta_id: int):
        r = db.query_one("SELECT account_id FROM rss_keyword_alerts WHERE id = ?", (alerta_id,))
        db.execute("DELETE FROM rss_keyword_alerts WHERE id = ?", (alerta_id,))
        if r:
            self._invalidar_cache_alertas(r[0])

    def editar_alerta(self, alerta_id: int, keyword: str, is_regex: bool):
        """Cambia la palabra (o expresión) de una alerta sin tener que borrarla y crearla."""
        r = db.query_one("SELECT account_id FROM rss_keyword_alerts WHERE id = ?", (alerta_id,))
        db.execute("UPDATE rss_keyword_alerts SET keyword = ?, is_regex = ? WHERE id = ?",
                   (keyword, 1 if is_regex else 0, alerta_id))
        if r:
            self._invalidar_cache_alertas(r[0])

    def activar_alerta(self, alerta_id: int, activa: bool):
        """Activa o desactiva una alerta (la desactivada se conserva, pero no avisa)."""
        r = db.query_one("SELECT account_id FROM rss_keyword_alerts WHERE id = ?", (alerta_id,))
        db.execute("UPDATE rss_keyword_alerts SET active = ? WHERE id = ?",
                   (1 if activa else 0, alerta_id))
        if r:
            self._invalidar_cache_alertas(r[0])

    def _invalidar_cache_alertas(self, account_id: int):
        """El parseo cachea las alertas por cuenta (ciclo): descartar al editar."""
        try:
            if hasattr(self, "_alertas_cache"):
                self._alertas_cache.pop(account_id, None)
        except Exception:
            pass

    # --- AJUSTES POR FEED ---
    def obtener_ajustes_feed(self, feed_id: int) -> Dict[str, Any]:
        r = db.query_one(
            "SELECT COALESCE(custom_interval,0), COALESCE(custom_user_agent,''), COALESCE(no_images,0), "
            "COALESCE(feed_kind,'rss'), COALESCE(force_fulltext,0) FROM rss_feeds WHERE id = ?", (feed_id,))
        if not r:
            return {"custom_interval": 0, "custom_user_agent": "", "no_images": False,
                    "feed_kind": "rss", "force_fulltext": False}
        return {"custom_interval": r[0], "custom_user_agent": r[1],
                "no_images": bool(r[2]), "feed_kind": r[3], "force_fulltext": bool(r[4])}

    def actualizar_ajustes_feed(self, feed_id: int, custom_interval: Optional[int] = None,
                                custom_user_agent: Optional[str] = None,
                                no_images: Optional[bool] = None,
                                force_fulltext: Optional[bool] = None):
        if custom_interval is not None:
            db.execute("UPDATE rss_feeds SET custom_interval = ? WHERE id = ?", (int(custom_interval), feed_id))
        if custom_user_agent is not None:
            db.execute("UPDATE rss_feeds SET custom_user_agent = ? WHERE id = ?", (custom_user_agent, feed_id))
        if no_images is not None:
            db.execute("UPDATE rss_feeds SET no_images = ? WHERE id = ?", (1 if no_images else 0, feed_id))
        if force_fulltext is not None:
            db.execute("UPDATE rss_feeds SET force_fulltext = ? WHERE id = ?", (1 if force_fulltext else 0, feed_id))

    # --- REPRODUCCIÓN DE PODCAST / YOUTUBE EN EL SEGUNDO REPRODUCTOR ---
    def reproducir_articulo_audio(self, article_id: str, callback: Optional[Callable] = None):
        """Reproduce un artículo como audio en el segundo reproductor.

        - Podcast: usa directamente la URL del adjunto de audio.
        - YouTube/otros: resuelve la pista de audio con yt-dlp a partir del enlace.
        El segundo reproductor recuerda la posición (continuar donde lo dejaste)."""
        r = db.query_one(
            "SELECT a.title, a.link, a.enclosure_url, COALESCE(a.is_podcast,0), COALESCE(f.feed_kind,'rss') "
            "FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id WHERE a.id = ?", (article_id,))
        if not r:
            if callback:
                callback(False, "No se encontró el artículo.")
            return
        title, link, enclosure_url, is_podcast, feed_kind = r

        from app.services.media_player.player2_service import (
            player2_service, KIND_PODCAST, KIND_YOUTUBE)

        # Podcast: si está descargado para escucha offline, reproducir el archivo LOCAL; si no, la
        # URL directa del audio.
        if is_podcast and enclosure_url:
            local = self.episodio_descargado(article_id)

            # player2 avisa con un solo argumento; los llamadores esperan (ok, error). Antes se
            # les pasaba tal cual y el aviso de fallo nunca llegaba (TypeError en el callback).
            def _on_podcast(ok):
                if callback:
                    callback(bool(ok), None if ok else _("No se pudo reproducir el episodio."))
            player2_service.cargar_y_reproducir(local or enclosure_url, title or _("Episodio"),
                                                KIND_PODCAST, resume=True, callback=_on_podcast,
                                                article_id=article_id)
            return

        # YouTube u otro enlace de servicio: resolver con yt-dlp en segundo plano.
        from app.services.audio import stream_resolver
        if not link:
            if callback:
                callback(False, "El artículo no tiene enlace.")
            return

        from app.core.task_manager import task_manager

        def _bg(ctx):
            audio_url, titulo = stream_resolver.resolver_audio_url(link)
            return (audio_url, titulo)

        def _reproducir_descargando(titulo_final):
            """Plan B: descargar el audio a un temporal y reproducirlo en local."""
            def _bg_dl(ctx):
                from app.core.paths import get_ffmpeg_path
                return stream_resolver.descargar_audio_temp(link, get_ffmpeg_path())

            def _after_dl(res2, err2):
                if err2 or not res2:
                    if callback:
                        callback(False, str(err2 or _("No se pudo reproducir el vídeo.")))
                    return
                ruta, titulo2 = res2

                def _on_play2(ok2):
                    if callback:
                        callback(bool(ok2), None if ok2 else _("No se pudo reproducir el vídeo."))

                player2_service.cargar_y_reproducir(
                    ruta, titulo2 or titulo_final, KIND_YOUTUBE, resume=True, callback=_on_play2,
                    article_id=article_id)

            task_manager.start_task(_bg_dl, callback=_after_dl, pass_context=True)

        def _finish(res, err):
            if err or not res:
                if callback:
                    callback(False, str(err or "No se pudo obtener el audio."))
                return
            audio_url, titulo = res
            titulo_final = titulo or title or _("Vídeo")

            def _on_play(ok):
                if ok:
                    if callback:
                        callback(True, None)
                    return
                # El streaming directo falló (formato no reproducible en directo):
                # descargar el audio a un temporal y reproducirlo en local.
                log.info("Streaming de vídeo falló; intentando descarga a archivo temporal.")
                _reproducir_descargando(titulo_final)

            player2_service.cargar_y_reproducir(
                audio_url, titulo_final, KIND_YOUTUBE, resume=True, callback=_on_play,
                article_id=article_id)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    # ------------------------------------------------------------------
    # DESCARGAS DE PODCAST (ESCUCHA OFFLINE)
    # ------------------------------------------------------------------
    @staticmethod
    def _carpeta_podcasts(feed_title: str = ""):
        from app.core.paths import get_data_path
        import re
        sub = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (feed_title or "").strip())[:60] or "podcasts"
        p = get_data_path("podcasts") / sub
        p.mkdir(parents=True, exist_ok=True)
        return p

    def episodio_descargado(self, article_id: str) -> Optional[str]:
        """Devuelve la ruta local del episodio si está descargado y el archivo sigue en disco; si
        la ruta guardada ya no existe (el usuario borró el archivo), la limpia y devuelve None."""
        import os
        r = db.query_one("SELECT download_path FROM rss_articles WHERE id = ?", (article_id,))
        ruta = r[0] if r and r[0] else None
        if ruta and os.path.exists(ruta):
            return ruta
        if ruta:  # estaba registrado pero el archivo ya no está: olvidarlo
            db.execute("UPDATE rss_articles SET download_path = NULL WHERE id = ?", (article_id,))
        return None

    def descargar_episodio(self, article_id: str, progreso: Optional[Callable] = None) -> Optional[str]:
        """Descarga el audio del episodio (enclosure) a la carpeta local de podcasts para escucharlo
        sin conexión. Guarda la ruta en download_path. progreso(actual, total) opcional (hilo UI).
        Devuelve la ruta local, o lanza una excepción si falla."""
        import os
        import requests
        from urllib.parse import urlparse
        r = db.query_one(
            "SELECT a.title, a.enclosure_url, a.media_url, f.title "
            "FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id WHERE a.id = ?", (article_id,))
        if not r:
            raise ValueError(_("Artículo no encontrado."))
        title, enclosure_url, media_url, feed_title = r
        url = enclosure_url or media_url
        if not url:
            raise ValueError(_("Este artículo no tiene audio descargable."))
        # Ya descargado: devolver la ruta existente.
        ya = self.episodio_descargado(article_id)
        if ya:
            return ya
        carpeta = self._carpeta_podcasts(feed_title)
        # Nombre de archivo: título saneado + extensión del enclosure.
        ext = os.path.splitext(urlparse(url).path)[1] or ".mp3"
        import re
        base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", (title or "episodio").strip())[:80] or "episodio"
        ruta = str(carpeta / (base + ext))
        # Descarga SEGURA (auditoría RSS-13): a un archivo temporal .part y renombrado
        # atómico al terminar. Un corte de red ya no deja un episodio a medias con el
        # nombre definitivo (que luego se reproduciría roto). Con tope de tamaño.
        MAX_EPISODIO = 1024 * 1024 * 1024  # 1 GiB: por encima, algo va mal
        ruta_part = ruta + ".part"
        try:
            with requests.get(url, stream=True, timeout=60,
                              headers={"User-Agent": "SocialRSS Suite"}) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                hecho = 0
                with open(ruta_part, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        hecho += len(chunk)
                        if hecho > MAX_EPISODIO:
                            raise ValueError(_("El episodio supera el tamaño máximo (1 GiB)."))
                        if progreso and total:
                            import wx
                            wx.CallAfter(progreso, hecho, total)
                # Si el servidor declaró el tamaño y llegó menos, la descarga está truncada.
                if total and hecho < total:
                    raise IOError(_("Descarga incompleta ({0} de {1} bytes).").format(hecho, total))
            os.replace(ruta_part, ruta)
        except Exception:
            try:
                if os.path.exists(ruta_part):
                    os.remove(ruta_part)
            except Exception:
                pass
            raise
        db.execute("UPDATE rss_articles SET download_path = ? WHERE id = ?", (ruta, article_id))
        log.info(f"Episodio de podcast descargado: {ruta}")
        return ruta

    def borrar_descarga_podcast(self, article_id: str) -> bool:
        """Borra el archivo descargado de un episodio y limpia su ruta."""
        import os
        r = db.query_one("SELECT download_path FROM rss_articles WHERE id = ?", (article_id,))
        ruta = r[0] if r and r[0] else None
        if ruta and os.path.exists(ruta):
            try:
                os.remove(ruta)
            except Exception as e:
                log.debug(f"No se pudo borrar el archivo {ruta}: {e}")
        db.execute("UPDATE rss_articles SET download_path = NULL WHERE id = ?", (article_id,))
        return True

    def obtener_descargas_podcast(self, account_id: int) -> List[Dict[str, Any]]:
        """Lista los episodios descargados (offline) DE UNA CUENTA con su tamaño en disco
        (auditoría RSS-03: antes mezclaba las descargas de todas las cuentas)."""
        import os
        filas = db.query("""
            SELECT a.id, a.title, f.title, a.download_path
            FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
            WHERE COALESCE(a.download_path,'') != '' AND f.account_id = ?
            ORDER BY a.published_at DESC
        """, (account_id,))
        salida = []
        for r in filas or []:
            existe = bool(r[3] and os.path.exists(r[3]))
            tam = os.path.getsize(r[3]) if existe else 0
            salida.append({"article_id": r[0], "title": r[1] or "", "feed_title": r[2] or "",
                           "path": r[3] or "", "existe": existe, "size": tam})
        return salida

    def vaciar_articulos_volatiles(self, account_id: int) -> int:
        """Borra los artículos NO esenciales de una cuenta para que la base de datos no crezca sin
        fin ("no conservar entre sesiones"). CONSERVA destacados, leer-más-tarde y los episodios
        descargados (offline). Devuelve cuántos se borraron. Se llama al arrancar."""
        cond = ("feed_id IN (SELECT id FROM rss_feeds WHERE account_id = ?) "
                "AND COALESCE(favorite,0)=0 AND COALESCE(read_later,0)=0 "
                "AND COALESCE(download_path,'')=''")
        try:
            n = (db.query_one(f"SELECT COUNT(*) FROM rss_articles WHERE {cond}", (account_id,)) or [0])[0]
            # ANTES de borrar: anotar en el registro persistente los artículos LEÍDOS que se van a
            # vaciar. Su id (feed_id:guid) es estable, así que si el feed los vuelve a servir se
            # re-insertarán ya como leídos en vez de reaparecer como novedades sin leer.
            try:
                from datetime import datetime as _dt
                db.execute(
                    f"INSERT OR IGNORE INTO rss_read_ledger (article_id, ts) "
                    f"SELECT id, ? FROM rss_articles WHERE {cond} AND COALESCE(read,0)=1",
                    (_dt.now().isoformat(), account_id))
                # Tope de seguridad: conservar solo las 100 000 anotaciones más recientes.
                db.execute(
                    "DELETE FROM rss_read_ledger WHERE article_id IN ("
                    "  SELECT article_id FROM rss_read_ledger ORDER BY ts DESC LIMIT -1 OFFSET 100000)")
            except Exception as e_led:
                log.debug(f"RSS: no se pudo anotar leídos en el registro antes del vaciado: {e_led}")
            # Limpiar primero su índice de búsqueda (FTS5) y luego los artículos.
            try:
                db.execute(
                    f"DELETE FROM rss_articles_fts WHERE article_db_id IN "
                    f"(SELECT id FROM rss_articles WHERE {cond})", (account_id,))
            except Exception:
                pass
            db.execute(f"DELETE FROM rss_articles WHERE {cond}", (account_id,))
            log.info(f"RSS: vaciados {n or 0} artículos volátiles de la cuenta {account_id}.")
            return n or 0
        except Exception as e:
            log.error(f"Error al vaciar artículos volátiles de RSS (cuenta {account_id}): {e}")
            return 0

    def obtener_enclosures(self, article_id: str) -> List[Dict[str, Any]]:
        """Todos los adjuntos (enclosures) de un artículo, en su orden original (RSS:
        "múltiples enclosures"). Cada uno: {url, mime, length, es_audio}."""
        try:
            filas = db.query(
                "SELECT url, COALESCE(mime,''), COALESCE(length,0), COALESCE(es_audio,0) "
                "FROM rss_enclosures WHERE article_id = ? ORDER BY orden, id", (article_id,))
        except Exception:
            return []
        return [{"url": r[0], "mime": r[1], "length": r[2], "es_audio": bool(r[3])}
                for r in filas]

    def obtener_metadatos_podcast(self, article_id: str) -> Dict[str, Any]:
        """Metadatos de Podcast 2.0 de un episodio (para el detalle accesible): duración,
        contenido explícito, temporada/episodio, imagen y los metadatos ricos (capítulos,
        transcripción, apoyo, personas). Diccionario vacío si el artículo no los tiene."""
        import json as _json
        try:
            r = db.query_one(
                "SELECT duration_seconds, COALESCE(explicit,0), episode_image, season, "
                "episode_num, podcast_json FROM rss_articles WHERE id = ?", (article_id,))
        except Exception:
            return {}
        if not r:
            return {}
        rico = {}
        if r[5]:
            try:
                rico = _json.loads(r[5]) or {}
            except Exception:
                rico = {}
        return {
            "duration_seconds": r[0], "explicit": bool(r[1]), "episode_image": r[2],
            "season": r[3], "episode_num": r[4],
            "transcript": rico.get("transcript"), "chapters": rico.get("chapters"),
            "funding": rico.get("funding"), "person": rico.get("person"),
        }

    def texto_detalles_episodio(self, article_id: str) -> str:
        """Datos de Podcast 2.0 de un episodio en texto legible, para el lector limpio y la vista
        previa: temporada y episodio, duración, contenido explícito, enlaces a la transcripción,
        los capítulos, el apoyo al podcast y las personas, y los adjuntos si hay más de uno.
        Se guardaban al leer el feed pero no se mostraban en ningún sitio. "" si no hay nada."""
        meta = self.obtener_metadatos_podcast(article_id)
        lineas = []
        if meta:
            partes = []
            if meta.get("season"):
                partes.append(_("temporada {0}").format(meta["season"]))
            if meta.get("episode_num"):
                partes.append(_("episodio {0}").format(meta["episode_num"]))
            if partes:
                lineas.append(_("Episodio: {0}").format(", ".join(partes)))
            duracion = self.formatear_duracion(meta.get("duration_seconds"))
            if duracion:
                lineas.append(_("Duración: {0}").format(duracion))
            if meta.get("explicit"):
                lineas.append(_("Contenido explícito"))
            for clave, etiqueta in (("transcript", _("Transcripción")),
                                    ("chapters", _("Capítulos")),
                                    ("funding", _("Apoyar al podcast"))):
                dato = meta.get(clave) or {}
                if isinstance(dato, dict) and dato.get("url"):
                    lineas.append(f"{etiqueta}: {dato['url']}")
            persona = meta.get("person") or {}
            if isinstance(persona, dict) and (persona.get("role") or persona.get("url")):
                lineas.append(_("Persona ({0}): {1}").format(
                    persona.get("role") or _("participante"), persona.get("url") or ""))
        adjuntos = self.obtener_enclosures(article_id)
        if len(adjuntos) > 1:
            lineas.append(_("Adjuntos ({0}):").format(len(adjuntos)))
            for a in adjuntos:
                tam = int(a.get("length") or 0)
                tam_txt = f", {tam / 1048576:.1f} MB" if tam > 0 else ""
                lineas.append(f"- {a.get('mime') or _('archivo')}{tam_txt}: {a.get('url')}")
        return "\n".join(lineas)

    def generar_digest(self, account_id: int, limite: int = 25,
                       con_ia: bool = True) -> Dict[str, Any]:
        """DIGEST de artículos no leídos (auditoría, prioridad D): reúne los no leídos más
        recientes y, si hay un proveedor de IA activo y se pide, produce un resumen breve;
        si no, una lista legible. Devuelve {texto, n, con_ia}. Se llama en segundo plano."""
        filas = db.query("""
            SELECT a.title, f.title, a.published_at, a.content
            FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
            WHERE a.account_id = ? AND COALESCE(a.read,0)=0 AND COALESCE(a.deleted,0)=0
            ORDER BY a.published_at DESC LIMIT ?
        """, (account_id, max(1, int(limite))))
        if not filas:
            return {"texto": _("No tienes artículos sin leer."), "n": 0, "con_ia": False}
        from app.core.html_utils import html_a_texto
        lineas = []
        for titulo, feed, fecha, contenido in filas:
            extracto = html_a_texto(contenido or "")[:200].strip()
            lineas.append("- {0} ({1}): {2}".format(titulo or "", feed or "", extracto))
        listado = "\n".join(lineas)
        if con_ia:
            try:
                from app.services.ai.ai_service import ai_service
                if ai_service.obtener_proveedor_activo():
                    peticion = _(
                        "A continuación tienes titulares de noticias sin leer, uno por línea. "
                        "Resúmelos en español en pocas frases, agrupando lo relacionado por "
                        "temas:\n\n") + listado
                    resumen = ai_service.resumir_texto(peticion)
                    if resumen:
                        return {"texto": resumen, "n": len(filas), "con_ia": True}
            except Exception as e:
                log.debug(f"Digest con IA no disponible: {e}")
        # Sin IA: lista legible de titulares.
        cab = _("Tienes {0} artículos sin leer. Titulares recientes:\n\n").format(len(filas))
        return {"texto": cab + listado, "n": len(filas), "con_ia": False}

    def obtener_imagenes_articulo(self, article_id: str) -> List[Dict[str, Any]]:
        """Imágenes del cuerpo de un artículo (auditoría, prioridad B: galería accesible).
        Cada una: {url, alt}. Incluye la imagen del episodio de podcast si la tiene. No
        descarga nada: solo extrae las URLs del HTML ya guardado y su texto alternativo."""
        r = db.query_one(
            "SELECT content, episode_image FROM rss_articles WHERE id = ?", (article_id,))
        if not r:
            return []
        contenido, episode_image = r[0] or "", r[1]
        imagenes, vistos = [], set()
        if episode_image:
            imagenes.append({"url": episode_image, "alt": _("Imagen del episodio")})
            vistos.add(episode_image)
        if contenido and "<img" in contenido.lower():
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(contenido, "html.parser")
                for img in soup.find_all("img"):
                    src = (img.get("src") or "").strip()
                    if not src or src in vistos or not src.lower().startswith(("http://", "https://")):
                        continue
                    vistos.add(src)
                    imagenes.append({"url": src, "alt": (img.get("alt") or "").strip()})
            except Exception as e:
                log.debug(f"No se pudieron extraer imágenes de {article_id}: {e}")
        return imagenes

    @staticmethod
    def formatear_duracion(segundos) -> str:
        """Segundos -> 'H:MM:SS' o 'M:SS' legible. Cadena vacía si no hay duración."""
        try:
            s = int(segundos)
        except (TypeError, ValueError):
            return ""
        if s <= 0:
            return ""
        h, resto = divmod(s, 3600)
        m, seg = divmod(resto, 60)
        return f"{h}:{m:02d}:{seg:02d}" if h else f"{m}:{seg:02d}"

    def obtener_articulo(self, article_id: str) -> Optional[RSSArticle]:
        """Devuelve un único artículo por su id (para copiar/acciones)."""
        try:
            r = db.query_one("""
                SELECT id, feed_id, guid, link, title, content, author, published_at,
                       read, favorite, has_media, media_url, media_type,
                       translated_content, ai_summary, COALESCE(read_later, 0)
                FROM rss_articles WHERE id = ?
            """, (article_id,))
            if not r:
                return None
            return RSSArticle(
                id=r[0], feed_id=r[1], guid=r[2], link=r[3], title=r[4],
                content=r[5], author=r[6], published_at=r[7], read=bool(r[8]),
                favorite=bool(r[9]), has_media=bool(r[10]), media_url=r[11],
                media_type=r[12], translated_content=r[13], ai_summary=r[14],
                read_later=bool(r[15])
            )
        except Exception as e:
            log.error(f"Error al obtener el artículo {article_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # HISTORIAL Y ESTADÍSTICAS DE LECTURA
    # ------------------------------------------------------------------
    def registrar_lectura(self, article_id: str):
        """Anota en el historial que se ha abierto un artículo (para historial y estadísticas).
        Cachea el título y el feed por si el artículo se borra más adelante."""
        try:
            r = db.query_one("""
                SELECT a.feed_id, a.title, f.title, f.account_id
                FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
                WHERE a.id = ?
            """, (article_id,))
            if not r:
                return
            db.execute_insert(
                "INSERT INTO rss_reading_log (account_id, article_db_id, feed_id, title, feed_title, opened_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r[3], article_id, r[0], r[1] or "", r[2] or "", datetime.now().isoformat()))
        except Exception as e:
            log.debug(f"No se pudo registrar la lectura del artículo {article_id}: {e}")

    def obtener_historial_lectura(self, account_id: int, limit: int = 200) -> List[Dict[str, Any]]:
        """Últimos artículos abiertos DE UNA CUENTA (sin repetir el mismo artículo seguido),
        de más reciente a más antiguo (auditoría RSS-03: antes mezclaba cuentas).
        Cada entrada: {article_db_id, title, feed_title, opened_at, existe}."""
        try:
            filas = db.query("""
                SELECT l.article_db_id, l.title, l.feed_title, MAX(l.opened_at) AS cuando,
                       (SELECT COUNT(*) FROM rss_articles a WHERE a.id = l.article_db_id) AS existe
                FROM rss_reading_log l
                WHERE l.account_id = ?
                GROUP BY l.article_db_id
                ORDER BY cuando DESC LIMIT ?
            """, (account_id, limit))
            return [{"article_db_id": r[0], "title": r[1] or "", "feed_title": r[2] or "",
                     "opened_at": r[3] or "", "existe": bool(r[4])} for r in filas]
        except Exception as e:
            log.error(f"Error al obtener el historial de lectura: {e}")
            return []

    def vaciar_historial_lectura(self, account_id: int) -> int:
        """Borra el historial de lectura DE UNA CUENTA (no el de las demás).
        Devuelve cuántos registros se borraron."""
        try:
            n = (db.query_one("SELECT COUNT(*) FROM rss_reading_log WHERE account_id = ?",
                              (account_id,)) or [0])[0]
            db.execute("DELETE FROM rss_reading_log WHERE account_id = ?", (account_id,))
            return n or 0
        except Exception as e:
            log.error(f"Error al vaciar el historial de lectura: {e}")
            return 0

    def obtener_estadisticas_lectura(self, account_id: int) -> Dict[str, Any]:
        """Estadísticas de lectura DE UNA CUENTA: totales (hoy/semana/total), top feeds y
        pendientes (auditoría RSS-03: antes contaba artículos de todas las cuentas)."""
        from datetime import timedelta
        res = {"hoy": 0, "semana": 0, "total": 0, "no_leidos": 0, "top_feeds": []}
        try:
            hoy = datetime.now().date().isoformat()
            semana = (datetime.now() - timedelta(days=7)).isoformat()
            res["total"] = (db.query_one(
                "SELECT COUNT(DISTINCT article_db_id) FROM rss_reading_log WHERE account_id = ?",
                (account_id,)) or [0])[0] or 0
            res["hoy"] = (db.query_one(
                "SELECT COUNT(DISTINCT article_db_id) FROM rss_reading_log "
                "WHERE account_id = ? AND opened_at >= ?", (account_id, hoy)) or [0])[0] or 0
            res["semana"] = (db.query_one(
                "SELECT COUNT(DISTINCT article_db_id) FROM rss_reading_log "
                "WHERE account_id = ? AND opened_at >= ?", (account_id, semana)) or [0])[0] or 0
            res["no_leidos"] = (db.query_one(
                "SELECT COUNT(*) FROM rss_articles WHERE account_id = ? "
                "AND COALESCE(read,0)=0 AND COALESCE(deleted,0)=0", (account_id,))
                or [0])[0] or 0
            res["top_feeds"] = [{"feed": r[0] or _("(desconocido)"), "n": r[1]} for r in db.query("""
                SELECT feed_title, COUNT(*) n FROM rss_reading_log
                WHERE account_id = ?
                GROUP BY feed_title ORDER BY n DESC LIMIT 10
            """, (account_id,))]
        except Exception as e:
            log.error(f"Error al calcular estadísticas de lectura: {e}")
        return res

    # ------------------------------------------------------------------
    # EXPORTAR UN ARTÍCULO A UN ARCHIVO
    # ------------------------------------------------------------------
    def exportar_articulo(self, article_id: str, ruta: str, formato: str = "txt") -> bool:
        """Exporta un artículo a un archivo de texto, HTML o Markdown (título, autor, fecha,
        enlace y contenido). formato: 'txt', 'html' o 'md'.

        SEGURIDAD (auditoría RSS-18): los metadatos se ESCAPAN y el cuerpo HTML se sanea
        con la misma lista blanca del visor. Un feed malicioso ya no puede colar scripts
        ni atributos peligrosos en el archivo exportado; el enlace solo se emite como tal
        si es http/https."""
        import html as _html
        import builtins as _bi
        from app.core.html_utils import html_a_texto, sanear_html
        _ = getattr(_bi, "_", lambda s: s)  # gettext puede no estar instalado (tests/CLI)
        r = db.query_one("""
            SELECT a.title, a.author, a.published_at, a.link, a.content, f.title
            FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id WHERE a.id = ?
        """, (article_id,))
        if not r:
            raise ValueError(_("Artículo no encontrado."))
        titulo, autor, fecha, enlace, contenido, feed = r
        titulo = titulo or _("(sin título)")
        enlace = (enlace or "").strip()
        enlace_seguro = enlace if enlace.lower().startswith(("http://", "https://")) else ""
        contenido_html = sanear_html(contenido or "")
        contenido_txt = html_a_texto(contenido or "") if contenido else ""
        if formato == "html":
            e = _html.escape
            href = e(enlace_seguro, quote=True)
            doc = (f"<!DOCTYPE html><html lang='es'><head><meta charset='utf-8'>"
                   f"<title>{e(titulo)}</title></head>"
                   f"<body><h1>{e(titulo)}</h1>"
                   f"<p><strong>{e(_('Fuente'))}:</strong> {e(feed or '')} | "
                   f"<strong>{e(_('Autor'))}:</strong> {e(autor or '')} | "
                   f"<strong>{e(_('Fecha'))}:</strong> {e(fecha or '')}</p>"
                   + (f"<p><a href='{href}'>{e(enlace_seguro)}</a></p>" if enlace_seguro else "")
                   + f"<hr>{contenido_html}</body></html>")
            cuerpo = doc
        elif formato == "md":
            # Escapar lo que rompería el formato o colaría un destino raro en el enlace.
            def _md(t):
                return (t or "").replace("[", "\\[").replace("]", "\\]").replace("`", "\\`")
            enlace_md = enlace_seguro.replace("(", "%28").replace(")", "%29")
            cuerpo = (f"# {_md(titulo)}\n\n**{_('Fuente')}:** {_md(feed)}  \n"
                      f"**{_('Autor')}:** {_md(autor)}  \n**{_('Fecha')}:** {fecha or ''}  \n\n"
                      + (f"[{_md(enlace_seguro)}]({enlace_md})\n\n" if enlace_seguro else "")
                      + f"---\n\n{contenido_txt}\n")
        else:  # txt
            cuerpo = (f"{titulo}\n{'=' * len(titulo)}\n"
                      f"{_('Fuente')}: {feed or ''}\n{_('Autor')}: {autor or ''}\n"
                      f"{_('Fecha')}: {fecha or ''}\n{_('Enlace')}: {enlace or ''}\n\n{contenido_txt}\n")
        with open(ruta, "w", encoding="utf-8") as fh:
            fh.write(cuerpo)
        return True

    def exportar_articulos_lote_async(self, ids: list, carpeta: str, formato: str = "txt",
                                      callback: Optional[Callable] = None):
        """Exporta VARIOS artículos (auditoría, prioridad A: exportación por lotes) a la
        carpeta indicada, un archivo por artículo con nombre saneado, en segundo plano y
        con progreso/cancelación. Reutiliza exportar_articulo (metadatos escapados y HTML
        saneado). callback(n_exportados, error)."""
        import os
        import re

        def _bg(ctx):
            exportados = 0
            usados = set()
            ext = {"html": ".html", "md": ".md"}.get(formato, ".txt")
            for i, art_id in enumerate(ids):
                if ctx.is_cancelled():
                    break
                r = db.query_one("SELECT title FROM rss_articles WHERE id = ?", (art_id,))
                titulo = (r[0] if r and r[0] else "articulo").strip()
                base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", titulo)[:80] or "articulo"
                nombre = base
                n = 1
                while (nombre + ext).lower() in usados:
                    n += 1
                    nombre = f"{base} ({n})"
                usados.add((nombre + ext).lower())
                ruta = os.path.join(carpeta, nombre + ext)
                try:
                    self.exportar_articulo(art_id, ruta, formato=formato)
                    exportados += 1
                except Exception as e:
                    log.debug(f"No se pudo exportar el artículo {art_id}: {e}")
                ctx.report_progress(int(((i + 1) / max(len(ids), 1)) * 100),
                                    _("Exportado: {0}").format(titulo))
            return exportados

        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                pass_context=True)

    def buscar_articulos_fts(self, account_id: int, query_text: str, limit: int = 100) -> List[RSSArticle]:
        """Búsqueda rápida en texto completo usando SQLite FTS5.

        UNA sola consulta con JOIN (auditoría RSS-16): la cuenta y la papelera se filtran
        ANTES de aplicar el límite y el orden es por relevancia real (bm25). Antes se
        tomaban los primeros N resultados GLOBALES del índice y luego se filtraba la
        cuenta: coincidencias de otras cuentas consumían el cupo y escondían resultados
        válidos de la tuya."""
        # Sanitizar consulta simple (las comillas dobles del usuario se escapan doblándolas)
        safe_query = ' '.join('"{0}"'.format(w.replace('"', '""')) for w in query_text.split() if w)
        if not safe_query:
            return []

        articulos = []
        try:
            rows = db.query("""
                SELECT a.id, a.feed_id, a.guid, a.link, a.title, a.content, a.author,
                       a.published_at, a.read, a.favorite, a.has_media, a.media_url,
                       a.media_type, a.translated_content, a.ai_summary
                FROM rss_articles_fts fts
                JOIN rss_articles a ON a.id = fts.article_db_id
                WHERE rss_articles_fts MATCH ? AND a.account_id = ?
                      AND COALESCE(a.deleted, 0) = 0
                ORDER BY bm25(rss_articles_fts)
                LIMIT ?
            """, (safe_query, account_id, limit))
            for r in rows:
                articulos.append(RSSArticle(
                    id=r[0], feed_id=r[1], guid=r[2], link=r[3], title=r[4],
                    content=r[5], author=r[6], published_at=r[7], read=bool(r[8]),
                    favorite=bool(r[9]), has_media=bool(r[10]), media_url=r[11],
                    media_type=r[12], translated_content=r[13], ai_summary=r[14]
                ))
        except Exception as e:
            log.error(f"Error en búsqueda FTS5 RSS: {e}")
            # Respaldo sin FTS5 (o con el índice roto): LIKE sencillo sobre título/contenido.
            try:
                patron = f"%{query_text.strip()}%"
                rows = db.query("""
                    SELECT a.id, a.feed_id, a.guid, a.link, a.title, a.content, a.author,
                           a.published_at, a.read, a.favorite, a.has_media, a.media_url,
                           a.media_type, a.translated_content, a.ai_summary
                    FROM rss_articles a
                    WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0
                          AND (a.title LIKE ? OR a.content LIKE ?)
                    ORDER BY a.published_at DESC LIMIT ?
                """, (account_id, patron, patron, limit))
                for r in rows:
                    articulos.append(RSSArticle(
                        id=r[0], feed_id=r[1], guid=r[2], link=r[3], title=r[4],
                        content=r[5], author=r[6], published_at=r[7], read=bool(r[8]),
                        favorite=bool(r[9]), has_media=bool(r[10]), media_url=r[11],
                        media_type=r[12], translated_content=r[13], ai_summary=r[14]
                    ))
            except Exception as e2:
                log.error(f"Búsqueda de respaldo (LIKE) también falló: {e2}")
        return articulos

    # --- IMPORTACIÓN/EXPORTACIÓN OPML ---
    def analizar_opml(self, account_id: int, opml_path: str) -> List[Dict[str, Any]]:
        """VISTA PREVIA de una importación OPML (auditoría, prioridad A): lee el archivo
        SIN escribir nada y clasifica cada feed como 'nuevo', 'duplicado' (ya suscrito,
        por URL normalizada) o 'invalido' (sin URL utilizable). Devuelve la lista con
        título, URL, ruta de carpetas y estado, para que el usuario elija qué importar."""
        feeds = importar_opml(opml_path)
        resultado = []
        for f in feeds:
            url = (f.get("url") or "").strip()
            if not url or not url.lower().startswith(("http://", "https://")):
                estado = "invalido"
            elif self.buscar_feed_por_url(account_id, url):
                estado = "duplicado"
            else:
                estado = "nuevo"
            resultado.append({
                "title": f.get("title") or url, "url": url,
                "category_path": f.get("category_path") or [],
                "estado": estado,
            })
        return resultado

    def importar_feeds_seleccionados_async(self, account_id: int, feeds: list,
                                           callback: Optional[Callable] = None):
        """Importa SOLO los feeds elegidos en la vista previa (lista de dicts con
        title/url/category_path), creando las carpetas anidadas que hagan falta."""
        def _bg_import(ctx):
            agregados = 0
            cache_rutas = {}

            def _categoria_de_ruta(ruta):
                ruta = tuple(ruta or ())
                if not ruta:
                    return None
                if ruta in cache_rutas:
                    return cache_rutas[ruta]
                padre = _categoria_de_ruta(ruta[:-1])
                cid = self.agregar_categoria(account_id, ruta[-1], parent_id=padre)
                cache_rutas[ruta] = cid or None
                return cache_rutas[ruta]

            for idx, f in enumerate(feeds):
                if ctx.is_cancelled():
                    break
                ctx.report_progress(int((idx / max(len(feeds), 1)) * 100),
                                    _("Importando: {0}").format(f.get("title", "")))
                cat_id = _categoria_de_ruta(f.get("category_path"))
                if not self.buscar_feed_por_url(account_id, f["url"]):
                    nuevo = self.agregar_feed(account_id, f["url"], f.get("title") or f["url"], cat_id)
                    self.sus_alta_feed(nuevo)
                    agregados += 1
            return agregados

        task_manager.start_task(_bg_import, callback=callback, success_sound="ready",
                                pass_context=True)

    def importar_feeds_opml_async(self, account_id: int, opml_path: str, callback: Optional[Callable[[int, Optional[Exception]], None]] = None):
        """Importa feeds desde un OPML e inserta categorías/feeds asíncronamente.
        Conserva la JERARQUÍA de carpetas del OPML (auditoría RSS-20) y evita duplicados
        por URL NORMALIZADA (barra final, mayúsculas del host...)."""
        def _bg_import(ctx):
            feeds = importar_opml(opml_path)
            agregados = 0
            cache_rutas = {}  # tupla de ruta -> category_id (no recrear por cada feed)

            def _categoria_de_ruta(ruta):
                ruta = tuple(ruta or ())
                if not ruta:
                    return None
                if ruta in cache_rutas:
                    return cache_rutas[ruta]
                padre = _categoria_de_ruta(ruta[:-1])
                cid = self.agregar_categoria(account_id, ruta[-1], parent_id=padre)
                cache_rutas[ruta] = cid or None
                return cache_rutas[ruta]

            for idx, f in enumerate(feeds):
                if ctx.is_cancelled():
                    break
                ctx.report_progress(int((idx / len(feeds)) * 100), f"Importando: {f['title']}")

                cat_id = _categoria_de_ruta(f.get("category_path")
                                            or ([f["category"]] if f.get("category") else []))

                # Registrar feed (evitar duplicar la misma URL —normalizada— en la cuenta)
                if not self.buscar_feed_por_url(account_id, f["url"]):
                    self.agregar_feed(account_id, f["url"], f["title"], cat_id)
                    agregados += 1

            return agregados

        task_manager.start_task(
            _bg_import,
            callback=callback,
            success_sound="ready",
            pass_context=True
        )

    def exportar_feeds_opml(self, account_id: int, opml_path: str):
        """Exporta los feeds registrados de la cuenta a un archivo OPML 2.0, conservando
        la jerarquía de carpetas (auditoría RSS-20)."""
        feeds_db = self.obtener_feeds(account_id)
        cats = {c["id"]: c for c in self.obtener_categorias(account_id)}

        def _ruta(cat_id):
            ruta, visto = [], set()
            actual = cat_id
            while actual and actual in cats and actual not in visto:
                visto.add(actual)
                ruta.append(cats[actual]["name"])
                actual = cats[actual].get("parent_id")
            return list(reversed(ruta))

        feeds_export = []
        for f in feeds_db:
            ruta = _ruta(f.category_id) if f.category_id else []
            feeds_export.append({
                "title": f.title,
                "url": f.url,
                "category": ruta[-1] if ruta else None,
                "category_path": ruta,
            })

        exportar_opml(opml_path, feeds_export)
