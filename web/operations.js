'use strict';
const ops = {
    workspace: null,
    data: null,
    key: null,
    signature: '',
    pending: false,
    contextVideo: null,
    view: 'overview',
    track: null,
    review: null,
    ruleRevision: 0,
    booted: false,
    health: null
};
let toastTimer;
const viewInfo = {
    overview: ['Monitor', 'Eyes on the floor.', 'Track movement. Investigate signals. Turn footage into decisions.'],
    tracking: ['Track explorer', 'Every route has context.', 'Follow anonymous tracks through the camera view. Inspect observations and export evidence.'],
    analytics: ['Analytics', 'Patterns, backed by footage.', 'Observed occupancy, area transitions, directional crossings and queue activity.'],
    incidents: ['Investigations', 'Signal. Evidence. Decision.', 'Review candidates, record decisions and keep the evidence attached.'],
    reports: ['Report centre', 'From footage to briefing.', 'Generate shift, daily and monthly reports with traceable source data.'],
    geometry: ['Camera setup', 'Give the scene context.', 'Map areas, configure tracking and place recordings on your reporting timeline.'],
    system: ['System & models', 'Know what is running.', 'Measured health, configurable rules and transparent model limits.'],
    help: ['Demo guide', 'Make the most of each view.', 'A practical guide to geometry, tracking and interpreting your results.']
};

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

function currentRun() {
    return state.live ? null : state.lastLive ? 'live:' + state.lastLive : state.opsRunKey
}

function shortTrack(id) {
    return 'Track ' + String(id).split(':').at(-1).padStart(2, '0')
}

function sec(value) {
    return value === null || value === undefined ? '—' : Number(value).toFixed(1) + 's'
}

function downloadObject(value, name, type = 'application/json') {
    const url = URL.createObjectURL(new Blob([typeof value === 'string' ? value : JSON.stringify(value, null, 2)], {
        type
    }));
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500)
}

function exportRows(rows, columns, name) {
    const cell = v => {
        let text = String(v ?? '');
        if (/^[\s]*[=+@-]/.test(text)) text = "'" + text;
        return '"' + text.replaceAll('"', '""') + '"'
    };
    downloadObject([columns, ...rows.map(r => columns.map(c => r[c]))].map(r => r.map(cell).join(',')).join('\n'), name, 'text/csv;charset=utf-8')
}
navigate = function(name) {
    if (!viewInfo[name]) name = 'overview';
    ops.view = name;
    document.body.dataset.view = name;
    const [label, title, subtitle] = viewInfo[name];
    $('page-label').textContent = label;
    $('page-title').textContent = title;
    $('page-subtitle').textContent = subtitle;
    document.querySelectorAll('[data-nav]').forEach(b => {
        b.classList.toggle('active', b.dataset.nav === name);
        b.setAttribute('aria-current', b.dataset.nav === name ? 'page' : 'false')
    });
    for (const [id, visible] of Object.entries({
            'tracking-section': name === 'tracking',
            'advanced-analytics': name === 'analytics',
            'incidents-section': name === 'incidents',
            'reports-section': name === 'reports',
            'context-section': name === 'geometry',
            'system-section': name === 'system'
        })) $(id).hidden = !visible;
    if (name === 'geometry' || name === 'help') tab(name);
    else if (state.tab !== 'results') tab('results');
    if (name === 'reports') refreshWorkspace().catch(e => toast(e.message, true));
    if (name === 'system') refreshHealth().catch(e => toast(e.message, true));
    if (name === 'analytics' || name === 'tracking' || name === 'incidents') refreshInsights().catch(e => toast(e.message, true));
    history.replaceState(null, '', `/?${new URLSearchParams({...(state.video?{video:state.video.id}:{}),view:name,...(currentRun()?{run:currentRun()}:{} )})}`);
    window.scrollTo({
        top: 0,
        behavior: 'instant'
    });
    closeMenu();
};

function syncOperations() {
    const busy = !!(state.live || state.offline) || !state.video;
    $('tracking-only').disabled = busy;
    $('analysis-run').disabled = busy;
    $('observed-now').textContent = state.video ? `${state.tracks.filter(t=>t.position_status==='observed').length} observed in frame` : 'Awaiting observations';
    $('clear-focus').hidden = !state.focusTrack;
    if (state.live && state.focusTrack) renderLiveFocus();
    if (state.video?.id !== ops.contextVideo && state.video) {
        ops.contextVideo = state.video.id;
        ops.track = null;
        state.focusTrack = null;
        $('track-focus').hidden = true;
        $('track-detail').hidden = true;
        fillContext();
        recoverTracking().catch(e => toast(e.message, true))
    }
    fillQueueMappings();
    renderFeedReviews();
    const key = currentRun(),
        signature = [key, state.geometryId, ops.ruleRevision].join('|');
    if (signature !== ops.signature && !ops.pending) {
        ops.signature = signature;
        refreshInsights(true).catch(e => toast(e.message, true))
    }
}
async function refreshInsights(force = false) {
    const key = currentRun();
    if (!key) {
        ops.data = null;
        ops.key = null;
        renderOperationViews();
        return
    }
    if (ops.pending) return;
    if (!force && ops.key === key && ops.data) return;
    ops.pending = true;
    try {
        const data = await api(`/api/ops/runs/${encodeURIComponent(key)}/insights`);
        if (key !== currentRun()) return;
        ops.data = data;
        ops.key = key;
        ops.availableRuns = await api('/api/ops/runs?video_id=' + state.video.id);
        fillRunSelector();
        renderOperationViews()
    } finally {
        ops.pending = false;
        if (key !== currentRun()) updateDashboard()
    }
}

function empty(text) {
    return `<div class="empty-state">${icon('camera')}<h3>${esc(text)}</h3><p>Select a completed run or analyze a recording to populate this view.</p></div>`
}

function renderOperationViews() {
    renderTrackTable();
    renderIncidents();
    renderAnalytics();
    $('nav-pending').textContent = ops.data?.summary.pending ?? 0;
    if (ops.track && ops.data?.tracks.some(t => t.id === ops.track)) renderFocus(ops.data.tracks.find(t => t.id === ops.track));
}

