"""Local protocol fixture with a fake model. Never an official validator or strength test."""

import contextlib
import copy
import hmac
import json
import os
import queue
import secrets
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import chess

from .protocol import encode

ALLOWED_CONTROLS = {
    "game_id",
    "attempt_id",
    "position_version",
    "messages",
    "max_tokens",
    "temperature",
    "top_p",
    "stop",
    "seed",
    "target_fen",
    "decoding",
    "candidates",
    "reasoning_effort",
    "reasoning_budget_tokens",
    "response_format",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
}


def initial(colour):
    return {
        "type": "initialize",
        "protocol": "chess.v1",
        "game_id": "local-game",
        "attempt_id": "local-attempt",
        "entry_id": "participant",
        "colour": colour,
        "harness_config": {"ponder": True},
        "limits": {"message_bytes": 65536, "output_tokens": None, "context_tokens": 4096, "invalid_limit": 3},
        "inference": {
            "transport": "http-unix", "path": "/v1/inference",
            "reasoning_efforts": ["none", "low", "medium", "high"],
            "answer_token_reserve": 32,
        },
    }


class MockGateway:
    def __init__(self, colour):
        self.colour = colour
        self.board = chess.Board()
        self.version = self.barrier = 0
        self.status = "running"
        self.token = secrets.token_urlsafe(24)
        self.traces = {}
        self.lock = threading.RLock()
        self.delay = 0.08
        self.requests = self.cancellations = self.pondering = 0

    def observation(self, kind="position"):
        return {
            "type": kind,
            "protocol": "chess.v1",
            "game_id": "local-game",
            "attempt_id": "local-attempt",
            "position_version": self.version,
            "decision_id": f"local-attempt:{self.version}",
            "initial_fen": chess.STARTING_FEN,
            "moves": [m.uci() for m in self.board.move_stack],
            "fen": self.board.fen(),
            "side_to_move": "white" if self.board.turn else "black",
            "white": "participant" if self.colour == "white" else "opponent",
            "black": "participant" if self.colour == "black" else "opponent",
            "status": self.status,
            "clock": {"white": 180, "black": 180},
            "increment": 2,
            "result": "1/2-1/2" if self.status == "ended" else None,
            "reason": "local_fixture_complete" if self.status == "ended" else None,
            "barrier": self.barrier,
            "legal_moves": [m.uci() for m in self.board.legal_moves],
        }

    def submit(self, body):
        if set(body) - ALLOWED_CONTROLS:
            raise ValueError(
                "Unsupported inference fields; this API is not OpenAI chat completions"
            )
        if body.get("game_id") != "local-game" or body.get("attempt_id") != "local-attempt":
            raise PermissionError("Wrong game/attempt")
        if body.get("position_version") != self.version or self.status != "running":
            raise ValueError("Stale observation or closed admission")
        if any(t["status"] in ("queued", "running") for t in self.traces.values()):
            raise ValueError("One outstanding request per agent")
        max_tokens = body.get("max_tokens")
        if not body.get("messages") or max_tokens is not None and (
            type(max_tokens) is not int or max_tokens <= 0
        ):
            raise ValueError("Invalid messages/output cap")
        board = chess.Board(body.get("target_fen") or self.board.fen())
        if not board.is_valid():
            raise ValueError("Invalid target FEN")
        candidates = body.get("candidates", [])
        decoding = body.get("decoding", "text")
        if (
            decoding not in ("text", "uci", "candidate_index")
            or decoding == "candidate_index"
            and not candidates
        ):
            raise ValueError("Unsupported decoding/missing candidates")
        moves = sorted(m.uci() for m in board.legal_moves)
        if decoding == "text" and candidates:
            raise ValueError("Candidates require move decoding")
        if (decoding != "text" and not moves) or any(candidate not in moves for candidate in candidates):
            raise ValueError("Candidates must be legal in a position with legal moves")
        effort = body.get("reasoning_effort") or (
            "high" if "reasoning_budget_tokens" in body and body["reasoning_budget_tokens"] != 0 else "none"
        )
        if effort not in ("none", "low", "medium", "high"):
            raise ValueError("Unsupported reasoning effort")
        budget = body.get("reasoning_budget_tokens")
        if budget is not None and (
            type(budget) is not int or budget < 0 or effort == "none" and budget
        ):
            raise ValueError("Invalid reasoning budget")
        choices = candidates or moves
        selected = choices[0] if decoding != "text" else None
        output = "0" if decoding == "candidate_index" else json.dumps(selected) if selected else "Local mock response."
        message = {"role": "assistant", "content": output}
        if decoding == "text" and body.get("tools") and body.get("tool_choice") != "none" and not any(m["role"] == "tool" for m in body["messages"]):
            output = ""
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "mock-tool-1", "type": "function", "function": {
                    "name": body["tools"][0]["function"]["name"], "arguments": "{}",
                },
            }]}
        request_id = "request-" + secrets.token_hex(8)
        to_move = ("white" if self.board.turn else "black") == self.colour
        trace = {
            "id": request_id,
            "status": "running",
            "game": "local-game",
            "attempt": "local-attempt",
            "entry": "participant",
            "version": self.version,
            "target_fen": board.fen(),
            "barrier": self.barrier,
            "to_move": to_move,
            "model_id": "local-mock",
            "output": output,
            "message": message,
            "output_schema": (body.get("response_format") or {}).get("json_schema", {}).get("schema"),
            "effective_reasoning_effort": "none" if budget == 0 else effort,
            "effective_reasoning_budget_tokens": budget if budget is not None else -1 if effort != "none" else 0,
            "reasoning_content": None,
            "selected_move": selected,
            "selection_status": "valid" if selected else "not_applicable",
            "selection_reason": "valid_selection" if selected else "general_inference",
            "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
            "usage": {
                "prompt_tokens": len(json.dumps(body["messages"]).split()),
                "completion_tokens": 1,
                "estimated": True,
            },
        }
        self.traces[request_id] = trace
        self.requests += 1
        self.pondering += not to_move

        def finish():
            with self.lock:
                if trace["status"] == "running":
                    if self.status == "running" and trace["barrier"] == self.barrier:
                        trace["status"] = "completed"
                    else:
                        trace.update(status="cancelled", reason="barrier")

        timer = threading.Timer(self.delay, finish)
        timer.daemon = True
        timer.start()
        return {"request_id": request_id}

    def cancel_all(self):
        for trace in self.traces.values():
            if trace["status"] in ("running", "queued"):
                trace.update(status="cancelled", reason="organizer_suspension")


