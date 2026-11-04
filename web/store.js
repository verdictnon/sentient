'use strict';
// The store view: one day or week on a single screen, plus the store half of Analytics.
const store = {
    data: null,
    id: null,
    day: null,
    span: 1,
    layer: 'heat',
    hour: null,
    timer: null,
    printed: false,
    loading: false,
    demoTimer: null,
    heatKey: ''
};
const STATUS_ORDER = ['left_without_checkout', 'lost_before_checkout', 'still_in_view', 'reached_checkout', 'checkout_not_mapped'];

function storeName(row) {
    if (!row) return '';
    if (row.name) return row.name;
    const known = workBoard.data?.stores?.find(s=>s.id===row.id) ?? store.data?.stores?.find(s=>s.id===row.id);
    if (known?.name) return known.name;
    if (row.simulated || ['demo-store','aster-market'].includes(row.id)) return t('Aster Market');
    return row.id.replace(/[-_]+/g, ' ').replace(/^./, c => c.toUpperCase());
}

function dayLabel(value, options = {
    weekday: 'short',
    day: 'numeric',
    month: 'short'
}) {
    return new Intl.DateTimeFormat(locale(), {
        ...options,
        timeZone: 'UTC'
    }).format(new Date(value + 'T12:00:00Z'));
}

function addDays(value, count) {
    const date = new Date(value + 'T12:00:00Z');
    date.setUTCDate(date.getUTCDate() + count);
    return date.toISOString().slice(0, 10);
}

function hourLabel(hour) {
    return String(hour).padStart(2, '0') + ':00';
}

function mark(value) {
    return `<mark>${esc(value)}</mark>`;
}

async function changeStore(id) {
    store.id = id;
    store.day = null;
    store.hour = null;
    await loadStore({
        print: true
    });
    const camera = store.data?.camera;
    if (camera && state.videos.some(v => v.id === camera.video_id) && camera.video_id !== state.video?.id && !state.live && !state.offline) await loadVideo(camera.video_id);
}

async function loadStore(options = {}) {
    if (store.loading) return;
    store.loading = true;
    document.body.classList.add('store-loading');
    try {
        if (!store.id && !state.includeGenerated && ops.workspace?.recordings.some(v=>v.store_id==='carr-unej')) store.id='carr-unej';
        const params = new URLSearchParams();
        if (store.id) params.set('store_id', store.id);
        if (store.day) {
            params.set('end', store.day);
            params.set('start', store.span > 1 ? addDays(store.day, 1 - store.span) : store.day);
        }
        const data = await api('/api/ops/overview?' + params);
        store.data = data;
        store.id = data.store?.id ?? null;
        store.day = data.end ?? null;
        store.heatKey = '';
        if (options.print) store.printed = false;
        renderStore();
        renderCaseScope(ops.data?.crosscheck?.totals);
        if (ops.view === 'analytics') renderFlow();
    } finally {
        store.loading = false;
        document.body.classList.remove('store-loading');
    }
}

function renderPeriod() {
    const d = store.data,
        select = $('store-select'),
        days = d?.available_days ?? [];
    select.innerHTML = (d?.stores ?? []).map(s => `<option value="${esc(s.id)}">${esc(storeName(s))}</option>`).join('');
    select.value = store.id ?? '';
    select.hidden = (d?.stores ?? []).length < 2;
    $('day-picker').innerHTML = days.slice().reverse().map(day => `<option value="${day}">${esc(dayLabel(day))}</option>`).join('');
    $('day-picker').value = store.day ?? '';
    const calendar = d?.mode === 'calendar';
    $('day-stepper').hidden = !calendar;
    $('span-switch').hidden = !calendar;
    $('day-prev').disabled = !calendar || days.indexOf(store.day) <= 0;
    $('day-next').disabled = !calendar || days.indexOf(store.day) >= days.length - 1;
    document.querySelectorAll('[data-span]').forEach(b => b.classList.toggle('selected', Number(b.dataset.span) === store.span));
}

function setSimulatedTape() {
    if (!state.includeGenerated) { $('sim-tape').hidden = true; return; }
    const onStore = (ops.view === 'work' && workBoard.data?.store?.simulated) || (['store', 'analytics'].includes(ops.view) && store.data?.basis === 'simulated') || (ops.view === 'shelves' && shelfOps.data?.simulated) || (ops.view === 'cameras' && cameraSim.data?.available),
        onCamera = !['store', 'work', 'shelves', 'cameras', 'reports', 'system', 'help'].includes(ops.view) && state.video?.source_profile?.simulated;
    $('sim-tape').hidden = !(onStore || onCamera);
}

function renderStore() {
    const d = store.data;
    renderPeriod();
    setSimulatedTape();
    const empty = !d || d.mode === 'empty';
    document.querySelector('.store-layout').hidden = empty;
    $('store-empty').hidden = !empty;
    if (empty) {
        renderOnboarding();
        return;
    }
    $('store-narrative').innerHTML = narrative(d);
    renderReceipt(d);
    renderActions(d);
    renderBusyGrid(d);
    renderMap();
    if (!store.printed) {
        store.printed = true;
        const paper = $('receipt');
        paper.classList.remove('printing');
        void paper.offsetWidth;
        paper.classList.add('printing');
    }
    const params = new URLSearchParams({
        lang: currentLanguage
    });
    if (store.id) params.set('store_id', store.id);
    if (d.start) params.set('start', d.start);
    if (d.end) params.set('end', d.end);
    $('z-report-pdf').href = '/api/ops/z-report.pdf?' + params;
}

