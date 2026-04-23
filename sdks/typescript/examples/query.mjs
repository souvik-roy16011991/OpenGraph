// Minimal example — query a graph and print the answer.
//
// Build first:    npm run build
// Then run:       OPENGRAPH_API_KEY=og_live_... \
//                 OPENGRAPH_WORKSPACE_ID=... \
//                 OPENGRAPH_BASE_URL=http://localhost:8000 \
//                 node examples/query.mjs "your question"

import { OpenGraphClient } from "../dist/index.js";

const client = new OpenGraphClient();

const question = process.argv.slice(2).join(" ") || "What's in this knowledge base?";
console.log(`Q: ${question}\n`);

try {
  const resp = await client.query({ query: question });
  console.log("A:", resp.response);
  if (resp.follow_up_suggestions?.length) {
    console.log("\nFollow-ups:");
    for (const s of resp.follow_up_suggestions) console.log(`  - ${s}`);
  }
  if (resp.usage) {
    console.log(
      `\nTokens: ${resp.usage.llm_total_tokens} ` +
      `(prompt ${resp.usage.llm_prompt_tokens} / completion ${resp.usage.llm_completion_tokens})`,
    );
  }
  console.log(`Session id: ${resp.session_id} · took ${resp.duration_ms} ms`);
} catch (err) {
  const e = /** @type {Error & { status?: number }} */ (err);
  console.error(`API error${e.status ? ` (${e.status})` : ""}: ${e.message}`);
  process.exit(1);
}
