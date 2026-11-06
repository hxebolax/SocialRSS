# -*- coding: utf-8 -*-
"""Actualizador en Linux: el version.json único lleva un bloque "linux" con su paquete .tar.gz, su
sha256 y su propia firma; el paquete se extrae con las mismas defensas que el ZIP de Windows y un
script de shell aplica la actualización al cerrarse la app. Corre en las dos plataformas."""
import io
import json
import os
import sys
import tarfile

import pytest

from app.services.updater import update_service as upd

pytest.importorskip("cryptography")
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import empaquetar_linux  # noqa: E402
import firmar_version  # noqa: E402


MANIFIESTO = {"version": "9.9.9", "sha256": "aa" * 32, "size": 10,
              "linux": {"file": "SocialRSS_v9.9.9_linux.tar.gz", "sha256": "bb" * 32, "size": 5}}


def test_manifiesto_por_plataforma():
    assert upd.manifiesto_de_plataforma(MANIFIESTO, windows=True) is MANIFIESTO
    linux = upd.manifiesto_de_plataforma(MANIFIESTO, windows=False)
    assert linux["version"] == "9.9.9" and linux["sha256"] == "bb" * 32
    # Una versión antigua, sin bloque linux, no tiene paquete para Linux.
    assert upd.manifiesto_de_plataforma({"version": "1.0.0", "sha256": "aa" * 32}, windows=False) is None


def test_nombre_del_paquete_por_plataforma():
    assert upd.nombre_paquete("9.9.9", windows=True) == "SocialRSS_v9.9.9.zip"
    assert upd.nombre_paquete("9.9.9", windows=False) == "SocialRSS_v9.9.9_linux.tar.gz"


def test_firma_del_bloque_linux_se_verifica(tmp_path, monkeypatch):
    priv = Ed25519PrivateKey.generate()
    clave = tmp_path / "clave.key"
    clave.write_bytes(priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                         serialization.NoEncryption()))
    publica = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    paquete = tmp_path / "p.tar.gz"
    paquete.write_bytes(b"paquete de linux")
    datos = json.loads(json.dumps(MANIFIESTO))
    datos["linux"]["sha256"] = upd._sha256_de_archivo(paquete)
    ruta_json = tmp_path / "version.json"
    ruta_json.write_text(json.dumps(datos), encoding="utf-8")
    firmar_version.firmar(str(clave), str(ruta_json))
    firmado = json.loads(ruta_json.read_text(encoding="utf-8"))
    assert firmado["firma_ed25519"] and firmado["linux"]["firma_ed25519"]
    assert firmado["firma_ed25519"] != firmado["linux"]["firma_ed25519"]

    monkeypatch.setattr(upd, "_CLAVE_PUBLICA_ED25519", publica)
    linux = upd.manifiesto_de_plataforma(firmado, windows=False)
    upd.verificar_manifiesto(linux)
    upd.verificar_zip(paquete, linux["sha256"])
    upd.verificar_manifiesto(upd.manifiesto_de_plataforma(firmado, windows=True))
    # La firma de Windows no vale para el paquete de Linux.
    falso = dict(linux, firma_ed25519=firmado["firma_ed25519"])
    with pytest.raises(ValueError):
        upd.verificar_manifiesto(falso)


def _tar(ruta, miembros):
    """miembros: [(nombre, bytes | None para carpeta | 'symlink', modo)]."""
    with tarfile.open(ruta, "w:gz") as tf:
        for nombre, datos, modo in miembros:
            ti = tarfile.TarInfo(nombre)
            ti.mode = modo
            if datos is None:
                ti.type = tarfile.DIRTYPE
                tf.addfile(ti)
            elif datos == "symlink":
                ti.type = tarfile.SYMTYPE
                ti.linkname = "/etc/passwd"
                tf.addfile(ti)
            else:
                ti.size = len(datos)
                tf.addfile(ti, io.BytesIO(datos))


def test_extraer_tar_quita_la_raiz_y_conserva_ejecutables(tmp_path):
    paquete = tmp_path / "p.tar.gz"
    _tar(paquete, [("SocialRSS", None, 0o755), ("SocialRSS/main.py", b"print()", 0o644),
                   ("SocialRSS/scripts", None, 0o755), ("SocialRSS/scripts/socialrss", b"#!/bin/sh", 0o755)])
    destino = tmp_path / "temp"
    vistos = []
    upd.extraer_paquete(str(paquete), str(destino), progreso=lambda h, t: vistos.append((h, t)))
    assert (destino / "main.py").read_bytes() == b"print()"
    assert (destino / "scripts" / "socialrss").exists()
    if os.name != "nt":
        assert os.stat(destino / "scripts" / "socialrss").st_mode & 0o111
    assert vistos[-1] == (4, 4)


