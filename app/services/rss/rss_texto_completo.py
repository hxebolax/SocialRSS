# -*- coding: utf-8 -*-
"""
Artículo completo de RSS: descargar la página de la noticia, extraer su cuerpo y guardarlo.

Lo usan el lector limpio (al abrir un artículo cuyo feed solo trae un resumen, o con su botón
"Descargar el artículo completo"), la cola de fondo del sondeo (feeds con "texto completo") y
cualquier otro punto que necesite la noticia entera. La extracción vive en rss_fulltext.py; aquí
solo se decide si merece la pena y se guarda.

Regla del proyecto: la red NUNCA va dentro de una transacción de escritura. Primero se descarga
y se extrae; después, en una transacción corta, se guarda.
"""

import logging
import time
from typing import Callable, Optional

from app.core.database import db
from app.core.task_manager import task_manager
from .rss_fulltext import FullTextExtractor

log = logging.getLogger(__name__)

# El texto nuevo sustituye al guardado solo si es claramente más largo (+10 %): así no se
# cambia un artículo que el feed ya traía completo por una versión equivalente de la web.
_MEJORA_MINIMA = 1.10


def _texto_de(contenido: str) -> str:
    from app.core.html_utils import html_a_texto
    return html_a_texto(contenido or "")


class _TextoCompletoMixin:
    """Descarga y guarda el artículo completo de la web (mixin de RSSService)."""

    def texto_completo_pendiente(self, article_id: str) -> bool:
        """True si nunca se ha intentado descargar el artículo completo de este artículo y
        tiene enlace (es lo que decide si el lector lo pide al abrirse)."""
        try:
            fila = db.query_one(
                "SELECT link, fulltext_at, enclosure_url FROM rss_articles WHERE id = ?", (article_id,))
        except Exception:
            return False
        return bool(fila and (fila[0] or fila[2]) and fila[1] is None)

    def descargar_articulo_completo(self, article_id: str, motor: Optional[str] = None,
                                    refrescar_sesion: bool = False) -> Optional[str]:
        """SÍNCRONO: llamar SIEMPRE desde un hilo de fondo. Descarga la página del artículo,
        extrae el cuerpo con el extractor elegido (motor=None: el de Preferencias) y, si es
        mejor que lo guardado, lo guarda como contenido del artículo.

        refrescar_sesion: vuelve a leer del navegador las cookies del medio suscrito (el
        usuario acaba de iniciar sesión en la web y pulsa "Descargar el artículo completo").

        Devuelve el HTML nuevo si se guardó, o None si no se obtuvo nada mejor (en ese caso
        se conserva el contenido del feed y se anota el intento para no repetirlo solo)."""
        fila = db.query_one(
            "SELECT link, title, author, COALESCE(content, ''), COALESCE(enclosure_url, ''), "
            "COALESCE(enclosure_type, '') FROM rss_articles WHERE id = ?", (article_id,))
        if not fila or not (fila[0] or fila[4]):
            return None
        link, titulo, autor, contenido, enclosure, tipo_enclosure = fila
        # Algunos feeds traen como "link" el GUID (p. ej. un UUID), no una dirección web:
        # descargarlo solo daba "Invalid URL ... No scheme supplied" en cada intento.
        if link and not str(link).strip().lower().startswith(("http://", "https://")):
            link = ""
        resultado = None
        # Hilos de foros (con sus comentarios) y vídeos de YouTube (descripción, capítulos y
        # transcripción): tienen su propio extractor. Se puede desactivar en Preferencias, RSS.
        from app.core.config_manager import config
        # Miniflux: el propio servidor extrae el artículo completo con sus reglas.
        html_srv = self.texto_completo_del_servidor(article_id)
        if html_srv and len(_texto_de(html_srv)) > len(_texto_de(contenido)) * _MEJORA_MINIMA:
            from .rss_fulltext import ArticuloExtraido
            resultado = ArticuloExtraido(html=html_srv, texto=_texto_de(html_srv), metodo="miniflux")
        if resultado is None and link and config.get("rss", "fulltext_sitios", True):
            from .rss_foros import extraer_por_sitio
            resultado = extraer_por_sitio(link, titulo or "")
        if resultado is None and link:
            resultado = FullTextExtractor.extraer_articulo(link, titulo or "", motor=motor,
                                                           refrescar_sesion=refrescar_sesion)
        # Podcasts sin notas en el feed ni en la web: las notas y los capítulos que lleva el
        # propio MP3 en sus etiquetas ID3 (se lee solo el principio del fichero).
        texto_actual = len(_texto_de(contenido))
        if enclosure and texto_actual < 400 and (
                resultado is None or len(resultado.texto) <= texto_actual * _MEJORA_MINIMA):
            from .rss_id3 import notas_para_articulo
            notas = notas_para_articulo(enclosure, tipo_enclosure)
            if notas:
                from .rss_fulltext import ArticuloExtraido
                unido = (contenido + "\n" + notas) if contenido.strip() else notas
                resultado = ArticuloExtraido(html=unido, texto=_texto_de(unido), metodo="id3")
        ahora = int(time.time())
        if resultado is None or len(resultado.texto) <= len(_texto_de(contenido)) * _MEJORA_MINIMA:
            try:
                db.execute("UPDATE rss_articles SET fulltext_at = ?, fulltext_metodo = ? WHERE id = ?",
                           (ahora, "", article_id))
            except Exception as e:
                log.debug(f"No se pudo anotar el intento de texto completo: {e}")
            return None
        try:
            with db.transaccion():
                db.execute(
                    "UPDATE rss_articles SET content = ?, fulltext_at = ?, fulltext_metodo = ? "
                    "WHERE id = ?", (resultado.html, ahora, resultado.metodo, article_id))
                db.execute("DELETE FROM rss_articles_fts WHERE article_db_id = ?", (article_id,))
                db.execute(
                    "INSERT INTO rss_articles_fts (article_db_id, title, content, author) "
                    "VALUES (?, ?, ?, ?)", (article_id, titulo, resultado.texto, autor))
        except Exception as e:
            log.error(f"Error al guardar el artículo completo en la base de datos: {e}")
            return None
        return resultado.html

    def descargar_articulo_completo_async(self, article_id: str,
                                          callback: Callable[[Optional[str], Optional[Exception]], None],
                                          motor: Optional[str] = None, forzar_sesion: bool = False):
        """Versión en segundo plano de descargar_articulo_completo. El callback recibe
        (html_nuevo_o_None, error) en el hilo de la interfaz."""
        def _bg(ctx):
            ctx.report_progress(50, "Descargando el artículo completo...")
            return self.descargar_articulo_completo(article_id, motor=motor,
                                                    refrescar_sesion=forzar_sesion)

        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def extraer_texto_completo_async(self, article_id: str, callback: Callable[[str, Optional[Exception]], None]):
        """Compatibilidad con la API anterior: el callback recibe el TEXTO plano del artículo
        completo ("" si no se consiguió nada mejor)."""
        def _fin(html_nuevo, error):
            callback(_texto_de(html_nuevo) if html_nuevo else "", error)

        self.descargar_articulo_completo_async(article_id, _fin)
