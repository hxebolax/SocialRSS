# -*- coding: utf-8 -*-
"""MainFrame (mixin): publicacion.
Metodos: _on_votar_encuesta, _on_marcar_dm_leido, _on_timeline_autor, _on_crear_cronologia_autor, _elegir_usuario_de_toot, _abrir_timeline_acct, _autor_de_toot, _on_seguidores_conocidos ...
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s
# -*- coding: utf-8 -*-
"""
Ventana principal de la aplicación SocialRSS Accessible Suite.
Layout dividido en navegación vertical de servicios, lista de toots/artículos y panel de lectura.
Todos los procesos de sincronización y red ocurren de forma asíncrona mediante el task_manager.
"""

import logging
import wx
from app.core.privacy import abrir_url

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar

from app.services.mastodon.mastodon_service import mastodon_service


log = logging.getLogger(__name__)
from .ui_helpers import formatear_tiempo_relativo


class _Mf_publicacion:
    def _on_votar_encuesta(self, db_id: str):
        """Detecta la encuesta del toot y permite votar."""
        if not self.active_mastodon_account_id:
            return
        speak(_("Buscando encuesta..."), interrupt=True)

        def _con_encuesta(poll, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not poll:
                speak(_("Esta publicación no tiene una encuesta."), interrupt=True)
                return
            if poll.get("expired"):
                speak(_("La encuesta ha finalizado."), interrupt=True)
                return
            if poll.get("voted"):
                speak(_("Ya has votado en esta encuesta."), interrupt=True)
                return
            opciones = poll.get("options", [])
            if not opciones:
                return
            if poll.get("multiple"):
                dlg = wx.MultiChoiceDialog(self, _("Elige una o varias opciones:"), _("Votar en la encuesta"), opciones)
                if dlg.ShowModal() == wx.ID_OK:
                    elegidas = dlg.GetSelections()
                else:
                    elegidas = None
                dlg.Destroy()
            else:
                dlg = wx.SingleChoiceDialog(self, _("Elige una opción:"), _("Votar en la encuesta"), opciones)
                if dlg.ShowModal() == wx.ID_OK:
                    elegidas = [dlg.GetSelection()]
                else:
                    elegidas = None
                dlg.Destroy()
            if not elegidas:
                return

            def _votado(res, err2):
                if err2:
                    notificar(_("No se pudo votar: {0}").format(err2))
                else:
                    speak(_("Voto emitido. Gracias por participar."), interrupt=True)

            mastodon_service.votar_encuesta_async(self.active_mastodon_account_id, poll.get("id"), list(elegidas), callback=_votado)

        mastodon_service.obtener_encuesta_async(self.active_mastodon_account_id, db_id, callback=_con_encuesta)

    def _on_marcar_dm_leido(self, db_id):
        """Marca como leída la conversación de mensajes directos a la que pertenece el toot."""
        if not self.active_mastodon_account_id:
            return
        row = db.query_one("SELECT conversation_id FROM mastodon_statuses WHERE id = ?", (db_id,))
        conv_id = row[0] if row and row[0] else None
        if not conv_id:
            speak(_("Esta conversación no se puede marcar como leída."), interrupt=True)
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo marcar como leída: {0}").format(err))
            else:
                speak(_("Conversación marcada como leída."), interrupt=True)
        mastodon_service.marcar_conversacion_leida_async(self.active_mastodon_account_id, conv_id, callback=_fin)

    def _on_timeline_autor(self, db_id):
        """Muestra las publicaciones de un usuario del toot en una lista interactiva. Si el toot
        involucra a varias cuentas (sin contarte a ti), deja ELEGIR de cuál."""
        acct, _display = self._elegir_usuario_de_toot(db_id, _("Ver publicaciones del usuario"))
        if acct:
            self._abrir_timeline_acct(acct)

    def _on_crear_cronologia_autor(self, db_id):
        """Crea un buffer permanente con la cronología de un usuario del toot enfocado. Si el
        toot involucra a varias cuentas (autor + mencionados, sin contarte a ti), deja ELEGIR
        de cuál; si solo hay una, la usa directamente."""
        acct, display = self._elegir_usuario_de_toot(db_id, _("Crear cronología de usuario"))
        if acct:
            self._anadir_cronologia_usuario(acct, display)

    def _elegir_usuario_de_toot(self, db_id, titulo_dialogo):
        """Devuelve (acct, display) del usuario elegido de un toot. Si el toot involucra a varias
        cuentas (quien lo impulsó, el autor, los mencionados y el autor del toot citado, sin
        contarte a ti), deja ELEGIR de cuál; si solo hay una, la usa directamente. Devuelve
        (None, None) si se cancela o no hay ninguna."""
        if not self.active_mastodon_account_id:
            return (None, None)
        row = db.query_one(
            "SELECT author_username, author_name FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            notificar(_("No se encontró el autor."))
            return (None, None)
        author_username, author_name = row
        candidatos = self._usuarios_de_toot_mastodon(db_id)
        if not candidatos:
            # Solo intervienes tú (por ejemplo, un toot tuyo sin menciones).
            candidatos = [author_username.lstrip("@")]
        if len(candidatos) == 1:
            elegido = candidatos[0]
        else:
            sel = wx.GetSingleChoice(_("¿De qué usuario?"), titulo_dialogo,
                                     ["@" + a for a in candidatos], self)
            if not sel:
                return (None, None)
            elegido = sel.lstrip("@")
        display = author_name if elegido == author_username.lstrip("@") else elegido
        return (elegido, display)

    def _usuarios_de_publicacion_enfocada(self, db_id=None):
        """TODAS las cuentas que intervienen en una publicación (la enfocada, o la de `db_id` si
        se indica: los menús contextuales la pasan para no depender de dónde esté el foco).

        Se incluyen, por este orden: quien la impulsó o reposteó, el autor, el autor de la
        publicación CITADA (si cita a otra) y las cuentas mencionadas. Devuelve acct/handle SIN
        arroba, sin repetidos y SIEMPRE sin tu propia cuenta (no tiene sentido ofrecerte abrir tu
        cronología desde aquí; para eso está tu buffer de publicaciones enviadas).

        Es lo que permite abrir la cronología de alguien a quien SOLO conoces porque aparece
        mencionado o citado en una publicación, sin saberte su nombre de cuenta. Lista vacía si no
        hay nada enfocado o la red no lo soporta."""
        # Se lee la lista directamente (y no _bluesky_db_id_enfocado) porque aquí NO hay que
        # protestar si no hay nada enfocado: simplemente no habrá candidatos que ofrecer.
        if not db_id:
            try:
                idx = self.lista_items.GetFocusedItem()
                db_id = self.lista_items_db_ids[idx] if 0 <= idx < len(self.lista_items_db_ids) else None
            except Exception:
                db_id = None
        if not db_id:
            return []
        if self.active_mastodon_account_id:
            return self._usuarios_de_toot_mastodon(db_id)
        if self.active_bluesky_account_id:
            row = db.query_one(
                "SELECT COALESCE(author_handle,''), COALESCE(content,''), COALESCE(quote_uri,''), "
                "COALESCE(repost_by_handle,'') FROM bluesky_posts WHERE id = ?", (db_id,))
            if not row:
                return []
            citado = ""
            if row[2]:
                q = db.query_one(
                    "SELECT COALESCE(author_handle,'') FROM bluesky_posts WHERE account_id = ? AND uri = ?",
                    (self.active_bluesky_account_id, row[2]))
                citado = q[0] if q else ""
            return self._menciones_del_texto(row[1], previos=[row[3], row[0]], posteriores=[citado],
                                             yo=self._cuenta_propia_acct("bluesky"))
        if self.active_x_account_id:
            row = db.query_one(
                "SELECT COALESCE(author_screen_name,''), COALESCE(text,''), COALESCE(quoted_id,''), "
                "COALESCE(retweeted_by,'') FROM x_tweets WHERE id = ?", (db_id,))
            if not row:
                return []
            citado = ""
            if row[2]:
                q = db.query_one(
                    "SELECT COALESCE(author_screen_name,'') FROM x_tweets WHERE account_id = ? AND tweet_id = ?",
                    (self.active_x_account_id, row[2]))
                citado = q[0] if q else ""
            return self._menciones_del_texto(row[1], previos=[row[3], row[0]], posteriores=[citado],
                                             yo=self._cuenta_propia_acct("x"))
        return []

    def _usuarios_de_toot_mastodon(self, db_id):
        """Cuentas que intervienen en un toot: quien lo impulsó, el autor, los mencionados y el
        autor del toot CITADO, sin tu cuenta y sin repetidos. Lista vacía si el toot no existe."""
        row = db.query_one(
            "SELECT author_username, COALESCE(mentions_json, ''), COALESCE(reblog_by_username, ''), "
            "COALESCE(quote_json, '') FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            return []
        # _construir_menciones_respuesta ya ordena (impulsor, autor, mencionados) y quita tu
        # cuenta; solo falta añadir al autor del toot citado, que no está en mentions_json.
        candidatos = [c.lstrip("@") for c in
                      self._construir_menciones_respuesta(row[0], row[1], reblog_by_username=row[2])]
        citado = self._acct_citado_mastodon(row[3])
        if citado:
            # Se pasa por el mismo filtro que las menciones (compara también el dominio), así el
            # citado tampoco aparece si eres tú quien escribió el toot citado.
            candidatos += [c.lstrip("@") for c in self._construir_menciones_respuesta(citado, "")]
        return self._sin_repetidos(candidatos)

    @staticmethod
    def _acct_citado_mastodon(quote_json):
        """acct del autor del toot CITADO (columna quote_json), o "" si el toot no cita a nadie."""
        if not quote_json:
            return ""
        import json as _json
        try:
            return (_json.loads(quote_json) or {}).get("acct", "").lstrip("@")
        except Exception:
            return ""

    def _cuenta_propia_acct(self, red):
        """Tu handle en la cuenta ACTIVA de esa red (sin arroba), tal como se guardó al iniciar
        sesión: en Bluesky y X, accounts.name es '@handle'. Cadena vacía si no se puede saber."""
        account_id = (self.active_bluesky_account_id if red == "bluesky" else self.active_x_account_id)
        if not account_id:
            return ""
        fila = db.query_one("SELECT name FROM accounts WHERE id = ?", (account_id,))
        return (fila[0] or "").strip().lstrip("@") if fila else ""

    @staticmethod
    def _sin_repetidos(accts):
        """Misma lista, en orden, sin vacíos ni repetidos (comparando sin distinguir mayúsculas)."""
        vistos, salida = set(), []
        for a in accts:
            clave = (a or "").strip().lstrip("@").lower()
            if clave and clave not in vistos:
                vistos.add(clave)
                salida.append(a.strip().lstrip("@"))
        return salida

    def _elegir_usuario_mencionado(self, titulo, db_id=None):
        """Deja elegir una de las cuentas que intervienen en la publicación (impulsor, autor,
        citado y mencionados; nunca la tuya). Si solo hay una, la usa sin preguntar. Devuelve el
        acct/handle sin arroba, o "" si no hay ninguna o se cancela."""
        candidatos = self._usuarios_de_publicacion_enfocada(db_id)
        if not candidatos:
            # Mensaje neutro: este selector lo usan varias acciones (cronología, perfil, seguir).
            notificar(_("En esta publicación no aparece ninguna otra cuenta."))
            return ""
        if len(candidatos) == 1:
            return candidatos[0]
        sel = wx.GetSingleChoice(_("¿De qué usuario?"), titulo, ["@" + c for c in candidatos], self)
        return sel.lstrip("@") if sel else ""

    @classmethod
    def _menciones_del_texto(cls, texto, previos=(), posteriores=(), yo=""):
        """Cuentas de un post de Bluesky o X: las de `previos` (impulsor y autor), después las
        mencionadas en el TEXTO y por último las de `posteriores` (el autor del post citado).

        Bluesky y X no guardan la lista de menciones aparte (Mastodon sí, en mentions_json), así
        que se sacan del propio texto. El patrón admite puntos y guiones porque los handles de
        Bluesky son dominios (nombre.bsky.social). `yo` es tu propia cuenta, que se excluye."""
        import re
        del_texto = re.findall(r"@([A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_])?)", texto or "")
        todos = list(previos) + del_texto + list(posteriores)
        # Un handle al final de una frase puede arrastrar el punto: @juan.bsky.social. -> se quita.
        todos = [(c or "").strip().lstrip("@").rstrip(".") for c in todos]
        propia = (yo or "").strip().lstrip("@").lower()
        return cls._sin_repetidos([c for c in todos if c.lower() != propia])

    def _abrir_timeline_acct(self, acct):
        """Abre las publicaciones de un usuario (por su acct) en una LISTA navegable por columnas
        donde se puede interactuar con cada publicación como en el timeline. Reutilizado por
        'Ver publicaciones del autor' y por 'Abrir cronología de un usuario...'."""
        if not self.active_mastodon_account_id:
            return
        from .mastodon_views import PublicacionesUsuarioDialog
        dlg = PublicacionesUsuarioDialog(self, self.active_mastodon_account_id, (acct or "").lstrip("@"))
        dlg.ShowModal()
        dlg.Destroy()

    def _autor_de_toot(self, db_id):
        """Devuelve el acct del autor de un toot (resolviendo el del impulsado si es boost)."""
        row = db.query_one(
            "SELECT COALESCE(NULLIF(reblog_by_username,''), author_username) "
            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        return (row[0] if row else None)

    def _on_seguidores_conocidos(self, db_id):
        """'Seguido por X a quien tú sigues': cuentas que sigues y que siguen al autor."""
        acct = self._autor_de_toot(db_id)
        if not acct:
            notificar(_("No se encontró el autor."))
            return
        self._mostrar_seguidores_conocidos(acct)

    def _mostrar_seguidores_conocidos(self, acct):
        """Núcleo por acct: muestra quién, de la gente que tú sigues, sigue también a `acct`."""
        if not self.active_mastodon_account_id or not acct:
            return
        acct = acct.lstrip("@")
        speak(_("Buscando seguidores conocidos..."), interrupt=True)

        def _fin(res, err):
            if err:
                speak(_("No se pudieron obtener: {0}").format(err), interrupt=True)
                return
            if not res:
                self._mostrar_texto_largo(_("Seguidores conocidos"),
                                          _("Nadie a quien sigues sigue a @{0}.").format(acct))
                return
            txt = _("De los que tú sigues, también siguen a @{0}:").format(acct) + "\n\n"
            txt += "\n".join("@" + n for n in res if n)
            self._mostrar_texto_largo(_("Seguidores conocidos"), txt)
        mastodon_service.seguidores_conocidos_async(self.active_mastodon_account_id, acct, callback=_fin)

    def _on_listas_de_autor(self, db_id):
        """Muestra las listas tuyas a las que pertenece el autor del toot."""
        acct = self._autor_de_toot(db_id)
        if not acct:
            notificar(_("No se encontró el autor."))
            return
        self._mostrar_listas_de_cuenta(acct)

    def _mostrar_listas_de_cuenta(self, acct):
        """Núcleo por acct: muestra las listas tuyas a las que pertenece `acct`."""
        if not self.active_mastodon_account_id or not acct:
            return
        acct = acct.lstrip("@")
        speak(_("Consultando listas..."), interrupt=True)

        def _fin(res, err):
            if err:
                speak(_("No se pudieron obtener: {0}").format(err), interrupt=True)
                return
            if not res:
                self._mostrar_texto_largo(_("Listas del autor"),
                                          _("@{0} no está en ninguna de tus listas.").format(acct))
                return
            txt = _("@{0} está en estas listas tuyas:").format(acct) + "\n\n"
            txt += "\n".join("• " + l["title"] for l in res)
            self._mostrar_texto_largo(_("Listas del autor"), txt)
        mastodon_service.listas_de_cuenta_async(self.active_mastodon_account_id, acct, callback=_fin)

    def _on_tarjeta_enlace(self, db_id):
        """Muestra la tarjeta de previsualización de enlace de un toot, si la tiene."""
        if not self.active_mastodon_account_id:
            return
        speak(_("Obteniendo tarjeta de enlace..."), interrupt=True)

        def _fin(res, err):
            if err:
                speak(_("No se pudo obtener: {0}").format(err), interrupt=True)
                return
            if not res:
                notificar(_("Esta publicación no tiene tarjeta de enlace."))
                return
            partes = []
            if res.get("title"):
                partes.append(_("Título: {0}").format(res["title"]))
            if res.get("description"):
                partes.append(_("Descripción: {0}").format(res["description"]))
            if res.get("provider"):
                partes.append(_("Sitio: {0}").format(res["provider"]))
            if res.get("author"):
                partes.append(_("Autor: {0}").format(res["author"]))
            if res.get("url"):
                partes.append(_("Enlace: {0}").format(res["url"]))
            self._mostrar_texto_largo(_("Tarjeta de enlace"), "\n".join(partes))
        mastodon_service.obtener_tarjeta_enlace_async(self.active_mastodon_account_id, db_id, callback=_fin)

    def _on_anuncio_leido(self, announcement_id):
        """Marca un anuncio del servidor como leído."""
        if not self.active_mastodon_account_id:
            return

        def _cb(res, err):
            if err:
                notificar(_("No se pudo marcar: {0}").format(err))
            else:
                notificar(_("Anuncio marcado como leído."))
        mastodon_service.marcar_anuncio_leido_async(self.active_mastodon_account_id, announcement_id, callback=_cb)

    def _on_anuncio_reaccionar(self, announcement_id, añadir):
        """Añade o quita una reacción a un anuncio del servidor (emoji o :atajo:)."""
        if not self.active_mastodon_account_id:
            return
        from .accessible_controls import CustomTextEntryDialog
        titulo = _("Reaccionar al anuncio") if añadir else _("Quitar reacción")
        dlg = CustomTextEntryDialog(self, _("Emoji o atajo (por ejemplo 👍 o :tada:):"), titulo)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        emoji = dlg.GetValue().strip()
        dlg.Destroy()
        if not emoji:
            return

        def _cb(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Reacción añadida.") if añadir else _("Reacción quitada."))
        mastodon_service.reaccionar_anuncio_async(self.active_mastodon_account_id, announcement_id,
                                                  emoji, añadir, callback=_cb)

    def _on_anadir_autor_a_lista(self, db_id):
        """Añade al autor del toot a una de tus listas (selector de lista)."""
        if not self.active_mastodon_account_id:
            return
        acct = self._autor_de_toot(db_id)
        if not acct:
            notificar(_("No se encontró el autor."))
            return
        acct = acct.lstrip("@")
        speak(_("Cargando tus listas..."), interrupt=True)

        def _fin(listas, err):
            if err:
                notificar(_("No se pudieron cargar las listas: {0}").format(err))
                return
            if not listas:
                notificar(_("No tienes listas. Crea una desde Mastodon, Listas de Mastodon."))
                return
            titulos = [l["title"] for l in listas]
            sel = wx.GetSingleChoice(_("¿A qué lista quieres añadir a @{0}?").format(acct),
                                     _("Añadir a una lista"), titulos, self)
            if not sel:
                return
            list_id = listas[titulos.index(sel)]["id"]

            def _cb(res, err2):
                if err2:
                    notificar(_("No se pudo añadir: {0}. Recuerda que en Mastodon solo puedes "
                                "añadir a una lista a cuentas que ya sigues.").format(err2))
                else:
                    notificar(_("@{0} añadido a la lista {1}.").format(acct, sel))
            mastodon_service.gestionar_miembro_lista_async(self.active_mastodon_account_id, list_id,
                                                           acct, True, callback=_cb)
        mastodon_service.obtener_listas_async(self.active_mastodon_account_id, callback=_fin)

    def _on_seguir_hashtag_buffer(self, seguir):
        """Sigue o deja de seguir el hashtag del buffer de búsqueda actual."""
        if not self.active_mastodon_account_id:
            return
        tag = self._hashtag_del_buffer()
        if not tag:
            return

        def _cb(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            elif seguir:
                notificar(_("Ahora sigues #{0}. Sus publicaciones entrarán en tu Inicio.").format(tag))
            else:
                notificar(_("Has dejado de seguir #{0}.").format(tag))
        mastodon_service.seguir_hashtag_async(self.active_mastodon_account_id, tag, seguir, callback=_cb)

    def _on_destacar_hashtag_buffer(self):
        """Destaca en tu perfil el hashtag del buffer de búsqueda actual."""
        if not self.active_mastodon_account_id:
            return
        tag = self._hashtag_del_buffer()
        if not tag:
            return

        def _cb(res, err):
            notificar(_("Hashtag #{0} destacado en tu perfil.").format(tag) if not err
                      else _("No se pudo: {0}").format(err))
        mastodon_service.destacar_hashtag_async(self.active_mastodon_account_id, tag, True, callback=_cb)

    def _hashtag_del_buffer(self):
        """Devuelve el hashtag (sin #) del buffer de búsqueda actual, o None si no es de hashtag."""
        tt = str(self.active_timeline_type or "")
        if not tt.startswith("search:"):
            return None
        termino = tt[len("search:"):].strip()
        if not termino.startswith("#"):
            return None
        return termino.lstrip("#")

    def _anadir_cronologia_usuario(self, acct, display=None):
        """Crea (o reutiliza) un buffer permanente con la cronología de un usuario, bajo el
        nodo 'Cronologías de usuarios' de la cuenta activa (estilo TWBlue). Descarga ya sus
        publicaciones para que el buffer aparezca con contenido."""
        if not self.active_mastodon_account_id or not acct:
            return
        acct = acct.lstrip("@").strip()
        lista = list(config.get("mastodon", "user_timelines", []))
        if any(u.get("account_id") == self.active_mastodon_account_id and u.get("acct") == acct for u in lista):
            speak(_("Ya tienes una cronología de ese usuario."), interrupt=True)
            return
        lista.append({"account_id": self.active_mastodon_account_id, "acct": acct, "display": (display or acct)})
        config.set("mastodon", "user_timelines", lista)  # set() ya guarda (autosave)
        self._cargar_navegacion()
        notificar(_("Cronología de {0} añadida. Descargando sus publicaciones...").format(display or acct))

        def _cb(res, err):
            if err:
                notificar(_("No se pudieron descargar las publicaciones: {0}").format(err))
        mastodon_service.cargar_timeline_async(
            self.active_mastodon_account_id, timeline_type="user:{0}".format(acct), callback=_cb)

    def _borrar_cronologia_usuario(self, acct, nodo=None):
        """Borra el buffer de cronología de un usuario de la cuenta activa (y su caché)."""
        acct = (acct or "").lstrip("@").strip()
        lista = [u for u in config.get("mastodon", "user_timelines", [])
                 if not (u.get("account_id") == self.active_mastodon_account_id and u.get("acct") == acct)]
        config.set("mastodon", "user_timelines", lista)  # set() ya guarda (autosave)
        try:
            db.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = ?",
                       (self.active_mastodon_account_id, "user:{0}".format(acct)))
        except Exception:
            pass
        if str(self.active_timeline_type) == "user:{0}".format(acct):
            self.active_timeline_type = "home"
        # Quitar SOLO ese nodo del árbol (estilo TWBlue), sin reconstruirlo entero.
        self._eliminar_nodo_buffer(nodo)
        notificar(_("Cronología de usuario borrada."))

    def _anadir_buffer_conversacion(self, db_id):
        """Abre una conversación (hilo) como BUFFER permanente, bajo el nodo 'Conversaciones'
        de la cuenta activa. Descarga ya la cadena de mensajes para que aparezca con contenido.
        Si la publicación es un MENSAJE DIRECTO, el buffer se nombra como conversación privada
        con los participantes (sin contarte a ti)."""
        if not self.active_mastodon_account_id or not db_id:
            return
        # La conversación es la del toot ORIGINAL (si la fila es un impulso, su reblog_of_id).
        fila = db.query_one(
            "SELECT COALESCE(reblog_of_id, status_id), author_username, visibility, "
            "COALESCE(mentions_json, '') FROM mastodon_statuses WHERE id = ?",
            (db_id,))
        if not fila or not fila[0]:
            notificar(_("No se encontró la publicación."))
            return
        sid = str(fila[0])
        autor = (fila[1] or "").lstrip("@")
        vis = fila[2] or ""
        mentions_json = fila[3]
        if vis == "direct":
            # Participantes = autor + mencionados, excluyéndote a ti (como en las respuestas).
            participantes = self._construir_menciones_respuesta(fila[1] or "", mentions_json)
            if participantes:
                display = _("Mensajes directos con {0}").format(", ".join(participantes))
            elif autor:
                display = _("Mensajes directos con @{0}").format(autor)
            else:
                display = _("Mensajes directos")
        else:
            display = _("Conversación de {0}").format(autor) if autor else _("Conversación {0}").format(sid)
        lista = list(config.get("mastodon", "thread_buffers", []))
        if any(c.get("account_id") == self.active_mastodon_account_id and str(c.get("status_id")) == sid for c in lista):
            speak(_("Ya tienes esta conversación abierta como buffer."), interrupt=True)
            return
        lista.append({"account_id": self.active_mastodon_account_id, "status_id": sid, "display": display})
        config.set("mastodon", "thread_buffers", lista)  # set() ya guarda (autosave)
        self._cargar_navegacion()
        notificar(_("Conversación añadida como buffer. Descargando los mensajes..."))

        def _cb(res, err):
            if err:
                notificar(_("No se pudieron descargar los mensajes: {0}").format(err))
        mastodon_service.cargar_timeline_async(
            self.active_mastodon_account_id, timeline_type="thread:{0}".format(sid), callback=_cb)

    def _eliminar_nodo_buffer(self, nodo):
        """Quita UN nodo de buffer del árbol al estilo de TWBlue (controller.remove_buffer): mueve
        la selección a un VECINO ANTES de borrar y elimina SOLO esa página/nodo —NO reconstruye
        todo el árbol—. La reconstrucción completa (_cargar_navegacion) al borrar un buffer era lo
        que dejaba la aplicación colgada/sin responder. Al mover la selección a un vecino se dispara
        _on_nav_selection con normalidad y el usuario queda en un buffer válido; como el nodo
        borrado ya no está seleccionado, su borrado no lanza ningún evento de selección.

        Si el nodo es el ÚLTIMO hijo de un grupo desplegable (Conversaciones / Cronologías de
        usuarios), se borra el GRUPO entero para no dejar un nodo vacío."""
        tree = getattr(self, "tree_nav", None)
        try:
            if tree is None or not nodo or not nodo.IsOk():
                # Sin nodo de referencia (caso raro): último recurso, reconstruir.
                self._cargar_navegacion()
                return
            a_borrar = nodo
            padre = tree.GetItemParent(nodo)
            padre_data = tree.GetItemData(padre) if (padre and padre.IsOk()) else None
            if (padre_data and padre_data.get("type") in ("user_timelines_root", "thread_buffers_root")
                    and tree.GetChildrenCount(padre, False) <= 1):
                a_borrar = padre
            # A dónde mover la selección (hermano anterior, siguiente o padre) ANTES de borrar.
            destino = tree.GetPrevSibling(a_borrar)
            if not (destino and destino.IsOk()):
                destino = tree.GetNextSibling(a_borrar)
            if not (destino and destino.IsOk()):
                destino = tree.GetItemParent(a_borrar)
            if destino and destino.IsOk():
                tree.SelectItem(destino)
            tree.Delete(a_borrar)
        except Exception as e:
            log.debug("Error al quitar el nodo de buffer; se reconstruye el árbol: %s", e)
            try:
                self._cargar_navegacion()
            except Exception:
                pass

    def _borrar_buffer_conversacion(self, status_id, nodo=None):
        """Borra el buffer de una conversación de la cuenta activa (y su caché)."""
        sid = str(status_id or "")
        lista = [c for c in config.get("mastodon", "thread_buffers", [])
                 if not (c.get("account_id") == self.active_mastodon_account_id and str(c.get("status_id")) == sid)]
        config.set("mastodon", "thread_buffers", lista)  # set() ya guarda (autosave)
        try:
            db.execute("DELETE FROM mastodon_timeline_statuses WHERE account_id = ? AND timeline = ?",
                       (self.active_mastodon_account_id, "thread:{0}".format(sid)))
        except Exception:
            pass
        # Si el buffer activo era ESTA conversación (al abrir el menú se seleccionó), dejar de
        # apuntarla para que nada intente recargarla tras borrarla.
        if str(self.active_timeline_type) == "thread:{0}".format(sid):
            self.active_timeline_type = "home"
        # Quitar SOLO ese nodo del árbol (estilo TWBlue), sin reconstruirlo entero.
        self._eliminar_nodo_buffer(nodo)
        notificar(_("Conversación borrada de los buffers."))

    def _on_guardar_busqueda(self, event):
        """Crea un buffer de búsqueda guardada (hashtag o texto)."""
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Búsqueda o hashtag a guardar (ej. #accesibilidad):"), _("Guardar búsqueda"))
        if dlg.ShowModal() == wx.ID_OK:
            termino = dlg.GetValue().strip()
            if termino:
                guardadas = list(config.get("mastodon", "saved_searches", []))
                if termino not in guardadas:
                    guardadas.append(termino)
                    config.set("mastodon", "saved_searches", guardadas)
                    self._cargar_navegacion()
                    notificar(_("Búsqueda guardada. Aparece como buffer; ábrela para ver los resultados."))
                else:
                    speak(_("Esa búsqueda ya existe."), interrupt=True)
        dlg.Destroy()

    def _on_eliminar_busqueda(self, termino, nodo=None):
        guardadas = [t for t in config.get("mastodon", "saved_searches", []) if t != termino]
        config.set("mastodon", "saved_searches", guardadas)
        if str(self.active_timeline_type) == "search:{0}".format(termino):
            self.active_timeline_type = "home"
        # Quitar SOLO ese nodo del árbol (estilo TWBlue), sin reconstruirlo entero.
        self._eliminar_nodo_buffer(nodo)
        notificar(_("Búsqueda eliminada."))

    def _on_gestionar_listas(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_lists import ListasDialog
        dlg = ListasDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()
        self._cargar_navegacion()  # refrescar buffers de listas (caché actualizada)

    def _on_filtros_servidor(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_lists import FiltrosServidorDialog
        acc = self.active_mastodon_account_id
        dlg = FiltrosServidorDialog(self, acc)
        dlg.ShowModal()
        cambiado = getattr(dlg, "cambiado", False)
        dlg.Destroy()
        if cambiado:
            # Aplicar ya los filtros nuevos/editados/borrados a lo que hay en pantalla.
            from app.services.mastodon.filtros_servidor import recalcular_buffer_activo
            recalcular_buffer_activo(self, acc)

    def _on_solicitudes_seguimiento(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_views import SolicitudesSeguimientoDialog
        dlg = SolicitudesSeguimientoDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_hashtags_destacados(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_lists import HashtagsDestacadosDialog
        dlg = HashtagsDestacadosDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_cuentas_destacadas(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_lists import CuentasDestacadasDialog
        dlg = CuentasDestacadasDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_notificaciones_agrupadas(self, event):
        """Notificaciones agrupadas REALES del servidor (N2): grupos con autores de muestra,
        extracto de la publicación, expansión de cuentas y descarte por grupo. Con servidores
        antiguos (sin API v2) se agrupa localmente."""
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_notif_grupos_ui import GruposNotificacionesDialog
        dlg = GruposNotificacionesDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_politica_notificaciones(self, event):
        """Abre el diálogo para configurar la política de filtrado de notificaciones."""
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_lists import PoliticaNotificacionesDialog
        dlg = PoliticaNotificacionesDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_destacar_hashtag_perfil(self, event):
        """Destaca (o quita) un hashtag en tu perfil."""
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Hashtag (sin almohadilla, ej. accesibilidad):"),
                                    _("Destacar hashtag en mi perfil"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        nombre = dlg.GetValue().strip().lstrip("#")
        dlg.Destroy()
        if not nombre:
            return
        accion = wx.GetSingleChoice(_("¿Qué quieres hacer con #{0}?").format(nombre),
                                    _("Destacar hashtag"),
                                    [_("Destacarlo en mi perfil"), _("Quitarlo de los destacados")], self)
        if not accion:
            return
        destacar = accion == _("Destacarlo en mi perfil")

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            elif destacar:
                notificar(_("Hashtag #{0} destacado en tu perfil.").format(nombre))
            else:
                notificar(_("Hashtag #{0} quitado de los destacados.").format(nombre))
        mastodon_service.destacar_hashtag_async(self.active_mastodon_account_id, nombre, destacar, callback=_fin)

    def _on_opciones_seguimiento(self, db_id):
        """Diálogo de opciones de seguimiento del autor (mostrar impulsos, avisar)."""
        if not self.active_mastodon_account_id:
            return
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            notificar(_("No se encontró el autor."))
            return
        acct = row[0]
        speak(_("Obteniendo la relación con el autor..."), interrupt=True)

        def _fin(rel, err):
            if err or not rel:
                notificar(_("No se pudo obtener la relación: {0}").format(err or ""))
                return
            from .mastodon_lists import OpcionesSeguimientoDialog
            dlg = OpcionesSeguimientoDialog(self, rel)
            if dlg.ShowModal() == wx.ID_OK:
                datos = dlg.obtener_datos()

                def _fin2(res, err2):
                    if err2:
                        notificar(_("No se pudieron guardar las opciones: {0}").format(err2))
                    else:
                        notificar(_("Opciones de seguimiento guardadas."))
                mastodon_service.seguir_con_opciones_async(
                    self.active_mastodon_account_id, rel["id"], datos["reblogs"], datos["notify"], callback=_fin2)
            dlg.Destroy()
        mastodon_service.obtener_relacion_autor_async(self.active_mastodon_account_id, acct, callback=_fin)

    def _on_limpiar_notificaciones(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .accessible_controls import confirmar
        if confirmar(self, _("¿Descartar TODAS tus notificaciones de Mastodon? Esto no se puede deshacer."),
                     _("Limpiar notificaciones")) != wx.ID_YES:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudieron limpiar: {0}").format(err))
                return
            notificar(_("Notificaciones limpiadas."))
            if self.active_mastodon_account_id and self.active_timeline_type in ("notifications", "mentions"):
                self._cargar_listado_toots(cache_only=True)
        mastodon_service.limpiar_notificaciones_async(self.active_mastodon_account_id, callback=_fin)

    def _on_descartar_notificacion(self, notif_id):
        if not self.active_mastodon_account_id or not notif_id:
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo descartar: {0}").format(err))
                return
            speak(_("Notificación descartada."), interrupt=True)
            if self.active_timeline_type in ("notifications", "mentions"):
                self._cargar_listado_toots(cache_only=True)
        mastodon_service.descartar_notificacion_async(self.active_mastodon_account_id, notif_id, callback=_fin)

    # --- MASTODON: funciones añadidas (paridad con la API 2.x) ---
    def _acct_de_toot(self, db_id):
        r = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        return (r[0] or "").lstrip("@") if r and r[0] else ""

    def _mostrar_cuentas_mastodon(self, titulo, cuentas, permitir_descartar=False):
        """Muestra una lista de cuentas y permite seguir o abrir el perfil en el navegador.
        Si `permitir_descartar` es True (cuentas sugeridas), añade la opción de descartar la
        sugerencia (suggestion_delete)."""
        if not cuentas:
            speak(_("No hay cuentas que mostrar."), interrupt=True)
            return
        opciones = ["{0} (@{1})".format(c.get("display", ""), c.get("acct", "")) for c in cuentas]
        sel = wx.GetSingleChoice(_("Elige una cuenta:"), titulo, opciones, self)
        if not sel:
            return
        c = cuentas[opciones.index(sel)]
        acct = c.get("acct", "")
        acciones = [_("Ver sus publicaciones (cronología)"), _("Ver detalles de la cuenta"),
                    _("Seguir a esta cuenta"), _("Abrir perfil en el navegador")]
        if permitir_descartar:
            acciones.append(_("Descartar esta sugerencia"))
        a = wx.GetSingleChoice(_("¿Qué quieres hacer con @{0}?").format(acct), titulo, acciones, self)
        if a == _("Ver sus publicaciones (cronología)"):
            if acct:
                self._abrir_timeline_acct(acct)
        elif a == _("Ver detalles de la cuenta"):
            if acct:
                self._mostrar_detalles_de_cuenta(acct)
        elif a == _("Seguir a esta cuenta"):
            def _cb(res, err):
                if not err:
                    self._invalidar_cache_cuentas()
                speak(_("Ahora sigues a @{0}.").format(c.get("acct", "")) if not err
                      else _("No se pudo seguir: {0}").format(err), interrupt=True)
            mastodon_service.accion_cuenta_por_acct_async(self.active_mastodon_account_id, c.get("acct", ""), "follow", callback=_cb)
        elif a == _("Abrir perfil en el navegador") and c.get("url"):
            abrir_url(c["url"])
        elif a == _("Descartar esta sugerencia"):
            def _cb(res, err):
                speak(_("Sugerencia descartada.") if not err
                      else _("No se pudo descartar: {0}").format(err), interrupt=True)
            mastodon_service.descartar_sugerencia_async(self.active_mastodon_account_id, c.get("acct", ""), callback=_cb)

    def _on_reportar_toot(self, db_id):
        # Denuncia completa: motivo (categoría), normas incumplidas, comentario y reenvío.
        from .mastodon_reportar_dialog import ReportarTootDialog
        dlg = ReportarTootDialog(self, self.active_mastodon_account_id)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        datos = dlg.get_datos()
        dlg.Destroy()

        def _cb(res, err):
            notificar(_("Publicación reportada.") if not err else _("No se pudo reportar: {0}").format(err))
        speak(_("Enviando el reporte..."), interrupt=True)
        mastodon_service.reportar_toot_async(self.active_mastodon_account_id, db_id, datos["comentario"],
                                             datos["reenviar"], callback=_cb, categoria=datos["categoria"],
                                             reglas=datos["reglas"])

    def _on_interacciones_toot(self, db_id, tipo):
        speak(_("Cargando..."), interrupt=True)
        titulo = _("Quién la impulsó") if tipo == "reblog" else _("Quién la marcó favorita")

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_cuentas_mastodon(titulo, res or [])
        mastodon_service.obtener_interacciones_async(self.active_mastodon_account_id, db_id, tipo, callback=_cb)

    def _on_historial_toot(self, db_id):
        speak(_("Cargando el historial de ediciones..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("Esta publicación no tiene ediciones."), interrupt=True)
                return
            partes = []
            for i, v in enumerate(res):
                cab = _("Versión {0} ({1})").format(i + 1, formatear_tiempo_relativo(v.get("created_at", "")))
                cw = v.get("spoiler") or ""
                cuerpo = (("[" + cw + "] ") if cw else "") + (v.get("content") or "")
                partes.append(cab + ":\n" + cuerpo)
            self._mostrar_texto_largo(_("Historial de ediciones"), "\n\n".join(partes))
        mastodon_service.obtener_historial_async(self.active_mastodon_account_id, db_id, callback=_cb)