def server_for(gateway, path):
    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_one(self, method):
            try:
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + gateway.token
                ):
                    raise PermissionError("Invalid credential")
                with gateway.lock:
                    if method == "POST" and self.path == "/v1/inference":
                        length = int(self.headers.get("Content-Length", 0))
                        if not 0 < length <= 65536:
                            raise ValueError("Message cap exceeded")
                        result = gateway.submit(json.loads(self.rfile.read(length)))
                        status = 202
                    else:
                        prefix = "/v1/inference/"
                        if not self.path.startswith(prefix):
                            raise ValueError("Unknown route")
                        trace = gateway.traces[self.path[len(prefix) :]]
                        if method == "DELETE" and trace["status"] in ("running", "queued"):
                            trace.update(status="cancelled", reason="participant_cancelled")
                            gateway.cancellations += 1
                        result = (
                            {"status": "cancelled"} if method == "DELETE" else copy.deepcopy(trace)
                        )
                        # Pending traces never expose partial model output.
                        if method == "GET" and result["status"] != "completed":
                            for key in ("output", "message", "reasoning_content", "selected_move",
                                        "selection_status", "selection_reason", "usage", "finish_reason"):
                                result.pop(key, None)
                        status = 200
            except PermissionError as exc:
                status, result = 403, {"detail": str(exc)}
            except (ValueError, KeyError, TypeError) as exc:
                status, result = 409, {"detail": str(exc)}
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.handle_one("POST")

        def do_GET(self):
            self.handle_one("GET")

        def do_DELETE(self):
            self.handle_one("DELETE")

    return Server(path, Handler)


