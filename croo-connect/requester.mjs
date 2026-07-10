// CROO requester — demo buyer for the Hook AI provider (A2A loop for the video).
//
// Run with a SECOND agent's key (register at agent.croo.network, deposit a few
// USDC on its AA wallet):
//   CROO_SDK_KEY=croo_sk_<requester> CROO_TARGET_SERVICE_ID=<service-id> \
//     node requester.mjs [task] [cv-file]
//
// Flow (mirrors the official examples/requester.ts):
//   negotiateOrder -> [WS] order_created -> payOrder (USDC escrow)
//   -> provider delivers -> [WS] order_completed -> getDelivery -> print JSON.

import "dotenv/config";
import { readFileSync } from "node:fs";

const {
  CROO_SDK_KEY,
  CROO_API_URL = "https://api.croo.network",
  CROO_WS_URL = "wss://api.croo.network/ws",
  BASE_RPC_URL,
  CROO_TARGET_SERVICE_ID,
} = process.env;

const task = process.argv[2] ?? "recommend";
const cvFile = process.argv[3];
const cv_text = cvFile
  ? readFileSync(cvFile, "utf-8")
  : `John Smith
john.smith@example.com | +1 555 0100
EXPERIENCE
Backend Developer at Acme Corp, 2021-2025: Python APIs, PostgreSQL, Docker.
SKILLS: Python, SQL, Docker, Go
EDUCATION: BSc Computer Science`;

if (!CROO_SDK_KEY || !CROO_TARGET_SERVICE_ID) {
  console.error(
    "Usage: CROO_SDK_KEY=croo_sk_<requester> CROO_TARGET_SERVICE_ID=<id> node requester.mjs [task] [cv-file]"
  );
  process.exit(1);
}

const { AgentClient, EventType, DeliverableType } = await import("@croo-network/sdk");

const client = new AgentClient(
  { baseURL: CROO_API_URL, wsURL: CROO_WS_URL, rpcURL: BASE_RPC_URL },
  CROO_SDK_KEY
);

const stream = await client.connectWebSocket();

stream.on(EventType.OrderCreated, async (e) => {
  console.log(`[buyer] order ${e.order_id} created — paying (USDC escrow)…`);
  try {
    const result = await client.payOrder(e.order_id);
    console.log(`[buyer] paid ✓ tx: ${result?.txHash ?? "(sponsored)"}`);
  } catch (err) {
    console.error("[buyer] pay error:", err?.message ?? err);
  }
});

stream.on(EventType.OrderCompleted, async (e) => {
  console.log(`[buyer] order ${e.order_id} completed — fetching deliverable`);
  try {
    const delivery = await client.getDelivery(e.order_id);
    const raw =
      delivery.deliverableType === DeliverableType.Schema
        ? delivery.deliverableSchema
        : delivery.deliverableText;
    try {
      console.log(JSON.stringify(JSON.parse(raw), null, 2));
    } catch {
      console.log(raw);
    }
  } catch (err) {
    console.error("[buyer] get delivery error:", err?.message ?? err);
  }
  stream.close();
  process.exit(0);
});

console.log(`[buyer] negotiating "${task}" with service ${CROO_TARGET_SERVICE_ID}…`);
const neg = await client.negotiateOrder({
  serviceId: CROO_TARGET_SERVICE_ID,
  requirements: JSON.stringify({ task, cv_text, params: {} }),
});
console.log(`[buyer] negotiation ${neg.negotiationId ?? neg.negotiation_id} sent — waiting for provider…`);

process.on("SIGINT", () => {
  stream.close();
  process.exit(0);
});
