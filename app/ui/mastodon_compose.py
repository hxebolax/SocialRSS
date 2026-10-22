# -*- coding: utf-8 -*-
"""Compositor de toots de Mastodon: clase base (UI, adjuntos, borrador).
"""
import logging
import wx
import wx.adv

from app.core.config_manager import config
from app.core.speech import notificar
from app.services.mastodon.mastodon_service import mastodon_service
from .accessible_controls import AccessibleTextCtrl
from .mastodon_compose_acciones import _ComposeAccionesMixin


log = logging.getLogger(__name__)

class ComposeTootDialog(_ComposeAccionesMixin, wx.Dialog):
    """Diálogo compositor modal para crear o responder toots en Mastodon."""

    # Extensiones admitidas por Mastodon, según el servidor oficial (app/models/media_attachment.rb).
    # Se clasifican por tipo porque las reglas de combinación dependen del tipo (ver _puede_anadir).
    EXT_IMAGEN = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif")
    EXT_VIDEO = (".webm", ".mp4", ".m4v", ".mov")
    EXT_AUDIO = (".ogg", ".oga", ".mp3", ".wav", ".flac", ".opus", ".aac", ".m4a", ".3gp", ".wma")

    @classmethod
    def _tipo_por_extension(cls, ruta):
        """Devuelve 'image', 'video', 'audio' o '' a partir de la extensión del archivo."""
        import os
        ext = os.path.splitext((ruta or "").lower())[1]
        if ext in cls.EXT_IMAGEN:
            return "image"
        if ext in cls.EXT_VIDEO:
            return "video"
        if ext in cls.EXT_AUDIO:
            return "audio"
        return ""

    def _tipo_adjunto(self, att):
        """Tipo de un adjunto de self.attachments. Para los ya publicados (edición) usa su 'tipo'
        de Mastodon (image/video/gifv/audio); para los locales, la extensión. 'gifv' (GIF animado
        convertido) se trata como imagen: el servidor SÍ permite combinarlo con imágenes."""
        if att.get("existing"):
            t = (att.get("tipo") or "").lower()
            if t in ("video", "audio"):
                return t
            return "image"  # image, gifv, unknown → como imagen a efectos de combinación
        return self._tipo_por_extension(att.get("path", ""))

    def _puede_anadir(self, tipo_nuevo):
        """Aplica la regla OFICIAL de combinación de adjuntos de Mastodon
        (post_status_service.rb: error si hay más de un adjunto y alguno es audio o vídeo) y el
        máximo de 4. Devuelve (True, "") si se puede añadir, o (False, motivo) si no."""
        tipos = [self._tipo_adjunto(a) for a in self.attachments]
        hay_av = any(t in ("audio", "video") for t in tipos)
        total = len(self.attachments)
        if tipo_nuevo in ("audio", "video"):
            if total >= 1:
                return False, _("El audio y el vídeo deben ir solos: una publicación no puede "
                                "combinar audio o vídeo con ningún otro archivo.")
            return True, ""
        # tipo_nuevo == imagen (o desconocido tratado como imagen)
        if hay_av:
            return False, _("No se pueden añadir imágenes a una publicación que ya lleva audio o "
                            "vídeo: el audio y el vídeo deben ir solos.")
        if total >= 4:
            return False, _("Máximo 4 imágenes por publicación.")
        return True, ""

    def _validar_combinacion_adjuntos(self):
        """Comprobación final antes de publicar (por si en edición se precargó una combinación no
        válida). Devuelve (True, '') o (False, motivo)."""
        tipos = [self._tipo_adjunto(a) for a in self.attachments]
        if len(tipos) > 1 and any(t in ("audio", "video") for t in tipos):
            return False, _("El audio y el vídeo deben ir solos: quite el resto de archivos o el "
                            "audio/vídeo antes de publicar.")
        if len(tipos) > 4:
            return False, _("Máximo 4 archivos por publicación.")
        return True, ""

    def _capturar_borrador(self):
        """Reúne TODO lo que se iba a publicar (texto, adjuntos, CW, privacidad, idioma, encuesta y
        programación) en un diccionario, para poder reabrir el compositor tal cual si el envío
        falla. Es el 'borrador de seguridad'; vive en memoria hasta que el toot se publica bien o
        el usuario decide descartarlo."""
        b = {
            "texto": self.txt_cuerpo.GetValue(),
            "cw_activo": self.chk_cw.IsChecked(),
            "cw": self.txt_cw.GetValue(),
            "priv_idx": self.choice_priv.GetSelection(),
            "idioma_idx": self.choice_idioma.GetSelection(),
            "attachments": [dict(a) for a in self.attachments],
            "encuesta_activa": self.chk_encuesta.IsChecked(),
            "opciones": [t.GetValue() for t in self.txt_opciones],
            "duracion_idx": self.choice_duracion.GetSelection(),
            "multiple": self.chk_multiple.IsChecked(),
            "programar_activo": self.chk_programar.IsChecked(),
            "prog_date": None,
            "prog_hora": self.spin_hora.GetValue(),
            "prog_min": self.spin_min.GetValue(),
        }
        try:
            if self.chk_programar.IsChecked():
                b["prog_date"] = self.date_prog.GetValue()  # wx.DateTime (borrador en memoria)
        except Exception:
            pass
        return b

    def _borrador_persistible(self):
        """Como _capturar_borrador, pero JSON-serializable para guardarlo en la base de datos
        (borradores persistentes, auditoría N3): la fecha de programación (wx.DateTime) se
        convierte a texto ISO."""
        b = self._capturar_borrador()
        pd = b.get("prog_date")
        if pd is not None and not isinstance(pd, str):
            try:
                b["prog_date"] = pd.FormatISODate()
            except Exception:
                b["prog_date"] = None
        return b

    def _hay_contenido(self):
        """True si el compositor tiene algo que merezca guardarse como borrador."""
        if self.txt_cuerpo.GetValue().strip():
            return True
        if self.chk_cw.IsChecked() and self.txt_cw.GetValue().strip():
            return True
        if self.attachments:
            return True
        if self.chk_encuesta.IsChecked() and any(t.GetValue().strip() for t in self.txt_opciones):
            return True
        return False

    def _guardar_borrador_persistente(self):
        """Guarda (o actualiza) el contenido actual como borrador persistente en la BD."""
        from app.services.mastodon import mastodon_borradores
        contexto = {}
        if self.in_reply_to_id:
            contexto["in_reply_to_id"] = self.in_reply_to_id
        if self.quote_status_id:
            contexto["quote_status_id"] = self.quote_status_id
        self.draft_db_id = mastodon_borradores.guardar_borrador(
            self.account_id, self.txt_cuerpo.GetValue().strip(),
            self._borrador_persistible(), contexto or None, draft_id=self.draft_db_id)
        notificar(_("Borrador guardado."))

    def _on_cancelar(self, event):
        """Cerrar sin publicar (botón Cancelar, Escape o la X de la ventana): si hay algo
        escrito, ofrece guardarlo como BORRADOR persistente que sobrevive al cierre de la
        aplicación (menú Mastodon → Borradores). Al editar un toot ya publicado no aplica
        (ahí cancelar simplemente descarta los cambios de la edición)."""
        if self.edit_status_db_id or not self._hay_contenido():
            self.EndModal(wx.ID_CANCEL)
            return
        from .accessible_controls import elegir_opcion
        if self.draft_db_id:
            msg = _("Has cambiado este borrador. ¿Guardar los cambios antes de cerrar? "
                    "(Cancelar vuelve al compositor.)")
            botones = [_("&Guardar los cambios"), _("&Descartar los cambios")]
        else:
            msg = _("¿Guardar lo escrito como borrador para retomarlo más tarde (menú "
                    "Mastodon → Borradores), o descartarlo? (Cancelar vuelve al compositor.)")
            botones = [_("&Guardar como borrador"), _("&Descartar")]
        r = elegir_opcion(self, msg, _("Cerrar el compositor"), botones)
        if r is None:
            return  # volver al compositor sin cerrar
        if r == 0:
            self._guardar_borrador_persistente()
        self.EndModal(wx.ID_CANCEL)

    def _restaurar_borrador(self, b):
        """Vuelca un borrador de seguridad (de _capturar_borrador) sobre los controles ya creados."""
        try:
            self.txt_cuerpo.SetValue(b.get("texto", "") or "")
            if b.get("cw_activo"):
                self.chk_cw.SetValue(True)
                self.txt_cw.Enable()
                self.txt_cw.SetValue(b.get("cw", "") or "")
            idx = b.get("priv_idx", wx.NOT_FOUND)
            if isinstance(idx, int) and 0 <= idx < self.choice_priv.GetCount() and self.choice_priv.IsEnabled():
                self.choice_priv.SetSelection(idx)
            idi = b.get("idioma_idx", wx.NOT_FOUND)
            if isinstance(idi, int) and 0 <= idi < self.choice_idioma.GetCount():
                self.choice_idioma.SetSelection(idi)
            # Encuesta primero (al activarla se vacían los adjuntos, son excluyentes).
            if b.get("encuesta_activa"):
                self.chk_encuesta.SetValue(True)
                self._on_toggle_encuesta(None)
                for t, val in zip(self.txt_opciones, b.get("opciones", []) or []):
                    t.SetValue(val or "")
                di = b.get("duracion_idx", 4)
                if isinstance(di, int) and 0 <= di < self.choice_duracion.GetCount():
                    self.choice_duracion.SetSelection(di)
                self.chk_multiple.SetValue(bool(b.get("multiple")))
            else:
                ats = b.get("attachments")
                if ats:
                    self.attachments = [dict(a) for a in ats]
                    self._update_attachments_list()
            # Programación. La fecha puede venir como wx.DateTime (borrador de seguridad en
            # memoria) o como texto ISO (borrador persistente leído de la base de datos).
            pd = b.get("prog_date")
            if isinstance(pd, str):
                d = wx.DateTime()
                pd = d if pd and d.ParseISODate(pd) else None
            if b.get("programar_activo") and pd is not None:
                self.chk_programar.SetValue(True)
                self._on_toggle_programar(None)
                try:
                    self.date_prog.SetValue(pd)
                    self.spin_hora.SetValue(int(b.get("prog_hora", 0)))
                    self.spin_min.SetValue(int(b.get("prog_min", 0)))
                except Exception:
                    pass
            self._recontar_caracteres()  # recontar caracteres
        except Exception as e:
            log.debug("No se pudo restaurar del todo el borrador de seguridad: %s", e)

    def __init__(self, parent, account_id: int, in_reply_to_id: str = None, texto_inicial: str = "",
                 reply_visibility: str = None, edit_status_db_id: str = None, cw_inicial: str = "",
                 media_existente: list = None, quote_status_id: str = None, borrador: dict = None,
                 draft_db_id: int = None):
        if edit_status_db_id:
            titulo = _("Editar Toot - Mastodon")
        elif quote_status_id:
            titulo = _("Citar Toot - Mastodon")
        elif in_reply_to_id:
            titulo = _("Responder Toot - Mastodon")
        else:
            titulo = _("Nuevo Toot - Mastodon")
        super().__init__(
            parent,
            title=titulo,
            size=(620, 520),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
            name=_("Diálogo de composición de toot")
        )

        self.account_id = account_id
        # Límite de caracteres REAL de la instancia (puede ser >500). Se toma de la caché sin
        # bloquear y se refresca de la instancia al abrir (ver final de __init__).
        self._max_chars = mastodon_service.obtener_limite_caracteres(account_id)
        # Límite de la descripción (texto alternativo) de adjuntos que marca el servidor; se
        # refresca de la instancia al abrir (ver final de __init__).
        self._max_desc = mastodon_service.obtener_limite_descripcion(account_id)
        self.in_reply_to_id = in_reply_to_id
        # Si es una cita, el id del toot citado (cita nativa de Mastodon, quoted_status_id).
        self.quote_status_id = quote_status_id
        # Si es una edición, el db_id del toot que se está editando (status_update).
        self.edit_status_db_id = edit_status_db_id
        # Al responder, la privacidad inicial es la del mensaje original (si te
        # escribieron en privado o por mensaje directo, respondes igual salvo que tú
        # lo cambies a propósito). Es la conducta de TWBlue, para no exponer por error
        # una conversación privada.
        self.reply_visibility = reply_visibility
        # Si el compositor se abrió desde el gestor de borradores, el id del borrador
        # persistente: al publicar se elimina y al cerrar se ofrece actualizar.
        self.draft_db_id = draft_db_id
        # Título base (sin el contador) para poder añadirle los caracteres restantes.
        self._titulo_base = self.GetTitle()

        # Cada elemento: {"path": str, "alt": str}
        self.attachments = []

        self._init_ui()
        self._instalar_accesos_directos()
        self.txt_cuerpo.SetValue(texto_inicial)
        # Precargar la advertencia de contenido (CW) si se indica: al editar (la del toot) o
        # al responder (se HEREDA la del original, como TWBlue). Así una respuesta a un toot
        # con CW sale ya marcada con la misma advertencia.
        if cw_inicial:
            self.chk_cw.SetValue(True)
            self.txt_cw.Enable()
            self.txt_cw.SetValue(cw_inicial)
        # Al editar, además precargar los adjuntos existentes y bloquear la privacidad
        # (Mastodon no permite cambiar la visibilidad de un toot ya publicado).
        if edit_status_db_id:
            if media_existente:
                self.attachments = list(media_existente)
                self._update_attachments_list()
            self.choice_priv.Disable()
            # Convertir en hilo crearía publicaciones NUEVAS, que no es editar la que hay.
            self.btn_a_hilo.Hide()
        # Borrador de seguridad: si una publicación falló y el usuario quiso recuperarla, se
        # reabre el compositor con TODO lo que iba a publicarse (texto, adjuntos, CW, privacidad,
        # encuesta, programación e idioma). Se restaura al final para pisar los valores por defecto.
        if borrador:
            self._restaurar_borrador(borrador)
        # Dejar el cursor al final (tras la mención si es una respuesta) y el foco
        # en el cuadro de escritura para empezar a teclear de inmediato.
        self.txt_cuerpo.SetInsertionPointEnd()
        self._recontar_caracteres()
        self.CenterOnParent()
        self.txt_cuerpo.SetFocus()
        # Refrescar el límite real desde la instancia (por si cambió o no estaba cacheado).
        def _aplicar_limite(limite, err):
            if not self:
                return
            if not err and limite and limite != self._max_chars:
                self._max_chars = int(limite)
                self._recontar_caracteres()  # recontar con el límite correcto
            # Refrescar también el límite de la descripción (se cacheó en la misma llamada).
            self._max_desc = mastodon_service.obtener_limite_descripcion(self.account_id)
        mastodon_service.actualizar_limite_caracteres_async(self.account_id, callback=_aplicar_limite)

    def _instalar_accesos_directos(self):
        """Alt+P publica desde CUALQUIER control del diálogo (el mnemónico del botón no se
        procesaba de forma fiable fuera del cuadro de texto) y, al volver de Alt+Tab, se
        re-enfoca el último control para que el lector recupere la posición del cursor."""
        # Alt+P -> publicar, válido tenga el foco quien lo tenga.
        self._id_publicar = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self._on_enviar, id=self._id_publicar)
        self.SetAcceleratorTable(wx.AcceleratorTable([
            wx.AcceleratorEntry(wx.ACCEL_ALT, ord('P'), self._id_publicar),
        ]))
        # Recordar el último control con foco dentro del diálogo, para restaurarlo al reactivar.
        self._ultimo_foco = None
        self.Bind(wx.EVT_CHILD_FOCUS, self._on_child_focus)
        self.Bind(wx.EVT_ACTIVATE, self._on_activate)

    def _on_child_focus(self, event):
        w = event.GetWindow()
        if w is not None:
            self._ultimo_foco = w
        event.Skip()

    def _on_activate(self, event):
        # Al RECUPERAR la activación de la ventana, devolver el foco al último control usado (o al
        # cuerpo). Sin esto, tras Alt+Tab el lector no sabe dónde está el cursor y las flechas no
        # anuncian nada.
        if event.GetActive():
            w = self._ultimo_foco if self._ultimo_foco else getattr(self, "txt_cuerpo", None)
            if w:
                wx.CallAfter(self._reenfocar, w)
        event.Skip()

    def _reenfocar(self, w):
        try:
            if w:
                w.SetFocus()
        except Exception:
            pass

    def _init_ui(self):
        vbox = wx.BoxSizer(wx.VERTICAL)
        
        # --- ADVERTENCIA DE CONTENIDO (CW) ---
        cw_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.chk_cw = wx.CheckBox(self, label=_("Contiene advertencia de contenido (CW)"), name=_("Activar advertencia de contenido"))
        self.chk_cw.Bind(wx.EVT_CHECKBOX, self._on_toggle_cw)
        cw_sizer.Add(self.chk_cw, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        self.txt_cw = AccessibleTextCtrl(
            self,
            name=_("Advertencia de contenido"),
            ayuda_texto=_("Introduzca un resumen de advertencia. Si está activo, el texto principal estará oculto.")
        )
        # El CW también cuenta para el límite (como en Mastodon): recontar al escribirlo.
        self.txt_cw.Bind(wx.EVT_TEXT, self._on_text_change)
        self.txt_cw.Disable() # Inicialmente desactivado
        cw_sizer.Add(self.txt_cw, 1, wx.EXPAND | wx.ALL, 5)
        vbox.Add(cw_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # --- CUERPO DEL TOOT ---
        vbox.Add(wx.StaticText(self, label=_("Texto de la publicación:")), 0, wx.ALL, 5)
        self.txt_cuerpo = AccessibleTextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_RICH2,
            name=_("Cuerpo del toot"),
            ayuda_texto=_("Texto principal de su publicación.")
        )
        self.txt_cuerpo.Bind(wx.EVT_TEXT, self._on_text_change)
        self.txt_cuerpo.Bind(wx.EVT_KEY_DOWN, self._on_cuerpo_key)
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt_cuerpo)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt_cuerpo)
        vbox.Add(self.txt_cuerpo, 1, wx.EXPAND | wx.ALL, 5)
        
        # --- META INFO (CONTADOR Y PRIVACIDAD) ---
        meta_sizer = wx.BoxSizer(wx.HORIZONTAL)
        
        self.lbl_contador = wx.StaticText(self, label="500")
        meta_sizer.Add(self.lbl_contador, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        meta_sizer.AddStretchSpacer()
        
        meta_sizer.Add(wx.StaticText(self, label=_("Privacidad:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        
        # Opciones legibles en español
        self.priv_keys = ["public", "unlisted", "private", "direct"]
        self.choice_priv = wx.Choice(
            self,
            choices=[_("Público"), _("No listado"), _("Privado"), _("Mensaje directo")],
            name=_("Selector de privacidad")
        )
        # Al responder, reply_visibility ya viene con la regla de TWBlue (directo si te
        # escribieron en directo; si no, no listado). Si no es una respuesta, se usa la
        # visibilidad por defecto (la del servidor si está activado, o la de la app).
        if self.reply_visibility in self.priv_keys:
            default_vis = self.reply_visibility
        else:
            default_vis = mastodon_service.visibilidad_predeterminada(self.account_id)
        if default_vis in self.priv_keys:
            self.choice_priv.SetSelection(self.priv_keys.index(default_vis))
        else:
            self.choice_priv.SetSelection(0)
            
        meta_sizer.Add(self.choice_priv, 0, wx.ALL, 5)

        # Idioma del toot (Mastodon lo guarda por publicación). Por defecto, el idioma
        # configurado en la app; el usuario puede cambiarlo por publicación.
        meta_sizer.Add(wx.StaticText(self, label=_("&Idioma:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
        self._idiomas = [
            ("es", _("Español")), ("en", _("Inglés")), ("ca", _("Catalán")),
            ("gl", _("Gallego")), ("eu", _("Euskera")), ("pt", _("Portugués")),
            ("fr", _("Francés")), ("it", _("Italiano")), ("de", _("Alemán")),
        ]
        self.choice_idioma = wx.Choice(self, choices=[n for _c, n in self._idiomas],
                                       name=_("Idioma de la publicación"))
        _idioma_def = (config.get("general", "language", "es") or "es")
        _codigos = [c for c, _n in self._idiomas]
        self.choice_idioma.SetSelection(_codigos.index(_idioma_def) if _idioma_def in _codigos else 0)
        meta_sizer.Add(self.choice_idioma, 0, wx.ALL, 5)

        # Quién puede CITAR esta publicación (Mastodon 4.5+). Solo si la instancia lo admite
        # (api_versions.mastodon >= 7); "Por defecto" respeta lo configurado en tu perfil.
        self._politicas_cita = [None, "public", "followers", "nobody"]
        self.choice_citas = None
        if mastodon_service.version_api(self.account_id) >= 7:
            meta_sizer.Add(wx.StaticText(self, label=_("Pueden &citarla:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            self.choice_citas = wx.Choice(self, choices=[
                _("Por defecto de tu cuenta"), _("Cualquiera"), _("Solo tus seguidores"), _("Solo tú")],
                name=_("Quién puede citar la publicación"))
            self.choice_citas.SetSelection(0)
            meta_sizer.Add(self.choice_citas, 0, wx.ALL, 5)

        vbox.Add(meta_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # Botón para insertar un emoji personalizado de la instancia (también Ctrl+Mayús+E
        # con el foco en el texto). Inserta ':atajo:' en la posición del cursor.
        self.btn_emoji = wx.Button(self, label=_("Insertar e&moji personalizado... (Ctrl+Mayús+E)"),
                                   name=_("Insertar emoji personalizado"))
        self.btn_emoji.Bind(wx.EVT_BUTTON, self._on_insertar_emoji)
        vbox.Add(self.btn_emoji, 0, wx.ALL, 5)

        # --- ARCHIVOS ADJUNTOS (LISTA Y ACCIONES) ---
        vbox.Add(wx.StaticText(self, label=_("Archivos multimedia adjuntos (máximo 4):")), 0, wx.ALL, 5)
        
        attachments_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.list_attachments = wx.ListBox(
            self,
            style=wx.LB_SINGLE,
            name=_("Lista de archivos adjuntos")
        )
        self.list_attachments.Bind(wx.EVT_LISTBOX, self._on_attachment_selected)
        attachments_sizer.Add(self.list_attachments, 1, wx.EXPAND | wx.ALL, 5)
        
        # Botones para gestionar adjuntos
        att_btn_sizer = wx.BoxSizer(wx.VERTICAL)
        
        self.btn_add_att = wx.Button(self, label=_("&Añadir adjunto..."), name=_("Añadir archivo adjunto"))
        self.btn_add_att.Bind(wx.EVT_BUTTON, self._on_add_attachment)
        att_btn_sizer.Add(self.btn_add_att, 0, wx.EXPAND | wx.BOTTOM, 5)
        
        self.btn_del_att = wx.Button(self, label=_("&Eliminar adjunto"), name=_("Eliminar adjunto seleccionado"))
        self.btn_del_att.Bind(wx.EVT_BUTTON, self._on_del_attachment)
        self.btn_del_att.Disable()
        att_btn_sizer.Add(self.btn_del_att, 0, wx.EXPAND | wx.BOTTOM, 5)
        
        self.btn_desc_ia = wx.Button(self, label=_("Describir con &IA"), name=_("Describir adjunto seleccionado con IA"))
        self.btn_desc_ia.Bind(wx.EVT_BUTTON, self._on_desc_ia)
        self.btn_desc_ia.Disable()
        att_btn_sizer.Add(self.btn_desc_ia, 0, wx.EXPAND | wx.BOTTOM, 5)
        
        self.btn_edit_alt = wx.Button(self, label=_("&Editar descripción"), name=_("Editar descripción de texto alternativo"))
        self.btn_edit_alt.Bind(wx.EVT_BUTTON, self._on_edit_alt)
        self.btn_edit_alt.Disable()
        att_btn_sizer.Add(self.btn_edit_alt, 0, wx.EXPAND | wx.BOTTOM, 5)

        # Punto focal (solo imágenes): qué zona de la imagen queda visible en las
        # miniaturas recortadas de los clientes visuales (auditoría N5).
        self.btn_focal = wx.Button(self, label=_("Punto &focal..."), name=_("Elegir el punto focal de la imagen"))
        self.btn_focal.Bind(wx.EVT_BUTTON, self._on_punto_focal)
        self.btn_focal.Disable()
        att_btn_sizer.Add(self.btn_focal, 0, wx.EXPAND | wx.BOTTOM, 5)
        
        attachments_sizer.Add(att_btn_sizer, 0, wx.EXPAND | wx.ALL, 5)
        vbox.Add(attachments_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # --- ENCUESTA (opcional) ---
        self.chk_encuesta = wx.CheckBox(self, label=_("Añadir una encuesta"), name=_("Activar encuesta"))
        self.chk_encuesta.Bind(wx.EVT_CHECKBOX, self._on_toggle_encuesta)
        vbox.Add(self.chk_encuesta, 0, wx.ALL, 5)

        self.panel_encuesta = wx.Panel(self)
        pe = wx.BoxSizer(wx.VERTICAL)
        # Mastodon: máximo 4 opciones, y cada opción admite hasta 50 caracteres
        # (poll_options_validator: MAX_OPTIONS=4, MAX_OPTION_CHARS=50).
        self.txt_opciones = []
        for i in range(4):
            # Etiqueta VISIBLE encima de cada campo: el lector la lee como nombre del cuadro
            # (además del 'name' accesible), de modo que se sabe para qué es cada opción.
            pe.Add(wx.StaticText(self.panel_encuesta, label=_("Opción {0}:").format(i + 1)),
                   0, wx.LEFT | wx.TOP, 3)
            t = AccessibleTextCtrl(self.panel_encuesta, name=_("Opción de encuesta {0}").format(i + 1),
                                   ayuda_texto=_("Escriba una opción de la encuesta (máximo 50 caracteres). Deje en blanco las que no use (mínimo 2)."))
            t.SetMaxLength(50)
            pe.Add(t, 0, wx.EXPAND | wx.ALL, 3)
            self.txt_opciones.append(t)
        fila_enc = wx.BoxSizer(wx.HORIZONTAL)
        fila_enc.Add(wx.StaticText(self.panel_encuesta, label=_("Duración:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        # Rango oficial de Mastodon para las encuestas: de 5 minutos a 1 mes
        # (poll_expiration_validator: MIN_EXPIRATION = 5 min, MAX_EXPIRATION = 1 mes).
        self._duraciones = [
            (_("5 minutos"), 300), (_("30 minutos"), 1800), (_("1 hora"), 3600),
            (_("6 horas"), 21600), (_("1 día"), 86400), (_("3 días"), 259200),
            (_("7 días"), 604800), (_("14 días"), 1209600), (_("30 días"), 2592000),
        ]
        self.choice_duracion = wx.Choice(self.panel_encuesta, choices=[d[0] for d in self._duraciones], name=_("Duración de la encuesta"))
        self.choice_duracion.SetSelection(4)  # 1 día por defecto
        fila_enc.Add(self.choice_duracion, 0, wx.ALL, 3)
        self.chk_multiple = wx.CheckBox(self.panel_encuesta, label=_("Permitir varias opciones"), name=_("Encuesta de opción múltiple"))
        fila_enc.Add(self.chk_multiple, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        pe.Add(fila_enc, 0, wx.EXPAND)
        self.panel_encuesta.SetSizer(pe)
        self.panel_encuesta.Hide()
        vbox.Add(self.panel_encuesta, 0, wx.EXPAND | wx.ALL, 5)

        # --- PROGRAMAR (opcional, solo en publicaciones nuevas) ---
        self.chk_programar = wx.CheckBox(self, label=_("Programar esta publicación para más tarde"), name=_("Programar publicación"))
        self.chk_programar.Bind(wx.EVT_CHECKBOX, self._on_toggle_programar)
        vbox.Add(self.chk_programar, 0, wx.ALL, 5)
        self.panel_programar = wx.Panel(self)
        pp = wx.BoxSizer(wx.HORIZONTAL)
        pp.Add(wx.StaticText(self.panel_programar, label=_("Fecha:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        self.date_prog = wx.adv.DatePickerCtrl(self.panel_programar, name=_("Fecha de programación"))
        pp.Add(self.date_prog, 0, wx.ALL, 3)
        # Hora y minutos como campos numéricos editables (se puede ESCRIBIR la cifra,
        # además de usar las flechas), en vez de un selector de hora que iba de 1 en 1.
        import datetime as _dt
        _inicio = _dt.datetime.now() + _dt.timedelta(minutes=15)
        pp.Add(wx.StaticText(self.panel_programar, label=_("Hora:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        self.spin_hora = wx.SpinCtrl(self.panel_programar, min=0, max=23, initial=_inicio.hour, name=_("Hora (0 a 23)"))
        pp.Add(self.spin_hora, 0, wx.ALL, 3)
        pp.Add(wx.StaticText(self.panel_programar, label=_("Minutos:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 3)
        self.spin_min = wx.SpinCtrl(self.panel_programar, min=0, max=59, initial=_inicio.minute, name=_("Minutos (0 a 59)"))
        pp.Add(self.spin_min, 0, wx.ALL, 3)
        self.panel_programar.SetSizer(pp)
        self.panel_programar.Hide()
        vbox.Add(self.panel_programar, 0, wx.EXPAND | wx.ALL, 5)

        # --- BOTONES DE ENVÍO / CANCELACIÓN ---
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_enviar = wx.Button(self, label=_("&Publicar Toot"), name=_("Enviar publicación"))
        self.btn_enviar.Bind(wx.EVT_BUTTON, self._on_enviar)
        self.btn_enviar.SetDefault()
        btn_sizer.Add(self.btn_enviar, 0, wx.ALL, 5)

        self.btn_corregir = wx.Button(self, label=_("Corre&gir ortografía"), name=_("Corregir ortografía del mensaje"))
        self.btn_corregir.Bind(wx.EVT_BUTTON, self._on_corregir_ortografia)
        btn_sizer.Add(self.btn_corregir, 0, wx.ALL, 5)

        # Pasar lo escrito al editor de hilos sin perder nada: útil cuando te has extendido más
        # de lo que permite tu instancia y no quieres cancelar y volver a empezar.
        self.btn_a_hilo = wx.Button(self, label=_("Convertir en &hilo..."),
                                    name=_("Convertir esta publicación en un hilo"))
        self.btn_a_hilo.SetToolTip(_(
            "Cierra este compositor y abre el editor de hilos con lo que llevas escrito: el texto "
            "(ya troceado), la privacidad, el aviso de contenido, el idioma, los adjuntos y las "
            "cuentas mencionadas, que pasan a ser los destinatarios del hilo."))
        self.btn_a_hilo.Bind(wx.EVT_BUTTON, self._on_convertir_en_hilo)
        btn_sizer.Add(self.btn_a_hilo, 0, wx.ALL, 5)

        self.btn_cancel = wx.Button(self, wx.ID_CANCEL, _("&Cancelar"))
        # Cancelar (también Escape, que dispara el botón wx.ID_CANCEL) y la X de la ventana
        # pasan por _on_cancelar: ofrece guardar lo escrito como borrador persistente (N3).
        self.btn_cancel.Bind(wx.EVT_BUTTON, self._on_cancelar)
        self.Bind(wx.EVT_CLOSE, self._on_cancelar)
        btn_sizer.Add(self.btn_cancel, 0, wx.ALL, 5)
        
        vbox.Add(btn_sizer, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        
        self.SetSizer(vbox)
