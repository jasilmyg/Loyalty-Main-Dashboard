"""
Persistence + lookup of the backend-trained demand model.

The trained LightGBM booster (plus its learned blend weight, calibration factor, conformal scales,
categorical vocabularies and out-of-time holdout metrics) is stored

  1. in ClickHouse table `demand_model_store` (shared by every web worker / deployment), and
  2. in a local cache file (fallback when ClickHouse is unreachable).

`get_pretrained_model()` is what `get_demand_intelligence_data` calls on every request; it is cached in
memory and re-checked every few minutes, so a retrain is picked up without restarting the server.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import zlib
from datetime import datetime
from typing import Any, Dict, Optional

from .demand_ml_engine import PretrainedDemandModel

logger = logging.getLogger(__name__)

MODEL_TABLE = "demand_model_store"
MODEL_NAME_SKU = "demand_sku"
CACHE_TTL_SEC = 600

_DDL = f"""
CREATE TABLE IF NOT EXISTS {MODEL_TABLE}
(
    model_name   LowCardinality(String),
    trained_at   DateTime DEFAULT now(),
    ref_date     Date,
    n_samples    UInt32,
    metrics_json String,
    payload      String
)
ENGINE = MergeTree
ORDER BY (model_name, trained_at)
"""

_mem: Dict[str, Any] = {}
_lock = threading.Lock()


def _local_path(name: str) -> str:
    try:
        from django.conf import settings
        base = str(getattr(settings, "BASE_DIR", "."))
    except Exception:  # noqa: BLE001
        base = "."
    d = os.path.join(base, "analytics", "model_cache")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{name}.model.z")


def _encode(model: PretrainedDemandModel) -> str:
    raw = json.dumps(model.to_dict(), separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def _decode(payload: str) -> PretrainedDemandModel:
    raw = zlib.decompress(base64.b64decode(payload.encode("ascii")))
    return PretrainedDemandModel.from_dict(json.loads(raw.decode("utf-8")))


def _ch_client():
    from analytics.starrocks_service import get_ch_backup_client
    return get_ch_backup_client()


def save_model(model: PretrainedDemandModel, name: str = MODEL_NAME_SKU) -> Dict[str, Any]:
    """Persist to ClickHouse (authoritative) and to the local file cache."""
    payload = _encode(model)
    info: Dict[str, Any] = {"bytes": len(payload), "clickhouse": False, "local": False}
    try:
        with open(_local_path(name), "w", encoding="ascii") as f:
            f.write(payload)
        info["local"] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("local model cache write failed: %s", e)
    try:
        ch = _ch_client()
        ch.command(_DDL)
        ref = datetime.strptime(model.ref_date, "%Y-%m-%d").date() if model.ref_date else datetime.utcnow().date()
        ch.insert(
            MODEL_TABLE,
            [[name, datetime.utcnow(), ref, int(model.n_samples), json.dumps(model.metrics, default=str), payload]],
            column_names=["model_name", "trained_at", "ref_date", "n_samples", "metrics_json", "payload"],
        )
        info["clickhouse"] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("ClickHouse model store write failed: %s", e)
        info["clickhouse_error"] = str(e)[:300]
    with _lock:
        _mem[name] = {"ts": time.time(), "model": model}
    return info


def load_model(name: str = MODEL_NAME_SKU, force: bool = False) -> Optional[PretrainedDemandModel]:
    """Latest stored model (ClickHouse first, local file as fallback). Cached in memory for CACHE_TTL_SEC."""
    with _lock:
        hit = _mem.get(name)
        if hit and not force and (time.time() - hit["ts"]) < CACHE_TTL_SEC:
            return hit["model"]

    model: Optional[PretrainedDemandModel] = None
    try:
        rows = _ch_client().query(
            f"SELECT payload FROM {MODEL_TABLE} WHERE model_name = {{n:String}} ORDER BY trained_at DESC LIMIT 1",
            parameters={"n": name},
        ).result_rows
        if rows and rows[0][0]:
            model = _decode(rows[0][0])
    except Exception as e:  # noqa: BLE001
        logger.info("model store (ClickHouse) unavailable (%s) - trying the local cache file", str(e)[:120])
    if model is None:
        try:
            p = _local_path(name)
            if os.path.exists(p):
                with open(p, "r", encoding="ascii") as f:
                    model = _decode(f.read())
        except Exception as e:  # noqa: BLE001
            logger.warning("local model cache read failed: %s", e)

    with _lock:
        _mem[name] = {"ts": time.time(), "model": model}
    return model


def get_pretrained_model(gran: str = "sku") -> Optional[PretrainedDemandModel]:
    """Model for the requested granularity; only the SKU-level model is trained offline."""
    if gran != "sku":
        return None
    try:
        return load_model(MODEL_NAME_SKU)
    except Exception as e:  # noqa: BLE001
        logger.warning("get_pretrained_model failed: %s", e)
        return None
