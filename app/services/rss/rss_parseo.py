# -*- coding: utf-8 -*-
"""Job de actualización de feeds y evaluación de alertas.

Mixin de RSSService (parte de la refactorización modular).
"""
import logging
from datetime import datetime
import feedparser

from app.core.database import db
from app.core.models import RSSArticle
from .rss_rules import RSSRulesEngine
from app.core.content_filters import ContentFilterEngine


log = logging.getLogger(__name__)


def _bajar_prioridad_hilo_actual():
    """Baja la prioridad del HILO actual a "inferior a la normal" en Windows. El parseo de feeds
    (feedparser) es Python puro (CPU) y, con varios hilos a la vez, compite con el hilo de la
    interfaz por el GIL y provoca tirones (p. ej. al navegar por Preferencias durante el sondeo de
    arranque). Con menor prioridad, el planificador de Windows favorece al hilo de la interfaz cuando
    el GIL queda libre. Es un ajuste suave y sin riesgo (si falla, no pasa nada)."""
    try:
        import ctypes
        # THREAD_PRIORITY_BELOW_NORMAL = -1
        k32 = ctypes.windll.kernel32
        k32.SetThreadPriority(k32.GetCurrentThread(), -1)
    except Exception:
        pass


class _ResultadoWeb:
    """Objeto mínimo compatible con feedparser (.entries, .feed, .bozo) para los feeds de tipo
    'web' (web scraper), para reutilizar el mismo bucle de procesamiento de entradas."""
    def __init__(self, entries, feed):
        self.entries = entries
        self.feed = feed
        self.bozo = False
        self.bozo_exception = None


def _parsear_json_feed(contenido):
    """Parsea un JSON FEED 1.0/1.1 (auditoría, prioridad B) y lo devuelve como objeto
    compatible con el bucle de entradas (pasa por el mismo contrato normalizado que RSS y
    Atom). Devuelve None si el contenido no es un JSON Feed válido."""
    import json
    try:
        if isinstance(contenido, bytes):
            texto = contenido.decode("utf-8-sig", "replace")
        else:
            texto = str(contenido)
        datos = json.loads(texto)
    except Exception:
        return None
    if not isinstance(datos, dict):
        return None
    version = str(datos.get("version") or "")
    if not version.startswith("https://jsonfeed.org/version/1"):
        return None
    entradas = []
    for it in (datos.get("items") or []):
        if not isinstance(it, dict):
            continue
        link = it.get("url") or it.get("external_url") or ""
        # Autor: 1.1 usa la lista 'authors'; 1.0, el objeto 'author'.
        autor = ""
        autores = it.get("authors") or ([it["author"]] if isinstance(it.get("author"), dict) else [])
        if autores and isinstance(autores[0], dict):
            autor = autores[0].get("name") or ""
        # Adjuntos -> enclosures con el vocabulario que espera el normalizador.
        encls = []
        for a in (it.get("attachments") or []):
            if isinstance(a, dict) and a.get("url"):
                encls.append({"url": a["url"], "type": a.get("mime_type", "") or "",
                              "length": a.get("size_in_bytes") or 0})
        entradas.append({
            "id": str(it.get("id") or link),
            "link": link,
            "title": it.get("title") or "",
            "author": autor,
            "content": it.get("content_html") or it.get("content_text") or "",
            "summary": it.get("summary") or "",
            # Fechas ISO tal cual: _normalizar_entrada las convierte a struct_time UTC.
            "published": it.get("date_published") or "",
            "updated": it.get("date_modified") or "",
            "enclosures": encls,
        })
    return _ResultadoWeb(entradas, {"title": datos.get("title") or "json"})


def _duracion_a_segundos(valor):
    """Convierte una duración de podcast (itunes:duration) a segundos enteros. Acepta
    'HH:MM:SS', 'MM:SS', o un número de segundos ('3723'). None si no se puede."""
    if valor is None:
        return None
    txt = str(valor).strip()
    if not txt:
        return None
    try:
        if ":" in txt:
            partes = [int(float(p)) for p in txt.split(":")]
            seg = 0
            for p in partes:
                seg = seg * 60 + p
            return seg
        return int(float(txt))
    except (ValueError, TypeError):
        return None


def _construir_podcast_json(entry) -> str:
    """Serializa los metadatos ricos de Podcast 2.0 (capítulos, transcripción, apoyo,
    personas) de una entrada normalizada a un JSON compacto. Cadena vacía si no hay nada.
    Solo guarda url/tipo/rol/idioma —lo consumible por un lector de pantalla— sin inflar."""
    import json as _json
    datos = {}

    def _dict_limpio(d, claves):
        if not isinstance(d, dict):
            return None
        out = {}
        for c in claves:
            v = d.get(c)
            if v:
                out[c] = v
        # feedparser usa 'href' para persons y 'url' para el resto: unificar en 'url'.
        if "href" in out and "url" not in out:
            out["url"] = out.pop("href")
        return out or None

    tr = _dict_limpio(entry.get("podcast_transcript"), ("url", "href", "type", "language"))
    if tr:
        datos["transcript"] = tr
    ch = _dict_limpio(entry.get("podcast_chapters"), ("url", "href", "type"))
    if ch:
        datos["chapters"] = ch
    fu = _dict_limpio(entry.get("podcast_funding"), ("url", "href"))
    if fu:
        datos["funding"] = fu
    pe = _dict_limpio(entry.get("podcast_person"), ("role", "href", "url"))
    if pe:
        datos["person"] = pe
    if not datos:
        return ""
    try:
        return _json.dumps(datos, ensure_ascii=False)
    except Exception:
        return ""


def _iso_a_struct_time(texto):
    """Convierte una fecha ISO-8601 (con o sin zona) a struct_time UTC, el formato que
    entrega feedparser en published_parsed. Devuelve None si no se puede interpretar."""
    if not texto:
        return None
    try:
        from datetime import timezone
        ds = str(texto).strip()
        if ds.endswith("Z"):
            ds = ds[:-1] + "+00:00"
        dt = datetime.fromisoformat(ds)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).timetuple()
    except Exception:
        return None


