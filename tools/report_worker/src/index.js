// Receives one-click problem reports from the Parallelizer QGIS plugin and files each as an issue in a private
// GitHub repository. The GitHub token lives only here (a Worker secret), never in the plugin.
// Nothing about the sender is stored: the IP address is only hashed into a short-lived rate-limit key.

import { MAX_BODY_BYTES, buildIssue, validate } from "./report.js";

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

async function rateLimitKey(request) {
  const ip = request.headers.get("cf-connecting-ip") || "unknown";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(ip));
  return [...new Uint8Array(digest)].slice(0, 12).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return json(405, { error: "use POST" });
    if (!(request.headers.get("content-type") || "").startsWith("application/json")) {
      return json(415, { error: "send application/json" });
    }
    // Before reading the body: rejected clients cost no more than their headers.
    // Optional: the binding may not exist on every plan (see README).
    if (env.REPORT_LIMITER) {
      const { success } = await env.REPORT_LIMITER.limit({ key: await rateLimitKey(request) });
      if (!success) return json(429, { error: "too many reports, try again later" });
    }
    const tooLarge = () => json(413, { error: "report too large" });
    if (Number(request.headers.get("content-length") || 0) > MAX_BODY_BYTES) return tooLarge();
    const bytes = await request.arrayBuffer();
    if (bytes.byteLength > MAX_BODY_BYTES) return tooLarge();
    const raw = new TextDecoder().decode(bytes);

    let payload;
    try {
      payload = JSON.parse(raw);
    } catch {
      return json(400, { error: "body is not valid JSON" });
    }
    const problem = validate(payload);
    if (problem) return json(400, { error: problem });

    if (env.DRY_RUN === "1") return json(201, { id: 0 });

    const response = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/issues`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: "application/vnd.github+json",
        "content-type": "application/json",
        "user-agent": "parallelizer-report-worker",
        "x-github-api-version": "2022-11-28",
      },
      body: JSON.stringify(buildIssue(payload)),
    });
    if (!response.ok) {
      // Status only: never log the report or anything about the sender.
      console.error(`GitHub API answered ${response.status}`);
      return json(502, { error: "could not file the report" });
    }
    const issue = await response.json();
    return json(201, { id: issue.number });
  },
};
