# -*- coding: utf-8 -*-
"""Pruebas del subsistema XMPP (auditoría 2026-07-13).

Cubren la lógica PURA de seguridad añadida por la auditoría, sin depender de un servidor XMPP ni de
slixmpp: bloqueo de adjuntos ejecutables (XMPP-008) y validación anti-SSRF de descargas (XMPP-007).
"""
import pytest

from app.services.xmpp.xmpp_medios import es_extension_activa, _url_apunta_a_interno


# ---------------- XMPP-008: adjuntos ejecutables ----------------
@pytest.mark.parametrize("nombre,activo", [
    ("virus.exe", True), ("payload.PS1", True), ("acceso.lnk", True), ("macro.vbs", True),
    ("script.js", True), ("app.apk", True),
    ("foto.jpg", False), ("documento.pdf", False), ("cancion.opus", False),
    # Con parámetros de query en la URL, la extensión real sigue detectándose.
    ("https://srv/upload/foto.jpg?token=abc", False),
    ("https://srv/upload/run.exe?x=1", True),
])
def test_extension_activa(nombre, activo):
    assert es_extension_activa(nombre) is activo


# ---------------- XMPP-007: anti-SSRF en descargas ----------------
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x",
    "http://localhost/x",
    "http://169.254.169.254/latest/meta-data",   # metadata cloud (link-local)
    "http://10.0.0.5/x",                          # privada
    "http://192.168.1.10/x",                      # privada
    "https://[::1]/x",                            # loopback IPv6
    "https:///sin-host",                          # sin host -> se bloquea
])
def test_url_interna_bloqueada(url):
    assert _url_apunta_a_interno(url) is True


def test_url_publica_permitida():
    # Un host público bien resuelto NO se bloquea (usa una IP pública literal para no depender de DNS).
    assert _url_apunta_a_interno("https://1.1.1.1/foto.jpg") is False
