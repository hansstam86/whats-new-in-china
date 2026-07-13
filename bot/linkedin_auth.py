#!/usr/bin/env python3
"""
One-time LinkedIn OAuth. Run this once to authorize the bot and write
linkedin_tokens.json. After that, bot.py refreshes tokens on its own.

Prereqs (in bot/.env or the environment):
  LINKEDIN_CLIENT_ID
  LINKEDIN_CLIENT_SECRET
  LINKEDIN_REDIRECT_URI   (must match one listed in your LinkedIn app's Auth tab;
                           http://localhost:8000/callback works fine)

Usage:
  source .venv/bin/activate
  python linkedin_auth.py
Then open the printed URL, approve, and paste the full redirected URL back.
"""

import os
import json
import time
import pathlib
import urllib.parse as up

import requests

HERE = pathlib.Path(__file__).parent


def load_env(path=".env"):
    p = HERE / path
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

CLIENT_ID = os.environ["LINKEDIN_CLIENT_ID"].strip()
CLIENT_SECRET = os.environ["LINKEDIN_CLIENT_SECRET"].strip()
REDIRECT_URI = os.environ.get("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback").strip()
SCOPES = "openid profile w_member_social"

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

params = {
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPES,
    "state": "chinafeed",
}
print("\n1) Open this URL, approve access:\n")
print(AUTH_URL + "?" + up.urlencode(params))
print("\n2) Your browser will redirect to a URL that starts with your redirect URI.")
print("   It will not load a page - that is fine. Copy the WHOLE address bar and paste here.\n")

redirected = input("Paste the full redirected URL: ").strip()
qs = up.parse_qs(up.urlparse(redirected).query)
code = qs.get("code", [None])[0]
if not code:
    raise SystemExit("No ?code= found in that URL. Try again.")

r = requests.post(TOKEN_URL, data={
    "grant_type": "authorization_code",
    "code": code,
    "redirect_uri": REDIRECT_URI,
    "client_id": CLIENT_ID,
    "client_secret": CLIENT_SECRET,
}, timeout=30)
r.raise_for_status()
d = r.json()

tokens = {
    "access_token": d["access_token"],
    "refresh_token": d.get("refresh_token", ""),
    "expires_at": int(time.time()) + int(d.get("expires_in", 5184000)),
}
(HERE / "linkedin_tokens.json").write_text(json.dumps(tokens, indent=2))
print("\nSaved linkedin_tokens.json. LinkedIn cross-posting is ready.")
if not tokens["refresh_token"]:
    print("Note: no refresh_token returned. Re-run this when the token expires (~60 days).")
