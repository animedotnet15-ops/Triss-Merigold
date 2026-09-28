# 🦋 Triss File Store Bot

Triss is a Telegram File Store Bot. The owner sends content to the bot,
Triss stores it internally in a private Store Channel, saves metadata in
MongoDB, and hands back a secure, unique deep link that anyone can open
to receive that content — optionally gated behind Force Subscription
requirements.

## Features

- **`/genlink`** — store a single message/file/text/link and get one new link.
- **`/batch`** — capture everything from a first message to a last message
  (via `/done`), stored and delivered in exact order as one link.
- **`/custombatch`** — hand-pick exactly which messages to include.
- **`/cancelbatch`** — abort an in-progress batch, cleaning up any content
  already copied into the Store Channel so nothing is left orphaned.
- **`/broadcast`** — send any content to every user who has started the bot,
  with FloodWait handling and automatic pruning of users who blocked the bot.
- **`/settings`** — a fully button-driven admin panel:
  - 🏠 Welcome (photo, text, spoiler image, sticker, animation speed, preview)
  - 💬 Comments (how-to for genlink/batch/custombatch)
  - 📣 Force Sub (channels, groups, folders)
  - 🧹 Auto Delete (preset or custom durations)
  - 🌐 Shortener (Old Method — domain, API key, min/max verification time,
    customizable popups, anti-bypass, tutorial video, on/off)
  - ♻️ System Access (New Method — its own fully separate domain/API
    key/min-max time/popups/anti-bypass, plus a Verify Time preset
    30m/1h/3h/6h/12h/24h; see below)
  - 📥 Direct Download (linkdl)
  - 🔒 Restrict Content (on/off — see below)
  - ⚙️ Bot Maintenance (Active / Maintenance)
  - 🗄️ Backup & Restore (config + metadata, never secrets)
  - 🏪 Store Channel configuration
- **Shortener + per-access verification (Old Method)** — when enabled, every
  fresh access to a protected link creates an independent, server-side-timed
  verification session (never reused across accesses or users), routes
  the user through the configured shortener, and only delivers the file
  if the round trip took between the configured minimum and maximum
  time. Too-fast returns are flagged as a bypass attempt; too-slow
  returns are reported as expired — both offer a "Try Again" that always
  starts a brand-new session.
- **♻️ System Access (New Method)** — an alternative to Old Method,
  mutually exclusive with it (enabling one automatically disables the
  other; both can be off together). Instead of gating one link per
  verification, ONE completed verification grants the user unlimited use
  of *every* link in the bot for a configured duration (30m/1h/3h/6h/
  12h/24h), after which they must verify again. Has its own fully
  independent Domain/API key/Min-Max Time/Popups/Anti-Bypass, so it never
  shares configuration with Old Method. Force Sub is unaffected and still
  applies normally during an active System Access window.
- **🔒 Restrict Content** — a single bot-wide toggle. When ON, every
  message this bot sends — welcome, Force Sub prompts, Shortener/System
  Access popups, broadcasts, delivered files, even the owner's own
  settings replies — is sent with Telegram's own `protect_content=True`,
  which disables forwarding and saving of that message for every
  recipient. Implemented as a single patch on Pyrogram's Client send
  methods (see `triss/utils/restrict.py`) so it applies instantly,
  everywhere, without touching this codebase's ~170 individual send call
  sites or requiring a restart.
- Secure, unguessable share tokens (`secrets.token_urlsafe`) — no database
  IDs or Store Channel identifiers ever appear in a link.
- The Store Channel is never exposed: all delivery uses `copy_message`,
  never `forward_message`, so there is no "Forwarded from" attribution.

## Requirements

- Python 3.11+
- A MongoDB database (Atlas free tier works fine)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- `API_ID` / `API_HASH` from https://my.telegram.org
- A private Telegram channel to use as the Store Channel, with the bot
  added as an administrator (can be configured after first boot via
  `/settings → 🏪 Store Channel`)

## Setup

```bash
git clone <this repo>
cd triss
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with your values
python main.py
```

### Environment variables

