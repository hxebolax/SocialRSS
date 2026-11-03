# -*- coding: utf-8 -*-
"""Diálogo «Compartir» de Telegram: uno o varios mensajes a uno o varios chats, con opciones.

Orden pensado para el teclado y el lector de pantalla:
1. «Buscar chat»: filtra la lista mientras escribes (por nombre o @usuario).
2. «Mostrar»: todos, personas, grupos, canales o bots.
3. Lista de chats con casillas (Espacio marca/desmarca; se pueden marcar varios). Las marcas se
   conservan aunque cambies el filtro o la búsqueda. Arriba van «Mensajes guardados» y los chats
   fijados; luego los más recientes.
4. Tema del foro (solo con UN destino que sea un supergrupo con temas).
5. Cómo compartir: reenviar (se ve el autor original) o enviar como copia (sin autor), quitar los
   pies de foto, álbum completo, comentario propio, envío silencioso y programado.
"""
import logging
import unicodedata

import wx

from app.core.database import db
from app.core.speech import speak
from app.core.task_manager import task_manager
from .accessible_controls import AccessibleCheckListBox, AccessibleTextCtrl

log = logging.getLogger(__name__)

# Clave de destino especial para «Mensajes guardados» (tu propio chat en Telegram).
GUARDADOS = "me"

def _tipo_legible(tipo: str) -> str:
    return {"private": _("persona"), "bot": _("bot"), "group": _("grupo"),
            "supergroup": _("grupo"), "channel": _("canal"),
            "contacto": _("contacto")}.get(tipo, tipo)


def _filtros() -> list:
    """Filtro «Mostrar»: (etiqueta, tipos de chat que entran). Función y no constante: las
    cadenas se traducen al usarse, con el idioma ya cargado."""
    return [(_("Todos"), None),
            (_("Personas"), {"private", "contacto"}),
            (_("Grupos"), {"group", "supergroup"}),
            (_("Canales"), {"channel"}),
            (_("Bots"), {"bot"})]


def _normalizar(texto: str) -> str:
    """Minúsculas y sin tildes, para que «maria» encuentre «María»."""
    texto = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def cargar_destinos(account_id: int, excluir_chat_id=None) -> list:
    """Destinos posibles de la cuenta: «Mensajes guardados», chats (fijados primero, luego los más
    recientes) y contactos con los que aún no hay chat. Cada uno: {clave, chat_id, titulo, tipo,
    usuario}."""
    destinos = [{"clave": GUARDADOS, "chat_id": GUARDADOS, "titulo": _("Mensajes guardados"),
                 "tipo": "private", "usuario": ""}]
    vistos = set()
    try:
        filas = db.query(
            "SELECT chat_id, title, chat_type, username FROM telegram_chats WHERE account_id = ? "
            "ORDER BY pinned DESC, last_message_date DESC, title COLLATE NOCASE", (account_id,))
    except Exception as e:
        log.debug(f"No se pudieron leer los chats para compartir: {e}")
        filas = []
    for chat_id, titulo, tipo, usuario in filas:
        if chat_id in vistos:
            continue
        vistos.add(chat_id)
        destinos.append({"clave": str(chat_id), "chat_id": chat_id,
                         "titulo": titulo or str(chat_id), "tipo": tipo or "private",
                         "usuario": usuario or ""})
    try:
        contactos = db.query(
            "SELECT user_id, nombre, username FROM telegram_contacts WHERE account_id = ? "
            "ORDER BY nombre COLLATE NOCASE", (account_id,))
    except Exception:
        contactos = []
    for user_id, nombre, usuario in contactos:
        if not user_id or user_id in vistos:
            continue
        vistos.add(user_id)
        destinos.append({"clave": str(user_id), "chat_id": user_id,
                         "titulo": nombre or usuario or str(user_id), "tipo": "contacto",
                         "usuario": usuario or ""})
    return destinos


def etiqueta_destino(d: dict) -> str:
    """Texto de la fila: «Nombre, tipo, @usuario»."""
    partes = [d["titulo"]]
    if d["clave"] != GUARDADOS:
        partes.append(_tipo_legible(d["tipo"]))
    if d.get("usuario"):
        partes.append("@" + d["usuario"].lstrip("@"))
    return ", ".join(partes)