def test_extraer_tar_no_escribe_fuera_ni_sigue_enlaces(tmp_path):
    paquete = tmp_path / "p.tar.gz"
    _tar(paquete, [("SocialRSS/main.py", b"x", 0o644), ("SocialRSS/../../fuera.txt", b"malo", 0o644),
                   ("SocialRSS/enlace", "symlink", 0o777)])
    destino = tmp_path / "a" / "temp"
    upd.extraer_tar(str(paquete), str(destino))
    assert not (tmp_path / "fuera.txt").exists() and not (tmp_path / "a" / "fuera.txt").exists()
    assert not (destino / "enlace").exists()


def test_extraer_tar_rechaza_un_paquete_ajeno(tmp_path):
    paquete = tmp_path / "p.tar.gz"
    _tar(paquete, [("otra/cosa.txt", b"x", 0o644)])
    with pytest.raises(ValueError):
        upd.extraer_tar(str(paquete), str(tmp_path / "temp"))


def test_script_de_linux(tmp_path):
    base = tmp_path / "Social RSS"          # con espacio: las rutas van entrecomilladas
    base.mkdir()
    script = upd._crear_script_linux(str(base), str(base / "_actualizacion_temp"),
                                     str(base / "_actualizacion_socialrss.tar.gz"))
    texto = open(script, encoding="utf-8").read()
    assert texto.startswith("#!/bin/sh\n") and "\r" not in texto
    assert f"kill -0 {os.getpid()}" in texto
    assert "cp -a" in texto and "requirements-linux.txt" in texto and 'rm -f "$0"' in texto
    assert "'" + str(base) in texto.replace('"', "'")
    assert texto.rstrip().splitlines()[-1].startswith("exec ")


@pytest.mark.skipif(not os.path.isdir(os.path.join(empaquetar_linux.RAIZ, ".git")),
                    reason="el paquete se arma con la lista de archivos de git")
def test_el_paquete_de_linux_no_lleva_nada_de_windows():
    rels = empaquetar_linux.archivos_del_paquete()
    for imprescindible in ("main.py", "instalar.sh", "requirements-linux.txt", "scripts/socialrss",
                           "socialrss.desktop", "app/gtk/main.py", "app/core/plataforma.py", "LICENSE"):
        assert imprescindible in rels
    assert any(r.startswith("data/locales/") for r in rels)
    for r in rels:
        assert not r.startswith(("__pip__/", "nvda_addon/", "tests/", "tools/", "data/bin/", "build/", "adjuntos/"))
        assert not r.endswith((".bat", ".cmd", ".exe", ".pyc")) and r not in ("setup.py", "requirements.txt")
        assert not r.startswith("packaging/")


def test_pkgbuild_de_arch_sigue_a_la_release(tmp_path):
    pkgbuild = tmp_path / "PKGBUILD"
    pkgbuild.write_text("pkgname=socialrss\npkgver=1.0.29\npkgrel=3\n"
                        "sha256sums=('" + "aa" * 32 + "'\n            '" + "cc" * 32 + "')\n",
                        encoding="utf-8", newline="")
    assert empaquetar_linux.actualizar_pkgbuild("1.0.30", "bb" * 32, str(pkgbuild))
    texto = pkgbuild.read_text(encoding="utf-8")
    assert "pkgver=1.0.30\npkgrel=1\n" in texto
    assert "sha256sums=('" + "bb" * 32 + "'" in texto and "cc" * 32 in texto
    # Misma versión: solo cambia la suma y pkgrel se respeta.
    pkgbuild.write_text(texto.replace("pkgrel=1", "pkgrel=2"), encoding="utf-8", newline="")
    assert empaquetar_linux.actualizar_pkgbuild("1.0.30", "dd" * 32, str(pkgbuild))
    assert "pkgrel=2" in pkgbuild.read_text(encoding="utf-8")
    assert not empaquetar_linux.actualizar_pkgbuild("1.0.30", "dd" * 32, str(pkgbuild))
    assert not empaquetar_linux.actualizar_pkgbuild("1.0.30", "dd" * 32, str(tmp_path / "no_existe"))


def test_un_paquete_del_sistema_no_se_actualiza_solo(tmp_path, monkeypatch):
    """El lanzador del paquete de Arch pone SOCIALRSS_PAQUETE: aunque la carpeta se pudiera escribir,
    la actualización es cosa de pacman y la app no intenta sobreescribirse."""
    monkeypatch.setattr(upd, "ES_WINDOWS", False)
    monkeypatch.setattr(upd, "ROOT_DIR", tmp_path)          # carpeta escribible y sin .git
    monkeypatch.delenv("SOCIALRSS_PAQUETE", raising=False)
    assert upd.paquete_del_sistema() == ""
    assert upd.actualizacion_disponible_para_instalar()
    monkeypatch.setenv("SOCIALRSS_PAQUETE", " Arch ")
    assert upd.paquete_del_sistema() == "arch"
    assert not upd.actualizacion_disponible_para_instalar()


def test_el_lanzador_de_arch_avisa_de_que_es_un_paquete():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(raiz, "packaging", "arch", "socialrss.sh"), encoding="utf-8") as f:
        assert "export SOCIALRSS_PAQUETE=arch" in f.read()