function renderTrackTable() {
    if (!ops.data) {
        $('track-total').textContent = state.live ? 'Live tracking active' : 'No selected run';
        $('track-table').innerHTML = '<tr><td colspan="7">Complete a run to inspect track histories. Click boxes during live inference to focus a visible track.</td></tr>';
        return
    }
    const query = $('track-search').value.trim().toLowerCase(),
        minimum = Number($('track-min').value) || 0,
        areas = new Map(ops.data.areas.map(a => [a.id, a.name]));
    const rows = ops.data.tracks.filter(t => (t.id + ' ' + t.area_ids.map(k => areas.get(k)).join(' ')).toLowerCase().includes(query) && (t.observed_seconds ?? 0) >= minimum);
    const sort = $('track-sort').value;
    rows.sort((a, b) => sort === 'dwell' ? (b.observed_seconds ?? 0) - (a.observed_seconds ?? 0) : sort === 'alerts' ? b.pickups - a.pickups : a.first_ms - b.first_ms);
    $('track-total').textContent = `${rows.length} / ${ops.data.tracks.length} tracks`;
    $('track-table').innerHTML = rows.map(t => `<tr><td><span class="track-pill">${esc(shortTrack(t.id))}</span><small>Camera local</small></td><td>${(t.first_ms/1000).toFixed(1)}–${(t.last_ms/1000).toFixed(1)}s</td><td>${sec(t.observed_seconds)}</td><td>${esc(t.area_ids.map(k=>areas.get(k)??k).join(', ')||'No mapped area')}</td><td>${Math.round(t.observed_fraction*100)}% observed<small>${Math.max(0,t.segments-1)} observation gaps</small></td><td><span class="signal-pill">${t.pickups??'—'}</span></td><td><button data-focus-track="${esc(t.id)}">Inspect ↗</button></td></tr>`).join('') || '<tr><td colspan="7">No tracks match these filters.</td></tr>';
    document.querySelectorAll('[data-focus-track]').forEach(b => b.onclick = task(() => selectTrack(b.dataset.focusTrack, true)));
}
for (const id of ['track-search', 'track-sort', 'track-min']) $(id).addEventListener(id === 'track-search' ? 'input' : 'change', renderTrackTable);
$('tracks-export').onclick = task(() => {
    if (!ops.data) throw Error('Select a completed run first.');
    exportRows(ops.data.tracks.map(t => ({
        ...t,
        area_ids: t.area_ids.join(';')
    })), ['id', 'first_ms', 'last_ms', 'observed_seconds', 'observed_samples', 'predicted_samples', 'observed_fraction', 'segments', 'path_pixels', 'area_ids', 'pickups'], 'sentient-tracks.csv')
});
async function selectTrack(identity, openDetail = false) {
    state.focusTrack = identity;
    ops.track = identity;
    render();
    const key = currentRun();
    if (!key) {
        renderLiveFocus();
        return
    }
    const data = await api(`/api/ops/runs/${encodeURIComponent(key)}/tracks/${encodeURIComponent(identity)}`);
    if (key !== currentRun() || identity !== ops.track) return;
    renderFocus(data.track);
    const t = data.track,
        first = data.samples.find(s => s.position_status === 'observed');
    $('track-detail').hidden = false;
    $('track-detail').innerHTML = `<div class="detail-header"><div><div class="eyebrow">TRACK INVESTIGATION</div><h3>${esc(shortTrack(identity))} <span class="badge">Camera local</span></h3></div><button id="detail-clear">Clear selection ×</button></div><div class="detail-grid"><a href="${evidenceURL(identity,t.first_ms)}" download><img src="${evidenceURL(identity,t.first_ms)}" alt="Observed source frame for ${esc(shortTrack(identity))}"></a><div><div class="track-stat-grid"><div><small>First observed</small><b>${(t.first_ms/1000).toFixed(1)}s</b></div><div><small>Last observed</small><b>${(t.last_ms/1000).toFixed(1)}s</b></div><div><small>Observed time</small><b>${sec(t.observed_seconds)}</b></div><div><small>Gaps</small><b>${Math.max(0,t.segments-1)}</b></div></div><div class="toolbar"><button id="track-first" class="primary">Jump to first observation</button><a class="button-link" href="${evidenceURL(identity,t.first_ms)}" download>Evidence PNG ↗</a><button id="trajectory-export">Trajectory CSV</button></div><p class="field-note">${Math.round(t.observed_fraction*100)}% of sampled track records are observed. Predicted boxes do not contribute to dwell. Distance, when available, is ${t.path_pixels??'unknown'} image pixels.</p></div></div><div class="visit-ledger"><h3>Area visit ledger</h3>${data.visits.map(v=>`<div class="visit-row"><span>${esc(v.name)}</span><span>${(v.start_ms/1000).toFixed(1)}–${(v.end_ms/1000).toFixed(1)}s</span><span>${sec(v.observed_seconds)} observed</span><span class="badge">${v.entry_observed&&v.end_observed?'Observed entry & exit':'Partial visit'}</span><button data-visit-ms="${v.start_ms}">Inspect ↗</button></div>`).join('')||'<p class="field-note">No mapped area visits for this track.</p>'}</div>`;
    $('detail-clear').onclick = clearFocus;
    $('track-first').onclick = task(async () => {
        navigate('tracking');
        await seekRecording(t.first_ms)
    });
    $('trajectory-export').onclick = () => exportRows(data.samples.map(s => ({
        ...s,
        box: s.box.join(';')
    })), ['frame_index', 'media_ms', 'local_track_id', 'position_status', 'box'], 'sentient-trajectory.csv');
    document.querySelectorAll('[data-visit-ms]').forEach(b => b.onclick = task(async () => {
        navigate('tracking');
        await seekRecording(Number(b.dataset.visitMs))
    }));
    if (openDetail) {
        if (ops.view !== 'tracking') navigate('tracking');
        $('track-detail').scrollIntoView({
            behavior: 'smooth',
            block: 'start'
        })
    }
}

