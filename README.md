# chess – LLM spielt Schach (mit Stockfish im Hintergrund)

Anbindung eines beliebigen LLM (OpenAI-kompatibles Chat-API) an die
Schach-Engine **Stockfish** – auf zwei Wegen:

1. **Tool-API (Empfehlung):** Ein OpenAI-kompatibler Proxy mit eingebauten
   Schach-Werkzeugen. Der Nutzer chattet in **OpenWebUI** (oder jedem
   OpenAI-Client) mit dem LLM, und das LLM bedient die Schach-App als
   Werkzeug (neue Partie, Züge, Stockfish-Analyse, Brettansicht …).
2. **Konsolen-App:** Klassisches Terminal-Spiel Mensch gegen LLM.

```
OpenWebUI ──► chess-proxy (Port 8300) ──► llm-bahnhof/Ollama/… (LLM)
                  │
                  └─► Stockfish + Spielverwaltung (chess_service)
```

## Komponenten

| Datei               | Aufgabe                                                              |
|---------------------|----------------------------------------------------------------------|
| `chess_proxy.py`    | OpenAI-kompatibler Proxy mit Tool-Loop + REST-API (Port 8300)        |
| `chess_service.py`  | Spielverwaltung + Schach-Werkzeuge (thread-sicher, PGN-Archiv)       |
| `openwebui_tool.py` | Optionale OpenWebUI-Tool-Klasse (natives Function-Calling)           |
| `start_proxy.sh`    | Start/Stop des Proxys (`start|stop|restart|status`)                  |
| `proxy.log`         | Log des Proxys (inkl. LLM-Tool-Aufrufe)                              |
| `chess_game.py`     | Konsolen-App: Spiel-Loop, Eingabe, PGN-Speicherung                   |
| `chess_engine.py`   | Stockfish-Wrapper (UCI, MultiPV-Kandidaten, Bewertungen, Fallback)   |
| `llm_client.py`     | Universeller Client für OpenAI-kompatible APIs (mit Retry)           |
| `chess_prompts.py`  | System-Prompt + Antwortformat des LLM (Konsolen-App)                 |
| `chess.ini`         | Konfiguration: `[chess]` (LLM/Engine), `[proxy]` (Port, API-Key)     |
| `start.sh`          | Startskript der Konsolen-App                                         |
| `engines/`          | Stockfish-Binary (universal, läuft auch ohne AVX2)                   |
| `partien/`, `partien_proxy/` | Gespeicherte Partien im PGN-Format                          |

## Unterstützte LLM-Endpunkte (OpenAI-Format `/v1/chat/completions`)

- **llm-bahnhof-Proxy** (Standard: `http://10.7.0.124:8000`, Modell `llm-bahnhof`)
- **Ollama** (`http://localhost:11434`, Modell z. B. `qwen3:8b`)
- LM Studio, vLLM, llama.cpp-Server, OpenAI, Groq, OpenRouter …

In `chess.ini` nur `LLM_BASE_URL` und `LLM_MODEL` anpassen – die URL wird
automatisch normalisiert (`.../`, `.../v1` → `.../v1/chat/completions`).## Nutzung

### A) Tool-API für OpenWebUI (chess-proxy)

```bash
cd ~/chess
./start_proxy.sh            # startet den Proxy (Port 8300)
./start_proxy.sh stop       # beendet ihn wieder
./start_proxy.sh status     # läuft er gerade?
```

In **OpenWebUI** dann eine neue Verbindung anlegen:

- **URL:** `http://192.168.179.3:8300/v1` (IP des chess-Rechners)
- **API-Key:** der Wert aus `PROXY_API_KEY` (leer = keiner nötig)
- **Modell:** `gambit-schach`

Danach im Chat einfach „Lass uns Schach spielen, ich spiele Weiß" schreiben –
das LLM startet die Partie, nimmt Züge entgegen, antwortet mit eigenem Zug
und Kommentar. Stockfish-Analysen kann es auf Wunsch („Was sind die besten
Züge?") jederzeit dazunehmen.

Die Werkzeuge gibt es zusätzlich als einfache REST-API:

```bash
curl -X POST http://192.168.179.3:8300/chess/api/brett_ansehen \
     -H 'Content-Type: application/json' -d '{"session_id": "test"}'
```

Für **natives Function-Calling in OpenWebUI** (statt Proxys): Datei
`openwebui_tool.py` in OpenWebUI als Tool importieren – sie ruft die
REST-API des Proxys auf.

### B) Konsolen-App

```bash
cd ~/chess


./start.sh                       # Du = Weiß, LLM = Schwarz
./start.sh --engine=black        # Du = Schwarz, LLM beginnt
./start.sh --personality="mürrischer alter Großmeister, der alles kommentiert"
./start.sh --moves=60            # Partie nach 60 Halbzügen beenden
```

Zugeingabe: SAN (`Sf3`, `e4`) **oder** UCI (`g1f3`, `e2e4`). `exit` beendet.
Jede Partie wird unter `partien/partie_YYYYMMDD_HHMMSS.pgn` gespeichert.

## So funktioniert ein LLM-Zug

1. Stockfish berechnet die N besten Kandidatenzüge (`ENGINE_TOP_N`, MultiPV)
   samt Bewertung (z. B. `Sf3 (+0.31)`, `e4 (+0.24)`).
2. Das LLM bekommt Stellung (FEN), bisherige Züge (SAN) und die Kandidaten
   und antwortet im Format:
   ```
   ZUG: e2e4
   KOMMENTAR: Die klassische Eröffnung – das Zentrum gehört mir!
   ```
3. Der Zug wird **validiert**: illegal oder nicht in der Kandidatenliste →
   erneute Anfrage; danach greift der beste Stockfish-Zug als Fallback.
   Das Spiel läuft also garantiert weiter.
4. Unter den Kandidaten wählt Stockfish bei Fallback gewichtet zufällig
   (72 % / 18 % / 10 %), damit Partien unterschiedlich verlaufen.

## Wichtige Einstellungen (`chess.ini`)

| Key                | Wirkung                                              |
|--------------------|------------------------------------------------------|
| `ENGINE_SKILL`     | 0–20, Stärke von Stockfish (default 12)              |
| `ENGINE_MOVETIME_MS`| Denkzeit pro Zug in ms (default 1200)               |
| `ENGINE_TOP_N`     | Anzahl Kandidaten für das LLM (1–5)                  |
| `LLM_NAME`/`LLM_STIL`| Name & Persönlichkeit des KI-Gegners               |

## Hardware-Hinweis

Die mitgelieferte Stockfish-Version ist das offizielle
`x86-64-universal`-Binary und wählt die bestmögliche Variante automatisch –
läuft daher auch auf alter Hardware ohne AVX2/BMI2 (z. B. Intel Atom).
