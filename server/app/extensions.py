from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from .backoff import client_net


def _client_key() -> str:
    # Group IPv6 clients by /64, as the login backoff does: one host usually
    # owns a whole /64 and could otherwise rotate addresses past the limits.
    return client_net(get_remote_address())


limiter = Limiter(key_func=_client_key)
