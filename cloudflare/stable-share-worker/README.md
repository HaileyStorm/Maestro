# Maestro stable share modes

This tiny Cloudflare Worker gives Maestro a reusable `https://*.workers.dev`
address while Pinokio continues to create a free Quick Tunnel on every launch.
It stores only the current canonical `https://*.trycloudflare.com` origin in KV
and supports two public traffic modes. The default `proxy` mode streams HTTP
requests and responses through the reusable address, so refreshing a Maestro
page keeps the stable hostname after Maestro or Pinokio restarts. The explicit
`redirect` rollback mode preserves the previous low-traffic `307` behavior. In
proxy mode polling,
uploads, downloads, `HEAD`, and byte-range/`206` responses pass through without
the Worker reading, cloning, or buffering their bodies. `Range`, response
status, `Content-Range`, and ordinary metadata are preserved. Cloudflare owns
the streamed response's wire framing, so the Worker deliberately removes the
upstream `Content-Length`; clients must not depend on that header surviving.

Proxy mode is a privacy and capacity tradeoff compared with redirect mode:
request and response content now transits the Cloudflare Worker runtime as well
as the Quick Tunnel. The Worker does not log request bodies, cookies,
Authorization, prompts, media, or responses; observability remains disabled and
it stores only the current Quick Tunnel origin in KV. Proxied responses use
`Cache-Control: no-store`. Host-only browser cookies stay attached to the stable
hostname and are forwarded only to the currently registered canonical Quick
Tunnel target.

Upstream fetches use `redirect: "manual"`. Redirects back to the registered
Quick Tunnel are rewritten onto the stable hostname; redirects to any other
origin are rejected with `502`, so cookies and Authorization are never replayed
by the Worker across a target-controlled redirect. Multiple `Set-Cookie` headers
remain separate. Inherited proxy-routing headers are removed so Cloudflare and
the Quick Tunnel establish the actual upstream host and forwarding identity.

> **Cloudflare Free inbound requests are limited to 100 MB.** That ceiling
> applies to the stable Worker proxy and is also expected on the Cloudflare edge
> serving a Quick Tunnel, so the direct `*.trycloudflare.com` link is not a
> large-upload bypass. Use Maestro locally or over an explicitly enabled LAN for
> larger uploads. Chunked upload assembly is not implemented here.

When the Quick Tunnel or Maestro is unavailable, a normal browser navigation
receives a self-contained no-tracking offline page. API calls and non-navigation
requests receive a small `503` JSON response. The page has no scripts, remote
assets, forms, user content, or analytics and is served with a restrictive CSP.

Use only a Cloudflare **Workers Free** account. Do not enable a paid Workers
plan, paid usage, or a custom domain for this setup. Free-plan limits fail
closed when exhausted instead of being handled by this Worker. The included
`*.workers.dev` hostname means no domain purchase is required. The Free plan is
limited to 100,000 Worker requests per day and 10 ms of CPU per invocation;
Cloudflare's request-body and subrequest limits also apply. Normal proxy hits
use one KV read and one upstream fetch. A health-cache miss adds one minimal
`/health` subrequest; that result is cached per edge and target for eight
seconds. Maestro's polling cadence can therefore consume the daily request
allowance even though streaming body time is not CPU time. The landed and
tested remote idle cadence is 2,880 requests/day per visible tab and zero while
hidden, below the 25,000/day enablement gate. A continuously active remote tab
has a conservative periodic upper bound of 56,160/day; one running job plus ten
queued jobs raises that safety budget to 59,040/day. Those figures cover
periodic requests only, before bounded event-driven refreshes. Local machine-control
polling is 23,040/day, but it reaches this Worker only when the local owner
chooses the stable public URL instead of Maestro's loopback URL. These budgets
support proxy as the default for ordinary use, but multiple continuously active
tabs can still exhaust the Free allowance. Quick Tunnels do not support
Server-Sent Events, so Maestro deliberately uses ordinary polling; WebSocket
upgrade proxying is not part of this Worker's contract.

