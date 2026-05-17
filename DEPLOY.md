# Deployment notes — BursaHack research portal

The portal is a server-rendered Next.js 15 app. Every page is prerendered at
build time (102 variants + 2 strategies + 4 reports + 4 static routes), so the
runtime is essentially a static-HTML server with API routes.

## Local

```
cd web
pnpm install
pnpm dev
# http://localhost:3000
```

## Production build

```
cd web
pnpm build
pnpm start
```

The `pnpm prebuild` hook runs `scripts/build_data.py` first (via the venv at
`../.venv/Scripts/python.exe` on Windows; `../.venv/bin/python` elsewhere) and
emits `web/data/*.json` from the artifacts under `../results/`.

## Railway (recommended)

The repo ships a `Dockerfile` and `railway.json`. From the Railway dashboard:

1. New project → Deploy from GitHub repo → pick `bursa-hack`.
2. Set the branch to `feat/research-portal` (or `master` after merge).
3. Railway detects the `Dockerfile` automatically. No env vars required.
4. (Optional) point a custom subdomain (`bursahack.fatfyre.com`) at the Railway
   service via Cloudflare CNAME.

The Docker build takes ~3-4 minutes (pulls Node 22, installs Python deps for the
data pipeline, runs `pnpm build`).

## Cloudflare Pages alternative

The app can also be statically exported. Reinstate `output: 'export'` in
`web/next.config.ts`, then:

```
cd web
pnpm build
# emits web/out/
wrangler pages deploy out --project-name bursahack
```

Caveats: the API route `/api/variants` becomes a static JSON file in `out/api/`
(it's already `dynamic = "force-static"` so this just works). The dynamic OG
image `opengraph-image.tsx` requires the edge runtime and is excluded from
the static export — switch it to a pre-rendered PNG before deploying static.

## E2E tests

```
cd web
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

Smoke covers every primary route + the command palette + the strategy page tabs.
Accessibility scan covers landing / strategy / search / methodology / compare.
