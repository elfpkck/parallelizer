import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import worker from "../src/index.js";
import { MAX_BODY_BYTES, buildIssue, validate } from "../src/report.js";

const valid = { schema: 1, title: "Bug: ValueError", plugin_version: "2.2.0", qgis_version: "4.2.2", report: "text" };

function post(body, headers = {}) {
  return new Request("https://reports.example.org/", {
    method: "POST",
    headers: { "content-type": "application/json", "cf-connecting-ip": "203.0.113.7", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

describe("validate", () => {
  it("accepts a valid report", () => assert.equal(validate(valid), null));
  it("rejects a wrong schema", () => assert.match(validate({ ...valid, schema: 2 }), /schema/));
  it("rejects an empty report", () => assert.match(validate({ ...valid, report: "  " }), /report/));
  it("rejects non-string fields", () => assert.match(validate({ ...valid, title: 5 }), /title/));
  it("rejects arrays", () => assert.match(validate([valid]), /object/));
});

describe("buildIssue", () => {
  it("prefixes and cleans the title", () => {
    assert.equal(buildIssue({ ...valid, title: "a\nb" }).title, "[report] a b");
    assert.equal(buildIssue({ ...valid, title: "" }).title, "[report] Problem report");
    assert.equal(buildIssue({ ...valid, title: "x".repeat(500) }).title.length, "[report] ".length + 120);
  });

  it("fences the report longer than any backtick run inside it", () => {
    const body = buildIssue({ ...valid, report: "before ```` after" }).body;
    assert.ok(body.includes("`````text\nbefore ```` after\n`````"));
    assert.ok(body.startsWith("Plugin: 2.2.0\nQGIS: 4.2.2\n"));
  });
});

describe("fetch", () => {
  const realFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = realFetch;
  });

  it("files a GitHub issue and returns its number", async () => {
    let sent;
    globalThis.fetch = async (url, init) => {
      sent = { url, init };
      return new Response(JSON.stringify({ number: 17 }), { status: 201 });
    };
    const env = { GITHUB_REPO: "owner/reports", GITHUB_TOKEN: "t" };

    const response = await worker.fetch(post(valid), env);

    assert.equal(response.status, 201);
    assert.deepEqual(await response.json(), { id: 17 });
    assert.equal(sent.url, "https://api.github.com/repos/owner/reports/issues");
    assert.equal(sent.init.headers.authorization, "Bearer t");
    assert.equal(JSON.parse(sent.init.body).title, "[report] Bug: ValueError");
  });

  it("returns 502 when GitHub fails", async () => {
    globalThis.fetch = async () => new Response("{}", { status: 500 });
    const response = await worker.fetch(post(valid), { GITHUB_REPO: "o/r", GITHUB_TOKEN: "t" });
    assert.equal(response.status, 502);
  });

  it("answers without calling GitHub in dry-run mode", async () => {
    globalThis.fetch = async () => assert.fail("GitHub must not be called");
    const response = await worker.fetch(post(valid), { DRY_RUN: "1" });
    assert.equal(response.status, 201);
  });

  it("rejects wrong methods, types, sizes and bodies", async () => {
    const env = { DRY_RUN: "1" };
    assert.equal((await worker.fetch(new Request("https://r.example.org/"), env)).status, 405);
    assert.equal((await worker.fetch(post(valid, { "content-type": "text/plain" }), env)).status, 415);
    const big = { ...valid, report: "x".repeat(MAX_BODY_BYTES) };
    assert.equal((await worker.fetch(post(big), env)).status, 413);
    assert.equal((await worker.fetch(post("{not json"), env)).status, 400);
    assert.equal((await worker.fetch(post({ ...valid, schema: 9 }), env)).status, 400);
  });

  it("rate-limits by a hashed client key", async () => {
    const keys = [];
    const env = { DRY_RUN: "1", REPORT_LIMITER: { limit: async ({ key }) => (keys.push(key), { success: false }) } };

    const response = await worker.fetch(post(valid), env);

    assert.equal(response.status, 429);
    assert.match(keys[0], /^[0-9a-f]{24}$/);
    assert.ok(!keys[0].includes("203.0.113.7"));
  });
});
