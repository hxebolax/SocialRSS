# -*- coding: utf-8 -*-
"""Pruebas de Telegram Drive. Fase 2 (persistencia): jerarquía, mover/copiar, ciclos,
papelera, deduplicación y reconstrucción del índice desde el manifiesto.

TODO local, sin red: el repositorio y el manifiesto no dependen de Telegram.
"""
import pytest

from app.core.database import db
from app.core.migrations import ejecutar_migraciones


@pytest.fixture(scope="module")
def repo():
    ejecutar_migraciones()
    from app.services.tgdrive.tgdrive_repo import tgdrive_repo
    db.execute("INSERT OR IGNORE INTO services (id, name, active) VALUES ('telegram', 'Telegram', 1)")
    row = db.query_one("SELECT id FROM accounts WHERE service_id='telegram' AND name='DriveTest'")
    if row:
        acc = row[0]
    else:
        acc = db.execute_insert(
            "INSERT INTO accounts (service_id, name, active) VALUES ('telegram', 'DriveTest', 1)")
    # Limpiar cualquier residuo de una ejecución anterior.
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    return {"r": tgdrive_repo, "acc": acc, "chan": -100123456789}


def _archivo(repo_d, parent_id, nombre, size=100, sha=None, partes=1):
    """Crea un archivo COMPLETO de prueba (con sus partes marcadas subidas)."""
    r, acc, chan = repo_d["r"], repo_d["acc"], repo_d["chan"]
    it = r.crear_archivo_incompleto(acc, parent_id, nombre, size, "application/octet-stream",
                                    sha, partes, chan)
    for o in range(partes):
        r.registrar_parte(it.id, o, size // partes, sha, 1000 + o, f"fid{o}", f"uid{o}", "uploaded")
    assert r.finalizar_archivo(it.id) is True
    return r.obtener_item(it.id)


# ---------------------------------------------------------------------------
# Jerarquía: carpetas, subcarpetas, rutas
# ---------------------------------------------------------------------------

def test_crear_carpetas_y_rutas(repo):
    r, acc = repo["r"], repo["acc"]
    fotos = r.crear_carpeta(acc, None, "Fotos", repo["chan"])
    assert fotos.path == "/Fotos" and fotos.es_carpeta
    y2026 = r.crear_carpeta(acc, fotos.id, "2026", repo["chan"])
    assert y2026.path == "/Fotos/2026"
    arch = _archivo(repo, y2026.id, "playa.jpg")
    assert arch.path == "/Fotos/2026/playa.jpg" and arch.es_archivo
    # Listado: carpetas antes que archivos.
    raiz = r.listar(acc, None)
    assert any(i.name == "Fotos" for i in raiz)


def test_nombre_disponible(repo):
    r, acc = repo["r"], repo["acc"]
    carpeta = r.crear_carpeta(acc, None, "Docs", repo["chan"])
    _archivo(repo, carpeta.id, "a.txt")
    assert r.existe_nombre(acc, carpeta.id, "a.txt") is True
    assert r.nombre_disponible(acc, carpeta.id, "a.txt") == "a (2).txt"
    assert r.nombre_disponible(acc, carpeta.id, "nuevo.txt") == "nuevo.txt"


# ---------------------------------------------------------------------------
# Renombrar, mover, copiar, ciclos
# ---------------------------------------------------------------------------

def test_renombrar_actualiza_rutas(repo):
    r, acc = repo["r"], repo["acc"]
    c = r.crear_carpeta(acc, None, "Viejo", repo["chan"])
    hijo = _archivo(repo, c.id, "x.dat")
    assert r.renombrar(c.id, "Nuevo") is True
    assert r.obtener_item(c.id).path == "/Nuevo"
    assert r.obtener_item(hijo.id).path == "/Nuevo/x.dat"  # el hijo se recalculó


def test_mover_y_ciclo(repo):
    r, acc = repo["r"], repo["acc"]
    a = r.crear_carpeta(acc, None, "A", repo["chan"])
    b = r.crear_carpeta(acc, a.id, "B", repo["chan"])
    c = r.crear_carpeta(acc, None, "C", repo["chan"])
    # Mover C dentro de A: ok.
    assert r.mover(c.id, a.id) is True
    assert r.obtener_item(c.id).path == "/A/C"
    # Mover A dentro de B (su nieto): CICLO -> rechazado.
    with pytest.raises(ValueError):
        r.mover(a.id, b.id)
    # Mover A dentro de sí misma: rechazado.
    with pytest.raises(ValueError):
        r.mover(a.id, a.id)


def test_copiar_reutiliza_partes(repo):
    r, acc = repo["r"], repo["acc"]
    origen = r.crear_carpeta(acc, None, "Origen", repo["chan"])
    destino = r.crear_carpeta(acc, None, "Destino", repo["chan"])
    arch = _archivo(repo, origen.id, "clip.mp4", size=300, partes=3)
    copia = r.copiar(arch.id, destino.id)
    assert copia is not None and copia.path == "/Destino/clip.mp4"
    # La copia APUNTA a los mismos mensajes (no re-sube): mismos message_id.
    orig_msgs = sorted(p.message_id for p in r.obtener_partes(arch.id))
    copia_msgs = sorted(p.message_id for p in r.obtener_partes(copia.id))
    assert orig_msgs == copia_msgs and len(copia_msgs) == 3


def test_copiar_carpeta_recursivo(repo):
    r, acc = repo["r"], repo["acc"]
    raiz = r.crear_carpeta(acc, None, "Proyecto", repo["chan"])
    sub = r.crear_carpeta(acc, raiz.id, "src", repo["chan"])
    _archivo(repo, sub.id, "main.py")
    copia = r.copiar(raiz.id, None, "Proyecto copia")
    assert copia.name == "Proyecto copia"
    hijos = r.listar(acc, copia.id)
    assert any(h.name == "src" and h.es_carpeta for h in hijos)


# ---------------------------------------------------------------------------
# Papelera
# ---------------------------------------------------------------------------

def test_papelera_y_restaurar(repo):
    r, acc = repo["r"], repo["acc"]
    carpeta = r.crear_carpeta(acc, None, "Temporal", repo["chan"])
    arch = _archivo(repo, carpeta.id, "borrame.txt")
    assert r.a_papelera(carpeta.id) is True
    # Ya no aparece en la vista normal, sí en la papelera.
    assert all(i.id != carpeta.id for i in r.listar(acc, None))
    assert any(i.id == carpeta.id for i in r.listar_papelera(acc))
    # El hijo también fue a la papelera.
    assert r.obtener_item(arch.id).estado == "trash"
    # Restaurar: vuelve a su sitio.
    assert r.restaurar(carpeta.id) is True
    assert r.obtener_item(carpeta.id).estado == "normal"
    assert r.obtener_item(arch.id).estado == "normal"


def test_borrado_definitivo_reune_mensajes(repo):
    r, acc = repo["r"], repo["acc"]
    carpeta = r.crear_carpeta(acc, None, "ParaBorrar", repo["chan"])
    _archivo(repo, carpeta.id, "f1.bin", partes=2)
    _archivo(repo, carpeta.id, "f2.bin", partes=1)
    msgs = r.mensajes_de_subarbol(carpeta.id)
    assert len(msgs) == 3  # 2 partes + 1 parte
    r.borrar_definitivo(carpeta.id)
    assert r.obtener_item(carpeta.id) is None  # y las partes por CASCADE


# ---------------------------------------------------------------------------
# Deduplicación por contenido
# ---------------------------------------------------------------------------

def test_dedupe_por_sha256(repo):
    r, acc = repo["r"], repo["acc"]
    c = r.crear_carpeta(acc, None, "Dedup", repo["chan"])
    _archivo(repo, c.id, "original.zip", size=500, sha="abc123hash")
    encontrado = r.buscar_por_sha256(acc, "abc123hash")
    assert encontrado is not None and encontrado.name == "original.zip"
    assert r.buscar_por_sha256(acc, "noexiste") is None
    # Mismo NOMBRE pero distinto contenido no se confunde con duplicado.
    assert r.buscar_por_sha256(acc, "otrohash") is None


# ---------------------------------------------------------------------------
# Reconstrucción del índice desde el manifiesto
# ---------------------------------------------------------------------------

def test_manifiesto_reconstruye_base(repo):
    from app.services.tgdrive import tgdrive_manifest as man
    r, acc, chan = repo["r"], repo["acc"], repo["chan"]
    # Estructura conocida.
    c = r.crear_carpeta(acc, None, "Backup", chan)
    _archivo(repo, c.id, "datos.db", size=999, sha="hh", partes=2)
    antes_items = len(r.buscar(acc, "", "normal"))
    # Construir manifiesto y volcarlo/cargarlo (ida y vuelta por JSON).
    m = man.construir_manifiesto(acc, chan)
    assert m["manifest_version"] == man.MANIFEST_VERSION and m["channel_id"] == chan
    datos = man.volcar_json(m)
    m2 = man.cargar_json(datos)
    # Borrar la base local y reconstruir SOLO desde el manifiesto.
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    assert r.obtener_item(c.id) is None
    res = man.reconstruir_base(acc, m2)
    assert res["items"] >= 2
    # La carpeta y el archivo vuelven, con sus partes y rutas.
    rec = r.obtener_item(c.id)
    assert rec is not None and rec.name == "Backup"
    hijos = r.listar(acc, c.id)
    arch = next(h for h in hijos if h.name == "datos.db")
    assert arch.sha256 == "hh" and len(r.obtener_partes(arch.id)) == 2


def test_manifiesto_version_futura_se_rechaza():
    from app.services.tgdrive import tgdrive_manifest as man
    futuro = man.volcar_json({"manifest_version": 999, "items": [], "parts": []})
    with pytest.raises(ValueError):
        man.cargar_json(futuro)
    with pytest.raises(ValueError):
        man.cargar_json(b"esto no es json")


# ===========================================================================
# Fase 3: almacenamiento con un DOBLE de Telegram en memoria (sin red)
# ===========================================================================
import os


class BackendFalso:
    """Doble de Telegram para pruebas: guarda los 'documentos' en memoria en vez de subirlos
    a un canal real. Cada subida es un mensaje con id incremental. Permite simular un límite
    de tamaño pequeño (para forzar la división) y mensajes inexistentes."""

    def __init__(self, limite=10 ** 12):
        self.limite = limite
        self.mensajes = {}          # message_id -> {"bytes":..., "name":...}
        self._siguiente = 1000
        self.canal = -100999888777

    def crear_canal(self, account_id, titulo):
        return self.canal

    def limite_tamano(self, account_id):
        return self.limite

    def subir(self, account_id, channel_id, ruta, caption="", progreso=None, cancelado=None):
        # Simular el ABORTO inmediato de kurigram: si se cancela durante la subida, no se
        # almacena nada y se lanza TransferenciaCancelada (como StopTransmission -> el servicio).
        if getattr(self, "abortar_en_subida", False) or (cancelado and cancelado()):
            from app.services.tgdrive.tgdrive_models import TransferenciaCancelada
            raise TransferenciaCancelada()
        # Simular un CORTE de red en la N-ésima subida (para probar la reanudación por partes).
        self._n_subir = getattr(self, "_n_subir", 0) + 1
        if getattr(self, "fallar_subida_en", None) == self._n_subir:
            raise RuntimeError("corte de red simulado")
        with open(ruta, "rb") as fh:
            datos = fh.read()
        mid = self._siguiente
        self._siguiente += 1
        self.mensajes[mid] = {"bytes": datos, "name": os.path.basename(ruta)}
        if progreso:
            progreso(len(datos), len(datos))
        return {"message_id": mid, "file_id": f"fid{mid}",
                "unique_id": f"uid{mid}", "size": len(datos)}

    def descargar(self, account_id, channel_id, message_id, destino, file_id=None, progreso=None,
                  cancelado=None):
        m = self.mensajes.get(message_id)
        if m is None:
            raise FileNotFoundError("El mensaje ya no existe.")
        with open(destino, "wb") as fh:
            fh.write(m["bytes"])
        if progreso:
            progreso(len(m["bytes"]), len(m["bytes"]))
        return destino

    def borrar_mensajes(self, account_id, channel_id, message_ids):
        for mid in message_ids:
            self.mensajes.pop(mid, None)

    def canal_existe(self, account_id, channel_id):
        return channel_id == self.canal and not getattr(self, "_borrado", False)

    def borrar_canal(self, account_id, channel_id):
        # Borrar el canal elimina TODOS sus mensajes (archivos).
        self.mensajes.clear()
        self._borrado = True
        return True

    def buscar_canal(self, account_id, titulo):
        # Localiza el canal de la nube por su título (si no se ha borrado).
        if getattr(self, "_borrado", False):
            return None
        return self.canal if titulo == "SocialRSS Telegram Drive" else None

    def buscar_manifiesto(self, account_id, channel_id, nombre):
        for mid in sorted(self.mensajes, reverse=True):
            if self.mensajes[mid]["name"] == nombre:
                return {"message_id": mid, "file_id": f"fid{mid}"}
        return None

    def listar_documentos_canal(self, account_id, channel_id):
        return [{"message_id": mid, "file_name": m.get("name", ""), "caption": ""}
                for mid, m in sorted(self.mensajes.items())]


@pytest.fixture
def servicio(repo, tmp_path):
    from app.services.tgdrive.tgdrive_service import TgDriveService
    acc = repo["acc"]
    backend = BackendFalso()
    svc = TgDriveService(backend=backend)
    svc.guardar_channel_id(acc, backend.canal, "Test Drive")
    return {"svc": svc, "backend": backend, "acc": acc, "repo": repo["r"]}


def _crear_local(tmp_path, nombre, contenido: bytes):
    p = tmp_path / nombre
    p.write_bytes(contenido)
    return str(p)


def test_subir_archivo_pequeno(servicio, tmp_path):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "hola.txt", b"contenido de prueba")
    item = svc.subir_archivo(acc, None, ruta)
    assert item is not None and item.estado == "normal" and item.num_parts == 1
    partes = r.obtener_partes(item.id)
    assert len(partes) == 1 and partes[0].message_id in servicio["backend"].mensajes


