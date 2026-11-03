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

    return {
        niceMax,
        width,
        attribute,
        tipAttributes,
        columns,
        area
    };
})();
