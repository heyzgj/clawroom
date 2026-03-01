import { RoomDurableObject } from "./worker_room";
import { json, notFound, readJson, route, text } from "./worker_util";

export { RoomDurableObject };

type Env = {
  ROOMS: DurableObjectNamespace;
  CLAWROOM_DEFAULT_TURN_LIMIT?: string;
  CLAWROOM_DEFAULT_STALL_LIMIT?: string;
  CLAWROOM_DEFAULT_TIMEOUT_MINUTES?: string;
  CLAWROOM_DEFAULT_TTL_MINUTES?: string;
  ROOMBRIDGE_DEFAULT_TURN_LIMIT?: string;
  ROOMBRIDGE_DEFAULT_STALL_LIMIT?: string;
  ROOMBRIDGE_DEFAULT_TIMEOUT_MINUTES?: string;
  ROOMBRIDGE_DEFAULT_TTL_MINUTES?: string;
};

function buildCorsHeaders(request: Request): Headers {
  const headers = new Headers();
  const origin = request.headers.get("Origin");
  headers.set("access-control-allow-origin", origin || "*");
  headers.set("access-control-allow-methods", "GET,POST,OPTIONS");
  headers.set("access-control-allow-headers", "content-type,authorization");
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
      const forwarded = new Request(forwardUrl, request);
      const response = await stub.fetch(forwarded);
      return withCors(request, response);
    } catch (error) {
      if (error instanceof Response) return withCors(request, error);
      throw error;
    }
  }
};
