# AgentGuard — Host-Powered AI Agent Reliability & Reasoning Layer

## 1. Project Vision

Build a lightweight, model-agnostic **AI Agent Reliability / Reasoning Control Layer** that attaches to existing AI coding agents and LLM-powered development environments such as:

- Claude Code
- Codex CLI
- Cursor
- GitHub Copilot
- OpenHands
- Other compatible coding agents
- Eventually ChatGPT, Claude, Gemini and other web-based LLM interfaces through browser/extension integrations where technically possible

The system must **NOT have its own mandatory LLM**.

Instead, it should use the intelligence of the LLM/agent it is connected to.

The best mental model is:

> **Venom + its host.**

The host already has the intelligence. AgentGuard attaches to it and makes that intelligence more disciplined, evidence-driven, appropriately skeptical, efficient and reliable.

The primary LLM remains the "brain".

AgentGuard becomes the:

- second brain
- skeptic
- evidence checker
- complexity governor
- engineering discipline layer
- hallucination guard
- verification layer
- domain-aware reasoning assistant

The objective is NOT to replace the agent.

The objective is:

> **Make existing AI agents substantially harder to fool, hallucinate, over-engineer, under-engineer, or incorrectly declare a task complete.**

---

# 2. Core Philosophy

The central principle of the project is:

> **Do not make the primary LLM smarter. Make it harder for the primary LLM to be wrong.**

Another important principle:

> **Do not optimize for simplicity. Optimize for the least complex solution that is sufficiently correct for the requirements, architecture, risk and evidence.**

This distinction is extremely important.

The system must NOT blindly force every task into a simple solution.

For example:

### Simple task

"Rename this function."

AgentGuard should encourage:

```text
Find references
→ Rename
→ Run tests
→ Done
```

It should NOT encourage:

```text
Architecture analysis
→ abstraction redesign
→ service layer
→ dependency restructuring
→ extensive planning
```

### Complex task

"Make authentication horizontally scalable across multiple services."

AgentGuard SHOULD encourage deeper reasoning around:

- architecture
- session state
- consistency
- caching
- failure modes
- security
- concurrency
- deployment
- observability
- rollback
- testing
- load testing

Therefore:

> **Planning depth must be proportional to complexity, uncertainty, risk and architectural impact.**

---

# 3. Problem Being Solved

Modern coding agents are extremely capable, but they can still:

- hallucinate files
- hallucinate APIs
- invent functions
- invent libraries
- make unsupported assumptions
- misunderstand developer intent
- over-plan trivial tasks
- under-plan complex tasks
- introduce unnecessary abstractions
- modify unrelated files
- introduce unnecessary dependencies
- ignore existing repository patterns
- overlook regressions
- incorrectly claim that a task is complete
- write insufficient tests
- fail to verify their own changes
- confidently continue after making an incorrect assumption

Typical instruction:

> "Don't hallucinate."

is insufficient.

AgentGuard should instead create a system where important decisions are checked against **actual evidence**.

---

# 4. Fundamental Architecture

```text
                         DEVELOPER
                             │
                             ▼
                  ┌─────────────────────┐
                  │    INTENT GATEWAY   │
                  │                     │
                  │ Intent extraction   │
                  │ Requirements        │
                  │ Constraints         │
                  │ Expected outcome    │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ TASK UNDERSTANDING  │
                  │                     │
                  │ Domain              │
                  │ Complexity          │
                  │ Risk                │
                  │ Uncertainty         │
                  │ Blast radius        │
                  │ Reversibility       │
                  └──────────┬──────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │       ENGINEERING BRAIN      │
              │                              │
              │ Repository evidence          │
              │ Existing architecture        │
              │ Existing patterns            │
              │ Dependencies                 │
              │ Domain policies              │
              │ Git history                  │
              └──────────────┬───────────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │  PLANNING GOVERNOR  │
                  │                     │
                  │ Planning budget     │
                  │ Minimal sufficient  │
                  │ approach            │
                  │ Complexity gate     │
                  └──────────┬──────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │  PRIMARY AGENT │
                    │                │
                    │ Claude Code    │
                    │ Codex          │
                    │ Cursor         │
                    │ Copilot        │
                    │ OpenHands      │
                    └───────┬────────┘
                            │
                     Proposed actions
                            │
                            ▼
               ┌─────────────────────────┐
               │    ACTION CHALLENGER    │
               │                         │
               │ Evidence?               │
               │ Assumption?             │
               │ Scope creep?            │
               │ Overengineering?        │
               │ Underengineering?       │
               │ Architecture mismatch?  │
               │ Risk?                   │
               │ Alternative?            │
               └────────────┬────────────┘
                            │
                    ALLOW / CHALLENGE /
                    BLOCK / MODIFY
                            │
                            ▼
                     AGENT EXECUTES
                            │
                            ▼
               ┌─────────────────────────┐
               │   VERIFICATION ENGINE   │
               │                         │
               │ Tests                   │
               │ Static analysis         │
               │ AST                     │
               │ Git diff                │
               │ Contracts               │
               │ Build                   │
               │ Regression              │
               │ Requirement coverage    │
               └────────────┬────────────┘
                            │
                            ▼
                  ┌─────────────────────┐
                  │ COMPLETION GATE     │
                  │                     │
                  │ Evidence-backed?    │
                  │ Tests passed?       │
                  │ Requirements met?   │
                  │ Scope respected?    │
                  └──────────┬──────────┘
                             │
                       PASS / CONTINUE
```

