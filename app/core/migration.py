# -*- coding: utf-8 -*-
"""
Migración completa de SocialRSS a otro ordenador (exportar / importar).

A diferencia de la copia de seguridad (`backup.py`), que copia `secrets.enc` tal cual
—cifrado con una clave ligada al equipo y, por tanto, ilegible en otro ordenador—, la
migración EXPORTA los secretos descifrados y los vuelve a cifrar con una CONTRASEÑA que
elige el usuario. Así, al importar en otro equipo con esa misma contraseña, se restaura
todo y queda funcionando igual:

  * Base de datos completa (cuentas, feeds, artículos, mensajes, etc.).
  * Configuración (settings.json).
  * Credenciales/secretos (tokens de Mastodon, sesión de Bluesky, api_id/api_hash de
    Telegram…), que al importar se vuelven a cifrar con la clave del equipo NUEVO.
  * Sesiones de Telegram (carpeta data/sessions), que mantienen el inicio de sesión.

El archivo exportado va cifrado con la contraseña (Fernet + PBKDF2), de modo que solo
quien la conozca puede importarlo.
"""

import os
import json
import base64
import hashlib
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from .paths import get_data_path

log = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False

_MAGIC = b"SRSSMIG1"          # formato ANTIGUO (v1): JSON+Base64 cifrado entero con Fernet
_MAGIC2 = b"SRSSMIG2"         # formato ACTUAL (v2): ZIP cifrado por bloques AES-GCM (streaming)
_SALT_LEN = 16
_PBKDF2_ITERS = 200000        # iteraciones del formato v1 (fijas, para poder importarlo)
_PBKDF2_ITERS_V2 = 600000     # v2: van ESCRITAS en la cabecera (parámetros versionados)
_CHUNK_CIFRADO = 4 * 1024 * 1024   # bloques de 4 MB: la memoria no depende del tamaño de la BD

# Versión del FORMATO del paquete de migración. La importación es RETROCOMPATIBLE: acepta esta
# versión y cualquier ANTERIOR (los campos que no existan en un paquete viejo simplemente se
# omiten). Si algún día el formato cambia de forma incompatible, súbela y maneja aquí cada caso;
# nunca se debe rechazar un paquete por ser de una versión más baja. La base de datos importada,
# aunque tenga un esquema antiguo, se pone al día sola con las migraciones de esquema al arrancar.
_VERSION_FORMATO = 2


class MigracionError(Exception):
    """Error de exportación/importación de migración."""


def _clave_desde_password(password: str, salt: bytes) -> bytes:
    """Deriva una clave Fernet (32 bytes en base64 url-safe) de la contraseña.
    Solo para LEER paquetes del formato antiguo (v1)."""
    if not password:
        raise MigracionError("La contraseña no puede estar vacía.")
    bruta = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS, dklen=32)
    return base64.urlsafe_b64encode(bruta)


def _clave_bruta(password: str, salt: bytes, iteraciones: int) -> bytes:
    """Deriva la clave AES-256 del formato v2 (las iteraciones van en la cabecera)."""
    if not password:
        raise MigracionError("La contraseña no puede estar vacía.")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt,
                               iteraciones, dklen=32)


