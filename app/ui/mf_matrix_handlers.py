# -*- coding: utf-8 -*-
"""Mixin del MainFrame para el cliente Matrix (tanda M1).
Metodos: _crear_menu_matrix, _matrix_cuenta_o_primera, _matrix_arranque_autoconectar.
"""
import wx
import logging

from app.core.database import db
from app.core.speech import speak, notificar

log = logging.getLogger(__name__)


class _Mf_matrix_handlers:
    def _crear_menu_matrix(self, menubar):
        """Menú Matrix de la barra. OJO: NUNCA asignar self.active_matrix_account_id desde aquí
        (esa variable es "el buffer que estás mirando" y solo la fija el árbol); las acciones
        reciben la cuenta como parámetro."""
        menu_mx = wx.Menu()
        it = menu_mx.Append(wx.ID_ANY, _("Conectar"),
                            _("Conectar la cuenta de Matrix activa (o la primera)"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.conectar_cuenta(self._matrix_cuenta_o_primera()), it)
        it = menu_mx.Append(wx.ID_ANY, _("Desconectar"),
                            _("Desconectar la cuenta de Matrix activa"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.desconectar_cuenta(self._matrix_cuenta_o_primera()), it)
        menu_mx.AppendSeparator()
        it = menu_mx.Append(wx.ID_ANY, _("Unirse a una sala..."),
                            _("Escribe un alias (#sala:servidor) y únete"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.unirse_preguntando(self._matrix_cuenta_o_primera()), it)
        it = menu_mx.Append(wx.ID_ANY, _("Crear sala..."),
                            _("Crear una sala privada nueva"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.crear_sala_preguntando(self._matrix_cuenta_o_primera()), it)
        it = menu_mx.Append(wx.ID_ANY, _("Mensaje directo a un usuario..."),
                            _("Abrir un directo con @usuario:servidor"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.dm_preguntando(self._matrix_cuenta_o_primera()), it)
        it = menu_mx.Append(wx.ID_ANY, _("Directorio de salas..."),
                            _("Salas públicas del servidor (o de otro federado); Intro te une"))
        self.Bind(wx.EVT_MENU, lambda e: self.matrix_ctrl.directorio_salas(self._matrix_cuenta_o_primera()), it)
        it = menu_mx.Append(wx.ID_ANY, _("Llamar a la puerta de una sala..."),
                            _("Pedir entrar en una sala que lo permita (knock)"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_knock(), it)
        it = menu_mx.Append(wx.ID_ANY, _("Crear espacio..."),
                            _("Crear un espacio (contenedor de salas)"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_crear_espacio(), it)
        menu_mx.AppendSeparator()
        it = menu_mx.Append(wx.ID_ANY, _("Mi perfil y estado..."),
                            _("Nombre visible, avatar y presencia (disponible/ausente)"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_dialogo("perfil"), it)
        it = menu_mx.Append(wx.ID_ANY, _("Mis dispositivos..."),
                            _("Sesiones de la cuenta: renombrar o cerrar con contraseña"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_dialogo("dispositivos"), it)
        it = menu_mx.Append(wx.ID_ANY, _("Palabras que me avisan..."),
                            _("Palabras clave que te avisan en cualquier sala (se guardan en el servidor)"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_palabras_aviso(), it)
        it = menu_mx.Append(wx.ID_ANY, _("Configurar o restablecer la identidad de cifrado (cross-signing)..."),
                            _("Crea (o RESTABLECE, si ya la tenías) tu identidad de cifrado en el servidor. "
                              "OJO: rehacerla obliga a volver a verificar tus sesiones. Para verificar esta "
                              "sesión con otro dispositivo tuyo, usa 'Mis dispositivos'."))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_cross_signing(), it)
        menu_mx.AppendSeparator()
        it = menu_mx.Append(wx.ID_ANY, _("Exportar claves de cifrado..."),
                            _("Guardar las claves E2EE en un archivo protegido con contraseña"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_claves(exportar=True), it)
        it = menu_mx.Append(wx.ID_ANY, _("Importar claves de cifrado..."),
                            _("Leer historial cifrado importando claves de Element u otro cliente"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_claves(exportar=False), it)
        it = menu_mx.Append(wx.ID_ANY, _("Volver a descifrar mensajes pendientes"),
                            _("Reintenta descifrar los mensajes que aparecen como cifrados, con las "
                              "claves que ya tenga la cuenta"))
        self.Bind(wx.EVT_MENU, lambda e: self._matrix_redescifrar(), it)
        menu_mx.AppendSeparator()
        it = menu_mx.Append(wx.ID_ANY, _("Añadir cuenta de Matrix..."),
                            _("Iniciar sesión en un homeserver de Matrix"))
        self.Bind(wx.EVT_MENU, self._on_matrix_add_cuenta, it)
        it = menu_mx.Append(wx.ID_ANY, _("Registrar cuenta nueva..."),
                            _("Crear una cuenta en un servidor con registro abierto"))
        self.Bind(wx.EVT_MENU, self._on_matrix_registrar, it)

        # Submenú de ADMINISTRACIÓN del servidor: solo si el usuario lo activó en Preferencias →
        # Matrix. Al cambiar esa casilla, _crear_menus se reconstruye, así que aparece/desaparece
        # en caliente (igual que el de Mastodon). Requiere ser admin de un servidor Synapse.
        from app.core.config_manager import config
        if config.get("matrix", "admin_tools", False):
            menu_mx.AppendSeparator()
            submenu_admin = wx.Menu()
            it = submenu_admin.Append(wx.ID_ANY, _("¿Soy administrador del servidor?"),
                                      _("Comprobar si tu cuenta administra el servidor"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_comprobar(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Usuarios del servidor..."),
                                      _("Buscar cuentas, ver detalles, desactivar, hacer admin, cambiar contraseña"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_usuarios(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Salas del servidor..."),
                                      _("Buscar cualquier sala del servidor, ver detalles y borrarla"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_salas(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Mensajes reportados..."),
                                      _("Contenido que los usuarios han reportado, con su motivo"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_informes(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Enviar aviso del servidor a un usuario..."),
                                      _("Enviarle un mensaje directo desde la cuenta de avisos del servidor"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_aviso_servidor(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Tokens de registro..."),
                                      _("Crear o borrar tokens para registrarse en un servidor restringido"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_tokens(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Federación (servidores remotos)..."),
                                      _("Ver con qué servidores federas y reintentar los que fallan"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_federacion(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Mantenimiento de archivos (media)..."),
                                      _("Borrar archivos antiguos no usados para liberar disco"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_media_mant(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Actualizaciones en segundo plano..."),
                                      _("Ver si la base de datos del servidor está al día tras actualizar Synapse"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_bg_updates(), it)
            it = submenu_admin.Append(wx.ID_ANY, _("Estadísticas del servidor..."),
                                      _("Versión de Synapse, número de usuarios y de salas"))
            self.Bind(wx.EVT_MENU, lambda e: self._matrix_admin_estadisticas(), it)
            menu_mx.AppendSubMenu(submenu_admin, _("Administración"))

        menubar.Append(menu_mx, _("Ma&trix"))

    def _matrix_dialogo(self, cual: str):
        """Abre un diálogo de cuenta (perfil/dispositivos) con la cuenta activa o la primera."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .matrix_dialogs import MatrixPerfilDialog, MatrixDispositivosDialog
        dlg = (MatrixPerfilDialog(self, acc) if cual == "perfil"
               else MatrixDispositivosDialog(self, acc))
        dlg.ShowModal()
        dlg.Destroy()

    def _matrix_palabras_aviso(self):
        """Gestiona las palabras clave que te avisan en cualquier sala (push rules del servidor)."""
        from app.core.speech import notificar
        from app.core.task_manager import task_manager
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.palabras_aviso(acc)

        def _fin(palabras, err):
            if err:
                notificar(_("No se pudieron leer: {0}").format(err))
                return
            palabras = palabras or []
            opciones = palabras + [_("[Añadir una palabra nueva...]")]
            dlg = wx.SingleChoiceDialog(
                self, _("Palabras que te avisan (elige una para quitarla, o añade una nueva):"),
                _("Palabras que me avisan"), opciones)
            if dlg.ShowModal() != wx.ID_OK:
                dlg.Destroy()
                return
            idx = dlg.GetSelection()
            dlg.Destroy()
            if idx == len(palabras):
                from .accessible_controls import CustomTextEntryDialog
                d2 = CustomTextEntryDialog(self, _("Palabra o frase que quieres que te avise:"),
                                           _("Añadir palabra de aviso"))
                nueva = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
                d2.Destroy()
                if nueva:
                    self._matrix_palabra_accion(acc, nueva, True)
            else:
                self._matrix_palabra_accion(acc, palabras[idx], False)
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_cross_signing(self):
        """Configura la verificación cruzada (cross-signing) de la cuenta: crea la identidad de
        cifrado en el servidor. Requiere tu contraseña (autenticación interactiva)."""
        from app.core.speech import notificar
        from app.core.task_manager import task_manager
        from .accessible_controls import CustomTextEntryDialog, confirmar, mostrar_info
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return

        def _estado_bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.estado_cross_signing(acc)

        def _estado_fin(est, err):
            if err:
                notificar(_("No se pudo consultar el estado: {0}").format(err))
                return
            est = est or {}
            if est.get("configurado_en_servidor") and est.get("claves_locales"):
                mostrar_info(self, _("Tu verificación cruzada ya está configurada y este equipo "
                                     "tiene las claves. Para que alguien te reconozca como de "
                                     "confianza, verifícate con esa persona por emojis."),
                             _("Verificación cruzada"))
                return
            aviso = _("AVISO: la verificación cruzada de esta aplicación es EXPERIMENTAL. La "
                      "biblioteca base (matrix-nio) no soporta cross-signing de forma oficial, así "
                      "que NO sustituye a la copia de seguridad de claves (Secure Backup) ni "
                      "garantiza recuperar tu identidad si reinstalas, y puede que Element no la "
                      "reconozca por completo. Guarda tus claves con «Exportar claves» como respaldo "
                      "fiable.\n\n"
                      "La verificación cruzada (cross-signing) crea tu identidad de cifrado en el "
                      "servidor: firma tus dispositivos para que, tras verificarte UNA vez con "
                      "alguien, esa persona confíe en todos tus dispositivos.\n\n")
            if est.get("configurado_en_servidor") and not est.get("claves_locales"):
                aviso += _("Ya la tenías configurada en el servidor pero este equipo no tiene las "
                           "claves privadas; al continuar se REEMPLAZA por una nueva (tendrás que "
                           "re-verificarte con quienes ya lo hubieran hecho).\n\n")
            aviso += _("Hace falta tu contraseña de Matrix (el servidor la pide). ¿Continuar?")
            if confirmar(self, aviso, _("Configurar verificación cruzada")) != wx.ID_YES:
                return
            d = CustomTextEntryDialog(self, _("Contraseña de tu cuenta de Matrix:"),
                                      _("Verificación cruzada"), password=True)
            password = d.GetValue() if d.ShowModal() == wx.ID_OK else ""
            d.Destroy()
            if not password:
                return
            speak(_("Configurando la verificación cruzada..."), interrupt=True)

            def _bg(ctx):
                from app.services.matrix import matrix_service
                return matrix_service.configurar_cross_signing(acc, password)

            def _fin_config(res, err):
                if err:
                    notificar(_("No se pudo configurar: {0}").format(err))
                    return
                res = res or {}
                if res.get("estado") == "aprobacion_navegador":
                    self._matrix_xsign_aprobar(acc, res.get("url") or "")
                else:
                    notificar(_("¡Verificación cruzada configurada! Este dispositivo ya está "
                                "firmado por tu identidad. Ahora verifícate por emojis con quien "
                                "quieras que confíe en ti."))
            task_manager.start_task(_bg, callback=_fin_config, pass_context=True)
        task_manager.start_task(_estado_bg, callback=_estado_fin, pass_context=True)

    def _matrix_xsign_aprobar(self, acc, url):
        """El servidor (autenticación moderna OAuth/MAS) exige aprobar en el navegador: se abre la
        página, el usuario la aprueba y, al confirmar, la aplicación sondea hasta completarlo."""
        from app.core.speech import notificar
        from app.core.task_manager import task_manager
        from app.core.privacy import abrir_url
        from .accessible_controls import confirmar
        if not url:
            notificar(_("El servidor no indicó la página de aprobación."))
            return
        abrir_url(url)
        r = confirmar(self, _("Tu servidor pide APROBAR la verificación cruzada en el navegador "
                              "(se acaba de abrir la página; si no, la dirección es:\n{0}\n).\n\n"
                              "Inicia sesión si hace falta, pulsa el botón para APROBAR/permitir, "
                              "y cuando termines vuelve aquí y pulsa Sí para finalizar.\n\n"
                              "¿Ya lo has aprobado?").format(url),
                      _("Aprobar en el navegador"))
        if r != wx.ID_YES:
            notificar(_("Verificación cruzada sin terminar. Puedes reintentarlo desde el menú."))
            return
        speak(_("Finalizando la verificación cruzada..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            matrix_service.continuar_cross_signing_tras_aprobacion(acc)
            return True
        task_manager.start_task(_bg, callback=lambda r2, e: notificar(
            _("¡Verificación cruzada configurada! Este dispositivo ya está firmado por tu "
              "identidad. Ahora verifícate por emojis con quien quieras que confíe en ti.")
            if not e else _("No se pudo finalizar: {0}").format(e)), pass_context=True)

    def _matrix_palabra_accion(self, acc, palabra, agregar):
        from app.core.speech import notificar
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if agregar:
                matrix_service.agregar_palabra_aviso(acc, palabra)
            else:
                matrix_service.quitar_palabra_aviso(acc, palabra)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            (_("Palabra añadida: te avisará en todas tus sesiones.") if agregar
             else _("Palabra quitada.")) if not e else _("No se pudo: {0}").format(e)),
            pass_context=True)

    def _matrix_knock(self):
        """Llamar a la puerta de una sala (knock)."""
        from app.core.speech import notificar
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Sala a la que llamar (ej: #sala:servidor):"),
                                    _("Llamar a la puerta"))
        sala = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not sala:
            return
        d2 = CustomTextEntryDialog(self, _("Motivo para los moderadores (opcional):"),
                                   _("Llamar a la puerta"))
        motivo = d2.GetValue().strip() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.llamar_puerta(acc, sala, motivo)
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Solicitud enviada: cuando te acepten, la sala aparecerá en la lista.") if not e
            else _("No se pudo: {0}").format(e)), pass_context=True)

    def _matrix_crear_espacio(self):
        """Crear un espacio (contenedor de salas)."""
        from app.core.speech import notificar
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        dlg = CustomTextEntryDialog(self, _("Nombre del espacio:"), _("Crear espacio"))
        nombre = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not nombre:
            return
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.crear_espacio(acc, nombre)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo crear el espacio: {0}").format(err))
            else:
                notificar(_("Espacio creado. Añade salas desde su menú contextual."))
                try:
                    self.matrix_ctrl._refrescar_si_activa(acc)
                except Exception:
                    pass
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_matrix_registrar(self, event):
        from .matrix_dialogs import MatrixRegistroDialog
        dlg = MatrixRegistroDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _matrix_claves(self, exportar: bool):
        """Exportar o importar las claves E2EE (compatible con Element)."""
        from app.core.speech import notificar
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        estilo = (wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) if exportar else (wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        dlg = wx.FileDialog(self, _("Archivo de claves de cifrado"),
                            defaultFile="claves_matrix.txt" if exportar else "",
                            wildcard=_("Claves|*.txt;*.keys|Todos|*.*"), style=estilo)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        ruta = dlg.GetPath()
        dlg.Destroy()
        from .accessible_controls import CustomTextEntryDialog
        d2 = CustomTextEntryDialog(self, _("Contraseña del archivo de claves:"),
                                   _("Exportar claves") if exportar else _("Importar claves"))
        clave = d2.GetValue() if d2.ShowModal() == wx.ID_OK else ""
        d2.Destroy()
        if not clave:
            return
        from app.core.task_manager import task_manager

        def _bg(ctx):
            from app.services.matrix import matrix_service
            if exportar:
                matrix_service.exportar_claves(acc, ruta, clave)
                return None
            return matrix_service.importar_claves(acc, ruta, clave)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            elif exportar:
                notificar(_("Claves exportadas."))
            else:
                # res = nº de mensajes que se pudieron re-descifrar con las claves importadas.
                if res:
                    notificar(_("Claves importadas. Se han descifrado {0} mensaje(s) que estaban "
                                "cifrados.").format(res))
                else:
                    notificar(_("Claves importadas. Ya puedes leer el historial cifrado de esas salas."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_redescifrar(self):
        """Reintenta descifrar los mensajes que quedaron como "[Mensaje cifrado...]" con las
        claves que ya tenga la cuenta (útil tras verificar la sesión o pedir claves)."""
        acc = self._matrix_cuenta_o_primera()
        if not acc:
            speak(_("Primero añade una cuenta de Matrix."), interrupt=True)
            return
        from app.core.task_manager import task_manager
        speak(_("Reintentando descifrar mensajes pendientes..."), interrupt=True)

        def _bg(ctx):
            from app.services.matrix import matrix_service
            return matrix_service.redescifrar_pendientes(acc)

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            recuperados, solicitadas = res
            if recuperados:
                notificar(_("Se han descifrado {0} mensaje(s).").format(recuperados))
            elif solicitadas:
                notificar(_("No estaban las claves de {0} conversación(es) cifrada(s). He pedido esas "
                            "claves a tus otros dispositivos: si alguno está en línea (por ejemplo tu "
                            "Element), los mensajes se descifrarán solos en unos segundos. Si el "
                            "mensaje lo envió otra persona antes de que existiera esta sesión, puede "
                            "que su clave no esté disponible en ningún sitio.").format(solicitadas))
            else:
                notificar(_("No había mensajes cifrados pendientes que reintentar."))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _matrix_cuenta_o_primera(self):
        if getattr(self, "active_matrix_account_id", None):
            return self.active_matrix_account_id
        fila = db.query_one(
            "SELECT id FROM accounts WHERE service_id = 'matrix' AND active = 1 ORDER BY position, id")
        return fila[0] if fila else None

    def _on_matrix_add_cuenta(self, event):
        from .matrix_dialogs import MatrixLoginDialog
        dlg = MatrixLoginDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            self._cargar_navegacion()
        dlg.Destroy()

    def _matrix_arranque_autoconectar(self):
        """Al arrancar: conectar las cuentas de Matrix en segundo plano (el sync es barato:
        long-polling con next_batch persistido)."""
        try:
            from app.core.feature_flags import servicio_habilitado
            if not servicio_habilitado("matrix") or getattr(self, "matrix_ctrl", None) is None:
                return
            self.matrix_ctrl.conectar_cuentas_auto()
        except Exception as e:
            log.debug(f"Matrix: autoconexión al arrancar no disponible: {e}")
