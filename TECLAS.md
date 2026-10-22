# Teclas de la aplicación — SocialRSS Accessible Suite

[TOC]

Esta es la referencia **completa** de todas las combinaciones de teclas de SocialRSS y
**dónde** funciona cada una. Está agrupada por el contexto (la ventana, el panel o el
servicio) en el que se usa.

> **En los diálogos** (escribir, cuentas, preferencias, editores de hilo, radio, etc.)
> cada botón o campo tiene una **letra subrayada**: con **Alt + esa letra** lo activas.
> **Intro** acepta el diálogo (botón por defecto) y **Escape** lo **cierra sin hacer
> nada** (en TODOS los diálogos). Excepción: si tienes el foco en un **desplegable**
> (cuadro combinado o lista desplegable), Escape cierra solo ese desplegable, no el diálogo.

> **Puedes PERSONALIZAR la mayoría de estas teclas** en **Preferencias → General →
> "Personalizar combinaciones de teclas…"** (por secciones: Generales, Publicaciones, RSS,
> Herramientas, Más acciones sobre el elemento y Reproductores), y volver a las de fábrica cuando
> quieras. Este documento muestra las teclas **por defecto**; en **Ayuda → "Teclas de la
> aplicación"** verás siempre las que tengas configuradas. La navegación por posición (Alt+1 a 9,
> Ctrl+Mayús+1 a 9) no se edita, y la interfaz invisible tiene su propio editor.
>
> La sección **"Más acciones sobre el elemento"** viene **sin teclas de fábrica** ("Sin asignar"):
> impulsar o repostear, abrir en el navegador, copiar el enlace, reproducir el audio o vídeo,
> seguir o dejar de seguir al autor y abrir el menú contextual. Asígnales las que quieras.

---

## 1. Globales de la ventana principal

Funcionan desde cualquier parte de la ventana principal (lista, árbol o detalles).

| Tecla | Acción |
|---|---|
| **Ctrl+Shift+R** | Actualizar el buffer activo |
| **Alt+1 … Alt+5** | Ir al 1.º … 5.º **servicio** según el orden actual del árbol (a la última cuenta/buffer donde estuviste) |
| **Ctrl+Shift+1 … Ctrl+Shift+9** | Ir al **buffer N de la cuenta enfocada** (si hay menos, al último) |
| **Ctrl+Shift+L** | Diálogo con **todos los buffers de la cuenta enfocada**; Intro para ir |
| **Ctrl+Tab** | Cuenta siguiente (de cualquier servicio) |
| **Ctrl+Shift+Tab** | Cuenta anterior |
| **Ctrl+Shift+F** | Búsqueda global (Mastodon + Bluesky + RSS) |
| **Ctrl+Shift+B** | Leer en voz la barra de estado |
| **Ctrl+H** | Anunciar la ayuda del control enfocado (funciona en cualquier ventana y diálogo; en el visor de lectura limpia alterna texto/HTML) |
| **Ctrl+P** | Abrir Preferencias |
| **Ctrl+Q** | Salir de la aplicación |
| **Ctrl+Shift+X** | Publicación cruzada (publicar en varias redes a la vez) |
| **Ctrl+Shift+A** | Centro de notificaciones (todas las redes) |
| **Ctrl+Shift+Y** | Ponte al día (resumen con IA de lo no leído) |
| **Ctrl+Alt+P** | Abrir el reproductor multimedia (global del sistema, desde cualquier ventana) |

> **Alt+1 a Alt+9** activan el servicio por su **posición** en el árbol, no por su nombre;
> si reordenas los servicios con Ctrl+Flecha, los atajos se reasignan solos. **Ctrl+Shift+1
> a 9** y **Ctrl+Shift+L** se refieren a los **buffers de la cuenta** en la que estás.

---

## 2. Reproductores: teclas F1 a F8 (globales en toda la aplicación)

Las teclas **F1 a F8** controlan los reproductores desde **cualquier parte**, **incluso con
un diálogo abierto**. La aplicación sabe **qué reproductor está sonando** (redes sociales,
RSS/podcast o radio) y actúa sobre él.

> **Se pueden personalizar.** En *Preferencias > General > «Personalizar combinaciones de
> teclas»* hay una sección **Reproductores** con estas ocho acciones. Como funcionan en toda la
> aplicación, solo admiten **teclas de función (F1 a F12)** o **combinaciones con Ctrl, Alt o
> Mayús**: una letra suelta se comería lo que escribes en cualquier campo de texto.

