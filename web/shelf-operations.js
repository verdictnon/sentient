'use strict';
const shelfOps = {
    data: null,
    video: null,
    edit: null,
    request: 0
};
const cameraSim = {
    data: null,
    raf: null
};

async function loadShelfOperations(videoId) {
    const request = ++shelfOps.request;
    const workspace = await api('/api/ops/workspace');
    if (request !== shelfOps.request) return;
    const recordings = workspace.recordings;
    const select = $('shelf-recording');
    select.innerHTML = recordings.map(v => `<option value="${esc(v.id)}">${esc(v.store_name ?? v.store_id)} · ${esc(v.name)}${v.simulated ? ' · '+esc(t('Simulated')) : ''}</option>`).join('');
    shelfOps.video = videoId ?? shelfOps.video ?? state.video?.id ?? recordings[0]?.id;
    if (!recordings.some(v => v.id === shelfOps.video)) shelfOps.video = recordings[0]?.id;
    select.value = shelfOps.video ?? '';
    const data = shelfOps.video ? await api(`/api/ops/recordings/${shelfOps.video}/shelves`) : null;
    if (request !== shelfOps.request) return;
    shelfOps.data = data;
    renderShelfOperations();
}

function stockStatus(status) {
    return t({
        empty: 'Empty',
        refill: 'Refill needed',
        healthy: 'Stock ready',
        unconfigured: 'Count needed'
    } [status]);
}

function renderShelfOperations() {
    setSimulatedTape();
    const data = shelfOps.data,
        shelves = [...(data?.shelves ?? [])];
    const ranking = $('shelf-sort').value;
    const priority = {
        empty: 0,
        refill: 1,
        unconfigured: 2,
        healthy: 3
    };
    shelves.sort((a, b) => ranking === 'priority' ? priority[a.status] - priority[b.status] || a.name.localeCompare(b.name) : (b[ranking] ?? -1) - (a[ranking] ?? -1));
    $('stock-basis').textContent = t('Stock is operator-confirmed. Visits and dwell use one saved run for this recording. Pickup predictions never subtract units.') + (data?.simulated ? ' ' + t('Simulated stock and activity.') : '');
    $('stock-empty').hidden = shelves.length > 0;
    $('stock-empty').innerHTML = `<p>${esc(t('No shelves mapped for this recording. Add named shelf areas and product types in Camera setup.'))}</p><button id="stock-map">${esc(t('Camera setup'))}</button>`;
    if ($('stock-map')) $('stock-map').onclick = task(async () => {
        if (shelfOps.video && state.video?.id !== shelfOps.video) await loadVideo(shelfOps.video);
        navigate('geometry');
    });
    $('shelf-overall-pdf').disabled = !data?.run_key;
    $('shelf-stock-csv').disabled = !shelves.length;
    const leaders = [...shelves].filter(s => s.visits !== null).sort((a, b) => b.visits - a.visits);
    const dwell = [...shelves].filter(s => s.observed_dwell_seconds !== null).sort((a, b) => b.observed_dwell_seconds - a.observed_dwell_seconds);
    $('stock-kpis').innerHTML = [
        [t('Refill queue'), shelves.filter(s => ['empty', 'refill'].includes(s.status)).length],
        [t('Most visited shelf'), (leaders[0] ? t(leaders[0].name) : null) ?? '—'],
        [t('Most time near shelf'), dwell[0] ? t(dwell[0].name) + ' · ' + sec(dwell[0].observed_dwell_seconds) : '—']
    ].map(([label, value]) => `<article class="stock-kpi"><small>${esc(label)}</small><strong>${esc(value)}</strong></article>`).join('');
    $('stock-grid').innerHTML = shelves.map(s => {
        const stock = s.stock,
            percent = stock ? Math.round(stock.on_hand / stock.capacity * 100) : 0;
        return `<article class="stock-card ${s.status}"><div class="stock-card-head"><span class="stock-status">${esc(stockStatus(s.status))}</span><small>${esc(data.camera_id)}</small></div><h3>${esc(t(s.name))}</h3><p class="stock-product">${esc(s.product_type ? t(s.product_type) : t('Product type not set'))}</p><div class="stock-level"><strong>${stock ? stock.on_hand : '—'}</strong><span>${stock ? '/ '+stock.capacity+' '+esc(t('units')) : esc(t('No confirmed count'))}</span></div><meter min="0" max="100" value="${percent}" aria-label="${esc(t('Shelf fill level'))}"></meter><p class="muted">${stock ? esc(t('Refill at {n} units; add {m} to fill.',{n:stock.reorder_at,m:s.refill_units})) : esc(t('Record a count to activate refill alerts.'))}</p><dl class="stock-metrics"><div><dt>${esc(t('Tracks'))}</dt><dd>${s.unique_tracks ?? '—'}</dd></div><div><dt>${esc(t('Visits'))}</dt><dd>${s.visits ?? '—'}</dd></div><div><dt>${esc(t('Total time near shelf'))}</dt><dd>${sec(s.observed_dwell_seconds)}</dd></div></dl><div class="toolbar"><button data-stock="${esc(s.id)}">${esc(t('Update stock'))}</button><button data-shelf-track="${esc(s.id)}" ${data.run_key?'':'disabled'}>${esc(t('View paths'))}</button></div>${stock ? `<small class="muted">${esc(t('Count updated'))} · ${esc(new Date(stock.updated_at).toLocaleString(locale()))}</small>` : ''}</article>`;
    }).join('');
    document.querySelectorAll('[data-stock]').forEach(b => b.onclick = () => openStockEditor(b.dataset.stock));
    document.querySelectorAll('[data-shelf-track]').forEach(b => b.onclick = task(async () => {
        await loadVideo(data.video_id);
        await refreshInsights(true);
        navigate('tracking');
        $('track-search').value = t(shelves.find(s => s.id === b.dataset.shelfTrack).name);
        $('track-search').dispatchEvent(new Event('input'));
    }));
    $('stock-history').innerHTML = `<table><thead><tr>${['Time','Shelf','Action','Quantity','Stock','Operator notes'].map(s=>`<th>${esc(t(s))}</th>`).join('')}</tr></thead><tbody>${(data?.history ?? []).map(e=>`<tr><td>${esc(new Date(e.updated_at).toLocaleString(locale()))}</td><td>${esc(t(e.name))}</td><td>${esc(t({count:'Set counted quantity',refill:'Add refill units',remove:'Remove confirmed units'}[e.action]))}</td><td>${e.quantity}</td><td>${e.on_hand} / ${e.capacity}</td><td>${esc(e.notes)}</td></tr>`).join('') || `<tr><td colspan="6">${esc(t('No stock updates yet.'))}</td></tr>`}</tbody></table>`;
}

