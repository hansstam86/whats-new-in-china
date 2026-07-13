"""
Draft dispatch captions from photos via the Anthropic API.

Needs ANTHROPIC_API_KEY in bot/.env, and CAPTION_AI=true to switch it on.
Optional CAPTION_MODEL (defaults to a current Sonnet). Loads its own .env so
import order does not matter. If the key is missing or the toggle is off,
enabled() is False and the bot posts photos exactly as before.
"""

import os
import base64
import pathlib

import requests

HERE = pathlib.Path(__file__).parent
API_URL = "https://api.anthropic.com/v1/messages"


def _load_env(path=".env"):
    p = HERE / path
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _key():
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def _model():
    return os.environ.get("CAPTION_MODEL", "claude-sonnet-5").strip() or "claude-sonnet-5"


def enabled():
    on = os.environ.get("CAPTION_AI", "").strip().lower() in ("1", "true", "yes")
    return bool(_key()) and on


PROMPT = (
    "You are drafting a short caption for a photo feed called 'What's New in China', "
    "written by a veteran hardware entrepreneur on the ground in China. "
    "Write 1 to 3 short sentences describing what is new or notable in the photo. "
    "Style: terse, factual, first person, execution-first. No em-dashes, no hashtags, "
    "no emoji, no preamble, no sign-off. "
    "Critical accuracy rule: describe only what is clearly visible. Do NOT guess brand "
    "names, model numbers, chip part numbers, prices, or technical specs. If a detail is "
    "uncertain, describe it generically (for example 'a battery-swap cabinet', not a "
    "specific brand). Being vague is fine; being wrong is not. "
    "Output only the caption text."
)


def _media_type(p):
    return "image/png" if pathlib.Path(p).suffix.lower() == ".png" else "image/jpeg"


def draft(image_paths, notes=""):
    key = _key()
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    content = []
    for p in list(image_paths)[:3]:
        data = base64.standard_b64encode(pathlib.Path(p).read_bytes()).decode()
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": _media_type(p), "data": data}})
    ask = PROMPT
    if notes:
        ask += f"\n\nThe author added these notes to work from: {notes}"
    content.append({"type": "text", "text": ask})

    r = requests.post(API_URL, headers={
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }, json={"model": _model(), "max_tokens": 300,
             "messages": [{"role": "user", "content": content}]}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"API {r.status_code}: {r.text[:200]}")
    parts = [b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()
