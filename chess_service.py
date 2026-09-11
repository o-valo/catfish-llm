#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_service.py
# Projekt:   chess – LLM + Stockfish Schachanbindung (Tool-API)
# ==============================================================================
# Spielverwaltung und die eigentlichen Schach-Werkzeuge, die dem LLM über den
# Proxy zur Verfügung stehen. Thread-sicher (Flask/waitress ist multithreaded),
# mehrere gleichzeitige Partien über session_id möglich.
#
# Verfügbare Tools für das LLM:
#   neue_partie        – startet eine Partie (Farbe wählen, Gegnername)
#   brett_ansehen      – Brett in ASCII + FEN + wer am Zug ist
#   zug_machen         – eigenen Zug spielen (SAN oder UCI), antwortet mit
#                        neuem Brett + Status (Schach/Matt/Patt/Remis)
#   beste_zuege        – Stockfish-Analyse: N beste Züge mit Bewertung
#   stellung_bewerten  – kurze Engine-Einschätzung (Centipawns/Matt)
#   zug_verlauf        – Züge dieser Partie (SAN, nummeriert)
#   partie_aufgeben    – Partie beenden/aufgeben
# ==============================================================================
import configparser
import os
import threading
import time
import uuid

import chess
import chess.pgn

from chess_engine import Stockfish

BASIS_ORDNER = os.path.dirname(os.path.abspath(__file__))
PARTIEN_ORDNER = os.path.join(BASIS_ORDNER, "partien_proxy")


class ChessServiceError(Exception):
    """Fehler, der als Tool-Ergebnis an das LLM zurückgegeben wird."""


