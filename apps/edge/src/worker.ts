import { RoomDurableObject } from "./worker_room";
import { RoomRegistryDurableObject } from "./worker_registry";
import { MissionDurableObject } from "./worker_mission";
import { TeamRegistryDurableObject } from "./worker_teams";
import { json, normalizePlatformError, notFound, readJson, route, text } from "./worker_util";

export { RoomDurableObject };
export { RoomRegistryDurableObject };
export { MissionDurableObject };
export { TeamRegistryDurableObject };

type Env = {
  ROOMS: DurableObjectNamespace;
  ROOM_REGISTRY: DurableObjectNamespace;
  MISSIONS: DurableObjectNamespace;
  TEAM_REGISTRY: DurableObjectNamespace;
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

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: buildCorsHeaders(request) });
    }

    try {
      const url = new URL(request.url);

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
      if (url.pathname.startsWith("/agents")) {
        const authFailure = requireMonitorAuth(request, env);
        if (authFailure) return withCors(request, authFailure);
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

        // If join succeeded and agent_id was provided, register in TeamRegistryDO
        if (response.ok && joinBody?.agent_id) {
          try {
            const agentId = String(joinBody.agent_id).slice(0, 200);
            const agentRuntime = String(joinBody.runtime || "").slice(0, 60);
            const displayName = String(joinBody.display_name || joinBody.client_name || agentId).slice(0, 120);

            const teamRegistryId = env.TEAM_REGISTRY.idFromName("global");
            const teamRegistry = env.TEAM_REGISTRY.get(teamRegistryId);
            await teamRegistry.fetch(new Request("https://teams/agents", {
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

            // Merge agent_registered: true into the response
            const responseBody = await response.json() as Record<string, unknown>;
            responseBody.agent_registered = true;
            return withCors(request, json(responseBody, { status: response.status }));
          } catch {
            // Registration failed silently — don't break the join flow
            // Return original response without agent_registered
          }
        }

        return withCors(request, response);
      }

      const forwarded = new Request(forwardUrl, request);
      const response = await stub.fetch(forwarded);
      return withCors(request, response);
    } catch (error) {
      const normalized = normalizePlatformError(error);
      if (normalized) return withCors(request, normalized);
      if (error instanceof Response) return withCors(request, error);
      throw error;
    }
  }
};
