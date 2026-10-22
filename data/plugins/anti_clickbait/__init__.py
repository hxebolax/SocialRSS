# -*- coding: utf-8 -*-
"""Complemento Anti-clickbait para SocialRSS.

Puntúa el título de cada artículo nuevo según señales típicas de clickbait (mayúsculas excesivas,
signos repetidos, frases gancho, listas tipo "7 cosas que..."). Según el umbral y la acción que
elijas, marca esos artículos: les añade un aviso al título, los marca como leídos o los oculta.

Sirve además de EJEMPLO de complemento: usa el hook ARTICLE_NEW y un panel de configuración.
"""
import re

from app.core.plugins.api import PluginBase, PluginInfo, PluginHook

# Frases/gancho típicas (en español e inglés).
_FRASES = [
    "no te imaginas", "no vas a creer", "te sorprenderá", "lo que pasó después",
    "esto es lo que", "el truco que", "nadie te dijo", "te cambiará la vida",
    "you won't believe", "what happened next", "this is why", "shocking", "the secret",
    "increíble", "impactante", "viral", "alucinante",
]


class AntiClickbaitPlugin(PluginBase):
    @staticmethod
    def get_info() -> PluginInfo:
        return PluginInfo(
            id="anti_clickbait",
            name="Anti-clickbait",
            version="1.0.0",
            author="SocialRSS",
            description=("Detecta titulares tipo clickbait en los artículos nuevos y, según tu "
                         "configuración, les añade un aviso, los marca como leídos o los oculta."),
            has_settings=True,
        )

    def on_enable(self) -> bool:
        cfg = self.api.get_config()
        self._umbral = int(cfg.get("umbral", 3))
        self._accion = cfg.get("accion", "avisar")   # avisar | leido | ocultar
        self.api.register_hook(PluginHook.ARTICLE_NEW, self._procesar)
        return True

    def _puntuar(self, titulo: str) -> int:
        if not titulo:
            return 0
        t = titulo.strip()
        score = 0
        # Frases gancho.
        bajo = t.lower()
        score += sum(2 for f in _FRASES if f in bajo)
        # Signos de exclamación/interrogación repetidos.
        if re.search(r"[!?]{2,}", t):
            score += 2
        elif t.endswith("!") or t.endswith("?"):
            score += 1
        # Proporción alta de MAYÚSCULAS (gritar).
        letras = [c for c in t if c.isalpha()]
        if letras:
            mays = sum(1 for c in letras if c.isupper())
            if mays / len(letras) > 0.6 and len(letras) > 8:
                score += 2
        # Listas tipo "7 cosas que...", "10 trucos...".
        if re.match(r"^\s*\d{1,2}\b", t):
            score += 1
        # Puntos suspensivos (cliffhanger).
        if "..." in t or "…" in t:
            score += 1
        return score

    def _procesar(self, article=None, **_kw):
        if not article:
            return None
        score = self._puntuar(article.get("title", ""))
        if score < self._umbral:
            return None
        if self._accion == "ocultar":
            return {"hide": True}
        if self._accion == "leido":
            return {"mark_read": True}
        # avisar: anteponer una marca al título.
        return {"title": "⚠ " + (article.get("title", "") or "")}

    # --- Panel de configuración ---
    def get_settings_panel(self, parent):
        import wx
        panel = wx.Panel(parent)
        s = wx.BoxSizer(wx.VERTICAL)
        s.Add(wx.StaticText(panel, label=_("Sensibilidad (umbral de puntuación, 1 = muy sensible):")), 0, wx.ALL, 6)
        self._spin = wx.SpinCtrl(panel, min=1, max=10, initial=self._umbral, name=_("Umbral de clickbait"))
        s.Add(self._spin, 0, wx.ALL, 6)
        s.Add(wx.StaticText(panel, label=_("¿Qué hacer con los titulares clickbait?")), 0, wx.ALL, 6)
        self._choice = wx.Choice(panel, choices=[
            _("Avisar (anteponer un símbolo al título)"),
            _("Marcarlos como leídos"),
            _("Ocultarlos (no añadirlos)"),
        ], name=_("Acción anti-clickbait"))
        self._choice.SetSelection({"avisar": 0, "leido": 1, "ocultar": 2}.get(self._accion, 0))
        s.Add(self._choice, 0, wx.EXPAND | wx.ALL, 6)
        btn = wx.Button(panel, label=_("&Guardar"), name=_("Guardar configuración del complemento"))
        btn.Bind(wx.EVT_BUTTON, self._guardar)
        s.Add(btn, 0, wx.ALL, 6)
        panel.SetSizer(s)
        return panel

    def _guardar(self, event):
        from app.core.speech import notificar
        self._umbral = int(self._spin.GetValue())
        self._accion = {0: "avisar", 1: "leido", 2: "ocultar"}.get(self._choice.GetSelection(), "avisar")
        self.api.save_config({"umbral": self._umbral, "accion": self._accion})
        notificar(_("Configuración de Anti-clickbait guardada."))
