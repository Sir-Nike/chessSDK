# Build with any framework

The contract belongs to your **process**, not a Python class. You can replace
`agent.py`, all SDK helpers, the Dockerfile and the runtime language. Your process
must read `chess.v1` JSON lines, emit the prescribed JSON lines, call the scoped
inference gateway and attach matching model evidence to its moves. See
[the process contract](protocol.md) and [the inference contract](inference.md).
The SDK recommends [a strict legal-move enum for the final answer](reasoning.md).
General inference calls support planning, custom response formats, and tool loops.

LangChain, LangGraph, Pi harnesses and custom agents can manage tools, planning and
memory internally and use the scoped gateway for all their model calls. It is a
queued API with submit/poll/cancel; it has no OpenAI-compatible chat-completions
route. Implement a provider adapter that maps framework messages, tool definitions,
and response formats onto this contract. The gateway binds each call to your
captured model and adapter. A built-in LangChain provider is not included.

`generate()` defaults to general inference. It accepts `tools`, `tool_choice`,
`parallel_tool_calls`, and a custom `response_format`. Assistant responses are
available in `trace["message"]`, including native `tool_calls`. Append that
message, execute the requested tools in your harness, append `tool` messages with
matching `tool_call_id`, and call again. The model/backend must support your tool
format. The SDK does not execute functions or impose a tool iteration limit.

For example, a cancellable tool loop can end in the SDK's recommended final move:

```python
import json
from chess_sdk import Decision


def choose(context):
    messages = [{"role": "user", "content": json.dumps(context.position)}]
    tools = [{"type": "function", "function": {
        "name": "inspect_position",
        "description": "Inspect the current board using a local rules tool",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }}]
    # This example chooses its own iteration budget; the gateway imposes none.
    for _ in range(8):
        context.check_cancelled()
        trace = context.inference.generate(
            context.position, messages, tools=tools, tool_choice="auto",
            cancel=context.cancel,
            timeout=max(0.05, context.remaining_seconds - 0.5),
        )
        assistant = trace["message"]
        calls = assistant.get("tool_calls", [])
        if assistant.get("content") is not None or calls:
            messages.append(assistant)
        if not calls:
            break
        for call in calls:
            context.check_cancelled()
            # Validate the function name and parsed arguments before dispatch.
            if call["function"]["name"] != "inspect_position":
                raise ValueError("Unknown function")
            arguments = json.loads(call["function"]["arguments"])
            if arguments != {}:
                raise ValueError("inspect_position takes no arguments")
            result = {"fen": context.position["fen"], "legal_moves": context.position["legal_moves"]}
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result)})
    messages.append({"role": "user", "content": "Choose the best legal move. Reply as a JSON UCI string."})
    final = context.inference.generate_move(
        context.position, messages, cancel=context.cancel,
        timeout=max(0.05, context.remaining_seconds - 0.5),
    )
    return Decision.from_trace(final)
```

Keep framework progress displays, tracing and logs off stdout. Disable remote
telemetry and services for offline evaluation. Pi and interactive harnesses need
a noninteractive process wrapper and a provider adapter for the queued API.

## Optional Python helpers

The supplied entry point is `python -m chess_sdk run --agent agent:choose`.
`choose(context)` runs in a worker thread while the main thread reads events.
It is a synchronous callback; an async framework can be invoked from that worker,
or you may replace the runner with your own event loop.

| Context member | Meaning |
|---|---|
| `initial` | Initialization envelope, own color, limits and captured harness config |
| `position` | Snapshot of the full current observation |
| `inference` | Scoped `InferenceClient` with general calls and optional submit_move/generate_move helpers |
| `cancel` | Thread event set when this analysis becomes obsolete |
| `check_cancelled()` | Raises `Cancelled` for obsolete work |
| `own_turn` | Whether this snapshot is your turn |
| `remaining_seconds` | Conservative local estimate of your observed clock balance |
| `last_response` | Latest action acknowledgement/rejection, or `None` |

Return `Decision.from_trace(completed_trace)` to preserve the selected move and
request ID, or `Decision(resign=True)` to resign. `Decision.from_trace` never
chooses a fallback legal move. Undecodable output produces an invalid move attempt
that the evaluator can reject and count; improve your prompt/correction logic.
A failed request raises `InferenceFailure`. Diagnose it; the stock starter does
not silently retry or select a substitute move.

`InferenceClient.generate(position, messages, cancel=..., timeout=..., **controls)`
submits once, polls to completion and cancels unfinished work when interrupted.
`generate_move()` and `submit_move()` add the recommended strict legal-move enum.
The timeout is your local waiting budget, not an extension of the chess clock.
The runner emits a decision only if its observation is still current and it is
your turn. It discards decisions computed for obsolete snapshots.

Long-running tools should regularly call `check_cancelled()` or inspect `cancel`.
Otherwise they can block new analysis and timely shutdown, even though the reader
continues receiving events. The organizer independently freezes the whole process
or container during suspension. Using the helper alone does not make an arbitrary
external subprocess cancellable; manage and terminate your own tool processes.

Module state or objects retained by your callback can hold memory for a game.
Each game starts a new process. `harness_config.ponder` enables off-turn callbacks
in the stock runner; it defaults to false. They may prepare analysis but do not
emit moves. Changing `harness_config` cannot override event clocks, model identity,
container limits or tournament rules.

## Your own process

Use any executable in the image's exec-form `ENTRYPOINT`. Keep stdin open, flush
each stdout event, and read continuously even while doing inference or tool work.
Keep action IDs unique, copy current game/attempt/version fields, and treat every
new position, suspension or game end as a cancellation boundary. After resume,
wait for the organizer's fresh position. Finish promptly on shutdown or EOF.

Install and capture framework dependencies during development. The official build
and running container cannot download them. Update the Dockerfile and vendored
assets for your language; Python's wheel preparation script is optional.
