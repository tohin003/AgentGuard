"""Test-runner detection and result parsing (SPEC §19, §20).

A deliberate design choice sits at the centre of this module: **AgentGuard reads the
agent's own test output rather than running tests itself.**

The reasons are practical and they compound. Running a second test suite concurrently with
the agent's work invites conflicts over ports, fixtures, databases and temp files. It is
slow, on a path that is supposed to be invisible. And it is unnecessary: when the agent
runs `pytest` the output arrives in `PostToolUse`, and that output is ground truth about
whether the tests passed — far better evidence than a claim in a summary message.

It also catches the failure the SPEC cares most about. An agent that runs the suite, sees
two failures, and then says "all tests pass" has been contradicted by an artefact it
produced itself. No re-execution required.

Running the suite independently remains available for Level-3 verification
(`verification.run_tests`), off by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from agentguard.repo.index import RepoIndex


@dataclass(slots=True)
class TestRunner:
    name: str  # pytest | jest | vitest | go | cargo | npm
    command: str  # how the agent would invoke it
    evidence: str  # what made us believe it exists


@dataclass(slots=True)
class TestOutcome:
    """What a test command's output actually says."""

    runner: str
    passed: bool | None  # None = ran, but the outcome could not be determined
    total: int = 0
    failed: int = 0
    summary: str = ""
    command: str = ""

    @property
    def is_failure(self) -> bool:
        return self.passed is False


@dataclass(slots=True)
class VerificationState:
    """Everything observed about verification during one task."""

    outcomes: list[TestOutcome] = field(default_factory=list)
    commands_seen: list[str] = field(default_factory=list)

    @property
    def ran_tests(self) -> bool:
        return bool(self.outcomes)

    @property
    def any_failed(self) -> bool:
        return any(outcome.is_failure for outcome in self.outcomes)

    @property
    def all_passed(self) -> bool:
        return bool(self.outcomes) and all(o.passed is True for o in self.outcomes)

    def latest_failure(self) -> TestOutcome | None:
        return next((o for o in reversed(self.outcomes) if o.is_failure), None)


# -- detection --------------------------------------------------------------------


def detect_runners(index: RepoIndex) -> list[TestRunner]:
    """Which test runners this repository actually has.

    Used to answer a question the Completion Gate must not get wrong: is "no tests were
    run" a problem, or does this project simply have no tests to run? Demanding tests
    that cannot exist would be exactly the nagging SPEC §39 forbids.
    """
    runners: list[TestRunner] = []
    files = index.files

    has_python_tests = any(r.is_test and r.lang == "python" for r in files.values())
    if has_python_tests or "pytest.ini" in files or "tox.ini" in files:
        runners.append(TestRunner("pytest", "pytest", "python test files present"))
    elif "pyproject.toml" in files:
        try:
            text = (index.root / "pyproject.toml").read_text(encoding="utf-8")
            if "[tool.pytest" in text or "pytest" in text:
                runners.append(TestRunner("pytest", "pytest", "pytest configured in pyproject.toml"))
        except OSError:
            pass

    if "package.json" in files:
        try:
            import json

            data = json.loads((index.root / "package.json").read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            dev = {**(data.get("devDependencies") or {}), **(data.get("dependencies") or {})}
            if "vitest" in dev:
                runners.append(TestRunner("vitest", "npx vitest run", "vitest in package.json"))
            elif "jest" in dev:
                runners.append(TestRunner("jest", "npx jest", "jest in package.json"))
            elif "test" in scripts:
                runners.append(TestRunner("npm", "npm test", "test script in package.json"))
        except (OSError, ValueError):
            pass

    if "go.mod" in files:
        runners.append(TestRunner("go", "go test ./...", "go.mod present"))
    if "Cargo.toml" in files:
        runners.append(TestRunner("cargo", "cargo test", "Cargo.toml present"))

    return runners


# -- recognising a test command ---------------------------------------------------

_TEST_COMMAND = re.compile(
    r"\b("
    r"pytest|py\.test|python\s+-m\s+pytest|unittest"
    r"|jest|vitest|mocha|npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test|yarn\s+test"
    r"|go\s+test|cargo\s+test|gradle\s+test|mvn\s+test|rspec|phpunit|dotnet\s+test"
    r")\b"
)


def is_test_command(command: str) -> bool:
    return bool(_TEST_COMMAND.search(command or ""))


def runner_for_command(command: str) -> str:
    match = _TEST_COMMAND.search(command or "")
    if not match:
        return "unknown"
    token = match.group(1).split()[0]
    return {"py.test": "pytest", "python": "pytest", "npm": "npm", "pnpm": "npm", "yarn": "npm"}.get(
        token, token
    )


# -- parsing output ---------------------------------------------------------------

_PYTEST_SUMMARY = re.compile(
    r"={2,}\s*(?P<body>[^=\n]*?(?:passed|failed|error|no tests ran)[^=\n]*?)\s*={2,}",
    re.IGNORECASE,
)
_PYTEST_COUNTS = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed)", re.IGNORECASE)
_JEST_TESTS = re.compile(r"Tests:\s+(?P<body>.+)")
_JEST_COUNTS = re.compile(r"(\d+)\s+(passed|failed|skipped|todo)", re.IGNORECASE)
_CARGO = re.compile(r"test result:\s*(?P<status>ok|FAILED)\.\s*(?P<counts>[^\n]*)")
_GO_FAIL = re.compile(r"^(FAIL|ok)\s+\S+", re.MULTILINE)


