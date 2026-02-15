/**
 * Dispatch Console — WebSocket client & UI controller
 */

let ws = null;
let reconnectTimer = null;
let currentAudio = null;
let currentPlayingEntry = null;

// Speaker colors
const SPEAKER_COLORS = [
    '#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6',
    '#ec4899', '#14b8a6', '#f97316', '#06b6d4', '#a855f7',
];
const speakerColorMap = {};
let speakerCount = 0;

function getSpeakerColor(speaker) {
    if (speaker === 'dispatch') return '#10b981';
    if (!speakerColorMap[speaker]) {
        speakerColorMap[speaker] = SPEAKER_COLORS[speakerCount % SPEAKER_COLORS.length];
        speakerCount++;
        updateSpeakersLegend();
    }
    return speakerColorMap[speaker];
}

function updateSpeakersLegend() {
    const legend = document.getElementById('speakersLegend');
    const card = document.getElementById('speakersCard');
    if (Object.keys(speakerColorMap).length === 0) {
        card.style.display = 'none';
        return;
    }
    card.style.display = 'block';
    legend.innerHTML = Object.entries(speakerColorMap).map(([name, color]) => `
        <div class="speaker-item">
            <div class="speaker-dot" style="background:${color};box-shadow:0 0 6px ${color}40"></div>
            <span>${name}</span>
        </div>
    `).join('');
}

// ===== Waveform Drawing =====

function drawWaveform(canvas, peaks, color) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();

    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const w = rect.width;
    const h = rect.height;
    const barCount = peaks.length;
    const barWidth = Math.max(1, (w / barCount) - 1);
    const gap = 1;
    const centerY = h / 2;

    ctx.clearRect(0, 0, w, h);

    for (let i = 0; i < barCount; i++) {
        const x = i * (barWidth + gap);
        const barH = Math.max(1, peaks[i] * (h * 0.85));

        ctx.fillStyle = color;
        ctx.globalAlpha = 0.4 + peaks[i] * 0.6;
        ctx.beginPath();
        ctx.roundRect(x, centerY - barH / 2, barWidth, barH, 1);
        ctx.fill();
    }
    ctx.globalAlpha = 1;
}

