import { NextRequest, NextResponse } from "next/server";

const BACKEND_BASE_URL =
  process.env.INTERNAL_API_BASE_URL ||
  process.env.API_BASE_URL ||
  "http://127.0.0.1:8000";

const AGENT_API_KEY =
  process.env.AGENT_API_KEY || process.env.NEXT_PUBLIC_AGENT_API_KEY || "";

const AGENT_ID = process.env.AGENT_ID || process.env.NEXT_PUBLIC_AGENT_ID || "";

type RouteContext = {
  params: Promise<{ path: string[] }>;
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function buildUpstreamUrl(path: string[], searchParams: URLSearchParams): URL {
  const requestPath = path.join("/");
  const upstreamPath =
    path[0] === "v1" ? `/api/${requestPath}` : `/${requestPath}`;

  const baseUrl = BACKEND_BASE_URL.endsWith("/")
    ? BACKEND_BASE_URL
    : `${BACKEND_BASE_URL}/`;

  const upstreamUrl = new URL(upstreamPath, baseUrl);

  for (const [key, value] of searchParams.entries()) {
    upstreamUrl.searchParams.append(key, value);
  }

  if (
    path.length === 3 &&
    path[0] === "bounties" &&
    path[2] === "claim" &&
    AGENT_ID &&
    !upstreamUrl.searchParams.has("agent_id")
  ) {
    upstreamUrl.searchParams.set("agent_id", AGENT_ID);
  }

  return upstreamUrl;
}

function isJsonRequest(request: NextRequest): boolean {
  const contentType = request.headers.get("content-type") || "";
  return contentType.includes("application/json");
}

function shouldInjectAgentIdInBody(path: string[]):
  | "agent_id"
  | "reviewer_id"
  | null {
  if (
    path.length === 4 &&
    path[0] === "v1" &&
    path[1] === "bounties" &&
    path[3] === "claim-preparation"
  ) {
    return "agent_id";
  }

  if (
    path.length === 4 &&
    path[0] === "v1" &&
    path[1] === "collaboration" &&
    path[2] === "reviews" &&
    path[3] === "create"
  ) {
    return "agent_id";
  }

  if (
    path.length === 5 &&
    path[0] === "v1" &&
    path[1] === "collaboration" &&
    path[2] === "reviews" &&
    path[4] === "submit"
  ) {
    return "reviewer_id";
  }

  return null;
}

function requiresAgentId(path: string[]): boolean {
  if (path.length === 3 && path[0] === "bounties" && path[2] === "claim") {
    return true;
  }

  if (
    path.length === 5 &&
    path[0] === "v1" &&
    path[1] === "collaboration" &&
    path[2] === "reviews" &&
    path[3] === "reviewer" &&
    path[4] === "me"
  ) {
    return true;
  }

  return shouldInjectAgentIdInBody(path) !== null;
}

function sanitizeRequestHeaders(request: NextRequest): Headers {
  const headers = new Headers(request.headers);

  headers.delete("host");
  headers.delete("connection");
  headers.delete("content-length");

  if (AGENT_API_KEY) {
    headers.set("x-api-key", AGENT_API_KEY);
  }

  return headers;
}

function sanitizeResponseHeaders(upstreamHeaders: Headers): Headers {
  const headers = new Headers(upstreamHeaders);

  headers.delete("connection");
  headers.delete("transfer-encoding");

  return headers;
}

async function proxy(request: NextRequest, context: RouteContext) {
  const { path } = await context.params;

  if (requiresAgentId(path) && !AGENT_ID) {
    return NextResponse.json(
      { detail: "Server AGENT_ID is not configured" },
      { status: 500 }
    );
  }

  const rewrittenPath =
    path.length === 5 &&
    path[0] === "v1" &&
    path[1] === "collaboration" &&
    path[2] === "reviews" &&
    path[3] === "reviewer" &&
    path[4] === "me"
      ? ["v1", "collaboration", "reviews", "reviewer", AGENT_ID]
      : path;

  const upstreamUrl = buildUpstreamUrl(rewrittenPath, request.nextUrl.searchParams);
  const headers = sanitizeRequestHeaders(request);

  const method = request.method.toUpperCase();
  const canHaveBody = !["GET", "HEAD"].includes(method);

  let body: BodyInit | undefined;

  if (canHaveBody) {
    const injectField = shouldInjectAgentIdInBody(path);

    if (injectField && isJsonRequest(request)) {
      const jsonBody = (await request.json().catch(() => ({}))) as Record<
        string,
        unknown
      >;
      jsonBody[injectField] = AGENT_ID;
      body = JSON.stringify(jsonBody);
      headers.set("content-type", "application/json");
    } else {
      body = await request.arrayBuffer();
    }
  }

  try {
    const upstreamResponse = await fetch(upstreamUrl, {
      method,
      headers,
      body,
      redirect: "manual",
      cache: "no-store",
    });

    return new NextResponse(upstreamResponse.body, {
      status: upstreamResponse.status,
      headers: sanitizeResponseHeaders(upstreamResponse.headers),
    });
  } catch (error) {
    return NextResponse.json(
      {
        detail: "Failed to reach upstream API",
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 502 }
    );
  }
}

export async function GET(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function POST(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function PUT(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function DELETE(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function HEAD(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}

export async function OPTIONS(request: NextRequest, context: RouteContext) {
  return proxy(request, context);
}