function durationText(minutes) {
    if (minutes === null || minutes === undefined) return '—';
    const total = Math.round(minutes);
    if (total < 60) return t('{n} min', {
        n: total
    });
    return t('{h} h {m} min', {
        h: Math.floor(total / 60),
        m: total % 60
    });
}

function waitText(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const total = Math.round(seconds);
    return `${Math.floor(total/60)}:${String(total%60).padStart(2,'0')}`;
}

function narrative(d) {
    const k = d.kpis;
    if (d.mode === 'collection') {
        if (!d.store.analysed) return t('{n} recordings added. Run the analysis to see this store.', {
            n: mark(number(d.store.recordings))
        });
        const parts = [t('{n} tracks across {m} analysed recordings.', {
            n: mark(number(k.tracks)),
            m: mark(d.store.analysed)
        })];
        if (k.pickups !== null) parts.push(t('{n} pickup candidates to review.', {
            n: mark(number(k.pickups))
        }));
        return parts.join(' ');
    }
    const when = d.span_days > 1 ? `${dayLabel(d.start,{day:'numeric',month:'short'})}–${dayLabel(d.end,{day:'numeric',month:'short'})}` : capitalise(dayLabel(d.end, {
        weekday: 'long'
    }));
    const parts = [];
    if (k.entries !== null) parts.push(t('{when}: {n} people came in.', {
        when: esc(when),
        n: mark(number(k.entries))
    }));
    else parts.push(t('{when}: {n} tracks observed.', {
        when: esc(when),
        n: mark(number(k.tracks))
    }));
    if (k.busiest_hour) parts.push(t('Busiest at {hour}.', {
        hour: mark(hourLabel(k.busiest_hour.hour))
    }));
    const queue = k.queue_over_minutes ? t('The line ran over its limit for {time}', {
        time: mark(durationText(k.queue_over_minutes))
    }) : null;
    const left = k.left_without_checkout ? t('{n} pickups never reached a till', {
        n: mark(number(k.left_without_checkout))
    }) : null;
    if (queue && left) parts.push(t('{a}, and {b}.', {
        a: queue,
        b: left
    }));
    else if (queue || left) parts.push(capitalise(queue ?? left) + '.');
    return parts.join(' ');
}

function receiptLine(label, value, strong = false) {
    return `<div class="r-line ${strong?'strong':''}"><span>${esc(label)}</span><i></i><b>${esc(value ?? '—')}</b></div>`;
}

function code39(text) {
    const table = {
        0: 'nnnwwnwnn',
        1: 'wnnwnnnnw',
        2: 'nnwwnnnnw',
        3: 'wnwwnnnnn',
        4: 'nnnwwnnnw',
        5: 'wnnwwnnnn',
        6: 'nnwwwnnnn',
        7: 'nnnwnnwnw',
        8: 'wnnwnnwnn',
        9: 'nnwwnnwnn',
        Z: 'nwwnwnnnn',
        '*': 'nwnnwnwnn'
    };
    let x = 0,
        bars = '';
    for (const character of `*${text}*`) {
        [...table[character]].forEach((element, index) => {
            const size = element === 'w' ? 3 : 1;
            if (index % 2 === 0) bars += `<rect x="${x}" y="0" width="${size}" height="40"/>`;
            x += size;
        });
        x += 1;
    }
    return `<svg class="r-barcode" viewBox="0 0 ${x} 40" preserveAspectRatio="none" role="img" aria-label="${esc(text)}">${bars}</svg>`;
}

function reportNumber(d) {
    return 'Z' + String(d.end ?? d.store.id.length).replaceAll('-', '');
}

function renderReceipt(d) {
    const k = d.kpis,
        peak = k.busiest_hour ? hourLabel(k.busiest_hour.hour) : null,
        when = d.mode === 'calendar' ? (d.span_days > 1 ? `${d.start} — ${d.end}` : dayLabel(d.end, {
            weekday: 'short',
            day: '2-digit',
            month: '2-digit',
            year: 'numeric'
        })) : t('Undated recordings');
    const money = d.value_at_risk?.amount === null || d.value_at_risk?.amount === undefined ? null : number(d.value_at_risk.amount) + ' ' + currencySign(d.settings.currency);
    let html = `<header><b class="r-store">${esc(storeName(d.store))}</b><span>${esc(t('Floor Z-report'))}</span><span>${esc(when)}</span>${d.basis==='simulated'?`<span class="r-flag">${esc(t('SIMULATED'))}</span>`:''}</header>`;
    html += `<section>${receiptLine(t('Came in'),number(k.entries))}${receiptLine(t('Left'),number(k.exits))}${receiptLine(t('Most in store'),number(k.people_peak))}${receiptLine(t('Busiest hour'),peak)}${receiptLine(t('Time in store'),k.visit_minutes===null?null:t('{n} min',{n:decimal(k.visit_minutes)}))}</section>`;
    if (k.queue_peak !== null) html += `<section><h3>${esc(t('Queue'))}</h3>${receiptLine(t('Longest line'),number(k.queue_peak))}${receiptLine(t('Over the limit'),durationText(k.queue_over_minutes))}${receiptLine(t('Wait at the peak'),waitText(k.wait_peak_s))}${receiptLine(t('Gave up waiting'),number(k.abandoned))}</section>`;
    if (k.pickups !== null) html += `<section><h3>${esc(t('Pickups against the till'))}</h3>${receiptLine(t('Pickup candidates'),number(k.pickups))}${receiptLine(t('Reached a till'),number(k.reached_checkout))}${receiptLine(t('Left without paying?'),number(k.left_without_checkout),true)}${receiptLine(t('Track lost'),number(k.lost_before_checkout))}</section>`;
    if (money) html += `<section class="r-total">${receiptLine(t('Value to review'),money,true)}<small>${esc(t('{n} × average item {v}',{n:number(k.left_without_checkout),v:number(d.value_at_risk.item_value)+' '+currencySign(d.settings.currency)}))}</small></section>`;
    html += `<footer>${code39(reportNumber(d))}<span>${esc(reportNumber(d))}</span><small>${esc(t('Review candidates before acting. A pickup without a till is a reason to look, not proof of theft.'))}</small></footer>`;
    $('receipt').innerHTML = html;
}

