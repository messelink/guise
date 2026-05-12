# Changelog

All notable changes to guise are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project does
not yet follow strict semantic versioning — backwards-incompatible changes
before 1.0 will be called out explicitly.

## [0.1.0] — 2026-05-12

Initial release. A small self-hosted Flask web app for managing
per-recipient email aliases on a `docker-mailserver` instance, deployed as a
sidecar inside the mailserver's compose project.

### Features

- Login via IMAP against the mailserver's dovecot — no password database
- Create labeled aliases with random 8-char hex prefix
  (e.g. `g-a3f82c11-netflix@example.com`)
- Per-user dashboard filtered by target email
- Delete aliases with a confirmation prompt; stronger prompt for aliases
  not created by guise
- Operator-configurable namespace tag, mail domain, denylist of usernames,
  IMAP host, and TLS verification mode

### Security

- CSRF protection on all unsafe-method routes (session-bound tokens
  validated in a before-request hook)
- Session cookies HttpOnly + SameSite=Lax + Secure (configurable); 12 h
  lifetime
- Open-redirect protection on the login `?next=` parameter
- App-layer rate limiting (flask-limiter): 20/min on `/login`, 30/min on
  `POST /aliases`, keyed on real client IP via `ProxyFix`
- IMAP TLS certificate validation enabled by default (`CERT_REQUIRED` against
  the system trust store or `GUISE_IMAP_CAFILE`); `GUISE_IMAP_INSECURE=1`
  escape hatch for genuinely trusted intra-bridge deployments
- Audit log entries for `LOGIN`, `LOGOUT`, `LOGIN_FAILED`, `LOGIN_DENIED`,
  `ALIAS_CREATED`, `ALIAS_DELETED`, `ALIAS_*_FAILED`, `ALIAS_DELETE_FORBIDDEN`
- Atomic secret-key creation (`O_EXCL` + mode 0o600)
- Strict shape validation on alias and target before subprocess invocation
- Generic flash messages on subprocess failures; detail logged server-side
  rather than leaked through the UI

### Documented (not auto-applied) hardening

- `docker-socket-proxy` in front of the host docker.sock, scoped to
  `CONTAINERS` + `EXEC` only
- Read-only container filesystem with a tmpfs for `/tmp`
- Custom AppArmor / seccomp profile
- Base image pinning by digest
