"""Evidence Engine (SPEC §14, §15, §16).

    AGENT CLAIM -> Evidence lookup -> FOUND: validate / NOT FOUND: UNVERIFIED

One call per proposed action: extract the claims it makes, resolve them against the
repository, and hand back findings. Deciding what to *do* with those findings belongs to
the Action Validator (Phase 4); deciding whether to *say* them belongs to the ledger.
"""

from __future__ import annotations

import logging

from agentguard.core.enums import ChallengeCategory
from agentguard.core.events import AgentEvent
from agentguard.core.models import EvidenceRef, Finding
from agentguard.evidence import extractors, resolvers
from agentguard.evidence.models import Resolution
from agentguard.repo.index import RepoIndex

log = logging.getLogger(__name__)

_CATEGORY_BY_KIND = {
    "attribute_on_type": ChallengeCategory.EVIDENCE,
    "symbol_exists": ChallengeCategory.EVIDENCE,
    "module_importable": ChallengeCategory.ASSUMPTION,
    "dependency_declared": ChallengeCategory.DEPENDENCY,
    "file_exists": ChallengeCategory.EVIDENCE,
}


def to_finding(resolution: Resolution) -> Finding:
    """Resolution -> Finding, the hand-off from evidence to the challenge layer."""
    claim = resolution.claim
    refs = list(resolution.evidence)
    if claim.path:
        refs.append(
            EvidenceRef(
                source="proposed-edit",
                path=claim.path,
                line=claim.line,
                snippet=claim.snippet,
                note="the line being written",
            )
        )
    return Finding(
        category=_CATEGORY_BY_KIND.get(str(claim.kind), ChallengeCategory.EVIDENCE),
        verdict=resolution.verdict,
        severity=resolution.severity,
        subject=claim.subject,
        summary=resolution.summary,
        detail=resolution.detail,
        evidence=refs,
        suggestion=(
            f"Existing members: {', '.join(resolution.alternatives)}"
            if resolution.alternatives
            else ""
        ),
    )


def check(event: AgentEvent, index: RepoIndex) -> list[Finding]:
    """Findings about one proposed action. Never raises; an empty list means "no opinion"."""
    if not index.is_built:
        # The index is still warming. Nothing is known, so nothing can be contradicted.
        return []

    try:
        claims, local_facts = extractors.claims_for_event(event, index.root)
    except Exception:
        log.exception("agentguard: claim extraction failed for %s", event.tool)
        return []

    if not claims:
        return []

    edited_path = ""
    raw_path = event.arg("file_path") or event.arg("notebook_path")
    if isinstance(raw_path, str) and raw_path:
        edited_path = index.normalize(raw_path)

    resolutions = resolvers.resolve_all(claims, index, local_facts, edited_path)
    return [to_finding(resolution) for resolution in resolutions if resolution.is_problem]
