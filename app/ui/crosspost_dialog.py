# -*- coding: utf-8 -*-
"""Publicación cruzada (cross-posting): un compositor único que publica el MISMO texto y adjuntos
en varias redes a la vez (Mastodon, Bluesky, X), respetando el límite de caracteres de cada una.

Reutiliza los servicios de publicación ya existentes de cada red; no reimplementa nada de la
subida ni el formato. Cada red se publica en su propio hilo de fondo (task_manager), así que la
interfaz nunca se bloquea y un fallo en una red no impide las demás.
"""
import os
import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.core.config_manager import config
from app.core.feature_flags import servicio_habilitado, x_cliente_disponible
from app.ui.accessible_controls import AccessibleTextCtrl

try:
    _
except NameError:
    _ = lambda x: x

# Límite de caracteres por red (aproximado; Mastodon puede ser mayor según instancia, se consulta).
_LIMITES = {"mastodon": 500, "bluesky": 300, "x": 280}


class CrossPostDialog(wx.Dialog):
    def __init__(self, parent, texto_inicial=""):
        super().__init__(parent, title=_("Publicación cruzada (varias redes)"), size=(600, 520),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, name=_("Diálogo de publicación cruzada"))
        self.media_paths = []
        self.alt_texts = []
        self._destinos = self._reunir_destinos()
        self._init_ui(texto_inicial)
        self.CenterOnParent()

    def _reunir_destinos(self):
        """Lista de (red, account_id, etiqueta) con TODAS las cuentas activas de las redes que:
        (a) están HABILITADAS (Preferencias / kill-switch de X) y (b) permiten publicar texto.
        Se recorren TODAS las cuentas de cada red (puedes tener varias de Mastodon, Bluesky o X);
        cada una es un destino independiente. RSS y Telegram no entran (no publican estados)."""
        destinos = []
        # (red, etiqueta de red). Solo las habilitadas; X pasa por servicio_habilitado, que aplica
        # el kill-switch + la preferencia del usuario + que la librería esté instalada.
        redes = [("mastodon", _("Mastodon")), ("bluesky", _("Bluesky")), ("x", _("X"))]
        for red, nombre_red in redes:
            if not servicio_habilitado(red):
                continue
            for aid, name in db.query(
                    "SELECT id, name FROM accounts WHERE service_id=? AND active=1 ORDER BY position, id",
                    (red,)):
                destinos.append((red, aid, _("{0}: {1}").format(nombre_red, name)))
        return destinos

    def _init_ui(self, texto_inicial):
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(self, label=_("Texto a publicar en todas las redes marcadas:")), 0, wx.ALL, 5)
        self.txt = AccessibleTextCtrl(self, value=texto_inicial or "",
                                      style=wx.TE_MULTILINE | wx.TE_RICH2, size=(-1, 160),
                                      name=_("Texto de la publicación cruzada"))
        self.txt.Bind(wx.EVT_TEXT, lambda e: self._actualizar_contador())
        # Ctrl+Mayús+Espacio (la misma tecla que en los demás compositores): aquí, al haber varias
        # redes con límites distintos, dice cuántos caracteres LLEVAS ESCRITOS (y de qué límites te
        # pasas), en vez de los que quedan.
        self.txt.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook_contador)
        s.Add(self.txt, 1, wx.EXPAND | wx.ALL, 5)
        # Insertar traducción y fragmentos también aquí.
        from .traductor_redaccion import instalar_traductor_redaccion
        instalar_traductor_redaccion(self.txt)
        from .snippets import instalar_snippets
        instalar_snippets(self.txt)

        self.lbl_contador = wx.StaticText(self, label="")
        s.Add(self.lbl_contador, 0, wx.LEFT | wx.BOTTOM, 5)

        s.Add(wx.StaticText(self, label=_("Publicar en estas cuentas (marca las que quieras):")), 0, wx.ALL, 5)
        self.checks = []
        if not self._destinos:
            s.Add(wx.StaticText(self, label=_("No hay cuentas de Mastodon, Bluesky o X activas.")), 0, wx.ALL, 10)
        else:
            # Casilla maestra para marcar o desmarcar TODAS de una vez (cómodo si tienes muchas
            # cuentas). No es un destino: solo alterna el resto.
            self.cb_todas = wx.CheckBox(self, label=_("Seleccionar todas las cuentas"), name=_("Seleccionar todas las cuentas"))
            self.cb_todas.Bind(wx.EVT_CHECKBOX, self._on_marcar_todas)
            s.Add(self.cb_todas, 0, wx.LEFT | wx.BOTTOM, 10)

            recordadas = set(config.get("general", "crosspost_ultimas", []) or [])
            for red, aid, etiqueta in self._destinos:
                cb = wx.CheckBox(self, label=etiqueta, name=etiqueta)
                clave = f"{red}:{aid}"
                cb.SetValue(clave in recordadas)
                cb.Bind(wx.EVT_CHECKBOX, lambda e: self._sincronizar_todas())
                s.Add(cb, 0, wx.LEFT, 15)
                self.checks.append((red, aid, cb))
            self._sincronizar_todas()

        # Adjuntos comunes (imágenes). Se envían a todas las redes marcadas.
        fila = wx.BoxSizer(wx.HORIZONTAL)
        b_adj = wx.Button(self, label=_("Añadir &imágenes..."))
        b_adj.Bind(wx.EVT_BUTTON, self._on_adjuntar)
        fila.Add(b_adj, 0, wx.ALL, 5)
        self.lbl_adj = wx.StaticText(self, label=_("Sin imágenes"))
        fila.Add(self.lbl_adj, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        s.Add(fila, 0, wx.EXPAND)

        bs = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_pub = wx.Button(self, wx.ID_OK, _("&Publicar en las redes marcadas"))
        self.btn_pub.Bind(wx.EVT_BUTTON, self._on_publicar)
        bs.Add(self.btn_pub, 0, wx.ALL, 5)
        bs.Add(wx.Button(self, wx.ID_CANCEL, _("C&ancelar")), 0, wx.ALL, 5)
        s.Add(bs, 0, wx.ALIGN_RIGHT | wx.ALL, 5)
        self.SetSizer(s)
        self._actualizar_contador()

    def _on_marcar_todas(self, event):
        """La casilla maestra marca o desmarca todas las cuentas."""
        valor = self.cb_todas.GetValue()
        for _red, _aid, cb in self.checks:
            cb.SetValue(valor)

    def _sincronizar_todas(self):
        """Mantiene la casilla maestra al día: marcada solo si TODAS las cuentas lo están."""
        if not self.checks or not hasattr(self, "cb_todas"):
            return
        todas = all(cb.GetValue() for _r, _a, cb in self.checks)
        self.cb_todas.SetValue(todas)

    def _actualizar_contador(self):
        n = len(self.txt.GetValue())
        # Avisar de las redes que se pasan del límite.
        avisos = [red.capitalize() for red, lim in _LIMITES.items() if n > lim]
        if avisos:
            self.lbl_contador.SetLabel(_("{0} caracteres. Excede el límite de: {1}").format(n, ", ".join(avisos)))
        else:
            self.lbl_contador.SetLabel(_("{0} caracteres.").format(n))

    def _on_char_hook_contador(self, event):
        """Ctrl+Mayús+Espacio: dice por voz cuántos caracteres LLEVAS ESCRITOS (y de qué límites de
        red te pasas). Misma tecla que 'consultar caracteres' del resto de compositores."""
        from app.ui.shortcut_manager import shortcut_manager
        if shortcut_manager.evento_coincide("consultar_caracteres", event):
            n = len(self.txt.GetValue())
            excede = [red.capitalize() for red, lim in _LIMITES.items() if n > lim]
            if excede:
                speak(_("{0} caracteres escritos. Excede el límite de: {1}.").format(n, ", ".join(excede)),
                      interrupt=True)
            else:
                speak(_("{0} caracteres escritos.").format(n), interrupt=True)
            return
        event.Skip()

    def _on_adjuntar(self, event):
        dlg = wx.FileDialog(self, _("Elige imágenes"), wildcard=_("Imágenes|*.jpg;*.jpeg;*.png;*.gif;*.webp"),
                            style=wx.FD_OPEN | wx.FD_MULTIPLE | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.media_paths = dlg.GetPaths()[:4]  # todas las redes admiten hasta 4 imágenes
            self.lbl_adj.SetLabel(_("{0} imágenes").format(len(self.media_paths)) if self.media_paths
                                  else _("Sin imágenes"))
        dlg.Destroy()

    def _on_publicar(self, event):
        # Evitar reentrada: si ya se pulsó Publicar, no volver a hacerlo (doble activación con
        # lector/ratón haría publicar dos veces en TODAS las cuentas).
        if getattr(self, "_publicando", False):
            return
        texto = self.txt.GetValue().strip()
        if not texto and not self.media_paths:
            speak(_("Escribe algo o añade una imagen."), interrupt=True)
            return
        # (red, account_id, etiqueta) de cada cuenta marcada, SIN duplicados (por si acaso
        # apareciera dos veces la misma cuenta: se publica una sola vez en cada una).
        elegidos, vistos = [], set()
        for red, aid, cb in self.checks:
            if cb.GetValue() and (red, aid) not in vistos:
                vistos.add((red, aid))
                elegidos.append((red, aid, cb.GetLabel()))
        if not elegidos:
            speak(_("Marca al menos una cuenta."), interrupt=True)
            return
        # Avisar (una vez) si el texto excede el límite de alguna red marcada; el usuario decide.
        n = len(texto)
        excede = sorted({red for red, _a, _e in elegidos if n > _LIMITES.get(red, 99999)})
        if excede:
            from .accessible_controls import confirmar
            if confirmar(self, _("El texto supera el límite de {0}. Puede que se rechace o se recorte en "
                                 "esas redes. ¿Publicar de todas formas?").format(", ".join(r.capitalize() for r in excede)),
                         _("Publicación cruzada")) != wx.ID_YES:
                return
        # Recordar la selección para la próxima vez.
        config.set("general", "crosspost_ultimas", [f"{red}:{aid}" for red, aid, _e in elegidos])

        self._publicando = True
        try:
            self.btn_pub.Disable()
        except Exception:
            pass
        # Clave de idempotencia ESTABLE para esta publicación (una por cuenta): si una red reintenta
        # la petición (límite de tasa, corte transitorio en una instancia pequeña), el servidor no
        # crea un toot duplicado. Antes cada intento generaba su propia clave.
        import uuid as _uuid
        base_idem = _uuid.uuid4().hex

        # Estado del conteo en un DICT capturado por los callbacks (NO en self): el diálogo se
        # cierra enseguida con EndModal y se destruye, así que el estado no debe vivir en el objeto
        # wx. Los callbacks llegan después, en el hilo de la interfaz (task_manager usa CallAfter),
        # uno tras otro, sin condición de carrera.
        estado = {"total": len(elegidos), "hechas": 0, "errores": []}
        speak(_("Publicando en {0} cuentas...").format(estado["total"]), interrupt=True)
        media = self.media_paths or None
        alts = self.alt_texts or None
        for red, aid, etiqueta in elegidos:
            self._publicar_en(red, aid, texto, media, alts, etiqueta, estado,
                              idem=f"{base_idem}:{red}:{aid}")
        self.EndModal(wx.ID_OK)

    @staticmethod
    def _publicar_en(red, account_id, texto, media, alts, etiqueta, estado, idem=None):
        """Publica en UNA cuenta y actualiza el conteo. IMPORTANTE: cada red invoca su callback con
        una firma distinta, así que se adapta por red:
          - Mastodon y Bluesky: callback(exito_bool, mensaje) — el 2.º arg SIEMPRE lleva texto
            (el toot/URI en éxito), por eso NO sirve mirar si el 2.º arg es "verdadero".
          - X: callback(resultado, error) — éxito = error is None (firma estándar de task_manager).
        Cuando una cuenta falla, se guarda el MOTIVO para el aviso final (así "no publicó en la
        segunda de Mastodon" deja de ser opaco y se ve por qué: token caducado, límite, etc.)."""
        def _terminado(ok, motivo=None):
            estado["hechas"] += 1
            if not ok:
                if motivo:
                    estado["errores"].append(_("{0} ({1})").format(etiqueta, str(motivo)[:120]))
                else:
                    estado["errores"].append(etiqueta)
            if estado["hechas"] >= estado["total"]:
                if not estado["errores"]:
                    notificar(_("Publicado en todas las redes."))
                else:
                    notificar(_("Publicado, pero falló en: {0}").format("; ".join(estado["errores"])))
        try:
            if red == "mastodon":
                # publicar_toot_async es un staticmethod de la CLASE MastodonComposer (no está en
                # la fachada mastodon_service), igual que lo llama el compositor normal.
                from app.services.mastodon.mastodon_composer import MastodonComposer
                MastodonComposer.publicar_toot_async(
                    account_id, texto, media_paths=media, alt_texts=alts,
                    idempotency_key=idem,
                    callback=lambda ok, msg: _terminado(bool(ok), None if ok else msg))
            elif red == "bluesky":
                from app.services.bluesky import bluesky_service
                bluesky_service.publicar_post_async(
                    account_id, texto, media_paths=media, alt_texts=alts,
                    callback=lambda ok, msg: _terminado(bool(ok), None if ok else msg))
            elif red == "x":
                from app.services.x.x_service import x_service
                x_service.publicar_async(
                    account_id, texto, media_paths=media, alt_texts=alts,
                    callback=lambda _res, err: _terminado(err is None, err))
        except Exception as e:
            _terminado(False, e)
