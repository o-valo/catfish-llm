#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_shell.py
# Projekt:   catfish-llm – LLM + Stockfish Schachanbindung
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# Im Terminal gegen catfish-llm spielen – ohne OpenWebUI.
#
# Der Client spricht dieselbe OpenAI-kompatible API wie OpenWebUI
# (/v1/chat/completions) und zeichnet das Brett lokal aus der FEN, die der
# Server im ZUSTAND-Block liefert. Kurze Befehle (brett, verlauf, beste …)
# laufen direkt über die REST-API des Proxys und brauchen keine LLM-Runde.
#
# Aufruf:
#   ./chess_shell.py                       # Konfiguration aus chess.ini
#   ./chess_shell.py --url http://10.7.0.116:8300
#   ./chess_shell.py --farbe schwarz       # du spielst Schwarz
#   ./chess_shell.py --figuren buchstaben  # statt Figurenzeichen (K Q R B N P)
#   ./chess_shell.py --hintergrund dunkel  # Terminal-Grund (auto|hell|dunkel)
#   ./chess_shell.py --session abend-1     # feste Partie-ID (weiterspielen)
#   ./chess_shell.py --help
# ==============================================================================
import argparse
import configparser
import os
import re
import sys
import threading
import time

BASIS_ORDNER = os.path.dirname(os.path.abspath(__file__))


def _venv_python():
    """Pfad zum Python der Projekt-venv (Windows-Scripts-Ordner berücksichtigt)."""
    if sys.platform.startswith("win"):
        return os.path.join(BASIS_ORDNER, ".venv", "Scripts", "python.exe")
    return os.path.join(BASIS_ORDNER, ".venv", "bin", "python")


# Wird der Client mit dem System-Python gestartet (typisch: ./chess_shell.py),
# fehlen python-chess/requests. Dann einmalig mit der Projekt-venv neu starten,
# damit der Aufruf trotzdem funktioniert. Die Umgebungsvariable verhindert eine
# Endlosschleife, falls auch die venv die Pakete nicht hat.
if not os.environ.get("CHESS_SHELL_VENV"):
    try:
        import chess  # noqa: F401
        import requests  # noqa: F401
    except ImportError:
        _py = _venv_python()
        # Über sys.prefix vergleichen, nicht über realpath(sys.executable): in einer
        # venv ist bin/python oft nur ein Symlink auf das System-Python, sodass
        # realpath beide gleich aussehen lässt (Endlosschleife).
        _venv_aktiv = os.path.realpath(sys.prefix) == os.path.realpath(
            os.path.join(BASIS_ORDNER, ".venv")
        )
        if os.path.exists(_py) and not _venv_aktiv:
            os.execve(
                _py,
                [_py, os.path.abspath(__file__), *sys.argv[1:]],
                dict(os.environ, CHESS_SHELL_VENV="1"),
            )
        sys.exit(
            "Fehlende Abhängigkeiten (python-chess, requests).\n"
            "Einmalig einrichten mit:  ./install.sh\n"
            "Oder mit der Projekt-venv starten:  .venv/bin/python chess_shell.py"
        )

import chess
import requests

FIGUREN = {"P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
           "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚"}
# Buchstaben statt Zeichen: in jeder Schrift und auf jedem Grund eindeutig.
FIGUREN_BUCHSTABEN = {k: k for k in "PNBRQKpnbrqk"}
# ANSI nur für den dunklen Grund: helles Weiß bzw. Grau.
ANSI = {"weiss": "\x1b[97m", "schwarz": "\x1b[90m", "aus": "\x1b[0m"}


