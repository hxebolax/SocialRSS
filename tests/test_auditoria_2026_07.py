# -*- coding: utf-8 -*-
"""Pruebas de regresión de la auditoría técnica del 11-07-2026.

Protegen las rutas DESTRUCTIVAS y de seguridad corregidas: copia/restauración con
WAL, ZIP maliciosos, saneamiento de HTML, filtrado de protocolos, migración v2,
lectura dentro de transacciones, cierre del gestor de tareas y secretos DPAPI.
Se ejecutan aisladas en un perfil temporal (ver conftest.py)."""

import os
import json
import sqlite3
import zipfile

import pytest

from app.core.paths import get_data_path


# ---------------------------------------------------------------------------
# C1/C2/A4: copia de seguridad y restauración
# ---------------------------------------------------------------------------

def _preparar_datos_minimos():
    """BD SQLite real + configuración + sesiones anidadas en el perfil temporal."""
    db_path = get_data_path("database/social_rss.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE IF NOT EXISTS prueba (x INTEGER)")
    con.execute("DELETE FROM prueba")
    con.execute("INSERT INTO prueba VALUES (7)")
    con.commit()
    # OJO: la conexión queda ABIERTA (WAL activo, cambios posiblemente en el -wal)
    get_data_path("config").mkdir(parents=True, exist_ok=True)
    get_data_path("config/settings.json").write_text('{"general": {}}', encoding="utf-8")
    ses = get_data_path("sessions/matrix/1")
    ses.mkdir(parents=True, exist_ok=True)
    (ses / "store.db").write_bytes(b"e2ee-store")
    (get_data_path("sessions") / "tg.session").write_bytes(b"tg")
    return con


def test_backup_consistente_con_wal_activo():
    """La copia debe incluir los cambios confirmados aunque vivan aún en el -wal,
    y el ZIP debe contener también las sesiones anidadas (Matrix E2EE)."""
    from app.core import backup
    con = _preparar_datos_minimos()
    try:
        ruta = backup.crear_backup(str(get_data_path("backups") / "t.zip"))
        with zipfile.ZipFile(ruta) as zf:
            nombres = set(zf.namelist())
            assert "database/social_rss.db" in nombres
            assert "sessions/matrix/1/store.db" in nombres
            assert "sessions/tg.session" in nombres
            # La instantánea de la BD dentro del ZIP debe estar íntegra y con los datos.
            import tempfile
            tmp = tempfile.mktemp(suffix=".db")
            with zf.open("database/social_rss.db") as src, open(tmp, "wb") as dst:
                dst.write(src.read())
            c2 = sqlite3.connect(tmp)
            assert c2.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert c2.execute("SELECT x FROM prueba").fetchone() == (7,)
            c2.close()
            os.remove(tmp)
    finally:
        con.close()


def test_restauracion_diferida_y_aplicada():
    """restaurar_backup NO debe tocar los ficheros vivos: prepara una carpeta de espera
    y aplicar_restauracion_pendiente() la aplica (como haría el siguiente arranque)."""
    from app.core import backup
    con = _preparar_datos_minimos()
    con.close()
    ruta = backup.crear_backup(str(get_data_path("backups") / "t2.zip"))
    # Alterar el estado actual para comprobar que la restauración lo revierte.
    con = sqlite3.connect(str(get_data_path("database/social_rss.db")))
    con.execute("UPDATE prueba SET x = 99")
    con.commit()
    con.close()
    get_data_path("sessions/matrix/1/store.db").write_bytes(b"cambiado")

    assert backup.restaurar_backup(ruta) is True
    assert backup.hay_restauracion_pendiente()
    # Los ficheros reales siguen con el estado alterado (nada se pisó en caliente).
    con = sqlite3.connect(str(get_data_path("database/social_rss.db")))
    assert con.execute("SELECT x FROM prueba").fetchone() == (99,)
    con.close()

    assert backup.aplicar_restauracion_pendiente() is True
    assert not backup.hay_restauracion_pendiente()
    con = sqlite3.connect(str(get_data_path("database/social_rss.db")))
    assert con.execute("SELECT x FROM prueba").fetchone() == (7,)
    con.close()
    assert get_data_path("sessions/matrix/1/store.db").read_bytes() == b"e2ee-store"


