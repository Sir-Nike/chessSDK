import io
import json
import threading

from chess_sdk import Decision
from chess_sdk.runner import Harness


class Client:
    def close(self):
        pass


def test_current_turn_retries_timeouts_without_waiting_for_another_observation():
    calls = []
    finished = threading.Event()

    def choose(context):
        calls.append(context.remaining_seconds)
        if len(calls) <= 2:
            raise TimeoutError("Transient local inference deadline")
        return Decision(uci="e2e4", request_id="completed-model-request")

    class Output(io.BytesIO):
        def write(self, data):
            result = super().write(data)
            if json.loads(data)["type"] == "action":
                finished.set()
            return result

    output = Output()
    harness = Harness(choose, stdout=output, client_factory=Client)
    harness.event({"type": "initialize", "protocol": "chess.v1", "colour": "white",
                   "limits": {"message_bytes": 65536}, "harness_config": {}})
    harness.event({"type": "position", "game_id": "g", "attempt_id": "a",
                   "position_version": 0, "side_to_move": "white", "status": "running",
                   "clock": {"white": 3, "black": 3}})
    worker = threading.Thread(target=harness.think)
    worker.start()
    try:
        assert finished.wait(2)
    finally:
        harness.event({"type": "shutdown"})
        worker.join(2)
    assert not worker.is_alive()
    assert len(calls) == 3 and calls[-1] < calls[0]
    actions = [json.loads(line) for line in output.getvalue().splitlines()]
    assert actions[-1]["evidence"] == {"request_id": "completed-model-request"}


def test_off_turn_timeout_does_not_restart_pondering_forever():
    called = threading.Event()
    calls = []

    def choose(context):
        calls.append(context.own_turn)
        called.set()
        raise TimeoutError("Optional pondering expired")

    harness = Harness(choose, stdout=io.BytesIO(), client_factory=Client)
    harness.event({"type": "initialize", "protocol": "chess.v1", "colour": "black",
                   "limits": {"message_bytes": 65536}, "harness_config": {"ponder": True}})
    harness.event({"type": "position", "side_to_move": "white", "status": "running",
                   "clock": {"white": 3, "black": 3}})
    worker = threading.Thread(target=harness.think)
    worker.start()
    assert called.wait(2)
    harness.event({"type": "shutdown"})
    worker.join(2)
    assert calls == [False] and not worker.is_alive()
