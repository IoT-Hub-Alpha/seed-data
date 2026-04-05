#!/usr/bin/env python3
"""
Standalone seed data loader for IoT Hub Alpha.
Reads seed_data.json and populates PostgreSQL with test data using psycopg2.
Fully idempotent — safe to run multiple times.
No Django dependency — portable to any project with this DB schema.
"""

import json
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg2
import psycopg2.extras


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "db"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "iot_hub_alpha_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

SEED_FILE = Path(__file__).parent / "seed_data.json"


# ─────────────────────────────────────────────────────────────────────────────
# DB Connection
# ─────────────────────────────────────────────────────────────────────────────

def wait_for_db():
    """Wait up to 60s for PostgreSQL to be ready."""
    deadline = time.time() + 60
    last_error = None

    while time.time() < deadline:
        try:
            conn = psycopg2.connect(**DB_CONFIG, connect_timeout=3)
            conn.close()
            print("[seed] ✓ Database ready", file=sys.stderr)
            return
        except Exception as e:
            last_error = e
            remaining = int(deadline - time.time())
            print(
                f"[seed] DB not ready: {e}. Retrying... ({remaining}s remaining)",
                file=sys.stderr,
            )
            time.sleep(2)

    print(f"[seed] ✗ Database not ready after 60s: {last_error}", file=sys.stderr)
    sys.exit(1)