def test_zip_malicioso_se_rechaza():
    """Miembros con rutas de escape o no esperados NO deben extraerse jamás."""
    from app.core import backup
    _preparar_datos_minimos()
    malo = get_data_path("backups") / "malo.zip"
    malo.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(malo, "w") as zf:
        zf.writestr("config/settings.json", "{}")               # miembro legítimo
        zf.writestr("../../fuera.txt", "escape")                # zip slip clásico
        zf.writestr("config/../../../fuera2.txt", "escape2")    # traversal anidado
        zf.writestr("C:/Windows/pwn.txt", "absoluto")           # ruta absoluta
        zf.writestr("cualquier/otro.bin", "no esperado")        # fuera de la lista blanca
    assert backup.restaurar_backup(str(malo)) is True
    staging = get_data_path("_restore_pending")
    extraidos = [str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_file()]
    # Solo el miembro legítimo (y el marcador READY).
    assert sorted(extraidos) == ["READY", os.path.join("config", "settings.json")]
    # Nada escapó del perfil temporal.
    assert not (get_data_path("") / ".." / ".." / "fuera.txt").resolve().exists()
    # Limpiar la restauración pendiente para no afectar a otras pruebas.
    import shutil
    shutil.rmtree(staging, ignore_errors=True)


def test_zip_sin_contenido_valido_se_rechaza():
    from app.core import backup
    vacio = get_data_path("backups") / "vacio.zip"
    vacio.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(vacio, "w") as zf:
        zf.writestr("nada.txt", "x")
    with pytest.raises(ValueError):
        backup.restaurar_backup(str(vacio))


# ---------------------------------------------------------------------------
# C3: saneamiento de HTML
# ---------------------------------------------------------------------------

def test_sanear_html_elimina_contenido_activo():
    from app.core.html_utils import sanear_html
    sucio = ('<p onclick="alert(1)">hola</p><script>alert(2)</script>'
             '<iframe src="https://mal"></iframe><object data="x"></object>'
             '<img src="https://a/b.png" onerror="alert(3)">'
             '<a href="javascript:alert(4)">j</a><a href="https://bien.com">ok</a>'
             '<style>body{display:none}</style><form><input></form>')
    limpio = sanear_html(sucio)
    for veneno in ("<script", "onclick", "onerror", "<iframe", "<object",
                   "javascript:", "<style", "<form", "<input"):
        assert veneno not in limpio.lower()
    assert "hola" in limpio
    assert 'href="https://bien.com"' in limpio


def test_html_a_documento_sanea_y_pone_csp():
    from app.core.html_utils import html_a_documento
    doc = html_a_documento("Título <x>", "<script>alert(1)</script><p>texto</p>")
    assert "<script>alert(1)</script>" not in doc
    assert "texto" in doc
    assert "Content-Security-Policy" in doc


# ---------------------------------------------------------------------------
# C4: filtrado de protocolos
# ---------------------------------------------------------------------------

def test_abrir_url_bloquea_protocolos_no_seguros():
    """Sin interfaz (no hay wx.App), un esquema no estándar se DENIEGA, no se abre."""
    from app.core.privacy import abrir_url
    assert abrir_url("ms-settings:network") is False
    assert abrir_url("javascript:alert(1)") is False
    assert abrir_url("vbscript:x") is False


def test_es_ruta_propia():
    from app.core.privacy import es_ruta_propia
    propia = get_data_path("temp") / "articulo.html"
    propia.parent.mkdir(parents=True, exist_ok=True)
    propia.write_text("x", encoding="utf-8")
    assert es_ruta_propia(str(propia)) is True
    assert es_ruta_propia(propia.as_uri()) is True
    assert es_ruta_propia("C:/Windows/system32/calc.exe") is False


