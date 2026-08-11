AgentGuard Memory & Database Management Plan
1. Storage model

Use one persistent local SQLite database, but logically separate data by:

AgentGuard SQLite
│
├── Projects
│   ├── Project A
│   │   ├── Session 1
│   │   ├── Session 2
│   │   └── Session 3
│   │
│   └── Project B
│       ├── Session 1
│       └── Session 2
│
└── Global configuration

The database survives application restarts and sessions.

2. Session-level data

Store detailed information temporarily:

sessions
agent_actions
evidence_checks
violations
verification_results

Example:

Session
   ↓
Task
   ↓
Agent Action
   ↓
Evidence Check
   ↓
Decision
   ↓
Verification

Raw/high-volume session data should have a limited retention period.

3. Persistent project memory

Only promote useful, validated information into persistent memory.

Examples:

Project architecture
Existing conventions
Important dependencies
Validated repository patterns
Repeated agent mistakes
Important engineering decisions
Domain configuration
Known constraints

This becomes AgentGuard's long-term project memory.

Session
   ↓
Useful validated information
   ↓
Persistent Project Memory

Do not automatically save every agent response.

4. Repository-scoped memory

Memory should be isolated by project.

Project A
   ↓
Project A Memory

Project B
   ↓
Project B Memory

AgentGuard must never accidentally use Project A's architectural knowledge while working on Project B.

5. Retention policy

Use configurable retention:

Raw tool/action logs       → ~7–30 days
Detailed verification      → ~30–90 days
Session summaries          → ~6–12 months
Validated project memory   → long-term
Important violations       → long-term
Temporary caches           → aggressively removable

These should be configuration values rather than hard-coded rules.

6. Storage optimization

Never store huge objects unnecessarily.

Instead of:

Entire 50 KB LLM response

store:

task_id
decision
evidence references
violation type
short summary
timestamp

Source code remains on disk/Git; SQLite stores metadata and useful memory, not copies of the repository.

7. Automatic cleanup

Run a lightweight maintenance process:

SQLite
  ↓
Check size / age
  ↓
Remove expired raw data
  ↓
Compact database
  ↓
Update indexes

Use SQLite maintenance such as:

WAL mode for normal operation
incremental cleanup
periodic checkpointing
VACUUM only when appropriate
indexes on frequently queried fields

Avoid running expensive maintenance during active agent execution.

8. Disk-space protection

AgentGuard should monitor available disk space.

          Disk Space
              ↓
       ┌──────┴──────┐
       ↓             ↓
    Healthy          Low
       ↓             ↓
    Normal       Cleanup cache
                     ↓
                Remove old logs
                     ↓
                  Critical
                     ↓
             Stop telemetry
             storage first

The critical rule:

If SQLite fails or disk space becomes unavailable, AgentGuard's core reliability functionality must continue working.

Database persistence is helpful; it must not become a dependency for agent execution.

9. Optional archival

For advanced users:

Local SQLite
     ↓
Optional export
     ↓
Compressed archive / PostgreSQL / Cloud

But this should be optional.

The default AgentGuard experience should remain:

Local + private + lightweight + offline-capable.

10. Final architecture
                 AGENTGUARD
                     │
                     ▼
              ┌─────────────┐
              │ SQLite      │
              │             │
              │ Sessions    │
              │ Actions     │
              │ Evidence    │
              │ Violations  │
              │ Verification│
              │             │
              │ Project     │
              │ Memory      │
              └──────┬──────┘
                     │
             Memory Promotion
                     │
                     ▼
             Persistent Memory
                     │
             ┌───────┴───────┐
             ↓               ↓
       Project A          Project B
        Memory             Memory
Core principle

Session data is temporary. Project knowledge is persistent. Raw history is aggressively managed.

That gives AgentGuard the long-term memory you want without allowing the local database to grow uncontrollably.