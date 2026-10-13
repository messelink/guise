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

For the full security posture, review the "Trust boundaries" section in [`server/README.md`](server/README.md). The default install includes a socket-proxy sidecar ([`wollomatic/socket-proxy`](https://github.com/wollomatic/socket-proxy)) that mediates guise's access to the Docker daemon. guise itself does not mount the host socket. The proxy allows only the five API calls needed to `docker exec` into the mailserver container. An RCE in guise cannot create or start containers, mount host paths, or exec into any other container.

**What is still exposed:** the Docker API cannot restrict which *command* an exec runs. An RCE in guise can therefore run arbitrary commands as root inside the mailserver container, which means all stored mail, the account password hashes, and anything else mounted into that container. Removing this residual risk is tracked in [#11](https://github.com/messelink/guise/issues/11).

Releases up to and including 0.3.1 documented a `tecnativa/docker-socket-proxy` config (`CONTAINERS=1`, `EXEC=1`, `POST=1`) that did **not** provide this isolation. That config permits `POST /containers/create`, so an RCE in guise could start a privileged container and gain root on the host. If you installed from those instructions, replace the proxy block with the current one from the [README](README.md#quickstart).

The IMAP TLS connection validates the server certificate by default. The explicit escape hatch is `GUISE_IMAP_INSECURE=1`; use it only in genuinely trusted networks (e.g. an intra-host Docker bridge) and prefer pinning a CA bundle via `GUISE_IMAP_CAFILE` instead.
