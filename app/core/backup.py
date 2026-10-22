# -*- coding: utf-8 -*-
"""
Copias de seguridad y portabilidad de SocialRSS.

Empaqueta en un único archivo ZIP la base de datos, la configuración, los
secretos cifrados y el estado de sesiones de los servicios (sesiones de
Telegram, almacenes E2EE de Matrix y OMEMO de XMPP), para poder restaurarlos
o moverlos a otro equipo.

Seguridad y consistencia:

* La base de datos se copia con la API de backup de SQLite (instantánea
  coherente aunque esté en modo WAL y en uso) y se verifica con
  `PRAGMA integrity_check` ANTES de publicar el ZIP. Nunca se comprime el
  fichero .db "en caliente" (perdería los cambios que aún viven en el -wal).
* La restauración NO sustituye ficheros en uso: valida el ZIP, lo extrae a una
  carpeta de espera (data/_restore_pending) y la sustitución real ocurre al
  siguiente arranque, antes de abrir la base de datos, mediante
  `aplicar_restauracion_pendiente()`. Es el mismo patrón que la migración.
* El ZIP se valida con lista blanca de miembros, comprobación de rutas con
  `os.path.commonpath` (no `startswith`, vulnerable a rutas hermanas con
  prefijo común) y límites de cantidad y tamaño (anti "zip bomb").

Nota: los secretos (`secrets.enc`) están cifrados con una clave derivada del
equipo (MachineGuid). Al restaurar una copia en OTRO ordenador, los
tokens/credenciales NO se podrán descifrar y habrá que volver a introducirlos;
el resto (feeds, artículos, preferencias) sí se restaura. Para cambiar de
equipo está la migración (Herramientas → Migrar a otro ordenador).
"""

import os
import json
import shutil
import sqlite3
import tempfile
import zipfile
import logging
import threading
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import List, Dict, Any

from app.core.paths import get_data_path

log = logging.getLogger(__name__)

# Elementos sueltos (relativos a data/) que entran en la copia. secrets.key es la
# clave maestra protegida con DPAPI: sin ella, secrets.enc no se puede descifrar ni
# siquiera en el mismo equipo.
_ELEMENTOS = [
    "database/social_rss.db",
    "config/settings.json",
    "config/secrets.enc",
    "config/secrets.key",
]

# Directorios (relativos a data/) que entran RECURSIVAMENTE: sesiones de
# Telegram (archivos sueltos), almacenes E2EE de Matrix (sessions/matrix/<id>/)
# y almacenes OMEMO de XMPP (sessions/xmpp_<id>_omemo.json). Sin ellos, tras
# restaurar no se podría descifrar el historial cifrado.
_DIRECTORIOS = [
    "sessions",
]

# Límites de la restauración (defensa ante ZIP maliciosos o corruptos).
_MAX_MIEMBROS = 5000                     # nº máximo de ficheros en el ZIP
_MAX_TAM_MIEMBRO = 1 * 1024 ** 3         # 1 GB por fichero descomprimido
_MAX_TAM_TOTAL = 4 * 1024 ** 3           # 4 GB en total descomprimido
_CHUNK = 1024 * 1024                     # copia en streaming de 1 MB


def _backups_dir():
    d = get_data_path("backups")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _staging_restore_dir():
    return get_data_path("_restore_pending")


def _snapshot_db(destino: str):
    """Crea una instantánea CONSISTENTE de la base de datos en `destino` usando la API
    de backup de SQLite (segura con WAL y escrituras concurrentes) y la verifica con
    integrity_check. Lanza RuntimeError si la instantánea no queda íntegra."""
    db_path = get_data_path("database/social_rss.db")
    if not db_path.exists():
        raise FileNotFoundError("No existe la base de datos.")
    origen = sqlite3.connect(str(db_path), timeout=10)
    try:
        origen.execute("PRAGMA busy_timeout=10000")
        copia = sqlite3.connect(destino)
        try:
            with copia:
                origen.backup(copia)
            fila = copia.execute("PRAGMA integrity_check").fetchone()
            if not fila or fila[0] != "ok":
                raise RuntimeError(f"La instantánea no pasó integrity_check: {fila}")
        finally:
            copia.close()
    finally:
        origen.close()


