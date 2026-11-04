'use strict';
const $ = id => document.getElementById(id),
    esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    } [c]));
const state = {
    videos: [],
    demo: [],
    video: null,
    geometry: null,
    geometryId: null,
    selected: null,
    tool: null,
    points: [],
    drag: null,
    dirty: false,
    tab: 'results',
    live: null,
    source: null,
    offline: null,
    tracking: null,
    paths: [],
    tracks: [],
    events: [],
    stats: null,
    frame: 0,
    ms: 0,
    generation: 0,
    overlayRequest: 0,
    lastLive: null,
    replayFrames: null,
    seenTrackCount: null,
    histories: [],
    analysisId: null,
    opsRunKey: null,
    focusTrack: null,
    trackingOnly: false
};
async function api(url, options) {
    const r = await fetch(url, options);
    if (!r.ok) {
        let e;
        try {
            e = await r.json()
        } catch {
            e = {
                detail: r.statusText
            }
        }
        throw Error(typeof e.detail === 'string' ? e.detail : JSON.stringify(e.detail))
    }
    return r.json()
}
const json = (method, body) => ({
    method,
    headers: {
        'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
});

function notice(text, error = false) {
    $('notice').textContent = text;
    $('notice').classList.toggle('error', error)
}

function guard(fn) {
    return async (...args) => {
        try {
            await fn(...args)
        } catch (e) {
            notice(e.message, true)
        }
    }
}

let toastTimer;

function toast(message, error = false) {
    clearTimeout(toastTimer);
    $('toast').textContent = message;
    $('toast').classList.toggle('error', error);
    $('toast').hidden = false;
    toastTimer = setTimeout(() => $('toast').hidden = true, 5500)
}

function task(fn) {
    return async (...args) => {
        try {
            await fn(...args)
        } catch (e) {
            toast(e.message, true)
        }
    }
}

function tab(name) {
    state.tab = name;
    document.body.dataset.tab = name;
    for (const n of ['results', 'geometry', 'help']) {
        $(n + '-panel').hidden = n !== name;
        $(n + '-tab').classList.toggle('selected', n === name);
        $(n + '-tab').setAttribute('aria-selected', String(n === name))
    }
    $('overlay').classList.toggle('editing', name === 'geometry' && !state.live && !state.offline);
    if (name === 'geometry') $('player').pause();
    render();
    updateDashboard()
}
for (const n of ['results', 'geometry', 'help']) $(n + '-tab').onclick = () => tab(n);

function title(v) {
    const label = typeof ops === 'object' ? ops.workspace?.recordings.find(row => row.id === v.id)?.context.label : null;
    if (label) return label;
    return t(v.display_name.replace(/^CARR_/, '').replace(/_720p\.mp4$/, '').replace(/_video\.mp4$/, '').replaceAll('_', ' ').replace(/\bdemo\b/gi, '').replace(/\s+/g, ' ').trim())
}

function library() {
    const query = $('library-search').value.trim().toLowerCase();
    const rows = ($('library-mode').value === 'demo' ? state.videos.filter(v => state.demo.includes(v.id) || v.source_profile?.simulated) : state.videos).filter(v => (title(v) + ' ' + v.camera_id).toLowerCase().includes(query));
    $('library-count').textContent = rows.length;
    document.body.classList.toggle('single-recording', state.videos.length < 2);
    $('library').innerHTML = rows.map(v => `<button class="asset ${v.id===state.video?.id?'selected':''}" data-video="${v.id}" aria-pressed="${v.id===state.video?.id}" title="${esc(title(v))}"><img class="asset-thumb" src="/api/videos/${v.id}/frame/0" loading="lazy" alt=""><span class="asset-copy"><b>${esc(title(v).replace(/^V[0-9]+ /,''))}</b><small>${((v.declared_duration_ms??v.indexed_span_ms)/1000).toFixed(0)} ${esc(t('s'))}, ${esc(v.camera_id)}</small></span></button>`).join('') || `<p class="field-note">${esc(t('No recordings match your search.'))}</p>`;
    document.querySelectorAll('[data-video]').forEach(b => b.onclick = guard(async () => {
        await loadVideo(b.dataset.video);
        navigate('overview');
        closeMenu()
    }));
}
$('library-search').oninput = library;
$('library-mode').onchange = library;
async function stopLive() {
    if (!state.live) return;
    const key = state.live;
    await api(`/api/live/sessions/${key}/stop`, {
        method: 'POST'
    });
    $('run-status').textContent = t('Stopping…');
    for (let i = 0; i < 60; i++) {
        const s = await api(`/api/live/sessions/${key}`);
        if (['stopped', 'completed', 'error'].includes(s.status)) {
            if (state.live === key) finishLive(s.latest);
            return
        }
        await new Promise(r => setTimeout(r, 100))
    }
    throw Error(t('The worker is still stopping. Wait for the current model operation to finish.'))
}
async function loadVideo(id) {
    if (state.dirty && !confirm(t('Discard unsaved geometry edits?'))) return;
    await stopLive();
    if (state.offline) throw Error(t('Wait for full-video analysis or cancel it before changing videos.'));
    const linkedRun = new URLSearchParams(location.search).get('run');
    const generation = ++state.generation;
    ++state.overlayRequest;
    state.lastLive = null;
    state.replayFrames = null;
    state.tracking = null;
    state.seenTrackCount = null;
    state.analysisId = null;
    state.opsRunKey = null;
    state.focusTrack = null;
    state.histories = [];
    state.paths = [];
    state.tracks = [];
    state.events = [];
    state.stats = null;
    state.frame = 0;
    state.ms = 0;
    state.dirty = false;
    state.selected = null;
    state.points = [];
    state.tool = null;
    $('player').pause();
    $('live-frame').hidden = true;
    $('player').hidden = false;
    $('export').hidden = true;
    $('pickup-badge').hidden = true;
    $('performance').textContent = '';
    $('run-status').textContent = t('Loading…');
    const [video, geometry, histories] = await Promise.all([api('/api/videos/' + id), api(`/api/videos/${id}/geometry`), api(`/api/live/videos/${id}`)]);
    if (generation !== state.generation) return;
    state.video = video;
    state.histories = histories;
    applyGeometry(geometry);
    $('stage-empty').hidden = true;
    $('camera-badge').textContent = video.frame_dims_px[1] + 'p';
    $('player').poster = `/api/videos/${id}/frame/0`;
    $('video-name').textContent = title(video);
    $('video-info').textContent = [video.camera_id, video.frame_dims_px.join(' × '), t(video.source_profile.simulated ? 'Simulated' : video.source_profile.dataset === 'recorded_video' ? 'Recorded video' : video.source_profile.dataset), `${((video.declared_duration_ms??video.indexed_span_ms)/1000).toFixed(1)} ${t('s')}`, t(video.source_profile.source_kind === 'sparse_clip' ? 'Selected research frames' : 'Continuous recording')].join(', ');
    $('start-live-label').textContent = t(video.source_profile.simulated ? 'Start simulated live run' : 'Start live analysis');
    $('stage').style.aspectRatio = video.frame_dims_px.join('/');
    $('overlay').setAttribute('viewBox', '0 0 ' + video.frame_dims_px.join(' '));
    $('player').src = `/api/videos/${id}/content`;
    $('start-live').disabled = video.source_profile.source_kind === 'sparse_clip';
    $('playback').disabled = false;
    $('analyze').disabled = false;
    $('run-status').textContent = t('Ready');
    $('frame-time').textContent = '0.00 ' + t('s');
    history.replaceState(null, '', '/?' + new URLSearchParams({
        video: id,
        ...(new URLSearchParams(location.search).get('view') ? {
            view: new URLSearchParams(location.search).get('view')
        } : {})
    }));
    library();
    renderEvents();
    renderStats();
    render();
    const jobs = await api(`/api/action-analysis/videos/${id}`);
    if (generation !== state.generation) return;
    const completed = jobs.find(j => j.status === 'completed');
    const active = histories.find(j => !['completed', 'stopped', 'error'].includes(j.status));
    const offline = jobs.find(j => ['queued', 'running', 'cancelling'].includes(j.status));
    if (active) {
        connectLive(active);
        return;
    }
    if (offline) {
        watchOffline(offline, generation).catch(e => notice(e.message, true));
        return;
    }
    if (typeof restoreLinkedRun === 'function' && await restoreLinkedRun(linkedRun, id, generation, jobs, histories)) return;
    if (completed) {
        await showOffline(completed, generation);
        $('run-status').textContent = t('Saved full-video results');
    } else {
        // A finished run reopens ahead of a newer one that someone stopped halfway.
        const usable = histories.filter(j => ['completed', 'stopped'].includes(j.status) && j.processed_frames > 0),
            saved = usable.find(j => j.status === 'completed') ?? usable[0];
        if (saved) {
            await showSavedLive(saved.id, generation);
            return;
        }
    }
    renderHistory();
    updateDashboard();
    notice(t(video.source_profile.source_kind === 'sparse_clip' ? 'This clip has selected research frames. Use full-video analysis; live cadence and dwell are unavailable.' : 'Start live analysis to run inference during playback. Geometry can be adjusted before starting.'));
}

function applyGeometry(saved) {
    state.geometry = saved.geometry;
    state.geometryId = saved.id;
    state.selected = state.geometry.shapes[0]?.id ?? null;
    state.dirty = false;
    $('geometry-export').href = `/api/videos/${state.video?.id??saved.video_id}/geometry/export`;
    $('geometry-revision').textContent = t('Saved revision {n}', {
        n: saved.revision
    });
    renderShapes()
}

function markDirty() {
    state.dirty = true;
    $('geometry-revision').textContent = t('Unsaved edits');
    render()
}

function renderShapes() {
    if (!state.geometry) return;
    $('shape-list').innerHTML = state.geometry.shapes.map(s => `<button class="shape ${s.id===state.selected?'selected':''}" data-shape="${esc(s.id)}">${esc(t(s.name))}<small>${esc(kindLabel(s.kind))}</small></button>`).join('') || `<p class="muted">${esc(t('No areas labelled. Add only what is visible in this camera.'))}</p>`;
    document.querySelectorAll('[data-shape]').forEach(b => b.onclick = () => {
        state.selected = b.dataset.shape;
        renderShapes();
        render()
    });
    const s = state.geometry.shapes.find(s => s.id === state.selected);
    $('shape-editor').hidden = !s;
    if (s) {
        $('shape-name').value = s.name;
        $('shelf-product-field').hidden = s.kind !== 'shelf';
        $('shelf-product').value = s.product_type ?? '';
        $('reverse').hidden = s.kind !== 'line';
        $('line-meaning-field').hidden = s.kind !== 'line';
        $('line-meaning').value = s.positive_means ?? ''
    }
    $('finish').hidden = !state.tool || state.tool === 'line';
    $('undo').hidden = !state.tool;
    $('cancel-draw').hidden = !state.tool;
}

const AREA_COLOURS = {
    shelf: '#6fb2ff',
    zone: '#6fb2ff',
    checkout: '#4fe0a8',
    queue: '#c6a4ff',
    exit: '#ff9a7a',
    line: '#ffffff'
};

function render() {
    if (!state.video) return;
    const [w, h] = state.video.frame_dims_px, unit = w / 1000;
    let out = '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10" fill="#ffffff"/></marker></defs>';
    if ($('show-geometry').checked || state.tab === 'geometry')
        for (const s of state.geometry.shapes) {
            const color = AREA_COLOURS[s.kind] ?? AREA_COLOURS.zone,
                points = s.points.map(p => p.join(',')).join(' ');
            out += s.kind === 'line' ? `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${2*unit}"/>` : `<polygon points="${points}" fill="${color}1f" stroke="${color}" stroke-width="${1.5*unit}"/>`;
            if (s.kind === 'line') {
                const [a, b] = s.points, dx = b[0] - a[0], dy = b[1] - a[1], l = Math.hypot(dx, dy) || 1, m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
                out += `<line x1="${m[0]}" y1="${m[1]}" x2="${m[0]-dy/l*w*.04}" y2="${m[1]+dx/l*w*.04}" stroke="${color}" stroke-width="${2*unit}" marker-end="url(#arrow)"/>`
            }
            out += `<text x="${s.points[0][0]+5*unit}" y="${Math.max(16*unit,s.points[0][1]-6*unit)}" fill="${color}" stroke="#0c1424" stroke-width="${2.4*unit}" paint-order="stroke" font-size="${12.5*unit}" font-weight="600">${esc(t(s.name))}</text>`;
            if (state.tab === 'geometry' && s.id === state.selected && !state.tool) s.points.forEach((p, i) => out += `<circle data-vertex="${i}" cx="${p[0]}" cy="${p[1]}" r="${6*unit}" fill="white" stroke="${color}" stroke-width="${2*unit}"/>`);
        }
    if ($('show-paths').checked)
        for (const track of state.paths) {
            const segments = track.segments ?? [track.points];
            for (const segment of segments) {
                const points = segment.filter(p => p[1] <= state.ms && p[1] >= state.ms - Number($('trail-window').value));
                if (points.length > 1) out += `<polyline points="${points.map(p=>p.slice(2).join(',')).join(' ')}" fill="none" stroke="${track.local_track_id===state.focusTrack?'#ffffff':'#ffd21f'}" stroke-width="${(track.local_track_id===state.focusTrack?3:1.6)*unit}" stroke-linejoin="round" opacity="${state.focusTrack&&track.local_track_id!==state.focusTrack?'.2':'.8'}"/>`
            }
        }
    const alerted = new Set(state.events.filter(e => state.live ? state.ms - e.end_media_ms <= 1400 && state.ms >= e.detected_media_ms : e.start_media_ms <= state.ms && state.ms <= e.end_media_ms).map(e => e.local_track_id));
    if ($('show-people').checked)
        for (const t_ of state.tracks) {
            const b = t_.box ?? t_.bbox_xyxy_px,
                observed = t_.position_status === 'observed',
                selected = t_.local_track_id === state.focusTrack,
                flagged = alerted.has(t_.local_track_id),
                color = flagged ? '#ff5a3c' : selected ? '#ffffff' : observed ? '#ffd21f' : '#c7cfdb';
            out += `<g data-track-id="${esc(t_.local_track_id)}" opacity="${state.focusTrack&&!selected?'.35':'1'}" style="pointer-events:visiblePainted;cursor:pointer"><rect x="${b[0]}" y="${b[1]}" width="${b[2]-b[0]}" height="${b[3]-b[1]}" fill="${selected?'#ffffff14':'#ffffff01'}" stroke="${color}" stroke-width="${(selected||flagged?2.6:1.6)*unit}" ${observed?'':'stroke-dasharray="8 6"'}/><text x="${b[0]}" y="${Math.max(16*unit,b[1]-5*unit)}" font-size="${12*unit}" font-weight="600" fill="${color}" stroke="#0c1424" stroke-width="${2.4*unit}" paint-order="stroke">${esc(t_.local_track_id.split(':').at(-1))}</text>`;
            if ($('show-footpoints').checked && observed) out += `<circle cx="${(b[0]+b[2])/2}" cy="${b[3]}" r="${3*unit}" fill="${color}" stroke="#0c1424" stroke-width="${unit}"/>`;
            out += '</g>';
        }
    if (state.points.length) {
        out += `<polyline points="${state.points.map(p=>p.join(',')).join(' ')}" fill="none" stroke="white" stroke-width="${2*unit}" stroke-dasharray="6 4"/>`;
        for (const p of state.points) out += `<circle cx="${p[0]}" cy="${p[1]}" r="${4*unit}" fill="white"/>`
    }
    $('overlay').innerHTML = out;
    updateDashboard();
    const active = state.events.filter(e => state.live ? state.ms - e.end_media_ms <= 1400 && state.ms >= e.detected_media_ms : e.start_media_ms <= state.ms && state.ms <= e.end_media_ms);
    $('pickup-badge').hidden = !active.length;
    if (active.length) $('pickup-badge').textContent = t('Pickup candidate, track {id}, {score}%', {
        id: active[0].local_track_id.split(':').at(-1),
        score: (active[0].score * 100).toFixed(1)
    });
    if (typeof movePlayhead === 'function') movePlayhead();
}
for (const id of ['show-people', 'show-paths', 'show-geometry']) $(id).onchange = render;

function renderStats() {
    const stats = state.stats,
        rows = stats?.areas ?? [];
    $('shelves').innerHTML = rows.length ? rows.map(s => `<tr><td>${esc(t(s.name))}${t(s.name)===kindLabel(s.kind)?'':`<small class="muted">${esc(kindLabel(s.kind))}</small>`}</td><td>${s.unique_tracks}</td><td>${s.visits}</td><td>${s.observed_dwell_seconds===null?'—':s.observed_dwell_seconds.toFixed(1)+' '+esc(t('s'))}</td><td>${s.occupancy}</td><td>${s.pickup_alerts}</td></tr>`).join('') : `<tr><td colspan="6">${esc(t('Draw and save shelf areas, then run analysis.'))}</td></tr>`;
    $('shelf-csv').disabled = !rows.length;
    $('crossings').textContent = (stats?.crossings ?? []).map(s => t('{name}: {a} positive, {b} negative crossings', {
        name: t(s.name),
        a: s.positive,
        b: s.negative
    })).join('. ');
    $('shelf-note').textContent = t(stats ? 'Ranked by local tracks and observed dwell, this run only.' : 'Map floor areas, then start analysis to see activity.');
    updateDashboard();
}

function outcomeLabel(status) {
    return t({
        left_without_checkout: 'Left without a till',
        lost_before_checkout: 'Track lost before a till',
        still_in_view: 'Still in view',
        reached_checkout: 'Reached a till',
        checkout_not_mapped: 'No till on this camera',
        unavailable: 'Cross-check unavailable'
    } [status] ?? status);
}

function eventOutcome(event) {
    return event.crosscheck?.status ?? (typeof ops === 'object' ? ops.data?.incidents.find(e => e.id === event.id)?.crosscheck?.status : null);
}

function renderEvents() {
    const events = state.events,
        trackingOnly = state.opsRunKey?.startsWith('track:') && !state.lastLive;
    $('pickup-count').textContent = events.length;
    $('pickup-events').innerHTML = events.length ? events.slice().reverse().map((e, i) => {
        const status = eventOutcome(e);
        return `<button class="event-card ${status==='left_without_checkout'?'flagged':''}" data-pickup="${events.length-1-i}"><span class="event-top"><b>${esc(t('Pickup candidate'))}</b><span class="event-score">${(e.score*100).toFixed(1)}%</span></span><small>${esc(t('Track {id}, {a}–{b} s',{id:e.local_track_id.split(':').at(-1),a:(e.start_media_ms/1000).toFixed(1),b:(e.end_media_ms/1000).toFixed(1)}))}</small>${status?`<span class="outcome ${status}">${esc(outcomeLabel(status))}</span>`:''}</button>`
    }).join('') : `<div class="feed-empty"><b>${esc(t(trackingOnly?'Tracking-only run':state.stats?'No pickup candidates':'Nothing yet'))}</b><p>${esc(t(trackingOnly?'Pickup scoring was not run. Use Analyze full video to score activity.':state.stats?'No scores crossed the threshold in this run.':'Start analysis to see predicted activity here.'))}</p></div>`;
    document.querySelectorAll('[data-pickup]').forEach(b => b.onclick = guard(async () => {
        if (state.live) throw Error(t('Stop live analysis to inspect an earlier candidate.'));
        const ms = state.events[Number(b.dataset.pickup)].start_media_ms;
        await seekRecording(ms);
        notice(t('Inspecting a model prediction. Confidence does not establish a purchase.'));
    }));
    updateDashboard();
}

function finishLive(payload) {
    if (!state.live) return;
    state.lastLive = state.live;
    state.source?.close();
    state.source = null;
    state.live = null;
    $('start-live').disabled = state.video?.source_profile.source_kind === 'sparse_clip';
    $('stop-live').hidden = true;
    $('analyze').disabled = false;
    $('overlay').classList.toggle('editing', state.tab === 'geometry' && !state.offline);
    $('run-status').textContent = t(payload.status === 'completed' ? 'Live analysis complete' : payload.status === 'error' ? 'Analysis failed' : 'Stopped');
    if (payload.error) notice(payload.error, true);
    else notice(t('Results saved. Choose Playback to inspect candidates and paths.'));
    refreshHistory().catch(e => notice(e.message, true));
    updateDashboard();
}
$('start-live').onclick = guard(async () => {
    if (state.dirty || state.tool) throw Error(t('Finish drawing and save geometry before starting analysis.'));
    if (state.offline) throw Error(t('Wait for full-video analysis to finish.'));
    $('player').pause();
    state.tracks = [];
    state.paths = [];
    state.events = [];
    state.stats = null;
    state.tracking = null;
    state.analysisId = null;
    state.seenTrackCount = 0;
    renderEvents();
    renderStats();
    render();
    $('start-live').disabled = true;
    $('analyze').disabled = true;
    let s;
    try {
        s = await api(`/api/live/videos/${state.video.id}`, {
            method: 'POST'
        })
    } catch (e) {
        $('start-live').disabled = false;
        $('analyze').disabled = false;
        throw e
    }
    connectLive(s);
});

function connectLive(s) {
    state.lastLive = null;
    state.replayFrames = null;
    state.live = s.id;
    state.opsRunKey = null;
    state.focusTrack = null;
    state.tracking = null;
    state.analysisId = null;
    state.seenTrackCount = 0;
    state.events = [];
    state.paths = [];
    state.stats = null;
    $('start-live').disabled = true;
    $('analyze').disabled = true;
    updateDashboard();
    $('stop-live').hidden = false;
    $('export').hidden = true;
    $('run-status').textContent = t(state.video?.source_profile?.simulated ? 'Starting the simulated run…' : 'Loading models…');
    $('overlay').classList.remove('editing');
    notice(t(state.video?.source_profile?.simulated ? 'Replaying the simulated camera at source pace. No model is running.' : 'Running causal inference on this recording at source cadence. First pickup score needs 13 observed samples.'));
    const source = new EventSource(`/api/live/sessions/${s.id}/stream`);
    state.source = source;
    let pending = null,
        rendering = false;
    async function display() {
        if (rendering) return;
        rendering = true;
        try {
            while (pending && state.live === s.id) {
                const p = pending;
                pending = null;
                if (p.image) {
                    const image = $('live-frame');
                    image.src = 'data:image/jpeg;base64,' + p.image;
                    await image.decode();
                    if (state.live !== s.id) break;
                    $('player').hidden = true;
                    image.hidden = false;
                    state.frame = p.frame_index;
                    state.ms = p.media_ms;
                    state.tracks = p.tracks ?? [];
                    state.paths = Object.entries(p.paths ?? {}).map(([id, points]) => ({
                        local_track_id: id,
                        points
                    }));
                    announceOutcomes(state.events, p.pickups ?? []);
                    state.events = p.pickups ?? [];
                    state.stats = p.shelves;
                    state.seenTrackCount = p.unique_tracks ?? Math.max(state.seenTrackCount ?? 0, state.paths.length);
                    $('threshold').textContent = p.threshold?.toFixed(2) ?? '0.97';
                    $('frame-time').textContent = t('{time} s, frame {n}', {
                        time: (p.media_ms / 1000).toFixed(2),
                        n: p.frame_index
                    });
                    $('performance').textContent = p.simulated ? t('Simulated replay. No model is running.') : t('{fps} fps, {ms} ms inference, {lag} ms behind source', {
                        fps: p.analysis_fps ?? '—',
                        ms: p.inference_ms ?? '—',
                        lag: p.behind_source_ms ?? '—'
                    });
                    $('run-status').textContent = p.scored_windows ? t('Live, {n} windows scored', {
                        n: p.scored_windows
                    }) : t('Live, collecting frames ({n}/13)', {
                        n: Math.min(13, p.warmup_frames ?? 0)
                    });
                    renderEvents();
                    renderStats();
                    render();
                }
                $('run-details').textContent = JSON.stringify({
                    session: s.id,
                    geometry_revision: s.geometry_id,
                    frames: p.processed_frames,
                    scored_windows: p.scored_windows,
                    status: p.status,
                    model: p.model_id ?? 'x3d-take-demo'
                }, null, 2);
                if (['completed', 'stopped', 'error'].includes(p.status)) {
                    $('export').href = `/api/live/sessions/${s.id}/export`;
                    $('export').hidden = false;
                    finishLive(p)
                }
            }
        } finally {
            rendering = false
        }
    }
    source.onmessage = e => {
        pending = JSON.parse(e.data);
        display().catch(error => notice(error.message, true))
    };
    source.onerror = () => {
        if (state.live === s.id) notice(t('Live connection interrupted. Reconnecting…'), true)
    };
}
$('stop-live').onclick = guard(stopLive);
async function playback() {
    await stopLive();
    if (state.lastLive && !state.replayFrames) {
        const response = await fetch(`/api/live/sessions/${state.lastLive}/observations`);
        if (!response.ok) throw Error(t('Saved live observations are unavailable.'));
        const rows = (await response.text()).trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
        state.replayFrames = rows;
        const paths = new Map();
        for (const row of rows)
            for (const t of row.tracks)
                if (t.position_status === 'observed') {
                    const b = t.box,
                        k = t.local_track_id;
                    if (!paths.has(k)) paths.set(k, []);
                    paths.get(k).push([row.frame_index, row.media_ms, (b[0] + b[2]) / 2, b[3]])
                }
        state.paths = Array.from(paths, ([local_track_id, points]) => ({
            local_track_id,
            segments: splitSegments(points)
        }));
        state.seenTrackCount = state.paths.length;
    }
    $('live-frame').hidden = true;
    $('player').hidden = false;
    state.ms = $('player').currentTime * 1000;
    state.tracks = [];
    $('frame-time').textContent = (state.ms / 1000).toFixed(2) + ' ' + t('s');
    render();
}
$('playback').onclick = guard(playback);
$('play-toggle').onclick = guard(async () => {
    if (state.live) return;
    const player = $('player');
    if (!player.hidden && !player.paused) {
        player.pause();
        return
    }
    if (player.hidden || state.replayFrames === null && state.lastLive) await playback();
    await player.play();
});
for (const name of ['play', 'pause', 'ended']) $('player').addEventListener(name, () => {
    const playing = !$('player').paused;
    $('play-icon').setAttribute('href', playing ? '#i-pause' : '#i-play');
    $('play-toggle').setAttribute('aria-label', t(playing ? 'Pause' : 'Play'));
});

// Saved runs are sampled below the video frame rate. Blend the two samples around a
// frame so boxes move with the picture and predicted boxes keep their dashed style.
function replaySample(ms) {
    const rows = state.replayFrames;
    if (!rows?.length) return null;
    let low = 0,
        high = rows.length - 1;
    while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (rows[middle].media_ms <= ms) low = middle;
        else high = middle - 1;
    }
    const before = rows[low],
        after = rows[low + 1];
    if (before.media_ms > ms) return ms >= before.media_ms - 120 ? before : null;
    if (!after || after.media_ms - before.media_ms > 1000) return ms - before.media_ms <= 400 ? before : null;
    const ratio = (ms - before.media_ms) / (after.media_ms - before.media_ms),
        next = new Map((after.tracks ?? []).map(row => [row.local_track_id, row])),
        tracks = (before.tracks ?? []).map(row => {
            const other = next.get(row.local_track_id);
            return other ? {
                ...row,
                box: row.box.map((value, i) => value + (other.box[i] - value) * ratio)
            } : row;
        });
    return {
        ...before,
        tracks
    };
}
async function offlineFrame(ms) {
    if (state.live || state.tab === 'geometry') return;
    state.ms = ms;
    if (!state.tracking) {
        const frame = replaySample(ms);
        state.tracks = frame?.tracks ?? [];
        $('frame-time').textContent = frame ? t('{time} s, frame {n}', {
            time: (ms / 1000).toFixed(2),
            n: frame.frame_index
        }) : (ms / 1000).toFixed(2) + ' ' + t('s');
        render();
        return;
    }
    const request = ++state.overlayRequest,
        generation = state.generation;
    state.tracks = [];
    render();
    const data = await api(`/api/tracking/jobs/${state.tracking}/observations?media_ms=${encodeURIComponent(ms)}`);
    if (request !== state.overlayRequest || generation !== state.generation || state.live) return;
    state.tracks = data.observations ?? [];
    state.frame = data.frame_index ?? 0;
    state.ms = ms;
    $('frame-time').textContent = t('{time} s, frame {n}', {
        time: (ms / 1000).toFixed(2),
        n: data.frame_index ?? '—'
    });
    render()
}
if ('requestVideoFrameCallback' in $('player')) {
    const callback = (now, metadata) => {
        if (!state.live) {
            state.ms = metadata.mediaTime * 1000;
            offlineFrame(state.ms).catch(e => notice(e.message, true))
        }
        $('player').requestVideoFrameCallback(callback)
    };
    $('player').requestVideoFrameCallback(callback)
} else $('player').ontimeupdate = () => offlineFrame($('player').currentTime * 1000).catch(e => notice(e.message, true));
async function showOffline(job, generation = state.generation) {
    const [pickups, paths, stats] = await Promise.all([api(`/api/action-analysis/analyses/${job.analysis_id}/pickups`), api(`/api/tracking/jobs/${job.tracking_job_id}/paths`), api(`/api/shelves/jobs/${job.tracking_job_id}`)]);
    if (generation !== state.generation || state.live) return;
    state.analysisId = job.analysis_id;
    state.opsRunKey = 'offline:' + job.id;
    if (typeof rememberRun === 'function') rememberRun(state.opsRunKey);
    state.lastLive = null;
    state.replayFrames = null;
    state.seenTrackCount = paths.tracks.length;
    state.events = pickups.events;
    state.paths = paths.tracks;
    state.tracking = job.tracking_job_id;
    state.stats = stats;
    $('threshold').textContent = pickups.policy?.threshold?.toFixed(2) ?? '0.97';
    $('run-details').textContent = JSON.stringify({
        status: job.status,
        analysis_id: job.analysis_id,
        tracking_job: job.tracking_job_id,
        geometry: state.geometryId,
        model: job.model_id
    }, null, 2);
    $('export').href = `/api/analyses/${job.analysis_id}/events`;
    $('export').hidden = false;
    renderEvents();
    renderStats();
    await offlineFrame($('player').currentTime * 1000)
}
async function watchOffline(job, generation = state.generation) {
    state.offline = job.id;
    $('cancel').hidden = false;
    $('analyze').disabled = true;
    $('start-live').disabled = true;
    $('progress').hidden = false;
    updateDashboard();
    try {
        let current = job;
        while (['queued', 'running', 'cancelling'].includes(current.status)) {
            $('run-status').textContent = t('Full-video analysis: {stage}', {
                stage: t(current.stage.replaceAll('_', ' '))
            });
            $('progress').value = current.progress;
            await new Promise(r => setTimeout(r, 500));
            current = await api('/api/action-analysis/jobs/' + job.id);
            if (generation !== state.generation) return;
        }
        if (current.status === 'cancelled') {
            notice(t('Analysis cancelled. You can start a new run.'));
            $('run-status').textContent = t('Cancelled');
            return;
        }
        if (current.status !== 'completed') throw Error(current.error ?? 'Analysis ' + current.status);
        await showOffline(current, generation);
        $('run-status').textContent = t('Full-video analysis complete');
        notice(t('Analysis complete. Play the recording to inspect tracks and candidates.'));
    } finally {
        state.offline = null;
        $('cancel').hidden = true;
        $('progress').hidden = true;
        $('analyze').disabled = false;
        $('start-live').disabled = state.video.source_profile.source_kind === 'sparse_clip';
        updateDashboard();
    }
}
$('analyze').onclick = guard(async () => {
    if (state.dirty || state.tool) throw Error(t('Finish drawing and save geometry first.'));
    await stopLive();
    const job = await api(`/api/action-analysis/videos/${state.video.id}`, json('POST', {
        geometry_id: state.geometryId,
        source_kind: state.video.source_profile.source_kind,
        tracking_config: {
            detector: 'rtdetrv2_r34',
            min_person_area_ratio: .005,
            person_nms_iou: .4,
            ...(typeof trackingProfile === 'function' ? trackingProfile() : {})
        }
    }));
    await watchOffline(job);
});
$('cancel').onclick = guard(() => api(`/api/action-analysis/jobs/${state.offline}/cancel`, {
    method: 'POST'
}));