| Variable             | Required | Description                                            |
|-----------------------|:--------:|----------------------------------------------------------|
| `API_ID`              | ✅       | Telegram API ID from my.telegram.org                    |
| `API_HASH`            | ✅       | Telegram API hash from my.telegram.org                  |
| `BOT_TOKEN`           | ✅       | Bot token from @BotFather                                |
| `MONGO_URI`           | ✅       | MongoDB connection string                                 |
| `DATABASE_NAME`       | ➖       | MongoDB database name (default `triss`)                  |
| `OWNER_ID`            | ✅       | Your numeric Telegram user ID (only owner with admin access) |
| `STORAGE_CHANNEL_ID`  | ➖       | Store Channel ID; can be set later via `/settings`        |
| `LOG_CHANNEL_ID`      | ➖       | Channel for owner-facing `/start`/security logs           |
| `PORT`                | ➖       | Health server port (default `8080`; Railway/Render set this) |
| `PUBLIC_BASE_URL`     | ➖*      | Public HTTPS base URL of this bot's own web server. **Required if you enable Shortener.** |
| `VERIFICATION_SECRET` | ➖       | Secret used to sign shortener verification proofs. Recommended to set explicitly; derived from `BOT_TOKEN` if left blank. |

Secrets are never logged, never included in backups, and never echoed
back to any user in error messages.

## Shortener verification flow

When **🌐 Shortener** is enabled in `/settings`, every fresh access to a
protected link works like this:

1. Telegram deep-link token is validated (exists, not revoked).
2. Link expiration is checked.
3. Force Sub is checked (if enabled) — the user must join first.
4. A brand-new verification session is created (`secrets.token_urlsafe`
   session id, per-user, per-access — see `triss/database/models.py`
   `create_verification_session`), state `CREATED`.
5. The bot builds a link to **its own web server**,
   `{PUBLIC_BASE_URL}/v/<session_id>` (**not** the Telegram deep link),
   and shortens *that* via the configured shortener API.
6. The user taps **🌀 Verify & Get File**, completes the shortener's page,
   and is redirected to `{PUBLIC_BASE_URL}/v/<session_id>`
   (`triss/web/server.py`). Reaching this route is what atomically moves
   the session `CREATED -> REDIRECTED`; it also re-checks the min/max
   timing bounds against the server clock. On success it mints a fresh,
   single-use random proof (only its salted hash is ever stored) and
   302-redirects the browser to
   `https://t.me/<bot>?start=verify_<session_id>.<proof>`, which opens
   Telegram and fires `/start`.
7. The bot evaluates that specific `(session_id, proof)` pair **entirely
   server-side**: the proof's hash must match the one stored for that
   session (`hmac.compare_digest`), the session must still be in
   `REDIRECTED` state, and elapsed time is re-checked against
   minimum/maximum a second time. A missing or incorrect proof is
   rejected outright — elapsed time is *never*, by itself, treated as
   proof of completion.
   - Too fast → 🚨 bypass detected, session flagged, "Try Again" creates
     a fresh session.
   - Too slow → ⏰ expired, "Try Again" creates a fresh session.
   - Missing/wrong proof → session flagged invalid; nothing is delivered.
   - Correct proof within range → session moves `VERIFIED`, then is
     **atomically consumed** (`VERIFIED -> CONSUMED`) immediately before
     delivery, so a duplicate Telegram update or a raced concurrent
     callback can never trigger a second delivery for the same session.

Reopening the same content link later — or tapping "Try Again" — always
creates a brand-new session with a brand-new proof; a previously
verified/consumed session can never be replayed.

**Genuine limitation, stated plainly, and how it's now handled:**
Telegram's Bot API gives third-party services no way to call back into a
bot directly, and most drop-in shortener APIs (including the generic one
implemented here) have no server-to-server "did the user actually
complete this" check either. The `/v/<session_id>` redirect-plus-proof
mechanism above is retained as a required layer — it stops guessed/replayed
deep links — but it is explicitly **not** treated as completion evidence
by itself. `evaluate_verification()` also requires the configured
provider to set `supports_completion_verification = True` and pass a real
`verify_completion()` check (a verification API call, a validated signed
callback, a provider-issued completion token, etc.). The bundled
`GenericQueryProvider` has no such capability and does not claim one, so:

