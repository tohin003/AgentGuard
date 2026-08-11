"""The vocabulary of AgentGuard.

Every enum here maps directly onto a section of the SPEC. Keeping them in one module
means the SPEC's terms and the code's terms cannot silently drift apart.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class EventType(StrEnum):
    """Normalized, agent-agnostic event names (SPEC §23).

    Adapters translate their host's native events into these; the core never learns
    which agent produced an event.
    """

    SESSION_START = "session_start"
    USER_PROMPT = "user_prompt"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_FAILURE = "post_tool_failure"
    STOP = "stop"
    SUBAGENT_STOP = "subagent_stop"
    SESSION_END = "session_end"


class DecisionAction(StrEnum):
    """What AgentGuard does about a proposed action (SPEC §18).

    CHALLENGE is the important one: it is not a refusal, it is a question handed back
    to the host agent so the host's own intelligence resolves it (SPEC §21).
    """

    ALLOW = "allow"
    BLOCK = "block"
    CHALLENGE = "challenge"
    MODIFY = "modify"
    REQUEST_REVIEW = "request_review"


class Verdict(StrEnum):
    """Outcome of evaluating a claim or proposal (SPEC §16).

    Note that both OVERENGINEERED *and* UNDERENGINEERED exist. AgentGuard is a
    proportionality system, not an anti-complexity system (SPEC §17).
    """

    SUPPORTED = "supported"
    SUPPORTED_WITH_RISK = "supported_with_risk"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTRADICTED = "contradicted"
    OVERENGINEERED = "overengineered"
    UNDERENGINEERED = "underengineered"
    REQUIRES_HUMAN = "requires_human"


class ChallengeCategory(StrEnum):
    """Challenge taxonomy (SPEC §16)."""

    ASSUMPTION = "assumption"
    EVIDENCE = "evidence"
    SCOPE = "scope"
    OVERENGINEERING = "overengineering"
    UNDERENGINEERING = "underengineering"
    CONSISTENCY = "consistency"
    DEPENDENCY = "dependency"
    RISK = "risk"
    REGRESSION = "regression"
    TESTABILITY = "testability"


class PlanningDepth(StrEnum):
    """Planning budget bands (SPEC §12).

    Score → band:  0-20 DIRECT · 21-40 LIGHT · 41-70 STRUCTURED · 71-100 DEEP
    """

    DIRECT = "direct"
    LIGHT = "light"
    STRUCTURED = "structured"
    DEEP = "deep"

    @classmethod
    def from_score(cls, score: float) -> PlanningDepth:
        if score <= 20:
            return cls.DIRECT
        if score <= 40:
            return cls.LIGHT
        if score <= 70:
            return cls.STRUCTURED
        return cls.DEEP


class GateResult(StrEnum):
    """Completion gate outcomes (SPEC §19)."""

    PASS = "pass"
    INCOMPLETE = "incomplete"
    VERIFICATION_FAILED = "verification_failed"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class EscalationLevel(IntEnum):
    """Reasoning escalation ladder (SPEC §7).

    Most everyday operations must stay at L0-L1. L2 hands the problem to the host
    agent; AgentGuard still makes no LLM call of its own (SPEC §6, §22).
    """

    DETERMINISTIC = 0
    REPOSITORY = 1
    HOST_CHALLENGE = 2
    DEEP_VERIFICATION = 3


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ClaimKind(StrEnum):
    """Kinds of claim the Evidence Engine can ground (SPEC §14, §15)."""

    SYMBOL_EXISTS = "symbol_exists"
    ATTRIBUTE_ON_TYPE = "attribute_on_type"
    FILE_EXISTS = "file_exists"
    MODULE_IMPORTABLE = "module_importable"
    DEPENDENCY_DECLARED = "dependency_declared"
    ENDPOINT_EXISTS = "endpoint_exists"
    CONFIG_KEY_EXISTS = "config_key_exists"
    TEST_EXISTS = "test_exists"


class Domain(StrEnum):
    """Engineering domains (SPEC §10, §11).

    Different domains demand different reasoning; policy packs are keyed on these.
    """

    BACKEND = "backend"
    FRONTEND = "frontend"
    DATABASE = "database"
    DISTRIBUTED_SYSTEMS = "distributed_systems"
    ML_ENGINEERING = "ml_engineering"
    LLM = "llm"
    COMPUTER_VISION = "computer_vision"
    DATA_PIPELINE = "data_pipeline"
    MLOPS = "mlops"
    CLOUD = "cloud"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    AUTHENTICATION = "authentication"
    SECRETS = "secrets"
    TESTING = "testing"
    DOCUMENTATION = "documentation"
    UNKNOWN = "unknown"
