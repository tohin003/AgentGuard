"""Static analysis of what changed (SPEC §19).

The cheapest verification there is: does the code the agent just wrote actually parse?
No test run required, no configuration, no false positives — a Python file that raises
`SyntaxError` is broken by definition.

Deliberately limited to that. Style opinions belong to the project's own linter, and
AgentGuard running someone else's ruff configuration and reporting the result as a
reliability finding would be both slow and presumptuous.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from agentguard.repo import symbols_ts
from agentguard.repo.paths import relative_path
from agentguard.repo.scanner import detect_language


@dataclass(slots=True)
class SyntaxProblem:
    path: str
    line: int
    message: str


def check_files(root: Path, paths: set[str]) -> list[SyntaxProblem]:
    """Files that no longer parse. An unreadable or deleted file is not a problem here."""
    problems: list[SyntaxProblem] = []
    for rel in sorted(paths):
        safe = relative_path(root, rel)
        if safe is None:
            continue
        rel = safe
        full = root / rel
        try:
            if not full.resolve(strict=False).is_relative_to(root.resolve(strict=False)):
                continue
        except (OSError, RuntimeError):
            continue
        if not full.is_file():
            continue
        language = detect_language(rel)
        try:
            source = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if language == "python":
            try:
                ast.parse(source, filename=rel)
            except SyntaxError as exc:
                problems.append(
                    SyntaxProblem(path=rel, line=exc.lineno or 0, message=exc.msg or "syntax error")
                )
            except (ValueError, RecursionError):
                continue
        elif symbols_ts.available(language):
            _, _, clean = symbols_ts.extract(source, rel, language)
            if not clean:
                problems.append(SyntaxProblem(path=rel, line=0, message="does not parse"))

    return problems
