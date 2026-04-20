FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# --- NEXT_PUBLIC_* build args ----------------------------------------------
# These get frozen into the client bundle at `npm run build`. Render passes
# every envVar declared in render.yaml as --build-arg automatically when
# dockerContext is set to this directory.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}

# BACKEND_URL is used by server-side rewrites(); we prefer browser-direct
# calls in prod, so rewrites are effectively a no-op. Still set it so dev
# mode + SSR pages work.
ARG BACKEND_URL
ENV BACKEND_URL=${BACKEND_URL:-${NEXT_PUBLIC_API_BASE}}

# --- Neon Auth (Stack Auth) -------------------------------------------------
# NEXT_PUBLIC_STACK_PROJECT_ID and NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY
# are baked into the bundle so the browser SDK can initialise. When unset,
# the app runs without Stack Auth (dev fallback path remains active).
ARG NEXT_PUBLIC_STACK_PROJECT_ID
ENV NEXT_PUBLIC_STACK_PROJECT_ID=${NEXT_PUBLIC_STACK_PROJECT_ID}
ARG NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY
ENV NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY=${NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY}
# STACK_SECRET_SERVER_KEY is server-only — not baked into the client bundle,
# but needed at build time because `next build` prerenders some server
# components that reference the Stack server app singleton.
ARG STACK_SECRET_SERVER_KEY
ENV STACK_SECRET_SERVER_KEY=${STACK_SECRET_SERVER_KEY}

RUN npm run build

FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000

# Copy only the artifacts needed to run `next start`.
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
COPY --from=builder /app/next.config.ts ./next.config.ts

# Re-expose NEXT_PUBLIC_* at runtime for SSR pages (they read from
# process.env on each request). These must match what was used at build.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
ARG BACKEND_URL
ENV BACKEND_URL=${BACKEND_URL:-${NEXT_PUBLIC_API_BASE}}
ARG NEXT_PUBLIC_STACK_PROJECT_ID
ENV NEXT_PUBLIC_STACK_PROJECT_ID=${NEXT_PUBLIC_STACK_PROJECT_ID}
ARG NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY
ENV NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY=${NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY}
# STACK_SECRET_SERVER_KEY is ONLY needed server-side at runtime. Render
# injects it as an envVar automatically — no ARG line needed.

EXPOSE ${PORT}
CMD ["sh", "-c", "npx next start -p ${PORT}"]
