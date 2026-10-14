# Process protocol: chess.v1

Run one long-lived, noninteractive process per agent per game. The organizer
writes UTF-8 newline-delimited JSON objects to stdin; your process writes JSON
objects to stdout. Flush each line. **Only protocol messages belong on stdout.**
Send diagnostics to stderr. No Markdown fences, banners, TTY UI or progress bars.
Each line, including its newline, must fit `limits.message_bytes` (development:
65,536 bytes). A JSON object may be formatted for reading below; on the wire it
must occupy a single line. No ready messages or acknowledgements for positions.

Unknown additional fields in incoming objects may be ignored. The version is
`chess.v1`; fail clearly on an unsupported version. Do not use operator/team REST
endpoints to play a game. The stdio contract and scoped inference API are the two
participant interfaces.

## Initialization and readiness

The first input is `initialize`, before chess clocks start:

```json
{"type":"initialize","protocol":"chess.v1","game_id":"game-1","attempt_id":"attempt-1","entry_id":"entry-white","colour":"white","harness_config":{"ponder":false},"limits":{"message_bytes":65536,"output_tokens":null,"context_tokens":4096,"invalid_limit":3},"inference":{"transport":"http-unix","path":"/v1/inference"}}
```

Reply once, promptly:

```json
{"type":"ready","protocol":"chess.v1"}
```

`entry_id` identifies you, `colour` is `white` or `black`. `harness_config` is your
captured manifest configuration; its keys are yours to define. Transport and execution limits are set by
the organizer. `limits.output_tokens: null` means there is no organizer output
ceiling; participants choose their own generation and reasoning budgets.
`limits.context_tokens` reports the captured model's physical context window. Socket path and credential arrive in the environment, not this
message. The development startup allowance is 15 seconds. Your process starts
fresh for another game, including Armageddon.

`inference` may also contain `reasoning_efforts` (supported values) and
`answer_token_reserve` (suggested headroom, currently 32). An empty or absent effort list means omit
reasoning controls. These describe the captured entry's backend, not permission
to select another model. See [structured moves and reasoning](reasoning.md).

## Full position observations

Both agents receive `position` after startup and every accepted move. The agent
whose action is rejected also receives a fresh observation, potentially with the
**same** position version. Treat each observation as an analysis boundary.

```json
{"type":"position","protocol":"chess.v1","game_id":"game-1","attempt_id":"attempt-1","position_version":0,"decision_id":"attempt-1:0","initial_fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","moves":[],"fen":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1","side_to_move":"white","white":"entry-white","black":"entry-black","status":"running","clock":{"white":600.0,"black":600.0},"increment":0.0,"result":null,"reason":null,"barrier":0,"legal_moves":["g1h3","g1f3","b1c3","b1a3","h2h3","g2g3","f2f3","e2e3","d2d3","c2c3","b2b3","a2a3","h2h4","g2g4","f2f4","e2e4","d2d4","c2c4","b2b4","a2a4"]}
```

| Field | Meaning |
|---|---|
| `game_id`, `attempt_id` | Match game identity and specific execution attempt |
| `position_version` | Integer increased after each accepted move; copy it into requests/actions |
| `decision_id` | Observation label, not an action ID or an inference request ID |
| `initial_fen`, `moves` | Starting board and complete UCI history; preserve repetition context |
| `fen`, `legal_moves` | Authoritative current board and legal UCI moves |
| `side_to_move` | Whose chess clock is running; only that player may act |
| `white`, `black` | Entry IDs assigned to each color |
| `status` | `preparing`, `running`, `suspended`, `ended`, or `invalid` |
| `clock` | Remaining seconds at observation time, not a fresh per-turn allowance |
| `increment` | Seconds credited after a legal move |
| `result`, `reason` | Nullable terminal result and explanation |
| `barrier` | Execution generation; increments when organizer suspends the game |

During a turn, subtract elapsed time from your observed clock estimate. The
organizer's receipt time is authoritative. Full history grows over a game; include
only the information you need in prompts to stay inside the model's token limit.

## Move and resignation output

After completed [inference](inference.md), emit an action on your turn:

```json
{"type":"action","game_id":"game-1","attempt_id":"attempt-1","position_version":0,"action_id":"unique-action-1","action":"move","uci":"e2e4","evidence":{"request_id":"request-1"}}
```

`action_id` is your unique string of 1–128 characters. UCI moves use lowercase
coordinates: castling is a king move; promotion includes `q`, `r`, `b` or `n`.
No SAN, free-form explanations, scores or tool payloads belong in the envelope.
The request must belong to your entry and attempt, be completed, target the
current FEN, match the current barrier and decode to exactly your submitted move.
Supplying an arbitrary ID or attaching a model's unrelated output fails evidence.

To resign on your turn, emit the same identities with `action:"resign"` and omit
`uci` and `evidence`. There are no draw-offer or claim actions in this version.

## Action responses and correction

The organizer acknowledges your action and broadcasts the new position after an
accepted move:

```json
{"type":"action_response","action_id":"unique-action-1","accepted":true,"reason":"legal_move","position_version":1,"status":"running","result":null}
```

A rejected action is acknowledged, followed by a fresh position for your agent:

```json
{"type":"action_response","action_id":"unique-action-2","accepted":false,"reason":"illegal_move","position_version":1,"status":"running","result":null,"invalid_attempts":1}
```

Reasons include `illegal_move`, `unsupported_action`, `invalid_action_id`,
`malformed_inference_evidence`, `missing_completed_inference`,
`wrong_inference_identity`, `inference_position_mismatch`, `llm_selection_mismatch`, `stale_position`, `wrong_attempt`, `not_your_turn`,
`action_id_reused`, and receipt/clock boundary reasons. Treat `reason` as an
extensible string; consult `accepted` and the next observation rather than rely
on a closed list. `status`, `result`, `invalid_attempts` may be absent on some
responses. Invalid attempts are cumulative; the clock keeps running.

Use a **new action ID** for a corrected decision, even on the same version. An
identical retransmission with the same ID returns its prior response; changing
an old ID's payload is rejected. Cancel obsolete inference before a new request.

## Suspension, resume and shutdown

The organizer freezes both process trees or containers and sends:

```json
{"type":"suspend","reason":"organizer_inference_failure","barrier":1}
```

The message may only be read after physical unpause. Discard pre-suspension work.
The organizer cancels outstanding requests and freezes both clocks. On resume:

```json
{"type":"resume","barrier":1}
```

Wait for the following **fresh `position`**. Neither `resume` nor your old
observation authorizes a move. Old buffered actions may be discarded and old
inference evidence cannot cross a barrier, even if the FEN is unchanged.

`game_ended` carries the same full observation fields as `position`, with
`type:"game_ended"`, terminal status/result/reason. Stop all analysis. The
organizer then sends `{"type":"shutdown"}`. Exit promptly and also handle stdin
EOF. Development shutdown allowance is three seconds. An invalid organizer
attempt may close without a `game_ended` notification; EOF remains terminal.

## Machine-readable definitions

[`chess-v1.json`](../src/chess_sdk/schemas/chess-v1.json) is JSON Schema draft
2020-12. Its root validates stdio envelopes; `$defs.inference_request` and
`$defs.inference_trace` describe the HTTP bodies. Structural validation cannot
establish move legality, evidence ownership or clock/barrier freshness: the
organizer evaluator remains authoritative for those checks.
