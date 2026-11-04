'use strict';
const workBoard = {
    data: null,
    store: null,
    edit: null,
    generation: 0
};

function workStatus(status) {
    return t({
        open: 'Open',
        in_progress: 'In progress',
        completed: 'Completed',
        cancelled: 'Cancelled'
    } [status]);
}

function workKind(kind) {
    return t({
        manual: 'Store task',
        refill: 'Refill shelf',
        audit: 'Check shelf stock'
    } [kind]);
}
async function loadWorkOrders(id) {
    const generation = ++workBoard.generation;
    const selected = id ?? workBoard.store ?? store.id;
    const data = await api('/api/ops/work-orders' + (selected ? '?store_id=' + encodeURIComponent(selected) : ''));
    if (generation !== workBoard.generation) return;
    workBoard.data = data;
    workBoard.store = data.store?.id ?? null;
    renderWorkOrders();
}

function renderWorkOrders() {
    const data = workBoard.data;
    if (!data) return;
    setSimulatedTape();
    $('work-store').innerHTML = data.stores.map(s => `<option value="${esc(s.id)}">${esc(storeName(s))}</option>`).join('');
    $('work-store').value = workBoard.store ?? '';
    $('store-profile-name').value = data.store ? storeName(data.store) : '';
    for (const id of ['work-new', 'work-handover', 'work-csv']) $(id).disabled = !data.store;
    $('work-basis').textContent = data.store?.simulated ? t('Simulated store activity. Work orders and stock changes are saved locally.') : t('Work orders use confirmed counts and saved camera mappings. Assignments are local operator labels.');
    $('work-kpis').innerHTML = [
        [t('Active work'), data.summary.open ?? 0],
        [t('Overdue'), data.summary.overdue ?? 0],
        [t('Completed'), data.summary.completed ?? 0],
        [t('Refill queue'), data.summary.refill ?? 0]
    ].map(([k, v]) => `<article class="stock-kpi"><small>${esc(k)}</small><strong>${v}</strong></article>`).join('');
    const filter = $('work-filter').value;
    const tasks = data.tasks.filter(task => filter === 'all' || (filter === 'active' ? ['open', 'in_progress'].includes(task.status) : task.status === 'completed'));
    $('work-grid').innerHTML = tasks.map(task => `<article class="work-card ${task.overdue?'overdue':''}"><div class="stock-card-head"><span class="stock-status">${esc(workKind(task.kind))}</span><span class="work-state ${task.status}">${esc(workStatus(task.status))}</span></div><h3>${esc(t(task.title))}</h3><p class="muted">${esc(task.assignee || t('Unassigned'))}${task.camera_id?' · '+esc(task.camera_id):''}</p><p class="work-due">${task.overdue?'<b>'+esc(t('Overdue'))+' · </b>':''}${task.due_at?esc(new Date(task.due_at).toLocaleString(locale())):esc(t('No due time'))}</p><p class="work-card-notes">${esc(task.notes)}</p><div class="toolbar"><button data-work="${esc(task.id)}">${esc(t(['completed','cancelled'].includes(task.status)?'View history':'Manage work'))}</button>${task.video_id?`<button data-work-shelf="${esc(task.video_id)}">${esc(t('Open shelf operations'))}</button>`:''}</div></article>`).join('') || `<div class="empty"><h3>${esc(t('No work in this view.'))}</h3><p>${esc(t('Low stock and overdue shelf checks create tasks automatically. You can also add a store task.'))}</p></div>`;
    document.querySelectorAll('[data-work]').forEach(b => b.onclick = () => openWorkOrder(b.dataset.work));
    document.querySelectorAll('[data-work-shelf]').forEach(b => b.onclick = () => {
        shelfOps.video = b.dataset.workShelf;
        navigate('shelves');
    });
    $('work-policies').innerHTML = data.shelves.length ? `<table><thead><tr>${['Shelf','Next check','Check every (hours)','Action'].map(s=>'<th>'+esc(t(s))+'</th>').join('')}</tr></thead><tbody>${data.shelves.map((s,i)=>`<tr><td>${esc(t(s.name))}<small class="muted">${esc(s.camera_id)} · ${esc(s.product_type)}</small></td><td>${esc(new Date(s.check_due_at).toLocaleString(locale()))}</td><td><input type="number" min="1" max="168" step="1" value="${s.policy.check_interval_hours}" id="work-policy-${i}" aria-label="${esc(t('Check every (hours)'))}"></td><td><button data-policy="${i}">${esc(t('Save'))}</button></td></tr>`).join('')}</tbody></table>` : `<p class="field-note">${esc(t('Map shelves in Camera setup to schedule checks.'))}</p>`;
    document.querySelectorAll('[data-policy]').forEach(b => b.onclick = task(async () => {
        const index = Number(b.dataset.policy),
            s = data.shelves[index];
        b.disabled = true;
        try {
            await api(`/api/ops/recordings/${s.video_id}/shelves/${encodeURIComponent(s.id)}/policy`, json('PUT', {
                check_interval_hours: Number($('work-policy-' + index).value),
                expected_revision: s.policy.revision
            }));
            await loadWorkOrders();
        } finally {
            b.disabled = false;
        }
    }));
    $('work-cameras').innerHTML = data.cameras.map(c => `<button class="work-camera-link" data-work-camera="${esc(c.video_id)}"><b>${esc(c.camera_id)}</b><span>${esc(c.name)}</span></button>`).join('');
    document.querySelectorAll('[data-work-camera]').forEach(b => b.onclick = task(async () => {
        await loadVideo(b.dataset.workCamera);
        navigate('overview');
    }));
}

