# -*- coding: utf-8 -*-
"""
Diálogo para mostrar u ocultar los buffers de cada red en el árbol de navegación
(inspirado en el panel "Buffers" de TWBlue). Reordenar se sigue haciendo con
Control más Flecha sobre el propio árbol; aquí solo se elige la visibilidad.

Los buffers ocultos se guardan por servicio en config ("hidden_buffers") y el árbol
los omite al construirse. Los buffers de cuentas/listas/feeds dinámicos no se listan
aquí: solo los buffers fijos de cada red.
"""

import wx
import logging
import builtins
from app.core.config_manager import config
from .accessible_controls import AccessibleCheckListBox

log = logging.getLogger(__name__)

# En condiciones normales el diálogo se importa después de inicializar gettext, que
# instala "_" en builtins. Por si se importa antes (p. ej. en pruebas), aseguramos un
# "_" identidad para que las etiquetas a nivel de módulo no fallen.
if not hasattr(builtins, "_"):
    _ = lambda s: s

# Etiquetas y orden canónico de los buffers fijos de cada red (deben coincidir con
# los usados al construir el árbol en main_frame.py y bluesky_views.py).
_BUFFERS = {
    "mastodon": [
        ("home", _("Inicio")), ("notifications", _("Notificaciones")),
        ("mentions", _("Menciones")),
        ("bookmarks", _("Marcadores")), ("favourites", _("Favoritos")),
        ("sent", _("Enviados")), ("direct", _("Mensajes Directos")),
        ("local", _("Local")), ("public", _("Federada")),
        ("announcements", _("Anuncios del servidor")), ("trending", _("Tendencias")),
        ("followers", _("Seguidores")), ("following", _("Siguiendo")),
        ("blocked", _("Bloqueados")), ("muted", _("Silenciados")),
        ("user_timelines", _("Cronologías de usuarios")),
        ("thread_buffers", _("Conversaciones")),
    ],
    "bluesky": [
        ("home", _("Inicio")), ("notifications", _("Notificaciones")),
        ("mentions", _("Menciones")), ("sent", _("Enviados")),
        ("likes", _("Me gusta")), ("bookmarks", _("Marcadores")),
        ("feeds", _("Feeds personalizados")), ("lists", _("Listas")),
        ("starterpacks", _("Starter packs")), ("trending", _("Tendencias")),
        ("followers", _("Seguidores")), ("follows", _("Siguiendo")),
        ("blocks", _("Bloqueados")), ("mutes", _("Silenciados")),
        ("convos", _("Mensajes directos")),
        # Grupos de buffers DINÁMICOS (creados por ti): desmarcar oculta todo el grupo del árbol.
        # Para quitar uno concreto, usa su menú contextual en el árbol (Eliminar / Borrar).
        ("searches", _("Búsquedas guardadas")),
        ("user_timelines", _("Cronologías de cuentas")),
        ("thread_buffers", _("Conversaciones")),
    ],
    "wsocial": [
        ("home", _("Inicio")), ("notifications", _("Notificaciones")),
        ("mentions", _("Menciones")), ("sent", _("Enviados")),
        ("likes", _("Me gusta")), ("bookmarks", _("Marcadores")),
        ("drafts", _("Borradores")), ("feeds", _("Feeds")), ("lists", _("Listas")),
        ("starterpacks", _("Paquetes de inicio")), ("trending", _("Tendencias")),
        ("followers", _("Seguidores")), ("follows", _("Siguiendo")),
        ("blocks", _("Bloqueados")), ("mutes", _("Silenciados")),
        ("convos", _("Mensajes directos")), ("requests", _("Solicitudes de mensaje")),
        # Grupos de buffers dinámicos: desmarcar oculta todo el grupo del árbol.
        ("searches", _("Búsquedas guardadas")),
        ("user_timelines", _("Cronologías de cuentas")),
        ("thread_buffers", _("Conversaciones")),
    ],
    "rss": [
        ("all", _("Todos los artículos")), ("unread", _("No leídos")),
        ("favorites", _("Destacados")), ("read_later", _("Leer más tarde")),
        ("today", _("Hoy")),
    ],
    "telegram": [
        ("all", _("Todos los chats")), ("unread", _("No leídos")),
        ("private", _("Usuarios")), ("bot", _("Bots")), ("group", _("Grupos")),
        ("supergroup", _("Supergrupos")), ("channel", _("Canales")),
        ("contacts", _("Contactos")),
    ],
    "x": [
        ("home", _("Inicio (Para ti)")), ("following", _("Siguiendo")),
        ("mentions", _("Menciones")), ("notifications", _("Notificaciones")),
        ("bookmarks", _("Marcadores")), ("followers", _("Seguidores")),
        ("verified_followers", _("Seguidores verificados")),
        ("friends", _("Cuentas que sigo")), ("lists", _("Listas")),
        ("communities", _("Comunidades")), ("scheduled", _("Programados")),
        # Grupos de buffers DINÁMICOS (creados por ti): desmarcar oculta todo el grupo del árbol.
        # Para quitar uno concreto, usa su menú contextual en el árbol (Eliminar / Borrar).
        ("searches", _("Búsquedas guardadas")),
        ("user_timelines", _("Cronologías de usuarios")),
        ("thread_buffers", _("Conversaciones abiertas como buffer")),
        ("community_buffers", _("Comunidades abiertas como buffer")),
    ],
    "irc": [
        ("channels", _("Canales")), ("privates", _("Privados")),
        ("server", _("Avisos del servidor")),
    ],
    "matrix": [
        ("rooms", _("Salas")), ("dms", _("Mensajes directos")), ("invites", _("Invitaciones")),
    ],
    "xmpp": [
        ("conversaciones", _("Conversaciones")), ("contactos", _("Contactos")),
        ("grupos", _("Grupos")), ("solicitudes", _("Solicitudes")),
        ("archivados", _("Archivados")), ("avisos", _("Avisos del servidor")),
    ],
    # Reddit: las etiquetas deben coincidir con BUFFER_LABELS de reddit_views.py.
    "reddit": [
        ("home", _("Página principal")), ("popular", _("Populares")), ("all", _("Todo (r/all)")),
        ("inbox", _("Bandeja de entrada")), ("unread", _("Mensajes sin leer")),
        ("mentions", _("Menciones")), ("sent", _("Mensajes enviados")),
        ("submitted", _("Mis publicaciones")), ("comments", _("Mis comentarios")),
        ("saved", _("Guardados")), ("upvoted", _("Votados positivos")),
        ("downvoted", _("Votados negativos")), ("hidden", _("Ocultos")),
        ("subreddits", _("Mis subreddits")), ("multireddits", _("Multireddits")),
        # Grupo de buffers DINÁMICOS (creados por ti): desmarcar oculta todo el grupo del árbol.
        # Para quitar una concreta, usa su menú contextual en el árbol.
        ("searches", _("Búsquedas guardadas")),
    ],
    # PeerTube: las etiquetas deben coincidir con SECTION_LABELS de peertube_views.py.
    "peertube": [
        ("recientes", _("Vídeos recientes")), ("populares", _("Populares")),
        ("tendencias", _("Tendencias")), ("locales", _("Vídeos locales")),
        ("federados", _("Vídeos federados")), ("suscripciones", _("Suscripciones")),
        ("directos", _("Directos")), ("notificaciones", _("Notificaciones")),
        # Ramas que AGRUPAN sub-secciones: desmarcarlas oculta la rama entera del árbol.
        ("biblioteca", _("Mi biblioteca (rama)")),
        ("busquedas", _("Búsquedas guardadas (rama)")),
        # Sub-secciones de "Mi biblioteca" (se pueden ocultar una a una).
        ("mis_videos", _("Mis vídeos")), ("mis_canales", _("Mis canales")),
        ("listas", _("Listas de reproducción")), ("ver_mas_tarde", _("Ver más tarde")),
        ("me_gusta", _("Me gusta")), ("historial", _("Historial")),
    ],
}

