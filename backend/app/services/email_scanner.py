# pyright: reportMissingImports=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportAny=false, reportExplicitAny=false, reportImplicitOverride=false, reportUnusedCallResult=false

from __future__ import annotations

import asyncio
import base64
import email
from email import policy
from email.utils import parsedate_to_datetime
import concurrent.futures
import functools
import hashlib
import imaplib
import socket
import json
import logging
import poplib
import re
import ssl
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable, cast

import httpx
import msal
from msal.exceptions import MsalServiceError
from cryptography.fernet import Fernet
from imap_tools import AND, MailBox
from imap_tools.errors import MailboxFetchError, MailboxLoginError
from imap_tools.query import UidRange as U

from app.config import get_settings
from app.models import EmailAccount

logger = logging.getLogger(__name__)

FIRST_SCAN_LIMIT: int | None = None

IMAP_FETCH_WORKERS: int = 4
IMAP_PARALLEL_THRESHOLD: int = 500

# Timeouts for IMAP I/O. The raw imap-tools library default is None on both
# (a) TCP handshake and (b) socket recv. Without these, a stalled QQ/163
# `FETCH` (e.g. server returns `NO [b'System busy!']` and then the SSL layer
# half-dies) can block the scan thread for 30-60 minutes until TCP finally
# gives up and the SSL layer raises `[SSL: BAD_LENGTH] bad length` — during
# which time the UI shows "fetching {folder} (~N msgs)" with zero progress.
IMAP_CONNECT_TIMEOUT: float = 15.0
IMAP_READ_TIMEOUT: float = 120.0
# Wall-clock timeout on parallel-worker `future.result()`. A single zombie
# worker can otherwise block the entire pool and cascade-fail the whole account.
IMAP_PARALLEL_WORKER_TIMEOUT: float = 300.0
# Transient "System busy" / rate-limit responses on QQ/163 are usually resolved
# within seconds. We do a single short-sleep retry on the parallel path before
# falling back to single-connection.
IMAP_PARALLEL_RETRY_DELAY_SECONDS: float = 5.0

# QQ Mail and other Chinese providers (163, QQ Exmail) enforce much tighter
# per-account concurrent-connection limits (observed ~1-2 simultaneous
# sessions; 4 parallel workers reliably trigger `NO [b'System busy!']` and
# subsequent SSL BAD_LENGTH corruption). They are also slow enough that a
# naive `mailbox.fetch(criteria='ALL', bulk=500)` on a 35k-message INBOX
# times out server-side (~30-60s) before any data returns. The v0.8.3
# production validation showed emails_scanned=0 across every QQ scan despite
# all v0.8.2/v0.8.3 timeout defenses firing correctly — the scanner never
# actually completed a single FETCH.
#
# QQ_* constants below encode the empirically-safe subset found by local
# probes against imap.qq.com:
#   - SELECT INBOX on a 35k-msg mailbox takes 15-17s on QQ, so we MUST
#     pass initial_folder=None to MailBox().login() to avoid imap-tools'
#     implicit auto-SELECT happening inside login (which blows past
#     IMAP_CONNECT_TIMEOUT). Applied to all providers for safety — there is
#     no benefit to the auto-SELECT because our scan loop explicitly calls
#     mailbox.folder.set(folder_name) per-folder.
#   - 1 connection (no parallel). Observed: 2 workers still trip QQ; 1 works.
#   - bulk=50 instead of 500. Avoids QQ's server-side per-FETCH timeout.
#   - Inter-batch sleep + periodic NOOP keepalive. Keeps us under QQ's rate
#     limit and prevents idle-timeout drops.
#   - Higher read timeout. QQ's SELECT/SEARCH/STATUS responses legitimately
#     take 15-25 seconds each on a large mailbox.
QQ_ACCOUNT_TYPES: frozenset[str] = frozenset({"qq"})
QQ_IMAP_HOSTS: frozenset[str] = frozenset({"imap.qq.com", "imap.exmail.qq.com"})
QQ_FETCH_WORKERS: int = 1
QQ_BULK_SIZE: int = 50
QQ_INTER_BATCH_SLEEP_SECONDS: float = 1.0
QQ_NOOP_EVERY_N_BATCHES: int = 10
QQ_IMAP_READ_TIMEOUT: float = 180.0
QQ_RECONNECT_MAX_RETRIES: int = 3
QQ_RECONNECT_BACKOFF_BASE_SECONDS: float = 10.0

IMAP_CONNECTION_ERRORS = (
    OSError,
    ssl.SSLError,
    imaplib.IMAP4.error,
    imaplib.IMAP4.abort,
    MailboxLoginError,
    MailboxFetchError,
    socket.timeout,
    TimeoutError,
)
POP3_CONNECTION_ERRORS = (OSError, ssl.SSLError, poplib.error_proto)
OUTLOOK_CONNECTION_ERRORS = (OSError, httpx.HTTPError, MsalServiceError)

URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')
TAG_PATTERN = re.compile(r"<[^>]+>")
INVOICE_EXTENSIONS = {".pdf", ".xml", ".ofd"}
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
OUTLOOK_PERSONAL_DOMAINS = frozenset(
    {
        "outlook.com",
        "hotmail.com",
        "live.com",
        "live.cn",
        "live.co.uk",
        "live.fr",
        "live.de",
        "live.it",
        "live.jp",
        "msn.com",
        "passport.com",
    }
)


@dataclass
class OAuthFlowState:
    status: str
    verification_uri: str = ""
    user_code: str = ""
    expires_at: datetime | None = None
    detail: str | None = None
    task: asyncio.Task[Any] | None = field(default=None, repr=False)


class OAuthFlowRegistry:
    def __init__(self) -> None:
        self._flows: dict[int, OAuthFlowState] = {}

    def get(self, account_id: int) -> OAuthFlowState | None:
        state = self._flows.get(account_id)
        if state and state.expires_at and datetime.now(timezone.utc) > state.expires_at:
            state.status = "expired"
            state.detail = "Device code expired"
            if state.task and not state.task.done():
                state.task.cancel()
        return state

    def set(self, account_id: int, state: OAuthFlowState) -> None:
        self._flows[account_id] = state

    def remove(self, account_id: int) -> None:
        state = self._flows.pop(account_id, None)
        if state and state.task and not state.task.done():
            state.task.cancel()


oauth_registry = OAuthFlowRegistry()


@dataclass
class RawAttachment:
    filename: str
    content_type: str
    size: int | None = None
    payload: bytes | None = None


@dataclass
class RawEmail:
    uid: str
    subject: str
    body_text: str
    body_html: str
    from_addr: str
    received_at: datetime
    attachments: list[RawAttachment] = field(default_factory=list)
    body_links: list[str] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    is_hydrated: bool = True
    folder: str = ""
    message_id: str = ""


