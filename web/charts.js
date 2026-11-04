'use strict';
// Small SVG chart helpers. Colours come from CSS so both themes share one code path.
const Chart = (() => {
    const tip = () => document.getElementById('chart-tip');

    function niceMax(value) {
        if (value <= 5) return Math.max(1, Math.ceil(value));
        const magnitude = 10 ** Math.floor(Math.log10(value)),
            scaled = value / magnitude,
            step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 2.5 ? 2.5 : scaled <= 5 ? 5 : 10;
        return step * magnitude;
    }

    // A tighter ceiling than niceMax, for charts that need the room more than round ticks.
    function closeMax(value) {
        if (value <= 5) return Math.max(1, Math.ceil(value));
        const step = 10 ** Math.floor(Math.log10(value)) / 2;
        return Math.ceil(value / step) * step;
    }

    function width(element, fallback = 640) {
        return Math.max(280, element.clientWidth || element.parentElement?.clientWidth || fallback);
    }

    function attribute(value) {
        return String(value ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#39;'
        } [c]));
    }

    // value and label stay separate so the value can lead in the tooltip.
    function tipAttributes(value, label) {
        return `data-tip-value="${attribute(value)}" data-tip-label="${attribute(label)}" tabindex="0"`;
    }

    function showTip(target, x, y) {
        const box = tip();
        box.replaceChildren();
        const strong = document.createElement('b');
        strong.textContent = target.dataset.tipValue;
        const small = document.createElement('span');
        small.textContent = target.dataset.tipLabel ?? '';
        box.append(strong, small);
        box.hidden = false;
        const rect = box.getBoundingClientRect(),
            left = Math.min(innerWidth - rect.width - 8, Math.max(8, x - rect.width / 2)),
            top = y - rect.height - 12;
        box.style.left = left + 'px';
        box.style.top = (top < 8 ? y + 16 : top) + 'px';
    }

    document.addEventListener('pointermove', event => {
        const target = event.target.closest?.('[data-tip-value]');
        if (target) showTip(target, event.clientX, event.clientY);
        else if (!tip().hidden && document.activeElement?.dataset?.tipValue === undefined) tip().hidden = true;
    });
    document.addEventListener('focusin', event => {
        const target = event.target.closest?.('[data-tip-value]');
        if (!target) return;
        const rect = target.getBoundingClientRect();
        showTip(target, rect.left + rect.width / 2, rect.top);
    });
    document.addEventListener('focusout', () => {
        tip().hidden = true
    });

    // One series of columns. rows: [{value, label, tick, tipValue, tipLabel}]
    function columns(element, rows, options = {}) {
        const W = width(element),
            H = options.height ?? 220,
            left = 34,
            right = 8,
            top = 22,
            bottom = 26,
            plot = H - top - bottom,
            values = rows.map(r => r.value ?? 0),
            highest = Math.max(...values, options.limit ?? 0, 1),
            max = niceMax(highest),
            slot = (W - left - right) / Math.max(1, rows.length),
            bar = Math.min(24, Math.max(4, slot * .68)),
            peak = values.indexOf(Math.max(...values));
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${attribute(options.aria)}">`;
        for (const share of [0, .5, 1]) {
            const y = top + plot * (1 - share),
                value = max * share;
            svg += `<line class="grid-line" x1="${left}" x2="${W-right}" y1="${y}" y2="${y}"/><text class="axis-text" x="${left-8}" y="${y+4}" text-anchor="end">${Number.isInteger(value)?value:value.toFixed(1)}</text>`;
        }
        if (options.limit) {
            const y = top + plot * (1 - options.limit / max);
            svg += `<line class="limit-line" x1="${left}" x2="${W-right}" y1="${y}" y2="${y}"/><text class="axis-text" x="${W-right}" y="${y-5}" text-anchor="end">${attribute(options.limitLabel)}</text>`;
        }
        rows.forEach((row, i) => {
            const value = row.value ?? 0,
                h = value / max * plot,
                x = left + slot * i + (slot - bar) / 2,
                y = top + plot - h,
                radius = Math.min(4, h, bar / 2),
                shape = h > 0 ? `M${x} ${top+plot}V${y+radius}Q${x} ${y} ${x+radius} ${y}H${x+bar-radius}Q${x+bar} ${y} ${x+bar} ${y+radius}V${top+plot}Z` : '',
                accent = i === peak && value > 0;
            svg += `<g class="column-hit" ${tipAttributes(row.tipValue ?? value, row.tipLabel ?? row.label)}><rect x="${left+slot*i}" y="${top}" width="${slot}" height="${plot}" fill="transparent"/><path class="column ${accent?'peak':''}" d="${shape}"/></g>`;
            if (accent) svg += `<text class="value-text" x="${x+bar/2}" y="${y-6}" text-anchor="middle">${attribute(row.tipValue ?? value)}</text>`;
            if (row.tick) svg += `<text class="axis-text" x="${x+bar/2}" y="${H-7}" text-anchor="middle">${attribute(row.tick)}</text>`;
        });
        element.innerHTML = svg + '</svg>';
    }

    // Filled area over a continuous axis, with a crosshair on hover.
    function area(element, points, options = {}) {
        const W = width(element),
            H = options.height ?? 180,
            left = 30,
            right = 8,
            top = 12,
            bottom = 24,
            plot = H - top - bottom,
            max = niceMax(Math.max(1, ...points.map(p => p.y))),
            start = points[0]?.x ?? 0,
            end = points.at(-1)?.x ?? 1,
            span = Math.max(1, end - start),
            scaleX = x => left + (x - start) / span * (W - left - right),
            scaleY = y => top + plot - y / max * plot;
        const step = Math.max(1, Math.ceil(points.length / 400)),
            thin = points.filter((p, i) => i % step === 0 || i === points.length - 1),
            line = thin.map(p => `${scaleX(p.x).toFixed(1)},${scaleY(p.y).toFixed(1)}`).join(' ');
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${attribute(options.aria)}">`;
        for (const share of [0, .5, 1]) {
            const y = top + plot * (1 - share),
                value = max * share;
            svg += `<line class="grid-line" x1="${left}" x2="${W-right}" y1="${y}" y2="${y}"/><text class="axis-text" x="${left-8}" y="${y+4}" text-anchor="end">${Number.isInteger(value)?value:value.toFixed(1)}</text>`;
        }
        if (thin.length) svg += `<polygon class="area-fill" points="${left},${top+plot} ${line} ${scaleX(end).toFixed(1)},${top+plot}"/><polyline class="area-line" points="${line}"/>`;
        for (let i = 0; i <= 4; i++) {
            const x = start + span * i / 4;
            svg += `<text class="axis-text" x="${scaleX(x)}" y="${H-6}" text-anchor="${i===0?'start':i===4?'end':'middle'}">${attribute(options.tick ? options.tick(x) : x)}</text>`;
        }
        svg += `<line class="crosshair" x1="0" x2="0" y1="${top}" y2="${top+plot}" visibility="hidden"/></svg>`;
        element.innerHTML = svg;
        const root = element.querySelector('svg'),
            cross = root.querySelector('.crosshair');
        root.addEventListener('pointermove', event => {
            const rect = root.getBoundingClientRect(),
                position = (event.clientX - rect.left) / rect.width * W,
                time = start + Math.max(0, Math.min(1, (position - left) / (W - left - right))) * span;
            let best = points[0],
                gap = Infinity;
            for (const point of thin) {
                const distance = Math.abs(point.x - time);
                if (distance < gap) {
                    gap = distance;
                    best = point
                }
            }
            if (!best) return;
            cross.setAttribute('x1', scaleX(best.x));
            cross.setAttribute('x2', scaleX(best.x));
            cross.setAttribute('visibility', 'visible');
            cross.dataset.tipValue = options.tipValue ? options.tipValue(best) : best.y;
            cross.dataset.tipLabel = options.tipLabel ? options.tipLabel(best) : '';
            showTip(cross, event.clientX, rect.top + scaleY(best.y) * rect.height / H);
        });
        root.addEventListener('pointerleave', () => {
            cross.setAttribute('visibility', 'hidden');
            tip().hidden = true
        });
    }

    // A day-by-day line with a rolling mean behind it. rows: [{value, tick, tipValue, tipLabel, selected}]
    function trend(element, rows, options = {}) {
        const W = width(element),
            H = options.height ?? 200,
            left = 38,
            right = 12,
            top = 22,
            bottom = 26,
            plot = H - top - bottom,
            known = rows.filter(r => r.value !== null && r.value !== undefined),
            max = closeMax(Math.max(1, ...known.map(r => r.value)) * 1.06),
            step = (W - left - right) / Math.max(1, rows.length - 1),
            x = i => left + step * i,
            y = value => top + plot * (1 - value / max),
            mean = rows.map((_, i) => {
                const window = rows.slice(Math.max(0, i - 6), i + 1).filter(r => r.value !== null && r.value !== undefined);
                return i >= 6 && window.length ? window.reduce((sum, r) => sum + r.value, 0) / window.length : null;
            });
        const path = values => {
            let line = '',
                open = false;
            values.forEach((value, i) => {
                if (value === null || value === undefined) {
                    open = false;
                    return;
                }
                line += `${open ? 'L' : 'M'}${x(i).toFixed(1)} ${y(value).toFixed(1)}`;
                open = true;
            });
            return line;
        };
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${attribute(options.aria)}">`;
        for (const share of [0, .5, 1]) {
            const value = max * share,
                position = top + plot * (1 - share);
            svg += `<line class="grid-line" x1="${left}" x2="${W-right}" y1="${position}" y2="${position}"/><text class="axis-text" x="${left-8}" y="${position+4}" text-anchor="end">${attribute(options.axis ? options.axis(value) : Math.round(value))}</text>`;
        }
        if (mean.some(v => v !== null)) svg += `<path class="trend-mean" d="${path(mean)}"/>`;
        svg += `<path class="area-line" d="${path(rows.map(r => r.value))}"/>`;
        rows.forEach((row, i) => {
            if (row.tick) svg += `<text class="axis-text" x="${x(i)}" y="${H-7}" text-anchor="${i===0?'start':i===rows.length-1?'end':'middle'}">${attribute(row.tick)}</text>`;
            if (row.value === null || row.value === undefined) return;
            const position = y(row.value),
                slot = Math.max(16, step);
            svg += `<g class="trend-hit" ${tipAttributes(row.tipValue ?? row.value, row.tipLabel)}><rect x="${x(i)-slot/2}" y="${top}" width="${slot}" height="${plot}" fill="transparent"/><circle class="trend-dot${row.selected?' selected':''}" cx="${x(i)}" cy="${position}" r="${row.selected?5.5:3.5}"/></g>`;
            if (row.selected) svg += `<text class="value-text" x="${Math.min(W-right, Math.max(left, x(i)))}" y="${Math.max(13, position-11)}" text-anchor="${i===rows.length-1?'end':i===0?'start':'middle'}">${attribute(row.tipValue ?? row.value)}</text>`;
        });
        element.innerHTML = svg + '</svg>';
    }

    // Two-column flow: every area on the left is where a move began, on the right where it ended.
    // This reads cleanly even though people walk in loops. links: [{from, to, count, tipLabel}]
    function flow(element, links, options = {}) {
        const W = width(element),
            narrow = W < 560,
            label = narrow ? 84 : 132,
            gap = 7,
            sum = (key, id) => links.reduce((total, link) => total + (link[key] === id ? link.count : 0), 0),
            side = key => [...new Set(links.map(link => link[key]))].map(id => ({
                id,
                total: sum(key, id)
            })).sort((a, b) => b.total - a.total),
            starts = side('from'),
            ends = side('to'),
            total = links.reduce((all, link) => all + link.count, 0),
            rows = Math.max(starts.length, ends.length),
            H = Math.max(280, rows * 40),
            scale = (H - 16 - gap * (rows - 1)) / Math.max(1, total),
            place = nodes => {
                const used = nodes.reduce((all, node) => all + node.total * scale, 0) + gap * (nodes.length - 1);
                let y = 8 + (H - 16 - used) / 2;
                return Object.fromEntries(nodes.map(node => {
                    const box = {
                        ...node,
                        y,
                        h: Math.max(2, node.total * scale),
                        used: 0
                    };
                    y += box.h + gap;
                    return [node.id, box];
                }));
            },
            startBox = place(starts),
            endBox = place(ends),
            x0 = label + 10,
            x1 = W - label - 10,
            middle = (x0 + x1) / 2,
            name = id => options.name ? options.name(id) : id;
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${attribute(options.aria)}">`;
        [...links].sort((a, b) => b.count - a.count).forEach(link => {
            const a = startBox[link.from],
                b = endBox[link.to],
                thick = Math.max(1, link.count * scale),
                ya = a.y + a.used,
                yb = b.y + b.used;
            a.used += thick;
            b.used += thick;
            svg += `<path class="flow-link" data-from="${attribute(link.from)}" data-to="${attribute(link.to)}" ${tipAttributes(link.tipValue ?? link.count, link.tipLabel ?? `${name(link.from)} → ${name(link.to)}`)} d="M${x0} ${ya.toFixed(1)}C${middle} ${ya.toFixed(1)} ${middle} ${yb.toFixed(1)} ${x1} ${yb.toFixed(1)}V${(yb+thick).toFixed(1)}C${middle} ${(yb+thick).toFixed(1)} ${middle} ${(ya+thick).toFixed(1)} ${x0} ${(ya+thick).toFixed(1)}Z"/>`;
        });
        for (const box of Object.values(startBox)) svg += `<g class="flow-node" data-node="${attribute(box.id)}"><rect x="${x0-5}" y="${box.y}" width="5" height="${box.h}" rx="2"/><text class="axis-text flow-label" x="${x0-10}" y="${box.y+box.h/2+4}" text-anchor="end">${attribute(name(box.id))}</text></g>`;
        for (const box of Object.values(endBox)) svg += `<g class="flow-node" data-node="${attribute(box.id)}"><rect x="${x1}" y="${box.y}" width="5" height="${box.h}" rx="2"/><text class="axis-text flow-label" x="${x1+10}" y="${box.y+box.h/2+4}">${attribute(name(box.id))}</text></g>`;
        element.innerHTML = svg + '</svg>';
        const root = element.querySelector('svg');
        root.querySelectorAll('.flow-node').forEach(node => {
            const id = node.dataset.node,
                paint = on => root.querySelectorAll('.flow-link').forEach(link => {
                    const linked = link.dataset.from === id || link.dataset.to === id;
                    link.classList.toggle('lit', on && linked);
                    link.classList.toggle('dim', on && !linked);
                });
            node.addEventListener('pointerenter', () => paint(true));
            node.addEventListener('pointerleave', () => paint(false));
        });
    }

    // Scatter split at the medians, so each corner names what to do. points: [{x, y, size, label, tipValue, tipLabel}]
    function quadrant(element, points, options = {}) {
        const W = width(element),
            H = options.height ?? 400,
            narrow = W < 560,
            left = 44,
            right = 18,
            top = 18,
            bottom = 38,
            plotW = W - left - right,
            plotH = H - top - bottom,
            median = values => {
                const sorted = [...values].sort((a, b) => a - b),
                    half = Math.floor(sorted.length / 2);
                return sorted.length % 2 ? sorted[half] : (sorted[half - 1] + sorted[half]) / 2;
            },
            maxX = closeMax(Math.max(...points.map(p => p.x)) * 1.1),
            maxY = closeMax(Math.max(...points.map(p => p.y)) * 1.18),
            x = value => left + value / maxX * plotW,
            y = value => top + plotH * (1 - value / maxY),
            midX = median(points.map(p => p.x)),
            midY = median(points.map(p => p.y)),
            biggest = Math.max(...points.map(p => p.size), 1),
            radius = size => 6 + 12 * Math.sqrt(size / biggest);
        let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${attribute(options.aria)}">`;
        svg += `<rect class="quadrant-focus" x="${x(midX)}" y="${y(midY)}" width="${W-right-x(midX)}" height="${top+plotH-y(midY)}"/>`;
        for (const share of [0, .5, 1]) {
            const px = left + plotW * share,
                py = top + plotH * (1 - share);
            svg += `<line class="grid-line" x1="${left}" x2="${W-right}" y1="${py}" y2="${py}"/><text class="axis-text" x="${left-8}" y="${py+4}" text-anchor="end">${attribute(options.axisY ? options.axisY(maxY*share) : maxY*share)}</text><text class="axis-text" x="${px}" y="${top+plotH+17}" text-anchor="${share===0?'start':share===1?'end':'middle'}">${attribute(Math.round(maxX*share))}</text>`;
        }
        svg += `<line class="median-line" x1="${x(midX)}" x2="${x(midX)}" y1="${top}" y2="${top+plotH}"/><line class="median-line" x1="${left}" x2="${W-right}" y1="${y(midY)}" y2="${y(midY)}"/>`;
        const corners = options.corners ?? [];
        if (corners.length === 4) {
            svg += `<text class="quadrant-label" x="${left+8}" y="${top+16}">${attribute(corners[0])}</text><text class="quadrant-label" x="${W-right-8}" y="${top+16}" text-anchor="end">${attribute(corners[1])}</text><text class="quadrant-label" x="${left+8}" y="${top+plotH-9}">${attribute(corners[2])}</text><text class="quadrant-label strong" x="${W-right-8}" y="${top+plotH-9}" text-anchor="end">${attribute(corners[3])}</text>`;
        }
        svg += `<text class="axis-text" x="${left+plotW/2}" y="${H-3}" text-anchor="middle">${attribute(options.xTitle)}</text>`;
        const taken = [];
        [...points].sort((a, b) => b.size - a.size).forEach(point => {
            const px = x(point.x),
                py = y(point.y),
                r = radius(point.size);
            svg += `<g class="quadrant-hit" ${tipAttributes(point.tipValue, point.tipLabel)}><circle class="quadrant-dot" cx="${px}" cy="${py}" r="${r}"/></g>`;
        });
        points.forEach(point => {
            const px = x(point.x),
                r = radius(point.size),
                flip = px > W - right - 90,
                lx = flip ? px - r - 5 : px + r + 5;
            let ly = y(point.y) + 4;
            // Nudge a label that would sit on one already placed.
            for (const other of taken.sort((a, b) => a.y - b.y)) {
                if (Math.abs(other.x - lx) < 96 && Math.abs(other.y - ly) < 14) ly = other.y + 14;
            }
            taken.push({
                x: lx,
                y: ly
            });
            if (!narrow || point.emphasis) svg += `<text class="value-text quadrant-name" x="${lx}" y="${ly}" text-anchor="${flip?'end':'start'}">${attribute(point.label)}</text>`;
        });
        element.innerHTML = svg + '</svg>';
    }

    return {
        niceMax,
        width,
        attribute,
        tipAttributes,
        columns,
        area,
        trend,
        flow,
        quadrant
    };
})();
