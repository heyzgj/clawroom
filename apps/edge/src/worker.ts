import { RoomDurableObject } from "./worker_room";
import { RoomRegistryDurableObject } from "./worker_registry";
import { MissionDurableObject } from "./worker_mission";
import { TeamRegistryDurableObject } from "./worker_teams";
import { AgentInboxDurableObject } from "./worker_inbox";
import { RoomParticipantWorkflow, type RoomParticipantWorkflowParams } from "./workflow_room_participant";
import { badRequest, json, normalizePlatformError, notFound, readJson, route, text, unauthorized } from "./worker_util";

export { RoomDurableObject };
export { RoomRegistryDurableObject };
export { MissionDurableObject };
export { TeamRegistryDurableObject };
export { AgentInboxDurableObject };
export { RoomParticipantWorkflow };

type WorkflowInstance = {
  id: string;
  status(): Promise<unknown>;
  sendEvent(event: { type: string; payload?: unknown }): Promise<void>;
};

type WorkflowBinding<TParams> = {
  create(input: { id?: string; params: TParams }): Promise<WorkflowInstance>;
  get(id: string): Promise<WorkflowInstance>;
};

type Env = {
  ROOMS: DurableObjectNamespace;
  ROOM_REGISTRY: DurableObjectNamespace;
  MISSIONS: DurableObjectNamespace;
  TEAM_REGISTRY: DurableObjectNamespace;
  AGENT_INBOXES: DurableObjectNamespace;
  ROOM_PARTICIPANT_WORKFLOW?: WorkflowBinding<RoomParticipantWorkflowParams>;
  AI?: unknown;
  CLAWROOM_DEFAULT_TURN_LIMIT?: string;
  CLAWROOM_DEFAULT_STALL_LIMIT?: string;
  CLAWROOM_DEFAULT_TIMEOUT_MINUTES?: string;
  CLAWROOM_DEFAULT_TTL_MINUTES?: string;
  ROOMBRIDGE_DEFAULT_TURN_LIMIT?: string;
  ROOMBRIDGE_DEFAULT_STALL_LIMIT?: string;
  ROOMBRIDGE_DEFAULT_TIMEOUT_MINUTES?: string;
  ROOMBRIDGE_DEFAULT_TTL_MINUTES?: string;
  MONITOR_ADMIN_TOKEN?: string;
  CLAWROOM_MONITOR_ADMIN_TOKEN?: string;
};

function buildCorsHeaders(request: Request): Headers {
  const headers = new Headers();
  const origin = request.headers.get("Origin");
  headers.set("access-control-allow-origin", origin || "*");
  headers.set("access-control-allow-methods", "GET,POST,OPTIONS");
  headers.set("access-control-allow-headers", "content-type,authorization,x-monitor-token");
  headers.set("access-control-max-age", "86400");
  headers.set("vary", "Origin");
  return headers;
}

function withCors(request: Request, response: Response): Response {
  const headers = new Headers(response.headers);
  const cors = buildCorsHeaders(request);
  cors.forEach((value, key) => headers.set(key, value));
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

function envNum(env: Env, primary: keyof Env, legacy: keyof Env, fallback: number): number {
  const raw = env[primary] ?? env[legacy];
  const parsed = Number(String(raw ?? ""));
  if (!Number.isFinite(parsed)) return fallback;
  return parsed;
}

function monitorToken(request: Request): string {
  const url = new URL(request.url);
  return (
    request.headers.get("x-monitor-token")
    || request.headers.get("X-Monitor-Token")
    || url.searchParams.get("admin_token")
    || ""
  ).trim();
}

function bearerToken(request: Request): string {
  const value = request.headers.get("authorization") || request.headers.get("Authorization") || "";
  const match = value.match(/^Bearer\s+(.+)$/i);
  return match?.[1]?.trim() || "";
}

function requireMonitorAuth(request: Request, env: Env): Response | null {
  const expected = String(env.MONITOR_ADMIN_TOKEN || env.CLAWROOM_MONITOR_ADMIN_TOKEN || "").trim();
  if (!expected) {
    return json(
      { error: "monitor_not_configured", message: "monitor admin token is not configured" },
      { status: 503 },
    );
  }
  const provided = monitorToken(request);
  if (!provided || provided !== expected) {
    return json({ error: "unauthorized", message: "invalid monitor admin token" }, { status: 401 });
  }
  return null;
}

async function requireAgentInboxAuth(request: Request, env: Env, agentId: string): Promise<Response | null> {
  const token = bearerToken(request);
  if (!token) return unauthorized("missing inbox bearer token");
  const registryId = env.TEAM_REGISTRY.idFromName("global");
  const registry = env.TEAM_REGISTRY.get(registryId);
  const verify = await registry.fetch(new Request(`https://teams/internal/agents/${encodeURIComponent(agentId)}/verify_inbox_token`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  }));
  if (verify.ok) return null;
  try {
    return await verify.json().then((body) => json(body, { status: verify.status }));
  } catch {
    return json({ error: "unauthorized", message: "invalid inbox token" }, { status: verify.status || 401 });
  }
}

