"""The vocabulary the Intent Gateway reasons over (SPEC §9, §10, §12).

This is domain knowledge expressed as data. In Phase 7 it moves into the YAML policy
packs of SPEC §11; keeping it in one module now means the packs have a concrete shape to
be extracted into rather than being designed in the abstract.

Nothing here is a model. Every entry is a keyword or pattern that a human can read,
argue with and correct — which is the point of "deterministic before probabilistic"
(SPEC §46.4).
"""

from __future__ import annotations

import functools
import re

from agentguard.core.enums import Domain


class VerbClass:
    """What kind of change is being asked for.

    `mechanical` verbs are the ones SPEC §2 insists must stay simple: the work is
    well-understood and the risk is in doing it thoroughly, not in designing it.
    `exploratory` verbs are the opposite — the work is mostly figuring out what the work
    is, which is where SPEC §34 says depth is justified.
    """

    RENAME = "rename"
    ADD = "add"
    FIX = "fix"
    REMOVE = "remove"
    REFACTOR = "refactor"
    OPTIMIZE = "optimize"
    MIGRATE = "migrate"
    TEST = "test"
    DOCUMENT = "document"
    UPGRADE = "upgrade"
    CONFIGURE = "configure"
    DESIGN = "design"
    INVESTIGATE = "investigate"
    REVIEW = "review"
    UNKNOWN = "unknown"


# Verbs whose work is well-understood; complexity comes from breadth, not from design.
MECHANICAL_VERBS: frozenset[str] = frozenset(
    {VerbClass.RENAME, VerbClass.DOCUMENT, VerbClass.TEST, VerbClass.REMOVE}
)

# Verbs that inherently require design work before implementation.
EXPLORATORY_VERBS: frozenset[str] = frozenset(
    {VerbClass.DESIGN, VerbClass.INVESTIGATE, VerbClass.MIGRATE, VerbClass.OPTIMIZE}
)

VERB_PATTERNS: dict[str, tuple[str, ...]] = {
    VerbClass.RENAME: ("rename", "renaming", "call it", "change the name"),
    VerbClass.ADD: (
        "add", "implement", "create", "introduce", "build", "support", "enable",
        "write a", "make a", "new endpoint", "expose",
    ),
    VerbClass.FIX: (
        "fix", "bug", "broken", "failing", "error", "crash", "regression", "repair",
        "resolve", "not working", "doesn't work", "incorrect",
    ),
    VerbClass.REMOVE: ("remove", "delete", "drop", "deprecate", "get rid of", "clean up"),
    VerbClass.REFACTOR: ("refactor", "restructure", "reorganize", "extract", "simplify", "tidy"),
    VerbClass.OPTIMIZE: (
        "optimize", "optimise", "speed up", "faster", "performance", "reduce latency",
        "scalable", "scale", "throughput", "memory usage", "efficient",
    ),
    VerbClass.MIGRATE: ("migrate", "port", "move to", "switch to", "replace with", "convert to"),
    VerbClass.TEST: ("test", "tests", "coverage", "unit test", "integration test"),
    VerbClass.DOCUMENT: ("document", "docs", "docstring", "readme", "comment"),
    VerbClass.UPGRADE: ("upgrade", "bump", "update the version", "latest version"),
    VerbClass.CONFIGURE: ("configure", "config", "set up", "setup", "wire up", "env var"),
    VerbClass.DESIGN: ("design", "architect", "architecture", "plan", "propose", "rfc"),
    VerbClass.INVESTIGATE: (
        "investigate", "why", "debug", "diagnose", "figure out", "look into",
        "understand", "audit", "analyse", "analyze",
    ),
    VerbClass.REVIEW: ("review", "check", "verify", "look at"),
}


