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

function envNum(env: Env, primary: keyof Env, legacy: keyof Env, fallback: number): number {
  const raw = env[primary] ?? env[legacy];
  const parsed = Number(String(raw ?? ""));
  if (!Number.isFinite(parsed)) return fallback;
  return parsed;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/healthz") {
      return text("ok");
    }

    const match = route(url.pathname);
    if (!match) return notFound();

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
      return stub.fetch(initReq);
    }

    // Everything else is per-room, forwarded to the room DO.
    const roomId = match.roomId;
    if (!roomId) return notFound();

    const id = env.ROOMS.idFromName(roomId);
    const stub = env.ROOMS.get(id);
    const forwardUrl = new URL(request.url);
    forwardUrl.pathname = match.forwardPath;
    const forwarded = new Request(forwardUrl, request);
    return stub.fetch(forwarded);
  }
};
