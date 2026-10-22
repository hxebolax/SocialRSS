# -*- coding: utf-8 -*-
"""Acciones sociales y de administración de W Social en la interfaz: cuentas, listas, grupos,
moderación y el menú &W Social.

Mixin de WSocialController.
"""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.config_manager import config
from app.core.database import db
from app.core.speech import speak, notificar
from app.services.wsocial import wsocial_service
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog


def mensaje_estado_cuenta(accion, estado):
    """Confirmación de seguir/bloquear/silenciar (o deshacerlo) con el estado real de la cuenta
    tal y como lo ha devuelto el servidor ("@cuenta está bloqueada"). Antes se decía solo
    "Cuenta bloqueada." aunque la caché no se enterase y el menú siguiera ofreciendo "Bloquear"."""
    est = estado if isinstance(estado, dict) else {}
    quien = "@" + est["handle"] if est.get("handle") else _("La cuenta")
    confirmado = {"follow": bool(est.get("following_uri", True)),
                  "unfollow": not est.get("following_uri"),
                  "block": bool(est.get("blocking_uri", True)),
                  "unblock": not est.get("blocking_uri"),
                  "mute": bool(est.get("muted", True)),
                  "unmute": not est.get("muted")}
    if not confirmado.get(accion, True):
        return _("El servidor aún no confirma el cambio en {0}.").format(quien)
    mensajes = {
        "follow": _("Ahora sigues a {0}."),
        "unfollow": _("Ya no sigues a {0}."),
        "block": _("{0} está bloqueada."),
        "unblock": _("{0} ya no está bloqueada."),
        "mute": _("{0} está silenciada."),
        "unmute": _("{0} ya no está silenciada."),
    }
    return mensajes.get(accion, _("Hecho: {0}.")).format(quien)