def osc11_hintergrund(frist=0.25):
    """Terminal nach seiner Hintergrundfarbe fragen (OSC 11).

    Antworten xterm, kitty, wezterm, iTerm2, VTE und Co. mit „rgb:…“, ist das
    die verlässlichste Auskunft – besser als jede Umgebungsvariable.
    Rückgabe: 'hell', 'dunkel' oder '' (keine Antwort).
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return ""
    try:
        import select
        import termios
        import tty
    except ImportError:                 # z. B. Windows
        return ""
    fd = sys.stdin.fileno()
    alt = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write("\x1b]11;?\x07")
        sys.stdout.flush()
        puffer, ende = "", time.time() + frist
        while time.time() < ende:
            bereit, _, _ = select.select([fd], [], [], max(0.0, ende - time.time()))
            if not bereit:
                break
            puffer += os.read(fd, 64).decode("utf-8", "replace")
            if "\x07" in puffer or "\x1b\\" in puffer:
                break
    except Exception:
        return ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, alt)
    treffer = re.search(
        r"rgb:([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})/([0-9a-fA-F]{2,4})", puffer)
    if not treffer:
        return ""
    r, g, b = (int(x[:2], 16) for x in treffer.groups())
    return "hell" if (0.299 * r + 0.587 * g + 0.114 * b) > 127 else "dunkel"


def colorfgbg_hintergrund():
    """COLORFGBG auswerten (rxvt, Konsole …): 'fg;bg', Hintergrund 0–6 = dunkel."""
    wert = os.environ.get("COLORFGBG", "").strip()
    if ";" in wert:
        letzte = wert.split(";")[-1].strip()
        if letzte.isdigit():
            return "hell" if int(letzte) >= 7 else "dunkel"
    return ""


def hintergrund_ermitteln():
    """'hell', 'dunkel' oder '' – erst das Terminal fragen, dann die Umgebung."""
    return osc11_hintergrund() or colorfgbg_hintergrund()


def brett_text(fen, invertiert=False, figuren="unicode", farbig=False):
    """Brett als Text – aus Sicht des angegebenen Spielers.

    Bewusst selbst gezeichnet: `chess.Board.unicode(invert_color=True)`
    vertauscht nur die Figurenfarben und dreht das Brett NICHT, ein
    Schwarz-Spieler sähe also seine Figuren oben und falsch eingefärbt.

    `figuren="buchstaben"` zeichnet K/Q/R/B/N/P (weiß) bzw. k/q/r/b/n/p
    (schwarz) – nötig, wenn die Schrift beide Zeichensätze gleich darstellt.
    `farbig=True` färbt weiße Figuren hell und schwarze gedimmt: Unicode
    zeichnet ♟ gefüllt und ♙ nur als Umriss, beides in der Vordergrundfarbe
    – auf dunklem Grund wirken die Farben sonst vertauscht.
    """
    brett = chess.Board(fen)
    zeichen = FIGUREN_BUCHSTABEN if str(figuren).lower().startswith("b") else FIGUREN
    reihen = range(1, 9) if invertiert else range(8, 0, -1)
    dateien = "hgfedcba" if invertiert else "abcdefgh"
    trenner = "  +" + "---+" * 8
    zeilen = [trenner]
    for reihe in reihen:
        zellen = []
        for datei in dateien:
            feld = chess.square(chess.FILE_NAMES.index(datei), reihe - 1)
            figur = brett.piece_at(feld)
            if figur is None:
                zellen.append("   ")
                continue
            zeichenreihe = zeichen[figur.symbol()]
            if farbig and zeichen is FIGUREN:
                zeichenreihe = (ANSI["weiss" if figur.color else "schwarz"]
                                + zeichenreihe + ANSI["aus"])
            zellen.append(" " + zeichenreihe + " ")
        zeilen.append(f"{reihe} |" + "|".join(zellen) + "|")
        zeilen.append(trenner)
    zeilen.append("    " + "   ".join(dateien))
    return "\n".join(zeilen)


BEFEHLE = """\
Lokale Befehle (ohne LLM-Runde):
  brett,  b        Brett anzeigen
  verlauf, v       Zugverlauf + Brett
  beste [n]        die n besten Züge (Stockfish)
  bewerten         Stellung bewerten
  neu              neue Partie starten
  aufgeben         Partie aufgeben
  hilfe, h, ?      diese Hilfe
  exit, quit, q    beenden

