# -*- coding: utf-8 -*-
"""Fachada de los diálogos de Telegram: re-exporta desde los submódulos.
"""
from .telegram_dialogs_cuenta import TelegramLoginDialog, TelegramQRDialog
from .telegram_dialogs_admin import TelegramAdminDialog, TelegramPropiedadesDialog
from .telegram_dialogs_perfil import (TelegramProfileDialog, TelegramSessionsDialog,
    TelegramCarpetasDialog, TelegramHistoriasDialog, TelegramChatsSugeridosDialog,
    TelegramPrivacidadGlobalDialog, TelegramAjustesCuentaDialog, TelegramMisBotsDialog,
    TelegramMediosChatDialog)
from .telegram_conversation import TelegramConversationDialog

__all__ = ['TelegramLoginDialog', 'TelegramQRDialog', 'TelegramAdminDialog',
           'TelegramPropiedadesDialog', 'TelegramProfileDialog', 'TelegramSessionsDialog',
           'TelegramCarpetasDialog', 'TelegramHistoriasDialog', 'TelegramChatsSugeridosDialog',
           'TelegramPrivacidadGlobalDialog', 'TelegramAjustesCuentaDialog', 'TelegramMisBotsDialog',
           'TelegramMediosChatDialog', 'TelegramConversationDialog']
