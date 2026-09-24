# PhotoShare — Cloud Server Deployment Guide

Step-by-step instructions for deploying this bundle on a real Ubuntu 22
cloud server. Everything here has been build-and-test validated end-to-end
against a real Docker daemon in a sandbox (see `README.md` and the project
wiki for what was tested); the steps below are what's left to run on your
actual infrastructure.

## Target architecture

- Two containers reachable only from Apache2: `data-proxy` (public gallery
  API) and `admin-proxy` (rescan/preheat/cleanup/root-change API). Both are
  published to `127.0.0.1` only, never to `0.0.0.0`.
- One `backend` container, not published to the host at all — only the two
  proxies can reach it, over the internal Compose network.
- Apache2 on the host serves both static frontends directly from disk and
  reverse-proxies each plane to its own container, terminating TLS itself.
  Neither container ever sees or handles TLS.
- The real photo library is bind-mounted read-only into the backend
  container; the backend's cache/metadata (SQLite) and logs are bind-mounted
  read-write from two more host paths you control (`PHOTOSHARE_CACHE_ROOT`,
  `PHOTOSHARE_LOG_ROOT`), so they survive container restarts and redeploys
  and are easy to inspect or back up directly from the host.

## Prerequisites on the server

1. Ubuntu 22.04 LTS with a public IP and DNS you control.
2. Docker Engine + the Compose plugin:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker "$USER"   # log out/in to pick this up
   docker compose version            # sanity check
   ```
3. Apache2 with the modules this stack needs:
   ```bash
   sudo apt update && sudo apt install -y apache2
   sudo a2enmod proxy proxy_http headers ssl
   ```
4. `certbot` for TLS (or your own certificate process):
   ```bash
   sudo apt install -y certbot python3-certbot-apache
   ```
5. DNS: point your gallery and admin hostnames (A/AAAA records) at the
   server's public IP before running certbot — e.g.
   `gallery.example.com` and `admin.example.com`.

## 1. Copy this bundle to the server

```bash
scp -r deployment/ user@your-server:/opt/photoshare
ssh user@your-server
cd /opt/photoshare
```

## 2. Configure environment variables

```bash
cp .env.example .env
nano .env   # fill in PHOTOSHARE_PHOTOS_ROOT, PHOTOSHARE_CACHE_ROOT, and
            # PHOTOSHARE_LOG_ROOT now; for GALLERY_PROXY_API_KEY and
            # ADMIN_PROXY_API_KEY, set a temporary placeholder like
            # "pending" for each -- Compose validates that every required
            # var in docker-compose.yml is non-empty just to PARSE the file,
            # even for commands that only target the backend, so leaving
            # them blank here would block step 4 below. You'll replace both
            # placeholders with real keys in step 4.
```

At minimum, set `PHOTOSHARE_PHOTOS_ROOT` to the absolute host path of your
real photo library (e.g. `/mnt/photos`), and `PHOTOSHARE_CACHE_ROOT` /
`PHOTOSHARE_LOG_ROOT` to absolute host paths for the backend's persistent
cache and logs (e.g. `/opt/photoshare/cache`, `/opt/photoshare/logs`) --
create those two directories first and make sure uid 1000 can write to
them, since the backend container runs as that uid:

```bash
sudo mkdir -p /opt/photoshare/cache /opt/photoshare/logs
sudo chown -R 1000:1000 /opt/photoshare/cache /opt/photoshare/logs
```

Leave `*_CORS_ORIGINS` empty if Apache2 will serve each frontend and its
proxy on the same origin (the setup these vhost configs assume) — that's
the recommended default.

## 3. Build and start the backend

```bash
docker compose build
docker compose up -d backend
docker compose ps    # confirm backend is "healthy" before proceeding
```

Targeting `backend` explicitly starts only that service (Compose does not
also start services that depend on it), which is exactly what you want
since the proxies aren't ready to start yet — they need the real API keys
from step 4 first.

## 4. Issue real API keys

With the backend container already running, `exec` into it to generate
both keys:

```bash
docker compose exec backend python -m photoshare.cli.manage \
  --config config.prod.yaml create-api-key --label data-proxy
