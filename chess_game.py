#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_game.py
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# Hauptprogramm: Spiel-Loop, in dem ein Mensch gegen ein LLM Schach spielt.
# Das LLM (beliebiges OpenAI-kompatibles API: llm-bahnhof, Ollama, ...) liefert
# Persönlichkeit und Zugwahl; Stockfish rechnet im Hintergrund und liefert
# die starken Kandidatenzüge. Ohne gültige LLM-Antwort greift der beste
# Stockfish-Zug (Fallback), das Spiel läuft also immer weiter.
#
# Start:   ./start.sh  oder  .venv/bin/python chess_game.py [Optionen]
# Optionen:
#   --engine=e2e4          User spielt diese Farbe (default: white)
#   --moves=N              Maximale Halbzüge pro Partie (default: 150)
#   --personality=TEXT     Zusätzliche Persönlichkeits-Note für das LLM
# ==============================================================================
import argparse
import configparser
import datetime
import os
import re
import sys
import time

import chess
import chess.pgn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from chess_engine import Stockfish          # noqa: E402
from llm_client import LLMClient, LLMError  # noqa: E402
from chess_prompts import (                 # noqa: E402
    PARSING_HINWEIS,
    SYSTEM_PROMPT,
)

UCI_RE = re.compile(r"^([a-h][1-8])([a-h][1-8])([qrbn])?$")


# --------------------------------------------------------------------------- #
def lade_konfig():
    """Liest chess.ini (KEY=VALUE, Sektion [chess])."""
    cfg = {
        "LLM_BASE_URL": "http://10.7.0.124:8000",
        "LLM_MODEL": "llm-bahnhof",
        "LLM_API_KEY": "",
        "LLM_TEMPERATURE": "0.7",
        "LLM_TIMEOUT": "120",
        "LLM_MAX_TOKENS": "300",
        "ENGINE_PATH": "engines/stockfish/stockfish-linux-x86-64-universal",
        "ENGINE_THREADS": "2",
        "ENGINE_HASH_MB": "64",
        "ENGINE_SKILL": "12",
        "ENGINE_MOVETIME_MS": "1200",
        "ENGINE_TOP_N": "3",
        "LLM_NAME": "Gambit",
        "LLM_STIL": "herzlich, humorvoll, ein wenig theatralisch",
    }
    pfad = os.path.join(BASE_DIR, "chess.ini")
    if os.path.exists(pfad):
        parser = configparser.ConfigParser()
        parser.read(pfad, encoding="utf-8")
        for key, wert in parser.items("chess") if parser.has_section("chess") else []:
            cfg[key.upper()] = wert
    return cfg


# --------------------------------------------------------------------------- #
def format_uci(zug):
    """Zug -> UCI-String; bei Promotion mit Buchstabe (z.B. e7e8q)."""
    u = zug.uci()
    if zug.promotion and u[-1] in "12345":
        u = u[:-1] + {chess.QUEEN: "q", chess.ROOK: "r", chess.BISHOP: "b",
                      chess.KNIGHT: "n"}.get(zug.promotion, "q")
    return u


def baue_brettgrafik(board):
    """Unicode-Brett mit Koordinaten (aus Schwarz-Sicht gespiegelt für Weiß)."""
    symbole = {
        "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
        "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
    }
    zeilen = []
    for reihe in range(7, -1, -1):
        zelle = [f" {reihe + 1} "]
        for spalte in range(8):
            figur = board.piece_at(chess.square(spalte, reihe))
            zelle.append(symbole.get(figur.symbol() if figur else "", "·"))
        zeilen.append(" ".join(zelle))
    zeilen.append("   a b c d e f g h")
    return "\n".join(zeilen)


def historie_san(board):
    """SAN-Historie der Partie (von der Grundstellung aus rekonstruiert)."""
    if not board.move_stack:
        return "(Partiebeginn)"
    wurzel = chess.Board()
    return wurzel.variation_san(board.move_stack)


