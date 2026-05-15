# Installation guide

The [Quickstart in the README](README.md#quickstart) covers the recommended path: pull `ghcr.io/messelink/guise:latest`, drop the compose snippet into your `docker-mailserver` compose project, and front it with a reverse proxy. This document covers the reverse-proxy alternatives.

## Reverse proxy alternatives

guise listens on `127.0.0.1:9100` (or whatever you bind it to in the compose `ports` line). Anything that can terminate TLS and forward to it works — pick the one that fits your existing infrastructure.

### Caddy

Minimal Caddyfile:

```Caddyfile
guise.example.com {
    reverse_proxy 127.0.0.1:9100
}
```

Caddy auto-provisions and renews a Let's Encrypt certificate and redirects HTTP → HTTPS. No extra config required.

### Caddy in Docker, via caddy-docker-proxy

If your Caddy already runs in Docker and you use [caddy-docker-proxy](https://github.com/lucaslorentz/caddy-docker-proxy), add labels to the guise service block:

```yaml
  guise:
    # ... rest of the service block ...
    labels:
      caddy: guise.example.com
      caddy.reverse_proxy: "{{upstreams 8000}}"
    networks:
      - caddy_net  # the network caddy-docker-proxy watches
```

No standalone Caddyfile; the proxy auto-discovers the route from the labels and the container's port.

### Apache

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

### nginx

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name guise.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name guise.example.com;

    ssl_certificate     /etc/letsencrypt/live/guise.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/guise.example.com/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;

    location / {
        proxy_pass http://127.0.0.1:9100/;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Pair with `certbot --nginx -d guise.example.com` or your existing ACME client.

### Traefik (compose labels)

If your Traefik already watches the docker-mailserver compose network:

```yaml
  guise:
    # ... rest of the service block ...
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.guise.rule=Host(`guise.example.com`)"
      - "traefik.http.routers.guise.entrypoints=websecure"
      - "traefik.http.routers.guise.tls.certresolver=letsencrypt"
      - "traefik.http.services.guise.loadbalancer.server.port=8000"
```

`port=8000` is the internal container port (gunicorn's listen address), not the `127.0.0.1:9100` host binding.

## Hostname trust and `X-Forwarded-*`

guise enables `ProxyFix(x_for=1, x_proto=1, x_host=1)` so `request.remote_addr` reflects the real client IP from `X-Forwarded-For` and `request.is_secure` reflects HTTPS termination at the proxy. This is correct **only** if guise is reached *exclusively* through your reverse proxy. If guise is also reachable directly (e.g. you bind it to `0.0.0.0:9100` for testing), a malicious client can spoof those headers; bind to `127.0.0.1` and front everything through the proxy.
