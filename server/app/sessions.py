"""Server-side session registry.

Flask sessions are signed cookies with nothing stored server-side, so on
their own a copied cookie outlives logout, and because Flask re-signs the
cookie on every request an active one never expires. This registry makes
sessions revocable: login issues a random session id that is stored in the
signed cookie, and every authenticated request checks it here.

- A session ends SESSION_LIFETIME after login, however active it is.
- Logout revokes its id; revoke_user() ends all of a user's sessions.
- At most MAX_SESSIONS are kept; past that the oldest are evicted (those
  users have to log in again).

State is in memory, which is accurate because guise runs as a single
process (see the gunicorn notes), and resets on restart: restarting guise
logs everyone out.
"""
import secrets
import threading
import time
from collections import OrderedDict
from typing import Literal

SESSION_LIFETIME = 12 * 60 * 60.0
MAX_SESSIONS = 10_000

Status = Literal["ok", "unknown", "expired"]


class SessionRegistry:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        # sid -> (user, time of login). Insertion order is login order, so
        # the oldest (and the first to expire) are at the front.
        self._sessions: OrderedDict[str, tuple[str, float]] = OrderedDict()

    def _drop_expired(self, now: float) -> None:
        while self._sessions:
            _, created = next(iter(self._sessions.values()))
            if now - created < SESSION_LIFETIME:
                break
            self._sessions.popitem(last=False)

    def create(self, user: str) -> str:
        sid = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            self._drop_expired(now)
            self._sessions[sid] = (user, now)
            while len(self._sessions) > MAX_SESSIONS:
                self._sessions.popitem(last=False)
        return sid

    def status(self, sid: object, user: object) -> Status:
        """"ok" if sid is a live session for user, else "unknown" or "expired"."""
        if not isinstance(sid, str) or not isinstance(user, str):
            return "unknown"
        now = self._clock()
        with self._lock:
            entry = self._sessions.get(sid)
            if entry is None:
                return "unknown"
            stored_user, created = entry
            if now - created >= SESSION_LIFETIME:
                del self._sessions[sid]
                return "expired"
            if not secrets.compare_digest(stored_user.encode(), user.encode()):
                return "unknown"
            return "ok"

    def check(self, sid: object, user: object) -> bool:
        return self.status(sid, user) == "ok"

    def revoke(self, sid: object) -> None:
        if not isinstance(sid, str):
            return
        with self._lock:
            self._sessions.pop(sid, None)

    def revoke_user(self, user: str) -> None:
        with self._lock:
            for sid in [s for s, (u, _) in self._sessions.items() if u == user]:
                del self._sessions[sid]
