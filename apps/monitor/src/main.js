import './css/design-tokens.css';
import './css/style.css';

/**
 * ClawRoom Monitor — Real API Client
 * Connects to ClawRoom monitor endpoints (SSE primary, polling fallback).
 * Event payload normalisation bridges the wire format to the timeline renderer.
 *
 * Views:
 *   1. Home Page (#homePage)   — shown when no room_id in URL
 *   2. Invite Modal (#inviteModal) — shown after room creation
 *   3. Monitor View (#app)     — shown when room_id + host_token in URL
 */

// ---------------------------------------------------------------------------
// Config: read from URL params
// ---------------------------------------------------------------------------

function parseConfig() {
  const p = new URLSearchParams(window.location.search);
  const roomId = p.get('room_id');
  const hostToken = p.get('host_token');
  // `api` allows pointing at a different origin in dev; empty = same-origin (prod on Cloudflare)
  const apiBase = (p.get('api') || '').replace(/\/$/, '');
  return roomId && hostToken ? { roomId, hostToken, apiBase } : null;
}

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const DOM = {
  // Views
  homePage: document.getElementById('homePage'),
  app: document.getElementById('app'),
  inviteModal: document.getElementById('inviteModal'),

  // Home page
  createForm: document.getElementById('createRoomForm'),
  inputTopic: document.getElementById('inputTopic'),
  inputGoal: document.getElementById('inputGoal'),
  inputOutcomes: document.getElementById('inputOutcomes'),
  inputTurnLimit: document.getElementById('inputTurnLimit'),
  inputTimeout: document.getElementById('inputTimeout'),
  advancedToggle: document.getElementById('advancedToggle'),
  advancedSection: document.getElementById('advancedSection'),
  btnCreate: document.getElementById('btnCreate'),
  formError: document.getElementById('formError'),

  // Invite modal
  modalRoomTopic: document.getElementById('modalRoomTopic'),
  modalInvites: document.getElementById('modalInvites'),
  btnEnterRoom: document.getElementById('btnEnterRoom'),

  // Monitor view
  headerTopic: document.getElementById('headerTopic'),
  headerStatus: document.getElementById('headerStatus'),
  headerId: document.getElementById('headerId'),
  agentOrbs: document.getElementById('agentOrbs'),
  roomSummary: document.getElementById('roomSummary'),
  summaryCompletionBadge: document.getElementById('summaryCompletionBadge'),
  summaryStopReason: document.getElementById('summaryStopReason'),
  summaryNarrative: document.getElementById('summaryNarrative'),
  summaryFilled: document.getElementById('summaryFilled'),
  summaryMissing: document.getElementById('summaryMissing'),
  timelineStream: document.getElementById('timelineStream'),
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const State = {
  roomId: '',
  hostToken: '',
  apiBase: '',
  status: 'active',
  participants: new Map(),   // name (lowercase) → { name, displayName, color, isTyping }
  cursor: 0,
  seenEventIds: new Set(),  // prevent duplicate renders on SSE + poll overlap
  summaryLoading: false,
  summaryLoaded: false,
  // Home page: stored after creation for "Enter Monitor" redirect
  createdRoom: null,
};

// Assign distinct accent colors in participant join order
const PARTICIPANT_COLORS = [
  'var(--color-accent-agent-1)',
  'var(--color-accent-agent-2)',
  'var(--color-accent-goal)',
  'var(--color-accent-owner)',
];
let colorIndex = 0;

function colorForParticipant(key) {
  if (!State.participants.has(key)) return 'var(--color-accent-system)';
  return State.participants.get(key).color;
}

function apiPath(path) {
  return `${State.apiBase}${path}`;
}

function statusReasonLabel(reason) {
  const labels = {
    goal_done: 'Goal Reached',
    mutual_done: 'Completed',
    timeout: 'Timed Out',
    turn_limit: 'Completed',
    stall_limit: 'Stalled',
    session_ended: 'Completed',
    manual_close: 'Closed',
    closed: 'Closed',
  };
  return labels[reason] || 'Completed';
}

// ---------------------------------------------------------------------------
// HOME PAGE: Create Room Logic (A1-A5)
// ---------------------------------------------------------------------------

function showHomePage() {
  DOM.homePage.hidden = false;
  DOM.app.hidden = true;

  // Advanced toggle
  DOM.advancedToggle.addEventListener('click', () => {
    const isOpen = DOM.advancedSection.classList.toggle('is-open');
    DOM.advancedToggle.classList.toggle('is-open', isOpen);
  });

  // Form submit
  DOM.createForm.addEventListener('submit', handleCreateRoom);

  // Instruction block copy — the prompt to paste into your agent
  const INSTRUCTION_TEXT = 'Read https://clawroom.cc/skill.md and create a ClawRoom for me.';

  const btnCopy = document.getElementById('btnCopyInstruction');
  if (btnCopy) {
    btnCopy.addEventListener('click', () => {
      navigator.clipboard.writeText(INSTRUCTION_TEXT).then(() => {
        btnCopy.textContent = 'Copied!';
        btnCopy.classList.add('copied');
        setTimeout(() => {
          btnCopy.textContent = 'Copy';
          btnCopy.classList.remove('copied');
        }, 2000);
      }).catch(() => {
        // Fallback: select the instruction text
        const pre = document.querySelector('.instruction-text');
        if (pre) {
          const range = document.createRange();
          range.selectNodeContents(pre);
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
        }
      });
    });
  }
}

async function handleCreateRoom(e) {
  e.preventDefault();

  // Disable button, show spinner
  DOM.btnCreate.disabled = true;
  const btnText = DOM.btnCreate.querySelector('.btn-text');
  const btnSpinner = DOM.btnCreate.querySelector('.btn-spinner');
  btnText.textContent = 'Creating…';
  btnSpinner.hidden = false;
  DOM.formError.hidden = true;

  // Build payload
  const topic = DOM.inputTopic.value.trim() || 'General discussion';
  const goal = DOM.inputGoal.value.trim() || 'Open-ended conversation';
  const participants = ['agent_a', 'agent_b']; // hidden default (A2)

  const payload = { topic, goal, participants };

  // Advanced fields
  const outcomesRaw = DOM.inputOutcomes.value.trim();
  if (outcomesRaw) {
    payload.expected_outcomes = outcomesRaw.split(',').map(s => s.trim()).filter(Boolean);
  }

  const turnLimit = parseInt(DOM.inputTurnLimit.value, 10);
  if (turnLimit && turnLimit > 0) payload.turn_limit = turnLimit;

  const timeout = parseInt(DOM.inputTimeout.value, 10);
  if (timeout && timeout > 0) payload.timeout_minutes = timeout;

  try {
    const res = await fetch('/rooms', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      throw new Error(errBody.error || errBody.message || `HTTP ${res.status}`);
    }

    const data = await res.json();
    State.createdRoom = data;
    showInviteModal(data);
  } catch (err) {
    DOM.formError.textContent = `Failed to create room: ${err.message}`;
    DOM.formError.hidden = false;
  } finally {
    DOM.btnCreate.disabled = false;
    btnText.textContent = 'Create Room';
    btnSpinner.hidden = true;
  }
}

// ---------------------------------------------------------------------------
// INVITE MODAL (A4-A5)
// ---------------------------------------------------------------------------

function showInviteModal(data) {
  const room = data.room || {};
  const invites = data.invites || {};
  const joinLinks = data.join_links || {};
  const monitorLink = data.monitor_link || '';
  const hostToken = data.host_token || '';
  const topic = room.topic || 'Untitled room';

  // Subtitle shows the room topic
  DOM.modalRoomTopic.textContent = topic;

  // Clear previous invites
  DOM.modalInvites.innerHTML = '';

  // Build monitor URL for "Enter Room" navigation
  const monitorUrl = buildMonitorUrl(room.id, hostToken, monitorLink);

  // Invite cards — one per participant
  const participantNames = Object.keys(invites);
  participantNames.forEach((name, i) => {
    const rawJoin = joinLinks[name] || `/join/${room.id}?token=${invites[name]}`;
    const joinUrl = absolutize(rawJoin);

    const card = document.createElement('div');
    card.className = 'invite-card';

    // Build a clean, copy-ready message for sharing
    const shareText = [
      `Join my ClawRoom session: "${topic}"`,
      ``,
      `Run this command:`,
      `uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "${joinUrl}"`,
    ].join('\n');

    // Display the bridge command in the card
    const bridgeCmd = `uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "${joinUrl}"`;

    card.innerHTML = `
      <div class="invite-card-header">
        <span class="invite-card-name">${escHtml(name)}</span>
        <button class="btn-copy" data-copy="${escAttr(shareText)}">Copy invite</button>
      </div>
      <div class="invite-code">${escHtml(bridgeCmd)}</div>
    `;
    DOM.modalInvites.appendChild(card);
  });

  // Copy buttons
  DOM.modalInvites.querySelectorAll('.btn-copy').forEach(btn => {
    btn.addEventListener('click', () => copyToClipboard(btn));
  });

  // Enter Room button
  DOM.btnEnterRoom.onclick = () => {
    window.location.href = monitorUrl;
  };

  // Show modal
  DOM.inviteModal.hidden = false;
}

function buildMonitorUrl(roomId, hostToken, monitorLink) {
  if (monitorLink && (monitorLink.startsWith('http://') || monitorLink.startsWith('https://'))) {
    return monitorLink;
  }
  // In dev, monitor is same-origin (Vite); in prod, same-origin too
  return `${window.location.origin}/?room_id=${roomId}&host_token=${hostToken}`;
}

function absolutize(pathOrUrl) {
  if (pathOrUrl.startsWith('http://') || pathOrUrl.startsWith('https://')) return pathOrUrl;
  // Use the API origin (same-origin via Vite proxy or Cloudflare)
  const origin = window.location.origin;
  return pathOrUrl.startsWith('/') ? `${origin}${pathOrUrl}` : `${origin}/${pathOrUrl}`;
}

async function copyToClipboard(btn) {
  const text = btn.dataset.copy;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.classList.remove('copied');
    }, 2000);
  } catch {
    // Fallback: select text in code block
    const code = btn.closest('.invite-card, .monitor-card')?.querySelector('.invite-code, .monitor-code');
    if (code) {
      const range = document.createRange();
      range.selectNodeContents(code);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    }
  }
}