- **Shortener cannot be turned on** with it at all — the `/settings`
  toggle refuses, with a clear on-screen error, until a provider with
  genuine completion verification is configured.
- If a provider is ever downgraded/misconfigured after a session was
  already created, `evaluate_verification()` independently re-checks the
  same capability and fails the session closed into `PROVIDER_UNAVAILABLE`
  — nothing is ever delivered on elapsed time, the `/v/` proof, or a
  client-supplied flag alone.

If your shortener provider genuinely offers a trustworthy completion-check
API, implement a new `ShortenerProvider` subclass in
`triss/services/shortener.py` that sets `supports_completion_verification
= True` and overrides `verify_completion()` with a real check against
that API/callback/token — only then can Shortener be enabled with it.

## Deployment

### Railway
1. Push this repo to GitHub and create a new Railway project from it.
2. Set the environment variables listed above in the Railway dashboard.
3. Railway auto-detects `Procfile`/`python main.py`; the `/health`
   endpoint on `PORT` satisfies Railway's health checks.

### Render
1. Create a new **Web Service** from this repo.
2. Build command: `pip install -r requirements.txt`
3. Start command: `python main.py`
4. Set the environment variables in the Render dashboard. Render pings
   `/health` automatically once `PORT` is bound.

### VPS
```bash
pip install -r requirements.txt
cp .env.example .env  # fill in values
python main.py
# or run under systemd / pm2 / tmux for persistence
```

## Architecture

```
triss/
    config.py          # env var loading & validation
    bot.py              # Kurigram Client instance + lifecycle
    database/
        mongodb.py       # connection, collections, indexes
        models.py        # typed repository functions (one atomic op each)
    handlers/
        start.py          # /start (welcome + deep-link token resolution)
        genlink.py         # /genlink
        batch.py            # /batch, /custombatch, /done, /cancelbatch
        custom_batch.py      # (logic consolidated into batch.py)
        broadcast.py          # /broadcast
        settings.py            # /settings entrypoint
        callbacks.py             # every inline button + settings text/media capture
    services/
        storage.py         # copy owner content into the Store Channel
        forcesub.py          # force-subscription membership checks
        delivery.py            # deliver stored content, auto-delete scheduling
        shortener.py             # shortener API calls + verification session lifecycle
        backup.py                  # config/metadata backup & restore
        cleanup.py                   # in-memory owner session state + sweep loop
        logging_service.py             # LOG_CHANNEL_ID notices
    utils/
        tokens.py, formatting.py, validators.py, keyboards.py, time_parser.py, auth.py
        restrict.py       # Restrict Content: patches Client send_*/copy_message globally
    web/
        server.py           # aiohttp GET /health
main.py                # entrypoint: runs web server + bot together
```

Owner interaction state (genlink waiting, batch/custombatch active,
broadcast waiting, force-sub/store-channel/welcome/auto-delete setup
flows) lives in an in-memory `SessionManager` rather than MongoDB. This
is deliberate: there is exactly one `OWNER_ID` by design, this bot is
meant to run as a single process, and in-memory state means a stale
session can never linger across restarts or corrupt persisted data. A
background sweep clears any session that goes untouched for 15 minutes.

## Genuine Telegram/API limitations

- **Telegram Folders have no membership-verification API.** A folder
  Force Sub entry is stored and shown to users as a join resource link,
  but the bot cannot check whether a user actually joined it — this is
  a hard Telegram platform limitation, not a shortcut taken here.
- **Media groups (albums)** sent during `/genlink`, `/batch`, or
  `/custombatch` are stored and copied message-by-message (each item in
  the album is captured individually); Kurigram delivers album items as
  separate `Message` updates, so this preserves every item and its
  order, but they are re-delivered as individual messages rather than
  re-grouped into a single album bubble.