---

# 5. The Venom / Host Model

The architecture should be based around a host-agent relationship.

```text
                    HOST
          ┌──────────────────────┐
          │ Claude / GPT / Gemini│
          │ / Cursor / Codex     │
          └──────────┬───────────┘
                     │
               Intelligence
                     ↕
          ┌──────────────────────┐
          │     AGENTGUARD       │
          │                      │
          │ Evidence             │
          │ Skepticism           │
          │ Constraints          │
          │ Context              │
          │ Planning control     │
          │ Verification         │
          └──────────────────────┘
```

The host supplies:

- reasoning
- natural language understanding
- code generation
- domain knowledge
- problem solving

AgentGuard supplies:

- evidence
- repository intelligence
- constraints
- challenge mechanisms
- complexity analysis
- verification
- deterministic validation
- domain-specific engineering policies

The host remains the brain.

AgentGuard makes that brain more reliable.

---

# 6. No Mandatory LLM Architecture

AgentGuard must be able to operate without owning or hosting its own LLM.

The majority of operations should be deterministic.

```text
                    AgentGuard
                        │
          ┌─────────────┴─────────────┐
          │                           │
   Deterministic Layer          Semantic Layer
          │                           │
          │                           │
   Filesystem                    Host LLM
   Git                           Claude
   AST                           GPT
   Tests                         Gemini
   Static analysis               etc.
   Dependency graph
   API schemas
   Repository structure
   Rules/policies
```

The semantic layer should only be used when semantic reasoning is actually necessary.

The system should prefer:

> **Evidence first → deterministic validation → host LLM only when required.**

---

# 7. Low-Latency Requirement

AgentGuard must be designed for daily use.

It must NOT become a slow proxy that interrupts every agent action.

Target behavior:

```text
Normal operation
─────────────────────────────
Agent
 ↓
AgentGuard
 ↓
Fast deterministic check
 ↓
ALLOW
 ↓
Agent continues
```

Only unusual situations should trigger deeper reasoning.

Use a reasoning escalation system:

```text
LEVEL 0
Deterministic checks
        │
        ▼
LEVEL 1
Repository/context analysis
        │
        ▼
LEVEL 2
Host LLM challenge
        │
        ▼
LEVEL 3
Deep verification / complex reasoning
```

Most everyday operations should remain at Level 0–1.

Only high-risk, ambiguous, contradictory or complex situations should escalate.

---

# 8. Latency Targets

Design toward approximately:

```text
Deterministic check:
< 100 ms target

Repository lookup:
tens to hundreds of milliseconds

Simple task analysis:
< 500 ms where possible

Host LLM invocation:
ONLY when necessary

Long-running tests:
ASYNC

Heavy verification:
ASYNC where possible
```

The user should not feel that AgentGuard is slowing down the coding agent.

Ideal UX:

> Install once → connect agent → forget AgentGuard exists.

It should behave like:

> **ABS / traction control for AI agents.**

Normally invisible.

Intervene immediately when the agent is heading toward a bad decision.

---

# 9. Intent Gateway

The first layer should understand what the developer actually wants.

Example:

```text
Developer:

"Add pagination to /users."
```

AgentGuard should derive something similar to:

```text
Task:
Add pagination to users endpoint

Domain:
Backend API

Complexity:
Low

Risk:
Medium

Expected changes:
Endpoint + query layer

Required verification:
API tests

Unnecessary actions:
New architecture
New service layer
New database
Caching
Repository redesign
```

