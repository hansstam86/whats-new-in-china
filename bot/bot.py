#!/usr/bin/env python3
"""
What's New in China - Telegram publisher bot.

Send the bot a photo with a caption (or plain text). It saves the photo into
the site repo, appends the post to posts.json, then git commits and pushes.
GitHub Pages does the rest.

LinkedIn (optional): instead of posting each link immediately, the bot posts ONE
roundup per day at 20:00 Europe/Berlin, covering that day's new dispatches, and
only if there are any. Put #noli in a caption to keep that dispatch out of the
roundup. Commands: /roundup posts now, /preview shows tonight's text.

Runs on the Mac mini via long-polling. No public URL / webhook needed.
"""

import os
import sys
import json
import time
import pathlib
import datetime
import subprocess

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import linkedin as li
except Exception:
    li = None


def load_env(path=".env"):
    p = pathlib.Path(__file__).parent / path
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
AUTHORIZED_USER_ID = os.environ.get("AUTHORIZED_USER_ID", "").strip()
SITE_DIR = pathlib.Path(os.environ.get("SITE_DIR", "../")).expanduser().resolve()
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").strip().rstrip("/")
LINKEDIN_TZ = os.environ.get("LINKEDIN_TZ", "Europe/Berlin").strip() or "Europe/Berlin"
DAILY_HOUR = int(os.environ.get("LINKEDIN_DAILY_HOUR", "20") or "20")

if not BOT_TOKEN or not AUTHORIZED_USER_ID:
    sys.exit("BOT_TOKEN and AUTHORIZED_USER_ID must be set (in bot/.env or the environment).")

AUTHORIZED_USER_ID = int(AUTHORIZED_USER_ID)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
POSTS_JSON = SITE_DIR / "posts.json"
STATE_FILE = pathlib.Path(__file__).parent / "linkedin_state.json"

# ============================================================================
# EDIT YOUR LINKEDIN COPY HERE
# {date} -> e.g. "13 July"   {site} -> your site URL   {n} -> number of items
# ============================================================================
LI_HEADER = "What's new in China \u2014 {date}"
LI_MULTI_INTRO = "{n} things that caught my eye on the ground today:"
LI_FOOTER = "Full feed \u2192 {site}"
LI_HASHTAGS = "#China #Shenzhen #hardware #manufacturing #supplychain"
LI_MAX_ITEMS = 10  # if a day has more, list this many and add "+N more"
# ============================================================================

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def api(method, **params):
    r = requests.post(f"{API}/{method}", data=params, timeout=70)
    r.raise_for_status()
    return r.json()


def send(chat_id, text):
    try:
        api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")
    except Exception as e:
        print("send error:", e, flush=True)


def notify(text):
    send(AUTHORIZED_USER_ID, text)


def load_posts():
    if POSTS_JSON.exists():
        try:
            return json.loads(POSTS_JSON.read_text() or "[]")
        except json.JSONDecodeError:
            return []
    return []


def save_posts(posts):
    POSTS_JSON.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n")


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(s):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def git(*args):
    subprocess.run(["git", "-C", str(SITE_DIR), *args],
                   check=True, capture_output=True, text=True)


def publish(message):
    git("add", "-A")
    git("commit", "-m", message)
    git("pull", "--rebase", "--autostash")
    git("push")


def download_photo(file_id, dest):
    info = api("getFile", file_id=file_id)
    file_path = info["result"]["file_path"]
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)


def new_id():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")


def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def add_post(tmp_image_path, text):
    posts = load_posts()
    pid = new_id()
    while any(p.get("id") == pid for p in posts):
        time.sleep(1)
        pid = new_id()
    image_rel = ""
    if tmp_image_path:
        image_rel = f"images/{pid}.jpg"
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        os.replace(tmp_image_path, SITE_DIR / image_rel)
    posts.insert(0, {"id": pid, "date": iso_now(), "text": text, "image": image_rel})
    save_posts(posts)
    return pid


def link_for(pid):
    return f"{SITE_BASE_URL}/#{pid}" if SITE_BASE_URL else "the site"


def dispatch_url(pid):
    return f"{SITE_BASE_URL}/#{pid}" if SITE_BASE_URL else ""


def parse_li(text):
    """Return (clean_text, include_in_roundup). '#noli' excludes the dispatch."""
    if "#noli" in text.lower():
        cleaned = " ".join(w for w in text.split() if w.lower() != "#noli").strip()
        return cleaned, False
    return text, True


