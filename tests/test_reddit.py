# -*- coding: utf-8 -*-
"""Pruebas PURAS (sin red) del cliente de Reddit.

Cubren la extracción de datos de PRAW (con objetos simulados), la resolución de buffers a llamadas
de la API, la persistencia con UPSERT + CASCADE, la purga de contenido borrado, las plantillas de
lectura y la configuración base. La red se simula con unittest.mock; la base de datos usa el
directorio temporal aislado por conftest.py.
"""
from unittest.mock import MagicMock

import pytest

from app.core import plantillas
# La migración vive en app.core.migrations; se ejecuta una vez para tener las tablas de Reddit.
from app.core.migrations import ejecutar_migraciones
from app.core.database import db
from app.services.reddit import reddit_service, HAS_PRAW
from app.services.reddit.reddit_common import es_cuerpo_borrado, user_agent, SCOPES


@pytest.fixture(scope="module", autouse=True)
def _preparar_bd():
    ejecutar_migraciones()


def _cuenta_reddit(nombre="u/tester"):
    return db.execute_insert(
        "INSERT INTO accounts (name, service_id, active, position) VALUES (?, 'reddit', 1, 0)",
        (nombre,))


def _submission_mock(**kw):
    m = MagicMock()
    defaults = dict(fullname="t3_abc", id="abc", subreddit="python", title="Título",
                    selftext="cuerpo", url="https://x", permalink="/r/python/comments/abc/",
                    created_utc=1700000000.0, score=10, upvote_ratio=0.9, num_comments=3,
                    over_18=False, spoiler=False, stickied=False, locked=False, saved=False,
                    hidden=False, clicked=False, likes=None, link_flair_text=None, is_self=True,
                    is_gallery=False, post_hint=None, domain="self.python", edited=False,
                    distinguished=None, media_metadata={}, is_video=False, media={},
                    crosspost_parent=None)
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(m, k, v)
    m.author = MagicMock(); m.author.name = kw.get("author_name", "aurora")
    return m


# --------------------------------------------------------------------------- básicos
def test_praw_disponible():
    assert HAS_PRAW is True


def test_user_agent_formato():
    ua = user_agent()
    assert ua.startswith("windows:SocialRSS:v") and "by /u/" in ua


def test_scopes_incluye_lo_esencial():
    for s in ("identity", "read", "submit", "vote", "save", "privatemessages", "subscribe"):
        assert s in SCOPES


def test_es_cuerpo_borrado():
    assert es_cuerpo_borrado("[deleted]")
    assert es_cuerpo_borrado("  [REMOVED] ")
    assert not es_cuerpo_borrado("un comentario normal")
    assert not es_cuerpo_borrado("")


# --------------------------------------------------------------------------- extracción
def test_post_a_valores():
    v = reddit_service._post_a_valores(_submission_mock(saved=True, likes=True), 1)
    assert v["id"] == "1:t3_abc"
    assert v["author"] == "aurora"
    assert v["saved"] == 1
    assert v["likes"] == 1
    assert v["is_self"] == 1


def test_post_a_valores_voto_negativo_y_none():
    assert reddit_service._post_a_valores(_submission_mock(likes=False), 1)["likes"] == 0
    assert reddit_service._post_a_valores(_submission_mock(likes=None), 1)["likes"] is None


def test_comment_a_valores():
    c = MagicMock()
    c.fullname = "t1_xyz"; c.id = "xyz"; c.link_id = "t3_abc"; c.parent_id = "t3_abc"
    c.subreddit = "python"; c.body = "hola"; c.created_utc = 1700000000.0; c.score = 5
    c.depth = 2; c.permalink = "/r/x/"; c.saved = False; c.likes = None; c.edited = False
    c.stickied = False; c.is_submitter = True; c.distinguished = None
    c.author = MagicMock(); c.author.name = "aurora"
    v = reddit_service._comment_a_valores(c, 1)
    assert v["id"] == "1:t1_xyz" and v["depth"] == 2 and v["is_submitter"] == 1


