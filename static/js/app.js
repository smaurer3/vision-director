// ── State ──
let ws = null;
let wsReconnectTimer = null;
let channels = [];
let rules = [];
let settings = {};
let gateStates = {};
let pendingTriggers = {};
let pendingGhold = {}; // channel_id -> { gholdVal, additionalHold }
let engineRunning = false;

// ── WebSocket ──
function connectWS() {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
    ws = new WebSocket(`${protocol}://${location.host}/ws`);

    ws.onopen = () => {
        console.log('[WS] Connected');
        clearTimeout(wsReconnectTimer);
    };

    ws.onclose = () => {
        console.log('[WS] Disconnected — reconnecting in 2s');
        wsReconnectTimer = setTimeout(connectWS, 2000);
    };

    ws.onerror = (e) => console.error('[WS] Error', e);

    ws.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        handleMessage(msg);
    };
}

function send(data) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(data));
    }
}

// ── Message handler ──
function handleMessage(msg) {
    switch (msg.type) {
        case 'init':
            settings = msg.settings || {};
            channels = msg.channels || [];
            rules = msg.rules || [];
            populateSettings();
            renderChannelsTable();
            renderRulesList();
            renderVarList();
            break;

        case 'mqtt_status':
            updateMqttStatus(msg.connected);
            break;

        case 'engine_status':
            engineRunning = msg.running;
            updateEngineBtn();
            break;

        case 'gate_state':
            gateStates[msg.name] = msg.value;
            updateGateCard(msg.name, msg.value);
            break;

        case 'all_states':
            gateStates = msg.states || {};
            renderGateGrid();
            break;

        case 'pending_trigger':
            handlePendingTrigger(msg);
            break;

        case 'switch_fired':
            addLogItem(msg);
            removePendingTrigger(msg.rule);
            break;

        case 'ghold_result':
            onGholdResult(msg);
            handleGholdForModal(msg);
            break;

        case 'channels_updated':
            channels = msg.channels;
            renderChannelsTable();
            renderGateGrid();
            renderVarList();
            break;

        case 'rules_updated':
            rules = msg.rules;
            renderRulesList();
            renderVarList();
            break;

        case 'settings_saved':
            settings = msg.settings;
            showSaveStatus('Saved & reconnecting…');
            break;

        case 'switch_log':
            msg.log.forEach(item => addLogItem(item, false));
            break;
    }
}

// ── MQTT status ──
function updateMqttStatus(connected) {
    const dot = document.getElementById('mqtt-dot');
    dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
}

// ── Engine button ──
function updateEngineBtn() {
    const btn = document.getElementById('engine-btn');
    const icon = document.getElementById('engine-icon');
    const label = document.getElementById('engine-label');
    if (engineRunning) {
        btn.classList.add('running');
        icon.textContent = '■';
        label.textContent = 'RUNNING';
    } else {
        btn.classList.remove('running');
        icon.textContent = '▶';
        label.textContent = 'STOPPED';
    }
}

// ── Tab navigation ──
document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
        btn.classList.add('active');
        document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
    });
});

document.getElementById('engine-btn').addEventListener('click', () => {
    send({ type: 'engine_toggle' });
});

// ── Gate grid ──
function renderGateGrid() {
    const grid = document.getElementById('gate-grid');
    if (!channels.length) {
        grid.innerHTML = '<div class="empty-state">No DSP channels configured</div>';
        return;
    }
    grid.innerHTML = '';
    channels.forEach(ch => {
        const val = gateStates[ch.friendly_name] || 0;
        grid.appendChild(createGateCard(ch, val));
    });
}

function createGateCard(ch, val) {
    const card = document.createElement('div');
    card.className = 'gate-card' + (val ? ' on' : '');
    card.id = 'gate-card-' + ch.friendly_name;
    card.innerHTML = `
        <div class="gate-card-top">
            <span class="gate-name" title="${ch.friendly_name}">${ch.friendly_name}</span>
            <span class="gate-indicator"></span>
        </div>
        <div class="gate-value">${val}</div>
        <div class="gate-meta">DEV ${ch.device_id} · CH ${ch.channel}</div>
    `;
    return card;
}

function updateGateCard(name, val) {
    const card = document.getElementById('gate-card-' + name);
    if (!card) return;
    card.className = 'gate-card' + (val ? ' on' : '');
    card.querySelector('.gate-value').textContent = val;
}

// ── Pending triggers ──
function handlePendingTrigger(msg) {
    const list = document.getElementById('pending-list');

    if (msg.state === 'waiting') {
        pendingTriggers[msg.rule] = { ...msg, startTime: Date.now() };
        renderPendingList();
        // Start countdown animation
        animatePendingBar(msg.rule, msg.delay);
    } else {
        removePendingTrigger(msg.rule);
    }
}

