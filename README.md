# Discord VC Stash

A self-hosted voice-call recording system: a Discord command bot, an operator-joined Chromium capture worker, dynamic video grids, FFmpeg encoding, private MinIO storage, and a password-protected recording library.

**Capture requires an operator.** Discord's bot API does not provide the supported video reception this system needs. [Discord prohibits automated normal-user accounts](https://support.discord.com/hc/en-us/articles/115002192352-Automated-User-Accounts-Self-Bots). This implementation never logs in or joins a call automatically: a human signs in, joins, and selects streams through noVNC. `/record start` and `/record end` control local capture after that setup. The command bot itself does not join the VC; audio and video come from the browser.

## Architecture

```text
VC side chat: /record start | /record end
    → bot/ (discord.js, official bot token)
    → capture/ (internal authenticated FastAPI API)
        → Chromium on Xvfb/Openbox, manually joined call
        → canvas grid: loaded video elements + participant names/voice-only tiles
        → FFmpeg x11grab + PulseAudio monitor → H.264/AAC MKV → MP4
    → shared/storage.py → MinIO: private MP4 + JSON metadata
    → dashboard/ → admin session → library + authenticated, seekable playback
```

`pipeline/` also provides an independent FFmpeg compositor for time-aligned participant streams. `tests/` covers commands, capture guards, live canvas changes, real FFmpeg output, storage, authentication, and playback. This version runs **one recording or processing job at a time**, with up to **16 tiles** at 1280×720 / 30 fps by default. Cameras and shares are separate tiles; voice-only members get name/initial tiles. No privileged Discord intents are needed.

