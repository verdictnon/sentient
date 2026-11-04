'use strict';
// Plays a real recording with the detections the real model saved for it.
const showcase = {
    data: null,
    raf: 0,
    events: []
};

function showcaseFrameIndex(ms) {
    const frames = showcase.data.frames;
    let low = 0,
        high = frames.length - 1;
    while (low < high) {
        const middle = Math.ceil((low + high) / 2);
        if (frames[middle].t <= ms) low = middle;
        else high = middle - 1;
    }
    return low;
}

// Samples are five a second and video is thirty, so boxes glide between samples.
function showcaseBoxes(ms) {
    const frames = showcase.data.frames,
        index = showcaseFrameIndex(ms),
        now = frames[index],
        next = frames[index + 1];
    if (!next) return now.boxes;
    const share = Math.max(0, Math.min(1, (ms - now.t) / (next.t - now.t))),
        ahead = new Map(next.boxes.map(box => [box[0], box]));
    return now.boxes.map(box => {
        const target = ahead.get(box[0]);
        if (!target) return box;
        return [box[0], ...[1, 2, 3, 4].map(i => box[i] + (target[i] - box[i]) * share), box[5], box[6], box[7]];
    });
}

function showcaseTrail(track, ms) {
    const frames = showcase.data.frames,
        index = showcaseFrameIndex(ms),
        points = [];
    for (let i = Math.max(0, index - 15); i <= index; i++) {
        const box = frames[i].boxes.find(row => row[0] === track);
        if (box) points.push([(box[1] + box[3]) / 2, box[4]]);
    }
    return points;
}

// COCO keypoints: arms, shoulders, hips and legs. Points the model missed come back as 0,0.
const SKELETON = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16]];

function drawPose(ctx, pose) {
    ctx.strokeStyle = 'rgba(123,166,255,.95)';
    ctx.beginPath();
    for (const [a, b] of SKELETON) {
        if (!pose[a][0] || !pose[b][0]) continue;
        ctx.moveTo(...pose[a]);
        ctx.lineTo(...pose[b]);
    }
    ctx.stroke();
    ctx.fillStyle = '#ff5a3c';
    for (const wrist of [pose[9], pose[10]]) {
        if (!wrist[0]) continue;
        ctx.beginPath();
        ctx.arc(wrist[0], wrist[1], 4, 0, Math.PI * 2);
        ctx.fill();
    }
}

function paintShowcase() {
    const video = $('showcase-video'),
        canvas = $('showcase-canvas'),
        data = showcase.data;
    if (!data) return;
    const [width, height] = data.size,
        scale = Math.max(1, Math.round(canvas.clientWidth * devicePixelRatio / width * 100) / 100),
        ctx = canvas.getContext('2d'),
        ms = video.currentTime * 1000,
        boxes = showcaseBoxes(ms);
    canvas.width = width * scale;
    canvas.height = height * scale;
    ctx.scale(scale, scale);
    ctx.lineWidth = 2;
    ctx.font = '600 12px Onest, system-ui, sans-serif';
    const seen = boxes.filter(box => box[6]);
    for (const box of boxes) {
        const [track, x1, y1, x2, y2, score, observed, pose] = box,
            trail = showcaseTrail(track, ms);
        if (pose) drawPose(ctx, pose);
        ctx.strokeStyle = observed ? '#ffd21f' : 'rgba(255,255,255,.65)';
        ctx.setLineDash(observed ? [] : [5, 4]);
        ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
        ctx.setLineDash([]);
        if (trail.length > 1) {
            ctx.beginPath();
            trail.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
            ctx.stroke();
        }
        const label = observed ? `#${track}  ${Math.round(score * 100)}%` : `#${track}`,
            wide = ctx.measureText(label).width + 10;
        ctx.fillStyle = observed ? '#ffd21f' : 'rgba(255,255,255,.8)';
        ctx.fillRect(x1 - 1, Math.max(0, y1 - 19), wide, 19);
        ctx.fillStyle = '#10131a';
        ctx.fillText(label, x1 + 4, Math.max(14, y1 - 5));
    }
    $('showcase-now').textContent = number(seen.length);
    $('showcase-seen').textContent = number(showcase.events.filter(event => event.t <= ms).length);
    $('showcase-score').textContent = seen.length ? Math.round(seen.reduce((sum, box) => sum + box[5], 0) / seen.length * 100) + '%' : '—';
    renderShowcaseLog(ms);
}

