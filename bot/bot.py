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
import email.utils

import requests

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

try:
    import linkedin as li
except Exception:
    li = None

try:
    import caption as cap
except Exception:
    cap = None

import uuid


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
FEED_FILE = SITE_DIR / "feed.xml"
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


def notify_kb(text, buttons):
    send_kb(AUTHORIZED_USER_ID, text, buttons)


def load_posts():
    if POSTS_JSON.exists():
        try:
            return json.loads(POSTS_JSON.read_text() or "[]")
        except json.JSONDecodeError:
            return []
    return []


def save_posts(posts):
    POSTS_JSON.write_text(json.dumps(posts, ensure_ascii=False, indent=2) + "\n")
    try:
        regenerate_feed(posts)
    except Exception as e:
        print("feed error:", e, flush=True)


def _xml_escape(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _abs_url(path):
    return f"{SITE_BASE_URL}/{path}" if SITE_BASE_URL else path


def _rfc822(iso):
    try:
        dt = datetime.datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except Exception:
        dt = datetime.datetime.now(datetime.timezone.utc)
    return email.utils.format_datetime(dt)


def regenerate_feed(posts=None):
    """Write feed.xml (RSS 2.0) from posts. Needs an absolute SITE_BASE_URL."""
    if not SITE_BASE_URL:
        return
    posts = posts if posts is not None else load_posts()
    items = []
    for p in posts[:50]:
        pid = p.get("id", "")
        link = f"{SITE_BASE_URL}/#{pid}"
        text = (p.get("text") or "").strip()
        title = _short(text, 90) if text else f"Dispatch {p.get('date', '')[:10]}"
        imgs = p.get("images") or ([p["image"]] if p.get("image") else [])
        desc = _xml_escape(text).replace("\n", "<br>")
        for im in imgs:
            desc += f'<br><img src="{_abs_url(im)}" alt="">'
        enclosure = ""
        if imgs:
            fp = SITE_DIR / imgs[0]
            if fp.exists():
                enclosure = f'\n      <enclosure url="{_abs_url(imgs[0])}" length="{fp.stat().st_size}" type="image/jpeg"/>'
        items.append(
            "    <item>\n"
            f"      <title>{_xml_escape(title)}</title>\n"
            f"      <link>{link}</link>\n"
            f'      <guid isPermaLink="false">{link}</guid>\n'
            f"      <pubDate>{_rfc822(p.get('date', ''))}</pubDate>{enclosure}\n"
            f"      <description><![CDATA[{desc}]]></description>\n"
            "    </item>"
        )
    built = email.utils.format_datetime(datetime.datetime.now(datetime.timezone.utc))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        "    <title>What's New in China</title>\n"
        f"    <link>{SITE_BASE_URL}</link>\n"
        f'    <atom:link href="{SITE_BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        "    <description>Field dispatches from the ground in China.</description>\n"
        "    <language>en</language>\n"
        f"    <lastBuildDate>{built}</lastBuildDate>\n"
        + "\n".join(items) + "\n"
        "  </channel>\n"
        "</rss>\n"
    )
    FEED_FILE.write_text(xml, encoding="utf-8")


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
        return "Send some caption text to go with the link."
    body = f"{caption}\n\n{SITE_BASE_URL}"
    try:
        li.post_text(body)
    except li.TokenExpired as e:
        return f"Not posted: {e}. Re-run linkedin_auth.py, then resend."
    except Exception as e:
        return f"LinkedIn post failed: {str(e)[:200]}"
    state["last_announced_id"] = max(p["id"] for p in batch)
    for k in ("pending", "pending_date", "pending_draft", "pending_token"):
        state.pop(k, None)
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
    for k in ("pending", "pending_date", "pending_draft", "pending_token"):
        state.pop(k, None)
    save_state(state)
    return f"Skipped. {len(batch)} dispatch(es) will not be posted to LinkedIn."