// ---------------------------------------------------------------------------
// Event normalisation: wire format → internal renderer shape
// ---------------------------------------------------------------------------

/**
 * Maps a raw API event (id, type, payload, created_at) to the internal
 * shape expected by renderTimelineEvent().
 */
function normalizeEvent(raw) {
  const base = { _rawId: raw.id, type: raw.type };

  switch (raw.type) {
    case 'join':
      return {
        ...base,
        name: raw.payload.participant,
        actor: raw.payload.participant,
      };

    case 'leave':
      return {
        ...base,
        name: raw.payload.participant,
        actor: raw.payload.participant,
      };

    case 'msg': {
      const msg = raw.payload.message || {};
      return {
        ...base,
        name: msg.sender,
        actor: msg.sender,
        text: msg.text,
        intent: msg.intent,
      };
    }

    case 'relay': {
      const msg = raw.payload.message || {};
      return {
        ...base,
        type: 'relay',
        name: raw.payload.from || msg.sender,
        actor: raw.payload.from || msg.sender,
        text: msg.text,
        intent: msg.intent,
      };
    }

    case 'owner_wait':
      return {
        ...base,
        name: raw.payload.participant,
        actor: raw.payload.participant,
        text: raw.payload.text || '',
      };

    case 'owner_resume':
      return {
        ...base,
        name: raw.payload.participant,
        actor: raw.payload.participant,
        text: raw.payload.text || '',
      };

    case 'status':
      return {
        ...base,
        payload: {
          status: raw.payload.status,
          reason: raw.payload.stop_reason || raw.payload.reason || '',
        },
      };

    case 'result_ready':
      // Reuse the status rendering path; result_ready implies room is closed
      return {
        type: 'status',
        _rawId: raw.id,
        payload: {
          status: 'closed',
          reason: raw.payload.stop_reason || 'session_ended',
        },
      };

    default:
      return { ...base, text: JSON.stringify(raw.payload) };
  }
}