def get_connection():
    """Get a new DB connection."""
    return psycopg2.connect(**DB_CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# Seed Functions
# ─────────────────────────────────────────────────────────────────────────────

def seed_device_types(cursor, data):
    """
    Upsert device_types.
    Returns: {name → uuid} map for FK resolution in devices.
    """
    cursor.execute("SET search_path TO public")

    device_types_map = {}

    for dt in data.get("device_types", []):
        device_id = str(uuid.uuid4())
        cursor.execute(
            """
            INSERT INTO device_types
              (id, name, description, metric_name, metric_unit, metric_min, metric_max, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (name) DO UPDATE SET
              description = EXCLUDED.description,
              metric_name = EXCLUDED.metric_name,
              metric_unit = EXCLUDED.metric_unit,
              metric_min = EXCLUDED.metric_min,
              metric_max = EXCLUDED.metric_max
            RETURNING id, name
            """,
            (
                device_id,
                dt["name"],
                dt.get("description"),
                dt["metric_name"],
                dt["metric_unit"],
                Decimal(dt["metric_min"]) if dt.get("metric_min") else None,
                Decimal(dt["metric_max"]) if dt.get("metric_max") else None,
            ),
        )
        row = cursor.fetchone()
        if row:
            device_types_map[dt["name"]] = row[0]

    print(
        f"[seed] device_types:           {len(data.get('device_types', []))} upserted",
        file=sys.stderr,
    )
    return device_types_map


def seed_notification_templates(cursor, data):
    """Upsert notification_templates."""
    for nt in data.get("notification_templates", []):
        cursor.execute(
            """
            INSERT INTO notification_templates
              (name, message_template, recipients, priority, retry_count, retry_delay_minutes, is_active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (name) DO UPDATE SET
              message_template = EXCLUDED.message_template,
              recipients = EXCLUDED.recipients,
              priority = EXCLUDED.priority,
              retry_count = EXCLUDED.retry_count,
              retry_delay_minutes = EXCLUDED.retry_delay_minutes,
              is_active = EXCLUDED.is_active,
              updated_at = NOW()
            """,
            (
                nt["name"],
                nt["message_template"],
                json.dumps(nt["recipients"]),
                nt["priority"],
                nt["retry_count"],
                nt["retry_delay_minutes"],
                nt["is_active"],
            ),
        )

    print(
        f"[seed] notification_templates: {len(data.get('notification_templates', []))} upserted",
        file=sys.stderr,
    )


def seed_telemetry_schema(cursor, data):
    """Upsert telemetry_schema."""
    for ts in data.get("telemetry_schema", []):
        cursor.execute(
            """
            INSERT INTO telemetry_schema
              (version, validation_schema, transformation_rules, is_active)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (version) DO UPDATE SET
              validation_schema = EXCLUDED.validation_schema,
              transformation_rules = EXCLUDED.transformation_rules,
              is_active = EXCLUDED.is_active
            """,
            (
                ts["version"],
                json.dumps(ts["validation_schema"]),
                json.dumps(ts["transformation_rules"]),
                ts["is_active"],
            ),
        )

    print(
        f"[seed] telemetry_schema:       {len(data.get('telemetry_schema', []))} upserted",
        file=sys.stderr,
    )


def seed_devices(cursor, data, device_types_map):
    """
    Upsert devices using device_types_map for FK resolution.
    Returns: {serial_number → uuid} map for FK resolution in rules/telemetry.
    """
    devices_map = {}

    for dev in data.get("devices", []):
        device_id = str(uuid.uuid4())
        device_type_id = device_types_map.get(dev["device_type"])

        if not device_type_id:
            print(
                f"[seed] ✗ Device {dev['serial_number']}: device_type '{dev['device_type']}' not found",
                file=sys.stderr,
            )
            continue

        cursor.execute(
            """
            INSERT INTO devices
              (id, device_type_id, name, serial_number, location, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (serial_number) DO UPDATE SET
              device_type_id = EXCLUDED.device_type_id,
              name = EXCLUDED.name,
              location = EXCLUDED.location,
              status = EXCLUDED.status,
              updated_at = NOW()
            RETURNING id, serial_number
            """,
            (
                device_id,
                device_type_id,
                dev["name"],
                dev["serial_number"],
                dev.get("location"),
                dev["status"],
            ),
        )
        row = cursor.fetchone()
        if row:
            devices_map[dev["serial_number"]] = row[0]

    print(
        f"[seed] devices:                {len(data.get('devices', []))} upserted",
        file=sys.stderr,
    )
    return devices_map


def seed_rules(cursor, data, devices_map):
    """
    Insert rules where not exists (no UNIQUE constraint on (name, device_id)).
    """
    inserted = 0
    skipped = 0

    for rule in data.get("rules", []):
        device_id = devices_map.get(rule["device"])

        if not device_id:
            print(
                f"[seed] ✗ Rule {rule['name']}: device '{rule['device']}' not found",
                file=sys.stderr,
            )
            continue

        # Check if rule already exists
        cursor.execute(
            "SELECT 1 FROM rules WHERE device_id = %s AND name = %s",
            (device_id, rule["name"]),
        )
        if cursor.fetchone():
            skipped += 1
            continue

        cursor.execute(
            """
            INSERT INTO rules
              (id, device_id, name, description, condition, action_config, is_enabled, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            """,
            (
                str(uuid.uuid4()),
                device_id,
                rule["name"],
                rule.get("description"),
                json.dumps(rule["condition"]),
                json.dumps(rule["action_config"]),
                rule.get("is_enabled", True),
            ),
        )
        inserted += 1

    print(
        f"[seed] rules:                  {inserted} inserted, {skipped} skipped",
        file=sys.stderr,
    )


def seed_telemetry(cursor, data, devices_map):
    """
    Insert telemetry where not exists (no UNIQUE constraint on (device_id, payload)).
    Payload is stored as-is from JSON (raw integer values).
    """
    inserted = 0
    skipped = 0

    for tel in data.get("telemetry", []):
        device_id = devices_map.get(tel["device"])

        if not device_id:
            print(
                f"[seed] ✗ Telemetry for '{tel['device']}': device not found",
                file=sys.stderr,
            )
            continue

        payload_json = json.dumps(tel["payload"])

        # Check if telemetry already exists for this device with this payload
        cursor.execute(
            "SELECT 1 FROM telemetry WHERE device_id = %s AND payload = %s::jsonb",
            (device_id, payload_json),
        )
        if cursor.fetchone():
            skipped += 1
            continue

        cursor.execute(
            """
            INSERT INTO telemetry
              (device_id, timestamp, payload)
            VALUES (%s, NOW(), %s::jsonb)
            """,
            (device_id, payload_json),
        )
        inserted += 1

    print(
        f"[seed] telemetry:              {inserted} inserted, {skipped} skipped",
        file=sys.stderr,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Load and execute seed data."""
    print("[seed] Starting seed-data loader...", file=sys.stderr)

    # Wait for DB
    wait_for_db()

    # Load seed data
    if not SEED_FILE.exists():
        print(f"[seed] ✗ Seed file not found: {SEED_FILE}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(SEED_FILE) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"[seed] ✗ Invalid JSON in seed file: {e}", file=sys.stderr)
        sys.exit(1)

    # Connect to DB and seed
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # All seeding in one transaction
        try:
            device_types_map = seed_device_types(cursor, data)
            seed_notification_templates(cursor, data)
            seed_telemetry_schema(cursor, data)
            devices_map = seed_devices(cursor, data, device_types_map)
            seed_rules(cursor, data, devices_map)
            seed_telemetry(cursor, data, devices_map)

            conn.commit()
            print("[seed] ✓ Done.", file=sys.stderr)

        except Exception as e:
            conn.rollback()
            print(f"[seed] ✗ Error during seeding: {e}", file=sys.stderr)
            raise

        finally:
            cursor.close()
            conn.close()

    except Exception as e:
        print(f"[seed] ✗ Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()