def test_mensaje_a_valores_pm_y_mencion():
    # _mensaje_a_valores distingue por type(item).__name__ == "Message", así que se usa una
    # clase real llamada Message (un MagicMock no cambia su type() al reasignar __class__).
    class _Autor:
        name = "aurora"

    class Message:
        fullname = "t4_m"; subject = "hola"; body = "texto"; new = True
        created_utc = 1700000000.0; parent_id = ""; dest = "tester"; author = _Autor()
    v = reddit_service._mensaje_a_valores(Message(), 1, "inbox")
    assert v["kind"] == "t4" and v["msg_type"] == "message" and v["new"] == 1


def test_nombre_autor_borrado():
    from app.services.reddit.reddit_common import _nombre_autor
    obj = MagicMock(); obj.author = None
    assert _nombre_autor(obj) == "[deleted]"


# --------------------------------------------------------------------------- resolución de buffers
@pytest.mark.parametrize("buffer,accede", [
    ("home", lambda c: c.front.hot),
    ("home:new", lambda c: c.front.new),
    ("popular", lambda c: c.subreddit.return_value.hot),
    ("all:top", lambda c: c.subreddit.return_value.top),
    ("sub:python", lambda c: c.subreddit.return_value.hot),
    ("sub:python:controversial", lambda c: c.subreddit.return_value.controversial),
    ("user:spez", lambda c: c.redditor.return_value.submissions.new),
    ("submitted", lambda c: c.user.me.return_value.submissions.new),
    ("saved", lambda c: c.user.me.return_value.saved),
    ("search:python", lambda c: c.subreddit.return_value.search),
])
def test_resolver_listing(buffer, accede):
    c = MagicMock()
    reddit_service._resolver_listing(c, 1, buffer, 25, None)
    assert accede(c).called


def test_objeto_por_fullname():
    c = MagicMock()
    reddit_service._objeto_por_fullname(c, "t1_x")
    assert c.comment.called
    c2 = MagicMock()
    reddit_service._objeto_por_fullname(c2, "t3_y")
    assert c2.submission.called


# --------------------------------------------------------------------------- persistencia + purga
def test_persistir_posts_y_cascade():
    acc = _cuenta_reddit("u/persist")
    v = reddit_service._post_a_valores(_submission_mock(fullname="t3_p1", id="p1"), acc)
    reddit_service._persistir_posts(acc, "home", [v], reemplazar=True)
    assert db.query_one("SELECT COUNT(*) FROM reddit_posts WHERE account_id=?", (acc,))[0] == 1
    assert db.query_one("SELECT COUNT(*) FROM reddit_timeline_posts WHERE account_id=?", (acc,))[0] == 1
    # Al borrar la cuenta, CASCADE limpia posts y relación.
    db.execute("DELETE FROM accounts WHERE id=?", (acc,))
    assert db.query_one("SELECT COUNT(*) FROM reddit_posts WHERE account_id=?", (acc,))[0] == 0
    assert db.query_one("SELECT COUNT(*) FROM reddit_timeline_posts WHERE account_id=?", (acc,))[0] == 0


def test_persistir_no_guarda_borrado():
    acc = _cuenta_reddit("u/borrado")
    v = reddit_service._post_a_valores(_submission_mock(fullname="t3_del", id="del",
                                                        selftext="[deleted]", is_self=True), acc)
    reddit_service._persistir_posts(acc, "home", [v], reemplazar=True)
    # El post borrado no se cachea.
    assert db.query_one("SELECT COUNT(*) FROM reddit_posts WHERE id=?", (v["id"],))[0] == 0
    db.execute("DELETE FROM accounts WHERE id=?", (acc,))


def test_purgar_reddit_borrados():
    from app.core.data_cleanup import purgar_reddit_borrados
    acc = _cuenta_reddit("u/purga")
    # Insertar directamente un comentario borrado (simulando un resto antiguo).
    db.execute("INSERT INTO reddit_comments (id, account_id, fullname, body) VALUES (?,?,?,?)",
               (f"{acc}:t1_old", acc, "t1_old", "[removed]"))
    assert purgar_reddit_borrados() >= 1
    assert db.query_one("SELECT COUNT(*) FROM reddit_comments WHERE id=?", (f"{acc}:t1_old",))[0] == 0
    db.execute("DELETE FROM accounts WHERE id=?", (acc,))


# --------------------------------------------------------------------------- plantillas
@pytest.mark.parametrize("clave", ["reddit_post", "reddit_comment", "reddit_message",
                                   "reddit_person", "reddit_subreddit"])
