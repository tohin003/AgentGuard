"""AgentGuard-Bench runner (SPEC §36, §37).

Two arms, identical in every respect except whether AgentGuard is enabled. The repository
is reset to a pristine commit between every run, so no run can inherit another's state.

The arms differ by exactly one environment variable. That is the whole experiment: if any
other difference creeps in — a different prompt, a different model, a different starting
tree — the comparison stops meaning anything.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from agentguard.bench import tasks as task_module
from agentguard.bench.tasks import BenchTask, Outcome

ARMS = ("control", "agentguard")


def reset(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "reset", "--hard", "-q"], check=False)
    subprocess.run(["git", "-C", str(repo), "clean", "-fdq", "-e", ".venv", "-e", ".claude"],
                   check=False)


def run_once(task: BenchTask, repo: Path, arm: str, settings: Path, timeout: int = 300) -> Outcome:
    reset(repo)
    if task.setup:
        task.setup(repo)

    env = dict(os.environ)
    if arm == "control":
        # The single difference between the arms.
        env["AGENTGUARD_DISABLE"] = "1"
    else:
        env.pop("AGENTGUARD_DISABLE", None)

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [
                "claude", "-p", task.prompt,
                "--settings", str(settings),
                "--permission-mode", "acceptEdits",
                "--allowedTools", "Read,Grep,Glob,Edit,Write,Bash",
                "--output-format", "text",
            ],
            cwd=repo,
            env=env,
            capture_output=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        transcript = proc.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return Outcome(duration_s=time.perf_counter() - started, error="timeout")

    duration = time.perf_counter() - started
    changed = task_module.changed_files(repo)
    expected = set(task.expected_files)

    outcome = Outcome(
        hallucinated_refs=task_module.count_hallucinated_refs(repo, task.bait_symbols),
        unnecessary_files=len({c for c in changed if c not in expected and not c.startswith("tests/")}),
        files_changed=len(changed),
        duration_s=round(duration, 1),
    )
    if "false_completion" in task.measures:
        outcome.false_completion = task_module.claimed_done_without_tests(transcript, repo)
    if "tests_pass" in task.measures:
        outcome.tests_pass = task_module.tests_pass(repo)
    return outcome


def run(repo: Path, settings: Path, task_ids: list[str] | None, n: int, out: Path) -> dict:
    selected = [t for t in task_module.TASKS if not task_ids or t.id in task_ids]
    results: list[dict] = []

    for task in selected:
        for arm in ARMS:
            for run_index in range(n):
                outcome = run_once(task, repo, arm, settings)
                row = {"task": task.id, "domain": task.domain, "arm": arm, "run": run_index,
                       **asdict(outcome)}
                results.append(row)
                flag = "!" if (outcome.hallucinated_refs or outcome.false_completion) else " "
                print(f"  {flag} {task.id:<22}{arm:<11}run {run_index + 1}/{n}  "
                      f"halluc={outcome.hallucinated_refs} unnec={outcome.unnecessary_files} "
                      f"false_done={outcome.false_completion} {outcome.duration_s}s"
                      f"{' ' + outcome.error if outcome.error else ''}", flush=True)

    reset(repo)
    payload = {"n": n, "results": results, "summary": summarize(results)}
    out.write_text(json.dumps(payload, indent=2))
    return payload


def summarize(results: list[dict]) -> dict:
    """Totals and per-run spread. Means alone would hide how noisy small-n really is."""
    summary: dict = {}
    for arm in ARMS:
        rows = [r for r in results if r["arm"] == arm]
        if not rows:
            continue
        summary[arm] = {
            "runs": len(rows),
            "hallucinated_refs": sum(r["hallucinated_refs"] for r in rows),
            "unnecessary_files": sum(r["unnecessary_files"] for r in rows),
            "false_completions": sum(r["false_completion"] for r in rows),
            "duration_s_median": round(statistics.median(r["duration_s"] for r in rows), 1),
            "errors": sum(1 for r in rows if r["error"]),
        }
    return summary


def render(payload: dict) -> str:
    s = payload["summary"]
    if not {"control", "agentguard"} <= set(s):
        return "incomplete: both arms are required for a comparison"

    c, a = s["control"], s["agentguard"]
    lines = [
        f"AgentGuard-Bench · n={payload['n']} per task per arm · "
        f"{c['runs']} control runs, {a['runs']} guarded runs",
        "",
        f"{'metric':<26}{'control':>10}{'agentguard':>12}",
        "-" * 48,
    ]
    # Every metric here is lower-is-better.
    for label, key in (
        ("hallucinated references", "hallucinated_refs"),
        ("unnecessary files", "unnecessary_files"),
        ("false completions", "false_completions"),
        ("median duration (s)", "duration_s_median"),
    ):
        lines.append(f"{label:<26}{c[key]:>10}{a[key]:>12}")

    lines.append("")
    caught = c["hallucinated_refs"] - a["hallucinated_refs"]
    if c["hallucinated_refs"] == 0:
        lines.append("The control arm produced no hallucinated references, so this corpus")
        lines.append("cannot show a reduction. That is a fact about the corpus, not a result.")
    else:
        lines.append(f"Hallucinated references: {c['hallucinated_refs']} -> "
                     f"{a['hallucinated_refs']} ({caught / c['hallucinated_refs'] * 100:.0f}% fewer)")
    lines.append("")
    lines.append("Per-run results are in the JSON; small n means wide error bars, and the")
    lines.append("spread matters more than the totals.")
    return "\n".join(lines)


def prepare_repo(source: Path, dest: Path) -> Path:
    """A pristine, committed copy with a working test suite."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git", ".venv", ".claude"))
    subprocess.run(["git", "-C", str(dest), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(dest), "-c", "user.email=b@b", "-c", "user.name=b",
         "commit", "-qm", "bench baseline"],
        check=True,
    )
    return dest
