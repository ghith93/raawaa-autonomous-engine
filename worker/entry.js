// Worker entry point: routes the durable quota-ledger API to worker/quota.js and
// delegates every other route (task queue, research candidates, health, cron)
// to the existing worker/index.js unchanged.
import base from "./index.js";
import { handleQuota } from "./quota.js";

export default {
  scheduled(controller, env, ctx) {
    return base.scheduled(controller, env, ctx);
  },
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/v1/quota/")) return handleQuota(request, url, env);
    return base.fetch(request, env, ctx);
  },
};