// ---------------------------------------------------------------------------
// Room snapshot → UI sync
// ---------------------------------------------------------------------------

function updateRoomUI(room) {
  // Header
  DOM.headerTopic.textContent = room.topic || room.id;
  DOM.headerId.textContent = `#${room.id}`;

  // Participants: sync orbs from server truth
  (room.participants || []).forEach(p => {
    const key = p.name.toLowerCase();
    if (!State.participants.has(key)) {
      State.participants.set(key, {
        name: key,
        displayName: p.name,
        color: PARTICIPANT_COLORS[colorIndex++ % PARTICIPANT_COLORS.length],
        isTyping: false,
      });
    }
    const entry = State.participants.get(key);
    entry.displayName = p.name;
  });
  renderOrbs();

  // Status badge
  if (room.status === 'closed') {
    updateStatusUI('closed', room.stop_reason || 'closed');
    maybeLoadRoomSummary(room.stop_reason || '');
  } else {
    // Check if any participant is waiting_owner
    const anyWaiting = (room.participants || []).some(p => p.waiting_owner);
    if (anyWaiting && State.status !== 'waiting_owner') {
      updateStatusUI('waiting_owner');
    } else if (!anyWaiting && State.status !== 'active') {
      updateStatusUI('active');
    }
  }
}

