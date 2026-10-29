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

**Docker Engine + the Compose plugin.** Package names vary by distro:

```bash
# Ubuntu (distro packages; the Compose plugin is called docker-compose-v2)
sudo apt update
sudo apt install -y docker.io docker-compose-v2

# Debian, Fedora/RHEL and others: Docker's own packages
# (installs docker-ce plus docker-compose-plugin)
curl -fsSL https://get.docker.com | sudo sh

# Arch-family (pacman)
sudo pacman -S docker docker-compose

docker compose version   # must work (with a space) before continuing
```

`docker-compose-plugin` only exists in Docker's own repository, so
`apt install docker-compose-plugin` fails on a stock Ubuntu install.

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
`OVERVIEW.md` — which only works when the frontend HTML and its proxy are
served from the same origin):

```bash
sudo apt install -y apache2
sudo a2enmod proxy proxy_http headers
sudo systemctl enable --now apache2
```

The Apache2 commands in this guide (`a2enmod`, `a2ensite`,
`sites-available/`, `/var/www`) use the Debian/Ubuntu layout. On Fedora or
Arch the package is `httpd`: enable the same modules in its config, and
put the two vhost files in `/etc/httpd/conf.d/` (Fedora) or include them
from `/etc/httpd/conf/httpd.conf` (Arch) instead of using `a2ensite`. Also:

- Replace `${APACHE_LOG_DIR}` in both vhost files with a real log folder
  (e.g. `/var/log/httpd`). That variable is only defined on Debian/Ubuntu,
  and `httpd` won't start without it.
- On Fedora/RHEL with SELinux enforcing, allow Apache to connect to the
  proxy ports, or every `/gallery/*` and `/admin/*` request fails with a
  503: `sudo setsebool -P httpd_can_network_connect 1`.

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
# Per-server settings (share-link address): copied only the first time,
# so your edits survive updates. Never overwrite it.
[ -e /var/www/photoshare-gallery/site-config.js ] || sudo cp frontend/site-config.js /var/www/photoshare-gallery/
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

Do this even if `photoshare-gallery.conf` / `photoshare-admin.conf`
already exist in `sites-available/` from an earlier bundle or install.
Older copies may lack the browser-caching rules or point `DocumentRoot`
somewhere else, and Apache2 will keep serving with them without any error.
`cp` overwrites them; you'll set `ServerName` again below.

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

### Verify the deployment

Run these from your checkout. Each one prints a result, so a silent
failure can't look like a pass.

**1. Installed vhosts match the repo.** This compares each installed
vhost with the repo copy, ignoring the `ServerName`/`ServerAlias` lines
you edited:

```bash
for s in gallery admin; do
  if diff <(grep -vE '^\s*(ServerName|ServerAlias)\b' apache/photoshare-$s.conf) \
          <(grep -vE '^\s*(ServerName|ServerAlias)\b' /etc/apache2/sites-available/photoshare-$s.conf); then
    echo "photoshare-$s.conf: matches repo"
  else
    echo "photoshare-$s.conf: DIFFERS from repo -- re-copy it (see above)"
  fi
done
```

Any output other than `matches repo` means the installed file is stale or
hand-edited. On Fedora/Arch, point the second path at your `httpd` copy;
there, the `${APACHE_LOG_DIR}` replacement will show up as an expected
difference.

**2. Each hostname reaches the right vhost:**

```bash
sudo apache2ctl -S 2>/dev/null | grep -E 'namevhost (gallery|admin)\.local'
```

