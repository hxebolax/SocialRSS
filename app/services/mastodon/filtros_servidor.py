"""Filtros del SERVIDOR de Mastodon (API v2) aplicados como lo hace el cliente web oficial.

El servidor NO quita de las respuestas los toots que casan con un filtro: les añade el campo
'filtered' (lista de FilterResult, cada uno con su filtro completo: id, título, contextos,
caducidad y acción). Es el CLIENTE quien decide, según la guía oficial
(documentacion_mastodon/content/en/api/guidelines.md, "Server-side filtering") y el código
de la web (app/javascript/mastodon/selectors/filters.ts):

- Solo cuentan los filtros cuyo CONTEXTO incluye el del buffer (home, notifications, public,
  thread, account) y que NO han caducado.
- Si alguno tiene acción 'hide', el toot NO se muestra en absoluto.
- Si no, con 'warn' el toot se ve solo como "[Filtrado: TÍTULO]" (nunca las palabras exactas)
  hasta que el usuario pide "Mostrar de todos modos" (revelar(), solo en la sesión).
- 'blur' (Mastodon 4.4+) solo protege la multimedia: el texto se muestra, pero no se lee el
  texto alternativo, no suena al enfocar y se pide confirmación antes de abrirla.
- Un hashtag guardado ("search:#tag") es contexto público normal; solo las búsquedas de texto
  avisan en vez de ocultar.
- Acción desconocida = 'warn'. Los toots PROPIOS nunca se filtran.
- En vista de detalle, favoritos, marcadores y búsquedas se AVISA en vez de ocultar (el
  usuario los ha pedido expresamente).

En la base se guarda en mastodon_statuses.filtered_by un JSON con esos filtros (antes era
solo el título en texto: se sigue entendiendo, como 'warn'). Para respetar los filtros que el
usuario borra o edita, se mantiene en memoria la lista VIGENTE de filtros de cada cuenta
(se refresca al gestionar filtros y con el evento 'filters_changed' del streaming): si está
cargada, manda sobre lo guardado en el toot.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()
# account_id -> {filter_id(str): {"title", "action", "context", "expires_at"}}
_vigentes: Dict[int, Dict[str, Dict[str, Any]]] = {}


def _norm_filtro(f: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(f, dict):
        return None
    fid = f.get("id")
    if fid is None:
        return None
    exp = f.get("expires_at")
    if isinstance(exp, datetime):
        exp = exp.isoformat()
    return {
        "id": str(fid),
        "title": f.get("title") or f.get("phrase") or "",
        "action": f.get("filter_action") or f.get("action") or "warn",
        "context": [str(c) for c in (f.get("context") or [])],
        "expires_at": str(exp) if exp else "",
    }


def serializar_filtrados(raw_filtered) -> Optional[str]:
    """Convierte el 'filtered' de la API en el JSON que se guarda en filtered_by."""
    res = []
    vistos = set()
    for fr in (raw_filtered or []):
        filt = fr.get("filter") if isinstance(fr, dict) else None
        n = _norm_filtro(filt) if isinstance(filt, dict) else None
        if n and n["id"] not in vistos:
            vistos.add(n["id"])
            res.append(n)
    return json.dumps(res, ensure_ascii=False) if res else None


def actualizar_vigentes(account_id: int, filtros: List[Dict[str, Any]]):
    """Fija la lista vigente de filtros de la cuenta (formato de obtener_filtros_servidor_async
    o el de la API). Solo filtros v2: los v1 no tienen id compatible con 'filtered'."""
    if any(isinstance(f, dict) and f.get("v2") is False for f in (filtros or [])):
        # Respuesta de la API v1 (servidor antiguo o fallo puntual de la v2): no sirve para
        # cruzar con 'filtered'; se usa lo guardado en cada toot.
        olvidar_vigentes(account_id)
        return
    mapa = {}
    for f in (filtros or []):
        n = _norm_filtro(f)
        if n:
            mapa[n["id"]] = n
    with _lock:
        _vigentes[account_id] = mapa


def olvidar_vigentes(account_id: int):
    with _lock:
        _vigentes.pop(account_id, None)


def tiene_vigentes(account_id: int) -> bool:
    with _lock:
        return account_id in _vigentes


def contexto_de_timeline(timeline: str) -> Dict[str, Any]:
    """Contexto de filtro del servidor y si se avisa en vez de ocultar, según el buffer
    (mismo mapeo que toServerSideType de la web oficial)."""
    t = str(timeline or "")
    if t in ("home", "bookmarks", "favourites") or t.startswith("list:"):
        ctx = "home"
    elif t in ("notifications", "mentions"):
        ctx = "notifications"
    elif t == "thread" or t.startswith("thread:"):
        ctx = "thread"
    elif t.startswith("user:"):
        ctx = "account"
    else:
        ctx = "public"     # local, federada, tendencias, hashtags, búsquedas, directos
    # Solo las BÚSQUEDAS de texto avisan en vez de ocultar (la web: contextType 'search'). Un
    # HASHTAG guardado ("search:#tag") es una columna pública normal: ahí 'hide' oculta.
    avisar = (t in ("bookmarks", "favourites")
              or (t.startswith("search:") and not t.startswith("search:#")))
    return {"contexto": ctx, "avisar_en_vez_de_ocultar": avisar}


def _caducado(expires_at: str, ahora: datetime) -> bool:
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= ahora
    except Exception:
        return False


def evaluar(filtered_by: Optional[str], account_id: int, timeline: str,
            foco_detalle: bool = False, fresco: bool = False) -> Dict[str, Any]:
    """Decide qué hacer con un toot según sus filtros del servidor en este buffer.

    Devuelve {"accion": None|"warn"|"hide", "titulos": [...], "titulos_media": [...]}:
    'titulos' son los filtros que avisan (warn, u hide convertido en aviso) y
    'titulos_media' los de acción 'blur' (solo afectan a la multimedia)."""
    vacio = {"accion": None, "titulos": [], "titulos_media": []}
    if not filtered_by:
        return vacio
    txt = str(filtered_by).strip()
    with _lock:
        vigentes = _vigentes.get(account_id)
    if not txt.startswith("["):
        # Formato antiguo (solo los TÍTULOS, guardado antes de conocer la acción): se cruza
        # por título con los filtros vigentes; sin ellos cargados, solo se puede avisar.
        if vigentes is None:
            return {"accion": "warn", "titulos": [txt], "titulos_media": []}
        por_titulo = {f.get("title"): f for f in vigentes.values()}
        nombres = [txt] if txt in por_titulo else [t.strip() for t in txt.split(", ")]
        guardados = [por_titulo[n] for n in nombres if n in por_titulo]
    else:
        try:
            guardados = json.loads(txt)
        except Exception:
            return vacio
    info = contexto_de_timeline(timeline)
    ctx = info["contexto"]
    avisar = info["avisar_en_vez_de_ocultar"] or foco_detalle
    ahora = datetime.now(timezone.utc)
    accion = None
    titulos, titulos_media = [], []
    for g in guardados:
        if not isinstance(g, dict):
            continue
        f = g
        if vigentes is not None:
            f = vigentes.get(str(g.get("id")))
            if f is None:
                if not fresco:     # el usuario borró ese filtro
                    continue
                f = g              # recién llegado del servidor: filtro nuevo aún no listado

        if ctx not in (f.get("context") or []):
            continue
        if _caducado(f.get("expires_at") or "", ahora):
            continue
        act = f.get("action") or "warn"
        titulo = f.get("title") or ""
        if act == "blur":
            titulos_media.append(titulo)
            continue
        if act == "hide" and not avisar:
            accion = "hide"
        elif accion is None:
            accion = "warn"
        titulos.append(titulo)
    return {"accion": accion, "titulos": list(dict.fromkeys(t for t in titulos if t)),
            "titulos_media": list(dict.fromkeys(t for t in titulos_media if t))}


def segmento_aviso(ev: Dict[str, Any], tiene_media: bool = False) -> List[str]:
    """Textos de aviso para la lista ('[Filtro: X]' y, si procede, el de multimedia)."""
    res = []
    if ev.get("accion") == "warn" and ev.get("titulos"):
        res.append(_("[Filtro: {0}]").format(", ".join(ev["titulos"])))
    if tiene_media and ev.get("titulos_media"):
        res.append(_("[Multimedia filtrada: {0}]").format(", ".join(ev["titulos_media"])))
    return res


# --- "Mostrar de todos modos" (revelado por publicación, solo en esta sesión) ---------------
# Como el botón "Mostrar de todos modos" de la web: un toot con acción 'warn' se ve en la lista
# solo como "[Filtrado: título]" (sin leer el cuerpo) hasta que el usuario lo pide; uno con
# 'blur' no lee el texto alternativo ni suena su multimedia hasta entonces. La clave es el db_id
# de la fila ("cuenta:status_id"). No se guarda en disco: al reiniciar vuelve a ocultarse.
_revelados = set()


def revelar(clave) -> None:
    if clave:
        with _lock:
            _revelados.add(str(clave))


def esta_revelado(clave) -> bool:
    if not clave:
        return False
    with _lock:
        return str(clave) in _revelados


def texto_filtrado(ev: Dict[str, Any]) -> str:
    """Lo que se ve en la lista en lugar del cuerpo de un toot con acción 'warn'."""
    return _("[Filtrado: {0}]").format(", ".join(ev.get("titulos") or []) or _("filtro del servidor"))


def oculta_texto(ev: Dict[str, Any], clave=None) -> bool:
    """True si el cuerpo del toot debe sustituirse por '[Filtrado: título]' (warn sin revelar)."""
    return bool(ev.get("accion") == "warn" and ev.get("titulos") and not esta_revelado(clave))


def media_filtrada(ev: Dict[str, Any], clave=None) -> bool:
    """True si la multimedia del toot está protegida (blur, o warn con el texto oculto) y no se
    ha revelado: no se lee el texto alternativo, no suena al enfocar y se pide confirmación
    antes de abrirla o reproducirla."""
    if esta_revelado(clave):
        return False
    return bool(ev.get("titulos_media")) or oculta_texto(ev, clave)


def esta_filtrado(ev: Dict[str, Any]) -> bool:
    """True si hay algo que revelar (aviso de texto o multimedia filtrada)."""
    return bool((ev.get("accion") == "warn" and ev.get("titulos")) or ev.get("titulos_media"))


def cita_filtrada(quote_json, account_id: int, timeline: str) -> bool:
    """True si la publicación CITADA (quote_json con 'filtered') tiene un aviso que revelar."""
    try:
        q = json.loads(quote_json) if quote_json else None
    except Exception:
        return False
    if not isinstance(q, dict) or not q.get("filtered"):
        return False
    ev = evaluar(q.get("filtered"), account_id, timeline)
    return bool(ev.get("accion") == "warn" and ev.get("titulos"))


def segmentos_lista(segmentos: List[str], ev: Dict[str, Any], tiene_media: bool = False,
                    clave=None, n_fijos: int = 0) -> List[str]:
    """Aplica el filtro del servidor a los segmentos de texto de una fila de la lista.

    Con 'warn' sin revelar, el cuerpo (y la cita, la tarjeta...) se sustituye por
    '[Filtrado: título]'; se conservan solo los 'n_fijos' primeros (p. ej. "Impulsado de X").
    Revelado (o sin warn), se antepone el aviso '[Filtro: X]' / '[Multimedia filtrada: Y]'."""
    if oculta_texto(ev, clave):
        return list(segmentos[:n_fijos]) + [texto_filtrado(ev)]
    return segmento_aviso(ev, tiene_media) + list(segmentos)


def contenido_con_filtro(contenido: str, ev: Dict[str, Any], clave=None) -> str:
    """Para textos de una sola pieza (notificaciones, visor de conversación, perfiles):
    '[Filtrado: título]' si el texto está oculto, o el texto con '[Filtro: X]' delante si se
    ha revelado."""
    if not contenido:
        return contenido
    if oculta_texto(ev, clave):
        return texto_filtrado(ev)
    if ev.get("accion") == "warn" and ev.get("titulos"):
        return _("[Filtro: {0}]").format(", ".join(ev["titulos"])) + " " + contenido
    return contenido


def evento_media_filtrado(ev: Dict[str, Any], evento, clave=None):
    """Evento de sonido de multimedia de la fila: None si la multimedia está protegida."""
    return None if media_filtrada(ev, clave) else evento


def lineas_detalle(ev: Dict[str, Any]) -> List[str]:
    """Líneas de aviso para las vistas de detalle (Ver publicación, panel de detalles...)."""
    res = []
    if ev.get("titulos") and ev.get("accion") in ("warn", "hide"):
        res.append(_("Filtrada por tu servidor: {0}").format(", ".join(ev["titulos"])))
    if ev.get("titulos_media"):
        res.append(_("Multimedia filtrada por tu servidor: {0}").format(", ".join(ev["titulos_media"])))
    return res


def serializar_de_raw(raw: Dict[str, Any], me_id=None) -> Optional[str]:
    """filtered_by de un status crudo de la API (en un impulso manda el del ORIGINAL; los toots
    propios nunca se filtran)."""
    if not isinstance(raw, dict):
        return None
    orig = raw.get("reblog") if isinstance(raw.get("reblog"), dict) else None
    filtrados = (orig or {}).get("filtered") or raw.get("filtered") or []
    autor = str(((orig or raw).get("account") or {}).get("id") or "")
    if not filtrados or (me_id is not None and autor == str(me_id)):
        return None
    return serializar_filtrados(filtrados)


def evaluar_raw(raw: Dict[str, Any], account_id: int, timeline: str, foco_detalle: bool = False,
                me_id=None) -> Dict[str, Any]:
    """evaluar() para un status crudo de la API (visor de conversación, notificaciones
    agrupadas...), sin pasar por la base de datos."""
    return evaluar(serializar_de_raw(raw, me_id), account_id, timeline, foco_detalle, fresco=True)


def evaluar_db_id(db_id, account_id: int, timeline: str, foco_detalle: bool = False) -> Dict[str, Any]:
    """evaluar() de una fila de mastodon_statuses por su db_id."""
    try:
        from app.core.database import db
        fila = db.query_one("SELECT filtered_by FROM mastodon_statuses WHERE id = ?", (db_id,))
    except Exception:
        fila = None
    return evaluar(fila[0] if fila else None, account_id, timeline, foco_detalle)


# --- Edición de filtros (API v2) -------------------------------------------------------------
def construir_keywords_attributes(palabras: List[str], previas: Optional[List[Dict[str, Any]]],
                                  whole_word=True) -> List[Dict[str, Any]]:
    """keywords_attributes para PUT /api/v2/filters/:id a partir de las palabras del formulario.

    - Una palabra que ya existía se reenvía con su id ({id, keyword, whole_word}), así el
      cambio de 'solo palabras completas' se aplica también a las existentes.
    - Las palabras previas que ya no están se REUTILIZAN para las nuevas (renombrar por id, en
      orden) y las que sobran se borran con '_destroy'.
    - whole_word=None conserva el valor de cada palabra existente (las nuevas, True)."""
    previas = [k for k in (previas or []) if isinstance(k, dict) and k.get("id")]
    nuevas = [p.strip() for p in (palabras or []) if (p or "").strip()]
    libres = list(previas)
    asignadas = []          # (palabra, previa o None)
    for p in nuevas:
        prev = next((k for k in libres if (k.get("keyword") or "") == p), None)
        if prev is not None:
            libres.remove(prev)
        asignadas.append([p, prev])
    for par in asignadas:   # renombrar: palabras sin pareja toman el id de una previa libre
        if par[1] is None and libres:
            par[1] = libres.pop(0)
    atributos = []
    for p, prev in asignadas:
        if whole_word is None:
            ww = bool(prev.get("whole_word")) if (prev and prev.get("keyword") == p) else True
        else:
            ww = bool(whole_word)
        if prev is not None:
            atributos.append({"id": prev["id"], "keyword": p, "whole_word": ww})
        else:
            atributos.append({"keyword": p, "whole_word": ww})
    for k in libres:
        atributos.append({"id": k["id"], "_destroy": True})
    return atributos


def fecha_legible(expires_at) -> str:
    """'17/10/2026 14:30' (hora local) de una fecha ISO de la API, o '' si no hay."""
    if not expires_at:
        return ""
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(expires_at)


def caducado(expires_at) -> bool:
    return _caducado(str(expires_at or ""), datetime.now(timezone.utc))


def describir_caducidad(expires_at) -> str:
    """Texto de la columna Caduca de la lista de filtros."""
    if not expires_at:
        return _("Nunca")
    if caducado(expires_at):
        return _("{0} (caducado)").format(fecha_legible(expires_at))
    return fecha_legible(expires_at)


def refrescar_vigentes_async(account_id: int, callback=None):
    """Recarga en segundo plano la lista vigente de filtros de la cuenta."""
    try:
        from app.services.mastodon.mastodon_service import mastodon_service
    except Exception:
        return

    def _fin(res, err):
        if not err and isinstance(res, list):
            actualizar_vigentes(account_id, res)
        else:
            log.debug("No se pudo refrescar los filtros del servidor de la cuenta %s: %s",
                      account_id, err)
        if callback:
            try:
                callback(res, err)
            except Exception:
                pass
    mastodon_service.obtener_filtros_servidor_async(account_id, callback=_fin)


_pedidos = set()


def asegurar_vigentes(account_id: int, al_cargar=None):
    """Pide UNA vez por sesión y cuenta la lista vigente de filtros (en segundo plano).
    'al_cargar' (opcional) se llama cuando llega, para repintar con los datos buenos."""
    if not account_id:
        return
    with _lock:
        if account_id in _vigentes or account_id in _pedidos:
            return
        _pedidos.add(account_id)

    def _fin(res, err):
        if err:
            with _lock:
                _pedidos.discard(account_id)   # se reintentará en la próxima carga
            return
        if al_cargar:
            try:
                al_cargar()
            except Exception:
                pass
    refrescar_vigentes_async(account_id, callback=_fin)


def _es_buffer_filtrable(timeline) -> bool:
    t = str(timeline or "")
    return (t in ("home", "local", "public", "direct", "bookmarks", "favourites", "trending",
                  "notifications", "mentions")
            or t.startswith(("search:", "list:", "user:", "thread:")))


def repintar_buffer_activo(frame, account_id: int):
    """Repinta desde la caché el buffer activo si es de esa cuenta y admite filtros."""
    import wx

    def _ui():
        try:
            if (getattr(frame, "active_mastodon_account_id", None) == account_id
                    and _es_buffer_filtrable(getattr(frame, "active_timeline_type", ""))
                    and not getattr(frame, "_cargando_buffers", False)):
                frame._cargar_listado_toots(cache_only=True)
        except Exception as e:
            log.debug("No se pudo repintar tras cambiar los filtros: %s", e)
    wx.CallAfter(_ui)


def ids_para_recalcular(account_id: int, timeline: str, visibles, limite: int = 200) -> List[str]:
    """db_ids que hay que volver a pedir al servidor tras cambiar los filtros: los VISIBLES del
    buffer (un filtro nuevo los puede ocultar) y también los que YA tienen algún filtro guardado
    en ese buffer, aunque estén ocultos (si quitas una palabra del filtro deben volver)."""
    pref = f"{account_id}:"
    res = [d for d in (visibles or []) if isinstance(d, str) and d.startswith(pref)][:limite]
    try:
        from app.core.database import db
        if timeline in ("notifications", "mentions"):
            filas = db.query(
                "SELECT s.id FROM mastodon_notifications n JOIN mastodon_statuses s "
                "ON s.account_id = n.account_id AND s.status_id = n.status_id "
                "WHERE n.account_id = ? AND s.filtered_by IS NOT NULL "
                "ORDER BY n.created_at DESC LIMIT ?", (account_id, limite))
        else:
            filas = db.query(
                "SELECT s.id FROM mastodon_timeline_statuses t JOIN mastodon_statuses s "
                "ON s.id = t.status_db_id WHERE t.account_id = ? AND t.timeline = ? "
                "AND s.filtered_by IS NOT NULL ORDER BY s.created_at DESC LIMIT ?",
                (account_id, timeline, limite))
        vistos = set(res)
        for (d,) in filas:
            if d not in vistos:
                vistos.add(d)
                res.append(d)
    except Exception as e:
        log.debug("No se pudieron leer los toots filtrados del buffer: %s", e)
    return res


def recalcular_buffer_activo(frame, account_id: int):
    """Tras cambiar los filtros (en el diálogo o desde otro cliente, evento 'filters_changed'):
    recarga la lista vigente, vuelve a pedir AL SERVIDOR las publicaciones visibles del buffer
    activo (el servidor recalcula su 'filtered') y repinta desde la caché. Así un filtro
    recién creado oculta ya lo que estaba en pantalla, y uno borrado lo vuelve a mostrar."""
    from app.services.mastodon.mastodon_service import mastodon_service

    def _repintar(*_a):
        repintar_buffer_activo(frame, account_id)

    def _tras_vigentes(res, err):
        ids = []
        try:
            if getattr(frame, "active_mastodon_account_id", None) == account_id:
                ids = ids_para_recalcular(
                    account_id, str(getattr(frame, "active_timeline_type", "") or ""),
                    list(getattr(frame, "lista_items_db_ids", []) or []))
        except Exception:
            ids = []
        if ids:
            mastodon_service.refrescar_contadores_async(account_id, ids, callback=_repintar)
        else:
            _repintar()
    refrescar_vigentes_async(account_id, callback=_tras_vigentes)
