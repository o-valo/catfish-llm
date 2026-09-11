#!/usr/bin/env python3
# ==============================================================================
# Dateiname: chess_proxy.py
# Projekt:   chess – LLM + Stockfish Schachanbindung (Tool-API)
# ==============================================================================
# OpenAI-kompatibler Proxy mit eingebauten Schach-Werkzeugen.
#
#   OpenWebUI ──► chess_proxy (dieses Programm) ──► LLM (llm-bahnhof, Ollama, …)
#                     │
#                     └──► Stockfish (lokal, via chess_service)
#
# Ablauf:
#   1. Client sendet eine normale /v1/chat/completions-Anfrage.
#   2. Der Proxy ergänzt den System-Prompt um Schach-Werkzeug-Anweisungen
#      (Text-Protokoll: TOOL/PARAMS – funktioniert mit JEDEM Modell,
#      auch ohne natives Function-Calling).
#   3. Antwortet das LLM mit TOOL/PARAMS, führt der Proxy das Werkzeug lokal
#      aus (Stockfish), hängt das Ergebnis an den Verlauf und fragt erneut.
#   4. Die finale Textantwort wird zurückgegeben (bei stream=true als SSE).
#
# Zusätzlich gibt es eine einfache REST-API unter /chess/api/<werkzeug>,
# damit OpenWebUI die Werkzeuge alternativ auch nativ (als OpenWebUI-Tool)
# anbinden kann (siehe openwebui_tool.py).
# ==============================================================================
import configparser
import hashlib
import json
import os
import re
import time
import uuid

from flask import Flask, jsonify, request, Response
from waitress import serve

from chess_service import baue_service, ChessServiceError
from llm_client import LLMClient, LLMError

BASIS_ORDNER = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------- #
# Konfiguration
# ---------------------------------------------------------------------------- #
WERKZEUGE = {
    "neue_partie":       {"eigene_farbe": "weiss|schwarz", "gegner_name": "Text"},
    "brett_ansehen":     {},
    "zug_machen":        {"zug": "SAN oder UCI, z.B. e4 oder e2e4"},
    "beste_zuege":       {"n": "1-5"},
    "stellung_bewerten": {},
    "zug_verlauf":       {},
    "partie_aufgeben":   {},
}

SYSTEM_ERGAENZUNG = """

# Schach-Werkzeuge (chess-proxy)
Du hast Zugriff auf eine lokale Schach-App mit einer Stockfish-Engine.
Wenn der Nutzer Schach spielen möchte oder Fragen zu Stellungen/Zügen hat,
nutze diese Werkzeuge.

Um ein Werkzeug zu nutzen, antworte AUSSCHLIESSLICH mit genau zwei Zeilen:
TOOL: <werkzeugname>
PARAMS: <einzeiliges JSON-Objekt mit den Parametern>

Verfügbare Werkzeuge:
- neue_partie: startet eine neue Partie. PARAMS: {"eigene_farbe": "weiss" oder "schwarz", "gegner_name": "<Name des Gegners>"}
- brett_ansehen: zeigt das aktuelle Brett an. PARAMS: {}
- zug_machen: führt einen Zug aus (SAN wie "Nf3"/"e4" oder UCI wie "g1f3"). PARAMS: {"zug": "<zug>"}
- beste_zuege: Stockfish-Analyse der aktuellen Stellung. PARAMS: {"n": 3}
- stellung_bewerten: kurze Engine-Einschätzung der Stellung. PARAMS: {}
- zug_verlauf: listet die bisherigen Züge auf. PARAMS: {}
- partie_aufgeben: beendet die laufende Partie. PARAMS: {}

Regeln:
- Führe pro Antwort GENAU EIN Werkzeug aus und sonst nichts.
- Danach erhältst du als nächste Nachricht das WERKZEUG-ERGEBNIS.
- PARTIEN-START: Wenn der Nutzer Schach spielen möchte, rufe zuerst
  'brett_ansehen' auf. Kommt als Ergebnis "FEHLER: Keine aktive Partie",
  rufe als Nächstes 'neue_partie' auf (Farbe des Nutzers erfragen oder
  aus seiner Nachricht entnehmen). Erst danach wird gespielt.
- ZUG-REGEL: Pro Nutzer-Nachricht führst du höchstens ZWEI Züge aus:
  1. den Zug des Menschen (wenn er dir einen Zug nennt) und
  2. deinen eigenen Antwortzug.
  Danach hörst du auf und wartest auf die nächste Nachricht des Nutzers.
  Spiele NIEMALS mehrere eigene Züge hintereinander und ziehe NIEMALS
  für den Menschen, ohne dass er dir seinen Zug genannt hat.
- Wenn du alle nötigen Werkzeuge ausgeführt hast, antworte dem Nutzer in
  normalem Fließtext (OHNE TOOL-Zeilen) und kommentiere die Partie mit
  deiner gewohnten Persönlichkeit.
- Geht es im Gespräch nicht um Schach, antworte ganz normal ohne Werkzeuge.
- Antworte dem Nutzer immer in der Sprache, die er selbst verwendet.
"""