def test_plantillas_reddit(clave):
    d = plantillas.definicion(clave)
    assert d and d["red"] == "reddit"
    # La plantilla por defecto genera al menos una columna.
    assert plantillas.columnas_de_plantilla(d["default"])


def test_reddit_en_redes():
    assert "reddit" in [c for c, _ in plantillas.REDES]


# --------------------------------------------------------------------------- moderación (Fase 2)
from app.services.reddit.reddit_common import RedditServiceError


class _Post:
    """Objeto tipo Submission (su type().__name__ NO es 'Comment')."""
    def __init__(self, **kw):
        self.fullname = kw.get("fullname", "t3_m1")
        self.subreddit = kw.get("subreddit", "python")
        self.title = kw.get("title", "Un título")
        self.selftext = kw.get("selftext", "cuerpo")
        self.permalink = kw.get("permalink", "/r/python/comments/m1/")
        self.num_reports = kw.get("num_reports", 2)
        self.created_utc = kw.get("created_utc", 1700000000.0)
        self.author = MagicMock(); self.author.name = kw.get("author_name", "aurora")


class Comment:
    """Objeto tipo Comment (type().__name__ == 'Comment', como comprueba _item_mod_a_dict)."""
    def __init__(self, **kw):
        self.fullname = kw.get("fullname", "t1_c1")
        self.subreddit = kw.get("subreddit", "python")
        self.body = kw.get("body", "un comentario")
        self.permalink = kw.get("permalink", "/r/python/comments/x/c1/")
        self.num_reports = kw.get("num_reports", 1)
        self.created_utc = kw.get("created_utc", 1700000000.0)
        self.author = MagicMock(); self.author.name = kw.get("author_name", "bruno")


@pytest.fixture
def _cliente_mod(monkeypatch):
    """Sustituye obtener_cliente por un cliente PRAW simulado; devuelve (cliente, account_id)."""
    c = MagicMock()
    monkeypatch.setattr(reddit_service, "obtener_cliente", lambda acc: c)
    return c


def test_item_mod_a_dict_post_y_comment():
    dp = reddit_service._item_mod_a_dict(_Post())
    assert dp["tipo"] == "post" and dp["titulo"] == "Un título" and dp["author"] == "aurora"
    assert dp["fullname"] == "t3_m1" and dp["num_reports"] == 2
    dc = reddit_service._item_mod_a_dict(Comment())
    assert dc["tipo"] == "comment" and dc["cuerpo"] == "un comentario" and dc["titulo"] == ""


@pytest.mark.parametrize("cola", ["modqueue", "spam", "reports", "unmoderated", "edited"])
def test_cargar_cola_usa_la_cola_correcta(_cliente_mod, cola):
    sub = _cliente_mod.subreddit.return_value
    gen = MagicMock(return_value=[])  # el generador de la cola devuelve una lista iterable vacía
    setattr(sub.mod, cola, gen)
    reddit_service.cargar_cola(1, "python", cola)
    assert gen.called and gen.call_args.kwargs.get("limit") == 50


def test_cargar_cola_rechaza_cola_desconocida(_cliente_mod):
    with pytest.raises(RedditServiceError):
        reddit_service.cargar_cola(1, "python", "inventada")


def test_mod_acciones_por_fullname(_cliente_mod):
    obj = _cliente_mod.submission.return_value
    reddit_service.mod_aprobar(1, "t3_x")
    assert obj.mod.approve.called
    reddit_service.mod_eliminar(1, "t3_x", spam=True)
    obj.mod.remove.assert_called_with(spam=True, reason_id=None)
    reddit_service.mod_distinguir(1, "t3_x", how="yes", sticky=True)
    obj.mod.distinguish.assert_called_with(how="yes", sticky=True)
    reddit_service.mod_distinguir(1, "t3_x", how="no")
    assert obj.mod.undistinguish.called
    reddit_service.mod_bloquear(1, "t3_x", True)
    assert obj.mod.lock.called
    reddit_service.mod_ignorar_reportes(1, "t3_x", True)
    assert obj.mod.ignore_reports.called