@dataclass
class ScanOptions:
    unread_only: bool = False
    since: datetime | None = None
    reset_state: bool = False


class BaseEmailScanner(ABC):
    @abstractmethod
    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        """Scan for emails. IMAP and Outlook enumerate folders and return metadata-only
        RawEmail objects (is_hydrated=False); POP3 returns fully-hydrated objects because
        POP3 has no efficient partial fetch. Caller should persist the value of
        ``_last_scan_state`` back to ``EmailAccount.last_scan_uid`` after a scan
        completes, if set. When ``options`` is provided, its filters (``unread_only``,
        ``since``) layer on top of the incremental UID/datetime state; when
        ``options.reset_state`` is True, existing state is discarded before scanning."""

    @abstractmethod
    async def test_connection(self, account: EmailAccount) -> bool:
        """Test if connection to email account is valid."""

    async def hydrate_email(self, account: EmailAccount, email: RawEmail) -> RawEmail:
        """Fetch body + attachment payloads for a previously metadata-only email.

        Default implementation is a no-op for scanners that already return hydrated
        emails (POP3). IMAP and Outlook override to perform the lazy second fetch.
        """
        return email


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet key from JWT_SECRET."""
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_password(plaintext: str, secret: str) -> str:
    fernet = Fernet(_derive_fernet_key(secret))
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_password(encrypted: str | None, secret: str | None = None) -> str:
    if not encrypted:
        return ""
    resolved_secret = secret or get_settings().JWT_SECRET
    fernet = Fernet(_derive_fernet_key(resolved_secret))
    return fernet.decrypt(encrypted.encode()).decode()


def _is_qq_imap(account: EmailAccount | None, host: str | None = None) -> bool:
    """True for IMAP accounts that must use QQ's conservative settings.
    Matches either the account type or the IMAP host (covers Exmail and
    any users who manually picked type='imap' for their QQ account)."""
    account_type = str(getattr(account, "type", "") or "").lower() if account is not None else ""
    if account_type in QQ_ACCOUNT_TYPES:
        return True
    resolved_host = str(host or (getattr(account, "host", "") if account is not None else "") or "").lower()
    return resolved_host in QQ_IMAP_HOSTS


def _extract_urls(*body_parts: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for match in URL_PATTERN.findall(" ".join(body_parts)):
        if match not in seen:
            seen.add(match)
            urls.append(match)
    return urls


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    without_tags = TAG_PATTERN.sub(" ", value)
    return re.sub(r"\s+", " ", unescape(without_tags)).strip()


def _normalize_datetime(value: datetime | str | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = parsedate_to_datetime(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _set_imap_keepalive(imap_client: Any, read_timeout: float | None = None) -> None:
    """Enable TCP keepalive AND a socket read timeout on the IMAP socket.

    TCP keepalive alone (v0.7.8) only detects silent peer disappearance via
    the TCP layer (~100s). It does NOT detect application-level stalls where
    TCP keepalive probes are ACKed but no IMAP data flows (observed on
    imap.qq.com after a `NO [b'System busy!']` reply, where the SSL layer
    ends up half-open for 30-60 minutes before finally raising
    `[SSL: BAD_LENGTH]`).

    A per-recv timeout via ``sock.settimeout()`` is the only reliable way to
    bound those stalls. `imap-tools` MailBox has no read-timeout kwarg, so we
    set it on the underlying socket ourselves. QQ legitimately needs a higher
    read timeout (SELECT/SEARCH on 35k-msg mailboxes can take 15-25s each),
    so callers pass read_timeout=QQ_IMAP_READ_TIMEOUT for QQ-type accounts.
    Safe no-op on platforms that don't expose TCP_KEEPIDLE."""
    if imap_client is None:
        return
    try:
        sock = imap_client.socket()
    except Exception:
        return
    try:
        sock.settimeout(read_timeout if read_timeout is not None else IMAP_READ_TIMEOUT)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "TCP_KEEPIDLE"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        elif hasattr(socket, "TCP_KEEPALIVE"):  # pragma: no cover
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 60)
    except Exception:
        pass


def _is_uid_newer(candidate: str | None, previous: str | None) -> bool:
    if not candidate:
        return False
    if not previous:
        return True
    if candidate.isdigit() and previous.isdigit():
        return int(candidate) > int(previous)
    return candidate > previous


def _resolve_filename(name: str | None, fallback: str) -> str:
    cleaned = (name or "").strip()
    return cleaned or fallback


IMAP_SKIP_FLAGS = frozenset({r"\noselect", r"\drafts", r"\trash", r"\all"})


def _imap_folder_should_scan(flags: tuple[str, ...]) -> bool:
    flags_lower = {str(f).lower() for f in (flags or ())}
    if flags_lower & IMAP_SKIP_FLAGS:
        return False
    return True


def _parse_imap_state(raw: str | None) -> dict[str, dict[str, str]]:
    """Parse `last_scan_uid` into a per-folder state dict.
    Accepts the new JSON format {folder: {uid, uidvalidity, uidnext?, messages?}}
    and legacy string form (treated as INBOX state with unknown UIDVALIDITY).
    Extra keys (uidnext, messages) from STATUS are preserved for the
    unchanged-folder skip optimization."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and all(isinstance(v, dict) for v in data.values()):
            return {
                str(k): {
                    "uid": str(v.get("uid") or ""),
                    "uidvalidity": str(v.get("uidvalidity") or ""),
                    "uidnext": str(v.get("uidnext") or ""),
                    "messages": str(v.get("messages") or ""),
                }
                for k, v in data.items()
            }
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {"INBOX": {"uid": str(raw), "uidvalidity": "", "uidnext": "", "messages": ""}}


def _serialize_imap_state(state: dict[str, dict[str, str]]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _build_imap_criteria(options: ScanOptions | None) -> Any:
    """Compose an imap-tools criteria from ScanOptions. IMAP SINCE is
    DATE-granularity per RFC 3501, so date_gte truncates to the local date;
    the scan loop still applies an exact datetime filter client-side."""
    if options is None:
        return "ALL"
    kwargs: dict[str, Any] = {}
    if options.unread_only:
        kwargs["seen"] = False
    if options.since is not None:
        local_since = options.since.astimezone() if options.since.tzinfo else options.since
        kwargs["date_gte"] = local_since.date()
    if not kwargs:
        return "ALL"
    return AND(**kwargs)


def _parse_graph_state(raw: str | None) -> dict[str, str]:
    """Parse Graph per-folder last-receivedDateTime state.
    New JSON format: {folder_id: last_received_dt}. Legacy: bare string is
    the most-recent internetMessageId (kept as "__legacy_uid__" key so we
    still honour it once, then switch to the new format)."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v or "") for k, v in data.items()}
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    return {"__legacy_uid__": str(raw)}


