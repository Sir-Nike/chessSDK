"""Queued organizer inference, over the entry-scoped Unix HTTP socket."""

import contextlib
import logging
import math
import os
import re
import threading
import time

import httpx


def move_response_format(moves, *, decoding="uci"):
    """Recommended final-answer format: a strict enum of legal moves or indices."""
    if not moves or any(not isinstance(move, str) or not re.fullmatch(r"[a-h][1-8][a-h][1-8][qrbn]?", move) for move in moves):
        raise ValueError("Supply a nonempty list of legal UCI moves")
    if decoding == "uci":
        schema = {"type": "string", "enum": list(dict.fromkeys(moves))}
    elif decoding == "candidate_index":
        schema = {"type": "integer", "enum": list(range(len(moves)))}
    else:
        raise ValueError("Move decoding must be uci or candidate_index")
    return {
        "type": "json_schema",
        "json_schema": {"name": "chess_move_selection", "strict": True, "schema": schema},
    }


def _move_controls(position, candidates, decoding, legal_moves, controls):
    if "response_format" in controls:
        raise ValueError("generate_move supplies its legal enum; use generate for custom formats")
    if controls.get("tools"):
        raise ValueError("Use generate for tool rounds before the final move selection")
    if legal_moves is None:
        if controls.get("target_fen") and controls["target_fen"] != position.get("fen"):
            raise ValueError("Supply legal_moves for a hypothetical target_fen")
        legal_moves = position["legal_moves"]
    if candidates and any(move not in legal_moves for move in candidates):
        raise ValueError("Candidates must be legal in the target position")
    if decoding == "candidate_index" and not candidates:
        raise ValueError("Candidate decoding requires an ordered candidates list")
    choices = candidates or legal_moves
    return {
        **controls, "decoding": decoding, "candidates": candidates or [],
        "response_format": move_response_format(choices, decoding=decoding),
    }


class Cancelled(Exception):
    """Analysis belongs to an obsolete observation or execution barrier."""


class InferenceFailure(Exception):
    def __init__(self, trace):
        self.trace = trace
        super().__init__(f"Inference {trace['status']}: {trace.get('reason', 'no reason')}")


class InferenceClient:
    def __init__(self, token=None, socket=None, url=None, *, transport=None, timeout=3):
        token = token or os.environ["CHESS_INFERENCE_TOKEN"]
        socket = socket or os.environ.get("CHESS_INFERENCE_SOCKET")
        self.http = httpx.Client(
            base_url=url or os.environ.get("CHESS_INFERENCE_URL", "http://gateway"),
            transport=transport or (httpx.HTTPTransport(uds=socket) if socket else None),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            trust_env=False,
        )

    @staticmethod
    def _check(response):
        if response.is_error:
            try:
                detail = response.json().get("detail", "No API reason")
            except (ValueError, AttributeError):
                detail = "Non-JSON API error"
            if response.status_code == 409 and detail in (
                "Stale observation", "Inference admission closed for this attempt",
                "Stale observation or closed admission",
            ):
                raise Cancelled()
            logging.getLogger(__name__).error(
                "Inference API rejection status=%s reason=%s", response.status_code, str(detail)[:2048]
            )
        response.raise_for_status()

    def submit(self, position, messages, *, request_timeout=None, **controls):
        reserved = {"game_id", "attempt_id", "position_version", "messages", "model", "adapter"}
        if reserved.intersection(controls):
            raise ValueError("Identity/model fields cannot be overridden in generation controls")
        response = self.http.post(
            "/v1/inference",
            **({"timeout": request_timeout} if request_timeout is not None else {}),
            json={
                "game_id": position["game_id"],
                "attempt_id": position["attempt_id"],
                "position_version": position["position_version"],
                "messages": messages,
                **controls,
            },
        )
        self._check(response)
        return response.json()["request_id"]

    def read(self, request_id, *, request_timeout=None):
        response = self.http.get(f"/v1/inference/{request_id}",
                                 **({"timeout": request_timeout} if request_timeout is not None else {}))
        self._check(response)
        return response.json()

    def submit_move(self, position, messages, *, candidates=None, decoding="uci", legal_moves=None, **controls):
        """Submit a final move with the SDK's recommended strict legal enum."""
        return self.submit(position, messages, **_move_controls(
            position, candidates, decoding, legal_moves, controls,
        ))

    def generate_move(self, position, messages, *, candidates=None, decoding="uci", legal_moves=None, **controls):
        """Select a legal final move. General reasoning/tool rounds use generate()."""
        return self.generate(position, messages, **_move_controls(
            position, candidates, decoding, legal_moves, controls,
        ))

    def cancel(self, request_id):
        response = self.http.delete(f"/v1/inference/{request_id}", timeout=1)
        self._check(response)
        return response.json()

    def wait(self, request_id, *, cancel=None, timeout=120, poll=0.03):
        cancel = cancel or threading.Event()
        deadline = time.monotonic() + timeout
        while True:
            if cancel.is_set():
                with contextlib.suppress(httpx.HTTPError, Cancelled):
                    self.cancel(request_id)
                raise Cancelled()
            if time.monotonic() >= deadline:
                with contextlib.suppress(httpx.HTTPError, Cancelled):
                    self.cancel(request_id)
                raise TimeoutError(
                    "Inference budget expired; the chess clock remains authoritative"
                )
            try:
                trace = self.read(request_id, request_timeout=max(0.001, min(3, deadline - time.monotonic())))
            except httpx.TimeoutException as exc:
                with contextlib.suppress(httpx.HTTPError, Cancelled):
                    self.cancel(request_id)
                if cancel.is_set():
                    raise Cancelled() from exc
                raise TimeoutError("Inference polling exceeded the local wait deadline") from exc
            # Polling HTTP can itself consume the remaining budget.
            if time.monotonic() >= deadline:
                with contextlib.suppress(httpx.HTTPError, Cancelled):
                    self.cancel(request_id)
                raise TimeoutError(
                    "Inference budget expired; the chess clock remains authoritative"
                )
            if trace["status"] == "completed":
                if cancel.is_set():
                    raise Cancelled()
                return trace
            if trace["status"] == "cancelled":
                raise Cancelled()
            if trace["status"] == "failed":
                raise InferenceFailure(trace)
            cancel.wait(min(poll, max(0, deadline - time.monotonic())))

    def generate(self, position, messages, *, cancel=None, timeout=120, **controls):
        """General model call; formats and tools are participant-controlled."""
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if not math.isfinite(timeout) or timeout <= 0:
            raise TimeoutError("Inference budget expired before admission")
        started = time.monotonic()
        try:
            request_id = self.submit(position, messages, request_timeout=max(0.001, min(3, timeout)), **controls)
        except httpx.TimeoutException as exc:
            if cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            raise TimeoutError("Inference admission exceeded the local wait deadline") from exc
        except httpx.HTTPStatusError as exc:
            # A new observation may arrive while admission is in flight.
            if exc.response.status_code == 409 and cancel is not None and cancel.is_set():
                raise Cancelled() from exc
            raise
        try:
            return self.wait(
                request_id, cancel=cancel, timeout=timeout - (time.monotonic() - started)
            )
        except BaseException:
            with contextlib.suppress(httpx.HTTPError, Cancelled):
                self.cancel(request_id)
            raise

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