The intent layer should extract:

- goal
- requirements
- constraints
- expected output
- domain
- implicit risk
- affected area
- acceptance criteria

For simple requests, this should happen quickly.

---

# 10. Domain-Aware Engineering

The system must recognize that different engineering domains require different reasoning.

Example:

```text
"Change the prediction API."
```

### Backend perspective

Consider:

- API contract
- validation
- latency
- errors
- backward compatibility
- database interactions

### ML perspective

Consider:

- model version
- feature compatibility
- distribution shift
- inference latency
- evaluation metrics
- reproducibility

### MLOps perspective

Consider:

- deployment
- model registry
- rollback
- monitoring
- resource utilization
- drift

Therefore the task representation should support:

```json
{
  "primary_domain": "ml_engineering",
  "secondary_domains": [
    "backend",
    "mlops"
  ],
  "risk": "high",
  "planning_depth": "deep"
}
```

---

# 11. Engineering Policy Packs

Domain reasoning should be represented through configurable policy packs.

Example:

```text
engineering/
├── software/
│   ├── backend.yaml
│   ├── frontend.yaml
│   ├── database.yaml
│   └── distributed_systems.yaml
│
├── ai/
│   ├── ml.yaml
│   ├── llm.yaml
│   ├── computer_vision.yaml
│   └── data_pipeline.yaml
│
├── devops/
│   ├── cloud.yaml
│   ├── docker.yaml
│   └── kubernetes.yaml
│
└── security/
    ├── authentication.yaml
    └── secrets.yaml
```

Policies should define:

- important evidence
- typical failure modes
- architectural concerns
- risk factors
- testing expectations
- planning expectations
- acceptable shortcuts
- unacceptable assumptions

The policies should guide the host LLM rather than replace it.

---

# 12. Complexity Engine

The complexity engine is one of the most important components.

Conceptually:

```text
Complexity =
    Scope
  + Dependency Count
  + Architectural Impact
  + Data Risk
  + Security Risk
  + Uncertainty
  + Blast Radius
  + Reversibility
```

Possible scale:

```text
0–20    Direct implementation
21–40   Lightweight plan
41–70   Structured plan
71–100  Deep architectural planning
```

This should NOT be a rigid formula.

It should be a decision system.

For example:

```text
Simple task?
      ↓
Can existing architecture solve it?
      ↓
YES
 ↓
Use minimal approach
```

But:

```text
Simple-looking task?
      ↓
Crosses service boundaries?
      ↓
Security/data/architecture risk?
      ↓
YES
 ↓
Increase planning depth
```

---

# 13. Planning Governor

The planning governor controls how much planning the host agent should perform.

The goal is:

> **Proportional reasoning.**

Not:

> Always make the smallest plan.

And not:

> Always make a detailed plan.

Instead:

```text
Planning Budget =
f(
    complexity,
    uncertainty,
    blast_radius,
    architectural_change,
    risk,
    reversibility
)
```

Example:

### Task

"Rename getUser() to fetchUser()."

```text
Complexity: 2/100

Plan:
1. Find references
2. Rename
3. Run tests
```

### Task

"Introduce distributed session caching."

```text
Complexity: 80+/100

Plan should investigate:
- architecture
- state management
- consistency
- cache strategy
- invalidation
- concurrency
- failure handling
- observability
- security
- deployment
- rollback
- tests
```

AgentGuard must allow complexity when complexity is genuinely justified.

---

# 14. Evidence Engine

This is the primary hallucination-reduction mechanism.

Every important agent claim should be associated with evidence.

Conceptually:

```text
AGENT CLAIM
     │
     ▼
Evidence lookup
     │
 ┌───┴────┐
 │        │
FOUND   NOT FOUND
 │        │
 ▼        ▼
Validate  UNVERIFIED
```

Evidence sources:

- filesystem
- source code
- AST
- imports
- package manifests
- dependency graph
- Git
- Git history
- tests
- API schemas
- OpenAPI
- database schemas
- documentation
- configuration
- runtime output
- build output

Example:

```text
Agent:

"I'll use UserRepository.get_active_users()."
```

AgentGuard searches the repository.

```text
No get_active_users() found.

→ Unsupported claim
→ Challenge agent
```

The challenge sent to the host could be:

```text
AGENTGUARD:

The proposed method
UserRepository.get_active_users()

was not found in the repository.

Evidence:
src/repositories/user.py

Re-evaluate the proposed implementation and
use an existing verified interface unless
you can provide evidence that this method
should be introduced.
```

The host LLM then uses its own intelligence to reconsider.

---

# 15. Evidence-First Reasoning

The fundamental decision flow should be:

```text
Claim
 ↓
Evidence
 ↓
Confidence
 ↓
Action
```

Not:

```text
Claim
 ↓
Action
```

This should apply to:

- APIs
- functions
- classes
- files
- dependencies
- architecture assumptions
- database structures
- configuration
- external services

---

# 16. Contradiction Engine

This is the "small second brain."

Before allowing consequential actions, AgentGuard should challenge the host agent.

Challenge categories:

```text
ASSUMPTION
Does this depend on something unverified?

EVIDENCE
Where did this claim come from?

SCOPE
Is this change actually required?

OVERENGINEERING
Is the proposed solution unnecessarily complex?

UNDERENGINEERING
Are important architectural consequences being ignored?

CONSISTENCY
Does this follow existing repository patterns?

DEPENDENCY
Is a new dependency actually necessary?

RISK
Could this introduce security/data/runtime issues?

REGRESSION
What existing behavior could break?

TESTABILITY
How will this decision be verified?
```

Possible verdicts:

```text
SUPPORTED
SUPPORTED_WITH_RISK
INSUFFICIENT_EVIDENCE
CONTRADICTED
OVERENGINEERED
UNDERENGINEERED
REQUIRES_HUMAN
```

---

# 17. Important: AgentGuard Must Not Always Contradict

A critical design requirement:

AgentGuard must NOT become an annoying system that constantly tells the host:

> "You're wrong."

That would reduce the intelligence of the overall system.

It must be capable of saying:

> **"The complex approach is justified."**

Example:

```text
Agent:
"Introducing a new abstraction is necessary."

AgentGuard:
Existing code indicates this may be unnecessary.

→ Challenge

Agent:
Provides architectural evidence showing
three existing consumers and independent
business rules.

AgentGuard:
Evidence sufficient.

→ ALLOW
```

Therefore AgentGuard is not an anti-complexity system.

It is an:

> **evidence-based proportionality system.**

---

# 18. Action Validation

Before an important tool/action is executed:

```text
Agent proposal
      ↓
Evidence check
      ↓
Scope check
      ↓
Architecture check
      ↓
Risk check
      ↓
Complexity check
      ↓
Decision
```

Possible decisions:

```text
ALLOW
BLOCK
CHALLENGE
MODIFY
REQUEST_REVIEW
```

Example:

```text
Task:
Fix login validation.

Agent attempts:
Modify 17 unrelated files.

AgentGuard:

Expected scope:
authentication module

Actual scope:
17 files

→ SCOPE VIOLATION
→ CHALLENGE
```

---

# 19. Test and Verification Gate

The agent should not simply say:

> "Done."

AgentGuard should determine whether the task has actually been verified.

Completion flow:

```text
Implementation
      ↓
Requirements extracted
      ↓
Acceptance criteria
      ↓
Tests identified
      ↓
Tests executed
      ↓
Static analysis
      ↓
Git diff analysis
      ↓
Requirement coverage
      ↓
Completion Gate
```

Possible outcome:

```text
PASS
```

or:

```text
INCOMPLETE
```

or:

```text
VERIFICATION_FAILED
```

or:

```text
HUMAN_REVIEW_REQUIRED
```

---

# 20. Domain-Specific Testing

Testing should also depend on the domain.

```text
Bug fix
→ Regression test

API change
→ API/contract tests

Algorithm change
→ Unit + edge cases

ML model change
→ Evaluation metrics

LLM pipeline
→ Evaluation cases + failure cases

Database change
→ Migration + integrity tests

Frontend change
→ Component/E2E tests

Infrastructure change
→ Configuration + deployment validation
```

AgentGuard should encourage the host agent to create tests when appropriate.

It should NOT blindly demand unnecessary tests for every trivial action.

---

# 21. Agent Interaction Model

The preferred interaction is:

```text
Developer
   ↓
Primary Agent
   ↓
AgentGuard observes
   ↓
Agent proposes action
   ↓
AgentGuard validates
   ↓
ALLOW
   ↓
Agent executes
```

When necessary:

```text
Developer
   ↓
Primary Agent
   ↓
AgentGuard detects issue
   ↓
Challenge
   ↓
Primary Agent receives challenge
   ↓
Primary Agent reasons again
   ↓
New proposal
   ↓
AgentGuard validates
   ↓
Execute
```

This is crucial.

AgentGuard should use the **host agent's own intelligence** to resolve semantic challenges whenever possible.

---

# 22. AgentGuard Should Not Become a Second LLM

Avoid this architecture:

```text
Developer
 ↓
AgentGuard LLM
 ↓
Claude
 ↓
AgentGuard LLM
 ↓
Claude
```

This causes:

- latency
- cost
- token consumption
- complexity
- competing reasoning systems

Preferred:

```text
Developer
 ↓
Primary Agent
 ↓
Fast deterministic AgentGuard
 ↓
Primary Agent
```

Only exceptional cases should invoke the host's semantic capabilities.

---

# 23. Integration Architecture

The core product should be independent of any particular agent.

```text
                     AgentGuard Core
                            │
       ┌────────────┬───────┼──────────┬──────────┐
       ↓            ↓       ↓          ↓          ↓
      MCP          Hooks    ACP        CLI      Browser
       │            │       │          │          │
       ↓            ↓       ↓          ↓          ↓
    Agents       Claude   Editors     Codex    Chat UIs
                 Cursor
                 Copilot
```

The core should expose a normalized internal event model.

Example:

```json
{
  "event": "pre_tool_use",
  "agent": "claude-code",
  "tool": "Edit",
  "arguments": {},
  "task_id": "123",
  "workspace": "/project"
}
```

Every adapter translates its native events into this common representation.

```text
Claude Hook
    ↓
AgentGuard Event

Cursor Hook
    ↓
AgentGuard Event

Codex Adapter
    ↓
AgentGuard Event

ACP Adapter
    ↓
AgentGuard Event
```

The core does not care which agent generated the event.

---

# 24. Integration Priorities

## 1. Claude Code

High priority.

Claude Code provides lifecycle hooks including pre-tool and post-tool events and mechanisms to allow, deny, challenge or control actions.

This is an ideal first integration.

## 2. Cursor

High priority.

Use its available agent/hook/extension capabilities.

## 3. Codex

High priority.

Build a dedicated adapter around its CLI/local agent interface where possible.

## 4. GitHub Copilot

Later adapter.

## 5. OpenHands

Later adapter.

## 6. ACP

Long-term interoperability layer.

ACP should not be a hard dependency of the first version.

---

# 25. MCP's Role

MCP should be an integration surface, not the entire product.

Use MCP to expose AgentGuard capabilities such as:

```text
inspect_repository
find_symbol
find_evidence
check_complexity
validate_action
get_domain_policy
verify_requirement
run_verification
```

But:

> MCP provides capability access. It does not automatically guarantee control over every agent action.

Therefore:

```text
MCP = Capability Interface

Hooks = Enforcement Interface

AgentGuard Core = Intelligence/Control

Verification = Evidence
```

---

# 26. Hooks' Role

Hooks should be the primary enforcement mechanism where available.

Conceptually:

```text
Agent
 ↓
PreToolUse
 ↓
AgentGuard
 ↓
ALLOW / BLOCK / CHALLENGE
 ↓
Tool executes
 ↓
PostToolUse
 ↓
AgentGuard verification
```

This is much more powerful than merely providing the agent with a skill or prompt.

---

# 27. Skills' Role

Skills/rules can be used as a soft behavioral layer.

Example:

```text
Before implementation:

1. Understand intent.
2. Inspect existing architecture.
3. Do not invent APIs.
4. Prefer evidence.
5. Select proportional planning depth.
6. Verify before completion.
```

But skills are advisory.

Therefore:

```text
Skill = Advice

Hook = Enforcement

Core = Control

Evidence Engine = Ground Truth
```

---

# 28. Plugin / Extension / Connector Strategy

The product should NOT fundamentally be:

- a plugin
- a skill
- an MCP server
- a browser extension
- a Cursor extension

Those should be integration surfaces.

The actual product is:

> **AgentGuard Core**

Recommended architecture:

```text
AgentGuard Core
      │
      ├── CLI
      ├── Local daemon
      ├── MCP server
      ├── Claude adapter
      ├── Cursor adapter
      ├── Codex adapter
      ├── ACP adapter
      └── Browser extension
```

---

# 29. Browser / LLM Chat Support

Eventually support:

- ChatGPT web
- Claude web
- Gemini web
- other LLM chat interfaces

