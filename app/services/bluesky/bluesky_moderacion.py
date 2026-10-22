# -*- coding: utf-8 -*-
"""Motor de MODERACIÓN de Bluesky / AT Protocol, réplica del oficial (packages/api/src/
moderation: decision.ts, subjects/post.ts, account.ts, profile.ts, util.ts y
definitions/labels.json).

A partir de los DATOS de una publicación (etiquetas con su origen, autor, cita, textos para
las palabras silenciadas: ver `bluesky_moddatos`), de las PREFERENCIAS del usuario (las mismas
de la app oficial: contenido adulto, ocultar/avisar/ignorar por etiqueta global y por
servicio de etiquetado, definiciones propias de cada servicio, palabras silenciadas,
publicaciones ocultadas) y del CONTEXTO (lista, búsqueda o detalle), decide:

- filtrar:          no mostrar (ni indexar).
- difuminar:        sustituir el TEXTO por un aviso (se puede revelar en detalle salvo
                    no_override).
- difuminar_media:  la multimedia se oculta (sin alt, sin sonido, confirmar antes de abrir),
                    el texto se lee (etiquetas "blurs: media": porn, sexual, nudity,
                    graphic-media...).
- avisar:           prefijo informativo (severity alert/inform).
- no_override:      no se puede revelar (!hide, o contenido adulto desactivado).

Funciones PURAS (sin wx ni red). Las preferencias las trae el servicio (prefs_moderacion) ya
normalizadas con `normalizar_prefs` / `parsear_preferencias`.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import re

from app.services.bluesky.bluesky_mutewords import (
    normalizar_palabra, palabras_de_post, valores_coincidentes)

# Servicio de moderación de Bluesky: el "app labeler", siempre activo y con capacidad de
# retirada (";redact" en la cabecera). No se puede dar de baja.
BSKY_LABELER_DID = "did:plc:ar7c4by46qjdydhdevvrndac"

# Valores de etiqueta personalizados válidos (CUSTOM_LABEL_VALUE_RE del oficial).
_CUSTOM_RE = re.compile(r"^[a-z-]+$")

# Etiquetas antiguas que el oficial remapea al leer (B16).
ALIAS = {"gore": "graphic-media", "nsfw": "porn", "suggestive": "sexual"}
ALIAS_LEGADO = {"graphic-media": "gore", "porn": "nsfw", "sexual": "suggestive"}

_B_CONTENIDO_OCULTO = {
    "account": {"profileList": "blur", "profileView": "blur", "avatar": "blur",
                "banner": "blur", "displayName": "blur", "contentList": "blur",
                "contentView": "blur"},
    "profile": {"avatar": "blur", "banner": "blur", "displayName": "blur"},
    "content": {"contentList": "blur", "contentView": "blur"},
}
_B_MEDIA = {
    "account": {"avatar": "blur", "banner": "blur"},
    "profile": {"avatar": "blur", "banner": "blur"},
    "content": {"contentMedia": "blur"},
}

# definitions/labels.json (las globales del protocolo).
LABELS = {
    "!hide": {"identifier": "!hide", "configurable": False, "defaultSetting": "hide",
              "flags": ["no-override", "no-self"], "severity": "alert", "blurs": "content",
              "behaviors": _B_CONTENIDO_OCULTO},
    "!warn": {"identifier": "!warn", "configurable": False, "defaultSetting": "warn",
              "flags": ["no-self"], "severity": "none", "blurs": "content",
              "behaviors": {
                  "account": {"profileList": "blur", "profileView": "blur", "avatar": "blur",
                              "banner": "blur", "contentList": "blur", "contentView": "blur"},
                  "profile": {"avatar": "blur", "banner": "blur", "displayName": "blur"},
                  "content": {"contentList": "blur", "contentView": "blur"}}},
    "!no-unauthenticated": {"identifier": "!no-unauthenticated", "configurable": False,
                            "defaultSetting": "hide", "flags": ["no-override", "unauthed"],
                            "severity": "none", "blurs": "content",
                            "behaviors": _B_CONTENIDO_OCULTO},
    "porn": {"identifier": "porn", "configurable": True, "defaultSetting": "hide",
             "flags": ["adult"], "severity": "none", "blurs": "media", "behaviors": _B_MEDIA},
    "sexual": {"identifier": "sexual", "configurable": True, "defaultSetting": "warn",
               "flags": ["adult"], "severity": "none", "blurs": "media", "behaviors": _B_MEDIA},
    "nudity": {"identifier": "nudity", "configurable": True, "defaultSetting": "ignore",
               "flags": [], "severity": "none", "blurs": "media", "behaviors": _B_MEDIA},
    "graphic-media": {"identifier": "graphic-media", "configurable": True,
                      "defaultSetting": "warn", "flags": ["adult"], "severity": "none",
                      "blurs": "media", "behaviors": _B_MEDIA},
}

# DEFAULT_LABEL_SETTINGS del oficial (las configurables globales).
DEFAULT_LABEL_SETTINGS = {"porn": "hide", "sexual": "warn", "nudity": "ignore",
                          "graphic-media": "warn"}

# Etiquetas globales configurables, en el orden de la app oficial (para el diálogo).
GLOBALES_CONFIGURABLES = ("porn", "sexual", "graphic-media", "nudity")

# Nombres legibles (accesibles) de las globales y de las más comunes de Bluesky Moderation.
NOMBRES = {
    "porn": "contenido adulto", "sexual": "contenido sexual", "nudity": "desnudos",
    "graphic-media": "contenido gráfico", "gore": "contenido gráfico",
    "nsfw": "contenido adulto", "suggestive": "contenido sexual",
    "spam": "spam", "!warn": "advertencia general", "!hide": "contenido retirado",
    "impersonation": "suplantación", "intolerant": "intolerancia",
    "threat": "amenazas", "rude": "contenido grosero", "misleading": "engañoso",
    "scam": "estafa", "self-harm": "autolesiones", "extremist": "extremismo",
    "sensitive": "contenido sensible", "security": "problema de seguridad",
    "engagement-farming": "búsqueda de interacciones", "harassment": "acoso",
}

VISIBILIDADES = ("ignore", "warn", "hide")

# Comportamientos fijos del oficial (types.ts).
_MUTE_BEHAVIOR = {"profileList": "inform", "profileView": "alert", "contentList": "blur",
                  "contentView": "inform"}
_BLOCK_BEHAVIOR = {"profileList": "blur", "profileView": "alert", "avatar": "blur",
                   "banner": "blur", "contentList": "blur", "contentView": "blur"}
_MUTEWORD_BEHAVIOR = {"contentList": "blur", "contentView": "blur"}
_HIDE_BEHAVIOR = {"contentList": "blur", "contentView": "blur"}

_CONTEXTOS = {"lista": "contentList", "busqueda": "contentList", "detalle": "contentView",
              "media": "contentMedia"}


# ---------------------------------------------------------------------------
# Definiciones de etiqueta
# ---------------------------------------------------------------------------
def _g(obj, *nombres, default=None):
    for n in nombres:
        if obj is None:
            return default
        if isinstance(obj, dict):
            if n in obj:
                return obj[n]
        elif hasattr(obj, n):
            return getattr(obj, n)
    return default


def interpretar_definicion(defn, labeler_did: str = "") -> dict:
    """interpretLabelValueDefinition del oficial: una labelValueDefinition de un servicio de
    etiquetado (modelo o dict) como definición con comportamientos por objetivo. Añade "name"
    y "description" en español si el servicio los da (locales), si no en inglés o el valor."""
    ident = str(_g(defn, "identifier", default="") or "")
    severity = str(_g(defn, "severity", default="") or "")
    blurs = str(_g(defn, "blurs", default="") or "")
    adult = bool(_g(defn, "adultOnly", "adult_only", default=False))
    aoi = "alert" if severity == "alert" else ("inform" if severity == "inform" else None)
    acc, prof, cont = {}, {}, {}

    def _pon(d, k, v):
        if v:
            d[k] = v
    if blurs == "content":
        _pon(acc, "profileList", aoi); _pon(acc, "profileView", aoi)
        acc["contentList"] = "blur"
        _pon(acc, "contentView", "blur" if adult else aoi)
        _pon(prof, "profileList", aoi); _pon(prof, "profileView", aoi)
        cont["contentList"] = "blur"
        _pon(cont, "contentView", "blur" if adult else aoi)
    elif blurs == "media":
        _pon(acc, "profileList", aoi); _pon(acc, "profileView", aoi)
        acc["avatar"] = "blur"; acc["banner"] = "blur"
        _pon(prof, "profileList", aoi); _pon(prof, "profileView", aoi)
        prof["avatar"] = "blur"; prof["banner"] = "blur"
        cont["contentMedia"] = "blur"
    elif blurs == "none":
        for d in (acc,):
            _pon(d, "profileList", aoi); _pon(d, "profileView", aoi)
            _pon(d, "contentList", aoi); _pon(d, "contentView", aoi)
        _pon(prof, "profileList", aoi); _pon(prof, "profileView", aoi)
        _pon(cont, "contentList", aoi); _pon(cont, "contentView", aoi)
    ds = str(_g(defn, "defaultSetting", "default_setting", default="") or "")
    default_setting = ds if ds in ("hide", "ignore") else "warn"
    flags = ["no-self"] + (["adult"] if adult else [])
    nombre, desc = _nombre_localizado(_g(defn, "locales", default=None), ident)
    return {"identifier": ident, "definedBy": labeler_did, "configurable": True,
            "defaultSetting": default_setting, "flags": flags, "severity": severity,
            "blurs": blurs, "adultOnly": adult, "name": nombre, "description": desc,
            "behaviors": {"account": acc, "profile": prof, "content": cont}}


def _nombre_localizado(locales, ident):
    """(nombre, descripción) de una definición: español si existe, si no inglés, si no el
    primero; si no hay locales, el nombre conocido o el propio valor."""
    elegido = None
    lista = list(locales or [])
    for pref in ("es", "en"):
        for loc in lista:
            lang = str(_g(loc, "lang", default="") or "").lower()
            if lang == pref or lang.startswith(pref + "-"):
                elegido = loc
                break
        if elegido is not None:
            break
    if elegido is None and lista:
        elegido = lista[0]
    if elegido is None:
        return NOMBRES.get(ident, ident), ""
    nombre = str(_g(elegido, "name", default="") or "") or NOMBRES.get(ident, ident)
    return nombre, str(_g(elegido, "description", default="") or "")


def nombre_etiqueta(val: str, src: str = "", prefs=None) -> str:
    """Nombre legible de una etiqueta (definición del servicio si la hay)."""
    defn = _definicion(val, src, prefs or {})
    if defn and defn.get("name"):
        return defn["name"]
    return NOMBRES.get(val, val)


def _definicion(val, src, prefs):
    """Definición aplicable a (valor, origen), como addLabel: las personalizadas (minúsculas y
    guiones) se buscan primero entre las del servicio que la puso."""
    if _CUSTOM_RE.match(val or ""):
        for d in ((prefs.get("label_defs") or {}).get(src) or []):
            if d.get("identifier") == val:
                return d
    return LABELS.get(val)


# ---------------------------------------------------------------------------
# Preferencias
# ---------------------------------------------------------------------------
def normalizar_prefs(prefs) -> dict:
    """Preferencias en la forma completa que usa el motor. Admite el formato antiguo
    {"adult_enabled", "labels"} (versión 1) y rellena los valores por defecto del oficial:
    contenido adulto DESACTIVADO, etiquetas globales con DEFAULT_LABEL_SETTINGS y Bluesky
    Moderation siempre entre los servicios."""
    prefs = dict(prefs or {})
    if prefs.get("_normalizadas"):
        return prefs
    labels = dict(DEFAULT_LABEL_SETTINGS)
    for k, v in (prefs.get("labels") or {}).items():
        k = str(k).lower()
        v = "ignore" if str(v).lower() == "show" else str(v).lower()
        if v in VISIBILIDADES:
            labels[k] = v
    for viejo, nuevo in ALIAS.items():
        if viejo in (prefs.get("labels") or {}) and nuevo not in (prefs.get("labels") or {}):
            labels[nuevo] = labels.get(viejo, labels.get(nuevo))
    labelers = []
    vistos = set()
    for lab in ([{"did": BSKY_LABELER_DID, "labels": {}}] + list(prefs.get("labelers") or [])):
        did = str(_g(lab, "did", default="") or "")
        if not did:
            continue
        if did in vistos:
            # El de Bluesky puede venir también en LabelersPref: fusionar sus preferencias.
            for x in labelers:
                if x["did"] == did:
                    x["labels"].update(dict(_g(lab, "labels", default={}) or {}))
            continue
        vistos.add(did)
        labelers.append({"did": did, "labels": dict(_g(lab, "labels", default={}) or {})})
    return {
        "_normalizadas": True,
        "adult_enabled": bool(prefs.get("adult_enabled", False)),
        "labels": labels,
        "labelers": labelers,
        "_labels_explicitas": {str(k).lower(): str(v).lower()
                               for k, v in (prefs.get("labels") or {}).items()},
        "label_defs": dict(prefs.get("label_defs") or {}),
        "muted_words": [normalizar_palabra(w) for w in (prefs.get("muted_words") or [])],
        "hidden_posts": list(prefs.get("hidden_posts") or []),
        "feed_view": dict(prefs.get("feed_view") or {}),
        "thread_view": dict(prefs.get("thread_view") or {}),
        "user_did": str(prefs.get("user_did") or ""),
        "mutewords_filtrar": bool(prefs.get("mutewords_filtrar", False)),
    }


def parsear_preferencias(lista_prefs, label_defs=None, user_did: str = "") -> dict:
    """Convierte la respuesta de app.bsky.actor.getPreferences (lista de modelos o dicts) en
    las preferencias de moderación SERIALIZABLES (se persisten en la configuración):
    {adult_enabled, labels, labelers, label_defs, muted_words, hidden_posts, feed_view,
    thread_view, labelers_dids, user_did}. Equivale a getPreferences() de agent.ts."""
    salida = {"adult_enabled": False, "labels": {}, "labelers": [], "muted_words": [],
              "hidden_posts": [], "feed_view": {}, "thread_view": {}, "labelers_dids": [],
              "user_did": user_did, "label_defs": dict(label_defs or {}), "version": 2}
    prefs_etiqueta = []
    for p in (lista_prefs or []):
        tipo = str(_g(p, "py_type", "$type", default="") or "").rsplit("#", 1)[-1].lower()
        if tipo == "adultcontentpref":
            salida["adult_enabled"] = bool(_g(p, "enabled", default=False))
        elif tipo == "contentlabelpref":
            vis = str(_g(p, "visibility", default="") or "").lower()
            prefs_etiqueta.append({
                "label": str(_g(p, "label", default="") or ""),
                "labelerDid": str(_g(p, "labeler_did", "labelerDid", default="") or ""),
                "visibility": "ignore" if vis == "show" else vis})
        elif tipo == "labelerspref":
            dids = [str(_g(it, "did", default="") or "")
                    for it in (_g(p, "labelers", default=None) or [])]
            salida["labelers_dids"] = [d for d in dids if d]
        elif tipo == "mutedwordspref":
            salida["muted_words"] = [normalizar_palabra(w)
                                     for w in (_g(p, "items", default=None) or [])]
        elif tipo == "hiddenpostspref":
            salida["hidden_posts"] = [str(u) for u in (_g(p, "items", default=None) or [])]
        elif tipo == "feedviewpref":
            feed = str(_g(p, "feed", default="") or "")
            if feed:
                salida["feed_view"][feed] = {
                    "hideReplies": bool(_g(p, "hide_replies", "hideReplies", default=False)),
                    "hideRepliesByUnfollowed": _g(p, "hide_replies_by_unfollowed",
                                                  "hideRepliesByUnfollowed",
                                                  default=None) is not False,
                    "hideRepliesByLikeCount": int(_g(p, "hide_replies_by_like_count",
                                                     "hideRepliesByLikeCount", default=0) or 0),
                    "hideReposts": bool(_g(p, "hide_reposts", "hideReposts", default=False)),
                    "hideQuotePosts": bool(_g(p, "hide_quote_posts", "hideQuotePosts",
                                              default=False)),
                }
        elif tipo == "threadviewpref":
            sort = _g(p, "sort", default=None)
            if sort:
                salida["thread_view"]["sort"] = str(sort)
    salida["labelers"] = [{"did": d, "labels": {}} for d in salida["labelers_dids"]]
    todos = [{"did": BSKY_LABELER_DID, "labels": {}}] + salida["labelers"]
    for pe in prefs_etiqueta:
        if pe["visibility"] not in VISIBILIDADES or not pe["label"]:
            continue
        if pe["labelerDid"]:
            for lab in todos:
                if lab["did"] == pe["labelerDid"]:
                    lab["labels"][pe["label"]] = pe["visibility"]
        else:
            salida["labels"][pe["label"].lower()] = pe["visibility"]
    # Las preferencias del labeler de Bluesky se guardan en su entrada (también si no está en
    # LabelersPref: es el "app labeler").
    if todos[0]["labels"]:
        salida["labelers"] = [todos[0]] + [x for x in salida["labelers"]
                                           if x["did"] != BSKY_LABELER_DID]
    return salida


def valor_cabecera_labelers(dids_suscritos) -> str:
    """Valor de atproto-accept-labelers como el oficial (agent.ts): el de Bluesky con ";redact"
    y después los suscritos SIN redact, separados por ", "."""
    partes = [f"{BSKY_LABELER_DID};redact"]
    for d in (dids_suscritos or []):
        d = str(d or "").strip()
        if d.startswith("did:") and d != BSKY_LABELER_DID and d not in partes:
            partes.append(d)
    return ", ".join(partes)


# ---------------------------------------------------------------------------
# Causas (ModerationDecision)
# ---------------------------------------------------------------------------
def _causa_etiqueta(target, val, src, author_did, prefs, user_did):
    """addLabel del oficial. Devuelve la causa o None si la etiqueta no aplica."""
    val = (val or "").strip()
    if not val:
        return None
    val = ALIAS.get(val, val)
    defn = _definicion(val, src, prefs)
    if defn is None:
        # Sin definición: el oficial la ignora. Excepción prudente: si el usuario tiene una
        # preferencia EXPLÍCITA para esa etiqueta (p. ej. "spam" de Bluesky Moderation cuando
        # aún no se han descargado sus definiciones), se respeta con "difumina el contenido".
        pref_exp = None
        for lab in prefs.get("labelers") or []:
            if lab["did"] == src and val in lab["labels"]:
                pref_exp = lab["labels"][val]
        if pref_exp is None:
            pref_exp = (prefs.get("_labels_explicitas") or {}).get(val)
        if pref_exp not in ("warn", "hide"):
            return None
        defn = {"identifier": val, "configurable": True, "defaultSetting": "warn",
                "flags": ["no-self"], "severity": "alert", "blurs": "content",
                "behaviors": {"account": {"contentList": "blur"},
                              "profile": {}, "content": {"contentList": "blur",
                                                         "contentView": "alert"}}}
    es_propia = bool(src) and src == author_did
    labeler = None
    if not es_propia:
        if not src:
            # Etiqueta sin origen conocido (datos antiguos, solo el valor): se trata como del
            # servicio de Bluesky, que siempre está activo.
            src = BSKY_LABELER_DID
        labeler = next((l for l in (prefs.get("labelers") or []) if l["did"] == src), None)
        if labeler is None:
            return None   # servicio al que no estás suscrito
    elif "no-self" in defn["flags"]:
        return None
    adult_off = "adult" in defn["flags"] and not prefs.get("adult_enabled")
    if not defn.get("configurable"):
        pref = defn.get("defaultSetting") or "hide"
    elif adult_off:
        pref = "hide"
    elif labeler is not None and labeler["labels"].get(defn["identifier"]):
        pref = labeler["labels"][defn["identifier"]]
    elif (prefs.get("labels") or {}).get(defn["identifier"]):
        pref = prefs["labels"][defn["identifier"]]
    else:
        pref = defn.get("defaultSetting") or "ignore"
    if pref == "ignore":
        return None
    if "unauthed" in defn["flags"]:
        return None   # el cliente siempre está autenticado
    beh = (defn.get("behaviors") or {}).get(target) or {}
    if beh.get("profileView") == "blur" or beh.get("contentView") == "blur":
        sev = 0
    elif beh.get("contentList") == "blur" or beh.get("contentMedia") == "blur":
        sev = 1
    else:
        sev = 2
    no_override = "no-override" in defn["flags"] or adult_off
    if no_override:
        prioridad = 1
    elif pref == "hide":
        prioridad = 2
    else:
        prioridad = (5, 7, 8)[sev]
    return {"tipo": "label", "target": target, "val": val, "src": src, "setting": pref,
            "behavior": beh, "no_override": no_override, "prioridad": prioridad,
            "nombre": defn.get("name") or NOMBRES.get(val, val),
            "es_propia": es_propia}


def _causas_cuenta(datos, author_did, prefs, user_did, downgrade=False):
    """decideAccount + decideProfile: silencio, bloqueo y etiquetas del autor."""
    causas = []
    if datos.get("mu"):
        causas.append({"tipo": "muted", "prioridad": 6})
    if datos.get("bl"):
        causas.append({"tipo": "blocking", "prioridad": 3})
    if datos.get("bb"):
        causas.append({"tipo": "blocked-by", "prioridad": 4})
    for x in (datos.get("al") or []):
        val, src = x[0], x[1] if len(x) > 1 else ""
        objetivo = "profile" if (len(x) > 2 and x[2] == "p") else "account"
        if objetivo == "profile" and val == "!no-unauthenticated":
            objetivo = "account"
        c = _causa_etiqueta(objetivo, val, src, author_did, prefs, user_did)
        if c:
            causas.append(c)
    if downgrade:
        for c in causas:
            c["downgraded"] = True
    return causas


def _etiquetas_de(datos, etiquetas_csv=None):
    """[[valor, origen]] del contenido. Si no hay datos nuevos (mod_json), se usan los valores
    de la columna antigua `labels` sin origen."""
    if datos.get("l") is not None or datos.get("al") is not None:
        return list(datos.get("l") or [])
    salida = []
    for e in (etiquetas_csv if isinstance(etiquetas_csv, (list, tuple))
              else str(etiquetas_csv or "").split(",")):
        if isinstance(e, (list, tuple)):
            salida.append(list(e))
        elif e and e.strip():
            salida.append([e.strip(), ""])
    return salida


def causas_post(datos, prefs, uri="", etiquetas_csv=None, author_did="") -> tuple:
    """decidePost del oficial. Devuelve (causas, es_mio)."""
    prefs = normalizar_prefs(prefs)
    datos = datos or {}
    user_did = prefs.get("user_did") or ""
    author_did = datos.get("d") or author_did or ""
    es_mio = bool(user_did) and author_did == user_did
    causas = []
    for val, src in ((x[0], x[1] if len(x) > 1 else "") for x in _etiquetas_de(datos, etiquetas_csv)):
        c = _causa_etiqueta("content", val, src, author_did, prefs, user_did)
        if c:
            causas.append(c)
    q = datos.get("q") or {}
    ocultas = prefs.get("hidden_posts") or []
    if ocultas and ((uri and uri in ocultas) or (q.get("u") and q.get("u") in ocultas
                                                   and not q.get("blk"))):
        causas.append({"tipo": "hidden", "prioridad": 6})
    if not es_mio and prefs.get("muted_words"):
        m = palabras_de_post(datos, prefs["muted_words"])
        if m:
            causas.append({"tipo": "mute-word", "prioridad": 6,
                           "palabras": valores_coincidentes(m)})
    # La cita, rebajada (downgrade): solo informa, no difumina la publicación que cita.
    if q:
        q_did = q.get("d") or ""
        if q.get("blk"):
            sub = _causas_cuenta({k: q.get(k) for k in ("mu", "bl", "bb")}, q_did, prefs, user_did,
                                 downgrade=True)
        else:
            sub = []
            for x in (q.get("l") or []):
                c = _causa_etiqueta("content", x[0], x[1] if len(x) > 1 else "", q_did, prefs,
                                    user_did)
                if c:
                    sub.append(c)
            sub += _causas_cuenta(q, q_did, prefs, user_did)
            for c in sub:
                c["downgraded"] = True
        causas += sub
    causas += _causas_cuenta(datos, author_did, prefs, user_did)
    return causas, es_mio


def ui(causas, contexto="lista", es_mio=False) -> dict:
    """ModerationDecision.ui(contexto) del oficial: filtros, difuminados, alertas e
    informaciones para el contexto dado."""
    ctx = _CONTEXTOS.get(contexto, contexto)
    filtros, blurs, alerts, informs = [], [], [], []
    no_override = False
    for c in causas:
        t = c["tipo"]
        down = c.get("downgraded", False)
        if t in ("blocking", "blocked-by", "block-other"):
            if es_mio:
                continue
            if ctx in ("profileList", "contentList"):
                filtros.append(c)
            if not down:
                b = _BLOCK_BEHAVIOR.get(ctx)
                if b == "blur":
                    no_override = True
                    blurs.append(c)
                elif b == "alert":
                    alerts.append(c)
                elif b == "inform":
                    informs.append(c)
        elif t in ("muted", "mute-word", "hidden"):
            if t != "hidden" and es_mio:
                continue
            if ctx == "contentList" or (t != "mute-word" and ctx == "profileList"):
                filtros.append(c)
            if not down:
                b = {"muted": _MUTE_BEHAVIOR, "mute-word": _MUTEWORD_BEHAVIOR,
                     "hidden": _HIDE_BEHAVIOR}[t].get(ctx)
                if b == "blur":
                    blurs.append(c)
                elif b == "alert":
                    alerts.append(c)
                elif b == "inform":
                    informs.append(c)
        elif t == "label":
            if ctx == "profileList" and c["target"] == "account":
                if c["setting"] == "hide" and not es_mio:
                    filtros.append(c)
            elif ctx == "contentList" and c["target"] in ("account", "content"):
                if c["setting"] == "hide" and not es_mio:
                    filtros.append(c)
            if not down:
                b = c["behavior"].get(ctx)
                if b == "blur":
                    blurs.append(c)
                    if c["no_override"] and not es_mio:
                        no_override = True
                elif b == "alert":
                    alerts.append(c)
                elif b == "inform":
                    informs.append(c)
    filtros.sort(key=lambda c: c["prioridad"])
    blurs.sort(key=lambda c: c["prioridad"])
    return {"filtros": filtros, "blurs": blurs, "alerts": alerts, "informs": informs,
            "no_override": no_override}


def nombre_causa(c) -> str:
    """Texto breve de una causa para los avisos accesibles."""
    t = c.get("tipo")
    if t == "label":
        return c.get("nombre") or NOMBRES.get(c.get("val"), c.get("val", ""))
    if t == "mute-word":
        return ", ".join(c.get("palabras") or [])
    if t == "hidden":
        return _("publicación ocultada por ti")
    if t == "muted":
        return _("cuenta silenciada")
    if t == "blocking":
        return _("cuenta bloqueada")
    if t in ("blocked-by", "block-other"):
        return _("bloqueo")
    return ""


def moderar_post(datos, prefs=None, contexto="lista", uri="", etiquetas=None,
                 author_did="", ignorar_cuenta=False) -> dict:
    """Decisión completa para una publicación en un contexto ("lista", "busqueda",
    "detalle"). `ignorar_cuenta=True` (buffer de las publicaciones de esa misma cuenta) hace
    como `ignoreFilterFor` del oficial: el silencio/bloqueo del autor no QUITA sus publicaciones
    de la lista (siguen difuminadas con el aviso). Devuelve:
    {filtrar, difuminar, difuminar_media, avisar, no_override, motivo, motivo_media,
     tipo_motivo, palabras, alertas, causas, es_mio}.

    En "detalle" nunca se filtra (el usuario abrió la publicación a propósito) salvo que el
    difuminado no se pueda revelar (no_override): entonces filtrar=True significa "no se
    puede mostrar"."""
    prefs = normalizar_prefs(prefs)
    causas, es_mio = causas_post(datos, prefs, uri=uri, etiquetas_csv=etiquetas,
                                 author_did=author_did)
    u = ui(causas, contexto, es_mio)
    if ignorar_cuenta:
        u["filtros"] = [c for c in u["filtros"]
                        if c["tipo"] not in ("muted", "blocking", "blocked-by")]
    um = ui(causas, "media", es_mio)
    res = {"filtrar": bool(u["filtros"]), "difuminar": bool(u["blurs"]),
           "difuminar_media": bool(um["blurs"]), "avisar": bool(u["alerts"] or u["informs"]),
           "no_override": bool(u["no_override"]),
           "no_override_media": bool(um["no_override"]), "motivo": "", "motivo_media": "",
           "tipo_motivo": "", "palabras": [], "alertas": [], "causas": causas,
           "es_mio": es_mio}
    principal = (u["blurs"] or u["filtros"] or u["alerts"] or u["informs"] or [None])[0]
    if principal:
        res["motivo"] = nombre_causa(principal)
        res["tipo_motivo"] = principal["tipo"]
    if um["blurs"]:
        res["motivo_media"] = nombre_causa(um["blurs"][0])
    res["alertas"] = [nombre_causa(c) for c in (u["alerts"] + u["informs"]) if nombre_causa(c)]
    for c in causas:
        if c["tipo"] == "mute-word":
            res["palabras"] = list(c.get("palabras") or [])
    # Palabras silenciadas en LISTAS: por defecto se difuminan con aviso (se pueden leer en
    # detalle); con la opción "mutewords_filtrar" se quitan de la lista como en el feed oficial.
    if contexto == "lista" and res["filtrar"] and not prefs.get("mutewords_filtrar"):
        if all(c["tipo"] == "mute-word" for c in u["filtros"]):
            res["filtrar"] = False
    if contexto == "detalle":
        res["filtrar"] = bool(res["difuminar"] and res["no_override"])
    return res


