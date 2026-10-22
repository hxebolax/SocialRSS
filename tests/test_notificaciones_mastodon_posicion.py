# -*- coding: utf-8 -*-
"""Notificaciones de Mastodon: la posición se recuerda por NOTIFICACIÓN, no por toot.

Reporte: en Notificaciones, al llevar el foco a la última leída y actualizar, la posición
"se iba donde le parecía". Causa: la fila apunta al toot (lista_items_db_ids) y varias
notificaciones comparten toot (tres favoritos a la misma publicación); la posición se
restauraba con .index() del toot = la PRIMERA fila de ese toot. Además, la inserción
incremental no mantenía alineada la lista paralela _notif_db_ids, con lo que el marcador
de lectura enviado al servidor apuntaba a otra notificación. Y la restauración desde el
marcador del servidor no se ejecutaba nunca (_restaurar_foco_lista dejaba apuntada la
primera fila antes de comprobar si había posición local).
"""
from app.ui.mf_telegram_chats import _Mf_telegram_chats
from app.ui.mf_acciones_toot import _Mf_acciones_toot, mastodon_service


class _ListaFalsa:
    """Doble mínimo de la lista virtual: guarda filas y foco, sin ventana real."""

    def __init__(self):
        self.filas = []
        self.foco = -1
        self._silencioso = False

    def GetItemCount(self):
        return len(self.filas)

    def GetFocusedItem(self):
        return self.foco

    def Focus(self, idx):
        self.foco = idx

    def Select(self, idx):
        self.foco = idx

    def EnsureVisible(self, idx):
        pass

    def Freeze(self):
        pass

    def Thaw(self):
        pass

    def establecer_medios(self, *a):
        pass

    def establecer_fecha(self, *a):
        pass

    def insertar_filas_lote(self, nuevas):
        # Como la lista real: las nuevas ocupan su posición final y el foco sigue al elemento.
        foco_previo = self.foco
        for pos, valores, _lectura in nuevas:
            self.filas.insert(pos, valores)
            if foco_previo >= 0 and pos <= foco_previo:
                foco_previo += 1
        self.foco = foco_previo


class _FrameFalso(_Mf_telegram_chats, _Mf_acciones_toot):
    def __init__(self):
        self.active_mastodon_account_id = 1
        self.active_timeline_type = "notifications"
        self._modo_lista = "toots"
        self._buffer_focus = {}
        self._buffer_focus_idx = {}
        self.lista_items = _ListaFalsa()
        self.lista_items_db_ids = []
        self._notif_db_ids = []
        self._nav_gen = 0

    def _buffer_key(self):
        return f"m:{self.active_mastodon_account_id}:{self.active_timeline_type}"

    def _nav_vigente(self, gen):
        return gen == self._nav_gen

    def _cols_de_campo(self, *a):
        return None

    def _col_de_campo(self, *a):
        return None


# Tres notificaciones sobre el MISMO toot (A) y una sobre otro (B), más nuevas primero.
_FILAS = [
    ("1:A", ["fav de Ana a A"], "", None, "2026-10-13T10:04", "1_104"),
    ("1:A", ["fav de Bea a A"], "", None, "2026-10-13T10:03", "1_103"),
    ("1:B", ["impulso a B"], "", None, "2026-10-13T10:02", "1_102"),
    ("1:A", ["fav de Carla a A"], "", None, "2026-10-13T10:01", "1_101"),
]


def _cargar(frame, filas):
    """Simula la carga completa del buffer (como _cargar_listado_toots)."""
    frame.lista_items.filas = [f[1] for f in filas]
    frame.lista_items_db_ids = [f[0] for f in filas]
    frame._notif_db_ids = [f[5] for f in filas]


def test_actualizar_conserva_la_notificacion_enfocada_aunque_comparta_toot():
    frame = _FrameFalso()
    _cargar(frame, _FILAS)
    frame._restaurar_foco_lista()
    # El usuario baja hasta el favorito de Carla (fila 3, mismo toot A que la fila 0).
    frame.lista_items.foco = 3
    frame._guardar_foco_buffer()
    # Actualizar = recarga completa con una notificación nueva arriba.
    nueva = ("1:C", ["mención nueva"], "", None, "2026-10-13T10:05", "1_105")
    _cargar(frame, [nueva] + _FILAS)
    frame._restaurar_foco_lista()
    # Debe seguir sobre el favorito de Carla (ahora fila 4), no saltar a la fila 1.
    assert frame.lista_items.foco == 4
    assert frame._notif_db_ids[frame.lista_items.foco] == "1_101"


def test_insercion_incremental_mantiene_alineados_los_ids_de_notificacion():
    frame = _FrameFalso()
    _cargar(frame, _FILAS)
    frame.lista_items.foco = 2
    # Llega otro favorito al MISMO toot A: antes se descartaba por tener un toot ya presente.
    nueva = ("1:A", ["fav de Dani a A"], "", None, "2026-10-13T10:05", "1_105")
    deseadas = [nueva] + [(f[0], None, "", None, f[4], f[5]) for f in _FILAS]
    insertados = frame._insertar_items_incremental(deseadas)
    assert insertados == 1
    assert frame._notif_db_ids == ["1_105", "1_104", "1_103", "1_102", "1_101"]
    assert len(frame._notif_db_ids) == len(frame.lista_items_db_ids)
    # El foco siguió al mismo elemento (el impulso a B).
    assert frame._notif_db_ids[frame.lista_items.foco] == "1_102"


def test_primera_visita_aplica_el_marcador_del_servidor(monkeypatch):
    frame = _FrameFalso()
    _cargar(frame, _FILAS)
    monkeypatch.setattr(
        "app.ui.mf_acciones_toot.db.query",
        lambda sql, params=(): [("1_104", "104"), ("1_103", "103"), ("1_102", "102"), ("1_101", "101")])
    monkeypatch.setattr(mastodon_service, "obtener_marcador_async",
                        lambda acc, tl, callback=None: callback("102", None))
    habia = frame._buffer_key() in frame._buffer_focus
    frame._restaurar_foco_lista()  # primera visita: deja apuntada la fila 0
    frame._restaurar_marcador_lectura(habia)
    assert frame.lista_items.foco == 2
    assert frame._buffer_focus[frame._buffer_key()] == "1_102"
    # El marcador del servidor es el punto de partida de "solo avanzar".
    assert frame._marker_enviados[(1, "notifications")] == "102"


def test_con_posicion_local_no_se_arrastra_al_marcador(monkeypatch):
    frame = _FrameFalso()
    _cargar(frame, _FILAS)
    frame._buffer_focus[frame._buffer_key()] = "1_103"
    monkeypatch.setattr(mastodon_service, "obtener_marcador_async",
                        lambda acc, tl, callback=None: callback("101", None))
    habia = frame._buffer_key() in frame._buffer_focus
    frame._restaurar_foco_lista()
    frame._restaurar_marcador_lectura(habia)
    assert frame.lista_items.foco == 1
