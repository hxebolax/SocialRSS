# scripts/ — Herramientas de traducción (idiomas)

Utilidades para traducir SocialRSS Accessible Suite a otros idiomas. Las traducciones usan **gettext**
y viven en `data/locales/<idioma>/LC_MESSAGES/socialrss.po` (editable) y `…/socialrss.mo` (compilado,
el que carga la aplicación). El dominio es `socialrss`.

## Archivos

- **`generar_pot.py`** — Escanea el código (`app/` y `main.py`), extrae todas las cadenas envueltas en
  `_( "..." )` y genera la plantilla `data/locales/socialrss.pot`.
- **`editor_po.py`** — Editor accesible (wxPython, optimizado para NVDA) para crear y traducir los
  `.po`. Permite traducir entrada a entrada o todo el archivo con Google Translate, preservando
  variables, atajos y aceleradores. Es la herramienta principal.
- **`traductor_google.py`** — Motor de traducción (API gratuita de Google) que respeta las
  convenciones de los `.po`: preserva `{variables}`, `%s`/`%d`, `\n`/`\t`, los atajos tras tabulador
  (`\tCtrl+R`) y **reubica los aceleradores de menú `&`** en la palabra equivalente del idioma destino.
  Reintenta con varios endpoints.
- **`compilar_idiomas.py`** — Compila todos los `.po` a `.mo` (lo que usa la app en marcha).
- **`polib.py`** — Librería de manejo de archivos `.po`/`.mo` (dependencia, sin cambios).

## Flujo de trabajo para añadir un idioma

1. **Actualizar la plantilla** (cuando hayas añadido o cambiado textos en el código):
   ```
   python scripts/generar_pot.py
   ```
2. **Crear/traducir el idioma**: abre el editor:
   ```
   python scripts/editor_po.py
   ```
   - "Generar .po desde .pot" → elige el idioma. Te sugiere guardarlo ya en
     `data/locales/<idioma>/LC_MESSAGES/socialrss.po`.
   - Traduce entrada a entrada o usa "Traducir todo". Revisa y guarda.
3. **Compilar**:
   ```
   python scripts/compilar_idiomas.py
   ```
4. **Probar**: en la aplicación, Preferencias → General → Idioma (el nuevo idioma aparece solo en la
   lista al existir su `.mo`). Crea también `data/docs/manual_<idioma>.md` y
   `data/docs/teclas_<idioma>.md` para que la Ayuda se abra en ese idioma.

> Nota: el **español** es el idioma base (las cadenas ya están en español en el código), así que no
> necesita traducción; el resto de idiomas se generan a partir del `.pot`.
