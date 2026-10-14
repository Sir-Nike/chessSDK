"""Optional integration against a separately installed organizer engine, never an SDK dependency."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_organizer_evaluator_accepts_sdk_and_preserves_pause(tmp_path):
    arena_path = os.environ.get("CHESS_ARENA_PATH")
    if not arena_path:
        pytest.skip("Set CHESS_ARENA_PATH and run with organizer dependencies installed")
    sys.path.insert(0, str(Path(arena_path) / "src"))
    from chess_arena.config import Settings
    from chess_arena.inference import Gateway
    from chess_arena.runner import ActiveGame, Runner, Session
    from chess_arena.service import Arena
    from chess_arena.store import Store

    class SDKSession(Session):
        async def start(self):
            self.process = await asyncio.create_subprocess_exec(
                str(ROOT / ".venv/bin/python"),
                "-m",
                "chess_sdk",
                "run",
                cwd=ROOT,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ
                | {
                    "CHESS_INFERENCE_SOCKET": str(Path(self.socket_dir) / "gateway.sock"),
                    "CHESS_INFERENCE_TOKEN": self.token,
                    "PYTHONUNBUFFERED": "1",
                },
                start_new_session=True,
                limit=self.arena.settings.message_bytes + 1,
            )
            self.log_task = asyncio.create_task(self.drain_logs())

    async def exercise():
        settings = Settings.load(os.environ.get("CHESS_ARENA_CONFIG"))
        settings.mode = "development"
        settings.max_plies = 6
        settings.database_url = "sqlite:///:memory:"
        settings.artifact_dir = str(tmp_path)
        store = Store(settings.database_url)
        arena = Arena(settings, store)
        gateway = Gateway(arena)
        runner = Runner(arena, gateway)
        model_id = next(iter(settings.models))
        entries = [
            arena.demo_entry(arena.create_team(f"SDK check {i}")["id"], model=model_id)
            for i in range(2)
        ]
        for entry in entries:
            entry["harness_config"] = {"ponder": True}
            with store.transaction() as tx:
                tx.put("entry", entry["id"], entry)
        tree = arena.create_tournament([e["id"] for e in entries], 123)
        claim = arena.claim(
            tree["id"], next(m["id"] for m in tree["matches"] if m["status"] == "ready"), "sdk-test"
        )
        gid = claim["game"]["id"]
        task = None
        try:
            await gateway.start()
            await runner.start()
            sessions = {
                e["id"]: SDKSession(arena, e, claim["credentials"][e["id"]], runner.socket_dir)
                for e in entries
            }
            active = ActiveGame(arena, gateway, claim, sessions)
            runner.running[gid] = active
            task = asyncio.create_task(active.run())
            deadline = asyncio.get_running_loop().time() + 30
            while arena.get_game(gid)["status"] == "preparing":
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.01)
            assert arena.get_game(gid)["status"] == "running"
            await active.pause("sdk_integration_check")
            paused = arena.get_game(gid)
            await asyncio.sleep(0.1)
            assert arena.get_game(gid)["remaining"] == paused["remaining"]
            assert all(s.paused for s in sessions.values())
            assert paused["barrier"] == 1
            await active.resume()
            await asyncio.wait_for(task, timeout=90)
            record = arena.get_game(gid)
            assert record["status"] == "ended", record
            assert len(record["move_log"]) == 6, record
            with store.transaction() as tx:
                for move in record["move_log"]:
                    trace = tx.get("inference", move["evidence"]["request_id"])
                    assert trace["status"] == "completed"
                    assert trace["selected_move"] == move["uci"]
                    assert trace["barrier"] == 1
            assert all(not s.logs for s in sessions.values()), [
                s.logs.decode() for s in sessions.values()
            ]
            report = {
                "passed": True,
                "model_id": model_id,
                "execution": "trusted-local-development",
                "accepted_moves": len(record["move_log"]),
                "barrier": record["barrier"],
                "result": record["result"],
                "reason": record["reason"],
                "official_approval": False,
            }
            destination = ROOT / "reports" / f"evaluator-{model_id}.json"
            destination.parent.mkdir(exist_ok=True)
            destination.write_text(json.dumps(report, indent=2) + "\n")
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await runner.close()
            await gateway.close()
            store.close()

    asyncio.run(exercise())
