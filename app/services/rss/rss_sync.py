# -*- coding: utf-8 -*-
"""Sincronización de RSS con servidores externos (mixin de RSSService).

Conecta una cuenta RSS local con un servidor de agregación para compartir estado entre
dispositivos. Dos protocolos, con CLIENTE PROPIO (sin librerías de terceros):

- **Google Reader API**: FreshRSS, Inoreader, BazQux, The Old Reader, Miniflux (modo GReader)...
- **Miniflux REST API**: nativo de Miniflux.

Modelo de "SINCRONIZACIÓN DE ESTADO" (no de contenido): el servidor aporta las
SUSCRIPCIONES (feeds/carpetas) y el ESTADO (qué está leído/destacado); los ARTÍCULOS los
sigue bajando el motor RSS local de cada feed. Así se reutiliza todo el pipeline existente
(parseo, FTS, reglas, scheduler) y el vínculo entre artículo local y servidor es la URL del
artículo NORMALIZADA (url_utils.normalizar_url), robusta frente a http/https, barra final y
parámetros de seguimiento.

CONCILIACIÓN EN TRES VÍAS (lo que hace que sea bidireccional de verdad): además del estado
local y del remoto se guarda, en `rss_sync_items`, el ÚLTIMO ESTADO CONOCIDO DEL SERVIDOR
para cada artículo (la "base"). Comparando los tres se sabe QUIÉN cambió:
  - cambió solo en local  -> se ENVÍA al servidor (edit-tag / PUT entries)
  - cambió solo en remoto -> se APLICA en local
  - cambiaron los dos     -> gana el servidor (es el estado compartido entre dispositivos)
Sin esa base no se puede distinguir "lo he leído aquí y aún no se ha enviado" de "lo han
marcado como no leído en la web", que es justo lo que hacía que el estado no viajara.

NO toca el arranque: la sincronización periódica corre en un hilo daemon propio que se
auto-arranca la primera vez que hay una cuenta con sync configurada.
"""
import threading
import time as _t
from datetime import datetime
from typing import Dict, Any, List

from app.core.database import db
from app.core.config_manager import config
from app.core.security import security
from app.core.net import http_session
from app.core.url_utils import normalizar_url

import builtins as _bi
if not hasattr(_bi, "_"):
    _bi._ = lambda s: s

import logging
log = logging.getLogger(__name__)

# Estados canónicos de Google Reader.
_GR_READ = "user/-/state/com.google/read"
_GR_STARRED = "user/-/state/com.google/starred"
_GR_READING_LIST = "user/-/state/com.google/reading-list"

# Cuántos artículos recientes del servidor se examinan en cada sincronización. La API de
# Google Reader no ofrece un "dime qué ha cambiado desde X", así que se barre una ventana
# de los más recientes (que es donde ocurre el 99 % de los cambios de estado). El límite
# por petición de contenidos es 1000 en la mayoría de servidores (The Old Reader incluido).
_VENTANA_ITEMS = 2000
_PAGINA = 1000


class SyncError(Exception):
    pass


def _url_clave(url: str) -> str:
    """Clave de emparejamiento entre un artículo local y uno del servidor."""
    return normalizar_url(url or "").lower()


def base_greader(url: str) -> str:
    """URL base del servidor Google Reader, tolerante con lo que pegue el usuario.

    El cliente añade luego '/reader/api/0/...', así que si la dirección configurada ya
    termina en ese prefijo (es lo que muestran muchas guías de The Old Reader) hay que
    quitarlo o saldría duplicado. Lo que NO se toca es el resto del camino: FreshRSS vive
    en '/api/greader.php' y eso sí forma parte de la base."""
    u = (url or "").strip().rstrip("/")
    for sufijo in ("/reader/api/0", "/reader/api", "/reader"):
        if u.endswith(sufijo):
            return u[: -len(sufijo)].rstrip("/")
    return u


