# herdr-web

Phone-first web UI for a local [herdr](https://herdr.dev): browse workspaces/tabs/panes with agent
status, watch each pane's live screen (ANSI colours), read searchable Claude transcript history, focus a
pane, and (optionally) send text/keys. Python 3 stdlib only; wraps the `herdr` CLI.

    python3 server.py                  # read-only, 0.0.0.0:8780
    python3 server.py --allow-input    # also enable send-text / send-keys

Open `http://<lan-ip>:8780/` and sign in with the token printed on first start
(stored `0600` in `~/.config/herdr-web/token`).

## Security model
- Random 48-byte token, constant-time compare, login rate limit (5 failures / 5 min per IP, 40 global).
- Session cookie: random id, `HttpOnly; SameSite=Strict`, 30 days, in-memory (restart logs everyone out).
- Host header allow-list (IP literals, localhost, hostname) and Origin must match Host on every POST.
- Client IP must be loopback/private (`--allow-cidr` to extend).
- Pane ids `^w\d+:p\d+$`, session ids UUID-only, transcript path resolved and confined to `~/.claude/projects`.
- No `shell=True`; text is passed after `--`; key names come from a fixed allow-list.
- Input endpoints return 403 unless started with `--allow-input`; the UI toggle is OFF by default.
- Plain HTTP: anyone on your WiFi can sniff the token. Keep it to a network you trust.

## Notes
- `herdr pane focus` is directional only, so Focus walks from the focused pane toward the target.
- `tests/cdp_shots.py` takes authenticated 390x844 screenshots with headless Chrome.
