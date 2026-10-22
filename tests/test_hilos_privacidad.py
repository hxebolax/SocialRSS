# -*- coding: utf-8 -*-
"""Pruebas de la PRIVACIDAD de los hilos de Mastodon.

El editor de hilos publicaba siempre en público, así que no se podía mandar un hilo en privado.
Al arreglarlo aparece un requisito de la propia red: la documentación de Mastodon define la
visibilidad `direct` como "visible solo para los usuarios MENCIONADOS", de modo que si el
destinatario solo aparece en el primer mensaje, del hilo únicamente recibe ese. Estas pruebas
fijan que las menciones se repiten en todas las partes y que se reservan al trocear.
"""
import pytest

from app.services.mastodon.mastodon_threading import (MastodonThreadingManager, asegurar_menciones,
                                                      menciones_de_texto, normalizar_menciones)


# ---------------- Normalización de destinatarios ----------------
@pytest.mark.parametrize("entrada,salida", [
    ("ana", "@ana"),
    ("@ana", "@ana"),
    ("ana, @luis@otra.social", "@ana @luis@otra.social"),
    ("@ana  @ana @ANA", "@ana"),                 # sin repetidos, sin distinguir mayúsculas
    ("", ""),
])
def test_normalizar_menciones(entrada, salida):
    assert normalizar_menciones(entrada) == salida


def test_menciones_de_texto_respeta_el_dominio():
    assert menciones_de_texto("Hola @ana@x.social y @luis, mirad") == "@ana@x.social @luis"


# ---------------- Garantía de mención por mensaje ----------------
def test_asegurar_menciones_anade_las_que_faltan():
    assert asegurar_menciones("texto", "@ana @luis") == "@ana @luis texto"


def test_asegurar_menciones_no_duplica_las_presentes():
    salida = asegurar_menciones("@ana ya estaba", "@ana @luis")
    assert salida.count("@ana") == 1 and "@luis" in salida


def test_asegurar_menciones_sin_destinatarios_no_toca_nada():
    assert asegurar_menciones("un texto", "") == "un texto"


# ---------------- Troceo de un hilo directo ----------------
def _partes_directas(texto, limite=200, menciones="@ana@instancia.social"):
    return MastodonThreadingManager.dividir_hilo(
        texto, limite_max=limite, numerar=True, formato="simple", menciones=menciones)


def test_todas_las_partes_llevan_al_destinatario():
    """Lo esencial: en un hilo de varios mensajes, TODOS deben mencionar al destinatario."""
    partes = _partes_directas("palabra " * 120)
    assert len(partes) > 1
    assert all(p.startswith("@ana@instancia.social") for p in partes)


def test_ninguna_parte_se_pasa_del_limite_contable():
    """La mención repetida se reserva: si no, el servidor rechazaría el hilo a media publicación."""
    limite = 200
    partes = _partes_directas("palabra " * 120, limite=limite)
    medir = MastodonThreadingManager._longitud_mastodon
    assert all(medir(p) <= limite for p in partes)


def test_el_cw_tambien_se_reserva_en_todas_las_partes():
    limite = 200
    cw = "Aviso largo de contenido para la prueba"
    partes = MastodonThreadingManager.dividir_hilo(
        "palabra " * 120, limite_max=limite, numerar=True, formato="simple",
        cw=cw, menciones="@ana")
    medir = MastodonThreadingManager._longitud_mastodon
    assert all(medir(p, cw) <= limite for p in partes)


def test_sin_menciones_el_troceo_no_cambia():
    """Un hilo público no debe llevar menciones repetidas (sería spam)."""
    partes = MastodonThreadingManager.dividir_hilo(
        "palabra " * 60, limite_max=200, numerar=True, formato="simple")
    assert not any(p.startswith("@") for p in partes)


# ---------------- La privacidad SE GUARDA con el hilo ----------------
@pytest.fixture
def cuenta_mastodon():
    """Cuenta de Mastodon de usar y tirar (los hilos tienen FK contra accounts)."""
    from app.core.database import db
    from app.core.migrations import ejecutar_migraciones
    ejecutar_migraciones()
    acc = db.execute_insert(
        "INSERT INTO accounts (name, service_id, active) VALUES ('@yo@instancia.social', 'mastodon', 1)")
    yield acc
    db.execute("DELETE FROM accounts WHERE id = ?", (acc,))


def test_un_hilo_directo_se_reanuda_como_directo(cuenta_mastodon):
    """Si no se guardara la visibilidad, reanudar un hilo directo que falló a medias lo
    publicaría en PÚBLICO: no una molestia, una fuga de un mensaje privado."""
    toots = [{"content": "@ana uno", "media_paths": [], "alt_texts": []},
             {"content": "@ana dos", "media_paths": [], "alt_texts": []}]
    tid = MastodonThreadingManager.guardar_hilo(
        None, "Hilo privado", cuenta_mastodon, toots,
        visibility="direct", spoiler_text="Aviso", mentions="@ana")
    hilo = MastodonThreadingManager.cargar_hilo(tid)
    assert hilo["visibility"] == "direct"
    assert hilo["spoiler_text"] == "Aviso"
    assert hilo["mentions"] == "@ana"
    MastodonThreadingManager.eliminar_hilo(tid)


def test_un_hilo_sin_privacidad_indicada_es_publico(cuenta_mastodon):
    tid = MastodonThreadingManager.guardar_hilo(
        None, "Hilo normal", cuenta_mastodon,
        [{"content": "hola", "media_paths": [], "alt_texts": []}])
    hilo = MastodonThreadingManager.cargar_hilo(tid)
    assert hilo["visibility"] == "public" and hilo["mentions"] == ""
    MastodonThreadingManager.eliminar_hilo(tid)