- **Force Sub verification for channels/groups requires the bot to be an
  admin** in each configured chat (a genuine Bot API requirement for
  `get_chat_member` to work reliably for non-participant lookups).
- **Link expiration** rejects access once expired but never deletes the
  underlying Store Channel content, per spec — expired links can be
  regenerated from the same stored content only if the owner re-runs
  `/genlink` (the original message metadata is not "renewed" automatically,
  since Telegram gives no reliable way to distinguish "same content,
  new link" from "genuinely new content" without owner action).
- **Shortener "callbacks" are not a real webhook.** No standard URL
  shortener offers a server-to-server callback into an arbitrary bot;
  they only redirect the user's browser. This implementation shortens a
  **Telegram deep link back to the bot itself**
  (`https://t.me/<bot>?start=verify_<session_id><proof>`), not a URL on
  any web server of ours — `proof` is a per-session secret minted at
  session-creation time (hashed, never stored in plaintext) and is
  required for delivery regardless of timing; elapsed time alone is
  never treated as sufficient. See "Shortener verification flow" above
  for the full, honest security model and its limits. `PUBLIC_BASE_URL`
  is unrelated to this — it's only used by the separate `/linkdl` direct
  HTTP-download feature.

  `triss/services/shortener.py` defines a `ShortenerProvider` extension
  point (`supports_completion_verification` / `verify_completion()`) for
  a future provider that offers a genuine, provider-side completion
  check; it is currently unused and does not gate anything, since no
  such provider is integrated here — implementing one for a specific
  paid API this project has no real credentials for would mean
  fabricating an integration, which we won't do. The bundled
  `GenericQueryProvider` (the common `?api=&url=&format=text` shortener
  shape) can be enabled today with just a Domain and API key — Minimum
  Time/Maximum Time is genuinely what gates delivery in that case, not a
  completion callback.
- **Elapsed-time checks assume the user's browser round-trip is what's
  being timed**, not network latency to the shortener's own servers;
  small clock/network variance is inherent to any time-boxed web
  redirect flow and is not something a Telegram bot can eliminate.
- **System Access grants are per-user, bot-wide, and time-boxed only.**
  There is no way to know from inside Telegram whether a user is still
  "actively engaged" during their window — `system_access_until` is a
  flat expiry timestamp, checked fresh on every access; it is not
  revoked early for any reason (ban/mute checks still run independently
  and still block a muted/banned user regardless of an active window).
- **🔒 Restrict Content is genuinely bot-wide, including the owner's own
  chat.** `protect_content` is a Telegram message property, not a
  per-viewer permission — Telegram does not let a bot exempt specific
  recipients (not even the bot's own owner) from it. While it's ON, the
  owner also cannot forward their own `/genlink` confirmations, broadcast
  drafts, or settings replies out of their chat with the bot; this is a
  real Telegram platform behavior, not a bug in this implementation.

## What could not be runtime-tested

This environment has no outbound network access and no live Telegram
bot token, MongoDB instance, or Store Channel to connect to, so the
following could **not** be exercised end-to-end and are validated only
statically (syntax compilation, cross-module symbol/reference checks,
and manual logic review):

- Actual Telegram API calls (`copy_message`, `get_chat_member`,
  `export_chat_invite_link`, FloodWait handling in practice, etc.)
- MongoDB connectivity, index creation, and query behavior against a
  real deployment
- The Railway/Render health-check integration against a real deploy
- Kurigram's `ButtonStyle` rendering in an actual Telegram client
- The live shortener provider HTTP call (`GenericQueryProvider.shorten`)
  and the `/v/<session_id>` landing route's redirect against a real
  shortener and a real `PUBLIC_BASE_URL` deployment
- The full end-to-end verification race scenarios (concurrent landing
  hits, concurrent `/start verify_...` callbacks) — the atomic
  `find_one_and_update` transitions were reviewed manually for
  correctness but not exercised under real concurrency against a live
  MongoDB

Before going to production, run the bot against a test bot token and a
disposable MongoDB database, and walk through `/genlink`, `/batch`,
`/custombatch`, `/broadcast`, and every `/settings` submenu at least once.