DOMAIN_KEYWORDS: dict[Domain, tuple[str, ...]] = {
    Domain.BACKEND: (
        "api", "endpoint", "route", "handler", "controller", "middleware", "rest",
        "graphql", "grpc", "request", "response", "server", "service", "http",
        "pagination", "serializer", "validation", "webhook",
    ),
    Domain.FRONTEND: (
        "component", "ui", "ux", "css", "style", "button", "form", "page", "render",
        "react", "vue", "svelte", "angular", "browser", "dom", "hook", "state management",
        "accessibility", "responsive", "layout",
    ),
    Domain.DATABASE: (
        "database", "db", "sql", "query", "schema", "migration", "table", "column",
        "index", "postgres", "mysql", "sqlite", "mongodb", "orm", "sqlalchemy",
        "prisma", "transaction", "foreign key",
    ),
    Domain.DISTRIBUTED_SYSTEMS: (
        "distributed", "microservice", "microservices", "across services",
        "multiple services", "consistency", "consensus", "replication", "sharding",
        "partition", "queue", "kafka", "event bus", "saga", "idempoten",
        "horizontally scalable", "horizontal scaling", "load balanc",
    ),
    Domain.ML_ENGINEERING: (
        "model", "training", "train", "inference", "prediction", "predict", "feature",
        "dataset", "accuracy", "precision", "recall", "f1", "hyperparameter", "epoch",
        "pytorch", "tensorflow", "sklearn", "scikit", "embedding", "fine-tune",
    ),
    Domain.LLM: (
        "llm", "prompt", "token", "gpt", "claude", "gemini", "completion", "rag",
        "retrieval", "context window", "temperature", "chain", "agent", "openai",
        "anthropic", "hallucinat",
    ),
    Domain.COMPUTER_VISION: (
        "image", "vision", "cnn", "segmentation", "detection", "bounding box",
        "opencv", "pixel", "augmentation", "yolo",
    ),
    Domain.DATA_PIPELINE: (
        "pipeline", "etl", "elt", "ingest", "batch job", "airflow", "dagster", "spark",
        "dataframe", "parquet", "warehouse", "dbt",
    ),
    Domain.MLOPS: (
        "deploy the model", "model registry", "model version", "drift", "monitoring",
        "serving", "mlflow", "sagemaker", "experiment tracking", "rollback",
        "production-ready", "production ready", "observability",
    ),
    Domain.CLOUD: (
        "aws", "gcp", "azure", "lambda", "s3", "ec2", "cloud", "terraform",
        "infrastructure", "iam", "vpc", "cloudformation",
    ),
    Domain.DOCKER: ("docker", "dockerfile", "container", "image build", "compose"),
    Domain.KUBERNETES: ("kubernetes", "k8s", "pod", "helm", "ingress", "kubectl", "deployment yaml"),
    Domain.AUTHENTICATION: (
        "auth", "authentication", "authorization", "login", "logout", "session",
        "jwt", "oauth", "sso", "permission", "role", "rbac", "password", "credential",
        "signin", "sign in", "token refresh",
    ),
    Domain.SECRETS: ("secret", "api key", "credential", "vault", "encryption", "encrypt", ".env"),
    Domain.TESTING: ("test", "pytest", "jest", "vitest", "coverage", "fixture", "mock", "e2e"),
    Domain.DOCUMENTATION: ("readme", "documentation", "docs", "changelog", "docstring"),
}

# Path fragments that indicate a domain regardless of prose (SPEC §10: domain is a
# property of the code being touched, not only of the words used).
DOMAIN_PATH_HINTS: dict[Domain, tuple[str, ...]] = {
    Domain.BACKEND: ("/api/", "/routes/", "/handlers/", "/controllers/", "/endpoints/", "/server/"),
    Domain.FRONTEND: ("/components/", "/pages/", "/views/", "/styles/", ".tsx", ".jsx", ".css", ".scss"),
    Domain.DATABASE: ("/migrations/", "/models/", "/schema", "/repositories/", ".sql"),
    Domain.ML_ENGINEERING: ("/models/", "/training/", "/inference/", "/features/", ".ipynb"),
    Domain.DATA_PIPELINE: ("/pipelines/", "/dags/", "/etl/"),
    Domain.CLOUD: ("/terraform/", "/infra/", ".tf"),
    Domain.DOCKER: ("dockerfile", "docker-compose"),
    Domain.KUBERNETES: ("/k8s/", "/helm/", "/manifests/"),
    Domain.AUTHENTICATION: ("/auth/", "/login", "/session", "middleware/auth"),
    Domain.TESTING: ("/tests/", "/test/", "__tests__", "conftest.py"),
}

