# -*- coding: utf-8 -*-
"""RSS: qué hace Intro sobre un artículo y vista previa COMPLETA en el panel (mixin de MainFrame).

- Intro: preferencia `rss.enter_action` = "navegador" (abrir el enlace, lo de siempre y el valor
  por defecto) | "lector" (abrir el lector limpio) | "preguntar" (elegir cada vez). Vale igual en
  los buffers de RSS y en la bandeja de entrada.
- Vista previa (el panel que se alcanza con Tab): muchos feeds solo traen un resumen y el artículo
  entero solo se descargaba al abrir el lector limpio. Con `rss.fulltext_en_vista_previa` (activado
  por defecto), si el panel está visible y el artículo nunca se ha descargado, se pide en segundo
  plano cuando te quedas un momento en él, y el panel se actualiza en silencio si sigues ahí. Se
  guarda como en el lector, así que la próxima vez sale al instante (y el lector lo reutiliza).
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import logging

import wx

from app.core.config_manager import config
from app.core.database import db

log = logging.getLogger(__name__)

ACCIONES_INTRO_RSS = ("navegador", "lector", "preguntar")

# Espera (ms) en un artículo antes de pedir su texto completo para la vista previa: al pasar
# rápido por la lista no se lanza ninguna descarga.
_ESPERA_DESCARGA_MS = 900


class _Mf_rss_vista:
    # ------------------------------------------------------------------ Intro
    def _abrir_articulo_rss_intro(self, db_id, index):
        """Intro sobre un artículo. Devuelve True si ya lo ha resuelto (lector o cancelado) y
        False si hay que seguir con lo de siempre (abrir el enlace en el navegador)."""
        accion = config.get("rss", "enter_action", "navegador")
        if accion not in ACCIONES_INTRO_RSS or accion == "navegador":
            return False
        if accion == "preguntar":
            from .accessible_controls import elegir_opcion
            eleccion = elegir_opcion(
                self, _("¿Cómo quieres abrir este artículo?"), _("Abrir artículo"),
                [_("Abrir el &lector limpio"), _("Abrir en el &navegador")])
            if eleccion == 1:
                return False
            if eleccion != 0:
                return True   # Cancelar o Escape (elegir_opcion añade su botón): nada
        # En la bandeja la fila no es de RSS: no se refresca (marcar leído va por su lado).
        self._on_ver_articulo_limpio(db_id)
        return True

    # ------------------------------------------------------------------ vista previa
    def _texto_vista_previa_rss(self, db_id) -> str:
        """Texto COMPLETO de la vista previa de un artículo (buffer RSS y bandeja): cabecera y
        todo el contenido guardado, más un aviso si se está pidiendo el artículo completo."""
        from app.ui.main_frame import formatear_tiempo_relativo
        from app.core.html_utils import html_a_texto
        row = db.query_one("""
            SELECT a.title, f.title, a.published_at, a.content, a.link, a.favorite, a.media_url,
                   a.ai_summary
            FROM rss_articles a JOIN rss_feeds f ON a.feed_id = f.id
            WHERE a.id = ?""", (db_id,))
        if not row:
            return ""
        title, feed, date, content, link, fav, media_url, summary = row
        partes = [_("Título: {0}").format(title or ""), _("Feed: {0}").format(feed or "")]
        if date:
            partes.append(_("Fecha: {0}").format(formatear_tiempo_relativo(str(date))))
        if link:
            partes.append(_("Enlace: {0}").format(link))
        if fav:
            partes.append(_("Marcado como Favorito"))
        if media_url:
            partes.append(_("Enclosure multimedia: {0}").format(media_url))
        texto = "\n".join(partes) + "\n"
        # Datos de Podcast 2.0 (duración, temporada/episodio, transcripción, capítulos...).
        try:
            from app.services.rss.rss_service import rss_service
            episodio = rss_service.texto_detalles_episodio(db_id)
        except Exception:
            episodio = ""
        if episodio:
            texto += episodio + "\n"
        if summary:
            texto += "\n" + _("--- Resumen IA ---") + f"\n{summary}\n"
        if self._pedir_completo_para_vista(db_id):
            texto += "\n[" + _("Descargando el artículo completo de la web...") + "]\n"
        texto += "\n" + html_a_texto(content or "")
        return texto

    def _pedir_completo_para_vista(self, db_id) -> bool:
        """True si la vista previa debe pedir el artículo completo: opción activada, panel
        visible y artículo con enlace que nunca se ha intentado descargar."""
        if not config.get("rss", "fulltext_en_vista_previa", True):
            return False
        if not (hasattr(self, "txt_detalles") and self.txt_detalles.IsShown()):
            return False
        try:
            from app.services.rss.rss_service import rss_service
            return bool(rss_service.texto_completo_pendiente(db_id))
        except Exception:
            return False

    def _mostrar_vista_previa_rss(self, db_id):
        """Vuelca la vista previa en el panel y, si hace falta, programa la descarga completa."""
        texto = self._texto_vista_previa_rss(db_id)
        self.txt_detalles.SetValue(texto)
        self.txt_detalles.SetInsertionPoint(0)
        if self._pedir_completo_para_vista(db_id):
            self._programar_completo_vista(db_id)

    def _db_id_enfocado_lista(self):
        i = self.lista_items.GetFocusedItem()
        return self.lista_items_db_ids[i] if 0 <= i < len(self.lista_items_db_ids) else None

    def _programar_completo_vista(self, db_id):
        """Pide el artículo completo si el foco SIGUE en él pasado un momento (sin red al pasar
        rápido por la lista) y no se está pidiendo ya."""
        self._vista_rss_objetivo = db_id
        temporizador = getattr(self, "_vista_rss_timer", None)
        if temporizador is not None:
            try:
                temporizador.Stop()
            except Exception:
                pass
        self._vista_rss_timer = wx.CallLater(_ESPERA_DESCARGA_MS, self._lanzar_completo_vista, db_id)

    def _lanzar_completo_vista(self, db_id):
        if not self or db_id != getattr(self, "_vista_rss_objetivo", None):
            return
        if self._db_id_enfocado_lista() != db_id or not self._pedir_completo_para_vista(db_id):
            return
        en_curso = getattr(self, "_vista_rss_en_curso", None)
        if en_curso is None:
            en_curso = self._vista_rss_en_curso = set()
        if db_id in en_curso:
            return
        en_curso.add(db_id)
        from app.services.rss.rss_service import rss_service

        def _fin(html_nuevo, err):
            en_curso.discard(db_id)
            if not self or err:
                if err:
                    log.debug(f"Vista previa: no se pudo descargar el artículo completo: {err}")
                return
            # Actualizar el panel SOLO si el foco sigue en ese artículo (en silencio: el lector
            # de pantalla no relee nada; lo nuevo se lee al entrar en el panel con Tab).
            if self._db_id_enfocado_lista() == db_id and self.txt_detalles.IsShown():
                self._refrescar_vista_previa_actual()
        rss_service.descargar_articulo_completo_async(db_id, callback=_fin)

    def _refrescar_vista_previa_actual(self):
        """Repinta la vista previa del elemento enfocado (buffer RSS o bandeja)."""
        i = self.lista_items.GetFocusedItem()
        if getattr(self, "_bandeja_activa", False):
            self._rellenar_detalle_bandeja(i)
        elif self.active_rss_account_id and 0 <= i < len(self.lista_items_db_ids):
            pos = self.txt_detalles.GetInsertionPoint()
            self.txt_detalles.SetValue(self._texto_vista_previa_rss(self.lista_items_db_ids[i]))
            try:
                self.txt_detalles.SetInsertionPoint(min(pos, self.txt_detalles.GetLastPosition()))
            except Exception:
                self.txt_detalles.SetInsertionPoint(0)
