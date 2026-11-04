'use strict';
const controlWall = {
    manifest: null,
    store: null,
    camera: null,
    client: crypto.randomUUID(),
    source: null,
    running: false,
    data: null,
    events: new Map(),
    arranging: false,
    drag: null,
    paint: 0,
    request: 0
};

async function openControlRoom() {
    const generation = ++controlWall.request;
    controlWall.manifest = await api('/api/control-room/manifest');
    if (ops.view !== 'wall' || generation !== controlWall.request) return;
    const stores = controlWall.manifest.stores;
    $('wall-empty').hidden = stores.length > 0;
    $('wall-content').hidden = !stores.length;
    $('wall-pause').disabled = !stores.length;
    $('wall-next').disabled = !stores.length;
    if (!stores.length) {
        $('wall-empty').textContent = t('Import HD continuous recordings to open the control room.');
        return;
    }
    controlWall.store = stores.find(s => s.id === controlWall.store?.id) ?? stores[0];
    $('wall-store').innerHTML = stores.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
    $('wall-store').value = controlWall.store.id;
    controlWall.camera = controlWall.store.cameras.find(c => c.id === controlWall.camera?.id) ?? controlWall.store.cameras[0];
    renderWallCameras();
    renderWallMap();
    await startControlRoom();
}
async function startControlRoom() {
    if (ops.view !== 'wall' || !controlWall.camera) return;
    await stopLive();
    controlWall.running = true;
    $('wall-next').disabled = false;
    $('wall-pause').textContent = t('Pause analysis');
    $('wall-status').textContent = t('Loading models…');
    $('wall-message').hidden = true;
    const clip = controlWall.camera.clips[0];
    showWallClip(clip);
    try {
        const data = await api('/api/control-room/start', json('POST', {
            store_id: controlWall.store.id,
            camera_id: controlWall.camera.id,
            client_id: controlWall.client
        }));
        if (ops.view !== 'wall' || !controlWall.running) {
            await pauseControlRoom();
            return;
        }
        controlWall.source?.close();
        const source = new EventSource('/api/control-room/stream');
        controlWall.source = source;
        source.onmessage = event => applyWallFrame(JSON.parse(event.data)).catch(e => showWallError(e.message));
        source.onerror = () => {
            $('wall-status').textContent = t('Reconnecting…');
        };
        await applyWallFrame(data);
    } catch (error) {
        controlWall.running = false;
        $('wall-next').disabled = true;
        $('wall-status').textContent = t('Analysis unavailable');
        $('wall-pause').textContent = t('Resume analysis');
        showWallError(error.message);
    }
}
async function pauseControlRoom() {
    if (!controlWall.running && !controlWall.source) return;
    controlWall.running = false;
    $('wall-next').disabled = true;
    controlWall.source?.close();
    controlWall.source = null;
    $('wall-video').pause();
    $('wall-pause').textContent = t('Resume analysis');
    $('wall-status').textContent = t('Paused');
    await api('/api/control-room/stop', json('POST', {
        client_id: controlWall.client
    }));
}

