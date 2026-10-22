# -*- coding: utf-8 -*-
"""Ventana de Conversación de XMPP (hermana de las de Telegram/IRC/Matrix, tanda X2).

Mensajes 1:1 en VIVO con inserción silenciosa (sin reconstruir la lista ni mover el foco, con
marca "(nuevo)"), campo de entrada con Intro para enviar, historial de envíos con las flechas,
"está escribiendo…" en ambos sentidos (XEP-0085), recibo de entrega (XEP-0184) y sincronización
con otros dispositivos por Carbons (XEP-0280). Fragmentos y traductor como en las demás.
"""
import re
import time
import wx
import logging

from app.core.database import db
from app.core.event_bus import event_bus
from app.core.speech import speak, notificar
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleListCtrl, AccessibleTextCtrl, mostrar_info, confirmar

log = logging.getLogger(__name__)


class XMPPConversationDialog(wx.Dialog):
    """Conversación 1:1 con un contacto de XMPP."""

    def __init__(self, parent, account_id: int, jid: str, nombre: str = "", es_muc: bool = False):
        self.es_muc = es_muc
        self._titulo_base = (_("Sala: {0}").format(nombre or jid) if es_muc
                             else _("Conversación: {0}").format(nombre or jid))
        super().__init__(parent, title=self._titulo_base, size=(700, 540),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=_("Ventana de conversación de XMPP"))
        self.account_id = account_id
        self.jid = jid
        self.nombre = nombre or jid
        self.msg_rows = []
        self._ultimo_id = 0
        self._recarga_pendiente = False   # coalescido de recargas por ráfagas (MAM/recibos)
        self._hist_envios = []
        self._hist_pos = None
        self._typing_ultimo = 0.0
        self._titulo_typing = None
        self._init_ui()
        self.Bind(wx.EVT_CLOSE, self._on_cerrar)
        self.CenterOnParent()
        self._cargar_mensajes()
        event_bus.subscribe("xmpp_evento", self._on_evento)
        # Atajos: Ctrl+Shift+M cargar anteriores (MAM); Ctrl+T traducir el mensaje enfocado;
        # Ctrl+I describir la imagen del mensaje enfocado (accesibilidad).
        ident_ant, ident_trad, ident_img = wx.NewIdRef(), wx.NewIdRef(), wx.NewIdRef()
        self.Bind(wx.EVT_MENU, lambda e: self._cargar_anteriores(), id=ident_ant)
        self.Bind(wx.EVT_MENU, lambda e: self._traducir_mensaje(), id=ident_trad)
        self.Bind(wx.EVT_MENU, lambda e: self._describir_imagen(), id=ident_img)
        self.SetAcceleratorTable(wx.AcceleratorTable([
            (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord('M'), ident_ant),
            (wx.ACCEL_CTRL, ord('T'), ident_trad),
            (wx.ACCEL_CTRL, ord('I'), ident_img)]))
        # Catch-up del historial (MAM) al abrir: en segundo plano, sin bloquear.
        self._catch_up_inicial()
        # Marcar leído al abrir (limpia el "sin leer" de fuera) sin perder la posición calculada.
        wx.CallAfter(self._marcar_leido_abrir)
        self.txt_mensaje.SetFocus()

    # ---------------- UI ----------------
    def _init_ui(self):
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label=_("Mensajes (los más recientes al final):")), 0, wx.ALL, 5)
        self.lista_msgs = AccessibleListCtrl(
            self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN,
            name=_("Lista de mensajes de la conversación"))
        self.lista_msgs.InsertColumn(0, _("Hora"), width=70)
        self.lista_msgs.InsertColumn(1, _("Autor"), width=150)
        self.lista_msgs.InsertColumn(2, _("Mensaje"), width=440)
        self.lista_msgs.Bind(wx.EVT_CONTEXT_MENU, self._on_context_menu)
        self.lista_msgs.Bind(wx.EVT_KEY_DOWN, self._on_msg_key)
        sizer.Add(self.lista_msgs, 1, wx.EXPAND | wx.ALL, 5)

        sizer.Add(wx.StaticText(self, label=_("Escribir mensaje (Intro envía):")), 0, wx.ALL, 5)
        self.txt_mensaje = AccessibleTextCtrl(
            self, style=wx.TE_PROCESS_ENTER, name=_("Campo de redacción de mensaje"),
            ayuda_texto=_("Escribe y pulsa Intro para enviar. Flecha arriba recupera envíos anteriores."))
        self.txt_mensaje.Bind(wx.EVT_TEXT_ENTER, self._on_enviar)
        self.txt_mensaje.Bind(wx.EVT_KEY_DOWN, self._on_key_entrada)
        self.txt_mensaje.Bind(wx.EVT_TEXT, self._on_escribiendo)
        try:
            from .traductor_redaccion import instalar_traductor_redaccion
            instalar_traductor_redaccion(self.txt_mensaje)
            from .snippets import instalar_snippets
            instalar_snippets(self.txt_mensaje)
        except Exception:
            pass
        sizer.Add(self.txt_mensaje, 0, wx.EXPAND | wx.ALL, 5)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_enviar = wx.Button(self, label=_("&Enviar"), name=_("Enviar mensaje"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        fila.Add(self.btn_enviar, 0, wx.ALL, 5)
        btn_adj = wx.Button(self, label=_("Ad&juntar archivo..."), name=_("Adjuntar y enviar un archivo"))
        btn_adj.Bind(wx.EVT_BUTTON, self._on_adjuntar)
        fila.Add(btn_adj, 0, wx.ALL, 5)
        self._grabando = False
        self.btn_grabar = wx.Button(self, label=_("&Grabar nota de voz"), name=_("Grabar y enviar una nota de voz"))
        self.btn_grabar.Bind(wx.EVT_BUTTON, self._on_grabar_voz)
        fila.Add(self.btn_grabar, 0, wx.ALL, 5)
        # Descartar la nota de voz que estás grabando SIN enviarla (solo activo mientras grabas).
        self.btn_cancelar_voz = wx.Button(self, label=_("Cancelar &grabación"), name=_("Cancelar la nota de voz que estás grabando"))
        self.btn_cancelar_voz.Bind(wx.EVT_BUTTON, self._on_cancelar_voz)
        self.btn_cancelar_voz.Disable()
        fila.Add(self.btn_cancelar_voz, 0, wx.ALL, 5)
        btn_ant = wx.Button(self, label=_("Cargar an&teriores"), name=_("Cargar mensajes más antiguos"))
        btn_ant.Bind(wx.EVT_BUTTON, lambda e: self._cargar_anteriores())
        fila.Add(btn_ant, 0, wx.ALL, 5)
        if self.es_muc:
            btn_ocu = wx.Button(self, label=_("&Ocupantes..."), name=_("Ver los ocupantes de la sala"))
            btn_ocu.Bind(wx.EVT_BUTTON, lambda e: self._ver_ocupantes())
            fila.Add(btn_ocu, 0, wx.ALL, 5)
            btn_tema = wx.Button(self, label=_("Te&ma..."), name=_("Ver o cambiar el tema de la sala"))
            btn_tema.Bind(wx.EVT_BUTTON, lambda e: self._cambiar_tema())
            fila.Add(btn_tema, 0, wx.ALL, 5)
        btn_final = wx.Button(self, label=_("Ir al &final"), name=_("Ir al último mensaje"))
        btn_final.Bind(wx.EVT_BUTTON, lambda e: self._ir_al_final())
        fila.Add(btn_final, 0, wx.ALL, 5)
        # Cifrado OMEMO: botón que activa/desactiva y botón de huellas (solo si la pila está instalada).
        # En un chat de PASARELA (WhatsApp, Discord…) no se muestra: la pasarela cifra en su lado, OMEMO
        # no aplica ahí y marcarlo solo dejaría el envío colgado.
        from app.services.xmpp.xmpp_service import xmpp_service as _xs
        if _xs.omemo_disponible() and not self._es_chat_de_puente(_xs):
            self.btn_cifrado = wx.Button(self, name=_("Activar o desactivar el cifrado OMEMO"))
            self.btn_cifrado.Bind(wx.EVT_BUTTON, lambda e: self._toggle_cifrado())
            fila.Add(self.btn_cifrado, 0, wx.ALL, 5)
            btn_huellas = wx.Button(self, label=_("&Huellas OMEMO..."),
                                    name=_("Ver y verificar las huellas de cifrado"))
            btn_huellas.Bind(wx.EVT_BUTTON, lambda e: self._ver_huellas())
            fila.Add(btn_huellas, 0, wx.ALL, 5)
            self._refrescar_boton_cifrado()
        else:
            self.btn_cifrado = None
        fila.AddStretchSpacer()
        fila.Add(wx.Button(self, wx.ID_CANCEL, _("&Cerrar")), 0, wx.ALL, 5)
        sizer.Add(fila, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(sizer)

    # ---------------- Carga y pintado ----------------
    def _componer_fila(self, fila_bd, leido=None):
        """Calcula lo que se muestra/lee de una fila (sin tocar la lista). Devuelve
        (hora, autor, mostrado, lectura, rowdict). Compartido por volcar (insertar) y repintar."""
        (mid, quien, texto, fecha, propio, entregado, editado, stanza_id,
         media_url, reacciones, reply_to, tipo, cifrado, leido_col, destacado,
         es_voz, duracion, reproducido) = fila_bd
        # Un mensaje se considera LEÍDO si su columna leido está a 1 o es propio; salvo que el
        # llamante fuerce el valor (p. ej. inserción en vivo que quiere marcar "(nuevo)").
        if leido is None:
            leido = bool(leido_col) or bool(propio)
        from app.services.xmpp.xmpp_common import hora_local
        from app.services.xmpp.xmpp_service import xmpp_service
        hora = hora_local(fecha)
        autor = _("Yo") if propio else quien
        cuerpo = texto or ""
        # Campos ricos del adjunto (para la plantilla y para una lectura tipo WhatsApp).
        media_lbl = dur_txt = estado_txt = ""
        if tipo == "media":
            ref = media_url or cuerpo
            voz = bool(es_voz) or xmpp_service.es_nota_voz(ref)
            if voz:
                media_lbl = _("Mensaje de voz")
            elif xmpp_service.es_audio(ref):
                media_lbl = _("Mensaje de audio")
            elif xmpp_service.es_imagen(ref):
                media_lbl = _("Imagen")
            elif xmpp_service.es_video(ref):
                media_lbl = _("Vídeo")
            else:
                media_lbl = _("Archivo")
            dur_txt = xmpp_service.formato_duracion(duracion)
            cuerpo = media_lbl  # etiqueta limpia (sin nombre de archivo feo), igual en ambos sentidos
            if voz:  # estado de nota de voz: pendiente / reproducido (como WhatsApp)
                estado_txt = _("reproducido") if (reproducido or propio) else _("pendiente")
        extras = []
        if tipo == "media":
            if dur_txt:
                extras.append(dur_txt)
            if estado_txt:
                extras.append(_("({0})").format(estado_txt))
        if destacado:
            extras.append(_("(destacado)"))
        if cifrado:
            extras.append(_("(cifrado)"))
        cita_txt = ""
        if reply_to:
            # Vista previa de la cita: a qué mensaje responde.
            cita_txt = self._texto_de_reply(reply_to)
            extras.append(_("(en respuesta a: {0})").format(cita_txt) if cita_txt else _("(respuesta)"))
        if editado:
            extras.append(_("(editado)"))
        if reacciones:
            extras.append(_("[Reacciones: {0}]").format(reacciones))
        if tipo == "media":
            if xmpp_service.es_audio(media_url or texto or ""):
                extras.append(_("(Espacio para reproducir)"))
            elif xmpp_service.es_imagen(media_url or texto or ""):
                extras.append(_("(Espacio para ver la imagen)"))
            else:
                extras.append(_("(Espacio para descargar)"))
        if propio and entregado:
            extras.append(_("(entregado)"))
        visible = " ".join([cuerpo] + extras).strip()
        mostrado = visible if leido else _("(nuevo) ") + visible
        from .xmpp_views import render_plantilla_xmpp
        lectura = render_plantilla_xmpp("xmpp_message", {
            "author": autor, "content": mostrado, "hora": hora, "contacto": self.nombre,
            "media": media_lbl, "duracion": dur_txt, "estado": estado_txt,
            "reacciones": reacciones or "", "cita": cita_txt})
        rowdict = {"id": mid, "quien": quien, "texto": cuerpo, "propio": bool(propio),
                   "stanza_id": stanza_id or "", "media_url": media_url or "",
                   "tipo": tipo or "msg", "leido": bool(leido), "destacado": bool(destacado),
                   "es_voz": bool(es_voz) or (tipo == "media" and xmpp_service.es_nota_voz(media_url or texto or "")),
                   "duracion": float(duracion or 0), "reproducido": bool(reproducido)}
        return hora, autor, mostrado, lectura, rowdict

    def _volcar_fila(self, fila_bd, leido=None):
        hora, autor, mostrado, lectura, rowdict = self._componer_fila(fila_bd, leido)
        idx = self.lista_msgs.GetItemCount()
        self.lista_msgs.InsertItem(idx, hora)
        self.lista_msgs.SetItem(idx, 1, autor)
        self.lista_msgs.SetItem(idx, 2, mostrado.replace("\n", " "))
        self.lista_msgs.establecer_lectura(idx, lectura)
        self.msg_rows.append(rowdict)
        self._ultimo_id = max(self._ultimo_id, rowdict["id"] or 0)

    def _repintar_fila(self, idx):
        """Vuelve a pintar SOLO la fila 'idx' (p. ej. tras marcar una nota de voz como reproducida),
        sin recargar toda la conversación ni mover el foco."""
        if not (0 <= idx < len(self.msg_rows)):
            return
        mid = self.msg_rows[idx].get("id")
        fila = db.query_one(self._SQL + " AND id = ?", (self.account_id, self.jid, mid))
        if not fila:
            return
        _hora, autor, mostrado, lectura, rowdict = self._componer_fila(fila)
        try:
            self.lista_msgs.SetItem(idx, 2, mostrado.replace("\n", " "))
            self.lista_msgs.establecer_lectura(idx, lectura)
        except Exception:
            return
        self.msg_rows[idx] = rowdict

    _SQL = ("SELECT id, COALESCE(NULLIF(sender_nombre,''), sender), texto, date, es_propio, "
            "COALESCE(entregado,0), COALESCE(editado,0), stanza_id, COALESCE(media_url,''), "
            "COALESCE(reacciones,''), COALESCE(reply_to,''), tipo, COALESCE(cifrado,0), "
            "COALESCE(leido,0), COALESCE(destacado,0), COALESCE(es_voz,0), "
            "COALESCE(duracion,0), COALESCE(reproducido,0) "
            "FROM xmpp_mensajes WHERE account_id = ? AND chat_jid = ?")

    def _texto_de_reply(self, reply_id):
        """Devuelve un fragmento del mensaje al que se responde (para la vista previa de la cita)."""
        if not reply_id:
            return ""
        fila = db.query_one(
            "SELECT COALESCE(NULLIF(sender_nombre,''), sender), texto FROM xmpp_mensajes "
            "WHERE account_id = ? AND (stanza_id = ? OR origin_id = ?) LIMIT 1",
            (self.account_id, reply_id, reply_id))
        if not fila:
            return ""
        quien, txt = fila
        txt = (txt or "").replace("\n", " ").strip()
        if len(txt) > 60:
            txt = txt[:60] + "…"
        return f"{quien}: {txt}" if txt else (quien or "")

    def _cargar_mensajes(self, foco_al_final=True):
        filas = db.query(self._SQL + " ORDER BY id DESC LIMIT 300", (self.account_id, self.jid))
        self.lista_msgs.Freeze()
        try:
            self.lista_msgs.DeleteAllItems()
            self.lista_msgs.limpiar_lecturas()
            self.msg_rows.clear()
            self._ultimo_id = 0
            for fila in reversed(filas):
                self._volcar_fila(fila)  # el leído sale de la propia fila (marca "(nuevo)")
        finally:
            self.lista_msgs.Thaw()
        if foco_al_final and self.msg_rows:
            self._posicionar_apertura()

    def _posicionar_apertura(self):
        """Al abrir, deja el foco en el ÚLTIMO mensaje LEÍDO, para ir bajando por los no leídos (como
        WhatsApp). Si no hay no leídos, va al final."""
        primer_no_leido = next((i for i, m in enumerate(self.msg_rows) if not m.get("leido")), None)
        if primer_no_leido is None:
            self._ir_al_final()
            return
        idx = max(0, primer_no_leido - 1)
        self.lista_msgs.Focus(idx)
        self.lista_msgs.Select(idx)
        self.lista_msgs.EnsureVisible(min(self.lista_msgs.GetItemCount() - 1, primer_no_leido))
        n = len(self.msg_rows) - primer_no_leido
        speak(_("{0} sin leer. Baja para verlos.").format(n) if n != 1
              else _("1 sin leer. Baja para verlo."), interrupt=False)

    def _marcar_leido_abrir(self):
        """Marca el chat como leído al abrir (limpia el 'sin leer') y refresca la lista de fuera.
        RENDIMIENTO: el marcado (y sobre todo el marcador XEP-0333, que espera al bucle de slixmpp)
        va en SEGUNDO PLANO. Así abrir el diálogo NUNCA bloquea la interfaz esperando a la red."""
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            if es_muc:
                from app.services.xmpp.xmpp_common import db_id_chat
                db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE id = ?",
                           (db_id_chat(acc, jid),))
                db.execute("UPDATE xmpp_mensajes SET leido = 1 WHERE account_id = ? AND chat_jid = ?",
                           (acc, jid))
            else:
                xmpp_service.marcar_leido(acc, jid)
            return True

        def _fin(res, err):
            # Refrescar la lista/árbol de fuera para que desaparezca el contador "sin leer".
            event_bus.emit("xmpp_evento", {"account_id": acc, "tipo": "leido"})
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"xmpp:marcarleido:{acc}:{jid}")

    def _insertar_nuevos(self):
        """Añade solo los mensajes con id > último, SIN mover el foco (inserción silenciosa)."""
        filas = db.query(self._SQL + " AND id > ? ORDER BY id ASC",
                         (self.account_id, self.jid, self._ultimo_id))
        if not filas:
            return
        foco = self.lista_msgs.GetFocusedItem()
        estaba_al_final = (foco == self.lista_msgs.GetItemCount() - 1) or foco < 0
        nuevas = []
        for fila in filas:
            self._volcar_fila(fila, leido=bool(fila[4]))  # entrante en vivo → "(nuevo)"; propio → leído
            nuevas.append(self.msg_rows[-1])
        # Solo seguimos al final si el usuario ya estaba en el último (regla de la casa).
        if estaba_al_final and self.msg_rows:
            self._ir_al_final()
        # Auto-abrir el QR: si es el chat de una PASARELA (JID de componente, sin @) y llega una
        # imagen entrante, la mostramos sola en una ventana para escanearla sin más pasos.
        try:
            if "@" not in (self.jid or ""):
                from app.services.xmpp.xmpp_service import xmpp_service
                for m in reversed(nuevas):
                    if (not m.get("propio") and m.get("tipo") == "media"
                            and xmpp_service.es_imagen(m.get("media_url") or m.get("texto") or "")):
                        wx.CallAfter(self._ver_imagen, m, True)
                        break
        except Exception:
            pass

    def _ir_al_final(self):
        n = self.lista_msgs.GetItemCount()
        if n:
            self.lista_msgs.Focus(n - 1)
            self.lista_msgs.Select(n - 1)
            self.lista_msgs.EnsureVisible(n - 1)

    # ---------------- Historial (MAM) ----------------
    def _catch_up_inicial(self):
        acc, jid = self.account_id, self.jid

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.catch_up(acc, jid)

        def _fin(n, err):
            if not err and n:
                self._recargar_conservando_foco()
        task_manager.start_task(_bg, callback=_fin, pass_context=True,
                                unique_key=f"xmpp:catchup:{acc}:{jid}")

    def _cargar_anteriores(self):
        acc, jid = self.account_id, self.jid
        speak(_("Cargando mensajes anteriores..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.cargar_anteriores(acc, jid)

        def _fin(n, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
                return
            if not n:
                notificar(_("No hay más mensajes anteriores (o el servidor no guarda historial)."))
                return
            # Recargar SIN mover el foco: quedas donde estabas leyendo.
            foco = self.lista_msgs.GetFocusedItem()
            ev = self.msg_rows[foco]["id"] if 0 <= foco < len(self.msg_rows) else None
            self._cargar_mensajes(foco_al_final=False)
            if ev is not None:
                for i, m in enumerate(self.msg_rows):
                    if m["id"] == ev:
                        self.lista_msgs.Focus(i)
                        self.lista_msgs.EnsureVisible(i)
                        break
            notificar(_("{0} mensajes anteriores añadidos.").format(n))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Cifrado OMEMO (X6) ----------------
    def _es_chat_de_puente(self, xs):
        """True si esta conversación es de una pasarela (su dominio es un gateway): ahí no hay OMEMO."""
        try:
            dom = self.jid.split("@", 1)[1] if "@" in self.jid else self.jid
            return dom in {p["dominio"] for p in xs.puentes(self.account_id)}
        except Exception:
            return False

    def _refrescar_boton_cifrado(self):
        """Pone la etiqueta del botón y el título de la ventana según el estado del cifrado."""
        if not getattr(self, "btn_cifrado", None):
            return
        from app.services.xmpp.xmpp_service import xmpp_service
        activo = xmpp_service.esta_cifrado(self.account_id, self.jid)
        self.btn_cifrado.SetLabel(_("Desactivar &cifrado") if activo else _("Activar &cifrado"))
        try:
            self.SetTitle(self._titulo_reposo())
        except Exception:
            pass

    def _toggle_cifrado(self):
        from app.services.xmpp.xmpp_service import xmpp_service
        activo = xmpp_service.esta_cifrado(self.account_id, self.jid)
        try:
            nuevo = xmpp_service.fijar_cifrado(self.account_id, self.jid, not activo)
        except Exception as e:
            notificar(_("No se pudo cambiar el cifrado: {0}").format(e))
            return
        self._refrescar_boton_cifrado()
        if nuevo:
            notificar(_("Cifrado OMEMO activado: los mensajes de esta conversación se enviarán "
                        "cifrados de extremo a extremo."))
        else:
            notificar(_("Cifrado OMEMO desactivado."))

    def _ver_huellas(self):
        from .xmpp_dialogs import XMPPHuellasDialog
        dlg = XMPPHuellasDialog(self, self.account_id, self.jid, self.nombre, es_muc=self.es_muc)
        dlg.ShowModal()
        dlg.Destroy()

    def _ver_ocupantes(self):
        from .xmpp_dialogs import XMPPOcupantesDialog
        dlg = XMPPOcupantesDialog(self, self.account_id, self.jid, self.nombre)
        dlg.ShowModal()
        dlg.Destroy()

    def _cambiar_tema(self):
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Tema de la sala (déjalo vacío para quitarlo):"),
                                  _("Tema de la sala"))
        nuevo = d.GetValue() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if nuevo is None:
            return
        acc, jid = self.account_id, self.jid

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.cambiar_tema(acc, jid, nuevo.strip())
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Tema cambiado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _corregir_ultimo(self):
        """Corrige tu último mensaje enviado a este contacto (XEP-0308)."""
        from app.services.xmpp.xmpp_service import xmpp_service
        ultimo = xmpp_service.ultimo_mensaje_propio(self.account_id, self.jid)
        if not ultimo:
            speak(_("No has enviado ningún mensaje que corregir."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Nuevo texto del mensaje:"), _("Corregir mi último mensaje"),
                                  value=ultimo["texto"])
        nuevo = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else None
        d.Destroy()
        if not nuevo:
            return
        acc, jid, oid = self.account_id, self.jid, ultimo["id"]

        def _bg(ctx):
            xmpp_service.enviar_correccion(acc, jid, nuevo, oid)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo corregir: {0}").format(err))
            else:
                notificar(_("Mensaje corregido."))
                self._recargar_conservando_foco()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ---------------- Envío ----------------
    def _on_enviar(self, event):
        texto = self.txt_mensaje.GetValue().strip()
        if not texto:
            return
        self.txt_mensaje.SetValue("")
        self._hist_envios.append(texto)
        self._hist_pos = None
        acc, jid = self.account_id, self.jid

        es_muc = self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            if es_muc:
                xmpp_service.enviar_muc(acc, jid, texto)  # el eco de la sala lo pinta al llegar
            else:
                xmpp_service.enviar_mensaje(acc, jid, texto)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            elif not es_muc:
                self._insertar_nuevos()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_key_entrada(self, event):
        code = event.GetKeyCode()
        if code == wx.WXK_UP and self._hist_envios:
            if self._hist_pos is None:
                self._hist_pos = len(self._hist_envios) - 1
            elif self._hist_pos > 0:
                self._hist_pos -= 1
            self.txt_mensaje.SetValue(self._hist_envios[self._hist_pos])
            self.txt_mensaje.SetInsertionPointEnd()
            return
        if code == wx.WXK_DOWN and self._hist_pos is not None:
            if self._hist_pos < len(self._hist_envios) - 1:
                self._hist_pos += 1
                self.txt_mensaje.SetValue(self._hist_envios[self._hist_pos])
            else:
                self._hist_pos = None
                self.txt_mensaje.SetValue("")
            self.txt_mensaje.SetInsertionPointEnd()
            return
        event.Skip()

    def _on_escribiendo(self, event):
        """Envía "composing" (está escribiendo) con debounce de 4 s (XEP-0085)."""
        event.Skip()
        ahora = time.time()
        if ahora - self._typing_ultimo < 4.0:
            return
        self._typing_ultimo = ahora
        if not self.txt_mensaje.GetValue().strip():
            return
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.enviar_estado_chat(acc, jid, "composing", es_muc=es_muc)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: None, pass_context=True,
                                unique_key=f"xmpp:typing:{acc}:{jid}")

    # ---------------- Teclas y menú de la lista ----------------
    def _on_msg_key(self, event):
        code = event.GetKeyCode()
        i = self.lista_msgs.GetFocusedItem()
        m = self.msg_rows[i] if 0 <= i < len(self.msg_rows) else None
        # Ctrl+C sobre la lista: copiar el mensaje enfocado (en el campo de escribir, Ctrl+C copia
        # normal porque este handler solo está en la lista).
        if code == ord('C') and event.ControlDown() and m:
            self._copiar(m)
            return
        # Espacio sobre un adjunto: audio → reproducir/pausar; imagen → ver; resto → descargar.
        if code == wx.WXK_SPACE and m and m.get("tipo") == "media" and m.get("media_url"):
            self._accion_media(m)
            return
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and m and re.search(r'https?://', m["texto"]):
            self._abrir_enlace(m)
            return
        event.Skip()

    # ---------------- Adjuntos y notas de voz ----------------
    def _msg_enfocado(self):
        i = self.lista_msgs.GetFocusedItem()
        return self.msg_rows[i] if 0 <= i < len(self.msg_rows) else None

    def _traducir_mensaje(self):
        """Ctrl+T: traduce a español el mensaje enfocado y lo muestra."""
        m = self._msg_enfocado()
        texto = (m or {}).get("texto", "").strip()
        if not texto:
            speak(_("No hay texto que traducir."), interrupt=True)
            return
        speak(_("Traduciendo..."), interrupt=True)

        def _bg(ctx):
            from app.services.translation.translation_service import translation_service
            return translation_service.traducir(texto, "auto", "es")

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo traducir: {0}").format(err or ""))
                return
            mostrar_info(self, res, _("Traducción"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _describir_imagen(self):
        """Ctrl+I: si el mensaje enfocado es una imagen, la describe con IA (accesibilidad)."""
        m = self._msg_enfocado()
        from app.services.xmpp.xmpp_service import xmpp_service
        ref = (m or {}).get("media_url") or (m or {}).get("texto") or ""
        if not m or m.get("tipo") != "media" or not xmpp_service.es_imagen(ref):
            speak(_("Este mensaje no es una imagen."), interrupt=True)
            return
        speak(_("Analizando la imagen..."), interrupt=True)
        acc = self.account_id

        def _bg(ctx):
            ruta = xmpp_service.descargar_media(acc, ref)
            from app.services.ai.ai_service import ai_service
            return ai_service.describir_imagen(ruta)

        def _fin(res, err):
            if err or not res:
                notificar(_("No se pudo describir la imagen: {0}").format(err or ""))
                return
            mostrar_info(self, res, _("Descripción de la imagen"))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _reenviar(self, m):
        """Reenvía el texto del mensaje a otro contacto o grupo (se elige de una lista)."""
        texto = (m or {}).get("texto", "").strip()
        if not texto:
            return
        # Lista de destinos: contactos y grupos de esta cuenta (no solicitudes).
        filas = db.query("SELECT jid, nombre, tipo FROM xmpp_chats WHERE account_id = ? "
                         "AND COALESCE(solicitud,0)=0 ORDER BY tipo, nombre",
                         (self.account_id,))
        if not filas:
            notificar(_("No tienes contactos ni grupos a los que reenviar."))
            return
        etiquetas = [f"{n or j}" + (_(" (grupo)") if t == "muc" else "") for j, n, t in filas]
        dlg = wx.SingleChoiceDialog(self, _("¿A qué chat reenvías el mensaje?"),
                                    _("Reenviar a..."), etiquetas)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        sel = dlg.GetSelection()
        dlg.Destroy()
        dest_jid, dest_nombre, dest_tipo = filas[sel]
        acc = self.account_id

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            if dest_tipo == "muc":
                xmpp_service.enviar_muc(acc, dest_jid, texto)
            else:
                xmpp_service.enviar_mensaje(acc, dest_jid, texto)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Reenviado a {0}.").format(dest_nombre or dest_jid) if not e
            else _("No se pudo reenviar: {0}").format(e)), pass_context=True)

    def _destacar(self, m):
        """Marca o desmarca un mensaje como destacado (favorito)."""
        mid = m.get("id")
        if not mid:
            return
        nuevo = 0 if m.get("destacado") else 1
        db.execute("UPDATE xmpp_mensajes SET destacado = ? WHERE id = ?", (nuevo, mid))
        notificar(_("Mensaje destacado.") if nuevo else _("Destacado quitado."))
        self._recargar_conservando_foco()

    def _accion_media(self, m):
        from app.services.xmpp.xmpp_service import xmpp_service
        ref = m.get("media_url") or m.get("texto") or ""
        if xmpp_service.es_audio(ref):
            self._reproducir_audio(m)
        elif xmpp_service.es_imagen(ref):
            self._ver_imagen(m)      # imágenes (p. ej. el QR): se ABREN en ventana, no se descargan
        else:
            self._descargar_adjunto(m)

    def _ver_imagen(self, m, auto=False):
        """Descarga la imagen a la caché y la abre en una ventana (para verla/escanear un QR)."""
        acc, url = self.account_id, m.get("media_url") or m.get("texto") or ""
        if not url:
            return
        if not auto:
            speak(_("Abriendo imagen..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.descargar_media(acc, url)

        def _fin(ruta, err):
            if err or not ruta:
                if not auto:
                    notificar(_("No se pudo abrir la imagen: {0}").format(err or ""))
                return
            from .qr_utils import mostrar_imagen
            es_pasarela = "@" not in (self.jid or "")
            mostrar_imagen(self, ruta, titulo=_("Imagen de {0}").format(self.nombre),
                           ayuda=(_("Si es un código QR (por ejemplo de WhatsApp), escanéalo con el "
                                    "móvil desde Dispositivos vinculados.") if es_pasarela else None))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _reproducir_audio(self, m):
        """Reproduce el mensaje de audio con player1 (como Telegram/Matrix): el mismo Espacio
        alterna pausa/reanudar si ya está sonando ese audio."""
        from app.services.media_player.player_service import player_service
        clave = m.get("stanza_id") or m.get("id")
        if getattr(self, "_audio_actual", None) == clave:
            try:
                estado = player_service.backend.obtener_estado()
            except Exception:
                estado = ""
            if estado == "reproduciendo":
                player_service.pausar()
                speak(_("Pausado."), interrupt=True)
            else:
                player_service.reproducir()
                speak(_("Reproduciendo."), interrupt=True)
            return
        speak(_("Cargando audio..."), interrupt=True)
        acc, url = self.account_id, m["media_url"]
        mid = m.get("id")
        falta_dur = not (m.get("duracion") or 0)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            from app.services.audio import microphone
            ruta = xmpp_service.descargar_media(acc, url)
            wav = microphone.convertir_a_wav_si_necesario(ruta) if ruta else ruta
            # Si aún no conocíamos la duración (nota recibida), medirla ahora del WAV decodificado.
            dur = microphone.duracion_wav(wav) if (falta_dur and wav and wav.lower().endswith(".wav")) else 0.0
            return (wav, dur)

        def _fin(res, err):
            ruta, dur = (res if isinstance(res, tuple) else (res, 0.0))
            if err or not ruta:
                notificar(_("No se pudo reproducir el audio: {0}").format(err or ""))
                return
            self._audio_actual = clave
            player_service.cargar_y_reproducir(ruta, self.nombre, service_origin="xmpp",
                                               account_origin_id=self.account_id)
            speak(_("Reproduciendo. Pulse Espacio para pausar."), interrupt=True)
            # Marcar la nota de voz como REPRODUCIDA y guardar la duración recién medida.
            try:
                if dur and falta_dur:
                    db.execute("UPDATE xmpp_mensajes SET reproducido = 1, duracion = ? WHERE id = ?",
                               (float(dur), mid))
                else:
                    db.execute("UPDATE xmpp_mensajes SET reproducido = 1 WHERE id = ?", (mid,))
            except Exception:
                pass
            # Repintar solo esa fila (pendiente -> reproducido, y su duración) sin recargar todo.
            for i, r in enumerate(self.msg_rows):
                if r.get("id") == mid:
                    self._repintar_fila(i)
                    break
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _descargar_adjunto(self, m):
        speak(_("Descargando adjunto..."), interrupt=True)
        acc, url = self.account_id, m["media_url"]
        titulo = self.nombre

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            return xmpp_service.descargar_a_descargas(acc, url, xmpp_service.nombre_desde_url(url), titulo)

        def _fin(ruta, err):
            if err:
                notificar(_("No se pudo descargar: {0}").format(err))
            else:
                notificar(_("Descargado en {0}").format(ruta))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _abrir_adjunto(self, m):
        acc, url = self.account_id, m["media_url"]
        # SEGURIDAD (XMPP-008): un adjunto de tipo ACTIVO (ejecutable/script/acceso directo) NUNCA se
        # abre desde la app. Se descarga a la carpeta de descargas (con Mark-of-the-Web) y se avisa.
        from app.services.xmpp.xmpp_medios import es_extension_activa
        from app.services.xmpp.xmpp_service import xmpp_service
        nombre = xmpp_service.nombre_desde_url(url)
        if es_extension_activa(nombre):
            notificar(_("Por seguridad, «{0}» no se abre desde la aplicación (es un archivo "
                        "ejecutable o de script). Se descargará a tu carpeta de descargas.").format(
                            nombre))
            self._descargar_adjunto(m)
            return

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service as _svc
            return _svc.descargar_media(acc, url)

        def _fin(ruta, err):
            if err or not ruta:
                notificar(_("No se pudo abrir: {0}").format(err or ""))
                return
            try:
                import os
                os.startfile(ruta)
            except Exception as e:
                notificar(_("Descargado en {0}, pero no se pudo abrir: {1}").format(ruta, e))
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_adjuntar(self, event):
        dlg = wx.FileDialog(self, _("Elige el archivo a enviar"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        ruta = dlg.GetPath() if dlg.ShowModal() == wx.ID_OK else ""
        dlg.Destroy()
        if not ruta:
            return
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc
        speak(_("Subiendo archivo..."), interrupt=True)

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.enviar_archivo(acc, jid, ruta, es_muc=es_muc)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar: {0}").format(err))
            elif not es_muc:
                self._insertar_nuevos()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_grabar_voz(self, event):
        from app.services.xmpp.xmpp_service import xmpp_service
        if not getattr(self, "_grabando", False):
            try:
                xmpp_service.iniciar_grabacion_voz()
            except Exception as e:
                notificar(_("No se pudo grabar: {0}").format(e))
                return
            self._grabando = True
            self.btn_grabar.SetLabel(_("&Detener y enviar nota de voz"))
            if getattr(self, "btn_cancelar_voz", None):
                self.btn_cancelar_voz.Enable()
            speak(_("Grabando nota de voz. Pulsa de nuevo para enviarla, o Cancelar grabación para "
                    "descartarla."), interrupt=True)
            return
        self._grabando = False
        self.btn_grabar.SetLabel(_("&Grabar nota de voz"))
        if getattr(self, "btn_cancelar_voz", None):
            self.btn_cancelar_voz.Disable()
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc
        speak(_("Enviando nota de voz..."), interrupt=True)

        def _bg(ctx):
            xmpp_service.detener_grabacion_y_enviar(acc, jid, es_muc=es_muc)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo enviar la nota de voz: {0}").format(err))
            elif not es_muc:
                self._insertar_nuevos()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _on_cancelar_voz(self, event):
        """Descarta la nota de voz que se está grabando, sin enviarla."""
        from app.services.xmpp.xmpp_service import xmpp_service
        if not getattr(self, "_grabando", False):
            return
        self._grabando = False
        self.btn_grabar.SetLabel(_("&Grabar nota de voz"))
        if getattr(self, "btn_cancelar_voz", None):
            self.btn_cancelar_voz.Disable()
        try:
            xmpp_service.cancelar_grabacion_voz()
        except Exception as e:
            notificar(_("No se pudo cancelar: {0}").format(e))
            return
        notificar(_("Grabación cancelada."))

    def _urls_de(self, m):
        crudas = re.findall(r'https?://[^\s<>"\')\]}]+', m.get("texto") or "")
        vistas, salida = set(), []
        for u in crudas:
            u = u.rstrip('.,;:!?')
            if u and u not in vistas:
                vistas.add(u)
                salida.append(u)
        return salida

    def _abrir_enlace(self, m):
        urls = self._urls_de(m)
        if not urls:
            speak(_("Este mensaje no tiene ningún enlace."), interrupt=True)
            return
        from app.core.privacy import abrir_url, limpiar_url
        if len(urls) == 1:
            abrir_url(limpiar_url(urls[0]))
            speak(_("Abriendo enlace."), interrupt=True)
            return
        dlg = wx.SingleChoiceDialog(self, _("Selecciona el enlace que quieres abrir:"),
                                    _("Abrir enlace"), urls)
        if dlg.ShowModal() == wx.ID_OK:
            abrir_url(limpiar_url(dlg.GetStringSelection()))
        dlg.Destroy()

    def _on_context_menu(self, event):
        i = self.lista_msgs.GetFocusedItem()
        if i < 0 or i >= len(self.msg_rows):
            return
        m = self.msg_rows[i]
        menu = wx.Menu()
        it = menu.Append(wx.ID_ANY, _("Responder a este mensaje..."))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._responder(mm), it)
        it = menu.Append(wx.ID_ANY, _("Reaccionar..."))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reaccionar(mm), it)
        it = menu.Append(wx.ID_ANY, _("Quitar mi reacción..."))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reaccionar(mm, quitar=True), it)
        it = menu.Append(wx.ID_ANY, _("Copiar mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._copiar(mm), it)
        if (m.get("texto") or "").strip():
            it = menu.Append(wx.ID_ANY, _("Reenviar a otro chat..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._reenviar(mm), it)
            it = menu.Append(wx.ID_ANY, _("Traducir mensaje"))
            self.Bind(wx.EVT_MENU, lambda e: self._traducir_mensaje(), it)
        it = menu.Append(wx.ID_ANY, _("Quitar destacado") if m.get("destacado") else _("Destacar mensaje"))
        self.Bind(wx.EVT_MENU, lambda e, mm=m: self._destacar(mm), it)
        # Mostrar como QR: útil para vincular pasarelas (WhatsApp) cuando el puente manda el código o
        # enlace de emparejamiento como texto; lo convierte en un QR grande para escanear con el móvil.
        if (m.get("texto") or "").strip():
            it = menu.Append(wx.ID_ANY, _("Mostrar como código QR..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._mostrar_qr(mm), it)
        if m.get("tipo") == "media" and m.get("media_url"):
            it = menu.Append(wx.ID_ANY, _("Descargar a la carpeta de descargas"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._descargar_adjunto(mm), it)
            it = menu.Append(wx.ID_ANY, _("Abrir el adjunto"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._abrir_adjunto(mm), it)
        if self._urls_de(m):
            it = menu.Append(wx.ID_ANY, _("Abrir el enlace del mensaje"))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._abrir_enlace(mm), it)
        if m.get("propio"):
            it = menu.Append(wx.ID_ANY, _("Retirar este mensaje..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._retractar(mm), it)
        if m.get("propio") and not self.es_muc:
            it = menu.Append(wx.ID_ANY, _("Corregir mi último mensaje..."))
            self.Bind(wx.EVT_MENU, lambda e: self._corregir_ultimo(), it)
        if self.es_muc and m.get("stanza_id"):
            it = menu.Append(wx.ID_ANY, _("Retirar este mensaje (moderación)..."))
            self.Bind(wx.EVT_MENU, lambda e, mm=m: self._moderar(mm), it)
        self.PopupMenu(menu)
        menu.Destroy()

    def _mostrar_qr(self, m):
        """Convierte el texto/enlace del mensaje en un código QR grande (para vincular WhatsApp u otra
        pasarela escaneándolo con el móvil). El texto también queda visible para leerlo o copiarlo."""
        texto = (m.get("media_url") or "").strip() or (m.get("texto") or "").strip()
        if not texto:
            speak(_("Este mensaje no tiene texto para el código QR."), interrupt=True)
            return
        from .qr_utils import mostrar_codigo_qr
        mostrar_codigo_qr(self, texto, titulo=_("Código QR de vinculación"),
                          ayuda=_("Si es un código de emparejamiento (por ejemplo de WhatsApp), "
                                  "escanéalo con el móvil desde Dispositivos vinculados."))

    def _responder(self, m):
        rid = m.get("stanza_id")
        if not rid:
            speak(_("Este mensaje no se puede responder."), interrupt=True)
            return
        from .accessible_controls import CustomTextEntryDialog
        d = CustomTextEntryDialog(self, _("Responder a «{0}»:").format((m.get("texto") or "")[:60]),
                                  _("Responder"))
        texto = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not texto:
            return
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.enviar_respuesta(acc, jid, rid, texto, es_muc=es_muc)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            elif not es_muc:
                self._insertar_nuevos()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _reaccionar(self, m, quitar=False):
        rid = m.get("stanza_id")
        if not rid:
            return
        from .accessible_controls import CustomTextEntryDialog
        titulo = _("Quitar mi reacción") if quitar else _("Reaccionar")
        d = CustomTextEntryDialog(self, _("Emoji (por ejemplo 👍, ❤, 😂):"), titulo)
        emoji = d.GetValue().strip() if d.ShowModal() == wx.ID_OK else ""
        d.Destroy()
        if not emoji:
            return
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.enviar_reaccion(acc, jid, rid, emoji, quitar=quitar, es_muc=es_muc)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                self._recargar_conservando_foco()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _retractar(self, m):
        rid = m.get("stanza_id")
        if not rid:
            return
        if confirmar(self, _("¿Retirar tu mensaje para todos?"), _("Retirar mensaje")) != wx.ID_YES:
            return
        acc, jid, es_muc = self.account_id, self.jid, self.es_muc

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.retractar_mensaje(acc, jid, rid, es_muc=es_muc)
            return True

        def _fin(res, err):
            if err:
                notificar(_("No se pudo: {0}").format(err))
            else:
                notificar(_("Mensaje retirado."))
                self._recargar_conservando_foco()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    def _moderar(self, m):
        if confirmar(self, _("¿Retirar este mensaje de la sala? (necesitas ser moderador/a)"),
                     _("Moderar mensaje")) != wx.ID_YES:
            return
        acc, jid, sid = self.account_id, self.jid, m["stanza_id"]

        def _bg(ctx):
            from app.services.xmpp.xmpp_service import xmpp_service
            xmpp_service.moderar_mensaje(acc, jid, sid)
            return True
        task_manager.start_task(_bg, callback=lambda r, e: notificar(
            _("Mensaje retirado.") if not e else _("No se pudo: {0}").format(e)), pass_context=True)

    def _copiar(self, m):
        try:
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(m.get("texto") or ""))
                wx.TheClipboard.Close()
                speak(_("Mensaje copiado."), interrupt=True)
        except Exception:
            pass

    # ---------------- Eventos en vivo ----------------
    def _on_evento(self, datos):
        if datos.get("account_id") != self.account_id:
            return
        tipo = datos.get("tipo")
        if tipo == "mensaje" and datos.get("chat_jid", self.jid) in (self.jid, None):
            wx.CallAfter(self._refrescar_vivo)
        elif tipo in ("entregado", "leido_por", "historial"):
            # Cambian marcas o llega histórico. El MAM/los recibos llegan en RÁFAGAS: en vez de una
            # recarga completa (300 filas) por cada evento, se coalesce en una sola. Se pone el flag
            # ANTES del salto de hilo para no encolar cientos de CallAfter.
            if not self._recarga_pendiente:
                self._recarga_pendiente = True
                wx.CallAfter(self._armar_recarga_coalescida)
        elif tipo == "chatstate" and datos.get("jid") == self.jid:
            wx.CallAfter(self._mostrar_typing, datos.get("estado"), datos.get("nick"))

    def _refrescar_vivo(self):
        try:
            antes = len(self.msg_rows)
            self._insertar_nuevos()
            if len(self.msg_rows) == antes:
                # No hubo mensajes nuevos: fue una edición (corrección) de uno existente → recargar.
                self._recargar_conservando_foco()
        except Exception:
            pass

    def _armar_recarga_coalescida(self):
        """En el hilo de la UI: programa la recarga con un pequeño retardo para juntar la ráfaga."""
        wx.CallLater(400, self._recargar_coalescido)

    def _recargar_coalescido(self):
        self._recarga_pendiente = False
        self._recargar_conservando_foco()

    def _recargar_conservando_foco(self):
        """Recarga la lista (para reflejar ediciones/entregado/histórico) sin perder tu posición."""
        try:
            foco = self.lista_msgs.GetFocusedItem()
            db_id = self.msg_rows[foco]["id"] if 0 <= foco < len(self.msg_rows) else None
            self._cargar_mensajes(foco_al_final=False)
            if db_id is not None:
                for i, m in enumerate(self.msg_rows):
                    if m["id"] == db_id:
                        self.lista_msgs.Focus(i)
                        self.lista_msgs.EnsureVisible(i)
                        break
        except Exception:
            pass

    def _titulo_reposo(self):
        """Título en reposo, con el indicador de cifrado si está activo."""
        try:
            from app.services.xmpp.xmpp_service import xmpp_service
            if xmpp_service.esta_cifrado(self.account_id, self.jid):
                return self._titulo_base + _(" [cifrado OMEMO]")
        except Exception:
            pass
        return self._titulo_base

    def _mostrar_typing(self, estado, nick=None):
        try:
            if estado == "composing":
                # En grupos indicamos QUIÉN escribe (el apodo); en 1:1, el nombre del contacto.
                quien = nick if (self.es_muc and nick) else self.nombre
                self.SetTitle(_("{0} — escribiendo...").format(quien))
                if self._titulo_typing:
                    self._titulo_typing.Stop()
                self._titulo_typing = wx.CallLater(8000, lambda: self.SetTitle(self._titulo_reposo()))
            else:
                self.SetTitle(self._titulo_reposo())
        except Exception:
            pass

    def _on_cerrar(self, event):
        try:
            event_bus.unsubscribe("xmpp_evento", self._on_evento)
        except Exception:
            pass
        # Si estabas grabando una nota de voz, cancelarla al cerrar (libera el micrófono compartido).
        if getattr(self, "_grabando", False):
            try:
                from app.services.xmpp.xmpp_service import xmpp_service
                xmpp_service.cancelar_grabacion_voz()
            except Exception:
                pass
        # Marcar leído al salir: en 1:1 envía además el marcador de lectura; en salas, solo local.
        try:
            if self.es_muc:
                from app.core.database import db as _db
                from app.services.xmpp.xmpp_common import db_id_chat
                _db.execute("UPDATE xmpp_chats SET unread_count = 0 WHERE id = ?",
                            (db_id_chat(self.account_id, self.jid),))
                _db.execute("UPDATE xmpp_mensajes SET leido = 1 WHERE account_id = ? AND chat_jid = ?",
                            (self.account_id, self.jid))
            else:
                from app.services.xmpp.xmpp_service import xmpp_service
                xmpp_service.marcar_leido(self.account_id, self.jid)
        except Exception:
            pass
        event.Skip()
