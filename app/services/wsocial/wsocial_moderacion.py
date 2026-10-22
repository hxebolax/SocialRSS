# -*- coding: utf-8 -*-
"""Moderación, notificaciones y preferencias de W Social.

Mixin de WSocialService. Cubre lo que un usuario necesita para cuidar su experiencia:

- Denunciar publicaciones, cuentas y mensajes.
- Servicios de etiquetado (labelers): suscribirse, ver sus etiquetas y decidir qué hacer con ellas.
- Palabras silenciadas y preferencias de contenido (adulto, etiquetas, idiomas).
- Preferencias de notificaciones, con el detalle por tipo que admite el protocolo.
- Suscripciones de actividad: avisar cuando una cuenta concreta publique.
- Verificación de edad, que W ofrece para cumplir la normativa europea.
"""
from app.services.wsocial.wsocial_common import (
    Any, Callable, Dict, List, Optional, config, db, log, models, WSocialServiceError,
    _g, _ahora_iso, MOTIVOS_NOTIFICACION)

# Motivos de denuncia del protocolo, con su texto en español.
MOTIVOS_DENUNCIA = {
    "com.atproto.moderation.defs#reasonSpam": "Spam o contenido repetitivo",
    "com.atproto.moderation.defs#reasonViolation": "Incumple las normas del servidor",
    "com.atproto.moderation.defs#reasonMisleading": "Engañoso o suplantación",
    "com.atproto.moderation.defs#reasonSexual": "Contenido sexual sin marcar",
    "com.atproto.moderation.defs#reasonRude": "Comportamiento hostil o acoso",
    "com.atproto.moderation.defs#reasonOther": "Otro motivo",
    "com.atproto.moderation.defs#reasonAppeal": "Apelar una decisión de moderación",
}

# Qué hacer con una etiqueta de contenido.
VISIBILIDADES_ETIQUETA = {"ignore": "Mostrar sin más", "warn": "Avisar antes de mostrar",
                          "hide": "Ocultar del todo"}

_CABECERA_LABELERS = "atproto-accept-labelers"


def aplicar_cabecera_labelers(cliente, dids: List[str]):
    """Fija (o QUITA) la cabecera atproto-accept-labelers del cliente. Sin suscritos se quita
    del todo para que el servidor aplique sus etiquetadores por defecto: antes, al darse de
    baja del último, se quedaba la cabecera vieja con ese servicio dentro. Los suscritos van
    SIN ';redact' (como el cliente oficial, que solo lo pone a los etiquetadores de la app)."""
    if cliente is None:
        return
    validos = [d for d in dict.fromkeys(dids or []) if str(d).startswith("did:")][:20]
    peticion = getattr(cliente, "request", None)
    if peticion is None:
        return
    if validos:
        peticion.add_additional_header(_CABECERA_LABELERS, ",".join(validos))
    else:
        cabeceras = getattr(peticion, "_additional_headers", None)
        if isinstance(cabeceras, dict):
            for k in [k for k in cabeceras if k.lower() == _CABECERA_LABELERS]:
                cabeceras.pop(k, None)


def palabras_de_prefs(prefs) -> List[Dict[str, Any]]:
    """Palabras silenciadas (mutedWordsPref) de una respuesta de getPreferences, normalizadas
    con el motor común de AT Protocol: {value, targets, actorTarget, expiresAt, id}."""
    from app.services.bluesky.bluesky_mutewords import normalizar_palabra
    palabras = []
    for p in (_g(prefs, "preferences", default=[]) or []):
        if str(_g(p, "py_type", "$type", default="")).endswith("mutedWordsPref"):
            palabras.extend(normalizar_palabra(w) for w in (_g(p, "items", default=[]) or []))
    return [w for w in palabras if w.get("value")]


def cachear_palabras(account_id: int, palabras: List[Dict[str, Any]]):
    """Guarda en la configuración las palabras silenciadas de la cuenta, para aplicarlas al
    pintar las listas sin consultar la red."""
    mapa = dict(config.get("wsocial", "palabras_silenciadas", {}) or {})
    if mapa.get(str(account_id)) != palabras:
        mapa[str(account_id)] = palabras
        config.set("wsocial", "palabras_silenciadas", mapa)


def dids_de_labelers_pref(prefs) -> List[str]:
    """DIDs suscritos según la preferencia labelersPref del servidor (la que comparte con la
    app oficial)."""
    dids = []
    for p in (_g(prefs, "preferences", default=[]) or []):
        if str(_g(p, "py_type", "$type", default="")).endswith("labelersPref"):
            for item in (_g(p, "labelers", default=[]) or []):
                did = _g(item, "did", default="")
                if did and did not in dids:
                    dids.append(did)
    return dids


