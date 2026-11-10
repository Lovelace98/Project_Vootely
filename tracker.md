# Project VoteCentral — Tracker

## Current Sprint: Infrastructure Migration — DO App Platform → Netcup VPS + Dokploy

### Active Task Scenario
Provision Netcup VPS 1000 G12 (x86), migrate from Supabase to self-hosted Postgres, deploy full stack (Django + Celery + Redis + Postgres) via Dokploy.

---

## Agent Progress Log

### 2026-06-21 — Initial: Provider Research & Decision

**Decision:** Netcup VPS 1000 G12 (x86) — €8.71/mo ex-VAT (~$9.30)
- 4 shared x86 vCores, 8 GB DDR5 ECC RAM, 256 GB NVMe, unmetered traffic
- Hourly billing, snapshots (Copy-on-Write), remote console, API, DDoS protection
- Datacenter: Nuremberg, Germany (EU, ~100–130ms to Ghana)

**Rationale over alternatives:**
- Hetzner CX33 (€4.49/mo): cheaper but 80 GB NVMe insufficient for Postgres + media
- Netcup ARM G11 (€6.53/mo): better specs/$ but ARM compatibility risk with 3rd-party Python C extensions
- Contabo (€3.83/mo): CPU overselling + 200 Mbps port too slow for production
- Linode ($48/mo): 7× price for same spec
- OVH ($4.20/mo): viable but less ecosystem than Netcup

**Remaining Supabase components to replace:**
- [x] Postgres → Self-hosted via Dokploy
- [x] S3 Storage → Local `media_volume` for the single-server launch (S3/MinIO remains the scale path)

### 2026-06-21 — Change: CONN_MAX_AGE=0 for PgBouncer Transaction Mode

**File:** `votecentral/settings.py:264`
- Changed `conn_max_age=600` → `0` to work with PgBouncer transaction pooling in production
- PgBouncer requires `conn_max_age=0` so Django doesn't hold persistent connections across transactions

### 2026-06-21 — Change: Added Postgres + MinIO to docker-compose.yml

- Added `votecentral_postgres_service` (PostgreSQL 16 with pgvector for future AI features)
- Added `votecentral_minio_service` (S3-compatible object storage to replace Supabase Storage)
- Updated `web`, `worker`, `beat` depends_on to include postgres and redis health checks
- Services use `dokploy-network` for inter-service communication in Dokploy

### 2026-06-21 — Change: Updated .env.example for self-hosted stack

- Replaced Supabase DATABASE_URL template with local Postgres connection string
- Replaced Supabase S3 config with MinIO endpoint + credentials
- Added `POSTGRES_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` vars

---


### 2026-11-10 — Launch Hardening: Error Logs, Media Config, Compose, Secure Codes

**Files:** `votecentral/views.py`, `votecentral/request_utils.py`, `votecentral/rate_limits.py`, `votecentral/settings.py`, `votecentral/urls.py`, `votecentral/tests.py`, `.env.example`, `docker-compose.yml`, `.dockerignore`, `.gitignore`, `nominees/models.py`, `wallets/views.py`, `payments/services.py`

**Changes:**
- Custom 404 handler now logs `Resolver404` as `Route does not exist` instead of dumping the full URLconf into production logs on every bot scan.
- Added `/healthz/` liveness route, exempted from SSL redirect; added a web healthcheck to compose.
- Media default is now the local `media_volume` (`SERVE_LOCAL_MEDIA=True`); removed the stale MinIO/AWS variables from `.env.example` because the MinIO service no longer exists.
- Compose hardening: required `POSTGRES_PASSWORD`, Redis AOF persistence, JSON log rotation, stop grace periods, `init`, lower Celery concurrency with task recycling, and a daily `pg_dump` backup sidecar writing to `./backups`.
- Replaced `random` with `secrets` for withdrawal OTPs and nominee vote codes.
- `TRUSTED_PROXY_COUNT` now controls how `X-Forwarded-For` is resolved for rate limits and error logs; default 1 (Traefik), set 2 behind Cloudflare.
- `.dockerignore` excludes local media, staticfiles, scratch scripts, backups, and test artifacts.
- Added regression tests for the concise 404 log, `/healthz/`, and proxy-depth IP resolution.

**Verification:** full suite passes across all 10 apps (244 tests); `manage.py check` and `docker compose config` pass.

---

## Completed Features (pre-sprint)
- N+1 query fixes across 27 files (commit `8ed88f5`)
- Election live results frontend removed
- Legal pages synced (TOS + Privacy Policy)
- SECRET_KEY build-time RecursionError resolved
- Comprehensive Redis caching (6 cache builders + 6 signal receivers in events/)
- Views wired: HomeView, EventDetailView, PublicElection*, Dashboard*

## Key Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-21 | Netcup VPS 1000 G12 (x86) over Hetzner CX33 | 256 GB NVMe vs 80 GB; DDR5 ECC; snapshots; unmetered traffic |
| 2026-06-21 | Self-host Postgres in Dokploy over Supabase Pro | Save $25/mo; no 7-day pause risk |
| 2026-06-21 | CONN_MAX_AGE=0 for PgBouncer | Required for transaction pooling with self-hosted Postgres |
| 2026-06-21 | MinIO over Hetzner Object Storage for S3 | $0 vs €4.99/mo; full S3 API compatibility |
| 2026-11-10 | Local media volume for single-server launch | MinIO service removed; local media works through `SERVE_LOCAL_MEDIA`; S3/MinIO remains the scale path |