function openStockEditor(id) {
    const shelf = shelfOps.data.shelves.find(s => s.id === id);
    shelfOps.edit = shelf;
    $('stock-dialog-title').textContent = t(shelf.name);
    $('stock-current').textContent = shelf.product_type ? t(shelf.product_type) : t('Product type not set');
    $('stock-action').value = 'count';
    $('stock-quantity').value = shelf.stock?.on_hand ?? 0;
    $('stock-capacity').value = shelf.stock?.capacity ?? 60;
    $('stock-threshold').value = shelf.stock?.reorder_at ?? 12;
    $('stock-notes').value = '';
    $('stock-error').textContent = '';
    $('stock-dialog').showModal();
}

$('stock-close').onclick = () => $('stock-dialog').close();
$('stock-action').onchange = () => {
    $('stock-quantity').value = $('stock-action').value === 'refill' ? (shelfOps.edit.refill_units ?? 0) : $('stock-action').value === 'remove' ? 1 : (shelfOps.edit.stock?.on_hand ?? 0);
};
$('stock-form').onsubmit = async event => {
    event.preventDefault();
    $('stock-save').disabled = true;
    try {
        await api(`/api/ops/recordings/${shelfOps.data.video_id}/shelves/${encodeURIComponent(shelfOps.edit.id)}`, json('PUT', {
            action: $('stock-action').value,
            quantity: Number($('stock-quantity').value),
            capacity: Number($('stock-capacity').value),
            reorder_at: Number($('stock-threshold').value),
            expected_revision: shelfOps.edit.stock?.revision ?? 0,
            notes: $('stock-notes').value
        }));
        $('stock-dialog').close();
        await loadShelfOperations();
    } catch (error) {
        $('stock-error').textContent = error.message;
    } finally {
        $('stock-save').disabled = false;
    }
};
$('shelf-sort').onchange = renderShelfOperations;
$('shelf-recording').onchange = task(() => loadShelfOperations($('shelf-recording').value));
$('shelf-refresh').onclick = task(() => loadShelfOperations());
$('shelf-stock-csv').onclick = () => exportRows(shelfOps.data.shelves.map(s => ({
    name: s.name,
    product_type: s.product_type,
    status: s.status,
    on_hand: s.stock?.on_hand,
    capacity: s.stock?.capacity,
    reorder_at: s.stock?.reorder_at,
    refill_units: s.refill_units,
    visits: s.visits,
    unique_tracks: s.unique_tracks,
    observed_dwell_seconds: s.observed_dwell_seconds,
    simulated: shelfOps.data.simulated
})), ['name', 'product_type', 'status', 'on_hand', 'capacity', 'reorder_at', 'refill_units', 'visits', 'unique_tracks', 'observed_dwell_seconds', 'simulated'], 'shelf-stock.csv');
$('shelf-overall-pdf').onclick = task(async () => {
    $('shelf-overall-pdf').disabled = true;
    try {
        const report = await api('/api/ops/reports', json('POST', {
            title: t('Overall operations report'),
            scope: 'collection',
            video_ids: [shelfOps.data.video_id],
            lang: currentLanguage,
            notes: t('Includes operator-confirmed stock at generation time; activity covers the selected recording.')
        }));
        const link = document.createElement('a');
        link.href = `/api/ops/reports/${report.id}/pdf`;
        link.download = 'sentient-overall.pdf';
        link.click();
    } finally {
        $('shelf-overall-pdf').disabled = false;
    }
});