class _ModeracionMixin:
    # ------------------------------------------------------------------
    # DENUNCIAS
    # ------------------------------------------------------------------
    def denunciar_async(self, account_id: int, motivo: str, descripcion: str = "",
                        uri: str = "", cid: str = "", did: str = "",
                        callback: Optional[Callable] = None):
        """Denuncia una publicación (uri + cid) o una cuenta (did) al servicio de moderación."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if uri:
                sujeto = models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
            elif did:
                sujeto = models.ComAtprotoAdminDefs.RepoRef(did=did)
            else:
                raise WSocialServiceError("No se ha indicado qué se quiere denunciar.")
            resp = client.com.atproto.moderation.create_report({
                "reason_type": motivo, "reason": (descripcion or None), "subject": sujeto})
            return {"id": _g(resp, "id", default=0),
                    "creada": _g(resp, "created_at", "createdAt", default="")}
        self._tarea(_bg, callback)

    def denunciar_mensaje_async(self, account_id: int, convo_id: str, message_id: str,
                                motivo: str, descripcion: str = "",
                                callback: Optional[Callable] = None):
        """Denuncia un mensaje privado (el moderador solo ve el mensaje denunciado)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            sujeto = models.ChatBskyConvoDefs.MessageRef(
                convo_id=convo_id, message_id=message_id, did=self._yo(account_id)["did"])
            resp = client.com.atproto.moderation.create_report({
                "reason_type": motivo, "reason": (descripcion or None), "subject": sujeto})
            return {"id": _g(resp, "id", default=0)}
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # SERVICIOS DE ETIQUETADO
    # ------------------------------------------------------------------
    def labelers_async(self, account_id: int, dids: Optional[List[str]] = None,
                       callback: Optional[Callable] = None):
        """Servicios de etiquetado: los suscritos (o los que se pidan), con sus etiquetas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            suscritos = list(config.get("wsocial", "labelers_suscritos", {}).get(str(account_id), []) or [])
            objetivo = dids or suscritos
            if not objetivo:
                return []
            resp = client.app.bsky.labeler.get_services({"dids": objetivo[:25], "detailed": True})
            salida = []
            for s in (_g(resp, "views", default=[]) or []):
                creador = _g(s, "creator", default=None)
                politicas = _g(_g(s, "policies", default=None), "label_values", "labelValues", default=[]) or []
                salida.append({
                    "did": _g(creador, "did", default=""),
                    "handle": _g(creador, "handle", default=""),
                    "nombre": _g(creador, "display_name", "displayName", default=""),
                    "descripcion": (_g(creador, "description", default="") or "").replace("\n", " "),
                    "likes": _g(s, "like_count", "likeCount", default=0),
                    "etiquetas": [str(v) for v in politicas],
                    "suscrito": _g(creador, "did", default="") in suscritos,
                })
            return salida
        self._tarea(_bg, callback)

    def suscribir_labeler(self, account_id: int, did: str, suscribir: bool = True) -> bool:
        """Suscribe (o cancela) un servicio de etiquetado. Se guarda localmente y se aplica en la
        cabecera de las peticiones: sin ella, sus etiquetas no llegarían en las respuestas."""
        mapa = dict(config.get("wsocial", "labelers_suscritos", {}) or {})
        actuales = list(mapa.get(str(account_id), []) or [])
        if suscribir and did not in actuales:
            actuales.append(did)
        elif not suscribir and did in actuales:
            actuales.remove(did)
        mapa[str(account_id)] = actuales
        config.set("wsocial", "labelers_suscritos", mapa)
        # Se aplica en caliente al cliente ya abierto.
        cliente = self._clients.get(account_id)
        if cliente is not None:
            try:
                aplicar_cabecera_labelers(cliente, actuales)
            except Exception as e:
                log.debug(f"No se pudo aplicar la cabecera de etiquetadores: {e}")
        # Y en la preferencia labelersPref del servidor (en segundo plano), para que la
        # suscripción sea la misma que en la app oficial y sobreviva a reinstalar.
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._reemplazar_preferencia(
                client, "labelersPref", models.AppBskyActorDefs.LabelersPref(
                    labelers=[models.AppBskyActorDefs.LabelerPrefItem(did=d) for d in actuales]))
        self._tarea(_bg, None, silencioso=True)
        return suscribir

    def sincronizar_labelers_servidor(self, account_id: int, client) -> List[str]:
        """Une los etiquetadores suscritos del servidor (labelersPref) con los locales y aplica
        la cabecera. Se llama al iniciar sesión, en segundo plano."""
        locales = list(config.get("wsocial", "labelers_suscritos", {}).get(str(account_id), []) or [])
        try:
            prefs = client.app.bsky.actor.get_preferences()
            del_servidor = dids_de_labelers_pref(prefs)
            # De paso, las palabras silenciadas (se aplican al pintar las listas).
            cachear_palabras(account_id, palabras_de_prefs(prefs))
        except Exception as e:
            log.debug(f"W Social: no se pudo leer labelersPref: {e}")
            del_servidor = []
        todos = list(dict.fromkeys(locales + del_servidor))
        if todos != locales:
            mapa = dict(config.get("wsocial", "labelers_suscritos", {}) or {})
            mapa[str(account_id)] = todos
            config.set("wsocial", "labelers_suscritos", mapa)
        aplicar_cabecera_labelers(client, todos)
        return todos

    def etiquetas_de_async(self, account_id: int, uris: List[str],
                           callback: Optional[Callable] = None):
        """Etiquetas puestas sobre unas publicaciones o cuentas concretas."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.com.atproto.label.query_labels({"uri_patterns": uris[:20], "limit": 100})
            return [{"uri": _g(l, "uri", default=""), "valor": _g(l, "val", default=""),
                     "origen": _g(l, "src", default=""),
                     "negada": bool(_g(l, "neg", default=False))}
                    for l in (_g(resp, "labels", default=[]) or [])]
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # PREFERENCIAS DE CONTENIDO
    # ------------------------------------------------------------------
    def obtener_preferencias_async(self, account_id: int, callback: Optional[Callable] = None):
        """Lee las preferencias de la cuenta en una forma manejable: contenido adulto, qué hacer
        con cada etiqueta, palabras silenciadas, idiomas y feeds guardados."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            prefs = client.app.bsky.actor.get_preferences()
            salida = {"adulto": False, "etiquetas": {}, "palabras": [], "idiomas": [],
                      "feeds_guardados": [], "hilos": {}, "crudas": []}
            for p in (_g(prefs, "preferences", default=[]) or []):
                tipo = str(_g(p, "py_type", "$type", default="")).rsplit("#", 1)[-1]
                salida["crudas"].append(tipo)
                if tipo == "adultContentPref":
                    salida["adulto"] = bool(_g(p, "enabled", default=False))
                elif tipo == "contentLabelPref":
                    salida["etiquetas"][str(_g(p, "label", default=""))] = str(
                        _g(p, "visibility", default="warn"))
                elif tipo == "mutedWordsPref":
                    for w in (_g(p, "items", default=[]) or []):
                        salida["palabras"].append({
                            "id": _g(w, "id", default="") or "",
                            "valor": _g(w, "value", default=""),
                            "ambitos": [str(t) for t in (_g(w, "targets", default=[]) or [])],
                            "caduca": _g(w, "expires_at", "expiresAt", default=""),
                            "solo_desconocidos": bool(
                                _g(w, "actor_target", "actorTarget", default="") == "exclude-following"),
                        })
                elif tipo == "languagePref":
                    salida["idiomas"] = [str(l) for l in (_g(p, "content_languages",
                                                             "contentLanguages", default=[]) or [])]
                elif tipo == "threadViewPref":
                    salida["hilos"] = {"orden": str(_g(p, "sort", default="")),
                                       "seguidos_primero": bool(
                                           _g(p, "prioritize_followed_users",
                                              "prioritizeFollowedUsers", default=False))}
            cachear_palabras(account_id, palabras_de_prefs(prefs))
            return salida
        self._tarea(_bg, callback)

    def _reemplazar_preferencia(self, client, sufijo_tipo: str, nueva):
        """Sustituye una preferencia concreta CONSERVANDO todas las demás. Es importante hacerlo
        así: `putPreferences` reemplaza el conjunto entero, y mandar solo una borraría el resto."""
        prefs = client.app.bsky.actor.get_preferences()
        lista = [p for p in (_g(prefs, "preferences", default=[]) or [])
                 if not str(_g(p, "py_type", "$type", default="")).endswith(sufijo_tipo)]
        if nueva is not None:
            lista.append(nueva)
        client.app.bsky.actor.put_preferences({"preferences": lista})
        return True

    def fijar_contenido_adulto_async(self, account_id: int, activar: bool,
                                     callback: Optional[Callable] = None):
        """Activa o desactiva el contenido para adultos."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._reemplazar_preferencia(
                client, "adultContentPref",
                models.AppBskyActorDefs.AdultContentPref(enabled=bool(activar)))
        self._tarea(_bg, callback)

    def fijar_visibilidad_etiqueta_async(self, account_id: int, etiqueta: str, visibilidad: str,
                                         labeler_did: str = "", callback: Optional[Callable] = None):
        """Decide qué hacer con una etiqueta: ignore, warn u hide."""
        def _bg(ctx):
            if visibilidad not in VISIBILIDADES_ETIQUETA:
                raise WSocialServiceError("Elige mostrar, avisar u ocultar.")
            client = self.obtener_cliente(account_id)
            prefs = client.app.bsky.actor.get_preferences()
            lista = []
            for p in (_g(prefs, "preferences", default=[]) or []):
                # Solo se sustituye la preferencia de ESA etiqueta y ESE etiquetador: antes se
                # borraban también las de la misma etiqueta de otros etiquetadores.
                es_esa = (str(_g(p, "py_type", "$type", default="")).endswith("contentLabelPref")
                          and str(_g(p, "label", default="")) == etiqueta
                          and (_g(p, "labeler_did", "labelerDid", default="") or "")
                          == (labeler_did or ""))
                if not es_esa:
                    lista.append(p)
            lista.append(models.AppBskyActorDefs.ContentLabelPref(
                label=etiqueta, visibility=visibilidad,
                labeler_did=(labeler_did or None)))
            client.app.bsky.actor.put_preferences({"preferences": lista})
            return visibilidad
        self._tarea(_bg, callback)

    def fijar_palabras_silenciadas_async(self, account_id: int, palabras: List[Dict[str, Any]],
                                         callback: Optional[Callable] = None):
        """Guarda la lista completa de palabras silenciadas.

        Cada palabra: {valor, ambitos: ['content'|'tag'], caduca: ISO o '', solo_desconocidos: bool}.
        Los ámbitos permiten silenciar solo la etiqueta (#palabra) o también el texto."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            items = []
            for p in palabras:
                valor = (p.get("valor") or "").strip()
                if not valor:
                    continue
                items.append(models.AppBskyActorDefs.MutedWord(
                    # El id se conserva al reescribir la lista (antes se perdía).
                    id=(p.get("id") or None),
                    value=valor,
                    targets=[t for t in (p.get("ambitos") or ["content", "tag"])],
                    actor_target=("exclude-following" if p.get("solo_desconocidos") else "all"),
                    expires_at=(p.get("caduca") or None)))
            self._reemplazar_preferencia(
                client, "mutedWordsPref", models.AppBskyActorDefs.MutedWordsPref(items=items))
            from app.services.bluesky.bluesky_mutewords import normalizar_palabra
            cachear_palabras(account_id, [normalizar_palabra(w) for w in items])
            return True
        self._tarea(_bg, callback)

    def fijar_idiomas_async(self, account_id: int, idiomas: List[str],
                            callback: Optional[Callable] = None):
        """Idiomas de contenido que quieres ver."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._reemplazar_preferencia(
                client, "languagePref",
                models.AppBskyActorDefs.LanguagePref(content_languages=list(idiomas)))
        self._tarea(_bg, callback)

    def fijar_preferencia_hilos_async(self, account_id: int, orden: str = "oldest",
                                      seguidos_primero: bool = True,
                                      callback: Optional[Callable] = None):
        """Cómo se ordenan las respuestas de un hilo (oldest, newest, most-likes, random)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._reemplazar_preferencia(
                client, "threadViewPref",
                models.AppBskyActorDefs.ThreadViewPref(
                    sort=orden, prioritize_followed_users=bool(seguidos_primero)))
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # NOTIFICACIONES: PREFERENCIAS Y SUSCRIPCIONES
    # ------------------------------------------------------------------
    def preferencias_notificaciones_async(self, account_id: int,
                                          callback: Optional[Callable] = None):
        """Preferencias de notificación por tipo (me gusta, respuestas, menciones...)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.notification.get_preferences()
            prefs = _g(resp, "preferences", default=None)
            salida = {}
            for clave, etiqueta in (("like", "Me gusta"), ("repost", "Republicaciones"),
                                    ("follow", "Nuevos seguidores"), ("mention", "Menciones"),
                                    ("reply", "Respuestas"), ("quote", "Citas"),
                                    ("like_via_repost", "Me gusta a través de republicaciones"),
                                    ("repost_via_repost", "Republicaciones en cadena"),
                                    ("starterpack_joined", "Altas con tu paquete de inicio"),
                                    ("subscribed_post", "Publicaciones de cuentas seguidas de cerca"),
                                    ("verified", "Verificaciones"), ("unverified", "Verificaciones retiradas")):
                bloque = _g(prefs, clave, default=None)
                if bloque is None:
                    continue
                salida[clave] = {
                    "etiqueta": etiqueta,
                    "lista": bool(_g(bloque, "list", default=True)),
                    "aviso": bool(_g(bloque, "push", default=True)),
                    "solo_seguidos": str(_g(bloque, "include", default="all")) == "follows",
                }
            return salida
        self._tarea(_bg, callback)

    def fijar_preferencia_notificacion_async(self, account_id: int, tipo: str,
                                             en_lista: bool = True, aviso: bool = True,
                                             solo_seguidos: bool = False,
                                             callback: Optional[Callable] = None):
        """Cambia la preferencia de un tipo de notificación."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            filtro = models.AppBskyNotificationDefs.FilterablePreference(
                include=("follows" if solo_seguidos else "all"),
                list=bool(en_lista), push=bool(aviso))
            client.app.bsky.notification.put_preference_v2({tipo: filtro}) \
                if hasattr(client.app.bsky.notification, "put_preference_v2") \
                else client.app.bsky.notification.put_preferences_v2({tipo: filtro})
            return True
        self._tarea(_bg, callback)

    def suscripciones_actividad_async(self, account_id: int, callback: Optional[Callable] = None):
        """Cuentas a las que sigues 'de cerca' (te avisan cuando publican)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.notification.list_activity_subscriptions({"limit": 100})
            return [{"did": _g(a, "did", default=""), "handle": _g(a, "handle", default=""),
                     "nombre": _g(a, "display_name", "displayName", default="")}
                    for a in (_g(resp, "subscriptions", default=[]) or [])]
        self._tarea(_bg, callback)

    def suscribir_actividad_async(self, account_id: int, actor: str, publicaciones: bool = True,
                                  respuestas: bool = False, callback: Optional[Callable] = None):
        """Avisar (o dejar de avisar) cuando una cuenta publique."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._resolver_did(client, actor)
            if not did:
                raise WSocialServiceError(f"No se ha encontrado la cuenta {actor}.")
            client.app.bsky.notification.put_activity_subscription({
                "subject": did,
                "activity_subscription": {"post": bool(publicaciones), "reply": bool(respuestas)}})
            return {"did": did, "post": publicaciones, "reply": respuestas}
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # VERIFICACIÓN DE EDAD (normativa europea)
    # ------------------------------------------------------------------
    def estado_verificacion_edad_async(self, account_id: int, callback: Optional[Callable] = None):
        """Estado de la verificación de edad de la cuenta y si el servidor la exige."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            estado = {}
            try:
                r = client.app.bsky.ageassurance.get_state()
                estado["estado"] = str(_g(r, "status", default=""))
                estado["ultima"] = _g(r, "last_initiated_at", "lastInitiatedAt", default="")
            except Exception as e:
                log.debug(f"W Social: sin estado de verificación de edad: {e}")
            try:
                c = client.app.bsky.ageassurance.get_config()
                estado["requerida"] = bool(_g(c, "required", default=False))
                estado["pais"] = str(_g(c, "country_code", "countryCode", default=""))
            except Exception:
                pass
            return estado
        self._tarea(_bg, callback)

    def iniciar_verificacion_edad_async(self, account_id: int, correo: str = "", idioma: str = "es",
                                        pais: str = "ES", callback: Optional[Callable] = None):
        """Empieza la verificación de edad. Devuelve la dirección web donde el usuario la completa:
        el proceso se hace en el navegador, este cliente nunca maneja documentos de identidad."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.ageassurance.begin({
                "email": (correo or None), "language": idioma, "country_code": pais})
            return {"url": _g(resp, "url", "redirect_url", "redirectUrl", default=""),
                    "estado": str(_g(resp, "status", default=""))}
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # LECTURA DE LA CACHÉ DE NOTIFICACIONES
    # ------------------------------------------------------------------
    def notificaciones_cacheadas(self, account_id: int, limite: int = 200) -> List[Dict[str, Any]]:
        """Notificaciones ya descargadas, para pintar la lista sin esperar a la red."""
        filas = db.query(
            "SELECT reason, author_handle, author_name, texto, is_read, indexed_at, uri "
            "FROM wsocial_notifications WHERE account_id = ? ORDER BY indexed_at DESC LIMIT ?",
            (account_id, limite))
        return [{"reason": f[0], "reason_legible": MOTIVOS_NOTIFICACION.get(f[0], f[0]),
                 "author_handle": f[1], "author_name": f[2], "texto": f[3],
                 "is_read": bool(f[4]), "indexed_at": f[5], "uri": f[6]} for f in filas]
