# -*- coding: utf-8 -*-
"""Router de LECTURA de X: combina twikit y twscrape de forma complementaria.

Cada lectura la sirve el motor que mejor la soporta y, si falla o devuelve vacío (p. ej. X cambió
los IDs de GraphQL de una librería), se cae automáticamente a la otra. Las acciones de ESCRITURA NO
pasan por aquí: siempre van por twikit.

Convención: los métodos `r_*` devuelven SIEMPRE listas de dicts uniformes (los mismos que produce
`_tweet_a_dict`/`_user_a_dict`), de modo que el guardado en la BD y la UI no dependen del motor.
Las implementaciones por motor son `_tw_*` (twikit, en x_timeline/x_extras) y `_tws_*` (twscrape).
"""
from app.services.x.x_common import config, log, XServiceError


class _RouterMixin:
    # ------------------------------------------------------------------
    # Selección de motor y respaldo cruzado
    # ------------------------------------------------------------------
    def _read_engine(self) -> str:
        e = config.get("x", "read_engine", "auto")
        return e if e in ("auto", "twikit", "twscrape") else "auto"

    # Motor preferido en modo "auto". Desde 2026-09 es twikit en TODAS las lecturas: se
    # comprobaron una a una con una sesión real y funcionan, mientras que twscrape 0.19.1 no
    # consigue generar la cabecera x-client-transaction-id ("Couldn't get XClientTxId indices
    # script"), reintenta ~12 s y devuelve VACÍO (que el router tomaba por "no hay nada").
    # twscrape queda de respaldo por si X rompe algo de twikit.
    PREFERIDO = "twikit"

    def _fuentes(self, twikit_fn, tws_fn, prefer: str = None):
        """Construye la lista ordenada [(nombre, callable), ...] a probar para una lectura.

        - `twikit_fn`/`tws_fn`: callables sin argumentos (o None si ese motor no soporta la op).
        - `prefer`: motor preferido en modo "auto".
        Reglas: un motor "forzado" (read_engine=twikit|twscrape) solo se respeta si soporta la op;
        si no, se usa el que sí (para no romper, p. ej., Inicio con el motor forzado a twscrape)."""
        engine = self._read_engine()
        prefer = prefer or self.PREFERIDO
        tws_ok = tws_fn is not None and self.twscrape_disponible()
        tw_ok = twikit_fn is not None
        if engine == "twikit" and tw_ok:
            return [("twikit", twikit_fn)]
        if engine == "twscrape" and tws_ok:
            return [("twscrape", tws_fn)]
        orden = []
        if prefer == "twscrape":
            if tws_ok:
                orden.append(("twscrape", tws_fn))
            if tw_ok:
                orden.append(("twikit", twikit_fn))
        else:
            if tw_ok:
                orden.append(("twikit", twikit_fn))
            if tws_ok:
                orden.append(("twscrape", tws_fn))
        return orden

    def _intentar(self, fuentes):
        """Prueba las fuentes en orden. Devuelve el primer resultado NO vacío. Si alguna devolvió
        vacío sin error, devuelve [] (vacío legítimo). Si TODAS fallaron, relanza el último error
        (para que la UI muestre un error real en vez de una lista vacía silenciosa)."""
        ultimo_err = None
        vacio_visto = False
        for nombre, fn in fuentes:
            try:
                res = fn()
            except Exception as e:
                ultimo_err = e
                log.debug(f"X: el motor {nombre} falló en la lectura: {e}")
                continue
            if res:
                log.debug(f"X: lectura servida por {nombre} ({len(res)} elementos).")
                return res
            vacio_visto = True
        if ultimo_err is not None and not vacio_visto:
            raise XServiceError(str(ultimo_err))
        return []

    # ------------------------------------------------------------------
    # Lecturas enrutadas (devuelven listas de dicts uniformes)
    # ------------------------------------------------------------------
    # Timelines que SOLO sabe servir twikit (sesión propia / algoritmo).
    _SOLO_TWIKIT_TL = ("home", "for_you", "following", "communities")

    def r_timeline(self, account_id: int, tipo: str, count: int):
        # Cronología de un usuario: clave 'user:<id>' o 'user:<id>:<modo>' (tweets/replies/media).
        # Conversación abierta como buffer: 'thread:<id_del_tuit>'.
        if str(tipo).startswith("thread:"):
            return self.r_hilo(account_id, tipo.split(":", 1)[1])
        # Comunidad abierta como buffer: 'community:<id>' (sus publicaciones recientes).
        if str(tipo).startswith("community:"):
            return self._intentar(self._fuentes(
                lambda: self._tw_timeline(account_id, tipo, count), None))
        if str(tipo).startswith("user:"):
            resto = tipo.split(":", 1)[1]
            uid, _, modo = resto.partition(":")
            return self.r_user_tweets(account_id, uid, modo or "tweets", count)
        twikit_fn = lambda: self._tw_timeline(account_id, tipo, count)
        # ¿twscrape sabe servir este tipo? (search/list/bookmarks/mentions)
        tws_capaz = (tipo.startswith(("search:", "list:"))
                     or tipo in ("bookmarks", "mentions"))
        if tipo in self._SOLO_TWIKIT_TL or not tws_capaz:
            return self._intentar(self._fuentes(twikit_fn, None))
        tws_fn = lambda: self._tws_timeline(account_id, tipo, count)
        return self._intentar(self._fuentes(twikit_fn, tws_fn))

    def r_user_tweets(self, account_id: int, uid: str, modo: str, count: int):
        """modo: 'tweets' | 'replies' | 'media' | 'likes'. 'likes' solo lo sirve twikit."""
        twikit_fn = lambda: self._tw_user_tweets(account_id, uid, modo, count)
        tws_fn = (None if modo == "likes"
                  else (lambda: self._tws_user_tweets(account_id, uid, modo, count)))
        return self._intentar(self._fuentes(twikit_fn, tws_fn))

    def r_hilo(self, account_id: int, tweet_id: str):
        twikit_fn = lambda: self._tw_hilo(account_id, tweet_id)
        tws_fn = lambda: self._tws_hilo(account_id, tweet_id)
        # twikit lee TweetDetail entero (encima, el tuit y las respuestas anidadas).
        return self._intentar(self._fuentes(twikit_fn, tws_fn))

    def r_actores(self, account_id: int, uid: str, bucket: str, count: int):
        """bucket: 'followers' | 'friends' | 'verified_followers' (este último solo twscrape)."""
        if not uid:
            uid = self._yo(account_id).get("id") or ""
        twikit_fn = lambda: self._tw_actores(account_id, uid, bucket, count)
        tws_fn = (lambda: self._tws_actores(account_id, uid, bucket, count)) if uid else None
        return self._intentar(self._fuentes(twikit_fn, tws_fn))

    def r_buscar_usuarios(self, account_id: int, query: str, count: int = 40):
        twikit_fn = lambda: self._tw_buscar_usuarios(account_id, query, count)
        tws_fn = lambda: self._tws_buscar_usuarios(account_id, query, count)
        return self._intentar(self._fuentes(twikit_fn, tws_fn))

    def r_perfil(self, account_id: int, screen_name: str):
        """Devuelve un dict de perfil (no lista). Prueba twscrape y cae a twikit (o al revés).
        Se envuelve el resultado en una lista para reutilizar `_intentar` (que filtra vacíos)."""
        fuentes = self._fuentes(
            (lambda: [p] if (p := self._tw_perfil(account_id, screen_name)) else []),
            (lambda: [p] if (p := self._tws_perfil(account_id, screen_name)) else []))
        res = self._intentar(fuentes)
        return res[0] if res else None

    def r_resolver_uid(self, account_id: int, screen_name: str):
        """Resuelve @usuario -> {user_id, screen_name, name}. None si no se encuentra."""
        p = self.r_perfil(account_id, screen_name)
        if not p or not p.get("user_id"):
            return None
        return {"user_id": p["user_id"], "screen_name": p.get("screen_name", ""),
                "name": p.get("name", "")}

    def r_tendencias(self, account_id: int):
        twikit_fn = lambda: self._tw_tendencias(account_id)
        tws_fn = lambda: self._tws_tendencias(account_id)
        return self._intentar(self._fuentes(twikit_fn, tws_fn))