function showWallClip(clip) {
    if (!clip || $('wall-video').dataset.video === clip.id) return;
    const player = $('wall-video');
    player.dataset.video = clip.id;
    player.src = clip.media_url;
    player.muted = true;
    player.loop = true;
    player.hidden = false;
    $('wall-frame').hidden = true;
    $('wall-overlay').innerHTML = '';
    player.play().catch(() => {
        $('wall-message').textContent = t('Autoplay was blocked by this browser.');
        $('wall-message').hidden = false;
    });
}
async function applyWallFrame(data) {
    if (ops.view !== 'wall' || !controlWall.running) return;
    controlWall.data = data;
    if (data.video) showWallClip(data.video);
    $('wall-camera-name').textContent = data.config?.camera_name ?? controlWall.camera.name;
    $('wall-clip-position').textContent = data.config ? t('{n} / {m}', {
        n: data.clip_index + 1,
        m: data.config.clips.length
    }) : '';
    $('wall-loop').textContent = t('Loop {n}', {
        n: data.cycle ?? 1
    });
    const p = data.latest;
    $('wall-status').textContent = t(data.status === 'running' ? 'Live analysis' : data.status === 'starting' ? 'Loading models…' : data.status === 'error' ? 'Analysis unavailable' : 'Paused');
    if (data.error) showWallError(data.error);
    if (['paused', 'error', 'idle'].includes(data.status)) {
        controlWall.running = false;
        controlWall.source?.close();
        controlWall.source = null;
        $('wall-next').disabled = true;
        $('wall-pause').textContent = t('Resume analysis');
    }
    if (!p?.image) return;
    // Publish the image and its annotations together, never over a different source frame.
    const generation = ++controlWall.paint;
    const image = new Image();
    image.src = 'data:image/jpeg;base64,' + p.image;
    await image.decode();
    if (generation !== controlWall.paint || ops.view !== 'wall' || !controlWall.running) return;
    $('wall-frame').src = image.src;
    $('wall-frame').hidden = false;
    $('wall-video').hidden = true;
    $('wall-video').pause();
    $('wall-message').hidden = !data.error;
    $('wall-people').textContent = (p.tracks ?? []).filter(t => t.position_status === 'observed').length;
    $('wall-pickups').textContent = p.pickup_count ?? p.pickups?.length ?? 0;
    $('wall-time').textContent = clockTime((p.source_elapsed_seconds ?? 0) * 1000);
    for (const event of p.pickups ?? []) {
        controlWall.events.set(event.id, {
            ...event,
            video_id: data.video.id,
            session_id: data.session_id,
            camera_name: data.config.camera_name
        });
    }
    while (controlWall.events.size > 40) controlWall.events.delete(controlWall.events.keys().next().value);
    renderWallOverlay(p, data.geometry);
    renderWallSignals();
    $('wall-source-details').textContent = t('{n} HD recordings from {store}. Recorded clips loop with source-paced inference. The layout is schematic; camera positions are editable.', {
        n: controlWall.store.clip_count,
        store: controlWall.store.name
    });
}

function renderWallOverlay(p, geometry) {
    const [w, h] = p.frame_dims_px ?? [1280, 720], svg = $('wall-overlay');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const candidates = new Set((p.pickups ?? []).filter(e => p.media_ms - e.detected_media_ms < 2500).map(e => e.local_track_id));
    let html = '';
    for (const area of geometry?.shapes ?? []) {
        if (area.kind !== 'shelf') continue;
        html += `<polygon points="${area.points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="#ffffff55" stroke-width="1.2"/>`;
    }
    if ($('wall-paths').checked) {
        for (const points of Object.values(p.paths ?? {})) {
            const recent = points.filter(point => p.media_ms - point[1] <= 12000);
            if (recent.length > 1) html += `<polyline points="${recent.map(point=>point.slice(2).join(',')).join(' ')}" fill="none" stroke="#a4dbc4" stroke-width="2" opacity=".8"/>`;
        }
    }
    for (const track of p.tracks ?? []) {
        const [x, y, x2, y2] = track.box, flag = candidates.has(track.local_track_id), color = flag ? '#ffcf6b' : '#b3e3ce';
        html += `<g><rect x="${x}" y="${y}" width="${x2-x}" height="${y2-y}" rx="3" fill="none" stroke="${color}" stroke-width="${flag?4:2}" ${track.position_status==='observed'?'':'stroke-dasharray="6 5"'}/><rect x="${x}" y="${Math.max(0,y-22)}" width="${flag?164:64}" height="22" rx="3" fill="${color}"/><text x="${x+6}" y="${Math.max(16,y-6)}" fill="#14251f" font-size="13" font-family="sans-serif">${esc(flag?t('Pickup candidate'):shortTrack(track.local_track_id))}</text></g>`;
    }
    svg.innerHTML = html;
}