// ---------------------------------------------------------------------------
// Event processing (shared with renderer)
// ---------------------------------------------------------------------------

function processEvent(evt) {
  // Dedup
  if (evt._rawId != null && State.seenEventIds.has(evt._rawId)) return;
  if (evt._rawId != null) State.seenEventIds.add(evt._rawId);

  switch (evt.type) {
    case 'join': {
      const key = (evt.name || '').toLowerCase();
      if (!State.participants.has(key)) {
        State.participants.set(key, {
          name: key,
          displayName: evt.name,
          color: PARTICIPANT_COLORS[colorIndex++ % PARTICIPANT_COLORS.length],
          isTyping: false,
        });
        renderOrbs();
      }
      renderTimelineEvent(evt);
      break;
    }
    case 'leave': {
      const key = (evt.name || '').toLowerCase();
      State.participants.delete(key);
      renderOrbs();
      renderTimelineEvent(evt);
      break;
    }
    case 'status':
      updateStatusUI(evt.payload.status, evt.payload.reason);
      if (evt.payload.status === 'closed') {
        maybeLoadRoomSummary(evt.payload.reason || '');
      }
      renderTimelineEvent(evt);
      break;
    case 'owner_wait':
      updateStatusUI('waiting_owner');
      renderTimelineEvent(evt);
      break;
    case 'owner_resume':
      updateStatusUI('active');
      renderTimelineEvent(evt);
      break;
    default:
      renderTimelineEvent(evt);
  }
}

// ---------------------------------------------------------------------------
// EventClient: SSE → poll fallback with reconnect
// ---------------------------------------------------------------------------

class EventClient {
  constructor(roomId, hostToken, apiBase) {
    this.roomId = roomId;
    this.hostToken = hostToken;
    this.apiBase = apiBase;
    this._sseFailures = 0;
    this._maxSseFail = 3;
    this._pollTimer = null;
    this._sse = null;
    this._stopped = false;
  }

  start() {
    this._trySSE();
  }

  stop() {
    this._stopped = true;
    if (this._sse) { this._sse.close(); this._sse = null; }
    if (this._pollTimer) { clearTimeout(this._pollTimer); this._pollTimer = null; }
  }

  // --- SSE ---

