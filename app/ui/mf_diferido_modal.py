# -*- coding: utf-8 -*-
"""MainFrame (mixin): repintado de la lista principal DIFERIDO mientras hay un diálogo modal.

Con un diálogo modal delante (el compositor sobre todo), la lista principal está tapada y no se
puede usar. Aun así, cada toot que llegaba por el streaming y cada tick del temporizador la
repintaban: una consulta del buffer entero a la base de datos, la evaluación de los filtros del
servidor fila a fila, los "hace X", los contadores... todo en el HILO DE LA INTERFAZ, el mismo que
procesa las teclas del compositor. Con una ráfaga de novedades (por ejemplo, la recuperación tras
reconectar el streaming) la escritura se atascaba más de un segundo y el lector de pantalla se
quedaba esperando a la ventana (reporte de usuario: "con el compositor abierto se bloquea y escribe
mayúsculas por minúsculas").

Ahora, mientras haya un modal abierto, esas actualizaciones NO tocan la lista: los datos siguen
entrando en la base de datos (el streaming los guarda desde su hilo) y solo se apunta que hay que
ponerse al día. Al cerrar el diálogo (la ventana principal se reactiva) o en el siguiente tick sin
modal, se repinta UNA vez el buffer activo desde la caché.

Metodos: _modal_delante, _diferir_por_modal, _ponerse_al_dia_tras_modal
"""
import logging
import wx

log = logging.getLogger(__name__)


class _Mf_diferido_modal:
    def _modal_delante(self):
        """True si hay un diálogo MODAL abierto en la aplicación. Se busca entre las ventanas de
        primer nivel (en vez de mirar si la principal está deshabilitada) porque, con la ventana
        oculta en la bandeja o en la interfaz invisible, wx no la deshabilita al abrir el modal."""
        try:
            return any(isinstance(w, wx.Dialog) and w.IsModal() for w in wx.GetTopLevelWindows())
        except Exception:
            return False

    def _diferir_por_modal(self, borrado=False):
        """Apunta que la lista principal quedó sin repintar por un modal. 'borrado' indica que
        entre lo pendiente hay borrados (exigen recargar desde caché, no solo insertar)."""
        self._pendiente_tras_modal = True
        if borrado:
            self._borrado_tras_modal = True

    def _cb_sync_diferido_por_modal(self, statuses, error):
        """Callback (hilo de la interfaz) de una sincronización a la BD lanzada con un modal
        delante: si trajo algo, queda pendiente; y si el modal ya se cerró mientras llegaba la
        respuesta, se repinta ya (si no, esperaría al siguiente evento o tick)."""
        if error or not statuses:
            return
        self._diferir_por_modal()
        self._ponerse_al_dia_tras_modal()

    def _ponerse_al_dia_tras_modal(self):
        """Repinta UNA vez el buffer activo de Mastodon con lo que llegó mientras había un modal.
        No hace nada si no hay nada pendiente o si sigue habiendo un modal delante."""
        if not getattr(self, "_pendiente_tras_modal", False) or self._modal_delante():
            return
        self._pendiente_tras_modal = False
        borrado = getattr(self, "_borrado_tras_modal", False)
        self._borrado_tras_modal = False
        if getattr(self, "_cargando_buffers", False) or getattr(self, "_bandeja_activa", False):
            return  # el cierre de la carga repinta solo; la bandeja tiene su propio sondeo
        if not getattr(self, "active_mastodon_account_id", None):
            return
        try:
            if borrado:
                self._cargar_listado_toots(cache_only=True)
            else:
                self._insertar_items_incremental(self._filas_mastodon_actual())
        except Exception as e:
            log.debug(f"Puesta al día de la lista tras cerrar un diálogo modal: {e}")
