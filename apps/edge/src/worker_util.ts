type RouteMatch =
  | { kind: "rooms"; roomId: null; forwardPath: string }
  | { kind: "rooms"; roomId: string; forwardPath: string };

export function route(pathname: string): RouteMatch | null {
  if (pathname === "/rooms") return { kind: "rooms", roomId: null, forwardPath: "/rooms" };
  if (pathname.startsWith("/rooms/")) {
    const rest = pathname.slice("/rooms/".length);
    const roomId = rest.split("/")[0];
    if (!roomId) return null;
    const suffix = rest.slice(roomId.length);
    const forwardPath = `/rooms/${roomId}${suffix}`;
    return { kind: "rooms", roomId, forwardPath };
  }
  // /join/:room_id → forward to /rooms/:room_id/join_info (convenience route)
  if (pathname.startsWith("/join/")) {
    const roomId = pathname.slice("/join/".length).split("/")[0];
    if (!roomId) return null;
    return { kind: "rooms", roomId, forwardPath: `/rooms/${roomId}/join_info` };
  }
  return null;
}

export async function readJson(request: Request): Promise<any> {
  const text = await request.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    throw new Response(JSON.stringify({ error: "invalid_json" }), {
      status: 400,
      headers: { "content-type": "application/json" }
    });
  }
}

export function json(data: any, init?: ResponseInit): Response {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers || {}) }
  });
}

export function text(body: string, init?: ResponseInit): Response {
  return new Response(body, { ...init, headers: { "content-type": "text/plain", ...(init?.headers || {}) } });
}

export function notFound(): Response {
  return json({ error: "not_found" }, { status: 404 });
}

export function badRequest(message: string, extra?: Record<string, any>): Response {
  return json({ error: "bad_request", message, ...(extra || {}) }, { status: 400 });
}

export function unauthorized(message: string): Response {
  return json({ error: "unauthorized", message }, { status: 401 });
}

export function conflict(message: string): Response {
  return json({ error: "conflict", message }, { status: 409 });
}

