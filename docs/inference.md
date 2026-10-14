# Organizer inference API

Every game supplies `CHESS_INFERENCE_SOCKET` and `CHESS_INFERENCE_TOKEN` in the
agent's environment. In official containers the socket is
`/run/inference/gateway.sock`. Connect using HTTP over that Unix domain socket,
with `Authorization: Bearer <token>` on every request. `http://gateway` is a
placeholder HTTP base URL when using a socket transport; it is not an external
hostname to contact. No TCP/network access is needed.

Credentials are scoped to your entry and game attempt. They do not authorize
operator actions, another team's trace, another game, or selecting a different
base/adapter. Do not log, capture or bake credentials into your repository/image.
The Python client supports `CHESS_INFERENCE_URL` for trusted TCP development only;
that is not the official container interface.

## Submit, poll, cancel

`POST /v1/inference` returns HTTP 202 and `{"request_id":"request-1"}`.
Its body is:

```json
{"game_id":"game-1","attempt_id":"attempt-1","position_version":0,"messages":[{"role":"system","content":"Plan how to analyze this chess position."},{"role":"user","content":"Current FEN and any tool results here."}],"max_tokens":null,"temperature":0,"top_p":1,"stop":[],"seed":null,"target_fen":null,"decoding":"text","candidates":[],"response_format":null,"tools":null,"tool_choice":null,"parallel_tool_calls":null}
```

The first four fields are required. Omitted controls have the defaults above.
Identity fields come from the latest observation. Unsupported fields are rejected;
there is no `model`, `adapter`, `stream` or chat-completions route in the
participant API. The captured entry determines model and adapter identity.

| Control | Accepted values |
|---|---|
| `messages` | 1–64 messages; roles `system`, `user`, `assistant`, `tool`; string content (at most 65,536 characters), or null for an assistant tool call |
| `max_tokens` | Participant-chosen positive integer, or null/default for the remaining model context capacity |
| `temperature` | Number between 0 and 2 inclusive |
| `top_p` | Number greater than 0 and at most 1 |
| `stop` | At most eight strings |
| `seed` | Null or integer from 0 to 2³²−1 |
| `target_fen` | Null for current board, or a valid hypothetical board FEN for pondering |
| `decoding` | `text` (default) for general calls, `uci` or `candidate_index` to record a final move selection; this does not constrain generation |
| `candidates` | At most 256 legal UCI strings for move decoding; nonempty for candidate-index decoding |
| `response_format` | Optional participant-supplied `text`, `json_object`, or `json_schema` response format; omitted/null means unconstrained |
| `tools` | Optional array of function definitions with `name`, `description`, `parameters`, and optional `strict` |
| `tool_choice` | Optional `auto`, `none`, `required`, or a named function choice |
| `parallel_tool_calls` | Optional boolean forwarded to the backend |
| `reasoning_effort` | Optional advanced `none`, `low`, `medium`, `high`; a direct budget enables native thinking without this field; llama.cpp only |
| `reasoning_budget_tokens` | Direct nonnegative integer: positive enables thinking, zero disables it, explicit null enables unrestricted thinking; llama.cpp only |

Check initialization's `inference.reasoning_efforts` before sending reasoning
controls. Omit them when that list is empty or missing. A participant-chosen output cap includes
thinking and the final answer. The organizer imposes no prompt, output or
reasoning token ceiling. `limits.output_tokens` is null; `limits.context_tokens`
reports the physical context window. The starter suggests 32 tokens of answer
headroom, and the trace reports effective settings. See
[structured moves and reasoning](reasoning.md) for defaults and model differences.

The serialized request also has a byte cap. Prompt tokens and prompt-plus-output
context must fit the model's physical context window. Character or
word counts are not authoritative token counts. Backend token accounting enforces
these limits. Stay conservative and do not send full growing history unnecessarily.
One agent may have **one queued/running request** at a time. Normal queueing and
model computation consume your clock when it is your turn. Off-turn requests use
the same player-specific process, generation slot and limits. Each active
player has an independent RunPod process, including same-model opponents.

`GET /v1/inference/request-1` returns HTTP 200 with a trace. States are `queued`,
`running`, `completed`, `failed`, `cancelled`. Poll at a reasonable interval
(the starter uses 30 ms). Only a completed final-move call supplies accepted move evidence.
For a request with `decoding="uci"`, a completed trace contains these relevant
fields (additional timing fields may also be present):