TOOL_RE = re.compile(
    r"TOOL:\s*([A-Za-z_][A-Za-z0-9_]*)\s*\n\s*PARAMS:\s*(\{[^{}]*\})",
    re.IGNORECASE)
TOOL_RE_OHNE_PARAMS = re.compile(
    r"TOOL:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\n|$)", re.IGNORECASE)

# Block aus 2-8 Wörtern, der unmittelbar hintereinander wiederholt wird
_WIEDERHOLUNG_RE = re.compile(
    r"\b((?:\S+[ \t]+){2,7}\S+)(?:[ \t]+\1)+\b", re.IGNORECASE)

# Junk-Antworten mancher Modelle (nur Metadaten statt Inhalt)
_JUNK_RE = re.compile(
    r"(?:User\s+Safety|Response\s+Safety|Safety\s*:)\s*[:\w\s]*", re.IGNORECASE)


def kollabiere_wiederholungen(text):
    """Entfernt unmittelbar aufeinanderfolgende identische Zeilen und Wortblöcke.
    Gegen Degenerationen mancher Modelle ("ich warte – ich warte – ich warte")."""
    if not text:
        return text
    # 1) Aufeinanderfolgende identische Zeilen entfernen
    zeilen, letzte_zeile = [], None
    for zeile in text.splitlines():
        norm = zeile.strip().lower()
        if norm and norm == letzte_zeile:
            continue
        letzte_zeile = norm
        zeilen.append(zeile)
    text = "\n".join(zeilen)
    # 2) Wiederholte Wortblöcke kollabieren (Block kann nicht über Zeilen gehen,
    #    da \S+ und [ \t] keine Newlines matchen); iterieren bis stabil
    for _ in range(5):
        neu = _WIEDERHOLUNG_RE.sub(r"\1", text)
        if neu == text:
            break
        text = neu
    return text.strip()


def lade_konfig():
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(BASIS_ORDNER, "chess.ini"))
    chess_cfg = cfg["chess"] if cfg.has_section("chess") else {}
    proxy_cfg = cfg["proxy"] if cfg.has_section("proxy") else {}
    return {
        "host": proxy_cfg.get("PROXY_HOST", "0.0.0.0"),
        "port": int(proxy_cfg.get("PROXY_PORT", "8300")),
        "api_key": proxy_cfg.get("PROXY_API_KEY", "").strip(),
        "max_loops": int(proxy_cfg.get("MAX_TOOL_LOOPS", "6")),
        "max_tokens": int(proxy_cfg.get("PROXY_MAX_TOKENS", "500")),
        "modell_name": proxy_cfg.get("PROXY_MODEL_NAME",
                                     chess_cfg.get("LLM_MODEL", "llm-bahnhof")),
        "llm": {
            "base_url": chess_cfg.get("LLM_BASE_URL", "http://10.7.0.124:8000"),
            "model": chess_cfg.get("LLM_MODEL", "llm-bahnhof"),
            "api_key": chess_cfg.get("LLM_API_KEY", ""),
            "temperature": float(chess_cfg.get("LLM_TEMPERATURE", "0.7")),
            "timeout": int(chess_cfg.get("LLM_TIMEOUT", "120")),
            "max_tokens": int(chess_cfg.get("LLM_MAX_TOKENS", "300")),
        },
    }


