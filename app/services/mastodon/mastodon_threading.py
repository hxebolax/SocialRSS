# -*- coding: utf-8 -*-
"""
Módulo para el editor y publicador de hilos secuenciales en Mastodon.
Permite dividir texto de forma inteligente, guardar borradores de hilos y publicar paso a paso.
"""

import os
import re
import json
import uuid
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Callable

from app.core.database import db
from app.core.task_manager import task_manager
from .mastodon_service import mastodon_service

log = logging.getLogger(__name__)

# Expresiones regulares para protección de bloques
RE_BLOQUE_CODIGO = re.compile(r"```.*?```", re.DOTALL)
RE_CODIGO_LINEA = re.compile(r"`[^`\n]+`")
RE_URL = re.compile(r"https?://[^\s]+")
RE_MENCION = re.compile(r"@[a-zA-Z0-9_]+(?:@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})?")
RE_HASHTAG = re.compile(r"#[a-zA-Z0-9_]+")
RE_COMILLAS = re.compile(r'"[^"\n]*"|\'[^\'\n]*\'|«[^»\n]*»|“[^”\n]*”')

# Signos de puntuación para cortes ordenados
RE_ORACION = re.compile(r"[.?!]+(?:\s+|$)")
RE_PUNTUACION = re.compile(r"[,;:-]+(?:\s+|$)")

def obtener_regiones_protegidas(texto: str) -> List[Tuple[int, int]]:
    """Devuelve los rangos de caracteres que no deben ser divididos (URLs, menciones, código)."""
    regiones = []
    patrones = [RE_BLOQUE_CODIGO, RE_CODIGO_LINEA, RE_URL, RE_MENCION, RE_HASHTAG, RE_COMILLAS]
    for p in patrones:
        for match in p.finditer(texto):
            regiones.append((match.start(), match.end()))
    return regiones

def es_indice_seguro(idx: int, regiones: List[Tuple[int, int]]) -> bool:
    """Verifica si cortar en una posición determinada cae dentro de una región protegida."""
    for inicio, fin in regiones:
        if inicio < idx < fin:
            return False
    return True

# Numeración de hilo al final de un toot, en cualquiera de los formatos soportados:
# "5/5", "(5/5)", "Hilo 5/5", "Parte 5 de 5". Se usa para quitarla y no duplicarla
# al volver a dividir un texto que ya estaba numerado (p. ej. al reanudar un hilo).
_RE_NUMERACION_FIN = re.compile(
    r"\s*(?:\(\d+\s*/\s*\d+\)|Hilo\s+\d+\s*/\s*\d+|Parte\s+\d+\s+de\s+\d+|\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE,
)


def quitar_numeracion_hilo(texto: str) -> str:
    """Quita la numeración de hilo del FINAL de un texto (incluso si está repetida)."""
    if not texto:
        return texto
    anterior = None
    actual = texto
    # Repetir por si la numeración quedó duplicada (p. ej. "5/5 5/5").
    while actual != anterior:
        anterior = actual
        actual = _RE_NUMERACION_FIN.sub("", actual).rstrip()
    return actual


def normalizar_menciones(texto: str) -> str:
    """Normaliza una lista de destinatarios escrita a mano ("ana, @luis@otra.social") como una
    cadena de menciones lista para anteponer: "@ana @luis@otra.social". Sin repetidos."""
    if not texto:
        return ""
    import re as _re
    vistos, salida = set(), []
    for bruto in _re.split(r"[\s,;]+", texto.strip()):
        acct = bruto.strip().lstrip("@")
        if not acct or acct.lower() in vistos:
            continue
        vistos.add(acct.lower())
        salida.append("@" + acct)
    return " ".join(salida)


def menciones_de_texto(texto: str) -> str:
    """Menciones (@acct) que ya aparecen en un texto, en orden y sin repetidos."""
    if not texto:
        return ""
    import re as _re
    encontradas = _re.findall(r"@([A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_])?(?:@[\w.\-]+)?)", texto)
    return normalizar_menciones(" ".join(encontradas))


