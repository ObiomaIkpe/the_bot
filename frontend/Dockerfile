# Multi-stage build: compile the React app with Node, serve the static
# output with nginx. This is the frontend's first-ever containerization
# -- previously there was no Dockerfile/deploy path for it at all (see
# DEPLOY_PLAN.md, 2026-09-02). `frontend/.env` (VITE_API_BASE_URL) must
# exist on disk BEFORE this builds -- Vite bakes env vars into the
# static output at build time, not read at runtime, so it can't be
# supplied later via docker-compose's `environment:` like the other
# services here.
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