def check_harness(command, colour="white", timeout=15):
    report = {
        "passed": False,
        "protocol": "chess.v1",
        "model": "local-mock",
        "checks": {},
        "notes": ["Local compatibility fixture, not official approval or a strength measurement."],
    }
    gateway = MockGateway(colour)
    proc = None
    server = None
    lines = queue.Queue()
    logs = bytearray()
    paused = False
    with tempfile.TemporaryDirectory(prefix="chess-sdk-", dir="/tmp") as folder:
        path = str(Path(folder) / "gateway.sock")
        try:
            server = server_for(gateway, path)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            env = os.environ | {
                "CHESS_INFERENCE_SOCKET": path,
                "CHESS_INFERENCE_TOKEN": gateway.token,
                "PYTHONUNBUFFERED": "1",
            }
            proc = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                start_new_session=True,
            )

            def reader():
                try:
                    while line := proc.stdout.readline(65537):
                        if len(line) > 65536 or not line.endswith(b"\n"):
                            raise ValueError("Oversized/incomplete stdout line")
                        value = json.loads(line)
                        if not isinstance(value, dict):
                            raise ValueError("stdout must contain JSON objects")
                        lines.put(value)
                except Exception as exc:
                    lines.put(exc)
                finally:
                    lines.put(EOFError("Harness stdout closed"))

            def stderr():
                while chunk := proc.stderr.read(4096):
                    logs.extend(chunk[: max(0, 65536 - len(logs))])

            threading.Thread(target=reader, daemon=True).start()
            threading.Thread(target=stderr, daemon=True).start()

            def send(event):
                proc.stdin.write(encode(event))
                proc.stdin.flush()

            def read():
                try:
                    value = lines.get(timeout=timeout)
                except queue.Empty as exc:
                    raise TimeoutError("Harness did not produce expected protocol output") from exc
                if isinstance(value, Exception):
                    raise value
                return value

            def get_action():
                value = read()
                if value.get("type") != "action" or value.get("action") != "move":
                    raise ValueError(
                        "The fixture needs model-selected moves to check compatibility"
                    )
                if (
                    value.get("game_id") != "local-game"
                    or value.get("attempt_id") != "local-attempt"
                    or value.get("position_version") != gateway.version
                ):
                    raise ValueError("Move identity/version does not match the observation")
                if (
                    not isinstance(value.get("action_id"), str)
                    or not 1 <= len(value["action_id"]) <= 128
                ):
                    raise ValueError("Invalid action ID")
                trace = gateway.traces.get(value.get("evidence", {}).get("request_id"))
                if (
                    not trace
                    or trace["status"] != "completed"
                    or trace["barrier"] != gateway.barrier
                    or trace["target_fen"] != gateway.board.fen()
                    or trace.get("selected_move") != value.get("uci")
                ):
                    raise ValueError(
                        "Move does not match completed current-position model evidence"
                    )
                if chess.Move.from_uci(value["uci"]) not in gateway.board.legal_moves:
                    raise ValueError("Model-selected move is not legal")
                return value

            send(initial(colour))
            ready = read()
            if ready.get("type") != "ready" or ready.get("protocol") != "chess.v1":
                raise ValueError("Expected ready handshake, without banners or framework logs")
            report["checks"]["handshake"] = True
            # Start off-turn and verify no action is committed. Pondering is optional.
            if colour == "white":
                gateway.board.push_uci("e2e4")
                gateway.version += 1
            send(gateway.observation())
            try:
                value = lines.get(timeout=0.2)
                raise ValueError(f"Unexpected off-turn output: {value!r}")
            except queue.Empty:
                pass
            report["checks"]["out_of_turn_safety"] = True
            with gateway.lock:
                gateway.board.push(sorted(gateway.board.legal_moves, key=lambda m: m.uci())[0])
                gateway.version += 1
            send(gateway.observation())
            first = get_action()
            report["checks"]["inference_evidence"] = True
            # Controlled rejection tests correction on the same version with a new ID.
            send(
                {
                    "type": "action_response",
                    "action_id": first["action_id"],
                    "accepted": False,
                    "reason": "local_fixture_rejection",
                    "position_version": gateway.version,
                    "status": "running",
                    "result": None,
                    "invalid_attempts": 1,
                }
            )
            send(gateway.observation())
            second = get_action()
            if second["action_id"] == first["action_id"]:
                raise ValueError("Correction must use a new action ID")
            report["checks"]["correction"] = True
            with gateway.lock:
                gateway.board.push_uci(second["uci"])
                gateway.version += 1
            send(
                {
                    "type": "action_response",
                    "action_id": second["action_id"],
                    "accepted": True,
                    "reason": "legal_move",
                    "position_version": gateway.version,
                    "status": "running",
                    "result": None,
                }
            )
            send(gateway.observation())
            with gateway.lock:
                gateway.delay = 0.5
                gateway.board.push(sorted(gateway.board.legal_moves, key=lambda m: m.uci())[0])
                gateway.version += 1
            send(gateway.observation())
            # Wait for model work, then apply a real process stop to mimic the evaluator.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                with gateway.lock:
                    if any(
                        t["status"] == "running" and t["version"] == gateway.version
                        for t in gateway.traces.values()
                    ):
                        break
                time.sleep(0.01)
            else:
                raise TimeoutError("No request observed for the suspension fixture")
            os.killpg(proc.pid, signal.SIGSTOP)
            paused = True
            with gateway.lock:
                gateway.status = "suspended"
                gateway.barrier += 1
                gateway.cancel_all()
            send({"type": "suspend", "reason": "local_fixture", "barrier": gateway.barrier})
            time.sleep(0.1)
            if not lines.empty():
                raise ValueError("Unexpected output crossed the suspension boundary")
            report["checks"]["paired_barrier_handling"] = True
            with gateway.lock:
                gateway.status = "running"
                gateway.delay = 0.08
            send({"type": "resume", "barrier": gateway.barrier})
            os.killpg(proc.pid, signal.SIGCONT)
            paused = False
            send(gateway.observation())
            third = get_action()
            report["checks"]["resume"] = True
            with gateway.lock:
                gateway.board.push_uci(third["uci"])
                gateway.version += 1
                gateway.status = "ended"
                gateway.cancel_all()
            send(gateway.observation("game_ended"))
            send({"type": "shutdown"})
            proc.stdin.close()
            if proc.wait(timeout=3) != 0:
                raise ValueError("Harness did not exit successfully")
            report["checks"]["shutdown"] = True
            report.update(
                passed=True,
                requests=gateway.requests,
                pondering_requests=gateway.pondering,
                participant_cancellations=gateway.cancellations,
                accepted_moves=2,
            )
        except Exception as exc:
            report["error"] = type(exc).__name__ + ": " + str(exc)
        finally:
            if proc and proc.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    if paused:
                        os.killpg(proc.pid, signal.SIGCONT)
                    os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            if server:
                server.shutdown()
                server.server_close()
            report["stderr"] = logs.decode(errors="replace")
    return report
