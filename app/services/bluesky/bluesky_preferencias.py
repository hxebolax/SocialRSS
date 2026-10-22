# -*- coding: utf-8 -*-
"""Preferencias de MODERACIÓN de Bluesky (app.bsky.actor.getPreferences / putPreferences).

Mixin de BlueskyService. Reúne todo lo que la app oficial guarda en las preferencias de la
cuenta y que decide qué se ve:
- contenido adulto (adultContentPref) y ocultar/avisar/mostrar por etiqueta, global o por
  servicio de etiquetado (contentLabelPref con labelerDid);
- servicios de etiquetado suscritos (labelersPref) y sus definiciones propias
  (app.bsky.labeler.getServices detailed: labelValueDefinitions);
- palabras silenciadas (mutedWordsPref: targets, actorTarget, expiresAt, id);
- publicaciones ocultadas (hiddenPostsPref);
- preferencias del feed Siguiendo (feedViewPref "home") y orden de los hilos (threadViewPref).

Toda escritura es leer → modificar → escribir bajo UN candado (B17): putPreferences sustituye
el conjunto entero y dos ediciones simultáneas se pisaban. Tras cada escritura la caché local y
la cabecera atproto-accept-labelers quedan al día sin otra petición.
"""
import threading
import time

from app.services.bluesky.bluesky_common import (
    Callable, Dict, List, Optional, BlueskyServiceError, task_manager, log, models, _g, security)
from app.services.bluesky.bluesky_moderacion import (
    BSKY_LABELER_DID, ALIAS_LEGADO, VISIBILIDADES, interpretar_definicion, normalizar_prefs,
    parsear_preferencias, valor_cabecera_labelers)
from app.services.bluesky.bluesky_mutewords import normalizar_palabra

_TTL_PREFS = 3600          # 1 h: preferencias de moderación
_TTL_DEFS = 6 * 3600       # 6 h: definiciones de los servicios de etiquetado
_CABECERA = "atproto-accept-labelers"
_ALFABETO_TID = "234567abcdefghijklmnopqrstuvwxyz"


def nuevo_tid() -> str:
    """Identificador TID (13 caracteres base32 ordenables por tiempo), como TID.nextStr() del
    oficial para el `id` de una palabra silenciada nueva."""
    import random
    v = (int(time.time() * 1_000_000) << 10) | random.getrandbits(10)
    s = ""
    for _i in range(13):
        s = _ALFABETO_TID[v & 31] + s
        v >>= 5
    return s


def _tipo_pref(p) -> str:
    return str(_g(p, "py_type", "$type", default="") or "").rsplit("#", 1)[-1].lower()