function formatDuration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`;
}

// ===== Settings =====

async function loadSettings() {
    try {
        const res = await fetch('/api/settings');
        const settings = await res.json();
        applySettingsToUI(settings);
    } catch (e) {
        console.error('Failed to load settings:', e);
    }
}

function applySettingsToUI(settings) {
    if (settings.model) {
        document.getElementById('modelSelect').value = settings.model;
        document.getElementById('sysModel').textContent = `faster-whisper ${settings.model}`;
    }
    if (settings.beam_size !== undefined) {
        document.getElementById('beamSize').value = settings.beam_size;
        document.getElementById('beamValue').textContent = settings.beam_size;
    }
    if (settings.vad_threshold !== undefined) {
        document.getElementById('vadThreshold').value = settings.vad_threshold;
        document.getElementById('vadThreshValue').textContent = settings.vad_threshold;
    }
    if (settings.vad_silence !== undefined) {
        document.getElementById('vadSilence').value = settings.vad_silence;
        document.getElementById('vadSilenceValue').textContent = settings.vad_silence + 's';
    }
    if (settings.mode) {
        document.getElementById('sysMode').textContent = settings.mode === 'transcribe' ? 'Transcription' : 'Dispatch';
    }
}

async function updateSetting(key, value) {
    // Update local display immediately
    if (key === 'beam_size') document.getElementById('beamValue').textContent = value;
    if (key === 'vad_threshold') document.getElementById('vadThreshValue').textContent = value;
    if (key === 'vad_silence') document.getElementById('vadSilenceValue').textContent = value + 's';

    const statusEl = document.getElementById('modelStatus');

    if (key === 'model') {
        statusEl.textContent = `Loading ${value}...`;
        statusEl.className = 'model-status loading';
    }

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [key]: value })
        });
        const result = await res.json();

        if (result.reloaded) {
            statusEl.textContent = `✓ ${value} loaded`;
            statusEl.className = 'model-status success';
            document.getElementById('sysModel').textContent = `faster-whisper ${value}`;
            setTimeout(() => { statusEl.textContent = ''; statusEl.className = 'model-status'; }, 3000);
        } else if (key !== 'model') {
            statusEl.textContent = `✓ Updated`;
            statusEl.className = 'model-status success';
            setTimeout(() => { statusEl.textContent = ''; statusEl.className = 'model-status'; }, 2000);
        }
    } catch (e) {
        statusEl.textContent = `✗ Failed`;
        statusEl.className = 'model-status';
        console.error('Setting update failed:', e);
    }
}

// Update slider value display on input
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('beamSize')?.addEventListener('input', e => {
        document.getElementById('beamValue').textContent = e.target.value;
    });
    document.getElementById('vadThreshold')?.addEventListener('input', e => {
        document.getElementById('vadThreshValue').textContent = e.target.value;
    });
    document.getElementById('vadSilence')?.addEventListener('input', e => {
        document.getElementById('vadSilenceValue').textContent = e.target.value + 's';
    });
});

// ===== Audio Playback =====

function togglePlayback(audioFile, entryEl) {
    // If this entry is already playing, stop it
    if (currentPlayingEntry === entryEl && currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        stopPlayback(entryEl);
        return;
    }

    // Stop any currently playing audio
    if (currentAudio) {
        currentAudio.pause();
        currentAudio.currentTime = 0;
        if (currentPlayingEntry) stopPlayback(currentPlayingEntry);
    }

    // Play new
    const audio = new Audio(`/recordings/${audioFile}`);
    currentAudio = audio;
    currentPlayingEntry = entryEl;

    const waveformEl = entryEl.querySelector('.entry-waveform');
    const progressEl = entryEl.querySelector('.waveform-progress');
    const playBtn = entryEl.querySelector('.waveform-play-btn');

    waveformEl.classList.add('playing');
    playBtn.classList.add('playing');
    playBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;

    audio.addEventListener('timeupdate', () => {
        if (audio.duration) {
            const pct = (audio.currentTime / audio.duration) * 100;
            progressEl.style.width = pct + '%';
        }
    });

    audio.addEventListener('ended', () => stopPlayback(entryEl));
    audio.play().catch(e => {
        console.error('Playback error:', e);
        stopPlayback(entryEl);
    });
}

function stopPlayback(entryEl) {
    const waveformEl = entryEl.querySelector('.entry-waveform');
    const progressEl = entryEl.querySelector('.waveform-progress');
    const playBtn = entryEl.querySelector('.waveform-play-btn');

    if (waveformEl) waveformEl.classList.remove('playing');
    if (playBtn) {
        playBtn.classList.remove('playing');
        playBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>`;
    }
    if (progressEl) progressEl.style.width = '0%';

    if (currentPlayingEntry === entryEl) {
        currentAudio = null;
        currentPlayingEntry = null;
    }
}

// ===== WebSocket =====

