#!/usr/bin/env bash
# ==============================================================================
# Dateiname: start_proxy.sh
# Projekt:   catfish-llm – LLM + Stockfish Schachanbindung
# ==============================================================================
# Copyright (C) 2026 Olav (https://github.com/o-valo)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Freie Software unter der GNU GPL v3 oder später – vollständiger Text in
# LICENSE. Weitergabe ohne jede Gewährleistung.
# ==============================================================================
# start_proxy.sh – Startet catfish-llm (OpenAI-kompatible API mit
#                  eingebauten Schach-Werkzeugen: Stockfish + Spielverwaltung)
#
# Aufruf:    ./start_proxy.sh              startet den Proxy
#            ./start_proxy.sh stop         beendet ihn
#            ./start_proxy.sh restart      Neustart
#            ./start_proxy.sh status       läuft er?
#            ./start_proxy.sh adressen     zeigt alle API-Adressen dieses Hosts
#            ./start_proxy.sh check        prüft die Voraussetzungen (ändert nichts)
#
# Fehlt etwas (venv, Pakete, Engine), wird es vor dem Start automatisch
# eingerichtet – siehe pruefen.sh.
#
# Log:       <Projektordner>/proxy.log
# ==============================================================================
set -u
cd "$(dirname "$0")"

# shellcheck source=pruefen.sh
source ./pruefen.sh

PIDFILE="proxy.pid"
LOGFILE="proxy.log"

# Port aus chess.ini ([proxy] PROXY_PORT), sonst 8300.
port_ermitteln() {
    lokal_port="$(awk '
        /^\[proxy\]/ { im_proxy = 1; next }
        /^\[/        { im_proxy = 0 }
        im_proxy && /^[[:space:]]*PROXY_PORT[[:space:]]*=/ {
            split($0, teile, "=")
            gsub(/[^0-9]/, "", teile[2])
            print teile[2]
            exit
        }' chess.ini 2>/dev/null)"
    echo "${lokal_port:-8300}"
}

# PROXY_HOST aus chess.ini ([proxy] PROXY_HOST), sonst 0.0.0.0.
host_ermitteln() {
    lokal_host="$(awk '
        /^\[proxy\]/ { im_proxy = 1; next }
        /^\[/        { im_proxy = 0 }
        im_proxy && /^[[:space:]]*PROXY_HOST[[:space:]]*=/ {
            split($0, teile, "=")
            gsub(/[[:space:]]/, "", teile[2])
            print teile[2]
            exit
        }' chess.ini 2>/dev/null)"
    echo "${lokal_host:-0.0.0.0}"
}

# Alle Adressen dieses Hosts, ohne Loopback und ohne IPv6-Link-Local.
# Ausgabe: <adresse>\t<interface>\t<IPv4|IPv6>
adressen_auflisten() {
    if ! command -v ip >/dev/null 2>&1; then
        # Fallback ohne iproute2
        for adresse in $(hostname -I 2>/dev/null); do
            printf "%s\t%s\t%s\n" "$adresse" "?" "IPv4"
        done
        return
    fi
    ip -o addr show 2>/dev/null | awk '
        $2 == "lo" { next }
        $3 != "inet" && $3 != "inet6" { next }
        {
            split($4, teile, "/")
            adresse = teile[1]
            if ($3 == "inet6" && adresse ~ /^fe80:/) next
            printf "%s\t%s\t%s\n", adresse, $2,
                   ($3 == "inet6" ? "IPv6" : "IPv4")
        }' | sort -t"$(printf '\t')" -k3,3 -k2,2
}

# Gibt für jede Host-Adresse die fertige API-URL aus. Ein Host kann mehrere
# Netze haben (LAN, VPN/WireGuard, Docker-Bridge …) – welche erreichbar ist,
# hängt davon ab, wo der Client steht. Steht der Rechner hinter NAT oder
# Doppel-NAT, funktioniert von außen nur die VPN-Adresse.
adressen_zeigen() {
    port="$(port_ermitteln)"
    host="$(host_ermitteln)"
    lokal_url="http://127.0.0.1:${port}/v1/chat/completions"

    # PROXY_HOST bestimmt, was überhaupt erreichbar ist – nur das ausgeben.
    case "$host" in
        127.0.0.1|localhost)
            echo "  PROXY_HOST=${host} – der Proxy ist NUR lokal erreichbar:"
            printf "    %-48s (%s)\n" "$lokal_url" "nur dieser Rechner"
            echo "  Für Clients im Netz in chess.ini PROXY_HOST=0.0.0.0 setzen –"
            echo "  oder gezielt eine Adresse, z. B. die VPN-Adresse."
            return ;;
        0.0.0.0|::|""|"*")
            : ;;                      # alle Schnittstellen -> unten auflisten
        *)
            if [ "${host#*:}" != "$host" ]; then
                basis="http://[${host}]"     # IPv6
            else
                basis="http://${host}"
            fi
            schnittstelle="$(adressen_auflisten \
                | awk -F"$(printf '\t')" -v a="$host" '$1 == a { print $2; exit }')"
            if [ -n "$schnittstelle" ]; then
                echo "  PROXY_HOST=${host} – der Proxy lauscht nur auf dieser Adresse:"
                printf "    %-48s (%s)\n" \
                    "${basis}:${port}/v1/chat/completions" "$schnittstelle"
            else
                echo "  PROXY_HOST=${host} gehört nicht zu diesem Rechner:" >&2
                printf "    %-48s\n" "${basis}:${port}/v1/chat/completions"
            fi
            return ;;
    esac

    echo "  API-Adressen dieses Rechners (die passende im Client eintragen):"
    printf "    %-48s (%s, nur dieser Rechner)\n" "$lokal_url" "lo"
    adressen_auflisten | while IFS="$(printf '\t')" read -r adresse schnittstelle familie; do
        [ -n "$adresse" ] || continue
        if [ "$familie" = "IPv6" ]; then
            basis="http://[${adresse}]"
        else
            basis="http://${adresse}"
        fi
        fall="lokales Netz"
        case "$schnittstelle" in
            wg*|tun*|tap*|tailscale*|zt*|ppp*)
                fall="VPN – auch von außen erreichbar" ;;
        esac
        printf "    %-48s (%s, %s)\n" \
            "${basis}:${port}/v1/chat/completions" "$schnittstelle" "$fall"
    done
    echo "  Hinweis: Hinter NAT/Doppel-NAT ist von außen nur die VPN-Adresse"
    echo "           nutzbar; eine Firewall muss den Port freigeben, z. B."
    echo "           sudo ufw allow in on wg0 to any port ${port} proto tcp"
}