def _cifrar_archivo(origen: str, destino: str, password: str):
    """Cifra `origen` en `destino` por BLOQUES autenticados (AES-GCM), en streaming:
    la memoria usada es constante (un bloque), no depende del tamaño de la base de
    datos. Cabecera: MAGIC2 + salt + iteraciones PBKDF2 (parámetros versionados).
    Cada bloque lleva su índice como nonce y el último va marcado, de modo que un
    archivo truncado o con bloques reordenados NO se acepta al importar."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt = os.urandom(_SALT_LEN)
    aes = AESGCM(_clave_bruta(password, salt, _PBKDF2_ITERS_V2))
    with open(origen, "rb") as fi, open(destino, "wb") as fo:
        fo.write(_MAGIC2)
        fo.write(salt)
        fo.write(_PBKDF2_ITERS_V2.to_bytes(4, "big"))
        indice = 0
        while True:
            bloque = fi.read(_CHUNK_CIFRADO)
            ultimo = len(bloque) < _CHUNK_CIFRADO
            nonce = indice.to_bytes(12, "big")
            cifrado = aes.encrypt(nonce, bloque, b"fin" if ultimo else b"seg")
            fo.write(len(cifrado).to_bytes(4, "big"))
            fo.write(cifrado)
            indice += 1
            if ultimo:
                break


def _descifrar_archivo(origen: str, destino: str, password: str):
    """Inverso de _cifrar_archivo. Lanza MigracionError si la contraseña es incorrecta
    o el archivo está dañado/truncado/reordenado (la autenticación GCM lo detecta)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(origen, "rb") as fi:
        magia = fi.read(len(_MAGIC2))
        if magia != _MAGIC2:
            raise MigracionError("El archivo no es una exportación de SocialRSS válida.")
        salt = fi.read(_SALT_LEN)
        iters_b = fi.read(4)
        if len(salt) != _SALT_LEN or len(iters_b) != 4:
            raise MigracionError("El archivo de migración está incompleto.")
        iteraciones = int.from_bytes(iters_b, "big")
        if not (10000 <= iteraciones <= 10000000):
            raise MigracionError("El archivo de migración tiene una cabecera inválida.")
        aes = AESGCM(_clave_bruta(password, salt, iteraciones))
        with open(destino, "wb") as fo:
            indice = 0
            while True:
                cab = fi.read(4)
                if len(cab) != 4:
                    raise MigracionError("El archivo de migración está truncado.")
                n = int.from_bytes(cab, "big")
                if n > _CHUNK_CIFRADO + 64:
                    raise MigracionError("El archivo de migración tiene un bloque inválido.")
                cifrado = fi.read(n)
                if len(cifrado) != n:
                    raise MigracionError("El archivo de migración está truncado.")
                # ¿Es el último bloque del fichero?
                sig = fi.read(1)
                if sig:
                    fi.seek(-1, os.SEEK_CUR)
                nonce = indice.to_bytes(12, "big")
                try:
                    datos = aes.decrypt(nonce, cifrado, b"fin" if not sig else b"seg")
                except InvalidTag:
                    raise MigracionError("Contraseña incorrecta o archivo dañado.")
                fo.write(datos)
                indice += 1
                if not sig:
                    break


def _copia_consistente_db_a(destino: str) -> bool:
    """Escribe en `destino` una copia ÍNTEGRA de la base de datos usando la API de
    backup de SQLite (segura aunque la base esté en modo WAL y con uso activo).
    Devuelve False si no hay base de datos."""
    db_path = get_data_path("database/social_rss.db")
    if not db_path.exists():
        return False
    # La base de datos está EN USO (modo WAL, con otras conexiones abiertas). La API
    # de backup de SQLite copia una instantánea coherente aunque haya escrituras en
    # curso; el busy_timeout evita fallar si justo hay un bloqueo momentáneo.
    origen = sqlite3.connect(str(db_path), timeout=10)
    try:
        origen.execute("PRAGMA busy_timeout=10000")
        copia = sqlite3.connect(destino)
        try:
            with copia:
                origen.backup(copia)
        finally:
            copia.close()
    finally:
        origen.close()
    return True


def _iter_sesiones():
    """Genera (ruta_relativa_posix, ruta_absoluta) de TODO el estado de sesiones
    (data/sessions), RECURSIVAMENTE: sesiones de Telegram (archivos sueltos),
    almacenes E2EE de Matrix (sessions/matrix/<id>/...) y almacenes OMEMO de XMPP.
    Sin los anidados, tras migrar no se podría descifrar el historial cifrado."""
    carpeta = get_data_path("sessions")
    if not carpeta.exists():
        return
    for raiz, dirs, archivos in os.walk(str(carpeta), followlinks=False):
        for nombre in archivos:
            elem = Path(raiz) / nombre
            if elem.is_symlink():
                continue
            yield elem.relative_to(carpeta).as_posix(), elem


def _nombre_sesion_seguro(nombre: str):
    """Valida una clave de sesión del paquete como ruta RELATIVA segura (sin '..',
    sin rutas absolutas ni unidades). Devuelve la ruta relativa o None si no es
    aceptable. Acepta tanto nombres planos antiguos como subrutas nuevas."""
    nombre = str(nombre).replace("\\", "/")
    partes = PurePosixPath(nombre).parts
    if not partes or nombre.startswith("/") or ":" in nombre:
        return None
    if any(p in ("..", ".") for p in partes):
        return None
    return PurePosixPath(*partes)


