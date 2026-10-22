# Manual de SocialRSS Accessible Suite

[TOC]

**SocialRSS Accessible Suite** reúne en una sola ventana **Mastodon, Bluesky, W Social, RSS,
Telegram, X (experimental), IRC, Matrix, XMPP, Reddit y PeerTube**. Está pensada al 100 % para el **teclado** y
para lectores de pantalla (**NVDA** y **JAWS**).

Este manual es una guía breve: lo justo para entenderlo todo. La lista completa de teclas está en
**Ayuda → Teclas de la aplicación**, y los sonidos se aprenden en **Ayuda → Prueba de sonidos**.

---

## 1. La ventana y cómo moverte

Tres paneles, y cambias entre ellos con **Tabulador**:

1. **Árbol** (izquierda): servicios, cuentas y buffers (Inicio, Notificaciones, chats…). Flechas
   para moverte; **Ctrl+flecha arriba/abajo** reordena y el orden se guarda.
2. **Lista** (centro): los elementos del buffer. **Flecha arriba/abajo** va elemento a elemento
   (con su sonido); **izquierda/derecha** lee columna a columna; **Intro** abre;
   **tecla Aplicaciones** (o Mayús+F10) abre el menú de acciones.
3. **Detalles** (derecha, opcional): el contenido del elemento enfocado, para leerlo con las
   flechas. Cada red tiene en sus Preferencias las casillas de **panel de detalles** y **botonera
   de acciones** (botones que cambian según el buffer y actúan sobre el elemento enfocado).

Las listas **se actualizan solas sin mover tu foco**. Para forzar: **Ctrl+Shift+R**.

**Atajos clave:** **Alt+1 a Alt+9** saltan al servicio N del árbol; **Ctrl+Tab** cambia de cuenta;
**Ctrl+Shift+F** busca en todo el contenido; **Ctrl+P** abre Preferencias; **Ctrl+Q** sale;
**Escape** cierra cualquier diálogo.

**Arranque:** un cuadro "Cargando buffers" espera a que se descargue todo la primera vez (RSS y X
no bloquean). **Iniciar en la bandeja** (Preferencias → General): la ventana no llega a mostrarse
y todo funciona por detrás. **Minimizar a la bandeja al cerrar**: la X y Alt+F4 van a la bandeja
sin preguntar; para salir de verdad, menú **Archivo → Salir** o el menú del icono de la bandeja.
Es una app de **una sola instancia**: si la relanzas, la abierta muestra su ventana y se trae al frente, aunque estuviera oculta en la bandeja (quita su icono y reactiva el tiempo real, igual que si la restauraras desde el icono).

**Dejarlo funcionando en segundo plano desde que enciendes el equipo:** marca **Iniciar SocialRSS
automáticamente con Windows** (Preferencias → General → Ventana) y, junto a ella, **Iniciar
minimizado en la bandeja del sistema**. Con las dos, SocialRSS arranca con el equipo y se queda
trabajando por detrás —tiempo real, sondeos y avisos— sin abrir la ventana; la recuperas cuando
quieras desde el icono de la bandeja. No hace falta ser administrador: se registra solo para tu
cuenta de Windows, y puedes quitarlo también desde el Administrador de tareas, pestaña Inicio.
(No es un servicio de Windows: un servicio corre sin escritorio y el lector de pantalla no podría
manejarlo.)

## 2. Cuentas

**Archivo → Gestionar cuentas** (o el asistente inicial): añade, renombra y elimina cuentas. Cada
servicio admite **varias cuentas**, cada una con sus buffers. Las credenciales se guardan
**cifradas** en tu equipo. Puedes **desactivar servicios enteros** que no uses: cada red tiene al
principio de su sección de Preferencias la casilla **"Activar el servicio…"**. Al desmarcarla, esa
red desaparece del árbol, de los menús y del alta de cuentas **al reiniciar** (el texto junto a la
casilla te lo recuerda). Tus cuentas y datos no se borran: vuelven tal cual al reactivarla. Debe
quedar siempre al menos una red activa.

## 3. Accesibilidad

- **Avisos** por voz, mensaje o ambos (Preferencias → Accesibilidad); opcionalmente también como
  **notificación de Windows**. Cada evento tiene su **sonido identificativo** (actívalos o
  desactívalos por separado en Preferencias → Sonidos).
- **Plantillas de lectura** (Preferencias): decides qué columnas tiene cada lista y en qué orden
  las lee el lector, por red (incluidos los chats y mensajes de Telegram, XMPP…).
- **Complemento de NVDA:** NVDA corta cada columna a 511 caracteres. El botón **"Instalar
  complemento en NVDA…"** (Preferencias → Accesibilidad) lo arregla: las publicaciones largas se
  leen completas al navegar.
- **Teclas personalizables:** Preferencias → General → **"Personalizar combinaciones de
  teclas…"**. Cambia cualquier atajo (se avisa si choca), restaura por acción, sección o todo; se
  aplican al instante y **Ayuda → Teclas** siempre muestra las actuales. La sección **"Más
  acciones sobre el elemento"** trae acciones que vienen **sin tecla de fábrica** (salen como "Sin
  asignar") para que les pongas la que quieras sin pisar ninguna otra: impulsar o repostear, abrir
  la publicación o el artículo en el navegador, copiar su enlace, reproducir su audio o vídeo,
  seguir o dejar de seguir al autor y abrir el menú contextual. Funcionan también en la bandeja de
  entrada.
- **Ctrl+H** lee la ayuda del control enfocado **en cualquier ventana de la aplicación**, también
  dentro de los diálogos (preferencias, compositores, visores…): anuncia el nombre del control
  (o su etiqueta visible si no tiene nombre propio) y, si la tiene, su ayuda o consejo emergente.
  Única excepción: en el visor de lectura limpia de artículos, Ctrl+H alterna texto/HTML, como
  siempre. La **barra de estado** dice red, buffer y posición.
- **Las teclas, en los menús:** cada opción que tiene atajo lo muestra entre paréntesis (el tuyo,
  si lo has personalizado), tanto en la barra como en los menús contextuales. En los menús de
  chats no se anuncian las teclas de publicaciones aunque la opción se llame igual, porque allí
  esa tecla no hace eso.
- **Acciones comunes:** el menú de cada red termina con **"Acciones comunes a todas las redes"**,
  un submenú con lo que vale en cualquier sitio: actualizar este buffer, ver todos los buffers de
  la cuenta, cuenta siguiente/anterior, buscar en todo el contenido, centro de notificaciones,
  publicación cruzada, ponte al día, leer la barra de estado, ver la descripción de la multimedia
  y Preferencias, cada una con su tecla.
