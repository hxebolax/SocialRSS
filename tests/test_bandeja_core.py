# -*- coding: utf-8 -*-
from datetime import datetime

from app.core.bandeja import parse_fecha


def test_parse_fecha_normaliza_zonas():
    utc = parse_fecha("2026-01-01T10:00:00Z")
    con_zona = parse_fecha("2026-01-01T11:00:00+01:00")
    assert utc == con_zona and utc.tzinfo is None


def test_parse_fecha_tolerante():
    assert parse_fecha("") == datetime.min
    assert parse_fecha("basura") == datetime.min
    assert parse_fecha("2026-01-01 10:00:00") == datetime(2026, 1, 1, 10, 0)
