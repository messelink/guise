# guise/server

Flask + gunicorn web app for managing [`docker-mailserver`](https://github.com/docker-mailserver/docker-mailserver) aliases. Packages as a Docker image (`guise:latest`) that is deployed as a sidecar service inside the docker-mailserver compose project.

## Architecture

- **Auth**: short username + password is checked by connecting to dovecot over IMAPS (`mailserver:993`). No password store of our own. Failed logins go through dovecot's fail2ban jail.
- **Alias CRUD**: `docker exec mailserver setup alias add/del/list` via the mounted host docker socket. The mailserver container is the source of truth.
- **State guise owns**: only `/data/secret_key` (Flask session signing key, regenerated on first start). Wiping `guise-data/` and restarting is a clean reset — no user data is lost because no user data is stored.
- **Alias naming**: `<GUISE_TAG><8 hex>[-<slug>]@<GUISE_DOMAIN>`. Default tag `g-`. The tag is how guise identifies its own aliases versus legacy ones in `postfix-virtual.cf`.

## Layout

```
server/
├── Dockerfile             python:3.12-slim + docker-ce-cli
├── Makefile               make build / make test
├── requirements.txt       flask, gunicorn
├── app/
│   ├── __init__.py        Flask factory
│   ├── config.py          env-var loading, secret_key bootstrap
│   ├── auth.py            IMAP login, session, denylist
│   ├── aliases.py         slug, tag, docker-exec wrappers, parser
│   ├── routes.py          dashboard + create + delete
│   ├── templates/{base,login,index}.html
│   └── static/style.css
└── tests/
    ├── test_aliases.py
    └── test_auth.py
```

## Build

```
make build           # docker build -t guise:latest .
```

## Test

```
python -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
make test            # python -m pytest tests/ -v
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
| `DATA_DIR` | `/data` | Where `secret_key` lives |
| `SESSION_COOKIE_SECURE` | `true` | Set false only if proxy is plain HTTP |

## Trust boundaries

- **Docker API access is mediated by a `docker-socket-proxy` sidecar** (`tecnativa/docker-socket-proxy`), included in the default install. The proxy bind-mounts `/var/run/docker.sock` and exposes only the `CONTAINERS` + `EXEC` API endpoints over `tcp://guise-socket-proxy:2375` on the project's internal Docker network — never on the host or the internet. The endpoint allowlist is what scopes the surface; the `:ro` flag on the bind mount is file-level defence in depth, not an API restriction. guise itself does not mount the host socket, is not a member of the host `docker` group, and is connected to the Docker daemon only through this restricted interface. An RCE in guise can do exactly what guise needs to do (run `setup alias …` inside the mailserver container) and nothing else: no starting new containers, no host-path mounts, no daemon reconfiguration, no access to other containers' state.
- IMAP connection uses TLS. By default the cert is validated against the system trust store (`GUISE_IMAP_CAFILE` overrides). Hostname verification is off because we reach the mailserver by container name, not by the cert CN. `GUISE_IMAP_INSECURE=1` is the explicit escape hatch for self-signed or testing setups.
- Login validation happens at dovecot. fail2ban-postfix and fail2ban-dovecot jails in the mailserver container catch brute force.
- App-level rate limit (`flask-limiter`) caps `/login` at 20/min and `POST /aliases` at 30/min per client IP. `ProxyFix` is enabled so the real client IP is used (set `RemoteIPHeader X-Forwarded-For` in your reverse proxy and trust only the proxy's IP).

### Further hardening (not default)

Worth considering for production deployments:

- `read_only: true` on the guise container with a tmpfs for `/tmp`. All writes guise actually needs are scoped to the volume-mounted `/data` dir.
- A custom AppArmor or seccomp profile for the guise container.
- Pinning `ghcr.io/messelink/guise` to a digest (`@sha256:…`) instead of `:latest` to make supply-chain compromise of the published image more visible.
- Bind-mount-only the `./guise-data` directory with `:rw,nodev,nosuid` (or similar) for defence-in-depth on the data volume.

## Deploy

See the top-level [`README.md`](../README.md).