function evidenceURL(identity, ms) {
    return `/api/ops/runs/${encodeURIComponent(currentRun())}/tracks/${encodeURIComponent(identity)}/evidence?media_ms=${ms}`
}

function renderFocus(track) {
    $('track-focus').hidden = false;
    $('track-focus').innerHTML = `<div class="focus-title"><span>FOCUS / ${esc(shortTrack(track.id))}</span><button id="focus-dismiss" class="icon-button" aria-label="Clear selected track">×</button></div><div class="focus-stats"><div><small>Observed time</small><b>${sec(track.observed_seconds)}</b></div><div><small>Pickup candidates</small><b>${track.pickups??'—'}</b></div></div><div class="toolbar"><button id="focus-investigate">Track details ↗</button><button id="focus-start">First seen ${(track.first_ms/1000).toFixed(1)}s</button></div>`;
    $('focus-dismiss').onclick = clearFocus;
    $('focus-investigate').onclick = task(() => selectTrack(track.id, true));
    $('focus-start').onclick = task(() => seekRecording(track.first_ms));
}

function renderLiveFocus() {
    const t = state.tracks.find(t => t.local_track_id === state.focusTrack);
    $('track-focus').hidden = false;
    $('track-focus').innerHTML = `<div class="focus-title"><span>LIVE / ${esc(shortTrack(state.focusTrack))}</span><button id="focus-dismiss" class="icon-button" aria-label="Clear selected track">×</button></div><p class="field-note">${t?(t.position_status==='observed'?'Observed in this frame':'Tracker prediction'):'Temporarily outside the visible track set'}. Full trajectory and evidence become available when the run is saved.</p>`;
    $('focus-dismiss').onclick = clearFocus;
}

function clearFocus() {
    state.focusTrack = null;
    ops.track = null;
    $('track-focus').hidden = true;
    $('track-detail').hidden = true;
    render()
}
$('clear-focus').onclick = clearFocus;
$('overlay').addEventListener('click', task(async event => {
    if (state.tab === 'geometry') return;
    const target = event.target.closest('[data-track-id]');
    if (target) await selectTrack(target.dataset.trackId, false)
}));
$('show-footpoints').onchange = render;
$('trail-window').onchange = render;
$('playback-speed').onchange = () => {
    $('player').playbackRate = Number($('playback-speed').value)
};
$('step-back').onclick = task(() => seekRecording(Math.max(0, state.ms - 200)));
$('step-forward').onclick = task(() => seekRecording(state.ms + 200));
$('open-investigations').onclick = () => navigate('incidents');

function renderIncidents() {
    if (!ops.data) {
        $('incident-list').innerHTML = empty(state.live ? 'The run is still being saved' : 'No completed run selected');
        $('incident-total').textContent = '';
        return
    }
    const kind = $('incident-kind').value,
        status = $('incident-status').value;
    const events = ops.data.incidents.filter(e => (kind === 'all' || e.kind === kind) && (status === 'all' || e.review.status === status));
    $('incident-total').textContent = `${events.length} signals · ${ops.data.summary.pending} pending`;
    $('incident-list').innerHTML = events.map(e => `<button class="signal-card" data-signal="${esc(e.id)}"><div class="signal-top"><span class="signal-type">${esc(e.kind)}</span><span class="review-badge ${e.review.status}">${e.review.status==='rejected'?'Dismissed':e.review.status==='accepted'?'Accepted':'Pending review'}</span></div><h3>${esc(e.name)}</h3><p>${e.track_id?esc(shortTrack(e.track_id)):'Area occupancy'} · ${(e.media_ms/1000).toFixed(1)}s${e.score!==undefined?' · '+(e.score*100).toFixed(1)+'% score':''}${e.count?' · peak '+e.count+' tracks':''}${e.seconds?' · '+sec(e.seconds)+' observed':''}</p>${e.review.notes?`<p class="review-note">${esc(e.review.notes).slice(0,180)}</p>`:''}<span class="signal-bottom"><span>${esc(ops.data.video.camera_id)}</span><span>Review signal ↗</span></span></button>`).join('') || empty('No signals match these filters');
    document.querySelectorAll('[data-signal]').forEach(b => b.onclick = () => openIncident(b.dataset.signal));
}
$('incident-kind').onchange = $('incident-status').onchange = renderIncidents;

function openIncident(id) {
    const e = ops.data?.incidents.find(e => e.id === id);
    if (!e) return;
    ops.review = {
        ...e,
        run_key: currentRun()
    };
    $('incident-title').textContent = e.name;
    $('incident-meta').textContent = `${ops.data.video.camera_id} · ${(e.media_ms/1000).toFixed(1)}s source time · ${e.track_id?shortTrack(e.track_id):'Area signal'}`;
    $('incident-evidence').innerHTML = e.track_id ? `<img src="${evidenceURL(e.track_id,e.media_ms)}" alt="Source frame with the observed person marked">` : `<p>${e.kind==='dwell'?'Extended observed presence':'Configured occupancy threshold exceeded'} in ${esc(e.name)}. Inspect the recording before recording your decision.</p>`;
    $('incident-image').hidden = !e.track_id;
    if (e.track_id) $('incident-image').href = evidenceURL(e.track_id, e.media_ms);
    $('incident-decision').value = e.review.status;
    $('incident-notes').value = e.review.notes ?? '';
    $('incident-message').textContent = '';
    $('incident-dialog').showModal();
}
$('incident-close').onclick = () => $('incident-dialog').close();
$('incident-inspect').onclick = task(async () => {
    const e = ops.review;
    $('incident-dialog').close();
    navigate('tracking');
    if (e.track_id) {
        await selectTrack(e.track_id, false)
    }
    await seekRecording(e.start_ms ?? e.media_ms)
});
$('incident-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('incident-form').querySelector('[type=submit]');
    button.disabled = true;
    try {
        const e = ops.review,
            review = await api(`/api/ops/runs/${encodeURIComponent(e.run_key)}/reviews/${encodeURIComponent(e.id)}`, json('PUT', {
                status: $('incident-decision').value,
                notes: $('incident-notes').value,
                expected_revision: e.review.revision ?? 0
            }));
        if (currentRun() === e.run_key && ops.data) {
            const signal = ops.data.incidents.find(s => s.id === e.id);
            if (signal) signal.review = review;
            for (const status of ['pending', 'accepted', 'rejected']) ops.data.summary[status] = ops.data.incidents.filter(s => s.review.status === status).length;
            renderOperationViews()
        }
        $('incident-dialog').close();
        toast('Review saved with this source run.');
    } catch (e) {
        $('incident-message').textContent = e.message
    } finally {
        button.disabled = false
    }
};
$('incidents-export').onclick = task(() => {
    if (!ops.data) throw Error('Select a completed run first.');
    exportRows(ops.data.incidents.map(e => ({
        ...e,
        review_status: e.review.status,
        review_notes: e.review.notes
    })), ['id', 'kind', 'track_id', 'media_ms', 'end_ms', 'name', 'score', 'review_status', 'review_notes'], 'sentient-signals.csv')
});

