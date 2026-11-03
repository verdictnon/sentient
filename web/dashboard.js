'use strict';
// Dashboard views derive exclusively from the selected run's observations.
let dashboardFrame = 0,
    chartKey = '',
    heatKey = '';
const icon = name => `<svg class="icon"><use href="#i-${name}"/></svg>`;

function splitSegments(points) {
    const result = [];
    for (const p of points) {
        let segment = result.at(-1);
        if (!segment || p[1] - segment.at(-1)[1] > 1500) {
            segment = [];
            result.push(segment)
        }
        segment.push(p)
    }
    return result;
}

function allPoints() {
    return state.paths.flatMap(t => t.segments ?? [t.points ?? []]).flat()
}

function updateDashboard() {
    if (dashboardFrame) return;
    dashboardFrame = requestAnimationFrame(() => {
        dashboardFrame = 0;
        const ready = !!state.stats || state.tracking || state.lastLive || state.live;
        const points = allPoints(),
            areas = state.stats?.areas ?? [],
            visits = areas.reduce((n, a) => n + a.visits, 0);
        const dwell = areas.reduce((n, a) => n + (a.observed_dwell_seconds ?? 0), 0);
        $('metric-tracks').textContent = ready ? (state.seenTrackCount ?? state.paths.length) : '—';
        $('metric-pickups').textContent = ready ? (state.opsRunKey?.startsWith('track:') && !state.lastLive ? '—' : state.events.length) : '—';
        $('metric-dwell').innerHTML = ready && state.stats?.physical_time_supported && visits ? `${(dwell/visits).toFixed(1)}<small>${esc(t('s'))}</small>` : '—';
        $('metric-areas').textContent = state.geometry ? state.geometry.shapes.filter(s => s.kind !== 'line').length : '—';
        document.body.classList.toggle('running', !!(state.live || state.offline));
        $('run-history').disabled = !!state.offline || !!state.live || !state.histories.some(s => ['completed', 'stopped'].includes(s.status) && s.processed_frames > 0);
        $('stage-mode').textContent = t(state.live ? (state.video?.source_profile?.simulated ? 'Simulated live run' : 'Analyzing recording') : state.offline ? 'Full-video analysis' : 'Recording playback');
        $('show-heatmap').disabled = !points.length || state.tab === 'geometry';
        $('insight-scope').textContent = t(state.live ? 'Current live run' : ready ? 'Selected saved run' : 'Recording only');
        const locked = !!(state.live || state.offline) || !state.video;
        for (const id of ['draw', 'finish', 'undo', 'cancel-draw', 'save-geometry', 'reload-geometry', 'shape-name', 'delete-shape', 'reverse', 'geometry-import', 'shape-kind']) $(id).disabled = locked;
        const key = [state.video?.id, state.live, state.tracking, state.lastLive, points.length, points.at(-1)?.[1], JSON.stringify(areas)].join('|');
        if (key !== chartKey) {
            chartKey = key;
            drawTraffic(points);
            drawZones(areas);
            heatKey = ''
        }
        drawHeatmap(points);
        if (typeof syncOperations === 'function') syncOperations();
    });
}

function drawTraffic(points) {
    if (!points.length) {
        $('traffic-chart').innerHTML = `<p class="empty-chart">${esc(t('Run analysis to explore observed track presence.'))}</p>`;
        return
    }
    const duration = Math.max(1, (state.live ? state.ms : (state.video.declared_duration_ms ?? state.video.last_media_time_ms)) / 1000),
        bins = Array.from({
            length: 12
        }, () => new Set());
    for (const track of state.paths)
        for (const segment of track.segments ?? [track.points ?? []])
            for (const p of segment) bins[Math.max(0, Math.min(11, Math.floor(p[1] / 1000 / duration * 12)))].add(track.local_track_id);
    const max = Math.max(1, ...bins.map(b => b.size)),
        W = 480,
        H = 150,
        left = 24,
        right = 8,
        top = 12,
        bottom = 24,
        plotH = H - top - bottom,
        step = (W - left - right) / 12,
        bar = Math.min(24, step * .68);
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(t('Distinct observed tracks per interval in the recording'))}">`;
    for (let i = 0; i <= 2; i++) {
        const y = top + plotH * i / 2,
            value = max * (1 - i / 2);
        svg += `<line class="grid-line" x1="${left}" y1="${y}" x2="${W-right}" y2="${y}"/><text class="axis-text" x="${left-8}" y="${y+3}" text-anchor="end">${Number.isInteger(value)?value:value.toFixed(1)}</text>`
    }
    bins.forEach((b, i) => {
        const h = b.size / max * plotH,
            x = left + i * step + (step - bar) / 2;
        svg += `<rect class="column" x="${x}" y="${top+plotH-h}" width="${bar}" height="${Math.max(0,h)}" rx="3"><title>${(duration*i/12).toFixed(1)}–${(duration*(i+1)/12).toFixed(1)} ${esc(t('s'))}: ${b.size}</title></rect>`
    });
    for (let i = 0; i <= 4; i++) svg += `<text class="axis-text" x="${left+(W-left-right)*i/4}" y="${H-5}" text-anchor="${i===0?'start':i===4?'end':'middle'}">${(duration*i/4).toFixed(0)}${esc(t('s'))}</text>`;
    $('traffic-chart').innerHTML = svg + '</svg>';
    $('traffic-caption').textContent = t(state.live ? 'Recent visible track samples, source time in seconds' : 'Source time in seconds, distinct camera-local tracks per interval');
}

