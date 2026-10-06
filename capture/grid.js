// Installed in an operator-joined Discord page. No login, joining, or user-token API calls.
// Read only streams Discord already exposes in the DOM; hidden/unwatched streams cannot be recovered.
(config) => {
  window.__vcStash?.stop();
  const canvas = document.createElement('canvas');
  canvas.id = 'vc-stash-grid';
  canvas.width = config.width;
  canvas.height = config.height;
  Object.assign(canvas.style, { position: 'fixed', inset: '0', width: '100vw', height: '100vh',
    zIndex: '2147483647', background: '#0b1020', pointerEvents: 'auto' });
  document.documentElement.appendChild(canvas);
  // A trusted local click enters element fullscreen, hiding browser chrome.
  canvas.addEventListener('click', () => { void canvas.requestFullscreen().catch(() => {}); }, { once: true });
  const ctx = canvas.getContext('2d');
  let participants = config.participants || [];
  let stopped = false, lastScan = 0, tiles = [], animation;
  const stats = { tileCount: 0, videoCount: 0, overflow: false, names: [] };
  function scan() {
    const videos = [...document.querySelectorAll(config.videoSelector)]
      .filter(v => v instanceof HTMLVideoElement && v.readyState >= 2 && v.videoWidth > 0 &&
        (!v.srcObject || v.srcObject.getVideoTracks().some(t => t.readyState === 'live')));
    const used = new Set();
    tiles = videos.map((video, i) => {
      let label = video.getAttribute('aria-label') || video.getAttribute('title') || '';
      let node = video.parentElement;
      for (let depth = 0; node && depth < 5; depth++, node = node.parentElement) {
        if (config.nameSelector) {
          const text = node.querySelector(config.nameSelector)?.textContent?.trim();
          if (text) { label = text; break; }
        }
        const accessible = node.getAttribute('aria-label');
        if (accessible) { label = accessible; break; }
        // Avoid scanning the whole call view, which would mislabel every video with its first name.
        const text = node.textContent?.trim() || '';
        if (text.length < 300) {
          const member = participants.find(p => text.includes(p.name));
          if (member) { label = member.name; break; }
        }
      }
      label = label.trim().slice(0, 100) || `Video ${i + 1}`;
      const member = participants.find(p => label === p.name || label.includes(p.name));
      if (member) used.add(member.id);
      return { video, name: label };
    });
    tiles.push(...participants.filter(p => !used.has(p.id)).map(p => ({ name: p.name })));
    Object.assign(stats, { videoCount: videos.length, tileCount: Math.min(tiles.length, 16),
      overflow: tiles.length > 16, names: tiles.map(t => t.name) });
    tiles = tiles.slice(0, 16);
  }
  function frame(now) {
    if (stopped) return;
    if (now - lastScan > 500) { scan(); lastScan = now; }
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#0b1020'; ctx.fillRect(0, 0, w, h);
    if (!tiles.length) {
      ctx.fillStyle = '#cbd5e1'; ctx.font = '28px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('Waiting for participants', w / 2, h / 2);
    } else {
      const cols = Math.ceil(Math.sqrt(tiles.length)), rows = Math.ceil(tiles.length / cols);
      const tw = Math.floor(w / cols), th = Math.floor(h / rows);
      tiles.forEach((tile, index) => {
        const row = Math.floor(index / cols), count = Math.min(cols, tiles.length - row * cols);
        const x = Math.floor((w - count * tw) / 2) + (index % cols) * tw, y = row * th;
        const gap = 5, labelHeight = Math.max(32, Math.floor(th / 8));
        ctx.fillStyle = '#172033'; ctx.fillRect(x + gap, y + gap, tw - gap * 2, th - gap * 2);
        if (tile.video) {
          const scale = Math.min((tw - gap * 2) / tile.video.videoWidth, (th - gap * 2) / tile.video.videoHeight);
          const vw = tile.video.videoWidth * scale, vh = tile.video.videoHeight * scale;
          try { ctx.drawImage(tile.video, x + (tw - vw) / 2, y + (th - vh) / 2, vw, vh); } catch { /* stream transitioning */ }
        } else {
          ctx.fillStyle = '#475569'; ctx.beginPath(); ctx.arc(x + tw / 2, y + th / 2, Math.min(tw, th) / 6, 0, Math.PI * 2); ctx.fill();
          ctx.fillStyle = '#fff'; ctx.font = `${Math.max(20, th / 8)}px sans-serif`; ctx.textAlign = 'center';
          ctx.fillText(tile.name.slice(0, 1).toUpperCase(), x + tw / 2, y + th / 2 + th / 24);
        }
        ctx.fillStyle = '#000b'; ctx.fillRect(x + gap, y + th - labelHeight - gap, tw - gap * 2, labelHeight);
        ctx.fillStyle = '#fff'; ctx.font = `${Math.max(14, labelHeight * 0.5)}px sans-serif`; ctx.textAlign = 'left';
        ctx.fillText(tile.name, x + 14, y + th - gap - labelHeight * 0.3, tw - 28);
      });
    }
    animation = requestAnimationFrame(frame);
  }
  window.__vcStash = { stats, setParticipants(next) { participants = next; scan(); },
    stop() {
      stopped = true; cancelAnimationFrame(animation);
      if (document.fullscreenElement === canvas) void document.exitFullscreen();
      canvas.remove();
    } };
  scan(); animation = requestAnimationFrame(frame);
  return stats;
}