# When a domain is primary, these are almost always in play too (SPEC §10's example:
# "Change the prediction API" is ML *and* backend *and* MLOps).
DOMAIN_AFFINITY: dict[Domain, tuple[Domain, ...]] = {
    Domain.ML_ENGINEERING: (Domain.MLOPS,),
    Domain.LLM: (Domain.MLOPS,),
    Domain.COMPUTER_VISION: (Domain.ML_ENGINEERING,),
    Domain.AUTHENTICATION: (Domain.SECRETS,),
    Domain.KUBERNETES: (Domain.CLOUD,),
    Domain.DOCKER: (Domain.CLOUD,),
    Domain.DISTRIBUTED_SYSTEMS: (Domain.BACKEND,),
}


# -- risk vocabularies ------------------------------------------------------------

DATA_RISK_TERMS: tuple[str, ...] = (
    "migration", "migrate", "schema", "drop table", "drop column", "alter table",
    "delete", "truncate", "backfill", "data loss", "pii", "personal data", "gdpr",
    "database", "production data", "seed",
)

SECURITY_RISK_TERMS: tuple[str, ...] = (
    "auth", "authentication", "authorization", "password", "secret", "token", "jwt",
    "oauth", "session", "permission", "credential", "encrypt", "decrypt", "crypto",
    "vulnerability", "injection", "xss", "csrf", "sanitize", "api key", "private key",
    "certificate", "tls", "ssl",
)

IRREVERSIBLE_TERMS: tuple[str, ...] = (
    "migration", "drop", "delete", "truncate", "remove", "deprecate", "breaking change",
    "rename the column", "production", "deploy", "release", "publish", "irreversible",
)

ARCHITECTURE_TERMS: tuple[str, ...] = (
    "architecture", "architectural", "redesign", "restructure", "abstraction", "interface",
    "service layer", "decouple", "coupling", "boundary", "boundaries", "pattern",
    "dependency injection", "plugin", "extensible", "framework", "rewrite", "refactor the",
    "across services", "multiple services", "microservice", "distributed", "horizontally",
    "scalable", "scale out",
)

# Words that signal the developer has not said what they actually want. High uncertainty
# is a reason for *more* planning, not less (SPEC §34).
#
# Split deliberately. "Fix the login bug properly" is a bug fix with an adverb attached;
# it must stay a bug fix. "Make the inference service production-ready" sets an open-ended
# quality bar with no stated finish line, and that genuinely is a deep task (SPEC §34).
# Only the second kind may raise planning depth on its own.
AMBIGUITY_TERMS: tuple[str, ...] = (
    "properly", "correctly", "better", "improve", "clean", "nice", "good", "modern",
    "somehow", "etc", "and so on", "as needed", "appropriate", "reasonable", "sensible",
)

OPEN_ENDED_TERMS: tuple[str, ...] = (
    "production-ready", "production ready", "best practice", "best practices",
    "make sure it works", "handle everything", "handle all", "all the cases",
    "all edge cases", "future-proof", "enterprise", "world-class", "optimal",
    "robust", "bulletproof", "battle-tested", "at scale", "fully",
)

