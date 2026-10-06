import asyncio
import hmac
import json
import logging
import os
import signal
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

from pipeline.composite import finalize, probe
from pipeline.demo import create_demo
from shared.storage import Storage, validate_id

log = logging.getLogger("capture")


def now():
    return datetime.now(timezone.utc).isoformat()


class Target(BaseModel):
    guild_id: str = Field(pattern=r"^\d{1,25}$")
    channel_id: str = Field(pattern=r"^\d{1,25}$")


class Participant(BaseModel):
    id: str = Field(max_length=25)
    name: str = Field(min_length=1, max_length=100)


class Roster(BaseModel):
    participants: list[Participant] = Field(max_length=16)


class Start(Target, Roster):
    channel_name: str = Field(min_length=1, max_length=100)
    requested_by: str = Field(max_length=25)


class Recorder:
    def __init__(self):
        self.root = Path(os.environ.get("DATA_DIR", "/data"))
        self.width = int(os.environ.get("CAPTURE_WIDTH", "1280"))
        self.height = int(os.environ.get("CAPTURE_HEIGHT", "720"))
        self.fps = int(os.environ.get("CAPTURE_FPS", "30"))
        self.max_seconds = int(os.environ.get("MAX_RECORDING_SECONDS", "14400"))
        if self.width % 2 or self.height % 2 or self.width < 320 or self.height < 180 or not 1 <= self.fps <= 60 or self.max_seconds < 1:
            raise ValueError("Invalid capture dimensions, FPS, or recording limit")
        self.lock = asyncio.Lock()
        self.active = None
        self.armed = None
        self.process = None
        self.log_handle = None
        self.tasks = set()
        self.context = None
        self.page = None
        self.playwright = None
        self.cdp = None
        self.window_id = None
        self.storage = Storage()

    def folder(self, recording_id):
        return self.root / "jobs" / validate_id(recording_id)

    def save(self, session):
        folder = self.folder(session["id"])
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / "session.tmp"
        temporary.write_text(json.dumps(session, indent=2), encoding="utf-8")
        temporary.replace(folder / "session.json")

    def load(self, recording_id):
        try:
            return json.loads((self.folder(recording_id) / "session.json").read_text())
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Recording not found")

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def clear_overlay(self):
        try:
            if self.page and not self.page.is_closed():
                await self.page.evaluate("window.__vcStash?.stop()")
        except Exception:
            pass
        if self.cdp:
            try:
                await self.cdp.send("Browser.setWindowBounds", {"windowId": self.window_id, "bounds": {"windowState": "normal"}})
                await self.cdp.detach()
            except Exception:
                pass
            self.cdp = None
            self.window_id = None

    async def open(self):
        key = os.environ.get("RECORDER_API_KEY", "")
        if len(key) < 32 or key.startswith("replace-"):
            raise RuntimeError("Set a unique RECORDER_API_KEY with at least 32 characters")
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)
        # A crash never leaves a persisted job claiming to still be recording.
        for path in (self.root / "jobs").glob("*/session.json"):
            session = json.loads(path.read_text())
            if session["status"] in ("recording", "processing"):
                session.update(status="failed", error="Worker restarted; raw media retained. Use retry to recover.")
                self.save(session)
        await asyncio.to_thread(self.storage.check)
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            str(self.root / "browser"), headless=False, bypass_csp=True,
            viewport={"width": self.width, "height": self.height},
            ignore_default_args=["--mute-audio"],
            args=["--kiosk", f"--window-size={self.width},{self.height}", "--window-position=0,0",
                  "--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"],
        )
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        if self.page.url == "about:blank":
            try:
                await self.page.goto("https://discord.com/app", wait_until="domcontentloaded", timeout=30000)
            except Exception:
                log.warning("Discord page unavailable at startup; open it manually through noVNC")

    async def close(self):
        if self.active and self.active["status"] == "recording":
            async with self.lock:
                await self.stop()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    async def target_page(self, target):
        expected = f"https://discord.com/channels/{target['guild_id']}/{target['channel_id']}"
        for page in self.context.pages:
            if page.url.rstrip("/") == expected:
                self.page = page
                await page.bring_to_front()
                return page
        raise HTTPException(409, "Open this VC side chat in the recorder browser, manually join the call, watch the desired streams, then arm it.")

    async def arm(self, target):
        async with self.lock:
            if self.active:
                raise HTTPException(409, "Recorder is busy")
            await self.target_page(target)
            self.armed = target
            return {"status": "armed", **target}

    async def start(self, request):
        async with self.lock:
            if self.active:
                raise HTTPException(409, "Recorder is busy recording or processing another session")
            target = {key: request[key] for key in ("guild_id", "channel_id")}
            if self.armed != target:
                raise HTTPException(409, "Operator must arm this call with: python -m capture.control arm GUILD_ID CHANNEL_ID")
            await self.target_page(target)
            config = {"width": self.width, "height": self.height,
                      "videoSelector": os.environ.get("CAPTURE_VIDEO_SELECTOR", "video"),
                      "nameSelector": os.environ.get("CAPTURE_NAME_SELECTOR", ""),
                      "participants": request["participants"]}
            await self.page.evaluate(Path(__file__).with_name("grid.js").read_text(), config)
            try:
                await self.page.locator("#vc-stash-grid").click(position={"x": 10, "y": 10})
                await self.page.wait_for_function("document.fullscreenElement?.id === 'vc-stash-grid'", timeout=5000)
                self.cdp = await self.context.new_cdp_session(self.page)
                window = await self.cdp.send("Browser.getWindowForTarget")
                self.window_id = window["windowId"]
                await self.cdp.send("Browser.setWindowBounds", {"windowId": self.window_id, "bounds": {"windowState": "fullscreen"}})
                bounds = await self.cdp.send("Browser.getWindowBounds", {"windowId": self.window_id})
                if bounds["bounds"]["windowState"] != "fullscreen":
                    raise RuntimeError("Native Chromium window did not enter fullscreen")
            except Exception:
                await self.clear_overlay()
                raise HTTPException(503, "Could not enter canvas fullscreen; check the recorder browser display")
            await asyncio.sleep(0.5)
            session = {**request, "id": uuid.uuid4().hex, "status": "recording", "started_at": now(),
                       "participant_history": [{"at": now(), "participants": request["participants"]}],
                       "participants": [p["name"] for p in request["participants"]]}
            self.active = session
            self.armed = None
            self.save(session)
            folder = self.folder(session["id"])
            self.log_handle = (folder / "ffmpeg.log").open("wb")
            command = ["ffmpeg", "-y", "-nostdin", "-v", "warning", "-thread_queue_size", "1024",
                       "-f", "x11grab", "-draw_mouse", "0", "-video_size", f"{self.width}x{self.height}", "-framerate", str(self.fps),
                       "-i", os.environ.get("DISPLAY", ":99") + ".0+0,0", "-thread_queue_size", "1024",
                       "-f", "pulse", "-i", "recording.monitor", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                       "-af", "aresample=async=1:first_pts=0", "-t", str(self.max_seconds), str(folder / "raw.mkv")]
            try:
                self.process = await asyncio.create_subprocess_exec(*command, stdout=self.log_handle, stderr=self.log_handle)
                await asyncio.sleep(1)
                if self.process.returncode is not None:
                    raise RuntimeError("FFmpeg failed to start; inspect the job's ffmpeg.log")
            except Exception as error:
                session.update(status="failed", error=str(error))
                self.save(session)
                self.active = None
                self.log_handle.close()
                self.log_handle = None
                await self.clear_overlay()
                raise HTTPException(503, str(error))
            self.spawn(self.monitor(session["id"]))
            return session

    async def monitor(self, recording_id):
        while self.active and self.active["id"] == recording_id and self.active["status"] == "recording":
            await asyncio.sleep(1)
            async with self.lock:
                if not self.active or self.active["id"] != recording_id or self.active["status"] != "recording":
                    break
                if self.process.returncode is not None:
                    # Normal exit is the configured duration limit; preserve and finalize.
                    await self.stop(error=None if self.process.returncode == 0 else "FFmpeg exited unexpectedly; retry retained media")
                    break
                try:
                    alive = await self.page.evaluate("document.fullscreenElement?.id === 'vc-stash-grid'")
                    bounds = await self.cdp.send("Browser.getWindowBounds", {"windowId": self.window_id})
                    alive = alive and bounds["bounds"]["windowState"] == "fullscreen"
                    if not alive:
                        raise RuntimeError("Capture left fullscreen or the browser navigated away")
                except Exception as error:
                    await self.stop(error=str(error))
                    break

    async def update_roster(self, recording_id, participants):
        async with self.lock:
            if not self.active or self.active["id"] != recording_id or self.active["status"] != "recording":
                raise HTTPException(409, "Session is not recording")
            history = self.active["participant_history"]
            if history[-1]["participants"] != participants:
                await self.page.evaluate("p => window.__vcStash.setParticipants(p)", participants)
                history.append({"at": now(), "participants": participants})
                self.active["participants"] = sorted(set(self.active["participants"]) | {p["name"] for p in participants})
                self.save(self.active)
            return {"status": "ok"}

    async def stop(self, error=None):
        session = self.active
        session.update(status="processing", ended_at=now())
        self.save(session)
        if self.process and self.process.returncode is None:
            self.process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.process.wait(), timeout=20)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
                error = "FFmpeg did not shut down cleanly; retry retained media"
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        try:
            session["capture_stats"] = await self.page.evaluate("window.__vcStash?.stats || {}")
        except Exception:
            pass
        await self.clear_overlay()
        if error:
            session.update(status="failed", error=error)
            self.save(session)
            self.active = None
        else:
            self.save(session)
            self.spawn(self.process_job(session))
        return session

    def finish_sync(self, session):
        folder = self.folder(session["id"])
        output = folder / "video.mp4"
        if session.get("demo"):
            create_demo(folder)
            (folder / "demo.mp4").replace(output)
        else:
            finalize(folder / "raw.mkv", output)
        session["duration_seconds"] = float(probe(output)["format"]["duration"])
        session.pop("error", None)
        return self.storage.publish(output, {**session, "status": "complete"})

    async def process_job(self, session):
        try:
            result = await asyncio.to_thread(self.finish_sync, dict(session))
            session.update(result)
            session.pop("error", None)
        except Exception as error:
            log.exception("Recording processing failed: %s", session["id"])
            session.update(status="failed", error=str(error))
        finally:
            self.save(session)
            async with self.lock:
                if self.active and self.active["id"] == session["id"]:
                    self.active = None

    async def demo(self):
        async with self.lock:
            if self.active:
                raise HTTPException(409, "Recorder is busy")
            session = {"id": uuid.uuid4().hex, "status": "processing", "demo": True,
                       "channel_name": "Grid demo", "guild_id": "demo", "channel_id": "demo",
                       "started_at": now(), "participants": ["Alex", "Sam", "Jordan", "Casey"]}
            self.active = session
            self.save(session)
            self.spawn(self.process_job(session))
            return session

    async def retry(self, recording_id):
        async with self.lock:
            if self.active:
                raise HTTPException(409, "Recorder is busy")
            session = self.load(recording_id)
            if session["status"] != "failed":
                raise HTTPException(409, "Only failed jobs can be retried")
            session.update(status="processing")
            self.active = session
            self.save(session)
            self.spawn(self.process_job(session))
            return session


