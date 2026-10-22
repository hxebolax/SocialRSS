# -*- coding: utf-8 -*-
"""
Actualizador seguro de reglas de Bypass Paywalls Clean para el lector RSS.

Comprueba periódicamente o a petición del usuario la disponibilidad de reglas
actualizadas (sites_updated.json y updates.json) en el repositorio oficial de BPC.
Descarga a un archivo temporal, valida el esquema e integridad, efectúa un reemplazo
atómico, mantiene copia de seguridad y permite la reversión a la versión previa.
"""

import json
import logging
import os
import shutil
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger(__name__)

URL_UPDATES = "https://gitflic.ru/project/magnolia1234/bpc_updates/blob/raw?file=updates.json"
URL_SITES_UPDATED = "https://gitflic.ru/project/magnolia1234/bpc_updates/blob/raw?file=sites_updated.json"
MAX_BYTES_ACTUALIZACION = 512 * 1024
TIMEOUT_ACTUALIZACION = (8, 15)


class BPCUpdater:
	"""Gestor de comprobación, descarga y aplicación segura de actualizaciones de reglas."""

	@staticmethod
	def _ruta_archivo_actualizado() -> str:
		"""Ruta del archivo de reglas descargadas en la carpeta de datos de usuario."""
		try:
			from app.core.paths import get_data_path
			return str(get_data_path("rss_bpc_updated.json"))
		except Exception:
			dir_rss = os.path.dirname(os.path.abspath(__file__))
			return os.path.join(dir_rss, "bpc_rules_updated.json")

	@staticmethod
	def _ruta_archivo_backup() -> str:
		"""Ruta del archivo de copia de seguridad previo a la última actualización."""
		try:
			from app.core.paths import get_data_path
			return str(get_data_path("rss_bpc_backup.json"))
		except Exception:
			dir_rss = os.path.dirname(os.path.abspath(__file__))
			return os.path.join(dir_rss, "bpc_rules_backup.json")

	@staticmethod
	def _descargar_json_seguro(url: str) -> Dict[str, Any]:
		"""Descarga un archivo JSON verificando HTTPS, tamaño máximo y resolución segura."""
		p = urlparse(url)
		if p.scheme != "https":
			raise ValueError("Solo se admiten conexiones seguras por HTTPS.")
		if not p.hostname:
			raise ValueError("URL sin nombre de host.")

		from app.core.net import _ip_privada, http_session
		if _ip_privada(p.hostname):
			raise ValueError("El destino no puede ser una dirección IP privada o local.")

		sesion = http_session()
		cabeceras = {
			"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
			"Accept": "application/json, text/plain, */*",
		}

		resp = sesion.get(url, headers=cabeceras, timeout=TIMEOUT_ACTUALIZACION, stream=True)
		try:
			if resp.status_code != 200:
				raise ValueError(f"El servidor respondió con código HTTP {resp.status_code}")

			total = 0
			trozos = []
			for trozo in resp.iter_content(chunk_size=16384):
				total += len(trozo)
				if total > MAX_BYTES_ACTUALIZACION:
					raise ValueError("El archivo de actualización supera el tamaño permitido.")
				trozos.append(trozo)

			contenido_bytes = b"".join(trozos)
			texto = contenido_bytes.decode("utf-8", errors="replace")
			datos = json.loads(texto)
			if not isinstance(datos, dict):
				raise ValueError("El formato recibido no es un objeto JSON válido.")
			return datos
		finally:
			resp.close()

	@classmethod
	def comprobar_actualizaciones(cls) -> Dict[str, Any]:
		"""Comprueba de forma síncrona si hay nuevas versiones de reglas BPC."""
		from .bpc_rules import BPCRulesEngine
		motor = BPCRulesEngine.obtener()
		version_actual = motor.version
		resultado = {
			"hay_actualizacion": False,
			"version_actual": version_actual,
			"version_remota": version_actual,
			"sitios_nuevos": 0,
			"sitios_detalle": [],
			"mensaje": "",
			"datos_sitios": None,
			"es_nueva_version_app": False,
		}

		# 1. Comprobar updates.json para ver la versión mayor/menor más reciente
		try:
			datos_updates = cls._descargar_json_seguro(URL_UPDATES)
			addons = datos_updates.get("addons", {})
			magnolia = addons.get("magnolia@12.34", {})
			lista_upd = magnolia.get("updates", [])
			if lista_upd and isinstance(lista_upd, list):
				v_remota = lista_upd[0].get("version", "")
				if v_remota:
					resultado["version_remota"] = v_remota
		except Exception as e:
			log.warning(f"No se pudo consultar updates.json: {e}")

		# 2. Descargar y validar sites_updated.json
		try:
			datos_sitios = cls._descargar_json_seguro(URL_SITES_UPDATED)
			resultado["datos_sitios"] = datos_sitios
			sitios_validos = []
			for nombre_sitio, regla in datos_sitios.items():
				if isinstance(regla, dict) and (regla.get("domain") or regla.get("group")):
					sitios_validos.append(nombre_sitio)

			resultado["sitios_nuevos"] = len(sitios_validos)
			resultado["sitios_detalle"] = sitios_validos

			# Determinar si hay cambios respecto a lo que tenemos cargado
			cambios_nuevos = False
			for nombre in sitios_validos:
				regla = datos_sitios[nombre]
				regla_actual = motor.sitios_actualizados.get(nombre)
				if regla != regla_actual:
					cambios_nuevos = True
					break

			if cambios_nuevos:
				resultado["hay_actualizacion"] = True
				resultado["mensaje"] = f"Hay {len(sitios_validos)} medios con reglas actualizadas en el repositorio."
			elif resultado["version_remota"] != version_actual:
				resultado["hay_actualizacion"] = True
				resultado["es_nueva_version_app"] = True
				resultado["mensaje"] = f"Existe una nueva revisión mayor ({resultado['version_remota']})."
			else:
				resultado["mensaje"] = "Las reglas ya se encuentran en su versión más reciente."

		except Exception as e:
			log.error(f"Fallo al comprobar sites_updated.json: {e}")
			resultado["mensaje"] = f"No se pudo conectar con el repositorio de reglas: {e}"

		return resultado

	@classmethod
	def aplicar_actualizacion(cls, datos_sitios: Dict[str, Any], version_nueva: str = "") -> Tuple[bool, str]:
		"""Aplica atómicamente las reglas actualizadas en disco y recarga el motor."""
		if not datos_sitios or not isinstance(datos_sitios, dict):
			return False, "Datos de reglas inválidos."

		ruta_destino = cls._ruta_archivo_actualizado()
		ruta_backup = cls._ruta_archivo_backup()
		ruta_tmp = ruta_destino + ".tmp"

		# Copia de seguridad si ya existe un archivo previo
		if os.path.exists(ruta_destino):
			try:
				shutil.copyfile(ruta_destino, ruta_backup)
			except Exception as e:
				log.warning(f"No se pudo crear backup de reglas previas: {e}")

		datos_a_guardar = {
			"version": version_nueva or "4.4.5.x",
			"release_date": time.strftime("%Y-%m-%d"),
			"sites_updated": datos_sitios,
		}

		try:
			with open(ruta_tmp, "w", encoding="utf-8") as f:
				json.dump(datos_a_guardar, f, indent=2, ensure_ascii=False)
			# Reemplazo atómico
			if os.path.exists(ruta_destino):
				os.replace(ruta_tmp, ruta_destino)
			else:
				os.rename(ruta_tmp, ruta_destino)
		except Exception as e:
			if os.path.exists(ruta_tmp):
				try:
					os.remove(ruta_tmp)
				except Exception:
					pass
			return False, f"Error al guardar las reglas en disco: {e}"

		# Recargar el motor en memoria
		from .bpc_rules import BPCRulesEngine
		BPCRulesEngine.obtener().cargar_reglas()
		log.info(f"Reglas BPC actualizadas correctamente en {ruta_destino}")
		return True, "Reglas actualizadas y aplicadas correctamente."

	@classmethod
	def revertir_version_anterior(cls) -> Tuple[bool, str]:
		"""Revierte las reglas a la copia de seguridad previa o a la versión de fábrica."""
		ruta_destino = cls._ruta_archivo_actualizado()
		ruta_backup = cls._ruta_archivo_backup()

		if os.path.exists(ruta_backup):
			try:
				shutil.copyfile(ruta_backup, ruta_destino)
				os.remove(ruta_backup)
				from .bpc_rules import BPCRulesEngine
				BPCRulesEngine.obtener().cargar_reglas()
				return True, "Se ha restaurado la versión de reglas anterior."
			except Exception as e:
				return False, f"No se pudo restaurar la copia de seguridad: {e}"

		if os.path.exists(ruta_destino):
			try:
				os.remove(ruta_destino)
				from .bpc_rules import BPCRulesEngine
				BPCRulesEngine.obtener().cargar_reglas()
				return True, "Se han restablecido las reglas de fábrica originales."
			except Exception as e:
				return False, f"No se pudo eliminar el archivo de reglas personalizadas: {e}"

		return False, "Ya estás utilizando las reglas de fábrica originales."

	@classmethod
	def comprobar_y_actualizar_async(cls, callback: Callable[[Dict[str, Any], Optional[Exception]], None]):
		"""Comprueba y aplica en segundo plano las actualizaciones informando a la interfaz."""
		from app.core.task_manager import task_manager

		def _tarea(ctx):
			ctx.report_progress(30, "Consultando repositorio de reglas...")
			info = cls.comprobar_actualizaciones()
			if info.get("hay_actualizacion") and info.get("datos_sitios"):
				ctx.report_progress(70, "Aplicando actualización de reglas...")
				ok, msg = cls.aplicar_actualizacion(info["datos_sitios"], info.get("version_remota", ""))
				info["aplicada"] = ok
				info["mensaje_aplicacion"] = msg
			return info

		task_manager.start_task(_tarea, callback=callback, pass_context=True)