def moderar_cita(datos, prefs=None) -> dict:
    """Decisión SOLO para la publicación citada (B13), como si se abriera en una lista:
    {difuminar, filtrar, motivo, oculta_por_ti}. {} si no hay cita."""
    q = (datos or {}).get("q") or {}
    if not q:
        return {}
    prefs = normalizar_prefs(prefs)
    if q.get("blk"):
        return {"difuminar": True, "filtrar": True, "motivo": _("publicación no disponible"),
                "bloqueada": True}
    sub = dict(q)
    sub.pop("q", None)
    res = moderar_post(sub, prefs, contexto="lista", uri=q.get("u", ""))
    res["oculta_por_ti"] = q.get("u", "") in (prefs.get("hidden_posts") or [])
    if res["oculta_por_ti"]:
        res["difuminar"] = True
        res["motivo"] = nombre_causa({"tipo": "hidden"})
    return res


def moderar_notificacion(datos, prefs=None) -> dict:
    """decideNotification del oficial (etiquetas de la notificación + cuenta/perfil del autor)
    más las palabras silenciadas sobre el texto de la publicación que la origina (mención,
    respuesta o cita de otra cuenta), como hace la app al filtrar notificaciones."""
    prefs = normalizar_prefs(prefs)
    datos = datos or {}
    return moderar_post(datos, prefs, contexto="lista")