Possible architecture:

```text
Browser
   ↓
AgentGuard Extension
   ↓
Prompt / response interception
   ↓
AgentGuard
   ↓
LLM website
```

However, browser integration should NOT be the foundation.

Web interfaces are more fragile and do not provide the same reliable lifecycle/tool control as coding-agent APIs/hooks.

Treat browser integration as a later phase.

---

# 30. Performance Architecture

AgentGuard should be a lightweight local process.

Recommended:

```text
Developer
    ↓
Coding Agent
    ↓
Local AgentGuard daemon
    ↓
Filesystem / Git / AST / tests
```

Avoid unnecessary network calls.

Core should work offline whenever possible.

Recommended storage:

```text
SQLite
```

Store:

- tasks
- decisions
- evidence
- violations
- agent actions
- verification results
- benchmark metrics

Use caching aggressively.

Examples:

```text
Repository map → cache

AST → cache

Dependency graph → cache

Git state → incremental update

Repeated symbol lookup → cache

Policy → memory
```

---

# 31. Technology Stack

Recommended initial stack:

```text
Language:
Python

Core:
Python

CLI:
Typer

Local service:
FastAPI

Validation:
Pydantic

Repository:
GitPython
ripgrep
tree-sitter
Python AST

Storage:
SQLite

Agent protocol:
MCP SDK

Future interoperability:
ACP

Frontend/dashboard:
React + TypeScript
```

Do NOT introduce a vector database initially.

Start with deterministic repository intelligence.

Only introduce embeddings/vector retrieval if evaluation proves they provide meaningful benefits.

---

# 32. Repository Intelligence

The first repository understanding system should be deterministic.

Build:

```text
Repository
    ↓
File map
    ↓
Symbol map
    ↓
Import graph
    ↓
Dependency graph
    ↓
Test map
    ↓
Configuration map
    ↓
Git history
```

This becomes the evidence base.

Later, semantic retrieval can be added.

---

# 33. Example End-to-End Scenario

Developer says:

```text
"Add pagination to /users."
```

AgentGuard:

```text
Intent:
Add pagination

Domain:
Backend API

Complexity:
Low

Risk:
Medium

Affected area:
users endpoint
```

Repository analysis:

```text
/users endpoint exists
SQLAlchemy query exists
Existing pagination utility exists
```

Agent proposes:

```text
Create UserPaginationService
Create PaginationRepository
Add Redis caching
Refactor API layer
```

AgentGuard checks:

```text
Existing pagination utility found.
Existing query layer sufficient.
No requirement for caching.
```

AgentGuard sends challenge:

```text
Existing pagination utility already satisfies
the required behavior.

Explain why the proposed new abstraction
is necessary.

Otherwise prefer the existing implementation.
```

Host LLM re-evaluates.

It changes its plan:

```text
Use existing pagination utility
Modify /users query
Add API tests
```

AgentGuard allows.

Implementation occurs.

Then:

```text
Run tests
Check API behavior
Inspect git diff
Verify requirement
```

Completion:

```text
PASS
```

This is exactly the behavior the project should produce.

---

# 34. Complex Scenario

Developer:

```text
"Make our inference service production-ready."
```

AgentGuard should NOT immediately say:

> "Keep it simple."

Instead it should detect:

```text
High ambiguity
High scope
High operational risk
ML domain
Backend domain
MLOps domain
```

Planning depth becomes:

```text
DEEP
```

The host agent is encouraged to investigate:

- model versioning
- inference performance
- API reliability
- batching
- resource utilization
- observability
- failure handling
- rollback
- model validation
- security
- deployment
- monitoring
- testing
- load testing

Here complexity is justified.

AgentGuard therefore **supports complexity when evidence demands it.**

---

# 35. The Decision Model

Every significant agent decision should conceptually become:

```text
INTENT
   +
EVIDENCE
   +
DOMAIN
   +
COMPLEXITY
   +
RISK
   +
CONSTRAINTS
   ↓
DECISION
```

Then:

```text
DECISION
   ↓
CHALLENGE
   ↓
EVIDENCE
   ↓
VERIFICATION
   ↓
EXECUTION
```

---

# 36. Benchmarking

This project must have an evaluation framework.

Create:

```text
AgentGuard-Bench
```

Benchmark tasks across:

```text
Backend
Frontend
Database
DevOps
ML
LLM
Computer Vision
Security
Debugging
Refactoring
```

