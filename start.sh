#!/usr/bin/env bash
# ==============================================================================
# chess – Startskript: Mensch gegen LLM (Stockfish-Backend)
# ==============================================================================
# Nutzung:
#   ./start.sh                       # Du spielst Weiß
#   ./start.sh --engine=black        # Du spielst Schwarz (LLM beginnt)
#   ./start.sh --personality="mürrischer Großmeister"
#   ./start.sh --moves=60
# ==============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Erstelle virtuelles Environment ..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
    .venv/bin/pip install --quiet chess requests
fi

exec .venv/bin/python chess_game.py "$@"
