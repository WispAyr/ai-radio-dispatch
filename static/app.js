/**
 * Dispatch Console — Frontend Application
 * Handles WebSocket communication, multi-channel UI, search, bookmarks, callsigns, and alerts.
 */

// ===== State =====
let ws = null;
let channels = {};      // channel_id -> channel data
let channelLogs = {};   // channel_id -> [entries]
let activeTab = 'all';
let knownSpeakers = new Set();
let callsigns = [];
let alerts = [];
let devices = [];
let bookmarks = [];
let searchTimeout = null;
let isSearching = false;
let stats = {};

// ===== WebSocket =====

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('ws-status').textContent = 'Connected';
        document.getElementById('ws-status').style.color = 'var(--accent)';
    };

    ws.onclose = () => {
        document.getElementById('ws-status').textContent = 'Reconnecting...';
        document.getElementById('ws-status').style.color = 'var(--warning)';
        setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();
    ws.onmessage = (event) => handleMessage(JSON.parse(event.data));
}

function handleMessage(data) {
    switch (data.type) {
        case 'init':
            handleInit(data);
            break;
        case 'state':
            updateGlobalState(data.state);
            break;
        case 'channel_state':
            updateChannelState(data.channel_id, data.state);
            break;
        case 'log':
            handleLogEntry(data.entry);
            break;
        case 'level':
            updateLevel(data.channel_id, data.level);
            break;
        case 'mute':
            updateMuteState(data.muted);
            break;
        case 'clear':
            clearTranscript();
            break;
        case 'channel_clear':
            clearChannelTranscript(data.channel_id);
            break;
        case 'channels':
            updateChannels(data.channels);
            break;
        case 'channel_added':
            addChannelToUI(data.channel);
            break;
        case 'channel_removed':
            removeChannelFromUI(data.channel_id);
            break;
        case 'channel_updated':
            updateChannelInUI(data.channel);
            break;
        case 'settings':
            updateSettingsUI(data.settings);
            break;
        case 'callsign_detected':
            handleCallsignDetected(data.callsigns, data.channel_id);
            break;
        case 'alert':
            handleAlert(data);
            break;
        case 'alerts_updated':
            alerts = data.alerts;
            renderAlerts();
            break;
    }
}

// ===== Init =====

function handleInit(data) {
    updateGlobalState(data.state);
    updateMuteState(data.muted);

    // Channels
    channels = {};
    channelLogs = {};
    (data.channels || []).forEach(ch => {
        channels[ch.id] = ch;
        channelLogs[ch.id] = [];
    });

    // Logs
    if (data.channel_logs) {
        for (const [chId, logs] of Object.entries(data.channel_logs)) {
            channelLogs[chId] = logs || [];
            logs.forEach(e => {
                if (e.speaker) knownSpeakers.add(e.speaker);
            });
        }
    }

    // Stats
    if (data.stats) {
        stats = data.stats;
        updateStats();
    }

    // Callsigns
    if (data.callsigns) {
        callsigns = data.callsigns;
        renderCallsigns();
    }

    // Alerts
    if (data.alerts) {
        alerts = data.alerts;
        renderAlerts();
    }

    // Devices
    if (data.devices) {
        updateDeviceLists(data.devices);
    }

    renderChannelTabs();
    renderChannelManagement();
    renderChannelLevels();
    renderSpeakerFilter();
    renderTranscript();
}

// ===== State Updates =====

function updateGlobalState(state) {
    const badge = document.getElementById('status-badge');
    const text = document.getElementById('status-text');
    text.textContent = state.toUpperCase();
    badge.className = 'status-badge status-' + state;
}

function updateChannelState(channelId, state) {
    if (channels[channelId]) {
        channels[channelId].state = state;
        channels[channelId].is_active = state !== 'idle';
    }
    const tab = document.querySelector(`.channel-tab[data-channel="${channelId}"]`);
    if (tab) {
        tab.classList.toggle('active-channel', state !== 'idle');
        if (state === 'transcribing') {
            tab.classList.add('transcribing');
        } else {
            tab.classList.remove('transcribing');
        }
    }
    renderChannelManagement();
}

function updateMuteState(muted) {
    const btn = document.getElementById('btn-mute');
    btn.textContent = muted ? '🔊 Unmute' : '🔇 Mute';
    btn.classList.toggle('btn-active', muted);
}

