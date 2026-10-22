# -*- coding: utf-8 -*-
"""Reglas automáticas y carpetas inteligentes de RSS (separado de rss_views.py, que las
reexporta, para mantener los archivos por debajo de 50 KB).

Reglas: condiciones con operador ("contiene", "no contiene", "es igual a", "empieza por",
expresión regular) sobre título, contenido, autor, dirección del artículo o nombre del feed,
combinadas con Y, con O o con una combinación libre como "(1 y 2) o 3"; acciones (leído,
destacar, leer más tarde, sonido, aviso, etiquetar, mover el feed, a la papelera, no avisar);
ámbito, prioridad y "detener". Se pueden editar, activar o desactivar y aplicar a los artículos
que ya tienes. Carpetas inteligentes: criterios estructurados, también editables.
"""
import json
import re

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.services.rss.rss_rules import RSSRulesEngine, evaluar_expresion
from app.services.rss.rss_service import rss_service
from .accessible_controls import (AccessibleListCtrl, AccessibleTextCtrl, confirmar,
                                  habilitar_escape_cierre, mostrar_info)

_ACCIONES_SIMPLES = (("mark_read", _("Marcar como leído")), ("mark_favorite", _("Destacar")),
                     ("read_later", _("Leer más tarde")), ("play_sound", _("Reproducir un sonido")),
                     ("delete", _("Mover a la papelera")), ("no_avisar", _("No avisar de su llegada")))
_NOMBRE_ACCION = dict(_ACCIONES_SIMPLES, notify=_("Avisar por voz"), tag=_("Etiquetar"),
                      categorize=_("Mover el feed"))
_CAMPOS = (("title", _("Título")), ("content", _("Contenido")), ("author", _("Autor")),
           ("link", _("Dirección del artículo")), ("feed", _("Nombre del feed")),
           ("all", _("Todo (título, contenido y autor)")))
_OPERADORES = (("contiene", _("contiene")), ("no_contiene", _("no contiene")),
               ("igual", _("es igual a")), ("empieza", _("empieza por")),
               ("regex", _("cumple la expresión regular")))
_N_CONDICIONES = 4


def _condiciones_de_fila(pattern, field, conditions_json):
    """Condiciones de una regla guardada, también de las antiguas (un patrón y un campo)."""
    try:
        conds = json.loads(conditions_json) if conditions_json else []
    except ValueError:
        conds = []
    if not conds and pattern:
        conds = [{"field": field or "title", "pattern": pattern, "is_regex": True}]
    for c in conds:
        if not c.get("op"):
            regex = bool(c.get("is_regex", True)) and re.search(r"[\\^$.|?*+()\[\]{}]", c.get("pattern", ""))
            c["op"] = "regex" if regex else "contiene"
    return conds


def _resumen_regla(conds, modo, acciones):
    partes = []
    for i, c in enumerate(conds, start=1):
        campo = dict(_CAMPOS).get(c.get("field"), c.get("field"))
        op = dict(_OPERADORES).get(c.get("op"), c.get("op"))
        partes.append(f"{i}: {campo} {op} «{c.get('pattern', '')}»")
    union = {"all": _(" y "), "any": _(" o ")}.get(modo)
    texto = (union or "; ").join(partes)
    if modo and modo.startswith("expr:"):
        texto += " — " + _("combinación: {0}").format(modo[5:])
    nombres = ", ".join(_NOMBRE_ACCION.get(a.get("action"), a.get("action") or "") for a in acciones)
    return f"{texto} → {nombres}"


