#!/usr/bin/env bash
# ==============================================================================
# Dateiname: pruefen.sh
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# Voraussetzungen prüfen – und fehlende Teile selbst nachinstallieren.
#
# Wird von install.sh, start.sh und start_proxy.sh eingebunden (`source`),
# NICHT direkt ausgeführt. Die Funktionen geben selbst nichts aus, sondern
# setzen INFO-Variablen; die Ausgabe macht der Aufrufer (Tabelle in
# pruefen_alle). So bleibt die Darstellung an einer Stelle.
#
# Für Tests lassen sich die Pfade umlenken:
#   CHESS_ROOT   Projektordner     (Standard: Ordner dieser Datei)
#   CHESS_INI    Konfigurationsdatei
#   VENV         virtuelles Environment
# ==============================================================================

CHESS_ROOT="${CHESS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
CHESS_INI="${CHESS_INI:-${CHESS_ROOT}/chess.ini}"
VENV="${VENV:-${CHESS_ROOT}/.venv}"
VENV_PY="${VENV}/bin/python"
PYTHON_MIN_TEILE="(3, 9)"     # Flask 3.1 benötigt Python 3.9; getestet mit 3.12

# ---------------------------------------------------------------------------- #
# Basis-Helfer
# ---------------------------------------------------------------------------- #

# ini_wert <sektion> <schlüssel> – Wert aus chess.ini (leer, wenn nicht gesetzt)
ini_wert() {
    [ -f "$CHESS_INI" ] || return 0
    awk -v sektion="$1" -v schluessel="$2" '
        /^\[/ {
            im_abschnitt = ($0 == "[" sektion "]")
            next
        }
        im_abschnitt && $0 ~ "^[[:space:]]*" schluessel "[[:space:]]*=" {
            wert = $0
            sub(/^[^=]*=/, "", wert)
            gsub(/^[[:space:]]+|[[:space:]]+$/, "", wert)
            print wert
            exit
        }' "$CHESS_INI" 2>/dev/null
}

# engine_name_fuer_plattform – offizieller Release-Name für dieses System
# (Rückgabe 1 = Plattform nicht unterstützt). Eine Quelle der Wahrheit für
# pruefen.sh, install.sh und stockfish-install.sh.
engine_name_fuer_plattform() {
    case "$(uname -s):$(uname -m)" in
        Linux:x86_64)  echo "stockfish-linux-x86-64-universal" ;;
        Linux:aarch64) echo "stockfish-linux-arm64-universal" ;;
        Linux:armv7l)  echo "stockfish-android-armv7-neon" ;;
        Linux:riscv64) echo "stockfish-linux-riscv64-universal" ;;
        Darwin:*)      echo "stockfish-macos-universal" ;;
        *)             return 1 ;;
    esac
}

# engine_uci_test <pfad> – 0, wenn die Engine auf 'uci' mit 'uciok' antwortet.
# Setzt ENGINE_VERSION.
engine_uci_test() {
    local pfad="$1" ausgabe
    [ -x "$pfad" ] || return 1
    ausgabe="$(printf 'uci\nisready\nquit\n' | "$pfad" 2>/dev/null || true)"
    printf '%s' "$ausgabe" | grep -qi '^uciok' || return 1
    ENGINE_VERSION="$(printf '%s' "$ausgabe" | sed -n 's/^id name //p' | head -1)"
    [ -n "$ENGINE_VERSION" ] || ENGINE_VERSION="Engine (Version unbekannt)"
    return 0
}

# engine_im_projektordner – absoluter Pfad einer Engine unter engines/stockfish
# (bevorzugt die offizielle Binary dieser Plattform, sonst eine selbst gebaute)
engine_im_projektordner() {
    local ordner="${CHESS_ROOT}/engines/stockfish" name
    [ -d "$ordner" ] || return 0
    name="$(engine_name_fuer_plattform || true)"
    if [ -n "$name" ] && [ -x "${ordner}/${name}" ]; then
        printf '%s\n' "${ordner}/${name}"
        return 0
    fi
    find "$ordner" -maxdepth 2 -type f -executable -name 'stockfish*' 2>/dev/null \
        | head -1
}

# pfad_absolut <pfad> – relativ zum Projektordner auflösen
pfad_absolut() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *)  printf '%s\n' "${CHESS_ROOT}/$1" ;;
    esac
}

# ---------------------------------------------------------------------------- #
# Einzelprüfungen (Rückgabe 0 = in Ordnung, 1 = fehlt/kaputt)
# ---------------------------------------------------------------------------- #