def asegurar_menciones(texto: str, menciones: str) -> str:
    """Devuelve el texto con TODAS las `menciones` presentes, añadiendo delante las que falten.

    Se usa antes de publicar un hilo privado o directo: si el usuario edita, inserta o fusiona
    mensajes a mano, alguno podría quedarse sin la mención del destinatario y, al ser `direct`
    "visible solo para los mencionados", ese mensaje no le llegaría."""
    menciones = (menciones or "").strip()
    if not menciones:
        return texto
    presentes = {m.lower() for m in menciones_de_texto(texto).split() if m}
    faltan = [m for m in menciones.split() if m and m.lower() not in presentes]
    if not faltan:
        return texto
    return (" ".join(faltan) + " " + (texto or "").lstrip()).strip()


def obtener_numeracion_str(indice: int, total: int, formato: str) -> str:
    """Genera la cadena de numeración según el formato escogido."""
    if formato == "sin":
        return ""
    elif formato == "simple":
        return f"{indice}/{total}"
    elif formato == "parentesis":
        return f"({indice}/{total})"
    elif formato == "hilo":
        return f"Hilo {indice}/{total}"
    elif formato == "parte":
        return f"Parte {indice} de {total}"
    return f"{indice}/{total}"

def buscar_mejor_corte(texto: str, limite: int, regiones: List[Tuple[int, int]]) -> int:
    """Busca heurísticamente el mejor punto para cortar el texto antes del límite."""
    if len(texto) <= limite:
        return len(texto)
        
    # 1. Cortar por párrafos dobles (\n\n)
    posiciones = [m.start() for m in re.finditer(r"\n\n", texto)]
    for pos in reversed(posiciones):
        if pos < limite and es_indice_seguro(pos, regiones):
            return pos + 2
            
    # 2. Cortar por saltos de línea simples (\n)
    posiciones = [m.start() for m in re.finditer(r"\n", texto)]
    for pos in reversed(posiciones):
        if pos < limite and es_indice_seguro(pos, regiones):
            return pos + 1
            
    # 3. Cortar por oraciones
    posiciones = [m.end() for m in RE_ORACION.finditer(texto)]
    for pos in reversed(posiciones):
        if pos < limite and es_indice_seguro(pos, regiones):
            return pos
            
    # 4. Cortar por puntuación intermedia
    posiciones = [m.end() for m in RE_PUNTUACION.finditer(texto)]
    for pos in reversed(posiciones):
        if pos < limite and es_indice_seguro(pos, regiones):
            return pos
            
    # 5. Cortar por palabras
    posiciones = [m.start() for m in re.finditer(r"\s+", texto)]
    for pos in reversed(posiciones):
        if pos < limite and es_indice_seguro(pos, regiones):
            return pos + 1
            
    # Fallback: corte bruto seguro
    for pos in range(limite, 0, -1):
        if es_indice_seguro(pos, regiones):
            return pos
            
    return limite

def dividir_texto_recursivo(texto: str, limite: int, regiones: List[Tuple[int, int]], offset: int = 0) -> List[str]:
    """Divide recursivamente el texto en fragmentos que no superen el límite útil."""
    if not texto:
        return []
        
    if len(texto) <= limite:
        return [texto]
        
    # Calcular regiones relativas al offset del fragmento actual
    regiones_locales = []
    for inicio, fin in regiones:
        if fin > offset and inicio < offset + len(texto):
            regiones_locales.append((max(0, inicio - offset), min(len(texto), fin - offset)))
            
    corte = buscar_mejor_corte(texto, limite, regiones_locales)
    if corte == 0:
        corte = limite
        
    actual = texto[:corte]
    resto = texto[corte:]
    
    return [actual] + dividir_texto_recursivo(resto, limite, regiones, offset + corte)