class ReglasDialog(wx.Dialog):
    """Gestor de reglas: crear (simple o avanzada), editar, activar/desactivar, probar,
    aplicar a los artículos ya guardados y eliminar."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Reglas y Filtros RSS Automáticos"), size=(760, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo reglas RSS"))
        self.account_id = account_id
        s = wx.BoxSizer(wx.VERTICAL)
        caja = wx.StaticBoxSizer(wx.VERTICAL, self, _("Crear una regla sencilla"))
        caja.Add(wx.StaticText(self, label=_("&Nombre:")), 0, wx.ALL, 2)
        self.txt_rule_title = AccessibleTextCtrl(self, name=_("Nombre de la regla"))
        caja.Add(self.txt_rule_title, 0, wx.EXPAND | wx.ALL, 2)
        caja.Add(wx.StaticText(self, label=_("Si el &título contiene:")), 0, wx.ALL, 2)
        self.txt_pattern = AccessibleTextCtrl(self, name=_("Texto que debe contener el título"))
        caja.Add(self.txt_pattern, 0, wx.EXPAND | wx.ALL, 2)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Acción:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 2)
        self.choice_action = wx.Choice(self, choices=[n for _k, n in _ACCIONES_SIMPLES],
                                       name=_("Acción de la regla"))
        self.choice_action.SetSelection(0)
        fila.Add(self.choice_action, 1, wx.EXPAND | wx.ALL, 2)
        b = wx.Button(self, label=_("&Crear regla"), name=_("Guardar regla nueva"))
        b.Bind(wx.EVT_BUTTON, self._on_crear_regla)
        fila.Add(b, 0, wx.ALL, 2)
        caja.Add(fila, 0, wx.EXPAND)
        s.Add(caja, 0, wx.EXPAND | wx.ALL, 5)

        s.Add(wx.StaticText(self, label=_("Reglas &registradas (por prioridad):")), 0, wx.ALL, 5)
        self.lista_rules = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                              name=_("Lista de reglas"))
        for i, (col, ancho) in enumerate(((_("Nombre"), 170), (_("Estado"), 80), (_("Prioridad"), 70),
                                          (_("Qué hace"), 400))):
            self.lista_rules.InsertColumn(i, col, width=ancho)
        self.lista_rules.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_editar())
        s.Add(self.lista_rules, 1, wx.EXPAND | wx.ALL, 5)

        botones = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, accion in ((_("Regla a&vanzada..."), self._on_regla_avanzada),
                                 (_("&Editar..."), lambda e=None: self._on_editar()),
                                 (_("Activar o &desactivar"), lambda e=None: self._on_activar()),
                                 (_("&Probar reglas"), self._on_probar_reglas),
                                 (_("Aplicar a los artículos que &ya tengo"), lambda e=None: self._on_aplicar()),
                                 (_("E&liminar"), self._on_eliminar_regla)):
            bt = wx.Button(self, label=etiqueta)
            bt.Bind(wx.EVT_BUTTON, accion)
            botones.Add(bt, 0, wx.ALL, 3)
        botones.AddStretchSpacer()
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cerra&r")), 0, wx.ALL, 3)
        s.Add(botones, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self._cargar_reglas()
        self.CenterOnParent()

    def _cargar_reglas(self, foco: int = 0):
        self.lista_rules.DeleteAllItems()
        self.lista_rules.limpiar_lecturas()
        self.rules_rows = db.query(
            "SELECT id, title, pattern, field, action, action_value, active, COALESCE(conditions_json,''), "
            "COALESCE(match_mode,'all'), COALESCE(priority,0), COALESCE(actions_json,'') "
            "FROM rss_rules WHERE account_id = ? ORDER BY COALESCE(priority,0) DESC, id", (self.account_id,))
        for i, r in enumerate(self.rules_rows):
            conds = _condiciones_de_fila(r[2], r[3], r[7])
            try:
                acciones = json.loads(r[10]) if r[10] else []
            except ValueError:
                acciones = []
            acciones = acciones or [{"action": r[4], "value": r[5]}]
            resumen = _resumen_regla(conds, r[8], acciones)
            estado = _("Activa") if r[6] else _("Desactivada")
            self.lista_rules.InsertItem(i, r[1] or "")
            self.lista_rules.SetItem(i, 1, estado)
            self.lista_rules.SetItem(i, 2, str(r[9]))
            self.lista_rules.SetItem(i, 3, resumen)
            self.lista_rules.establecer_lectura(i, f"{r[1]}. {estado}. {resumen}")
        if self.rules_rows:
            foco = max(0, min(foco, len(self.rules_rows) - 1))
            self.lista_rules.Select(foco)
            self.lista_rules.Focus(foco)

    def _actual(self):
        i = self.lista_rules.GetFocusedItem()
        return (i, self.rules_rows[i]) if 0 <= i < len(self.rules_rows) else (i, None)

    def _on_crear_regla(self, event):
        titulo = self.txt_rule_title.GetValue().strip()
        patron = self.txt_pattern.GetValue().strip()
        if not titulo or not patron:
            speak(_("Rellena el nombre y el texto."), interrupt=True)
            return
        accion = _ACCIONES_SIMPLES[self.choice_action.GetSelection()][0]
        cond = [{"field": "title", "pattern": patron, "op": "contiene"}]
        db.execute("INSERT INTO rss_rules (account_id, title, pattern, field, action, action_value, active, "
                   "conditions_json, match_mode, actions_json) VALUES (?, ?, ?, 'title', ?, ?, 1, ?, 'all', ?)",
                   (self.account_id, titulo, patron, accion, "ready" if accion == "play_sound" else None,
                    json.dumps(cond, ensure_ascii=False),
                    json.dumps([{"action": accion, "value": "ready" if accion == "play_sound" else None}])))
        RSSRulesEngine.invalidar_plan(self.account_id)
        self.txt_rule_title.Clear()
        self.txt_pattern.Clear()
        self._cargar_reglas()
        notificar(_("Regla guardada correctamente."))

    def _on_regla_avanzada(self, event=None, regla_id=None):
        dlg = ReglaAvanzadaDialog(self, self.account_id, regla_id=regla_id)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_reglas(self.lista_rules.GetFocusedItem())
        dlg.Destroy()

    def _on_editar(self):
        _i, fila = self._actual()
        if fila:
            self._on_regla_avanzada(regla_id=fila[0])

    def _on_activar(self):
        i, fila = self._actual()
        if not fila:
            return
        db.execute("UPDATE rss_rules SET active = ? WHERE id = ?", (0 if fila[6] else 1, fila[0]))
        RSSRulesEngine.invalidar_plan(self.account_id)
        self._cargar_reglas(i)
        speak(_("Regla desactivada.") if fila[6] else _("Regla activada."), interrupt=True)

    def _on_aplicar(self):
        if not self.rules_rows:
            return
        i, fila = self._actual()
        opciones = [_("Solo la regla elegida: {0}").format(fila[1]) if fila else None,
                    _("Todas las reglas activas")]
        opciones = [o for o in opciones if o]
        eleccion = wx.GetSingleChoice(_("¿Qué reglas quieres aplicar a los artículos que ya tienes? "
                                        "(Los sonidos y avisos por voz no se ejecutan.)"),
                                      _("Aplicar a los artículos que ya tengo"), opciones, self)
        if not eleccion:
            return
        regla_id = fila[0] if fila and eleccion == opciones[0] and len(opciones) == 2 else None
        if regla_id and not fila[6]:
            notificar(_("La regla está desactivada: actívala antes de aplicarla."))
            return
        n = RSSRulesEngine.aplicar_a_existentes(self.account_id, regla_id=regla_id)
        notificar(_("Reglas aplicadas: {0} artículos coincidieron.").format(n))
        self.aplicadas = True

    def _on_probar_reglas(self, event):
        speak(_("Probando las reglas..."), interrupt=True)
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
        mostrar_info(self, "\n".join(lineas).strip() or _("Ninguna regla coincide."),
                     _("Prueba de reglas (sin aplicar acciones)"))

    def _on_eliminar_regla(self, event):
        i, fila = self._actual()
        if fila and confirmar(self, _("¿Eliminar la regla «{0}»?").format(fila[1]), _("Eliminar regla")):
            db.execute("DELETE FROM rss_rules WHERE id = ?", (fila[0],))
            RSSRulesEngine.invalidar_plan(self.account_id)
            self._cargar_reglas(i)
            notificar(_("Regla eliminada."))


class ReglaAvanzadaDialog(wx.Dialog):
    """Crear o EDITAR una regla con varias condiciones, combinación, acciones, ámbito,
    prioridad y "detener"."""

    def __init__(self, parent, account_id: int, regla_id=None):
        super().__init__(parent, title=_("Editar regla") if regla_id else _("Regla avanzada"),
                         size=(660, 680), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Regla avanzada de RSS"))
        self.account_id = account_id
        self.regla_id = regla_id
        panel = wx.ScrolledWindow(self)
        panel.SetScrollRate(0, 16)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("&Nombre de la regla:")), 0, wx.ALL, 4)
        self.txt_titulo = AccessibleTextCtrl(panel, name=_("Nombre de la regla"))
        s.Add(self.txt_titulo, 0, wx.EXPAND | wx.ALL, 4)

        self._condiciones = []
        for i in range(1, _N_CONDICIONES + 1):
            caja = wx.StaticBoxSizer(wx.HORIZONTAL, panel, _("Condición {0}").format(i))
            campo = wx.Choice(panel, choices=[n for _k, n in _CAMPOS], name=_("Campo de la condición {0}").format(i))
            campo.SetSelection(0)
            op = wx.Choice(panel, choices=[n for _k, n in _OPERADORES], name=_("Operador de la condición {0}").format(i))
            op.SetSelection(0)
            patron = AccessibleTextCtrl(panel, name=_("Texto de la condición {0}").format(i),
                                        ayuda_texto=_("Déjalo vacío para no usar esta condición."))
            caja.Add(campo, 0, wx.ALL, 2)
            caja.Add(op, 0, wx.ALL, 2)
            caja.Add(patron, 1, wx.EXPAND | wx.ALL, 2)
            s.Add(caja, 0, wx.EXPAND | wx.ALL, 3)
            self._condiciones.append((campo, op, patron))

        self.choice_modo = wx.Choice(panel, choices=[
            _("Deben cumplirse todas (Y)"), _("Basta con una (O)"),
            _("Combinación personalizada")], name=_("Cómo se combinan las condiciones"))
        self.choice_modo.SetSelection(0)
        self.choice_modo.Bind(wx.EVT_CHOICE, lambda e: self._ajustar_expr())
        s.Add(wx.StaticText(panel, label=_("Co&mbinar las condiciones:")), 0, wx.ALL, 4)
        s.Add(self.choice_modo, 0, wx.EXPAND | wx.ALL, 4)
        self.txt_expr = AccessibleTextCtrl(panel, name=_("Combinación personalizada"),
                                           ayuda_texto=_("Con los números de las condiciones, y, o, no y "
                                                         "paréntesis. Por ejemplo: (1 y 2) o 3"))
        s.Add(self.txt_expr, 0, wx.EXPAND | wx.ALL, 4)

        caja_a = wx.StaticBoxSizer(wx.VERTICAL, panel, _("Acciones al coincidir"))
        self.cb_acciones = {}
        for clave, nombre in (("mark_read", _("Marcar como leído")), ("mark_favorite", _("Destacar")),
                              ("read_later", _("Marcar para leer más tarde")),
                              ("play_sound", _("Reproducir un sonido")), ("notify", _("Avisar por voz con el título")),
                              ("delete", _("Mover a la papelera")), ("no_avisar", _("No avisar de su llegada"))):
            cb = wx.CheckBox(panel, label=nombre, name=_("Acción: {0}").format(nombre))
            caja_a.Add(cb, 0, wx.ALL, 2)
            self.cb_acciones[clave] = cb
        self._tags = db.query("SELECT id, name FROM rss_tags WHERE account_id = ? ORDER BY position, name",
                              (account_id,))
        caja_a.Add(wx.StaticText(panel, label=_("Etiquetar el artículo con:")), 0, wx.ALL, 2)
        self.choice_tag = wx.Choice(panel, choices=[_("No etiquetar")] + [n for _i, n in self._tags],
                                    name=_("Acción: etiquetar el artículo"))
        self.choice_tag.SetSelection(0)
        caja_a.Add(self.choice_tag, 0, wx.EXPAND | wx.ALL, 2)
        self._cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name",
                              (account_id,))
        caja_a.Add(wx.StaticText(panel, label=_("Mover el feed entero a la carpeta:")), 0, wx.ALL, 2)
        self.choice_mover = wx.Choice(panel, choices=[_("No mover")] + [n for _i, n in self._cats],
                                      name=_("Acción: mover el feed a una carpeta"))
        self.choice_mover.SetSelection(0)
        caja_a.Add(self.choice_mover, 0, wx.EXPAND | wx.ALL, 2)
        s.Add(caja_a, 0, wx.EXPAND | wx.ALL, 4)

        s.Add(wx.StaticText(panel, label=_("A&plicar a:")), 0, wx.ALL, 4)
        self._feeds = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? ORDER BY title", (account_id,))
        self.choice_ambito = wx.Choice(panel, choices=(
            [_("Todos los feeds")] + [_("Solo el feed: {0}").format(t) for _i, t in self._feeds]
            + [_("Solo la carpeta: {0}").format(n) for _i, n in self._cats]), name=_("Ámbito de la regla"))
        self.choice_ambito.SetSelection(0)
        s.Add(self.choice_ambito, 0, wx.EXPAND | wx.ALL, 4)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(panel, label=_("Prioridad (mayor = se evalúa antes):")), 0,
                 wx.ALIGN_CENTER_VERTICAL | wx.ALL, 4)
        self.spin_prioridad = wx.SpinCtrl(panel, min=-100, max=100, initial=0, name=_("Prioridad de la regla"))
        fila.Add(self.spin_prioridad, 0, wx.ALL, 4)
        s.Add(fila, 0)
        self.cb_detener = wx.CheckBox(panel, label=_("Detener: si esta regla coincide, no evaluar las siguientes"),
                                      name=_("Detener el procesamiento al coincidir"))
        s.Add(self.cb_detener, 0, wx.ALL, 4)
        self.cb_activa = wx.CheckBox(panel, label=_("Regla activa"), name=_("Regla activa"))
        self.cb_activa.SetValue(True)
        s.Add(self.cb_activa, 0, wx.ALL, 4)
        panel.SetSizer(s)

        raiz = wx.BoxSizer(wx.VERTICAL)
        raiz.Add(panel, 1, wx.EXPAND)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Guardar regla"), name=_("Guardar la regla"))
        ok.Bind(wx.EVT_BUTTON, self._on_guardar)
        botones.Add(ok, 0, wx.ALL, 6)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cancela&r")), 0, wx.ALL, 6)
        raiz.Add(botones, 0, wx.ALIGN_RIGHT)
        self.SetSizer(raiz)
        habilitar_escape_cierre(self)
        if regla_id:
            self._cargar(regla_id)
        self._ajustar_expr()
        self.Layout()
        self.CenterOnParent()
        self.txt_titulo.SetFocus()

    def _ajustar_expr(self):
        self.txt_expr.Enable(self.choice_modo.GetSelection() == 2)

    def _cargar(self, regla_id):
        r = db.query_one(
            "SELECT title, pattern, field, action, action_value, active, COALESCE(conditions_json,''), "
            "COALESCE(match_mode,'all'), COALESCE(priority,0), COALESCE(stop_processing,0), scope_feed_id, "
            "scope_category_id, COALESCE(actions_json,'') FROM rss_rules WHERE id = ?", (regla_id,))
        if not r:
            return
        self.txt_titulo.SetValue(r[0] or "")
        campos, ops = [k for k, _n in _CAMPOS], [k for k, _n in _OPERADORES]
        for (campo, op, patron), c in zip(self._condiciones, _condiciones_de_fila(r[1], r[2], r[6])):
            campo.SetSelection(campos.index(c["field"]) if c.get("field") in campos else 0)
            op.SetSelection(ops.index(c["op"]) if c.get("op") in ops else 0)
            patron.SetValue(c.get("pattern", ""))
        modo = r[7] or "all"
        if modo.startswith("expr:"):
            self.choice_modo.SetSelection(2)
            self.txt_expr.SetValue(modo[5:])
        else:
            self.choice_modo.SetSelection(1 if modo == "any" else 0)
        try:
            acciones = json.loads(r[12]) if r[12] else []
        except ValueError:
            acciones = []
        for a in acciones or [{"action": r[3], "value": r[4]}]:
            if a.get("action") in self.cb_acciones:
                self.cb_acciones[a["action"]].SetValue(True)
            elif a.get("action") == "tag":
                ids = [str(i) for i, _n in self._tags]
                if str(a.get("value")) in ids:
                    self.choice_tag.SetSelection(ids.index(str(a["value"])) + 1)
            elif a.get("action") == "categorize":
                ids = [str(i) for i, _n in self._cats]
                if str(a.get("value")) in ids:
                    self.choice_mover.SetSelection(ids.index(str(a["value"])) + 1)
        if r[10]:
            ids = [i for i, _t in self._feeds]
            if r[10] in ids:
                self.choice_ambito.SetSelection(ids.index(r[10]) + 1)
        elif r[11]:
            ids = [i for i, _n in self._cats]
            if r[11] in ids:
                self.choice_ambito.SetSelection(len(self._feeds) + ids.index(r[11]) + 1)
        self.spin_prioridad.SetValue(int(r[8]))
        self.cb_detener.SetValue(bool(r[9]))
        self.cb_activa.SetValue(bool(r[5]))

    def _on_guardar(self, event):
        titulo = self.txt_titulo.GetValue().strip()
        condiciones = []
        for campo, op, patron in self._condiciones:
            texto = patron.GetValue().strip()
            if not texto:
                continue
            clave_op = _OPERADORES[op.GetSelection()][0]
            if clave_op == "regex":
                try:
                    re.compile(texto)
                except re.error as e:
                    notificar(_("La expresión regular «{0}» no es válida: {1}").format(texto, e))
                    return
            condiciones.append({"field": _CAMPOS[campo.GetSelection()][0], "pattern": texto,
                                "op": clave_op, "is_regex": clave_op == "regex"})
        acciones = []
        for clave, cb in self.cb_acciones.items():
            if cb.GetValue():
                valor = "ready" if clave == "play_sound" else (titulo or _("Regla RSS") if clave == "notify" else None)
                acciones.append({"action": clave, "value": valor})
        if self.choice_tag.GetSelection() > 0:
            acciones.append({"action": "tag", "value": str(self._tags[self.choice_tag.GetSelection() - 1][0])})
        if self.choice_mover.GetSelection() > 0:
            acciones.append({"action": "categorize", "value": str(self._cats[self.choice_mover.GetSelection() - 1][0])})
        if not titulo or not condiciones or not acciones:
            notificar(_("Rellena el nombre, al menos una condición y al menos una acción."))
            return
        modo = ("all", "any", "expr")[self.choice_modo.GetSelection()]
        if modo == "expr":
            expr = self.txt_expr.GetValue().strip()
            try:
                evaluar_expresion(expr, [True] * len(condiciones))
            except ValueError as e:
                notificar(_("La combinación no es válida: {0}").format(e))
                self.txt_expr.SetFocus()
                return
            modo = "expr:" + expr
        sel = self.choice_ambito.GetSelection()
        scope_feed = scope_cat = None
        if 1 <= sel <= len(self._feeds):
            scope_feed = self._feeds[sel - 1][0]
        elif sel > len(self._feeds):
            scope_cat = self._cats[sel - 1 - len(self._feeds)][0]
        valores = (titulo, condiciones[0]["pattern"], acciones[0]["action"], acciones[0]["value"],
                   1 if self.cb_activa.GetValue() else 0, json.dumps(condiciones, ensure_ascii=False), modo,
                   self.spin_prioridad.GetValue(), 1 if self.cb_detener.GetValue() else 0, scope_feed, scope_cat,
                   json.dumps(acciones, ensure_ascii=False))
        if self.regla_id:
            db.execute("UPDATE rss_rules SET title = ?, pattern = ?, field = 'title', action = ?, action_value = ?, "
                       "active = ?, conditions_json = ?, match_mode = ?, priority = ?, stop_processing = ?, "
                       "scope_feed_id = ?, scope_category_id = ?, actions_json = ? WHERE id = ?",
                       valores + (self.regla_id,))
        else:
            db.execute("INSERT INTO rss_rules (title, pattern, field, action, action_value, active, conditions_json, "
                       "match_mode, priority, stop_processing, scope_feed_id, scope_category_id, actions_json, "
                       "account_id) VALUES (?, ?, 'title', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       valores + (self.account_id,))
        RSSRulesEngine.invalidar_plan(self.account_id)
        notificar(_("Regla guardada."))
        self.EndModal(wx.ID_OK)


class CarpetaInteligenteDialog(wx.Dialog):
    """Crear o EDITAR una carpeta inteligente con criterios estructurados (nada de SQL)."""

    def __init__(self, parent, account_id: int, carpeta=None):
        super().__init__(parent, title=_("Editar carpeta inteligente") if carpeta else _("Nueva carpeta inteligente"),
                         size=(580, 680), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Carpeta inteligente de RSS"))
        self.account_id = account_id
        self.resultado = None
        c = (carpeta or {}).get("criteria") or {}
        panel = wx.ScrolledWindow(self)
        panel.SetScrollRate(0, 16)
        s = wx.BoxSizer(wx.VERTICAL)

        def _texto(etiqueta, nombre, valor=""):
            s.Add(wx.StaticText(panel, label=etiqueta), 0, wx.ALL, 4)
            t = AccessibleTextCtrl(panel, value=valor, name=nombre)
            s.Add(t, 0, wx.EXPAND | wx.ALL, 4)
            return t
        self.txt_nombre = _texto(_("&Nombre de la carpeta:"), _("Nombre de la carpeta inteligente"),
                                 (carpeta or {}).get("name", ""))
        s.Add(wx.StaticText(panel, label=_("Estado de lectura:")), 0, wx.ALL, 4)
        self.choice_leido = wx.Choice(panel, choices=[_("Da igual"), _("Solo no leídos"), _("Solo leídos")],
                                      name=_("Filtro por estado de lectura"))
        self.choice_leido.SetSelection({None: 0, 0: 1, 1: 2}.get(c.get("read"), 0))
        s.Add(self.choice_leido, 0, wx.EXPAND | wx.ALL, 4)
        self.cb_fav = wx.CheckBox(panel, label=_("Solo destacados"), name=_("Solo destacados"))
        self.cb_tarde = wx.CheckBox(panel, label=_("Solo leer más tarde"), name=_("Solo leer más tarde"))
        self.cb_media = wx.CheckBox(panel, label=_("Solo con audio o vídeo (podcasts)"), name=_("Solo con multimedia"))
        self.cb_corregidos = wx.CheckBox(panel, label=_("Solo los corregidos en origen (tienen versiones anteriores)"),
                                         name=_("Solo artículos corregidos en origen"))
        for cb, clave in ((self.cb_fav, "favorite"), (self.cb_tarde, "read_later"), (self.cb_media, "has_media"),
                          (self.cb_corregidos, "updated")):
            cb.SetValue(bool(c.get(clave)))
            s.Add(cb, 0, wx.ALL, 4)
        s.Add(wx.StaticText(panel, label=_("Origen:")), 0, wx.ALL, 4)
        self._feeds = db.query("SELECT id, title FROM rss_feeds WHERE account_id = ? ORDER BY title", (account_id,))
        self._cats = db.query("SELECT id, name FROM rss_categories WHERE account_id = ? ORDER BY name", (account_id,))
        self.choice_origen = wx.Choice(panel, choices=(
            [_("Todos los feeds")] + [_("Solo el feed: {0}").format(t) for _i, t in self._feeds]
            + [_("Solo la carpeta: {0}").format(n) for _i, n in self._cats]), name=_("Origen de los artículos"))
        sel = 0
        if c.get("feed_id") in [i for i, _t in self._feeds]:
            sel = [i for i, _t in self._feeds].index(c["feed_id"]) + 1
        elif c.get("category_id") in [i for i, _n in self._cats]:
            sel = len(self._feeds) + [i for i, _n in self._cats].index(c["category_id"]) + 1
        self.choice_origen.SetSelection(sel)
        s.Add(self.choice_origen, 0, wx.EXPAND | wx.ALL, 4)
        s.Add(wx.StaticText(panel, label=_("Antigüedad máxima:")), 0, wx.ALL, 4)
        self._dias = (0, 1, 7, 30, 365)
        self.choice_dias = wx.Choice(panel, choices=[_("Sin límite"), _("Último día"), _("Última semana"),
                                                     _("Último mes"), _("Último año")],
                                     name=_("Antigüedad máxima de los artículos"))
        self.choice_dias.SetSelection(self._dias.index(c.get("days")) if c.get("days") in self._dias else 0)
        s.Add(self.choice_dias, 0, wx.EXPAND | wx.ALL, 4)
        self.txt_texto = _texto(_("Que contengan el &texto (título o contenido):"), _("Texto que deben contener"),
                                c.get("text", ""))
        self.txt_no = _texto(_("Que NO contengan el texto:"), _("Texto que no deben contener"), c.get("text_not", ""))
        self.txt_autor = _texto(_("Autor (contiene):"), _("Autor de los artículos"), c.get("author", ""))
        self.txt_link = _texto(_("Dirección del artículo (contiene):"), _("Dirección de los artículos"),
                               c.get("link", ""))
        panel.SetSizer(s)
        raiz = wx.BoxSizer(wx.VERTICAL)
        raiz.Add(panel, 1, wx.EXPAND)
        botones = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(self, wx.ID_OK, _("&Guardar carpeta"), name=_("Guardar la carpeta inteligente"))
        ok.Bind(wx.EVT_BUTTON, self._on_ok)
        botones.Add(ok, 0, wx.ALL, 6)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("Cancela&r")), 0, wx.ALL, 6)
        raiz.Add(botones, 0, wx.ALIGN_RIGHT)
        self.SetSizer(raiz)
        habilitar_escape_cierre(self)
        self.Layout()
        self.CenterOnParent()
        self.txt_nombre.SetFocus()

    def _on_ok(self, event):
        nombre = self.txt_nombre.GetValue().strip()
        if not nombre:
            notificar(_("Escribe un nombre para la carpeta."))
            return
        criterios = {}
        leido = self.choice_leido.GetSelection()
        if leido:
            criterios["read"] = 0 if leido == 1 else 1
        for cb, clave in ((self.cb_fav, "favorite"), (self.cb_tarde, "read_later"), (self.cb_media, "has_media"),
                          (self.cb_corregidos, "updated")):
            if cb.GetValue():
                criterios[clave] = 1
        sel = self.choice_origen.GetSelection()
        if 1 <= sel <= len(self._feeds):
            criterios["feed_id"] = self._feeds[sel - 1][0]
        elif sel > len(self._feeds):
            criterios["category_id"] = self._cats[sel - 1 - len(self._feeds)][0]
        if self._dias[self.choice_dias.GetSelection()]:
            criterios["days"] = self._dias[self.choice_dias.GetSelection()]
        for ctrl, clave in ((self.txt_texto, "text"), (self.txt_no, "text_not"), (self.txt_autor, "author"),
                            (self.txt_link, "link")):
            if ctrl.GetValue().strip():
                criterios[clave] = ctrl.GetValue().strip()
        if not criterios:
            notificar(_("Elige al menos un criterio (si no, ya tienes el buffer «Todos»)."))
            return
        self.resultado = (nombre, criterios)
        self.EndModal(wx.ID_OK)


class CarpetasInteligentesDialog(wx.Dialog):
    """Gestor de carpetas inteligentes: lista, crear, editar y eliminar (aparecen en el árbol)."""

    def __init__(self, parent, account_id: int):
        super().__init__(parent, title=_("Carpetas inteligentes"), size=(620, 440),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Gestor de carpetas inteligentes de RSS"))
        self.account_id = account_id
        self.hubo_cambios = False
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Tus carpetas inteligentes (se abren desde el árbol de RSS):")), 0, wx.ALL, 6)
        self.lista = AccessibleListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
                                        name=_("Lista de carpetas inteligentes"))
        self.lista.InsertColumn(0, _("Nombre"), width=200)
        self.lista.InsertColumn(1, _("Criterios"), width=360)
        self.lista.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self._on_editar(None))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 6)
        fila = wx.BoxSizer(wx.HORIZONTAL)
        for etiqueta, accion in ((_("&Nueva..."), self._on_nueva), (_("&Editar..."), self._on_editar),
                                 (_("E&liminar"), self._on_borrar)):
            b = wx.Button(self, label=etiqueta)
            b.Bind(wx.EVT_BUTTON, accion)
            fila.Add(b, 0, wx.ALL, 4)
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 4)
        s.Add(fila, 0, wx.EXPAND)
        self.SetSizer(s)
        habilitar_escape_cierre(self)
        self.CenterOnParent()
        self._cargar()

    @staticmethod
    def _describe(c):
        partes = []
        if c.get("read") == 0:
            partes.append(_("no leídos"))
        elif c.get("read") == 1:
            partes.append(_("leídos"))
        for clave, texto in (("favorite", _("destacados")), ("read_later", _("leer más tarde")),
                             ("has_media", _("con multimedia")), ("updated", _("corregidos en origen")),
                             ("feed_id", _("de un feed")), ("category_id", _("de una carpeta"))):
            if c.get(clave):
                partes.append(texto)
        if c.get("days"):
            partes.append(_("últimos {0} días").format(c["days"]))
        for clave, plantilla in (("text", _("con «{0}»")), ("text_not", _("sin «{0}»")),
                                 ("author", _("autor «{0}»")), ("link", _("dirección «{0}»"))):
            if c.get(clave):
                partes.append(plantilla.format(c[clave]))
        return ", ".join(partes)

    def _cargar(self, foco: int = 0):
        self.lista.DeleteAllItems()
        self.lista.limpiar_lecturas()
        self._items = rss_service.obtener_carpetas_inteligentes(self.account_id)
        for i, it in enumerate(self._items):
            desc = self._describe(it["criteria"])
            self.lista.InsertItem(i, it["name"])
            self.lista.SetItem(i, 1, desc)
            self.lista.establecer_lectura(i, f"{it['name']}. {desc}")
        if self._items:
            foco = max(0, min(foco, len(self._items) - 1))
            self.lista.Focus(foco)
            self.lista.Select(foco)

    def _on_nueva(self, event):
        dlg = CarpetaInteligenteDialog(self, self.account_id)
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            rss_service.crear_carpeta_inteligente(self.account_id, *dlg.resultado)
            self.hubo_cambios = True
            notificar(_("Carpeta inteligente creada; la verás en el árbol de RSS."))
            self._cargar(len(self._items))
        dlg.Destroy()

    def _on_editar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._items)):
            return
        dlg = CarpetaInteligenteDialog(self, self.account_id, carpeta=self._items[idx])
        if dlg.ShowModal() == wx.ID_OK and dlg.resultado:
            rss_service.editar_carpeta_inteligente(self._items[idx]["id"], *dlg.resultado)
            self.hubo_cambios = True
            notificar(_("Carpeta inteligente guardada."))
            self._cargar(idx)
        dlg.Destroy()

    def _on_borrar(self, event):
        idx = self.lista.GetFocusedItem()
        if not (0 <= idx < len(self._items)):
            return
        it = self._items[idx]
        if confirmar(self, _("¿Eliminar la carpeta inteligente «{0}»? (Los artículos no se tocan.)").format(it["name"]),
                     _("Eliminar carpeta inteligente")):
            rss_service.borrar_carpeta_inteligente(it["id"])
            self.hubo_cambios = True
            self._cargar(idx)