# ---------------------------------------------------------------------------
# Cliente Google Reader API (propio)
# ---------------------------------------------------------------------------
class GReaderClient:
    """Cliente mínimo de la Google Reader API. `base` es la URL del servidor (p. ej.
    https://theoldreader.com o https://tu-freshrss/api/greader.php). Autentica con
    ClientLogin y cachea el token."""

    def __init__(self, base: str, usuario: str, password: str, timeout: int = 30):
        self.base = base_greader(base)
        self.usuario = usuario
        self.password = password
        self.timeout = timeout
        self._sid = None      # token de sesión (Auth)
        self._wtoken = None   # token de escritura (edit-tag)

    def _s(self):
        return http_session()

    def login(self):
        r = self._s().post(
            f"{self.base}/accounts/ClientLogin",
            data={"Email": self.usuario, "Passwd": self.password},
            timeout=self.timeout)
        if r.status_code != 200:
            raise SyncError(_("Usuario o contraseña incorrectos, o URL del servidor errónea."))
        for linea in r.text.splitlines():
            if linea.startswith("Auth="):
                self._sid = linea[5:].strip()
                return
        raise SyncError(_("El servidor no devolvió un token de acceso."))

    def _headers(self):
        if not self._sid:
            self.login()
        return {"Authorization": f"GoogleLogin auth={self._sid}"}

    def _get(self, ruta: str, params: dict = None):
        r = self._s().get(f"{self.base}/reader/api/0/{ruta}", headers=self._headers(),
                          params=params or {}, timeout=self.timeout)
        if r.status_code == 401:  # token caducado: reintentar una vez
            self._sid = None
            r = self._s().get(f"{self.base}/reader/api/0/{ruta}", headers=self._headers(),
                              params=params or {}, timeout=self.timeout)
        r.raise_for_status()
        return r

    def _token_escritura(self):
        """Token de escritura para edit-tag. Algunos servidores no lo exigen; si el
        endpoint no existe, se sigue sin él en vez de abortar el envío."""
        if self._wtoken is not None:
            return self._wtoken
        try:
            self._wtoken = self._get("token").text.strip()
        except Exception as e:
            log.debug(f"Sync GReader: el servidor no da token de escritura ({e}); se envía sin él.")
            self._wtoken = ""
        return self._wtoken

    def probar(self) -> bool:
        """Verifica las credenciales (login). Lanza SyncError si fallan."""
        self.login()
        return True

    def suscripciones(self) -> List[Dict[str, Any]]:
        """Feeds suscritos en el servidor: [{url, title, categorias:[...]}]."""
        datos = self._get("subscription/list", {"output": "json"}).json()
        salida = []
        for s in datos.get("subscriptions", []):
            cats = [c.get("label") or "" for c in (s.get("categories") or []) if c.get("label")]
            salida.append({"url": s.get("url", ""), "title": s.get("title", ""),
                           "categorias": cats})
        return salida

    # -- lectura de estado ---------------------------------------------------
    def _paginar(self, ruta: str, params: dict, clave: str, limite: int) -> List[dict]:
        """Recorre un endpoint paginado por 'continuation' hasta 'limite' elementos.

        IMPORTANTE: el identificador del stream va SIEMPRE en el parámetro `s`, NUNCA
        dentro de la ruta. Poner `stream/contents/user/-/state/com.google/read` en el path
        devuelve 404 en The Old Reader (sus rutas no admiten las barras del estado) y era
        la causa de que no llegara nada de lo leído en la web. Con `s=` funciona en todos
        (FreshRSS, Inoreader, BazQux, The Old Reader, Miniflux en modo GReader).
        """
        salida, cont = [], None
        p = dict(params)
        while len(salida) < limite:
            if cont:
                p["c"] = cont
            datos = self._get(ruta, p).json()
            items = datos.get(clave) or []
            if not items:
                break
            salida.extend(items)
            cont = datos.get("continuation")
            if not cont:
                break
        return salida[:limite]

    @staticmethod
    def _id_canonico(valor) -> str:
        """Los ids de item llegan en dos formatos según el endpoint: 'long form'
        (tag:google.com,2005:reader/item/XXXX) en stream/contents y a veces decimal con
        signo en stream/items/ids. Se reducen a una forma común para poder cruzarlos."""
        s = str(valor or "").strip()
        if not s:
            return ""
        if "/" in s:
            s = s.rsplit("/", 1)[-1]
        s = s.lower()
        # Decimal con signo -> hexadecimal de 16 (forma canónica de Google Reader). Solo se
        # convierte si es corto: un id ya hexadecimal de 16+ dígitos que por casualidad fuera
        # todo cifras (o un ObjectId de The Old Reader) se deja como está.
        if len(s) < 16 and s.lstrip("-").isdigit():
            try:
                s = format(int(s) & 0xFFFFFFFFFFFFFFFF, "016x")
            except ValueError:
                pass
        # Sin ceros a la izquierda, para que "0000000000000005" y "5" sean el mismo id.
        return s.lstrip("0") or "0"

    def _ids_no_leidos(self) -> set:
        """IDs de los artículos SIN LEER del servidor (barato: solo ids, no contenidos).
        Sirve como fuente de verdad del estado leído, sin depender de que el servidor
        rellene el array `categories` de cada item."""
        try:
            refs = self._paginar("stream/items/ids",
                                 {"output": "json", "n": 10000, "s": _GR_READING_LIST,
                                  "xt": _GR_READ},
                                 "itemRefs", 10000)
            return {self._id_canonico(r.get("id")) for r in refs if r.get("id")}
        except Exception as e:
            log.debug(f"Sync GReader: no se pudieron pedir los ids sin leer: {e}")
            return set()

    def estado_reciente(self, limite: int = _VENTANA_ITEMS) -> Dict[str, dict]:
        """Estado de los artículos recientes del servidor.

        Devuelve {url_normalizada: {"item_id": str, "read": bool, "starred": bool}}.
        Una sola pasada por el reading-list da la URL, el id del item (necesario para
        escribir con edit-tag) y su estado. El estado se toma de los ids sin leer cuando el
        servidor los facilita, y si no, del array `categories` de cada artículo."""
        items = self._paginar("stream/contents",
                              {"output": "json", "n": min(_PAGINA, limite),
                               "s": _GR_READING_LIST},
                              "items", limite)
        sin_leer = self._ids_no_leidos()
        # Solo se usan los ids sin leer si de verdad cruzan con los de los contenidos: si los
        # formatos no casaran (servidor exótico), se cae a `categories`, que siempre viene.
        canon = {self._id_canonico(it.get("id")) for it in items if it.get("id")}
        usar_ids = bool(sin_leer) and bool(sin_leer & canon)
        if sin_leer and not usar_ids:
            log.debug("Sync GReader: los ids sin leer no cruzan con los contenidos; "
                      "se usa el estado de cada artículo (categories).")
        salida = {}
        for it in items:
            url = ""
            for alt in (it.get("alternate") or []):
                if alt.get("href"):
                    url = alt["href"]
                    break
            clave = _url_clave(url)
            if not clave:
                continue
            cats = set(it.get("categories") or [])
            iid = it.get("id") or ""
            if usar_ids and iid:
                leido = self._id_canonico(iid) not in sin_leer
            else:
                leido = _GR_READ in cats
            salida[clave] = {"item_id": iid, "read": leido,
                             "starred": _GR_STARRED in cats}
        return salida

    # -- escritura de estado -------------------------------------------------
    def marcar(self, item_ids: List[str], estado: str, poner: bool):
        """Añade (a=) o quita (r=) un estado a una lista de items del servidor."""
        if not item_ids:
            return
        tok = self._token_escritura()
        clave = "a" if poner else "r"
        for i in range(0, len(item_ids), 250):
            lote = item_ids[i:i + 250]
            data = [(clave, estado)]
            if tok:
                data.insert(0, ("T", tok))
            data += [("i", x) for x in lote]
            r = self._s().post(f"{self.base}/reader/api/0/edit-tag", headers=self._headers(),
                               data=data, timeout=self.timeout)
            r.raise_for_status()

    def enviar_cambios(self, cambios: List[dict]) -> int:
        """Envía al servidor los cambios locales. `cambios`: [{item_id, campo, valor}] con
        campo 'read'|'starred'. Devuelve cuántos items se enviaron."""
        grupos = {(_GR_READ, True): [], (_GR_READ, False): [],
                  (_GR_STARRED, True): [], (_GR_STARRED, False): []}
        for c in cambios:
            iid = c.get("item_id")
            if not iid:
                continue
            estado = _GR_READ if c["campo"] == "read" else _GR_STARRED
            grupos[(estado, bool(c["valor"]))].append(iid)
        enviados = 0
        for (estado, poner), ids in grupos.items():
            if ids:
                self.marcar(ids, estado, poner)
                enviados += len(ids)
        return enviados


