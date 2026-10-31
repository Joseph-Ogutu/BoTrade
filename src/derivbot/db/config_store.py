"""Persist configuration snapshots in config_versions, idempotent on the content hash (HANDBOOK 8)."""

from __future__ import annotations

import json

from pydantic import BaseModel
from sqlalchemy import Connection, text

from derivbot.config.risk import canonical, config_hash


def store_config(conn: Connection, kind: str, cfg: BaseModel) -> int:
    """Insert the canonical content once; return the existing id when the same content is stored again."""
    content = json.dumps(canonical(cfg), sort_keys=True)
    h = config_hash(cfg)
    conn.execute(
        text("INSERT INTO config_versions (kind, content, content_hash) VALUES (:k, CAST(:c AS jsonb), :h) "
             "ON CONFLICT (content_hash) DO NOTHING"),
        {"k": kind, "c": content, "h": h},
    )
    row = conn.execute(text("SELECT id, kind FROM config_versions WHERE content_hash = :h"), {"h": h}).one()
    if row.kind != kind:
        raise ValueError(f"content hash already stored under kind {row.kind!r}, not {kind!r}")
    return int(row.id)