class SpielVerwaltung:
    """Verwaltet mehrere gleichzeitige Partien (session_id -> Spielzustand)."""

    def __init__(self, engine):
        self.engine = engine
        self._lock = threading.RLock()
        self._spiele = {}

    # ------------------------------------------------------------------ #
    # Interne Helfer
    # ------------------------------------------------------------------ #
    def _spiel(self, session_id):
        with self._lock:
            spiel = self._spiele.get(session_id)
            if not spiel:
                raise ChessServiceError(
                    "Keine aktive Partie. Rufe zuerst das Tool 'neue_partie' auf.")
            return spiel

    def _speichern(self, spiel):
        """Partie als PGN ins Archiv schreiben (best effort)."""
        if not spiel:
            return
        try:
            os.makedirs(PARTIEN_ORDNER, exist_ok=True)
            brett = spiel["brett"]
            spiel["partie"].headers["White"] = spiel["weiss_name"]
            spiel["partie"].headers["Black"] = spiel["schwarz_name"]
            spiel["partie"].headers["Event"] = "chess-proxy (LLM + Stockfish)"
            spiel["partie"].headers["Site"] = "chess-proxy"
            spiel["partie"].headers["Date"] = spiel["start_zeit"][:10].replace("-", ".")
            if brett.is_game_over():
                if brett.is_checkmate():
                    spiel["partie"].headers["Result"] = "0-1" if brett.turn == chess.WHITE else "1-0"
                else:
                    spiel["partie"].headers["Result"] = "1/2-1/2"
            datei = os.path.join(PARTIEN_ORDNER, f"partie_{spiel['id']}.pgn")
            with open(datei, "w", encoding="utf-8") as f:
                print(spiel["partie"], file=f)
        except Exception:
            pass  # Archivierung darf den Spielbetrieb nie blockieren

    @staticmethod
    def _brettgrafik(brett):
        from chess_game import baue_brettgrafik
        return baue_brettgrafik(brett)

    # ------------------------------------------------------------------ #
    # Werkzeuge (werden vom Proxy als Tool-Ergebnisse ans LLM gereicht)
    # ------------------------------------------------------------------ #
    def neue_partie(self, session_id, eigene_farbe="weiss", gegner_name="Mensch"):
        eig = (eigene_farbe or "weiss").strip().lower()
        if eig in ("weiss", "weiß", "white", "w"):
            eig, gegenseite = "weiss", "schwarz"
        else:
            eig, gegenseite = "schwarz", "weiss"
        name = (gegner_name or "Mensch").strip()[:40] or "Mensch"

        with self._lock:
            if session_id and session_id in self._spiele:
                self._speichern(self._spiele[session_id])
            sid = session_id or uuid.uuid4().hex[:8]
            partie = chess.pgn.Game()
            spiel = {
                "id": sid,
                "brett": chess.Board(),
                "partie": partie,
                "pgn_node": partie,  # aktueller Knoten für add_variation()
                "eigene_farbe": eig,
                "gegner_name": name,
                "weiss_name": name if eig == "schwarz" else "Gambit (LLM)",
                "schwarz_name": name if eig == "weiss" else "Gambit (LLM)",
                "start_zeit": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._spiele[sid] = spiel
            self._speichern(spiel)

        if eig == "weiss":
            return (f"Neue Partie gestartet (ID {sid}). Du spielst mit Weiß, "
                    f"Gegner: {name}. Du bist am Zug – überlege dir einen "
                    f"Eröffnungszug und spiele ihn mit 'zug_machen'.")
        return (f"Neue Partie gestartet (ID {sid}). Du spielst mit Schwarz, "
                f"Gegner: {name}. Der Mensch (Weiß) ist am Zug – warte auf "
                f"seinen Zug oder sieh dir das Brett mit 'brett_ansehen' an.")

    def brett_ansehen(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            am_zug = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
            grafik = self._brettgrafik(brett)
            return (f"Stellung nach {len(brett.move_stack)} Halbzügen. "
                    f"{am_zug} ist am Zug.\n\n{grafik}\n\nFEN: {brett.fen()}")

    def zug_machen(self, session_id, zug):
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                raise ChessServiceError("Die Partie ist bereits beendet. "
                                        "Starte mit 'neue_partie' eine neue.")
            move = self._parse_zug(brett, zug)
            if move is None:
                legale = ", ".join(brett.san(m)
                                   for m in list(brett.legal_moves)[:20])
                raise ChessServiceError(
                    f"Ungültiger oder illegaler Zug '{zug}'. "
                    f"Legale Züge (Auszug): {legale}")
            san = brett.san(move)
            spiel["pgn_node"] = spiel["pgn_node"].add_variation(move)
            brett.push(move)
            self._speichern(spiel)
        return self._status_nach_zug(spiel["brett"], f"Dein Zug: {san}.")

    def beste_zuege(self, session_id, n=3):
        spiel = self._spiel(session_id)
        try:
            n = max(1, min(int(n or 3), 5))
        except (TypeError, ValueError):
            n = 3
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                raise ChessServiceError("Die Partie ist bereits beendet.")
            kandidaten = self.engine.kandidaten(brett, n=n)
            if not kandidaten:
                raise ChessServiceError("Keine Kandidatenzüge gefunden.")
            zeilen = ["Beste Züge laut Stockfish (aus Sicht des Spielers am Zug):"]
            for i, (zug, score_weiss) in enumerate(kandidaten, 1):
                san = brett.san(zug)
                pov_score = chess.engine.PovScore(score_weiss, chess.WHITE)
                score_am_zug = pov_score.pov(brett.turn)
                bew = self._bewertungstext_am_zug(score_am_zug)
                zeilen.append(f"{i}. {san} ({bew})")
        return "\n".join(zeilen)

    def stellung_bewerten(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                return "Die Partie ist beendet, keine Bewertung mehr möglich."
            k = self.engine.kandidaten(brett, n=1)
        if not k:
            return "Keine Bewertung möglich."
        score_weiss = k[0][1]
        bew = Stockfish.bewertungstext(score_weiss)
        return (f"Stellungsbewertung (Weiß-Sicht): {bew}. "
                f"{'Weiß' if brett.turn == chess.WHITE else 'Schwarz'} ist am Zug.")

    def zug_verlauf(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            stack = list(spiel["brett"].move_stack)
        if not stack:
            return "Noch keine Züge in dieser Partie."
        temp = chess.Board()
        teile = []
        for zug in stack:
            teile.append(temp.san(zug))
            temp.push(zug)
        nummeriert = []
        for i in range(0, len(teile), 2):
            nr = i // 2 + 1
            weiss = teile[i]
            schwarz = teile[i + 1] if i + 1 < len(teile) else ""
            nummeriert.append(f"{nr}. {weiss} {schwarz}".rstrip())
        return "Partieverlauf: " + " ".join(nummeriert)

    def partie_aufgeben(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            sid = spiel["id"]
            farbe = spiel["eigene_farbe"]
            spiel["partie"].headers["Result"] = "0-1" if farbe == "weiss" else "1-0"
            del self._spiele[sid]
        self._speichern(spiel)
        return (f"Partie {sid} wurde beendet (aufgegeben). "
                f"Du kannst mit 'neue_partie' sofort neu beginnen.")

    # ------------------------------------------------------------------ #
    # Hilfsfunktionen
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_zug(brett, zug_text):
        """Akzeptiert SAN (e4, Nf3, O-O) und UCI (e2e4, e7e8q) mit Toleranz."""
        text = (zug_text or "").strip().strip('"').strip("'").strip()
        if not text:
            return None
        # 1) SAN direkt (akzeptiert auch O-O, e4, Nf3, exd5 ...)
        try:
            return brett.parse_san(text)
        except Exception:
            pass
        # 2) SAN mit UCI-artiger Rochade ("e1g1" -> O-O), falls nötig
        if text.lower() in ("e1g1", "e1c1", "e8g1", "e8c1"):
            try:
                return brett.parse_san("O-O" if text.lower().endswith("g1") else "O-O-O")
            except Exception:
                pass
        # 3) UCI inkl. Promotion (z.B. e7e8q)
        uci = text.lower().replace(" ", "")
        try:
            move = chess.Move.from_uci(uci)
            if move in brett.legal_moves:
                return move
        except Exception:
            pass
        return None

    @staticmethod
    def _bewertungstext_am_zug(score_am_zug):
        """Bewertung aus Sicht des Spielers am Zug, z.B. '+0.42' oder 'Matt in 3'."""
        if score_am_zug.is_mate():
            m = score_am_zug.mate()
            if m > 0:
                return f"Matt in {m}"
            return f"Matt in {abs(m)} (für den Gegner)"
        cp = score_am_zug.score(mate_score=100000) / 100.0
        return f"{cp:+.2f}"

    @staticmethod
    def _am_zug(brett):
        return "Weiß" if brett.turn == chess.WHITE else "Schwarz"

    def _status_nach_zug(self, brett, einleitung):
        if brett.is_checkmate():
            verlierer = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
            return f"{einleitung} SCHACHMATT! {verlierer} ist matt. Partie beendet."
        if brett.is_stalemate():
            return f"{einleitung} PATT – die Partie endet remis."
        if brett.is_insufficient_material():
            return f"{einleitung} Remis – ungenügendes Material."
        if brett.can_claim_fifty_moves():
            return f"{einleitung} Remis durch 50-Züge-Regel beantragbar."
        if brett.is_check():
            return f"{einleitung} SCHACH! {self._am_zug(brett)} ist am Zug."
        return f"{einleitung} {self._am_zug(brett)} ist am Zug."


# ============================================================================== #
# Fabrik-Funktion (Dependency-Injection für Tests)
# ============================================================================== #
class NullEngine:
    """Minimal-Engine-Ersatz für Unit-Tests ohne Stockfish."""

    def kandidaten(self, brett, n=None):
        zuege = list(brett.legal_moves)[: (n or 3)]
        return [(z, chess.PovScore(chess.engine_dummy_score(brett), chess.WHITE))
                for z in zuege]


def engine_dummy_score(brett):  # pragma: no cover - nur für Tests
    class _Score:
        def is_mate(self):
            return False

        def score(self, mate_score=0):
            return 0

    return _Score()


def baue_service():
    """Erzeugt SpielVerwaltung mit echter Stockfish-Engine aus chess.ini."""
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(BASIS_ORDNER, "chess.ini"))
    s = cfg["chess"] if cfg.has_section("chess") else {}
    pfad = os.path.join(
        BASIS_ORDNER,
        s.get("ENGINE_PATH", "engines/stockfish/stockfish-linux-x86-64-universal"))
    engine = Stockfish(
        pfad,
        threads=int(s.get("ENGINE_THREADS", 2)),
        hash_mb=int(s.get("ENGINE_HASH_MB", 64)),
        skill=int(s.get("ENGINE_SKILL", 12)),
        movetime_ms=int(s.get("ENGINE_MOVETIME_MS", 1200)),
        top_n=int(s.get("ENGINE_TOP_N", 3)),
    )
    return SpielVerwaltung(engine)
