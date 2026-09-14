<p align="center">
  <img src="catfish-llm.jpg" alt="catfish-llm Banner" width="100%">
</p>

    Vorwort:
    Der Schachtürke war gestern, catfish-llm bringt einem LLM das Schachspielen bei :-)

Naja, nicht ganz, ich habe da eher die moderne Version eines "Schachtürken" gebaut. Es schließt auf einfache Art die Lücke, dass selbst große LLMs in der Regel bisher kein Schach spielen können. Und wenn sie es jetzt doch wirklich können, haben die Entwickler mit Sicherheit ebenfalls einen "Schachtürken" eingebaut :-) Bei mir greift das LLM intern auf die Stockfish-Engine zurück.

Wer es selbst mal testen mag, kann sich die Software ganz einfach auf seinem Rechner unter Linux installieren. 



# catfish-llm – spielt Schach mit Stockfish im Hintergrund

![Powered with AI](https://img.shields.io/badge/Powered%20with-AI-8A2BE2)
![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue)

**Sprache:** Deutsch · [English](README.md)

**Version 0.9.9 (Beta)** – Änderungshistorie siehe [`CHANGELOG.md`](CHANGELOG.md).
Serverseitiger Zugzwang, Halluzinationsschutz und die Behandlung der
Notationen sind validiert (16 Proxy-Szenarien + echte Live-Partien über den
Proxy). Für die Beta suchen wir Rückmeldungen – Einzelheiten, Testfälle und
Anleitung zum Melden in [`BETA.ger.md`](BETA.ger.md).

Anbindung eines beliebigen LLM (OpenAI-kompatibles Chat-API) an die
Schach-Engine **Stockfish** – auf zwei Wegen:

1. **Tool-API (Empfehlung):** Ein OpenAI-kompatibler Proxy mit eingebauten
   Schach-Werkzeugen. Der Nutzer chattet in **OpenWebUI** (oder jedem
   OpenAI-Client) mit dem LLM, und das LLM bedient die Schach-App als
   Werkzeug (neue Partie, Züge, Stockfish-Analyse, Brettansicht …).
2. **Konsolen-App:** Klassisches Terminal-Spiel Mensch gegen LLM.

```
OpenWebUI ──► catfish-llm (Port 8300) ──► llm-bahnhof/Ollama/… (LLM)
                  │
                  └─► Stockfish + Spielverwaltung (chess_service)
```

> **Projektname:** `catfish-llm`. Die Datei- und Befehlsnamen
> (`chess_proxy.py`, `chess.ini`, `./start_proxy.sh` …) tragen aus
> Kompatibilität weiterhin das historische Präfix `chess` – sie sind Teil
> der stabilen Schnittstelle und bleiben unverändert.

## Komponenten

| Datei               | Aufgabe                                                              |
|---------------------|----------------------------------------------------------------------|
| `chess_proxy.py`    | OpenAI-kompatibler Proxy mit Tool-Loop + REST-API (Port 8300)        |
| `chess_service.py`  | Spielverwaltung + Schach-Werkzeuge (thread-sicher, PGN-Archiv)       |
| `openwebui_tool.py` | Optionale OpenWebUI-Tool-Klasse (natives Function-Calling)           |
| `start_proxy.sh`    | Start/Stop des Proxys (`start|stop|restart|status|adressen|check`)   |
| `install.sh`        | Installer/Reparatur: prüft alles und richtet Fehlendes ein           |
| `pruefen.sh`        | Gemeinsame Prüf- und Selbstheilungslogik (Installer + Startskripte)  |
| `stockfish-install.sh` | Installiert/wechselt nur die Engine (Download oder eigener Pfad)  |
| `requirements.txt`  | Python-Abhängigkeiten (`pip install -r requirements.txt`)            |
| `chess_game.py`     | Konsolen-App: Spiel-Loop, Eingabe, PGN-Speicherung                   |
| `chess_engine.py`   | Stockfish-Wrapper (UCI, MultiPV-Kandidaten, Bewertungen, Fallback)   |
| `llm_client.py`     | Universeller Client für OpenAI-kompatible APIs (mit Retry)           |
| `chess_prompts.py`  | System-Prompt + Antwortformat des LLM (Konsolen-App)                 |
| `chess.ini`         | Konfiguration: `[chess]` (LLM/Engine), `[proxy]` (Port, API-Key)     |
| `start.sh`          | Startskript der Konsolen-App (ruft bei Bedarf `install.sh` auf)      |
| `chess_shell.py`    | Terminal-Client: spielt gegen den Proxy, zeichnet das Brett lokal    |
| `engines/stockfish/`| Stockfish-Binary – wird vom Installer geladen (nicht im Repo)        |
| `partien/`, `partien_proxy/` | Gespeicherte Partien im PGN-Format                          |
| `partien_journal/` | Zug-Journal je Partie (Grounding fürs LLM, wird automatisch geführt) |

## Unterstützte LLM-Endpunkte (OpenAI-Format `/v1/chat/completions`)

- **llm-bahnhof-Proxy** (Standard: `http://10.7.0.124:8000`, Modell `llm-bahnhof`)
- **Ollama** (`http://localhost:11434`, Modell z. B. `qwen3:8b`)
- LM Studio, vLLM, llama.cpp-Server, OpenAI, Groq, OpenRouter …

In `chess.ini` nur `LLM_BASE_URL` und `LLM_MODEL` anpassen – die URL wird
automatisch normalisiert (`.../`, `.../v1` → `.../v1/chat/completions`).

## Installation

```bash
cd ~/catfish-llm
./install.sh
```

Der Installer ist **idempotent**: Er prüft jede Voraussetzung und richtet nur
das ein, was wirklich fehlt. In sechs Schritten:

1. Systemvoraussetzungen (Python 3.9+ mit `venv`-Modul, `chess.ini`,
   Schreibrechte)
2. Virtuelles Environment `.venv`
3. Python-Pakete aus `requirements.txt` (danach per Import geprüft)
4. Laufzeit-Ordner (`partien`, `partien_journal`, `partien_proxy`)
5. Stockfish über `stockfish-install.sh` – passende **Universal-Binary** aus
   dem offiziellen GitHub-Release (x86-64, ARM64/Raspberry Pi, ARMv7,
   RISC-V oder macOS). Die Engine ist **nicht** Teil des Repos.
6. Abschlussprüfung mit Status-Tabelle

| Aufruf | Wirkung |
|---|---|
| `./install.sh` | alles einrichten / Fehlendes reparieren |
| `./install.sh --check` | nur prüfen, ändert nichts (Exit 1 = es fehlt etwas) |
| `./install.sh --no-engine` | ohne Engine-Installation |
| `./install.sh --force-engine` | Engine neu laden |
| `./install.sh --engine-tag sf_17` | bestimmte Stockfish-Version |

Exit-Codes: `0` alles bereit · `1` Fehler bzw. etwas fehlt · `2` falscher
Aufruf. Damit eignet sich `./install.sh --check` auch für Skripte und
Monitoring.

**Startskripte reparieren sich selbst:** `./start_proxy.sh` und `./start.sh`
prüfen vor jedem Start dieselben Voraussetzungen und rufen bei Bedarf
automatisch `./install.sh` auf (einmalig – danach startet der Aufruf sofort).
Sie prüfen zusätzlich: plausibler `PROXY_PORT`, `PROXY_HOST` gehört zu diesem
Rechner, Port ist frei. Eine verwaiste `proxy.pid` wird entfernt; zeigt sie
auf einen Proxy aus einem **anderen** Ordner (z. B. eine Kopie des Projekts),
bleibt dieser Prozess unangetastet.

```bash
./start_proxy.sh check        # Voraussetzungen prüfen (identisch zu install.sh --check)
```

Konfiguriert wird in `chess.ini`; gestartet wird mit `./start_proxy.sh`
(Proxy) oder `./start.sh` (Konsolen-App).

### Nur die Engine installieren (`stockfish-install.sh`)

Für die Engine gibt es ein eigenes, mehrfach ausführbares Skript – nützlich
beim Umzug, beim Wechsel der Version oder wenn nur der Engine-Teil fehlt:

```bash
./stockfish-install.sh                      # passende Engine laden
./stockfish-install.sh --force              # neu laden (z. B. nach Update)
./stockfish-install.sh --tag sf_17          # bestimmte Release-Version
./stockfish-install.sh --use /usr/games/stockfish   # vorhandene eintragen
./stockfish-install.sh --help
```

Das Skript erkennt die Plattform, lädt die passende Binary nach
`engines/stockfish/`, **prüft sie per UCI-Abfrage** (gibt die erkannte
Version aus) und trägt `ENGINE_PATH` in `chess.ini` ein. Ist die Engine
schon vorhanden, passiert nichts (außer Prüfen und Anzeigen). Ein
**funktionierender** `ENGINE_PATH` wird **nicht** überschrieben – mit
`--use <pfad>` schon, denn das ist eine bewusste Ansage. Zeigt der Eintrag
dagegen auf eine fehlende oder nicht startende Datei, wird er repariert
(der alte Wert wird dabei ausgegeben). Für ein Distributionspaket
(`sudo apt install stockfish`) genügt also:
`./stockfish-install.sh --use /usr/games/stockfish`.

## Nutzung

### A) Tool-API für OpenWebUI (catfish-llm)

```bash
cd ~/catfish-llm
./start_proxy.sh            # startet den Proxy (Port 8300)
./start_proxy.sh stop       # beendet ihn wieder
./start_proxy.sh status     # läuft er gerade?
```

In **OpenWebUI** dann eine neue Verbindung anlegen:

- **URL:** `http://<Adresse-des-catfish-llm-Rechners>:8300/v1`

  Ein Rechner kann in mehreren Netzen stehen (LAN, VPN/WireGuard, Docker-
  Bridge …). `./start_proxy.sh` listet beim Start **alle** Adressen des
  Hosts mit Interface und passender API-URL auf; dieselbe Liste gibt es
  jederzeit mit `./start_proxy.sh adressen`:

  ```
  API-Adressen dieses Rechners (die passende im Client eintragen):
    http://127.0.0.1:8300/v1/chat/completions      (lo, nur dieser Rechner)
    http://192.168.1.20:8300/v1/chat/completions   (eth0, lokales Netz)
    http://10.7.0.116:8300/v1/chat/completions     (wg0, VPN – auch von außen erreichbar)
  ```

  Welche passt, hängt davon ab, wo der Client steht. Steht der Rechner
  hinter NAT (oder Doppel-NAT), ist von außen **nur** die VPN-/WireGuard-
  Adresse nutzbar – nicht die LAN-Adresse aus `hostname -I`. Der Port wird
  aus `chess.ini` gelesen (`PROXY_PORT`).

  Der Proxy lauscht auf `0.0.0.0`, ist also auf allen Interfaces
  erreichbar – eine Firewall (z. B. `ufw`) muss den Port auf dem genutzten
  Interface freigeben: `sudo ufw allow in on wg0 to any port 8300 proto tcp`.
- **API-Key:** der Wert aus `PROXY_API_KEY` (leer = keiner nötig)
- **Modell:** `catfish-llm`

Danach im Chat einfach „Lass uns Schach spielen, ich spiele Weiß" schreiben –
das LLM startet die Partie, nimmt Züge entgegen, antwortet mit eigenem Zug
und Kommentar. Stockfish-Analysen kann es auf Wunsch („Was sind die besten
Züge?") jederzeit dazunehmen.

Die Werkzeuge gibt es zusätzlich als einfache REST-API:

```bash
curl -X POST http://10.7.0.116:8300/chess/api/brett_ansehen \
     -H 'Content-Type: application/json' -d '{"session_id": "test"}'
```

### Verlässliche Partiedaten (Anti-Halluzination)

Jedes Werkzeug-Ergebnis endet mit einem `ZUSTAND`-Block, den der Server
berechnet: Partie-ID, DU BIST (Farbe), wer am Zug ist, vollständiger
Zugverlauf (SAN + FEN) und der Pfad zur Journal-Datei unter
`partien_journal/` – dort steht jeder Halbzug fortlaufend (z. B.
`1. e4` / `1... e5`) und kann dem LLM bei Bedarf direkt vorgehalten werden.
Zusätzlich prüft der Proxy die finale Antwort: Finden sich darin Züge, die
weder gespielt noch aktuell legal sind, hängt er eine
`[Server-Korrektur: …]`-Notiz an. Halluzinierte Züge landen so nie
ungekennzeichnet beim Nutzer. Geprüft werden nur eindeutige Zugangaben
(Figur- oder UCI-Form); blanke Feldnennungen („der König deckt e7“) und die
Rochade in Null-Schreibweise (`0-0`) lösen bewusst keine Korrektur aus.

**Server-seitiger Farbzwang (seit 0.7.2):** Züge des Menschen laufen über das
eigene Werkzeug `gegner_zug` und werden abgelehnt, wenn das LLM selbst am
Zug ist (`FARBE-FEHLER`); umgekehrt lehnt `zug_machen` ab, wenn das LLM
nicht am Zug ist (`NICHT-DEIN-ZUG`). Ziehen für die falsche Farbe ist damit
technisch unmöglich. Und: Das LLM **wählt keine Züge mehr aus** – ruft es
`zug_machen` auf; das Werkzeug führt den eigenen regelkonformen Zug mit
Stockfish aus. Eine falsche `eigene_farbe` bei `neue_partie` korrigiert der
Server, wenn der Nutzer seine Farbe explizit nennt (`nutzer_farbe`).

**Schutz gegen schwache Modelle (seit 0.7.3):** Behauptet das Modell nur
"wurde gespielt", ohne das Tool aufzurufen (Announce-only), führt der
Server den in der Nutzernachricht genannten Zug selbst aus. Gedankenlecks
im Antworttext, Phantom-Züge und XML-artige Tool-Aufrufe
(`<function=…>`) werden erkannt: Ermahnung + Nachbesserung im Loop bzw.
großzügige Annahme. Der Nutzer sieht davon nur noch korrekte Antworten –
auch Free-Router-Modelle spielen damit zuverlässig mit.

**Native Tool-Tokens (seit 0.7.4):** Liefert ein Fine-Tune Werkzeugaufrufe
im eigenen Token-Format (`<|tool_call_begin|>[zug_machen({"zug": "e4"})]`,
`<|tool_call_start|>[brett_ansehen()]<|tool_call_end|>`), führt der Proxy sie
aus, statt sie als Text anzuzeigen. Mehrere Aufrufe in einer Nachricht sind
möglich (z. B. `gegner_zug` + `zug_machen`). Auch die klammerlose
Parameterform `[gegner_zug(zug="b8c6")]` wird ausgewertet.

**Notationen gleichwertig:** Zugeingaben werden in allen drei üblichen
Schreibweisen akzeptiert – englische SAN (`Nf3`, `exd5`, `O-O`), deutsche
Notation (`Sf3`, `Lxf7`, `0-0`, `e8=D`) und UCI (`g1f3`, `e2e4`, `e7e8q`).
Die Übersetzung geschieht in `chess_service.german_to_san()` und damit auf
dem Weg **ins Spiel** (Werkzeug-Parameter, REST-API, erzwungener
Menschenzug) – nicht nur in der Antwortprüfung. Englischsprachige Spieler
brauchen daher keine deutschen Figurenkürzel, und der Gegner darf trotzdem
in deutscher Notation antworten, ohne dass eine Server-Korrektur ausgelöst
wird.

Für **natives Function-Calling in OpenWebUI** (statt Proxys): Datei
`openwebui_tool.py` in OpenWebUI als Tool importieren – sie ruft die
REST-API des Proxys auf.

### B) Konsolen-App

```bash
cd ~/catfish-llm


./start.sh                       # Du = Weiß, LLM = Schwarz
./start.sh --engine=black        # Du = Schwarz, LLM beginnt
./start.sh --personality="mürrischer alter Großmeister, der alles kommentiert"
./start.sh --moves=60            # Partie nach 60 Halbzügen beenden
```

Zugeingabe: englische SAN (`Nf3`, `e4`), deutsche Notation (`Sf3`) oder UCI
(`g1f3`, `e2e4`). `exit` beendet.
Jede Partie wird unter `partien/partie_YYYYMMDD_HHMMSS.pgn` gespeichert.

### C) Shell-Client gegen den Proxy (chess_shell.py)

Wer nicht extra OpenWebUI starten will, aber trotzdem gegen **denselben
Proxy** spielen möchte (gleiche Partien, gleiches Grounding), nimmt den
Terminal-Client. Er spricht die OpenAI-kompatible API des Proxys und
zeichnet das Brett aus der FEN, die der Server liefert – inklusive
richtiger Ausrichtung für Schwarz.

```bash
./chess_shell.py                       # Konfiguration aus chess.ini
./chess_shell.py --farbe schwarz       # du spielst Schwarz
./chess_shell.py --session abend-1     # feste Partie-ID (später weiterspielen)
./chess_shell.py --url http://10.7.0.116:8300
./chess_shell.py --kein-brett          # Brett nicht automatisch zeigen
```

Beispiel:

```
  +---+---+---+---+---+---+---+---+
8 | ♜ | ♞ | ♝ | ♚ | ♛ | ♝ | ♞ | ♜ |
  ...
7 | ♟ | ♟ | ♟ | ♟ | ♟ | ♟ | ♟ | ♟ |
  ...
1 | ♖ | ♘ | ♗ | ♔ | ♕ | ♗ | ♘ | ♖ |
  +---+---+---+---+---+---+---+---+
    h   g   f   e   d   c   b   a
  Verlauf: 1. e4
  Am Zug: Schwarz  ← du   (Partie: shell-20260913-194522)

  Du> e7e5
```

Züge dürfen in jeder Notation eingegeben werden (UCI `e2e4`, englische SAN
`Nf3`/`O-O`, deutsch `Sf3`/`0-0`) – alles Weitere wird als Nachricht an den
Gegner geschickt. Kurze Befehle laufen **ohne LLM-Runde** direkt über die
REST-API und antworten sofort:

| Befehl            | Wirkung                                  |
|-------------------|------------------------------------------|
| `brett`, `b`      | Brett anzeigen                           |
| `verlauf`, `v`    | Zugverlauf + Zustand                     |
| `beste [n]`       | die n besten Züge (Stockfish)            |
| `bewerten`        | Stellung bewerten                        |
| `neu`             | neue Partie starten                      |
| `aufgeben`        | Partie aufgeben                          |
| `hilfe`, `exit`   | Hilfe bzw. beenden                       |

Der Proxy muss laufen (`./start_proxy.sh`); die Adresse kommt aus
`chess.ini` (`PROXY_HOST`/`PROXY_PORT`) oder per `--url`.

Der Client braucht `python-chess` und `requests`. Fehlen sie (Aufruf mit dem
System-Python), startet er sich **automatisch mit der Projekt-venv neu** – ein
vorheriges `source .venv/bin/activate` ist also nicht nötig. Ist gar keine venv
da, sagt er klar, dass `./install.sh` fehlt.

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
| `PROXY_HOST`       | Adresse, auf der der Proxy lauscht: `0.0.0.0` = alle Schnittstellen, `127.0.0.1` = nur dieser Rechner, oder eine bestimmte IP (z. B. die VPN-Adresse) |
| `PROXY_PORT`       | Port des Proxys (default 8300)                       |
| `PROXY_API_KEY`    | Bearer-Token, den Clients senden müssen (leer = keiner) |
| `PROXY_MODEL_NAME` | Modellname, den der Proxy Clients meldet (default `catfish-llm`) |
| `ENGINE_SKILL`     | 0–20, Stärke von Stockfish (default 12)              |
| `ENGINE_MOVETIME_MS`| Denkzeit pro Zug in ms (default 1200)               |
| `ENGINE_TOP_N`     | Anzahl Kandidaten für das LLM (1–5)                  |
| `LLM_NAME`/`LLM_STIL`| Name & Persönlichkeit des KI-Gegners               |

`PROXY_HOST=127.0.0.1` ist die sichere Variante, wenn der Proxy nicht direkt
erreichbar sein soll – z. B. wenn der Zugriff nur über einen eigenen
Reverse-Proxy läuft. Mit einer konkreten IP lauscht er ausschließlich auf
dieser Schnittstelle (praktisch: nur die VPN-Adresse freigeben). Welche
Adressen passen, zeigt `./start_proxy.sh adressen`; das Startskript richtet
seine Ausgabe nach `PROXY_HOST`.

## Schach-Engine: Auswahl & Austausch

Die Engine ist einfach konfiguriert: In `chess.ini` steht unter
`ENGINE_PATH` der Pfad zur UCI-Engine – dieser lässt sich jederzeit
austauschen (auch gegen eine ganz andere UCI-Engine).

| Umgebung                  | Empfehlung                                              |
|---------------------------|---------------------------------------------------------|
| x86-64-PC (Linux)         | `stockfish-linux-x86-64-universal` (Installer-Standard) |
| Raspberry Pi (64-bit OS)  | `stockfish-linux-arm64-universal` (lädt der Installer automatisch) |
| Raspberry Pi (32-bit OS)  | `stockfish-android-armv7-neon` (vom Installer erkannt)  |
| macOS / RISC-V            | ebenfalls über den Installer abgedeckt                  |
| Debian/Ubuntu, egal wo    | `sudo apt install stockfish` → `ENGINE_PATH=/usr/games/stockfish` |
| Andere CPU / andere Engine| Selbst bauen (siehe unten) oder UCI-Pfad tauschen       |

Alle Universal-Binaries erkennen die CPU-Fähigkeiten zur Laufzeit und
nutzen automatisch das Beste davon (AVX2, NEON, dotprod, …) – man wählt
keine Architektur mehr von Hand. Andere Engine-Version:

```bash
./stockfish-install.sh --tag sf_17     # beliebiger Release-Tag
./stockfish-install.sh --force         # gleiche Version neu laden
```

Die Engine prüfen und den erkannten Pfad/Version ausgeben – genau das macht
das Skript am Ende selbst:

```bash
printf 'uci\nquit\n' | ./engines/stockfish/stockfish-linux-x86-64-universal | head -3
```

### Stockfish selbst bauen (optional)

Normalerweise unnötig – der Installer deckt alle gängigen Plattformen ab.
Wer trotzdem bauen will, holt den Quellcode selbst und trägt danach den
Pfad in `chess.ini` ein:

```bash
git clone --depth 1 https://github.com/official-stockfish/Stockfish.git
cd Stockfish/src
make -j$(nproc) profile-build      # ohne ARCH = optimal für genau diese CPU
```

```ini
ENGINE_PATH=/pfad/zu/Stockfish/src/stockfish
```

## Hardware-Hinweis

Der Installer lädt je Plattform das offizielle Universal-Binary – es wählt
die bestmögliche Variante automatisch und läuft daher sowohl auf alter
Hardware ohne AVX2/BMI2 (z. B. Intel Atom) als auch auf Raspberry Pi und
anderen ARM-Boards.

## Lizenz

Dieses Projekt steht unter der **GNU General Public License, Version 3 oder
später (GPL-3.0-or-later)** – der vollständige Lizenztext liegt in
[`LICENSE`](LICENSE).

```text
Copyright (C) 2026 Olav (https://github.com/o-valo)

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <https://www.gnu.org/licenses/>.
```

**Warum GPL und nicht MIT?** Nicht aus Vorliebe, sondern weil die
Abhängigkeiten es vorgeben: Der Proxy importiert **`python-chess`**
(Brettlogik, PGN, UCI-Anbindung) – und das steht unter **GPL-3.0-or-later**.
Ein Programm, das GPL-Code enthält, darf nur unter der GPL weitergegeben
werden. Eine permissivere Lizenz wie MIT wäre hier **unzulässig** gewesen:
MIT und GPL sind nur in eine Richtung kombinierbar (MIT-Code darf in ein
GPL-Projekt, nicht umgekehrt).

**Was das praktisch bedeutet**

- Wer den Proxy weitergibt oder ein Paket daraus baut, muss den Quellcode
  mitliefern bzw. darauf verweisen – das ist der Kern der GPL. Hier ist das
  ohnehin erfüllt: Das Projekt liegt offen auf GitHub.
- Die übrigen Abhängigkeiten sind GPL-kompatibel: `Flask` (BSD-3-Clause),
  `waitress` (ZPL 2.1), `requests` (Apache-2.0).
- **Stockfish** (ebenfalls GPL-3.0) lädt der Installer nur als
  **eigenständiges Programm** – es ist kein Bestandteil dieses Repositorys,
  sondern ein separates Werkzeug (siehe
  [`stockfish-install.sh`](stockfish-install.sh)), das Lizenz und Quellcode
  selbst mitbringt. Wer Stockfish selbst weitergibt, muss dessen
  GPL-Bedingungen beachten.
- Für **Offenlegungspflichten** genügt ein Verweis auf dieses Repository;
  die Lizenz erlaubt ausdrücklich, das Programm zu ändern und
  weiterzuverbreiten – nur eben nicht als geschlossenes Produkt.

## Powered by AI

Dieses Projekt ist **mit KI-Unterstützung entstanden** – Code, Doku und
Tests wurden im Dialog mit einem KI-Coding-Agenten erarbeitet. Die Idee, die
Architektur, die fachlichen Entscheidungen und die Abnahme am Ende stammen
vom Menschen; die Umsetzung entstand gemeinschaftlich.

Das ist kein Zufall, sondern passt zum Gegenstand: Das Programm lässt ein
LLM Schach spielen und zieht dabei genau die Grenze, die solche Systeme
brauchen – das Modell formuliert, der Server prüft. Züge werden validiert,
halluzinierte Züge korrigiert, Werkzeugaufrufe erzwungen. Derselbe Ansatz
hat auch beim Bauen geholfen: viel schreiben lassen, alles nachprüfen.
