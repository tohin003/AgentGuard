"""Intent Gateway — prompt to grounded TaskSpec (SPEC §9, §10)."""

from agentguard.intent.extractor import extract
from agentguard.intent.models import TaskSpec

__all__ = ["TaskSpec", "extract"]
