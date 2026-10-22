# -*- coding: utf-8 -*-
"""Riqueza total del cliente XMPP (tanda X9): ficha de contacto, estado enriquecido y utilidades.

Aquí vive todo lo que hace de SocialRSS un cliente XMPP completo al nivel de Gajim/Dino/Conversations,
y siempre de forma accesible (todo en palabras, nunca solo iconos):

  * vCard (XEP-0054): ver la ficha de un contacto y editar la tuya (nombre, apodo, correo, teléfono,
    web, cumpleaños, nota…).
  * Información técnica de un contacto (XEP-0092 versión de cliente, 0202 su hora, 0012 cuánto lleva
    inactivo, 0199 ping/latencia) resumida en palabras.
  * Estado enriquecido por PEP: publicar tu ÁNIMO (XEP-0107) y tu ACTIVIDAD (XEP-0108), y recibir los
    de tus contactos (se guardan y se ven en su ficha).
  * Zumbido / atención (XEP-0224): mandar y recibir un aviso de atención con sonido propio.
  * Presencia invisible (XEP-0186): conectarte sin que te vean disponible.
  * Búsqueda de usuarios (XEP-0055) en el directorio del servidor, si lo ofrece.

Transferencia directa de archivos (IBB 0047 / Jingle 0234) queda como respaldo REGISTRADO pero no
cableada a UI: HTTP Upload (0363, tanda X5) es el camino primario y funciona en la práctica; la
transferencia peer-to-peer se excluye a propósito (como el DCC de IRC y las llamadas de Matrix).
"""
from app.services.xmpp.xmpp_common import (
    Any, Dict, List, Optional, db, log, XMPPError, jid_bare, db_id_chat)

_NS_VCARD = "vcard-temp"

# Ánimos estándar de XEP-0107 (clave del protocolo -> etiqueta en español). Lista práctica, no la
# exhaustiva del XEP.
MOODS = [
    ("happy", "Feliz"), ("sad", "Triste"), ("angry", "Enfadado/a"), ("excited", "Emocionado/a"),
    ("relaxed", "Relajado/a"), ("bored", "Aburrido/a"), ("tired", "Cansado/a"), ("sick", "Enfermo/a"),
    ("in_love", "Enamorado/a"), ("nervous", "Nervioso/a"), ("hungry", "Con hambre"),
    ("calm", "Tranquilo/a"), ("worried", "Preocupado/a"), ("grumpy", "De mal humor"),
    ("curious", "Con curiosidad"), ("surprised", "Sorprendido/a"),
]
# Actividades generales de XEP-0108.
ACTIVIDADES = [
    ("relaxing", "Descansando"), ("working", "Trabajando"), ("eating", "Comiendo"),
    ("traveling", "De viaje"), ("exercising", "Haciendo ejercicio"), ("having_appointment", "En una cita"),
    ("grooming", "Arreglándome"), ("inactive", "Inactivo/a"), ("talking", "Hablando"),
    ("drinking", "Bebiendo algo"),
]
_MOOD_MAP = dict(MOODS)
_ACT_MAP = dict(ACTIVIDADES)