async function loadCameraSimulation() {
    cameraSim.data = await api('/api/ops/demo-store/cameras');
    const d = cameraSim.data;
    setSimulatedTape();
    $('sim-empty').hidden = d.available;
    $('sim-content').hidden = !d.available;
    if (!d.available) {
        $('sim-empty').innerHTML = `<p>${esc(t('Build the simulated store from Store to enable synchronized cameras.'))}</p><button id="sim-build-store">${esc(t('Open Store'))}</button>`;
        $('sim-build-store').onclick = () => navigate('store');
        return;
    }
    $('sim-camera-grid').innerHTML = d.cameras.map(c => `<article class="sim-camera panel"><div class="panel-head"><h3>${esc(t(c.name))}</h3><span class="stock-status">${esc(t('Simulated'))}</span></div><canvas id="sim-canvas-${c.id}" width="960" height="540" aria-label="${esc(t(c.name))}"></canvas><div class="sim-camera-foot"><span id="sim-count-${c.id}"></span><button data-sim-focus="${c.id}">${esc(t('Expand view'))}</button></div></article>`).join('');
    document.querySelectorAll('[data-sim-focus]').forEach(b => b.onclick = () => $("sim-canvas-" + b.dataset.simFocus).requestFullscreen().catch(e => toast(e.message, true)));
    const player = $('sim-player');
    if (!player.getAttribute('src')) {
        player.src = d.media_url;
        player.load();
    }
    $('sim-time').max = d.duration_seconds;
    $('sim-events').innerHTML = d.incidents.slice(0, 40).map(e => `<button data-sim-ms="${e.media_ms}"><b>${clockTime(e.media_ms)}</b> ${esc(t(e.name))}</button>`).join('') || `<p>${esc(t('No signals in this run.'))}</p>`;
    document.querySelectorAll('[data-sim-ms]').forEach(b => b.onclick = () => {
        player.pause();
        player.currentTime = (Number(b.dataset.simMs) - d.first_media_ms) / 1000;
    });
    paintSimulation();
}