function point(event) {
    const r = $('overlay').getBoundingClientRect(),
        [w, h] = state.video.frame_dims_px;
    return [Math.max(0, Math.min(w, (event.clientX - r.left) / r.width * w)), Math.max(0, Math.min(h, (event.clientY - r.top) / r.height * h))].map(n => Math.round(n * 10) / 10)
}
$('draw').onclick = guard(() => {
    if (!state.video) throw Error(t('Select a video first.'));
    if (state.live || state.offline) throw Error(t('Stop analysis before changing geometry.'));
    state.tool = $('shape-kind').value;
    state.points = [];
    $('stage').scrollIntoView({
        block: 'center',
        behavior: 'instant'
    });
    $('drawing-help').textContent = t(state.tool === 'line' ? 'Click two endpoints across the visible doorway. Reverse the arrow if necessary.' : 'Click each corner on the floor. Finish polygon when the area is enclosed.');
    renderShapes();
    render()
});

function finishShape() {
    if (state.points.length < (state.tool === 'line' ? 2 : 3)) throw Error(t('Add more vertices before finishing.'));
    const id = state.tool + '-' + crypto.randomUUID().slice(0, 8),
        shape = {
            id,
            name: state.tool === 'shelf' ? t('Shelf {n}', {
                n: state.geometry.shapes.filter(s => s.kind === 'shelf').length + 1
            }) : kindLabel(state.tool),
            kind: state.tool,
            points: state.points
        };
    if (state.tool === 'line') shape.hysteresis_px = 5;
    state.geometry.shapes.push(shape);
    state.geometry.geometry_version = 2;
    state.selected = id;
    if (['line', 'zone'].includes(shape.kind) && !state.geometry.active[shape.kind]) state.geometry.active[shape.kind] = id;
    if (['queue', 'checkout'].includes(shape.kind)) {
        const q = state.geometry.shapes.find(s => s.kind === 'queue'),
            c = state.geometry.shapes.find(s => s.kind === 'checkout');
        if (q && c) {
            state.geometry.active.queue ??= q.id;
            state.geometry.active.checkout ??= c.id;
        }
    }
    state.tool = null;
    state.points = [];
    markDirty();
    renderShapes()
}
$('finish').onclick = guard(finishShape);
$('undo').onclick = () => {
    state.points.pop();
    render()
};
$('cancel-draw').onclick = () => {
    state.tool = null;
    state.points = [];
    renderShapes();
    render()
};
$('overlay').onpointerdown = guard(event => {
    if (state.tab !== 'geometry' || state.live || state.offline) return;
    if (state.tool) {
        state.points.push(point(event));
        if (state.tool === 'line' && state.points.length === 2) finishShape();
        else render();
        return
    }
    if (event.target.dataset.vertex !== undefined) {
        state.drag = Number(event.target.dataset.vertex);
        $('overlay').setPointerCapture(event.pointerId);
        event.preventDefault()
    }
});
$('overlay').onpointermove = event => {
    if (state.drag === null) return;
    const s = state.geometry.shapes.find(s => s.id === state.selected);
    s.points[state.drag] = point(event);
    markDirty()
};
$('overlay').onpointerup = $('overlay').onpointercancel = () => state.drag = null;
$('shape-name').oninput = () => {
    const s = state.geometry.shapes.find(s => s.id === state.selected);
    s.name = $('shape-name').value;
    markDirty();
    const b = document.querySelector(`[data-shape="${CSS.escape(s.id)}"]`);
    if (b) b.firstChild.textContent = s.name;
    if ($('stage').getBoundingClientRect().top < 0) $('stage').scrollIntoView({
        block: 'center',
        behavior: 'instant'
    })
};
$('shelf-product').oninput = () => {
    const shape = state.geometry.shapes.find(s => s.id === state.selected);
    if (shape?.kind === 'shelf') { shape.product_type = $('shelf-product').value; markDirty(); }
};
$('reverse').onclick = () => {
    state.geometry.shapes.find(s => s.id === state.selected).points.reverse();
    markDirty()
};
$('line-meaning').onchange = () => {
    const shape = state.geometry.shapes.find(s => s.id === state.selected);
    if ($('line-meaning').value) shape.positive_means = $('line-meaning').value;
    else delete shape.positive_means;
    markDirty()
};
$('delete-shape').onclick = () => {
    state.geometry.shapes = state.geometry.shapes.filter(s => s.id !== state.selected);
    for (const k of Object.keys(state.geometry.active))
        if (state.geometry.active[k] === state.selected) state.geometry.active[k] = null;
    if (!state.geometry.active.queue || !state.geometry.active.checkout) {
        state.geometry.active.queue = null;
        state.geometry.active.checkout = null;
    }
    state.selected = state.geometry.shapes[0]?.id;
    state.geometry.geometry_version = 2;
    markDirty();
    renderShapes()
};
$('save-geometry').onclick = guard(async () => {
    if (state.live || state.offline) throw Error(t('Stop analysis before saving geometry.'));
    if (state.tool) throw Error(t('Finish or cancel the current drawing before saving.'));
    const saved = await api(`/api/videos/${state.video.id}/geometry`, json('PUT', state.geometry));
    applyGeometry(saved);
    notice(t('Geometry saved. The next live run will use revision {n}.', {
        n: saved.revision
    }));
    render();
    await refreshAreaStats()
});
$('reload-geometry').onclick = guard(async () => {
    applyGeometry(await api(`/api/videos/${state.video.id}/geometry`));
    state.tool = null;
    state.points = [];
    render()
});
$('geometry-import').onclick = () => $('geometry-file').click();
$('geometry-file').onchange = guard(async () => {
    if (state.live || state.offline) throw Error(t('Stop analysis before importing geometry.'));
    const file = $('geometry-file').files[0];
    if (file) {
        const saved = await api(`/api/videos/${state.video.id}/geometry`, json('PUT', JSON.parse(await file.text())));
        applyGeometry(saved);
        await refreshAreaStats();
        render();
        notice(t('Geometry imported as a saved revision.'));
        $('geometry-file').value = ''
    }
});
$('shelf-csv').onclick = () => {
    const cols = ['name', 'kind', 'unique_tracks', 'visits', 'observed_dwell_seconds', 'occupancy', 'pickup_alerts'],
        quote = x => '"' + String(x ?? '').replaceAll('"', '""') + '"',
        text = [cols, ...state.stats.areas.map(s => cols.map(k => s[k]))].map(row => row.map(quote).join(',')).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], {
        type: 'text/csv'
    }));
    a.download = 'sentient-area-activity.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000)
};
$('import-open').onclick = () => {
    $('session-id').value = 'recording-' + new Date().toISOString().slice(0, 19).replaceAll(':', '-');
    $('import-error').hidden = true;
    $('import-dialog').showModal()
};
$('import-close').onclick = () => $('import-dialog').close();
$('import-form').onsubmit = async e => {
    e.preventDefault();
    const submit = $('import-form').querySelector('[type=submit]');
    submit.disabled = true;
    $('import-progress').hidden = false;
    $('import-error').hidden = true;
    try {
        if (state.offline) throw Error(t('Wait for analysis or cancel it before importing.'));
        let options;
        const file = $('video-file').files[0],
            body = {
                store_id: $('store-id').value.trim(),
                camera_id: $('camera-id').value.trim(),
                stream_session_id: $('session-id').value.trim()
            };
        if (!body.store_id || !body.camera_id || !body.stream_session_id) throw Error(t('Enter store, camera and recording session.'));
        if (file) {
            const data = new FormData();
            for (const [k, v] of Object.entries(body)) data.append(k, v);
            data.append('file', file);
            options = {
                method: 'POST',
                body: data
            }
        } else {
            if (!$('video-path').value.trim()) throw Error(t('Choose an MP4 or enter its absolute local path.'));
            options = json('POST', {
                ...body,
                path: $('video-path').value.trim()
            })
        }
        const v = await api(file ? '/api/videos/upload' : '/api/videos/import', options);
        state.videos = await api('/api/videos');
        $('library-mode').value = 'all';
        $('library-search').value = '';
        await loadVideo(v.id);
        $('import-dialog').close();
        $('import-form').reset();
        navigate('overview');
        await loadStore();
    } catch (error) {
        $('import-error').textContent = error.message;
        $('import-error').hidden = false;
    } finally {
        submit.disabled = false;
        $('import-progress').hidden = true;
    }
};
window.addEventListener('beforeunload', event => {
    if (state.dirty) {
        event.preventDefault();
        event.returnValue = ''
    }
});
async function bootWorkspace() {
    const status = await api('/api/status');
    state.demo = status.demo_library ?? [];
    state.videos = await api('/api/videos');
    let id = new URLSearchParams(location.search).get('video');
    if (!state.videos.some(v => v.id === id)) id = [status.demo?.video_id, ...state.demo, ...state.videos.map(v => v.id)].find(key => state.videos.some(v => v.id === key));
    if (id && !state.demo.includes(id) && !state.videos.find(v => v.id === id)?.source_profile?.simulated) $('library-mode').value = 'all';
    library();
    if (id) await loadVideo(id)
}
