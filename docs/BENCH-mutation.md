# Mutation benchmark — does the evidence engine actually work?

**Method:** take working code from real repositories, programmatically introduce a
reference that genuinely does not exist, ask AgentGuard whether it notices. Ground truth is
known by construction. **Zero agent sessions.**

    recall    = seeded hallucinations flagged / seeded hallucinations
    precision = correct flags / all flags (unmutated code must stay silent)

## Result

| Repository | Recall | Precision |
|---|---|---|
| AgentGuard (plain Python) | **97.2%** | **100%** |
| A real framework-heavy monorepo | **88.8%** | **100%** |

**Zero false positives on unmutated code**, in either repository, at every stage — matching
the independent 5,750-claim audit from Phase 3.

## How it got there

The first measurement was 0.6% recall on the real codebase. Two fixes, each found by
looking at what was missed rather than by guessing:

| | Real monorepo recall |
|---|---|
| Initial | 0.6% |
| After enums | 13.8% |
| After ORM / pydantic bases | **88.8%** |

**Enums.** `class Mode(str, Enum)` was unknowable because `str` is an unresolvable base, so
AgentGuard was silent on every enum in the codebase — 159 of 160 misses. An enum's members
are entirely in its own body; a value mixin hides nothing. Ignored only alongside an `Enum`
base, so an ordinary class inheriting `str` still counts as unknown.

**Generated bases.** 138 of the remaining misses were SQLAlchemy models: `class
KnowledgeNode(Base)` where `Base` comes from `declarative_base()` and is invisible from
source. Same argument — the model's real attributes are its own column declarations.

That second fix required asserting what those frameworks contribute, and anything missed
from that list becomes a *false positive*. So a framework base is only seen through when
the defining file actually imports the framework, and the API surface is enumerated
explicitly. Precision held at 100%, which is the check that mattered.

## What this does and does not establish

**Establishes:** the evidence engine detects ~9 in 10 hallucinated references in real
framework-heavy code, and does not cry wolf. The mechanism works.

**Does not establish:** that it improves outcomes with a real agent attached. A detector
that fires correctly still has to change what the agent does, and a capable model often
catches its own mistake before AgentGuard sees it — run 01 showed exactly that. The
behavioural question needs live sessions and remains open.

**Limit worth stating:** the mutations are ones AgentGuard could in principle catch —
references to things that genuinely do not exist. This measures how well it finds what it
was designed to find, not how often agents fail in ways it was never built for.