def test_banear_limpia_usuario_y_pasa_kwargs(_cliente_mod):
    banned = _cliente_mod.subreddit.return_value.banned
    reddit_service.banear(1, "python", "u/spammer", motivo="spam", mensaje="Adiós",
                          duracion=7, nota="reincidente")
    args, kwargs = banned.add.call_args
    assert args[0] == "spammer"  # 'u/' eliminado
    assert kwargs == {"ban_reason": "spam", "ban_message": "Adiós", "duration": 7, "note": "reincidente"}


def test_listar_moderadores_y_relaciones(_cliente_mod):
    m = MagicMock(); m.name = "aurora"; m.mod_permissions = ["all"]
    _cliente_mod.subreddit.return_value.moderator.return_value = [m]
    res = reddit_service.listar_moderadores(1, "python")
    assert res == [{"name": "aurora", "permissions": ["all"]}]


def test_invitar_moderador_y_permisos(_cliente_mod):
    sub = _cliente_mod.subreddit.return_value
    reddit_service.invitar_moderador(1, "python", "u/nuevo", permisos=["posts", "flair"])
    sub.moderator.invite.assert_called_with("nuevo", permissions=["posts", "flair"])
    reddit_service.actualizar_permisos_moderador(1, "python", "aurora", ["all"])
    sub.moderator.update.assert_called_with("aurora", permissions=["all"])


def test_motivos_eliminacion(_cliente_mod):
    mod = _cliente_mod.subreddit.return_value.mod
    r = MagicMock(); r.id = "rid1"; r.title = "Spam"; r.message = "No spam"
    mod.removal_reasons.__iter__.return_value = iter([r])
    lst = reddit_service.listar_motivos(1, "python")
    assert lst == [{"id": "rid1", "title": "Spam", "message": "No spam"}]
    reddit_service.añadir_motivo(1, "python", "Off-topic", "Fuera de tema")
    assert mod.removal_reasons.add.called
    mod.removal_reasons.__iter__.return_value = iter([r])  # nuevo iterador para la segunda pasada
    assert reddit_service.borrar_motivo(1, "python", "rid1") is True
    assert r.delete.called


def test_config_subreddit(_cliente_mod):
    _cliente_mod.subreddit.return_value.mod.settings.return_value = {"title": "Py", "over_18": False}
    cfg = reddit_service.obtener_config_subreddit(1, "python")
    assert cfg["title"] == "Py"
    reddit_service.actualizar_config_subreddit(1, "python", title="Nuevo", over_18=True)
    _cliente_mod.subreddit.return_value.mod.update.assert_called_with(title="Nuevo", over_18=True)


# --------------------------------------------------------------------------- Fase 3: wiki
def test_listar_paginas_wiki(_cliente_mod):
    p1 = MagicMock(); p1.name = "index"
    p2 = MagicMock(); p2.name = "config/reglas"
    _cliente_mod.subreddit.return_value.wiki.__iter__.return_value = iter([p2, p1])
    assert reddit_service.listar_paginas_wiki(1, "python") == ["config/reglas", "index"]


def test_leer_pagina_wiki(_cliente_mod):
    wp = MagicMock()
    wp.name = "index"; wp.content_md = "# Hola"; wp.revision_date = 1700000000.0; wp.may_revise = True
    wp.revision_by = MagicMock(); wp.revision_by.name = "aurora"
    _cliente_mod.subreddit.return_value.wiki.__getitem__.return_value = wp
    d = reddit_service.leer_pagina_wiki(1, "python", "index")
    assert d["content_md"] == "# Hola" and d["revision_by"] == "aurora" and d["may_revise"] is True


def test_crear_y_editar_wiki(_cliente_mod):
    wiki = _cliente_mod.subreddit.return_value.wiki
    reddit_service.crear_pagina_wiki(1, "python", "reglas", "texto", reason="init")
    wiki.create.assert_called_with(name="reglas", content="texto", reason="init")
    reddit_service.editar_pagina_wiki(1, "python", "reglas", "nuevo", reason="")
    wiki.__getitem__.return_value.edit.assert_called_with(content="nuevo", reason=None)


def test_revisiones_wiki(_cliente_mod):
    rev = {"author": MagicMock(name="a"), "reason": "typo", "timestamp": 1700000000.0}
    rev["author"].name = "bruno"
    _cliente_mod.subreddit.return_value.wiki.__getitem__.return_value.revisions.return_value = [rev]
    res = reddit_service.revisiones_wiki(1, "python", "index")
    assert res == [{"author": "bruno", "reason": "typo", "timestamp": 1700000000.0}]