**Redes sociales y RSS/podcast (audio o vídeo bajo demanda):**

| Tecla | Acción |
|---|---|
| **F1** | Retroceder (el salto configurado en Preferencias > Reproductores) |
| **F2** | Reproducir / Pausar |
| **F3** | Avanzar |
| **F4** | Detener (guarda la posición y limpia la sesión) |
| **F5** | Bajar el volumen |
| **F6** | Subir el volumen |
| **F7** | Bajar la velocidad |
| **F8** | Subir la velocidad |

**Radio (emisión en directo):**

| Tecla | Acción |
|---|---|
| **F1** | Abrir el diálogo de **emisoras favoritas** (Intro reproduce la elegida) |
| **F2** | Silenciar / quitar el silencio de la emisora |
| **F3** | Recargar la emisora (si se corta el directo) |
| **F4** | Detener la emisora |
| **F5** | Bajar el volumen |
| **F6** | Subir el volumen |
| **F7 / F8** | No son válidas en la radio (te lo avisa) |

> Si **no hay nada reproduciéndose**, al pulsar una de estas teclas se avisa de ello. El
> reproductor de redes sociales tiene **prioridad**: cuando suena un audio de una publicación,
> la radio se silencia y estas teclas actúan sobre el audio de la red social.
>
> **Importante:** F1 ya no abre el manual (está en el menú Ayuda) y F5 ya no actualiza (eso
> es Ctrl+Shift+R).

---

## 3. Árbol de navegación (panel izquierdo)

| Tecla | Acción |
|---|---|
| **Flecha derecha / izquierda** | Desplegar / contraer un nodo |
| **Flechas arriba / abajo** | Moverse entre nodos |
| **Ctrl+Flecha arriba / abajo** | Reordenar el elemento (servicio, cuenta, buffer, feed o categoría) y guardar el orden |
| **Tabulador / Shift+Tabulador** | Cambiar de panel (árbol → lista → detalles) |
| **Aplicaciones** o **Shift+F10** | Menú contextual del nodo (actualizar, renombrar, eliminar…) |

---

## 4. Lista central de elementos (panel central)

| Tecla | Acción |
|---|---|
| **Flecha arriba / abajo** | Moverse entre elementos, fila a fila (suena el sonido identificativo) |
| **Flecha izquierda / derecha** | Moverse **entre las columnas** del elemento, leyendo cada celda |
| **Ctrl+1 … Ctrl+9** | Saltar y leer directamente la columna 1…9 (autor, contenido, fecha, etc.) |
| **Alt+Flecha izquierda / derecha** | Cambiar la **unidad de movimiento** (la anuncia): Mismo autor, 1 hora, 6 horas, 1 día |
| **Alt+Flecha arriba / abajo** | Saltar al elemento anterior / siguiente que cumple la unidad (p. ej. siguiente del mismo autor, o el primero de hace más de un día) |
| **Intro** | Abrir el elemento (enlaces de la publicación, artículo, conversación de Telegram, DM de Bluesky). En RSS se puede elegir que abra el lector limpio o que pregunte (Preferencias → RSS) |
| **Aplicaciones** o **Shift+F10** | Menú contextual del elemento (responder, me gusta, traducir, IA…) |

> Las **unidades de movimiento** (Alt+flechas) sirven para recorrer timelines largos de un salto: con
> el lector de pantalla es mucho más rápido que ir fila a fila.

---

## 5. Publicaciones (Mastodon, Bluesky y X) — atajos unificados

Actúan sobre la red en la que estés (Mastodon, Bluesky, X y Reddit) y la cuenta activa. En
Telegram y RSS no crean publicaciones. En PeerTube solo aplican **Ctrl+B** (me gusta) y
**Ctrl+Shift+M** (cargar más); ver la sección 8-sexies.

