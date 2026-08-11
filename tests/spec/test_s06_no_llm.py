"""SPEC §6, §22, §46.1 — AgentGuard must not own an LLM.

    "AgentGuard must be able to operate without owning or hosting its own LLM."
    "The majority of operations should be deterministic."

This is the project's defining constraint. If AgentGuard ever starts making its own model
calls it has become "another LLM wrapper" (SPEC §47) and the thesis is dead. These tests
are cheap and permanent insurance against that drift.
"""

from __future__ import annotations

import socket
import tomllib

import pytest

from agentguard.adapters.claude_code import translate as claude
from agentguard.core.config import Settings
from agentguard.core.engine import Guard
from tests.conftest import REPO_ROOT, pre_tool_use, session_start, stop_event, user_prompt_submit

pytestmark = pytest.mark.spec

LLM_PACKAGES = {
    "anthropic", "openai", "google-generativeai", "google-genai", "vertexai",
    "cohere", "mistralai", "litellm", "langchain", "langchain-core", "llama-index",
    "transformers", "torch", "sentence-transformers", "ollama", "replicate",
    "huggingface-hub", "boto3",  # boto3 would imply Bedrock
}

LLM_IMPORT_NAMES = {
    "anthropic", "openai", "google.generativeai", "google.genai", "vertexai", "cohere",
    "mistralai", "litellm", "langchain", "llama_index", "transformers", "torch",
    "sentence_transformers", "ollama", "replicate", "huggingface_hub",
}

LLM_API_HOSTS = {
    "api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com",
    "api.cohere.ai", "api.mistral.ai", "api.together.xyz", "openrouter.ai",
}


def source_files() -> list:
    return sorted((REPO_ROOT / "src" / "agentguard").rglob("*.py"))


def test_no_llm_sdk_is_declared_as_a_dependency():
    manifest = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = list(manifest["project"]["dependencies"])
    for extra in manifest["project"].get("optional-dependencies", {}).values():
        declared.extend(extra)

    names = {
        spec.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
        for spec in declared
    }
    offenders = names & LLM_PACKAGES
    assert not offenders, f"LLM SDK declared as a dependency: {offenders}"


def test_no_source_file_imports_an_llm_sdk():
    import ast

    offenders: list[str] = []
    for path in source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                root = module.split(".")[0]
                if module in LLM_IMPORT_NAMES or root in LLM_IMPORT_NAMES:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}: {module}")

    assert not offenders, "AgentGuard imports an LLM SDK:\n" + "\n".join(offenders)


def test_no_source_file_references_an_llm_api_endpoint():
    offenders: list[str] = []
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        for host in LLM_API_HOSTS:
            if host in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {host}")
    assert not offenders, "AgentGuard references an LLM API endpoint:\n" + "\n".join(offenders)


def test_handling_a_full_session_opens_no_network_connection(workspace, monkeypatch):
    """The strongest form of the check: watch the socket layer during real work.

    SPEC §30: "Core should work offline whenever possible." A decision that needed the
    network could not be made in under 100ms anyway (SPEC §8).
    """
    connections: list[object] = []
    real_connect = socket.socket.connect

    def spy_connect(self, address):
        connections.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy_connect)

    guard = Guard(Settings())
    try:
        payloads = [
            session_start(cwd=str(workspace)),
            user_prompt_submit("Add pagination to the /users endpoint", cwd=str(workspace)),
            pre_tool_use("Edit", cwd=str(workspace), file_path="a.py", old_string="x", new_string="y"),
            pre_tool_use("Bash", cwd=str(workspace), command="pytest -q"),
            {"hook_event_name": "PostToolUse", "cwd": str(workspace), "session_id": "s",
             "tool_name": "Edit", "tool_input": {}, "tool_output": "ok"},
            stop_event(cwd=str(workspace)),
            {"hook_event_name": "SessionEnd", "cwd": str(workspace), "session_id": "s"},
        ]
        for payload in payloads:
            event = claude.to_event(payload)
            assert event is not None
            guard.handle(event)
    finally:
        guard.close()

    assert not connections, f"AgentGuard opened network connections: {connections}"
