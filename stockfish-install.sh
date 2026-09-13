#!/usr/bin/env bash
# ==============================================================================
# Dateiname: stockfish-install.sh
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# stockfish-install.sh – Stockfish für dieses System installieren
#
# Die Engine wird NICHT mit dem Projekt ausgeliefert. Dieses Skript lädt die
# passende offizielle Universal-Binary aus den Stockfish-GitHub-Releases nach
# engines/stockfish/, prüft sie (UCI-Antwort) und trägt den Pfad in chess.ini
# ein. Es ist mehrfach ausführbar: Ist die Engine schon da, passiert nichts.
#
# Aufruf:
#   ./stockfish-install.sh                     # passende Engine installieren
#   ./stockfish-install.sh --force             # neu laden, auch wenn vorhanden
#   ./stockfish-install.sh --tag sf_17         # bestimmte Version
#   ./stockfish-install.sh --use /usr/games/stockfish
#                                              # vorhandene Engine nur eintragen
#   ./stockfish-install.sh --help
#
# Umgebungsvariablen: ENGINE_TAG (Standard sf_19)
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# Gemeinsame Prüf-Helfer (Plattform-Name, UCI-Test, ENGINE_PATH-Lesen).
# shellcheck source=pruefen.sh
source ./pruefen.sh

ENGINE_TAG="${ENGINE_TAG:-sf_19}"
ENGINE_ORDNER="engines/stockfish"
ENGINE_NAME=""
FORCE=0
USE_PFAD=""

info()   { echo "    $*"; }
schritt(){ echo "==> $*"; }
fehler() { echo "FEHLER: $*" >&2; }

usage() {
    cat <<'EOF'
stockfish-install.sh – Stockfish für dieses System installieren

Die Engine wird NICHT mit dem Projekt ausgeliefert. Dieses Skript lädt die
passende offizielle Universal-Binary aus den Stockfish-GitHub-Releases nach
engines/stockfish/, prüft sie (UCI-Antwort) und trägt den Pfad in chess.ini
ein. Mehrfach ausführbar: Ist die Engine schon da, passiert nichts.

Aufruf:
  ./stockfish-install.sh                 passende Engine installieren
  ./stockfish-install.sh --force         neu laden, auch wenn vorhanden
  ./stockfish-install.sh --tag sf_17     bestimmte Version laden
  ./stockfish-install.sh --use <pfad>    vorhandene Engine nur eintragen
  ./stockfish-install.sh --help          diese Hilfe

Umgebungsvariablen: ENGINE_TAG (Standard sf_19)
EOF
}

# ---------------------------------------------------------------------------- #
# Argumente
# ---------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
    case "$1" in
        --force|-f) FORCE=1 ;;
        --tag)      ENGINE_TAG="${2:?--tag braucht eine Version, z. B. sf_19}"; shift ;;
        --tag=*)    ENGINE_TAG="${1#*=}" ;;
        --use)      USE_PFAD="${2:?--use braucht einen Pfad zur Binary}"; shift ;;
        --use=*)    USE_PFAD="${1#*=}" ;;
        -h|--help)  usage; exit 0 ;;
        *) fehler "Unbekannte Option '$1' (siehe --help)"; exit 2 ;;
    esac
    shift
done

# ---------------------------------------------------------------------------- #
# Hilfsfunktionen
# ---------------------------------------------------------------------------- #
engine_fuer_plattform() {
    if ! ENGINE_NAME="$(engine_name_fuer_plattform)"; then
        fehler "Plattform '$(uname -s) ($(uname -m))' wird nicht unterstützt."
        echo "  Bitte Stockfish manuell installieren und den Pfad eintragen:" >&2
        echo "    ./stockfish-install.sh --use /pfad/zur/stockfish" >&2
        echo "  Alternativen je System: siehe README, Abschnitt „Schach-Engine“." >&2
        exit 1
    fi
}

# Prüft, ob die Binary startet und auf 'uci' mit 'uciok' antwortet.
# Der Test selbst liegt in pruefen.sh (engine_uci_test) – eine Quelle für
# Installer, Startskripte und diesen Aufruf.
engine_testen() {
    test_datei="$1"
    if [ ! -x "$test_datei" ]; then
        fehler "Binary nicht ausführbar: $test_datei"
        return 1
    fi
    if ! engine_uci_test "$test_datei"; then
        fehler "Engine antwortet nicht auf UCI: $test_datei"
        return 1
    fi
    return 0
}

# Trägt ENGINE_PATH in chess.ini ein (bzw. ersetzt ihn bei --use).
engine_path_setzen() {
    path_pfad="$1"
    path_ersetzen="$2"
    if [ ! -f chess.ini ]; then
        fehler "chess.ini nicht gefunden – bitte im Projektordner ausführen."
        return 1
    fi
    if grep -q '^ENGINE_PATH=' chess.ini; then
        if [ "$path_ersetzen" = "1" ]; then
            sed -i "s|^ENGINE_PATH=.*|ENGINE_PATH=${path_pfad}|" chess.ini
            info "ENGINE_PATH in chess.ini gesetzt: ${path_pfad}"
        else
            path_alt="$(sed -n 's/^ENGINE_PATH=//p' chess.ini | head -1)"
            info "ENGINE_PATH in chess.ini belassen: ${path_alt}"
            if [ "$path_alt" != "$path_pfad" ]; then
                info "(andere Engine gewünscht? dann: ./stockfish-install.sh --use <pfad>)"
            fi
        fi
    else
        printf 'ENGINE_PATH=%s\n' "$path_pfad" >> chess.ini
        info "ENGINE_PATH in chess.ini ergänzt: ${path_pfad}"
    fi
}

