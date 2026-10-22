# SocialRSS Accessible Suite

**SocialRSS Accessible Suite** es una aplicación para Windows que reúne en una sola ventana tus
redes sociales, tus chats y tus noticias: **Mastodon, Bluesky, W Social, RSS y podcasts, Telegram,
X (experimental), IRC, Matrix, XMPP (Jabber, con pasarelas como WhatsApp), Reddit y PeerTube**.

Está pensada desde el principio para manejarse **solo con el teclado** y con **lectores de
pantalla** (NVDA y JAWS): cada lista, diálogo y aviso está diseñado para que se lea bien y no te
haga perder el foco.

- [Para quien quiere usarla](#para-quien-quiere-usarla)
- [Para quien quiere compilarla](#para-quien-quiere-compilarla)

---

## Para quien quiere usarla

### Qué vas a encontrar

- **Una ventana con tres paneles**: a la izquierda, el árbol con tus servicios, cuentas y
  buffers (Inicio, Notificaciones, chats, feeds...); en el centro, la lista de elementos; y, si
  quieres, un panel de detalles para leer el contenido con las flechas.
- **Listas que se actualizan solas sin moverte el foco**: lo nuevo aparece en su sitio y el lector
  no te vuelve a leer nada. Las redes que lo admiten funcionan en **tiempo real**.
- **Bandeja de entrada unificada**: un único buffer con lo importante de varias redes.
- **Cada red, completa**:
  - Mastodon: publicar (con encuestas, avisos de contenido, adjuntos con descripción, programado,
    hilos), citas, filtros del servidor, listas, hashtags, notificaciones agrupadas, borradores,
    conversaciones y herramientas de moderación.
  - Bluesky y W Social: publicaciones, hilos, mensajes privados, listas, moderación y palabras
    silenciadas.
  - RSS y podcasts: carpetas, lectura del artículo completo, sincronización con servidores
    compatibles con Google Reader (como The Old Reader), reglas y descarga de episodios.
  - Telegram: chats, grupos, canales, bots, notas de voz, chats de voz y **Telegram Drive** (una
    nube personal sobre un canal privado).
  - Matrix con cifrado de extremo a extremo, XMPP con OMEMO y pasarelas, IRC, Reddit, PeerTube y
    X (cliente no oficial, experimental).
- **Reproductores**: radio, podcasts, vídeos de YouTube y el audio de las publicaciones, con
  control de velocidad y volumen y teclas globales.
- **Interfaz invisible**: teclas globales para leer y moverte por tus buffers sin abrir la
  ventana, desde cualquier aplicación.
- **IA y traducción**: descripción de imágenes, resúmenes y traducción con varios proveedores
  (Gemini, OpenAI, Claude y otros compatibles), además de corrector ortográfico.
- **Sonidos** para cada tipo de evento, avisos al lector, y un **complemento para NVDA** que hace
  que las publicaciones largas se lean completas en las listas.
- **Todas las teclas se pueden cambiar** desde un editor propio que avisa de los conflictos.
- **Idiomas**: español e inglés.

### Requisitos

- Windows 10 u 11 de 64 bits.
- Un lector de pantalla (NVDA o JAWS) si lo necesitas. La aplicación también se puede usar sin él.

### Descargar e instalar

1. Descarga el archivo `SocialRSS_vX.Y.Z.zip` de la última versión en la página de
   [Releases](https://github.com/hxebolax/SocialRSS/releases/latest).
2. Descomprímelo en la carpeta que quieras (por ejemplo, `C:\SocialRSS`). No necesita
   instalación ni permisos de administrador: es portable.
3. Ejecuta `SocialRSS.exe`.

Tus datos (cuentas, configuración, base de datos) se guardan en la carpeta `data`, junto al
ejecutable. Las contraseñas y los tokens se guardan cifrados y ligados a tu equipo.

### Actualizaciones

La aplicación comprueba sola si hay una versión nueva al arrancar (y en **Ayuda → Buscar
actualizaciones**), la descarga, la instala sin tocar tus datos y se reinicia. Cada paquete va
firmado digitalmente (Ed25519) y se verifica su huella SHA-256 antes de instalarlo, así que una
descarga dañada o manipulada se rechaza. Si el servidor principal no responde, la actualización
se descarga de las releases de este repositorio.

### Ayuda

Dentro de la aplicación, el menú **Ayuda** tiene el manual, la lista de teclas, las novedades de
cada versión y una prueba de sonidos. También puedes leerlos aquí:
[MANUAL.md](MANUAL.md) y [TECLAS.md](TECLAS.md).

---

## Para quien quiere compilarla

### Requisitos

- **Windows 10 u 11 de 64 bits** (la aplicación usa APIs de Windows y wxPython).
- **Python 3.13** de 64 bits (es la versión con la que se compila y se prueba).
- **Git** y **[Git LFS](https://git-lfs.com)**: la librería `basskit` va como wheel en `__pip__/`
  y pesa más de 100 MB, así que está guardada con Git LFS.
- Opcional: **Visual Studio** (Community vale) con "Desarrollo para el escritorio con C++" y
  **CMake**, solo si quieres el cifrado de extremo a extremo de Matrix (ver más abajo).

### 1. Obtener el código

```bash
git lfs install
git clone https://github.com/hxebolax/SocialRSS.git
cd SocialRSS
```

Comprueba que `__pip__/basskit-0.8.12-py3-none-win_amd64.whl` pesa unos 100 MB. Si pesa unos
pocos bytes, es que Git LFS no estaba instalado al clonar: instálalo y ejecuta `git lfs pull`.

### 2. Preparar el entorno

```bash
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` instala también `basskit` desde la carpeta `__pip__` (es una librería propia
del autor para el audio, basada en BASS). `requirements-dev.txt` añade lo necesario para compilar,
traducir y probar (cx_Freeze, polib, pytest y flake8).

### 3. Cifrado de Matrix (opcional)

El motor de cifrado de Matrix (`python-olm`) no se puede instalar con pip en Windows: hay que
compilarlo una vez. Con Visual Studio y CMake instalados, ejecuta:

```bash
tools\build_python_olm.bat
```

Sin él, la aplicación funciona igual, pero las salas cifradas de Matrix no se podrán leer.

### 4. Ejecutar desde el código

```bash
python main.py
```

### 5. Pruebas

```bash
python -m pytest tests -q
```

La prueba de firma del actualizador se salta si no tienes la clave privada del proyecto (ver más
abajo); el resto debe pasar.

### 6. Compilar el ejecutable

```bash
python setup.py build
python comprimir_build.py
```

- `setup.py build` (cx_Freeze) genera la aplicación en `build\SocialRSS\`, con `SocialRSS.exe`, y
  le añade el complemento de NVDA generado a partir de `nvda_addon/`. Con
  `python setup.py build --debug` el ejecutable abre una consola para ver los errores.
- `comprimir_build.py` crea `build\SocialRSS_vX.Y.Z.zip` y el manifiesto `build\version.json`
  (versión, SHA-256 y tamaño) que usa el actualizador, y lo firma.

**Sobre la firma:** el manifiesto se firma con una clave privada Ed25519 que solo tiene el autor
(`tools/claves/`, que no se publica). Sin ella, `comprimir_build.py` genera igualmente el zip, pero
avisa de que no puede firmar el manifiesto. Tu compilación funcionará, pero no podrá publicarse
como actualización oficial: el actualizador de la aplicación rechaza cualquier manifiesto que no
esté firmado con la clave del proyecto.

Todo lo anterior se puede hacer también desde un menú: ejecuta `build.bat` y elige la opción
(crear el entorno, instalar dependencias, ejecutar, compilar, limpiar, traducciones, pruebas...).

### Traducciones

Los textos de la interfaz están en español en el código, dentro de `_()`, y se traducen con
gettext (dominio `socialrss`) en `data/locales/`.

```bash
python scripts/generar_pot.py
python scripts/editor_po.py
python scripts/compilar_idiomas.py
```

1. `generar_pot.py` extrae todas las cadenas del código a `data/locales/socialrss.pot`.
2. `editor_po.py` es un editor accesible para crear o traducir el `.po` de un idioma, con
   traducción automática que respeta variables, atajos y aceleradores.
3. `compilar_idiomas.py` compila los `.po` a `.mo`, que es lo que carga la aplicación.

Los detalles están en [scripts/README.md](scripts/README.md).

### Estructura del proyecto

| Ruta | Contenido |
| --- | --- |
| `main.py` | Punto de entrada. |
| `app/core/` | Infraestructura: rutas, configuración, base de datos SQLite, secretos cifrados, voz, traducciones, tareas en segundo plano, registro. |
| `app/services/` | Un paquete por red o servicio (mastodon, bluesky, wsocial, rss, telegram, x, irc, matrix, xmpp, reddit, peertube), más audio, reproductor, IA, traducción y actualizador. |
| `app/ui/` | Interfaz wxPython: ventana principal (dividida en mixins), diálogos y controles accesibles. |
| `data/` | Carpeta de ejecución. En el repositorio solo va lo que se empaqueta: `bin/ffmpeg.exe`, `docs/` (changelog, manual y teclas), `locales/`, `plugins/` y `sounds/default/`. El resto (base de datos, configuración, sesiones, registros...) lo crea la aplicación al arrancar y nunca se sube. |
| `__pip__/` | Wheel de `basskit`, la librería de audio propia del autor. |
| `nvda_addon/` | Código del complemento de NVDA que se empaqueta con la aplicación. |
| `tests/` | Pruebas con pytest. |
| `tools/` | Utilidades de compilación: complemento de NVDA, `python-olm`, firma del manifiesto, traducciones, normalización de saltos de línea. |
| `scripts/` | Herramientas de traducción (plantilla, editor accesible, compilación). |
| `setup.py`, `comprimir_build.py`, `build.bat` | Compilación con cx_Freeze, empaquetado y menú de administración. |
| `MANUAL.md`, `TECLAS.md` | Manual de usuario y lista de teclas (se empaquetan en `data/docs`). |

### Normas del código

- Saltos de línea LF en todo, salvo CRLF en `.bat` y `.cmd` (`.editorconfig` y
  `.gitattributes`). Si algo entra con otro formato: `python tools/normalizar_saltos.py`.
- Nada de red ni trabajo pesado en el hilo de la interfaz: todo va en hilos de fondo, y la
  interfaz solo pinta el resultado.
- Las listas se actualizan sin mover el foco ni hacer que el lector repita lo que ya leyó.
- Los textos visibles van siempre dentro de `_()` para poder traducirlos.
