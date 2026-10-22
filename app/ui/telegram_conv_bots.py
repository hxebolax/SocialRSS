# -*- coding: utf-8 -*-
"""Conversación Telegram: interacción con BOTS (mixin).

Abre los diálogos accesibles de botones, comandos y modo inline, y conecta con el servicio.
"""
import wx

from .accessible_controls import CustomTextEntryDialog


class _ConvBotsMixin:
    def _es_bot(self) -> bool:
        """True si este chat es un bot (para ofrecer las acciones específicas de bots)."""
        return getattr(self, "chat_type", "") == "bot"

    def _on_botones_mensaje(self, meta):
        """Abre el diálogo de botones (teclado) de un mensaje. Al pulsar un botón, el diálogo se
        cierra y el foco vuelve a la lista de mensajes para seguir interactuando con el bot."""
        from .telegram_bot_dialogs import BotonesMensajeDialog
        dlg = BotonesMensajeDialog(self, self.account_id, self.chat_id, meta["message_id"])
        dlg.ShowModal()
        dlg.Destroy()
        # Devolver el foco a la conversación (a la lista de mensajes) tras cerrar el diálogo.
        try:
            self.lista_msgs.SetFocus()
        except Exception:
            pass

    def _on_comandos_bot(self):
        """Lista y ejecuta los comandos publicados por el bot."""
        from .telegram_bot_dialogs import ComandosBotDialog
        dlg = ComandosBotDialog(self, self.account_id, self.chat_id)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_iniciar_bot(self):
        """Inicia el bot con /start, opcionalmente con un parámetro de enlace profundo."""
        dlg = CustomTextEntryDialog(
            self, _("Parámetro de inicio (opcional, déjalo vacío para un /start normal):"),
            _("Iniciar bot"))
        parametro = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if parametro is None:
            return
        self._accion_simple(
            lambda: self.telegram_service_iniciar_bot(parametro),
            _("Bot iniciado."), _("Iniciando bot..."))

    def telegram_service_iniciar_bot(self, parametro):
        from app.services.telegram.telegram_service import telegram_service
        return telegram_service.iniciar_bot(self.account_id, self.chat_id, parametro or "")

    def _on_consulta_inline(self):
        """Abre el diálogo de consulta inline. Si el chat es un bot, lo propone como destinatario."""
        from .telegram_bot_dialogs import InlineQueryDialog
        bot_predef = str(self.chat_id) if self._es_bot() else ""
        dlg = InlineQueryDialog(self, self.account_id, self.chat_id, bot_predef=bot_predef)
        dlg.ShowModal()
        dlg.Destroy()