# Clave de configuración donde cada red guarda sus buffers ocultos. PeerTube llama "secciones" a sus
# buffers (y su orden va en "section_order"), así que usa "hidden_sections".
_CLAVE_OCULTOS = {"peertube": "hidden_sections"}


def _clave_ocultos(servicio):
    return _CLAVE_OCULTOS.get(servicio, "hidden_buffers")


_SERVICIOS = [
    ("mastodon", _("Mastodon")),
    ("bluesky", _("Bluesky")),
    ("rss", _("RSS")),
    ("telegram", _("Telegram")),
    ("x", _("X (Twitter)")),
    ("irc", _("IRC")),
    ("matrix", _("Matrix")),
    ("xmpp", _("XMPP")),
    ("reddit", _("Reddit")),
    ("peertube", _("PeerTube")),
    ("wsocial", _("W Social")),
]


def servicios_disponibles():
    """Redes que se ofrecen en el diálogo: SOLO las ACTIVAS. Una red desactivada por el usuario —o
    cuya librería no está instalada (Reddit sin praw, IRC sin pydle...)— no aparece aquí, igual que
    no aparece en el árbol ni en los menús. `servicio_habilitado` es el punto único de decisión (X
    lleva dentro su propia lógica de kill-switch + librería + preferencia)."""
    try:
        from app.core.feature_flags import servicio_habilitado
        return [(s, l) for s, l in _SERVICIOS if servicio_habilitado(s)]
    except Exception as e:
        # No debería ocurrir; si pasa, es mejor ofrecerlas todas que dejar el diálogo vacío.
        log.debug("No se pudo filtrar las redes activas para el diálogo de buffers: %s", e)
        return list(_SERVICIOS)


