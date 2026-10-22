# -*- coding: utf-8 -*-
"""MainFrame (mixin): BANDEJA DE ENTRADA unificada.

Un buffer especial que reúne publicaciones de VARIOS buffers de VARIAS redes que el usuario elige
(p. ej. Inicio de Mastodon + Inicio de Bluesky + Directos de Mastodon). Es de SOLO LECTURA agregada:
mezcla las filas por fecha y las pinta con su propia plantilla (`inbox_template`), que incluye una
columna {origen} para identificar de qué red/cuenta/buffer viene cada publicación.

Clave: al actuar sobre una fila, se usa el MENÚ CONTEXTUAL ESPECÍFICO de la red de ESA fila. Para
ello la bandeja guarda el origen de cada fila (red, cuenta, buffer) y, al abrir el menú, establece
temporalmente el contexto de esa red y delega en el menú contextual normal (`_on_context_menu`), que
así construye el menú correcto y sus acciones funcionan igual que en el buffer original.

Se activa/desactiva y se configura por completo en Preferencias y desde el árbol (agregar/quitar
fuentes). Redes soportadas: Mastodon, Bluesky, RSS y X (Telegram usa un modelo de chats distinto y
no entra de momento).
"""
import wx
import logging
from datetime import datetime, timedelta

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar

log = logging.getLogger(__name__)


# Buffers (de PUBLICACIONES) que se pueden añadir a la bandeja, por red. Clave interna -> etiqueta.
# Se excluyen los de notificaciones/menciones (usan menús con estructuras paralelas) y Telegram.
INBOX_BUFFERS = {
    "mastodon": [
        ("home", lambda: _("Inicio")), ("local", lambda: _("Local")), ("public", lambda: _("Federada")),
        ("direct", lambda: _("Mensajes directos")), ("bookmarks", lambda: _("Marcadores")),
        ("favourites", lambda: _("Favoritos")), ("sent", lambda: _("Enviados")),
    ],
    "bluesky": [
        ("home", lambda: _("Inicio")), ("sent", lambda: _("Enviados")), ("likes", lambda: _("Me gusta")),
    ],
    "rss": [
        ("all", lambda: _("Todos")), ("unread", lambda: _("No leídos")), ("today", lambda: _("Hoy")),
        ("favorites", lambda: _("Destacados")), ("read_later", lambda: _("Leer más tarde")),
    ],
    "x": [
        ("home", lambda: _("Inicio")), ("mentions_tl", lambda: _("Menciones")),
    ],
}
# Modo de lista y servicio que hay que fijar al delegar el menú contextual de cada red.
_MODO_POR_RED = {"mastodon": "toots", "x": "x_posts", "bluesky": "bluesky_inbox", "rss": "rss"}


def _etiqueta_buffer(red, clave):
    for c, f in INBOX_BUFFERS.get(red, []):
        if c == clave:
            return f()
    return clave