function recommendation(item) {
    const p = item.params,
        area = p.zone ? t(p.zone) : '';
    switch (item.code) {
        case 'review_pickups':
            return [t('{n} pickups need a look', {
                n: number(p.count)
            }), area ? t('{left} left without reaching a till. Most flagged near {zone}.', {
                left: number(p.left),
                zone: area
            }) : t('{left} left without reaching a till.', {
                left: number(p.left)
            }), t('Open cases')];
        case 'open_till':
            return [t('Open a second till {a}–{b}', {
                a: hourLabel(p.start),
                b: hourLabel(p.end)
            }), [t('The line stayed above {limit} people for {minutes} min in that window.', {
                limit: p.limit,
                minutes: number(p.minutes)
            }), p.abandoned ? t('{n} people gave up and left.', {
                n: number(p.abandoned)
            }) : ''].join(' ').trim(), t('See the queue')];
        case 'dwell_no_pickup':
            return [t('People stop at {zone} but rarely take anything', {
                zone: area
            }), t('{dwell} s per visit, and only {rate}% of visits end in a pickup. A typical shelf reaches {typical}%. Check price, layout and stock.', {
                dwell: Math.round(p.dwell),
                rate: p.rate,
                typical: p.typical
            }), t('See shelves')];
        case 'cold_zone':
            return [t('Few people reach {zone}', {
                zone: area
            }), t('{reach}% of tracks get there, against {top}% at the busiest shelf. Consider moving it or adding signage.', {
                reach: p.reach,
                top: p.top
            }), t('See shelves')];
        case 'peak_hour':
            return [t('Staff for {hour}', {
                hour: hourLabel(p.hour)
            }), t('The busiest hour brings about {value} people a day.', {
                value: number(Math.round(p.value))
            }), t('See footfall')];
        case 'traffic_change':
            return [p.delta > 0 ? t('Footfall up {n}%', {
                n: decimal(Math.abs(p.delta))
            }) : t('Footfall down {n}%', {
                n: decimal(Math.abs(p.delta))
            }), t('Compared with the previous period of the same length.'), t('See footfall')];
    }
    return [item.code, '', ''];
}

// What a real store still needs before its numbers can exist. Simulated stores are complete.
function setupSteps(d) {
    if (d.basis === 'simulated') return [];
    const steps = [];
    if (!d.camera?.shapes?.length) steps.push(setupStep('geometry', t('Map this camera'), t('Draw the doors, shelves, queue and checkout. Every count here depends on them.'), t('Camera setup')));
    if (!d.store.analysed) steps.push(setupStep('overview', t('Analyse a recording'), t('It runs on this computer. If the detector is missing, System & models lists what to install.'), t('Open cameras')));
    if (d.mode !== 'calendar') steps.push(setupStep('geometry', t('Say when it was filmed'), t('Set recording start times to see hours and days.'), t('Camera setup')));
    return steps;
}

function setupStep(view, title, detail, link) {
    return {
        view,
        title,
        detail,
        link,
        tone: 'action'
    };
}

// Fixed advice per finding: how to stop the same thing happening again.
const PREVENTION = {
    review_pickups: 'Keep this shelf in view of the till and check flagged clips before closing.',
    open_till: 'Roster a second cashier for these hours every week, not when the line has already formed.',
    dwell_no_pickup: 'Check price tags and stock here first, then move the best seller to eye level.',
    cold_zone: 'Put a sign at the aisle entrance or swap this shelf with one on the main route.',
    peak_hour: 'Plan breaks and restocking outside this hour.',
    traffic_change: 'Compare this with promotions and weather before changing the rota.'
};

// What is going right, read from the same numbers as the warnings.
function strengths(d) {
    const rows = [],
        delta = d.deltas ?? {},
        shelves = d.zones.filter(z => z.kind === 'shelf' && z.visits >= 20),
        best = [...shelves].sort((a, b) => b.pickup_rate - a.pickup_rate)[0];
    if (d.flags.pickups_scored && best?.pickup_rate > 0) rows.push(t('{zone} turns the most visits into pickups: {rate}%.', {
        zone: t(best.name),
        rate: Math.round(best.pickup_rate * 100)
    }));
    if (delta.wait_peak_s < 0) rows.push(t('The peak wait fell {n}% against the previous period.', {
        n: Math.abs(Math.round(delta.wait_peak_s))
    }));
    if (delta.entries > 0) rows.push(t('Footfall rose {n}% against the previous period.', {
        n: Math.round(delta.entries)
    }));
    if (d.kpis.abandoned === 0 && d.flags.queue_configured) rows.push(t('Nobody gave up in the queue.'));
    return rows;
}

