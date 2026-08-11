"""SPEC §7, §8 — latency budgets.

    "Deterministic check: < 100 ms target"
    "The user should not feel that AgentGuard is slowing down the coding agent."

These assertions run on every commit from here on. As real checks land in Phases 1-4, the
budget is the thing that stops them from quietly getting expensive.

Two numbers are measured, and they answer different questions:

* **in-process** — what the Guard itself costs. This is the number the engines must keep
  down as they grow.
* **end-to-end over HTTP** — what Claude Code actually experiences, including the socket
  round trip. This is the number the developer feels.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.config import Settings
from agentguard.core.engine import Guard
from agentguard.core.metrics import percentile
from tests.conftest import pre_tool_use, user_prompt_submit

pytestmark = [pytest.mark.spec, pytest.mark.latency]

ITERATIONS = 200
WARMUP = 20

# SPEC §8
DETERMINISTIC_BUDGET_MS = 100.0
REPOSITORY_BUDGET_MS = 500.0


def measure(fn, iterations: int = ITERATIONS, warmup: int = WARMUP) -> dict[str, float]:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "p50": percentile(samples, 50),
        "p95": percentile(samples, 95),
        "p99": percentile(samples, 99),
        "max": max(samples),
        "mean": sum(samples) / len(samples),
    }


def report(label: str, stats: dict[str, float]) -> None:
    print(
        f"\n  {label:<34} p50={stats['p50']:6.2f}ms  p95={stats['p95']:6.2f}ms  "
        f"p99={stats['p99']:6.2f}ms  max={stats['max']:6.2f}ms"
    )


class TestInProcess:
    def test_read_only_tool_is_effectively_free(self, workspace):
        """SPEC §7: read-only tools never leave Level 0."""
        guard = Guard(Settings())
        event = claude.to_event(pre_tool_use("Read", cwd=str(workspace), file_path="a.py"))
        try:
            stats = measure(lambda: guard.handle(event))
        finally:
            guard.close()
        report("L0 read-only (in-process)", stats)
        assert stats["p95"] < DETERMINISTIC_BUDGET_MS

    def test_mutating_tool_within_deterministic_budget(self, workspace):
        guard = Guard(Settings())
        event = claude.to_event(
            pre_tool_use("Edit", cwd=str(workspace), file_path="a.py", old_string="x", new_string="y")
        )
        try:
            stats = measure(lambda: guard.handle(event))
        finally:
            guard.close()
        report("L0 mutating (in-process)", stats)
        assert stats["p95"] < DETERMINISTIC_BUDGET_MS

    def test_prompt_handling_within_repository_budget(self, workspace):
        """UserPromptSubmit does the heaviest Level-1 work (intent + complexity)."""
        guard = Guard(Settings())
        event = claude.to_event(
            user_prompt_submit("Add pagination to the /users endpoint", cwd=str(workspace))
        )
        try:
            stats = measure(lambda: guard.handle(event), iterations=100)
        finally:
            guard.close()
        report("L1 user prompt (in-process)", stats)
        assert stats["p95"] < REPOSITORY_BUDGET_MS

    def test_prompt_handling_against_a_real_index(self, tmp_path):
        """The measurement that matters: intent + complexity + governor over a repository
        with real symbols, imports and a real dependency graph."""
        import shutil

        from agentguard.complexity import assess
        from agentguard.intent import extract
        from agentguard.planning import render
        from agentguard.repo import RepoIndex

        dest = tmp_path / "pyrepo"
        shutil.copytree(Path(__file__).parent.parent / "fixtures" / "pyrepo", dest)
        index = RepoIndex(dest).build()

        prompts = [
            "Add pagination to /users.",
            "Make authentication horizontally scalable across multiple services.",
            "Rename get_user to fetch_user.",
        ]

        def once() -> None:
            for prompt in prompts:
                spec = extract(prompt, index)
                spec.complexity = assess(spec, index)
                render(spec, index)

        stats = measure(once, iterations=60, warmup=10)
        report("L1 full prompt path (indexed)", stats)
        assert stats["p95"] < REPOSITORY_BUDGET_MS


class TestEndToEnd:
    def test_http_hook_round_trip_within_budget(self, daemon, workspace):
        """What Claude Code actually pays per tool call.

        The `http` hook type is what makes this affordable: there is no Python process
        spawn here, only a localhost round trip.
        """
        url = f"{daemon.url}/hook/claude-code"
        headers = {"Authorization": f"Bearer {daemon.token}", "Content-Type": "application/json"}
        payload = json.dumps(
            pre_tool_use("Edit", cwd=str(workspace), file_path="a.py", old_string="x", new_string="y")
        )

        with httpx.Client(timeout=5.0) as client:
            stats = measure(lambda: client.post(url, content=payload, headers=headers), iterations=100)

        report("end-to-end over HTTP", stats)
        assert stats["p95"] < DETERMINISTIC_BUDGET_MS, (
            f"hot path p95 {stats['p95']:.1f}ms exceeds the SPEC §8 budget of "
            f"{DETERMINISTIC_BUDGET_MS}ms"
        )

    @pytest.mark.slow
    def test_command_shim_cost_is_measured_and_understood(self, daemon, workspace):
        """The fallback transport, measured so the choice of `http` stays justified.

        This is expected to be *over* budget — it pays Python interpreter startup per
        call. That is precisely why it is the fallback and not the default. The test
        records the number rather than asserting a budget it cannot meet.
        """
        import os
        import subprocess
        import sys

        from tests.conftest import REPO_ROOT

        env = dict(os.environ, AGENTGUARD_HOME=str(daemon.home), PYTHONPATH=str(REPO_ROOT / "src"))
        payload = json.dumps(pre_tool_use("Edit", cwd=str(workspace))).encode()

        def run_once():
            subprocess.run(
                [sys.executable, "-m", "agentguard.adapters.claude_code.shim"],
                input=payload,
                capture_output=True,
                env=env,
                timeout=30,
            )

        stats = measure(run_once, iterations=15, warmup=3)
        report("command shim (fallback)", stats)
        # Only a sanity ceiling: if even the fallback took seconds, something is wrong.
        assert stats["p95"] < 2000.0