def exportar_migracion(ruta_destino: str, password: str) -> str:
    """Crea un archivo de migración cifrado con la contraseña dada (formato v2:
    ZIP cifrado por bloques AES-GCM, en streaming: la memoria no depende del
    tamaño de la base). Incluye base de datos, configuración, secretos descifrados
    y TODO el estado de sesiones (Telegram, Matrix E2EE, XMPP OMEMO)."""
    if not HAS_CRYPTOGRAPHY:
        raise MigracionError("Falta la librería de cifrado (cryptography); no se puede exportar de forma segura.")
    if not password or len(password) < 10:
        raise MigracionError("Use una contraseña de al menos 10 caracteres: el archivo "
                             "contiene sus credenciales descifradas.")

    from .security import security
    from .config_manager import config

    # Secretos DESCIFRADOS (en claro solo dentro del paquete, que luego se cifra entero).
    secretos = security._cargar_secretos()
    # Configuración actual (settings.json) tal cual está en memoria/disco.
    settings_path = get_data_path("config/settings.json")
    settings_data = {}
    if settings_path.exists():
        try:
            settings_data = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            settings_data = dict(config.settings)

    manifest = {
        "tipo": "socialrss-migracion",
        "version": _VERSION_FORMATO,
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "settings": settings_data,
        "secrets": secretos,
    }

    tmpdir = tempfile.mkdtemp(prefix="srssmig_")
    try:
        # 1. Instantánea consistente de la base directamente a fichero (sin pasar por bytes).
        db_tmp = os.path.join(tmpdir, "database.db")
        tiene_db = _copia_consistente_db_a(db_tmp)
        # 2. Paquete ZIP en disco: manifiesto pequeño + base + sesiones, sin Base64.
        zip_tmp = os.path.join(tmpdir, "paquete.zip")
        with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            if tiene_db:
                zf.write(db_tmp, "database.db")
            for rel, ruta in _iter_sesiones():
                try:
                    zf.write(str(ruta), "sessions/" + rel)
                except OSError as e:
                    log.warning(f"No se pudo incluir la sesión {rel}: {e}")
        # 3. Cifrado autenticado por bloques al destino final.
        _cifrar_archivo(zip_tmp, ruta_destino, password)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    log.info(f"Migración exportada a: {ruta_destino}")
    return ruta_destino


def _leer_paquete(ruta: str, password: str) -> dict:
    """Lee y descifra un archivo de migración. Lanza MigracionError si la contraseña es
    incorrecta o el archivo no es válido."""
    if not HAS_CRYPTOGRAPHY:
        raise MigracionError("Falta la librería de cifrado (cryptography); no se puede importar.")
    with open(ruta, "rb") as f:
        contenido = f.read()
    if contenido[:len(_MAGIC)] != _MAGIC:
        raise MigracionError("El archivo no es una exportación de SocialRSS válida.")
    salt = contenido[len(_MAGIC):len(_MAGIC) + _SALT_LEN]
    token = contenido[len(_MAGIC) + _SALT_LEN:]
    try:
        payload = Fernet(_clave_desde_password(password, salt)).decrypt(token)
    except Exception:
        raise MigracionError("Contraseña incorrecta o archivo dañado.")
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except Exception:
        raise MigracionError("El contenido de la migración no se pudo leer.")
    if manifest.get("tipo") != "socialrss-migracion":
        raise MigracionError("El archivo no es una exportación de SocialRSS válida.")
    # Retrocompatibilidad: NO se rechaza por versión. Una versión MÁS BAJA (copia antigua) es
    # siempre válida; una MÁS ALTA (exportada por una app más nueva) se intenta igualmente, solo
    # se avisa por si algún campo nuevo no se entiende.
    try:
        ver = int(manifest.get("version", 1))
        if ver > _VERSION_FORMATO:
            log.warning(f"El paquete de migración es de un formato más nuevo (v{ver} > v{_VERSION_FORMATO}); "
                        f"se intentará importar de todos modos.")
    except (TypeError, ValueError):
        pass
    return manifest


