"""Mutation benchmark: does the evidence engine actually detect hallucinations?

The live benchmark could not answer this. Thirty agent sessions produced three
"hallucinations", all of them artefacts of a broken oracle, because a capable model
would not take the bait. Measuring a detector by waiting for an agent to make mistakes is
a bad experiment: it measures the agent, and it costs a quota.

So measure the detector directly. Take real code that works, introduce a hallucination
programmatically, and ask AgentGuard whether it notices:

    recall    = seeded hallucinations flagged / seeded hallucinations
    precision = real flags / all flags   (the unmutated file must stay silent)

Zero agent sessions, hundreds of cases instead of thirty, and every case has a known
ground truth because the mutation is what created it.

**The mutations are only the ones AgentGuard could in principle catch** — a reference to
something that genuinely does not exist in the repository. That is a real limit on what
this measures: it reports how well the detector finds what it was designed to find, not
how often agents hallucinate in ways it was never built for. Stated here rather than
discovered by a reader.
"""

from __future__ import annotations

import ast
import random
from dataclasses import dataclass
from pathlib import Path

from agentguard.core.enums import EventType
from agentguard.core.events import AgentEvent
from agentguard.evidence import check as evidence_check
from agentguard.repo.index import RepoIndex


@dataclass(slots=True)
class Mutation:
    path: str
    kind: str
    original: str
    mutated: str
    subject: str  # the hallucinated name the detector should flag


@dataclass(slots=True)
class Result:
    total: int = 0
    caught: int = 0
    missed: list[Mutation] = None  # type: ignore[assignment]
    false_positives: int = 0
    clean_files: int = 0

    @property
    def recall(self) -> float:
        return self.caught / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        flags = self.caught + self.false_positives
        return self.caught / flags if flags else 1.0


def _known_types(index: RepoIndex) -> dict[str, set[str]]:
    """Classes whose full attribute set AgentGuard can be confident about."""
    out: dict[str, set[str]] = {}
    for name, records in index.symbols_by_name.items():
        for record in records:
            if record.kind == "class" and record.bases_known and index.is_parsed(record.path):
                attrs = index.attributes_of(record.qualname)
                if attrs:
                    out[name] = attrs
    return out


def generate(index: RepoIndex, limit: int = 400, seed: int = 7) -> list[Mutation]:
    """Introduce references that genuinely do not exist, at call sites that do."""
    rng = random.Random(seed)
    types = _known_types(index)
    mutations: list[Mutation] = []

    for path, record in index.files.items():
        if record.lang != "python" or record.is_test or not index.is_parsed(path):
            continue
        try:
            source = (index.root / path).read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, SyntaxError, ValueError):
            continue

        for node in ast.walk(tree):
            if len(mutations) >= limit:
                return mutations

            # `X.attr` where X is a class we fully understand -> rename attr to something
            # that exists nowhere. A real call site, a genuinely absent member.
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in types
                and node.attr in types[node.value.id]
                and not node.attr.startswith("__")
            ):
                owner = node.value.id
                invented = f"{node.attr}_{rng.randint(1000, 9999)}_missing"
                line = source.splitlines()[node.lineno - 1]
                if f"{owner}.{node.attr}" not in line:
                    continue
                mutations.append(
                    Mutation(
                        path=path,
                        kind="attribute_on_type",
                        original=source,
                        mutated=source.replace(f"{owner}.{node.attr}", f"{owner}.{invented}", 1),
                        subject=f"{owner}.{invented}",
                    )
                )

            # `from <real module> import <name that is not there>`
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if index.resolve_module(node.module) is None or not node.names:
                    continue
                real = node.names[0].name
                invented = f"{real}_{rng.randint(1000, 9999)}_missing"
                mutations.append(
                    Mutation(
                        path=path,
                        kind="symbol_exists",
                        original=source,
                        mutated=source.replace(
                            f"import {real}", f"import {invented}", 1
                        ),
                        subject=invented,
                    )
                )

    return mutations


def _event(index: RepoIndex, path: str, content: str) -> AgentEvent:
    return AgentEvent(
        event=EventType.PRE_TOOL_USE,
        agent="bench",
        workspace=str(index.root),
        session_id="mutation",
        tool="Write",
        arguments={"file_path": path, "content": content},
    )


def evaluate(index: RepoIndex, mutations: list[Mutation]) -> Result:
    """Every mutation is a known hallucination; every original is known-clean."""
    result = Result(missed=[])
    seen_clean: set[str] = set()

    for mutation in mutations:
        result.total += 1
        findings = evidence_check(_event(index, mutation.path, mutation.mutated), index)
        subjects = {f.subject for f in findings}
        bare = mutation.subject.rsplit(".", 1)[-1]
        if any(bare in s or s == mutation.subject for s in subjects):
            result.caught += 1
        else:
            result.missed.append(mutation)

        # The unmutated file must produce nothing. Counted once per file.
        if mutation.path not in seen_clean:
            seen_clean.add(mutation.path)
            result.clean_files += 1
            if evidence_check(_event(index, mutation.path, mutation.original), index):
                result.false_positives += 1

    return result


def render(result: Result) -> str:
    lines = [
        f"Mutation benchmark · {result.total} seeded hallucinations · "
        f"{result.clean_files} clean files",
        "",
        f"  recall     {result.recall * 100:5.1f}%   ({result.caught}/{result.total} caught)",
        f"  precision  {result.precision * 100:5.1f}%   "
        f"({result.false_positives} false positive(s) on unmutated code)",
        "",
    ]
    by_kind: dict[str, list[int]] = {}
    for mutation in result.missed:
        by_kind.setdefault(mutation.kind, []).append(1)
    if result.missed:
        lines.append("misses by kind:")
        for kind, hits in sorted(by_kind.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"  {kind:<22}{len(hits)}")
        lines.append("")
        lines.append("examples of what it missed:")
        for mutation in result.missed[:5]:
            lines.append(f"  {mutation.path}: {mutation.subject}")
    return "\n".join(lines)


def run(repo: Path, limit: int = 400) -> Result:
    index = RepoIndex(repo).build()
    mutations = generate(index, limit=limit)
    return evaluate(index, mutations)
