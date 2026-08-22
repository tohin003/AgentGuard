"""AgentGuard CLI (SPEC §31 — Typer).

Design intent (SPEC §8): "Install once -> connect agent -> forget AgentGuard exists."
So the CLI is deliberately small. The commands that matter are `install`, `doctor` and
`log`; everything else is for debugging.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import typer

from agentguard.core.config import (
    Settings,
    agentguard_home,
    ensure_private_dir,
    get_or_create_token,
    write_private_text,
)

app = typer.Typer(
    name="agentguard",
    help="Reliability and reasoning control layer for AI coding agents.",
    no_args_is_help=True,
    add_completion=False,
)
daemon_app = typer.Typer(name="daemon", help="Manage the local daemon.", no_args_is_help=True)
app.add_typer(daemon_app)


def _echo(msg: str = "") -> None:
    typer.echo(msg)


def _ok(msg: str) -> None:
    typer.secho(f"  ✓ {msg}", fg=typer.colors.GREEN)


def _warn(msg: str) -> None:
    typer.secho(f"  ! {msg}", fg=typer.colors.YELLOW)


def _bad(msg: str) -> None:
    typer.secho(f"  ✗ {msg}", fg=typer.colors.RED)


# ---------------------------------------------------------------- daemon


@daemon_app.command("run")
def daemon_run(
    host: str = typer.Option(None, help="Bind host (default from config)."),
    port: int = typer.Option(None, help="Bind port; 0 for ephemeral."),
    log_level: str = typer.Option("warning", help="uvicorn log level."),
) -> None:
    """Run the daemon in the foreground."""
    from agentguard.daemon.app import run

    run(host=host, port=port, log_level=log_level)


@daemon_app.command("start")
def daemon_start(timeout: float = typer.Option(10.0, help="Seconds to wait for readiness.")) -> None:
    """Start the daemon in the background."""
    from agentguard.daemon.app import daemon_is_alive, read_handshake
    from agentguard.daemon.lifecycle import interprocess_lock, open_private_append, startup_lock_path

    home = agentguard_home()
    with interprocess_lock(startup_lock_path(home), timeout=timeout) as locked:
        if not locked:
            _bad(f"could not acquire the daemon startup lock within {timeout}s")
            raise typer.Exit(1)

        if daemon_is_alive():
            hs = read_handshake() or {}
            _echo(f"already running (pid {hs.get('pid')}) on {hs.get('host')}:{hs.get('port')}")
            raise typer.Exit(0)

        home.mkdir(parents=True, exist_ok=True)
        log_file = open_private_append(home / "daemon.log")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "agentguard.daemon", "run"],
                stdout=log_file,
                stderr=log_file,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        finally:
            log_file.close()

        deadline = time.time() + timeout
        while time.time() < deadline:
            if daemon_is_alive():
                hs = read_handshake() or {}
                _echo(f"started (pid {hs.get('pid')}) on {hs.get('host')}:{hs.get('port')}")
                raise typer.Exit(0)
            time.sleep(0.05)

        _bad(f"daemon did not become ready within {timeout}s — see {home / 'daemon.log'}")
        raise typer.Exit(1)


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the daemon."""
    import signal

    from agentguard.core.config import agentguard_home
    from agentguard.daemon.app import clear_handshake, daemon_is_alive, read_handshake
    from agentguard.daemon.lifecycle import handshake_lock_path, interprocess_lock

    # Hold the handshake lock across the final liveness check and SIGTERM. This closes
    # the race where an old snapshot is read, a replacement publishes a new handshake,
    # and `stop` accidentally signals the wrong PID. Release before waiting for the old
    # process to unwind: its shutdown handler needs the same lock to clear its record.
    stale_to_clear: dict[str, object] | None = None
    with interprocess_lock(handshake_lock_path(agentguard_home()), timeout=2.0) as locked:
        if not locked:
            _bad("could not acquire the daemon handshake lock")
            raise typer.Exit(1)
        hs = read_handshake() or {}
        if not hs:
            _echo("not running")
            raise typer.Exit(0)
        if not daemon_is_alive():
            _echo("not running")
            # A stale record is safe to remove only while its PID/token still match the
            # record we inspected. With no record, leave the filesystem untouched.
            stale_to_clear = hs
        else:
            pid = hs.get("pid")
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (OSError, TypeError, ValueError) as exc:
                _bad(f"could not stop pid {pid}: {exc}")
                raise typer.Exit(1) from exc

    if stale_to_clear:
        clear_handshake(
            expected_pid=stale_to_clear.get("pid"),
            expected_token=stale_to_clear.get("token"),
        )
        raise typer.Exit(0)

    for _ in range(60):
        if not daemon_is_alive():
            break
        time.sleep(0.05)
    if daemon_is_alive():
        _bad(f"daemon did not stop within 3s (pid {pid}); handshake left intact")
        raise typer.Exit(1)
    clear_handshake(expected_pid=pid, expected_token=hs.get("token"))
    _echo(f"stopped (pid {pid})")