function renderShowcaseLog(ms) {
    const past = showcase.events.filter(event => event.t <= ms),
        key = past.length;
    if (showcase.logged === key) return;
    showcase.logged = key;
    $('showcase-log').innerHTML = past.slice(-7).reverse().map(event => `<li><button data-seek="${event.t}"><span class="mono">${clockTime(event.t)}</span>${esc(t('Person #{n} found', {n: event.track}))}</button></li>`).join('') || `<li class="muted">${esc(t('Press play. Found people appear here.'))}</li>`;
    $('showcase-log').querySelectorAll('[data-seek]').forEach(button => button.onclick = () => {
        $('showcase-video').currentTime = button.dataset.seek / 1000;
    });
}

function animateShowcase() {
    paintShowcase();
    if (!$('showcase-video').paused && ops.view === 'showcase') showcase.raf = requestAnimationFrame(animateShowcase);
}

async function openShowcase() {
    if (showcase.list === undefined) {
        showcase.list = await api('/api/showcase');
        $('showcase-empty').hidden = showcase.list.length > 0;
        $('showcase-stage').hidden = !showcase.list.length;
        $('showcase-picker').innerHTML = showcase.list.map(clip => `<option value="${esc(clip.id)}">${esc(t(clip.name))}${clip.store ? ' · ' + esc(clip.store) : ''}</option>`).join('');
        $('showcase-picker').hidden = showcase.list.length < 2;
        $('showcase-picker').onchange = () => loadShowcase($('showcase-picker').value).catch(e => toast(e.message, true));
        if (showcase.list.length) await loadShowcase(showcase.list[0].id);
        return;
    }
    if (showcase.data) paintShowcase();
}

async function loadShowcase(id) {
    const data = await api('/api/showcase/' + encodeURIComponent(id)),
        model = data.model,
        first = new Map();
    showcase.data = data;
    showcase.logged = -1;
    for (const frame of data.frames)
        for (const box of frame.boxes)
            if (box[6] && !first.has(box[0])) first.set(box[0], frame.t);
    showcase.events = [...first].map(([track, time]) => ({
        track,
        t: time
    })).sort((a, b) => a.t - b.t);
    $('showcase-title').textContent = t(data.name);
    $('showcase-note').textContent = t('Real footage. The model found these people ahead of time, so this plays on any laptop.');
    $('showcase-credit').innerHTML = `${esc(t('Video'))}: <a href="${esc(data.source_url)}" target="_blank" rel="noreferrer">${esc(data.source)}</a>, ${esc(data.licence)}. ${esc(t('Model'))}: ${esc(model.detector)}, ${esc(model.tracker)}, ${esc(t('{n} samples a second', {n: Math.round(1000 / model.sample_ms)}))}.`;
    $('showcase-video').src = `/api/showcase/${encodeURIComponent(id)}/video.mp4`;
    Chart.area($('showcase-chart'), data.frames.map(frame => ({
        x: frame.t,
        y: frame.boxes.filter(box => box[6]).length
    })), {
        height: 130,
        aria: t('People in frame'),
        tick: value => clockTime(value),
        tipValue: point => number(point.y),
        tipLabel: point => clockTime(point.x)
    });
    paintShowcase();
}

function pauseShowcase() {
    $('showcase-video').pause();
}

$('showcase-video').addEventListener('play', animateShowcase);
for (const name of ['seeked', 'loadeddata', 'pause', 'ended']) $('showcase-video').addEventListener(name, paintShowcase);
addEventListener('resize', () => ops.view === 'showcase' && paintShowcase());
