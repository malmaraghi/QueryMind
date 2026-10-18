"""Web-layer protections for the QueryMind Flask application.

Small, dependency-free building blocks used by ``app.py``:

* ``SessionStore``  - server-side sessions: the browser only holds a random session id, never database credentials;
* ``RateLimiter``   - login rate limiting per client address and per database account;
* ``resolve_db_host`` - policy for the database host a user may connect to (blocks unintended internal-network access);
* input validation helpers, CSRF token helpers, per-session search-index directories and security headers.

None of this touches the three SELECT-only layers (``security.py``), SQL generation, embeddings or retrieval.
Sessions and rate-limit counters live in the memory of ONE process; see docs/SECURITY_LAYERS.md for the limits.
"""
import hmac
import ipaddress
import os
import re
import secrets
import shutil
import socket
import threading
import time
from collections import deque
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# ------------------------------------------------------------------------------------------------ sessions


class SessionStore:
    """In-memory server-side sessions with idle and absolute timeouts and a size cap.

    ``on_expire(data)`` is called whenever a session ends (logout, timeout or eviction) so resources such as the
    per-session search index can be removed.
    """

    def __init__(self, idle_seconds: float = 1800, absolute_seconds: float = 8 * 3600, max_sessions: int = 200,
                 clock: Callable[[], float] = time.monotonic, on_expire: Optional[Callable[[dict], None]] = None):
        self._idle, self._absolute, self._max = idle_seconds, absolute_seconds, max_sessions
        self._clock, self._on_expire = clock, on_expire
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.RLock()

    def _expired(self, entry: dict, now: float) -> bool:
        return now - entry["last_seen"] > self._idle or now - entry["created"] > self._absolute

    def _drop(self, sid: str) -> None:
        entry = self._sessions.pop(sid, None)
        if entry and self._on_expire:
            try:
                self._on_expire(entry["data"])
            except Exception:  # noqa: BLE001 - cleanup must never break a request
                pass

    def purge(self) -> int:
        with self._lock:
            now = self._clock()
            stale = [sid for sid, entry in self._sessions.items() if self._expired(entry, now)]
            for sid in stale:
                self._drop(sid)
            return len(stale)

    def create(self, data: dict) -> str:
        with self._lock:
            self.purge()
            while len(self._sessions) >= self._max:
                oldest = min(self._sessions, key=lambda s: self._sessions[s]["last_seen"])
                self._drop(oldest)
            sid = secrets.token_urlsafe(32)
            now = self._clock()
            self._sessions[sid] = {"data": dict(data), "created": now, "last_seen": now}
            return sid

    def get(self, sid: Optional[str]) -> Optional[dict]:
        if not sid or not isinstance(sid, str) or len(sid) > 128:
            return None
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is None:
                return None
            now = self._clock()
            if self._expired(entry, now):
                self._drop(sid)
                return None
            entry["last_seen"] = now
            return entry["data"]

    def delete(self, sid: Optional[str]) -> None:
        if sid:
            with self._lock:
                self._drop(sid)

    def __len__(self) -> int:
        return len(self._sessions)


# ------------------------------------------------------------------------------------------------ rate limiting


class RateLimiter:
    """Sliding-window limiter for failed logins: ``max_failures`` within ``window`` seconds blocks the key."""

    def __init__(self, max_failures: int = 5, window: float = 300, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic):
        self._max, self._window, self._max_keys, self._clock = max_failures, window, max_keys, clock
        self._failures: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def _trim(self, key: str, now: float) -> deque:
        queue = self._failures.get(key)
        if queue is None:
            return deque()
        while queue and now - queue[0] > self._window:
            queue.popleft()
        if not queue:
            self._failures.pop(key, None)
        return queue

    def blocked_for(self, key: str) -> int:
        """Seconds until ``key`` may try again (0 if it is not blocked)."""
        with self._lock:
            now = self._clock()
            queue = self._trim(key, now)
            if len(queue) >= self._max:
                return max(1, int(self._window - (now - queue[0])) + 1)
            return 0

    def failure(self, key: str) -> None:
        with self._lock:
            now = self._clock()
            if key not in self._failures and len(self._failures) >= self._max_keys:
                for old in [k for k in list(self._failures) if not self._trim(k, now)]:
                    self._failures.pop(old, None)
                if len(self._failures) >= self._max_keys:
                    self._failures.pop(next(iter(self._failures)))
            self._failures.setdefault(key, deque()).append(now)

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


# ------------------------------------------------------------------------------------------------ database host policy


class HostPolicyError(ValueError):
    """The requested database host is not permitted (message is safe to show to the user)."""


_HOST_CHARS = re.compile(r"^[A-Za-z0-9._:\-\[\]]{1,255}$")


def parse_allowlist(text: Optional[str]) -> List[str]:
    return [part.strip().lower() for part in (text or "").split(",") if part.strip()]


def validate_port(value: str) -> int:
    try:
        port = int(str(value).strip())
    except ValueError as error:
        raise HostPolicyError("The port must be a number between 1 and 65535.") from error
    if not 1 <= port <= 65535:
        raise HostPolicyError("The port must be a number between 1 and 65535.")
    return port


