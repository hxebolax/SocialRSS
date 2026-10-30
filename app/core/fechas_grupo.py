# -*- coding: utf-8 -*-
"""Etiqueta de grupo por fecha ("Hoy", "Ayer"...) para las listas de artículos agrupadas por fecha."""
from datetime import datetime


def bucket_fecha(fecha_iso) -> str:
    """Etiqueta del grupo de una fecha (Hoy, Ayer, Esta semana, Este mes o AAAA-MM); vacía si no se puede interpretar."""
    try:
        d = datetime.fromisoformat(str(fecha_iso)).date()
        dias = (datetime.now().date() - d).days
        if dias <= 0:
            return _("Hoy")
        if dias == 1:
            return _("Ayer")
        if dias < 7:
            return _("Esta semana")
        if dias < 30:
            return _("Este mes")
        return d.strftime("%Y-%m")
    except Exception:
        return ""
