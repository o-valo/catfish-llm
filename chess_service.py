#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_service.py
# Projekt:   chess – LLM + Stockfish Schachanbindung (Tool-API)
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
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
JOURNAL_ORDNER = os.path.join(BASIS_ORDNER, "partien_journal")


# Deutsche Figurenkürzel auf die englischen SAN-Buchstaben abbilden.
# In englischer SAN kommen S/T/L/D nicht als Figur vor, die Umsetzung ist
# also eindeutig – englische Züge (Nf3, exd5) bleiben unverändert.
_DEUTSCH_ZU_SAN = str.maketrans({"S": "N", "T": "R", "L": "B", "D": "Q"})


def german_to_san(text):
    """Deutsche Notation in SAN überführen (Sf3 → Nf3, Lxf7 → Bxf7,
    0-0 → O-O, e8=D+ → e8=Q+).

    Englische SAN-Züge und UCI (e2e4) bleiben unverändert, damit beide
    Schreibweisen gleichberechtigt funktionieren.
    """
    t = (text or "").strip()
    if "-" in t and t[:1] in ("0", "O"):
        t = t.replace("0", "O")
    if t and t[0] in "STLD":
        t = t[0].translate(_DEUTSCH_ZU_SAN) + t[1:]
    if "=" in t:
        kopf, _, rest = t.partition("=")
        if rest[:1] in "STLD":
            t = kopf + "=" + rest[:1].translate(_DEUTSCH_ZU_SAN) + rest[1:]
    # UCI-Umwandlung mit deutschem Kürzel (e7e8d → e7e8q)
    if len(t) == 5 and t[4] in "STLDd":
        t = t[:4] + t[4].upper().translate(_DEUTSCH_ZU_SAN).lower()
    return t


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
    # Partie-Journal + ZUSTAND-Block (Grounding gegen LLM-Halluzinationen)
    # ------------------------------------------------------------------ #
    def _journal_datei(self, spiel):
        return os.path.join(JOURNAL_ORDNER, f"partie_{spiel['id']}.md")

    def _journal_anlegen(self, spiel):
        """Journal-Datei neu anlegen (beim Partiestart), best effort."""
        try:
            os.makedirs(JOURNAL_ORDNER, exist_ok=True)
            with open(self._journal_datei(spiel), "w", encoding="utf-8") as f:
                f.write(f"# Partie-Journal {spiel['id']}\n")
                f.write(f"Gestartet: {spiel['start_zeit']}\n")
                f.write(f"Weiß: {spiel['weiss_name']} | "
                        f"Schwarz: {spiel['schwarz_name']}\n\n")
        except Exception:
            pass  # Journalierung darf den Spielbetrieb nie blockieren

    def _journal_anhaengen(self, spiel, zeile):
        """Eine Zeile ans Journal anhängen, best effort."""
        try:
            with open(self._journal_datei(spiel), "a", encoding="utf-8") as f:
                f.write(zeile + "\n")
        except Exception:
            pass

    @staticmethod
    def _san_teile(brett):
        """Züge des Partieverlaufs als SAN-Liste."""
        temp = chess.Board()
        teile = []
        for zug in brett.move_stack:
            teile.append(temp.san(zug))
            temp.push(zug)
        return teile

    @staticmethod
    def _verlauf_text(brett):
        """Züge als nummerierte SAN-Zeile, z.B. '1. e4 e5 2. Sf3'."""
        if not brett.move_stack:
            return ""
        teile = SpielVerwaltung._san_teile(brett)
        nummeriert = []
        for i in range(0, len(teile), 2):
            nr = i // 2 + 1
            zeile = f"{nr}. {teile[i]}"
            if i + 1 < len(teile):
                zeile += f" {teile[i + 1]}"
            nummeriert.append(zeile)
        return " ".join(nummeriert)

    def _zustand_block(self, spiel):
        """Maßgeblicher Zustand, der an JEDES Tool-Ergebnis angehängt wird."""
        brett = spiel["brett"]
        du_bist = {"weiss": "Weiß", "schwarz": "Schwarz"}[spiel["eigene_farbe"]]
        am_zug = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
        verlauf = self._verlauf_text(brett)
        zeilen = [
            "=== ZUSTAND (maßgeblich) ===",
            f"Partie-ID: {spiel['id']} | Halbzüge: {len(brett.move_stack)}",
            f"DU BIST: {du_bist} | GEGNER: {spiel['gegner_name']}",
            f"Am Zug: {am_zug}",
        ]
        if verlauf:
            zeilen.append(f"Verlauf: {verlauf}")
        zeilen.append(f"FEN: {brett.fen()}")
        zeilen.append(f"Journal-Datei: partien_journal/partie_{spiel['id']}.md "
                      "(dort steht jeder Halbzug)")
        zeilen.append("REGELN: Diese Angaben sind verbindlich. Erfinde NIEMALS "
                      "Züge, Stellungen oder Verläufe; rufe für deinen Zug "
                      "'zug_machen' auf. Bei Unsicherheit: 'brett_ansehen' "
                      "oder 'zug_verlauf' aufrufen.")
        return "\n".join(zeilen)

    # ------------------------------------------------------------------ #
    # Werkzeuge (werden vom Proxy als Tool-Ergebnisse ans LLM gereicht)
    # ------------------------------------------------------------------ #
    def neue_partie(self, session_id, eigene_farbe="weiss", gegner_name="Mensch",
                    nutzer_farbe=""):
        """eigene_farbe = Farbe des LLM. Optional wird nutzer_farbe übergeben
        (explizit genannt), um Farbverwechslungen serverseitig abzufangen."""
        eig = (eigene_farbe or "weiss").strip().lower()
        if eig in ("weiss", "weiß", "white", "w"):
            eig, gegenseite = "weiss", "schwarz"
        else:
            eig, gegenseite = "schwarz", "weiss"
        # Farbverwechslung abfangen: Das Tool sagt immer, welche Farbe das LLM
        # hat. Nennt der Nutzer explizit seine eigene Farbe, muss diese der
        # Gegenseite entsprechen – sonst war 'eigene_farbe' falsch belegt.
        nutz = (nutzer_farbe or "").strip().lower()
        if nutz in ("weiss", "weiß", "white", "w") and eig != "schwarz":
            eig, gegenseite = "schwarz", "weiss"
        elif nutz in ("schwarz", "black", "b") and eig != "weiss":
            eig, gegenseite = "weiss", "schwarz"
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
                "letzte_mensch_san": None,  # letzter ausgeführter Menschen-Zug
                "weiss_name": name if eig == "schwarz" else "Gambit (LLM)",
                "schwarz_name": name if eig == "weiss" else "Gambit (LLM)",
                "start_zeit": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._spiele[sid] = spiel
            self._speichern(spiel)
            self._journal_anlegen(spiel)

        if eig == "weiss":
            kopf = (f"Neue Partie gestartet (ID {sid}). Du spielst mit Weiß, "
                    f"Gegner: {name}. Du bist am Zug – rufe 'zug_machen' auf, "
                    f"dann spiele ich meinen Zug.")
        else:
            kopf = (f"Neue Partie gestartet (ID {sid}). Du spielst mit Schwarz, "
                    f"Gegner: {name}. Der Mensch (Weiß) ist am Zug – warte auf "
                    f"seinen Zug oder sieh dir das Brett mit 'brett_ansehen' an.")
        return kopf + "\n\n" + self._zustand_block(spiel)

    def pruef_daten(self, session_id):
        """Maßgebliche Daten für die Proxy-Integritätsprüfung:
        gespielte SAN-Züge, aktuell legale SAN-Züge, wer am Zug ist."""
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            return {
                "gespielt": self._san_teile(brett),
                "legal": [brett.san(m) for m in brett.legal_moves],
                "am_zug": "Weiß" if brett.turn == chess.WHITE else "Schwarz",
                "du_bist": ("Weiß" if spiel["eigene_farbe"] == "weiss"
                            else "Schwarz"),
                "eigene_farbe": spiel["eigene_farbe"],
                "letzte_mensch_san": spiel.get("letzte_mensch_san"),
                "halbzuege": len(brett.move_stack),
                "beendet": brett.is_game_over(),
                "verlauf": self._verlauf_text(brett),
                "gespielt_uci": [m.uci() for m in brett.move_stack],
                "legal_uci": [m.uci() for m in brett.legal_moves],
            }

    def prompt_kontext(self, session_id):
        """Erzeugt den maßgeblichen Mini-RAG-Kontext für einen LLM-Aufruf.

        Der aktuelle Brettzustand wird immer aus dem Speicher berechnet. Das
        Journal dient als persistente Verlaufsquelle und wird nur ergänzend
        eingelesen; veralteter oder fehlender Journalinhalt kann den
        maßgeblichen FEN-/Verlaufsblock daher nicht überschreiben.
        """
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            eigene_farbe = ("Weiß" if spiel["eigene_farbe"] == "weiss"
                            else "Schwarz")
            am_zug = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
            verlauf = self._verlauf_text(brett) or "(Partiebeginn)"
            legal = ", ".join(brett.san(m) for m in brett.legal_moves)
            journal_datei = self._journal_datei(spiel)
            spiel_id = spiel["id"]
            letzte_mensch = spiel.get("letzte_mensch_san") or "(keiner)"

        try:
            with open(journal_datei, "r", encoding="utf-8") as f:
                journal = f.read().strip()
        except OSError:
            journal = "(Journaldatei momentan nicht verfügbar)"
        # Ein beschädigtes oder versehentlich sehr großes Journal darf den
        # Prompt nicht unbegrenzt anwachsen lassen.
        journal = journal[-12000:]

        return "\n".join([
            "=== PARTIE-KONTEXT (maßgeblich, vor jedem LLM-Aufruf aktualisiert) ===",
            f"Partie-ID: {spiel_id}",
            f"DU BIST: {eigene_farbe}",
            f"AM ZUG: {am_zug}",
            f"HALBZÜGE: {len(brett.move_stack)}",
            f"VERLAUF (Server): {verlauf}",
            f"FEN (Server): {brett.fen()}",
            f"LETZTER MENSCHENZUG: {letzte_mensch}",
            f"LEGALE ZÜGE JETZT (nur zur Orientierung): {legal}",
            "REGEL: Erfinde keinen Zug und behaupte keinen ausgeführten Zug.",
            "Wenn DU am Zug bist, rufe ausschließlich 'zug_machen' auf; "
            "der ausgeführte Zug steht danach im neuen PARTIE-KONTEXT.",
            "PERSISTENTES JOURNAL:",
            journal,
            "=== ENDE PARTIE-KONTEXT ===",
        ])

    def gegner_zug(self, session_id, zug):
        """Führt den Zug des MENSCHEN aus (immer gegen die eigene Farbe des
        LLM geprüft – gegen Farbverwechslungen)."""
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                raise ChessServiceError("Die Partie ist bereits beendet. "
                                        "Starte mit 'neue_partie' eine neue.")
            if brett.turn == chess.WHITE and spiel["eigene_farbe"] == "weiss":
                raise ChessServiceError(
                    "FARBE-FEHLER: Du (das LLM) spielst Weiß und bist gerade "
                    "am Zug. Für den Menschen gibt es hier keinen Zug – "
                    "siehe ZUSTAND-Block. Sein Zug gehört in eine Partie, in "
                    "der du Schwarz spielst.")
            if brett.turn == chess.BLACK and spiel["eigene_farbe"] == "schwarz":
                raise ChessServiceError(
                    "FARBE-FEHLER: Du (das LLM) spielst Schwarz und bist gerade "
                    "am Zug. Für den Menschen gibt es hier keinen Zug – "
                    "siehe ZUSTAND-Block. Sein Zug gehört in eine Partie, in "
                    "der du Weiß spielst.")
            move = self._parse_zug(brett, zug)
            if move is None:
                legale = ", ".join(brett.san(m)
                                   for m in list(brett.legal_moves)[:20])
                raise ChessServiceError(
                    f"Ungültiger oder illegaler Zug '{zug}' für den Menschen. "
                    f"Legale Züge (Auszug): {legale}")
            san = brett.san(move)
            spiel["pgn_node"] = spiel["pgn_node"].add_variation(move)
            brett.push(move)
            spiel["letzte_mensch_san"] = san
            self._speichern(spiel)
            gezogen_von = "Weiß" if brett.turn == chess.BLACK else "Schwarz"
            nr = (len(brett.move_stack) + 1) // 2
            self._journal_anhaengen(
                spiel,
                f"{nr}. {san}" if gezogen_von == "Weiß" else f"{nr}... {san}")
            if brett.is_game_over():
                self._journal_anhaengen(
                    spiel,
                    f"[Partie beendet – Ergebnis: {brett.result(claim_draw=True)}]")
            status = self._status_nach_zug(
                brett, f"Zug des Menschen: {san}.")
        return status + "\n\n" + self._zustand_block(spiel)

    def brett_ansehen(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            am_zug = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
            grafik = self._brettgrafik(brett)
            text = (f"Stellung nach {len(brett.move_stack)} Halbzügen. "
                    f"{am_zug} ist am Zug.\n\n{grafik}\n\nFEN: {brett.fen()}")
            return text + "\n\n" + self._zustand_block(spiel)

    def zug_machen(self, session_id, zug=""):
        """Eigener Zug des LLM – nur ausgeführt, wenn die EIGENE Farbe am Zug
        ist. Der Parameter 'zug' wird ignoriert: Das LLM gibt keinen Zugtext
        vor, sondern spielt über sein eigenes Werkzeug.
        """

        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                raise ChessServiceError("Die Partie ist bereits beendet. "
                                        "Starte mit 'neue_partie' eine neue.")
            am_zug = "Weiß" if brett.turn == chess.WHITE else "Schwarz"
            du_bist = "Weiß" if spiel["eigene_farbe"] == "weiss" else "Schwarz"
            if brett.turn != (chess.WHITE
                              if spiel["eigene_farbe"] == "weiss"
                              else chess.BLACK):
                raise ChessServiceError(
                    f"NICHT-DEIN-ZUG: Du spielst {du_bist}, aber aktuell ist "
                    f"{am_zug} am Zug (der Mensch). Warte auf seinen Zug bzw. "
                    "führe 'gegner_zug' aus, wenn er dir einen Zug nennt. "
                    "Führe NIEMALS Züge für den Menschen aus.")
            # Der Zugtext wird bewusst ignoriert; das Spielsystem wählt den
            # gültigen Zug, damit die Partie regelkonform bleibt.
            kandidaten = self.engine.kandidaten(brett, n=1)
            if not kandidaten:
                raise ChessServiceError(
                    "Es konnte kein legaler Zug ermittelt werden. "
                    "Siehe ZUSTAND-Block.")
            move = kandidaten[0][0]
            san = brett.san(move)
            spiel["pgn_node"] = spiel["pgn_node"].add_variation(move)
            brett.push(move)
            self._speichern(spiel)
            # Journal: Halbzug in PGN-Notation anhängen (1. e4 / 1... e5)
            gezogen_von = "Weiß" if brett.turn == chess.BLACK else "Schwarz"
            nr = (len(brett.move_stack) + 1) // 2
            self._journal_anhaengen(
                spiel,
                f"{nr}. {san}" if gezogen_von == "Weiß" else f"{nr}... {san}")
            if brett.is_game_over():
                self._journal_anhaengen(
                    spiel,
                    f"[Partie beendet – Ergebnis: {brett.result(claim_draw=True)}]")
        return (self._status_nach_zug(
                    spiel["brett"],
                    f"Dein Zug: {san}.")
                + "\n\n" + self._zustand_block(spiel))

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
            zeilen = ["Meine besten Züge für die aktuelle Stellung:"]
            for i, (zug, score_weiss) in enumerate(kandidaten, 1):
                san = brett.san(zug)
                pov_score = chess.engine.PovScore(score_weiss, chess.WHITE)
                score_am_zug = pov_score.pov(brett.turn)
                bew = self._bewertungstext_am_zug(score_am_zug)
                zeilen.append(f"{i}. {san} ({bew})")
        return "\n".join(zeilen) + "\n\n" + self._zustand_block(spiel)

    def stellung_bewerten(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            brett = spiel["brett"]
            if brett.is_game_over():
                text = "Die Partie ist beendet, keine Bewertung mehr möglich."
                return text + "\n\n" + self._zustand_block(spiel)
            k = self.engine.kandidaten(brett, n=1)
        if not k:
            return "Keine Bewertung möglich.\n\n" + self._zustand_block(spiel)
        score_weiss = k[0][1]
        bew = Stockfish.bewertungstext(score_weiss)
        text = (f"Stellungsbewertung (Weiß-Sicht): {bew}. "
                f"{'Weiß' if brett.turn == chess.WHITE else 'Schwarz'} ist am Zug.")
        return text + "\n\n" + self._zustand_block(spiel)

    def zug_verlauf(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            verlauf = self._verlauf_text(spiel["brett"])
        text = ("Partieverlauf: " + verlauf) if verlauf else \
            "Noch keine Züge in dieser Partie."
        return text + "\n\n" + self._zustand_block(spiel)

    def partie_aufgeben(self, session_id):
        spiel = self._spiel(session_id)
        with self._lock:
            sid = spiel["id"]
            farbe = spiel["eigene_farbe"]
            spiel["partie"].headers["Result"] = "0-1" if farbe == "weiss" else "1-0"
            del self._spiele[sid]
        self._speichern(spiel)
        self._journal_anhaengen(spiel, "[Partie aufgegeben – beendet]")
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
        # 1) SAN direkt (englische Notation: O-O, e4, Nf3, exd5 ...)
        try:
            return brett.parse_san(text)
        except Exception:
            pass
        # 1b) Deutsche Notation gleichwertig (Sf3, Lxf7, Td8, Dd5, e8=D+)
        uebersetzt = german_to_san(text)
        if uebersetzt != text:
            try:
                return brett.parse_san(uebersetzt)
            except Exception:
                pass
        # 2) SAN mit UCI-artiger Rochade ("e1g1" -> O-O), falls nötig
        if text.lower() in ("e1g1", "e1c1", "e8g8", "e8c8"):
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
        return [(z, chess.engine.PovScore(engine_dummy_score(brett),
                                          chess.WHITE))
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