def prompt_text(batch):
    lines = [f"- {_short(p.get('text'))}" for p in batch]
    return (f"{len(batch)} new dispatch(es) today. What should the LinkedIn post say?\n\n"
            + "\n".join(lines)
            + f"\n\nReply with your caption. The post links to {SITE_BASE_URL} so people "
              "see all of today's posts.\nOr /skip. Explicit: /li <caption>.")


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

    draft = None
    if cap_enabled():
        texts = [(p.get("text") or "").strip() for p in batch]
        texts = [t for t in texts if t]
        img_paths = []
        for p in batch:
            for im in (p.get("images") or ([p["image"]] if p.get("image") else [])):
                fp = SITE_DIR / im
                if fp.exists():
                    img_paths.append(fp)
        img_paths = img_paths[:3]
        try:
            draft = cap.draft_linkedin(texts, img_paths)
        except Exception as e:
            print("linkedin draft error:", e, flush=True)

    if draft:
        token = uuid.uuid4().hex[:8]
        state["pending_draft"] = draft
        state["pending_token"] = token
        save_state(state)
        msg = (f"{len(batch)} new dispatch(es) today. Draft LinkedIn post:\n\n{draft}\n\n"
               f"Tap Post, or reply with your own version. Links to {SITE_BASE_URL}.")
        notify_kb(msg, [("Post", f"lipost:{token}"), ("Skip", f"liskip:{token}")])
    else:
        state.pop("pending_draft", None)
        state.pop("pending_token", None)
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


def cap_enabled():
    try:
        return bool(cap and cap.enabled())
    except Exception as e:
        print("caption enabled() error:", e, flush=True)
        return False


def li_note(include):
    if not li_enabled():
        return ""
    return "\nExcluded from LinkedIn." if not include else f"\nWill be in the {DAILY_HOUR:02d}:00 {TZ_LABEL} LinkedIn prompt."


def finalize_dispatch(chat_id, tmp_paths, text, include):
    pid, n = add_post(tmp_paths, text)
    publish(f"post {pid}" + (f" ({n} photos)" if n > 1 else ""))
    if not include:
        st = load_state()
        st.setdefault("excluded", []).append(pid)
        save_state(st)
    label = f"Posted {n} photos." if n > 1 else "Posted."
    send(chat_id, f"{label} {link_for(pid)}" + li_note(include))


# --- AI caption drafts: hold the post, show a draft with tap buttons ---
PENDING = {}      # token -> {"paths", "draft", "include", "chat_id"}
EDIT_TOKEN = None
EDIT_POST_ID = None   # id of an existing post whose caption is being edited


def delete_post(pid):
    posts = load_posts()
    idx = next((i for i, p in enumerate(posts) if p.get("id") == pid), None)
    if idx is None:
        return False
    p = posts.pop(idx)
    for im in (p.get("images") or ([p["image"]] if p.get("image") else [])):
        fp = SITE_DIR / im
        if fp.exists():
            try:
                fp.unlink()
            except Exception:
                pass
    st = load_state()
    if pid in st.get("excluded", []):
        st["excluded"] = [x for x in st["excluded"] if x != pid]
        save_state(st)
    save_posts(posts)
    publish(f"delete {pid}")
    return True


def edit_post_text(pid, new_text):
    posts = load_posts()
    for p in posts:
        if p.get("id") == pid:
            p["text"] = (new_text or "").strip()
            save_posts(posts)
            publish(f"edit {pid}")
            return True
    return False


def send_kb(chat_id, text, buttons):
    kb = {"inline_keyboard": [[{"text": l, "callback_data": d} for l, d in buttons]]}
    try:
        return api("sendMessage", chat_id=chat_id, text=text,
                   reply_markup=json.dumps(kb), disable_web_page_preview="true")
    except Exception as e:
        print("send_kb error:", e, flush=True)
        return {}