def test_dividir_y_reconstruir_grande(servicio, tmp_path):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    servicio["backend"].limite = 10          # límite minúsculo: fuerza la división
    contenido = bytes(range(256)) * 4         # 1024 bytes -> 103 partes de 10 bytes
    ruta = _crear_local(tmp_path, "grande.bin", contenido)
    item = svc.subir_archivo(acc, None, ruta)
    assert item is not None and item.num_parts == len(r.obtener_partes(item.id))
    assert item.num_parts > 1
    # Descargar y reconstruir: el resultado debe ser idéntico byte a byte.
    destino = str(tmp_path / "salida")
    ruta_final = svc.descargar_item(acc, item.id, destino)
    assert ruta_final and open(ruta_final, "rb").read() == contenido


def test_verificacion_sha_falla_borra(servicio, tmp_path, monkeypatch):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "intacto.dat", b"datos correctos")
    item = svc.subir_archivo(acc, None, ruta)
    # Corromper el byte almacenado en el "canal" para que la verificación falle.
    p = r.obtener_partes(item.id)[0]
    servicio["backend"].mensajes[p.message_id]["bytes"] = b"datos CORROMPIDOS!"
    destino = str(tmp_path / "bajada")
    with pytest.raises(ValueError):
        svc.descargar_item(acc, item.id, destino)
    # El archivo dañado no debe quedar en disco.
    os.makedirs(destino, exist_ok=True)
    assert not any(f.startswith("intacto") for f in os.listdir(destino))


