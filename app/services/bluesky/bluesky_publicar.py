# -*- coding: utf-8 -*-
"""Publicación (texto, media, facets), acciones y borrado.

Mixin de BlueskyService (parte de la refactorización modular).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
from app.services.bluesky.bluesky_common import (
    Callable, List, Optional, db, BlueskyServiceError, task_manager, log, models, ETIQUETAS_CONTENIDO,
    _g, _ahora_iso)


class _PublicarMixin:
    # Límite de imagen CLÁSICA del lexicón vigente de app.bsky.embed.images: 2.000.000 de
    # bytes (el antiguo 1.000.000 obligaba a recomprimir imágenes ya válidas, con coste de
    # CPU y pérdida de calidad; auditoría Bluesky 6.3). OJO: avatar y banner de PERFIL
    # siguen limitados a 1.000.000 (ver _LIMITE_BLOB_PERFIL).
    _LIMITE_BLOB = 2_000_000
    _LIMITE_BLOB_PERFIL = 1_000_000

    # Alfabeto base32-sortable de AT Protocol para los TID (identificadores de registro).
    _B32_SORTABLE = "234567abcdefghijklmnopqrstuvwxyz"

    @classmethod
    def _generar_tid(cls) -> str:
        """TID de AT Protocol (13 caracteres, ordenable): microsegundos + clock id.
        Sirve como RKEY IDEMPOTENTE (auditoría Bluesky 6.6): al fijarlo ANTES de crear
        el registro, un reintento con el mismo rkey no puede duplicar la publicación
        (el PDS rechaza el segundo create con ese rkey) y un timeout ambiguo puede
        reconciliarse consultando getRecord con él."""
        import random
        import time
        v = ((time.time_ns() // 1000) << 10) | random.randrange(0, 1024)
        s = ""
        for _ in range(13):
            s = cls._B32_SORTABLE[v & 31] + s
            v >>= 5
        return s

    def _subir_blob(self, client, ruta: str, para_perfil: bool = False):
        """Sube un archivo y devuelve el blob para referenciarlo en un embed.

        Con `para_perfil=True` la imagen pasa por la canalización de PERFIL: avatar y
        banner mantienen el límite de 1.000.000 de bytes del lexicón de perfil (distinto
        del de imágenes de post) y se validan/optimizan antes de subir; antes se subían
        tal cual y un archivo grande fallaba en el servidor (auditoría Bluesky 6.3)."""
        import os as _os
        with open(ruta, "rb") as f:
            datos = f.read()
        if para_perfil:
            datos, _dims = self._optimizar_bytes_imagen(
                datos, _os.path.splitext(ruta)[1], limite=self._LIMITE_BLOB_PERFIL)
        resp = client.upload_blob(datos)
        return _g(resp, "blob")

    def _optimizar_bytes_imagen(self, original: bytes, ext: str = "", limite: int = None):
        """Núcleo: dada una imagen EN BYTES, devuelve (bytes, (ancho, alto)) por debajo del
        límite de bytes por blob (2.000.000 para imágenes de post según el lexicón vigente;
        1.000.000 para avatar/banner), recomprimiendo/redimensionando a JPEG si hace falta.
        Conserva el original si ya cabe y es de un formato aceptado."""
        import io
        from PIL import Image
        limite = limite or self._LIMITE_BLOB
        try:
            img = Image.open(io.BytesIO(original))
            ancho, alto = img.size
        except Exception:
            return original, None
        ext = (ext or "").lower()
        # Si ya cabe y el formato es aceptado (o no se conoce la extensión), no se toca.
        if len(original) <= limite and ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ""):
            return original, (ancho, alto)
        if getattr(img, "is_animated", False):
            if len(original) > limite:
                raise BlueskyServiceError(
                    "El GIF animado supera el tamaño máximo que admite Bluesky aquí "
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

    def _imagen_optimizada(self, ruta: str):
        """Lee una imagen de disco y la deja lista para Bluesky (ver _optimizar_bytes_imagen).
        Devuelve (bytes, (ancho, alto)) para el blob y su aspectRatio."""
        import os
        with open(ruta, "rb") as f:
            original = f.read()
        return self._optimizar_bytes_imagen(original, os.path.splitext(ruta)[1])

    def _subir_imagen(self, client, ruta: str):
        """Sube una imagen (optimizada al límite de Bluesky) y devuelve (blob, aspect_ratio)."""
        datos, dims = self._imagen_optimizada(ruta)
        resp = client.upload_blob(datos)
        aspect = None
        if dims and dims[0] and dims[1]:
            aspect = models.AppBskyEmbedDefs.AspectRatio(width=int(dims[0]), height=int(dims[1]))
        return _g(resp, "blob"), aspect

    def _external_embed(self, client, texto: str):
        """Si el texto contiene un enlace, genera la TARJETA de vista previa (external embed) con el
        título, la descripción y la miniatura del enlace (Open Graph), como hace el cliente oficial
        de Bluesky. Devuelve el embed o None si no hay enlace o no se pudo obtener (en cuyo caso el
        enlace se publica igual, como facet clicable)."""
        from .bluesky_facets import primera_url
        # El parser de facets ya recorta la puntuación final: "mira https://x.com." no
        # debe pedir la página con el punto incluido (auditoría 6.4).
        url = primera_url(texto or "")
        if not url:
            return None
        try:
            from urllib.parse import urljoin
            from bs4 import BeautifulSoup
            # Descarga ENDURECIDA (auditoría Bluesky 6.5): la URL viene del contenido, así
            # que se valida contra destinos privados/loopback (SSRF), se limita el cuerpo
            # (1 MB de HTML) y las redirecciones se revalidan. Antes se descargaba directo
            # y sin límites de memoria ni de destino.
            from app.core.net import descargar_url_segura
            html_bytes, ctype = descargar_url_segura(url, max_bytes=1024 * 1024)
            if ctype and "html" not in ctype and "xml" not in ctype:
                return None   # no es una página: sin tarjeta (el enlace queda como facet)
            soup = BeautifulSoup(html_bytes, "html.parser")

            def _meta(*props):
                for p in props:
                    tag = soup.find("meta", property=p) or soup.find("meta", attrs={"name": p})
                    if tag and tag.get("content"):
                        return tag["content"].strip()
                return ""

            titulo = _meta("og:title", "twitter:title") or (soup.title.string.strip() if soup.title and soup.title.string else "") or url
            desc = _meta("og:description", "twitter:description", "description")
            img_url = _meta("og:image", "twitter:image")
            thumb = None
            if img_url:
                try:
                    if img_url.startswith("//"):
                        img_url = "https:" + img_url
                    elif img_url.startswith("/"):
                        img_url = urljoin(url, img_url)
                    # La miniatura también pasa por la descarga endurecida (máx. 5 MB).
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

    def _resolver_did_cacheado(self, client, handle: str):
        """Resuelve un handle a DID con CACHÉ con TTL (auditoría 6.4: cada mención
        resolvía en secuencia con una petición de red; los handles repetidos ya no
        vuelven a la red). Los fallos también se cachean (5 min) para no martillear
        con handles inválidos."""
        import time as _t
        if not hasattr(self, "_did_cache"):
            self._did_cache = {}
        entrada = self._did_cache.get(handle)
        if entrada:
            did, ts = entrada
            ttl = 3600 if did else 300
            if _t.time() - ts < ttl:
                return did
        try:
            did = _g(client.resolve_handle(handle), "did") or None
        except Exception:
            did = None
        self._did_cache[handle] = (did, _t.time())
        return did

    def _construir_facets(self, client, texto: str):
        """Construye los facets del texto (offsets en bytes UTF-8) con el parser puro de
        bluesky_facets (auditoría 6.4): enlaces sin la puntuación final, dominios
        desnudos, menciones con límite anterior (nunca dentro de un correo o URL),
        hashtags, sin solapes y en orden. Las menciones cuyo handle no resuelve a DID
        se dejan como texto plano (regla del cliente oficial)."""
        from .bluesky_facets import detectar_facets
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

    @staticmethod
    def _aspecto_video(ruta: str):
        """Relación de aspecto del vídeo con ffprobe (viene con el ffmpeg de data/bin).
        Mejor esfuerzo: sin ella el vídeo se publica igual, solo que el cliente oficial
        lo muestra con un marco genérico (auditoría BSKY-002)."""
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
            # Si el vídeo está rotado 90/270º, el ancho y el alto van intercambiados.
            rot = str(((stream.get("side_data_list") or [{}])[0]).get("rotation", "0"))
            if rot.lstrip("-") in ("90", "270"):
                ancho, alto = alto, ancho
            if ancho > 0 and alto > 0:
                return models.AppBskyEmbedDefs.AspectRatio(width=ancho, height=alto)
        except Exception as e:
            log.debug(f"No se pudo obtener la relación de aspecto del vídeo: {e}")
        return None

    def _strong_ref(self, client, uri: str):
        """Devuelve un StrongRef(uri, cid) resolviendo el cid actual del post."""
        cid = None
        fila = db.query_one("SELECT cid FROM bluesky_posts WHERE uri = ? LIMIT 1", (uri,))
        if fila and fila[0]:
            cid = fila[0]
        if not cid:
            r = client.get_posts([uri])
            posts = _g(r, "posts", default=[])
            if posts:
                cid = _g(posts[0], "cid")
        return models.ComAtprotoRepoStrongRef.Main(uri=uri, cid=cid)

    def contexto_respuesta_async(self, account_id: int, uri: str, callback=None):
        """A quién respondes y a quién avisará Bluesky, para mostrarlo en el editor.

        Bluesky no necesita menciones para avisar: el servidor notifica una respuesta a los
        autores de las publicaciones de encima en el hilo (hasta 4 niveles), como hace la app
        oficial. Devuelve {"padre": {handle, nombre, texto}, "avisados": [{handle, nombre}],
        "yo": handle} — sin la cuenta propia en "avisados" (a uno mismo no se le avisa)."""
        from app.core.at_lectura import AVISO_RESPUESTA_NIVELES, contexto_desde_hilo

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id) or {}
            resp = client.get_post_thread(uri=uri, depth=0,
                                          parent_height=AVISO_RESPUESTA_NIVELES - 1)
            return contexto_desde_hilo(_g(resp, "thread"), yo.get("did", ""), yo.get("handle", ""))
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def _construir_reply(self, client, reply_uri: str):
        """Construye un ReplyRef(root, parent) a partir del post al que se responde."""
        try:
            resp = client.get_post_thread(uri=reply_uri)
            thread = _g(resp, "thread")
            parent_post = _g(thread, "post")
            parent_uri = _g(parent_post, "uri", default=reply_uri)
            parent_cid = _g(parent_post, "cid")
            # La raíz: si el padre es a su vez respuesta, su record.reply.root es la raíz.
            root_uri, root_cid = parent_uri, parent_cid
            rec_reply = _g(_g(parent_post, "record"), "reply")
            if rec_reply:
                root_uri = _g(_g(rec_reply, "root"), "uri", default=parent_uri)
                root_cid = _g(_g(rec_reply, "root"), "cid", default=parent_cid)
            return models.AppBskyFeedPost.ReplyRef(
                root=models.ComAtprotoRepoStrongRef.Main(uri=root_uri, cid=root_cid),
                parent=models.ComAtprotoRepoStrongRef.Main(uri=parent_uri, cid=parent_cid),
            )
        except Exception as e:
            log.error(f"No se pudo construir la referencia de respuesta: {e}")
            return None

    # ------------------------------------------------------------------
    # OUTBOX (bandeja de salida) — auditoría Bluesky 6.6
    # ------------------------------------------------------------------
    def _outbox_actualizar(self, outbox_id: int, estado: str, uri: str = None, error: str = None):
        """Cambia el estado de un apunte de la outbox (pending/sent/unknown/failed)."""
        try:
            db.execute(
                "UPDATE bluesky_outbox SET estado = ?, uri = COALESCE(?, uri), "
                "error = ?, updated_at = ? WHERE id = ?",
                (estado, uri, (error or "")[:500] or None, _ahora_iso(), outbox_id))
        except Exception as e:
            log.debug(f"No se pudo actualizar la outbox {outbox_id}: {e}")

    def _es_error_ambiguo(self, e: Exception) -> bool:
        """True si NO se sabe si la escritura llegó al servidor: cortes de red, timeouts
        y 5xx/408. Un error con código XRPC claro (validación, auth, límite) no es ambiguo."""
        if self._codigo_error(e):
            return False
        status = getattr(getattr(e, "response", None), "status_code", None)
        if status in (408, 500, 502, 503, 504):
            return True
        # Los fallos de transporte de atproto (InvokeTimeoutError, NetworkError) traen el texto
        # vacío: sin el nombre del tipo, un timeout nunca se detectaba como ambiguo.
        bajo = (str(e) + " " + type(e).__name__).lower()
        return any(p in bajo for p in ("timeout", "timed out", "connection", "network",
                                       "reset", "aborted", "temporarily"))

    def _reconciliar_getrecord(self, client, did: str, collection: str, rkey: str):
        """Tras un timeout ambiguo: pregunta al PDS si el registro con ese rkey EXISTE
        (getRecord). Si existe, la publicación llegó y no hay que reenviar (devuelve su
        uri); si el servidor responde que no existe, devuelve None (se puede reintentar
        con el mismo rkey); si ni siquiera se puede consultar, relanza (estado unknown)."""
        try:
            resp = client.com.atproto.repo.get_record(
                models.ComAtprotoRepoGetRecord.Params(repo=did, collection=collection, rkey=rkey))
            return _g(resp, "uri")
        except Exception as e:
            if "could not locate record" in str(e).lower() or \
                    self._codigo_error(e) in ("RecordNotFound", "InvalidRequest"):
                return None
            raise

    def listar_outbox(self, account_id: int) -> list:
        """Apuntes de la bandeja de salida que NO llegaron a confirmarse (failed/unknown/
        pending), del más reciente al más antiguo. Operación local de BD."""
        filas = db.query(
            "SELECT id, rkey, estado, error, intentos, created_at, payload_json "
            "FROM bluesky_outbox WHERE account_id = ? AND estado != 'sent' "
            "ORDER BY id DESC LIMIT 100", (account_id,))
        import json as _json
        res = []
        for f in filas:
            payload = {}
            try:
                payload = _json.loads(f[6]) if f[6] else {}
            except (ValueError, TypeError):
                pass
            res.append({"id": f[0], "rkey": f[1], "estado": f[2], "error": f[3] or "",
                        "intentos": f[4] or 0, "fecha": f[5] or "", "payload": payload})
        return res

    def borrar_outbox(self, outbox_id: int):
        try:
            db.execute("DELETE FROM bluesky_outbox WHERE id = ?", (outbox_id,))
        except Exception as e:
            log.debug(f"No se pudo borrar el apunte de outbox {outbox_id}: {e}")

    def reintentar_outbox_async(self, account_id: int, outbox_id: int,
                                callback: Optional[Callable] = None):
        """Reintenta un apunte fallido de la outbox con su MISMO rkey (el PDS rechaza un
        segundo create con ese rkey, así que no puede duplicarse) y su carga original."""
        fila = db.query_one(
            "SELECT rkey, payload_json FROM bluesky_outbox WHERE id = ? AND account_id = ?",
            (outbox_id, account_id))
        if not fila:
            if callback:
                callback(False, _("El apunte de salida ya no existe."))
            return
        import json as _json
        try:
            payload = _json.loads(fila[1]) if fila[1] else {}
        except (ValueError, TypeError):
            payload = {}
        self.publicar_post_async(
            account_id, payload.get("texto", ""),
            reply_to_uri=payload.get("reply_to_uri"),
            quote_uri=payload.get("quote_uri"),
            media_paths=payload.get("media_paths"),
            alt_texts=payload.get("alt_texts"),
            es_video=bool(payload.get("es_video")),
            langs=payload.get("langs"),
            labels=payload.get("labels"),
            reply_control=payload.get("reply_control"),
            rkey=fila[0], outbox_id=outbox_id, callback=callback)

    def publicar_post_async(self, account_id: int, texto: str,
                           reply_to_uri: Optional[str] = None,
                           quote_uri: Optional[str] = None,
                           media_paths: Optional[List[str]] = None,
                           alt_texts: Optional[List[str]] = None,
                           es_video: bool = False,
                           langs: Optional[List[str]] = None,
                           labels: Optional[List[str]] = None,
                           reply_control: Optional[str] = None,
                           rkey: Optional[str] = None,
                           outbox_id: Optional[int] = None,
                           callback: Optional[Callable] = None):
        """
        Publica un post (texto enriquecido + media + respuesta + cita + idioma + etiquetas).
        Construye el record manualmente para tener control total (create_record).

        reply_control: quién puede responder -> None/"everybody" (todos), "nobody" (nadie),
                       "following" (a quien sigo), "mentioned" (solo mencionados).
                       Se materializa como un record threadgate tras crear el post.
        rkey/outbox_id: solo en REINTENTOS desde la bandeja de salida (mismo rkey = sin duplicados).
        """
        # RKEY idempotente generado ANTES de cualquier trabajo (auditoría 6.6): si el PDS
        # confirma la escritura pero la respuesta se pierde, reintentar con el MISMO rkey
        # no duplica el post. También evita el doble envío por doble clic (unique_key).
        rkey = rkey or self._generar_tid()

        # OUTBOX: persistir la operación ANTES de enviarla (rkey + carga completa). Si la
        # app se cierra o la red falla, el apunte queda y se puede reintentar sin duplicar.
        import json as _json
        try:
            payload_json = _json.dumps({
                "texto": texto, "reply_to_uri": reply_to_uri, "quote_uri": quote_uri,
                "media_paths": list(media_paths or []) or None,
                "alt_texts": list(alt_texts or []) or None, "es_video": bool(es_video),
                "langs": list(langs or []) or None, "labels": list(labels or []) or None,
                "reply_control": reply_control,
            }, ensure_ascii=False)
            if outbox_id:
                db.execute("UPDATE bluesky_outbox SET estado = 'pending', error = NULL, "
                           "intentos = intentos + 1, updated_at = ? WHERE id = ?",
                           (_ahora_iso(), outbox_id))
            else:
                outbox_id = db.execute_insert(
                    "INSERT INTO bluesky_outbox (account_id, rkey, collection, payload_json, "
                    "estado, intentos, created_at) VALUES (?, ?, 'app.bsky.feed.post', ?, 'pending', 1, ?)",
                    (account_id, rkey, payload_json, _ahora_iso()))
        except Exception as e:
            log.debug(f"No se pudo registrar la publicación en la outbox: {e}")
            outbox_id = outbox_id or None

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)

            facets = self._construir_facets(client, texto)

            # Embed de media
            embed = None
            if media_paths:
                if es_video and len(media_paths) == 1:
                    import os as _os
                    ruta_video = media_paths[0]
                    # Validar ANTES de subir (BSKY-002): tipo y tamaño. Bluesky admite
                    # hasta 50 MB de vídeo; subir un archivo inválido gasta cuota y falla.
                    ext = _os.path.splitext(ruta_video)[1].lower()
                    if ext not in (".mp4", ".mov", ".webm", ".mpeg", ".mpg", ".gif"):
                        raise BlueskyServiceError(
                            _("El formato de vídeo {0} no está admitido por Bluesky.").format(ext or "?"))
                    if _os.path.getsize(ruta_video) > 50 * 1024 * 1024:
                        raise BlueskyServiceError(
                            _("El vídeo supera los 50 MB que admite Bluesky."))
                    ctx.report_progress(20, "Subiendo vídeo...")
                    blob = self._subir_blob(client, ruta_video)
                    alt = (alt_texts[0] if alt_texts else "") or ""
                    embed = models.AppBskyEmbedVideo.Main(
                        video=blob, alt=alt,
                        aspect_ratio=self._aspecto_video(ruta_video))
                else:
                    # Máximo 4 imágenes (regla de Bluesky); cada una se optimiza a <=1 MB.
                    imagenes = []
                    for idx, ruta in enumerate(media_paths[:4]):
                        if ctx.is_cancelled():
                            raise Exception("Publicación cancelada.")
                        ctx.report_progress(int((idx / max(len(media_paths), 1)) * 80),
                                            f"Subiendo imagen {idx + 1}...")
                        blob, aspect = self._subir_imagen(client, ruta)
                        alt = (alt_texts[idx] if alt_texts and idx < len(alt_texts) else "") or ""
                        imagenes.append(models.AppBskyEmbedImages.Image(
                            alt=alt, image=blob, aspect_ratio=aspect))
                    embed = models.AppBskyEmbedImages.Main(images=imagenes)

            # Tarjeta de vista previa de un enlace (external embed): solo si NO hay ya media, vídeo
            # ni cita; así el enlace se ve como en el cliente oficial (título + descripción + imagen).
            if embed is None and not quote_uri:
                try:
                    ctx.report_progress(50, "Preparando la vista previa del enlace...")
                except Exception:
                    pass
                embed = self._external_embed(client, texto)

            # Embed de cita (quote). Si además hay media -> recordWithMedia.
            if quote_uri:
                ref = self._strong_ref(client, quote_uri)
                rec_embed = models.AppBskyEmbedRecord.Main(record=ref)
                if embed is not None:
                    embed = models.AppBskyEmbedRecordWithMedia.Main(record=rec_embed, media=embed)
                else:
                    embed = rec_embed

            # Referencia de respuesta
            reply = self._construir_reply(client, reply_to_uri) if reply_to_uri else None

            # Etiquetas de contenido (self-labels)
            labels_obj = None
            vals = [v for v in (labels or []) if v in ETIQUETAS_CONTENIDO]
            if vals:
                labels_obj = models.ComAtprotoLabelDefs.SelfLabels(
                    values=[models.ComAtprotoLabelDefs.SelfLabel(val=v) for v in vals])

            ctx.report_progress(90, "Publicando...")
            record = models.AppBskyFeedPost.Record(
                created_at=_ahora_iso(),
                text=texto,
                facets=facets,
                embed=embed,
                reply=reply,
                langs=langs or None,
                labels=labels_obj,
            )
            try:
                resp = client.com.atproto.repo.create_record(
                    models.ComAtprotoRepoCreateRecord.Data(
                        repo=yo["did"],
                        collection="app.bsky.feed.post",
                        rkey=rkey,
                        record=record,
                    ))
                uri = _g(resp, "uri")
            except Exception as e:
                # Escrituras: NUNCA reintentar a ciegas (auditoría 6.6/9.5). Si el fallo es
                # AMBIGUO (timeout/corte: no se sabe si llegó), se pregunta al PDS con
                # getRecord por el rkey: si el registro existe, la publicación SÍ llegó y
                # se continúa como éxito; si no existe, queda 'failed' y el reintento desde
                # la bandeja de salida usará el MISMO rkey (imposible duplicar).
                if self._es_error_ambiguo(e):
                    try:
                        uri = self._reconciliar_getrecord(
                            client, yo["did"], "app.bsky.feed.post", rkey)
                    except Exception:
                        if outbox_id:
                            self._outbox_actualizar(outbox_id, "unknown", error=str(e))
                        raise BlueskyServiceError(
                            "Se cortó la conexión y no se pudo confirmar si la publicación "
                            "llegó al servidor. Quedó en la bandeja de salida (menú Bluesky → "
                            "Salida pendiente) para comprobarla o reintentarla sin duplicados.") from e
                    if not uri:
                        if outbox_id:
                            self._outbox_actualizar(outbox_id, "failed", error=self._mensaje_error(e))
                        raise
                    log.info("Publicación reconciliada con getRecord: llegó pese al corte.")
                else:
                    if self._es_error_limite(e):
                        self.registrar_limite_429(account_id, e)
                    if outbox_id:
                        self._outbox_actualizar(outbox_id, "failed", error=self._mensaje_error(e))
                    raise
            if outbox_id:
                self._outbox_actualizar(outbox_id, "sent", uri=uri)

            # Threadgate: limitar quién puede responder (solo en posts raíz).
            if reply_control and reply_control != "everybody" and not reply_to_uri:
                try:
                    if reply_control == "nobody":
                        allow = []   # lista vacía = nadie puede responder
                    elif reply_control == "following":
                        allow = [models.AppBskyFeedThreadgate.FollowingRule()]
                    elif reply_control == "mentioned":
                        allow = [models.AppBskyFeedThreadgate.MentionRule()]
                    else:
                        allow = None
                    if allow is not None:
                        # OJO: variable PROPIA (rkey_tg), no 'rkey': reasignar 'rkey' aquí
                        # convertía la variable de cierre en local de _bg y el create_record
                        # del post fallaba con UnboundLocalError (fallo reportado).
                        rkey_tg = uri.rsplit("/", 1)[-1]
                        tg = models.AppBskyFeedThreadgate.Record(
                            post=uri, allow=allow, created_at=_ahora_iso())
                        client.com.atproto.repo.create_record(
                            models.ComAtprotoRepoCreateRecord.Data(
                                repo=yo["did"], collection="app.bsky.feed.threadgate",
                                rkey=rkey_tg, record=tg))
                except Exception as e:
                    log.debug(f"No se pudo aplicar el threadgate: {e}")

            # Guardar en caché local para que aparezca en "Enviados".
            try:
                pr = client.get_posts([uri])
                posts = _g(pr, "posts", default=[])
                if posts:
                    po = self._procesar_y_guardar_post(account_id, posts[0])
                    if po:
                        db.execute(
                            "INSERT OR IGNORE INTO bluesky_timeline_posts (account_id, timeline, post_db_id) VALUES (?, 'sent', ?)",
                            (account_id, po.id))
            except Exception as e:
                log.debug(f"No se pudo cachear el post recién publicado: {e}")
            return uri

        def _on_finish(result, error):
            from app.core.task_manager import TareaDuplicadaError
            if isinstance(error, TareaDuplicadaError):
                # Doble pulsación de "Publicar": el mismo post (mismo rkey) ya se está
                # enviando. NO marcar la outbox como fallida: el envío original sigue vivo.
                if callback:
                    callback(False, _("Esa publicación ya se está enviando."))
                return
            if error:
                # Fallos ANTES de llegar al servidor (validación, subida de un adjunto...):
                # dejar el apunte de la outbox como 'failed' si seguía 'pending'.
                if outbox_id:
                    try:
                        db.execute(
                            "UPDATE bluesky_outbox SET estado = 'failed', error = ?, "
                            "updated_at = ? WHERE id = ? AND estado = 'pending'",
                            (str(error)[:500], _ahora_iso(), outbox_id))
                    except Exception:
                        pass
                if callback:
                    msg = str(error) if isinstance(error, BlueskyServiceError) \
                        else self._mensaje_error(error)
                    callback(False, msg)
            else:
                if callback:
                    callback(True, str(result))

        task_manager.start_task(_bg, callback=_on_finish, success_sound="ready",
                               error_sound="error", pass_context=True,
                               unique_key=f"bsky:publicar:{rkey}")

    def borrar_post_async(self, account_id: int, uri: str, callback: Optional[Callable] = None):
        """Borra un post propio (registro remoto + caché local COMPLETA: fila, índice FTS
        y asociaciones de timeline; antes la fila FTS quedaba huérfana y la búsqueda
        seguía encontrando el post borrado — auditoría Bluesky 13.2)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            client.delete_post(uri)
            with db.transaccion():
                filas = db.query("SELECT id FROM bluesky_posts WHERE account_id = ? AND uri = ?",
                                 (account_id, uri))
                for (dbid,) in filas:
                    db.execute("DELETE FROM bluesky_posts_fts WHERE post_db_id = ?", (dbid,))
                    db.execute("DELETE FROM bluesky_timeline_posts WHERE post_db_id = ?", (dbid,))
                    db.execute("DELETE FROM bluesky_posts WHERE id = ?", (dbid,))
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def fijar_post_async(self, account_id: int, post_uri: str, post_cid: str,
                         fijar: bool = True, callback: Optional[Callable] = None):
        """Fija (o quita) una publicación PROPIA en tu perfil (campo pinnedPost del
        registro de perfil), como hacen TWBlue y FastSM.

        NO destructivo (BSKY-001): usa la mutación compartida de perfil, que conserva
        TODOS los campos del registro (conocidos y desconocidos) y escribe con swap
        para no pisar ediciones de otros clientes."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = self._yo(account_id).get("did")
            if not did:
                raise BlueskyServiceError(_("No se pudo identificar tu cuenta."))

            def _mutar(registro):
                if fijar:
                    registro["pinnedPost"] = {"uri": post_uri, "cid": post_cid}
                else:
                    registro.pop("pinnedPost", None)

            return self._mutar_registro_perfil(client, did, _mutar)
        task_manager.start_task(_bg, callback=callback, success_sound="ready",
                                error_sound="error", pass_context=True)

    def obtener_sugerencias_async(self, account_id: int, limit: int = 50,
                                  callback: Optional[Callable] = None):
        """Devuelve cuentas sugeridas para seguir (descubrimiento), como FastSM/TWBlue."""
        def _bg(ctx):
            from atproto import models
            client = self.obtener_cliente(account_id)
            params = models.AppBskyActorGetSuggestions.Params(limit=min(limit, 100))
            resp = client.app.bsky.actor.get_suggestions(params)
            return [self._actor_a_dict(a) for a in (getattr(resp, "actors", []) or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # INTERACCIONES (like / repost)
    # ------------------------------------------------------------------
    def ejecutar_accion_post_async(self, account_id: int, post_db_id: str, accion: str,
                                  callback: Optional[Callable] = None):
        """Ejecuta like/unlike/repost/unrepost sobre un post cacheado."""
        fila = db.query_one(
            "SELECT uri, cid, liked_uri, reposted_uri FROM bluesky_posts WHERE id = ?", (post_db_id,))
        if not fila:
            if callback:
                callback(None, BlueskyServiceError("Post no encontrado en la base de datos local."))
            return
        uri, cid, liked_uri, reposted_uri = fila

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if accion == "like":
                r = client.like(uri, cid)
                nuevo = _g(r, "uri", default="")
                db.execute("UPDATE bluesky_posts SET liked_uri = ?, like_count = like_count + 1 WHERE id = ?",
                           (nuevo, post_db_id))
            elif accion == "unlike":
                if liked_uri:
                    client.unlike(liked_uri)
                db.execute("UPDATE bluesky_posts SET liked_uri = '', like_count = MAX(like_count - 1, 0) WHERE id = ?",
                           (post_db_id,))
            elif accion == "repost":
                r = client.repost(uri, cid)
                nuevo = _g(r, "uri", default="")
                db.execute("UPDATE bluesky_posts SET reposted_uri = ?, repost_count = repost_count + 1 WHERE id = ?",
                           (nuevo, post_db_id))
            elif accion == "unrepost":
                if reposted_uri:
                    client.unrepost(reposted_uri)
                db.execute("UPDATE bluesky_posts SET reposted_uri = '', repost_count = MAX(repost_count - 1, 0) WHERE id = ?",
                           (post_db_id,))
            else:
                raise BlueskyServiceError(f"Acción desconocida: {accion}")
            return True
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # GRAFO SOCIAL (follow / block / mute)
    # ------------------------------------------------------------------
    # Bucket de la caché de actores que refleja cada estado (Bloqueados, Silenciados, Siguiendo).
    _BUCKET_DE_ESTADO = (("blocking_uri", "blocks"), ("muted", "mutes"), ("following_uri", "follows"))

    def _sincronizar_estado_actor(self, client, account_id: int, did: str,
                                  conocido: Optional[dict] = None) -> dict:
        """Tras seguir/bloquear/silenciar (o deshacerlo), pide al servidor el estado REAL de tu
        relación con la cuenta (viewer de app.bsky.actor.getProfile) y lo guarda en la caché de
        actores: en TODAS sus filas y en el bucket que lo refleja (la cuenta aparece o desaparece
        de Bloqueados/Silenciados/Siguiendo). Antes solo se tocaba el servidor y el menú seguía
        ofreciendo "Bloquear" porque la caché no se enteraba. 'conocido' (p. ej. el uri del
        bloqueo recién creado) se usa si el perfil no se puede consultar."""
        try:
            d = self._actor_a_dict(client.get_profile(did))
        except Exception as e:
            log.debug(f"No se pudo leer el perfil tras la acción de cuenta ({e}); se usa lo conocido.")
            fila = db.query_one(
                "SELECT handle, display_name, description, following_uri, blocking_uri, muted "
                "FROM bluesky_actors WHERE account_id = ? AND did = ? LIMIT 1", (account_id, did))
            d = {"did": did, "handle": fila[0] if fila else "", "display_name": fila[1] if fila else "",
                 "description": fila[2] if fila else "", "following_uri": fila[3] if fila else "",
                 "blocking_uri": fila[4] if fila else "", "muted": bool(fila[5]) if fila else False}
            d.update(conocido or {})
        d["did"] = d.get("did") or did
        with db.transaccion():
            db.execute("UPDATE bluesky_actors SET following_uri = ?, blocking_uri = ?, muted = ? "
                       "WHERE account_id = ? AND did = ?",
                       (d.get("following_uri") or "", d.get("blocking_uri") or "",
                        1 if d.get("muted") else 0, account_id, did))
            for campo, bucket in self._BUCKET_DE_ESTADO:
                db_id = f"{account_id}:{bucket}:{did}"
                if d.get(campo):
                    if not db.query_one("SELECT 1 FROM bluesky_actors WHERE id = ?", (db_id,)):
                        fila = db.query_one("SELECT COALESCE(MIN(position), 0) - 1 FROM bluesky_actors "
                                            "WHERE account_id = ? AND bucket = ?", (account_id, bucket))
                        db.execute(
                            "INSERT OR REPLACE INTO bluesky_actors (id, account_id, bucket, did, handle, "
                            "display_name, description, following_uri, blocking_uri, muted, position) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (db_id, account_id, bucket, did, d.get("handle") or "",
                             d.get("display_name") or "", d.get("description") or "",
                             d.get("following_uri") or "", d.get("blocking_uri") or "",
                             1 if d.get("muted") else 0, fila[0] if fila else 0))
                else:
                    db.execute("DELETE FROM bluesky_actors WHERE id = ?", (db_id,))
        return d

    def accion_cuenta_async(self, account_id: int, did: str, accion: str,
                           following_uri: str = "", blocking_uri: str = "",
                           callback: Optional[Callable] = None):
        """seguir/dejar_seguir, bloquear/desbloquear, silenciar/desilenciar sobre un DID.
        Devuelve {"accion", "did", "handle", "display_name", "following_uri", "blocking_uri",
        "muted"} con el estado YA confirmado por el servidor y guardado en la caché."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            yo = self._yo(account_id)
            conocido = {}

            def _uri_viewer(campo):
                # Sin el uri cacheado, se pregunta al perfil (antes se ignoraba en silencio y
                # "Desbloquear" no hacía nada si la cuenta no estaba en la caché).
                try:
                    return _g(_g(client.get_profile(did), "viewer"), campo, default="") or ""
                except Exception:
                    return ""

            if accion == "follow":
                res = client.follow(did)
                conocido["following_uri"] = _g(res, "uri", default="") or ""
            elif accion == "unfollow":
                uri = following_uri or _uri_viewer("following")
                if not uri:
                    raise BlueskyServiceError("No sigues a esa cuenta.")
                client.unfollow(uri)
                conocido["following_uri"] = ""
            elif accion == "mute":
                client.mute(did)
                conocido["muted"] = True
            elif accion in ("mute_reposts", "mute_citas"):
                # Silencio PARCIAL (lexicon de 2026-08, atproto 0.0.72): solo sus reposts o solo
                # sus citas; lo demás de la cuenta se sigue viendo. Repetir la llamada sustituye
                # el alcance anterior. "Quitar silencio" lo deshace igual que un silencio total.
                from atproto import models as _m
                datos = _m.AppBskyGraphMuteActor.Data(
                    actor=did, only_reposts=(accion == "mute_reposts") or None,
                    only_quoteposts=(accion == "mute_citas") or None)
                client.app.bsky.graph.mute_actor(datos)
                conocido["muted"] = True
            elif accion == "unmute":
                client.unmute(did)
                conocido["muted"] = False
            elif accion == "block":
                record = models.AppBskyGraphBlock.Record(subject=did, created_at=_ahora_iso())
                res = client.com.atproto.repo.create_record(models.ComAtprotoRepoCreateRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.block", record=record))
                conocido["blocking_uri"] = _g(res, "uri", default="") or ""
            elif accion == "unblock":
                uri = blocking_uri or _uri_viewer("blocking")
                if not uri:
                    raise BlueskyServiceError("Esa cuenta no está bloqueada.")
                # blocking_uri = at://did/app.bsky.graph.block/<rkey>
                rkey = uri.rsplit("/", 1)[-1]
                client.com.atproto.repo.delete_record(models.ComAtprotoRepoDeleteRecord.Data(
                    repo=yo["did"], collection="app.bsky.graph.block", rkey=rkey))
                conocido["blocking_uri"] = ""
            else:
                raise BlueskyServiceError(f"Acción de cuenta desconocida: {accion}")
            estado = self._sincronizar_estado_actor(client, account_id, did, conocido)
            estado["accion"] = accion
            return estado
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def seguir_autor_async(self, account_id: int, actor: str, callback: Optional[Callable] = None):
        """Alterna seguir/dejar de seguir resolviendo primero la relación actual.

        Robusto ante cuentas ROTAS (p. ej. did:web cuyo perfil ya no resuelve y que aún aparecen
        en 'Siguiendo'): si get_profile falla pero tenemos cacheado el following_uri (el registro
        de tu follow), se puede DEJAR DE SEGUIR igualmente sin depender del perfil."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            did = actor
            following_uri = ""
            try:
                p = client.get_profile(actor)
                did = _g(p, "did") or actor
                following_uri = _g(_g(p, "viewer"), "following", default="") or ""
            except Exception as e:
                # Perfil no resoluble: recuperar el following_uri cacheado para poder dejar de seguir.
                row = db.query_one(
                    "SELECT following_uri FROM bluesky_actors WHERE account_id = ? AND did = ? "
                    "AND following_uri IS NOT NULL AND following_uri != '' LIMIT 1",
                    (account_id, actor))
                following_uri = (row[0] if row else "") or ""
                if not following_uri:
                    # No se puede ni resolver el perfil ni dejar de seguir: propagar con mensaje claro.
                    raise BlueskyServiceError(
                        "Esta cuenta ya no existe o su perfil no está disponible, y no hay datos "
                        "locales para dejar de seguirla. Prueba a actualizar el buffer (Ctrl+Shift+R).") from e
            if following_uri:
                client.unfollow(following_uri)
                conocido, res = {"following_uri": ""}, {"seguido": False, "did": did}
            else:
                r = client.follow(did)
                conocido = {"following_uri": _g(r, "uri", default="") or ""}
                res = {"seguido": True, "did": did}
            # Que el menú ofrezca ya la acción contraria (la caché no se enteraba).
            try:
                self._sincronizar_estado_actor(client, account_id, did, conocido)
            except Exception as e:
                log.debug(f"No se pudo actualizar la caché tras seguir/dejar de seguir: {e}")
            return res
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ------------------------------------------------------------------
    # TANDA 2 — DESCUBRIMIENTO Y GRAFO SOCIAL
    # ------------------------------------------------------------------
