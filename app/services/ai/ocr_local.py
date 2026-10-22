# -*- coding: utf-8 -*-
"""OCR LOCAL con el motor de Windows (Windows.Media.Ocr), sin necesidad de ninguna clave ni
proveedor de IA. Es una alternativa gratuita y sin conexión al OCR por IA (que sí requiere clave).

Usa el paquete opcional 'winsdk' (proyección de WinRT en Python). Si no está instalado, se avisa
con un mensaje claro y el usuario puede seguir usando el OCR por IA. La importación es DIFERIDA
(solo al primer uso) para no penalizar el arranque, en línea con la política de la app.
"""
import asyncio
import logging

log = logging.getLogger(__name__)


def disponible() -> bool:
    """True si el motor de OCR de Windows está disponible (winsdk instalado)."""
    try:
        import winsdk.windows.media.ocr  # noqa: F401
        return True
    except Exception:
        return False


def ocr_imagen(ruta_imagen: str, idioma: str = "es") -> str:
    """Extrae el texto de una imagen con el OCR nativo de Windows. Devuelve el texto (puede ser
    cadena vacía si no reconoce nada). Lanza excepción si winsdk no está o el idioma no tiene
    paquete de reconocimiento instalado en Windows."""
    from winsdk.windows.media.ocr import OcrEngine
    from winsdk.windows.globalization import Language
    from winsdk.windows.graphics.imaging import BitmapDecoder
    from winsdk.windows.storage import StorageFile, FileAccessMode

    async def _run():
        # Motor para el idioma pedido; si no hay paquete de ese idioma, usar el del sistema.
        engine = None
        try:
            lang = Language(idioma)
            if OcrEngine.is_language_supported(lang):
                engine = OcrEngine.try_create_from_language(lang)
        except Exception:
            engine = None
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("Windows no tiene ningún idioma de OCR instalado. "
                               "Añádelo en Configuración de Windows → Hora e idioma → Idioma.")
        archivo = await StorageFile.get_file_from_path_async(str(ruta_imagen))
        stream = await archivo.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        resultado = await engine.recognize_async(bitmap)
        return resultado.text or ""

    # WinRT es asíncrono; se ejecuta en un bucle propio de este hilo de fondo.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
