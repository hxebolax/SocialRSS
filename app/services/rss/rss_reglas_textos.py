# -*- coding: utf-8 -*-
"""Textos y utilidades de las reglas automáticas y las carpetas inteligentes de RSS, sin interfaz gráfica: los comparten
las ventanas de Windows (wx) y de Linux (GTK)."""
import builtins
import json
import re

if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

ACCIONES_SIMPLES = (("mark_read", _("Marcar como leído")), ("mark_favorite", _("Destacar")),
                     ("read_later", _("Leer más tarde")), ("play_sound", _("Reproducir un sonido")),
                     ("delete", _("Mover a la papelera")), ("no_avisar", _("No avisar de su llegada")))
NOMBRE_ACCION = dict(ACCIONES_SIMPLES, notify=_("Avisar por voz"), tag=_("Etiquetar"),
                      categorize=_("Mover el feed"))
CAMPOS = (("title", _("Título")), ("content", _("Contenido")), ("author", _("Autor")),
           ("link", _("Dirección del artículo")), ("feed", _("Nombre del feed")),
           ("all", _("Todo (título, contenido y autor)")))
OPERADORES = (("contiene", _("contiene")), ("no_contiene", _("no contiene")),
               ("igual", _("es igual a")), ("empieza", _("empieza por")),
               ("regex", _("cumple la expresión regular")))
N_CONDICIONES = 4


def condiciones_de_fila(pattern, field, conditions_json):
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


def resumen_regla(conds, modo, acciones):
    partes = []
    for i, c in enumerate(conds, start=1):
        campo = dict(CAMPOS).get(c.get("field"), c.get("field"))
        op = dict(OPERADORES).get(c.get("op"), c.get("op"))
        partes.append(f"{i}: {campo} {op} «{c.get('pattern', '')}»")
    union = {"all": _(" y "), "any": _(" o ")}.get(modo)
    texto = (union or "; ").join(partes)
    if modo and modo.startswith("expr:"):
        texto += " — " + _("combinación: {0}").format(modo[5:])
    nombres = ", ".join(NOMBRE_ACCION.get(a.get("action"), a.get("action") or "") for a in acciones)
    return f"{texto} → {nombres}"


def describir_criterios(c):
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