@daemon_app.command("status")
def daemon_status() -> None:
    """Show daemon status."""
    from agentguard.daemon.app import daemon_is_alive, read_handshake

    hs = read_handshake()
    if not hs or not daemon_is_alive():
        _echo("daemon: not running")
        raise typer.Exit(1)
    _echo(f"daemon: running  pid={hs['pid']}  url=http://{hs['host']}:{hs['port']}")
    _echo(f"uptime: {time.time() - hs.get('started_at', time.time()):.1f}s")


# ---------------------------------------------------------------- install


@app.command("install")
def install_cmd(
    agent: str = typer.Argument("claude", help="Which agent to attach to (currently: claude)."),
    project: bool = typer.Option(False, "--project", help="Install into ./.claude/settings.local.json"),
    global_: bool = typer.Option(False, "--global", help="Install into ~/.claude/settings.json (default)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change; write nothing."),
) -> None:
    """Attach AgentGuard to a coding agent."""
    if agent != "claude":
        _bad(f"unknown agent '{agent}' (Cursor and Codex adapters arrive in Phase 9)")
        raise typer.Exit(2)

    from agentguard.adapters.claude_code import install as inst

    try:
        path = inst.settings_path(project=project, global_=global_)
    except ValueError as exc:
        _bad(str(exc))
        raise typer.Exit(2) from exc

    try:
        result, changed = inst.install(path, dry_run=dry_run)
    except RuntimeError as exc:
        _bad(str(exc))
        raise typer.Exit(1) from exc

    _echo(f"settings file: {path}")
    if dry_run:
        _echo(json.dumps(result.get("hooks", {}), indent=2))
        _warn("dry run — nothing written")
        return

    _ok("hooks installed" if changed else "hooks already up to date")
    get_or_create_token()
    _echo()
    _echo("Next: start the daemon (or just start a Claude Code session — SessionStart does it):")
    _echo("  agentguard daemon start")