class GestionBuffersDialog(wx.Dialog):
    """Permite marcar qué buffers se muestran en el árbol de cada red."""

    def __init__(self, parent):
        super().__init__(parent, title=_("Mostrar u ocultar buffers"),
                         style=wx.DEFAULT_DIALOG_STYLE, name=_("Mostrar u ocultar buffers"))
        self._servicios = servicios_disponibles()
        if not self._servicios:
            self._servicios = [("mastodon", _("Mastodon"))]
        # Estado de trabajo: {servicio: set(tipos_ocultos)}; se carga de config y se
        # va actualizando al cambiar de servicio o de casillas, sin escribir hasta Aceptar.
        self._ocultos = {srv: set(config.get(srv, _clave_ocultos(srv), []))
                         for srv, _l in self._servicios}
        self._srv_actual = self._servicios[0][0]

        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label=_("&Red:")), 0, wx.LEFT | wx.TOP, 8)
        self.choice_srv = wx.Choice(self, choices=[l for _s, l in self._servicios], name=_("Red"))
        self.choice_srv.SetSelection(0)
        self.choice_srv.Bind(wx.EVT_CHOICE, self._on_cambiar_servicio)
        sizer.Add(self.choice_srv, 0, wx.EXPAND | wx.ALL, 8)

        sizer.Add(wx.StaticText(self, label=_("&Buffers visibles (marca para mostrar, desmarca para ocultar):")), 0, wx.LEFT | wx.TOP, 8)
        self.lista = AccessibleCheckListBox(self, name=_("Buffers visibles"))
        sizer.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)

        btns = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_ok = wx.Button(self, wx.ID_OK, _("&Aceptar"))
        self.btn_ok.SetDefault()
        self.btn_cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"))
        btns.Add(self.btn_ok, 0, wx.ALL, 5)
        btns.Add(self.btn_cancel, 0, wx.ALL, 5)
        sizer.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        self.SetSizerAndFit(sizer)
        self.SetSize((420, 460))
        self.CenterOnParent()
        self._cargar_lista(self._srv_actual)
        self.choice_srv.SetFocus()

    def _buffers(self, servicio):
        """Buffers del servicio: los fijos + (en XMPP) un ítem por cada PUENTE detectado, para poder
        ocultar una pasarela entera (WhatsApp, Discord…). El tipo de un puente es 'puente:<dominio>'."""
        base = list(_BUFFERS.get(servicio, []))
        if servicio == "xmpp":
            try:
                from app.core.database import db
                from app.services.xmpp.xmpp_service import xmpp_service
                vistos = set()
                for (acc_id,) in db.query("SELECT id FROM accounts WHERE service_id='xmpp' AND active=1") or []:
                    for p in xmpp_service.puentes(acc_id):
                        clave = "puente:" + p["dominio"]
                        if clave not in vistos:
                            vistos.add(clave)
                            base.append((clave, _("Puente: {0}").format(p["nombre"])))
            except Exception:
                pass
        return base

    def _cargar_lista(self, servicio):
        """Rellena la lista de casillas con los buffers del servicio dado."""
        self._srv_actual = servicio
        self.lista.Clear()
        buffers = self._buffers(servicio)
        self.lista.AppendItems([lbl for _t, lbl in buffers])
        ocultos = self._ocultos.get(servicio, set())
        for i, (b_type, _lbl) in enumerate(buffers):
            self.lista.Check(i, b_type not in ocultos)

    def _guardar_estado_actual(self):
        """Lee las casillas y actualiza el conjunto de ocultos del servicio actual."""
        ocultos = set()
        for i, (b_type, _lbl) in enumerate(self._buffers(self._srv_actual)):
            if not self.lista.IsChecked(i):
                ocultos.add(b_type)
        self._ocultos[self._srv_actual] = ocultos

    def _on_cambiar_servicio(self, event):
        self._guardar_estado_actual()
        sel = self.choice_srv.GetSelection()
        if 0 <= sel < len(self._servicios):
            self._cargar_lista(self._servicios[sel][0])

    def aplicar(self):
        """Guarda en config los buffers ocultos de cada servicio. Devuelve True si
        algo cambió respecto a lo que había guardado."""
        self._guardar_estado_actual()
        cambio = False
        for srv, _l in self._servicios:
            clave = _clave_ocultos(srv)
            previo = list(config.get(srv, clave, []))
            nuevo = [b for b, _lbl in self._buffers(srv) if b in self._ocultos.get(srv, set())]
            if set(previo) != set(nuevo):
                config.set(srv, clave, nuevo)
                cambio = True
        return cambio
