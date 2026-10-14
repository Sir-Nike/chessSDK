"""Personal practice only: the participant's local model chooses each move."""

import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx


class PracticeClient:
    def __init__(self, url=None, token=None, *, transport=None, timeout=5):
        self.http = httpx.Client(
            base_url=url or os.environ.get("STOCKFISH_API_URL", "http://127.0.0.1:8003"),
            headers={"Authorization": "Bearer " + (token or os.environ["STOCKFISH_TEAM_TOKEN"])},
            transport=transport,
            timeout=timeout,
            trust_env=False,
        )

    def _request(self, method, path, **kwargs) -> dict[str, Any]:
        # All mutations carry a stable request/action ID. A lost response may be retried safely.
        for attempt in range(3):
            try:
                response = self.http.request(method, path, **kwargs)
                if response.status_code == 429 and attempt < 2:
                    time.sleep(1)
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Practice API response must be a JSON object")
                return payload
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(0.1 * (attempt + 1))
        raise RuntimeError("Practice request retries exhausted")

    def profiles(self):
        return self._request("GET", "/v1/profiles")

    def create(
        self,
        *,
        colour="random",
        level=5,
        white_seconds=600,
        black_seconds=600,
        increment=0,
        agent=None,
        request_id=None,
    ):
        return self._request(
            "POST",
            "/v1/sessions",
            json={
                "request_id": request_id or uuid.uuid4().hex,
                "colour": colour,
                "level": level,
                "time_control": {
                    "white": white_seconds,
                    "black": black_seconds,
                    "increment": increment,
                },
                "agent": agent or {},
            },
        )

    def read(self, session_id):
        return self._request("GET", f"/v1/sessions/{session_id}")

    def action(self, session, *, action="move", uci=None, action_id=None):
        data = {
            "action_id": action_id or uuid.uuid4().hex,
            "position_version": session["position_version"],
            "action": action,
        }
        if uci is not None:
            data["uci"] = uci
        return self._request("POST", f"/v1/sessions/{session['id']}/actions", json=data)

    def move(self, session, uci, *, action_id=None):
        return self.action(session, uci=uci, action_id=action_id)

    def resign(self, session, *, action_id=None):
        return self.action(session, action="resign", action_id=action_id)

    def wait(self, session_id, *, timeout=120, poll=0.1):
        deadline = time.monotonic() + timeout
        while True:
            session = self.read(session_id)
            if not session["engine_pending"] or session["status"] != "running":
                return session
            if time.monotonic() >= deadline:
                raise TimeoutError("Engine response pending; reconnect with the same session ID")
            time.sleep(poll)

    def history(self, *, limit=50, offset=0):
        return self._request(
            "GET", "/v1/teams/me/history", params={"limit": limit, "offset": offset}
        )

    def pgn(self, session_id):
        response = self.http.get(f"/v1/sessions/{session_id}/pgn")
        response.raise_for_status()
        return response.text

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def run_practice(args):
    if args.model:
        from .local_llm import LlamaCppAgent

        with LlamaCppAgent(
            args.model,
            server_path=args.llama_server,
            gpu_layers=args.gpu_layers,
            context_size=args.context_size,
        ) as choose:
            _run_practice(args, choose)
    else:
        from .runner import load_agent

        choose = load_agent(args.agent) if args.agent else None
        _run_practice(args, choose)


def _run_practice(args, choose):
    with PracticeClient(url=args.url) as client:
        session = (
            client.read(args.session)
            if args.session
            else client.create(
                colour=args.colour,
                level=args.level,
                white_seconds=args.white_minutes * 60,
                black_seconds=args.black_minutes * 60,
                increment=args.increment,
                agent={
                    "model": Path(args.model).name if args.model else "interactive",
                    "harness": args.agent or ("llama.cpp" if args.model else "chess-sdk-practice"),
                },
            )
        )
        print(
            f"Session {session['id']}; playing {session['colour']}; level {session['opponent']['level']}",
            flush=True,
        )
        try:
            while session["status"] == "running":
                session = client.wait(session["id"])
                if session["status"] != "running":
                    break
                uci = (
                    choose(session)
                    if choose
                    else input(f"{session['fen']}\nUCI move (or resign): ").strip()
                )
                if not isinstance(uci, str):
                    raise TypeError("Local practice callback must return a UCI string or 'resign'")
                result = client.resign(session) if uci == "resign" else client.move(session, uci)
                session = result["session"]
                if not result["accepted"]:
                    print(f"Rejected: {result['reason']}", flush=True)
        except KeyboardInterrupt:
            print(f"Disconnected; clock continues. Reconnect with --session {session['id']}")
            return
        print(f"{session['status']}: {session['result']} ({session['reason']})")
        if session["rating_update"]:
            update = session["rating_update"]
            print(f"Internal practice rating: {update['before']:.1f} -> {update['after']:.1f}")