| Tecla | Acción |
|---|---|
| **Ctrl+N** | Nueva publicación (toot en Mastodon, post en Bluesky/X) |
| **Ctrl+R** | Responder a la publicación enfocada |
| **Ctrl+Shift+U** | Citar la publicación enfocada |
| **Ctrl+B** | **Me gusta / favorito** (favorito en Mastodon; me gusta en Bluesky y X). Para **impulsar/repostear**, usa el menú contextual |
| **Ctrl+D** | Ver la publicación (Mastodon) |
| **Ctrl+Shift+N** | Nuevo hilo (Mastodon y Bluesky) |
| **Ctrl+Shift+M** | Cargar más publicaciones antiguas (Mastodon, Bluesky y X); en Seguidores, Siguiendo, Bloqueados y Silenciados de Mastodon carga la siguiente página de cuentas |
| **Ctrl+T** | Traducir la publicación enfocada (Mastodon, Bluesky, X y RSS) |
| **Ctrl+I** | Describir imagen con IA (Mastodon, Bluesky y X) |
| **Ctrl+Shift+O** | Extraer texto de imagen (OCR; Mastodon, Bluesky y X) |
| **Ctrl+Shift+D** | Ver la descripción (texto alternativo) de la multimedia |
| **Ctrl+Shift+V** | Ver la conversación o hilo de la publicación (Mastodon, Bluesky y X) |
| **Ctrl+Shift+T** | Ver el texto completo en un cuadro de solo lectura |
| **Ctrl+Shift+G** | Abrir la cronología de un usuario: ofrece las cuentas de la publicación que tengas delante (quien la impulsó, el autor, la citada y los mencionados, nunca la tuya) y, si prefieres otra, la escribes. Mastodon, Bluesky y X |

**Dentro del visor de conversación (Ctrl+Shift+V, en Mastodon y Bluesky):** sobre el mensaje
enfocado del hilo funcionan los mismos atajos que en una publicación normal: **Ctrl+R**
responder, **Ctrl+Shift+U** citar, **Ctrl+B** me gusta/favorito, **Ctrl+T** traducir,
**Ctrl+Shift+D** ver la descripción (texto alternativo) de la multimedia, **Ctrl+I** describir
la imagen con IA (solo Mastodon), y la **tecla Aplicaciones** abre el menú con el resto de
acciones (en Bluesky incluye repost, marcadores, abrir en el navegador, copiar enlace, reportar
y perfil del autor). Ambos visores ofrecen además **resumir el hilo con IA**, **traducir** y
**exportar** la conversación, y **abrirla como buffer** del árbol.

---

## 6. Bluesky

| Tecla | Acción |
|---|---|
| **Ctrl+N** | Nuevo post (atajo unificado, ver sección 5) |
| **Alt+N** | Activar Bluesky si es el N.º servicio del árbol (ver sección 1) |

El resto de acciones (responder, citar, me gusta, repost, nuevo hilo, cargar más, feeds,
listas, perfil, contraseñas de aplicación, palabras silenciadas, borradores) están en el
**menú Bluesky** y en el **menú contextual** de cada publicación.

---

## 7. RSS

Sobre el artículo enfocado (solo cuando el servicio activo es RSS):

| Tecla | Acción |
|---|---|
| **Ctrl+M** | Marcar como leído / no leído |
| **Ctrl+D** | Destacar / quitar destacado |
| **Ctrl+L** | Marcar / quitar "Leer más tarde" |
| **Ctrl+Shift+C** | Copiar el título al portapapeles |
| **Ctrl+Shift+E** | Abrir el artículo enfocado en el lector limpio |
| **Ctrl+K** | Ir al siguiente artículo no leído |
| **Ctrl+Shift+K** | Ir al anterior artículo no leído |
| **Suprimir** | Artículo a la papelera (o borrado definitivo si el feed lo tiene así en Editar feed; pide confirmación) |
| **Mayús+Suprimir** | Borrar sin preguntar (en los feeds con borrado definitivo) |

**Visor de artículo limpio:**

| Tecla | Acción |
|---|---|
| **Ctrl+H** | Alternar entre texto limpio y HTML accesible |
| **Ctrl+Shift+L** | Leer el artículo en voz alta (lector TTS) |
| **Ctrl++** (o Ctrl+=) | Aumentar el tamaño de letra |
| **Ctrl+-** | Reducir el tamaño de letra |
| **Ctrl+Intro** | Abrir el artículo en el navegador |
| **Alt+A** | Descargar el artículo completo de la web (se hace solo al abrir si el feed trae solo un resumen) |

**Moverse por el artículo en el visor** (en el cuadro de texto; con **Mayúsculas**, hacia atrás):

