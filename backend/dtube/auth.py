import hashlib
import secrets
import threading
import time

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError
from fastapi import HTTPException, Request, WebSocket

from .db import connect, transaction

COOKIE = "dtube_session"
SESSION_DAYS = 30
WINDOW = 900
MAX_PER_CLIENT = 5
MAX_TOTAL = 50

hasher = PasswordHasher()
_failures = []
_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def needs_setup() -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users").fetchone() is None


def create_admin(username: str, password: str) -> bool:
    """Create the only account. The check and insert share a transaction, so it can happen once."""
    hashed = hasher.hash(password)
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM users").fetchone():
            return False
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
    return True


def verify(username: str, password: str):
    with connect() as conn:
        user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    try:
        if user:
            hasher.verify(user["password_hash"], password)
            return user
        # Spend the same time as a real check, so usernames can't be probed.
        hasher.hash(password)
    except (Argon2Error, InvalidHashError):
        pass
    return None


def change_password(user_id: int, password: str):
    with transaction() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hasher.hash(password), user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?)",
            (_hash_token(token), user_id, now + SESSION_DAYS * 86400),
        )
    return token


def delete_session(token: str):
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


def _session_user(token: str | None):
    if not token:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.expires_at > ?",
            (_hash_token(token), time.time()),
        ).fetchone()


def require_user(request: Request):
    user = _session_user(request.cookies.get(COOKIE))
    if not user:
        raise HTTPException(401, "Not logged in")
    return user


def websocket_allowed(socket: WebSocket) -> bool:
    # Compare hostnames only: a proxy on a non-standard port forwards Host without the port.
    origin = socket.headers.get("origin", "").split("://")[-1].split(":")[0]
    host = socket.headers.get("host", "").split(":")[0]
    if not origin or origin != host:
        return False
    return _session_user(socket.cookies.get(COOKIE)) is not None


def client_ip(request: Request) -> str:
    # The proxy appends the address it saw, so the last entry is the trustworthy one.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def check_rate_limit(ip: str):
    cutoff = time.time() - WINDOW
    with _lock:
        _failures[:] = [f for f in _failures if f[0] > cutoff]
        per_client = sum(1 for f in _failures if f[1] == ip)
        if per_client >= MAX_PER_CLIENT or len(_failures) >= MAX_TOTAL:
            raise HTTPException(429, "Too many failed logins. Try again in 15 minutes.")


def record_failure(ip: str):
    with _lock:
        _failures.append((time.time(), ip))
