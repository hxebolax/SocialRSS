# -*- coding: utf-8 -*-
"""Datos de Telegram para la interfaz GTK: árbol, listados desde la caché y detalle (sin red ni pantalla)."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_telegram as dt  # noqa: E402

ACC = 9801
ANA, GRUPO, CANAL = 4001, -100401, -100402


def _mensaje(mid, quien, texto, saliente=0, medio="", dur=0, responde=0, reacciones="", chat=ANA):
    db.execute("INSERT OR REPLACE INTO telegram_messages (id, account_id, chat_id, message_id, sender_name, "
               "sender_username, text, date, outgoing, media_type, duration, reply_to, reactions) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (f"{ACC}:{chat}:{mid}", ACC, chat, mid, quien, "@ana" if not saliente else "", texto,
                f"2026-10-26T08:{mid:02d}:00", saliente, medio, dur, responde, reacciones))


@pytest.fixture
def cuenta():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('telegram', 'Telegram', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) "
               "VALUES (?, '@yo_tg', 'telegram', 1, 92)", (ACC,))
    db.execute("DELETE FROM telegram_chats WHERE account_id = ?", (ACC,))
    db.execute("DELETE FROM telegram_messages WHERE account_id = ?", (ACC,))
    for cid, titulo, tipo, no_leidos, ultimo, fecha, fijado, silenciado in (
            (ANA, "Ana Pérez", "private", 2, "¿Quedamos?", "2026-10-26T09:00:00", 0, 0),
            (GRUPO, "Amigos", "supergroup", 0, "Foto", "2026-10-26T08:00:00", 1, 0),
            (CANAL, "Noticias", "channel", 5, "Última hora", "2026-10-25T20:00:00", 0, 1)):
        db.execute("INSERT OR REPLACE INTO telegram_chats (id, account_id, chat_id, title, chat_type, unread_count, "
                   "last_message, last_message_date, pinned, muted, verified) VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                   (f"{ACC}:{cid}", ACC, cid, titulo, tipo, no_leidos, ultimo, fecha, fijado, silenciado))
    _mensaje(1, "Ana Pérez", "Hola")
    _mensaje(2, "Yo", "Muy bien", saliente=1, responde=1, reacciones="👍 1")
    _mensaje(3, "Ana Pérez", "", medio="voice", dur=75)
    _mensaje(4, "Ana Pérez", "¿Quedamos?")
    db.execute("UPDATE telegram_chats SET last_read_message_id = 2 WHERE id = ?", (f"{ACC}:{ANA}",))
    return ACC


def test_arbol_de_cuentas_y_buffers(cuenta):
    raiz = dt.construir_nodos()
    assert raiz.servicio == "telegram" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == cuenta)
    assert [h.timeline for h in mia.hijos] == ["chats", "unread"]
    assert all(h.listable and h.servicio == "telegram" for h in mia.hijos)


def test_chats_fijados_primero_y_con_su_estado(cuenta):
    filas = dt.listar(cuenta, "chats")
    assert [f.autor for f in filas] == ["Amigos", "Ana Pérez", "Noticias"]
    por = {f.autor: f for f in filas}
    assert "fijado" in por["Amigos"].titulo and "silenciado" in por["Noticias"].titulo
    assert "2 sin leer" in por["Ana Pérez"].titulo and not por["Ana Pérez"].leido
    assert por["Ana Pérez"].extra["chat_id"] == ANA and por["Ana Pérez"].extra["tipo"] == "chat"


def test_chats_sin_leer_solo_los_pendientes(cuenta):
    assert [f.autor for f in dt.listar(cuenta, "unread")] == ["Ana Pérez", "Noticias"]


def test_conversacion_en_orden_cronologico_y_marca_lo_nuevo(cuenta):
    filas = dt.listar(cuenta, f"chat:{ANA}")
    assert [f.extra["message_id"] for f in filas] == [1, 2, 3, 4]
    assert [f.extra["nuevo"] for f in filas] == [False, False, True, True]
    assert filas[1].titulo.startswith("Yo: Muy bien") and "Respuesta" in filas[1].titulo and "👍 1" in filas[1].titulo
    assert "Nota de voz 1:15" in filas[2].titulo and filas[2].extra["reproducible"] and filas[2].audio
    assert dt.seleccion_inicial(filas) == 2     # el primer mensaje nuevo


def test_sin_nuevos_la_seleccion_cae_en_el_ultimo(cuenta):
    dt.guardar_leido_hasta(cuenta, ANA, 4)
    filas = dt.listar(cuenta, f"chat:{ANA}")
    assert not any(f.extra["nuevo"] for f in filas) and dt.seleccion_inicial(filas) == 3


def test_ids_y_puntos_de_lectura(cuenta):
    assert dt.id_de_chat(f"chat:{GRUPO}") == GRUPO and dt.id_de_chat("chats") is None
    assert dt.ultimo_mensaje_id(cuenta, ANA) == 4 and dt.mensaje_mas_antiguo_id(cuenta, ANA) == 1
    assert dt.titulo_chat(cuenta, ANA) == "Ana Pérez" and dt.titulo_chat(cuenta, 1) == "1"


def test_detalle_de_mensaje_y_de_chat(cuenta):
    filas = dt.listar(cuenta, f"chat:{ANA}")
    titulo, meta, _enlace, cuerpo = dt.detalle(filas[1])
    assert titulo == "Yo" and "En respuesta a Ana Pérez: Hola" in cuerpo and "Reacciones: 👍 1" in cuerpo
    assert "sin descargar" in dt.detalle(filas[2])[3]
    chat = dt.listar(cuenta, "chats")[1]
    assert "Intro" in dt.detalle(chat)[3]
