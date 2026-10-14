"""Run a participant's local GGUF through llama.cpp during personal practice."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import httpx


class LlamaCppAgent:
    """Callable practice agent backed by a private local llama-server process."""

    def __init__(self, model_path, *, server_path=None, gpu_layers=99, context_size=4096):
        self.model_path = Path(model_path).expanduser().resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(f"GGUF model file does not exist: {self.model_path}")
        if self.model_path.suffix.lower() != ".gguf":
            raise ValueError("--model must point to a .gguf file")
        if gpu_layers < 0 or context_size < 256:
            raise ValueError("GPU layers must be nonnegative and context size at least 256")

        executable = server_path or os.environ.get("LLAMA_SERVER") or shutil.which("llama-server")
        if not executable:
            raise FileNotFoundError(
                "Could not find llama-server. Install/build llama.cpp, put it on PATH, "
                "or pass --llama-server /path/to/llama-server."
            )
        self.server_path = str(Path(executable).expanduser()) if "/" in executable else executable
        if "/" in self.server_path and not Path(self.server_path).is_file():
            raise FileNotFoundError(f"llama-server executable does not exist: {self.server_path}")

        self.gpu_layers = gpu_layers
        self.context_size = context_size
        self.port = self._free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.process = None
        self.http = None

    @staticmethod
    def _free_port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def __enter__(self):
        command = [
            self.server_path,
            "--model", str(self.model_path),
            "--ctx-size", str(self.context_size),
            "--n-gpu-layers", str(self.gpu_layers),
            "--parallel", "1",
            "--host", "127.0.0.1",
            "--port", str(self.port),
            "--alias", "chess-sdk-local",
            "--jinja",
            "--reasoning", "auto",
            "--no-webui",
        ]
        try:
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL)
            self.http = httpx.Client(base_url=self.base_url, timeout=5, trust_env=False)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    return_code = self.process.returncode
                    raise RuntimeError(
                        f"llama-server exited during startup (status {return_code}); "
                        "see its stderr output for details."
                    )
                try:
                    response = self.http.get("/health")
                    if response.is_success:
                        return self
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            raise TimeoutError("llama-server did not become ready within 180 seconds")
        except BaseException:
            self.close()
            raise

    def __call__(self, session):
        legal_moves = session.get("legal_moves") or []
        if not legal_moves:
            raise ValueError("Practice session has no legal moves for the agent to choose")

        side = session.get("turn")
        remaining = session.get("clocks", {}).get(side, 60)
        timeout = max(1, min(900, float(remaining) - 0.5))
        if remaining <= 0.5:
            raise TimeoutError("Not enough clock remains to request a local model move")

        prompt = {
            "fen": session.get("fen"),
            "history_uci": session.get("history", []),
            "legal_moves_uci": legal_moves,
            "side_to_move": side,
            "your_colour": session.get("colour"),
            "clocks_seconds": session.get("clocks", {}),
            "opponent": session.get("opponent", {}),
        }
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "chess_move_selection",
                "strict": True,
                "schema": {"type": "string", "enum": legal_moves},
            },
        }
        with httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=5),
            trust_env=False,
        ) as client:
            response = client.post(
                "/v1/chat/completions",
                json={
                    "model": "chess-sdk-local",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are playing chess. Choose the strongest legal move for "
                                "your side. Return exactly one move from the supplied legal "
                                "UCI move list. The required output format enforces this."
                            ),
                        },
                        {"role": "user", "content": json.dumps(prompt)},
                    ],
                    "response_format": response_format,
                    "temperature": 0,
                    "max_tokens": 512,
                    "reasoning_effort": "low",
                    "reasoning_budget_tokens": 96,
                },
            )
            response.raise_for_status()

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("llama-server returned an unexpected chat completion") from exc
        if not isinstance(content, str):
            raise ValueError("llama-server returned no move text")
        try:
            move = json.loads(content)
        except json.JSONDecodeError:
            move = content.strip()
        if move not in legal_moves:
            raise ValueError(f"Local model returned a move outside the legal move list: {move!r}")
        return move

    def close(self):
        if self.http is not None:
            self.http.close()
            self.http = None
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None

    def __exit__(self, *_exc):
        self.close()