def kandidaten_text(board, kandidaten, engine):
    """Baut die LLM-Textvorlage mit Stellung, Historie und Kandidatenzügen."""
    farbe = "weißen" if board.turn == chess.WHITE else "schwarzen"
    zeilen = [
        f"Aktuelle Stellung (FEN): {board.fen()}",
        f"Du spielst: {farbe.capitalize()}",
        "",
        f"Bisherige Züge (SAN): {historie_san(board)}",
        "",
        "Kandidatenzüge von Stockfish (bester zuerst):",
    ]
    for i, (zug, score) in enumerate(kandidaten, 1):
        san = board.san(zug)
        bewertung = engine.bewertungstext(score)
        zeilen.append(f"  {i}. {san}  ({format_uci(zug)})  Bewertung: {bewertung}")
    zeilen += [
        "",
        "Welchen Zug spielst Du? Antworte exakt im geforderten Format",
        "(ZUG: <uci> / KOMMENTAR: <Text>).",
    ]
    return "\n".join(zeilen)


def parse_zug_und_kommentar(antwort, board, erlaubt=None):
    """
    Extrahiert 'ZUG: <uci>' und 'KOMMENTAR: ...' aus der LLM-Antwort.
    Prüft alle ZUG:-Vorkommen der Reihe nach und nimmt den ersten Zug,
    der legal ist (und, wenn gesetzt, in der Kandidatenliste steht).
    Rückgabe: (zug_oder_None, kommentar, erster_roher_uci_oder_None)
    """
    kommentar = ""
    mk = re.search(r"KOMMENTAR\s*:\s*(.+)", antwort, re.IGNORECASE | re.DOTALL)
    if mk:
        kommentar = mk.group(1).strip()
    else:
        # Fallback: Text ohne 'KOMMENTAR:'-Präfix (alles nach der ZUG-Zeile)
        teile = re.split(r"ZUG\s*:\s*[a-h][1-8][a-h][1-8][qrbn]?\s*",
                         antwort, flags=re.IGNORECASE)
        if len(teile) > 1 and teile[-1].strip():
            kommentar = teile[-1].strip()

    # Kommentar aufräumen: keine verschachtelten Formatfragmente, Länge begrenzen
    kommentar = re.split(r"\bZUG\s*:", kommentar, flags=re.IGNORECASE)[0].strip()
    kommentar = re.sub(r"\bKOMMENTAR\s*:\s*", " ", kommentar,
                       flags=re.IGNORECASE).strip()
    if len(kommentar) > 400:
        kommentar = kommentar[:397].rstrip() + "..."

    zug = None
    uci_roh = None
    for m in re.finditer(r"ZUG\s*:\s*([a-h][1-8][a-h][1-8][qrbn]?)",
                         antwort, re.IGNORECASE):
        kandidat_uci = m.group(1).lower()
        if uci_roh is None:
            uci_roh = kandidat_uci
        try:
            kandidat = chess.Move.from_uci(kandidat_uci)
        except ValueError:
            continue
        if board.is_legal(kandidat) and (erlaubt is None or kandidat_uci in erlaubt):
            zug = kandidat
            break
        # Promotion-Default: Dame, falls das LLM den Buchstaben vergessen hat
        if len(kandidat_uci) == 4:
            kandidat5 = chess.Move.from_uci(kandidat_uci + "q")
            if board.is_legal(kandidat5) and (erlaubt is None or kandidat_uci + "q" in erlaubt):
                zug = kandidat5
                uci_roh = kandidat_uci + "q"
                break
    return zug, kommentar, uci_roh


