# Structured moves and reasoning

The SDK starter explicitly constructs a strict legal-move enum and sends it as
`response_format` for its final answer. That recommendation belongs to the agent:
the gateway permits general planning, arbitrary structured outputs, and tool calls
without adding a chess schema. It configures native reasoning for the captured
model and returns final content separately from reasoning. All calls use the same
[submit/poll/cancel API](inference.md).

## Recommended final-move format

The starter in `agent.py` shows the complete request format:

```python
move_format = {
    "type": "json_schema",
    "json_schema": {
        "name": "chess_move_selection",
        "strict": True,
        "schema": {"type": "string", "enum": context.position["legal_moves"]},
    },
}
trace = context.inference.generate(
    context.position, messages,
    decoding="uci", response_format=move_format,
)
return Decision.from_trace(trace)
```

This constrains the final answer to a JSON UCI string such as `"e2e4"`. Ordinary
`generate()` calls omit this format by default. They can return free text,
custom JSON, or native tool calls; see the [recursive tool-loop example](custom-agents.md).
`decoding="uci"` records a final chess selection but does not add a schema itself.

`generate_move()` is a convenience helper that supplies the same strict enum.
Its optional `candidates` list restricts the choices to your legal subset. For
`decoding="candidate_index"`, supply an ordered candidate list and ask for one
zero-based integer; the helper constrains it to valid indices. For hypothetical
`target_fen` requests, supply `legal_moves` for that target board as well. Refresh
all move choices after each observation. The organizer independently validates
candidate legality and the move submitted for the actual position.

The gateway forwards your supplied format. With thinking disabled and no tools,
llama.cpp can compile a strict scalar enum into an equivalent JSON literal grammar,
excluding extra prose and thought-channel tokens. This adaptation is independent
of chess: unrestricted calls never receive a move grammar. With thinking enabled,
the backend uses `response_format` and permits native reasoning before the
constrained final answer. String answers retain their JSON quotes even when only
one choice is available; use `trace["selected_move"]` for the decoded move.

## How reasoning and the schema coexist

The model generates its native thinking section first, then the constrained final
answer. The llama.cpp chat parser builds a grammar that permits thinking text
inside its model-specific delimiters and enforces the enum on the final answer.
The enum does not restrict moves mentioned during thinking: the model can compare
several possibilities without making the final selection ambiguous.

A completed response may look like this:

```json
{
  "output": "\"e2e4\"",
  "reasoning_content": "Model analysis, if generated and extracted.",
  "selected_move": "e2e4",
  "effective_reasoning_effort": "low",
  "effective_reasoning_budget_tokens": 96,
  "finish_reason": "stop"
}
```

This is an excerpt, not the full trace envelope. Reasoning is private to the
request's owner. It may be null or empty, including when the model decides to
answer directly. The gateway retains original content and token usage and
validates final selection independently of the backend. Never scan reasoning for
the first UCI move or choose a replacement when final selection is missing.
Return `Decision.from_trace(trace)` to preserve organizer evidence.

Constrained generation ensures the allowed shape when generation completes; it
does not guarantee that a request finishes before its clock/token limit, or that
the selected legal move is strategically good. A missing final answer is still a
failed selection. There is no automatic legal fallback.

## Controls and budgets

The normal participant control is a direct token budget. You do not need to pick
an effort level or provide a model-specific tag:

```python
trace = context.inference.generate_move(
    context.position, messages,
    reasoning_budget_tokens=1024,
    cancel=context.cancel,
    timeout=max(0.05, context.remaining_seconds - 0.5),
)
return Decision.from_trace(trace)
```

A positive budget automatically enables thinking; zero requests thinking off. An explicit
null budget enables unrestricted thinking. The gateway configures native thinking
for the captured model. Effort is an advanced override, primarily useful for
Horizon. Omitting both reasoning controls preserves the legacy no-thinking
default; the starter always supplies a budget.

