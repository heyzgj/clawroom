import './css/design-tokens.css';
import './css/style.css';

/**
 * ClawRoom Monitor — Real API Client
 * Connects to ClawRoom monitor endpoints (SSE primary, polling fallback).
 * Event payload normalisation bridges the wire format to the timeline renderer.
 *
 * Views:
 *   1. Home Page (#homePage)   — shown when no room_id in URL
 *   2. Monitor View (#app)     — shown when room_id + host_token or participant token in URL
 */

// ---------------------------------------------------------------------------
// Config: read from URL params
// ---------------------------------------------------------------------------

function parseConfig() {
  const p = new URLSearchParams(window.location.search);
  const opsMode = p.get('ops');
  const roomId = p.get('room_id');
  const hostToken = (p.get('host_token') || '').trim();
  const participantToken = (p.get('participant_token') || p.get('token') || '').trim();
  let adminToken = (p.get('admin_token') || '').trim();
  // Ops auth is a shared secret. To reduce friction (and avoid leaking the
  // token in URLs), remember it in localStorage once provided.
  if (opsMode === '1' || opsMode === 'true') {
    try {
      const stored = (localStorage.getItem('clawroom_monitor_admin_token') || '').trim();
      if (!adminToken && stored) adminToken = stored;
      if (adminToken) localStorage.setItem('clawroom_monitor_admin_token', adminToken);
    } catch (_) {
      // localStorage might be unavailable (private mode). Best-effort only.
    }
  }
  const apiBase = resolveApiBase();
  const missionsMode = p.get('missions');
  const briefingMode = p.get('briefing');
  if (roomId && hostToken) return { mode: 'room', roomId, authMode: 'host', authToken: hostToken, apiBase };
  if (roomId && participantToken) return { mode: 'room', roomId, authMode: 'participant', authToken: participantToken, apiBase };
  // Briefing view: ?briefing=1&rooms=r1,r2&tokens=t1,t2&title=...&bot=...
  if (briefingMode === '1' || briefingMode === 'true' || missionsMode === '1' || missionsMode === 'true') {
    const rooms = (p.get('rooms') || '').split(',').filter(Boolean);
    const tokens = (p.get('tokens') || '').split(',').filter(Boolean);
    const title = p.get('title') || '';
    const bot = p.get('bot') || '';
    return { mode: 'briefing', rooms, tokens, title, bot, apiBase };
  }
  if (opsMode === '1' || opsMode === 'true') return { mode: 'ops', adminToken, apiBase };
  return { mode: 'home', apiBase };
}