| Tecla | Acción |
|---|---|
| **H** | Encabezado siguiente (el título del artículo es el de nivel 1) |
| **1** a **6** | Encabezado de ese nivel (en los hilos de foros, 3 son los comentarios y 4, 5 y 6 sus respuestas) |
| **T** | Tabla siguiente |
| **L** / **I** | Lista / elemento de lista siguiente |
| **Q** | Cita siguiente |
| **K** | Enlace siguiente |
| **G** | Imagen siguiente (las que tienen texto alternativo) |
| **S** | Separador siguiente |
| **Z** | Salir al final de la tabla, lista o cita en la que estás (**Mayús+Z**: a su principio) |
| **Intro** | Sobre una tabla: verla en una lista (filas con arriba/abajo, columnas con izquierda/derecha; botón "Ver como HTML"). Sobre un enlace: abrirlo en el navegador |
| **Ctrl+F** | Buscar en el artículo |
| **F3** o **Ctrl+G** | Siguiente resultado (**Mayús+F3** o **Ctrl+Mayús+G**: anterior). Si F3 la usa el reproductor, vale siempre Ctrl+G |

> Otras acciones RSS están en el **menú RSS** o en el **menú del artículo/feed** (sin tecla fija):
> *Configurar Wallabag* y *Enviar a Wallabag* (leer más tarde externo), y *"descargar siempre el
> texto completo"* en **Editar feed** (menú contextual del feed).

---

## 7-bis. Bandeja de entrada unificada

En la bandeja, cada publicación usa los **atajos y el menú de su propia red** (Mastodon, Bluesky, X
o RSS), igual que en su buffer original; la app "adivina" sola a qué red pertenece la fila enfocada.

| Tecla | Acción |
|---|---|
| **Intro** | Abrir los enlaces de la publicación / artículo enfocado (según su red). En un artículo RSS, según *Preferencias → RSS → "Al pulsar Intro sobre un artículo"*: navegador, lector limpio o preguntar |
| **Atajos de la red** | Responder, me gusta, citar, traducir, leer artículo (Ctrl+Mayús+E de RSS), etc., según la fila |
| **Aplicaciones** o **Shift+F10** | Menú contextual específico de la red de la fila |

> El panel de detalles y la botonera de la bandeja son **opcionales** (Preferencias → General) y se
> adaptan también a la red de la publicación enfocada.

---

## 8. Telegram

| Tecla | Acción | Dónde |
|---|---|---|
| **Intro** | Abrir la conversación del chat enfocado | Lista de chats |
| **Ctrl+Intro** | Enviar el mensaje | Cuadro de texto de la conversación |
| **Espacio** | Si el mensaje tiene botones de bot, abre la lista de botones; si no, reproduce / pausa su audio, nota de voz o vídeo | Conversación |
| **Espacio** | Reproducir el archivo (audio/voz/vídeo) enfocado | Propiedades del chat |
| **Ctrl+I** | Describir con IA la imagen del mensaje | Conversación |
| **Ctrl+T** | Traducir el texto del mensaje | Conversación |
| **Ctrl+C** | Copiar el texto del mensaje al portapapeles | Conversación |
| **Ctrl+Shift+D** | Ver la descripción del mensaje multimedia | Conversación |
| **Aplicaciones** | Menú del mensaje (responder, reaccionar, fijar, copiar, traducir, IA…) | Conversación |

---

## 8-a. Telegram Drive (nube personal)

Ventana propia (menú Telegram → "Telegram Drive (tu nube personal)..."). Todos los atajos tienen
también su entrada de menú.

