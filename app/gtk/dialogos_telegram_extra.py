# -*- coding: utf-8 -*-
"""Telegram: álbumes, GIF, stickers y videonotas, copiar mensajes, editar pies, chats en común, saltar a una fecha, privacidad
global, cambio de número e importar contactos. Los mismos servicios que usa la versión de Windows."""

from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from app.core.database import db
from app.core.speech import notificar
from app.services.telegram.telegram_service import telegram_service

from .dialogos import DialogoBase, DialogoElegir, ejecutar
from .dialogos_telegram_admin import _hacer, _pedir
from .dialogos_telegram_cuenta import _archivo
from .dialogos_telegram_mensajes import _area, _lineas
from .dialogos_reddit import _texto_de
from .dialogos_peertube_instancia import texto_en_dialogo


def _varios(padre, titulo: str, al_elegir):
    dlg = Gtk.FileDialog(title=titulo)

    def _fin(origen, res):
        try:
            lista = origen.open_multiple_finish(res)
        except Exception:
            return
        al_elegir([lista.get_item(i).get_path() for i in range(lista.get_n_items())])
    dlg.open_multiple(padre, None, _fin)


# --------------------------------------------------------------------------- enviar
def enviar_album(padre, account_id: int, chat_id: int, titulo: str):
    def _con(rutas):
        if len(rutas) < 2 or len(rutas) > 10:
            return notificar(_("Un álbum necesita entre 2 y 10 fotos o vídeos."))
        _pedir(padre, _("Álbum para {t}").format(t=titulo), _("_Texto del álbum (opcional):"), lambda pie: _hacer(
            padre, lambda: telegram_service.enviar_album(account_id, chat_id, rutas, pie), _("Álbum enviado.")), _("_Enviar"))
    _varios(padre, _("Elegir las fotos o los vídeos del álbum"), _con)


def enviar_gif(padre, account_id: int, chat_id: int, titulo: str):
    def _con(ruta):
        _pedir(padre, _("GIF para {t}").format(t=titulo), _("_Texto (opcional):"), lambda pie: _hacer(
            padre, lambda: telegram_service.enviar_gif(account_id, chat_id, ruta, pie), _("GIF enviado.")), _("_Enviar"))
    _archivo(padre, _("Elegir el GIF o el vídeo corto"), _con)


def enviar_videonota(padre, account_id: int, chat_id: int, titulo: str):
    _archivo(padre, _("Elegir el vídeo cuadrado"), lambda ruta: _hacer(
        padre, lambda: telegram_service.enviar_videonota(account_id, chat_id, ruta), _("Videonota enviada.")))


def enviar_sticker(padre, account_id: int, chat_id: int, titulo: str):
    def _paquete(nombre):
        notificar(_("Buscando el paquete…"))

        def _fin(res, err):
            if err or not res:
                return notificar(_("No se encontró ese paquete: {e}").format(e=err or nombre))
            DialogoElegir(padre, _("Elegir el sticker"), _("_Sticker:"),
                          [f"{i + 1}. {s['emoji'] or _('sin emoji')}" for i, s in enumerate(res)],
                          lambda i: _hacer(padre, lambda: telegram_service.enviar_sticker(account_id, chat_id, res[i]["file_id"]),
                                           _("Sticker enviado.")), _("_Enviar")).present()
        ejecutar(lambda: telegram_service.obtener_stickers(account_id, nombre.lstrip("@")), _fin)
    _pedir(padre, _("Enviar un sticker a {t}").format(t=titulo), _("_Nombre corto del paquete (por ejemplo AnimatedEmojies):"),
           _paquete, _("_Buscar"))


# --------------------------------------------------------------------------- sobre un mensaje
def editar_pie(padre, account_id: int, chat_id: int, message_id: int, tras=None):
    _pedir(padre, _("Editar el texto del archivo"), _("_Nuevo texto:"), lambda t: _hacer(
        padre, lambda: telegram_service.editar_pie_mensaje(account_id, chat_id, message_id, t), _("Texto editado."),
        (lambda _r: tras()) if tras else None), _("_Guardar"))


