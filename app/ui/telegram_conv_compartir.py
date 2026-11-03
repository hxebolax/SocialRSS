# -*- coding: utf-8 -*-
"""Conversación Telegram: marcar varios mensajes y COMPARTIRLOS con otros chats (mixin).

- Ctrl+Espacio en la lista marca o desmarca el mensaje para compartir (como en las listas de
  selección múltiple de Windows). La fila marcada empieza por «Marcado:», así el lector lo dice
  al pasar por ella.
- Ctrl+Mayús+C (o «Compartir...» del menú) abre el diálogo con los marcados; si no hay ninguno,
  con el mensaje enfocado.
Va ANTES de _ConvMensajesMixin en la herencia para reaplicar las marcas tras recargar la lista.
"""
import logging

import wx

from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from app.services.telegram.telegram_service import telegram_service
from .accessible_controls import mostrar_info

log = logging.getLogger(__name__)


def _prefijo_marca() -> str:
    return _("Marcado: ")


class _ConvCompartirMixin:
    # ------------------------------------------------------------------ marcas
    def _marcas_compartir(self) -> set:
        if not hasattr(self, "_marcas_comp"):
            self._marcas_comp = set()
        return self._marcas_comp

    def _pintar_marca(self, fila: int, marcado: bool):
        """Pone o quita «Marcado: » al principio de la primera columna de la fila."""
        try:
            texto = self.lista_msgs.GetItemText(fila, 0)
            prefijo = _prefijo_marca()
            if marcado and not texto.startswith(prefijo):
                self.lista_msgs.SetItem(fila, 0, prefijo + texto)
            elif not marcado and texto.startswith(prefijo):
                self.lista_msgs.SetItem(fila, 0, texto[len(prefijo):])
        except Exception as e:
            log.debug(f"No se pudo pintar la marca de compartir: {e}")

    def _cargar_mensajes_cache(self):
        super()._cargar_mensajes_cache()
        marcas = self._marcas_compartir()
        if not marcas:
            return
        for i, meta in enumerate(getattr(self, "msg_rows", []) or []):
            if meta.get("message_id") in marcas:
                self._pintar_marca(i, True)

    def _alternar_marca_compartir(self, idx: int):
        if not (0 <= idx < len(self.msg_rows)):
            return
        meta = self.msg_rows[idx]
        mid = meta.get("message_id")
        if meta.get("_separador") or mid is None:
            return
        if getattr(self, "_protegido", False):
            speak(_("Este chat tiene el contenido protegido: Telegram no permite reenviar sus mensajes."),
                  interrupt=True)
            return
        marcas = self._marcas_compartir()
        if mid in marcas:
            marcas.discard(mid)
            self._pintar_marca(idx, False)
            estado = _("Desmarcado")
        else:
            marcas.add(mid)
            self._pintar_marca(idx, True)
            estado = _("Marcado")
        n = len(marcas)
        cuantos = (_("ninguno marcado") if n == 0 else _("1 marcado") if n == 1
                   else _("{0} marcados").format(n))
        speak(f"{estado}. {cuantos}.", interrupt=True)

    def _desmarcar_todos_compartir(self, avisar: bool = True):
        marcas = self._marcas_compartir()
        for i, meta in enumerate(self.msg_rows):
            if meta.get("message_id") in marcas:
                self._pintar_marca(i, False)
        marcas.clear()
        if avisar:
            speak(_("Ningún mensaje marcado."), interrupt=True)

    # ------------------------------------------------------------------ compartir
    def _on_compartir(self, meta=None):
        """Abre «Compartir» con los mensajes marcados o, si no hay, con el enfocado/indicado."""
        if getattr(self, "_protegido", False):
            speak(_("Este chat tiene el contenido protegido: Telegram no permite reenviar sus mensajes."),
                  interrupt=True)
            return
        marcas = self._marcas_compartir()
        if marcas:
            ids = sorted(marcas)
        else:
            if meta is None:
                idx = self.lista_msgs.GetFocusedItem()
                meta = self.msg_rows[idx] if 0 <= idx < len(self.msg_rows) else None
            if not meta or meta.get("_separador") or meta.get("message_id") is None:
                speak(_("Elige un mensaje para compartir."), interrupt=True)
                return
            ids = [meta["message_id"]]
        hay_album = any(m.get("media_group_id") for m in self.msg_rows if m.get("message_id") in ids)

        from .telegram_compartir_dialog import CompartirTelegramDialog
        dlg = CompartirTelegramDialog(self, self.account_id, self.chat_title, len(ids),
                                      hay_album=hay_album)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            op = dlg.obtener()
        finally:
            dlg.Destroy()
        if not op:
            return
        self._ejecutar_compartir(ids, op)

    def _ejecutar_compartir(self, ids, op):
        account_id, chat_id = self.account_id, self.chat_id
        destinos = op["destinos"]
        n_dest = len(destinos)

        def _bg(ctx):
            mids = telegram_service.ids_del_album(account_id, chat_id, ids) if op["album"] else ids

            def _progreso(hechos, total, res):
                if total > 1 and not res["ok"]:
                    wx.CallAfter(speak, _("Falló en {0}.").format(res["titulo"]), interrupt=False)
            return telegram_service.compartir_mensajes(
                account_id, chat_id, mids, destinos, ocultar_autor=op["ocultar_autor"],
                ocultar_pies=op["ocultar_pies"], comentario=op["comentario"],
                silencioso=op["silencioso"], programar=op["programar"], al_progresar=_progreso)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo compartir: {0}").format(err))
                return
            bien = [r for r in res if r["ok"]]
            mal = [r for r in res if not r["ok"]]
            if bien:
                # Lo compartido sale de la selección: así no se reenvía dos veces sin querer.
                try:
                    if self:
                        self._desmarcar_todos_compartir(avisar=False)
                except Exception:
                    pass
            verbo = _("programado") if op["programar"] else _("compartido")
            if not mal:
                if n_dest == 1:
                    notificar(_("{0} en {1}.").format(verbo.capitalize(), bien[0]["titulo"]))
                else:
                    notificar(_("{0} en {1} chats.").format(verbo.capitalize(), n_dest))
                return
            lineas = [_("{0}: {1}").format(r["titulo"], r["error"]) for r in mal]
            if bien:
                cabecera = _("Se ha {0} en {1} de {2} chats. No se pudo en:").format(
                    verbo, len(bien), n_dest)
            else:
                cabecera = _("No se pudo compartir en ningún chat:")
            notificar(cabecera.rstrip(":") + ".")
            try:
                mostrar_info(self if self else None, cabecera + "\n\n" + "\n".join(lineas),
                             _("Resultado de compartir"))
            except Exception:
                pass

        if n_dest == 1:
            speak(_("Compartiendo en {0}...").format(destinos[0]["titulo"]), interrupt=True)
        else:
            speak(_("Compartiendo en {0} chats...").format(n_dest), interrupt=True)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)