function openWorkOrder(id) {
    const order = id ? workBoard.data.tasks.find(t => t.id === id) : null;
    workBoard.edit = order;
    const closed = order && ['completed', 'cancelled'].includes(order.status);
    $('work-dialog-title').textContent = order ? workKind(order.kind) + ' · ' + t(order.title) : t('New task');
    $('work-create-fields').hidden = !!order;
    $('work-edit-fields').hidden = !order;
    $('work-title').value = '';
    $('work-due').value = '';
    $('work-status').value = order?.status ?? 'open';
    $('work-assignee').value = order?.assignee ?? '';
    $('work-notes').value = order?.notes ?? '';
    $('work-error').textContent = '';
    for (const field of ['work-status', 'work-assignee', 'work-notes']) $(field).disabled = !!closed;
    $('work-save').hidden = !!closed;
    $('work-status').querySelector('[value=cancelled]').disabled = !!order && order.kind !== 'manual';
    $('work-history').innerHTML = (order?.history ?? []).map(h => `<p><b>${esc(workStatus(h.status))}</b> · ${esc(new Date(h.at).toLocaleString(locale()))}<br>${esc(h.assignee??'')} ${esc(h.notes)}</p>`).join('');
    setWorkQuantity();
    $('work-dialog').showModal();
}