def _elegir_chat(padre, account_id: int, titulo: str, etiqueta: str, aceptar: str, al_elegir):
    chats = db.query("SELECT chat_id, COALESCE(title, '') FROM telegram_chats WHERE account_id = ? "
                     "ORDER BY pinned DESC, last_message_date DESC LIMIT 100", (account_id,))
    if not chats:
        return notificar(_("Primero carga la lista de chats"))
    DialogoElegir(padre, titulo, etiqueta, [t or str(c) for c, t in chats], lambda i: al_elegir(chats[i][0]), aceptar).present()


def compartir(account_id: int, chat_id: int, message_id: int, destino, ocultar_autor: bool = False,
              album: bool = False):
    """Reenvía (o copia, con `ocultar_autor`) un mensaje —o su álbum entero— a un chat con el servicio
    `compartir_mensajes`. Lanza una excepción con el motivo si Telegram lo rechaza."""
    ids = [message_id]
    if album:
        ids = telegram_service.ids_del_album(account_id, chat_id, ids)
    res = telegram_service.compartir_mensajes(account_id, chat_id, ids, [{"chat_id": destino}],
                                              ocultar_autor=ocultar_autor)
    if not res or not res[0].get("ok"):
        raise RuntimeError(res[0].get("error") if res else _("No se pudo compartir"))
    return res


def copiar_a_otro_chat(padre, account_id: int, chat_id: int, message_id: int):
    """Copia el mensaje a otro chat sin la marca de «reenviado»."""
    _elegir_chat(padre, account_id, _("Copiar el mensaje"), _("_Copiar a:"), _("_Copiar"), lambda destino: _hacer(
        padre, lambda: compartir(account_id, chat_id, message_id, destino, ocultar_autor=True), _("Mensaje copiado.")))


def reenviar_album(padre, account_id: int, chat_id: int, message_id: int):
    _elegir_chat(padre, account_id, _("Reenviar el álbum entero"), _("_Reenviar a:"), _("_Reenviar"), lambda destino: _hacer(
        padre, lambda: compartir(account_id, chat_id, message_id, destino, album=True), _("Álbum reenviado.")))


def leido_hasta_aqui(padre, account_id: int, chat_id: int, message_id: int, tras=None):
    _hacer(padre, lambda: telegram_service.marcar_leido_hasta(account_id, chat_id, message_id, 0),
           _("Marcado como leído hasta este mensaje."), (lambda _r: tras()) if tras else None)


# --------------------------------------------------------------------------- sobre un chat
def saltar_a_fecha(padre, account_id: int, chat_id: int, titulo: str, tras=None):
    def _fecha(texto):
        try:
            f = datetime.strptime(texto, "%Y-%m-%d")
        except ValueError:
            return notificar(_("Escribe la fecha como AAAA-MM-DD."))
        _hacer(padre, lambda: telegram_service.cargar_mensajes_desde_fecha(account_id, chat_id, f, 60),
               _("Mensajes cargados."), (lambda n: (notificar(_("{n} mensajes cargados.").format(n=n)), tras and tras())) if tras else None)
    _pedir(padre, _("Ir a una fecha en {t}").format(t=titulo), _("_Fecha (AAAA-MM-DD):"), _fecha, _("_Cargar"))


def chats_en_comun(padre, account_id: int, chat_id: int, titulo: str):
    if chat_id is None or chat_id < 0:
        return notificar(_("Esto solo vale en la conversación con una persona."))

    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        texto_en_dialogo(padre, _("Chats en común con {t}").format(t=titulo),
                         "\n".join(c["titulo"] or str(c["id"]) for c in res) or _("No tenéis ningún chat en común."))
    ejecutar(lambda: telegram_service.chats_en_comun(account_id, chat_id), _fin)


def historias_del_chat(padre, account_id: int, chat_id: int, titulo: str):
    def _fin(res, err):
        if err:
            return notificar(_("No se pudo: {e}").format(e=err))
        texto_en_dialogo(padre, _("Historias de {t}").format(t=titulo), "\n".join(
            f"{h['fecha']}: {h['caption'] or _('sin texto')} ({h['tipo'] or '?'})" for h in res) or _("No tiene historias activas."))
    ejecutar(lambda: telegram_service.historias_de_chat(account_id, chat_id), _fin)


