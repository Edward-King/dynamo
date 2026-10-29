# PhotoShare — Cloud Server Deployment Guide

Step-by-step instructions for deploying this repository on a real Ubuntu 22
cloud server. Everything here has been build-and-test validated end-to-end
against a real Docker daemon in a sandbox (see `OVERVIEW.md` for what was
tested); the steps below are what's left to run on your actual
infrastructure. Already running an older install? Skip to
[Updating an existing deployment](#updating-an-existing-deployment).

## Target architecture

- Two proxy containers that Apache2 forwards to: `data-proxy` (public
  gallery API, port 8100) and `admin-proxy` (rescan/preheat/cleanup/
  root-change API, port 8200). As shipped, `docker-compose.yml` publishes
  both ports on **all** host interfaces so you can reach them directly while
  debugging. Lock them down to `127.0.0.1` once the stack works; see
  [Lock down the proxy ports](#lock-down-the-proxy-ports).
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

## 1. Get the code onto the server

```bash
ssh user@your-server
sudo git clone https://github.com/Edward-King/dynamo.git /opt/photoshare
sudo chown -R "$USER": /opt/photoshare
cd /opt/photoshare
```

The repository root is the Compose project directory: `docker-compose.yml`,
`.env.example`, `apache/`, `frontend/` and the service folders all sit
directly under `/opt/photoshare`. Installing from a git checkout (rather
than a copied folder) makes later updates a `git pull`. Your `.env` is
gitignored, so pulls never touch it.

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
# Gallery: copy only the site files (not TESTING.md or the stamping tool),
# then tag the CSS/JS links so browsers pick up new versions after deploys.
sudo cp frontend/{index.html,about.html,script.js,style.css,common.js} /var/www/photoshare-gallery/
# Per-server settings (share-link address): copied only the first time,
# so your edits survive updates. Never overwrite it.
[ -e /var/www/photoshare-gallery/site-config.js ] || sudo cp frontend/site-config.js /var/www/photoshare-gallery/
sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery
sudo cp -r admin-frontend/* /var/www/photoshare-admin/

sudo cp apache/photoshare-gallery.conf /etc/apache2/sites-available/
sudo cp apache/photoshare-admin.conf   /etc/apache2/sites-available/
```

Edit both files under `/etc/apache2/sites-available/` and replace the
placeholder `ServerName` values (`gallery.example.com` in the gallery
vhost, `admin.photoshare.example.com` in the admin vhost) with your real
hostnames, then:

```bash
sudo a2ensite photoshare-gallery photoshare-admin
sudo apache2ctl configtest   # must say "Syntax OK"
sudo systemctl reload apache2
```

## 6. Enable TLS

```bash
sudo certbot --apache -d gallery.example.com -d admin.example.com
```

Certbot copies each vhost into a new HTTPS file
(`photoshare-gallery-le-ssl.conf`, `photoshare-admin-le-ssl.conf`) with a
matching `:443` block, and (by default) adds a redirect from `:80` to HTTPS
to the original file. Neither container needs any change — they only ever
see plain HTTP from Apache2, which still terminates TLS in front of them.

From now on, real traffic is served by the `-le-ssl.conf` copies. Any later
change to a vhost in this repo (for example the gallery caching rules) must
be applied to **both** the original file and its `-le-ssl.conf` copy; see
[Updating an existing deployment](#updating-an-existing-deployment).

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

Check the lightbox download option (pick any real image ID from the
gallery, e.g. from the browser's Network tab):

```bash
ID=<image-id>
curl -sI "https://gallery.example.com/gallery/image/$ID/full?download=1" \
  | grep -i -E '^(HTTP|content-disposition|vary)'
#   expect: 200, Content-Disposition: attachment; filename="...", Vary: Origin
curl -sI "https://gallery.example.com/gallery/image/$ID/full" \
  | grep -i -E '^(content-disposition|vary)'
#   expect: Vary: Origin only (normal viewing has no Content-Disposition)
```

Check the frontend caching rules:

```bash
curl -sI https://gallery.example.com/ | grep -i cache-control
#   expect: Cache-Control: no-cache
TAGGED=$(grep -o 'script.js?v=[0-9a-f]*' /var/www/photoshare-gallery/index.html); echo "$TAGGED"
#   expect: script.js?v= followed by 12 hex characters
curl -sI "https://gallery.example.com/$TAGGED" | grep -i cache-control
#   expect: Cache-Control: public, max-age=31536000, immutable
```

If the first `curl` shows no `Cache-Control` header, the caching rules are
missing from the HTTPS vhost (`photoshare-gallery-le-ssl.conf`).

Check the share-link preview page (it lives in `data-proxy`, so it needs
no vhost change):

```bash
ROOT_ID=$(curl -s https://gallery.example.com/gallery/portfolios/root | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -s "https://gallery.example.com/gallery/share?p=$ROOT_ID" | grep -o '<meta property="og:[a-z:]*" content="[^"]*"'
#   expect: og:title, og:url and (if the library has photos) og:image lines,
#           with URLs starting https://gallery.example.com/
```

A `{"detail":"Not Found"}` reply means `data-proxy` wasn't rebuilt. Share
previews load faster once the 800x600 preview thumbnails exist: in the
admin UI, run **Preheat thumbnails** with width `800`, height `600` and
the library root (they're also made on first use).

Then load both hostnames in a browser and confirm the gallery renders your
real photo library and the admin UI's health badge is green. Open a photo
and use the lightbox's Download button (on a phone, Save should open the
share sheet), then **Copy link** and paste the link into a new tab.
`frontend/TESTING.md` has the full manual checklist.

## Updating an existing deployment

Use this after new changes are merged to `main`.

### Once: switch an older install to a git checkout

Installs made with the older `scp -r deployment/` instructions are plain
copies with no git history. Turn the folder into a checkout in place,
after backing it up. `.env` and other gitignored or server-only files are
left alone, but any files you edited on the server that also exist in the
repo are replaced with the repo version, so check the backup if you
customised any:

```bash
cd /opt/photoshare
BACKUP=~/photoshare-backup-$(date +%Y%m%d-%H%M).tgz
sudo tar czf "$BACKUP" -C /opt photoshare
sudo chmod 600 "$BACKUP"    # it contains .env and the API-key store
sudo chown -R "$USER": /opt/photoshare
# The line above also re-owns the cache/logs folders if they live here;
# hand them back to the container's uid 1000:
for d in cache logs; do [ -d "$d" ] && sudo chown -R 1000:1000 "$d"; done
git init -q
git remote add origin https://github.com/Edward-King/dynamo.git
git fetch -q origin main
git checkout -f -B main origin/main
git status --short    # only server-only files (if any) should be listed
```

If your `.env` was created before `PHOTOSHARE_CACHE_ROOT` /
`PHOTOSHARE_LOG_ROOT` existed, compare it with `.env.example` and add any
missing variables. Very early installs kept the cache in Docker named
volumes instead: if `docker volume ls` lists PhotoShare cache or log
volumes, copy their contents into the new host folders before running
`docker compose up -d`, or the backend starts with an empty key store and
you'll need to issue new API keys (step 4).

### Every update

1. **Pull and rebuild the containers.** Only services whose code changed
   are rebuilt and restarted; the cache directory and API keys are
   untouched.
   ```bash
   cd /opt/photoshare
   OLD=$(git rev-parse HEAD)
   git pull --ff-only
   git log --oneline "$OLD"..HEAD        # what changed
   docker compose build
   docker compose up -d
   docker compose ps                     # wait for all three to be healthy
   ```
2. **Redeploy both frontends**, then re-tag the gallery's CSS/JS. Always
   run `stamp_assets.py` after copying: the vhost lets browsers keep tagged
   CSS/JS for a year, so the tags must change whenever those files do.
   Pages (HTML) are always re-checked, so visitors never need a hard
   refresh.
   ```bash
   sudo cp frontend/{index.html,about.html,script.js,style.css,common.js} /var/www/photoshare-gallery/
   [ -e /var/www/photoshare-gallery/site-config.js ] || sudo cp frontend/site-config.js /var/www/photoshare-gallery/
   sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery
   sudo cp -r admin-frontend/* /var/www/photoshare-admin/
   ```
   Plain `cp` (not `rsync --delete`) keeps server-only files in the web root,
   such as your own About-page photo. `site-config.js` is copied only if
   it's missing, so your settings in it are kept. If you skip stamping, nothing breaks;
   untagged files are just re-checked on every visit.
3. **Apply any vhost changes by hand.** Your installed vhosts have your
   real `ServerName` and certbot's additions, so don't overwrite them with
   the repo copies. See what changed, then make the same edit in both the
   original file and its `-le-ssl.conf` copy:
   ```bash
   git diff "$OLD"..HEAD -- apache/
   sudo nano /etc/apache2/sites-available/photoshare-gallery.conf
   sudo nano /etc/apache2/sites-available/photoshare-gallery-le-ssl.conf
   sudo apache2ctl configtest   # must say "Syntax OK"
   sudo systemctl reload apache2
   ```
4. **Verify** with the checks in step 7.

### Upgrading to the lightbox download and caching changes

If your server predates the lightbox download option
([PR #1](https://github.com/Edward-King/dynamo/pull/1)) and the frontend
caching rules ([PR #2](https://github.com/Edward-King/dynamo/pull/2)), the
steps above cover it:

- Step 1 rebuilds `data-proxy` (new `?download=1` option and `Vary: Origin`
  header). The backend, admin proxy and `.env` don't change.
- Step 2 deploys the new gallery files and tags them.
- Step 3: add the caching block from `apache/photoshare-gallery.conf` (the
  part starting at `# --- Browser caching` and ending with the
  `RequestHeader edit "If-None-Match"` line) just before `</Directory>`
  inside `<Directory /var/www/photoshare-gallery>`, in both gallery vhost
  files. If the `-le-ssl.conf` file has no `<Directory>` section, copy the
  whole `<Directory /var/www/photoshare-gallery>` section into its `:443`
  `VirtualHost`. Confirm both files have it:
  ```bash
  grep -c "Header set Cache-Control" /etc/apache2/sites-available/photoshare-gallery*.conf
  #   expect: 3 for each file
  ```
  `mod_headers` is already enabled by the prerequisites.

### Upgrading to the per-portfolio background

[PR #7](https://github.com/Edward-King/dynamo/pull/7) makes the gallery's
page background follow the open portfolio. It changes only the gallery's
`common.js`, `script.js` and `style.css`:

- Step 1: `git pull --ff-only` fetches the new files. No containers change,
  so `docker compose build` has nothing to rebuild and `up -d` restarts
  nothing. Running them anyway is harmless.
- Step 2 is the one that matters: copy the gallery files and re-run
  `stamp_assets.py`. Without the new tags, browsers that already cached
  the old CSS/JS keep using it for up to a year.
- Step 3: nothing to do. `git diff "$OLD"..HEAD -- apache/` prints nothing.
- Confirm the new files are the ones being served:
  ```bash
  grep -c applyPortfolioBackground /var/www/photoshare-gallery/common.js /var/www/photoshare-gallery/script.js
  #   expect: 1 or more for each file (0 means the old files are still deployed)
  grep -o 'common.js?v=[0-9a-f]*' /var/www/photoshare-gallery/index.html /var/www/photoshare-gallery/about.html
  #   expect: the same tag in both pages, different from before the update
  ```
  Then open the gallery and click into a portfolio: the dimmed background
  changes to that portfolio's icon image. The Page background section of
  `frontend/TESTING.md` has the full checklist.

### Upgrading to share links

[PR #9](https://github.com/Edward-King/dynamo/pull/9) adds **Copy link** in
the lightbox and **Share album** above the folders, keeps the address bar
in step with the open album and photo, and adds a preview page in
`data-proxy` (`/gallery/share`) so chat apps show the album or photo name
and a picture:

- Step 1 matters this time: `docker compose build` rebuilds `data-proxy`
  with the new `/gallery/share` route and `up -d` restarts it. Without it,
  every shared link opens a `{"detail":"Not Found"}` page. The backend and
  admin proxy don't change.
- Step 2 copies the new gallery files, adds the new `site-config.js` (first
  time only) and re-tags everything.
- Step 3: nothing to do. `git diff "$OLD"..HEAD -- apache/` prints nothing;
  `/gallery/` is already passed to `data-proxy`.
- Optional: `GALLERY_PROXY_SITE_NAME` in `.env` sets the site name shown in
  previews (default `My Dynamic Gallery`); run `docker compose up -d`
  after changing it. `site-config.js` can stay as it is: on a public HTTPS
  host, links already use the address in the browser. Set its
  `shareBaseUrl` only if visitors use a different public address than the
  one you share from, and re-run `stamp_assets.py` after editing it.
- Optional: preheat the 800x600 preview thumbnails (see step 7).
- Confirm with the share-page check in step 7, then:
  ```bash
  grep -c shareCurrentPhoto /var/www/photoshare-gallery/script.js
  #   expect: 1 or more (0 means the old script is still deployed)
  ```
  Open a photo, click **Copy link**, paste it into a new tab: the same
  photo opens. The Share links and Link previews sections of
  `frontend/TESTING.md` have the full checklist.

## Ongoing operations

- **Logs:** `docker compose logs -f backend` (or `data-proxy` /
  `admin-proxy`).
- **Updates:** see
  [Updating an existing deployment](#updating-an-existing-deployment). The
  backend's `PHOTOSHARE_CACHE_ROOT` directory on the host is untouched by
  rebuilds, so existing API keys keep working.
- **Rescans:** the backend's `rescan.on_startup: true` default (in
  `backend/config.prod.yaml`) means every restart rescans the whole photo
  tree; combined with `server.workers: 4` that's 4 redundant rescans per
  restart. For a large library, consider setting it to `false` and running
  an explicit `POST /admin/rescan` after each deploy instead.
- **Filesystem permissions:** the backend container runs as uid 1000 —
  make sure that uid has read access to whatever real path you set as
  `PHOTOSHARE_PHOTOS_ROOT`.

## Lock down the proxy ports

Once everything works through Apache2, stop publishing the proxy ports on
the public interface. In `docker-compose.yml`, change:

```yaml
      - "8100:8100"    # data-proxy  ->  - "127.0.0.1:8100:8100"
      - "8200:8200"    # admin-proxy ->  - "127.0.0.1:8200:8200"
```

then run `docker compose up -d`. Apache2 still reaches both on
`127.0.0.1`, so nothing else changes. Don't rely on `ufw` for this: ports
published by Docker bypass `ufw` rules. Since this edits a tracked file,
`git pull` will refuse to update it if the repo version also changes; keep
your edit with `git stash && git pull --ff-only && git stash pop`.

## What this guide does not cover

Server provisioning/hardening, backup strategy for the
`PHOTOSHARE_CACHE_ROOT` directory, and monitoring/alerting are all
deliberately out of scope here — this covers only getting the PhotoShare
stack itself running behind Apache2 with real data, real keys, and real
TLS.