# Named infrastructure and libraries. Mentioning one in an "add"/"migrate" task usually
# means a new dependency, which SPEC §16 says to challenge.
TECH_NAMES: tuple[str, ...] = (
    "redis", "memcached", "kafka", "rabbitmq", "celery", "elasticsearch", "opensearch",
    "postgres", "postgresql", "mysql", "mongodb", "cassandra", "dynamodb", "clickhouse",
    "nginx", "envoy", "consul", "etcd", "zookeeper", "prometheus", "grafana", "datadog",
    "sentry", "temporal", "airflow", "dagster", "spark", "flink", "snowflake",
    "react-query", "redux", "zustand", "tailwind", "prisma", "drizzle", "trpc",
)

BREADTH_TERMS: tuple[str, ...] = (
    "all", "every", "everywhere", "entire", "whole", "across", "throughout",
    "globally", "codebase", "project-wide", "each of the",
)

CONSTRAINT_MARKERS: tuple[str, ...] = (
    "don't", "do not", "never", "avoid", "without", "must not", "no need to",
    "only", "just", "keep", "preserve", "maintain", "instead of", "rather than",
    "make sure not to", "leave",
)

ACCEPTANCE_MARKERS: tuple[str, ...] = (
    "so that", "should", "must", "ensure", "make sure", "expect", "verify that",
    "it needs to", "the result should", "acceptance",
)


# -- term matching ----------------------------------------------------------------


@functools.lru_cache(maxsize=4096)
def _term_pattern(term: str) -> re.Pattern[str]:
    """Match at a word start, with different tail rules for words and phrases.

    Plain substring matching is wrong in both directions, and two real bugs proved it:

    * ``"all" in "horizontally"`` was true, inventing breadth language in a prompt that
      contained none.
    * ``"make a" in "make authentication ..."`` was true, classifying "make
      authentication scalable" as an *add* task.

    But requiring a closing boundary everywhere would stop ``auth`` matching
    ``authentication``, which is wanted. So: single words anchor at the start of a word
    and may run on; phrases must end on a word boundary too, because their final word is
    always meant as a complete word.
    """
    tail = r"\b" if _is_phrase(term) else ""
    return re.compile(r"\b" + re.escape(term) + tail, re.IGNORECASE)


def _is_phrase(term: str) -> bool:
    return " " in term or "-" in term


def contains(text: str, term: str) -> bool:
    return _term_pattern(term).search(text) is not None


def matching_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if contains(text, term)]


def first_position(text: str, term: str) -> int:
    match = _term_pattern(term).search(text)
    return match.start() if match else -1


# -- token patterns ---------------------------------------------------------------

RE_BACKTICKED = re.compile(r"`([^`\n]{1,120})`")
RE_QUOTED = re.compile(r"[\"']([A-Za-z_][\w./-]{2,80})[\"']")
RE_PATH = re.compile(r"\b((?:[\w.-]+/)+[\w.-]+\.\w{1,6})\b")
RE_ENDPOINT = re.compile(r"(?<![\w.])(/[a-z][\w-]*(?:/[\w{}:<>-]+)*)\b")
RE_DOTTED = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b")
RE_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(\s*\)")
RE_IDENTIFIER = re.compile(
    r"\b([a-z]+(?:_[a-z0-9]+)+"  # snake_case
    r"|[a-z]+(?:[A-Z][a-z0-9]*)+"  # camelCase
    r"|[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+)\b"  # PascalCase
)

# Words that look like identifiers but are ordinary English. Without this list, "GitHub"
# or "JavaScript" would be treated as unresolved symbols and inflate uncertainty.
IDENTIFIER_STOPWORDS: frozenset[str] = frozenset(
    {
        "javascript", "typescript", "github", "gitlab", "postgresql", "mysql", "mongodb",
        "graphql", "openapi", "restapi", "webapp", "frontend", "backend", "database",
        "codebase", "filename", "username", "hostname", "endpoint", "middleware",
        "kubernetes", "dockerfile", "namespace", "workflow", "changelog", "readme",
        "dropdown", "checkbox", "textarea", "timestamp", "boolean", "nullable",
        "async", "await", "todo", "fixme", "setup", "cleanup", "runtime", "buildtime",
    }
)
