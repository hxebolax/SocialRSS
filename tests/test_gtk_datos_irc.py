# -*- coding: utf-8 -*-
"""Datos de IRC para la interfaz GTK y comandos de barra (sin red ni pantalla)."""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones

pytest.importorskip("gi")
from app.gtk import datos_irc as di  # noqa: E402
from app.services.irc import irc_comandos  # noqa: E402
from app.services.irc.irc_common import IRCError  # noqa: E402
from app.services.irc.irc_service import irc_service  # noqa: E402

ACC = 9901


@pytest.fixture
def red():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('irc', 'IRC', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active, position) "
               "VALUES (?, 'FakeNet', 'irc', 1, 98)", (ACC,))
    db.execute("DELETE FROM irc_channels WHERE account_id = ?", (ACC,))
    db.execute("DELETE FROM irc_messages WHERE account_id = ?", (ACC,))
    db.execute("INSERT INTO irc_channels (id, account_id, name, topic, unread_count, joined, es_privado, last_message, "
               "last_message_date) VALUES (?,?,?,?,?,?,?,?,?)",
               (f"{ACC}|#nvda", ACC, "#NVDA", "Lector de pantalla", 2, 1, 0, "ana: hola", "2026-10-26T10:00:00"))
    db.execute("INSERT INTO irc_channels (id, account_id, name, unread_count, joined, es_privado, last_message, "
               "last_message_date) VALUES (?,?,?,?,?,?,?,?)",
               (f"{ACC}|ana", ACC, "ana", 0, 0, 1, "adiós", "2026-10-26T09:00:00"))
    datos = [("#nvda", "ana", "Buenas", "msg", 0, 0, 0), ("#nvda", "yo_irc", "Hola", "msg", 0, 1, 1),
             ("#nvda", "carl", "", "join", 0, 0, 1), ("#nvda", "bob", "yo_irc: ¿me oyes?", "msg", 1, 0, 0),
             ("#nvda", "ana", "se ríe", "action", 0, 0, 0), ("*", "NickServ", "Registra tu apodo", "notice", 0, 0, 0)]
    for i, (canal, nick, texto, tipo, mencion, propio, leido) in enumerate(datos):
        db.execute("INSERT INTO irc_messages (account_id, channel, nick, text, tipo, date, es_propio, highlight, leido) "
                   "VALUES (?,?,?,?,?,?,?,?,?)", (ACC, canal, nick, texto, tipo, f"2026-10-26T10:0{i}:00", propio, mencion, leido))
    return ACC


def test_arbol_de_redes_y_buffers(red):
    raiz = di.construir_nodos()
    assert raiz.servicio == "irc" and not raiz.listable
    mia = next(c for c in raiz.hijos if c.account_id == red)
    assert [h.timeline for h in mia.hijos][:3] == ["channels", "privates", "server"]
    assert all(h.listable and h.servicio == "irc" for h in mia.hijos)
    assert "desconectada" in mia.titulo          # sin cliente no hay conexión


def test_buffers_extra_se_abren_y_cierran(red):
    n = di.nodo_extra(red, di.clave_chat("#NVDA"), "#NVDA")
    assert n.timeline == "chat:#nvda" and n.cerrable and di.nombre_de_chat(n.timeline) == "#nvda"
    assert di.nodo_extra(red, "chat:#nvda", "#NVDA") is n
    di.cerrar_extra(n)
    assert n not in next(c for c in di.construir_nodos().hijos if c.account_id == red).hijos
    assert di.nombre_de_chat("channels") is None


def test_lista_de_canales_y_privados(red):
    (canal,) = di.listar(red, "channels")
    assert canal.extra["tipo"] == "chat" and canal.extra["chat"] == "#NVDA" and canal.leido is False
    assert "2 sin leer" in canal.titulo and "ana: hola" in canal.titulo and "Lector de pantalla" in canal.titulo
    (privado,) = di.listar(red, "privates")
    assert privado.extra["privado"] is True and privado.leido is True
    assert "Pulsa Intro" in di.detalle(canal)[3]


def test_conversacion_cronologica_con_menciones_y_propios(red):
    filas = di.listar(red, "chat:#nvda")
    textos = [f.titulo for f in filas]
    assert textos[0] == "Nuevo. ana: Buenas"
    assert textos[1] == "Yo: Hola"
    assert textos[2] == "carl ha entrado en el canal"
    assert "te mencionan" in textos[3] and textos[3].startswith("Nuevo. ")
    assert textos[4] == "Nuevo. * ana se ríe"
    assert di.listar(red, "chat:#nvda")[1].extra["propio"] is True
    assert di.detalle(filas[0])[3] == "ana: Buenas"