class _PerfilMixin:
    # ---------------- vCard (0054) ----------------
    def ver_vcard(self, account_id: int, jid: str) -> List[tuple]:
        """Ficha (vCard) de un contacto como lista de (etiqueta, valor) en palabras."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _t():
            iq = await cliente["xep_0054"].get_vcard(jid=jid)
            return self._leer_vcard(iq)
        return self._run(_t(), timeout=30.0)

    def mi_vcard_campos(self, account_id: int) -> Dict[str, str]:
        """Mi vCard como diccionario de campos editables (para rellenar el diálogo de edición)."""
        cliente = self._cliente_conectado(account_id)

        async def _t():
            iq = await cliente["xep_0054"].get_vcard()
            return self._vcard_a_dict(iq)
        try:
            return self._run(_t(), timeout=30.0)
        except Exception:
            return {}

    def editar_mi_vcard(self, account_id: int, campos: Dict[str, str]) -> bool:
        """Publica tu vCard con los campos dados (FN, NICKNAME, EMAIL, TEL, URL, BDAY, TITLE, DESC)."""
        cliente = self._cliente_conectado(account_id)

        async def _t():
            from xml.etree import ElementTree as ET
            vcard = cliente["xep_0054"].make_vcard()
            def _sub(tag, texto, hijo=None):
                if not texto:
                    return
                el = ET.SubElement(vcard.xml, "{%s}%s" % (_NS_VCARD, tag))
                if hijo:
                    ET.SubElement(el, "{%s}%s" % (_NS_VCARD, hijo)).text = texto
                else:
                    el.text = texto
            if campos.get("FN"):
                vcard["FN"] = campos["FN"]
            _sub("NICKNAME", campos.get("NICKNAME", ""))
            _sub("URL", campos.get("URL", ""))
            _sub("BDAY", campos.get("BDAY", ""))
            _sub("TITLE", campos.get("TITLE", ""))
            _sub("ROLE", campos.get("ROLE", ""))
            _sub("DESC", campos.get("DESC", ""))
            _sub("EMAIL", campos.get("EMAIL", ""), hijo="USERID")
            _sub("TEL", campos.get("TEL", ""), hijo="NUMBER")
            if campos.get("ORG"):
                el = ET.SubElement(vcard.xml, "{%s}ORG" % _NS_VCARD)
                ET.SubElement(el, "{%s}ORGNAME" % _NS_VCARD).text = campos["ORG"]
            await cliente["xep_0054"].publish_vcard(vcard)
            return True
        self._run(_t(), timeout=30.0)
        return True

    _VCARD_CAMPOS = [
        ("FN", "Nombre"), ("NICKNAME", "Apodo"), ("EMAIL/USERID", "Correo"),
        ("TEL/NUMBER", "Teléfono"), ("URL", "Web"), ("BDAY", "Cumpleaños"),
        ("TITLE", "Cargo"), ("ROLE", "Rol"), ("ORG/ORGNAME", "Organización"), ("DESC", "Nota"),
    ]

    @classmethod
    def _leer_vcard(cls, iq) -> List[tuple]:
        vcard = iq.xml.find("{%s}vCard" % _NS_VCARD)
        if vcard is None:
            return []
        salida = []
        for ruta, etiqueta in cls._VCARD_CAMPOS:
            valor = cls._vcard_texto(vcard, ruta)
            if valor:
                salida.append((_(etiqueta), valor))
        return salida

    @classmethod
    def _vcard_a_dict(cls, iq) -> Dict[str, str]:
        vcard = iq.xml.find("{%s}vCard" % _NS_VCARD)
        d = {}
        if vcard is None:
            return d
        for clave in ("FN", "NICKNAME", "URL", "BDAY", "TITLE", "ROLE", "DESC"):
            d[clave] = cls._vcard_texto(vcard, clave)
        d["EMAIL"] = cls._vcard_texto(vcard, "EMAIL/USERID")
        d["TEL"] = cls._vcard_texto(vcard, "TEL/NUMBER")
        d["ORG"] = cls._vcard_texto(vcard, "ORG/ORGNAME")
        return d

    @staticmethod
    def _vcard_texto(vcard, ruta: str) -> str:
        partes = ruta.split("/")
        nodo = vcard
        for p in partes:
            if nodo is None:
                return ""
            nodo = nodo.find("{%s}%s" % (_NS_VCARD, p))
        return (nodo.text or "").strip() if nodo is not None and nodo.text else ""

    # ---------------- información técnica (0092/0202/0012/0199) ----------------
    def info_tecnica(self, account_id: int, jid: str) -> List[tuple]:
        """Información técnica de un contacto/recurso en palabras: cliente y versión, su hora,
        cuánto lleva inactivo y la latencia (ping). Los que fallen se omiten."""
        cliente = self._cliente_conectado(account_id)
        jid = jid.strip()

        async def _t():
            salida = []
            try:
                iq = await cliente["xep_0092"].get_version(jid)
                v = iq["software_version"]
                cli = v["name"] or ""
                ver = v["version"] or ""
                so = v["os"] or ""
                if cli:
                    salida.append((_("Cliente"), (cli + (" " + ver if ver else "")).strip()))
                if so:
                    salida.append((_("Sistema"), so))
            except Exception:
                pass
            try:
                iq = await cliente["xep_0202"].get_entity_time(jid)
                t = iq["entity_time"]
                hora = t["time"] or t["utc"] or ""
                if hora:
                    salida.append((_("Su hora"), str(hora)))
            except Exception:
                pass
            try:
                iq = await cliente["xep_0012"].get_last_activity(jid)
                seg = iq["last_activity"]["seconds"]
                if seg is not None:
                    salida.append((_("Inactivo desde hace"), self._duracion_legible(int(seg))))
            except Exception:
                pass
            try:
                import time
                lat = await cliente["xep_0199"].ping(jid, timeout=10)
                if lat is not None:
                    salida.append((_("Latencia (ping)"), _("{0} ms").format(int(float(lat) * 1000))))
            except Exception:
                pass
            return salida
        return self._run(_t(), timeout=40.0)

    @staticmethod
    def _duracion_legible(seg: int) -> str:
        if seg < 60:
            return _("{0} segundos").format(seg)
        if seg < 3600:
            return _("{0} minutos").format(seg // 60)
        if seg < 86400:
            return _("{0} horas").format(seg // 3600)
        return _("{0} días").format(seg // 86400)

    # ---------------- PEP: publicar ánimo y actividad (0107/0108) ----------------
    def publicar_animo(self, account_id: int, valor: str, texto: str = "") -> bool:
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0107"].publish_mood(value=valor or None, text=(texto or None))
            return True
        self._run(_t(), timeout=20.0)
        return True

    def limpiar_animo(self, account_id: int) -> bool:
        return self.publicar_animo(account_id, "", "")

    def publicar_actividad(self, account_id: int, general: str, texto: str = "") -> bool:
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0108"].publish_activity(general or "", None, (texto or None))
            return True
        self._run(_t(), timeout=20.0)
        return True

    def limpiar_actividad(self, account_id: int) -> bool:
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0108"].stop()
            return True
        try:
            self._run(_t(), timeout=20.0)
        except Exception:
            pass
        return True

    # ---------------- zumbido / atención (0224) ----------------
    def enviar_zumbido(self, account_id: int, jid: str) -> bool:
        """Envía un zumbido (llamada de atención) a un contacto (XEP-0224)."""
        cliente = self._cliente_conectado(account_id)
        jid = jid_bare(jid)

        async def _t():
            cliente["xep_0224"].request_attention(jid)
            return True
        self._run(_t(), timeout=15.0)
        return True

    # ---------------- presencia invisible (0186) ----------------
    def fijar_invisible(self, account_id: int) -> bool:
        cliente = self._cliente_conectado(account_id)

        async def _t():
            await cliente["xep_0186"].set_invisible()
            return True
        self._run(_t(), timeout=15.0)
        return True

    def fijar_visible(self, account_id: int) -> bool:
        cliente = self._cliente_conectado(account_id)

        async def _t():
            try:
                await cliente["xep_0186"].set_visible()
            except Exception:
                pass
            cliente.send_presence()
            return True
        self._run(_t(), timeout=15.0)
        return True

    # ---------------- búsqueda de usuarios (0055) ----------------
    def formulario_busqueda(self, account_id: int, servicio: str) -> Dict[str, Any]:
        """Pide el formulario de búsqueda de un directorio (0055). Devuelve {campos, instrucciones}."""
        cliente = self._cliente_conectado(account_id)
        servicio = jid_bare(servicio)

        async def _t():
            from slixmpp.plugins.xep_0004 import Form
            iq = cliente.make_iq_get(ito=servicio)
            iq.enable("search")
            resp = await iq.send(timeout=30)
            form = resp["search"]["form"]
            campos = self._campos_de_form(form) if form and form.get_fields() else []
            instrucciones = (form["instructions"] if form else "") or ""
            return {"campos": campos, "instrucciones": instrucciones,
                    "es_form": bool(campos)}
        return self._run(_t(), timeout=35.0)

    def ejecutar_busqueda(self, account_id: int, servicio: str,
                          respuestas: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
        """Ejecuta la búsqueda y devuelve las filas encontradas [{jid, nombre, ...}]."""
        cliente = self._cliente_conectado(account_id)
        servicio = jid_bare(servicio)

        async def _t():
            form = cliente["xep_0004"].make_form(ftype="submit")
            for var, info in (respuestas or {}).items():
                try:
                    form.add_field(var=var, ftype=info.get("type"), value=info.get("value"))
                except Exception:
                    form.add_field(var=var, value=info.get("value"))
            iq = cliente.make_iq_set(ito=servicio)
            iq.enable("search")
            iq["search"].append(form)
            resp = await iq.send(timeout=45)
            rform = resp["search"]["form"]
            filas = []
            if rform:
                for item in (rform["items"] or []):
                    fila = {k: (", ".join(v) if isinstance(v, (list, tuple)) else str(v))
                            for k, v in item.items()}
                    filas.append(fila)
            return filas
        return self._run(_t(), timeout=50.0)
