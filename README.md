# SocialRSS Accessible Suite

**SocialRSS Accessible Suite** es una aplicación para Windows (y, con interfaz GTK 4, para el
escritorio Linux con Orca) que reúne en una sola ventana tus
redes sociales, tus chats y tus noticias: **Mastodon, Bluesky, W Social, RSS y podcasts, Telegram,
X (experimental), IRC, Matrix, XMPP (Jabber, con pasarelas como WhatsApp), Reddit y PeerTube**.

Está pensada desde el principio para manejarse **solo con el teclado** y con **lectores de
pantalla** (NVDA y JAWS): cada lista, diálogo y aviso está diseñado para que se lea bien y no te
haga perder el foco.

- [Para quien quiere usarla](#para-quien-quiere-usarla)
- [En Linux: escritorio GTK 4 con Orca](#en-linux-escritorio-gtk-4-con-orca)
- [Para quien quiere compilarla](#para-quien-quiere-compilarla)
- [Licencia](#licencia)

---

## Para quien quiere usarla

### Qué vas a encontrar

- **Una ventana con tres paneles**: a la izquierda, el árbol con tus servicios, cuentas y
  buffers (Inicio, Notificaciones, chats, feeds...); en el centro, la lista de elementos; y, si
  quieres, un panel de detalles para leer el contenido con las flechas.
- **Listas que se actualizan solas sin moverte el foco**: lo nuevo aparece en su sitio y el lector
  no te vuelve a leer nada. Las redes que lo admiten funcionan en **tiempo real**.
- **Botonera de acciones** (opcional, una casilla por red en Preferencias, como en Windows): una fila de botones bajo la lista con
las acciones más usadas del elemento enfocado (Responder, Me gusta, Impulsar, Conversación, Marcar leído, Voto +...) cuyas
etiquetas reflejan el estado. Se llega con Tab desde la lista, Izquierda y Derecha pasan de un botón a otro, y «Más acciones…»
abre el menú completo. En la bandeja actúa sobre la red de origen de cada publicación.

**Reordenar el árbol:** con el foco en el árbol, Ctrl+Arriba y Ctrl+Abajo mueven el elemento elegido: los servicios, las cuentas,
los buffers de cada red (el orden vale para todas sus cuentas), los buffers fijos de RSS, las categorías y los feeds (los feeds
fijados siempre van arriba) y los buffers abiertos en la sesión (menos las conversaciones, cronologías y búsquedas de Mastodon, Bluesky y W Social, que se conservan entre sesiones y se reordenan dentro de su grupo). En X se mueven también las listas, las búsquedas, las cronologías y las conversaciones guardadas, y en Reddit los subreddits, las búsquedas y los demás buffers guardados; siempre dentro de su grupo. El orden se guarda en la misma configuración que usa Windows.

**Bandeja de entrada unificada**: un único buffer con lo importante de varias redes.
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

## En Linux: escritorio GTK 4 con Orca

En Linux la aplicación usa una interfaz propia escrita en **GTK 4**, pensada para el lector de
pantalla **Orca**. El núcleo (base de datos, redes, RSS, cifrado de credenciales...) es el mismo
que en Windows; solo cambia la ventana, la voz y el audio.

**Estado actual:** RSS y podcasts completos (árbol de carpetas y feeds, lectura, búsqueda, OPML,
texto completo, reproductor con velocidad) y **Mastodon**: varias cuentas, Inicio, Notificaciones,
Menciones, Local, Federada, Mensajes directos, Marcadores, Favoritos y Mis publicaciones;
publicar y responder (aviso de contenido, visibilidad, adjuntos con descripción y encuestas), me
gusta, impulsar, marcadores, borrar, conversaciones y cronologías de usuario como buffers del
árbol, listas, hashtags y búsquedas, votar en encuestas, citar, editar, reportar, perfil del autor
(seguir, silenciar, bloquear), cargar publicaciones anteriores, quién impulsó o marcó favorita una
publicación, historial de ediciones, silenciar conversaciones, fijar, seguidores, siguiendo,
solicitudes de seguimiento, bloqueados y silenciados, filtros del servidor, borradores (con
aviso al cerrar el editor), publicaciones programadas, hashtags en tendencia y seguidos, editar tu
perfil, descartar notificaciones, notificaciones agrupadas con su política y solicitudes, hilos
propios (un texto largo dividido en mensajes, con hilos guardados), colecciones de cuentas (las tuyas, las de otra cuenta y
aquellas en las que apareces), anuncios del servidor (con reacciones), enlaces en tendencia, directorio y cuentas sugeridas,
mis listas (crear, editar, miembros), hashtags y cuentas destacados en mi perfil, servidores bloqueados, novedades de seguidores, más
acciones sobre la cuenta de un autor (seguir con opciones, destacarla, nota, listas, bloquear su servidor), filtrar una publicación, borrar y
volver a redactar, citas y su política, información de la instancia y su administración (informes, cuentas, dominios bloqueados y permitidos, bloqueos de IP y de
correo, tendencias y descubrimiento, y estadísticas del servidor). Las novedades de Inicio, Notificaciones,
Menciones y Mensajes directos llegan en **tiempo real** (se puede apagar en Preferencias) y se
anuncian sin interrumpir a Orca. **Preferencias** en pestañas (General, Inteligencia artificial y Traducción): proveedor de IA con
sus claves y modelo, prompts, motor y idiomas de traducción, historiales y pruebas. Con ellas, Ctrl+T
traduce (primero con el servidor de Mastodon si lo ofrece), Ctrl+Alt+R resume, Ctrl+I describe
imágenes, Ctrl+Mayús+O extrae su texto y el editor puede mejorar la redacción, sugerir hashtags y
escribir el texto alternativo. Todavía faltan en esta interfaz: colecciones, anuncios de la instancia, el directorio, y la
administración avanzada (bloqueos de IP y de correo, estadísticas). **Bluesky**: cuentas con contraseña de aplicación, Inicio, Notificaciones, Menciones,
Mis publicaciones, Me gusta y Marcadores; publicar con imágenes o vídeo y texto alternativo,
responder (con control de quién puede responder), citar, me gusta, republicar, marcadores, borrar,
conversaciones, cronologías de usuario, perfil (seguir, silenciar, bloquear), búsqueda, feeds y
listas (crear y gestionar sus miembros), mensajes directos, palabras silenciadas, moderación de
contenido, servicios de etiquetado, denuncias, contraseñas de aplicación y tiempo real, además de
traducir, resumir y describir imágenes con IA, e hilos largos (Ctrl+Mayús+N, públicos o por mensaje directo). También en Bluesky y W Social: tendencias (Intro busca el tema), paquetes de inicio (ver, crear y borrar), buscar cuentas, grupos de chat (crear, entrar con un enlace, añadir y expulsar miembros, renombrar, enlace de invitación, solicitudes de entrada y salir), la salida pendiente de las dos redes, y en Bluesky las solicitudes de mensajes, las cuentas a cuya actividad sigues, las cuentas similares, los seguidores en común y los feeds del autor.
**W Social**: alta con usuario y contraseña de aplicación (con «Comprobar el servidor» para ver dónde vive la cuenta
antes de enviar nada, y «Volver a autorizar»), Inicio, Notificaciones, Menciones, Mis publicaciones, Me gusta, Marcadores y
mensajes directos; publicar con imágenes o vídeo, responder (con control de quién puede responder), citar, me gusta,
republicar, marcadores, borrar, conversaciones, citas, cronologías de usuario, perfil (seguir, silenciar, bloquear),
búsqueda, feeds, listas (crear y gestionar sus miembros), quién ha dado me gusta o republicado, silenciar hilos, denuncias,
seguidores, seguidos, bloqueadas y silenciadas, editar el perfil, contraseñas de aplicación y avisos de notificaciones y
mensajes nuevos. Comparte las teclas de Mastodon y Bluesky. Los hilos largos se escriben con Ctrl+Mayús+N, como en
Mastodon. También: borradores del servidor, salida pendiente (reintentar o descartar), palabras silenciadas, preferencias de
contenido, servicios de etiquetado, grupos de chat, paquetes de inicio y tendencias. Todavía faltan la búsqueda con filtros, la
moderación de verificación de edad y los contactos. No se ha probado con una cuenta real de W Social.
**Telegram** (necesita `kurigram`, ver más abajo): alta con teléfono, código y verificación en dos pasos
(las credenciales `api_id` y `api_hash` se piden en my.telegram.org y se indican desde el menú Telegram);
lista de chats y de chats sin leer, cada conversación como un buffer del árbol, enviar, responder, editar,
borrar, reaccionar, reenviar, copiar, descargar y abrir adjuntos, reproducir notas de voz y audios, enviar
archivos, mensajes guardados, silenciar, fijar, archivar, marcar leído, información del chat y avisos de mensajes nuevos en
**tiempo real** (por voz solo de la conversación abierta y de chats privados). También: carpetas de chats (con enlaces para
compartirlas), crear grupos, foros y canales, unirse por enlace, búsquedas (en todos los chats, en uno y de publicaciones de
canales), chats frecuentes y canales parecidos, contenido compartido; administración de grupos y canales (miembros,
administradores, enlaces de invitación, solicitudes de unión, ajustes, temas de foro, grupo de comentarios y registro de
administración); encuestas, listas de tareas, mensajes fijados y programados, botones y comandos de bots, ubicación,
contactos y dados; y la cuenta (perfil, contactos, bloqueados, sesiones, privacidad, verificación en dos pasos, historias y
tus bots). También Telegram Drive, tu nube personal en un canal privado: una ventana por cuenta para navegar por carpetas virtuales,
subir y descargar archivos y carpetas (con cola de transferencias, conflictos de nombre y reanudación), crear, renombrar,
mover, copiar, papelera, búsqueda, propiedades y el mantenimiento del índice y del canal. También álbumes, GIF, stickers y
videonotas, copiar mensajes, editar pies, saltar a una fecha, marcar leído hasta un mensaje, chats en común, privacidad de los
chats, cambio de número, importar contactos y más acciones sobre historias (archivadas, texto, privacidad y fijar). Aún faltan los
chats de voz. En Bluesky y W Social: cuentas sugeridas y cambio de nombre de usuario; en Bluesky, la vista de las cronologías y los
hilos y la lista de hilos silenciados y publicaciones ocultas; en W Social, estado de la cuenta, información del servidor,
verificación de edad, estado temporal, quién puede escribirte, copia del repositorio, feeds populares y búsqueda con filtros.
**IRC** (necesita `pydle`): redes con TLS, SASL y NickServ; cada red es una cuenta con los buffers Canales,
Privados y Avisos del servidor, y cada conversación se abre como un buffer del árbol. Se escribe con Ctrl+N
(Ctrl+R responde mencionando) y todo lo que empieza por barra es un comando (`/help` los enseña: `/me`, `/join`,
`/mode`, `/kick`, `/whois`...). Incluye usuarios del canal con sus rangos, canales del servidor con filtro,
tema, modos y baneos, nivel de aviso por canal, entradas y salidas ocultables, ignorados, amigos, ausente,
consola del servidor, exportar la conversación y reconexión automática. Los mensajes llegan en **tiempo real**
(por voz: la conversación abierta, los privados y las menciones, según el nivel de aviso). El resto de redes
**XMPP / Jabber** (necesita `slixmpp`; `slixmpp-omemo` para cifrar): alta con JID y contraseña, cuentas con
Conversaciones, Contactos, Grupos, Solicitudes y Archivados, y una rama propia por cada pasarela (WhatsApp, Discord...).
Cada conversación es un buffer del árbol. Escribir, responder citando, corregir tu último mensaje, retirarlo,
reaccionar, destacar, enviar y descargar archivos, reproducir notas de voz y audios, aceptar o rechazar
solicitudes, agregar, renombrar, bloquear y quitar contactos, ficha (vCard) e información técnica, zumbidos,
salas (unirse, directorio, ocupantes con moderación, tema, salir), fijar, silenciar, archivar, nivel de aviso,
vaciar y exportar conversaciones, cifrado OMEMO con verificación de huellas, y tu presencia, ánimo y actividad.
Los mensajes, la presencia y las solicitudes llegan en **tiempo real**. También: los comandos del servidor (formularios de
administración y de bots), las pasarelas (registrarse, comandos, chat y cancelar el registro), la búsqueda de usuarios,
editar tu ficha, cambiar la contraseña y crear o borrar cuentas en el servidor. Aún falta la grabación de notas de voz.
**Matrix** (necesita `matrix-nio`; el cifrado necesita además `python-olm`, que hay que compilar con `libolm`): alta con
usuario y contraseña, inicio de sesión por navegador (servidores con MAS/OAuth) y registro de cuentas nuevas. Cada cuenta
tiene Salas, Mensajes directos e Invitaciones, y cada sala se abre como un buffer del árbol. Escribir, responder, editar,
borrar, reaccionar, hilos, encuestas (crear, votar y cerrar), ubicaciones, fijar mensajes, reportar, ignorar usuarios,
«¿quién ha leído esto?», enviar y descargar archivos, reproducir audios, abrir enlaces, mensajes no enviados con reintento,
unirse (alias, directorio, llamar a la puerta), crear salas y espacios, mensajes directos, miembros con rangos y
moderación (invitar, expulsar, banear, cambiar el rango), ajustes de la sala (nombre, tema, alias, acceso, historial,
directorio, cifrado, versión), etiquetas favorita y baja prioridad, nivel de aviso, búsqueda en lo guardado, exportar
conversaciones, tu perfil y presencia, tus dispositivos, palabras que te avisan, verificación de dispositivos por emojis,
verificación cruzada, y exportar e importar claves. Los mensajes llegan en **tiempo real** (por voz: la conversación abierta,
los mensajes directos y las menciones, según el nivel de aviso de cada sala). El cifrado de extremo a extremo se probó en
Linux contra un Synapse local (mensajes en los dos sentidos, adjuntos cifrados, verificación por emojis, verificación
cruzada y exportar e importar claves para leer el historial desde otro usuario); en Linux se instala con
`pip install matrix-nio[e2e]` (que trae `python-olm`) y, con CMake 4 o posterior, `CMAKE_POLICY_VERSION_MINIMUM=3.5` en
el entorno, o con el `libolm` del sistema si tu distribución lo trae. La administración del servidor Synapse (usuarios, salas, mensajes reportados, tokens de registro, federación,
mantenimiento de archivos y estadísticas) se activa en Preferencias y exige una cuenta administradora. Aún falta la
grabación de notas de voz.
**X (Twitter)** (cliente no oficial, necesita `twifork`, que se importa como `twikit`; riesgo real de limitación o bloqueo
de la cuenta, que el alta te recuerda): alta leyendo las cookies del navegador (en Linux funciona Firefox), pegándolas
(`auth_token` y `ct0`) o con usuario y contraseña, y reimportarlas si la sesión caduca (se intenta sola una vez).
Cada cuenta tiene Inicio, Siguiendo, Menciones, Notificaciones, Marcadores, Seguidores, Seguidores verificados, Cuentas que
sigo y Comunidades, más sus listas, búsquedas, cronologías de usuario, conversaciones y mensajes directos como buffers del
árbol. Publicar con imágenes o vídeo y texto alternativo, con encuesta o programada, responder, citar, me gusta, retweet,
marcadores, borrar, votar en encuestas, abrir la conversación, perfil (seguir, silenciar, bloquear), quién ha dado me gusta o
hecho retweet, buscar publicaciones y cuentas, tendencias (también de un país o ciudad), publicaciones programadas, añadir cuentas a listas y mensajes
directos con tiempo real (por streaming, con un sonido cuando la otra persona escribe). **X no se actualiza solo**: se descarga al entrar en un buffer vacío y con
Ctrl+Mayús+R; la actualización automática es opcional en Preferencias. Gestión de tus listas (crear, editar, borrar y sus
miembros) y de comunidades (buscarlas, abrirlas como buffer, unirse y salir).
**Reddit** (necesita `praw`): el alta pide el identificador de una aplicación que registras tú en reddit.com/prefs/apps
(tipo «installed app», redirección `http://localhost:8080`) y abre el navegador para autorizar; la sesión se guarda cifrada. Cada
cuenta tiene Página principal, Populares, r/all, Bandeja de entrada (con sin leer, menciones y enviados), Mis publicaciones,
Mis comentarios, Guardados, Votados, Ocultos, Mis subreddits y Multireddits, más los subreddits y búsquedas que fijas (las
mismas claves que usa la versión de Windows) y los perfiles y órdenes que abres. Intro sobre una publicación abre sus comentarios
anidados, donde se puede responder, votar, guardar, editar, borrar y traducir. Además: publicar texto, enlace, imagen, vídeo,
galería o encuesta con etiqueta, +18 y spoiler, crosspost, votos, guardados, ocultar, perfiles (seguir, bloquear, mensaje), mensajes
privados, buscar publicaciones, subreddits y usuarios, y cambiar el orden de un buffer. La lectura de cada elemento usa tus
plantillas de lectura. El menú Reddit abre la wiki de un subreddit (leer, editar si tienes permiso, crear páginas y ver revisiones) y el modmail (bandejas, responder con nota interna, archivar y crear conversaciones), los hilos en directo (leer y publicar novedades), las colecciones de un subreddit y los borradores de Reddit. Etiquetas (flair), emojis y barra lateral de un subreddit. Para moderadores: las colas (cola de moderación, reportados, spam, sin moderar y editados) con aprobar, eliminar, distinguir, bloquear y mensaje de eliminación, «Moderar…» sobre cualquier publicación o comentario, los usuarios del subreddit (baneados, silenciados, contribuidores y moderadores con sus permisos), los motivos de eliminación, la configuración y el registro de moderación.
**PeerTube** (necesita `httpx`): el alta pide la instancia, el usuario y la contraseña (y el código de doble factor si lo tienes).
Cada cuenta tiene Vídeos recientes, Populares, Tendencias, Locales, Federados, Suscripciones, Directos, Notificaciones, la
rama Mi biblioteca (Mis vídeos, Mis canales, Listas de reproducción, Ver más tarde, Me gusta e Historial) y las búsquedas
guardadas (las mismas claves de configuración que la versión de Windows); los canales, cuentas y listas que abres quedan como
buffers. Intro reproduce el vídeo (suena el audio) y F2 lo pausa. Además: me gusta y no me gusta, suscribirse a canales,
comentarios con respuestas anidadas, subtítulos como texto, calidad, descargar, ver más tarde, añadir a listas, subir (con
subida reanudable) e importar vídeos, editar y borrar los tuyos, crear y gestionar listas y canales, notificaciones, silenciar
cuentas e instancias, reportar y buscar vídeos y canales, mis importaciones y las preferencias de notificaciones. También: crear directos (con sus datos de emisión, la clave oculta y las sesiones), las estadísticas de un vídeo y de la
instancia, mis denuncias, y para moderadores y administradores la cola de denuncias, los vídeos bloqueados, los usuarios, las
solicitudes de registro, la federación, los trabajos, los plugins y los tokens de runners. Reddit pide ahora que Reddit apruebe el acceso a su API (las cuentas nuevas no pueden crear la
aplicación del alta mientras tanto), así que su alta puede no estar disponible para cuentas recién registradas.

**Gestión de feeds RSS** (los mismos servicios y datos que la versión de Windows): menú contextual del árbol (tecla Menú o
Mayús+F10) con actualizar, marcar como leído, fijar, pausar, renombrar, mover, etiquetas, editar el feed (dirección con
comprobación, intervalo propio, identificación, imágenes, texto completo y qué hace Suprimir), recuperar episodios antiguos del
archivo de Internet y eliminar; en las carpetas, subcarpetas, renombrar, mover, estadísticas y eliminar. El menú Feeds
reúne el buscador de feeds y podcasts en directorios públicos, crear un feed desde una web sin RSS, añadir un canal de YouTube
o importar las suscripciones desde Google Takeout, importar OPML con vista previa, categorías, etiquetas, reglas automáticas
(varias condiciones, acciones, ámbito y prioridad), carpetas inteligentes, búsquedas guardadas como buffers del árbol, alertas por
palabra clave, salud de los feeds, descargas de podcast, sincronización con FreshRSS, Inoreader o Miniflux, historial y
estadísticas de lectura, resumen de lo no leído y exportar artículos en lote. En Artículo: etiquetas, imágenes con su texto
alternativo y la cola de podcasts. «Agrupar por fecha» (menú Feeds o Preferencias) pone delante del primer artículo de cada bloque su etiqueta: Hoy, Ayer, Esta semana, Este mes. El panel de lectura bajo la lista se puede ocultar por red y en la bandeja (Preferencias, General: «Mostrar el panel de detalles en…»; en Mastodon viene oculto, como en Windows); Intro y Alt+3 lo enseñan hasta que cambias de buffer. Lo que hace Intro sobre un artículo (también en la bandeja) se elige en Preferencias, pestaña RSS: abrir el enlace en el navegador (por defecto, como en Windows), abrir el texto en el panel de lectura o preguntar cada vez. Preferencias tiene una pestaña RSS (intervalo, avisos, límites de conservación...).

**Bandeja de entrada unificada** (la misma configuración que en Windows: se activa en Preferencias o en Ver): un buffer que
mezcla por fecha las publicaciones de los buffers y redes que elijas (Mastodon, Bluesky, W Social, X, Reddit, PeerTube y los
buffers de RSS). Se añaden desde Ver, Añadir buffers a la bandeja, o con la tecla Menú sobre cualquier buffer del árbol.
Cada fila dice de dónde viene y todas las acciones (responder, me gusta, impulsar, conversación...) funcionan como en su buffer
de origen; Ctrl+Alt+Z salta a él. Se actualiza con Ctrl+Mayús+R y se refresca sola con lo que llega por tiempo real.

**Menú Herramientas** (las mismas funciones y los mismos datos que la versión de Windows): filtros de contenido (ocultan
publicaciones por palabra clave en Mastodon, Bluesky, W Social, X y RSS), fragmentos de texto (Ctrl+Mayús+J al redactar),
corrector ortográfico (F7 al redactar), copias de seguridad y migración a otro ordenador (la restauración y la importación se
aplican al reiniciar, y la aplicación se relanza sola), borrar datos y liberar espacio, mostrar u ocultar buffers de cada red,
centro de notificaciones de todas las redes (Ctrl+Mayús+A), publicación cruzada (Ctrl+Mayús+X), búsqueda global en lo guardado
(Ctrl+Mayús+S), borradores y programados de todas las redes, «Ponte al día» con IA (Ctrl+Mayús+Y), estadísticas, radio por
internet, el reproductor de radio, podcast y YouTube (capítulos, cola, ecualizador y saltar silencios), descripción de la
multimedia (Ctrl+Alt+K), editor de atajos de teclado (cualquier acción de la ventana), complementos (feeds populares,
suscripciones de YouTube, web scraper y anti-clickbait), editor de plantillas de lectura (las mismas que en Windows: publicaciones y notificaciones de
Mastodon, Bluesky, W Social y X, artículos RSS, chats de Telegram, IRC, Matrix y XMPP, Reddit, PeerTube y la bandeja; los indicadores
como «Favorito» cambian al momento) y visor de registros. En Feeds, la papelera de artículos; en Artículo, las versiones
anteriores; y en Ayuda, el manual, el documento de teclas, el changelog, la prueba de sonidos y la búsqueda de actualizaciones
(que en Linux avisa y abre la página de descargas, no instala nada).

**Notas de voz** (Telegram, XMPP y Matrix): Ctrl+Alt+A empieza a grabar en el chat enfocado y, pulsado otra vez, la envía;
Ctrl+Alt+Mayús+A la cancela. En Linux se graba con GStreamer (en Windows sigue siendo BASS), con el micrófono y el volumen
que se eligen en Preferencias, y se codifica a Opus con ffmpeg o, si no lo hay, con el propio GStreamer (`opusenc`).

### Qué necesitas

Paquetes del sistema (nombres de Arch; en Debian/Ubuntu y Fedora son los equivalentes):

- `gtk4`, `python-gobject` (PyGObject) y `at-spi2-core` (accesibilidad, Orca).
- `gstreamer`, `gst-plugins-base`, `gst-plugins-good`, `gst-plugins-bad`, `gst-plugins-ugly`
  (reproductor y sonidos; los dos últimos dan los códecs de audio habituales).
- Opcionales: `libsecret` (guarda la clave de cifrado en el llavero del escritorio), `speech-dispatcher`
  (avisos por voz si no usas Orca), `ffmpeg`.
- Python 3.12 o superior.

```bash
python -m venv --system-site-packages .venv   # --system-site-packages: para ver PyGObject
.venv/bin/pip install -r requirements-linux.txt
scripts/socialrss                              # o: python main.py
```

En Linux `main.py` abre la interfaz GTK 4 por sí solo (`SOCIALRSS_UI=wx` fuerza wxPython).
Tus datos viven en la carpeta `data`, junto al código, igual que en Windows.

### Cómo se usa con Orca

- La ventana tiene tres paneles: **Navegación** (árbol), **Artículos** y **Contenido del
  artículo**. `Tab` y `Mayús+Tab` pasan de uno a otro (las flechas mueven por las filas, y al volver a una
  lista el foco cae en la última fila en la que estabas); `Alt+1`, `Alt+2` y `Alt+3` saltan directos.
- Cada artículo se lee entero al moverte con las flechas (estado, título, feed, autor, fecha).
- Las acciones (leído, destacado, actualizar, reproductor...) se anuncian por el lector sin mover
  el foco. `Ctrl+Mayús+B` repite la barra de estado.
- Las teclas son las mismas que en Windows siempre que tienen sentido: `Ctrl+M`, `Ctrl+D`,
  `Ctrl+L`, `Ctrl+K`, `Supr`, `Ctrl+Mayús+R`, y `F1`-`F8` para el reproductor. La lista completa
  está en **Ayuda → Teclas de la aplicación**.

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

---

## Licencia

Copyright (C) 2026 hxebolax.

SocialRSS Accessible Suite es software libre: puedes redistribuirlo y modificarlo bajo los
términos de la **Licencia Pública General de GNU, versión 3 o (a tu elección) cualquier versión
posterior** (GPL-3.0-or-later). Se distribuye con la esperanza de que sea útil, pero **sin
ninguna garantía**. El texto completo está en [LICENSE](LICENSE).

### Permiso adicional para BASS

El audio de la aplicación usa la biblioteca **BASS** de Un4seen Developments y sus complementos,
a través de `basskit`. BASS no es software libre y no es compatible con la GPL. Por eso, conforme
a la sección 7 de la GPLv3, el titular de los derechos concede este permiso adicional:

> Si modificas este programa, o cualquier obra cubierta, enlazándolo o combinándolo con la
> biblioteca BASS de Un4seen Developments o con sus complementos (o con una versión modificada de
> ellos), con lo que la obra resultante contiene partes cubiertas por sus licencias, el titular de
> los derechos de SocialRSS Accessible Suite te concede permiso adicional para distribuir esa
> obra combinada. La fuente correspondiente de la obra combinada no tiene que incluir el código
> fuente de BASS ni de sus complementos, pero sí el de las partes cubiertas por la GPL.

Este permiso solo cubre BASS y sus complementos: no se extiende a ninguna otra biblioteca. Si
redistribuyes una versión modificada, puedes conservarlo o quitarlo.

### Componentes de terceros

Cada uno mantiene su propia licencia:

- **basskit** (`__pip__/`): el código Python es MIT; las DLL que lleva dentro (BASS, sus
  complementos y FFmpeg) conservan sus licencias, detalladas en el propio paquete
  (`basskit/data/licenses` y `THIRD_PARTY_NOTICES.md`). BASS es gratuito para uso no comercial;
  el uso comercial necesita una licencia de [Un4seen](https://www.un4seen.com/bass.html#license).
- **FFmpeg** (`data/bin/ffmpeg.exe`): compilación "essentials" de
  [gyan.dev](https://www.gyan.dev/ffmpeg/builds/), con licencia GPL-3.0. Su código fuente está en
  [ffmpeg.org](https://ffmpeg.org). La aplicación lo ejecuta como un programa aparte.
- **Dependencias de Python** (`requirements.txt`): cada una con su licencia (MIT, BSD, Apache-2.0,
  LGPL-3.0, la wxWindows Library License...). `slixmpp-omemo`, que da el cifrado OMEMO de XMPP,
  es AGPL-3.0, compatible con la GPLv3 de este proyecto.
