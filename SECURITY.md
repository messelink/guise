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

For production deployment, review the "Hardening the docker socket" subsection in `server/README.md`. The default compose snippet mounts the host docker socket directly into guise — an unpatched RCE in guise would escalate to host root. The recommended mitigation (a `docker-socket-proxy` in front of the raw socket, scoped to `CONTAINERS` + `EXEC` only) is documented there.

The IMAP TLS connection validates the server certificate by default. The explicit escape hatch is `GUISE_IMAP_INSECURE=1`; use it only in genuinely trusted networks (e.g. an intra-host Docker bridge) and prefer pinning a CA bundle via `GUISE_IMAP_CAFILE` instead.
