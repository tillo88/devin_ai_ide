from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ACCESS_INDEX = '.log_access.json'


def _utc_now() -> float:
    return datetime.now(timezone.utc).timestamp()


@dataclass(frozen=True)
class LogRetentionPolicy:
    enabled: bool = True
    retention_days: int = 14
    keep_recent_runs: int = 50

    @classmethod
    def from_env(cls) -> 'LogRetentionPolicy':
        enabled = os.getenv('DEVIN_LOG_AUTOCLEAN', '1').strip().lower() not in {'0', 'false', 'no', 'off'}
        retention_days = _safe_int(os.getenv('DEVIN_LOG_RETENTION_DAYS'), 14, minimum=1, maximum=3650)
        keep_recent_runs = _safe_int(os.getenv('DEVIN_LOG_KEEP_RECENT_RUNS'), 50, minimum=0, maximum=10000)
        return cls(enabled=enabled, retention_days=retention_days, keep_recent_runs=keep_recent_runs)


def _safe_int(value: str | None, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _load_access_index(log_dir: Path) -> dict[str, float]:
    path = log_dir / ACCESS_INDEX
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or "/" in key or chr(92) in key:
            continue
        try:
            out[key] = float(value)
        except Exception:
            continue
    return out


def _save_access_index(log_dir: Path, data: dict[str, float]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / ACCESS_INDEX
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')


def mark_log_opened(log_dir: str | Path, filename: str, *, now: float | None = None) -> None:
    if not filename or "/" in filename or chr(92) in filename:
        return
    root = Path(log_dir)
    data = _load_access_index(root)
    data[filename] = float(now if now is not None else _utc_now())
    _save_access_index(root, data)


def _is_cleanup_candidate(path: Path) -> bool:
    name = path.name
    if name == ACCESS_INDEX or name.startswith('.'):
        return False
    if path.suffix == '.log':
        return True
    if name.endswith('.events.jsonl'):
        return True
    if '_attempt' in name and (name.endswith('_patch.diff') or name.endswith('_files.txt')):
        return True
    return False


def _run_id_for(path: Path) -> str:
    name = path.name
    if name.startswith('run_') and name.endswith('.events.jsonl'):
        return name[: -len('.events.jsonl')]
    if name.startswith('run_') and '_attempt' in name:
        return name.split('_attempt', 1)[0]
    if name.startswith('run_') and path.suffix == '.log':
        return path.stem
    return ''


def cleanup_logs(
    log_dir: str | Path,
    *,
    policy: LogRetentionPolicy | None = None,
    active_run_ids: Iterable[str] = (),
    now: float | None = None,
    dry_run: bool = False,
) -> dict:
    root = Path(log_dir)
    policy = policy or LogRetentionPolicy.from_env()
    now_ts = float(now if now is not None else _utc_now())
    summary = {
        'enabled': policy.enabled,
        'dry_run': dry_run,
        'retention_days': policy.retention_days,
        'keep_recent_runs': policy.keep_recent_runs,
        'log_dir': str(root),
        'scanned': 0,
        'deleted': 0,
        'would_delete': 0,
        'kept': 0,
        'bytes_deleted': 0,
        'candidates': [],
        'protected_run_ids': [],
    }
    if not root.exists() or not policy.enabled:
        return summary

    active = {str(item) for item in active_run_ids}
    access = _load_access_index(root)
    files = [item for item in root.iterdir() if item.is_file() and _is_cleanup_candidate(item)]

    run_logs = [item for item in files if item.name.startswith('run_') and item.suffix == '.log']
    latest_run_ids = {
        item.stem
        for item in sorted(run_logs, key=lambda p: p.stat().st_mtime, reverse=True)[: policy.keep_recent_runs]
    }
    protected_run_ids = active | latest_run_ids
    summary['protected_run_ids'] = sorted(protected_run_ids)

    cutoff_seconds = policy.retention_days * 24 * 60 * 60
    for path in sorted(files, key=lambda p: p.name):
        summary['scanned'] += 1
        stat = path.stat()
        run_id = _run_id_for(path)
        explicit_access = access.get(path.name)
        last_used = max(stat.st_mtime, stat.st_atime, explicit_access or 0)
        age_seconds = max(0.0, now_ts - last_used)

        if run_id and run_id in protected_run_ids:
            summary['kept'] += 1
            continue
        if age_seconds <= cutoff_seconds:
            summary['kept'] += 1
            continue

        item = {
            'file': path.name,
            'size': stat.st_size,
            'age_days': round(age_seconds / 86400, 2),
            'last_used': datetime.fromtimestamp(last_used, timezone.utc).isoformat(),
        }
        summary['candidates'].append(item)
        if dry_run:
            summary['would_delete'] += 1
            continue
        try:
            path.unlink()
            summary['deleted'] += 1
            summary['bytes_deleted'] += stat.st_size
        except FileNotFoundError:
            continue
        except Exception as exc:
            item['error'] = str(exc)
            summary['kept'] += 1

    return summary
