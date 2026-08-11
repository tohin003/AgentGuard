"""Intent Gateway (SPEC §9, §10).

    Developer: "Add pagination to /users."
      -> Task, Domain, Complexity, Risk, Expected changes, Required verification,
         Unnecessary actions

Extraction is deterministic and then **grounded**: every identifier-shaped token the
prompt mentions is looked up in the repository index. That lookup is what separates this
from keyword matching — "add pagination to /users" in a repo that already has a
pagination utility is a different task from the same words in a repo that does not, and
the difference is visible here rather than being left for the agent to notice.

A token that looks like a symbol but resolves to nothing is not an error. It is an
*uncertainty signal*, which raises planning depth (SPEC §12) rather than raising an
objection.
"""

from __future__ import annotations

import re

from agentguard.core.enums import Domain
from agentguard.core.models import EvidenceRef
from agentguard.intent import lexicon as lex
from agentguard.intent.models import Target, TaskSpec
from agentguard.repo.index import RepoIndex

_MAX_TOKENS = 40
_MAX_PROMPT = 20_000


def extract(prompt: str, index: RepoIndex | None = None) -> TaskSpec:
    """Prompt (+ repository) -> TaskSpec. Never raises."""
    prompt = (prompt or "")[:_MAX_PROMPT]
    lowered = prompt.lower()

    spec = TaskSpec(prompt=prompt, goal=_goal(prompt))
    spec.verbs = _verbs(lowered)
    spec.targets = _targets(prompt, index)
    spec.constraints = _constraints(prompt)
    spec.acceptance_criteria = _acceptance(prompt)
    spec.ambiguities = _ambiguities(lowered)

    primary, secondary = classify_domains(lowered, spec.targets)
    spec.primary_domain = primary
    spec.secondary_domains = secondary

    spec.expected_scope = _expected_scope(spec, index)
    return spec


# -- pieces -----------------------------------------------------------------------


def _goal(prompt: str) -> str:
    """First sentence, trimmed. The prompt's own summary of itself."""
    text = " ".join(prompt.strip().split())
    if not text:
        return ""
    match = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)
    return match[0][:200]


def _verbs(lowered: str) -> list[str]:
    """Ordered by first appearance, so the leading verb is the primary one."""
    found: list[tuple[int, str]] = []
    for verb, patterns in lex.VERB_PATTERNS.items():
        positions = [pos for pos in (lex.first_position(lowered, p) for p in patterns) if pos >= 0]
        if positions:
            found.append((min(positions), verb))
    return [verb for _, verb in sorted(found)] or [lex.VerbClass.UNKNOWN]


def _candidate_tokens(prompt: str) -> list[tuple[str, str]]:
    """(token, kind) pairs, most explicit signal first, de-duplicated."""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(token: str, kind: str) -> None:
        token = token.strip().strip("`'\".,;:()")
        if not token or token.lower() in seen or len(token) < 2:
            return
        if token.lower() in lex.IDENTIFIER_STOPWORDS:
            return
        seen.add(token.lower())
        out.append((token, kind))

    for match in lex.RE_BACKTICKED.finditer(prompt):
        inner = match.group(1)
        kind = "file" if lex.RE_PATH.fullmatch(inner) else "symbol"
        add(inner, kind)
    for match in lex.RE_PATH.finditer(prompt):
        add(match.group(1), "file")
    for match in lex.RE_ENDPOINT.finditer(prompt):
        add(match.group(1), "endpoint")
    for match in lex.RE_DOTTED.finditer(prompt):
        add(match.group(1), "symbol")
    for match in lex.RE_CALL.finditer(prompt):
        add(match.group(1), "symbol")
    for match in lex.RE_QUOTED.finditer(prompt):
        add(match.group(1), "symbol")
    for match in lex.RE_IDENTIFIER.finditer(prompt):
        add(match.group(1), "symbol")

    return out[:_MAX_TOKENS]


