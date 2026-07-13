# What's New in China

A photo-and-notes feed. Send the Telegram bot a picture with a caption; it lands
on the site within a minute. Hosted on GitHub Pages, no build step, no server.

- `index.html` — the site. Renders the feed from `posts.json` client-side.
- `posts.json` — the feed data. Newest first. The bot appends to this.
- `images/` — uploaded photos, named by post id.
- `bot/` — the Telegram publisher that runs on the Mac mini.

Setup: see `SETUP.md`.

Post a photo with a caption → bot saves it → git commit + push → Pages updates.
`/undo` removes the most recent post. Plain text works as a text-only note.
