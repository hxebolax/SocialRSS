# -*- coding: utf-8 -*-
"""
Script de compilación cx_Freeze para SocialRSS Accessible Suite.
Empaqueta la aplicación en un ejecutable autónomo para Windows.
"""

import sys
import os
import re
from cx_Freeze import setup, Executable

# Si el sistema no es Windows, advertir
if sys.platform != "win32":
    print("Este script de compilación está diseñado para Windows.")
    sys.exit(1)

# Leer la versión real desde app/core/version.py (sin importar la app).
APP_VERSION = "1.0.0"
try:
    _vsrc = open(os.path.join("app", "core", "version.py"), encoding="utf-8").read()
    _m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', _vsrc)
    if _m:
        APP_VERSION = _m.group(1)
except Exception:
    pass

# Carpeta de salida del build con nombre fijo "SocialRSS" (en vez de
# exe.win-amd64-3.13). El zip final se genera desde build.bat tras compilar.
BUILD_DIR = os.path.join("build", "SocialRSS")

# Determinar archivos a incluir. TODO va dentro de 'data' (una sola estructura
# junto al .exe). Solo se empaqueta lo imprescindible para un arranque limpio;
# el resto de subcarpetas de data (config, cuentas, etc.) se crean en el primer
# arranque y los sonidos por defecto se autogeneran en data/sounds/default.
include_files = []

# Complemento de NVDA (lectura completa de listas) -> data/bin/socialRSSListViewFix.nvda-addon.
# Se genera ANTES de empaquetar data/bin para que quede incluido en la app compilada. El botón de
# Preferencias > Accesibilidad lo instala en NVDA.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
    from build_nvda_addon import construir_addon as _construir_addon_nvda
    _ruta_addon = _construir_addon_nvda()
    print("setup.py: complemento de NVDA generado en %s" % _ruta_addon)
except Exception as _e:
    print("setup.py: AVISO -> no se pudo generar el complemento de NVDA: %r" % (_e,))

# Binarios (ffmpeg) -> data/bin. ffmpeg lo usa Telegram para grabar/decodificar
# notas de voz (Opus) y para el chat de voz; sigue siendo necesario.
_bin_dir = os.path.join("data", "bin")
_ffmpeg = os.path.join(_bin_dir, "ffmpeg.exe")
# Respaldo: si ffmpeg no está en data/bin pero sí en una carpeta _data/bin antigua,
# copiarlo para no compilar sin ffmpeg.
if not os.path.exists(_ffmpeg) and os.path.exists(os.path.join("_data", "bin", "ffmpeg.exe")):
    os.makedirs(_bin_dir, exist_ok=True)
    import shutil as _shutil
    _shutil.copy2(os.path.join("_data", "bin", "ffmpeg.exe"), _ffmpeg)
    print("setup.py: ffmpeg.exe copiado de _data/bin a data/bin.")
if os.path.exists(_bin_dir) and os.listdir(_bin_dir):
    include_files.append((_bin_dir, _bin_dir))
    if not os.path.exists(_ffmpeg):
        print("setup.py: AVISO -> data/bin no contiene ffmpeg.exe; el grabado/decodificado de voz de Telegram no funcionará.")
else:
    print("setup.py: AVISO -> data/bin está vacío; coloca ffmpeg.exe en data/bin antes de compilar.")

# Manuales de usuario por idioma -> data/docs/manual_<idioma>.md.
# El español canónico es MANUAL.md (se copia como manual_es.md). Si en el futuro
# hay manuales de otros idiomas (manual_en.md, etc.), también se incluyen.
if os.path.exists("MANUAL.md"):
    include_files.append(("MANUAL.md", os.path.join("data", "docs", "manual_es.md")))
import glob as _glob
for _man in _glob.glob("manual_*.md"):
    include_files.append((_man, os.path.join("data", "docs", os.path.basename(_man))))

# Licencia (GPL-3.0-or-later): la GPL exige que el ejecutable se distribuya con su texto.
if os.path.exists("LICENSE"):
    include_files.append(("LICENSE", "LICENSE.txt"))

# Lista de teclas por idioma -> data/docs/teclas_<idioma>.md. El español canónico es
# TECLAS.md (se copia como teclas_es.md), igual que el manual.
if os.path.exists("TECLAS.md"):
    include_files.append(("TECLAS.md", os.path.join("data", "docs", "teclas_es.md")))
