# -*- coding: utf-8 -*-
"""Compartir mensajes de Telegram: varios mensajes, varios destinos y opciones (2026-11-03)."""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.core.database import db
from app.services.telegram.telegram_compartir import _CompartirMixin, motivo_error_compartir


class ChatWriteForbidden(Exception):
    """Mismo nombre que el error de pyrogram: el servicio lo traduce por el nombre de la clase."""


class _Msg:
    def __init__(self, chat_id, mid):
        self.chat_id, self.id = chat_id, mid


class _ClienteFalso:
    def __init__(self, prohibidos=()):
        self.llamadas = []
        self.prohibidos = set(prohibidos)

    async def send_message(self, chat_id, texto, **kw):
        self.llamadas.append(("texto", chat_id, texto, kw))
        return _Msg(chat_id, 0)

    async def forward_messages(self, chat_id, from_chat_id, ids, **kw):
        if chat_id in self.prohibidos:
            raise ChatWriteForbidden("CHAT_WRITE_FORBIDDEN")
        self.llamadas.append(("reenvio", chat_id, list(ids), kw))
        return [_Msg(chat_id, i) for i in ids]


class _Servicio(_CompartirMixin):
    def __init__(self, cliente):
        self.cliente = cliente
        self.guardados = []

    async def _cliente(self, account_id):
        return self.cliente

    def _run(self, coro, timeout=60):
        return asyncio.run(coro)

    def _guardar_mensaje(self, account_id, msg):
        self.guardados.append(msg)


@pytest.fixture(autouse=True)
def _sin_pausa(monkeypatch):
    import app.services.telegram.telegram_compartir as mod
    monkeypatch.setattr(mod, "_PAUSA_ENTRE_DESTINOS", 0)


@pytest.fixture(scope="module", autouse=True)
def _bd():
    from app.core.migrations import ejecutar_migraciones
    ejecutar_migraciones()
    for cuenta in (91, 92):
        db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) "
                   "VALUES (?, ?, 'telegram', 1)", (cuenta, f"+3460000{cuenta}"))


def test_varios_mensajes_a_varios_destinos_en_orden_cronologico():
    cli = _ClienteFalso()
    srv = _Servicio(cli)
    res = srv.compartir_mensajes(1, 50, [30, 10, 20], [
        {"chat_id": 7, "titulo": "Ana"}, {"chat_id": "me", "titulo": "Mensajes guardados"}])
    assert [r["ok"] for r in res] == [True, True]
    reenvios = [c for c in cli.llamadas if c[0] == "reenvio"]
    assert [c[1] for c in reenvios] == [7, "me"]
    assert all(c[2] == [10, 20, 30] for c in reenvios)       # orden cronológico
    assert reenvios[0][3]["hide_sender_name"] is None          # reenvío normal: se ve el autor
    assert len(srv.guardados) == 6                             # se guardan en la caché local


def test_copia_sin_autor_quitar_pies_comentario_silencioso_y_tema():
    cli = _ClienteFalso()
    srv = _Servicio(cli)
    srv.compartir_mensajes(1, 50, [5], [{"chat_id": 9, "titulo": "Foro", "tema": 44}],
                           ocultar_pies=True, comentario="  Mira esto  ", silencioso=True)
    texto, reenvio = cli.llamadas
    assert texto[:3] == ("texto", 9, "Mira esto")                 # el comentario va ANTES
    assert texto[3]["message_thread_id"] == 44
    kw = reenvio[3]
    assert kw["hide_sender_name"] is True                          # quitar pies implica sin autor
    assert kw["hide_captions"] is True
    assert kw["disable_notification"] is True
    assert kw["message_thread_id"] == 44


def test_un_destino_que_falla_no_detiene_los_demas_y_explica_el_motivo():
    cli = _ClienteFalso(prohibidos={8})
    srv = _Servicio(cli)
    progreso = []
    res = srv.compartir_mensajes(1, 50, [1], [
        {"chat_id": 8, "titulo": "Canal ajeno"}, {"chat_id": 7, "titulo": "Ana"}],
        al_progresar=lambda n, t, r: progreso.append((n, t, r["ok"])))
    assert res[0]["ok"] is False
    assert "permiso para escribir" in res[0]["error"]
    assert res[1]["ok"] is True
    assert progreso == [(1, 2, False), (2, 2, True)]


