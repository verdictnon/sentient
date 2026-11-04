'use strict';
const ops = {
    workspace: null,
    data: null,
    key: null,
    signature: '',
    pending: false,
    contextVideo: null,
    view: 'store',
    track: null,
    review: null,
    ruleRevision: 0,
    booted: false,
    health: null
};
const viewInfo = {
    wall: ['Control room', 'Cameras, activity and store layout.'],
    work: ['Operations board', 'Assign, track and close store work.'],
    shelves: ['Shelves', 'Stock checks, refill work and shelf activity.'],
    cameras: ['Camera simulation', 'Synchronized views of the simulated store.'],
    store: ['Store', 'The day on one screen.'],
    overview: ['Monitor', 'Watch a camera and its signals.'],
    analytics: ['Analytics', 'Traffic, queues, shelves and routes.'],
    incidents: ['Investigations', 'Review candidates and record decisions.'],
    tracking: ['Track explorer', 'Follow anonymous tracks through the camera view.'],
    reports: ['Report centre', 'Shift, daily and monthly reports with traceable data.'],
    geometry: ['Camera setup', 'Map areas and place recordings on the calendar.'],
    system: ['System & models', 'Health, rules and model limits.'],
    help: ['Operator guide', 'How to get the most out of each view.']
};
const VIEW_SECTIONS = {
    wall: ['control-room-section'],
    work: ['work-orders-section'],
    shelves: ['shelf-operations-section'],
    cameras: ['camera-simulation-section'],
    store: ['store-section'],
    overview: ['filmstrip', 'monitor-work'],
    tracking: ['filmstrip', 'monitor-work', 'tracking-section'],
    analytics: ['flow-section', 'analytics-section', 'advanced-analytics'],
    incidents: ['incidents-section'],
    reports: ['reports-section'],
    geometry: ['filmstrip', 'monitor-work', 'context-section'],
    system: ['system-section'],
    help: ['monitor-work']
};
const ALL_SECTIONS = [...new Set(Object.values(VIEW_SECTIONS).flat())];

function currentRun() {
    return state.live ? null : state.lastLive ? 'live:' + state.lastLive : state.opsRunKey
}

function shortTrack(id) {
    return t('Track {id}', {
        id: String(id).split(':').at(-1).padStart(2, '0')
    })
}

function sec(value) {
    return value === null || value === undefined ? '—' : Number(value).toFixed(1) + ' ' + t('s')
}

