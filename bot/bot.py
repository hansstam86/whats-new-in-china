#!/usr/bin/env python3
"""
What's New in China - Telegram publisher bot.

Send the bot a photo with a caption (or plain text). It saves the photo into
the site repo, appends the post to posts.json, then git commits and pushes.
GitHub Pages does the rest.

Runs on the Mac mini via long-polling. No public URL / webhook needed.

Env vars (see .env.example):
  BOT_TOKEN            - from @BotFather
  AUTHORIZED_USER_ID   - your numeric Telegram user id (only you can post)
  SITE_DIR             - path to the local clone of the site repo
  SITE_BASE_URL        - public site URL, used for the reply link (optional)
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

if not BOT_TOKEN or not AUTHORIZED_USER_ID:
    sys.exit("BOT_TOKEN and AUTHORIZED_USER_ID must be set (in bot/.env or the environment).")

AUTHORIZED_USER_ID = int(AUTHORIZED_USER_ID)
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
IMAGES_DIR = SITE_DIR / "images"
POSTS_JSON = SITE_DIR / "posts.json"


def api(method, **params):
    r = requests.post(f"{API}/{method}", data=params, timeout=70)
    r.raise_for_status()
    return r.json()


def send(chat_id, text):
    try:
        api("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview="true")
    except Exception as e:
        print("send error:", e, flush=True)


def load_posts():
    if POSTS_JSON.exists():
        try:
            return json.loads(POSTS_JSON.read_text() or "[]")
        except json.JSONDecodeError:
            return []
    return []


def save_posts(posts):
    POSTS_JSON.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n")


def git(*args):
    subprocess.run(["git", "-C", str(SITE_DIR), *args],
                   check=True, capture_output=True, text=True)


def publish(message):
    git("add", "-A")
    git("commit", "-m", message)
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
    # guard against same-second collisions
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


def parse_li(text):
    """Return (clean_text, cross_to_linkedin). '#noli' anywhere skips LinkedIn."""
    if "#noli" in text.lower():
        cleaned = " ".join(w for w in text.split() if w.lower() != "#noli").strip()
        return cleaned, False
    return text, True


def cross_post(text, image_path, do_li):
    if not do_li:
        return "\nLinkedIn: skipped (#noli)."
    if not (li and li.enabled()):
        return ""  # not configured, stay silent and unchanged
    try:
        li.post(text, str(image_path) if image_path else None)
        return "\nLinkedIn: posted."
    except Exception as e:
        return f"\nLinkedIn failed: {str(e)[:200]}"


def handle(msg):
    chat_id = msg["chat"]["id"]
    user_id = msg.get("from", {}).get("id")
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()

    if text.startswith("/start") or text.startswith("/help"):
        send(chat_id, "Send a photo with a caption to post it to the site.\n"
                      "Plain text also works as a text-only note.\n"
                      "Add #noli to a caption to skip LinkedIn for that post.\n"
                      "/undo removes the most recent post.\n"
                      "/id shows your Telegram user id.")
        return
    if text.startswith("/id"):
        send(chat_id, f"Your Telegram user id: {user_id}")
        return

    if user_id != AUTHORIZED_USER_ID:
        send(chat_id, "Not authorized to post here.")
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
        send(chat_id, "Removed the most recent post.")
        return

    if "photo" in msg:
        clean, do_li = parse_li(caption)
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        tmp = SITE_DIR / "images" / "_incoming.jpg"
        download_photo(msg["photo"][-1]["file_id"], tmp)
        pid = add_post(tmp, clean)
        publish(f"post {pid}")
        reply = f"Posted. {link_for(pid)}"
        reply += cross_post(clean, SITE_DIR / f"images/{pid}.jpg", do_li)
        send(chat_id, reply)
        return

    if text and not text.startswith("/"):
        clean, do_li = parse_li(text)
        pid = add_post("", clean)
        publish(f"note {pid}")
        reply = f"Posted note. {link_for(pid)}"
        reply += cross_post(clean, None, do_li)
        send(chat_id, reply)
        return

    send(chat_id, "Send a photo with a caption, or plain text. /help for options.")


def main():
    print(f"china-feed bot up. SITE_DIR={SITE_DIR}", flush=True)
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
        except requests.exceptions.RequestException as e:
            print("poll error:", e, flush=True)
            time.sleep(5)
        except Exception as e:
            print("loop error:", e, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