def parse_output(command: str, output: str) -> TestOutcome:
    """Read a test command's output. `passed=None` means we could not tell.

    Never guesses. An unrecognised format yields `None`, and the Completion Gate treats
    "could not tell" as "not verified" rather than as a failure — accusing an agent of
    breaking tests on the strength of an unparsed string would be worse than silence.
    """
    runner = runner_for_command(command)
    text = output or ""
    outcome = TestOutcome(runner=runner, passed=None, command=command.strip()[:200])

    if runner in ("pytest", "unittest"):
        counts = {kind.lower(): int(n) for n, kind in _PYTEST_COUNTS.findall(text)}
        summary_match = _PYTEST_SUMMARY.search(text)
        if summary_match:
            outcome.summary = summary_match.group("body").strip()[:200]
        if counts:
            outcome.failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
            outcome.total = sum(counts.values())
            outcome.passed = outcome.failed == 0
        elif "no tests ran" in text.lower():
            outcome.passed = None
            outcome.summary = outcome.summary or "no tests ran"
        return outcome

    if runner in ("jest", "vitest", "npm", "mocha"):
        tests_line = _JEST_TESTS.search(text)
        if tests_line:
            body = tests_line.group("body")
            outcome.summary = body.strip()[:200]
            counts = {kind.lower(): int(n) for n, kind in _JEST_COUNTS.findall(body)}
            outcome.failed = counts.get("failed", 0)
            outcome.total = sum(counts.values())
            outcome.passed = outcome.failed == 0
        elif "FAIL" in text:
            outcome.passed = False
            outcome.summary = "reported FAIL"
        return outcome

    if runner == "cargo":
        match = _CARGO.search(text)
        if match:
            outcome.passed = match.group("status") == "ok"
            outcome.summary = match.group("counts").strip()[:200]
        return outcome

    if runner == "go":
        results = _GO_FAIL.findall(text)
        if results:
            outcome.passed = "FAIL" not in results
            outcome.summary = f"{results.count('ok')} package(s) ok, {results.count('FAIL')} failed"
        return outcome

    # Unknown runner: look only for signals strong enough to be unambiguous.
    lowered = text.lower()
    if "traceback (most recent call last)" in lowered or re.search(r"\b\d+ failed\b", lowered):
        outcome.passed = False
        outcome.summary = "failure reported in output"
    return outcome


def affected_tests(index: RepoIndex, changed: set[str], limit: int = 20) -> list[str]:
    """Tests covering the changed files, plus tests of their direct dependents."""
    tests: set[str] = set()
    for path in changed:
        tests.update(index.tests_for(path))
        for dependent in index.dependents_of(path):
            tests.update(index.tests_for(dependent))
    return sorted(tests)[:limit]


def suggested_command(runners: list[TestRunner], tests: list[str]) -> str:
    """The most specific command that would verify these changes."""
    if not runners:
        return ""
    runner = runners[0]
    if runner.name == "pytest" and tests:
        return f"pytest {' '.join(tests[:6])}"
    return runner.command


def workspace_root(index: RepoIndex) -> Path:
    return index.root