Each benchmark should define:

```text
Intent
Repository
Expected behavior
Acceptable scope
Forbidden scope
Tests
Complexity
Risk
```

Compare:

```text
Agent Alone
      VS
Agent + AgentGuard
```

---

# 37. Metrics

Measure:

```text
Task success rate

Hallucinated API/function/file references

Unsupported assumptions

Tool calls

Token consumption

Files modified

Unnecessary files modified

Planning length

Test failures

Regression rate

False completion rate

Requirement coverage

Human intervention rate

Latency overhead
```

The goal is not only:

> "More accurate."

It is:

> **More reliable with minimal overhead.**

---

# 38. Main Research Question

The project should investigate:

> **Can a lightweight, mostly deterministic control layer improve the reliability, planning efficiency and verification behavior of existing AI coding agents without requiring a dedicated LLM or introducing significant latency?**

Secondary questions:

1. Can evidence-based validation reduce hallucinated APIs and assumptions?
2. Can adaptive planning budgets reduce unnecessary planning?
3. Can domain-aware policies improve engineering decisions?
4. Can contradiction-based feedback improve agent decisions?
5. Can deterministic verification reduce false completion?
6. Can these improvements be achieved without materially slowing down the developer?

---

# 39. Important Product Constraint

The system must NOT become:

> "AI that constantly interrupts AI."

It should be:

> **"AI that silently guards AI and intervenes only when necessary."**

Normal task:

```text
AgentGuard:
████████████████████
Invisible
```

Suspicious task:

```text
AgentGuard:
⚠ Evidence missing
```

Complex task:

```text
AgentGuard:
⚠ Architecture review required
```

Dangerous/unverified action:

```text
AgentGuard:
🛑 Action blocked
```

---

# 40. MVP

Do NOT build everything initially.

### Version 0.1

Build only:

```text
AgentGuard Core
│
├── Repository Scanner
├── Intent/Task Spec
├── Complexity Engine
├── Evidence Engine
├── Action Validator
└── Verification Engine
```

Integrate first with:

```text
Claude Code
Cursor
```

No custom LLM.

No browser extension.

No dashboard initially.

---

# 41. Version 0.2

Add:

```text
MCP server
Codex adapter
Domain policy packs
Decision logs
Caching
Performance instrumentation
```

---

# 42. Version 0.3

Add:

```text
Planning Governor
Contradiction Engine
Adaptive test generation
Completion Gate
AgentGuard-Bench
```

---

# 43. Version 0.4

Add:

```text
GitHub Copilot
OpenHands
ACP
More domain profiles
```

---

# 44. Version 0.5

Add:

```text
IDE dashboard
Browser extension
ChatGPT web integration experiments
Claude web integration experiments
Gemini web integration experiments
```

---

# 45. Final Product Architecture

```text
                         DEVELOPER
                             │
                             ▼
                    ┌─────────────────┐
                    │  AGENTGUARD SDK │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │        AGENTGUARD CORE       │
              │                              │
              │ Intent Engine                │
              │ Complexity Engine            │
              │ Domain Policies              │
              │ Repository Intelligence      │
              │ Evidence Engine              │
              │ Planning Governor             │
              │ Contradiction Engine         │
              │ Action Validator             │
              │ Verification Engine          │
              │ Completion Gate              │
              │ Evaluation Engine            │
              └──────────────┬───────────────┘
                             │
             ┌───────────────┼────────────────┐
             │               │                │
             ▼               ▼                ▼
            MCP             Hooks            ACP
             │               │                │
             ▼               ▼                ▼
        ┌──────────┐   ┌───────────┐   ┌──────────┐
        │ Agents   │   │ Agents    │   │ Editors  │
        │          │   │           │   │          │
        │ Claude   │   │ Claude    │   │ VS Code  │
        │ Codex    │   │ Cursor    │   │ IDEs     │
        │ etc.     │   │ Copilot   │   │          │
        └────┬─────┘   └─────┬─────┘   └────┬─────┘
             └───────────────┼──────────────┘
                             ▼
                       PRIMARY LLM
                             │
                             ▼
                       TOOL ACTION
                             │
                             ▼
                    AGENTGUARD CHECK
                             │
                    ┌────────┴────────┐
                    │                 │
                  ALLOW            CHALLENGE
                    │                 │
                    ▼                 ▼
                 EXECUTE        HOST REASONS
                    │                 │
                    └────────┬────────┘
                             ▼
                         VERIFY
                             │
                     ┌───────┴───────┐
                     ▼               ▼
                   PASS           FAILURE
                     │               │
                     ▼               ▼
                  COMPLETE          RETRY
```

