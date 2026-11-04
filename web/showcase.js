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

function drawDetections(canvas, data, ms, size = {font: 12, line: 2}) {
    const previous = showcase.data;
    showcase.data = data;
    const [width, height] = data.size,
        scale = Math.max(1, Math.round(canvas.clientWidth * devicePixelRatio / width * 100) / 100),
        ctx = canvas.getContext('2d'),
        boxes = showcaseBoxes(ms);
    canvas.width = width * scale;
    canvas.height = height * scale;
    ctx.scale(scale, scale);
    ctx.lineWidth = size.line;
    ctx.font = `600 ${size.font}px Onest, system-ui, sans-serif`;
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
            wide = ctx.measureText(label).width + 10,
            tall = size.font + 7;
        ctx.fillStyle = observed ? '#ffd21f' : 'rgba(255,255,255,.8)';
        ctx.fillRect(x1 - 1, Math.max(0, y1 - tall), wide, tall);
        ctx.fillStyle = '#10131a';
        ctx.fillText(label, x1 + 4, Math.max(size.font + 2, y1 - 5));
    }
    showcase.data = previous;
    return {
        ctx,
        boxes
    };
}

function paintShowcase() {
    const data = showcase.data;
    if (!data) return;
    const ms = $('showcase-video').currentTime * 1000,
        seen = drawDetections($('showcase-canvas'), data, ms).boxes.filter(box => box[6]);
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

// Every recording at once, the way a shop's camera monitor looks.
const cameraWall = {
    tiles: [],
    timer: 0
};

async function openShowcase() {
    if (showcase.list === undefined) {
        showcase.list = await api('/api/showcase');
        $('showcase-empty').hidden = showcase.list.length > 0;
        $('allcam-view').hidden = !showcase.list.length;
        $('allcam-cameras').textContent = number(showcase.list.length);
        $('allcam-grid').innerHTML = showcase.list.map(clip => `<button class="allcam-tile" data-clip="${esc(clip.id)}"><span class="allcam-screen"><video muted loop playsinline preload="auto" src="/api/showcase/${encodeURIComponent(clip.id)}/video.mp4"></video><canvas></canvas><em>${esc(t('Recording'))}</em></span><span class="allcam-name"><b>${esc(t(clip.name))}</b><small></small></span></button>`).join('');
        cameraWall.tiles = await Promise.all([...document.querySelectorAll('.allcam-tile')].map(async tile => ({
            tile,
            video: tile.querySelector('video'),
            canvas: tile.querySelector('canvas'),
            count: tile.querySelector('small'),
            data: await api('/api/showcase/' + encodeURIComponent(tile.dataset.clip))
        })));
        cameraWall.tiles.forEach(({
            tile
        }) => tile.onclick = () => focusCamera(tile.dataset.clip).catch(e => toast(e.message, true)));
    }
    if (!$('showcase-focus').hidden) return;
    startCameraWall();
}

function startCameraWall() {
    for (const tile of cameraWall.tiles) tile.video.play().catch(() => {});
    clearInterval(cameraWall.timer);
    cameraWall.timer = setInterval(paintCameraWall, 80);
}

function paintCameraWall() {
    if (ops.view !== 'showcase') return;
    let people = 0;
    for (const tile of cameraWall.tiles) {
        const inFrame = drawDetections(tile.canvas, tile.data, tile.video.currentTime * 1000, {
            font: 26,
            line: 4
        }).boxes.filter(box => box[6]).length;
        people += inFrame;
        tile.count.textContent = t('{n} in frame', {
            n: number(inFrame)
        });
    }
    $('allcam-people').textContent = number(people);
}

function stopCameraWall() {
    clearInterval(cameraWall.timer);
    for (const tile of cameraWall.tiles) tile.video.pause();
}

async function focusCamera(id) {
    stopCameraWall();
    $('allcam-view').hidden = true;
    $('showcase-focus').hidden = false;
    await loadShowcase(id);
    $('showcase-video').play().catch(() => {});
}

$('showcase-back').onclick = () => {
    $('showcase-video').pause();
    $('showcase-focus').hidden = true;
    $('allcam-view').hidden = false;
    startCameraWall();
};

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
    stopCameraWall();
}

$('showcase-video').addEventListener('play', animateShowcase);
for (const name of ['seeked', 'loadeddata', 'pause', 'ended']) $('showcase-video').addEventListener(name, paintShowcase);
addEventListener('resize', () => ops.view === 'showcase' && paintShowcase());

// The Store map, on a real recording. Heat, routes and shelf counts all come from the saved tracks.
const realFloor = {
    data: null,
    list: null,
    heat: null
};