function renderActions(d) {
    const items = setupSteps(d).concat((d.recommendations ?? []).map(item => {
        const [title, detail, link] = recommendation(item);
        return {
            ...item,
            title,
            detail,
            link
        };
    }));
    const good = strengths(d);
    $('store-strengths').hidden = !good.length;
    $('store-strengths').innerHTML = `<h3>${esc(t('What is working'))}</h3><ul>${good.map(line => `<li>${esc(line)}</li>`).join('')}</ul>`;
    $('store-actions').innerHTML = items.map(item => `<article class="action ${item.tone}"><div><h3>${esc(item.title)}</h3><p>${esc(item.detail)}</p>${PREVENTION[item.code] ? `<p class="action-tip">${esc(t('To avoid this'))}: ${esc(t(PREVENTION[item.code]))}</p>` : ''}</div>${item.view === 'incidents' && d.basis === 'simulated' && state.videos.every(v => !v.source_profile?.simulated) ? '' : `<button class="subtle-button" data-action-view="${esc(item.view)}">${esc(item.link)}</button>`}</article>`).join('') || `<p class="field-note">${esc(t('Nothing stands out in this period.'))}</p>`;
    document.querySelectorAll('[data-action-view]').forEach(b => b.onclick = () => openFromAction(b.dataset.actionView));
}

async function openFromAction(view) {
    const camera = store.data?.camera;
    if (view !== 'analytics' && camera && state.videos.some(v => v.id === camera.video_id) && camera.video_id !== state.video?.id && !state.live && !state.offline) await loadVideo(camera.video_id);
    navigate(view);
}

async function showDemoCamera() {
    const video = state.videos.find(v => v.source_profile?.simulated);
    if (video) await loadVideo(video.id);
}

function renderBusyGrid(d) {
    const rows = d.week_grid ?? [],
        hours = [...new Set(rows.flatMap(r => Object.keys(r.hours)).map(Number))].sort((a, b) => a - b);
    $('grid-title').parentElement.hidden = !rows.length;
    if (!rows.length) return;
    const metric = d.flags.doors_configured ? 'entries' : 'tracks',
        max = Math.max(1, ...rows.flatMap(r => Object.values(r.hours).map(v => v[metric] ?? 0)));
    const head = `<div class="busy-corner"></div>` + hours.map(h => `<div class="busy-hour">${h%2===0?h:''}</div>`).join('');
    const body = rows.map(row => {
        const label = `<div class="busy-day ${row.date===store.day?'selected':''}">${esc(capitalise(dayLabel(row.date,{weekday:'short'})))}</div>`;
        const cells = hours.map(hour => {
            const cell = row.hours[hour],
                value = cell?.[metric] ?? 0,
                level = value ? Math.ceil(value / max * 6) : 0;
            return `<div class="busy-cell level-${level}" ${Chart.tipAttributes(number(value),`${capitalise(dayLabel(row.date))}, ${hourLabel(hour)}`)}></div>`;
        }).join('');
        return label + cells;
    }).join('');
    $('busy-grid').innerHTML = `<div class="busy" style="grid-template-columns:40px repeat(${hours.length},minmax(0,1fr))">${head}${body}</div><p class="chart-caption">${esc(metric==='entries'?t('People coming in per hour. Darker means busier.'):t('New tracks per hour. Darker means busier.'))}</p>`;
}

function mapHours() {
    const hours = Object.keys(store.data?.heat ?? {}).filter(h => h !== 'x').map(Number).sort((a, b) => a - b);
    return hours;
}

function renderMap() {
    const d = store.data,
        camera = d?.camera;
    document.querySelector('.floor').hidden = !camera;
    if (!camera) return;
    if (state.portableDemo && d.basis === 'simulated') {
        document.querySelectorAll('[data-layer]').forEach(b => b.classList.toggle('selected', b.dataset.layer === store.layer));
        showRealFloor().then(real => {
            $('floor-real').hidden = !real;
            $('floor-screen').hidden = real;
            $('hour-scrub').hidden = real;
            if (real) {
                $('floor-note').textContent = t('Real recording: {store}. Boxes, heat, routes and shelf counts come from the model. The numbers above are generated.', {
                    store: realFloor.data.store_name
                });
                paintRealFloor();
                setFloorPlayback(ops.view === 'store');
            } else renderDrawnMap();
        }).catch(e => toast(e.message, true));
        return;
    }
    renderDrawnMap();
}

function renderDrawnMap() {
    const d = store.data,
        camera = d.camera;
    $('floor-real').hidden = true;
    $('floor-screen').hidden = false;
    const [w, h] = camera.dimensions;
    $('floor-screen').style.aspectRatio = `${w}/${h}`;
    const source = camera.frame_url;
    if ($('floor-frame').getAttribute('src') !== source) $('floor-frame').src = source;
    $('floor-overlay').setAttribute('viewBox', `0 0 ${w} ${h}`);
    const hours = mapHours(),
        range = $('hour-range');
    $('hour-scrub').hidden = hours.length < 2;
    if (hours.length) {
        range.min = hours[0];
        range.max = hours.at(-1) + 1;
        range.value = store.hour ?? hours.at(-1) + 1;
    }
    $('hour-label').textContent = store.hour === null ? t('All day') : `${hourLabel(store.hour)}–${hourLabel(store.hour+1)}`;
    document.querySelectorAll('[data-layer]').forEach(b => b.classList.toggle('selected', b.dataset.layer === store.layer));
    drawFloorHeat();
    drawFloorOverlay();
    $('floor-note').textContent = t(d.mode === 'calendar' ? 'Density and areas are in camera image space, not a calibrated floor plan.' : 'Undated recordings, in camera image space.');
}