function updateLevel(channelId, level) {
    const fill = document.getElementById(`level-fill-${channelId}`);
    if (fill) {
        const pct = Math.min(level * 100, 100);
        fill.style.width = pct + '%';
        fill.className = 'level-fill' + (pct > 70 ? ' level-hot' : pct > 40 ? ' level-warm' : '');
    }
}

// ===== Log Entry Handling =====

function handleLogEntry(entry) {
    const chId = entry.channel_id;
    if (chId && channelLogs[chId]) {
        channelLogs[chId].push(entry);
    }

    if (entry.speaker) knownSpeakers.add(entry.speaker);

    // Update stats
    stats.total_transcripts = (stats.total_transcripts || 0) + 1;
    updateStats();

    // Flash tab if not active
    if (chId && activeTab !== 'all' && activeTab !== chId) {
        const tab = document.querySelector(`.channel-tab[data-channel="${chId}"]`);
        if (tab) {
            tab.classList.add('tab-flash');
            setTimeout(() => tab.classList.remove('tab-flash'), 3000);
        }
    }

    if (!isSearching) {
        if (activeTab === 'all' || activeTab === chId) {
            appendTranscriptEntry(entry);
        }
    }
}

function appendTranscriptEntry(entry) {
    const container = document.getElementById('transcript');
    const emptyState = container.querySelector('.empty-state');
    if (emptyState) emptyState.remove();

    const el = createTranscriptElement(entry);
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
}

function createTranscriptElement(entry) {
    const div = document.createElement('div');
    div.className = 'transcript-entry';
    if (entry.id) div.dataset.id = entry.id;

    const ch = entry.channel_id ? channels[entry.channel_id] : null;
    const channelColor = ch ? ch.color : '#666';
    const channelName = ch ? ch.name : '';

    let callsignBadges = '';
    if (entry.callsigns && entry.callsigns.length > 0) {
        callsignBadges = entry.callsigns.map(cs =>
            `<span class="callsign-badge" title="Detected callsign">${cs}</span>`
        ).join('');
    }

    let channelBadge = '';
    if (activeTab === 'all' && ch) {
        channelBadge = `<span class="channel-badge" style="background:${channelColor}">${channelName}</span>`;
    }

    const flagged = entry.flagged ? ' flagged' : '';
    const flagIcon = entry.flagged ? '★' : '☆';

    div.innerHTML = `
        <div class="entry-header">
            <span class="entry-time">${entry.time_str || ''}</span>
            ${channelBadge}
            <span class="entry-speaker" style="color:${getSpeakerColor(entry.speaker)}">${entry.speaker}</span>
            ${callsignBadges}
            <span class="entry-duration">${entry.duration || 0}s</span>
            <button class="entry-flag${flagged}" onclick="toggleFlag(this, ${entry.id})" title="Bookmark">${flagIcon}</button>
        </div>
        <div class="entry-text">${highlightCallsigns(entry.text, entry.callsigns)}</div>
        ${entry.waveform ? `<div class="entry-waveform" onclick="playRecording('${entry.audio_file}')">${renderWaveform(entry.waveform, channelColor)}</div>` : ''}
    `;

    return div;
}

