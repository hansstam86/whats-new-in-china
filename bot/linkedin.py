"""
LinkedIn link-poster for the china-feed bot.

Posts a short text + link share to your personal profile via the UGC Posts API.
No image upload: the dispatch link is the payload, so LinkedIn renders its own
preview card from the site.

Self-serve "Share on LinkedIn" apps get a 60-day access token and NO refresh
token (programmatic refresh is gated behind LinkedIn's partner program). So when
the token lapses you re-run linkedin_auth.py. The bot warns you first.

Token file (linkedin_tokens.json) is produced by linkedin_auth.py. This module
loads its own .env so it does not depend on bot.py's import order.
"""

import os
import json
import time
import pathlib

import requests

HERE = pathlib.Path(__file__).parent
TOKENS_FILE = HERE / "linkedin_tokens.json"
UGC_URL = "https://api.linkedin.com/v2/ugcPosts"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
MAX_LEN = 2900  # LinkedIn commentary limit is ~3000 chars


def _load_env(path=".env"):
    p = HERE / path
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


class LinkedInError(Exception):
    pass


class TokenExpired(LinkedInError):
    pass


def _client():
    return (os.environ.get("LINKEDIN_CLIENT_ID", "").strip(),
            os.environ.get("LINKEDIN_CLIENT_SECRET", "").strip())


def enabled():
    cid, secret = _client()
    return bool(cid and secret and TOKENS_FILE.exists())


def _load():
    if not TOKENS_FILE.exists():
        raise LinkedInError("no token - run linkedin_auth.py")
    return json.loads(TOKENS_FILE.read_text())


def _save(t):
    TOKENS_FILE.write_text(json.dumps(t, indent=2))


def days_left():
    try:
        t = _load()
    except Exception:
        return None
    return max(0, int((t.get("expires_at", 0) - time.time()) // 86400))


def _person_urn(t):
    urn = t.get("person_urn")
    if urn:
        return urn
    r = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {t['access_token']}"}, timeout=30)
    if r.status_code == 401:
        raise TokenExpired("token rejected (401) - run linkedin_auth.py")
    r.raise_for_status()
    urn = f"urn:li:person:{r.json()['sub']}"
    t["person_urn"] = urn
    _save(t)
    return urn


def _post_commentary(commentary):
    t = _load()
    if t.get("expires_at", 0) - time.time() <= 0:
        raise TokenExpired("token expired - run linkedin_auth.py")
    person_urn = _person_urn(t)
    payload = {
        "author": person_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": commentary[:MAX_LEN]},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    headers = {
        "Authorization": f"Bearer {t['access_token']}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    r = requests.post(UGC_URL, headers=headers, json=payload, timeout=30)
    if r.status_code == 401:
        raise TokenExpired("token rejected (401) - run linkedin_auth.py")
    if r.status_code not in (200, 201):
        raise LinkedInError(f"{r.status_code}: {r.text[:300]}")
    return r.headers.get("x-restli-id", "posted")


def post_text(commentary):
    """Post arbitrary commentary (used for the daily roundup)."""
    return _post_commentary(commentary)


def post_link(text, url):
    body = (text or "").strip()
    commentary = (body + "\n\n" + url) if body else ("New dispatch\n\n" + url)
    return _post_commentary(commentary)
