#!/usr/bin/env python3
"""
One-time (and every ~60 days) LinkedIn authorization for the china-feed bot.

Run this to authorize link cross-posting and write linkedin_tokens.json.
Self-serve LinkedIn apps have NO refresh token, so when the 60-day access
token expires, just run this again. Takes about 30 seconds.

Prereqs in bot/.env:
  LINKEDIN_CLIENT_ID
  LINKEDIN_CLIENT_SECRET
  LINKEDIN_REDIRECT_URI   (must exactly match one in your app's Auth tab;
                           http://localhost:8000/callback is fine)

Usage:
  source .venv/bin/activate
  python linkedin_auth.py
Open the printed URL, approve, then paste the full redirected address back.
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
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"

params = {
    "response_type": "code",
    "client_id": CLIENT_ID,
    "redirect_uri": REDIRECT_URI,
    "scope": SCOPES,
    "state": "chinafeed",
}
print("\n1) Open this URL in a browser and approve access:\n")
print(AUTH_URL + "?" + up.urlencode(params))
print("\n2) Your browser redirects to your redirect URI (the page will not load -")
print("   that is expected). Copy the WHOLE address bar and paste it below.\n")

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
if r.status_code != 200:
    raise SystemExit(f"Token exchange failed: {r.status_code} {r.text[:300]}")
d = r.json()
access = d["access_token"]
expires_at = int(time.time()) + int(d.get("expires_in", 5184000))

ui = requests.get(USERINFO_URL, headers={"Authorization": f"Bearer {access}"}, timeout=30)
if ui.status_code != 200:
    raise SystemExit(f"Could not read profile: {ui.status_code} {ui.text[:300]}")
info = ui.json()

tokens = {
    "access_token": access,
    "expires_at": expires_at,
    "person_urn": f"urn:li:person:{info['sub']}",
    "name": info.get("name", ""),
}
(HERE / "linkedin_tokens.json").write_text(json.dumps(tokens, indent=2))

days = (expires_at - int(time.time())) // 86400
print(f"\nSaved. Connected as {info.get('name', 'member')}. Token valid ~{days} days.")
print("No refresh token on self-serve apps: re-run this before it expires.")
print("The bot will warn you in Telegram when under 7 days remain.")