class MastodonThreadingManager:
    """Gestor para dividir, guardar y publicar hilos de toots."""
    
    @staticmethod
    def _longitud_mastodon(texto: str, cw: str = "") -> int:
        """Longitud CONTABLE según las reglas de Mastodon (URLs=23, menciones sin dominio,
        grafemas, el CW cuenta). Es la que valida el servidor, no len() de Python."""
        try:
            from app.ui.mastodon_compose_helpers import longitud_contable_mastodon
            return longitud_contable_mastodon(texto, cw)
        except Exception:
            return len(cw or "") + len(texto or "")

    @staticmethod
    def dividir_hilo(
        texto: str,
        limite_max: int = 500,
        margen: int = 15,
        numerar: bool = True,
        formato: str = "simple",
        texto_inicial: str = "",
        texto_final: str = "",
        cw: str = "",
        menciones: str = ""
    ) -> List[str]:
        """Divide inteligentemente el texto en una lista de toots formateada.

        Cada parte FINAL (con prefijo, sufijo y numeración ya puestos) se valida con la
        longitud CONTABLE de Mastodon (URLs=23, menciones sin dominio, grafemas, CW):
        antes se contaba con len() y un prefijo largo o una URL corta (que Mastodon cuenta
        como 23) podían dejar una parte por encima del límite y el servidor la rechazaba a
        mitad de hilo (auditoría H3). Si alguna parte se pasa, se re-divide con un límite
        menor hasta que todas quepan.

        `menciones` ("@ana @luis") se antepone a TODAS las partes, no solo a la primera. Es lo
        que exige un hilo privado o directo: la documentación de Mastodon define la visibilidad
        `direct` como "visible solo para los usuarios MENCIONADOS", así que si el destinatario
        solo aparece en el primer mensaje, del hilo únicamente recibe ese. Su longitud se reserva
        en todas las partes."""
        if not texto:
            return []

        texto_limpio = texto.replace("\r\n", "\n").replace("\r", "\n").strip()
        regiones = obtener_regiones_protegidas(texto_limpio)
        medir = MastodonThreadingManager._longitud_mastodon

        # Reservas EXACTAS: el prefijo y el sufijo se descuentan por su longitud contable
        # (+2 por el separador de párrafo), y el CW cuenta en TODAS las partes. Las menciones
        # también van en todas (+1 por el espacio que las separa del texto).
        menciones = (menciones or "").strip()
        reserva_menciones = (medir(menciones) + 1) if menciones else 0
        reserva_prefijo = (medir(texto_inicial.strip()) + 2) if texto_inicial.strip() else 0
        reserva_sufijo = (medir(texto_final.strip()) + 2) if texto_final.strip() else 0
        reserva_cw = medir("", cw) if cw else 0
        limite_util_base = (limite_max - margen - reserva_cw - reserva_menciones
                            - max(reserva_prefijo, reserva_sufijo))

        def _formatear(cuerpos):
            total = len(cuerpos)
            partes = []
            for idx, cuerpo in enumerate(cuerpos, 1):
                toot = cuerpo.strip()
                if idx == 1 and texto_inicial:
                    toot = f"{texto_inicial.strip()}\n\n{toot}"
                if idx == total and texto_final:
                    toot = f"{toot}\n\n{texto_final.strip()}"
                if numerar:
                    num_str = obtener_numeracion_str(idx, total, formato)
                    if num_str:
                        toot = f"{toot} {num_str}"
                if menciones:
                    toot = f"{menciones} {toot}"
                partes.append(toot)
            return partes

        # Iteración de convergencia para aproximar el total y reservar la numeración.
        total_estimado = 3
        toots_cuerpo = []
        limite_util = limite_util_base
        for _ in range(4):
            long_num_max = 0
            if numerar:
                for i in range(1, total_estimado + 1):
                    num_str = obtener_numeracion_str(i, total_estimado, formato)
                    long_num_max = max(long_num_max, len(num_str) + 1)  # +1 por el espacio
            limite_util = max(20, limite_util_base - long_num_max)
            toots_cuerpo = dividir_texto_recursivo(texto_limpio, limite_util, regiones)
            if len(toots_cuerpo) == total_estimado:
                break
            total_estimado = len(toots_cuerpo)

        # VALIDACIÓN FINAL con la longitud contable real: si alguna parte formateada se
        # pasa (URL corta contada como 23, emojis...), re-dividir con un límite menor.
        hilo_formateado = _formatear(toots_cuerpo)
        for _ in range(6):
            exceso = max((medir(t, cw) - limite_max for t in hilo_formateado), default=0)
            if exceso <= 0:
                break
            limite_util = max(20, limite_util - exceso - 5)
            toots_cuerpo = dividir_texto_recursivo(texto_limpio, limite_util, regiones)
            hilo_formateado = _formatear(toots_cuerpo)
        return hilo_formateado

    # --- PERSISTENCIA (GUARDAR Y CARGAR BORRADORES DE HILOS) ---
    @staticmethod
    def guardar_hilo(
        thread_id: Optional[str],
        title: str,
        account_id: int,
        toots_data: List[Dict[str, Any]],
        visibility: str = "public",
        spoiler_text: str = "",
        mentions: str = ""
    ) -> str:
        """
        Guarda un hilo en SQLite (borrador o publicado parcial).
        toots_data: lista de dicts: {'content': str, 'media_paths': List[str], 'alt_texts': List[str], 'posted_id': str, 'posted_at': str}

        La privacidad (visibility), el aviso de contenido y los destinatarios se guardan CON el
        hilo: un hilo directo que falle a media publicación se reanuda desde "Hilos guardados", y
        sin esto se reanudaría en público, que es justo lo contrario de lo que el usuario pidió.
        """
        tid = thread_id if thread_id else str(uuid.uuid4())
        now_str = datetime.now().isoformat()

        # TODO el agregado (cabecera + ítems + FTS) se confirma en UNA transacción: un fallo
        # a mitad ya no puede dejar el hilo vacío o incompleto (auditoría H2). Las rutas y
        # los textos alternativos van en JSON: una ruta con coma ("C:\tmp\foto,uno.jpg") se
        # partía en dos con el formato antiguo separado por comas (auditoría H1).
        with db.transaccion():
            sql_header = """
            INSERT OR REPLACE INTO mastodon_threads (id, account_id, title, created_at, updated_at, published,
                                                     visibility, spoiler_text, mentions)
            VALUES (?, ?, ?, COALESCE((SELECT created_at FROM mastodon_threads WHERE id = ?), ?), ?, 0, ?, ?, ?)
            """
            db.execute(sql_header, (tid, account_id, title, tid, now_str, now_str,
                                    visibility or "public", spoiler_text or None, mentions or None))

            db.execute("DELETE FROM mastodon_thread_items WHERE thread_id = ?", (tid,))

            sql_item = """
            INSERT INTO mastodon_thread_items (thread_id, item_order, content, media_paths, alt_texts, posted_status_id, posted_at, idem_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            params_list = []
            for idx, toot in enumerate(toots_data):
                paths = toot.get("media_paths") or []
                alts = toot.get("alt_texts") or []
                params_list.append((
                    tid,
                    idx,
                    toot["content"],
                    json.dumps(paths, ensure_ascii=False) if paths else None,
                    json.dumps(alts, ensure_ascii=False) if alts else None,
                    toot.get("posted_status_id"),
                    toot.get("posted_at"),
                    toot.get("idem_key")
                ))

            db.execute_many(sql_item, params_list)

            # Indexar cabecera de hilo en FTS
            try:
                db.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (f"thread:{tid}",))
                db.execute(
                    "INSERT INTO mastodon_statuses_fts (status_db_id, author_name, author_username, content, spoiler_text) VALUES (?, ?, ?, ?, ?)",
                    (f"thread:{tid}", title, "Hilo Local", f"Hilo: {title} conteniendo {len(toots_data)} toots.", "")
                )
            except Exception as fts_err:
                log.debug(f"Error indexando hilo local: {fts_err}")

        log.info(f"Hilo guardado con éxito. ID: {tid}")
        return tid

    @staticmethod
    def _parsear_lista(valor, separador):
        """Lee una lista guardada en JSON (formato actual) o en el formato antiguo
        separado por caracteres (compatibilidad con hilos ya guardados)."""
        if not valor:
            return []
        try:
            datos = json.loads(valor)
            if isinstance(datos, list):
                return [str(x) for x in datos]
        except (ValueError, TypeError):
            pass
        return valor.split(separador)

    @staticmethod
    def cargar_hilo(thread_id: str) -> Optional[Dict[str, Any]]:
        """Carga un hilo de base de datos con todos sus toots asociados."""
        sql_header = ("SELECT id, account_id, title, created_at, updated_at, published, "
                      "COALESCE(visibility, 'public'), COALESCE(spoiler_text, ''), COALESCE(mentions, '') "
                      "FROM mastodon_threads WHERE id = ?")
        header = db.query_one(sql_header, (thread_id,))
        if not header:
            return None
            
        sql_items = """
        SELECT item_order, content, media_paths, alt_texts, posted_status_id, posted_at, idem_key
        FROM mastodon_thread_items
        WHERE thread_id = ?
        ORDER BY item_order ASC
        """
        rows = db.query(sql_items, (thread_id,))

        toots = []
        for r in rows:
            toots.append({
                "content": r[1],
                "media_paths": MastodonThreadingManager._parsear_lista(r[2], ","),
                "alt_texts": MastodonThreadingManager._parsear_lista(r[3], "\x1f"),
                "posted_status_id": r[4],
                "posted_at": r[5],
                "idem_key": r[6]
            })
            
        return {
            "id": header[0],
            "account_id": header[1],
            "title": header[2],
            "created_at": header[3],
            "updated_at": header[4],
            "published": bool(header[5]),
            "visibility": header[6] or "public",
            "spoiler_text": header[7] or "",
            "mentions": header[8] or "",
            "toots": toots
        }

    @staticmethod
    def obtener_hilos(account_id: int) -> List[Dict[str, Any]]:
        """Recupera la lista de hilos registrados en una cuenta."""
        sql = "SELECT id, title, updated_at, published FROM mastodon_threads WHERE account_id = ? ORDER BY updated_at DESC"
        hilos = []
        try:
            rows = db.query(sql, (account_id,))
            for r in rows:
                hilos.append({
                    "id": r[0],
                    "title": r[1],
                    "updated_at": r[2],
                    "published": bool(r[3])
                })
        except Exception as e:
            log.error(f"Error al obtener hilos locales: {e}")
        return hilos

    @staticmethod
    def eliminar_hilo(thread_id: str):
        """Elimina por completo un hilo y sus toots."""
        db.execute("DELETE FROM mastodon_threads WHERE id = ?", (thread_id,))
        try:
            db.execute("DELETE FROM mastodon_statuses_fts WHERE status_db_id = ?", (f"thread:{thread_id}",))
        except Exception:
            pass
        log.info(f"Hilo ID {thread_id} eliminado de la base de datos.")

    # --- PUBLICACIÓN SECUENCIAL ASÍNCRONA ---
    def publicar_hilo_async(
        self,
        account_id: int,
        thread_id: str,
        visibilidad: Optional[str] = None,
        sensitive: bool = False,
        spoiler_text: Optional[str] = None,
        language: Optional[str] = None,
        callback: Optional[Callable[[bool, Optional[str]], None]] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ):
        """Publica secuencialmente el hilo en segundo plano respondiendo al toot anterior.

        Si no se indican `visibilidad` ni `spoiler_text`, se usan los GUARDADOS con el hilo. Así,
        reanudar un hilo directo desde "Hilos guardados" lo sigue publicando en directo y no en
        público, que sería un problema de privacidad, no una simple molestia."""

        def _bg_thread_post(ctx):
            hilo_info = self.cargar_hilo(thread_id)
            if not hilo_info:
                raise Exception("El hilo especificado no existe en la base de datos.")

            vis = visibilidad or hilo_info.get("visibility") or "public"
            cw_hilo = spoiler_text if spoiler_text is not None else hilo_info.get("spoiler_text", "")
            es_sensible = sensitive or bool((cw_hilo or "").strip())

            client = mastodon_service.obtener_cliente(account_id)
            toots = hilo_info["toots"]
            total_toots = len(toots)
            
            parent_id = None
            
            # Buscar el último publicado previamente para soportar continuación tras error
            for idx, toot in enumerate(toots):
                if toot.get("posted_status_id"):
                    parent_id = toot["posted_status_id"]
                    
            # Iniciar publicación secuencial
            for idx, toot in enumerate(toots):
                if ctx.is_cancelled():
                    raise Exception("Publicación cancelada por el usuario.")
                    
                # Si ya se publicó en un intento previo, saltarlo
                if toot.get("posted_status_id"):
                    ctx.report_progress(
                        int(((idx + 1) / total_toots) * 100),
                        f"Toot {idx + 1} de {total_toots} ya estaba enviado."
                    )
                    continue
                    
                ctx.report_progress(
                    int((idx / total_toots) * 100),
                    f"Preparando toot {idx + 1} de {total_toots}..."
                )
                
                # 1. Subir multimedia si corresponde
                media_ids = []
                media_paths = toot.get("media_paths", [])
                alt_texts = toot.get("alt_texts", [])
                
                for m_idx, path in enumerate(media_paths):
                    if ctx.is_cancelled():
                        raise Exception("Publicación cancelada.")
                        
                    if not os.path.exists(path):
                        raise FileNotFoundError(f"Adjunto no encontrado: {path}")
                        
                    ctx.report_progress(
                        int((idx / total_toots) * 100),
                        f"Toot {idx+1}: Subiendo multimedia {m_idx+1}..."
                    )
                    
                    from .mastodon_composer import _adivinar_mime
                    mime = _adivinar_mime(path)
                    alt = alt_texts[m_idx] if m_idx < len(alt_texts) else None

                    with open(path, "rb") as f:
                        res = client.media_post(f, mime_type=mime, description=alt)
                    # Esperar a que el servidor TERMINE de procesar el adjunto (vídeos y GIF
                    # tardan): publicar con un medio a medio procesar hace que algunas
                    # instancias rechacen el toot (auditoría H4; igual que el compositor).
                    if isinstance(res, dict) and not res.get("url"):
                        import time as _t
                        for _ in range(60):  # hasta ~60 s
                            if ctx.is_cancelled():
                                raise Exception("Publicación cancelada.")
                            _t.sleep(1)
                            try:
                                actualizado = client.media(res["id"])
                            except Exception:
                                break
                            if isinstance(actualizado, dict) and actualizado.get("url"):
                                break
                    media_ids.append(res["id"])
                    
                # 2. Publicar toot respondiendo al anterior
                ctx.report_progress(
                    int((idx / total_toots) * 100),
                    f"Toot {idx+1}: Publicando texto..."
                )
                
                cw = cw_hilo.strip() if cw_hilo and cw_hilo.strip() else None

                # Clave de IDEMPOTENCIA por elemento, PERSISTIDA ANTES del primer envío y
                # reutilizada en todo reintento (auditoría M7): si un timeout llega después
                # de que el servidor aceptara el toot pero antes de guardar posted_status_id,
                # "reanudar" repite la petición con la MISMA clave y el servidor devuelve el
                # toot ya creado en vez de duplicarlo (conserva las claves ~1 hora).
                idem_key = toot.get("idem_key") or ""
                if not idem_key:
                    idem_key = uuid.uuid4().hex
                    try:
                        db.execute(
                            "UPDATE mastodon_thread_items SET idem_key = ? "
                            "WHERE thread_id = ? AND item_order = ?",
                            (idem_key, thread_id, idx))
                    except Exception as e:
                        log.debug(f"No se pudo persistir la clave de idempotencia: {e}")

                status = client.status_post(
                    status=toot["content"],
                    in_reply_to_id=parent_id, # Respuesta al anterior (secuencial)
                    media_ids=media_ids if media_ids else None,
                    sensitive=es_sensible,
                    visibility=vis,
                    spoiler_text=cw,
                    language=(language or None),
                    idempotency_key=idem_key
                )
                
                # 3. Actualizar estado del ítem en SQLite de inmediato
                parent_id = status["id"]
                now_str = datetime.now().isoformat()
                
                db.execute(
                    "UPDATE mastodon_thread_items SET posted_status_id = ?, posted_at = ? WHERE thread_id = ? AND item_order = ?",
                    (parent_id, now_str, thread_id, idx)
                )
                
                # Cargar en timeline local cache
                try:
                    mastodon_service._procesar_y_guardar_toot(account_id, status)
                except Exception:
                    pass
                    
            # 4. Marcar hilo completo como publicado en SQLite
            db.execute("UPDATE mastodon_threads SET published = 1 WHERE id = ?", (thread_id,))
            return "Hilo completo enviado con éxito."

        def _on_finish(result, error):
            if error:
                log.error(f"Error al enviar el hilo: {error}")
                if callback: callback(False, str(error))
            else:
                if callback: callback(True, str(result))

        # unique_key: dos publicaciones simultáneas del MISMO hilo (doble clic, reanudar
        # mientras el primer intento sigue vivo) duplicarían toots; la segunda se descarta.
        task_manager.start_task(
            _bg_thread_post,
            callback=_on_finish,
            progress_callback=progress_callback,
            success_sound="thread_posted",
            error_sound="thread_error",
            pass_context=True,
            unique_key=f"mastodon:hilo:{thread_id}"
        )
