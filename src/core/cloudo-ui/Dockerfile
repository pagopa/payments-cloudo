# Multi-stage Dockerfile for ClouDO Next.js Frontend
# This builds the Next.js frontend in standalone mode for production

# ============================================
# Stage 1: Build Next.js
# ============================================
FROM node:22-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f AS builder

WORKDIR /app

# Disable telemetry during build
ENV NEXT_TELEMETRY_DISABLED=1

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . ./

# Build Next.js for production
# Note: Next.js 'standalone' output must be enabled in next.config.ts/js
ENV NODE_ENV=production
RUN npm run build

# ============================================
# Stage 2: Runtime
# ============================================
FROM node:22-alpine@sha256:8ea2348b068a9544dae7317b4f3aafcdc032df1647bb7d768a05a5cad1a7683f AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Create a non-root user for security
RUN addgroup --system --gid 1001 nodejs
RUN adduser --system --uid 1001 nextjs

# Copy necessary files from builder
# With output: 'standalone', Next.js puts everything in .next/standalone
COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Check if server.js is in a subdirectory and move it to /app if necessary
# This happens when the project is not in the root of the builder WORKDIR
RUN if [ ! -f server.js ]; then \
    find . -name server.js -type f | grep -q "." && \
    SERVER_PATH=$(find . -name server.js -type f | head -n 1) && \
    DIR_PATH=$(dirname $SERVER_PATH) && \
    cp -r $DIR_PATH/. ./ && \
    rm -rf $DIR_PATH; \
    fi

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# server.js is created by next build from the standalone output
CMD ["node", "server.js"]