# --------------------------------------------------------------------------- #
def llm_zug_sicher(board, kandidaten, engine, llm, chat, log):
    """
    Holt den LLM-Zug; bei wiederholt ungültiger Antwort greift der beste
    Stockfish-Zug (Fallback). Rückgabe: (zug, kommentar, quelle)
    quelle: 'llm' oder 'fallback'
    """
    erlaubt = {format_uci(z) for z, _ in kandidaten}

    for versuch in range(2):
        # Historie begrenzen: System-Prompt + die letzten 10 Einträge behalten
        if len(chat) > 11:
            del chat[1:-10]
        nachricht = kandidaten_text(board, kandidaten, engine)
        if versuch == 1:
            nachricht = PARSING_HINWEIS + "\n\n" + nachricht
        chat.append({"role": "user", "content": nachricht})

        try:
            antwort = llm.chat(chat)
        except LLMError as exc:
            log.warning("LLM-Fehler (%s) -> Stockfish-Fallback.", exc)
            return None, "", "fallback"

        zug, kommentar, uci_roh = parse_zug_und_kommentar(antwort, board, erlaubt)
        if zug is not None and format_uci(zug) in erlaubt:
            # Bereinigte Antwort in den Verlauf schreiben, damit sich
            # Degenerationen (Wiederholungs-Loops) nicht fortsetzen
            chat.append({"role": "assistant",
                         "content": f"ZUG: {format_uci(zug)}\nKOMMENTAR: {kommentar or '(kein Kommentar)'}"})
            return zug, kommentar, "llm"

        if uci_roh and uci_roh not in erlaubt:
            log.warning("LLM wählte ungültigen/illegalen Zug '%s' (Versuch %d).",
                        uci_roh, versuch + 1)
        else:
            log.warning("LLM-Antwort nicht parsebar (Versuch %d): %.120s",
                        versuch + 1, antwort.replace("\n", " | "))

    fallback = engine.bester_zug(board)
    log.info("Stockfish-Fallback-Zug: %s", fallback.uci() if fallback else "-")
    return fallback, "", "fallback"


# --------------------------------------------------------------------------- #
def partien_ende_text(board):
    """Kurzer Ergebnistext, wenn die Partie vorbei ist."""
    if board.is_checkmate():
        sieger = "Weiß" if not board.turn == chess.WHITE else "Schwarz"
        return f"Schachmatt – {sieger} gewinnt!"
    if board.is_stalemate():
        return "Patt – Remis."
    if board.is_insufficient_material():
        return "Unzureichendes Material – Remis."
    if board.can_claim_fifty_moves():
        return "50-Züge-Regel – Remis."
    if board.can_claim_threefold_repetition():
        return "Dreifache Stellungswiederholung – Remis."
    return None