function drawZones(areas) {
    if (!areas.length) {
        $('zone-chart').innerHTML = `<p class="empty-chart">${esc(t('Map floor areas, then start analysis.'))}</p>`;
        return
    }
    const rows = [...areas].sort((a, b) => b.unique_tracks - a.unique_tracks).slice(0, 5),
        max = Math.max(1, ...rows.map(a => a.unique_tracks));
    $('zone-chart').innerHTML = rows.map(a => `<div class="zone-row"><span class="zone-name" title="${esc(t(a.name))}">${esc(t(a.name))}</span><span class="zone-track"><span class="zone-fill" style="width:${a.unique_tracks/max*100}%"></span></span><span class="zone-value">${a.unique_tracks}</span></div>`).join('');
}

function drawHeatmap(points) {
    const canvas = $('heatmap');
    canvas.hidden = !$('show-heatmap').checked || state.tab === 'geometry' || !points.length;
    if (canvas.hidden || !state.video) return;
    const visible = state.live ? points : points.filter(p => p[1] <= state.ms);
    const key = [state.video.id, visible.length, visible.at(-1)?.[1]].join('|');
    if (key === heatKey) return;
    heatKey = key;
    const [w, h] = state.video.frame_dims_px;
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d'),
        mask = document.createElement('canvas');
    mask.width = w;
    mask.height = h;
    const m = mask.getContext('2d'),
        radius = w * .035;
    for (const p of visible) {
        const gradient = m.createRadialGradient(p[2], p[3], 0, p[2], p[3], radius);
        gradient.addColorStop(0, 'rgba(0,0,0,.12)');
        gradient.addColorStop(1, 'rgba(0,0,0,0)');
        m.fillStyle = gradient;
        m.fillRect(p[2] - radius, p[3] - radius, radius * 2, radius * 2)
    }
    const data = m.getImageData(0, 0, w, h);
    for (let i = 0; i < data.data.length; i += 4) {
        const a = data.data[i + 3] / 255;
        if (!a) continue;
        const [red, green, blue] = heatColour(Math.max(.07, a));
        data.data[i] = red;
        data.data[i + 1] = green;
        data.data[i + 2] = blue;
        data.data[i + 3] = Math.round(Math.min(.8, a) * 255)
    }
    ctx.putImageData(data, 0, 0);
}
$('show-heatmap').onchange = () => {
    heatKey = '';
    updateDashboard()
};

