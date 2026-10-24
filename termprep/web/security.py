"""Security helpers for the TermPrep web API.

Implements the HARNESS task book items:

* §5.1 — admin authentication for destructive / write / upload / debug routes
* §5.2 — upload validation (extension whitelist, size cap, filename sanitising)
* §5.3 — debug endpoint information convergence (no infrastructure disclosure)

Security red lines (§1) respected here:

* the admin token is read from the environment only and is **never** logged,
  echoed back, or included in any response body;
* privileged routes **fail closed** (503) when no token is configured, so a
  mis-configured deployment cannot be written to anonymously.
"""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException

# Environment variable holding the admin token (HF Space: Settings → Secrets).
ADMIN_TOKEN_ENV = "TERMPREP_ADMIN_TOKEN"
# Header the front-end uses to present the token.
ADMIN_TOKEN_HEADER = "X-Admin-Token"

# ── §5.2 upload policy ──
# `.doc` and `.xlsx` are included in addition to the task book's list because
# ``termprep.translator.parse_file()`` supports them natively and dropping them
# would break existing document translation.
ALLOWED_UPLOAD_EXTENSIONS: frozenset[str] = frozenset(
    {".txt", ".md", ".csv", ".json", ".docx", ".pdf", ".doc", ".xlsx"}
)

# Single file cap (§5.2).
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
# Whole-request cap, leaving head-room for multipart framing overhead.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 2 * 1024 * 1024  # 12 MB


def configured_admin_token() -> str:
    """Return the configured admin token (never log or return this value)."""
    return (os.environ.get(ADMIN_TOKEN_ENV) or "").strip()


def admin_token_configured() -> bool:
    """Whether an admin token is configured at all."""
    return bool(configured_admin_token())


async def require_admin(
    x_admin_token: str = Header(default="", alias=ADMIN_TOKEN_HEADER),
) -> bool:
    """FastAPI dependency guarding privileged (write/upload/debug) routes.

    * 503 when the deployment has no token configured (fail closed);
    * 401 when the presented token is missing or wrong.
    """
    expected = configured_admin_token()
    if not expected:
        raise HTTPException(status_code=503, detail="admin token not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="unauthorized")
    return True


def clean_upload_filename(raw: str | None) -> str:
    """Strip directory components from a client-supplied file name.

    Defeats path-traversal payloads such as ``../../etc/passwd`` or
    ``..\\..\\evil.doc`` (§5.2).
    """
    name = (raw or "").replace("\\", "/").strip()
    name = os.path.basename(name).strip()
    # Remove any residual traversal / control characters.
    name = name.replace("\x00", "")
    if name in ("", ".", ".."):
        return "upload"
    return name


def validate_upload(filename: str | None, size: int) -> str:
    """Validate an upload against the whitelist and size cap.

    Returns the sanitised file name. Raises ``HTTPException`` (4xx) when the
    extension is not whitelisted or the file is empty / too large.
    """
    safe_name = clean_upload_filename(filename)
    ext = os.path.splitext(safe_name)[1].lower()
    if not ext:
        raise HTTPException(status_code=400, detail="文件缺少扩展名，无法识别格式")
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
        raise HTTPException(
            status_code=400, detail=f"不支持的文件类型 {ext}；仅允许: {allowed}"
        )
    if size <= 0:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if size > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413, detail=f"文件过大，单文件上限 {limit_mb} MB"
        )
    return safe_name


def precheck_content_length(content_length: str | None) -> None:
    """Reject oversized request bodies before reading them into memory (§5.2)."""
    if not content_length:
        return
    try:
        declared = int(content_length)
    except (TypeError, ValueError):
        return
    if declared > MAX_REQUEST_BYTES:
        limit_mb = MAX_REQUEST_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413, detail=f"请求体过大，上限 {limit_mb} MB"
        )