function renderAnalytics() {
    if (!ops.data) {
        $('ops-kpis').innerHTML = '';
        for (const id of ['occupancy-chart', 'transition-chart', 'crossing-cards', 'queue-summary']) $(id).innerHTML = empty('No completed run selected');
        return
    }
    const d = ops.data,
        s = d.summary;
    $('ops-kpis').innerHTML = [
        ['Peak observed', s.peak_people, 'Simultaneous tracks in one view'],
        ['Observed footage', sec(s.observed_seconds), 'Between sampled source frames'],
        ['Positive crossings', d.line_counts.length ? d.crossings.filter(c => c.direction === 'positive').length : '—', d.line_counts.length + ' directional lines configured'],
        ['Observed sample share', Math.round(s.observed_fraction * 100) + '%', 'Observed / sampled track records']
    ].map(([l, v, n]) => `<article><small>${l}</small><b>${v}</b><p>${esc(n)}</p></article>`).join('');
    const rows = d.timeline,
        W = 600,
        H = 190,
        left = 28,
        top = 12,
        bottom = 27,
        right = 9,
        max = Math.max(1, ...rows.map(r => r.people)),
        start = rows[0]?.media_ms ?? 0,
        end = rows.at(-1)?.media_ms ?? 1,
        span = Math.max(1, end - start),
        height = H - top - bottom;
    const points = rows.filter((r, i) => i % Math.max(1, Math.ceil(rows.length / 250)) === 0 || i === rows.length - 1).map(r => [left + (r.media_ms - start) / span * (W - left - right), top + height - r.people / max * height]);
    const line = points.map(p => p.join(',')).join(' ');
    let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Observed occupancy along the recording">`;
    for (let i = 0; i < 3; i++) {
        const y = top + i * height / 2;
        svg += `<line x1="${left}" x2="${W-right}" y1="${y}" y2="${y}" stroke="#354a2c" stroke-dasharray="3 4"/><text x="${left-8}" y="${y+3}" text-anchor="end">${(max*(1-i/2)).toFixed(max%2?1:0)}</text>`
    }
    if (points.length) svg += `<polygon points="${left},${top+height} ${line} ${points.at(-1)[0]},${top+height}" fill="#a9de5c14"/><polyline points="${line}" stroke="#b2eb72" fill="none" stroke-width="2"/>`;
    for (let i = 0; i <= 4; i++) svg += `<text x="${left+i*(W-left-right)/4}" y="${H-5}" text-anchor="middle">${((start+i*span/4)/1000).toFixed(1)}s</text>`;
    $('occupancy-chart').innerHTML = svg + '</svg>';
    $('occupancy-chart').onclick = task(async event => {
        const r = $('occupancy-chart').getBoundingClientRect();
        const ms = start + Math.max(0, Math.min(1, (event.clientX - r.left) / r.width)) * span;
        navigate('tracking');
        await seekRecording(ms)
    });
    $('transition-chart').innerHTML = d.transitions.length ? d.transitions.slice(0, 8).map(t => `<div class="transition-row"><span>${esc(t.from_name)}</span><span class="route-arrow">→</span><span>${esc(t.to_name)}</span><b>${t.count}</b></div>`).join('') : '<p class="empty-chart">No continuous transitions between distinct areas were observed.</p>';
    $('crossing-cards').innerHTML = d.line_counts.length ? d.line_counts.map(l => `<div class="crossing-row"><span>${esc(l.name)}</span><b>+${l.positive}</b><span>positive</span><b>−${l.negative}</b><span>negative</span></div>`).join('') : '<p class="empty-chart">Draw a doorway line to measure directional crossings.</p>';
    $('queue-summary').innerHTML = `<div class="queue-values"><div><b>${d.areas.some(a=>a.kind==='queue')?d.queue.peak_people:'—'}</b><small>Peak observed waiting tracks</small></div><div><b>${sec(d.queue.median_complete_wait_seconds)}</b><small>Median observed entry → checkout</small></div></div><p class="queue-method">${esc(d.queue.method)} ${d.queue.observed_waits.length} observed transitions; ${d.queue.observed_waits.filter(w=>w.complete_entry).length} with an observed waiting-area entry.</p>`;
}
async function refreshWorkspace() {
    const data = await api('/api/ops/workspace');
    ops.workspace = data;
    fillReports();
    fillSchedules();
    if (!ops.booted) fillSettings();
    fillContext();
    if (state.video) {
        $('video-name').textContent = title(state.video);
        library();
    }
    return data;
}

function fillContext() {
    const v = ops.workspace?.recordings.find(v => v.id === state.video?.id),
        context = v?.context ?? {};
    $('context-label').value = context.label ?? '';
    $('context-start').value = context.started_at_utc ? deviceInput(context.started_at_utc) : '';
    $('context-shift').value = context.shift_label ?? '';
    $('context-notes').value = context.notes ?? '';
    $('context-message').textContent = context.started_at_utc ? 'Time basis: explicitly assigned by an operator.' : 'Undated footage is excluded from calendar reports.';
}