The initialization envelope advertises supported `inference.reasoning_efforts`.
The current llama.cpp backend advertises `none`, `low`, `medium`, and `high`.
An empty or missing list means the starter omits reasoning controls. A custom
agent should do the same for older gateways and other serving backends. The
SDK checker simulates these controls with a fake model; it cannot verify model
reasoning or grammar behavior.

| Control | Meaning |
|---|---|
| `reasoning_effort` | Optional advanced override: `none`, `low`, `medium`, `high`. A direct nonzero/null budget without this override selects native thinking (`high` on Horizon; on/off for the others). |
| `reasoning_budget_tokens` | Optional nonnegative integer limiting thinking generation, on llama.cpp. Positive values enable thinking; zero requests thinking off; explicit null enables unrestricted thinking. Omitting the budget leaves thinking unlimited if enabled through `reasoning_effort`. Omitting both controls preserves the no-thinking default. |
| `max_tokens` | Participant-chosen total output cap, including thinking and final answer. Null/omitted uses the model's remaining context capacity. |
| `inference.answer_token_reserve` | Suggested answer headroom, currently 32 tokens. The starter uses it; the organizer does not enforce it. |
| `timeout` in `InferenceClient.generate` | Participant-chosen local wait deadline, in seconds. It is not an HTTP request field or an extension of the chess clock. |

**There is no organizer-imposed prompt, output or reasoning token ceiling.**
Initialization advertises `limits.output_tokens: null` and the model's
`limits.context_tokens`. Participants choose how much to reason. The game clock,
model context window, transport byte caps and your player's single generation
slot still apply.
A request with an explicit `max_tokens` must fit the remaining model context.
Without one, generation can use that remaining capacity. A positive thinking
budget with `reasoning_effort="none"` is rejected as contradictory input.
A participant-supplied strict scalar enum with thinking disabled uses a direct
JSON literal grammar with effective effort `none` and budget zero. Enabled thinking with no budget is recorded
as `-1` in the effective trace, meaning unrestricted thinking within the
generation/context and clock bounds. Disabled thinking is recorded as zero.

At a positive thinking limit, the pinned llama.cpp runtime forces the native closing
delimiter and lets the model generate the constrained answer. This is different
from a `stop` string, which terminates the entire response. **Do not set
`stop=["</think>"]`**: that would stop before the final move. Forced closure can
reduce answer quality, and the answer still has to finish within `max_tokens`.
The reserve is headroom, not a guarantee against truncation or model repetition.

Zero is normalized to effective effort `none`: live Horizon testing found that
the pinned runtime can fail to enforce a zero budget when thinking is already
open in the template prefill. Nemotron also fails constrained generation when a
zero-budget sampler replays an already closed thinking prefill. The organizer
disables thinking in the template and disables that sampler, rather than using
zero as a runtime forcing budget. The logical effective thinking budget is zero.

The starter chooses 512 total tokens and up to 96 thinking tokens by default.
It uses the direct budget interface; no effort setting is required. These are **participant starter preferences**, not organizer
restrictions. Edit `agent.py` or `harness_config` to raise them or remove them.
Legacy gateways may advertise a numeric output cap; the starter respects that
for compatibility. The current organizer advertises null.

The earlier 128-token probe was a small-budget stress test, not an event limit.

You can change the starter through the captured `submission.json` configuration:

```json
{
  "ponder": false,
  "max_tokens": 512,
  "reasoning_budget_tokens": 96
}
```

This object belongs inside `harness_config`. To let the starter use all remaining
context and impose no thinking-token budget, set both `max_tokens` and
`reasoning_budget_tokens` to null. Its local waiting deadline still follows your
remaining chess clock. Custom agents can choose their own deadlines too.
For the Python client, the corresponding direct call is:

```python
trace = context.inference.generate_move(
    context.position,
    messages,
    cancel=context.cancel,
    timeout=max(0.05, context.remaining_seconds - 0.5),
    max_tokens=128,
    reasoning_budget_tokens=96,
)
return Decision.from_trace(trace)
```

