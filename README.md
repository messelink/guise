# guise

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![tests](https://github.com/messelink/guise/actions/workflows/tests.yml/badge.svg)](https://github.com/messelink/guise/actions/workflows/tests.yml)
[![CodeQL](https://github.com/messelink/guise/actions/workflows/codeql.yml/badge.svg)](https://github.com/messelink/guise/actions/workflows/codeql.yml)
[![Quality Gate](https://sonarcloud.io/api/project_badges/measure?project=messelink_guise&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=messelink_guise)

> Copyright (C) 2026 Pim Messelink &lt;g-2eebed68-guise@club77.org&gt;
> Licensed under the GNU Affero General Public License v3.0 or later. See `LICENSE`.

Self-hosted web app for managing per-recipient email aliases on a [`docker-mailserver`](https://github.com/docker-mailserver/docker-mailserver) instance, without SSH. Generates random alias addresses labeled with the service they're for, e.g. `g-a3f82c11-netflix@example.com`, and routes them to your real mailbox.

Auth piggybacks on the mailserver itself (IMAP), so there's no separate user database. Aliases live in `postfix-virtual.cf`, and the embedded label travels in the address itself — guise owns no application data of its own beyond a regenerable session key.

## Screenshots

### Login

<img src="screenshots/login.jpg" alt="Login screen" width="50%">

### Dashboard

<img src="screenshots/dashboard.jpg" alt="Dashboard" width="50%">

## Repo layout

```
guise/
├── README.md              this file
├── LICENSE                AGPL-3.0
├── CHANGELOG.md
├── CONTRIBUTING.md
├── SECURITY.md
├── screenshots/           login + dashboard
├── .github/workflows/     GitHub Actions CI (pytest on push)
└── server/                Python/Flask source for the guise Docker image
```

`server/` builds the image; the running deployment is a sidecar service inside your `docker-mailserver` compose project. Prebuilt images are published to GitHub Container Registry at `ghcr.io/messelink/guise` for `linux/amd64` and `linux/arm64`.

## Quickstart

Add the two services below to your `docker-mailserver` `compose.yaml`, alongside the existing `mailserver` service. The `guise-socket-proxy` sidecar restricts guise's Docker API access to only the `exec` calls it needs to write aliases — an RCE in guise can no longer touch the host Docker daemon directly. See `server/README.md` for the trust-boundary rationale.

```yaml
  guise-socket-proxy:
    image: tecnativa/docker-socket-proxy:latest
    container_name: guise-socket-proxy
    restart: always
    environment:
      CONTAINERS: 1   # allow GET on /containers/*
      EXEC: 1         # allow POST on /containers/{id}/exec and /exec/{id}/start
      POST: 1         # allow POST requests in general
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    # not exposed on the host; only reachable from the project's docker network

  guise:
    image: ghcr.io/messelink/guise:latest
    container_name: guise
    restart: always
    ports: ["127.0.0.1:9100:8000"]
    volumes:
      - ./guise-data:/data
    environment:
      GUISE_DOMAIN: example.com               # your mail domain
      GUISE_TAG: g-
      GUISE_DENIED_USERS: noreply,admin,bot   # service accounts to block
      GUISE_MAILSERVER_CONTAINER: mailserver
      GUISE_IMAP_HOST: mailserver
      GUISE_IMAP_PORT: "993"
      DATA_DIR: /data
      SESSION_COOKIE_SECURE: "true"
      DOCKER_HOST: tcp://guise-socket-proxy:2375
    depends_on:
      - mailserver
      - guise-socket-proxy
```

Then from your docker-mailserver compose project directory:

```
mkdir -p guise-data
docker compose pull guise guise-socket-proxy
docker compose up -d guise guise-socket-proxy
```

To pin a specific version instead of tracking `:latest`, use `ghcr.io/messelink/guise:0.3.0` (or any tag from the [Releases page](https://github.com/messelink/guise/releases)). Upgrades are then a `docker compose pull guise && docker compose up -d --force-recreate guise`.

### Build from source instead

If you'd rather not pull a prebuilt image:

```
cd guise/server
make build         # produces local guise:latest
```

…then use `image: guise:latest` in the compose block above.

guise listens on `127.0.0.1:9100`. Front it with your existing reverse proxy.

## Apache reverse-proxy vhost (example)

```apache
<VirtualHost *:80 [::]:80>
    ServerName guise.example.com
    RewriteEngine On
    RewriteCond %{HTTPS} off
    RewriteRule ^ https://%{SERVER_NAME}%{REQUEST_URI} [END,NE,R=permanent]
</VirtualHost>

<VirtualHost *:443 [::]:443>
    ServerName guise.example.com
    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    ProxyPass / http://127.0.0.1:9100/
    ProxyPassReverse / http://127.0.0.1:9100/
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/guise.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/guise.example.com/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf
</VirtualHost>
```

Point DNS at the host (A/AAAA or CNAME, depending on your zone), then issue the cert with your usual ACME client (e.g. `certbot --apache -d guise.example.com`).

## SimpleLogin-compatible API

guise exposes the subset of the [SimpleLogin REST API](https://github.com/simple-login/app/blob/master/docs/api.md) that password-manager "forwarded email alias" generators rely on. Any client that supports pointing at a *self-hosted* SimpleLogin server should be able to create guise aliases without modification.

Point your client at `https://guise.example.com`; the API key is your mailbox short-username and IMAP password joined by `:` — same auth path as the web UI, no separate token to manage. For example, if your mailbox is `alice@example.com` and your IMAP password is `s3cret-imap-password`, the API key is `alice:s3cret-imap-password`.

**Confirmed working**:

- **Bitwarden** — *Username Generator → Forwarded email alias → SimpleLogin (self-hosted server)*. Browser extension, mobile, desktop. In-page autofill on a sign-up form passes the page URL, which guise uses to auto-label the alias (e.g. `g-a3f82c11-netflix@example.com` for a Netflix signup); the standalone popup generator produces an unlabeled `g-<8hex>@example.com`.

**Theoretically compatible (untested — feedback welcome)**:

- Any other client that exposes a configurable *self-hosted* SimpleLogin server URL and only needs `POST /api/alias/random/new`. This likely includes SimpleLogin's own browser extension in self-hosted mode.

**Currently won't work**:

- Clients pinned to a hosted SimpleLogin instance: 1Password (Watchtower), Proton Pass (Proton-managed SimpleLogin). No user-facing setting points them at a custom server.

Auto-labeling is opt-out per instance via `GUISE_API_AUTOLABEL=0`. Full request/response spec, error codes, and the SimpleLogin subset implemented are in [`docs/api.md`](docs/api.md).

## User flow

1. Browse to `https://guise.example.com/login`
2. Log in with your short mailserver username + password
3. Dashboard shows two sections:
   - **Managed by guise** — addresses starting with `g-`, with their labels
   - **Other aliases routing to you** — anything else in `postfix-virtual.cf` pointing to your address (pre-existing aliases). Deleting one of these prompts an extra confirmation.
4. Type a label, click *Create alias* → fresh `g-<8 hex>-<label>` address appears, ready to copy.
5. Click *delete* on any alias to remove it.

## Rollback

```
docker compose stop guise && docker compose rm -f guise
# remove the guise service block from compose.yaml
rm -rf guise-data
docker image rm guise:latest
```

Mailserver is untouched.

## Development

See `server/README.md` for build/test details.
