# -*- coding: utf-8 -*-
"""Categorías, alta/detección de feeds, validación y mantenimiento.

Mixin de RSSService (parte de la refactorización modular).
"""
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable
import feedparser

from app.core.database import db
from app.core.config_manager import config
from app.core.task_manager import task_manager
from app.core.models import RSSFeed


log = logging.getLogger(__name__)

class _FeedsMixin:
    def _generar_article_id(self, feed_id: int, guid: str, link: str, title: str) -> str:
        """Genera un hash único estable para evitar duplicados en la base de datos."""
        val = guid or link or title
        sha = hashlib.sha256()
        sha.update(f"{feed_id}:{val}".encode("utf-8"))
        return sha.hexdigest()

    # Máximo de bytes DESCOMPRIMIDOS que se aceptan de un feed (auditoría RSS-13):
    # un servidor malicioso o roto no puede llenar la memoria con un XML "bomba".
    _MAX_BYTES_FEED = 10 * 1024 * 1024  # 10 MiB
    # Estados HTTP que SÍ se reintentan (auditoría RSS-10): transitorios. El resto de
    # 4xx (URL mal, sin permiso, retirado...) es permanente: reintentarlo solo quema red.
    _HTTP_REINTENTABLES = {408, 425, 429, 500, 502, 503, 504}

    @staticmethod
    def _leer_cuerpo_limitado(resp, maximo: int) -> bytes:
        """Lee el cuerpo en streaming hasta 'maximo' bytes DESCOMPRIMIDOS; si el servidor
        manda más, corta con error (mejor fallar que tragar un cuerpo gigante)."""
        trozos = []
        leido = 0
        for chunk in resp.iter_content(chunk_size=65536, decode_unicode=False):
            if not chunk:
                continue
            leido += len(chunk)
            if leido > maximo:
                raise ValueError(f"La respuesta supera el límite de {maximo // (1024*1024)} MiB.")
            trozos.append(chunk)
        return b"".join(trozos)

    # Valor especial de custom_user_agent: "preséntate como un navegador de escritorio" (con el
    # juego completo de cabeceras de Firefox, no solo el User-Agent, que se queda viejo).
    UA_NAVEGADOR = "navegador"

    @classmethod
    def _cabeceras_feed(cls, user_agent: str = "") -> dict:
        """Cabeceras para pedir un feed: la identificación de la app, la del usuario para ese
        feed, o las de un Firefox de escritorio si vale UA_NAVEGADOR."""
        if user_agent == cls.UA_NAVEGADOR:
            from app.services.rss.rss_fulltext import CABECERAS_NAVEGADOR
            cab = dict(CABECERAS_NAVEGADOR)
            cab["Accept"] = ("application/rss+xml, application/atom+xml, application/xml;q=0.9, "
                             "text/xml;q=0.9, text/html;q=0.8, */*;q=0.7")
            return cab
        return {"User-Agent": user_agent or "SocialRSS Suite"}

    def _descargar_con_reintentos(self, url: str, intentos: int = 3, timeout: int = 20,
                                  user_agent: str = "", etag: str = "", modificado: str = "",
                                  devolver_meta: bool = False, feed_id: int = None):
        """
        Descarga el contenido de una URL con política de reintentos ACOTADA (auditoría
        RSS-10): solo errores de conexión/timeout y estados transitorios (408, 425, 429,
        5xx) se reintentan, respetando Retry-After y con variación aleatoria; un 4xx
        permanente (404, 410, 401...) falla a la primera. El cuerpo se lee en streaming
        con límite de tamaño descomprimido (RSS-13).

        PETICIONES CONDICIONALES: si se pasan `etag`/`modificado` (de una descarga anterior), se
        envían If-None-Match / If-Modified-Since; si el servidor responde 304 (no modificado), NO se
        descarga el cuerpo y se evita el reparseo (que es lo caro). Con `devolver_meta=True` se
        devuelve una tupla (contenido_o_None, etag_nuevo, modificado_nuevo, no_modificado,
        url_mudanza); `url_mudanza` es la nueva URL si el servidor respondió con un redirect
        PERMANENTE (301/308) —feed mudado, RSS prioridad D—, o "" si no. Sin `devolver_meta` se
        devuelven solo los bytes (compatibilidad con otros llamadores)."""
        import time
        import random
        import requests
        from app.core.net import http_session
        headers = self._cabeceras_feed(user_agent)
        if etag:
            headers["If-None-Match"] = etag
        if modificado:
            headers["If-Modified-Since"] = modificado
        ultimo_error = None
        probado_navegador = user_agent == self.UA_NAVEGADOR
        for intento in range(intentos):
            try:
                # Sesión POR HILO con pool de conexiones: reutiliza la conexión TLS entre
                # los sondeos de feeds del mismo servidor (antes, un handshake por petición).
                resp = http_session().get(url, timeout=timeout, headers=headers, stream=True)
                # IDENTIFICARSE COMO NAVEGADOR: algunos cortafuegos web (WAF) rechazan con
                # 403/406 a quien no es un navegador, aunque el feed sea público (Diario de
                # Mallorca: 406 "WAF Forbidden" con "SocialRSS Suite" y 200 con Firefox).
                # Solo si el usuario no fijó una identificación propia para el feed. Si
                # funciona, se recuerda para ese feed (custom_user_agent = "navegador").
                if (resp.status_code in (403, 406) and not user_agent
                        and not probado_navegador):
                    probado_navegador = True
                    headers = dict(headers, **self._cabeceras_feed(self.UA_NAVEGADOR))
                    resp2 = http_session().get(url, timeout=timeout, headers=headers,
                                               stream=True)
                    if resp2.status_code < 400:
                        log.info(f"Feed {url}: rechazado como aplicación ({resp.status_code}); "
                                 f"se accede identificándose como navegador.")
                        if feed_id:
                            try:
                                db.execute("UPDATE rss_feeds SET custom_user_agent = ? WHERE id = ? "
                                           "AND COALESCE(custom_user_agent,'') = ''",
                                           (self.UA_NAVEGADOR, feed_id))
                            except Exception:
                                pass
                        resp.close()
                        resp = resp2
                    else:
                        resp2.close()
                if resp.status_code == 304:
                    resp.close()
                    if devolver_meta:
                        return (None, etag, modificado, True, "")
                    return b""
                with resp:
                    if resp.status_code >= 400:
                        if resp.status_code in self._HTTP_REINTENTABLES and intento < intentos - 1:
                            # Transitorio: esperar lo que pida el servidor (Retry-After, con
                            # tope) o la espera exponencial con variación aleatoria.
                            espera = 0.5 * (2 ** intento) + random.uniform(0, 0.3)
                            ra = resp.headers.get("Retry-After", "")
                            if ra.isdigit():
                                espera = min(float(ra), 30.0)
                            ultimo_error = requests.HTTPError(
                                f"{resp.status_code} para {url}", response=resp)
                            time.sleep(espera)
                            continue
                        resp.raise_for_status()
                    contenido = self._leer_cuerpo_limitado(resp, self._MAX_BYTES_FEED)
                    if devolver_meta:
                        # Feed MUDADO: si algún salto del historial fue permanente (301/308)
                        # y la URL final difiere, se propone la nueva ubicación.
                        url_mudanza = ""
                        try:
                            if (any(h.status_code in (301, 308) for h in (resp.history or []))
                                    and resp.url and resp.url != url):
                                url_mudanza = resp.url
                        except Exception:
                            pass
                        return (contenido, resp.headers.get("ETag", "") or "",
                                resp.headers.get("Last-Modified", "") or "", False, url_mudanza)
                    return contenido
            except requests.HTTPError:
                raise  # permanente (o agotados los reintentos del transitorio): no repetir
            except ValueError:
                raise  # cuerpo demasiado grande: reintentarlo traería lo mismo
            except Exception as e:
                # Errores de CONEXIÓN (timeout, reset...): sí se reintentan.
                ultimo_error = e
                if intento < intentos - 1:
                    time.sleep(0.5 * (2 ** intento) + random.uniform(0, 0.3))
        raise ultimo_error if ultimo_error else RuntimeError("Descarga fallida")

    # --- CATEGORÍAS (por cuenta; auditoría RSS-01) ---
    # --- ETIQUETAS (muchos-a-muchos; auditoría, prioridad B) ---
    # Un feed puede pertenecer a UNA carpeta (física) y tener VARIAS etiquetas; los
    # artículos reciben etiquetas por reglas o a mano. Las etiquetas son por cuenta.
    def crear_etiqueta(self, account_id: int, name: str) -> int:
        """Crea (o devuelve) una etiqueta por su nombre dentro de una cuenta."""
        name = (name or "").strip()
        if not name:
            return 0
        row = db.query_one("SELECT id FROM rss_tags WHERE account_id = ? AND name = ?",
                           (account_id, name))
        if row:
            return row[0]
        try:
            return db.execute_insert(
                "INSERT INTO rss_tags (account_id, name) VALUES (?, ?)", (account_id, name)) or 0
        except Exception as e:
            log.error(f"Error al crear la etiqueta '{name}': {e}")
            row = db.query_one("SELECT id FROM rss_tags WHERE account_id = ? AND name = ?",
                               (account_id, name))
            return row[0] if row else 0

    def borrar_etiqueta(self, tag_id: int):
        """Borra una etiqueta y sus relaciones (los feeds y artículos no se tocan)."""
        db.execute("DELETE FROM rss_tags WHERE id = ?", (tag_id,))

    def obtener_etiquetas(self, account_id: int) -> List[Dict[str, Any]]:
        """Etiquetas de la cuenta con cuántos feeds y artículos etiquetan."""
        try:
            filas = db.query("""
                SELECT t.id, t.name,
                    (SELECT COUNT(*) FROM rss_feed_tags ft WHERE ft.tag_id = t.id),
                    (SELECT COUNT(*) FROM rss_article_tags at WHERE at.tag_id = t.id)
                FROM rss_tags t WHERE t.account_id = ? ORDER BY t.position, t.name
            """, (account_id,))
        except Exception:
            return []
        return [{"id": r[0], "name": r[1], "feeds": r[2], "articulos": r[3]} for r in filas]

    def etiquetar_feed(self, tag_id: int, feed_id: int, poner: bool = True):
        if poner:
            db.execute("INSERT OR IGNORE INTO rss_feed_tags (tag_id, feed_id) VALUES (?, ?)",
                       (tag_id, feed_id))
        else:
            db.execute("DELETE FROM rss_feed_tags WHERE tag_id = ? AND feed_id = ?",
                       (tag_id, feed_id))

    def etiquetar_articulo(self, tag_id: int, article_id: str, poner: bool = True):
        if poner:
            db.execute("INSERT OR IGNORE INTO rss_article_tags (tag_id, article_id) VALUES (?, ?)",
                       (tag_id, article_id))
        else:
            db.execute("DELETE FROM rss_article_tags WHERE tag_id = ? AND article_id = ?",
                       (tag_id, article_id))

    def obtener_etiquetas_de_feed(self, feed_id: int) -> List[int]:
        try:
            return [r[0] for r in db.query(
                "SELECT tag_id FROM rss_feed_tags WHERE feed_id = ?", (feed_id,))]
        except Exception:
            return []

    def obtener_etiquetas_de_articulo(self, article_id: str) -> List[int]:
        try:
            return [r[0] for r in db.query(
                "SELECT tag_id FROM rss_article_tags WHERE article_id = ?", (article_id,))]
        except Exception:
            return []

    def agregar_categoria(self, account_id: int, name: str,
                          parent_id: Optional[int] = None) -> int:
        """Añade una categoría A UNA CUENTA y devuelve su ID (o el de la existente con
        ese nombre en el mismo padre). Las categorías nunca se comparten entre cuentas."""
        # SELECT antes de INSERT: el UNIQUE de la tabla no cubre parent_id NULL
        # (en SQLite dos NULL no chocan), así que la unicidad en la raíz va por código.
        row = db.query_one(
            "SELECT id FROM rss_categories WHERE account_id = ? AND parent_id IS ? AND name = ?",
            (account_id, parent_id, name))
        if row:
            return row[0]
        try:
            return db.execute_insert(
                "INSERT INTO rss_categories (account_id, name, parent_id) VALUES (?, ?, ?)",
                (account_id, name, parent_id)) or 0
        except Exception as e:
            log.error(f"Error al agregar categoría RSS '{name}': {e}")
            row = db.query_one(
                "SELECT id FROM rss_categories WHERE account_id = ? AND parent_id IS ? AND name = ?",
                (account_id, parent_id, name))
            return row[0] if row else 0

    def obtener_categorias(self, account_id: int) -> List[Dict[str, Any]]:
        """Recupera las categorías DE UNA CUENTA (con su padre, para jerarquías)."""
        sql = ("SELECT id, name, parent_id FROM rss_categories "
               "WHERE account_id = ? ORDER BY position, name")
        cats = []
        try:
            rows = db.query(sql, (account_id,))
            for r in rows:
                cats.append({"id": r[0], "name": r[1], "parent_id": r[2]})
        except Exception as e:
            log.error(f"Error al obtener categorías RSS: {e}")
        return cats

    def categoria_crearia_ciclo(self, category_id: int, nuevo_padre: Optional[int]) -> bool:
        """True si colgar category_id de nuevo_padre crearía un CICLO en la jerarquía
        (el nuevo padre es la propia categoría o uno de sus descendientes)."""
        visto = set()
        actual = nuevo_padre
        while actual is not None and actual not in visto:
            if actual == category_id:
                return True
            visto.add(actual)
            r = db.query_one("SELECT parent_id FROM rss_categories WHERE id = ?", (actual,))
            actual = r[0] if r else None
        return actual is not None  # padre ya en un ciclo previo: tampoco permitirlo

    # --- FEEDS ---
    def buscar_feed_por_url(self, account_id: int, url: str):
        """Busca un feed de la cuenta por URL NORMALIZADA (auditoría RSS-21): variantes con
        barra final, mayúsculas del host o parámetros de seguimiento no duplican la
        suscripción. Devuelve (id, title) o None."""
        from app.core.url_utils import normalizar_url
        return db.query_one(
            "SELECT id, title FROM rss_feeds WHERE account_id = ? "
            "AND (url = ? OR url_normalized = ?)",
            (account_id, url, normalizar_url(url)))

    def agregar_feed(self, account_id: int, url: str, title: str, category_id: Optional[int] = None,
                     feed_kind: str = "rss") -> int:
        """Registra un nuevo feed RSS en la base de datos."""
        from app.core.url_utils import normalizar_url
        sql = """
        INSERT INTO rss_feeds (account_id, url, url_normalized, title, category_id, active, feed_kind)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """
        try:
            fid = db.execute_insert(sql, (account_id, url, normalizar_url(url), title,
                                          category_id, feed_kind)) or 0
            log.info(f"Feed registrado ({feed_kind}): '{title}' ({url})")
            return fid
        except Exception as e:
            log.error(f"Error al agregar feed RSS: {e}")
            raise

    def _detectar_url_feed(self, url: str) -> str:
        """
        Si la URL apunta a una página web normal (no a un feed), intenta
        descubrir la dirección del feed RSS/Atom declarada en su HTML.
        Devuelve la URL del feed encontrada o la URL original si no detecta nada.
        """
        try:
            from app.core.net import http_session
            from bs4 import BeautifulSoup
            from urllib.parse import urljoin

            resp = http_session().get(url, timeout=15, headers={"User-Agent": "SocialRSS Suite"})
            content_type = resp.headers.get("Content-Type", "").lower()

            # Si el servidor ya devuelve XML/RSS, la propia URL es el feed
            if "xml" in content_type or "rss" in content_type or "atom" in content_type:
                return url

            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("link"):
                rel = " ".join(link.get("rel", [])).lower()
                tipo = (link.get("type") or "").lower()
                href = link.get("href")
                if href and rel == "alternate" and ("rss" in tipo or "atom" in tipo or "xml" in tipo):
                    return urljoin(url, href)
        except Exception as e:
            log.debug(f"No se pudo autodetectar feed desde {url}: {e}")
        return url

    def _resolver_feed_youtube(self, url: str) -> str:
        """Convierte una URL/ID de canal de YouTube en la URL de su feed RSS.

        Acepta: un ID de canal (UC...), una URL /channel/UC..., o cualquier URL de
        canal (@handle, /c/, /user/), de la que se extrae el channel_id del HTML."""
        import re
        from app.core.net import http_session
        u = (url or "").strip()
        # ID de canal directo.
        if re.fullmatch(r"UC[\w-]{20,}", u):
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={u}"
        if not u.startswith("http"):
            u = "https://www.youtube.com/" + u.lstrip("/")
        # /channel/UC...
        m = re.search(r"/channel/(UC[\w-]+)", u)
        if m:
            return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"
        # Cualquier otra URL de canal: descargar el HTML y extraer el channel_id.
        try:
            html = http_session().get(u, timeout=15,
                                      headers={"User-Agent": "Mozilla/5.0 SocialRSS"}).text
            m = (re.search(r'"channelId":"(UC[\w-]+)"', html)
                 or re.search(r'"externalId":"(UC[\w-]+)"', html)
                 or re.search(r'channel_id=(UC[\w-]+)', html))
            if m:
                return f"https://www.youtube.com/feeds/videos.xml?channel_id={m.group(1)}"
        except Exception as e:
            log.debug(f"No se pudo resolver el canal de YouTube {url}: {e}")
        raise ValueError("No se pudo identificar el canal de YouTube. Pega la URL del canal o su ID (UC...).")

    def añadir_canal_youtube(self, account_id: int, url: str, category_id: Optional[int] = None,
                             custom_title: Optional[str] = None, ctx=None) -> str:
        """Suscribe a un canal de YouTube como feed (sus vídeos se escuchan como audio)."""
        feed_url = self._resolver_feed_youtube(url)
        return self.añadir_feed(account_id, feed_url, category_id=category_id,
                                custom_title=custom_title, ctx=ctx, feed_kind="youtube")

    def analizar_web(self, url: str, user_agent: str = "") -> dict:
        """Analiza una página web SIN RSS y propone los artículos detectados (para previsualizar
        antes de crear el feed). Devuelve {titulo_pagina, items:[{title,link,summary}], selector}."""
        from app.services.rss import rss_scraper
        url = (url or "").strip()
        if not url:
            raise ValueError("Escribe la dirección de la web.")
        if not url.startswith("http"):
            url = "https://" + url
        return rss_scraper.analizar(url, user_agent=user_agent)

    def crear_feed_web(self, account_id: int, url: str, title: str,
                       category_id: Optional[int] = None, selector: str = "",
                       scraper_module: str = "", ctx=None) -> int:
        """Crea un feed de tipo 'web' (web scraper) a partir de una página sin RSS. Guarda el
        selector detectado/elegido (o el módulo de scraper personalizado) y descarga ya sus
        artículos iniciales. Devuelve el feed_id."""
        import json
        url = (url or "").strip()
        if not url.startswith("http"):
            url = "https://" + url
        existe = self.buscar_feed_por_url(account_id, url)
        if existe:
            return existe[0]
        feed_id = self.agregar_feed(account_id, url, title or url, category_id, feed_kind="web")
        try:
            db.execute("UPDATE rss_feeds SET scrape_config = ? WHERE id = ?",
                       (json.dumps({"selector": selector or "", "scraper_module": scraper_module or ""}), feed_id))
        except Exception as e:
            log.debug(f"No se pudo guardar scrape_config del feed web {feed_id}: {e}")
        try:
            self._actualizar_feed_job(ctx, feed_id)
        except Exception as e:
            log.warning(f"Feed web creado pero falló la descarga inicial de {title}: {e}")
        return feed_id

    def añadir_feed(
        self,
        account_id: int,
        url: str,
        category_id: Optional[int] = None,
        custom_title: Optional[str] = None,
        ctx=None,
        feed_kind: str = "rss"
    ) -> str:
        """
        Añade un feed validando la URL, autodetectando el feed si se pasa una
        web, obteniendo el título real y descargando los primeros artículos.
        Devuelve el título final del feed registrado.
        """
        url = (url or "").strip()
        if not url:
            raise ValueError("La URL del feed no puede estar vacía.")
        if not url.startswith("http"):
            url = "https://" + url

        # 1. Comprobar si ya existe en la misma cuenta (por URL NORMALIZADA: variantes con
        # barra final o parámetros de seguimiento no duplican la suscripción; RSS-21).
        existe = self.buscar_feed_por_url(account_id, url)
        if existe:
            return existe[1]

        # 2. DESCUBRIR el feed (rss_descubrir): redes y servicios conocidos, la propia
        # dirección, los feeds que anuncia la página y las rutas habituales. Cada candidato se
        # descarga con la identificación de la app y, si un cortafuegos la rechaza, como
        # navegador. Antes feedparser descargaba la dirección por su cuenta y, sin <link
        # rel="alternate">, no se encontraba nada.
        candidatos = self.descubrir_feeds(url)
        if not candidatos:
            raise ValueError("La dirección no es un feed RSS válido o no se pudo descargar.")
        elegido = candidatos[0]
        url = elegido["url"]
        existe = self.buscar_feed_por_url(account_id, url)
        if existe:
            return existe[1]

        # 4. Determinar el título final
        title = custom_title or elegido.get("titulo") or url

        # 5. Registrar el feed y descargar sus artículos iniciales
        feed_id = self.agregar_feed(account_id, url, title, category_id, feed_kind=feed_kind)
        # Alta también en el servidor de sincronización, si está activado (rss_sync_subs.py).
        self.sus_alta_feed(feed_id)
        if elegido.get("navegador"):
            # El servidor solo responde a navegadores: recordarlo para este feed.
            db.execute("UPDATE rss_feeds SET custom_user_agent = ? WHERE id = ?",
                       (self.UA_NAVEGADOR, feed_id))
        try:
            self._actualizar_feed_job(ctx, feed_id)
        except Exception as e:
            log.warning(f"Feed añadido pero falló la descarga inicial de {title}: {e}")

        # 6. Si no se forzó el tipo y el feed trae episodios de audio, marcarlo
        # automáticamente como podcast.
        if feed_kind == "rss":
            try:
                fila = db.query_one(
                    "SELECT COUNT(*) FROM rss_articles WHERE feed_id = ? AND is_podcast = 1", (feed_id,))
                if fila and fila[0] > 0:
                    db.execute("UPDATE rss_feeds SET feed_kind = 'podcast' WHERE id = ?", (feed_id,))
            except Exception as e:
                log.debug(f"No se pudo autodetectar podcast en {title}: {e}")

        return title

    def obtener_feeds(self, account_id: int, category_id: Optional[int] = None) -> List[RSSFeed]:
        """Devuelve los feeds de una cuenta, con los fijados (pinned) arriba."""
        cols = ("id, account_id, url, title, category_id, update_interval, "
                "active, last_error, pinned, paused")
        # Fijados primero, luego por posición/título.
        orden = " ORDER BY pinned DESC, position, title"
        if category_id is not None:
            sql = f"SELECT {cols} FROM rss_feeds WHERE account_id = ? AND category_id = ?" + orden
            params = (account_id, category_id)
        else:
            sql = f"SELECT {cols} FROM rss_feeds WHERE account_id = ?" + orden
            params = (account_id,)

        feeds = []
        try:
            rows = db.query(sql, params)
            for r in rows:
                feeds.append(RSSFeed(
                    id=r[0], account_id=r[1], url=r[2], title=r[3],
                    category_id=r[4], update_interval=r[5], active=bool(r[6]),
                    last_error=r[7], pinned=bool(r[8]), paused=bool(r[9])
                ))
        except Exception as e:
            log.error(f"Error al obtener feeds: {e}")
        return feeds

    def validar_feed(self, url: str) -> Dict[str, Any]:
        """
        Comprueba una URL ANTES de añadirla: detecta el feed si es una web,
        verifica que es un feed válido y devuelve título y nº de artículos.
        Devuelve dict: {valido, url, title, entries, error}.
        """
        url = (url or "").strip()
        if not url:
            return {"valido": False, "error": "La URL está vacía."}
        if not url.startswith("http"):
            url = "https://" + url
        try:
            candidatos = self.descubrir_feeds(url)
            if not candidatos:
                return {"valido": False, "url": url,
                        "error": "No es un feed RSS/Atom/JSON válido o no se pudo descargar."}
            c = candidatos[0]
            return {
                "valido": True, "url": c["url"],
                "title": c.get("titulo") or c["url"],
                "entries": c.get("n", 0),
                "error": None,
                # Otros feeds encontrados a partir de la misma dirección (para elegir).
                "otros": [{"url": o["url"], "title": o.get("titulo") or o["url"],
                           "entries": o.get("n", 0)} for o in candidatos[1:]],
                "navegador": bool(c.get("navegador")),
            }
        except Exception as e:
            return {"valido": False, "url": url, "error": str(e)}

    # --- CARPETAS INTELIGENTES (auditoría, prioridad A) ---
    def obtener_carpetas_inteligentes(self, account_id: int) -> List[Dict[str, Any]]:
        """Carpetas inteligentes de la cuenta: consultas guardadas ESTRUCTURADAS (no SQL
        libre) que se abren como buffers del árbol."""
        import json
        salida = []
        try:
            rows = db.query("SELECT id, name, criteria_json FROM rss_smart_folders "
                            "WHERE account_id = ? ORDER BY position, name", (account_id,))
        except Exception:
            return salida
        for r in rows:
            try:
                criterios = json.loads(r[2]) or {}
            except Exception:
                criterios = {}
            salida.append({"id": r[0], "name": r[1], "criteria": criterios})
        return salida

    def crear_carpeta_inteligente(self, account_id: int, name: str, criteria: dict) -> int:
        import json
        from datetime import datetime as _dt
        return db.execute_insert(
            "INSERT INTO rss_smart_folders (account_id, name, criteria_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (account_id, name, json.dumps({"v": 1, **(criteria or {})}, ensure_ascii=False),
             _dt.now().isoformat()))

    def borrar_carpeta_inteligente(self, folder_id: int):
        db.execute("DELETE FROM rss_smart_folders WHERE id = ?", (folder_id,))

    def editar_carpeta_inteligente(self, folder_id: int, name: str, criteria: dict):
        import json
        db.execute("UPDATE rss_smart_folders SET name = ?, criteria_json = ? WHERE id = ?",
                   (name, json.dumps({"v": 1, **(criteria or {})}, ensure_ascii=False), folder_id))

    @staticmethod
    def where_carpeta_inteligente(criteria: dict):
        """Convierte los criterios ESTRUCTURADOS de una carpeta inteligente en un trozo de
        WHERE parametrizado sobre rss_articles (alias a) / rss_feeds (alias f). Solo se
        aceptan claves conocidas: nada de SQL arbitrario. Devuelve (sql_extra, params)."""
        sql, params = [], []
        c = criteria or {}
        if c.get("read") in (0, 1):
            sql.append("COALESCE(a.read,0) = ?")
            params.append(int(c["read"]))
        if c.get("favorite"):
            sql.append("COALESCE(a.favorite,0) = 1")
        if c.get("read_later"):
            sql.append("COALESCE(a.read_later,0) = 1")
        if c.get("has_media"):
            sql.append("COALESCE(a.has_media,0) = 1")
        if c.get("feed_id"):
            sql.append("a.feed_id = ?")
            params.append(int(c["feed_id"]))
        if c.get("category_id"):
            sql.append("f.category_id = ?")
            params.append(int(c["category_id"]))
        if c.get("days"):
            sql.append("datetime(a.published_at) >= datetime('now', 'localtime', ?)")
            params.append(f"-{int(c['days'])} days")
        if (c.get("text") or "").strip():
            patron = f"%{c['text'].strip()}%"
            sql.append("(a.title LIKE ? OR a.content LIKE ?)")
            params.extend([patron, patron])
        # Criterios añadidos en la fase 4 del informe de BlindRSS: texto que NO debe aparecer,
        # autor, dirección del artículo y "corregidos en origen" (tienen versiones anteriores).
        if (c.get("text_not") or "").strip():
            patron = f"%{c['text_not'].strip()}%"
            sql.append("(COALESCE(a.title,'') NOT LIKE ? AND COALESCE(a.content,'') NOT LIKE ?)")
            params.extend([patron, patron])
        if (c.get("author") or "").strip():
            sql.append("COALESCE(a.author,'') LIKE ?")
            params.append(f"%{c['author'].strip()}%")
        if (c.get("link") or "").strip():
            sql.append("COALESCE(a.link,'') LIKE ?")
            params.append(f"%{c['link'].strip()}%")
        if c.get("updated"):
            from .rss_versiones import asegurar_tabla
            asegurar_tabla()
            sql.append("a.id IN (SELECT article_id FROM rss_article_versions)")
        return ((" AND " + " AND ".join(sql)) if sql else "", params)

    def obtener_salud_feeds(self, account_id: int) -> List[Dict[str, Any]]:
        """Estado de salud de TODOS los feeds de la cuenta (centro de salud, prioridad A
        de la auditoría): último intento/éxito, fallos seguidos, estado HTTP, error y
        próxima comprobación programada. Los problemáticos primero."""
        filas = db.query(
            "SELECT id, title, url, COALESCE(paused,0), COALESCE(active,1), "
            "COALESCE(fail_count,0), last_status, COALESCE(last_error,''), "
            "COALESCE(last_attempt_at,''), COALESCE(last_success_at,''), "
            "COALESCE(next_update_at,''), COALESCE(feed_kind,'rss') "
            "FROM rss_feeds WHERE account_id = ? "
            "ORDER BY COALESCE(paused,0) DESC, COALESCE(fail_count,0) DESC, title",
            (account_id,))
        salida = []
        for r in filas:
            if r[3]:
                estado = "pausado"
            elif r[5] > 0 or r[7]:
                estado = "fallando"
            else:
                estado = "ok"
            salida.append({
                "id": r[0], "title": r[1], "url": r[2], "paused": bool(r[3]),
                "active": bool(r[4]), "fail_count": r[5], "last_status": r[6],
                "last_error": r[7], "last_attempt_at": r[8], "last_success_at": r[9],
                "next_update_at": r[10], "feed_kind": r[11], "estado": estado,
            })
        return salida

    def reintentar_feeds_rotos_async(self, account_id: int,
                                     callback: Optional[Callable] = None):
        """Fuerza AHORA la actualización de todos los feeds con fallos (incluidos los
        pausados por un 410, que se reactivan): borra su programación y su contador de
        fallos y lanza una pasada solo con ellos. callback(n_reintentados, err)."""
        rotos = [r[0] for r in db.query(
            "SELECT id FROM rss_feeds WHERE account_id = ? "
            "AND (COALESCE(fail_count,0) > 0 OR COALESCE(paused,0) = 1 "
            "     OR COALESCE(last_error,'') != '')", (account_id,))]
        if not rotos:
            if callback:
                callback(0, None)
            return
        with db.transaccion():
            for fid in rotos:
                db.execute("UPDATE rss_feeds SET paused = 0, fail_count = 0, "
                           "next_update_at = NULL, last_error = NULL WHERE id = ?", (fid,))

        def _bg(ctx):
            total = 0
            for i, fid in enumerate(rotos):
                if ctx.is_cancelled():
                    break
                try:
                    total += self._actualizar_feed_job(ctx, fid) or 0
                except Exception as e:
                    log.debug(f"Reintento del feed {fid} falló: {e}")
                ctx.report_progress(int(((i + 1) / len(rotos)) * 100), "")
            return len(rotos)

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def set_feed_pinned(self, feed_id: int, pinned: bool):
        """Fija o desfija un feed (los fijados aparecen arriba)."""
        try:
            db.execute("UPDATE rss_feeds SET pinned = ? WHERE id = ?", (1 if pinned else 0, feed_id))
        except Exception as e:
            log.error(f"Error al fijar feed {feed_id}: {e}")

    def set_feed_paused(self, feed_id: int, paused: bool):
        """Pone en pausa o reactiva un feed (los pausados no se actualizan)."""
        try:
            db.execute("UPDATE rss_feeds SET paused = ? WHERE id = ?", (1 if paused else 0, feed_id))
        except Exception as e:
            log.error(f"Error al pausar feed {feed_id}: {e}")

    # --- ACTUALIZACIÓN ASÍNCRONA ---
    def actualizar_feed_async(self, feed_id: int, callback: Optional[Callable[[int, Optional[Exception]], None]] = None):
        """Dispara la actualización de un feed específico en segundo plano."""
        def _bg_update(ctx):
            return self._actualizar_feed_job(ctx, feed_id)
            
        task_manager.start_task(
            _bg_update,
            callback=callback,
            success_sound="mastodon_new_status" if config.get("rss", "notifications_enabled", True) else None,
            pass_context=True
        )

    def _feeds_vencidos(self, account_id: int) -> set:
        """IDs de los feeds cuya PRÓXIMA actualización programada ya venció (scheduler por
        vencimiento, auditoría RSS-05). Un feed sin next_update_at (recién creado o sin
        migrar) cuenta como vencido. La fecha es ISO local, comparable como texto."""
        ahora = datetime.now().isoformat()
        try:
            return {r[0] for r in db.query(
                "SELECT id FROM rss_feeds WHERE account_id = ? "
                "AND (next_update_at IS NULL OR next_update_at = '' OR next_update_at <= ?)",
                (account_id, ahora))}
        except Exception:
            return set()

    def actualizar_todos_async(self, account_id: int, callback: Optional[Callable[[int, Optional[Exception]], None]] = None,
                               max_par_override: Optional[int] = None, unique_key: Optional[str] = None,
                               solo_vencidos: bool = False):
        """Actualiza en PARALELO los feeds activos (no pausados) de la cuenta.

        max_par_override limita las descargas simultáneas SOLO para esta llamada (sin
        cambiar la preferencia del usuario). Se usa en el arranque para suavizar el
        pico: menos feeds a la vez aunque el usuario tenga configurado un número alto.

        unique_key: si se indica y ya hay una actualización con esa clave en curso, NO se lanza
        otra (evita que sondeos de fondo solapados repitan el trabajo). El refresco MANUAL (F5) no
        la usa, para conservar siempre su aviso de fin.

        solo_vencidos: True en los sondeos AUTOMÁTICOS: solo se consultan los feeds cuya
        próxima actualización ya venció (intervalo propio del feed, retroceso por errores).
        El refresco MANUAL va con False y se salta la programación (auditoría RSS-05).
        """
        def _bg_update_all(ctx):
            from concurrent.futures import ThreadPoolExecutor, as_completed
            # Solo feeds activos y NO en pausa.
            feeds = [f for f in self.obtener_feeds(account_id) if f.active and not f.paused]
            if solo_vencidos and feeds:
                vencidos = self._feeds_vencidos(account_id)
                feeds = [f for f in feeds if f.id in vencidos]
            if not feeds:
                return 0
            # Nº de descargas simultáneas configurable (1-50).
            try:
                max_par = int(config.get("rss", "max_parallel_updates", 5))
            except Exception:
                max_par = 5
            if max_par_override is not None:
                max_par = min(max_par, max_par_override)
            max_par = max(1, min(max_par, 50, len(feeds)))

            total_nuevos = 0
            completados = 0

            def _job_y_cerrar(feed_id):
                # Estos hilos son EFÍMEROS (el executor se crea y destruye en cada actualización
                # global). Cerramos su conexión de lectura al terminar para que no se acumulen en el
                # registro global hasta cerrar la app (fuga lenta de descriptores/mmap).
                try:
                    return self._actualizar_feed_job(ctx, feed_id)
                finally:
                    db.cerrar_lectura_hilo()

            with ThreadPoolExecutor(max_workers=max_par) as executor:
                futuros = {executor.submit(_job_y_cerrar, f.id): f for f in feeds}
                for fut in as_completed(futuros):
                    feed = futuros[fut]
                    completados += 1
                    if ctx.is_cancelled():
                        break
                    ctx.report_progress(int((completados / len(feeds)) * 100),
                                        f"Actualizado: {feed.title}")
                    try:
                        total_nuevos += fut.result() or 0
                    except Exception as e:
                        # El job (_actualizar_feed_job) ya registra el error con detalle (y evita
                        # repetirlo si el feed sigue fallando igual). Aquí solo a DEBUG para no
                        # duplicar la línea por cada feed roto en cada sondeo.
                        log.debug(f"Fallo al actualizar feed {feed.title}: {e}")
            return total_nuevos

        task_manager.start_task(
            _bg_update_all,
            callback=callback,
            success_sound="mastodon_new_status" if config.get("rss", "notifications_enabled", True) else None,
            pass_context=True,
            unique_key=unique_key
        )

    # --- MANTENIMIENTO DE LA BASE DE DATOS (N.8) ---
    def _recortar_feed(self, feed_id: int):
        """Recorta un feed al máximo de artículos configurado (0 = sin límite),
        borrando los más antiguos pero conservando favoritos y leer-más-tarde."""
        try:
            maximo = int(config.get("rss", "max_articles_per_feed", 0))
        except Exception:
            maximo = 0
        if maximo <= 0:
            return
        try:
            # Se seleccionan primero los ids a borrar para eliminar TAMBIÉN sus filas del
            # índice de búsqueda (rss_articles_fts) en la misma transacción. Antes solo se
            # borraba de rss_articles y el FTS acumulaba huérfanos sin límite: crecía aunque
            # la poda estuviera activa y la búsqueda devolvía menos resultados de los reales
            # (los huérfanos consumían posiciones del LIMIT).
            cond = """feed_id = ? AND favorite = 0 AND COALESCE(read_later, 0) = 0
                AND id NOT IN (
                    SELECT id FROM rss_articles WHERE feed_id = ?
                    ORDER BY published_at DESC LIMIT ?
                )"""
            ids = [r[0] for r in db.query(
                f"SELECT id FROM rss_articles WHERE {cond}", (feed_id, feed_id, maximo))]
            if not ids:
                return
            with db.transaccion():
                db.execute(f"DELETE FROM rss_articles WHERE {cond}", (feed_id, feed_id, maximo))
                for i in range(0, len(ids), 500):
                    lote = ids[i:i + 500]
                    ph = ",".join("?" for _ in lote)
                    db.execute(f"DELETE FROM rss_articles_fts WHERE article_db_id IN ({ph})", tuple(lote))
        except Exception as e:
            log.debug(f"No se pudo recortar el feed {feed_id}: {e}")

    def limpieza_mantenimiento(self, account_id: Optional[int] = None) -> int:
        """Elimina artículos más antiguos que 'keep_articles_days' (0 = nunca),
        preservando favoritos y leer-más-tarde. Devuelve cuántos borró."""
        try:
            dias = int(config.get("rss", "keep_articles_days", 0))
        except Exception:
            dias = 0
        if dias <= 0:
            return 0
        from datetime import timedelta
        limite = (datetime.now() - timedelta(days=dias)).isoformat()
        try:
            cond = "favorite = 0 AND COALESCE(read_later, 0) = 0 AND published_at < ?"
            params = [limite]
            if account_id is not None:
                cond += " AND feed_id IN (SELECT id FROM rss_feeds WHERE account_id = ?)"
                params.append(account_id)
            # Ids primero, para borrar TAMBIÉN del índice FTS (ver _recortar_feed).
            ids = [r[0] for r in db.query(
                f"SELECT id FROM rss_articles WHERE {cond}", tuple(params))]
            if not ids:
                return 0
            with db.transaccion():
                db.execute(f"DELETE FROM rss_articles WHERE {cond}", tuple(params))
                for i in range(0, len(ids), 500):
                    lote = ids[i:i + 500]
                    ph = ",".join("?" for _ in lote)
                    db.execute(f"DELETE FROM rss_articles_fts WHERE article_db_id IN ({ph})", tuple(lote))
            # Compactar el índice tras una purga grande para recuperar el espacio.
            if len(ids) >= 500:
                try:
                    db.execute("INSERT INTO rss_articles_fts(rss_articles_fts) VALUES('optimize')")
                except Exception:
                    pass
            log.info(f"Limpieza RSS: {len(ids)} artículos antiguos eliminados (y su índice de búsqueda).")
            return len(ids)
        except Exception as e:
            log.error(f"Error en la limpieza de mantenimiento RSS: {e}")
            return 0

    def optimizar_base_datos(self) -> Dict[str, Any]:
        """Comprueba la integridad y optimiza la base de datos (VACUUM).
        Devuelve {ok, integridad, error}."""
        resultado = {"ok": False, "integridad": "", "error": None}
        try:
            row = db.query_one("PRAGMA integrity_check")
            resultado["integridad"] = row[0] if row else "desconocido"
            db.execute("VACUUM")
            try:
                db.execute("INSERT INTO rss_articles_fts(rss_articles_fts) VALUES('optimize')")
            except Exception:
                pass
            resultado["ok"] = True
        except Exception as e:
            resultado["error"] = str(e)
            log.error(f"Error al optimizar la base de datos: {e}")
        return resultado