Cloudflare references: [Quick Tunnel limitations](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/),
[Worker fetch](https://developers.cloudflare.com/workers/runtime-apis/fetch/),
[Request bodies](https://developers.cloudflare.com/workers/runtime-apis/request/),
[multiple `Set-Cookie` headers](https://developers.cloudflare.com/workers/runtime-apis/headers/),
and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/).

## One-time provisioning

1. In the Cloudflare dashboard, confirm the account says **Workers Free**, has
   no paid Workers subscription, and has no payment method you intend this
   setup to use. Then set `CLOUDFLARE_WORKERS_FREE_CONFIRMED=true` in the
   ignored `ENVIRONMENT`. The provisioner refuses every cloud mutation without
   this explicit zero-charge gate.
2. Preferred: create a narrowly scoped Cloudflare API token for only this
   account's Workers Scripts, Workers KV Storage, and Account Settings read
   permissions. Put it and the account ID only in the ignored `ENVIRONMENT`.
3. One-time OAuth fallback: if there is no API token, run `npx wrangler@4 login`
   in this directory first. The provisioner uses `wrangler whoami` only to
   confirm the authenticated account; it never reads or prints the OAuth token.
4. Run `node provision.mjs`. When no update secret exists, the script generates
   32 random bytes locally. It passes the secret to Wrangler only over stdin,
   uses a mode-0600 temporary config, disables observability, creates one KV
   namespace when needed, deploys, and uploads `UPDATE_TOKEN` as a Worker secret.
5. The script atomically updates the ignored `ENVIRONMENT` (mode 0600) with the
   update secret, account ID, KV ID, Worker name, and emitted
   `https://<worker>.<subdomain>.workers.dev` origin. It never prints the secret.
   A scoped API token is blanked from `ENVIRONMENT` after successful setup;
   add a fresh token again only when intentionally reprovisioning.
   In the OAuth lane it then runs `wrangler logout` and verifies `whoami` no
   longer reports an account, removing the broad one-time credential. If that
   cleanup cannot be verified, run `npx wrangler@4 logout` immediately.

Starting Maestro remains the only runtime action. After Pinokio reports its
Quick Tunnel, the local helper updates the Worker over the authenticated target
endpoint, then polls authenticated health for up to about one minute to allow
Workers KV's eventual propagation at the updating edge. Only after that edge
reports the exact current target does Maestro display/register the stable URL.
Any missing, unauthorized, unhealthy, or still-stale Worker falls back to the
current Quick Tunnel.

Workers KV is globally eventually consistent. A different edge can briefly
retain the previous launch's target even after local verification, typically
failing on that expired Quick Tunnel until KV converges. The proxy never
weakens the backend's project passwords or remote restrictions, and target
validation prevents KV from sending traffic outside canonical Quick Tunnel
origins.

## Default proxy, direct fallback, and rollback

Maestro continues to display the current `*.trycloudflare.com` URL separately;
that direct address avoids the extra Worker hop and is the fallback if the
Workers Free request allowance is exhausted. It does not bypass Cloudflare's
inbound request ceiling. Copy the direct URL from Maestro or Pinokio before
relying on it. While the Worker itself is healthy and still accepting requests,
`GET /.well-known/maestro-share/direct` performs a bounded `307` to the validated
current Quick Tunnel root. The `/direct` route cannot work after Worker quota or
platform failure and is therefore a convenience, not the quota fallback. It
ignores query-supplied destinations, so it is not an open redirect. A direct
visit uses the Quick Tunnel hostname and may require unlocking the project again
because stable-host cookies are not shared across hosts.

Stable-host proxying is the default when `SHARE_MODE` is absent or `proxy`. To
roll back, set `SHARE_MODE=redirect`. An unrecognized nonblank value fails safe
to redirect mode. Redirect mode sends every ordinary path to the validated
target while target registration, health checks, and the offline response remain
unchanged. The example Wrangler config records the default explicitly. Changing
a Worker variable creates a Cloudflare deployment; it does not require a paid
plan.

The reserved authenticated endpoints are:

- `PUT /.well-known/maestro-share/target`
- `GET /.well-known/maestro-share/health`
- `GET /.well-known/maestro-share/direct` (public bounded fallback)

The target and health endpoints require
`Authorization: Bearer <PINOKIO_STABLE_SHARE_UPDATE_SECRET>`; the direct fallback
does not. Responses use `Cache-Control: no-store`, and the update secret is
never returned. Run the offline Worker tests with
`node --test worker.test.mjs`.