# --------------------------------------------------------------------------
# LinkedIn daily roundup
# --------------------------------------------------------------------------

def berlin_now():
    if ZoneInfo is not None:
        try:
            return datetime.datetime.now(ZoneInfo(LINKEDIN_TZ))
        except Exception as e:
            print("tz error, falling back to local:", e, flush=True)
    return datetime.datetime.now()


def _fmt_date(dt):
    return f"{dt.day} {MONTHS[dt.month - 1]}"


def _short(s, n=100):
    s = (s or "").strip()
    s = s.splitlines()[0] if s else "New dispatch"
    return s if len(s) <= n else s[:n - 1].rstrip() + "\u2026"


def build_digest(batch):
    """batch is oldest-first, already filtered."""
    date = _fmt_date(berlin_now())
    header = LI_HEADER.format(date=date)
    footer = LI_FOOTER.format(site=SITE_BASE_URL)

    if len(batch) == 1:
        p = batch[0]
        cap = (p.get("text") or "").strip() or "New dispatch from the ground."
        return f"{header}\n\n{cap}\n\n{dispatch_url(p['id'])}\n\n{footer}\n{LI_HASHTAGS}"

    shown = batch[:LI_MAX_ITEMS]
    overflow = len(batch) - len(shown)
    intro = LI_MULTI_INTRO.format(n=len(batch))
    lines = [f"\u2022 {_short(p.get('text'))}\n  {dispatch_url(p['id'])}" for p in shown]
    body = "\n".join(lines)
    if overflow > 0:
        body += f"\n\n+{overflow} more \u2192 {SITE_BASE_URL}"
    return f"{header}\n\n{intro}\n\n{body}\n\n{footer}\n{LI_HASHTAGS}"


def _eligible_batch(state):
    """Un-announced, non-excluded dispatches, oldest-first."""
    last = state.get("last_announced_id", "")
    excluded = set(state.get("excluded", []))
    posts = load_posts()
    batch = [p for p in posts if p.get("id", "") > last and p.get("id") not in excluded]
    return list(reversed(batch))  # oldest-first for reading order


def init_li_state():
    """On first enable, don't backfill history: only today's dispatches (Berlin)
    are eligible for tonight's first roundup; everything older is marked done."""
    state = load_state()
    if "last_announced_id" in state:
        return state
    today = berlin_now().strftime("%Y%m%d")
    older = [p["id"] for p in load_posts() if p.get("id", "")[:8] < today]
    state["last_announced_id"] = max(older) if older else ""
    state.setdefault("excluded", [])
    save_state(state)
    return state


def run_roundup(manual=False, mark_today=None):
    """Post the day's roundup. Returns a short status string."""
    if not (li and li.enabled()):
        return "LinkedIn not configured."
    if not SITE_BASE_URL:
        return "SITE_BASE_URL not set."

    state = load_state()
    batch = _eligible_batch(state)
    if mark_today:
        state["last_fired_date"] = mark_today  # scheduled run: don't retry all day

    if not batch:
        save_state(state)
        return "No new dispatches for today."

    text = build_digest(batch)
    try:
        li.post_text(text)
        state["last_announced_id"] = max(p["id"] for p in batch)
        save_state(state)
        d = li.days_left()
        tail = f" (LinkedIn token: {d}d left, re-run linkedin_auth.py soon.)" if (d is not None and d <= 7) else ""
        return f"Posted LinkedIn roundup: {len(batch)} dispatch(es).{tail}"
    except li.TokenExpired as e:
        save_state(state)  # keep last_announced_id so it retries after re-auth
        return f"LinkedIn roundup not posted: {e}"
    except Exception as e:
        save_state(state)
        return f"LinkedIn roundup failed: {str(e)[:200]}"


def maybe_run_scheduler():
    if not (li and li.enabled()):
        return
    now = berlin_now()
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    if now.hour >= DAILY_HOUR and state.get("last_fired_date") != today:
        status = run_roundup(manual=False, mark_today=today)
        # stay quiet on the "nothing new" case; only ping on a real post or error
        if not status.startswith("No new"):
            notify(status)


# --------------------------------------------------------------------------

