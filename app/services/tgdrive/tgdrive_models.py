# -*- coding: utf-8 -*-
"""Modelos de dominio de Telegram Drive (independientes de Telegram y de wxPython).

Son objetos de datos planos que describen la jerarquía virtual de la nube: carpetas y
archivos (`ItemNube`), las partes en que se divide un archivo grande (`ParteNube`) y las
transferencias en curso (`Transferencia`). No contienen lógica de red ni de interfaz.
"""
from dataclasses import dataclass, field
from typing import Optional, List

# Tipos de elemento.
TIPO_CARPETA = "folder"
TIPO_ARCHIVO = "file"

# Estados de un elemento de la nube.
ESTADO_NORMAL = "normal"        # visible en su carpeta
ESTADO_PAPELERA = "trash"       # en la papelera
ESTADO_INCOMPLETO = "incomplete"  # subida a medias (nunca se muestra como archivo válido)
ESTADO_ELIMINADO = "deleted"    # borrado definitivo (pendiente de quitar de Telegram)

# Versión del formato de metadatos (para migraciones futuras del manifiesto).
META_VERSION = 1


class TransferenciaCancelada(Exception):
    """Se lanza cuando una subida o descarga se aborta a mitad por cancelación cooperativa del
    usuario. El servicio la trata como 'cancelada' (limpia lo temporal y no la registra como
    error). Permite detener la transferencia AL INSTANTE, sin esperar a que termine."""
    pass


@dataclass
class ParteNube:
    """Una parte de un archivo. Un archivo pequeño tiene UNA parte; uno grande, varias,
    cada una enviada como un documento independiente al canal privado."""
    orden: int                       # 0..num_parts-1
    size: int = 0                    # bytes de esta parte
    sha256: Optional[str] = None     # hash de la parte (para reintentar solo las que falten)
    message_id: Optional[int] = None  # id del mensaje en el canal
    tg_file_id: Optional[str] = None  # file_id del documento (para descargar/reenviar)
    tg_unique_id: Optional[str] = None  # file_unique_id (estable, sirve para deduplicar)
    estado: str = "pending"          # pending|uploaded|failed
    id: Optional[int] = None         # PK en SQLite (autoincrement)


@dataclass
class ItemNube:
    """Un elemento de la nube: una carpeta o un archivo dentro de la jerarquía virtual."""
    id: str                          # UUID interno
    account_id: int                  # cuenta de Telegram propietaria
    name: str                        # nombre original
    tipo: str = TIPO_ARCHIVO         # TIPO_CARPETA | TIPO_ARCHIVO
    parent_id: Optional[str] = None  # carpeta padre (UUID) o None en la raíz
    path: str = ""                   # ruta virtual denormalizada ("/Fotos/2026")
    descripcion: str = ""            # descripción opcional que pone el usuario
    channel_id: Optional[int] = None
    size: int = 0
    mime: Optional[str] = None
    sha256: Optional[str] = None     # hash del contenido completo
    created_at: str = ""
    modified_at: str = ""
    estado: str = ESTADO_NORMAL
    trashed_at: Optional[str] = None
    original_parent_id: Optional[str] = None
    original_path: Optional[str] = None
    num_parts: int = 1
    encrypted: bool = False
    meta_version: int = META_VERSION
    partes: List[ParteNube] = field(default_factory=list)

    @property
    def es_carpeta(self) -> bool:
        return self.tipo == TIPO_CARPETA

    @property
    def es_archivo(self) -> bool:
        return self.tipo == TIPO_ARCHIVO


@dataclass
class Transferencia:
    """Una transferencia (subida o descarga) en la cola. Refleja su progreso y estado para
    que la interfaz lo muestre sin bloquearse."""
    id: str                          # UUID
    operacion: str                   # 'upload' | 'download'
    account_id: Optional[int] = None
    item_id: Optional[str] = None
    nombre: str = ""
    local_path: str = ""             # origen (subida) o destino (descarga)
    size_total: int = 0
    bytes_hechos: int = 0
    estado: str = "pendiente"
    error: Optional[str] = None
    prioridad: int = 0
    created_at: str = ""
    updated_at: str = ""

    @property
    def porcentaje(self) -> int:
        if self.size_total <= 0:
            return 0
        return int(min(100, self.bytes_hechos * 100 // self.size_total))


# Estados de una transferencia (los que se muestran al usuario y se guardan en la cola).
TR_PENDIENTE = "pendiente"
TR_PREPARANDO = "preparando"
TR_HASH = "calculando_hash"
TR_SUBIENDO = "subiendo"
TR_DESCARGANDO = "descargando"
TR_RECONSTRUYENDO = "reconstruyendo"
TR_VERIFICANDO = "verificando"
TR_PAUSADA = "pausada"
TR_CANCELANDO = "cancelando"
TR_CANCELADA = "cancelada"
TR_COMPLETADA = "completada"
TR_FALLIDA = "fallida"

# Etiquetas legibles (traducibles en la interfaz; aquí solo la clave canónica).
ESTADOS_ACTIVOS = {TR_PENDIENTE, TR_PREPARANDO, TR_HASH, TR_SUBIENDO,
                   TR_DESCARGANDO, TR_RECONSTRUYENDO, TR_VERIFICANDO, TR_PAUSADA}
