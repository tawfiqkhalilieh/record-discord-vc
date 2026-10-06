"""Run a synthetic live call through the capture API and dashboard, without Discord.

Requires requirements-dev.txt (moto supplies isolated, in-memory S3 storage).
"""
import argparse
import os
import secrets
import signal
import socket
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
import uvicorn
from moto import mock_aws

from capture.app import Recorder, app as capture_app, live_encoding_options, now
from dashboard.app import app as dashboard_app
from pipeline.demo import create_demo
from shared.storage import Storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=3000)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    password = 'live-demo-password'
    with TemporaryDirectory(prefix='vc-live-demo-') as temporary, mock_aws():
        os.environ.update(
            DATA_DIR=temporary, S3_ENDPOINT='https://s3.amazonaws.com',
            S3_ACCESS_KEY='demo', S3_SECRET_KEY='demo', S3_BUCKET='recordings',
            S3_REGION='us-east-1', ADMIN_PASSWORD=password, COOKIE_SECURE='false',
            SESSION_SECRET=secrets.token_hex(32), RECORDER_API_KEY=secrets.token_hex(32),
            PUBLIC_BASE_URL=os.environ.get('PUBLIC_BASE_URL', f'http://localhost:{args.port}'),
        )
        boto3.client('s3', region_name='us-east-1').create_bucket(Bucket='recordings')
        source = create_demo(Path(temporary) / 'sources')
        storage = Storage()
        storage.publish(source, {'id': uuid.uuid4().hex, 'status': 'complete',
            'channel_name': 'Synthetic grid recording', 'started_at': now(),
            'participants': ['Alex', 'Sam', 'Jordan', 'Casey'], 'duration_seconds': 8})
        recorder = Recorder()
        recorder.active = {'id': uuid.uuid4().hex, 'status': 'recording',
            'channel_name': 'Synthetic live call · grid demo', 'started_at': now(),
            'participants': ['Alex', 'Sam', 'Jordan', 'Casey']}
        recorder.save(recorder.active)
        folder = recorder.folder(recorder.active['id'])
        (folder / 'live').mkdir()
        process = subprocess.Popen(['ffmpeg', '-y', '-v', 'error',
            '-re', '-stream_loop', '-1', '-i', str(source),
            '-stream_loop', '-1', '-i', str(source), '-c:v', 'libx264', '-preset', 'veryfast',
            *live_encoding_options(15, 14400)], cwd=folder)

        @asynccontextmanager
        async def capture_lifespan(application):
            application.state.recorder = recorder
            yield

        capture_app.router.lifespan_context = capture_lifespan
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        os.environ['RECORDER_URL'] = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(capture_app, log_level='warning'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.05)
            if not server.started:
                raise RuntimeError('Demo capture API did not start')
            print(f'Dashboard: http://localhost:{args.port} · password: {password}', flush=True)
            print('Synthetic media only; no Discord connection. Live output lasts up to four hours.', flush=True)
            uvicorn.run(dashboard_app, host=args.host, port=args.port, log_level='warning', access_log=False)
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