Alles andere wird als Nachricht an den Gegner geschickt – also einfach
"e2e4", "Sf3", "O-O" oder ein ganzer Satz."""


class Spinner:
    """Kleine Warteanzeige, damit die LLM-Runde nicht wie ein Hänger wirkt."""

    def __init__(self, text="überlegt"):
        self.text = text
        self._stopp = threading.Event()
        self._thread = None

    def __enter__(self):
        if not sys.stdout.isatty():
            print(f"  ({self.text} …)", flush=True)
            return self
        self._thread = threading.Thread(target=self._drehen, daemon=True)
        self._thread.start()
        return self

    def _drehen(self):
        for zeichen in "|/-\\":
            if self._stopp.is_set():
                break
            sys.stdout.write(f"\r  {zeichen} {self.text} …")
            sys.stdout.flush()
            time.sleep(0.12)

    def __exit__(self, *_):
        self._stopp.set()
        if self._thread:
            self._thread.join(timeout=1)
            sys.stdout.write("\r" + " " * (len(self.text) + 8) + "\r")
            sys.stdout.flush()


# ---------------------------------------------------------------------------- #
# Konfiguration
# ---------------------------------------------------------------------------- #
def konfiguration():
    datei = os.path.join(BASIS_ORDNER, "chess.ini")
    cfg = configparser.ConfigParser()
    cfg.read(datei)
    proxy = cfg["proxy"] if cfg.has_section("proxy") else {}
    chess_teil = cfg["chess"] if cfg.has_section("chess") else {}
    host = proxy.get("PROXY_HOST", "127.0.0.1").strip() or "127.0.0.1"
    # Bindet der Proxy an alle Interfaces, ist lokal 127.0.0.1 gemeint.
    if host in ("0.0.0.0", "::", "*"):
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"                      # IPv6
    return {
        "url": f"http://{host}:{proxy.get('PROXY_PORT', '8300').strip() or '8300'}",
        "api_key": proxy.get("PROXY_API_KEY", "").strip(),
        "modell": proxy.get("PROXY_MODEL_NAME", "catfish-llm").strip(),
        "gegner": chess_teil.get("LLM_NAME", "Gambit").strip() or "Gambit",
        "figuren": (chess_teil.get("SHELL_FIGUREN", "unicode").strip().lower()
                    or "unicode"),
        "hintergrund": (chess_teil.get("SHELL_HINTERGRUND", "auto").strip().lower()
                        or "auto"),
    }


def argumente():
    k = konfiguration()
    p = argparse.ArgumentParser(
        description="Im Terminal gegen catfish-llm spielen.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=BEFEHLE)
    p.add_argument("--url", default=k["url"],
                   help=f"Adresse des Proxys (Standard: {k['url']})")
    p.add_argument("--session", default="shell-" + time.strftime("%Y%m%d-%H%M%S"),
                   help="Partie-ID – mit derselben ID später weiterspielen")
    p.add_argument("--farbe", choices=["weiss", "schwarz"], default=None,
                   help="deine Farbe (Standard: weiß)")
    p.add_argument("--api-key", default=k["api_key"], help="PROXY_API_KEY")
    p.add_argument("--kein-brett", action="store_true",
                   help="Brett nicht automatisch nach jedem Zug zeigen")
    p.add_argument("--figuren", choices=["unicode", "buchstaben"],
                   default=k["figuren"] if k["figuren"] in ("unicode", "buchstaben")
                   else "unicode",
                   help="Figurensatz: 'buchstaben' (K Q R B N P) ist in jeder "
                        "Schrift eindeutig lesbar")
    p.add_argument("--hintergrund", choices=["auto", "hell", "dunkel"],
                   default=k["hintergrund"] if k["hintergrund"] in
                   ("auto", "hell", "dunkel") else "auto",
                   help="Hintergrund des Terminals – steuert die Figurenfarben "
                        "(Standard: auto = Terminal fragen)")
    return p.parse_args(), k


def ohne_zustand(text):
    """Schneidet den ZUSTAND-Block ab.

    Der Server hängt ihn an jede Werkzeugantwort, damit das LLM geerdet ist –
    für den Menschen im Terminal ist nur der Teil davor interessant.
    """
    text = text.split("\n\n=== ZUSTAND")[0].rstrip()
    # "Meine besten Züge …" ist die Anrede an das LLM – im Terminal passt "Beste".
    return text.replace("Meine besten Züge für die aktuelle Stellung:",
                        "Beste Züge für die aktuelle Stellung:")


# ---------------------------------------------------------------------------- #
# Client
# ---------------------------------------------------------------------------- #
class ShellClient:
    def __init__(self, args, konfig):
        self.url = args.url.rstrip("/")
        self.session = args.session
        self.farbe = args.farbe
        self.kein_brett = args.kein_brett
        self.figuren = getattr(args, "figuren", "unicode")
        self.hintergrund = getattr(args, "hintergrund", "auto")
        self.farbig = False          # wird beim ersten Brett entschieden
        self.ansicht_gefragt = False
        self.gegner = konfig["gegner"]
        self.modell = konfig["modell"]
        self.kopf = {"Content-Type": "application/json"}
        if args.api_key:
            self.kopf["Authorization"] = f"Bearer {args.api_key}"
        self.verlauf = []           # Chatverlauf – der Proxy ist zustandslos

    # ---------------------------------------------------------------- API --
    def chat(self, text):
        self.verlauf.append({"role": "user", "content": text})
        antwort = requests.post(
            f"{self.url}/v1/chat/completions", headers=self.kopf, timeout=300,
            json={"session_id": self.session, "messages": self.verlauf})
        antwort.raise_for_status()
        inhalt = antwort.json()["choices"][0]["message"]["content"]
        self.verlauf.append({"role": "assistant", "content": inhalt})
        return inhalt

    def werkzeug(self, name, **params):
        antwort = requests.post(f"{self.url}/chess/api/{name}", headers=self.kopf,
                                timeout=60, json={"session_id": self.session, **params})
        daten = antwort.json()
        if antwort.status_code != 200:
            raise RuntimeError(daten.get("error", antwort.text))
        return daten.get("result", "")

    def stand(self):
        """Serverzustand: FEN, wer am Zug ist, Verlauf, Farben."""
        text = self.werkzeug("brett_ansehen")

        def hole(muster, standard=None):
            treffer = re.search(muster, text)
            return treffer.group(1) if treffer else standard

        return {
            "fen": hole(r"FEN: (.+)"),
            "verlauf": (hole(r"Verlauf: (.*)") or "").strip(),
            "am_zug": hole(r"Am Zug: (\w+)"),
            "du_bist": hole(r"DU BIST: (\w+)"),          # Farbe des LLM
            "mensch": hole(r"MENSCH: (\w+)"),           # Farbe des Menschen
            "halbzuege": int(hole(r"Halbzüge: (\d+)", "0")),
            "text": text,
        }

    # --------------------------------------------------------------- Brett --
    def ansicht_klaeren(self):
        """Einmalig entscheiden, ob die Figuren eingefärbt werden.

        Auf dunklem Grund sehen die gefüllten schwarzen Figuren heller aus als
        die weißen Umrisse – die Farben wirken vertauscht. Deshalb wird der
        Hintergrund ermittelt (Terminal-Abfrage, COLORFGBG) und nur dann
        eingefärbt. Ist er nicht ermittelbar, wird einmal gefragt; bleibt die
        Antwort aus, ändert sich nichts.
        """
        if self.figuren == "buchstaben" or self.hintergrund == "hell":
            return
        if self.hintergrund == "dunkel":
            self.farbig = True
            return
        ermittelt = hintergrund_ermitteln()
        if ermittelt:
            self.farbig = (ermittelt == "dunkel")
            return
        if self.ansicht_gefragt or not sys.stdout.isatty():
            return
        self.ansicht_gefragt = True
        try:
            antwort = input("  Terminal dunkel? Die Figurenfarben wirken dort "
                            "sonst vertauscht. [j/N, Enter = unverändert] ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        antwort = antwort.strip().lower()
        if antwort in ("j", "ja", "y", "yes"):
            self.farbig = True
        else:                       # n, Enter oder Unbekanntes: wie bisher
            self.farbig = False

    def brett_zeichnen(self, stand):
        if not stand["fen"]:
            print("  (noch keine Partie – 'neu' startet eine)\n")
            return
        brett = chess.Board(stand["fen"])
        self.ansicht_klaeren()
        # Aus Sicht des Menschen zeichnen
        print(brett_text(stand["fen"],
                         invertiert=(self.mensch_farbe() == "schwarz"),
                         figuren=self.figuren, farbig=self.farbig))
        if stand["verlauf"]:
            print(f"  Verlauf: {stand['verlauf']}")
        print(f"  Am Zug: {stand['am_zug']}"
              + ("  ← du" if stand["am_zug"] == self.mensch_anzeige() else "")
              + f"   (Partie: {self.session})")
        if brett.is_check():
            print("  Achtung: Schach!")
        print()

    def mensch_farbe(self):
        """Eigene Farbe – aus --farbe oder aus dem Serverzustand abgeleitet."""
        if self.farbe:
            return self.farbe
        return "weiss"

    def mensch_anzeige(self):
        return "Weiß" if self.mensch_farbe() == "weiss" else "Schwarz"

    def farbe_vom_server(self, stand):
        """Übernimmt die Farbe, die der Server für den Menschen führt.

        Der Server nennt sie ausdrücklich als „MENSCH: …“. Ältere Stände
        nennen nur die Farbe des LLM – dann gilt die Gegenseite.
        """
        if self.farbe:
            return
        mensch = stand.get("mensch")
        if mensch in ("Weiß", "Schwarz"):
            self.farbe = "weiss" if mensch == "Weiß" else "schwarz"
            return
        if not stand.get("du_bist"):
            return
        self.farbe = "schwarz" if stand["du_bist"] == "Weiß" else "weiss"

    # ------------------------------------------------------------------ UI --
    def zeige(self, text):
        print(f"\n  {self.gegner}: {text.strip()}\n")

    def hilfe(self):
        print(BEFEHLE + "\n")

    def starten(self):
        farbe = self.farbe or "weiss"
        print(f"chess_shell → {self.url}  (Partie: {self.session})")
        print(f"Du spielst {farbe.capitalize()}. Der Proxy startet die Partie …")
        eroeffnung = f"Lass uns eine neue Partie Schach spielen. " \
                     f"Ich spiele {farbe.capitalize()}"
        if farbe == "weiss":
            eroeffnung += " und ziehe zuerst."
        else:
            eroeffnung += " – du fängst an."
        try:
            with Spinner(f"{self.gegner} bereitet das Brett vor"):
                antwort = self.chat(eroeffnung)
        except requests.exceptions.RequestException as exc:
            self.netzfehler(exc)
            return False
        self.zeige(antwort)
        try:
            stand = self.stand()
            self.farbe_vom_server(stand)
            self.brett_zeichnen(stand)
        except RuntimeError:
            print("  Es läuft noch keine Partie – 'neu' startet eine.\n")
        self.hilfe()
        return True

    @staticmethod
    def netzfehler(exc):
        print(f"\n  Kein Kontakt zum Proxy: {exc}\n", file=sys.stderr)
        print("  Läuft er?   ./start_proxy.sh status", file=sys.stderr)
        print("  Starten?    ./start_proxy.sh", file=sys.stderr)
        print("  Adresse prüfen: PROXY_HOST/PROXY_PORT in chess.ini"
              " oder --url angeben", file=sys.stderr)

    def runde(self, eingabe):
        """Eine Eingabe verarbeiten. False = beenden."""
        text = eingabe.strip()
        if not text:
            return True
        befehl = text.lstrip(":").lower()
        wort, _, rest = befehl.partition(" ")

        if wort in ("exit", "quit", "q"):
            print("  Bis zum nächsten Mal!")
            return False
        if wort in ("hilfe", "help", "h", "?"):
            self.hilfe()
            return True
        if wort in ("neu", "new"):
            farbe = self.mensch_farbe()
            try:
                self.werkzeug("neue_partie",
                              eigene_farbe="schwarz" if farbe == "weiss" else "weiss",
                              gegner_name="Mensch", nutzer_farbe=farbe)
            except RuntimeError as exc:
                print(f"  Fehler: {exc}\n")
                return True
            self.verlauf = []
            print()
            self.brett_zeichnen(self.stand())
            # Spielt der Mensch Schwarz, muss der Gegner die Partie eröffnen.
            if farbe != "weiss":
                try:
                    with Spinner(f"{self.gegner} eröffnet"):
                        self.zeige(self.chat("Neue Partie – du bist am Zug."))
                    self.brett_zeichnen(self.stand())
                except requests.exceptions.RequestException as exc:
                    self.netzfehler(exc)
            return True
        if wort in ("brett", "board", "b"):
            print()
            self.brett_zeichnen(self.stand())
            return True
        if wort in ("verlauf", "moves", "v"):
            text = ohne_zustand(self.werkzeug("zug_verlauf"))
            print("\n  " + text.replace("\n", "\n  ") + "\n")
            return True
        if wort in ("beste", "best"):
            n = re.sub(r"\D", "", rest) or "3"
            text = ohne_zustand(self.werkzeug("beste_zuege", n=int(n)))
            print("\n  " + text.replace("\n", "\n  ") + "\n")
            return True
        if wort in ("bewerten", "eval", "bewertung"):
            text = ohne_zustand(self.werkzeug("stellung_bewerten"))
            print("\n  " + text.replace("\n", "\n  ") + "\n")
            return True
        if wort in ("aufgeben", "resign"):
            print(f"\n  {self.werkzeug('partie_aufgeben')}\n")
            return True

        # Alles andere geht an den Gegner (LLM über den Proxy)
        try:
            with Spinner(f"{self.gegner} überlegt"):
                antwort = self.chat(text)
        except requests.exceptions.RequestException as exc:
            self.netzfehler(exc)
            return True
        except RuntimeError as exc:
            print(f"\n  Fehler: {exc}\n")
            return True
        self.zeige(antwort)
        if not self.kein_brett:
            try:
                stand = self.stand()
                self.farbe_vom_server(stand)
                print()
                self.brett_zeichnen(stand)
            except RuntimeError:
                pass
        return True


def main():
    args, konfig = argumente()
    client = ShellClient(args, konfig)
    try:
        if not client.starten():
            return 1
        while True:
            try:
                eingabe = input("  Du> ")
            except EOFError:
                print()
                return 0
            if not client.runde(eingabe):
                return 0
    except KeyboardInterrupt:
        print("\n  Abgebrochen.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
