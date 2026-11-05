# -*- coding: utf-8 -*-
"""Reglas automáticas y carpetas inteligentes de RSS en la interfaz GTK: las mismas reglas, con varias condiciones, acciones,
ámbito y prioridad, y las mismas carpetas de criterios estructurados que la versión de Windows."""

import json
import logging
import re

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.database import db
from app.core.speech import notificar
from app.services.rss.rss_reglas_textos import (ACCIONES_SIMPLES, CAMPOS, N_CONDICIONES, OPERADORES, condiciones_de_fila,
                                                describir_criterios, resumen_regla)
from app.services.rss.rss_rules import RSSRulesEngine, evaluar_expresion
from app.services.rss.rss_service import rss_service

from .dialogos import DialogoBase, confirmar_async
from .dialogos_mastodon import DialogoLista
from .dialogos_rss import desplazable

log = logging.getLogger(__name__)


def _desplegable(opciones) -> Gtk.DropDown:
    return Gtk.DropDown.new_from_strings(list(opciones))


# --------------------------------------------------------------------------- reglas
class DialogoReglas(DialogoLista):
    """Gestor de reglas: crear (simple o avanzada), editar, activar o desactivar, probar, aplicar a lo que ya tienes y eliminar."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Reglas y filtros RSS automáticos"), _("Lista de reglas"), al_activar=lambda pos: self._editar())
        self.account_id = account_id
        self.filas = []
        for et, fn in ((_("_Nueva regla sencilla…"), self._nueva_sencilla), (_("Regla a_vanzada…"), self._nueva_avanzada),
                       (_("_Editar…"), self._editar), (_("Activar o _desactivar"), self._activar),
                       (_("_Probar las reglas"), self._probar), (_("Aplicar a los artículos que _ya tengo"), self._aplicar),
                       (_("E_liminar"), self._eliminar)):
            self.boton(et, fn)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.filas = db.query(
            "SELECT id, title, pattern, field, action, action_value, active, COALESCE(conditions_json,''), "
            "COALESCE(match_mode,'all'), COALESCE(priority,0), COALESCE(actions_json,'') FROM rss_rules "
            "WHERE account_id = ? ORDER BY COALESCE(priority,0) DESC, id", (self.account_id,))
        lineas = []
        for r in self.filas:
            conds = condiciones_de_fila(r[2], r[3], r[7])
            try:
                acciones = json.loads(r[10]) if r[10] else []
            except ValueError:
                acciones = []
            acciones = acciones or [{"action": r[4], "value": r[5]}]
            lineas.append("{0}. {1}. {2} {3}. {4}".format(r[1] or "", _("Activa") if r[6] else _("Desactivada"),
                                                          _("Prioridad"), r[9], resumen_regla(conds, r[8], acciones)))
        self.lista.poner(lineas)
        if self.filas:
            self.lista.seleccion.set_selected(max(0, min(pos, len(self.filas) - 1)))
        self.mostrar_estado(_("{0} reglas (por prioridad).").format(len(self.filas)) if self.filas else _("No hay reglas."))
        self.lista.enfocar()

    def _actual(self):
        p = self.lista.posicion()
        return (p, self.filas[p]) if 0 <= p < len(self.filas) else (p, None)

    def _nueva_sencilla(self):
        d = DialogoBase(self, _("Crear una regla sencilla"), _("_Crear la regla"))
        nombre = d.campo(_("_Nombre:"), Gtk.Entry(width_chars=40, activates_default=True))
        patron = d.campo(_("Si el _título contiene:"), Gtk.Entry(width_chars=40, activates_default=True))
        accion = d.campo(_("_Acción:"), _desplegable(n for _k, n in ACCIONES_SIMPLES))

        def _crear():
            titulo, texto = nombre.get_text().strip(), patron.get_text().strip()
            if not titulo or not texto:
                notificar(_("Rellena el nombre y el texto."))
                return
            clave = ACCIONES_SIMPLES[accion.get_selected()][0]
            valor = "ready" if clave == "play_sound" else None
            db.execute("INSERT INTO rss_rules (account_id, title, pattern, field, action, action_value, active, "
                       "conditions_json, match_mode, actions_json) VALUES (?, ?, ?, 'title', ?, ?, 1, ?, 'all', ?)",
                       (self.account_id, titulo, texto, clave, valor,
                        json.dumps([{"field": "title", "pattern": texto, "op": "contiene"}], ensure_ascii=False),
                        json.dumps([{"action": clave, "value": valor}])))
            RSSRulesEngine.invalidar_plan(self.account_id)
            notificar(_("Regla guardada correctamente."))
            d.close()
            self._cargar(len(self.filas))
        d.aceptar = _crear
        d.set_focus(nombre)
        d.present()

    def _nueva_avanzada(self, regla_id=None):
        DialogoReglaAvanzada(self, self.account_id, regla_id, lambda: self._cargar(self.lista.posicion())).present()

    def _editar(self):
        _p, fila = self._actual()
        if fila:
            self._nueva_avanzada(fila[0])

    def _activar(self):
        p, fila = self._actual()
        if fila:
            db.execute("UPDATE rss_rules SET active = ? WHERE id = ?", (0 if fila[6] else 1, fila[0]))
            RSSRulesEngine.invalidar_plan(self.account_id)
            self._cargar(p)
            notificar(_("Regla desactivada.") if fila[6] else _("Regla activada."))

    def _aplicar(self):
        if not self.filas:
            return
        _p, fila = self._actual()
        from .dialogos import DialogoElegir
        opciones = ([_("Solo la regla elegida: {0}").format(fila[1])] if fila else []) + [_("Todas las reglas activas")]

        def _hecho(i):
            regla_id = fila[0] if fila and i == 0 and len(opciones) == 2 else None
            if regla_id and not fila[6]:
                notificar(_("La regla está desactivada: actívala antes de aplicarla."))
                return
            n = RSSRulesEngine.aplicar_a_existentes(self.account_id, regla_id=regla_id)
            notificar(_("Reglas aplicadas: {0} artículos coincidieron.").format(n))
            self.padre.cargar_arbol()
        DialogoElegir(self, _("Aplicar a los artículos que ya tengo"),
                      _("¿Qué reglas quieres aplicar? (Los sonidos y avisos por voz no se ejecutan.)"), opciones, _hecho,
                      _("_Aplicar")).present()

    def _probar(self):
        notificar(_("Probando las reglas…"))
        resultados = RSSRulesEngine.simular(self.account_id)
        if not resultados:
            notificar(_("No hay reglas activas que probar."))
            return
        lineas = []
        for r in resultados:
            n = len(r["coincidencias"])
            lineas.append(_("Regla «{0}»: {1} coincidencias").format(r["regla"], n))
            lineas += ["  - " + t for t in r["coincidencias"][:10]]
            if n > 10:
                lineas.append(_("  ... y {0} más").format(n - 10))
            lineas.append("")
        from .dialogos_mastodon_extra import DialogoTextoLargo
        DialogoTextoLargo(self, _("Prueba de reglas (sin aplicar acciones)"),
                          "\n".join(lineas).strip() or _("Ninguna regla coincide.")).present()

    def _eliminar(self):
        p, fila = self._actual()
        if not fila:
            return

        def _hacer():
            db.execute("DELETE FROM rss_rules WHERE id = ?", (fila[0],))
            RSSRulesEngine.invalidar_plan(self.account_id)
            self._cargar(p)
            notificar(_("Regla eliminada."))
        confirmar_async(self, _("¿Eliminar la regla «{0}»?").format(fila[1]), _hacer, etiqueta_si=_("Eliminar"))


class DialogoReglaAvanzada(DialogoBase):
    """Crear o editar una regla con varias condiciones, combinación, acciones, ámbito, prioridad y «detener»."""

    ACCIONES = (("mark_read", _("Marcar como leído")), ("mark_favorite", _("Destacar")),
                ("read_later", _("Marcar para leer más tarde")), ("play_sound", _("Reproducir un sonido")),
                ("notify", _("Avisar por voz con el título")), ("delete", _("Mover a la papelera")),
                ("no_avisar", _("No avisar de su llegada")))

    def __init__(self, padre, account_id: int, regla_id, al_guardar):
        super().__init__(padre, _("Editar regla") if regla_id else _("Regla avanzada"), _("_Guardar la regla"))
        self.account_id, self.regla_id, self.al_guardar = account_id, regla_id, al_guardar
        self.titulo = self.campo(_("_Nombre de la regla:"), Gtk.Entry(width_chars=50))
        self.condiciones = []
        for i in range(1, N_CONDICIONES + 1):
            campo = self.campo(_("Condición {0}: c_ampo").format(i), _desplegable(n for _k, n in CAMPOS))
            op = self.campo(_("Condición {0}: o_perador").format(i), _desplegable(n for _k, n in OPERADORES))
            texto = self.campo(_("Condición {0}: _texto (vacío = no usarla)").format(i), Gtk.Entry(width_chars=50))
            self.condiciones.append((campo, op, texto))
        self.modo = self.campo(_("Co_mbinar las condiciones:"), _desplegable(
            [_("Deben cumplirse todas (Y)"), _("Basta con una (O)"), _("Combinación personalizada")]))
        self.modo.connect("notify::selected", lambda *_a: self._ajustar_expr())
        self.expr = self.campo(_("Combinación _personalizada (por ejemplo: (1 y 2) o 3):"), Gtk.Entry(width_chars=40),
                               _("Con los números de las condiciones, y, o, no y paréntesis."))
        self.acciones = {}
        for clave, nombre in self.ACCIONES:
            self.acciones[clave] = self.campo("", Gtk.CheckButton(label=nombre))
        self.etiquetas = db.query("SELECT id, name FROM rss_tags WHERE account_id = ? ORDER BY position, name", (account_id,))
        self.etiqueta = self.campo(_("Etiquetar el artículo _con:"), _desplegable([_("No etiquetar")] + [n for _i, n in self.etiquetas]))
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (account_id,))
        self.mover = self.campo(_("Mover el feed _entero a la carpeta:"), _desplegable([_("No mover")] + [n for _i, n in self.cats]))
        self.feeds = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? ORDER BY title", (account_id,))
        self.ambito = self.campo(_("A_plicar a:"), _desplegable(
            [_("Todos los feeds")] + [_("Solo el feed: {0}").format(t) for _i, t in self.feeds]
            + [_("Solo la carpeta: {0}").format(n) for _i, n in self.cats]))
        self.prioridad = self.campo(_("_Prioridad (mayor = se evalúa antes):"), Gtk.SpinButton.new_with_range(-100, 100, 1))
        self.detener = self.campo("", Gtk.CheckButton(label=_("_Detener: si esta regla coincide, no evaluar las siguientes"),
                                                      use_underline=True))
        self.activa = self.campo("", Gtk.CheckButton(label=_("Regla _activa"), use_underline=True, active=True))
        if regla_id:
            self._cargar(regla_id)
        self._ajustar_expr()
        desplazable(self, 640)
        self.set_focus(self.titulo)

    def _ajustar_expr(self):
        self.expr.set_sensitive(self.modo.get_selected() == 2)

    def _cargar(self, regla_id):
        r = db.query_one(
            "SELECT title, pattern, field, action, action_value, active, COALESCE(conditions_json,''), COALESCE(match_mode,'all'), "
            "COALESCE(priority,0), COALESCE(stop_processing,0), scope_feed_id, scope_category_id, COALESCE(actions_json,'') "
            "FROM rss_rules WHERE id = ?", (regla_id,))
        if not r:
            return
        self.titulo.set_text(r[0] or "")
        campos, ops = [k for k, _n in CAMPOS], [k for k, _n in OPERADORES]
        for (campo, op, texto), c in zip(self.condiciones, condiciones_de_fila(r[1], r[2], r[6])):
            campo.set_selected(campos.index(c["field"]) if c.get("field") in campos else 0)
            op.set_selected(ops.index(c["op"]) if c.get("op") in ops else 0)
            texto.set_text(c.get("pattern", ""))
        modo = r[7] or "all"
        if modo.startswith("expr:"):
            self.modo.set_selected(2)
            self.expr.set_text(modo[5:])
        else:
            self.modo.set_selected(1 if modo == "any" else 0)
        try:
            acciones = json.loads(r[12]) if r[12] else []
        except ValueError:
            acciones = []
        for a in acciones or [{"action": r[3], "value": r[4]}]:
            if a.get("action") in self.acciones:
                self.acciones[a["action"]].set_active(True)
            elif a.get("action") == "tag":
                ids = [str(i) for i, _n in self.etiquetas]
                if str(a.get("value")) in ids:
                    self.etiqueta.set_selected(ids.index(str(a["value"])) + 1)
            elif a.get("action") == "categorize":
                ids = [str(i) for i, _n in self.cats]
                if str(a.get("value")) in ids:
                    self.mover.set_selected(ids.index(str(a["value"])) + 1)
        if r[10]:
            ids = [i for i, _t in self.feeds]
            if r[10] in ids:
                self.ambito.set_selected(ids.index(r[10]) + 1)
        elif r[11]:
            ids = [i for i, _n in self.cats]
            if r[11] in ids:
                self.ambito.set_selected(len(self.feeds) + ids.index(r[11]) + 1)
        self.prioridad.set_value(int(r[8]))
        self.detener.set_active(bool(r[9]))
        self.activa.set_active(bool(r[5]))

    def aceptar(self):
        titulo = self.titulo.get_text().strip()
        condiciones = []
        for campo, op, texto in self.condiciones:
            t = texto.get_text().strip()
            if not t:
                continue
            clave_op = OPERADORES[op.get_selected()][0]
            if clave_op == "regex":
                try:
                    re.compile(t)
                except re.error as e:
                    notificar(_("La expresión regular «{0}» no es válida: {1}").format(t, e))
                    return
            condiciones.append({"field": CAMPOS[campo.get_selected()][0], "pattern": t, "op": clave_op,
                                "is_regex": clave_op == "regex"})
        acciones = []
        for clave, cb in self.acciones.items():
            if cb.get_active():
                valor = "ready" if clave == "play_sound" else (titulo or _("Regla RSS") if clave == "notify" else None)
                acciones.append({"action": clave, "value": valor})
        if self.etiqueta.get_selected() > 0:
            acciones.append({"action": "tag", "value": str(self.etiquetas[self.etiqueta.get_selected() - 1][0])})
        if self.mover.get_selected() > 0:
            acciones.append({"action": "categorize", "value": str(self.cats[self.mover.get_selected() - 1][0])})
        if not titulo or not condiciones or not acciones:
            notificar(_("Rellena el nombre, al menos una condición y al menos una acción."))
            return
        modo = ("all", "any", "expr")[self.modo.get_selected()]
        if modo == "expr":
            expr = self.expr.get_text().strip()
            try:
                evaluar_expresion(expr, [True] * len(condiciones))
            except ValueError as e:
                notificar(_("La combinación no es válida: {0}").format(e))
                self.expr.grab_focus()
                return
            modo = "expr:" + expr
        sel = self.ambito.get_selected()
        scope_feed = scope_cat = None
        if 1 <= sel <= len(self.feeds):
            scope_feed = self.feeds[sel - 1][0]
        elif sel > len(self.feeds):
            scope_cat = self.cats[sel - 1 - len(self.feeds)][0]
        valores = (titulo, condiciones[0]["pattern"], acciones[0]["action"], acciones[0]["value"],
                   1 if self.activa.get_active() else 0, json.dumps(condiciones, ensure_ascii=False), modo,
                   int(self.prioridad.get_value()), 1 if self.detener.get_active() else 0, scope_feed, scope_cat,
                   json.dumps(acciones, ensure_ascii=False))
        if self.regla_id:
            db.execute("UPDATE rss_rules SET title = ?, pattern = ?, field = 'title', action = ?, action_value = ?, active = ?, "
                       "conditions_json = ?, match_mode = ?, priority = ?, stop_processing = ?, scope_feed_id = ?, "
                       "scope_category_id = ?, actions_json = ? WHERE id = ?", valores + (self.regla_id,))
        else:
            db.execute("INSERT INTO rss_rules (title, pattern, field, action, action_value, active, conditions_json, match_mode, "
                       "priority, stop_processing, scope_feed_id, scope_category_id, actions_json, account_id) "
                       "VALUES (?, ?, 'title', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", valores + (self.account_id,))
        RSSRulesEngine.invalidar_plan(self.account_id)
        notificar(_("Regla guardada."))
        self.close()
        self.al_guardar()


# --------------------------------------------------------------------------- carpetas inteligentes
class DialogoCarpetaInteligente(DialogoBase):
    DIAS = (0, 1, 7, 30, 365)

    def __init__(self, padre, account_id: int, carpeta, al_guardar):
        super().__init__(padre, _("Editar carpeta inteligente") if carpeta else _("Nueva carpeta inteligente"),
                         _("_Guardar la carpeta"))
        self.al_guardar = al_guardar
        c = (carpeta or {}).get("criteria") or {}
        self.nombre = self.campo(_("_Nombre de la carpeta:"), Gtk.Entry(text=(carpeta or {}).get("name", ""), width_chars=50))
        self.leido = self.campo(_("_Estado de lectura:"), _desplegable([_("Da igual"), _("Solo no leídos"), _("Solo leídos")]))
        self.leido.set_selected({None: 0, 0: 1, 1: 2}.get(c.get("read"), 0))
        self.casillas = {}
        for clave, etiqueta in (("favorite", _("Solo _destacados")), ("read_later", _("Solo leer más _tarde")),
                                ("has_media", _("Solo con _audio o vídeo (podcasts)")),
                                ("updated", _("Solo los _corregidos en origen (tienen versiones anteriores)"))):
            self.casillas[clave] = self.campo("", Gtk.CheckButton(label=etiqueta, use_underline=True, active=bool(c.get(clave))))
        self.feeds = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? ORDER BY title", (account_id,))
        self.cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (account_id,))
        self.origen = self.campo(_("_Origen:"), _desplegable(
            [_("Todos los feeds")] + [_("Solo el feed: {0}").format(t) for _i, t in self.feeds]
            + [_("Solo la carpeta: {0}").format(n) for _i, n in self.cats]))
        sel = 0
        if c.get("feed_id") in [i for i, _t in self.feeds]:
            sel = [i for i, _t in self.feeds].index(c["feed_id"]) + 1
        elif c.get("category_id") in [i for i, _n in self.cats]:
            sel = len(self.feeds) + [i for i, _n in self.cats].index(c["category_id"]) + 1
        self.origen.set_selected(sel)
        self.dias = self.campo(_("Antigüedad _máxima:"), _desplegable(
            [_("Sin límite"), _("Último día"), _("Última semana"), _("Último mes"), _("Último año")]))
        self.dias.set_selected(self.DIAS.index(c.get("days")) if c.get("days") in self.DIAS else 0)
        self.texto = self.campo(_("Que contengan el te_xto (título o contenido):"), Gtk.Entry(text=c.get("text", ""), width_chars=40))
        self.texto_no = self.campo(_("Que NO contengan el texto:"), Gtk.Entry(text=c.get("text_not", ""), width_chars=40))
        self.autor = self.campo(_("_Autor (contiene):"), Gtk.Entry(text=c.get("author", ""), width_chars=40))
        self.enlace = self.campo(_("Di_rección del artículo (contiene):"), Gtk.Entry(text=c.get("link", ""), width_chars=40))
        desplazable(self, 600)
        self.set_focus(self.nombre)

    def aceptar(self):
        nombre = self.nombre.get_text().strip()
        if not nombre:
            notificar(_("Escribe un nombre para la carpeta."))
            return
        criterios = {}
        leido = self.leido.get_selected()
        if leido:
            criterios["read"] = 0 if leido == 1 else 1
        for clave, cb in self.casillas.items():
            if cb.get_active():
                criterios[clave] = 1
        sel = self.origen.get_selected()
        if 1 <= sel <= len(self.feeds):
            criterios["feed_id"] = self.feeds[sel - 1][0]
        elif sel > len(self.feeds):
            criterios["category_id"] = self.cats[sel - 1 - len(self.feeds)][0]
        if self.DIAS[self.dias.get_selected()]:
            criterios["days"] = self.DIAS[self.dias.get_selected()]
        for entrada, clave in ((self.texto, "text"), (self.texto_no, "text_not"), (self.autor, "author"), (self.enlace, "link")):
            if entrada.get_text().strip():
                criterios[clave] = entrada.get_text().strip()
        if not criterios:
            notificar(_("Elige al menos un criterio (si no, ya tienes el buffer «Todos»)."))
            return
        self.close()
        self.al_guardar(nombre, criterios)


class DialogoCarpetasInteligentes(DialogoLista):
    """Lista, crea, edita y elimina carpetas inteligentes (aparecen en el árbol de RSS)."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Carpetas inteligentes"), _("Lista de carpetas inteligentes"), al_activar=lambda pos: self._editar())
        self.account_id = account_id
        self.items = []
        self.boton(_("_Nueva…"), self._nueva)
        self.boton(_("_Editar…"), self._editar)
        self.boton(_("E_liminar"), self._borrar)
        self.cerrar_boton()
        self._cargar()

    def _cargar(self, pos: int = 0):
        self.items = rss_service.obtener_carpetas_inteligentes(self.account_id)
        self.lista.poner([f"{it['name']}. {describir_criterios(it['criteria'])}" for it in self.items])
        if self.items:
            self.lista.seleccion.set_selected(max(0, min(pos, len(self.items) - 1)))
        self.mostrar_estado(_("Tus carpetas inteligentes (se abren desde el árbol de RSS)."))

    def _nueva(self):
        def _crear(nombre, criterios):
            rss_service.crear_carpeta_inteligente(self.account_id, nombre, criterios)
            notificar(_("Carpeta inteligente creada; la verás en el árbol de RSS."))
            self._cargar(len(self.items))
            self.padre.cargar_arbol()
        DialogoCarpetaInteligente(self, self.account_id, None, _crear).present()

    def _editar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return

        def _guardar(nombre, criterios):
            rss_service.editar_carpeta_inteligente(self.items[p]["id"], nombre, criterios)
            notificar(_("Carpeta inteligente guardada."))
            self._cargar(p)
            self.padre.cargar_arbol()
        DialogoCarpetaInteligente(self, self.account_id, self.items[p], _guardar).present()

    def _borrar(self):
        p = self.lista.posicion()
        if not 0 <= p < len(self.items):
            return
        it = self.items[p]

        def _hacer():
            rss_service.borrar_carpeta_inteligente(it["id"])
            self._cargar(p)
            self.padre.cargar_arbol()
        confirmar_async(self, _("¿Eliminar la carpeta inteligente «{0}»? (Los artículos no se tocan.)").format(it["name"]),
                        _hacer, etiqueta_si=_("Eliminar"))
