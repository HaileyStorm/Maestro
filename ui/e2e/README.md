# Synthetic browser acceptance

This suite opens only the Vite UI and fulfills a content-free API allowlist in
the browser context. Browser requests to unknown API routes, backend routes,
WebSockets, external origins, and Vite filesystem paths outside the isolated
dependency cache are rejected. It never intentionally connects to a Maestro
backend or uses real accounts, prompts, jobs, media, or storage state.

From `ui/`:

```sh
npm ci
npm run test:e2e:install
npm run check:e2e
npm run test:e2e -- --project=desktop-firefox --project=android-like-chromium
```

`check:e2e` compiles the harness and lists the checked-in Playwright suite as
one explicit E2E gate. The ordinary product `build` does not invoke the harness
or require its external cache/result volumes.

The runner accepts only test-file filters plus `--grep`, `--grep-invert`,
`--project`, and `--list`. It rejects configuration, output, reporter, trace,
UI/debug, snapshot, and other Playwright overrides so command-line arguments
cannot bypass the checked-in security and artifact settings.

Browser binaries, npm cache, Playwright results, and Vite cache must resolve to
a different mounted filesystem or Windows volume from the checkout. The runner
creates each directory before resolving its real path and checking its device
or volume identity; each test invocation receives exclusive UUID-named result
and Vite-cache directories. Linux defaults use `/var/tmp/maestro-*`; other
platforms must override any default that shares the checkout filesystem:

```sh
PLAYWRIGHT_BROWSERS_PATH=/external/cache/browsers \
MAESTRO_PLAYWRIGHT_OUTPUT_DIR=/external/cache/results \
MAESTRO_VITE_CACHE_DIR=/external/cache/vite \
npm_config_cache=/external/cache/npm \
npm run test:e2e
```

The runner retains browser binaries, npm cache, and every external UUID-named
result/Vite-cache directory. It does not automatically delete them. Remove old
directories only after confirming no E2E invocation is active. No browser
binaries, reports, screenshots, video, traces, HAR, credentials, or
`storageState` are written to the repository.

The child environment is an OS/runtime allowlist plus the generated harness
identity and external paths; parent credentials, proxy settings, and
`NODE_OPTIONS` are not inherited. The global setup accepts only the exact
UUID-authenticated `127.0.0.1` listener and refuses redirects. There remains a
brief availability-only handoff between selecting the loopback port and Vite
binding it; strict-port startup and the run-token identity check prevent a
different listener from being accepted.

The browser route layer is not an OS sandbox for trusted Node test or config
code. Direct Node `fetch`, HTTP(S), sockets, TLS, DNS, or a newly created request
context could attempt egress if unreviewed code were added to this harness.
Keep the checked-in test/config surface reviewed and use host network isolation
when that stronger threat model is required.

The responsive matrix covers the initial shell at the mobile/desktop boundary
and representative expanded states at 320, 390, 568-landscape, and 767 CSS
pixels. It exercises post-load resize, touch-style input, Image mode, Gallery
search/filter/select, Support, What's New, Recipes, and Director overlays;
checks rendered button/link/input/select/combobox/tabindex/contenteditable and
cursor-marked targets when present; verifies target size and scroll-owner
reachability; and runs serious/critical axe checks on representative overlays.
This is representative regression coverage, not an exhaustive proof of every
conditional control or assistive-technology interaction.

The fixture also exercises the optional account UI without enabling real
accounts or loading credentials. Deterministic, in-memory scenarios cover the
accounts-disabled compatibility path, locally offered first-owner bootstrap,
anonymous sign-in, authenticated owner and normal-user projections, recent
owner confirmation, session inventory/revocation, sign-out, and the remote
bootstrap boundary. The synthetic identities, passwords, nonces, sessions, and
recovery codes exist only in the browser test process; no production account
flags, cookies, passkeys, provider APIs, payments, or billing are used. These
journeys verify that project/browser-session authority remains visibly separate
from account state. They do not claim real credential, passkey, remote-device,
or production account acceptance.

CI images must provide Playwright's documented Linux browser dependencies.
Android-like Chromium and iOS-like WebKit are browser emulations, not physical
device acceptance. This release gate runs Firefox plus Android-like Chromium;
it makes no WebKit pass claim. iOS Safari with VoiceOver and Android Chrome with
TalkBack remain separate device-lab/manual checks.