def _ip_in_allowlist(ip, host: str, allowlist: Iterable[str]) -> bool:
    for entry in allowlist:
        if entry == host.lower():
            return True
        try:
            if ip in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def resolve_db_host(host: str, allowlist: Iterable[str] = (),
                    resolver: Callable = socket.getaddrinfo) -> str:
    """Return the address to connect to, or raise ``HostPolicyError``.

    Always refused: unspecified, multicast, link-local (including cloud metadata addresses such as 169.254.169.254) and
    reserved addresses. Allowed by default: loopback only. Any other host (or address range) must be listed in
    ``QUERYMIND_ALLOWED_DB_HOSTS`` (host names, IP addresses or CIDR ranges). Host names are resolved once and the
    connection is made to the checked address, so a name cannot later point somewhere else.
    """
    host = (host or "").strip()
    if not _HOST_CHARS.match(host):
        raise HostPolicyError("The database host is not valid.")
    allowlist = list(allowlist)
    if host.lower() == "localhost":
        return "localhost"                      # loopback by definition (unix socket or 127.0.0.1)
    literal = host.strip("[]")
    try:
        candidates = [ipaddress.ip_address(literal)]
    except ValueError:
        try:
            infos = resolver(host, None, type=socket.SOCK_STREAM)
        except (socket.gaierror, UnicodeError, OSError) as error:
            raise HostPolicyError("The database host could not be resolved.") from error
        candidates = []
        for info in infos:
            try:
                candidates.append(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not candidates:
            raise HostPolicyError("The database host could not be resolved.")
    chosen = None
    for ip in candidates:
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_loopback and (ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved):
            raise HostPolicyError("This database host is not allowed by the server's policy.")
        if not ip.is_loopback and not _ip_in_allowlist(ip, host, allowlist):
            raise HostPolicyError("This database host is not allowed by the server's policy. "
                                  "The server administrator can allow it with QUERYMIND_ALLOWED_DB_HOSTS.")
        if chosen is None or (chosen.version == 6 and ip.version == 4):
            chosen = ip
    return str(chosen)


# ------------------------------------------------------------------------------------------------ input validation

_USER_CHARS = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")
_DB_NAME = re.compile(r"^[A-Za-z0-9_$.\-]{1,128}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$]{1,64}$")


def is_identifier(name: str) -> bool:
    """A plain table name (letters, digits, underscore, dollar; at most 64 characters)."""
    return bool(isinstance(name, str) and _IDENTIFIER.match(name))


def validate_login_fields(form) -> Tuple[Optional[dict], Optional[str]]:
    """Return ``(clean fields, None)`` or ``(None, reason)``."""
    host = (form.get("db_host", "localhost") or "").strip()
    user = (form.get("db_user", "") or "").strip()
    password = form.get("db_password", "") or ""
    name = (form.get("db_name", "") or "").strip()
    if not user or not name:
        return None, "missing user or database"
    if not _USER_CHARS.match(user):
        return None, "invalid user"
    if not _DB_NAME.match(name):
        return None, "invalid database name"
    if len(password) > 256 or "\x00" in password:
        return None, "invalid password"
    try:
        port = validate_port(form.get("db_port", "3306") or "3306")
    except HostPolicyError:
        return None, "invalid port"
    return {"host": host, "port": port, "user": user, "password": password, "name": name}, None


# ------------------------------------------------------------------------------------------------ CSRF


def new_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: Optional[str], supplied: Optional[str]) -> bool:
    return bool(expected) and bool(supplied) and hmac.compare_digest(str(expected).encode(), str(supplied).encode())


# ------------------------------------------------------------------------------------------------ per-session indexes

INDEX_PREFIX = "sess-"


def new_index_path(base: str) -> str:
    """A fresh, unguessable search-index directory for one login (never shared between sessions)."""
    return os.path.join(base, INDEX_PREFIX + secrets.token_hex(16))


def remove_index_dir(path: Optional[str], base: str) -> bool:
    """Delete a per-session index directory; refuses anything that is not ``<base>/sess-<hex>``."""
    if not path:
        return False
    real_base, real = os.path.realpath(base), os.path.realpath(path)
    if os.path.dirname(real) != real_base or not os.path.basename(real).startswith(INDEX_PREFIX):
        return False
    shutil.rmtree(real, ignore_errors=True)
    return True


def purge_stale_index_dirs(base: str, max_age_seconds: float) -> int:
    """Remove ``sess-*`` directories older than ``max_age_seconds`` (leftovers of a restarted process)."""
    removed = 0
    if not os.path.isdir(base):
        return 0
    now = time.time()
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if name.startswith(INDEX_PREFIX) and os.path.isdir(path) and now - os.path.getmtime(path) > max_age_seconds:
            removed += remove_index_dir(path, base)
    return removed


# ------------------------------------------------------------------------------------------------ headers


def apply_headers(response, nonce: str, https: bool, static: bool = False):
    """Security headers. The CSP allows only this server's own scripts (nonce) and styles; inline scripts need the nonce."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self' 'nonce-{}'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'".format(nonce))
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if not static:
        response.headers["Cache-Control"] = "no-store"
    if https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response