# ---------------------------------------------------------------------------- #
# 0. Vorhandene Engine nur eintragen (--use)
# ---------------------------------------------------------------------------- #
if [ -n "$USE_PFAD" ]; then
    schritt "Vorhandene Engine verwenden"
    if [ ! -x "$USE_PFAD" ]; then
        fehler "'$USE_PFAD' ist nicht vorhanden oder nicht ausführbar."
        exit 1
    fi
    if engine_testen "$USE_PFAD"; then
        info "Engine OK: ${ENGINE_VERSION:-unbekannt}"
    else
        info "Warnung: Engine antwortet nicht auf UCI – wird trotzdem eingetragen."
    fi
    engine_path_setzen "$USE_PFAD" 1
    echo ""
    echo "Fertig. Starten mit ./start_proxy.sh (Proxy) oder ./start.sh (Konsole)."
    exit 0
fi

# ---------------------------------------------------------------------------- #
# 1. Plattform bestimmen
# ---------------------------------------------------------------------------- #
schritt "Stockfish für dieses System (Release ${ENGINE_TAG})"
engine_fuer_plattform
info "System: $(uname -s) $(uname -m) → ${ENGINE_NAME}"

ZIEL="${ENGINE_ORDNER}/${ENGINE_NAME}"

# ---------------------------------------------------------------------------- #
# 2. Engine laden (falls nötig)
# ---------------------------------------------------------------------------- #
if [ -x "$ZIEL" ] && [ "$FORCE" != "1" ]; then
    info "Stockfish ist bereits installiert: ${ZIEL}"
else
    URL="https://github.com/official-stockfish/Stockfish/releases/download/${ENGINE_TAG}/${ENGINE_NAME}.tar.gz"
    # Kompakte Fortschrittsanzeige statt curl-Tabelle/wget-Geschwätz.
    if command -v curl >/dev/null 2>&1; then
        herunterladen() { curl -fL --retry 3 --retry-delay 2 --progress-bar -o "$1" "$2"; }
    elif command -v wget >/dev/null 2>&1; then
        herunterladen() { wget -q --show-progress -O "$1" "$2"; }
    else
        fehler "Weder 'curl' noch 'wget' gefunden – bitte eines davon installieren."
        exit 1
    fi

    TMP="$(mktemp -d)"
    trap 'rm -rf "${TMP:-}"' EXIT
    info "Lade ${ENGINE_NAME}.tar.gz (ca. 80 MB – je nach Verbindung einige Minuten) …"
    if ! herunterladen "${TMP}/engine.tar.gz" "$URL"; then
        fehler "Download fehlgeschlagen: $URL"
        echo "  Prüfe die Internetverbindung und ob die Version '${ENGINE_TAG}'" >&2
        echo "  existiert (https://github.com/official-stockfish/Stockfish/releases)." >&2
        exit 1
    fi

    mkdir -p "$ENGINE_ORDNER"
    tar -xzf "${TMP}/engine.tar.gz" -C "$ENGINE_ORDNER" --strip-components=1 \
        || tar -xzf "${TMP}/engine.tar.gz" -C "$ENGINE_ORDNER"
    rm -rf "$TMP"
    trap - EXIT

    if [ ! -f "$ZIEL" ]; then
        fehler "Binary nach dem Entpacken nicht gefunden: ${ZIEL}"
        echo "  Inhalt von ${ENGINE_ORDNER}:" >&2
        ls -1 "$ENGINE_ORDNER" >&2 || true
        exit 1
    fi
    chmod +x "$ZIEL"
    info "Entpackt nach ${ZIEL}"
fi

# ---------------------------------------------------------------------------- #
# 3. Engine prüfen und Pfad eintragen
# ---------------------------------------------------------------------------- #
schritt "Engine prüfen"
if engine_testen "$ZIEL"; then
    info "Engine antwortet auf UCI: ${ENGINE_VERSION:-unbekannt}"
else
    fehler "Die installierte Engine funktioniert nicht (siehe oben)."
    exit 1
fi

schritt "chess.ini"
# Einen funktionierenden, selbst eingetragenen ENGINE_PATH niemals überschreiben –
# aber einen unbrauchbaren reparieren, sonst bliebe chess.ini kaputt und jeder
# Start würde erneut scheitern.
if pruefen_engine; then
    engine_path_setzen "$ZIEL" 0
else
    [ -n "${ENGINE_PFAD:-}" ] && info "vorheriger ENGINE_PATH war unbrauchbar: ${ENGINE_PFAD}"
    engine_path_setzen "$ZIEL" 1
fi

echo ""
echo "Stockfish fertig."
echo "  Binary:  ${ZIEL}"
echo "  Version: ${ENGINE_VERSION:-unbekannt} (Release ${ENGINE_TAG})"
echo "  Test:    ./start.sh --engine=black"
