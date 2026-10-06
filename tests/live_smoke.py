"""Optional container integration check: synthetic browser streams → FFmpeg → real S3.

docker compose cp tests/live_smoke.py capture:/tmp/live_smoke.py
docker compose exec capture python /tmp/live_smoke.py
Run on an idle test stack: this opens a synthetic call on the virtual display.
"""
import asyncio
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

from playwright.async_api import async_playwright

from capture.app import Recorder
from pipeline.composite import probe


async def main():
    recorder = Recorder()
    recorder.root = Path(tempfile.mkdtemp(prefix="vc-live-smoke-"))
    recorder.playwright = await async_playwright().start()
    try:
        recorder.context = await recorder.playwright.chromium.launch_persistent_context(
            str(recorder.root / "browser"), headless=False, bypass_csp=True,
            viewport={"width": recorder.width, "height": recorder.height},
            ignore_default_args=["--mute-audio"],
            args=["--kiosk", f"--window-size={recorder.width},{recorder.height}", "--window-position=0,0",
                  "--autoplay-policy=no-user-gesture-required", "--disable-dev-shm-usage"],
        )
        page = recorder.context.pages[0]
        recorder.page = page
        await page.route("https://discord.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body="<html><body><div id='call'></div></body></html>"))
        await page.goto("https://discord.com/channels/123/456")
        await page.evaluate("""async () => {
          for (const [name, color] of [['Alex', '#dc2626'], ['Sam · Screen share', '#2563eb']]) {
            const canvas = document.createElement('canvas'); canvas.width=640; canvas.height=360;
            const ctx = canvas.getContext('2d');
            setInterval(() => { ctx.fillStyle=color; ctx.fillRect(0,0,640,360); }, 50);
            const video=document.createElement('video'); video.setAttribute('aria-label',name);
            video.autoplay=true; video.muted=true; video.srcObject=canvas.captureStream(20);
            document.getElementById('call').appendChild(video); await video.play();
          }
          const audio = new AudioContext(); await audio.resume();
          const oscillator = audio.createOscillator(); oscillator.frequency.value=440;
          const gain = audio.createGain(); gain.gain.value=0.15;
          oscillator.connect(gain).connect(audio.destination); oscillator.start();
          window.__audio = audio;
        }""")
        await recorder.arm({"guild_id": "123", "channel_id": "456"})
        session = await recorder.start({"guild_id": "123", "channel_id": "456", "channel_name": "Live capture smoke test",
            "requested_by": "789", "participants": [{"id": "1", "name": "Alex"}, {"id": "2", "name": "Sam"}]})
        await asyncio.sleep(1)
        assert await page.evaluate("document.fullscreenElement?.id") == "vc-stash-grid"
        assert await page.evaluate("window.__vcStash.stats.tileCount") == 2
        await recorder.update_roster(session["id"], [{"id": "1", "name": "Alex"}, {"id": "2", "name": "Sam"}, {"id": "3", "name": "Jordan"}])
        assert await page.evaluate("window.__vcStash.stats.tileCount") == 3
        await asyncio.sleep(1)
        await page.evaluate("document.querySelectorAll('video')[1].remove()")
        await recorder.update_roster(session["id"], [{"id": "1", "name": "Alex"}, {"id": "3", "name": "Jordan"}])
        assert await page.evaluate("window.__vcStash.stats.tileCount") == 2
        await asyncio.sleep(1)
        async with recorder.lock:
            await recorder.stop()
        await asyncio.gather(*list(recorder.tasks))
        final = recorder.load(session["id"])
        assert final["status"] == "complete", final
        assert len(final["participant_history"]) == 3
        output = recorder.folder(session["id"]) / "video.mp4"
        data = probe(output)
        assert float(data["format"]["duration"]) > 2
        assert {s["codec_type"] for s in data["streams"]} == {"video", "audio"}
        frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "2", "-i", str(output), "-frames:v", "1",
                                "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], check=True, capture_output=True).stdout
        # Canvas borders are dark. A light browser toolbar at (0, 0) would both
        # contaminate the recording and clip its bottom row on the virtual display.
        assert max(frame[:3]) < 45, f"Browser chrome visible in recording: {list(frame[:3])}"
        result = subprocess.run(["ffmpeg", "-i", str(output), "-af", "volumedetect", "-f", "null", "-"],
                                check=True, capture_output=True, text=True)
        volume = float(re.search(r"mean_volume: ([-\d.]+) dB", result.stderr).group(1))
        assert volume > -60, f"Audio loopback was silent: {volume} dB"
        assert recorder.storage.get(session["id"])["status"] == "complete"
        print(f"PASS: live 2 → 3 → 2 grid, audio {volume} dB, uploaded ID {session['id']}")
        print(f"Retained test artifact: {output}")
    finally:
        await recorder.close()


asyncio.run(main())