function drawFloorHeat() {
    const d = store.data,
        canvas = $('floor-heat'),
        camera = d.camera,
        show = store.layer === 'heat';
    canvas.hidden = !show;
    if (!show) return;
    const [gw, gh] = d.heat_grid,
        key = [d.end, d.start, store.id, store.hour].join('|');
    if (key === store.heatKey) return;
    store.heatKey = key;
    const values = new Float32Array(gw * gh);
    const hours = store.hour === null ? Object.keys(d.heat) : [String(store.hour)];
    for (const hour of hours)
        for (const [cell, count] of Object.entries(d.heat[hour] ?? {})) values[Number(cell)] += count;
    const blurred = blur(values, gw, gh),
        ranked = Array.from(blurred).filter(Boolean).sort((a, b) => a - b),
        max = Math.max(1e-6, ranked[Math.floor(ranked.length * .985)] ?? 0),
        small = document.createElement('canvas');
    small.width = gw;
    small.height = gh;
    const context = small.getContext('2d'),
        image = context.createImageData(gw, gh);
    for (let i = 0; i < blurred.length; i++) {
        const [r, g, b, a] = heatColour(Math.min(1, blurred[i] / max) ** .8);
        image.data.set([r, g, b, a], i * 4);
    }
    context.putImageData(image, 0, 0);
    const [w, h] = camera.dimensions;
    canvas.width = w;
    canvas.height = h;
    const target = canvas.getContext('2d');
    target.clearRect(0, 0, w, h);
    target.imageSmoothingEnabled = true;
    target.imageSmoothingQuality = 'high';
    target.drawImage(small, 0, 0, w, h);
}

function blur(values, w, h) {
    const output = new Float32Array(values.length),
        kernel = [1, 2, 1];
    for (let y = 0; y < h; y++)
        for (let x = 0; x < w; x++) {
            let sum = 0,
                weight = 0;
            for (let dy = -1; dy <= 1; dy++)
                for (let dx = -1; dx <= 1; dx++) {
                    const nx = x + dx,
                        ny = y + dy;
                    if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
                    const k = kernel[dx + 1] * kernel[dy + 1];
                    sum += values[ny * w + nx] * k;
                    weight += k;
                }
            output[y * w + x] = sum / weight;
        }
    return output;
}

function heatColour(level) {
    if (level < .12) return [0, 0, 0, 0];
    const stops = [
        [0, 255, 210, 31],
        [.55, 255, 138, 31],
        [1, 226, 45, 30]
    ];
    let index = 0;
    while (index < stops.length - 2 && level > stops[index + 1][0]) index++;
    const [a, ar, ag, ab] = stops[index], [b, br, bg, bb] = stops[index + 1],
        ratio = (level - a) / (b - a);
    return [ar + (br - ar) * ratio, ag + (bg - ag) * ratio, ab + (bb - ab) * ratio, Math.min(225, (level - .12) * 300)];
}

function centroid(points) {
    return [points.reduce((n, p) => n + p[0], 0) / points.length, points.reduce((n, p) => n + p[1], 0) / points.length];
}

