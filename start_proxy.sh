#!/usr/bin/env bash
# ==============================================================================
# start_proxy.sh – Startet den chess-proxy (OpenAI-kompatible API mit
#                  eingebauten Schach-Werkzeugen: Stockfish + Spielverwaltung)
#
# Aufruf:    ./start_proxy.sh
# Stoppen:   ./start_proxy.sh stop
# Log:       ~/chess/proxy.log
# ==============================================================================
set -u
cd "$(dirname "$0")"

PIDFILE="proxy.pid"
LOGFILE="proxy.log"

starten() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "chess-proxy läuft bereits (PID $(cat "$PIDFILE"))."
        exit 0
    fi
    setsid nohup .venv/bin/python -u chess_proxy.py >>"$LOGFILE" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    sleep 2
    if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "chess-proxy gestartet (PID $(cat "$PIDFILE"))."
        echo "  API:      http://$(hostname -I | awk '{print $1}'):8300/v1/chat/completions"
        echo "  Log:      $(pwd)/$LOGFILE"
        echo "  Stoppen:  $(pwd)/$(basename "$0") stop"
    else
        echo "Start fehlgeschlagen – siehe $LOGFILE:" >&2
        tail -20 "$LOGFILE" >&2
        rm -f "$PIDFILE"
        exit 1
    fi
}

stoppen() {
    if [ -f "$PIDFILE" ]; then
        PID="$(cat "$PIDFILE")"
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            sleep 1
            kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
            echo "chess-proxy gestoppt (PID $PID)."
        else
            echo "chess-proxy lief nicht mehr."
        fi
        rm -f "$PIDFILE"
    else
        echo "Keine PID-Datei gefunden – läuft der Proxy?"
        pgrep -af chess_proxy.py || true
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "chess-proxy läuft (PID $(cat "$PIDFILE"))."
    else
        echo "chess-proxy läuft nicht."
        exit 1
    fi
}

case "${1:-start}" in
    start) starten ;;
    stop)  stoppen ;;
    restart) stoppen; starten ;;
    status) status ;;
    *) echo "Aufruf: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
