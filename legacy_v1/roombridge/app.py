from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .store import MessageData, RoomCreateData, RoomStore


def _norm_name(text: str) -> str:
    return " ".join(text.strip().lower().split())


class RoomCreateIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    goal: str = Field(..., min_length=1, max_length=2000)
    participants: list[str] = Field(..., min_length=2, max_length=8)
    required_fields: list[str] = Field(default_factory=list, max_length=50)
    turn_limit: int = Field(default=12, ge=2, le=200)
    timeout_minutes: int = Field(default=20, ge=1, le=1440)
    stall_limit: int = Field(default=2, ge=1, le=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JoinIn(BaseModel):
    client_name: str | None = Field(default=None, max_length=120)


class LeaveIn(BaseModel):
    reason: str = Field(default="left room", max_length=500)


class CloseIn(BaseModel):
    reason: str = Field(default="manual close", max_length=500)


class MessageIn(BaseModel):
    intent: Literal["ASK", "ANSWER", "DONE", "NEED_HUMAN", "NOTE"] = "ANSWER"
    text: str = Field(..., min_length=1, max_length=8000)
    fills: dict[str, str] = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    wants_reply: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


def _clean_participants(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = _norm_name(item)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _clean_fields(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


DB_PATH = Path(os.getenv("ROOMBRIDGE_DB", Path(__file__).resolve().parents[1] / "roombridge.db"))
store = RoomStore(DB_PATH)
app = FastAPI(title="RoomBridge", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    store.init()


def _participant_token(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail="missing X-Invite-Token")
    return value


def _map_store_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


def _sse(data: dict[str, Any], event: str = "event", event_id: int | None = None) -> bytes:
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    payload = json.dumps(data, ensure_ascii=False)
    for line in payload.splitlines():
        lines.append(f"data: {line}")
    lines.append("")
    return ("\n".join(lines) + "\n").encode("utf-8")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "true"}


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.post("/rooms")
def create_room(body: RoomCreateIn, request: Request) -> dict[str, Any]:
    participants = _clean_participants(body.participants)
    if len(participants) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 unique participants")
    required_fields = _clean_fields(body.required_fields)
    created = store.create_room(
        RoomCreateData(
            topic=body.topic.strip(),
            goal=body.goal.strip(),
            participants=participants,
            required_fields=required_fields,
            turn_limit=body.turn_limit,
            timeout_minutes=body.timeout_minutes,
            stall_limit=body.stall_limit,
            metadata=body.metadata,
        )
    )
    base = str(request.base_url).rstrip("/")
    room_id = created["room"]["id"]
    invite_links = {
        name: f"{base}/invites/{token}"
        for name, token in created["invite_tokens"].items()
    }
    host_link = f"{base}/rooms/{room_id}/monitor?host_token={created['host_token']}"
    return {
        "room": created["room"],
        "invites": created["invite_tokens"],
        "invite_links": invite_links,
        "host_token": created["host_token"],
        "host_link": host_link,
        "policy": {
            "turn_limit": body.turn_limit,
            "timeout_minutes": body.timeout_minutes,
            "stall_limit": body.stall_limit,
            "stop_when": "goal_done OR mutual_done OR need_human OR limits",
        },
    }


@app.get("/invites/{token}")
def inspect_invite(token: str) -> dict[str, Any]:
    try:
        data = store.inspect_invite(token)
        return data
    except Exception as exc:  # pragma: no cover - mapped
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}")
def get_room(
    room_id: str,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return {"room": store.get_room_for_participant(room_id, _participant_token(x_invite_token))}
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/join")
def join_room(
    room_id: str,
    body: JoinIn,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return store.join_room(room_id, _participant_token(x_invite_token), body.client_name)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/leave")
def leave_room(
    room_id: str,
    body: LeaveIn,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return store.leave_room(room_id, _participant_token(x_invite_token), body.reason)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/messages")
def post_message(
    room_id: str,
    body: MessageIn,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        data = MessageData(
            intent=body.intent,
            text=body.text,
            fills=body.fills,
            facts=body.facts,
            questions=body.questions,
            wants_reply=body.wants_reply,
            metadata=body.metadata,
        )
        return store.post_message(room_id, _participant_token(x_invite_token), data)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/events")
def room_events(
    room_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return store.participant_events(room_id, _participant_token(x_invite_token), after, limit)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/stream")
async def room_stream(
    room_id: str,
    request: Request,
    invite_token: str = Query(..., min_length=1),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        store.get_room_for_participant(room_id, invite_token)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc

    async def gen() -> Any:
        cursor = after
        while True:
            if await request.is_disconnected():
                break
            try:
                batch = store.participant_events(room_id, invite_token, cursor, 200)
            except Exception as exc:
                yield _sse({"error": str(exc)}, event="error")
                break
            events = batch["events"]
            if events:
                for item in events:
                    cursor = item["id"]
                    yield _sse({"event": item, "room": batch["room"]}, event="room_event", event_id=item["id"])
            else:
                yield b": ping\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/rooms/{room_id}/result")
def room_result(
    room_id: str,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return {"result": store.participant_result(room_id, _participant_token(x_invite_token))}
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/close")
def close_room(
    room_id: str,
    body: CloseIn,
    x_invite_token: str | None = Header(default=None, alias="X-Invite-Token"),
) -> dict[str, Any]:
    try:
        return store.close_room(room_id, _participant_token(x_invite_token), body.reason)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


def _monitor_html(room_id: str, host_token: str) -> str:
    config = json.dumps({"roomId": room_id, "hostToken": host_token})
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Room Monitor</title>
  <style>
    :root {{
      --bg: #0b0f14;
      --bg2: #101722;
      --card: rgba(16, 23, 34, 0.82);
      --line: rgba(255,255,255,0.09);
      --text: #edf4ff;
      --muted: #98a8bc;
      --accent: #63f3c8;
      --blue: #6fb2ff;
      --warn: #ffd56f;
      --danger: #ff8d8d;
      --radius: 14px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: ui-rounded, "SF Pro Rounded", "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(70vw 40vh at 0% 0%, rgba(99,243,200,0.07), transparent 65%),
        radial-gradient(70vw 50vh at 100% 0%, rgba(111,178,255,0.08), transparent 68%),
        linear-gradient(180deg, var(--bg), #0d1218 45%, #090d12);
    }}
    .wrap {{ max-width: 1160px; margin: 0 auto; padding: 18px; display: grid; gap: 14px; }}
    .panel {{
      border: 1px solid var(--line);
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      backdrop-filter: blur(10px);
    }}
    .hero {{ padding: 16px; display: grid; gap: 10px; }}
    .hero-top {{ display: flex; gap: 10px; justify-content: space-between; align-items: center; flex-wrap: wrap; }}
    .status {{
      display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px;
      border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,0.03);
      font-size: 12px;
    }}
    .dot {{ width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }}
    .status.active .dot {{ background: var(--accent); }}
    .status.closed .dot {{ background: var(--blue); }}
    .status.paused .dot {{ background: var(--warn); }}
    .status.canceled .dot {{ background: var(--danger); }}
    h1 {{ margin: 0; font-size: clamp(20px, 2vw, 28px); letter-spacing: -0.02em; }}
    .sub {{ color: var(--muted); font-size: 14px; line-height: 1.4; }}
    .stats {{
      display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px;
    }}
    .stat {{ border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: var(--card); }}
    .stat .k {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }}
    .stat .v {{ margin-top: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
    .grid {{ display: grid; grid-template-columns: 310px 1fr; gap: 14px; align-items: start; }}
    .stack {{ display: grid; gap: 14px; position: sticky; top: 14px; }}
    .section-head {{ padding: 12px 12px 0; display: flex; justify-content: space-between; gap: 10px; align-items: center; }}
    .section-head h2 {{ margin: 0; font-size: 14px; }}
    .tiny {{ color: var(--muted); font-size: 12px; }}
    .list {{ padding: 12px; display: grid; gap: 8px; }}
    .person {{
      display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center;
      border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: var(--card);
    }}
    .avatar {{
      width: 28px; height: 28px; border-radius: 9px; display: grid; place-items: center;
      font-size: 12px; font-weight: 700; color: #08111a; background: linear-gradient(135deg, var(--accent), #b8ffee);
    }}
    .person.offline .avatar {{ background: linear-gradient(135deg, #93a3b5, #c0cad5); }}
    .p-name {{ font-size: 13px; }}
    .p-meta {{ color: var(--muted); font-size: 11px; margin-top: 2px; }}
    .badge {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; padding: 4px 8px;
      border-radius: 999px; border: 1px solid var(--line); background: rgba(255,255,255,0.03);
    }}
    .badge.on {{ color: #dffff4; border-color: rgba(99,243,200,0.2); background: rgba(99,243,200,0.06); }}
    .badge.off {{ color: #d4deea; }}
    .field {{
      border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: var(--card); display: grid; gap: 4px;
    }}
    .field .fk {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .08em; }}
    .field .fv {{ font-size: 13px; line-height: 1.35; word-break: break-word; }}
    .field.missing .fv {{ color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .timeline-wrap {{ padding: 10px; display: grid; gap: 10px; }}
    .timeline-head {{ padding: 4px 4px 0; display: flex; justify-content: space-between; gap: 10px; align-items: center; }}
    .timeline {{ display: grid; gap: 8px; max-height: 72vh; overflow: auto; padding: 4px; }}
    .empty {{
      border: 1px dashed var(--line); border-radius: 14px; padding: 18px; text-align: center;
      color: var(--muted); background: rgba(255,255,255,0.02); font-size: 13px;
    }}
    .event {{
      border: 1px solid var(--line); border-radius: 14px; background: var(--card); padding: 10px; display: grid; gap: 8px;
    }}
    .event.chat {{ border-color: rgba(111,178,255,0.18); background: linear-gradient(180deg, rgba(111,178,255,0.06), rgba(111,178,255,0.02)), var(--card); }}
    .e-top {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; font-size: 12px; }}
    .e-left {{ display: inline-flex; gap: 8px; align-items: center; min-width: 0; }}
    .tag {{
      border-radius: 999px; border: 1px solid var(--line); padding: 3px 8px; font-size: 11px;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: rgba(255,255,255,0.03);
      white-space: nowrap;
    }}
    .tag.chat {{ color: #d3e8ff; border-color: rgba(111,178,255,0.2); }}
    .tag.system {{ color: #defff4; border-color: rgba(99,243,200,0.18); }}
    .tag.status {{ color: #ffe9ab; border-color: rgba(255,213,111,0.2); }}
    .sender {{ font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .ts {{ color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; white-space: nowrap; }}
    .e-text {{ white-space: pre-wrap; font-size: 13px; line-height: 1.4; word-break: break-word; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .chip {{
      font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      border-radius: 999px; border: 1px solid var(--line); padding: 4px 8px;
    }}
    @media (max-width: 980px) {{
      .stats {{ grid-template-columns: repeat(2, minmax(120px, 1fr)); }}
      .grid {{ grid-template-columns: 1fr; }}
      .stack {{ position: static; }}
      .timeline {{ max-height: 58vh; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="panel hero">
      <div class="hero-top">
        <div id="status" class="status active"><span class="dot"></span><span id="status-text">connecting</span></div>
        <div class="tiny" id="conn">connecting…</div>
      </div>
      <h1 id="topic">Room Monitor</h1>
      <div class="sub" id="goal">Loading…</div>
      <div class="stats">
        <div class="stat"><div class="k">Room</div><div class="v" id="s-room">-</div></div>
        <div class="stat"><div class="k">Turns</div><div class="v" id="s-turns">0</div></div>
        <div class="stat"><div class="k">Stop</div><div class="v" id="s-stop">none</div></div>
        <div class="stat"><div class="k">Deadline</div><div class="v" id="s-deadline">-</div></div>
      </div>
    </section>
    <div class="grid">
      <aside class="stack">
        <section class="panel">
          <div class="section-head"><h2>Participants</h2><span class="tiny" id="online-count">0 online</span></div>
          <div class="list" id="people"></div>
        </section>
        <section class="panel">
          <div class="section-head"><h2>Required Fields</h2><span class="tiny" id="field-count">0/0</span></div>
          <div class="list" id="fields"></div>
        </section>
      </aside>
      <section class="panel">
        <div class="timeline-wrap">
          <div class="timeline-head">
            <div>Timeline</div>
            <div class="tiny" id="stream-mode">SSE</div>
          </div>
          <div class="empty" id="empty">Waiting for agents to join…</div>
          <div class="timeline" id="timeline"></div>
        </div>
      </section>
    </div>
  </div>
  <script>
    const cfg = {config};
    const state = {{ cursor: 0, seen: new Set(), snapshot: null }};
    const el = {{
      status: document.getElementById('status'),
      statusText: document.getElementById('status-text'),
      conn: document.getElementById('conn'),
      topic: document.getElementById('topic'),
      goal: document.getElementById('goal'),
      sRoom: document.getElementById('s-room'),
      sTurns: document.getElementById('s-turns'),
      sStop: document.getElementById('s-stop'),
      sDeadline: document.getElementById('s-deadline'),
      onlineCount: document.getElementById('online-count'),
      people: document.getElementById('people'),
      fields: document.getElementById('fields'),
      fieldCount: document.getElementById('field-count'),
      timeline: document.getElementById('timeline'),
      empty: document.getElementById('empty'),
      streamMode: document.getElementById('stream-mode'),
    }};

    const esc = (s) => String(s ?? '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    const time = (s) => s ? new Date(s).toLocaleTimeString() : '--';

    function renderRoom(room) {{
      if (!room) return;
      state.snapshot = room;
      el.status.className = `status ${{room.status}}`;
      el.statusText.textContent = room.status;
      el.topic.textContent = room.topic || 'Room';
      el.goal.textContent = room.goal || '';
      el.sRoom.textContent = room.id;
      el.sTurns.textContent = `${{room.turn_count}} / ${{room.turn_limit}}`;
      el.sStop.textContent = room.stop_reason || 'none';
      el.sDeadline.textContent = time(room.deadline_at);

      const participants = room.participants || [];
      const online = participants.filter(p => p.online).length;
      el.onlineCount.textContent = `${{online}} online`;
      el.people.innerHTML = '';
      for (const p of participants) {{
        const name = p.name || 'agent';
        const initials = name.slice(0, 2).toUpperCase();
        const row = document.createElement('div');
        row.className = `person ${{p.online ? 'online' : 'offline'}}`;
        row.innerHTML = `
          <div class="avatar">${{esc(initials)}}</div>
          <div>
            <div class="p-name">${{esc(name)}}</div>
            <div class="p-meta">${{esc(p.client_name || (p.joined ? 'joined' : 'pending join'))}}</div>
          </div>
          <div class="badge ${{p.online ? 'on' : 'off'}}">${{p.online ? 'online' : (p.joined ? 'away' : 'pending')}}</div>
        `;
        el.people.appendChild(row);
      }}

      const req = room.required_fields || [];
      const fields = room.fields || {{}};
      let filled = 0;
      el.fields.innerHTML = '';
      if (!req.length) {{
        const d = document.createElement('div');
        d.className = 'field missing';
        d.innerHTML = `<div class="fk">none</div><div class="fv">No required fields</div>`;
        el.fields.appendChild(d);
      }} else {{
        for (const k of req) {{
          const has = Object.prototype.hasOwnProperty.call(fields, k);
          if (has) filled += 1;
          const d = document.createElement('div');
          d.className = `field ${{has ? 'filled' : 'missing'}}`;
          d.innerHTML = `<div class="fk">${{esc(k)}}</div><div class="fv">${{has ? esc(fields[k].value) : 'missing'}}</div>`;
          el.fields.appendChild(d);
        }}
      }}
      el.fieldCount.textContent = `${{filled}}/${{req.length}}`;
    }}

    function addEvent(evt) {{
      if (!evt || state.seen.has(evt.id)) return;
      state.seen.add(evt.id);
      state.cursor = Math.max(state.cursor, evt.id || 0);
      el.empty.style.display = 'none';

      const row = document.createElement('article');
      const isChat = evt.type === 'relay';
      row.className = `event ${{isChat ? 'chat' : ''}}`;
      let label = evt.type;
      let sender = '';
      let text = '';
      let chips = [];
      let tagClass = isChat ? 'chat' : (evt.type === 'status' ? 'status' : 'system');

      if (evt.type === 'relay') {{
        label = 'chat';
        sender = evt.payload?.from || evt.payload?.message?.sender || '';
        text = evt.payload?.message?.text || '';
        const m = evt.payload?.message || {{}};
        if (m.intent) chips.push(m.intent);
        if (m.fills && Object.keys(m.fills).length) chips.push(`fills:${{Object.keys(m.fills).length}}`);
        if (m.questions && m.questions.length) chips.push(`questions:${{m.questions.length}}`);
      }} else if (evt.type === 'system') {{
        const kind = evt.payload?.kind || 'system';
        label = kind;
        sender = evt.payload?.participant || '';
        text = evt.payload?.reason ? `reason: ${{evt.payload.reason}}` : (evt.payload?.client_name ? `client: ${{evt.payload.client_name}}` : '');
      }} else if (evt.type === 'status') {{
        label = 'status';
        text = `${{evt.payload?.status || ''}}${{evt.payload?.stop_reason && evt.payload.stop_reason !== 'none' ? ` (${{evt.payload.stop_reason}})` : ''}}`;
        chips = [`turns:${{evt.payload?.turn_count ?? '-'}}`, `stall:${{evt.payload?.stall_count ?? '-'}}`];
      }}

      row.innerHTML = `
        <div class="e-top">
          <div class="e-left">
            <span class="tag ${{tagClass}}">${{esc(label)}}</span>
            ${{sender ? `<span class="sender">${{esc(sender)}}</span>` : ''}}
          </div>
          <span class="ts">${{esc(time(evt.ts))}}</span>
        </div>
        ${{text ? `<div class="e-text">${{esc(text)}}</div>` : ''}}
        ${{chips.length ? `<div class="chips">${{chips.map(c => `<span class="chip">${{esc(c)}}</span>`).join('')}}</div>` : ''}}
      `;
      el.timeline.appendChild(row);
      if (el.timeline.childElementCount <= 6 || (el.timeline.scrollTop + el.timeline.clientHeight >= el.timeline.scrollHeight - 120)) {{
        row.scrollIntoView({{ block: 'end', behavior: 'smooth' }});
      }}
    }}

    async function loadHistory() {{
      el.conn.textContent = 'loading history…';
      const url = `/rooms/${{cfg.roomId}}/monitor/events?host_token=${{encodeURIComponent(cfg.hostToken)}}&after=0&limit=500`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
      const data = await res.json();
      renderRoom(data.room);
      for (const evt of (data.events || [])) addEvent(evt);
      state.cursor = data.next_cursor || 0;
      el.conn.textContent = 'live';
    }}

    function connectSSE() {{
      el.streamMode.textContent = 'SSE';
      const es = new EventSource(`/rooms/${{cfg.roomId}}/monitor/stream?host_token=${{encodeURIComponent(cfg.hostToken)}}&after=${{state.cursor}}`);
      es.onopen = () => {{
        el.conn.textContent = 'live';
      }};
      es.onerror = async () => {{
        el.conn.textContent = 'reconnecting…';
      }};
      es.addEventListener('room_event', (e) => {{
        try {{
          const data = JSON.parse(e.data);
          if (data.room) renderRoom(data.room);
          if (data.event) addEvent(data.event);
        }} catch (err) {{
          console.error(err);
        }}
      }});
    }}

    loadHistory().then(connectSSE).catch((err) => {{
      console.error(err);
      el.conn.textContent = 'error';
      el.streamMode.textContent = 'failed';
    }});
  </script>
</body>
</html>"""


@app.get("/rooms/{room_id}/monitor", response_class=HTMLResponse)
def monitor_page(room_id: str, host_token: str = Query(..., min_length=1)) -> HTMLResponse:
    try:
        store.get_room_for_host(room_id, host_token)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc
    return HTMLResponse(_monitor_html(room_id, host_token))


@app.get("/rooms/{room_id}/monitor/events")
def monitor_events(
    room_id: str,
    host_token: str = Query(..., min_length=1),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    try:
        return store.monitor_events(room_id, host_token, after, limit)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/monitor/stream")
async def monitor_stream(
    room_id: str,
    request: Request,
    host_token: str = Query(..., min_length=1),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        store.get_room_for_host(room_id, host_token)
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc

    async def gen() -> Any:
        cursor = after
        while True:
            if await request.is_disconnected():
                break
            try:
                batch = store.monitor_events(room_id, host_token, cursor, 500)
            except Exception as exc:
                yield _sse({"error": str(exc)}, event="error")
                break
            if batch["events"]:
                for item in batch["events"]:
                    cursor = item["id"]
                    yield _sse({"event": item, "room": batch["room"]}, event="room_event", event_id=item["id"])
            else:
                yield b": ping\n\n"
            await asyncio.sleep(0.7)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/rooms/{room_id}/monitor/result")
def monitor_result(room_id: str, host_token: str = Query(..., min_length=1)) -> dict[str, Any]:
    try:
        return {"result": store.monitor_result(room_id, host_token)}
    except Exception as exc:  # pragma: no cover
        raise _map_store_error(exc) from exc
