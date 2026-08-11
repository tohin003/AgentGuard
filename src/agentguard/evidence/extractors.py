"""Tool arguments -> claims (SPEC §14, §18).

Two decisions shape this module.

**Claims come from the post-edit file, not from the edit fragment.** An `Edit` supplies a
`new_string` with no context; `repo.get_active_users()` in isolation says nothing about
what `repo` is. Applying the edit to the file on disk and analysing the result recovers
the imports, the class definitions and the local bindings that make the call meaningful.

**Only *newly introduced* claims are checked.** Claims present before the edit are
subtracted out, so AgentGuard never challenges the agent for something that was already
in the file. Being blamed for pre-existing code is the fastest way to become noise.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentguard.core.enums import ClaimKind
from agentguard.core.events import AgentEvent
from agentguard.evidence import pyanalysis
from agentguard.evidence.models import Claim

PYTHON_SUFFIXES = (".py", ".pyi")

# Commands that add a dependency — SPEC §16's DEPENDENCY challenge.
_INSTALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpip3?\s+install\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\buv\s+(?:pip\s+install|add)\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\bpoetry\s+add\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\b(?:npm|pnpm)\s+(?:install|i|add)\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\byarn\s+add\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\bcargo\s+add\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
    re.compile(r"\bgo\s+get\s+(?!-)(?P<pkgs>[^\n;&|]+)"),
)

_PKG_NAME = re.compile(r"^[@A-Za-z0-9][\w.@/-]*")


class EditOutcome:
    """The state of one file before and after a proposed edit."""

    __slots__ = ("after", "applied", "before", "path")

    def __init__(self, path: str, before: str | None, after: str | None, applied: bool) -> None:
        self.path = path
        self.before = before
        self.after = after
        self.applied = applied


def resolve_edit(event: AgentEvent, root: Path) -> EditOutcome | None:
    """Compute the file content this action would produce.

    Returns None when the tool does not edit a file, or when the edit cannot be applied
    (a missing `old_string` means the tool call will fail on its own — not our problem).
    """
    tool = event.tool
    raw_path = event.arg("file_path") or event.arg("notebook_path")
    if not isinstance(raw_path, str) or not raw_path:
        return None

    path = _relative(raw_path, root)
    full = root / path
    try:
        before = full.read_text(encoding="utf-8", errors="replace") if full.is_file() else ""
    except OSError:
        return None

    if tool == "Write":
        content = event.arg("content")
        if not isinstance(content, str):
            return None
        return EditOutcome(path, before, content, True)

    if tool == "Edit":
        old = event.arg("old_string")
        new = event.arg("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return None
        if old and old not in before:
            return None  # the tool call will fail; nothing to assess
        after = before.replace(old, new, -1 if event.arg("replace_all") else 1) if old else before + new
        return EditOutcome(path, before, after, True)

    if tool == "MultiEdit":
        edits = event.arg("edits")
        if not isinstance(edits, list):
            return None
        after = before
        for edit in edits:
            if not isinstance(edit, dict):
                return None
            old, new = edit.get("old_string"), edit.get("new_string")
            if not isinstance(old, str) or not isinstance(new, str):
                return None
            if old and old not in after:
                return None
            after = after.replace(old, new, -1 if edit.get("replace_all") else 1) if old else after + new
        return EditOutcome(path, before, after, True)

    if tool == "NotebookEdit":
        source = event.arg("new_source")
        if not isinstance(source, str):
            return None
        return EditOutcome(path, "", source, True)

    return None


def _relative(raw: str, root: Path) -> str:
    candidate = Path(raw)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    return candidate.as_posix().removeprefix("./")


def claims_from_edit(outcome: EditOutcome) -> tuple[list[Claim], pyanalysis.SourceFacts | None]:
    """Newly introduced claims, plus the facts about the resulting file.

    An empty list is returned — deliberately — when the *previous* content could not be
    parsed. Without a clean baseline there is no way to tell a new claim from an old one,
    and guessing would mean blaming the agent for someone else's code.
    """
    if not outcome.path.endswith(PYTHON_SUFFIXES):
        return [], None
    if outcome.after is None:
        return [], None

    after_facts = pyanalysis.analyze(outcome.after, outcome.path)
    if not after_facts.parsed:
        # The edit produces unparsable Python. That is a problem, but it is a syntax
        # problem for the verification layer, not a hallucinated-symbol problem.
        return [], after_facts

    before_facts = pyanalysis.analyze(outcome.before or "", outcome.path)
    if (outcome.before or "").strip() and not before_facts.parsed:
        return [], after_facts

    seen = {claim.fingerprint() for claim in before_facts.claims}
    new_claims = [claim for claim in after_facts.claims if claim.fingerprint() not in seen]
    return new_claims, after_facts


def claims_from_bash(command: str) -> list[Claim]:
    """Dependency additions only.

    File paths in shell commands are far too noisy to treat as claims — most commands
    create the paths they mention. Dependency installs are unambiguous.
    """
    claims: list[Claim] = []
    seen: set[str] = set()

    for pattern in _INSTALL_PATTERNS:
        for match in pattern.finditer(command):
            for token in match.group("pkgs").split():
                if token.startswith("-"):
                    continue
                name_match = _PKG_NAME.match(token)
                if not name_match:
                    continue
                name = name_match.group(0).rstrip(",")
                # Version specifiers and extras are not part of the package identity.
                name = re.split(r"[=<>!~\[]", name)[0]
                if not name or name in seen or name in {"install", "add", "get"}:
                    continue
                seen.add(name)
                claims.append(
                    Claim(
                        kind=ClaimKind.DEPENDENCY_DECLARED,
                        subject=name,
                        snippet=command.strip()[:160],
                    )
                )
    return claims


def claims_for_event(event: AgentEvent, root: Path) -> tuple[list[Claim], pyanalysis.SourceFacts | None]:
    if event.tool == "Bash":
        command = event.arg("command")
        return (claims_from_bash(command) if isinstance(command, str) else []), None

    outcome = resolve_edit(event, root)
    if outcome is None:
        return [], None
    return claims_from_edit(outcome)