Both lines must name `sites-enabled/photoshare-gallery.conf` and
`sites-enabled/photoshare-admin.conf`. If a hostname is missing, its
`ServerName` wasn't changed, and Apache2 sends that host to
`000-default.conf` instead. (Ignore the `AH00558 ... fully qualified domain
name` warning. It's harmless, and `2>/dev/null` hides it.)

**3. The gallery page is stamped and served with caching rules:**

```bash
grep -c '?v=' /var/www/photoshare-gallery/index.html
#   expect: 4   (0 means stamp_assets.py wasn't run on this copy;
#                3 means site-config.js is missing from the web root)
stat -c 'on disk: %s bytes' /var/www/photoshare-gallery/index.html
curl -sI http://gallery.local/ | grep -iE '^(cache-control|content-length):' \
  || echo "no Cache-Control/Content-Length in reply -- see table below"
#   expect: Cache-Control: no-cache
#           Content-Length: <same number as "on disk">
```

| Symptom | Likely cause | Fix |
|---|---|---|
| Check 1 says `DIFFERS` | Old or edited vhost in `sites-available/` | Re-copy both vhosts, set `ServerName`, reload |
| Check 2 is missing a hostname | `ServerName` still `*.example.com`, or site not enabled | Edit `ServerName`; `sudo a2ensite ...`; reload |
| No `Cache-Control`, Content-Length differs from on-disk size | Request is served from a different `DocumentRoot` (often an old bundle folder) | Re-copy the vhosts (check 1) and reload |
| No `Cache-Control`, sizes match | `mod_headers` off, or Apache2 not reloaded | `sudo a2enmod headers`; `sudo systemctl reload apache2` |
| `grep -c` prints `0` | Gallery files copied but not stamped | Re-run `stamp_assets.py` on `/var/www/photoshare-gallery` |

Re-run all three checks after any fix.

## 8. Use it

- Gallery: [http://gallery.local](http://gallery.local)
- Admin: [http://admin.local](http://admin.local)

Open a photo and use the lightbox's Download button to save it with its
original filename (press `D` as a shortcut). `frontend/TESTING.md` has the
full manual checklist, including phones.

### Sharing links

**Copy link** in the lightbox (shortcut `L`) and **Share album** above the
folders hand out a link to the photo or album. Because this setup is plain
HTTP, the browser can't copy automatically: a box opens with the link
selected and a **Copy** button. The box also says who the link works for:

- `http://gallery.local` or `localhost`: only this computer. Another
  device resolves `gallery.local` to itself, not to this machine.
- A LAN address or name (see the next section): only devices on your
  home network that use the same address or hosts entry.

To share with people outside, you need a public address that reaches this
machine (for example a router port-forward with a dynamic-DNS name, or a
tunnel such as Cloudflare Tunnel or Tailscale Funnel), and its name as a
`ServerAlias` in the gallery vhost. Then set it in
`/var/www/photoshare-gallery/site-config.js`:

```js
    shareBaseUrl: 'https://photos.example.net',
```

and re-run `sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery`
(browsers keep tagged files for a year, so the tag must change). Deploys
never overwrite `site-config.js`. Shared links then always start with that
address, and the "only works on" note goes away.

Links go through a small preview page in `data-proxy` (`/gallery/share`)
that gives chat apps the album or photo name and a picture. Apps that
build the preview on the sender's phone (iMessage, WhatsApp, Signal) show
it whenever that phone can reach the gallery. Apps that build it on their
own servers (Slack, Discord, Facebook) show it only with a public address
as above. Without a preview, the link still works for anyone who can reach
the gallery.

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
  [ -e /var/www/photoshare-gallery/site-config.js ] || sudo cp frontend/site-config.js /var/www/photoshare-gallery/
  sudo python3 frontend/stamp_assets.py /var/www/photoshare-gallery
  sudo cp -r admin-frontend/* /var/www/photoshare-admin/
  git diff "$OLD"..HEAD -- apache/   # any vhost changes?
  ```
  `~/photoshare-data/cache` is untouched, so existing API keys keep
  working. Always re-run `stamp_assets.py` after copying the gallery files.
  If `apache/` changed, apply the same edit to your installed vhost (or
  re-copy it and set `ServerName` again), then `sudo apache2ctl configtest
  && sudo systemctl reload apache2`. Then run the three checks under
  "Verify the deployment" in step 7. Check 1 compares against the repo
  directly, so it also catches a vhost that was already stale before this
  pull, which `git diff "$OLD"..HEAD` can't see.
- **Frontend-only updates**, such as the per-portfolio background
  ([PR #7](https://github.com/Edward-King/dynamo/pull/7)): the commands
  above still apply, but `docker compose build` has nothing to rebuild and
  `apache/` doesn't change, so copying and re-stamping the gallery files is
  what puts the new code live. Confirm it:
  ```bash
  grep -c applyPortfolioBackground /var/www/photoshare-gallery/common.js /var/www/photoshare-gallery/script.js
  #   expect: 1 or more for each file (0 means the old files are still deployed)
  grep -o 'common.js?v=[0-9a-f]*' /var/www/photoshare-gallery/index.html /var/www/photoshare-gallery/about.html
  #   expect: the same tag in both pages, different from before the update
  ```
  Then open [http://gallery.local](http://gallery.local) and click into a
  portfolio: the dimmed background changes to that portfolio's icon image.
- **Share links** ([PR #9](https://github.com/Edward-King/dynamo/pull/9))
  change both the gallery and `data-proxy`, so `docker compose build && docker
  compose up -d` is required this time (it adds the `/gallery/share`
  preview page; without it shared links open `{"detail":"Not Found"}`).
  The update commands above also add `site-config.js` the first time.
  Confirm it:
  ```bash
  grep -c shareCurrentPhoto /var/www/photoshare-gallery/script.js
  #   expect: 1 or more
  ROOT_ID=$(curl -s http://gallery.local/gallery/portfolios/root | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
  curl -s "http://gallery.local/gallery/share?p=$ROOT_ID" | grep -o '<meta property="og:title" content="[^"]*"'
  #   expect: og:title with your library's name
  ```
  Optional: `GALLERY_PROXY_SITE_NAME` in `.env` sets the site name in
  previews, and **Preheat thumbnails** in the admin UI with width `800`,
  height `600` makes previews faster. See "Sharing links" in step 8.
- **Coming from an older unzipped bundle:** clone the repo as in step 2,
  copy your existing `.env` into it (compare with `.env.example` and add
  any missing variables), run `docker compose down` in the old folder,
  then follow steps 6–7 from the new one. If your `.env` points the cache
  and logs at `~/photoshare-data` (step 3), API keys carry over; if it used
  paths inside the old folder, copy those directories across first. Step 7
  overwrites the old vhosts with the repo's copies. Don't keep or patch
  the bundle-era files, which may lack the caching rules or point
  `DocumentRoot` at the bundle folder. Finish with the checks under
  "Verify the deployment".
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
