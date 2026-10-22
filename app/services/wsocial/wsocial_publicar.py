# -*- coding: utf-8 -*-
"""Publicación en W Social: texto enriquecido, imágenes, vídeo, citas, hilos y acciones.

Mixin de WSocialService.

Dos ideas gobiernan este módulo:

1. **Nada se publica dos veces.** Antes de enviar, la publicación se apunta en la bandeja de
   salida (`wsocial_outbox`) con una clave de registro (rkey) fijada por el cliente. Si la
   respuesta se pierde (corte de red, tiempo agotado), el reintento usa la MISMA clave: el
   servidor la rechaza por duplicada o devuelve la que ya existía, y además se puede preguntar
   por ella con getRecord para saber si llegó.
2. **El texto se publica como lo escribió el usuario.** Los enlaces, menciones y etiquetas se
   marcan con facets (offsets en bytes UTF-8) usando el analizador puro compartido con Bluesky,
   que es común a AT Protocol.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

from app.services.wsocial.wsocial_common import (
    Callable, List, Optional, db, WSocialServiceError, task_manager, log, models,
    ETIQUETAS_CONTENIDO, LIMITE_CARACTERES, _g, _ahora_iso, json, rkey_de_uri)


class _PublicarMixin:
    # Límite de bytes por blob de imagen en el lexicon vigente de app.bsky.embed.images.
    # El avatar y el banner del perfil tienen su propio límite, más estricto.
    _LIMITE_BLOB = 2_000_000
    _LIMITE_BLOB_PERFIL = 1_000_000
    # Alfabeto base32 ordenable de AT Protocol para los identificadores de registro (TID).
    _B32_SORTABLE = "234567abcdefghijklmnopqrstuvwxyz"

    @classmethod
    def _generar_tid(cls) -> str:
        """Identificador de registro de AT Protocol (13 caracteres, ordenable por tiempo).

        Se usa como clave IDEMPOTENTE: al fijarla antes de crear el registro, un reintento con la
        misma clave no puede duplicar la publicación."""
        import random
        import time
        v = ((time.time_ns() // 1000) << 10) | random.randrange(0, 1024)
        s = ""
        for _ in range(13):
            s = cls._B32_SORTABLE[v & 31] + s
            v >>= 5
        return s

    # ------------------------------------------------------------------
    # ADJUNTOS
    # ------------------------------------------------------------------
    def _optimizar_bytes_imagen(self, original: bytes, ext: str = "", limite: int = None):
        """Deja una imagen por debajo del límite de bytes por blob, recomprimiendo solo si hace
        falta. Devuelve (bytes, (ancho, alto)); el tamaño sirve para la relación de aspecto."""
        import io
        from PIL import Image
        limite = limite or self._LIMITE_BLOB
        try:
            img = Image.open(io.BytesIO(original))
            ancho, alto = img.size
        except Exception:
            return original, None
        ext = (ext or "").lower()
        if len(original) <= limite and ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ""):
            return original, (ancho, alto)
        if getattr(img, "is_animated", False):
            if len(original) > limite:
                raise WSocialServiceError(
                    "El GIF animado supera el tamaño máximo admitido aquí "
                    f"({limite // 1_000_000} MB). Usa uno más ligero.")
            return original, (ancho, alto)
        img = img.convert("RGB")
        if max(img.size) > 2000:
            img.thumbnail((2000, 2000))
        for calidad in (90, 85, 80, 72, 64, 55, 45):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=calidad, optimize=True)
            if buf.tell() <= limite:
                return buf.getvalue(), img.size
        img.thumbnail((1280, 1280))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50, optimize=True)
        return buf.getvalue(), img.size

    def _subir_imagen(self, client, ruta: str):
        """Sube una imagen ya optimizada y devuelve (blob, relación de aspecto)."""
        import os
        with open(ruta, "rb") as f:
            original = f.read()
        datos, dims = self._optimizar_bytes_imagen(original, os.path.splitext(ruta)[1])
        resp = client.upload_blob(datos)
        aspect = None
        if dims and dims[0] and dims[1]:
            aspect = models.AppBskyEmbedDefs.AspectRatio(width=int(dims[0]), height=int(dims[1]))
        return _g(resp, "blob"), aspect

    def _subir_blob(self, client, ruta: str, para_perfil: bool = False):
        """Sube un archivo cualquiera y devuelve su blob. Con `para_perfil` aplica el límite
        más estricto del avatar y el banner."""
        import os
        with open(ruta, "rb") as f:
            datos = f.read()
        if para_perfil:
            datos, _dims = self._optimizar_bytes_imagen(
                datos, os.path.splitext(ruta)[1], limite=self._LIMITE_BLOB_PERFIL)
        return _g(client.upload_blob(datos), "blob")

    @staticmethod
    def _aspecto_video(ruta: str):
        """Relación de aspecto del vídeo con ffprobe (viene con el ffmpeg de data/bin). Es de
        mejor esfuerzo: sin ella el vídeo se publica igual."""
        try:
            import json as _json
            import subprocess
            res = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-select_streams", "v:0", ruta],
                capture_output=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            info = _json.loads(res.stdout or b"{}")
            stream = (info.get("streams") or [{}])[0]
            ancho, alto = int(stream.get("width") or 0), int(stream.get("height") or 0)
            rot = str(((stream.get("side_data_list") or [{}])[0]).get("rotation", "0"))
            if rot.lstrip("-") in ("90", "270"):
                ancho, alto = alto, ancho
            if ancho > 0 and alto > 0:
                return models.AppBskyEmbedDefs.AspectRatio(width=ancho, height=alto)
        except Exception as e:
            log.debug(f"No se pudo obtener la relación de aspecto del vídeo: {e}")
        return None

    def _subir_video(self, client, ruta: str, alt: str = ""):
        """Sube un vídeo por el servicio de vídeo y espera a que termine de procesarse.

        El servidor lo transcodifica; hasta que el trabajo no está listo, publicar con ese blob
        daría error, así que se consulta el estado hasta que acaba (con un tope de espera)."""
        import os
        import time as _t
        nombre = os.path.basename(ruta)
        with open(ruta, "rb") as f:
            datos = f.read()
        limites = None
        try:
            limites = client.app.bsky.video.get_upload_limits()
        except Exception as e:
            log.debug(f"No se pudieron consultar los límites de vídeo: {e}")
        if limites is not None and not _g(limites, "can_upload", "canUpload", default=True):
            motivo = _g(limites, "message", default="") or "el servidor no admite vídeos ahora mismo"
            raise WSocialServiceError(f"No se puede subir el vídeo: {motivo}")
        resp = client.app.bsky.video.upload_video(datos)
        job = _g(resp, "job_status", "jobStatus", default=None)
        job_id = _g(job, "job_id", "jobId", default="")
        blob = _g(job, "blob", default=None)
        # Espera activa acotada (unos 5 minutos): el procesado de vídeo tarda.
        for _ in range(150):
            if blob is not None:
                break
            _t.sleep(2)
            try:
                estado = client.app.bsky.video.get_job_status({"job_id": job_id})
            except Exception as e:
                log.debug(f"No se pudo consultar el estado del vídeo: {e}")
                continue
            js = _g(estado, "job_status", "jobStatus", default=None)
            fase = str(_g(js, "state", default="")).upper()
            blob = _g(js, "blob", default=None)
            if "FAILED" in fase:
                raise WSocialServiceError(
                    "El servidor no ha podido procesar el vídeo: {0}".format(
                        _g(js, "error", default="") or "error desconocido"))
        if blob is None:
            raise WSocialServiceError(
                f"El vídeo «{nombre}» sigue procesándose en el servidor. Inténtalo dentro de un rato.")
        return models.AppBskyEmbedVideo.Main(video=blob, alt=(alt or None),
                                             aspect_ratio=self._aspecto_video(ruta))

    # ------------------------------------------------------------------
    # TEXTO ENRIQUECIDO
    # ------------------------------------------------------------------
    def _resolver_did_cacheado(self, client, handle: str):
        """Resuelve un handle a DID con caché: los handles repetidos no vuelven a la red. Los
        fallos también se cachean unos minutos para no insistir con handles inexistentes."""
        import time as _t
        entrada = self._did_cache.get(handle)
        if entrada:
            did, ts = entrada
            if _t.time() - ts < (3600 if did else 300):
                return did
        try:
            did = _g(client.resolve_handle(handle), "did") or None
        except Exception:
            did = None
        self._did_cache[handle] = (did, _t.time())
        return did

    def _construir_facets(self, client, texto: str):
        """Marca enlaces, menciones y etiquetas del texto con offsets en bytes UTF-8.

        Usa el analizador PURO de `bluesky_facets`: es lógica de AT Protocol (no de una red
        concreta), está probada y compartirla evita que las dos redes se comporten distinto ante
        el mismo texto. Las menciones cuyo handle no resuelve se dejan como texto plano."""
        from app.services.bluesky.bluesky_facets import detectar_facets
        facets = []
        for f in detectar_facets(texto or ""):
            indice = models.AppBskyRichtextFacet.ByteSlice(
                byte_start=f["byte_start"], byte_end=f["byte_end"])
            if f["tipo"] == "link":
                feature = models.AppBskyRichtextFacet.Link(uri=f["valor"])
            elif f["tipo"] == "mention":
                did = self._resolver_did_cacheado(client, f["valor"])
                if not did:
                    continue
                feature = models.AppBskyRichtextFacet.Mention(did=did)
            elif f["tipo"] == "tag":
                feature = models.AppBskyRichtextFacet.Tag(tag=f["valor"])
            else:
                continue
            facets.append(models.AppBskyRichtextFacet.Main(index=indice, features=[feature]))
        return facets or None

    def _external_embed(self, client, texto: str):
        """Tarjeta de vista previa del primer enlace del texto (título, descripción y miniatura).
        Si no se puede obtener, el enlace se publica igual como texto clicable."""
        from app.services.bluesky.bluesky_facets import primera_url
        url = primera_url(texto or "")
        if not url:
            return None
        try:
            from urllib.parse import urljoin
            from bs4 import BeautifulSoup
            # La URL viene del contenido, así que la descarga va endurecida: se valida el destino
            # (nada de direcciones internas) y se limita el tamaño del cuerpo.
            from app.core.net import descargar_url_segura
            html_bytes, ctype = descargar_url_segura(url, max_bytes=1024 * 1024)
            if ctype and "html" not in ctype and "xml" not in ctype:
                return None
            soup = BeautifulSoup(html_bytes, "html.parser")

            def _meta(*props):
                for p in props:
                    tag = soup.find("meta", property=p) or soup.find("meta", attrs={"name": p})
                    if tag and tag.get("content"):
                        return tag["content"].strip()
                return ""

            titulo = (_meta("og:title", "twitter:title")
                      or (soup.title.string.strip() if soup.title and soup.title.string else "")
                      or url)
            desc = _meta("og:description", "twitter:description", "description")
            img_url = _meta("og:image", "twitter:image")
            thumb = None
            if img_url:
                try:
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    elif img_url.startswith("/"):
                        img_url = urljoin(url, img_url)
                    img_bytes, img_ctype = descargar_url_segura(img_url, max_bytes=5 * 1024 * 1024)
                    if img_bytes and (not img_ctype or img_ctype.startswith("image/")):
                        datos, _dims = self._optimizar_bytes_imagen(img_bytes)
                        thumb = _g(client.upload_blob(datos), "blob")
                except Exception as e:
                    log.debug(f"No se pudo subir la miniatura del enlace: {e}")
            external = models.AppBskyEmbedExternal.External(
                uri=url, title=(titulo or url)[:300], description=(desc or "")[:1000], thumb=thumb)
            return models.AppBskyEmbedExternal.Main(external=external)
        except Exception as e:
            log.debug(f"No se pudo generar la tarjeta del enlace: {e}")
            return None

    # ------------------------------------------------------------------
    # REFERENCIAS Y BANDEJA DE SALIDA
    # ------------------------------------------------------------------
    def _strong_ref(self, client, uri: str):
        """Referencia fuerte (uri + cid) de una publicación, resolviendo el cid actual."""
        cid = None
        fila = db.query_one("SELECT cid FROM wsocial_posts WHERE uri = ? LIMIT 1", (uri,))
        if fila and fila[0]:
            cid = fila[0]
        if not cid:
            resp = client.app.bsky.feed.get_posts({"uris": [uri]})
            posts = _g(resp, "posts", default=[]) or []
            if not posts:
                raise WSocialServiceError("No se ha encontrado la publicación a la que respondes o citas.")
            cid = _g(posts[0], "cid", default="")
        return models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)

    def _construir_reply(self, client, reply_uri: str):
        """Referencia de respuesta (raíz + padre). La raíz se hereda de la publicación a la que
        se responde; si no la tiene, ella misma es la raíz del hilo."""
        padre = self._strong_ref(client, reply_uri)
        fila = db.query_one("SELECT reply_root_uri, reply_root_cid FROM wsocial_posts "
                            "WHERE uri = ? LIMIT 1", (reply_uri,))
        if fila and fila[0] and fila[1]:
            raiz = models.ComAtprotoRepoStrongRef.Main(uri=fila[0], cid=fila[1])
        else:
            raiz = padre
        return models.AppBskyFeedPost.ReplyRef(root=raiz, parent=padre)

    def _outbox_crear(self, account_id: int, rkey: str, payload: dict,
                      collection: str = "app.bsky.feed.post") -> int:
        """Apunta una publicación en la bandeja de salida ANTES de enviarla."""
        return db.execute_insert(
            "INSERT INTO wsocial_outbox (account_id, rkey, collection, payload_json, estado, "
            "created_at) VALUES (?,?,?,?,'pending',?)",
            (account_id, rkey, collection, json.dumps(payload, ensure_ascii=False), _ahora_iso()))

    def _outbox_actualizar(self, outbox_id: int, estado: str, uri: str = None, error: str = None):
        """Actualiza el estado de una entrada de la bandeja de salida."""
        try:
            db.execute(
                "UPDATE wsocial_outbox SET estado = ?, uri = COALESCE(?, uri), "
                "error = ?, intentos = intentos + 1, updated_at = ? WHERE id = ?",
                (estado, uri, error, _ahora_iso(), outbox_id))
        except Exception as e:
            log.debug(f"No se pudo actualizar la bandeja de salida: {e}")

    def _es_error_ambiguo(self, e: Exception) -> bool:
        """True si el error deja en duda si la publicación llegó (tiempo agotado, corte de red o
        error del servidor). En ese caso NO se puede reintentar a ciegas: primero se comprueba."""
        texto = str(e).lower()
        if any(p in texto for p in ("timeout", "timed out", "connection", "read error", "reset")):
            return True
        try:
            estado = getattr(getattr(e, "response", None), "status_code", None)
            return estado is not None and int(estado) >= 500
        except Exception:
            return False

    def _reconciliar_getrecord(self, client, did: str, collection: str, rkey: str):
        """Pregunta al servidor si el registro con esa clave existe. Es lo que convierte un
        error ambiguo en una respuesta clara: o llegó (y se recupera su URI) o no llegó."""
        try:
            resp = client.com.atproto.repo.get_record(
                {"repo": did, "collection": collection, "rkey": rkey})
            return _g(resp, "uri", default=""), _g(resp, "cid", default="")
        except Exception:
            return "", ""

    def listar_outbox(self, account_id: int) -> list:
        """Publicaciones pendientes o fallidas, para poder reintentarlas o descartarlas."""
        filas = db.query(
            "SELECT id, rkey, collection, payload_json, estado, uri, error, intentos, created_at "
            "FROM wsocial_outbox WHERE account_id = ? AND estado <> 'ok' ORDER BY id DESC",
            (account_id,))
        salida = []
        for f in filas:
            try:
                payload = json.loads(f[3] or "{}")
            except Exception:
                payload = {}
            salida.append({"id": f[0], "rkey": f[1], "collection": f[2], "payload": payload,
                           "estado": f[4], "uri": f[5], "error": f[6], "intentos": f[7],
                           "created_at": f[8], "texto": payload.get("texto", "")})
        return salida

    def borrar_outbox(self, outbox_id: int):
        """Descarta una entrada de la bandeja de salida."""
        db.execute("DELETE FROM wsocial_outbox WHERE id = ?", (outbox_id,))

    # ------------------------------------------------------------------
    # PUBLICAR
    # ------------------------------------------------------------------
    def publicar_post_async(self, account_id: int, texto: str,
                            imagenes: Optional[List[str]] = None,
                            alt_texts: Optional[List[str]] = None,
                            video: str = "", video_alt: str = "",
                            reply_uri: str = "", quote_uri: str = "",
                            langs: Optional[List[str]] = None,
                            labels: Optional[List[str]] = None,
                            reply_control: str = "", quote_control: str = "",
                            rkey_previo: str = "", outbox_id: int = 0,
                            callback: Optional[Callable] = None):
        """Publica: texto con facets y, opcionalmente, hasta 4 imágenes o un vídeo, respondiendo
        a otra publicación o citándola.

        `reply_control` limita quién puede responder (everybody, following, mentioned, nobody) y
        `quote_control` si se puede citar (everybody, nobody); ambos se materializan como
        registros aparte asociados a la publicación."""
        imagenes = [r for r in (imagenes or []) if r]
        alt_texts = list(alt_texts or [])
        texto = texto or ""

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            if not texto.strip() and not imagenes and not video:
                raise WSocialServiceError("No se puede publicar algo vacío.")
            if len(imagenes) > 4:
                raise WSocialServiceError("Solo se pueden adjuntar 4 imágenes a una publicación.")
            if imagenes and video:
                raise WSocialServiceError("Una publicación puede llevar imágenes o un vídeo, no las dos cosas.")

            # Clave de registro idempotente: se decide AQUÍ y se apunta antes de enviar.
            rkey = rkey_previo or self._generar_tid()
            oid = outbox_id or self._outbox_crear(account_id, rkey, {
                "texto": texto, "imagenes": imagenes, "video": video,
                "reply_uri": reply_uri, "quote_uri": quote_uri})

            try:
                facets = self._construir_facets(client, texto)
                # Embed: imágenes > vídeo > cita (con o sin media) > tarjeta de enlace.
                embed = None
                if imagenes:
                    lista = []
                    for i, ruta in enumerate(imagenes):
                        blob, aspecto = self._subir_imagen(client, ruta)
                        lista.append(models.AppBskyEmbedImages.Image(
                            image=blob, alt=(alt_texts[i] if i < len(alt_texts) else "") or "",
                            aspect_ratio=aspecto))
                    embed = models.AppBskyEmbedImages.Main(images=lista)
                elif video:
                    embed = self._subir_video(client, video, video_alt)
                if quote_uri:
                    citado = models.AppBskyEmbedRecord.Main(record=self._strong_ref(client, quote_uri))
                    embed = (models.AppBskyEmbedRecordWithMedia.Main(record=citado, media=embed)
                             if embed is not None else citado)
                if embed is None:
                    embed = self._external_embed(client, texto)

                etiquetas = [v for v in (labels or []) if v in ETIQUETAS_CONTENIDO]
                labels_obj = None
                if etiquetas:
                    labels_obj = models.ComAtprotoLabelDefs.SelfLabels(
                        values=[models.ComAtprotoLabelDefs.SelfLabel(val=v) for v in etiquetas])

                record = models.AppBskyFeedPost.Record(
                    created_at=_ahora_iso(), text=texto,
                    facets=facets,
                    reply=(self._construir_reply(client, reply_uri) if reply_uri else None),
                    embed=embed, langs=(langs or None), labels=labels_obj)
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.feed.post",
                        rkey=rkey, record=record))
                uri = _g(resp, "uri", default="")
                self._outbox_actualizar(oid, "ok", uri=uri)
                self._aplicar_controles(client, yo["did"], uri, reply_control, quote_control)
                return {"uri": uri, "cid": _g(resp, "cid", default=""), "rkey": rkey}
            except Exception as e:
                # Error AMBIGUO: puede que sí se publicara. Se comprueba por la clave antes de
                # dar nada por perdido, que es justo lo que evita los duplicados.
                if self._es_error_ambiguo(e):
                    uri, _cid = self._reconciliar_getrecord(
                        client, yo["did"], "app.bsky.feed.post", rkey)
                    if uri:
                        self._outbox_actualizar(oid, "ok", uri=uri)
                        return {"uri": uri, "cid": _cid, "rkey": rkey, "reconciliado": True}
                self._outbox_actualizar(oid, "error", error=str(e)[:500])
                raise
        self._tarea(_bg, callback)

    def _aplicar_controles(self, client, did: str, uri: str, reply_control: str, quote_control: str):
        """Crea los registros que limitan quién puede responder y quién puede citar.

        Van asociados a la publicación por su misma clave de registro, así que se crean después
        de publicarla. Si fallan, la publicación ya está hecha: se avisa por el registro y no se
        tira todo por eso."""
        if not uri:
            return
        rkey = rkey_de_uri(uri)
        if reply_control and reply_control != "everybody":
            try:
                if reply_control == "nobody":
                    allow = []
                elif reply_control == "following":
                    allow = [models.AppBskyFeedThreadgate.FollowingRule()]
                elif reply_control == "mentioned":
                    allow = [models.AppBskyFeedThreadgate.MentionRule()]
                else:
                    allow = None
                if allow is not None:
                    client.com.atproto.repo.create_record(
                        models.ComAtprotoRepoCreateRecord.Data(
                            repo=did, collection="app.bsky.feed.threadgate", rkey=rkey,
                            record=models.AppBskyFeedThreadgate.Record(
                                post=uri, allow=allow, created_at=_ahora_iso())))
            except Exception as e:
                log.debug(f"No se pudo limitar quién responde: {e}")
        if quote_control == "nobody":
            try:
                client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=did, collection="app.bsky.feed.postgate", rkey=rkey,
                        record=models.AppBskyFeedPostgate.Record(
                            post=uri, created_at=_ahora_iso(),
                            embedding_rules=[models.AppBskyFeedPostgate.DisableRule()])))
            except Exception as e:
                log.debug(f"No se pudo impedir que citen la publicación: {e}")

    def reintentar_outbox_async(self, account_id: int, outbox_id: int,
                                callback: Optional[Callable] = None):
        """Reintenta una publicación pendiente. Primero pregunta al servidor si aquella ya llegó
        (misma clave de registro): si llegó, no se vuelve a publicar, solo se marca como hecha."""
        fila = db.query_one(
            "SELECT rkey, collection, payload_json FROM wsocial_outbox WHERE id = ? AND account_id = ?",
            (outbox_id, account_id))
        if not fila:
            if callback:
                callback(False, "Esa publicación ya no está pendiente.")
            return
        rkey, collection, payload_json = fila
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {}

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            uri, cid = self._reconciliar_getrecord(client, yo["did"], collection, rkey)
            if uri:
                self._outbox_actualizar(outbox_id, "ok", uri=uri)
                return {"uri": uri, "cid": cid, "rkey": rkey, "ya_estaba": True}
            return {"reenviar": True, "payload": payload, "rkey": rkey}

        def _fin(res, error):
            if error:
                if callback:
                    callback(False, error)
                return
            if res.get("reenviar"):
                p = res["payload"]
                self.publicar_post_async(
                    account_id, p.get("texto", ""), imagenes=p.get("imagenes"),
                    video=p.get("video", ""), reply_uri=p.get("reply_uri", ""),
                    quote_uri=p.get("quote_uri", ""), rkey_previo=res["rkey"],
                    outbox_id=outbox_id, callback=callback)
                return
            if callback:
                callback(True, res)
        self._tarea(_bg, _fin, traducir=False)

    def publicar_hilo_async(self, account_id: int, textos: List[str],
                            langs: Optional[List[str]] = None,
                            labels: Optional[List[str]] = None,
                            reply_control: str = "",
                            callback: Optional[Callable] = None):
        """Publica varias publicaciones encadenadas (cada una responde a la anterior)."""
        textos = [t.strip() for t in textos if t and t.strip()]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            root_uri = root_cid = prev_uri = prev_cid = None
            uris = []
            labels_obj = None
            etiquetas = [v for v in (labels or []) if v in ETIQUETAS_CONTENIDO]
            if etiquetas:
                labels_obj = models.ComAtprotoLabelDefs.SelfLabels(
                    values=[models.ComAtprotoLabelDefs.SelfLabel(val=v) for v in etiquetas])
            for i, texto in enumerate(textos):
                if ctx.is_cancelled():
                    raise WSocialServiceError("Hilo cancelado.")
                ctx.report_progress(int((i / len(textos)) * 100),
                                    f"Publicando {i + 1} de {len(textos)}...")
                reply = None
                if prev_uri:
                    reply = models.AppBskyFeedPost.ReplyRef(
                        root=models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid),
                        parent=models.ComAtprotoRepoStrongRef.Main(uri=prev_uri, cid=prev_cid))
                record = models.AppBskyFeedPost.Record(
                    created_at=_ahora_iso(), text=texto,
                    facets=self._construir_facets(client, texto), reply=reply,
                    langs=(langs or None), labels=(labels_obj if i == 0 else None))
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.feed.post",
                        rkey=self._generar_tid(), record=record))
                uri, cid = _g(resp, "uri"), _g(resp, "cid")
                uris.append(uri)
                if i == 0:
                    root_uri, root_cid = uri, cid
                    self._aplicar_controles(client, yo["did"], uri, reply_control, "")
                prev_uri, prev_cid = uri, cid
            return uris
        self._tarea(_bg, callback, sonidos=True)

    def borrar_post_async(self, account_id: int, uri: str, callback: Optional[Callable] = None):
        """Borra una publicación propia (y la quita de la caché local)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            client.com.atproto.repo.delete_record(
                models.ComAtprotoRepoDeleteRecord.Data(
                    repo=yo["did"], collection="app.bsky.feed.post", rkey=rkey_de_uri(uri)))
            db_id = f"{account_id}:{uri}"
            db.execute("DELETE FROM wsocial_timeline_posts WHERE post_db_id = ?", (db_id,))
            try:
                db.execute("DELETE FROM wsocial_posts_fts WHERE post_db_id = ?", (db_id,))
            except Exception:
                pass
            db.execute("DELETE FROM wsocial_posts WHERE id = ?", (db_id,))
            return True
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # ACCIONES SOBRE UNA PUBLICACIÓN
    # ------------------------------------------------------------------
    def ejecutar_accion_post_async(self, account_id: int, post_db_id: str, accion: str,
                                   callback: Optional[Callable] = None):
        """Acciones de un solo paso sobre una publicación cacheada:

        like / unlike, repost / unrepost, bookmark / unbookmark.

        Los marcadores usan el servicio propio de AT Protocol (app.bsky.bookmark), no un registro
        del repositorio: son privados y no aparecen en tu repositorio público."""
        fila = db.query_one("SELECT uri, cid, liked_uri, reposted_uri, bookmarked "
                            "FROM wsocial_posts WHERE id = ?", (post_db_id,))
        if not fila:
            if callback:
                callback(False, "No se ha encontrado la publicación.")
            return
        uri, cid, liked_uri, reposted_uri, bookmarked = fila

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            ref = models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)
            if accion == "like":
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.feed.like",
                        rkey=self._generar_tid(),
                        record=models.AppBskyFeedLike.Record(created_at=_ahora_iso(), subject=ref)))
                nuevo = _g(resp, "uri", default="")
                db.execute("UPDATE wsocial_posts SET liked_uri = ?, like_count = like_count + 1 "
                           "WHERE id = ?", (nuevo, post_db_id))
                return {"accion": "like", "uri": nuevo}
            if accion == "unlike":
                if liked_uri:
                    client.com.atproto.repo.delete_record(
                        models.ComAtprotoRepoDeleteRecord.Data(
                            repo=yo["did"], collection="app.bsky.feed.like",
                            rkey=rkey_de_uri(liked_uri)))
                db.execute("UPDATE wsocial_posts SET liked_uri = '', "
                           "like_count = MAX(0, like_count - 1) WHERE id = ?", (post_db_id,))
                return {"accion": "unlike"}
            if accion == "repost":
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"], collection="app.bsky.feed.repost",
                        rkey=self._generar_tid(),
                        record=models.AppBskyFeedRepost.Record(created_at=_ahora_iso(), subject=ref)))
                nuevo = _g(resp, "uri", default="")
                db.execute("UPDATE wsocial_posts SET reposted_uri = ?, "
                           "repost_count = repost_count + 1 WHERE id = ?", (nuevo, post_db_id))
                return {"accion": "repost", "uri": nuevo}
            if accion == "unrepost":
                if reposted_uri:
                    client.com.atproto.repo.delete_record(
                        models.ComAtprotoRepoDeleteRecord.Data(
                            repo=yo["did"], collection="app.bsky.feed.repost",
                            rkey=rkey_de_uri(reposted_uri)))
                db.execute("UPDATE wsocial_posts SET reposted_uri = '', "
                           "repost_count = MAX(0, repost_count - 1) WHERE id = ?", (post_db_id,))
                return {"accion": "unrepost"}
            if accion == "bookmark":
                client.app.bsky.bookmark.create_bookmark({"uri": uri, "cid": cid})
                db.execute("UPDATE wsocial_posts SET bookmarked = 1 WHERE id = ?", (post_db_id,))
                return {"accion": "bookmark"}
            if accion == "unbookmark":
                client.app.bsky.bookmark.delete_bookmark({"uri": uri})
                db.execute("UPDATE wsocial_posts SET bookmarked = 0 WHERE id = ?", (post_db_id,))
                db.execute("DELETE FROM wsocial_timeline_posts WHERE account_id = ? AND "
                           "timeline = 'bookmarks' AND post_db_id = ?", (account_id, post_db_id))
                return {"accion": "unbookmark"}
            raise WSocialServiceError(f"Acción desconocida: {accion}")
        self._tarea(_bg, callback)

    def fijar_post_async(self, account_id: int, post_uri: str, post_cid: str,
                         fijar: bool = True, callback: Optional[Callable] = None):
        """Fija (o deja de fijar) una publicación propia en el perfil."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            try:
                actual = client.com.atproto.repo.get_record(
                    {"repo": yo["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
                registro = _g(actual, "value", default=None)
            except Exception:
                registro = None
            perfil = models.AppBskyActorProfile.Record(
                display_name=_g(registro, "display_name", "displayName", default=None),
                description=_g(registro, "description", default=None),
                avatar=_g(registro, "avatar", default=None),
                banner=_g(registro, "banner", default=None),
                pinned_post=(models.ComAtprotoRepoStrongRef.Main(uri=post_uri, cid=post_cid)
                             if fijar else None))
            client.com.atproto.repo.put_record(
                models.ComAtprotoRepoPutRecord.Data(
                    repo=yo["did"], collection="app.bsky.actor.profile", rkey="self", record=perfil))
            return fijar
        self._tarea(_bg, callback)

    def silenciar_hilo_async(self, account_id: int, root_uri: str, silenciar: bool = True,
                             callback: Optional[Callable] = None):
        """Deja de recibir notificaciones de un hilo (o vuelve a recibirlas)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if silenciar:
                client.app.bsky.graph.mute_thread({"root": root_uri})
            else:
                client.app.bsky.graph.unmute_thread({"root": root_uri})
            return silenciar
        self._tarea(_bg, callback)

    def registrar_interaccion_async(self, account_id: int, uri: str, evento: str,
                                    feed_uri: str = "", callback: Optional[Callable] = None):
        """Envía una señal de interacción al feed que sirvió la publicación (por ejemplo, que se
        ha abierto o que no interesa). Es lo que permite a los feeds algorítmicos aprender; es
        opcional y silencioso: si falla, no molesta al usuario."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            interaccion = models.AppBskyFeedDefs.Interaction(
                item=uri, event=evento, feed_context=(feed_uri or None))
            client.app.bsky.feed.send_interactions({"interactions": [interaccion]})
            return True
        self._tarea(_bg, callback, silencioso=True)

    # ------------------------------------------------------------------
    # BORRADORES GUARDADOS EN EL SERVIDOR
    # ------------------------------------------------------------------
    def listar_borradores_async(self, account_id: int, callback: Optional[Callable] = None):
        """Borradores guardados en el servidor (se sincronizan entre dispositivos)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.draft.get_drafts({"limit": 100})
            salida = []
            with db.transaccion() as conn:
                conn.execute("DELETE FROM wsocial_drafts WHERE account_id = ?", (account_id,))
                for d in (_g(resp, "drafts", default=[]) or []):
                    did_draft = _g(d, "id", "draft_id", "draftId", default="")
                    contenido = _g(d, "content", default=None)
                    texto = _g(contenido, "text", default="") or _g(d, "text", default="") or ""
                    actualizado = _g(d, "updated_at", "updatedAt", default="") or _g(
                        d, "created_at", "createdAt", default="")
                    conn.execute(
                        "INSERT OR REPLACE INTO wsocial_drafts "
                        "(id, account_id, draft_id, texto, payload_json, updated_at) VALUES (?,?,?,?,?,?)",
                        (f"{account_id}:{did_draft}", account_id, did_draft, texto,
                         json.dumps(self._a_dict(d), ensure_ascii=False), actualizado))
                    salida.append({"draft_id": did_draft, "texto": texto, "updated_at": actualizado})
            return salida
        self._tarea(_bg, callback)

    @staticmethod
    def _a_dict(obj):
        """Convierte un modelo de atproto en un dict simple (para guardarlo tal cual)."""
        for metodo in ("model_dump", "dict"):
            try:
                return getattr(obj, metodo)()
            except Exception:
                continue
        try:
            return dict(obj)
        except Exception:
            return {"repr": str(obj)}

    def crear_borrador_async(self, account_id: int, texto: str, callback: Optional[Callable] = None):
        """Guarda un borrador en el servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            resp = client.app.bsky.draft.create_draft({"content": {"text": texto}})
            return _g(resp, "id", "draft_id", "draftId", default="")
        self._tarea(_bg, callback)

    def actualizar_borrador_async(self, account_id: int, draft_id: str, texto: str,
                                  callback: Optional[Callable] = None):
        """Reescribe un borrador del servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.draft.update_draft({"id": draft_id, "content": {"text": texto}})
            db.execute("UPDATE wsocial_drafts SET texto = ? WHERE id = ?",
                       (texto, f"{account_id}:{draft_id}"))
            return True
        self._tarea(_bg, callback)

    def borrar_borrador_async(self, account_id: int, draft_id: str, callback: Optional[Callable] = None):
        """Borra un borrador del servidor."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.app.bsky.draft.delete_draft({"id": draft_id})
            db.execute("DELETE FROM wsocial_drafts WHERE id = ?", (f"{account_id}:{draft_id}",))
            return True
        self._tarea(_bg, callback)

    # ------------------------------------------------------------------
    # PERFIL PROPIO
    # ------------------------------------------------------------------
    def editar_perfil_async(self, account_id: int, nombre: str = None, descripcion: str = None,
                            avatar: str = "", banner: str = "", callback: Optional[Callable] = None):
        """Actualiza el perfil conservando lo que no se toca (los campos a None no se cambian)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            try:
                actual = client.com.atproto.repo.get_record(
                    {"repo": yo["did"], "collection": "app.bsky.actor.profile", "rkey": "self"})
                registro = _g(actual, "value", default=None)
            except Exception:
                registro = None
            blob_avatar = self._subir_blob(client, avatar, para_perfil=True) if avatar else \
                _g(registro, "avatar", default=None)
            blob_banner = self._subir_blob(client, banner, para_perfil=True) if banner else \
                _g(registro, "banner", default=None)
            perfil = models.AppBskyActorProfile.Record(
                display_name=(nombre if nombre is not None
                              else _g(registro, "display_name", "displayName", default=None)),
                description=(descripcion if descripcion is not None
                             else _g(registro, "description", default=None)),
                avatar=blob_avatar, banner=blob_banner,
                pinned_post=_g(registro, "pinned_post", "pinnedPost", default=None))
            client.com.atproto.repo.put_record(
                models.ComAtprotoRepoPutRecord.Data(
                    repo=yo["did"], collection="app.bsky.actor.profile", rkey="self", record=perfil))
            return True
        self._tarea(_bg, callback)

    def fijar_estado_async(self, account_id: int, estado: str = "", duracion_min: int = 60,
                           callback: Optional[Callable] = None):
        """Publica (o retira) tu estado temporal: lo que W muestra junto al perfil, por ejemplo
        que estás en directo. Con `estado` vacío se retira."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            if not estado:
                client.com.atproto.repo.delete_record(
                    models.ComAtprotoRepoDeleteRecord.Data(
                        repo=yo["did"], collection="app.bsky.actor.status", rkey="self"))
                return ""
            registro = models.AppBskyActorStatus.Record(
                status=estado, created_at=_ahora_iso(),
                duration_minutes=max(1, int(duracion_min or 60)))
            client.com.atproto.repo.put_record(
                models.ComAtprotoRepoPutRecord.Data(
                    repo=yo["did"], collection="app.bsky.actor.status", rkey="self",
                    record=registro))
            return estado
        self._tarea(_bg, callback)