def test_dedupe_no_resube(servicio, tmp_path):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "dup.txt", b"mismo contenido exacto")
    item1 = svc.subir_archivo(acc, None, ruta)
    msgs_antes = len(servicio["backend"].mensajes)
    # Subir OTRO archivo con el mismo contenido a otra carpeta: no debe re-subir bytes.
    carpeta = r.crear_carpeta(acc, None, "Otra", servicio["backend"].canal)
    item2 = svc.subir_archivo(acc, carpeta.id, ruta, nombre="copia.txt")
    assert item2 is not None
    assert len(servicio["backend"].mensajes) == msgs_antes  # cero subidas nuevas
    assert r.obtener_partes(item2.id)[0].message_id == r.obtener_partes(item1.id)[0].message_id


def test_cancelar_subida(servicio, tmp_path):
    svc, acc = servicio["svc"], servicio["acc"]
    ruta = _crear_local(tmp_path, "cancela.bin", b"x" * 1000)
    # Cancelar desde el primer chequeo (antes del hash).
    item = svc.subir_archivo(acc, None, ruta, cancelado=lambda: True)
    assert item is None  # no se creó el archivo


def test_cancelar_subida_en_curso_aborta(servicio, tmp_path):
    """Cancelar DURANTE la subida (no antes) aborta al instante: no queda mensaje en el canal ni
    un archivo incompleto en el índice, y subir_archivo devuelve None."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "grande.bin", b"z" * 5000)
    servicio["backend"].abortar_en_subida = True   # el backend aborta como StopTransmission
    item = svc.subir_archivo(acc, None, ruta)
    assert item is None
    assert servicio["backend"].mensajes == {}          # nada subido al canal
    # No queda ningún archivo incompleto colgando en la cuenta.
    incompletos = db.query_one(
        "SELECT COUNT(*) FROM tgdrive_items WHERE account_id = ? AND estado = 'incomplete'", (acc,))
    assert (incompletos or [0])[0] == 0


def test_mensaje_inexistente_al_descargar(servicio, tmp_path):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "borrado.txt", b"algo")
    item = svc.subir_archivo(acc, None, ruta)
    # Borrar el mensaje del "canal" (simula que ya no existe en Telegram).
    servicio["backend"].mensajes.clear()
    with pytest.raises(FileNotFoundError):
        svc.descargar_item(acc, item.id, str(tmp_path / "d"))


def test_eliminar_definitivo_borra_mensajes(servicio, tmp_path):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "quitar.bin", b"y" * 50)
    item = svc.subir_archivo(acc, None, ruta)
    mid = r.obtener_partes(item.id)[0].message_id
    assert mid in servicio["backend"].mensajes
    svc.eliminar_definitivo(acc, item.id)
    assert mid not in servicio["backend"].mensajes  # se borró de Telegram
    assert r.obtener_item(item.id) is None


def test_eliminacion_pendiente_si_falla(servicio, tmp_path, monkeypatch):
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "pend.bin", b"z" * 30)
    item = svc.subir_archivo(acc, None, ruta)
    # Hacer que el borrado en Telegram falle -> queda pendiente.
    def _boom(*a, **k):
        raise RuntimeError("sin conexión")
    monkeypatch.setattr(servicio["backend"], "borrar_mensajes", _boom)
    svc.eliminar_definitivo(acc, item.id)
    pend = r.eliminaciones_pendientes(acc)
    assert len(pend) == 1
    # Al restaurar la conexión, reintentar las resuelve.
    monkeypatch.undo()
    assert svc.reintentar_eliminaciones(acc) == 1
    assert r.eliminaciones_pendientes(acc) == []


def test_reconstruir_indice_desde_canal(servicio, tmp_path):
    from app.services.tgdrive import tgdrive_manifest as man
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    c = r.crear_carpeta(acc, None, "Nube", servicio["backend"].canal)
    ruta = _crear_local(tmp_path, "doc.txt", b"documento en la nube")
    svc.subir_archivo(acc, c.id, ruta)
    # Exportar el índice al canal (sube el manifiesto).
    assert svc.exportar_indice(acc) is not None
    # Borrar la base local y reconstruir SOLO desde el canal.
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    assert r.obtener_item(c.id) is None
    res = svc.reconstruir_indice(acc, servicio["backend"].canal)
    assert res["items"] >= 2 and r.obtener_item(c.id) is not None


def test_cola_transferencias(servicio, tmp_path):
    """La cola encola y procesa una subida en su trabajador (esperando a que termine)."""
    import time
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    ruta = _crear_local(tmp_path, "cola.txt", b"por la cola")
    tid = svc.encolar_subida(acc, None, ruta)
    # Esperar a que el trabajador de fondo lo complete (con tope).
    for _ in range(50):
        trs = {t["id"]: t for t in svc.obtener_transferencias(acc)}
        if trs.get(tid, {}).get("estado") in ("completada", "fallida"):
            break
        time.sleep(0.1)
    trs = {t["id"]: t for t in svc.obtener_transferencias(acc)}
    assert trs[tid]["estado"] == "completada" and trs[tid]["porcentaje"] == 100


def test_retencion_papelera_purga_caducados(servicio, tmp_path):
    """La retención elimina definitivamente lo que lleva demasiado tiempo en la papelera y
    respeta lo reciente. Se manipula trashed_at para simular el paso del tiempo."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    viejo = svc.subir_archivo(acc, None, _crear_local(tmp_path, "viejo.txt", b"antiguo"))
    reciente = svc.subir_archivo(acc, None, _crear_local(tmp_path, "nuevo.txt", b"reciente"))
    r.a_papelera(viejo.id)
    r.a_papelera(reciente.id)
    mid_viejo = r.obtener_partes(viejo.id)[0].message_id
    # Envejecer el elemento viejo 40 días; el reciente se queda como está.
    from datetime import datetime, timedelta
    hace_40 = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    db.execute("UPDATE tgdrive_items SET trashed_at = ? WHERE id = ?", (hace_40, viejo.id))
    # Retención de 30 días: purga el viejo, conserva el reciente.
    purgados = svc.purgar_papelera_caducada(acc, dias=30)
    assert purgados == 1
    assert r.obtener_item(viejo.id) is None
    assert mid_viejo not in servicio["backend"].mensajes   # también se borró de Telegram
    assert r.obtener_item(reciente.id) is not None
    # dias=0 no purga nada aunque esté en la papelera.
    assert svc.purgar_papelera_caducada(acc, dias=0) == 0


