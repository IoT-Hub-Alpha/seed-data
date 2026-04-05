# Seed Data Service

Standalone test data loader for IoT Hub Alpha. Automatically populates the PostgreSQL database with sample DeviceTypes, Devices, Rules, NotificationTemplates, Telemetry, and TelemetrySchema on startup.

## Overview

- **No Django dependency** — uses only `psycopg2`
- **Fully idempotent** — safe to run multiple times, uses `ON CONFLICT DO UPDATE` and `WHERE NOT EXISTS`
- **Portable** — can be used by any project with the same PostgreSQL schema
- **One-shot service** — runs once and exits (restart: "no" in docker-compose)

## Architecture

```
docker compose up
    ├─ db (service_healthy)
    ├─ migrate (service_completed_successfully) ← schema must exist
    └─ seed-data (runs once, loads JSON to PostgreSQL)
```

## Seed Data

The `seed_data.json` file contains:

| Entity | Count | Upsert Key |
|--------|-------|-----------|
| `device_types` | 6 | `name` (UNIQUE) |
| `devices` | 8 | `serial_number` (UNIQUE) |
| `notification_templates` | 5 | `name` (UNIQUE) |
| `telemetry_schema` | 1 | `version` (UNIQUE) |
| `rules` | 9 | `(device_id, name)` (WHERE NOT EXISTS) |
| `telemetry` | 3 | `(device_id, payload)` (WHERE NOT EXISTS) |

## Usage

### Docker Compose (Automatic)

The service runs automatically on `docker compose up`:

```bash
docker compose up  # seed-data waits for db → migrate → runs → exits
```

Check logs:
```bash
docker compose logs seed-data
```

Expected output:
```
[seed] Starting seed-data loader...
[seed] ✓ Database ready
[seed] device_types:           6 upserted
[seed] notification_templates: 5 upserted
[seed] telemetry_schema:       1 upserted
[seed] devices:                8 upserted
[seed] rules:                  9 inserted, 0 skipped
[seed] telemetry:              3 inserted, 0 skipped
[seed] ✓ Done.
```

### Manual Run

```bash
# Run from repository root
docker compose run --rm seed-data
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_HOST` | `db` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `iot_hub_alpha_db` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |

All are read from `env_file: .env` in docker-compose.

## Implementation Details

`seed.py` is a standalone Python script that:

1. **Waits for DB** — retries for up to 60s with psycopg2.connect()
2. **Loads seed_data.json** — parses all entity types
3. **Seeds in dependency order** (one transaction):
   - device_types (no FKs)
   - notification_templates (no FKs)
   - telemetry_schema (no FKs)
   - devices (FK → device_types)
   - rules (FK → devices)
   - telemetry (FK → devices)

4. **Idempotent upserts:**
   - `device_types`, `devices`, `notification_templates`, `telemetry_schema` use `ON CONFLICT (unique_key) DO UPDATE`
   - `rules`, `telemetry` use `WHERE NOT EXISTS` (no unique constraint)

5. **Outputs progress per entity** to stderr

## Portable to Other Projects

To use this seed-data service in another project:

1. Add this git submodule: `git submodule add https://github.com/IoT-Hub-Alpha/seed-data.git services/seed-data`
2. Add to your `docker-compose.yml`:
   ```yaml
   seed-data:
     build:
       context: services/seed-data
     env_file:
       - .env
     depends_on:
       db:
         condition: service_healthy
       migrate:  # or your migrations service
         condition: service_completed_successfully
     networks:
       - your_network_name
   ```
3. Ensure your `.env` has `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`

The schema must match (same table names, column names, and FK relationships).

## Modifying Seed Data

Edit `seed_data.json` and re-run:

```bash
docker compose run --rm seed-data
```

All changes will be applied idempotently (updates if exists, inserts if new, skips if already there).

## Notes

- **UUID PKs:** Generated server-side using `gen_random_uuid()` on INSERT only
- **Timestamps:** `created_at` and `updated_at` are set to `NOW()` at insertion
- **Payload:** Telemetry payload is stored as-is (raw integer values); transformation rules in TelemetrySchema define processing at query time
- **Transaction:** All seeding happens in one transaction; rolls back entirely on any error