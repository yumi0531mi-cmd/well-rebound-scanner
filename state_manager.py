"""Atomic PROGRESS.json recovery for resumable scanner work."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

PROGRESS_PATH = Path(__file__).with_name("PROGRESS.json")
REQUIRED_KEYS = {"schema_version", "status", "current_step", "completed_steps", "next_actions"}


def load_progress(path: str | Path = PROGRESS_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"복구 상태 파일 없음: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"복구 상태 파일 손상: {target}") from exc
    if not isinstance(payload, dict) or not REQUIRED_KEYS.issubset(payload):
        missing = sorted(REQUIRED_KEYS - set(payload) if isinstance(payload, dict) else REQUIRED_KEYS)
        raise ValueError(f"복구 상태 필수 키 누락: {missing}")
    if not isinstance(payload["completed_steps"], list) or not isinstance(payload["next_actions"], list):
        raise ValueError("completed_steps와 next_actions는 목록이어야 합니다")
    return payload


def save_progress(payload: dict[str, Any], path: str | Path = PROGRESS_PATH) -> None:
    target = Path(path)
    missing = sorted(REQUIRED_KEYS - set(payload))
    if missing:
        raise ValueError(f"복구 상태 필수 키 누락: {missing}")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