class _PreferenciasMixin:
    # ------------------------------------------------------------------
    # Candado y lectura/escritura de preferencias
    # ------------------------------------------------------------------
    def _mutex_prefs(self):
        if not hasattr(self, "_prefs_mutex"):
            self._prefs_mutex = threading.RLock()
        return self._prefs_mutex

    def _editar_preferencias(self, account_id: int, transform: Callable):
        """Lee las preferencias, aplica `transform(lista) -> lista` y las guarda, todo bajo el
        candado de preferencias. Actualiza la caché de moderación con el resultado."""
        client = self.obtener_cliente(account_id)
        with self._mutex_prefs():
            resp = client.app.bsky.actor.get_preferences()
            lista = list(_g(resp, "preferences", default=[]) or [])
            nueva = transform(list(lista))
            client.app.bsky.actor.put_preferences(
                models.AppBskyActorPutPreferences.Data(preferences=nueva))
        try:
            self._guardar_cache_prefs(account_id, client, nueva)
        except Exception as e:
            log.debug(f"No se pudo actualizar la caché de moderación tras guardar: {e}")
        return nueva

    @staticmethod
    def _sin_tipo(lista, tipo: str):
        return [p for p in lista if _tipo_pref(p) != tipo.lower()]

    # ------------------------------------------------------------------
    # Caché de preferencias de moderación
    # ------------------------------------------------------------------
    def _user_did_local(self, account_id: int) -> str:
        me = (getattr(self, "_me", {}) or {}).get(account_id) or {}
        if me.get("did"):
            return me["did"]
        try:
            return security.obtener_credencial(f"bluesky:{account_id}", "did") or ""
        except Exception:
            return ""

    def _aplicar_cabecera_labelers(self, client, dids) -> None:
        """atproto-accept-labelers como el oficial: Bluesky Moderation con ;redact y los
        suscritos sin redact (B1). Se usa la API de cabeceras del SDK (request)."""
        try:
            client.request.add_additional_header(_CABECERA, valor_cabecera_labelers(dids))
        except Exception as e:
            log.debug(f"No se pudo aplicar la cabecera de labelers: {e}")

    def aplicar_cabecera_labelers_cacheada(self, account_id: int, client) -> None:
        """Al reanudar la sesión: cabecera con los suscritos guardados (sin red)."""
        from app.core.config_manager import config as _cfg
        dids = list((_cfg.get("bluesky", "labelers_suscritos", {}) or {}).get(str(account_id), []) or [])
        self._aplicar_cabecera_labelers(client, dids)

    def _definiciones_labelers(self, account_id: int, client, dids, forzar=False) -> dict:
        """{did: [definiciones interpretadas]} de los servicios dados (con caché de 6 h)."""
        if not hasattr(self, "_label_defs_cache"):
            self._label_defs_cache = {}
        entrada = self._label_defs_cache.get(account_id)
        clave = tuple(sorted(set(dids)))
        if entrada and not forzar and entrada[1] == clave and time.time() - entrada[2] < _TTL_DEFS:
            return entrada[0]
        defs = {}
        for i in range(0, len(clave), 20):
            lote = list(clave[i:i + 20])
            try:
                resp = client.app.bsky.labeler.get_services({"dids": lote, "detailed": True})
            except Exception as e:
                log.debug(f"No se pudieron leer las definiciones de los labelers: {e}")
                if entrada:
                    return entrada[0]
                continue
            for v in (_g(resp, "views", default=[]) or []):
                did = _g(_g(v, "creator"), "did", default="") or ""
                pol = _g(v, "policies", default=None)
                lista = _g(pol, "label_value_definitions", "labelValueDefinitions",
                           default=None) or []
                defs[did] = [interpretar_definicion(d, did) for d in lista
                             if _g(d, "identifier", default="")]
        self._label_defs_cache[account_id] = (defs, clave, time.time())
        return defs

    def _guardar_cache_prefs(self, account_id: int, client, lista_prefs, forzar_defs=False):
        """Parsea la lista de preferencias, añade las definiciones de los labelers, actualiza
        la caché en memoria, la copia persistida, la lista de suscritos y la cabecera."""
        from app.core.config_manager import config as _cfg
        previas = getattr(self, "_mod_prefs", {}).get(account_id)
        user_did = self._user_did_local(account_id)
        crudo = parsear_preferencias(lista_prefs, user_did=user_did)
        dids = crudo.get("labelers_dids") or []
        if client is not None:
            crudo["label_defs"] = self._definiciones_labelers(
                account_id, client, [BSKY_LABELER_DID] + dids, forzar=forzar_defs)
        elif previas:
            crudo["label_defs"] = previas[0].get("label_defs") or {}
        crudo["mutewords_filtrar"] = bool(_cfg.get("bluesky", "mutewords_filtrar", False))
        if not hasattr(self, "_mod_prefs"):
            self._mod_prefs = {}
        self._mod_prefs[account_id] = (normalizar_prefs(crudo), time.time())
        guardadas = dict(_cfg.get("bluesky", "mod_prefs", {}) or {})
        if guardadas.get(str(account_id)) != crudo:
            guardadas[str(account_id)] = crudo
            _cfg.set("bluesky", "mod_prefs", guardadas)
        suscritos = dict(_cfg.get("bluesky", "labelers_suscritos", {}) or {})
        if suscritos.get(str(account_id)) != dids:
            suscritos[str(account_id)] = dids
            _cfg.set("bluesky", "labelers_suscritos", suscritos)
        if client is not None:
            self._aplicar_cabecera_labelers(client, dids)
        return self._mod_prefs[account_id][0]

    def prefs_moderacion(self, account_id: int, permitir_red: bool = False) -> dict:
        """Preferencias de moderación NORMALIZADAS (las mismas de la app oficial), con caché de
        sesión (1 h) y copia persistida para arrancar sin red. `permitir_red=True` solo desde
        hilos de fondo y SIEMPRE fuera de una transacción de escritura (refresca si caducó);
        desde la interfaz se usa lo cacheado sin bloquear."""
        from app.core.config_manager import config as _cfg
        if not hasattr(self, "_mod_prefs"):
            self._mod_prefs = {}
        entrada = self._mod_prefs.get(account_id)
        if entrada and (time.time() - entrada[1] < _TTL_PREFS or not permitir_red):
            return entrada[0]
        persistidas = (_cfg.get("bluesky", "mod_prefs", {}) or {}).get(str(account_id))
        if persistidas and persistidas.get("version") != 2:
            persistidas = None   # formato antiguo (adulto activado por defecto): se descarta
        if not permitir_red:
            base = dict(persistidas or {})
            base.setdefault("user_did", self._user_did_local(account_id))
            base["mutewords_filtrar"] = bool(_cfg.get("bluesky", "mutewords_filtrar", False))
            norm = normalizar_prefs(base)
            # Sin copia persistida, refrescar en cuanto un hilo de fondo pueda.
            self._mod_prefs[account_id] = (norm, time.time() - (3000 if persistidas else _TTL_PREFS))
            return norm
        try:
            client = self.obtener_cliente(account_id)
            resp = self._get_con_reintentos(
                account_id, lambda: client.app.bsky.actor.get_preferences())
            return self._guardar_cache_prefs(
                account_id, client, list(_g(resp, "preferences", default=[]) or []))
        except Exception as e:
            log.debug(f"No se pudieron refrescar las preferencias de moderación: {e}")
            # Cachear TAMBIÉN el fallo: sin esto cada post reintentaba la red en bucle.
            base = dict(persistidas or {})
            base.setdefault("user_did", self._user_did_local(account_id))
            norm = normalizar_prefs(base)
            self._mod_prefs[account_id] = (norm, time.time())
            return norm

    def refrescar_prefs_moderacion_async(self, account_id: int, callback: Optional[Callable] = None):
        """Fuerza la lectura de las preferencias (p. ej. al abrir un diálogo de moderación)."""
        def _bg(ctx):
            if hasattr(self, "_mod_prefs"):
                self._mod_prefs.pop(account_id, None)
            return self.prefs_moderacion(account_id, permitir_red=True)
        task_manager.start_task(_bg, callback=callback, pass_context=True,
                                unique_key=f"bsky:prefsmod:{account_id}")

    def fijar_opcion_local_mutewords(self, account_id: int, filtrar: bool):
        """Opción LOCAL: quitar de las listas lo silenciado por palabra (como el feed oficial) en
        vez de mostrarlo con aviso."""
        from app.core.config_manager import config as _cfg
        _cfg.set("bluesky", "mutewords_filtrar", bool(filtrar))
        for acc, (p, ts) in list(getattr(self, "_mod_prefs", {}).items()):
            p = dict(p)
            p["mutewords_filtrar"] = bool(filtrar)
            self._mod_prefs[acc] = (p, ts)

    # ------------------------------------------------------------------
    # Contenido adulto y visibilidad por etiqueta (B4/B8/B9)
    # ------------------------------------------------------------------
    def guardar_moderacion_async(self, account_id: int, adulto: Optional[bool] = None,
                                 globales: Optional[Dict[str, str]] = None,
                                 por_labeler: Optional[Dict[str, Dict[str, str]]] = None,
                                 callback: Optional[Callable] = None):
        """Guarda en UNA escritura el contenido adulto y las visibilidades por etiqueta,
        globales ({etiqueta: ignore|warn|hide}) y por servicio ({did: {etiqueta: ...}}), sin
        tocar el resto de preferencias. Las globales con nombre antiguo se escriben también con
        él (setContentLabelPref del oficial)."""
        globales = dict(globales or {})
        por_labeler = {d: dict(v or {}) for d, v in (por_labeler or {}).items()}

        def _t(lista):
            if adulto is not None:
                lista = self._sin_tipo(lista, "adultContentPref")
                lista.append(models.AppBskyActorDefs.AdultContentPref(enabled=bool(adulto)))
            cambios = []   # (etiqueta, labelerDid o None, visibilidad)
            for et, vis in globales.items():
                if vis in VISIBILIDADES:
                    cambios.append((et, None, vis))
                    if et in ALIAS_LEGADO:
                        cambios.append((ALIAS_LEGADO[et], None, vis))
            for did, mapa in por_labeler.items():
                for et, vis in mapa.items():
                    if vis in VISIBILIDADES:
                        cambios.append((et, did, vis))
            claves = {(et, did or "") for et, did, _v in cambios}
            lista = [p for p in lista if not (
                _tipo_pref(p) == "contentlabelpref"
                and (str(_g(p, "label", default="")),
                     str(_g(p, "labeler_did", "labelerDid", default="") or "")) in claves)]
            for et, did, vis in cambios:
                kw = {"label": et, "visibility": vis}
                if did:
                    kw["labeler_did"] = did
                lista.append(models.AppBskyActorDefs.ContentLabelPref(**kw))
            return lista

        def _bg(ctx):
            self._editar_preferencias(account_id, _t)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Palabras silenciadas (B2/B10)
    # ------------------------------------------------------------------
    def obtener_muted_words_async(self, account_id: int, callback: Optional[Callable] = None):
        """Palabras silenciadas como dicts {value, targets, actorTarget, expiresAt, id}."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.actor.get_preferences()
            lista = list(_g(resp, "preferences", default=[]) or [])
            try:
                self._guardar_cache_prefs(account_id, client, lista)
            except Exception as e:
                log.debug(f"Caché de moderación no actualizada: {e}")
            for p in lista:
                if _tipo_pref(p) == "mutedwordspref":
                    return [normalizar_palabra(w) for w in (_g(p, "items", default=[]) or [])
                            if _g(w, "value", default="")]
            return []
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    @staticmethod
    def _modelo_palabra(w: dict):
        w = normalizar_palabra(w)
        targets = [t for t in (w.get("targets") or []) if t in ("content", "tag")] or ["content", "tag"]
        kw = {"value": w["value"], "targets": targets,
              "actor_target": w.get("actorTarget") or "all", "id": w.get("id") or nuevo_tid()}
        if w.get("expiresAt"):
            kw["expires_at"] = w["expiresAt"]
        return models.AppBskyActorDefs.MutedWord(**kw)

    def _modificar_muted_words(self, account_id: int, transform: Callable):
        """transform(lista de dicts normalizados) -> lista de dicts. Conserva el id de cada
        palabra (W4/B10) y el resto de preferencias."""
        def _t(lista):
            items = []
            for p in lista:
                if _tipo_pref(p) == "mutedwordspref":
                    items = [normalizar_palabra(w) for w in (_g(p, "items", default=[]) or [])]
            nuevas = transform(items)
            lista = self._sin_tipo(lista, "mutedWordsPref")
            lista.append(models.AppBskyActorDefs.MutedWordsPref(
                items=[self._modelo_palabra(w) for w in nuevas if w.get("value")]))
            return lista
        self._editar_preferencias(account_id, _t)

    def guardar_muted_word_async(self, account_id: int, palabra: dict,
                                 original: Optional[dict] = None,
                                 callback: Optional[Callable] = None):
        """Añade una palabra silenciada o EDITA `original` (por id; si no tiene, por valor)
        conservando su id."""
        nueva = normalizar_palabra(palabra)
        nueva["value"] = nueva["value"].strip()
        orig = normalizar_palabra(original) if original else None

        def _transform(items):
            if orig:
                for i, w in enumerate(items):
                    if (orig.get("id") and w.get("id") == orig["id"]) or \
                            (not orig.get("id") and w.get("value") == orig["value"]):
                        nueva["id"] = w.get("id") or nueva.get("id") or nuevo_tid()
                        items[i] = nueva
                        return items
            # Alta (o el original ya no existe): sin duplicar el mismo valor.
            for i, w in enumerate(items):
                if w.get("value", "").lower() == nueva["value"].lower():
                    nueva["id"] = w.get("id") or nuevo_tid()
                    items[i] = nueva
                    return items
            nueva["id"] = nueva.get("id") or nuevo_tid()
            items.append(nueva)
            return items

        def _bg(ctx):
            if not nueva["value"]:
                raise BlueskyServiceError(_("La palabra no puede estar vacía."))
            self._modificar_muted_words(account_id, _transform)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def añadir_muted_word_async(self, account_id: int, palabra: str,
                                callback: Optional[Callable] = None, targets=None,
                                actor_target: str = "all", expires_at: str = ""):
        """Compatibilidad: añade una palabra (contenido y etiquetas por defecto)."""
        self.guardar_muted_word_async(
            account_id, {"value": palabra, "targets": list(targets or ["content", "tag"]),
                         "actorTarget": actor_target, "expiresAt": expires_at},
            callback=callback)

    def quitar_muted_word_async(self, account_id: int, palabra, callback: Optional[Callable] = None):
        """Quita una palabra silenciada (por id si se da un dict con id; si no, por valor)."""
        w = normalizar_palabra(palabra)

        def _transform(items):
            if w.get("id"):
                return [x for x in items if x.get("id") != w["id"]]
            return [x for x in items if x.get("value") != w["value"]]

        def _bg(ctx):
            self._modificar_muted_words(account_id, _transform)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Publicaciones ocultadas (B11)
    # ------------------------------------------------------------------
    def publicaciones_ocultas(self, account_id: int) -> List[str]:
        return list(self.prefs_moderacion(account_id).get("hidden_posts") or [])

    def ocultar_post_async(self, account_id: int, uri: str, ocultar: bool = True,
                           callback: Optional[Callable] = None):
        """"Ocultar esta publicación para mí" (hiddenPostsPref, como hidePost/unhidePost del
        oficial). La caché se actualiza al momento para que la lista la quite ya."""
        def _t(lista):
            items = []
            for p in lista:
                if _tipo_pref(p) == "hiddenpostspref":
                    items = [str(u) for u in (_g(p, "items", default=[]) or [])]
            if ocultar and uri not in items:
                items.append(uri)
            elif not ocultar:
                items = [u for u in items if u != uri]
            lista = self._sin_tipo(lista, "hiddenPostsPref")
            lista.append(models.AppBskyActorDefs.HiddenPostsPref(items=items))
            return lista

        # Reflejo inmediato en la caché (la escritura de red va después).
        entrada = getattr(self, "_mod_prefs", {}).get(account_id)
        if entrada:
            p = dict(entrada[0])
            ocultas = [u for u in (p.get("hidden_posts") or []) if u != uri]
            if ocultar:
                ocultas.append(uri)
            p["hidden_posts"] = ocultas
            self._mod_prefs[account_id] = (p, entrada[1])

        def _bg(ctx):
            self._editar_preferencias(account_id, _t)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Feed Siguiendo (feedViewPref) y orden de hilos (threadViewPref) (B12)
    # ------------------------------------------------------------------
    def fijar_feed_view_async(self, account_id: int, valores: dict, feed: str = "home",
                              callback: Optional[Callable] = None):
        def _t(lista):
            lista = [p for p in lista if not (_tipo_pref(p) == "feedviewpref"
                                              and str(_g(p, "feed", default="")) == feed)]
            lista.append(models.AppBskyActorDefs.FeedViewPref(
                feed=feed,
                hide_replies=bool(valores.get("hideReplies")),
                hide_replies_by_unfollowed=bool(valores.get("hideRepliesByUnfollowed", True)),
                hide_replies_by_like_count=int(valores.get("hideRepliesByLikeCount") or 0),
                hide_reposts=bool(valores.get("hideReposts")),
                hide_quote_posts=bool(valores.get("hideQuotePosts"))))
            return lista

        def _bg(ctx):
            self._editar_preferencias(account_id, _t)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def fijar_thread_view_async(self, account_id: int, sort: str,
                                callback: Optional[Callable] = None):
        def _t(lista):
            previa = None
            for p in lista:
                if _tipo_pref(p) == "threadviewpref":
                    previa = p
            lista = self._sin_tipo(lista, "threadViewPref")
            kw = {}
            try:
                kw = previa.model_dump(exclude_none=True) if previa is not None else {}
            except Exception:
                kw = {}
            kw.pop("py_type", None)
            kw["sort"] = sort
            lista.append(models.AppBskyActorDefs.ThreadViewPref(**kw))
            return lista

        def _bg(ctx):
            self._editar_preferencias(account_id, _t)
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # Servicios de etiquetado (B1/B8/B15)
    # ------------------------------------------------------------------
    def obtener_labelers_async(self, account_id: int, callback: Optional[Callable] = None):
        """Servicios de etiquetado: Bluesky Moderation (siempre activo) y los suscritos, con
        sus etiquetas propias. Cada uno: {did, uri, handle, name, likes, descripcion,
        suscrito, oficial, etiquetas: [definiciones]}."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp_p = client.app.bsky.actor.get_preferences()
            lista = list(_g(resp_p, "preferences", default=[]) or [])
            prefs = self._guardar_cache_prefs(account_id, client, lista, forzar_defs=True)
            suscritos = [l["did"] for l in prefs.get("labelers") or [] if l["did"] != BSKY_LABELER_DID]
            dids = [BSKY_LABELER_DID] + suscritos
            return self._vistas_labelers(client, dids, set(suscritos), prefs)
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _vistas_labelers(self, client, dids, suscritos, prefs):
        salida = []
        for i in range(0, len(dids), 20):
            resp = client.app.bsky.labeler.get_services({"dids": dids[i:i + 20], "detailed": True})
            for s in (_g(resp, "views", default=[]) or []):
                creator = _g(s, "creator")
                did = _g(creator, "did", default="") or ""
                salida.append({
                    # El DID es el del CREADOR del servicio; la URI AT del registro va aparte.
                    "did": did,
                    "uri": _g(s, "uri", default=""),
                    "handle": _g(creator, "handle", default=""),
                    "name": _g(creator, "display_name", "displayName", default="") or _g(creator, "handle", default=""),
                    "descripcion": _g(creator, "description", default="") or "",
                    "likes": _g(s, "like_count", "likeCount", default=0) or 0,
                    "oficial": did == BSKY_LABELER_DID,
                    "suscrito": did == BSKY_LABELER_DID or did in suscritos,
                    "etiquetas": list((prefs.get("label_defs") or {}).get(did) or []),
                })
        return salida

    def buscar_labeler_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Resuelve un servicio de etiquetado por usuario (handle) o DID para suscribirse."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = (actor or "").strip().lstrip("@")
            if not did.startswith("did:"):
                did = _g(client.com.atproto.identity.resolve_handle({"handle": did}), "did", default="")
            if not did:
                raise BlueskyServiceError(_("No se encontró esa cuenta."))
            prefs = self.prefs_moderacion(account_id)
            suscritos = {l["did"] for l in prefs.get("labelers") or []}
            vistas = self._vistas_labelers(client, [did], suscritos, prefs)
            if not vistas:
                raise BlueskyServiceError(_("Esa cuenta no es un servicio de etiquetado."))
            return vistas[0]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def suscribir_labeler_async(self, account_id: int, did: str, suscribir: bool = True,
                                callback: Optional[Callable] = None):
        """Suscribe (o da de baja) un servicio de etiquetado (labelersPref). Bluesky Moderation
        no se puede quitar. La cabecera atproto-accept-labelers se rehace al momento (también
        al quedarse sin suscritos) y se descargan las definiciones del nuevo servicio."""
        def _bg(ctx):
            if did == BSKY_LABELER_DID and not suscribir:
                raise BlueskyServiceError(
                    _("El servicio de moderación de Bluesky no se puede quitar."))
            if did == BSKY_LABELER_DID:
                return True

            def _t(lista):
                items = []
                for p in lista:
                    if _tipo_pref(p) == "labelerspref":
                        items = [str(_g(it, "did", default="") or "")
                                 for it in (_g(p, "labelers", default=[]) or [])]
                items = [d for d in items if d]
                if suscribir and did not in items:
                    items.append(did)
                elif not suscribir:
                    items = [d for d in items if d != did]
                lista = self._sin_tipo(lista, "labelersPref")
                lista.append(models.AppBskyActorDefs.LabelersPref(
                    labelers=[models.AppBskyActorDefs.LabelerPrefItem(did=d) for d in items]))
                return lista
            if hasattr(self, "_label_defs_cache"):
                self._label_defs_cache.pop(account_id, None)
            self._editar_preferencias(account_id, _t)
            return True
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    # ------------------------------------------------------------------
    # Hilos silenciados (B14): no hay API para listarlos; se recuerdan en local.
    # ------------------------------------------------------------------
    def hilos_silenciados(self, account_id: int) -> List[str]:
        from app.core.config_manager import config as _cfg
        return list((_cfg.get("bluesky", "hilos_silenciados", {}) or {}).get(str(account_id), []) or [])

    def _recordar_hilo_silenciado(self, account_id: int, root_uri: str, silenciado: bool):
        from app.core.config_manager import config as _cfg
        mapa = dict(_cfg.get("bluesky", "hilos_silenciados", {}) or {})
        lista = [u for u in (mapa.get(str(account_id)) or []) if u != root_uri]
        if silenciado:
            lista.append(root_uri)
        mapa[str(account_id)] = lista[-500:]
        _cfg.set("bluesky", "hilos_silenciados", mapa)