# ---------------------------------------------------------------------------
# Cliente Miniflux REST (propio)
# ---------------------------------------------------------------------------
class MinifluxClient:
    """Cliente mínimo de la API REST de Miniflux. Autentica con token (X-Auth-Token) o
    usuario/contraseña (Basic)."""

    def __init__(self, base: str, token: str = "", usuario: str = "", password: str = "",
                 timeout: int = 30):
        self.base = base.rstrip("/")
        self.token = token
        self.usuario = usuario
        self.password = password
        self.timeout = timeout

    def _s(self):
        return http_session()

    def _kw(self):
        kw = {"timeout": self.timeout}
        if self.token:
            kw["headers"] = {"X-Auth-Token": self.token}
        else:
            kw["auth"] = (self.usuario, self.password)
        return kw

    def probar(self) -> bool:
        r = self._s().get(f"{self.base}/v1/me", **self._kw())
        if r.status_code in (401, 403):
            raise SyncError(_("Credenciales de Miniflux incorrectas."))
        r.raise_for_status()
        return True

    def suscripciones(self) -> List[Dict[str, Any]]:
        r = self._s().get(f"{self.base}/v1/feeds", **self._kw())
        r.raise_for_status()
        salida = []
        for f in r.json():
            cat = (f.get("category") or {}).get("title")
            salida.append({"url": f.get("feed_url", ""), "title": f.get("title", ""),
                           "categorias": [cat] if cat else []})
        return salida

    def estado_reciente(self, limite: int = _VENTANA_ITEMS) -> Dict[str, dict]:
        """Igual contrato que GReaderClient.estado_reciente: en Miniflux cada entrada ya
        trae su id, su URL, su status ('read'/'unread') y si está marcada (starred)."""
        salida, offset = {}, 0
        while offset < limite:
            params = {"limit": min(250, limite - offset), "offset": offset,
                      "direction": "desc", "order": "published_at"}
            r = self._s().get(f"{self.base}/v1/entries", params=params, **self._kw())
            r.raise_for_status()
            datos = r.json()
            entradas = datos.get("entries", [])
            if not entradas:
                break
            for e in entradas:
                clave = _url_clave(e.get("url", ""))
                if clave:
                    salida[clave] = {"item_id": e.get("id"),
                                     "read": (e.get("status") == "read"),
                                     "starred": bool(e.get("starred"))}
            offset += len(entradas)
            if offset >= (datos.get("total") or 0):
                break
        return salida

    def enviar_cambios(self, cambios: List[dict]) -> int:
        """Cambia el estado en Miniflux: los 'read' en lote (PUT /v1/entries) y los
        destacados uno a uno (PUT /v1/entries/{id}/bookmark, que alterna)."""
        leidos = [c["item_id"] for c in cambios if c["campo"] == "read" and c["valor"]]
        no_leidos = [c["item_id"] for c in cambios if c["campo"] == "read" and not c["valor"]]
        enviados = 0
        for ids, estado in ((leidos, "read"), (no_leidos, "unread")):
            for i in range(0, len(ids), 100):
                lote = [x for x in ids[i:i + 100] if x is not None]
                if not lote:
                    continue
                r = self._s().put(f"{self.base}/v1/entries",
                                  json={"entry_ids": lote, "status": estado}, **self._kw())
                r.raise_for_status()
                enviados += len(lote)
        for c in cambios:
            if c["campo"] == "starred" and c.get("item_id") is not None:
                # El endpoint de Miniflux ALTERNA el marcador; solo se llama cuando el
                # estado local y el del servidor difieren, así que alternar es correcto.
                r = self._s().put(f"{self.base}/v1/entries/{c['item_id']}/bookmark", **self._kw())
                r.raise_for_status()
                enviados += 1
        return enviados


