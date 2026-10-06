"""Exercise real HLS playback through the dashboard and OBS page in Chromium."""
import socket
import subprocess
import threading
import time
from contextlib import asynccontextmanager

import httpx
import uvicorn
from playwright.sync_api import expect, sync_playwright


def wait_for_playback(page):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if page.locator('#live-video').evaluate('(v) => v.currentTime > 0'):
            return
        time.sleep(.1)
    raise AssertionError(page.locator('#live-status').inner_text())


def test_dashboard_and_obs_play_live_audio_video(storage, tmp_path, monkeypatch):
    from capture.app import live_encoding_options
    from dashboard.app import app

    (tmp_path / 'live').mkdir()
    process = subprocess.Popen(['ffmpeg', '-y', '-v', 'error', '-re', '-f', 'lavfi', '-i',
        'testsrc2=size=320x180:rate=15', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
        '-c:v', 'libx264', '-preset', 'ultrafast', *live_encoding_options(15, 45)],
        cwd=tmp_path, stderr=subprocess.PIPE)
    state = {'id': 'e' * 32, 'status': 'recording', 'channel_name': 'Browser live test',
             'participants': ['Alex']}
    def upstream(request):
        if request.url.path == '/state':
            return httpx.Response(200, json=state)
        name = request.url.path.rsplit('/', 1)[-1]
        path = tmp_path / 'live' / name
        if state['status'] != 'recording' or not path.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=path.read_bytes())

    @asynccontextmanager
    async def lifespan(application):
        application.state.storage = storage
        async with httpx.AsyncClient(base_url='http://capture', transport=httpx.MockTransport(upstream)) as upstream_client:
            application.state.recorder_http = upstream_client
            yield

    monkeypatch.setattr(app.router, 'lifespan_context', lifespan)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    monkeypatch.setenv('PUBLIC_BASE_URL', url)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=['--autoplay-policy=no-user-gesture-required'])
            page = browser.new_page()
            page.goto(url + '/login')
            page.locator('input[type=password]').fill('testing-password-123456')
            page.locator('.login button').click()
            page.wait_for_url(url + '/')
            wait_for_playback(page)
            assert page.locator('#live-video').evaluate('(v) => v.videoWidth') == 320
            obs_url = page.locator('#obs-url').input_value()
            # OBS has no admin session cookie. Its signed source must still play audio/video.
            context = browser.new_context()
            source = context.new_page()
            source.goto(obs_url)
            wait_for_playback(source)
            assert source.locator('#live-video').evaluate('(v) => !v.muted && v.videoWidth === 320')
            state['status'] = 'processing'
            expect(page.locator('#live-video')).to_be_hidden(timeout=10000)
            expect(source.locator('#live-video')).to_have_js_property('paused', True, timeout=10000)
            assert 'Saving the recording' in page.locator('#live-status').inner_text()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
