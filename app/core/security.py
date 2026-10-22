# -*- coding: utf-8 -*-
"""
Módulo para la gestión y almacenamiento seguro de secretos y credenciales.

Los secretos (tokens de Mastodon, api_id/api_hash de Telegram, etc.) se guardan
ÚNICAMENTE dentro de la carpeta `data` (en `data/config/secrets.enc`), cifrados
con una CLAVE MAESTRA aleatoria que a su vez se guarda protegida con **DPAPI de
Windows** (CryptProtectData, ámbito del usuario actual) en
`data/config/secrets.key`. De este modo:

  * Solo el MISMO usuario de Windows en el MISMO equipo puede descifrar los
    secretos: otro usuario del PC (u otro proceso con acceso al disco pero no a
    la sesión) no puede, cosa que la antigua clave derivada de MachineGuid no
    impedía (MachineGuid identifica el equipo, pero no es un secreto).
  * Todo sigue viviendo en `data`: al borrar la carpeta desaparecen los secretos.
  * Si se copia `data` a otro equipo, no se podrá descifrar y habrá que volver a
    introducir las credenciales (para cambiar de PC está la migración).

MIGRACIÓN: los `secrets.enc` antiguos (cifrados con la clave derivada de
MachineGuid, o con la clave "legacy" anterior a esa) se descifran con las claves
antiguas y se recifran automáticamente con la clave maestra nueva. Si DPAPI no
está disponible (no-Windows), se mantiene la derivación por MachineGuid.
"""

import os
import json
import base64
import hashlib
import logging
import threading
from typing import Optional
from .paths import get_data_path

log = logging.getLogger(__name__)

# Importar cryptography para el cifrado
HAS_CRYPTOGRAPHY = False
try:
    from cryptography.fernet import Fernet
    HAS_CRYPTOGRAPHY = True
except ImportError:
    log.warning("cryptography no está disponible. Los secretos se guardarán sin cifrar (no recomendado).")


def _machine_guid() -> str:
    """MachineGuid del registro de Windows: identificador ESTABLE del equipo."""
    if os.name == "nt":
        try:
            import winreg
            # El MachineGuid vive en la vista de 64 bits del registro; forzar KEY_WOW64_64KEY
            # para leerlo igual desde un proceso de 32 bits (evita derivar una clave distinta).
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"SOFTWARE\Microsoft\Cryptography", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                guid, _ = winreg.QueryValueEx(k, "MachineGuid")
                return str(guid)
        except Exception as e:
            log.debug(f"No se pudo leer MachineGuid: {e}")
    return ""


def _identificador_maquina() -> str:
    """Identificador ESTABLE del ordenador para derivar la clave de cifrado.

    Usa SOLO el MachineGuid de Windows, que no cambia. ANTES se mezclaban también la MAC
    (uuid.getnode(), que Python puede inventar al azar) y COMPUTERNAME (renombrable): si
    cualquiera cambiaba, la clave cambiaba, los secretos dejaban de descifrarse y el
    siguiente guardado los borraba. Para descifrar ficheros antiguos creados con aquel
    método se conserva `_identificador_maquina_legacy()`."""
    guid = _machine_guid()
    if guid:
        return guid
    # Sin MachineGuid (raro, o no-Windows): respaldo estable basado en el nombre del equipo.
    return "socialrss-" + (os.environ.get("COMPUTERNAME", "") or "fallback")