def _archivos_de_directorio(rel_dir: str):
    """Genera (ruta_absoluta, arcname_posix) de todos los ficheros bajo data/rel_dir,
    recursivamente y sin seguir enlaces simbólicos."""
    base = get_data_path(rel_dir)
    if not base.exists():
        return
    for raiz, dirs, archivos in os.walk(str(base), followlinks=False):
        for nombre in archivos:
            ruta = Path(raiz) / nombre
            if ruta.is_symlink():
                continue
            rel = ruta.relative_to(get_data_path(""))
            yield ruta, rel.as_posix()


def crear_backup(destino: str = None) -> str:
    """Crea un ZIP con una instantánea íntegra de la base de datos, la configuración,
    los secretos y las sesiones/almacenes E2EE de los servicios.
    Si no se indica `destino`, se guarda en data/backups con marca de tiempo.
    Devuelve la ruta del archivo creado."""
    if not destino:
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = str(_backups_dir() / f"socialrss_backup_{marca}.zip")

    # Instantánea consistente de la BD en un temporal (nunca el .db en caliente).
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    # El ZIP se escribe primero en un temporal y se publica con os.replace, para no
    # dejar nunca un ZIP a medias con nombre definitivo si algo falla a mitad.
    tmp_zip = destino + ".parcial"
    try:
        _snapshot_db(tmp_db.name)
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_db.name, arcname="database/social_rss.db")
            for rel in _ELEMENTOS:
                if rel == "database/social_rss.db":
                    continue
                ruta = get_data_path(rel)
                if ruta.exists():
                    zf.write(str(ruta), arcname=rel)
            for rel_dir in _DIRECTORIOS:
                for ruta, arcname in _archivos_de_directorio(rel_dir):
                    try:
                        zf.write(str(ruta), arcname=arcname)
                    except OSError as e:
                        # Un almacén puede estar bloqueado por su servicio; la copia
                        # sigue siendo útil sin ese fichero, pero se deja constancia.
                        log.warning(f"No se pudo incluir en la copia: {arcname}: {e}")
        os.replace(tmp_zip, destino)
    except Exception:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
        raise
    finally:
        try:
            os.remove(tmp_db.name)
        except OSError:
            pass
    log.info(f"Copia de seguridad creada en: {destino}")
    return destino