# ---------------------------------------------------------------------------
# Compatibilidad: API antigua (lista de valores de etiqueta)
# ---------------------------------------------------------------------------
def etiquetas_visibles(etiquetas):
    """Solo las etiquetas 'de texto' (sin las de sistema, que empiezan por '!'), para
    mostrarlas en la columna de advertencia."""
    salida = []
    for e in (etiquetas or []):
        v = e[0] if isinstance(e, (list, tuple)) else e
        if v and not str(v).startswith("!") and v not in salida:
            salida.append(v)
    return salida


def decidir(etiquetas, prefs=None, contexto="lista", datos=None, uri="") -> dict:
    """API histórica: decide con una lista de etiquetas (valores, o pares [valor, origen]).
    Si se pasan `datos` (mod_json) se usa el motor completo (palabras silenciadas, cita,
    publicaciones ocultas, cuenta). Devuelve las mismas claves de siempre y las nuevas."""
    if datos:
        return moderar_post(datos, prefs, contexto=contexto, uri=uri, etiquetas=etiquetas)
    pares = []
    for e in (etiquetas or []):
        if isinstance(e, (list, tuple)):
            pares.append([e[0], e[1] if len(e) > 1 else ""])
        elif e:
            pares.append([str(e).strip().lower(), ""])
    return moderar_post({"l": pares}, prefs, contexto=contexto, uri=uri)


