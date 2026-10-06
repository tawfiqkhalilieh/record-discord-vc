(() => {
  const video = document.getElementById('live-video');
  if (!video) return;
  const status = document.getElementById('live-status');
  const obsId = document.body.dataset.liveObs;
  const token = new URLSearchParams(location.search).get('token');
  const query = token ? `?token=${encodeURIComponent(token)}` : '';
  let player, currentId, retryAt = 0;

  function reset() {
    if (player) player.destroy();
    player = null;
    video.pause();
    video.removeAttribute('src');
    video.load();
    currentId = null;
    if (!obsId) video.hidden = true;
  }

  function play() {
    video.play().catch(() => {
      status.textContent = 'Press play to start video and audio.';
      video.controls = true;
    });
  }

  function connect(id, url) {
    reset();
    currentId = id;
    video.hidden = false;
    status.textContent = 'Starting live stream…';
    if (window.Hls && Hls.isSupported()) {
      const hls = player = new Hls({ enableWorker: false, liveSyncDurationCount: 2, liveMaxLatencyDurationCount: 5, backBufferLength: 12, maxBufferLength: 12 });
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, play);
      hls.on(Hls.Events.ERROR, (_, data) => {
        if (data.fatal) {
          status.textContent = 'Stream interrupted. Reconnecting…';
          reset();
          retryAt = Date.now() + 3000;
        }
      });
    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
      video.src = url;
      play();
    } else {
      status.textContent = 'This browser does not support HLS playback.';
      retryAt = Infinity;
    }
  }

  video.addEventListener('playing', () => { status.textContent = obsId ? '' : 'Live · preview starts muted'; });
  video.addEventListener('error', () => { reset(); retryAt = Date.now() + 3000; });

  async function poll() {
    try {
      const response = await fetch(obsId ? `/live/${obsId}/state${query}` : '/api/live', { cache: 'no-store' });
      if (!response.ok) throw new Error(response.status === 401 ? 'Access expired. Sign in again or refresh the OBS URL.' : 'Capture worker unavailable. Retrying…');
      const state = await response.json();
      const live = state.status === 'recording';
      if (!obsId) {
        document.getElementById('live-title').textContent = live ? state.channel_name : 'Live stream';
        document.getElementById('obs-details').hidden = !live;
        document.getElementById('obs-url').value = live ? state.obs_url : '';
      }
      if (live) {
        const id = obsId || state.id;
        if (currentId !== id && Date.now() >= retryAt) connect(id, obsId ? `/live/${id}/index.m3u8${query}` : state.playlist_url);
      } else {
        reset();
        status.textContent = state.status === 'processing' ? 'Call ended. Saving the recording…' : 'No live call. Start capture with /record start.';
      }
    } catch (error) {
      reset();
      if (!obsId) document.getElementById('obs-details').hidden = true;
      status.textContent = error.message;
    } finally {
      setTimeout(poll, 3000);
    }
  }
  poll();
})();
