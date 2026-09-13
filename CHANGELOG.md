# Changelog – chess

Alle nennenswerten Änderungen werden hier dokumentiert.

Format: [Keep a Changelog](https://keepachangelog.com/) ·
Versionierung: [Semantische Versionierung](https://semver.org/) (MAJOR.MINOR.PATCH)

> Hinweis: Die Testsuiten (`tests/` – Proxy-Szenarien, Live-Partie, Service-
> und Journal-Tests) gehören zur Entwicklerkopie und sind **nicht** Teil
> dieses Repositorys. Die Einträge unten dokumentieren sie als Teil der
> Entwicklungshistorie.

## [0.9.9] – 2026-09-13

Erste **Beta** für externe Tester (siehe [`BETA.md`](BETA.md)). Der
serverseitige Zugzwang, der Halluzinationsschutz und die Notations-
behandlung sind durch 16 Proxy-Szenarien sowie echte Live-Partien über den
Proxy validiert – in UCI, englischer SAN und deutscher Notation, inklusive
Rochade.

Versionssprung `0.0.7` → `0.9.9`: Die Validierungsbremse aus 0.0.7 ist
aufgehoben. `0.9.x` heißt „funktioniert praktisch, ist aber noch nicht
1.0“ – die 1.0 folgt nach den Beta-Rückmeldungen.

### Hinzugefügt

- **`chess_shell.py`:** Terminal-Client, der gegen den laufenden Proxy
  spielt – ohne OpenWebUI. Zeichnet das Brett aus der Server-FEN (korrekt
  ausgerichtet für Weiß *und* Schwarz), führt ein eigenes Protokoll des
  Gesprächs und bietet kurze Befehle (`brett`, `verlauf`, `beste`,
  `bewerten`, `neu`, `aufgeben`), die direkt über die REST-API laufen und
  keine LLM-Runde kosten. Optionen: `--farbe`, `--session`, `--url`,
  `--kein-brett`. Fehlen die Abhängigkeiten, startet er sich selbst mit der
  Projekt-venv neu (`./chess_shell.py` genügt also); die ZUSTAND-Blöcke aus
  den Werkzeugantworten (Grounding für das LLM) werden nicht angezeigt, und
  `neu` übernimmt die gewählte Farbe – spielt der Mensch Schwarz, eröffnet
  der Gegner selbst.
- **`stockfish-install.sh`:** eigenes, mehrfach ausführbares Skript für die
  Engine – erkennt die Plattform, lädt die passende Universal-Binary,
  **prüft sie per UCI-Abfrage** und trägt `ENGINE_PATH` in `chess.ini` ein.
  Optionen: `--force` (neu laden), `--tag <release>` (bestimmte Version),
  `--use <pfad>` (vorhandene Engine eintragen, z. B. das
  Distributionspaket `/usr/games/stockfish`). `install.sh` ruft es auf,
  statt die Download-Logik doppelt zu führen.
- `start_proxy.sh`: Unterbefehl `adressen` listet alle API-Adressen des
  Hosts mit Interface und Kennzeichnung (VPN/lokales Netz); die Ausgabe
  richtet sich nach `PROXY_HOST`.
- **`pruefen.sh`**: gemeinsame Prüf- und Selbstheilungslogik für Installer
  und Startskripte – Konfiguration (inkl. `PROXY_PORT`-Plausibilität),
  System-Python (3.9+ mit `venv`-Modul), `.venv`, importierbare Pakete und
  Engine (echter UCI-Test). Ausgabe als Status-Tabelle; über `CHESS_ROOT`,
  `CHESS_INI` und `VENV` für Tests umlenkbar.
- **`install.sh` als idempotenter Installer/Reparaturlauf**: sechs Schritte
  (Voraussetzungen, `.venv`, Pakete mit Import-Prüfung, Laufzeit-Ordner,
  Engine, Abschlussprüfung), Optionen `--check` (nur prüfen), `--no-engine`,
  `--force-engine`, `--engine-tag <tag>`, Exit-Codes 0/1/2 – damit auch für
  Skripte und Monitoring geeignet. Fehlermeldungen nennen die konkrete
  Ursache (z. B. fehlendes `python3-venv`) samt Installationsbefehl.
- `start_proxy.sh check` (entspricht `install.sh --check`).

### Behoben

- **Menschenzug ging verloren (bug-2json):** Werkzeugaufrufe im nativen
  Token-Format ohne JSON-Klammern – `[gegner_zug(zug="b8c6")]` – wurden als
  leere Parameter `{}` geparst. Der Zug wurde nie ausgeführt, der zweite
  identische Aufruf als „Wiederholung“ abgebrochen, und der Nutzer bekam
  „Das Werkzeug lieferte keine Antwort“; Chat und Brett liefen auseinander.
  Schlüsselwort-Parameter (`zug="e2e4"`, `n=3`) werden jetzt geparst.
- Fehlgeschlagene oder leere Werkzeugaufrufe gelten nicht mehr als erledigt
  und blockieren damit ihre eigene Wiederholung. Ein doppelter Aufruf wird
  einmal ermahnt, statt die ganze Anfrage abzubrechen.
- Der Zug des Menschen verlässt die Anfrage nie unausgeführt: Verpasst das
  Modell `gegner_zug`, spielt der Server den Zug aus der letzten
  Nutzernachricht selbst aus und zieht danach seinen Antwortzug.
- **Falsche Server-Korrekturen:** Blanke Feldnennungen („der König deckt
  e7“) gelten nicht mehr als Zugansage, deutsche Notation ohne Schachzeichen
  (`Lxf7` für das gespielte `Bxf7+`) zählt als derselbe Zug, und `0-0` ist
  dieselbe Rochade wie `O-O`.
- Der Proxy verbrennt keine Tool-Loops mehr an Phantom-Fehlalarmen, und ein
  LLM-Ausfall im Tool-Loop wirft eine bereits fertige Antwort nicht mehr als
  502 weg.
- **`proxy.pid` aus einer kopierten Projektmappe konnte fremde Prozesse
  treffen:** Die Datei zeigt dann auf den Proxy der Originalkopie, und
  `stop` bzw. `status` hätten diesen abgeschossen. Der Start prüft jetzt,
  ob die PID wirklich zu *diesem* Ordner gehört (Kommandozeile + cwd) – sonst
  wird die Datei nur entfernt und der Prozess bleibt unangetastet.
- **Dauerhaft kaputter `ENGINE_PATH`:** `stockfish-install.sh` beließ einen
  unbrauchbaren Eintrag in `chess.ini` (Regel „nie überschreiben“), sodass
  jeder Start erneut scheiterte. Ein funktionierender, selbst eingetragener
  Pfad wird weiterhin nicht angetastet – ein unbrauchbarer wird repariert.
- Startskripte prüfen nicht mehr nur „existiert `.venv`“, sondern ob die
  Umgebung benutzbar ist und die Pakete importierbar sind. Damit scheitert
  ein Start nicht mehr erst später mit einem Import- oder Engine-Fehler.

### Geändert

- **Notationen gleichwertig:** `chess_service.german_to_san()` übersetzt
  deutsche Figurenkürzel auf dem Weg ins Spiel (Werkzeug-Parameter,
  REST-API, erzwungener Menschenzug). Englische SAN (`Nf3`, `O-O`) und UCI
  (`g1f3`, `e2e4`) bleiben unverändert – englischsprachige Spieler brauchen
  keine deutsche Notation.
- `_parse_zug()` akzeptiert deutsche Notation gleichwertig zu SAN und UCI
  (bisher nur über die Antwortprüfung des Proxys abgedeckt).
- `start_proxy.sh` gibt bevorzugt die VPN-/WireGuard-Adresse aus (`wg*`),
  weil der Rechner hinter NAT/Doppel-NAT nur dort erreichbar ist, und wartet
  beim Start, bis der Port wirklich lauscht (kein falsches „läuft“ mehr).
- Startskripte reparieren sich selbst: `start_proxy.sh` und `start.sh` rufen
  bei fehlenden Voraussetzungen einmalig `install.sh` auf, statt mit
  Installationshinweisen abzubrechen.
- Vor dem Start wird geprüft, was sonst als Rätsel im Log landen würde:
  `PROXY_PORT` im gültigen Bereich, `PROXY_HOST` gehört zu diesem Rechner,
  Port ist frei. Ein belegter Port wird mit Prozesshinweis gemeldet.
- Der Engine-Pfad wird beim Reparieren **projekt-relativ** eingetragen
  (`engines/stockfish/...`), damit `chess.ini` zwischen Rechnern und Ordnern
  übertragbar bleibt.
- Plattformnamen und UCI-Test der Engine gibt es nur noch an einer Stelle
  (`pruefen.sh`) statt doppelt in Installer und Engine-Skript.

### Tests

- `tests/proxy_test.py`: neue Szenarien 10–16 (Schlüsselwort-Parameter,
  wiederholter Aufruf, Feldnennung, erfundener Zug, Null-Rochade, deutsche
  Notation ohne Schachzeichen, Gleichwertigkeit der Notationen).
- `tests/live_game.py` (neu): End-to-End-Test gegen den laufenden Proxy –
  prüft nach jedem Zug den Serverstand (`Halbzüge +2`, Menschenzug im
  Verlauf, keine Server-Korrektur); `--san` sendet Züge als englische SAN.

## [0.0.7] – 2026-09-12

### Geändert

- Version bewusst auf `0.0.7` zurückgesetzt, bis der erzwungene Ablauf
  Menschenszug → eigener Werkzeugzug vollständig validiert ist.
- Der Schachspieler sieht in den Spielausgaben keine interne
  Berechnungskomponente; das LLM präsentiert den ausgeführten Zug als eigenen
  Zug. Die Admin-Dokumentation beschreibt Stockfish weiterhin offen.
- Nach einem ausgeführten Menschenzug darf der Proxy keine freie Antwort
  akzeptieren, sondern fordert zwingend `zug_machen` an.

## [0.7.4] – 2026-09-11

Fix aus der Bug-Analyse (`bug-chat-export-1789163002893.json`): Das Modell
liefert Werkzeugaufrufe im nativen Token-Format, die der Proxy als Text
anzeigte, ohne sie auszuführen ("Du musst mir nicht den Tool-Call vorlesen
sondern den Zug").

### Hinzugefügt

- **Erkennung nativer Tool-Tokens:** Aufrufe der Form
  `<|tool_call_begin|>[zug_machen({"zug": "e4"})]` bzw.
  `<|tool_call_start|>[brett_ansehen()]<|tool_call_end|>` werden als
  Werkzeugaufrufe gedeutet und sofort serverseitig ausgeführt – statt als
  roher Text beim Nutzer zu landen.
- **Mehrere Aufrufe pro Nachricht:** Eine LLM-Nachricht kann mehrere
  Werkzeuge enthalten (z. B. `gegner_zug` + `zug_machen`); alle werden
  ausgeführt, das Zuglimit (je ein Menschen-/LLM-Zug) bleibt bestehen.
- Tests: `tests/proxy_test.py` um Szenario 7 (native Tool-Tokens) und
  Szenario 8 (geleaktes `brett_ansehen`) erweitert.

### Geändert

- `parse_tool_call` → `parse_tool_calls` (liefert alle Aufrufe einer
  Antwort); Parameter werden zusätzlich tolerant als Python-Literal
  geparst (einfache Anführungszeichen).
- Finaltext entfernt native Tool-Aufrufe, übrige Marker und XML-Reste.

## [0.7.3] – 2026-09-11

Fixes aus der Bug-Analyse (`bug-2.txt`): Schutzmechanismen für schwache
Modelle (z. B. Free-Router-Modelle auf OpenRouter).

### Hinzugefügt

- **Announce-only-Erzwingung:** Behauptet das Modell nur, ein Zug sei
  "gespielt", ohne das Tool aufzurufen, führt der **Server** den in der
  Nutzernachricht genannten Zug selbst aus (`gegner_zug`, einmal pro
  Anfrage) und liefert dem Modell das Ergebnis – die Partie läuft also
  garantiert weiter, egal wie schwach das Modell ist.
- **Normierte Ermahnungen:** Bei Gedankenlecks ("Here's a thinking
  process …"), Phantom-Zügen und behaupteten Ausführungen erhält das
  Modell eine serverseitige SYSTEM-ERINNERUNG (je Kontext einmal pro
  Anfrage) und muss nachbessern.
- **Gedankenleck-Schutz:** Interne Denkprozesse im Antworttext werden
  erkannt und erreichen den Nutzer nie (Nachbesserung im Loop).
- **XML-Werkzeug-Fallback:** Aufrufe im Format `<function=…>` schwacher
  Modelle werden akzeptiert, statt den Tool-Loop zu verbrauchen.
- **Zuglimits pro Anfrage:** Maximal ein `zug_machen` **und** ein
  `gegner_zug` (zuvor: 2 Züge nur für `zug_machen`).
- Neuer Test `tests/proxy_test.py`: sechs Szenarien mit Fake-LLM
  (Announce-only, XML, Gedankenleck, Phantom-Zug, Limit, Normalfall).

### Geändert

- System-Prompt: Ankündigungen ohne Ausführung verboten, keine
  Eröffnungsvorschläge statt eigener Züge, keine Gedanken im Antworttext.
- Proxy-Log meldet die Announce-only-Reparatur sichtbar.

## [0.7.2] – 2026-09-11

Fixes aus der Bug-Analyse (`bug.txt`): Farbverwechslungen und erfundene
Menschen-Züge, beide serverseitig abgestellt.

### Hinzugefügt

- Neues Tool **`gegner_zug`** für Züge des Menschen – wird serverseitig
  abgelehnt, wenn das LLM selbst am Zug ist (`FARBE-FEHLER`).
- **Server-seitiger Farbzwang:** `zug_machen` wird abgelehnt, wenn nicht die
  eigene Farbe des LLM am Zug ist (`NICHT-DEIN-ZUG`). Ziehen für den Menschen
  ist damit technisch unmöglich.
- **Serverseitiger eigener Zug:** `zug_machen` nimmt keinen Zug mehr
  entgegen – der Zug wird ausschließlich regelkonform im Backend ausgeführt.
  Eine freie Zugauswahl oder Halluzination des Modells ist ausgeschlossen.
- `neue_partie` mit neuem Parameter `nutzer_farbe`: Nennt der Nutzer
  explizit seine eigene Farbe, korrigiert der Server eine falsche
  `eigene_farbe`-Zuordnung automatisch.
- `pruef_daten()` meldet jetzt zusätzlich `du_bist`, `eigene_farbe` und
  `letzte_mensch_san` (Grundlage für Proxy-Prüfungen).
- Tests: `service_test.py` (Farbzwang, Auto-Engine, `nutzer_farbe`) und
  `journal_test.py` (Stub-Engine, Journal, Farbzwang) vollständig auf das
  neue Modell umgestellt.

### Geändert

- System-Prompt: Werkzeuge klar nach Farbe getrennt (`gegner_zug` nur für
  Menschen-Züge, `zug_machen` ohne Parameter), Farb-Zuordnungsregeln für
  `neue_partie` verschärft, ZUSTAND-Block ist weiterhin maßgeblich.
- REST-API `/chess/api/*` auf die neuen Signaturen angepasst.

## [0.7.1] – 2026-09-11

### Hinzugefügt

- `install.sh` – Installer: richtet `.venv` ein, installiert
  `requirements.txt` und lädt die offizielle **Stockfish-Universal-Binary**
  passend zur Plattform aus den GitHub-Releases (Linux x86-64/ARM64/ARMv7,
  RISC-V, macOS; Version wählbar via `ENGINE_TAG=sf_19`).
- README: Badge **„Powered with AI“**.

### Geändert

- Die Engine wird **nicht mehr mitgeliefert**, sondern vom Installer
  heruntergeladen; `.gitignore` ignoriert `engines/` komplett.
- `start.sh` / `start_proxy.sh` rufen bei fehlendem `.venv` automatisch den
  Installer auf.
- README-Installationsabschnitt, Komponententabelle und Engine-Kapitel an
  Installer-Workflow angepasst (kein mitgelieferter Quellcode mehr).

## [0.7.0] – 2026-09-11

Erste versionierte Fassung.

### Hinzugefügt

- **Anti-Halluzinations-Paket:**
  - Server-seitiger `ZUSTAND`-Block in **jedem** Tool-Ergebnis
    (Partie-ID, DU BIST/Farbe, Am Zug, Verlauf, FEN, verbindliche Regeln).
  - Partie-Journal je Session unter `partien_journal/` – automatisch
    angelegt, fortlaufend jeder Halbzug (z. B. `1. e4` / `1... e5`).
  - Proxy-Integritätsprüfung: erfundene Züge in der Finalantwort werden mit
    `[Server-Korrektur: …]` sichtbar markiert (SAN + UCI, deutsche Notation
    S/T/L/D wird mitnormalisiert).
  - Strikte Zugregeln im System-Prompt (niemals für den Menschen ziehen,
    nur bei eigenem Zug `zug_machen`, Quelle ist ausschließlich der
    ZUSTAND-Block).
- `requirements.txt` – gepinnt aus der getesteten venv
  (`chess`, `Flask`, `waitress`, `requests`).
- `.gitignore` – venv, Cache, Logs/PID, Stockfish-Binary/Wiki/Build-Artefakte
  (Quellcode bleibt im Repo), Journals.
- README: Abschnitt **Installation** (venv + requirements.txt) und
  **Schach-Engine: Auswahl & Austausch** inkl. Raspberry-Pi-Eigenbau
  (`make ARCH=arm64-universal`).
- `start.sh` / `start_proxy.sh` legen `.venv` und Abhängigkeiten beim ersten
  Start automatisch an (Ein-Befehl-Start auf frischen Klonen/Pi).

### Geändert

- `chess.ini`: kommentierte `ENGINE_PATH`-Alternativen (Pi-Eigenbau,
  `apt install stockfish` → `/usr/games/stockfish`).
- README-Komponententabelle aktualisiert (requirements.txt, Journal,
  Engine-Quellcode).