def rotar_backups(maximo: int = 5):
    """Mantiene como mucho `maximo` copias automáticas, borrando las más antiguas."""
    if maximo <= 0:
        return
    try:
        archivos = sorted(
            _backups_dir().glob("socialrss_backup_*.zip"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        for viejo in archivos[maximo:]:
            try:
                viejo.unlink()
            except Exception:
                pass
    except Exception as e:
        log.debug(f"No se pudieron rotar las copias: {e}")


def listar_backups() -> List[Dict[str, Any]]:
    """Lista las copias disponibles en data/backups (más recientes primero)."""
    res = []
    try:
        for p in sorted(_backups_dir().glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
            st = p.stat()
            res.append({
                "ruta": str(p),
                "nombre": p.name,
                "fecha": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "tamano_kb": round(st.st_size / 1024, 1),
            })
    except Exception as e:
        log.error(f"Error al listar copias: {e}")
    return res


def comprobar_backup_automatico():
    """Si está activada la copia automática y ha pasado el intervalo configurado,
    crea una copia y rota las antiguas EN UN HILO DE FONDO con prioridad baja, para
    no retrasar el arranque. Pensado para llamarse al arrancar."""
    from app.core.config_manager import config
    if not config.get("backup", "auto_backup", False):
        return
    try:
        intervalo = int(config.get("backup", "backup_interval_days", 7))
    except Exception:
        intervalo = 7
    ultimo = config.get("backup", "last_backup", "")
    necesita = True
    if ultimo:
        try:
            from datetime import timedelta
            fecha = datetime.fromisoformat(ultimo)
            necesita = (datetime.now() - fecha) >= timedelta(days=max(1, intervalo))
        except Exception:
            necesita = True
    if not necesita:
        return

    def _trabajo():
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetThreadPriority(k32.GetCurrentThread(), -1)  # THREAD_PRIORITY_BELOW_NORMAL
        except Exception:
            pass
        try:
            crear_backup()
            rotar_backups(int(config.get("backup", "max_backups", 10)))
            config.set("backup", "last_backup", datetime.now().isoformat())
            log.info("Copia de seguridad automática creada.")
        except Exception as e:
            log.error(f"Error en la copia de seguridad automática: {e}")

    threading.Thread(target=_trabajo, name="AutoBackup", daemon=True).start()


def _miembro_permitido(nombre: str) -> bool:
    """Solo se restauran los miembros esperados: los elementos sueltos exactos o
    ficheros dentro de los directorios de sesiones. Nada de rutas absolutas, unidades,
    barras invertidas ni '..'."""
    if not nombre or nombre.endswith("/"):
        return False  # entradas de directorio: se crean solas al extraer ficheros
    if "\\" in nombre or nombre.startswith("/") or ":" in nombre:
        return False
    partes = PurePosixPath(nombre).parts
    if any(p in ("..", ".") for p in partes):
        return False
    if nombre in _ELEMENTOS:
        return True
    return any(partes[0] == d for d in _DIRECTORIOS)


def _validar_y_extraer(zf: zipfile.ZipFile, destino_base: Path) -> int:
    """Extrae a `destino_base` SOLO los miembros permitidos, en streaming y con
    límites de cantidad y tamaño. Devuelve el nº de ficheros extraídos."""
    infos = zf.infolist()
    if len(infos) > _MAX_MIEMBROS:
        raise ValueError(f"El archivo contiene demasiados ficheros ({len(infos)}).")
    base_res = destino_base.resolve()
    total = 0
    extraidos = 0
    for info in infos:
        nombre = info.filename
        if not _miembro_permitido(nombre):
            if not nombre.endswith("/"):
                log.warning(f"Se omite un miembro no esperado del ZIP: {nombre}")
            continue
        destino = (destino_base / PurePosixPath(nombre)).resolve()
        # Doble candado contra zip slip: la ruta resuelta debe quedar DENTRO de la base.
        if os.path.commonpath([str(base_res), str(destino)]) != str(base_res):
            log.warning(f"Se omite una ruta sospechosa en el ZIP: {nombre}")
            continue
        destino.parent.mkdir(parents=True, exist_ok=True)
        escritos = 0
        with zf.open(info) as src, open(destino, "wb") as dst:
            while True:
                bloque = src.read(_CHUNK)
                if not bloque:
                    break
                escritos += len(bloque)
                total += len(bloque)
                if escritos > _MAX_TAM_MIEMBRO or total > _MAX_TAM_TOTAL:
                    raise ValueError("El archivo se expande más de lo permitido; "
                                     "se aborta por seguridad.")
                dst.write(bloque)
        extraidos += 1
    return extraidos


def restaurar_backup(ruta_zip: str) -> bool:
    """PREPARA la restauración de una copia: valida el ZIP, lo extrae a una carpeta
    de espera y deja marcada la restauración, que se aplicará al siguiente arranque
    (antes de abrir la base de datos, cuando nada la tiene abierta). NO sustituye
    ficheros en uso. Antes, crea una copia de seguridad del estado actual (rollback).

    Tras llamar a esta función hay que reiniciar la aplicación."""
    if not os.path.exists(ruta_zip):
        raise FileNotFoundError("No se encuentra el archivo de copia.")

    staging = _staging_restore_dir()
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(ruta_zip, "r") as zf:
        nombres = set(zf.namelist())
        if not any(n in nombres for n in _ELEMENTOS):
            raise ValueError("El archivo no parece una copia válida de SocialRSS.")
        extraidos = _validar_y_extraer(zf, staging)
    if not extraidos:
        shutil.rmtree(staging, ignore_errors=True)
        raise ValueError("La copia no contiene ningún fichero restaurable.")

    # Si la copia trae base de datos, verificar su integridad ANTES de comprometerse.
    db_stage = staging / "database" / "social_rss.db"
    if db_stage.exists():
        con = sqlite3.connect(str(db_stage))
        try:
            fila = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        if not fila or fila[0] != "ok":
            shutil.rmtree(staging, ignore_errors=True)
            raise ValueError("La base de datos de la copia está dañada; no se restaura.")

    # Salvaguarda del estado actual, para poder volver atrás a mano.
    try:
        crear_backup(str(_backups_dir() / "_pre_restauracion.zip"))
    except Exception as e:
        log.warning(f"No se pudo crear copia previa a la restauración: {e}")

    (staging / "READY").write_text(
        json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"),
                    "origen": os.path.basename(ruta_zip)}), encoding="utf-8")
    log.info(f"Restauración preparada desde: {ruta_zip}; se aplicará al reiniciar.")
    return True


def hay_restauracion_pendiente() -> bool:
    return (_staging_restore_dir() / "READY").exists()


def aplicar_restauracion_pendiente() -> bool:
    """Si hay una restauración preparada, sustituye los ficheros reales por los de la
    carpeta de espera. DEBE llamarse al arrancar, ANTES de abrir la base de datos o
    cargar configuración/secretos. Nunca lanza: ante un fallo, avisa y continúa.

    Devuelve True si se aplicó algo."""
    staging = _staging_restore_dir()
    if not (staging / "READY").exists():
        return False

    aplicado = False
    base = get_data_path("")

    # 1. Base de datos: reemplazo + retirar -wal/-shm viejos para que no se mezclen
    #    con la base restaurada.
    db_stage = staging / "database" / "social_rss.db"
    if db_stage.exists():
        try:
            db_dest = get_data_path("database/social_rss.db")
            db_dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(db_stage), str(db_dest))
            for sufijo in ("-wal", "-shm"):
                extra = db_dest.with_name(db_dest.name + sufijo)
                try:
                    if extra.exists():
                        extra.unlink()
                except OSError:
                    pass
            aplicado = True
        except Exception as e:
            log.error(f"No se pudo aplicar la base de datos restaurada: {e}")

    # 2. Configuración y secretos.
    for rel in ("config/settings.json", "config/secrets.enc"):
        origen = staging / PurePosixPath(rel)
        if origen.exists():
            try:
                dest = base / PurePosixPath(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(origen), str(dest))
                aplicado = True
            except Exception as e:
                log.error(f"No se pudo aplicar {rel}: {e}")

    # 3. Sesiones y almacenes E2EE: se sobrescribe lo que trae la copia y se conserva
    #    lo demás (una cuenta añadida después de la copia no pierde su sesión).
    ses_stage = staging / "sessions"
    if ses_stage.exists():
        for raiz, dirs, archivos in os.walk(str(ses_stage)):
            for nombre in archivos:
                origen = Path(raiz) / nombre
                rel = origen.relative_to(staging)
                try:
                    dest = base / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(str(origen), str(dest))
                    aplicado = True
                except Exception as e:
                    log.warning(f"No se pudo aplicar la sesión {rel}: {e}")

    shutil.rmtree(staging, ignore_errors=True)
    if aplicado:
        log.info("Restauración de copia de seguridad aplicada al arrancar.")
    return aplicado