for _tec in _glob.glob("teclas_*.md"):
    include_files.append((_tec, os.path.join("data", "docs", os.path.basename(_tec))))

# NOTA: la carpeta 'docs/' contiene documentación de DESARROLLO (arquitectura,
# compilación, etc.) y NO se incluye en el ejecutable. En data/docs solo van el
# manual de usuario (manual_<idioma>.md) y el changelog, empaquetados arriba/abajo.

# Changelog de la versión -> data/docs/changelog.txt (se muestra al cambiar de
# versión). Se incluye desde data/docs (o desde la raíz si está ahí).
for _cl in (os.path.join("data", "docs", "changelog.txt"), "changelog.txt"):
    if os.path.exists(_cl):
        include_files.append((_cl, os.path.join("data", "docs", "changelog.txt")))
        break

# Traducciones (gettext) -> data/locales, solo si existen.
if os.path.exists(os.path.join("data", "locales")) and os.listdir(os.path.join("data", "locales")):
    include_files.append((os.path.join("data", "locales"), os.path.join("data", "locales")))

# Sonidos por defecto -> data/sounds/default, solo si existen (si no, se generan
# en el primer arranque).
_def_sounds = os.path.join("data", "sounds", "default")
if os.path.exists(_def_sounds):
    include_files.append((_def_sounds, _def_sounds))

# Complementos (plugins) -> data/plugins. Se copian ARCHIVO A ARCHIVO para NO arrastrar las
# carpetas __pycache__ ni los .pyc (cx_Freeze copiaría el directorio entero tal cual). Así el
# ejecutable incluye los complementos limpios y la app los descubre al arrancar.
_plugins_dir = os.path.join("data", "plugins")
if os.path.isdir(_plugins_dir):
    for _raiz, _dirs, _archivos in os.walk(_plugins_dir):
        _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        for _f in _archivos:
            if _f.endswith(".pyc"):
                continue
            _ruta = os.path.join(_raiz, _f)
            include_files.append((_ruta, _ruta))

