# -*- coding: utf-8 -*-
"""
Módulo para la conexión y gestión de la base de datos SQLite de la aplicación.
Proporciona métodos seguros para el acceso concurrente desde hilos secundarios.
"""

import sqlite3
import logging
import threading
from contextlib import contextmanager
from typing import List, Tuple, Optional
from .paths import get_data_path

log = logging.getLogger(__name__)

# Lock de ESCRITURA: solo un hilo escribe a la vez (las escrituras se serializan).
db_lock = threading.Lock()
# Contexto de TRANSACCIÓN por hilo. Cuando un hilo abre db.transaccion(), aquí queda su conexión
# y profundidad de anidamiento. Mientras está abierta, las llamadas a db.execute()/query() DE ESE
# MISMO HILO se UNEN a la transacción (misma conexión, sin commit individual y sin volver a tomar
# db_lock —que no es reentrante—). Así se puede envolver cualquier bucle de guardado en una sola
# transacción aunque por dentro llame a funciones que hacen db.execute (reglas de RSS, plugins,
# _procesar_y_guardar_*...), sin riesgo de interbloqueo y con un único commit.
_tx_local = threading.local()
# LECTURAS CONCURRENTES SIN LOCK: con WAL, SQLite permite varios lectores a la vez. En vez de UNA
# conexión de lectura compartida bajo un lock global (que serializaba TODAS las consultas de la app:
# el hilo de la interfaz hacía cola detrás de las miles de lecturas de los hilos de fondo —RSS,
# timelines, el "cheap-skip" que consulta cada toot/post/artículo—, y eso daba tirones al abrir
# diálogos o navegar), cada hilo usa su PROPIA conexión de lectura (thread-local). Así las lecturas
# del hilo de la interfaz NO esperan a las de fondo. Se registran todas para cerrarlas al salir.
_read_local = threading.local()
_read_conns_lock = threading.Lock()
_read_conns: list = []


def _describe_params(params) -> str:
    """Describe los parámetros de una consulta SIN volcar su contenido en el log
    (pueden ser mensajes privados, artículos o credenciales): tipo y longitud."""
    try:
        partes = []
        for p in (params or ()):
            if isinstance(p, (bytes, bytearray)):
                partes.append(f"bytes[{len(p)}]")
            elif isinstance(p, str):
                partes.append(f"str[{len(p)}]")
            elif p is None:
                partes.append("None")
            else:
                partes.append(type(p).__name__)
        return "(" + ", ".join(partes) + ")"
    except Exception:
        return "(?)"


def _resumen_sql(query: str) -> str:
    """La sentencia SQL en una línea y truncada (es código, no datos privados)."""
    q = " ".join((query or "").split())
    return (q[:200] + "…") if len(q) > 200 else q