`infra/minio/` builds MinIO from pinned commit `7aac2a2c5b7c882e68c1ce017d8256be2feea27f` because upstream community container images are no longer available. The [upstream repository](https://github.com/minio/minio) is archived and the community edition is source-only; this is a local development storage scaffold. Its AGPLv3 license is included in the built image. The initial build downloads Go dependencies and takes longer than subsequent cached builds. Bucket initialization uses boto3, without an `mc` image.

## Requirements

- Linux host with Docker Engine and Docker Compose v2, internet access for Discord and image downloads.
- Start with 4 CPU cores, 4 GB RAM, and sufficient disk for both raw recordings and uploaded MP4s. Encoding requirements vary by call size.
- A Discord application and bot, a server you manage, and a human browser participant permitted to view the call.

## Start the services

```bash
cp .env.example .env
openssl rand -hex 32  # run separately for each application/storage secret
```

Edit `.env`. Replace **all** placeholder passwords and secrets: `MINIO_ROOT_PASSWORD`, `ADMIN_PASSWORD`, `SESSION_SECRET`, `RECORDER_API_KEY`, and `VNC_PASSWORD`. `ADMIN_PASSWORD` must have at least 12 characters, and `SESSION_SECRET` / `RECORDER_API_KEY` at least 32. Classic VNC only uses the first eight password characters; give it an independent password and keep its port local.

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f capture dashboard
```

This starts MinIO, creates a **private** bucket, launches the browser worker, and serves the dashboard. The bot is in the `discord` profile so you can try the system without a bot token.

- Dashboard: <http://localhost:3000> — enter `ADMIN_PASSWORD`.
- Operator browser: <http://localhost:6080/vnc.html> — Connect, then enter `VNC_PASSWORD`.
- MinIO console: <http://localhost:9001> — use `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`.

The S3 API and recorder API have no published host ports. The dashboard streams video from MinIO on your behalf; there are no public bucket objects or externally addressed presigned URLs. Named volumes preserve browser authentication, jobs, and storage across service restarts. For local development, the MinIO root credentials also serve as application S3 credentials; use scoped S3 credentials when adapting the deployment beyond this local scaffold.

### Try the complete media/storage/dashboard path

```bash
docker compose exec capture python -m capture.control demo
# Copy the returned recording ID:
docker compose exec capture python -m capture.control job RECORDING_ID
```

When the status becomes `complete`, refresh the dashboard. The demo creates actual camera/share stand-ins with sound, composes an eight-second video with **1 → 2 → 4 → 3** tiles, uploads it, and publishes its metadata. It requires no Discord credentials or joined call.

## Configure the bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications), create an application and bot. Put the bot token in `DISCORD_TOKEN` and its application ID in `DISCORD_APPLICATION_ID`. Set `DISCORD_GUILD_ID` to your server ID. Enable Developer Mode in Discord to copy IDs.
2. Invite the application with scopes `bot` and `applications.commands`, and permissions **View Channels** and **Send Messages**. Enable **Use Application Commands** for the users who will invoke it in the voice channel's chat. The bot uses `Guilds` and `GuildVoiceStates` intents; leave privileged intents disabled.
3. By default, users need **Manage Server**. Optionally set `RECORD_ROLE_ID` to allow a recording role as well; the handler checks membership on every command. If you configured command permissions separately in Server Settings → Integrations, ensure that role can see `/record` there.
4. Start the bot:

```bash
docker compose --profile discord up -d --build
docker compose logs -f bot
```

The bot upserts its guild-scoped `/record` command on startup, with `start` and `end` subcommands. It preserves other commands owned by the application. Guild commands avoid the global registration propagation delay. You can register independently with:

```bash
docker compose --profile discord run --rm --no-deps bot npm run register
```

Command definitions follow [Discord's application command API](https://docs.discord.com/developers/docs/interactions/slash-commands).

## Record a call

1. Open noVNC. Sign into Discord **manually**, including any MFA prompt. Browser credentials persist in the `capture-data` volume; no normal-user token is requested in configuration.
2. Join the desired voice channel manually. Mute the recorder's microphone, **do not deafen it**, and set speaker volume as desired. Open the VC's side chat so the browser URL is `https://discord.com/channels/GUILD_ID/CHANNEL_ID`.
3. Open/watch the webcams and screenshares you want included. Switch to Discord's call grid when possible. Discord may only deliver a selected screenshare; an unwatched stream cannot be captured. Leave this browser tab active.
4. Optionally set `RECORDER_USER_ID` in `.env` to omit the human recorder from the bot's participant roster, then recreate the bot with `docker compose --profile discord up -d`.
5. Arm that call after confirming its audio is audible and desired videos are visible:

```bash
docker compose exec capture python -m capture.control arm GUILD_ID CHANNEL_ID
```

6. A permitted member joins that same VC and runs **`/record start` in its side chat**. The worker replaces its display with a composited canvas, and the bot posts a recording notice. Make sure participants know the call is being recorded before starting.
7. Run **`/record end` in the same VC side chat**. The bot confirms processing has been queued and provides the stash link. Refresh it after upload finishes. Login is required for both viewing and downloading.

Arming is consumed by each start; re-arm before the next recording. The recording stops automatically after `MAX_RECORDING_SECONDS` (four hours by default). Another channel cannot stop the current channel's recording. Processing blocks the next recording until the job completes or fails.

### Capture boundaries

- This is **browser capture**, not an official Discord video receiver. Live Discord capture must be validated in your own server; repository tests use synthetic browser streams and real local media.
- Only `<video>` elements already loaded in the joined browser can be drawn. Watch all desired streams before starting. The worker does not fetch hidden streams or select new shares automatically.
- Video names are inferred from nearby DOM/ARIA labels and the bot roster. Discord UI changes may require `CAPTURE_VIDEO_SELECTOR` or `CAPTURE_NAME_SELECTOR` adjustments. The latter is a CSS selector searched in each video's ancestor containers. Unknown labels appear as `Video N`; an unidentified video may also have a separate roster tile.
- The live grid reflows every 500 ms for video changes and on roster updates. The bot syncs the roster after voice-state changes and every five seconds. The first 16 tiles are displayed; `capture_stats.overflow` records whether the final observed layout exceeded the limit.
- Audio is the browser's mixed speaker output, including any notification sounds. It is not isolated per user. FFmpeg captures the virtual display; leave the recorder browser untouched while recording. Navigating away causes the watchdog to fail the job and preserve partial media.
- The bot sends start/end notices, but it does not represent a separate voice connection. The operator must ensure the human browser remains joined. Arming checks the page URL and is an operator confirmation, not proof from a supported Discord call-state API.

## Jobs and recovery

```bash
docker compose exec capture python -m capture.control state
docker compose exec capture python -m capture.control job RECORDING_ID
docker compose exec capture python -m capture.control retry RECORDING_ID
```

Job status is persisted at `/data/jobs/ID/session.json` inside `capture-data`. Jobs transition through `recording`, `processing`, `complete`, or `failed`. Retries are allowed only for failed jobs and reprocess/upload their retained raw media. A worker restart marks unfinished jobs failed so they can be recovered; it does not silently resume capture or rejoin a call. A damaged MKV may still fail recovery.

Inspect `/data/jobs/ID/ffmpeg.log` for capture errors, and `docker compose logs capture` for encoding/storage errors. MP4 objects are written before metadata, so incomplete uploads stay out of the dashboard. Each finalized job contains:

```text
recordings/ID/video.mp4
recordings/ID/metadata.json
```

Metadata includes channel, participants, timestamps, duration, file size, and object key. The dashboard paginates S3 listing calls and sorts metadata by start time; it does not maintain a separate database. Raw files and local final videos are retained for recovery and **have no automatic retention policy**. Monitor disk usage and remove completed job directories or archive them as needed. `docker compose down` preserves volumes; adding `-v` deletes recordings and saved browser credentials.

## Independent FFmpeg grid compositing

You can compose pre-captured, time-aligned streams without Chromium:

```bash
python -m pipeline.demo --output-dir demo-output
python -m pipeline.composite demo-output/manifest.json demo-output/rebuilt.mp4
```

A manifest consists of segments, each describing one participant layout:

```json
{
  "width": 1280,
  "height": 720,
  "fps": 30,
  "segments": [
    {
      "duration": 5,
      "videos": [
        {"path": "alex.mp4", "name": "Alex"},
        {"path": "sam-share.mp4", "name": "Sam · Screen share"}
      ],
      "audio": ["call-audio.wav"]
    }
  ]
}
```

Paths are relative to the manifest. Each source starts at segment time zero; callers must trim/synchronize inputs before supplying them. Add a segment at each participant transition. The compositor computes a grid, scales and letterboxes inputs, centers incomplete rows, draws names from UTF-8 text files, pads shorter videos, mixes audio, and concatenates the segments. With no audio inputs it adds silence. Names do not enter a shell or FFmpeg expression. Live capture uses the same grid concept in canvas, with FFmpeg providing video/audio encoding and final MP4 remuxing.

## Serve through Tailscale

Keep `BIND_ADDRESS=127.0.0.1`, install Tailscale on the host, and expose only the dashboard:

```bash
tailscale serve --bg http://localhost:3000
```

Set `PUBLIC_BASE_URL` to the HTTPS URL reported by Tailscale, set `COOKIE_SECURE=true`, then recreate the dashboard and bot:

```bash
docker compose --profile discord up -d
```

[Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) makes the service available inside your tailnet. Apply your tailnet access rules; the stash still requires its admin password. The bot's playback links use `PUBLIC_BASE_URL`, while media URLs are relative and pass through the dashboard. No public S3 hostname is necessary.

For remote operator setup, use an SSH tunnel to the host's loopback noVNC port:

```bash
ssh -L 6080:127.0.0.1:6080 user@YOUR_TAILSCALE_HOST
```

Then open `http://localhost:6080/vnc.html` on your own machine. MinIO, the recording control API, and VNC do not need public exposure.

## Development and validation

Python 3.11+ and Node 20+ are required outside Docker. Install FFmpeg, DejaVu fonts, and Playwright's Chromium:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
playwright install --with-deps chromium
pytest -q
cd bot
npm ci
npm test
```

The production Python dependency graph is pinned in `requirements.lock`; `requirements.txt` lists direct dependencies. Regenerate with `uv pip compile requirements.txt -o requirements.lock` when upgrading. Keep the capture Docker image's Playwright version synchronized with the Python package. The workflow template in `ci/verify.yml` runs tests, validates Compose configuration, and builds the service images. To enable GitHub Actions, copy it to `.github/workflows/verify.yml` using a credential with workflow permission; the mission's push credential cannot create workflow files. Tests do not connect to Discord; slash-command registration in a real guild and live call capture require the setup above.

An optional live capture smoke test checks Xvfb, PulseAudio, changing synthetic browser streams, FFmpeg, and real S3 together. Use an idle test stack: it opens a separate browser on the recorder's virtual display and publishes a test recording.

```bash
docker compose cp tests/live_smoke.py capture:/tmp/live_smoke.py
docker compose exec capture python /tmp/live_smoke.py
```

Validated on 2026-10-06: all service images built, 22 Python tests and 3 bot tests passed, and the container smoke test recorded a clean **2 → 3 → 2** grid with audible loopback audio and uploaded it to MinIO. Admin login, real S3 metadata listing, HTTP range playback, and HTML5 seeking were also verified. Real Discord registration and call capture require your credentials and operator setup and were not exercised in these checks.

To run the dashboard directly, export `.env` values plus `S3_ACCESS_KEY`, `S3_SECRET_KEY`, and an accessible `S3_ENDPOINT`, then run `uvicorn dashboard.app:app --port 3000`. The capture worker additionally needs Xvfb, PulseAudio, and its display setup; use its Docker service for that environment.
