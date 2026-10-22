# -*- coding: utf-8 -*-
"""Complemento de NVDA: lectura completa de listas (SysListView32) de SocialRSS.

Hace DOS cosas, ambas reversibles al desactivar el complemento:

1) LECTURA COMPLETA POR COLUMNA. NVDA, al navegar por una lista multicolumna, recorta el texto de
   cada columna a 511 caracteres (CBEMAXSTRLEN = 512); una publicación larga solo se lee a medias.
   Subimos CBEMAXSTRLEN y forzamos la ruta out-of-process para leer el contenido completo.

2) ACTUALIZACIÓN SILENCIOSA (estilo TWBlue). La lista de la app es VIRTUAL: cuando entran novedades
   en segundo plano, la app mueve el foco para que TÚ sigas sobre tu elemento, pero ese movimiento
   programático dispara un evento de foco que NVDA leería (te releería el elemento). La app marca
   ese movimiento AUTOMÁTICO poniendo en la lista la propiedad de ventana "SocialRSSSilentMove";
   aquí, cuando esa marca está, hacemos que NVDA NO trate ese cambio como un foco a anunciar
   (shouldAllowIAccessibleFocusEvent = False), de modo que el lector se queda callado. Al navegar
   TÚ con las flechas, la marca no está y NVDA anuncia con normalidad.
"""

import globalPluginHandler

try:
	from logHandler import log
except Exception:  # por si cambia la ubicación del logger entre versiones de NVDA
	import logging
	log = logging.getLogger("socialRSSListViewFix")

# Tamaño máximo de lectura por columna (caracteres). Generoso para cubrir publicaciones largas.
NUEVO_MAXIMO = 16384

# Nombre de la propiedad de ventana que la app pone en la lista mientras hace un movimiento de foco
# AUTOMÁTICO (debe coincidir con accessible_controls._marcar_movimiento_silencioso).
_PROP_SILENCIO = "SocialRSSSilentMove"


def _lista_en_movimiento_silencioso(obj) -> bool:
	"""True si la lista del objeto tiene puesta la marca de movimiento automático de la app."""
	try:
		import ctypes
		hwnd = getattr(obj, "windowHandle", 0)
		if not hwnd:
			return False
		return bool(ctypes.windll.user32.GetPropW(ctypes.c_void_p(int(hwnd)),
		                                          ctypes.c_wchar_p(_PROP_SILENCIO)))
	except Exception:
		return False


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
	def __init__(self):
		super().__init__()
		self._parcheado = False
		self._sysLV = None
		self._max_original = None
		self._raw_original = None
		# Estado del parche de "actualización silenciosa".
		self._silencio_parcheado = False
		self._listItem_cls = None
		self._shouldAllow_original = None
		try:
			from NVDAObjects.IAccessible import sysListView32
		except Exception as e:
			log.warning("socialRSSListViewFix: no se pudo importar sysListView32: %r" % (e,))
			return

		ListItem = getattr(sysListView32, "ListItem", None)
		# Comprobación defensiva: solo parcheamos si la versión de NVDA tiene exactamente las piezas
		# que esperamos. Si no, no tocamos nada (no rompemos NVDA en versiones distintas).
		if (
			ListItem is None
			or not hasattr(sysListView32, "CBEMAXSTRLEN")
			or not hasattr(ListItem, "_getColumnContentRaw")
			or not hasattr(ListItem, "_getColumnContentRawOutProc")
		):
			log.warning(
				"socialRSSListViewFix: esta versión de NVDA no es compatible con el parche; "
				"no se aplica ningún cambio."
			)
			return

		self._sysLV = sysListView32
		self._max_original = sysListView32.CBEMAXSTRLEN
		self._raw_original = ListItem._getColumnContentRaw
		raw_original = self._raw_original

		def _getColumnContentRaw_parcheado(itemSelf, index):
			# Forzamos la ruta out-of-process (la in-process usa un buffer fijo en C de 512). Si
			# fallara (p. ej. en procesos de 32 bits con poca memoria disponible), caemos a la
			# implementación original para no quedarnos sin leer nada.
			try:
				return itemSelf._getColumnContentRawOutProc(index)
			except Exception:
				log.debugWarning(
					"socialRSSListViewFix: la lectura out-of-process falló; uso la ruta original.",
					exc_info=True,
				)
				return raw_original(itemSelf, index)

		sysListView32.CBEMAXSTRLEN = NUEVO_MAXIMO
		ListItem._getColumnContentRaw = _getColumnContentRaw_parcheado
		self._parcheado = True
		log.info(
			"socialRSSListViewFix: lectura de listas ampliada a %d caracteres por columna."
			% (NUEVO_MAXIMO,)
		)

		# --- Parche 2: actualización SILENCIOSA (no anunciar el foco en movimientos automáticos) ---
		try:
			# La propiedad shouldAllowIAccessibleFocusEvent decide si NVDA trata un evento de foco
			# como un foco a anunciar. La sobreescribimos en ListItem: si la lista lleva la marca
			# de la app, devolvemos False (NVDA ignora ese foco automático y no relee).
			orig = getattr(ListItem, "_get_shouldAllowIAccessibleFocusEvent", None)

			def _shouldAllow_parcheado(itemSelf):
				if _lista_en_movimiento_silencioso(itemSelf):
					return False
				if orig is not None:
					return orig(itemSelf)
				return True

			# ListItem hereda la propiedad de IAccessible; la sobreescribimos en ListItem con una
			# 'property' propia (gana por MRO). Guardamos lo que había en ListItem para revertir.
			self._listItem_cls = ListItem
			self._shouldAllow_original = ListItem.__dict__.get("shouldAllowIAccessibleFocusEvent", None)
			ListItem.shouldAllowIAccessibleFocusEvent = property(_shouldAllow_parcheado)
			self._silencio_parcheado = True
			log.info("socialRSSListViewFix: actualización silenciosa de listas activada.")
		except Exception:
			log.debugWarning("socialRSSListViewFix: no se pudo activar la actualización silenciosa.",
			                 exc_info=True)

	def terminate(self, *args, **kwargs):
		# Revertir los parches para dejar NVDA exactamente como estaba antes de activar el complemento.
		try:
			if self._parcheado and self._sysLV is not None:
				self._sysLV.CBEMAXSTRLEN = self._max_original
				self._sysLV.ListItem._getColumnContentRaw = self._raw_original
				self._parcheado = False
		except Exception:
			log.debugWarning("socialRSSListViewFix: error al revertir el parche de lectura.", exc_info=True)
		try:
			if self._silencio_parcheado and self._listItem_cls is not None:
				if self._shouldAllow_original is not None:
					self._listItem_cls.shouldAllowIAccessibleFocusEvent = self._shouldAllow_original
				else:
					# No existía en ListItem (era heredada): quitamos la nuestra para restaurar la herencia.
					try:
						del self._listItem_cls.shouldAllowIAccessibleFocusEvent
					except Exception:
						pass
				self._silencio_parcheado = False
		except Exception:
			log.debugWarning("socialRSSListViewFix: error al revertir la actualización silenciosa.",
			                 exc_info=True)
		try:
			super().terminate(*args, **kwargs)
		except Exception:
			pass