def test_programado_no_se_guarda_como_enviado_y_lotes_de_100():
    cli = _ClienteFalso()
    srv = _Servicio(cli)
    cuando = datetime.now() + timedelta(hours=1)
    srv.compartir_mensajes(1, 50, list(range(1, 251)), [{"chat_id": 7, "titulo": "Ana"}],
                           programar=cuando)
    reenvios = [c for c in cli.llamadas if c[0] == "reenvio"]
    assert [len(c[2]) for c in reenvios] == [100, 100, 50]
    assert reenvios[0][3]["schedule_date"] == cuando
    assert srv.guardados == []


def test_sin_mensajes_es_un_error():
    with pytest.raises(ValueError):
        _Servicio(_ClienteFalso()).compartir_mensajes(1, 50, [], [{"chat_id": 7}])


def test_motivos_legibles():
    class SlowmodeWait(Exception):
        value = 30
    assert "30" in motivo_error_compartir(SlowmodeWait())
    assert motivo_error_compartir(RuntimeError("algo raro")) == "algo raro"


def test_album_completo_desde_la_cache():
    for mid, grupo in ((1, None), (2, "g1"), (3, "g1"), (4, "g1"), (5, None)):
        db.execute("INSERT OR REPLACE INTO telegram_messages (id, account_id, chat_id, message_id, "
                   "media_group_id) VALUES (?, ?, ?, ?, ?)", (f"91:77:{mid}", 91, 77, mid, grupo))
    srv = _Servicio(_ClienteFalso())
    assert srv.ids_del_album(91, 77, [3, 5]) == [2, 3, 4, 5]
    assert srv.ids_del_album(91, 77, [1]) == [1]


def test_destinos_filtros_y_busqueda_sin_tildes():
    from app.ui.telegram_compartir_dialog import (cargar_destinos, filtrar_destinos,
                                                  etiqueta_destino, GUARDADOS)
    datos = [(f"92:{c}", 92, c, t, tipo, u, 0, "", fecha, pin) for c, t, tipo, u, fecha, pin in (
        (1, "María López", "private", "maria", "2026-11-01", 0),
        (2, "Grupo NVDA", "supergroup", "", "2026-11-03", 0),
        (3, "Noticias", "channel", "noticias", "2026-10-01", 1),
        (4, "BotPadre", "bot", "botpadre", "2026-09-01", 0))]
    for fila in datos:
        db.execute("INSERT OR REPLACE INTO telegram_chats (id, account_id, chat_id, title, chat_type, "
                   "username, unread_count, last_message, last_message_date, pinned) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", fila)
    db.execute("INSERT INTO telegram_contacts (account_id, user_id, nombre, username) "
               "VALUES (?, ?, ?, ?)", (92, 99, "Pepe Contacto", "pepe"))
    db.execute("INSERT INTO telegram_contacts (account_id, user_id, nombre, username) "
               "VALUES (?, ?, ?, ?)", (92, 1, "María (contacto repetido)", "maria"))
    d = cargar_destinos(92)
    claves = [x["clave"] for x in d]
    # Guardados primero, luego el fijado, luego por fecha; los contactos sin chat al final, sin duplicar.
    assert claves == [GUARDADOS, "3", "2", "1", "4", "99"]
    assert [x["clave"] for x in filtrar_destinos(d, "maria")] == ["1"]
    assert [x["clave"] for x in filtrar_destinos(d, "@noti")] == ["3"]
    assert [x["clave"] for x in filtrar_destinos(d, "", {"group", "supergroup"})] == ["2"]
    assert [x["clave"] for x in filtrar_destinos(d, "", {"private", "contacto"})] == [GUARDADOS, "1", "99"]
    assert etiqueta_destino(d[1]) == "Noticias, canal, @noticias"
