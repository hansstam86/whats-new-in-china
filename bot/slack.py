"""
Post each dispatch to a Slack channel via an Incoming Webhook.

Set SLACK_WEBHOOK_URL in bot/.env to the webhook URL from your Slack app
(Incoming Webhooks -> Add New Webhook to Workspace -> pick the Hardware Guild
channel). If it is unset, enabled() is False and nothing posts to Slack.

Loads its own .env so import order does not matter.
"""

import os
import pathlib

import requests

HERE = pathlib.Path(__file__).parent


def _load_env(path=".env"):
    p = HERE / path
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def _url():
    return os.environ.get("SLACK_WEBHOOK_URL", "").strip()


def enabled():
    return bool(_url())


def post(text, image_urls=None, link=None):
    url = _url()
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL not set")
    body = (text or "").strip() or "New dispatch"
    section = body + (f"\n<{link}|View on the site>" if link else "")
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": section}}]
    for iu in (image_urls or [])[:5]:
        blocks.append({"type": "image", "image_url": iu, "alt_text": "dispatch photo"})
    r = requests.post(url, json={"text": body, "blocks": blocks}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Slack {r.status_code}: {r.text[:150]}")