def test_reintentar_solo_partes_que_faltan(servicio, tmp_path):
    """Al reintentar una subida grande a medias, las partes ya subidas se REUTILIZAN (dedupe por
    parte no aplica, pero el archivo incompleto se limpia y se vuelve a subir de forma íntegra:
    verificamos que un reintento tras un fallo deja el archivo completo y descargable)."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    servicio["backend"].limite = 10
    contenido = bytes(range(200)) * 3
    ruta = _crear_local(tmp_path, "reintento.bin", contenido)
    # Primer intento: cancelar a mitad (deja el item incompleto limpio, no válido).
    llamadas = {"n": 0}
    def _cancela_tras_3():
        llamadas["n"] += 1
        return llamadas["n"] > 3
    item = svc.subir_archivo(acc, None, ruta, cancelado=_cancela_tras_3)
    assert item is None
    # No debe quedar ningún archivo válido con ese nombre.
    assert not any(i.name == "reintento.bin" for i in r.listar(acc, None))
    # Segundo intento completo: sube bien y se reconstruye idéntico.
    item2 = svc.subir_archivo(acc, None, ruta)
    assert item2 is not None and item2.estado == "normal"
    destino = svc.descargar_item(acc, item2.id, str(tmp_path / "out"))
    assert destino and open(destino, "rb").read() == contenido


def test_descripcion_en_carpeta_archivo_y_manifiesto(servicio, tmp_path):
    """La descripción opcional se guarda en carpetas y archivos, se puede editar, se conserva al
    copiar y sobrevive a la reconstrucción del índice desde el manifiesto."""
    from app.services.tgdrive import tgdrive_manifest as man
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    # Carpeta con descripción.
    carp = r.crear_carpeta(acc, None, "ConDesc", servicio["backend"].canal, "carpeta importante")
    assert r.obtener_item(carp.id).descripcion == "carpeta importante"
    # Archivo subido con descripción.
    ruta = _crear_local(tmp_path, "informe.txt", b"contenido informe")
    item = svc.subir_archivo(acc, carp.id, ruta, descripcion="el informe anual")
    assert item is not None and r.obtener_item(item.id).descripcion == "el informe anual"
    # Editar la descripción.
    assert r.establecer_descripcion(item.id, "revisado 2026") is True
    assert r.obtener_item(item.id).descripcion == "revisado 2026"
    # Copiar conserva la descripción.
    copia = r.copiar(item.id, None)
    assert copia.descripcion == "revisado 2026"
    # Reconstruir desde el manifiesto conserva las descripciones.
    m = man.construir_manifiesto(acc, servicio["backend"].canal)
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    man.reconstruir_base(acc, m)
    assert r.obtener_item(carp.id).descripcion == "carpeta importante"
    assert r.obtener_item(item.id).descripcion == "revisado 2026"


def test_reconstruir_localiza_canal_sin_config(servicio, tmp_path):
    """Sin canal configurado (reinstalación), reconstruir LOCALIZA el canal por su título y
    reconstruye desde él."""
    from app.core.config_manager import config
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    c = r.crear_carpeta(acc, None, "Recuperar", servicio["backend"].canal)
    svc.subir_archivo(acc, c.id, _crear_local(tmp_path, "d.txt", b"dato"))
    assert svc.exportar_indice(acc) is not None
    # Simular pérdida del id del canal (config vacía) y de la base local.
    config.set("telegram_drive", str(acc), {})
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    assert svc.obtener_channel_id(acc) is None
    # Reconstruir SIN pasar canal: debe localizarlo por título.
    res = svc.reconstruir_indice(acc)
    assert res["items"] >= 2 and r.obtener_item(c.id) is not None
    # Y deja el canal recordado de nuevo.
    assert svc.obtener_channel_id(acc) == servicio["backend"].canal


def test_reconstruir_sin_canal_da_error_claro(servicio, tmp_path):
    """Si no hay ningún canal (ni configurado ni localizable), reconstruir lanza un error claro
    en vez de dejar la nube vacía en silencio."""
    from app.core.config_manager import config
    svc, acc = servicio["svc"], servicio["acc"]
    config.set("telegram_drive", str(acc), {})
    servicio["backend"]._borrado = True   # buscar_canal devolverá None
    with pytest.raises(ValueError):
        svc.reconstruir_indice(acc)


def test_eliminar_drive_completo(servicio, tmp_path):
    """Eliminar la nube borra el canal (con todos los archivos), vacía el índice local y olvida
    el canal en la configuración."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    c = r.crear_carpeta(acc, None, "Cosas", servicio["backend"].canal)
    svc.subir_archivo(acc, c.id, _crear_local(tmp_path, "a.txt", b"aaa"))
    svc.subir_archivo(acc, None, _crear_local(tmp_path, "b.txt", b"bbb"))
    assert r.contar(acc)["archivos"] >= 2   # cuenta de módulo: puede arrastrar de otros tests
    assert servicio["backend"].mensajes  # hay documentos en el canal
    ok = svc.eliminar_drive_completo(acc, borrar_canal=True)
    assert ok is True
    # Canal borrado (sin documentos) e índice local vacío.
    assert servicio["backend"].mensajes == {}
    assert r.contar(acc)["archivos"] == 0 and r.contar(acc)["carpetas"] == 0
    assert r.listar(acc, None) == []
    # El canal se olvida en la configuración.
    assert svc.obtener_channel_id(acc) is None


