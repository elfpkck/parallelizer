# Report receiver (Cloudflare Worker)

Receives the reports users send with **Send report** in the plugin's report dialog and files each one as an issue in a **private** GitHub repository. The plugin only knows this Worker's URL; the GitHub token lives here as a Worker secret. Runs on the Cloudflare Workers free plan (100,000 requests/day).

What it does with a request:

- accepts only `POST` with `application/json`, at most 64 KB, schema 1, a non-empty `report`;
- limits each client to 5 reports per minute, keyed by a hash of the IP address (the IP itself is never stored or logged; Worker logs are off in `wrangler.toml`);
- creates an issue titled `[report] <title>` with the plugin/QGIS versions and the report in a code fence (so `@mentions` and `#links` in it stay inert), and answers `201 {"id": <issue number>}`, which the user sees as their reference.

## One-time setup

1. **Private repository** for the reports, e.g. `elfpkck/parallelizer-reports`.
2. **Token**: a fine-grained personal access token (GitHub › Settings › Developer settings) with access to that repository only and **Issues: Read and write**. Nothing else.
3. **Cloudflare account** (free). Then, from this folder:
   ```shell
   npx wrangler login
   npx wrangler secret put GITHUB_TOKEN          # paste the token
   npx wrangler deploy --var GITHUB_REPO:elfpkck/parallelizer-reports
   ```
   `deploy` prints the Worker URL, e.g. `https://parallelizer-reports.<account>.workers.dev`.
   If it fails on the `[[ratelimits]]` block (the rate-limiting binding isn't available on every plan), remove that block and add a rate-limiting rule for the Worker's route in the Cloudflare dashboard (Security › WAF; free plans get one rule) instead. The Worker runs without the binding.
4. **Plugin**: add the URL as the `REPORT_ENDPOINT` repository secret of the plugin repo (Settings › Secrets and variables › Actions). The release workflow writes it into the package; see `DEVELOPMENT.md`.

## Local testing

```shell
node --test                      # unit tests, no dependencies
npm run dev                      # wrangler dev with DRY_RUN=1: answers 201 {"id": 0} without calling GitHub
```

To try it from QGIS, point a local build at it by setting `REPORT_ENDPOINT` in `PolygonsParallelToLine/src/contact.py` (don't commit that).

## Operating it

- **Retention**: close and delete report issues once the problem is handled, and at the latest after 12 months (this is what the privacy note promises).
- **Deletion requests** come with the report reference (the issue number): delete that issue.
- **Rotating the token**: create a new one, `npx wrangler secret put GITHUB_TOKEN`, revoke the old one. The plugin needs no change.
- **Spam**: lower the limit in `wrangler.toml`, or add stricter WAF rules. If the URL itself gets abused, deploy under a new name and publish a plugin release with the new `REPORT_ENDPOINT`.
