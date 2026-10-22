# -*- coding: utf-8 -*-
"""Estado de una cuenta de Mastodon (mudada, suspendida, limitada, en memoria, identificador no
verificable de 4.7...) y campos de su perfil, como texto accesible. Funciones puras: se usan en la
ficha de la cuenta y se prueban sin red."""


def describir_estado_cuenta(c) -> list:
    """Avisos sobre el ESTADO de una cuenta de Mastodon, en español y listos para leer: se ha
    mudado, suspendida, limitada, en memoria, privada, grupo e identificador no verificable
    (4.7). La ficha de la cuenta no decía nada de esto y, por ejemplo, podías seguir a una
    cuenta abandonada sin saber que su dueño se había mudado a otra."""
    if not c:
        return []
    avisos = []
    movida = c.get("moved")
    if movida:
        destino = movida.get("acct") if isinstance(movida, dict) else str(movida)
        avisos.append(_("Se ha mudado a @{0}: ya no publica aquí.").format(destino))
    if c.get("suspended"):
        avisos.append(_("Cuenta suspendida o no disponible."))
    if c.get("limited"):
        avisos.append(_("Cuenta limitada por la moderación de tu servidor."))
    if c.get("memorial"):
        avisos.append(_("Cuenta en memoria de una persona fallecida."))
    if c.get("locked"):
        avisos.append(_("Cuenta privada: aprueba a mano a quien la sigue."))
    if c.get("group"):
        avisos.append(_("Es un grupo."))
    if c.get("invalid_handle"):
        # 4.7: el servidor no puede verificar el identificador (p. ej. la cuenta remota lo
        # acaba de cambiar); el @usuario que se muestra es provisional y puede no servir fuera.
        avisos.append(_("Su identificador no se puede verificar ahora mismo: el @usuario que "
                        "ves es provisional y puede no funcionar fuera de tu servidor."))
    return avisos


def campos_de_perfil(c) -> list:
    """Campos de metadatos del perfil como texto: [(nombre, valor, verificado)]."""
    from app.core.html_utils import html_a_texto
    campos = []
    for f in (c.get("fields") or []) if c else []:
        campos.append((f.get("name") or "", html_a_texto(f.get("value") or "").strip(),
                       bool(f.get("verified_at"))))
    return campos