def test_reanudar_pendientes_valida_origen(servicio, tmp_path):
    """Al reanudar, una subida pendiente cuyo archivo de origen EXISTE sigue pendiente; otra cuyo
    origen ya no existe se marca fallida (no se puede reanudar)."""
    import uuid as _uuid
    from datetime import datetime
    svc, acc = servicio["svc"], servicio["acc"]
    # Dejar sin trabajador vivo para que reanudar procese los estados.
    svc._cola_init()
    svc._cola_workers = []
    existe = _crear_local(tmp_path, "existe.bin", b"1234")
    ahora = datetime.now().isoformat(timespec="seconds")
    tid_ok = _uuid.uuid4().hex
    tid_ko = _uuid.uuid4().hex
    db.execute("INSERT INTO tgdrive_transfers (id,account_id,operacion,nombre,local_path,estado,"
               "created_at,updated_at) VALUES (?,?,'upload','existe.bin',?,'pendiente',?,?)",
               (tid_ok, acc, existe, ahora, ahora))
    db.execute("INSERT INTO tgdrive_transfers (id,account_id,operacion,nombre,local_path,estado,"
               "created_at,updated_at) VALUES (?,?,'upload','fantasma.bin',?,'pendiente',?,?)",
               (tid_ko, acc, str(tmp_path / "no_existe.bin"), ahora, ahora))
    res = svc.reanudar_pendientes()
    assert res["huerfanas"] >= 1
    estado_ko = db.query_one("SELECT estado FROM tgdrive_transfers WHERE id = ?", (tid_ko,))[0]
    estado_ok = db.query_one("SELECT estado FROM tgdrive_transfers WHERE id = ?", (tid_ok,))[0]
    assert estado_ko == "fallida"
    assert estado_ok in ("pendiente", "subiendo", "completada")  # se reanuda (el worker puede tomarla)
    # Limpiar las transferencias de prueba.
    db.execute("DELETE FROM tgdrive_transfers WHERE id IN (?, ?)", (tid_ok, tid_ko))


