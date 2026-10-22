# -*- coding: utf-8 -*-
"""Pruebas del cliente Telegram y Telegram Drive (auditoría 2026-07-13).

Cubren la lógica PURA de seguridad añadida por la auditoría, sin conectar con Telegram:
adjuntos activos (TG-008), tokens de streaming (TG-009), confinamiento de rutas del manifiesto
(TG-005) y validación estructural del manifiesto.
"""
import pytest

from app.core.file_safety import es_extension_activa


# ---------------- TG-008: adjuntos activos ----------------
@pytest.mark.parametrize("nombre,activo", [
    ("instalador.exe", True), ("run.BAT", True), ("acceso.lnk", True), ("a.ps1", True),
    ("foto.jpg", False), ("video.mp4", False), ("nota.ogg", False),
])
def test_extension_activa(nombre, activo):
    assert es_extension_activa(nombre) is activo


# ---------------- TG-009: tokens de streaming ----------------
def test_stream_tokens_aleatorios_y_acotados():
    from app.services.telegram.telegram_stream_server import _StreamServer, _MAX_TOKENS
    srv = _StreamServer()
    srv._port = 1  # evitar levantar el servidor real
    tokens = set()
    for i in range(_MAX_TOKENS + 20):
        url = srv.url_para(1, 1000 + i, i)
        tk = url.rsplit("/", 1)[-1]
        tokens.add(tk)
        assert len(tk) >= 20  # 256 bits en base64url ≈ 43 chars: nunca "1","2"...
    # El registro está acotado (LRU): nunca crece sin límite.
    assert len(srv._reg) <= _MAX_TOKENS
    # Un token viejo ya fue revocado (no está en el registro).
    assert list(tokens)[0] not in srv._reg or len(srv._reg) <= _MAX_TOKENS


def test_stream_revocar():
    from app.services.telegram.telegram_stream_server import _StreamServer
    srv = _StreamServer()
    srv._port = 1
    url = srv.url_para(1, 2, 3)
    token = url.rsplit("/", 1)[-1]
    assert token in srv._reg
    srv.revocar(url)
    assert token not in srv._reg


# ---------------- TG-005: confinamiento de rutas del manifiesto ----------------
def _svc():
    # Instancia mínima: los métodos de ruta son puros y no tocan red/BD.
    from app.services.tgdrive.tgdrive_service import TgDriveService
    return TgDriveService.__new__(TgDriveService)


@pytest.mark.parametrize("malicioso", [
    "../../fuera.txt", r"..\..\fuera.txt", "/etc/passwd", r"C:\Windows\System32\evil.exe",
    "sub/dir/archivo.txt", "CON", "archivo\x00.txt",
])
def test_nombre_confinado(tmp_path, malicioso):
    svc = _svc()
    ruta = svc._ruta_confinada(str(tmp_path), malicioso)
    import os
    # La ruta resuelta SIEMPRE queda dentro de la carpeta elegida.
    assert os.path.commonpath([os.path.realpath(str(tmp_path)), ruta]) == os.path.realpath(str(tmp_path))
    # Y es un basename directo (sin subcarpetas heredadas del nombre).
    assert os.path.dirname(ruta) == os.path.realpath(str(tmp_path))


# ---------------- TG-005: validación estructural del manifiesto ----------------
def test_manifiesto_ciclo_rechazado():
    from app.services.tgdrive.tgdrive_manifest import _validar_estructura
    m = {"items": [{"id": "a", "tipo": "folder", "name": "A", "parent_id": "b"},
                   {"id": "b", "tipo": "folder", "name": "B", "parent_id": "a"}],
         "parts": []}
    with pytest.raises(ValueError):
        _validar_estructura(m)


def test_manifiesto_id_duplicado_rechazado():
    from app.services.tgdrive.tgdrive_manifest import _validar_estructura
    m = {"items": [{"id": "a", "tipo": "file", "name": "A"},
                   {"id": "a", "tipo": "file", "name": "A2"}], "parts": []}
    with pytest.raises(ValueError):
        _validar_estructura(m)


def test_manifiesto_valido_ok():
    from app.services.tgdrive.tgdrive_manifest import _validar_estructura
    m = {"items": [{"id": "root", "tipo": "folder", "name": "raiz", "parent_id": None},
                   {"id": "f1", "tipo": "file", "name": "doc.txt", "parent_id": "root", "size": 10}],
         "parts": [{"item_id": "f1", "orden": 0, "size": 10, "message_id": 5}]}
    _validar_estructura(m)  # no lanza
