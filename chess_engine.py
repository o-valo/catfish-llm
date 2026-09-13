#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_engine.py
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Wrapper um die Stockfish-Engine (UCI) via python-chess.
#   - Liefert die N besten Kandidatenzüge (MultiPV) für die aktuelle Stellung
#   - Berechnet Bewertungen (Centipawns / Matt-in-N)
#   - Gewichtetes Auswählen unter den Topzügen (Varianz zwischen Partien)
# ==============================================================================
import os
import random

import chess
import chess.engine


class Stockfish:
    def __init__(self, pfad, threads=2, hash_mb=64, skill=12,
                 movetime_ms=1200, top_n=3):
        self.pfad = os.path.abspath(os.path.expanduser(pfad))
        if not os.path.exists(self.pfad):
            raise FileNotFoundError(f"Stockfish-Binary nicht gefunden: {self.pfad}")
        self.limit = chess.engine.Limit(time=movetime_ms / 1000.0)
        self.top_n = max(1, int(top_n))
        self.engine = chess.engine.SimpleEngine.popen_uci(self.pfad)
        self.engine.configure({
            "Threads": int(threads),
            "Hash": int(hash_mb),
            "Skill Level": int(skill),
        })

    # ------------------------------------------------------------------ #
    def kandidaten(self, board, n=None):
        """
        Liefert die n besten Züge als Liste (bester zuerst):
            [(zug, score_weiss_pov), ...]
        score ist ein chess.engine.PovScore (aus White-Sicht).
        """
        n = n or self.top_n
        anzahl = board.legal_moves.count()
        if anzahl == 0:  # Partie vorbei (Matt/Patt) -> keine Kandidaten
            return []
        n = max(1, min(n, anzahl))
        infos = self.engine.analyse(board, self.limit, multipv=n)
        if isinstance(infos, dict):  # multipv=1 liefert ein einzelnes Dict
            infos = [infos]
        ergebnis = []
        for info in infos:
            pv = info.get("pv") or []
            score = info.get("score")
            if pv and score is not None:
                ergebnis.append((pv[0], score.pov(chess.WHITE)))
        return ergebnis

    def bester_zug(self, board):
        """Einziger bester Zug (Fallback, wenn das LLM nichts Gültiges liefert)."""
        k = self.kandidaten(board, n=1)
        return k[0][0] if k else None

    def zufalls_kandidat(self, kandidaten):
        """
        Wählt gewichtet zufällig unter den Kandidaten (bester Zug am wahrscheinlichsten).
        Gewichte: 0.72 / 0.18 / 0.10 (restliche Züge teilen den Rest).
        """
        if not kandidaten:
            return None
        if len(kandidaten) == 1:
            return kandidaten[0][0]
        basis = [0.72, 0.18, 0.10]
        gewichte = basis[:len(kandidaten)]
        rest = 1.0 - sum(gewichte)
        if len(kandidaten) > len(basis):
            gewichte += [rest / (len(kandidaten) - len(basis))] * (len(kandidaten) - len(basis))
        else:
            faktor = 1.0 / sum(gewichte)
            gewichte = [g * faktor for g in gewichte]
        zuege = [k[0] for k in kandidaten]
        return random.choices(zuege, weights=gewichte, k=1)[0]

    # ------------------------------------------------------------------ #
    @staticmethod
    def bewertungstext(score_weiss):
        """Menschenlesbare Bewertung aus White-Sicht, z.B. '+0.42' oder 'Matt in 3'."""
        if score_weiss.is_mate():
            m = score_weiss.mate()
            if m > 0:
                return f"Matt in {m} (Weiß)"
            return f"Matt in {abs(m)} (Schwarz)"
        cp = score_weiss.score(mate_score=100000) / 100.0
        return f"{cp:+.2f}"

    def einschaetzung(self, board):
        """Kurze Engine-Einschätzung der aktuellen Stellung (als Text)."""
        k = self.kandidaten(board, n=1)
        if not k:
            return "keine Bewertung"
        return self.bewertungstext(k[0][1])

    # ------------------------------------------------------------------ #
    def schliessen(self):
        try:
            self.engine.quit()
        except Exception:
            pass
