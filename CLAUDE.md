# new.justgotochina.com

Static site on GitHub Pages. Managed by the web-master (see ~/Projects/web-master).

- Live: https://new.justgotochina.com
- Repo: hansstam86/whats-new-in-china (branch `main`, served from repo root, legacy Pages build)
- DNS: GoDaddy (apex A records to GitHub Pages IPs, www CNAME to the owner's github.io)
- Photo feed rendered from posts.json; a Telegram bot appends posts and pushes to `main` automatically (see README.md, SETUP.md).

## Rules
- No build step: edit the HTML directly and push to `main` to deploy.
- Do not change `CNAME`, DNS or Pages settings without Hans's OK.
- Draft only; don't invent content, prices or claims.
- The bot pushes to main on its own: always `git pull --rebase` before committing, and never edit posts.json or feed.xml by hand.
