#!/usr/bin/env python3
"""
LinkedIn cross-poster for personal profiles.

Publishes text (and an optional image) to the authenticated member's feed via
the /rest/posts API. Handles the 3-step image upload and access-token refresh.

Requires, in the environment (bot/.env):
  LINKEDIN_CLIENT_ID
  LINKEDIN_CLIENT_SECRET
  LINKEDIN_VERSION        (optional, YYYYMM, defaults below)

And a token file (bot/linkedin_tokens.json) produced once by linkedin_auth.py.

If credentials or tokens are missing, enabled() returns False and the bot
simply skips LinkedIn without failing the site post.
"""

import os
import json
import time
import pathlib

import requests

HERE = pathlib.Path(__file__).parent
TOKENS_FILE = HERE / "linkedin_tokens.json"

CLIENT_ID = os.environ.get("LINKEDIN_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip()
VERSION = os.environ.get("LINKEDIN_VERSION", "202506").strip()

TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
POSTS_URL = "https://api.linkedin.com/rest/posts"
IMAGES_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"

# Characters LinkedIn's post "commentary" treats as reserved. Escaping keeps
# plain captions from returning 400s. They render as the literal character.
_RESERVED = r"\|{}@[]()<>#*_~"


def enabled():
    return bool(CLIENT_ID and CLIENT_SECRET and TOKENS_FILE.exists())


def _load_tokens():
    return json.loads(TOKENS_FILE.read_text())


def _save_tokens(t):
    TOKENS_FILE.write_text(json.dumps(t, indent=2))


def _refresh(tokens):
    r = requests.post(TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }, timeout=30)
    r.raise_for_status()
    data = r.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = int(time.time()) + int(data.get("expires_in", 5184000))
    if data.get("refresh_token"):
        tokens["refresh_token"] = data["refresh_token"]
    _save_tokens(tokens)
    return tokens


def _access_token():
    tokens = _load_tokens()
    # refresh a day before expiry
    if int(time.time()) > int(tokens.get("expires_at", 0)) - 86400:
        tokens = _refresh(tokens)
    return tokens["access_token"], tokens


def _person_urn(token, tokens):
    urn = tokens.get("person_urn")
    if urn:
        return urn
    r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    sub = r.json()["sub"]
    urn = f"urn:li:person:{sub}"
    tokens["person_urn"] = urn
    _save_tokens(tokens)
    return urn


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": "application/json",
    }


def _escape(text):
    out = []
    for ch in text or "":
        if ch in _RESERVED:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _upload_image(token, person_urn, image_path):
    init = requests.post(IMAGES_URL, headers=_headers(token),
                         json={"initializeUploadRequest": {"owner": person_urn}}, timeout=30)
    init.raise_for_status()
    value = init.json()["value"]
    upload_url = value["uploadUrl"]
    image_urn = value["image"]
    with open(image_path, "rb") as f:
        put = requests.put(upload_url, data=f.read(),
                           headers={"Authorization": f"Bearer {token}"}, timeout=120)
    put.raise_for_status()
    return image_urn


def post(text, image_path=None):
    """Publish to the member's feed. Returns the post URN. Raises on failure."""
    token, tokens = _access_token()
    person_urn = _person_urn(token, tokens)

    commentary = _escape((text or "").strip())[:2999]
    body = {
        "author": person_urn,
        "commentary": commentary,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if image_path and pathlib.Path(image_path).exists():
        image_urn = _upload_image(token, person_urn, image_path)
        body["content"] = {"media": {
            "id": image_urn,
            "altText": (text or "field dispatch")[:290],
        }}

    r = requests.post(POSTS_URL, headers=_headers(token), json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"LinkedIn {r.status_code}: {r.text[:400]}")
    return r.headers.get("x-restli-id", "posted")