# --------------------------------------------------------------------------- Fase 3: flairs
def test_plantillas_flair_usuario_y_enlace(_cliente_mod):
    flair = _cliente_mod.subreddit.return_value.flair
    flair.templates.__iter__.return_value = iter([
        {"id": "t1", "text": "Novato", "mod_only": False, "text_editable": True}])
    ut = reddit_service.plantillas_flair_usuario(1, "python")
    assert ut == [{"id": "t1", "text": "Novato", "css_class": "", "mod_only": False,
                   "text_editable": True, "background_color": "", "text_color": ""}]
    flair.link_templates.__iter__.return_value = iter([{"id": "l1", "text": "Duda", "mod_only": True}])
    lt = reddit_service.plantillas_flair_enlace(1, "python")
    assert lt[0]["id"] == "l1" and lt[0]["mod_only"] is True


def test_crear_borrar_plantilla_flair(_cliente_mod):
    flair = _cliente_mod.subreddit.return_value.flair
    reddit_service.crear_plantilla_flair(1, "python", "Experto", tipo="user", mod_only=True)
    flair.templates.add.assert_called_with("Experto", mod_only=True, text_editable=False,
                                           background_color=None, text_color=None)
    reddit_service.crear_plantilla_flair(1, "python", "Pregunta", tipo="link")
    assert flair.link_templates.add.called
    reddit_service.borrar_plantilla_flair(1, "python", "t1")
    flair.templates.delete.assert_called_with("t1")


def test_flair_usuario_set_y_delete(_cliente_mod):
    flair = _cliente_mod.subreddit.return_value.flair
    flair.choices.return_value = [{"flair_template_id": "t1", "flair_text": "Novato",
                                   "flair_text_editable": True}]
    op = reddit_service.opciones_flair_usuario(1, "python")
    assert op == [{"id": "t1", "text": "Novato", "editable": True}]
    reddit_service.establecer_mi_flair(1, "python", text="Yo")
    assert flair.set.called
    reddit_service.asignar_flair_usuario(1, "python", "u/bruno", text="VIP")
    flair.set.assert_called_with("bruno", text="VIP", flair_template_id=None)
    reddit_service.quitar_flair_usuario(1, "python", "u/bruno")
    flair.delete.assert_called_with("bruno")


# --------------------------------------------------------------------------- Fase 3: emojis + widgets
def test_emojis(_cliente_mod):
    em = MagicMock(); em.name = "party"; em.url = "http://x"; em.mod_flair_only = False
    _cliente_mod.subreddit.return_value.emoji.__iter__.return_value = iter([em])
    lst = reddit_service.listar_emojis(1, "python")
    assert lst[0]["name"] == "party"
    reddit_service.añadir_emoji(1, "python", "wow", "/ruta/wow.png")
    _cliente_mod.subreddit.return_value.emoji.add.assert_called_with("wow", "/ruta/wow.png")
    reddit_service.borrar_emoji(1, "python", "party")
    assert _cliente_mod.subreddit.return_value.emoji.__getitem__.return_value.delete.called


def test_widgets_sidebar(_cliente_mod):
    w = MagicMock(); w.kind = "textarea"; w.shortName = "Bienvenida"; w.text = "Hola a todos"
    _cliente_mod.subreddit.return_value.widgets.sidebar = [w]
    res = reddit_service.listar_widgets_sidebar(1, "python")
    assert res == [{"kind": "textarea", "shortName": "Bienvenida", "text": "Hola a todos"}]


# --------------------------------------------------------------------------- Fase 4: multi navegable
@pytest.mark.parametrize("buffer,accede", [
    ("multi:spez:favoritos", lambda c: c.multireddit.return_value.hot),
    ("multi:spez:favoritos:new", lambda c: c.multireddit.return_value.new),
])
def test_resolver_listing_multi(buffer, accede):
    c = MagicMock()
    reddit_service._resolver_listing(c, 1, buffer, 25, None)
    assert accede(c).called
    # El multireddit se pide con dueño y nombre correctos.
    c.multireddit.assert_called_with(redditor="spez", name="favoritos")


