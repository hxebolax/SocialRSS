# -*- coding: utf-8 -*-
"""Aplicación GTK 4: menú, acciones globales e instancia única (la gestiona Gio.Application)."""

import logging

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gio, GLib  # noqa: E402

from app.core.config_manager import config
from app.core.speech import notificar
from app.core.version import APP_VERSION
from app.services.rss.rss_service import rss_service

from . import datos, datos_peertube, datos_reddit, datos_x, plataforma_gtk
from .dialogos import DialogoAnadirFeed, DialogoTeclas
from .preferencias import DialogoPreferencias
from .ventana import VentanaPrincipal

log = logging.getLogger(__name__)

APP_ID = "io.github.hxebolax.SocialRSS"


def _menu() -> Gio.Menu:
    def sec(*items):
        m = Gio.Menu()
        for etiqueta, accion in items:
            m.append(etiqueta, accion)
        return m

    barra = Gio.Menu()
    archivo = Gio.Menu()
    archivo.append_section(None, sec((_("Añadir _cuenta de Mastodon…"), "app.anadir-cuenta-mastodon"),
                                     (_("Añadir cuenta de _Bluesky…"), "app.anadir-cuenta-bluesky"),
                                     (_("Añadir cuenta de _W Social…"), "app.anadir-cuenta-wsocial"),
                                     (_("Añadir cuenta de Tele_gram…"), "app.anadir-cuenta-telegram"),
                                     (_("Añadir red de _IRC…"), "app.anadir-red-irc"),
                                     (_("Añadir cuenta de _XMPP…"), "app.anadir-cuenta-xmpp"),
                                     (_("Añadir cuenta de _Matrix…"), "app.anadir-cuenta-matrix"),
                                     (_("Registrar una cuenta nueva de Matri_x…"), "win.mx-registrar"),
                                     *(((_("Añadir cuenta de X (_Twitter)…"), "app.anadir-cuenta-x"),) if datos_x.disponible() else ()),
                                     *(((_("Añadir cuenta de Re_ddit…"), "app.anadir-cuenta-reddit"),) if datos_reddit.disponible() else ()),
                                     *(((_("Añadir cuenta de _PeerTube…"), "app.anadir-cuenta-peertube"),) if datos_peertube.disponible() else ()),
                                     (_("_Eliminar la cuenta seleccionada…"), "win.eliminar-cuenta"),
                                     (_("_Añadir feed…"), "app.anadir-feed"),
                                     (_("_Importar OPML…"), "app.importar-opml"),
                                     (_("_Exportar OPML…"), "app.exportar-opml")))
    archivo.append_section(None, sec((_("_Preferencias"), "app.preferencias"),
                                     (_("_Salir"), "app.salir")))
    barra.append_submenu(_("_Archivo"), archivo)

    feeds = Gio.Menu()
    feeds.append_section(None, sec((_("Actualizar _todos"), "win.actualizar"),
                                   (_("Actualizar el _feed seleccionado"), "win.actualizar-feed"),
                                   (_("_Marcar todo como leído"), "win.marcar-todo-leido")))
    feeds.append_section(None, sec((_("_Buscar feeds y podcasts…"), "win.rss-buscar-feeds"),
                                   (_("Crear un feed desde una _web sin RSS…"), "win.rss-feed-web"),
                                   (_("Añadir un canal de _YouTube…"), "win.rss-youtube"),
                                   (_("Importar suscripciones de YouTube (Google _Takeout)…"), "win.rss-takeout")))
    feeds.append_section(None, sec((_("Gestionar _categorías…"), "win.rss-categorias"),
                                   (_("Gestionar _etiquetas…"), "win.rss-etiquetas"),
                                   (_("_Reglas y filtros automáticos…"), "win.rss-reglas"),
                                   (_("Carpetas _inteligentes…"), "win.rss-carpetas"),
                                   (_("Búsquedas _guardadas…"), "win.rss-busquedas"),
                                   (_("_Guardar la búsqueda actual como buffer…"), "win.rss-guardar-busqueda"),
                                   (_("_Alertas por palabra clave…"), "win.rss-alertas")))
    feeds.append_section(None, sec((_("_Salud de los feeds…"), "win.rss-salud"),
                                   (_("_Descargas de podcast…"), "win.rss-descargas"),
                                   (_("_Sincronización con un servidor…"), "win.rss-sincronizacion")))
    feeds.append_section(None, sec((_("_Historial de lectura…"), "win.rss-historial"),
                                   (_("Estadísticas de _lectura…"), "win.rss-estadisticas"),
                                   (_("_Resumen de los artículos sin leer"), "win.rss-resumen"),
                                   (_("Agrupar por _fecha"), "win.rss-agrupar-fecha"),
                                   (_("E_xportar artículos en lote…"), "win.rss-exportar"),
                                   (_("_Papelera de artículos…"), "win.herr-papelera")))
    barra.append_submenu(_("_Feeds"), feeds)

    art = Gio.Menu()
    art.append_section(None, sec((_("_Leído / no leído"), "win.leido"),
                                 (_("_Destacar"), "win.favorito"),
                                 (_("Leer más _tarde"), "win.mas-tarde"),
                                 (_("A la _papelera"), "win.papelera")))
    art.append_section(None, sec((_("Siguiente sin leer"), "win.siguiente-no-leido"),
                                 (_("Anterior sin leer"), "win.anterior-no-leido")))
    art.append_section(None, sec((_("Descargar el artículo _completo"), "win.texto-completo"),
                                 (_("Abrir en el _navegador"), "win.navegador"),
                                 (_("Copiar el _enlace"), "win.copiar-enlace"),
                                 (_("Copiar el t_ítulo"), "win.copiar-titulo"),
                                 (_("_Versiones anteriores…"), "win.rss-versiones")))
    art.append_section(None, sec((_("Eti_quetas del artículo…"), "win.rss-etiquetas-articulo"),
                                 (_("_Imágenes del artículo…"), "win.rss-galeria"),
                                 (_("Añadir a la _cola de podcasts"), "win.rss-encolar")))
    barra.append_submenu(_("A_rtículo"), art)

    pub = Gio.Menu()
    pub.append_section(None, sec((_("_Nueva publicación"), "win.nuevo"),
                                 (_("_Responder"), "win.responder"),
                                 (_("C_itar"), "win.citar"),
                                 (_("_Editar mi publicación"), "win.editar"),
                                 (_("Nuevo _hilo…"), "win.nuevo-hilo"),
                                 (_("Hilos _guardados…"), "win.hilos-guardados")))
    pub.append_section(None, sec((_("Me _gusta"), "win.me-gusta"),
                                 (_("_Impulsar"), "win.impulsar"),
                                 (_("_Marcador"), "win.marcador")))
    pub.append_section(None, sec((_("Ver la _conversación"), "win.conversacion"),
                                 (_("_Perfil del autor"), "win.perfil"),
                                 (_("Publicaciones del _autor"), "win.cronologia-usuario"),
                                 (_("_Votar en la encuesta"), "win.votar"),
                                 (_("Re_portar la publicación"), "win.reportar"),
                                 (_("Cargar publicaciones _anteriores"), "win.cargar-mas"),
                                 (_("C_errar el buffer"), "win.cerrar-buffer")))
    pub.append_section(None, sec((_("Más acciones sobre la _cuenta del autor…"), "win.ms-cuenta-del-autor"),
                                 (_("_Filtrar esta publicación…"), "win.ms-filtrar-publicacion"),
                                 (_("Borrar y _volver a redactar"), "win.ms-borrar-redactar"),
                                 (_("Ver las citas de mi publicación…"), "win.ms-citas"),
                                 (_("Quién puede citar mi publicación…"), "win.ms-politica-citas")))
    pub.append_section(None, sec((_("Quién la ha impulsa_do"), "win.quien-impulso"),
                                 (_("Quién la ha marcado como _favorita"), "win.quien-favorito"),
                                 (_("_Historial de ediciones"), "win.historial"),
                                 (_("_Silenciar la conversación"), "win.silenciar-conversacion"),
                                 (_("Reacti_var la conversación"), "win.reactivar-conversacion"),
                                 (_("Fi_jar en mi perfil"), "win.fijar"),
                                 (_("Desfijar de mi perfil"), "win.desfijar")))
    pub.append_section(None, sec((_("Abrir una _lista…"), "win.abrir-lista"),
                                 (_("Abrir un _hashtag…"), "win.abrir-hashtag"),
                                 (_("_Buscar en Mastodon…"), "win.buscar-mastodon"),
                                 (_("Hashtags en _tendencia…"), "win.tendencias"),
                                 (_("Hashtags que _sigo…"), "win.hashtags-seguidos"),
                                 (_("Seguir este hashtag"), "win.seguir-hashtag"),
                                 (_("Dejar de seguir este hashtag"), "win.dejar-hashtag")))
    pub.append_section(None, sec((_("_Borradores…"), "win.borradores"),
                                 (_("Publicaciones pro_gramadas…"), "win.programadas"),
                                 (_("Descartar la _notificación"), "win.descartar-notificacion"),
                                 (_("_Borrar todas las notificaciones"), "win.limpiar-notificaciones")))
    cuentas = Gio.Menu()
    cuentas.append_section(None, sec((_("_Seguidores…"), "win.cuentas-followers"),
                                     (_("S_iguiendo…"), "win.cuentas-following"),
                                     (_("Solicitudes de seguimiento…"), "win.cuentas-solicitudes"),
                                     (_("Cuentas _bloqueadas…"), "win.cuentas-blocked"),
                                     (_("Cuentas s_ilenciadas…"), "win.cuentas-muted")))
    cuentas.append_section(None, sec((_("Filtros del _servidor…"), "win.filtros"),
                                     (_("Editar _mi perfil…"), "win.mi-perfil")))
    cuentas.append_section(None, sec((_("Mis _listas…"), "win.ms-listas"),
                                     (_("_Hashtags destacados en mi perfil…"), "win.ms-hashtags-destacados"),
                                     (_("Cuentas _destacadas en mi perfil…"), "win.ms-cuentas-destacadas"),
                                     (_("Servidores _bloqueados…"), "win.ms-dominios-bloqueados"),
                                     (_("_Novedades de seguidores…"), "win.ms-novedades-seguidores"),
                                     (_("Quitar mi foto de _perfil…"), "win.ms-quitar-avatar"),
                                     (_("Quitar mi imagen de _cabecera…"), "win.ms-quitar-cabecera")))
    cuentas.append_section(None, sec((_("_Anuncios del servidor…"), "win.ms-anuncios"),
                                     (_("Enlaces en _tendencia…"), "win.ms-enlaces-tendencia"),
                                     (_("_Información de la instancia…"), "win.ms-info-instancia"),
                                     (_("_Directorio de cuentas…"), "win.ms-directorio"),
                                     (_("Cuentas _sugeridas…"), "win.ms-sugerencias")))
    cuentas.append_section(None, sec((_("Mis co_lecciones…"), "win.ms-colecciones-mias"),
                                     (_("Colecciones en las que _apareces…"), "win.ms-colecciones-donde-estoy"),
                                     (_("Colecciones de una cuenta…"), "win.ms-colecciones-de"),
                                     (_("Añadir al autor a una de mis colecciones…"), "win.ms-coleccion-anadir-autor")))
    cuentas.append_section(None, sec((_("Notificaciones a_grupadas…"), "win.notif-agrupadas"),
                                     (_("_Política de notificaciones…"), "win.notif-politica"),
                                     (_("Solicitudes de _notificaciones…"), "win.notif-solicitudes")))
    admin = Gio.Menu()
    admin.append_section(None, sec((_("_Informes de moderación…"), "win.admin-informes"),
                                   (_("_Cuentas de la instancia…"), "win.admin-cuentas"),
                                   (_("_Dominios bloqueados…"), "win.admin-dominios")))
    admin.append_section(None, sec((_("Bloqueos de _IP…"), "win.ms-admin-ip"),
                                   (_("Dominios _permitidos…"), "win.ms-admin-permitidos"),
                                   (_("Dominios de correo bloqueados…"), "win.ms-admin-correos"),
                                   (_("Correos canónicos bloqueados…"), "win.ms-admin-canonicos"),
                                   (_("_Tendencias y descubrimiento…"), "win.ms-admin-tendencias"),
                                   (_("_Estadísticas del servidor…"), "win.ms-admin-estadisticas")))
    cuentas.append_submenu(_("Adminis_tración de la instancia"), admin)
    pub.append_submenu(_("Mi _cuenta"), cuentas)
    barra.append_submenu(_("_Mastodon"), pub)

    bsky = Gio.Menu()
    bsky.append_section(None, sec((_("_Nueva publicación"), "win.nuevo"),
                                  (_("Nuevo _hilo…"), "win.nuevo-hilo"),
                                  (_("_Responder"), "win.responder"),
                                  (_("C_itar"), "win.citar"),
                                  (_("Borrar mi publicación"), "win.papelera")))
    bsky.append_section(None, sec((_("Me _gusta"), "win.me-gusta"),
                                  (_("Re_publicar"), "win.impulsar"),
                                  (_("_Marcador"), "win.marcador")))
    bsky.append_section(None, sec((_("Ver la _conversación"), "win.conversacion"),
                                  (_("_Perfil del autor"), "win.perfil"),
                                  (_("Publicaciones del _autor"), "win.cronologia-usuario"),
                                  (_("Cargar publicaciones a_nteriores"), "win.cargar-mas"),
                                  (_("C_errar el buffer"), "win.cerrar-buffer")))
    bsky.append_section(None, sec((_("_Buscar en Bluesky…"), "win.buscar-mastodon"),
                                  (_("Abrir un _feed…"), "win.abrir-feed"),
                                  (_("Abrir una _lista…"), "win.abrir-lista")))
    bsky.append_section(None, sec((_("Quién ha dado me gusta…"), "win.bsky-quien-gusta"),
                                  (_("Quién ha republicado…"), "win.bsky-quien-republico"),
                                  (_("Quién ha citado…"), "win.bsky-quien-cito"),
                                  (_("Ocultar la publicación para mí"), "win.bsky-ocultar"),
                                  (_("Silenciar el hilo"), "win.bsky-silenciar-hilo"),
                                  (_("Reactivar el hilo"), "win.bsky-reactivar-hilo"),
                                  (_("Denunciar la publicación…"), "win.bsky-reportar"),
                                  (_("Denunciar la cuenta…"), "win.bsky-reportar-cuenta")))
    dm = Gio.Menu()
    dm.append_section(None, sec((_("_Crear un grupo…"), "win.at-grupo-crear"),
                                (_("_Entrar en un grupo con un enlace…"), "win.at-grupo-entrar"),
                                (_("_Gestionar el grupo…"), "win.at-grupo-gestionar"),
                                (_("Solicitudes de _mensajes…"), "win.at-solicitudes-mensajes")))
    dm.append_section(None, sec((_("_Nuevo mensaje directo…"), "win.bsky-mensaje-nuevo"),
                                (_("Mensaje directo al _autor"), "win.bsky-mensaje-autor"),
                                (_("_Reaccionar al mensaje…"), "win.bsky-reaccionar"),
                                (_("_Silenciar o reactivar la conversación"), "win.bsky-convo-silenciar")))
    bsky.append_submenu(_("Mensajes _directos"), dm)
    cta = Gio.Menu()
    cta.append_section(None, sec((_("_Seguidores…"), "win.bsky-seguidores"),
                                 (_("S_iguiendo…"), "win.bsky-siguiendo"),
                                 (_("Cuentas _bloqueadas…"), "win.bsky-bloqueadas"),
                                 (_("Cuentas s_ilenciadas…"), "win.bsky-silenciadas")))
    cta.append_section(None, sec((_("_Tendencias…"), "win.at-tendencias"),
                                 (_("Paquetes de _inicio…"), "win.at-paquetes"),
                                 (_("Buscar _cuentas…"), "win.at-buscar-cuentas"),
                                 (_("Cuentas a cuya _actividad sigues…"), "win.at-actividad"),
                                 (_("Cuentas _similares al autor…"), "win.at-cuentas-similares"),
                                 (_("Seguidores _en común con el autor…"), "win.at-seguidores-conocidos"),
                                 (_("_Feeds del autor…"), "win.at-feeds-de"),
                                 (_("A_visarme cuando el autor publique"), "win.at-suscribir-actividad")))
    cta.append_section(None, sec((_("Cuentas _sugeridas…"), "win.at-sugerencias"),
                                 (_("Cambiar el nombre de _usuario…"), "win.at-cambiar-usuario"),
                                 (_("_Vista de las cronologías y los hilos…"), "win.at-vista"),
                                 (_("Hilos silenciados y publicaciones _ocultas…"), "win.at-ocultos"),
                                 (_("Salida _pendiente…"), "win.at-salida")))
    cta.append_section(None, sec((_("_Listas…"), "win.bsky-listas"),
                                 (_("_Feeds…"), "win.bsky-feeds"),
                                 (_("_Palabras silenciadas…"), "win.bsky-palabras"),
                                 (_("_Moderación de contenido…"), "win.bsky-moderacion"),
                                 (_("Servicios de _etiquetado…"), "win.bsky-etiquetadores")))
    cta.append_section(None, sec((_("Editar mi _perfil…"), "win.bsky-mi-perfil"),
                                 (_("_Contraseñas de aplicación…"), "win.bsky-claves"),
                                 (_("_Eliminar la cuenta…"), "win.bsky-eliminar-cuenta")))
    bsky.append_submenu(_("Mi _cuenta"), cta)
    barra.append_submenu(_("Blues_ky"), bsky)

    ws = Gio.Menu()
    ws.append_section(None, sec((_("_Nueva publicación"), "win.nuevo"),
                                (_("Nuevo _hilo…"), "win.nuevo-hilo"),
                                (_("_Responder"), "win.responder"),
                                (_("C_itar"), "win.citar"),
                                (_("Borrar mi publicación"), "win.papelera")))
    ws.append_section(None, sec((_("Me _gusta"), "win.me-gusta"),
                                (_("Re_publicar"), "win.impulsar"),
                                (_("_Marcador"), "win.marcador")))
    ws.append_section(None, sec((_("Ver la _conversación"), "win.conversacion"),
                                (_("_Perfil del autor"), "win.perfil"),
                                (_("Publicaciones del _autor"), "win.cronologia-usuario"),
                                (_("Cargar publicaciones a_nteriores"), "win.cargar-mas"),
                                (_("C_errar el buffer"), "win.cerrar-buffer")))
    ws.append_section(None, sec((_("_Buscar en W Social…"), "win.buscar-mastodon"),
                                (_("Abrir un _feed…"), "win.abrir-feed"),
                                (_("Abrir una _lista…"), "win.abrir-lista")))
    ws.append_section(None, sec((_("Quién ha dado me gusta…"), "win.ws-quien-gusta"),
                                (_("Quién ha republicado…"), "win.ws-quien-republico"),
                                (_("Quién ha citado…"), "win.ws-quien-cito"),
                                (_("Silenciar el hilo"), "win.ws-silenciar-hilo"),
                                (_("Reactivar el hilo"), "win.ws-reactivar-hilo"),
                                (_("Denunciar la publicación…"), "win.ws-reportar"),
                                (_("Denunciar la cuenta…"), "win.ws-reportar-cuenta")))
    wdm = Gio.Menu()
    wdm.append_section(None, sec((_("_Crear un grupo…"), "win.at-grupo-crear"),
                                 (_("_Entrar en un grupo con un enlace…"), "win.at-grupo-entrar"),
                                 (_("_Gestionar el grupo…"), "win.at-grupo-gestionar")))
    wdm.append_section(None, sec((_("_Nuevo mensaje directo…"), "win.ws-mensaje-nuevo"),
                                 (_("Mensaje directo al _autor"), "win.ws-mensaje-autor"),
                                 (_("_Reaccionar al mensaje…"), "win.ws-reaccionar"),
                                 (_("_Silenciar o reactivar la conversación"), "win.ws-convo-silenciar")))
    ws.append_submenu(_("Mensajes _directos"), wdm)
    wcta = Gio.Menu()
    wcta.append_section(None, sec((_("_Seguidores…"), "win.ws-seguidores"),
                                  (_("S_iguiendo…"), "win.ws-siguiendo"),
                                  (_("Cuentas _bloqueadas…"), "win.ws-bloqueadas"),
                                  (_("Cuentas s_ilenciadas…"), "win.ws-silenciadas")))
    wcta.append_section(None, sec((_("_Tendencias…"), "win.at-tendencias"),
                                  (_("Paquetes de _inicio…"), "win.at-paquetes"),
                                  (_("Buscar _cuentas…"), "win.at-buscar-cuentas")))
    wcta.append_section(None, sec((_("_Listas…"), "win.ws-listas"),
                                  (_("_Palabras silenciadas…"), "win.ws-palabras"),
                                  (_("Preferencias de _contenido…"), "win.ws-contenido"),
                                  (_("Servicios de _etiquetado…"), "win.ws-etiquetadores"),
                                  (_("_Borradores del servidor…"), "win.ws-borradores"),
                                  (_("Salida _pendiente…"), "win.at-salida")))
    wcta.append_section(None, sec((_("Cuentas _sugeridas…"), "win.at-sugerencias"),
                                  (_("Feeds _populares…"), "win.at-feeds-populares"),
                                  (_("Buscar publicaciones con _filtros…"), "win.at-busqueda-filtros"),
                                  (_("_Estado temporal…"), "win.at-estado-temporal"),
                                  (_("¿Quién puede e_scribirme?…"), "win.at-privacidad-chat")))
    wcta.append_section(None, sec((_("E_stado de la cuenta…"), "win.at-estado-cuenta"),
                                  (_("Información del _servidor…"), "win.at-servidor"),
                                  (_("_Verificación de edad…"), "win.at-verificacion-edad"),
                                  (_("Cambiar el nombre de _usuario…"), "win.at-cambiar-usuario"),
                                  (_("Guardar una _copia de mi cuenta…"), "win.at-exportar-repositorio")))
    wcta.append_section(None, sec((_("Editar mi _perfil…"), "win.ws-mi-perfil"),
                                  (_("_Contraseñas de aplicación…"), "win.ws-claves"),
                                  (_("_Volver a autorizar la cuenta…"), "win.ws-reautorizar"),
                                  (_("_Eliminar la cuenta…"), "win.ws-eliminar-cuenta")))
    ws.append_submenu(_("Mi _cuenta"), wcta)
    barra.append_submenu(_("_W Social"), ws)

    if datos_x.disponible():
        tw = Gio.Menu()
        tw.append_section(None, sec((_("_Nueva publicación"), "win.nuevo"),
                                    (_("_Responder"), "win.responder"),
                                    (_("C_itar"), "win.citar"),
                                    (_("Borrar mi publicación"), "win.papelera"),
                                    (_("_Votar en la encuesta…"), "win.votar")))
        tw.append_section(None, sec((_("Me _gusta"), "win.me-gusta"),
                                    (_("_Retweet"), "win.impulsar"),
                                    (_("_Marcador"), "win.marcador")))
        tw.append_section(None, sec((_("Ver la _conversación"), "win.conversacion"),
                                    (_("_Perfil del autor"), "win.perfil"),
                                    (_("Publicaciones del _autor"), "win.cronologia-usuario"),
                                    (_("Perfil de un usuario _mencionado…"), "win.tw-perfil-mencionado"),
                                    (_("Cronología de un usuario mencionado…"), "win.tw-cronologia-mencionado"),
                                    (_("Cargar publicaciones a_nteriores"), "win.cargar-mas"),
                                    (_("C_errar el buffer"), "win.cerrar-buffer")))
        tw.append_section(None, sec((_("_Buscar en X…"), "win.buscar-mastodon"),
                                    (_("Buscar _cuentas…"), "win.tw-buscar-usuarios"),
                                    (_("Cronología de un _usuario…"), "win.tw-cronologia-de"),
                                    (_("Perfil de un usuario…"), "win.tw-perfil-de"),
                                    (_("Abrir una _lista…"), "win.abrir-lista"),
                                    (_("_Gestionar mis listas…"), "win.tw-listas"),
                                    (_("Buscar c_omunidades…"), "win.tw-buscar-comunidades"),
                                    (_("Salir de la comunidad de este buffer"), "win.tw-salir-comunidad"),
                                    (_("_Tendencias…"), "win.tw-tendencias"),
                                    (_("Tendencias de un _país…"), "win.tw-tendencias-lugar"),
                                    (_("Publicaciones pro_gramadas…"), "win.tw-programados")))
        tw.append_section(None, sec((_("Quién ha dado me gusta…"), "win.tw-quien-gusta"),
                                    (_("Quién ha hecho retweet…"), "win.tw-quien-rt"),
                                    (_("Añadir al autor a una lista…"), "win.tw-anadir-lista"),
                                    (_("Seguir al autor"), "win.tw-seguir"),
                                    (_("Dejar de seguir al autor"), "win.tw-dejar-seguir"),
                                    (_("Silenciar al autor"), "win.tw-silenciar"),
                                    (_("Quitar el silencio al autor"), "win.tw-quitar-silencio"),
                                    (_("Bloquear al autor"), "win.tw-bloquear"),
                                    (_("Desbloquear al autor"), "win.tw-desbloquear")))
        tdm = Gio.Menu()
        tdm.append_section(None, sec((_("_Nuevo mensaje directo…"), "win.tw-mensaje-nuevo"),
                                     (_("Mensaje directo al _autor"), "win.tw-mensaje-autor"),
                                     (_("_Reaccionar al mensaje…"), "win.tw-reaccionar")))
        tw.append_submenu(_("Mensajes _directos"), tdm)
        tw.append_section(None, sec((_("Mostrar u o_cultar buffers…"), "win.tw-ocultar-buffers"),
                                    (_("_Reimportar las cookies de la cuenta…"), "win.tw-reimportar"),
                                    (_("_Eliminar la cuenta…"), "win.eliminar-cuenta")))
        barra.append_submenu(_("X (Twitt_er)"), tw)

    if datos_reddit.disponible():
        rd = Gio.Menu()
        rd.append_section(None, sec((_("_Nueva publicación o mensaje"), "win.nuevo"),
                                    (_("_Responder"), "win.responder"),
                                    (_("_Editar lo mío"), "win.editar"),
                                    (_("_Borrar lo mío"), "win.papelera"),
                                    (_("Crosspost a otra _comunidad…"), "win.impulsar")))
        rd.append_section(None, sec((_("Voto _positivo"), "win.me-gusta"),
                                    (_("Voto _negativo"), "win.voto-negativo"),
                                    (_("_Guardar o quitar de guardados"), "win.marcador"),
                                    (_("_Ocultar o mostrar la publicación"), "win.rd-ocultar")))
        rd.append_section(None, sec((_("Ver los co_mentarios"), "win.conversacion"),
                                    (_("Perfil del _autor"), "win.perfil"),
                                    (_("Publicaciones del a_utor"), "win.cronologia-usuario"),
                                    (_("Abrir el su_breddit de la publicación"), "win.rd-subreddit-fila"),
                                    (_("Cargar elementos a_nteriores"), "win.cargar-mas"),
                                    (_("C_errar el buffer"), "win.cerrar-buffer")))
        rd.append_section(None, sec((_("_Ir a un subreddit…"), "win.abrir-lista"),
                                    (_("_Buscar publicaciones…"), "win.buscar-mastodon"),
                                    (_("Buscar _subreddits…"), "win.abrir-hashtag"),
                                    (_("Buscar _usuarios…"), "win.rd-buscar-usuarios"),
                                    (_("Perfil de un usuario…"), "win.rd-perfil-de"),
                                    (_("Cambiar el or_den de este buffer…"), "win.rd-orden")))
        rd.append_section(None, sec((_("S_uscribirse al subreddit"), "win.rd-suscribirse"),
                                    (_("Cancelar la suscripción"), "win.rd-cancelar-suscripcion"),
                                    (_("_Fijar el subreddit en el árbol"), "win.rd-fijar"),
                                    (_("Mensaje privado al autor…"), "win.rd-mensaje-autor"),
                                    (_("Marcar la bandeja como _leída"), "win.marcar-todo-leido")))
        rd.append_section(None, sec((_("Moderar el elemento enfo_cado…"), "win.rd-moderar"),
                                    (_("Cola de moderación…"), "win.rd-mod-cola"),
                                    (_("Usuarios del subreddit…"), "win.rd-mod-usuarios"),
                                    (_("Motivos de eliminación…"), "win.rd-mod-motivos"),
                                    (_("Configuración del subreddit…"), "win.rd-mod-config"),
                                    (_("Registro de moderación…"), "win.rd-mod-registro")))
        rd.append_section(None, sec((_("_Wiki de un subreddit…"), "win.rd-wiki"),
                                    (_("_Modmail…"), "win.rd-modmail"),
                                    (_("Hilos en _directo…"), "win.rd-live"),
                                    (_("_Etiquetas (flair) de un subreddit…"), "win.rd-flairs"),
                                    (_("Emo_jis de un subreddit…"), "win.rd-emojis"),
                                    (_("_Barra lateral de un subreddit…"), "win.rd-widgets"),
                                    (_("Co_lecciones de un subreddit…"), "win.rd-colecciones"),
                                    (_("Bo_rradores de Reddit…"), "win.rd-borradores")))
        rd.append_section(None, sec((_("Mostrar u ocultar bu_ffers…"), "win.rd-ocultar-buffers"),
                                    (_("Volver a autorizar la cuenta…"), "win.rd-reautorizar"),
                                    (_("Eliminar la cuenta…"), "win.eliminar-cuenta")))
        barra.append_submenu(_("Re_ddit"), rd)

    if datos_peertube.disponible():
        pt = Gio.Menu()
        pt.append_section(None, sec((_("_Reproducir"), "win.reproducir"),
                                    (_("Ver _detalles"), "win.pt-detalles"),
                                    (_("Ver _subtítulos…"), "win.pt-subtitulos"),
                                    (_("_Calidad de reproducción…"), "win.pt-calidad")))
        pt.append_section(None, sec((_("Me _gusta"), "win.me-gusta"),
                                    (_("_No me gusta"), "win.voto-negativo"),
                                    (_("Ver los co_mentarios"), "win.conversacion"),
                                    (_("Co_mentar"), "win.responder"),
                                    (_("S_uscribirse al canal"), "win.pt-suscribir"),
                                    (_("Cancelar la suscripción al canal"), "win.pt-cancelar-suscripcion")))
        pt.append_section(None, sec((_("Datos del _canal"), "win.perfil"),
                                    (_("Abrir el c_anal"), "win.cronologia-usuario"),
                                    (_("Abrir la c_uenta"), "win.pt-abrir-cuenta"),
                                    (_("Añadir a «ver más _tarde»"), "win.marcador"),
                                    (_("Añadir a una _lista…"), "win.pt-anadir-lista"),
                                    (_("_Descargar…"), "win.pt-descargar")))
        pt.append_section(None, sec((_("_Buscar vídeos…"), "win.buscar-mastodon"),
                                    (_("Buscar canal_es…"), "win.abrir-hashtag"),
                                    (_("_Ir a un canal…"), "win.abrir-lista"),
                                    (_("Cargar vídeos a_nteriores"), "win.cargar-mas"),
                                    (_("C_errar el buffer"), "win.cerrar-buffer")))
        pt.append_section(None, sec((_("_Subir un vídeo…"), "win.pt-subir"),
                                    (_("_Importar un vídeo por dirección…"), "win.pt-importar"),
                                    (_("_Editar mi vídeo, lista o canal…"), "win.editar"),
                                    (_("_Borrar o quitar…"), "win.papelera"),
                                    (_("Crear una lista o un canal (en su sección)"), "win.nuevo"),
                                    (_("Vaciar el _historial…"), "win.pt-vaciar-historial")))
        mod = Gio.Menu()
        mod.append_section(None, sec((_("Cola de denuncias…"), "win.pt-mod-denuncias"),
                                     (_("Vídeos bloqueados…"), "win.pt-mod-bloqueados")))
        adm = Gio.Menu()
        adm.append_section(None, sec((_("Usuarios…"), "win.pt-admin-usuarios"),
                                     (_("Solicitudes de registro…"), "win.pt-admin-registros"),
                                     (_("Federación (instancias)…"), "win.pt-admin-federacion"),
                                     (_("Trabajos del servidor…"), "win.pt-admin-trabajos"),
                                     (_("Plugins…"), "win.pt-admin-plugins"),
                                     (_("Tokens de runners…"), "win.pt-admin-runners")))
        pt.append_section(None, sec((_("Crear un _directo…"), "win.pt-crear-directo"),
                                    (_("Estadísticas del _vídeo…"), "win.pt-estadisticas-video"),
                                    (_("Datos de emisión del directo…"), "win.pt-datos-emision"),
                                    (_("Sesiones y repeticiones del directo…"), "win.pt-sesiones-directo"),
                                    (_("Estadísticas de la instancia…"), "win.pt-estadisticas-instancia"),
                                    (_("Mis denuncias…"), "win.pt-mis-denuncias"),
                                    (_("Mis _importaciones…"), "win.pt-importaciones"),
                                    (_("Preferencias de _notificaciones…"), "win.pt-avisos")))
        pt.append_submenu(_("_Moderación"), mod)
        pt.append_submenu(_("_Administración"), adm)
        pt.append_section(None, sec((_("Silenciar la cuenta del autor"), "win.pt-silenciar"),
                                    (_("Silenciar la instancia del autor"), "win.pt-silenciar-instancia"),
                                    (_("_Reportar el vídeo…"), "win.pt-reportar"),
                                    (_("Marcar la notificación como leída"), "win.leido"),
                                    (_("Marcar todas las notificaciones como leídas"), "win.marcar-todo-leido")))
        pt.append_section(None, sec((_("Mostrar u ocultar secciones…"), "win.pt-ocultar-secciones"),
                                    (_("Volver a iniciar sesión…"), "win.pt-reautorizar"),
                                    (_("Eliminar la cuenta…"), "win.eliminar-cuenta")))
        barra.append_submenu(_("Peer_Tube"), pt)

    tg = Gio.Menu()
    tg.append_section(None, sec((_("_Nuevo mensaje"), "win.nuevo"),
                                (_("_Responder"), "win.responder"),
                                (_("_Editar mi mensaje"), "win.editar"),
                                (_("_Borrar el mensaje o salir del chat"), "win.papelera"),
                                (_("Re_accionar…"), "win.tg-reaccionar"),
                                (_("Quién ha reaccionado…"), "win.tg-quien-reacciono"),
                                (_("Re_enviar a otro chat…"), "win.tg-reenviar"),
                                (_("_Copiar el texto"), "win.tg-copiar")))
    tg.append_section(None, sec((_("Reproducir la nota de _voz o el audio"), "win.reproducir"),
                                (_("_Descargar el adjunto"), "win.tg-descargar"),
                                (_("_Abrir el adjunto"), "win.tg-abrir-adjunto"),
                                (_("Enviar un a_rchivo…"), "win.tg-enviar-archivo"),
                                (_("_Grabar y enviar una nota de voz"), "win.grabar-voz"),
                                (_("Cancelar la grabación de voz"), "win.cancelar-voz")))
    tg.append_section(None, sec((_("Carpetas de _chats…"), "win.tg-carpetas"),
                                (_("Crear un grupo, foro o _canal…"), "win.tg-crear-chat"),
                                (_("_Unirse a un chat con un enlace…"), "win.tg-unirse-enlace"),
                                (_("_Buscar en todos los chats…"), "win.tg-buscar-global"),
                                (_("Buscar en el chat _actual…"), "win.tg-buscar-chat"),
                                (_("Buscar publicaciones de canales por _hashtag…"), "win.tg-buscar-publicaciones"),
                                (_("Chats _frecuentes…"), "win.tg-frecuentes"),
                                (_("Canales _parecidos…"), "win.tg-similares"),
                                (_("Contenido _compartido del chat…"), "win.tg-medios")))
    tg.append_section(None, sec((_("Nuevo _chat con un usuario…"), "win.tg-chat-nuevo"),
                                (_("Mensajes _guardados"), "win.tg-guardados"),
                                (_("_Silenciar o reactivar el chat"), "win.tg-silenciar"),
                                (_("_Fijar o desfijar el chat"), "win.tg-fijar"),
                                (_("Archi_var el chat"), "win.tg-archivar"),
                                (_("Marcar el chat como _leído"), "win.tg-leido"),
                                (_("Marcar el chat como _no leído"), "win.tg-no-leido"),
                                (_("Marcar _todos como leídos"), "win.tg-todo-leido"),
                                (_("_Información del chat"), "win.perfil"),
                                (_("Cargar mensajes _anteriores"), "win.cargar-mas")))
    tgm = Gio.Menu()
    tgm.append_section(None, sec((_("Crear una _encuesta…"), "win.tg-encuesta-nueva"),
                                 (_("Ver o _votar la encuesta del mensaje…"), "win.tg-encuesta"),
                                 (_("Crear una lista de _tareas…"), "win.tg-lista-tareas-nueva"),
                                 (_("Ver la lista de tareas del _mensaje…"), "win.tg-lista-tareas"),
                                 (_("Enviar una _ubicación…"), "win.tg-ubicacion"),
                                 (_("Compartir un c_ontacto…"), "win.tg-contacto"),
                                 (_("Enviar un _dado…"), "win.tg-dado")))
    tgm.append_section(None, sec((_("Enviar un á_lbum…"), "win.tg-album"),
                                 (_("Enviar un _GIF…"), "win.tg-gif"),
                                 (_("Enviar un _sticker…"), "win.tg-sticker"),
                                 (_("Enviar una _videonota…"), "win.tg-videonota")))
    tgm.append_section(None, sec((_("_Editar el texto del archivo…"), "win.tg-editar-pie"),
                                 (_("Copiar a _otro chat…"), "win.tg-copiar-a"),
                                 (_("Reenviar el á_lbum entero…"), "win.tg-reenviar-album"),
                                 (_("Marcar como leído _hasta este mensaje"), "win.tg-leido-hasta"),
                                 (_("I_r a una fecha…"), "win.tg-saltar-fecha"),
                                 (_("Chats en _común con esta persona…"), "win.tg-chats-comun"),
                                 (_("Historias del _chat…"), "win.tg-historias-chat")))
    tgm.append_section(None, sec((_("_Fijar el mensaje"), "win.tg-fijar-mensaje"),
                                 (_("D_esfijar el mensaje"), "win.tg-desfijar-mensaje"),
                                 (_("Desfijar _todos los mensajes"), "win.tg-desfijar-todos"),
                                 (_("_Programar un mensaje…"), "win.tg-programar"),
                                 (_("Mensajes pro_gramados…"), "win.tg-programados")))
    tgm.append_section(None, sec((_("_Botones del mensaje…"), "win.tg-botones"),
                                 (_("Comandos del _bot…"), "win.tg-comandos-bot"),
                                 (_("_Iniciar el bot"), "win.tg-iniciar-bot")))
    tgm.append_section(None, sec((_("_Detalles del mensaje…"), "win.tg-detalles-mensaje"),
                                 (_("T_raducir el mensaje (Telegram)…"), "win.tg-traducir-mensaje"),
                                 (_("_Guardar el mensaje en Mensajes guardados"), "win.tg-guardar-mensaje"),
                                 (_("Borrar el archivo descargado de este e_quipo"), "win.tg-borrar-descarga"),
                                 (_("Marcar las _menciones como leídas"), "win.tg-menciones-leidas"),
                                 (_("Marcar las _reacciones como leídas"), "win.tg-reacciones-leidas")))
    tg.append_submenu(_("_Mensajes especiales"), tgm)
    tga = Gio.Menu()
    tga.append_section(None, sec((_("_Miembros…"), "win.tg-miembros"),
                                 (_("_Enlaces de invitación…"), "win.tg-enlaces"),
                                 (_("_Solicitudes de unión…"), "win.tg-solicitudes"),
                                 (_("_Ajustes del chat…"), "win.tg-ajustes"),
                                 (_("_Temas del foro…"), "win.tg-temas"),
                                 (_("_Grupo de comentarios del canal…"), "win.tg-grupo-comentarios"),
                                 (_("_Registro de administración…"), "win.tg-registro-admin"),
                                 (_("_Vaciar la conversación…"), "win.tg-vaciar"),
                                 (_("Enviar _como…"), "win.tg-enviar-como")))
    tg.append_submenu(_("Administrar el _chat"), tga)
    tgc = Gio.Menu()
    tgc.append_section(None, sec((_("Mi _perfil…"), "win.tg-mi-perfil"),
                                 (_("_Contactos…"), "win.tg-contactos"),
                                 (_("Usuarios _bloqueados…"), "win.tg-bloqueados"),
                                 (_("_Sesiones activas…"), "win.tg-sesiones"),
                                 (_("P_rivacidad…"), "win.tg-privacidad"),
                                 (_("S_eguridad (verificación en dos pasos)…"), "win.tg-seguridad")))
    tgc.append_section(None, sec((_("Privacidad de los c_hats…"), "win.tg-privacidad-global"),
                                 (_("Cambiar el _número de teléfono…"), "win.tg-cambiar-numero"),
                                 (_("_Importar contactos…"), "win.tg-importar-contactos")))
    tgc.append_section(None, sec((_("Historias _archivadas…"), "win.tg-historias-archivadas"),
                                 (_("_Historias de tus contactos…"), "win.tg-historias"),
                                 (_("_Mis historias…"), "win.tg-mis-historias"),
                                 (_("Mis b_ots…"), "win.tg-mis-bots"),
                                 (_("Estado de la potenciación del _canal…"), "win.tg-boosts"),
                                 (_("Potenciar el canal"), "win.tg-potenciar")))
    tg.append_submenu(_("Mi c_uenta"), tgc)
    tg.append_section(None, sec((_("Telegram _Drive (tu nube personal)…"), "win.tg-drive"),))
    tg.append_section(None, sec((_("Credenciales de la _API…"), "win.tg-credenciales"),
                                (_("Cerrar la _sesión y eliminar la cuenta…"), "win.tg-eliminar-cuenta")))
    barra.append_submenu(_("_Telegram"), tg)

    irc = Gio.Menu()
    irc.append_section(None, sec((_("_Conectar a la red"), "win.irc-conectar"),
                                 (_("_Desconectar de la red"), "win.irc-desconectar"),
                                 (_("Consola del _servidor…"), "win.irc-consola")))
    irc.append_section(None, sec((_("_Escribir en el chat"), "win.nuevo"),
                                 (_("_Responder mencionando"), "win.responder"),
                                 (_("_Unirse a un canal…"), "win.irc-unirse"),
                                 (_("Salir del _canal"), "win.irc-salir"),
                                 (_("Canales del s_ervidor…"), "win.irc-canales-servidor")))
    irc.append_section(None, sec((_("_Usuarios del canal…"), "win.irc-usuarios"),
                                 (_("Datos de un usuario (_whois)…"), "win.irc-whois"),
                                 (_("_Información del chat"), "win.irc-info-canal"),
                                 (_("Cambiar el _tema…"), "win.irc-tema"),
                                 (_("_Modos del canal…"), "win.irc-modos"),
                                 (_("_Baneos del canal…"), "win.irc-baneos")))
    irc.append_section(None, sec((_("_Nivel de aviso del canal…"), "win.irc-nivel-aviso"),
                                 (_("Mostrar u ocultar entradas y _salidas"), "win.irc-eventos"),
                                 (_("Marcar el chat como _leído"), "win.irc-leido"),
                                 (_("Marcar _todo como leído"), "win.marcar-todo-leido"),
                                 (_("E_xportar la conversación…"), "win.irc-exportar"),
                                 (_("Co_piar el texto"), "win.irc-copiar")))
    irc.append_section(None, sec((_("Marcarme como _ausente…"), "win.irc-ausente"),
                                 (_("Volver a estar d_isponible"), "win.irc-disponible"),
                                 (_("Cambiar de _apodo…"), "win.irc-nick"),
                                 (_("Datos de un apodo desconectado…"), "win.irc-whowas"),
                                 (_("Usuarios _ignorados…"), "win.irc-ignorados"),
                                 (_("A_migos (avisos de conexión)…"), "win.irc-amigos")))
    irc.append_section(None, sec((_("Editar la _red…"), "win.irc-editar-red"),
                                 (_("Eliminar la red…"), "win.irc-eliminar-red")))
    barra.append_submenu(_("_IRC"), irc)

    xm = Gio.Menu()
    xm.append_section(None, sec((_("_Conectar la cuenta"), "win.x-conectar"),
                                (_("_Desconectar la cuenta"), "win.x-desconectar")))
    xm.append_section(None, sec((_("_Escribir en el chat"), "win.nuevo"),
                                (_("_Responder al mensaje"), "win.responder"),
                                (_("Corregir mi _último mensaje"), "win.editar"),
                                (_("Re_tirar el mensaje o quitar el chat"), "win.papelera"),
                                (_("Re_accionar…"), "win.x-reaccionar"),
                                (_("Co_piar el texto"), "win.x-copiar"),
                                (_("Destacar el mensaje"), "win.favorito")))
    xm.append_section(None, sec((_("Enviar un a_rchivo…"), "win.x-enviar-archivo"),
                                (_("_Grabar y enviar una nota de voz"), "win.grabar-voz"),
                                (_("Cancelar la grabación de voz"), "win.cancelar-voz"),
                                (_("Re_producir el audio"), "win.reproducir"),
                                (_("_Descargar el adjunto"), "win.x-descargar"),
                                (_("_Abrir el adjunto"), "win.x-abrir-adjunto")))
    xm.append_section(None, sec((_("A_gregar un contacto…"), "win.x-agregar-contacto"),
                                (_("Aceptar la solicitud o abrir el chat"), "win.abrir-chat"),
                                (_("Rechazar la solicitud"), "win.x-rechazar"),
                                (_("Re_nombrar el contacto…"), "win.x-renombrar"),
                                (_("_Bloquear o desbloquear"), "win.x-bloquear"),
                                (_("_Quitar el contacto"), "win.x-quitar-contacto")))
    xm.append_section(None, sec((_("_Información del chat"), "win.perfil"),
                                (_("_Ficha (vCard)"), "win.x-vcard"),
                                (_("Información _técnica"), "win.x-info-tecnica"),
                                (_("Enviar un _zumbido"), "win.x-zumbido")))
    xm.append_section(None, sec((_("_Unirse a una sala…"), "win.unirse-canal"),
                                (_("Directorio de _salas…"), "win.x-directorio-salas"),
                                (_("_Ocupantes de la sala…"), "win.usuarios-canal"),
                                (_("Cambiar el _tema de la sala…"), "win.x-tema"),
                                (_("Salir del _grupo"), "win.x-salir-grupo"),
                                (_("Salir y olvidar el grupo"), "win.x-olvidar-grupo")))
    xm.append_section(None, sec((_("_Fijar o desfijar el chat"), "win.x-fijar"),
                                (_("Silenciar o reactivar el chat"), "win.x-silenciar"),
                                (_("Archi_var o desarchivar"), "win.x-archivar"),
                                (_("_Nivel de aviso…"), "win.x-nivel-aviso"),
                                (_("Marcar como _leído"), "win.x-leido"),
                                (_("Marcar como no leído"), "win.x-no-leido"),
                                (_("Marcar _todo como leído"), "win.marcar-todo-leido"),
                                (_("Cargar mensajes _anteriores"), "win.cargar-mas"),
                                (_("Vaciar la conversación…"), "win.x-vaciar"),
                                (_("E_xportar la conversación…"), "win.x-exportar")))
    xm.append_section(None, sec((_("Activar o quitar el cifrado _OMEMO"), "win.x-cifrado"),
                                (_("_Huellas de cifrado OMEMO…"), "win.x-huellas")))
    xm.append_section(None, sec((_("Comandos del _servidor…"), "win.x-comandos-servidor"),
                                (_("Comandos del chat o la pasarela enfocados…"), "win.x-comandos-de"),
                                (_("_Pasarelas (WhatsApp, Telegram…)…"), "win.x-pasarelas"),
                                (_("Buscar _usuarios en el servidor…"), "win.x-buscar-usuarios")))
    xm.append_section(None, sec((_("Editar mi _ficha (vCard)…"), "win.x-editar-ficha"),
                                (_("Cambiar la _contraseña…"), "win.x-cambiar-contrasena"),
                                (_("Crear una cuenta _nueva en un servidor…"), "win.x-crear-cuenta-nueva"),
                                (_("Borrar mi cuenta del _servidor…"), "win.x-cancelar-cuenta-servidor")))
    xm.append_section(None, sec((_("Mi _presencia…"), "win.x-presencia"),
                                (_("Mi estado de á_nimo…"), "win.x-animo"),
                                (_("Mi activi_dad…"), "win.x-actividad"),
                                (_("Ser invisible"), "win.x-invisible"),
                                (_("Volver a ser visible"), "win.x-visible"),
                                (_("Eliminar la cuenta de este equipo…"), "win.x-eliminar-cuenta")))
    barra.append_submenu(_("_XMPP"), xm)

    mx = Gio.Menu()
    mx.append_section(None, sec((_("_Conectar la cuenta"), "win.mx-conectar"),
                                (_("_Desconectar la cuenta"), "win.mx-desconectar")))
    mx.append_section(None, sec((_("_Escribir en la sala"), "win.nuevo"),
                                (_("_Responder al mensaje"), "win.responder"),
                                (_("Responder en un _hilo o ver el hilo…"), "win.mx-hilo"),
                                (_("_Editar mi mensaje"), "win.editar"),
                                (_("_Borrar el mensaje o salir de la sala"), "win.papelera"),
                                (_("Re_accionar…"), "win.mx-reaccionar"),
                                (_("Quitar mi reacción…"), "win.mx-quitar-reaccion"),
                                (_("¿_Quién ha leído este mensaje?"), "win.mx-quien-leyo"),
                                (_("Co_piar el texto"), "win.mx-copiar"),
                                (_("Fijar o desfijar el mensaje"), "win.mx-fijar-mensaje"),
                                (_("Abrir el enlace o la ubicación"), "win.abrir-chat")))
    mx.append_section(None, sec((_("Enviar un a_rchivo…"), "win.mx-enviar-archivo"),
                                (_("_Grabar y enviar una nota de voz"), "win.grabar-voz"),
                                (_("Cancelar la grabación de voz"), "win.cancelar-voz"),
                                (_("Crear una en_cuesta…"), "win.mx-crear-encuesta"),
                                (_("Votar en la encuesta…"), "win.votar"),
                                (_("Cerrar mi encuesta…"), "win.mx-cerrar-encuesta"),
                                (_("Compartir una _ubicación…"), "win.mx-enviar-ubicacion"),
                                (_("Re_producir el audio"), "win.reproducir"),
                                (_("_Descargar el adjunto"), "win.mx-descargar"),
                                (_("_Abrir el adjunto"), "win.mx-abrir-adjunto")))
    mx.append_section(None, sec((_("_Unirse a una sala…"), "win.unirse-canal"),
                                (_("C_rear una sala…"), "win.mx-crear-sala"),
                                (_("_Mensaje directo a un usuario…"), "win.mx-mensaje-directo"),
                                (_("Directorio de _salas…"), "win.mx-directorio"),
                                (_("Llamar a la puerta de una sala…"), "win.mx-llamar-puerta"),
                                (_("Crear un espa_cio…"), "win.mx-crear-espacio"),
                                (_("Aceptar la invitación o abrir la sala"), "win.abrir-chat"),
                                (_("Rechazar la invitación"), "win.mx-rechazar")))
    mx.append_section(None, sec((_("_Información de la sala"), "win.perfil"),
                                (_("Mi_embros y moderación…"), "win.usuarios-canal"),
                                (_("Hilos de la sala…"), "win.mx-hilos"),
                                (_("Mensajes fi_jados…"), "win.mx-fijados"),
                                (_("Aju_stes de la sala…"), "win.mx-ajustes"),
                                (_("Permitir llamar a la puerta"), "win.mx-permitir-llamar"),
                                (_("Cambiar el avatar de la sala…"), "win.mx-avatar-sala"),
                                (_("Añadir la sala a un espacio…"), "win.mx-a-espacio"),
                                (_("Ver las salas del espacio…"), "win.mx-ver-espacio"),
                                (_("Re_portar la sala…"), "win.mx-reportar-sala"),
                                (_("Dispositivos cifrados de la sala…"), "win.mx-dispositivos-sala")))
    mx.append_section(None, sec((_("_Nivel de aviso…"), "win.mx-nivel-aviso"),
                                (_("Etiqueta de la sala (favorita…)"), "win.mx-etiqueta"),
                                (_("Marcar como _leída"), "win.mx-leido"),
                                (_("Marcar como no leída"), "win.mx-no-leido"),
                                (_("Marcar _todo como leído"), "win.marcar-todo-leido"),
                                (_("Cargar mensajes _anteriores"), "win.cargar-mas"),
                                (_("Buscar en los mensajes guardados…"), "win.mx-buscar"),
                                (_("E_xportar la conversación…"), "win.mx-exportar"),
                                (_("Salir y olvidar la sala"), "win.mx-olvidar")))
    mx.append_section(None, sec((_("Mi _perfil y estado…"), "win.mx-perfil"),
                                (_("Mis _dispositivos…"), "win.mx-dispositivos"),
                                (_("Palabras que me avisan…"), "win.mx-palabras"),
                                (_("Configurar la verificación cru_zada…"), "win.mx-cross-signing"),
                                (_("E_xportar las claves de cifrado…"), "win.mx-exportar-claves"),
                                (_("_Importar claves de cifrado…"), "win.mx-importar-claves"),
                                (_("Volver a descifrar los mensajes pendientes"), "win.mx-redescifrar"),
                                (_("Eliminar la cuenta de este equipo…"), "win.mx-eliminar-cuenta")))
    if config.get("matrix", "admin_tools", False):
        adm = Gio.Menu()
        adm.append_section(None, sec((_("¿Soy administrador del servidor?"), "win.mx-admin-comprobar"),
                                     (_("_Usuarios del servidor…"), "win.mx-admin-usuarios"),
                                     (_("_Salas del servidor…"), "win.mx-admin-salas"),
                                     (_("Mensajes _reportados…"), "win.mx-admin-informes"),
                                     (_("Enviar un _aviso del servidor…"), "win.mx-admin-aviso"),
                                     (_("_Tokens de registro…"), "win.mx-admin-tokens"),
                                     (_("_Federación…"), "win.mx-admin-federacion"),
                                     (_("Mantenimiento de _archivos…"), "win.mx-admin-media"),
                                     (_("Actualizaciones en segundo plano"), "win.mx-admin-actualizaciones"),
                                     (_("_Estadísticas del servidor…"), "win.mx-admin-estadisticas")))
        mx.append_submenu(_("_Administración del servidor"), adm)
    barra.append_submenu(_("Ma_trix"), mx)

    rep = Gio.Menu()
    rep.append_section(None, sec((_("Reproducir el audio del artículo"), "win.reproducir-articulo"),
                                 (_("Reproducir / pausar"), "win.reproducir"),
                                 (_("Retroceder"), "win.retroceder"),
                                 (_("Avanzar"), "win.avanzar"),
                                 (_("Detener"), "win.detener")))
    rep.append_section(None, sec((_("Bajar volumen"), "win.volumen-menos"),
                                 (_("Subir volumen"), "win.volumen-mas"),
                                 (_("Bajar velocidad"), "win.velocidad-menos"),
                                 (_("Subir velocidad"), "win.velocidad-mas")))
    barra.append_submenu(_("_Reproductor"), rep)

    herr = Gio.Menu()
    herr.append_section(None, sec((_("_Traducir el elemento"), "win.traducir"),
                                  (_("_Resumir con IA"), "win.resumir-ia"),
                                  (_("_Describir la imagen con IA"), "win.describir-imagen"),
                                  (_("_Extraer el texto de la imagen (OCR con IA)"), "win.ocr-imagen")))
    herr.append_section(None, sec((_("Historial de _traducciones…"), "win.historial-traducciones"),
                                  (_("Historial de _IA…"), "win.historial-ia")))
    herr.append_section(None, sec((_("_Radio por internet…"), "win.herr-radio"),
                                  (_("Reproductor de radio, _podcast y YouTube…"), "win.herr-reproductor"),
                                  (_("Ver la _descripción de la multimedia"), "win.herr-ver-descripcion")))
    herr.append_section(None, sec((_("Buscar en _todo el contenido…"), "win.herr-buscar-global"),
                                  (_("_Borradores y programados de todas las redes…"), "win.herr-cola"),
                                  (_("Centro de _notificaciones (todas las redes)…"), "win.herr-centro-notificaciones"),
                                  (_("_Publicación cruzada (varias redes)…"), "win.herr-publicacion-cruzada"),
                                  (_("_Filtros de contenido…"), "win.herr-filtros"),
                                  (_("Fra_gmentos de texto…"), "win.herr-fragmentos")))
    herr.append_section(None, sec((_("_Copias de seguridad…"), "win.herr-copias"),
                                  (_("_Migrar a otro ordenador…"), "win.herr-migrar"),
                                  (_("_Borrar datos / liberar espacio…"), "win.herr-borrar")))
    herr.append_section(None, sec((_("_Ponte al día (resumen con IA)…"), "win.herr-ponte-al-dia"),
                                  (_("_Estadísticas de mis redes…"), "win.herr-estadisticas")))
    herr.append_section(None, sec((_("Mostrar u _ocultar buffers…"), "win.herr-buffers"),
                                  (_("_Editor de plantillas de lectura…"), "win.herr-plantillas"),
                                  (_("Editor de _atajos de teclado…"), "win.herr-atajos"),
                                  (_("C_omplementos…"), "win.herr-complementos"),
                                  (_("_Visor de registros…"), "win.herr-registro")))
    barra.append_submenu(_("_Herramientas"), herr)

    ver = Gio.Menu()
    ver.append_section(None, sec((_("Ir al árbol"), "win.ir-arbol"),
                                 (_("Ir a la lista de artículos"), "win.ir-lista"),
                                 (_("Ir al texto del artículo"), "win.ir-lectura"),
                                 (_("_Buscar"), "win.buscar")))
    ver.append_section(None, sec((_("Mostrar u ocultar la _bandeja de entrada"), "win.bandeja-alternar"),
                                 (_("_Añadir buffers a la bandeja…"), "win.bandeja-anadir"),
                                 (_("_Quitar buffers de la bandeja…"), "win.bandeja-quitar"),
                                 (_("Ir al buffer de _origen de la publicación"), "win.bandeja-origen")))
    ver.append_section(None, sec((_("Mover hacia a_rriba el elemento del árbol (Ctrl+Arriba en el árbol)"), "win.arbol-subir"),
                                 (_("Mover hacia a_bajo el elemento del árbol (Ctrl+Abajo en el árbol)"), "win.arbol-bajar")))
    barra.append_submenu(_("_Ver"), ver)

    ayuda = Gio.Menu()
    ayuda.append_section(None, sec((_("_Manual del usuario"), "win.ayuda-manual"),
                                   (_("_Teclas de la aplicación"), "app.teclas"),
                                   (_("_Documento completo de teclas"), "win.ayuda-teclas-documento"),
                                   (_("Último _changelog"), "win.ayuda-changelog"),
                                   (_("Prueba de _sonidos identificativos…"), "win.ayuda-sonidos")))
    ayuda.append_section(None, sec((_("_Buscar actualizaciones…"), "win.ayuda-actualizaciones"),
                                   (_("_Invítame a un café si te gusta mi trabajo"), "win.ayuda-cafe"),
                                   (_("_Acerca de"), "app.acerca")))
    barra.append_submenu(_("A_yuda"), ayuda)
    return barra