  _trySSE() {
    if (this._stopped) return;
    if (this._sseFailures >= this._maxSseFail) {
      console.info('[ClawRoom Monitor] SSE too many failures, switching to polling permanently.');
      this._startPolling();
      return;
    }

    const url = `${this.apiBase}/rooms/${this.roomId}/monitor/stream`
      + `?host_token=${encodeURIComponent(this.hostToken)}&after=${State.cursor}`;

    this._sse = new EventSource(url);

    // Each SSE `event:` name matches the event type (join, msg, relay, etc.)
    const TYPES = ['join', 'leave', 'msg', 'relay', 'owner_wait', 'owner_resume', 'status', 'result_ready'];
    TYPES.forEach(type => {
      this._sse.addEventListener(type, e => {
        try {
          const raw = JSON.parse(e.data);
          raw.type = raw.type || type;
          if (e.lastEventId) State.cursor = Math.max(State.cursor, parseInt(e.lastEventId, 10));
          const evt = normalizeEvent(raw);
          processEvent(evt);
        } catch (err) {
          console.error('[ClawRoom Monitor] SSE parse error', err, e.data);
        }
      });
    });

    // room_closed is a synthetic SSE event the server emits when the room is done
    this._sse.addEventListener('room_closed', e => {
      try {
        const room = JSON.parse(e.data);
        updateRoomUI(room);
        maybeLoadRoomSummary(room.stop_reason || '');
      } catch (_) { }
      this.stop();
    });

    this._sse.addEventListener('error', _e => {
      if (this._stopped) return;
      this._sse.close();
      this._sse = null;
      this._sseFailures++;
      const delay = Math.min(2000 * this._sseFailures, 10000);
      console.warn(`[ClawRoom Monitor] SSE error (attempt ${this._sseFailures}), retrying in ${delay}ms`);
      updateReconnectUI(true);
      setTimeout(() => {
        updateReconnectUI(false);
        this._trySSE();
      }, delay);
    });
  }

  // --- Polling fallback ---

  _startPolling() {
    if (this._stopped) return;
    this._poll();
  }

  async _poll() {
    if (this._stopped) return;
    try {
      const url = `${this.apiBase}/rooms/${this.roomId}/monitor/events`
        + `?host_token=${encodeURIComponent(this.hostToken)}&after=${State.cursor}&limit=500`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      updateRoomUI(data.room);

      for (const raw of (data.events || [])) {
        if (raw.id > State.cursor) State.cursor = raw.id;
        const evt = normalizeEvent(raw);
        processEvent(evt);
      }

      // Stop polling once room is closed
      if (data.room.status !== 'active') {
        maybeLoadRoomSummary(data.room.stop_reason || '');
        this.stop();
        return;
      }
    } catch (err) {
      console.error('[ClawRoom Monitor] Poll error:', err);
    }
    this._pollTimer = setTimeout(() => this._poll(), 1500);
  }
}

// ---------------------------------------------------------------------------
// UI Updaters
// ---------------------------------------------------------------------------

function updateStatusUI(status, reason = '') {
  State.status = status;
  DOM.headerStatus.className = 'status-indicator';

  if (status === 'active') {
    DOM.headerStatus.classList.add('thinking');
    DOM.headerStatus.textContent = 'Active Sync';
  } else if (status === 'waiting_owner') {
    DOM.headerStatus.classList.add('owner-wait');
    DOM.headerStatus.textContent = 'Owner Action Required';
  } else if (status === 'closed') {
    DOM.headerStatus.classList.add('done');
    DOM.headerStatus.textContent = statusReasonLabel(reason);
  } else if (status === 'reconnecting') {
    DOM.headerStatus.classList.add('reconnecting');
    DOM.headerStatus.textContent = 'Reconnecting…';
  }
}

function updateReconnectUI(isReconnecting) {
  if (isReconnecting) {
    DOM.headerStatus.className = 'status-indicator reconnecting';
    DOM.headerStatus.textContent = 'Reconnecting…';
  } else {
    updateStatusUI(State.status);
  }
}

function renderOrbs() {
  DOM.agentOrbs.innerHTML = '';
  State.participants.forEach((data) => {
    const orb = document.createElement('div');
    orb.className = `agent-orb ${data.isTyping ? 'is-typing' : ''}`;
    orb.style.setProperty('--orb-color', data.color);
    orb.innerHTML = `
      <div class="avatar"></div>
      <div class="name">${data.displayName || data.name}</div>
    `;
    DOM.agentOrbs.appendChild(orb);
  });
}

// ---------------------------------------------------------------------------
// Cinematic Timeline Renderer
// ---------------------------------------------------------------------------

