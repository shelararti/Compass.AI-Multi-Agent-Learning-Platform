"""
Thin wrapper around the local Ollama chat endpoint.

Every agent goes through this module rather than calling urllib directly,
so retries, JSON coaxing, and error formatting live in exactly one place.
"""

import json
import re
import urllib.request
import urllib.error

from . import config


def call_llm_messages(messages: list, timeout: int = config.LLM_TIMEOUT_SECONDS) -> str:
    """Call the local Ollama chat endpoint with a full messages list (system
    + however much conversation history the caller wants included) and
    return the assistant's text. Use this instead of call_llm when a single
    system+user pair isn't enough context (e.g. a multi-turn chat)."""
    payload = {"model": config.MODEL_NAME, "messages": messages, "stream": False}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_URL, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not reach Ollama at {config.OLLAMA_URL}. Is it running? "
            f"(ollama serve / ollama run {config.MODEL_NAME}) Details: {e}"
        )
    message = body.get("message", {})
    text = message.get("content", "")
    if not text:
        raise RuntimeError("Empty response from the model.")
    return text.strip()


def call_llm(system: str, user: str, timeout: int = config.LLM_TIMEOUT_SECONDS) -> str:
    """Call the local Ollama chat endpoint with a single system+user turn."""
    return call_llm_messages(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        timeout=timeout,
    )


def extract_json(text: str):
    """Best-effort JSON extraction. Small local models often wrap JSON in
    prose or code fences, or produce near-valid JSON -- this tries fallbacks."""
    cleaned = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(cleaned)):
            if cleaned[i] == "{":
                depth += 1
            elif cleaned[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start : i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break
    raise ValueError("Could not parse JSON from model output.")


def call_llm_json(system: str, user: str, attempts: int = config.JSON_CALL_ATTEMPTS):
    """Call the LLM expecting JSON back, retrying with a stricter reminder
    if parsing fails -- small local models sometimes need a nudge."""
    last_err = None
    for attempt in range(attempts):
        prompt = user
        if attempt > 0:
            prompt += (
                "\n\nReminder: reply with ONLY the JSON object. "
                "No markdown fences, no explanation, no extra text."
            )
        try:
            raw = call_llm(system, prompt)
            return extract_json(raw)
        except (ValueError, RuntimeError) as e:
            last_err = e
    raise RuntimeError(f"Model did not return valid JSON after {attempts} attempts: {last_err}")