# ---------------------------------------------------------------------------- #
# App-Erzeugung ( closures für Konfig/Service/LLM – gut testbar )
# ---------------------------------------------------------------------------- #
def erstelle_app(konfig, service, llm):
    app = Flask("chess-proxy")
    app.config["JSON_AS_ASCII"] = False

    # ------------------------------------------------------------------ #
    # Helfer
    # ------------------------------------------------------------------ #
    def api_schluessel_pruefen():
        erwartet = konfig["api_key"]
        if not erwartet:
            return True
        auth = request.headers.get("Authorization", "")
        return auth == f"Bearer {erwartet}"

    def session_schluessel():
        """Eine Partie pro Client (OpenWebUI sendet seinen API-Key als
        Authorization-Header). Ohne Header: gemeinsame Session 'default'."""
        auth = request.headers.get("Authorization", "")
        if auth:
            return "s-" + hashlib.sha256(auth.encode("utf-8")).hexdigest()[:12]
        return request.get_json(silent=True).get("session_id", "default") \
            if request.get_json(silent=True) else "default"

    def ergaenze_system_prompt(nachrichten):
        msgs = list(nachrichten)
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = dict(msgs[0])
            msgs[0]["content"] = (msgs[0].get("content") or "") + SYSTEM_ERGAENZUNG
        else:
            msgs.insert(0, {"role": "system", "content": SYSTEM_ERGAENZUNG.strip()})
        return msgs

    def parse_tool_call(text):
        treffer = TOOL_RE.search(text or "")
        if treffer:
            name = treffer.group(1).lower()
            try:
                params = json.loads(treffer.group(2))
                if not isinstance(params, dict):
                    params = {}
            except json.JSONDecodeError:
                params = {}
            return name, params
        treffer = TOOL_RE_OHNE_PARAMS.search(text or "")
        if treffer:
            return treffer.group(1).lower(), {}
        return None

    def fuehre_tool_aus(name, params, sid):
        if name not in WERKZEUGE:
            bekannt = ", ".join(sorted(WERKZEUGE))
            raise ChessServiceError(
                f"Unbekanntes Werkzeug '{name}'. Bekannt: {bekannt}")
        if name == "neue_partie":
            return service.neue_partie(
                sid,
                eigene_farbe=params.get("eigene_farbe", "weiss"),
                gegner_name=params.get("gegner_name", "Mensch"))
        if name == "brett_ansehen":
            return service.brett_ansehen(sid)
        if name == "zug_machen":
            return service.zug_machen(sid, str(params.get("zug", "")))
        if name == "beste_zuege":
            return service.beste_zuege(sid, params.get("n", 3))
        if name == "stellung_bewerten":
            return service.stellung_bewerten(sid)
        if name == "zug_verlauf":
            return service.zug_verlauf(sid)
        if name == "partie_aufgeben":
            return service.partie_aufgeben(sid)
        raise ChessServiceError(f"Werkzeug '{name}' ist nicht implementiert.")

    def entferne_tool_zeilen(text):
        zeilen = []
        for zeile in (text or "").splitlines():
            if re.match(r"\s*(TOOL|PARAMS):", zeile, re.IGNORECASE):
                continue
            zeilen.append(zeile)
        rest = "\n".join(zeilen).strip()
        return rest

    def sse_antwort(text, modell):
        def generiere():
            cid = "chatcmpl-chess-" + uuid.uuid4().hex[:24]
            jetzt = int(time.time())

            def chunk(delta, finish=None):
                return "data: " + json.dumps({
                    "id": cid, "object": "chat.completion.chunk",
                    "created": jetzt, "model": modell,
                    "choices": [{"index": 0, "delta": delta,
                                 "finish_reason": finish}],
                }, ensure_ascii=False) + "\n\n"

            yield chunk({"role": "assistant"})
            for i in range(0, len(text), 80):
                yield chunk({"content": text[i:i + 80]})
            yield chunk({}, finish="stop")
            yield "data: [DONE]\n\n"

        return Response(generiere(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ #
    # Routen: Chat (OpenAI-kompatibel)
    # ------------------------------------------------------------------ #
    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "chess-proxy"})

    @app.route("/v1/models", methods=["GET"])
    @app.route("/models", methods=["GET"])
    def modelle():
        if not api_schluessel_pruefen():
            return jsonify({"error": {"message": "Ungültiger API-Key",
                                      "type": "auth_error"}}), 401
        jetzt = int(time.time())
        return jsonify({"object": "list", "data": [
            {"id": konfig["modell_name"], "object": "model", "created": jetzt,
             "owned_by": "chess-proxy"},
        ]})

    @app.route("/v1/chat/completions", methods=["POST"])
    @app.route("/chat/completions", methods=["POST"])
    def chat_completions():
        if not api_schluessel_pruefen():
            return jsonify({"error": {"message": "Ungültiger API-Key",
                                      "type": "auth_error"}}), 401
        body = request.get_json(force=True, silent=True) or {}
        nachrichten = body.get("messages") or []
        if not nachrichten:
            return jsonify({"error": {"message": "'messages' fehlt",
                                      "type": "invalid_request_error"}}), 400
        stream = bool(body.get("stream"))
        sid = session_schluessel()

        msgs = ergaenze_system_prompt(nachrichten)
        antwort = ""
        ausgefuehrte_calls = []   # für Wiederholungs-Erkennung
        zug_machen_zaehler = 0    # max. 2 Züge pro Anfrage (Nutzer + LLM)
        try:
            for loop_nr in range(konfig["max_loops"]):
                antwort = llm.chat(msgs, max_tokens=konfig["max_tokens"])
                print(f"[chess-proxy] Loop {loop_nr + 1}: "
                      f"LLM-Antwort ({len(antwort)} Zeichen): "
                      f"{antwort[:120].replace(chr(10), ' / ')}", flush=True)
                # Manche Upstream-Modelle antworten gelegentlich mit Junk
                # ("User Safety: safe", z.B. aionlabs) statt echter Antwort
                junk = _JUNK_RE.fullmatch(antwort.strip())
                if junk:
                    msgs.append({"role": "assistant", "content": antwort})
                    msgs.append({
                        "role": "user",
                        "content": ("Deine letzte Nachricht enthielt keine echte "
                                    "Antwort. Antworte jetzt dem Nutzer "
                                    "entsprechend – bei Bedarf zuerst mit einem "
                                    "Werkzeug im TOOL-/PARAMS-Format."),
                    })
                    continue
                call = parse_tool_call(antwort)
                if not call:
                    break
                name, params = call
                # Gleicher Aufruf doppelt? -> Schleife abbrechen
                kennung = name + ":" + json.dumps(params, sort_keys=True)
                if kennung in ausgefuehrte_calls:
                    print(f"[chess-proxy] Wiederholter Aufruf abgebrochen: "
                          f"{kennung[:80]}", flush=True)
                    break
                ausgefuehrte_calls.append(kennung)
                # Mehr als 2 Züge pro Anfrage verhindern (Nutzer + LLM-Antwort)
                if name == "zug_machen":
                    zug_machen_zaehler += 1
                    if zug_machen_zaehler > 2:
                        ergebnis = ("HINWEIS: Pro Nachricht sind maximal zwei "
                                    "Züge erlaubt (der Zug des Menschen und "
                                    "deine Antwort). Jetzt bist du wieder am "
                                    "Zug mit dem Antworten – warte auf die "
                                    "nächste Nachricht des Nutzers.")
                        antwort = ""
                        msgs.append({"role": "assistant",
                                     "content": antwort or "TOOL-Aufruf gestoppt."})
                        msgs.append({"role": "user", "content": ergebnis})
                        continue
                name, params = call
                try:
                    ergebnis = fuehre_tool_aus(name, params, sid)
                except ChessServiceError as exc:
                    ergebnis = f"FEHLER: {exc}"
                except Exception as exc:  # unerwartet – trotzdem weitermachen
                    ergebnis = f"FEHLER (intern): {exc!r}"
                msgs.append({"role": "assistant", "content": antwort})
                msgs.append({
                    "role": "user",
                    "content": (f"WERKZEUG-ERGEBNIS ({name}):\n{ergebnis}\n\n"
                                "Nutze das Ergebnis. Falls du noch ein weiteres "
                                "Werkzeug brauchst, antworte wieder im TOOL-"
                                "Format. Sonst antworte dem Nutzer in Fließtext."),
                })
            else:
                antwort = (entferne_tool_zeilen(antwort)
                           or "Ich habe die maximale Anzahl an Werkzeug-Aufrufen "
                              "erreicht. Bitte frag noch einmal konkret nach.")
        except LLMError as exc:
            return jsonify({"error": {"message": str(exc),
                                      "type": "upstream_error"}}), 502

        finale = entferne_tool_zeilen(antwort) or \
            "Das Werkzeug lieferte keine Antwort. Bitte versuch es erneut."
        finale = kollabiere_wiederholungen(finale)
        if stream:
            return sse_antwort(finale, konfig["modell_name"])

        return jsonify({
            "id": "chatcmpl-chess-" + uuid.uuid4().hex[:24],
            "object": "chat.completion",
            "created": int(time.time()),
            "model": konfig["modell_name"],
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": finale},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
        })

    # ------------------------------------------------------------------ #
    # Routen: REST-API für die Schach-Werkzeuge (z.B. für openwebui_tool.py)
    # ------------------------------------------------------------------ #
    @app.route("/chess/api/<werkzeug>", methods=["POST", "GET"])
    def chess_api(werkzeug):
        if not api_schluessel_pruefen():
            return jsonify({"error": "Ungültiger API-Key"}), 401
        body = request.get_json(force=True, silent=True) or {}
        sid = (body.get("session_id")
               or request.args.get("session_id")
               or "default")
        try:
            if werkzeug == "neue_partie":
                text = service.neue_partie(
                    sid,
                    eigene_farbe=body.get("eigene_farbe", "weiss"),
                    gegner_name=body.get("gegner_name", "Mensch"))
            elif werkzeug == "brett_ansehen":
                text = service.brett_ansehen(sid)
            elif werkzeug == "zug_machen":
                text = service.zug_machen(sid, str(body.get("zug", "")))
            elif werkzeug == "beste_zuege":
                text = service.beste_zuege(sid, body.get("n", 3))
            elif werkzeug == "stellung_bewerten":
                text = service.stellung_bewerten(sid)
            elif werkzeug == "zug_verlauf":
                text = service.zug_verlauf(sid)
            elif werkzeug == "partie_aufgeben":
                text = service.partie_aufgeben(sid)
            else:
                return jsonify({"error": f"Unbekanntes Werkzeug '{werkzeug}'"}), 404
        except ChessServiceError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"result": text, "session_id": sid})

    @app.errorhandler(404)
    def nicht_gefunden(_):
        return jsonify({"error": "Pfad nicht gefunden"}), 404

    @app.errorhandler(500)
    def server_fehler(_):
        return jsonify({"error": "Interner Fehler im chess-proxy"}), 500

    return app


# ---------------------------------------------------------------------------- #
# Start
# ---------------------------------------------------------------------------- #
def main():
    konfig = lade_konfig()
    service = baue_service()
    llm = LLMClient(
        base_url=konfig["llm"]["base_url"],
        model=konfig["llm"]["model"],
        api_key=konfig["llm"]["api_key"],
        temperature=konfig["llm"]["temperature"],
        timeout=konfig["llm"]["timeout"],
        max_tokens=konfig["llm"]["max_tokens"],
    )
    app = erstelle_app(konfig, service, llm)
    print(f"chess-proxy läuft auf http://{konfig['host']}:{konfig['port']} "
          f"(Modell '{konfig['modell_name']}' via {konfig['llm']['base_url']})",
          flush=True)
    serve(app, host=konfig["host"], port=konfig["port"], threads=8)


if __name__ == "__main__":
    main()