def _abrir_paquete(ruta: str, password: str):
    """Abre un paquete de migración de CUALQUIER formato. Devuelve
    (manifest, ruta_zip_o_None, tmpdir_o_None): en v2 el contenido grande (base de
    datos, sesiones) está dentro del ZIP descifrado en `ruta_zip` (el llamador debe
    borrar `tmpdir` al terminar); en v1 va incrustado en el propio manifest."""
    if not HAS_CRYPTOGRAPHY:
        raise MigracionError("Falta la librería de cifrado (cryptography); no se puede importar.")
    if not os.path.exists(ruta):
        raise MigracionError("No se encuentra el archivo de migración.")
    with open(ruta, "rb") as f:
        magia = f.read(len(_MAGIC2))
    if magia == _MAGIC2:
        tmpdir = tempfile.mkdtemp(prefix="srssmig_imp_")
        zip_path = os.path.join(tmpdir, "paquete.zip")
        try:
            _descifrar_archivo(ruta, zip_path, password)
            with zipfile.ZipFile(zip_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        except MigracionError:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise
        except Exception:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise MigracionError("El contenido de la migración no se pudo leer.")
        if manifest.get("tipo") != "socialrss-migracion":
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise MigracionError("El archivo no es una exportación de SocialRSS válida.")
        return manifest, zip_path, tmpdir
    # Formato antiguo (v1): todo va dentro del JSON cifrado con Fernet.
    return _leer_paquete(ruta, password), None, None


def _staging_dir():
    return get_data_path("_import_pending")


def importar_migracion(ruta: str, password: str) -> bool:
    """Prepara la importación de un archivo de migración en ESTE ordenador.

    IMPORTANTE: la base de datos y las sesiones de Telegram están EN USO mientras la
    aplicación corre, así que NO se sobrescriben en caliente (eso corrompería la base de
    datos o dejaría conexiones colgadas). En su lugar, esta función deja preparados todos
    los archivos en una carpeta de espera (data/_import_pending) y marca que hay una
    importación pendiente; la sustitución real la hace `aplicar_importacion_pendiente()`
    al volver a arrancar, ANTES de abrir la base de datos o cargar la configuración, que
    es el único momento en que nada las tiene abiertas.

    Tras llamar a esta función hay que CERRAR la aplicación para que, al reabrirla, se
    aplique la importación. Lanza MigracionError si la contraseña no es correcta o el
    archivo no es válido."""
    manifest, zip_path, tmpdir = _abrir_paquete(ruta, password)
    try:
        from .security import security

        staging = _staging_dir()
        # Empezar de cero por si quedó una importación a medias de un intento anterior.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        # 1. Base de datos y sesiones/almacenes E2EE.
        if zip_path:
            # Formato v2: van como ficheros dentro del ZIP (copia en streaming).
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    nombre = info.filename
                    if nombre == "database.db":
                        destino = staging / "database.db"
                    elif nombre.startswith("sessions/") and not nombre.endswith("/"):
                        rel = _nombre_sesion_seguro(nombre[len("sessions/"):])
                        if rel is None:
                            log.warning(f"Se omite una sesión con ruta sospechosa: {nombre}")
                            continue
                        destino = staging / "sessions" / rel
                    else:
                        continue
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(destino, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
        else:
            # Formato v1: base en Base64 y sesiones como dict {nombre: b64}.
            db_b64 = manifest.get("database_b64") or ""
            if db_b64:
                (staging / "database.db").write_bytes(base64.b64decode(db_b64))
            sesiones = manifest.get("sessions") or {}
            if isinstance(sesiones, dict) and sesiones:
                ses_dir = staging / "sessions"
                ses_dir.mkdir(parents=True, exist_ok=True)
                for nombre, datos_b64 in sesiones.items():
                    rel = _nombre_sesion_seguro(nombre)
                    if rel is None:
                        log.warning(f"Se omite una sesión con ruta sospechosa: {nombre}")
                        continue
                    try:
                        destino = ses_dir / rel
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        destino.write_bytes(base64.b64decode(datos_b64))
                    except Exception as e:
                        log.debug(f"No se pudo preparar la sesión {nombre}: {e}")

        # 2. Configuración (settings.json) tal cual.
        settings = manifest.get("settings")
        if isinstance(settings, dict):
            (staging / "settings.json").write_text(
                json.dumps(settings, indent=4, ensure_ascii=False), encoding="utf-8")

        # 3. Secretos: re-cifrarlos con la clave de ESTE ordenador para dejar el secrets.enc
        #    final ya listo (así al aplicarlo solo hay que moverlo).
        secretos = manifest.get("secrets")
        if isinstance(secretos, dict):
            datos = json.dumps(secretos).encode("utf-8")
            try:
                if getattr(security, "_fernet", None):
                    datos = security._fernet.encrypt(datos)
            except Exception as e:
                log.warning(f"No se pudieron re-cifrar los secretos importados: {e}")
            (staging / "secrets.enc").write_bytes(datos)

        # 4. Marcar como lista. Mientras este archivo exista, el próximo arranque aplicará.
        (staging / "READY").write_text(
            json.dumps({"fecha": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        log.info("Importación preparada; se aplicará al reiniciar.")
        return True
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)


def aplicar_importacion_pendiente() -> bool:
    """Si hay una importación preparada, sustituye los archivos reales por los de la
    carpeta de espera. DEBE llamarse al arrancar, ANTES de abrir la base de datos o
    cargar la configuración/secretos (cuando nada los tiene abiertos). Usa solo la
    biblioteca estándar para no instanciar todavía la base de datos ni la config.

    Devuelve True si se aplicó una importación. Nunca lanza: ante cualquier fallo, deja un
    aviso y continúa el arranque con lo que haya."""
    staging = _staging_dir()
    marcador = staging / "READY"
    if not marcador.exists():
        return False

    aplicado_algo = False

    # Base de datos: reemplazo + limpiar WAL/SHM antiguos para que no se mezclen.
    db_stage = staging / "database.db"
    if db_stage.exists():
        try:
            db_dest = get_data_path("database/social_rss.db")
            db_dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(db_stage), str(db_dest))
            aplicado_algo = True
            for sufijo in ("-wal", "-shm"):
                extra = db_dest.with_name(db_dest.name + sufijo)
                try:
                    if extra.exists():
                        extra.unlink()
                except Exception:
                    pass
        except Exception as e:
            log.error(f"No se pudo aplicar la base de datos importada: {e}")

    # Configuración.
    set_stage = staging / "settings.json"
    if set_stage.exists():
        try:
            dest = get_data_path("config/settings.json")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(set_stage), str(dest))
            aplicado_algo = True
        except Exception as e:
            log.error(f"No se pudo aplicar la configuración importada: {e}")

    # Secretos (ya cifrados con la clave de este equipo durante la preparación).
    sec_stage = staging / "secrets.enc"
    if sec_stage.exists():
        try:
            dest = get_data_path("config/secrets.enc")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(sec_stage), str(dest))
            aplicado_algo = True
        except Exception as e:
            log.error(f"No se pudieron aplicar los secretos importados: {e}")

    # Sesiones de Telegram: vaciar las actuales y poner las importadas, para que el
    # equipo quede EXACTAMENTE igual que el de origen.
    ses_stage = staging / "sessions"
    if ses_stage.exists():
        try:
            ses_dest = get_data_path("sessions")
            # Vaciar TODO el estado actual (archivos y almacenes anidados) para que el
            # equipo quede exactamente igual que el de origen.
            if ses_dest.exists():
                shutil.rmtree(ses_dest, ignore_errors=True)
            ses_dest.mkdir(parents=True, exist_ok=True)
            for raiz, dirs, archivos in os.walk(str(ses_stage)):
                for nombre in archivos:
                    nuevo = Path(raiz) / nombre
                    rel = nuevo.relative_to(ses_stage)
                    try:
                        destino = ses_dest / rel
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(str(nuevo), str(destino))
                    except Exception as e:
                        log.debug(f"No se pudo aplicar la sesión {rel}: {e}")
            aplicado_algo = True
        except Exception as e:
            log.error(f"No se pudieron aplicar las sesiones importadas: {e}")

    # Retirar la carpeta de espera para no reaplicar en bucle.
    try:
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        pass

    if aplicado_algo:
        log.info("Importación de migración aplicada al arrancar.")
    return aplicado_algo


def info_paquete(ruta: str, password: str) -> dict:
    """Devuelve metadatos del paquete (fecha, nº de cuentas) sin aplicarlo, para
    confirmar antes de importar. Acepta ambos formatos (v1 y v2)."""
    manifest, zip_path, tmpdir = _abrir_paquete(ruta, password)
    try:
        secretos = manifest.get("secrets") or {}
        cuentas = len({k.split(":", 1)[0] for k in secretos.keys()})
        if zip_path:
            with zipfile.ZipFile(zip_path, "r") as zf:
                nombres = zf.namelist()
            tiene_db = "database.db" in nombres
            num_sesiones = sum(1 for n in nombres
                               if n.startswith("sessions/") and not n.endswith("/"))
        else:
            tiene_db = bool(manifest.get("database_b64"))
            num_sesiones = len(manifest.get("sessions") or {})
        return {
            "fecha": manifest.get("fecha", ""),
            "version": manifest.get("version", 1),
            "num_secretos": len(secretos),
            "num_cuentas_aprox": cuentas,
            "tiene_db": tiene_db,
            "num_sesiones": num_sesiones,
        }
    finally:
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)
