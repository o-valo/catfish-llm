#!/usr/bin/env python3
# ==============================================================================
# Dateiname: llm_client.py
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Universeller Client für OpenAI-kompatible Chat-APIs.
#
# Funktioniert u.a. mit:
#   - llm-bahnhof Proxy      (http://10.7.0.124:8000  bzw. http://10.7.0.124:8000/)
#   - Ollama                 (http://localhost:11434)
#   - LM Studio              (http://localhost:1234/v1)
#   - vLLM / llama.cpp-Server / jede OpenAI-kompatible API
#
# Die Endpunkt-URL wird automatisch normalisiert:
#   http://host:8000/        -> http://host:8000/v1/chat/completions
#   http://host:8000/v1      -> http://host:8000/v1/chat/completions
#   http://host:8000         -> http://host:8000/v1/chat/completions
# ==============================================================================
import re
import time

import requests


class LLMError(RuntimeError):
    """Wird geworfen, wenn das LLM dauerhaft nicht antwortet."""


class LLMClient:
    def __init__(self, base_url, model, api_key="", temperature=0.7,
                 timeout=120, max_retries=3, max_tokens=300):
        self.chat_url = self._normalisiere_url(base_url)
        self.model = model
        self.api_key = (api_key or "").strip()
        self.temperature = float(temperature)
        self.timeout = int(timeout)
        self.max_retries = int(max_retries)
        self.max_tokens = int(max_tokens)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalisiere_url(base_url):
        """Hängt '/v1/chat/completions' intelligent an die Basis-URL an."""
        u = (base_url or "").strip().rstrip("/")
        if u.endswith("/chat/completions"):
            return u
        if u.endswith("/v1"):
            return u + "/chat/completions"
        return u + "/v1/chat/completions"

    @staticmethod
    def _entferne_think(text):
        """Entfernt <think>...</think>-Blöcke von Reasoning-Modellen."""
        return re.sub(r"<think>.*?</think>", "", text,
                      flags=re.DOTALL | re.IGNORECASE).strip()

    # ------------------------------------------------------------------ #
    def chat(self, messages, temperature=None, max_tokens=None):
        """Sendet eine Chat-Anfrage und liefert den Antworttext zurück."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        fehler = None
        for versuch in range(1, self.max_retries + 1):
            try:
                r = requests.post(self.chat_url, headers=headers,
                                  json=payload, timeout=self.timeout)
                r.raise_for_status()
                daten = r.json()
                inhalt = (daten.get("choices") or [{}])[0].get("message", {}).get("content") or ""
                inhalt = self._entferne_think(inhalt)
                if inhalt:
                    return inhalt
                fehler = "Leere Antwort vom LLM"
            except Exception as exc:  # Netzwerk, Timeout, HTTP-Fehler ...
                fehler = repr(exc)
            time.sleep(min(2 * versuch, 6))

        raise LLMError(
            f"LLM-Aufruf nach {self.max_retries} Versuchen fehlgeschlagen "
            f"({self.chat_url}, Modell '{self.model}'): {fehler}")