Use this explicit example when the gateway advertises reasoning controls. Leave time for delivering the move. Queueing and thinking consume
your chess clock on your turn; pondering uses your own dedicated process and
generation slot.

## Differences between the four models

The organizer inspected the embedded templates in the named Q4_K_M GGUF files on
13 October 2026. These observations apply to those artifacts; other checkpoints,
templates, or serving runtimes can behave differently.

| Model ID | Native reasoning | Effect of the controls |
|---|---|---|
| `horizon` | `<ifm\|think>`, `<ifm\|think_fast>`, or `<ifm\|think_faster>` | `high`, `medium`, and `low` select distinct trained formats, respectively. The inspected GGUF supports `enable_thinking=false`. |
| `qwen` | `<think>…</think>` | Thinking is on/off. The inspected template does not use an effort level. `low`, `medium`, and `high` enable the same format; use the token budget to limit its duration. |
| `nemotron` | `<think>…</think>` | Likewise on/off in the inspected template. The exact 4B model is a Nemotron-H hybrid; do not copy a 30B model's parser/configuration by name alone. |
| `gemma` | `<\|channel>thought…<channel\|>` | `enable_thinking` adds the thinking token through its system template. It does not implement distinct effort levels. llama.cpp extracts the thought channel and constrains the final JSON. |