function renderHistory() {
    const rows = state.histories.filter(s => ['completed', 'stopped'].includes(s.status) && s.processed_frames > 0);
    $('run-history').innerHTML = `<option value="">${esc(t('Choose a saved live run…'))}</option>` + rows.map((s, i) => `<option value="${s.id}">${esc(t('{when}, {n} frames',{when:new Date(s.saved_at*1000).toLocaleString(locale(),{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}),n:s.processed_frames}))}</option>`).join('');
    if (state.lastLive) $('run-history').value = state.lastLive;
    $('run-history').disabled = !!state.offline || !!state.live || !rows.length;
}
async function refreshHistory() {
    const generation = state.generation,
        rows = await api(`/api/live/videos/${state.video.id}`);
    if (generation !== state.generation) return;
    state.histories = rows;
    renderHistory()
}
async function showSavedLive(key, generation = state.generation) {
    if (state.offline) throw Error(t('Wait for analysis or cancel it before opening another run.'));
    await stopLive();
    const [session, stats, response] = await Promise.all([api(`/api/live/sessions/${key}`), api(`/api/live/sessions/${key}/shelves`), fetch(`/api/live/sessions/${key}/observations`)]);
    if (!response.ok) throw Error(t('This run has no saved observations.'));
    const rows = (await response.text()).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
    if (generation !== state.generation) return;
    const p = session.latest,
        paths = new Map();
    for (const row of rows)
        for (const t of row.tracks ?? [])
            if (t.position_status === 'observed') {
                if (!paths.has(t.local_track_id)) paths.set(t.local_track_id, []);
                const b = t.box;
                paths.get(t.local_track_id).push([row.frame_index, row.media_ms, (b[0] + b[2]) / 2, b[3]])
            }
    state.lastLive = key;
    state.opsRunKey = 'live:' + key;
    if (typeof rememberRun === 'function') rememberRun(state.opsRunKey);
    state.tracking = null;
    state.analysisId = null;
    state.replayFrames = rows;
    state.paths = Array.from(paths, ([local_track_id, points]) => ({
        local_track_id,
        segments: splitSegments(points)
    }));
    state.seenTrackCount = p.unique_tracks ?? paths.size;
    state.events = p.pickups ?? [];
    state.stats = stats;
    state.tracks = [];
    state.ms = 0;
    $('player').pause();
    $('player').currentTime = 0;
    $('live-frame').hidden = true;
    $('player').hidden = false;
    $('threshold').textContent = p.threshold?.toFixed(2) ?? '0.97';
    $('run-status').textContent = t('Saved live results');
    $('performance').textContent = t('{n} sampled frames, {m} windows scored', {
        n: p.processed_frames,
        m: p.scored_windows
    });
    $('run-details').textContent = JSON.stringify({
        session: key,
        status: p.status,
        model: p.model_id,
        processed_frames: p.processed_frames,
        scored_windows: p.scored_windows,
        geometry: stats.geometry_id,
        execution: p.execution
    }, null, 2);
    $('export').href = `/api/live/sessions/${key}/export`;
    $('export').hidden = false;
    renderEvents();
    renderStats();
    renderHistory();
    await offlineFrame(0);
    notice(t('Saved live run restored. Play the recording or inspect a candidate to explore the results.'));
}
async function refreshAreaStats() {
    if (state.tracking) state.stats = await api(`/api/shelves/jobs/${state.tracking}`);
    else if (state.lastLive) state.stats = await api(`/api/live/sessions/${state.lastLive}/shelves`);
    renderStats()
}
$('run-history').onchange = guard(async () => {
    const key = $('run-history').value;
    if (!key) return;
    if (state.dirty) throw Error(t('Save or discard geometry edits before opening a saved run.'));
    await showSavedLive(key)
});
async function seekRecording(ms) {
    await playback();
    if (state.tab === 'geometry') tab('results');
    const player = $('player');
    player.pause();
    const target = Math.max(0, Math.min(ms / 1000, Number.isFinite(player.duration) ? player.duration : ms / 1000));
    if (Math.abs(player.currentTime - target) > .005) await new Promise(resolve => {
        const done = () => {
            clearTimeout(timer);
            player.removeEventListener('seeked', done);
            resolve()
        };
        const timer = setTimeout(done, 1500);
        player.addEventListener('seeked', done);
        player.currentTime = target
    });
    await offlineFrame(player.currentTime * 1000);
    if ($('stage').getBoundingClientRect().top < 0) $('stage').scrollIntoView({
        behavior: 'smooth',
        block: 'center'
    });
}

function closeMenu() {
    $('sidebar').classList.remove('open');
    $('menu-toggle').setAttribute('aria-expanded', 'false')
}
$('menu-toggle').onclick = () => {
    const open = $('sidebar').classList.toggle('open');
    $('menu-toggle').setAttribute('aria-expanded', String(open))
};
document.addEventListener('click', e => {
    if (innerWidth <= 760 && !$('sidebar').contains(e.target) && !$('menu-toggle').contains(e.target)) closeMenu()
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeMenu()
});

document.querySelectorAll('[data-nav]').forEach(b => b.onclick = () => navigate(b.dataset.nav));
$('configure-areas').onclick = () => navigate('geometry');
$('fullscreen').onclick = guard(async () => {
    if (document.fullscreenElement) await document.exitFullscreen();
    else await $('stage').requestFullscreen()
});
$('player').addEventListener('loadeddata', () => {
    if (!state.live) offlineFrame($('player').currentTime * 1000).catch(e => notice(e.message, true))
});
$('player').addEventListener('error', () => {
    if ($('player').getAttribute('src')) notice(t('This recording could not be played. Check that its source file is available.'), true)
});
$('import-dialog').addEventListener('click', e => {
    if (e.target === $('import-dialog')) {
        const r = $('import-dialog').getBoundingClientRect();
        if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) $('import-dialog').close()
    }
});
updateDashboard();
$('export').addEventListener('click', guard(async event => {
    event.preventDefault();
    if (state.dirty) throw Error(t('Save or discard geometry edits before exporting.'));
    const generation = state.generation,
        source = await api($('export').getAttribute('href'));
    if (generation !== state.generation) throw Error(t('Recording changed. Export the selected run again.'));
    const report = {
        ...(Array.isArray(source) ? {
            evidence: source
        } : source),
        report_format: 'sentient.demo-report/1',
        recording: {
            id: state.video.id,
            name: state.video.display_name,
            camera_id: state.video.camera_id,
            source_profile: state.video.source_profile
        },
        observed_tracks: state.seenTrackCount,
        pickup_candidates: state.events,
        area_activity: state.stats,
        area_geometry: {
            id: state.geometryId,
            geometry: state.geometry
        }
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {
        type: 'application/json'
    }));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'sentient-analysis.json';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}));