class Aplicacion(Gtk.Application):
    def __init__(self, app_id: str = APP_ID):
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.ventana = None
        self.account_id = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        plataforma_gtk.registrar(self)
        self.account_id = datos.cuenta_rss()

        def accion(nombre, fn, accels=()):
            a = Gio.SimpleAction.new(nombre, None)
            a.connect("activate", lambda *_a: fn())
            self.add_action(a)
            if accels:
                self.set_accels_for_action(f"app.{nombre}", list(accels))
        accion("anadir-feed", self.anadir_feed, ["<Ctrl><Alt>n"])
        accion("anadir-cuenta-mastodon", self.anadir_cuenta_mastodon)
        accion("anadir-cuenta-bluesky", self.anadir_cuenta_bluesky)
        accion("anadir-cuenta-wsocial", self.anadir_cuenta_wsocial)
        accion("anadir-cuenta-telegram", self.anadir_cuenta_telegram)
        accion("anadir-red-irc", self.anadir_red_irc)
        accion("anadir-cuenta-xmpp", self.anadir_cuenta_xmpp)
        accion("anadir-cuenta-matrix", self.anadir_cuenta_matrix)
        accion("anadir-cuenta-x", self.anadir_cuenta_x)
        accion("anadir-cuenta-reddit", self.anadir_cuenta_reddit)
        accion("anadir-cuenta-peertube", self.anadir_cuenta_peertube)
        accion("importar-opml", self.importar_opml, ["<Ctrl>o"])
        accion("exportar-opml", self.exportar_opml)
        accion("preferencias", lambda: DialogoPreferencias(self.ventana).present(), ["<Ctrl>p"])
        accion("teclas", lambda: DialogoTeclas(self.ventana).present())
        accion("acerca", self.acerca)
        accion("salir", self.quit, ["<Ctrl>q"])
        self.set_menubar(_menu())

    def do_activate(self):
        if self.ventana is None:
            self.ventana = VentanaPrincipal(self, self.account_id)
            self.ventana.set_show_menubar(True)
        self.ventana.present()
        # Foco en la lista de artículos: es lo primero que se quiere leer.
        self.ventana.vista_lista.grab_focus()
        notificar_listo()

    def do_shutdown(self):
        try:
            from app.core.task_manager import task_manager
            task_manager.shutdown() if hasattr(task_manager, "shutdown") else None
        except Exception:
            pass
        Gtk.Application.do_shutdown(self)

    # --- acciones de aplicación ---
    def anadir_feed(self):
        DialogoAnadirFeed(self.ventana, self.account_id, self._tras_cambiar_feeds).present()

    def _tras_cambiar_feeds(self):
        self.ventana.cargar_arbol()

    def importar_opml(self):
        self.ventana.rss_importar_opml()

    def exportar_opml(self):
        dlg = Gtk.FileDialog(title=_("Exportar OPML"), initial_name="socialrss.opml")

        def _fin(origen, res):
            try:
                f = origen.save_finish(res)
            except GLib.Error:
                return
            try:
                rss_service.exportar_feeds_opml(self.account_id, f.get_path())
                notificar(_("Feeds exportados"))
            except Exception as e:
                notificar(_("Error al exportar: {e}").format(e=e))
        dlg.save(self.ventana, None, _fin)

    def anadir_cuenta_mastodon(self):
        from .dialogos import DialogoCuentaMastodon

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("mastodon", account_id, "timeline", None, None, "home"))
        DialogoCuentaMastodon(self.ventana, _hecha).present()

    def anadir_cuenta_bluesky(self):
        from .dialogos_bluesky import DialogoCuentaBluesky

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("bluesky", account_id, "timeline", None, None, "home"))
            self.ventana.iniciar_tiempo_real_bluesky()
        DialogoCuentaBluesky(self.ventana, _hecha).present()

    def anadir_cuenta_wsocial(self):
        from .dialogos_wsocial import DialogoCuentaWSocial

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("wsocial", account_id, "timeline", None, None, "home"))
            self.ventana.iniciar_tiempo_real_wsocial()
        DialogoCuentaWSocial(self.ventana, _hecha).present()

    def anadir_cuenta_telegram(self):
        from .dialogos_telegram import DialogoCuentaTelegram

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("telegram", account_id, "timeline", None, None, "chats"))
            self.ventana.iniciar_tiempo_real_telegram()
        DialogoCuentaTelegram(self.ventana, _hecha).present()

    def anadir_cuenta_xmpp(self):
        from .dialogos_xmpp import DialogoCuentaXMPP

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("xmpp", account_id, "timeline", None, None, "conversaciones"))
            self.ventana.iniciar_tiempo_real_xmpp()
        DialogoCuentaXMPP(self.ventana, _hecha).present()

    def anadir_cuenta_matrix(self):
        from .dialogos_matrix import DialogoCuentaMatrix

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("matrix", account_id, "timeline", None, None, "rooms"))
            self.ventana.iniciar_tiempo_real_matrix()
        DialogoCuentaMatrix(self.ventana, _hecha).present()

    def anadir_cuenta_x(self):
        if not datos_x.disponible():
            notificar(_("El cliente de X no está disponible: falta la librería twikit o está desactivado"))
            return
        from .dialogos_x import DialogoCuentaX

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("x", account_id, "timeline", None, None, "home"))
        DialogoCuentaX(self.ventana, _hecha).present()

    def anadir_cuenta_reddit(self):
        if not datos_reddit.disponible():
            notificar(_("Reddit no está disponible: falta la librería praw o está desactivado"))
            return
        from .dialogos_reddit import DialogoCuentaReddit

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("reddit", account_id, "timeline", None, None, "home"))
        DialogoCuentaReddit(self.ventana, _hecha).present()

    def anadir_cuenta_peertube(self):
        if not datos_peertube.disponible():
            notificar(_("PeerTube no está disponible: falta la librería httpx o está desactivado"))
            return
        from .dialogos_peertube import DialogoCuentaPeerTube

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("peertube", account_id, "timeline", None, None, "recientes"))
        DialogoCuentaPeerTube(self.ventana, _hecha).present()

    def anadir_red_irc(self):
        from .dialogos_irc import DialogoRedIRC

        def _hecha(account_id):
            self.ventana.cargar_arbol(seleccionar=("irc", account_id, "timeline", None, None, "channels"))
            self.ventana.iniciar_tiempo_real_irc()
        DialogoRedIRC(self.ventana, None, _hecha).present()

    def acerca(self):
        dlg = Gtk.AboutDialog(transient_for=self.ventana, modal=True, program_name="SocialRSS Accessible Suite",
                              version=APP_VERSION, license_type=Gtk.License.GPL_3_0,
                              comments=_("Redes sociales, chats y noticias en una sola ventana, "
                                         "accesible con el teclado y con Orca."),
                              website="https://github.com/hxebolax/SocialRSS")
        dlg.present()


def notificar_listo():
    from app.core.config_manager import config
    if config.get("general", "ready_message", True):
        from app.core.speech import speak
        speak(_("SocialRSS listo."), interrupt=False)
