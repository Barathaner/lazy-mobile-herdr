# lazy-mobile-herdr

Control and watch your [herdr](https://herdr.dev) terminal workspace, and the Claude Code sessions in it,
from your phone. Two independent parts:

| Part | What it does |
|---|---|
| `web/` | Phone-first web app: list workspaces/panes with agent status, live pane screens, searchable Claude transcript history, optional input. Python 3 stdlib only. |
| `telegram/` | Claude Code Stop hook that posts every non-leader session's last reply to your Telegram chat. |

> **Security warning.** `web/` can expose your machine's herdr panes (and, with `--allow-input`, send
> keystrokes to them) to your network. Plain HTTP means the login token can be sniffed on the WiFi.
> Use it only on a network you trust, or put it behind Tailscale/an SSH tunnel. Read the security model
> in `web/README.md` before running it.

## Requirements
- Linux or macOS with `herdr` installed and a running herdr session
- Python 3.9+
- Optional: Claude Code with the Telegram channel plugin (for `telegram/`)

## Setup: web app
1. Clone and start it (read-only by default):
   ```bash
   git clone https://github.com/Barathaner/lazy-mobile-herdr.git
   cd lazy-mobile-herdr/web
   python3 server.py            # listens on 0.0.0.0:8780
   ```
2. On first start it creates a random token in `~/.config/herdr-web/token` (mode 0600).
   Read it with `cat ~/.config/herdr-web/token`.
3. Find your PC's LAN IP (`hostname -I`) and open `http://<lan-ip>:8780/` on your phone, same WiFi.
4. Paste the token on the login page.
5. To send text and keys from the phone, restart with `python3 server.py --allow-input`, then switch the
   input toggle on in the UI. It is off by default.
6. Bind to loopback only (use with Tailscale or an SSH tunnel): see `python3 server.py --help` for the bind option.
7. Rotate the token: delete `~/.config/herdr-web/token` and restart.

Scrollback note: herdr panes keep no terminal scrollback for Claude's TUI, so history is rendered from
the Claude transcripts in `~/.claude/projects/*/<session-id>.jsonl`.

## Setup: Telegram forwarding (optional)
Only one process can poll a Telegram bot token, so one "leader" Claude session (started with
`--channels plugin:telegram@claude-plugins-official`) receives your messages. All other sessions only send.

1. Create a bot with @BotFather and configure the Claude Code Telegram plugin so that
   `~/.claude/channels/telegram/.env` contains `TELEGRAM_BOT_TOKEN=...` and `access.json` lists your
   chat id under `allowFrom`.
2. Copy the hook:
   ```bash
   cp telegram/tg-forward.py ~/.claude/tg-forward.py
   ```
3. Add a Stop hook to `~/.claude/settings.json`:
   ```json
   {
     "hooks": {
       "Stop": [
         { "hooks": [ { "type": "command", "command": "python3 ~/.claude/tg-forward.py", "timeout": 20 } ] }
       ]
     }
   }
   ```
4. Start new Claude sessions. Their replies arrive in Telegram prefixed with `[pane cwd]`.
   The leader session is skipped automatically.

Find the leader: `pgrep -af "claude.*--channels"`, then
`tr '\0' '\n' < /proc/<pid>/environ | grep HERDR_PANE_ID`.

## Layout
```
web/server.py        backend (wraps the herdr CLI)
web/static/          single-page frontend
web/tests/           headless Chrome screenshot helper
telegram/tg-forward.py   Stop hook
```

## License
MIT