function renderTimelineEvent(event) {
  const el = document.createElement('div');
  el.className = 'timeline-event';

  if (event.type === 'msg' || event.type === 'relay') {
    const key = (event.actor || event.name || '').toLowerCase();
    const color = colorForParticipant(key);
    el.style.setProperty('--event-color', color);
    el.innerHTML = `
      <div class="event-meta">
        <span class="event-actor">${event.name}</span>
      </div>
      <div class="event-content">${escHtml(event.text || '')}</div>
    `;
  } else if (event.type === 'owner_wait') {
    el.classList.add('event-owner-wait');
    el.innerHTML = `
      <div class="event-meta">
        <span class="event-actor">⏸ Waiting for you</span>
      </div>
      <div class="event-content">${escHtml(event.text || '')}</div>
    `;
  } else if (event.type === 'owner_resume') {
    el.innerHTML = `
      <div class="event-meta">
        <span class="event-actor" style="color: var(--color-accent-owner)">${event.name}</span>
      </div>
      <div class="event-content">${escHtml(event.text || '')}</div>
    `;
  } else if (event.type === 'system') {
    el.classList.add('event-system');
    el.innerHTML = `<div class="event-content">${escHtml(event.text || '')}</div>`;
  } else if (event.type === 'join') {
    el.classList.add('event-system');
    el.innerHTML = `<div class="event-content" style="opacity: 0.7;">&bull; ${escHtml(event.name)} joined the room</div>`;
  } else if (event.type === 'leave') {
    el.classList.add('event-system');
    el.innerHTML = `<div class="event-content" style="opacity: 0.7;">&bull; ${escHtml(event.name)} left the room</div>`;
  } else if (event.type === 'status') {
    const status = event.payload?.status;
    const reason = event.payload?.reason;
    // Avoid noisy startup line from the initial "active" status event.
    if (status === 'active' && !reason) return;

    el.classList.add('event-system');
    const isSuccess = ['goal_done', 'mutual_done', 'turn_limit'].includes(reason);
    const icon = isSuccess ? '✓' : '•';
    const label = status === 'waiting_owner'
      ? 'Owner Action Required'
      : statusReasonLabel(reason || (status === 'closed' ? 'closed' : 'session_ended'));
    el.innerHTML = `<div class="event-content" style="color: var(--color-accent-goal)">${icon} ${label}</div>`;
  }

  if (el.innerHTML) {
    DOM.timelineStream.appendChild(el);
    setTimeout(() => {
      el.classList.add('revealed');
      window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
    }, 50);
  }
}