- **Menús en subcategorías (opcional):** Preferencias → General → **"Organizar los menús en
  subcategorías"**. Desactivada (por defecto), los menús son la lista plana clásica. Activada,
  los menús con muchas opciones —Mastodon, Bluesky, Reddit, PeerTube, RSS, Telegram, X, IRC,
  Matrix, XMPP y Herramientas, y también los menús contextuales grandes de las listas— se
  reagrupan en submenús descriptivos ("Publicar y responder", "Autor y cuentas", "Moderación y
  seguridad"…), de modo que cada menú es más corto y se encuentra antes cada acción. Todas las
  opciones siguen ahí con sus mismas teclas; lo que no encaja en ninguna categoría queda a primer
  nivel, debajo de los submenús. El cambio se aplica al instante al Guardar, sin reiniciar.
- **Contador de caracteres:** si notas tirones al escribir, desactiva "Actualizar el contador
  mientras escribes" y consúltalo con **Ctrl+Mayús+Espacio**.
- **Navegación por saltos:** en cualquier lista, **Alt+Izquierda/Derecha** elige la unidad (mismo
  autor, 1 hora, 6 horas, 1 día) y **Alt+Arriba/Abajo** salta a la anterior/siguiente que la
  cumpla. Las unidades de tiempo van por bloques de calendario en tu hora local.

### Interfaz invisible (manejar la app sin ventana)

Actívala en **Preferencias → Accesibilidad**: teclas **globales** (funcionan con la app en segundo
plano) para navegar por elementos, buffers, cuentas y servicios; actuar sobre el elemento
(responder, me gusta, impulsar, traducir, abrir, menú contextual…); y acciones de aplicación
(mostrar/ocultar ventana, publicar, actualizar, última mención, "Ponte al día"…). Cubre la bandeja
de entrada y varias cuentas. **Todas sus teclas se personalizan** en su propio editor
(Ctrl+Windows+K), con un **mapa para Windows 11** que evita las combinaciones que ese sistema
reserva (la app te lo ofrece sola). La tecla de "Mostrar u ocultar la ventana" alterna entre modo
ventana y modo invisible; una casilla opcional hace que los comandos invisibles solo funcionen con
la ventana oculta. Al cambiar de buffer, cuenta o servicio se dice el nombre del buffer seguido
del elemento actual y el total, como en TWBlue ("Inicio, 3 de 120"); se desactiva con la casilla
"Al cambiar de buffer en la interfaz invisible, decir el elemento actual y el total" de
**Preferencias → Accesibilidad**. Consulta sus teclas en **Ayuda → Teclas de la interfaz invisible**.

## 4. Herramientas comunes (menú Herramientas)

- **Bandeja de entrada unificada** (activar en Preferencias → General): una sola lista, por fecha,
  con los buffers que elijas de Mastodon, Bluesky, RSS y X (menú contextual del nodo → "Agregar /
  Quitar buffers…"). Cada publicación conserva **el menú y los atajos de su red**, se actualiza
  sola y tiene su propia plantilla (con columna "Origen").
  Las publicaciones **sin texto** (solo imagen, vídeo, audio o encuesta) no aparecen mudas: la
  bandeja describe la advertencia de contenido, los adjuntos (con su texto alternativo, o "Imagen
  (sin descripción)") y la encuesta, igual que el buffer normal de esa red. Si aun así no hubiera
  nada que leer, se indica con "(sin texto)".
- **Publicación cruzada:** escribe una vez y publica en varias cuentas de Mastodon, Bluesky y X a
  la vez (con imágenes; avisa si superas el límite de alguna red y qué cuenta falló).
- **Centro de notificaciones:** menciones, respuestas, me gusta, seguidores y chats sin leer de
  todas las redes, en una lista. **Ponte al día:** resumen con IA de lo pendiente.
- **Fragmentos de texto:** textos frecuentes que insertas al redactar con **Ctrl+Mayús+J**.
- **Cronología de cualquiera que salga en una publicación** (Mastodon, Bluesky y X): con
  **Ctrl+Mayús+G**, o desde el menú de la publicación, se te ofrecen las cuentas que intervienen en
  la que tienes delante —quien la impulsó o reposteó, el autor, el autor de la publicación citada y
  los **mencionados**— y abres la de quien elijas; si prefieres otra cuenta, la escribes. Tu propia
  cuenta nunca aparece en esa lista. Así puedes leer a alguien a quien solo conoces porque lo han
  mencionado, sin saberte su nombre de cuenta.
- **Borradores y programados** de todas las redes, **Estadísticas de mis redes** (datos locales) y
  **Visor de registros** (filtra por nivel, busca, sigue en vivo; útil para reportar fallos).
- **Mostrar u ocultar buffers:** elige qué buffers de cada red se ven en el árbol. El selector solo
  ofrece las redes que tienes **activas**: si desactivas una en Preferencias (o su librería no está
  instalada), no aparece aquí, igual que no aparece en el árbol ni en los menús.
- **Radio por internet** (ver sección 16) y **Copias de seguridad** (sección 18).

## 5. Mastodon

- **Buffers:** Inicio, Notificaciones, Menciones, Directos, Local, Federada, Marcadores,
  Favoritos, Enviados, Seguidores/Siguiendo, listas, hashtags y búsquedas guardadas.
- **Si tu instancia deja de responder** (está caída, o un proxy inverso devuelve una página web en
  vez de la API), se te avisa una vez indicando qué comprobar. La cuenta se sigue reintentando en
  cada sondeo, así que en cuanto el servidor vuelva funciona sola, sin tocar nada.
- **Cronologías de usuarios y conversaciones abiertas como buffer: se actualizan solas.** Las
  cronologías que añades (Ctrl+Mayús+G o el menú de una publicación) se ponen al día al entrar en
  ellas, mientras las miras y también en segundo plano, así que ya no hace falta pulsar
  **Ctrl+Mayús+R** para ver lo nuevo. Como Mastodon no ofrece tiempo real por cuenta, se consultan
  por turnos (cada cronología, como mucho, cada dos minutos) para no cargar el servidor; las
  conversaciones abiertas como buffer se refrescan una vez por minuto.
- **Publicar:** **Ctrl+N** nuevo toot (privacidad, CW, idioma, encuesta, programación y adjuntos:
  4 imágenes o un audio/vídeo, con texto alternativo); **Ctrl+R** responder (hereda CW y menciona
  a todos menos a ti; la visibilidad propuesta se elige en Preferencias → Mastodon);
  **Ctrl+Shift+U** citar (cita **nativa** si tu servidor es 4.5+; si no, por enlace visible);
  **Ctrl+B** favorito/impulso; **Ctrl+Shift+N** hilos. Al publicar, el diálogo se cierra y el
  envío sigue en segundo plano.
- **Hilos con privacidad (Ctrl+Shift+N):** el editor de hilos tiene su propio selector de
  **privacidad** (público, no listado, solo seguidores o **directo**), aviso de contenido e
  idioma, así que se puede mandar un hilo entero **en privado**. Para un hilo directo hay que
  indicar los **destinatarios**: Mastodon entrega los mensajes directos solo a las cuentas
  mencionadas *en cada publicación*, así que la app repite esa mención en todos los mensajes del
  hilo (y descuenta su longitud al trocear) para que el destinatario reciba el hilo completo, no
  solo el primero. El botón "Tomar del texto" los rellena con las cuentas que ya hayas mencionado.
  La privacidad se guarda **con el hilo**: si se interrumpe a medias y lo retomas desde "Hilos
  guardados", sigue siendo directo, no se convierte en público.
- **De una publicación larga a un hilo:** si te extiendes más de lo que admite tu instancia, no
  hace falta cancelar y empezar de nuevo. El compositor tiene el botón **"Convertir en hilo..."**,
  y si al publicar el texto se pasa del límite, se te ofrece convertirlo en hilo o guardarlo como
  borrador. Al convertirlo se cierra el compositor y se abre el editor de hilos con **todo**: el
  texto ya troceado, la privacidad, el aviso de contenido, el idioma, los adjuntos (en el primer
  mensaje) y las cuentas mencionadas, que pasan a ser los destinatarios del hilo.
- **Borradores de verdad:** al cancelar el compositor con algo escrito, se ofrece **guardarlo como
  borrador** (texto, CW, adjuntos, encuesta, programación, respuesta/cita). Se retoman en **menú
  Mastodon → Borradores…** y se borran solos al publicarse. Si un envío falla: reintentar,
  guardar como borrador o descartar.
- **Menú contextual adaptado a cada publicación:** responder, citar, impulsar (o **"Deshacer mi
  impulso"** si ya lo hiciste), favorito, marcador, ver conversación, traducir, describir imagen
  con IA, OCR, reproducir audio/vídeo, abrir en navegador, **"Borrar y volver a redactar"** (tus
  toots: los borra y reabre el compositor con el texto original), **alias local** del autor
  (cambia cómo se lee su nombre solo en tu equipo) y fijar/desfijar según el estado real.
- **Visor de conversación (Ctrl+Mayús+V):** el hilo completo en una lista navegable, con los
  mismos atajos (Ctrl+R, Ctrl+B, Ctrl+T…), resumen con IA, traducción, exportación y **"Abrir
  como buffer"** (la conversación queda como un buffer persistente del árbol).
- **Marcador de lectura sincronizado:** en Inicio/Notificaciones/Menciones retomas donde lo
  dejaste (aunque leyeras desde otro cliente) y tu posición se envía al servidor al detenerte.
  Solo avanza: subir a releer algo antiguo no la hace retroceder. Se aplica la primera vez que
  entras en el buffer en cada sesión; si ya te habías movido por él, manda tu posición. En
  Notificaciones, al actualizar sigues en la misma notificación aunque haya varias de la misma
  publicación.
- **Listas de cuentas paginadas:** Seguidores/Siguiendo/Bloqueados/Silenciados cargan 80 y
  **Ctrl+Shift+M** trae la página siguiente sin mover el foco. **Ctrl+Shift+M** también carga
  publicaciones más antiguas en los buffers normales.
- **Cuentas y perfiles:** "Buscar usuarios…" (también remotos), "Ver perfil de una cuenta…" (con
  tu **relación**: te sigue, le sigues, te bloquea), "Novedades de seguidores…" (quién te siguió y
  quién te dejó de seguir desde la última vez; Mastodon no avisa de esto, la app lo compara).
- **Más en el menú Mastodon:** traducción **con tu propio servidor** si la ofrece (Ctrl+T la usa
  primero y te dice el proveedor), **notificaciones agrupadas** reales ("A Ana y 3 más les
  gustó…", con descarte por grupo), **conversaciones de directos** (lista real del servidor),
  solicitudes de notificación **por lotes**, filtros del servidor, publicaciones programadas
  (reprogramables), hashtags (seguir, destacados), tendencias, directorio, dominios bloqueados,
  editar perfil, **"Capacidades de tu instancia"** (límites reales del servidor en lenguaje llano)
  y "Acerca de esta instancia".
- **Punto focal:** en los adjuntos de imagen del compositor eliges qué zona queda visible cuando
  otros clientes recortan la miniatura.
- **Colecciones (Mastodon 4.6):** listas **públicas** de cuentas que recomiendas ("gente que
  sabe de accesibilidad"), con nombre, descripción, idioma y etiqueta. **Menú Mastodon → "Mis
  colecciones..."** para crearlas, editarlas, borrarlas y añadir o quitar cuentas;
  **"Colecciones en las que apareces..."** muestra las de otras personas que te incluyen y te deja
  **retirarte**. Desde el menú de una publicación: **"Colecciones de este autor..."** y **"Añadir el
  autor a una de mis colecciones..."**. Cada cuenta decide si acepta aparecer: unas entran al
  momento y otras quedan "pendiente de que la acepte"; si una cuenta no lo permite, se avisa antes
  de intentarlo. Las notificaciones "te han añadido a una colección" dicen de qué colección se trata.
- **Quién puede citar:** al redactar eliges **"Pueden citarla"** (por defecto de tu cuenta,
  cualquiera, solo tus seguidores o solo tú). Solo aparece si tu servidor admite citas (4.5+).
  Desde el menú de cualquier publicación puedes **"Ver quién cita esta publicación"** (en las
  tuyas, además, retirar una cita) y **"Abrir la publicación citada"**.
- **Citas que no se pueden mostrar:** si una publicación cita a otra pero esa cita está pendiente
  de aprobación, rechazada, retirada, borrada o es de alguien que tienes bloqueado o silenciado,
  la app **te dice cuál es el caso** en lugar de que la cita desaparezca sin más. Cuando alguien de
  otro servidor cita una publicación, la cita llega primero **"pendiente"** y el servidor del autor
  la aprueba unos segundos después sin avisar; la app **vuelve a comprobarla sola** (a los 15
  segundos, al minuto, a los 5 y a los 30 minutos) y, en cuanto está aceptada, la fila pasa a
  "Citando a…" en su sitio, sin mover el foco. Al arrancar también revisa las que se quedaron
  pendientes en los últimos 7 días.
- **Ficha de una cuenta:** además de la biografía y la relación, avisa si la cuenta **se ha mudado**
  (y a cuál), si está **suspendida**, **limitada** por tu servidor, **en memoria**, si es privada o
  un grupo, y (Mastodon 4.7) si su **identificador no se puede verificar** ahora mismo (el @usuario
  que ves es provisional). También muestra sus **campos del perfil**, indicando los verificados.
- **Editar mi perfil**, ampliado: tantos **campos de metadatos** como permita tu servidor (ya no se
  pierden los que pasen de 4), **descripción del avatar y del banner** (texto alternativo, Mastodon
  4.6: es lo que oye quien llega a tu perfil con un lector de pantalla), si tus publicaciones
  públicas se pueden **buscar**, **ocultar** a quién sigues y quién te sigue, y los **valores por
  defecto al publicar** que se guardan en tu servidor: visibilidad, multimedia sensible, idioma y
  quién puede citarte.
- **Silenciar a un autor** pide **cuánto tiempo** (para siempre, 30 minutos, 1 hora, 6 horas, 1
  día, 3 días o 1 semana) y si ocultar **también sus notificaciones**.
- **Denunciar una publicación:** eliges el **motivo** (spam, contenido ilegal, incumple las normas
  del servidor u otro) y, en el tercer caso, **qué normas** de tu servidor incumple, en una lista
  de casillas; además del comentario y el reenvío al servidor del autor.
- **Conversaciones de directos:** el botón **"Quitar de mi lista"** saca una conversación de tu
  bandeja de directos (no borra los mensajes para nadie; vuelve si llega uno nuevo).
- **Filtros del servidor** (menú Mastodon → Filtros del servidor): cada filtro tiene **título**,
  **palabras** (cada una puede ser "solo palabras completas"), **publicaciones concretas**,
  **contextos** (Inicio y listas, Notificaciones, Públicas, Conversaciones, Perfiles),
  **caducidad** y **acción**. Se aplican **igual que la web oficial**:
  - **"Ocultar":** la publicación **no aparece** (ni su notificación).
  - **"Avisar":** en la lista solo se lee *[Filtrado: título]*, sin el texto. Para leerla, menú
    contextual → **"Mostrar de todos modos (filtrada)"**: se revela solo esa fila, sin mover el
    foco, hasta que cierres el programa. En *Ver publicación* se ve el aviso y el contenido.
  - **"Difuminar multimedia"** (Mastodon 4.4 o posterior; solo se ofrece si tu servidor lo
    admite): el texto se lee, pero no el texto alternativo de los adjuntos, no suena el sonido
    de multimedia y se pide confirmación antes de abrirlos, reproducirlos o describirlos con IA.
  - Cada filtro actúa solo en sus **contextos** y hasta que **caduque**, nunca sobre **tus
    propias** publicaciones. En Favoritos, Marcadores, búsquedas de texto y en la publicación
    principal de una conversación **avisa en vez de ocultar**; en los **hashtags guardados** sí
    oculta. También se aplican en el visor de conversación, en las publicaciones de un usuario,
    en las notificaciones agrupadas, en la bandeja unificada y en las **citas**.
  - **Filtrar una publicación concreta:** menú contextual → **"Filtrar esta publicación..."**:
    eliges un filtro existente o creas uno nuevo. En el diálogo de filtros, **"Publicaciones
    filtradas..."** muestra las de cada filtro y permite quitarlas.
  - **Editar:** un filtro puede no tener palabras (si tiene título). La caducidad muestra
    **"Mantener (caduca el …)"**; **"Nunca (quitar la caducidad)"** la quita de verdad. "Solo
    palabras completas" se aplica también a las palabras que ya tenía. Hay duración de 12 horas,
    los caducados se marcan *(caducado)* y eliminar pide confirmación.
  - Al crear, editar o borrar un filtro (aquí o en otra app) lo que tienes en pantalla se
    recalcula al momento, y lo que dejaste de filtrar vuelve a aparecer.
- **Administración de instancia** (solo admins): actívala en Preferencias → Mastodon y añade la
  cuenta marcando "Soy moderador o administrador". Submenú con informes (sin resolver, resueltos o
  **todos**, este último con Mastodon 4.6+), cuentas, dominios, IPs, tendencias por aprobar y
  estadísticas.

## 6. Bluesky

Inicia sesión con tu usuario y una **contraseña de aplicación** (créala en bsky.app →
Configuración; para usar los **mensajes directos**, márcala con "Permitir acceso a los mensajes
directos"). La sesión se mantiene sola; si caduca, en **Gestionar cuentas → "Volver a autorizar
(Bluesky)"** la renuevas **sin perder buffers ni datos** (también tras restaurar una copia en otro
ordenador, donde las sesiones cifradas no viajan por seguridad). **Si caduca, se te avisa en el
momento** diciéndote qué cuenta es; hasta que la renueves, esa cuenta deja de sondearse (no trae
nada y no gasta conexión), y en cuanto vuelvas a iniciar sesión todo retoma solo.

- **Mismos atajos que Mastodon** (Ctrl+N, Ctrl+R, Ctrl+Shift+U, Ctrl+B, Ctrl+Shift+V para la
  conversación, Ctrl+Shift+M para paginar). Límite de 300 caracteres.
- **Repostear:** la opción del menú contextual pregunta con los botones **"Repostear"**,
  **"Citar"** y **"Cancelar"**, igual que "Impulsar" en Mastodon (Ctrl+Shift+U cita directamente).
  Si no quieres que pregunte, en *Preferencias → Bluesky → "Modo de repost"* elige **"Repostear
  directamente"**.
- **Bloquear, silenciar y seguir:** tras hacerlo desde el menú, la aplicación comprueba con
  Bluesky el estado real y te lo confirma (*"@cuenta está bloqueada"*). El menú ofrece ya la acción
  contraria (**Desbloquear autor**, **Quitar silencio**, **Dejar de seguir**) y la cuenta aparece en
  los buffers Bloqueados, Silenciados o Siguiendo. Desbloquear funciona aunque la bloquearas desde
  otra aplicación.
- **Adjuntos:** 4 imágenes **o** un vídeo de hasta 50 MB (las imágenes se ajustan solas al
  límite). Los #hashtags, @menciones y enlaces se publican como elementos reales y clicables; los
  enlaces no se llevan la puntuación final y los dominios sueltos (bsky.app/algo) también cuentan.
- **Grupos de chat:** además de los mensajes de dos, Bluesky admite **grupos**: *menú Bluesky →
  "Crear un grupo de chat..."* y *"Entrar en un grupo con un enlace..."*. Desde el menú de una
  conversación de grupo puedes añadir o expulsar cuentas, cambiar el nombre, generar el enlace de
  invitación y aceptar o rechazar a quien pide entrar.
- **Hilos:** el editor divide el texto en publicaciones de 300 y deja elegir el **destino**:
  publicarlo como hilo (con "quién puede responder") o **enviarlo por mensaje directo**, en cuyo
  caso cada trozo se manda como un mensaje seguido de vuestra conversación. Bluesky **no tiene
  publicaciones privadas** —su formato de publicación no incluye ningún campo de audiencia, así
  que todo lo publicado es público y "quién puede responder" solo limita las respuestas, no quién
  lo ve—, de modo que los mensajes directos son la única forma de mandar un hilo en privado.
- **Buffers:** Inicio, Notificaciones, Menciones, Directos, Enviados, Me gusta, Marcadores,
  feeds, listas, tendencias (**Intro abre la tendencia como buffer de búsqueda**), starter packs,
  Seguidores/Siguiendo/Bloqueados/Silenciados (paginados con Ctrl+Shift+M), búsquedas guardadas y
  cronologías de cuentas (desde el menú contextual de una publicación). El **orden es el del
  servidor** (los feeds algorítmicos conservan su ranking). Las **conversaciones abiertas como
  buffer** respetan la preferencia de "más nuevos arriba o abajo" igual que cualquier otro buffer
  (también en W Social).
- **Reposts y respuestas, a tu gusto (Preferencias → Bluesky):** en "Cómo se leen los reposts"
  eliges entre el formato de siempre (*De Ana. [Reposteado por @luis] texto*) o **quien lo comparte
  primero** (*De Luis. Reposteó el mensaje de Ana: texto*, como se lee un impulso en Mastodon). El
  "Reposteado por" sale **solo en el buffer donde de verdad llegó como repost** (antes se colaba
  también en el feed del autor, en hilos y en búsquedas). Para las respuestas, la plantilla de
  lectura tiene el campo nuevo **{reply_to}** ("En respuesta a @usuario", con su propia columna), y
  la casilla **"Decir a quién responde al principio del texto"** lo antepone sin tocar la
  plantilla. Si la original es tuya se lee **"En respuesta a ti"**, y si no se puede saber (por
  ejemplo, está borrada) no se inventa nada. La **bandeja de entrada** lo lee igual que el buffer.
- **Al responder (Ctrl+R):** la ventana se titula **"Responder a [nombre]"** y, con Mayúsculas+Tab
  desde el texto, el campo **"Respondiendo a"** te lee la publicación a la que contestas y **quién
  recibirá el aviso**. En Bluesky no hacen falta menciones: el servidor avisa de tu respuesta a los
  autores de las publicaciones de encima en el hilo (el de la que respondes y hasta tres más
  arriba, la regla de la app oficial). Así, si respondes a Óscar que a su vez respondía a Hanky,
  verás que el aviso les llega a los dos. Si prefieres el estilo de Mastodon, marca en
  *Preferencias → Bluesky* **"Al responder, empezar el texto mencionando al autor y a quien él
  menciona"** y el texto empezará con esas @menciones (ocupan caracteres del límite de 300).
- **Ver la conversación (Ctrl+Mayús+V):** el foco cae en **la publicación desde la que la abriste**,
  con su padre justo encima; cada mensaje dice **a quién responde**, y si el mensaje original ya no
  está (borrado o de una cuenta bloqueada) se dice "en respuesta a una publicación no disponible"
  en vez de dejar la respuesta suelta. Una conversación **abierta como buffer** cuya publicación
  inicial se ha borrado ya no sale vacía: se carga desde la publicación con la que la abriste.
- **Silenciar solo una parte:** en el menú de una publicación, además de "Silenciar autor", están
  **"Silenciar solo sus reposts"** y **"Silenciar solo sus citas"**: sigues viendo lo que escribe,
  pero no lo que reenvía o cita. "Quitar silencio al autor" lo deshace.
- **"Le gusta a gente que sigues":** junto al número de me gusta se dice cuántos son de personas
  que sigues y quiénes (hasta cinco nombres), como en la aplicación oficial.
- **Notificaciones:** cada aviso dice **de qué publicación habla** (se muestra su texto en la
  columna de contenido y en el panel de detalles). **Intro** abre la conversación de esa
  publicación, y si el aviso es de cuenta (nuevo seguidor, verificación) abre su perfil; lo mismo
  con **"Ver la publicación"** del menú contextual. En este buffer, **Ctrl+Mayús+V** (ver la
  conversación), **Ctrl+R** (responder) y **Ctrl+Mayús+U** (citar) actúan sobre la publicación de
  la que habla el aviso.
- **Sin duplicados ni pérdidas:** cada publicación se apunta en una **bandeja de salida** antes de
  enviarse; si la red se corta, la app pregunta al servidor si llegó y, si no, queda en **menú
  Bluesky → "Salida pendiente…"** para reintentarla sin riesgo de duplicarla. Ante un límite de
  peticiones (429) se respeta la hora de reintento del servidor y se te dice.
- **Tiempo real (opcional, Preferencias → Bluesky):** el Inicio se actualiza al instante cuando
  publica alguien a quien sigues (vía Jetstream, con reconexión y cursor); la "Monitorización en
  vivo" avisa al momento de menciones y respuestas reales. Los **directos** se sincronizan solos
  (mensajes nuevos, borrados, reacciones y lecturas desde otros dispositivos).
- **Moderación como la app oficial:** se aplican tus **preferencias de la cuenta** (las mismas de
  la app oficial) en Inicio y demás buffers, notificaciones, conversaciones, búsqueda, citas y la
  bandeja unificada:
  - **Etiquetas:** lo marcado "ocultar" no aparece ni se indexa; lo marcado "avisar" se lee como
    *[Oculto por la etiqueta: X…]* y se revela con **"Mostrar de todos modos"** (menú contextual,
    sin mover el foco) o al abrir los detalles. Lo retirado por moderación no se muestra nunca.
  - **Contenido para adultos:** como en la app oficial, viene **desactivado**: lo marcado como
    pornografía, sexual o gráfico se oculta y no se puede mostrar hasta activarlo. Activado, esas
    etiquetas solo ocultan la **multimedia**: el texto se lee y la multimedia sale como
    *[Multimedia oculta: X]*, sin descripción ni sonido, y se pide confirmación antes de abrirla.
  - **Menú Bluesky → "Preferencias de moderación..."**: activar el contenido para adultos y
    elegir mostrar, avisar u ocultar para cada etiqueta, general y de cada servicio de etiquetado.
  - **Servicios de etiquetado:** *Bluesky Moderation* está siempre activo; se indica a cuáles
    estás suscrito, puedes añadir uno por su usuario y ajustar sus etiquetas (con sus nombres y
    su comportamiento propio). Las suscripciones son las mismas que en la app oficial.
  - **Palabras silenciadas** (menú Bluesky → "Palabras silenciadas..."): añadir, editar y borrar;
    dónde silenciar (texto y etiquetas, o solo etiquetas), **"Excepto en las cuentas que sigo"**
    y duración (para siempre, 24 horas, 7 días, 30 días). Se buscan en el texto, las #etiquetas,
    el texto alternativo, la tarjeta del enlace y la cita. En las listas la publicación se lee
    como *[Silenciado por la palabra: X]*; una casilla del diálogo permite quitarla del todo,
    como hace el feed oficial.
  - **Ocultar una publicación para ti:** menú contextual → **"Ocultar esta publicación para mí"**
    (también desaparecen las que la citan). Se recuperan en **menú Bluesky → "Publicaciones
    ocultadas..."** o con "Dejar de ocultar esta publicación".
  - **Menú Bluesky → "Preferencias del feed Siguiendo y de los hilos..."**: mostrar o no las
    respuestas, solo las respuestas entre cuentas que sigues, mínimo de "me gusta", reposts y
    citas en Inicio, y el orden de las respuestas en las conversaciones.
  - **"Quitar silencio del hilo"** deshace "Silenciar hilo".
- **Más en el menú Bluesky:** gestionar feeds (fijar/quitar), feeds populares y sugeridos, listas
  y sus miembros, palabras silenciadas, servicios de etiquetas (labelers, con suscripción), crear
  starter pack, borradores y perfil (editarlo **no toca** los campos que la app no muestra).

## 7. W Social

**W Social** (wsocial.eu) es una red europea de micromensajes construida sobre **AT Protocol**, la
misma tecnología abierta que usa Bluesky. Por eso se maneja casi igual que Bluesky, con los mismos
atajos, y comparte con él la forma de escribir (enlaces, menciones y etiquetas reales).

**Añadir tu cuenta:** *Archivo → Gestionar cuentas → Cuenta de W Social*. Escribe tu usuario (por
ejemplo `nombre.wsocial.eu`) y una **contraseña de aplicación** (créala en los ajustes de W en la
web; si quieres usar los mensajes directos, márcala con acceso a mensajes). Antes de escribir la
contraseña puedes pulsar **"Comprobar el servidor"**: la aplicación resuelve tu identidad y te dice
en qué servidor vive tu cuenta. Esto no es un detalle técnico caprichoso: en AT Protocol cada
cuenta puede estar alojada en un servidor distinto, y el cliente **no fija ninguno** —lo descubre a
partir de tu identidad—, así que funciona igual con el servidor de W o con cualquier otro.

**La sesión se mantiene sola.** En AT Protocol la autorización se renueva sola, pero solo cuando
la aplicación hace alguna petición; por eso, si pasas semanas sin entrar en esta red, SocialRSS
hace por su cuenta una petición mínima cada pocas horas cuyo único fin es renovarla. Funciona
aunque tengas los avisos apagados y no entres nunca, así que en condiciones normales **no tendrás
que volver a autorizar la cuenta**. Si aun así caducara (por ejemplo, si revocas la contraseña de
aplicación o pasas meses con el programa cerrado), la aplicación te lo dice y te ofrece **volver a
autorizarla en el momento**; también puedes hacerlo cuando quieras desde *Archivo → Gestionar
cuentas → Volver a autorizar*. Se conservan tus buffers y todos tus datos: solo hay que escribir de
nuevo el usuario y la contraseña de aplicación.

- **Buffers:** Inicio, Notificaciones, Menciones, Enviados, Me gusta, Marcadores, Borradores
  (los que guardas en el servidor), Feeds, Listas, Paquetes de inicio, Tendencias, Seguidores,
  Siguiendo, Bloqueados, Silenciados, Mensajes directos y Solicitudes de mensaje. Además, los que
  crees tú: búsquedas guardadas, cronologías de cuentas y conversaciones abiertas como buffer. Se
  ordenan y se ocultan como los de cualquier red (Ctrl+flechas y *Herramientas → Mostrar u ocultar
  buffers*).
- **Publicar (Ctrl+N):** hasta 300 caracteres, 4 imágenes con descripción **o** un vídeo, aviso de
  contenido, idioma, y control de **quién puede responder** y de si se puede **citar**. Si te pasas
  del límite, se te ofrece convertirlo en un **hilo** (Ctrl+Mayús+N abre el editor de hilos, que
  además puede enviarlo por mensaje directo).
- **Nada se publica dos veces:** cada envío lleva su propia clave; si se corta la conexión, la
  aplicación pregunta al servidor si llegó antes de reintentar. Lo que quede en el aire se ve en
  *menú W Social → Salida pendiente*.
- **Sobre una publicación:** responder, citar, me gusta, republicar, guardar en marcadores, ver la
  conversación (o abrirla como buffer), ver quién le ha dado me gusta, quién la ha republicado y
  quién la ha citado, abrir la cronología del autor **o de un usuario mencionado**, escribir al
  autor, silenciar la conversación, fijarla en tu perfil, borrarla o denunciarla.
- **Notificaciones:** igual que en Bluesky, cada aviso muestra el texto de la publicación a la que
  se refiere e **Intro** abre su conversación (o el perfil, si el aviso es de cuenta).
- **Republicaciones y respuestas:** las mismas opciones que en Bluesky, en *Preferencias → W
  Social*: cómo se lee una republicación, el campo **{reply_to}** de la plantilla y la casilla para
  decir a quién responde al principio del texto. El visor de conversación también empieza en la
  publicación desde la que lo abriste y dice a quién responde cada mensaje.
- **Al responder:** igual que en Bluesky, la ventana se titula "Responder a [nombre]" y el campo
  **"Respondiendo a"** (Mayúsculas+Tab desde el texto) dice a qué publicación contestas y quién
  recibirá el aviso. El servidor de W sigue la misma regla que Bluesky: avisa a los autores de
  encima en el hilo sin necesidad de menciones. La casilla **"Al responder, empezar el texto
  mencionando al autor y a quien él menciona"** de *Preferencias → W Social* viene **marcada**,
  porque W Social siempre había empezado la respuesta con la mención del autor; desmárcala si
  prefieres responder sin menciones.
- **Bloquear, silenciar y seguir:** igual que en Bluesky, tras hacerlo desde el menú de una
  cuenta o desde su perfil, la aplicación comprueba con W Social el estado real y te lo confirma
  (*"@cuenta está bloqueada"*). El menú y los botones del perfil ofrecen ya la acción contraria
  (**Desbloquear**, **Quitar el silencio**, **Dejar de seguir**) y la cuenta aparece en los
  buffers Bloqueados, Silenciados o Siguiendo al volver a entrar en ellos (la lista que tienes
  abierta no se recarga, para no moverte el foco). Desbloquear funciona aunque la bloquearas
  desde otra aplicación.
- **Mensajes directos y grupos:** conversaciones con reacciones y solicitudes; y **grupos** con
  nombre, participantes, enlace de invitación, solicitudes de entrada, expulsión y cierre.
- **Moderación a tu medida:** denunciar publicaciones, cuentas y mensajes; suscribirte a
  **servicios de etiquetado** y decidir qué hacer con cada etiqueta (mostrar, avisar u ocultar);
  **palabras silenciadas** con ámbito y caducidad; contenido para adultos e idiomas. Las
  palabras silenciadas se aplican en las listas igual que en Bluesky (texto, #etiquetas y texto
  alternativo; *[Silenciado por la palabra: X]*), y las suscripciones a servicios de etiquetado
  son las mismas que en la app oficial.
- **Verificación de edad:** W puede pedirla para ciertos contenidos. Se completa **en el
  navegador**, en la web de W: esta aplicación nunca maneja tus documentos.
- **Tu cuenta:** editar el perfil, estado temporal, cambiar tu nombre de usuario, contraseñas de
  aplicación, estado de la cuenta, datos del servidor y **descargar una copia completa** de todo tu
  contenido.
- **Avisos en tiempo real:** un vigilante propio comprueba en segundo plano si hay notificaciones o
  mensajes nuevos. Cada red tiene el suyo, así que esto no interfiere con el tiempo real de las
  demás. Se apaga en *Preferencias → W Social*.

## 8. Lector de RSS

Añade **feeds** y organízalos en carpetas. **Ctrl+Shift+R** actualiza (la actualización
automática tiene su propio intervalo en Preferencias → Servicios → RSS: en minutos, mínimo 1);
sobre un artículo:
**Ctrl+M** leído, **Ctrl+D** destacar, **Ctrl+L** leer más tarde, **Ctrl+Mayús+E** abre el
**lector limpio** (Ctrl+H alterna texto/HTML accesible, Ctrl+Intro lo abre en el navegador, botón
Exportar…). **Intro** abre el enlace en el navegador, salvo que en Preferencias → RSS elijas que
abra el lector limpio o que pregunte. El foco **no se pierde** al volver de leer.

### Encontrar feeds y suscribirse

- **Añadir Feed RSS…** acepta la dirección del feed **o la de cualquier web**. Si la web no
  anuncia su feed, se prueban las rutas habituales (`/feed`, `/rss`, `/rss.xml`, `/atom.xml`…) y,
  si hay varios, eliges cuál de una lista. También entiende direcciones de redes y servicios y
  las convierte en su feed: una comunidad o usuario de **Reddit**, un perfil de **Bluesky**, un
  perfil o una etiqueta de **Mastodon**, una comunidad o usuario de **Lemmy**, un canal o una
  **lista de reproducción de YouTube**, un repositorio de **GitHub** (publicaciones y cambios) y
  un autor de **Medium**.
- **Buscar feeds y podcasts…** (menú RSS, o el botón "Buscar en directorios…" de Añadir Feed):
  escribe un tema o un nombre y se consulta a la vez **Apple Podcasts, fyyd, gPodder, Feedly,
  NewsBlur, Lemmy y Mastodon**. La lista se va llenando a medida que responde cada directorio,
  sin mover el foco; Intro lee la descripción completa y **Suscribirse** lo añade a la carpeta
  que elijas. Si algún directorio no responde, se dice al final.
- **Importar suscripciones de YouTube (Google Takeout)…** (menú RSS): en takeout.google.com
  exporta solo "YouTube y YouTube Music" y elige el ZIP que te dan, **sin descomprimirlo**. Se
  ofrecen tus suscripciones, tus canales y tus listas de reproducción (marcados) y los canales de
  tu historial de reproducciones (sin marcar), con la misma vista previa que el OPML. No necesita
  cookies ni sesión. Entran en la carpeta "YouTube".

### Editar un feed (también su dirección)

Sobre un feed del árbol, menú contextual → **"Editar feed (título, dirección, ajustes)…"**. En una
sola ventana cambias el **título**, la **dirección**, la carpeta, el intervalo propio, cómo se
identifica ante el servidor, las imágenes, el texto completo y la pausa.

- **Comprobar la dirección** (Alt+O) explica qué pasa con ella: el código del servidor, si
  redirige a otra, si solo responde a navegadores, si es un feed y cuántos artículos trae o, si es
  una web, qué feeds tiene. Si encuentra una dirección mejor, **Usar la dirección sugerida**
  (Alt+U) la pone en el campo. El resultado se lee línea a línea con las flechas.
- Al **cambiar la dirección** se olvida el estado de descarga de la antigua (errores, caché) y el
  feed se actualiza al momento. Si esa dirección ya la tienes en otro feed, se avisa y no se
  guarda.
- **Webs que rechazan a la aplicación:** algunas webs tienen un cortafuegos que responde con un
  error 403 o 406 a todo lo que no sea un navegador, aunque el feed sea público. Cuando pasa, la
  aplicación **lo reintenta sola identificándose como Firefox** y, si funciona, lo recuerda para
  ese feed. En "Identificarse ante el servidor como" puedes fijarlo tú: la aplicación (y como
  navegador si la rechazan, lo normal), siempre como navegador o una identificación propia (que
  nunca se cambia sola).

### El artículo completo

Muchos feeds solo traen el **primer párrafo** de la noticia. Por eso el lector limpio
(**Ctrl+Mayús+E**), al abrir un artículo, **descarga de la web la noticia entera** y la pone en su
sitio en cuanto llega, sin cerrarse y sin mover el cursor. Mientras tanto verás la línea
"[Descargando el artículo completo de la web...]" y, al terminar, oirás "Artículo completo
cargado". Se hace **una vez por artículo** (queda guardado) y, si la web no da más texto que el feed,
se deja lo que había sin molestarte. El botón **"Descargar el artículo completo"** (Alt+A) lo
vuelve a pedir cuando quieras.

**La vista previa también sale entera.** Si tienes visible el panel de vista previa (se llega con
Tab desde la lista, en RSS y en la bandeja de entrada), al quedarte un momento en un artículo cuyo
feed solo trae un resumen se descarga igualmente el artículo completo y el panel se actualiza solo,
sin que el lector de pantalla relea nada. Mientras llega verás
"[Descargando el artículo completo de la web...]". Al pasar deprisa por la lista no se descarga
nada. Se desactiva en *Preferencias → RSS → "En la vista previa, descargar el artículo completo
cuando el feed solo trae un resumen"*.

**Qué hace Intro sobre un artículo:** en *Preferencias → RSS → "Al pulsar Intro sobre un
artículo"* eliges entre **abrir el enlace en el navegador** (lo de siempre), **abrir el lector
limpio** o **preguntar cada vez** (con botones "Abrir el lector limpio" y "Abrir en el
navegador"). Vale en los buffers de RSS y en la bandeja de entrada. El lector limpio se abre
también siempre con Ctrl+Mayús+E.

**Moverse por el artículo:** el texto del lector limpio conserva la estructura de la noticia y
se recorre con letras, como en una web con el lector de pantalla (con **Mayúsculas**, hacia
atrás): **H** encabezados (y **1** a **6** por nivel; el título es el de nivel 1), **T** tablas,
**L** listas, **I** elementos de lista, **Q** citas, **K** enlaces, **G** imágenes y **S**
separadores. **Z** sale al final de la tabla, lista o cita en la que estás (Mayús+Z, a su
principio). Cada tabla empieza con una línea "Tabla de N filas y M columnas", sigue una línea por
fila con las celdas separadas por tabuladores y termina con "Fin de la tabla."; **Intro** sobre
ella la abre en una **lista** (filas con arriba y abajo, columnas con izquierda y derecha, cada
celda leída con su columna) con un botón **"Ver como HTML"** para recorrerla con los comandos de
tabla del lector de pantalla. Intro sobre un enlace lo abre en el navegador. Las citas van entre
"Cita:" y "Fin de la cita.", y las imágenes con texto alternativo se leen como "[Imagen: ...]".
**Ctrl+F** busca dentro del artículo, y **F3** o **Ctrl+G** (con Mayúsculas, hacia atrás) repiten
la búsqueda.

**Foros, YouTube, noticias en varias páginas y podcasts:**

- Si el artículo es un **hilo de Lemmy o Discourse**, el artículo completo trae **todos los
  comentarios**, cada uno con su autor, sus puntos y a quién responde. Cada comentario es un
  encabezado (**3** para los de primer nivel, **4**, **5** y **6** para sus respuestas), así que se
  salta de uno a otro con esas teclas. En **Reddit**, que ya no deja leer hilos sin iniciar
  sesión, se usa tu cuenta de Reddit de la aplicación si la tienes y, si no, **tu sesión de
  Reddit iniciada en Firefox** (el navegador de *Preferencias → RSS → "Artículo completo y
  suscripciones"*): solo se leen las cookies de reddit.com, no se guardan y solo se envían a
  Reddit. Se desactiva con la casilla "Leer los hilos de Reddit con mi sesión de Reddit del
  navegador".
- Si es un **vídeo de YouTube**: descripción, **capítulos** con su minuto y la **transcripción**
  completa (subtítulos del autor o, si no hay, los automáticos), repartida por capítulos.
- Las noticias **partidas en varias páginas** se unen solas (hasta 6 páginas), sin repetir los
  párrafos que salen en todas.
- En los **podcasts** cuyo feed no trae notas, se leen las **notas y los capítulos que lleva el
  propio MP3** (solo se descarga el principio del archivo, no el audio).

Lo de foros y YouTube se desactiva en *Preferencias → RSS → "Artículo completo y suscripciones"*.

La extracción imita a la **vista de lectura de Firefox**: se queda con el cuerpo de la noticia y
descarta menús, publicidad y listas de "noticias relacionadas". En **Preferencias → RSS →
"Artículo completo y suscripciones"** eliges el **extractor**:

- **Automático (recomendado):** compara todos y se queda con el mejor.
- **Trafilatura:** el más completo en las pruebas públicas; conserva el subtítulo y los primeros
  párrafos que otros a veces pierden.
- **Readability:** el algoritmo clásico, parecido al de la vista de lectura de Firefox.
- **Datos estructurados (JSON-LD):** el texto que el propio periódico publica para los
  buscadores; útil cuando la página visible viene recortada.

Si el elegido no saca nada en una página concreta, se prueban los demás antes de rendirse. Ahí
mismo puedes desactivar la descarga al abrir.

**Medios de pago en los que estás suscrito:** si pagas un periódico, escribe su dirección en esa
misma sección (uno por línea, por ejemplo `elpais.com`) y elige el **navegador** en el que tienes
la sesión iniciada. Sus artículos se descargarán **con tu sesión**, igual que en el navegador, así
que el lector recibe la noticia completa que ya pagas, sin tener que pelearte con su web. Las
cookies se leen **solo** al descargar un artículo de ese medio, **no se guardan** en ningún sitio
y **solo se envían a ese medio**. El botón **"Probar la sesión en estos medios"** te dice si la
encuentra. Usa **Firefox**: Chrome, Edge, Brave y Opera cifran sus cookies desde 2024 y
normalmente no se pueden leer desde otra aplicación. Esto **no salta ningún muro de pago**: sin
una suscripción tuya, el periódico entrega lo mismo que a cualquiera.

El sondeo automático es **inteligente**: cada feed respeta su **intervalo propio** ("Editar feed…"
en el menú del feed), los que llevan días sin publicar se consultan menos (vuelven al ritmo normal
en cuanto publican) y los que fallan esperan cada vez más antes de reintentarse (un feed
retirado por el servidor se pausa solo y te avisa). Ctrl+Shift+R fuerza todos igualmente.
Las carpetas, alertas, búsquedas guardadas, historial y estadísticas son **de cada cuenta**
RSS (no se comparten). Las **búsquedas guardadas** y las **alertas** se pueden editar desde su
propio menú, y las alertas se pueden **desactivar** sin borrarlas. En *Preferencias → RSS* eliges
también si abrir un artículo lo marca como leído y el **espacio máximo** para los podcasts
descargados (0 = sin límite). Las descargas de podcasts que quedaron a medias al cerrar se
**retoman solas** al volver a abrir. Por privacidad, el visor **no carga imágenes remotas** (se leen por su
texto alternativo); si las quieres, actívalas en Preferencias → Servicios → RSS.

- **Siguiente/anterior no leído:** **Ctrl+K** y **Ctrl+Mayús+K** saltan entre los artículos
  sin leer de la lista, sin tocar nada más.
- **Carpetas inteligentes** (menú RSS): carpetas que se llenan solas según tus criterios
  (no leídos, destacados, con audio, de un feed o carpeta, antigüedad, texto…). Se crean con
  controles normales y viven en el árbol como un buffer más.
- **Salud de los feeds** (menú RSS): estado real de cada feed (último éxito, error, fallos,
  próximo intento) con acciones: actualizar ya, pausar/reactivar y "Reintentar todos los rotos".
- **Reglas avanzadas** (en Reglas y Filtros): varias condiciones con Y/O, varias acciones,
  ámbito por feed o carpeta, prioridad y "detener"; el botón **"Probar reglas"** simula contra
  tus artículos guardados sin aplicar nada.
- **OPML con vista previa** al importar (eliges qué entra; los duplicados se detectan por URL
  normalizada) y **exportación de artículos por lotes** (vista actual, destacados o leer más
  tarde, a texto/HTML/Markdown). Se admiten también feeds **JSON Feed**.
- **Etiquetas** (menú RSS → Etiquetas, o el menú contextual de un feed/artículo): además de su
  carpeta, un feed o artículo puede llevar varias etiquetas; cada una es un buffer del árbol y
  las reglas pueden etiquetar solas.
- **Resumen de artículos sin leer** (menú RSS): un digest de tus pendientes por temas, con IA
  si la tienes configurada.

### Organizar: reglas, carpetas inteligentes, búsquedas, borrar y avisos

- **Reglas** (menú RSS → "Reglas y Filtros RSS"): cada condición elige **campo** (título,
  contenido, autor, dirección del artículo o nombre del feed), **operador** ("contiene", "no
  contiene", "es igual a", "empieza por" o expresión regular) y texto. Se combinan con **Y**, con
  **O** o con una **combinación personalizada** escrita con los números de las condiciones, por
  ejemplo `(1 y 2) o no 3`. Acciones nuevas: **mover a la papelera** y **no avisar de su
  llegada**. Las reglas se pueden **editar** (Intro sobre la regla), **activar o desactivar** y
  **aplicar a los artículos que ya tienes** (sin sonidos ni avisos por voz). La regla sencilla de
  arriba ya muestra las acciones con su nombre.
- **Carpetas inteligentes**: se pueden **editar**, y admiten "que no contengan", autor, dirección
  y "solo los corregidos en origen".
- **Búsquedas guardadas en el árbol**: cada búsqueda guardada aparece como un buffer más
  ("Búsqueda: nombre") que se actualiza solo con cada sondeo. Su menú contextual permite
  editarla o borrarla. Se puede desactivar en *Preferencias → RSS*.
- **Suprimir** sobre un artículo lo lleva a la **papelera**, sin perder tu sitio en la lista. En
  **Editar feed** puedes elegir que en ese feed Suprimir **borre definitivamente**: entonces pide
  confirmación (Mayúsculas+Suprimir borra sin preguntar) y el artículo **no vuelve a aparecer**
  aunque el feed lo siga publicando. Vaciar la papelera también borra de forma que no vuelvan.
- **Avisos:** en Editar feed, **"No avisar de sus artículos nuevos"** quita el sonido y la
  notificación de ese feed (los artículos siguen llegando). Las alertas por palabra clave avisan
  como mucho de **3 artículos por actualización** y resumen el resto (se cambia en Preferencias
  → RSS). **Pulsar la notificación** de artículos nuevos abre el artículo si es uno, o lleva al
  primero de los nuevos si son varios.
- **Versiones anteriores:** cuando un medio corrige una noticia, se guarda la versión anterior.
  En el menú del artículo, **"Versiones anteriores del artículo"** muestra qué frases se quitaron
  y cuáles se añadieron.

### Podcasts

Al **descargar un episodio** entra en la **cola de descargas** (menú RSS → "Descargas de
podcast…"): puedes **pausar, reanudar** (continúa donde iba) y **cancelar**, ver el progreso y
saber cuáles ya escuchaste, con un límite de espacio para la carpeta. Los episodios muestran su
**duración, temporada/episodio y si son explícitos**, y cuando el podcast lo ofrece, enlaces a
**transcripción, capítulos y apoyo**. Un artículo puede tener **varios adjuntos** y una
**galería de imágenes** accesible (menú del artículo → "Imágenes del artículo…").

**Escuchar podcasts (reproductor de radio, podcast y YouTube):**

- **Continúa donde lo dejaste**, y ahora es la misma posición tanto si lo escuchas en línea como
  si lo descargaste. Al reanudar **retrocede 5 segundos** para recuperar el hilo, y un episodio se
  da por **terminado a menos de un minuto del final** (así no se retoma para oír solo la
  despedida). Al terminar, su artículo se **marca como leído**. Todo se ajusta en
  *Preferencias → Reproductores*.
- **Velocidad** de 0,5x a **3x** en pasos de 0,1 (en la ventana del reproductor o con F7 y F8).
- **Capítulos:** si el episodio los trae (los de Podcast 2.0 del feed, los que lleva dentro el
  MP3 o los de un vídeo de YouTube), la ventana del reproductor dice en qué capítulo estás,
  **Av Pág** y **Re Pág** saltan al siguiente y al anterior, y el botón **Capítulos** los lista.
- **Cola de podcasts:** en el menú de un episodio, **"Añadir a la cola de podcasts"**. Al terminar
  uno suena el siguiente, y la cola se conserva al cerrar la aplicación. Se gestiona con el botón
  **Cola** del reproductor (Ctrl+Av Pág pasa al siguiente).
- **Ecualizador** de 10 bandas con preamplificador y preajustes ("Voz", "Más graves",
  "Menos graves"...): se oye al momento y se guarda al aceptar.
- **Saltar silencios:** casilla del reproductor. Cuando hay un silencio, se pasa rápido por él
  hasta que vuelve la voz, sin perder nada de lo que se dice.
- **Episodios antiguos:** muchos podcasts solo dejan en su feed los últimos episodios. En el menú
  contextual del feed, **"Recuperar episodios antiguos (archivo de Internet)"** busca copias
  antiguas del feed en la Wayback Machine y añade los episodios que faltan, como leídos y sin
  avisos.

### Sincronización con un servidor

**Menú RSS → "Sincronización con servidor…"**: conecta la cuenta con **FreshRSS, Inoreader,
BazQux, The Old Reader** (protocolo Google Reader) o **Miniflux** para compartir tus feeds y lo
leído/destacado entre dispositivos. Tus credenciales se guardan **cifradas**; el servidor aporta
las suscripciones y el estado, y los artículos los sigue descargando la app. Se sincroniza sola
con su propio intervalo, también nada más abrir la aplicación (y hay un botón "Sincronizar
ahora").

La sincronización del estado va en **los dos sentidos**: lo que marcas como leído o destacado en
la web (o en otra aplicación conectada al mismo servidor) llega a SocialRSS, y lo que marcas aquí
se envía al servidor; también viaja el "marcar como no leído". Si algo cambió a la vez en los dos
sitios, manda el servidor, que es el estado que comparten todos tus dispositivos. Ten en cuenta
que el estado viaja **en cada sincronización**, no al instante: si quieres que sea más ágil, baja
el intervalo (mínimo 5 minutos) o pulsa "Sincronizar ahora", que además te dice cuántos cambios
se han aplicado aquí y cuántos se han enviado al servidor. Se comparan los artículos **recientes**
(los últimos 2000 del servidor), que es donde ocurren los cambios; los muy antiguos no se tocan.

**Dirección del servidor:** en The Old Reader es `https://theoldreader.com` (si pegas la dirección
con `/reader/api/0` al final, se corrige sola); en FreshRSS es la de su API, que suele terminar en
`/api/greader.php`; en Miniflux, la URL de tu instancia.

**Las suscripciones también hacia el servidor (opcional):** en la misma ventana, la casilla
**"Llevar también al servidor las altas, bajas, cambios de nombre y de carpeta de los feeds"** hace
que añadir un feed (también desde el buscador, un OPML o Google Takeout), eliminarlo,
renombrarlo, cambiarlo de carpeta o cambiar su dirección se haga también en el servidor. Viene
desactivada: sin ella, las suscripciones solo bajan del servidor, como hasta ahora (y un feed que
borres aquí volverá en la siguiente sincronización si sigue en el servidor). Con **Miniflux**,
el artículo completo se pide primero al propio servidor, que usa sus reglas de extracción.

- **Leer en voz alta (Ctrl+Shift+L):** TTS propio con velocidad y voz, que **recuerda por dónde
  ibas** en cada artículo.
- **Podcasts:** reproduce episodios (velocidad 0,5x–2x sin cambiar el tono, continúa donde lo
  dejaste) y **descárgalos para escucharlos sin conexión** (menú del artículo; gestión en RSS →
  "Descargas de podcast…").
- **Webs sin RSS:** **RSS → "Crear feed desde una web…"** analiza la página y la convierte en un
  feed normal. **Texto completo por feed** en "Editar feed…". **Wallabag** para "leer más
  tarde" en otros dispositivos (RSS → Configurar Wallabag).
- **Historial y estadísticas de lectura** en el menú RSS. Papelera y mantenimiento.
- **Complementos** (RSS → Complementos → Gestionar): Anti-clickbait, **Suscripciones de YouTube**
  (importa tus canales usando las cookies del navegador y cada canal pasa a ser un feed), Feeds
  populares y Web Scraper por sitio (con plantilla para crear los tuyos en
  `data/plugins/web_scraper/scrapers/`).

## 9. Telegram

### Conseguir el api_id y el api_hash (obligatorio, gratis, una sola vez)

1. Entra en **https://my.telegram.org** con tu **número de teléfono** en formato internacional
   (ej. `+34600112233`). El código de confirmación te llega **a la app de Telegram** (no por SMS).
2. Pulsa **"API development tools"** y crea una aplicación: solo necesitas **App title** (por
   ejemplo `SocialRSS`) y **Short name** (`socialrss`); el resto puede quedar vacío.
3. Copia el **api_id** (un número) y el **api_hash** (una cadena larga) en **Preferencias →
   Telegram** y guarda. Son claves personales: no las compartas. Después inicia sesión con tu
   número y el código que te llega.

### Uso diario

- La lista de **chats, canales, grupos y carpetas** se carga sola y los mensajes llegan **en
  tiempo real**. **Intro** abre la conversación: caes en el **primer mensaje sin leer** (marcados
  "(nuevo)"), se marcan leídos al pasar por ellos y al salir se guarda hasta dónde llegaste. En
  chats de solo lectura se ocultan los controles de envío.
- **Por dónde ibas leyendo:** se guarda el **mensaje exacto** por el que ibas en cada chat, así que
  al volver a entrar caes justo ahí, sin retroceder. Además, antes del primer mensaje sin leer se
  inserta una línea **"--- Mensajes no leídos ---"**, como en Unigram, para verlo de un vistazo; no
  es un mensaje (no tiene acciones) y el foco se coloca en el mensaje, no en la marca. Se puede
  quitar en *Preferencias > Telegram*.
- **En la conversación:** **Ctrl+Intro** envía; **Espacio** reproduce/pausa audio, voz o vídeo;
  **Ctrl+C** copia el texto; **Intro** abre el enlace del mensaje (si hay varios, eliges). El
  menú del mensaje (**Aplicaciones**) es **dinámico según tipo y permisos**: responder, editar,
  borrar, reaccionar (y ver quién reaccionó), fijar, traducir, describir con IA, reenviar,
  responder en privado, votar encuestas, editar pie, reemplazar archivo, listas de tareas, Mis
  GIF… **"Ver mensaje…"** muestra detalles, **todos los adjuntos** (descarga múltiple con
  porcentaje) y las respuestas.
- **Enviar:** archivos con comentario individual y **subida con porcentaje en vivo**, álbumes,
  **notas de voz** (grabar, cancelar; opción de autoreproducción **en cadena** de las que
  llegan), mensajes **programados** y **streaming sin descargar** de audio/vídeo. **@mención**
  con Ctrl+Espacio; **temas de foro** navegables por tema.
- **Buscar en el chat (botón "Buscar…", Alt+B):** los resultados dicen **qué adjunto es y cómo se
  llama** junto a su texto o pie (*[Documento: acta.pdf] El acta de ayer*), no solo "documento".
  **"Ir al mensaje en la conversación"** te lleva al mensaje exacto y **la búsqueda se recuerda**:
  al pulsar otra vez Buscar vuelve tal como la dejaste (texto, fechas, resultados y el resultado
  en el que estabas), sin repetirla. Se olvida al **cerrarla** con Cerrar o Escape, o al salir del
  chat.
- **"Más acciones…"** dentro del chat: multimedia y archivos por tipo, ir a una fecha, enviar
  como, comandos e inline de **bots** (sus botones se pulsan con Espacio → lista accesible),
  chat de voz (entrar a escuchar y hablar).
- **Menú Telegram:** búsqueda global de mensajes, buscar usuarios/grupos/canales públicos, buscar
  publicaciones de canales por hashtag, chats frecuentes, canales similares, carpetas (crear,
  reordenar, compartir por enlace), contactos (importar CSV/vCard, notas privadas), historias (ver, publicar,
  gestionar las tuyas), privacidad global, ajustes de cuenta (autodestrucción, sesiones, cambiar
  número) y **crear/administrar grupos y canales** (miembros con roles en español, permisos,
  moderación, temas, invitaciones, registro de administración).
- **Preferencias → Telegram:** anunciar por voz los mensajes nuevos (solo privados), sincronizar
  todo el historial al abrir un chat, marcar leído al navegar, autoplay de voz, y las plantillas
  de chats y mensajes (campos como `{reactions}`, `{album}`, `{silenciado}`…).

### Telegram Drive (tu nube personal)

**Telegram Drive** convierte tu cuenta de Telegram en una **nube personal** (al estilo de Google
Drive, OneDrive o MEGA): un explorador de archivos accesible que guarda tus documentos en un
**canal privado** llamado "SocialRSS Telegram Drive". El canal es **privado y solo tuyo** (no
aparece en búsquedas) y es un canal de **difusión**: solo tú publicas en él a través de la app,
así que es **de solo lectura** para cualquier otra persona (nadie más puede enviarle archivos). Se
abre desde **menú Telegram → "Telegram Drive (tu nube personal)..."**. La primera vez te ofrece
**crear el canal** o **reconstruir** tu nube desde uno que ya tengas.

- **Explorar y organizar:** crea carpetas y subcarpetas, cambia nombres, mueve, copia/pega, entra
  y sube de nivel, ve **atrás/adelante**, **busca** por nombre y consulta **propiedades**. La
  lista es accesible por columnas (Nombre, Tipo, Tamaño, Modificado, Estado) y las **carpetas van
  siempre antes** que los archivos. Las novedades aparecen sin moverte el foco.
- **Descripción opcional:** al **crear una carpeta** o al **subir archivos** puedes escribir, si
  quieres, una descripción. También puedes cambiarla luego con **"Cambiar la descripción..."** y
  se muestra en las Propiedades. La descripción se guarda en el índice, así que se conserva al
  reconstruir la nube.
- **Marcar varios:** marca elementos con la **barra espaciadora** para operar sobre varios a la
  vez; el estado se ve en la columna **"Marcado"** y el lector lo anuncia. Las operaciones
  (descargar, papelera, cortar...) actúan sobre lo **marcado**; si no hay nada marcado, sobre el
  elemento con foco. **Ctrl+A** marca todo y hay **"Desmarcar todo"**.
- **Columnas personalizables:** la lista tiene columnas Marcado, Nombre, Tipo, Tamaño, Modificado,
  **Descripción** y Estado. Puedes elegir **qué columnas** se muestran, **en qué orden** y **cómo
  las lee** el lector desde **Herramientas → Plantillas → Telegram → "Telegram Drive (nube)"**.
- **Título y barra de estado:** el **título** de la ventana muestra tu **ubicación** actual. La
  **barra de estado** resume ubicación, elementos a la vista, marcados, transferencias en curso y
  el tamaño total de la nube; puedes **leerla con Ctrl+Mayús+B**.
- **Eliminar la nube entera:** menú **Nube → "Eliminar Telegram Drive por completo..."** borra el
  canal privado y **todos** tus archivos, además del índice local. Avisa **dos veces** porque es
  **irreversible** (no hay papelera que valga).
- **Subir y descargar sin bloquear:** puedes **seguir usando SocialRSS** mientras trabajan las
  transferencias. Sube **archivos sueltos** o una **carpeta entera** (conserva su estructura) y
  descarga archivos o carpetas completas. La ventana **Transferencias** muestra el progreso en
  vivo y permite **cancelar, reintentar y quitar**.
- **Archivos grandes:** los que superan el límite de Telegram (2 GB / 4 GB con Premium) se
  **parten solos** en trozos, se suben por partes sin llenar la memoria y se **reconstruyen** al
  descargar, **verificando el contenido** (SHA-256); si algo no cuadra, el archivo dañado se
  descarta y solo se reintenta lo que falte.
- **Sin duplicar y sin pisar:** si subes un contenido que ya está en la nube (aunque tenga otro
  nombre), **no se vuelven a subir sus datos**. Si repites un nombre, se te **pregunta** qué hacer
  (Reemplazar, Conservar ambos, Omitir); **nunca** se sobrescribe en silencio.
- **Papelera:** enviar a la papelera **no borra**; puedes **restaurar** a su sitio, **vaciar** o
  **eliminar definitivamente** (esto sí quita los mensajes del canal). Retención configurable: lo
  que lleve más de X días en la papelera se limpia solo al abrir (30 días por defecto; 0 = nunca).
- **Reanudar subidas grandes:** un archivo grande se sube por trozos; si cierras la app a media
  subida, al reintentar **se reanuda** desde donde iba (solo sube los trozos que faltan), no
  empieza de cero.
- **Revisar y reparar el canal** (menú **Nube → "Revisar y reparar el canal..."**): compara el
  canal con tu índice y, si algo no cuadra, te ofrece **borrar del canal** los documentos
  huérfanos (restos que no le corresponden a ningún archivo) y **quitar del índice** los archivos
  rotos (a los que les falta alguna parte). No borra nada sin preguntarte.
- **Purga avanzada del canal** (menú **Nube → "Listar TODO el canal (purga avanzada)..."**): para
  usuarios avanzados. Lista **todos** los documentos del canal con casillas; marcas los que quieras
  y se **borran directamente del canal, SIN papelera** ni recuperación. Indica cuáles son archivos
  de tu nube, cuáles están sueltos y cuáles son índices json; un botón marca de golpe lo que sobra
  (sueltos e índices viejos), respetando tus archivos y el índice vigente. Úsalo solo si sabes lo
  que haces (sirve para purgar restos: json acumulados, archivos colgados, partes sueltas).
- **Recuperable:** además de la base local, la nube guarda un **índice versionado en el canal**.
  Si reinstalas, borras la base o cambias de equipo, usa **menú Nube → "Reconstruir el índice
  desde el canal..."** para recuperar toda tu nube: si la app perdió el id del canal, lo **localiza
  sola** por su título; y si **no hay ningún canal** que reconstruir, te lo dice con un mensaje
  claro (en vez de dejarte una nube vacía). Una subida a medias **nunca** cuenta como archivo
  válido.
- **Atajos** (todos con su entrada de menú): **Intro** abre carpeta o descarga archivo,
  **Retroceso** sube de nivel, **Alt+Izquierda/Derecha** atrás/adelante, **Ctrl+Mayús+R**
  actualiza, **Ctrl+Mayús+N** nueva carpeta, **Ctrl+U** subir archivos, **Ctrl+Mayús+U** subir
  carpeta, **Ctrl+D** descargar, **Ctrl+F** buscar, **Ctrl+F2** cambiar nombre, **Supr** a la
  papelera, **Mayús+Supr** eliminar definitivamente, **Ctrl+X/C/V/A** cortar/copiar/pegar/todo y
  **Alt+Intro** propiedades.
- **Preferencias → Servicios → Telegram → "Telegram Drive (nube personal)"** (su propia subsección
  en el árbol): carpeta de descargas, transferencias simultáneas, política ante nombres repetidos,
  días de retención de la papelera, confirmar antes de la papelera, limpiar temporales y cada
  cuántos segundos anunciar el progreso.
  El **cifrado** del contenido queda preparado en la arquitectura pero **desactivado a propósito**
  hasta poder ofrecerlo de forma segura.

## 10. X (Twitter) — no oficial, experimental

Cliente **no oficial** por cookies del navegador: **X puede limitar o bloquear la cuenta**; úsalo
con prudencia (se puede desactivar por completo). Importa las cookies desde **Firefox** (Chrome y
derivados las cifran y no se pueden leer; en ese caso pega a mano `auth_token` y `ct0`). La sesión
queda cifrada y se mantiene.

Buffers: Inicio, Siguiendo, Menciones, Notificaciones, Marcadores, Seguidores (y verificados),
Cuentas que sigo, Listas, Comunidades, Programados, búsquedas, cronologías de usuario (sus
publicaciones, con respuestas, solo multimedia o **sus me gusta**), **conversaciones** y
**comunidades** abiertas como buffer. Publica con texto, imágenes/vídeo, encuestas y programación;
me gusta, retweet, cita, marcador, seguir/bloquear, perfiles y tendencias.

- **Conversaciones (hilos), Ctrl+Mayús+V:** abre el **visor de conversación**: todo en orden de
  lectura (lo que hay **encima** —a qué responde, hasta el tuit original—, la publicación y sus
  respuestas con las de cada rama), con el foco en la publicación desde la que lo abriste. Cada
  mensaje dice **a quién responde** (o que el original ya no está disponible) y, con la tecla
  Aplicaciones o el botón Acciones, puedes responder, citar, dar me gusta, retuitear, guardar,
  votar, ver el perfil o la cronología del autor, **abrir la conversación de otro mensaje** o
  **abrir la conversación como buffer** (queda en el árbol; su menú contextual la actualiza o la
  quita). Un hilo del propio autor (1/7, 2/7…) se lee entero y en orden.
- **Qué se lee de cada publicación:** el **tuit citado** con su autor y su texto, la **encuesta**
  con sus opciones y votos (y cuál votaste) y la descripción de las **imágenes y vídeos**. El
  campo **{reply_to}** de la plantilla dice a quién responde, y la casilla *Preferencias → X →
  "Decir a quién responde al principio del texto"* lo antepone sin tocar la plantilla. La
  **bandeja de entrada** lee las publicaciones de X igual que su buffer, menciones incluidas.
- **Encuestas:** *"Votar en la encuesta..."* en el menú de la publicación (mientras está abierta).
- **Menú X:** *Buscar cuentas...* (perfil, cronología, seguir, añadir a una lista, silenciar o
  bloquear), *Buscar comunidades...* (unirte, salir o abrir sus publicaciones como buffer),
  *Tendencias de un país o ciudad...* y *Gestionar listas...* (crear, renombrar, borrar, ver y
  quitar miembros, añadir miembros). En el menú de una publicación, *"Añadir al autor a una
  lista..."*.
- Sobre cualquier publicación puedes además **ver la cronología, ver el perfil o seguir a un
  usuario mencionado**: te ofrece las cuentas que aparecen en ella (autor, mencionados, citado,
  quien la retuiteó) y, si solo hay una, actúa sin preguntar.
- **Mensajes directos** en ventana tipo Telegram. OJO: X ha pasado sus mensajes directos a
  **XChat** (cifrados); las conversaciones que ya estén en XChat no se pueden leer con este
  cliente todavía (ninguna librería por cookies lo permite aún).
- **Ctrl+Shift+M** carga más antiguos. La actualización es **manual por defecto**
  (F5/Ctrl+Shift+R); la automática existe en Preferencias → X pero aumenta el riesgo de bloqueo
  (intervalo mínimo 2 minutos). Si X rechaza una petición de forma pasajera, se repite sola sin
  darte por caducada la sesión.
- Por dentro combina **dos motores** con respaldo mutuo (uno publica y actúa; otro, de solo
  lectura, sirve de reserva si X rompe algo del primero): por eso, si alguna lista llega por el
  de reserva, tu "me gusta" puede aparecer en blanco hasta que interactúas.

## 11. IRC

**Gestionar cuentas → Red IRC:** servidor (por defecto 6697 con TLS), apodo con alternativos,
canales de autoconexión, **SASL** o NickServ (contraseñas cifradas). Varias redes a la vez, con
conexión automática opcional y **reconexión sola** si se cae.

En el árbol: **Canales**, **Privados** y **Avisos del servidor**. La conversación funciona como la
de Telegram (los mensajes entran sin mover tu posición): **Intro** envía, flechas recuperan tus
envíos, **Tab** completa apodos. Botones: Usuarios (con rango en palabras; operadores pueden dar
voz/op, expulsar, banear), Tema y Canal (modos con casillas, baneos). **Comandos** con barra
(`/me`, `/join`, `/msg`, `/whois`, `/away`… `/help` los lista; `//` para texto literal). Cada
canal tiene **nivel de aviso** (todo / solo menciones / silencio) y puede **ocultar entradas y
salidas**. Más: canales del servidor, whois/whowas, ignorar usuarios, amigos (avisos de
conexión), CTCP, invitaciones. El historial entra en la búsqueda global y funciona con
**bouncers** (ZNC…).

## 12. Matrix

**Gestionar cuentas → Cuenta de Matrix:** servidor (ej. `matrix.org`), usuario y contraseña (no se
guarda: se guarda el token cifrado). También puedes **registrar cuenta nueva** (incluso con token
de registro). En el árbol: **Salas**, **Directos** e **Invitaciones**; sincronización en tiempo
real.

La conversación es como la de Telegram/IRC: responder (enlazado real), **editar**, reaccionar,
borrar, hilos, encuestas compatibles con Element, ubicaciones, menciones reales, "¿quién ha leído
este mensaje?", adjuntos (**Espacio** reproduce audios y descarga el resto), notas de voz y
"Cargar anteriores". Menú Matrix: unirse/crear salas y **espacios**, directorio (también de otros
servidores), mensaje directo, mi perfil (avatar, presencia, zona horaria), mis dispositivos y
palabras que avisan. **Moderación** desde la lista de miembros (rango en palabras, expulsar,
banear, invitar), etiquetas de sala, silenciar en el servidor, exportar.

**Cifrado de extremo a extremo:** funciona solo; para el historial antiguo **importa las claves**
desde Element (Matrix → Importar claves) y los mensajes pendientes **se descifran solos** al
llegar la clave. **Verificación por emojis** en ambos sentidos y **verificación cruzada** para que
tus dispositivos te reconozcan. Administración de servidor Synapse opcional (usuarios, salas,
reportes, tokens, estadísticas) en Preferencias → Matrix.

## 13. XMPP (Jabber)

**Gestionar cuentas → Cuenta de XMPP:** tu **JID** (`usuario@servidor`) y contraseña; el servidor
se descubre solo y la conexión siempre va cifrada. En el árbol: **Contactos** (con presencia en
palabras), **Conversaciones** (solo chats activos), **Grupos**, **Archivados**, **Solicitudes** y
**Avisos**.

- **Conversación:** como las demás (entra solo, "(nuevo)", Intro envía, "escribiendo…",
  entregado/leído, sincronía entre dispositivos). **Historial del servidor (MAM)** con "Cargar
  anteriores" (Ctrl+Mayús+M). Responder citando, reaccionar, **corregir** tu último mensaje,
  retirar, reenviar, destacar, traducir (Ctrl+T), describir imagen (Ctrl+I).
- **Adjuntos y voz:** "Adjuntar archivo…" (HTTP Upload); **Espacio** reproduce los audios y
  descarga el resto; **notas de voz** con duración y estado ("pendiente"/"reproducido"), estilo
  WhatsApp.
- **Grupos (MUC):** unirse por dirección o directorio, marcadores con autoentrada, ocupantes con
  rol en palabras (moderación si te corresponde), menciones con sonido propio.
- **Cifrado OMEMO:** botón "Activar cifrado" por chat; confianza automática al estilo
  Conversations, con **huellas** verificables. Nunca envía en claro si no puede cifrar.
- **Gestión estilo WhatsApp:** fijar, silenciar con temporizador, archivar, leído/no leído,
  búsqueda rápida de chats (Ctrl+Mayús+B).
- **Pasarelas (WhatsApp, Discord…):** si tu servidor tiene puentes, regístrate en **XMPP →
  "Pasarelas del servidor…"** y vincula por **QR** (se muestra en grande) o por número/código.
  Cada puente cuelga como **su propia rama** (contactos, grupos, solicitudes) sin mezclarse con tu
  servidor; se da de baja desde el menú de su rama.
- **Extras:** ficha vCard (la tuya y la de otros), información técnica, ánimo y actividad,
  zumbido, invisible, buscar usuarios, y administración del servidor con **comandos ad-hoc** en
  formularios accesibles (crear cuenta, cambiar contraseña, borrar cuenta…).

**XMPP, Matrix e IRC recuerdan** si dejaste cada cuenta conectada o desconectada al cerrar.

## 14. Reddit

**Conseguir tu identificador de aplicación (client id) — una sola vez, gratis.** Reddit exige que
cada programa se identifique, igual que Telegram con el api_id. Abre
`https://www.reddit.com/prefs/apps` (hay un botón en **Preferencias → Reddit**), pulsa
"create another app…", elige el tipo **installed app**, en "redirect uri" escribe EXACTAMENTE
`http://localhost:8080`, guarda y copia el identificador que aparece bajo el nombre de la app.
Pégalo en **Preferencias → Reddit**. Cada usuario usa el suyo (así tienes tu propio límite de uso).

**Añadir la cuenta:** Gestionar cuentas → **Cuenta de Reddit**. Se abre el navegador para que
inicies sesión y autorices el acceso; **tu contraseña nunca pasa por la aplicación**. En el árbol
tendrás: Página principal, Populares, r/all, Bandeja de entrada, Sin leer, Menciones, Enviados,
Mis publicaciones, Mis comentarios, Guardados, Votados positivos/negativos, Ocultos, Mis
subreddits, Multireddits y tus búsquedas guardadas.

- **Leer:** cada buffer usa tus plantillas y columnas; las novedades entran en silencio.
  **Ctrl+Mayús+R** actualiza y **Ctrl+Mayús+M** carga elementos más antiguos. **Intro** sobre una
  publicación abre el **visor de comentarios** (hilo anidado, con "cargar más comentarios",
  traducir y resumir con IA).
- **Acciones** sobre el elemento enfocado (menú contextual, botonera o teclas): voto
  positivo/negativo/quitar (el "me gusta" unificado da voto positivo), **guardar**, **ocultar**,
  **responder** (Ctrl+R), **editar** y **borrar** lo tuyo, marcar **+18/spoiler**, poner
  **etiqueta (flair)**, abrir en el navegador y copiar enlace.
- **Publicar (Ctrl+N):** texto, enlace, imagen, vídeo, galería o encuesta, con +18/spoiler, aviso
  de respuestas y flair. **Crosspost** a otra comunidad desde el menú de la publicación.
- **Comunidades y gente (menú Reddit):** Ir a un subreddit, Buscar publicaciones, Buscar
  subreddits, Buscar usuarios y Enviar mensaje privado. Desde "Mis subreddits" puedes abrir,
  fijar en el árbol o cancelar la suscripción; en un perfil verás su karma y descripción.
- **Moderación (menú Reddit → Moderación):** si moderas alguna comunidad, tienes herramientas
  completas. Primero se te pregunta qué subreddit (de los que moderas); si solo moderas uno, se elige
  solo.
  - **Cola de moderación:** revisa los elementos pendientes (cola de moderación, reportados, spam,
    sin moderar y editados). Sobre cada uno, con el menú contextual o Intro: **aprobar**, **eliminar**
    (normal o como spam), **bloquear/desbloquear** los comentarios, **distinguir** (con placa de
    moderador, y en publicaciones fijar arriba) o quitar la distinción, **ignorar los reportes** y
    **enviar un mensaje de eliminación** al autor.
  - **Registro de moderación:** las últimas acciones de moderación de la comunidad.
  - **Usuarios:** gestiona **baneados** (con motivo, mensaje al usuario, duración y nota interna),
    **silenciados**, **contribuidores aprobados** y **moderadores** (invitar con permisos concretos,
    cambiar sus permisos o quitarlos). Añadir/Quitar con los botones del diálogo.
  - **Motivos de eliminación:** crea y borra los motivos predefinidos de la comunidad.
  - **Configuración del subreddit:** edita el título, la descripción pública, la barra lateral, el
    texto al publicar, el tipo de acceso (público/restringido/privado) y si es +18; el resto de
    ajustes se muestran como solo lectura.
  - También puedes moderar directamente desde la lista: en el menú contextual de una publicación o
    comentario hay un submenú **Moderar** con estas acciones. Si no eres moderador de esa comunidad,
    Reddit lo rechaza y se te avisa.
- **Personalización (menú Reddit → Personalización):** herramientas para las comunidades. Si estás
  en el nodo de un subreddit se usa ese; si no, se te pregunta el nombre. Leer funciona en cualquier
  comunidad; crear o editar suele exigir ser moderador (si no, se te avisa).
  - **Wiki:** lista de páginas; al elegir una ves su contenido y su última edición. Puedes
    **editarla y guardarla** (con motivo opcional), **crear una página nueva** y ver el **historial
    de revisiones**. Las páginas de solo lectura se indican como tales.
  - **Etiquetas (flair):** cambia entre **etiquetas de usuario** y **de publicación**. Puedes crear
    plantillas nuevas (texto, solo para moderadores, editable por el usuario) y borrarlas; y para las
    de usuario, **establecer tu propia etiqueta** o **asignar una etiqueta a otro usuario** (texto
    vacío para quitarla).
  - **Emojis:** lista los emojis personalizados de la comunidad; puedes **añadir** uno (nombre +
    imagen) o **borrarlo**.
  - **Barra lateral (widgets):** muestra en solo lectura los elementos de la barra lateral (nombre,
    tipo y, al seleccionar uno, su texto).
- **Funciones avanzadas:**
  - **Multireddits navegables:** en «Multireddits» pulsa Intro sobre uno y se abre como un feed más
    (se lee y se actúa igual que cualquier lista de publicaciones), en vez de abrirse en el navegador.
  - **Borradores (menú Reddit → Borradores):** tus borradores de publicación; puedes crear uno nuevo
    (texto o enlace, con su comunidad), publicarlo o borrarlo.
  - **Hilos en directo (menú Reddit → Hilos en directo):** crea un hilo en directo o abre uno por su
    identificador; ves sus novedades y puedes publicar novedades nuevas.
  - **Modmail (menú Reddit → Moderación → Modmail):** la mensajería de moderación de tus comunidades.
    Cambia entre bandejas (nuevas, en curso, archivadas, apelaciones...), abre una conversación para
    leerla y responder (con opción de nota interna solo para moderadores), archívala o crea una nueva.
  - **Colecciones (menú Reddit → Moderación → Colecciones):** lista, crea, consulta y borra las
    colecciones de publicaciones de un subreddit.
- **IA y traducción:** igual que en el resto (Ctrl+T traducir, resumir con IA, describir imágenes).
- **Privacidad:** el contenido borrado o eliminado en Reddit se purga también de tu copia local,
  como exige su política de datos.
- **Nota:** Reddit limita a 100 peticiones por minuto por identificador de aplicación; por eso el
  intervalo de actualización es holgado (ajustable en Preferencias → Reddit).

## 15. PeerTube

**PeerTube** es una red de **vídeo federada**: no hay un servidor único, sino muchas **instancias**.
Como es un cliente de audio para lectores de pantalla, de un vídeo **suena solo el audio** (lo
habitual y deseado aquí).

- **Añadir cuenta:** Gestionar cuentas → **Cuenta de PeerTube**. Escribe la **dirección de la
  instancia** (por ejemplo, `framatube.org`), tu **usuario** y tu **contraseña**. No hay que
  registrar ninguna aplicación: tu contraseña solo se envía a tu instancia para obtener un
  identificador de sesión, que se guarda **cifrado**. Si tu cuenta tiene **verificación en dos
  pasos**, se te pide el código después. Puedes tener **varias cuentas en varias instancias**.
- **Navegación (árbol):** Vídeos recientes, Populares, Tendencias, Locales, Federados,
  Suscripciones, Directos y Notificaciones. La rama **Mi biblioteca** agrupa lo que posees o has
  marcado: **Mis vídeos**, **Mis canales**, **Listas de reproducción**, **Ver más tarde**, **Me
  gusta** e **Historial**. La rama **Búsquedas guardadas** contiene cada búsqueda que hayas
  guardado. **Intro** sobre un vídeo lo **reproduce** (control de transporte con las teclas globales
  del reproductor, como el resto de audio de la app). **Listas de reproducción** y **Mis canales**
  abren su ventana de gestión al pulsar **Intro** (no al pasar por encima, para no interrumpir la
  navegación por el árbol).
- **Búsquedas guardadas:** al buscar vídeos puedes guardar la búsqueda y queda como un buffer bajo la
  rama **Búsquedas guardadas**. Para quitarla, sitúate en ella y abre su **menú contextual** (tecla
  Aplicaciones): **Actualizar resultados** o **Eliminar esta búsqueda guardada**.
- **Ocultar secciones:** en **Herramientas → Mostrar u ocultar buffers** elige la red **PeerTube** y
  desmarca lo que no quieras ver: una sección suelta, una sub-sección de "Mi biblioteca", o una rama
  entera ("Mi biblioteca" o "Búsquedas guardadas").
- **Menú contextual de un vídeo:** reproducir, ver subtítulos (como texto), me gusta / no me gusta,
  ver o publicar comentarios, suscribirse al canal, calidad de reproducción, abrir en el navegador,
  añadir a "ver más tarde" o a una lista, descargar, estadísticas, silenciar la cuenta o la
  instancia, y reportar. Si el vídeo es **tuyo**: editar o borrar. Si eres **moderador**: bloquear o
  desbloquear el vídeo.
- **Comentarios:** un visor de árbol accesible que anuncia el **nivel** de cada respuesta; puedes
  comentar, responder y borrar.
- **Subtítulos:** el menú "Ver subtítulos" descarga y muestra el texto del idioma que elijas.
- **Menú PeerTube:** buscar vídeos y canales, ir a un canal, cargar más, **subir un vídeo**
  (elige archivo, canal, título, privacidad; la subida es reanudable y avisa del progreso),
  **importar por URL o magnet**, ver **Mis importaciones**, gestionar **listas** y **canales**,
  **crear un directo** (te da la URL RTMP y la **clave de emisión**, que se muestra oculta y solo se
  copia con aviso, para tu programa tipo OBS), ver **Mis denuncias** y las **estadísticas de la
  instancia**.
- **Notificaciones:** la sección "Notificaciones" lista los avisos (nuevo vídeo de una suscripción,
  comentario, seguidor, mención, importación terminada…); puedes marcarlos como leídos y abrir el
  vídeo. Elige de qué recibir avisos en **PeerTube → Preferencias de notificaciones**.
- **Moderación** (si eres moderador): submenú **PeerTube → Moderación**: cola de denuncias
  (aceptar/rechazar con comentario, mensajes) y vídeos bloqueados.
- **Administración** (si eres administrador): submenú **PeerTube → Administración**: usuarios (roles
  y cuotas), solicitudes de registro, federación entre instancias, trabajos del servidor, plugins y
  tokens de *runners*. La configuración completa de la instancia se edita desde su web de
  administración (aquí se muestra un resumen).
- **Reproducción:** funciona mejor con vídeos MP4 progresivos; algunos formatos (WebM, ciertos HLS)
  pueden requerir conversión. La calidad preferida se ajusta en el menú (por defecto, la de menor
  ancho de banda, ya que el audio es el mismo).
- **Preferencias:** en **Herramientas → Preferencias → PeerTube** tienes todos los ajustes, ordenados
  por categorías en subsecciones: **PeerTube** (activar el servicio, intervalo de actualización,
  vídeos por carga), **Reproducción** (calidad e idioma de subtítulos preferidos, continuar donde lo
  dejaste, carpeta de descargas), **Contenido y publicación** (política de contenido +18, privacidad
  por defecto al subir, panel de detalles y botonera de acciones) y **Mantenimiento** (máximo de
  vídeos por sección y no conservarlos entre sesiones).

## 16. Reproductores, radio y podcasts

Dos reproductores independientes: el de **redes sociales** (audio/vídeo de publicaciones y chats)
y el de **radio/podcast/YouTube**. El de redes tiene prioridad: silencia la radio y pausa el
podcast, que se reanudan al terminar. Las teclas **F1 a F8** controlan el que suena **desde
cualquier parte** (retroceder, reproducir/pausar, avanzar, detener, volumen, velocidad; en la
radio, favoritos y silenciar). Esas ocho teclas **se pueden cambiar** en *Preferencias > General >
«Personalizar combinaciones de teclas»*, sección **Reproductores**; como funcionan en toda la
aplicación, solo admiten teclas de función (F1 a F12) o combinaciones con Ctrl, Alt o Mayús.
**Ctrl+Alt+P** invoca el reproductor globalmente. La **radio** usa
el directorio RadioBrowser (con favoritos propios, buscador e historial). Al detener un audio de
redes se guarda la posición y te ofrece continuar la próxima vez.

## 17. IA, traducción y lectura en voz

- **IA:** describir imágenes (**Ctrl+I**), texto alternativo (**Ctrl+Shift+I**), OCR
  (**Ctrl+Shift+O**; también hay **OCR local de Windows sin IA**) y resúmenes. Proveedores:
  Gemini, OpenAI, DeepSeek, xAI, OpenRouter, Groq, Mistral, Claude y Llama local; cada uno admite
  **varias claves** que rotan solas, y un botón lista sus modelos.
- **Traducción:** **Ctrl+T** traduce el elemento (en Mastodon usa primero la traducción de tu
  propio servidor si existe); **Ctrl+Shift+T** el artículo o hilo completo. Motores gratuitos sin
  clave (Preferencias → Traducción). **Redactar traduciendo:** en cualquier campo de escritura,
  **Ctrl+Shift+T** abre un cuadro donde escribes en tu idioma y pega la traducción.
- **TTS:** lector en voz alta con Alt+R / Alt+P / Alt+D y Escape para cerrar.

## 18. Privacidad, seguridad y copias

- Credenciales **cifradas con DPAPI de Windows** (ligadas a tu usuario y equipo). El HTML se
  **sanea** antes de mostrarse y los enlaces con protocolos raros piden confirmación. Proxy
  opcional.
- **Copias de seguridad** (Herramientas): incluyen base de datos, configuración, credenciales y
  los almacenes de cifrado de Telegram, Matrix y XMPP. Restaurar se aplica al **siguiente
  arranque** (guardando antes el estado actual). Para llevarlo a **otro ordenador** usa
  **"Migrar a otro ordenador"** (archivo cifrado con contraseña de 10+ caracteres); si restauras
  una copia normal en otro equipo, las sesiones no viajan: reautoriza cada cuenta (en Bluesky,
  con el botón "Volver a autorizar").
- **Borrado de datos** por red para liberar espacio; en RSS puedes activar el **corte de
  importación** (al limpiar, cada feed solo baja artículos nuevos a partir de ese momento).
- **Ctrl+Shift+F** busca en todo el contenido (por red o global); **filtros de contenido** para
  Mastodon, Bluesky, RSS y X.

## 19. Actualizaciones

Al arrancar (y en **Ayuda → Buscar actualizaciones**) se comprueba si hay versión nueva. La
descarga se verifica con **huella SHA-256 y firma digital Ed25519**: una descarga corrupta o un
servidor comprometido no pueden colarte una actualización falsa. Se instala sin tocar tus datos y
se reinicia.

## 20. Menú Ayuda y consejos para NVDA

**Ayuda:** este manual, las teclas (lista actual, con tus cambios), el último changelog, la prueba
de sonidos, buscar actualizaciones, donaciones y Acerca de.

**NVDA:** usa el **modo enfoque** (NVDA+Espacio) para que las flechas lleguen a listas y árbol;
lee el panel de detalles con las flechas; si se te pierden avisos, sube el "Retardo de los
mensajes de voz" en Preferencias → Accesibilidad; e instala el **complemento de NVDA** para leer
completas las publicaciones largas.

---

*SocialRSS Accessible Suite — la información, sin barreras.*
