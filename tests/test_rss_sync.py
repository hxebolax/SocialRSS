# -*- coding: utf-8 -*-
"""Sincronización de RSS con servidor: conciliación en tres vías y forma de las peticiones.

El bug que motivó estas pruebas: en The Old Reader, pedir el stream con el estado DENTRO de
la ruta (stream/contents/user/-/state/com.google/read) devuelve 404, así que nunca llegaba
nada de lo marcado como leído en la web. El estado va en el parámetro `s`.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import pytest

from app.services.rss.rss_sync import (GReaderClient, MinifluxClient, _SyncMixin,
                                       _url_clave, base_greader)


def test_base_del_servidor_tolera_lo_que_pegue_el_usuario():
    assert base_greader("https://theoldreader.com/") == "https://theoldreader.com"
    assert base_greader("https://theoldreader.com/reader/api/0") == "https://theoldreader.com"
    assert base_greader("https://theoldreader.com/reader/api/0/") == "https://theoldreader.com"
    # FreshRSS: el camino de su API SÍ forma parte de la base y no se debe recortar.
    assert base_greader("https://mi.web/api/greader.php") == "https://mi.web/api/greader.php"


# --------------------------------------------------------------------------- URLs
def test_clave_de_url_tolera_variantes():
    """El emparejamiento local<->servidor no puede romperse por http/https, barra final
    o parámetros de seguimiento: el servidor suele devolver la URL ya normalizada."""
    a = _url_clave("https://blog.example.com/post/1/")
    b = _url_clave("https://blog.example.com/post/1?utm_source=rss")
    c = _url_clave("HTTPS://Blog.Example.com/post/1#comentarios")
    assert a == b == c
    assert _url_clave("") == ""


# --------------------------------------------------------------------------- GReader
class _RespuestaFalsa:
    def __init__(self, datos=None, texto=""):
        self._datos = datos or {}
        self.text = texto
        self.status_code = 200

    def json(self):
        return self._datos

    def raise_for_status(self):
        return None


class _GReaderEspia(GReaderClient):
    """Cliente que no toca la red: registra las peticiones y devuelve datos fijos."""

    def __init__(self, items, sin_leer_ids=None):
        super().__init__("https://theoldreader.com", "u", "p")
        self._sid = "token-falso"
        self._wtoken = "w"
        self._items = items
        self._sin_leer = sin_leer_ids or []
        self.gets = []            # [(ruta, params)]
        self.enviados = []        # [(estado, poner, [ids])]

    def _get(self, ruta, params=None):
        self.gets.append((ruta, dict(params or {})))
        if ruta == "stream/contents":
            if params.get("c"):       # segunda página: fin
                return _RespuestaFalsa({"items": []})
            return _RespuestaFalsa({"items": self._items})
        if ruta == "stream/items/ids":
            return _RespuestaFalsa({"itemRefs": [{"id": i} for i in self._sin_leer]})
        return _RespuestaFalsa({}, "w")

    def marcar(self, item_ids, estado, poner):
        self.enviados.append((estado, poner, list(item_ids)))


def _item(iid, url, leido=False, destacado=False):
    cats = ["user/-/state/com.google/reading-list"]
    if leido:
        cats.append("user/-/state/com.google/read")
    if destacado:
        cats.append("user/-/state/com.google/starred")
    return {"id": iid, "alternate": [{"href": url}], "categories": cats}


def test_el_stream_va_en_el_parametro_s_y_no_en_la_ruta():
    """Regresión del 404 de The Old Reader."""
    c = _GReaderEspia([_item("i1", "https://e.com/1")])
    c.estado_reciente()
    rutas = [r for r, _p in c.gets]
    assert "stream/contents" in rutas
    assert not any("com.google" in r for r in rutas), "el estado no debe ir en la ruta"
    params = dict(c.gets[0][1])
    assert params["s"] == "user/-/state/com.google/reading-list"


def test_estado_reciente_usa_los_ids_sin_leer():
    """Con ids sin leer del servidor, mandan ellos: i2 está sin leer y el resto leídos."""
    items = [_item("i1", "https://e.com/1"), _item("i2", "https://e.com/2"),
             _item("i3", "https://e.com/3", destacado=True)]
    c = _GReaderEspia(items, sin_leer_ids=["i2"])
    est = c.estado_reciente()
    assert est[_url_clave("https://e.com/1")]["read"] is True
    assert est[_url_clave("https://e.com/2")]["read"] is False
    assert est[_url_clave("https://e.com/3")]["read"] is True
    assert est[_url_clave("https://e.com/3")]["starred"] is True


def test_estado_reciente_cae_a_categories_si_los_ids_no_cruzan():
    """Si el servidor devuelve ids en otro formato, se usa el estado de cada artículo."""
    items = [_item("i1", "https://e.com/1", leido=True), _item("i2", "https://e.com/2")]
    c = _GReaderEspia(items, sin_leer_ids=["formato-raro-999"])
    est = c.estado_reciente()
    assert est[_url_clave("https://e.com/1")]["read"] is True
    assert est[_url_clave("https://e.com/2")]["read"] is False


def test_enviar_cambios_agrupa_por_estado():
    c = _GReaderEspia([])
    n = c.enviar_cambios([
        {"item_id": "a", "campo": "read", "valor": True},
        {"item_id": "b", "campo": "read", "valor": True},
        {"item_id": "c", "campo": "read", "valor": False},
        {"item_id": "d", "campo": "starred", "valor": True},
    ])
    assert n == 4
    envios = {(e, p): ids for e, p, ids in c.enviados}
    assert envios[("user/-/state/com.google/read", True)] == ["a", "b"]
    assert envios[("user/-/state/com.google/read", False)] == ["c"]
    assert envios[("user/-/state/com.google/starred", True)] == ["d"]


def test_id_canonico_normaliza_formatos():
    """stream/items/ids puede dar decimales y stream/contents la forma larga."""
    largo = "tag:google.com,2005:reader/item/0000000000000005"
    assert GReaderClient._id_canonico(largo) == GReaderClient._id_canonico("5")
    # Los ObjectId de The Old Reader (hex de 24) se respetan tal cual, en minúsculas.
    assert GReaderClient._id_canonico("00157A17B192950B65BE3791") == "157a17b192950b65be3791"


# --------------------------------------------------------------------------- conciliación
class _ServidorFalso:
    def __init__(self, estado):
        self._estado = estado
        self.enviados = []

    def estado_reciente(self, limite=None):
        return dict(self._estado)

    def enviar_cambios(self, cambios):
        self.enviados.extend(cambios)
        return len(cambios)


class _SyncPrueba(_SyncMixin):
    """Mixin con la parte de base de datos sustituida por diccionarios en memoria."""

    def __init__(self, locales, base):
        self._locales = locales       # {url: (article_id, read, favorite)}
        self._base = base             # {article_id: (read_srv, star_srv)}
        self.updates = []             # [(ids, columna, valor)]
        self.base_guardada = []

    def _articulos_locales(self, account_id, limite=8000):
        return dict(self._locales)

    def _base_conocida(self, account_id):
        return dict(self._base)

    def _fijar_columna(self, ids, columna, valor):
        if ids:
            self.updates.append((sorted(ids), columna, valor))
        return len(ids)

    def _guardar_base(self, filas):
        self.base_guardada = list(filas)

    def _limpiar_base_huerfana(self, account_id):
        pass


def _clave(u):
    return _url_clave(u)


def test_lo_leido_en_la_web_llega_a_la_app():
    """El caso reportado: marcado como leído en The Old Reader, pendiente en la app."""
    url = "https://e.com/1"
    sync = _SyncPrueba(locales={_clave(url): ("art1", False, False)},
                       base={"art1": (False, False)})
    srv = _ServidorFalso({_clave(url): {"item_id": "i1", "read": True, "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert (["art1"], "read", 1) in sync.updates
    assert resumen["leidos_aplicados"] == 1
    assert srv.enviados == [], "no había nada que enviar"


def test_lo_leido_en_la_app_se_envia_al_servidor():
    url = "https://e.com/2"
    sync = _SyncPrueba(locales={_clave(url): ("art2", True, False)},
                       base={"art2": (False, False)})
    srv = _ServidorFalso({_clave(url): {"item_id": "i2", "read": False, "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert srv.enviados == [{"item_id": "i2", "campo": "read", "valor": True}]
    assert sync.updates == [], "en local no cambia nada"


def test_marcar_como_no_leido_en_la_web_tambien_viaja():
    url = "https://e.com/3"
    sync = _SyncPrueba(locales={_clave(url): ("art3", True, False)},
                       base={"art3": (True, False)})   # el servidor lo tenía leído
    srv = _ServidorFalso({_clave(url): {"item_id": "i3", "read": False, "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert (["art3"], "read", 0) in sync.updates


def test_primer_encuentro_no_deslee_lo_ya_leido_aqui():
    """Sin base previa, un artículo leído en la app se ENVÍA; nunca se marca como no leído."""
    url = "https://e.com/4"
    sync = _SyncPrueba(locales={_clave(url): ("art4", True, False)}, base={})
    srv = _ServidorFalso({_clave(url): {"item_id": "i4", "read": False, "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert srv.enviados == [{"item_id": "i4", "campo": "read", "valor": True}]
    assert sync.updates == []
    assert sync.base_guardada == [(1, "art4", "i4", 1, 0)]


def test_destacados_en_ambos_sentidos():
    u1, u2 = "https://e.com/5", "https://e.com/6"
    sync = _SyncPrueba(
        locales={_clave(u1): ("art5", False, False), _clave(u2): ("art6", True, True)},
        base={"art5": (False, False), "art6": (True, False)})
    srv = _ServidorFalso({
        _clave(u1): {"item_id": "i5", "read": False, "starred": True},   # destacado en la web
        _clave(u2): {"item_id": "i6", "read": True, "starred": False},   # destacado aquí
    })
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert (["art5"], "favorite", 1) in sync.updates
    assert {"item_id": "i6", "campo": "starred", "valor": True} in srv.enviados


def test_articulo_del_servidor_que_no_esta_en_local_se_ignora():
    sync = _SyncPrueba(locales={}, base={})
    srv = _ServidorFalso({_clave("https://e.com/9"): {"item_id": "i9", "read": True,
                                                      "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert sync.updates == [] and srv.enviados == []


def test_si_falla_el_envio_no_se_guarda_la_base():
    """Así el cambio se reintenta en la siguiente pasada en vez de darse por sincronizado."""
    class _SrvRoto(_ServidorFalso):
        def enviar_cambios(self, cambios):
            raise RuntimeError("500 del servidor")

    url = "https://e.com/7"
    sync = _SyncPrueba(locales={_clave(url): ("art7", True, False)},
                       base={"art7": (False, False)})
    srv = _SrvRoto({_clave(url): {"item_id": "i7", "read": False, "starred": False}})
    resumen = {}
    sync._conciliar_estado(1, srv, resumen)
    assert sync.base_guardada == []


# --------------------------------------------------------------------------- Miniflux
class _MinifluxEspia(MinifluxClient):
    def __init__(self):
        super().__init__("https://mf.example", token="t")
        self.puts = []

    def _s(self):
        espia = self

        class _S:
            def put(self, url, json=None, **kw):
                espia.puts.append((url, json))
                return _RespuestaFalsa()
        return _S()


def test_miniflux_envia_leidos_en_lote_y_destacados_uno_a_uno():
    c = _MinifluxEspia()
    n = c.enviar_cambios([
        {"item_id": 1, "campo": "read", "valor": True},
        {"item_id": 2, "campo": "read", "valor": True},
        {"item_id": 3, "campo": "read", "valor": False},
        {"item_id": 4, "campo": "starred", "valor": True},
    ])
    assert n == 4
    assert (c.base + "/v1/entries", {"entry_ids": [1, 2], "status": "read"}) in c.puts
    assert (c.base + "/v1/entries", {"entry_ids": [3], "status": "unread"}) in c.puts
    assert (c.base + "/v1/entries/4/bookmark", None) in c.puts