| Tecla | Acción | Dónde |
|---|---|---|
| **Intro** | Abrir carpeta, o descargar el archivo enfocado | Lista de la nube |
| **Retroceso** | Subir a la carpeta anterior | Lista de la nube |
| **Alt+Izquierda** / **Alt+Derecha** | Ir atrás / adelante en el historial | Lista de la nube |
| **Ctrl+Mayús+R** | Actualizar la vista | Lista de la nube |
| **Ctrl+Mayús+N** | Crear una carpeta nueva | Lista de la nube |
| **Ctrl+U** | Subir archivos | Lista de la nube |
| **Ctrl+Mayús+U** | Subir una carpeta entera (con su estructura) | Lista de la nube |
| **Ctrl+D** | Descargar la selección | Lista de la nube |
| **Ctrl+F** | Buscar por nombre | Lista de la nube |
| **Ctrl+F2** | Cambiar el nombre | Lista de la nube |
| **Supr** | Enviar a la papelera | Lista de la nube |
| **Mayús+Supr** | Eliminar definitivamente | Lista de la nube |
| **Espacio** | Marcar o desmarcar el elemento (casilla) para operar sobre varios a la vez | Lista de la nube |
| **Ctrl+X** / **Ctrl+C** / **Ctrl+V** | Cortar / copiar / pegar | Lista de la nube |
| **Ctrl+A** | Marcar todo | Lista de la nube |
| **Ctrl+Mayús+B** | Leer en voz la barra de estado (ubicación, elementos, marcados, transferencias, total) | Ventana de la nube |
| **Alt+Intro** | Propiedades del elemento | Lista de la nube |
| **Escape** | Cerrar la ventana | Ventana de la nube |

Las **operaciones** (descargar, papelera, cortar/copiar...) actúan sobre los elementos **marcados**
con la barra espaciadora; si no hay ninguno marcado, sobre el que tenga el foco. Para borrar toda
la nube: menú **Nube → "Eliminar Telegram Drive por completo..."** (avisa dos veces; es irreversible).

---

## 8-bis. IRC

| Tecla | Acción | Dónde |
|---|---|---|
| **Intro** | Abrir la conversación del canal o privado enfocado | Lista de chats |
| **Intro** | Enviar el mensaje | Campo de escritura de la conversación |
| **Tab** | Completar el apodo EMPEZADO (pulsa varias veces para rotar; se anuncia). Si no hay nada que completar (campo vacío o cursor tras un espacio), Tab navega por la ventana con normalidad | Campo de escritura |
| **Mayús+Tab** | Navegar hacia atrás por la ventana (SIEMPRE; nunca completa) | Campo de escritura |
| **Flecha arriba / abajo** | Recuperar tus envíos anteriores (historial de envío) | Campo de escritura |
| **Ctrl+Mayús+J** | Insertar un fragmento de texto | Campo de escritura |
| **Ctrl+Mayús+T** | Redactar traduciendo | Campo de escritura |
| **Aplicaciones** | Menú del mensaje (copiar, mencionar al autor, abrir privado) | Conversación |

Comandos con barra en el campo de escritura: `/me`, `/msg`, `/query`, `/notice`, `/join`, `/part`,
`/nick`, `/topic`, `/clear`, `/quote` y `/help`.

---

## 8-ter. Matrix

| Tecla | Acción | Dónde |
|---|---|---|
| **Intro** | Abrir la conversación de la sala o directo (en una invitación: aceptar/rechazar) | Lista de salas |
| **Intro** | Enviar el mensaje | Campo de escritura de la conversación |
| **Flecha arriba / abajo** | Recuperar tus envíos anteriores (historial de envío) | Campo de escritura |
| **Ctrl+Mayús+J** | Insertar un fragmento de texto | Campo de escritura |
| **Ctrl+Mayús+T** | Redactar traduciendo | Campo de escritura |
| **Espacio** | Descargar y abrir el adjunto del mensaje enfocado | Conversación |
| **Aplicaciones** | Menú del mensaje (responder, editar, reaccionar, borrar, copiar, mencionar, adjunto) | Conversación |

Comandos con barra: `/me`, `/clear` y `/help`.

---

## 8-quater. XMPP (Jabber / puentes como WhatsApp)

| Tecla | Acción | Dónde |
|---|---|---|
| **menú XMPP → Buscar contacto o grupo...** | Buscar contacto o grupo (filtra en vivo; Intro abre). Ya no usa Ctrl+Mayús+B, que ahora lee la barra de estado en todas partes | Con XMPP activo |
| **Intro** | Abrir la conversación (en una solicitud/invitación: aceptar/rechazar) | Listas de XMPP |
| **Intro** | Enviar el mensaje | Campo de escritura de la conversación |
| **Intro** | Abrir el enlace del mensaje enfocado | Lista de mensajes |
| **Flecha arriba / abajo** | Recuperar tus envíos anteriores | Campo de escritura |
| **Espacio** | Reproducir/pausar audio, **abrir imagen** en ventana (escanear QR) o descargar | Lista de mensajes |
| **Ctrl+T** | Traducir al español el mensaje enfocado | Lista de mensajes |
| **Ctrl+I** | Describir con IA la imagen del mensaje enfocado | Lista de mensajes |
| **Ctrl+C** | Copiar el mensaje enfocado al portapapeles | Lista de mensajes |
| **Ctrl+Mayús+M** | Cargar mensajes anteriores del historial (MAM) | Conversación |
| **Ctrl+Mayús+T** | Redactar traduciendo | Campo de escritura |
| **Aplicaciones** | Menú del mensaje (responder, reenviar, reaccionar, destacar, retirar, copiar, QR…) | Lista de mensajes |
| **Aplicaciones** | Menú del chat (fijar, silenciar, archivar, marcar leído/no leído, salir del grupo…) | Listas de XMPP |