class DatabaseManager:
    """Administrador de base de datos relacional SQLite.

    Conexiones a la misma base (en modo WAL):
    - Una de ESCRITURA (con db_lock), para INSERT/UPDATE/DELETE y migraciones.
    - Una de LECTURA POR HILO (thread-local, sin lock), para SELECT: así el hilo de la UI lee sin
      bloquearse ni por las escrituras de fondo ni por las lecturas de otros hilos.
    """

    def __init__(self, filename: str = "database/social_rss.db"):
        self.db_path = get_data_path(filename)
        self._conn: Optional[sqlite3.Connection] = None        # conexión de escritura
        self.connect()

    def connect(self):
        """Conecta a la base de datos (escritura y lectura) y configura pragmas."""
        with db_lock:
            try:
                # Asegurar que el directorio padre existe
                self.db_path.parent.mkdir(parents=True, exist_ok=True)

                # Conexión de ESCRITURA. check_same_thread=False: se usa desde varios
                # hilos, pero el acceso se serializa con db_lock.
                self._conn = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                    timeout=30.0
                )
                # Claves foráneas + WAL (lectores y un escritor concurrentes) + NORMAL.
                self._conn.execute("PRAGMA foreign_keys = ON")
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
                # Ajustes de rendimiento (Fase 5): más caché de páginas y E/S mapeada
                # en memoria para reducir lecturas a disco; checkpoint del WAL un poco
                # más frecuente para que el archivo WAL no crezca demasiado.
                for _pragma in ("PRAGMA cache_size = -16000",      # ~16 MB de caché
                                "PRAGMA mmap_size = 134217728",     # 128 MB mmap
                                "PRAGMA wal_autocheckpoint = 800"):
                    try:
                        self._conn.execute(_pragma)
                    except Exception:
                        pass

                # Las conexiones de LECTURA se crean por hilo bajo demanda (ver _get_read_connection).
                log.info(f"Conectado a la base de datos SQLite: {self.db_path}")
            except Exception as e:
                log.critical(f"Error al conectar con la base de datos SQLite: {e}")
                raise

    def get_connection(self) -> sqlite3.Connection:
        """Devuelve la conexión de ESCRITURA (la usan las migraciones y las escrituras)."""
        if not self._conn:
            self.connect()
        return self._conn

    def _get_read_connection(self) -> sqlite3.Connection:
        """Devuelve la conexión de LECTURA propia de ESTE hilo (la crea la primera vez). WAL permite
        lectores concurrentes, así que cada hilo lee en paralelo sin lock global. Cada SELECT corre
        en autocommit y ve siempre el último estado CONFIRMADO (no se mantiene transacción abierta)."""
        conn = getattr(_read_local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
        try:
            conn.execute("PRAGMA query_only = ON")
            conn.execute("PRAGMA cache_size = -16000")
            # mmap por conexión de LECTURA: hay UNA por hilo (task_manager x10 + UI + loops de cada
            # red), así que se mantiene BAJO. La base real ronda ~11 MB tras la compactación, así que
            # 16 MB por conexión sobra para mapearla entera y acota el working set con muchos hilos.
            conn.execute("PRAGMA mmap_size = 16777216")
        except Exception:
            pass
        _read_local.conn = conn
        with _read_conns_lock:
            _read_conns.append(conn)
        return conn

    def compactar(self, cerrar_lecturas: bool = False) -> bool:
        """Compacta el fichero de la base (VACUUM) y trunca el WAL. Recupera el espacio muerto que
        dejan los borrados (la BD puede quedar hinchada muy por encima de su contenido real). Para que
        el fichero se ENCOJA de verdad hace falta que no queden lectores con una foto vieja abierta.

        'cerrar_lecturas' cierra las conexiones de lectura registradas para poder truncar: SOLO es
        seguro al ARRANQUE (no hay otros hilos leyendo). En pleno uso se deja en False (hace lo que
        puede sin arriesgar a cerrar una conexión que otro hilo esté usando). Devuelve True si termina
        sin error."""
        with db_lock:
            if cerrar_lecturas:
                # Cerrar las conexiones de LECTURA (mmap/foto WAL) para que el checkpoint pueda truncar.
                with _read_conns_lock:
                    for c in list(_read_conns):
                        try:
                            c.close()
                        except Exception:
                            pass
                    _read_conns.clear()
                _read_local.conn = None
            try:
                conn = self.get_connection()
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                conn.execute("VACUUM")
                try:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                return True
            except Exception as e:
                log.error(f"No se pudo compactar la base de datos (VACUUM): {e}")
                return False

    def compactar_si_procede(self, porcentaje: float = 0.20, minimo_mb: int = 10) -> bool:
        """Al ARRANQUE (todavía sin hilos de servicio), si hay bastante espacio muerto
        recuperable, compacta. El espacio recuperable se mide con las PÁGINAS LIBRES
        (freelist_count * page_size): page_count INCLUYE las libres, así que comparar el
        fichero con page_count*page_size casi nunca disparaba la compactación aunque la
        base estuviera medio hueca. Se compacta si lo recuperable supera `minimo_mb` Y
        el `porcentaje` del tamaño físico."""
        try:
            conn = self.get_connection()
            libres = conn.execute("PRAGMA freelist_count").fetchone()[0] or 0
            ps = conn.execute("PRAGMA page_size").fetchone()[0] or 0
            recuperable = libres * ps
            fisico = self.db_path.stat().st_size if self.db_path.exists() else 0
            if recuperable < minimo_mb * 1024 * 1024:
                return False           # poco que recuperar: no merece la pena
            if fisico and recuperable < fisico * porcentaje:
                return False           # proporcionalmente pequeño para el tamaño de la base
            antes = fisico
            if self.compactar(cerrar_lecturas=True):
                despues = self.db_path.stat().st_size
                log.info("BD compactada al arranque: %.1f MB -> %.1f MB"
                         % (antes / 1048576, despues / 1048576))
                return True
            return False
        except Exception as e:
            log.warning(f"No se pudo comprobar/compactar la base al arranque: {e}")
            return False

    def cerrar_lectura_hilo(self):
        """Cierra la conexión de LECTURA del hilo actual (si la creó) y la quita del registro.

        La usan los hilos EFÍMEROS (p. ej. el ThreadPoolExecutor propio de RSS, que se crea y
        destruye en cada actualización global): sin esto, cada hilo nuevo dejaba su conexión de
        lectura abierta en _read_conns hasta cerrar la app, acumulando descriptores y espacio
        mapeado (mmap) en sesiones largas. Los hilos del pool central NO deben llamarla: se
        reutilizan y conviene que conserven su conexión."""
        conn = getattr(_read_local, "conn", None)
        if conn is None:
            return
        try:
            conn.close()
        except Exception as e:
            log.debug(f"No se pudo cerrar la conexión de lectura del hilo: {e}")
        _read_local.conn = None
        with _read_conns_lock:
            try:
                _read_conns.remove(conn)
            except ValueError:
                pass

    def execute(self, query: str, params: Tuple = ()) -> int:
        """Ejecuta una sentencia de inserción/actualización/borrado. Devuelve rowcount."""
        tx = getattr(_tx_local, "conn", None)
        if tx is not None:
            # Dentro de una transacción de este hilo: misma conexión, sin commit ni lock.
            return tx.execute(query, params).rowcount
        with db_lock:
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return cursor.rowcount
            except Exception as e:
                log.error(f"Error en query de escritura: {_resumen_sql(query)}. "
                          f"Parámetros: {_describe_params(params)}. Error: {e}")
                if self._conn:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                raise

    def execute_insert(self, query: str, params: Tuple = ()) -> int:
        """Ejecuta un INSERT y devuelve el id de la fila insertada (lastrowid),
        capturado de forma ATÓMICA con la inserción (mismo cursor, bajo el lock de
        escritura). Sustituye al patrón inseguro INSERT + 'SELECT last_insert_rowid()'
        (que ahora, con la conexión de lectura separada, daría un valor erróneo y
        además podía verse afectado por escrituras intercaladas de otros hilos)."""
        tx = getattr(_tx_local, "conn", None)
        if tx is not None:
            return tx.execute(query, params).lastrowid
        with db_lock:
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
                return cursor.lastrowid
            except Exception as e:
                log.error(f"Error en INSERT: {_resumen_sql(query)}. "
                          f"Parámetros: {_describe_params(params)}. Error: {e}")
                if self._conn:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                raise

    @contextmanager
    def transaccion(self):
        """Agrupa varias escrituras en UNA sola transacción con UN único commit, tomando el
        lock de escritura UNA vez. Reduce drásticamente los commits/fsync al sincronizar (p. ej.
        guardar un timeline pasa de ~160 mini-transacciones a 1).

        Uso:
            with db.transaccion():
                db.execute(sql, params)   # se UNE a la transacción; NO commitea aquí
                otra_funcion_que_hace_db_execute(...)   # también se une (mismo hilo)

        Dentro del bloque, TODAS las llamadas a db.execute()/db.query() de ESTE hilo se unen a la
        transacción (misma conexión, sin commit), así que es seguro envolver bucles que por dentro
        llaman a otras funciones de guardado (reglas de RSS, plugins, _procesar_y_guardar_*...). El
        commit (o rollback si hay excepción) ocurre al salir. Reentrante: anidar transacciones
        reutiliza la de fuera (un solo commit, el externo). (También se cede `conn` por
        compatibilidad con el código que ya lo usa explícitamente.)"""
        # Anidada: reutilizar la transacción ya abierta en este hilo (no abrir otra ni commitear).
        if getattr(_tx_local, "conn", None) is not None:
            _tx_local.depth += 1
            try:
                yield _tx_local.conn
            finally:
                _tx_local.depth -= 1
            return
        with db_lock:
            conn = self.get_connection()
            _tx_local.conn = conn
            _tx_local.depth = 1
            try:
                yield conn
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                _tx_local.conn = None
                _tx_local.depth = 0

    def execute_many(self, query: str, params_list: List[Tuple]) -> int:
        """Ejecuta una sentencia por lotes en una única transacción."""
        tx = getattr(_tx_local, "conn", None)
        if tx is not None:
            return tx.executemany(query, params_list).rowcount
        with db_lock:
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                cursor.executemany(query, params_list)
                conn.commit()
                return cursor.rowcount
            except Exception as e:
                log.error(f"Error en query de escritura en lote (executemany): "
                          f"{_resumen_sql(query)}. Error: {e}")
                if self._conn:
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
                raise

    def query(self, query: str, params: Tuple = ()) -> List[Tuple]:
        """Ejecuta una consulta y devuelve todas las filas.

        Si ESTE hilo tiene abierta una db.transaccion(), la consulta se hace por la MISMA
        conexión de la transacción: así ve las escrituras aún sin confirmar (lectura tras
        escritura) y las decisiones dentro de la transacción son coherentes con lo ya
        insertado. El hilo ya posee db_lock en ese caso, así que no cuesta concurrencia.

        Fuera de una transacción usa la conexión de lectura POR HILO (sin lock) y ve el
        último estado CONFIRMADO."""
        tx = getattr(_tx_local, "conn", None)
        if tx is not None:
            try:
                return tx.execute(query, params).fetchall()
            except Exception as e:
                log.error(f"Error en query dentro de transacción: {_resumen_sql(query)}. "
                          f"Parámetros: {_describe_params(params)}. Error: {e}")
                raise
        try:
            cursor = self._get_read_connection().cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
        except Exception as e:
            log.error(f"Error en query de consulta: {_resumen_sql(query)}. "
                      f"Parámetros: {_describe_params(params)}. Error: {e}")
            raise

    def query_one(self, query: str, params: Tuple = ()) -> Optional[Tuple]:
        """Ejecuta una consulta y devuelve la primera fila, o None. Igual que query():
        dentro de una db.transaccion() de este hilo usa la conexión de la transacción
        (ve lo aún no confirmado); fuera, la conexión de lectura por hilo."""
        tx = getattr(_tx_local, "conn", None)
        if tx is not None:
            try:
                return tx.execute(query, params).fetchone()
            except Exception as e:
                log.error(f"Error en query_one dentro de transacción: {_resumen_sql(query)}. "
                          f"Parámetros: {_describe_params(params)}. Error: {e}")
                raise
        try:
            cursor = self._get_read_connection().cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
        except Exception as e:
            log.error(f"Error en query de consulta (one): {_resumen_sql(query)}. "
                      f"Parámetros: {_describe_params(params)}. Error: {e}")
            raise

    def close(self):
        """Cierra ambas conexiones a la base de datos."""
        with db_lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception as e:
                    log.error(f"Error al cerrar la base de datos (escritura): {e}")
                finally:
                    self._conn = None
        with _read_conns_lock:
            for c in _read_conns:
                try:
                    c.close()
                except Exception as e:
                    log.error(f"Error al cerrar una conexión de lectura: {e}")
            _read_conns.clear()
            try:
                _read_local.conn = None
            except Exception:
                pass
            log.info("Conexiones de lectura a base de datos SQLite cerradas.")

import sys

# Comprobar si estamos corriendo bajo un entorno de pruebas (pytest o unittest)
_es_test = "pytest" in sys.modules or "unittest" in sys.modules or any("test" in arg for arg in sys.argv)
_db_filename = "database/social_rss_test.db" if _es_test else "database/social_rss.db"

# Instancia global del gestor de base de datos
db = DatabaseManager(_db_filename)

