import { McpServer } from "@modelcontextprotocol/server";
import { createMcpHandler } from "agents/mcp/server";
import { z } from "zod";

interface Env {
  ORIGIN_BASE_URL: string;
  ORIGIN_API_TOKEN: string;
  CF_ACCESS_CLIENT_ID: string;
  CF_ACCESS_CLIENT_SECRET: string;
  MAX_WINDOW_DAYS: string;
}

const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/).describe("ISO date (YYYY-MM-DD)");
const optionalCsv = z.string().max(500).optional();

function assertWindow(start?: string, end?: string, maxDays = 90) {
  if (!start || !end) return;
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs > endMs) {
    throw new Error("Invalid date window: start must be before or equal to end");
  }
  const days = Math.floor((endMs - startMs) / 86_400_000) + 1;
  if (days > maxDays) throw new Error(`Date window cannot exceed ${maxDays} days`);
}

async function originJson(env: Env, path: string, query?: Record<string, string | undefined>) {
  const url = new URL(path, env.ORIGIN_BASE_URL);
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value) url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    headers: {
      Authorization: `Bearer ${env.ORIGIN_API_TOKEN}`,
      "CF-Access-Client-Id": env.CF_ACCESS_CLIENT_ID,
      "CF-Access-Client-Secret": env.CF_ACCESS_CLIENT_SECRET,
    },
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) {
    const requestId = response.headers.get("x-request-id");
    throw new Error(`Energy API returned HTTP ${response.status}${requestId ? ` (${requestId})` : ""}`);
  }
  return response.json();
}

function textResult(value: unknown) {
  return { content: [{ type: "text" as const, text: JSON.stringify(value) }] };
}

function createServer(env: Env) {
  const server = new McpServer({ name: "European Energy Market Data", version: "0.1.0" });
  const readOnly = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };

  server.registerTool(
    "get_data_status",
    {
      description: "Check data freshness, latest available dates, row counts, gaps and recent collection failures. Call this before requesting prices.",
      inputSchema: z.object({}),
      annotations: readOnly,
    },
    async () => textResult(await originJson(env, "/v1/brief/status")),
  );

  server.registerTool(
    "get_futures_settlements",
    {
      description: "Read EEX futures settlement prices. Values preserve source units and provenance. The maximum window is 90 days.",
      inputSchema: z.object({
        start: isoDate.optional(),
        end: isoDate.optional(),
        product_codes: optionalCsv.describe("Comma-separated EEX product codes, for example F7BY,F7PY,G3BY"),
        maturities: optionalCsv.describe("Comma-separated maturity values"),
      }),
      annotations: readOnly,
    },
    async (args) => {
      assertWindow(args.start, args.end, Number(env.MAX_WINDOW_DAYS || "90"));
      return textResult(await originJson(env, "/v1/brief/futures", args));
    },
  );

  server.registerTool(
    "get_intraday_contracts",
    {
      description: "Read the latest Nord Pool intraday snapshot per area, delivery date and contract. No daily price is invented or aggregated.",
      inputSchema: z.object({
        start: isoDate.optional(),
        end: isoDate.optional(),
        areas: optionalCsv.describe("Comma-separated delivery areas: FR,BE,DE-LU"),
        resolutions: optionalCsv.describe("Comma-separated resolutions in minutes: 15,30,60"),
      }),
      annotations: readOnly,
    },
    async (args) => {
      assertWindow(args.start, args.end, Number(env.MAX_WINDOW_DAYS || "90"));
      return textResult(await originJson(env, "/v1/brief/intraday", args));
    },
  );

  server.registerTool(
    "get_data_gaps",
    {
      description: "List open audited data gaps for a dataset and date window.",
      inputSchema: z.object({
        dataset: z.string().max(100).optional(),
        start: isoDate.optional(),
        end: isoDate.optional(),
      }),
      annotations: readOnly,
    },
    async (args) => {
      assertWindow(args.start, args.end, Number(env.MAX_WINDOW_DAYS || "90"));
      return textResult(await originJson(env, "/v1/brief/gaps", args));
    },
  );
  return server;
}

export default {
  fetch(request: Request, env: Env, ctx: ExecutionContext) {
    return createMcpHandler(() => createServer(env))(request, env, ctx);
  },
} satisfies ExportedHandler<Env>;
