# -*- coding: utf-8 -*-
"""Descubrir el feed de cualquier dirección y diagnosticar una URL (mixin de RSSService).

Antes, al añadir un feed solo se miraban las etiquetas <link rel="alternate"> de la página y
feedparser descargaba la dirección por su cuenta, con su propia identificación. Resultado: webs
que no anuncian su feed decían "no es un feed" aunque lo tuvieran en /feed, y feeds públicos
tras un cortafuegos que rechaza aplicaciones (Diario de Mallorca: 406) ni se podían añadir.

Ahora, para una dirección cualquiera:
1. Direcciones de redes y servicios se convierten a su feed (Reddit, Mastodon, Bluesky, Lemmy,
   YouTube —canales y listas—, GitHub, Medium).
2. Se prueba la propia dirección, luego los feeds que anuncia la página y después las rutas
   habituales (/feed, /rss, /atom.xml...). Idea tomada de BlindRSS (serrebidev, MIT,
   core/discovery.py), reescrita para esta aplicación.
3. Cada candidato se DESCARGA con la identificación de la app y, si un cortafuegos la rechaza
   (403/406), como navegador; y se valida parseándolo (RSS, Atom o JSON Feed).
"""
import concurrent.futures
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs

import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

log = logging.getLogger(__name__)

# Rutas donde suele estar el feed cuando la página no lo anuncia.
RUTAS_HABITUALES = ("/feed", "/feed/", "/rss", "/rss/", "/rss.xml", "/atom.xml", "/index.xml",
                    "/feed.xml", "/feeds/posts/default", "/?feed=rss2", "/blog/feed", "/news/rss")

_MAX_BYTES = 10 * 1024 * 1024


