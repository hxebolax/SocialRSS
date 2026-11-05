#!/usr/bin/env bash
# Lanzador de SocialRSS Accessible Suite instalada como paquete de Arch Linux.
#
# El programa vive en /usr/share/socialrss, donde no se puede escribir, así que los datos de quien
# la usa (configuración, base de datos, sesiones, registros, descargas...) van a
# ${XDG_DATA_HOME:-~/.local/share}/socialrss. Los recursos de solo lectura que trae el paquete
# (documentación, traducciones, sonidos por defecto y complementos incluidos) se enlazan ahí en cada
# arranque, para que al actualizar el paquete se vean siempre los nuevos.
set -e
APP=/usr/share/socialrss
DATOS="${SOCIALRSS_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/socialrss}"

mkdir -p "$DATOS/sounds" "$DATOS/plugins"
for recurso in docs locales sounds/default; do
    # Una carpeta real (no enlace) en ese sitio sería de una copia anterior: se deja tal cual.
    if [ -L "$DATOS/$recurso" ] || [ ! -e "$DATOS/$recurso" ]; then
        ln -sfn "$APP/data/$recurso" "$DATOS/$recurso"
    fi
done
# Complementos: cada uno incluido se enlaza por separado, así se pueden añadir otros propios.
for complemento in "$APP"/data/plugins/*/; do
    nombre="$(basename "$complemento")"
    if [ -L "$DATOS/plugins/$nombre" ] || [ ! -e "$DATOS/plugins/$nombre" ]; then
        ln -sfn "${complemento%/}" "$DATOS/plugins/$nombre"
    fi
done
# Quita los enlaces de complementos que una versión nueva ya no trae.
for enlace in "$DATOS"/plugins/*; do
    [ -L "$enlace" ] && [ ! -e "$enlace" ] && rm -f "$enlace"
done

export SOCIALRSS_DATA_DIR="$DATOS"
export SOCIALRSS_UI=gtk
export GTK_A11Y="${GTK_A11Y:-atspi}"
# Dependencias de Python que no están en los repositorios de Arch: van dentro del paquete.
export PYTHONPATH="$APP/lib${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 "$APP/main.py" "$@"