def _normalizar_entrada(entry) -> dict:
    """CONTRATO ÚNICO DE PARSEO (auditoría RSS-07): convierte una entrada de CUALQUIER
    parser (feedparser o fastfeedparser) a un dict con las claves que espera el bucle de
    procesamiento (las de feedparser). Corrige las dos incompatibilidades medidas de
    fastfeedparser: fechas como texto ISO sin *_parsed (se perdía la fecha real y se
    sustituía por "ahora") y enclosures con 'url' en vez de 'href' (los episodios de
    podcast no se detectaban)."""
    def _g(clave, defecto=None):
        try:
            v = entry.get(clave)
        except AttributeError:
            v = getattr(entry, clave, None)
        return v if v is not None else defecto

    e = {
        "title": _g("title", ""), "link": _g("link", ""), "id": _g("id") or _g("guid") or "",
        "author": _g("author", ""), "summary": _g("summary"), "description": _g("description"),
    }
    # Contenido RESUELTO a texto/HTML: feedparser lo da como lista de dicts con .value;
    # fastfeedparser puede dar la lista con dicts planos o directamente una cadena.
    contenido = ""
    bruto = _g("content")
    if bruto:
        if isinstance(bruto, str):
            contenido = bruto
        else:
            try:
                c0 = bruto[0]
                contenido = (c0.get("value") if isinstance(c0, dict)
                             else getattr(c0, "value", "")) or ""
            except Exception:
                contenido = ""
    if not contenido:
        contenido = _g("summary") or _g("description") or ""
    e["contenido_resuelto"] = contenido
    # Fechas: preferir el struct_time del parser; si el parser dio TEXTO (fastfeedparser
    # entrega ISO en 'published'/'updated'), convertirlo aquí para no perder la fecha real.
    for base in ("published", "updated", "created"):
        st = _g(base + "_parsed")
        if not st:
            st = _iso_a_struct_time(_g(base))
        if st:
            e[base + "_parsed"] = st
    # Enclosures: aceptar href (feedparser) o url (fastfeedparser / RSS crudo).
    encls = []
    for enc in (_g("enclosures") or []):
        try:
            href = enc.get("href") or enc.get("url") or ""
            tipo = enc.get("type", "") or ""
            length = enc.get("length") or 0
        except AttributeError:
            href = getattr(enc, "href", "") or getattr(enc, "url", "") or ""
            tipo = getattr(enc, "type", "") or ""
            length = getattr(enc, "length", 0) or 0
        if href:
            encls.append({"href": href, "type": tipo, "length": length})
    # media:content (Media RSS) como respaldo si no hay enclosure clásico.
    if not encls:
        for mc in (_g("media_content") or []):
            try:
                href = mc.get("url") or ""
                tipo = mc.get("type", "") or mc.get("medium", "") or ""
            except AttributeError:
                href, tipo = "", ""
            if href:
                encls.append({"href": href, "type": tipo, "length": 0})
    e["enclosures"] = encls
    # PODCAST 2.0 / iTunes (feedparser los mapea de forma nativa; fastfeedparser no, así
    # que solo estarán presentes cuando el parser principal —feedparser— procesó el feed).
    # Campos ligeros para la lista/detalle:
    e["itunes_duration"] = _g("itunes_duration")
    e["itunes_explicit"] = _g("itunes_explicit")
    e["itunes_season"] = _g("itunes_season")
    e["itunes_episode"] = _g("itunes_episode")
    img = _g("image")
    if isinstance(img, dict):
        e["episode_image"] = img.get("href") or img.get("url") or ""
    elif isinstance(img, str):
        e["episode_image"] = img
    # Metadatos ricos (capítulos, transcripción, apoyo, personas): dicts que feedparser
    # entrega como {'url'/'href', 'type', 'role', 'language'...}. Se guardan tal cual para
    # serializarlos a podcast_json; nulos si el feed no es Podcast 2.0.
    e["podcast_transcript"] = _g("podcast_transcript")
    e["podcast_chapters"] = _g("podcast_chapters")
    e["podcast_funding"] = _g("podcast_funding")
    e["podcast_person"] = _g("podcast_person")
    return e


