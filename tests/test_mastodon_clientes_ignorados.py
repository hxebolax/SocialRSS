# -*- coding: utf-8 -*-
"""Lo que se oculta en Mastodon se puede recuperar: clientes ignorados y conversaciones silenciadas.

Sin red ni ventanas en pantalla: los diálogos se construyen sin mostrarlos, para no robar el foco
al lector de pantalla del equipo donde se ejecuten las pruebas."""
import pytest

from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.services.mastodon import clientes_ignorados, mastodon_social
from app.services.mastodon.mastodon_service import mastodon_service

wx = pytest.importorskip("wx")

ACC = 992
DB_ID = f"{ACC}:7001"


@pytest.fixture(autouse=True)
def _limpio():
    config.set("mastodon", "ignored_clients", [])
    yield
    config.set("mastodon", "ignored_clients", [])


@pytest.fixture
def toot():
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO accounts (id, service_id, name, active, position) "
               "VALUES (?, 'mastodon', 'prueba clientes', 1, 99)", (ACC,))
    db.execute("DELETE FROM mastodon_statuses WHERE id = ?", (DB_ID,))
    db.execute("INSERT INTO mastodon_statuses (id, account_id, status_id, uri, application) "
               "VALUES (?, ?, ?, ?, ?)", (DB_ID, ACC, "7001", "https://m.test/7001", "Cliente de prueba"))
    yield DB_ID
    db.execute("DELETE FROM mastodon_statuses WHERE id = ?", (DB_ID,))


@pytest.fixture
def sincrono(monkeypatch):
    def _sinc(fn, callback=None, **kw):
        try:
            res, err = fn(None), None
        except Exception as e:
            res, err = None, e
        if callback:
            callback(res, err)
    monkeypatch.setattr(mastodon_social.task_manager, "start_task", _sinc)


# ------------------------------------------------------------ lista de clientes
def test_un_cliente_ignorado_se_puede_recuperar():
    assert clientes_ignorados.ignorar("Mona")
    assert clientes_ignorados.esta_ignorado("Mona")
    assert clientes_ignorados.dejar_de_ignorar("Mona")
    assert not clientes_ignorados.esta_ignorado("Mona")
    assert clientes_ignorados.listar() == []


def test_no_duplica_ni_falla_con_lo_que_no_esta():
    assert clientes_ignorados.ignorar("Mona")
    assert not clientes_ignorados.ignorar("Mona")
    assert not clientes_ignorados.ignorar("")
    assert clientes_ignorados.listar() == ["Mona"]
    assert not clientes_ignorados.dejar_de_ignorar("Otro")
    assert not clientes_ignorados.esta_ignorado("")


# ------------------------------------------------------------ conversaciones
class _ClienteFalso:
    def __init__(self, muted):
        self.muted = muted
        self.llamadas = []

    def status(self, status_id):
        return {"id": status_id, "muted": self.muted}

    def status_mute(self, status_id):
        self.llamadas.append(("mute", status_id))

    def status_unmute(self, status_id):
        self.llamadas.append(("unmute", status_id))


@pytest.mark.parametrize("muted,accion,queda", [(False, "mute", True), (True, "unmute", False)])
def test_la_conversacion_alterna_segun_el_servidor(monkeypatch, sincrono, toot, muted, accion, queda):
    falso = _ClienteFalso(muted)
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda _acc: falso)
    res = {}
    mastodon_service.silenciar_conversacion_async(ACC, toot, callback=lambda r, e: res.update(r=r, e=e))
    assert res == {"r": queda, "e": None}
    assert falso.llamadas == [(accion, "7001")]


def test_la_conversacion_admite_una_orden_explicita(monkeypatch, sincrono, toot):
    falso = _ClienteFalso(True)
    monkeypatch.setattr(mastodon_service, "obtener_cliente", lambda _acc: falso)
    res = {}
    mastodon_service.silenciar_conversacion_async(ACC, toot, True, callback=lambda r, e: res.update(r=r))
    assert res["r"] is True
    assert falso.llamadas == [("mute", "7001")]