function workflowBinding(env: Env): WorkflowBinding<RoomParticipantWorkflowParams> | null {
  return env.ROOM_PARTICIPANT_WORKFLOW ?? null;
}

function isHistoryFallbackPath(pathname: string): boolean {
  return pathname.endsWith("/monitor/result") || pathname.endsWith("/monitor/events") || pathname.endsWith("/monitor/stream");
}

async function isRoomNotFoundResponse(response: Response): Promise<boolean> {
  if (response.status !== 404) return false;
  try {
    const body = await response.clone().json() as Record<string, unknown>;
    return String(body.error || "") === "room_not_found";
  } catch {
    return false;
  }
}

async function fetchRegistryHistoryFallback(
  env: Env,
  roomId: string,
  request: Request,
  forwardPath: string,
): Promise<Response | null> {
  const registryId = env.ROOM_REGISTRY.idFromName("global");
  const registry = env.ROOM_REGISTRY.get(registryId);
  const url = new URL(request.url);
  if (forwardPath.endsWith("/monitor/result")) {
    return await registry.fetch(new Request(`https://registry/monitor/rooms/${encodeURIComponent(roomId)}/result${url.search}`, {
      method: "GET",
      headers: request.headers,
    }));
  }
  if (forwardPath.endsWith("/monitor/events")) {
    return await registry.fetch(new Request(`https://registry/monitor/rooms/${encodeURIComponent(roomId)}/events${url.search}`, {
      method: "GET",
      headers: request.headers,
    }));
  }
  if (forwardPath.endsWith("/monitor/stream")) {
    const eventsRes = await registry.fetch(new Request(`https://registry/monitor/rooms/${encodeURIComponent(roomId)}/events${url.search}`, {
      method: "GET",
      headers: request.headers,
    }));
    if (!eventsRes.ok) return eventsRes;
    const payload = await eventsRes.json() as { room?: unknown; events?: Array<Record<string, unknown>> };
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(": clawroom historical stream\n\n"));
        for (const evt of Array.isArray(payload.events) ? payload.events : []) {
          const eventId = Number(evt.id || 0);
          const type = String(evt.type || "message");
          controller.enqueue(encoder.encode(`id: ${eventId}\n`));
          controller.enqueue(encoder.encode(`event: ${type}\n`));
          controller.enqueue(encoder.encode(`data: ${JSON.stringify(evt)}\n\n`));
        }
        const room = payload.room && typeof payload.room === "object" ? payload.room : {};
        const finalEventId = Array.isArray(payload.events) && payload.events.length
          ? Number(payload.events[payload.events.length - 1]?.id || 0) + 1
          : 1;
        controller.enqueue(encoder.encode(`id: ${finalEventId}\n`));
        controller.enqueue(encoder.encode("event: room_closed\n"));
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(room)}\n\n`));
        controller.close();
      },
    });
    return new Response(stream, {
      status: 200,
      headers: {
        "content-type": "text/event-stream",
        "cache-control": "no-cache, no-transform",
        connection: "keep-alive",
      },
    });
  }
  return null;
}

function roomParticipantWorkflowId(roomId: string, participant: string): string {
  return `roompt_${roomId}_${participant}`;
}

function wantsConversationWorkflow(body: unknown): boolean {
  if (!body || typeof body !== "object") return false;
  const value = (body as Record<string, unknown>).workflow_mode;
  return typeof value === "string" && value.trim().toLowerCase() === "conversation";
}

function workflowErrorResponse(error: unknown): Response {
  const normalized = normalizePlatformError(error);
  if (normalized) return normalized;
  return json(
    {
      error: "workflow_operation_failed",
      message: String((error as Error)?.message || error || "workflow operation failed"),
    },
    { status: 500 },
  );
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: buildCorsHeaders(request) });
    }

    try {
      const url = new URL(request.url);

      if (url.pathname === "/workflows/room-participants" && request.method === "POST") {
        const binding = workflowBinding(env);
        if (!binding) {
          return withCors(request, json({ error: "workflow_not_configured" }, { status: 503 }));
        }
        const body = await readJson(request);
        const roomId = String(body.room_id || "").trim();
        const participant = String(body.participant || "").trim();
        const participantToken = String(body.participant_token || "").trim();
        if (!roomId || !participant || !participantToken) {
          return withCors(request, badRequest("room_id, participant, and participant_token are required"));
        }
        const workflowId = String(body.workflow_id || roomParticipantWorkflowId(roomId, participant));
        const instance = await binding.create({
          id: workflowId,
          params: {
            room_id: roomId,
            participant,
            participant_token: participantToken,
            mode: "conversation",
            room_url: typeof body.room_url === "string" ? body.room_url : undefined,
            model: typeof body.model === "string" ? body.model : undefined,
          },
        });
        if (body.kickoff !== false) {
          await instance.sendEvent({
            type: "room-event",
            payload: {
              payload_json: JSON.stringify({
                kind: "workflow_kickoff",
                room_id: roomId,
                participant,
              }),
            },
          });
        }
        return withCors(request, json({
          workflow_id: instance.id,
          room_id: roomId,
          participant,
          status: await instance.status(),
        }));
      }

      if (url.pathname.startsWith("/workflows/room-participants/")) {
        const binding = workflowBinding(env);
        if (!binding) {
          return withCors(request, json({ error: "workflow_not_configured" }, { status: 503 }));
        }
        const rest = url.pathname.slice("/workflows/room-participants/".length);
        const parts = rest.split("/").filter(Boolean);
        const workflowId = parts[0] || "";
        if (!workflowId) return withCors(request, notFound());
        let instance: WorkflowInstance;
        try {
          instance = await binding.get(workflowId);
        } catch (error) {
          return withCors(request, workflowErrorResponse(error));
        }

        if (parts.length === 1 && request.method === "GET") {
          try {
            return withCors(request, json({
              workflow_id: workflowId,
              status: await instance.status(),
            }));
          } catch (error) {
            return withCors(request, workflowErrorResponse(error));
          }
        }

        if (parts.length === 2 && parts[1] === "events" && request.method === "POST") {
          const body = await readJson(request);
          const eventType = String(body.type || "").trim();
          if (!eventType) {
            return withCors(request, badRequest("event type is required"));
          }
          try {
            await instance.sendEvent({
              type: eventType,
              payload: {
                payload_json: body && Object.prototype.hasOwnProperty.call(body, "payload")
                  ? JSON.stringify(body.payload ?? null)
                  : null,
              },
            });
            return withCors(request, json({ workflow_id: workflowId, accepted: true, event_type: eventType }));
          } catch (error) {
            return withCors(request, workflowErrorResponse(error));
          }
        }
      }

      if (request.method === "GET" && url.pathname === "/healthz") {
        return withCors(request, text("ok"));
      }

      if (
        request.method === "GET"
        && (
          url.pathname === "/monitor/overview"
          || url.pathname === "/monitor/summary"
          || url.pathname === "/monitor/rooms"
          || url.pathname === "/monitor/events"
        )
      ) {
        const authFailure = requireMonitorAuth(request, env);
        if (authFailure) return withCors(request, authFailure);
        const registryId = env.ROOM_REGISTRY.idFromName("global");
        const registry = env.ROOM_REGISTRY.get(registryId);
        const registryReq = new Request(`https://registry${url.pathname}${url.search}`, { method: "GET" });
        const response = await registry.fetch(registryReq);
        return withCors(request, response);
      }

      const agentInboxMatch = url.pathname.match(/^\/agents\/([^/]+)\/inbox$/);
      if (request.method === "GET" && agentInboxMatch) {
        const agentId = decodeURIComponent(agentInboxMatch[1] || "").trim();
        if (!agentId) return withCors(request, notFound());
        const authFailure = await requireAgentInboxAuth(request, env, agentId);
        if (authFailure) return withCors(request, authFailure);
        const inboxId = env.AGENT_INBOXES.idFromName(agentId);
        const inbox = env.AGENT_INBOXES.get(inboxId);
        const forwarded = new Request(new URL(`https://inbox/events${url.search}`), { method: "GET" });
        return withCors(request, await inbox.fetch(forwarded));
      }

      // --- Mission routes: /missions/* → Mission DO ---
      if (url.pathname.startsWith("/missions/") || url.pathname === "/missions") {
        if (url.pathname === "/missions" && request.method === "POST") {
          // Create mission: generate ID, forward to Mission DO
          const body = await readJson(request);
          const missionId = String(body.mission_id || `mission_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`);
          const id = env.MISSIONS.idFromName(missionId);
          const stub = env.MISSIONS.get(id);
          const initReq = new Request("https://mission/init", {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ ...body, mission_id: missionId }),
          });
          return withCors(request, await stub.fetch(initReq));
        }
        if (url.pathname.startsWith("/missions/")) {
          const parts = url.pathname.slice("/missions/".length).split("/");
          const missionId = parts[0];
          if (!missionId) return withCors(request, notFound());
          const id = env.MISSIONS.idFromName(missionId);
          const stub = env.MISSIONS.get(id);
          const suffix = "/" + parts.slice(1).join("/") || "/status";
          const fwdUrl = new URL(`https://mission${suffix === "/" ? "/status" : suffix}`);
          const forwarded = new Request(fwdUrl, request);
          return withCors(request, await stub.fetch(forwarded));
        }
      }

      // --- Team/Agent routes: /teams/*, /agents/* → Team Registry DO (global singleton) ---
      if (url.pathname.startsWith("/teams") || url.pathname.startsWith("/assignments")) {
        const registryId = env.TEAM_REGISTRY.idFromName("global");
        const stub = env.TEAM_REGISTRY.get(registryId);
        const forwarded = new Request(new URL(`https://teams${url.pathname}${url.search}`), request);
        return withCors(request, await stub.fetch(forwarded));
      }
      // GET /agents — operator-only in Phase 1 (admin_token required)
      if (request.method === "GET" && url.pathname === "/agents") {
        const authFailure = requireMonitorAuth(request, env);
        if (authFailure) return withCors(request, authFailure);
        const registryId = env.TEAM_REGISTRY.idFromName("global");
        const stub = env.TEAM_REGISTRY.get(registryId);
        const forwarded = new Request(new URL(`https://teams${url.pathname}${url.search}`), request);
        return withCors(request, await stub.fetch(forwarded));
      }
      if (url.pathname.startsWith("/agents")) {
        const registryId = env.TEAM_REGISTRY.idFromName("global");
        const stub = env.TEAM_REGISTRY.get(registryId);
        const forwarded = new Request(new URL(`https://teams${url.pathname}${url.search}`), request);
        return withCors(request, await stub.fetch(forwarded));
      }

      const match = route(url.pathname);
      if (!match) return withCors(request, notFound());

      // Top-level create uses a random room id and forwards to that room DO to initialize state.
      if (match.kind === "rooms" && match.roomId === null && request.method === "POST") {
        const body = await readJson(request);
        const roomId = `room_${crypto.randomUUID().replace(/-/g, "").slice(0, 12)}`;
        const id = env.ROOMS.idFromName(roomId);
        const stub = env.ROOMS.get(id);
        const initReq = new Request("https://room/init", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            room_id: roomId,
            create: body,
            defaults: {
              turn_limit: envNum(env, "CLAWROOM_DEFAULT_TURN_LIMIT", "ROOMBRIDGE_DEFAULT_TURN_LIMIT", 12),
              stall_limit: envNum(env, "CLAWROOM_DEFAULT_STALL_LIMIT", "ROOMBRIDGE_DEFAULT_STALL_LIMIT", 3),
              timeout_minutes: envNum(env, "CLAWROOM_DEFAULT_TIMEOUT_MINUTES", "ROOMBRIDGE_DEFAULT_TIMEOUT_MINUTES", 20),
              ttl_minutes: envNum(env, "CLAWROOM_DEFAULT_TTL_MINUTES", "ROOMBRIDGE_DEFAULT_TTL_MINUTES", 60)
            }
          })
        });
        const response = await stub.fetch(initReq);
        if (!response.ok) return withCors(request, response);

        let responseBody: Record<string, unknown> | null = null;
        try {
          responseBody = await response.clone().json() as Record<string, unknown>;
        } catch {
          responseBody = null;
        }

        if (responseBody) {
          const participants = Array.isArray(body.participants)
            ? body.participants.map((value: unknown) => String(value || "").trim()).filter(Boolean).slice(0, 8)
            : [];
          const createdByAgentId = String(body.created_by_agent_id || "").trim();
          const participantOwnerContexts = body.participant_owner_contexts && typeof body.participant_owner_contexts === "object"
            ? body.participant_owner_contexts as Record<string, unknown>
            : {};
          const invites = responseBody.invites && typeof responseBody.invites === "object"
            ? responseBody.invites as Record<string, unknown>
            : {};
          const joinLinks = responseBody.join_links && typeof responseBody.join_links === "object"
            ? responseBody.join_links as Record<string, unknown>
            : {};
          const inviteResults: Record<string, string> = {};
          const participantRuntimeHints: Record<string, { runtime: string; managed_runnerd_url: string }> = {};
          if (participants.length > 0) {
            const teamRegistryId = env.TEAM_REGISTRY.idFromName("global");
            const teamRegistry = env.TEAM_REGISTRY.get(teamRegistryId);
            for (const participant of participants) {
              if (participant === createdByAgentId) {
                inviteResults[participant] = "creator_direct";
                continue;
              }
              const lookup = await teamRegistry.fetch(new Request(`https://teams/internal/agents/${encodeURIComponent(participant)}`, {
                method: "GET",
              }));
              if (!lookup.ok) {
                inviteResults[participant] = "agent_not_registered";
                continue;
              }
              const lookupBody = await lookup.json().catch(() => null) as { agent?: Record<string, unknown> } | null;
              const lookedUpAgent = lookupBody && lookupBody.agent && typeof lookupBody.agent === "object"
                ? lookupBody.agent
                : {};
              const managedRunnerdUrl = typeof lookedUpAgent.managed_runnerd_url === "string"
                ? String(lookedUpAgent.managed_runnerd_url || "").trim()
                : "";
              const targetRuntime = typeof lookedUpAgent.runtime === "string"
                ? String(lookedUpAgent.runtime || "").trim()
                : "";
              if (targetRuntime || managedRunnerdUrl) {
                participantRuntimeHints[participant] = {
                  runtime: targetRuntime,
                  managed_runnerd_url: managedRunnerdUrl,
                };
              }
              const inviteToken = typeof invites[participant] === "string" ? String(invites[participant]) : "";
              const rawJoinLink = typeof joinLinks[participant] === "string" ? String(joinLinks[participant]) : "";
              const joinLink = rawJoinLink.startsWith("/")
                ? `${url.origin}${rawJoinLink}`
                : rawJoinLink;
              if (!inviteToken || !joinLink) {
                inviteResults[participant] = "invite_not_available";
                continue;
              }
              try {
                const inboxId = env.AGENT_INBOXES.idFromName(participant);
                const inbox = env.AGENT_INBOXES.get(inboxId);
                const inboxResponse = await inbox.fetch(new Request("https://inbox/events", {
                  method: "POST",
                  headers: { "content-type": "application/json" },
                  body: JSON.stringify({
                    type: "room_invite",
                    payload: {
                      room_id: roomId,
                      participant,
                      invite_token: inviteToken,
                      join_link: joinLink,
                      topic: String(body.topic || ""),
                      goal: String(body.goal || ""),
                      required_fields: Array.isArray(body.required_fields) ? body.required_fields : [],
                      runtime: targetRuntime,
                      managed_runnerd_url: managedRunnerdUrl,
                      workflow_mode: "conversation",
                      owner_context: typeof participantOwnerContexts[participant] === "string"
                        ? String(participantOwnerContexts[participant] || "").trim().slice(0, 4000)
                        : "",
                      invited_by: createdByAgentId,
                      created_at_ms: Date.now(),
                    },
                  }),
                }));
                inviteResults[participant] = inboxResponse.ok ? "invite_written" : "invite_failed";
              } catch {
                inviteResults[participant] = "invite_failed";
              }
            }
            responseBody.invite_results = inviteResults;
            if (Object.keys(participantRuntimeHints).length > 0) {
              responseBody.participant_runtime_hints = participantRuntimeHints;
            }
          }
          return withCors(request, json(responseBody, { status: response.status }));
        }

        return withCors(request, response);
      }

      // Everything else is per-room, forwarded to the room DO.
      const roomId = match.roomId;
      if (!roomId) return withCors(request, notFound());

      const id = env.ROOMS.idFromName(roomId);
      const stub = env.ROOMS.get(id);
      const forwardUrl = new URL(request.url);
      forwardUrl.pathname = match.forwardPath;

      // --- Auto-register agents on join (Phase 1: passive directory seeding) ---
      const isJoin = request.method === "POST" && match.forwardPath.endsWith("/join");
      let joinBody: any = null;

      if (isJoin) {
        // Read body, clone for forwarding (body can only be read once)
        const bodyText = await request.text();
        try { joinBody = JSON.parse(bodyText); } catch { joinBody = {}; }
        const forwarded = new Request(forwardUrl, {
          method: request.method,
          headers: request.headers,
          body: bodyText,
        });
        const response = await stub.fetch(forwarded);
        if (!response.ok) {
          return withCors(request, response);
        }

        let responseBody: Record<string, unknown> | null = null;
        try {
          responseBody = await response.clone().json() as Record<string, unknown>;
        } catch {
          responseBody = null;
        }

        // If join succeeded and agent_id was provided, register in TeamRegistryDO
        if (joinBody?.agent_id && responseBody) {
          try {
            const agentId = String(joinBody.agent_id).slice(0, 200);
            const agentRuntime = String(joinBody.runtime || "").slice(0, 60);
            const displayName = String(joinBody.display_name || joinBody.client_name || agentId).slice(0, 120);

            const teamRegistryId = env.TEAM_REGISTRY.idFromName("global");
            const teamRegistry = env.TEAM_REGISTRY.get(teamRegistryId);
            const registrationResponse = await teamRegistry.fetch(new Request("https://teams/agents", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                agent_id: agentId,
                name: displayName,
                runtime: agentRuntime,
                capabilities: [],
                team_id: "",
              }),
            }));

            if (registrationResponse.ok) {
              responseBody.agent_registered = true;
            }
          } catch {
            // Registration failed silently — don't break the join flow
          }
        }

        if (responseBody && wantsConversationWorkflow(joinBody)) {
          const binding = workflowBinding(env);
          if (binding) {
            try {
              const participant = typeof responseBody.participant === "string" ? responseBody.participant : "";
              const participantToken = typeof responseBody.participant_token === "string" ? responseBody.participant_token : "";
              const workflowRoomId =
                typeof responseBody.room === "object"
                && responseBody.room
                && typeof (responseBody.room as Record<string, unknown>).id === "string"
                  ? String((responseBody.room as Record<string, unknown>).id)
                  : roomId;
              if (participant && participantToken && workflowRoomId) {
                const workflowId = roomParticipantWorkflowId(workflowRoomId, participant);
                const workflow = await binding.create({
                  id: workflowId,
                  params: {
                    room_id: workflowRoomId,
                    participant,
                    participant_token: participantToken,
                    mode: "conversation",
                    room_url: typeof joinBody?.room_url === "string" ? joinBody.room_url : undefined,
                    model: typeof joinBody?.workflow_model === "string" ? joinBody.workflow_model : undefined,
                  },
                });
                await workflow.sendEvent({
                  type: "room-event",
                  payload: {
                    payload_json: JSON.stringify({
                      kind: "workflow_kickoff",
                      room_id: workflowRoomId,
                      participant,
                    }),
                  },
                });
                responseBody.workflow_started = true;
                responseBody.workflow_id = workflow.id;
                responseBody.workflow_mode = "conversation";
              }
            } catch {
              // Workflow auto-start is best-effort for now.
            }
          }
        }

        return responseBody
          ? withCors(request, json(responseBody, { status: response.status }))
          : withCors(request, response);
      }

      const isJoinGateResolve =
        request.method === "POST"
        && /\/join_gates\/[^/]+\/resolve$/.test(match.forwardPath);

      if (isJoinGateResolve) {
        const bodyText = await request.text();
        let resolveBody: any = {};
        try { resolveBody = JSON.parse(bodyText); } catch { resolveBody = {}; }
        const forwarded = new Request(forwardUrl, {
          method: request.method,
          headers: request.headers,
          body: bodyText,
        });
        const response = await stub.fetch(forwarded);
        if (!response.ok) {
          return withCors(request, response);
        }

        let responseBody: Record<string, unknown> | null = null;
        try {
          responseBody = await response.clone().json() as Record<string, unknown>;
        } catch {
          responseBody = null;
        }

        if (responseBody && responseBody.joined === true && responseBody.workflow_mode === "conversation") {
          const binding = workflowBinding(env);
          if (binding) {
            try {
              const participant = typeof responseBody.participant === "string" ? responseBody.participant : "";
              const participantToken = typeof responseBody.participant_token === "string" ? responseBody.participant_token : "";
              const workflowRoomId =
                typeof responseBody.room === "object"
                && responseBody.room
                && typeof (responseBody.room as Record<string, unknown>).id === "string"
                  ? String((responseBody.room as Record<string, unknown>).id)
                  : roomId;
              if (participant && participantToken && workflowRoomId) {
                const workflowId = roomParticipantWorkflowId(workflowRoomId, participant);
                const workflow = await binding.create({
                  id: workflowId,
                  params: {
                    room_id: workflowRoomId,
                    participant,
                    participant_token: participantToken,
                    mode: "conversation",
                    room_url: typeof resolveBody?.room_url === "string" ? resolveBody.room_url : undefined,
                    model: typeof responseBody?.workflow_model === "string" ? responseBody.workflow_model : undefined,
                  },
                });
                await workflow.sendEvent({
                  type: "room-event",
                  payload: {
                    payload_json: JSON.stringify({
                      kind: "workflow_kickoff",
                      room_id: workflowRoomId,
                      participant,
                    }),
                  },
                });
                responseBody.workflow_started = true;
                responseBody.workflow_id = workflow.id;
              }
            } catch {
              // Workflow auto-start is best-effort for now.
            }
          }
        }

        return responseBody
          ? withCors(request, json(responseBody, { status: response.status }))
          : withCors(request, response);
      }

      if (request.method === "POST" && match.forwardPath.endsWith("/messages")) {
        const forwarded = new Request(forwardUrl, request);
        const response = await stub.fetch(forwarded);
        if (!response.ok) {
          return withCors(request, response);
        }

        const binding = workflowBinding(env);
        if (binding) {
          try {
            const responseBody = await response.clone().json() as Record<string, unknown>;
            const room = (responseBody.room && typeof responseBody.room === "object")
              ? responseBody.room as Record<string, unknown>
              : null;
            const roomIdForEvent = typeof room?.id === "string" ? room.id : roomId;
            const participants = Array.isArray(room?.participants) ? room?.participants as Array<Record<string, unknown>> : [];

            for (const participantInfo of participants) {
              const participant = typeof participantInfo?.name === "string" ? participantInfo.name : "";
              if (!participant) continue;
              const instance = await binding.get(roomParticipantWorkflowId(roomIdForEvent, participant));
              await instance.sendEvent({
                type: "room-event",
                payload: {
                  payload_json: JSON.stringify({
                    kind: "room_message",
                    room_id: roomIdForEvent,
                    participant,
                  }),
                },
              });
            }
          } catch {
            // Workflow event fanout is best-effort for now.
          }
        }

        return withCors(request, response);
      }

      const forwarded = new Request(forwardUrl, request);
      const response = await stub.fetch(forwarded);
      if (isHistoryFallbackPath(match.forwardPath) && await isRoomNotFoundResponse(response)) {
        const fallback = await fetchRegistryHistoryFallback(env, roomId, request, match.forwardPath);
        if (fallback) return withCors(request, fallback);
      }
      return withCors(request, response);
    } catch (error) {
      const normalized = normalizePlatformError(error);
      if (normalized) return withCors(request, normalized);
      if (error instanceof Response) return withCors(request, error);
      throw error;
    }
  }
};