def answer_cb(cb_id, text=""):
    try:
        api("answerCallbackQuery", callback_query_id=cb_id, text=text)
    except Exception as e:
        print("answer_cb error:", e, flush=True)


def stage_draft(chat_id, tmp_paths, include):
    token = uuid.uuid4().hex[:8]
    paths = []
    for i, tp in enumerate(tmp_paths):
        pp = SITE_DIR / "images" / f"_pending-{token}-{i}.jpg"
        os.replace(tp, pp)
        paths.append(pp)
    try:
        d = cap.draft(paths)
    except Exception as e:
        print("caption draft error:", e, flush=True)
        send(chat_id, f"Couldn't draft a caption ({str(e)[:120]}). Posting without one.")
        finalize_dispatch(chat_id, paths, "", include)
        return
    PENDING[token] = {"paths": paths, "draft": d, "include": include, "chat_id": chat_id}
    send_kb(chat_id, f"Draft caption:\n\n{d}",
            [("Post", f"post:{token}"), ("Edit", f"edit:{token}"), ("Discard", f"discard:{token}")])


def _cleanup_paths(paths):
    for p in paths:
        try:
            pathlib.Path(p).unlink()
        except Exception:
            pass


def handle_callback(cb):
    global EDIT_TOKEN
    cb_id = cb["id"]
    data = cb.get("data", "")
    frm = cb.get("from", {}).get("id")
    m = cb.get("message", {})
    chat_id = m.get("chat", {}).get("id")
    mid = m.get("message_id")
    if frm != AUTHORIZED_USER_ID:
        answer_cb(cb_id, "Not authorized")
        return
    if ":" not in data:
        answer_cb(cb_id)
        return
    action, token = data.split(":", 1)

    # LinkedIn daily post: Post the AI draft, or Skip. Draft lives in state.
    if action in ("lipost", "liskip"):
        st = load_state()
        if st.get("pending_token") != token:
            answer_cb(cb_id, "Expired")
            try:
                api("editMessageReplyMarkup", chat_id=chat_id, message_id=mid)
            except Exception:
                pass
            return
        try:
            api("editMessageReplyMarkup", chat_id=chat_id, message_id=mid)
        except Exception:
            pass
        if action == "lipost":
            answer_cb(cb_id, "Posting")
            send(chat_id, compose_and_post(st.get("pending_draft", "")))
        else:
            answer_cb(cb_id, "Skipped")
            send(chat_id, skip_linkedin())
        return

    # Delete / edit a specific existing post (token is the post id).
    if action == "del":
        answer_cb(cb_id, "Deleting")
        try:
            ok = delete_post(token)
            try:
                api("editMessageText", chat_id=chat_id, message_id=mid,
                    text="Deleted." if ok else "Already gone.")
            except Exception:
                pass
        except subprocess.CalledProcessError as e:
            send(chat_id, f"Deleted locally but publish failed:\n{(e.stderr or str(e))[:200]}")
        except Exception as e:
            send(chat_id, f"Delete error: {e}")
        return
    if action == "ed":
        global EDIT_POST_ID
        if not any(p.get("id") == token for p in load_posts()):
            answer_cb(cb_id, "Post is gone")
            return
        EDIT_POST_ID = token
        answer_cb(cb_id, "Send new caption")
        send(chat_id, "Send the new caption for this post.")
        return

    pend = PENDING.get(token)
    if not pend:
        answer_cb(cb_id, "Draft expired")
        try:
            api("editMessageText", chat_id=chat_id, message_id=mid, text="(draft expired, resend the photo)")
        except Exception:
            pass
        return
    if action == "post":
        answer_cb(cb_id, "Posting")
        PENDING.pop(token, None)
        try:
            api("editMessageReplyMarkup", chat_id=chat_id, message_id=mid)
        except Exception:
            pass
        finalize_dispatch(pend["chat_id"], pend["paths"], pend["draft"], pend["include"])
    elif action == "edit":
        EDIT_TOKEN = token
        answer_cb(cb_id, "Send your caption")
        send(chat_id, "Send the caption you want for this photo.")
    elif action == "discard":
        answer_cb(cb_id, "Discarded")
        PENDING.pop(token, None)
        _cleanup_paths(pend["paths"])
        try:
            api("editMessageText", chat_id=chat_id, message_id=mid, text="Discarded.")
        except Exception:
            pass
    else:
        answer_cb(cb_id)


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
    capt = (msg.get("caption") or "").strip()
    if capt:
        buf["caption"] = capt
    buf["last"] = time.time()