function resolveApiBase() {
  const p = new URLSearchParams(window.location.search);
  const explicit = (p.get('api') || '').replace(/\/$/, '');
  if (explicit) return explicit;

  // Production monitor runs on clawroom.cc while API is served from api.clawroom.cc.
  const host = window.location.hostname.toLowerCase();
  if (host === 'clawroom.cc' || host === 'www.clawroom.cc') {
    return 'https://api.clawroom.cc';
  }
  return '';
}

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const DOM = {
  // Views
  homePage: document.getElementById('homePage'),
  opsPage: document.getElementById('opsPage'),
  briefingPage: document.getElementById('briefingPage'),
  app: document.getElementById('app'),

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

  // Ops dashboard view
  opsMetricTotal: document.getElementById('opsMetricTotal'),
  opsMetricActive: document.getElementById('opsMetricActive'),
  opsMetricInputRequired: document.getElementById('opsMetricInputRequired'),
  opsMetricOnline: document.getElementById('opsMetricOnline'),
  opsMetricTurns: document.getElementById('opsMetricTurns'),
  opsMetricEvents5m: document.getElementById('opsMetricEvents5m'),
  opsMetricCreated1h: document.getElementById('opsMetricCreated1h'),
  opsMetricMessages5m: document.getElementById('opsMetricMessages5m'),
  opsMetricStale: document.getElementById('opsMetricStale'),
  opsMetricBudget: document.getElementById('opsMetricBudget'),
  opsRoomsTbody: document.getElementById('opsRoomsTbody'),
  opsEventLog: document.getElementById('opsEventLog'),
  opsUpdatedAt: document.getElementById('opsUpdatedAt'),
  opsSystemSummary: document.getElementById('opsSystemSummary'),
  opsAlerts: document.getElementById('opsAlerts'),
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const State = {
  roomId: '',
  authMode: 'host',
  authToken: '',
  apiBase: '',
  status: 'active',
  participants: new Map(),   // name (lowercase) → { name, displayName, color, isTyping }
  cursor: 0,
  seenEventIds: new Set(),  // prevent duplicate renders on SSE + poll overlap
  summaryLoading: false,
  summaryLoaded: false,
};

const OpsState = {
  apiBase: '',
  adminToken: '',
  cursor: 0,
  timer: null,
  stopping: false,
  hasHealthySnapshot: false,
};

const BriefingState = {
  timer: null,
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

function participantRole(name) {
  const key = String(name || '').trim().toLowerCase();
  if (key === 'host') return 'host';
  if (key === 'guest') return 'guest';
  return 'other';
}

function apiPath(path) {
  return `${State.apiBase}${path}`;
}

function currentRoomAuthQuery() {
  if (!State.authToken) return '';
  const key = State.authMode === 'host' ? 'host_token' : 'token';
  return `${key}=${encodeURIComponent(State.authToken)}`;
}

function roomApiUrl(path, extraQuery = '') {
  const params = [currentRoomAuthQuery(), extraQuery].filter(Boolean).join('&');
  return `${apiPath(path)}${params ? `?${params}` : ''}`;
}

function statusReasonLabel(reason) {
  const labels = {
    goal_done: 'Outcome ready',
    mutual_done: 'Outcome ready',
    timeout: 'Needs a quick check',
    turn_limit: 'Result ready',
    stall_limit: 'Needs a quick check',
    session_ended: 'Finished',
    manual_close: 'Finished',
    closed: 'Finished',
  };
  return labels[reason] || 'Finished';
}

function humanizeCode(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.replace(/[_-]+/g, ' ');
}

function executionModeLabel(mode) {
  const value = String(mode || '').trim().toLowerCase();
  if (value === 'managed_attached' || value === 'managed_hosted') return 'managed handoff';
  if (value === 'compatibility') return 'standard handoff';
  return humanizeCode(value);
}

function certificationLabel(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!text) return '';
  if (text.includes('uncertified')) return 'best-effort runtime';
  if (text.includes('certified')) return 'stable runtime';
  return humanizeCode(text);
}

function buildResultHeadline(stopReason, filled, total) {
  const reason = String(stopReason || '').trim().toLowerCase();
  if (reason === 'timeout' || reason === 'stall_limit') return 'Needs a quick check';
  if (total > 0 && filled > 0) return 'Result ready';
  if (reason === 'goal_done' || reason === 'mutual_done' || reason === 'turn_limit') return 'Outcome ready';
  return 'Conversation finished';
}

function buildResultNarrative(stopReason, filled, total, turnCount) {
  const reason = String(stopReason || '').trim().toLowerCase();
  const safeTurns = Number(turnCount) || 0;
  const turnText = safeTurns > 0 ? ` after ${safeTurns} ${safeTurns === 1 ? 'turn' : 'turns'}` : '';

  if (total > 0 && filled >= total) {
    return `The room wrapped up with all ${total} requested outcomes ready${turnText}.`;
  }
  if (total > 0 && filled > 0) {
    const remaining = Math.max(total - filled, 0);
    if (reason === 'timeout' || reason === 'stall_limit') {
      return `The room paused before it fully finished. ${filled} of ${total} requested outcomes are ready, and ${remaining} still need follow-up.`;
    }
    return `The room wrapped up with ${filled} of ${total} requested outcomes ready${turnText}. ${remaining} ${remaining === 1 ? 'item still needs follow-up.' : 'items still need follow-up.'}`;
  }
  if (total > 0) {
    if (reason === 'timeout' || reason === 'stall_limit') {
      return 'The room stopped before it could finish the requested handoff.';
    }
    return `The room ended before it captured the requested handoff${turnText}.`;
  }
  if (reason === 'timeout' || reason === 'stall_limit') {
    return 'The conversation paused before it fully finished.';
  }
  return `The conversation wrapped up${turnText}.`;
}

function buildAttentionCopy(room = {}) {
  const executionAttention = room.execution_attention || {};
  const reasons = new Set(
    Array.isArray(executionAttention.reasons)
      ? executionAttention.reasons.map((reason) => String(reason || '').trim()).filter(Boolean)
      : []
  );

  if (reasons.has('waiting_on_owner') || reasons.has('owner_reply_overdue')) {
    return {
      label: 'Needs your input',
      detail: 'A collaborator asked for your answer before they can continue.',
      action: 'Reply in Telegram to keep this moving.',
      cta: 'Reply in Telegram',
    };
  }
  if (reasons.has('join_not_started')) {
    return {
      label: 'Waiting on the other side',
      detail: 'The room is ready, but the other side has not joined yet.',
      action: 'Open Telegram and resend the invite if it keeps waiting.',
      cta: 'Open Telegram',
    };
  }
  if (
    reasons.has('compatibility_mode') ||
    reasons.has('no_managed_runner') ||
    reasons.has('compatibility_room_stalled') ||
    reasons.has('first_relay_overdue') ||
    reasons.has('replacement_pending') ||
    reasons.has('repair_claim_overdue')
  ) {
    return {
      label: 'Needs your attention',
      detail: 'This room has not started cleanly yet.',
      action: 'Open Telegram and restart or resend the handoff.',
      cta: 'Open Telegram',
    };
  }
  if (reasons.has('awaiting_mutual_completion') || reasons.has('terminal_turn_without_room_close')) {
    return {
      label: 'Almost done',
      detail: 'The answer is basically ready, but one side has not wrapped up yet.',
      action: 'Wait a moment. If it stays here, reopen the room.',
      cta: 'Open Telegram',
    };
  }
  if (reasons.has('required_fields_not_progressing')) {
    return {
      label: 'Needs a clearer handoff',
      detail: 'The room is active, but it is not landing the result you asked for yet.',
      action: 'Reply with the missing detail or refocus the request.',
      cta: 'Open Telegram',
    };
  }
  return {
    label: 'Needs your attention',
    detail: 'This room needs a quick manual check before it can continue.',
    action: 'Open Telegram and keep the conversation moving.',
    cta: 'Open Telegram',
  };
}

// ---------------------------------------------------------------------------
// HOME PAGE: One-line instruction copy
// ---------------------------------------------------------------------------

function showHomePage() {
  DOM.homePage.hidden = false;
  DOM.opsPage.hidden = true;
  DOM.briefingPage.hidden = true;
  DOM.app.hidden = true;

  // Instruction block copy — the prompt to paste into your agent
const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md. Then help me with the task I send next. After I send the task, ask me one short clarify before you create any ClawRoom. Keep that clarify to one focused question or confirmation, not a checklist. If another agent should join, create the room, give me one watch link I can open plus one forwardable full invite, keep watching until the room closes, and report back in plain language.";
  const COPY_LABEL = 'Copy for My Agent';

  const btnCopy = document.getElementById('btnCopyInstruction');
  const instructionTextEl = document.getElementById('instructionText');
  if (instructionTextEl) instructionTextEl.textContent = INSTRUCTION_TEXT;
  if (btnCopy) {
    btnCopy.addEventListener('click', () => {
      navigator.clipboard.writeText(INSTRUCTION_TEXT).then(() => {
        btnCopy.textContent = 'Copied!';
        btnCopy.classList.add('copied');
        setTimeout(() => {
          btnCopy.textContent = COPY_LABEL;
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
        online: Boolean(p.online),
        lastSeenAt: p.last_seen_at || null,
      });
    }
    const entry = State.participants.get(key);
    entry.displayName = p.name;
    entry.online = Boolean(p.online);
    entry.lastSeenAt = p.last_seen_at || null;
  });
  renderOrbs();

  // Status badge
  if (room.status === 'closed') {
    updateStatusUI('closed', room.stop_reason || 'closed');
    maybeLoadRoomSummary(room.stop_reason || '');
  } else {
    const lifecycle = String(room.lifecycle_state || '').trim().toLowerCase();
    const anyWaiting = (room.participants || []).some(p => p.waiting_owner);
    const waitingOwner = lifecycle === 'input_required' || anyWaiting;
    if (waitingOwner && State.status !== 'waiting_owner') {
      updateStatusUI('waiting_owner');
    } else if (!waitingOwner && State.status !== 'active') {
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
          online: true,
          lastSeenAt: null,
        });
        renderOrbs();
      }
      const entry = State.participants.get(key);
      if (entry) {
        entry.online = true;
      }
      renderTimelineEvent(evt);
      break;
    }
    case 'leave': {
      const key = (evt.name || '').toLowerCase();
      const entry = State.participants.get(key);
      if (entry) {
        entry.online = false;
        entry.lastSeenAt = new Date().toISOString();
        renderOrbs();
      }
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
  constructor(roomId, authMode, authToken, apiBase) {
    this.roomId = roomId;
    this.authMode = authMode;
    this.authToken = authToken;
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

    const basePath = this.authMode === 'host'
      ? `/rooms/${this.roomId}/monitor/stream`
      : `/rooms/${this.roomId}/stream`;
    const queryKey = this.authMode === 'host' ? 'host_token' : 'token';
    const url = `${this.apiBase}${basePath}`
      + `?${queryKey}=${encodeURIComponent(this.authToken)}&after=${State.cursor}`;

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
      const basePath = this.authMode === 'host'
        ? `/rooms/${this.roomId}/monitor/events`
        : `/rooms/${this.roomId}/events`;
      const queryKey = this.authMode === 'host' ? 'host_token' : 'token';
      const url = `${this.apiBase}${basePath}`
        + `?${queryKey}=${encodeURIComponent(this.authToken)}&after=${State.cursor}&limit=500`;
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
    DOM.headerStatus.textContent = 'Working on it';
  } else if (status === 'waiting_owner') {
    DOM.headerStatus.classList.add('owner-wait');
    DOM.headerStatus.textContent = 'Needs your input';
  } else if (status === 'closed') {
    DOM.headerStatus.classList.add('done');
    DOM.headerStatus.textContent = buildResultHeadline(reason, 0, 0);
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
    const offlineClass = data.online === false ? 'offline' : '';
    const role = participantRole(data.displayName || data.name);
    const roleClass = role === 'host' ? 'role-host' : (role === 'guest' ? 'role-guest' : 'role-other');
    orb.className = `agent-orb ${roleClass} ${data.isTyping ? 'is-typing' : ''} ${offlineClass}`.trim();
    if (role === 'host') {
      orb.style.setProperty('--orb-color', 'var(--color-accent-agent-1)');
    } else if (role === 'guest') {
      orb.style.setProperty('--orb-color', 'var(--color-accent-agent-2)');
    } else {
      orb.style.setProperty('--orb-color', data.color);
    }
    const presence = data.online
      ? 'online'
      : (data.lastSeenAt ? `last active ${new Date(data.lastSeenAt).toLocaleTimeString()}` : 'not currently active');
    orb.innerHTML = `
      <div class="avatar"></div>
      <div class="meta">
        <div class="name">${data.displayName || data.name}</div>
        <div class="presence">${presence}</div>
      </div>
    `;
    DOM.agentOrbs.appendChild(orb);
  });
}

async function primeRoomSnapshot() {
  if (!State.roomId || !State.authToken) return;
  try {
    const path = State.authMode === 'host'
      ? `/rooms/${encodeURIComponent(State.roomId)}/monitor/events`
      : `/rooms/${encodeURIComponent(State.roomId)}/events`;
    const url = roomApiUrl(path, 'after=0&limit=1');
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    if (data && data.room) {
      updateRoomUI(data.room);
    }
  } catch (_) {
    // Best-effort priming. Event stream/polling will continue.
  }
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
        <span class="event-actor">⏸ Needs your input</span>
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
    el.innerHTML = `<div class="event-content"><em>${escHtml(event.name)} joined the room</em></div>`;
  } else if (event.type === 'leave') {
    el.classList.add('event-system');
    el.innerHTML = `<div class="event-content"><em>${escHtml(event.name)} left the room</em></div>`;
  } else if (event.type === 'status') {
    const status = event.payload?.status;
    const reason = event.payload?.reason;
    // Avoid noisy startup line from the initial "active" status event.
    if (status === 'active' && !reason) return;

    el.classList.add('event-system');
    const isSuccess = ['goal_done', 'mutual_done', 'turn_limit'].includes(reason);
    const icon = isSuccess ? '✓' : '•';
    const label = status === 'waiting_owner'
      ? 'Needs your input'
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
    state.textContent = isMissing ? 'still needed' : 'ready';

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
  DOM.summaryCompletionBadge.textContent = total > 0 ? `${filled} of ${total} ready` : 'Open conversation';

  const stopReason = String(result.stop_reason || stopReasonFallback || '').trim();
  DOM.summaryStopReason.textContent = buildResultHeadline(stopReason, filled, total);

  const summaryText = String(result.summary || '').trim();
  const turnCount = Number(result.turn_count) || 0;
  DOM.summaryNarrative.textContent =
    fallbackMessage
    || summaryText
    || buildResultNarrative(stopReason, filled, total, turnCount);

  renderSummaryList(
    DOM.summaryFilled,
    filledRows,
    total > 0 ? 'Nothing is ready yet.' : 'No structured handoff was requested.',
    false,
  );
  renderSummaryList(
    DOM.summaryMissing,
    missingOutcomes.map((outcome) => ({ key: outcome, value: '' })),
    total > 0 ? 'Everything you asked for is here.' : 'This room was open-ended.',
    true,
  );

  DOM.roomSummary.hidden = false;
  DOM.roomSummary.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function maybeLoadRoomSummary(stopReasonFallback = '') {
  if (State.summaryLoaded || State.summaryLoading) return;
  if (!State.roomId || !State.authToken) return;

  State.summaryLoading = true;
  try {
    const path = State.authMode === 'host'
      ? `/rooms/${encodeURIComponent(State.roomId)}/monitor/result`
      : `/rooms/${encodeURIComponent(State.roomId)}/result`;
    const url = roomApiUrl(path);
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderRoomSummary(data.result || {}, stopReasonFallback);
    State.summaryLoaded = true;
  } catch (err) {
    console.error('[ClawRoom Monitor] failed to load room summary:', err);
    renderRoomSummary({}, stopReasonFallback, 'The room finished, but the handoff summary is not ready yet.');
  } finally {
    State.summaryLoading = false;
  }
}

// ---------------------------------------------------------------------------
// Ops Dashboard (multi-room observability)
// ---------------------------------------------------------------------------

function monitorApiPath(path) {
  return `${OpsState.apiBase}${path}`;
}

function monitorFetch(path) {
  const token = (OpsState.adminToken || '').trim();
  const headers = token ? { 'x-monitor-token': token } : {};
  return fetch(monitorApiPath(path), { headers });
}

function resetOpsMetrics() {
  DOM.opsMetricTotal.textContent = '--';
  DOM.opsMetricActive.textContent = '--';
  DOM.opsMetricInputRequired.textContent = '--';
  DOM.opsMetricOnline.textContent = '--';
  DOM.opsMetricTurns.textContent = '--';
  DOM.opsMetricEvents5m.textContent = '--';
  DOM.opsMetricCreated1h.textContent = '--';
  DOM.opsMetricMessages5m.textContent = '--';
  DOM.opsMetricStale.textContent = '--';
  DOM.opsMetricBudget.textContent = '--';
}

function renderOpsDegraded(message) {
  const text = String(message || 'Ops data is temporarily unavailable.');
  DOM.opsUpdatedAt.textContent = `degraded: ${text}`;
  const authFailure = /admin token|required|unauthorized/i.test(text);
  if (!OpsState.hasHealthySnapshot || authFailure) {
    resetOpsMetrics();
    DOM.opsRoomsTbody.innerHTML = `<tr><td colspan="9" class="ops-empty">${escHtml(text)}</td></tr>`;
    DOM.opsEventLog.innerHTML = `<div class="ops-empty">${escHtml(text)}</div>`;
    DOM.opsSystemSummary.innerHTML = `<div class="ops-empty-inline">${escHtml(text)}</div>`;
    DOM.opsAlerts.innerHTML = `<div class="ops-empty-inline">${escHtml(text)}</div>`;
    return;
  }
  DOM.opsAlerts.innerHTML = `
    <div class="ops-alert-item warning">
      <span class="ops-alert-label">degraded</span>
      <div class="ops-alert-message">${escHtml(text)} Showing the last good snapshot until the feed recovers.</div>
    </div>
  ` + DOM.opsAlerts.innerHTML;
}

function fmtTime(ts) {
  const date = new Date(ts);
  if (!Number.isFinite(date.getTime())) return '--';
  return date.toLocaleTimeString();
}

function statusPillClass(status) {
  const value = String(status || '').toLowerCase();
  if (value === 'active') return 'active';
  if (value === 'closed') return 'closed';
  return '';
}

function tonePillClass(value) {
  const tone = String(value || '').toLowerCase();
  if (tone === 'healthy' || tone === 'normal') return 'ok';
  if (tone === 'attention' || tone === 'warm') return 'warning';
  if (tone === 'degraded' || tone === 'hot' || tone === 'stale') return 'critical';
  return '';
}

function renderOpsRooms(rooms) {
  if (!Array.isArray(rooms) || !rooms.length) {
    DOM.opsRoomsTbody.innerHTML = '<tr><td colspan="9" class="ops-empty">No rooms yet</td></tr>';
    return;
  }

  DOM.opsRoomsTbody.innerHTML = '';
  for (const room of rooms) {
    const tr = document.createElement('tr');
    const topic = String(room.topic || '').trim();
    const safeTopic = topic || '(no topic)';
    const executionAttention = String(room.execution_attention_state || 'healthy');
    const executionSummary = String(room.execution_attention_summary || '').trim();
    const managedCoverage = String(room.managed_coverage || 'none');
    const productOwned = Boolean(room.product_owned);
    const primaryRootCauseCode = String(room.primary_root_cause_code || '').trim();
    const primaryRootCauseConfidence = String(room.primary_root_cause_confidence || '').trim();
    const primaryRootCauseSummary = String(room.primary_root_cause_summary || '').trim();
    const recoveryPending = Number(room.recovery_pending_count || 0);
    const recoveryIssued = Number(room.recovery_issued_count || 0);
    const recoverySummary =
      recoveryPending > 0 || recoveryIssued > 0
        ? `Recovery backlog · pending ${recoveryPending} · issued ${recoveryIssued}`
        : '';
    const currentPhase = String(room.current_runner_phase || '').trim();
    const currentPhaseDetail = String(room.current_runner_phase_detail || '').trim();
    const currentPhaseAge = Number(room.current_runner_phase_age_ms || 0);
    const currentLeaseRemaining = Number(room.current_runner_lease_remaining_ms || 0);
    const runnerPhaseSummary = currentPhase
      ? `Runner checkpoint · ${currentPhase}${currentPhaseDetail ? ` · ${currentPhaseDetail}` : ''}${currentPhaseAge > 0 ? ` · phase age ${fmtDurationMs(currentPhaseAge)}` : ''}${currentLeaseRemaining !== 0 ? ` · lease ${currentLeaseRemaining > 0 ? `${fmtDurationMs(currentLeaseRemaining)} left` : `${fmtDurationMs(Math.abs(currentLeaseRemaining))} overdue`}` : ''}`
      : '';
    const rootCauseSummary = primaryRootCauseSummary
      ? `Likely root cause · ${primaryRootCauseSummary}${primaryRootCauseCode ? ` (${primaryRootCauseCode}${primaryRootCauseConfidence ? ` · ${primaryRootCauseConfidence}` : ''})` : ''}`
      : '';
    tr.innerHTML = `
      <td>
        <div class="ops-room-id">${escHtml(String(room.room_id || ''))}</div>
        <span class="ops-room-topic">${escHtml(safeTopic)}</span>
        <div class="ops-room-id">${escHtml(String(room.execution_mode || 'compatibility'))} · ${escHtml(String(room.runner_certification || 'none'))} · ${escHtml(managedCoverage)} managed · ${escHtml(productOwned ? 'product-owned' : 'not product-owned')} · ${escHtml(String(room.attempt_status || 'pending'))} · ${escHtml(executionAttention)}</div>
        ${executionSummary ? `<div class="ops-room-topic">${escHtml(executionSummary)}</div>` : ''}
        ${runnerPhaseSummary ? `<div class="ops-room-topic">${escHtml(runnerPhaseSummary)}</div>` : ''}
        ${rootCauseSummary ? `<div class="ops-room-topic">${escHtml(rootCauseSummary)}</div>` : ''}
        ${recoverySummary ? `<div class="ops-room-topic">${escHtml(recoverySummary)}</div>` : ''}
      </td>
      <td><span class="ops-status-pill ${statusPillClass(room.status)}">${escHtml(String(room.status || ''))}</span></td>
      <td>${escHtml(String(room.lifecycle_state || ''))}</td>
      <td>
        <span class="ops-status-pill ${tonePillClass(room.health_state)}">${escHtml(String(room.health_state || 'healthy'))}</span>
        <span class="ops-status-pill ${tonePillClass(room.budget_state)}">${escHtml(String(room.budget_state || 'normal'))}</span>
      </td>
      <td>${Number(room.participants_joined || 0)}/${Number(room.participants_total || 0)}</td>
      <td>${Number(room.participants_online || 0)}</td>
      <td>${Number(room.turn_count || 0)}</td>
      <td>${fmtDuration(room.time_remaining_seconds)}</td>
      <td>${fmtTime(room.updated_at)}</td>
    `;
    DOM.opsRoomsTbody.appendChild(tr);
  }
}

function renderOpsEvent(event) {
  const first = DOM.opsEventLog.firstElementChild;
  if (first && first.classList.contains('ops-empty')) {
    first.remove();
  }
  const el = document.createElement('div');
  el.className = 'ops-event-item';
  const payload = event.payload || {};
  const status = payload.status || payload.lifecycle_state || '';
  const body = [
    payload.topic ? `topic=${payload.topic}` : '',
    status ? `status=${status}` : '',
    payload.turn_count != null ? `turns=${payload.turn_count}` : '',
    payload.participants_online != null ? `online=${payload.participants_online}` : '',
    payload.stop_reason ? `stop=${payload.stop_reason}` : '',
  ].filter(Boolean).join(' | ');
  el.innerHTML = `
    <div class="ops-event-meta">
      <span>${escHtml(String(event.type || 'event'))}</span>
      <span>${escHtml(String(event.room_id || ''))} · ${fmtTime(event.created_at)}</span>
    </div>
    <div class="ops-event-body">${escHtml(body || JSON.stringify(payload))}</div>
  `;
  DOM.opsEventLog.prepend(el);
}

function fmtDuration(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return '--';
  if (value < 60) return `${Math.round(value)}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function fmtDurationMs(ms) {
  const value = Number(ms || 0);
  if (!Number.isFinite(value) || value <= 0) return '--';
  if (value < 1000) return `${Math.round(value)}ms`;
  return fmtDuration(value / 1000);
}

function renderOpsSystemSummary(data) {
  const metrics = data.metrics || {};
  const capacity = data.capacity || {};
  const budget = data.budget || {};
  const registry = data.registry || {};
  const startSlo = data.start_slo || {};
  const rootCauses = data.root_causes || {};
  const activeTopRootCauses = Array.isArray(rootCauses.active_top) ? rootCauses.active_top : [];
  const recentTopRootCauses = Array.isArray(rootCauses.recent_24h_top) ? rootCauses.recent_24h_top : [];
  const stopReasons = data.stop_reasons_last_24h || {};

  const summaryItems = [
    {
      label: 'Throughput',
      value: `${Number(metrics.rooms_created_last_1h || 0)} room(s) created in the last hour and ${Number(metrics.messages_last_5m || 0)} message event(s) in the last 5 minutes.`,
    },
    {
      label: 'Capacity',
      value: `${Number(capacity.stale_active_rooms || 0)} stale active room(s), ${Number(capacity.active_rooms_without_online || 0)} active room(s) without online participants, ${Number(metrics.waiting_owner_rooms || 0)} waiting on owner, ${Number(capacity.owner_reply_overdue_rooms || 0)} room(s) waiting too long for an owner reply, ${Number(capacity.runner_attention_rooms || 0)} room(s) need runner attention, ${Number(capacity.takeover_rooms || 0)} room(s) need takeover, ${Number(capacity.recovery_backlog_rooms || 0)} room(s) carrying recovery backlog, ${Number(capacity.repair_claim_overdue_rooms || 0)} room(s) with overdue repair claims, ${Number(capacity.first_relay_risk_rooms || 0)} room(s) at first-relay risk, ${Number(capacity.runner_lease_low_rooms || 0)} room(s) with low runner lease, oldest active age ${fmtDuration(capacity.oldest_active_room_age_seconds)}.`,
    },
    {
      label: 'Runner Plane',
      value: `${Number(metrics.active_runners || 0)} active runner(s), ${Number(metrics.product_owned_rooms || 0)} product-owned room(s), ${Number(metrics.full_managed_rooms || 0)} fully managed room(s), ${Number(metrics.partial_managed_rooms || 0)} partially managed room(s), ${Number(metrics.certified_managed_rooms || 0)} certified managed room(s), ${Number(metrics.candidate_managed_rooms || 0)} uncertified managed room(s), ${Number(metrics.automatic_recovery_eligible_rooms || 0)} auto-recovery eligible room(s), ${Number(metrics.compatibility_rooms || 0)} compatibility room(s), ${Number(metrics.unmanaged_compatibility_rooms || 0)} unmanaged compatibility room(s), ${Number(metrics.stalled_runner_rooms || 0)} stalled room(s), ${Number(metrics.restarting_runner_rooms || 0)} restarting room(s), ${Number(metrics.abandoned_runner_rooms || 0)} abandoned room(s), ${Number(metrics.recovery_rooms || 0)} room(s) carrying a recovery reason, ${Number(metrics.recovery_pending_actions || 0)} pending recovery action(s), ${Number(metrics.recovery_issued_actions || 0)} issued recovery action(s), ${Number(metrics.repair_package_issued_rooms || 0)} room(s) with repair packages already sent, ${Number(metrics.repair_claim_overdue_rooms || 0)} room(s) with overdue repair claims, ${Number(metrics.owner_reply_overdue_rooms || 0)} room(s) with overdue owner replies, ${Number(metrics.first_relay_risk_rooms || 0)} room(s) at first-relay risk, ${Number(metrics.runner_lease_low_rooms || 0)} room(s) with a low runner lease.`,
    },
    {
      label: 'Root Causes',
      value: activeTopRootCauses.length || recentTopRootCauses.length
        ? `Active top: ${activeTopRootCauses.length ? activeTopRootCauses.map((bucket) => `${String(bucket.summary || bucket.code || 'unknown')} (${Number(bucket.rooms || 0)})`).join(' · ') : 'none'}; last 24h: ${recentTopRootCauses.length ? recentTopRootCauses.map((bucket) => `${String(bucket.summary || bucket.code || 'unknown')} (${Number(bucket.rooms || 0)})`).join(' · ') : 'none'}.`
        : 'No room-level root causes are currently clustered.',
    },
    {
      label: 'Start SLO',
      value: `join p50 ${fmtDurationMs(startSlo.join_latency_ms?.p50)} · p95 ${fmtDurationMs(startSlo.join_latency_ms?.p95)} · relay p50 ${fmtDurationMs(startSlo.first_relay_latency_ms?.p50)} · p95 ${fmtDurationMs(startSlo.first_relay_latency_ms?.p95)}.`,
    },
    {
      label: 'Budget',
      value: budget.configured
        ? `Projected monthly load: ${Number(budget.projected_monthly_rooms || 0)} rooms, ${Number(budget.projected_monthly_events || 0)} events. Current risk is ${String(budget.status || 'normal')}.`
        : 'Budget proxy is off. Configure monthly rooms, events, or active-room thresholds to turn it on.',
    },
    {
      label: 'Registry',
      value: `${String(registry.mode || 'healthy')} · last event ${fmtDuration(registry.last_event_age_seconds)} ago · ${Number(registry.event_rows || 0)}/${Number(registry.max_event_rows || 0)} recent event rows retained.`,
    },
    {
      label: 'Close Mix (24h)',
      value: Object.keys(stopReasons).length
        ? Object.entries(stopReasons).map(([reason, count]) => `${reason}: ${count}`).join(' · ')
        : 'No closed rooms in the last 24 hours.',
    },
  ];

  DOM.opsSystemSummary.innerHTML = summaryItems.map((item) => `
    <div class="ops-summary-item">
      <span class="ops-summary-label">${escHtml(item.label)}</span>
      <div class="ops-summary-value">${escHtml(item.value)}</div>
    </div>
  `).join('');
}

function renderOpsAlerts(alerts, budget) {
  const items = Array.isArray(alerts) ? [...alerts] : [];
  if (!items.length) {
    items.push({
      key: 'healthy',
      severity: 'info',
      message: budget && budget.configured
        ? 'No active alerts. Capacity and projected budget are within the configured envelope.'
        : 'No active alerts. Budget estimate is currently unconfigured.',
    });
  }
  DOM.opsAlerts.innerHTML = items.map((alert) => `
    <div class="ops-alert-item ${escHtml(String(alert.severity || 'info'))}">
      <span class="ops-alert-label">${escHtml(String(alert.key || 'alert'))}</span>
      <div class="ops-alert-message">${escHtml(String(alert.message || ''))}</div>
    </div>
  `).join('');
}

async function refreshOpsOverview() {
  const res = await monitorFetch('/monitor/overview?limit=120');
  if (!res.ok) {
    let detail = '';
    try {
      const payload = await res.json();
      detail = payload.message || payload.error || '';
    } catch (_) {
      // ignore parse failures
    }
    throw new Error(detail ? `overview HTTP ${res.status}: ${detail}` : `overview HTTP ${res.status}`);
  }
  const data = await res.json();
  const metrics = data.metrics || {};
  DOM.opsMetricTotal.textContent = String(Number(metrics.total_rooms || 0));
  DOM.opsMetricActive.textContent = String(Number(metrics.active_rooms || 0));
  DOM.opsMetricInputRequired.textContent = String(Number(metrics.input_required_rooms || 0));
  DOM.opsMetricOnline.textContent = String(Number(metrics.online_participants || 0));
  DOM.opsMetricTurns.textContent = String(Number(metrics.total_turns || 0));
  DOM.opsMetricEvents5m.textContent = String(Number(metrics.events_last_5m || 0));
  DOM.opsMetricCreated1h.textContent = String(Number(metrics.rooms_created_last_1h || 0));
  DOM.opsMetricMessages5m.textContent = String(Number(metrics.messages_last_5m || 0));
  DOM.opsMetricStale.textContent = String(Number((data.capacity || {}).stale_active_rooms || 0));
  const budget = data.budget || {};
  DOM.opsMetricBudget.textContent = budget.configured
    ? `${String(budget.status || 'normal')} ${Math.round(Number(budget.utilization_ratio || 0) * 100)}%`
    : 'off';
  DOM.opsUpdatedAt.textContent = `updated ${fmtTime(data.generated_at)}`;
  OpsState.hasHealthySnapshot = true;
  renderOpsRooms(data.rooms || []);
  renderOpsSystemSummary(data);
  renderOpsAlerts(data.alerts || [], budget);
}

async function refreshOpsEvents() {
  const res = await monitorFetch(`/monitor/events?after=${OpsState.cursor}&limit=300`);
  if (!res.ok) {
    let detail = '';
    try {
      const payload = await res.json();
      detail = payload.message || payload.error || '';
    } catch (_) {
      // ignore parse failures
    }
    throw new Error(detail ? `events HTTP ${res.status}: ${detail}` : `events HTTP ${res.status}`);
  }
  const data = await res.json();
  const events = Array.isArray(data.events) ? data.events : [];
  for (const event of events) {
    renderOpsEvent(event);
  }
  OpsState.cursor = Number(data.next_cursor || OpsState.cursor || 0);
}

async function pollOps() {
  if (OpsState.stopping) return;
  try {
    await Promise.all([refreshOpsOverview(), refreshOpsEvents()]);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    renderOpsDegraded(message);
  } finally {
    if (!OpsState.stopping) {
      OpsState.timer = setTimeout(pollOps, 2000);
    }
  }
}

function showOpsDashboard(cfg) {
  // Stop briefing polling if running
  if (BriefingState.timer) {
    clearInterval(BriefingState.timer);
    BriefingState.timer = null;
  }
  DOM.homePage.hidden = true;
  DOM.briefingPage.hidden = true;
  DOM.app.hidden = true;
  DOM.opsPage.hidden = false;
  DOM.opsEventLog.innerHTML = '<div class="ops-empty">Waiting for events…</div>';
  DOM.opsRoomsTbody.innerHTML = '';
  DOM.opsSystemSummary.innerHTML = '<div class="ops-empty-inline">Waiting for ops overview…</div>';
  DOM.opsAlerts.innerHTML = '<div class="ops-empty-inline">Waiting for alerts…</div>';
  OpsState.apiBase = cfg.apiBase || '';
  OpsState.adminToken = cfg.adminToken || '';
  OpsState.cursor = 0;
  OpsState.stopping = false;
  OpsState.hasHealthySnapshot = false;
  if (OpsState.timer) clearTimeout(OpsState.timer);
  if (!String(OpsState.adminToken || '').trim()) {
    OpsState.stopping = true;
    renderOpsDegraded('Monitor admin token required. Open the ops link with ?admin_token=... first.');
    return;
  }
  pollOps();
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

function showMonitorView(cfg) {
  DOM.homePage.hidden = true;
  DOM.opsPage.hidden = true;
  DOM.briefingPage.hidden = true;
  DOM.app.hidden = false;
  OpsState.stopping = true;
  if (OpsState.timer) {
    clearTimeout(OpsState.timer);
    OpsState.timer = null;
  }

  State.roomId = cfg.roomId;
  State.authMode = cfg.authMode || 'host';
  State.authToken = cfg.authToken || '';
  State.apiBase = cfg.apiBase || '';
  State.cursor = 0;
  State.seenEventIds.clear();
  State.summaryLoading = false;
  State.summaryLoaded = false;
  State.participants.clear();
  colorIndex = 0;

  DOM.roomSummary.hidden = true;
  DOM.summaryCompletionBadge.textContent = 'Starting up';
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
  void primeRoomSnapshot();

  const client = new EventClient(cfg.roomId, State.authMode, State.authToken, cfg.apiBase);
  client.start();
}

// ---------------------------------------------------------------------------
// Briefing View — CEO check-in surface
// ---------------------------------------------------------------------------

function showBriefingView(cfg) {
  const page = document.getElementById('briefingPage');
  if (!page) return;

  // Hide all other pages, show briefing
  DOM.homePage.hidden = true;
  DOM.opsPage.hidden = true;
  DOM.app.hidden = true;
  page.hidden = false;

  // Stop any running polling (ops or previous briefing)
  OpsState.stopping = true;
  if (OpsState.timer) {
    clearTimeout(OpsState.timer);
    OpsState.timer = null;
  }
  if (BriefingState.timer) {
    clearInterval(BriefingState.timer);
    BriefingState.timer = null;
  }

  function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }

  const apiBase = cfg.apiBase || '';
  const rooms = cfg.rooms || [];
  const tokens = cfg.tokens || [];
  const bot = cfg.bot || '';
  const title = cfg.title || 'Mission Briefing';

  // DOM refs
  const titleEl = document.getElementById('briefingTitle');
  const subtitleEl = document.getElementById('briefingSubtitle');
  const quietSection = document.getElementById('briefingQuiet');
  const quietLabel = document.getElementById('briefingQuietLabel');
  const quietDetail = document.getElementById('briefingQuietDetail');
  const needsYouSection = document.getElementById('briefingNeedsYou');
  const needsYouLabel = document.getElementById('briefingNeedsYouLabel');
  const needsYouDetail = document.getElementById('briefingNeedsYouDetail');
  const telegramLink = document.getElementById('briefingTelegramLink');
  const resultsSection = document.getElementById('briefingResults');
  const resultsLabel = document.getElementById('briefingResultsLabel');
  const outcomesEl = document.getElementById('briefingOutcomes');
  const detailsBody = document.getElementById('briefingDetailsBody');

  if (titleEl) titleEl.textContent = title;

  // pollTimer managed via BriefingState.timer

  async function fetchRoomData(roomId, token) {
    // Use query param auth to avoid CORS preflight on custom headers
    const qs = token ? `?host_token=${encodeURIComponent(token)}` : '';
    // Try the result endpoint first for completed rooms
    try {
      const resultRes = await fetch(`${apiBase}/rooms/${roomId}/monitor/result${qs}`);
      if (resultRes.ok) {
        const data = await resultRes.json();
        return { roomId, result: data.result || null, room: data.room || null, ok: true };
      }
    } catch (_) { /* fall through */ }
    // Fall back to basic room status
    try {
      const roomRes = await fetch(`${apiBase}/rooms/${roomId}${qs}`);
      if (roomRes.ok) {
        const data = await roomRes.json();
        return { roomId, result: null, room: data.room || data, ok: true };
      }
    } catch (_) { /* fall through */ }
    return { roomId, result: null, room: null, ok: false };
  }

  async function refresh() {
    const fetches = rooms.map((roomId, i) => fetchRoomData(roomId, tokens[i] || ''));
    const results = await Promise.all(fetches);

    // Determine overall state
    const roomsWithData = results.filter(r => r.ok);
    const needsOwner = [];
    const doneRooms = [];
    const activeRooms = [];

    for (const r of roomsWithData) {
      const room = r.room || {};
      const lifecycleState = room.lifecycle_state || '';
      const attentionState = (room.execution_attention || {}).state || '';
      const status = room.status || '';

      if (lifecycleState === 'input_required' ||
          attentionState === 'attention' ||
          attentionState === 'takeover_recommended' ||
          attentionState === 'takeover_required') {
        needsOwner.push(r);
      } else if (status === 'closed' || lifecycleState === 'completed') {
        doneRooms.push(r);
      } else {
        activeRooms.push(r);
      }
    }

    // Hide all sections first
    quietSection.hidden = true;
    needsYouSection.hidden = true;
    resultsSection.hidden = true;

    const allDone = rooms.length > 0 && doneRooms.length === rooms.length;

    if (needsOwner.length > 0) {
      // State 2: Needs you
      needsYouSection.hidden = false;
      const count = needsOwner.length;
      const primaryNeed = buildAttentionCopy(needsOwner[0].room || {});
      if (needsYouLabel) {
        needsYouLabel.textContent = count === 1 ? primaryNeed.label : `${count} rooms need your attention`;
      }
      needsYouDetail.textContent = count === 1
        ? `${primaryNeed.detail} ${primaryNeed.action}`
        : `${count} rooms need your attention. ${primaryNeed.action}`;
      if (bot) {
        telegramLink.href = `https://t.me/${bot}`;
        telegramLink.textContent = primaryNeed.cta;
        telegramLink.hidden = false;
      } else {
        telegramLink.hidden = true;
      }
      if (subtitleEl) {
        subtitleEl.textContent = count === 1
          ? 'Something needs your attention now'
          : `${count} rooms need your attention right now`;
      }
    } else if (allDone) {
      // State 3: Done
      resultsSection.hidden = false;
      resultsLabel.textContent = doneRooms.length === 1 ? 'Result ready' : `${doneRooms.length} results ready`;

      // Render outcomes
      let outcomesHtml = '';
      for (const r of doneRooms) {
        const outcomes = (r.result && r.result.outcomes_filled) || {};
        const keys = Object.keys(outcomes);
        if (keys.length === 0) {
          outcomesHtml += `<div class="briefing-outcome">
            <span class="briefing-outcome-key">Result</span>
            <span class="briefing-outcome-value">Finished, but there is no structured handoff to review.</span>
            <span class="briefing-outcome-room">${esc(r.roomId)}</span>
          </div>`;
        } else {
          for (const key of keys) {
            outcomesHtml += `<div class="briefing-outcome">
              <span class="briefing-outcome-key">${esc(key)}</span>
              <span class="briefing-outcome-value">${esc(String(outcomes[key]))}</span>
              ${doneRooms.length > 1 ? `<span class="briefing-outcome-room">${esc(r.roomId)}</span>` : ''}
            </div>`;
          }
        }
      }
      outcomesEl.innerHTML = outcomesHtml;

      // Render execution details
      let detailsHtml = '';
      for (const r of doneRooms) {
        const result = r.result || {};
        const room = r.room || {};
        const turnCount = result.turn_count || room.turn_count || 0;
        const certification = result.runner_certification || '';
        const executionMode = result.execution_mode || '';
        const recoveryReason = result.last_recovery_reason || '';
        const topic = room.topic || '';

        let metaParts = [];
        if (turnCount) metaParts.push(`${turnCount} ${turnCount === 1 ? 'turn' : 'turns'}`);
        if (executionMode) metaParts.push(executionModeLabel(executionMode));
        if (topic) metaParts.push(topic);

        let badgesHtml = '';
        if (certification) {
          badgesHtml += `<span class="briefing-badge briefing-badge--certified">${esc(certificationLabel(certification))}</span>`;
        }
        if (recoveryReason) {
          badgesHtml += `<span class="briefing-badge briefing-badge--recovery">Recovered after: ${esc(humanizeCode(recoveryReason))}</span>`;
        }

        detailsHtml += `<div class="briefing-detail-card">
          <div class="briefing-detail-room">${esc(r.roomId)}</div>
          <div class="briefing-detail-meta">${esc(metaParts.join(' \u00B7 '))}</div>
          ${badgesHtml ? `<div style="margin-top:4px">${badgesHtml}</div>` : ''}
        </div>`;
      }
      detailsBody.innerHTML = detailsHtml;

      if (subtitleEl) subtitleEl.textContent = `Everything wrapped up \u00B7 ${new Date().toLocaleTimeString()}`;
    } else {
      // State 1: All quiet
      quietSection.hidden = false;
      const inProgress = activeRooms.length + needsOwner.length;
      const total = roomsWithData.length;
      if (total === 0) {
        quietLabel.textContent = 'Couldn’t load rooms';
        quietDetail.textContent = rooms.length > 0
          ? 'We could not reach these rooms yet. Check the link and try again.'
          : 'This briefing link does not include any rooms yet.';
      } else {
        quietLabel.textContent = 'Working on it';
        quietDetail.textContent = inProgress === 1
          ? '1 room is moving. Nothing you need to do right now.'
          : `${inProgress} rooms are moving. Nothing you need to do right now.`;
        if (doneRooms.length > 0) {
          quietDetail.textContent += doneRooms.length === 1
            ? ' 1 result is already ready.'
            : ` ${doneRooms.length} results are already ready.`;
        }
      }
      if (subtitleEl) {
        subtitleEl.textContent = total === 0
          ? 'Checking progress...'
          : `${total} room${total !== 1 ? 's' : ''} tracked \u00B7 ${new Date().toLocaleTimeString()}`;
      }
    }
  }

  refresh();
  BriefingState.timer = setInterval(refresh, 5000);
}

function init() {
  const cfg = parseConfig();

  if (cfg.mode === 'room') {
    showMonitorView(cfg);
  } else if (cfg.mode === 'briefing') {
    State.apiBase = cfg.apiBase || '';
    showBriefingView(cfg);
  } else if (cfg.mode === 'ops') {
    State.apiBase = cfg.apiBase || '';
    showOpsDashboard(cfg);
  } else {
    State.apiBase = cfg.apiBase || resolveApiBase();
    showHomePage();
  }
}

document.addEventListener('DOMContentLoaded', init);
