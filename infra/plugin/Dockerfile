###############################
# Multi-stage build for Mattermost plugin (mm-importer)
# Stages:
# 1. base-node: install node deps (cached)
# 2. webapp-build: build React/webpack bundle
# 3. go-build: compile Go server binary (linux/amd64)
# 4. package: assemble final plugin directory and produce tarball
#
# Usage (outputs tarball to local dist/ using BuildKit):
#   DOCKER_BUILDKIT=1 docker build \
#     -f infra/plugin/Dockerfile \
#     --target package \
#     --output type=local,dest=infra/plugin/dist \
#     infra/plugin
# Result: infra/plugin/dist/mm-importer-<version>.tar.gz
###############################

# syntax=docker/dockerfile:1.7-labs

ARG GO_VERSION=1.22
ARG NODE_VERSION=20-alpine

###############################
# Stage: base-node (deps)
###############################
FROM node:${NODE_VERSION} AS base-node
WORKDIR /src/webapp

# Copy only package manifests first for better layer cache reuse
COPY webapp/package.json webapp/package-lock.json ./

# Install dependencies (no audit/fund to speed up; respect lockfile)
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

###############################
# Stage: webapp-build
###############################
FROM node:${NODE_VERSION} AS webapp-build
WORKDIR /src/webapp

COPY --from=base-node /src/webapp/node_modules ./node_modules
COPY plugin.json /src/plugin.json
COPY webapp/ ./

# Build production bundle
RUN --mount=type=cache,target=/root/.npm \
    npm run build

###############################
# Stage: go-build
###############################
FROM golang:${GO_VERSION}-alpine AS go-build
WORKDIR /src
RUN apk add --no-cache git ca-certificates

ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64 GOFLAGS="-buildvcs=false"

COPY server/ ./server/
COPY go.mod go.sum ./
COPY plugin.json .

WORKDIR /src/server
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
    go build -trimpath -o dist/plugin-linux-amd64

###############################
# Stage: package
###############################
FROM alpine:3.20 AS package
WORKDIR /package

# Install tar just in case (busybox has tar, but ensure gzip too)
RUN apk add --no-cache ca-certificates

# Copy plugin metadata + assets
COPY plugin.json ./
# Optional assets/public (copy later in RUN to avoid build failure if absent)
COPY . ./__context__
RUN set -e; \
    if [ -d __context__/assets ]; then cp -r __context__/assets ./assets; fi; \
    if [ -d __context__/public ]; then cp -r __context__/public ./public; fi; \
    rm -rf __context__

# Copy built artifacts
COPY --from=go-build /src/server/dist ./server/dist
COPY --from=webapp-build /src/webapp/dist ./webapp/dist

# Determine id/version (simple awk parse, no node needed)
RUN set -euo pipefail; \
    ID=$(awk -F '"' '/"id"/ {print $4; exit}' plugin.json); \
    VER=$(awk -F '"' '/"version"/ {print $4; exit}' plugin.json); \
    [ -n "$ID" ] && [ -n "$VER" ]; \
    mkdir -p dist/${ID}/server dist/${ID}/webapp; \
    cp -r server/dist dist/${ID}/server/; \
    if [ -d webapp/dist ]; then cp -r webapp/dist dist/${ID}/webapp/; fi; \
    [ -d public ] && cp -r public dist/${ID}/ || true; \
    [ -d assets ] && cp -r assets dist/${ID}/ || true; \
    cp plugin.json dist/${ID}/plugin.json; \
    tar -C dist -czf ${ID}-${VER}.tar.gz ${ID}; \
    mv ${ID}-${VER}.tar.gz dist/; \
    echo "Created ${ID}-${VER}.tar.gz"; \
    ls -lah

# When using BuildKit --output=type=local,dest=... we'll copy all *.tar.gz out
CMD ["/bin/sh", "-c", "ls -1 *.tar.gz"]