function clockTime(ms) {
    const total = Math.max(0, Math.round(ms / 1000));
    return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`;
}

function kindLabel(kind) {
    return t({
        shelf: 'Shelf',
        zone: 'Floor area',
        line: 'Doorway line',
        checkout: 'Checkout',
        queue: 'Queue',
        exit: 'Exit'
    } [kind] ?? kind);
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

function navigate(name) {
    if (!viewInfo[name]) name = 'store';
    if (ops.view === 'wall' && name !== 'wall') pauseControlRoom().catch(e=>toast(e.message,true));
    ops.view = name;
    document.body.dataset.view = name;
    const [label, subtitle] = viewInfo[name];
    $('page-label').textContent = t(label);
    $('page-label').title = t(subtitle);
    document.querySelectorAll('[data-nav]').forEach(b => {
        b.classList.toggle('active', b.dataset.nav === name);
        b.setAttribute('aria-current', b.dataset.nav === name ? 'page' : 'false')
    });
    const visible = new Set(VIEW_SECTIONS[name]);
    for (const id of ALL_SECTIONS) $(id).hidden = !visible.has(id);
    if (name === 'geometry' || name === 'help') tab(name);
    else if (state.tab !== 'results') tab('results');
    if (name !== 'cameras') $('sim-player').pause();
    if (name === 'wall') openControlRoom().catch(e=>showWallError(e.message));
    if (name === 'work') loadWorkOrders().catch(e => toast(e.message, true));
    if (name === 'shelves') loadShelfOperations().catch(e => toast(e.message, true));
    if (name === 'cameras') loadCameraSimulation().catch(e => toast(e.message, true));
    if (name === 'reports') refreshWorkspace().catch(e => toast(e.message, true));
    if (name === 'system') refreshHealth().catch(e => toast(e.message, true));
    if (name === 'analytics' || name === 'tracking' || name === 'incidents') refreshInsights().catch(e => toast(e.message, true));
    if (name === 'analytics') renderFlow();
    if (name === 'analytics') renderAnalytics();
    if (name === 'store') renderStore();
    if (name === 'overview' || name === 'tracking') renderTimeline();
    setSimulatedTape();
    history.replaceState(null, '', `/?${new URLSearchParams({...(state.video?{video:state.video.id}:{}),view:name,...(currentRun()?{run:currentRun()}:{} )})}`);
    window.scrollTo({
        top: 0,
        behavior: 'instant'
    });
    closeMenu();
}

function syncOperations() {
    const busy = !!(state.live || state.offline) || !state.video;
    $('tracking-only').disabled = busy;
    $('analysis-run').disabled = busy;
    $('observed-now').textContent = state.video ? t('{n} observed in frame', {
        n: state.tracks.filter(row => row.position_status === 'observed').length
    }) : t('Awaiting observations');
    $('clear-focus').hidden = !state.focusTrack;
    if (state.live && state.focusTrack) renderLiveFocus();
    if (state.video?.id !== ops.contextVideo && state.video) {
        ops.contextVideo = state.video.id;
        ops.track = null;
        state.focusTrack = null;
        $('track-focus').hidden = true;
        $('track-detail').hidden = true;
        fillContext();
        setSimulatedTape();
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
        renderOperationViews();
        renderEvents()
    } finally {
        ops.pending = false;
        if (key !== currentRun()) updateDashboard()
    }
}

function empty(title, detail = 'Select a completed run or analyze a recording to populate this view.') {
    return `<div class="empty-state"><h3>${esc(t(title))}</h3><p>${esc(t(detail))}</p></div>`
}

function renderOperationViews() {
    renderTrackTable();
    renderIncidents();
    renderAnalytics();
    renderTimeline();
    const pending = (ops.data?.incidents ?? []).filter(e => e.review.status === 'pending' && (e.kind === 'pickup' ? ['left_without_checkout', 'lost_before_checkout'].includes(e.crosscheck?.status) : false)).length || ops.data?.summary.pending || 0;
    $('nav-pending').textContent = pending;
    $('nav-pending').hidden = !pending;
    if (ops.track && ops.data?.tracks.some(row => row.id === ops.track)) renderFocus(ops.data.tracks.find(row => row.id === ops.track));
}

// The recording timeline: how full the view was, and where the signals sit.
function renderTimeline() {
    const box = $('timeline'),
        d = ops.data?.video.id === state.video?.id ? ops.data : null;
    if (!state.video) {
        box.hidden = true;
        return
    }
    const rows = d?.timeline ?? [],
        start = state.video.first_media_time_ms ?? 0,
        span = Math.max(1, (state.video.last_media_time_ms ?? 1) - start),
        max = Math.max(1, ...rows.map(row => row.people)),
        step = Math.max(1, Math.ceil(rows.length / 300)),
        points = rows.filter((row, i) => i % step === 0).map(row => `${((row.media_ms-start)/span*1000).toFixed(1)},${(38-row.people/max*34).toFixed(1)}`).join(' ');
    const pins = (d?.incidents ?? []).filter(e => e.kind === 'pickup' || e.kind === 'queue').slice(0, 80).map(e => {
        const left = Math.max(0, Math.min(100, (e.media_ms - start) / span * 100)),
            status = e.crosscheck?.status,
            tone = e.kind === 'queue' ? 'queue' : status === 'left_without_checkout' ? 'flagged' : status === 'reached_checkout' ? 'resolved' : 'pickup',
            label = e.kind === 'queue' ? t('Queue over the limit') : `${t('Pickup candidate')}: ${outcomeLabel(status)}`;
        return `<button class="pin ${tone}" style="left:${left}%" data-seek="${e.start_ms ?? e.media_ms}" ${Chart.tipAttributes(clockTime(e.media_ms-start),label)} aria-label="${esc(label)}"></button>`
    }).join('');
    box.innerHTML = `<div class="timeline-plot" id="timeline-plot"><svg viewBox="0 0 1000 40" preserveAspectRatio="none" aria-hidden="true"><polygon points="0,40 ${points} 1000,40"/></svg>${pins}<span class="playhead" id="playhead"></span></div><div class="timeline-ticks"><span>0:00</span><span>${clockTime(span)}</span></div>`;
    box.hidden = false;
    $('timeline-plot').onclick = task(async event => {
        if (state.live) return;
        const pin = event.target.closest('[data-seek]');
        const rect = $('timeline-plot').getBoundingClientRect();
        await seekRecording(pin ? Number(pin.dataset.seek) : start + (event.clientX - rect.left) / rect.width * span);
    });
    movePlayhead();
}

function movePlayhead() {
    const head = $('playhead');
    if (!head || !state.video) return;
    const start = state.video.first_media_time_ms ?? 0,
        span = Math.max(1, (state.video.last_media_time_ms ?? 1) - start);
    head.style.left = Math.max(0, Math.min(100, (state.ms - start) / span * 100)) + '%';
}

function announceOutcomes(previous, next) {
    const before = new Map(previous.map(e => [e.id, e.crosscheck?.status]));
    for (const event of next) {
        if (event.crosscheck?.status === 'left_without_checkout' && before.get(event.id) !== 'left_without_checkout') toast(t('{track} left without reaching a till. Review the case when the run ends.', {
            track: shortTrack(event.local_track_id)
        }), true);
    }
}

function renderTrackTable() {
    if (!ops.data) {
        $('track-total').textContent = t(state.live ? 'Live tracking active' : 'No selected run');
        $('track-table').innerHTML = `<tr><td colspan="7">${esc(t('Complete a run to inspect track histories. Click boxes during live inference to focus a visible track.'))}</td></tr>`;
        return
    }
    const query = $('track-search').value.trim().toLowerCase(),
        minimum = Number($('track-min').value) || 0,
        areas = new Map(ops.data.areas.map(a => [a.id, t(a.name)]));
    const rows = ops.data.tracks.filter(row => (row.id + ' ' + row.area_ids.map(k => areas.get(k)).join(' ')).toLowerCase().includes(query) && (row.observed_seconds ?? 0) >= minimum);
    const sort = $('track-sort').value;
    rows.sort((a, b) => sort === 'dwell' ? (b.observed_seconds ?? 0) - (a.observed_seconds ?? 0) : sort === 'alerts' ? b.pickups - a.pickups : a.first_ms - b.first_ms);
    $('track-total').textContent = t('{a} of {b} tracks', {
        a: rows.length,
        b: ops.data.tracks.length
    });
    $('track-table').innerHTML = rows.map(row => `<tr><td><span class="track-pill">${esc(shortTrack(row.id))}</span></td><td>${(row.first_ms/1000).toFixed(1)}–${(row.last_ms/1000).toFixed(1)} ${esc(t('s'))}</td><td>${sec(row.observed_seconds)}</td><td>${esc(row.area_ids.map(k=>areas.get(k)??k).join(', ')||t('No mapped area'))}</td><td>${t('{n}% observed',{n:Math.round(row.observed_fraction*100)})}<small>${t('{n} gaps',{n:Math.max(0,row.segments-1)})}</small></td><td>${row.pickups??'—'}</td><td><button class="subtle-button" data-focus-track="${esc(row.id)}">${esc(t('Inspect'))}</button></td></tr>`).join('') || `<tr><td colspan="7">${esc(t('No tracks match these filters.'))}</td></tr>`;
    document.querySelectorAll('[data-focus-track]').forEach(b => b.onclick = task(() => selectTrack(b.dataset.focusTrack, true)));
}
for (const id of ['track-search', 'track-sort', 'track-min']) $(id).addEventListener(id === 'track-search' ? 'input' : 'change', renderTrackTable);
$('tracks-export').onclick = task(() => {
    if (!ops.data) throw Error(t('Select a completed run first.'));
    exportRows(ops.data.tracks.map(row => ({
        ...row,
        area_ids: row.area_ids.join(';')
    })), ['id', 'first_ms', 'last_ms', 'observed_seconds', 'observed_samples', 'predicted_samples', 'observed_fraction', 'segments', 'path_pixels', 'area_ids', 'pickups'], 'sentient-tracks.csv')
});

function journeyStep(step) {
    const time = `<time>${clockTime(step.ms)}</time>`;
    if (step.type === 'crossing') return `<li class="door">${time}<span>${esc(step.flow==='enter'?t('Came in through {name}',{name:t(step.name)}):step.flow==='leave'?t('Left through {name}',{name:t(step.name)}):t('Crossed {name}',{name:t(step.name)}))}</span></li>`;
    if (step.type === 'pickup') return `<li class="pickup">${time}<span>${esc(t('Pickup candidate, {score}%',{score:(step.score*100).toFixed(1)}))}</span></li>`;
    return `<li class="${esc(step.kind)}">${time}<span>${esc(t(step.name))}${step.seconds?` <small>${sec(step.seconds)}</small>`:''}</span></li>`;
}

function journeyList(steps) {
    const shown = steps.filter(step => step.type !== 'visit' || (step.seconds ?? 0) >= 2 || ['checkout', 'queue', 'exit'].includes(step.kind));
    return shown.length ? `<ol class="journey">${shown.map(journeyStep).join('')}</ol>` : `<p class="field-note">${esc(t('No mapped areas on this track.'))}</p>`;
}
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
    const row = data.track;
    $('track-detail').hidden = false;
    $('track-detail').innerHTML = `<div class="detail-header"><h3>${esc(shortTrack(identity))}</h3><span class="badge">${esc(t('Camera local'))}</span><button id="detail-clear" class="subtle-button">${esc(t('Clear selection'))}</button></div><div class="detail-grid"><a class="detail-image" href="${evidenceURL(identity,row.first_ms)}" download><img src="${evidenceURL(identity,row.first_ms)}" alt="${esc(t('Observed source frame for {track}',{track:shortTrack(identity)}))}"></a><div><div class="track-stat-grid"><div><small>${esc(t('First observed'))}</small><b>${sec(row.first_ms/1000)}</b></div><div><small>${esc(t('Last observed'))}</small><b>${sec(row.last_ms/1000)}</b></div><div><small>${esc(t('Observed time'))}</small><b>${sec(row.observed_seconds)}</b></div><div><small>${esc(t('Gaps'))}</small><b>${Math.max(0,row.segments-1)}</b></div></div><div class="toolbar"><button id="track-first" class="primary">${esc(t('Jump to first observation'))}</button><a class="button-link" href="${evidenceURL(identity,row.first_ms)}" download>${esc(t('Evidence PNG'))}</a><button id="trajectory-export">${esc(t('Trajectory CSV'))}</button></div><p class="field-note">${esc(t('{n}% of sampled track records are observed. Predicted boxes do not add dwell. Route length is {px} image pixels.',{n:Math.round(row.observed_fraction*100),px:row.path_pixels??'—'}))}</p></div><div class="detail-journey"><h4>${esc(t('Journey'))}</h4>${journeyList(data.journey ?? [])}</div></div><div class="visit-ledger"><h3>${esc(t('Area visit ledger'))}</h3>${data.visits.map(v=>`<div class="visit-row"><span>${esc(t(v.name))}</span><span>${(v.start_ms/1000).toFixed(1)}–${(v.end_ms/1000).toFixed(1)} ${esc(t('s'))}</span><span>${esc(t('{time} observed',{time:sec(v.observed_seconds)}))}</span><span class="badge">${esc(t(v.entry_observed&&v.end_observed?'Observed entry and exit':'Partial visit'))}</span><button class="subtle-button" data-visit-ms="${v.start_ms}">${esc(t('Inspect'))}</button></div>`).join('')||`<p class="field-note">${esc(t('No mapped area visits for this track.'))}</p>`}</div>`;
    $('detail-clear').onclick = clearFocus;
    $('track-first').onclick = task(async () => {
        navigate('tracking');
        await seekRecording(row.first_ms)
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

function clipURL(identity, ms) {
    return `/api/ops/runs/${encodeURIComponent(currentRun())}/tracks/${encodeURIComponent(identity)}/clip?media_ms=${ms}`
}

function renderFocus(track) {
    $('track-focus').hidden = false;
    $('track-focus').innerHTML = `<div class="focus-title"><b>${esc(shortTrack(track.id))}</b><button id="focus-dismiss" class="icon-button" aria-label="${esc(t('Clear selected track'))}"><svg class="icon"><use href="#i-close"/></svg></button></div><div class="focus-stats"><div><small>${esc(t('Observed time'))}</small><b>${sec(track.observed_seconds)}</b></div><div><small>${esc(t('Pickup candidates'))}</small><b>${track.pickups??'—'}</b></div></div><div class="toolbar"><button id="focus-investigate" class="subtle-button">${esc(t('Track details'))}</button><button id="focus-start" class="subtle-button">${esc(t('First seen {time}',{time:sec(track.first_ms/1000)}))}</button></div>`;
    $('focus-dismiss').onclick = clearFocus;
    $('focus-investigate').onclick = task(() => selectTrack(track.id, true));
    $('focus-start').onclick = task(() => seekRecording(track.first_ms));
}

function renderLiveFocus() {
    const row = state.tracks.find(item => item.local_track_id === state.focusTrack);
    $('track-focus').hidden = false;
    $('track-focus').innerHTML = `<div class="focus-title"><b>${esc(shortTrack(state.focusTrack))}</b><button id="focus-dismiss" class="icon-button" aria-label="${esc(t('Clear selected track'))}"><svg class="icon"><use href="#i-close"/></svg></button></div><p class="field-note">${esc(t(row?(row.position_status==='observed'?'Observed in this frame.':'Tracker prediction.'):'Temporarily outside the visible track set.'))} ${esc(t('Full trajectory and evidence become available when the run is saved.'))}</p>`;
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

const REVIEW_STATUSES = ['left_without_checkout', 'lost_before_checkout'];

function renderCaseScope(totals) {
    const whole = store.data?.kpis?.needs_review,
        sameStore = state.video?.store_id === store.id,
        differs = sameStore && whole !== null && whole !== undefined && whole !== totals?.needs_review;
    $('case-scope').hidden = !differs;
    if (differs) $('case-scope').textContent = t('These cases come from one saved run, {time} of footage. Store totals cover the whole period.', {
        time: clockTime(state.video.indexed_span_ms)
    });
}

function renderCaseSummary() {
    const totals = ops.data?.crosscheck?.totals;
    renderCaseScope(totals);
    if (!totals || !ops.data.incidents.some(e => e.kind === 'pickup')) {
        $('case-summary').hidden = true;
        return
    }
    $('case-summary').hidden = false;
    const cells = [
        ['left_without_checkout', 'Left without a till', 'Saw the pickup, then the door. No till in between.'],
        ['lost_before_checkout', 'Track lost', 'The track ended before a till. It may have split in two.'],
        ['still_in_view', 'Still in view', 'The recording ended before the outcome.'],
        ['reached_checkout', 'Reached a till', 'Nothing to review.'],
        ['checkout_not_mapped', 'No till on this camera', 'Draw a checkout area to check these.']
    ].filter(([key]) => totals[key] || !['checkout_not_mapped', 'still_in_view'].includes(key));
    $('case-summary').innerHTML = cells.map(([key, label, note]) => `<button class="case-count ${key}" data-case-filter="${key}"><b>${number(totals[key])}</b><span>${esc(t(label))}</span><small>${esc(t(note))}</small></button>`).join('');
    document.querySelectorAll('[data-case-filter]').forEach(b => b.onclick = () => {
        $('incident-kind').value = REVIEW_STATUSES.includes(b.dataset.caseFilter) ? 'review' : 'pickup';
        renderIncidents()
    });
}

function incidentTitle(e) {
    if (e.kind === 'pickup') return t('Pickup candidate');
    return t(e.name);
}

function incidentKind(e) {
    return t({
        pickup: 'Pickup',
        dwell: 'Extended dwell',
        crowding: 'Crowding',
        queue: 'Queue'
    } [e.kind] ?? e.kind);
}

function renderIncidents() {
    renderCaseSummary();
    if (!ops.data) {
        $('incident-list').innerHTML = empty(state.live ? 'The run is still being saved' : 'No completed run selected');
        $('incident-total').textContent = '';
        return
    }
    const kind = $('incident-kind').value,
        status = $('incident-status').value;
    const events = ops.data.incidents.filter(e => (kind === 'all' || (kind === 'review' ? REVIEW_STATUSES.includes(e.crosscheck?.status) : e.kind === kind)) && (status === 'all' || e.review.status === status)).sort((a, b) => (a.priority ?? 9) - (b.priority ?? 9) || a.media_ms - b.media_ms);
    $('incident-total').textContent = t('{n} signals, {p} pending', {
        n: events.length,
        p: ops.data.summary.pending
    });
    $('incident-list').innerHTML = events.map(e => {
        const outcome = e.crosscheck?.status,
            image = e.track_id && e.kind === 'pickup' ? `<img loading="lazy" src="${evidenceURL(e.track_id,e.media_ms)}&crop=true" alt="">` : '',
            facts = [e.track_id ? shortTrack(e.track_id) : t('Area occupancy'), clockTime(e.media_ms), e.score !== undefined ? (e.score * 100).toFixed(1) + '%' : null, e.count ? t('peak {n} tracks', {
                n: e.count
            }) : null, e.seconds ? t('{time} observed', {
                time: sec(e.seconds)
            }) : null, e.crosscheck?.shelf_name ? t(e.crosscheck.shelf_name) : null].filter(Boolean).join(', ');
        return `<button class="signal-card ${outcome??e.kind}" data-signal="${esc(e.id)}">${image}<span class="signal-body"><span class="signal-top"><span class="signal-type">${esc(incidentKind(e))}</span><span class="review-badge ${e.review.status}">${esc(t(e.review.status==='rejected'?'Dismissed':e.review.status==='accepted'?'Accepted':'Pending review'))}</span></span><b class="signal-title">${esc(outcome?outcomeLabel(outcome):incidentTitle(e))}</b><span class="signal-facts">${esc(facts)}</span>${e.review.notes?`<span class="review-note">${esc(e.review.notes).slice(0,180)}</span>`:''}</span></button>`
    }).join('') || empty('No signals match these filters', 'Change a filter to see more signals.');
    document.querySelectorAll('[data-signal]').forEach(b => b.onclick = () => openIncident(b.dataset.signal));
}
$('incident-kind').onchange = $('incident-status').onchange = renderIncidents;

function verdictText(e) {
    const check = e.crosscheck;
    if (!check) return '';
    return t({
        left_without_checkout: 'The same track was followed from the pickup to the door without entering a checkout area. Look at the clip before deciding.',
        lost_before_checkout: 'The track ended before a checkout. It may have left the camera view or split into a new ID.',
        still_in_view: 'The recording ended while the track was still in view, so the outcome is unknown.',
        reached_checkout: 'The track entered a checkout area after the pickup.',
        checkout_not_mapped: 'This camera has no mapped checkout, so the pickup cannot be cross-checked.',
        unavailable: 'Elapsed time is unknown for this footage.'
    } [check.status] ?? '');
}
async function openIncident(id) {
    const e = ops.data?.incidents.find(item => item.id === id);
    if (!e) return;
    ops.review = {
        ...e,
        run_key: currentRun()
    };
    $('incident-title').textContent = e.crosscheck ? outcomeLabel(e.crosscheck.status) : incidentTitle(e);
    $('incident-meta').textContent = [ops.data.video.camera_id, clockTime(e.media_ms), e.track_id ? shortTrack(e.track_id) : t('Area signal'), e.score !== undefined ? t(state.video?.source_profile?.simulated ? 'simulated score {n}%' : 'model score {n}%', {
        n: (e.score * 100).toFixed(1)
    }) : null].filter(Boolean).join(', ');
    $('incident-evidence').innerHTML = e.track_id ? `<video id="incident-video" src="${clipURL(e.track_id,e.media_ms)}" poster="${evidenceURL(e.track_id,e.media_ms)}" controls muted autoplay playsinline></video>` : `<p>${esc(t(e.kind==='dwell'?'Extended observed presence in {name}. Inspect the recording before recording your decision.':'Configured occupancy threshold exceeded in {name}. Inspect the recording before recording your decision.',{name:t(e.name)}))}</p>`;
    $('incident-image').hidden = !e.track_id;
    $('incident-clip').hidden = !e.track_id;
    if (e.track_id) {
        $('incident-image').href = evidenceURL(e.track_id, e.media_ms);
        $('incident-clip').href = clipURL(e.track_id, e.media_ms);
    }
    $('incident-decision').value = e.review.status;
    $('incident-notes').value = e.review.notes ?? '';
    $('incident-message').textContent = '';
    $('incident-journey').innerHTML = e.crosscheck ? `<p class="verdict ${e.crosscheck.status}">${esc(verdictText(e))}</p>` : '';
    $('incident-dialog').showModal();
    if (e.track_id) {
        const data = await api(`/api/ops/runs/${encodeURIComponent(ops.review.run_key)}/tracks/${encodeURIComponent(e.track_id)}`);
        if (ops.review?.id === id) $('incident-journey').innerHTML += journeyList(data.journey ?? []);
    }
}
$('incident-close').onclick = () => {
    $('incident-video')?.pause();
    $('incident-dialog').close()
};
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
        $('incident-video')?.pause();
        $('incident-dialog').close();
        toast(t('Review saved with this source run.'));
    } catch (e) {
        $('incident-message').textContent = e.message
    } finally {
        button.disabled = false
    }
};
$('incidents-export').onclick = task(() => {
    if (!ops.data) throw Error(t('Select a completed run first.'));
    exportRows(ops.data.incidents.map(e => ({
        ...e,
        review_status: e.review.status,
        review_notes: e.review.notes,
        crosscheck_status: e.crosscheck?.status ?? ''
    })), ['id', 'kind', 'track_id', 'media_ms', 'end_ms', 'name', 'score', 'crosscheck_status', 'review_status', 'review_notes'], 'sentient-signals.csv')
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
        [t('Most in view'), number(s.peak_people)],
        [t('Observed footage'), sec(s.observed_seconds)],
        [t('Entries'), s.entries ?? '—'],
        [t('Exits'), s.exits ?? '—'],
        [t('Observed sample share'), Math.round(s.observed_fraction * 100) + '%']
    ].map(([label, value]) => statTile(label, value)).join('');
    const rows = d.timeline;
    if (ops.view === 'analytics' && rows.length) {
        Chart.area($('occupancy-chart'), rows.map(row => ({
            x: row.media_ms,
            y: row.people
        })), {
            height: 190,
            aria: t('Observed occupancy along the recording'),
            tick: x => clockTime(x),
            tipValue: point => t('{n} people', {
                n: point.y
            }),
            tipLabel: point => clockTime(point.x)
        });
        $('occupancy-chart').onclick = task(async event => {
            const rect = $('occupancy-chart').getBoundingClientRect(),
                span = rows.at(-1).media_ms - rows[0].media_ms,
                ms = rows[0].media_ms + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * span;
            navigate('tracking');
            await seekRecording(ms)
        });
    }
    const flows = d.routes?.flows ?? [];
    $('transition-chart').innerHTML = flows.length ? flows.slice(0, 8).map(row => `<div class="transition-row"><span>${esc(t(row.from_name))}</span><span class="route-arrow" aria-hidden="true"></span><span>${esc(t(row.to_name))}</span><b>${row.count}</b></div>`).join('') : `<p class="empty-chart">${esc(t('No movement between distinct areas was observed.'))}</p>`;
    $('crossing-cards').innerHTML = d.line_counts.length ? d.line_counts.map(row => {
        const shape = d.geometry.geometry.shapes.find(s => s.id === row.id),
            positive = shape?.positive_means === 'enter' ? t('in') : shape?.positive_means === 'leave' ? t('out') : t('positive'),
            negative = shape?.positive_means === 'enter' ? t('out') : shape?.positive_means === 'leave' ? t('in') : t('negative');
        return `<div class="crossing-row"><span>${esc(t(row.name))}</span><b>${row.positive}</b><span>${esc(positive)}</span><b>${row.negative}</b><span>${esc(negative)}</span></div>`
    }).join('') : `<p class="empty-chart">${esc(t('Draw a doorway line to measure directional crossings.'))}</p>`;
    $('queue-summary').innerHTML = `<div class="queue-values"><div><b>${d.areas.some(a=>a.kind==='queue')?d.queue.peak_people:'—'}</b><small>${esc(t('Longest observed line'))}</small></div><div><b>${sec(d.queue.median_complete_wait_seconds)}</b><small>${esc(t('Median entry to checkout'))}</small></div><div><b>${d.queue.abandoned??'—'}</b><small>${esc(t('Gave up waiting'))}</small></div></div><p class="field-note">${esc(t('{n} observed transitions, {m} with an observed waiting-area entry. Payment and actual service are not inferred.',{n:d.queue.observed_waits.length,m:d.queue.observed_waits.filter(w=>w.complete_entry).length}))}</p>`;
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
    const v = ops.workspace?.recordings.find(row => row.id === state.video?.id),
        context = v?.context ?? {};
    $('context-label').value = context.label ?? '';
    $('context-start').value = context.started_at_utc ? deviceInput(context.started_at_utc) : '';
    $('context-shift').value = t(context.shift_label ?? '');
    $('context-notes').value = t(context.notes ?? '');
    $('context-message').textContent = t(context.time_basis === 'simulated' ? 'Time basis: simulated.' : context.started_at_utc ? 'Time basis: explicitly assigned by an operator.' : 'Undated footage is excluded from calendar reports.');
}

function deviceInput(value) {
    const d = new Date(value);
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
$('context-form').onsubmit = async event => {
    event.preventDefault();
    if (!state.video) {
        toast(t('Select a recording first.'), true);
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
        await loadStore();
        $('context-message').textContent = t('Recording context saved. Calendar reports now use this explicitly assigned time.');
        toast(t('Recording context saved.'))
    } catch (e) {
        $('context-message').textContent = e.message
    }
};

function fillQueueMappings() {
    if (!state.geometry) return;
    for (const kind of ['queue', 'checkout']) {
        const select = $('active-' + kind),
            shapes = state.geometry.shapes.filter(s => s.kind === kind),
            html = `<option value="">${esc(t('Not configured'))}</option>` + shapes.map(s => `<option value="${esc(s.id)}">${esc(t(s.name))}</option>`).join('');
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
    if (profile.high_threshold < .15 || profile.high_threshold > .95 || profile.max_lost_ms < 200 || profile.max_lost_ms > 5000) throw Error(t('Use confidence 0.15–0.95 and lost buffer 200–5000 ms.'));
    localStorage.setItem('sentient.tracking-profile', JSON.stringify(profile));
    $('tracking-profile-message').textContent = t('Profile saved. It applies to the next full-video or tracking-only run.');
    toast(t('Tracking profile saved.'))
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
    $('run-status').textContent = t('Person tracking complete');
    notice(t('Person detection and tracking saved. Open Track explorer to investigate identities and routes.'));
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
            $('run-status').textContent = t('Person tracking: {n}%', {
                n: Math.round(current.progress * 100)
            });
            $('progress').value = current.progress;
            await new Promise(r => setTimeout(r, 500));
            current = await api('/api/tracking/jobs/' + job.id);
            if (generation !== state.generation) return
        }
        if (current.status === 'completed') await attachTracking(current);
        else if (current.status === 'cancelled') {
            notice(t('Tracking cancelled.'));
            $('run-status').textContent = t('Cancelled')
        } else throw Error(current.error ?? t('Tracking failed'));
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
    if (state.dirty || state.tool) throw Error(t('Finish drawing and save geometry before analysis.'));
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
    $('setting-item-value').value = s.avg_item_value ?? 0;
    $('setting-currency').value = s.currency ?? 'KZT';
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
        queue_limit: Number($('setting-queue').value),
        avg_item_value: Number($('setting-item-value').value) || 0,
        currency: $('setting-currency').value || 'KZT'
    };
    try {
        await api('/api/ops/settings', json('PUT', body));
        ops.ruleRevision++;
        await refreshWorkspace();
        fillSettings();
        await refreshInsights(true);
        await loadStore();
        $('settings-message').textContent = t('Settings saved. Signal rules were recalculated from the saved observations.');
        toast(t('Workspace settings saved.'))
    } catch (e) {
        $('settings-message').textContent = e.message
    }
};
async function refreshHealth() {
    const h = await api('/api/ops/health');
    ops.health = h;
    $('system-health').innerHTML = [
        statTile(t('CPU usage'), h.cpu_percent + '%'),
        statTile(t('System memory'), `${h.ram_used_gb} / ${h.ram_total_gb} GB`),
        statTile(t('GPU'), h.gpu ? h.gpu.utilization + '%' : t('Unavailable')),
        statTile(t('GPU memory'), h.gpu ? `${(h.gpu.memory_used_mb/1024).toFixed(1)} / ${(h.gpu.memory_total_mb/1024).toFixed(1)} GB` : '—')
    ].join('');
    const reviewed = ops.data ? ops.data.summary.accepted + ops.data.summary.rejected : 0;
    $('model-readiness').innerHTML = `<div class="model-row"><b>${esc(t(h.action_model.available?'Pickup model available locally':'Pickup model not configured'))}</b><code>${esc(h.model_id??t('No configured model'))}</code><p>${esc(t('Score threshold {n}. Continuous store-footage precision is unmeasured.',{n:h.pickup_policy?.threshold?.toFixed(2)??t('unknown')}))}</p></div><div class="model-row"><b>${esc(t('RT-DETRv2 and time-aware local association'))}</b><p>${esc(t(h.detector_cached?'Live detector cached.':'Live detector loads on demand.'))} ${esc(t('The tracker uses motion and two-stage IoU association. It has no appearance embeddings or cross-camera identity.'))}</p></div><div class="model-row"><b>${esc(t('Operator review coverage'))}</b><p>${esc(t('{n} reviewed signals in the selected run, {m} pending. Review decisions are annotations, not an independent accuracy benchmark.',{n:reviewed,m:ops.data?.summary.pending??0}))}</p></div><div class="model-row"><b>${esc(t(h.scheduler_running?'Report scheduler running':'Report scheduler stopped'))}</b><p>${esc(t('Schedules execute while this app is running. Results stay in the local report library.'))}</p></div>${h.limitations.map(row=>`<p class="field-note">${esc(t(row))}</p>`).join('')}`;
}
$('health-refresh').onclick = task(refreshHealth);

function fillReports() {
    const previously = new Set(Array.from(document.querySelectorAll('[data-report-source]:checked'), e => e.value));
    const initial = !$('report-sources').children.length;
    const recordings = ops.workspace.recordings.filter(v => (v.runs.length || state.demo.includes(v.id)) && (!$('report-store').value || v.store_id === $('report-store').value)).sort((a, b) => Number(state.demo.includes(b.id)) - Number(state.demo.includes(a.id)));
    const store_ = $('report-store').value;
    const storeHTML = `<option value="">${esc(t('All selected stores'))}</option>` + [...new Set(ops.workspace.recordings.filter(v => v.runs.length).map(v => v.store_id))].sort().map(s => `<option value="${esc(s)}">${esc(storeName({id:s,simulated:s==='sim-store'}))}</option>`).join('');
    if ($('report-store').innerHTML !== storeHTML) {
        $('report-store').innerHTML = storeHTML;
        $('report-store').value = store_
    }
    $('report-sources').innerHTML = recordings.map(v => `<label class="report-source"><input data-report-source type="checkbox" value="${v.id}" ${initial?v.simulated===reportBasis()&&(state.demo.includes(v.id)||v.id===state.video?.id)?'checked':'':previously.has(v.id)?'checked':''}><span>${esc(v.context.label||title({display_name:v.name}))}<small>${esc([v.camera_id,t(v.runs.length?'Analysis available':'No completed run'),v.context.started_at_utc?new Date(v.context.started_at_utc).toLocaleString(locale()):t('Undated')].join(', '))}</small></span></label>`).join('') || `<p class="field-note">${esc(t('Analyze a recording to add it to reporting.'))}</p>`;
    $('report-zone-note').textContent = t('Calendar timezone: {zone}. Undated footage appears only in selected-recording reports.', {
        zone: ops.workspace.settings.timezone
    });
    $('report-library').innerHTML = ops.workspace.reports.map(r => `<article class="report-file"><h3>${esc(title({display_name:r.title}))}</h3>${r.simulated?`<p class="report-flag">${esc(t('Simulated'))}</p>`:''}<p>${esc(new Date(r.created_at).toLocaleString(locale()))}, ${esc(t('{n} recordings',{n:r.recordings}))}</p><p>${esc(t('{a} local track IDs, {b} candidates',{a:r.summary.tracks,b:r.summary.pickups??t('unscored')}))}${r.coverage_status==='no_matching_recordings'?esc(', '+t('no matching coverage')):''}</p><div class="report-downloads">${['pdf','docx','csv','json'].map(f=>`<a href="/api/ops/reports/${r.id}/${f}" download>${f.toUpperCase()}</a>`).join('')}</div></article>`).join('') || empty('Your report library is empty', 'Generate a report and it stays here.');
}
$('report-store').onchange = fillReports;
$('report-select-all').onclick = () => document.querySelectorAll('[data-report-source]').forEach(e => {
    const recording = ops.workspace.recordings.find(v => v.id === e.value);
    e.checked = !!recording?.runs.length && recording.simulated === reportBasis();
});

// Simulated and real recordings are reported separately, so a selection follows the open camera.
function reportBasis() {
    return !!state.video?.source_profile?.simulated;
}

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
    if (!ids.length) throw Error(t('Select at least one recording.'));
    const scope = $('report-scope').value,
        s = ops.workspace.settings;
    const body = {
        title: $('report-title').value.trim(),
        scope,
        video_ids: ids,
        store_id: $('report-store').value || null,
        notes: $('report-notes').value,
        lang: currentLanguage
    };
    if (!body.title) throw Error(t('Enter a report title.'));
    if (scope === 'collection') return body;
    if (scope === 'custom') {
        if (!$('report-start').value || !$('report-end').value) throw Error(t('Set report start and end.'));
        const [startDate, startTime] = $('report-start').value.split('T'), [endDate, endTime] = $('report-end').value.split('T');
        body.start = zonedInput(startDate, startTime, s.timezone);
        body.end = zonedInput(endDate, endTime, s.timezone)
    } else if (scope === 'month') {
        const month = $('report-month').value;
        if (!month) throw Error(t('Select a month.'));
        const start = month + '-01',
            d = new Date(start + 'T12:00:00Z');
        d.setUTCMonth(d.getUTCMonth() + 1);
        body.start = zonedInput(start, '00:00', s.timezone);
        body.end = zonedInput(d.toISOString().slice(0, 10), '00:00', s.timezone)
    } else {
        const date = $('report-date').value;
        if (!date) throw Error(t('Select a report date.'));
        body.start = zonedInput(date, scope === 'shift' ? s.shift_start : '00:00', s.timezone);
        body.end = zonedInput(scope === 'shift' && s.shift_end > s.shift_start ? date : addDay(date, 1), scope === 'shift' ? s.shift_end : '00:00', s.timezone)
    }
    if (new Date(body.end) <= new Date(body.start)) throw Error(t('Report end must be after its start.'));
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
    $('report-preview-content').innerHTML = `<h3>${esc(report.title)}</h3><p class="preview-period">${esc(report.scope==='collection'?t('Selected recording collection'):report.period.start+' — '+report.period.end)}</p><div class="preview-metrics">${[['Recordings',report.recordings.length],['Observed tracks',s.tracks],['Pickup candidates',s.pickups],['Pending signals',s.pending]].map(([l,v])=>`<div><b>${v??'—'}</b><small>${esc(t(l))}</small></div>`).join('')}</div><p class="coverage-note">${esc(t('{time} of analysed source footage. Peak {n} observed tracks in one view.',{time:sec(s.observed_seconds),n:s.peak_people}))}</p>${report.warnings.map(w=>`<p class="coverage-note ${report.recordings.length?'':'warning'}">${esc(t(w))}</p>`).join('')}${report.excluded.length?`<p class="coverage-note warning">${esc(t('{n} excluded: {why}.',{n:report.excluded.length,why:[...new Set(report.excluded.map(e=>t(e.reason)))].join('; ')}))}</p>`:''}`;
}
async function doReport(generate) {
    $('report-preview').disabled = $('report-generate').disabled = true;
    $('report-message').textContent = t(generate ? 'Generating PDF, editable Word, CSV and JSON…' : 'Calculating coverage and building preview…');
    try {
        const body = reportBody();
        const preview = await api('/api/ops/reports/preview', json('POST', body));
        showReportPreview(preview);
        if (generate) {
            await api('/api/ops/reports', json('POST', body));
            await refreshWorkspace();
            $('report-message').textContent = t('Report saved. All four formats are ready in the report library.');
            toast(t('Report generated and saved.'));
            $('report-library').scrollIntoView({
                behavior: 'smooth',
                block: 'center'
            })
        } else $('report-message').textContent = t('Preview ready. Generate documents to save a frozen snapshot.')
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
    $('schedule-list').innerHTML = ops.workspace.schedules.map(s => `<div class="schedule-row"><div><b>${esc(s.name)}</b><p>${esc(t({shift:'End of shift',daily:'Daily at midnight',monthly:'Month end'}[s.cadence]))}, ${esc(t('next {when}',{when:new Date(s.next_due).toLocaleString(locale(),{timeZone:s.timezone})}))} (${esc(s.timezone)})${s.last_error?esc('. '+t('Last attempt: {error}',{error:s.last_error})):''}</p></div><button class="danger" data-delete-schedule="${s.id}">${esc(t('Remove'))}</button></div>`).join('') || `<p class="field-note">${esc(t('No schedules yet. Uses the shift times and timezone in System & models.'))}</p>`;
    document.querySelectorAll('[data-delete-schedule]').forEach(b => b.onclick = task(async () => {
        await api('/api/ops/schedules/' + b.dataset.deleteSchedule, {
            method: 'DELETE'
        });
        await refreshWorkspace();
        toast(t('Report schedule removed.'))
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
        toast(t('Report schedule saved. It will run while the app is running.'))
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
    const views = Object.entries(viewInfo).filter(([, v]) => t(v[0]).toLowerCase().includes(q)).map(([key, v]) => `<button data-command-view="${key}"><span>${esc(t(v[0]))}</span><small>${esc(t('View'))}</small></button>`);
    const videos = state.videos.filter(v => (title(v) + ' ' + v.camera_id).toLowerCase().includes(q)).slice(0, 8).map(v => `<button data-command-video="${v.id}"><span>${esc(title(v))}</span><small>${esc(t('Recording'))}</small></button>`);
    $('command-results').innerHTML = [...views, ...videos].join('') || `<p class="field-note">${esc(t('No matching views or recordings.'))}</p>`;
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
        document.querySelector('#command-results button')?.click()
    }
};
document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        $('command-open').click()
    }
    if (e.key === 'Escape' && state.focusTrack && !document.querySelector('dialog[open]')) clearFocus();
    // Number keys move between the main views, which helps during a live demo.
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName ?? '') || document.querySelector('dialog[open]');
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (/^[1-6]$/.test(e.key)) navigate(['store', 'overview', 'analytics', 'incidents', 'tracking', 'reports'][Number(e.key) - 1]);
    if (!['overview', 'tracking'].includes(ops.view) || !state.video || state.live || document.activeElement?.tagName === 'BUTTON') return;
    if (e.code === 'Space') {
        e.preventDefault();
        $('play-toggle').click()
    }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') seekRecording(Math.max(0, state.ms + (e.key === 'ArrowLeft' ? -1000 : 1000))).catch(error => toast(error.message, true));
});

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    $('theme-icon').setAttribute('href', theme === 'dark' ? '#i-sun' : '#i-moon');
    $('theme-toggle').setAttribute('aria-label', t(theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'));
    $('theme-toggle').title = $('theme-toggle').getAttribute('aria-label');
    document.querySelector('meta[name=theme-color]').content = theme === 'dark' ? '#0c1424' : '#ffffff';
    try {
        localStorage.setItem('sentient.theme', theme)
    } catch {}
}
$('theme-toggle').onclick = () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
};
$('lang-toggle').onclick = () => setLanguage(currentLanguage === 'ru' ? 'en' : 'ru');

async function bootOperations() {
    const params = new URLSearchParams(location.search),
        requested = params.get('view'),
        savedRun = params.get('video');
    applyTheme(document.documentElement.dataset.theme || 'light');
    $('lang-toggle').textContent = currentLanguage === 'ru' ? 'EN' : 'RU';
    $('report-title').value = t($('report-title').value);
    await bootWorkspace();
    await refreshWorkspace();
    ops.booted = true;
    const date = currentDateInZone(ops.workspace.settings.timezone);
    $('report-date').value = date;
    $('report-month').value = date.slice(0, 7);
    $('report-start').value = date + 'T09:00';
    $('report-end').value = date + 'T18:00';
    store.demo = await api('/api/ops/demo-store');
    await loadStore({
        print: true
    });
    if (store.demo.status === 'building') startDemoWatch();
    const cameras = await api('/api/control-room/manifest');
    navigate(requested && viewInfo[requested] ? requested : cameras.stores.length ? 'wall' : savedRun ? 'overview' : 'store');
    syncOperations();
}

function startDemoWatch() {
    clearInterval(store.demoTimer);
    store.demoTimer = setInterval(task(async () => {
        store.demo = await api('/api/ops/demo-store');
        if (store.demo.status === 'ready') location.reload();
        else renderOnboarding();
    }), 800);
}
bootOperations().catch(e => {
    notice(e.message, true);
    toast(t('Workspace failed to start: {error}', {
        error: e.message
    }), true)
});

function renderFeedReviews() {
    const data = ops.key === currentRun() ? ops.data : null;
    if ($('feed-review-state')) $('feed-review-state').textContent = data ? t('{n} pending signals', {
        n: data.summary.pending
    }) : t('Review required');
    for (const button of document.querySelectorAll('[data-pickup]')) {
        const event = state.events[Number(button.dataset.pickup)],
            review = data?.incidents.find(e => e.id === event?.id)?.review;
        let badge = button.querySelector('.feed-review-status');
        if (review && review.status !== 'pending') {
            if (!badge) {
                badge = document.createElement('span');
                button.appendChild(badge);
            }
            badge.className = 'feed-review-status ' + review.status;
            badge.textContent = t(review.status === 'rejected' ? 'Dismissed by operator' : 'Accepted by operator');
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
        $('run-status').textContent = t('Saved full-video results');
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
            notice(t('The linked tracking run is unavailable; showing another saved run.'))
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
    const html = `<option value="">${esc(t('Choose a completed run…'))}</option>` + rows.map(r => `<option value="${esc(r.key)}">${esc(t(r.kind==='offline'?'Full analysis':r.kind==='track'?'Person tracking':'Live recording run'))}${r.created_at?', '+esc(new Date(r.created_at).toLocaleString(locale(),{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})):''}</option>`).join('');
    if ($('analysis-run').innerHTML !== html) $('analysis-run').innerHTML = html;
    $('analysis-run').value = key ?? '';
}
$('analysis-run').onchange = task(async () => {
    const key = $('analysis-run').value;
    if (!key) return;
    if (state.dirty || state.tool) throw Error(t('Save or discard geometry edits before changing runs.'));
    if (state.offline) throw Error(t('Wait for analysis to finish or cancel it.'));
    await stopLive();
    if (key.startsWith('live:')) await showSavedLive(key.slice(5));
    else if (key.startsWith('offline:')) {
        await showOffline(await api('/api/action-analysis/jobs/' + key.slice(8)));
        $('run-status').textContent = t('Saved full-video results');
    } else if (key.startsWith('track:')) await attachTracking(await api('/api/tracking/jobs/' + key.slice(6)));
});