def _targets(prompt: str, index: RepoIndex | None) -> list[Target]:
    targets: list[Target] = []

    for token, kind in _candidate_tokens(prompt):
        target = Target(token=token, kind=kind)

        if index is not None and index.is_built:
            if kind == "file":
                normalized = index.normalize(token)
                if index.file_exists(normalized):
                    target.resolved = True
                    target.paths = [normalized]
                    target.evidence = [EvidenceRef(source="filesystem", path=normalized)]
                else:
                    matches = [p for p in index.files if p.endswith("/" + token) or p == token]
                    if matches:
                        target.resolved = True
                        target.paths = matches[:5]
                        target.evidence = [
                            EvidenceRef(source="filesystem", path=p) for p in matches[:5]
                        ]
            elif kind == "endpoint":
                matches = _endpoint_matches(token, index)
                if matches:
                    target.resolved = True
                    target.paths = matches[:5]
                    target.evidence = [
                        EvidenceRef(source="filesystem", path=p, note=f"matches {token}")
                        for p in matches[:5]
                    ]
            else:
                symbols = index.find_symbol(token)
                if symbols:
                    target.resolved = True
                    target.paths = sorted({s.path for s in symbols})[:5]
                    target.evidence = [
                        EvidenceRef(
                            source="ast", path=s.path, line=s.line, symbol=s.qualname,
                            note=s.kind,
                        )
                        for s in symbols[:5]
                    ]

        targets.append(target)

    return targets


def _endpoint_matches(endpoint: str, index: RepoIndex) -> list[str]:
    """Find files plausibly implementing an HTTP path like `/users`.

    Deliberately shallow: a route table is framework-specific, and guessing wrong would
    put a file in expected scope that has nothing to do with the task. Filename and
    literal-string matching is what can be claimed deterministically.
    """
    stem = endpoint.strip("/").split("/")[0]
    if not stem:
        return []
    singular = stem.rstrip("s")
    matches: list[str] = []
    for path, record in index.files.items():
        if record.is_test or not record.parsable:
            continue
        filename = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if filename in (stem, singular):
            matches.append(path)
    return sorted(matches)


def _constraints(prompt: str) -> list[str]:
    """Clauses where the developer said what *not* to do — SPEC §9 constraints."""
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", prompt):
        clean = sentence.strip()
        if not clean:
            continue
        lowered = clean.lower()
        if lex.matching_terms(lowered, lex.CONSTRAINT_MARKERS):
            out.append(clean[:200])
    return out[:10]


def _acceptance(prompt: str) -> list[str]:
    out: list[str] = []
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", prompt):
        clean = sentence.strip()
        lowered = clean.lower()
        if clean and lex.matching_terms(lowered, lex.ACCEPTANCE_MARKERS):
            out.append(clean[:200])
    return out[:10]


def _ambiguities(lowered: str) -> list[str]:
    return sorted(
        set(lex.matching_terms(lowered, lex.AMBIGUITY_TERMS))
        | set(lex.matching_terms(lowered, lex.OPEN_ENDED_TERMS))
    )


def classify_domains(lowered: str, targets: list[Target]) -> tuple[Domain, list[Domain]]:
    """SPEC §10: a task can be ML *and* backend *and* MLOps at once."""
    scores: dict[Domain, float] = dict.fromkeys(Domain, 0.0)

    for domain, keywords in lex.DOMAIN_KEYWORDS.items():
        for keyword in lex.matching_terms(lowered, keywords):
            # Multi-word phrases are far more specific than single words.
            scores[domain] += 2.0 if " " in keyword else 1.0

    resolved_paths = [path.lower() for target in targets for path in target.paths]
    for domain, hints in lex.DOMAIN_PATH_HINTS.items():
        for hint in hints:
            if any(hint in path for path in resolved_paths):
                scores[domain] += 1.5

    ranked = sorted(
        ((domain, score) for domain, score in scores.items() if score > 0 and domain is not Domain.UNKNOWN),
        key=lambda item: -item[1],
    )
    if not ranked:
        return Domain.UNKNOWN, []

    primary = ranked[0][0]
    top_score = ranked[0][1]

    secondary: list[Domain] = [
        domain for domain, score in ranked[1:] if score >= max(1.0, top_score * 0.3)
    ]

    # Domains that travel together (SPEC §10's prediction-API example is ML + backend
    # + MLOps even though nothing in the sentence says "MLOps").
    for affine in lex.DOMAIN_AFFINITY.get(primary, ()):
        if affine is not primary and affine not in secondary:
            secondary.append(affine)

    return primary, secondary[:4]


def _expected_scope(spec: TaskSpec, index: RepoIndex | None) -> list[str]:
    """Files the task plausibly touches — the baseline for SPEC §18's scope check.

    Kept generous on purpose. Expected scope is used to notice a change touching 17
    unrelated files, not to police every edit, and a too-narrow scope would produce
    exactly the nagging SPEC §39 forbids.
    """
    paths: set[str] = set()
    for target in spec.resolved_targets:
        paths.update(target.paths)

    if index is not None and index.is_built:
        # Anything already covering a target file is fair game to edit as well.
        for path in list(paths):
            paths.update(index.tests_for(path))

    return sorted(paths)