def roundup_note(include):
    if not (li and li.enabled()):
        return ""
    if not include:
        return "\nExcluded from LinkedIn."
    return f"\nQueued for the {DAILY_HOUR:02d}:00 {LINKEDIN_TZ.split('/')[-1]} LinkedIn roundup."


def handle(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        send(chat_id, "Send a photo with a caption to post it to the site.\n"
                      "Plain text also works as a text-only note.\n"
                      f"LinkedIn posts one roundup daily at {DAILY_HOUR:02d}:00 "
                      f"{LINKEDIN_TZ.split('/')[-1]}, only if there are new dispatches.\n"
                      "Add #noli to a caption to keep that dispatch out of LinkedIn.\n"
                      "/roundup posts the LinkedIn roundup now.\n"
                      "/republish forces a fresh site rebuild.\n"
                      "/preview shows tonight's roundup text without posting.\n"
                      "/undo removes the most recent post.\n"
                      "/id shows your Telegram user id.")
        return
    if text.startswith("/id"):
        send(chat_id, f"Your Telegram user id: {user_id}")
        return

    if user_id != AUTHORIZED_USER_ID:
        send(chat_id, "Not authorized to post here.")
        return

    if text.startswith("/republish"):
        try:
            git("commit", "--allow-empty", "-m", "force pages rebuild")
            git("pull", "--rebase", "--autostash")
            git("push")
            send(chat_id, "Triggered a fresh site rebuild.")
        except subprocess.CalledProcessError as e:
            send(chat_id, f"Republish failed:\n{(e.stderr or str(e))[:300]}")
        return

    if text.startswith("/roundup"):
        send(chat_id, run_roundup(manual=True))
        return

    if text.startswith("/preview"):
        if not SITE_BASE_URL:
            send(chat_id, "SITE_BASE_URL not set.")
            return
        batch = _eligible_batch(load_state())
        if not batch:
            send(chat_id, "Nothing queued for the next roundup.")
        else:
            send(chat_id, "Tonight's roundup would read:\n\n" + build_digest(batch))
        return

    if text.startswith("/undo"):
        posts = load_posts()
        if not posts:
            send(chat_id, "Nothing to undo.")
            return
        removed = posts.pop(0)
        img = removed.get("image")
        if img and (SITE_DIR / img).exists():
            (SITE_DIR / img).unlink()
        save_posts(posts)
        publish(f"undo {removed.get('id')}")
        send(chat_id, "Removed the most recent post. (If already announced on LinkedIn, that post stays.)")
        return

    if "photo" in msg:
        clean, include = parse_li(caption)
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        tmp = SITE_DIR / "images" / "_incoming.jpg"
        download_photo(msg["photo"][-1]["file_id"], tmp)
        pid = add_post(tmp, clean)
        publish(f"post {pid}")
        if not include:
            state = load_state()
            state.setdefault("excluded", []).append(pid)
            save_state(state)
        send(chat_id, f"Posted. {link_for(pid)}" + roundup_note(include))
        return

    if text and not text.startswith("/"):
        clean, include = parse_li(text)
        pid = add_post("", clean)
        publish(f"note {pid}")
        if not include:
            state = load_state()
            state.setdefault("excluded", []).append(pid)
            save_state(state)
        send(chat_id, f"Posted note. {link_for(pid)}" + roundup_note(include))
        return

    send(chat_id, "Send a photo with a caption, or plain text. /help for options.")


def main():
    print(f"china-feed bot up. SITE_DIR={SITE_DIR}", flush=True)
    if li and li.enabled():
        init_li_state()
        print(f"LinkedIn roundup enabled: daily at {DAILY_HOUR:02d}:00 {LINKEDIN_TZ}", flush=True)
    offset = None
    while True:
        try:
            resp = api("getUpdates", offset=offset, timeout=45)
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue
                try:
                    handle(msg)
                except subprocess.CalledProcessError as e:
                    err = (e.stderr or str(e)).strip()
                    print("git error:", err, flush=True)
                    cid = msg.get("chat", {}).get("id")
                    if cid:
                        send(cid, f"Saved locally but publish failed:\n{err[:300]}")
                except Exception as e:
                    print("handler error:", e, flush=True)
                    cid = msg.get("chat", {}).get("id")
                    if cid:
                        send(cid, f"Error: {e}")
            maybe_run_scheduler()
        except requests.exceptions.RequestException as e:
            print("poll error:", e, flush=True)
            time.sleep(5)
        except Exception as e:
            print("loop error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
