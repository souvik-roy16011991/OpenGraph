FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are frozen into the client bundle at build time. Render
# injects them via --build-arg so every deploy gets the correct backend URL.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
# BACKEND_URL is used by server-side rewrites() but we prefer browser-direct
# calls in prod so rewrites are a no-op — still set so dev mode works.
ARG BACKEND_URL
ENV BACKEND_URL=${BACKEND_URL:-${NEXT_PUBLIC_API_BASE}}
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

# Re-expose the same NEXT_PUBLIC_* var at runtime for server-rendered pages.
ARG NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE}
ARG BACKEND_URL
ENV BACKEND_URL=${BACKEND_URL:-${NEXT_PUBLIC_API_BASE}}

EXPOSE ${PORT}
CMD ["sh", "-c", "npx next start -p ${PORT}"]
