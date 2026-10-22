# -*- coding: utf-8 -*-
"""
Módulo para el motor de reglas y filtros automáticos de artículos RSS.
Ejecuta acciones (marcar leído, sonar alerta, etiquetar) según el contenido de los nuevos artículos.
"""

import re
import logging
from typing import List, Dict, Any, Optional
from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak

log = logging.getLogger(__name__)

# Artículos a los que una regla quitó el aviso de "artículos nuevos" (acción no_avisar). Lo
# consulta la interfaz al anunciar las novedades del sondeo; acotado para no crecer sin fin.
SIN_AVISO: set = set()
_MAX_SIN_AVISO = 5000

# Operadores de una condición. Sin "op" (reglas antiguas) vale el comportamiento de siempre:
# expresión regular si es válida y, si no, "contiene".
OPERADORES = ("contiene", "no_contiene", "igual", "empieza", "regex")
# Acciones que "Aplicar a los artículos que ya tengo" ejecuta (las de aviso y sonido no, para
# no llenar de avisos al aplicarlas a cientos de artículos antiguos).
ACCIONES_PERSISTENTES = ("mark_read", "mark_favorite", "read_later", "tag", "categorize", "delete")


def _tokens(expr: str):
    import re as _re
    for t in _re.findall(r"\d+|\(|\)|[^\s()\d]+", (expr or "").lower()):
        if t in ("y", "and", "&", "&&"):
            yield "y"
        elif t in ("o", "or", "|", "||"):
            yield "o"
        elif t in ("no", "not", "!"):
            yield "no"
        else:
            yield t


def evaluar_expresion(expr: str, valores: List[bool]) -> bool:
    """Evalúa una combinación como "(1 y 2) o no 3" con los resultados de las condiciones
    (numeradas desde 1). "no" pesa más que "y", y "y" más que "o". Lanza ValueError si la
    expresión está mal escrita o nombra una condición que no existe."""
    toks = list(_tokens(expr))
    pos = [0]

    def ver():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def tomar():
        t = ver()
        pos[0] += 1
        return t

    def primario():
        t = tomar()
        if t == "no":
            return not primario()
        if t == "(":
            v = o_()
            if tomar() != ")":
                raise ValueError(_("Falta cerrar un paréntesis."))
            return v
        if t and t.isdigit() and 1 <= int(t) <= len(valores):
            return bool(valores[int(t) - 1])
        raise ValueError(_("«{0}» no es una condición válida.").format(t or ""))

    def y_():
        v = primario()
        while ver() == "y":
            tomar()
            v = primario() and v
        return v

    def o_():
        v = y_()
        while ver() == "o":
            tomar()
            v = y_() or v
        return v

    resultado = o_()
    if ver() is not None:
        raise ValueError(_("Sobra «{0}» en la combinación.").format(ver()))
    return resultado


