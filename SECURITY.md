# Security policy

## Reporting a vulnerability

If you believe you've found a security issue in guise, please report it privately to **g-2eebed68-guise@club77.org** rather than opening a public issue.

Include:

- A description of the issue and its impact
- Steps to reproduce
- The affected version (see the `__version__` constant in `server/app/__init__.py` or the version footer in the deployed UI)
- Any suggested mitigation

I'll acknowledge receipt within a few days and aim to publish a fix within two weeks for high-severity issues. Please give a reasonable timeline before any public disclosure.

## Supported versions

Only the latest tagged release is supported. Older versions receive no security updates.

## Hardening notes

For the full security posture, review the "Trust boundaries" section in `server/README.md`. The default install includes a `docker-socket-proxy` sidecar that mediates guise's access to the Docker daemon, scoped to the `CONTAINERS` + `EXEC` API endpoints only — guise itself does not mount the host socket. An RCE in guise can run `setup alias …` inside the mailserver container and nothing else; it cannot start new containers, mount host paths, reconfigure the Docker daemon, or reach other containers' state.

The IMAP TLS connection validates the server certificate by default. The explicit escape hatch is `GUISE_IMAP_INSECURE=1`; use it only in genuinely trusted networks (e.g. an intra-host Docker bridge) and prefer pinning a CA bundle via `GUISE_IMAP_CAFILE` instead.
