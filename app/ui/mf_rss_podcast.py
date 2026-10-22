# -*- coding: utf-8 -*-
"""MainFrame (mixin): podcasts y artículos de RSS — añadir episodios a la cola del reproductor
de podcasts, recuperar episodios antiguos de un feed desde el archivo de Internet y ver las
versiones anteriores de un artículo."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.rss.rss_service import rss_service

log = logging.getLogger(__name__)


class _Mf_rss_podcast:
    def _on_encolar_podcast(self, db_id):
        from app.services.media_player import cola_podcasts
        fila = db.query_one("SELECT title FROM rss_articles WHERE id = ?", (db_id,))
        titulo = (fila[0] if fila else "") or _("Episodio")
        if cola_podcasts.encolar(db_id, titulo):
            n = len(cola_podcasts.listar())
            speak(_("Añadido a la cola de podcasts, posición {0}.").format(n), interrupt=True)
        else:
            speak(_("Ya estaba en la cola de podcasts."), interrupt=True)

    def _on_recuperar_archivados(self, feed_id):
        from .accessible_controls import confirmar
        fila = db.query_one("SELECT title FROM rss_feeds WHERE id = ?", (feed_id,))
        titulo = (fila[0] if fila else "") or _("este feed")
        if not confirmar(self, _(
                "Se buscarán en el archivo de Internet (Wayback Machine) copias antiguas de «{0}» "
                "y se añadirán los episodios que ya no están en el feed. Entran como leídos y sin "
                "avisos. Puede tardar un par de minutos.\n\n¿Continuar?").format(titulo),
                _("Recuperar episodios antiguos")):
            return
        speak(_("Buscando copias antiguas del feed en el archivo de Internet..."), interrupt=True)

        def _bg(ctx):
            return rss_service.recuperar_episodios_archivados(feed_id, ctx=ctx)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudieron recuperar episodios: {0}").format(err or ""))
                return
            if not res["capturas"]:
                notificar(_("El archivo de Internet no tiene copias de este feed."))
            elif res["nuevos"]:
                notificar(_("Recuperados {0} episodios antiguos de {1} copias del feed.").format(
                    res["nuevos"], res["leidas"]))
                try:
                    self._cargar_navegacion()
                except Exception:
                    pass
            else:
                notificar(_("No hay episodios antiguos que no tengas ya ({0} copias revisadas).").format(
                    res["leidas"]))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_versiones_articulo(self, db_id):
        from .rss_versiones_dialog import mostrar_versiones
        mostrar_versiones(self, db_id)