# 46. Core Design Principles

The implementation must follow these principles:

### 1. No mandatory proprietary LLM

AgentGuard should work without owning an LLM.

### 2. Bring Your Own Brain

Use the intelligence of the connected host agent.

### 3. Evidence before assumptions

Claims should be grounded in repository/runtime evidence.

### 4. Deterministic before probabilistic

Use filesystem, AST, Git, tests, schemas and static analysis whenever possible.

### 5. Challenge, don't replace

AgentGuard should challenge the host rather than independently solve every problem.

### 6. Proportional planning

Simple tasks should remain simple.

Complex tasks should receive appropriate depth.

### 7. Domain-aware reasoning

Backend, ML, MLOps, frontend, security and distributed systems should have different engineering considerations.

### 8. Verify before completion

The agent should have evidence that the task actually works.

### 9. Minimal latency

AgentGuard should remain almost invisible during normal operation.

### 10. Model/agent agnostic

The core should not depend on Claude, OpenAI, Gemini, Cursor or any single provider.

### 11. Adapter architecture

Agent-specific integrations belong outside the core.

### 12. Measurable improvement

Every major claim should eventually be benchmarked.

---

# 47. What AgentGuard Is NOT

It is NOT:

- another coding agent
- another LLM wrapper
- a replacement for Claude/Codex/Cursor
- a mandatory AI model
- a generic prompt optimizer
- a system that always chooses the simplest implementation
- a system that always challenges the agent
- a system that generates huge plans
- merely an MCP server
- merely a browser extension

It IS:

> **A lightweight, model-agnostic reliability and reasoning control plane for existing AI agents.**

---

# 48. Resume / AI Engineering Positioning

The project should ultimately demonstrate expertise in:

```text
LLM Reliability
Agentic AI
AI Engineering
AI Agent Orchestration
Context Engineering
Tool-use validation
Hallucination mitigation
Evidence-grounded reasoning
Agent evaluation
Static analysis
Repository intelligence
Human-AI interaction
AI safety/guardrails
Automated testing
Model-agnostic architecture
```

A strong eventual resume description would be based on measured results, for example:

> **Built AgentGuard, a model-agnostic reliability layer for AI coding agents that uses repository evidence, adaptive planning budgets, domain-aware policies and deterministic verification to challenge unsupported agent actions and reduce hallucinations and unnecessary code changes without requiring a dedicated LLM.**

After benchmarking, replace generic claims with actual measured numbers.

---

# 49. Final Product Thesis

The entire project can be summarized as:

```text
                EXISTING AI
                    +
              AGENTGUARD
                    =
          MORE RELIABLE AI
```

The host provides intelligence.

AgentGuard provides discipline.

The host proposes.

AgentGuard challenges.

The host reasons.

AgentGuard verifies.

The host executes.

AgentGuard validates.

The final goal is:

> **Not to make AI agents think more.**
>
> **Make them think appropriately.**

And:

> **Not to make AI agents always choose simpler solutions.**
>
> **Make them choose the least complex solution that is actually sufficient.**

And:

> **Not to prevent every mistake through another LLM.**
>
> **Make unsupported decisions difficult to execute through evidence, deterministic checks, adaptive reasoning and verification.**

The long-term vision is a **"second brain" that can attach to any capable AI agent and make that agent more senior-engineer-like without becoming another AI agent itself.**

# 50. Initial Implementation Directive

When beginning implementation, do NOT attempt to build the entire architecture at once.

Start with:

```text
Phase 1:

AgentGuard Core
        ↓
Repository Scanner
        ↓
Task/Intent Representation
        ↓
Complexity Engine
        ↓
Evidence Engine
        ↓
Action Validation
        ↓
Verification
```

Then create a Claude Code adapter.

Then Cursor.

Then Codex.

Only after the core proves useful should MCP, ACP, browser extensions, dashboards and additional integrations be expanded.

The first milestone should be:

> **Take a real coding task, attach AgentGuard to a real coding agent, observe its decisions, detect unsupported assumptions or unnecessary complexity, challenge the host using its own intelligence, allow valid actions, block/redirect invalid actions, and verify the final implementation — all without AgentGuard owning an LLM.**

That is the smallest version that proves the fundamental idea.