function footPoints(data) {
    const tracks = new Map();
    for (const frame of data.frames)
        for (const box of frame.boxes) {
            if (!box[6]) continue;
            if (!tracks.has(box[0])) tracks.set(box[0], []);
            tracks.get(box[0]).push([(box[1] + box[3]) / 2, box[4]]);
        }
    return tracks;
}

// Density on a coarse grid, blurred and coloured once, so a person standing still reads as a soft
// patch and not a hard disc.
function buildRealHeat(data) {
    const [width, height] = data.size,
        columns = 128,
        rows = Math.round(columns * height / width),
        grid = new Float32Array(columns * rows),
        reach = 7;
    for (const points of footPoints(data).values())
        for (const [x, y] of points) {
            const column = x / width * columns,
                row = y / height * rows;
            for (let v = Math.max(0, Math.floor(row - reach)); v < Math.min(rows, row + reach); v++)
                for (let u = Math.max(0, Math.floor(column - reach)); u < Math.min(columns, column + reach); u++)
                    grid[v * columns + u] += Math.exp(-((u - column) ** 2 + (v - row) ** 2) / (2 * 3.2 ** 2));
        }
    const highest = Math.max(1e-6, ...grid),
        canvas = document.createElement('canvas'),
        ctx = canvas.getContext('2d'),
        image = ctx.createImageData(columns, rows);
    canvas.width = columns;
    canvas.height = rows;
    grid.forEach((value, i) => {
        const share = Math.sqrt(value / highest);
        image.data.set([255, Math.round(210 * (1 - share)), 40, Math.round(190 * Math.min(1, share * 1.4))], i * 4);
    });
    ctx.putImageData(image, 0, 0);
    return canvas;
}

function inside(point, polygon) {
    let hit = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const [xi, yi] = polygon[i],
            [xj, yj] = polygon[j];
        if ((yi > point[1]) !== (yj > point[1]) && point[0] < (xj - xi) * (point[1] - yi) / (yj - yi) + xi) hit = !hit;
    }
    return hit;
}

// A person counts at a shelf once a wrist the model found is inside it.
function peopleReaching(data, shape) {
    const people = new Set();
    for (const frame of data.frames)
        for (const box of frame.boxes) {
            const pose = box[7];
            if (pose && [pose[9], pose[10]].some(wrist => wrist[0] && inside(wrist, shape.points))) people.add(box[0]);
        }
    return people.size;
}

function paintRealFloor() {
    const data = realFloor.data;
    if (!data) return;
    const {
        ctx
    } = drawDetections($('floor-real-canvas'), data, $('floor-video').currentTime * 1000);
    if (store.layer === 'heat') {
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(realFloor.heat, 0, 0, ...data.size);
    }
    if (store.layer === 'flows') {
        ctx.strokeStyle = '#ffd21f';
        ctx.lineWidth = 3;
        for (const points of footPoints(data).values()) {
            ctx.beginPath();
            points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
            ctx.stroke();
        }
    }
    if (store.layer === 'zones') {
        for (const shape of data.geometry?.shapes ?? []) {
            const people = peopleReaching(data, shape);
            ctx.beginPath();
            shape.points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
            ctx.closePath();
            ctx.fillStyle = 'rgba(255,210,31,.2)';
            ctx.fill();
            ctx.strokeStyle = '#ffd21f';
            ctx.lineWidth = 2;
            ctx.stroke();
            ctx.fillStyle = '#fff';
            ctx.font = '700 18px Onest, system-ui, sans-serif';
            ctx.fillText(`${t(shape.name)}: ${people}`, shape.points[0][0] + 10, shape.points[0][1] + 26);
        }
    }
    if (!$('floor-video').paused && ops.view === 'store') requestAnimationFrame(paintRealFloor);
}

// Returns false when no real clip exists, so the store falls back to its own map.
async function showRealFloor() {
    if (realFloor.list === null) realFloor.list = await api('/api/showcase').catch(() => []);
    if (!realFloor.list.length) return false;
    if (!realFloor.data) {
        const clip = realFloor.list.find(row => row.id.startsWith('V0797')) ?? realFloor.list[0];
        realFloor.data = await api('/api/showcase/' + encodeURIComponent(clip.id));
        realFloor.heat = buildRealHeat(realFloor.data);
        $('floor-video').src = `/api/showcase/${encodeURIComponent(clip.id)}/video.mp4`;
        $('floor-note').textContent = '';
    }
    paintRealFloor();
    return true;
}

function setFloorPlayback(playing) {
    const video = $('floor-video');
    if (!video.src) return;
    if (playing) video.play().catch(() => {});
    else video.pause();
}

$('floor-video').addEventListener('play', paintRealFloor);
for (const name of ['seeked', 'loadeddata', 'pause']) $('floor-video').addEventListener(name, paintRealFloor);