# Konfiguration: chess.ini lesbar, PROXY_PORT plausibel
pruefen_konfig() {
    local port host
    if [ ! -f "$CHESS_INI" ]; then
        KONFIG_INFO="chess.ini fehlt (${CHESS_INI})"
        return 1
    fi
    if [ ! -r "$CHESS_INI" ]; then
        KONFIG_INFO="chess.ini ist nicht lesbar (${CHESS_INI})"
        return 1
    fi

    port="$(ini_wert proxy PROXY_PORT)"
    host="$(ini_wert proxy PROXY_HOST)"
    if [ -n "$port" ]; then
        if ! printf '%s' "$port" | grep -Eq '^[0-9]+$'; then
            KONFIG_INFO="PROXY_PORT ist keine Zahl ('${port}')"
            return 1
        fi
        # 10# vermeidet Oktal-Fehldeutungen wie '008'
        if [ "$((10#${port}))" -lt 1 ] || [ "$((10#${port}))" -gt 65535 ]; then
            KONFIG_INFO="PROXY_PORT muss zwischen 1 und 65535 liegen ('${port}')"
            return 1
        fi
    fi

    KONFIG_INFO="chess.ini (Host ${host:-0.0.0.0}, Port ${port:-8300})"
    return 0
}

# System-Python: vorhanden, aktuell genug, venv-Modul nutzbar
pruefen_python() {
    local version
    if ! command -v python3 >/dev/null 2>&1; then
        PYTHON_INFO="python3 nicht gefunden – siehe Hinweise unten"
        return 1
    fi
    version="$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' \
        2>/dev/null || true)"
    if [ -z "$version" ]; then
        PYTHON_INFO="python3 lässt sich nicht ausführen"
        return 1
    fi
    if ! python3 -c "import sys; sys.exit(0 if sys.version_info[:2] >= ${PYTHON_MIN_TEILE} else 1)" \
        >/dev/null 2>&1; then
        PYTHON_INFO="python3 ${version} ist zu alt (nötig: 3.9 oder neuer)"
        return 1
    fi
    if ! python3 -c 'import venv, ensurepip' >/dev/null 2>&1; then
        PYTHON_INFO="python3 ${version}, aber das venv-Modul fehlt – siehe Hinweise unten"
        return 1
    fi
    PYTHON_INFO="python3 ${version}"
    return 0
}

# Virtuelles Environment: vorhanden und benutzbar
pruefen_venv() {
    local version
    if [ ! -d "$VENV" ]; then
        VENV_INFO=".venv fehlt – wird angelegt"
        return 1
    fi
    if [ ! -x "$VENV_PY" ]; then
        VENV_INFO=".venv ist unvollständig (${VENV_PY} fehlt) – wird neu angelegt"
        return 1
    fi
    version="$("$VENV_PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' \
        2>/dev/null || true)"
    if [ -z "$version" ]; then
        VENV_INFO=".venv startet nicht (${VENV_PY}) – wird neu angelegt"
        return 1
    fi
    VENV_INFO=".venv (Python ${version})"
    return 0
}

# Python-Pakete: alle vier Laufzeit-Abhängigkeiten importierbar?
pruefen_pakete() {
    local fehlend
    if ! pruefen_venv >/dev/null 2>&1; then
        PAKETE_INFO="übersprungen – .venv fehlt"
        return 1
    fi
    fehlend="$("$VENV_PY" - <<'PY' 2>/dev/null || true
import importlib
for name in ("chess", "flask", "waitress", "requests"):
    try:
        importlib.import_module(name)
    except Exception:
        print(name)
PY
)"
    if [ -n "$fehlend" ]; then
        PAKETE_INFO="fehlen: $(printf '%s' "$fehlend" | tr '\n' ' ' | sed 's/ $//')"
        return 1
    fi
    PAKETE_INFO="chess, flask, waitress, requests"
    return 0
}

