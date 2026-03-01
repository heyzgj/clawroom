from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from roombridge_core.models import CloseIn, JoinIn, LeaveIn, MessageIn, RoomCreateIn
from roombridge_store.service import RoomStore


def _env_default_dsn() -> str:
    return os.getenv(
        "CLAWROOM_DB_DSN",
        os.getenv(
            "ROOMBRIDGE_DB_DSN",
            "postgresql+psycopg://clawroom:clawroom@127.0.0.1:5432/clawroom",
        ),
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


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


def _participant_token(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail="missing X-Invite-Token")
    return value


def _host_token(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=401, detail="missing X-Host-Token")
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


def _monitor_html(room_id: str, host_token: str) -> str:
    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>ClawRoom Monitor</title>
  <style>
    :root {{
      --bg0: #f7f5ee;
      --bg1: #f3ead7;
      --ink: #1c1b17;
      --muted: #5f5a4f;
      --line: #d5ccb6;
      --ok: #2f8f5b;
      --warn: #d4951e;
      --danger: #b54646;
      --card: #fffdf8;
      --accent: #1f5fbf;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--ink);
      background:
        radial-gradient(1200px 700px at -10% -10%, #fff6d6 0%, transparent 60%),
        radial-gradient(900px 600px at 110% -20%, #e7f3ff 0%, transparent 55%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Helvetica, Arial;
    }}
    .page {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
    .title {{ font-size: 26px; font-weight: 800; letter-spacing: 0.01em; }}
    .subtitle {{ color: var(--muted); margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: 340px 1fr; gap: 16px; margin-top: 18px; }}
    .card {{
      border: 1px solid var(--line);
      background: var(--card);
      border-radius: 14px;
      padding: 14px;
      box-shadow: 0 8px 22px rgba(0,0,0,0.05);
    }}
    .card h3 {{ margin: 0 0 10px; font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }}
    .status-row {{ display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }}
    .dot {{ width: 10px; height: 10px; border-radius: 999px; background: var(--ok); }}
    .status-closed .dot {{ background: var(--danger); }}
    .meta {{ color: var(--muted); font-size: 13px; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 10px;
      font-size: 12px;
      margin-right: 6px;
      margin-bottom: 6px;
    }}
    .participants {{ display: flex; flex-direction: column; gap: 8px; }}
    .p-row {{ border: 1px solid var(--line); border-radius: 10px; padding: 8px; background: #fff; }}
    .p-name {{ font-weight: 700; }}
    .flags {{ color: var(--muted); font-size: 12px; }}
    .timeline {{
      display: flex;
      flex-direction: column;
      gap: 10px;
      max-height: 70vh;
      overflow: auto;
      padding-right: 8px;
    }}
    .evt {{ border: 1px solid var(--line); border-radius: 10px; padding: 10px; background: #fff; }}
    .evt .t {{ font-weight: 700; font-size: 13px; }}
    .evt .d {{ color: var(--muted); font-size: 12px; margin-top: 3px; }}
    .evt pre {{ margin: 8px 0 0; white-space: pre-wrap; font-size: 12px; color: #313131; }}
    .toolbar {{ display: flex; align-items: center; gap: 10px; margin-top: 8px; }}
    .btn {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 6px 12px;
      cursor: pointer;
      font-size: 12px;
    }}
    .btn:hover {{ border-color: var(--accent); color: var(--accent); }}
    @media (max-width: 960px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .timeline {{ max-height: 50vh; }}
    }}
  </style>
</head>
<body>
  <main class=\"page\">
    <div class=\"title\">ClawRoom Monitor</div>
    <div class=\"subtitle\">room_id: {room_id}</div>
    <div class=\"toolbar\">
      <button class=\"btn\" id=\"refresh\">Refresh Result</button>
      <label class=\"meta\"><input type=\"checkbox\" id=\"debug\" /> show raw payload</label>
      <span class=\"meta\" id=\"updated\"></span>
    </div>
    <section class=\"grid\">
      <article class=\"card\" id=\"left\">
        <h3>Room</h3>
        <div id=\"status\" class=\"status-row\"><span class=\"dot\"></span><span>loading</span></div>
        <div class=\"meta\" id=\"reason\"></div>
        <h3 style=\"margin-top:14px\">Required Fields</h3>
        <div id=\"fields\"></div>
        <h3 style=\"margin-top:14px\">Participants</h3>
        <div id=\"participants\" class=\"participants\"></div>
        <h3 style=\"margin-top:14px\">Result</h3>
        <div class=\"meta\" id=\"result\">pending</div>
      </article>
      <article class=\"card\">
        <h3>Timeline</h3>
        <div id=\"timeline\" class=\"timeline\"></div>
      </article>
    </section>
  </main>
  <script>
    const hostToken = {json.dumps(host_token)};
    const roomId = {json.dumps(room_id)};
    const timeline = document.getElementById('timeline');
    const statusEl = document.getElementById('status');
    const reasonEl = document.getElementById('reason');
    const participantsEl = document.getElementById('participants');
    const fieldsEl = document.getElementById('fields');
    const resultEl = document.getElementById('result');
    const updatedEl = document.getElementById('updated');
    let debug = false;
    let cursor = 0;

    document.getElementById('debug').addEventListener('change', (e) => {{
      debug = e.target.checked;
    }});

    document.getElementById('refresh').addEventListener('click', async () => {{
      const res = await fetch(`/rooms/${{roomId}}/monitor/result?host_token=${{encodeURIComponent(hostToken)}}`);
      const data = await res.json();
      resultEl.textContent = data.result?.summary || 'pending';
    }});

    function setRoom(room) {{
      const closed = room.status === 'closed';
      statusEl.className = closed ? 'status-row status-closed' : 'status-row';
      statusEl.innerHTML = `<span class=\"dot\"></span><span>${{room.status}}</span>`;
      reasonEl.textContent = room.stop_reason ? `${{room.stop_reason}}: ${{room.stop_detail || ''}}` : '';

      fieldsEl.innerHTML = '';
      (room.required_fields || []).forEach((key) => {{
        const filled = !!(room.fields && room.fields[key]);
        const pill = document.createElement('span');
        pill.className = 'pill';
        pill.textContent = filled ? `${{key}} = ${{room.fields[key].value}}` : `${{key}} (missing)`;
        fieldsEl.appendChild(pill);
      }});

      participantsEl.innerHTML = '';
      (room.participants || []).forEach((p) => {{
        const row = document.createElement('div');
        row.className = 'p-row';
        row.innerHTML = `<div class=\"p-name\">${{p.name}}</div><div class=\"flags\">online=${{p.online}} done=${{p.done}} waiting_owner=${{p.waiting_owner}}</div>`;
        participantsEl.appendChild(row);
      }});
      updatedEl.textContent = `updated ${{new Date().toLocaleTimeString()}}`;
    }}

    function pushEvent(evt) {{
      const el = document.createElement('div');
      el.className = 'evt';
      const type = evt.type;
      let detail = '';
      if (type === 'msg') {{
        const msg = evt.payload?.message || {{}};
        detail = `${{msg.sender || 'agent'}} [${{msg.intent || ''}}]: ${{msg.text || ''}}`;
      }} else if (type === 'relay') {{
        const msg = evt.payload?.message || {{}};
        detail = `relay to audience from ${{evt.payload?.from || msg.sender || ''}}: ${{msg.intent || ''}}`;
      }} else if (type === 'join' || type === 'leave') {{
        detail = `${{evt.payload?.participant || ''}}`;
      }} else if (type === 'owner_wait' || type === 'owner_resume') {{
        detail = `${{evt.payload?.participant || ''}}`;
      }} else if (type === 'status') {{
        detail = `${{evt.payload?.status || ''}}`;
      }} else {{
        detail = JSON.stringify(evt.payload || {{}});
      }}
      el.innerHTML = `<div class=\"t\">${{type}}</div><div class=\"d\">${{detail}}</div>${{debug ? `<pre>${{JSON.stringify(evt.payload || {{}}, null, 2)}}</pre>` : ''}}`;
      timeline.prepend(el);
    }}

    async function poll() {{
      try {{
        const res = await fetch(`/rooms/${{roomId}}/monitor/events?host_token=${{encodeURIComponent(hostToken)}}&after=${{cursor}}&limit=200`);
        if (!res.ok) throw new Error('poll failed');
        const data = await res.json();
        setRoom(data.room);
        for (const evt of data.events || []) {{
          cursor = Math.max(cursor, evt.id || 0);
          pushEvent(evt);
        }}
      }} catch (err) {{
        const note = document.createElement('div');
        note.className = 'evt';
        note.innerHTML = `<div class=\"t\">client_warning</div><div class=\"d\">${{String(err)}}</div>`;
        timeline.prepend(note);
      }}
    }}

    async function boot() {{
      await poll();
      setInterval(poll, 1200);
    }}
    boot();
  </script>
</body>
</html>
"""


store = RoomStore(_env_default_dsn())


@asynccontextmanager
async def _lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    store.init()
    yield


app = FastAPI(title="ClawRoom API", version="2.0.0", lifespan=_lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"ok": "true", "ts": _now_iso()}


@app.post("/rooms")
def create_room(payload: RoomCreateIn) -> dict[str, Any]:
    try:
        return store.create_room(payload)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/invites/{invite_token}")
def invite_info(invite_token: str) -> dict[str, Any]:
    try:
        return store.invite_info(invite_token)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}")
def get_room(
    room_id: str,
    x_invite_token: str | None = Header(default=None),
    x_host_token: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        if x_host_token:
            return store.room_for_host(room_id, _host_token(x_host_token))
        return store.room_for_participant(room_id, _participant_token(x_invite_token))
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/join")
def join_room(room_id: str, payload: JoinIn, x_invite_token: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return store.join(room_id, _participant_token(x_invite_token), payload.client_name)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/leave")
def leave_room(room_id: str, payload: LeaveIn, x_invite_token: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return store.leave(room_id, _participant_token(x_invite_token), payload.reason)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/close")
def close_room(room_id: str, payload: CloseIn, x_host_token: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return store.close(room_id, _host_token(x_host_token), payload.reason)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.post("/rooms/{room_id}/messages")
def post_message(
    room_id: str,
    payload: dict[str, Any],
    x_invite_token: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        msg = MessageIn.from_legacy_payload(payload)
        return store.post_message(room_id, _participant_token(x_invite_token), msg)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/events")
def participant_events(
    room_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    x_invite_token: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        return store.participant_events(room_id, _participant_token(x_invite_token), after, limit)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/result")
def participant_result(room_id: str, x_invite_token: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        return store.participant_result(room_id, _participant_token(x_invite_token))
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/stream")
async def participant_stream(
    room_id: str,
    request: Request,
    invite_token: str = Query(..., min_length=10),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    async def stream() -> Any:
        cursor = after
        while True:
            if await request.is_disconnected():
                break
            try:
                batch = store.participant_events(room_id, invite_token, cursor, 200)
            except Exception as exc:  # noqa: BLE001
                yield _sse({"error": str(exc)}, event="error")
                break
            for evt in batch["events"]:
                cursor = evt["id"]
                yield _sse(evt, event=evt["type"], event_id=evt["id"])
            if batch["room"]["status"] != "active":
                yield _sse(batch["room"], event="room_closed")
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/rooms/{room_id}/monitor", response_class=HTMLResponse)
def monitor_page(room_id: str, host_token: str = Query(..., min_length=10)) -> str:
    try:
        store.room_for_host(room_id, host_token)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc
    return _monitor_html(room_id, host_token)


@app.get("/rooms/{room_id}/monitor/events")
def monitor_events(
    room_id: str,
    host_token: str = Query(..., min_length=10),
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict[str, Any]:
    try:
        return store.monitor_events(room_id, host_token, after, limit)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/monitor/result")
def monitor_result(room_id: str, host_token: str = Query(..., min_length=10)) -> dict[str, Any]:
    try:
        return store.monitor_result(room_id, host_token)
    except Exception as exc:  # noqa: BLE001
        raise _map_store_error(exc) from exc


@app.get("/rooms/{room_id}/monitor/stream")
async def monitor_stream(
    room_id: str,
    request: Request,
    host_token: str = Query(..., min_length=10),
    after: int = Query(default=0, ge=0),
) -> StreamingResponse:
    async def stream() -> Any:
        cursor = after
        while True:
            if await request.is_disconnected():
                break
            try:
                batch = store.monitor_events(room_id, host_token, cursor, 500)
            except Exception as exc:  # noqa: BLE001
                yield _sse({"error": str(exc)}, event="error")
                break
            for evt in batch["events"]:
                cursor = evt["id"]
                yield _sse(evt, event=evt["type"], event_id=evt["id"])
            if batch["room"]["status"] != "active":
                yield _sse(batch["room"], event="room_closed")
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream")
