# Beta-Test – chess 0.9.9

Danke, dass du mittestest! Du spielst dabei Schach gegen ein LLM, das die
Partie über lokale Werkzeuge führt; die Züge kommen aus **Stockfish**, die
Kommentare vom Modell. Alles läuft auf dem chess-Rechner – dein Chat geht
nur an den dort konfigurierten LLM-Endpunkt.

**Was 0.9.9 ist:** „funktioniert praktisch, ist aber noch nicht 1.0“. Genau
dafür suchen wir Fälle, in denen es das noch nicht tut.

---

## 1. Zugang

Frag den Betreiber nach der Adresse des chess-Rechners. Zwei Fälle:

- **Im selben Netz:** `http://<LAN-IP>:8300/v1`
- **Von außen / hinter NAT:** die WireGuard-Adresse, z. B.
  `http://10.7.0.116:8300/v1`

Erreichbarkeit prüfen:

```bash
curl http://<adresse>:8300/health
# -> {"service":"chess-proxy","status":"ok","version":"0.9.9"}
```

Kommt nichts zurück und der Rechner läuft, fehlt meistens die Firewall-
Freigabe: `sudo ufw allow in on wg0 to any port 8300 proto tcp`.

Auf dem chess-Rechner prüft `./start_proxy.sh check` die komplette
Installation (Konfiguration, Python-Umgebung, Pakete, Engine) und zeigt, was
fehlt. `./start_proxy.sh` und `./start.sh` richten Fehlendes vor dem Start
automatisch ein.

### In OpenWebUI einrichten

| Feld      | Wert |
|-----------|------|
| URL       | `http://<adresse>:8300/v1` |
| API-Key   | leer (bzw. der Wert aus `PROXY_API_KEY`) |
| Modell    | `gambit-schach` |

Jeder OpenAI-kompatible Client geht genauso.

**Ohne OpenWebUI** – direkt im Terminal (auf dem chess-Rechner):

```bash
./chess_shell.py --url http://<adresse>:8300 --farbe schwarz
```

Der Shell-Client zeigt nach jedem Zug das Brett, versteht Züge in allen drei
Notationen und hat Kurzbefehle wie `brett`, `beste 3` oder `verlauf`
(`hilfe` listet sie auf).

---

## 2. Spielen

Einfach schreiben: „Lass uns Schach spielen, ich spiele Weiß.“ Danach nennst
du deine Züge. **Jede der drei Schreibweisen funktioniert:**

| Notation | Beispiele |
|---|---|
| UCI | `e2e4`, `g1f3`, `e1g1`, `e7e8q` |
| Englische SAN | `e4`, `Nf3`, `exd5`, `O-O`, `e8=Q` |
| Deutsche Notation | `e4`, `Sf3`, `Lxf7`, `0-0`, `e8=D` |

Der Gegner antwortet in der Sprache, die du verwendest.

---

## 3. Was wir getestet haben wollen

Bitte einmal durchspielen und **notieren, was passiert**:

1. **Synchronität:** Nenne nach jedem Zug `Was steht auf dem Brett?`. Der
   vom Modell genannte Zug muss zum Verlauf passen.
2. **Wiederholung:** Nenne denselben Zug zweimal hintereinander, oder
   schreib „versuch es nochmal“. Früher ging hier der Zug verloren.
3. **Notationen mischen:** Deine Züge in UCI, die Antwort in deutscher
   Notation (oder umgekehrt). Es darf **keine**
   `[Server-Korrektur: …]` auftauchen.
4. **Rochade:** kurz (`e1g1` / `O-O` / `0-0`) und lang. Genauso Umwandlung.
5. **Druck aufs Modell:** widerspreche ihm, frag „bist du sicher?“, lass dir
   die besten Züge zeigen („Was sind hier die besten Züge?“).
6. **Nicht-Schach:** frag zwischendurch etwas anderes. Es soll normal
   antworten, ohne Werkzeug-Gerümpel im Text.
7. **Neue Partie:** starte mitten im Spiel eine neue – Farbe wechseln.

### Diese Meldungen sind Fehler (bitte melden)

- „Das Werkzeug lieferte keine Antwort. Bitte versuch es erneut.“
- `[Server-Korrektur: …]` obwohl die genannten Züge tatsächlich gespielt sind
- Der Chat nennt einen Zug, der nicht im Verlauf steht (oder umgekehrt)
- Rohes Werkzeug-Markup im Text: `TOOL: …`, PARAMS, `<|tool_call…|>`
- Der Gegner zieht für dich, oder du zweimal hintereinander
- Antwort in einer anderen Sprache als deiner

---

## 4. Fehler melden

Am hilfreichsten ist ein Paket aus drei Dingen:

1. **Version:** `curl http://<adresse>:8300/health`
2. **Zugverlauf der Partie:** `partien_journal/partie_<id>.md`
   (die `<id>` steht im ZUSTAND-Block bzw. auf `ls partien_journal/`)
3. **Log der Anfrage:** die letzten ~30 Zeilen aus `~/chess/proxy.log`

Dazu kurz: *was du geschrieben hast*, *was du erwartet hast*, *was passiert
ist*. Wenn möglich den Chatverlauf als Screenshot oder Text.

---

## 5. Bekannte Grenzen (kein Fehler)

- **Rate-Limits:** Der Standard-LLM-Endpunkt ist ein Free-Tier-Router. Bei
  HTTP 502 / „upstream_error“ bitte die Nachricht einfach nochmal senden.
  Die Partie selbst bleibt intakt.
- **Partien liegen im Speicher.** Ein Neustart des Proxys beendet laufende
  Partien; abgeschlossene landen als PGN unter `partien_proxy/`.
- **Der Chat ist das Gedächtnis.** Wird der Verlauf im Client gelöscht,
  beginnt ein neues Spiel.
- **Die Stärke** hängt an `ENGINE_SKILL` in `chess.ini` (0–20, Standard 12).

---

Copyright (C) 2026 Olav (https://github.com/o-valo) ·
SPDX-License-Identifier: GPL-3.0-or-later ·
Freie Software unter der GNU GPL v3 oder später, ohne Gewährleistung –
siehe [`LICENSE`](LICENSE).