# Stockfish: ENGINE_PATH gültig? Setzt ENGINE_ZUSTAND (ok|ini_falsch|fehlt),
# ENGINE_PFAD (Wert aus der ini), ENGINE_KANDIDAT (Engine im Projektordner).
pruefen_engine() {
    local voll kandidat
    ENGINE_PFAD=""
    ENGINE_KANDIDAT=""

    if [ ! -f "$CHESS_INI" ]; then
        ENGINE_ZUSTAND="fehlt"
        ENGINE_INFO="nicht prüfbar – chess.ini fehlt"
        return 1
    fi

    ENGINE_PFAD="$(ini_wert chess ENGINE_PATH)"
    if [ -n "$ENGINE_PFAD" ]; then
        voll="$(pfad_absolut "$ENGINE_PFAD")"
        if engine_uci_test "$voll"; then
            ENGINE_ZUSTAND="ok"
            ENGINE_INFO="${ENGINE_VERSION} (${ENGINE_PFAD})"
            return 0
        fi
        if [ -e "$voll" ]; then
            ENGINE_INFO="ENGINE_PATH '${ENGINE_PFAD}' startet nicht (keine UCI-Antwort)"
        else
            ENGINE_INFO="ENGINE_PATH zeigt auf eine fehlende Datei: ${ENGINE_PFAD}"
        fi
    else
        ENGINE_INFO="kein ENGINE_PATH in chess.ini"
    fi

    # Zweite Chance: liegt eine brauchbare Engine im Projektordner?
    kandidat="$(engine_im_projektordner)"
    if [ -n "$kandidat" ] && engine_uci_test "$kandidat"; then
        ENGINE_KANDIDAT="$kandidat"
        ENGINE_ZUSTAND="ini_falsch"
        ENGINE_INFO="${ENGINE_INFO}; gefunden: ${kandidat#${CHESS_ROOT}/}"
        return 1
    fi

    ENGINE_ZUSTAND="fehlt"
    return 1
}

# ---------------------------------------------------------------------------- #
# Gesamtbild und Selbstheilung
# ---------------------------------------------------------------------------- #

# pruefen_alle – Tabelle ausgeben; 0 = alles bereit, 1 = es fehlt etwas
pruefen_alle() {
    local fehlt=0 status
    printf '  %-18s %s\n' "Komponente" "Status"
    printf '  %s\n' "-------------------------------------------------------------------------"

    if pruefen_konfig; then status="[ OK ]"; else status="[FEHLT]"; fehlt=$((fehlt + 1)); fi
    printf '  %-18s %s %s\n' "Konfiguration" "$status" "$KONFIG_INFO"

    if pruefen_python; then status="[ OK ]"; else status="[FEHLT]"; fehlt=$((fehlt + 1)); fi
    printf '  %-18s %s %s\n' "Python" "$status" "$PYTHON_INFO"

    if pruefen_venv; then status="[ OK ]"; else status="[FEHLT]"; fehlt=$((fehlt + 1)); fi
    printf '  %-18s %s %s\n' "Python-Umgebung" "$status" "$VENV_INFO"

    if pruefen_pakete; then status="[ OK ]"; else status="[FEHLT]"; fehlt=$((fehlt + 1)); fi
    printf '  %-18s %s %s\n' "Python-Pakete" "$status" "$PAKETE_INFO"

    if pruefen_engine; then status="[ OK ]"; else status="[FEHLT]"; fehlt=$((fehlt + 1)); fi
    printf '  %-18s %s %s\n' "Stockfish-Engine" "$status" "$ENGINE_INFO"

    [ "$fehlt" -eq 0 ]
}

# python_hinweise – Installationsbefehle für das System-Python ausgeben
python_hinweise() {
    echo "  So installiert man Python 3 mit venv-Unterstützung:" >&2
    echo "    Debian/Ubuntu:  sudo apt install python3 python3-venv" >&2
    echo "    Fedora/RHEL:    sudo dnf install python3" >&2
    echo "    Arch:           sudo pacman -S python" >&2
    echo "    macOS:          brew install python3" >&2
}

# sicherstellen – prüft alles und ruft bei Bedarf install.sh auf.
# 0 = startklar, 1 = konnte nicht automatisch behoben werden.
sicherstellen() {
    local bericht

    # Im Installer läuft die Prüfung ohnehin – keine Rekursion.
    if [ "${CHESS_SETUP_LAEUFT:-0}" = "1" ]; then
        return 0
    fi

    if bericht="$(pruefen_alle 2>&1)"; then
        return 0
    fi

    echo "chess ist noch nicht startklar:"
    echo "$bericht"
    echo
    echo "Die fehlenden Teile werden jetzt automatisch eingerichtet."
    echo "(Das passiert nur einmal – danach startet der Aufruf sofort.)"
    echo
    if ! CHESS_SETUP_LAEUFT=1 "$CHESS_ROOT/install.sh"; then
        echo "FEHLER: Die automatische Einrichtung ist fehlgeschlagen." >&2
        echo "        Nur prüfen: ./install.sh --check" >&2
        return 1
    fi
    if ! pruefen_alle >/dev/null 2>&1; then
        echo "FEHLER: Es fehlen weiterhin Voraussetzungen (siehe Tabelle oben)." >&2
        return 1
    fi
    echo
    echo "Einrichtung abgeschlossen – alle Voraussetzungen erfüllt."
    return 0
}
