# Setup

Three parts: put the site on GitHub, create the bot, run the bot on the Mac mini.
Total time about 15 minutes. Commands assume macOS with git and Python 3 installed.

Replace `hansstam86` and paths with your own if they differ.

---

## Part A — Site on GitHub Pages

1. Create a new repo on GitHub named `whats-new-in-china` (public).

2. On the Mac mini, put these files in it and push:

   ```bash
   cd ~
   git clone git@github.com:hansstam86/whats-new-in-china.git
   # copy the contents of this folder into ~/whats-new-in-china, then:
   cd ~/whats-new-in-china
   git add -A && git commit -m "init" && git push
   ```

3. On GitHub: repo → Settings → Pages → Source = "Deploy from a branch",
   Branch = `main`, folder = `/ (root)`. Save.

4. Wait about a minute, then open:
   `https://hansstam86.github.io/whats-new-in-china`
   You should see the masthead and "No dispatches yet."

Confirm `git push` works without a password prompt (SSH key or cached token).
The bot pushes non-interactively, so this must already be silent.

---

## Part B — Telegram bot token

1. In Telegram, message **@BotFather** → `/newbot`. Pick a name and a username.
   Copy the token it gives you (looks like `123456:ABC...`).

2. That is all you need here. You will get your own user id in Part C.

---

## Part C — Run the bot on the Mac mini

1. Set up the environment:

   ```bash
   cd ~/whats-new-in-china/bot
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Create the config:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`:
   - `BOT_TOKEN` — from BotFather.
   - `AUTHORIZED_USER_ID` — leave as-is for now.
   - `SITE_DIR` — absolute path to the repo, e.g. `/Users/hans/whats-new-in-china`.
   - `SITE_BASE_URL` — your Pages URL.

3. First run, to grab your user id:

   ```bash
   python bot.py
   ```

   In Telegram, open your bot and send `/id`. It replies with your numeric id.
   Stop the bot (Ctrl-C), put that number in `.env` as `AUTHORIZED_USER_ID`, save.

4. Run again and test:

   ```bash
   python bot.py
   ```

   Send the bot a photo with a caption. Within a minute it should appear on the
   site, and the bot replies with a link. `/undo` removes the last post.

---

## Part D — Keep it running (launchd)

So it survives reboots and restarts if it crashes.

1. Fill in the plist:

   ```bash
   cp com.hans.chinabot.plist.example ~/Library/LaunchAgents/com.hans.chinabot.plist
   ```

   Edit that file and set the two absolute paths (python in `.venv`, and `bot.py`)
   and the `WorkingDirectory` to your `bot/` folder. Secrets stay in `.env`; the
   plist does not need them.

2. Load it:

   ```bash
   launchctl load ~/Library/LaunchAgents/com.hans.chinabot.plist
   ```

   Logs go to `/tmp/chinabot.log` and `/tmp/chinabot.err`.

   To stop or reload after edits:

   ```bash
   launchctl unload ~/Library/LaunchAgents/com.hans.chinabot.plist
   launchctl load   ~/Library/LaunchAgents/com.hans.chinabot.plist
   ```

Keep the Mac mini set to not sleep (System Settings → Energy → Prevent sleeping),
or the bot pauses when it sleeps.

---

## Custom domain (optional)

Point a subdomain at it, e.g. `new.justgotochina.com`:

1. GoDaddy DNS → add a CNAME: host `new`, value `hansstam86.github.io`.
2. GitHub repo → Settings → Pages → Custom domain → `new.justgotochina.com` → Save.
3. Set `SITE_BASE_URL` in `.env` to `https://new.justgotochina.com` and reload the bot.

---

## Part E — LinkedIn cross-posting (optional)

Posts every dispatch to your personal LinkedIn feed. Uses the self-serve
"Share on LinkedIn" product, so no weeks-long review. Skip this whole part and
the site still works exactly the same.

1. Go to the LinkedIn Developer Portal → Create app. Link it to a company page
   (a placeholder page is fine if you do not have one). Verify the app.

2. In the app: Products tab → add **Share on LinkedIn** and **Sign In with
   LinkedIn using OpenID Connect**. Auth tab → add the redirect URL
   `http://localhost:8000/callback`. Copy the Client ID and Client Secret.

3. Put them in `bot/.env`:

   ```
   LINKEDIN_CLIENT_ID=...
   LINKEDIN_CLIENT_SECRET=...
   LINKEDIN_REDIRECT_URI=http://localhost:8000/callback
   ```

4. Authorize once:

   ```bash
   cd ~/whats-new-in-china/bot
   source .venv/bin/activate
   python linkedin_auth.py
   ```

   Open the printed URL, approve, and paste the full redirected address back.
   It writes `linkedin_tokens.json` (gitignored). The bot refreshes tokens on its
   own; you re-run this about once a year when the refresh token expires.

5. Reload the bot (`launchctl unload`/`load`). From now on each post also goes to
   LinkedIn, and the bot's reply says "LinkedIn: posted" or reports the error.

   - Add `#noli` anywhere in a caption to skip LinkedIn for that one post.
   - If you get a version error on first post, set `LINKEDIN_VERSION` in `.env`
     to the current month (format `YYYYMM`) and reload.
   - Company Page posting instead of your profile needs Marketing Developer
     Platform approval (1–4 weeks) and a different scope. Ask and I'll adjust.

---

## Notes

- Only your Telegram user id can post. Anyone else gets "Not authorized."
- One photo per message = one post. If you send a Telegram album (several photos
  at once), each photo becomes its own post and only the first carries the caption.
  Send them one at a time if you want a caption on each. Album batching can be
  added later.
- `posts.json` is the whole feed. To hand-edit or delete an old post, edit that
  file and `git push`.
