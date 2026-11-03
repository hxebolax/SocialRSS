# -*- coding: utf-8 -*-
"""Telegram: COMPARTIR uno o varios mensajes con uno o varios chats (mixin del servicio).

Sustituye al "Reenviar" básico (un mensaje, un destino, sin opciones). Lo que permite, igual que
los clientes oficiales:
- Varios mensajes a la vez (se reenvían en orden cronológico y los álbumes llegan agrupados).
- Varios destinos a la vez: personas, grupos, canales, bots y «Mensajes guardados»; en un foro,
  el tema concreto.
- Ocultar el autor original (llega como si lo enviaras tú) y, además, quitar los pies de foto.
- Un comentario propio que se envía justo antes de los mensajes.
- Envío silencioso y envío programado.

Un fallo en un destino no detiene los demás: se devuelve el resultado de CADA destino con un
motivo comprensible ("no tienes permiso para escribir en este chat"...).
"""
import logging
import time
from typing import Callable, List, Optional

log = logging.getLogger(__name__)

# forwardMessages admite como mucho 100 mensajes por llamada.
_LOTE_MAX = 100
# Pausa entre destinos: varios reenvíos seguidos a chats distintos disparan el FloodWait.
_PAUSA_ENTRE_DESTINOS = 0.4


def motivo_error_compartir(err) -> str:
    """Traduce el error de Telegram al compartir a una frase entendible (por NOMBRE de clase, sin
    importar pyrogram: la carga del SDK es diferida)."""
    nombre = type(err).__name__
    valor = getattr(err, "value", None)
    motivos = {
        "ChatWriteForbidden": _("no tienes permiso para escribir en este chat"),
        "ChatForwardsRestricted": _("el chat de origen tiene el contenido protegido y no permite reenviar"),
        "ChatSendMediaForbidden": _("en este chat no se permite enviar multimedia"),
        "ChatSendPhotosForbidden": _("en este chat no se permite enviar fotos"),
        "ChatSendVideosForbidden": _("en este chat no se permite enviar vídeos"),
        "ChatSendAudiosForbidden": _("en este chat no se permite enviar audios"),
        "ChatSendVoicesForbidden": _("en este chat no se permite enviar notas de voz"),
        "ChatSendDocsForbidden": _("en este chat no se permite enviar archivos"),
        "ChatSendStickersForbidden": _("en este chat no se permite enviar stickers"),
        "ChatSendGifsForbidden": _("en este chat no se permite enviar GIF"),
        "ChatSendPollForbidden": _("en este chat no se permite enviar encuestas"),
        "ChatSendPlainForbidden": _("en este chat no se permite enviar texto"),
        "ChatAdminRequired": _("hace falta ser administrador de este chat"),
        "UserIsBlocked": _("esta persona te ha bloqueado"),
        "YouBlockedUser": _("tienes bloqueada a esta persona"),
        "UserBannedInChannel": _("tu cuenta está restringida y no puede escribir en grupos públicos"),
        "ChannelPrivate": _("ya no tienes acceso a este chat"),
        "PeerIdInvalid": _("no se encuentra el chat; ábrelo una vez y vuelve a intentarlo"),
        "MessageIdInvalid": _("alguno de los mensajes ya no existe"),
        "TopicClosed": _("el tema del foro está cerrado"),
        "ScheduleDateTooLate": _("la fecha programada está demasiado lejos"),
        "ScheduleTooMuch": _("este chat ya tiene demasiados mensajes programados"),
        "PrivacyPremiumRequired": _("esta persona solo recibe mensajes de usuarios Premium"),
    }
    if nombre == "SlowmodeWait":
        return _("el chat tiene modo lento; espera {0} segundos").format(valor or "?")
    if nombre == "FloodWait":
        return _("Telegram pide esperar {0} segundos antes de seguir enviando").format(valor or "?")
    if nombre in motivos:
        return motivos[nombre]
    texto = str(err).strip()
    return texto or nombre


