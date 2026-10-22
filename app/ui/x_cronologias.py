# -*- coding: utf-8 -*-
"""Cronologías de usuario de X como buffer (mixin de XController): abrir la de un @usuario,
la del autor o la de alguien mencionado, en cualquiera de sus modos (publicaciones, con
respuestas, solo multimedia o sus me gusta). Separado de x_controller.py por tamaño."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.services.x import x_service
from .accessible_controls import CustomTextEntryDialog


class _XCronologiasMixin:
    # ---------------- Cronologías de usuario ----------------
    _MODOS_CRONOLOGIA = (("tweets", _("Solo publicaciones")),
                         ("replies", _("Publicaciones y respuestas")),
                         ("media", _("Solo multimedia")),
                         ("likes", _("Sus me gusta (lo que le ha gustado)")))

    def abrir_cronologia_usuario(self):
        """Pide un @usuario (y qué mostrar) y abre sus publicaciones como buffer permanente."""
        acc = self._cuenta()
        if not acc:
            return
        dlg = CustomTextEntryDialog(self.f, _("Usuario de X (con o sin @):"),
                                    _("Abrir cronología de un usuario"))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        screen = dlg.GetValue().strip().lstrip("@")
        dlg.Destroy()
        if screen:
            self.crear_cronologia_por_nombre(screen)

    def cronologia_usuario_mencionado(self, db_id=None):
        """Cronología de alguien que aparece en el tweet (mencionado, citado o quien lo retuiteó):
        se ofrecen todas esas cuentas —menos la tuya— y se abre la elegida."""
        screen = self.f._elegir_usuario_mencionado(_("Cronología de un usuario mencionado"), db_id)
        if screen:
            self.crear_cronologia_por_nombre(screen)

    def perfil_usuario_mencionado(self, db_id=None):
        """Ficha de alguien que aparece en el tweet (mencionado, citado o quien lo retuiteó).
        Mismo selector de cuentas que la cronología: si solo hay una, no pregunta."""
        screen = self.f._elegir_usuario_mencionado(_("Perfil de un usuario mencionado"), db_id)
        if screen:
            self.ver_perfil(screen)

    def seguir_usuario_mencionado(self, db_id=None):
        """Sigue a alguien que aparece en el tweet. Como seguir necesita el identificador
        interno de la cuenta y aquí solo se tiene el @usuario, primero se resuelve."""
        screen = self.f._elegir_usuario_mencionado(_("Seguir a un usuario mencionado"), db_id)
        if not screen:
            return
        acc = self._cuenta()
        if not acc:
            return
        speak(_("Buscando al usuario..."), interrupt=True)

        def _cb(info, err):
            if err or not info or not info.get("user_id"):
                notificar(_("No se encontró a @{0} en X.").format(screen))
                return
            self.accion_cuenta(info["user_id"], "follow")
        x_service.resolver_usuario_async(acc, screen, callback=_cb)

    def crear_cronologia_por_nombre(self, screen):
        """Abre la cronología de un @usuario YA conocido (viene escrito o elegido de una lista,
        por ejemplo de los usuarios mencionados en un tweet). Pregunta qué mostrar y la crea."""
        acc = self._cuenta()
        screen = (screen or "").strip().lstrip("@")
        if not acc or not screen:
            return
        etiquetas = [e for _c, e in self._MODOS_CRONOLOGIA]
        sel = wx.GetSingleChoice(_("¿Qué quieres ver de @{0}?").format(screen),
                                 _("Cronología de usuario"), etiquetas, self.f)
        if not sel:
            return
        modo = self._MODOS_CRONOLOGIA[etiquetas.index(sel)][0]
        speak(_("Buscando al usuario..."), interrupt=True)

        def _cb(info, err):
            if err or not info or not info.get("user_id"):
                notificar(_("No se encontró ese usuario en X."))
                return
            self._guardar_cronologia(acc, info["user_id"], info.get("screen_name") or screen,
                                     info.get("name") or "", modo)
        x_service.resolver_usuario_async(acc, screen, callback=_cb)

    def ver_cronologia_autor(self, author_id, screen, name=""):
        """Desde el menú contextual de un tweet: abre la cronología (publicaciones) de su autor."""
        acc = self._cuenta()
        if not acc or not author_id:
            notificar(_("No se pudo identificar al autor."))
            return
        self._guardar_cronologia(acc, str(author_id), screen or "", name or "", "tweets")

    def _guardar_cronologia(self, acc, uid, screen, name, modo):
        """Inserta (si no existe) la cronología de usuario, reconstruye el árbol y la selecciona."""
        existe = db.query_one(
            "SELECT id FROM x_user_timelines WHERE account_id = ? AND user_id = ? AND COALESCE(modo,'tweets') = ?",
            (acc, str(uid), modo))
        if not existe:
            db.execute_insert(
                "INSERT INTO x_user_timelines (account_id, user_id, screen_name, name, modo, position) "
                "VALUES (?,?,?,?,?,0)", (acc, str(uid), screen, name, modo))
        self.f._cargar_navegacion()
        x_type = "user:" + str(uid) + ("" if modo == "tweets" else ":" + modo)
        self.f._re_seleccionar_x_timeline(acc, x_type)
        notificar(_("Cronología de @{0} abierta como buffer.").format(screen or uid))