function renderWallSignals() {
    const rows = [...controlWall.events.values()].reverse();
    $('wall-event-count').textContent = rows.length;
    $('wall-events').innerHTML = rows.length ? rows.map(e => `<button class="wall-signal" data-wall-event="${esc(e.id)}"><span class="wall-signal-icon">↗</span><span><b>${esc(t('Pickup candidate'))}</b><small>${esc(e.camera_name)} · ${clockTime(e.detected_media_ms)} · ${Math.round(e.score*100)}%</small></span><span>›</span></button>`).join('') : `<div class="wall-signal-empty"><svg class="icon"><use href="#i-camera"/></svg><p>${esc(t('Watching for activity'))}</p></div>`;
    document.querySelectorAll('[data-wall-event]').forEach(button => button.onclick = task(async () => {
        const event = controlWall.events.get(button.dataset.wallEvent);
        await pauseControlRoom();
        await loadVideo(event.video_id);
        await showSavedLive(event.session_id);
        navigate('incidents');
        const key = 'live:' + event.session_id;
        const insight = await api('/api/ops/runs/' + encodeURIComponent(key) + '/insights');
        if (currentRun() !== key) return;
        ops.data = insight;
        ops.key = key;
        renderOperationViews();
        if (insight.incidents.some(e => e.id === event.id)) await openIncident(event.id);
    }));
}

function renderWallCameras() {
    $('wall-license').hidden = !controlWall.camera.clips.every(c=>c.dataset==='CARR');
    $('wall-camera-list').innerHTML = controlWall.store.cameras.map(c => `<button class="wall-camera-card ${c.id===controlWall.camera.id?'selected':''}" data-wall-camera="${esc(c.id)}"><img src="${esc(c.clips[0].thumbnail_url)}" alt=""><span><b>${esc(c.name)}</b><small>${esc(t('{n} recordings',{n:c.clips.length}))}</small></span></button>`).join('');
    document.querySelectorAll('[data-wall-camera]').forEach(b => b.onclick = task(() => selectWallCamera(b.dataset.wallCamera)));
}
async function selectWallCamera(id) {
    if (controlWall.arranging) return;
    controlWall.camera = controlWall.store.cameras.find(c => c.id === id);
    renderWallCameras();
    renderWallMap();
    await startControlRoom();
}

