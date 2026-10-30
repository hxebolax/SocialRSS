# -*- coding: utf-8 -*-
"""Clave maestra de secretos en Linux: llavero del escritorio y, si no hay, fichero 0600."""
import os
import stat

import pytest

pytest.importorskip("cryptography")
from app.core import security


@pytest.mark.skipif(os.name == "nt", reason="rama de Linux/macOS")
def test_sin_llavero_la_clave_va_a_un_fichero_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "get_data_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(security, "_secret_tool", lambda *a, **k: None)   # sin llavero
    m = security.SecurityManager()
    assert m.guardar_credencial("cuenta", "token", "secreto") is True
    clave = tmp_path / "config" / "secrets.key"
    assert clave.exists()
    assert stat.S_IMODE(clave.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "config" / "secrets.enc").stat().st_mode) == 0o600
    # Una instancia nueva (otro arranque) descifra con la misma clave.
    assert security.SecurityManager().obtener_credencial("cuenta", "token") == "secreto"
    # Y el contenido no queda en claro.
    assert b"secreto" not in (tmp_path / "config" / "secrets.enc").read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="rama de Linux/macOS")
def test_con_llavero_no_se_guarda_la_clave_en_disco(tmp_path, monkeypatch):
    almacen = {}

    def _falso(*args, entrada=None):
        if args[0] == "store":
            almacen["v"] = entrada
            return b""
        return almacen.get("v")
    monkeypatch.setattr(security, "get_data_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(security, "_secret_tool", _falso)
    m = security.SecurityManager()
    assert m.guardar_credencial("c", "k", "v") is True
    assert not (tmp_path / "config" / "secrets.key").exists()
    assert security.SecurityManager().obtener_credencial("c", "k") == "v"