def filtrar_destinos(destinos: list, busqueda: str = "", tipos=None) -> list:
    """Destinos que casan con la búsqueda (nombre o @usuario, sin tildes) y el tipo elegido."""
    patron = _normalizar(busqueda.strip().lstrip("@"))
    salida = []
    for d in destinos:
        if tipos is not None and d["clave"] != GUARDADOS and d["tipo"] not in tipos:
            continue
        if tipos is not None and d["clave"] == GUARDADOS and "private" not in tipos:
            continue
        if patron and patron not in _normalizar(d["titulo"]) and patron not in _normalizar(d.get("usuario", "")):
            continue
        salida.append(d)
    return salida


class CompartirTelegramDialog(wx.Dialog):
    """Elige destinos y opciones. Tras Compartir, `obtener()` devuelve un dict con todo."""

    def __init__(self, parent, account_id: int, origen_titulo: str, n_mensajes: int,
                 hay_album: bool = False):
        titulo = _("Compartir mensaje") if n_mensajes == 1 else _("Compartir {0} mensajes").format(n_mensajes)
        super().__init__(parent, title=titulo, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
                         name=titulo)
        self.account_id = account_id
        self._destinos = cargar_destinos(account_id)
        self._por_clave = {d["clave"]: d for d in self._destinos}
        self._marcados = []          # claves, en el orden en que se marcaron
        self._visibles = []          # destinos mostrados ahora en la lista
        self._temas = []             # [(id, título)] del foro elegido
        self._temas_de = None        # clave del destino cuyos temas están cargados
        self._resultado = None

        s = wx.BoxSizer(wx.VERTICAL)
        if n_mensajes == 1:
            resumen = _("Vas a compartir un mensaje de «{0}».").format(origen_titulo)
        else:
            resumen = _("Vas a compartir {0} mensajes de «{1}».").format(n_mensajes, origen_titulo)
        s.Add(wx.StaticText(self, label=resumen), 0, wx.ALL, 8)

        fila = wx.BoxSizer(wx.HORIZONTAL)
        fila.Add(wx.StaticText(self, label=_("&Buscar chat:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.txt_buscar = AccessibleTextCtrl(
            self, name=_("Buscar chat"),
            ayuda_texto=_("Escribe parte del nombre o del @usuario para filtrar la lista de chats."))
        fila.Add(self.txt_buscar, 1, wx.RIGHT, 12)
        fila.Add(wx.StaticText(self, label=_("&Mostrar:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.ch_filtro = wx.Choice(self, choices=[f[0] for f in _filtros()], name=_("Mostrar"))
        self.ch_filtro.SetSelection(0)
        fila.Add(self.ch_filtro, 0)
        s.Add(fila, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        s.Add(wx.StaticText(self, label=_("C&hats (Espacio marca o desmarca; puedes elegir varios):")),
              0, wx.LEFT | wx.TOP, 8)
        self.lista = AccessibleCheckListBox(self, name=_("Chats de destino"), size=(-1, 220))
        s.Add(self.lista, 1, wx.EXPAND | wx.ALL, 8)
        self.lbl_elegidos = wx.StaticText(self, label=_("Ningún chat elegido."))
        s.Add(self.lbl_elegidos, 0, wx.LEFT | wx.RIGHT, 8)

        fila_tema = wx.BoxSizer(wx.HORIZONTAL)
        fila_tema.Add(wx.StaticText(self, label=_("&Tema del foro:")), 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.ch_tema = wx.Choice(self, choices=[_("Solo con un grupo con temas elegido")],
                                 name=_("Tema del foro"))
        self.ch_tema.SetSelection(0)
        self.ch_tema.Disable()
        fila_tema.Add(self.ch_tema, 1)
        s.Add(fila_tema, 0, wx.EXPAND | wx.ALL, 8)

        self.rb_modo = wx.RadioBox(
            self, label=_("Cómo compartir"),
            choices=[_("&Reenviar (se ve quién lo escribió)"),
                     _("Enviar como co&pia (sin el autor original)")],
            majorDimension=1, style=wx.RA_SPECIFY_COLS, name=_("Cómo compartir"))
        s.Add(self.rb_modo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.chk_pies = wx.CheckBox(self, label=_("&Quitar los pies de foto y descripciones"))
        self.chk_pies.Disable()
        s.Add(self.chk_pies, 0, wx.ALL, 8)
        self.chk_album = None
        if hay_album:
            self.chk_album = wx.CheckBox(self, label=_("Incluir el álbum comp&leto"))
            self.chk_album.SetValue(True)
            s.Add(self.chk_album, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)

        s.Add(wx.StaticText(self, label=_("Com&entario (opcional, se envía antes de los mensajes):")),
              0, wx.LEFT | wx.RIGHT, 8)
        self.txt_comentario = AccessibleTextCtrl(
            self, style=wx.TE_MULTILINE, size=(-1, 60), name=_("Comentario"),
            ayuda_texto=_("Texto propio que se envía en cada chat justo antes de lo compartido."))
        s.Add(self.txt_comentario, 0, wx.EXPAND | wx.ALL, 8)

        self.chk_silencio = wx.CheckBox(self, label=_("Enviar &sin sonido (no avisa a los destinatarios)"))
        s.Add(self.chk_silencio, 0, wx.LEFT | wx.RIGHT, 8)
        self.chk_programar = wx.CheckBox(self, label=_("Pro&gramar el envío"))
        s.Add(self.chk_programar, 0, wx.ALL, 8)

        from datetime import datetime, timedelta
        inicial = datetime.now() + timedelta(minutes=5)
        from .telegram_busqueda import _CamposFecha
        self._fecha = _CamposFecha(self, s, _("Fecha de envío:"), hoy=inicial.date())
        box = wx.StaticBox(self, label=_("Hora de envío"))
        bs = wx.StaticBoxSizer(box, wx.HORIZONTAL)
        bs.Add(wx.StaticText(box, label=_("Hora:")), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)
        self.hora = wx.SpinCtrl(box, min=0, max=23, initial=inicial.hour, name=_("Hora de envío, hora"))
        bs.Add(self.hora, 0, wx.RIGHT, 6)
        bs.Add(wx.StaticText(box, label=_("Minuto:")), 0, wx.ALIGN_CENTER_VERTICAL)
        self.minuto = wx.SpinCtrl(box, min=0, max=59, initial=inicial.minute, name=_("Minuto"))
        bs.Add(self.minuto, 0)
        s.Add(bs, 0, wx.ALL | wx.EXPAND, 8)
        self._caja_hora = box

        botones = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_compartir = wx.Button(self, wx.ID_OK, _("C&ompartir"))
        self.btn_compartir.SetDefault()
        botones.Add(self.btn_compartir, 0, wx.RIGHT, 8)
        botones.Add(wx.Button(self, wx.ID_CANCEL, _("&Cancelar")), 0)
        s.Add(botones, 0, wx.ALL | wx.ALIGN_RIGHT, 8)

        self.SetSizerAndFit(s)
        self.SetSize((620, 720))
        self.CenterOnParent()

        self.txt_buscar.Bind(wx.EVT_TEXT, lambda e: self._rellenar())
        self.ch_filtro.Bind(wx.EVT_CHOICE, lambda e: self._rellenar())
        self.lista.Bind(wx.EVT_CHECKLISTBOX, self._on_marcar)
        self.rb_modo.Bind(wx.EVT_RADIOBOX, self._on_modo)
        self.chk_programar.Bind(wx.EVT_CHECKBOX, lambda e: self._activar_programar())
        self.Bind(wx.EVT_BUTTON, self._on_compartir, id=wx.ID_OK)
        self._activar_programar()
        self._rellenar()
        self.txt_buscar.SetFocus()

    # ------------------------------------------------------------------ lista de destinos
    def _rellenar(self):
        tipos = _filtros()[max(0, self.ch_filtro.GetSelection())][1]
        self._visibles = filtrar_destinos(self._destinos, self.txt_buscar.GetValue(), tipos)
        self.lista.Freeze()
        try:
            self.lista.Set([etiqueta_destino(d) for d in self._visibles])
            for i, d in enumerate(self._visibles):
                if d["clave"] in self._marcados:
                    self.lista.Check(i, True)
            if self._visibles:
                self.lista.SetSelection(0)
        finally:
            self.lista.Thaw()

    def _on_marcar(self, event):
        event.Skip()  # el control anuncia «marcado / desmarcado»
        i = event.GetSelection()
        if not (0 <= i < len(self._visibles)):
            return
        clave = self._visibles[i]["clave"]
        if self.lista.IsChecked(i):
            if clave not in self._marcados:
                self._marcados.append(clave)
        elif clave in self._marcados:
            self._marcados.remove(clave)
        self._actualizar_elegidos()

    def _actualizar_elegidos(self):
        n = len(self._marcados)
        if n == 0:
            texto = _("Ningún chat elegido.")
        elif n == 1:
            texto = _("Elegido: {0}.").format(self._por_clave[self._marcados[0]]["titulo"])
        else:
            texto = _("{0} chats elegidos.").format(n)
        self.lbl_elegidos.SetLabel(texto)
        self._preparar_temas()

    # ------------------------------------------------------------------ temas de foro
    def _preparar_temas(self):
        """Con UN solo destino que sea supergrupo, carga sus temas (si es un foro) en segundo
        plano; con cualquier otra elección, el selector queda desactivado."""
        unico = self._por_clave[self._marcados[0]] if len(self._marcados) == 1 else None
        if not unico or unico["tipo"] != "supergroup":
            self._temas, self._temas_de = [], None
            self.ch_tema.Set([_("Solo con un grupo con temas elegido")])
            self.ch_tema.SetSelection(0)
            self.ch_tema.Disable()
            return
        if self._temas_de == unico["clave"]:
            return
        self._temas_de = unico["clave"]
        clave = unico["clave"]
        from app.services.telegram.telegram_service import telegram_service

        def _bg(ctx):
            return telegram_service.obtener_temas(self.account_id, unico["chat_id"])

        def _fin(res, err):
            if not self or self._temas_de != clave:
                return
            temas = [(t["id"], t["title"]) for t in (res or []) if t.get("id")] if not err else []
            self._temas = temas
            if temas:
                self.ch_tema.Set([_("Tema general")] + [t for _i, t in temas])
                self.ch_tema.SetSelection(0)
                self.ch_tema.Enable()
                speak(_("Este grupo tiene {0} temas: puedes elegir en cuál compartir.").format(len(temas)),
                      interrupt=False)
            else:
                self.ch_tema.Set([_("Este grupo no tiene temas")])
                self.ch_tema.SetSelection(0)
                self.ch_tema.Disable()
        task_manager.start_task(_bg, callback=_fin, pass_context=True)

    # ------------------------------------------------------------------ opciones
    def _on_modo(self, event):
        copia = self.rb_modo.GetSelection() == 1
        self.chk_pies.Enable(copia)
        if not copia:
            self.chk_pies.SetValue(False)

    def _activar_programar(self):
        activo = self.chk_programar.GetValue()
        for ctrl in (self._fecha.dia, self._fecha.mes, self._fecha.anio, self.hora, self.minuto):
            ctrl.Enable(activo)

    def _on_compartir(self, event):
        from datetime import datetime
        if not self._marcados:
            speak(_("Marca al menos un chat de destino con la barra espaciadora."), interrupt=True)
            self.lista.SetFocus()
            return
        programar = None
        if self.chk_programar.GetValue():
            fecha = self._fecha.valor()
            if fecha is None:
                speak(_("La fecha no es válida."), interrupt=True)
                return
            programar = datetime(fecha.year, fecha.month, fecha.day,
                                 self.hora.GetValue(), self.minuto.GetValue())
            if programar <= datetime.now():
                speak(_("La fecha y hora deben ser futuras. Elige una fecha y hora posteriores a ahora."),
                      interrupt=True)
                return
        tema = None
        if self._temas and self.ch_tema.IsEnabled() and self.ch_tema.GetSelection() > 0:
            tema = self._temas[self.ch_tema.GetSelection() - 1][0]
        destinos = []
        for clave in self._marcados:
            d = self._por_clave[clave]
            destinos.append({"chat_id": d["chat_id"], "titulo": d["titulo"],
                             "tema": tema if len(self._marcados) == 1 else None})
        copia = self.rb_modo.GetSelection() == 1
        self._resultado = {
            "destinos": destinos,
            "ocultar_autor": copia,
            "ocultar_pies": copia and self.chk_pies.GetValue(),
            "album": bool(self.chk_album and self.chk_album.GetValue()),
            "comentario": self.txt_comentario.GetValue().strip(),
            "silencioso": self.chk_silencio.GetValue(),
            "programar": programar,
        }
        self.EndModal(wx.ID_OK)

    def obtener(self):
        """Opciones elegidas (solo tras pulsar Compartir)."""
        return self._resultado