# ------------------------------------------------------------ acciones de la ventana
@pytest.fixture
def ventana(monkeypatch):
    from app.ui import mf_autores_listas as modulo

    clase = next(v for k, v in vars(modulo).items()
                 if isinstance(v, type) and hasattr(v, "_on_ignorar_cliente"))

    class _Ventana(clase):
        def __init__(self):
            self.active_mastodon_account_id = ACC
            self._bandeja_activa = False
            self.repintados = 0

        def _cargar_listado_toots(self, cache_only=True):
            self.repintados += 1

    v = _Ventana()
    v.avisos = []
    v.respuesta = True
    monkeypatch.setattr(modulo, "notificar", v.avisos.append)
    monkeypatch.setattr(modulo, "speak", lambda *a, **k: None)
    monkeypatch.setattr(modulo, "confirmar", lambda *a, **k: v.respuesta)
    return v


def test_ignorar_pide_confirmacion(ventana, toot):
    ventana.respuesta = False
    ventana._on_ignorar_cliente(toot)
    assert clientes_ignorados.listar() == []
    assert ventana.repintados == 0


def test_la_misma_accion_deshace_el_ignorado(ventana, toot):
    ventana._on_ignorar_cliente(toot)
    assert clientes_ignorados.listar() == ["Cliente de prueba"]
    ventana.respuesta = False  # recuperar no debe depender de la confirmación
    ventana._on_ignorar_cliente(toot)
    assert clientes_ignorados.listar() == []
    assert ventana.repintados == 2


@pytest.mark.parametrize("cuenta,bandeja", [(None, False), (ACC, True)])
def test_no_repinta_mastodon_sobre_otra_lista(ventana, toot, cuenta, bandeja):
    """_cargar_listado_toots vacía la lista: con otra red o la bandeja delante no se debe llamar."""
    ventana.active_mastodon_account_id = cuenta
    ventana._bandeja_activa = bandeja
    ventana._on_ignorar_cliente(toot)
    assert clientes_ignorados.listar() == ["Cliente de prueba"]
    assert ventana.repintados == 0


# ------------------------------------------------------------ diálogo y menús
@pytest.fixture(scope="module")
def app():
    return wx.App(False)


@pytest.fixture
def padre(app):
    f = wx.Frame(None)
    yield f
    f.Destroy()


def test_el_dialogo_recupera_los_clientes(padre, monkeypatch):
    from app.ui import mastodon_filtros_dialogs as modulo
    clientes_ignorados.ignorar("Uno")
    clientes_ignorados.ignorar("Dos")
    avisos = []
    monkeypatch.setattr(modulo, "notificar", avisos.append)

    dlg = modulo.ClientesIgnoradosDialog(padre)
    try:
        assert dlg.lista.GetItemCount() == 2
        assert not dlg.cambiado
        dlg._on_quitar(None)
        assert clientes_ignorados.listar() == ["Dos"]
        assert dlg.cambiado and dlg.lista.GetItemCount() == 1
        dlg._on_quitar(None)
        assert clientes_ignorados.listar() == []
        assert not dlg.b_quitar.IsEnabled(), "sin clientes no queda nada que quitar"
        assert len(avisos) == 2
    finally:
        dlg.Destroy()


def test_las_etiquetas_nuevas_se_clasifican_en_los_menus(app, monkeypatch):
    from app.ui import menu_organizador as mo
    monkeypatch.setattr(mo, "activo", lambda: True)

    barra = wx.MenuBar()
    menu = wx.Menu()
    for t in ("Listas de Mastodon...", "Filtros del servidor...", "Dominios bloqueados...",
              "Clientes ignorados...", "Seguir un hashtag...", "Hashtags que sigues...",
              "Descubrir: hashtags en tendencia...", "Descubrir: enlaces en tendencia..."):
        menu.Append(wx.ID_ANY, t)
    barra.Append(menu, "&Mastodon")
    mo.organizar_menubar(barra)
    primer_nivel = [it.GetItemLabelText() for it in menu.GetMenuItems() if not it.IsSeparator()]
    assert "Clientes ignorados..." not in primer_nivel, "debe ir con los filtros, en su submenú"