class _CompartirMixin:
    def ids_del_album(self, account_id: int, chat_id: int, message_ids) -> List[int]:
        """Amplía la lista con TODOS los mensajes de los álbumes a los que pertenece alguno de los
        mensajes (según la caché local). Mantiene el orden cronológico (por id)."""
        from app.core.database import db
        ids = {int(m) for m in message_ids}
        try:
            grupos = db.query(
                "SELECT DISTINCT media_group_id FROM telegram_messages WHERE account_id = ? AND "
                "chat_id = ? AND media_group_id IS NOT NULL AND media_group_id != '' AND message_id IN "
                f"({','.join('?' * len(ids))})", (account_id, chat_id, *ids)) if ids else []
            for (grupo,) in grupos:
                for (mid,) in db.query(
                        "SELECT message_id FROM telegram_messages WHERE account_id = ? AND "
                        "chat_id = ? AND media_group_id = ?", (account_id, chat_id, grupo)):
                    ids.add(int(mid))
        except Exception as e:
            log.debug(f"No se pudieron ampliar los álbumes al compartir: {e}")
        return sorted(ids)

    def compartir_mensajes(self, account_id: int, from_chat_id: int, message_ids,
                           destinos: List[dict], ocultar_autor: bool = False,
                           ocultar_pies: bool = False, comentario: str = "",
                           silencioso: bool = False, programar=None,
                           al_progresar: Optional[Callable[[int, int, dict], None]] = None
                           ) -> List[dict]:
        """Comparte `message_ids` del chat `from_chat_id` en cada destino.

        destinos: [{"chat_id": int|str, "titulo": str, "tema": int|None}, ...]
        ocultar_pies implica ocultar_autor (Telegram solo quita los pies en la copia sin autor).
        programar: datetime futuro o None. al_progresar(hechos, total, resultado) se llama (en el
        hilo de trabajo) tras cada destino.

        Devuelve [{"chat_id", "titulo", "ok", "enviados", "error"}] en el orden de `destinos`."""
        ids = sorted({int(m) for m in (message_ids or [])})
        if not ids:
            raise ValueError("No hay mensajes que compartir.")
        if ocultar_pies:
            ocultar_autor = True
        comentario = (comentario or "").strip()
        resultados = []
        total = len(destinos)
        for n, destino in enumerate(destinos, 1):
            chat_id = destino.get("chat_id")
            tema = destino.get("tema") or None
            res = {"chat_id": chat_id, "titulo": destino.get("titulo") or str(chat_id),
                   "ok": False, "enviados": 0, "error": ""}

            async def _coro(chat_id=chat_id, tema=tema):
                client = await self._cliente(account_id)
                comunes = {"disable_notification": bool(silencioso) or None,
                           "schedule_date": programar}
                hilo = {"message_thread_id": tema} if tema else {}
                enviados = []
                if comentario:
                    enviados.append(await client.send_message(chat_id, comentario, **comunes, **hilo))
                for i in range(0, len(ids), _LOTE_MAX):
                    r = await client.forward_messages(
                        chat_id, from_chat_id, ids[i:i + _LOTE_MAX],
                        hide_sender_name=bool(ocultar_autor) or None,
                        hide_captions=bool(ocultar_pies) or None, **comunes, **hilo)
                    enviados.extend(r if isinstance(r, list) else [r])
                return enviados

            try:
                enviados = self._run(_coro(), timeout=120) or []
                res["ok"] = True
                res["enviados"] = sum(1 for m in enviados if m is not None)
                if not programar:
                    for m in enviados:
                        try:
                            if m is not None:
                                self._guardar_mensaje(account_id, m)
                        except Exception as e:
                            log.debug(f"No se pudo guardar en caché un mensaje compartido: {e}")
            except Exception as e:
                res["error"] = motivo_error_compartir(e)
                log.warning(f"Compartir en Telegram: falló el destino {res['titulo']}: "
                            f"{type(e).__name__}: {e}")
            resultados.append(res)
            if al_progresar:
                try:
                    al_progresar(n, total, res)
                except Exception:
                    pass
            if n < total:
                time.sleep(_PAUSA_ENTRE_DESTINOS)
        return resultados