function drawFloorOverlay() {
    const d = store.data,
        [w] = d.camera.dimensions,
        unit = w / 1000,
        zones = new Map(d.zones.map(z => [z.id, z])),
        shapes = d.camera.shapes ?? [];
    let out = '<defs><marker id="flow-arrow" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="4" markerHeight="4" orient="auto"><path d="M0 0L10 5L0 10z" fill="#ffd21f"/></marker></defs>';
    const value = zone => {
        if (!zone) return 0;
        if (store.hour === null) return zone.tracks;
        return zone.people_by_hour?.[store.hour] ?? 0;
    };
    const max = Math.max(1, ...d.zones.filter(z => z.kind !== 'checkout').map(value));
    for (const shape of shapes) {
        const zone = zones.get(shape.id),
            points = shape.points.map(p => p.join(',')).join(' '),
            label = t(shape.name);
        if (shape.kind === 'line') {
            const [a, b] = shape.points;
            out += `<polyline class="map-door" points="${points}" stroke-width="${3*unit}"/><text class="map-label" x="${(a[0]+b[0])/2}" y="${(a[1]+b[1])/2-9*unit}" font-size="${12.5*unit}" stroke-width="${3*unit}" text-anchor="middle">${esc(label)}</text>`;
            continue;
        }
        const share = value(zone) / max,
            detail = zone ? t('{tracks} tracks, {visits} visits, {dwell} s per visit', {
                tracks: number(zone.tracks),
                visits: number(zone.visits),
                dwell: Math.round(zone.dwell_per_visit_s ?? 0)
            }) : t('No visits in this period');
        const fill = store.layer === 'zones' ? `fill="#ffd21f" fill-opacity="${(.08+share*.72).toFixed(2)}"` : 'fill="transparent"';
        out += `<g class="map-zone" ${Chart.tipAttributes(label,detail)}><polygon points="${points}" ${fill} stroke-width="${1.2*unit}"/></g>`;
        if (store.layer === 'zones' || store.layer === 'heat') {
            const [x, y] = centroid(shape.points);
            out += `<text class="map-label" x="${x}" y="${y}" font-size="${12.5*unit}" stroke-width="${3*unit}" text-anchor="middle">${esc(label)}</text>`;
            if (store.layer === 'zones' && zone) out += `<text class="map-value" x="${x}" y="${y+15*unit}" font-size="${14*unit}" stroke-width="${3*unit}" text-anchor="middle">${esc(number(store.hour===null?zone.tracks:Math.round(value(zone)/3600*10)/10))}</text>`;
        }
    }
    if (store.layer === 'flows') {
        const lookup = new Map(shapes.filter(s => s.kind !== 'line').map(s => [s.id, centroid(s.points)])),
            kinds = new Map(shapes.map(s => [s.id, s.kind])),
            flows = (d.flows ?? []).filter(f => !(kinds.get(f.from_id) === 'queue' && kinds.get(f.to_id) === 'checkout')).slice(0, 14),
            top = Math.max(1, ...flows.map(f => f.count));
        for (const flow of flows) {
            const a = lookup.get(flow.from_id),
                b = lookup.get(flow.to_id);
            if (!a || !b) continue;
            const mx = (a[0] + b[0]) / 2,
                my = (a[1] + b[1]) / 2,
                dx = b[0] - a[0],
                dy = b[1] - a[1],
                bend = .18,
                cx = mx - dy * bend,
                cy = my + dx * bend,
                from = shapes.find(s => s.id === flow.from_id)?.name,
                to = shapes.find(s => s.id === flow.to_id)?.name;
            out += `<path class="map-flow" d="M${a[0]} ${a[1]}Q${cx} ${cy} ${b[0]} ${b[1]}" stroke-width="${(1.5+flow.count/top*8)*unit}" marker-end="url(#flow-arrow)" opacity="${(.35+flow.count/top*.6).toFixed(2)}" ${Chart.tipAttributes(number(flow.count),`${t(from)} → ${t(to)}`)}/>`;
        }
        for (const shape of shapes) {
            if (shape.kind === 'line') continue;
            const [x, y] = centroid(shape.points);
            out += `<text class="map-label" x="${x}" y="${y}" font-size="${12.5*unit}" stroke-width="${3*unit}" text-anchor="middle">${esc(t(shape.name))}</text>`;
        }
    }
    $('floor-overlay').innerHTML = out;
}

function renderOnboarding() {
    if (!state.includeGenerated) {
        $('store-empty').innerHTML = `<h2>${esc(t('Add your store recordings'))}</h2><button id="real-import" class="primary">${esc(t('Add video'))}</button>`;
        $('real-import').onclick=()=>$('import-open').click();
        return;
    }
    const demo = store.demo ?? {};
    const building = demo.status === 'building';
    $('store-empty').innerHTML = `<h2>${esc(t('See a whole store before installing anything'))}</h2><p>${esc(t('Build a simulated store with two weeks of traffic and one camera. It needs no footage, models or GPU, and it stays clearly labelled as simulated.'))}</p>${building?`<div class="build-progress"><progress value="${demo.progress??0}" max="1"></progress><span>${esc(buildStage(demo.stage))}</span></div>`:`<div class="toolbar"><button id="demo-build" class="primary">${esc(t('Prepare Aster Market'))}</button><button id="demo-import">${esc(t('Add my own video'))}</button></div>`}${demo.error?`<p class="field-note">${esc(demo.error)}</p>`:''}`;
    if (!building) {
        $('demo-build').onclick = task(startDemoBuild);
        $('demo-import').onclick = () => $('import-open').click();
    }
}

function buildStage(stage) {
    return {
        days: t('Simulating two weeks of shoppers…'),
        video: t('Rendering the camera view…'),
        clips: t('Preparing review clips…'),
        run: t('Saving the analysis…'),
        done: t('Finishing…')
    } [stage] ?? t('Starting…');
}
async function startDemoBuild() {
    store.demo = await api('/api/ops/demo-store', {
        method: 'POST'
    });
    renderOnboarding();
    clearInterval(store.demoTimer);
    store.demoTimer = setInterval(task(async () => {
        store.demo = await api('/api/ops/demo-store');
        if (store.demo.status === 'ready') {
            clearInterval(store.demoTimer);
            state.videos = playableVideos(await api('/api/videos'));
            await loadStore({
                print: true
            });
            await refreshWorkspace();
            library();
            await showDemoCamera();
            navigate('store');
        } else renderOnboarding();
    }), 800);
}

function statTile(label, value, delta, goodWhenUp = null) {
    const sign = delta === null || delta === undefined ? null : delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat',
        good = goodWhenUp === null || sign === null || sign === 'flat' ? '' : (sign === 'up') === goodWhenUp ? 'good' : 'bad',
        change = sign === null ? '' : `<small class="delta ${good}">${delta>0?'+':''}${esc(decimal(delta))}%</small>`;
    return `<article><small>${esc(label)}</small><b>${esc(value ?? '—')}</b>${change}</article>`;
}

