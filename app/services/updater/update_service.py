# -*- coding: utf-8 -*-
"""
Servicio de actualización de SocialRSS.

Comprueba e instala desde el SERVIDOR PROPIO y, si no responde, desde las RELEASES de GitHub
(hxebolax/SocialRSS), que publican los mismos dos archivos: el zip y su version.json firmado. La
seguridad no depende del origen: el manifiesto se verifica con la firma Ed25519 y el zip con su
SHA-256, vengan de donde vengan.

Flujo:
1. Comprobar la versión publicada en el servidor (version.json) y compararla con la local. Si el
   servidor no responde, se lee el version.json de la última release de GitHub.
2. Si hay una versión nueva, descargar el ZIP con esa versión (SocialRSS_v<version>.zip): primero
   del servidor y, si falla, de la release de GitHub de esa versión.
3. Extraer el ZIP a una carpeta temporal (quitando la carpeta raíz que contiene).
4. Crear un script que ESPERA a que se cierre la aplicación, copia los archivos nuevos sobre la
   instalación (sin tocar la base de datos ni la configuración del usuario, que NO van en el ZIP),
   limpia y relanza la aplicación.

La descarga, extracción y copia se hacen siempre fuera del hilo de la interfaz.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import logging
import urllib.request

from app.core.paths import ROOT_DIR, IS_FROZEN
from app.core.version import APP_VERSION

log = logging.getLogger(__name__)

# Base del servidor propio (origen principal).
URL_BASE = "https://xebolax.synology.me/SocialRSS"
# Respaldo: releases de GitHub. Se usan las URL de descarga directa (sin la API, que limita a 60
# peticiones por hora sin autenticar): 'latest/download/<archivo>' redirige al de la última release
# y 'download/v<version>/<archivo>' al de una versión concreta.
URL_GITHUB_RELEASES = "https://github.com/hxebolax/SocialRSS/releases"
_CABECERAS = {"User-Agent": "SocialRSS-Updater"}
_TIEMPO_ESPERA = 20

# Nombres de los archivos temporales que se crean junto a la instalación durante la actualización.
NOMBRE_ZIP_DESCARGA = "_actualizacion_socialrss.zip"
NOMBRE_CARPETA_TEMP = "_actualizacion_temp"
NOMBRE_SCRIPT = "_actualizar_socialrss.bat"

# Límites de extracción del paquete (defensa ante un ZIP corrupto o malicioso: el hash
# sha256 protege el transporte, pero si el SERVIDOR se viera comprometido el zip y su
# hash vendrían los dos manipulados; estos límites y la firma acotan el daño).
_MAX_MIEMBROS_ZIP = 30000
_MAX_TAM_MIEMBRO = 1 * 1024 ** 3      # 1 GB por fichero descomprimido
_MAX_TAM_TOTAL = 3 * 1024 ** 3        # 3 GB en total descomprimido

# Clave PÚBLICA Ed25519 (hex, 32 bytes) para verificar la FIRMA del manifiesto
# version.json. Separa la autenticidad del transporte: aunque alguien comprometa el
# servidor, sin la clave PRIVADA (que no está en el servidor) no puede firmar un
# manifiesto falso. El par se generó con tools/firmar_version.py; la privada vive en
# tools/claves/socialrss_ed25519.key (fuera del paquete compilado; guarda una copia
# fuera del equipo) y comprimir_build.py firma el version.json en cada build.
# OJO: con esta clave puesta, un manifiesto SIN firma válida se rechaza siempre.
_CLAVE_PUBLICA_ED25519 = "c9a790f8bb4eef8d8a30eb67968ad4db1f7f3337b8da0dd67e55ae127f3013c1"


def _parsear_version(v):
    """'1.0.6' -> (1, 0, 6). Tolerante a sufijos y a valores vacíos."""
    numeros = re.findall(r"\d+", str(v or ""))
    return tuple(int(n) for n in numeros) if numeros else (0,)


def version_local():
    return APP_VERSION


class DescargaCancelada(Exception):
    """El usuario canceló la descarga: no se prueba el origen de respaldo."""


def _leer_manifiesto(url, timeout):
    """Descarga y valida un version.json. Devuelve el dict o None (y lo deja en el log)."""
    try:
        req = urllib.request.Request(url, headers=_CABECERAS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            datos = json.loads(r.read().decode("utf-8"))
        if not isinstance(datos, dict) or not str(datos.get("version", "")).strip():
            return None
        return datos
    except Exception as e:
        log.debug(f"No se pudo leer el manifiesto de versión ({url}): {e}")
        return None


def obtener_info_remota(timeout=_TIEMPO_ESPERA):
    """Devuelve el contenido de version.json como dict ({version, sha256, size, ...}), o None si
    no se pudo. Se pide al servidor propio y, si no responde, al de la última release de GitHub.
    El sha256 sirve para VERIFICAR la descarga antes de instalarla (y la firma, el manifiesto):
    una descarga corrupta o manipulada no se aplica."""
    datos = _leer_manifiesto(f"{URL_BASE}/version.json", timeout)
    if datos:
        return datos
    datos = _leer_manifiesto(f"{URL_GITHUB_RELEASES}/latest/download/version.json", timeout)
    if datos:
        log.info("El servidor de actualizaciones no responde: se usa la release de GitHub.")
    return datos


def obtener_version_remota(timeout=_TIEMPO_ESPERA):
    """Devuelve la versión (str) publicada en el servidor (version.json), o None si no se pudo."""
    datos = obtener_info_remota(timeout)
    if not datos:
        return None
    v = str(datos.get("version", "")).strip()
    return v or None


def _sha256_de_archivo(ruta) -> str:
    """Calcula el SHA-256 de un archivo leyéndolo por bloques (el zip pesa bastante)."""
    import hashlib
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_zip(ruta_zip, sha256_esperado: str):
    """Comprueba que el zip descargado coincide con el hash publicado en version.json.
    Lanza excepción si no coincide (no debe instalarse). Si no hay hash publicado
    (servidor antiguo), no se puede verificar y se acepta con un aviso en el log."""
    esperado = (sha256_esperado or "").strip().lower()
    if not esperado:
        log.warning("El servidor no publica sha256 en version.json: no se puede verificar el zip.")
        return
    real = _sha256_de_archivo(ruta_zip)
    if real != esperado:
        raise ValueError(
            "La verificación del paquete descargado falló (el hash no coincide con el "
            "publicado). No se instalará. Inténtalo de nuevo más tarde.")
    log.info("Paquete de actualización verificado (sha256 correcto).")


def verificar_manifiesto(datos: dict):
    """Verifica la FIRMA Ed25519 del manifiesto version.json (campo 'firma_ed25519',
    en base64, sobre el mensaje '<version>:<sha256>'). Con la clave pública embebida
    vacía no hace nada (compatibilidad con el servidor actual); con clave configurada,
    un manifiesto sin firma o con firma inválida NO se instala."""
    if not _CLAVE_PUBLICA_ED25519:
        return
    import base64
    firma_b64 = str((datos or {}).get("firma_ed25519", "")).strip()
    if not firma_b64:
        raise ValueError("El manifiesto de versión no viene firmado; no se instalará.")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        clave = Ed25519PublicKey.from_public_bytes(bytes.fromhex(_CLAVE_PUBLICA_ED25519))
        mensaje = f"{str(datos.get('version', '')).strip()}:" \
                  f"{str(datos.get('sha256', '')).strip().lower()}".encode("utf-8")
        clave.verify(base64.b64decode(firma_b64), mensaje)
    except ValueError:
        raise
    except Exception:
        raise ValueError("La firma del manifiesto de versión no es válida; no se instalará.")
    log.info("Manifiesto de actualización verificado (firma Ed25519 correcta).")


def hay_actualizacion(version_remota, version_local_=None):
    """True si la versión del servidor es MÁS NUEVA que la local."""
    if not version_remota:
        return False
    try:
        return _parsear_version(version_remota) > _parsear_version(version_local_ or APP_VERSION)
    except Exception:
        return False


def url_del_zip(version):
    """URL del ZIP de esa versión: misma carpeta del servidor + 'SocialRSS_v<version>.zip'."""
    return f"{URL_BASE}/SocialRSS_v{version}.zip"


def url_del_zip_github(version):
    """URL del ZIP de esa versión en su release de GitHub (etiqueta 'v<version>')."""
    return f"{URL_GITHUB_RELEASES}/download/v{version}/SocialRSS_v{version}.zip"


def actualizacion_disponible_para_instalar():
    """La instalación automática SOLO tiene sentido en la versión compilada (.exe). En código
    fuente se puede comprobar y avisar, pero no sobreescribir (no hay paquete equivalente)."""
    return bool(IS_FROZEN)


def ruta_base_instalacion():
    """Carpeta raíz de la instalación (donde está el .exe en la versión compilada)."""
    return str(ROOT_DIR)


def descargar_zip(version, ruta_destino, progreso=None, cancelado=None, timeout=120):
    """Descarga el ZIP de la versión indicada a 'ruta_destino', informando del avance con
    progreso(bytes_descargados, bytes_totales). Si cancelado() devuelve True, aborta.
    Se descarga del servidor propio y, si falla (no responde, no tiene el zip o se corta a
    medias), se empieza de nuevo desde la release de GitHub de esa versión. Lanza excepción si
    fallan los dos (el llamador la captura para informar al usuario)."""
    try:
        return _descargar_de(url_del_zip(version), ruta_destino, progreso, cancelado, timeout)
    except DescargaCancelada:
        raise
    except Exception as e:
        log.warning(f"No se pudo descargar la actualización del servidor ({e}); "
                    "se intenta desde GitHub.")
    return _descargar_de(url_del_zip_github(version), ruta_destino, progreso, cancelado, timeout)


def _descargar_de(url, ruta_destino, progreso, cancelado, timeout):
    log.info(f"Descargando actualización: {url}")
    req = urllib.request.Request(url, headers=_CABECERAS)
    resp = urllib.request.urlopen(req, timeout=timeout)
    try:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        leido = 0
        # Descargamos a DISCO (no a memoria): el paquete pesa bastante.
        with open(ruta_destino, "wb") as f:
            while True:
                if cancelado and cancelado():
                    raise DescargaCancelada("Descarga cancelada.")
                trozo = resp.read(65536)
                if not trozo:
                    break
                f.write(trozo)
                leido += len(trozo)
                if progreso:
                    progreso(leido, total)
    finally:
        resp.close()
    return ruta_destino


def extraer_zip(ruta_zip, ruta_temp, progreso=None):
    """Extrae el ZIP a 'ruta_temp', QUITANDO la carpeta raíz que lo contiene (el ZIP es del tipo
    'SocialRSS/<archivos>'; queremos solo '<archivos>'). Informa con progreso(hechos, total)."""
    if os.path.exists(ruta_temp):
        shutil.rmtree(ruta_temp, ignore_errors=True)
    os.makedirs(ruta_temp, exist_ok=True)

    with zipfile.ZipFile(ruta_zip) as zf:
        infos = zf.infolist()
        if len(infos) > _MAX_MIEMBROS_ZIP:
            raise ValueError(f"El paquete contiene demasiados archivos ({len(infos)}); "
                             "no se instalará.")
        miembros = [i.filename for i in infos]
        total = len(miembros) or 1
        # Detectar el prefijo de la carpeta raíz contenedora (p. ej. 'SocialRSS/').
        prefijo = ""
        if miembros:
            top = miembros[0].split("/")[0]
            if top and all(m == top or m.startswith(top + "/") for m in miembros):
                prefijo = top + "/"
        # Guardia anti "Zip Slip": ningún miembro del zip puede escribir FUERA de ruta_temp
        # (nombres con '..' o rutas absolutas). Se compara con commonpath (startswith es
        # vulnerable a carpetas hermanas con prefijo común). Además se limita la expansión
        # total y por archivo, copiando en streaming (nunca el miembro entero a memoria).
        raiz_segura = os.path.realpath(ruta_temp)
        total_escrito = 0
        for i, info in enumerate(infos):
            miembro = info.filename
            rel = miembro[len(prefijo):] if prefijo and miembro.startswith(prefijo) else miembro
            if rel:
                destino = os.path.realpath(os.path.join(ruta_temp, rel))
                if os.path.commonpath([raiz_segura, destino]) != raiz_segura:
                    log.warning(f"Miembro del zip fuera de la carpeta de extracción (omitido): {miembro}")
                    if progreso:
                        progreso(i + 1, total)
                    continue
                if miembro.endswith("/"):
                    os.makedirs(destino, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(destino), exist_ok=True)
                    escrito = 0
                    with zf.open(info) as src, open(destino, "wb") as dst:
                        while True:
                            bloque = src.read(1024 * 1024)
                            if not bloque:
                                break
                            escrito += len(bloque)
                            total_escrito += len(bloque)
                            if escrito > _MAX_TAM_MIEMBRO or total_escrito > _MAX_TAM_TOTAL:
                                raise ValueError("El paquete se expande más de lo permitido; "
                                                 "no se instalará.")
                            dst.write(bloque)
            if progreso:
                progreso(i + 1, total)

    # Comprobación de cordura ANTES de aplicar nada: el paquete extraído debe parecer una
    # instalación de SocialRSS (ejecutable o main.py). Evita "actualizar" con un zip vacío
    # o ajeno y dejar la instalación rota.
    if not any(os.path.exists(os.path.join(ruta_temp, n))
               for n in ("SocialRSS.exe", "main.py")):
        raise ValueError("El paquete extraído no parece una instalación de SocialRSS; "
                         "no se aplicará.")


def comando_relanzar():
    """Comando 'start' para volver a abrir la aplicación, tanto en la versión compilada (.exe)
    como en código fuente (intérprete + script). Se usa en los .bat de actualización y reinicio."""
    if IS_FROZEN:
        return f'start "" "{sys.executable}"'
    script_py = os.path.abspath(sys.argv[0])
    return f'start "" "{sys.executable}" "{script_py}"'


def crear_script_actualizacion(ruta_base, ruta_temp, ruta_zip):
    """Crea un .bat (cmd) que aplica la actualización EN CALIENTE (con la app abierta): espera a
    que muera este proceso, copia los archivos nuevos sobre la instalación con robocopy (que NUNCA
    borra la base de datos ni la configuración del usuario: solo escribe lo que trae el paquete),
    limpia los temporales, relanza la app y se autoelimina. Devuelve la ruta del script.

    Se usa un .bat con cmd (no PowerShell) porque, lanzado con CREATE_NO_WINDOW, funciona también
    en la versión COMPILADA sin consola; con PowerShell desacoplado fallaba al arrancar el auxiliar
    (handles estándar inválidos) y no se aplicaba nada. Es el mismo patrón de AudioRecorder Pro."""
    pid = os.getpid()
    script_path = os.path.join(ruta_base, NOMBRE_SCRIPT)
    contenido = (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "set /a intento=0\r\n"
        ":esperar\r\n"
        f'tasklist /FI "PID eq {pid}" 2>NUL | find /I "{pid}" >NUL\r\n'
        "if not errorlevel 1 (\r\n"
        '    set /a "intento+=1"\r\n'
        "    if %intento% gtr 30 (\r\n"
        f"        taskkill /F /PID {pid} >nul 2>&1\r\n"
        "    ) else (\r\n"
        "        timeout /t 1 /nobreak >nul\r\n"
        "        goto esperar\r\n"
        "    )\r\n"
        ")\r\n"
        f'robocopy "{ruta_temp}" "{ruta_base}" /E /IS /IT /R:3 /W:1 /NFL /NDL /NJH /NJS >nul 2>&1\r\n'
        # robocopy devuelve >= 8 cuando NO pudo copiar algo. En ese caso se conservan los
        # temporales (para poder reintentar o diagnosticar) y se deja constancia en un
        # archivo que la app puede detectar al arrancar; con éxito, se limpia todo.
        "if errorlevel 8 (\r\n"
        f'    echo robocopy fallo con codigo %errorlevel% > "{os.path.join(ruta_base, "_actualizacion_error.log")}"\r\n'
        ") else (\r\n"
        f'    rmdir /s /q "{ruta_temp}" >nul 2>&1\r\n'
        f'    del /q "{ruta_zip}" >nul 2>&1\r\n'
        ")\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f"{comando_relanzar()}\r\n"
        'del "%~f0" >nul 2>&1\r\n'
    )
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(contenido)
    return script_path