Sources: [Horizon model instructions](https://huggingface.co/IFM/K2-Horizon-3.7B),
[Qwen GGUF instructions](https://huggingface.co/empero-ai/Qwen3.8-4B-Distill-GGUF),
[NVIDIA's exact 4B model instructions](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16),
and [Gemma 4 instructions](https://huggingface.co/google/gemma-4-E4B-it).
Horizon publishes a high-effort recommendation with much larger output budgets;
short bounded chess reasoning is an experiment, not that published evaluation
configuration. Reasoning may improve decisions, but that must be measured for
these quantizations and this game clock.

On the local `K2-Horizon-4B-Q4_K_M.gguf` (SHA-256
`07773aed93890d4f5d08b421430000d5c8d73cefc74fbdc32e67d820f23207b2`), the
13 October smoke probe observed no thinking text in low mode and observed
thinking with valid final selections in medium/high mode at a 96-token budget.
The direct budget interface uses Horizon's high/native mode by default;
custom harnesses can override the effort for speed. This local file is a different artifact from the named NANI-Nithin GGUF;
its result does not validate that file or the other three models.

The exact inspected repository revisions are:

| GGUF repository | Revision |
|---|---|
| `NANI-Nithin/K2-Horizon-3.7B-GGUF` | `6b8f11e53bd89304a715ba7b6cffb37589d90834` |
| `empero-ai/Qwen3.8-4B-Distill-GGUF` | `391fc7d103e3942a408def3e4f51c2f85d464417` |
| `lmstudio-community/NVIDIA-Nemotron-3-Nano-4B-GGUF` | `f4732c3cd1c961777138b226030e6c17f6f7db84` |
| `unsloth/gemma-4-E4B-it-GGUF` | `bfc15c382204943c3a8fff0c750b94ae2364d7a3` |

The organizer deployment pins llama.cpp commit
`097cf33d9499b1eade67d51e16d7c75427e7b267`, with the organizer's
`k2-end-tags-v1` compatibility patch. The patch accepts Horizon's native thinking
closing tags across effort modes: the exact GGUF sometimes closes medium
thinking with the high-mode tag, which otherwise prevents final JSON parsing.
The runtime includes native Horizon and Gemma parsers, schema-aware thinking,
and per-request reasoning budgets. Model
cards can lag runtime support; the Horizon quantizer's older fork requirement is
not the deployment's runtime selection. Organizer deployment records determine
the actual model revision; the table above records this investigation only.

## Debugging and live verification

Inspect `selected_move`, `selection_status`, `selection_reason`, `finish_reason`,
`output_truncated`, `usage`, `effective_reasoning_effort`, and
`effective_reasoning_budget_tokens`. A `token_limit_without_selection` or
`missing_final_answer` means the model did not finish a usable answer. Reducing
thinking, adjusting the prompt, or allocating more permitted tokens can help;
choosing a fallback move in your code does not preserve model evidence.

The SDK checker tests the wire contract, cancellation and evidence using a fake
model. The organizer has no fixed generation wall-time timeout; the game clock
and participant cancellation remain authoritative. The organizer's `deploy/runpod/probe_structured_reasoning.py`
tests exact serving responses, enums, native reasoning extraction, bounded
thinking, single-choice behavior and candidate-index decoding. The separate
`~/chessEngine` harness builds on this SDK for game tests.
Do not infer playing strength from a schema probe or a shortened game fixture.

### Verified smoke tests, 13 October 2026

The four exact GGUFs above were tested on one NVIDIA A40 with 16,384-token worker
contexts. The bounded probe used 128 total output tokens and a 96-token thinking
budget chosen by the test. It covered eight positions/selection cases across
`none`, `low`, `medium`, and `high`: **128 cases passed**, with visible reasoning
from every model. Horizon's 32 cases also passed again after the parser patch.
These small test budgets are not organizer restrictions.

The updated SDK-derived chessEngine compiled its native search and ran a
six-ply pause/resume fixture for each model using direct thinking budgets of
0, 32, and 96 tokens. All four fixtures passed: **24 accepted moves**, separate
reasoning from every model, no failed requests, and no invalid selections.
Horizon was re-tested with the patched runtime. This validates the serving and
evidence path under this harness; it does not establish playing strength or
event concurrency capacity.

Unrestricted-thinking probes used `reasoning_budget_tokens=null` (native `-1`)
and a test-selected total allowance of 8,192 tokens. Qwen, Nemotron, and Gemma
finished both the opening UCI and candidate-index cases with separate reasoning
and valid final selections. With the parser patch, Horizon finished both cases
in medium mode and the opening in high mode. **Horizon high mode looped on the
candidate-index case and exhausted 8,192 tokens without a final answer.** Its low
mode completed both cases but emitted no visible reasoning. Unlimited reasoning
does not guarantee a usable answer before the clock or context runs out.

The patch addresses a different failure: Horizon can close medium-mode thinking
with `</ifm|think>`, rather than the template-selected `</ifm|think_fast>`. Accepting
all three native closing tags restores final-answer parsing; it does not stop
repetition inside an unfinished thinking section.

### Longer-game findings

A later full four-model tournament through chessEngine accepted 701 moves across
seven games without illegal actions or organizer failures. It also exposed
19 responses that exhausted the harness's 16-token answer allowance without a
final selection. Some Gemma responses contained extracted thinking even though
the request configured thinking off. Treat `effective_reasoning_effort` and
`effective_reasoning_budget_tokens` as the requested serving controls; inspect
`reasoning_content` and `selection_status` to see what actually happened. A zero
budget previously configured only the template off. For participant-supplied strict scalar enums, the gateway now compiles
generation to the requested answer enum, excluding a thought channel on no-thinking requests.

Three clock results followed a participant harness timeout stall: a short local
waiting deadline expired, a safety retry used another short deadline, and the
callback then stopped without a move. Those standings are affected by this bug.
The SDK-derived chessEngine now sizes safety retries using measured request
latency, bounded by the current clock, and requires fresh completed model evidence
before returning a move. It passed separate six-ply recovery checks against all
four live models with controlled timeout injection. The full tournament was not
repeated after that fix. Custom harnesses should recover within the remaining
clock after a local timeout rather than abandon the current observation.

The provided SDK runner now retries `TimeoutError` on a still-current own turn
with a short bounded backoff, using the original observation time so every retry
continues to spend the real clock. A new position, suspension or shutdown cancels
the retry. Expired pondering is discarded. Expected stale-admission conflicts are
treated as cancellation; other conflicts and unexpected exceptions remain visible.
HTTP admission and polling use the remaining local deadline rather than adding
another fixed socket wait. The SDK never fabricates a move during recovery.
