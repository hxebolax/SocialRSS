# -*- coding: utf-8 -*-
"""Handlers de eventos y lógica de envío del compositor de toots (mixin).
"""
import logging
import wx
import wx.adv

from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.database import db
from app.core.task_manager import task_manager
from app.services.mastodon.mastodon_service import mastodon_service
from app.services.mastodon.mastodon_composer import MastodonComposer
from .accessible_controls import CustomTextEntryDialog, mostrar_info
from .mastodon_compose_helpers import (
    MASTODON_MAX_CHARS,
    longitud_contable_mastodon, EmojiPickerDialog, _ofrecer_borrador_seguridad)


log = logging.getLogger(__name__)

class _ComposeAccionesMixin:
    def _on_corregir_ortografia(self, event):
        """Abre el corrector ortográfico sobre el texto del toot."""
        from app.services import spellcheck
        if not spellcheck.disponible():
            speak(_("La corrección ortográfica no está disponible."), interrupt=True)
            return
        from .spellcheck_dialog import SpellCheckDialog
        dlg = SpellCheckDialog(self, self.txt_cuerpo.GetValue())
        if dlg.ShowModal() == wx.ID_OK:
            self.txt_cuerpo.SetValue(dlg.get_texto())
            notificar(_("Corrección aplicada."))
        dlg.Destroy()

    def _on_cuerpo_key(self, event):
        """Ctrl+Espacio: autocompletar @mención. Ctrl+Mayús+E: insertar emoji personalizado.
        Ctrl+Mayús+Espacio: consultar/anunciar los caracteres restantes (útil con el contador en vivo
        desactivado)."""
        from .shortcut_manager import coincide as _atajo_coincide
        mod = event.GetModifiers()
        key = event.GetKeyCode()
        if mod == wx.ACCEL_CTRL and key == wx.WXK_SPACE:
            self._autocompletar_mencion()
        elif _atajo_coincide("consultar_caracteres", event):
            self._recontar_caracteres(anunciar=True)
        elif mod == (wx.ACCEL_CTRL | wx.ACCEL_SHIFT) and key == ord('E'):
            self._on_insertar_emoji(None)
        else:
            event.Skip()

    def _on_insertar_emoji(self, event):
        """Abre el selector accesible de emojis e inserta ':atajo:' donde está el cursor."""
        pos = self.txt_cuerpo.GetInsertionPoint()
        dlg = EmojiPickerDialog(self, self.account_id)
        elegido = dlg.shortcode_elegido if dlg.ShowModal() == wx.ID_OK else None
        dlg.Destroy()
        if elegido:
            self.txt_cuerpo.SetFocus()
            self.txt_cuerpo.SetInsertionPoint(pos)
            self.txt_cuerpo.WriteText(":{0}: ".format(elegido))
            self._recontar_caracteres()
            speak(_("Emoji {0} insertado.").format(elegido), interrupt=True)

    def _autocompletar_mencion(self):
        """Busca el token @parcial antes del cursor y ofrece cuentas conocidas."""
        import re
        texto = self.txt_cuerpo.GetValue()
        pos = self.txt_cuerpo.GetInsertionPoint()
        antes = texto[:pos]
        m = re.search(r"@([\w.]*)$", antes)
        if not m:
            speak(_("Escriba @ y parte del nombre, luego pulse Ctrl+Espacio."), interrupt=True)
            return
        parcial = m.group(1)
        # Buscar cuentas conocidas (autores de toots ya vistos) que coincidan.
        like = "@" + parcial + "%"
        filas = db.query(
            "SELECT DISTINCT author_username, author_name FROM mastodon_statuses "
            "WHERE author_username LIKE ? ORDER BY author_username LIMIT 30",
            (like,)
        )
        opciones = [f"{r[0]} ({r[1]})" if r[1] else r[0] for r in filas]
        accts = [r[0] for r in filas]
        if not opciones:
            speak(_("No hay cuentas conocidas que coincidan con {0}.").format(parcial), interrupt=True)
            return
        dlg = wx.SingleChoiceDialog(self, _("Elija la cuenta a mencionar:"), _("Autocompletar mención"), opciones)
        if dlg.ShowModal() == wx.ID_OK:
            acct = accts[dlg.GetSelection()]  # incluye la @
            # Reemplazar el token @parcial por la mención completa + espacio.
            nuevo_antes = antes[:m.start()] + acct + " "
            nuevo_texto = nuevo_antes + texto[pos:]
            self.txt_cuerpo.SetValue(nuevo_texto)
            self.txt_cuerpo.SetInsertionPoint(len(nuevo_antes))
            speak(_("Mención insertada: {0}").format(acct), interrupt=True)
        dlg.Destroy()

    def _idioma_seleccionado(self) -> str:
        """Código de idioma (ISO) elegido en el compositor para la publicación."""
        idx = self.choice_idioma.GetSelection()
        if 0 <= idx < len(self._idiomas):
            return self._idiomas[idx][0]
        return config.get("general", "language", "es") or "es"

    def _on_toggle_cw(self, event):
        active = self.chk_cw.IsChecked()
        self.txt_cw.Enable(active)
        if active:
            self.txt_cw.SetFocus()
            speak(_("Advertencia de contenido activada. Escriba el resumen."), interrupt=True)
        else:
            self.txt_cw.Clear()
            speak(_("Advertencia de contenido desactivada."), interrupt=True)
        # El CW cuenta para el límite: recalcular el contador al activarlo/desactivarlo.
        self._recontar_caracteres()

    def _on_toggle_encuesta(self, event):
        activa = self.chk_encuesta.IsChecked()
        self.panel_encuesta.Show(activa)
        # Regla oficial de Mastodon: una publicación con encuesta NO puede llevar adjuntos (son
        # mutuamente excluyentes). Al activar la encuesta se desactivan los controles de adjuntos
        # y se quitan los que hubiera, para que no haya errores al publicar.
        self._aplicar_exclusion_encuesta_adjuntos(activa)
        self.Layout()

    def _aplicar_exclusion_encuesta_adjuntos(self, encuesta_activa):
        """Aplica la exclusión oficial encuesta/adjuntos. Con la encuesta activa: vacía y
        deshabilita los adjuntos. Sin ella: rehabilita la lista y el botón de añadir (los de
        eliminar/describir/editar se rehabilitan al seleccionar un adjunto)."""
        botones = [getattr(self, n, None) for n in
                   ("btn_add_att", "btn_del_att", "btn_desc_ia", "btn_edit_alt")]
        if encuesta_activa:
            habia = bool(getattr(self, "attachments", None))
            self.attachments = []
            try:
                self.list_attachments.Clear()
            except Exception:
                pass
            for b in botones:
                if b is not None:
                    try: b.Disable()
                    except Exception: pass
            try:
                self.list_attachments.Disable()
            except Exception:
                pass
            if habia:
                speak(_("Una encuesta no puede llevar adjuntos: se han quitado los archivos adjuntos."), interrupt=True)
        else:
            try:
                self.list_attachments.Enable()
            except Exception:
                pass
            if self.btn_add_att is not None:
                try: self.btn_add_att.Enable()
                except Exception: pass

    def _on_toggle_programar(self, event):
        self.panel_programar.Show(self.chk_programar.IsChecked())
        self.Layout()

    def _fecha_programada(self):
        """Devuelve la fecha/hora de programación (datetime con zona) si está activada y es
        válida (al menos 5 minutos en el futuro), o None. Lanza ValueError si es inválida."""
        if not getattr(self, "chk_programar", None) or not self.chk_programar.IsChecked():
            return None
        import datetime as _dt
        wd = self.date_prog.GetValue()
        dt = _dt.datetime(wd.GetYear(), wd.GetMonth() + 1, wd.GetDay(),
                          self.spin_hora.GetValue(), self.spin_min.GetValue(), 0).astimezone()
        if dt <= _dt.datetime.now().astimezone() + _dt.timedelta(minutes=5):
            raise ValueError(_("La fecha de programación debe ser al menos 5 minutos en el futuro."))
        return dt

    def _on_text_change(self, event):
        # Si el usuario desactiva el contador EN VIVO (Preferencias → Accesibilidad), al escribir NO
        # se recalcula ni se toca la etiqueta/título: así no hay NINGÚN trabajo por pulsación ni
        # eventos de accesibilidad que puedan provocar tirones. Puede consultarlo con Ctrl+Mayús+Espacio.
        if not config.get("accessibility", "contador_en_vivo", True):
            return
        # DEBOUNCE: recalcular el contador en CADA tecla era caro y ralentizaba la escritura. Se
        # recalcula tras una breve pausa al dejar de teclear.
        try:
            t = getattr(self, "_contador_timer", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass
        self._contador_timer = wx.CallLater(160, self._recontar_caracteres)

    def _recontar_caracteres(self, anunciar=False):
        """Calcula y muestra los caracteres restantes (cuenta como Mastodon: URLs = 23, menciones =
        solo @usuario, y el CW también cuenta). Se llama con retardo al escribir (ver _on_text_change)
        y de inmediato desde acciones programáticas (insertar emoji, restaurar borrador, CW). Con
        anunciar=True (Ctrl+Mayús+Espacio) además lo dice por voz."""
        if not self:  # el diálogo pudo cerrarse antes de que dispare el temporizador
            return
        cw = self.txt_cw.GetValue() if self.chk_cw.IsChecked() else ""
        chars = longitud_contable_mastodon(self.txt_cuerpo.GetValue(), cw)
        limite = getattr(self, "_max_chars", MASTODON_MAX_CHARS) or MASTODON_MAX_CHARS
        restantes = limite - chars
        self.lbl_contador.SetLabel(str(restantes))

        # Los caracteres restantes también van en la BARRA DE TÍTULO, pero SOLO con el contador EN
        # VIVO activado (y con retardo, para no saturar a NVDA/JAWS al escribir). Si está en modo «a
        # petición», el título NO lleva nunca el contador: solo se anuncia por voz con la combinación.
        if config.get("accessibility", "contador_en_vivo", True):
            self._programar_titulo_contador(restantes)

        if restantes < 0:
            self.lbl_contador.SetForegroundColour(wx.Colour(255, 0, 0))
        else:
            self.lbl_contador.SetForegroundColour(wx.NullColour)

        if anunciar:
            speak(_("{0} caracteres restantes.").format(restantes), interrupt=True)

    def _programar_titulo_contador(self, restantes):
        """Actualiza el contador de la barra de título con un retardo (debounce) para no machacar
        a NVDA/JAWS con un cambio de título en cada tecla, que ralentizaba la escritura."""
        try:
            t = getattr(self, "_titulo_timer", None)
            if t is not None and t.IsRunning():
                t.Stop()
        except Exception:
            pass
        self._titulo_timer = wx.CallLater(
            500, lambda: self.SetTitle(_("{0} ({1} caracteres restantes)").format(self._titulo_base, restantes)))

    # Posiciones con nombre del PUNTO FOCAL (auditoría N5). Coordenadas de la API de
    # Mastodon: x e y en [-1, 1], con y positiva hacia ARRIBA (el centro es 0,0).
    FOCOS = [
        (_("Centro"), (0.0, 0.0)), (_("Arriba"), (0.0, 1.0)), (_("Abajo"), (0.0, -1.0)),
        (_("Izquierda"), (-1.0, 0.0)), (_("Derecha"), (1.0, 0.0)),
        (_("Arriba a la izquierda"), (-1.0, 1.0)), (_("Arriba a la derecha"), (1.0, 1.0)),
        (_("Abajo a la izquierda"), (-1.0, -1.0)), (_("Abajo a la derecha"), (1.0, -1.0)),
    ]

    def _nombre_foco(self, focus):
        """Nombre legible de unas coordenadas de punto focal, o '' si no hay."""
        if not focus:
            return ""
        try:
            x, y = float(focus[0]), float(focus[1])
        except (TypeError, ValueError, IndexError):
            return ""
        for nombre, (fx, fy) in self.FOCOS:
            if abs(x - fx) < 0.01 and abs(y - fy) < 0.01:
                return nombre
        return _("personalizado")

    def _on_punto_focal(self, event):
        """Elige la zona de la imagen que quedará visible cuando los clientes visuales la
        recorten (miniaturas). Solo aplica a imágenes; se envía como 'focus' de Mastodon."""
        idx = self.list_attachments.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        att = self.attachments[idx]
        nombres = [n for n, _c in self.FOCOS]
        dlg = wx.SingleChoiceDialog(
            self, _("¿Qué zona de la imagen debe quedar visible cuando se recorte en "
                    "miniaturas? (Centro es el comportamiento normal.)"),
            _("Punto focal de la imagen"), nombres)
        actual = self._nombre_foco(att.get("focus"))
        if actual in nombres:
            dlg.SetSelection(nombres.index(actual))
        if dlg.ShowModal() == wx.ID_OK:
            nombre, coords = self.FOCOS[dlg.GetSelection()]
            att["focus"] = list(coords)
            self._update_attachments_list()
            speak(_("Punto focal: {0}.").format(nombre), interrupt=True)
        dlg.Destroy()

    def _on_attachment_selected(self, event):
        idx = self.list_attachments.GetSelection()
        has_selection = (idx != wx.NOT_FOUND)
        self.btn_del_att.Enable(has_selection)
        self.btn_desc_ia.Enable(has_selection)
        self.btn_edit_alt.Enable(has_selection)
        # El punto focal solo tiene sentido en imágenes.
        es_imagen = has_selection and self._tipo_adjunto(self.attachments[idx]) == "image"
        self.btn_focal.Enable(es_imagen)

    def _update_attachments_list(self):
        import os
        self.list_attachments.Clear()
        for att in self.attachments:
            if att.get("existing"):
                # Adjunto ya publicado (en edición): no tiene ruta local.
                name = att.get("nombre") or att.get("tipo") or _("Adjunto")
            else:
                name = os.path.basename(att.get("path", ""))
            desc = att["alt"] if att.get("alt") else _("Sin descripción")
            foco = self._nombre_foco(att.get("focus"))
            extra = _(" - punto focal: {0}").format(foco) if foco and foco != _("Centro") else ""
            self.list_attachments.Append(f"{name} - [{desc}]{extra}")

        # Desactivar botones si no hay selección
        self.btn_del_att.Disable()
        self.btn_desc_ia.Disable()
        self.btn_edit_alt.Disable()
        self.btn_focal.Disable()

    def _on_add_attachment(self, event):
        # Regla oficial: encuesta y adjuntos son excluyentes. Si hay encuesta activa, no se añaden.
        if getattr(self, "chk_encuesta", None) is not None and self.chk_encuesta.IsChecked():
            speak(_("No se pueden añadir adjuntos mientras la publicación tiene una encuesta."), interrupt=True)
            return
        if len(self.attachments) >= 4:
            speak(_("Máximo 4 adjuntos por toot."), interrupt=True)
            return

        # Filtros del diálogo con las extensiones OFICIALES de Mastodon (por tipo y un "todos").
        def _comodin(exts):
            return ";".join("*{0}".format(e) for e in exts)
        todos = self.EXT_IMAGEN + self.EXT_VIDEO + self.EXT_AUDIO
        wildcard = "|".join([
            "{0} ({1})|{1}".format(_("Archivos multimedia admitidos"), _comodin(todos)),
            "{0} ({1})|{1}".format(_("Imágenes"), _comodin(self.EXT_IMAGEN)),
            "{0} ({1})|{1}".format(_("Vídeo"), _comodin(self.EXT_VIDEO)),
            "{0} ({1})|{1}".format(_("Audio"), _comodin(self.EXT_AUDIO)),
            "{0} (*.*)|*.*".format(_("Todos los archivos")),
        ])
        dlg = wx.FileDialog(
            self,
            message=_("Seleccionar archivo para adjuntar"),
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST
        )
        if dlg.ShowModal() == wx.ID_OK:
            path = dlg.GetPath()
            tipo = self._tipo_por_extension(path)
            if not tipo:
                speak(_("Ese tipo de archivo no lo admite Mastodon como adjunto."), interrupt=True)
                dlg.Destroy()
                return
            # Aplicar la regla oficial de combinación (audio/vídeo van solos; máximo 4 imágenes).
            ok, motivo = self._puede_anadir(tipo)
            if not ok:
                # El cuadro de aviso ya lo lee el lector; no lo locutamos también (doble lectura).
                mostrar_info(self, motivo, _("No se puede adjuntar"))
                dlg.Destroy()
                return
            self.attachments.append({"path": path, "alt": ""})
            self._update_attachments_list()
            speak(_("Archivo adjuntado con éxito."), interrupt=True)
        dlg.Destroy()

    def _on_del_attachment(self, event):
        idx = self.list_attachments.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        del self.attachments[idx]
        self._update_attachments_list()
        notificar(_("Adjunto eliminado."))

    def _on_edit_alt(self, event):
        idx = self.list_attachments.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        att = self.attachments[idx]
        limite = int(getattr(self, "_max_desc", 1500) or 1500)
        dlg = CustomTextEntryDialog(
            self,
            message=_("Modificar descripción (texto alternativo) de la imagen/archivo "
                      "(máximo {0} caracteres):").format(limite),
            title=_("Editar Texto Alternativo"),
            value=att["alt"],
            multiline=True,
            max_length=limite
        )
        if dlg.ShowModal() == wx.ID_OK:
            # El control ya impide pasarse, pero recortamos por si acaso (alineado con el servidor).
            att["alt"] = dlg.GetValue().strip()[:limite]
            self._update_attachments_list()
            speak(_("Texto alternativo modificado."), interrupt=True)
        dlg.Destroy()

    def _on_desc_ia(self, event):
        idx = self.list_attachments.GetSelection()
        if idx == wx.NOT_FOUND:
            return
        att = self.attachments[idx]
        speak(_("Generando texto alternativo con IA para el archivo seleccionado..."), interrupt=True)
        path = att.get("path")
        url = att.get("url")

        def _bg_alt(ctx):
            from app.services.ai.ai_service import ai_service
            ruta = path
            # Adjunto ya publicado (edición): no hay copia local, descargar la imagen.
            if not ruta and url:
                import requests
                from app.core.paths import get_data_path
                temp_path = get_data_path("temp/alt_edit.jpg")
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                temp_path.write_bytes(requests.get(url, timeout=20, headers=hdrs).content)
                ruta = str(temp_path)
            if not ruta:
                raise RuntimeError(_("No hay imagen disponible para describir."))
            return ai_service.generar_texto_alternativo(ruta)

        def _finish(res, err):
            if err:
                notificar(f"Error al generar alt-text: {err}")
            else:
                limite = int(getattr(self, "_max_desc", 1500) or 1500)
                att["alt"] = (res or "")[:limite]
                self._update_attachments_list()
                speak(f"Alt-text generado e insertado con éxito: {att['alt']}", interrupt=True)

        task_manager.start_task(_bg_alt, callback=_finish, pass_context=True)

    def _imagenes_sin_alt(self):
        """Devuelve los índices de adjuntos de imagen que NO tienen descripción."""
        import os
        exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
        faltan = []
        for i, att in enumerate(self.attachments):
            if not (att.get("alt") or "").strip():
                if att.get("existing"):
                    if (att.get("tipo") or "").lower() == "image":
                        faltan.append(i)
                else:
                    ext = os.path.splitext(att.get("path", ""))[1].lower()
                    if ext in exts:
                        faltan.append(i)
        return faltan

    def _generar_alts_y_publicar(self, indices):
        """Genera con IA el texto alternativo de los adjuntos indicados y, al
        terminar, continúa con la publicación automáticamente."""
        speak(_("Generando descripciones con IA para {0} imagen(es)...").format(len(indices)), interrupt=True)
        self.btn_enviar.Disable()

        def _bg(ctx):
            from app.services.ai.ai_service import ai_service
            for i in indices:
                att = self.attachments[i]
                ruta = att.get("path")
                try:
                    # Adjunto ya publicado (edición): descargar su imagen para describirla.
                    if not ruta and att.get("url"):
                        import requests
                        from app.core.paths import get_data_path
                        temp_path = get_data_path("temp/alt_edit_{0}.jpg".format(i))
                        temp_path.parent.mkdir(parents=True, exist_ok=True)
                        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                        temp_path.write_bytes(requests.get(att["url"], timeout=20, headers=hdrs).content)
                        ruta = str(temp_path)
                    if ruta:
                        limite = int(getattr(self, "_max_desc", 1500) or 1500)
                        att["alt"] = (ai_service.generar_texto_alternativo(ruta) or "")[:limite]
                except Exception as e:
                    log.error(f"No se pudo generar alt-text del adjunto {i}: {e}")
            return True

        def _finish(res, err):
            self.btn_enviar.Enable()
            self._update_attachments_list()
            if err:
                notificar(_("Error al generar descripciones: {0}").format(err))
                return
            speak(_("Descripciones generadas. Publicando..."), interrupt=True)
            self._continuar_envio()

        task_manager.start_task(_bg, callback=_finish, pass_context=True)

    # --- CONVERTIR LO ESCRITO EN UN HILO ---
    def _excede_limite(self) -> int:
        """Caracteres que sobran respecto al límite de la instancia (0 o menos si cabe). Se mide
        como lo hace Mastodon (URLs=23, menciones sin dominio, el aviso de contenido cuenta)."""
        cw = self.txt_cw.GetValue() if self.chk_cw.IsChecked() else ""
        limite = getattr(self, "_max_chars", MASTODON_MAX_CHARS) or MASTODON_MAX_CHARS
        return longitud_contable_mastodon(self.txt_cuerpo.GetValue(), cw) - limite

    def _estado_para_hilo(self) -> dict:
        """Todo lo que se está escribiendo, listo para reabrirlo en el editor de hilos."""
        sel_idx = self.choice_priv.GetSelection()
        return {
            "texto": self.txt_cuerpo.GetValue(),
            "visibilidad": self.priv_keys[sel_idx] if sel_idx != wx.NOT_FOUND else "public",
            "cw": self.txt_cw.GetValue().strip() if self.chk_cw.IsChecked() else "",
            "idioma": self._idioma_seleccionado(),
            "adjuntos": [(a["path"], a.get("alt") or "") for a in self.attachments],
        }

    def _on_convertir_en_hilo(self, event=None):
        """Cierra el compositor y abre el editor de hilos con lo que hubiera escrito.

        El editor se abre con wx.CallAfter, ya después de que termine el bucle modal de este
        diálogo: así se cumple lo pedido (el compositor se cierra y te deja en el editor de
        hilos) sin encadenar dos ventanas modales, que confunde al lector de pantalla."""
        if not self.txt_cuerpo.GetValue().strip():
            notificar(_("Escribe primero el texto que quieres convertir en hilo."))
            return
        datos = self._estado_para_hilo()
        cuenta = self.account_id
        # El editor de hilos cuelga de la ventana principal, no de este diálogo (que va a morir).
        padre = wx.GetApp().GetTopWindow() or self.GetParent()
        # Si venía de un borrador guardado, se descarta: su contenido pasa al hilo.
        if getattr(self, "draft_db_id", None):
            from app.services.mastodon import mastodon_borradores
            mastodon_borradores.borrar_borrador(self.draft_db_id)
            self.draft_db_id = None
        # (EndModal no pasa por _on_cancelar, así que no se ofrece guardar borrador: el
        # contenido no se pierde, se lo lleva el editor de hilos.)
        self.EndModal(wx.ID_CANCEL)

        def _abrir():
            from .thread_editor import ThreadEditorDialog
            dlg = ThreadEditorDialog(padre, cuenta, inicial=datos)
            dlg.ShowModal()
            dlg.Destroy()
        wx.CallAfter(_abrir)
        speak(_("Abriendo el editor de hilos con lo que llevabas escrito."), interrupt=True)

    def _on_enviar(self, event):
        txt = self.txt_cuerpo.GetValue().strip()
        if not txt and not self.attachments:
            notificar(_("No se puede publicar un toot vacío."))
            return

        # Si el texto se pasa del límite de la instancia, el servidor lo rechazaría: se ofrece
        # convertirlo en hilo (lo más útil) en vez de perder lo escrito. Al EDITAR una publicación
        # ya existente no se ofrece: un hilo nuevo no es una edición de la que hay.
        sobran = self._excede_limite()
        limite = getattr(self, "_max_chars", MASTODON_MAX_CHARS) or MASTODON_MAX_CHARS
        if sobran > 0 and self.edit_status_db_id:
            mostrar_info(self, _(
                "La publicación editada tiene {0} caracteres de más: tu instancia admite {1}. "
                "Recórtala para poder guardar los cambios.").format(sobran, limite),
                _("La publicación excede el límite"))
            self.txt_cuerpo.SetFocus()
            return
        if sobran > 0:
            from .accessible_controls import elegir_opcion
            r = elegir_opcion(
                self,
                _("La publicación tiene {0} caracteres de más: tu instancia admite {1} por "
                  "publicación. ¿Qué quieres hacer? (Cancelar vuelve al compositor para "
                  "recortarla.)").format(sobran, limite),
                _("La publicación excede el límite"),
                [_("Convertirla en un &hilo"), _("&Guardarla como borrador")])
            if r is None:
                return
            if r == 0:
                self._on_convertir_en_hilo()
                return
            self._guardar_borrador_persistente()
            self.EndModal(wx.ID_CANCEL)
            return

        # Asistente de texto alternativo: avisar si hay imágenes sin descripción. Con
        # botones EXPLÍCITOS (auditoría §11): antes era Sí/No/Cancelar y había que
        # recordar qué hacía cada uno.
        if config.get("mastodon", "alt_text_reminder", True):
            faltan = self._imagenes_sin_alt()
            if faltan:
                from .accessible_controls import elegir_opcion
                r = elegir_opcion(
                    self,
                    _("Hay {0} imagen(es) sin descripción (texto alternativo), fundamental "
                      "para las personas ciegas. ¿Qué quieres hacer? (Cancelar vuelve al "
                      "compositor para escribirlas a mano.)").format(len(faltan)),
                    _("Imágenes sin descripción"),
                    [_("&Generar descripciones con IA"), _("&Publicar sin descripción")])
                if r is None:
                    return
                if r == 0:
                    self._generar_alts_y_publicar(faltan)
                    return
                # r == 1: continuar publicando igualmente
        self._continuar_envio()

    def _continuar_envio(self):
        txt = self.txt_cuerpo.GetValue().strip()
        if config.get("mastodon", "confirm_post", False):
            dlg = wx.MessageDialog(
                self,
                _("¿Está seguro de publicar este toot?"),
                _("Confirmar toot"),
                wx.YES_NO | wx.ICON_QUESTION
            )
            if dlg.ShowModal() != wx.ID_YES:
                dlg.Destroy()
                return
            dlg.Destroy()

        # Comprobación FINAL de la combinación de adjuntos (regla oficial de Mastodon): evita el
        # error 422 del servidor (p. ej. varios audios, o audio/vídeo mezclado con imágenes).
        # Solo aplica cuando NO hay encuesta (con encuesta no se envían adjuntos).
        sin_encuesta = not (self.chk_encuesta.IsChecked() and
                            len([t for t in self.txt_opciones if t.GetValue().strip()]) >= 2)
        if sin_encuesta and self.attachments:
            ok_comb, motivo = self._validar_combinacion_adjuntos()
            if not ok_comb:
                # El cuadro de aviso ya lo lee el lector; no lo locutamos también (doble lectura).
                mostrar_info(self, motivo, _("No se puede publicar"))
                return

        sel_idx = self.choice_priv.GetSelection()
        priv = self.priv_keys[sel_idx] if sel_idx != wx.NOT_FOUND else "public"
        cw = self.txt_cw.GetValue().strip() if self.chk_cw.IsChecked() else ""

        # Borrador de seguridad: foto de TODO lo que se va a publicar. El diálogo se cerrará ya,
        # así que capturamos también la ventana padre y el contexto (respuesta/cita/edición) para
        # poder reabrir el compositor intacto si el envío falla. El borrador solo se descarta al
        # publicarse correctamente o si el usuario rechaza recuperarlo.
        parent = self.GetParent()
        borrador = self._capturar_borrador()
        acc = self.account_id
        reply_id = self.in_reply_to_id
        quote_id = self.quote_status_id
        edit_id = self.edit_status_db_id

        # --- MODO EDICIÓN: actualizar el toot existente en vez de publicar uno nuevo ---
        if self.edit_status_db_id:
            txt = self.txt_cuerpo.GetValue().strip()
            media_items = list(self.attachments)

            def _cb_edit(success, err):
                # El diálogo YA está cerrado: el callback NO toca sus controles.
                if success:
                    notificar(_("Publicación editada correctamente."))
                else:
                    # Fallo: ofrecer recuperar el borrador de seguridad para reintentar.
                    _ofrecer_borrador_seguridad(parent, acc, borrador, err, edit_status_db_id=edit_id)

            # Cerrar el diálogo de inmediato y guardar en segundo plano (que no espere a ver el
            # resultado, para no quedarte mirando ni pulsar dos veces).
            speak(_("Guardando los cambios..."), interrupt=True)
            mastodon_service.editar_toot_async(
                self.account_id, self.edit_status_db_id, txt,
                spoiler_text=cw if cw else None,
                media_items=media_items, callback=_cb_edit
            )
            self.EndModal(wx.ID_OK)
            return

        # Programación (opcional): valida que sea al menos 5 minutos en el futuro.
        try:
            scheduled_at = self._fecha_programada()
        except ValueError as e:
            # Aviso VISIBLE para que quede claro por qué no se publica y el usuario pueda corregir
            # la fecha/hora. El lector ya lee el cuadro al abrirse, así que no lo locutamos también.
            mostrar_info(self, str(e), _("Programar publicación"))
            return

        programada = bool(scheduled_at)

        es_respuesta = bool(self.in_reply_to_id)

        def _cb(success, msg):
            # El diálogo YA está cerrado: el callback NO toca sus controles.
            if success:
                notificar(_("Publicación programada.") if programada else _("Toot publicado correctamente."))
                # Si fue una RESPUESTA, el contador de respuestas del toot original ya se refrescó en
                # el servicio: repintar YA los contadores de la lista (sin esperar al sondeo).
                if es_respuesta and not programada:
                    try:
                        import wx
                        f = parent
                        while f is not None and not hasattr(f, "_refrescar_contadores_mastodon"):
                            f = f.GetParent() if hasattr(f, "GetParent") else None
                        if f is not None:
                            wx.CallAfter(f._refrescar_contadores_mastodon)
                    except Exception:
                        pass
            else:
                # Fallo: ofrecer recuperar el borrador de seguridad para volver a publicar.
                _ofrecer_borrador_seguridad(parent, acc, borrador, msg,
                                            in_reply_to_id=reply_id, quote_status_id=quote_id)

        # Extraer listas de paths, alt texts y puntos focales (paralelas por índice)
        paths = [att["path"] for att in self.attachments]
        alts = [att["alt"] for att in self.attachments]
        focos = [att.get("focus") for att in self.attachments]

        # Encuesta (si está activada y tiene al menos 2 opciones)
        poll_opts = None
        poll_exp = 86400
        poll_mult = False
        if self.chk_encuesta.IsChecked():
            opciones = [t.GetValue().strip() for t in self.txt_opciones if t.GetValue().strip()]
            if len(opciones) >= 2:
                poll_opts = opciones
                poll_exp = self._duraciones[self.choice_duracion.GetSelection()][1]
                poll_mult = self.chk_multiple.IsChecked()
                paths = []  # Mastodon no permite encuesta + adjuntos
            else:
                speak(_("La encuesta necesita al menos 2 opciones; se publicará sin encuesta."), interrupt=True)

        # Avisar y CERRAR el diálogo ya: la subida y publicación siguen en segundo plano. Así no
        # te quedas esperando a ver si se publica ni puedes pulsar "Publicar" dos veces.
        speak(_("Programando publicación...") if programada else _("Enviando publicación..."), interrupt=True)

        # Si venía de un borrador persistente, eliminarlo: la publicación ya está en camino
        # (si falla, el borrador de seguridad ofrece volver a guardarlo).
        if getattr(self, "draft_db_id", None):
            from app.services.mastodon import mastodon_borradores
            mastodon_borradores.borrar_borrador(self.draft_db_id)

        MastodonComposer.publicar_toot_async(
            self.account_id,
            txt,
            visibility=priv,
            sensitive=(len(cw) > 0),
            spoiler_text=cw if cw else None,
            media_paths=paths if paths else None,
            alt_texts=alts if alts else None,
            focus_points=focos if any(focos) else None,
            poll_options=poll_opts,
            poll_expires_in=poll_exp,
            poll_multiple=poll_mult,
            scheduled_at=scheduled_at,
            in_reply_to_id=self.in_reply_to_id,
            quoted_status_id=self.quote_status_id,
            language=self._idioma_seleccionado(),
            callback=_cb,
            quote_approval_policy=self._politica_cita_seleccionada(),
        )
        self.EndModal(wx.ID_OK)

    def _politica_cita_seleccionada(self):
        """Política de citas elegida en el compositor (None = la de la cuenta)."""
        ctrl = getattr(self, "choice_citas", None)
        if ctrl is None:
            return None
        sel = ctrl.GetSelection()
        politicas = getattr(self, "_politicas_cita", [None])
        return politicas[sel] if 0 <= sel < len(politicas) else None
