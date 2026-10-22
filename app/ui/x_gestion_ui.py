# -*- coding: utf-8 -*-
"""Acciones ampliadas de X en la interfaz (mixin de XController): votar en encuestas,
comunidades (buscar, entrar, salir, abrir como buffer), buscar cuentas, gestionar listas y
tendencias de un país. Separado de x_controller.py para mantener los archivos pequeños."""
import builtins
if not hasattr(builtins, "_"):
    builtins._ = lambda s: s

import wx

from app.core.database import db
from app.core.speech import speak, notificar
from app.services.x import x_service
from .accessible_controls import mostrar_info, confirmar, CustomTextEntryDialog
from .x_lectura import datos_encuesta_x


def _pedir_texto(parent, mensaje, titulo, valor=""):
    dlg = CustomTextEntryDialog(parent, mensaje, titulo)
    if valor:
        try:
            dlg.SetValue(valor)
        except Exception:
            pass
    texto = dlg.GetValue().strip() if dlg.ShowModal() == wx.ID_OK else None
    dlg.Destroy()
    return texto


def _elegir(parent, mensaje, titulo, opciones):
    """Índice elegido en una lista accesible, o None."""
    if not opciones:
        return None
    dlg = wx.SingleChoiceDialog(parent, mensaje, titulo, opciones)
    idx = dlg.GetSelection() if dlg.ShowModal() == wx.ID_OK else None
    dlg.Destroy()
    return idx