---

## 8-quinquies. Reddit

Reddit usa los **atajos unificados de publicación** (sección 5) sobre el elemento enfocado y la
cuenta activa:

| Tecla | Acción |
|---|---|
| **Ctrl+N** | Nueva publicación (texto, enlace, imagen, vídeo, galería o encuesta) |
| **Ctrl+R** | Responder al elemento enfocado |
| **Ctrl+B** | Voto positivo / quitar el voto |
| **Ctrl+Shift+U** | Crosspost del elemento a otra comunidad |
| **Ctrl+Shift+V** | Ver el hilo de comentarios |
| **Ctrl+Shift+T** | Ver el texto completo en un cuadro de solo lectura |
| **Ctrl+Shift+M** | Cargar más publicaciones o comentarios |
| **Ctrl+T** | Traducir el elemento enfocado |
| **Ctrl+I** | Describir con IA la imagen del elemento |
| **Ctrl+Shift+O** | Extraer texto de la imagen (OCR) |
| **Alt+N** | Activar Reddit si es el N.º servicio del árbol (ver sección 1) |

El resto de acciones (voto negativo, guardar, ocultar, editar o borrar lo tuyo, marcar +18 o
spoiler, elegir flair, buscar, mensajes privados, suscribirse o abandonar comunidades,
**moderación** —cola, informes, usuarios, reglas—, **wiki**, **flairs y emojis**, **widgets**,
**modmail**, **colecciones**, **multireddits**, **borradores** e **hilos en directo**) están en el
**menú Reddit** y en el **menú contextual** (tecla Aplicaciones) de cada elemento.

---

## 8-sexies. PeerTube

PeerTube es una red de **vídeo federada** (varias instancias). Como cliente de audio, de un vídeo
**suena solo el audio**.

| Tecla | Acción |
|---|---|
| **Intro** | Reproducir el vídeo enfocado (suena el audio) |
| **Ctrl+B** | Me gusta al vídeo enfocado |
| **Ctrl+Shift+M** | Cargar más vídeos del catálogo activo |
| **Alt+N** | Activar PeerTube si es el N.º servicio del árbol (ver sección 1) |

El **transporte del reproductor** (pausa, avance, volumen, velocidad) usa las **teclas F1–F8**
globales (sección 2), igual que el resto del audio de la app.

El resto de acciones (ver subtítulos como texto, ver o publicar **comentarios** en árbol,
me gusta / no me gusta, **suscribirse** al canal, elegir **calidad**, abrir en el navegador,
añadir a **ver más tarde** o a una **lista**, **descargar**, **estadísticas**, silenciar la cuenta
o la instancia, **reportar**, y —si el vídeo es tuyo— editar o borrar) están en el **menú
contextual** (tecla Aplicaciones) de cada vídeo. En el **menú PeerTube**: buscar vídeos y canales,
ir a un canal, **subir un vídeo** (reanudable), **importar por URL o magnet**, gestionar **listas**
y **canales**, **crear un directo** (URL RTMP y clave de emisión oculta), **notificaciones** y sus
preferencias, **Mis denuncias**, **estadísticas de la instancia**, y los submenús **Moderación** y
**Administración** según tu rol.

---

## 9. IA, traducción y TTS

| Tecla | Acción |
|---|---|
| **Ctrl+I** | Describir imagen con IA |
| **Ctrl+Shift+I** | Generar texto alternativo (alt-text) con IA |
| **Ctrl+Shift+O** | Extraer texto de imagen (OCR con IA) |
| **Ctrl+T** | Traducir el elemento |
| **Ctrl+Shift+T** | En una lista: traducir el artículo o el hilo completo. **En un campo de escritura: Redactar traduciendo** (escribes en tu idioma, eliges destino y se pega la traducción) |
| **Ctrl+Shift+D** | Ver la descripción (texto alternativo) de la multimedia |