def texto_difuminado(motivo, contexto="lista", tipo="label"):
    """Texto accesible que sustituye al contenido difuminado."""
    motivo = motivo or _("etiquetado")
    if tipo == "mute-word":
        return _("[Silenciado por la palabra: {0}]").format(motivo)
    if tipo == "hidden":
        return _("[Publicación ocultada por ti]")
    if tipo in ("muted", "blocking", "blocked-by"):
        return _("[Oculto: {0}]").format(motivo)
    if contexto == "detalle":
        return _("[Contenido con advertencia: {0}]").format(motivo)
    return _("[Oculto por la etiqueta: {0}. Ábrelo en detalles para verlo]").format(motivo)


def texto_aviso(res, contexto="lista") -> str:
    """Aviso para una decisión de moderar_post (usa el tipo de la causa principal)."""
    return texto_difuminado(res.get("motivo"), contexto, res.get("tipo_motivo") or "label")


def texto_media_oculta(res) -> str:
    return _("[Multimedia oculta: {0}]").format(res.get("motivo_media") or _("etiquetada"))


# ---------------------------------------------------------------------------
# Preferencias de Inicio (feedViewPref) y orden de hilos (threadViewPref)
# ---------------------------------------------------------------------------
FEED_VIEW_DEFAULTS = {"hideReplies": False, "hideRepliesByUnfollowed": True,
                      "hideRepliesByLikeCount": 0, "hideReposts": False,
                      "hideQuotePosts": False}


