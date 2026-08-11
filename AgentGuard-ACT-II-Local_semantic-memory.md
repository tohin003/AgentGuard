AgentGuard — ACT II: Local Semantic Memory
Why add it?

The purpose isn't simply:

"Let's add a vector database because AI projects use vector databases."

The purpose is:

Allow AgentGuard to remember validated knowledge from previous sessions and retrieve relevant experience when the same project presents a related problem.

Example:

Monday

AgentGuard discovers:
"This project uses Service → Repository → DB architecture."

        ↓

Store validated memory


Friday

Developer:
"Add caching to the user service."

        ↓

Semantic Memory
        ↓
Retrieves:
"This project previously rejected
a second data-access abstraction."

        ↓
Current repository verification
        ↓
AgentGuard gives relevant context/challenge
        ↓
Host LLM reasons better

This directly strengthens your original "second brain" concept.

ACT II Architecture
                     AGENTGUARD CORE
                           │
          ┌────────────────┴────────────────┐
          │                                 │
          ▼                                 ▼
   CURRENT REPOSITORY                 PERSISTENT MEMORY
          │                                 │
   Files / AST / Git                       │
   Tests / Dependencies                    │
          │                                 │
          ▼                                 ▼
   Evidence Engine                 ┌──────────────────┐
          │                         │ Local Memory     │
          │                         │                  │
          │                         │ SQLite           │
          │                         │ + sqlite-vec     │
          │                         │ + FTS5           │
          │                         └────────┬─────────┘
          │                                  │
          │                           Semantic Search
          │                                  │
          └──────────────┬───────────────────┘
                         ▼
                  Memory Validation
                         │
                  Is memory still true?
                    /            \
                  YES             NO
                   │              │
                   ▼              ▼
              Use memory       Ignore/stale
                   │
                   ▼
             Host Agent

The crucial part is:

Vector memory suggests relevant knowledge; the current repository decides whether that knowledge is still true.

What should actually be stored?

Do not vectorize everything.

Store only high-value memories.

1. Architectural decisions
"Authentication is handled through middleware X."
2. Validated repository patterns
"All database access goes through repositories."
3. Important constraints
"Redis is already used for distributed rate limiting."
4. Previous verified decisions
"Pagination utility already exists and should be reused."
5. Repeated agent failures
"Previous agents repeatedly attempted to introduce
a second authentication layer."
6. Important debugging conclusions
"Connection pooling is required because Cloud Run
scales horizontally."
7. Domain/project knowledge
"This service performs asynchronous LLM inference."
Memory lifecycle

The important part is memory promotion.

Don't do:

Every conversation
      ↓
Vector DB

Instead:

Agent session
      ↓
Potential memory
      ↓
Is it useful?
      ↓
Was it validated?
      ↓
Is it project-specific?
      ↓
YES
      ↓
Memory promotion
      ↓
Embedding
      ↓
Vector store

This keeps the database small and high-quality.

Hybrid Search

I would not use vector search alone.

Use:

Current query
      │
      ├──────────────┐
      ▼              ▼
Semantic Search   Keyword Search
      │              │
      │            SQLite FTS5
      │              │
      └───────┬──────┘
              ▼
        Hybrid Ranking
              ↓
       Relevant memories

This is important because:

Vector search is good at meaning.

Keyword search is good at exact technical identifiers.

For example:

"authentication architecture"

Vector search → finds related auth decisions

FTS → finds exact:
JWTMiddleware
AuthService
auth/middleware.py

Combining them is more robust. Local projects are already using SQLite + sqlite-vec + FTS5 for this kind of hybrid retrieval.

Recommended storage

I would keep the entire thing local:

~/.agentguard/
│
├── agentguard.db
│
├── projects/
│   ├── project-A/
│   └── project-B/
│
└── config/

Inside SQLite:

projects
sessions
memories
memory_embeddings
evidence
decisions
verification_results

Use sqlite-vec for the vector layer rather than introducing a separate vector database server. This keeps the architecture aligned with your lightweight/local requirement. sqlite-vec is specifically designed to bring vector search into SQLite.

But where do embeddings come from?

This is the one thing we need to design carefully because your original requirement is:

AgentGuard should not have its own LLM.

I would extend that philosophy to embeddings.

Use an abstraction:

EmbeddingProvider
       │
       ├── Local embedding model
       ├── Host-provided embedding capability
       └── Optional external provider

For the default experience:

Local embedding model, completely separate from the host LLM.

This does NOT violate your "no own LLM" principle because an embedding model is not being used as a reasoning model.

However, do not bundle a large embedding model into AgentGuard initially.

Make it optional and pluggable.

Very important: Don't let memory slow AgentGuard

Memory retrieval should NOT happen on every tool call.

Instead:

Simple task
    ↓
No semantic memory required
    ↓
Agent continues immediately

For something more contextual:

Complex / ambiguous task
        ↓
Memory relevant?
        ↓
YES
        ↓
Semantic retrieval
        ↓
Validate against repository
        ↓
Inject useful context