def main():
    cfg = lade_konfig()

    ap = argparse.ArgumentParser(description="Schach: Mensch gegen LLM (Stockfish-Backend)")
    ap.add_argument("--engine", default="white", choices=["white", "black"],
                    help="Farbe des Menschen (default: white)")
    ap.add_argument("--moves", type=int, default=150, help="Max. Halbzüge (default: 150)")
    ap.add_argument("--personality", default="", help="Zusätzliche Persönlichkeits-Note")
    args = ap.parse_args()

    log = lambda msg: print(msg, flush=True)  # noqa: E731

    # ---- Engine + LLM starten -------------------------------------------- #
    engine = Stockfish(
        pfad=os.path.join(BASE_DIR, cfg["ENGINE_PATH"]) if not os.path.isabs(cfg["ENGINE_PATH"]) else cfg["ENGINE_PATH"],
        threads=int(cfg["ENGINE_THREADS"]),
        hash_mb=int(cfg["ENGINE_HASH_MB"]),
        skill=int(cfg["ENGINE_SKILL"]),
        movetime_ms=int(cfg["ENGINE_MOVETIME_MS"]),
        top_n=int(cfg["ENGINE_TOP_N"]),
    )
    llm = LLMClient(
        base_url=cfg["LLM_BASE_URL"],
        model=cfg["LLM_MODEL"],
        api_key=cfg["LLM_API_KEY"],
        temperature=float(cfg["LLM_TEMPERATURE"]),
        timeout=int(cfg["LLM_TIMEOUT"]),
        max_tokens=int(cfg["LLM_MAX_TOKENS"]),
    )

    board = chess.Board()
    user_weiss = (args.engine == "white")
    llm_name = cfg["LLM_NAME"]
    stil = cfg["LLM_STIL"] + (f"; {args.personality}" if args.personality else "")
    llm_farbe = "weißen" if not user_weiss else "schwarzen"

    chat = [{
        "role": "system",
        "content": SYSTEM_PROMPT.format(name=llm_name, farbe=llm_farbe, stil=stil),
    }]

    log("=" * 62)
    log(f"  Schach: Du ({'Weiß' if user_weiss else 'Schwarz'}) gegen {llm_name} "
        f"({'Weiß' if not user_weiss else 'Schwarz'}, LLM + Stockfish)")
    log(f"  Engine: Stockfish | LLM: {cfg['LLM_MODEL']} @ {cfg['LLM_BASE_URL']}")
    log("=" * 62)
    log(baue_brettgrafik(board))

    pgn_name = os.path.join(BASE_DIR, "partien",
                            datetime.datetime.now().strftime("partie_%Y%m%d_%H%M%S.pgn"))
    os.makedirs(os.path.dirname(pgn_name), exist_ok=True)
    pgn_game = chess.pgn.Game()
    pgn_game.headers["Event"] = "LLM vs. Mensch"
    pgn_game.headers["White"] = llm_name if not user_weiss else "Spieler"
    pgn_game.headers["Black"] = llm_name if user_weiss else "Spieler"
    pgn_game.headers["Date"] = datetime.datetime.now().strftime("%Y.%m.%d")
    node = pgn_game

    ende_grund = None

    try:
        while not board.is_game_over(claim_draw=False) and len(board.move_stack) < args.moves:
            if board.turn == chess.WHITE:
                log(f"\n[{len(board.move_stack) // 2 + 1}. Weiß am Zug]")
            else:
                log(f"\n[{len(board.move_stack) // 2 + 1}... Schwarz am Zug]")

            if (board.turn == chess.WHITE) == user_weiss:
                # ---- Menschlicher Zug ---------------------------------- #
                while True:
                    try:
                        roh = input("Dein Zug (z.B. e2e4 oder Sf3, 'exit' beendet): ").strip()
                    except (EOFError, KeyboardInterrupt):
                        roh = "exit"
                    if roh.lower() in ("exit", "quit", "q"):
                        log("Partie abgebrochen.")
                        ende_grund = "Abbruch durch Spieler"
                        break
                    zug = None
                    try:
                        zug = board.parse_san(roh)          # z.B. "Sf3"
                    except ValueError:
                        try:
                            m = UCI_RE.match(roh.lower())   # z.B. "g1f3"
                            if m:
                                kandidat = chess.Move.from_uci(m.group(0))
                                if board.is_legal(kandidat):
                                    zug = kandidat
                        except ValueError:
                            pass
                    if zug is None:
                        log("Ungültiger Zug – bitte erneut versuchen.")
                        continue
                    san = board.san(zug)
                    board.push(zug)
                    node = node.add_variation(zug)
                    log(f"Du spielst: {san}")
                    break
                if ende_grund:
                    break
            else:
                # ---- LLM-Zug (mit Stockfish-Kandidaten) ----------------- #
                kandidaten = engine.kandidaten(board)
                if not kandidaten:
                    ende_grund = "keine legalen Züge"
                    break
                t0 = time.time()
                zug, kommentar, quelle = llm_zug_sicher(board, kandidaten, engine, llm, chat, log)
                dauer = time.time() - t0
                if zug is None:
                    ende_grund = "kein Zug möglich"
                    break
                san = board.san(zug)
                board.push(zug)
                node = node.add_variation(zug)
                herkunft = "" if quelle == "llm" else "  [Stockfish-Fallback]"
                log(f"{llm_name} spielt: {san}  ({format_uci(zug)}, {dauer:.1f}s){herkunft}")
                if kommentar:
                    log(f"{llm_name} sagt: {kommentar}")

            log("")
            log(baue_brettgrafik(board))
            log(f"Stellungseinschätzung: {engine.einschaetzung(board)}")

        if not ende_grund:
            ende_grund = partien_ende_text(board) or \
                f"Zuglimit erreicht ({args.moves} Halbzüge) – abgebrochen/Remis."

        if board.is_checkmate():
            pgn_game.headers["Result"] = "1-0" if board.turn == chess.BLACK else "0-1"
        elif ende_grund and ("Remis" in ende_grund):
            pgn_game.headers["Result"] = "1/2-1/2"
        elif ende_grund and "Weiß gewinnt" in ende_grund:
            pgn_game.headers["Result"] = "1-0"
        elif ende_grund and "Schwarz gewinnt" in ende_grund:
            pgn_game.headers["Result"] = "0-1"

        log("\n" + "=" * 62)
        log(f"ENDE: {ende_grund}")
        log("=" * 62)
        exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True)
        pgn_text = pgn_game.accept(exporter)
        with open(pgn_name, "w", encoding="utf-8") as fh:
            fh.write(pgn_text + "\n")
        log(f"Partie gespeichert: {pgn_name}")

    finally:
        engine.schliessen()


if __name__ == "__main__":
    main()