def prefs_feed_view(prefs, feed="home") -> dict:
    d = dict(FEED_VIEW_DEFAULTS)
    d.update((normalizar_prefs(prefs).get("feed_view") or {}).get(feed) or {})
    return d


def oculto_por_feed_view(fv, es_respuesta, es_repost, es_cita, like_count=0,
                         author_did="", user_did="", datos=None) -> bool:
    """Equivale a los ajustes del feed "Siguiendo" del cliente oficial (FeedTuner):
    - hideReposts: fuera los reposts.
    - hideQuotePosts: fuera las publicaciones que citan.
    - hideReplies: fuera las respuestas (un repost de una respuesta es un repost).
    - hideRepliesByUnfollowed: solo respuestas en las que sigues (o eres) a TODOS los autores
      (autor, padre y raíz). Si no se sabe a quién responde, no se oculta.
    - hideRepliesByLikeCount: respuestas con menos "me gusta" que el umbral, salvo que sigas a
      todos sus autores o sean tuyas."""
    fv = fv or FEED_VIEW_DEFAULTS
    if es_repost:
        return bool(fv.get("hideReposts"))
    if es_cita and fv.get("hideQuotePosts"):
        return True
    if not es_respuesta:
        return False
    if user_did and author_did == user_did:
        return False   # tus propias respuestas (hilos propios) siempre se ven
    if fv.get("hideReplies"):
        return True
    rp = (datos or {}).get("rp") or {}
    sigue_todos = None
    if rp:
        autor_ok = bool((datos or {}).get("f"))
        sigue_todos = autor_ok and all(
            (rp.get(k + "d") == user_did and user_did) or rp.get(k + "f")
            for k in ("p", "r") if (k + "d") in rp)
    if fv.get("hideRepliesByUnfollowed") and sigue_todos is False:
        return True
    umbral = int(fv.get("hideRepliesByLikeCount") or 0)
    if umbral > 0 and (like_count or 0) < umbral and not sigue_todos:
        return True
    return False