function deviceInput(value) {
    const d = new Date(value);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
$('context-form').onsubmit = async event => {
    event.preventDefault();
    if (!state.video) {
        toast('Select a recording first.', true);
        return
    }
    try {
        const context = {
            label: $('context-label').value,
            started_at_utc: $('context-start').value ? new Date($('context-start').value).toISOString() : null,
            shift_label: $('context-shift').value,
            notes: $('context-notes').value
        };
        await api('/api/ops/recordings/' + state.video.id, json('PUT', context));
        await refreshWorkspace();
        $('context-message').textContent = 'Recording context saved. Calendar reports now use this explicitly assigned time.';
        toast('Recording context saved.')
    } catch (e) {
        $('context-message').textContent = e.message
    }
};

function fillQueueMappings() {
    if (!state.geometry) return;
    for (const kind of ['queue', 'checkout']) {
        const select = $('active-' + kind),
            shapes = state.geometry.shapes.filter(s => s.kind === kind),
            html = '<option value="">Not configured</option>' + shapes.map(s => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('');
        if (select.innerHTML !== html) select.innerHTML = html;
        select.value = state.geometry.active[kind] ?? '';
        select.disabled = !!(state.live || state.offline)
    }
}
for (const kind of ['queue', 'checkout']) $('active-' + kind).onchange = () => {
    state.geometry.active[kind] = $('active-' + kind).value || null;
    if (!state.geometry.active[kind]) {
        state.geometry.active.queue = null;
        state.geometry.active.checkout = null;
    }
    markDirty()
};

function trackingProfile() {
    try {
        return JSON.parse(localStorage.getItem('sentient.tracking-profile') || '{}')
    } catch {
        return {}
    }
}
const storedProfile = trackingProfile();
$('tracking-confidence').value = storedProfile.high_threshold ?? .5;
$('tracking-gap').value = storedProfile.max_lost_ms ?? 1000;
$('apply-tracking').onclick = task(() => {
    const profile = {
        high_threshold: Number($('tracking-confidence').value),
        max_lost_ms: Number($('tracking-gap').value)
    };
    if (profile.high_threshold < .15 || profile.high_threshold > .95 || profile.max_lost_ms < 200 || profile.max_lost_ms > 5000) throw Error('Use confidence 0.15–0.95 and lost buffer 200–5000 ms.');
    localStorage.setItem('sentient.tracking-profile', JSON.stringify(profile));
    $('tracking-profile-message').textContent = 'Profile saved. It applies to the next full-video or tracking-only run.';
    toast('Tracking profile saved.')
});
async function attachTracking(job) {
    const [paths, stats] = await Promise.all([api(`/api/tracking/jobs/${job.id}/paths`), api(`/api/shelves/jobs/${job.id}`)]);
    state.tracking = job.id;
    state.opsRunKey = 'track:' + job.id;
    rememberRun(state.opsRunKey);
    $('threshold').textContent = '—';
    state.lastLive = null;
    state.replayFrames = null;
    state.analysisId = null;
    state.events = [];
    state.paths = paths.tracks;
    state.seenTrackCount = paths.tracks.length;
    state.stats = stats;
    state.tracks = [];
    $('live-frame').hidden = true;
    $('player').hidden = false;
    $('export').href = `/api/ops/runs/${encodeURIComponent(state.opsRunKey)}/insights`;
    $('export').hidden = false;
    $('run-status').textContent = 'Person tracking complete';
    notice('Person detection and tracking saved. Open Track explorer to investigate identities and routes.');
    renderEvents();
    renderStats();
    await offlineFrame($('player').currentTime * 1000);
}
async function watchTracking(job) {
    state.offline = job.id;
    state.trackingOnly = true;
    $('start-live').disabled = true;
    $('analyze').disabled = true;
    $('cancel').hidden = false;
    $('progress').hidden = false;
    updateDashboard();
    const generation = state.generation;
    try {
        let current = job;
        while (['queued', 'running', 'cancelling'].includes(current.status)) {
            $('run-status').textContent = 'Person tracking · ' + Math.round(current.progress * 100) + '%';
            $('progress').value = current.progress;
            await new Promise(r => setTimeout(r, 500));
            current = await api('/api/tracking/jobs/' + job.id);
            if (generation !== state.generation) return
        }
        if (current.status === 'completed') await attachTracking(current);
        else if (current.status === 'cancelled') {
            notice('Tracking cancelled.');
            $('run-status').textContent = 'Cancelled'
        } else throw Error(current.error ?? 'Tracking failed');
    } finally {
        state.offline = null;
        state.trackingOnly = false;
        localStorage.removeItem('sentient.pending-tracking.' + state.video.id);
        $('cancel').hidden = true;
        $('progress').hidden = true;
        $('start-live').disabled = state.video.source_profile.source_kind === 'sparse_clip';
        $('analyze').disabled = false;
        updateDashboard()
    }
}
$('tracking-only').onclick = task(async () => {
    if (state.dirty || state.tool) throw Error('Finish drawing and save geometry before analysis.');
    await stopLive();
    const job = await api('/api/tracking/videos/' + state.video.id, json('POST', {
        geometry_id: state.geometryId,
        config: {
            detector: 'rtdetrv2_r34',
            source_kind: state.video.source_profile.source_kind,
            min_person_area_ratio: .005,
            person_nms_iou: .4,
            ...trackingProfile()
        }
    }));
    localStorage.setItem('sentient.pending-tracking.' + state.video.id, job.id);
    await watchTracking(job)
});
$('cancel').onclick = task(() => api(`/api/${state.trackingOnly?'tracking':'action-analysis'}/jobs/${state.offline}/cancel`, {
    method: 'POST'
}));
async function recoverTracking() {
    const id = localStorage.getItem('sentient.pending-tracking.' + state.video?.id);
    if (!id || state.offline || state.live) return;
    const job = await api('/api/tracking/jobs/' + id);
    if (job.video_id === state.video.id) await watchTracking(job)
}

function fillSettings() {
    const s = ops.workspace.settings;
    $('setting-name').value = s.workspace_name;
    $('setting-timezone').value = s.timezone;
    $('setting-shift-start').value = s.shift_start;
    $('setting-shift-end').value = s.shift_end;
    $('setting-dwell').value = s.dwell_limit_s;
    $('setting-crowd').value = s.crowd_limit;
    $('setting-queue').value = s.queue_limit;
    document.querySelector('.workspace-switch b').textContent = s.workspace_name
}
$('settings-form').onsubmit = async event => {
    event.preventDefault();
    const body = {
        workspace_name: $('setting-name').value,
        timezone: $('setting-timezone').value,
        shift_start: $('setting-shift-start').value,
        shift_end: $('setting-shift-end').value,
        dwell_limit_s: Number($('setting-dwell').value),
        crowd_limit: Number($('setting-crowd').value),
        queue_limit: Number($('setting-queue').value)
    };
    try {
        await api('/api/ops/settings', json('PUT', body));
        ops.ruleRevision++;
        await refreshWorkspace();
        fillSettings();
        await refreshInsights(true);
        $('settings-message').textContent = 'Settings saved. Signal rules were recalculated from the saved observations.';
        toast('Workspace settings saved.')
    } catch (e) {
        $('settings-message').textContent = e.message
    }
};
async function refreshHealth() {
    const h = await api('/api/ops/health');
    ops.health = h;
    $('system-health').innerHTML = [
        ['CPU usage', h.cpu_percent + '%', 'Measured process host'],
        ['System memory', h.ram_used_gb + ' GB', h.ram_total_gb + ' GB installed'],
        ['GPU', h.gpu ? h.gpu.utilization + '%' : 'Unavailable', h.gpu?.name ?? 'No NVIDIA telemetry'],
        ['GPU memory', h.gpu ? (h.gpu.memory_used_mb / 1024).toFixed(1) + ' GB' : '—', h.gpu ? (h.gpu.memory_total_mb / 1024).toFixed(1) + ' GB installed' : 'CPU fallback may be available']
    ].map(([l, v, n]) => `<article><small>${l}</small><b>${v}</b><p>${esc(n)}</p></article>`).join('');
    const reviewed = ops.data ? ops.data.summary.accepted + ops.data.summary.rejected : 0;
    $('model-readiness').innerHTML = `<div class="model-row"><b>Pickup model ${h.action_model.available?'available locally':'not configured'}</b><code>${esc(h.model_id??'No configured model')}</code><p>Score threshold ${h.pickup_policy?.threshold?.toFixed(2)??'unknown'}. Continuous store-footage precision is unmeasured.</p></div><div class="model-row"><b>RT-DETRv2 + time-aware local association</b><p>Live detector ${h.detector_cached?'cached':'loads on demand'}. Tracker uses motion and two-stage IoU association. It has no appearance embeddings or cross-camera identity.</p></div><div class="model-row"><b>Operator review coverage</b><p>${reviewed} reviewed signals in the selected run. ${ops.data?.summary.pending??0} pending. Review decisions are annotations, not an independent accuracy benchmark.</p></div><div class="model-row"><b>Report scheduler ${h.scheduler_running?'running':'stopped'}</b><p>Schedules execute while this app is running. Results stay in the local report library.</p></div>${h.limitations.map(l=>`<p class="field-note">${esc(l)}</p>`).join('')}`;
}
$('health-refresh').onclick = task(refreshHealth);

function fillReports() {
    const previously = new Set(Array.from(document.querySelectorAll('[data-report-source]:checked'), e => e.value));
    const initial = !$('report-sources').children.length;
    const recordings = ops.workspace.recordings.filter(v => (v.runs.length || state.demo.includes(v.id)) && (!$('report-store').value || v.store_id === $('report-store').value)).sort((a, b) => Number(state.demo.includes(b.id)) - Number(state.demo.includes(a.id)));
    const store = $('report-store').value;
    const storeHTML = '<option value="">All selected stores</option>' + [...new Set(ops.workspace.recordings.filter(v => v.runs.length).map(v => v.store_id))].sort().map(s => `<option value="${esc(s)}">${esc(s)}</option>`).join('');
    if ($('report-store').innerHTML !== storeHTML) {
        $('report-store').innerHTML = storeHTML;
        $('report-store').value = store
    }
    $('report-sources').innerHTML = recordings.map(v => `<label class="report-source"><input data-report-source type="checkbox" value="${v.id}" ${initial?state.demo.includes(v.id)||v.id===state.video?.id?'checked':'':previously.has(v.id)?'checked':''}><span>${esc(v.context.label||title({display_name:v.name}))}<small>${esc(v.camera_id)} · ${v.runs.length?'Analysis available':'No completed run'} · ${v.context.started_at_utc?new Date(v.context.started_at_utc).toLocaleString():'Undated'}</small></span></label>`).join('') || '<p class="field-note">Analyze a recording to add it to reporting.</p>';
    $('report-zone-note').textContent = `Calendar timezone: ${ops.workspace.settings.timezone}. Undated footage appears only in selected-recording reports.`;
    $('report-library').innerHTML = ops.workspace.reports.map(r => `<article class="report-file"><div class="eyebrow">${esc(r.scope)} REPORT</div><h3>${esc(r.title)}</h3><p>${new Date(r.created_at).toLocaleString()} · ${r.recordings} recordings</p><p>${r.summary.tracks} local track IDs · ${r.summary.pickups??'Unscored'} candidates${r.coverage_status==='no_matching_recordings'?' · No matching coverage':''}</p><div class="report-downloads">${['pdf','docx','csv','json'].map(f=>`<a href="/api/ops/reports/${r.id}/${f}" download>${f.toUpperCase()} ↗</a>`).join('')}</div></article>`).join('') || empty('Your report library is ready');
}
$('report-store').onchange = fillReports;
$('report-select-all').onclick = () => document.querySelectorAll('[data-report-source]').forEach(e => e.checked = !!ops.workspace.recordings.find(v => v.id === e.value)?.runs.length);

function zonedInput(date, time, tz) {
    const base = new Date(date + 'T' + time + ':00Z');
    let result = new Date(base);
    for (let i = 0; i < 3; i++) {
        const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
            timeZone: tz,
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit',
            hourCycle: 'h23'
        }).formatToParts(result).filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
        const formatted = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
        result = new Date(result.getTime() + base.getTime() - formatted)
    }
    return result.toISOString();
}

