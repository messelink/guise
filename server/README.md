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
- **State Guise owns**: only `/data/secret_key` (Flask session signing key,
  regenerated on first start). Wiping `guise-data/` and restarting is a clean
  reset — no user data is lost because no user data is stored.
- **Alias naming**: `<GUISE_TAG><8 hex>[-<slug>]@<GUISE_DOMAIN>`. Default
  tag `g-`. The tag is how Guise identifies its own aliases versus legacy
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

- The Docker socket is mounted in. Anyone with shell access to the Guise
  container is root-equivalent on the host.
- IMAP connection uses TLS but skips hostname verification (the certificate is
  typically issued for the public mail hostname while we reach the mailserver
  by its Docker network name). Encryption is still in place; trust comes from
  the Docker bridge boundary.
- Login validation happens at dovecot. fail2ban-postfix and fail2ban-dovecot
  jails in the mailserver container catch brute force.

## Deploy

See the top-level `README.md`.