def ordenar_respuestas(nodos, sort="", op_did="", user_did="", like_de=None, fecha_de=None,
                       did_de=None):
    """Ordena las respuestas de un nivel del hilo como el cliente oficial: primero las del
    autor original, luego las tuyas y después según threadViewPref.sort ("oldest", "newest",
    "most-likes"; "hotness" y "random" conservan el orden del servidor)."""
    nodos = list(nodos or [])
    like_de = like_de or (lambda n: 0)
    fecha_de = fecha_de or (lambda n: "")
    did_de = did_de or (lambda n: "")

    def _grupo(n):
        d = did_de(n)
        if op_did and d == op_did:
            return 0
        if user_did and d == user_did:
            return 1
        return 2
    indices = list(range(len(nodos)))
    if sort == "oldest":
        clave = lambda i: (_grupo(nodos[i]), fecha_de(nodos[i]) or "", i)  # noqa: E731
    elif sort == "newest":
        clave = lambda i: (_grupo(nodos[i]),  # noqa: E731
                           "".join(chr(0x10FFFF - ord(c)) for c in (fecha_de(nodos[i]) or "")), i)
    elif sort == "most-likes":
        clave = lambda i: (_grupo(nodos[i]), -(like_de(nodos[i]) or 0), i)  # noqa: E731
    else:
        clave = lambda i: (_grupo(nodos[i]), i)  # noqa: E731
    return [nodos[i] for i in sorted(indices, key=clave)]


