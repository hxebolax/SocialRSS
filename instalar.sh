#!/usr/bin/env bash
# Instalador de SocialRSS Accessible Suite en Linux (interfaz GTK 4, accesible con Orca).
#
# No pide permisos de administrador: comprueba los paquetes del sistema y dice cuáles faltan,
# crea el entorno de Python (.venv) dentro de esta carpeta, instala requirements-linux.txt y añade
# la orden «socialrss» (~/.local/bin) y la entrada del menú de aplicaciones.
#
# Uso: ./instalar.sh               instala o actualiza
#      ./instalar.sh --desinstalar quita la orden y la entrada del menú (tus datos no se tocan)
set -euo pipefail

DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
PY="${PYTHON:-python3}"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

if [ "${1:-}" = "--desinstalar" ]; then
    rm -f "$BIN/socialrss" "$APPS/socialrss.desktop"
    echo "Se quitaron la orden socialrss y la entrada del menú. La carpeta $DIR y tus datos siguen ahí."
    exit 0
fi

# --- Paquetes del sistema -------------------------------------------------------------------
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "No se encontró python3. Instálalo con el gestor de paquetes de tu distribución."
    exit 1
fi
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo "Hace falta Python 3.12 o posterior (tienes $("$PY" -V 2>&1))."
    exit 1
fi

faltan=""
"$PY" -c 'import venv, ensurepip' >/dev/null 2>&1 || faltan="$faltan venv"
"$PY" -c 'import gi' >/dev/null 2>&1 || faltan="$faltan pygobject"
"$PY" -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk' >/dev/null 2>&1 \
    || faltan="$faltan gtk4"
"$PY" -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst' >/dev/null 2>&1 \
    || faltan="$faltan gstreamer"

if [ -n "$faltan" ]; then
    echo "Faltan paquetes del sistema:$faltan"
    ID_DISTRO="$(. /etc/os-release 2>/dev/null; echo "${ID:-} ${ID_LIKE:-}")"
    case "$ID_DISTRO" in
        *debian*|*ubuntu*)
            echo "Instálalos con:"
            echo "  sudo apt install python3-venv python3-gi gir1.2-gtk-4.0 gir1.2-gstreamer-1.0 \\"
            echo "    gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \\"
            echo "    gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-pulseaudio at-spi2-core \\"
            echo "    libsecret-tools speech-dispatcher ffmpeg" ;;
        *fedora*|*rhel*)
            echo "Instálalos con:"
            echo "  sudo dnf install python3-gobject gtk4 gstreamer1-plugins-base gstreamer1-plugins-good \\"
            echo "    gstreamer1-plugins-bad-free gstreamer1-plugins-ugly-free at-spi2-core libsecret speech-dispatcher" ;;
        *arch*)
            echo "Instálalos con:"
            echo "  sudo pacman -S python-gobject gtk4 gstreamer gst-plugins-base gst-plugins-good \\"
            echo "    gst-plugins-bad gst-plugins-ugly gst-libav at-spi2-core libsecret speech-dispatcher ffmpeg" ;;
        *)
            echo "Instala PyGObject, GTK 4, GStreamer (base, good, bad y ugly) y AT-SPI con el gestor de"
            echo "paquetes de tu distribución. La lista completa está en README.md, apartado «En Linux»." ;;
    esac
    echo "Después vuelve a ejecutar ./instalar.sh"
    exit 1
fi

gtk_ok="$("$PY" -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk
print(1 if (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 14) else 0)' 2>/dev/null || echo 0)"
if [ "$gtk_ok" != "1" ]; then
    echo "Aviso: tu GTK 4 es anterior a la 4.14; los anuncios a Orca sin mover el foco no funcionarán."
fi
for orden in secret-tool spd-say ffmpeg orca; do
    command -v "$orden" >/dev/null 2>&1 || echo "Aviso (opcional): no se encontró «$orden»."
done

# --- Entorno de Python ------------------------------------------------------------------------
# --system-site-packages: el entorno ve PyGObject y GStreamer, que vienen del sistema.
if [ ! -x "$DIR/.venv/bin/python" ] || ! "$DIR/.venv/bin/python" -c 'import gi' >/dev/null 2>&1; then
    echo "Creando el entorno de Python en $DIR/.venv ..."
    rm -rf "$DIR/.venv"
    "$PY" -m venv --system-site-packages "$DIR/.venv"
fi
echo "Instalando las dependencias de Python (puede tardar unos minutos)..."
"$DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/.venv/bin/python" -m pip install --quiet -r "$DIR/requirements-linux.txt"

# --- Orden y menú de aplicaciones ---------------------------------------------------------------
chmod +x "$DIR/scripts/socialrss" "$DIR/instalar.sh"
mkdir -p "$BIN" "$APPS"
ln -sf "$DIR/scripts/socialrss" "$BIN/socialrss"
sed -e "s#^Exec=.*#Exec=$BIN/socialrss#" -e "s#^Icon=.*#Icon=internet-news-reader#" \
    "$DIR/socialrss.desktop" > "$APPS/socialrss.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q "$APPS" || true

echo
echo "SocialRSS Accessible Suite está instalada."
echo "Arráncala desde el menú de aplicaciones o con la orden: socialrss"
case ":$PATH:" in
    *":$BIN:"*) ;;
    *) echo "Nota: $BIN no está en tu PATH; mientras tanto puedes usar $BIN/socialrss" ;;
esac
