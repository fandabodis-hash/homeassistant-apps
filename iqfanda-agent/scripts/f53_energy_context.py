"""Read-only cached energy context for F53, never simultaneous canary evidence.

The existing canary holds the inverter bus lock. Its surrounding cloud state
cannot establish PV/load/grid conditions during a phase or the energy source.
captured_at is the add-on's snapshot-processing timestamp, not a per-register
measurement time; received_at and updated_at are backend ingestion metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math


MAX_CAPTURE_AGE_SECONDS = 180
# Units and permitted sign metadata match the existing foxess_h3_10_0_e
# profile. Signs are exported only when the stored telemetry reports them.
ENTITY_SPECS = {
    "pv1.vykon": ("W", "positive_generation"),
    "pv2.vykon": ("W", "positive_generation"),
    "spotreba.vykon_celkem": ("W", "positive_consumption"),
    "smartmeter.vykon_celkem": ("W", "negative_import_positive_export"),
    "baterie.vykon": ("W", "negative_charging_positive_discharging"),
    "baterie.soc": ("%", None),
    "stridac.vykon_celkem": ("W", "positive_generation"),
}


def _timestamp(base, value):
    if value is None:
        return None, None, "missing"
    if not isinstance(value, datetime):
        return None, None, "invalid"
    encoded = value.isoformat()
    if value.tzinfo is None or value.utcoffset() is None:
        return encoded, None, "timezone_missing"
    normalized = base.aware(value)
    if not isinstance(normalized, datetime) or normalized.utcoffset() is None:
        return encoded, None, "invalid"
    return encoded, normalized.astimezone(timezone.utc), "valid"


def _record(base, key, row, observed_at, missing_reason="entity_missing"):
    unit, expected_sign = ENTITY_SPECS[key]
    raw = getattr(row, "value", None)
    numeric = type(raw) is int or (type(raw) is float and math.isfinite(raw))
    result = {
        "entity_key": key, "present": row is not None,
        "value": raw if numeric else None, "expected_unit": unit,
        "unit": getattr(row, "unit", None), "quality": getattr(row, "quality", None),
        "source": getattr(row, "source", None), "issues": [],
    }
    issues = result["issues"]
    if row is None:
        issues.append(missing_reason)
    elif raw is None:
        issues.append("value_missing")
    elif not numeric:
        issues.append("value_not_finite_number")
    if numeric and key == "baterie.soc" and not 0 <= raw <= 100:
        issues.append("value_out_of_range")
    if row is not None and result["unit"] != unit:
        issues.append("unit_mismatch")
    if row is not None and result["quality"] != "good":
        issues.append("quality_not_good")
    if row is not None and not result["source"]:
        issues.append("source_missing")

    stamps = {}
    timestamp_status = {}
    for name in ("captured_at", "received_at", "updated_at"):
        encoded, normalized, status = _timestamp(base, getattr(row, name, None))
        result[name], stamps[name], timestamp_status[name] = encoded, normalized, status
    result["timestamp_status"] = timestamp_status
    captured, received = stamps["captured_at"], stamps["received_at"]
    age = (observed_at - captured).total_seconds() if captured is not None else None
    receive_age = (observed_at - received).total_seconds() if received is not None else None
    lag = (received - captured).total_seconds() if captured is not None and received is not None else None
    result.update(capture_age_seconds=age, receive_age_seconds=receive_age, ingestion_lag_seconds=lag)
    capture_status = timestamp_status["captured_at"]
    if age is not None:
        capture_status = "future" if age < 0 else "stale" if age > MAX_CAPTURE_AGE_SECONDS else "recent_timestamp"
    result["capture_timestamp_status"] = capture_status
    result["clock_uncertain"] = any(value is not None and value < 0 for value in (age, receive_age, lag))
    if row is not None and capture_status != "recent_timestamp":
        issues.append("capture_timestamp_" + capture_status)
    if row is not None and timestamp_status["received_at"] != "valid":
        issues.append("received_timestamp_" + timestamp_status["received_at"])
    if result["clock_uncertain"]:
        issues.append("clock_or_ingestion_order_uncertain")

    attributes = getattr(row, "attributes", None)
    reported_sign = attributes.get("sign_convention") if isinstance(attributes, dict) else None
    result["sign_convention"] = expected_sign if expected_sign is not None and reported_sign == expected_sign else None
    result["sign_convention_status"] = (
        "not_applicable" if expected_sign is None else
        "missing" if reported_sign is None else "reported" if reported_sign == expected_sign else "unexpected"
    )
    if row is not None and result["sign_convention_status"] == "unexpected":
        issues.append("unexpected_sign_convention")
    if row is not None and result["sign_convention_status"] == "missing":
        issues.append("missing_sign_convention")
    result["usable_numeric_context"] = row is not None and numeric and not issues
    return result


def capture_energy_context(base, stage):
    """Read only the selected device's seven allowlisted photovoltaic states.

    base supplies SessionLocal, select/text, selected, now/aware and ORM models.
    A fresh short transaction is opened on every call; no commands or writes
    are issued. Missing, stale or failed context never becomes a physical PASS.
    """
    observed_at = None
    result = {
        "schema_version": 1, "stage": stage, "status": "unavailable",
        "context_only": True, "concurrent_measurement_confirmed": False,
        "observed_at_utc": None, "query_started_at_utc": None, "serial": None,
        "device_id": None, "site_id": None, "installation_module_id": None,
        "max_capture_age_seconds": MAX_CAPTURE_AGE_SECONDS,
        "freshness_basis": "captured_at_snapshot_processing_timestamp",
        "entities": [],
    }
    rows_by_key = {}
    missing_reason = "entity_missing"
    try:
        _, query_started, clock_status = _timestamp(base, base.now())
        if clock_status != "valid":
            raise ValueError("Energy context requires an aware observation clock")
        result["query_started_at_utc"] = query_started.isoformat()
        with base.SessionLocal() as db:
            db.execute(base.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            db.execute(base.text("SET LOCAL statement_timeout = '5000ms'"))
            device = base.selected(db)
            if device is None or device.id is None or device.site_id is None:
                raise ValueError("Target identity is missing")
            result.update(serial=device.serial_number, device_id=str(device.id), site_id=str(device.site_id))
            photovoltaic = db.scalar(base.select(base.InstallationModule).where(
                base.InstallationModule.site_id == device.site_id,
                base.InstallationModule.module_key == "photovoltaic"))
            if photovoltaic is None:
                result["status"] = missing_reason = "photovoltaic_module_missing"
            else:
                result["installation_module_id"] = str(photovoltaic.id)
                rows = db.scalars(base.select(base.ModuleEntityState).where(
                    base.ModuleEntityState.installation_module_id == photovoltaic.id,
                    base.ModuleEntityState.source_device_id == device.id,
                    base.ModuleEntityState.entity_key.in_(tuple(ENTITY_SPECS)),
                ).limit(2 * len(ENTITY_SPECS) + 1)).all()
                if len(rows) > 2 * len(ENTITY_SPECS):
                    raise ValueError("Energy context row limit exceeded")
                for row in rows:
                    if (row.installation_module_id != photovoltaic.id or
                            row.source_device_id != device.id or row.entity_key not in ENTITY_SPECS):
                        raise ValueError("Energy context identity scope mismatch")
                    rows_by_key.setdefault(row.entity_key, []).append(row)
                result["status"] = "captured"
            _, observed_at, clock_status = _timestamp(base, base.now())
            if clock_status != "valid":
                raise ValueError("Energy context requires an aware observation clock")
            result["observed_at_utc"] = observed_at.isoformat()
            # Use one common observation clock after the reads, and materialize
            # while this transaction owns the ORM objects.
            if result["status"] == "captured":
                result["entities"] = [
                    _record(base, key, items[0] if len(items) == 1 else None, observed_at,
                            "ambiguous_entity" if len(items) > 1 else "entity_missing")
                    for key in ENTITY_SPECS for items in [rows_by_key.get(key, [])]
                ]
    except Exception:
        # Connection/ORM errors may contain credentials. Context is ancillary;
        # export a fixed failure code, never the exception or partial rows.
        result["status"] = missing_reason = "energy_context_read_failed"
        result["entities"] = []
    if not result["entities"]:
        if observed_at is None:
            try:
                _, observed_at, _ = _timestamp(base, base.now())
                result["observed_at_utc"] = observed_at.isoformat() if observed_at is not None else None
            except Exception:
                pass
        result["entities"] = [_record(base, key, None, observed_at, missing_reason) for key in ENTITY_SPECS]
    result["missing_entity_keys"] = [row["entity_key"] for row in result["entities"] if not row["present"]]
    return result