class _ParseoMixin:
    def _parsear_web(self, url: str, scrape_config: str, user_agent: str) -> "_ResultadoWeb":
        """Analiza una web (feed de tipo 'web') y devuelve sus 'entradas' como si fuera un feed."""
        import json
        from app.services.rss import rss_scraper
        selector = ""
        scraper_module = ""
        if scrape_config:
            try:
                cfg = json.loads(scrape_config) or {}
                selector = cfg.get("selector", "") or ""
                scraper_module = cfg.get("scraper_module", "") or ""
            except Exception:
                pass
        # Si el feed está asociado a un MÓDULO de scraper personalizado, usarlo; si falla (p. ej. el
        # complemento está desactivado), recurrir al detector automático.
        items = None
        if scraper_module:
            try:
                items = rss_scraper.scrapear_con_modulo(scraper_module, url)
            except Exception as e:
                log.debug(f"Scraper de sitio '{scraper_module}' no disponible: {e}")
                items = None
        if items is None:
            items = rss_scraper.scrapear(url, selector=selector, user_agent=user_agent)
        entries = []
        for it in items:
            link = it.get("link", "") or ""
            entries.append({
                "title": it.get("title", "") or _("Sin título"),
                "link": link, "id": link, "author": "",
                "summary": it.get("summary", "") or "",
            })
        # feed.title truthy para que pase la validación aunque una pasada no traiga elementos.
        return _ResultadoWeb(entries, {"title": "web"})

    def _actualizar_feed_job(self, ctx, feed_id: int) -> int:
        """Método de fondo que descarga y parsea el XML de un feed."""
        # Parsear es CPU puro: baja la prioridad de este hilo para no robarle tiempo a la interfaz.
        _bajar_prioridad_hilo_actual()
        # 1. Obtener datos del feed de base de datos (incluidos ajustes propios)
        row = db.query_one(
            "SELECT url, title, account_id, COALESCE(custom_user_agent,''), "
            "COALESCE(feed_kind,'rss'), COALESCE(scrape_config,''), COALESCE(last_error,''), "
            "COALESCE(http_etag,''), COALESCE(http_modified,''), COALESCE(force_fulltext,0), "
            "COALESCE(import_cutoff,''), COALESCE(custom_interval,0), COALESCE(fail_count,0), "
            "COALESCE(body_hash,'') "
            "FROM rss_feeds WHERE id = ?", (feed_id,))
        if not row:
            return 0
        (url, feed_title, account_id, feed_user_agent, feed_kind, scrape_config, last_error_prev,
         feed_etag, feed_modified, force_fulltext, corte_import, custom_interval,
         fail_count_prev, body_hash_prev) = row
        # Marca de INTENTO (salud del feed, auditoría RSS-11): pase lo que pase queda
        # constancia de cuándo se comprobó por última vez.
        _ahora_iso = datetime.now().isoformat()
        try:
            db.execute("UPDATE rss_feeds SET last_attempt_at = ? WHERE id = ?",
                       (_ahora_iso, feed_id))
        except Exception:
            pass
        # Texto completo por feed: si el usuario lo activó (o la opción global), se descarga el
        # artículo completo cuando el feed solo trae el resumen. Con tope por ciclo para no
        # disparar el tiempo de actualización (cada descarga es una petición extra).
        try:
            from app.core.config_manager import config as _cfg
            quiere_fulltext = bool(force_fulltext) or bool(_cfg.get("rss", "download_fulltext", False))
        except Exception:
            quiere_fulltext = bool(force_fulltext)
        _fulltext_restantes = 15  # máximo de extracciones de texto completo por actualización

        # DEBUG, no INFO: con decenas de feeds sondeados cada pocos minutos, estas trazas rutinarias
        # (inicio/304/fin sin novedades) llenaban el 90% del log de una sesión larga. Solo lo
        # relevante (llegaron artículos nuevos, o hubo un error) sube a INFO/ERROR.
        log.debug(f"Iniciando descarga de feed: {feed_title} ({url})")
        nuevos_articulos = 0

        try:
            # 2. Obtener las entradas. Para un feed de tipo 'web' (web scraper) no hay XML: se
            # analiza la página y se construye un objeto compatible con el bucle de entradas. Para
            # el resto, se descarga y parsea el XML como siempre.
            contenido = None
            _etag_new = _mod_new = ""
            if feed_kind == "web":
                parsed = self._parsear_web(url, scrape_config, feed_user_agent)
            else:
                try:
                    contenido, _etag_new, _mod_new, _no_modificado, _url_mudanza = \
                        self._descargar_con_reintentos(
                            url, user_agent=feed_user_agent, etag=feed_etag,
                            modificado=feed_modified, devolver_meta=True, feed_id=feed_id)
                    # FEED MUDADO (prioridad D): un 301/308 devolvió una URL nueva. Se aplica
                    # solo cuando la MISMA propuesta se repite (moved_to previo == actual),
                    # para no cambiar por un redirect temporal mal configurado.
                    if _url_mudanza and _url_mudanza != url:
                        try:
                            self._procesar_mudanza_feed(feed_id, url, _url_mudanza)
                        except Exception as e:
                            log.debug(f"No se pudo procesar la mudanza de {url}: {e}")
                    if _no_modificado:
                        # 304 No Modificado: el feed NO ha cambiado desde la última vez. Nos saltamos
                        # el parseo (feedparser es lo caro y lo que satura el GIL en las ráfagas) y la
                        # poda. 0 artículos nuevos. Esto es lo que quita los tirones con muchos feeds.
                        self._registrar_exito_feed(feed_id, custom_interval, 0, status=304)
                        log.debug(f"Feed sin cambios (304): {feed_title}")
                        return 0
                    # HASH del cuerpo: bastantes servidores no dan ETag/Last-Modified y
                    # devuelven 200 con el MISMO contenido; parsearlo cada vez era lo que
                    # quemaba la CPU (medido: 233 parseos completos en 2 minutos con el
                    # intervalo de 1 minuto). Si el cuerpo no cambió, se termina aquí.
                    import hashlib as _hlb
                    _body_hash = _hlb.sha1(contenido or b"").hexdigest()
                    if _body_hash and _body_hash == body_hash_prev:
                        self._registrar_exito_feed(feed_id, custom_interval, 0, status=200)
                        log.debug(f"Feed sin cambios (mismo contenido): {feed_title}")
                        return 0
                    # JSON FEED 1.0/1.1 (auditoría, prioridad B): si el cuerpo es JSON, se
                    # parsea directamente (feedparser no lo entiende); si no lo es, XML normal.
                    parsed = None
                    if contenido and contenido.lstrip()[:1] == b"{":
                        parsed = _parsear_json_feed(contenido)
                    if parsed is None:
                        parsed = feedparser.parse(contenido)
                    # OJO: el ETag/Last-Modified NO se guardan aquí, sino DESPUÉS de confirmar que el
                    # contenido parseó bien (más abajo). Si un servidor devuelve 200 con una página de
                    # error (HTML, p. ej. YouTube bajo mucha presión), guardar ya su ETag dejaría el
                    # feed "atascado": en el siguiente sondeo un 304 nos haría saltar el parseo sin
                    # haber traido nunca los articulos reales.
                except Exception as e_desc:
                    # Reintento directo, pero SIEMPRE descargando nosotros con timeout: pasarle la
                    # URL a feedparser hacía que descargara él con urllib SIN timeout, y un servidor
                    # que acepta la conexión pero no responde dejaba este hilo del pool bloqueado
                    # indefinidamente (con varios feeds así, el pool entero se agotaba).
                    log.debug(f"Descarga resiliente falló para {url} ({e_desc}); reintento directo.")
                    import requests as _rq
                    resp_directo = _rq.get(url, timeout=30,
                                           headers=self._cabeceras_feed(feed_user_agent))
                    resp_directo.raise_for_status()
                    contenido = resp_directo.content
                    parsed = feedparser.parse(contenido)

            # Respaldo: si feedparser no sacó NADA (ni entradas ni título), reintentar con
            # fastfeedparser (parser de alto rendimiento alternativo). Es opcional: si no está
            # instalado o también falla, se continúa con el resultado de feedparser.
            if not parsed.entries and not parsed.feed.get("title"):
                try:
                    import fastfeedparser
                    alt = fastfeedparser.parse(contenido or url)
                    if getattr(alt, "entries", None):
                        log.info(f"feedparser no devolvió nada para {url}; se usa fastfeedparser.")
                        parsed = alt
                except Exception as e_fast:
                    log.debug(f"fastfeedparser tampoco pudo con {url}: {e_fast}")

            # Solo fallar si NO se obtuvo nada útil. Muchos feeds marcan "bozo"
            # por avisos inofensivos (codificación declarada distinta, etc.) pero
            # aun así se parsean bien y traen entradas; en ese caso continuamos.
            if not parsed.entries and not parsed.feed.get("title"):
                if parsed.bozo and isinstance(parsed.bozo_exception, Exception):
                    raise parsed.bozo_exception
                raise ValueError("El feed no devolvió ningún artículo (¿URL incorrecta o feed vacío?).")

            # El contenido parseó bien: AHORA sí es seguro guardar el ETag/Last-Modified y el
            # hash del cuerpo para la próxima comprobación condicional (no se guarda el de
            # una posible página de error).
            if contenido:
                import hashlib as _hlb2
                db.execute("UPDATE rss_feeds SET http_etag = ?, http_modified = ?, "
                           "body_hash = ? WHERE id = ?",
                           (_etag_new, _mod_new, _hlb2.sha1(contenido).hexdigest(), feed_id))
            elif _etag_new or _mod_new:
                db.execute("UPDATE rss_feeds SET http_etag = ?, http_modified = ? WHERE id = ?",
                           (_etag_new, _mod_new, feed_id))

            db.execute("UPDATE rss_feeds SET last_error = NULL WHERE id = ?", (feed_id,))

            # Precargar los filtros de contenido una sola vez para todo el feed.
            filtros_rss = ContentFilterEngine.cargar("rss")
            # Precargar las alertas por palabra clave activas DE ESTA CUENTA (auditoría
            # RSS-02: antes se aplicaban las de todas las cuentas), con caché por ciclo.
            alertas_rss = self._cargar_alertas(account_id)

            # 3. FASE DE CÁLCULO (CPU), FUERA de cualquier transacción: parsear cada entrada,
            # aplicar filtros y complementos, y extraer enlaces (BeautifulSoup, que es lo más caro).
            # Antes todo esto ocurría DENTRO de db.transaccion(), de modo que el lock de ESCRITURA
            # quedaba tomado mientras se hacía trabajo de CPU y otros servicios que querían escribir
            # esperaban en balde. Aquí solo se LEE (chequeo de duplicados, por la conexión de lectura
            # sin lock) y se calcula; los INSERT van luego, en una transacción corta (fase 6).
            from bs4 import BeautifulSoup
            import json
            import time as _time
            a_insertar = []  # candidatos listos para insertar (un dict por artículo nuevo)
            # IDs ya procesados EN ESTA descarga: algunos feeds repiten el mismo artículo dos veces
            # en el mismo XML; el set evita procesarlo (y luego insertarlo) dos veces.
            _vistos_lote = set()
            # Chequeo de duplicados POR LOTE: una sola consulta por feed (troceada de 500 en 500
            # por el límite de parámetros de SQLite) en vez de un SELECT por entrada. Con un
            # sondeo global de muchos feeds, esto ahorra miles de idas y vueltas a la base.
            # CONTRATO ÚNICO DE PARSEO (auditoría RSS-07): todas las entradas pasan por el
            # normalizador, venga el resultado de feedparser, de fastfeedparser o del web
            # scraper. A partir de aquí el bucle solo ve dicts con claves de feedparser.
            entradas = [_normalizar_entrada(_x) for _x in parsed.entries]
            _ids_candidatos = []
            for _e in entradas:
                _lnk = _e.get("link", "")
                _ids_candidatos.append(self._generar_article_id(
                    feed_id, _e.get("id") or _lnk, _lnk, _e.get("title", "Sin título")))
            # {id: content_hash} de los ya guardados: sirve para el chequeo de duplicados Y
            # para detectar artículos MODIFICADOS por el editor (auditoría RSS-14).
            _hash_existentes = {}
            # Registro de leídos: artículos que ya se leyeron y luego se vaciaron ("no conservar
            # entre sesiones"). Si el feed los vuelve a servir, entran YA como leídos (no como
            # novedad sin leer). Se precarga en lote, igual que _existentes, sin consulta por entrada.
            _leidos_ledger = set()
            for _i in range(0, len(_ids_candidatos), 500):
                _lote = _ids_candidatos[_i:_i + 500]
                _ph = ",".join("?" for _ in _lote)
                for r in db.query(
                        f"SELECT id, COALESCE(content_hash,'') FROM rss_articles "
                        f"WHERE id IN ({_ph})", tuple(_lote)):
                    _hash_existentes[r[0]] = r[1]
                try:
                    _leidos_ledger.update(r[0] for r in db.query(
                        f"SELECT article_id FROM rss_read_ledger WHERE article_id IN ({_ph})",
                        tuple(_lote)))
                except Exception:
                    pass
            _existentes = set(_hash_existentes)
            # Borrados DEFINITIVAMENTE por el usuario: no vuelven (rss_borrados.py).
            try:
                from .rss_borrados import borrados_entre
                _borrados = borrados_entre(_ids_candidatos)
            except Exception:
                _borrados = set()
            a_actualizar = []  # artículos ya guardados cuyo ORIGEN cambió (RSS-14)
            for _i_entry, entry in enumerate(entradas):
                if ctx and ctx.is_cancelled():
                    break
                # Ceder el GIL al hilo de la INTERFAZ cada pocos artículos: parsear HTML
                # (BeautifulSoup) es CPU pura y, en feeds grandes (cientos de artículos) o al
                # reprocesar todo al arrancar, podía dejar al lector de pantalla sin turno. Como este
                # hilo va con prioridad baja, al ceder, Windows da paso a la UI. No bloquea (es fondo).
                if _i_entry and _i_entry % 8 == 0:
                    _time.sleep(0)

                title = entry.get("title") or "Sin título"
                link = entry.get("link", "")
                guid = entry.get("id") or link
                author = entry.get("author", "")

                # Contenido ya RESUELTO por el normalizador (content/summary/description,
                # sea cual sea el parser de origen).
                content = entry.get("contenido_resuelto") or ""

                # Parsear fecha de publicación. feedparser entrega las fechas en
                # UTC (struct_time), por lo que se usa calendar.timegm (UTC) y no
                # time.mktime (que asumiría hora local y desfasaría el tiempo).
                published_at = None
                for date_key in ["published_parsed", "updated_parsed", "created_parsed"]:
                    parsed_date = entry.get(date_key)
                    if parsed_date:
                        import calendar
                        published_at = datetime.fromtimestamp(calendar.timegm(parsed_date)).isoformat()
                        break
                fecha_real = published_at is not None  # False si el feed no traía fecha (respaldo now)
                if not published_at:
                    published_at = datetime.now().isoformat()

                # 4. Detectar adjuntos multimedia (enclosures) / podcasts. El PRINCIPAL
                # (primer audio/vídeo, el que se reproduce/descarga) va en media_url/
                # enclosure_*; ADEMÁS se conservan TODOS los adjuntos en la lista
                # `enclosures_todos` para persistirlos en rss_enclosures (RSS: "múltiples
                # enclosures"). Antes el `break` descartaba todos salvo el primero.
                has_media = 0
                media_url = None
                media_type = None
                enclosure_length = 0
                is_podcast = 0

                def _es_audio(u, t):
                    return ("audio" in (t or "")
                            or (u or "").lower().endswith((".mp3", ".ogg", ".m4a", ".wav")))

                enclosures = entry.get("enclosures", [])
                enclosures_todos = []
                for _orden, enc in enumerate(enclosures or []):
                    m_url = enc.get("href") or enc.get("url") or ""
                    m_type = enc.get("type", "") or ""
                    if not m_url:
                        continue
                    try:
                        m_len = int(enc.get("length") or 0)
                    except (TypeError, ValueError):
                        m_len = 0
                    es_av = ("audio" in m_type or "video" in m_type
                             or m_url.lower().endswith((".mp3", ".wav", ".ogg", ".mp4", ".m4a")))
                    enclosures_todos.append({"url": m_url, "mime": m_type, "length": m_len,
                                             "es_audio": 1 if _es_audio(m_url, m_type) else 0})
                    # El PRIMER audio/vídeo es el "principal" (reproducible).
                    if es_av and media_url is None:
                        has_media = 1
                        media_url = m_url
                        media_type = m_type
                        enclosure_length = m_len
                        if _es_audio(m_url, m_type):
                            is_podcast = 1

                # Podcast 2.0 / iTunes: duración, contenido explícito, temporada/episodio,
                # imagen del episodio y metadatos ricos (capítulos/transcripción/apoyo/
                # personas) en podcast_json. Solo se rellenan si el feed los trae.
                duration_seconds = _duracion_a_segundos(entry.get("itunes_duration"))
                _expl = entry.get("itunes_explicit")
                explicit = 1 if (_expl is True or str(_expl).lower() in ("yes", "true", "explicit")) else 0
                episode_image = entry.get("episode_image") or None
                try:
                    season = int(entry.get("itunes_season")) if entry.get("itunes_season") else None
                except (TypeError, ValueError):
                    season = None
                try:
                    episode_num = int(entry.get("itunes_episode")) if entry.get("itunes_episode") else None
                except (TypeError, ValueError):
                    episode_num = None
                podcast_json = _construir_podcast_json(entry) if is_podcast else ""

                # 5. Generar ID único y comprobar si ya existe en SQLite
                article_id = self._generar_article_id(feed_id, guid, link, title)
                # Si el feed repite este artículo en la misma descarga, no lo procesamos otra vez.
                if article_id in _vistos_lote or article_id in _borrados:
                    continue
                _vistos_lote.add(article_id)

                # Hash ESTABLE del contenido de origen (auditoría RSS-14): si el editor
                # corrige título, cuerpo, autor o adjunto manteniendo el GUID, el hash
                # cambia y el artículo guardado se ACTUALIZA (antes se ignoraba para siempre).
                import hashlib as _hl
                content_hash = _hl.sha1(
                    "\x1f".join((title or "", content or "", author or "", link or "",
                                 media_url or "")).encode("utf-8", "replace")).hexdigest()

                # Chequeo de duplicados contra el mapa precargado (una consulta por lote, no
                # por entrada). La carrera con otro hilo la sigue cubriendo el try del INSERT.
                if article_id in _existentes:
                    hash_previo = _hash_existentes.get(article_id, "")
                    if hash_previo and hash_previo != content_hash:
                        # Modificado en el origen: actualizar SOLO campos de origen; los
                        # estados del usuario (leído, favorito, leer después, papelera,
                        # descarga) no se tocan. El texto para FTS se recalcula abajo.
                        a_actualizar.append({
                            "article_id": article_id, "title": title, "content": content,
                            "author": author, "link": link, "media_url": media_url,
                            "media_type": media_type, "has_media": has_media,
                            "is_podcast": is_podcast, "enclosure_length": enclosure_length,
                            "content_hash": content_hash,
                            "source_updated_at": published_at if fecha_real else None,
                        })
                    continue

                # Corte de importación: si el feed tiene fecha de corte (se puso al limpiar los datos
                # de RSS con la opción "solo nuevos a partir de la limpieza"), NO importar artículos
                # publicados ANTES de esa fecha. Solo se aplica a artículos con fecha REAL del feed;
                # los que no traen fecha (respaldo = ahora) se consideran nuevos y sí entran. Las
                # cadenas ISO son naive-local en ambos lados, así que la comparación textual es válida.
                if corte_import and fecha_real and published_at < corte_import:
                    continue

                # Filtros de contenido unificados: ocultar => no insertar;
                # o marcar automáticamente leído / destacado / leer más tarde.
                acc = ContentFilterEngine.evaluar(
                    "rss", f"{title} {content}", author or "", filtros_rss
                )
                if acc["hide"]:
                    continue
                ini_read = 1 if acc["mark_read"] else 0
                ini_fav = 1 if acc["star"] else 0
                ini_later = 1 if acc["read_later"] else 0
                # Ya leído en una sesión anterior (y vaciado después): no reaparece como no leído.
                if article_id in _leidos_ledger:
                    ini_read = 1

                # Complementos: pueden interceptar el artículo nuevo (ocultarlo, marcarlo
                # leído/destacado/leer-más-tarde o reescribir el título). Si nadie está activo
                # no cuesta nada.
                try:
                    from app.core.plugins import plugin_manager, PluginHook
                    if plugin_manager.hay_hooks(PluginHook.ARTICLE_NEW):
                        cambios = plugin_manager.dispatch_articulo({
                            "title": title, "content": content, "author": author,
                            "link": link, "feed_id": feed_id})
                        if cambios.get("hide"):
                            continue
                        if cambios.get("mark_read"):
                            ini_read = 1
                        if cambios.get("star"):
                            ini_fav = 1
                        if cambios.get("read_later"):
                            ini_later = 1
                        if cambios.get("title"):
                            title = str(cambios["title"])
                except Exception as e:
                    log.debug(f"Complementos ARTICLE_NEW: {e}")

                # Texto completo por feed: se ENCOLA para el trabajador de enriquecimiento
                # (auditoría RSS-12) en vez de descargarse aquí: antes cada página (hasta
                # 15 s por artículo) retenía este trabajador del sondeo y una ronda podía
                # tardar minutos. El artículo aparece YA con su resumen y el texto completo
                # llega solo, en segundo plano, sin frenar al resto de feeds.
                # No pedirlo para artículos que entran YA LEÍDOS (los re-importados tras
                # el vaciado "no conservar entre sesiones" via ledger): no se van a leer,
                # y tras un vaciado eran CIENTOS de páginas descargadas y analizadas en
                # balde justo durante la carga (saturaban CPU/disco; fallo reportado).
                pedir_fulltext = bool(
                    quiere_fulltext and link and _fulltext_restantes > 0
                    and len(content or "") < 600 and not ini_read)
                if pedir_fulltext:
                    _fulltext_restantes -= 1

                # Crear objeto del artículo temporal para evaluar reglas
                art_obj = RSSArticle(
                    id=article_id, feed_id=feed_id, guid=guid, link=link, title=title,
                    content=content, author=author, published_at=published_at,
                    read=bool(ini_read), favorite=bool(ini_fav), has_media=bool(has_media),
                    media_url=media_url, media_type=media_type, read_later=bool(ini_later)
                )

                # Extraer todos los enlaces únicos (BeautifulSoup: lo más caro; va fuera del lock)
                urls = []
                if link and link not in urls:
                    urls.append(link)
                if media_url and media_url not in urls:
                    urls.append(media_url)
                # Solo analizar como HTML si realmente contiene etiquetas
                # (evita el aviso MarkupResemblesLocatorWarning de BeautifulSoup).
                # content_fts: TEXTO PLANO para el índice de búsqueda. Indexar el HTML crudo (con
                # etiquetas, atributos y URLs) disparaba el tamaño del índice FTS y metía tokens
                # basura; aquí se reaprovecha el mismo BeautifulSoup para extraer solo el texto.
                content_fts = content
                if content and "<" in content:
                    try:
                        soup_rss = BeautifulSoup(content, "html.parser")
                        for a in soup_rss.find_all("a"):
                            href = a.get("href")
                            if href and href.startswith("http") and href not in urls:
                                urls.append(href)
                        content_fts = soup_rss.get_text(" ", strip=True)
                    except Exception as e:
                        log.error(f"Error parsing HTML content in RSS: {e}")
                urls_json = json.dumps(urls)

                a_insertar.append({
                    "article_id": article_id, "guid": guid, "link": link, "title": title,
                    "content": content, "content_fts": content_fts, "author": author,
                    "published_at": published_at,
                    "ini_read": ini_read, "ini_fav": ini_fav, "ini_later": ini_later,
                    "has_media": has_media, "media_url": media_url, "media_type": media_type,
                    "urls_json": urls_json, "enclosure_length": enclosure_length,
                    "is_podcast": is_podcast, "art_obj": art_obj,
                    "content_hash": content_hash,
                    "source_updated_at": published_at if fecha_real else None,
                    "pedir_fulltext": pedir_fulltext,
                    # Podcast 2.0 + múltiples adjuntos.
                    "duration_seconds": duration_seconds, "explicit": explicit,
                    "episode_image": episode_image, "season": season,
                    "episode_num": episode_num, "podcast_json": podcast_json,
                    "enclosures_todos": enclosures_todos,
                })

            # 6. FASE DE ESCRITURA: todos los artículos nuevos del feed en UNA transacción (un solo
            # commit). El lock de escritura solo se toma aquí, ya sin trabajo de CPU por medio. Con
            # el auto-join por hilo, los db.execute de dentro (insert, reglas, FTS) comparten la
            # misma transacción.
            # account_id se denormaliza (además de en el feed) para que los buffers RSS globales
            # filtren y ordenen por (account_id, published_at) usando un índice, sin tabla temporal.
            sql_insert = """
            INSERT INTO rss_articles (
                id, feed_id, account_id, guid, link, title, content, author, published_at, read, favorite, has_media, media_url, media_type, urls_json, read_later,
                enclosure_url, enclosure_type, enclosure_length, is_podcast,
                first_seen_at, last_seen_at, source_updated_at, content_hash,
                duration_seconds, explicit, episode_image, season, episode_num, podcast_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            _ahora = datetime.now().isoformat()
            # Plan de reglas COMPILADO una vez para todo el feed (auditoría RSS-17): antes
            # cada artículo recargaba las reglas de la base y compilaba la regex de nuevo.
            _plan_reglas = RSSRulesEngine.plan(account_id)
            articulos_modificados = 0
            if a_actualizar:
                # Versiones anteriores de los artículos corregidos en origen (rss_versiones.py).
                from .rss_versiones import asegurar_tabla as _asegurar_versiones
                _asegurar_versiones()
            if a_insertar or a_actualizar or _existentes:
                with db.transaccion():
                    for art in a_insertar:
                        try:
                            db.execute(sql_insert, (
                                art["article_id"], feed_id, account_id, art["guid"], art["link"],
                                art["title"], art["content"], art["author"], art["published_at"],
                                art["ini_read"], art["ini_fav"], art["has_media"], art["media_url"],
                                art["media_type"], art["urls_json"], art["ini_later"],
                                art["media_url"] if art["is_podcast"] else None,
                                art["media_type"] if art["is_podcast"] else None,
                                art["enclosure_length"], art["is_podcast"],
                                _ahora, _ahora, art["source_updated_at"], art["content_hash"],
                                art["duration_seconds"], art["explicit"], art["episode_image"],
                                art["season"], art["episode_num"], art["podcast_json"] or None
                            ))
                        except Exception as ins_err:
                            # Carrera rara: otro hilo insertó el mismo id entre el chequeo y aquí.
                            # Saltar este artículo sin abortar el resto del feed.
                            log.debug(f"No se pudo insertar artículo RSS {art['article_id']}: {ins_err}")
                            continue

                        # 8bis. Múltiples adjuntos: guardar TODOS los enclosures del artículo
                        # (el principal ya va en enclosure_url; aquí queda la lista completa).
                        if art["enclosures_todos"]:
                            for _o, _enc in enumerate(art["enclosures_todos"]):
                                try:
                                    db.execute(
                                        "INSERT INTO rss_enclosures (article_id, url, mime, "
                                        "length, es_audio, orden) VALUES (?, ?, ?, ?, ?, ?)",
                                        (art["article_id"], _enc["url"], _enc["mime"],
                                         _enc["length"], _enc["es_audio"], _o))
                                except Exception as enc_err:
                                    log.debug(f"No se pudo guardar enclosure de {art['article_id']}: {enc_err}")

                        # 7. Evaluar reglas automáticas (pueden marcar como leído, favorito, etc.)
                        RSSRulesEngine.evaluar_articulo(account_id, art["art_obj"], plan=_plan_reglas)

                        # 8. Indexar en FTS5 para búsquedas instantáneas (TEXTO PLANO, no HTML).
                        try:
                            db.execute(
                                "INSERT INTO rss_articles_fts (article_db_id, title, content, author) VALUES (?, ?, ?, ?)",
                                (art["article_id"], art["title"], art["content_fts"], art["author"])
                            )
                        except Exception as fts_err:
                            log.debug(f"Error al indexar FTS5 de artículo RSS: {fts_err}")

                        nuevos_articulos += 1

                    # 8a. Artículos MODIFICADOS en el origen (auditoría RSS-14): actualizar solo
                    # los campos de origen; leído/favorito/leer después/papelera/descarga se
                    # preservan. El índice FTS se reindexa con el texto nuevo.
                    for art in a_actualizar:
                        try:
                            from .rss_versiones import guardar_version_anterior
                            guardar_version_anterior(art["article_id"])
                            db.execute(
                                "UPDATE rss_articles SET title = ?, content = ?, author = ?, "
                                "link = ?, media_url = ?, media_type = ?, has_media = ?, "
                                "is_podcast = ?, enclosure_url = COALESCE(?, enclosure_url), "
                                "enclosure_type = COALESCE(?, enclosure_type), "
                                "enclosure_length = ?, content_hash = ?, "
                                "source_updated_at = COALESCE(?, source_updated_at), "
                                "last_seen_at = ? WHERE id = ?",
                                (art["title"], art["content"], art["author"], art["link"],
                                 art["media_url"], art["media_type"], art["has_media"],
                                 art["is_podcast"],
                                 art["media_url"] if art["is_podcast"] else None,
                                 art["media_type"] if art["is_podcast"] else None,
                                 art["enclosure_length"], art["content_hash"],
                                 art["source_updated_at"], _ahora, art["article_id"]))
                            db.execute("DELETE FROM rss_articles_fts WHERE article_db_id = ?",
                                       (art["article_id"],))
                            texto_fts = art["content"]
                            if texto_fts and "<" in texto_fts:
                                try:
                                    texto_fts = BeautifulSoup(
                                        texto_fts, "html.parser").get_text(" ", strip=True)
                                except Exception:
                                    pass
                            db.execute(
                                "INSERT INTO rss_articles_fts (article_db_id, title, content, author) VALUES (?, ?, ?, ?)",
                                (art["article_id"], art["title"], texto_fts, art["author"]))
                            articulos_modificados += 1
                        except Exception as up_err:
                            log.debug(f"No se pudo actualizar artículo RSS {art['article_id']}: {up_err}")
                    # 8a-bis. last_seen_at por LOTE para los ya guardados que el feed sigue
                    # sirviendo (los modificados ya lo llevan en su UPDATE).
                    vistos_sin_cambios = [i for i in _existentes
                                          if i not in {a["article_id"] for a in a_actualizar}]
                    for _i in range(0, len(vistos_sin_cambios), 500):
                        _lote = vistos_sin_cambios[_i:_i + 500]
                        _ph = ",".join("?" for _ in _lote)
                        db.execute(f"UPDATE rss_articles SET last_seen_at = ? WHERE id IN ({_ph})",
                                   (_ahora, *(_lote)))
            if articulos_modificados:
                log.debug(f"{feed_title}: {articulos_modificados} artículos actualizados del origen.")

            # 8b. Alertas por palabra clave: avisar si el título o el contenido contiene alguna
            # palabra vigilada por el usuario. Fuera de la transacción (puede locutar/notificar).
            if alertas_rss:
                # Tope de avisos por actualización (Preferencias, RSS): los que pasen del tope
                # se resumen en un aviso final en lugar de uno por artículo.
                casadas = []
                for art in a_insertar:
                    kw = self._evaluar_alertas(alertas_rss, art["title"], art["content"], avisar=False)
                    if kw:
                        casadas.append((kw, art["title"]))
                try:
                    from app.core.config_manager import config as _cfg_al
                    tope = max(1, int(_cfg_al.get("rss", "max_alert_notifications", 3)))
                except (TypeError, ValueError):
                    tope = 3
                if casadas:
                    from app.core.speech import notificar
                    for kw, titulo_al in casadas[:tope]:
                        notificar(_("Alerta RSS «{0}»: {1}").format(kw, titulo_al or ""),
                                  _("Alerta de palabra clave"))
                    if len(casadas) > tope:
                        notificar(_("Y {0} alertas más en {1}.").format(len(casadas) - tope, feed_title),
                                  _("Alerta de palabra clave"))

            # 8c. Encolar el TEXTO COMPLETO de los artículos que lo pidieron (auditoría
            # RSS-12): trabajador propio, fuera del sondeo, sin retener este hilo.
            pendientes_ft = [(a["article_id"], a["link"]) for a in a_insertar if a["pedir_fulltext"]]
            if pendientes_ft:
                self._encolar_fulltext(pendientes_ft)

            # 9. Recortar el feed si supera el máximo configurado (0 = sin límite),
            # conservando los más recientes y NO borrando favoritos / leer-más-tarde.
            self._recortar_feed(feed_id)

            # El resultado por feed y por ciclo es RUTINARIO (aunque lleguen artículos): va en DEBUG,
            # para no inflar el log en sesiones largas (era ~el 18 % del log). Lo importante —fallos
            # de un feed, errores— se sigue registrando en su nivel. Las novedades ya se ven en la app.
            if nuevos_articulos:
                log.debug(f"Actualización completada para {feed_title}. Nuevos artículos: {nuevos_articulos}")
            else:
                log.debug(f"Actualización completada para {feed_title}. Sin artículos nuevos.")
            try:
                from app.core.plugins import plugin_manager, PluginHook
                plugin_manager.dispatch(PluginHook.FEED_UPDATED, feed_id=feed_id, nuevos=nuevos_articulos)
            except Exception as e:
                log.debug(f"Complementos FEED_UPDATED: {e}")
            # Éxito: anotar salud y programar el PRÓXIMO sondeo (scheduler por vencimiento).
            self._registrar_exito_feed(feed_id, custom_interval, nuevos_articulos)
            return nuevos_articulos

        except Exception as e:
            err_msg = str(e)
            # Un feed roto (XML mal formado, etc.) se sondea cada pocos minutos y, si no se controla,
            # llena el log con cientos de ERROR idénticos en una sesión larga. Se registra como ERROR
            # solo la PRIMERA vez (o cuando el error CAMBIA respecto al último guardado); mientras siga
            # fallando con el mismo motivo, se baja a DEBUG.
            if err_msg == last_error_prev:
                log.debug(f"Feed {feed_title} ({url}) sigue fallando con el mismo error: {err_msg}")
            else:
                log.error(f"Error al descargar o procesar feed {feed_title} ({url}): {err_msg}")
            # Fallo: anotar salud y RETROCESO por errores (30 min -> ... -> 24 h), con
            # tratamiento por clase (410 pausa, 404 diaria, 401/403 aviso de credenciales).
            self._registrar_fallo_feed(feed_id, feed_title, e, fail_count_prev, custom_interval)
            raise

    # --- COLA DE TEXTO COMPLETO (enriquecimiento; auditoría RSS-12) ---
    # Un ÚNICO trabajador de fondo con presupuesto por hora: los artículos aparecen al
    # momento con su resumen y el cuerpo completo llega después, sin retener el sondeo.
    _FULLTEXT_POR_HORA = 60

    def _encolar_fulltext(self, pares):
        """Encola (article_id, link) para descargar su texto completo en segundo plano."""
        import threading
        from collections import deque
        if not hasattr(self, "_ft_cola"):
            self._ft_cola = deque()
            self._ft_lock = threading.Lock()
            self._ft_hora = []  # timestamps de descargas de la última hora (presupuesto)
            self._ft_worker = None
        with self._ft_lock:
            en_cola = {p[0] for p in self._ft_cola}
            for par in pares:
                if par[0] not in en_cola:
                    self._ft_cola.append(par)
            if self._ft_worker is None or not self._ft_worker.is_alive():
                self._ft_worker = threading.Thread(
                    target=self._trabajador_fulltext, name="rss-fulltext", daemon=True)
                self._ft_worker.start()

    def _trabajador_fulltext(self):
        """Vacía la cola de texto completo con calma (modo FONDO total, presupuesto por hora)."""
        import time as _t
        # THREAD_MODE_BACKGROUND_BEGIN: además de la CPU, baja la prioridad de DISCO y
        # memoria del hilo. readability (lxml) es intensivo en ambas; con la prioridad
        # normal de E/S competía con la base de datos y el sistema justo durante la carga.
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            if not k32.SetThreadPriority(k32.GetCurrentThread(), 0x00010000):
                _bajar_prioridad_hilo_actual()
        except Exception:
            _bajar_prioridad_hilo_actual()
        try:
            while True:
                with self._ft_lock:
                    # Presupuesto: no más de _FULLTEXT_POR_HORA extracciones por hora.
                    ahora = _t.time()
                    self._ft_hora = [x for x in self._ft_hora if ahora - x < 3600]
                    if not self._ft_cola:
                        return  # cola vacía: el hilo muere; se recrea al encolar
                    if len(self._ft_hora) >= self._FULLTEXT_POR_HORA:
                        dormir = 120.0
                    else:
                        self._ft_hora.append(ahora)
                        article_id, link = self._ft_cola.popleft()
                        dormir = 0.0
                if dormir:
                    _t.sleep(dormir)
                    continue
                try:
                    # Mismo camino que el lector: extractor elegido en Preferencias, se guarda
                    # el HTML limpio (sirve a la vista de texto y a la HTML) y se anota el
                    # intento para que el lector no vuelva a descargarlo al abrirlo.
                    self.descargar_articulo_completo(article_id)
                except Exception as e:
                    log.debug(f"Texto completo en cola falló para {link}: {e}")
                _t.sleep(1.0)  # respiro entre páginas: nunca martillear a un mismo host
        finally:
            db.cerrar_lectura_hilo()

    # --- SALUD DEL FEED Y SCHEDULER POR VENCIMIENTO (auditoría RSS-05/11) ---
    def _intervalo_base_minutos(self, custom_interval: int) -> int:
        """Intervalo base del feed en minutos: el suyo propio si lo tiene, si no el global."""
        if custom_interval and int(custom_interval) > 0:
            return int(custom_interval)
        try:
            from app.core.config_manager import config
            return max(1, int(config.get("rss", "update_interval", 30)))
        except Exception:
            return 30

    def _registrar_exito_feed(self, feed_id: int, custom_interval: int, nuevos: int,
                              status: int = 200):
        """Anota el éxito (salud) y programa la PRÓXIMA actualización. Sin intervalo propio,
        el ritmo es ADAPTATIVO por la frecuencia real de publicación: los feeds "calientes"
        (publicaron en la última hora) van al intervalo del usuario; los templados, con un
        suelo de 5 o 15 minutos; los que llevan días parados, x2/x4 hasta 24 h. Publicar los
        devuelve al ritmo rápido en la siguiente pasada. Sin esto, 99 feeds con el intervalo
        global a 1 minuto suponían ~230 parseos completos cada 2 minutos (CPU sostenida que
        entorpecía hasta el lector de pantalla; medido en el log del fallo reportado)."""
        from datetime import timedelta
        minutos = self._intervalo_base_minutos(custom_interval)
        if not (custom_interval and int(custom_interval) > 0):
            try:
                r = db.query_one(
                    "SELECT MAX(published_at) FROM rss_articles WHERE feed_id = ?", (feed_id,))
                ultima = r[0] if r and r[0] else None
                if ultima and not nuevos:
                    sin_publicar = datetime.now() - datetime.fromisoformat(ultima)
                    if sin_publicar.days >= 7:
                        minutos = min(minutos * 4, 1440)
                    elif sin_publicar.days >= 2:
                        minutos = min(minutos * 2, 1440)
                    elif sin_publicar >= timedelta(hours=6):
                        minutos = max(minutos, 15)
                    elif sin_publicar >= timedelta(hours=1):
                        minutos = max(minutos, 5)
            except Exception:
                pass
        # Dispersión ESTABLE por feed (segundos): que 99 feeds con el mismo intervalo no
        # venzan todos en el mismo segundo (ráfagas de conexiones).
        siguiente = datetime.now() + timedelta(minutes=minutos, seconds=(feed_id * 37) % 60)
        try:
            db.execute(
                "UPDATE rss_feeds SET last_success_at = ?, next_update_at = ?, fail_count = 0, "
                "last_status = ?, last_error = NULL WHERE id = ?",
                (datetime.now().isoformat(), siguiente.isoformat(), status, feed_id))
        except Exception as e:
            log.debug(f"No se pudo registrar el éxito del feed {feed_id}: {e}")

    def _registrar_fallo_feed(self, feed_id: int, feed_title: str, error: Exception,
                              fail_count_prev: int, custom_interval: int):
        """Anota el fallo y aplica RETROCESO: 30 min, 1 h, 2 h... hasta 24 h. Por clase:
        410 (retirado) pausa el feed y avisa; 404 pasa a comprobación diaria; 401/403
        guarda un error claro de credenciales. El refresco manual siempre puede forzarlo."""
        from datetime import timedelta
        import requests
        import builtins as _bi
        _ = getattr(_bi, "_", lambda s: s)  # gettext puede no estar instalado (tests/CLI)
        status = None
        if isinstance(error, requests.HTTPError) and getattr(error, "response", None) is not None:
            status = error.response.status_code
        fallos = int(fail_count_prev or 0) + 1
        err_msg = str(error)

        if status == 410:
            # El feed ya NO existe (retirado a propósito): dejar de consultarlo y avisar.
            try:
                db.execute(
                    "UPDATE rss_feeds SET paused = 1, fail_count = ?, last_status = ?, "
                    "last_error = ? WHERE id = ?",
                    (fallos, status, _("El feed fue retirado por el servidor (410); se ha pausado."),
                     feed_id))
                from app.core.speech import notificar
                notificar(_("El feed «{0}» ya no existe (410) y se ha puesto en pausa.")
                          .format(feed_title))
            except Exception:
                pass
            return
        if status == 404:
            minutos = 1440  # no encontrado: quizá vuelva; comprobar una vez al día
            err_msg = _("No encontrado (404): se comprobará una vez al día.")
        elif status in (401, 403):
            minutos = 720
            err_msg = _("Acceso denegado ({0}): el feed necesita credenciales o configuración.").format(status)
        else:
            # Retroceso exponencial genérico: 30 min * 2^(n-1), tope 24 h. Nunca antes que
            # el intervalo normal del feed (no tendría sentido reintentar más deprisa).
            minutos = min(30 * (2 ** max(0, fallos - 1)), 1440)
            minutos = max(minutos, self._intervalo_base_minutos(custom_interval))
        siguiente = datetime.now() + timedelta(minutes=minutos, seconds=(feed_id * 37) % 60)
        try:
            db.execute(
                "UPDATE rss_feeds SET next_update_at = ?, fail_count = ?, last_status = ?, "
                "last_error = ? WHERE id = ?",
                (siguiente.isoformat(), fallos, status, err_msg, feed_id))
        except Exception as e:
            log.debug(f"No se pudo registrar el fallo del feed {feed_id}: {e}")

    def _procesar_mudanza_feed(self, feed_id: int, url_actual: str, url_nueva: str):
        """Feed MUDADO (prioridad D): un 301/308 apunta a `url_nueva`. Se CONFIRMA a los dos
        ciclos (misma propuesta repetida) para no cambiar por un redirect temporal, y solo
        si la URL nueva no está ya suscrita en la cuenta. Al aplicarlo, se avisa al usuario."""
        r = db.query_one("SELECT account_id, COALESCE(moved_to,'') FROM rss_feeds WHERE id = ?",
                         (feed_id,))
        if not r:
            return
        account_id, moved_prev = r[0], r[1]
        if moved_prev != url_nueva:
            # Primera vez (o propuesta distinta): registrar, aún no aplicar.
            db.execute("UPDATE rss_feeds SET moved_to = ? WHERE id = ?", (url_nueva, feed_id))
            return
        # Confirmada: aplicar si la URL nueva no colisiona con otro feed de la cuenta.
        from app.core.url_utils import normalizar_url
        norm = normalizar_url(url_nueva)
        existe = db.query_one(
            "SELECT id FROM rss_feeds WHERE account_id = ? AND id != ? "
            "AND (url = ? OR url_normalized = ?)", (account_id, feed_id, url_nueva, norm))
        if existe:
            db.execute("UPDATE rss_feeds SET moved_to = NULL WHERE id = ?", (feed_id,))
            return
        db.execute("UPDATE rss_feeds SET url = ?, url_normalized = ?, moved_to = NULL WHERE id = ?",
                   (url_nueva, norm, feed_id))
        try:
            from app.core.speech import notificar
            fila = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (feed_id,))
            notificar(_("El feed «{0}» se ha mudado y su dirección se ha actualizado sola.")
                      .format(fila[0] if fila else url_nueva))
        except Exception:
            pass

    # --- ALERTAS POR PALABRA CLAVE ---
    def _cargar_alertas(self, account_id: int) -> list:
        """Carga las alertas por palabra clave activas DE UNA CUENTA (palabra + regex
        compilada), con caché breve: en un ciclo global de decenas de feeds se cargan
        una vez por cuenta, no una vez por feed."""
        import re
        import time as _t
        if not hasattr(self, "_alertas_cache"):
            self._alertas_cache = {}
        en_cache = self._alertas_cache.get(account_id)
        if en_cache and (_t.time() - en_cache[1]) < 120:
            return en_cache[0]
        alertas = []
        try:
            rows = db.query(
                "SELECT keyword, is_regex FROM rss_keyword_alerts WHERE active = 1 "
                "AND account_id = ?", (account_id,))
        except Exception:
            return alertas
        for kw, is_regex in rows:
            if not kw:
                continue
            patron = None
            if is_regex:
                try:
                    patron = re.compile(kw, re.IGNORECASE)
                except re.error:
                    patron = None
            alertas.append({"kw": kw, "regex": patron})
        self._alertas_cache[account_id] = (alertas, _t.time())
        return alertas

    @staticmethod
    def _evaluar_alertas(alertas: list, title: str, content: str, avisar: bool = True):
        """Si un artículo nuevo coincide con alguna alerta, avisa al usuario (o, con
        avisar=False, solo devuelve la palabra que coincidió, para agrupar los avisos)."""
        texto = f"{title or ''} {content or ''}"
        texto_low = texto.lower()
        for a in alertas:
            casa = (a["regex"].search(texto) if a["regex"] else (a["kw"].lower() in texto_low))
            if casa:
                if avisar:
                    try:
                        from app.core.speech import notificar
                        notificar(_("Alerta RSS «{0}»: {1}").format(a["kw"], title or ""),
                                  _("Alerta de palabra clave"))
                    except Exception:
                        pass
                return a["kw"]  # un solo aviso por artículo
        return None

    # --- LECTURA Y OPERACIONES ---