# ---------------------------------------------------------------------------
# A6: migración v2 (cifrado por bloques)
# ---------------------------------------------------------------------------

def test_migracion_v2_ida_y_vuelta():
    from app.core import migration
    _preparar_datos_minimos()
    destino = str(get_data_path("") / "mig.srmig")
    migration.exportar_migracion(destino, "contraseña-larga-1")
    info = migration.info_paquete(destino, "contraseña-larga-1")
    assert info["version"] == 2 and info["tiene_db"] and info["num_sesiones"] >= 2

    with pytest.raises(migration.MigracionError):
        migration.info_paquete(destino, "contraseña-mala-xx")

    # Un archivo manipulado (un byte cambiado) debe rechazarse (autenticación GCM).
    datos = bytearray(open(destino, "rb").read())
    datos[len(datos) // 2] ^= 0xFF
    alterado = str(get_data_path("") / "mig_alterada.srmig")
    open(alterado, "wb").write(bytes(datos))
    with pytest.raises(migration.MigracionError):
        migration.info_paquete(alterado, "contraseña-larga-1")


def test_migracion_password_corta_rechazada():
    from app.core import migration
    with pytest.raises(migration.MigracionError):
        migration.exportar_migracion(str(get_data_path("") / "x.srmig"), "corta")


def test_nombre_sesion_seguro():
    from app.core.migration import _nombre_sesion_seguro
    assert _nombre_sesion_seguro("cuenta.session") is not None
    assert _nombre_sesion_seguro("matrix/3/store.db") is not None
    assert _nombre_sesion_seguro("../../evil") is None
    assert _nombre_sesion_seguro("/absoluta") is None
    assert _nombre_sesion_seguro("C:/win/x") is None
    assert _nombre_sesion_seguro("a/../../b") is None


# ---------------------------------------------------------------------------
# 8.1: lecturas dentro de una transacción ven lo aún no confirmado
# ---------------------------------------------------------------------------

def test_query_dentro_de_transaccion_ve_lo_no_confirmado():
    from app.core.database import db
    db.execute("CREATE TABLE IF NOT EXISTS t_aud (v INTEGER)")
    db.execute("DELETE FROM t_aud")
    with db.transaccion():
        db.execute("INSERT INTO t_aud VALUES (1)")
        # Antes, query() abría otra conexión y NO veía la fila (contrato contradicho).
        assert db.query_one("SELECT COUNT(*) FROM t_aud")[0] == 1
    assert db.query_one("SELECT COUNT(*) FROM t_aud")[0] == 1
    db.execute("DROP TABLE t_aud")


# ---------------------------------------------------------------------------
# A2: el gestor de tareas rechaza trabajo nuevo al cerrarse
# ---------------------------------------------------------------------------

def test_task_manager_shutdown_rechaza_tareas():
    from app.core.task_manager import TaskManager
    tm = TaskManager(max_workers=2)
    assert tm.shutdown(plazo=1.0) is True
    assert tm.start_task(lambda ctx: None) is None


def test_task_manager_duplicada_avisa_al_callback(monkeypatch):
    """Una tarea descartada por unique_key duplicada debe AVISAR a su callback con
    TareaDuplicadaError (fallo reportado: el descarte silencioso dejaba atascados los
    flags de 'actualizando' de la interfaz y los buffers dejaban de refrescarse)."""
    import threading
    from app.core import task_manager as tm_mod
    from app.core.task_manager import TaskManager, TareaDuplicadaError

    # En las pruebas no hay bucle wx: entregar el aviso directamente.
    monkeypatch.setattr(tm_mod.wx, "CallAfter", lambda fn, *a, **kw: fn(*a, **kw))

    tm = TaskManager(max_workers=2)
    puerta = threading.Event()
    recibido = {}

    try:
        # 1.ª tarea: se queda bloqueada manteniendo ocupada la unique_key.
        assert tm.start_task(lambda ctx: puerta.wait(5), pass_context=True,
                             unique_key="prueba:dup") is not None
        # 2.ª tarea equivalente: se descarta, PERO el callback debe enterarse.
        def _cb(res, err):
            recibido["res"] = res
            recibido["err"] = err
        assert tm.start_task(lambda ctx: None, callback=_cb, pass_context=True,
                             unique_key="prueba:dup") is None
        assert isinstance(recibido.get("err"), TareaDuplicadaError)
        assert recibido.get("res") is None
    finally:
        puerta.set()
        tm.shutdown(plazo=2.0)


# ---------------------------------------------------------------------------
# A7: secretos con clave maestra DPAPI y migración desde MachineGuid
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="DPAPI es de Windows")
def test_secretos_migran_de_machineguid_a_dpapi():
    import importlib
    import app.core.security as sec
    # Simular un almacén viejo cifrado con la clave derivada de MachineGuid.
    f_old = sec.SecurityManager._derivar_fernet(sec._identificador_maquina())
    get_data_path("config").mkdir(parents=True, exist_ok=True)
    keyfile = get_data_path("config/secrets.key")
    if keyfile.exists():
        keyfile.unlink()
    get_data_path("config/secrets.enc").write_bytes(
        f_old.encrypt(json.dumps({"mastodon:token": "tok"}).encode()))
    m = sec.SecurityManager()
    assert m.obtener_credencial("mastodon", "token") == "tok"
    assert keyfile.exists()
    # Tras la migración, la clave antigua ya no descifra el fichero.
    contenido = get_data_path("config/secrets.enc").read_bytes()
    with pytest.raises(Exception):
        f_old.decrypt(contenido)
    # Y una instancia nueva (clave maestra desde DPAPI) sí lo lee.
    assert sec.SecurityManager().obtener_credencial("mastodon", "token") == "tok"


