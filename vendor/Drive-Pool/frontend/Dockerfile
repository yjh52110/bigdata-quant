# ── Stage 1: Install all deps and build the Next.js app ───────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

# Baked in at build time — Next.js server-side rewrites use this to reach the
# backend container via the internal Docker network name
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

# ── Stage 2: Production runtime — no dev dependencies, no source ───────────────
FROM node:20-alpine

WORKDIR /app

ENV NODE_ENV=production

# Reinstall only production deps (avoids copying the full node_modules from builder)
COPY --from=builder /app/package*.json ./
RUN npm ci --omit=dev

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["npx", "next", "start"]