# Dependencias de paquetes
packages = [
    "wx",
    "mastodon",
    "google.genai",
    # IA: servicios compatibles con OpenAI (OpenAI/DeepSeek/xAI/OpenRouter/Groq/Mistral) y Claude.
    "openai",
    "anthropic",
    "feedparser",
    # Parser de respaldo de alto rendimiento (se usa si feedparser no devuelve nada).
    "fastfeedparser",
    "bs4",
    # Saneado de HTML no confiable (artículos RSS, toots) con lista blanca de
    # etiquetas/atributos; sin él se usa un respaldo propio menos completo.
    "nh3",
    "requests",
    "cryptography",
    "readability",
    "lxml_html_clean",  # requerido por readability-lxml en lxml 5.x
    # Visor HTML accesible (AccessibleWebView) del lector de artículos. Si cx_Freeze no lo
    # encuentra, el visor degrada a wx.html2.WebView directo y, en último caso, a texto.
    "wx_accessible_webview",
    "PIL",
    "win32com",
    "basskit",
    "sqlite3",
    "asyncio",
    "html",
    # Conversión del manual Markdown -> HTML (menú Ayuda). Sin esto, el ejecutable
    # congelado no encontraba 'markdown' y mostraba el manual como texto plano.
    "markdown",
    # Etiquetas ID3 de los podcasts (notas del episodio y capítulos): import diferido.
    "mutagen",
    # Corrector ortográfico (incluye sus diccionarios de frecuencias por idioma)
    "spellchecker",
    # Extracción de audio de servicios (YouTube, SoundCloud, etc.)
    "yt_dlp",
    # Telegram (Kurigram se importa como 'pyrogram'). Se incluyen si están
    # instalados; si no, cx_Freeze los omitirá y Telegram quedará deshabilitado.
    "pyrogram",
    "tgcrypto",
    # Bluesky / AT Protocol (SDK autogenerado de lexicons). Incluye todos sus
    # subpaquetes; si no está instalado, Bluesky quedará deshabilitado.
    "atproto",
    "atproto_client",
    "atproto_core",
    "atproto_identity",
    "atproto_crypto",
    "atproto_firehose",
    "atproto_lexicon",
    "atproto_server",
    # Desde atproto 0.0.70, el paquete raíz importa al arrancar Jetstream y las suscripciones,
    # y Jetstream necesita zstandard: sin ellos, Client() fallaría al primer uso en la build.
    "atproto_jetstream",
    "atproto_subscription",
    "zstandard",
    # Dependencias nativas/transitivas de atproto que cx_Freeze puede no detectar.
    "libipld",
    "dns",        # dnspython (resolución de handles)
    "websockets", # firehose / suscripciones en tiempo real
    # X (Twitter) [CLIENTE NO OFICIAL]. Si no están instalados, cx_Freeze los omite y el
    # cliente de X queda deshabilitado. Se puede apagar del todo en feature_flags.py.
    "twikit",
    "httpx",
    "httpcore",
    "socksio",
    "pyotp",
    "filetype",
    "webvtt",
    "m3u8",
    "js2py_",      # Js2Py-3.13 instala el módulo como 'js2py_' (lo usa twikit en ui_metrics)
    "pyjsparser",
    "tzlocal",
    "tzdata",      # base de datos de zonas horarias para zoneinfo (Windows no la trae; perfil Matrix)
    "slixmpp",     # cliente XMPP (Jabber); casi puro Python con jid.pyd opcional (fallback verificado)
    "aiodns", "pyasn1", "pyasn1_modules",  # dependencias de slixmpp (puras)
    "slixmpp_omemo", "omemo", "oldmemo", "twomemo",  # cifrado OMEMO (XEP-0384); ruedas, sin compilar
    "doubleratchet", "x3dh", "xeddsa",  # cripto de OMEMO (todo en ruedas para Windows)
    "google.protobuf", "elementpath", "xmlschema",  # transitivas de OMEMO que cx_Freeze puede omitir
    "browser_cookie3",  # importar cookies de X desde el navegador
    # twscrape: segundo motor de X (solo lectura), opcional. Si no está instalado, cx_Freeze lo
    # omite y el motor twscrape queda deshabilitado (se cae a twikit). Arrastra estas:
    "twscrape",
    "aiosqlite",
    "fake_useragent",
    "loguru",
    "machineid",   # py-machineid instala el módulo como 'machineid'
    # IRC (pydle + SASL). Python puro; si no está instalado, cx_Freeze lo omite y el
    # cliente de IRC queda deshabilitado (feature flag lo oculta solo).
    "pydle",
    "puresasl",    # pure-sasl instala el módulo como 'puresasl'
    # Matrix (matrix-nio, se importa como 'nio'). El cifrado (olm y sus dependencias) se
    # añade más abajo SOLO si está instalado (tools/build_python_olm.bat).
    "nio",
    # Reddit (PRAW, síncrono). Python puro; si no está instalado, cx_Freeze lo omite y el
    # cliente de Reddit queda deshabilitado (feature flag lo oculta solo).
    "praw",
    "prawcore",       # OAuth + rate limiting (dependencia de praw)
    "update_checker",  # dependencia de praw
    "websocket",       # websocket-client: praw lo usa al subir imagen/vídeo
]

# Paquetes del cifrado E2EE de Matrix: solo si el extra está instalado en el venv (si no,
# cx_Freeze fallaría por paquete inexistente; el aviso de _libolm de más abajo lo explica).
import importlib.util as _ilu_pk
for _pk in ("olm", "peewee", "cachetools", "atomicwrites"):
    if _ilu_pk.find_spec(_pk) is not None:
        packages.append(_pk)

# Extractor del artículo completo de RSS (Trafilatura) y sus dependencias. Trae datos propios
# (settings.cfg, listas de palabras vacías de justext, datos de dateparser/babel), por eso van
# como paquetes completos. Si no está instalado, el lector usa readability-lxml.
for _pk in ("trafilatura", "justext", "htmldate", "courlan", "dateparser", "dateparser_data",
            "babel", "tld", "regex", "charset_normalizer", "dateutil", "pytz"):
    if _ilu_pk.find_spec(_pk) is not None and _pk not in packages:
        packages.append(_pk)

includes = [
    # feedparser importa sgmllib de forma que cx_Freeze no siempre lo detecta.
    "sgmllib",
]

# Motor de cifrado E2EE de Matrix: extensión C de python-olm (libolm va ESTÁTICA dentro del
# propio .pyd, no hay DLL aparte). Se fuerza SOLO si está instalada en el venv (se instala una
# vez con tools/build_python_olm.bat); si falta, la build sale igualmente pero SIN cifrado de
# Matrix, y se avisa bien claro por consola.
import importlib.util as _ilu
if _ilu.find_spec("_libolm") is not None:
    includes.append("_libolm")
    print("[build] Cifrado de Matrix (python-olm): INCLUIDO en la build.")