# ---------------------------------------------------------------------------
# 10.1: actualizador — límites del ZIP y cordura del paquete
# ---------------------------------------------------------------------------

def test_extraer_zip_rechaza_paquete_ajeno(tmp_path):
    from app.services.updater import update_service as upd
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("SocialRSS/cualquiera.txt", "x")   # sin exe ni main.py
    with pytest.raises(ValueError):
        upd.extraer_zip(str(z), str(tmp_path / "out"))


def test_manifiesto_de_actualizacion_firmado(tmp_path):
    """El actualizador lleva la clave pública Ed25519 incrustada: un manifiesto
    firmado por comprimir_build.py se acepta; sin firma o manipulado, se rechaza."""
    from app.services.updater import update_service as upd
    import comprimir_build
    assert upd._CLAVE_PUBLICA_ED25519, "la clave pública debe estar incrustada"
    if not os.path.exists(comprimir_build.CLAVE_FIRMA):
        pytest.skip("Sin la clave privada de firma (no se publica): solo la tiene el autor.")
    ruta = tmp_path / "version.json"
    ruta.write_text(json.dumps({"version": "9.9.9", "sha256": "ab" * 32}), encoding="utf-8")
    assert comprimir_build._firmar_manifiesto(str(ruta)) is True
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    upd.verificar_manifiesto(datos)          # firmado: no lanza
    sin_firma = {k: v for k, v in datos.items() if k != "firma_ed25519"}
    with pytest.raises(ValueError):
        upd.verificar_manifiesto(sin_firma)  # sin firma: rechazado
    datos["version"] = "1.2.3"
    with pytest.raises(ValueError):
        upd.verificar_manifiesto(datos)      # manipulado: rechazado


def test_extraer_zip_omite_miembros_con_escape(tmp_path):
    from app.services.updater import update_service as upd
    z = tmp_path / "p2.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("SocialRSS/main.py", "print('ok')")
        zf.writestr("SocialRSS/../../evil.txt", "mal")
    destino = tmp_path / "out2"
    upd.extraer_zip(str(z), str(destino))
    assert (destino / "main.py").exists()
    assert not (tmp_path / "evil.txt").exists()
