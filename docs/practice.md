# Personal Stockfish practice

This runs on your own computer, with your own local agent/model. Its results are
separate from hosted practice/final tournaments. Hosted containers cannot access
the Stockfish service.

The organizer issues a **practice** team token. Hosted Chess credentials do not
work here.

```sh
export STOCKFISH_API_URL=http://127.0.0.1:8003
export STOCKFISH_TEAM_TOKEN='your-practice-team-token'
uv run --locked --extra dev chess-sdk practice --colour random --level 5
uv run --locked --extra dev chess-sdk practice --colour black --level 10 --white-minutes 10 --black-minutes 10
```

With no `--agent`, the CLI accepts interactive UCI input. For your local model,
create a function such as `local_agent:choose(session)` that returns one UCI
string (for example `e2e4`, or `e7e8q` for promotion), or `resign`. It receives
current FEN, full move history, legal moves, clocks, your colour, opponent profile,
and agent metadata. Your function owns local inference and tool execution:

```sh
uv run --locked --extra dev chess-sdk practice --agent local_agent:choose --color white --level 3 \
  --white-minutes 5 --black-minutes 5 --increment 2
```

For an already-downloaded GGUF, `chess-sdk practice --model PATH_TO_GGUF` starts
`llama-server` locally, sends each position with a schema constrained to the
current legal UCI moves, and shuts the server down when the practice command
ends. Install/build llama.cpp separately and make `llama-server` available on
`PATH`, set `LLAMA_SERVER`, or pass `--llama-server /path/to/llama-server`.
The default is 99 GPU layers; use `--gpu-layers 0` for CPU inference. The default
context is 4096 tokens and can be changed with `--context-size`.

```sh
uv run --locked --extra dev chess-sdk practice \
  --model ~/Downloads/Qwen3.8-4B-Q4_K_M.gguf \
  --colour white --level 5 --white-minutes 10 --black-minutes 10
```

The practice runner only uses the GGUF you provide; it does not download weights
or change the tournament harness. Model generation time counts against your
practice clock. If llama.cpp cannot load the model or the model fails to return
a legal move, the CLI prints the runtime error or the practice service rejects
the move.

This callback differs from the hosted starter's `agent:choose(context)`, which
returns a `Decision` backed by organizer inference. Use `--model` or write a
local-model callback for practice; the hosted starter is not a practice agent.

Default clocks are **600 seconds per side with zero increment**, and the default
colour is random. API levels 1–10 select search-depth and thinking-time budgets;
they are not Stockfish Skill Levels or Elo ratings. See the practice server's
`/docs` for its current profile values. Even the lowest Stockfish preset can be
much stronger at chess than a local LLM, so level 1 is not necessarily a balanced
matchup.

For any framework or language, use the HTTP endpoints in the service's `/docs`.
The optional Python client is:

```python
from chess_sdk import PracticeClient

with PracticeClient() as api:
    game = api.create(colour="black", level=5,
                      agent={"model": "my-local-model", "harness": "v2"})
    while game["status"] == "running":
        game = api.wait(game["id"])
        if game["status"] != "running":
            break
        uci = your_local_agent(game)  # your LLM chooses the move
        reply = api.move(game, uci)
        game = reply["session"]
        if not reply["accepted"]:
            print(reply["reason"])  # correct the move; your clock keeps running
    print(game["result"])
    print(api.pgn(game["id"]))
    print(api.history())
```

Save the session ID printed at startup. `chess-sdk practice --session SESSION_ID`
reconnects without creating a game. Disconnecting or closing the CLI does not
pause the clock or resign; `api.resign(game)` explicitly resigns. A Black session
queues the engine's first move immediately, so cancellation before the first
accepted move may only be available briefly.

Create and action requests carry stable IDs; the client preserves them when
retrying a lost HTTP response. Replaying an action returns the original
acknowledgement plus the current session snapshot. Poll the session for an engine
reply; do not submit the previous move again under a fresh ID.

This practice service does not calculate ratings. Engine failures mark a game
invalid, and a service restart clears in-memory sessions and history.
