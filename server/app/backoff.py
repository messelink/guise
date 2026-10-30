"""Per-username login backoff.

Every IMAP login reaches dovecot from guise's own container IP, so the
mailserver's fail2ban can't tell attackers apart (and ignores that IP
anyway). This tracker throttles failed logins per account instead:

- (username, client network): FREE_PER_PAIR failures, then a lock that
  starts at BASE_DELAY and doubles per further failure, capped at
  MAX_DELAY. IPv6 clients are grouped by /64, since one host usually owns
  a whole /64 and could otherwise rotate addresses for free.
- username across all clients: the same curve after FREE_PER_USER
  failures, so spreading guesses over many IPs doesn't help.

A client that logged in successfully as a username within TRUST_FOR is
exempt from that username's user-wide lock (its own pair lock still
applies). An attacker can't earn the exemption without the password, so
the real user's usual devices keep working while someone hammers the
account from elsewhere.

Only one password check per username runs at a time (begin() refuses a
second while the first is waiting on dovecot), so concurrent requests
can't all slip past an expired lock before any failure is recorded.

A single lock never lasts longer than MAX_DELAY. State is in memory, which
is accurate because guise runs as a single process (see the gunicorn
notes), and resets on restart.
"""
import ipaddress
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

FREE_PER_PAIR = 5
FREE_PER_USER = 20
BASE_DELAY = 30.0
MAX_DELAY = 15 * 60.0
EXPIRE_AFTER = 60 * 60.0
MAX_ENTRIES = 10_000
TRUST_FOR = 30 * 24 * 60 * 60.0
MAX_TRUSTED = 10_000
# Doubling stops once the cap is reached; this also keeps 2**n finite.
_MAX_STEPS = 16


@dataclass
class _Entry:
    failures: int
    last_failure: float
    locked_until: float


def _client_net(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return str(addr)
    if addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(ipaddress.ip_network(f"{addr}/64", strict=False))


class _Table:
    """Bounded map of failure counters. When full it evicts, in order: entries
    still within their free attempts, then escalated ones whose lock has run
    out, then the lock closest to expiry. So spraying new keys can't reset
    an attacker's escalation."""

    def __init__(self, free: int, clock):
        self.free = free
        self._clock = clock
        self.entries: OrderedDict[tuple, _Entry] = OrderedDict()

    def live(self, key: tuple, now: float) -> _Entry | None:
        entry = self.entries.get(key)
        if entry is not None and now - entry.last_failure > EXPIRE_AFTER:
            del self.entries[key]
            return None
        return entry

    def wait(self, key: tuple, now: float) -> float:
        entry = self.live(key, now)
        return max(0.0, entry.locked_until - now) if entry else 0.0

    def fail(self, key: tuple, now: float) -> None:
        entry = self.live(key, now) or _Entry(0, now, 0.0)
        entry.failures += 1
        entry.last_failure = now
        if entry.failures > self.free:
            steps = min(entry.failures - self.free - 1, _MAX_STEPS)
            entry.locked_until = now + min(BASE_DELAY * 2**steps, MAX_DELAY)
        self.entries[key] = entry
        self.entries.move_to_end(key)
        self._evict(now)

    def _evict(self, now: float) -> None:
        while len(self.entries) > MAX_ENTRIES:
            victim = next((k for k, e in self.entries.items() if e.failures <= self.free), None)
            if victim is None:
                victim = next(
                    (k for k, e in self.entries.items() if e.locked_until <= now), None,
                )
            if victim is None:
                # Everything is locked: memory still has to stay bounded.
                victim = min(self.entries, key=lambda k: self.entries[k].locked_until)
            del self.entries[victim]


class LoginBackoff:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._pairs = _Table(FREE_PER_PAIR, clock)
        self._users = _Table(FREE_PER_USER, clock)
        self._in_flight: set[str] = set()
        # (username, client net) -> time of last successful login there.
        self._trusted: OrderedDict[tuple, float] = OrderedDict()

    def _is_trusted(self, key: tuple, now: float) -> bool:
        since = self._trusted.get(key)
        if since is None:
            return False
        if now - since > TRUST_FOR:
            del self._trusted[key]
            return False
        return True

    def _wait(self, username: str, net: str, now: float) -> float:
        pair_wait = self._pairs.wait((username, net), now)
        if self._is_trusted((username, net), now):
            return pair_wait
        return max(pair_wait, self._users.wait((username,), now))

    def retry_after(self, username: str, ip: str) -> float:
        """Seconds until this username may try again from this IP; 0 if allowed."""
        with self._lock:
            return self._wait(username, _client_net(ip), self._clock())

    def begin(self, username: str, ip: str) -> str:
        """Claim the right to check a password: "ok", "throttled", or "busy"
        (another check for this username is waiting on dovecot). On "ok" the
        caller must call record_failure or record_success, which release it."""
        with self._lock:
            if self._wait(username, _client_net(ip), self._clock()) > 0:
                return "throttled"
            if username in self._in_flight:
                return "busy"
            self._in_flight.add(username)
            return "ok"

    def record_failure(self, username: str, ip: str) -> None:
        now = self._clock()
        with self._lock:
            self._in_flight.discard(username)
            self._pairs.fail((username, _client_net(ip)), now)
            self._users.fail((username,), now)

    def record_success(self, username: str, ip: str) -> None:
        # Only the pair resets. The username-wide counter tracks guesses from
        # other IPs, and the real user logging in doesn't make those go away.
        now = self._clock()
        key = (username, _client_net(ip))
        with self._lock:
            self._in_flight.discard(username)
            self._pairs.entries.pop(key, None)
            # Only real logins add entries, so plain LRU eviction is safe here.
            self._trusted[key] = now
            self._trusted.move_to_end(key)
            while len(self._trusted) > MAX_TRUSTED:
                self._trusted.popitem(last=False)