# ---------------------------------------------------------------------------
# Bandeja unificada (la integra mf_bandeja.py)
# ---------------------------------------------------------------------------
def moderar_fila_bandeja(prefs, labels_csv="", mod_json="", uri="", author_did="",
                         contenido="", buffer="", like_count=0, reply_parent_uri="",
                         quote_uri="", reason_json="") -> dict:
    """Moderación de una fila de Bluesky en la bandeja unificada, igual que en el buffer:
    {"ocultar": bool, "contenido": str, "ocultar_media": bool, "aviso_media": str}.
    - ocultar: la fila no se pinta (filtrada por etiquetas/ocultada/feedViewPref en Inicio).
    - contenido: el texto a mostrar (el aviso si está difuminado; con "[Aviso: X]" delante si
      solo informa).
    - ocultar_media: no describir la multimedia (en su lugar, aviso_media)."""
    from app.services.bluesky.bluesky_moddatos import de_json
    datos = de_json(mod_json)
    res = moderar_post(datos, prefs, contexto="lista", uri=uri, etiquetas=labels_csv,
                       author_did=author_did)
    salida = {"ocultar": res["filtrar"], "contenido": contenido,
              "ocultar_media": res["difuminar"] or res["difuminar_media"],
              "aviso_media": texto_media_oculta(res) if res["difuminar_media"] else ""}
    if not salida["ocultar"] and buffer == "home":
        p = normalizar_prefs(prefs)
        salida["ocultar"] = oculto_por_feed_view(
            prefs_feed_view(p, "home"), bool(reply_parent_uri),
            "reasonrepost" in str(reason_json or "").lower(), bool(quote_uri),
            like_count, datos.get("d") or author_did, p.get("user_did", ""), datos)
    if res["difuminar"]:
        salida["contenido"] = texto_aviso(res)
    elif res["avisar"] and res["alertas"]:
        salida["contenido"] = _("[Aviso: {0}] ").format(", ".join(res["alertas"])) + (contenido or "")
    return salida