# --------------------------------------------------------------------------- cuenta
class DialogoPrivacidadGlobalTG(DialogoBase):
    """Privacidad de los chats nuevos y de la fecha de lectura."""

    CLAVES = (("archive_and_mute_new_chats", "Archivar y silenciar los chats nuevos de desconocidos"),
              ("keep_unmuted_chats_archived", "Mantener archivados los chats silenciados"),
              ("keep_chats_from_folders_archived", "Mantener archivados los chats de carpetas"),
              ("show_read_date", "Mostrar a los demás cuándo leo sus mensajes"),
              ("allow_new_chats_from_unknown_users", "Aceptar chats nuevos de desconocidos"))

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Privacidad de los chats"), _("_Guardar"))
        self.account_id, self.casillas = account_id, {}
        for clave, nombre in self.CLAVES:
            self.casillas[clave] = self.campo("", Gtk.CheckButton(label=_(nombre)))
        self.set_focus(self.casillas[self.CLAVES[0][0]])

        def _fin(r, err):
            if err or not r:
                return notificar(_("No se pudieron leer los ajustes: {e}").format(e=err or ""))
            for k, c in self.casillas.items():
                c.set_active(bool(r.get(k)))
        ejecutar(lambda: telegram_service.obtener_privacidad_global(account_id), _fin)

    def aceptar(self):
        valores = {k: c.get_active() for k, c in self.casillas.items()}
        self.close()
        _hacer(self.padre, lambda: telegram_service.establecer_privacidad_global(self.account_id, **valores),
               _("Privacidad guardada."))


class DialogoNumeroTG(DialogoBase):
    """Cambia el número de teléfono: Telegram manda un código al número nuevo."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Cambiar el número de teléfono"), _("_Enviar el código"))
        self.account_id, self.hash = account_id, None
        self.numero = self.campo(_("_Número nuevo (con prefijo, por ejemplo +34600000000):"),
                                 Gtk.Entry(activates_default=True, width_chars=24))
        self.codigo = self.campo(_("_Código recibido:"), Gtk.Entry(activates_default=True, sensitive=False))
        self.set_focus(self.numero)

    def aceptar(self):
        n = self.numero.get_text().strip()
        if not n:
            return
        if self.hash is None:
            def _fin(h, err):
                if err or not h:
                    return notificar(_("No se pudo enviar el código: {e}").format(e=err or ""))
                self.hash = h
                self.numero.set_sensitive(False)
                self.codigo.set_sensitive(True)
                self.boton_aceptar.set_label(_("_Cambiar el número"))
                self.codigo.grab_focus()
                notificar(_("Código enviado. Escríbelo para confirmar el cambio."))
            ejecutar(lambda: telegram_service.enviar_codigo_cambio_numero(self.account_id, n), _fin)
            return
        c = self.codigo.get_text().strip()
        if not c:
            return
        self.close()
        _hacer(self.padre, lambda: telegram_service.confirmar_cambio_numero(self.account_id, n, self.hash, c),
               _("Número cambiado."))


class DialogoImportarContactosTG(DialogoBase):
    """Añade varios contactos de una vez: un teléfono por línea, con nombre y apellido separados por comas."""

    def __init__(self, padre, account_id: int):
        super().__init__(padre, _("Importar contactos"), _("_Importar"))
        self.account_id = account_id
        self.texto = _area(self, _("_Contactos (teléfono, nombre, apellido):"), 180)
        self.set_focus(self.texto)

    def aceptar(self):
        contactos = []
        for linea in _lineas(_texto_de(self.texto)):
            partes = [p.strip() for p in linea.split(",")] + ["", ""]
            if partes[0] and partes[1]:
                contactos.append((partes[0], partes[1], partes[2]))
        if not contactos:
            return notificar(_("Escribe al menos un contacto: teléfono y nombre."))
        self.close()
        _hacer(self.padre, lambda: telegram_service.importar_contactos(self.account_id, contactos), _("Contactos importados."),
               lambda n: notificar(_("{n} contactos importados.").format(n=n)))
