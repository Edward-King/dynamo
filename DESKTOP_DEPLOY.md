# PhotoShare — Desktop Linux Deployment

This adapts `CLOUD_DEPLOY.md` for running the same three-container stack
(backend, data-proxy, admin-proxy) directly on a Linux desktop/workstation
instead of a headless cloud server. The architecture is identical — same
`docker-compose.yml`, same Apache2 vhosts for the same-origin reverse-proxy
trick the frontends rely on — only the environment changes:

- No public IP, DNS, or TLS. Everything is reachable at `localhost` (or
  your LAN if you choose to open it up) over plain HTTP.
- You're almost certainly your desktop's first user account, which on
  every mainstream distro means your login UID is `1000` — the exact UID
  the backend container always runs as. That removes most of the
  `chown`/`sudo` friction from the cloud guide's permission steps.
- No production secrets-rotation concerns; it's still worth keeping `.env`
  non-world-readable, but you're not provisioning a shared server.

## 1. Prerequisites

**Docker Engine + the Compose plugin.** Package manager varies by distro
family:

```bash
# Debian/Ubuntu-family (apt)
sudo apt update
sudo apt install -y docker.io docker-compose-plugin

# Fedora/RHEL-family (dnf)
sudo dnf install -y docker docker-compose-plugin

# Arch-family (pacman)
sudo pacman -S docker docker-compose
```

Add yourself to the `docker` group so you don't need `sudo` for every
`docker compose` command, then start a new shell session (or log out/in)
for the group change to apply:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

Enable Docker on boot, since desktops get rebooted far more often than
servers:

```bash
sudo systemctl enable --now docker
```

Confirm your UID matches the container's fixed UID (should read `1000` on
a normal single-user desktop install):

```bash
id -u
```

If it's not `1000`, you'll need the same `chown -R 1000:1000 <path>` step
from the cloud guide for the cache/log directories below.

**Apache2**, for the same-origin reverse proxy the frontends expect
(`common.js`/`admin.js` use a relative `PROXY_BASE_URL` — see
`README.md` — which only works when the frontend HTML and its proxy are
served from the same origin):

```bash
sudo apt install -y apache2       # or dnf/pacman equivalent
sudo a2enmod proxy proxy_http headers
sudo systemctl enable --now apache2
```

## 2. Get the code onto the machine

Clone the repository wherever you keep local projects, e.g.:

```bash
git clone https://github.com/Edward-King/dynamo.git ~/photoshare
cd ~/photoshare
```

The repository root is the Compose project directory (`docker-compose.yml`,
`.env.example`, `apache/`, `frontend/`, ...). Your `.env` is gitignored, so
later `git pull`s never touch it.

## 3. Create the persistent host directories

Keep these outside the project folder itself so they stay separate from
the code you pull and rebuild:

```bash
mkdir -p ~/photoshare-data/cache ~/photoshare-data/logs
```

If step 1 confirmed your UID is `1000`, nothing further is needed — you
already own these directories. Otherwise:

```bash
sudo chown -R 1000:1000 ~/photoshare-data/cache ~/photoshare-data/logs
```

## 4. Configure environment variables

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set:

```
PHOTOSHARE_PHOTOS_ROOT=/home/<you>/Pictures      # your real photo library
PHOTOSHARE_CACHE_ROOT=/home/<you>/photoshare-data/cache
PHOTOSHARE_LOG_ROOT=/home/<you>/photoshare-data/logs
GALLERY_PROXY_API_KEY=pending
ADMIN_PROXY_API_KEY=pending
```

(`pending` is a temporary placeholder — Compose validates every required
`.env` variable just to parse the file, even for backend-only commands, so
leaving these blank blocks step 5 below. You'll swap in real keys after
generating them.) Leave both `*_CORS_ORIGINS` empty — Apache2 serving the
frontend and proxying to the same origin means no CORS is needed.

## 5. Start the backend and issue real API keys

```bash
docker compose up -d backend
docker compose logs -f backend      # wait for it to report healthy, then Ctrl-C

docker compose exec backend python -m photoshare.cli.manage \
  --config config.prod.yaml create-api-key --label data-proxy

docker compose exec backend python -m photoshare.cli.manage \
  --config config.prod.yaml create-api-key --label admin-proxy
```

Each command prints a raw key exactly once — copy both into `.env`,
replacing the `pending` placeholders (`GALLERY_PROXY_API_KEY` for the
`data-proxy` key, `ADMIN_PROXY_API_KEY` for the `admin-proxy` key).

## 6. Bring up the full stack

```bash
docker compose up -d
docker compose ps       # all three services should show healthy
```

The proxies publish on port `8100` (data-proxy) and `8200` (admin-proxy).
As shipped, `docker-compose.yml` publishes them on **all** interfaces, so
other machines on your network can reach them directly (they still need
the API keys). To keep them on this machine only, change the two `ports`
entries to `"127.0.0.1:8100:8100"` and `"127.0.0.1:8200:8200"` and run
`docker compose up -d` again. Apache2 reaches them on `127.0.0.1` either
way.

## 7. Configure Apache2 vhosts for local access

Give yourself two local hostnames so Apache2's name-based virtual hosting
can tell the two vhosts apart on port 80, the same way it would with real
domains in production. Add to `/etc/hosts`:

```
127.0.0.1   gallery.local admin.local
```

