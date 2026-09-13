"""
title: Gambit Schach (chess-proxy)
author: Olav
version: 1.0
description: Bindet die Schach-App des chess-proxys als natives
             OpenWebUI-Tool ein.
"""

import requests


class Tools:
    """OpenWebUI-Tool-Klasse: jede öffentliche Methode = ein Werkzeug,
    das das LLM per nativem Function-Calling selbstständig aufrufen kann.

    Konfiguration:
      - CHESS_PROXY_URL: Basis-URL des chess-proxys
      - CHESS_PROXY_KEY: API-Key (leer lassen, wenn der Proxy keinen verlangt)
      - session_id: OpenWebUI übergibt diese automatisch pro Chat/Sitzung,
        dadurch hat jeder Chat seine eigene Partie.
    """

    def __init__(self):
        self.CHESS_PROXY_URL = "http://192.168.179.3:8300"
        self.CHESS_PROXY_KEY = ""
        self.timeout = 30

    # ------------------------------------------------------------------ #
    def _call(self, werkzeug, session_id, **params):
        headers = {"Content-Type": "application/json"}
        if self.CHESS_PROXY_KEY:
            headers["Authorization"] = f"Bearer {self.CHESS_PROXY_KEY}"
        payload = {"session_id": session_id, **params}
        try:
            r = requests.post(f"{self.CHESS_PROXY_URL}/chess/api/{werkzeug}",
                              json=payload, headers=headers,
                              timeout=self.timeout)
            daten = r.json()
        except Exception as exc:
            return f"FEHLER: chess-proxy nicht erreichbar ({exc!r})"
        if r.status_code != 200:
            return f"FEHLER: {daten.get('error', r.text)}"
        return daten.get("result", "")

    # ------------------------------------------------------------------ #
    # Werkzeuge (Methodennamen = Tool-Namen für das LLM)
    # ------------------------------------------------------------------ #
    def neue_partie(self, session_id: str, eigene_farbe: str = "weiss",
                    gegner_name: str = "Mensch", nutzer_farbe: str = "") -> str:
        """Startet eine neue Schachpartie.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        :param eigene_farbe: Farbe des LLM-Spielers, "weiss" oder "schwarz"
        :param gegner_name: Name des menschlichen Gegners
        """
        return self._call("neue_partie", session_id,
                          eigene_farbe=eigene_farbe, gegner_name=gegner_name,
                          nutzer_farbe=nutzer_farbe)

    def brett_ansehen(self, session_id: str) -> str:
        """Zeigt das aktuelle Schachbrett (ASCII) inkl. FEN und wer am Zug ist.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        """
        return self._call("brett_ansehen", session_id)

    def gegner_zug(self, session_id: str, zug: str) -> str:
        """Führt den wörtlich genannten Zug des Menschen aus.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        :param zug: SAN wie 'e4'/'Sf3' oder UCI wie 'e2e4'
        """
        return self._call("gegner_zug", session_id, zug=zug)

    def zug_machen(self, session_id: str) -> str:
        """Führt den eigenen Zug des LLM aus.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        """
        return self._call("zug_machen", session_id)

    def beste_zuege(self, session_id: str, n: int = 3) -> str:
        """Liefert die n besten Züge für die aktuelle Stellung.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        :param n: Anzahl der Züge (1-5)
        """
        return self._call("beste_zuege", session_id, n=n)

    def stellung_bewerten(self, session_id: str) -> str:
        """Kurze Engine-Einschätzung der aktuellen Stellung.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        """
        return self._call("stellung_bewerten", session_id)

    def zug_verlauf(self, session_id: str) -> str:
        """Listet alle bisherigen Züge der Partie auf.

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        """
        return self._call("zug_verlauf", session_id)

    def partie_aufgeben(self, session_id: str) -> str:
        """Beendet die laufende Partie (Aufgabe).

        :param session_id: Sitzungs-ID (von OpenWebUI bereitgestellt)
        """
        return self._call("partie_aufgeben", session_id)