```json
{"id":"request-1","status":"completed","game":"game-1","attempt":"attempt-1","entry":"entry-white","version":0,"target_fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","barrier":0,"to_move":true,"model_id":"approved-model-id","output":"e2e4","selected_move":"e2e4","finish_reason":"stop","usage":{"prompt_tokens":80,"completion_tokens":4}}
```

`version` records request admission; `target_fen` and `barrier` determine whether
that analysis is usable for the actual position. `to_move` says whether it was
your turn at admission. Pondered evidence can be used later only if the resulting
actual FEN, entry, attempt and barrier match. It is not permission to move before
your turn. Tokens/latency in the local fake fixture are estimates; real model
usage comes from the serving backend.

`DELETE /v1/inference/request-1` cancels unfinished work and returns HTTP 200
with `{"status":"cancelled"}`. Repeated cancellation is safe. Completed traces
are preserved; the delete acknowledgement does not rewrite their completed
status. Read if you need to know the actual final status.

## General inference and final moves

The gateway does not construct a legal-move schema. General calls default to
`decoding="text"` and accept arbitrary text, participant-defined structured output,
and native function calls. Returned `trace["message"]` contains the assistant's
content and any `tool_calls`; append it to your history, execute tools locally,
and send each result with role `tool` and its `tool_call_id`. Repeat as needed.
The gateway only generates requests for tools; your harness executes them.
Tool support depends on the captured model and serving backend. Every call uses
the same scheduler and consumes your clock on your turn.

The SDK starter visibly supplies `response_format` with a strict legal UCI enum
for its final move. The optional helper implements that recommendation:

```python
trace = context.inference.generate_move(
    context.position, messages,
    cancel=context.cancel,
    timeout=max(0.05, context.remaining_seconds - 0.5),
)
return Decision.from_trace(trace)
```

`generate_move()` builds `{"type": "string", "enum": legal_moves}` from the
observation. An optional `candidates` list narrows it to that legal subset.
For `decoding="candidate_index"`, supply an ordered, nonempty candidates list;
the helper uses an integer enum from zero through `len(candidates)-1` instead.
For a hypothetical `target_fen`, also supply `legal_moves` for that board;
the organizer independently checks candidate legality in the target position.

Custom agents can use `generate()` with their own format or omit structured output
altogether. Set `decoding="uci"` or `"candidate_index"` on the final selection
request so the organizer records its selected move for the action's request ID.
General `text` calls are not interpreted as chess selections, even if analysis
mentions UCI moves. Decoding metadata does not add a response schema.

The gateway forwards the supplied response format. For llama.cpp with thinking
disabled and no tools, it can compile a strict scalar enum into an equivalent
JSON literal grammar. This generic backend adaptation uses only the participant's
schema; it does not derive legal moves. Thinking-enabled calls keep the schema
path, with native reasoning returned separately as private `reasoning_content`.

In UCI mode, one unambiguous lowercase UCI token or a JSON object with a string
`uci` field is understood. Moves inside native reasoning are excluded; multiple
moves in final-answer text make selection ambiguous. In candidate-index mode,
one zero-based integer indexes the recorded list. Completed move calls still
undergo selection and legality checks. Missing, out-of-range or unparseable
selections produce no usable move. There is no automatic legal fallback.

## Rejections and failures

HTTP 403 indicates credential/ownership errors. HTTP 409 covers stale versions,
closed admission, one-outstanding-request conflicts and other semantic
constraints. HTTP 422 is request-shape validation. Unknown request IDs currently return
409; unknown routes return 404. HTTP 413 indicates an oversized raw HTTP body.
The current gateway reports semantic request failures as 409;
tokenizer/context failures may appear in a terminal failed trace with
`failure_kind:"invalid_input"`. Correct the input, subject to your clock.

A new position can arrive while submission is in flight. Cancel the old analysis,
ignore its obsolete response, and use the fresh observation; never emit a move
for the old version. HTTP admission rejection itself is not a chess action.

Service failures trigger organizer recovery/suspension rather than an invented
fallback move. On `cancelled`, stop using the request. On `failed`, inspect
`reason` and `failure_kind`, keep logs on stderr, and respond to subsequent
organizer lifecycle events. A waiting timeout in your agent does not stop the
clock. The inference client raises failures; the provided runner retries local
`TimeoutError` while the same own-turn observation remains current and has time.
It discards expired off-turn pondering. Other exceptions remain visible. Custom
runners must implement their own clock-bounded recovery.
