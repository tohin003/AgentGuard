"""Daemon startup/handshake ownership invariants."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from agentguard.core.config import ensure_private_dir, write_private_text
from agentguard.daemon.app import clear_handshake, read_handshake, write_handshake
from agentguard.daemon.lifecycle import (
    create_private_marker,
    interprocess_lock,
    open_private_append,
    startup_lock_path,
)


def test_startup_lock_is_exclusive(isolated_home):
    path = startup_lock_path(isolated_home)
    with interprocess_lock(path, timeout=0.1) as first:
        assert first
        with interprocess_lock(path, timeout=0.05) as second:
            assert not second


def test_shutdown_only_clears_the_owned_handshake(isolated_home):
    token = "old-token"
    write_handshake("127.0.0.1", 8787, token)
    path = isolated_home / "daemon.json"
    original = read_handshake()
    assert original is not None

    replacement = {**original, "pid": original["pid"] + 1, "token": "replacement-token"}
    path.write_text(json.dumps(replacement), encoding="utf-8")

    clear_handshake(expected_pid=os.getpid(), expected_token=token)
    assert read_handshake() == replacement

    clear_handshake(expected_pid=replacement["pid"], expected_token=replacement["token"])
    assert not path.exists()


def test_startup_lock_rejects_a_symlinked_home(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with interprocess_lock(startup_lock_path(link), timeout=0.01) as acquired:
        assert not acquired
    assert not (target / "daemon.start.lock").exists()


def test_private_paths_reject_existing_symlinked_ancestors(tmp_path):
    target = tmp_path / "target"
    (target / "existing").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(RuntimeError):
        ensure_private_dir(link / "existing")
    with interprocess_lock(startup_lock_path(link / "existing"), timeout=0.01) as acquired:
        assert not acquired
    assert not (target / "existing" / "daemon.start.lock").exists()

    with pytest.raises(RuntimeError):
        write_private_text(link / "existing" / "settings.json", "secret")
    with pytest.raises(OSError):
        open_private_append(link / "existing" / "daemon.log")
    with pytest.raises(OSError):
        create_private_marker(link / "existing" / "notified-session", "pid")

    assert not (target / "existing" / "settings.json").exists()
    assert not (target / "existing" / "daemon.log").exists()
    assert not (target / "existing" / "notified-session").exists()


def test_macos_system_tmp_alias_remains_usable(tmp_path):
    """The standard /tmp -> /private/tmp alias is not user-controlled."""
    if os.path.realpath("/tmp") != "/private/tmp":
        pytest.skip("not macOS's /tmp alias")
    root = Path(tempfile.mkdtemp(prefix="agentguard-compat-", dir="/tmp"))
    try:
        assert ensure_private_dir(root / "agentguard-home").is_dir()
    finally:
        shutil.rmtree(root, ignore_errors=True)