function animatePendingBar(ruleName, delay) {
    const bar = document.getElementById('pbar-' + ruleName);
    if (!bar) return;
    bar.style.width = '100%';
    const start = Date.now();
    const tick = () => {
        if (!pendingTriggers[ruleName]) return;
        const elapsed = (Date.now() - start) / 1000;
        const pct = Math.max(0, 100 - (elapsed / delay * 100));
        const b = document.getElementById('pbar-' + ruleName);
        if (b) b.style.width = pct + '%';
        if (pct > 0) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
}

function removePendingTrigger(ruleName) {
    delete pendingTriggers[ruleName];
    renderPendingList();
}

function renderPendingList() {
    const list = document.getElementById('pending-list');
    const items = Object.values(pendingTriggers);
    if (!items.length) {
        list.innerHTML = '<div class="empty-state">No pending triggers</div>';
        return;
    }
    list.innerHTML = '';
    items.forEach(item => {
        const div = document.createElement('div');
        div.className = 'pending-item';
        div.innerHTML = `
            <div class="pending-rule">${item.rule}</div>
            <div class="pending-cam">CAM ${item.camera_input}</div>
            <div class="pending-bar-wrap"><div class="pending-bar" id="pbar-${item.rule}" style="width:100%"></div></div>
            <div class="pending-state">WAITING</div>
        `;
        list.appendChild(div);
        animatePendingBar(item.rule, item.delay);
    });
}

// ── Switch log ──
function addLogItem(item, animate = true) {
    const list = document.getElementById('log-list');
    const empty = list.querySelector('.empty-state');
    if (empty) empty.remove();

    const div = document.createElement('div');
    div.className = 'log-item';
    if (!animate) div.style.animation = 'none';
    div.innerHTML = `
        <span class="log-time">${item.timestamp}</span>
        <span class="log-rule">${item.rule}</span>
        <span class="log-cam">CAM ${item.camera_input}</span>
    `;
    list.insertBefore(div, list.firstChild);

    // Keep max 50 items
    while (list.children.length > 50) list.removeChild(list.lastChild);
}

function clearLog() {
    document.getElementById('log-list').innerHTML = '<div class="empty-state">No switches fired yet</div>';
}

// ── Channels table ──
function renderChannelsTable() {
    const tbody = document.getElementById('channels-tbody');
    if (!channels.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No channels configured</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    channels.forEach(ch => {
        const ghold = ch.ghold_time != null ? ch.ghold_time.toFixed(2) : '—';
        const total = ch.ghold_time != null ? (ch.ghold_time + ch.additional_hold).toFixed(2) : '—';
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${ch.friendly_name}</strong></td>
            <td><span class="tag-device">${ch.device_id}</span></td>
            <td><span class="tag-device">${ch.channel}</span></td>
            <td>
                <span class="ghold-val" id="ghold-${ch.id}">${ghold}s</span>
                <button class="btn-refresh" title="Refresh GHOLD" onclick="refreshGhold(${ch.id})">↻</button>
            </td>
            <td><span class="ghold-val">${ch.additional_hold.toFixed(2)}s</span></td>
            <td><span class="delay-total" id="total-${ch.id}">${total !== '—' ? total + 's' : '—'}</span></td>
            <td>
                <button class="btn-edit" onclick="openChannelModal(${ch.id})">Edit</button>
                <button class="btn-danger" onclick="deleteChannel(${ch.id})">Delete</button>
            </td>
        `;
        tbody.appendChild(tr);
    });
}

function refreshGhold(id) {
    send({ type: 'fetch_ghold', id });
    const el = document.getElementById('ghold-' + id);
    if (el) el.textContent = '…';
}

function onGholdResult(msg) {
    // Update in channels array
    const ch = channels.find(c => c.id === msg.channel_id);
    if (ch) {
        ch.ghold_time = msg.ghold_time;
        const el = document.getElementById('ghold-' + ch.id);
        if (el) el.textContent = msg.ghold_time.toFixed(2) + 's';
        const tot = document.getElementById('total-' + ch.id);
        if (tot) tot.textContent = (msg.ghold_time + ch.additional_hold).toFixed(2) + 's';
        // Update modal if open
        updateModalGhold(msg.ghold_time);
    }
}

// ── Channel modal ──
let channelGholdVal = null;

function openChannelModal(id = null) {
    channelGholdVal = null;
    document.getElementById('ch-id').value = '';
    document.getElementById('ch-device').value = '';
    document.getElementById('ch-channel').value = '';
    document.getElementById('ch-name').value = '';
    document.getElementById('ch-hold').value = '0.5';
    document.getElementById('ch-ghold-val').textContent = '—';
    document.getElementById('ch-total-delay').textContent = '—';
    document.getElementById('ch-ghold-status').textContent = '';
    document.getElementById('channel-modal-title').textContent = id ? 'Edit DSP Channel' : 'Add DSP Channel';

    if (id) {
        const ch = channels.find(c => c.id === id);
        if (ch) {
            document.getElementById('ch-id').value = ch.id;
            document.getElementById('ch-device').value = ch.device_id;
            document.getElementById('ch-channel').value = ch.channel;
            document.getElementById('ch-name').value = ch.friendly_name;
            document.getElementById('ch-hold').value = ch.additional_hold;
            channelGholdVal = ch.ghold_time;
            if (ch.ghold_time != null) {
                document.getElementById('ch-ghold-val').textContent = ch.ghold_time.toFixed(2);
                updateTotalDelay();
            }
        }
    }

    document.getElementById('channel-modal').classList.add('open');
}

function closeChannelModal() {
    document.getElementById('channel-modal').classList.remove('open');
}

let gholdFetchTimeout = null;

function onChannelFieldChange() {
    const dev = document.getElementById('ch-device').value.trim();
    const ch = document.getElementById('ch-channel').value.trim();
    if (dev && ch) {
        document.getElementById('ch-ghold-status').textContent = 'fetching…';
        clearTimeout(gholdFetchTimeout);
        gholdFetchTimeout = setTimeout(() => {
            // Request GHOLD directly via a temporary pending entry
            const topic = `clearone/${dev}/GHOLD/${ch}/set`;
            // We store a special pending key
            const key = `${dev}/${ch}`;
            // Use a sentinel id -1 for modal fetches
            pendingGhold[key] = true;
            send({ type: 'fetch_ghold_temp', device_id: dev, channel: ch });
        }, 600);
    }
}

function updateModalGhold(val) {
    channelGholdVal = val;
    document.getElementById('ch-ghold-val').textContent = val.toFixed(2);
    document.getElementById('ch-ghold-status').textContent = '✓';
    updateTotalDelay();
}

function updateTotalDelay() {
    const add = parseFloat(document.getElementById('ch-hold').value) || 0;
    if (channelGholdVal != null) {
        document.getElementById('ch-total-delay').textContent = (channelGholdVal + add).toFixed(2);
    }
}

document.getElementById('ch-hold').addEventListener('input', updateTotalDelay);

function saveChannel() {
    const id = document.getElementById('ch-id').value;
    const data = {
        device_id: document.getElementById('ch-device').value.trim(),
        channel: parseInt(document.getElementById('ch-channel').value),
        friendly_name: document.getElementById('ch-name').value.trim(),
        additional_hold: parseFloat(document.getElementById('ch-hold').value) || 0.5,
    };
    if (id) data.id = parseInt(id);
    if (!data.device_id || !data.channel || !data.friendly_name) {
        alert('Please fill in Device ID, Channel and Variable Name');
        return;
    }
    send({ type: 'save_channel', channel: data });
    closeChannelModal();
}

function deleteChannel(id) {
    if (!confirm('Delete this channel?')) return;
    send({ type: 'delete_channel', id });
}

// ── Rules ──
function renderRulesList() {
    const list = document.getElementById('rules-list');
    if (!rules.length) {
        list.innerHTML = '<div class="empty-state">No rules configured</div>';
        return;
    }
    list.innerHTML = '';
    rules.forEach(rule => {
        const card = document.createElement('div');
        card.className = 'rule-card' + (rule.enabled ? '' : ' disabled');
        card.innerHTML = `
            <input type="checkbox" class="rule-toggle" ${rule.enabled ? 'checked' : ''} 
                onchange="toggleRule(${rule.id}, this.checked)">
            <div class="rule-body">
                <div class="rule-top">
                    <span class="rule-name">${rule.name}</span>
                    <span class="rule-cam-badge">CAM ${rule.camera_input}</span>
                    <span class="rule-trigger-on-badge ${rule.trigger_on === 'rising' ? 'rising' : 'any'}">${rule.trigger_on === 'rising' ? '↑ RISING' : '↕ ANY'}</span>
                    <span class="rule-priority">Priority ${rule.priority}</span>
                </div>
                <div class="rule-exprs">
                    <div class="rule-expr-row">
                        <span class="expr-label">Trigger</span>
                        <span class="expr-value">${rule.trigger_expression || '—'}</span>
                    </div>
                    ${rule.condition_expression ? `
                    <div class="rule-expr-row">
                        <span class="expr-label">Condition</span>
                        <span class="expr-value">${rule.condition_expression}</span>
                    </div>` : ''}
                </div>
            </div>
            <div class="rule-actions">
                <button class="btn-edit" onclick="openRuleModal(${rule.id})">Edit</button>
                <button class="btn-danger" onclick="deleteRule(${rule.id})">Delete</button>
            </div>
        `;
        list.appendChild(card);
    });
}

function toggleRule(id, enabled) {
    const rule = rules.find(r => r.id === id);
    if (rule) {
        rule.enabled = enabled ? 1 : 0;
        send({ type: 'save_rule', rule: { ...rule } });
    }
}

function openRuleModal(id = null) {
    document.getElementById('rule-id').value = '';
    document.getElementById('rule-name').value = '';
    document.getElementById('rule-camera').value = '';
    document.getElementById('rule-priority').value = '0';
    document.getElementById('rule-trigger-on').value = 'rising';
    document.getElementById('rule-trigger').value = '';
    document.getElementById('rule-condition').value = '';
    document.getElementById('rule-modal-title').textContent = id ? 'Edit Logic Rule' : 'Add Logic Rule';

    if (id) {
        const rule = rules.find(r => r.id === id);
        if (rule) {
            document.getElementById('rule-id').value = rule.id;
            document.getElementById('rule-name').value = rule.name;
            document.getElementById('rule-camera').value = rule.camera_input;
            document.getElementById('rule-priority').value = rule.priority;
            document.getElementById('rule-trigger-on').value = rule.trigger_on || 'rising';
            document.getElementById('rule-trigger').value = rule.trigger_expression;
            document.getElementById('rule-condition').value = rule.condition_expression || '';
        }
    }
    document.getElementById('rule-modal').classList.add('open');
}

function closeRuleModal() {
    document.getElementById('rule-modal').classList.remove('open');
}

function saveRule() {
    const id = document.getElementById('rule-id').value;
    const data = {
        name: document.getElementById('rule-name').value.trim(),
        camera_input: parseInt(document.getElementById('rule-camera').value),
        priority: parseInt(document.getElementById('rule-priority').value) || 0,
        trigger_on: document.getElementById('rule-trigger-on').value,
        trigger_expression: document.getElementById('rule-trigger').value.trim(),
        condition_expression: document.getElementById('rule-condition').value.trim(),
        enabled: 1,
    };
    if (id) data.id = parseInt(id);
    if (!data.name || !data.camera_input || !data.trigger_expression) {
        alert('Please fill in Name, Camera Input and Trigger Expression');
        return;
    }
    send({ type: 'save_rule', rule: data });
    closeRuleModal();
}

function deleteRule(id) {
    if (!confirm('Delete this rule?')) return;
    send({ type: 'delete_rule', id });
}

// ── Variable list ──
function renderVarList() {
    const container = document.getElementById('var-list');
    if (!channels.length) {
        container.innerHTML = '—';
        return;
    }
    container.innerHTML = '';
    channels.forEach(ch => {
        const chip = document.createElement('span');
        chip.className = 'var-chip';
        chip.textContent = ch.friendly_name;
        container.appendChild(chip);
    });
}

// ── Settings ──
function populateSettings() {
    document.getElementById('s-mqtt-host').value = settings.mqtt_host || '';
    document.getElementById('s-mqtt-port').value = settings.mqtt_port || '1883';
    document.getElementById('s-mqtt-user').value = settings.mqtt_user || '';
    document.getElementById('s-mqtt-pass').value = settings.mqtt_pass || '';
    document.getElementById('s-camera-topic').value = settings.camera_topic || 'camera/switch';
}

function saveSettings() {
    const data = {
        mqtt_host: document.getElementById('s-mqtt-host').value.trim(),
        mqtt_port: document.getElementById('s-mqtt-port').value.trim(),
        mqtt_user: document.getElementById('s-mqtt-user').value.trim(),
        mqtt_pass: document.getElementById('s-mqtt-pass').value.trim(),
        camera_topic: document.getElementById('s-camera-topic').value.trim(),
    };
    send({ type: 'save_settings', settings: data });
}

function showSaveStatus(msg) {
    const el = document.getElementById('save-status');
    el.textContent = msg;
    setTimeout(() => el.textContent = '', 3000);
}

// ── Close modals on overlay click ──
document.getElementById('channel-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChannelModal();
});
document.getElementById('rule-modal').addEventListener('click', function(e) {
    if (e.target === this) closeRuleModal();
});

// ── Handle GHOLD result for modal (channel_id == -1 means temp/modal fetch) ──
function handleGholdForModal(msg) {
    const modal = document.getElementById('channel-modal');
    if (!modal.classList.contains('open')) return;
    const dev = document.getElementById('ch-device').value.trim();
    const ch = document.getElementById('ch-channel').value.trim();
    if (msg.pending_key === `${dev}/${ch}`) {
        updateModalGhold(msg.ghold_time);
        document.getElementById('ch-ghold-status').textContent = '✓';
    }
}

// ── Init ──
connectWS();

// Request log on load
setTimeout(() => send({ type: 'get_log' }), 500);