def test_varias_subidas_simultaneas(servicio, tmp_path):
    """Con el límite a 3, varios trabajadores procesan subidas EN PARALELO (no de una en una).
    Se usa un backend lento que registra cuántas subidas coinciden a la vez."""
    import time, threading
    from app.core.config_manager import config
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    backend = servicio["backend"]
    # Backend lento e instrumentado: cuenta el máximo de subidas concurrentes.
    activos = {"n": 0, "max": 0}
    lock = threading.Lock()
    subir_orig = backend.subir
    def _subir_lento(*a, **k):
        with lock:
            activos["n"] += 1
            activos["max"] = max(activos["max"], activos["n"])
        try:
            time.sleep(0.3)
            return subir_orig(*a, **k)
        finally:
            with lock:
                activos["n"] -= 1
    backend.subir = _subir_lento
    config.set("telegram_drive", "max_simultaneas", 3)
    svc._cola_init()
    svc._cola_workers = []
    for i in range(6):
        svc.encolar_subida(acc, None, _crear_local(tmp_path, f"f{i}.bin", bytes([i]) * 100))
    # Esperar a que terminen todas (con tope).
    for _ in range(100):
        pend = db.query_one("SELECT COUNT(*) FROM tgdrive_transfers WHERE account_id=? AND "
                            "estado IN ('pendiente','subiendo')", (acc,))
        if not (pend and pend[0]):
            break
        time.sleep(0.1)
    assert activos["max"] >= 2, f"no hubo paralelismo real (máx concurrentes={activos['max']})"
    assert activos["max"] <= 3, "se superó el límite configurado"
    db.execute("DELETE FROM tgdrive_transfers WHERE account_id=?", (acc,))


