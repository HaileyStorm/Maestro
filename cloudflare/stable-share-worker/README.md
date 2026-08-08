# Maestro stable share redirect

This tiny Cloudflare Worker gives Maestro a reusable `https://*.workers.dev`
address while Pinokio continues to create a free Quick Tunnel on every launch.
It stores only the current canonical `https://*.trycloudflare.com` origin in KV
and, while Maestro's cookie-free `/health` endpoint responds, sends a `307`
redirect preserving request path, query, and method. The liveness subrequest has
a two-second ceiling and an eight-second Cache API result to bound edge checks.
It never proxies media, uploads, prompts, or application responses; it emits no
logs and does not enable Worker observability.

When the Quick Tunnel or Maestro is unavailable, a normal browser navigation
receives a self-contained no-tracking offline page. API calls and non-navigation
requests receive a small `503` JSON response. The page has no scripts, remote
assets, forms, user content, or analytics and is served with a restrictive CSP.

Use only a Cloudflare **Workers Free** account. Do not enable a paid Workers
plan, paid usage, or a custom domain for this setup. Free-plan limits fail
closed when exhausted instead of being handled by this Worker. The included
`*.workers.dev` hostname means no domain purchase is required.

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
failing on that expired Quick Tunnel until KV converges. This redirect never
weakens the backend's project passwords or remote restrictions, and target
validation prevents KV from redirecting outside canonical Quick Tunnel origins.

The reserved authenticated endpoints are:

- `PUT /.well-known/maestro-share/target`
- `GET /.well-known/maestro-share/health`

Both require `Authorization: Bearer <PINOKIO_STABLE_SHARE_UPDATE_SECRET>`.
Responses and redirects use `Cache-Control: no-store`. The secret is never
returned. Run the offline Worker tests with `node --test worker.test.mjs`.
