# LiteMCP frontend development image (M0-BOOT-001).
#
# Runs the Vite dev server on :5173 for local development
# (docs/architecture/00-overview.md §5.2/§5.3). The source and lockfile are
# baked in; `docker compose up` runs `npm run dev -- --host 0.0.0.0`.
# frontend/node_modules is excluded from the build context via .dockerignore
# and installed here with `npm ci` from the committed lockfile.

FROM node:22-alpine

WORKDIR /app

# Install from the lockfile first so dependency-layer changes are cached.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