def _dpapi_proteger(datos: bytes) -> Optional[bytes]:
    """Protege bytes con DPAPI (CryptProtectData, ámbito del usuario actual).
    Devuelve None si DPAPI no está disponible (no-Windows o error)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        entrada = DATA_BLOB(len(datos), ctypes.cast(ctypes.create_string_buffer(datos, len(datos)),
                                                    ctypes.POINTER(ctypes.c_char)))
        salida = DATA_BLOB()
        descripcion = ctypes.c_wchar_p("SocialRSS")
        if not ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(entrada), descripcion, None, None, None, 0, ctypes.byref(salida)):
            return None
        try:
            return ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(salida.pbData)
    except Exception as e:
        log.debug(f"DPAPI no disponible para proteger: {e}")
        return None


def _dpapi_desproteger(datos: bytes) -> Optional[bytes]:
    """Inverso de _dpapi_proteger. None si no se puede (otro usuario/equipo o error)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        entrada = DATA_BLOB(len(datos), ctypes.cast(ctypes.create_string_buffer(datos, len(datos)),
                                                    ctypes.POINTER(ctypes.c_char)))
        salida = DATA_BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(salida)):
            return None
        try:
            return ctypes.string_at(salida.pbData, salida.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(salida.pbData)
    except Exception as e:
        log.debug(f"DPAPI no disponible para desproteger: {e}")
        return None


def _identificador_maquina_legacy() -> str:
    """Reproduce el identificador ANTIGUO (MachineGuid|MAC|COMPUTERNAME) solo para poder
    descifrar y migrar secretos guardados con versiones anteriores."""
    partes = []
    guid = _machine_guid()
    if guid:
        partes.append(guid)
    try:
        import uuid
        partes.append(str(uuid.getnode()))
    except Exception:
        pass
    partes.append(os.environ.get("COMPUTERNAME", "") or "")
    return "|".join(p for p in partes if p) or "socialrss-fallback"


class SecurityManager:
    """Administrador de credenciales seguras ligadas al equipo."""

    def __init__(self):
        self._fallback_file = get_data_path("config/secrets.enc")
        self._keyfile = get_data_path("config/secrets.key")
        self._fernet = None          # clave PRINCIPAL (maestra DPAPI o, sin DPAPI, MachineGuid)
        self._fernet_mguid = None    # clave anterior derivada de MachineGuid (para migrar)
        self._fernet_legacy = None   # clave antiquísima MachineGuid|MAC|nombre (para migrar)
        # True si el fichero existe, NO está vacío y NO se pudo descifrar con ninguna clave:
        # en ese caso NO se debe sobreescribir (se haría un respaldo antes), para no borrar
        # credenciales que quizá aún sean recuperables.
        self._ilegible = False
        # CANDADO de lectura-modificación-escritura (auditoría Bluesky CORE-SEC-001): cada
        # guardado carga TODO el diccionario, cambia una clave y lo reescribe; dos rotaciones
        # de sesión simultáneas (dos cuentas refrescando a la vez) podían leer la misma
        # versión y la segunda escritura PISABA el secreto de la primera. La escritura
        # atómica evita ficheros corruptos, pero no actualizaciones perdidas: esto sí.
        self._rmw_lock = threading.RLock()
        self._init_cifrado()

    @staticmethod
    def _derivar_fernet(semilla: str):
        clave = hashlib.pbkdf2_hmac("sha256", semilla.encode("utf-8"),
                                    b"SocialRSSAccessibleSuite", 200000, dklen=32)
        return Fernet(base64.urlsafe_b64encode(clave))

    def _cargar_o_crear_clave_maestra(self):
        """Devuelve un Fernet con la clave MAESTRA aleatoria protegida por DPAPI
        (data/config/secrets.key), creándola si no existe. None si DPAPI no está
        disponible o la clave existente no se puede desproteger (otro usuario/PC)."""
        try:
            if self._keyfile.exists():
                protegida = self._keyfile.read_bytes()
                if not protegida:
                    return None
                bruta = _dpapi_desproteger(protegida)
                if bruta is None:
                    log.warning("secrets.key no se pudo desproteger con DPAPI (¿otro usuario "
                                "u otro equipo?). Se usarán las claves de respaldo.")
                    return None
                return Fernet(base64.urlsafe_b64encode(bruta[:32]))
            # No existe todavía: crear una clave aleatoria nueva y guardarla protegida.
            bruta = os.urandom(32)
            protegida = _dpapi_proteger(bruta)
            if protegida is None:
                return None
            self._keyfile.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._keyfile.with_suffix(".key.tmp")
            with open(tmp, "wb") as f:
                f.write(protegida)
                f.flush()
                os.fsync(f.fileno())
            tmp.replace(self._keyfile)
            log.info("Creada clave maestra de secretos protegida con DPAPI.")
            return Fernet(base64.urlsafe_b64encode(bruta))
        except Exception as e:
            log.error(f"No se pudo cargar/crear la clave maestra DPAPI: {e}")
            return None

    def _init_cifrado(self):
        """Inicializa el cifrado: primero la clave maestra protegida con DPAPI; si no hay
        DPAPI, la clave derivada de MachineGuid (comportamiento anterior). Las claves
        antiguas quedan disponibles SOLO para descifrar y migrar almacenes previos."""
        if not HAS_CRYPTOGRAPHY:
            return
        try:
            self._fernet_mguid = self._derivar_fernet(_identificador_maquina())
        except Exception as e:
            log.error(f"Error al inicializar el cifrado ligado a la máquina: {e}")
        self._fernet = self._cargar_o_crear_clave_maestra() or self._fernet_mguid
        if self._fernet is self._fernet_mguid:
            self._fernet_mguid = None  # sin DPAPI la clave MachineGuid ya es la principal
        try:
            legacy_seed = _identificador_maquina_legacy()
            # Solo tiene sentido si difiere de la estable.
            if legacy_seed and legacy_seed != _identificador_maquina():
                self._fernet_legacy = self._derivar_fernet(legacy_seed)
        except Exception as e:
            log.debug(f"No se pudo derivar la clave legacy de cifrado: {e}")

    def guardar_credencial(self, account_name: str, key_name: str, secret_value: str) -> bool:
        """Guarda una credencial cifrada en data, ligada a este equipo."""
        username = f"{account_name}:{key_name}"
        try:
            with self._rmw_lock:
                secretos = self._cargar_secretos()
                secretos[username] = secret_value
                self._guardar_secretos(secretos)
            return True
        except Exception as e:
            log.error(f"Error al guardar secreto '{key_name}': {e}")
            return False

    def guardar_credenciales(self, credenciales: dict) -> bool:
        """Guarda VARIAS credenciales en una única actualización atómica (un solo ciclo de
        cargar-modificar-guardar bajo el candado). Para el alta de cuentas, que antes hacía
        varios guardados sueltos: si uno fallaba, la cuenta quedaba a medias.

        `credenciales`: {(account_name, key_name): valor} o {"cuenta:clave": valor}."""
        try:
            with self._rmw_lock:
                secretos = self._cargar_secretos()
                for clave, valor in (credenciales or {}).items():
                    if isinstance(clave, tuple):
                        clave = f"{clave[0]}:{clave[1]}"
                    secretos[str(clave)] = valor
                self._guardar_secretos(secretos)
            return True
        except Exception as e:
            log.error(f"Error al guardar credenciales en bloque: {e}")
            return False

    def _respaldar_ilegible(self):
        """Renombra un secrets.enc ilegible a una copia con marca de tiempo antes de empezar
        de cero, para no perder credenciales que podrían recuperarse (en vez de pisarlas)."""
        try:
            if self._fallback_file.exists() and self._fallback_file.stat().st_size > 0:
                from datetime import datetime
                destino = self._fallback_file.with_name(
                    f"secrets.enc.ilegible-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                self._fallback_file.replace(destino)
                log.warning(f"secrets.enc no se podía descifrar; respaldado en {destino.name} "
                            f"y se empieza un almacén nuevo. Vuelve a introducir las credenciales.")
        except Exception as e:
            log.error(f"No se pudo respaldar el secrets.enc ilegible: {e}")

    def obtener_credencial(self, account_name: str, key_name: str) -> Optional[str]:
        """Recupera una credencial guardada (None si no existe o no se puede descifrar)."""
        username = f"{account_name}:{key_name}"
        try:
            return self._cargar_secretos().get(username)
        except Exception as e:
            log.error(f"Error al leer secreto '{key_name}': {e}")
            return None

    def borrar_credencial(self, account_name: str, key_name: str) -> bool:
        """Elimina una credencial guardada."""
        username = f"{account_name}:{key_name}"
        try:
            with self._rmw_lock:
                secretos = self._cargar_secretos()
                if username in secretos:
                    del secretos[username]
                    self._guardar_secretos(secretos)
                    return True
        except Exception as e:
            log.error(f"Error al borrar secreto '{key_name}': {e}")
        return False

    def _cargar_secretos(self) -> dict:
        """Carga y descifra el diccionario de secretos. {} si no existe o no descifra.

        Marca `self._ilegible = True` SOLO cuando el fichero existe, no está vacío y no se
        pudo descifrar con ninguna clave (estable ni legacy): así un guardado posterior NO
        pisará esos datos, sino que los respaldará antes."""
        self._ilegible = False
        if not self._fallback_file.exists():
            return {}
        try:
            with open(self._fallback_file, "rb") as f:
                contenido = f.read()
            if not contenido:
                return {}
            if self._fernet:
                # 1) Clave principal (maestra DPAPI o, sin DPAPI, la de MachineGuid).
                try:
                    return json.loads(self._fernet.decrypt(contenido).decode("utf-8"))
                except Exception:
                    pass
                # 2) Claves anteriores (MachineGuid y la legacy previa): si alguna funciona,
                #    los datos se recifran YA con la clave principal (migración transparente).
                for anterior, nombre in ((self._fernet_mguid, "MachineGuid"),
                                         (self._fernet_legacy, "legacy")):
                    if not anterior:
                        continue
                    try:
                        datos = json.loads(anterior.decrypt(contenido).decode("utf-8"))
                        log.info(f"Secretos descifrados con la clave antigua ({nombre}); "
                                 f"se migran a la clave nueva.")
                        try:
                            self._guardar_secretos(datos)  # recifrar ya con la clave principal
                        except Exception:
                            pass
                        return datos
                    except Exception:
                        pass
                # 3) No se pudo con ninguna clave: NO devolver {} como si estuviera vacío,
                #    marcar ilegible para no sobreescribirlo sin respaldar.
                self._ilegible = True
                log.warning("No se pudieron descifrar los secretos (¿cambió el equipo o se "
                            "copió data de otro PC?). No se sobreescribirán sin respaldar.")
                return {}
            # Sin cifrado disponible: leer como JSON plano
            try:
                return json.loads(contenido.decode("utf-8"))
            except Exception:
                return {}
        except Exception as e:
            log.error(f"Error al cargar secretos: {e}")
            return {}

    def _guardar_secretos(self, secretos: dict):
        """Cifra y guarda el diccionario de secretos en data, de forma atómica.

        Si el fichero actual era ilegible, lo respalda antes (no lo pisa) para no perder
        credenciales que quizá se puedan recuperar."""
        self._fallback_file.parent.mkdir(parents=True, exist_ok=True)
        if self._ilegible:
            self._respaldar_ilegible()
            self._ilegible = False
        datos = json.dumps(secretos).encode("utf-8")
        if self._fernet:
            datos = self._fernet.encrypt(datos)
        # Escritura atómica: fichero temporal + reemplazo, para que un fallo a mitad no deje
        # el secrets.enc corrupto (y con él, todas las credenciales perdidas). El nombre del
        # temporal es único por proceso e hilo: dos escrituras concurrentes ya no comparten
        # el mismo .tmp (auditoría Bluesky CORE-SEC-001).
        tmp = self._fallback_file.with_suffix(
            f"{self._fallback_file.suffix}.{os.getpid()}-{threading.get_ident()}.tmp")
        with open(tmp, "wb") as f:
            f.write(datos)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(self._fallback_file)


# Instancia única del gestor de seguridad para la suite
security = SecurityManager()
