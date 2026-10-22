# -*- coding: utf-8 -*-
"""MainFrame (mixin): autores_listas.
Metodos: _on_nota_autor, _on_destacar_autor, _destacar_acct, _on_quitar_seguidor, _quitar_seguidor_acct, _on_bloquear_dominio_autor, _on_editar_mi_perfil_mastodon, _on_programados ...
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
from app.core.task_manager import task_manager

from app.services.mastodon.mastodon_service import mastodon_service
from app.services.rss.rss_service import rss_service

from .accessible_controls import confirmar

log = logging.getLogger(__name__)


class _Mf_autores_listas:
    def _on_nota_autor(self, db_id):
        acct = self._acct_de_toot(db_id)
        if not acct:
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Nota personal sobre @{0} (vacío para borrarla):").format(acct),
                                    _("Nota sobre la cuenta"), multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()

        def _cb(res, err):
            notificar(_("Nota guardada.") if not err else _("Error: {0}").format(err))
        mastodon_service.nota_cuenta_async(self.active_mastodon_account_id, acct, texto, callback=_cb)

    def _on_destacar_autor(self, db_id, destacar):
        acct = self._acct_de_toot(db_id)
        if acct:
            self._destacar_acct(acct, destacar)

    def _destacar_acct(self, acct, destacar):
        """Destaca (o deja de destacar) una cuenta en tu perfil, por su acct."""
        if not self.active_mastodon_account_id or not acct:
            return

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Cuenta destacada en tu perfil.") if destacar else _("La cuenta ya no está destacada."))
        mastodon_service.destacar_cuenta_async(self.active_mastodon_account_id, acct.lstrip("@"), destacar, callback=_cb)

    def _on_quitar_seguidor(self, db_id):
        acct = self._acct_de_toot(db_id)
        if acct:
            self._quitar_seguidor_acct(acct)

    def _quitar_seguidor_acct(self, acct):
        """Quita a una cuenta de tus seguidores (sin bloquearla), por su acct."""
        if not self.active_mastodon_account_id or not acct:
            return
        acct = acct.lstrip("@")
        if confirmar(self, _("¿Quitar a @{0} de tus seguidores? (no se le bloquea)").format(acct),
                     _("Quitar seguidor")) != wx.ID_YES:
            return

        def _cb(res, err):
            speak(_("Listo.") if not err else _("Error: {0}").format(err), interrupt=True)
        mastodon_service.quitar_seguidor_async(self.active_mastodon_account_id, acct, callback=_cb)

    def _on_bloquear_dominio_autor(self, db_id):
        acct = self._acct_de_toot(db_id)
        dominio = acct.split("@")[-1] if "@" in acct else ""
        if not dominio:
            notificar(_("El autor es de tu propia instancia; no se puede bloquear su dominio."))
            return
        if confirmar(self, _("¿Bloquear TODO el dominio {0}? Dejarás de ver a todas las cuentas de esa instancia.").format(dominio),
                     _("Bloquear dominio")) != wx.ID_YES:
            return

        def _cb(res, err):
            notificar(_("Dominio {0} bloqueado.").format(dominio) if not err else _("Error: {0}").format(err))
        mastodon_service.bloquear_dominio_async(self.active_mastodon_account_id, dominio, True, callback=_cb)

    def _on_editar_mi_perfil_mastodon(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .mastodon_views import PerfilMastodonDialog
        dlg = PerfilMastodonDialog(self, self.active_mastodon_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_programados(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando publicaciones programadas..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("No tienes publicaciones programadas."))
                return
            opciones = ["{0} — {1}".format(p.get("scheduled_at", "")[:16].replace("T", " "),
                                           (p.get("text", "") or "")[:60]) for p in res]
            sel = wx.GetSingleChoice(_("Programadas (elige una):"), _("Publicaciones programadas"), opciones, self)
            if not sel:
                return
            p = res[opciones.index(sel)]
            accion = wx.GetSingleChoice(
                _("¿Qué quieres hacer con esta publicación programada?"),
                _("Publicación programada"),
                [_("Reprogramar (cambiar fecha y hora)"), _("Cancelar la publicación")], self)
            if not accion:
                return

            if accion.startswith(_("Reprogramar")):
                self._reprogramar_programada(p)
                return
            if confirmar(self, _("¿Cancelar esta publicación programada?"), _("Cancelar programada")) != wx.ID_YES:
                return

            def _cb2(r2, e2):
                notificar(_("Programada cancelada.") if not e2 else _("Error: {0}").format(e2))
            mastodon_service.cancelar_programado_async(self.active_mastodon_account_id, p.get("id"), callback=_cb2)
        mastodon_service.obtener_programados_async(self.active_mastodon_account_id, callback=_cb)

    def _reprogramar_programada(self, programada):
        """Pide la nueva fecha/hora y reprograma la publicación sin cancelarla."""
        import datetime as _dt
        from .accessible_controls import CustomTextEntryDialog
        # Sugerir la fecha actual de la programación, en hora local y formato editable.
        actual = (programada.get("scheduled_at") or "")[:16].replace("T", " ")
        try:
            _f = _dt.datetime.fromisoformat(programada.get("scheduled_at", "").replace("Z", "+00:00"))
            actual = _f.astimezone().strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
        dlg = CustomTextEntryDialog(
            self, _("Nueva fecha y hora (formato AAAA-MM-DD HH:MM, hora local):"),
            _("Reprogramar publicación"), value=actual)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        try:
            nueva = _dt.datetime.strptime(texto, "%Y-%m-%d %H:%M").astimezone()
        except ValueError:
            notificar(_("Fecha no válida. Usa el formato AAAA-MM-DD HH:MM, por ejemplo 2026-07-15 09:30."))
            return
        if nueva <= _dt.datetime.now().astimezone() + _dt.timedelta(minutes=5):
            notificar(_("La nueva fecha debe ser al menos 5 minutos en el futuro."))
            return

        def _cb(res, err):
            if err:
                notificar(_("No se pudo reprogramar: {0}").format(err))
            else:
                notificar(_("Publicación reprogramada para {0}.").format(nueva.strftime("%d/%m/%Y %H:%M")))
        mastodon_service.reprogramar_programado_async(
            self.active_mastodon_account_id, programada.get("id"), nueva, callback=_cb)

    def _on_seguir_hashtag(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Hashtag a seguir (con o sin #):"), _("Seguir hashtag"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        tag = dlg.GetValue().strip().lstrip("#")
        dlg.Destroy()
        if not tag:
            return

        def _cb(res, err):
            speak(_("Ahora sigues el hashtag {0}.").format(tag) if not err else _("Error: {0}").format(err), interrupt=True)
        mastodon_service.seguir_hashtag_async(self.active_mastodon_account_id, tag, True, callback=_cb)

    def _on_hashtags_seguidos(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No sigues ningún hashtag."), interrupt=True)
                return
            opciones = ["#" + t.get("name", "") for t in res]
            sel = wx.GetSingleChoice(_("Hashtags que sigues (elige para dejar de seguir):"), _("Hashtags seguidos"), opciones, self)
            if not sel:
                return
            nombre = sel.lstrip("#")

            def _cb2(r2, e2):
                speak(_("Has dejado de seguir {0}.").format(sel) if not e2 else _("Error: {0}").format(e2), interrupt=True)
            mastodon_service.seguir_hashtag_async(self.active_mastodon_account_id, nombre, False, callback=_cb2)
        mastodon_service.obtener_hashtags_seguidos_async(self.active_mastodon_account_id, callback=_cb)

    def _on_descubrir_tendencias_tags(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando hashtags en tendencia..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No hay hashtags en tendencia."), interrupt=True)
                return
            opciones = ["#{0} ({1})".format(t.get("name", ""), t.get("usos", 0)) for t in res]
            sel = wx.GetSingleChoice(_("Hashtags en tendencia (elige para seguirlo):"), _("Tendencias"), opciones, self)
            if not sel:
                return
            nombre = res[opciones.index(sel)].get("name", "")
            if confirmar(self, _("¿Seguir el hashtag #{0}?").format(nombre), _("Seguir hashtag")) != wx.ID_YES:
                return

            def _cb2(r2, e2):
                speak(_("Ahora sigues #{0}.").format(nombre) if not e2 else _("Error: {0}").format(e2), interrupt=True)
            mastodon_service.seguir_hashtag_async(self.active_mastodon_account_id, nombre, True, callback=_cb2)
        mastodon_service.cargar_trending_tags_async(self.active_mastodon_account_id, callback=_cb)

    def _on_descubrir_enlaces(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando enlaces en tendencia..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                speak(_("No hay enlaces en tendencia."), interrupt=True)
                return
            opciones = [l.get("title", "") for l in res]
            sel = wx.GetSingleChoice(_("Enlaces en tendencia (elige uno):"), _("Tendencias"), opciones, self)
            if not sel:
                return
            url = res[opciones.index(sel)].get("url", "")
            if not url:
                return
            acc = wx.GetSingleChoice(_("¿Qué quieres hacer con este enlace?"), _("Enlace en tendencia"),
                                     [_("Abrir en el navegador"), _("Ver publicaciones que lo comentan")], self)
            if acc == _("Abrir en el navegador"):
                abrir_url(url)
            elif acc == _("Ver publicaciones que lo comentan"):
                speak(_("Buscando publicaciones..."), interrupt=True)
                def _cb2(n, err2):
                    if err2:
                        notificar(_("Error: {0}").format(err2))
                    else:
                        notificar(_("{0} publicaciones que comentan el enlace (búscalas en tus buffers).").format(n or 0))
                mastodon_service.timeline_de_enlace_async(self.active_mastodon_account_id, url, callback=_cb2)
        mastodon_service.cargar_trending_links_async(self.active_mastodon_account_id, callback=_cb)

    def _on_buscar_usuarios(self, event):
        """Busca usuarios por nombre, @usuario o usuario@instancia (como TWBlue/Mona) y muestra los
        resultados en la lista de cuentas, con sus acciones (ver perfil, cronología, seguir, DM...)."""
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nombre, @usuario o usuario@instancia a buscar:"),
                                  _("Buscar usuarios en Mastodon"))
        q = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not q:
            return
        speak(_("Buscando usuarios..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                notificar(_("Sin resultados para «{0}».").format(q))
                return
            self._mostrar_cuentas_mastodon(_("Resultados de búsqueda: {0}").format(q), res or [])
        mastodon_service.buscar_cuentas_async(self.active_mastodon_account_id, q, callback=_cb)

    def _on_directorio(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando el directorio de perfiles..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_cuentas_mastodon(_("Directorio de perfiles"), res or [])
        mastodon_service.cargar_directorio_async(self.active_mastodon_account_id, callback=_cb)

    def _on_sugerencias_mastodon(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando cuentas sugeridas..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            self._mostrar_cuentas_mastodon(_("Cuentas sugeridas"), res or [], permitir_descartar=True)
        mastodon_service.cargar_sugerencias_async(self.active_mastodon_account_id, callback=_cb)

    def _on_dominios_bloqueados(self, event):
        if not self.active_mastodon_account_id:
            notificar(_("Active primero una cuenta de Mastodon."))
            return
        speak(_("Cargando dominios bloqueados..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            opciones = list(res or []) + [_("[Bloquear un dominio nuevo...]")]
            sel = wx.GetSingleChoice(_("Dominios bloqueados (elige uno para desbloquearlo, o añade uno nuevo):"),
                                     _("Dominios bloqueados"), opciones, self)
            if not sel:
                return
            if sel == opciones[-1]:
                from .accessible_controls import CustomTextEntryDialog
                d = CustomTextEntryDialog(self, _("Dominio a bloquear (por ejemplo, ejemplo.social):"), _("Bloquear dominio"))
                nuevo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
                d.Destroy()
                if nuevo:
                    mastodon_service.bloquear_dominio_async(self.active_mastodon_account_id, nuevo, True,
                                                            callback=lambda r, e: speak(_("Dominio bloqueado.") if not e else _("Error: {0}").format(e), interrupt=True))
            else:
                if confirmar(self, _("¿Desbloquear el dominio {0}?").format(sel), _("Desbloquear dominio")) == wx.ID_YES:
                    mastodon_service.bloquear_dominio_async(self.active_mastodon_account_id, sel, False,
                                                            callback=lambda r, e: speak(_("Dominio desbloqueado.") if not e else _("Error: {0}").format(e), interrupt=True))
        mastodon_service.obtener_dominios_bloqueados_async(self.active_mastodon_account_id, callback=_cb)

    def _on_accion_cuenta(self, user_id, acct, accion):
        """Ejecuta una acción sobre una cuenta desde un buffer de cuentas."""
        cuenta = self.active_mastodon_account_id
        if accion == "follow_toggle":
            # Sin relación previa conocida: intentamos seguir; si ya se sigue, la API
            # devolverá el estado. Para alternar de forma fiable usamos relationships.
            def _bg(ctx):
                client = mastodon_service.obtener_cliente(cuenta)
                rel = client.account_relationships(user_id)
                if rel and rel[0].get("following"):
                    client.account_unfollow(user_id)
                    return False
                client.account_follow(user_id)
                return True
            def _fin(res, err):
                if err:
                    notificar(_("No se pudo completar: {0}").format(err))
                else:
                    self._invalidar_cache_cuentas()
                    speak(_("Ahora sigues a {0}.").format(acct) if res else _("Has dejado de seguir a {0}.").format(acct), interrupt=True)
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
            return

        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar: {0}").format(err))
                return
            msgs = {"mute": _("Cuenta silenciada."), "unmute": _("Silencio retirado."),
                    "block": _("Cuenta bloqueada."), "unblock": _("Cuenta desbloqueada.")}
            speak(msgs.get(accion, _("Hecho.")), interrupt=True)
            # Esas acciones cambian las listas de cuentas: olvidar su caché para que se reobtengan.
            self._invalidar_cache_cuentas()
            # Refrescar la lista actual (el elemento puede desaparecer)
            self._cargar_listado_toots(cache_only=False)
        mastodon_service.accion_cuenta_async(cuenta, user_id, accion, callback=_fin)

    def _on_silenciar_o_bloquear_autor(self, db_id, accion):
        """Silencia o bloquea al autor del toot (resolviendo por su acct)."""
        if not self.active_mastodon_account_id:
            return
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        if not row or not row[0]:
            notificar(_("No se encontró el autor."))
            return
        duracion, notifs = None, True
        if accion == "mute":
            # Diálogo propio: duración del silencio y si se ocultan también sus notificaciones.
            from .mastodon_silenciar_dialog import SilenciarCuentaDialog
            dlg = SilenciarCuentaDialog(self, row[0])
            r = dlg.ShowModal()
            duracion, notifs = dlg.get_opciones()
            dlg.Destroy()
            if r != wx.ID_OK:
                return
        else:
            from .accessible_controls import confirmar
            if confirmar(self, _("¿Seguro que quieres bloquear a {0}?").format(row[0]),
                         _("Confirmar")) != wx.ID_YES:
                return
        speak(_("Procesando..."), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo completar: {0}").format(err))
            else:
                notificar(_("Autor silenciado.") if accion == "mute" else _("Autor bloqueado."))
                self._invalidar_cache_cuentas()
                self._cargar_listado_toots(cache_only=True)
        mastodon_service.accion_cuenta_por_acct_async(self.active_mastodon_account_id, row[0], accion, callback=_fin,
                                                      duracion=duracion, notificaciones=notifs)

    def _on_silenciar_conversacion(self, db_id):
        """Silencia las notificaciones de la conversación del toot."""
        if not self.active_mastodon_account_id:
            return
        speak(_("Silenciando conversación..."), interrupt=True)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo silenciar: {0}").format(err))
            else:
                notificar(_("Conversación silenciada."))
        mastodon_service.silenciar_conversacion_async(self.active_mastodon_account_id, db_id, True, callback=_fin)

    def _on_ignorar_cliente(self, db_id):
        """Añade el cliente (app) del toot a la lista de clientes ignorados."""
        row = db.query_one("SELECT application FROM mastodon_statuses WHERE id = ?", (db_id,))
        cliente = (row[0] if row else "") or ""
        if not cliente.strip():
            speak(_("Esta publicación no indica el cliente desde el que se publicó."), interrupt=True)
            return
        ignorados = list(config.get("mastodon", "ignored_clients", []))
        if cliente in ignorados:
            speak(_("Ese cliente ya está en la lista de ignorados."), interrupt=True)
            return
        ignorados.append(cliente)
        config.set("mastodon", "ignored_clients", ignorados)
        self._cargar_listado_toots(cache_only=True)
        speak(_("Se ocultarán las publicaciones del cliente {0}.").format(cliente), interrupt=True)

    # --- CACHÉ DE RELACIÓN CON AUTORES (para el menú contextual dinámico) ---
    # Guarda en memoria si sigues o no a cada autor: {(account_id, acct): bool}. Se rellena la
    # primera vez que abres el menú sobre un toot de ese autor (consulta en segundo plano, el
    # menú no espera) y se actualiza al seguir/dejar de seguir. Así el menú puede decir
    # directamente "Seguir al autor" o "Dejar de seguir al autor" sin llamadas bloqueantes.

    def _acct_autor_de(self, db_id: str):
        """Devuelve el acct (usuario@instancia) del autor de un toot, o None."""
        row = db.query_one("SELECT author_username FROM mastodon_statuses WHERE id = ?", (db_id,))
        return (row[0] or "").lstrip("@") if (row and row[0]) else None

    def _relacion_autor_cacheada(self, db_id: str):
        """True/False si se conoce la relación con el autor del toot; None si aún no."""
        acct = self._acct_autor_de(db_id)
        if not acct:
            return None
        return self._rel_autor_cache.get((self.active_mastodon_account_id, acct))

    def _precargar_relacion_autor(self, db_id: str):
        """Consulta la relación con el autor en segundo plano y la deja cacheada, para que la
        PRÓXIMA apertura del menú contextual ya muestre 'Seguir' o 'Dejar de seguir'."""
        acc = self.active_mastodon_account_id
        if not acc:
            return

        def _guardar(res, err):
            if not err and res and res.get("acct"):
                self._rel_autor_cache[(acc, res["acct"])] = bool(res.get("following"))

        mastodon_service.relacion_autor_async(acc, db_id, callback=_guardar)

    def _on_seguir_autor(self, db_id: str):
        """Alterna seguir/dejar de seguir al autor del toot enfocado."""
        if not self.active_mastodon_account_id:
            return
        speak(_("Procesando..."), interrupt=True)
        _acc = self.active_mastodon_account_id

        def _finish(res, err):
            if err or not res:
                notificar(_("No se pudo completar: {0}").format(err or ""))
            elif res.get("following"):
                speak(_("Ahora sigues a {0}.").format(res.get("display") or res.get("acct")), interrupt=True)
            else:
                speak(_("Has dejado de seguir a {0}.").format(res.get("display") or res.get("acct")), interrupt=True)
            # Actualizar la caché de relación para que el menú refleje el nuevo estado.
            if not err and res and res.get("acct"):
                self._rel_autor_cache[(_acc, res["acct"])] = bool(res.get("following"))

        mastodon_service.seguir_autor_async(self.active_mastodon_account_id, db_id, callback=_finish)

    def _player2_puede_reproducir(self) -> bool:
        """Comprueba la regla de 'una sola cosa a la vez' del segundo reproductor.
        Si ya hay algo sonando (radio/podcast/YouTube), pide confirmación para sustituirlo."""
        from app.services.media_player.player2_service import player2_service
        if player2_service.esta_activo():
            desc = player2_service.descripcion_actual()
            from .accessible_controls import confirmar
            return confirmar(
                self,
                _("Ya se está reproduciendo {0}. ¿Quieres detenerlo y reproducir lo nuevo?").format(
                    desc or _("otro audio")),
                _("Reproductor de radio, podcast y YouTube"))
        return True

    def _on_play_podcast(self, media_url: str, db_id: str):
        """Reproduce un episodio de podcast (o el audio del enlace) en el SEGUNDO
        reproductor, que recuerda la posición y respeta la prioridad de las redes."""
        if not self._player2_puede_reproducir():
            return
        row = db.query_one("SELECT title FROM rss_articles WHERE id = ?", (db_id,))
        title = row[0] if row else _("Podcast")
        speak(_("Cargando: {0}").format(title), interrupt=True)

        def _cb(ok, err):
            if not ok:
                notificar(_("No se pudo reproducir: {0}").format(err or ""))
        rss_service.reproducir_articulo_audio(db_id, callback=_cb)