docker compose exec backend python -m photoshare.cli.manage \
  --config config.prod.yaml create-api-key --label admin-proxy
```

Each command prints a raw key **once** — copy both into `.env`, replacing
the `pending` placeholders, as `GALLERY_PROXY_API_KEY` and
`ADMIN_PROXY_API_KEY` respectively, then bring up the full stack (this
starts the two proxies for the first time, now with real keys):

```bash
docker compose up -d   # picks up the new .env values
```

Keys are hashed and stored in the backend's own SQLite metadata store,
which lives under `PHOTOSHARE_CACHE_ROOT` on the host — they survive
container restarts and redeploys, but regenerate them (and update `.env`)
if you ever delete or recreate that directory.

## 5. Deploy the two frontends and vhosts

```bash
sudo mkdir -p /var/www/photoshare-gallery /var/www/photoshare-admin
sudo cp -r frontend/*      /var/www/photoshare-gallery/
sudo cp -r admin-frontend/* /var/www/photoshare-admin/

sudo cp apache/photoshare-gallery.conf /etc/apache2/sites-available/
sudo cp apache/photoshare-admin.conf   /etc/apache2/sites-available/
```

Edit both files under `/etc/apache2/sites-available/` and replace the
placeholder `ServerName` values (`gallery.example.com`,
`admin.example.com`) with your real hostnames, then:

```bash
sudo a2ensite photoshare-gallery photoshare-admin
sudo apache2ctl configtest   # must say "Syntax OK"
sudo systemctl reload apache2
```

## 6. Enable TLS

```bash
sudo certbot --apache -d gallery.example.com -d admin.example.com
```

Certbot rewrites both vhosts in place to add matching `:443` blocks and
(by default) redirect `:80` to HTTPS. Neither container needs any change —
they only ever see plain HTTP from Apache2, which still terminates TLS in
front of them.

## 7. Verify

```bash
curl -s https://gallery.example.com/gallery/health
curl -s https://admin.example.com/admin-proxy-health
curl -s https://admin.example.com/admin/backend-health

# gated admin op should 401 without a key:
curl -i -s -X POST https://admin.example.com/admin/rescan
# and succeed with the real admin key:
curl -s -X POST https://admin.example.com/admin/rescan \
  -H "X-API-Key: <your ADMIN_PROXY_API_KEY>"
```

Then load both hostnames in a browser and confirm the gallery renders your
real photo library and the admin UI's health badge is green.

## Ongoing operations

- **Logs:** `docker compose logs -f backend` (or `data-proxy` /
  `admin-proxy`).
- **Redeploy after a code change:** `docker compose build && docker compose
  up -d` — the backend's `PHOTOSHARE_CACHE_ROOT` directory on the host is
  untouched, so existing API keys keep working.
- **Rescans:** the backend's `rescan.on_startup: true` default (in
  `backend/config.prod.yaml`) means every restart rescans the whole photo
  tree; combined with `server.workers: 4` that's 4 redundant rescans per
  restart. For a large library, consider setting it to `false` and running
  an explicit `POST /admin/rescan` after each deploy instead.
- **Filesystem permissions:** the backend container runs as uid 1000 —
  make sure that uid has read access to whatever real path you set as
  `PHOTOSHARE_PHOTOS_ROOT`.

## What this guide does not cover

Server provisioning/hardening, backup strategy for the
`PHOTOSHARE_CACHE_ROOT` directory, and monitoring/alerting are all
deliberately out of scope here — this covers only getting the PhotoShare
stack itself running behind Apache2 with real data, real keys, and real
TLS. See `todo.md` for the current outstanding checklist.
