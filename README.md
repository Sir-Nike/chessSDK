# Chess participant SDK

Build your own agent for Triad Tactics Chess. **Use any language, framework, agent
harness, or internal architecture.** LangChain, Pi, a custom planner, and a plain
program are all viable choices. The evaluator only speaks the published process
and inference protocols; using this Python package is optional.

Framework freedom does not change the event rules: the approved organizer-served
LLM selects the final move, and imported chess engines are prohibited during
hosted evaluation. See [the rules](rules/RULES.md).

This repository is a **development starter**, not an official event release.
Event clocks and the LoRA rank ceiling (1–32) follow the participant rulebook.
The four allowed GGUF bases and tested revisions are listed below. Adapter
training checkpoints, tensor schema/dtype/size limits and official container
settings must be published before adapter submissions open.

## Start building

Install uv 0.11.28 for the Python starter and checker. The project defaults to
Python 3.12 and uses the committed `uv.lock`.

From this repository's root:

```sh
uv sync --locked --extra dev
uv run --locked --extra dev chess-sdk check --report reports/check.json
uv run --locked --extra dev chess-sdk check --colour black
```

Edit [`agent.py`](agent.py). Its `choose(context)` function receives the current
position, clocks and inference client, and returns a model-backed decision.
The provided runner handles stdin, correction notifications, stale analysis,
cancellation and suspension. It runs once per game and retains process memory.

The starter explicitly sends `response_format` with a strict enum of legal moves.
That recommendation lives in the SDK: ordinary `inference.generate()` calls can
return text, use custom schemas, or request tools. `generate_move()` is an optional
helper for the same final-move pattern. The starter also enables short, bounded
reasoning when the backend advertises support. See [structured moves and reasoning](docs/reasoning.md)
for model differences, budgets, configuration and debugging. All four approved
GGUFs passed structured-output smoke tests and six-move games through the updated
SDK-derived chessEngine. See the guide for the runtime patch and test limits.

The checker starts a temporary local inference gateway with a **fake model**.
It checks the handshake, off-turn behavior, evidence, correction, pause/resume and
shutdown. Passing it establishes local compatibility, not playing strength,
complete rules compliance, sandbox safety or official acceptance.

You may replace the entire starter. Check any executable with:

```sh
uv run --locked --extra dev chess-sdk check --report reports/custom.json --command ./your-agent
```

Everything following `--command` is your executable and its arguments. Run from
the directory your agent expects. The checker supplies the socket and credential;
there is no need to download a model for these checks.

## Choose an approved model

Set `model_id` in [`submission.json`](submission.json) to one of these IDs.
Only the **Q4_K_M** GGUF variant is allowed. The starter defaults to `qwen`.

| `model_id` | Allowed model |
|---|---|
| `horizon` | [K2-Horizon-3.7B (Q4_K_M)](https://huggingface.co/NANI-Nithin/K2-Horizon-3.7B-GGUF) |
| `qwen` | [Qwen3.8-4B-Distill (Q4_K_M)](https://huggingface.co/empero-ai/Qwen3.8-4B-Distill-GGUF) |
| `nemotron` | [NVIDIA Nemotron-3-Nano-4B (Q4_K_M)](https://huggingface.co/lmstudio-community/NVIDIA-Nemotron-3-Nano-4B-GGUF) |
| `gemma` | [Gemma-4-E4B-it (Q4_K_M)](https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF) |

[`rules/approved-models.json`](rules/approved-models.json) records the exact
GGUF filenames, repository revisions, SHA-256 hashes and pinned download links
used in the RunPod rehearsals. These are GGUF serving identities; they do not
identify a compatible original checkpoint for LoRA training.

Your hosted container does not download or load these models. The organizer
runs a separate inference process for each player and game attempt; you call
it through the scoped local inference API. Local practice may use the pinned
downloads. Keep `adapter: null` until per-base adapter compatibility and training
checkpoint revisions are published.

## Interface and submission

| Read | Purpose |
|---|---|
| [Rules](rules/RULES.md) | Confirmed competition rules and pending event parameters |
| [Process protocol](docs/protocol.md) | Exact stdin/stdout events and move envelope |
| [Inference API](docs/inference.md) | Authenticated queued generation and move evidence |
| [Structured moves and reasoning](docs/reasoning.md) | Preconfigured legal-move enums, native thinking and token budgets |
| [Custom agents](docs/custom-agents.md) | Framework freedom and optional Python helper contract |
| [Personal Stockfish practice](docs/practice.md) | Local GGUF or custom agent play, selectable colour/engine level/clocks |
| [Docker guide](docs/docker.md) | Offline builds, Chess harness resource profile and submission preparation |
| [JSON Schema](src/chess_sdk/schemas/chess-v1.json) | Machine-readable protocol definitions |
| [Development settings](rules/development.json) | Event clocks plus provisional development values |

The [`Dockerfile`](Dockerfile) is editable. Declare dependencies with `uv add PACKAGE` in
[`pyproject.toml`](pyproject.toml), prepare the vendored wheels, and build
with networking disabled. See the Docker guide for the complete sequence.
[`submission.json`](submission.json) records your selected approved model,
optional adapter directory and harness configuration. Select one of the IDs
above before submitting your repository and exact commit.

There is one editable starter and no separate example collection. The organizer
engine lives in `~/Chess`, and the personal Stockfish practice service lives in
`~/triad-tactics-stockfish-practice`. This SDK includes a practice client and
CLI; it does not run the Stockfish service. See [personal practice](docs/practice.md).

## SDK verification

```sh
uv run --locked --extra dev pytest -q
uv run --locked --extra dev ruff check src agent.py scripts tests
```

The optional `tests/test_evaluator.py` integration requires the separate organizer
engine and its installed dependencies. Run it using that engine's Python with
`CHESS_ARENA_PATH` set to its repository; `CHESS_ARENA_CONFIG` can select a running
model backend. It creates an isolated in-memory development event, checks paired
pause/resume, and verifies six model-backed moves. It does not change an existing
event database or confer official approval. Local reports are written to ignored
`reports/` files. The standard SDK test run skips this integration when the engine
is absent.
