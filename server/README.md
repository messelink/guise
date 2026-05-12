# guise/server

Flask + gunicorn web app for managing
[`docker-mailserver`](https://github.com/docker-mailserver/docker-mailserver)
aliases. Packages as a Docker image (`guise:latest`) that is deployed as a
sidecar service inside the docker-mailserver compose project.

## Architecture

- **Auth**: short username + password is checked by connecting to dovecot over
  IMAPS (`mailserver:993`). No password store of our own. Failed logins go
  through dovecot's fail2ban jail.
- **Alias CRUD**: `docker exec mailserver setup alias add/del/list` via the
  mounted host docker socket. The mailserver container is the source of truth.
- **State guise owns**: only `/data/secret_key` (Flask session signing key,
  regenerated on first start). Wiping `guise-data/` and restarting is a clean
  reset — no user data is lost because no user data is stored.
- **Alias naming**: `<GUISE_TAG><8 hex>[-<slug>]@<GUISE_DOMAIN>`. Default
  tag `g-`. The tag is how guise identifies its own aliases versus legacy
  ones in `postfix-virtual.cf`.

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

Tests mock `subprocess.run` and `imaplib.IMAP4_SSL` — no containers, no
network.

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

- The Docker socket is mounted in. Anyone with shell access to the guise
  container is root-equivalent on the host.
- IMAP connection uses TLS. By default the cert is validated against the
  system trust store (`GUISE_IMAP_CAFILE` overrides). Hostname verification
  is off because we reach the mailserver by container name, not by the cert
  CN. `GUISE_IMAP_INSECURE=1` is the explicit escape hatch for self-signed
  or testing setups.
- Login validation happens at dovecot. fail2ban-postfix and fail2ban-dovecot
  jails in the mailserver container catch brute force.
- App-level rate limit (`flask-limiter`) caps `/login` at 20/min and
  `POST /aliases` at 30/min per client IP. `ProxyFix` is enabled so the
  real client IP is used (set `RemoteIPHeader X-Forwarded-For` in your
  reverse proxy and trust only the proxy's IP).

### Hardening the docker socket (recommended)

The default compose snippet mounts `/var/run/docker.sock` directly, giving
the guise container full Docker API access. Any RCE in guise escalates to
host root.

For defense-in-depth, front the socket with
[`tecnativa/docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy)
and grant only the endpoints guise needs:

```yaml
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy
    container_name: guise-socket-proxy
    restart: always
    environment:
      CONTAINERS: 1     # GET /containers (used by docker exec)
      EXEC: 1           # POST /containers/.../exec
      POST: 1
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    # not exposed to the host; only reachable from the project's docker network

  guise:
    image: guise:latest
    environment:
      DOCKER_HOST: tcp://guise-socket-proxy:2375
      # … other env vars as before
    # remove the docker.sock volume and the group_add: ["988"] entry
    depends_on:
      - guise-socket-proxy
      - mailserver
```

A bug in guise can then only ask the proxy to run `exec` on existing
containers — not start new containers with host-path mounts, modify
networks, or read arbitrary container state.

Complementary hardening worth considering:

- `read_only: true` on the guise container with a tmpfs for `/tmp`. All
  writes guise actually needs are scoped to the volume-mounted `/data` dir.
- A custom AppArmor or seccomp profile for the guise container.
- Pinning the `python:3.12-slim` base image to a digest.

## Deploy

See the top-level `README.md`.
