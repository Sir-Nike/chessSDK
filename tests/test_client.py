import json
import threading

import httpx
import pytest

from chess_sdk import Cancelled, InferenceClient, InferenceFailure, move_response_format

POSITION = {"game_id": "g", "attempt_id": "a", "position_version": 7}
MESSAGES = [{"role": "user", "content": "Choose one move"}]


def client(handler):
    return InferenceClient(token="private-test-token", transport=httpx.MockTransport(handler))


def test_entry_identity_candidate_controls_and_completed_result():
    calls = []

    def handler(request):
        assert request.headers["authorization"] == "Bearer private-test-token"
        calls.append(request.method)
        if request.method == "POST":
            body = json.loads(request.content)
            assert body == POSITION | {
                "messages": MESSAGES,
                "decoding": "candidate_index",
                "candidates": ["e2e4", "d2d4"],
            }
            return httpx.Response(202, json={"request_id": "r"})
        return httpx.Response(200, json={"id": "r", "status": "completed", "selected_move": "d2d4"})

    with client(handler) as inference:
        trace = inference.generate(
            POSITION, MESSAGES, decoding="candidate_index", candidates=["e2e4", "d2d4"]
        )
        assert trace["selected_move"] == "d2d4"
        with pytest.raises(ValueError):
            inference.submit(POSITION, MESSAGES, model="different-model")
    assert calls == ["POST", "GET"]


@pytest.mark.parametrize("interrupt", ["cancel", "timeout", "failed"])
def test_unfinished_requests_are_cancelled_without_fallback(interrupt):
    methods = []
    cancel = threading.Event()

    def handler(request):
        methods.append(request.method)
        if request.method == "POST":
            if interrupt == "cancel":
                cancel.set()
            return httpx.Response(202, json={"request_id": "r"})
        if request.method == "DELETE":
            return httpx.Response(200, json={"status": "cancelled"})
        return httpx.Response(200, json={"id": "r", "status": "failed", "reason": "invalid_input"})

    expected = {"cancel": Cancelled, "timeout": TimeoutError, "failed": InferenceFailure}[interrupt]
    with client(handler) as inference, pytest.raises(expected):
        inference.generate(
            POSITION, MESSAGES, cancel=cancel, timeout=0 if interrupt == "timeout" else 1
        )
    assert ("DELETE" in methods) is (interrupt != "timeout")


def test_stale_admission_race_is_cancelled_but_real_conflict_is_visible():
    cancel = threading.Event()

    def handler(request):
        cancel.set()
        return httpx.Response(409, json={"detail": "Stale observation"})

    with client(handler) as inference, pytest.raises(Cancelled):
        inference.generate(POSITION, MESSAGES, cancel=cancel)
    with client(
        lambda _: httpx.Response(409, json={"detail": "One outstanding request"})
    ) as inference:
        with pytest.raises(httpx.HTTPStatusError):
            inference.generate(POSITION, MESSAGES, cancel=threading.Event())


def test_polling_latency_cannot_return_output_after_budget():
    import time

    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "GET":
            time.sleep(0.03)
            return httpx.Response(200, json={"id": "request", "status": "completed"})
        return httpx.Response(200, json={"status": "cancelled"})

    with InferenceClient(token="test", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(TimeoutError):
            client.wait("request", timeout=0.01)
    assert calls == ["GET", "DELETE"]


def test_generation_budget_includes_admission_latency():
    import time

    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "POST":
            time.sleep(0.03)
            return httpx.Response(202, json={"request_id": "request"})
        return httpx.Response(200, json={"status": "cancelled"})

    position = {"game_id": "g", "attempt_id": "a", "position_version": 0}
    with InferenceClient(token="test", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(TimeoutError):
            client.generate(position, [{"role": "user", "content": "move"}], timeout=0.01)
    assert "GET" not in calls
    assert "DELETE" in calls


def test_stale_admission_before_notification_is_cancellation_without_error_log(caplog):
    with client(lambda _: httpx.Response(409, json={"detail": "Stale observation"})) as inference:
        with pytest.raises(Cancelled):
            inference.generate(POSITION, MESSAGES, cancel=threading.Event())
    assert not caplog.records


def test_transport_poll_timeout_is_recoverable_and_cancels_request():
    methods = []
    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            assert request.extensions["timeout"]["read"] <= 0.2
            raise httpx.ReadTimeout("Delayed socket", request=request)
        return httpx.Response(200, json={"status": "cancelled"})
    with client(handler) as inference, pytest.raises(TimeoutError):
        inference.wait("r", timeout=0.2)
    assert methods == ["GET", "DELETE"]


@pytest.mark.parametrize("mode,candidates,expected", [
    ("uci", None, {"type": "string", "enum": ["e2e4", "d2d4", "g1f3"]}),
    ("uci", ["d2d4"], {"type": "string", "enum": ["d2d4"]}),
    ("candidate_index", ["d2d4", "e2e4"], {"type": "integer", "enum": [0, 1]}),
])
def test_sdk_final_move_supplies_strict_enum(mode, candidates, expected):
    position = POSITION | {"legal_moves": ["e2e4", "d2d4", "g1f3"]}

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["response_format"] == {"type": "json_schema", "json_schema": {
                "name": "chess_move_selection", "strict": True, "schema": expected,
            }}
            assert body["decoding"] == mode
            assert body["candidates"] == (candidates or [])
            return httpx.Response(202, json={"request_id": "r"})
        return httpx.Response(200, json={"id": "r", "status": "completed", "selected_move": "d2d4"})

    with client(handler) as inference:
        assert inference.generate_move(position, MESSAGES, decoding=mode, candidates=candidates)["selected_move"] == "d2d4"


def test_general_calls_pass_tools_and_custom_formats_without_move_constraints():
    tools = [{"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}]
    format = {"type": "json_schema", "json_schema": {"name": "plan", "strict": True, "schema": {"type": "object"}}}

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert "decoding" not in body and "candidates" not in body
            assert body["tools"] == tools
            assert body["response_format"] == format
            assert body["tool_choice"] == "auto"
            return httpx.Response(202, json={"request_id": "r"})
        return httpx.Response(200, json={"id": "r", "status": "completed", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function", "function": {"name": "inspect", "arguments": "{}"},
            }],
        }})

    with client(handler) as inference:
        trace = inference.generate(POSITION, MESSAGES, tools=tools, tool_choice="auto", response_format=format)
        assert trace["message"]["tool_calls"][0]["function"]["name"] == "inspect"


def test_sdk_move_helper_rejects_bad_candidates_and_requires_hypothetical_legal_moves():
    with client(lambda _: pytest.fail("Invalid move request must not reach the gateway")) as inference:
        position = POSITION | {"fen": "current", "legal_moves": ["e2e4"]}
        with pytest.raises(ValueError, match="Candidates must be legal"):
            inference.generate_move(position, MESSAGES, candidates=["g8f6"])
        with pytest.raises(ValueError, match="ordered candidates"):
            inference.generate_move(position, MESSAGES, decoding="candidate_index")
        with pytest.raises(ValueError, match="hypothetical"):
            inference.generate_move(position, MESSAGES, target_fen="future")
    with pytest.raises(ValueError, match="nonempty"):
        move_response_format([])
