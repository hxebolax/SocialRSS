# Módulos de scraper de sitio

Aquí van los **módulos personalizados** para leer webs que no tienen RSS. Cada módulo enseña a la
app a extraer los artículos de una web concreta (un foro, un blog, un sitio con login o paginación…).

## Cómo crear el tuyo

1. Copia `_plantilla.py` con otro nombre **sin** el guion bajo inicial, por ejemplo `miforo.py`.
2. Edita la clase: pon un `ID` único, un `NAME` legible, un `URL_PATTERN` (regex de las URLs que
   maneja) y escribe el método `scrape(url)`.
3. `scrape(url)` debe devolver una **lista** de diccionarios; cada uno con al menos:
   - `title`: el título del artículo.
   - `link`: el enlace (URL absoluta).
   - `summary` (opcional): un resumen o el contenido.
4. Reinicia la app (o desactiva y reactiva el complemento **Web Scraper**).

## Cómo usarlo

Menú **RSS → Complementos → "Crear feed con scraper de sitio…"**: escribe la URL, deja el módulo en
**Automático** (lo detecta por la URL) o elígelo a mano, pulsa **Probar** para ver los artículos y
**Crear feed**. A partir de ahí esa web se sigue como un feed normal: al actualizarlo se vuelve a
llamar a tu módulo, y entran sus artículos nuevos con todo lo demás (leído/no leído, destacar, leer
en voz alta, etc.).

> Los archivos que empiezan por `_` (como `_plantilla.py`) se ignoran: son plantillas.
