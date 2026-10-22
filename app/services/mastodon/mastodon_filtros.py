# -*- coding: utf-8 -*-
"""Filtros del SERVIDOR de Mastodon: listar, crear, editar y borrar (API v2, con respaldo a
la v1 en instancias antiguas) y filtrar publicaciones concretas (FilterStatus).

La v2 es la API actual (la v1 está obsoleta) y añade: un TÍTULO por filtro, VARIAS palabras
clave por filtro, publicaciones sueltas filtradas y la acción avisar / ocultar / difuminar la
multimedia. La decisión de qué hacer con cada toot filtrado está en filtros_servidor.py.

Mixin de MastodonService (separado de mastodon_social.py para mantener los archivos pequeños).
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from app.core.database import db
from app.core.task_manager import task_manager

log = logging.getLogger(__name__)


def _filtro_v2_a_dict(f: Dict[str, Any]) -> Dict[str, Any]:
    """Filtro v2 de la API -> dict que usan el diálogo y filtros_servidor."""
    keywords = [{"id": k.get("id"), "keyword": k.get("keyword") or "",
                 "whole_word": bool(k.get("whole_word"))}
                for k in (f.get("keywords") or [])]
    estados = [{"id": s.get("id"), "status_id": str(s.get("status_id") or "")}
               for s in (f.get("statuses") or []) if isinstance(s, dict)]
    return {
        "id": f.get("id"),
        "v2": True,
        "title": f.get("title") or "",
        # Resumen de las palabras para la lista. Un filtro SIN palabras (solo publicaciones
        # sueltas) se queda vacío: antes se ponía el título, y al editarlo el título acababa
        # convertido en palabra clave del filtro.
        "phrase": ", ".join(k["keyword"] for k in keywords),
        "keywords": keywords,
        "statuses": estados,
        "filter_action": f.get("filter_action") or "warn",
        "context": list(f.get("context") or []),
        "expires_at": str(f.get("expires_at") or ""),
        "whole_word": all(k["whole_word"] for k in keywords) if keywords else True,
    }


class _FiltrosMixin:

    def obtener_filtros_servidor_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = []
            try:
                for f in (self._api(client, "filters_v2")() or []):
                    res.append(_filtro_v2_a_dict(f))
                # Lista vigente para decidir ocultar/avisar en las listas (filtros_servidor.py).
                from app.services.mastodon.filtros_servidor import actualizar_vigentes
                actualizar_vigentes(account_id, res)
                return res
            except Exception as e:
                log.debug(f"Filtros v2 no disponibles ({e}); usando la API v1.")
            for f in (client.filters() or []):
                res.append({
                    "id": f.get("id"),
                    "v2": False,
                    "title": "",
                    "phrase": f.get("phrase", ""),
                    "keywords": [{"id": None, "keyword": f.get("phrase", ""),
                                  "whole_word": bool(f.get("whole_word"))}],
                    "statuses": [],
                    "filter_action": "warn",
                    "context": f.get("context", []),
                    "expires_at": str(f.get("expires_at") or ""),
                    "whole_word": bool(f.get("whole_word")),
                })
            return res
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # La VERSIÓN de la API de filtros se decide ANTES de mutar (auditoría A2): el patrón
    # antiguo (intentar v2 y, ante CUALQUIER excepción, repetir con v1) podía duplicar la
    # mutación si la respuesta v2 se perdía DESPUÉS de aplicarse (timeout, 429, 5xx).
    @staticmethod
    def _es_error_endpoint_inexistente(e) -> bool:
        """True solo si el error confirma que el endpoint NO existe o la versión no lo
        soporta (404/410/gone/version). Un timeout, 401, 429 o 5xx NO cuentan."""
        nombre = type(e).__name__.lower()
        if "notfound" in nombre or "version" in nombre:
            return True
        txt = str(e).lower()
        return "404" in txt or "410" in txt or "not found" in txt or "gone" in txt

    def _usar_filtros_v2(self, account_id: int, client, es_v2_hint=None) -> bool:
        """Decide qué versión de la API de filtros usar, SIN mutar nada: la pista del
        propio filtro (si se listó por v2/v1), la versión de la instancia cacheada
        (v2 = Mastodon 4.0+), o un sondeo de SOLO LECTURA. Si el sondeo falla por un
        error transitorio, se lanza (mejor abortar que mutar con la versión equivocada)."""
        if es_v2_hint is not None:
            return bool(es_v2_hint)
        if not hasattr(self, "_filtros_v2_cache"):
            self._filtros_v2_cache = {}
        if account_id in self._filtros_v2_cache:
            return self._filtros_v2_cache[account_id]
        caps = self.capacidades(account_id)
        if caps.get("version"):
            res = self._version_tupla(caps["version"]) >= (4, 0)
        else:
            try:
                self._api(client, "filters_v2")()   # lectura inofensiva
                res = True
            except Exception as e:
                if not self._es_error_endpoint_inexistente(e):
                    raise   # transitorio: no decidir en falso, abortar la operación
                res = False
        self._filtros_v2_cache[account_id] = res
        return res

    def soporta_filtro_blur(self, account_id: int):
        """True/False si se sabe si la instancia admite la acción 'blur' (Mastodon 4.4+);
        None si aún no se conoce la versión."""
        version = (self.capacidades(account_id) or {}).get("version")
        if not version:
            return None
        return self._version_tupla(version) >= (4, 4)

    def crear_filtro_servidor_async(self, account_id: int, phrase: str, contexts: list,
                                    expires_in=None, whole_word: bool = True,
                                    title: str = "", keywords: Optional[list] = None,
                                    filter_action: str = "warn", callback: Optional[Callable] = None):
        """Crea un filtro. Con la v2 admite varias palabras ('keywords', también NINGUNA: un
        filtro solo de publicaciones sueltas), título y acción (warn/hide/blur). En servidores
        antiguos usa la v1 (un filtro por palabra); la versión se decide antes de crear nada."""
        palabras = [p for p in (keywords if keywords is not None else [phrase]) if (p or "").strip()]

        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if self._usar_filtros_v2(account_id, client):
                atributos = [{"keyword": p.strip(), "whole_word": bool(whole_word)} for p in palabras]
                return self._api(client, "create_filter_v2")(
                    title or (palabras[0] if palabras else phrase), contexts,
                    filter_action or "warn", expires_in=expires_in, keywords_attributes=atributos)
            if not palabras:
                from app.core.errors import MastodonServiceError
                raise MastodonServiceError(
                    "Tu servidor solo admite filtros por palabra (API v1): indica al menos una.")
            # v1: un filtro por palabra (la v1 no agrupa). Si una palabra intermedia falla,
            # se registra hasta dónde llegó, para poder reparar a mano sin duplicar.
            ultimo = None
            creadas = []
            for p in palabras:
                try:
                    ultimo = client.filter_create(p.strip(), contexts, whole_word=bool(whole_word),
                                                  expires_in=expires_in or None)
                    creadas.append(p.strip())
                except Exception:
                    log.error(f"Creación de filtros v1 incompleta: creadas {len(creadas)} "
                              f"de {len(palabras)} palabras.")
                    raise
            return ultimo
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def borrar_filtro_servidor_async(self, account_id: int, filtro_id, es_v2: bool = None,
                                     callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if self._usar_filtros_v2(account_id, client, es_v2):
                return self._api(client, "delete_filter_v2")(filtro_id)
            return client.filter_delete(filtro_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def editar_filtro_servidor_async(self, account_id: int, filtro_id, phrase: str, contexts: list,
                                     expires_in=None, whole_word=True,
                                     title: str = "", keywords: Optional[list] = None,
                                     keywords_previas: Optional[list] = None,
                                     filter_action: str = "warn", es_v2: bool = None,
                                     callback: Optional[Callable] = None):
        """Edita un filtro.

        - expires_in: None = MANTENER la caducidad actual (Mastodon.py no envía los None);
          "" = quitarla (el servidor la borra con un valor vacío, Expireable#expires_in=);
          un número = caduca dentro de esos segundos.
        - En v2 sincroniza las palabras con construir_keywords_attributes: reenvía las
          existentes con su id (así 'solo palabras completas' se aplica también a ellas),
          renombra por id y borra con '_destroy' las que ya no están. whole_word=None conserva
          el de cada palabra. Se admiten CERO palabras (filtro solo de publicaciones).
        La versión de la API se decide antes de editar nada."""
        palabras = [p.strip() for p in (keywords if keywords is not None else [phrase]) if (p or "").strip()]

        def _bg(ctx):
            from app.services.mastodon.filtros_servidor import construir_keywords_attributes
            client = self.obtener_cliente(account_id)
            if self._usar_filtros_v2(account_id, client, es_v2):
                atributos = construir_keywords_attributes(palabras, keywords_previas, whole_word)
                return self._api(client, "update_filter_v2")(
                    filtro_id, title=title or (palabras[0] if palabras else None), context=contexts,
                    filter_action=filter_action or "warn", expires_in=expires_in,
                    keywords_attributes=atributos or None)
            if not palabras:
                from app.core.errors import MastodonServiceError
                raise MastodonServiceError(
                    "Tu servidor solo admite filtros por palabra (API v1): indica al menos una.")
            return self._api(client, "filter_update")(
                filtro_id, phrase=palabras[0], context=contexts,
                whole_word=bool(True if whole_word is None else whole_word), expires_in=expires_in)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # --- Publicaciones sueltas filtradas (FilterStatus, API v2) ---
    def filtrar_publicacion_async(self, account_id: int, filtro_id, status_db_id,
                                  callback: Optional[Callable] = None):
        """Añade una publicación a un filtro (POST /api/v2/filters/:id/statuses). Se usa SIEMPRE
        el id ORIGINAL (si la fila es un impulso, el del toot impulsado), como la web."""
        fila = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?",
                            (status_db_id,))

        def _bg(ctx):
            if not fila or not fila[0]:
                from app.core.errors import MastodonServiceError
                raise MastodonServiceError("No se encontró la publicación.")
            client = self.obtener_cliente(account_id)
            return self._api(client, "add_filter_status_v2")(filtro_id, fila[0])
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def crear_filtro_para_publicacion_async(self, account_id: int, titulo: str, status_db_id,
                                            callback: Optional[Callable] = None):
        """Crea un filtro nuevo SIN palabras (todos los contextos, acción avisar, como la web) y
        le añade la publicación. Dos llamadas seguidas en el mismo hilo de fondo."""
        fila = db.query_one("SELECT COALESCE(reblog_of_id, status_id) FROM mastodon_statuses WHERE id = ?",
                            (status_db_id,))

        def _bg(ctx):
            from app.core.errors import MastodonServiceError
            if not fila or not fila[0]:
                raise MastodonServiceError("No se encontró la publicación.")
            client = self.obtener_cliente(account_id)
            if not self._usar_filtros_v2(account_id, client):
                raise MastodonServiceError(
                    "Tu servidor no admite filtrar publicaciones sueltas (necesita Mastodon 4.0).")
            nuevo = self._api(client, "create_filter_v2")(
                titulo, ["home", "notifications", "public", "thread", "account"], "warn",
                keywords_attributes=[])
            self._api(client, "add_filter_status_v2")(nuevo.get("id"), fila[0])
            return nuevo
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def publicaciones_de_filtro_async(self, account_id: int, filtro_id,
                                      callback: Optional[Callable] = None):
        """Publicaciones sueltas de un filtro: [{id (del FilterStatus), status_id, texto}]. El
        texto sale de la caché local; las que no están se piden al servidor en lote."""
        def _bg(ctx):
            from app.core.html_utils import html_a_texto
            client = self.obtener_cliente(account_id)
            res = []
            for fs in (self._api(client, "filter_statuses_v2")(filtro_id) or []):
                res.append({"id": fs.get("id"), "status_id": str(fs.get("status_id") or ""), "texto": ""})
            faltan = []
            for it in res:
                fila = db.query_one(
                    "SELECT author_username, content FROM mastodon_statuses WHERE account_id = ? "
                    "AND (status_id = ? OR reblog_of_id = ?) LIMIT 1",
                    (account_id, it["status_id"], it["status_id"]))
                if fila:
                    it["texto"] = "{0}: {1}".format(fila[0] or "", (fila[1] or "").replace("\n", " "))
                else:
                    faltan.append(it)
            if faltan:
                por_id = {}
                try:
                    ids = [it["status_id"] for it in faltan][:40]
                    for i in range(0, len(ids), 20):
                        for st in (client.statuses(ids[i:i + 20]) or []):
                            por_id[str(st.get("id"))] = st
                except Exception as e:
                    log.debug(f"No se pudo obtener el texto de las publicaciones filtradas: {e}")
                for it in faltan:
                    st = por_id.get(it["status_id"])
                    if st:
                        acct = (st.get("account") or {}).get("acct") or ""
                        it["texto"] = "@{0}: {1}".format(acct, html_a_texto(st.get("content") or ""))
            return res
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def quitar_publicacion_de_filtro_async(self, account_id: int, filter_status_id,
                                           callback: Optional[Callable] = None):
        """Quita una publicación suelta de su filtro (DELETE /api/v2/filters/statuses/:id)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "delete_filter_status_v2")(filter_status_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)


__all__: List[str] = ["_FiltrosMixin", "_filtro_v2_a_dict"]