def _serialize_graph_state(state: dict[str, str]) -> str:
    return json.dumps(state, ensure_ascii=False)


def _build_qq_fetch_criteria(effective_prev_uid: str, options: ScanOptions | None) -> Any:
    """For QQ, ALWAYS restrict the server-side SEARCH to a UID range. A bare
    `SEARCH ALL` on a 35k-message INBOX makes QQ's IMAP server time out
    (observed server-side ~30-60s) before any UIDs are returned. When we
    have a saved highest-UID baseline we fetch `UID last+1:*`; on a first
    scan with no baseline we still scan ALL but via the generator-safe
    bulk=QQ_BULK_SIZE path so batches stay small."""
    if effective_prev_uid and effective_prev_uid.isdigit():
        next_uid = str(int(effective_prev_uid) + 1)
        if options is not None and (options.unread_only or options.since is not None):
            kwargs: dict[str, Any] = {"uid": U(next_uid, "*")}
            if options.unread_only:
                kwargs["seen"] = False
            if options.since is not None:
                local_since = options.since.astimezone() if options.since.tzinfo else options.since
                kwargs["date_gte"] = local_since.date()
            return AND(**kwargs)
        return AND(uid=U(next_uid, "*"))
    return _build_imap_criteria(options)


def _fetch_folder_worker(
    host: str,
    port: int,
    username: str,
    password: str,
    folder_name: str,
    uid_list: list[str],
    criteria: Any,
    since: Any,
    options: Any,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch a sub-list of UIDs from a dedicated IMAP connection.

    Runs in a thread. Returns (raw_message_dicts, error_or_empty_str) where
    raw_message_dicts contains one dict per message with uid/subject/from_/date/
    headers fields so the caller can build RawEmail without re-parsing."""
    results: list[dict[str, Any]] = []
    error_msg = ""
    try:
        worker_is_qq = _is_qq_imap(None, host=host)
        worker_read_timeout = QQ_IMAP_READ_TIMEOUT if worker_is_qq else IMAP_READ_TIMEOUT
        worker_bulk = QQ_BULK_SIZE if worker_is_qq else 500
        with MailBox(host, port=port, timeout=IMAP_CONNECT_TIMEOUT).login(
            username, password, initial_folder=None
        ) as mb:
            _set_imap_keepalive(getattr(mb, "client", None), read_timeout=worker_read_timeout)
            mb.folder.set(folder_name)
            for msg in mb.fetch(
                criteria,
                mark_seen=False,
                headers_only=True,
                bulk=worker_bulk,
                reverse=True,
            ):
                msg_uid = cast(str, getattr(msg, "uid", "") or "")
                if msg_uid not in uid_list:
                    continue
                received_at = _normalize_datetime(getattr(msg, "date", None))
                if options is not None and options.since is not None and received_at < options.since:
                    continue
                results.append({
                    "uid": msg_uid,
                    "subject": cast(str, getattr(msg, "subject", "") or ""),
                    "from_": cast(str, getattr(msg, "from_", "") or ""),
                    "received_at": received_at,
                    "headers": {
                        key: str(value)
                        for key, value in cast(Any, getattr(msg, "headers", {})).items()
                    },
                })
    except IMAP_CONNECTION_ERRORS as exc:
        error_msg = str(exc)
    return results, error_msg


class ImapScanner(BaseEmailScanner):
    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._scan_sync, account, last_uid, options, progress_callback)
        )

    def _scan_sync(
        self,
        account: EmailAccount,
        last_uid: str | None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        password = decrypt_password(account.password_encrypted)
        emails: list[RawEmail] = []
        seen_message_ids: set[str] = set()
        is_qq = _is_qq_imap(account)
        read_timeout = QQ_IMAP_READ_TIMEOUT if is_qq else IMAP_READ_TIMEOUT
        bulk_size = QQ_BULK_SIZE if is_qq else 500
        max_workers = QQ_FETCH_WORKERS if is_qq else IMAP_FETCH_WORKERS

        if options is not None and options.reset_state:
            state_in: dict[str, dict[str, str]] = {}
        else:
            state_in = _parse_imap_state(last_uid)
        state_out: dict[str, dict[str, str]] = {}
        criteria = _build_imap_criteria(options)

        def _publish(update: dict[str, Any]) -> None:
            if progress_callback is not None:
                try:
                    progress_callback(update)
                except Exception as exc:
                    logger.debug("progress_callback raised, ignoring: %s", exc)

        try:
            with MailBox(account.host or "", port=account.port or 993, timeout=IMAP_CONNECT_TIMEOUT).login(
                account.username, password, initial_folder=None
            ) as mailbox:
                _set_imap_keepalive(getattr(mailbox, "client", None), read_timeout=read_timeout)
                all_folders = list(mailbox.folder.list())
                scannable_folders = [
                    f for f in all_folders
                    if getattr(f, "name", "") and _imap_folder_should_scan(tuple(getattr(f, "flags", ()) or ()))
                ]
                _publish({"total_folders": len(scannable_folders), "current_folder_idx": 0, "folder_fetch_msg": ""})

                for folder_idx, folder_info in enumerate(scannable_folders):
                    folder_name = getattr(folder_info, "name", "") or ""
                    _publish({
                        "current_folder_idx": folder_idx + 1,
                        "current_folder_name": folder_name,
                        "folder_fetch_msg": f"selecting {folder_name}",
                    })

                    try:
                        mailbox.folder.set(folder_name)
                    except IMAP_CONNECTION_ERRORS as exc:
                        logger.warning("IMAP folder %r could not be selected (%s); skipping", folder_name, exc)
                        continue

                    prev = state_in.get(folder_name, {})
                    prev_uid = prev.get("uid") or ""
                    prev_uidvalidity = prev.get("uidvalidity") or ""
                    prev_uidnext = prev.get("uidnext") or ""
                    prev_messages = prev.get("messages") or ""

                    current_uidvalidity = ""
                    current_uidnext = ""
                    current_messages = ""
                    try:
                        status = mailbox.folder.status(folder_name, ["UIDVALIDITY", "UIDNEXT", "MESSAGES"])
                        current_uidvalidity = str(status.get("UIDVALIDITY") or "")
                        current_uidnext = str(status.get("UIDNEXT") or "")
                        current_messages = str(status.get("MESSAGES") or "")
                    except (IMAP_CONNECTION_ERRORS + (Exception,)):
                        pass

                    uidvalidity_changed = bool(prev_uidvalidity) and bool(current_uidvalidity) and prev_uidvalidity != current_uidvalidity

                    if (
                        not uidvalidity_changed
                        and prev_uidnext
                        and current_uidnext
                        and prev_uidnext == current_uidnext
                        and prev_messages == current_messages
                    ):
                        logger.info(
                            "IMAP folder %r unchanged (UIDNEXT=%s, MESSAGES=%s); skipping fetch",
                            folder_name,
                            current_uidnext,
                            current_messages,
                        )
                        state_out[folder_name] = {
                            "uid": prev_uid,
                            "uidvalidity": current_uidvalidity,
                            "uidnext": current_uidnext,
                            "messages": current_messages,
                        }
                        _publish({"folder_fetch_msg": f"{folder_name}: unchanged, skipped"})
                        continue

                    effective_prev_uid = "" if uidvalidity_changed or not prev_uid else prev_uid
                    limit = FIRST_SCAN_LIMIT if not effective_prev_uid else None
                    highest_uid = effective_prev_uid
                    folder_emails_this_run = 0

                    _publish({"folder_fetch_msg": f"fetching {folder_name} (~{current_messages or '?'} msgs)"})

                    n_workers = max_workers if max_workers > 1 else 1
                    all_uids: list[str] = []
                    if n_workers > 1:
                        _publish({"folder_fetch_msg": f"{folder_name}: searching UIDs (~{current_messages or '?'} msgs)"})
                        try:
                            all_uids_raw = mailbox.uids(criteria)
                            all_uids = [u for u in all_uids_raw if not effective_prev_uid or _is_uid_newer(u, effective_prev_uid)]
                            if limit is not None:
                                all_uids = all_uids[:limit]
                            _publish({"folder_fetch_msg": f"{folder_name}: {len(all_uids)} new UIDs to fetch"})
                        except (IMAP_CONNECTION_ERRORS + (AttributeError,)) as exc:
                            logger.warning(
                                "IMAP uids() search failed for folder %r (%s); falling back to single-connection serial fetch",
                                folder_name,
                                exc,
                            )
                            all_uids = []

                    use_parallel = (
                        n_workers > 1
                        and len(all_uids) >= IMAP_PARALLEL_THRESHOLD
                    )

                    def _process_msg_dict(
                        msg_dict: dict[str, Any],
                    ) -> RawEmail | None:
                        msg_uid = msg_dict["uid"]
                        headers_map = msg_dict["headers"]
                        message_id = ""
                        for header_key in ("Message-ID", "Message-Id", "message-id"):
                            if header_key in headers_map:
                                message_id = headers_map[header_key].strip().strip("<>")
                                break
                        if message_id and message_id in seen_message_ids:
                            nonlocal highest_uid
                            if msg_uid and (not highest_uid or _is_uid_newer(msg_uid, highest_uid)):  # pragma: no branch
                                highest_uid = msg_uid
                            return None
                        if message_id:
                            seen_message_ids.add(message_id)
                        received_at = msg_dict["received_at"]
                        if msg_uid and (not highest_uid or _is_uid_newer(msg_uid, highest_uid)):  # pragma: no branch
                            highest_uid = msg_uid
                        return RawEmail(
                            uid=msg_uid,
                            subject=msg_dict["subject"],
                            body_text="",
                            body_html="",
                            from_addr=msg_dict["from_"],
                            received_at=received_at,
                            attachments=[],
                            body_links=[],
                            headers=headers_map,
                            is_hydrated=False,
                            folder=folder_name,
                            message_id=message_id,
                        )

                    if use_parallel:
                        partition_size = (len(all_uids) + n_workers - 1) // n_workers
                        partitions = [all_uids[i * partition_size : (i + 1) * partition_size] for i in range(n_workers)]

                        parallel_ok = False
                        worker_msgs: list[dict[str, Any]] = []
                        for attempt in range(2):
                            pool = concurrent.futures.ThreadPoolExecutor(max_workers=n_workers)
                            worker_futures = []
                            try:
                                for p in partitions:
                                    worker_futures.append(pool.submit(
                                        _fetch_folder_worker,
                                        account.host or "",
                                        account.port or 993,
                                        account.username,
                                        password,
                                        folder_name,
                                        list(set(p)),
                                        criteria,
                                        options.since if options else None,
                                        options,
                                    ))

                                attempt_msgs: list[dict[str, Any]] = []
                                attempt_ok = True
                                attempt_err: str | None = None
                                for fut in worker_futures:
                                    try:
                                        batch_msgs, err = fut.result(timeout=IMAP_PARALLEL_WORKER_TIMEOUT)
                                        if err:
                                            attempt_err = err
                                            attempt_ok = False
                                            attempt_msgs.extend(batch_msgs)
                                            continue
                                        attempt_msgs.extend(batch_msgs)
                                    except concurrent.futures.TimeoutError as exc:
                                        attempt_err = f"worker timed out after {IMAP_PARALLEL_WORKER_TIMEOUT:.0f}s: {exc}"
                                        attempt_ok = False
                                    except Exception as exc:
                                        attempt_err = f"worker raised: {exc}"
                                        attempt_ok = False
                            finally:
                                pool.shutdown(wait=False, cancel_futures=True)

                            if attempt_ok:
                                worker_msgs = attempt_msgs
                                parallel_ok = True
                                break

                            if attempt == 0:
                                logger.warning(
                                    "IMAP parallel fetch failed for folder %r (%s); partial=%d msgs; retrying once in %.0fs",
                                    folder_name,
                                    attempt_err,
                                    len(attempt_msgs),
                                    IMAP_PARALLEL_RETRY_DELAY_SECONDS,
                                )
                                worker_msgs = attempt_msgs
                                try:
                                    time.sleep(IMAP_PARALLEL_RETRY_DELAY_SECONDS)
                                except Exception:  # pragma: no cover
                                    pass
                            else:
                                logger.warning(
                                    "IMAP parallel fetch still failing for folder %r (%s); will fall back to single-connection with partial=%d msgs preserved",
                                    folder_name,
                                    attempt_err,
                                    len(attempt_msgs),
                                )
                                if len(attempt_msgs) > len(worker_msgs):
                                    worker_msgs = attempt_msgs

                        if worker_msgs:
                            worker_msgs.sort(key=lambda m: m["received_at"])
                            for msg_dict in worker_msgs:
                                email_obj = _process_msg_dict(msg_dict)
                                if email_obj is not None:
                                    emails.append(email_obj)
                                    folder_emails_this_run += 1
                                    if folder_emails_this_run % 200 == 0:
                                        _publish({
                                            "folder_fetch_msg": f"{folder_name}: +{folder_emails_this_run} msgs (parallel)",
                                            "total_emails": len(emails),
                                        })

                        if not parallel_ok:
                            use_parallel = False

                    if not use_parallel:
                        fetch_criteria = (
                            _build_qq_fetch_criteria(effective_prev_uid, options)
                            if is_qq else criteria
                        )
                        if is_qq:
                            _publish({
                                "folder_fetch_msg": (
                                    f"{folder_name}: fetching in batches of {bulk_size} "
                                    f"(QQ-safe mode)"
                                )
                            })
                        try:
                            iterator = mailbox.fetch(
                                fetch_criteria,
                                limit=limit,
                                reverse=True,
                                mark_seen=False,
                                headers_only=True,
                                bulk=bulk_size,
                            )
                            for msg in iterator:
                                msg_uid = cast(str, getattr(msg, "uid", "") or "")
                                if effective_prev_uid and not _is_uid_newer(msg_uid, effective_prev_uid):
                                    continue
                                headers_map = {key: str(value) for key, value in cast(Any, getattr(msg, "headers", {})).items()}
                                message_id = ""
                                for header_key in ("Message-ID", "Message-Id", "message-id"):
                                    if header_key in headers_map:
                                        message_id = headers_map[header_key].strip().strip("<>")
                                        break
                                if message_id and message_id in seen_message_ids:
                                    if msg_uid and (not highest_uid or _is_uid_newer(msg_uid, highest_uid)):  # pragma: no branch
                                        highest_uid = msg_uid
                                    continue
                                if message_id:
                                    seen_message_ids.add(message_id)

                                received_at = _normalize_datetime(getattr(msg, "date", None))
                                if options is not None and options.since is not None and received_at < options.since:
                                    if msg_uid and (not highest_uid or _is_uid_newer(msg_uid, highest_uid)):  # pragma: no branch
                                        highest_uid = msg_uid
                                    continue
                                emails.append(
                                    RawEmail(
                                        uid=msg_uid,
                                        subject=cast(str, getattr(msg, "subject", "") or ""),
                                        body_text="",
                                        body_html="",
                                        from_addr=cast(str, getattr(msg, "from_", "") or ""),
                                        received_at=received_at,
                                        attachments=[],
                                        body_links=[],
                                        headers=headers_map,
                                        is_hydrated=False,
                                        folder=folder_name,
                                        message_id=message_id,
                                    )
                                )
                                folder_emails_this_run += 1
                                progress_step = bulk_size if is_qq else 200
                                if folder_emails_this_run % progress_step == 0:
                                    _publish({
                                        "folder_fetch_msg": f"{folder_name}: +{folder_emails_this_run} msgs",
                                        "total_emails": len(emails),
                                    })
                                    if is_qq:
                                        try:
                                            time.sleep(QQ_INTER_BATCH_SLEEP_SECONDS)
                                        except Exception:  # pragma: no cover
                                            pass
                                        batches_so_far = folder_emails_this_run // bulk_size
                                        if batches_so_far > 0 and batches_so_far % QQ_NOOP_EVERY_N_BATCHES == 0:
                                            try:
                                                mailbox.client.noop()
                                            except IMAP_CONNECTION_ERRORS as noop_exc:
                                                logger.warning(
                                                    "IMAP NOOP keepalive failed for folder %r (%s); "
                                                    "batch fetch loop will naturally end on next error",
                                                    folder_name,
                                                    noop_exc,
                                                )
                                if msg_uid and (not highest_uid or _is_uid_newer(msg_uid, highest_uid)):
                                    highest_uid = msg_uid
                        except IMAP_CONNECTION_ERRORS as exc:
                            logger.warning(
                                "IMAP fetch failed for folder %r (%s); partial results for this folder will be saved and we will continue to the next folder",
                                folder_name,
                                exc,
                            )

                    state_out[folder_name] = {
                        "uid": highest_uid or "",
                        "uidvalidity": current_uidvalidity,
                        "uidnext": current_uidnext,
                        "messages": current_messages,
                    }
                    _publish({
                        "folder_fetch_msg": f"{folder_name}: {folder_emails_this_run} msgs fetched",
                        "total_emails": len(emails),
                    })
        except IMAP_CONNECTION_ERRORS as exc:
            logger.warning(
                "IMAP session dropped during scan of %s (%s); preserving partial progress from %d folder(s) before the drop",
                account.name,
                exc,
                len(state_out),
            )

        emails.sort(key=lambda e: e.received_at)
        self._last_scan_state = _serialize_imap_state(state_out)
        _publish({"folder_fetch_msg": f"{len(emails)} emails across {len(state_out)} folders"})
        return emails

    async def hydrate_email(self, account: EmailAccount, email: RawEmail) -> RawEmail:
        if email.is_hydrated:
            return email
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._hydrate_sync, account, email)
        )

    def _hydrate_sync(self, account: EmailAccount, email: RawEmail) -> RawEmail:
        password = decrypt_password(account.password_encrypted)
        folder_name = email.folder or "INBOX"
        try:
            with MailBox(account.host or "", port=account.port or 993, timeout=IMAP_CONNECT_TIMEOUT).login(
                account.username, password, initial_folder=None
            ) as mailbox:
                _set_imap_keepalive(
                    getattr(mailbox, "client", None),
                    read_timeout=QQ_IMAP_READ_TIMEOUT if _is_qq_imap(account) else IMAP_READ_TIMEOUT,
                )
                try:
                    mailbox.folder.set(folder_name)
                except IMAP_CONNECTION_ERRORS as exc:
                    logger.warning("IMAP hydrate cannot select folder %r for uid=%s: %s", folder_name, email.uid, exc)
                    email.is_hydrated = True
                    return email

                for msg in mailbox.fetch(
                    AND(uid=email.uid), mark_seen=False, limit=1, bulk=False
                ):
                    attachments: list[RawAttachment] = []
                    for att in getattr(msg, "attachments", []):
                        filename = getattr(att, "filename", None)
                        ext = Path(filename or "").suffix.lower()
                        if ext not in INVOICE_EXTENSIONS:
                            continue
                        payload = cast(bytes, getattr(att, "payload", b""))
                        attachments.append(
                            RawAttachment(
                                filename=_resolve_filename(filename, f"attachment{ext or '.bin'}"),
                                content_type=getattr(att, "content_type", None) or "application/octet-stream",
                                size=len(payload),
                                payload=payload,
                            )
                        )
                    body_text = cast(str, getattr(msg, "text", "") or "")
                    body_html = cast(str, getattr(msg, "html", "") or "")
                    email.body_text = body_text
                    email.body_html = body_html
                    email.attachments = attachments
                    email.body_links = _extract_urls(body_text, body_html)
                    email.is_hydrated = True
                    return email
        except IMAP_CONNECTION_ERRORS as exc:
            logger.warning("IMAP hydrate failed for uid=%s: %s", email.uid, exc)
        email.is_hydrated = True
        return email

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(self._test_sync, account))
        except IMAP_CONNECTION_ERRORS as exc:
            logger.exception("IMAP connection test failed for account %s: %s", account.id, exc)
            return False

    def _test_sync(self, account: EmailAccount) -> bool:
        password = decrypt_password(account.password_encrypted)
        with MailBox(account.host or "", port=account.port or 993, timeout=IMAP_CONNECT_TIMEOUT).login(
            account.username, password, initial_folder=None
        ):
            return True


class Pop3Scanner(BaseEmailScanner):
    _MAX_RECENT_IDS = 1000

    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        del progress_callback
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, functools.partial(self._scan_sync, account, last_uid, options)
        )

    def _scan_sync(
        self,
        account: EmailAccount,
        last_uid: str | None,
        options: ScanOptions | None = None,
    ) -> list[RawEmail]:
        password = decrypt_password(account.password_encrypted)
        effective_last_uid = None if (options is not None and options.reset_state) else last_uid
        known_ids = self._load_recent_ids(effective_last_uid)
        emails: list[RawEmail] = []

        mailbox = poplib.POP3_SSL(account.host or "", account.port or 995)
        try:
            mailbox.user(account.username)
            mailbox.pass_(password)
            total_messages = len(mailbox.list()[1])
            if effective_last_uid is not None:
                start_index = 1
            elif FIRST_SCAN_LIMIT is None:
                start_index = 1
            else:
                start_index = max(1, total_messages - FIRST_SCAN_LIMIT + 1)

            for index in range(total_messages, start_index - 1, -1):
                _, lines, _ = mailbox.retr(index)
                raw_message = b"\n".join(lines)
                msg = email.message_from_bytes(raw_message, policy=policy.default)
                message_id = self._message_id_for(msg, index)
                if message_id in known_ids:
                    break

                received_at = _normalize_datetime(msg.get("Date"))
                if options is not None and options.since is not None and received_at < options.since:
                    continue

                attachments: list[RawAttachment] = []
                body_text_parts: list[str] = []
                body_html_parts: list[str] = []

                for part in msg.walk():
                    content_disposition = part.get_content_disposition()
                    content_type = part.get_content_type()

                    if part.is_multipart():
                        continue

                    if content_disposition == "attachment":
                        filename = part.get_filename()
                        ext = Path(filename or "").suffix.lower()
                        if ext not in INVOICE_EXTENSIONS:
                            continue
                        attachments.append(
                            RawAttachment(
                                filename=_resolve_filename(filename, f"attachment{ext or '.bin'}"),
                                payload=part.get_payload(decode=True) or b"",
                                content_type=content_type or "application/octet-stream",
                            )
                        )
                        continue

                    payload = part.get_content()
                    if not isinstance(payload, str) or not payload:
                        continue
                    if content_type == "text/plain":
                        body_text_parts.append(payload)
                    elif content_type == "text/html":
                        body_html_parts.append(payload)

                body_text = "\n".join(body_text_parts).strip()
                body_html = "\n".join(body_html_parts).strip()
                if not body_text and body_html:
                    body_text = _html_to_text(body_html)

                emails.append(
                    RawEmail(
                        uid=message_id,
                        subject=str(msg.get("Subject") or ""),
                        body_text=body_text,
                        body_html=body_html,
                        from_addr=str(msg.get("From") or ""),
                        received_at=received_at,
                        attachments=attachments,
                        body_links=_extract_urls(body_text, body_html),
                        headers={key: str(value) for key, value in msg.items()},
                    )
                )
        finally:
            try:
                mailbox.quit()
            except POP3_CONNECTION_ERRORS:
                mailbox.close()

        return emails

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, functools.partial(self._test_sync, account))
        except POP3_CONNECTION_ERRORS as exc:
            logger.exception("POP3 connection test failed for account %s: %s", account.id, exc)
            return False

    def _test_sync(self, account: EmailAccount) -> bool:
        password = decrypt_password(account.password_encrypted)
        mailbox = poplib.POP3_SSL(account.host or "", account.port or 995)
        try:
            mailbox.user(account.username)
            mailbox.pass_(password)
            return True
        finally:
            try:
                mailbox.quit()
            except POP3_CONNECTION_ERRORS:
                mailbox.close()

    def _load_recent_ids(self, raw_value: str | None) -> set[str]:
        if not raw_value:
            return set()
        try:
            decoded = json.loads(raw_value)
        except json.JSONDecodeError:
            return {raw_value}
        if not isinstance(decoded, list):
            return set()
        return {str(item) for item in decoded if item}

    def serialize_recent_ids(self, ids: list[str]) -> str:
        return json.dumps(ids[: self._MAX_RECENT_IDS], ensure_ascii=False)

    def _message_id_for(self, msg: Any, index: int) -> str:
        headers = getattr(msg, "headers", None)
        message_id = ""

        if headers is not None:
            try:
                if hasattr(headers, "get"):
                    header_value = headers.get("Message-ID") or headers.get("Message-Id")
                else:
                    header_value = headers["Message-ID"]
            except (AttributeError, KeyError, TypeError):
                header_value = None

            if isinstance(header_value, list):
                message_id = str(header_value[0]) if header_value else ""
            elif header_value is not None:
                message_id = str(header_value)

        if not message_id and hasattr(msg, "get"):
            header_value = msg.get("Message-ID") or msg.get("Message-Id")
            if header_value is not None:
                message_id = str(header_value)

        if message_id:
            return message_id.strip()

        fallback_bits = [
            cast(str, getattr(msg, "subject", None) or (msg.get("Subject") if hasattr(msg, "get") else "") or ""),
            cast(str, getattr(msg, "from_", None) or (msg.get("From") if hasattr(msg, "get") else "") or ""),
            str(getattr(msg, "date", None) or (msg.get("Date") if hasattr(msg, "get") else "") or ""),
            str(index),
        ]
        digest = hashlib.sha256("|".join(fallback_bits).encode()).hexdigest()
        return f"pop3-{digest}"


def _is_personal_microsoft_account(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return domain in OUTLOOK_PERSONAL_DOMAINS


def _get_outlook_msal_params(account: EmailAccount) -> tuple[str, str]:
    outlook_type = getattr(account, "outlook_account_type", "personal")
    return _get_outlook_msal_params_from_type(outlook_type)


def _get_outlook_msal_params_from_type(outlook_type: str) -> tuple[str, str]:
    from app.config import get_settings
    settings = get_settings()
    if outlook_type == "organizational":
        return settings.OUTLOOK_AAD_CLIENT_ID, "https://login.microsoftonline.com/common"
    return settings.OUTLOOK_PERSONAL_CLIENT_ID, "https://login.microsoftonline.com/consumers"


class OutlookScanner(BaseEmailScanner):
    SCOPES = ["Mail.Read"]

    SKIP_WELL_KNOWN_FOLDERS = frozenset({
        "drafts",
        "deleteditems",
        "outbox",
    })

    async def scan(
        self,
        account: EmailAccount,
        last_uid: str | None = None,
        options: ScanOptions | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> list[RawEmail]:
        access_token = await self._acquire_access_token(account)
        headers = {"Authorization": f"Bearer {access_token}"}
        if options is not None and options.reset_state:
            state_in: dict[str, str] = {}
        else:
            state_in = _parse_graph_state(last_uid)
        state_out: dict[str, str] = {}
        seen_message_ids: set[str] = set()
        emails: list[RawEmail] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            folders = [f async for f in self._iter_mail_folders(client, headers)]
            for folder in folders:
                if self._should_skip_folder(folder):
                    continue
                folder_id = cast(str, folder.get("id") or "")
                folder_name = cast(str, folder.get("displayName") or folder.get("wellKnownName") or "")
                if not folder_id:
                    continue

                prev_dt = state_in.get(folder_id, "") or state_in.get("__legacy_uid__", "")
                highest_dt = prev_dt
                params: dict[str, str] = {
                    "$top": "200",
                    "$orderby": "receivedDateTime asc",
                    "$select": "id,internetMessageId,subject,bodyPreview,from,receivedDateTime,hasAttachments",
                }
                filter_clauses: list[str] = []
                if prev_dt:
                    # ge (not gt): a strict > boundary permanently excludes any
                    # email whose receivedDateTime equals the watermark, which
                    # happens when two messages land in the same second or when
                    # the server's precision truncates to whole seconds. Using
                    # ge re-fetches the boundary email; the per-attachment
                    # seen-check in scheduler._was_attachment_seen and the
                    # composite UNIQUE(user_id, invoice_no) dedupe it cheaply.
                    filter_clauses.append(f"receivedDateTime ge {prev_dt}")
                if options is not None:
                    if options.unread_only:
                        filter_clauses.append("isRead eq false")
                    if options.since is not None:
                        since_iso = options.since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                        filter_clauses.append(f"receivedDateTime ge {since_iso}")
                if filter_clauses:
                    params["$filter"] = " and ".join(filter_clauses)

                next_url: str | None = f"{GRAPH_BASE_URL}/me/mailFolders/{folder_id}/messages"
                email_limit = FIRST_SCAN_LIMIT if not prev_dt else None

                while next_url and (email_limit is None or len(emails) < email_limit):
                    response = await client.get(next_url, headers=headers, params=params or None)
                    response.raise_for_status()
                    payload = response.json()
                    params = {}

                    for item in payload.get("value", []):
                        message_uid = cast(str, item.get("internetMessageId") or item.get("id") or "")
                        if message_uid and message_uid in seen_message_ids:
                            continue
                        if message_uid:
                            seen_message_ids.add(message_uid)

                        received_dt_str = cast(str, item.get("receivedDateTime") or "")
                        body_preview = str(item.get("bodyPreview") or "")
                        sender = cast(dict[str, Any], item.get("from") or {})
                        email_address = cast(dict[str, Any], sender.get("emailAddress") or {})

                        emails.append(
                            RawEmail(
                                uid=message_uid,
                                subject=str(item.get("subject") or ""),
                                body_text=body_preview,
                                body_html="",
                                from_addr=str(email_address.get("address") or ""),
                                received_at=_normalize_datetime(item.get("receivedDateTime")),
                                attachments=[],
                                body_links=[],
                                headers={
                                    "_graph_id": cast(str, item.get("id") or ""),
                                    "_graph_folder_id": folder_id,
                                    "_has_attachments": str(bool(item.get("hasAttachments"))),
                                },
                                is_hydrated=False,
                                folder=folder_name,
                                message_id=message_uid,
                            )
                        )
                        if received_dt_str and (not highest_dt or received_dt_str > highest_dt):
                            highest_dt = received_dt_str

                        if email_limit is not None and len(emails) >= email_limit:
                            break

                    next_url = cast(str | None, payload.get("@odata.nextLink"))

                if highest_dt:
                    state_out[folder_id] = highest_dt

        self._last_scan_state = _serialize_graph_state(state_out)
        return emails

    async def _iter_mail_folders(self, client: Any, headers: dict[str, str]):
        # includeHiddenFolders=true: without it, Microsoft Graph omits
        # Clutter, some Archive configurations, and any user-created
        # hidden folders. Invoice emails sometimes get auto-sorted into
        # these by Outlook rules, so skipping hidden folders silently
        # drops valid invoice candidates. Must be applied at BOTH the
        # root and every child-folder listing.
        seen_urls: set[str] = set()
        stack: list[str] = [
            f"{GRAPH_BASE_URL}/me/mailFolders?$top=100&includeHiddenFolders=true"
        ]
        while stack:
            url = stack.pop()
            while url:
                if url in seen_urls:
                    break
                seen_urls.add(url)
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                payload = response.json()
                for folder in payload.get("value", []):
                    yield folder
                    folder_id = cast(str, folder.get("id") or "")
                    if folder_id and int(folder.get("childFolderCount") or 0) > 0:
                        child_url = (
                            f"{GRAPH_BASE_URL}/me/mailFolders/{folder_id}/childFolders"
                            f"?$top=100&includeHiddenFolders=true"
                        )
                        if child_url not in seen_urls:  # pragma: no branch
                            stack.append(child_url)
                url = cast(str | None, payload.get("@odata.nextLink"))

    def _should_skip_folder(self, folder: dict[str, Any]) -> bool:
        well_known = str(folder.get("wellKnownName") or "").lower()
        if well_known in self.SKIP_WELL_KNOWN_FOLDERS:
            return True
        odata_type = str(folder.get("@odata.type") or "")
        if odata_type == "#microsoft.graph.mailSearchFolder":
            return True
        total = folder.get("totalItemCount")
        if isinstance(total, int) and total == 0:
            return True
        return False

    async def hydrate_email(self, account: EmailAccount, email: RawEmail) -> RawEmail:
        if email.is_hydrated:
            return email
        graph_id = email.headers.get("_graph_id", "")
        has_attachments = email.headers.get("_has_attachments", "False") == "True"
        if not graph_id:
            email.is_hydrated = True
            return email
        try:
            access_token = await self._acquire_access_token(account)
            headers_auth = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=30.0) as client:
                body_resp = await client.get(
                    f"{GRAPH_BASE_URL}/me/messages/{graph_id}",
                    headers=headers_auth,
                    params={"$select": "body"},
                )
                body_resp.raise_for_status()
                body = cast(dict[str, Any], body_resp.json().get("body") or {})
                body_type = str(body.get("contentType") or "").lower()
                raw_body = str(body.get("content") or "")
                email.body_text = raw_body if body_type == "text" else _html_to_text(raw_body)
                email.body_html = raw_body if body_type == "html" else ""
                email.body_links = _extract_urls(email.body_text, email.body_html)

                if has_attachments:
                    email.attachments = await self._fetch_attachments(client, headers_auth, graph_id)
        except OUTLOOK_CONNECTION_ERRORS as exc:
            logger.warning("Outlook hydrate failed for uid=%s: %s", email.uid, exc)
        email.is_hydrated = True
        return email

    async def test_connection(self, account: EmailAccount) -> bool:
        try:
            access_token = await self._acquire_access_token(account)
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    f"{GRAPH_BASE_URL}/me/mailFolders/inbox/messages",
                    headers=headers,
                    params={"$top": "1", "$select": "id"},
                )
                response.raise_for_status()
            return True
        except OUTLOOK_CONNECTION_ERRORS as exc:
            logger.exception("Outlook connection test failed for account %s: %s", account.id, exc)
            return False

    async def has_cached_token_async(self, account: EmailAccount) -> bool:
        try:
            await self._acquire_access_token(account)
        except RuntimeError:
            return False
        return True

    async def initiate_device_flow_async(self, account: EmailAccount) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._initiate_device_flow_sync, account))

    async def complete_device_flow_async(self, account: EmailAccount, flow: dict[str, Any]) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self._complete_device_flow_sync, account, flow))

    async def _acquire_access_token(self, account: EmailAccount) -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, functools.partial(self._acquire_token_sync, account))
        access_token = result.get("access_token")
        if not access_token:
            raise RuntimeError(f"Outlook auth failed: {result.get('error_description', 'unknown')}")
        return str(access_token)

    def _acquire_token_sync(self, account: EmailAccount) -> dict[str, Any]:
        if not account.oauth_token_path:
            raise RuntimeError("Outlook authorization required. Use the Settings page to authenticate.")
        token_cache = self._load_cache(account)
        client_id, authority = _get_outlook_msal_params(account)
        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=token_cache,
        )

        result: dict[str, Any] | None = None
        accounts = app.get_accounts()
        if accounts:
            result = cast(dict[str, Any] | None, app.acquire_token_silent(self.SCOPES, account=accounts[0]))

        if not result:
            raise RuntimeError("Outlook authorization required. Use the Settings page to authenticate.")

        self._save_cache(account, token_cache)
        return result or {}

    def _initiate_device_flow_sync(self, account: EmailAccount) -> dict[str, Any]:
        token_cache = self._load_cache(account)
        client_id, authority = _get_outlook_msal_params(account)
        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=token_cache,
        )
        flow = cast(dict[str, Any], app.initiate_device_flow(scopes=self.SCOPES))
        if "user_code" not in flow:
            raise RuntimeError("Failed to start Outlook device flow")
        return flow

    def _complete_device_flow_sync(self, account: EmailAccount, flow: dict[str, Any]) -> dict[str, Any]:
        token_cache = self._load_cache(account)
        client_id, authority = _get_outlook_msal_params(account)
        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=token_cache,
        )
        result = cast(dict[str, Any], app.acquire_token_by_device_flow(flow))
        self._save_cache(account, token_cache)
        return result

    def _complete_device_flow_with_path_sync(
        self, flow: dict[str, Any], token_path: str | None, outlook_type: str
    ) -> dict[str, Any]:
        token_cache = msal.SerializableTokenCache()
        if token_path:
            try:
                token_cache.deserialize(Path(token_path).read_text(encoding="utf-8"))
            except FileNotFoundError:
                pass

        client_id, authority = _get_outlook_msal_params_from_type(outlook_type)
        app = msal.PublicClientApplication(
            client_id=client_id,
            authority=authority,
            token_cache=token_cache,
        )
        result = cast(dict[str, Any], app.acquire_token_by_device_flow(flow))

        if token_path and (result.get("access_token") or token_cache.has_state_changed):
            path = Path(token_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token_cache.serialize(), encoding="utf-8")

        return result

    async def complete_device_flow_async_with_path(
        self, flow: dict[str, Any], token_path: str | None, outlook_type: str
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            functools.partial(self._complete_device_flow_with_path_sync, flow, token_path, outlook_type),
        )

    async def _fetch_attachments(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        message_id: str,
    ) -> list[RawAttachment]:
        if not message_id:
            return []

        response = await client.get(
            f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments",
            headers=headers,
            params={"$top": "50"},
        )
        response.raise_for_status()

        attachments: list[RawAttachment] = []
        for item in response.json().get("value", []):
            if item.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            filename = _resolve_filename(cast(str | None, item.get("name")), "attachment.bin")
            ext = Path(filename).suffix.lower()
            if ext not in INVOICE_EXTENSIONS:
                continue
            payload_base64 = item.get("contentBytes")
            if not isinstance(payload_base64, str):
                continue
            attachments.append(
                RawAttachment(
                    filename=filename,
                    payload=base64.b64decode(payload_base64),
                    content_type=str(item.get("contentType") or "application/octet-stream"),
                )
            )
        return attachments

    def _load_cache(self, account: EmailAccount) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        token_path = account.oauth_token_path
        if not token_path:
            return cache
        try:
            cache.deserialize(Path(token_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cache
        return cache

    def _save_cache(self, account: EmailAccount, cache: msal.SerializableTokenCache) -> None:
        token_path = account.oauth_token_path
        if not token_path or not cache.has_state_changed:
            return
        path = Path(token_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(cache.serialize(), encoding="utf-8")

    def _recent_message_filter(self) -> str:
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        return f"receivedDateTime ge {since}"


class ScannerFactory:
    @staticmethod
    def get_scanner(account_type: str) -> BaseEmailScanner:
        scanners: dict[str, BaseEmailScanner] = {
            "imap": ImapScanner(),
            "pop3": Pop3Scanner(),
            "outlook": OutlookScanner(),
            "qq": ImapScanner(),
        }
        scanner = scanners.get(account_type)
        if scanner is None:
            raise ValueError(f"Unknown email account type: {account_type}")
        return scanner


__all__ = [
    "BaseEmailScanner",
    "ImapScanner",
    "OAuthFlowRegistry",
    "OAuthFlowState",
    "OutlookScanner",
    "Pop3Scanner",
    "RawAttachment",
    "RawEmail",
    "ScannerFactory",
    "_get_outlook_msal_params",
    "_is_personal_microsoft_account",
    "decrypt_password",
    "encrypt_password",
    "oauth_registry",
]
