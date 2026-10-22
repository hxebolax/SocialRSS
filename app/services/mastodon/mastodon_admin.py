# -*- coding: utf-8 -*-
"""Herramientas de administración de instancia de Mastodon (mixin de MastodonService).

Solo tienen efecto si la cuenta es administradora/moderadora de su instancia. La UI que las usa se
muestra únicamente cuando el usuario activa la opción en Preferencias -> Mastodon.
"""
import logging
from datetime import datetime
from typing import Optional, Callable

from app.core.html_utils import html_a_texto
from app.core.task_manager import task_manager

log = logging.getLogger(__name__)


def _valor(obj, clave, defecto=None):
    """Lee respuestas dict/AttribAccessDict sin acoplar la UI a Mastodon.py."""
    if obj is None:
        return defecto
    try:
        return obj.get(clave, defecto)
    except (AttributeError, TypeError):
        return getattr(obj, clave, defecto)


def _fecha(valor) -> str:
    if not valor:
        return ""
    if isinstance(valor, datetime):
        return valor.astimezone().strftime("%d/%m/%Y %H:%M")
    return str(valor).replace("T", " ")[:19]


def _acct(obj) -> str:
    """Obtiene un acct tanto de Account como de AdminAccount."""
    if not obj:
        return ""
    cuenta = _valor(obj, "account") or obj
    acct = _valor(cuenta, "acct") or _valor(cuenta, "username") or _valor(obj, "username") or ""
    dominio = _valor(obj, "domain") or ""
    if dominio and "@" not in acct:
        acct = f"{acct}@{dominio}"
    return acct.lstrip("@")


def _rol(obj) -> str:
    rol = _valor(obj, "role")
    if isinstance(rol, dict):
        return rol.get("name") or rol.get("id") or ""
    return str(rol or "")


def _estado_cuenta(obj) -> str:
    if _valor(obj, "suspended", False):
        return "suspended"
    if _valor(obj, "silenced", False):
        return "silenced"
    if _valor(obj, "disabled", False):
        return "disabled"
    if _valor(obj, "sensitized", False):
        return "sensitive"
    if not _valor(obj, "approved", True):
        return "pending"
    if not _valor(obj, "confirmed", True):
        return "unconfirmed"
    return "active"


def _serializar_cuenta_admin(obj) -> dict:
    cuenta = _valor(obj, "account") or {}
    ips = []
    for ip in (_valor(obj, "ips") or []):
        ips.append({"ip": _valor(ip, "ip") or "", "usada": _fecha(_valor(ip, "used_at"))})
    campos = []
    for campo in (_valor(cuenta, "fields") or []):
        campos.append({"nombre": _valor(campo, "name") or "",
                       "valor": html_a_texto(_valor(campo, "value") or "")})
    return {
        "id": str(_valor(obj, "id") or _valor(cuenta, "id") or ""),
        "acct": _acct(obj),
        "usuario": _valor(obj, "username") or _valor(cuenta, "username") or "",
        "dominio": _valor(obj, "domain") or "",
        "nombre": _valor(cuenta, "display_name") or "",
        "nota": html_a_texto(_valor(cuenta, "note") or ""),
        "email": _valor(obj, "email") or "",
        "ip": str(_valor(obj, "ip") or ""),
        "ips": ips,
        "rol": _rol(obj),
        "estado": _estado_cuenta(obj),
        "confirmado": bool(_valor(obj, "confirmed", False)),
        "aprobado": bool(_valor(obj, "approved", False)),
        "suspendido": bool(_valor(obj, "suspended", False)),
        "silenciado": bool(_valor(obj, "silenced", False)),
        "desactivado": bool(_valor(obj, "disabled", False)),
        "sensible": bool(_valor(obj, "sensitized", False)),
        "local": not bool(_valor(obj, "domain")),
        "locale": _valor(obj, "locale") or "",
        "solicitud": _valor(obj, "invite_request") or "",
        "creado": _fecha(_valor(obj, "created_at") or _valor(cuenta, "created_at")),
        "ultima_publicacion": _fecha(_valor(cuenta, "last_status_at")),
        "publicaciones": int(_valor(cuenta, "statuses_count", 0) or 0),
        "seguidores": int(_valor(cuenta, "followers_count", 0) or 0),
        "seguidos": int(_valor(cuenta, "following_count", 0) or 0),
        "url": _valor(cuenta, "url") or "",
        "campos": campos,
        "aplicacion_id": str(_valor(obj, "created_by_application_id") or ""),
        "invitado_por": str(_valor(obj, "invited_by_account_id") or ""),
    }