function setWorkQuantity() {
    const order = workBoard.edit,
        shelf = order ? workBoard.data.shelves.find(s => s.id === order.shelf_id && s.camera_id === order.camera_id) : null;
    const needed = order && order.kind !== 'manual' && $('work-status').value === 'completed' && !['completed', 'cancelled'].includes(order.status);
    $('work-quantity-field').hidden = !needed;
    $('work-quantity').required = !!needed;
    $('work-quantity-label').textContent = t(order?.kind === 'refill' ? 'Confirmed refill units' : 'Counted units on shelf');
    $('work-quantity').value = order?.kind === 'refill' ? (shelf?.refill_units ?? 0) : (shelf?.stock?.on_hand ?? 0);
}
$('work-status').onchange = setWorkQuantity;
$('work-new').onclick = () => openWorkOrder(null);
$('work-close').onclick = () => $('work-dialog').close();
$('work-form').onsubmit = async event => {
    event.preventDefault();
    $('work-save').disabled = true;
    try {
        const order = workBoard.edit;
        if (order) {
            const shelf = workBoard.data.shelves.find(s => s.id === order.shelf_id && s.camera_id === order.camera_id);
            await api('/api/ops/work-orders/' + order.id, json('PUT', {
                status: $('work-status').value,
                assignee: $('work-assignee').value,
                notes: $('work-notes').value,
                expected_revision: order.revision,
                quantity: $('work-quantity-field').hidden ? null : Number($('work-quantity').value),
                expected_stock_revision: shelf?.stock?.revision ?? 0
            }));
        } else {
            const due = $('work-due').value;
            await api('/api/ops/work-orders', json('POST', {
                store_id: workBoard.store,
                title: $('work-title').value,
                assignee: $('work-assignee').value,
                notes: $('work-notes').value,
                due_at: due ? new Date(due).toISOString() : null
            }));
        }
        $('work-dialog').close();
        await loadWorkOrders();
    } catch (error) {
        $('work-error').textContent = error.message;
    } finally {
        $('work-save').disabled = false;
    }
};
$('work-refresh').onclick = task(() => loadWorkOrders());
$('work-store').onchange = task(() => loadWorkOrders($('work-store').value));
$('work-filter').onchange = renderWorkOrders;
$('store-profile-form').onsubmit = async event => {
    event.preventDefault();
    try {
        await api(`/api/ops/stores/${encodeURIComponent(workBoard.store)}/profile`, json('PUT', {
            name: $('store-profile-name').value
        }));
        await loadWorkOrders();
        await loadStore();
        toast(t('Store name saved.'));
    } catch (error) {
        toast(error.message, true);
    }
};
$('work-csv').onclick = () => exportRows(workBoard.data.tasks.map(t => ({
    ...t,
    simulated: workBoard.data.store.simulated
})), ['title', 'kind', 'status', 'assignee', 'due_at', 'camera_id', 'shelf_id', 'notes', 'created_at', 'updated_at', 'simulated'], 'store-work-orders.csv');
$('work-handover').onclick = () => {
    const d = workBoard.data;
    const clean = s => String(s ?? '').replaceAll(/\r?\n/g, ' ').replaceAll(/[<>]/g, '');
    const lines = ['# ' + clean(storeName(d.store)) + ' — ' + t('Shift handover'), '', t('Generated') + ': ' + new Date(d.as_of).toLocaleString(locale()), d.store.simulated ? t('Simulated store activity. Work orders and stock changes are saved locally.') : '', '', '## ' + t('Active work'), ''];
    for (const order of d.tasks.filter(t => ['open', 'in_progress'].includes(t.status))) lines.push('- ' + clean(workKind(order.kind)) + ' · ' + clean(order.title) + ' | ' + clean(workStatus(order.status)) + ' | ' + clean(order.assignee || t('Unassigned')) + ' | ' + (order.due_at ? new Date(order.due_at).toLocaleString(locale()) : t('No due time')) + '\n  ' + clean(order.notes));
    lines.push('', '## ' + t('Shelf stock'), '', '| ' + [t('Shelf'), t('Stock'), t('Refill at or below'), t('Next check')].join(' | ') + ' |', '| --- | --- | --- | --- |');
    for (const s of d.shelves) lines.push('| ' + [s.name + ' / ' + s.product_type, s.stock ? s.stock.on_hand + '/' + s.stock.capacity : t('Count needed'), s.stock?.reorder_at ?? '—', new Date(s.check_due_at).toLocaleString(locale())].map(v => clean(v).replaceAll('|', '/')).join(' | ') + ' |');
    lines.push('', '## ' + t('Completed work'), '');
    for (const order of d.tasks.filter(t => t.status === 'completed')) lines.push('- ' + clean(order.title) + ' | ' + clean(order.assignee) + ' | ' + new Date(order.updated_at).toLocaleString(locale()) + ' | ' + clean(order.notes));
    downloadObject(lines.join('\n'), 'shift-handover.md', 'text/markdown;charset=utf-8');
};
setInterval(() => {
    if (ops.view === 'work' && !$('work-dialog').open && document.visibilityState === 'visible') loadWorkOrders().catch(() => {});
}, 30000);
$('shelf-work-board').onclick = () => {
    workBoard.store = shelfOps.data?.store_id ?? null;
    navigate('work');
};