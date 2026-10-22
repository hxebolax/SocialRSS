# -*- coding: utf-8 -*-
"""RSS (2026-09-26): qué hace Intro sobre un artículo y vista previa completa del panel.

- Preferencia rss.enter_action: navegador (lo de siempre) | lector | preguntar, en los buffers de
  RSS y en la bandeja.
- La vista previa mostraba solo lo que traía el feed (a menudo un resumen): ahora descarga el
  artículo completo, como el lector limpio, si el panel está visible.
- En la bandeja, abrir un artículo de "No leídos" recargaba la lista de RSS encima de la bandeja."""
from app.core.config_manager import config
from app.core.database import db
from app.core.migrations import ejecutar_migraciones
from app.ui.mf_rss_vista import _Mf_rss_vista

ACC = 5501
ART = "rssprueba:1"


class _Panel:
    def __init__(self, visible=True):
        self.visible, self.valor, self.pos = visible, "", 0

    def IsShown(self):
        return self.visible

    def SetValue(self, v):
        self.valor = v

    def SetInsertionPoint(self, p):
        self.pos = p

    def GetInsertionPoint(self):
        return self.pos

    def GetLastPosition(self):
        return len(self.valor)


class _Lista:
    def __init__(self, i=0):
        self.i = i

    def GetFocusedItem(self):
        return self.i


class _Frame(_Mf_rss_vista):
    def __init__(self, panel_visible=True):
        self.txt_detalles = _Panel(panel_visible)
        self.lista_items = _Lista(0)
        self.lista_items_db_ids = [ART]
        self.active_rss_account_id = ACC
        self.abiertos = []

    def _on_ver_articulo_limpio(self, db_id):
        self.abiertos.append(db_id)


def _preparar(contenido="<p>Solo el resumen del feed.</p>", intentado=None):
    ejecutar_migraciones()
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('rss', 'RSS', 1)")
    db.execute("INSERT OR IGNORE INTO accounts (id, name, service_id, active) VALUES (?, 'R', 'rss', 1)", (ACC,))
    db.execute("INSERT OR IGNORE INTO rss_feeds (id, account_id, title, url) VALUES (?, ?, ?, ?)",
               (ACC, ACC, "Mi periódico", "https://ejemplo.test/rss"))
    db.execute("INSERT OR REPLACE INTO rss_articles (id, feed_id, account_id, title, link, content, "
               "published_at, fulltext_at) VALUES (?,?,?,?,?,?,?,?)",
               (ART, ACC, ACC, "Titular", "https://ejemplo.test/a", contenido,
                "2026-09-26T10:00:00", intentado))


def test_intro_por_defecto_sigue_abriendo_el_navegador():
    config.set("rss", "enter_action", "navegador")
    f = _Frame()
    assert f._abrir_articulo_rss_intro(ART, 0) is False and f.abiertos == []


def test_intro_abre_el_lector_limpio():
    config.set("rss", "enter_action", "lector")
    try:
        f = _Frame()
        assert f._abrir_articulo_rss_intro(ART, 0) is True and f.abiertos == [ART]
    finally:
        config.set("rss", "enter_action", "navegador")


def test_intro_pregunta(monkeypatch):
    import app.ui.accessible_controls as ac
    config.set("rss", "enter_action", "preguntar")
    try:
        for eleccion, esperado, abiertos in ((0, True, [ART]), (1, False, []), (None, True, [])):
            monkeypatch.setattr(ac, "elegir_opcion", lambda *a, e=eleccion, **k: e)
            f = _Frame()
            assert f._abrir_articulo_rss_intro(ART, 0) is esperado
            assert f.abiertos == abiertos
    finally:
        config.set("rss", "enter_action", "navegador")


def test_vista_previa_muestra_todo_el_contenido():
    largo = "<p>" + ("Párrafo largo del artículo. " * 3000) + "FINAL</p>"
    _preparar(largo, intentado=123)          # ya intentado: no hay aviso de descarga
    texto = _Frame()._texto_vista_previa_rss(ART)
    assert texto.startswith("Título: Titular\nFeed: Mi periódico")
    assert texto.rstrip().endswith("FINAL") and "Descargando" not in texto


def test_vista_previa_pide_el_articulo_completo(monkeypatch):
    _preparar(intentado=None)
    f = _Frame()
    assert "Descargando el artículo completo" in f._texto_vista_previa_rss(ART)
    # Panel oculto u opción desactivada: nada de red.
    assert _Frame(panel_visible=False)._pedir_completo_para_vista(ART) is False
    config.set("rss", "fulltext_en_vista_previa", False)
    try:
        assert f._pedir_completo_para_vista(ART) is False
    finally:
        config.set("rss", "fulltext_en_vista_previa", True)

    # La descarga se lanza si el foco sigue en el artículo, y el panel se repinta al llegar.
    from app.services.rss.rss_service import rss_service
    pedidos = []

    def _descargar(article_id, callback, **kw):
        pedidos.append(article_id)
        db.execute("UPDATE rss_articles SET content = ?, fulltext_at = 1 WHERE id = ?",
                   ("<p>Artículo entero descargado.</p>", article_id))
        callback("<p>ok</p>", None)
    monkeypatch.setattr(rss_service, "descargar_articulo_completo_async", _descargar)
    f._vista_rss_objetivo = ART
    f._lanzar_completo_vista(ART)
    assert pedidos == [ART] and "Artículo entero descargado." in f.txt_detalles.valor
    # Si el foco ya se fue a otro artículo, no se descarga nada.
    _preparar(intentado=None)
    f.lista_items_db_ids = ["otro"]
    f._lanzar_completo_vista(ART)
    assert pedidos == [ART]


def test_bandeja_no_recarga_la_lista_de_rss_al_marcar_leido():
    from app.ui.mf_reproductor_media import _Mf_reproductor_media
    _preparar(intentado=1)
    db.execute("UPDATE rss_articles SET read = 0 WHERE id = ?", (ART,))

    class _F(_Mf_reproductor_media):
        _bandeja_activa = True
        active_timeline_type = "unread"
        lista_items_db_ids = [ART]
        recargas = 0

        def _cargar_listado_artículos(self):
            self.recargas += 1

        def _refrescar_fila_rss(self, i):
            self.recargas += 1

        def _actualizar_contadores_rss(self):
            pass
    f = _F()
    f._marcar_leido_rss(ART, 0)
    assert f.recargas == 0
    assert db.query_one("SELECT read FROM rss_articles WHERE id = ?", (ART,))[0] == 1
