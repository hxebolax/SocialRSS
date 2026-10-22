# -*- coding: utf-8 -*-
"""Teclas visibles en los menús y bloque de acciones comunes a todas las redes.

Lo importante aquí es que las tres capas convivan: anotar teclas, añadir el submenú común y la
organización OPCIONAL en subcategorías. Ninguna debe estropear a las otras, ni con los menús
planos de siempre ni con los organizados.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import pytest

wx = pytest.importorskip("wx")


@pytest.fixture(scope="module")
def app():
    return wx.App(False)


@pytest.fixture
def frame(app):
    f = wx.Frame(None)
    yield f
    f.Destroy()


def _etiquetas(menu, sangria=0):
    """[(profundidad, etiqueta visible)] de un menú, recorriendo sus submenús."""
    salida = []
    for it in menu.GetMenuItems():
        if it.IsSeparator():
            continue
        salida.append((sangria, it.GetItemLabelText()))
        if it.GetSubMenu() is not None:
            salida.extend(_etiquetas(it.GetSubMenu(), sangria + 1))
    return salida


# --------------------------------------------------------------------- teclas
def test_anota_la_tecla_de_las_acciones_conocidas(frame):
    from app.ui.menu_teclas import anotar_menu
    from app.ui.shortcut_manager import shortcut_manager

    menu = wx.Menu()
    for t in ("Responder", "Citar toot", "Ver conversación", "Traducir toot",
              "Opción sin tecla conocida"):
        menu.Append(wx.ID_ANY, t)
    anotar_menu(menu)
    visibles = dict.fromkeys(e for _d, e in _etiquetas(menu))
    esperado = f"Responder ({shortcut_manager.combo_legible('pub_responder')})"
    assert esperado in visibles
    assert f"Citar toot ({shortcut_manager.combo_legible('pub_citar')})" in visibles
    assert "Opción sin tecla conocida" in visibles, "lo desconocido se deja intacto"


def test_no_duplica_la_tecla_si_ya_la_lleva(frame):
    """Los items creados con _lbl_atajo ya la muestran: no debe salir dos veces."""
    from app.ui.menu_teclas import anotar_menu
    from app.ui.shortcut_manager import shortcut_manager

    combo = shortcut_manager.combo_legible("pub_responder")
    menu = wx.Menu()
    menu.Append(wx.ID_ANY, f"Responder ({combo})")
    anotar_menu(menu)
    anotar_menu(menu)   # y aunque se pase varias veces
    etiqueta = _etiquetas(menu)[0][1]
    assert etiqueta.count(combo) == 1


def test_no_toca_los_items_con_acelerador_real(frame):
    """Si el item lleva su acelerador con tabulador, wx ya lo muestra: no se toca."""
    from app.ui.menu_teclas import anotar_menu
    menu = wx.Menu()
    menu.Append(wx.ID_ANY, "Responder\tCtrl+R")
    anotar_menu(menu)
    assert "\t" in menu.GetMenuItems()[0].GetItemLabel()
    assert menu.GetMenuItems()[0].GetItemLabelText() == "Responder"


def test_en_chats_no_se_anotan_las_teclas_de_publicacion(frame):
    """'Responder' en un chat no se hace con la tecla de responder a publicaciones."""
    from app.ui.menu_teclas import anotar_menu
    menu = wx.Menu()
    menu.Append(wx.ID_ANY, "Responder")
    menu.Append(wx.ID_ANY, "Actualizar")
    anotar_menu(menu, incluir_publicacion=False)
    visibles = [e for _d, e in _etiquetas(menu)]
    assert visibles[0] == "Responder", "sin tecla en un chat"
    assert visibles[1].startswith("Actualizar ("), "las globales sí se anotan"


def test_anota_dentro_de_los_submenus(frame):
    from app.ui.menu_teclas import anotar_menu
    menu = wx.Menu()
    sub = wx.Menu()
    sub.Append(wx.ID_ANY, "Responder")
    menu.AppendSubMenu(sub, "Publicar")
    anotar_menu(menu)
    assert any(e.startswith("Responder (") for _d, e in _etiquetas(menu))


# ------------------------------------------------------------------- comunes
class _FrameFalso(wx.Frame):
    """Frame con los métodos que usa el bloque de acciones comunes."""

    def __init__(self):
        super().__init__(None)
        self.llamadas = []

    def _on_actualizar_buffer_actual(self, event):
        self.llamadas.append(("actualizar", event))

    def _mostrar_dialogo_buffers_cuenta(self):      # OJO: sin parámetro de evento
        self.llamadas.append(("buffers",))

    def _cambiar_cuenta(self, paso):
        self.llamadas.append(("cuenta", paso))

    def _on_buscar_global(self, event):
        self.llamadas.append(("buscar", event))

    def _on_centro_notificaciones(self, event):
        self.llamadas.append(("notifs", event))

    def _on_crosspost(self, event):
        self.llamadas.append(("crosspost", event))

    def _on_ponte_al_dia(self, event):
        self.llamadas.append(("aldia", event))

    def _on_leer_barra_estado(self, event):
        self.llamadas.append(("estado", event))

    def _on_ver_descripcion(self, event):
        self.llamadas.append(("descripcion", event))

    def _on_preferencias(self, event):
        self.llamadas.append(("prefs", event))


@pytest.fixture
def frame_falso(app):
    f = _FrameFalso()
    yield f
    f.Destroy()


def _barra_con(titulos, frame):
    barra = wx.MenuBar()
    for t in titulos:
        m = wx.Menu()
        m.Append(wx.ID_ANY, "Algo de esta red")
        barra.Append(m, t)
    frame.SetMenuBar(barra)
    return barra


def test_las_redes_reciben_el_submenu_comun_y_los_demas_no(frame_falso):
    from app.ui.menu_teclas import anadir_acciones_comunes
    barra = _barra_con(["&Mastodon", "&RSS", "&Archivo", "H&erramientas", "A&yuda"], frame_falso)
    anadir_acciones_comunes(barra, frame_falso)
    for i in range(barra.GetMenuCount()):
        titulo = barra.GetMenuLabelText(i)
        tiene = any(e == "Acciones comunes a todas las redes"
                    for _d, e in _etiquetas(barra.GetMenu(i)))
        if titulo in ("Mastodon", "RSS"):
            assert tiene, f"{titulo} debería tener el bloque común"
        else:
            assert not tiene, f"{titulo} no es una red y no debería tenerlo"


def test_el_bloque_comun_es_un_solo_item_de_primer_nivel(frame_falso):
    """Va en submenú a propósito: el menú de la red solo crece en una entrada."""
    from app.ui.menu_teclas import anadir_acciones_comunes
    barra = _barra_con(["&Mastodon"], frame_falso)
    menu = barra.GetMenu(0)
    antes = len([i for i in menu.GetMenuItems() if not i.IsSeparator()])
    anadir_acciones_comunes(barra, frame_falso)
    despues = len([i for i in menu.GetMenuItems() if not i.IsSeparator()])
    assert despues == antes + 1


def test_las_acciones_comunes_llaman_al_metodo_correcto(frame_falso):
    """Incluidos los métodos que NO reciben evento y los que llevan un valor fijo."""
    from app.ui.menu_teclas import anadir_acciones_comunes
    barra = _barra_con(["&Mastodon"], frame_falso)
    anadir_acciones_comunes(barra, frame_falso)
    sub = None
    for it in barra.GetMenu(0).GetMenuItems():
        if it.GetSubMenu() is not None:
            sub = it.GetSubMenu()
    assert sub is not None
    por_texto = {it.GetItemLabelText(): it for it in sub.GetMenuItems() if not it.IsSeparator()}
    for texto, it in por_texto.items():
        evt = wx.CommandEvent(wx.EVT_MENU.typeId, it.GetId())
        frame_falso.ProcessEvent(evt)
    hechas = {c[0] for c in frame_falso.llamadas}
    assert {"actualizar", "buffers", "cuenta", "buscar", "prefs"} <= hechas
    pasos = sorted(c[1] for c in frame_falso.llamadas if c[0] == "cuenta")
    assert pasos == [-1, 1]


def test_los_items_comunes_muestran_su_tecla(frame_falso):
    from app.ui.menu_teclas import anadir_acciones_comunes
    from app.ui.shortcut_manager import shortcut_manager
    barra = _barra_con(["&Mastodon"], frame_falso)
    anadir_acciones_comunes(barra, frame_falso)
    textos = [e for _d, e in _etiquetas(barra.GetMenu(0))]
    combo = shortcut_manager.combo_legible("actualizar_buffer")
    assert any(f"Actualizar este buffer ({combo})" == t for t in textos)


# ------------------------------------------- convivencia con las subcategorías
def test_el_organizador_respeta_el_submenu_comun_y_las_teclas(frame_falso, monkeypatch):
    """Con las subcategorías activadas, el bloque común sigue entero y en primer nivel, y los
    items anotados con su tecla se siguen clasificando bien."""
    from app.ui import menu_organizador as mo
    from app.ui.menu_teclas import anotar_menu, anadir_acciones_comunes
    monkeypatch.setattr(mo, "activo", lambda: True)

    barra = wx.MenuBar()
    menu = wx.Menu()
    for t in ("Nuevo Toot o post...", "Responder...", "Citar...", "Me gusta / favorito",
              "Listas de Mastodon...", "Filtros del servidor...", "Dominios bloqueados...",
              "Seguir un hashtag...", "Hashtags que sigues..."):
        menu.Append(wx.ID_ANY, t)
    barra.Append(menu, "&Mastodon")
    frame_falso.SetMenuBar(barra)

    anotar_menu(menu)                              # 1) teclas
    anadir_acciones_comunes(barra, frame_falso)    # 2) bloque común
    mo.organizar_menubar(barra)                    # 3) subcategorías

    primer_nivel = [it.GetItemLabelText() for it in menu.GetMenuItems() if not it.IsSeparator()]
    assert "Acciones comunes a todas las redes" in primer_nivel
    # Y las publicaciones anotadas con su tecla han acabado en su submenú temático.
    todos = _etiquetas(menu)
    assert any(d == 1 and e.startswith("Responder... (") for d, e in todos), \
        "un item con tecla debe clasificarse igual"
    assert any(e == "Publicar y responder" for _d, e in todos)