Copy both frontends into Apache2's web root (the same paths the vhosts
already use), then tag the gallery's CSS/JS links so browsers pick up new
versions after each update:

```bash
sudo mkdir -p /var/www/photoshare-gallery /var/www/photoshare-admin
sudo cp frontend/{index.html,about.html,script.js,style.css,common.js} /var/www/photoshare-gallery/
sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery
sudo cp -r admin-frontend/* /var/www/photoshare-admin/
```

Serve copies rather than pointing `DocumentRoot` at your checkout: the
gallery vhost's permissions and browser-caching rules are attached to
`<Directory /var/www/photoshare-gallery>`, `frontend/` in the repo also
holds the stamping tool and its tests, and stamping rewrites `index.html`,
which would leave your checkout modified.

Copy the two vhost files:

```bash
sudo cp apache/photoshare-gallery.conf /etc/apache2/sites-available/
sudo cp apache/photoshare-admin.conf   /etc/apache2/sites-available/
```

Edit each copy and change only `ServerName`:

- `photoshare-gallery.conf`: `ServerName gallery.local`
- `photoshare-admin.conf`: `ServerName admin.local`

Both already `ProxyPass` to the correct loopback ports (8100/8200) and use
the `/var/www/...` document roots above. Ignore the TLS/certbot comments in
both files entirely; this is plain HTTP on your own machine, no certificate
needed.

Enable and reload:

```bash
sudo a2ensite photoshare-gallery photoshare-admin
sudo apache2ctl configtest   # must say "Syntax OK"
sudo systemctl reload apache2
```

Quick check that the caching rules are active:

```bash
curl -sI http://gallery.local/ | grep -i cache-control
#   expect: Cache-Control: no-cache
```

## 8. Use it

- Gallery: [http://gallery.local](http://gallery.local)
- Admin: [http://admin.local](http://admin.local)

Open a photo and use the lightbox's Download button to save it with its
original filename (press `D` as a shortcut). `frontend/TESTING.md` has the
full manual checklist, including phones.

## Optional: LAN access from other devices

To reach the gallery from your phone or another computer on the same
network instead of just this machine:

1. Find this machine's LAN IP: `ip addr show` (or `hostname -I`).
2. Open the firewall for port 80: `sudo ufw allow 80/tcp` (or your
   distro's firewall equivalent). Apache2's default `Listen 80` already
   accepts connections from any interface, not just loopback, so no
   config change is needed there.
3. On each device that needs access, add the same two hostnames to that
   device's own hosts file pointing at the LAN IP instead of `127.0.0.1`
   (or use this machine's `avahi`/mDNS name, e.g. `desktop.local`, if your
   network supports it, and adjust `ServerName` accordingly).

This exposes the gallery to your whole LAN with no authentication in
front of Apache2 itself — the API-key checks still protect the proxies,
but anyone on your network can load the frontend. Ports 8100/8200 are also
reachable from the LAN unless you bound them to `127.0.0.1` in step 6;
Docker-published ports bypass `ufw`, so the firewall rule above doesn't
cover them. Skip this section
entirely if you only need access from the desktop itself.

## Day-to-day operations

- **Logs:** `docker compose logs -f backend` (or `data-proxy` /
  `admin-proxy`).
- **Update to the latest code:**
  ```bash
  cd ~/photoshare
  OLD=$(git rev-parse HEAD)
  git pull --ff-only
  docker compose build && docker compose up -d
  sudo cp frontend/{index.html,about.html,script.js,style.css,common.js} /var/www/photoshare-gallery/
  sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery
  sudo cp -r admin-frontend/* /var/www/photoshare-admin/
  git diff "$OLD"..HEAD -- apache/   # any vhost changes?
  ```
  `~/photoshare-data/cache` is untouched, so existing API keys keep
  working. Always re-run `stamp_assets.py` after copying the gallery files.
  If `apache/` changed, apply the same edit to your installed vhost (or
  re-copy it and set `ServerName` again), then `sudo apache2ctl configtest
  && sudo systemctl reload apache2`.
- **Coming from an older unzipped bundle:** clone the repo as in step 2,
  copy your existing `.env` into it (compare with `.env.example` and add
  any missing variables), run `docker compose down` in the old folder,
  then follow steps 6–7 from the new one. If your `.env` points the cache
  and logs at `~/photoshare-data` (step 3), API keys carry over; if it used
  paths inside the old folder, copy those directories across first. If your old vhosts pointed
  `DocumentRoot` at the bundle folder, switch them back to the
  `/var/www/...` paths from step 7.
- **Restart everything after a reboot:** nothing to do manually — Docker
  starts on boot (step 1) and every service has `restart: unless-stopped`,
  so the stack comes back up on its own once the Docker daemon is
  running. Apache2 is enabled the same way.
- **Back up your keys:** `~/photoshare-data/cache` holds the backend's
  SQLite store, including hashed API keys. Back this directory up the
  same way you'd back up any other personal data on this machine.

## What this guide skips (vs. the cloud guide)

No server provisioning/hardening, no DNS, no TLS/certbot, no firewall
rules beyond the optional LAN section above. If you later want this
desktop reachable from the public internet rather than just your LAN,
follow `CLOUD_DEPLOY.md`'s TLS and firewall sections instead — the
Compose/Apache2 architecture underneath doesn't change either way.
