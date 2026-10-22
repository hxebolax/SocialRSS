# -*- coding: utf-8 -*-
"""Pruebas del RESPALDO en GitHub del actualizador: si el servidor propio no responde (o no tiene
el zip), el manifiesto y el paquete se toman de las releases de hxebolax/SocialRSS. La firma y el
SHA-256 se siguen verificando igual, vengan de donde vengan."""
import io
import json

import pytest

from app.services.updater import update_service as upd


class _Resp(io.BytesIO):
    def __init__(self, datos):
        super().__init__(datos)
        self.headers = {"Content-Length": str(len(datos))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _urlopen_falso(respuestas, pedidas):
    """urlopen simulado: 'respuestas' es {url: bytes | Exception}; apunta las URL pedidas."""
    def _abrir(req, timeout=None):
        url = req.full_url
        pedidas.append(url)
        r = respuestas.get(url, OSError("sin conexión"))
        if isinstance(r, Exception):
            raise r
        return _Resp(r)
    return _abrir


MANIFIESTO = json.dumps({"version": "9.9.9", "sha256": "ab" * 32}).encode("utf-8")
URL_SERVIDOR_JSON = f"{upd.URL_BASE}/version.json"
URL_GITHUB_JSON = f"{upd.URL_GITHUB_RELEASES}/latest/download/version.json"


def test_con_el_servidor_disponible_no_se_pregunta_a_github(monkeypatch):
    pedidas = []
    monkeypatch.setattr(upd.urllib.request, "urlopen",
                        _urlopen_falso({URL_SERVIDOR_JSON: MANIFIESTO}, pedidas))
    assert upd.obtener_version_remota() == "9.9.9"
    assert pedidas == [URL_SERVIDOR_JSON]


def test_si_el_servidor_no_responde_se_usa_la_ultima_release(monkeypatch):
    pedidas = []
    monkeypatch.setattr(upd.urllib.request, "urlopen",
                        _urlopen_falso({URL_GITHUB_JSON: MANIFIESTO}, pedidas))
    assert upd.obtener_info_remota()["version"] == "9.9.9"
    assert pedidas == [URL_SERVIDOR_JSON, URL_GITHUB_JSON]


def test_sin_servidor_ni_github_no_hay_version(monkeypatch):
    monkeypatch.setattr(upd.urllib.request, "urlopen", _urlopen_falso({}, []))
    assert upd.obtener_version_remota() is None


def test_el_zip_se_baja_de_github_si_el_servidor_falla(monkeypatch, tmp_path):
    pedidas = []
    url_gh = upd.url_del_zip_github("9.9.9")
    assert url_gh.endswith("/download/v9.9.9/SocialRSS_v9.9.9.zip")
    monkeypatch.setattr(upd.urllib.request, "urlopen",
                        _urlopen_falso({url_gh: b"zip de github"}, pedidas))
    destino = tmp_path / "a.zip"
    upd.descargar_zip("9.9.9", str(destino))
    assert pedidas == [upd.url_del_zip("9.9.9"), url_gh]
    assert destino.read_bytes() == b"zip de github"


def test_si_el_servidor_tiene_el_zip_no_se_toca_github(monkeypatch, tmp_path):
    pedidas = []
    monkeypatch.setattr(upd.urllib.request, "urlopen",
                        _urlopen_falso({upd.url_del_zip("9.9.9"): b"zip"}, pedidas))
    upd.descargar_zip("9.9.9", str(tmp_path / "a.zip"))
    assert pedidas == [upd.url_del_zip("9.9.9")]


def test_cancelar_no_salta_a_github(monkeypatch, tmp_path):
    pedidas = []
    monkeypatch.setattr(upd.urllib.request, "urlopen",
                        _urlopen_falso({upd.url_del_zip("9.9.9"): b"zip"}, pedidas))
    with pytest.raises(upd.DescargaCancelada):
        upd.descargar_zip("9.9.9", str(tmp_path / "a.zip"), cancelado=lambda: True)
    assert pedidas == [upd.url_del_zip("9.9.9")]
