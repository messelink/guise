# guise/server

Flask + gunicorn web app for managing [`docker-mailserver`](https://github.com/docker-mailserver/docker-mailserver) aliases. Packages as a Docker image (`ghcr.io/messelink/guise`) that is deployed as a sidecar service inside the docker-mailserver compose project.

## Architecture

- **Auth**: short username + password is checked by connecting to dovecot over IMAPS (`mailserver:993`). No password store of our own. Failed logins go through dovecot's fail2ban jail.
- **Alias CRUD**: `docker exec mailserver setup alias add/del/list` via the `docker-socket-proxy` sidecar's restricted Docker API. The mailserver container is the source of truth.
- **HTTP API**: `POST /api/alias/random/new` accepts a SimpleLogin-style `Authentication: user:password` header, runs the same IMAP auth + denylist as the web UI, and creates an alias targeting the authenticated mailbox. CSRF-exempt (header-auth, no session). Optional `?hostname=` query parameter triggers PSL-aware auto-labelling. Spec in [`../docs/api.md`](../docs/api.md).
- **State guise owns**: only `/data/secret_key` (Flask session signing key, regenerated on first start). Wiping `guise-data/` and restarting is a clean reset — no user data is lost because no user data is stored.
- **Alias naming**: `<GUISE_TAG><8 hex>[-<slug>]@<GUISE_DOMAIN>`. Default tag `g-`. The tag is how guise identifies its own aliases versus legacy ones in `postfix-virtual.cf`.

## Layout

```text
server/
├── Dockerfile             python:3.12-slim + docker-ce-cli
├── Makefile               make build / make test
├── requirements.txt       flask, flask-limiter, gunicorn, tldextract
├── app/
│   ├── __init__.py        Flask factory, ProxyFix, logger bridge
│   ├── config.py          env-var loading, secret_key bootstrap
│   ├── auth.py            IMAP login, session, CSRF, denylist, redirect-safety
│   ├── aliases.py         slug, tag, docker-exec wrappers, parser, hostname-to-label
│   ├── api.py             SimpleLogin-compatible HTTP API
│   ├── routes.py          dashboard + create + delete
│   ├── extensions.py      flask-limiter singleton
│   ├── templates/{base,login,index}.html
│   └── static/{style.css,favicon.svg}
└── tests/
    ├── conftest.py        pytest fixtures (Flask test client)
    ├── test_aliases.py
    ├── test_api.py
    └── test_auth.py
```

## Build

```bash
make build           # docker build -t guise:latest .
```

## Test

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest tests/ -q
```

Tests mock `subprocess.run` and `imaplib.IMAP4_SSL` — no containers, no network.

## Configuration (env vars)

Set in the deploying compose file.

| Var | Default | Notes |
|---|---|---|
| `GUISE_DOMAIN` | *required* | Appended to short username at login; used as alias domain |
| `GUISE_TAG` | `g-` | Namespace prefix on managed aliases |
| `GUISE_DENIED_USERS` | (empty) | Comma-separated short usernames blocked from login (use for service accounts) |
| `GUISE_MAILSERVER_CONTAINER` | `mailserver` | `docker exec <this>` target |
| `GUISE_IMAP_HOST` | `mailserver` | IMAP host for auth |
| `GUISE_IMAP_PORT` | `993` | IMAPS port |
| `GUISE_IMAP_CAFILE` | (system trust store) | Optional CA bundle path for IMAP TLS validation |
| `GUISE_IMAP_INSECURE` | `false` | Disable IMAP TLS verification entirely — explicit escape hatch |
| `GUISE_API_AUTOLABEL` | `true` | Auto-label aliases from the API's `?hostname=` query parameter; set to `false` to always produce unlabeled `g-<8hex>` aliases via the API |
| `DOCKER_HOST` | (unset) | Set to `tcp://guise-socket-proxy:2375` in the default install so guise talks to the socket-proxy instead of the host docker daemon |
| `DATA_DIR` | `/data` | Where `secret_key` lives |
| `SESSION_COOKIE_SECURE` | `true` | Set false only if proxy is plain HTTP |

## Trust boundaries

- **Docker API access is mediated by a `docker-socket-proxy` sidecar** (`tecnativa/docker-socket-proxy`), included in the default install. The proxy bind-mounts `/var/run/docker.sock` and exposes only the `CONTAINERS` + `EXEC` API endpoints over `tcp://guise-socket-proxy:2375` on the project's internal Docker network — never on the host or the internet. The endpoint allowlist is what scopes the surface; the `:ro` flag on the bind mount is file-level defence in depth, not an API restriction. guise itself does not mount the host socket, is not a member of the host `docker` group, and is connected to the Docker daemon only through this restricted interface. An RCE in guise can do exactly what guise needs to do (run `setup alias …` inside the mailserver container) and nothing else: no starting new containers, no host-path mounts, no daemon reconfiguration, no access to other containers' state.
- IMAP connection uses TLS. By default the cert is validated against the system trust store (`GUISE_IMAP_CAFILE` overrides). Hostname verification is off because we reach the mailserver by container name, not by the cert CN. `GUISE_IMAP_INSECURE=1` is the explicit escape hatch for self-signed or testing setups.
- Login validation happens at dovecot. fail2ban-postfix and fail2ban-dovecot jails in the mailserver container catch brute force.
- App-level rate limit (`flask-limiter`) caps `/login` at 20/min, `POST /aliases` at 30/min, and `POST /api/alias/random/new` at 30/min per client IP. `ProxyFix` is enabled so the real client IP is used (set `RemoteIPHeader X-Forwarded-For` in your reverse proxy and trust only the proxy's IP).
- **Defence-in-depth on the web surface**: CSRF tokens validated on every unsafe-method request (except `/api/*`, which is header-authed and uses no session cookie); 16 KB `MAX_CONTENT_LENGTH` body cap; 12 h session lifetime with HttpOnly + SameSite=Lax cookies (and Secure unless `SESSION_COOKIE_SECURE=false`); atomic `O_EXCL` + `0o600` secret-key creation; open-redirect protection on the login `?next=` parameter.

### Further hardening (not default)

Worth considering for production deployments:

- `read_only: true` on the guise container with a tmpfs for `/tmp`. All writes guise actually needs are scoped to the volume-mounted `/data` dir.
- A custom AppArmor or seccomp profile for the guise container.
- Pinning `ghcr.io/messelink/guise` to a digest (`@sha256:…`) instead of `:latest` to make supply-chain compromise of the published image more visible.
- Bind-mount-only the `./guise-data` directory with `:rw,nodev,nosuid` (or similar) for defence-in-depth on the data volume.

## Deploy

See the top-level [`README.md`](../README.md).
