# -*- coding: utf-8 -*-
"""MainFrame (mixin): media_servicio.
Metodos: _on_tg_2fa, _on_tg_unirse, _on_tg_crear_grupo, _on_tg_crear_canal, _on_tg_marcar_todos_leidos, _on_telegram_chat_accion, _on_toggle_read_rss, _on_toggle_fav_rss ...
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

import os
import logging
import json
from datetime import datetime
import wx
from app.core.privacy import abrir_url

from app.core.database import db
from app.core.config_manager import config
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager

from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service
from app.services.telegram.telegram_service import telegram_service


log = logging.getLogger(__name__)


class _Mf_media_servicio:
    def _on_tg_2fa(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        opciones = [_("Activar la verificación en dos pasos"),
                    _("Cambiar la contraseña"),
                    _("Quitar la verificación en dos pasos")]
        dlg = wx.SingleChoiceDialog(self, _("¿Qué quieres hacer?"), _("Verificación en dos pasos"), opciones)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy(); return
        accion = dlg.GetSelection()
        dlg.Destroy()

        def pedir(msg):
            d = CustomTextEntryDialog(self, msg, _("Verificación en dos pasos"))
            val = d.GetValue() if d.ShowModal() == wx.ID_OK else None
            d.Destroy()
            return val

        if accion == 0:
            nueva = pedir(_("Nueva contraseña:"))
            if not nueva:
                return
            pista = pedir(_("Pista (opcional):")) or ""
            fn = lambda: telegram_service.activar_2fa(cuenta, nueva, pista)
            ok = _("Verificación en dos pasos activada.")
        elif accion == 1:
            actual = pedir(_("Contraseña actual:"))
            if not actual:
                return
            nueva = pedir(_("Nueva contraseña:"))
            if not nueva:
                return
            pista = pedir(_("Nueva pista (opcional):")) or ""
            fn = lambda: telegram_service.cambiar_2fa(cuenta, actual, nueva, pista)
            ok = _("Contraseña cambiada.")
        else:
            actual = pedir(_("Contraseña actual:"))
            if not actual:
                return
            fn = lambda: telegram_service.quitar_2fa(cuenta, actual)
            ok = _("Verificación en dos pasos desactivada.")

        def _bg(ctx):
            return fn()
        def _finish(res, err):
            notificar(_("Error: {0}").format(err) if err else ok)
        speak(_("Aplicando..."), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_tg_unirse(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Enlace de invitación o @usuario del grupo/canal:"), _("Unirse a Telegram"))
        if dlg.ShowModal() == wx.ID_OK:
            enlace = dlg.GetValue().strip()
            if enlace:
                def _bg(ctx):
                    return telegram_service.unirse_por_enlace(cuenta, enlace)
                def _finish(res, err):
                    if err:
                        notificar(_("Error al unirse: {0}").format(err))
                    else:
                        self._cargar_navegacion()
                        if self.active_telegram_account_id:
                            self._cargar_listado_chats_telegram()
                        speak(_("Te has unido correctamente."), interrupt=True)
                speak(_("Uniéndose..."), interrupt=True)
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg.Destroy()

    def _on_tg_crear_grupo(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg_t = CustomTextEntryDialog(self, _("Título del grupo:"), _("Crear grupo"))
        if dlg_t.ShowModal() == wx.ID_OK:
            titulo = dlg_t.GetValue().strip()
            dlg_m = CustomTextEntryDialog(self, _("Miembros iniciales (@usuarios separados por comas):"), _("Crear grupo"))
            miembros = []
            if dlg_m.ShowModal() == wx.ID_OK:
                miembros = [m.strip() for m in dlg_m.GetValue().split(",") if m.strip()]
            dlg_m.Destroy()
            if titulo:
                def _bg(ctx):
                    return telegram_service.crear_grupo(cuenta, titulo, miembros)
                def _finish(res, err):
                    if err:
                        notificar(_("Error al crear grupo: {0}").format(err))
                    else:
                        self._cargar_listado_chats_telegram()
                        notificar(_("Grupo creado."))
                speak(_("Creando grupo..."), interrupt=True)
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg_t.Destroy()

    def _on_tg_crear_supergrupo(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog, confirmar
        dlg_t = CustomTextEntryDialog(self, _("Título del supergrupo:"), _("Crear supergrupo"))
        if dlg_t.ShowModal() == wx.ID_OK:
            titulo = dlg_t.GetValue().strip()
            dlg_d = CustomTextEntryDialog(self, _("Descripción (opcional):"), _("Crear supergrupo"))
            descripcion = dlg_d.GetValue().strip() if dlg_d.ShowModal() == wx.ID_OK else ""
            dlg_d.Destroy()
            es_foro = confirmar(self, _("¿Crear el supergrupo con TEMAS (foros) activados?"),
                                _("Crear supergrupo")) == wx.ID_YES
            if titulo:
                def _bg(ctx):
                    return telegram_service.crear_supergrupo(cuenta, titulo, descripcion, es_foro)
                def _finish(res, err):
                    if err:
                        notificar(_("Error al crear supergrupo: {0}").format(err))
                    else:
                        self._cargar_listado_chats_telegram()
                        notificar(_("Supergrupo creado."))
                speak(_("Creando supergrupo..."), interrupt=True)
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg_t.Destroy()

    def _on_tg_crear_canal(self, event):
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg_t = CustomTextEntryDialog(self, _("Título del canal:"), _("Crear canal"))
        if dlg_t.ShowModal() == wx.ID_OK:
            titulo = dlg_t.GetValue().strip()
            if titulo:
                def _bg(ctx):
                    return telegram_service.crear_canal(cuenta, titulo)
                def _finish(res, err):
                    if err:
                        notificar(_("Error al crear canal: {0}").format(err))
                    else:
                        self._cargar_listado_chats_telegram()
                        notificar(_("Canal creado."))
                speak(_("Creando canal..."), interrupt=True)
                task_manager.start_task(_bg, callback=_finish, pass_context=True)
        dlg_t.Destroy()

    def _on_tg_drive(self, event):
        """Abre Telegram Drive (la nube personal) para la cuenta de Telegram activa. La ventana
        es independiente para que SocialRSS siga usándose durante las transferencias; se reutiliza
        si ya estaba abierta para esa cuenta."""
        cuenta = self._tg_cuenta_activa()
        if not cuenta:
            return
        if not hasattr(self, "_tgdrive_frames"):
            self._tgdrive_frames = {}
        # Si YA hay una ventana de la nube abierta para esta cuenta, traerla al frente en vez de
        # abrir otra (aunque estuviera minimizada, oculta o detrás de la principal). `if frame:`
        # (bool del objeto wx) es False si la ventana ya se destruyó: entonces se crea una nueva.
        frame = self._tgdrive_frames.get(cuenta)
        if frame:
            try:
                if frame.IsIconized():
                    frame.Iconize(False)
                frame.Show()
                frame.Raise()
                try:
                    frame.RequestUserAttention()
                except Exception:
                    pass
                return
            except RuntimeError:
                self._tgdrive_frames.pop(cuenta, None)
        else:
            # Referencia muerta (ventana cerrada): olvidarla antes de crear otra.
            self._tgdrive_frames.pop(cuenta, None)
        # Carga diferida: no importar la UI de la nube hasta el primer uso.
        from .tgdrive_window import TelegramDriveFrame
        frame = TelegramDriveFrame(self, cuenta)
        self._tgdrive_frames[cuenta] = frame
        frame.Bind(wx.EVT_CLOSE, lambda e, c=cuenta: self._on_tgdrive_cerrada(e, c))
        frame.Show()
        frame.Raise()

    def _on_tgdrive_cerrada(self, event, cuenta):
        """Olvida la referencia a la ventana de la nube al cerrarse (para poder reabrirla)."""
        try:
            self._tgdrive_frames.pop(cuenta, None)
        except Exception:
            pass
        event.Skip()

    def _on_tg_marcar_todos_leidos(self, account_id):
        """Marca todos los chats de la cuenta de Telegram como leídos."""
        speak(_("Marcando todos los chats como leídos..."), interrupt=True)
        def _bg(ctx):
            return telegram_service.marcar_todos_leidos(account_id)
        def _finish(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                if self.active_telegram_account_id == account_id:
                    self._cargar_listado_chats_telegram()
                self._cargar_navegacion()
                speak(_("{0} chats marcados como leídos.").format(res or 0), interrupt=True)
        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_telegram_chat_accion(self, chat_id, accion):
        """Ejecuta una acción sobre un chat de Telegram en segundo plano. Las acciones reflejan
        el estado (silenciar con duración o para siempre, fijar, bloquear, salir...)."""
        cuenta = self.active_telegram_account_id
        # Estado actual (para alternar) y TIPO del chat (para borrar/salir con el método correcto
        # según sea privado/bot, grupo o canal).
        fila = db.query_one("SELECT pinned, muted, chat_type FROM telegram_chats WHERE id = ?", (f"{cuenta}:{chat_id}",))
        pinned = bool(fila[0]) if fila else False
        tipo_chat = fila[2] if fila else None

        # Confirmar las acciones destructivas (salir de un grupo/canal, borrar o vaciar el chat).
        if accion in ("salir", "borrar", "vaciar"):
            from .accessible_controls import confirmar
            if accion == "salir":
                msg = _("¿Seguro que quieres salir de este chat?")
            elif accion == "vaciar":
                if tipo_chat in ("channel", "supergroup"):
                    msg = _("¿Vaciar este canal? Se borrarán TODOS los mensajes PARA TODOS (no solo "
                            "para ti). Solo funciona si eres administrador y NO se puede deshacer.")
                else:
                    msg = _("¿Vaciar toda la conversación? Se borrará tu historial de este chat.")
            else:
                msg = _("¿Seguro que quieres borrar este chat? Se borrará tu historial.")
            if confirmar(self, msg, _("Confirmar")) != wx.ID_YES:
                return

        def _bg(ctx):
            if accion == "leido":
                return telegram_service.marcar_chat_leido(cuenta, chat_id)
            if accion == "no_leido":
                return telegram_service.marcar_chat_no_leido(cuenta, chat_id)
            if accion == "archivar":
                return telegram_service.archivar_chat(cuenta, chat_id, archivar=True)
            if accion == "desarchivar":
                return telegram_service.archivar_chat(cuenta, chat_id, archivar=False)
            if accion == "reactivar":
                return telegram_service.silenciar_chat(cuenta, chat_id, silenciar=False)
            if accion == "silenciar_siempre":
                return telegram_service.silenciar_chat(cuenta, chat_id, silenciar=True)
            if accion.startswith("silenciar_min:"):
                try:
                    mins = int(accion.split(":", 1)[1])
                except ValueError:
                    mins = 0
                return telegram_service.silenciar_chat(cuenta, chat_id, silenciar=True, minutos=mins)
            if accion == "fijar":
                return telegram_service.fijar_chat(cuenta, chat_id, fijar=not pinned)
            if accion == "bloquear":
                return telegram_service.bloquear_usuario(cuenta, chat_id)
            if accion == "desbloquear":
                return telegram_service.desbloquear_usuario(cuenta, chat_id)
            if accion == "salir":
                return telegram_service.salir_chat(cuenta, chat_id, eliminar=False, tipo=tipo_chat)
            if accion == "borrar":
                return telegram_service.salir_chat(cuenta, chat_id, eliminar=True, tipo=tipo_chat)
            if accion == "vaciar":
                return telegram_service.vaciar_chat(cuenta, chat_id, tipo=tipo_chat)
            if accion == "aviso_captura":
                return telegram_service.aviso_captura_pantalla(cuenta, chat_id)
            return None

        def _finish(res, err):
            if err:
                if accion in ("salir", "borrar"):
                    # Ahora que el chat solo se quita en local si el servidor confirma el borrado,
                    # un error aquí significa que de verdad NO se ha borrado (antes "desaparecía y
                    # volvía"). Orientamos al usuario: si es propietario de un canal/grupo, debe
                    # cerrarlo desde administración.
                    notificar(_("No se pudo borrar el chat: {0}. Si eres el propietario de un canal o "
                                "grupo, ciérralo desde sus opciones de administración.").format(err))
                elif accion == "vaciar" and tipo_chat in ("channel", "supergroup"):
                    # Vaciar un canal/supergrupo borra los mensajes PARA TODOS y exige ser admin.
                    notificar(_("No se pudo vaciar el canal: {0}. Para borrar los mensajes de un canal "
                                "o supergrupo hay que ser administrador (borra para todos).").format(err))
                else:
                    notificar(_("Error: {0}").format(err))
            else:
                self._cargar_listado_chats_telegram()
                speak(_("Acción realizada."), interrupt=True)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    # --- MÉTODOS DE SOPORTE RSS CONTEXTUAL ---
    def _on_toggle_read_rss(self, index, db_id, current_val):
        new_val = 0 if current_val else 1
        db.execute("UPDATE rss_articles SET read = ? WHERE id = ?", (new_val, db_id))
        # En el buffer de "No leídos", al marcar leído el artículo debe desaparecer
        if self.active_timeline_type == "unread" and new_val == 1:
            self._cargar_listado_artículos()
        else:
            self._refrescar_fila_rss(index)
        self._actualizar_contadores_rss()
        speak(_("Marcado como leído.") if new_val else _("Marcado como no leído."), interrupt=True)

    def _on_toggle_fav_rss(self, index, db_id, current_val):
        new_val = 0 if current_val else 1
        db.execute("UPDATE rss_articles SET favorite = ? WHERE id = ?", (new_val, db_id))
        if self.active_timeline_type == "favorites" and new_val == 0:
            self._cargar_listado_artículos()
        else:
            self._refrescar_fila_rss(index)
        notificar(_("Destacado.") if new_val else _("Quitado de destacados."))

    def _on_toggle_read_later_rss(self, index, db_id, current_val):
        new_val = 0 if current_val else 1
        db.execute("UPDATE rss_articles SET read_later = ? WHERE id = ?", (new_val, db_id))
        # En el buffer "Leer más tarde", al quitarlo debe desaparecer de la lista.
        if self.active_timeline_type == "read_later" and new_val == 0:
            self._cargar_listado_artículos()
        else:
            self._refrescar_fila_rss(index)
        notificar(_("Guardado para leer más tarde.") if new_val else _("Quitado de Leer más tarde."))

    def _articulo_actual_rss_db_id(self):
        """Devuelve el db_id del artículo RSS enfocado, o None."""
        idx = self.lista_items.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.lista_items_db_ids):
            return None
        return self.lista_items_db_ids[idx]

    def _atajo_rss_ver_visor(self):
        """Abre el visor de lectura limpia del artículo RSS enfocado (atajo rápido). Solo actúa si
        hay una cuenta RSS activa y un artículo enfocado."""
        if not self.active_rss_account_id:
            return
        db_id = self._articulo_actual_rss_db_id()
        if not db_id:
            speak(_("Sitúate sobre un artículo."), interrupt=True)
            return
        self._on_ver_articulo_limpio(db_id)

    def _atajo_rss_articulo(self, accion):
        """Aplica una acción rápida (destacar / leer más tarde / leído) al artículo
        RSS enfocado. Solo actúa si hay una cuenta RSS activa y un artículo enfocado."""
        if not self.active_rss_account_id:
            return
        idx = self.lista_items.GetFocusedItem()
        if idx is None or idx < 0 or idx >= len(self.lista_items_db_ids):
            return
        db_id = self.lista_items_db_ids[idx]
        col = {"destacar": "favorite", "leer_tarde": "read_later", "toggle_read": "read"}[accion]
        row = db.query_one(f"SELECT COALESCE({col}, 0) FROM rss_articles WHERE id = ?", (db_id,))
        actual = (row[0] if row else 0)
        if accion == "destacar":
            self._on_toggle_fav_rss(idx, db_id, actual)
        elif accion == "leer_tarde":
            self._on_toggle_read_later_rss(idx, db_id, actual)
        else:
            self._on_toggle_read_rss(idx, db_id, actual)

    def _atajo_rss_no_leido(self, delta):
        """Salta al SIGUIENTE (delta=1) o ANTERIOR (delta=-1) artículo NO LEÍDO de la lista
        RSS activa, sin tocar el resto de la lista (estilo SimpleRSS). El lector anuncia la
        fila al recibir el foco; si no queda ninguno, se avisa con suavidad."""
        if not self.active_rss_account_id or not self.lista_items_db_ids:
            return
        ids = list(self.lista_items_db_ids)
        # Estado de leído de TODAS las filas visibles en una consulta por lotes.
        no_leidos = set()
        for i in range(0, len(ids), 500):
            lote = ids[i:i + 500]
            ph = ",".join("?" for _ in lote)
            no_leidos.update(r[0] for r in db.query(
                f"SELECT id FROM rss_articles WHERE id IN ({ph}) AND COALESCE(read,0) = 0",
                tuple(lote)))
        if not no_leidos:
            speak(_("No hay artículos sin leer en esta lista."), interrupt=True)
            return
        idx = self.lista_items.GetFocusedItem()
        if idx is None or idx < 0:
            idx = -1 if delta > 0 else len(ids)
        recorrido = range(idx + 1, len(ids)) if delta > 0 else range(idx - 1, -1, -1)
        for i in recorrido:
            if ids[i] in no_leidos:
                self.lista_items.Focus(i)
                self.lista_items.Select(i)
                return
        speak(_("No hay más artículos sin leer hacia adelante.") if delta > 0
              else _("No hay más artículos sin leer hacia atrás."), interrupt=True)

    def _on_copiar_titulo_rss(self, db_id=None):
        from app.core.speech import copiar_al_portapapeles
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        row = db.query_one("SELECT title FROM rss_articles WHERE id = ?", (db_id,))
        if row and row[0]:
            copiar_al_portapapeles(row[0], _("Título copiado al portapapeles."))

    def _on_copiar_enlace_rss(self, db_id=None):
        from app.core.speech import copiar_al_portapapeles
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        row = db.query_one("SELECT link FROM rss_articles WHERE id = ?", (db_id,))
        if row and row[0]:
            copiar_al_portapapeles(row[0], _("Enlace copiado al portapapeles."))

    def _on_eliminar_articulo_rss(self, db_id=None):
        """Mueve el artículo a la papelera y lo quita de la lista."""
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        rss_service.mover_a_papelera(db_id)
        # Quitar la fila en su sitio (antes se recargaba la lista entera y se perdía la posición).
        self._quitar_fila_articulo_rss(db_id)
        self._actualizar_contadores_rss()
        speak(_("Artículo movido a la papelera."), interrupt=True)

    def _on_descargar_episodio(self, db_id=None):
        """Añade un episodio a la COLA de descargas de podcast (auditoría, prioridad B):
        se descarga en segundo plano con pausa/reanudación y se puede seguir su progreso
        en menú RSS → 'Descargas de podcast...'. Antes era una descarga síncrona sin cola."""
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        if rss_service.episodio_descargado(db_id):
            speak(_("Este episodio ya está descargado."), interrupt=True)
            return
        if rss_service.encolar_descarga(db_id):
            notificar(_("Episodio añadido a la cola de descargas. Sigue su progreso en "
                        "RSS, Descargas de podcast."))
        else:
            notificar(_("Este artículo no tiene audio descargable."))

    def _on_borrar_descarga_podcast(self, db_id=None):
        """Borra la descarga local de un episodio de podcast."""
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        rss_service.borrar_descarga_podcast(db_id)
        notificar(_("Descarga del episodio borrada."))

    def _on_compartir_rss_mastodon(self, db_id=None):
        """Compatibilidad: comparte el artículo (ahora ofrece Mastodon y Bluesky)."""
        self._on_compartir_rss(db_id)

    def _on_compartir_rss(self, db_id=None):
        """Comparte el artículo en Mastodon o Bluesky. Si hay cuentas en ambas redes (o varias en
        una), pregunta a cuál. Abre el compositor de la red elegida con el título y el enlace."""
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        row = db.query_one("SELECT title, link FROM rss_articles WHERE id = ?", (db_id,))
        if not row:
            return
        titulo, enlace = row
        from app.core.privacy import limpiar_url
        texto = (titulo or "").strip()
        if enlace:
            texto += "\n\n" + limpiar_url(enlace)

        # Reunir los destinos posibles: cada cuenta activa de Mastodon y de Bluesky (si la red está
        # habilitada). Cada destino: (red, account_id, etiqueta legible).
        from app.core.feature_flags import servicio_habilitado
        destinos = []
        if servicio_habilitado("mastodon"):
            for aid, name in db.query("SELECT id, name FROM accounts WHERE service_id = 'mastodon' AND active = 1 ORDER BY position, id"):
                destinos.append(("mastodon", aid, _("Mastodon: {0}").format(name)))
        if servicio_habilitado("bluesky"):
            for aid, name in db.query("SELECT id, name FROM accounts WHERE service_id = 'bluesky' AND active = 1 ORDER BY position, id"):
                destinos.append(("bluesky", aid, _("Bluesky: {0}").format(name)))
        if not destinos:
            speak(_("No hay ninguna cuenta de Mastodon ni de Bluesky para compartir."), interrupt=True)
            return

        if len(destinos) == 1:
            red, cuenta = destinos[0][0], destinos[0][1]
        else:
            etiquetas = [d[2] for d in destinos]
            sel = wx.GetSingleChoice(_("¿Dónde quieres compartir el artículo?"),
                                     _("Compartir artículo"), etiquetas, self)
            if not sel:
                return
            elegido = destinos[etiquetas.index(sel)]
            red, cuenta = elegido[0], elegido[1]

        if red == "mastodon":
            from .mastodon_views import ComposeTootDialog
            dlg = ComposeTootDialog(self, cuenta, texto_inicial=texto)
        else:
            from .bluesky_dialogs import BlueskyComposeDialog
            dlg = BlueskyComposeDialog(self, cuenta, texto_inicial=texto)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_config_wallabag(self, event):
        """Diálogo de configuración de Wallabag: servidor y credenciales de la API."""
        from app.services.rss import wallabag
        from app.core.security import security
        from .accessible_controls import CustomTextEntryDialog
        campos = [
            ("wallabag_url_v", _("URL del servidor Wallabag (ej. https://app.wallabag.it):"),
             config.get("rss", "wallabag_url", ""), False),
            ("client_id", _("Client ID (API):"), security.obtener_credencial("wallabag", "client_id") or "", False),
            ("client_secret", _("Client Secret (API):"), security.obtener_credencial("wallabag", "client_secret") or "", True),
            ("username", _("Usuario de Wallabag:"), security.obtener_credencial("wallabag", "username") or "", False),
            ("password", _("Contraseña de Wallabag:"), "", True),
        ]
        valores = {}
        for clave, etiqueta, valor, es_pass in campos:
            d = CustomTextEntryDialog(self, etiqueta, _("Configurar Wallabag"), value=valor, password=es_pass)
            if d.ShowModal() != wx.ID_OK:
                d.Destroy()
                return
            valores[clave] = d.GetValue().strip()
            d.Destroy()
        wallabag.guardar_config(valores["wallabag_url_v"], valores["client_id"],
                                valores["client_secret"], valores["username"], valores["password"])
        # Comprobar la conexión en segundo plano para dar feedback inmediato.
        speak(_("Comprobando la conexión con Wallabag..."), interrupt=True)

        def _bg(ctx):
            from app.services.rss import wallabag as _wb
            _wb._obtener_token()
            return True

        def _cb(res, err):
            notificar(_("Wallabag configurado correctamente.") if not err
                      else _("Se guardó, pero la conexión falló: {0}").format(err))
        task_manager.start_task(_bg, callback=_cb, pass_context=True)

    def _on_enviar_wallabag(self, db_id=None):
        """Envía el artículo enfocado a Wallabag (leer más tarde externo) en segundo plano."""
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        row = db.query_one("SELECT title, link FROM rss_articles WHERE id = ?", (db_id,))
        if not row or not row[1]:
            notificar(_("Este artículo no tiene un enlace que enviar."))
            return
        titulo, link = row
        speak(_("Enviando a Wallabag..."), interrupt=True)

        def _bg(ctx):
            from app.services.rss import wallabag
            return wallabag.enviar_articulo(link, titulo or "")

        def _cb(res, err):
            notificar(_("Artículo enviado a Wallabag.") if not err
                      else _("No se pudo enviar a Wallabag: {0}").format(err))
        task_manager.start_task(_bg, callback=_cb, pass_context=True)

    def _on_copiar_articulo_rss(self, db_id=None):
        """Copia el título, el enlace y el contenido limpio del artículo."""
        from app.core.speech import copiar_al_portapapeles
        from app.core.html_utils import html_a_texto
        db_id = db_id or self._articulo_actual_rss_db_id()
        if not db_id:
            return
        row = db.query_one("SELECT title, link, content FROM rss_articles WHERE id = ?", (db_id,))
        if not row:
            return
        titulo, enlace, contenido = row
        texto = (titulo or "").strip()
        if enlace:
            texto += "\n" + enlace
        cuerpo = html_a_texto(contenido or "")
        if cuerpo:
            texto += "\n\n" + cuerpo
        copiar_al_portapapeles(texto, _("Artículo copiado al portapapeles."))

    def _on_exportar_articulo_rss(self, db_id):
        """Exporta un artículo a un archivo de texto, Markdown o HTML."""
        from app.core.html_utils import html_a_texto
        row = db.query_one("SELECT title, link, content, author, published_at FROM rss_articles WHERE id = ?", (db_id,))
        if not row:
            return
        titulo, enlace, contenido, autor, fecha = row
        dlg = wx.FileDialog(
            self, message=_("Exportar artículo como"),
            defaultFile=(titulo or "articulo")[:60].replace("/", "-") + ".md",
            wildcard="Markdown (*.md)|*.md|Texto (*.txt)|*.txt|HTML (*.html)|*.html",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()
        try:
            if ruta.lower().endswith(".html"):
                texto = (f"<h1>{titulo or ''}</h1>\n"
                         f"<p><em>{autor or ''} — {fecha or ''}</em></p>\n"
                         f"<p><a href=\"{enlace or ''}\">{enlace or ''}</a></p>\n"
                         f"{contenido or ''}")
            elif ruta.lower().endswith(".txt"):
                cuerpo = html_a_texto(contenido or "")
                texto = f"{titulo or ''}\n{autor or ''} — {fecha or ''}\n{enlace or ''}\n\n{cuerpo}"
            else:  # markdown
                cuerpo = html_a_texto(contenido or "")
                texto = (f"# {titulo or ''}\n\n*{autor or ''} — {fecha or ''}*\n\n"
                         f"[{enlace or ''}]({enlace or ''})\n\n{cuerpo}")
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(texto)
            notificar(_("Artículo exportado a {0}").format(ruta))
        except Exception as e:
            notificar(_("No se pudo exportar: {0}").format(e))

    def _on_marcar_todo_leido_rss(self):
        """Marca como leídos todos los artículos del buffer RSS activo."""
        if not self.active_rss_account_id:
            return
        sql = """
        UPDATE rss_articles SET read = 1
        WHERE feed_id IN (SELECT id FROM rss_feeds WHERE account_id = ?)
        """
        params = [self.active_rss_account_id]
        if self.active_timeline_type == "category" and self.active_rss_category_id:
            sql += " AND feed_id IN (SELECT id FROM rss_feeds WHERE category_id = ?)"
            params.append(self.active_rss_category_id)
        elif self.active_timeline_type == "feed" and self.active_rss_feed_id:
            sql += " AND feed_id = ?"
            params.append(self.active_rss_feed_id)
        elif self.active_timeline_type == "favorites":
            sql += " AND favorite = 1"
        elif self.active_timeline_type == "read_later":
            sql += " AND COALESCE(read_later, 0) = 1"
        elif self.active_timeline_type == "today":
            sql += " AND date(published_at) = date('now', 'localtime')"
        db.execute(sql, tuple(params))
        self._cargar_listado_artículos()
        self._actualizar_contadores_rss()
        speak(_("Todo el buffer marcado como leído."), interrupt=True)

    def _reproducir_social_con_reanudar(self, ruta, titulo, service_origin,
                                        account_origin_id, source_link, anuncio=None):
        """Reproduce en el reproductor de redes sociales. Si esa pista tiene una posición
        guardada (porque se detuvo a la mitad), PREGUNTA si continuar desde ahí o empezar desde
        el principio, y reproduce en consecuencia. Si no hay posición, empieza directamente."""
        from app.services.media_player.player_service import player_service

        def _hacer():
            try:
                pos = player_service.historial.obtener_progreso(ruta)
            except Exception:
                pos = 0.0
            resume = True
            if pos and pos > 3.0:
                m, s = int(pos // 60), int(pos % 60)
                dlg = wx.MessageDialog(
                    self,
                    _("Hay una reproducción guardada de este audio o vídeo en {0:02d}:{1:02d}.\n\n"
                      "¿Quieres continuar desde ahí o empezar desde el principio?").format(m, s),
                    _("Continuar reproducción"),
                    wx.YES_NO | wx.ICON_QUESTION)
                dlg.SetYesNoLabels(_("Continuar"), _("Desde el principio"))
                resume = (dlg.ShowModal() == wx.ID_YES)
                dlg.Destroy()
            player_service.cargar_y_reproducir(
                ruta, titulo, service_origin=service_origin,
                account_origin_id=account_origin_id, source_link=source_link, resume=resume)
            speak(anuncio or _("Reproduciendo."), interrupt=True)

        wx.CallAfter(_hacer)

    def _url_media_reproducible(self, media_json: str):
        """Devuelve la URL del primer adjunto de audio/vídeo, o None."""
        try:
            for att in json.loads(media_json):
                tipo = att.get("type", "")
                if tipo in ("audio", "video", "gifv"):
                    return att.get("url") or att.get("remote_url")
        except Exception:
            pass
        return None

    @staticmethod
    def _media_tiene_imagen(media_json) -> bool:
        """True si el media_json contiene alguna IMAGEN (para ofrecer 'Describir imagen' solo
        cuando aplica). 'image' en Mastodon/Bluesky, 'photo' en X."""
        if not media_json:
            return False
        try:
            return any(att.get("type") in ("image", "photo")
                       for att in json.loads(media_json) or [])
        except Exception:
            return False

    def _on_reproducir_media_mastodon(self, db_id: str):
        """Reproduce el audio/vídeo adjunto de un toot."""
        row = db.query_one("SELECT media_json, author_name FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            speak(_("Esta publicación no tiene audio ni vídeo."), interrupt=True)
            return
        url = self._url_media_reproducible(row[0])
        if not url:
            speak(_("Esta publicación no tiene audio ni vídeo reproducible."), interrupt=True)
            return
        # Multimedia difuminada por un filtro del servidor: confirmar antes de reproducirla.
        if not self._confirmar_media_filtrada(db_id):
            return
        titulo = row[1] or "Mastodon"
        speak(_("Cargando audio..."), interrupt=True)

        def _bg(ctx):
            # Descargar a un archivo temporal y convertir si BASS no lo soporta.
            # Muchos CDN de Mastodon (Cloudflare) devuelven 403 al User-Agent por
            # defecto de urllib; usamos una cabecera de navegador y requests.
            import hashlib, requests
            from app.services.audio import microphone
            from app.core.paths import get_data_path
            ext = os.path.splitext(url.split("?")[0])[1] or ".mp4"
            destino = str(get_data_path("temp") / ("mastodon_" + hashlib.sha256(url.encode()).hexdigest()[:16] + ext))
            if not os.path.exists(destino):
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "*/*",
                }
                resp = requests.get(url, headers=headers, timeout=60, stream=True)
                resp.raise_for_status()
                with open(destino, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            return microphone.convertir_a_wav_si_necesario(destino)

        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo reproducir: {0}").format(err) if err else _("No se pudo reproducir."))
            else:
                self._reproducir_social_con_reanudar(
                    res, titulo, service_origin="mastodon",
                    account_origin_id=self.active_mastodon_account_id, source_link=url)

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    def _on_detener_media_mastodon(self):
        """Detiene el audio/vídeo adjunto: GUARDA la posición y limpia la sesión. Al volver a
        reproducirlo se preguntará si continuar desde ahí o empezar desde el principio."""
        from app.services.media_player.player_service import player_service
        player_service.detener_y_limpiar()
        speak(_("Audio o vídeo detenido."), interrupt=True)

    def _reproducir_audio_servicio_desde_urls(self, urls, titulo_fallback, service_origin, account_origin_id):
        """Busca una URL de servicio (YouTube/SoundCloud...) en la lista y reproduce su audio."""
        from app.services.audio import stream_resolver
        if not stream_resolver.disponible():
            speak(_("La reproducción de audio de servicios no está disponible."), interrupt=True)
            return
        url = stream_resolver.primera_url_de_servicio(urls)
        if not url:
            notificar(_("Esta entrada no contiene enlaces de YouTube, SoundCloud u otros servicios compatibles."))
            return
        speak(_("Obteniendo audio del enlace..."), interrupt=True)

        def _bg(ctx):
            audio_url, titulo = stream_resolver.resolver_audio_url(url)
            # La URL resuelta (p. ej. de YouTube) suele ser webm/opus, que BASS no
            # reproduce, y además BASS no puede abrir URLs de streaming en este
            # binding. ffmpeg la convierte a un WAV local reproducible.
            from app.services.audio import microphone
            ruta = microphone.convertir_a_wav_si_necesario(audio_url, forzar=True)
            return ruta, titulo

        def _fin(res, err):
            if err or not res or not res[0]:
                notificar(_("No se pudo obtener el audio: {0}").format(err or ""))
                return
            ruta, titulo = res
            self._reproducir_social_con_reanudar(
                ruta, titulo or titulo_fallback, service_origin=service_origin,
                account_origin_id=account_origin_id, source_link=url,
                anuncio=_("Reproduciendo: {0}").format(titulo or titulo_fallback))

        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _enlace_servicio_de_db(self, db_id) -> str:
        """Primer enlace de servicio (YouTube/SoundCloud...) de un toot, o '' si no tiene. Sirve
        para saber si ese enlace es el que está sonando ahora mismo en el reproductor."""
        row = db.query_one("SELECT urls_json FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            return ""
        try:
            urls = json.loads(row[0])
        except Exception:
            return ""
        try:
            from app.services.audio import stream_resolver
            return stream_resolver.primera_url_de_servicio(urls) or ""
        except Exception:
            return ""

    def _on_detener_audio_servicio(self):
        """Detiene el audio del enlace (YouTube, SoundCloud...): GUARDA la posición y limpia la
        sesión. Al volver a reproducirlo se preguntará si continuar o empezar de cero."""
        from app.services.media_player.player_service import player_service
        player_service.detener_y_limpiar()
        speak(_("Audio del enlace detenido."), interrupt=True)

    def _on_reproducir_audio_servicio_mastodon(self, db_id):
        row = db.query_one("SELECT urls_json, author_name FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return
        try:
            urls = json.loads(row[0]) if row[0] else []
        except Exception:
            urls = []
        self._reproducir_audio_servicio_desde_urls(urls, row[1] or "Mastodon", "mastodon", self.active_mastodon_account_id)

    def _on_reproducir_audio_servicio_rss(self, db_id):
        # El audio de RSS (podcast o vídeo de un enlace) va al SEGUNDO reproductor.
        if not self._player2_puede_reproducir():
            return
        speak(_("Obteniendo audio..."), interrupt=True)

        def _cb(ok, err):
            if not ok:
                notificar(_("No se pudo reproducir el audio: {0}").format(err or ""))
        rss_service.reproducir_articulo_audio(db_id, callback=_cb)

    def _on_abrir_toot_navegador(self, db_id: str):
        row = db.query_one("SELECT uri FROM mastodon_statuses WHERE id = ?", (db_id,))
        if row and row[0]:
            abrir_url(row[0])
            speak(_("Abriendo la publicación en el navegador."), interrupt=True)
        else:
            speak(_("No hay enlace para esta publicación."), interrupt=True)

    def _on_copiar_enlace_toot(self, db_id: str):
        row = db.query_one("SELECT uri FROM mastodon_statuses WHERE id = ?", (db_id,))
        if row and row[0]:
            from app.core.speech import copiar_al_portapapeles
            copiar_al_portapapeles(row[0], _("Enlace copiado al portapapeles."))

    def _on_ver_perfil_cuenta(self, event):
        """Pide una cuenta ESCRITA (con o sin arroba, local o remota) y muestra su perfil. Útil para
        perfiles citados sin arroba que no están en ninguna lista."""
        if not self.active_mastodon_account_id:
            notificar(_("Activa primero una cuenta de Mastodon."))
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(
            self, _("Cuenta a ver (ej. @usuario o usuario@instancia.social):"),
            _("Ver perfil de una cuenta"))
        acct = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if acct:
            self._mostrar_detalles_de_cuenta(acct)

    def _on_novedades_seguidores(self, event):
        """Compara tu lista de seguidores con la de la última vez y muestra quién te ha seguido y
        quién te ha dejado de seguir (Mastodon no avisa de los "unfollow"; se detecta comparando)."""
        if not self.active_mastodon_account_id:
            notificar(_("Activa primero una cuenta de Mastodon."))
            return
        speak(_("Comprobando novedades de seguidores..."), interrupt=True)

        def _finish(res, err):
            if err or not res:
                speak(_("No se pudieron comprobar los seguidores: {0}").format(err or ""), interrupt=True)
                return
            if res.get("primera_vez"):
                self._mostrar_texto_largo(_("Novedades de seguidores"), _(
                    "Es la primera vez que compruebas tus seguidores en esta cuenta.\n"
                    "He guardado tu lista actual: {0} seguidores.\n\n"
                    "La próxima vez que abras esta opción te diré quién te ha seguido y quién te ha "
                    "dejado de seguir desde ahora.").format(res.get("total", 0)))
                return
            nuevos = res.get("nuevos") or []
            perdidos = res.get("perdidos") or []
            descarga = res.get("descarga", "completa")
            lineas = [_("Seguidores actuales: {0}.").format(res.get("total", 0)), ""]
            lineas.append(_("Te han seguido ({0}):").format(len(nuevos)))
            lineas += ["  {0} (@{1})".format(d["display_name"] or d["acct"], d["acct"]) for d in nuevos] \
                or ["  " + _("(nadie nuevo)")]
            lineas.append("")
            if descarga == "completa":
                lineas.append(_("Te han dejado de seguir ({0}):").format(len(perdidos)))
                lineas += ["  {0} (@{1})".format(d["display_name"] or d["acct"], d["acct"]) for d in perdidos] \
                    or ["  " + _("(nadie)")]
            else:
                # Descarga parcial o limitada: comparar las bajas daría "unfollows" FALSOS
                # (las páginas no descargadas parecerían pérdidas), así que no se muestran.
                lineas.append(_("No se pudo descargar la lista completa de seguidores esta vez "
                                "(conexión o cuenta muy grande), así que no puedo comprobar "
                                "quién te ha dejado de seguir. Vuelve a intentarlo más tarde."))
            self._mostrar_texto_largo(_("Novedades de seguidores"), "\n".join(lineas))

        mastodon_service.novedades_seguidores_async(self.active_mastodon_account_id, callback=_finish)

    @staticmethod
    def _texto_relacion_mastodon(rel: dict) -> str:
        """Frase(s) en español con tu relación con la cuenta (te sigue, le sigues, te ha
        bloqueado…). Devuelve '' si no hay datos, o un bloque que acaba en salto de línea.
        NOTA: Mastodon no da una lista de quién te bloquea; 'blocked_by' solo dice si ESTA
        cuenta concreta te bloquea a ti."""
        if not rel:
            return ""
        if rel.get("es_yo"):
            return _("Relación: esta es tu propia cuenta.") + "\n"
        partes = []
        # Seguimiento en ambos sentidos.
        if rel.get("followed_by"):
            partes.append(_("te sigue"))
        else:
            partes.append(_("no te sigue"))
        if rel.get("requested"):
            partes.append(_("le has enviado una solicitud de seguimiento (pendiente)"))
        elif rel.get("following"):
            partes.append(_("le sigues"))
        else:
            partes.append(_("no le sigues"))
        if rel.get("requested_by"):
            partes.append(_("te ha enviado una solicitud de seguimiento (pendiente)"))
        # Bloqueos y silencio (lo relevante para la pregunta de "¿me ha bloqueado?").
        if rel.get("blocked_by"):
            partes.append(_("TE HA BLOQUEADO"))
        if rel.get("blocking"):
            partes.append(_("le tienes bloqueado"))
        if rel.get("muting"):
            partes.append(_("le tienes silenciado"))
        return _("Relación: {0}.").format(", ".join(partes)) + "\n"

    def _mostrar_detalles_de_cuenta(self, acct: str):
        """Obtiene y muestra la ficha de una cuenta (bio, seguidores, etc.) por su acct."""
        if not self.active_mastodon_account_id or not acct:
            return
        speak(_("Obteniendo detalles de la cuenta..."), interrupt=True)

        def _finish(res, err):
            if err or not res:
                speak(_("No se pudieron obtener los detalles: {0}").format(err or ""), interrupt=True)
                return
            # Estado de la cuenta (mudada, suspendida, limitada, en memoria, identificador no
            # verificable...) justo tras el nombre: es lo primero que conviene saber.
            estado = "".join(e + "\n" for e in (res.get("estado") or []))
            campos = ""
            for nombre_c, valor_c, verificado in res.get("campos") or []:
                campos += "{0}: {1}{2}\n".format(
                    nombre_c, valor_c, " " + _("(verificado)") if verificado else "")
            texto = _(
                "{nombre} (@{acct})\n"
                "{estado}"
                "Seguidores: {seg}. Siguiendo: {sig}. Publicaciones: {pub}.\n"
                "{relacion}"
                "{bot}\n"
                "Biografía:\n{bio}\n\n"
                "{campos}"
                "Perfil: {url}"
            ).format(
                nombre=res.get("display_name", ""), acct=res.get("acct", ""), estado=estado,
                seg=res.get("followers", 0), sig=res.get("following", 0), pub=res.get("statuses", 0),
                relacion=self._texto_relacion_mastodon(res.get("relacion") or {}),
                bot=_("Es una cuenta automatizada (bot).") if res.get("bot") else "",
                bio=res.get("bio") or _("(sin biografía)"),
                campos=(_("Campos del perfil:") + "\n" + campos + "\n") if campos else "",
                url=res.get("url", "")
            )
            self._mostrar_texto_largo(_("Detalles de la cuenta"), texto)

        mastodon_service.obtener_detalles_cuenta_async(self.active_mastodon_account_id, acct, callback=_finish)

    def _on_detalles_autor(self, db_id: str):
        """Muestra la ficha del autor del toot. Si la publicación implica a VARIAS cuentas (autor,
        quien la impulsó y los mencionados), deja elegir de quién ver los detalles."""
        if not self.active_mastodon_account_id:
            return
        row = db.query_one(
            "SELECT author_username, COALESCE(reblog_by_username, ''), COALESCE(mentions_json, '') "
            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return
        # Reunir las cuentas implicadas, en orden y sin repetir: autor, quien la impulsó, mencionados.
        candidatos = []
        def _add(u):
            u = (u or "").lstrip("@").strip()
            if u and u not in candidatos:
                candidatos.append(u)
        _add(row[0])
        _add(row[1])
        try:
            import json as _json
            for m in (_json.loads(row[2]) if row[2] else []):
                _add(m)
        except Exception:
            pass
        if not candidatos:
            speak(_("No se encontró el autor."), interrupt=True)
            return
        if len(candidatos) == 1:
            self._mostrar_detalles_de_cuenta(candidatos[0])
            return
        # Varias cuentas: elegir.
        opciones = ["@" + c for c in candidatos]
        dlg = wx.SingleChoiceDialog(
            self,
            _("Esta publicación implica a varias cuentas. ¿De quién quieres ver los detalles?"),
            _("Elegir cuenta"), opciones)
        if dlg.ShowModal() == wx.ID_OK:
            sel = dlg.GetSelection()
            dlg.Destroy()
            if 0 <= sel < len(candidatos):
                self._mostrar_detalles_de_cuenta(candidatos[sel])
        else:
            dlg.Destroy()

    def _mostrar_texto_largo(self, titulo, texto):
        """Muestra un texto multilínea en un diálogo accesible de solo lectura."""
        from .accessible_controls import AccessibleTextCtrl
        dlg = wx.Dialog(self, title=titulo, size=(520, 420),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=titulo)
        s = wx.BoxSizer(wx.VERTICAL)
        txt = AccessibleTextCtrl(dlg, value=texto,
                                 style=wx.TE_MULTILINE | wx.TE_READONLY, name=titulo)
        s.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        btn = wx.Button(dlg, wx.ID_OK, _("&Aceptar"))
        s.Add(btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizer(s)
        txt.SetFocus()
        dlg.ShowModal()
        dlg.Destroy()

    def _texto_detalle_publicacion(self, db_id):
        """Devuelve (cabecera, cuerpo) de una publicación (o mensaje directo) para 'Ver publicación':
        la CABECERA reúne autor, quién la impulsó, fecha, visibilidad, cliente, estado, menciones,
        advertencia de contenido y adjuntos; el CUERPO es el texto de la publicación y el enlace. El
        diálogo inserta las estadísticas del servidor entre cabecera y cuerpo. Devuelve None si no
        existe la publicación."""
        row = db.query_one(
            "SELECT author_name, author_username, content, created_at, COALESCE(favourited,0), "
            "COALESCE(reblogged,0), COALESCE(bookmarked,0), visibility, COALESCE(spoiler_text,''), "
            "COALESCE(media_json,''), COALESCE(reblog_by_name,''), COALESCE(reblog_by_username,''), "
            "COALESCE(application,''), COALESCE(has_poll,0), COALESCE(mentions_json,''), COALESCE(uri,'') "
            "FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row:
            return None
        (author, user, content, date, fav, rebl, book, vis, cw, media, rbn, rbu, application,
         has_poll, mentions_json, uri) = row
        vis_map = {"public": _("Público"), "unlisted": _("No listado"),
                   "private": _("Solo seguidores"), "direct": _("Mensaje directo")}
        try:
            dt = datetime.fromisoformat((date or "").replace("Z", "+00:00"))
            fecha_str = dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            fecha_str = (date or "").replace("T", " ")[:19]
        import json as _json
        lineas = [_("Autor: {0} ({1})").format(author or "", user or "")]
        if rbn:
            lineas.append(_("Impulsado por: {0} {1}").format(rbn, rbu or ""))
        lineas.append(_("Fecha: {0}").format(fecha_str))
        lineas.append(_("Visibilidad: {0}").format(vis_map.get(vis, vis or "")))
        if application:
            lineas.append(_("Cliente: {0}").format(application))
        estados = []
        if fav: estados.append(_("favorito"))
        if rebl: estados.append(_("impulsado"))
        if book: estados.append(_("en marcadores"))
        if estados:
            lineas.append(_("Estado: {0}").format(", ".join(estados)))
        try:
            menc = _json.loads(mentions_json) if mentions_json else []
        except Exception:
            menc = []
        if menc:
            lineas.append(_("Menciona a: {0}").format(", ".join("@" + m for m in menc)))
        if cw:
            lineas.append(_("Advertencia de contenido: {0}").format(cw))
        # Filtro del servidor: en la vista de detalle (la has pedido tú) se avisa y se muestra.
        try:
            from app.services.mastodon import filtros_servidor as _fsrv
            lineas.extend(_fsrv.lineas_detalle(self._ev_filtro_toot(db_id)))
        except Exception:
            pass
        if has_poll:
            lineas.append(_("Contiene una encuesta (usa Acciones, 'Ver publicación y encuesta')."))
        if media:
            try:
                for att in _json.loads(media):
                    lineas.append(_("Adjunto ({0}): {1}").format(
                        att.get("type"), att.get("description") or _("sin descripción")))
            except Exception:
                pass
        cabecera = "\n".join(lineas)
        cuerpo = content or ""
        if uri:
            cuerpo += "\n\n" + _("Enlace: {0}").format(uri)
        return cabecera, cuerpo

    def _on_ver_publicacion(self, db_id, idx=None):
        """Diálogo 'Ver publicación': muestra la publicación o el mensaje directo completo y permite
        interactuar con TODAS las acciones disponibles para ese tipo de publicación, mediante el
        botón 'Acciones' (que reaprovecha el mismo menú contextual de la lista)."""
        if not self.active_mastodon_account_id or not db_id:
            return
        partes = self._texto_detalle_publicacion(db_id)
        if partes is None:
            speak(_("No se encontró la publicación."), interrupt=True)
            return
        cabecera, cuerpo = partes
        es_dm = (self.active_timeline_type == "direct")
        titulo = _("Ver mensaje directo") if es_dm else _("Ver publicación")
        from .mastodon_views import VerPublicacionDialog
        dlg = VerPublicacionDialog(self, titulo, cabecera, cuerpo, db_id=db_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _abrir_menu_publicacion(self, ventana, db_id):
        """Muestra el menú de acciones de la publicación (el MISMO del menú contextual de la lista)
        sobre 'ventana' (el diálogo Ver publicación). Resuelve el índice a partir del db_id para no
        depender de cuál esté enfocado en la lista (con el diálogo modal abierto, GetFocusedItem
        puede no ser fiable). Así el botón Acciones del diálogo funciona de verdad."""
        idx = None
        try:
            idx = self.lista_items_db_ids.index(db_id)
        except (ValueError, AttributeError):
            idx = self.lista_items.GetFocusedItem()
        if idx is None or idx < 0:
            return
        # Fijar ESTE elemento como el enfocado en la lista: varias acciones (responder, citar,
        # favorito, traducir, describir imagen...) actúan sobre el elemento enfocado, y con el
        # diálogo modal abierto el foco de la lista no es fiable. Solo FOCUSED (no SELECTED, para no
        # disparar sonidos ni recargas del panel).
        try:
            self.lista_items.SetItemState(idx, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
        except Exception:
            pass
        self._on_context_menu(None, _popup_target=ventana, _idx_forzado=idx)

    def _on_ver_encuesta(self, db_id: str):
        """Abre un diálogo con el texto de la publicación y su encuesta (ver y votar)."""
        if not self.active_mastodon_account_id:
            return
        from app.core.html_utils import html_a_texto
        row = db.query_one("SELECT content FROM mastodon_statuses WHERE id = ?", (db_id,))
        contenido = html_a_texto(row[0]) if row and row[0] else ""
        from .mastodon_lists import PollViewDialog
        dlg = PollViewDialog(self, self.active_mastodon_account_id, db_id, contenido)
        dlg.ShowModal()
        dlg.Destroy()