def test_entradas_y_salidas_se_pueden_ocultar(red):
    irc_service.fijar_mostrar_eventos(red, "#NVDA", False)
    try:
        assert not any("ha entrado" in f.titulo for f in di.listar(red, "chat:#nvda"))
    finally:
        irc_service.fijar_mostrar_eventos(red, "#NVDA", True)
    assert any("ha entrado" in f.titulo for f in di.listar(red, "chat:#nvda"))


def test_avisos_del_servidor(red):
    (aviso,) = di.listar(red, "server")
    assert aviso.titulo.endswith("Aviso de NickServ: Registra tu apodo") and aviso.extra["texto"] == "Registra tu apodo"


def test_marcar_leido_y_eliminar_chat(red):
    di.marcar_leido(red, "#nvda")
    assert di.listar(red, "channels")[0].leido is True
    assert all(f.leido for f in di.listar(red, "chat:#nvda"))
    di.eliminar_chat(red, "#nvda")
    assert di.listar(red, "channels") == [] and di.listar(red, "chat:#nvda") == []


def test_ultimo_ajeno_para_anunciar(red):
    assert di.ultimo_ajeno(red, "#nvda") == ("ana", "se ríe", "action")


class _Servicio:
    """Registra qué llamadas hace un comando, sin red."""

    def __init__(self, monkeypatch):
        self.llamadas = []
        for nombre in ("enviar_action", "enviar_mensaje", "enviar_notice", "abrir_privado", "unirse_canal",
                       "salir_canal", "reentrar_canal", "cambiar_nick", "cambiar_tema", "comando_crudo", "fijar_ausente",
                       "volver_disponible", "invitar", "llamar_puerta", "ctcp_ping", "ctcp_a", "fijar_modo_canal",
                       "hacerse_operador", "enviar_wallops", "accion_usuario_canal", "banear_mascara", "quitar_baneo",
                       "fijar_mis_modos", "fijar_metadata"):
            monkeypatch.setattr(irc_service, nombre, lambda *a, _n=nombre: self.llamadas.append((_n,) + a))
        monkeypatch.setattr(irc_service, "whowas_legible", lambda acc, nick: f"whowas {nick}")


@pytest.fixture
def servicio(monkeypatch):
    return _Servicio(monkeypatch)


def test_comandos_basicos(servicio):
    c = irc_comandos.ejecutar_comando
    assert c(1, "#a", "/me saluda") is None
    c(1, "#a", "/msg ana hola ana")
    c(1, "#a", "/join #b")
    c(1, "#a", "/part")
    c(1, "#a", "/part #c")
    c(1, "#a", "/nick nuevo")
    c(1, "#a", "/topic otro tema")
    assert servicio.llamadas == [
        ("enviar_action", 1, "#a", "saluda"), ("enviar_mensaje", 1, "ana", "hola ana"), ("unirse_canal", 1, "#b"),
        ("salir_canal", 1, "#a"), ("salir_canal", 1, "#c"), ("cambiar_nick", 1, "nuevo"),
        ("cambiar_tema", 1, "#a", "otro tema")]


def test_comandos_de_canal_y_modos(servicio):
    c = irc_comandos.ejecutar_comando
    c(1, "#a", "/mode +m")
    c(1, "#a", "/mode #b +k clave")
    c(1, "#a", "/kick pepe fuera de aquí")
    c(1, "#a", "/invite luis")
    c(1, "#a", "/invite luis #z")
    assert servicio.llamadas == [
        ("fijar_modo_canal", 1, "#a", "+m", ""), ("fijar_modo_canal", 1, "#b", "+k", "clave"),
        ("accion_usuario_canal", 1, "#a", "pepe", "kick", "fuera de aquí"), ("invitar", 1, "#a", "luis"),
        ("invitar", 1, "#z", "luis")]


def test_comandos_que_devuelven_algo(servicio):
    c = irc_comandos.ejecutar_comando
    assert c(1, "#a", "/query ana") == {"privado": "ana"}
    assert c(1, "#a", "/clear") == {"limpiar": True}
    assert "/join" in c(1, "*", "/help")["texto"]
    assert c(1, "#a", "/whowas pepe")["texto"] == "whowas pepe"


def test_comandos_con_error_de_uso(servicio):
    for linea in ("/foo", "/msg ana", "/join", "/kick", "/ctcp ana", "/oper solo"):
        with pytest.raises(IRCError):
            irc_comandos.ejecutar_comando(1, "#a", linea)
    assert servicio.llamadas == []