def _serializar_estado_reportado(obj) -> dict:
    autor = _valor(obj, "account") or {}
    return {
        "id": str(_valor(obj, "id") or ""),
        "autor": _acct(autor),
        "contenido": html_a_texto(_valor(obj, "content") or ""),
        "aviso": _valor(obj, "spoiler_text") or "",
        "fecha": _fecha(_valor(obj, "created_at")),
        "url": _valor(obj, "url") or _valor(obj, "uri") or "",
        "sensible": bool(_valor(obj, "sensitive", False)),
        "adjuntos": len(_valor(obj, "media_attachments") or []),
    }


def _serializar_informe(obj) -> dict:
    reporta = _valor(obj, "account") or {}
    objetivo = _valor(obj, "target_account") or {}
    asignado = _valor(obj, "assigned_account")
    resuelto_por = _valor(obj, "action_taken_by_account")
    reglas = []
    for regla in (_valor(obj, "rules") or []):
        reglas.append({"id": str(_valor(regla, "id") or ""),
                       "texto": _valor(regla, "text") or ""})
    estados = [_serializar_estado_reportado(s) for s in (_valor(obj, "statuses") or [])]
    return {
        "id": str(_valor(obj, "id") or ""),
        "categoria": _valor(obj, "category") or "other",
        "comentario": _valor(obj, "comment") or "",
        "reporta": _acct(reporta),
        "reporta_id": str(_valor(reporta, "id") or ""),
        "objetivo": _acct(objetivo),
        "objetivo_id": str(_valor(objetivo, "id") or ""),
        "objetivo_nombre": _valor(objetivo, "display_name") or "",
        "objetivo_nota": html_a_texto(_valor(objetivo, "note") or ""),
        "objetivo_url": _valor(objetivo, "url") or "",
        "objetivo_publicaciones": int(_valor(objetivo, "statuses_count", 0) or 0),
        "objetivo_seguidores": int(_valor(objetivo, "followers_count", 0) or 0),
        "objetivo_seguidos": int(_valor(objetivo, "following_count", 0) or 0),
        "creado": _fecha(_valor(obj, "created_at")),
        "actualizado": _fecha(_valor(obj, "updated_at")),
        "resuelto": bool(_valor(obj, "action_taken", False)),
        "resuelto_en": _fecha(_valor(obj, "action_taken_at")),
        "asignado": _acct(asignado),
        "resuelto_por": _acct(resuelto_por),
        "reenviado": _valor(obj, "forwarded"),
        "reglas": reglas,
        "estados": estados,
        "aplicacion": _valor(_valor(obj, "application") or {}, "name") or "",
    }


