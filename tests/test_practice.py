import httpx
import pytest

from chess_sdk import PracticeClient


def test_practice_retries_keep_creation_identity_and_settings():
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadError("lost reply")
        return httpx.Response(201, json={"id": "session"})

    with PracticeClient(token="team", transport=httpx.MockTransport(handle)) as client:
        assert client.create(colour="black", level=10)["id"] == "session"
    import json

    first, second = [json.loads(request.content) for request in requests]
    assert first == second
    assert first["time_control"] == {"white": 600, "black": 600, "increment": 0}
    assert first["colour"] == "black" and first["level"] == 10
    assert requests[0].headers["Authorization"] == "Bearer team"


def test_action_retries_keep_id_and_version():
    bodies = []

    def handle(request):
        bodies.append(request.content)
        if len(bodies) == 1:
            raise httpx.ReadTimeout("lost reply")
        return httpx.Response(200, json={"accepted": True})

    with PracticeClient(token="team", transport=httpx.MockTransport(handle)) as client:
        assert client.move({"id": "s", "position_version": 4}, "e2e4")["accepted"]
    assert bodies[0] == bodies[1]


def test_wait_returns_terminal_engine_failure_without_another_move():
    def handle(request):
        return httpx.Response(200, json={"id": "s", "engine_pending": False, "status": "invalid"})

    with PracticeClient(token="team", transport=httpx.MockTransport(handle)) as client:
        assert client.wait("s")["status"] == "invalid"


def test_authentication_errors_are_not_retried():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(403, json={"detail": "Invalid token"})

    with PracticeClient(token="team", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.history()
    assert len(calls) == 1