@app.command("uninstall")
def uninstall_cmd(
    agent: str = typer.Argument("claude"),
    project: bool = typer.Option(False, "--project"),
    global_: bool = typer.Option(False, "--global"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Detach AgentGuard, leaving any hooks you wrote yourself untouched."""
    from agentguard.adapters.claude_code import install as inst

    try:
        path = inst.settings_path(project=project, global_=global_)
        result, changed = inst.uninstall(path, dry_run=dry_run)
    except (RuntimeError, ValueError) as exc:
        _bad(str(exc))
        raise typer.Exit(1 if isinstance(exc, RuntimeError) else 2) from exc
    _echo(f"settings file: {path}")
    if dry_run:
        if changed:
            _echo(json.dumps(result.get("hooks", {}), indent=2))
        _warn("dry run — nothing written")
    else:
        _ok("hooks removed" if changed else "nothing to remove")


# ---------------------------------------------------------------- diagnostics


@app.command("doctor")
def doctor() -> None:
    """Check that everything AgentGuard needs is present and wired up."""
    import shutil
    import socket

    from agentguard.adapters.claude_code import install as inst
    from agentguard.daemon.app import daemon_is_alive, read_handshake

    settings = Settings.load()
    problems = 0

    _echo("environment")
    _ok(f"python {sys.version.split()[0]}")
    for tool, required in (("git", True), ("rg", False)):
        if shutil.which(tool):
            _ok(f"{tool} found")
        elif required:
            _bad(f"{tool} not found — git evidence will be unavailable")
            problems += 1
        else:
            _warn(f"{tool} not found — falling back to a slower filesystem walk")

    _echo("\nconfig")
    _echo(f"  home: {agentguard_home()}")
    _echo(f"  daemon: {settings.daemon.base_url}")
    if settings.config_warning:
        _warn(f"using safe defaults because {settings.config_warning}")
        problems += 1
    if not settings.enabled:
        _warn("AgentGuard is DISABLED (AGENTGUARD_DISABLE is set, or config says enabled=false)")
    elif settings.observe_only:
        # Worth surfacing here above all: a developer who has forgotten this is on
        # believes they are guarded and is not. Exactly the failure mode plan D9 exists
        # to prevent, arrived at from the other direction.
        _warn("OBSERVE-ONLY is on — findings are recorded but nothing is ever said")

    _echo("\nstorage")
    from agentguard.core.store import Database

    stats = Database.shared().stats()
    if settings.config_warning:
        _bad(settings.config_warning)
        problems += 1
    _echo(f"  {stats['path']}  ({stats['size_mb']} MB)")
    if stats["disk_state"] == "healthy":
        _ok(f"{stats['free_disk_mb']:.0f} MB free")
    elif stats["disk_state"] == "low":
        _warn(f"{stats['free_disk_mb']:.0f} MB free — pruning aggressively")
    else:
        _bad(f"{stats['free_disk_mb']:.0f} MB free — persistence paused (guarding continues)")
        problems += 1

    _echo("\ndaemon")
    if daemon_is_alive():
        hs = read_handshake() or {}
        _ok(f"running (pid {hs.get('pid')}) on http://{hs.get('host')}:{hs.get('port')}")
    else:
        _warn("not running — start it with `agentguard daemon start`")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            if s.connect_ex((settings.daemon.host, settings.daemon.port)) == 0:
                _bad(
                    f"port {settings.daemon.port} is occupied by something else — "
                    "change [daemon].port in config.toml and re-run `agentguard install`"
                )
                problems += 1

    _echo("\nclaude code")
    for label, path in (
        ("global", inst.global_settings_path()),
        ("project", inst.project_settings_path()),
    ):
        if inst.is_installed(path):
            _ok(f"{label} hooks installed ({path})")
        else:
            _echo(f"  · {label} hooks not installed ({path})")

    installed_anywhere = any(
        inst.is_installed(path)
        for path in (inst.global_settings_path(), inst.project_settings_path())
    )
    if not installed_anywhere:
        _bad("no Claude Code hooks installed — this project is currently unguarded")
        problems += 1

    _echo()
    if problems:
        _bad(f"{problems} problem(s) found")
        raise typer.Exit(1)
    _ok("ready")


@app.command("log")
def log_cmd(
    limit: int = typer.Option(20, "-n", help="How many decisions to show."),
    workspace: Path = typer.Option(Path.cwd(), help="Workspace to read decisions from."),
) -> None:
    """Show recent decisions (SPEC §41 decision logs)."""
    from agentguard.core.store import Store

    store = Store.for_workspace(workspace)
    rows = store.recent_decisions(limit)
    if not rows:
        _echo("no decisions recorded yet")
        return
    for row in rows:
        stamp = time.strftime("%H:%M:%S", time.localtime(row["ts"]))
        action = str(row["action"]).upper()
        colour = {
            "ALLOW": typer.colors.GREEN,
            "CHALLENGE": typer.colors.YELLOW,
            "BLOCK": typer.colors.RED,
            "MODIFY": typer.colors.CYAN,
            "REQUEST_REVIEW": typer.colors.MAGENTA,
        }.get(action, typer.colors.WHITE)
        typer.secho(
            f"{stamp}  {action:<15}{row['tool'] or '-':<14}{row['latency_ms']:>7.1f}ms  {row['id'][:8]}",
            fg=colour,
        )
        if row["reason"]:
            _echo(f"           {row['reason'].splitlines()[0][:100]}")


@app.command("why")
def why_cmd(
    decision_id: str = typer.Argument(..., help="Decision id (prefix accepted)."),
    workspace: Path = typer.Option(Path.cwd()),
) -> None:
    """Explain a decision: every finding and every piece of evidence behind it."""
    from agentguard.core.store import Store

    store = Store.for_workspace(workspace)
    detail = store.decision_detail(decision_id)
    if detail is None:
        for row in store.recent_decisions(500):
            if row["id"].startswith(decision_id):
                detail = store.decision_detail(row["id"])
                break
    if detail is None:
        _bad(f"no decision matching '{decision_id}'")
        raise typer.Exit(1)

    _echo(f"decision {detail['id']}")
    _echo(f"  action:  {detail['action']}")
    _echo(f"  tool:    {detail['tool']}")
    _echo(f"  level:   {detail['level']}")
    _echo(f"  latency: {detail['latency_ms']:.1f}ms")
    if detail["reason"]:
        _echo(f"\n{detail['reason']}")
    for f in detail.get("findings", []):
        _echo(f"\n  [{f['severity']}] {f['category']} — {f['verdict']}")
        _echo(f"  subject: {f['subject']}")
        if f["summary"]:
            _echo(f"  {f['summary']}")
        for ev in json.loads(f["evidence"] or "[]"):
            _echo(f"    evidence: {ev.get('path') or ev.get('symbol') or ev.get('source')}")


# ---------------------------------------------------------------- repository


@app.command("index")
def index_cmd(
    workspace: Path = typer.Argument(Path.cwd(), help="Repository to index."),
    verbose: bool = typer.Option(False, "-v", help="Show per-language breakdown."),
) -> None:
    """Build the repository index and report what it found (SPEC §32)."""
    from agentguard.repo import RepoIndex

    index = RepoIndex(workspace).build()
    summary = index.summary()

    _echo(f"{summary['root']}")
    _echo(f"  files         {summary['files']}  ({summary['parsed_files']} parsed for symbols)")
    _echo(f"  symbols       {summary['symbols']}")
    _echo(f"  tests         {summary['test_files']} files covering {summary['tested_sources']} sources")
    _echo(f"  config        {summary['config_files']}")
    _echo(f"  dependencies  {summary['dependencies']}  from {', '.join(summary['manifests']) or '—'}")
    _echo(f"  built in      {summary['build_ms']}ms")
    if verbose:
        _echo("\n  languages")
        for lang, count in summary["languages"].items():
            _echo(f"    {lang:<14}{count}")
    git = index.git
    if git.is_repo:
        _echo(f"\n  git           {git.branch} @ {git.head[:8]}  ({len(git.dirty)} dirty)")


@app.command("find-symbol")
def find_symbol_cmd(
    name: str = typer.Argument(..., help="Symbol name, bare or qualified."),
    workspace: Path = typer.Option(Path.cwd()),
) -> None:
    """Look up a symbol — the core question the Evidence Engine asks (SPEC §14)."""
    from agentguard.repo import RepoIndex

    index = RepoIndex(workspace).build()
    matches = index.find_symbol(name)
    if not matches:
        _bad(f"no symbol '{name}' found in {workspace}")
        if "." in name:
            owner = name.rsplit(".", 1)[0]
            if index.knows_type(owner):
                known = sorted(index.attributes_of(owner))
                _echo(f"  '{owner}' exists and defines: {', '.join(known)}")
            else:
                _warn(f"  '{owner}' is also unknown — no evidence either way")
        raise typer.Exit(1)

    for symbol in matches:
        _ok(f"{symbol.qualname}{symbol.signature}")
        _echo(f"      {symbol.kind:<10}{symbol.path}:{symbol.line}")


@app.command("explain")
def explain_cmd(
    path: str = typer.Argument(..., help="File to explain."),
    workspace: Path = typer.Option(Path.cwd()),
) -> None:
    """Everything the index knows about one file: symbols, imports, dependents, tests."""
    from agentguard.repo import RepoIndex

    index = RepoIndex(workspace).build()
    rel = index.normalize(path)
    if not index.file_exists(rel):
        _bad(f"{rel} is not in the index")
        raise typer.Exit(1)

    record = index.files[rel]
    _echo(f"{rel}")
    _echo(f"  language      {record.lang}{'  (test file)' if record.is_test else ''}")
    _echo(f"  parsed        {'yes' if index.is_parsed(rel) else 'no — symbol evidence incomplete'}")

    symbols = index.symbols_in(rel)
    _echo(f"\n  defines ({len(symbols)})")
    for symbol in symbols[:30]:
        _echo(f"    {symbol.kind:<10}{symbol.qualname}  :{symbol.line}")
    if len(symbols) > 30:
        _echo(f"    … {len(symbols) - 30} more")

    internal = [i for i in index.imports_of(rel) if i.is_internal]
    external = [i for i in index.imports_of(rel) if not i.is_internal]
    _echo(f"\n  imports       {len(internal)} internal, {len(external)} external")
    for record_ in internal[:10]:
        _echo(f"    {record_.raw}  ->  {record_.resolved}")

    dependents = index.dependents_of(rel)
    radius = index.blast_radius(rel, depth=3)
    _echo(f"\n  dependents    {len(dependents)} direct, {len(radius)} within 3 hops")
    for dependent in sorted(dependents)[:10]:
        _echo(f"    {dependent}")

    tests = index.tests_for(rel)
    if tests:
        _echo(f"\n  covered by    {len(tests)} test file(s)")
        for test in sorted(tests):
            _echo(f"    {test}")
    else:
        _echo()
        _warn("covered by    no tests found")


@app.command("report")
def report_cmd(
    workspace: Path = typer.Option(Path.cwd(), help="Project to report on."),
    days: int = typer.Option(7, "-d", help="How far back to look."),
) -> None:
    """What AgentGuard has actually done in this project.

    Deliberately reports *observations*, not effect. It can tell you how many unsupported
    claims were challenged; it cannot tell you how many would have shipped without it,
    because that needs a run of the same tasks with AgentGuard absent. That comparison is
    what `agentguard bench` is for (SPEC §36) and it does not exist yet — so nothing here
    is phrased as a reduction.
    """
    import time as _t

    from agentguard.core.store import Database, ProjectStore

    store = ProjectStore.for_workspace(workspace)
    db = Database.shared()
    since = _t.time() - days * 86400
    pid = store.project_id

    def scalar(sql: str, params: tuple = ()) -> int:
        rows = db.query(sql, params)
        return int(rows[0][0]) if rows else 0

    tasks = scalar("SELECT COUNT(*) FROM tasks WHERE project_id=? AND created_at>?", (pid, since))
    sessions = scalar(
        "SELECT COUNT(*) FROM sessions WHERE project_id=? AND started_at>?", (pid, since)
    )
    actions = db.query(
        "SELECT action, COUNT(*) n FROM decisions WHERE project_id=? AND ts>? GROUP BY action",
        (pid, since),
    )
    total = sum(r["n"] for r in actions) or 1

    _echo(f"{workspace}")
    _echo(f"  last {days} days · {sessions} session(s) · {tasks} task(s)\n")

    _echo("decisions")
    for row in sorted(actions, key=lambda r: -r["n"]):
        share = row["n"] / total * 100
        _echo(f"  {row['action']:<16}{row['n']:>6}   {share:5.1f}%")

    interventions = sum(r["n"] for r in actions if r["action"] != "allow")
    _echo(f"\n  {interventions} intervention(s) in {total} decisions "
          f"({interventions / total * 100:.1f}%) — the rest were silent")

    findings = db.query(
        "SELECT category, severity, subject, summary, COUNT(*) n FROM findings "
        "WHERE project_id=? AND ts>? GROUP BY category, subject ORDER BY n DESC LIMIT 10",
        (pid, since),
    )
    if findings:
        _echo("\nwhat it caught")
        for f in findings:
            _echo(f"  [{f['severity']}] {f['category']:<14}{f['summary'][:60]}")
    else:
        _echo("\nwhat it caught\n  nothing — no unsupported claims or scope drift observed")

    gate = db.query(
        "SELECT reason FROM decisions WHERE project_id=? AND ts>? AND action='block' "
        "ORDER BY ts DESC LIMIT 5",
        (pid, since),
    )
    _echo(f"\ncompletion gate\n  held {len(gate)} turn(s) open")
    for row in gate:
        line = next((ln for ln in (row["reason"] or "").splitlines() if ln.strip()), "")
        _echo(f"    {line[:74]}")

    latency = [
        float(r["latency_ms"])
        for r in db.query(
            "SELECT latency_ms FROM decisions WHERE project_id=? AND ts>?", (pid, since)
        )
    ]
    if latency:
        from agentguard.core.metrics import percentile

        _echo(
            f"\noverhead\n  p50 {percentile(latency, 50):.1f}ms · "
            f"p95 {percentile(latency, 95):.1f}ms · max {max(latency):.1f}ms"
        )

    _echo(
        "\nnote: these are observations, not an effect size. Measuring how much AgentGuard\n"
        "      changes outcomes needs the same tasks run without it — see SPEC §36."
    )


# ---------------------------------------------------------------- census (SPEC §3)


@app.command("census")
def census_cmd(
    workspace: Path = typer.Option(Path.cwd(), help="Project to report on."),
    days: int = typer.Option(14, "-d", help="Observation window."),
    verbose: bool = typer.Option(False, "-v", help="Also show what each detector proves."),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Which of SPEC §3's seventeen failure modes actually occur (Phase 7).

    Ranks observed failures by how many tasks they affected. Modes with no detector are
    listed separately and never reported as zero — "nothing looks for it" and "it does not
    happen" are different facts, and only one of them is true here.

    Pair with `agentguard observe on`, which makes AgentGuard record everything and say
    nothing, so the count describes an unguarded agent.
    """
    from agentguard.census import collect, render, render_detectors
    from agentguard.core.store import ProjectStore

    result = collect(ProjectStore.for_workspace(workspace), days=days)

    if as_json:
        _echo(json.dumps(result.to_dict(), indent=2, default=str))
        return

    _echo(render(result))
    if verbose:
        _echo()
        _echo(render_detectors())


# ---------------------------------------------------------------- kill switch


def _set_flag(key: str, value: bool) -> Path:
    """Persist a top-level boolean without disturbing sections or credentials.

    This is intentionally line-oriented because the stdlib has no TOML writer. Only a
    key at the document's top level is replaced; a same-named key inside ``[daemon]`` or
    another table belongs to the user's config and must remain untouched.
    """
    path = agentguard_home() / "config.toml"
    ensure_private_dir()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept: list[str] = []
    in_table = False
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
        if not in_table and stripped and not stripped.startswith("#"):
            name, separator, _ = stripped.partition("=")
            if separator and name.strip() == key:
                if not replaced:
                    kept.append(f"{key} = {'true' if value else 'false'}")
                    replaced = True
                continue
        kept.append(line)
    if not replaced:
        kept.insert(0, f"{key} = {'true' if value else 'false'}")
    write_private_text(path, "\n".join(kept).rstrip() + "\n")
    return path


def _set_enabled(enabled: bool) -> Path:
    return _set_flag("enabled", enabled)


observe_app = typer.Typer(
    name="observe",
    help="Observe-only mode: record everything, say nothing (SPEC §3 census).",
    no_args_is_help=True,
)
app.add_typer(observe_app)


@observe_app.command("on")
def observe_on() -> None:
    """Turn AgentGuard into a sensor.

    Every engine still runs and every finding is still recorded; none of it reaches the
    agent. No challenges, no completion gate, no injected planning budget — so the
    failures counted are an *unguarded* agent's, which is the only baseline worth having.

    The cost is not hidden: while this is on, AgentGuard protects nothing.
    """
    path = _set_flag("observe_only", True)
    _ok(f"AgentGuard is OBSERVING ({path})")
    _warn("guarding is suspended: no challenges, no completion gate, no planning budget")
    _echo("  restart the daemon to pick this up:  agentguard daemon stop && agentguard daemon start")
    _echo("  read the results with:               agentguard census")
    _echo("  for a single session instead, set AGENTGUARD_OBSERVE=1")


@observe_app.command("off")
def observe_off() -> None:
    """Resume guarding."""
    _set_flag("observe_only", False)
    _ok("observe-only is OFF — AgentGuard will speak again")
    if Settings.load().observe_only:
        _warn("still observing because AGENTGUARD_OBSERVE is set in this environment")
    _echo("  restart the daemon to pick this up:  agentguard daemon stop && agentguard daemon start")


@observe_app.command("status")
def observe_status() -> None:
    """Whether AgentGuard is currently allowed to speak."""
    settings = Settings.load()
    if not settings.enabled:
        _warn("AgentGuard is OFF entirely — nothing is computed or recorded")
    elif settings.observe_only:
        _ok("observe-only: recording findings, saying nothing")
    else:
        _ok("guarding: challenges, completion gate and planning budget are live")


@app.command("off")
def off_cmd() -> None:
    """Stop guarding, without uninstalling anything.

    The hooks stay in place and keep firing; they simply decide nothing. Useful when
    AgentGuard is in the way and you want it back later without re-installing.
    """
    path = _set_enabled(False)
    _ok(f"AgentGuard is OFF ({path})")
    _echo("  hooks remain installed and inert · re-enable with `agentguard on`")
    _echo("  for a single session instead, set AGENTGUARD_DISABLE=1")
    _echo("  restart the daemon to pick this up:  agentguard daemon stop && agentguard daemon start")


@app.command("on")
def on_cmd() -> None:
    """Resume guarding."""
    _set_enabled(True)
    _ok("AgentGuard is ON")
    if not Settings.load().enabled:
        _warn("still disabled by AGENTGUARD_DISABLE in this environment")
    _echo("  restart the daemon to pick this up:  agentguard daemon stop && agentguard daemon start")


# ---------------------------------------------------------------- storage


db_app = typer.Typer(name="db", help="Inspect and maintain local storage.", no_args_is_help=True)
app.add_typer(db_app)


@db_app.command("stats")
def db_stats() -> None:
    """Show what the database holds and how much room it has."""
    from agentguard.core.store import Database

    stats = Database.shared().stats()
    _echo(f"database: {stats['path']}")
    _echo(f"  size       {stats['size_mb']} MB")
    _echo(f"  free disk  {stats['free_disk_mb']} MB  ({stats['disk_state']})")
    _echo(f"  writes     {'enabled' if stats['writes_enabled'] else 'DISABLED (low disk)'}")
    _echo("\nrows")
    for table, count in stats["rows"].items():
        _echo(f"  {table:<16}{count}")

    if stats["disk_state"] == "critical":
        _bad("free disk is critical — AgentGuard has stopped persisting, but still guards")
    elif stats["disk_state"] == "low":
        _warn("free disk is low — retention is pruning more aggressively")


@db_app.command("projects")
def db_projects() -> None:
    """List the projects AgentGuard has seen."""
    from agentguard.core.store import Database

    rows = Database.shared().query("SELECT * FROM projects ORDER BY last_seen DESC")
    if not rows:
        _echo("no projects recorded yet")
        return
    for row in rows:
        seen = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["last_seen"]))
        _echo(f"{row['id']}  {row['name']:<24}{seen}")
        _echo(f"    {row['root']}")
        if row["git_remote"]:
            _echo(f"    {row['git_remote']}")


