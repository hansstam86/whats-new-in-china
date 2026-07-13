#!/usr/bin/env python3
"""
What's New in China - Telegram publisher bot.

Send the bot a photo with a caption (or plain text). It saves the photo into
the site repo, appends the post to posts.json, then git commits and pushes.
GitHub Pages does the rest.

LinkedIn (optional, human in the loop): at 20:00 Europe/Berlin, if there are new
dispatches that day, the bot messages you a reminder and asks for the caption.
You reply with the words; the bot appends the dispatch links and posts it. Put
#noli in a caption to keep that dispatch out of LinkedIn.

LinkedIn commands: reply with text after the reminder to post, or use
/li <caption> anytime, /skip to pass, /preview to see the queued links,
/prompt to trigger the reminder now.

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

TZ_LABEL = LINKEDIN_TZ.split("/")[-1]  # e.g. "Berlin"


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


def add_post(tmp_paths, text):
    """tmp_paths: list of temp image file paths (0, 1, or many)."""
    posts = load_posts()
    pid = new_id()
    while any(p.get("id") == pid for p in posts):
        time.sleep(1)
        pid = new_id()
    images = []
    if tmp_paths:
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        if len(tmp_paths) == 1:
            rel = f"images/{pid}.jpg"
            os.replace(tmp_paths[0], SITE_DIR / rel)
            images = [rel]
        else:
            for i, tp in enumerate(tmp_paths, 1):
                rel = f"images/{pid}-{i}.jpg"
                os.replace(tp, SITE_DIR / rel)
                images.append(rel)
    post = {"id": pid, "date": iso_now(), "text": text}
    if len(images) > 1:
        post["images"] = images
    else:
        post["image"] = images[0] if images else ""
    posts.insert(0, post)
    save_posts(posts)
    return pid, len(images)


def link_for(pid):
    return f"{SITE_BASE_URL}/#{pid}" if SITE_BASE_URL else "the site"


def dispatch_url(pid):
    return f"{SITE_BASE_URL}/#{pid}" if SITE_BASE_URL else ""


def parse_li(text):
    """Return (clean_text, include_in_linkedin). '#noli' excludes the dispatch."""
    if "#noli" in text.lower():
        cleaned = " ".join(w for w in text.split() if w.lower() != "#noli").strip()
        return cleaned, False
    return text, True


# --------------------------------------------------------------------------
# LinkedIn: reminder + you-compose flow
# --------------------------------------------------------------------------

def berlin_now():
    if ZoneInfo is not None:
        try:
            return datetime.datetime.now(ZoneInfo(LINKEDIN_TZ))
        except Exception as e:
            print("tz error, falling back to local:", e, flush=True)
    return datetime.datetime.now()


def _berlin_date(iso):
    try:
        dt = datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        return None
    if ZoneInfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo(LINKEDIN_TZ))
        except Exception:
            pass
    return dt.date().isoformat()


def _short(s, n=90):
    s = (s or "").strip()
    s = s.splitlines()[0] if s else "New dispatch"
    return s if len(s) <= n else s[:n - 1].rstrip() + "\u2026"


def eligible_batch(state=None):
    """Un-announced, non-excluded dispatches, oldest-first."""
    state = state if state is not None else load_state()
    last = state.get("last_announced_id", "")
    excluded = set(state.get("excluded", []))
    posts = load_posts()
    batch = [p for p in posts if p.get("id", "") > last and p.get("id") not in excluded]
    return list(reversed(batch))


def init_li_state():
    """On first enable, don't backfill history: only today's dispatches (Berlin)
    are eligible; everything older is marked already announced."""
    state = load_state()
    if "last_announced_id" in state:
        return state
    today = berlin_now().strftime("%Y%m%d")
    older = [p["id"] for p in load_posts() if p.get("id", "")[:8] < today]
    state["last_announced_id"] = max(older) if older else ""
    state.setdefault("excluded", [])
    save_state(state)
    return state


def _links_block(batch):
    return "\n".join(dispatch_url(p["id"]) for p in batch if dispatch_url(p["id"]))


def compose_and_post(caption):
    """Post the caption + the eligible dispatch links to LinkedIn."""
    if not (li and li.enabled()):
        return "LinkedIn is not set up. See bot/LINKEDIN.md."
    if not SITE_BASE_URL:
        return "SITE_BASE_URL not set."
    state = load_state()
    batch = eligible_batch(state)
    if not batch:
        return "No new dispatches to post."
    caption = (caption or "").strip()
    if not caption:
        return "Send some caption text to go with the links."
    body = f"{caption}\n\n{_links_block(batch)}"
    try:
        li.post_text(body)
    except li.TokenExpired as e:
        return f"Not posted: {e}. Re-run linkedin_auth.py, then resend."
    except Exception as e:
        return f"LinkedIn post failed: {str(e)[:200]}"
    state["last_announced_id"] = max(p["id"] for p in batch)
    state.pop("pending", None)
    state.pop("pending_date", None)
    save_state(state)
    d = li.days_left()
    tail = f"\nLinkedIn token: {d}d left, re-run linkedin_auth.py soon." if (d is not None and d <= 7) else ""
    return f"Posted to LinkedIn: {len(batch)} dispatch(es).{tail}"


def skip_linkedin():
    state = load_state()
    batch = eligible_batch(state)
    if not batch:
        return "Nothing queued for LinkedIn."
    state["last_announced_id"] = max(p["id"] for p in batch)
    state.pop("pending", None)
    state.pop("pending_date", None)
    save_state(state)
    return f"Skipped. {len(batch)} dispatch(es) will not be posted to LinkedIn."


def prompt_text(batch):
    lines = [f"- {_short(p.get('text'))}\n  {dispatch_url(p['id'])}" for p in batch]
    return (f"{len(batch)} new dispatch(es) today. What should the LinkedIn post say?\n\n"
            + "\n".join(lines)
            + "\n\nReply with your caption (the links get appended), or /skip.\n"
              "Explicit: /li <caption>. See links: /preview.")


def send_prompt(manual=False):
    if not (li and li.enabled()):
        return "LinkedIn is not set up. See bot/LINKEDIN.md." if manual else None
    if not SITE_BASE_URL:
        return "SITE_BASE_URL not set." if manual else None
    state = load_state()
    batch = eligible_batch(state)
    if not batch:
        return "No new dispatches to post." if manual else None
    state["pending"] = True
    state["pending_date"] = berlin_now().date().isoformat()
    save_state(state)
    notify(prompt_text(batch))
    return "Reminder sent." if manual else None


def maybe_run_scheduler():
    if not (li and li.enabled()):
        return
    now = berlin_now()
    today = now.strftime("%Y-%m-%d")
    state = load_state()
    if now.hour < DAILY_HOUR or state.get("last_fired_date") == today:
        return
    batch = eligible_batch(state)
    today_iso = now.date().isoformat()
    has_new_today = any(_berlin_date(p.get("date")) == today_iso for p in batch)
    state["last_fired_date"] = today
    save_state(state)
    if batch and has_new_today:
        send_prompt(manual=False)


def pending_active():
    state = load_state()
    return bool(state.get("pending")) and state.get("pending_date") == berlin_now().date().isoformat()


def li_enabled():
    try:
        return bool(li and li.enabled())
    except Exception as e:
        print("linkedin enabled() error:", e, flush=True)
        return False


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Album buffering: a Telegram album arrives as several messages sharing a
# media_group_id, caption on the first. Buffer them and flush as one dispatch.
# --------------------------------------------------------------------------

ALBUMS = {}          # media_group_id -> {"file_ids", "caption", "chat_id", "last"}
ALBUM_FLUSH_SEC = 2.5


def buffer_album(msg):
    mgid = msg["media_group_id"]
    buf = ALBUMS.setdefault(mgid, {"file_ids": [], "caption": "", "chat_id": msg["chat"]["id"], "last": 0.0})
    buf["file_ids"].append(msg["photo"][-1]["file_id"])
    cap = (msg.get("caption") or "").strip()
    if cap:
        buf["caption"] = cap
    buf["last"] = time.time()


def flush_albums(force=False):
    for mgid in [k for k, v in ALBUMS.items() if force or (time.time() - v["last"] >= ALBUM_FLUSH_SEC)]:
        buf = ALBUMS.pop(mgid)
        chat_id = buf["chat_id"]
        try:
            (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
            tmps = []
            for i, fid in enumerate(buf["file_ids"]):
                tmp = SITE_DIR / "images" / f"_albtmp-{i}.jpg"
                download_photo(fid, tmp)
                tmps.append(tmp)
            clean, include = parse_li(buf["caption"])
            pid, n = add_post(tmps, clean)
            publish(f"post {pid} ({n} photos)")
            if not include:
                state = load_state()
                state.setdefault("excluded", []).append(pid)
                save_state(state)
            note = ""
            if li_enabled():
                note = "\nExcluded from LinkedIn." if not include else f"\nWill be in the {DAILY_HOUR:02d}:00 {TZ_LABEL} LinkedIn prompt."
            send(chat_id, f"Posted {n} photos. {link_for(pid)}" + note)
        except subprocess.CalledProcessError as e:
            send(chat_id, f"Saved locally but publish failed:\n{(e.stderr or str(e))[:300]}")
        except Exception as e:
            print("album flush error:", e, flush=True)
            send(chat_id, f"Album error: {e}")


def handle(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        send(chat_id, "Send a photo with a caption to post it to the site.\n"
                      "Plain text also works as a text-only note.\n"
                      f"At {DAILY_HOUR:02d}:00 {TZ_LABEL} the bot asks you for the LinkedIn "
                      "caption, only if there are new dispatches.\n"
                      "Add #noli to a caption to keep that dispatch out of LinkedIn.\n"
                      "/li <caption> posts the queued dispatches to LinkedIn now.\n"
                      "/skip passes on LinkedIn for the queued dispatches.\n"
                      "/preview shows the queued LinkedIn links.\n"
                      "/prompt sends the LinkedIn reminder now.\n"
                      "/republish forces a fresh site rebuild.\n"
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

    if text.startswith("/li"):
        cap = text[3:].strip()
        if not cap:
            send(chat_id, "Usage: /li your caption here")
        else:
            send(chat_id, compose_and_post(cap))
        return

    if text.startswith("/skip"):
        send(chat_id, skip_linkedin())
        return

    if text.startswith("/preview"):
        if not SITE_BASE_URL:
            send(chat_id, "SITE_BASE_URL not set.")
            return
        batch = eligible_batch()
        if not batch:
            send(chat_id, "Nothing queued for LinkedIn.")
        else:
            send(chat_id, "Queued links:\n\n" + _links_block(batch)
                 + "\n\nReply with a caption or /li <caption> to post.")
        return

    if text.startswith("/prompt"):
        send(chat_id, send_prompt(manual=True))
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
        send(chat_id, "Removed the most recent post. (If already posted on LinkedIn, that stays.)")
        return

    if "photo" in msg:
        clean, include = parse_li(caption)
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        tmp = SITE_DIR / "images" / "_incoming.jpg"
        download_photo(msg["photo"][-1]["file_id"], tmp)
        pid, n = add_post([tmp], clean)
        publish(f"post {pid}")
        if not include:
            state = load_state()
            state.setdefault("excluded", []).append(pid)
            save_state(state)
        note = ""
        if li_enabled():
            note = "\nExcluded from LinkedIn." if not include else f"\nWill be in the {DAILY_HOUR:02d}:00 {TZ_LABEL} LinkedIn prompt."
        send(chat_id, f"Posted. {link_for(pid)}" + note)
        return

    if text and not text.startswith("/"):
        # If the bot asked for a LinkedIn caption today, this text is the caption.
        if li_enabled() and pending_active():
            send(chat_id, compose_and_post(text))
            return
        clean, include = parse_li(text)
        pid, n = add_post([], clean)
        publish(f"note {pid}")
        if not include:
            state = load_state()
            state.setdefault("excluded", []).append(pid)
            save_state(state)
        note = ""
        if li_enabled():
            note = "\nExcluded from LinkedIn." if not include else f"\nWill be in the {DAILY_HOUR:02d}:00 {TZ_LABEL} LinkedIn prompt."
        send(chat_id, f"Posted note. {link_for(pid)}" + note)
        return

    send(chat_id, "Send a photo with a caption, or plain text. /help for options.")


def main():
    print(f"china-feed bot up. SITE_DIR={SITE_DIR}", flush=True)
    if li and li.enabled():
        init_li_state()
        print(f"LinkedIn reminder enabled: daily at {DAILY_HOUR:02d}:00 {LINKEDIN_TZ}", flush=True)
    offset = None
    while True:
        try:
            resp = api("getUpdates", offset=offset, timeout=(1 if ALBUMS else 45))
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("channel_post")
                if not msg:
                    continue
                # Album photos: buffer instead of handling one by one.
                if (msg.get("media_group_id") and "photo" in msg
                        and msg.get("from", {}).get("id") == AUTHORIZED_USER_ID):
                    buffer_album(msg)
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
            flush_albums()
            maybe_run_scheduler()
        except requests.exceptions.RequestException as e:
            print("poll error:", e, flush=True)
            time.sleep(5)
        except Exception as e:
            print("loop error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
