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
import ast
import configparser
import hashlib
import json
import os
import re
import time
import uuid

from flask import Flask, jsonify, request, Response
from waitress import serve

from chess_service import baue_service, german_to_san, ChessServiceError
from llm_client import LLMClient, LLMError
from version import VERSION

BASIS_ORDNER = os.path.dirname(os.path.abspath(__file__))
_PARTIE_KONTEXT_MARKER = "\n\n=== INTERNER PARTIE-KONTEXT ==="

# ---------------------------------------------------------------------------- #
# Konfiguration
# ---------------------------------------------------------------------------- #
WERKZEUGE = {
    "neue_partie":       {"eigene_farbe": "weiss|schwarz", "gegner_name": "Text"},
    "brett_ansehen":     {},
    "gegner_zug":        {"zug": "Wörtlicher Zug des Menschen – wird SOFORT ausgeführt"},
    "zug_machen":        {},  # ohne Parameter – das LLM spielt selbst
    "beste_zuege":       {"n": "1-5"},
    "stellung_bewerten": {},
    "zug_verlauf":       {},
    "partie_aufgeben":   {},
}

SYSTEM_ERGAENZUNG = """

# Schach-Werkzeuge (chess-proxy)
Du spielst hier selbst eine regelkonforme Schachpartie. Nutze die lokalen
Schach-Werkzeuge als dein Brett und deine Zugkontrolle.
Wenn der Nutzer Schach spielen möchte oder Fragen zu Stellungen/Zügen hat,
nutze diese Werkzeuge.

Um ein Werkzeug zu nutzen, antworte AUSSCHLIESSLICH mit genau zwei Zeilen:
TOOL: <werkzeugname>
PARAMS: <einzeiliges JSON-Objekt mit den Parametern>

Verfügbare Werkzeuge:
- neue_partie: startet eine neue Partie. PARAMS: {"eigene_farbe": "weiss" oder "schwarz", "gegner_name": "<Name des Gegners>"}
- brett_ansehen: zeigt das aktuelle Brett an. PARAMS: {}
- gegner_zug: führt den ZUG DES MENSCHEN aus (SAN wie "e5" oder UCI wie "e7e5"). PARAMS: {"zug": "<zug>"}. Ausführung ist AUTOMATISCH: Sobald der Mensch einen Zug nennt, rufst du sofort 'gegner_zug' mit seinem wörtlichen Zug auf – keine Rückfragen, keine Vorschläge, keine Ankündigung wie "Ich setze deinen Zug um".
- zug_machen: spielt DEINEN Zug. PARAMS: {} (KEIN 'zug' angeben)
- beste_zuege: zeigt dir die besten Züge für die aktuelle Stellung. PARAMS: {"n": 3}
- stellung_bewerten: bewertet die aktuelle Stellung. PARAMS: {}
- zug_verlauf: listet die bisherigen Züge auf. PARAMS: {}
- partie_aufgeben: beendet die laufende Partie. PARAMS: {}

Regeln:
- Führe pro Antwort GENAU EIN Werkzeug aus und sonst nichts.
- Danach erhältst du als nächste Nachricht das WERKZEUG-ERGEBNIS.
- FARBEN: 'eigene_farbe' bei 'neue_partie' ist IMMER DEINE Farbe als LLM,
  nicht die des Menschen! Nennt der Nutzer explizit seine eigene Farbe
  ("ich möchte weiß/schwarz"), setzt du die GEGENSEITE als 'eigene_farbe'
  und gibst zusätzlich 'nutzer_farbe' an. Beispiel: Nutzer sagt "ich
  spiele weiß" → {"eigene_farbe":"schwarz","gegner_name":"…",
  "nutzer_farbe":"weiss"}. Der ZUSTAND-Block zeigt dir danach immer
  "DU BIST: …" – ist das unerwartet, starte die Partie korrekt neu.
- WER ZIEHT WEN: Nennt der Mensch einen eigenen Zug, benutze AUSSCHLIESSLICH
  'gegner_zug' dafür – NIE 'zug_machen'. Bist DU am Zug, rufe 'zug_machen'
  OHNE Parameter auf: Das Werkzeug spielt dann deinen Zug. Du wählst Züge
  NIEMALS für den Menschen aus. Der im WERKZEUG-ERGEBNIS genannte Zug ist
  der tatsächlich
  gespielte – nur er zählt, zitiere ihn wörtlich.
- ANTI-HALLUZINATION: Die MAßGEBLICHE Quelle für Stellung, Verlauf und
  Zustand ist der aktuelle PARTIE-KONTEXT sowie der ZUSTAND-Block in den
  Werkzeug-Ergebnissen. Erfinde NIEMALS Züge, Stellungen, Verläufe oder
  Ergebnisse. Nenne dem Menschen niemals einen "deinen" Zug, den du nicht
  vorher per 'zug_machen' ausgeführt hast – zitiere im Zweifel wörtlich aus
  dem aktuellen Kontext oder ZUSTAND-Block.
- TOOL-PFLICHT: Der PARTIE-KONTEXT ist nur eine Gedächtnisstütze und ersetzt
  kein Werkzeug. Menschenszüge müssen über 'gegner_zug' ausgeführt werden;
  eigene Züge ausschließlich über 'zug_machen'. Behaupte niemals einen Zug,
  bevor das zugehörige Werkzeug-Ergebnis vorliegt.
- ZUG DES MENSCHEN: Wenn der Mensch einen Zug nennt, prüfe ihn gegen die
  legale Zugliste (aus 'brett_ansehen' oder der Fehlermeldung von
  'zug_machen'). Ist sein Zug nicht dabei, sage das klar und nenne legale
  Alternativen – ziehe NIEMALS für ihn und "verbessere" seinen Zug nie
  stillschweigend.
- WER IST AM ZUG: Ist DU laut ZUSTAND am Zug, darfst du ziehen. Bist du
  NICHT am Zug, darfst du nur 'brett_ansehen', 'beste_zuege',
  'stellung_bewerten' oder 'zug_verlauf' aufrufen – NIEMALS 'zug_machen'.
- PARTIEN-START: Wenn der Nutzer Schach spielen möchte, rufe zuerst
  'brett_ansehen' auf. Kommt als Ergebnis "FEHLER: Keine aktive Partie",
  rufe als Nächstes 'neue_partie' auf (Farbe des Nutzers erfragen oder
  aus seiner Nachricht entnehmen). Erst danach wird gespielt.
- ZUG-REGEL: Pro Nutzer-Nachricht führst du höchstens ZWEI Züge aus:
  1. den Zug des Menschen (wenn er dir einen Zug nennt) über 'gegner_zug'
  2. danach zwingend deinen eigenen Antwortzug über 'zug_machen'.
  Nach dem Menschenzug ist jede freie Antwort verboten: Rufe zuerst
  'zug_machen' auf. Warte danach auf die nächste Nutzernachricht.
  Spiele NIEMALS mehrere eigene Züge hintereinander und ziehe NIEMALS
  für den Menschen, ohne dass er dir seinen Zug genannt hat.
- Wenn du alle nötigen Werkzeuge ausgeführt hast, antworte dem Nutzer in
  normalem Fließtext (OHNE TOOL-Zeilen) und kommentiere die Partie mit
  deiner gewohnten Persönlichkeit.
- FEHLERFORMATE: Manche Modelle antworten mit XML-Argumenten wie
  "<function=brett_ansehen>" oder schalten ihren Denkprozess ein
  ("Here's a thinking process ..."). Beides ist FALSCH: Benutze
  AUSSCHLIESSLICH das TOOL:/PARAMS:-Format und zeige dem Menschen
  NIEMALS deine Gedanken, Analyse-Schritte oder Nummerierungen von
  Überlegungen – nur die fertige Antwort.
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

# Zug-Tokens (SAN oder UCI) für die Integritätsprüfung der Finalantwort
_ZUG_TOKEN_RE = re.compile(
    r"\b(?:O-O-O|O-O|0-0-0|0-0"
    # Figurenbuchstaben englisch (K,N,R,B,Q) und deutsch (K,S,T,L,D) –
    # deutsche Notation wird von den Modellen auch GROSS geschrieben ("Dh8").
    r"|[KQRBStldTLD]?[a-h]?[1-8]?x?[a-h][1-8](?:=[QRBSqrbnDd])?[+#]?"
    r"|[a-h][1-8][a-h][1-8][qrbnQRBNDd]?)\b")
# Reine Feldangabe ohne Zugcharakter ("der Bauer kann auf c3 zurückschlagen")
_FELD_RE = re.compile(r"[a-h][1-8]", re.IGNORECASE)

# Zeilen in ZUSTAND-Blöcken, die die Server-Ausführung dokumentieren
_ZUSTAND_AUSFUEHRUNG_RE = re.compile(
    r"^\s*(?:Verlauf:|Zug des Menschen:|Dein Zug:).*", re.IGNORECASE)

# XML-artige Werkzeugaufrufe schwacher Modelle:
#   <dots_function_call> / <function=brett_ansehen> / <parameter name=...>
_XML_TOOL_RE = re.compile(r"<\s*(?:function|tool|tool_call)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)",
                          re.IGNORECASE)

# Native Tool-Tokens mancher Fine-Tunes (Qwen/Hermes/„fish“-Modelle), die
# der Proxy bisher NICHT erkannt hat und die deshalb als Text beim Nutzer
# landeten (siehe bug-chat-export-*.json):
#   <|tool_call_begin|>[zug_machen({"zug": "e4"})]
#   <|tool_call_start|>[brett_ansehen()]<|tool_call_end|>
# Mehrere Aufrufe stehen in einer Nachricht unmittelbar hintereinander.
_LEAKED_TOOL_RE = re.compile(
    r"<\|tool_call_(?:begin|start)\|>\s*\[\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([^()]*?)\s*\)\s*\]",
    re.IGNORECASE)

# Übrig gebliebene Marker (z. B. alleinstehendes <|tool_call_end|>)
_TOOL_MARKER_RE = re.compile(
    r"<\|\s*tool_call_(?:begin|start|end)\s*\|>", re.IGNORECASE)

# Leaked Chain-of-Thought ("Here's a thinking process", "**Analyze ...")
_GEDANKEN_RE = re.compile(
    r"^\s*(?:Here'?s (?:a |my |the )?(?:thinking|thought|reasoning)(?: process)?"
    r"|Let me think(?: this)? (?:through|step by step)"
    r"|\*\*(?:Step|Analysis|Analyze|Plan)\b\*\*|(?:(?:1|2|3)\.\s+\*\*Analyze))",
    re.IGNORECASE | re.MULTILINE)

# Reine Ankündigung ohne Ausführung ("Dein Zug d4 wurde gespielt")
_ANKUENDIGUNG_RE = re.compile(
    r"\bwurde\s+(?:gespielt|ausgeführt|umgesetzt|eingegeben)\b", re.IGNORECASE)

# Normierte Ermahnungen (einmal pro Anfrage und Kontext)
_ERMAHNUNGSTEXTE = {
    "gedanken": (
        "Deine letzte Antwort enthielt interne Gedanken/Analyse – das ist "
        "verboten. Gib dem Menschen NUR die fertige Antwort; Denkarbeit "
        "geschieht unsichtbar, niemals im Antworttext."),
    "phantom_zug": (
        "Deine letzte Antwort enthielt Züge, die vom Server NICHT ausgeführt "
        "wurden. Erfinde niemals Züge! Die einzige verbindliche Quelle ist "
        "der ZUSTAND-Block."),
    "ausfuehrung": (                    "Du hast den Zug des Menschen nur angekündigt statt auszuführen. "
        "Ankündigungen sind unzulässig – das Werkzeug führt jeden genannten "

        "Zug sofort aus. Announce-only wird nicht akzeptiert."),
}


def extrahiere_zug_tokens(text):
    return _ZUG_TOKEN_RE.findall(text or "")


def ohne_schachsuffix(token):
    """Entfernt Schach-/Matt-Zeichen ("Bxf7+" -> "Bxf7").

    Für den Vergleich zählt der Zug, nicht die Angabe ob er Schach gibt:
    Modelle schreiben "Lxf7", gespielt wurde aber "Bxf7+".
    """
    return (token or "").rstrip("+#")


def _teile_argumente(roh):
    """Zerlegt eine Argumentliste an Kommas, die nicht in Anführungszeichen
    oder Klammern stehen (zug="e4", n=3)."""
    teile, puffer, tiefe, quote = [], [], 0, ""
    for zeichen in roh:
        if quote:
            puffer.append(zeichen)
            if zeichen == quote:
                quote = ""
        elif zeichen in "\"'":
            quote = zeichen
            puffer.append(zeichen)
        elif zeichen in "([{":
            tiefe += 1
            puffer.append(zeichen)
        elif zeichen in ")]}":
            tiefe -= 1
            puffer.append(zeichen)
        elif zeichen == "," and tiefe == 0:
            teile.append("".join(puffer))
            puffer = []
        else:
            puffer.append(zeichen)
    if puffer:
        teile.append("".join(puffer))
    return [t for t in (s.strip() for s in teile) if t]


def _parse_params(roh):
    """Robust ein Werkzeug-Parameter-Objekt aus JSON/Python-Literal bauen.

    Unterstützt zusätzlich die Schlüsselwort-Form, die Modelle in nativen
    Tool-Tokens liefern – z. B. `zug="e2e4"`, `zug='e2e4'` oder `n=3`.
    Ohne diese Form landete z. B. `[gegner_zug(zug="b8c6")]` als leeres
    Parameter-Objekt, wodurch der Menschenzug verloren ging (bug-2json).
    """
    roh = (roh or "").strip()
    if not roh:
        return {}
    for parser in (json.loads, ast.literal_eval):
        try:
            params = parser(roh)
        except (ValueError, SyntaxError):
            continue
        return params if isinstance(params, dict) else {}
    # Schlüsselwort-Form: key=value, ... (Werte als Literal oder roher Text)
    params = {}
    for teil in _teile_argumente(roh):
        schluessel, trenner, wert = teil.partition("=")
        schluessel = schluessel.strip()
        if not trenner or not schluessel:
            return {}
        wert = wert.strip()
        try:
            wert = ast.literal_eval(wert)
        except (ValueError, SyntaxError):
            pass  # unquotierter Wert (zug=e2e4) bleibt ein String
        params[schluessel] = wert
    return params


def normalisiere_zug_token(token):
    """Deutsche Notation (Sf3, Td8, 0-0) auf SAN (Nf3, Rd8, O-O) bringen.

    Die Übersetzung liegt in chess_service (german_to_san), damit Werkzeuge
    und Proxy dieselbe Regel verwenden und englische Notation unberührt
    bleibt."""
    return german_to_san(token)


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
        """Ermittelt die Partie-ID des Clients.

        OpenWebUI kann für mehrere Chats denselben Authorization-Header
        verwenden. Deshalb haben explizite Sitzungs-/Konversations-IDs
        Vorrang; der API-Key bleibt nur der Fallback.
        """
        body = request.get_json(silent=True) or {}
        for key in ("session_id", "conversation_id", "chat_id"):
            wert = body.get(key) or request.headers.get(
                "X-Session-ID" if key == "session_id" else f"X-{key.replace('_', '-').title()}")
            if wert:
                return str(wert)[:128]
        auth = request.headers.get("Authorization", "")
        if auth:
            return "s-" + hashlib.sha256(auth.encode("utf-8")).hexdigest()[:12]
        return "default"

    def ergaenze_system_prompt(nachrichten):
        msgs = list(nachrichten)
        if msgs and msgs[0].get("role") == "system":
            msgs[0] = dict(msgs[0])
            msgs[0]["content"] = (msgs[0].get("content") or "") + SYSTEM_ERGAENZUNG
        else:
            msgs.insert(0, {"role": "system", "content": SYSTEM_ERGAENZUNG.strip()})
        return msgs

    def aktualisiere_partie_kontext(nachrichten, sid):
        """Fügt den aktuellen serverseitigen Mini-RAG-Kontext ein.

        Der Kontext wird vor jedem LLM-Aufruf neu erzeugt. Er ist nur ein
        interner Prompt-Bestandteil; Tool-Aufrufe bleiben für jeden Zug
        zwingend erforderlich.
        """
        bereinigte = []
        for msg in nachrichten:
            if msg.get("role") == "system":
                kopie = dict(msg)
                inhalt = kopie.get("content") or ""
                if _PARTIE_KONTEXT_MARKER in inhalt:
                    inhalt = inhalt.split(_PARTIE_KONTEXT_MARKER, 1)[0].rstrip()
                kopie["content"] = inhalt
                bereinigte.append(kopie)
            else:
                bereinigte.append(msg)

        try:
            kontext = service.prompt_kontext(sid)
        except ChessServiceError:
            kontext = "(Keine aktive Partie. Bei Spielbeginn zuerst 'neue_partie' aufrufen.)"
        except Exception:
            kontext = "(Partie-Kontext momentan nicht verfügbar.)"

        for msg in bereinigte:
            if msg.get("role") == "system":
                msg["content"] = (msg.get("content") or "") + \
                    _PARTIE_KONTEXT_MARKER + "\n" + kontext
                break
        else:
            bereinigte.insert(0, {
                "role": "system",
                "content": _PARTIE_KONTEXT_MARKER.lstrip("\\n") + "\n" + kontext,
            })
        nachrichten[:] = bereinigte

    def parse_tool_calls(text):
        """Liefert ALLE Werkzeugaufrufe einer LLM-Antwort als [(name, params)].

        Ein Modell kann in einer Nachricht mehrere Aufrufe hintereinander
        ausgeben. Unterstützt werden das Text-Protokoll (TOOL:/PARAMS:), das
        XML-Format (<function=…>) und die nativen Tool-Tokens von Fine-Tunes
        (<|tool_call_begin|>[name({…})])."""
        text = text or ""
        # 1) Native Tool-Tokens – haben Vorrang, da sie sonst als Text
        #    beim Nutzer landen würden.
        aufrufe = [
            (t.group(1).lower(), _parse_params(t.group(2)))
            for t in _LEAKED_TOOL_RE.finditer(text)
        ]
        if aufrufe:
            return aufrufe
        # 2) Text-Protokoll: TOOL: <name>\nPARAMS: {…}
        aufrufe = [
            (t.group(1).lower(), _parse_params(t.group(2)))
            for t in TOOL_RE.finditer(text)
        ]
        if aufrufe:
            return aufrufe
        # 3) Text-Protokoll ohne Parameter: TOOL: <name>
        aufrufe = [(t.group(1).lower(), {})
                   for t in TOOL_RE_OHNE_PARAMS.finditer(text)]
        if aufrufe:
            return aufrufe
        # 4) Schwache Modelle nutzen gelegentlich ein XML-Format:
        #    <function=brett_ansehen> ... </function>
        #    Wir akzeptieren es großzügig statt den Loop zu verbrauchen.
        return [(t.group(1).lower(), {})
                for t in _XML_TOOL_RE.finditer(text)]

    def fuehre_tool_aus(name, params, sid):
        if name not in WERKZEUGE:
            bekannt = ", ".join(sorted(WERKZEUGE))
            raise ChessServiceError(
                f"Unbekanntes Werkzeug '{name}'. Bekannt: {bekannt}")
        if name == "neue_partie":
            return service.neue_partie(
                sid,
                eigene_farbe=params.get("eigene_farbe", "weiss"),
                gegner_name=params.get("gegner_name", "Mensch"),
                nutzer_farbe=params.get("nutzer_farbe", ""))
        if name == "brett_ansehen":
            return service.brett_ansehen(sid)
        if name == "gegner_zug":
            if not params.get("zug"):
                return "HINWEIS_AUSFUEHRUNG: Führe den Zug des Menschen jetzt " \
                       "aus. Ruf 'gegner_zug' nochmals auf und gib seinen " \
                       "wörtlich genannten Zug als PARAMS {\"zug\": \"…\"} an."
            return service.gegner_zug(sid, str(params.get("zug", "")))
        if name == "zug_machen":
            return service.zug_machen(sid, "")
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
        # Nativer Tool-Aufruf (inkl. Marker) und übrige Marker entfernen –
        # zuerst den vollständigen Aufruf, dann alleinstehende Marker.
        text = _LEAKED_TOOL_RE.sub("", text or "")
        text = _TOOL_MARKER_RE.sub("", text)
        zeilen = []
        for zeile in text.splitlines():
            if re.match(r"\s*(TOOL|PARAMS):", zeile, re.IGNORECASE):
                continue
            # XML-artige Reste (<function=…>, <parameter …>) ebenfalls weg.
            if _XML_TOOL_RE.search(zeile):
                continue
            zeilen.append(zeile)
        rest = "\n".join(zeilen).strip()
        return rest

    def pruefe_finale(finale, sid):
        """Anti-Halluzination: Hängt eine Server-Korrektur an, wenn die
        Finalantwort Züge nennt, die weder gespielt noch aktuell legal sind."""
        tokens = extrahiere_zug_tokens(finale)
        if not tokens:
            return finale
        try:
            daten = service.pruef_daten(sid)
        except Exception:
            return finale  # keine aktive Partie -> nichts zu prüfen
        erlaubt_san = {ohne_schachsuffix(s)
                       for s in daten["gespielt"] + daten["legal"]}
        erlaubt_uci = {u.lower()
                       for u in daten["gespielt_uci"] + daten["legal_uci"]}
        falsche = []
        for tok in tokens:
            # Eine bloße Feldnennung ("der König deckt e7") ist keine
            # Zugansage: Sie ist von einem Bauernzug nicht zu unterscheiden
            # und würde in normaler Prosa ständig Fehlalarme auslösen.
            # Echte, nur behauptete Züge fängt der Loop-Schutz ab.
            if _FELD_RE.fullmatch(tok):
                continue
            norm = ohne_schachsuffix(normalisiere_zug_token(tok))
            if norm in erlaubt_san or norm.lower() in erlaubt_uci:
                continue
            falsche.append(tok)
        if not falsche:
            return finale
        uniq = ", ".join(dict.fromkeys(falsche))
        return (finale + "\n\n[Server-Korrektur: Diese Züge sind nicht Teil "
                f"der Partie: {uniq}. Maßgeblich ist allein der ZUSTAND-Block "
                "aus den Werkzeug-Ergebnissen.]")

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
        return jsonify({"status": "ok", "service": "chess-proxy",
                        "version": VERSION})

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
        zug_zaehler = {"zug_machen": 0, "gegner_zug": 0}  # je 1 pro Anfrage
        menschenzug_ausgefuehrt = False
        eigenerzug_ausgefuehrt = False
        _ermahnt = set()          # einmal pro Kontext ermahnen
        llm_ausfall = None        # Upstream-Fehler im Tool-Loop

        def hat_server_ausfuehrung():
            """True, wenn im Gespräch ein vom Server ausgeführter Zug
            dokumentiert ist (ZUSTAND-Block: Verlauf / Zug des Menschen /
            Dein Zug)."""
            for msg in msgs:
                inhalt = msg.get("content") or ""
                if msg.get("role") == "assistant" and "ZUSTAND" not in inhalt:
                    continue
                if any(_ZUSTAND_AUSFUEHRUNG_RE.match(zeile)
                       for zeile in inhalt.splitlines()):
                    return True
            return False

        def fuehre_menschen_zug_erzwungen():
            """Announce-only-Reparatur: Nennt die letzte echte Nutzernachricht
            einen legalen Zug, führt der Server ihn selbst aus (statt auf das
            schwache Modell zu warten). Nur einmal pro Anfrage; True bei
            Erfolg."""
            nonlocal menschenzug_ausgefuehrt
            if "ausfuehrung_erzwungen" in _ermahnt:
                return False
            letzte_nutzer = next(
                (m.get("content") or "" for m in reversed(msgs)
                 if m.get("role") == "user"
                 and not m.get("content", "").startswith(
                     ("WERKZEUG-ERGEBNIS", "SYSTEM-ERINNERUNG"))), "")
            tokens = extrahiere_zug_tokens(letzte_nutzer)
            if not tokens:
                return False
            try:
                ergebnis = service.gegner_zug(sid, tokens[0])
            except Exception:
                return False  # keine Partie / LLM am Zug / illegal -> Ermahnung
            _ermahnt.add("ausfuehrung_erzwungen")
            menschenzug_ausgefuehrt = True
            msgs.append({
                "role": "user",
                "content": (
                    f"WERKZEUG-ERGEBNIS (gegner_zug):\n"
                    f"{ergebnis}\n\nDer Menschenzug wurde ausgeführt. "
                    "Du bist jetzt am Zug. Rufe zwingend 'zug_machen' auf; "
                    "antworte noch nicht in Fließtext und erfinde keinen Zug."),
            })
            return True

        def fuehre_eigenen_zug_als_fallback_aus():
            """Sicherheitsfallback: niemals nach einem Menschenzug mit einem
            nicht ausgeführten eigenen Zug antworten."""
            nonlocal eigenerzug_ausgefuehrt
            if eigenerzug_ausgefuehrt:
                return None
            try:
                ergebnis = fuehre_tool_aus("zug_machen", {}, sid)
            except Exception:
                return None
            if isinstance(ergebnis, str) and not ergebnis.startswith("FEHLER"):
                eigenerzug_ausgefuehrt = True
                return ergebnis
            return None

        def _fehlverhalten_ermahnt(kontext):
            """Normierte Ermahnung als Nutzer-Nachricht anhängen; True, wenn
            dieser Kontext in dieser Anfrage bereits ermahnt wurde."""
            if kontext in _ermahnt:
                return True
            _ermahnt.add(kontext)
            msgs.append({
                "role": "user",
                "content": (
                    "SYSTEM-ERINNERUNG (serverseitig): "
                    + _ERMAHNUNGSTEXTE[kontext]
                    + " Rufe jetzt das passende Werkzeug im TOOL-/PARAMS-"
                      "Format auf – sonst nichts."),
            })
            return False
        try:
            for loop_nr in range(konfig["max_loops"]):
                # Mini-RAG vor JEDEM Modellaufruf aktualisieren. Die Daten
                # stammen direkt aus dem aktuellen Brett/Journaleintrag und
                # können keinen alten Tool-Output überstimmen.
                aktualisiere_partie_kontext(msgs, sid)
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
                calls = parse_tool_calls(antwort)
                if not calls:
                    # Nach jedem ausgeführten Menschenzug muss das Modell
                    # zwingend noch seinen eigenen Zug über das Werkzeug
                    # ausführen. Eine freie Antwort ist hier niemals zulässig.
                    if menschenzug_ausgefuehrt and not eigenerzug_ausgefuehrt:
                        msgs.append({"role": "assistant", "content": antwort})
                        msgs.append({
                            "role": "user",
                            "content": (
                                "SYSTEM-ERINNERUNG (serverseitig): Dein letzter "
                                "Menschenzug wurde ausgeführt. Du bist jetzt am Zug. "
                                "Rufe jetzt zwingend 'zug_machen' auf, damit dein "
                                "eigener Zug ausgeführt wird. Antworte noch nicht "
                                "in Fließtext und erfinde keinen Zug."),
                        })
                        continue
                    # Kein Tool, aber Gedankenleck oder bekanntes Phantom-Muster?
                    clean = _GEDANKEN_RE.search(antwort)
                    phantom = (not hat_server_ausfuehrung()
                               and _ZUG_TOKEN_RE.search(antwort or ""))
                    if (clean or phantom) and loop_nr < konfig["max_loops"] - 1:
                        if clean:
                            kontext = "gedanken"
                        elif _ANKUENDIGUNG_RE.search(antwort or ""):
                            kontext = "ausfuehrung"  # behauptete Ausführung
                        else:
                            kontext = "phantom_zug"
                        msgs.append({"role": "assistant", "content": antwort})
                        # Announce-only: Server repariert durch Zwangsausführung
                        if kontext == "ausfuehrung" and \
                                fuehre_menschen_zug_erzwungen():
                            print("[chess-proxy] Announce-only repariert: "
                                  "Zug serverseitig ausgeführt", flush=True)
                            continue
                        if kontext == "phantom_zug" and \
                                _fehlverhalten_ermahnt(kontext):
                            # Der Phantom-Verdacht ist meist ein Fehlalarm (die
                            # Antwort nennt nur Beispielzüge wie „e4“). Ein
                            # zweiter Loop kostet nur einen LLM-Aufruf und
                            # läuft ins Rate-Limit – die Antwort ist gültig.
                            print("[chess-proxy] Phantom-Ermahnung bereits "
                                  "erfolgt – Antwort wird ausgeliefert",
                                  flush=True)
                            break
                        _fehlverhalten_ermahnt(kontext)
                        continue
                    break
                # Eine Nachricht kann MEHRERE Aufrufe enthalten (native
                # Tool-Tokens von Fine-Tunes). Alle ausführen – so wird aus
                # "[gegner_zug(...), zug_machen(...)]" genau eine volle Runde.
                msgs.append({"role": "assistant", "content": antwort})
                neu_fragen = True
                for name, params in calls:
                    # Gleicher Aufruf doppelt? -> Modell einmal ermahnen; erst
                    # wenn es ihn trotzdem wiederholt, den Loop beenden.
                    kennung = name + ":" + json.dumps(params, sort_keys=True)
                    if kennung in ausgefuehrte_calls:
                        print(f"[chess-proxy] Wiederholter Aufruf: "
                              f"{kennung[:80]}", flush=True)
                        if "wiederholung" in _ermahnt:
                            neu_fragen = False
                        else:
                            _ermahnt.add("wiederholung")
                            msgs.append({
                                "role": "user",
                                "content": (
                                    "SYSTEM-ERINNERUNG (serverseitig): Den "
                                    f"Werkzeugaufruf '{name}' hast du in "
                                    "dieser Anfrage bereits ausgeführt – das "
                                    "Ergebnis steht oben. Wiederhole ihn "
                                    "nicht. Fahre stattdessen mit dem "
                                    "nächsten Schritt fort: Ist dein eigener "
                                    "Zug noch offen, rufe 'zug_machen' auf, "
                                    "sonst antworte dem Nutzer in Fließtext."),
                            })
                        break
                    # Mehr als 2 Züge pro Anfrage verhindern (Nutzer + LLM)
                    if name in ("zug_machen", "gegner_zug"):
                        zug_zaehler[name] += 1
                        if zug_zaehler[name] > 1:
                            antwort = ""
                            neu_fragen = False
                            msgs.append({
                                "role": "user",
                                "content": (
                                    "HINWEIS: Pro Nachricht ist maximal ein "
                                    "Menschen-Zug erlaubt – dieser wurde bereits "
                                    "ausgeführt. Der zweite wurde verworfen."),
                            })
                            break
                    try:
                        ergebnis = fuehre_tool_aus(name, params, sid)
                    except ChessServiceError as exc:
                        ergebnis = f"FEHLER: {exc}"
                    except Exception as exc:  # unerwartet – weitermachen
                        ergebnis = f"FEHLER (intern): {exc!r}"
                    # Nur tatsächlich ausgefuehrte Aufrufe gelten als erledigt;
                    # fehlgeschlagene oder leere Aufrufe darf das Modell erneut
                    # versuchen (sonst blockiert der Wiederholungs-Schutz die
                    # Reparatur und der Menschenzug geht verloren).
                    if isinstance(ergebnis, str) and not ergebnis.startswith(
                            ("FEHLER", "HINWEIS_AUSFUEHRUNG:")):
                        ausgefuehrte_calls.append(kennung)
                    if name == "gegner_zug" and isinstance(ergebnis, str) \
                            and not ergebnis.startswith(("FEHLER", "HINWEIS_AUSFUEHRUNG:")):
                        menschenzug_ausgefuehrt = True
                    elif name == "zug_machen" and isinstance(ergebnis, str) \
                            and not ergebnis.startswith("FEHLER"):
                        eigenerzug_ausgefuehrt = True
                    if isinstance(ergebnis, str) and \
                            ergebnis.startswith("HINWEIS_AUSFUEHRUNG:"):
                        # Das Werkzeug hat den Zug ausgeführt, das LLM wollte
                        # ihn nur vorbereiten -> sofortiger Ausführungszwang
                        ergebnis = ergebnis.split(":", 1)[1]
                        if not _fehlverhalten_ermahnt("ausfuehrung"):
                            break
                    naechster_schritt = (
                        "Du bist jetzt am Zug: Rufe zwingend 'zug_machen' auf; "
                        "antworte noch nicht in Fließtext."
                        if name == "gegner_zug" and menschenzug_ausgefuehrt
                        else "Nutze das Ergebnis. Falls du noch ein weiteres "
                             "Werkzeug brauchst, antworte wieder im TOOL-Format. "
                             "Sonst antworte dem Nutzer in Fließtext."
                    )
                    msgs.append({
                        "role": "user",
                        "content": (f"WERKZEUG-ERGEBNIS ({name}):\n{ergebnis}\n\n"
                                    + naechster_schritt),
                    })
                if not neu_fragen:
                    break
                continue
            else:
                antwort = (entferne_tool_zeilen(antwort)
                           or "Ich habe die maximale Anzahl an Werkzeug-Aufrufen "
                              "erreicht. Bitte frag noch einmal konkret nach.")
        except LLMError as exc:
            # Ein Ausfall des LLM darf eine laufende Partie nicht wegwerfen:
            # Liegt aus einem früheren Loop schon eine fertige Antwort vor,
            # wird sie unten regulär ausgeliefert (kein 502 für den Client).
            llm_ausfall = exc
            print(f"[chess-proxy] Upstream-Fehler im Loop: {exc}", flush=True)

        if llm_ausfall is not None and (not antwort or parse_tool_calls(antwort)):
            return jsonify({"error": {"message": str(llm_ausfall),
                                      "type": "upstream_error"}}), 502

        # Harte Sicherheitsgrenze: Der vom Menschen genannte Zug darf die
        # Anfrage NIE unausgeführt verlassen. Hat das Modell 'gegner_zug'
        # verpasst, leer oder fehlerhaft aufgerufen, spielt der Server ihn
        # jetzt selbst aus der letzten Nutzernachricht aus.
        if not menschenzug_ausgefuehrt and fuehre_menschen_zug_erzwungen():
            print("[chess-proxy] Finale Reparatur: Menschenzug serverseitig "
                  "ausgeführt", flush=True)

        # Danach gilt: Nach einem ausgeführten Menschenzug darf niemals eine
        # Antwort mit einem nicht ausgeführten eigenen Zug an den Nutzer
        # gelangen. Falls das Modell trotz Erinnerungen keinen Werkzeugaufruf
        # liefert, spielt das Backend den Zug als Fallback aus.
        if menschenzug_ausgefuehrt and not eigenerzug_ausgefuehrt:
            fallback_ergebnis = fuehre_eigenen_zug_als_fallback_aus()
            if fallback_ergebnis:
                try:
                    verlauf = service.pruef_daten(sid)["verlauf"]
                except Exception:
                    verlauf = ""
                antwort = ("Dein Zug wurde ausgeführt – ich habe mit meinem "
                           "Zug geantwortet.")
                if verlauf:
                    antwort += "\n\nPartieverlauf: " + verlauf
            else:
                antwort = "Ich konnte meinen Zug nicht regelkonform ausführen. Bitte versuch es erneut."

        finale = entferne_tool_zeilen(antwort)
        if not finale:
            # Letzte Absicherung: Lief die Partie serverseitig weiter, darf
            # beim Nutzer niemals eine leere Antwort bzw. „keine Antwort“
            # ankommen (Symptom aus bug-2json).
            if menschenzug_ausgefuehrt or eigenerzug_ausgefuehrt:
                try:
                    verlauf = service.pruef_daten(sid)["verlauf"]
                except Exception:
                    verlauf = ""
                finale = "Ich habe gezogen – der Zug steht."
                if verlauf:
                    finale += "\n\nPartieverlauf: " + verlauf
            else:
                finale = ("Das Werkzeug lieferte keine Antwort. "
                          "Bitte versuch es erneut.")
        finale = kollabiere_wiederholungen(finale)
        finale = pruefe_finale(finale, sid)
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
                    gegner_name=body.get("gegner_name", "Mensch"),
                    nutzer_farbe=body.get("nutzer_farbe", ""))
            elif werkzeug == "brett_ansehen":
                text = service.brett_ansehen(sid)
            elif werkzeug == "gegner_zug":
                zug = body.get("zug", "")
                if not zug:
                    return jsonify({"error": "Parameter 'zug' fehlt"}), 400
                text = service.gegner_zug(sid, str(zug))
            elif werkzeug == "zug_machen":
                text = service.zug_machen(sid, "")
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
    print(f"chess-proxy v{VERSION} läuft auf http://{konfig['host']}:{konfig['port']} "
          f"(Modell '{konfig['modell_name']}' via {konfig['llm']['base_url']})",
          flush=True)
    serve(app, host=konfig["host"], port=konfig["port"], threads=8)


if __name__ == "__main__":
    main()