function renderFlow() {
    const d = store.data;
    if (!d || d.mode === 'empty') {
        $('flow-section').hidden = true;
        return;
    }
    const k = d.kpis,
        delta = d.deltas ?? {};
    $('flow-kpis').innerHTML = [
        statTile(k.entries !== null ? t('Came in') : t('Observed tracks'), number(k.entries ?? k.tracks), delta.entries, true),
        statTile(t('Most in store'), number(k.people_peak), delta.people_peak),
        statTile(t('Time in store'), k.visit_minutes === null ? null : t('{n} min', {
            n: decimal(k.visit_minutes)
        })),
        statTile(t('Wait at the peak'), waitText(k.wait_peak_s), delta.wait_peak_s, false),
        statTile(t('To review'), number(k.needs_review), delta.needs_review, false)
    ].join('');
    const hours = d.hourly.filter(h => h.hour !== null),
        metric = d.flags.doors_configured ? 'per_day' : 'tracks_per_day';
    $('footfall-note').textContent = d.span_days > 1 ? t('Average per day over the period.') : t('People coming in each hour.');
    if (hours.length) Chart.columns($('footfall-chart'), hours.map(h => ({
        value: h[metric],
        tick: h.hour % 2 === 0 ? String(h.hour) : '',
        tipValue: number(Math.round(h[metric])),
        tipLabel: `${hourLabel(h.hour)}–${hourLabel(h.hour+1)}`
    })), {
        aria: t('Footfall by hour')
    });
    else $('footfall-chart').innerHTML = `<p class="empty-chart">${esc(t('Set recording start times to see hours.'))}</p>`;
    $('queue-note').textContent = d.flags.queue_configured ? t('Longest line each hour. The rule marks your limit of {n}.', {
        n: d.settings.queue_limit
    }) : t('Draw waiting and checkout areas to measure the queue.');
    if (hours.length && d.flags.queue_configured) Chart.columns($('queue-chart'), hours.map(h => ({
        value: h.queue_peak,
        tick: h.hour % 2 === 0 ? String(h.hour) : '',
        tipValue: number(h.queue_peak),
        tipLabel: h.wait_mean_s === null ? hourLabel(h.hour) : t('{hour}, mean wait {wait}', {
            hour: hourLabel(h.hour),
            wait: waitText(h.wait_mean_s)
        })
    })), {
        limit: d.settings.queue_limit,
        limitLabel: t('Limit'),
        aria: t('Queue by hour')
    });
    else $('queue-chart').innerHTML = '';
    renderZoneTable(d);
    renderFunnel(d);
    renderRoutes(d);
    renderTrend(d);
    renderQuadrant(d);
    renderFlowMap(d);
}

function renderTrend(d) {
    const rows = d.trend ?? [];
    $('trend-panel').hidden = rows.length < 3;
    if ($('trend-panel').hidden) return;
    $('trend-note').textContent = t('{n} days. The dashed line is the 7-day mean.', {
        n: rows.length
    });
    const every = rows.length > 10 ? 2 : 1,
        tick = (row, i) => i % every === (rows.length - 1) % every ? dayLabel(row.date, {
            day: 'numeric',
            month: 'short'
        }) : '',
        label = row => dayLabel(row.date, {
            weekday: 'long',
            day: 'numeric',
            month: 'long'
        }),
        draw = (id, field, text, options = {}) => Chart.trend($(id), rows.map((row, i) => ({
            value: row[field],
            tick: tick(row, i),
            tipValue: row[field] === null ? '—' : text(row[field]),
            tipLabel: label(row),
            selected: row.date >= d.start && row.date <= d.end
        })), options),
        people = d.flags.doors_configured ? 'entries' : 'tracks';
    draw('trend-entries', people, value => number(value), {
        height: 220,
        aria: t('Day by day')
    });
    draw('trend-wait', 'wait_peak_s', waitText, {
        height: 150,
        axis: value => waitText(value),
        aria: t('Wait at the peak')
    });
    draw('trend-queue', 'queue_over_minutes', value => t('{n} min', {
        n: decimal(value)
    }), {
        height: 150,
        aria: t('Minutes over the queue limit')
    });
}

function renderQuadrant(d) {
    const shelves = d.zones.filter(z => z.kind === 'shelf' && z.visits > 0);
    $('quadrant-panel').hidden = !d.flags.pickups_scored || shelves.length < 4;
    if ($('quadrant-panel').hidden) return;
    Chart.quadrant($('quadrant-chart'), shelves.map(z => ({
        x: z.visits / d.span_days,
        y: z.pickup_rate * 100,
        size: z.dwell_per_visit_s,
        label: t(z.name),
        emphasis: true,
        tipValue: t(z.name),
        tipLabel: t('{visits} visits a day, {rate}% pickups, {dwell} s a stop', {
            visits: number(Math.round(z.visits / d.span_days)),
            rate: decimal(z.pickup_rate * 100),
            dwell: decimal(z.dwell_per_visit_s)
        })
    })), {
        aria: t('Attention against pickups'),
        xTitle: t('Visits a day'),
        axisY: value => Math.round(value) + '%',
        corners: [t('Few visit, many take'), t('Doing well'), t('Quiet shelves'), t('They stop but do not take')]
    });
}

