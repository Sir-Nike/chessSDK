"""Optional responsive Python runner. Replace it entirely if you prefer."""

import copy
import importlib
import json
import logging
import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from .client import Cancelled, InferenceClient
from .protocol import DEFAULT_LINE_BYTES, PROTOCOL, Decision, encode


@dataclass
class Context:
    initial: dict
    position: dict
    inference: InferenceClient
    cancel: threading.Event
    received_at: float
    last_response: dict | None = None

    @property
    def own_turn(self):
        return self.position["side_to_move"] == self.initial["colour"]

    @property
    def remaining_seconds(self):
        balance = self.position["clock"][self.initial["colour"]]
        return max(0, balance - (time.monotonic() - self.received_at if self.own_turn else 0))

    def check_cancelled(self):
        if self.cancel.is_set():
            raise Cancelled()


def load_agent(spec):
    module, separator, attribute = spec.partition(":")
    if not separator:
        raise ValueError("Use MODULE:CALLABLE, for example agent:choose")
    sys.path.insert(0, os.getcwd())
    callback = getattr(importlib.import_module(module), attribute)
    if not callable(callback):
        raise ValueError("Agent callback must be callable")
    return callback


class Harness:
    def __init__(
        self, choose: Callable, *, stdin=None, stdout=None, client_factory=InferenceClient
    ):
        self.choose = choose
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self.client_factory = client_factory
        self.condition = threading.Condition()
        self.initial = self.position = self.last_response = None
        self.received_at = 0
        self.serial = 0
        self.cancel = threading.Event()
        self.closed = self.suspended = False
        self.line_bytes = DEFAULT_LINE_BYTES

    def emit(self, event):
        self.stdout.write(encode(event, self.line_bytes))
        self.stdout.flush()

    def event(self, event):
        with self.condition:
            kind = event.get("type")
            if kind == "initialize":
                if self.initial or event.get("protocol") != PROTOCOL:
                    raise ValueError("Expected one chess.v1 initialization per process")
                self.initial = event
                self.line_bytes = event["limits"]["message_bytes"]
                self.emit({"type": "ready", "protocol": PROTOCOL})
            elif kind == "position":
                if not self.initial:
                    raise ValueError("Position arrived before initialization")
                self.cancel.set()
                self.cancel = threading.Event()
                self.serial += 1
                self.position = event
                self.received_at = time.monotonic()
            elif kind == "action_response":
                self.last_response = event
            elif kind == "suspend":
                self.cancel.set()
                self.serial += 1
                self.suspended = True
            elif kind == "resume":
                self.suspended = False
                # The organizer sends a fresh position after resume; never restart
                # from a pre-barrier observation.
            elif kind in ("game_ended", "shutdown"):
                self.closed = True
                self.cancel.set()
            else:
                raise ValueError(f"Unknown organizer event: {kind}")
            self.condition.notify_all()

    def think(self):
        completed = -1
        client = None
        retry_serial, retries = -1, 0
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(
                        lambda previous=completed: self.closed
                        or (
                            self.initial is not None
                            and self.position is not None
                            and not self.suspended
                            and not self.cancel.is_set()
                            and self.serial != previous
                        )
                    )
                    if self.closed:
                        return
                    serial, cancel = self.serial, self.cancel
                    if self.position["status"] != "running":
                        completed = serial
                        continue
                    if client is None:
                        client = self.client_factory()
                    context = Context(
                        copy.deepcopy(self.initial),
                        copy.deepcopy(self.position),
                        client,
                        cancel,
                        self.received_at,
                        copy.deepcopy(self.last_response),
                    )
                if not context.own_turn and not context.initial.get("harness_config", {}).get(
                    "ponder", False
                ):
                    completed = serial
                    continue
                try:
                    decision = self.choose(context)
                    context.check_cancelled()
                    with self.condition:
                        if (
                            not self.closed
                            and not self.suspended
                            and self.serial == serial
                            and context.own_turn
                        ):
                            if not isinstance(decision, Decision):
                                raise TypeError(
                                    "Callback must return Decision; a custom runner may use another internal contract"
                                )
                            self.emit(decision.action(context.position))
                except Cancelled:
                    pass
                except TimeoutError:
                    with self.condition:
                        if (not self.closed and not self.suspended and self.serial == serial
                                and context.own_turn and context.remaining_seconds > 0.25):
                            retries = retries + 1 if retry_serial == serial else 1
                            retry_serial = serial
                            logging.getLogger(__name__).info("Inference deadline expired; retrying current turn")
                            self.condition.wait(timeout=min(1.0, 0.1 * 2 ** min(retries - 1, 4),
                                                            max(0, context.remaining_seconds - 0.2)))
                            continue
                except Exception:
                    traceback.print_exc(file=sys.stderr)
                completed = serial
        finally:
            if client:
                client.close()

    def run(self):
        worker = threading.Thread(target=self.think, name="agent-analysis", daemon=True)
        worker.start()
        try:
            while not self.closed:
                line = self.stdin.readline(self.line_bytes + 1)
                if not line:
                    break
                if len(line) > self.line_bytes or not line.endswith(b"\n"):
                    raise ValueError("Incomplete or oversized organizer line")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Organizer event must be a JSON object")
                self.event(event)
        finally:
            with self.condition:
                self.closed = True
                self.cancel.set()
                self.condition.notify_all()
            worker.join(timeout=2)
