# guise

[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![tests](https://github.com/messelink/guise/actions/workflows/tests.yml/badge.svg)](https://github.com/messelink/guise/actions/workflows/tests.yml)

> Copyright (C) 2026 Pim Messelink &lt;g-2eebed68-guise@club77.org&gt;
> Licensed under the GNU Affero General Public License v3.0 or later. See `LICENSE`.

Self-hosted web app for managing per-recipient email aliases on a
[`docker-mailserver`](https://github.com/docker-mailserver/docker-mailserver)
instance, without SSH. Generates random alias addresses labeled with the
service they're for, e.g. `g-a3f82c11-netflix@example.com`, and routes them
to your real mailbox.

Auth piggybacks on the mailserver itself (IMAP), so there's no separate user
database. Aliases live in `postfix-virtual.cf`, and the embedded label travels
in the address itself — guise owns no application data of its own beyond a
regenerable session key.

## Screenshots

### Login

![Login screen](screenshots/login.jpg)

### Dashboard

![Dashboard](screenshots/dashboard.jpg)

## Repo layout

```
guise/
├── README.md         this file
└── server/           Python/Flask source for the guise Docker image
```

`server/` builds the image (`guise:latest`); the running deployment is a
sidecar service inside your `docker-mailserver` compose project.

## Quickstart

Build the image on the host running `docker-mailserver`:

```
cd guise/server
make build
```

Add guise as a service in your `docker-mailserver` `compose.yaml`, alongside
the existing `mailserver` service (see `server/README.md` for env vars). The
minimum compose block:

```yaml
  guise:
    image: guise:latest
    container_name: guise
    restart: always
    ports: ["127.0.0.1:9100:8000"]
    volumes:
      - ./guise-data:/data
      - /var/run/docker.sock:/var/run/docker.sock
    environment:
      GUISE_DOMAIN: example.com               # your mail domain
      GUISE_TAG: g-
      GUISE_DENIED_USERS: noreply,admin,bot   # service accounts to block
      GUISE_MAILSERVER_CONTAINER: mailserver
      GUISE_IMAP_HOST: mailserver
      GUISE_IMAP_PORT: "993"
      DATA_DIR: /data
      SESSION_COOKIE_SECURE: "true"
    group_add: ["<host docker gid>"]          # e.g. "988"; see `getent group docker`
    depends_on: [mailserver]
```

Then:

```
mkdir -p guise-data
docker compose config -q && docker compose up -d guise
```

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
    ProxyPass / http://127.0.0.1:9100/
    ProxyPassReverse / http://127.0.0.1:9100/
    SSLEngine on
    SSLCertificateFile /etc/letsencrypt/live/guise.example.com/fullchain.pem
    SSLCertificateKeyFile /etc/letsencrypt/live/guise.example.com/privkey.pem
    Include /etc/letsencrypt/options-ssl-apache.conf
</VirtualHost>
```

Point DNS at the host (A/AAAA or CNAME, depending on your zone), then issue
the cert with your usual ACME client (e.g. `certbot --apache -d guise.example.com`).

## User flow

1. Browse to `https://guise.example.com/login`
2. Log in with your short mailserver username + password
3. Dashboard shows two sections:
   - **Managed by guise** — addresses starting with `g-`, with their labels
   - **Other aliases routing to you** — anything else in `postfix-virtual.cf`
     pointing to your address (pre-existing aliases). Deleting one of these
     prompts an extra confirmation.
4. Type a label, click *Create alias* → fresh `g-<8 hex>-<label>` address
   appears, ready to copy.
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
