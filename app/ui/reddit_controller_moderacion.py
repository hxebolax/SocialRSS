# -*- coding: utf-8 -*-
"""Controlador de Reddit: moderación de subreddits (mixin de UI, Fase 2).

Puntos de entrada del menú &Reddit → Moderación y del menú contextual del timeline. Cada acción
elige primero el subreddit a moderar (de los que el usuario modera) y abre el diálogo correspondiente
o ejecuta la acción de moderación sobre el elemento enfocado. Toda la red va por task_manager.
"""
import logging

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.reddit import reddit_service

log = logging.getLogger(__name__)


class _ModeracionUIMixin:
    # ------------------------------------------------------------------
    # Elegir el subreddit a moderar
    # ------------------------------------------------------------------
    def _subreddit_del_buffer(self):
        """Nombre del subreddit del buffer activo si es 'sub:<nombre>[:sort]'; si no, ''."""
        tipo = str(getattr(self.f, "active_reddit_type", "") or "")
        partes = tipo.split(":")
        if len(partes) >= 2 and partes[0] == "sub":
            return partes[1]
        return ""

    def _pedir_subreddit_que_modero(self, callback, titulo=None):
        """Carga la lista de subreddits que el usuario modera y pide elegir uno; luego llama
        callback(nombre_subreddit). Si solo modera uno, lo usa directamente. Preselecciona el
        subreddit del buffer activo si está entre ellos."""
        acc = self.f.active_reddit_account_id
        titulo = titulo or _("Elegir subreddit")
        speak(_("Cargando tus subreddits..."), interrupt=True)

        def _bg(ctx):
            return reddit_service.subreddits_que_modero(acc)

        def _fin(subs, err):
            if err:
                notificar(_("No se pudo obtener la lista de subreddits que moderas: {0}").format(err))
                return
            subs = subs or []
            if not subs:
                from .accessible_controls import mostrar_info
                mostrar_info(self.f, _("No moderas ningún subreddit con esta cuenta."),
                             _("Sin subreddits"))
                return
            if len(subs) == 1:
                callback(subs[0])
                return
            import wx
            actual = self._subreddit_del_buffer()
            d = wx.SingleChoiceDialog(self.f, _("Elige el subreddit:"), titulo, subs)
            if actual in subs:
                d.SetSelection(subs.index(actual))
            if d.ShowModal() == wx.ID_OK:
                callback(subs[d.GetSelection()])
            d.Destroy()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------
    # Entradas del menú Moderación
    # ------------------------------------------------------------------
    def abrir_colas_moderacion(self):
        from .reddit_moderacion_dialogs import RedditModColaDialog

        def _abrir(sub):
            dlg = RedditModColaDialog(self.f, self.f.active_reddit_account_id, sub)
            dlg.ShowModal()
            dlg.Destroy()
        self._pedir_subreddit_que_modero(_abrir, _("Cola de moderación"))

    def abrir_usuarios_subreddit(self):
        from .reddit_moderacion_dialogs import RedditModUsuariosDialog

        def _abrir(sub):
            dlg = RedditModUsuariosDialog(self.f, self.f.active_reddit_account_id, sub)
            dlg.ShowModal()
            dlg.Destroy()
        self._pedir_subreddit_que_modero(_abrir, _("Usuarios del subreddit"))

    def abrir_motivos_eliminacion(self):
        from .reddit_moderacion_dialogs import RedditModMotivosDialog

        def _abrir(sub):
            dlg = RedditModMotivosDialog(self.f, self.f.active_reddit_account_id, sub)
            dlg.ShowModal()
            dlg.Destroy()
        self._pedir_subreddit_que_modero(_abrir, _("Motivos de eliminación"))

    def abrir_config_subreddit(self):
        from .reddit_moderacion_dialogs import RedditModConfigDialog

        def _abrir(sub):
            dlg = RedditModConfigDialog(self.f, self.f.active_reddit_account_id, sub)
            dlg.ShowModal()
            dlg.Destroy()
        self._pedir_subreddit_que_modero(_abrir, _("Configuración del subreddit"))

    def ver_registro_moderacion(self):
        """Muestra las últimas acciones del registro de moderación en un cuadro de solo lectura."""
        def _abrir(sub):
            acc = self.f.active_reddit_account_id
            speak(_("Cargando el registro..."), interrupt=True)

            def _bg(ctx):
                return reddit_service.modlog(acc, sub, limit=100)

            def _fin(res, err):
                if err:
                    notificar(_("No se pudo cargar el registro: {0}").format(err))
                    return
                if not res:
                    from .accessible_controls import mostrar_info
                    mostrar_info(self.f, _("No hay acciones recientes de moderación."),
                                 _("Registro de r/{0}").format(sub))
                    return
                from datetime import datetime, timezone
                lineas = []
                for a in res:
                    fecha = ""
                    try:
                        fecha = datetime.fromtimestamp(a.get("created_utc", 0),
                                                       timezone.utc).strftime("%d/%m/%Y %H:%M")
                    except Exception:
                        pass
                    detalle = a.get("details") or a.get("description") or ""
                    lineas.append(_("{0} — u/{1}: {2} {3}").format(
                        fecha, a.get("mod", ""), a.get("action", ""), detalle).strip())
                from .accessible_controls import mostrar_info
                mostrar_info(self.f, "\n".join(lineas), _("Registro de r/{0}").format(sub))
            task_manager.start_task(_bg, callback=_fin, pass_context=True)
        self._pedir_subreddit_que_modero(_abrir, _("Registro de moderación"))

    # ------------------------------------------------------------------
    # Moderación del elemento enfocado (menú contextual del timeline)
    # ------------------------------------------------------------------
    def menu_moderacion_para(self, menu, fullname, tipo):
        """Rellena un (sub)menú con acciones de moderación sobre el elemento (por fullname). Las
        acciones se ejecutan directamente; si el usuario no es moderador, el servicio lo notifica."""
        import wx
        es_comment = (tipo == "comment") or str(fullname).startswith("t1_")

        def add(label, fn, ok_msg):
            it = menu.Append(wx.ID_ANY, label)
            self.f.Bind(wx.EVT_MENU,
                        lambda e: self._accion_async(lambda a: fn(a),
                                                     tras_exito=lambda r: speak(ok_msg, interrupt=True)),
                        it)

        add(_("Aprobar"), lambda a: reddit_service.mod_aprobar(a, fullname), _("Aprobado."))
        add(_("Eliminar"), lambda a: reddit_service.mod_eliminar(a, fullname), _("Eliminado."))
        add(_("Eliminar como spam"), lambda a: reddit_service.mod_eliminar(a, fullname, spam=True),
            _("Marcado como spam."))
        menu.AppendSeparator()
        add(_("Distinguir"), lambda a: reddit_service.mod_distinguir(a, fullname, how="yes"),
            _("Distinguido."))
        if not es_comment:
            add(_("Distinguir y fijar arriba"),
                lambda a: reddit_service.mod_distinguir(a, fullname, how="yes", sticky=True),
                _("Distinguido y fijado."))
        add(_("Quitar distinción"), lambda a: reddit_service.mod_distinguir(a, fullname, how="no"),
            _("Distinción quitada."))
        add(_("Bloquear comentarios"), lambda a: reddit_service.mod_bloquear(a, fullname, True),
            _("Bloqueado."))
        add(_("Desbloquear comentarios"), lambda a: reddit_service.mod_bloquear(a, fullname, False),
            _("Desbloqueado."))
        add(_("Ignorar reportes"), lambda a: reddit_service.mod_ignorar_reportes(a, fullname, True),
            _("Reportes ignorados."))

        it = menu.Append(wx.ID_ANY, _("Enviar mensaje de eliminación..."))
        self.f.Bind(wx.EVT_MENU, lambda e: self._mensaje_eliminacion_mod(fullname), it)

    def _mensaje_eliminacion_mod(self, fullname):
        import wx
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Motivo que se enviará al autor:"),
                                    _("Mensaje de eliminación"), multiline=True)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        texto = dlg.GetValue().strip()
        dlg.Destroy()
        if not texto:
            return
        self._accion_async(lambda a: reddit_service.mod_mensaje_eliminacion(a, fullname, texto),
                           tras_exito=lambda r: speak(_("Mensaje enviado."), interrupt=True))
