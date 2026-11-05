# -*- coding: utf-8 -*-
"""Tiempo real de Bluesky (Jetstream, con el firehose clásico de reserva) para la interfaz GTK.

Un suscriptor por cuenta, filtrado en el servidor por las cuentas que sigues y la propia. El contenido lo
trae el sondeo normal (formato y orden correctos): el tiempo real solo avisa de que hay novedades o de que
te han mencionado o respondido."""

import logging

from app.core.plataforma import llamar_en_ui
from app.core.task_manager import task_manager
from app.services.bluesky.bluesky_service import bluesky_service

log = logging.getLogger(__name__)


def _clase():
    try:
        from app.services.bluesky.bluesky_jetstream import HAS_JETSTREAM, BlueskyJetstream
        if HAS_JETSTREAM:
            return BlueskyJetstream
    except Exception:
        pass
    try:
        from app.services.bluesky.bluesky_firehose import HAS_FIREHOSE, BlueskyFirehose
        if HAS_FIREHOSE:
            return BlueskyFirehose
    except Exception:
        pass
    return None


class TiempoRealBluesky:
    def __init__(self, al_novedad, al_aviso):
        """al_novedad(account_id) y al_aviso(account_id, info) se llaman SIEMPRE en el hilo de la interfaz."""
        self._al_novedad = al_novedad
        self._al_aviso = al_aviso
        self._activos = {}
        self._pendientes = set()

    def sincronizar(self, cuentas):
        deseadas = set(cuentas)
        for acc in list(self._activos):
            if acc not in deseadas:
                self._detener_uno(acc)
        for acc in deseadas:
            if acc not in self._activos and acc not in self._pendientes:
                self._arrancar(acc)

    def _arrancar(self, acc):
        clase = _clase()
        if clase is None:
            log.info("Tiempo real de Bluesky no disponible (falta la librería websockets)")
            return
        self._pendientes.add(acc)

        def _bg(ctx):
            client = bluesky_service.obtener_cliente(acc)
            mi_did = bluesky_service._yo(acc)["did"]
            dids, cursor = [], None
            for _i in range(100):
                res = client.get_follows(mi_did, cursor=cursor)
                dids.extend(getattr(f, "did", "") for f in (getattr(res, "follows", []) or []))
                cursor = getattr(res, "cursor", None)
                if not cursor or len(dids) >= 10000:
                    break
            if mi_did and mi_did not in dids:
                dids.append(mi_did)
            return mi_did, dids

        def _fin(datos, err):
            self._pendientes.discard(acc)
            if err or not datos or acc in self._activos:
                log.debug(f"No se pudo activar el tiempo real de Bluesky ({acc}): {err}")
                return
            mi_did, dids = datos
            kw = {"mi_did": mi_did, "dids_seguidos": dids,
                  "on_match": lambda info, a=acc: llamar_en_ui(self._al_aviso, a, info),
                  "on_follow_activity": lambda a=acc: llamar_en_ui(self._al_novedad, a)}
            if clase.__name__ == "BlueskyJetstream":
                kw["clave_cursor"] = f"rt:{acc}"
            suscriptor = clase(**kw)
            if suscriptor.start():
                self._activos[acc] = suscriptor
                log.info(f"Tiempo real de Bluesky activado ({len(dids)} cuentas)")
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _detener_uno(self, acc):
        s = self._activos.pop(acc, None)
        if s is not None:
            try:
                s.stop()
            except Exception:
                pass

    def detener(self):
        for acc in list(self._activos):
            self._detener_uno(acc)