function renderFlowMap(d) {
    const flows = d.flows ?? [];
    $('flow-panel').hidden = flows.length < 3;
    if ($('flow-panel').hidden) return;
    const names = Object.fromEntries(d.zones.map(z => [z.id, t(z.name)]));
    Chart.flow($('flow-chart'), flows.map(f => ({
        from: f.from_id,
        to: f.to_id,
        count: f.count,
        tipValue: number(f.count),
        tipLabel: `${names[f.from_id] ?? f.from_id} → ${names[f.to_id] ?? f.to_id}`
    })), {
        name: id => names[id] ?? id,
        aria: t('Where people go next')
    });
}

function renderZoneTable(d) {
    const rows = d.zones.filter(z => !['queue', 'checkout'].includes(z.kind)),
        top = Math.max(1, ...rows.map(r => r.tracks));
    if (!rows.length) {
        $('zone-table').innerHTML = `<p class="empty-chart">${esc(t('Map shelf areas in Camera setup to rank them.'))}</p>`;
        return;
    }
    $('zone-table').innerHTML = `<table class="data-table"><thead><tr><th>${esc(t('Area'))}</th><th class="wide">${esc(t('Tracks'))}</th><th>${esc(t('Visits'))}</th><th>${esc(t('Total time near shelf'))}</th><th>${esc(t('Time per visit'))}</th><th>${esc(t('Pickups'))}</th><th>${esc(t('Pickup rate'))}</th><th>${esc(t('To review'))}</th></tr></thead><tbody>${rows.map(z=>`<tr><td>${esc(t(z.name))}${z.product_type?`<small class="muted">${esc(t(z.product_type))}</small>`:""}</td><td class="wide"><span class="bar-cell"><span class="bar-fill" style="width:${z.tracks/top*100}%"></span></span><span class="bar-value">${number(z.tracks)}</span></td><td>${number(z.visits)}</td><td>${sec(z.dwell_s)}</td><td>${z.dwell_per_visit_s===null?'—':Math.round(z.dwell_per_visit_s)+' '+esc(t('s'))}</td><td>${number(z.pickups)}</td><td>${z.pickup_rate===null?'—':Math.round(z.pickup_rate*100)+'%'}</td><td>${z.needs_review?`<span class="count-flag">${number(z.needs_review)}</span>`:'—'}</td></tr>`).join('')}</tbody></table>`;
}

function renderFunnel(d) {
    const steps = [
            ['observed', t('Tracks in store')],
            ['engaged', t('Stopped at a shelf')],
            ['checkout', t('Went on to a till')]
        ].map(([key, label]) => [key, label, d.funnel?.[key]]).filter(s => s[2] !== undefined),
        top = Math.max(1, ...steps.map(s => s[2]));
    $('funnel-chart').innerHTML = steps.map(([key, label, value], index) => `<div class="funnel-row"><span>${esc(label)}</span><span class="funnel-track"><span class="funnel-fill step-${index}" style="width:${value/top*100}%"></span></span><b>${number(value)}</b><small>${index&&steps[0][2]?Math.round(value/steps[0][2]*100)+'%':''}</small></div>`).join('') || `<p class="empty-chart">${esc(t('Map doors, shelves and a checkout to build the funnel.'))}</p>`;
}

function renderRoutes(d) {
    const rows = d.routes ?? [];
    $('route-list').innerHTML = rows.slice(0, 6).map(r => `<div class="route-row"><span class="route-steps">${r.steps.map(s=>`<span class="route-stop ${esc(s.kind)}">${esc(t(s.name))}</span>`).join('<span class="route-arrow" aria-hidden="true"></span>')}</span><b>${number(r.count)}</b></div>`).join('') || `<p class="empty-chart">${esc(t('Routes need mapped areas and continuous tracks.'))}</p>`;
}

function changeDay(step) {
    const days = store.data?.available_days ?? [],
        next = days[days.indexOf(store.day) + step];
    if (!next) return;
    store.day = next;
    stopHourPlay();
    return loadStore({
        print: true
    });
}

function stopHourPlay() {
    clearInterval(store.timer);
    store.timer = null;
    $('hour-play-icon').setAttribute('href', '#i-play');
}
$('store-select').onchange = task(() => changeStore($('store-select').value));
$('day-picker').onchange = task(() => {
    store.day = $('day-picker').value;
    return loadStore({
        print: true
    });
});
$('day-prev').onclick = task(() => changeDay(-1));
$('day-next').onclick = task(() => changeDay(1));
document.querySelectorAll('[data-span]').forEach(button => button.onclick = task(() => {
    store.span = Number(button.dataset.span);
    return loadStore({
        print: true
    });
}));
document.querySelectorAll('[data-layer]').forEach(button => button.onclick = () => {
    store.layer = button.dataset.layer;
    store.heatKey = '';
    renderMap();
});
$('hour-range').oninput = () => {
    stopHourPlay();
    const value = Number($('hour-range').value);
    store.hour = value >= Number($('hour-range').max) ? null : value;
    renderMap();
};
$('hour-play').onclick = () => {
    if (store.timer) {
        stopHourPlay();
        return;
    }
    const hours = mapHours();
    if (!hours.length) return;
    let index = 0;
    $('hour-play-icon').setAttribute('href', '#i-pause');
    store.hour = hours[0];
    renderMap();
    store.timer = setInterval(() => {
        index += 1;
        if (index >= hours.length) {
            stopHourPlay();
            store.hour = null;
        } else store.hour = hours[index];
        renderMap();
    }, 700);
};
window.addEventListener('resize', () => {
    clearTimeout(store.resize);
    store.resize = setTimeout(() => {
        if (ops.view === 'analytics') renderFlow();
    }, 180);
});