// Safe HTML escaping to prevent XSS from agent message text
function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Escape string for use in HTML attribute
function escAttr(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function renderSummaryList(container, rows, emptyText, isMissing = false) {
  container.innerHTML = '';
  if (!rows.length) {
    const empty = document.createElement('div');
    empty.className = 'summary-empty';
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }

  for (const row of rows) {
    const item = document.createElement('div');
    item.className = `summary-item ${isMissing ? 'is-missing' : ''}`.trim();

    const head = document.createElement('div');
    head.className = 'summary-item-head';

    const key = document.createElement('span');
    key.className = 'summary-item-key';
    key.textContent = row.key;

    const state = document.createElement('span');
    state.className = 'summary-item-state';
    state.textContent = isMissing ? 'missing' : 'filled';

    head.appendChild(key);
    head.appendChild(state);
    item.appendChild(head);

    if (row.value) {
      const value = document.createElement('div');
      value.className = 'summary-item-value';
      value.textContent = row.value;
      item.appendChild(value);
    }

    container.appendChild(item);
  }
}

function renderRoomSummary(result = {}, stopReasonFallback = '', fallbackMessage = '') {
  const expectedOutcomes = Array.isArray(result.expected_outcomes)
    ? result.expected_outcomes.map((x) => String(x || '').trim()).filter(Boolean)
    : [];

  const outcomesFilled = result.outcomes_filled && typeof result.outcomes_filled === 'object'
    ? result.outcomes_filled
    : {};

  const missingFromResult = Array.isArray(result.outcomes_missing)
    ? result.outcomes_missing.map((x) => String(x || '').trim()).filter(Boolean)
    : [];

  const filledRows = [];
  const filledKeys = new Set();
  for (const outcome of expectedOutcomes) {
    const value = String(outcomesFilled[outcome] || '').trim();
    if (value) {
      filledRows.push({ key: outcome, value });
      filledKeys.add(outcome);
    }
  }
  for (const [k, v] of Object.entries(outcomesFilled)) {
    const key = String(k || '').trim();
    const value = String(v || '').trim();
    if (!key || !value || filledKeys.has(key)) continue;
    filledRows.push({ key, value });
  }

  const missingOutcomes = missingFromResult.length
    ? missingFromResult
    : expectedOutcomes.filter((outcome) => !filledRows.find((row) => row.key === outcome));

  const completion = result.outcomes_completion && typeof result.outcomes_completion === 'object'
    ? result.outcomes_completion
    : {
      filled: filledRows.filter((row) => expectedOutcomes.includes(row.key)).length,
      total: expectedOutcomes.length,
    };

  const total = Number(completion.total) || 0;
  const filled = Math.min(Number(completion.filled) || 0, total);
  DOM.summaryCompletionBadge.textContent = total > 0 ? `${filled}/${total} complete` : 'Open-ended room';

  const stopReason = String(result.stop_reason || stopReasonFallback || '').trim();
  DOM.summaryStopReason.textContent = stopReason
    ? `Session ended: ${statusReasonLabel(stopReason)}`
    : 'Session ended';

  const summaryText = String(result.summary || '').trim();
  DOM.summaryNarrative.textContent =
    fallbackMessage
    || summaryText
    || (total > 0
      ? `Captured ${filled} of ${total} expected outcomes.`
      : 'No required outcomes were set for this room.');

  renderSummaryList(
    DOM.summaryFilled,
    filledRows,
    total > 0 ? 'No outcomes were filled.' : 'No outcomes were requested.',
    false,
  );
  renderSummaryList(
    DOM.summaryMissing,
    missingOutcomes.map((outcome) => ({ key: outcome, value: '' })),
    total > 0 ? 'All expected outcomes were completed.' : 'Open-ended room.',
    true,
  );

  DOM.roomSummary.hidden = false;
  DOM.roomSummary.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function maybeLoadRoomSummary(stopReasonFallback = '') {
  if (State.summaryLoaded || State.summaryLoading) return;
  if (!State.roomId || !State.hostToken) return;

  State.summaryLoading = true;
  try {
    const url =
      apiPath(`/rooms/${encodeURIComponent(State.roomId)}/monitor/result`)
      + `?host_token=${encodeURIComponent(State.hostToken)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderRoomSummary(data.result || {}, stopReasonFallback);
    State.summaryLoaded = true;
  } catch (err) {
    console.error('[ClawRoom Monitor] failed to load room summary:', err);
    renderRoomSummary({}, stopReasonFallback, 'Room ended. Summary is not available yet.');
  } finally {
    State.summaryLoading = false;
  }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function showMonitorView(cfg) {
  DOM.homePage.hidden = true;
  DOM.app.hidden = false;

  State.roomId = cfg.roomId;
  State.hostToken = cfg.hostToken;
  State.apiBase = cfg.apiBase || '';
  State.cursor = 0;
  State.seenEventIds.clear();
  State.summaryLoading = false;
  State.summaryLoaded = false;
  State.participants.clear();
  colorIndex = 0;

  DOM.roomSummary.hidden = true;
  DOM.summaryCompletionBadge.textContent = '0/0 complete';
  DOM.summaryStopReason.textContent = '';
  DOM.summaryNarrative.textContent = '';
  DOM.summaryFilled.innerHTML = '';
  DOM.summaryMissing.innerHTML = '';
  DOM.timelineStream.innerHTML = '';
  DOM.agentOrbs.innerHTML = '';

  // Initial placeholder text while loading
  DOM.headerTopic.textContent = 'Connecting to Room…';
  DOM.headerId.textContent = `#${cfg.roomId}`;
  updateStatusUI('active');

  const client = new EventClient(cfg.roomId, cfg.hostToken, cfg.apiBase);
  client.start();
}

function init() {
  const cfg = parseConfig();

  if (cfg) {
    // URL has room_id + host_token → go directly to monitor
    showMonitorView(cfg);
  } else {
    // No room info → show create room home page
    showHomePage();
  }
}

document.addEventListener('DOMContentLoaded', init);
