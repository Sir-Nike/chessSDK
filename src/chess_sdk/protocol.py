"""Wire-format helpers; no chess strategy or agent framework is imposed."""

import json
import uuid
from dataclasses import dataclass

PROTOCOL = "chess.v1"
DEFAULT_LINE_BYTES = 65536


def encode(event, max_bytes=DEFAULT_LINE_BYTES):
    data = (
        json.dumps(event, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    if len(data) > max_bytes:
        raise ValueError("Protocol line exceeds the organizer's byte limit")
    return data


def move_action(position, uci, request_id, action_id=None):
    return {
        "type": "action",
        "game_id": position["game_id"],
        "attempt_id": position["attempt_id"],
        "position_version": position["position_version"],
        "action_id": action_id or uuid.uuid4().hex,
        "action": "move",
        "uci": uci,
        "evidence": {"request_id": request_id},
    }


def resign_action(position, action_id=None):
    return {
        "type": "action",
        "game_id": position["game_id"],
        "attempt_id": position["attempt_id"],
        "position_version": position["position_version"],
        "action_id": action_id or uuid.uuid4().hex,
        "action": "resign",
    }


@dataclass(frozen=True)
class Decision:
    """A move from completed organizer inference, or an explicit resignation."""

    uci: str | None = None
    request_id: str | None = None
    resign: bool = False

    @classmethod
    def from_trace(cls, trace):
        if trace["status"] != "completed":
            raise ValueError("A move must reference completed inference")
        # Ambiguous model output goes through organizer correction. Never replace
        # it with an SDK-selected legal move.
        return cls(uci=trace.get("selected_move") or "invalid", request_id=trace["id"])

    def action(self, position):
        if self.resign:
            return resign_action(position)
        if not isinstance(self.uci, str) or not isinstance(self.request_id, str):
            raise ValueError("Return a Decision with a move and inference request ID")
        return move_action(position, self.uci, self.request_id)
