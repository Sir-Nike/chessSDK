import threading
from types import SimpleNamespace

import pytest

from agent import choose
from chess_sdk.check import MockGateway, initial


def test_checker_enforces_legal_subset_and_accepts_bounded_thinking():
    gateway = MockGateway("white")
    body = {
        "game_id": "local-game", "attempt_id": "local-attempt", "position_version": 0,
        "messages": [{"role": "user", "content": "Select"}], "max_tokens": 128,
        "decoding": "uci", "reasoning_effort": "low", "reasoning_budget_tokens": 96, "candidates": ["e2e4"],
    }
    with pytest.raises(ValueError, match="Candidates must be legal"):
        gateway.submit(body | {"candidates": ["g8f6"]})
    with pytest.raises(ValueError, match="Invalid reasoning budget"):
        gateway.submit(body | {"reasoning_budget_tokens": -1})
    rid = gateway.submit(body)["request_id"]
    assert gateway.traces[rid]["selected_move"] == "e2e4"


@pytest.mark.parametrize("supported,cap,expected", [(True, None, 96), (True, 128, 96), (True, 512, 96), (True, 16, 0), (False, 128, None)])
def test_starter_negotiates_reasoning_and_leaves_answer_headroom(supported, cap, expected):
    class Recorder:
        def generate(self, position, messages, **controls):
            self.controls = controls
            return {"status": "completed", "id": "r", "selected_move": "e2e4"}

    initialize = initial("white")
    initialize["limits"]["output_tokens"] = cap
    if not supported:
        initialize["inference"].pop("reasoning_efforts")
    recorder = Recorder()
    ctx = SimpleNamespace(initial=initialize, position=MockGateway("white").observation(),
                          last_response=None, own_turn=True, remaining_seconds=100,
                          cancel=threading.Event(), inference=recorder)
    assert choose(ctx).uci == "e2e4"
    assert recorder.controls["response_format"]["json_schema"] == {
        "name": "chess_move_selection", "strict": True,
        "schema": {"type": "string", "enum": ctx.position["legal_moves"]},
    }
    assert recorder.controls["decoding"] == "uci"
    assert recorder.controls["max_tokens"] == (min(512, cap) if cap is not None else 512)
    if supported:
        assert recorder.controls["reasoning_budget_tokens"] == expected
        assert "reasoning_effort" not in recorder.controls
    else:
        assert "reasoning_effort" not in recorder.controls
        assert "reasoning_budget_tokens" not in recorder.controls