> **OCR local sin IA:** en el menú de IA, *"Extraer texto de imagen (OCR local, sin IA)"* usa el
> motor de Windows, sin clave ni conexión (no tiene tecla fija).

**Ventana del lector TTS** ("Leer en voz alta"):

| Tecla | Acción |
|---|---|
| **Alt+R** | Reproducir desde el principio |
| **Alt+P** | Pausar / Reanudar |
| **Alt+D** | Detener la lectura |
| **Esc** | Cerrar el lector |

---

## 10. Reproductor multimedia (su propia ventana)

Además de las teclas globales F1 a F8 (sección 2), dentro de la ventana del reproductor:

| Tecla | Acción |
|---|---|
| **Espacio** | Reproducir / Pausar |
| **Ctrl+Espacio** | Detener |
| **Flecha derecha / izquierda** | Avanzar / Atrasar 10 segundos |
| **Ctrl+Flecha derecha / izquierda** | Avanzar / Atrasar 60 segundos |
| **Flecha arriba / abajo** | Subir / Bajar el volumen (5 %) |
| **Ctrl+Flecha arriba / abajo** | Aumentar / Reducir la velocidad |
| **Ctrl+0** | Velocidad normal (100 %) |
| **M** | Silenciar / activar el sonido |
| **Inicio** | Ir al principio de la pista |
| **Esc** | Cerrar la ventana del reproductor |

Botones de transporte con acelerador **Alt+letra**: **Alt+R** (Reproducir/Pausar),
**Alt+D** (Detener), **Alt+A** (Atrasar), **Alt+V** (Avanzar), **Alt+N** (Anterior),
**Alt+S** (Siguiente).

**Ventana del reproductor de radio, podcast y YouTube** (menú Herramientas):

| Tecla | Acción |
|---|---|
| **Av Pág / Re Pág** | Capítulo siguiente / anterior (si el episodio tiene capítulos; hacia atrás, primero al principio del capítulo actual) |
| **Ctrl+Av Pág** | Siguiente episodio de la cola de podcasts |
| **Alt+P** | Lista de capítulos (Intro va al elegido) |
| **Alt+O** | Cola de podcasts (reproducir ahora, subir, bajar, quitar, vaciar) |
| **Alt+E** | Ecualizador (10 bandas, preamplificador y preajustes) |
| **Alt+S** | Saltar silencios (casilla) |
| **Alt+I** | Velocidad (de 0,5x a 3x en pasos de 0,1; también F7 y F8) |

---

## 11. Compositores y diálogos (general)

| Tecla | Acción | Dónde |
|---|---|---|
| **Ctrl+Espacio** | Autocompletar @mención | Compositor de Mastodon |
| **Ctrl+Mayús+Espacio** | Consultar (por voz) los caracteres restantes; en Publicación cruzada, los caracteres escritos | Compositores de Mastodon, Bluesky y Publicación cruzada |
| **Ctrl+Mayús+E** | Insertar un emoji personalizado de la instancia | Compositor de Mastodon |
| **Ctrl+Mayús+T** | Redactar traduciendo (escribes en tu idioma y se pega traducido) | Cualquier campo de escritura |
| **Ctrl+Mayús+J** | Insertar un **fragmento de texto** guardado (firmas, respuestas frecuentes…) | Cualquier campo de escritura |
| **Intro** | Enviar / Aceptar | Campos de envío y botón por defecto |
| **Esc** | **Cerrar el diálogo sin hacer nada** | **Todos los diálogos** |
| **Esc** | Cerrar solo la lista desplegable (no el diálogo) | Con el foco en un cuadro combinado o desplegable |
| **Alt + letra subrayada** | Activar el botón o campo correspondiente | Todos los diálogos |

---

## 12. Interfaz invisible (teclas globales del sistema)