function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws`);

    ws.onopen = () => {
        document.getElementById('wsStatus').textContent = 'Connected';
        document.getElementById('wsStatus').style.color = 'var(--accent-green)';
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    };
    ws.onclose = () => {
        document.getElementById('wsStatus').textContent = 'Disconnected';
        document.getElementById('wsStatus').style.color = 'var(--accent-red)';
        reconnectTimer = setTimeout(connect, 2000);
    };
    ws.onerror = () => { };
    ws.onmessage = (event) => handleMessage(JSON.parse(event.data));
}

function handleMessage(data) {
    switch (data.type) {
        case 'init':
            updateState(data.state);
            updateMuteButton(data.muted);
            if (data.devices) updateDeviceList(data.devices);
            const transcript = document.getElementById('transcript');
            transcript.innerHTML = '';
            if (data.log && data.log.length > 0) {
                data.log.forEach(entry => addLogEntry(entry));
            } else {
                showEmptyState();
            }
            break;
        case 'state':
            updateState(data.state);
            break;
        case 'log':
            removeEmptyState();
            addLogEntry(data.entry);
            break;
        case 'level':
            updateLevel(data.level);
            break;
        case 'mute':
            updateMuteButton(data.muted);
            break;
        case 'devices':
            updateDeviceList(data.devices);
            break;
        case 'settings':
            applySettingsToUI(data.settings);
            break;
        case 'clear':
            document.getElementById('transcript').innerHTML = '';
            showEmptyState();
            document.getElementById('entryCount').textContent = '0 entries';
            Object.keys(speakerColorMap).forEach(k => delete speakerColorMap[k]);
            speakerCount = 0;
            updateSpeakersLegend();
            break;
    }
}

// ===== State =====

function updateState(state) {
    const badge = document.getElementById('statusBadge');
    badge.setAttribute('data-state', state);
    badge.querySelector('.status-text').textContent = state.toUpperCase();

    document.getElementById('btnStart').disabled = state !== 'idle';
    document.getElementById('btnStop').disabled = state === 'idle';
    document.getElementById('btnMute').disabled = state === 'idle';
}

function updateLevel(level) {
    document.getElementById('levelBar').style.width = Math.min(100, (level / 0.15) * 100) + '%';
}

function updateMuteButton(muted) {
    const btn = document.getElementById('btnMute');
    const text = document.getElementById('muteText');
    btn.classList.toggle('active', muted);
    text.textContent = muted ? 'UNMUTE' : 'MUTE';
}

function updateDeviceList(devices) {
    document.getElementById('deviceSelect').innerHTML = devices.map(d =>
        `<option value="${d.id}" ${d.active ? 'selected' : ''}>${d.name}</option>`
    ).join('');
}

// ===== Transcript =====

let entryIdCounter = 0;

function addLogEntry(entry) {
    const transcript = document.getElementById('transcript');
    const color = getSpeakerColor(entry.speaker);
    const entryId = `entry-${entryIdCounter++}`;

    const el = document.createElement('div');
    el.className = 'entry';
    el.id = entryId;
    el.setAttribute('data-speaker', entry.speaker);

    let waveformHtml = '';
    if (entry.audio_file && entry.waveform) {
        waveformHtml = `
            <div class="entry-waveform" onclick="togglePlayback('${entry.audio_file}', document.getElementById('${entryId}'))">
                <div class="waveform-play-btn">
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
                </div>
                <div class="waveform-canvas-wrap">
                    <canvas data-peaks='${JSON.stringify(entry.waveform)}' data-color="${color}"></canvas>
                    <div class="waveform-progress"></div>
                </div>
                <span class="waveform-duration">${formatDuration(entry.duration)}</span>
            </div>
        `;
    }

    el.innerHTML = `
        <div class="entry-indicator" style="background:${color}"></div>
        <div class="entry-content">
            <div class="entry-header">
                <span class="entry-speaker" style="color:${color}">${escapeHtml(entry.speaker)}</span>
                <span class="entry-time">${entry.time_str}</span>
            </div>
            <div class="entry-text">${escapeHtml(entry.text)}</div>
            ${waveformHtml}
        </div>
    `;

    transcript.appendChild(el);
    transcript.scrollTop = transcript.scrollHeight;

    // Draw waveform after DOM insert
    const canvas = el.querySelector('canvas[data-peaks]');
    if (canvas) {
        requestAnimationFrame(() => {
            const peaks = JSON.parse(canvas.dataset.peaks);
            drawWaveform(canvas, peaks, canvas.dataset.color);
        });
    }

    const entries = transcript.querySelectorAll('.entry');
    document.getElementById('entryCount').textContent = `${entries.length} entries`;
}

function showEmptyState() {
    const transcript = document.getElementById('transcript');
    if (!transcript.querySelector('.transcript-empty')) {
        transcript.innerHTML = `
            <div class="transcript-empty">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.3">
                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                    <line x1="12" y1="19" x2="12" y2="23"/>
                    <line x1="8" y1="23" x2="16" y2="23"/>
                </svg>
                <p>Waiting for audio...</p>
                <p class="hint">Click START to begin transcribing</p>
            </div>
        `;
    }
}

function removeEmptyState() {
    const empty = document.querySelector('.transcript-empty');
    if (empty) empty.remove();
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===== Controls =====

async function startDispatch() {
    try { await fetch('/api/start', { method: 'POST' }); }
    catch (e) { console.error('Start failed:', e); }
}

async function stopDispatch() {
    try { await fetch('/api/stop', { method: 'POST' }); }
    catch (e) { console.error('Stop failed:', e); }
}

async function toggleMute() {
    try { await fetch('/api/mute', { method: 'POST' }); }
    catch (e) { console.error('Mute failed:', e); }
}

async function clearLog() {
    try { await fetch('/api/clear', { method: 'POST' }); }
    catch (e) { console.error('Clear failed:', e); }
}

async function switchDevice(deviceId) {
    try {
        await fetch('/api/device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device_id: parseInt(deviceId) })
        });
    } catch (e) {
        console.error('Device switch failed:', e);
    }
}

// ===== Clock =====

function updateClock() {
    const now = new Date();
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    document.getElementById('clock').textContent = `${h}:${m}:${s}`;
}

// ===== Resize handler =====

window.addEventListener('resize', () => {
    document.querySelectorAll('canvas[data-peaks]').forEach(canvas => {
        const peaks = JSON.parse(canvas.dataset.peaks);
        drawWaveform(canvas, peaks, canvas.dataset.color);
    });
});

// ===== Init =====

setInterval(updateClock, 1000);
updateClock();
connect();
loadSettings();
