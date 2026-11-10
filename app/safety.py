"""Prompt-injection AWARENESS - detect and log, never block. The real
defenses are the RULES text and the booking confirmation gate. See the ADK
project's common/safety.py for the reasoning."""

import logging

logger = logging.getLogger("reset_fitness.safety")

_PATTERNS = (
    "ignore previous instructions", "ignore the above", "ignore all previous",
    "disregard previous", "disregard the above", "disregard your instructions",
    "forget your instructions", "forget the above", "new instructions:",
    "system prompt", "you are now", "act as a", "act as if", "pretend you are",
    "pretend to be", "reveal your instructions", "reveal your prompt",
    "repeat your instructions", "print your instructions", "what are your instructions",
    "what is your system prompt", "developer mode", "jailbreak", "override your",
    "bypass your", "do anything now",
)


def detect_injection_attempt(text: str) -> str | None:
    lowered = (text or "").lower()
    return next((p for p in _PATTERNS if p in lowered), None)


def log_if_injection_attempt(agent_slug: str, thread_id: str, text: str) -> None:
    matched = detect_injection_attempt(text)
    if matched:
        logger.warning(
            "Possible prompt injection - agent=%s thread=%s matched=%r text=%r",
            agent_slug, thread_id, matched, text.strip().replace("\n", " ")[:200])