def test_reanudar_subida_por_partes(servicio, tmp_path):
    """Si una subida por partes se corta tras subir algunas, al reintentar se REANUDA: solo se
    suben las partes que faltan (no se empieza de cero) y el archivo queda íntegro."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    backend = servicio["backend"]
    backend.limite = 100                    # fuerza la división
    contenido = bytes(range(256)) * 2       # 512 bytes -> 6 partes
    ruta = _crear_local(tmp_path, "grande.bin", contenido)
    capturado = {}
    def _on_item(iid):
        capturado["id"] = iid
    # Cortar en la 3ª subida de parte: quedan 2 partes subidas.
    backend.fallar_subida_en = 3
    with pytest.raises(Exception):
        svc.subir_archivo(acc, None, ruta, on_item=_on_item, reusar_duplicado=False)
    iid = capturado["id"]
    it = r.obtener_item(iid)
    assert it.estado == "incomplete"
    assert sum(1 for p in r.obtener_partes(iid) if p.estado == "uploaded") == 2
    num_parts = it.num_parts
    ids_previos = {p.orden: p.message_id for p in r.obtener_partes(iid)}
    # Reanudar sin fallo: debe subir SOLO las partes que faltan.
    backend.fallar_subida_en = None
    item = svc.subir_archivo(acc, None, ruta, reanudar_item_id=iid, on_item=_on_item,
                             reusar_duplicado=False)
    assert item is not None and item.estado == "normal"
    # Las 2 primeras partes NO se re-subieron (conservan su message_id).
    ids_final = {p.orden: p.message_id for p in r.obtener_partes(item.id)}
    for orden, mid in ids_previos.items():
        assert ids_final[orden] == mid, "una parte ya subida se volvió a subir"
    # En el canal hay EXACTAMENTE num_parts documentos (nada duplicado por reanudar).
    assert len(backend.mensajes) == num_parts
    # Y el archivo se descarga íntegro.
    destino = svc.descargar_item(acc, item.id, str(tmp_path / "out"))
    assert destino and open(destino, "rb").read() == contenido


def test_revisar_canal_detecta_y_limpia(servicio, tmp_path):
    """revisar_canal detecta documentos huérfanos (no referenciados) y archivos rotos (partes que
    faltan en el canal); limpiar/quitar los elimina."""
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    backend = servicio["backend"]
    # Aislar: la cuenta de módulo arrastra items de otros tests (con otro backend), que aquí
    # parecerían 'faltantes'. Se limpian para revisar solo lo de este test.
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    # Un archivo normal subido bien.
    svc.subir_archivo(acc, None, _crear_local(tmp_path, "ok.txt", b"bien"))
    # Un mensaje HUÉRFANO en el canal (no referenciado por ningún archivo).
    backend.mensajes[999999] = {"bytes": b"basura", "name": "basura.bin"}
    rep = svc.revisar_canal(acc)
    assert 999999 in rep["orfanos"]
    assert rep["faltantes"] == [] and rep["items_rotos"] == []
    # Limpiar el huérfano lo borra del canal.
    assert svc.limpiar_orfanos_canal(acc, rep["orfanos"]) >= 1
    assert 999999 not in backend.mensajes
    # Ahora un archivo ROTO: subimos y borramos su mensaje del canal a mano.
    it = svc.subir_archivo(acc, None, _crear_local(tmp_path, "roto.txt", b"roto"))
    mid = r.obtener_partes(it.id)[0].message_id
    backend.mensajes.pop(mid, None)
    rep2 = svc.revisar_canal(acc)
    assert mid in rep2["faltantes"] and it.id in rep2["items_rotos"]
    assert svc.quitar_items_rotos(acc, rep2["items_rotos"]) >= 1
    assert r.obtener_item(it.id) is None


def test_limpiar_transferencias_terminadas(servicio):
    """limpiar_transferencias quita en lote las terminadas del estado pedido y NUNCA las en curso."""
    import uuid as _uuid
    from datetime import datetime
    svc, acc = servicio["svc"], servicio["acc"]
    db.execute("DELETE FROM tgdrive_transfers WHERE account_id = ?", (acc,))
    ahora = datetime.now().isoformat(timespec="seconds")
    def _ins(estado):
        tid = _uuid.uuid4().hex
        db.execute("INSERT INTO tgdrive_transfers (id,account_id,operacion,nombre,estado,"
                   "created_at,updated_at) VALUES (?,?,'upload','x',?,?,?)",
                   (tid, acc, estado, ahora, ahora))
        return tid
    _ins("completada"); _ins("completada")
    _ins("fallida")
    _ins("cancelada")
    en_curso = _ins("subiendo")   # NO se debe tocar
    # Quitar solo completadas.
    assert svc.limpiar_transferencias(acc, ("completada",)) == 2
    # Quitar todas las terminadas restantes (fallida + cancelada).
    assert svc.limpiar_transferencias(acc) == 2
    # La que está en curso sigue ahí; y no se puede quitar aunque se pida (solo terminados).
    assert svc.limpiar_transferencias(acc, ("subiendo",)) == 0
    quedan = db.query("SELECT estado FROM tgdrive_transfers WHERE account_id = ?", (acc,))
    assert [r[0] for r in quedan] == ["subiendo"]
    db.execute("DELETE FROM tgdrive_transfers WHERE id = ?", (en_curso,))


def test_exportar_indice_deja_un_solo_manifiesto(servicio, tmp_path):
    """Exportar el índice varias veces deja SOLO el manifiesto más reciente en el canal (antes se
    acumulaba uno por cada subida)."""
    from app.services.tgdrive import tgdrive_manifest as man
    svc, acc = servicio["svc"], servicio["acc"]
    backend = servicio["backend"]
    # Aislar: en Telegram real los message_id nunca se reutilizan; el backend falso empieza en
    # 1000 en cada test, así que un manifest_msg_id de otro test podría colisionar. Se resetea.
    svc._guardar_manifest_msg_id(acc, None)
    svc.subir_archivo(acc, None, _crear_local(tmp_path, "a.txt", b"a"))
    svc.exportar_indice(acc)
    svc.exportar_indice(acc)
    svc.exportar_indice(acc)
    manifiestos = [mid for mid, m in backend.mensajes.items()
                   if m.get("name") == man.NOMBRE_MANIFIESTO]
    assert len(manifiestos) == 1, f"quedaron {len(manifiestos)} manifiestos, debería quedar 1"


def test_revisar_canal_limpia_manifiestos_viejos(servicio, tmp_path):
    """revisar_canal marca como huérfanos los manifiestos viejos (todos menos el más reciente)."""
    from app.services.tgdrive import tgdrive_manifest as man
    svc, acc = servicio["svc"], servicio["acc"]
    backend = servicio["backend"]
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    # Simular manifiestos viejos sueltos en el canal.
    backend.mensajes[5001] = {"bytes": b"{}", "name": man.NOMBRE_MANIFIESTO}
    backend.mensajes[5002] = {"bytes": b"{}", "name": man.NOMBRE_MANIFIESTO}
    backend.mensajes[5003] = {"bytes": b"{}", "name": man.NOMBRE_MANIFIESTO}
    rep = svc.revisar_canal(acc)
    # Los dos más antiguos son huérfanos; el más nuevo (5003) se conserva.
    assert 5001 in rep["orfanos"] and 5002 in rep["orfanos"]
    assert 5003 not in rep["orfanos"]
    svc.limpiar_orfanos_canal(acc, rep["orfanos"])
    restantes = [mid for mid, m in backend.mensajes.items()
                 if m.get("name") == man.NOMBRE_MANIFIESTO]
    assert restantes == [5003]


def test_purga_avanzada_del_canal(servicio, tmp_path):
    """listar_canal_detallado marca qué es referenciado/suelto/manifiesto; purgar_mensajes_canal
    borra del canal Y quita del índice los archivos afectados."""
    from app.services.tgdrive import tgdrive_manifest as man
    svc, acc, r = servicio["svc"], servicio["acc"], servicio["repo"]
    backend = servicio["backend"]
    db.execute("DELETE FROM tgdrive_items WHERE account_id = ?", (acc,))
    it = svc.subir_archivo(acc, None, _crear_local(tmp_path, "real.txt", b"real"))
    mid_real = r.obtener_partes(it.id)[0].message_id
    backend.mensajes[7001] = {"bytes": b"x", "name": "suelto.bin"}          # suelto
    backend.mensajes[7002] = {"bytes": b"{}", "name": man.NOMBRE_MANIFIESTO}  # índice
    detalle = {d["message_id"]: d for d in svc.listar_canal_detallado(acc)}
    assert detalle[mid_real]["referenciado"] and detalle[mid_real]["item"] == "real.txt"
    assert detalle[7001]["referenciado"] is False
    assert detalle[7002]["es_manifiesto"] is True
    # Purga: borrar el suelto y el archivo real (su mensaje). El item real se quita del índice.
    n = svc.purgar_mensajes_canal(acc, [7001, mid_real])
    assert n == 2
    assert 7001 not in backend.mensajes and mid_real not in backend.mensajes
    assert r.obtener_item(it.id) is None            # el archivo real se quitó del índice
    assert 7002 in backend.mensajes                 # lo no marcado sigue


def test_floodwait_se_reintenta():
    """El envoltorio de reintentos espera ante FloodWait y reintenta (unidad, sin red)."""
    import asyncio
    from app.services.tgdrive.tgdrive_storage import _DriveStorageMixin

    class _FW(Exception):
        value = 0  # segundos que pide Telegram

    llamadas = {"n": 0}

    async def _factory():
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            e = _FW()
            e.__class__.__name__ = "FloodWait"
            raise e
        return "ok"

    obj = _DriveStorageMixin()

    async def _run():
        return await obj._drive_llamar(_factory)

    async def _fake_sleep(_s):
        return None
    real = asyncio.sleep
    asyncio.sleep = _fake_sleep
    loop = asyncio.new_event_loop()
    try:
        assert loop.run_until_complete(_run()) == "ok"
    finally:
        loop.close()
        asyncio.sleep = real
    assert llamadas["n"] == 2  # falló una vez, reintentó y funcionó