class _SocialMixin:
    # ------------------------------------------------------------------
    # UTILIDADES
    # ------------------------------------------------------------------
    def _cuenta_activa_o_primera(self):
        """La cuenta de W Social activa o, si no hay ninguna enfocada, la primera dada de alta."""
        f = self.f
        if f.active_wsocial_account_id:
            return f.active_wsocial_account_id
        fila = db.query_one("SELECT id FROM accounts WHERE service_id = 'wsocial' AND active = 1 "
                            "ORDER BY position, id LIMIT 1")
        return fila[0] if fila else None

    def _pedir_texto(self, mensaje, titulo, valor=""):
        dlg = CustomTextEntryDialog(self.f, mensaje, titulo, value=valor)
        texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        return texto

    def _resultado(self, ok, res, exito, recargar=False):
        """Aviso común de resultado de una acción."""
        if ok:
            notificar(exito)
            if recargar:
                wx.CallAfter(self.cargar_listado)
        else:
            notificar(_("No se pudo: {0}").format(res))

    # ------------------------------------------------------------------
    # CUENTAS
    # ------------------------------------------------------------------
    def accion_cuenta(self, actor, accion):
        """Seguir, dejar de seguir, silenciar, bloquear... sobre una cuenta."""
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        # Se confirma con el estado REAL que devuelve el servidor y ya está en la caché, así que
        # el menú ofrece la acción contraria. No se recarga la lista para no mover el foco:
        # Bloqueados/Silenciados/Siguiendo lo muestran al volver a entrar.
        wsocial_service.accion_cuenta_async(
            acc, actor, accion,
            callback=lambda ok, res: notificar(
                mensaje_estado_cuenta(accion, res) if ok else _("No se pudo: {0}").format(res)))

    def mensaje_directo_a(self, actor):
        """Abre (o crea) la conversación con una cuenta."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        speak(_("Abriendo la conversación..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo abrir la conversación: {0}").format(res))
                return
            def _abrir():
                from .wsocial_dialogs import WSocialConversationDialog
                dlg = WSocialConversationDialog(f, acc, res["convo_id"], res["title"])
                dlg.ShowModal()
                dlg.Destroy()
            wx.CallAfter(_abrir)
        wsocial_service.abrir_convo_con_async(acc, actor, callback=_cb)

    def buscar_cuentas(self):
        """Busca cuentas y las muestra en la lista principal para poder actuar sobre ellas."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        texto = self._pedir_texto(_("Nombre o usuario a buscar:"), _("Buscar cuentas"))
        if not texto:
            return
        speak(_("Buscando..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo buscar: {0}").format(res))
                return
            def _pintar():
                f._modo_lista = "wsocial_actores"
                self._preparar_columnas([(_("Nombre"), 220), (_("Usuario"), 220),
                                         (_("Descripción"), 400), (_("Estado"), 160)])
                for i, a in enumerate(res):
                    pos = f.lista_items.InsertItem(i, a.get("display_name") or a.get("handle", ""))
                    f.lista_items.SetItem(pos, 1, "@" + a.get("handle", ""))
                    f.lista_items.SetItem(pos, 2, a.get("description", ""))
                    f.lista_items.SetItem(pos, 3, _("La sigues") if a.get("following_uri") else "")
                    f.lista_items_db_ids.append("actor:" + a.get("did", ""))
                    f.lista_items.establecer_lectura(
                        pos, f"{a.get('display_name') or a.get('handle')}. @{a.get('handle')}. "
                             f"{a.get('description', '')}")
                f._restaurar_foco_lista()
                speak(_("{0} cuentas encontradas.").format(len(res)), interrupt=True)
            wx.CallAfter(_pintar)
        wsocial_service.buscar_cuentas_async(acc, texto, callback=_cb)

    def crear_buffer_usuario_mencionado(self, db_id=None):
        """Cronología de alguien que aparece en la publicación (mencionado, citado o quien la
        republicó), nunca la tuya."""
        handle = self.f._elegir_usuario_mencionado(_("Publicaciones de un usuario mencionado"), db_id)
        if not handle:
            return
        acc = self._cuenta_activa_o_primera()

        def _cb(ok, res):
            if ok and res.get("did"):
                wx.CallAfter(self.crear_buffer_usuario, res["did"], res.get("handle") or handle)
            else:
                notificar(_("No se encontró la cuenta @{0}.").format(handle))
        wsocial_service.obtener_perfil_async(acc, handle, callback=_cb)

    def ver_interacciones(self, db_id, tipo="likes"):
        """Quién ha dado me gusta o ha republicado una publicación."""
        f = self.f
        fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            return
        speak(_("Consultando..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo consultar: {0}").format(res))
                return
            titulo = _("Me gusta") if tipo == "likes" else _("Republicaciones")
            texto = "\n".join(f"{a.get('display_name') or a.get('handle')} (@{a.get('handle')})"
                              for a in res) or _("Todavía no hay nadie.")
            wx.CallAfter(f._mostrar_texto_largo, titulo, texto)
        wsocial_service.interacciones_async(f.active_wsocial_account_id, fila[0], tipo, callback=_cb)

    def ver_citas(self, db_id):
        """Abre como buffer quién ha citado una publicación."""
        f = self.f
        fila = db.query_one("SELECT uri FROM wsocial_posts WHERE id = ?", (db_id,))
        if not fila:
            return
        tipo = "quotes:" + fila[0]
        f.active_timeline_type = tipo
        wsocial_service.cargar_timeline_async(
            f.active_wsocial_account_id, tipo,
            callback=lambda ok, res: wx.CallAfter(self.cargar_listado) if ok else None)
        speak(_("Cargando quién ha citado esta publicación..."), interrupt=True)

    def silenciar_hilo(self, root_uri, silenciar=True):
        """Deja de recibir avisos de una conversación."""
        acc = self._cuenta_activa_o_primera()
        wsocial_service.silenciar_hilo_async(
            acc, root_uri, silenciar,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Conversación silenciada.") if silenciar else _("Ya no está silenciada.")))

    def fijar_post(self, uri, cid, fijar=True):
        """Fija o deja de fijar una publicación propia en el perfil."""
        acc = self._cuenta_activa_o_primera()
        wsocial_service.fijar_post_async(
            acc, uri, cid, fijar,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Publicación fijada.") if fijar else _("Ya no está fijada.")))

    def marcar_notificaciones_leidas(self):
        """Marca todas las notificaciones como leídas."""
        acc = self._cuenta_activa_o_primera()
        wsocial_service.marcar_notificaciones_vistas_async(
            acc, callback=lambda ok, res: self._resultado(
                ok, res, _("Notificaciones marcadas como leídas."), recargar=True))

    # ------------------------------------------------------------------
    # LISTAS
    # ------------------------------------------------------------------
    def anadir_a_lista(self, did):
        """Añade una cuenta a una de tus listas (o la quita si ya está)."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        speak(_("Consultando tus listas..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron consultar las listas: {0}").format(res))
                return
            if not res:
                wx.CallAfter(mostrar_info, f, _("Todavía no tienes ninguna lista. Créala desde el "
                                                "menú W Social."), _("Sin listas"))
                return
            def _elegir():
                opciones = [("✔ " if l["pertenece"] else "") + l["name"] for l in res]
                sel = wx.GetSingleChoice(
                    _("Elige la lista (las marcadas ya la incluyen):"), _("Añadir a una lista"),
                    opciones, f)
                if not sel:
                    return
                lista = res[opciones.index(sel)]
                if lista["pertenece"] and lista["item_uri"]:
                    wsocial_service.quitar_miembro_lista_async(
                        acc, lista["item_uri"],
                        callback=lambda o, r: self._resultado(o, r, _("Quitada de la lista.")))
                else:
                    wsocial_service.anadir_miembro_lista_async(
                        acc, lista["uri"], did,
                        callback=lambda o, r: self._resultado(o, r, _("Añadida a la lista.")))
            wx.CallAfter(_elegir)
        wsocial_service.listas_con_pertenencia_async(acc, did, callback=_cb)

    def crear_lista(self):
        """Crea una lista de curación o de moderación."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        nombre = self._pedir_texto(_("Nombre de la lista:"), _("Nueva lista"))
        if not nombre:
            return
        descripcion = self._pedir_texto(_("Descripción (opcional):"), _("Nueva lista"))
        proposito = "curatelist"
        sel = wx.GetSingleChoice(
            _("¿Para qué es la lista?"), _("Nueva lista"),
            [_("Para leer sus publicaciones"), _("Para silenciar o bloquear en bloque")], f)
        if not sel:
            return
        if sel != _("Para leer sus publicaciones"):
            proposito = "modlist"
        wsocial_service.crear_lista_async(
            acc, nombre, descripcion, proposito,
            callback=lambda ok, res: self._resultado(ok, res, _("Lista creada.")))

    def gestionar_listas(self):
        """Abre el gestor de listas: crear, editar, borrar y administrar sus miembros."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialListsDialog
        dlg = WSocialListsDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    # ------------------------------------------------------------------
    # CONVERSACIONES Y GRUPOS
    # ------------------------------------------------------------------
    def accion_convo(self, convo_id, accion):
        """Acciones rápidas sobre una conversación."""
        acc = self._cuenta_activa_o_primera()
        textos = {"leer": _("Marcada como leída."), "silenciar": _("Conversación silenciada."),
                  "no_silenciar": _("Ya no está silenciada."), "aceptar": _("Solicitud aceptada."),
                  "cerrar": _("Grupo cerrado."), "reabrir": _("Grupo reabierto."),
                  "leer_todo": _("Todo marcado como leído.")}
        wsocial_service.accion_convo_async(
            acc, convo_id, accion,
            callback=lambda ok, res: self._resultado(ok, res, textos.get(accion, _("Hecho.")),
                                                     recargar=True))

    def salir_convo(self, convo_id):
        """Sale de una conversación o grupo, con confirmación."""
        f = self.f
        if confirmar(f, _("¿Seguro que quieres salir de esta conversación?"),
                     _("Salir de la conversación")) != wx.ID_YES:
            return
        self.accion_convo(convo_id, "salir")

    def crear_grupo(self):
        """Crea un grupo de chat."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        nombre = self._pedir_texto(_("Nombre del grupo:"), _("Nuevo grupo"))
        if not nombre:
            return
        cuentas = self._pedir_texto(
            _("Cuentas que quieres añadir, separadas por comas (por ejemplo nombre.wsocial.eu):"),
            _("Nuevo grupo"))
        actores = [c.strip() for c in cuentas.replace(";", ",").split(",") if c.strip()]
        if not actores:
            mostrar_info(f, _("Añade al menos una cuenta al grupo."), _("Nuevo grupo"))
            return
        wsocial_service.crear_grupo_async(
            acc, nombre, actores,
            callback=lambda ok, res: self._resultado(ok, res, _("Grupo creado."), recargar=True))

    def renombrar_grupo(self, convo_id):
        acc = self._cuenta_activa_o_primera()
        nombre = self._pedir_texto(_("Nuevo nombre del grupo:"), _("Cambiar el nombre"))
        if not nombre:
            return
        wsocial_service.editar_grupo_async(
            acc, convo_id, nombre,
            callback=lambda ok, res: self._resultado(ok, res, _("Nombre cambiado."), recargar=True))

    def gestionar_grupo(self, convo_id):
        """Participantes de un grupo: ver, añadir y expulsar."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        from .wsocial_dialogs import WSocialGroupMembersDialog
        dlg = WSocialGroupMembersDialog(f, acc, convo_id)
        dlg.ShowModal()
        dlg.Destroy()

    def enlace_grupo(self, convo_id):
        """Crea y muestra el enlace de invitación de un grupo."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        speak(_("Preparando el enlace..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo obtener el enlace: {0}").format(res))
                return
            enlace = res.get("enlace") or ""
            def _mostrar():
                if enlace:
                    try:
                        if wx.TheClipboard.Open():
                            wx.TheClipboard.SetData(wx.TextDataObject(enlace))
                            wx.TheClipboard.Close()
                    except Exception:
                        pass
                mostrar_info(f, _("Enlace de invitación al grupo (copiado al portapapeles):\n\n{0}")
                             .format(enlace or _("el servidor no ha devuelto ningún enlace")),
                             _("Invitar al grupo"))
            wx.CallAfter(_mostrar)
        wsocial_service.enlace_grupo_async(acc, convo_id, "crear", callback=_cb)

    def solicitudes_grupo(self, convo_id):
        """Quién quiere entrar en el grupo: aceptar o rechazar."""
        f = self.f
        acc = self._cuenta_activa_o_primera()

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron consultar las solicitudes: {0}").format(res))
                return
            if not res:
                wx.CallAfter(mostrar_info, f, _("No hay solicitudes pendientes."),
                             _("Solicitudes del grupo"))
                return
            def _elegir():
                opciones = [f"{s.get('nombre') or s.get('handle')} (@{s.get('handle')})" for s in res]
                sel = wx.GetSingleChoice(_("Elige la solicitud:"), _("Solicitudes del grupo"),
                                         opciones, f)
                if not sel:
                    return
                solicitud = res[opciones.index(sel)]
                aceptar = confirmar(
                    f, _("¿Aceptar a {0} en el grupo? Si eliges No, se rechazará.").format(
                        solicitud.get("handle")), _("Solicitud de entrada")) == wx.ID_YES
                wsocial_service.resolver_solicitud_grupo_async(
                    acc, convo_id, solicitud.get("did"), aceptar,
                    callback=lambda o, r: self._resultado(
                        o, r, _("Solicitud aceptada.") if aceptar else _("Solicitud rechazada.")))
            wx.CallAfter(_elegir)
        wsocial_service.solicitudes_grupo_async(acc, convo_id, callback=_cb)

    def entrar_en_grupo(self):
        """Pide entrar en un grupo con su enlace de invitación."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        enlace = self._pedir_texto(_("Pega el enlace de invitación al grupo:"), _("Entrar en un grupo"))
        if not enlace:
            return
        wsocial_service.pedir_entrada_grupo_async(
            acc, enlace,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Solicitud enviada. Te avisarán cuando la acepten.")
                if (isinstance(res, dict) and res.get("pendiente")) else _("Ya estás en el grupo."),
                recargar=True))

    # ------------------------------------------------------------------
    # MODERACIÓN
    # ------------------------------------------------------------------
    def denunciar(self, uri="", cid="", did=""):
        """Denuncia una publicación o una cuenta eligiendo el motivo."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        from app.services.wsocial.wsocial_moderacion import MOTIVOS_DENUNCIA
        etiquetas = list(MOTIVOS_DENUNCIA.values())
        sel = wx.GetSingleChoice(_("¿Por qué la denuncias?"), _("Denunciar"), etiquetas, f)
        if not sel:
            return
        motivo = [k for k, v in MOTIVOS_DENUNCIA.items() if v == sel][0]
        detalle = self._pedir_texto(_("Explica brevemente el problema (opcional):"), _("Denunciar"))
        wsocial_service.denunciar_async(
            acc, motivo, detalle, uri=uri, cid=cid, did=did,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Denuncia enviada al equipo de moderación.")))

    def palabras_silenciadas(self):
        """Editor de palabras silenciadas."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialMutedWordsDialog
        dlg = WSocialMutedWordsDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def preferencias_contenido(self):
        """Preferencias de contenido: adulto, etiquetas e idiomas."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialContentPrefsDialog
        dlg = WSocialContentPrefsDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def servicios_etiquetado(self):
        """Servicios de etiquetado: ver los suscritos y suscribirse a otros."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialLabelersDialog
        dlg = WSocialLabelersDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def verificacion_edad(self):
        """Estado de la verificación de edad y cómo completarla."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo consultar: {0}").format(res))
                return
            estado = (res or {}).get("estado") or _("sin iniciar")
            requerida = _("Sí") if (res or {}).get("requerida") else _("No")
            texto = _("Estado de la verificación de edad: {0}.\n"
                      "¿La exige tu servidor? {1}.\n\n"
                      "La verificación se completa en el navegador, en la web de W Social: esta "
                      "aplicación nunca maneja tus documentos.").format(estado, requerida)
            def _mostrar():
                if confirmar(f, texto + "\n\n" + _("¿Quieres empezar la verificación ahora?"),
                             _("Verificación de edad")) != wx.ID_YES:
                    return
                def _cb2(ok2, res2):
                    if ok2 and (res2 or {}).get("url"):
                        from app.core.privacy import abrir_url
                        abrir_url(res2["url"])
                        notificar(_("Se ha abierto el navegador para completar la verificación."))
                    else:
                        notificar(_("No se pudo iniciar la verificación: {0}").format(res2))
                wsocial_service.iniciar_verificacion_edad_async(acc, callback=_cb2)
            wx.CallAfter(_mostrar)
        wsocial_service.estado_verificacion_edad_async(acc, callback=_cb)

    # ------------------------------------------------------------------
    # CUENTA Y DATOS
    # ------------------------------------------------------------------
    def editar_perfil(self):
        """Editar el perfil (nombre, descripción, avatar y banner)."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialProfileEditDialog
        dlg = WSocialProfileEditDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def info_servidor(self):
        """Muestra con qué servidor está hablando esta cuenta y sus condiciones."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo consultar el servidor: {0}").format(res))
                return
            partes = [_("Servidor (PDS): {0}").format(res.get("pds", "")),
                      _("Identificador del servidor: {0}").format(res.get("did", "") or "—"),
                      _("Dominios disponibles para usuarios: {0}").format(
                          ", ".join(res.get("dominios", [])) or "—"),
                      _("¿Hace falta invitación para registrarse? {0}").format(
                          _("Sí") if res.get("requiere_invitacion") else _("No")),
                      _("Condiciones del servicio: {0}").format(res.get("condiciones") or "—"),
                      _("Política de privacidad: {0}").format(res.get("privacidad") or "—"),
                      _("Contacto: {0}").format(res.get("contacto") or "—")]
            wx.CallAfter(f._mostrar_texto_largo, _("Servidor de W Social"), "\n".join(partes))
        wsocial_service.describir_servidor_async(acc, callback=_cb)

    def estado_cuenta(self):
        """Estado de la cuenta en el servidor (activa, correo, repositorio)."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudo consultar: {0}").format(res))
                return
            partes = [_("Usuario: @{0}").format(res.get("handle", "")),
                      _("Identificador permanente (DID): {0}").format(res.get("did", "")),
                      _("Servidor: {0}").format(res.get("pds", "")),
                      _("Cuenta activada: {0}").format(_("Sí") if res.get("activada") else _("No")),
                      _("Correo: {0} ({1})").format(
                          res.get("correo") or "—",
                          _("confirmado") if res.get("correo_confirmado") else _("sin confirmar"))]
            wx.CallAfter(f._mostrar_texto_largo, _("Estado de la cuenta"), "\n".join(partes))
        wsocial_service.estado_cuenta_async(acc, callback=_cb)

    def contrasenas_aplicacion(self):
        """Gestor de contraseñas de aplicación."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialAppPasswordsDialog
        dlg = WSocialAppPasswordsDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    def exportar_datos(self):
        """Descarga una copia completa del repositorio de la cuenta."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        dlg = wx.FileDialog(f, _("Guardar la copia de tu cuenta"), wildcard="*.car",
                            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
                            defaultFile="wsocial-copia.car")
        destino = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not destino:
            return
        speak(_("Descargando tu copia. Puede tardar un poco."), interrupt=True)
        wsocial_service.exportar_repositorio_async(
            acc, destino,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Copia guardada ({0} bytes).").format(
                    (res or {}).get("bytes", 0) if isinstance(res, dict) else 0)))

    def salida_pendiente(self):
        """Publicaciones que quedaron en el aire: reintentarlas o descartarlas."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialOutboxDialog
        dlg = WSocialOutboxDialog(f, acc)
        dlg.ShowModal()
        dlg.Destroy()

    # ------------------------------------------------------------------
    # MENÚ &W SOCIAL
    # ------------------------------------------------------------------
    def construir_menu(self, menubar):
        """Crea el menú &W Social de la barra principal."""
        f = self.f
        menu = wx.Menu()

        def _item(etiqueta, handler, submenu=None):
            destino = submenu if submenu is not None else menu
            it = destino.Append(wx.ID_ANY, etiqueta)
            f.Bind(wx.EVT_MENU, lambda e: handler(), it)
            return it

        _item(_("&Nueva publicación..."), self.nuevo_post)
        _item(_("Nuevo &hilo..."), self.nuevo_hilo)
        _item(_("&Buscar publicaciones..."), self.buscar_publicaciones)
        _item(_("Buscar &cuentas..."), self.buscar_cuentas)
        _item(_("&Guardar una búsqueda..."), self.guardar_busqueda)
        menu.AppendSeparator()

        m_listas = wx.Menu()
        _item(_("&Gestionar mis listas..."), self.gestionar_listas, m_listas)
        _item(_("&Crear una lista..."), self.crear_lista, m_listas)
        _item(_("Descubrir &feeds..."), self.descubrir_feeds, m_listas)
        _item(_("&Paquetes de inicio sugeridos..."), self.paquetes_sugeridos, m_listas)
        menu.AppendSubMenu(m_listas, _("&Listas y feeds"))

        m_chat = wx.Menu()
        _item(_("&Crear un grupo..."), self.crear_grupo, m_chat)
        _item(_("&Entrar en un grupo con un enlace..."), self.entrar_en_grupo, m_chat)
        _item(_("&Quién puede escribirme..."), self.quien_puede_escribirme, m_chat)
        menu.AppendSubMenu(m_chat, _("&Mensajes y grupos"))

        m_mod = wx.Menu()
        _item(_("&Palabras silenciadas..."), self.palabras_silenciadas, m_mod)
        _item(_("Preferencias de &contenido..."), self.preferencias_contenido, m_mod)
        _item(_("Servicios de &etiquetado..."), self.servicios_etiquetado, m_mod)
        _item(_("&Verificación de edad..."), self.verificacion_edad, m_mod)
        menu.AppendSubMenu(m_mod, _("&Moderación y contenido"))

        m_cuenta = wx.Menu()
        _item(_("&Editar mi perfil..."), self.editar_perfil, m_cuenta)
        _item(_("Mi &estado temporal..."), self.fijar_estado, m_cuenta)
        _item(_("Estado de la &cuenta..."), self.estado_cuenta, m_cuenta)
        _item(_("Información del &servidor..."), self.info_servidor, m_cuenta)
        _item(_("Contraseñas de &aplicación..."), self.contrasenas_aplicacion, m_cuenta)
        _item(_("&Descargar una copia de mi cuenta..."), self.exportar_datos, m_cuenta)
        _item(_("Cambiar mi &nombre de usuario..."), self.cambiar_handle, m_cuenta)
        menu.AppendSubMenu(m_cuenta, _("Mi &cuenta"))

        menu.AppendSeparator()
        _item(_("Salida &pendiente..."), self.salida_pendiente)
        self._menu_tiempo_real = menu.AppendCheckItem(
            wx.ID_ANY, _("Avisos en &tiempo real"))
        self._menu_tiempo_real.Check(bool(config.get("wsocial", "tiempo_real", True)))
        f.Bind(wx.EVT_MENU, lambda e: self.alternar_tiempo_real(), self._menu_tiempo_real)

        menubar.Append(menu, _("&W Social"))
        return menu

    def buscar_publicaciones(self):
        """Búsqueda de publicaciones con filtros."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        from .wsocial_dialogs import WSocialSearchDialog
        dlg = WSocialSearchDialog(f, acc)
        if dlg.ShowModal() == wx.ID_OK and dlg.criterios:
            criterios = dlg.criterios
            speak(_("Buscando..."), interrupt=True)

            def _cb(ok, res):
                if not ok:
                    notificar(_("No se pudo buscar: {0}").format(res))
                    return
                def _pintar():
                    f.active_timeline_type = "search:" + criterios["texto"]
                    self.cargar_listado()
                    speak(_("{0} resultados.").format(len(res)), interrupt=True)
                wx.CallAfter(_pintar)
            wsocial_service.buscar_publicaciones_async(
                acc, criterios["texto"], orden=criterios["orden"], autor=criterios["autor"],
                desde=criterios["desde"], hasta=criterios["hasta"], idioma=criterios["idioma"],
                callback=_cb)
        dlg.Destroy()

    def descubrir_feeds(self):
        """Feeds populares para descubrir y guardar."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        if not acc:
            return
        speak(_("Buscando feeds..."), interrupt=True)

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar los feeds: {0}").format(res))
                return
            def _elegir():
                if not res:
                    mostrar_info(f, _("No se han encontrado feeds."), _("Feeds"))
                    return
                opciones = [f"{x['name']} (@{x['creator']}) — {x['likes']} me gusta" for x in res]
                sel = wx.GetSingleChoice(_("Elige un feed para guardarlo:"), _("Descubrir feeds"),
                                         opciones, f)
                if not sel:
                    return
                feed = res[opciones.index(sel)]
                wsocial_service.guardar_feed_async(
                    acc, feed["uri"], True, False,
                    callback=lambda o, r: self._resultado(
                        o, r, _("Feed guardado. Aparecerá en el árbol."), recargar=False))
                wx.CallAfter(f._cargar_navegacion)
            wx.CallAfter(_elegir)
        wsocial_service.feeds_populares_async(acc, callback=_cb)

    def paquetes_sugeridos(self):
        """Paquetes de inicio sugeridos."""
        f = self.f
        acc = self._cuenta_activa_o_primera()

        def _cb(ok, res):
            if not ok:
                notificar(_("No se pudieron cargar: {0}").format(res))
                return
            def _elegir():
                if not res:
                    mostrar_info(f, _("No hay paquetes sugeridos ahora mismo."),
                                 _("Paquetes de inicio"))
                    return
                opciones = [f"{x['name']} (@{x['creator']})" for x in res]
                sel = wx.GetSingleChoice(_("Elige un paquete para verlo:"),
                                         _("Paquetes de inicio"), opciones, f)
                if sel:
                    self.abrir_starter_pack(res[opciones.index(sel)]["uri"])
            wx.CallAfter(_elegir)
        wsocial_service.paquetes_sugeridos_async(acc, callback=_cb)

    def quien_puede_escribirme(self):
        """Quién puede mandarte mensajes directos."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        opciones = {_("Cualquiera"): "all", _("Solo las cuentas que sigo"): "following",
                    _("Nadie"): "none"}
        sel = wx.GetSingleChoice(_("¿Quién puede escribirte por privado?"),
                                 _("Mensajes directos"), list(opciones.keys()), f)
        if not sel:
            return
        wsocial_service.preferencias_chat_async(
            acc, opciones[sel],
            callback=lambda ok, res: self._resultado(ok, res, _("Preferencia guardada.")))

    def fijar_estado(self):
        """Publica un estado temporal en el perfil (o lo retira)."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        estado = self._pedir_texto(
            _("Estado temporal (déjalo vacío para retirarlo):"), _("Mi estado"))
        wsocial_service.fijar_estado_async(
            acc, estado,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Estado publicado.") if estado else _("Estado retirado.")))

    def cambiar_handle(self):
        """Cambia el nombre de usuario de la cuenta."""
        f = self.f
        acc = self._cuenta_activa_o_primera()
        nuevo = self._pedir_texto(
            _("Nuevo nombre de usuario (por ejemplo nombre.wsocial.eu):"),
            _("Cambiar el nombre de usuario"))
        if not nuevo:
            return
        if confirmar(f, _("Vas a cambiar tu nombre de usuario a {0}. Quien te siga lo seguirá "
                          "haciendo, pero los enlaces antiguos dejarán de funcionar. ¿Continuar?")
                     .format(nuevo), _("Cambiar el nombre de usuario")) != wx.ID_YES:
            return
        wsocial_service.cambiar_handle_async(
            acc, nuevo,
            callback=lambda ok, res: self._resultado(
                ok, res, _("Nombre cambiado a @{0}.").format(res if ok else ""), recargar=True))

    def alternar_tiempo_real(self):
        """Enciende o apaga los avisos en tiempo real de esta red (sin tocar las demás)."""
        f = self.f
        activo = bool(self._menu_tiempo_real.IsChecked()) if getattr(self, "_menu_tiempo_real", None) \
            else not bool(config.get("wsocial", "tiempo_real", True))
        config.set("wsocial", "tiempo_real", activo)
        if activo:
            self.iniciar_tiempo_real()
            notificar(_("Avisos en tiempo real activados para W Social."))
        else:
            wsocial_service.detener_tiempo_real()
            notificar(_("Avisos en tiempo real desactivados para W Social."))
