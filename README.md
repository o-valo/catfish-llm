<p align="center">
  <img src="catfish-llm.png" alt="catfish-llm Banner" width="100%">
</p>

# catfish-llcatfishchess with Stockfish in the background

![Powered with AI](https://img.shields.io/badge/Powered%20with-AI-8A2BE2)
![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue)

**Language:** [Deutsch](README.ger.md) · English

**Version 0.9.9 (Beta)** – see [`CHANGELOG.md`](CHANGELOG.md) for the change
history. Server-side turn enforcement, hallucination protection and the
handling of chess notations are validated (16 proxy scenarios + real live
games through the proxy). We are looking for feedback for the beta – details,
test cases and how to report issues are in [`BETA.md`](BETA.md) (German
version: [`BETA.ger.md`](BETA.ger.md)).

Connecting any LLM (OpenAI-compatible chat API) to the chess engine
**Stockfish** – in two ways:

1. **Tool API (recommended):** an OpenAI-compatible proxy with built-in chess
   tools. The user chats in **OpenWebUI** (or any OpenAI client) with the LLM,
   and the LLM operates the chess app as a tool (new game, moves, Stockfish
   analysis, board view, …).
2. **Console app:** classic terminal game, human versus LLM.

```
OpenWebUI ──► catfish-llm (port 8300) ──► llm-bahnhof/Ollama/… (LLM)
                  │
                  └─► Stockfish + game management (chess_service)
```

> **Project name:** `catfish-llm`. The file and command names
> (`chess_proxy.py`, `chess.ini`, `./start_proxy.sh`, …) keep their
> historical `chess` prefix for compatibility – they are part of the stable
> interface and stay unchanged.

## Components

| File                | Purpose                                                             |
|---------------------|---------------------------------------------------------------------|
| `chess_proxy.py`    | OpenAI-compatible proxy with tool loop + REST API (port 8300)        |
| `chess_service.py`  | Game management + chess tools (thread-safe, PGN archive)             |
| `openwebui_tool.py` | Optional OpenWebUI tool class (native function calling)              |
| `start_proxy.sh`    | Start/stop the proxy (`start|stop|restart|status|adressen|check`)   |
| `install.sh`        | Installer/repair: checks everything and sets up what is missing      |
| `pruefen.sh`        | Shared check and self-repair logic (installer + start scripts)       |
| `stockfish-install.sh` | Installs/switches only the engine (download or your own path)     |
| `requirements.txt`  | Python dependencies (`pip install -r requirements.txt`)             |
| `chess_game.py`     | Console app: game loop, input, PGN storage                           |
| `chess_engine.py`   | Stockfish wrapper (UCI, MultiPV candidates, evaluations, fallback)   |
| `llm_client.py`     | Universal client for OpenAI-compatible APIs (with retry)             |
| `chess_prompts.py`  | System prompt + response format of the LLM (console app)             |
| `chess.ini`         | Configuration: `[chess]` (LLM/engine), `[proxy]` (port, API key)     |
| `start.sh`          | Start script for the console app (calls `install.sh` if needed)      |
| `chess_shell.py`    | Terminal client: plays against the proxy, renders the board locally  |
| `engines/stockfish/`| Stockfish binary – downloaded by the installer (not in the repo)     |
| `partien/`, `partien_proxy/` | Saved games in PGN format                                  |
| `partien_journal/`  | Move journal per game (grounding for the LLM, kept automatically)    |

## Supported LLM endpoints (OpenAI format `/v1/chat/completions`)

- **llm-bahnhof proxy** (default: `http://10.7.0.124:8000`, model `llm-bahnhof`)
- **Ollama** (`http://localhost:11434`, model e.g. `qwen3:8b`)
- LM Studio, vLLM, llama.cpp server, OpenAI, Groq, OpenRouter …

In `chess.ini` you only adjust `LLM_BASE_URL` and `LLM_MODEL` – the URL is
normalized automatically (`.../`, `.../v1` → `.../v1/chat/completions`).

## Installation

```bash
cd ~/catfish-llm
./install.sh
```

The installer is **idempotent**: it checks every requirement and only sets up
what is actually missing. In six steps:

1. System requirements (Python 3.9+ with the `venv` module, `chess.ini`,
   write permissions)
2. Virtual environment `.venv`
3. Python packages from `requirements.txt` (verified by import afterwards)
4. Runtime folders (`partien`, `partien_journal`, `partien_proxy`)
5. Stockfish via `stockfish-install.sh` – the matching **universal binary**
   from the official GitHub release (x86-64, ARM64/Raspberry Pi, ARMv7,
   RISC-V or macOS). The engine is **not** part of the repo.
6. Final check with a status table

| Invocation | Effect |
|---|---|
| `./install.sh` | set up everything / repair what is missing |
| `./install.sh --check` | check only, changes nothing (exit 1 = something is missing) |
| `./install.sh --no-engine` | without engine installation |
| `./install.sh --force-engine` | re-download the engine |
| `./install.sh --engine-tag sf_17` | a specific Stockfish version |

Exit codes: `0` everything ready · `1` error or something missing · `2` wrong
invocation. That makes `./install.sh --check` suitable for scripts and
monitoring as well.

**The start scripts repair themselves:** `./start_proxy.sh` and `./start.sh`
check the same requirements before every start and call `./install.sh`
automatically if needed (once – after that the call starts immediately). They
additionally check: plausible `PROXY_PORT`, `PROXY_HOST` belongs to this
machine, port is free. An orphaned `proxy.pid` is removed; if it points to a
proxy from a **different** folder (e.g. a copy of the project), that process is
left untouched.

```bash
./start_proxy.sh check        # check requirements (identical to install.sh --check)
```

Configuration happens in `chess.ini`; start with `./start_proxy.sh` (proxy) or
`./start.sh` (console app).

### Installing only the engine (`stockfish-install.sh`)

There is a separate, re-runnable script for the engine – useful when moving to
another machine, when switching versions, or when only the engine part is
missing:

```bash
./stockfish-install.sh                      # download the matching engine
./stockfish-install.sh --force              # download again (e.g. after an update)
./stockfish-install.sh --tag sf_17          # a specific release version
./stockfish-install.sh --use /usr/games/stockfish   # register an existing one
./stockfish-install.sh --help
```

The script detects the platform, downloads the matching binary to
`engines/stockfish/`, **verifies it via a UCI query** (prints the detected
version) and writes `ENGINE_PATH` into `chess.ini`. If the engine is already
present, nothing happens (apart from checking and printing). A **working**
`ENGINE_PATH` is **not** overwritten – with `--use <path>` it is, since that is
an explicit instruction. If the entry points to a missing or non-starting file,
it is repaired (the old value is printed). For a distribution package
(`sudo apt install stockfish`), this is therefore enough:
`./stockfish-install.sh --use /usr/games/stockfish`.

## Usage

### A) Tool API for OpenWebUI (catfish-llm)

```bash
cd ~/catfish-llm
./start_proxy.sh            # starts the proxy (port 8300)
./start_proxy.sh stop       # stops it again
./start_proxy.sh status     # is it running?
```

Then create a new connection in **OpenWebUI**:

- **URL:** `http://<address-of-the-catfish-llm-machine>:8300/v1`

  A machine can be on several networks (LAN, VPN/WireGuard, Docker bridge …).
  `./start_proxy.sh` lists **all** addresses of the host with interface and
  matching API URL when it starts; the same list is available at any time with
  `./start_proxy.sh adressen`:

  ```
  API-Adressen dieses Rechners (die passende im Client eintragen):
    http://127.0.0.1:8300/v1/chat/completions      (lo, nur dieser Rechner)
    http://192.168.1.20:8300/v1/chat/completions   (eth0, lokales Netz)
    http://10.7.0.116:8300/v1/chat/completions     (wg0, VPN – auch von außen erreichbar)
  ```

  (The start script prints its own output in German; the addresses and URLs
  are what matter.)

  Which one fits depends on where the client runs. If the machine is behind NAT
  (or double NAT), **only** the VPN/WireGuard address is usable from the
  outside – not the LAN address from `hostname -I`. The port is read from
  `chess.ini` (`PROXY_PORT`).

  The proxy listens on `0.0.0.0`, so it is reachable on all interfaces – a
  firewall (e.g. `ufw`) has to open the port on the interface in use:
  `sudo ufw allow in on wg0 to any port 8300 proto tcp`.
- **API key:** the value from `PROXY_API_KEY` (empty = none required)
- **Model:** `catfish-llm`

Afterwards just write "Let's play chess, I'll take White" in the chat – the LLM
starts the game, accepts moves, answers with its own move and a comment.
Stockfish analyses can be included at any time on request ("What are the best
moves?").

The tools are also available as a plain REST API:

```bash
curl -X POST http://10.7.0.116:8300/chess/api/brett_ansehen \
     -H 'Content-Type: application/json' -d '{"session_id": "test"}'
```

### Reliable game data (anti-hallucination)

Every tool result ends with a `ZUSTAND` block computed by the server: game ID,
YOU ARE (colour), whose turn it is, the complete move history (SAN + FEN) and
the path to the journal file under `partien_journal/` – it records every half
move in sequence (e.g. `1. e4` / `1... e5`) and can be shown to the LLM
directly when needed. In addition, the proxy checks the final answer: if it
contains moves that were neither played nor are currently legal, the proxy
appends a `[Server-Korrektur: …]` note. Hallucinated moves therefore never
reach the user unmarked. Only unambiguous move notations are checked (piece or
UCI form); bare square mentions ("the king covers e7") and castling written
with zeros (`0-0`) deliberately do not trigger a correction.

**Server-side colour enforcement (since 0.7.2):** moves by the human go through
the dedicated tool `gegner_zug` and are rejected if the LLM itself is to move
(`FARBE-FEHLER`); conversely, `zug_machen` rejects when the LLM is not to move
(`NICHT-DEIN-ZUG`). Moving for the wrong colour is therefore technically
impossible. And: the LLM **no longer selects moves** – when it calls
`zug_machen`, the tool executes its own legal move with Stockfish. A wrong
`eigene_farbe` in `neue_partie` is corrected by the server if the user states
their colour explicitly (`nutzer_farbe`).

**Protection against weak models (since 0.7.3):** if the model only claims a
move "was played" without calling the tool (announce-only), the server executes
the move named in the user message itself. Thought leaks in the response text,
phantom moves and XML-style tool calls (`<function=…>`) are detected:
admonishment plus a correction round in the loop, or generous acceptance. The
user only ever sees correct answers – even free-router models play reliably.

**Native tool tokens (since 0.7.4):** if a fine-tune delivers tool calls in its
own token format (`<|tool_call_begin|>[zug_machen({"zug": "e4"})]`,
`<|tool_call_start|>[brett_ansehen()]<|tool_call_end|>`), the proxy executes
them instead of showing them as text. Several calls in one message are possible
(e.g. `gegner_zug` + `zug_machen`). The parenthesis-free parameter form
`[gegner_zug(zug="b8c6")]` is evaluated as well.

**Equal notations:** move input is accepted in all three common spellings –
English SAN (`Nf3`, `exd5`, `O-O`), German notation (`Sf3`, `Lxf7`, `0-0`,
`e8=D`) and UCI (`g1f3`, `e2e4`, `e7e8q`). The translation happens in
`chess_service.german_to_san()` and therefore on the way **into the game**
(tool parameters, REST API, enforced human move) – not just in the response
check. English-speaking players do not need German piece letters, and the
opponent may still answer in German notation without triggering a server
correction.

For **native function calling in OpenWebUI** (instead of the proxy): import the
file `openwebui_tool.py` as a tool in OpenWebUI – it calls the REST API of the
proxy.

### B) Console app

```bash
cd ~/catfish-llm


./start.sh                       # you = White, LLM = Black
./start.sh --engine=black        # you = Black, the LLM starts
./start.sh --personality="grumpy old grandmaster who comments on everything"
./start.sh --moves=60            # end the game after 60 half moves
```

Move input: English SAN (`Nf3`, `e4`), German notation (`Sf3`) or UCI
(`g1f3`, `e2e4`). `exit` quits.
Every game is saved under `partien/partie_YYYYMMDD_HHMMSS.pgn`.

### C) Shell client against the proxy (chess_shell.py)

If you do not want to start OpenWebUI but still want to play against the **same
proxy** (same games, same grounding), use the terminal client. It speaks the
OpenAI-compatible API of the proxy and renders the board from the FEN the
server provides – including correct orientation for Black.

```bash
./chess_shell.py                       # configuration from chess.ini
./chess_shell.py --farbe schwarz       # you play Black
./chess_shell.py --session abend-1     # fixed game ID (continue later)
./chess_shell.py --url http://10.7.0.116:8300
./chess_shell.py --kein-brett          # do not show the board automatically
```

Example:

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

The client's own labels (`Verlauf`, `Am Zug`, `Partie`) are printed in
German; the commands themselves all have English aliases (see the table).

Moves may be entered in any notation (UCI `e2e4`, English SAN `Nf3`/`O-O`,
German `Sf3`/`0-0`) – anything else is sent to the opponent as a message. Short
commands run directly via the REST API **without an LLM round** and answer
immediately. Every command has a German and an English spelling – both work:

| German              | English              | Effect                       |
|---------------------|----------------------|------------------------------|
| `brett`, `b`        | `board`, `b`         | show the board               |
| `verlauf`, `v`      | `moves`, `v`         | move history + state         |
| `beste [n]`         | `best [n]`           | the n best moves (Stockfish) |
| `bewerten`, `bewertung` | `eval`           | evaluate the position        |
| `neu`               | `new`                | start a new game             |
| `aufgeben`          | `resign`             | resign the game              |
| `hilfe`, `h`, `?`   | `help`               | show help                    |
| `exit`              | `quit`, `q`          | quit                         |

The proxy must be running (`./start_proxy.sh`); the address comes from
`chess.ini` (`PROXY_HOST`/`PROXY_PORT`) or from `--url`.

The client requires `python-chess` and `requests`. If they are missing (call
with the system Python), it **restarts itself with the project venv** – a
previous `source .venv/bin/activate` is not necessary. If there is no venv at
all, it says clearly that `./install.sh` is missing.

## How an LLM move works

1. Stockfish computes the N best candidate moves (`ENGINE_TOP_N`, MultiPV)
   including an evaluation (e.g. `Sf3 (+0.31)`, `e4 (+0.24)`).
2. The LLM receives the position (FEN), the moves so far (SAN) and the
   candidates, and answers in the format:
   ```
   ZUG: e2e4
   KOMMENTAR: The classic opening – the centre is mine!
   ```
   `ZUG` and `KOMMENTAR` are literal protocol labels (the parser expects
   them); the comment text itself may be in any language.
3. The move is **validated**: illegal or not in the candidate list → another
   request; afterwards the best Stockfish move serves as fallback. The game
   therefore always continues.
4. Among the candidates, Stockfish picks randomly by weight on fallback
   (72 % / 18 % / 10 %), so games unfold differently.

## Important settings (`chess.ini`)

| Key                | Effect                                               |
|--------------------|------------------------------------------------------|
| `PROXY_HOST`       | Address the proxy listens on: `0.0.0.0` = all interfaces, `127.0.0.1` = this machine only, or a specific IP (e.g. the VPN address) |
| `PROXY_PORT`       | Port of the proxy (default 8300)                     |
| `PROXY_API_KEY`    | Bearer token clients must send (empty = none)         |
| `PROXY_MODEL_NAME` | Model name the proxy reports to clients (default `catfish-llm`) |
| `ENGINE_SKILL`     | 0–20, Stockfish strength (default 12)                |
| `ENGINE_MOVETIME_MS`| Thinking time per move in ms (default 1200)         |
| `ENGINE_TOP_N`     | Number of candidates for the LLM (1–5)               |
| `LLM_NAME`/`LLM_STIL`| Name & personality of the AI opponent              |

`PROXY_HOST=127.0.0.1` is the safe variant if the proxy should not be reachable
directly – e.g. when access only goes through your own reverse proxy. With a
specific IP it listens exclusively on that interface (handy: expose only the VPN
address). Which addresses fit is shown by `./start_proxy.sh adressen`; the start
script adapts its output to `PROXY_HOST`.

## Chess engine: choosing and swapping

The engine is configured simply: `ENGINE_PATH` in `chess.ini` holds the path to
the UCI engine – this can be swapped at any time (even for a completely
different UCI engine).

| Environment                | Recommendation                                          |
|----------------------------|---------------------------------------------------------|
| x86-64 PC (Linux)          | `stockfish-linux-x86-64-universal` (installer default)  |
| Raspberry Pi (64-bit OS)   | `stockfish-linux-arm64-universal` (installer downloads automatically) |
| Raspberry Pi (32-bit OS)   | `stockfish-android-armv7-neon` (detected by the installer) |
| macOS / RISC-V             | also covered by the installer                           |
| Debian/Ubuntu, anywhere    | `sudo apt install stockfish` → `ENGINE_PATH=/usr/games/stockfish` |
| Other CPU / other engine   | Build it yourself (see below) or swap the UCI path       |

All universal binaries detect the CPU capabilities at runtime and automatically
use the best of them (AVX2, NEON, dotprod, …) – you no longer pick an
architecture by hand. A different engine version:

```bash
./stockfish-install.sh --tag sf_17     # any release tag
./stockfish-install.sh --force         # re-download the same version
```

Check the engine and print the detected path/version – exactly what the script
does at the end:

```bash
printf 'uci\nquit\n' | ./engines/stockfish/stockfish-linux-x86-64-universal | head -3
```

### Building Stockfish yourself (optional)

Normally unnecessary – the installer covers all common platforms. If you still
want to build it, fetch the source yourself and then enter the path in
`chess.ini`:

```bash
git clone --depth 1 https://github.com/official-stockfish/Stockfish.git
cd Stockfish/src
make -j$(nproc) profile-build      # without ARCH = optimal for exactly this CPU
```

```ini
ENGINE_PATH=/path/to/Stockfish/src/stockfish
```

## Hardware note

The installer downloads the official universal binary for each platform – it
picks the best possible variant automatically and therefore runs both on old
hardware without AVX2/BMI2 (e.g. Intel Atom) and on Raspberry Pi and other ARM
boards.

## License

This project is released under the **GNU General Public License, version 3 or
later (GPL-3.0-or-later)** – the full license text is in [`LICENSE`](LICENSE).

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

**Why GPL and not MIT?** Not out of preference, but because the dependencies
dictate it: the proxy imports **`python-chess`** (board logic, PGN, UCI
handling) – and that is licensed under **GPL-3.0-or-later**. A program that
contains GPL code may only be distributed under the GPL. A more permissive
license such as MIT would have been **not permissible** here: MIT and GPL
combine in one direction only (MIT code may go into a GPL project, not the
other way round).

**What this means in practice**

- Anyone redistributing the proxy or building a package from it must ship or
  point to the source code – that is the core of the GPL. It is already
  satisfied here: the project is public on GitHub.
- The remaining dependencies are GPL-compatible: `Flask` (BSD-3-Clause),
  `waitress` (ZPL 2.1), `requests` (Apache-2.0).
- **Stockfish** (also GPL-3.0) is only downloaded by the installer as a
  **standalone program** – it is not part of this repository but a separate
  tool (see [`stockfish-install.sh`](stockfish-install.sh)) that brings its own
  license and source. Anyone redistributing Stockfish must comply with its GPL
  terms.
- For **disclosure obligations**, a reference to this repository is enough;
  the license explicitly allows modifying and redistributing the program – just
  not as a closed-source product.

## Powered by AI

This project was **built with AI support** – code, documentation and tests were
developed in dialogue with an AI coding agent. The idea, the architecture, the
domain decisions and the final acceptance come from the human; the
implementation was a joint effort.

That is no coincidence – it fits the subject: the program lets an LLM play
chess and draws exactly the line such systems need: the model proposes, the
server verifies. Moves are validated, hallucinated moves corrected, tool calls
enforced. The same approach helped during the build: let a lot be written,
verify all of it.
