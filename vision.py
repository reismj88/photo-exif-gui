import base64
import json
import re
import urllib.request
import urllib.error
from typing import Optional

import config

_PEOPLE_PROMPT = (
    "Are there people or faces visible in this image? "
    "Answer yes or no only."
)
_SUBJECT_PROMPT = (
    "Is the main subject in focus and sharp? "
    "Ignore background blur. "
    "Rate sharpness of the subject only from 0-100. "
    "Respond with only a number."
)
_FRAME_PROMPT = (
    "Is the entire frame sharp and in focus? "
    "Rate overall sharpness from 0-100. "
    "Respond with only a number."
)


def analyze_sharpness(
    image_bytes: bytes,
    aperture: Optional[float],
    has_people: Optional[bool] = None,
) -> tuple[int, str, bool]:
    """Two-pass vision analysis. Returns (score 0-100, sharpness_type, has_people)."""
    if has_people is None:
        reply = _request(_PEOPLE_PROMPT, image_bytes)
        has_people = "yes" in reply.lower()

    if has_people or (aperture is not None and aperture <= 4.0):
        prompt = _SUBJECT_PROMPT
        sharpness_type = "subject"
    else:
        prompt = _FRAME_PROMPT
        sharpness_type = "whole-frame"

    reply = _request(prompt, image_bytes)
    score = _parse_score(reply) or 50
    return score, sharpness_type, has_people


def score_to_stars(score: int) -> int:
    if score <= 20:
        return 1
    if score <= 40:
        return 2
    if score <= 60:
        return 3
    if score <= 80:
        return 4
    return 5


def _request(prompt: str, image_bytes: bytes) -> str:
    if config.VISION_BACKEND == "ollama":
        return _ollama_request(prompt, image_bytes)
    if config.VISION_BACKEND == "claude":
        return _claude_request(prompt, image_bytes)
    raise ValueError(f"Unknown VISION_BACKEND: {config.VISION_BACKEND!r}")


def _ollama_request(prompt: str, image_bytes: bytes) -> str:
    payload = json.dumps({
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "images": [base64.b64encode(image_bytes).decode()],
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        f"{config.OLLAMA_HOST}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read()).get("response", "")
    except urllib.error.URLError as exc:
        raise ConnectionError(
            f"Ollama unreachable at {config.OLLAMA_HOST}.\n"
            f"Start it with:  ollama serve\n"
            f"Pull the model: ollama pull {config.OLLAMA_MODEL}\n"
            f"({exc})"
        ) from exc


def _claude_request(prompt: str, image_bytes: bytes) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise ImportError(
            "Install the Anthropic SDK to use the Claude backend:\n"
            "  .venv/bin/pip install anthropic"
        ) from exc
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=50,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(image_bytes).decode(),
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }],
    )
    return msg.content[0].text


def _parse_score(text: str) -> Optional[int]:
    m = re.search(r'\b(\d{1,3})\b', text)
    return max(0, min(100, int(m.group(1)))) if m else None