class _AdminMixin:
    # ---------------- INFORMES (reports) ----------------
    def admin_listar_informes_async(self, account_id: int, resueltos: bool = False,
                                    callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_reports")(resolved=resueltos, limit=50)
            informes = []
            for r in (res or []):
                objetivo = (r.get("target_account") or {}).get("account") or r.get("target_account") or {}
                informes.append({
                    "id": r.get("id"),
                    "categoria": r.get("category") or "",
                    "comentario": (r.get("comment") or "")[:200],
                    "objetivo": (objetivo or {}).get("acct") or "",
                    "resuelto": bool(r.get("action_taken")),
                })
            return informes
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_accion_informe_async(self, account_id: int, informe_id, accion: str,
                                   callback: Optional[Callable] = None):
        """accion: 'resolver', 'reabrir', 'asignar', 'desasignar'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if accion == "resolver":
                return self._api(client, "admin_report_resolve")(informe_id)
            if accion == "reabrir":
                return self._api(client, "admin_report_reopen")(informe_id)
            if accion == "asignar":
                return self._api(client, "admin_report_assign")(informe_id)
            if accion == "desasignar":
                return self._api(client, "admin_report_unassign")(informe_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # ---------------- CUENTAS ----------------
    def admin_listar_cuentas_async(self, account_id: int, estado: str = "active", remote: bool = False,
                                   callback: Optional[Callable] = None):
        """estado: 'active', 'pending', 'disabled', 'silenced', 'suspended'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_accounts")(status=estado, remote=remote, limit=50)
            cuentas = []
            for a in (res or []):
                cu = a.get("account") or {}
                cuentas.append({
                    "id": a.get("id"),
                    "usuario": cu.get("acct") or cu.get("username") or a.get("username") or "",
                    "email": a.get("email") or "",
                    "rol": (a.get("role") or {}).get("name") if isinstance(a.get("role"), dict) else (a.get("role") or ""),
                    "confirmado": bool(a.get("confirmed")),
                    "desactivado": bool(a.get("disabled")),
                    "silenciado": bool(a.get("silenced")),
                    "suspendido": bool(a.get("suspended")),
                })
            return cuentas
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_accion_cuenta_async(self, account_id: int, cuenta_id, accion: str,
                                  callback: Optional[Callable] = None):
        """accion: 'aprobar','rechazar','suspender','silenciar','desactivar','reactivar',
        'quitar_suspension','quitar_silencio','marcar_no_sensible'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if accion == "aprobar":
                return self._api(client, "admin_account_approve")(cuenta_id)
            if accion == "rechazar":
                return self._api(client, "admin_account_reject")(cuenta_id)
            if accion == "suspender":
                return self._api(client, "admin_account_moderate")(cuenta_id, action="suspend")
            if accion == "silenciar":
                return self._api(client, "admin_account_moderate")(cuenta_id, action="silence")
            if accion == "desactivar":
                return self._api(client, "admin_account_moderate")(cuenta_id, action="disable")
            if accion == "reactivar":
                return self._api(client, "admin_account_enable")(cuenta_id)
            if accion == "quitar_suspension":
                return self._api(client, "admin_account_unsuspend")(cuenta_id)
            if accion == "quitar_silencio":
                return self._api(client, "admin_account_unsilence")(cuenta_id)
            if accion == "marcar_no_sensible":
                return self._api(client, "admin_account_unsensitive")(cuenta_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # ---------------- DOMINIOS ----------------
    def admin_listar_dominios_bloqueados_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_domain_blocks")()
            return [{"id": d.get("id"), "dominio": d.get("domain") or "",
                     "severidad": d.get("severity") or ""} for d in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_bloquear_dominio_async(self, account_id: int, dominio: str, severidad: str = "silence",
                                     callback: Optional[Callable] = None):
        """severidad: 'silence', 'suspend' o 'noop'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_create_domain_block")(dominio, severity=severidad)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_desbloquear_dominio_async(self, account_id: int, bloqueo_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_delete_domain_block")(bloqueo_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_permitir_dominio_async(self, account_id: int, dominio: str, callback: Optional[Callable] = None):
        """Lista blanca de dominios (solo en modo allowlist)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_create_domain_allow")(dominio)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_listar_dominios_permitidos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Dominios PERMITIDOS (lista blanca de federación, cuando la instancia va en modo allowlist)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_domain_allows")()
            return [{"id": d.get("id"), "dominio": d.get("domain") or ""} for d in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_quitar_dominio_permitido_async(self, account_id: int, permiso_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_delete_domain_allow")(permiso_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # ---------------- BLOQUEOS DE EMAIL CANÓNICO ----------------
    def admin_listar_email_canonicos_async(self, account_id: int, callback: Optional[Callable] = None):
        """Bloqueos de email canónico: emails vetados (se guardan como hash, no en claro)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_canonical_email_blocks")()
            return [{"id": b.get("id"), "hash": (b.get("canonical_email_hash") or "")[:16]}
                    for b in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_bloquear_email_canonico_async(self, account_id: int, email: str, callback: Optional[Callable] = None):
        """Bloquea un email concreto (el servidor lo canoniza y guarda su hash)."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_create_canonical_email_block")(email=email)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_desbloquear_email_canonico_async(self, account_id: int, bloqueo_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_delete_canonical_email_block")(bloqueo_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_probar_email_canonico_async(self, account_id: int, email: str, callback: Optional[Callable] = None):
        """Comprueba si un email ya está bloqueado por un bloqueo canónico. Devuelve cuántos coinciden."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_test_canonical_email_block")(email)
            return len(res or [])
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ---------------- BLOQUEOS DE EMAIL / IP ----------------
    def admin_listar_email_blocks_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_email_domain_blocks")()
            return [{"id": e.get("id"), "dominio": e.get("domain") or ""} for e in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_bloquear_email_async(self, account_id: int, dominio: str, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_create_email_domain_block")(dominio)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_desbloquear_email_async(self, account_id: int, bloqueo_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_delete_email_domain_block")(bloqueo_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_listar_ip_blocks_async(self, account_id: int, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = self._api(client, "admin_ip_blocks")()
            return [{"id": b.get("id"), "ip": b.get("ip") or "", "severidad": b.get("severity") or ""}
                    for b in (res or [])]
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_bloquear_ip_async(self, account_id: int, ip: str, severidad: str = "no_access",
                                callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_create_ip_block")(ip, severidad)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    def admin_desbloquear_ip_async(self, account_id: int, bloqueo_id, callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            return self._api(client, "admin_delete_ip_block")(bloqueo_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # ---------------- TENDENCIAS POR APROBAR ----------------
    def admin_listar_tendencias_async(self, account_id: int, tipo: str = "tags",
                                      callback: Optional[Callable] = None):
        """tipo: 'tags', 'statuses' o 'links'."""
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            res = []
            if tipo == "tags":
                for t in (self._api(client, "admin_trending_tags")() or []):
                    res.append({"id": t.get("id") or t.get("name"), "texto": "#" + (t.get("name") or "")})
            elif tipo == "statuses":
                for s in (self._api(client, "admin_trending_statuses")() or []):
                    res.append({"id": s.get("id"), "texto": html_a_texto(s.get("content") or "")[:200]})
            else:
                for l in (self._api(client, "admin_trending_links")() or []):
                    res.append({"id": l.get("id") or l.get("url"), "texto": (l.get("title") or l.get("url") or "")})
            return res
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    def admin_moderar_tendencia_async(self, account_id: int, tipo: str, item_id, aprobar: bool,
                                      callback: Optional[Callable] = None):
        def _bg(ctx):
            client = self.obtener_cliente(account_id)
            if tipo == "tags":
                return self._api(client, "admin_approve_trending_tag")(item_id) if aprobar else self._api(client, "admin_reject_trending_tag")(item_id)
            if tipo == "statuses":
                return self._api(client, "admin_approve_trending_status")(item_id) if aprobar else self._api(client, "admin_reject_trending_status")(item_id)
            return self._api(client, "admin_approve_trending_link")(item_id) if aprobar else self._api(client, "admin_reject_trending_link")(item_id)
        task_manager.start_task(_bg, callback=callback, success_sound="ready", error_sound="error", pass_context=True)

    # ---------------- ESTADÍSTICAS DEL SERVIDOR ----------------
    def admin_estadisticas_async(self, account_id: int, dias: int = 30,
                                 callback: Optional[Callable] = None):
        """Métricas del servidor para cuentas admin (admin_measures + admin_dimensions +
        admin_retention): usuarios activos, registros, interacciones, informes, software,
        idiomas y retención. Devuelve un dict con listas ya legibles."""
        def _bg(ctx):
            from datetime import datetime, timedelta
            client = self.obtener_cliente(account_id)
            fin = datetime.now()
            inicio = fin - timedelta(days=max(1, int(dias)))
            datos = {"medidas": [], "dimensiones": [], "retencion": []}
            try:
                for m in (self._api(client, "admin_measures")(
                        inicio, fin, active_users=True, new_users=True, interactions=True,
                        opened_reports=True, resolved_reports=True) or []):
                    datos["medidas"].append({"clave": m.get("key") or "",
                                             "total": m.get("total") or 0})
            except Exception as e:
                log.debug(f"admin_measures no disponible: {e}")
            try:
                for d in (self._api(client, "admin_dimensions")(
                        inicio, fin, limit=8, languages=True, sources=True,
                        software_versions=True) or []):
                    valores = [f"{x.get('human_key') or x.get('key')}: {x.get('human_value') or x.get('value')}"
                               for x in (d.get("data") or [])[:8]]
                    datos["dimensiones"].append({"clave": d.get("key") or "", "valores": valores})
            except Exception as e:
                log.debug(f"admin_dimensions no disponible: {e}")
            try:
                for r in (self._api(client, "admin_retention")(inicio, fin, frequency="month") or []):
                    tasas = [str(x.get("percent") or x.get("rate") or "") for x in (r.get("data") or [])[:6]]
                    datos["retencion"].append({"cohorte": str(r.get("period") or "")[:10], "tasas": tasas})
            except Exception as e:
                log.debug(f"admin_retention no disponible: {e}")
            return datos
        task_manager.start_task(_bg, callback=callback, pass_context=True)

    # ==================================================================
    # ADMINISTRACIÓN COMPLETA. Estos métodos conservan la respuesta rica
    # de la API y son los usados por los diálogos accesibles de administración.
    # Los anteriores se mantienen por compatibilidad con handlers antiguos.
    # ==================================================================
    def _admin_tarea(self, account_id: int, operacion: Callable, callback=None, escritura=False):
        def _bg(ctx):
            return operacion(self.obtener_cliente(account_id))
        task_manager.start_task(
            _bg, callback=callback, pass_context=True,
            success_sound="ready" if escritura else None,
            error_sound="error" if escritura else None,
        )

    @staticmethod
    def _admin_peticion_directa(client, metodo: str, ruta: str, parametros=None):
        """Accede a endpoints públicos aún no envueltos por Mastodon.py 2.2.1.

        Mastodon.py no ofrece un método público genérico, pero su propio módulo de endpoints usa
        este requestor. Se concentra aquí para que una actualización de la librería solo requiera
        cambiar un punto y para no duplicar HTTP/autenticación fuera del cliente.
        """
        request = getattr(client, "_Mastodon__api_request", None)
        if request is None:
            raise RuntimeError("La versión instalada de Mastodon.py no permite esta operación administrativa.")
        return request(metodo, ruta, parametros or {})

    @staticmethod
    def _pagina(items, limit: int) -> dict:
        lista = list(items or [])
        ultimo = str(_valor(lista[-1], "id") or "") if lista else ""
        return {"items": lista, "next_max_id": ultimo if len(lista) >= limit else None,
                "has_more": len(lista) >= limit}

    # ---------------- INFORMES RICOS ----------------
    def admin_buscar_informes_async(self, account_id: int, resolved=False, reporter_id=None,
                                    target_id=None, max_id=None, limit=100, callback=None):
        limit = min(100, max(1, int(limit)))

        def _op(client):
            if resolved is None:
                # TODOS (resueltos y sin resolver): parámetro "unresolved" de Mastodon 4.6, que
                # Mastodon.py no envía. Combinado con resolved=true devuelve ambos.
                params = {"resolved": "true", "unresolved": "true", "limit": limit}
                for clave, valor in (("account_id", reporter_id), ("target_account_id", target_id),
                                     ("max_id", max_id)):
                    if valor:
                        params[clave] = valor
                raw = self._admin_peticion_directa(client, "GET", "/api/v1/admin/reports", params)
            else:
                raw = self._api(client, "admin_reports")(
                    resolved=bool(resolved), account_id=reporter_id or None,
                    target_account_id=target_id or None, max_id=max_id or None, limit=limit)
            pagina = self._pagina(raw, limit)
            pagina["items"] = [_serializar_informe(r) for r in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_detalle_informe_async(self, account_id: int, report_id, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: _serializar_informe(self._api(c, "admin_report")(report_id)),
            callback,
        )

    def admin_actualizar_informe_async(self, account_id: int, report_id, categoria: str,
                                       rule_ids=None, callback=None):
        def _op(client):
            raw = self._admin_peticion_directa(
                client, "PUT", f"/api/v1/admin/reports/{report_id}",
                {"category": categoria, "rule_ids": list(rule_ids or [])})
            return _serializar_informe(raw)
        self._admin_tarea(account_id, _op, callback, escritura=True)

    def admin_listar_reglas_instancia_async(self, account_id: int, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: [{"id": str(_valor(r, "id") or ""), "texto": _valor(r, "text") or ""}
                       for r in (self._api(c, "instance_rules")() or [])],
            callback,
        )

    def admin_moderar_cuenta_async(self, account_id: int, target_account_id, accion=None,
                                   report_id=None, texto=None, enviar_email=True, callback=None):
        """Replica la acción personalizada de la web con los parámetros de la API pública."""
        def _op(client):
            self._api(client, "admin_account_moderate")(
                target_account_id, action=accion, report_id=report_id,
                text=texto or None, send_email_notification=bool(enviar_email))
            return True
        self._admin_tarea(account_id, _op, callback, escritura=True)

    # ---------------- CUENTAS RICAS ----------------
    def admin_buscar_cuentas_async(self, account_id: int, filtros=None, max_id=None,
                                   limit=100, callback=None):
        filtros = dict(filtros or {})
        limit = min(100, max(1, int(limit)))

        def _op(client):
            permitidos = ("origin", "by_domain", "status", "username", "display_name",
                          "email", "ip", "permissions", "invited_by", "role_ids")
            params = {k: filtros.get(k) for k in permitidos if filtros.get(k) not in (None, "", [])}
            params.update({"max_id": max_id or None, "limit": limit})
            raw = self._api(client, "admin_accounts_v2")(**params)
            pagina = self._pagina(raw, limit)
            pagina["items"] = [_serializar_cuenta_admin(a) for a in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_detalle_cuenta_async(self, account_id: int, target_account_id, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: _serializar_cuenta_admin(self._api(c, "admin_account")(target_account_id)),
            callback,
        )

    def admin_eliminar_cuenta_async(self, account_id: int, target_account_id, callback=None):
        self._admin_tarea(
            account_id, lambda c: self._api(c, "admin_account_delete")(target_account_id),
            callback, escritura=True)

    # ---------------- BLOQUEOS DE DOMINIO COMPLETOS ----------------
    @staticmethod
    def _serializar_bloqueo_dominio(obj):
        return {
            "id": str(_valor(obj, "id") or ""), "dominio": _valor(obj, "domain") or "",
            "severidad": _valor(obj, "severity") or "silence",
            "rechazar_media": bool(_valor(obj, "reject_media", False)),
            "rechazar_informes": bool(_valor(obj, "reject_reports", False)),
            "comentario_privado": _valor(obj, "private_comment") or "",
            "comentario_publico": _valor(obj, "public_comment") or "",
            "ofuscar": bool(_valor(obj, "obfuscate", False)),
            "digest": _valor(obj, "digest") or "", "creado": _fecha(_valor(obj, "created_at")),
        }

    def admin_buscar_bloqueos_dominio_async(self, account_id: int, max_id=None, limit=100,
                                             callback=None):
        limit = min(500, max(1, int(limit)))

        def _op(client):
            raw = self._api(client, "admin_domain_blocks")(max_id=max_id or None, limit=limit)
            pagina = self._pagina(raw, limit)
            pagina["items"] = [self._serializar_bloqueo_dominio(x) for x in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_detalle_bloqueo_dominio_async(self, account_id: int, bloqueo_id, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: self._serializar_bloqueo_dominio(
                self._api(c, "admin_domain_blocks")(id=bloqueo_id)),
            callback,
        )

    def admin_guardar_bloqueo_dominio_async(self, account_id: int, datos: dict,
                                             bloqueo_id=None, callback=None):
        def _op(client):
            kwargs = {
                "severity": datos.get("severidad") or "silence",
                "reject_media": bool(datos.get("rechazar_media")),
                "reject_reports": bool(datos.get("rechazar_informes")),
                "private_comment": datos.get("comentario_privado") or None,
                "public_comment": datos.get("comentario_publico") or None,
                "obfuscate": bool(datos.get("ofuscar")),
            }
            if bloqueo_id:
                raw = self._api(client, "admin_update_domain_block")(bloqueo_id, **kwargs)
            else:
                raw = self._api(client, "admin_create_domain_block")(
                    datos.get("dominio") or "", **kwargs)
            return self._serializar_bloqueo_dominio(raw)
        self._admin_tarea(account_id, _op, callback, escritura=True)

    # ---------------- BLOQUEOS IP COMPLETOS ----------------
    @staticmethod
    def _serializar_ip(obj):
        return {
            "id": str(_valor(obj, "id") or ""), "ip": _valor(obj, "ip") or "",
            "severidad": _valor(obj, "severity") or "no_access",
            "comentario": _valor(obj, "comment") or "", "creado": _fecha(_valor(obj, "created_at")),
            "expira": _fecha(_valor(obj, "expires_at")),
        }

    def admin_buscar_ip_blocks_async(self, account_id: int, max_id=None, limit=100, callback=None):
        limit = min(100, max(1, int(limit)))

        def _op(client):
            raw = self._api(client, "admin_ip_blocks")(max_id=max_id or None, limit=limit)
            pagina = self._pagina(raw, limit)
            pagina["items"] = [self._serializar_ip(x) for x in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_detalle_ip_block_async(self, account_id: int, bloqueo_id, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: self._serializar_ip(self._api(c, "admin_ip_block")(bloqueo_id)),
            callback,
        )

    def admin_guardar_ip_block_async(self, account_id: int, datos: dict, bloqueo_id=None,
                                     callback=None):
        def _op(client):
            kwargs = {"ip": datos.get("ip") or None,
                      "severity": datos.get("severidad") or "no_access",
                      "comment": datos.get("comentario") or None,
                      "expires_in": datos.get("expira_en") or None}
            if bloqueo_id:
                raw = self._api(client, "admin_update_ip_block")(bloqueo_id, **kwargs)
            else:
                ip = kwargs.pop("ip")
                severity = kwargs.pop("severity")
                raw = self._api(client, "admin_create_ip_block")(ip, severity, **kwargs)
            return self._serializar_ip(raw)
        self._admin_tarea(account_id, _op, callback, escritura=True)

    # ---------------- RECURSOS DE REGISTRO/FEDERACIÓN ----------------
    def admin_listar_recurso_async(self, account_id: int, recurso: str, max_id=None,
                                   limit=100, callback=None):
        metodos = {
            "domain_allows": "admin_domain_allows",
            "email_domains": "admin_email_domain_blocks",
            "canonical_emails": "admin_canonical_email_blocks",
        }
        if recurso not in metodos:
            raise ValueError("Recurso administrativo no válido")
        limit = min(500 if recurso == "domain_allows" else 100, max(1, int(limit)))

        def _op(client):
            raw = self._api(client, metodos[recurso])(max_id=max_id or None, limit=limit)
            pagina = self._pagina(raw, limit)
            salida = []
            for x in pagina["items"]:
                salida.append({
                    "id": str(_valor(x, "id") or ""),
                    "valor": (_valor(x, "domain") or _valor(x, "canonical_email_hash") or ""),
                    "creado": _fecha(_valor(x, "created_at")),
                    "historial": [{"dia": _fecha(_valor(h, "day")),
                                   "cuentas": int(_valor(h, "accounts", 0) or 0),
                                   "usos": int(_valor(h, "uses", 0) or 0)}
                                  for h in (_valor(x, "history") or [])],
                })
            pagina["items"] = salida
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_detalle_recurso_async(self, account_id: int, recurso: str, item_id, callback=None):
        metodos = {
            "domain_allows": "admin_domain_allow",
            "email_domains": "admin_email_domain_block",
            "canonical_emails": "admin_canonical_email_block",
        }
        if recurso not in metodos:
            raise ValueError("Recurso administrativo no válido")

        def _op(client):
            x = self._api(client, metodos[recurso])(item_id)
            return {
                "id": str(_valor(x, "id") or ""),
                "valor": _valor(x, "domain") or _valor(x, "canonical_email_hash") or "",
                "creado": _fecha(_valor(x, "created_at")),
                "historial": [{"dia": _fecha(_valor(h, "day")),
                               "cuentas": int(_valor(h, "accounts", 0) or 0),
                               "usos": int(_valor(h, "uses", 0) or 0)}
                              for h in (_valor(x, "history") or [])],
            }
        self._admin_tarea(account_id, _op, callback)

    def admin_crear_recurso_async(self, account_id: int, recurso: str, valor: str,
                                  allow_with_approval=False, es_hash=False, callback=None):
        def _op(client):
            if recurso == "domain_allows":
                return self._api(client, "admin_create_domain_allow")(valor)
            if recurso == "canonical_emails":
                kwargs = {"canonical_email_hash": valor} if es_hash else {"email": valor}
                return self._api(client, "admin_create_canonical_email_block")(**kwargs)
            if recurso == "email_domains":
                if allow_with_approval:
                    return self._admin_peticion_directa(
                        client, "POST", "/api/v1/admin/email_domain_blocks",
                        {"domain": valor, "allow_with_approval": True})
                return self._api(client, "admin_create_email_domain_block")(valor)
            raise ValueError("Recurso administrativo no válido")
        self._admin_tarea(account_id, _op, callback, escritura=True)

    def admin_borrar_recurso_async(self, account_id: int, recurso: str, item_id, callback=None):
        metodos = {
            "domain_allows": "admin_delete_domain_allow",
            "email_domains": "admin_delete_email_domain_block",
            "canonical_emails": "admin_delete_canonical_email_block",
        }
        if recurso not in metodos:
            raise ValueError("Recurso administrativo no válido")
        self._admin_tarea(account_id, lambda c: self._api(c, metodos[recurso])(item_id),
                          callback, escritura=True)

    # ---------------- HASHTAGS Y PROVEEDORES (API SIN WRAPPER 2.2.1) ----------------
    def admin_listar_hashtags_async(self, account_id: int, max_id=None, limit=100, callback=None):
        limit = min(100, max(1, int(limit)))

        def _op(client):
            raw = self._admin_peticion_directa(
                client, "GET", "/api/v1/admin/tags",
                {"max_id": max_id, "limit": limit})
            pagina = self._pagina(raw, limit)
            pagina["items"] = [{
                "id": str(_valor(x, "id") or ""), "nombre": _valor(x, "name") or "",
                "display_name": _valor(x, "display_name") or _valor(x, "name") or "",
                "url": _valor(x, "url") or "", "listable": bool(_valor(x, "listable", False)),
                "trendable": bool(_valor(x, "trendable", False)),
                "usable": bool(_valor(x, "usable", False)),
                "requiere_revision": bool(_valor(x, "requires_review", False)),
            } for x in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_actualizar_hashtag_async(self, account_id: int, tag_id, datos: dict, callback=None):
        self._admin_tarea(
            account_id,
            lambda c: self._admin_peticion_directa(
                c, "PUT", f"/api/v1/admin/tags/{tag_id}", {
                    "display_name": datos.get("display_name") or None,
                    "listable": bool(datos.get("listable")),
                    "trendable": bool(datos.get("trendable")),
                    "usable": bool(datos.get("usable")),
                }), callback, escritura=True)

    def admin_listar_publicadores_async(self, account_id: int, max_id=None, limit=100, callback=None):
        limit = min(100, max(1, int(limit)))

        def _op(client):
            raw = self._admin_peticion_directa(
                client, "GET", "/api/v1/admin/trends/links/publishers",
                {"max_id": max_id, "limit": limit})
            pagina = self._pagina(raw, limit)
            pagina["items"] = [{
                "id": str(_valor(x, "id") or ""), "dominio": _valor(x, "domain") or "",
                "trendable": bool(_valor(x, "trendable", False)),
                "revisado": _fecha(_valor(x, "reviewed_at")),
                "solicitado": _fecha(_valor(x, "requested_review_at")),
                "requiere_revision": bool(_valor(x, "requires_review", False)),
            } for x in pagina["items"]]
            return pagina
        self._admin_tarea(account_id, _op, callback)

    def admin_moderar_publicador_async(self, account_id: int, provider_id, aprobar: bool, callback=None):
        accion = "approve" if aprobar else "reject"
        self._admin_tarea(
            account_id,
            lambda c: self._admin_peticion_directa(
                c, "POST", f"/api/v1/admin/trends/links/publishers/{provider_id}/{accion}"),
            callback, escritura=True)

    # ---------------- MÉTRICAS COMPLETAS ----------------
    def admin_estadisticas_avanzadas_async(self, account_id: int, dias=30, claves=None,
                                           dominio=None, tag_id=None, frecuencia="month", callback=None):
        """Expone todas las claves de measures/dimensions de Mastodon.py 2.2.1."""
        claves = set(claves or [])

        def _op(client):
            from datetime import timedelta
            fin = datetime.now()
            inicio = fin - timedelta(days=max(1, int(dias)))
            medidas_bool = ("active_users", "new_users", "interactions", "opened_reports", "resolved_reports")
            medidas_tag = ("tag_accounts", "tag_uses", "tag_servers")
            medidas_dom = ("instance_accounts", "instance_media_attachments", "instance_reports",
                            "instance_statuses", "instance_follows", "instance_followers")
            dims_bool = ("languages", "sources", "servers", "space_usage", "software_versions")
            mk = {k: True for k in medidas_bool if k in claves}
            mk.update({k: tag_id for k in medidas_tag if k in claves and tag_id})
            mk.update({k: dominio for k in medidas_dom if k in claves and dominio})
            dk = {k: True for k in dims_bool if k in claves}
            if "tag_servers_dim" in claves and tag_id:
                dk["tag_servers"] = tag_id
            if "tag_languages" in claves and tag_id:
                dk["tag_languages"] = tag_id
            if "instance_accounts_dim" in claves and dominio:
                dk["instance_accounts"] = dominio
            if "instance_languages" in claves and dominio:
                dk["instance_languages"] = dominio
            salida = {"medidas": [], "dimensiones": [], "retencion": []}
            if mk:
                for m in self._api(client, "admin_measures")(inicio, fin, **mk):
                    salida["medidas"].append({
                        "clave": _valor(m, "key") or "", "total": _valor(m, "total") or 0,
                        "humano": _valor(m, "human_value") or "", "unidad": _valor(m, "unit") or "",
                        "anterior": _valor(m, "previous_total"),
                        "serie": [{"fecha": _fecha(_valor(x, "date")), "valor": _valor(x, "value", 0)}
                                  for x in (_valor(m, "data") or [])],
                    })
            if dk:
                for d in self._api(client, "admin_dimensions")(inicio, fin, limit=20, **dk):
                    salida["dimensiones"].append({
                        "clave": _valor(d, "key") or "",
                        "valores": [{"clave": _valor(x, "human_key") or _valor(x, "key") or "",
                                     "valor": _valor(x, "value", 0)}
                                    for x in (_valor(d, "data") or [])],
                    })
            if "retention" in claves:
                for r in self._api(client, "admin_retention")(
                        inicio, fin, frequency=frecuencia if frecuencia in ("day", "month") else "month"):
                    salida["retencion"].append({
                        "periodo": _fecha(_valor(r, "period")), "frecuencia": _valor(r, "frequency") or "",
                        "datos": [{"fecha": _fecha(_valor(x, "date")),
                                   "tasa": float(_valor(x, "rate", 0) or 0),
                                   "valor": int(_valor(x, "value", 0) or 0)}
                                  for x in (_valor(r, "data") or [])],
                    })
            return salida
        self._admin_tarea(account_id, _op, callback)
