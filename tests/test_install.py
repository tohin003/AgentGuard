"""Installer safety (SPEC §26).

Writing into a developer's settings.json is the single most destructive thing this
project does. These tests exist to make sure it is boring: idempotent, additive, and
exactly reversible.
"""

from __future__ import annotations

import json

import pytest

from agentguard.adapters.claude_code import install as inst


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / ".claude" / "settings.json"


class TestHookConfig:
    def test_covers_every_subscribed_event(self):
        from agentguard.adapters.claude_code.translate import SUBSCRIBED_EVENTS

        config = inst.build_hook_config()
        # SubagentStop and PostToolUseFailure are handled if they arrive but are not
        # installed by default — they add cost without adding signal at this stage.
        installed = set(config)
        assert installed <= set(SUBSCRIBED_EVENTS)
        assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"} <= installed

    def test_hot_path_uses_http_not_subprocess(self):
        """SPEC §8: a Python process spawn per tool call would eat the whole budget."""
        config = inst.build_hook_config()
        for event in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"):
            for group in config[event]:
                for hook in group["hooks"]:
                    assert hook["type"] == "http", f"{event} must not spawn a process"

    def test_session_start_warms_the_daemon(self):
        hook = inst.build_hook_config()["SessionStart"][0]["hooks"][0]
        assert hook["type"] == "command"
        assert "--ensure-daemon" in hook["args"]

    def test_only_mutating_tools_are_intercepted(self):
        """Read/Grep/Glob would short-circuit to ALLOW anyway; intercepting is pure cost."""
        config = inst.build_hook_config()
        for event in ("PreToolUse", "PostToolUse"):
            matcher = config[event][0]["matcher"]
            assert "Edit" in matcher and "Write" in matcher and "Bash" in matcher
            assert "Read" not in matcher and "Grep" not in matcher

    def test_carries_auth_token(self):
        hook = inst.build_hook_config()["PreToolUse"][0]["hooks"][0]
        assert hook["headers"]["Authorization"].startswith("Bearer ")
        assert hook["headers"]["X-AgentGuard"] == "1"

    def test_timeouts_are_bounded(self):
        """A wedged daemon must not hold the developer hostage."""
        config = inst.build_hook_config()
        for groups in config.values():
            for group in groups:
                for hook in group["hooks"]:
                    assert 0 < hook["timeout"] <= 20


class TestInstall:
    def test_creates_file_and_hooks(self, settings_file):
        result, changed = inst.install(settings_file)
        assert changed
        assert settings_file.exists()
        assert inst.is_installed(settings_file)
        assert "PreToolUse" in result["hooks"]

    def test_is_idempotent(self, settings_file):
        inst.install(settings_file)
        first = json.loads(settings_file.read_text())
        _, changed = inst.install(settings_file)
        second = json.loads(settings_file.read_text())
        assert not changed
        assert first == second
        assert len(second["hooks"]["PreToolUse"]) == 1

    def test_preserves_unrelated_settings(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            json.dumps({"model": "opus", "env": {"FOO": "bar"}, "permissions": {"allow": ["Bash(ls)"]}})
        )
        result, _ = inst.install(settings_file)
        assert result["model"] == "opus"
        assert result["env"] == {"FOO": "bar"}
        assert result["permissions"] == {"allow": ["Bash(ls)"]}

    def test_preserves_the_users_own_hooks(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/my-linter"}]}
        settings_file.write_text(json.dumps({"hooks": {"PreToolUse": [mine]}}))

        result, _ = inst.install(settings_file)
        assert mine in result["hooks"]["PreToolUse"]
        assert len(result["hooks"]["PreToolUse"]) == 2

    def test_dry_run_writes_nothing(self, settings_file):
        result, changed = inst.install(settings_file, dry_run=True)
        assert changed
        assert result["hooks"]
        assert not settings_file.exists()

    def test_refuses_to_clobber_invalid_json(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text("{ this is not json")
        with pytest.raises(RuntimeError, match="not valid JSON"):
            inst.install(settings_file)
        assert settings_file.read_text() == "{ this is not json"


class TestUninstall:
    def test_removes_exactly_what_was_added(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        original = {
            "model": "opus",
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/mine"}]}
                ]
            },
        }
        settings_file.write_text(json.dumps(original))

        inst.install(settings_file)
        assert inst.is_installed(settings_file)

        inst.uninstall(settings_file)
        assert not inst.is_installed(settings_file)
        assert json.loads(settings_file.read_text()) == original

    def test_removes_the_hooks_key_when_it_becomes_empty(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"model": "opus"}))
        inst.install(settings_file)
        inst.uninstall(settings_file)
        assert json.loads(settings_file.read_text()) == {"model": "opus"}

    def test_uninstall_on_a_clean_file_is_a_no_op(self, settings_file):
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"model": "opus"}))
        _, changed = inst.uninstall(settings_file)
        assert not changed
