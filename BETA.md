# Beta test – catfish-llm 0.9.9

**Language:** [Deutsch](BETA.ger.md) · English

Thanks for testing with us! You play chess against an LLM that runs the game
through local tools; the moves come from **Stockfish**, the commentary from
the model. Everything runs on the catfish-llm machine – your chat only goes
to the LLM endpoint configured there.

**What 0.9.9 is:** "works in practice, but is not 1.0 yet". That is exactly
what we are looking for: the cases where it still does not.

---

## 1. Access

Ask the operator for the address of the catfish-llm machine. Two cases:

- **Same network:** `http://<LAN-IP>:8300/v1`
- **From outside / behind NAT:** the WireGuard address, e.g.
  `http://10.7.0.116:8300/v1`

Check reachability:

```bash
curl http://<address>:8300/health
# -> {"service":"catfish-llm","status":"ok","version":"0.9.9"}
```

If nothing comes back and the machine is running, the firewall rule is
usually missing: `sudo ufw allow in on wg0 to any port 8300 proto tcp`.

On the catfish-llm machine, `./start_proxy.sh check` verifies the complete
installation (configuration, Python environment, packages, engine) and shows
what is missing. `./start_proxy.sh` and `./start.sh` set up anything missing
automatically before starting.

### Setting it up in OpenWebUI

| Field     | Value |
|-----------|-------|
| URL       | `http://<address>:8300/v1` |
| API key   | empty (or the value from `PROXY_API_KEY`) |
| Model     | `catfish-llm` |

Any OpenAI-compatible client works the same way.

**Without OpenWebUI** – directly in the terminal (on the catfish-llm machine):

```bash
./chess_shell.py --url http://<address>:8300 --farbe schwarz
```

The shell client shows the board after every move, understands moves in all
three notations and offers short commands such as `brett`, `beste 3` or
`verlauf` (`hilfe` lists them; the English aliases `board`, `best`, `moves`
and `help` work as well).

---

## 2. Playing

Just write: "Let's play chess, I'll be White." After that you name your
moves. **All three notations work:**

| Notation | Examples |
|---|---|
| UCI | `e2e4`, `g1f3`, `e1g1`, `e7e8q` |
| English SAN | `e4`, `Nf3`, `exd5`, `O-O`, `e8=Q` |
| German notation | `e4`, `Sf3`, `Lxf7`, `0-0`, `e8=D` |

The opponent replies in whichever notation you use.

---

## 3. What we would like you to test

Please play through it once and **note down what happens**:

1. **Sync:** after every move ask `Was steht auf dem Brett?` ("What is on the
   board?"). The move the model names must match the move history.
2. **Repetition:** name the same move twice in a row, or write "try that
   again". In the past the move got lost here.
3. **Mixed notations:** your moves in UCI, the reply in German notation (or
   the other way round). There must be **no** `[Server-Korrektur: …]`
   appearing.
4. **Castling:** short (`e1g1` / `O-O` / `0-0`) and long. Same for promotion.
5. **Pressure on the model:** contradict it, ask "are you sure?", have it
   show the best moves ("What are the best moves here?").
6. **Non-chess:** ask something else in between. It should answer normally,
   without tool clutter in the text.
7. **New game:** start a new game mid-game – switch colours.

### These messages are bugs (please report them)

- "Das Werkzeug lieferte keine Antwort. Bitte versuch es erneut." (The tool
  returned no answer.)
- `[Server-Korrektur: …]` even though the moves named were actually played
- The chat names a move that is not in the history (or the other way round)
- Raw tool markup in the text: `TOOL: …`, PARAMS, `<|tool_call…|>`
- The opponent moves for you, or you move twice in a row
- An answer in a language other than yours

The program's own messages are in German; that is not a bug in itself. What
matters is whether they appear at all, and whether the board stays in sync.

---

## 4. Reporting bugs

The most helpful thing is a package of three:

1. **Version:** `curl http://<address>:8300/health`
2. **Move history of the game:** `partien_journal/partie_<id>.md`
   (the `<id>` is in the ZUSTAND block or from `ls partien_journal/`)
3. **Request log:** the last ~30 lines from `~/catfish-llm/proxy.log`

Plus three sentences: *what you wrote*, *what you expected*, *what actually
happened*. If you can, attach the chat as a screenshot or as text.

---

## 5. Known limits (not bugs)

- **Rate limits:** the default LLM endpoint is a free-tier router. On
  HTTP 502 / "upstream_error" please simply send the message again.
  The game itself stays intact.
- **Games live in memory.** Restarting the proxy ends running games;
  finished ones are stored as PGN under `partien_proxy/`.
- **The chat is the memory.** If the history is deleted in the client, a new
  game begins.
- **Strength** depends on `ENGINE_SKILL` in `chess.ini` (0–20, default 12).

---

Copyright (C) 2026 Olav (https://github.com/o-valo) ·
SPDX-License-Identifier: GPL-3.0-or-later ·
Free software under the GNU GPL v3 or later, without warranty –
see [`LICENSE`](LICENSE).