function currentDateInZone(tz) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
        timeZone: tz,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    }).formatToParts(new Date()).filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}`
}

function addDay(date, n) {
    const d = new Date(date + 'T12:00:00Z');
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10)
}

function reportBody() {
    const ids = Array.from(document.querySelectorAll('[data-report-source]:checked'), e => e.value);
    if (!ids.length) throw Error('Select at least one recording.');
    const scope = $('report-scope').value,
        s = ops.workspace.settings;
    const body = {
        title: $('report-title').value.trim(),
        scope,
        video_ids: ids,
        store_id: $('report-store').value || null,
        notes: $('report-notes').value
    };
    if (!body.title) throw Error('Enter a report title.');
    if (scope === 'collection') return body;
    if (scope === 'custom') {
        if (!$('report-start').value || !$('report-end').value) throw Error('Set report start and end.');
        const [startDate, startTime] = $('report-start').value.split('T'), [endDate, endTime] = $('report-end').value.split('T');
        body.start = zonedInput(startDate, startTime, s.timezone);
        body.end = zonedInput(endDate, endTime, s.timezone)
    } else if (scope === 'month') {
        const month = $('report-month').value;
        if (!month) throw Error('Select a month.');
        const start = month + '-01',
            d = new Date(start + 'T12:00:00Z');
        d.setUTCMonth(d.getUTCMonth() + 1);
        body.start = zonedInput(start, '00:00', s.timezone);
        body.end = zonedInput(d.toISOString().slice(0, 10), '00:00', s.timezone)
    } else {
        const date = $('report-date').value;
        if (!date) throw Error('Select a report date.');
        body.start = zonedInput(date, scope === 'shift' ? s.shift_start : '00:00', s.timezone);
        body.end = zonedInput(scope === 'shift' && s.shift_end > s.shift_start ? date : addDay(date, 1), scope === 'shift' ? s.shift_end : '00:00', s.timezone)
    }
    if (new Date(body.end) <= new Date(body.start)) throw Error('Report end must be after its start.');
    return body;
}
$('report-scope').onchange = () => {
    const v = $('report-scope').value;
    $('report-date-label').hidden = !['shift', 'day'].includes(v);
    $('report-month-label').hidden = v !== 'month';
    $('report-times').hidden = v !== 'custom'
};

function showReportPreview(report) {
    const s = report.summary;
    $('report-preview-content').innerHTML = `<h3>${esc(report.title)}</h3><p class="preview-period">${report.scope==='collection'?'Selected recording collection':esc(report.period.start)+' → '+esc(report.period.end)}</p><div class="preview-metrics">${[['Recordings',report.recordings.length],['Observed tracks',s.tracks],['Pickup candidates',s.pickups],['Pending signals',s.pending]].map(([l,v])=>`<div><b>${v??'—'}</b><small>${l}</small></div>`).join('')}</div><p class="coverage-note">${sec(s.observed_seconds)} of analysed source footage. Peak ${s.peak_people} observed tracks in one view.</p>${report.warnings.map(w=>`<p class="coverage-note ${report.recordings.length?'':'warning'}">${esc(w)}</p>`).join('')}${report.excluded.length?`<p class="coverage-note warning">${report.excluded.length} excluded: ${esc([...new Set(report.excluded.map(e=>e.reason))].join('; '))}.</p>`:''}`;
}
async function doReport(generate) {
    $('report-preview').disabled = $('report-generate').disabled = true;
    $('report-message').textContent = generate ? 'Generating PDF, editable Word, CSV and JSON…' : 'Calculating coverage and building preview…';
    try {
        const body = reportBody();
        const preview = await api('/api/ops/reports/preview', json('POST', body));
        showReportPreview(preview);
        if (generate) {
            const report = await api('/api/ops/reports', json('POST', body));
            await refreshWorkspace();
            $('report-message').textContent = 'Report saved. All four formats are ready in the report library.';
            toast('Report generated and saved.');
            $('report-library').scrollIntoView({
                behavior: 'smooth',
                block: 'center'
            })
        } else $('report-message').textContent = 'Preview ready. Generate documents to save an immutable snapshot.'
    } catch (e) {
        $('report-message').textContent = e.message;
        toast(e.message, true)
    } finally {
        $('report-preview').disabled = $('report-generate').disabled = false
    }
}
$('report-preview').onclick = () => doReport(false);
$('report-form').onsubmit = e => {
    e.preventDefault();
    doReport(true)
};
$('refresh-reports').onclick = task(refreshWorkspace);

function fillSchedules() {
    $('schedule-list').innerHTML = ops.workspace.schedules.map(s => `<div class="schedule-row"><div><b>${esc(s.name)}</b><p>${esc(s.cadence)} · next ${new Date(s.next_due).toLocaleString(undefined,{timeZone:s.timezone})} (${esc(s.timezone)})${s.last_error?' · Last attempt: '+esc(s.last_error):''}</p></div><button class="danger" data-delete-schedule="${s.id}">Remove</button></div>`).join('') || '<p class="field-note">No schedules yet. Uses the shift times and timezone in System & models.</p>';
    document.querySelectorAll('[data-delete-schedule]').forEach(b => b.onclick = task(async () => {
        await api('/api/ops/schedules/' + b.dataset.deleteSchedule, {
            method: 'DELETE'
        });
        await refreshWorkspace();
        toast('Report schedule removed.')
    }));
}
$('schedule-form').onsubmit = async event => {
    event.preventDefault();
    const button = $('schedule-form').querySelector('[type=submit]');
    button.disabled = true;
    try {
        const s = ops.workspace.settings;
        await api('/api/ops/schedules', json('POST', {
            name: $('schedule-name').value,
            cadence: $('schedule-cadence').value,
            timezone: s.timezone,
            shift_start: s.shift_start,
            shift_end: s.shift_end,
            store_id: $('report-store').value || null
        }));
        await refreshWorkspace();
        $('schedule-name').value = '';
        toast('Report schedule saved. It will run while the app is running.')
    } catch (e) {
        toast(e.message, true)
    } finally {
        button.disabled = false
    }
};
$('command-open').onclick = () => {
    $('command-search').value = '';
    renderCommands();
    $('command-dialog').showModal();
    $('command-search').focus()
};

function renderCommands() {
    const q = $('command-search').value.toLowerCase().trim();
    const views = Object.entries(viewInfo).filter(([key, v]) => v[0].toLowerCase().includes(q)).map(([key, v]) => `<button data-command-view="${key}"><span>${v[0]}</span><small>Workspace view ↗</small></button>`);
    const videos = state.videos.filter(v => (title(v) + ' ' + v.camera_id).toLowerCase().includes(q)).slice(0, 8).map(v => `<button data-command-video="${v.id}"><span>${esc(title(v))}</span><small>Recording ↗</small></button>`);
    $('command-results').innerHTML = [...views, ...videos].join('') || '<p class="field-note">No matching views or recordings.</p>';
    document.querySelectorAll('[data-command-view]').forEach(b => b.onclick = () => {
        $('command-dialog').close();
        navigate(b.dataset.commandView)
    });
    document.querySelectorAll('[data-command-video]').forEach(b => b.onclick = task(async () => {
        $('command-dialog').close();
        await loadVideo(b.dataset.commandVideo);
        navigate('overview')
    }))
}
$('command-search').oninput = renderCommands;
$('command-search').onkeydown = e => {
    if (e.key === 'Enter') {
        e.preventDefault();
        $('command-results button')?.click()
    }
};
document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        $('command-open').click()
    }
    if (e.key === 'Escape' && state.focusTrack && !document.querySelector('dialog[open]')) clearFocus()
});
async function bootOperations() {
    await bootWorkspace();
    await refreshWorkspace();
    ops.booted = true;
    const date = currentDateInZone(ops.workspace.settings.timezone);
    $('report-date').value = date;
    $('report-month').value = date.slice(0, 7);
    $('report-start').value = date + 'T09:00';
    $('report-end').value = date + 'T18:00';
    const view = new URLSearchParams(location.search).get('view');
    if (view && viewInfo[view]) navigate(view);
    syncOperations();
}
bootOperations().catch(e => {notice(e.message,true);toast('Operations workspace: '+e.message,true)});

function renderFeedReviews() {
    const data = ops.key === currentRun() ? ops.data : null;
    if ($('feed-review-state')) $('feed-review-state').textContent = data ? `${data.summary.pending} pending signals` : 'Review required';
    for (const button of document.querySelectorAll('[data-pickup]')) {
        const event = state.events[Number(button.dataset.pickup)],
            review = data?.incidents.find(e => e.id === event?.id)?.review;
        let badge = button.querySelector('.feed-review-status');
        if (review && review.status !== 'pending') {
            if (!badge) {
                badge = document.createElement('span');
                button.querySelector('.event-body').appendChild(badge);
            }
            badge.className = 'feed-review-status review-badge ' + review.status;
            badge.textContent = review.status === 'rejected' ? 'Dismissed by operator' : 'Accepted by operator';
        } else badge?.remove();
    }
}

function rememberRun(key) {
    const params = new URLSearchParams(location.search);
    params.set('run', key);
    if (state.video) params.set('video', state.video.id);
    history.replaceState(null, '', '/?' + params)
}
async function restoreLinkedRun(key, videoId, generation, jobs, histories) {
    if (!key) return false;
    if (key.startsWith('live:') && histories.some(s => 'live:' + s.id === key && ['completed', 'stopped'].includes(s.status))) {
        await showSavedLive(key.slice(5), generation);
        return true;
    }
    const job = jobs.find(j => 'offline:' + j.id === key && j.status === 'completed');
    if (job) {
        await showOffline(job, generation);
        $('run-status').textContent = 'Saved full-video results';
        renderHistory();
        return true;
    }
    if (/^track:[a-f0-9]{64}$/.test(key)) {
        try {
            const job = await api('/api/tracking/jobs/' + key.slice(6));
            if (job.video_id === videoId && job.status === 'completed' && generation === state.generation) {
                await attachTracking(job);
                return true;
            }
        } catch (error) {
            notice('The linked tracking run is unavailable; showing another saved run.')
        }
    }
    return false;
}

function fillRunSelector() {
    const key = currentRun(),
        rows = [...(ops.availableRuns ?? [])];
    if (key && !rows.some(r => r.key === key)) rows.unshift({
        key,
        kind: key.split(':')[0]
    });
    const html = '<option value="">Choose a completed run…</option>' + rows.map(r => `<option value="${esc(r.key)}">${r.kind==='offline'?'Full analysis':r.kind==='track'?'Person tracking':'Live recording run'}${r.created_at?' · '+new Date(r.created_at).toLocaleString():''}</option>`).join('');
    if ($('analysis-run').innerHTML !== html) $('analysis-run').innerHTML = html;
    $('analysis-run').value = key ?? '';
}
$('analysis-run').onchange = task(async () => {
    const key = $('analysis-run').value;
    if (!key) return;
    if (state.dirty || state.tool) throw Error('Save or discard geometry edits before changing runs.');
    if (state.offline) throw Error('Wait for analysis to finish or cancel it.');
    await stopLive();
    if (key.startsWith('live:')) await showSavedLive(key.slice(5));
    else if (key.startsWith('offline:')) {
        await showOffline(await api('/api/action-analysis/jobs/' + key.slice(8)));
        $('run-status').textContent = 'Saved full-video results';
    } else if (key.startsWith('track:')) await attachTracking(await api('/api/tracking/jobs/' + key.slice(6)));
});