@db_app.command("maintain")
def db_maintain(
    force: bool = typer.Option(False, "--force", help="Ignore the rate limit."),
) -> None:
    """Prune expired data, checkpoint the WAL and reclaim space."""
    from agentguard.core.store import Database

    db = Database.shared()
    before = db.size_megabytes()
    removed = db.maintain(force=force)
    after = db.size_megabytes()

    if not removed:
        _echo("nothing to do (run with --force to override the interval)")
        return
    for table, count in removed.items():
        if count:
            _echo(f"  pruned {count} from {table}")
    _ok(f"{before:.2f} MB → {after:.2f} MB")


@app.command("version")
def version_cmd() -> None:
    from agentguard import __version__

    _echo(__version__)


@app.command("bench")
def bench_cmd(
    repo: Path = typer.Option(..., help="Repository to run the benchmark against."),
    settings: Path = typer.Option(..., help="Claude settings file with AgentGuard's hooks."),
    task: list[str] = typer.Option(None, "-t", help="Task ids; default is the whole corpus."),
    runs: int = typer.Option(3, "-n", help="Runs per task per arm."),
    out: Path = typer.Option(Path("bench-results.json")),
    model: str = typer.Option("", help="Model for the agent under test, e.g. haiku."),
) -> None:
    """Measure AgentGuard against a control arm (SPEC §36).

    Two arms differing only in AGENTGUARD_DISABLE. Deterministic scoring, per-run results
    kept. Designed so it can conclude there is no benefit.
    """
    from agentguard.bench import runner

    _echo(f"repo: {repo}\ncorpus: {', '.join(task) if task else 'all'}\n")
    payload = runner.run(repo.resolve(), settings.resolve(), list(task) if task else None,
                         runs, out, model=model)
    _echo("\n" + runner.render(payload))
    _echo(f"\nper-run data: {out}")


if __name__ == "__main__":
    app()