# Ist der Port schon belegt? Setzt PORT_PROZESS, wenn der Name bekannt ist.
port_belegt() {
    PORT_PROZESS=""
    lokal_port="$1"
    if command -v ss >/dev/null 2>&1; then
        lokal_zeile="$(ss -ltn 2>/dev/null | awk -v p=":${lokal_port}" '$4 ~ p"$" { print; exit }')"
        [ -n "$lokal_zeile" ] && return 0
        return 1
    fi
    if command -v lsof >/dev/null 2>&1; then
        lokal_zeile="$(lsof -nP -iTCP:"$lokal_port" -sTCP:LISTEN 2>/dev/null | sed -n '2p')"
        if [ -n "$lokal_zeile" ]; then
            PORT_PROZESS="$(printf '%s' "$lokal_zeile" | awk '{ print $1 }')"
            return 0
        fi
        return 1
    fi
    # Letzter Notnagel: Verbindungsversuch (Name bleibt unbekannt)
    if (exec 3<>/dev/tcp/127.0.0.1/"$lokal_port") 2>/dev/null; then
        return 0
    fi
    return 1
}

# Gehört <pid> wirklich zu DIESEM Projektordner? Wichtig, weil eine kopierte
# Projektmappe eine alte proxy.pid mitbringt – die zeigt dann auf den Proxy der
# Originalkopie, den 'stop' sonst abschießen würde.
pid_gehoert_hierher() {
    lokal_pid="$1"
    kill -0 "$lokal_pid" 2>/dev/null || return 1
    ps -o args= -p "$lokal_pid" 2>/dev/null | grep -q chess_proxy.py || return 1
    if [ -r "/proc/${lokal_pid}/cwd" ]; then
        [ "$(readlink "/proc/${lokal_pid}/cwd" 2>/dev/null)" = "$(pwd)" ] || return 1
    elif command -v lsof >/dev/null 2>&1; then
        [ "$(lsof -a -p "$lokal_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')" = "$(pwd)" ] \
            || return 1
    fi
    return 0
}

starten() {
    if ! sicherstellen; then
        echo "Start abgebrochen – bitte die Hinweise oben beheben." >&2
        exit 1
    fi
    if [ -f "$PIDFILE" ] && pid_gehoert_hierher "$(cat "$PIDFILE")"; then
        echo "catfish-llm läuft bereits (PID $(cat "$PIDFILE"))."
        echo "  Adressen: $(pwd)/$(basename "$0") adressen"
        exit 0
    fi
    if [ -f "$PIDFILE" ]; then
        # Absturz, Reboot oder kopierter Projektordner: die Datei taugt nicht.
        echo "Hinweis: unbrauchbare ${PIDFILE} (PID $(cat "$PIDFILE") läuft nicht bzw."
        echo "         gehört zu einem anderen Ordner) – wird entfernt."
        rm -f "$PIDFILE"
    fi

    # Konfiguration vorab prüfen – sonst endet der Start als Rätsel im Log.
    if ! pruefen_konfig; then
        echo "FEHLER: ${KONFIG_INFO}" >&2
        echo "        Bitte in chess.ini korrigieren." >&2
        exit 1
    fi
    ziel_host="$(host_ermitteln)"
    case "$ziel_host" in
        127.0.0.1|localhost|0.0.0.0|::|""|"*") : ;;
        *)
            if ! adressen_auflisten | cut -f1 | grep -Fxq "$ziel_host"; then
                echo "FEHLER: PROXY_HOST=${ziel_host} gehört nicht zu diesem Rechner." >&2
                echo "        Verfügbare Adressen zeigt: ./start_proxy.sh adressen" >&2
                exit 1
            fi
            ;;
    esac
    ziel_port="$(port_ermitteln)"
    if port_belegt "$ziel_port"; then
        echo "FEHLER: Port ${ziel_port} ist bereits belegt${PORT_PROZESS:+ von ${PORT_PROZESS}}." >&2
        echo "        Läuft der Proxy schon?   pgrep -af chess_proxy.py" >&2
        echo "        Anderer Port:            PROXY_PORT in chess.ini ändern" >&2
        exit 1
    fi

    setsid nohup "$VENV_PY" -u chess_proxy.py >>"$LOGFILE" 2>&1 </dev/null &
    echo $! >"$PIDFILE"
    sleep 2
    # Warten, bis der Port wirklich lauscht: waitress meldet den Prozess
    # erst "läuft", bindet den Socket aber ein paar Zehntelsekunden später.
    port="$ziel_port"
    for _ in $(seq 1 40); do
        if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
            break
        fi
        sleep 0.25
    done
    if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "catfish-llm gestartet (PID $(cat "$PIDFILE"))."
        echo "  Log:      $(pwd)/$LOGFILE"
        echo "  Stoppen:  $(pwd)/$(basename "$0") stop"
        echo
        adressen_zeigen
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
        if pid_gehoert_hierher "$PID"; then
            kill "$PID"
            sleep 1
            kill -0 "$PID" 2>/dev/null && kill -9 "$PID"
            echo "catfish-llm gestoppt (PID $PID)."
        elif kill -0 "$PID" 2>/dev/null; then
            echo "Hinweis: ${PIDFILE} zeigt auf PID ${PID} – die gehört NICHT zu"
            echo "         diesem Ordner (z. B. Kopie des Projekts)."
            echo "         Der Prozess bleibt unangetastet, die Datei wird entfernt."
            pgrep -af chess_proxy.py || true
        else
            echo "catfish-llm lief nicht mehr."
        fi
        rm -f "$PIDFILE"
    else
        echo "Keine PID-Datei gefunden – läuft der Proxy?"
        pgrep -af chess_proxy.py || true
    fi
}

status() {
    if [ -f "$PIDFILE" ] && pid_gehoert_hierher "$(cat "$PIDFILE")"; then
        echo "catfish-llm läuft (PID $(cat "$PIDFILE"))."
        return 0
    fi
    echo "catfish-llm läuft nicht (in diesem Ordner)."
    lokal_fremd="$(pgrep -af chess_proxy.py || true)"
    if [ -n "$lokal_fremd" ]; then
        echo "Andernorts läuft aber noch ein catfish-llm-Proxy:"
        printf '%s\n' "$lokal_fremd"
    fi
    exit 1
}

case "${1:-start}" in
    start)   starten ;;
    stop)    stoppen ;;
    restart) stoppen; starten ;;
    status)  status ;;
    adressen|ips) adressen_zeigen ;;
    check|pruefen) exec ./install.sh --check ;;
    *) echo "Aufruf: $0 {start|stop|restart|status|adressen|check}" >&2; exit 2 ;;
esac