else:
    print("=" * 78)
    print("[build] AVISO: python-olm NO esta instalado en el venv. La build saldra SIN el")
    print("[build] cifrado de extremo a extremo de Matrix (las salas cifradas no se leeran).")
    print("[build] Para incluirlo, ejecute UNA vez: tools\\build_python_olm.bat y recompile.")
    print("=" * 78)

excludes = [
    "tkinter",
    "unittest",
    "pydoc",
    "readability.compat.two",
    # cchardet trae extensiones nativas problemáticas congeladas. chardet YA NO se
    # excluye: se fijó a la 5.2.0 (Python puro, sin mypyc; el problema era de la 7.x)
    # y readability-lxml lo necesita para la extracción de texto completo.
    "cchardet",
    # Herramientas de desarrollo que NO deben viajar en el ejecutable (auditoría
    # 2026-07 §9.2: el build arrastraba pip y pytest sin usarlos). setuptools y
    # pkg_resources NO se excluyen: click/lz4/pluggy aún los referencian.
    "pip",
    "pytest",
    "_pytest",
    "flake8",
    "pyflakes",
    "pycodestyle",
    "wheel",
    "polib",
    "cx_Freeze",
    "keyring",
]

# Configuración del ejecutable
# base="Win32GUI" oculta la consola de comandos de Windows.
# Si se compila en modo debug, se mantiene la consola.
base = None
if len(sys.argv) > 1 and "build" in sys.argv:
    # Por defecto Win32GUI para no abrir consola al usuario final
    base = "gui"

# Si el usuario añade la bandera --debug, desactivar Win32GUI para ver salida de consola
if "--debug" in sys.argv:
    base = None
    sys.argv.remove("--debug")

executables = [
    Executable(
        script="main.py",
        target_name="SocialRSS.exe",
        base=base,
        icon="data/icons/app.ico" if os.path.exists("data/icons/app.ico") else None,
        shortcut_name="SocialRSS Accessible Suite",
        shortcut_dir="DesktopFolder",
    )
]

setup(
    name="SocialRSS Accessible Suite",
    version=APP_VERSION,
    description="Cliente accesible de Mastodon, RSS y Reproductor Multimedia",
    author="SocialRSS Team",
    options={
        "build_exe": {
            "build_exe": BUILD_DIR,  # carpeta de salida = build/SocialRSS
            "packages": packages,
            "includes": includes,
            "excludes": excludes,
            "include_files": include_files,
            "include_msvcr": True,
            # Mastodon.py modifica __doc__ en decoradores; optimize=2 (-OO)
            # elimina docstrings y rompe el arranque del ejecutable.
            "optimize": 1,
        }
    },
    executables=executables,
)


# BASSKIT 0.8 incluye FFmpeg completo (unos 257 MB: avcodec, avformat, avfilter..., ffmpeg.exe,
# ffprobe.exe y el plugin bass_ffmpeg) para basskit.multimedia, que la aplicación NO usa: el
# ecualizador y "saltar silencios" de los podcasts van con BASS_FX y BASS, y la app tiene su
# propio ffmpeg.exe en data/bin (notas de voz, YouTube, Bluesky), que SÍ se empaqueta. Se quita
# del paquete tras compilar para ahorrar espacio (comprobado el 2026-09-26).
_FFMPEG_BASSKIT = ("avfilter-11.dll", "avcodec-62.dll", "avformat-62.dll", "swscale-9.dll",
                   "avdevice-62.dll", "avutil-60.dll", "swresample-6.dll", "ffmpeg.exe",
                   "ffprobe.exe", "bass_ffmpeg.dll")
if "build" in sys.argv or "build_exe" in sys.argv or "bdist_msi" in sys.argv:
    _ahorro = 0
    for _raiz, _dirs, _ficheros in os.walk(BUILD_DIR):
        if os.path.normpath(_raiz).replace("\\", "/").endswith("basskit/data/dlls/x64"):
            for _f in _ficheros:
                if _f.lower() in _FFMPEG_BASSKIT:
                    _ruta = os.path.join(_raiz, _f)
                    try:
                        _ahorro += os.path.getsize(_ruta)
                        os.remove(_ruta)
                    except OSError as _e:
                        print(f"setup.py: no se pudo quitar {_ruta}: {_e}")
    if _ahorro:
        print(f"setup.py: quitado el FFmpeg de basskit (no se usa): {_ahorro // (1024 * 1024)} MB menos.")
