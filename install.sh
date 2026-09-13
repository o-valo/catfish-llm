#!/usr/bin/env bash
# ==============================================================================
# Dateiname: install.sh
# Projekt:   chess – LLM + Stockfish Schachanbindung
# ==============================================================================
# Installer: richtet alles ein bzw. repariert Fehlendes – beliebig oft
# ausführbar (idempotent). Es wird nur getan, was wirklich fehlt.
#
#   1/6 Systemvoraussetzungen prüfen (python3 + venv-Modul, Schreibrechte)
#   2/6 Virtuelles Environment .venv anlegen
#   3/6 Python-Pakete aus requirements.txt installieren und prüfen
#   4/6 Laufzeit-Ordner anlegen (partien, partien_journal, partien_proxy)
#   5/6 Schach-Engine installieren (über stockfish-install.sh)
#   6/6 Abschlussprüfung
#
# Aufruf:
#   ./install.sh                    alles einrichten / Fehlendes reparieren
#   ./install.sh --check            nur prüfen (ändert nichts) – für CI/Support
#   ./install.sh --no-engine        ohne Stockfish-Installation
#   ./install.sh --force-engine     Engine neu laden
#   ./install.sh --engine-tag sf_17 bestimmte Stockfish-Version (Standard sf_19)
#   ./install.sh --help
#
# Exit-Codes: 0 = alles bereit · 1 = Fehler bzw. etwas fehlt · 2 = falscher Aufruf
#
# Nur die Engine (neu) installieren oder eine vorhandene eintragen:
#   ./stockfish-install.sh [--force|--tag <tag>|--use <pfad>]
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=pruefen.sh
source ./pruefen.sh

CHECK=0
MIT_ENGINE=1
FORCE_ENGINE=0
ENGINE_TAG="${ENGINE_TAG:-sf_19}"

schritt() { echo; echo "==> $*"; }
info()    { echo "    $*"; }
warnung() { echo "    WARNUNG: $*" >&2; }
fehler()  { echo "FEHLER: $*" >&2; }

usage() {
    cat <<'EOF'
install.sh – chess einrichten bzw. Fehlendes reparieren

Aufruf:
  ./install.sh                     alles einrichten / reparieren
  ./install.sh --check             nur prüfen (ändert nichts); Exit 1 = es fehlt etwas
  ./install.sh --no-engine         ohne Stockfish-Installation
  ./install.sh --force-engine      Stockfish neu laden
  ./install.sh --engine-tag sf_17  bestimmte Stockfish-Version (Standard: sf_19)
  ./install.sh --help              diese Hilfe

Die Engine wird nicht mit dem Projekt ausgeliefert. Details:
  ./stockfish-install.sh --help
EOF
}

# ---------------------------------------------------------------------------- #
# Argumente
# ---------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
    case "$1" in
        --check|--dry-run) CHECK=1 ;;
        --no-engine)       MIT_ENGINE=0 ;;
        --force-engine)    FORCE_ENGINE=1 ;;
        --engine-tag)      ENGINE_TAG="${2:?--engine-tag braucht eine Version, z. B. sf_19}"; shift ;;
        --engine-tag=*)    ENGINE_TAG="${1#*=}" ;;
        -h|--help)         usage; exit 0 ;;
        *) echo "Unbekannte Option '$1' (siehe --help)" >&2; exit 2 ;;
    esac
    shift
done

# Schutzgitter: niemals ein falsches Verzeichnis löschen oder beschreiben.
case "$VENV" in
    ""|"/"|"."|"..") fehler "Ungültiger venv-Pfad '${VENV}'"; exit 1 ;;
esac
if [ ! -w . ]; then
    fehler "Der Projektordner ist nicht beschreibbar: $(pwd)"
    exit 1
fi

# ---------------------------------------------------------------------------- #
# Nur prüfen (ändert nichts)
# ---------------------------------------------------------------------------- #
if [ "$CHECK" = "1" ]; then
    echo "Voraussetzungen prüfen (es wird nichts geändert):"
    echo
    if pruefen_alle; then
        echo
        echo "Ergebnis: alles bereit – chess kann starten."
        exit 0
    fi
    echo
    echo "Ergebnis: es fehlt noch etwas."
    echo "  Beheben:  ./install.sh"
    exit 1
fi

echo "chess-Installer – es wird nur eingerichtet, was fehlt."
echo "Projektordner: $(pwd)"

# ---------------------------------------------------------------------------- #
# 1/6 Systemvoraussetzungen
# ---------------------------------------------------------------------------- #
schritt "1/6 Systemvoraussetzungen"

if ! pruefen_python; then
    fehler "$PYTHON_INFO"
    python_hinweise
    exit 1
fi
info "$PYTHON_INFO"

if ! pruefen_konfig; then
    fehler "$KONFIG_INFO"
    echo "  chess.ini muss im Projektordner liegen. Wenn sie fehlt, ist der" >&2
    echo "  Ordner unvollständig – bitte das Projekt vollständig kopieren" >&2
    echo "  (die Datei enthält LLM-, Engine- und Proxy-Einstellungen)." >&2
    exit 1
fi
info "$KONFIG_INFO"

# ---------------------------------------------------------------------------- #
# 2/6 Python-Umgebung (.venv)
# ---------------------------------------------------------------------------- #
schritt "2/6 Python-Umgebung (.venv)"

if pruefen_venv; then
    info "vorhanden – ${VENV_INFO}"
