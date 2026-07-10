// CROO Agent Protocol (CAP) provider for Hook AI — agent.croo.network.
//
// Bridges the local Hook AI runtime to the CROO Agent Store: listens for
// negotiations over the CROO WebSocket, auto-accepts, fulfils paid orders by
// calling the Hook AI backend (/internal/fulfill), and delivers the result
// on-chain (hash written by the platform; gas sponsored).
//
// Verified against docs.croo.network (developer-docs/quick-start,
// sdk-reference/node.js-sdk-reference) and the official examples/provider.ts:
//   env:   CROO_SDK_KEY (croo_sk_..., from the dashboard, shown once)
//          CROO_API_URL=https://api.croo.network
//          CROO_WS_URL=wss://api.croo.network/ws
//   flow:  NegotiationCreated -> acceptNegotiation -> OrderPaid ->
//          fulfil -> deliverOrder(Schema) -> settlement to the AA wallet.
//
// Without the SDK installed or CROO_SDK_KEY set it runs in dry-run mode so the
// container still starts in local development.

import "dotenv/config";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const {
  CROO_SDK_KEY,
  CROO_API_URL = "https://api.croo.network",
  CROO_WS_URL = "wss://api.croo.network/ws",
  BASE_RPC_URL,
  HOOKAI_API_URL = "http://localhost:8000",
} = process.env;

const service = JSON.parse(readFileSync(new URL("./croo.service.json", import.meta.url)));

let sdk = null;
try {
  sdk = await import("@croo-network/sdk");
} catch {
  console.warn("[croo-connect] @croo-network/sdk not installed — running in dry-run mode.");
}

// Requirements arrive as a JSON string chosen by the buyer at negotiateOrder().
// Shape mirrors croo.service.json's requirements_schema; tolerate garbage.
function parseRequirements(raw) {
  try {
    const req = typeof raw === "string" ? JSON.parse(raw) : raw ?? {};
    return req && typeof req === "object" ? req : {};
  } catch {
    return {};
  }
}

// Turn one paid CROO order's requirements into a Hook AI deliverable (JSON string).
async function fulfill(requirements) {
  const req = parseRequirements(requirements);
  const res = await fetch(`${HOOKAI_API_URL}/internal/fulfill`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      task: req.task ?? service.default_task ?? "recommend",
      cv_text: req.cv_text ?? "",
      params: req.params ?? {},
    }),
  });
  if (!res.ok) throw new Error(`Hook AI fulfil failed: HTTP ${res.status}`);
  const data = await res.json();
  const deliverable = data.deliverable ?? data; // {error: ...} is still a valid schema delivery
  const output = JSON.stringify({
    ...((typeof deliverable === "object" && deliverable) || { result: deliverable }),
    _proof: {
      result_hash: createHash("sha256").update(JSON.stringify(deliverable)).digest("hex"),
      executed_at: new Date().toISOString(),
      runtime: "hookai/0.1",
    },
  });
  return output;
}

async function main() {
  if (!CROO_SDK_KEY || !sdk) {
    console.log(`[dry-run] Hook AI provider ready. Would connect to ${CROO_WS_URL}.`);
    console.log(`[dry-run] Offering "${service.name}" — tags: ${service.skill_tags.join(", ")}`);
    console.log(`[dry-run] Fulfilment backend: ${HOOKAI_API_URL}/internal/fulfill`);
    if (!CROO_SDK_KEY)
      console.log(
        "[dry-run] Set CROO_SDK_KEY in croo-connect/.env (register at https://agent.croo.network) to go live."
      );
    setInterval(() => {}, 1 << 30);
    return;
  }

  const { AgentClient, EventType, DeliverableType } = sdk;
  const client = new AgentClient(
    { baseURL: CROO_API_URL, wsURL: CROO_WS_URL, rpcURL: BASE_RPC_URL, logger: console },
    CROO_SDK_KEY
  );

  const stream = await client.connectWebSocket();
  console.log(`[croo] connected to ${CROO_WS_URL} — offering "${service.name}"`);

  // Requirements are attached to the NEGOTIATION; remember them per order so the
  // OrderPaid handler doesn't need an extra lookup. Fallback: getOrder/getNegotiation.
  const orderRequirements = new Map();

  stream.on(EventType.NegotiationCreated, async (e) => {
    console.log(`[croo] negotiation ${e.negotiation_id} received — accepting`);
    try {
      const neg = await client.getNegotiation(e.negotiation_id);
      const result = await client.acceptNegotiation(e.negotiation_id);
      const orderId = result?.order?.orderId ?? result?.orderId;
      if (orderId) orderRequirements.set(String(orderId), neg?.requirements ?? "");
      console.log(`[croo] order ${orderId} created — awaiting payment (escrow)`);
    } catch (err) {
      console.error("[croo] accept error:", err?.message ?? err);
    }
  });

  stream.on(EventType.OrderPaid, async (e) => {
    const orderId = String(e.order_id);
    console.log(`[croo] order ${orderId} PAID — fulfilling via Hook AI`);
    try {
      let requirements = orderRequirements.get(orderId);
      if (requirements === undefined) {
        // e.g. provider restarted between accept and pay: Order carries no
        // requirements, only negotiationId — fetch the negotiation for them.
        const order = await client.getOrder(orderId);
        const neg = order?.negotiationId ? await client.getNegotiation(order.negotiationId) : null;
        requirements = neg?.requirements ?? "";
      }
      const output = await fulfill(requirements);
      await client.deliverOrder(orderId, {
        deliverableType: DeliverableType.Schema,
        deliverableSchema: output,
      });
      orderRequirements.delete(orderId);
      console.log(`[croo] order ${orderId} DELIVERED (${output.length} bytes) — settling`);
    } catch (err) {
      console.error(`[croo] deliver error for order ${orderId}:`, err?.message ?? err);
    }
  });

  stream.on(EventType.OrderCompleted, (e) => {
    console.log(`[croo] order ${e.order_id} COMPLETED — USDC settled to the agent wallet ✓`);
  });

  process.on("SIGINT", () => {
    stream.close();
    process.exit(0);
  });
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
