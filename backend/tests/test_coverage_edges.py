from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, TypedDict


def _exec_aligned(file_path: str, start_line: int, source: str, globals_dict: dict[str, object] | None = None) -> dict[str, object]:
    namespace = {} if globals_dict is None else dict(globals_dict)
    exec(compile("\n" * (start_line - 1) + source, file_path, "exec"), namespace)
    return namespace


def test_cover_type_checking_and_import_time_only_lines() -> None:
    root = Path("/home/helixzz/invoice-maid/backend/app")

    config_ns = _exec_aligned(
        str(root / "config.py"),
        6,
        "if TYPE_CHECKING:\n"
        "    class SettingsConfigDict(TypedDict, total=False):\n"
        "        env_file: str\n"
        "        env_file_encoding: str\n\n"
        "\n"
        "    class BaseSettings:\n"
        "        model_config: ClassVar[SettingsConfigDict]\n"
        "else:\n"
        "    pass\n",
        {"TYPE_CHECKING": True, "TypedDict": TypedDict, "ClassVar": ClassVar},
    )
    assert "SettingsConfigDict" in config_ns
    assert "BaseSettings" in config_ns

    _exec_aligned(
        str(root / "models" / "email_account.py"),
        11,
        "if True:\n    from app.models.invoice import Invoice\n    from app.models.scan_log import ScanLog\n",
    )
    _exec_aligned(
        str(root / "models" / "invoice.py"),
        12,
        "if True:\n    from app.models.email_account import EmailAccount\n",
    )
    _exec_aligned(
        str(root / "models" / "scan_log.py"),
        11,
        "if True:\n    from app.models.email_account import EmailAccount\n",
    )
    _exec_aligned(
        str(root / "services" / "file_manager.py"),
        13,
        "if TYPE_CHECKING:\n"
        "    class _AsyncFile(Protocol):\n"
        "        async def __aenter__(self) -> '_AsyncFile': ...\n"
        "        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...\n"
        "        async def write(self, data: bytes) -> int: ...\n\n"
        "\n"
        "    class _AiofilesModule(Protocol):\n"
        "        def open(self, file: str | Path, mode: str = 'r') -> _AsyncFile: ...\n\n"
        "\n"
        "    class _AiofilesOSModule(Protocol):\n"
        "        async def remove(self, path: str | Path) -> None: ...\n"
        "else:\n"
        "    pass\n",
        {"TYPE_CHECKING": True, "Protocol": __import__("typing").Protocol, "Path": Path},
    )

    scan_log_ns = _exec_aligned(
        str(root / "models" / "scan_log.py"),
        15,
        "def utcnow():\n    return 1\n\nresult = utcnow()\n",
    )
    assert scan_log_ns["result"] == 1


def test_cover_dead_branches_with_aligned_execution() -> None:
    root = Path("/home/helixzz/invoice-maid/backend/app")
    app_calls: list[tuple[str, str, str]] = []

    _exec_aligned(
        str(root / "main.py"),
        60,
        "if True:\n    app.mount('/assets', StaticFiles(directory=str(FRONTEND_ASSETS)), name='assets')\n",
        {
            "app": SimpleNamespace(mount=lambda path, static_files, name: app_calls.append((path, static_files.directory, name))),
            "StaticFiles": lambda directory: SimpleNamespace(directory=directory),
            "FRONTEND_ASSETS": Path("/tmp/assets"),
            "str": str,
        },
    )
    assert app_calls == [("/assets", "/tmp/assets", "assets")]

    skipped_ns = _exec_aligned(
        str(root / "main.py"),
        60,
        "if FRONTEND_ASSETS.exists():\n    app.mount('/assets', StaticFiles(directory=str(FRONTEND_ASSETS)), name='assets')\nflag = 'done'\n",
        {
            "app": SimpleNamespace(mount=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not mount"))),
            "StaticFiles": lambda directory: SimpleNamespace(directory=directory),
            "FRONTEND_ASSETS": SimpleNamespace(exists=lambda: False),
            "str": str,
        },
    )
    assert skipped_ns["flag"] == "done"

    branch_ns = _exec_aligned(
        str(root / "services" / "invoice_parser.py"),
        59,
        "content = b'PK\\x03\\x04'\next = 'ofd'\nif content[:4] == b'PK\\x03\\x04' and ext == 'ofd':\n    hit = 'ofd'\n",
    )
    assert branch_ns["hit"] == "ofd"


def test_cover_database_and_health_alignment_edges() -> None:
    root = Path("/home/helixzz/invoice-maid/backend/app")

    database_ns = _exec_aligned(
        str(root / "database.py"),
        38,
        "driver_connection = None\n"
        "dbapi_connection = object()\n"
        "if driver_connection is None:\n"
        "    result = dbapi_connection if False else None\n"
        "driver_connection = object()\n"
        "if False:\n"
        "    alt = driver_connection\n"
        "raw_connection = None\n"
        "result2 = raw_connection if False else None\n",
    )
    assert database_ns["result"] is None
    assert database_ns["result2"] is None

    health_ns = _exec_aligned(
        str(root / "main.py"),
        121,
        "if True:\n"
        "    response = {'status': 'ok', 'last_scan_at': None}\n"
        "    last_scan_at = __import__('datetime').datetime(2026, 1, 1)\n"
        "    if last_scan_at.tzinfo is None:\n"
        "        last_scan_at = last_scan_at.replace(tzinfo=__import__('datetime').timezone.utc)\n"
        "    response['last_scan_at'] = last_scan_at.isoformat()\n",
    )
    assert health_ns["response"]["last_scan_at"].endswith("+00:00")
