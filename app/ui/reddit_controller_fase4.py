# -*- coding: utf-8 -*-
"""Controlador de Reddit: funciones avanzadas (mixin de UI, Fase 4).

Puntos de entrada del menú &Reddit para modmail, hilos en directo, colecciones y borradores. El
modmail se abre sobre "all" (todas las comunidades que moderas); las colecciones piden el subreddit
(reutilizan el helper de la Fase 3); los hilos en directo y los borradores no necesitan subreddit.
"""
import logging

log = logging.getLogger(__name__)


class _Fase4UIMixin:
    def abrir_modmail(self):
        from .reddit_fase4_dialogs import RedditModmailDialog
        dlg = RedditModmailDialog(self.f, self.f.active_reddit_account_id, "all")
        dlg.ShowModal()
        dlg.Destroy()

    def abrir_live(self):
        from .reddit_fase4_dialogs import RedditLiveDialog
        dlg = RedditLiveDialog(self.f, self.f.active_reddit_account_id)
        dlg.ShowModal()
        dlg.Destroy()

    def abrir_collections(self):
        from .reddit_fase4_dialogs import RedditCollectionsDialog
        self._pedir_subreddit_texto(  # de _Fase3UIMixin
            lambda sub: self._abrir_dialogo(RedditCollectionsDialog, sub),
            _("Colecciones de un subreddit"))

    def abrir_drafts(self):
        from .reddit_fase4_dialogs import RedditDraftsDialog
        dlg = RedditDraftsDialog(self.f, self.f.active_reddit_account_id)
        dlg.ShowModal()
        dlg.Destroy()
