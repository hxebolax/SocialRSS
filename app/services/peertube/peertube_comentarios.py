# -*- coding: utf-8 -*-
"""Comentarios de vídeos (Fase 4 de PeerTube).

Mixin de `PeerTubeService`. Los comentarios de PeerTube son un ÁRBOL: hilos de primer nivel
(`comment-threads`) y, dentro de cada uno, un árbol de respuestas (`VideoCommentThreadTree`). Aquí se
listan los hilos, se carga un hilo aplanado con su nivel de profundidad (para un árbol accesible), y
se publica/responde/borra. El texto HTML se convierte a texto plano legible por el lector.
"""
import re
import logging
from typing import Any, Dict, List
from urllib.parse import urlsplit

from app.core.config_manager import config
from app.services.peertube.peertube_common import _g

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _html_a_texto(html: str) -> str:
    """Convierte el HTML de un comentario a texto plano (quita etiquetas y colapsa espacios)."""
    if not html:
        return ""
    texto = html.replace("</p>", "\n").replace("<br>", "\n").replace("<br/>", "\n")
    texto = _TAG_RE.sub("", texto)
    texto = (texto.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"[ \t]+", " ", texto).strip()


class _ComentariosMixin:
    def _comentario_a_dict(self, c: dict, nivel: int = 0) -> Dict[str, Any]:
        """Normaliza un comentario de la API a un dict estable para la UI."""
        cuenta = _g(c, "account") or {}
        nombre = _g(cuenta, "name") or ""
        host = _g(cuenta, "host") or ""
        return {"id": _g(c, "id"),
                "texto": _html_a_texto(_g(c, "text") or ""),
                "autor": f"{nombre}@{host}" if host else nombre,
                "autor_display": _g(cuenta, "displayName") or nombre,
                "fecha": _g(c, "createdAt") or "",
                "total_replies": int(_g(c, "totalReplies", default=0) or 0),
                "borrado": bool(_g(c, "isDeleted", default=False)),
                "nivel": nivel}

    def listar_hilos(self, account_id: int, video_id_or_uuid: str, *,
                     limite: int = 25) -> List[Dict[str, Any]]:
        """Lista los hilos de primer nivel de un vídeo (con su número de respuestas)."""
        def _fn(cliente):
            data, _total = cliente.pagina(f"videos/{video_id_or_uuid}/comment-threads",
                                          params={"sort": "-createdAt"}, start=0, count=limite,
                                          auth=True)
            return [self._comentario_a_dict(c, 0) for c in (data or [])]
        return self._ejecutar(account_id, _fn)

    def cargar_hilo(self, account_id: int, video_id_or_uuid: str,
                    thread_id: Any) -> List[Dict[str, Any]]:
        """Carga un hilo completo y lo devuelve APLANADO en orden de lectura, con el nivel de cada
        comentario (para un árbol accesible)."""
        def _fn(cliente):
            datos = cliente.get(f"videos/{video_id_or_uuid}/comment-threads/{thread_id}", auth=True)
            resultado: List[Dict[str, Any]] = []
            self._aplanar_arbol(datos, 0, resultado)
            return resultado
        return self._ejecutar(account_id, _fn)

    def _aplanar_arbol(self, nodo: Any, nivel: int, salida: List[Dict[str, Any]]) -> None:
        """Recorre un `VideoCommentThreadTree` en profundidad, añadiendo (comentario, nivel)."""
        if not isinstance(nodo, dict):
            return
        comentario = nodo.get("comment")
        if comentario:
            salida.append(self._comentario_a_dict(comentario, nivel))
        for hijo in (nodo.get("children") or []):
            self._aplanar_arbol(hijo, nivel + 1, salida)

    def publicar_comentario(self, account_id: int, video_id_or_uuid: str, texto: str) -> Dict[str, Any]:
        """Publica un comentario de primer nivel (nuevo hilo). Devuelve el comentario creado."""
        def _fn(cliente):
            datos = cliente.post(f"videos/{video_id_or_uuid}/comment-threads",
                                 json_body={"text": texto}, auth=True)
            c = (datos.get("comment") if isinstance(datos, dict) else None) or datos
            return self._comentario_a_dict(c or {}, 0)
        return self._ejecutar(account_id, _fn)

    def responder_comentario(self, account_id: int, video_id_or_uuid: str, comment_id: Any,
                             texto: str) -> Dict[str, Any]:
        """Responde a un comentario existente."""
        def _fn(cliente):
            datos = cliente.post(f"videos/{video_id_or_uuid}/comments/{comment_id}",
                                 json_body={"text": texto}, auth=True)
            c = (datos.get("comment") if isinstance(datos, dict) else None) or datos
            return self._comentario_a_dict(c or {}, 0)
        return self._ejecutar(account_id, _fn)

    def borrar_comentario(self, account_id: int, video_id_or_uuid: str, comment_id: Any) -> bool:
        """Borra un comentario (propio, o cualquiera si eres moderador del vídeo)."""
        def _fn(cliente):
            cliente.delete(f"videos/{video_id_or_uuid}/comments/{comment_id}", auth=True)
            return True
        return bool(self._ejecutar(account_id, _fn))
