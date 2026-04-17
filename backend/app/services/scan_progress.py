from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ScanPhase(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    DONE = "done"
    ERROR = "error"


@dataclass
class ScanProgress:
    phase: ScanPhase = ScanPhase.IDLE
    total_accounts: int = 0
    current_account_idx: int = 0
    current_account_name: str = ""
    total_emails: int = 0
    current_email_idx: int = 0
    current_email_subject: str = ""
    total_attachments: int = 0
    current_attachment_idx: int = 0
    current_attachment_name: str = ""
    current_attachment_url: str = ""
    current_download_outcome: str = ""
    current_parse_method: str = ""
    current_parse_format: str = ""
    last_classification_tier: int = 0
    emails_processed: int = 0
    invoices_found: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    @property
    def account_pct(self) -> float:
        return self.current_account_idx / self.total_accounts if self.total_accounts else 0.0

    @property
    def email_pct(self) -> float:
        return self.emails_processed / self.total_emails if self.total_emails else 0.0

    @property
    def overall_pct(self) -> float:
        if not self.total_accounts:
            return 0.0
        completed = max(0, self.current_account_idx - 1)
        base = completed / self.total_accounts
        current_weight = 1.0 / self.total_accounts
        email_frac = self.emails_processed / max(self.total_emails, 1)
        return min(base + current_weight * email_frac, 1.0)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["account_pct"] = round(self.account_pct * 100, 1)
        data["email_pct"] = round(self.email_pct * 100, 1)
        data["overall_pct"] = round(self.overall_pct * 100, 1)
        data["phase"] = self.phase.value
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


_progress = ScanProgress()
_subscribers: list[asyncio.Queue[str]] = []
_scan_lock = asyncio.Lock()
_progress_lock = asyncio.Lock()


def get_progress() -> ScanProgress:
    return _progress


def is_scanning() -> bool:
    return _scan_lock.locked()


def reset_progress(total_accounts: int) -> None:
    global _progress
    _progress = ScanProgress(
        phase=ScanPhase.SCANNING,
        total_accounts=total_accounts,
        started_at=time.time(),
        updated_at=time.time(),
    )


async def update_progress(**kwargs: Any) -> None:
    async with _progress_lock:
        progress = _progress
        for key, value in kwargs.items():
            setattr(progress, key, value)
        progress.touch()
        _broadcast(progress.to_json())


async def inc_emails_processed(n: int = 1) -> None:
    async with _progress_lock:
        _progress.emails_processed += n
        _progress.touch()
        _broadcast(_progress.to_json())


async def inc_invoices_found(n: int = 1) -> None:
    async with _progress_lock:
        _progress.invoices_found += n
        _progress.touch()
        _broadcast(_progress.to_json())


async def inc_errors(n: int = 1) -> None:
    async with _progress_lock:
        _progress.errors += n
        _progress.touch()
        _broadcast(_progress.to_json())


async def finish_progress(error: str | None = None) -> None:
    async with _progress_lock:
        if error:
            _progress.phase = ScanPhase.ERROR
        else:
            _progress.phase = ScanPhase.DONE
            _progress.current_account_idx = _progress.total_accounts
            _progress.current_email_idx = _progress.total_emails
            _progress.emails_processed = _progress.total_emails
        _progress.touch()
        _broadcast(_progress.to_json())


def _broadcast(payload: str) -> None:
    dead: list[asyncio.Queue[str]] = []
    for queue in _subscribers:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(queue)
    for queue in dead:
        unsubscribe(queue)


def subscribe() -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
    _subscribers.append(queue)
    return queue


def unsubscribe(queue: asyncio.Queue[str]) -> None:
    try:
        _subscribers.remove(queue)
    except ValueError:
        pass