# ---------------------------------------------------------------------------
# Mixin de sincronización
# ---------------------------------------------------------------------------
class _SyncMixin:
    def _sync_init(self):
        if not hasattr(self, "_sync_lock"):
            self._sync_lock = threading.Lock()
            self._sync_worker = None

    def obtener_config_sync(self, account_id: int) -> Dict[str, Any]:
        """Config de sincronización de una cuenta (sin la contraseña). Vacío si no hay."""
        cfg = config.get("rss_sync", str(account_id), {}) or {}
        return dict(cfg) if isinstance(cfg, dict) else {}

    def configurar_sync(self, account_id: int, tipo: str, base_url: str,
                        usuario: str = "", password: str = "", token: str = "",
                        intervalo_min: int = 30, enviar_suscripciones: bool = False) -> bool:
        """Guarda la configuración de sync (tipo: 'greader'|'miniflux') y verifica que las
        credenciales funcionan ANTES de guardarlas. La contraseña/token va cifrada aparte."""
        self._sync_init()
        cliente = self._crear_cliente(tipo, base_url, usuario, password, token)
        cliente.probar()  # lanza SyncError si falla
        config.set("rss_sync", str(account_id), {
            "tipo": tipo, "base_url": base_url.rstrip("/"), "usuario": usuario,
            "intervalo_min": max(5, int(intervalo_min)), "activo": True,
            "usa_token": bool(token),
            # Altas, bajas, renombres y carpetas de los feeds también al servidor
            # (rss_sync_subs.py). Opcional y desactivado por defecto.
            "enviar_suscripciones": bool(enviar_suscripciones),
        })
        secreto = token or password
        if secreto:
            security.guardar_credencial("rss_sync", f"{account_id}:secreto", secreto)
        self._arrancar_sync_periodico()
        return True

    def desactivar_sync(self, account_id: int):
        cfg = self.obtener_config_sync(account_id)
        if cfg:
            cfg["activo"] = False
            config.set("rss_sync", str(account_id), cfg)

    def _crear_cliente(self, tipo, base_url, usuario, password, token):
        if tipo == "greader":
            return GReaderClient(base_url, usuario, password or token)
        if tipo == "miniflux":
            return MinifluxClient(base_url, token=token, usuario=usuario, password=password)
        raise SyncError(_("Tipo de sincronización desconocido: {0}").format(tipo))

    def _cliente_de_cuenta(self, account_id: int):
        cfg = self.obtener_config_sync(account_id)
        if not cfg or not cfg.get("activo"):
            return None
        secreto = security.obtener_credencial("rss_sync", f"{account_id}:secreto") or ""
        tipo = cfg.get("tipo")
        if tipo == "greader":
            return GReaderClient(cfg["base_url"], cfg.get("usuario", ""), secreto)
        if tipo == "miniflux":
            if cfg.get("usa_token"):
                return MinifluxClient(cfg["base_url"], token=secreto)
            return MinifluxClient(cfg["base_url"], usuario=cfg.get("usuario", ""), password=secreto)
        return None

    def sincronizar_cuenta(self, account_id: int) -> Dict[str, Any]:
        """Sincroniza UNA cuenta con su servidor: importa las suscripciones que falten y
        concilia el estado leído/destacado en AMBOS sentidos. Devuelve un resumen."""
        cliente = self._cliente_de_cuenta(account_id)
        if cliente is None:
            return {"ok": False, "error": _("sin configuración")}
        resumen = {"ok": True, "feeds_nuevos": 0, "leidos_aplicados": 0,
                   "destacados_aplicados": 0, "enviados": 0}
        # 1) Suscripciones -> crear feeds locales que falten (en carpeta por su etiqueta).
        try:
            for s in cliente.suscripciones():
                url = (s.get("url") or "").strip()
                if not url or self.buscar_feed_por_url(account_id, url):
                    continue
                cat_id = None
                if s.get("categorias"):
                    cat_id = self.agregar_categoria(account_id, s["categorias"][0])
                try:
                    self.agregar_feed(account_id, url, s.get("title") or url, cat_id)
                    resumen["feeds_nuevos"] += 1
                except Exception as e:
                    log.debug(f"Sync: no se pudo crear el feed {url}: {e}")
        except Exception as e:
            log.warning(f"Sync: fallo al traer suscripciones (cuenta {account_id}): {e}")
            resumen["ok"] = False
            resumen["error"] = str(e)[:200]
            return resumen
        # 2) Conciliar el estado en los dos sentidos.
        try:
            self._conciliar_estado(account_id, cliente, resumen)
        except Exception as e:
            log.warning(f"Sync: fallo al conciliar estado (cuenta {account_id}): {e}")
            resumen["ok"] = False
            resumen["error"] = str(e)[:200]
        config.set("rss_sync_last", str(account_id), datetime.now().isoformat())
        return resumen

    # ------------------------------------------------------------ conciliación
    def _articulos_locales(self, account_id: int, limite: int = 8000) -> Dict[str, tuple]:
        """{url_normalizada: (article_id, read, favorite)} de los artículos más recientes
        de la cuenta. Se emparejan con los del servidor por esa clave."""
        salida = {}
        try:
            filas = db.query(
                "SELECT id, link, COALESCE(read,0), COALESCE(favorite,0) FROM rss_articles "
                "WHERE account_id = ? ORDER BY published_at DESC LIMIT ?",
                (account_id, limite))
        except Exception as e:
            log.debug(f"Sync: no se pudieron leer los artículos locales: {e}")
            return salida
        for aid, link, leido, fav in filas:
            clave = _url_clave(link)
            # Si dos artículos comparten URL normalizada gana el más reciente (el primero).
            if clave and clave not in salida:
                salida[clave] = (aid, bool(leido), bool(fav))
        return salida

    def _base_conocida(self, account_id: int) -> Dict[str, tuple]:
        """Último estado del servidor que conocemos por artículo: {article_id: (read, star)}."""
        base = {}
        try:
            for aid, r, s in db.query(
                    "SELECT article_id, COALESCE(read_srv,0), COALESCE(star_srv,0) "
                    "FROM rss_sync_items WHERE account_id = ?", (account_id,)):
                base[aid] = (bool(r), bool(s))
        except Exception as e:
            log.debug(f"Sync: no se pudo leer la base de sincronización: {e}")
        return base

    def _conciliar_estado(self, account_id: int, cliente, resumen: dict):
        """Compara local, remoto y la base conocida y aplica los cambios a cada lado."""
        for clave_res in ("leidos_aplicados", "destacados_aplicados", "enviados"):
            resumen.setdefault(clave_res, 0)
        remoto = cliente.estado_reciente()
        if not remoto:
            log.info(f"Sync cuenta {account_id}: el servidor no devolvió artículos.")
            return
        locales = self._articulos_locales(account_id)
        base = self._base_conocida(account_id)

        a_leer, a_no_leer, a_destacar, a_quitar_destacado = [], [], [], []
        cambios_remotos = []          # [{item_id, campo, valor}]
        nueva_base = []               # [(account_id, article_id, item_id, read, star)]

        for clave, datos in remoto.items():
            local = locales.get(clave)
            if not local:
                continue  # el servidor lo tiene pero aquí aún no está descargado
            article_id, l_read, l_star = local
            r_read, r_star = bool(datos["read"]), bool(datos["starred"])
            b_read, b_star = base.get(article_id, (None, None))
            item_id = datos.get("item_id")

            # --- LEÍDO ---
            final_read = r_read
            if b_read is None:
                # Primer encuentro con este artículo: no sabemos quién cambió qué. Se toma
                # el estado del servidor, salvo que aquí ya esté leído (nunca se "des-lee"
                # algo que el usuario ya ha leído en este equipo: se envía al servidor).
                if l_read and not r_read:
                    cambios_remotos.append({"item_id": item_id, "campo": "read", "valor": True})
                    final_read = True
                elif r_read and not l_read:
                    a_leer.append(article_id)
            elif l_read != b_read and r_read == b_read:
                # Cambió solo aquí -> enviar al servidor.
                cambios_remotos.append({"item_id": item_id, "campo": "read", "valor": l_read})
                final_read = l_read
            elif r_read != b_read and l_read == b_read:
                # Cambió solo en el servidor -> aplicar aquí (marcar leído o NO leído).
                (a_leer if r_read else a_no_leer).append(article_id)
            elif l_read != r_read:
                # Cambiaron los dos de forma distinta: gana el servidor (estado compartido).
                (a_leer if r_read else a_no_leer).append(article_id)

            # --- DESTACADO ---
            final_star = r_star
            if b_star is None:
                if l_star and not r_star:
                    cambios_remotos.append({"item_id": item_id, "campo": "starred", "valor": True})
                    final_star = True
                elif r_star and not l_star:
                    a_destacar.append(article_id)
            elif l_star != b_star and r_star == b_star:
                cambios_remotos.append({"item_id": item_id, "campo": "starred", "valor": l_star})
                final_star = l_star
            elif r_star != b_star and l_star == b_star:
                (a_destacar if r_star else a_quitar_destacado).append(article_id)
            elif l_star != r_star:
                (a_destacar if r_star else a_quitar_destacado).append(article_id)

            nueva_base.append((account_id, article_id, item_id, int(final_read), int(final_star)))

        # Aplicar en local (por lotes).
        resumen["leidos_aplicados"] = (self._fijar_columna(a_leer, "read", 1)
                                       + self._fijar_columna(a_no_leer, "read", 0))
        resumen["destacados_aplicados"] = (self._fijar_columna(a_destacar, "favorite", 1)
                                           + self._fijar_columna(a_quitar_destacado, "favorite", 0))
        # Enviar al servidor lo que cambió aquí. Si falla el envío NO se guarda la base: así
        # se reintenta en la siguiente pasada en vez de dar por sincronizado lo que no llegó.
        enviado_ok = True
        if cambios_remotos:
            try:
                resumen["enviados"] = cliente.enviar_cambios(cambios_remotos)
            except Exception as e:
                enviado_ok = False
                log.warning(f"Sync: no se pudieron enviar {len(cambios_remotos)} cambios "
                            f"al servidor (cuenta {account_id}): {e}")
        if enviado_ok:
            self._guardar_base(nueva_base)
        self._limpiar_base_huerfana(account_id)
        log.info(f"Sync cuenta {account_id}: {len(remoto)} artículos en el servidor, "
                 f"{len(locales)} locales; aplicados {resumen['leidos_aplicados']} leídos y "
                 f"{resumen['destacados_aplicados']} destacados; enviados {resumen['enviados']}.")

    @staticmethod
    def _fijar_columna(ids: List[str], columna: str, valor: int) -> int:
        """UPDATE por lotes de read/favorite sobre una lista de artículos. Devuelve cuántas
        filas cambiaron de verdad (la condición evita reescribir lo que ya estaba igual)."""
        if not ids:
            return 0
        cambiados = 0
        for i in range(0, len(ids), 400):
            lote = ids[i:i + 400]
            ph = ",".join("?" for _ in lote)
            try:
                cambiados += db.execute(
                    f"UPDATE rss_articles SET {columna} = ? "
                    f"WHERE id IN ({ph}) AND COALESCE({columna},0) != ?",
                    (valor, *lote, valor)) or 0
            except Exception as e:
                log.debug(f"Sync: no se pudo aplicar {columna}={valor}: {e}")
        return cambiados

    @staticmethod
    def _guardar_base(filas: List[tuple]):
        """Guarda el estado del servidor ya conciliado (la base de la próxima comparación).

        En UNA transacción: son hasta un par de miles de filas y, sueltas, serían otros
        tantos commits con fsync en el disco del usuario. La red ya ha terminado antes de
        llegar aquí, así que no se abre ninguna transacción de escritura sobre la red."""
        if not filas:
            return
        try:
            with db.transaccion():
                for fila in filas:
                    db.execute(
                        "INSERT INTO rss_sync_items (account_id, article_id, item_id, "
                        "read_srv, star_srv) VALUES (?, ?, ?, ?, ?) "
                        "ON CONFLICT(account_id, article_id) DO UPDATE SET "
                        "item_id = excluded.item_id, read_srv = excluded.read_srv, "
                        "star_srv = excluded.star_srv", fila)
        except Exception as e:
            log.debug(f"Sync: no se pudo guardar la base de sincronización: {e}")

    @staticmethod
    def _limpiar_base_huerfana(account_id: int):
        """Quita de la base las filas de artículos que ya no existen (limpieza de antiguos,
        papelera, borrado de un feed). Si no, la tabla crecería sin fin."""
        try:
            db.execute(
                "DELETE FROM rss_sync_items WHERE account_id = ? AND NOT EXISTS "
                "(SELECT 1 FROM rss_articles a WHERE a.id = rss_sync_items.article_id)",
                (account_id,))
        except Exception as e:
            log.debug(f"Sync: no se pudo limpiar la base de sincronización: {e}")

    # -------------------------------------------------------------- periódico
    def iniciar_sync_al_arrancar(self) -> bool:
        """Arranca la sincronización periódica si alguna cuenta la tiene activa. La llama la
        ventana principal al abrirse: antes, el hilo solo se lanzaba al GUARDAR la
        configuración de sincronización, así que tras reiniciar la app no se sincronizaba
        sola hasta volver a guardarla. Devuelve True si hay algo que sincronizar."""
        if not self._cuentas_con_sync():
            return False
        self._arrancar_sync_periodico()
        return True

    def _arrancar_sync_periodico(self):
        self._sync_init()
        with self._sync_lock:
            if self._sync_worker is not None and self._sync_worker.is_alive():
                return
            self._sync_worker = threading.Thread(
                target=self._bucle_sync, name="rss-sync", daemon=True)
            self._sync_worker.start()

    def _cuentas_con_sync(self) -> List[int]:
        salida = []
        try:
            for (aid,) in db.query("SELECT id FROM accounts WHERE service_id='rss' AND active=1"):
                cfg = self.obtener_config_sync(aid)
                if cfg.get("activo"):
                    salida.append(aid)
        except Exception:
            pass
        return salida

    def _bucle_sync(self):
        """Hilo daemon: cada cuenta se sincroniza según su intervalo. Prioridad baja para
        no competir con la interfaz. Muere si ya no hay cuentas con sync."""
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)
        except Exception:
            pass
        ultimo = {}
        while True:
            cuentas = self._cuentas_con_sync()
            if not cuentas:
                return  # nada que sincronizar: el hilo muere; se recrea al configurar
            ahora = _t.time()
            for aid in cuentas:
                cfg = self.obtener_config_sync(aid)
                intervalo = max(5, int(cfg.get("intervalo_min", 30))) * 60
                if aid in ultimo and (ahora - ultimo[aid]) < intervalo:
                    continue
                ultimo[aid] = ahora
                try:
                    r = self.sincronizar_cuenta(aid)
                    log.info(f"Sync RSS cuenta {aid}: {r}")
                except Exception as e:
                    log.debug(f"Sync RSS cuenta {aid} falló: {e}")
                finally:
                    db.cerrar_lectura_hilo()
            _t.sleep(30)
