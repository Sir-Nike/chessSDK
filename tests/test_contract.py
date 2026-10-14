import json
import re
import sys
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from chess_sdk.check import MockGateway, check_harness, initial
from chess_sdk.protocol import Decision, encode, move_action, resign_action

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(files("chess_sdk").joinpath("schemas/chess-v1.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def test_schema_and_documented_envelopes():
    Draft202012Validator.check_schema(SCHEMA)
    for name in ("protocol.md", "inference.md"):
        text = (ROOT / "docs" / name).read_text()
        for block in re.findall(r"```json\n(.*?)\n```", text, flags=re.S):
            value = json.loads(block)
            if "type" in value:
                VALIDATOR.validate(value)
            else:
                definition = "inference_trace" if "id" in value else "inference_request"
                Draft202012Validator(
                    {"$ref": "#/$defs/" + definition, "$defs": SCHEMA["$defs"]}
                ).validate(value)
    VALIDATOR.validate(initial("white"))
    VALIDATOR.validate(MockGateway("white").observation())


@pytest.mark.parametrize("colour", ["white", "black"])
def test_starter_lifecycle_and_current_evidence(colour):
    report = check_harness([sys.executable, "-m", "chess_sdk", "run"], colour=colour, timeout=5)
    assert report["passed"], report
    assert report["accepted_moves"] == 2
    assert not report["stderr"], report["stderr"]


def test_arbitrary_executable_does_not_accept_banner():
    report = check_harness(
        [sys.executable, "-c", 'print("Agent is ready!", flush=True)'], timeout=2
    )
    assert not report["passed"]
    assert "JSONDecodeError" in report["error"]


def test_forged_evidence_rejected(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text("""import json,sys
for line in sys.stdin:
    p=json.loads(line)
    if p['type']=='initialize':
        colour=p['colour']; print(json.dumps({'type':'ready','protocol':'chess.v1'}),flush=True)
    elif p['type']=='position' and p['side_to_move']==colour:
        print(json.dumps({'type':'action','action':'move','action_id':'forged',
            'game_id':p['game_id'],'attempt_id':p['attempt_id'],
            'position_version':p['position_version'],'uci':p['legal_moves'][0],
            'evidence':{'request_id':'invented'}}),flush=True)
""")
    report = check_harness([sys.executable, str(script)], timeout=2)
    assert not report["passed"]
    assert "model evidence" in report["error"]


def test_undecodable_model_output_has_no_legal_fallback():
    decision = Decision.from_trace({"id": "r", "status": "completed", "selected_move": None})
    assert decision.uci == "invalid"
    with pytest.raises(ValueError):
        Decision.from_trace({"id": "r", "status": "running"})


def test_utf8_line_budget_and_nonfinite_numbers():
    assert len(encode({"text": "♟"})) > len('{"text":"♟"}\n')
    with pytest.raises(ValueError):
        encode({"text": "♟"}, max_bytes=14)
    with pytest.raises(ValueError):
        encode({"score": float("nan")})


def test_move_resignation_and_control_schemas():
    position = MockGateway("black").observation()
    VALIDATOR.validate(move_action(position, "e2e4", "request-1"))
    VALIDATOR.validate(resign_action(position))
    wrong = move_action(position, "e2e4", "request-1")
    wrong.pop("evidence")
    assert not VALIDATOR.is_valid(wrong)
    request = {
        "game_id": "g",
        "attempt_id": "a",
        "position_version": 0,
        "messages": [{"role": "user", "content": "Select one index"}],
        "decoding": "candidate_index",
        "candidates": ["e2e4", "d2d4"],
    }
    validator = Draft202012Validator(
        {"$ref": "#/$defs/inference_request", "$defs": SCHEMA["$defs"]}
    )
    validator.validate(request)
    assert not validator.is_valid(request | {"model": "unapproved"})
    assert not validator.is_valid(request | {"candidates": []})

    general = request | {
        "decoding": "text", "candidates": [],
        "tools": [{"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}],
        "messages": [
            {"role": "user", "content": "Inspect the position"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function", "function": {"name": "inspect", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "call-1", "content": "Board inspected"},
        ],
    }
    validator.validate(general)
    assert not validator.is_valid(general | {"messages": [{"role": "tool", "content": "Missing call ID"}]})