function highlightCallsigns(text, callsignList) {
    if (!callsignList || callsignList.length === 0) return text;
    let result = text;
    for (const cs of callsignList) {
        const regex = new RegExp(`(${cs.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
        result = result.replace(regex, '<mark class="callsign-highlight">$1</mark>');
    }
    return result;
}

function renderWaveform(peaks, color) {
    if (!peaks || peaks.length === 0) return '';
    const bars = peaks.map(p => {
        const h = Math.max(2, p * 24);
        return `<div class="waveform-bar" style="height:${h}px;background:${color}"></div>`;
    }).join('');
    return `<div class="waveform-bars">${bars}</div>`;
}

function getSpeakerColor(speaker) {
    const colors = ['#3b82f6', '#ef4444', '#f59e0b', '#10b981', '#8b5cf6', '#ec4899', '#06b6d4'];
    if (!speaker) return colors[0];
    let hash = 0;
    for (let i = 0; i < speaker.length; i++) hash = speaker.charCodeAt(i) + ((hash << 5) - hash);
    return colors[Math.abs(hash) % colors.length];
}

function playRecording(filename) {
    if (!filename) return;
    const audio = new Audio(`/recordings/${filename}`);
    audio.play();
}

// ===== Transcript Rendering =====

function renderTranscript() {
    const container = document.getElementById('transcript');
    container.innerHTML = '';

    let entries = [];
    if (activeTab === 'all') {
        for (const logs of Object.values(channelLogs)) {
            entries = entries.concat(logs);
        }
        entries.sort((a, b) => a.timestamp - b.timestamp);
    } else if (channelLogs[activeTab]) {
        entries = channelLogs[activeTab];
    }

    if (entries.length === 0) {
        container.innerHTML = '<div class="empty-state"><div class="empty-icon">📡</div><p>Waiting for transmissions...</p></div>';
        return;
    }

    entries.forEach(entry => {
        container.appendChild(createTranscriptElement(entry));
    });
    container.scrollTop = container.scrollHeight;
}

function clearTranscript() {
    // Clear all channel logs and re-render
    for (const chId of Object.keys(channelLogs)) {
        channelLogs[chId] = [];
    }
    knownSpeakers.clear();
    renderTranscript();
    renderSpeakerFilter();
}

function clearChannelTranscript(channelId) {
    if (channelLogs[channelId]) {
        channelLogs[channelId] = [];
    }
    if (activeTab === 'all' || activeTab === channelId) {
        renderTranscript();
    }
}

// ===== Channel Tabs =====

function renderChannelTabs() {
    const tabsContainer = document.getElementById('channel-tabs');
    tabsContainer.innerHTML = '<button class="channel-tab active" data-channel="all" onclick="switchTab(\'all\')">ALL</button>';

    Object.values(channels).forEach(ch => {
        const btn = document.createElement('button');
        btn.className = 'channel-tab' + (activeTab === ch.id ? ' active' : '');
        btn.dataset.channel = ch.id;
        btn.style.borderColor = ch.color;
        btn.onclick = () => switchTab(ch.id);
        btn.innerHTML = `<span class="tab-dot" style="background:${ch.color}"></span>${ch.name}`;
        if (ch.is_active) btn.classList.add('active-channel');
        tabsContainer.appendChild(btn);
    });

    // Update filter dropdown
    const filterCh = document.getElementById('filter-channel');
    filterCh.innerHTML = '<option value="">All Channels</option>';
    Object.values(channels).forEach(ch => {
        filterCh.innerHTML += `<option value="${ch.id}">${ch.name}</option>`;
    });
}

function switchTab(tabId) {
    activeTab = tabId;
    document.querySelectorAll('.channel-tab').forEach(t => {
        t.classList.toggle('active', t.dataset.channel === tabId);
    });
    if (!isSearching) renderTranscript();
}

// ===== Channel Management =====

function renderChannelManagement() {
    const container = document.getElementById('channel-management');
    container.innerHTML = '';

    Object.values(channels).forEach(ch => {
        const div = document.createElement('div');
        div.className = 'channel-mgmt-row';

        // Build device options
        let deviceOptions = '<option value="">No device</option>';
        devices.forEach(d => {
            const sel = (ch.device_id !== null && ch.device_id !== undefined && ch.device_id === d.id) ? ' selected' : '';
            const name = d.name.length > 28 ? d.name.substring(0, 28) + '…' : d.name;
            deviceOptions += `<option value="${d.id}"${sel}>${name}</option>`;
        });

        div.innerHTML = `
            <span class="channel-mgmt-dot" style="background:${ch.color}"></span>
            <div class="channel-mgmt-info">
                <span class="channel-mgmt-name">${ch.name}</span>
                <select class="channel-device-select" onchange="setChannelDevice('${ch.id}', this.value)">${deviceOptions}</select>
            </div>
            <span class="channel-mgmt-state ${ch.is_active ? 'active' : ''}">${ch.state}</span>
            <div class="channel-mgmt-actions">
                <button class="btn-icon" onclick="toggleChannel('${ch.id}')" title="${ch.is_active ? 'Stop' : 'Start'}">${ch.is_active ? '⏹' : '▶'}</button>
                <button class="btn-icon" onclick="removeChannel('${ch.id}')" title="Remove">✕</button>
            </div>
        `;
        container.appendChild(div);
    });
}

function renderChannelLevels() {
    const container = document.getElementById('channel-levels');
    container.innerHTML = '';
    Object.values(channels).forEach(ch => {
        container.innerHTML += `
            <div class="level-row">
                <span class="level-label" style="color:${ch.color}">${ch.name}</span>
                <div class="level-bar"><div class="level-fill" id="level-fill-${ch.id}"></div></div>
            </div>
        `;
    });
}

// ===== Search =====

document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('search-input');
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(performSearch, 300);
    });

    document.getElementById('search-clear').addEventListener('click', clearSearch);
    document.getElementById('filter-channel').addEventListener('change', performSearch);
    document.getElementById('filter-speaker').addEventListener('change', performSearch);
    document.getElementById('filter-flagged').addEventListener('change', performSearch);
});

async function performSearch() {
    const query = document.getElementById('search-input').value.trim();
    const channel = document.getElementById('filter-channel').value;
    const speaker = document.getElementById('filter-speaker').value;
    const flagged = document.getElementById('filter-flagged').checked;

    if (!query && !channel && !speaker && !flagged) {
        clearSearch();
        return;
    }

    isSearching = true;
    const params = new URLSearchParams();
    if (query) params.set('q', query);
    if (channel) params.set('channel', channel);
    if (speaker) params.set('speaker', speaker);
    if (flagged) params.set('flagged', '1');

    try {
        const res = await fetch(`/api/search?${params}`);
        const data = await res.json();
        const container = document.getElementById('transcript');
        container.innerHTML = '';

        if (data.results.length === 0) {
            container.innerHTML = '<div class="empty-state"><div class="empty-icon">🔍</div><p>No results found</p></div>';
            return;
        }

        // Results come in reverse chronological; show oldest first
        data.results.reverse().forEach(entry => {
            entry.time_str = entry.time_str || formatTime(entry.timestamp);
            container.appendChild(createTranscriptElement(entry));
        });
        container.scrollTop = container.scrollHeight;
    } catch (e) {
        console.error('Search error:', e);
    }
}

function clearSearch() {
    document.getElementById('search-input').value = '';
    document.getElementById('filter-channel').value = '';
    document.getElementById('filter-speaker').value = '';
    document.getElementById('filter-flagged').checked = false;
    isSearching = false;
    renderTranscript();
}

function renderSpeakerFilter() {
    const select = document.getElementById('filter-speaker');
    select.innerHTML = '<option value="">All Speakers</option>';
    knownSpeakers.forEach(s => {
        select.innerHTML += `<option value="${s}">${s}</option>`;
    });
}

function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ===== Bookmarks & Flagging =====

async function toggleFlag(btn, transcriptId) {
    if (!transcriptId) return;
    const isFlagged = btn.classList.contains('flagged');

    try {
        if (isFlagged) {
            // Unflag
            await fetch(`/api/transcripts/${transcriptId}/flag`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ flagged: false })
            });
            btn.classList.remove('flagged');
            btn.textContent = '☆';
        } else {
            // Flag + bookmark
            await fetch(`/api/transcripts/${transcriptId}/flag`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ flagged: true })
            });
            await fetch('/api/bookmarks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ transcript_id: transcriptId })
            });
            btn.classList.add('flagged');
            btn.textContent = '★';
            stats.total_bookmarks = (stats.total_bookmarks || 0) + 1;
            updateStats();
        }
        loadBookmarks();
    } catch (e) {
        console.error('Flag error:', e);
    }
}

async function loadBookmarks() {
    try {
        const res = await fetch('/api/bookmarks');
        const data = await res.json();
        bookmarks = data.bookmarks || [];
        renderBookmarks();
    } catch (e) {
        console.error('Bookmarks error:', e);
    }
}

function renderBookmarks() {
    const container = document.getElementById('bookmark-list');
    document.getElementById('bookmark-count').textContent = bookmarks.length;

    if (bookmarks.length === 0) {
        container.innerHTML = '<div class="empty-hint">No bookmarks yet — click ★ on any entry</div>';
        return;
    }

    container.innerHTML = bookmarks.map(bm => {
        const ts = formatTime(bm.transcript_ts || bm.created_at);
        return `
            <div class="bookmark-item">
                <div class="bookmark-header">
                    <span class="bookmark-time">${ts}</span>
                    <span class="bookmark-speaker">${bm.speaker || ''}</span>
                    <button class="btn-icon" onclick="removeBookmark(${bm.id})" title="Remove">✕</button>
                </div>
                <div class="bookmark-text">${(bm.text || '').substring(0, 80)}${(bm.text || '').length > 80 ? '...' : ''}</div>
            </div>
        `;
    }).join('');
}

async function removeBookmark(id) {
    try {
        await fetch(`/api/bookmarks/${id}`, { method: 'DELETE' });
        loadBookmarks();
    } catch (e) {
        console.error('Remove bookmark error:', e);
    }
}

// ===== Callsigns =====

function handleCallsignDetected(detectedList, channelId) {
    detectedList.forEach(cs => {
        const existing = callsigns.find(c => c.callsign === cs.callsign);
        if (existing) {
            existing.count = (existing.count || 0) + 1;
            existing.last_seen = Date.now() / 1000;
        } else {
            callsigns.push({
                callsign: cs.callsign,
                domain: cs.domain,
                count: 1,
                last_seen: Date.now() / 1000,
                last_channel: channelId,
            });
        }
    });
    stats.total_callsigns = callsigns.length;
    updateStats();
    renderCallsigns();
}

function renderCallsigns() {
    const container = document.getElementById('callsign-list');
    document.getElementById('callsign-count').textContent = callsigns.length;

    if (callsigns.length === 0) {
        container.innerHTML = '<div class="empty-hint">No callsigns detected yet</div>';
        return;
    }

    // Sort by last seen
    const sorted = [...callsigns].sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0));

    container.innerHTML = sorted.slice(0, 50).map(cs => {
        const ago = timeSince(cs.last_seen);
        const domainIcon = getDomainIcon(cs.domain);
        return `
            <div class="callsign-item">
                <span class="callsign-domain-icon" title="${cs.domain || 'general'}">${domainIcon}</span>
                <span class="callsign-name">${cs.callsign}</span>
                <span class="callsign-count">${cs.count || 1}×</span>
                <span class="callsign-ago">${ago}</span>
            </div>
        `;
    }).join('');
}

function getDomainIcon(domain) {
    const icons = {
        amateur: '📻', aviation: '✈️', maritime: '⚓', emergency: '🚨',
        phonetic: '🔤', general: '📡', scanner: '📡'
    };
    return icons[domain] || '📡';
}

function timeSince(ts) {
    if (!ts) return '';
    const seconds = Math.floor(Date.now() / 1000 - ts);
    if (seconds < 60) return 'now';
    if (seconds < 3600) return Math.floor(seconds / 60) + 'm';
    if (seconds < 86400) return Math.floor(seconds / 3600) + 'h';
    return Math.floor(seconds / 86400) + 'd';
}

// ===== Alerts =====

async function addAlert() {
    const pattern = document.getElementById('alert-pattern').value.trim();
    if (!pattern) return;
    const isRegex = document.getElementById('alert-regex').checked;

    try {
        await fetch('/api/alerts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: pattern, pattern, is_regex: isRegex })
        });
        document.getElementById('alert-pattern').value = '';
        document.getElementById('alert-regex').checked = false;
    } catch (e) {
        console.error('Add alert error:', e);
    }
}

async function removeAlert(id) {
    try {
        await fetch(`/api/alerts/${id}`, { method: 'DELETE' });
    } catch (e) {
        console.error('Remove alert error:', e);
    }
}

function renderAlerts() {
    const container = document.getElementById('alert-list');
    const keywordAlerts = alerts.filter(a => a.type === 'keyword');

    if (keywordAlerts.length === 0) {
        container.innerHTML = '<div class="empty-hint">No alerts configured</div>';
        return;
    }

    container.innerHTML = keywordAlerts.map(a => `
        <div class="alert-item">
            <span class="alert-pattern">${a.is_regex ? '/' + a.pattern + '/' : a.pattern}</span>
            <button class="btn-icon" onclick="removeAlert(${a.id})" title="Remove">✕</button>
        </div>
    `).join('');
}

function handleAlert(data) {
    showToast(data);
    // Play alert sound
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain).connect(ctx.destination);
        osc.frequency.value = data.alert_type === 'volume_spike' ? 880 : 660;
        gain.gain.value = 0.15;
        osc.start();
        osc.stop(ctx.currentTime + 0.3);
    } catch (e) { }
}

function showToast(alertData) {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast toast-' + alertData.alert_type;

    let message = '';
    if (alertData.alert_type === 'keyword') {
        message = `🔔 Alert: "${alertData.alert_name}" matched on ${alertData.channel_id || 'unknown'}`;
    } else if (alertData.alert_type === 'silence') {
        message = `🔕 Silence alert: ${alertData.silence_duration}s on ${alertData.channel_id}`;
    } else if (alertData.alert_type === 'volume_spike') {
        message = `📢 Volume spike on ${alertData.channel_id}`;
    }

    toast.innerHTML = `<span>${message}</span><button onclick="this.parentElement.remove()">✕</button>`;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 8000);
}

// ===== Stats =====

function updateStats() {
    document.getElementById('stat-transcripts').textContent = stats.total_transcripts || 0;
    document.getElementById('stat-callsigns').textContent = stats.total_callsigns || callsigns.length;
    document.getElementById('stat-bookmarks').textContent = stats.total_bookmarks || 0;
}

// ===== Export =====

function exportData(format) {
    const channel = activeTab !== 'all' ? `&channel=${activeTab}` : '';
    window.open(`/api/export?format=${format}${channel}`, '_blank');
}

// ===== Controls =====

async function startAll() {
    await fetch('/api/start', { method: 'POST' });
}

async function stopAll() {
    await fetch('/api/stop', { method: 'POST' });
}

async function toggleMute() {
    await fetch('/api/mute', { method: 'POST' });
}

async function clearAll() {
    if (!confirm('Clear all transcripts?')) return;
    await fetch('/api/clear', { method: 'POST' });
}

async function toggleChannel(channelId) {
    const ch = channels[channelId];
    if (!ch) return;
    const endpoint = ch.is_active ? 'stop' : 'start';
    await fetch(`/api/channels/${channelId}/${endpoint}`, { method: 'POST' });
}

async function addChannel() {
    const name = prompt('Channel name:', `Channel ${Object.keys(channels).length + 1}`);
    if (!name) return;
    await fetch('/api/channels', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
}

async function removeChannel(channelId) {
    if (!confirm('Remove this channel?')) return;
    await fetch(`/api/channels/${channelId}`, { method: 'DELETE' });
}

function addChannelToUI(ch) {
    channels[ch.id] = ch;
    channelLogs[ch.id] = [];
    renderChannelTabs();
    renderChannelManagement();
    renderChannelLevels();
}

function removeChannelFromUI(channelId) {
    delete channels[channelId];
    delete channelLogs[channelId];
    if (activeTab === channelId) switchTab('all');
    renderChannelTabs();
    renderChannelManagement();
    renderChannelLevels();
}

function updateChannelInUI(ch) {
    channels[ch.id] = ch;
    renderChannelTabs();
    renderChannelManagement();
}

function updateChannels(channelList) {
    channels = {};
    channelList.forEach(ch => channels[ch.id] = ch);
    renderChannelTabs();
    renderChannelManagement();
    renderChannelLevels();
}

function updateDeviceLists(deviceList) {
    devices = deviceList || [];
    renderChannelManagement();
}

async function setChannelDevice(channelId, deviceValue) {
    const deviceId = deviceValue === '' ? null : parseInt(deviceValue);
    // Update local state immediately so re-renders keep the selection
    if (channels[channelId]) {
        channels[channelId].device_id = deviceId;
    }
    try {
        await fetch(`/api/channels/${channelId}/device`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: deviceId })
        });
    } catch (e) {
        console.error('Set device error:', e);
    }
}

// ===== Settings =====

function updateSettingsUI(settings) {
    if (settings.model) document.getElementById('setting-model').value = settings.model;
    if (settings.beam_size) {
        document.getElementById('setting-beam').value = settings.beam_size;
        document.getElementById('setting-beam').nextElementSibling.textContent = settings.beam_size;
    }
    if (settings.vad_threshold) {
        document.getElementById('setting-vad').value = settings.vad_threshold;
        document.getElementById('setting-vad').nextElementSibling.textContent = settings.vad_threshold;
    }
    if (settings.vad_silence) {
        document.getElementById('setting-silence').value = settings.vad_silence;
        document.getElementById('setting-silence').nextElementSibling.textContent = settings.vad_silence + 's';
    }
}

async function applySettings() {
    const settings = {
        model: document.getElementById('setting-model').value,
        beam_size: parseInt(document.getElementById('setting-beam').value),
        vad_threshold: parseFloat(document.getElementById('setting-vad').value),
        vad_silence: parseFloat(document.getElementById('setting-silence').value),
    };
    await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settings)
    });
}

// ===== Card Toggle =====

function toggleCard(header) {
    const body = header.nextElementSibling;
    const icon = header.querySelector('.toggle-icon');
    body.classList.toggle('collapsed');
    icon.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
}

// ===== Clock =====

function updateClock() {
    const now = new Date();
    document.getElementById('clock').textContent = now.toLocaleTimeString('en-GB');
}
setInterval(updateClock, 1000);
updateClock();

// ===== Init =====

connect();
setInterval(() => loadBookmarks(), 30000); // Refresh bookmarks periodically