class RSSRulesEngine:
    """Motor de evaluación y ejecución de reglas automáticas para RSS."""

    # Plan de reglas COMPILADO por cuenta, con caché breve (auditoría RSS-17): antes cada
    # artículo recargaba TODAS las reglas de la base y ejecutaba re.search desde el patrón
    # sin compilar. Ahora la carga y la compilación se hacen una vez por cuenta y ciclo.
    _plan_cache: Dict[int, tuple] = {}
    _PLAN_TTL = 120.0

    @staticmethod
    def cargar_reglas(account_id: int) -> List[Dict[str, Any]]:
        """Recupera todas las reglas activas de una cuenta, ordenadas por PRIORIDAD
        (mayor primero). Incluye los campos avanzados (condiciones múltiples Y/O,
        ámbito por feed/carpeta, varias acciones y detener procesamiento)."""
        import json
        sql = """
        SELECT id, account_id, title, pattern, field, action, action_value, active,
               COALESCE(conditions_json,''), COALESCE(match_mode,'all'),
               COALESCE(priority,0), COALESCE(stop_processing,0),
               scope_feed_id, scope_category_id, COALESCE(actions_json,'')
        FROM rss_rules
        WHERE account_id = ? AND active = 1
        ORDER BY COALESCE(priority,0) DESC, id
        """
        reglas = []
        try:
            rows = db.query(sql, (account_id,))
            for r in rows:
                condiciones = []
                if r[8]:
                    try:
                        condiciones = json.loads(r[8]) or []
                    except Exception:
                        condiciones = []
                acciones = []
                if r[14]:
                    try:
                        acciones = json.loads(r[14]) or []
                    except Exception:
                        acciones = []
                reglas.append({
                    "id": r[0],
                    "account_id": r[1],
                    "title": r[2],
                    "pattern": r[3],
                    "field": r[4],
                    "action": r[5],
                    "action_value": r[6],
                    "active": bool(r[7]),
                    "conditions": condiciones,
                    "match_mode": (r[9] or "all"),
                    "priority": r[10],
                    "stop_processing": bool(r[11]),
                    "scope_feed_id": r[12],
                    "scope_category_id": r[13],
                    "actions": acciones,
                })
        except Exception as e:
            log.error(f"Error al cargar reglas RSS de base de datos: {e}")
        return reglas

    @classmethod
    def invalidar_plan(cls, account_id: int = None):
        """Descarta el plan cacheado (llamar al crear/editar/borrar reglas)."""
        if account_id is None:
            cls._plan_cache.clear()
        else:
            cls._plan_cache.pop(account_id, None)

    @staticmethod
    def _compilar_condicion(field: str, pattern: str, es_regex_posible: bool = True,
                            op: str = None) -> dict:
        """Compila una condición (campo + operador + patrón). Sin operador: regex si es
        válida, subcadena si no (reglas de siempre)."""
        compilada = None
        if op == "regex" or (op is None and es_regex_posible):
            try:
                compilada = re.compile(pattern or "", re.IGNORECASE)
            except re.error:
                compilada = None
        return {"field": field or "all", "pattern": pattern or "", "op": op,
                "patron_lower": (pattern or "").lower().strip(), "compilada": compilada}

    @classmethod
    def plan(cls, account_id: int) -> List[Dict[str, Any]]:
        """Reglas activas de la cuenta con sus CONDICIONES ya compiladas (regex válida o
        subcadena). Una regla simple (pattern/field, las de siempre) se convierte en una
        condición única; las avanzadas traen varias con modo 'all' (Y) o 'any' (O).
        Cacheado unos segundos para el ciclo de sondeo."""
        import time as _t
        en_cache = cls._plan_cache.get(account_id)
        if en_cache and (_t.time() - en_cache[1]) < cls._PLAN_TTL:
            return en_cache[0]
        plan = []
        for regla in cls.cargar_reglas(account_id):
            if regla.get("conditions"):
                regla["cond_compiladas"] = [
                    cls._compilar_condicion(c.get("field", "all"), c.get("pattern", ""),
                                            bool(c.get("is_regex", True)),
                                            c.get("op") if c.get("op") in OPERADORES else None)
                    for c in regla["conditions"] if (c.get("pattern") or "").strip()]
            else:
                regla["cond_compiladas"] = [
                    cls._compilar_condicion(regla.get("field") or "all",
                                            regla.get("pattern") or "")]
            # Compatibilidad con código que aún mire estos campos derivados.
            regla["compilada"] = regla["cond_compiladas"][0]["compilada"] if regla["cond_compiladas"] else None
            regla["patron_lower"] = (regla.get("pattern") or "").lower()
            plan.append(regla)
        cls._plan_cache[account_id] = (plan, _t.time())
        return plan

    _titulos_feed: Dict[int, str] = {}

    @classmethod
    def _titulo_feed(cls, feed_id) -> str:
        if feed_id not in cls._titulos_feed:
            fila = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (feed_id,))
            cls._titulos_feed[feed_id] = (fila[0] if fila else "") or ""
        return cls._titulos_feed[feed_id]

    @classmethod
    def _texto_de_campo(cls, article, campo: str) -> str:
        if campo == "title":
            return getattr(article, "title", "") or ""
        if campo == "content":
            return getattr(article, "content", "") or ""
        if campo == "author":
            return getattr(article, "author", "") or ""
        if campo == "link":
            return getattr(article, "link", "") or ""
        if campo == "feed":
            return cls._titulo_feed(getattr(article, "feed_id", None))
        return f"{getattr(article, 'title', '')} {getattr(article, 'content', '')} {getattr(article, 'author', '') or ''}"

    @staticmethod
    def _cumple(c: dict, texto: str) -> bool:
        op = c.get("op")
        bajo = (texto or "").lower()
        if op == "no_contiene":
            return c["patron_lower"] not in bajo
        if not texto:
            return False
        if op == "contiene":
            return c["patron_lower"] in bajo
        if op == "igual":
            return bajo.strip() == c["patron_lower"]
        if op == "empieza":
            return bajo.lstrip().startswith(c["patron_lower"])
        if c["compilada"] is not None:
            return c["compilada"].search(texto) is not None
        return c["patron_lower"] in bajo

    @classmethod
    def _regla_coincide(cls, regla: dict, article) -> bool:
        """Evalúa las condiciones compiladas de una regla contra el artículo, con el
        ÁMBITO por feed/carpeta si la regla lo tiene."""
        if regla.get("scope_feed_id") and getattr(article, "feed_id", None) != regla["scope_feed_id"]:
            return False
        if regla.get("scope_category_id"):
            r = db.query_one("SELECT category_id FROM rss_feeds WHERE id = ?",
                             (getattr(article, "feed_id", None),))
            if not r or r[0] != regla["scope_category_id"]:
                return False
        condiciones = regla.get("cond_compiladas") or []
        if not condiciones:
            return False
        resultados = [cls._cumple(c, cls._texto_de_campo(article, c["field"])) for c in condiciones]
        modo = regla.get("match_mode") or "all"
        if modo.startswith("expr:"):
            # Combinación libre, p. ej. "(1 y 2) o 3". Si está mal (no debería: se valida al
            # guardar), la regla no coincide en vez de hacer algo inesperado.
            try:
                return evaluar_expresion(modo[5:], resultados)
            except ValueError:
                return False
        return any(resultados) if modo == "any" else all(resultados)

    @classmethod
    def simular(cls, account_id: int, limit: int = 300) -> List[Dict[str, Any]]:
        """PRUEBA las reglas contra los últimos artículos guardados SIN ejecutar ninguna
        acción (auditoría, prioridad A: vista previa de reglas). Devuelve, por regla, los
        títulos que coincidirían."""
        cls.invalidar_plan(account_id)
        plan = cls.plan(account_id)
        if not plan:
            return []

        class _Art:
            pass

        filas = db.query(
            "SELECT id, feed_id, title, COALESCE(content,''), COALESCE(author,''), COALESCE(link,'') "
            "FROM rss_articles WHERE account_id = ? AND COALESCE(deleted,0) = 0 "
            "ORDER BY published_at DESC LIMIT ?", (account_id, limit))
        resultado = [{"regla": r["title"], "coincidencias": []} for r in plan]
        for fila in filas:
            art = _Art()
            art.id, art.feed_id, art.title, art.content, art.author, art.link = fila
            for i, regla in enumerate(plan):
                if cls._regla_coincide(regla, art):
                    if len(resultado[i]["coincidencias"]) < 50:
                        resultado[i]["coincidencias"].append(art.title or "")
                    if regla.get("stop_processing"):
                        break
        return resultado

    @classmethod
    def aplicar_a_existentes(cls, account_id: int, limite: int = 5000,
                             regla_id: Optional[int] = None) -> int:
        """Aplica las reglas (o solo una) a los artículos YA guardados, no solo a los nuevos.
        Solo las acciones que cambian algo guardado (ACCIONES_PERSISTENTES): los sonidos y
        avisos por voz no, para no llenar de avisos. Devuelve cuántos artículos coincidieron."""
        cls.invalidar_plan(account_id)
        plan = [r for r in cls.plan(account_id) if regla_id is None or r["id"] == regla_id]
        if not plan:
            return 0

        class _Art:
            pass
        filas = db.query(
            "SELECT id, feed_id, title, COALESCE(content,''), COALESCE(author,''), COALESCE(link,'') "
            "FROM rss_articles WHERE account_id = ? AND COALESCE(deleted,0) = 0 "
            "ORDER BY published_at DESC LIMIT ?", (account_id, limite))
        afectados = 0
        with db.transaccion():
            for fila in filas:
                art = _Art()
                art.id, art.feed_id, art.title, art.content, art.author, art.link = fila
                coincidio = False
                for regla in plan:
                    if not cls._regla_coincide(regla, art):
                        continue
                    coincidio = True
                    acciones = regla.get("actions") or [
                        {"action": regla["action"], "value": regla["action_value"]}]
                    for a in acciones:
                        if a.get("action") in ACCIONES_PERSISTENTES:
                            cls._ejecutar_accion(art, a.get("action"), a.get("value"))
                    if regla.get("stop_processing"):
                        break
                afectados += 1 if coincidio else 0
        cls._titulos_feed.clear()
        return afectados

    @classmethod
    def evaluar_articulo(cls, account_id: int, article: Any, plan: Optional[list] = None) -> bool:
        """
        Evalúa un artículo contra las reglas activas de la cuenta.
        Aplica las acciones correspondientes a las reglas coincidentes.
        `plan`: reglas ya compiladas (de cls.plan()); si no se pasa, se obtiene (cacheado).
        Devuelve True si coincidió con alguna regla.
        """
        reglas = plan if plan is not None else cls.plan(account_id)
        if not reglas:
            return False

        coincidencia_general = False

        for regla in reglas:
            if not cls._regla_coincide(regla, article):
                continue
            log.debug(f"Artículo '{getattr(article, 'title', '')}' coincide con la regla: '{regla['title']}'")
            coincidencia_general = True
            # Ejecutar TODAS las acciones de la regla: las avanzadas traen una lista en
            # 'actions'; las simples, la pareja action/action_value de siempre.
            acciones = regla.get("actions") or [
                {"action": regla["action"], "value": regla["action_value"]}]
            for a in acciones:
                cls._ejecutar_accion(article, a.get("action"), a.get("value"))
            # Prioridad + detener: una regla puede cortar la cadena para las siguientes.
            if regla.get("stop_processing"):
                break

        return coincidencia_general

    @classmethod
    def _ejecutar_accion(cls, article: Any, action: str, action_value: Optional[str]):
        """Ejecuta una acción concreta sobre el artículo y base de datos."""
        try:
            if action == "mark_read":
                # Cambiar estado a leído en la base de datos
                db.execute("UPDATE rss_articles SET read = 1 WHERE id = ?", (article.id,))
                article.read = True
                log.debug(f"Acción ejecutada: Marcar como leído {article.id}")
                
            elif action == "mark_favorite":
                db.execute("UPDATE rss_articles SET favorite = 1 WHERE id = ?", (article.id,))
                article.favorite = True
                log.debug(f"Acción ejecutada: Marcar como favorito {article.id}")

            elif action == "read_later":
                db.execute("UPDATE rss_articles SET read_later = 1 WHERE id = ?", (article.id,))
                try:
                    article.read_later = True
                except Exception:
                    pass
                log.debug(f"Acción ejecutada: Leer más tarde {article.id}")
                
            elif action == "play_sound":
                # Reproducir un sonido por evento
                sonido = action_value or "ready"
                event_bus.emit("sound_trigger", sonido)
                log.debug(f"Acción ejecutada: Reproducir sonido {sonido}")
                
            elif action == "notify":
                # Verbalizar
                msg = action_value or "Regla RSS coincidente"
                speak(f"{msg}: {article.title}")
                log.debug(f"Acción ejecutada: Notificación verbal '{msg}'")
                
            elif action == "categorize":
                # Cambiar de categoría el feed en la DB
                if action_value:
                    try:
                        cat_id = int(action_value)
                        db.execute("UPDATE rss_feeds SET category_id = ? WHERE id = ?", (cat_id, article.feed_id))
                        log.debug(f"Acción ejecutada: Feed {article.feed_id} recategorizado a {cat_id}")
                    except ValueError:
                        pass

            elif action == "delete":
                # A la PAPELERA (se puede recuperar), no borrado definitivo.
                from datetime import datetime as _dt
                db.execute("UPDATE rss_articles SET deleted = 1, deleted_at = ? WHERE id = ?",
                           (_dt.now().isoformat(), article.id))

            elif action == "no_avisar":
                if len(SIN_AVISO) > _MAX_SIN_AVISO:
                    SIN_AVISO.clear()
                SIN_AVISO.add(article.id)

            elif action == "tag":
                # ETIQUETAR el ARTÍCULO (no el feed) con la etiqueta indicada (auditoría,
                # prioridad B): a diferencia de 'categorize', no mueve el feed entero.
                if action_value:
                    try:
                        tag_id = int(action_value)
                        db.execute("INSERT OR IGNORE INTO rss_article_tags (tag_id, article_id) "
                                   "VALUES (?, ?)", (tag_id, article.id))
                        log.debug(f"Acción ejecutada: artículo {article.id} etiquetado con {tag_id}")
                    except ValueError:
                        pass
        except Exception as e:
            log.error(f"Error al ejecutar acción '{action}' de regla RSS: {e}")