function renderWallMap() {
    const svg = $('wall-map');
    svg.classList.toggle('arranging', controlWall.arranging);
    let html = `<rect x="22" y="16" width="856" height="235" rx="15" fill="var(--sunken)" stroke="var(--line-strong)"/><path d="M90 250h110m500 0h110" stroke="var(--bg)" stroke-width="7"/>`;
    for (let i = 0; i < 6; i++) html += `<rect x="${225+i*83}" y="67" width="28" height="133" rx="5" fill="var(--line)"/>`;
    html += `<rect x="66" y="155" width="85" height="35" rx="5" fill="var(--line)"/>`;
    for (const camera of controlWall.store?.cameras ?? []) {
        const x = 22 + camera.layout.x / 100 * 856,
            y = 16 + camera.layout.y / 100 * 235,
            active = camera.id === controlWall.camera?.id;
        html += `<g data-map-camera="${esc(camera.id)}" tabindex="0" role="button" aria-label="${esc(camera.name)}" transform="translate(${x},${y})"><path d="M0 0 65 -28 65 28Z" transform="rotate(${camera.layout.heading})" fill="${active?'#d9b74933':'#8198a422'}"/><circle r="21" fill="${active?'var(--tag)':'var(--surface)'}" stroke="${active?'var(--tag-strong)':'var(--line-strong)'}" stroke-width="2"/><path d="M-9-6H4V6H-9ZM4-3 10-6V6L4 3" fill="none" stroke="${active?'#312a16':'var(--ink-2)'}" stroke-width="2"/><text y="40" text-anchor="middle" fill="var(--ink-2)" font-family="sans-serif" font-size="12">${esc(camera.name)}</text></g>`;
    }
    svg.innerHTML = html;
    svg.querySelectorAll('[data-map-camera]').forEach(marker => {
        marker.onclick = task(() => selectWallCamera(marker.dataset.mapCamera));
        marker.onkeydown = event => {
            if (['Enter', ' '].includes(event.key)) {
                event.preventDefault();
                marker.click();
            }
        };
        marker.onpointerdown = event => {
            if (!controlWall.arranging) return;
            controlWall.drag = marker.dataset.mapCamera;
            svg.setPointerCapture(event.pointerId);
            event.preventDefault();
        };
    });
}
$('wall-map').onpointermove = event => {
    if (!controlWall.drag) return;
    const svg = $('wall-map'),
        point = new DOMPoint(event.clientX, event.clientY).matrixTransform(svg.getScreenCTM().inverse());
    const c = controlWall.store.cameras.find(c => c.id === controlWall.drag);
    c.layout.x = Math.max(5, Math.min(95, (point.x - 22) / 856 * 100));
    c.layout.y = Math.max(5, Math.min(95, (point.y - 16) / 235 * 100));
    renderWallMap();
};
$('wall-map').onpointerup = $('wall-map').onpointercancel = task(async () => {
    const id = controlWall.drag;
    controlWall.drag = null;
    if (!id) return;
    const camera = controlWall.store.cameras.find(c => c.id === id);
    camera.layout = await api('/api/control-room/layout', json('PUT', {
        store_id: controlWall.store.id,
        camera_id: id,
        x: camera.layout.x,
        y: camera.layout.y,
        heading: camera.layout.heading,
        expected_revision: camera.layout.revision
    }));
    renderWallMap();
});
$('wall-arrange').onclick = () => {
    controlWall.arranging = !controlWall.arranging;
    $('wall-arrange').textContent = t(controlWall.arranging ? 'Done' : 'Arrange cameras');
    renderWallMap();
};
$('wall-store').onchange = task(async () => {
    await pauseControlRoom();
    controlWall.events.clear();
    controlWall.store = controlWall.manifest.stores.find(s => s.id === $('wall-store').value);
    controlWall.camera = controlWall.store.cameras[0];
    renderWallCameras();
    renderWallMap();
    renderWallSignals();
    await startControlRoom();
});
$('wall-next').onclick = task(() => api('/api/control-room/next', json('POST', {
    client_id: controlWall.client
})));
$('wall-pause').onclick = task(() => controlWall.running ? pauseControlRoom() : startControlRoom());
$('wall-fullscreen').onclick = () => $('wall-stage').requestFullscreen().catch(e => showWallError(e.message));
$('wall-paths').onchange = () => {
    const d = controlWall.data;
    if (d?.latest) renderWallOverlay(d.latest, d.geometry);
};

function showWallError(message) {
    $('wall-message').textContent = message;
    $('wall-message').hidden = false;
}
setInterval(() => {
    if (controlWall.running) api('/api/control-room/heartbeat', json('POST', {
        client_id: controlWall.client
    })).catch(e => showWallError(e.message));
}, 15000);
window.addEventListener('pagehide', () => {
    if (controlWall.running) navigator.sendBeacon('/api/control-room/stop', new Blob([JSON.stringify({
        client_id: controlWall.client
    })], {
        type: 'application/json'
    }));
});

let explanations = false;
try {
    explanations = localStorage.getItem('sentient.explanations') === 'yes';
} catch {}

function applyQuietMode() {
    document.body.classList.toggle('quiet-ui', !explanations);
    $('copy-toggle').setAttribute('aria-pressed', String(explanations));
}
$('copy-toggle').onclick = () => {
    explanations = !explanations;
    try {
        localStorage.setItem('sentient.explanations', explanations ? 'yes' : 'no');
    } catch {}
    applyQuietMode();
};
applyQuietMode();
renderWallSignals();