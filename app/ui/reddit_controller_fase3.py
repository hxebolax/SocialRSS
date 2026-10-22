# -*- coding: utf-8 -*-
"""Controlador de Reddit: personalización de comunidades (mixin de UI, Fase 3).

Puntos de entrada del menú &Reddit → Personalización: wiki, etiquetas (flair), emojis y widgets de la
barra lateral. Cada acción determina el subreddit (el del buffer activo si es 'sub:<nombre>', o se
pide por nombre) y abre el diálogo correspondiente.
"""
import logging

log = logging.getLogger(__name__)


class _Fase3UIMixin:
    def _pedir_subreddit_texto(self, callback, titulo):
        """Determina el subreddit: usa el del buffer activo si es 'sub:<n>'; si no, lo pide por
        nombre. Luego llama callback(nombre). A diferencia de la moderación, estas herramientas
        funcionan en cualquier comunidad (aunque editar exija permisos)."""
        actual = self._subreddit_del_buffer()  # de _ModeracionUIMixin
        if actual:
            callback(actual)
            return
        import wx
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self.f, _("Nombre del subreddit (sin r/):"), titulo)
        if dlg.ShowModal() == wx.ID_OK:
            nombre = dlg.GetValue().strip().lstrip("r/").lstrip("/")
            if nombre:
                callback(nombre)
        dlg.Destroy()

    def abrir_wiki(self):
        from .reddit_fase3_dialogs import RedditWikiDialog
        self._pedir_subreddit_texto(lambda sub: self._abrir_dialogo(RedditWikiDialog, sub),
                                    _("Wiki de un subreddit"))

    def abrir_flairs(self):
        from .reddit_fase3_dialogs import RedditFlairsDialog
        self._pedir_subreddit_texto(lambda sub: self._abrir_dialogo(RedditFlairsDialog, sub),
                                    _("Etiquetas de un subreddit"))

    def abrir_emojis(self):
        from .reddit_fase3_dialogs import RedditEmojisDialog
        self._pedir_subreddit_texto(lambda sub: self._abrir_dialogo(RedditEmojisDialog, sub),
                                    _("Emojis de un subreddit"))

    def abrir_widgets(self):
        from .reddit_fase3_dialogs import RedditWidgetsDialog
        self._pedir_subreddit_texto(lambda sub: self._abrir_dialogo(RedditWidgetsDialog, sub),
                                    _("Barra lateral de un subreddit"))

    def _abrir_dialogo(self, clase, subreddit):
        dlg = clase(self.f, self.f.active_reddit_account_id, subreddit)
        dlg.ShowModal()
        dlg.Destroy()