def flush_albums(force=False):
    for mgid in [k for k, v in ALBUMS.items() if force or (time.time() - v["last"] >= ALBUM_FLUSH_SEC)]:
        buf = ALBUMS.pop(mgid)
        chat_id = buf["chat_id"]
        try:
            (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
            tok = uuid.uuid4().hex[:8]
            tmps = []
            for i, fid in enumerate(buf["file_ids"]):
                tmp = SITE_DIR / "images" / f"_albtmp-{tok}-{i}.jpg"
                download_photo(fid, tmp)
                tmps.append(tmp)
            clean, include = parse_li(buf["caption"])
            if clean or not cap_enabled():
                finalize_dispatch(chat_id, tmps, clean, include)
            else:
                stage_draft(chat_id, tmps, include)
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
        send(chat_id,
             "What's New in China bot\n"
             "\n"
             "POSTING\n"
             "- Photo + caption: posts it to the site\n"
             "- Photo, no caption: Claude drafts one, you approve with a tap\n"
             "- Several photos as an album: one gallery post\n"
             "- Plain text: a text-only note\n"
             "- #noli in a caption: keep that one off LinkedIn\n"
             "\n"
             "LINKEDIN\n"
             f"- At {DAILY_HOUR:02d}:00 {TZ_LABEL} the bot drafts a LinkedIn post you approve (if there are new dispatches)\n"
             "- /li <caption> - post the queued dispatches to LinkedIn now\n"
             "- /skip - don't post the queued dispatches to LinkedIn\n"
             "- /preview - show the queued LinkedIn links\n"
             "- /prompt - send the LinkedIn reminder now\n"
             "\n"
             "SITE\n"
             "- /list - show recent posts to edit or delete\n"
             "- /undo - remove the most recent post\n"
             "- /republish - force a fresh site rebuild\n"
             "\n"
             "OTHER\n"
             "- /help - this menu\n"
             "- /id - your Telegram user id")
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

    if text == "/li" or text.startswith("/li "):
        body = text[3:].strip()
        if body:
            send(chat_id, compose_and_post(body))
        else:
            send(chat_id, send_prompt(manual=True))
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
            lines = "\n".join(f"- {_short(p.get('text'))}" for p in batch)
            send(chat_id, f"{len(batch)} dispatch(es) queued:\n\n{lines}\n\n"
                          f"The post will link to {SITE_BASE_URL}\n"
                          "Reply with a caption or /li <caption> to post.")
        return

    if text.startswith("/prompt"):
        send(chat_id, send_prompt(manual=True))
        return

    if text.startswith("/list") or text.startswith("/recent"):
        posts = load_posts()
        if not posts:
            send(chat_id, "No posts yet.")
            return
        send(chat_id, f"Last {min(len(posts), 8)} dispatch(es). Tap Edit or Delete:")
        for p in posts[:8]:
            t = (p.get("text") or "").strip()
            preview = _short(t, 80) if t else "(photo, no caption)"
            nimg = len(p.get("images") or ([p["image"]] if p.get("image") else []))
            tag = f" [{nimg} photos]" if nimg > 1 else (" [photo]" if nimg == 1 else "")
            when = p.get("date", "")[:10]
            send_kb(chat_id, f"{preview}{tag}\n{when} · {link_for(p['id'])}",
                    [("Edit", f"ed:{p['id']}"), ("Delete", f"del:{p['id']}")])
        return

    if text.startswith("/undo"):
        posts = load_posts()
        if not posts:
            send(chat_id, "Nothing to undo.")
            return
        pid = posts[0].get("id")
        try:
            delete_post(pid)
            send(chat_id, "Removed the most recent post. (If already posted on LinkedIn, that stays.)")
        except subprocess.CalledProcessError as e:
            send(chat_id, f"Removed locally but publish failed:\n{(e.stderr or str(e))[:200]}")
        return

    if "photo" in msg:
        clean, include = parse_li(caption)
        (SITE_DIR / "images").mkdir(parents=True, exist_ok=True)
        tmp = SITE_DIR / "images" / f"_tmp-{uuid.uuid4().hex[:8]}.jpg"
        download_photo(msg["photo"][-1]["file_id"], tmp)
        if clean or not cap_enabled():
            finalize_dispatch(chat_id, [tmp], clean, include)
        else:
            stage_draft(chat_id, [tmp], include)
        return

    if text and not text.startswith("/"):
        global EDIT_TOKEN, EDIT_POST_ID
        # If a photo draft is awaiting an edited caption, this text is that caption.
        if EDIT_TOKEN and EDIT_TOKEN in PENDING:
            pend = PENDING.pop(EDIT_TOKEN)
            EDIT_TOKEN = None
            finalize_dispatch(pend["chat_id"], pend["paths"], text.strip(), pend["include"])
            return
        # If editing an existing post's caption (tapped Edit on /list), apply it.
        if EDIT_POST_ID:
            pid = EDIT_POST_ID
            EDIT_POST_ID = None
            try:
                ok = edit_post_text(pid, text)
                send(chat_id, "Updated." if ok else "That post is gone.")
            except subprocess.CalledProcessError as e:
                send(chat_id, f"Updated locally but publish failed:\n{(e.stderr or str(e))[:200]}")
            return
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
        send(chat_id, f"Posted note. {link_for(pid)}" + li_note(include))
        return

    send(chat_id, "Send a photo with a caption, or plain text. /help for options.")


def set_commands():
    cmds = [
        {"command": "help", "description": "Show all commands"},
        {"command": "li", "description": "Post queued dispatches to LinkedIn now"},
        {"command": "skip", "description": "Skip LinkedIn for queued dispatches"},
        {"command": "preview", "description": "Show queued LinkedIn links"},
        {"command": "prompt", "description": "Send the LinkedIn reminder now"},
        {"command": "undo", "description": "Remove the most recent post"},
        {"command": "list", "description": "Edit or delete a specific post"},
        {"command": "republish", "description": "Force a fresh site rebuild"},
        {"command": "id", "description": "Show your Telegram user id"},
    ]
    try:
        api("setMyCommands", commands=json.dumps(cmds))
    except Exception as e:
        print("setMyCommands error:", e, flush=True)


def main():
    print(f"china-feed bot up. SITE_DIR={SITE_DIR}", flush=True)
    # clear any orphaned temp images from a previous run
    for pat in ("_pending-*", "_albtmp-*", "_tmp-*", "_incoming*"):
        for f in (SITE_DIR / "images").glob(pat):
            try:
                f.unlink()
            except Exception:
                pass
    set_commands()
    if cap_enabled():
        print("AI captions enabled (draft from photo, tap to approve)", flush=True)
    try:
        regenerate_feed()
    except Exception as e:
        print("feed error:", e, flush=True)
    if li and li.enabled():
        init_li_state()
        print(f"LinkedIn reminder enabled: daily at {DAILY_HOUR:02d}:00 {LINKEDIN_TZ}", flush=True)
    offset = None
    while True:
        try:
            resp = api("getUpdates", offset=offset, timeout=(1 if ALBUMS else 45))
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    try:
                        handle_callback(upd["callback_query"])
                    except Exception as e:
                        print("callback error:", e, flush=True)
                    continue
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