So vector memory becomes an escalation capability, just like your reasoning layer.

Memory confidence

Every memory should have metadata:

memory_id
project_id
content
source
created_at
last_verified
confidence
validation_status
memory_type
source_files

Example:

Memory:
"Authentication uses JWT middleware."

confidence:
0.96

source:
src/auth/middleware.py

last_verified:
2026-08-11

status:
VALIDATED

If the repository changes:

Memory
 ↓
Current code check
 ↓
Contradiction
 ↓
STALE

The vector database should never blindly inject stale knowledge.

The really powerful part

This creates a three-layer truth system:

             AGENTGUARD
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
 Current Code   Memory    Host LLM
    TRUTH       CONTEXT   REASONING
       │          │          │
       └──────────┼──────────┘
                  ▼
             Final Decision
Current code

"What is true NOW?"

Vector memory

"What did we learn BEFORE?"

Host LLM

"What should we DO about it?"

That's a very strong architecture.

ACT II integration into your current roadmap

Your screenshot currently has:

Phase 4 — Action Validator + Verification + Completion Gate
Phase 5 — Full Claude Code adapter + install UX
Phase 6 — First real-world validation (SPEC §50 milestone)

Act II (Phases 7–12) — production hardening and release

I would modify Act II to:

ACT II — Production Hardening + Persistent Intelligence

Phase 7 — Performance + reliability hardening

Phase 8 — Agent interoperability
          Codex / Cursor / additional adapters

Phase 9 — Persistent project memory foundation
          SQLite memory model
          Memory promotion
          Retention
          Project isolation

Phase 10 — Local semantic memory
           Embedding abstraction
           sqlite-vec
           FTS5
           Hybrid retrieval
           Memory ranking

Phase 11 — Memory validation + intelligence
           Staleness detection
           Evidence verification
           Memory confidence
           Contradiction detection
           Context injection

Phase 12 — AgentGuard-Bench + production release
           Baseline vs AgentGuard
           Latency
           hallucination reduction
           planning efficiency
           memory recall
           reliability
What I would NOT do

Don't add:

Pinecone
Weaviate
Milvus
Qdrant server
Postgres + pgvector

to the first version.

That introduces:

network dependency
another service
deployment complexity
more latency
more failure points
unnecessary infrastructure

Your product's identity is:

local + lightweight + fast + model-agnostic.

Keep it that way.

One important research opportunity

When you reach Phase 12, benchmark:

Agent
   VS
Agent + AgentGuard
   VS
Agent + AgentGuard + Semantic Memory

Then you can answer:

Does persistent semantic memory actually improve coding-agent reliability?

Measure:

Task success
Hallucinations
Unsupported assumptions
Repeated mistakes
Planning length
Tool calls
Tokens
Latency
Relevant-memory retrieval precision
Stale-memory rate

This is much more scientifically interesting than simply saying:

"AgentGuard uses a vector database."

It should have reranking and automatic updating, but designed so they don't become a bottleneck.

The architecture should be:

Current Task
    ↓
Hybrid Retrieval
 ┌───────────────┐
 │ Vector Search │
 │ FTS5 Search   │
 └───────┬───────┘
         ↓
   Candidate Memories
         ↓
      Reranker
         ↓
  Top relevant memories
         ↓
Current Repository
         ↓
Evidence / Freshness Check
         ↓
Trusted Context
         ↓
Host LLM
Updating should be incremental

Don't re-embed the entire project after every change.

Instead:

Agent changes file
       ↓
Git/file change detected
       ↓
Identify affected memories
       ↓
Revalidate only those memories
       ↓
Update / invalidate if necessary

For example:

Memory:
"Authentication uses auth/middleware.py"

Developer changes auth/middleware.py
        ↓
Memory marked potentially stale
        ↓
Re-check repository
        ↓
Updated memory
Reranking

Don't rerank thousands of memories.

Use:

Vector + FTS5
     ↓
Top 20–50 candidates
     ↓
Fast reranker
     ↓
Top 3–10 memories

And importantly, the reranker doesn't need an LLM. Start with metadata + similarity + freshness + project relevance:

Score =
semantic_similarity
+ keyword_match
+ project_match
+ freshness
+ validation_confidence
+ source_relevance

Later, an optional host-LLM reranker could be used only for difficult queries.

Preventing bottlenecks

The key is asynchronous maintenance:

                Agent execution
                       │
              ┌────────┴────────┐
              ↓                 ↓
        Critical path      Background
              │             maintenance
              ↓                 │
         Fast retrieval    Re-embedding
         + validation      Memory updates
                           Cleanup
                           Re-ranking

So the agent doesn't wait for the entire memory system to update.

The final design principle

Retrieval must be fast; memory maintenance can happen asynchronously.

That means AgentGuard's memory becomes self-maintaining rather than self-blocking.

And I would absolutely include this in ACT II because it makes the "persistent second brain" much more credible: it doesn't just remember old information—it detects stale knowledge, updates validated knowledge, ranks what matters, and continuously aligns memory with the current repository.