def _parse_fecha(s):
    """Convierte una fecha ISO en un datetime COMPARABLE entre redes, para ordenar la bandeja por
    tiempo real. CLAVE: respeta la zona horaria y la normaliza a hora local (igual que
    formatear_tiempo_relativo), porque cada red guarda la fecha distinto —Mastodon/Bluesky en UTC
    (con 'Z'), RSS a menudo en hora local—. Si no se respetara la zona, todas las de UTC quedarían
    sistemáticamente "más antiguas" y la bandeja saldría AGRUPADA POR RED en vez de mezclada por
    tiempo. Tolerante: si no se puede interpretar, devuelve la fecha mínima (va al final)."""
    if not s:
        return datetime.min
    ds = str(s).strip()
    if ds.endswith("Z"):
        ds = ds[:-1] + "+00:00"
    dt = None
    try:
        dt = datetime.fromisoformat(ds)
    except ValueError:
        limpio = ds.replace("T", " ")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(limpio[:26] if len(limpio) > 26 else limpio, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        # Normalizar a hora local SIN zona, para comparar de forma homogénea con las que vienen
        # ya en local (mismo criterio que el "hace X minutos" que se muestra).
        try:
            dt = dt.astimezone().replace(tzinfo=None)
        except (ValueError, OSError):
            dt = dt.replace(tzinfo=None)
    return dt


class _Mf_bandeja:
    # ---------------- Estado / configuración ----------------
    def _inbox_habilitada(self) -> bool:
        return bool(config.get("inbox", "enabled", False))

    def _inbox_fuentes(self) -> list:
        """Lista de fuentes elegidas: cada una {red, account_id, buffer}."""
        fuentes = config.get("inbox", "fuentes", []) or []
        return [f for f in fuentes if isinstance(f, dict) and f.get("red") and f.get("buffer")]

    def _inbox_guardar_fuentes(self, fuentes: list):
        config.set("inbox", "fuentes", fuentes)  # set() ya guarda (autosave)

    def _inbox_nombre_cuenta(self, account_id) -> str:
        try:
            row = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
            return row[0] if row else str(account_id)
        except Exception:
            return str(account_id)

    def _inbox_origen_label(self, red, account_id, buffer) -> str:
        from app.core.plantillas import nombre_red
        return "{0} · {1} · {2}".format(nombre_red(red), self._inbox_nombre_cuenta(account_id),
                                        _etiqueta_buffer(red, buffer))

    def _inbox_buffers_disponibles(self) -> list:
        """Todos los buffers que se pueden añadir, de las cuentas ACTIVAS de cada red soportada.
        Devuelve lista de dicts {red, account_id, buffer, label}."""
        from app.core import feature_flags
        disponibles = []
        for red in ("mastodon", "bluesky", "rss", "x"):
            if not feature_flags.servicio_habilitado(red):
                continue
            try:
                cuentas = db.query("SELECT id, name FROM accounts WHERE service_id = ? AND active = 1 ORDER BY position, id", (red,))
            except Exception:
                cuentas = []
            for acc_id, _name in cuentas:
                for clave, _f in INBOX_BUFFERS.get(red, []):
                    disponibles.append({
                        "red": red, "account_id": acc_id, "buffer": clave,
                        "label": self._inbox_origen_label(red, acc_id, clave),
                    })
        return disponibles

    def _construir_servicio_inbox(self, root):
        """Construye el nodo raíz de la BANDEJA en el árbol. Es un 'category_root' con
        service='inbox' para que el sistema de servicios lo trate como uno más: reordenable con
        Ctrl+flechas y activable con Alt+1..9 según su posición. Al seleccionarlo NO se trata como
        contenedor vacío (se excluye en _on_nav_selection), sino que se carga la lista mezclada."""
        self.tree_nav.AppendItem(root, _("Bandeja de entrada"),
                                 data={"type": "category_root", "service": "inbox"})

    # ---------------- Construcción de filas (normalización por red) ----------------
    def _inbox_filas_de_fuente(self, fuente, limite=200):
        """Devuelve filas normalizadas de UNA fuente: lista de dicts
        {red, account_id, buffer, db_id, author, content, date_iso, dt}."""
        red = fuente.get("red")
        acc = fuente.get("account_id")
        buf = fuente.get("buffer")
        filas = []
        try:
            if red == "mastodon":
                # Se traen TAMBIÉN los adjuntos, la encuesta y la advertencia de contenido: sin
                # ellos, un toot de SOLO imagen/vídeo/audio (o una encuesta sin texto) se quedaba
                # con la columna Contenido vacía y la fila sonaba a "publicación fantasma" (solo
                # autor y fecha). La bandeja no tiene columna {media} donde caiga esa información.
                rows = db.query(
                    "SELECT s.id, s.author_name, s.content, s.created_at, "
                    "COALESCE(s.quote_json,''), COALESCE(s.card_json,''), "
                    "COALESCE(s.media_json,''), COALESCE(s.has_poll,0), COALESCE(s.spoiler_text,''), "
                    "s.filtered_by "
                    "FROM mastodon_statuses s JOIN mastodon_timeline_statuses t ON s.id = t.status_db_id "
                    "WHERE t.account_id = ? AND t.timeline = ? ORDER BY s.created_at DESC LIMIT ?",
                    (acc, buf, limite))
            elif red == "bluesky":
                rows = db.query(
                    "SELECT p.id, p.author_name, p.content, COALESCE(p.indexed_at, p.created_at), "
                    "COALESCE(p.media_json,''), COALESCE(p.quote_uri,''), "
                    # Como en el buffer de Bluesky: a quién responde (guardado o por la caché) y
                    # el motivo del repost DE ESTE BUFFER, para leer igual en los dos sitios.
                    "COALESCE(p.author_handle,''), "
                    "COALESCE(NULLIF(p.reply_parent_handle,''), (SELECT pp.author_handle "
                    "FROM bluesky_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), ''), "
                    "COALESCE(NULLIF(p.reply_parent_name,''), (SELECT pp.author_name "
                    "FROM bluesky_posts pp WHERE pp.id = p.account_id || ':' || p.reply_parent_uri), ''), "
                    "COALESCE(t.reason_json,''), "
                    # Para la moderación (etiquetas, palabras silenciadas, ocultadas, feed).
                    "COALESCE(p.labels,''), COALESCE(p.mod_json,''), COALESCE(p.uri,''), "
                    "COALESCE(p.author_did,''), COALESCE(p.like_count,0), "
                    "COALESCE(p.reply_parent_uri,'') "
                    "FROM bluesky_posts p JOIN bluesky_timeline_posts t ON p.id = t.post_db_id "
                    "WHERE t.account_id = ? AND t.timeline = ? "
                    "ORDER BY COALESCE(p.indexed_at, p.created_at) DESC LIMIT ?",
                    (acc, buf, limite))
            elif red == "x":
                # "mentions_tl" es la clave histórica de la bandeja (puede estar guardada en la
                # configuración), pero el buffer de menciones de X se llama "mentions": con la
                # clave vieja la bandeja nunca encontraba ninguna mención.
                tl_x = "mentions" if buf == "mentions_tl" else buf
                rows = db.query(
                    "SELECT p.id, p.author_name, p.text, p.created_at, "
                    "COALESCE(p.retweeted_by,''), COALESCE(p.quoted_id,''), "
                    "COALESCE(p.quoted_author,''), COALESCE(p.quoted_text,''), "
                    "COALESCE(p.poll_json,''), COALESCE(p.media_json,''), "
                    "COALESCE(p.in_reply_to_screen_name,'') "
                    "FROM x_tweets p JOIN x_timeline_tweets t ON p.id = t.tweet_db_id "
                    "WHERE t.account_id = ? AND t.timeline = ? ORDER BY p.created_at DESC LIMIT ?",
                    (acc, tl_x, limite))
            elif red == "rss":
                # Filtrar por a.account_id (denormalizado) para que el orden por fecha salga del
                # índice idx_rssart_acc_pub, sin tabla temporal (ver migración rss_articles.account_id).
                sql = ("SELECT a.id, f.title, a.title, a.published_at "
                       "FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id "
                       "WHERE a.account_id = ? AND COALESCE(a.deleted, 0) = 0")
                if buf == "unread":
                    sql += " AND a.read = 0"
                elif buf == "favorites":
                    sql += " AND a.favorite = 1"
                elif buf == "read_later":
                    sql += " AND COALESCE(a.read_later, 0) = 1"
                elif buf == "today":
                    sql += " AND date(a.published_at) = date('now', 'localtime')"
                sql += " ORDER BY a.published_at DESC LIMIT ?"
                rows = db.query(sql, (acc, limite))
            else:
                rows = []
        except Exception as e:
            log.debug(f"Bandeja: no se pudo leer la fuente {red}/{acc}/{buf}: {e}")
            return filas

        for r in rows:
            db_id, autor, contenido, fecha = r[0], r[1], r[2], r[3]
            content_txt = (contenido or "").replace("\n", " ").strip()
            # Enriquecer el contenido como en el buffer normal de cada red, para que la bandeja muestre
            # TODO: Mastodon (cita + tarjeta del enlace), Bluesky (descripción de media + cita) y
            # X (prefijo RT + cita). RSS ya muestra el título completo, igual que su buffer.
            if red == "mastodon" and len(r) >= 9:
                content_txt = self._inbox_contenido_mastodon(r, acc, buf)
                if content_txt is None:      # filtro del servidor con acción "ocultar"
                    continue
            elif red == "bluesky" and len(r) >= 6:
                media_json = r[4]
                if len(r) >= 16:
                    # La misma moderación que en el buffer de Bluesky: etiquetas, palabras
                    # silenciadas, publicaciones ocultadas y preferencias del feed Siguiendo.
                    m = self._inbox_moderar_bluesky(r, acc, buf, content_txt)
                    if m is None or m["ocultar"]:
                        continue
                    content_txt = m["contenido"]
                    if m["ocultar_media"]:
                        media_json = ""
                content_txt = self._enriquecer_content_bluesky(content_txt, media_json, r[5])
                if len(r) >= 16 and m["aviso_media"]:
                    content_txt += ": " + m["aviso_media"]
                if len(r) >= 10:
                    autor, content_txt = self._repost_y_respuesta_bluesky(
                        acc, autor, r[6], content_txt, r[7], r[8], r[9])
            elif red == "x" and len(r) >= 6:
                content_txt = self._enriquecer_content_x(content_txt, r[4], r[5], *r[6:11])
            filas.append({
                "red": red, "account_id": acc, "buffer": buf, "db_id": db_id,
                "author": autor or "", "content": content_txt,
                "date_iso": fecha or "", "dt": _parse_fecha(fecha),
            })
        return filas

    def _inbox_moderar_bluesky(self, r, acc, buf, content_txt):
        """Moderación de una fila de Bluesky de la bandeja (r = fila de _inbox_filas_de_fuente).
        Devuelve el dict de moderar_fila_bandeja, o None si no se puede evaluar y conviene no
        mostrarla (nunca: ante un fallo se muestra tal cual, como antes)."""
        try:
            from app.services.bluesky import bluesky_service
            from app.services.bluesky.bluesky_moderacion import moderar_fila_bandeja
            return moderar_fila_bandeja(
                bluesky_service.prefs_moderacion(acc), r[10], r[11], uri=r[12],
                author_did=r[13], contenido=content_txt, buffer=buf, like_count=r[14],
                reply_parent_uri=r[15], quote_uri=r[5], reason_json=r[9])
        except Exception as e:
            log.debug(f"Bandeja: no se pudo moderar la fila de Bluesky {r[0]}: {e}")
            return {"ocultar": False, "contenido": content_txt, "ocultar_media": False,
                    "aviso_media": ""}

    def _inbox_contenido_mastodon(self, r, acc, buf):
        """Contenido de una fila de Mastodon de la bandeja (r = fila de la consulta de
        _inbox_filas_de_fuente) con los filtros del servidor del buffer de ORIGEN, como la web
        oficial: None si se oculta; con 'avisar', "[Filtrado: título]" hasta "Mostrar de todos
        modos"; con 'difuminar', sin leer el texto alternativo de los adjuntos."""
        from app.services.mastodon import filtros_servidor as _fsrv
        # Lista vigente de filtros (la acción 'difuminar' llega por streaming sin acción).
        _fsrv.asegurar_vigentes(acc, al_cargar=self._repintar_bandeja_tras_filtros)
        db_id = r[0]
        cuerpo = (r[2] or "").replace("\n", " ").strip()
        ev = _fsrv.evaluar(r[9] if len(r) >= 10 else None, acc, buf)
        if ev["accion"] == "hide":
            return None
        tiene_media = bool(r[6] and r[6] != "[]")
        if _fsrv.oculta_texto(ev, db_id):
            return _fsrv.texto_filtrado(ev)
        txt = self._enriquecer_content_mastodon(cuerpo, r[4], r[5], r[6], r[7], r[8],
                                                ocultar_alt=_fsrv.media_filtrada(ev, db_id),
                                                account_id=acc, timeline=buf, clave=db_id)
        avisos = _fsrv.segmento_aviso(ev, tiene_media)
        return ": ".join(avisos + ([txt] if txt else [])) if avisos else txt

    def _inbox_contenido_mastodon_db(self, db_id, acc, buf):
        """_inbox_contenido_mastodon() de un solo toot por su db_id ("Mostrar de todos modos")."""
        r = db.query_one(
            "SELECT s.id, s.author_name, s.content, s.created_at, COALESCE(s.quote_json,''), "
            "COALESCE(s.card_json,''), COALESCE(s.media_json,''), COALESCE(s.has_poll,0), "
            "COALESCE(s.spoiler_text,''), s.filtered_by FROM mastodon_statuses s WHERE s.id = ?",
            (db_id,))
        if not r:
            return None
        return (self._inbox_contenido_mastodon(r, acc, buf) or "").strip() or _("(sin texto)")

    def _repintar_bandeja_tras_filtros(self):
        """Al llegar la lista vigente de filtros, repinta la bandeja si es el buffer activo (el
        repintado conserva el foco en tu elemento, por su db_id)."""
        def _ui():
            try:
                if getattr(self, "_bandeja_activa", False) and getattr(self, "_modo_lista", "") == "bandeja":
                    cols = getattr(self, "_cols_actuales", None)
                    if cols:
                        self._pintar_filas_bandeja(self._inbox_filas_completas(cols), cols)
            except Exception as e:
                log.debug(f"Bandeja: no se pudo repintar tras cargar los filtros: {e}")
        wx.CallAfter(_ui)

    def _enriquecer_content_mastodon(self, cuerpo, quote_json, card_json,
                                     media_json="", has_poll=0, spoiler_text="",
                                     ocultar_alt=False, account_id=None, timeline="", clave=None):
        """Construye el contenido de un toot para la bandeja igual que el buffer normal: advertencia
        de contenido + cuerpo + cita (si la hay) + tarjeta del enlace (card: título y descripción) +
        descripción de los adjuntos y de la encuesta.

        Los adjuntos y la encuesta se describen AQUÍ (como ya se hacía en Bluesky) porque la
        plantilla de la bandeja no tiene columnas {media} ni {indicators}: sin esto, un toot de solo
        imagen se quedaba sin contenido y la fila solo decía el autor y la fecha."""
        import json as _json
        from app.ui.ui_helpers import formato_cita
        segmentos = []
        if spoiler_text and str(spoiler_text).strip():
            segmentos.append(_("Advertencia de contenido: {cw}").format(cw=str(spoiler_text).strip()))
        if cuerpo:
            segmentos.append(cuerpo)
        try:
            cita = formato_cita(quote_json, account_id=account_id, timeline=timeline, clave=clave)
            if cita:
                segmentos.append(cita)
        except Exception:
            pass
        if card_json:
            try:
                c = _json.loads(card_json)
                tit = (c.get("title") or "").strip()
                desc = (c.get("description") or "").strip()
                if tit:
                    segmentos.append(_("Enlace: {0}").format(tit + (". " + desc if desc else "")))
            except Exception:
                pass
        if media_json:
            try:
                for a in (_json.loads(media_json) or []):
                    tipo = (a.get("type") or "").lower()
                    alt = (a.get("description") or "").strip()
                    if "video" in tipo:
                        etq = _("Vídeo")
                    elif "gif" in tipo:
                        etq = _("GIF")
                    elif "audio" in tipo:
                        etq = _("Audio")
                    else:
                        etq = _("Imagen")
                    if ocultar_alt:
                        # Multimedia difuminada por un filtro: no se lee su texto alternativo.
                        etq += " " + _("(oculta por un filtro)")
                    else:
                        etq += (": " + alt) if alt else (" " + _("(sin descripción)"))
                    segmentos.append(etq)
            except Exception:
                pass
        if has_poll:
            segmentos.append(_("Encuesta"))
        return ": ".join(s for s in segmentos if s)

    def _repost_y_respuesta_bluesky(self, account_id, autor, handle, cuerpo, reply_handle,
                                    reply_name, reason_json):
        """Aplica a una fila de Bluesky de la bandeja lo mismo que al buffer: el formato de repost
        y "En respuesta a @x" según Preferencias → Bluesky. La plantilla de la bandeja no tiene
        campo {reply_to}, así que con la casilla marcada va delante del texto. Devuelve
        (autor, contenido)."""
        from app.core.at_lectura import (aplicar_repost_y_respuesta, repost_de_reason_json,
                                         mi_handle)
        from app.services.bluesky import bluesky_service
        repost_handle, repost_name = repost_de_reason_json(reason_json)
        v = aplicar_repost_y_respuesta(
            {"author": autor or "", "username": f"@{handle}" if handle else "", "content": cuerpo},
            red="bluesky", autor=autor, handle=handle, repost_handle=repost_handle,
            repost_name=repost_name, reply_handle=reply_handle, reply_name=reply_name,
            formato_repost=config.get("bluesky", "formato_repost", "original"),
            prefijo_respuesta=bool(config.get("bluesky", "prefijo_respuesta", False)),
            plantilla="", con_usuario=False,
            mi_handle=mi_handle(bluesky_service, account_id) if reply_handle else "")
        return v["author"], v["content"]

    def _enriquecer_content_bluesky(self, cuerpo, media_json, quote_uri):
        """Contenido de un post de Bluesky para la bandeja: cuerpo + descripción de la media (tipo y
        texto alternativo) + marca de cita, como en el buffer normal de Bluesky."""
        import json as _json
        segmentos = [cuerpo] if cuerpo else []
        if media_json:
            try:
                for a in (_json.loads(media_json) or []):
                    tipo = (a.get("type") or "").lower()
                    alt = (a.get("alt") or "").strip()
                    if a.get("video") or "video" in tipo:
                        etq = _("Vídeo")
                    elif "gif" in tipo or "animated" in tipo:
                        etq = _("GIF")
                    else:
                        etq = _("Imagen")
                    etq += (": " + alt) if alt else (" " + _("(sin descripción)"))
                    segmentos.append(etq)
            except Exception:
                pass
        if quote_uri:
            segmentos.append(_("Cita"))
        return ": ".join(s for s in segmentos if s)

    def _enriquecer_content_x(self, cuerpo, retweeted_by, quoted_id, quoted_author="",
                              quoted_text="", poll_json="", media_json="", resp_a=""):
        """Contenido de un tweet para la bandeja, como en el buffer normal de X: [RT de @x],
        a quién responde (si se pidió en Preferencias → X), el cuerpo, el tuit citado con su
        texto, la encuesta y los adjuntos (un tuit de solo imagen no queda vacío)."""
        from app.ui.x_lectura import describir_media_x, texto_cita_x, texto_encuesta_x
        txt = cuerpo or ""
        if retweeted_by:
            txt = _("[RT de @{0}] ").format(retweeted_by) + txt
        cita = texto_cita_x(quoted_author, quoted_text) or (_("Cita") if quoted_id else "")
        extras = [x for x in (cita, texto_encuesta_x(poll_json), describir_media_x(media_json)) if x]
        if extras:
            txt = ": ".join([txt] + extras) if txt else ": ".join(extras)
        if resp_a and config.get("x", "prefijo_respuesta", False):
            from app.core.at_lectura import texto_respuesta_a
            txt = _("{0}: ").format(texto_respuesta_a(resp_a)) + txt
        return txt

    def _inbox_filas_completas(self, cols):
        """Devuelve la lista DESEADA de filas de la bandeja (todas las fuentes, ordenadas por
        fecha), lista para pintar o para inserción incremental. Cada elemento:
        (db_id, cols_vals, origen, date_iso, fecha_rel)."""
        from app.ui.main_frame import formatear_tiempo_relativo
        todas = []
        for fuente in self._inbox_fuentes():
            todas.extend(self._inbox_filas_de_fuente(fuente))
        # Descartar entradas con fecha A FUTURO (feeds con fecha futura o zona mal declarada): se
        # quedarían arriba del todo sin poder quitarse y descolocan el orden. Se da un pequeño margen
        # por desfases de reloj. También se descartan filas SIN contenido útil (autor y texto vacíos),
        # que solo mostraban la fecha ("hace 1 hora" y nada más).
        limite_futuro = datetime.now() + timedelta(minutes=2)
        todas = [f for f in todas
                 if f["dt"] <= limite_futuro and (f["author"].strip() or f["content"].strip())]
        newest = config.get("general", "newest_first", True)
        todas.sort(key=lambda f: f["dt"], reverse=bool(newest))
        resultado = []
        for f in todas:
            fecha_rel = formatear_tiempo_relativo(f["date_iso"]) if f["date_iso"] else ""
            # Red de seguridad: si tras enriquecer sigue sin texto (una red que aún no describa
            # todo su contenido), se dice explícitamente en vez de dejar la fila muda, que es lo
            # que el usuario percibe como "publicación fantasma".
            contenido = f["content"].strip() or _("(sin texto)")
            valores = {
                "origen": self._inbox_origen_label(f["red"], f["account_id"], f["buffer"]),
                "author": f["author"], "content": contenido, "date": fecha_rel,
            }
            cols_vals = [valores.get(c, "") for c, _e in cols]
            origen = {"red": f["red"], "account_id": f["account_id"],
                      "timeline_type": f["buffer"], "modo": _MODO_POR_RED.get(f["red"], "toots")}
            resultado.append((f["db_id"], cols_vals, origen, f["date_iso"], fecha_rel))
        return resultado

    def _cargar_listado_bandeja(self):
        """Carga el buffer de bandeja: mezcla las fuentes elegidas, ordena por fecha y pinta con la
        plantilla inbox_template (que incluye la columna {origen})."""
        self.lista_items.congelar_diferido()
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items.reset_focus_col()
        self.lista_items_db_ids.clear()
        # ORIGEN por fila: lista PARALELA a lista_items_db_ids (se mantiene al insertar/quitar).
        self._bandeja_origen = []
        self._modo_lista = "bandeja"
        # Marca que el buffer ACTIVO es la bandeja (se mantiene aunque _fijar_contexto_bandeja cambie
        # temporalmente _modo_lista para una fila). Gobierna el panel de detalles y la botonera de la
        # bandeja (preferencias inbox.show_details_panel / inbox.show_action_buttons).
        self._bandeja_activa = True
        # Panel de detalles y botonera de la bandeja: mostrar/ocultar según sus preferencias.
        self._aplicar_visibilidad_detalles()
        self._actualizar_botonera()

        cols = self._construir_columnas_plantilla("inbox_template")
        self._pintar_filas_bandeja(self._inbox_filas_completas(cols), cols)

    def _pintar_filas_bandeja(self, deseadas, cols):
        """Pinta la lista de la bandeja con 'deseadas' (ya ordenadas), preservando el FOCO en el
        elemento donde estabas (se restaura por db_id, no por índice). Reutilizado por la carga
        completa y por la actualización (que recoloca lo que llega tarde)."""
        col_fecha = self._col_de_campo(cols, "date")
        self.lista_items.congelar_diferido()
        self.lista_items.DeleteAllItems()
        self.lista_items.limpiar_lecturas()
        self.lista_items.reset_focus_col()
        self.lista_items_db_ids.clear()
        # ORIGEN por fila: lista PARALELA a lista_items_db_ids.
        self._bandeja_origen = []
        for idx, (db_id, cols_vals, origen, date_iso, fecha_rel) in enumerate(deseadas):
            valores = {c: cols_vals[i] for i, (c, _e) in enumerate(cols)}
            pos = self._volcar_fila_plantilla(idx, cols, valores)
            self.lista_items_db_ids.append(db_id)
            self._bandeja_origen.append(origen)
            if col_fecha is not None and date_iso:
                self.lista_items.establecer_fecha(pos, col_fecha, date_iso, fecha_rel)
        self._restaurar_foco_lista()

    def _insertar_bandeja_incremental(self, deseadas, cols) -> int:
        """Inserta en la bandeja SOLO las publicaciones nuevas, en su posición por fecha, UNA A UNA
        y SIN reconstruir ni mover el foco (igual que _insertar_items_incremental, pero manteniendo
        la lista PARALELA de orígenes _bandeja_origen). Así NVDA no relee tu elemento al entrar algo
        nuevo. No se reordena lo que ya estaba ni se elimina nada: eso se hace al pulsar F5."""
        actuales = set(self.lista_items_db_ids)
        if not any(d[0] not in actuales for d in deseadas):
            return 0
        col_fecha = self._col_de_campo(cols, "date")
        insertados = 0
        # Freeze/Thaw: la lista se repinta una sola vez al final, no por cada fila.
        self.lista_items.Freeze()
        try:
            for i, (db_id, cols_vals, origen, date_iso, fecha_rel) in enumerate(deseadas):
                if db_id in actuales:
                    continue
                # insertar_fila desplaza el foco posición a posición sobre TU elemento, en silencio.
                self.lista_items.insertar_fila(i, list(cols_vals), "")
                if col_fecha is not None and date_iso:
                    self.lista_items.establecer_fecha(i, col_fecha, date_iso, fecha_rel)
                # Mantener en paralelo db_ids y orígenes en la MISMA posición.
                self.lista_items_db_ids.insert(i, db_id)
                self._bandeja_origen.insert(i, origen)
                insertados += 1
        finally:
            self.lista_items.Thaw()
        return insertados

    def _actualizar_bandeja_incremental(self):
        """Mantiene la bandeja al día conforme llegan publicaciones, insertando SOLO lo nuevo en su
        sitio por fecha, en SILENCIO y sin mover el foco (estilo TWBlue). NO reconstruye la lista en
        los sondeos (eso hacía que NVDA releyera tu elemento); el reordenado de lo que llega tarde y
        la limpieza se hacen al pulsar F5 (recarga completa)."""
        # DOBLE comprobación (defensa en profundidad): además del modo, el marcador
        # _bandeja_activa, que la navegación pone a False SIEMPRE al salir de la bandeja.
        # Sin esto, si algún buffer no fijaba su propio modo al cargar, el sondeo de la
        # bandeja seguía insertando sus filas encima de la lista del otro buffer.
        if (getattr(self, "_modo_lista", "") != "bandeja"
                or not getattr(self, "_bandeja_activa", False)):
            return
        cols = getattr(self, "_cols_actuales", None)
        if not cols:
            return
        deseadas = self._inbox_filas_completas(cols)
        deseados_ids = [d[0] for d in deseadas]
        if deseados_ids == list(self.lista_items_db_ids):
            return  # mismo contenido y mismo orden: nada que insertar
        insertados = self._insertar_bandeja_incremental(deseadas, cols)
        if insertados:
            from app.core.event_bus import event_bus
            event_bus.emit("sound_trigger", "timeline_refreshed")

    def _sondear_fuentes_bandeja(self):
        """Sondea en segundo plano las fuentes de la bandeja (descarga lo nuevo a la base de datos)
        y, al terminar cada una, inserta lo nuevo en la lista. Se llama desde el temporizador de
        actualización cuando la bandeja es el buffer activo. Deduplica por (red, cuenta, buffer).

        IMPORTANTE: cada fuente se sondea respetando SU intervalo configurado (no en cada tick). Sin
        este freno, RSS —que actualiza TODOS sus feeds de golpe— se relanzaba cada ~60 s aunque su
        intervalo fuera de 5 min, descargando/parseando decenas de feeds por minuto (tirones y
        cuelgues). El sondeo de fondo general (_sondeo_fondo_servicios) ya mantiene las redes al día."""
        import time as _t
        from app.services.mastodon.mastodon_service import mastodon_service
        if not getattr(self, "_bandeja_activa", False):
            return  # ya no estás en la bandeja: no sondear ni insertar nada suyo
        ahora = _t.time()
        ult = getattr(self, "_inbox_ultimo_sondeo", None)
        if ult is None:
            ult = self._inbox_ultimo_sondeo = {}

        def _interval(red):
            if red == "rss":
                return max(60, int(config.get("rss", "update_interval", 30)) * 60)
            if red == "bluesky":
                return max(60, int(config.get("bluesky", "update_interval", 60)))
            if red == "mastodon":
                return max(30, int(config.get("mastodon", "update_interval", 60)))
            return 60

        vistos = set()
        def _cb(*_a):
            wx.CallAfter(self._actualizar_bandeja_incremental)
        for f in self._inbox_fuentes():
            clave = (f.get("red"), f.get("account_id"), f.get("buffer"))
            if clave in vistos:
                continue
            vistos.add(clave)
            red, acc, buf = clave
            # Freno por intervalo: solo se sondea esta fuente si venció SU intervalo desde la última
            # vez (la primera vez, t0 None, sí se sondea para traer datos frescos al entrar).
            t0 = ult.get(clave)
            if t0 is not None and (ahora - t0) < _interval(red):
                continue
            ult[clave] = ahora
            try:
                if red == "mastodon":
                    mastodon_service.cargar_timeline_async(acc, timeline_type=buf, callback=_cb)
                elif red == "bluesky":
                    from app.services.bluesky import bluesky_service as _bsky
                    # Con la sesión caducada la llamada aborta sola (ya se avisó al usuario);
                    # se comprueba aquí para no marcar la fuente como sondeada y que retome en
                    # cuanto vuelva a iniciar sesión.
                    if not _bsky.sesion_caducada(acc):
                        _bsky.cargar_timeline_async(acc, timeline_type=buf, callback=_cb)
                elif red == "rss":
                    from app.services.rss.rss_service import rss_service
                    # Sondeo AUTOMÁTICO desde la bandeja: pocos feeds a la vez (CPU) para no dar
                    # tirones en la interfaz, y SOLO los feeds vencidos (scheduler, RSS-05).
                    # El refresco MANUAL (F5) usa tu concurrencia y fuerza todos.
                    rss_service.actualizar_todos_async(acc, callback=_cb, max_par_override=3,
                                                       solo_vencidos=True)
                # X no se sondea aquí (límites de peticiones estrictos); se relee de la BD igualmente.
            except Exception as e:
                log.debug(f"Bandeja: no se pudo sondear la fuente {clave}: {e}")
        # Releer también lo que ya hubiera traído el sondeo de fondo de cada red.
        self._actualizar_bandeja_incremental()

    # ---------------- Contexto temporal por fila (menú contextual y ATAJOS) ----------------
    def _fijar_contexto_bandeja(self, idx):
        """Fija TEMPORALMENTE el contexto de la RED de la fila 'idx' (cuenta, buffer y modo) para que
        el menú contextual y los ATAJOS de teclado actúen sobre ella con los handlers de su red.
        Devuelve el estado anterior (para restaurarlo con _restaurar_contexto_bandeja), o None."""
        origenes = getattr(self, "_bandeja_origen", [])
        origen = origenes[idx] if 0 <= idx < len(origenes) else None
        if not origen:
            return None
        guardado = (
            self.active_mastodon_account_id, self.active_rss_account_id, self.active_telegram_account_id,
            self.active_bluesky_account_id, self.active_x_account_id,
            getattr(self, "_modo_lista", "toots"), self.active_timeline_type,
        )
        self.active_mastodon_account_id = None
        self.active_rss_account_id = None
        self.active_telegram_account_id = None
        self.active_bluesky_account_id = None
        self.active_x_account_id = None
        red = origen["red"]
        acc = origen["account_id"]
        self._modo_lista = origen.get("modo", "toots")
        self.active_timeline_type = origen.get("timeline_type", "home")
        if red == "mastodon":
            self.active_mastodon_account_id = acc
        elif red == "rss":
            self.active_rss_account_id = acc
        elif red == "bluesky":
            self.active_bluesky_account_id = acc
        elif red == "x":
            self.active_x_account_id = acc
        return guardado

    def _restaurar_contexto_bandeja(self, guardado):
        """Restaura el contexto que había antes de _fijar_contexto_bandeja."""
        try:
            (self.active_mastodon_account_id, self.active_rss_account_id, self.active_telegram_account_id,
             self.active_bluesky_account_id, self.active_x_account_id,
             self._modo_lista, self.active_timeline_type) = guardado
        finally:
            self._bandeja_ctx_pendiente = False

    def _menu_contextual_bandeja(self, event, idx):
        """Abre el menú contextual de la RED de la fila enfocada: fija su contexto y delega en el
        menú contextual normal; al cerrarse (PopupMenu es síncrono), restaura el contexto."""
        guardado = self._fijar_contexto_bandeja(idx)
        if guardado is None:
            return
        try:
            self._on_context_menu(event, _idx_forzado=idx)
        finally:
            self._restaurar_contexto_bandeja(guardado)

    # ---------------- Panel de detalles dinámico (por red de la fila) ----------------
    def _rellenar_detalle_bandeja(self, idx):
        """Rellena el panel de detalles con el contenido de la fila 'idx' SEGÚN su red (Mastodon,
        Bluesky, X o RSS). Es el panel de detalles de la bandeja (opcional: inbox.show_details_panel)."""
        origenes = getattr(self, "_bandeja_origen", [])
        if not (0 <= idx < len(origenes)) or idx >= len(self.lista_items_db_ids):
            return
        origen = origenes[idx]
        db_id = self.lista_items_db_ids[idx]
        red = (origen or {}).get("red", "")
        from app.core.html_utils import html_a_texto

        def _fecha(iso):
            if not iso:
                return ""
            try:
                dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                return dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                return str(iso).replace("T", " ")[:16]

        texto = ""
        try:
            if red == "mastodon":
                r = db.query_one("SELECT author_name, author_username, COALESCE(spoiler_text,''), "
                                 "COALESCE(content,''), created_at, COALESCE(visibility,''), "
                                 "COALESCE(reblog_by_name,''), COALESCE(favourited,0), COALESCE(reblogged,0) "
                                 "FROM mastodon_statuses WHERE id = ?", (db_id,))
                if r:
                    autor, user, cw, content, fecha, vis, reblog, fav, rebl = r
                    vis_map = {"public": _("Público"), "unlisted": _("No listado"),
                               "private": _("Privado"), "direct": _("Mensaje directo")}
                    partes = [f"{autor} ({user})"]
                    if reblog:
                        partes.append(_("Impulsado por: {0}").format(reblog))
                    if fecha:
                        partes.append(_("Fecha: {0}").format(_fecha(fecha)))
                    if vis:
                        partes.append(_("Visibilidad: {0}").format(vis_map.get(vis, vis)))
                    ind = []
                    if fav:
                        ind.append(_("Favorito"))
                    if rebl:
                        ind.append(_("Impulsado"))
                    if ind:
                        partes.append(", ".join(ind))
                    if cw:
                        partes.append(_("Advertencia de contenido: {0}").format(cw))
                    cuerpo = html_a_texto(content) if content else ""
                    texto = "\n".join(partes) + ("\n\n" + cuerpo if cuerpo else "")
            elif red == "bluesky":
                r = db.query_one("SELECT author_name, author_handle, COALESCE(content,''), created_at "
                                 "FROM bluesky_posts WHERE id = ?", (db_id,))
                if r:
                    autor, handle, content, fecha = r
                    texto = f"{autor} (@{handle})\n" + _("Fecha: {0}").format(_fecha(fecha)) + \
                            ("\n\n" + (html_a_texto(content) if content else ""))
            elif red == "x":
                r = db.query_one("SELECT author_name, author_screen_name, COALESCE(text,''), created_at "
                                 "FROM x_tweets WHERE id = ?", (db_id,))
                if r:
                    autor, screen, content, fecha = r
                    texto = f"{autor} (@{screen})\n" + _("Fecha: {0}").format(_fecha(fecha)) + \
                            ("\n\n" + (content or ""))
            elif red == "rss":
                # La MISMA vista previa completa que en el buffer RSS (mf_rss_vista), con la
                # descarga del artículo entero si el feed solo trae un resumen.
                texto = self._texto_vista_previa_rss(db_id)
        except Exception as e:
            log.debug(f"No se pudo construir el detalle de la bandeja: {e}")
            texto = ""
        try:
            self.txt_detalles.SetValue(texto)
        except Exception:
            pass
        if red == "rss" and self._pedir_completo_para_vista(db_id):
            self._programar_completo_vista(db_id)

    # ---------------- Diálogos de configuración (agregar / quitar fuentes) ----------------
    def _on_inbox_agregar_fuentes(self):
        """Diálogo para AÑADIR uno o varios buffers (de cualquier red y cuenta activa) a la bandeja."""
        disponibles = self._inbox_buffers_disponibles()
        actuales = self._inbox_fuentes()
        def _clave(f):
            return (f["red"], f["account_id"], f["buffer"])
        ya = {_clave(f) for f in actuales}
        candidatos = [d for d in disponibles if _clave(d) not in ya]
        if not candidatos:
            notificar(_("No hay más buffers que añadir (ya están todos en la bandeja o no hay cuentas activas)."))
            return
        etiquetas = [d["label"] for d in candidatos]
        sel = self._inbox_dialogo_casillas(
            _("Agregar buffers a la bandeja"),
            _("Marca los buffers que quieres añadir a la bandeja de entrada (puedes marcar varios):"),
            etiquetas)
        if sel is None:
            return
        nuevas = list(actuales)
        for i in sel:
            d = candidatos[i]
            nuevas.append({"red": d["red"], "account_id": d["account_id"], "buffer": d["buffer"]})
        self._inbox_guardar_fuentes(nuevas)
        notificar(_("{0} buffer(s) añadido(s) a la bandeja.").format(len(sel)))
        if getattr(self, "_modo_lista", "") == "bandeja":
            self._cargar_listado_bandeja()

    def _on_inbox_quitar_fuentes(self):
        """Diálogo para QUITAR uno o varios buffers de la bandeja."""
        actuales = self._inbox_fuentes()
        if not actuales:
            notificar(_("La bandeja de entrada no tiene ningún buffer configurado."))
            return
        etiquetas = [self._inbox_origen_label(f["red"], f["account_id"], f["buffer"]) for f in actuales]
        sel = self._inbox_dialogo_casillas(
            _("Quitar buffers de la bandeja"),
            _("Marca los buffers que quieres quitar de la bandeja de entrada (puedes marcar varios):"),
            etiquetas)
        if sel is None:
            return
        quitar = set(sel)
        nuevas = [f for i, f in enumerate(actuales) if i not in quitar]
        self._inbox_guardar_fuentes(nuevas)
        notificar(_("{0} buffer(s) quitado(s) de la bandeja.").format(len(quitar)))
        if getattr(self, "_modo_lista", "") == "bandeja":
            self._cargar_listado_bandeja()

    def _inbox_dialogo_casillas(self, titulo, mensaje, etiquetas):
        """Diálogo accesible con CASILLAS (AccessibleCheckListBox, el mismo que en Sonidos): se
        pueden marcar y desmarcar varias. Devuelve la lista de índices marcados, o None si se
        cancela."""
        from .accessible_controls import AccessibleCheckListBox, habilitar_escape_cierre
        dlg = wx.Dialog(self, title=titulo, size=(560, 460),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(dlg, label=mensaje), 0, wx.ALL, 8)
        clb = AccessibleCheckListBox(dlg, choices=etiquetas, name=titulo)
        s.Add(clb, 1, wx.EXPAND | wx.ALL, 8)
        s.Add(dlg.CreateButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        dlg.SetSizer(s)
        try:
            habilitar_escape_cierre(dlg)
        except Exception:
            pass
        if etiquetas:
            clb.SetSelection(0)
        clb.SetFocus()
        sel = None
        if dlg.ShowModal() == wx.ID_OK:
            sel = list(clb.GetCheckedItems())
        dlg.Destroy()
        return sel