def normalizar_entrada(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    return url


def convertir_url_social(url: str) -> List[str]:
    """Direcciones de feed para páginas de redes y servicios conocidos (sin red). Lista vacía si
    la dirección no es de ninguno. Pueden salir varias (GitHub: publicaciones y cambios)."""
    try:
        p = urlparse(normalizar_entrada(url))
    except Exception:
        return []
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    partes = [x for x in (p.path or "").split("/") if x]
    salida = []
    if host.endswith("reddit.com") and len(partes) >= 2 and partes[0] in ("r", "user", "u"):
        tipo = "user" if partes[0] in ("user", "u") else "r"
        salida.append(f"https://www.reddit.com/{tipo}/{partes[1]}/.rss")
    elif host in ("bsky.app",) and len(partes) >= 2 and partes[0] == "profile":
        salida.append(f"https://bsky.app/profile/{partes[1]}/rss")
    elif host.endswith("youtube.com") or host == "youtu.be":
        lista = parse_qs(p.query or "").get("list", [""])[0]
        if lista:
            salida.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={lista}")
        elif partes and partes[0] == "channel" and len(partes) >= 2:
            salida.append(f"https://www.youtube.com/feeds/videos.xml?channel_id={partes[1]}")
    elif host == "github.com" and len(partes) >= 2:
        base = f"https://github.com/{partes[0]}/{partes[1]}"
        if len(partes) >= 3 and partes[2] == "commits":
            salida.append(base + "/commits.atom")
        else:
            salida += [base + "/releases.atom", base + "/commits.atom"]
    elif host == "medium.com" and partes and partes[0].startswith("@"):
        salida.append(f"https://medium.com/feed/{partes[0]}")
    elif len(partes) >= 2 and partes[0] in ("c", "u") and "." in host:
        # Lemmy (y compatibles): /c/<comunidad> y /u/<usuario>.
        salida.append(f"{p.scheme}://{p.netloc}/feeds/{partes[0]}/{partes[1]}.xml")
    elif len(partes) == 1 and partes[0].startswith("@") and "." in host:
        # Mastodon y compatibles: /@usuario -> /@usuario.rss
        salida.append(f"{p.scheme}://{p.netloc}/{partes[0]}.rss")
    elif len(partes) == 2 and partes[0] == "tags" and "." in host:
        salida.append(f"{p.scheme}://{p.netloc}/tags/{partes[1]}.rss")
    return salida


def feeds_anunciados(html: str, base: str) -> List[Dict[str, str]]:
    """Todos los <link rel="alternate"> de RSS, Atom o JSON Feed de una página."""
    salida = []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")
        for link in soup.find_all("link"):
            rel = " ".join(link.get("rel", []) if isinstance(link.get("rel"), list)
                           else [link.get("rel") or ""]).lower()
            tipo = (link.get("type") or "").lower()
            href = link.get("href")
            if href and "alternate" in rel and any(t in tipo for t in ("rss", "atom", "xml", "json")):
                u = urljoin(base, href)
                if u not in [s["url"] for s in salida]:
                    salida.append({"url": u, "titulo": (link.get("title") or "").strip()})
    except Exception as e:
        log.debug(f"No se pudieron leer los feeds anunciados en {base}: {e}")
    return salida


class _DescubrirMixin:
    # --------------------------------------------------------------- descarga y prueba
    def _pedir_url(self, url: str, user_agent: str = "", timeout: int = 15) -> Dict[str, Any]:
        """Descarga una dirección para analizarla. Devuelve {estado, url_final, tipo, cuerpo,
        navegador(bool), redirige(bool), error}. Si la identificación de la app es rechazada
        (403/406), repite como navegador y lo indica."""
        from app.core.net import http_session
        res = {"estado": 0, "url_final": url, "tipo": "", "cuerpo": b"", "navegador": False,
               "redirige": False, "error": ""}
        intentos = [user_agent] if user_agent else ["", self.UA_NAVEGADOR]
        for ua in intentos:
            try:
                resp = http_session().get(url, timeout=timeout, headers=self._cabeceras_feed(ua),
                                          stream=True, allow_redirects=True)
                with resp:
                    res.update(estado=resp.status_code, url_final=resp.url or url,
                               tipo=(resp.headers.get("Content-Type") or "").lower(),
                               navegador=(ua == self.UA_NAVEGADOR),
                               redirige=bool(resp.history) and (resp.url or url) != url,
                               error="")
                    if resp.status_code in (403, 406) and not user_agent and ua == "":
                        continue   # probar como navegador
                    if resp.status_code < 400:
                        res["cuerpo"] = self._leer_cuerpo_limitado(resp, _MAX_BYTES)
                return res
            except Exception as e:
                res["error"] = str(e)
        return res

    def _analizar_cuerpo_feed(self, cuerpo: bytes):
        """Parsea un cuerpo como RSS/Atom (feedparser) o JSON Feed. Devuelve el resultado o
        None si no es un feed."""
        if not cuerpo:
            return None
        import feedparser
        try:
            parsed = feedparser.parse(cuerpo)
            if parsed.entries or parsed.feed.get("title"):
                return parsed
        except Exception:
            pass
        try:
            from .rss_parseo import _parsear_json_feed
            return _parsear_json_feed(cuerpo)
        except Exception:
            return None

    def probar_feed(self, url: str, user_agent: str = "") -> Dict[str, Any]:
        """Comprueba si una dirección es un feed. Devuelve {valido, url, titulo, n,
        ultimo, navegador, estado, redirige, error}."""
        r = self._pedir_url(url, user_agent)
        salida = {"valido": False, "url": r["url_final"] or url, "titulo": "", "n": 0,
                  "ultimo": "", "navegador": r["navegador"], "estado": r["estado"],
                  "redirige": r["redirige"], "error": r["error"], "_html": ""}
        parsed = self._analizar_cuerpo_feed(r["cuerpo"])
        if parsed is not None:
            entradas = list(getattr(parsed, "entries", None) or parsed.get("entries", []) or [])
            feed = getattr(parsed, "feed", None) or parsed.get("feed", {}) or {}
            ultimo = ""
            if entradas:
                e0 = entradas[0]
                ultimo = (e0.get("published") or e0.get("updated") or "") if hasattr(e0, "get") else ""
            salida.update(valido=True, titulo=(feed.get("title") or "").strip(), n=len(entradas),
                          ultimo=ultimo)
        elif r["cuerpo"] and ("html" in r["tipo"] or r["cuerpo"][:200].lstrip().lower().startswith(b"<!doctype html")):
            try:
                salida["_html"] = r["cuerpo"].decode("utf-8", "replace")
            except Exception:
                salida["_html"] = ""
        return salida

    # --------------------------------------------------------------- descubrir
    def descubrir_feeds(self, url: str, maximo: int = 8) -> List[Dict[str, Any]]:
        """Feeds válidos para una dirección cualquiera, en orden de preferencia: el de la red
        o servicio si se reconoce, la propia dirección, los anunciados en la página y las rutas
        habituales. Cada uno con el resultado de probar_feed y "origen"."""
        url = normalizar_entrada(url)
        if not url:
            return []
        encontrados, vistos = [], set()

        def _anotar(res, origen):
            clave = (res.get("url") or "").rstrip("/")
            if res.get("valido") and clave not in vistos:
                vistos.add(clave)
                res["origen"] = origen
                res.pop("_html", None)
                encontrados.append(res)

        # 1. Redes y servicios conocidos.
        sociales = convertir_url_social(url)
        if not sociales and "youtube.com" in url.lower():
            try:
                sociales = [self._resolver_feed_youtube(url)]
            except Exception:
                sociales = []
        for u in sociales:
            _anotar(self.probar_feed(u), "red")
        if encontrados:
            return encontrados[:maximo]

        # 2. La propia dirección. Si ni siquiera conecta y no lleva "www", se prueba con él:
        # muchos dominios solo tienen bien configurado el certificado con www (Diario de
        # Mallorca sin www da un error de SSL).
        directo = self.probar_feed(url)
        if not directo.get("estado") and directo.get("error"):
            p0 = urlparse(url)
            if p0.netloc and not p0.netloc.lower().startswith("www."):
                con_www = p0._replace(netloc="www." + p0.netloc).geturl()
                alt = self.probar_feed(con_www)
                if alt.get("estado"):
                    url, directo = con_www, alt
        if directo.get("valido"):
            _anotar(directo, "directo")
            return encontrados

        # 3. Anunciados en la página y 4. rutas habituales, probados en paralelo.
        base = directo.get("url") or url
        candidatos = [(c["url"], "enlace") for c in feeds_anunciados(directo.get("_html", ""), base)]
        p = urlparse(base)
        raiz = f"{p.scheme}://{p.netloc}"
        for ruta in RUTAS_HABITUALES:
            u = (raiz + ruta) if ruta.startswith("/") else (base.rstrip("/") + ruta)
            if u.rstrip("/") not in [c.rstrip("/") for c, _o in candidatos]:
                candidatos.append((u, "ruta"))
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            futuros = [(ex.submit(self.probar_feed, u), o) for u, o in candidatos]
            for fut, origen in futuros:
                try:
                    _anotar(fut.result(timeout=40), origen)
                except Exception:
                    pass
        return encontrados[:maximo]

    # --------------------------------------------------------------- diagnóstico
    def diagnosticar_url(self, url: str, user_agent: str = "") -> Dict[str, Any]:
        """Qué pasa con la dirección de un feed, en texto legible (para "Editar feed →
        Comprobar la dirección"). Devuelve {texto, valido, url_sugerida, navegador,
        candidatos}."""
        url = normalizar_entrada(url)
        r = self.probar_feed(url, "" if user_agent == self.UA_NAVEGADOR else user_agent)
        lineas, sugerida, candidatos = [], "", []
        if r["error"] and not r["estado"]:
            lineas.append(_("No se pudo conectar con la dirección: {0}").format(r["error"]))
        else:
            lineas.append(_("El servidor responde con el código {0}.").format(r["estado"]))
        if r["navegador"]:
            lineas.append(_("El servidor rechaza a las aplicaciones y solo responde a los navegadores: "
                            "el feed se leerá identificándose como navegador."))
        if r["redirige"] and r["url"] and r["url"].rstrip("/") != url.rstrip("/"):
            lineas.append(_("La dirección redirige a: {0}").format(r["url"]))
            sugerida = r["url"]
        if r["valido"]:
            lineas.append(_("Es un feed válido: «{0}», con {1} artículos.").format(r["titulo"] or url, r["n"]))
            if r["ultimo"]:
                lineas.append(_("El más reciente es de: {0}").format(r["ultimo"]))
        else:
            if r["estado"] in (404, 410):
                lineas.append(_("La dirección ya no existe en el servidor (el feed puede haberse movido)."))
            elif r["estado"] in (401, 403, 406) and not r["navegador"]:
                lineas.append(_("El servidor no deja leerlo, ni como aplicación ni como navegador."))
            elif r["estado"] and r["estado"] < 400:
                lineas.append(_("La dirección responde, pero no es un feed."))
            candidatos = self.descubrir_feeds(url)
            if candidatos:
                lineas.append(_("Feeds encontrados a partir de esa dirección:"))
                for c in candidatos:
                    lineas.append(f"- {c['titulo'] or c['url']} ({c['n']}): {c['url']}")
                if not sugerida:
                    sugerida = candidatos[0]["url"]
            else:
                lineas.append(_("No se ha encontrado ningún feed a partir de esa dirección."))
        return {"texto": "\n".join(lineas), "valido": r["valido"], "url_sugerida": sugerida,
                "navegador": r["navegador"] or any(c.get("navegador") for c in candidatos),
                "candidatos": candidatos}

    # --------------------------------------------------------------- editar
    def editar_feed(self, feed_id: int, titulo: Optional[str] = None, url: Optional[str] = None,
                    category_id: Any = "sin_cambio", custom_interval: Optional[int] = None,
                    custom_user_agent: Optional[str] = None, no_images: Optional[bool] = None,
                    force_fulltext: Optional[bool] = None, paused: Optional[bool] = None) -> bool:
        """Guarda los cambios de un feed. Si cambia la DIRECCIÓN, se comprueba que no esté ya
        suscrita en la cuenta y se reinicia su estado de descarga (caché condicional, errores,
        mudanza pendiente) para que la próxima actualización la lea de cero. Devuelve True si
        cambió la dirección."""
        from app.core.database import db
        from app.core.url_utils import normalizar_url
        fila = db.query_one("SELECT account_id, url FROM rss_feeds WHERE id = ?", (feed_id,))
        if not fila:
            raise ValueError(_("El feed ya no existe."))
        account_id, url_actual = fila
        _acc, _url, titulo_antes, carpeta_antes = self._datos_feed(feed_id)
        cambio_url = False
        if url is not None:
            url = normalizar_entrada(url)
            if url and url != url_actual:
                otro = self.buscar_feed_por_url(account_id, url)
                if otro and otro[0] != feed_id:
                    raise ValueError(_("Ya tienes esa dirección en otro feed: «{0}».").format(otro[1]))
                db.execute(
                    "UPDATE rss_feeds SET url = ?, url_normalized = ?, http_etag = NULL, "
                    "http_modified = NULL, body_hash = NULL, fail_count = 0, last_error = NULL, "
                    "last_status = NULL, moved_to = NULL, next_update_at = NULL WHERE id = ?",
                    (url, normalizar_url(url), feed_id))
                cambio_url = True
        if titulo:
            db.execute("UPDATE rss_feeds SET title = ? WHERE id = ?", (titulo.strip(), feed_id))
        if category_id != "sin_cambio":
            db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (category_id, feed_id))
        if paused is not None:
            self.set_feed_paused(feed_id, bool(paused))
        self.actualizar_ajustes_feed(feed_id, custom_interval=custom_interval,
                                     custom_user_agent=custom_user_agent, no_images=no_images,
                                     force_fulltext=force_fulltext)
        # Llevar los cambios al servidor de sincronización, si está activado.
        _acc, _url, titulo_ahora, carpeta_ahora = self._datos_feed(feed_id)
        if cambio_url:
            self.cambio_suscripcion(account_id, "baja", url_actual)
            self.sus_alta_feed(feed_id)
        else:
            if titulo_ahora != titulo_antes:
                self.sus_renombrar_feed(feed_id)
            if carpeta_ahora != carpeta_antes:
                self.sus_mover_feed(feed_id, carpeta_antes)
        return cambio_url