def authorize(authorization: str = Header(default="")):
    expected = os.environ.get("RECORDER_API_KEY", "")
    if not expected or not hmac.compare_digest(authorization.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(401, "Invalid recorder API key")


@asynccontextmanager
async def lifespan(app):
    recorder = Recorder()
    app.state.recorder = recorder
    try:
        await recorder.open()
        yield
    finally:
        await recorder.close()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    recorder = app.state.recorder
    if not recorder.context or not recorder.page or recorder.page.is_closed():
        raise HTTPException(503, "Browser unavailable")
    return {"status": "ok"}


@app.get("/state", dependencies=[Depends(authorize)])
async def state():
    recorder = app.state.recorder
    return recorder.active or {"status": "idle", "armed": recorder.armed}


@app.post("/arm", dependencies=[Depends(authorize)])
async def arm(target: Target):
    return await app.state.recorder.arm(target.model_dump())


@app.post("/sessions", dependencies=[Depends(authorize)], status_code=201)
async def start(request: Start):
    return await app.state.recorder.start(request.model_dump())


@app.get("/sessions/{recording_id}", dependencies=[Depends(authorize)])
async def job(recording_id: str):
    return app.state.recorder.load(recording_id)


@app.put("/sessions/{recording_id}/participants", dependencies=[Depends(authorize)])
async def participants(recording_id: str, roster: Roster):
    return await app.state.recorder.update_roster(recording_id, roster.model_dump()["participants"])


@app.post("/end", dependencies=[Depends(authorize)], status_code=202)
async def end(target: Target):
    recorder = app.state.recorder
    async with recorder.lock:
        session = recorder.active
        if not session or session["status"] != "recording":
            raise HTTPException(409, "No active recording")
        if any(session[key] != value for key, value in target.model_dump().items()):
            raise HTTPException(409, "That channel does not own the active recording")
        return await recorder.stop()


@app.post("/sessions/{recording_id}/retry", dependencies=[Depends(authorize)], status_code=202)
async def retry(recording_id: str):
    return await app.state.recorder.retry(recording_id)


@app.post("/demo", dependencies=[Depends(authorize)], status_code=202)
async def demo():
    return await app.state.recorder.demo()
