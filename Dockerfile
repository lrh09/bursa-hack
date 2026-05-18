# --- BursaHack research portal — production image ----------------------------
# Multi-stage Docker build for Next.js 15. Python prebuild bakes web/data/*.json
# from results/. Output is a slim Node image suitable for Railway / Fly / any
# container host.

FROM node:22-bookworm-slim AS deps
WORKDIR /repo
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY src ./src
# Minimal Python env so prebuild can run
RUN python3 -m venv .venv && \
    .venv/bin/pip install --no-cache-dir pandas pyarrow numpy && \
    .venv/bin/pip install --no-cache-dir -e .

COPY web/package.json web/pnpm-lock.yaml ./web/
WORKDIR /repo/web
RUN corepack enable && corepack prepare pnpm@9 --activate
RUN pnpm install --frozen-lockfile

# --- build stage --------------------------------------------------------------
FROM deps AS build
WORKDIR /repo
COPY results ./results
COPY DEPLOYMENT_FRAMEWORK.md ./
COPY scripts ./scripts
COPY web ./web
WORKDIR /repo/web
RUN node ../scripts/run-build-data.mjs
RUN pnpm build

# --- runtime stage ------------------------------------------------------------
FROM node:22-bookworm-slim AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV HOSTNAME=0.0.0.0
RUN corepack enable && corepack prepare pnpm@9 --activate
# Copy the standalone build + static assets + prebuilt data
COPY --from=build /repo/web/.next ./web/.next
COPY --from=build /repo/web/public ./web/public
COPY --from=build /repo/web/data ./web/data
COPY --from=build /repo/web/node_modules ./web/node_modules
COPY --from=build /repo/web/package.json ./web/package.json
WORKDIR /app/web
EXPOSE 3000
# Shell form so ${PORT} from Railway/Fly expands at runtime; falls back to 3000
# for local docker runs. Call next directly to dodge pnpm's argv forwarding for -p.
CMD ["sh", "-c", "pnpm exec next start -p ${PORT:-3000} -H 0.0.0.0"]