# --------------------------------------------------------------------------- Fase 4: modmail
def test_iso_a_epoch():
    from app.services.reddit.reddit_modmail import _iso_a_epoch
    assert _iso_a_epoch("2018-03-20T23:45:04.505221+00:00") > 0
    assert _iso_a_epoch("") == 0.0
    assert _iso_a_epoch("nada") == 0.0


def test_listar_conversaciones_modmail(_cliente_mod):
    conv = MagicMock()
    conv.id = "abc"; conv.subject = "Duda"; conv.num_messages = 2; conv.state = "new"
    conv.is_highlighted = False; conv.last_updated = "2018-03-20T23:45:04.505221+00:00"
    conv.owner = "python"; conv.participant = MagicMock(); conv.participant.name = "bruno"
    _cliente_mod.subreddit.return_value.modmail.conversations.return_value = [conv]
    res = reddit_service.listar_conversaciones_modmail(1, "all", "new")
    assert res[0]["id"] == "abc" and res[0]["participant"] == "bruno"
    assert res[0]["subreddit"] == "python" and res[0]["last_updated"] > 0


def test_modmail_responder_y_archivar(_cliente_mod):
    conv = _cliente_mod.subreddit.return_value.modmail.return_value
    reddit_service.responder_modmail(1, "python", "abc", "hola", interno=True)
    conv.reply.assert_called_with(body="hola", internal=True)
    reddit_service.archivar_modmail(1, "python", "abc", True)
    assert conv.archive.called
    reddit_service.crear_modmail(1, "python", "Asunto", "Cuerpo", "u/bruno")
    _cliente_mod.subreddit.return_value.modmail.create.assert_called_with(
        subject="Asunto", body="Cuerpo", recipient="bruno")


# --------------------------------------------------------------------------- Fase 4: live/collections/drafts
def test_live_threads(_cliente_mod):
    _cliente_mod.live.create.return_value.id = "live1"
    assert reddit_service.crear_live_thread(1, "Evento", "desc") == "live1"
    u = MagicMock(); u.body = "Empieza"; u.stricken = False
    u.author = MagicMock(); u.author.name = "aurora"
    _cliente_mod.live.return_value.updates.return_value = [u]
    ups = reddit_service.leer_updates_live(1, "live1")
    assert ups == [{"body": "Empieza", "author": "aurora", "stricken": False}]
    reddit_service.publicar_update_live(1, "live1", "Gol")
    _cliente_mod.live.return_value.contrib.add.assert_called_with("Gol")


def test_collections(_cliente_mod):
    col = MagicMock(); col.collection_id = "c1"; col.title = "Mejores"; col.description = ""
    col.link_ids = ["t3_a", "t3_b"]
    _cliente_mod.subreddit.return_value.collections.__iter__.return_value = iter([col])
    lst = reddit_service.listar_collections(1, "python")
    assert lst[0]["id"] == "c1" and lst[0]["num_links"] == 2
    reddit_service.crear_collection(1, "python", "Nueva", "d")
    _cliente_mod.subreddit.return_value.collections.mod.create.assert_called_with(
        title="Nueva", description="d")
    reddit_service.añadir_post_collection(1, "python", "c1", "t3_x")
    assert _cliente_mod.subreddit.return_value.collections.return_value.mod.add_post.called


def test_drafts(_cliente_mod):
    d = MagicMock(); d.id = "d1"; d.title = "Idea"; d.selftext = "texto"; d.url = ""
    d.subreddit = MagicMock(); d.subreddit.display_name = "python"
    _cliente_mod.drafts.__iter__.return_value = iter([d])
    lst = reddit_service.listar_drafts(1)
    assert lst[0]["id"] == "d1" and lst[0]["subreddit"] == "python" and lst[0]["is_link"] is False
    reddit_service.crear_draft(1, "python", "T", selftext="cuerpo")
    _cliente_mod.drafts.create.assert_called_with(title="T", subreddit="python", selftext="cuerpo")
    reddit_service.enviar_draft(1, "d1")
    assert _cliente_mod.drafts.return_value.submit.called
    reddit_service.borrar_draft(1, "d1")
    assert _cliente_mod.drafts.return_value.delete.called