else
    if [ -d "$VENV" ]; then
        # Ein halbfertiges venv ist wertlos, aber reproduzierbar: neu anlegen.
        if [ -f "$VENV/pyvenv.cfg" ] || [ -d "$VENV/bin" ]; then
            info "unvollständiges .venv wird neu angelegt (${VENV_INFO})"
            rm -rf "$VENV"
        else
            fehler "'${VENV}' existiert, sieht aber nicht wie ein venv aus."
            echo "  Bitte den Ordner prüfen und ggf. entfernen – dann erneut starten." >&2
            exit 1
        fi
    fi
    if ! python3 -m venv "$VENV"; then
        fehler "'python3 -m venv' ist fehlgeschlagen"
        python_hinweise
        exit 1
    fi
    info ".venv angelegt"
fi

# pip aktualisieren – scheitert offline, ist aber nicht kritisch.
if "$VENV_PY" -m pip install --quiet --disable-pip-version-check --upgrade pip \
    >/dev/null 2>&1; then
    info "pip aktuell"
else
    warnung "pip konnte nicht aktualisiert werden (offline?) – wird übersprungen"
fi

# ---------------------------------------------------------------------------- #
# 3/6 Python-Pakete
# ---------------------------------------------------------------------------- #
schritt "3/6 Python-Pakete (requirements.txt)"

if [ ! -f requirements.txt ]; then
    fehler "requirements.txt fehlt im Projektordner"
    exit 1
fi

if pruefen_pakete; then
    info "alle Pakete vorhanden – ${PAKETE_INFO}"
else
    info "installiere/aktualisiere Pakete (${PAKETE_INFO}) …"
    paket_log="$(mktemp)"
    if ! "$VENV_PY" -m pip install --disable-pip-version-check \
        -r requirements.txt >"$paket_log" 2>&1; then
        fehler "Installation der Python-Pakete fehlgeschlagen"
        tail -20 "$paket_log" >&2
        rm -f "$paket_log"
        echo "  Häufigste Ursache: keine Internetverbindung." >&2
        exit 1
    fi
    rm -f "$paket_log"
    if ! pruefen_pakete; then
        fehler "Pakete sind nach der Installation nicht importierbar (${PAKETE_INFO})"
        exit 1
    fi
    info "installiert und geprüft – ${PAKETE_INFO}"
fi

# ---------------------------------------------------------------------------- #
# 4/6 Laufzeit-Ordner
# ---------------------------------------------------------------------------- #
schritt "4/6 Laufzeit-Ordner"

for ordner in partien partien_journal partien_proxy; do
    if [ ! -d "$ordner" ]; then
        mkdir -p "$ordner"
        info "${ordner}/ angelegt"
    fi
done
info "bereit: partien, partien_journal, partien_proxy"

# ---------------------------------------------------------------------------- #
# 5/6 Stockfish-Engine
# ---------------------------------------------------------------------------- #
schritt "5/6 Stockfish-Engine"

if [ "$MIT_ENGINE" = "0" ]; then
    info "übersprungen (--no-engine)"
elif pruefen_engine; then
    info "bereits einsatzbereit – ${ENGINE_INFO}"
    info "(andere Engine gewünscht? ./stockfish-install.sh --use <pfad>)"
elif [ "$ENGINE_ZUSTAND" = "ini_falsch" ] && [ -n "$ENGINE_KANDIDAT" ]; then
    # Engine liegt im Projektordner, nur der Eintrag in chess.ini passt nicht.
    # Projekt-relativ eintragen – so bleibt die chess.ini übertragbar.
    engine_pfad="${ENGINE_KANDIDAT#${CHESS_ROOT}/}"
    info "chess.ini zeigt nicht auf die vorhandene Engine:"
    info "  ENGINE_PATH:  ${ENGINE_PFAD:-(nicht gesetzt)}"
    info "  gefunden:     ${engine_pfad}"
    ENGINE_TAG="$ENGINE_TAG" ./stockfish-install.sh --use "$engine_pfad"
elif ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
    fehler "Zum Laden der Engine wird 'curl' oder 'wget' benötigt."
    echo "  Alternative: Stockfish selbst installieren und eintragen, z. B." >&2
    echo "    sudo apt install stockfish && ./stockfish-install.sh --use /usr/games/stockfish" >&2
    exit 1
else
    engine_extra=""
    [ "$FORCE_ENGINE" = "1" ] && engine_extra="--force"
    # shellcheck disable=SC2086
    ENGINE_TAG="$ENGINE_TAG" ./stockfish-install.sh $engine_extra
fi

# ---------------------------------------------------------------------------- #
# 6/6 Abschlussprüfung
# ---------------------------------------------------------------------------- #
schritt "6/6 Abschlussprüfung"

if ! pruefen_alle; then
    echo
    fehler "Nach der Installation fehlen noch Voraussetzungen – siehe Tabelle oben."
    exit 1
fi

echo
echo "Fertig – chess ist einsatzbereit."
echo
echo "  Proxy starten:      ./start_proxy.sh          (für OpenWebUI & Co.)"
echo "  Adressen anzeigen:  ./start_proxy.sh adressen"
echo "  Terminal-Client:    ./chess_shell.py          (spielen ohne OpenWebUI)"
echo "  Konsolen-App:       ./start.sh                (Mensch gegen LLM)"
echo "  Status prüfen:      ./install.sh --check"
echo "  Engine wechseln:    ./stockfish-install.sh --help"