Manejan **casi toda la aplicación aunque esté en segundo plano o minimizada**, sin traerla al
frente. Están **desactivadas de fábrica**: se activan en **Preferencias → Accesibilidad → "Interfaz
invisible"**. **Todas se pueden personalizar** en su propio editor (Preferencias → Accesibilidad →
"Personalizar las teclas de la interfaz invisible…", o con **Ctrl+Windows+K**), separado del editor
de atajos normales, con comprobación de conflictos en cuatro niveles. Para consultarlas rápido:
**Ayuda → "Teclas de la interfaz invisible (lista actual)"**.

Contemplan la **bandeja de entrada**, **varias cuentas** por red y los **servicios activados**.

**Navegar (por defecto, Ctrl+Windows):**

| Tecla | Acción |
|---|---|
| **Ctrl+Windows+Flecha arriba / abajo** | Elemento anterior / siguiente |
| **Ctrl+Windows+Flecha izquierda / derecha** | Buffer anterior / siguiente |
| **Ctrl+Windows+Mayús+Flecha izquierda / derecha** | Cuenta anterior / siguiente |
| **Ctrl+Windows+Mayús+Flecha arriba / abajo** | Servicio (red) anterior / siguiente |
| **Ctrl+Windows+Inicio / Fin** | Primer / último elemento |
| **Ctrl+Windows+RePág / AvPág** | Saltar 20 elementos |
| **Ctrl+Windows+Alt+Flecha izq / der** | Cambiar la unidad de movimiento |
| **Ctrl+Windows+Alt+Flecha arriba / abajo** | Saltar por la unidad (autor, 1 h, 6 h, 1 día) |
| **Ctrl+Windows+Z** | Deshacer el último salto |
| **Ctrl+Windows+Espacio** | Repetir el elemento actual |
| **Ctrl+Windows+B** | Decir el buffer y la posición |
| **Ctrl+Windows+A** | Decir la cuenta y el servicio activos |

**Actuar sobre el elemento (por defecto, Alt+Windows):**

| Tecla | Acción |
|---|---|
| **Alt+Windows+R** | Responder |
| **Alt+Windows+F** | Me gusta / favorito |
| **Alt+Windows+Mayús+R** | Impulsar / repost |
| **Alt+Windows+Q** | Citar |
| **Alt+Windows+V** | Ver publicación |
| **Alt+Windows+C** | Ver la conversación |
| **Alt+Windows+Intro** | Abrir la URL de la publicación |
| **Ctrl+Alt+Windows+Intro** | Abrir en el navegador |
| **Alt+Windows+Mayús+Intro** | Reproducir / detener audio o vídeo |
| **Alt+Windows+Mayús+C** | Copiar al portapapeles |
| **Alt+Windows+T** | Traducir |
| **Alt+Windows+O** | OCR de la imagen |
| **Alt+Windows+I** | Describir la imagen con IA |
| **Alt+Windows+S** | Seguir / dejar de seguir al autor |
| **Alt+Windows+M** | RSS: leído / no leído |
| **Alt+Windows+E** | RSS: lector limpio |
| **Ctrl+Windows+Alt+M** | Menú contextual del elemento |

**Aplicación y novedades:**

| Tecla | Acción |
|---|---|
| **Ctrl+Windows+W** | Mostrar u ocultar la ventana |
| **Ctrl+Windows+N** | Nueva publicación |
| **Ctrl+Windows+X** | Publicación cruzada |
| **Ctrl+Windows+U** | Actualizar el buffer |
| **Ctrl+Windows+O** | Preferencias |
| **Ctrl+Windows+K** | Editor de teclas invisibles |
| **Ctrl+Windows+F4** | Salir |
| **Ctrl+Alt+M** | Decir la última mención |
| **Ctrl+Alt+N** | Decir el siguiente artículo RSS sin leer |
| **Ctrl+Alt+Y** | Decir qué se está reproduciendo |
| **Ctrl+Windows+Mayús+Y** | Ponte al día (resumen con IA) |
| **Ctrl+Windows+Mayús+A** | Centro de notificaciones |
| **Ctrl+Windows+Mayús+B** | Contadores globales |
| **Ctrl+Windows+Mayús+Espacio** | Leer los últimos elementos seguidos |

> **Ctrl+Alt+P** (abrir el reproductor) es global siempre, esté o no activada la interfaz invisible.

> El contador de caracteres se actualiza solo mientras escribes; si lo desactivas en **Preferencias →
> Accesibilidad**, consúltalo con **Ctrl+Mayús+Espacio**.

---

*SocialRSS Accessible Suite — la información, sin barreras.*