function paintSimulation() {
    const d = cameraSim.data,
        video = $('sim-player');
    if (!d?.available || video.readyState < 2) return;
    const ms = video.currentTime * 1000 + d.first_media_ms;
    // Binary search preserves one shared source timestamp at any playback speed.
    let low = 0,
        high = d.rows.length;
    while (low < high) {
        const mid = (low + high) >> 1;
        if (d.rows[mid].media_ms <= ms) low = mid + 1;
        else high = mid;
    }
    const row = d.rows[low - 1],
        tracks = row && ms - row.media_ms <= 1000 ? row.tracks.filter(t => t.position_status === 'observed') : [];
    $('sim-time').value = video.currentTime;
    $('sim-clock').textContent = clockTime(video.currentTime * 1000);
    $('sim-play').textContent = t(video.paused ? 'Play simulation' : 'Pause simulation');
    for (const camera of d.cameras) {
        const canvas = $('sim-canvas-' + camera.id);
        if (!canvas) continue;
        const ctx = canvas.getContext('2d'),
            [x, y, x2, y2] = camera.roi,
            w = x2 - x,
            h = y2 - y;
        canvas.width = 960;
        canvas.height = Math.round(960 * h / w);
        ctx.drawImage(video, x, y, w, h, 0, 0, canvas.width, canvas.height);
        ctx.save();
        ctx.scale(canvas.width / w, canvas.height / h);
        ctx.translate(-x, -y);
        for (const shape of d.geometry.shapes.filter(s => camera.kinds.includes(s.kind))) {
            ctx.beginPath();
            shape.points.forEach((p, i) => i ? ctx.lineTo(...p) : ctx.moveTo(...p));
            if (shape.kind !== 'line') ctx.closePath();
            ctx.strokeStyle = '#f4ca49';
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.font = '12px sans-serif';
            ctx.fillStyle = '#fff';
            ctx.fillText(t(shape.name), shape.points[0][0] + 3, shape.points[0][1] + 14);
        }
        if (camera.id === 'shelves') {
            for (const shelf of d.shelf_operations.shelves) {
                if (!shelf.stock) continue;
                const shape = d.geometry.shapes.find(s => s.id === shelf.id);
                const px = shape.points[0][0] + 4,
                    py = shape.points[0][1] + 31;
                ctx.fillStyle = ['empty', 'refill'].includes(shelf.status) ? '#efac43' : '#5df0c0';
                ctx.font = 'bold 11px sans-serif';
                ctx.fillText(shelf.stock.on_hand + '/' + shelf.stock.capacity + ' ' + t('units'), px, py);
            }
        }
        if ($('sim-trails').checked) {
            const paths = new Map();
            for (let i = low - 1; i >= 0 && ms - d.rows[i].media_ms <= 12000; i--) {
                for (const t of d.rows[i].tracks) {
                    if (t.position_status !== 'observed') continue;
                    const p = paths.get(t.local_track_id) ?? [];
                    p.push([(t.box[0] + t.box[2]) / 2, t.box[3], d.rows[i].media_ms]);
                    paths.set(t.local_track_id, p);
                }
            }
            ctx.strokeStyle = '#5df0c0';
            ctx.lineWidth = 1.8;
            for (const points of paths.values()) {
                ctx.beginPath();
                points.forEach((p, i) => {
                    if (i === 0 || points[i - 1][2] - p[2] > 1000) ctx.moveTo(p[0], p[1]);
                    else ctx.lineTo(p[0], p[1]);
                });
                ctx.stroke();
            }
        }
        let count = 0;
        for (const t of tracks) {
            const b = t.box,
                fx = (b[0] + b[2]) / 2,
                fy = b[3];
            if (fx < x || fx > x2 || fy < y || fy > y2) continue;
            count++;
            ctx.strokeStyle = '#5df0c0';
            ctx.lineWidth = 2;
            ctx.strokeRect(b[0], b[1], b[2] - b[0], b[3] - b[1]);
            ctx.fillStyle = '#fff';
            ctx.font = '11px sans-serif';
            ctx.fillText(t.local_track_id.split(':').at(-1), b[0], b[1] - 4);
        }
        ctx.restore();
        ctx.fillStyle = '#141c2d';
        ctx.fillRect(0, 0, canvas.width, 28);
        ctx.fillStyle = '#ffd24d';
        ctx.font = 'bold 13px sans-serif';
        ctx.fillText(t('Simulated') + ' · ' + t(camera.name) + ' · ' + clockTime(video.currentTime * 1000), 12, 19);
        $('sim-count-' + camera.id).textContent = t('{n} observed in frame', {
            n: count
        });
    }
}

function animateSimulation() {
    paintSimulation();
    if (!$('sim-player').paused && ops.view === 'cameras') cameraSim.raf = requestAnimationFrame(animateSimulation);
}
$('sim-player').onplay = () => {
    cancelAnimationFrame(cameraSim.raf);
    animateSimulation();
};
for (const name of ['seeked', 'loadeddata', 'pause']) $('sim-player').addEventListener(name, paintSimulation);
$('sim-play').onclick = task(async () => {
    const p = $('sim-player');
    if (p.paused) await p.play();
    else p.pause();
});
$('sim-time').oninput = () => {
    $('sim-player').currentTime = Number($('sim-time').value);
};
$('sim-speed').onchange = () => {
    $('sim-player').playbackRate = Number($('sim-speed').value);
};
$('sim-trails').onchange = paintSimulation;
$('sim-open-shelves').onclick = task(async () => {
    shelfOps.video = cameraSim.data?.video_id ?? null;
    navigate('shelves');
});
$('sim-explore').onclick = task(async () => {
    await loadVideo(cameraSim.data.video_id);
    navigate('tracking');
});