class _XGestionUIMixin:
    # ------------------------------------------------------------------ encuestas
    def votar_encuesta(self, db_id):
        fila = db.query_one("SELECT tweet_id, COALESCE(poll_json,'') FROM x_tweets WHERE id = ?", (db_id,))
        if not fila:
            return
        tid, poll_json = fila
        datos = datos_encuesta_x(poll_json)
        if not datos:
            speak(_("Esta publicación no tiene encuesta."), interrupt=True)
            return
        if datos.get("final"):
            speak(_("La encuesta ya está cerrada."), interrupt=True)
            return
        opciones = [c.get("label", "") for c in datos.get("choices", [])]
        idx = _elegir(self.f, _("Elige tu voto:"), _("Votar en la encuesta"), opciones)
        if idx is None:
            return
        numero = str(datos["choices"][idx].get("number") or idx + 1)

        def _cb(res, err):
            if err:
                notificar(_("No se pudo votar: {0}").format(err))
                return
            notificar(_("Voto registrado: {0}.").format(opciones[idx]))
            db.execute("UPDATE x_tweets SET poll_json = json_set(poll_json, '$.selected', ?) "
                       "WHERE id = ?", (numero, db_id))
        x_service.votar_encuesta_async(self._cuenta(), tid, numero, callback=_cb, poll_json=poll_json)

    # ------------------------------------------------------------------ comunidades
    def buscar_comunidades(self):
        acc = self._cuenta()
        if not acc:
            return
        q = _pedir_texto(self.f, _("Buscar comunidades de X:"), _("Buscar comunidades"))
        if not q:
            return
        speak(_("Buscando comunidades..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("No se encontraron comunidades."), _("Comunidades"))
                return
            opciones = [_("{0}. {1} miembros{2}").format(
                c["name"], c["member_count"], _(", eres miembro") if c["is_member"] else "")
                for c in res]
            idx = _elegir(self.f, _("Elige una comunidad:"), _("Comunidades de X"), opciones)
            if idx is not None:
                self._menu_comunidad(res[idx])
        x_service.buscar_comunidades_async(acc, q, callback=_cb)

    def _menu_comunidad(self, c):
        acciones = [(_("Abrir sus publicaciones como buffer"),
                     lambda: self.abrir_comunidad_como_buffer(c["id"], c["name"]))]
        if c.get("is_member"):
            acciones.append((_("Salir de la comunidad"), lambda: self.comunidad(c["id"], "leave")))
        else:
            acciones.append((_("Unirse a la comunidad"), lambda: self.comunidad(c["id"], "join")))
        acciones.append((_("Ver la descripción"), lambda: self.f._mostrar_texto_largo(
            c["name"], _("{0} miembros.\n\n{1}").format(c["member_count"], c.get("description") or ""))))
        idx = _elegir(self.f, c["name"], _("Comunidad"), [a for a, _f in acciones])
        if idx is not None:
            acciones[idx][1]()

    def comunidad(self, community_id, accion):
        """accion: 'join' | 'leave'."""
        if accion == "leave" and not confirmar(self.f, _("¿Salir de esta comunidad?"),
                                               _("Salir de la comunidad")):
            return

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
            else:
                notificar(_("Te has unido a la comunidad.") if accion == "join"
                          else _("Has salido de la comunidad."))
        x_service.comunidad_async(self._cuenta(), str(community_id), accion, callback=_cb)

    # ------------------------------------------------------------------ cuentas
    def buscar_usuarios(self):
        acc = self._cuenta()
        if not acc:
            return
        q = _pedir_texto(self.f, _("Buscar cuentas de X (nombre o @usuario):"), _("Buscar cuentas"))
        if not q:
            return
        speak(_("Buscando cuentas..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("No se encontraron cuentas."), _("Buscar cuentas"))
                return
            opciones = [_("{0} (@{1}). {2} seguidores. {3}").format(
                u.get("name", ""), u.get("screen_name", ""), u.get("followers", 0),
                (u.get("description") or "")[:120]) for u in res]
            idx = _elegir(self.f, _("Elige una cuenta:"), _("Cuentas de X"), opciones)
            if idx is not None:
                self._menu_cuenta(res[idx])
        x_service.buscar_usuarios_async(acc, q, callback=_cb)

    def _menu_cuenta(self, u):
        screen, uid = u.get("screen_name", ""), str(u.get("user_id", ""))
        acciones = [
            (_("Ver perfil"), lambda: self.ver_perfil(screen)),
            (_("Abrir su cronología"), lambda: self.crear_cronologia_por_nombre(screen)),
            (_("Seguir"), lambda: self.accion_cuenta(uid, "follow")),
            (_("Dejar de seguir"), lambda: self.accion_cuenta(uid, "unfollow")),
            (_("Añadir a una lista..."), lambda: self._anadir_a_lista(uid, screen)),
            (_("Silenciar"), lambda: self.accion_cuenta(uid, "mute")),
            (_("Bloquear"), lambda: self.accion_cuenta(uid, "block")),
        ]
        idx = _elegir(self.f, "@" + screen, _("Cuenta de X"), [a for a, _f in acciones])
        if idx is not None:
            acciones[idx][1]()

    # ------------------------------------------------------------------ listas
    def _mis_listas(self):
        return db.query("SELECT list_id, name, COALESCE(member_count,0) FROM x_lists "
                        "WHERE account_id = ? ORDER BY position", (self._cuenta(),))

    def gestionar_listas(self):
        """Crear, renombrar, borrar y ver/quitar miembros de tus listas de X."""
        acc = self._cuenta()
        if not acc:
            return
        listas = self._mis_listas()
        opciones = [_("Crear una lista nueva...")] + [
            _("{0} ({1} miembros)").format(n or lid, m) for lid, n, m in listas]
        idx = _elegir(self.f, _("Elige una lista o crea una nueva:"), _("Gestionar listas de X"), opciones)
        if idx is None:
            return
        if idx == 0:
            self._crear_lista()
            return
        lid, nombre, _m = listas[idx - 1]
        acciones = [
            (_("Ver sus miembros (y quitar alguno)"), lambda: self._miembros_lista(lid, nombre)),
            (_("Añadir un miembro..."), lambda: self._anadir_miembro_por_nombre(lid, nombre)),
            (_("Cambiar el nombre o la descripción..."), lambda: self._editar_lista(lid, nombre)),
            (_("Borrar la lista"), lambda: self._borrar_lista(lid, nombre)),
        ]
        i = _elegir(self.f, nombre, _("Lista"), [a for a, _f in acciones])
        if i is not None:
            acciones[i][1]()

    def _tras_cambio_listas(self, mensaje):
        notificar(mensaje)
        self._refrescar_listas()

    def _crear_lista(self):
        nombre = _pedir_texto(self.f, _("Nombre de la lista:"), _("Crear lista"))
        if not nombre:
            return
        descripcion = _pedir_texto(self.f, _("Descripción (opcional):"), _("Crear lista")) or ""
        privada = confirmar(self.f, _("¿Lista privada (solo la ves tú)?"), _("Crear lista"))
        x_service.crear_lista_async(
            self._cuenta(), nombre, descripcion, bool(privada),
            callback=lambda r, e: notificar(_("Error: {0}").format(e)) if e
            else self._tras_cambio_listas(_("Lista creada.")))

    def _editar_lista(self, lid, nombre):
        nuevo = _pedir_texto(self.f, _("Nombre de la lista:"), _("Editar lista"), nombre)
        if nuevo is None:
            return
        descripcion = _pedir_texto(self.f, _("Descripción (vacío = no cambiarla):"), _("Editar lista"))
        x_service.editar_lista_async(
            self._cuenta(), lid, nuevo or None, descripcion or None,
            callback=lambda r, e: notificar(_("Error: {0}").format(e)) if e
            else self._tras_cambio_listas(_("Lista actualizada.")))

    def _borrar_lista(self, lid, nombre):
        if not confirmar(self.f, _("¿Borrar la lista «{0}»? No se puede deshacer.").format(nombre),
                         _("Borrar lista")):
            return
        x_service.borrar_lista_async(
            self._cuenta(), lid,
            callback=lambda r, e: notificar(_("Error: {0}").format(e)) if e
            else self._tras_cambio_listas(_("Lista borrada.")))

    def _miembros_lista(self, lid, nombre):
        speak(_("Cargando los miembros..."), interrupt=True)

        def _cb(res, err):
            if err:
                notificar(_("Error: {0}").format(err))
                return
            if not res:
                mostrar_info(self.f, _("La lista no tiene miembros."), nombre)
                return
            opciones = [f"{u.get('name', '')} (@{u.get('screen_name', '')})" for u in res]
            idx = _elegir(self.f, _("Miembros de «{0}» ({1}). Elige uno para quitarlo o ver su "
                                    "perfil:").format(nombre, len(res)), nombre, opciones)
            if idx is None:
                return
            u = res[idx]
            acciones = [(_("Ver perfil"), lambda: self.ver_perfil(u.get("screen_name"))),
                        (_("Quitar de la lista"), lambda: self._quitar_miembro(lid, u))]
            i = _elegir(self.f, opciones[idx], nombre, [a for a, _f in acciones])
            if i is not None:
                acciones[i][1]()
        x_service.miembros_lista_async(self._cuenta(), lid, callback=_cb)

    def _quitar_miembro(self, lid, u):
        if not confirmar(self.f, _("¿Quitar a @{0} de la lista?").format(u.get("screen_name")),
                         _("Quitar de la lista")):
            return
        x_service.gestionar_miembro_lista_async(
            self._cuenta(), lid, str(u.get("user_id")), False,
            callback=lambda r, e: notificar(_("Error: {0}").format(e) if e
                                            else _("Quitado de la lista.")))

    def _anadir_miembro_por_nombre(self, lid, nombre):
        screen = _pedir_texto(self.f, _("Usuario de X a añadir (con o sin @):"), nombre)
        if not screen:
            return

        def _cb(info, err):
            if err or not info or not info.get("user_id"):
                notificar(_("No se encontró a @{0} en X.").format(screen.lstrip("@")))
                return
            x_service.gestionar_miembro_lista_async(
                self._cuenta(), lid, str(info["user_id"]), True,
                callback=lambda r, e: notificar(_("Error: {0}").format(e) if e
                                                else _("Añadido a la lista.")))
        x_service.resolver_usuario_async(self._cuenta(), screen.lstrip("@"), callback=_cb)

    def _anadir_a_lista(self, uid, screen):
        listas = self._mis_listas()
        if not listas:
            mostrar_info(self.f, _("No tienes listas. Créalas en Gestionar listas."), _("Listas"))
            return
        idx = _elegir(self.f, _("¿A qué lista añades a @{0}?").format(screen), _("Añadir a una lista"),
                      [n or lid for lid, n, _m in listas])
        if idx is None:
            return
        x_service.gestionar_miembro_lista_async(
            self._cuenta(), listas[idx][0], str(uid), True,
            callback=lambda r, e: notificar(_("Error: {0}").format(e) if e
                                            else _("Añadido a la lista.")))

    # ------------------------------------------------------------------ tendencias por país
    def tendencias_de_un_lugar(self):
        acc = self._cuenta()
        if not acc:
            return
        speak(_("Cargando los lugares..."), interrupt=True)

        def _cb_lugares(lugares, err):
            if err or not lugares:
                notificar(_("No se pudieron cargar los lugares: {0}").format(err or ""))
                return
            opciones = [(l["name"] if l["name"] == l["country"] or not l["country"]
                         else f"{l['name']} ({l['country']})") for l in lugares]
            idx = _elegir(self.f, _("Elige un país o una ciudad:"), _("Tendencias de un lugar"), opciones)
            if idx is None:
                return
            lugar = lugares[idx]

            def _cb(res, err2):
                if err2 or not res:
                    notificar(_("No hay tendencias para ese lugar."))
                    return
                self._elegir_tendencia(res, _("Tendencias en {0}").format(opciones[idx]))
            x_service.tendencias_lugar_async(acc, lugar["woeid"], callback=_cb)
        x_service.lugares_tendencias_async(acc, callback=_cb_lugares)

    def _elegir_tendencia(self, res, titulo):
        opciones = [(f"{t['name']} ({t['count']})" if t.get("count") else t["name"]) for t in res]
        idx = _elegir(self.f, _("Elige una tendencia para guardarla como búsqueda:"), titulo, opciones)
        if idx is None:
            return
        termino = res[idx]["name"]
        db.execute_insert("INSERT INTO x_searches (account_id, name, query, position) VALUES (?,?,?,0)",
                          (self._cuenta(), termino, termino))
        self.f._cargar_navegacion()
        notificar(_("Búsqueda de la tendencia guardada como buffer."))
