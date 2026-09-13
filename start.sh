#!/usr/bin/env bash
# ==============================================================================
# chess – Startskript: Mensch gegen LLM (Stockfish-Backend)
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# Nutzung:
#   ./start.sh                       # Du spielst Weiß
#   ./start.sh --engine=black        # Du spielst Schwarz (LLM beginnt)
#   ./start.sh --personality="mürrischer Großmeister"
#   ./start.sh --moves=60
#
# Fehlt etwas (venv, Pakete, Engine), wird es vor dem Start automatisch
# eingerichtet – siehe pruefen.sh.
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")"

# shellcheck source=pruefen.sh
source ./pruefen.sh

if ! sicherstellen; then
    echo "Start abgebrochen – bitte die Hinweise oben beheben." >&2
    exit 1
fi

exec "$VENV_PY" chess_game.py "$@"
