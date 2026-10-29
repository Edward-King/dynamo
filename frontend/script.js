// ---------------------------------------------------------------------------
// PhotoShare Gallery frontend.
//
// Talks only to the local proxy (see /photoshare-proxy) at PROXY_BASE_URL --
// never to PhotoShare directly, and never holds an API key. The proxy
// exposes a small, gallery-shaped subset of PhotoShare's API:
//
//   GET  /gallery/portfolios/root
//   GET  /gallery/portfolios/{id}
//   GET  /gallery/portfolios/{id}/children
//   GET  /gallery/portfolios/{id}/images
//   GET  /gallery/image/{id}/thumb
//   GET  /gallery/image/{id}/full
//   GET  /gallery/image/{id}/full?download=1   (saved as a file, real filename)
//
// Share buttons hand out /gallery/share?p=<album>[&i=<photo>] links: a small
// proxy page with link-preview tags that sends people on to this page.
//
// The page background follows the open portfolio: loadPortfolio() asks
// common.js to show that portfolio's icon image (or the fallback gradient).
//
// Navigation model: a breadcrumb trail of portfolios the user has drilled
// into, starting at root. Clicking a folder tile pushes onto the trail and
// re-fetches; clicking a breadcrumb entry truncates the trail back to that
// point and re-fetches. Images in the *current* portfolio populate both the
// grid and the lightbox's navigable set.
//
// Addresses: the address bar follows what's on screen -- ?p=<album id> for
// an album (nothing at the root) plus &i=<photo id> while a photo is open --
// so Back/Forward, reloading and pasting the address all work. Opening an
// album or a photo adds a history entry; stepping between photos replaces
// it, so Back leaves the photo viewer in one step.
// ---------------------------------------------------------------------------

// PROXY_BASE_URL is declared once in common.js (loaded before this file on
// index.html) so both the header/background logic and the gallery logic
// stay pointed at the same proxy without risking the two constants drifting.

// Ordered list of {id, name} the user has navigated through, root first.
// An entry with id null is a folder name shown as plain text: an album
// opened from a link knows the names of the folders above it (from its
// path) but not their IDs.
let breadcrumbTrail = [];

// Images currently loaded for the active portfolio (drives the lightbox).
let currentImages = [];
let currentIndex = 0;
let lastFocusedThumbnail = null;

// Bumped by every loadPortfolio() call. A load only updates the page if
// it's still the newest one, so when the user clicks quickly through
// portfolios a slow, older response can't overwrite the folders, images,
// or status message of the portfolio actually being shown.
let portfolioLoadSeq = 0;

const dom = {};

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchJSON(path) {
    const res = await fetch(`${PROXY_BASE_URL}${path}`);
    if (!res.ok) {
        const body = await res.text();
        const err = new Error(`Request to ${path} failed (${res.status}): ${body}`);
        err.status = res.status;
        throw err;
    }
    return res.json();
}

async function fetchAllImages(portUuid, isCurrent = () => true) {
    // Walks pagination until has_next is false. Fine for personal-library
    // sized portfolios; page_size is already the proxy's max (200 per PhotoShare's
    // own server-side clamp).
    let page = 1;
    let all = [];
    while (true) {
        const data = await fetchJSON(`/gallery/portfolios/${portUuid}/images?page=${page}&page_size=200`);
        all = all.concat(data.items || []);
        // Stop paging once a newer load has replaced this one; its results
        // would be thrown away anyway.
        if (!data.has_next || !isCurrent()) break;
        page += 1;
    }
    return all;
}

// ---------------------------------------------------------------------------
// Rendering: breadcrumb
// ---------------------------------------------------------------------------

function renderBreadcrumb() {
    dom.breadcrumb.innerHTML = '';

    breadcrumbTrail.forEach((node, index) => {
        const isLast = index === breadcrumbTrail.length - 1;

        if (!node.id && !isLast) {
            // Folder known only by name (see breadcrumbTrail): not clickable.
            const text = document.createElement('span');
            text.className = 'breadcrumb-text';
            text.textContent = node.name;
            dom.breadcrumb.appendChild(text);
        } else {
            const btn = document.createElement('button');
            btn.textContent = node.name;
            btn.type = 'button';
            if (isLast) {
                btn.setAttribute('aria-current', 'true');
                btn.disabled = true;
            } else {
                btn.addEventListener('click', () => navigateToTrailIndex(index));
            }
            dom.breadcrumb.appendChild(btn);
        }

        if (!isLast) {
            const sep = document.createElement('span');
            sep.className = 'breadcrumb-sep';
            sep.textContent = '/';
            sep.setAttribute('aria-hidden', 'true');
            dom.breadcrumb.appendChild(sep);
        }
    });
}

// ---------------------------------------------------------------------------
// Rendering: folders (sub-portfolios)
// ---------------------------------------------------------------------------

function renderFolders(children) {
    dom.folderGrid.innerHTML = '';

    children.forEach((portfolio) => {
        const tile = document.createElement('div');
        tile.className = 'folder-tile';
        tile.setAttribute('tabindex', '0');
        tile.setAttribute('role', 'button');
        tile.setAttribute('aria-label', `Open portfolio ${portfolio.name}`);

        const openThisFolder = () => enterPortfolio(portfolio.id, portfolio.name);
        tile.addEventListener('click', openThisFolder);
        tile.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openThisFolder();
            }
        });

        if (portfolio.icon_thumbnail_url) {
            const icon = document.createElement('img');
            icon.className = 'folder-tile-icon';
            icon.src = `${PROXY_BASE_URL}${portfolio.icon_thumbnail_url}`;
            icon.alt = '';
            icon.loading = 'lazy';
            icon.decoding = 'async';
            tile.appendChild(icon);
        } else {
            const placeholder = document.createElement('div');
            placeholder.className = 'folder-tile-icon-placeholder';
            placeholder.textContent = '\u{1F4C1}'; // folder emoji as a lightweight fallback glyph
            placeholder.setAttribute('aria-hidden', 'true');
            tile.appendChild(placeholder);
        }

        const label = document.createElement('div');
        label.className = 'folder-tile-name';
        label.textContent = portfolio.name;
        tile.appendChild(label);

        dom.folderGrid.appendChild(tile);
    });
}

// ---------------------------------------------------------------------------
// Rendering: image grid (same structure/behavior as the static version)
// ---------------------------------------------------------------------------

function renderImageGrid(images) {
    dom.grid.innerHTML = '';

    images.forEach((item, index) => {
        const container = document.createElement('div');
        container.className = 'grid-img-container';
        container.setAttribute('tabindex', '0');
        container.setAttribute('role', 'button');

        const caption = item.name || item.alternate_name || '';
        container.setAttribute('aria-label', caption);

        container.addEventListener('click', () => openLightbox(index, container));
        container.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openLightbox(index, container);
            }
        });

        // Hover/zoom-in-place styling is driven by this JS-managed class
        // rather than CSS :hover, to avoid Safari/WebKit's well-known bug
        // where :hover can stay "stuck" on an element after a click if the
        // pointer doesn't move afterward (e.g. clicking a thumbnail to open
        // the lightbox, then closing it without moving the mouse).
        //
        // Deliberately keyed off 'mousemove' rather than 'mouseenter': when
        // the lightbox modal closes, the tile underneath is suddenly back
        // under the (stationary) cursor, and browsers dispatch a synthetic
        // mouseenter/mouseover to reflect the new hit-test target even
        // though the user's hand never moved. 'mousemove' only ever fires
        // from genuine pointer motion, so it can't be fooled by an element
        // reappearing under a static cursor.
        container.addEventListener('mousemove', () => container.classList.add('is-active'));
        container.addEventListener('mouseleave', () => container.classList.remove('is-active'));

        const img = document.createElement('img');
        img.src = `${PROXY_BASE_URL}${item.thumbnail_url}`;
        img.alt = caption;
        img.loading = 'lazy';
        img.decoding = 'async';

        const overlay = document.createElement('div');
        overlay.className = 'hover-text-overlay';
        const captionEl = document.createElement('p');
        captionEl.textContent = caption;
        overlay.appendChild(captionEl);

        container.appendChild(img);
        container.appendChild(overlay);
        dom.grid.appendChild(container);
    });
}

// ---------------------------------------------------------------------------
// Portfolio navigation
// ---------------------------------------------------------------------------

function setStatus(message, isError) {
    dom.status.textContent = message || '';
    dom.status.classList.toggle('error', Boolean(isError));
}

// Resolves true once this portfolio is on screen, or false if it failed or
// a newer load replaced it (so callers know whether it's safe to open a
// photo from it).
async function loadPortfolio(portUuid, displayName) {
    const seq = ++portfolioLoadSeq;
    const isCurrent = () => seq === portfolioLoadSeq;

    setStatus('Loading\u2026', false);
    dom.folderGrid.innerHTML = '';
    dom.grid.innerHTML = '';

    // Started separately and not awaited with the requests below, so a slow
    // or failed background can't delay or break the folders and images.
    applyPortfolioBackground(portUuid);

    try {
        const [children, images] = await Promise.all([
            fetchJSON(`/gallery/portfolios/${portUuid}/children`),
            fetchAllImages(portUuid, isCurrent),
        ]);

        // A newer load started while this one was waiting: leave the page
        // to it.
        if (!isCurrent()) return false;

        renderFolders(children);

        currentImages = images;
        renderImageGrid(images);

        if (children.length === 0 && images.length === 0) {
            setStatus('This portfolio is empty.', false);
        } else {
            setStatus('', false);
        }
        updateShareAlbumButton();
        return true;
    } catch (err) {
        // An outdated load failing must not put an error over the
        // portfolio that's now open.
        if (!isCurrent()) return false;
        console.error(err);
        setStatus(
            `Could not load "${displayName}". Is the gallery proxy running at ${PROXY_BASE_URL}?`,
            true
        );
        updateShareAlbumButton(false);
        return false;
    }
}

function currentPortfolio() {
    return breadcrumbTrail[breadcrumbTrail.length - 1] || null;
}

async function enterPortfolio(portUuid, name) {
    breadcrumbTrail.push({ id: portUuid, name });
    renderBreadcrumb();
    recordHistory('push');
    await loadPortfolio(portUuid, name);
}

async function navigateToTrailIndex(index) {
    breadcrumbTrail = breadcrumbTrail.slice(0, index + 1);
    renderBreadcrumb();
    recordHistory('push');
    const node = currentPortfolio();
    await loadPortfolio(node.id, node.name);
}

// ---------------------------------------------------------------------------
// Address bar and history
// ---------------------------------------------------------------------------

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// True while the open lightbox has its own history entry (opened by a click
// here, or by Forward). Closing it then goes Back one entry instead of
// leaving a duplicate album entry behind. False when the photo came straight
// from a link, where there's no earlier entry to go back to.
let lightboxHasHistoryEntry = false;

// The photo shown in the lightbox, or null when it's closed.
function openImageId() {
    if (!isLightboxOpen()) return null;
    const item = currentImages[currentIndex];
    return item ? item.id : null;
}

// The page address for an album and, optionally, a photo in it. Root is
// the plain page address. Other query parameters are kept.
function galleryAddress(albumId, imageId) {
    const params = new URLSearchParams(window.location.search);
    params.delete('p');
    params.delete('i');
    const root = breadcrumbTrail[0];
    if (albumId && !(root && root.id === albumId && !imageId)) params.set('p', albumId);
    if (albumId && imageId) params.set('i', imageId);
    const query = params.toString();
    return `${window.location.pathname}${query ? `?${query}` : ''}`;
}

// mode: 'push' adds an entry, 'replace' rewrites the current one.
function recordHistory(mode, imageId) {
    const node = currentPortfolio();
    if (!node || !window.history || !history.pushState) return;
    const image = imageId === undefined ? openImageId() : imageId;
    const state = { gallery: true, trail: breadcrumbTrail.slice(), image: image || null };
    try {
        if (mode === 'push') history.pushState(state, '', galleryAddress(node.id, image));
        else history.replaceState(state, '', galleryAddress(node.id, image));
    } catch (err) {
        // Safari limits how often the address can change (e.g. very fast
        // swiping); the address just lags behind. Never worth breaking for.
        console.warn('Could not update the address bar.', err);
    }
}

function readAddress() {
    const params = new URLSearchParams(window.location.search);
    return { albumId: params.get('p'), imageId: params.get('i') };
}

// Open a photo of the album on screen by ID, if it's there.
function openImageById(imageId, hasHistoryEntry) {
    const index = currentImages.findIndex((item) => item.id === imageId);
    if (index < 0) return false;
    openLightbox(index, dom.grid.children[index], { history: 'none' });
    lightboxHasHistoryEntry = hasHistoryEntry;
    return true;
}

function showUnavailable(message) {
    // Message plus a way back to the Library.
    setStatus(message, false);
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'status-action';
    button.textContent = 'Go to the Library';
    button.addEventListener('click', () => navigateToTrailIndex(0));
    dom.status.appendChild(document.createTextNode(' '));
    dom.status.appendChild(button);
}

// Back/Forward: show whatever the entry describes.
async function onHistoryNavigation(event) {
    const state = event.state;
    if (!state || !state.gallery || !Array.isArray(state.trail) || !state.trail.length) {
        // An entry this page didn't make (e.g. a typed address): read it.
        if (isLightboxOpen()) closeLightbox({ history: 'none' });
        await openFromAddress();
        return;
    }
    const target = state.trail[state.trail.length - 1];
    const node = currentPortfolio();
    if (!node || node.id !== target.id) {
        if (isLightboxOpen()) closeLightbox({ history: 'none' });
        breadcrumbTrail = state.trail.slice();
        renderBreadcrumb();
        const loaded = await loadPortfolio(target.id, target.name);
        if (loaded && state.image && !openImageById(state.image, true)) {
            setStatus("This photo isn't available any more.", false);
        }
        return;
    }
    // Same album: only the photo changed.
    if (!state.image) {
        if (isLightboxOpen()) closeLightbox({ history: 'none' });
    } else if (openImageId() !== state.image) {
        const index = currentImages.findIndex((item) => item.id === state.image);
        if (index < 0) return;
        if (isLightboxOpen()) {
            currentIndex = index;
            updateLightboxContent();
        } else {
            openImageById(state.image, true);
        }
    }
}

// Show the album (and photo) named in the address. Used on first load and
// for history entries this page didn't create.
async function openFromAddress() {
    const { albumId, imageId } = readAddress();
    setStatus('Loading\u2026', false);
    const startSeq = portfolioLoadSeq;
    try {
        if (!albumId) {
            const root = await fetchJSON('/gallery/portfolios/root');
            if (portfolioLoadSeq !== startSeq) return;
            breadcrumbTrail = [{ id: root.id, name: root.name || 'Library' }];
            renderBreadcrumb();
            recordHistory('replace', null);
            await loadPortfolio(root.id, root.name || 'Library');
            return;
        }

        // An album link: the album itself, plus root for the Library
        // breadcrumb. Root's photos aren't loaded and its background isn't
        // shown, so the album's background is the first one to appear.
        const valid = UUID_PATTERN.test(albumId);
        const [root, album] = await Promise.all([
            fetchJSON('/gallery/portfolios/root'),
            valid ? fetchJSON(`/gallery/portfolios/${albumId}`).catch((err) => {
                if (err.status === 400 || err.status === 404 || err.status === 422) return null;
                throw err;
            }) : Promise.resolve(null),
        ]);
        if (portfolioLoadSeq !== startSeq) return;
        const rootNode = { id: root.id, name: root.name || 'Library' };

        if (!album) {
            breadcrumbTrail = [rootNode];
            renderBreadcrumb();
            applyPortfolioBackground(root.id);
            showUnavailable("This album isn't available any more.");
            updateShareAlbumButton(false);
            return;
        }

        breadcrumbTrail = [rootNode];
        if (album.id !== root.id) {
            // Folder names between the Library and the album, from its path
            // (e.g. "Travel/Japan/Kyoto" -> Travel, Japan). Virtual albums
            // have no folders of their own, so nothing is added for them.
            const parts = album.virtual ? [] : String(album.rel_path || '').split('/')
                .filter((part) => part && part !== '.');
            parts.slice(0, -1).forEach((name) => breadcrumbTrail.push({ id: null, name }));
            breadcrumbTrail.push({ id: album.id, name: album.name || 'Album' });
        }
        renderBreadcrumb();
        const node = currentPortfolio();
        const loaded = await loadPortfolio(node.id, node.name);
        if (!loaded) return;
        if (imageId && openImageById(imageId, false)) {
            recordHistory('replace', imageId);
        } else {
            recordHistory('replace', null);
            if (imageId) setStatus("This photo isn't available any more.", false);
        }
    } catch (err) {
        if (portfolioLoadSeq !== startSeq) return;
        console.error(err);
        applyFallbackBackground();
        setStatus(
            `Could not reach the gallery proxy at ${PROXY_BASE_URL}. Make sure it's running.`,
            true
        );
    }
}

function initGallery() {
    return openFromAddress();
}

// ---------------------------------------------------------------------------
// Lightbox (unchanged behavior from the static version: swipe, keyboard,
// focus management -- just re-pointed at currentImages/proxy URLs)
// ---------------------------------------------------------------------------

// options.history: 'push' (default) gives the open photo its own history
// entry; 'none' leaves history alone (the caller handles it).
function openLightbox(index, triggerEl, options = {}) {
    currentIndex = index;
    lastFocusedThumbnail = triggerEl || document.activeElement;
    updateLightboxContent();

    dom.lightbox.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    dom.closeBtn.focus();

    if (options.history !== 'none') {
        recordHistory('push');
        lightboxHasHistoryEntry = true;
    }
}

function updateLightboxContent() {
    const item = currentImages[currentIndex];
    if (!item) return;

    dom.lightboxImg.style.animation = 'none';
    dom.lightboxImg.style.webkitAnimation = 'none';
    void dom.lightboxImg.offsetHeight; // Force reflow
    dom.lightboxImg.style.animation = null;
    dom.lightboxImg.style.webkitAnimation = null;

    const caption = item.name || item.alternate_name || '';
    dom.lightboxImg.src = `${PROXY_BASE_URL}${item.full_url}`;
    dom.lightboxImg.alt = caption;
    dom.lightboxCaption.textContent = caption;

    // On share-sheet devices, get the file ready now so a tap on Save can
    // open the share sheet immediately (see prepareShareFile).
    prepareShareFile(item);
}

// options.history: 'auto' (default) takes the photo out of the address;
// 'none' when Back/Forward already did.
function closeLightbox(options = {}) {
    const wasOpen = isLightboxOpen();
    dom.lightbox.style.display = 'none';
    document.body.style.overflow = '';
    releaseShareFile();
    hideNotice();
    closeLinkPopover(false);

    if (wasOpen && options.history !== 'none') {
        if (lightboxHasHistoryEntry) {
            // Return to the album's own entry, so Back afterwards leaves the
            // album rather than reopening the photo.
            history.back();
        } else {
            recordHistory('replace', null);
        }
    }
    lightboxHasHistoryEntry = false;

    if (lastFocusedThumbnail) {
        const thumbnail = lastFocusedThumbnail;

        // The zoom/caption-overlay "hovered" look is driven by the
        // JS-managed .is-active class (added/removed on real mouseenter/
        // mouseleave -- see renderImageGrid), not CSS :hover. That sidesteps
        // Safari/WebKit's well-known sticky-:hover bug, but it introduces a
        // parallel issue here: opening the lightbox added .is-active on
        // mouseenter, and while the lightbox is open the modal covers the
        // pointer so no mouseleave ever fires on the tile underneath. Clear
        // it explicitly on close so the tile doesn't stay stuck looking
        // "hovered" no matter which browser is used.
        thumbnail.classList.remove('is-active');

        // Restoring focus programmatically also satisfies the thumbnail's
        // :focus-within CSS selector (used for keyboard-accessible caption
        // display), which would otherwise leave the caption overlay stuck
        // visible until the tile naturally loses focus. Suppress it until
        // the user does something that isn't keyboard navigation: a real
        // mouse hover over this tile (then release focus, since the mouse
        // is now driving), or the tile actually losing focus.
        thumbnail.classList.add('suppress-focus-overlay');
        thumbnail.focus();

        // Keyed off 'mousemove' rather than 'mouseenter' for the same
        // reason as the .is-active wiring above: closing the modal can
        // itself trigger a synthetic mouseenter on this tile (the cursor
        // didn't move, but the element under it changed), which would
        // otherwise immediately undo the suppression we just set.
        const clearSuppression = () => {
            thumbnail.classList.remove('suppress-focus-overlay');
            thumbnail.removeEventListener('mousemove', onRealMouseMove);
            thumbnail.removeEventListener('blur', clearSuppression);
            document.removeEventListener('keydown', clearSuppression);
        };
        const onRealMouseMove = () => {
            clearSuppression();
            thumbnail.blur();
        };
        thumbnail.addEventListener('mousemove', onRealMouseMove);
        thumbnail.addEventListener('blur', clearSuppression);
        document.addEventListener('keydown', clearSuppression);
    }
}

function navigateLightbox(direction) {
    if (currentImages.length === 0) return;
    currentIndex = (currentIndex + direction + currentImages.length) % currentImages.length;
    updateLightboxContent();
    recordHistory('replace');
}

function isLightboxOpen() {
    return dom.lightbox.style.display === 'flex';
}

// ---------------------------------------------------------------------------
// Download / Save
//
// Two paths, chosen once per page load by what the browser can do (never by
// user-agent sniffing):
//
//   * Share mode -- the main input is touch with no hover (phones, most
//     tablets) AND the browser can share files (Web Share API Level 2: iOS/
//     iPadOS Safari, Android Chrome). The button reads "Save" and opens the
//     system share sheet, where iOS offers "Save Image" to Photos. A plain
//     download on iOS would land in the Files app instead, which isn't where
//     people look for photos.
//
//   * Download mode -- everything else (desktop browsers on macOS, Windows,
//     Linux; touch devices without file sharing). The button reads
//     "Download" and points the browser at /full?download=1, where the proxy
//     sends Content-Disposition: attachment with the image's real filename.
//     The proxy has to do this because the gallery and proxy are on different
//     origins and browsers ignore the HTML `download` attribute cross-origin.
//
// Long-press ("Save to Photos" / "Download image") and right-click ("Save
// Image As...") on the lightbox photo are deliberately left alone: nothing
// here blocks the context menu or covers the image.
// ---------------------------------------------------------------------------

const SHARE_MODE = detectShareMode();

// The prepared File for the image currently in the lightbox (share mode only).
// Holding at most one at a time keeps memory bounded on phones.
let shareFileCache = { id: null, file: null, promise: null, controller: null };
let downloadBusy = false;
let noticeTimer = null;

// True once the bootstrap has found the Download button, its label and the
// notice area in the page. If any is missing -- e.g. a browser still has an
// older cached index.html from before the button existed, paired with this
// newer script -- the download feature switches itself off instead of
// throwing, so the rest of the gallery keeps working.
let downloadAvailable = false;

// Touch-first only: an iPad with a trackpad or a touchscreen laptop with a
// mouse reports hover support and is treated like a desktop.
function isTouchFirst() {
    if (!window.matchMedia) return false;
    return window.matchMedia('(pointer: coarse)').matches &&
        !window.matchMedia('(hover: hover)').matches;
}

function detectShareMode() {
    if (typeof navigator.share !== 'function' || typeof navigator.canShare !== 'function') return false;
    if (typeof File !== 'function' || !window.matchMedia) return false;
    if (!isTouchFirst()) return false;
    try {
        return navigator.canShare({ files: [new File([''], 'probe.jpg', { type: 'image/jpeg' })] });
    } catch (_) {
        return false;
    }
}

function downloadFilename(item, mimeType) {
    // Mirrors the proxy's _safe_download_filename: last path component, with
    // characters that are invalid on some OS (Windows is strictest) replaced.
    const name = (item.name || '')
        .split(/[\\/]/).pop()
        .replace(/[\x00-\x1f\x7f<>:"/\\|?*]/g, '_')
        .replace(/^[\s.]+|[\s.]+$/g, '');
    if (name) return name;
    let ext = ((mimeType || 'image/jpeg').split('/')[1] || 'jpg').split(';')[0];
    if (ext === 'jpeg') ext = 'jpg';
    return `${item.id}.${ext}`;
}

function prepareShareFile(item) {
    // Safari only opens the share sheet while it still counts the tap as
    // "recent". Downloading the photo after the tap can take long enough to
    // lose that, so in share mode the file is fetched as soon as the photo is
    // shown. The lightbox <img> uses the same CORS request (crossOrigin is set
    // at startup in share mode) and the proxy forwards PhotoShare's immutable
    // Cache-Control, so this is normally served from the browser cache rather
    // than downloaded a second time.
    //
    // Only the photo on screen is prepared: moving to another photo (or
    // closing the lightbox) cancels the previous photo's request if it's
    // still running, so quick swiping doesn't pile up full-size downloads.
    if (!downloadAvailable || !SHARE_MODE || !item || shareFileCache.id === item.id) return;
    releaseShareFile();
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    const entry = { id: item.id, file: null, promise: null, controller };
    entry.promise = fetch(`${PROXY_BASE_URL}${item.full_url}`, controller ? { signal: controller.signal } : {})
        .then((res) => {
            if (!res.ok) throw new Error(`Image request failed (${res.status})`);
            return res.blob();
        })
        .then((blob) => {
            const type = blob.type || item.mime_type || 'image/jpeg';
            entry.file = new File([blob], downloadFilename(item, type), { type });
            return entry.file;
        });
    // Errors are reported when the user actually taps Save, not here.
    entry.promise.catch(() => {});
    shareFileCache = entry;
}

function releaseShareFile() {
    // Cancel the request if it hasn't finished; harmless if it has.
    if (shareFileCache.controller && !shareFileCache.file) shareFileCache.controller.abort();
    shareFileCache = { id: null, file: null, promise: null, controller: null };
}

function setDownloadBusy(busy) {
    downloadBusy = busy;
    if (!dom.downloadBtn) return;
    dom.downloadBtn.classList.toggle('is-busy', busy);
    dom.downloadBtn.setAttribute('aria-busy', busy ? 'true' : 'false');
}

// Shows in the lightbox while it's open, otherwise at the bottom of the page.
// Longer messages (e.g. with a "home network only" hint) stay up longer.
function showNotice(message, isError) {
    clearTimeout(noticeTimer);
    hideNotice();
    const el = isLightboxOpen() ? dom.lightboxNotice : dom.pageNotice;
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('is-error', !!isError);
    el.classList.add('is-visible');
    const base = isError ? 4000 : 2500;
    noticeTimer = setTimeout(hideNotice, message.length > 40 ? base + 2500 : base);
}

function hideNotice() {
    clearTimeout(noticeTimer);
    if (dom.lightboxNotice) dom.lightboxNotice.classList.remove('is-visible');
    if (dom.pageNotice) dom.pageNotice.classList.remove('is-visible');
}

// How long a download frame is kept before it's removed. Removing it while
// the response headers are still arriving can cancel the download in some
// browsers, so this is generous; the frame is invisible and tiny meanwhile.
const DOWNLOAD_FRAME_LIFETIME_MS = 60000;

function startFileDownload(item) {
    // The download runs in a hidden iframe rather than by following a link
    // in this page. On success the proxy's attachment header makes the
    // browser save the file either way. On failure (e.g. a 502 while
    // PhotoShare is down) the error response isn't an attachment, so a link
    // would have navigated the whole gallery to a raw JSON error page; in an
    // iframe it loads out of sight and the gallery stays where it was.
    // A new frame per download avoids the browser ignoring a repeat of the
    // same URL.
    const sep = item.full_url.includes('?') ? '&' : '?';
    const frame = document.createElement('iframe');
    frame.className = 'download-frame';
    frame.setAttribute('aria-hidden', 'true');
    frame.tabIndex = -1;
    frame.src = `${PROXY_BASE_URL}${item.full_url}${sep}download=1`;
    document.body.appendChild(frame);
    setTimeout(() => frame.remove(), DOWNLOAD_FRAME_LIFETIME_MS);

    // The browser doesn't report where (or whether) the file was saved, so
    // the message stays neutral. The short busy window stops double taps
    // from starting two downloads of a large original.
    showNotice('Download started');
    setDownloadBusy(true);
    setTimeout(() => setDownloadBusy(false), 1500);
}

async function shareImage(item) {
    prepareShareFile(item);
    const entry = shareFileCache;
    // startFileDownload manages its own busy window; don't cut it short.
    let fellBackToDownload = false;
    setDownloadBusy(true);
    try {
        // If the file is already prepared there is no await before share(),
        // so the tap still counts as the user's action.
        let file = entry.file;
        if (!file) {
            try {
                file = await entry.promise;
            } catch (prepareErr) {
                if (prepareErr && prepareErr.name === 'AbortError') {
                    // The user swiped to another photo or closed the
                    // lightbox while it was being prepared -- nothing to do.
                    return;
                }
                // Couldn't get the photo ready for the share sheet (e.g. a
                // network hiccup). Rather than just showing an error, fall
                // back to a regular download, which is a separate request.
                console.warn('Could not prepare photo for sharing; downloading instead.', prepareErr);
                if (shareFileCache === entry) releaseShareFile();
                fellBackToDownload = true;
                startFileDownload(item);
                return;
            }
        }
        if (!navigator.canShare({ files: [file] })) {
            fellBackToDownload = true;
            startFileDownload(item);
            return;
        }
        await navigator.share({ files: [file], title: file.name });
    } catch (err) {
        const name = err && err.name;
        if (name === 'AbortError') {
            // User closed the share sheet -- not an error.
        } else if (name === 'NotAllowedError') {
            // The browser no longer treated this as a tap (slow network while
            // preparing the file). Fall back to a regular download.
            fellBackToDownload = true;
            startFileDownload(item);
        } else {
            // The share sheet itself failed (the photo was ready).
            console.error(err);
            showNotice("Couldn't save. Try again.", true);
            // Drop the failed attempt so the next tap fetches afresh.
            if (shareFileCache === entry) releaseShareFile();
        }
    } finally {
        if (!fellBackToDownload) setDownloadBusy(false);
    }
}

function handleDownload() {
    const item = currentImages[currentIndex];
    if (!downloadAvailable || !item || downloadBusy) return;
    if (SHARE_MODE) shareImage(item);
    else startFileDownload(item);
}

function configureDownloadButton() {
    dom.downloadBtn.classList.toggle('share-mode', SHARE_MODE);
    dom.downloadLabel.textContent = SHARE_MODE ? 'Save' : 'Download';
    dom.downloadBtn.setAttribute('aria-label', SHARE_MODE ? 'Save image' : 'Download image');
    dom.downloadBtn.title = SHARE_MODE ? 'Save image' : 'Download (D)';
    if (SHARE_MODE) {
        // Lets prepareShareFile reuse the image the lightbox already loaded.
        // Safe because every gallery request already depends on the proxy's
        // CORS settings allowing this page.
        dom.lightboxImg.crossOrigin = 'anonymous';
    }
}

// ---------------------------------------------------------------------------
// Share links
//
// "Copy link" in the photo viewer and "Share album" above the folders hand
// out a link to what's on screen. What happens is chosen by what the browser
// can do, never by looking at the address:
//
//   * Touch-first devices with a share sheet (secure pages only): the system
//     share sheet opens with the link.
//   * Other secure pages (HTTPS, or localhost): the link is copied and
//     "Link copied" shows.
//   * Plain-HTTP pages (e.g. http://gallery.local or a home-network address)
//     can't use either, so a small box shows the link already selected,
//     with a Copy button.
//
// Links point at the proxy's /gallery/share page, which gives chat apps a
// preview (name and photo) and then opens this page at the album or photo.
// They start with shareBaseUrl from site-config.js when it's set, otherwise
// with this page's own address.
// ---------------------------------------------------------------------------

// True once the photo viewer's share button and its label are in the page;
// the album button and the copy box are optional (see bootstrap).
let shareLinkAvailable = false;
let shareBusy = false;
let linkPopoverOpener = null;
let shareBaseWarned = false;

// Share sheet for links: needs only navigator.share (no file support).
function linkShareSheetAvailable() {
    return window.isSecureContext === true && typeof navigator.share === 'function' && isTouchFirst();
}

function clipboardAvailable() {
    return window.isSecureContext === true && !!navigator.clipboard &&
        typeof navigator.clipboard.writeText === 'function';
}

// { base, configured }: where share links start, without a trailing slash.
function shareBase() {
    const config = window.PHOTOSHARE_SITE_CONFIG;
    const raw = config && typeof config.shareBaseUrl === 'string' ? config.shareBaseUrl.trim() : '';
    if (raw) {
        try {
            const url = new URL(raw);
            if ((url.protocol === 'https:' || url.protocol === 'http:') && !url.search && !url.hash &&
                !url.username && !url.password) {
                return { base: `${url.origin}${url.pathname.replace(/\/+$/, '')}`, configured: true };
            }
        } catch (_) { /* reported below */ }
        if (!shareBaseWarned) {
            shareBaseWarned = true;
            console.warn(`site-config.js: shareBaseUrl "${raw}" isn't an http:// or https:// address; using this page's address instead.`);
        }
    }
    // This page's folder, so a gallery under a path prefix keeps it.
    const folder = window.location.pathname.replace(/[^/]*$/, '').replace(/\/+$/, '');
    return { base: `${window.location.origin}${folder}`, configured: false };
}

function shareUrl(albumId, imageId) {
    const { base } = shareBase();
    const query = `p=${encodeURIComponent(albumId)}${imageId ? `&i=${encodeURIComponent(imageId)}` : ''}`;
    // The preview page lives on the proxy. With PROXY_BASE_URL pointing at
    // another origin (local development without Apache) it isn't under
    // this address, so hand out the plain gallery address instead.
    if (PROXY_BASE_URL) return `${base}/?${query}`;
    return `${base}/gallery/share?${query}`;
}

// Private and loopback addresses work only on this computer or this
// network. Returns a hint for the person sharing, or '' (also when
// shareBaseUrl is set: then the owner has chosen the address).
function shareReachHint() {
    if (shareBase().configured) return '';
    const host = window.location.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    // U+2060 after the hyphen keeps "site-config.js" from wrapping there.
    const where = ' To share it further, set shareBaseUrl in site-\u2060config.js.';
    if (host === 'localhost' || host.endsWith('.localhost') || /^127\./.test(host) || host === '::1') {
        return `This link only works on this computer.${where}`;
    }
    const privateV4 = /^(10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(host);
    const privateV6 = /^(fe80:|fc|fd)/.test(host) && host.includes(':');
    const localName = /\.(local|lan|home|internal|home\.arpa)$/.test(host) ||
        (!host.includes('.') && !host.includes(':'));
    if (privateV4 || privateV6 || localName) {
        return `This link only works on your home network.${where}`;
    }
    return '';
}

async function copyOrShareLink(url, title, opener) {
    if (shareBusy) return;
    const hint = shareReachHint();
    if (linkShareSheetAvailable()) {
        shareBusy = true;
        try {
            await navigator.share({ url, title });
            if (hint) showNotice(hint);
            return;
        } catch (err) {
            // Closed without choosing: nothing to report.
            if (err && err.name === 'AbortError') return;
            // Anything else: fall through to copying.
            console.warn('Share sheet unavailable; copying instead.', err);
        } finally {
            shareBusy = false;
        }
    }
    if (clipboardAvailable()) {
        try {
            await navigator.clipboard.writeText(url);
            showNotice(hint ? `Link copied. ${hint}` : 'Link copied');
            return;
        } catch (err) {
            console.warn('Could not copy automatically; showing the link instead.', err);
        }
    }
    openLinkPopover(url, hint, opener);
}

function openLinkPopover(url, hint, opener) {
    if (!dom.linkPopover) {
        // Older cached page without the box: the browser's own prompt still
        // shows the link selected.
        window.prompt('Copy this link:', url);
        return;
    }
    hideNotice();
    linkPopoverOpener = opener || document.activeElement;
    dom.linkPopoverInput.value = url;
    dom.linkPopoverHint.textContent = hint;
    dom.linkPopoverHint.hidden = !hint;
    dom.linkPopoverCopy.textContent = 'Copy';
    dom.linkPopover.hidden = false;
    selectPopoverLink();
}

function selectPopoverLink() {
    const input = dom.linkPopoverInput;
    input.focus();
    input.select();
    // iOS Safari ignores select() on its own.
    try { input.setSelectionRange(0, input.value.length); } catch (_) { /* not supported */ }
    // Show the start of the link (selecting scrolls to its end).
    input.scrollLeft = 0;
}

function isLinkPopoverOpen() {
    return !!dom.linkPopover && !dom.linkPopover.hidden;
}

function closeLinkPopover(restoreFocus = true) {
    if (!isLinkPopoverOpen()) return;
    dom.linkPopover.hidden = true;
    const opener = linkPopoverOpener;
    linkPopoverOpener = null;
    if (restoreFocus && opener && typeof opener.focus === 'function' && document.contains(opener)) {
        opener.focus();
    }
}

function copyPopoverLink() {
    const input = dom.linkPopoverInput;
    // iOS Safari won't copy from a read-only box, so it's editable for the
    // moment of the copy only.
    input.readOnly = false;
    selectPopoverLink();
    let copied = false;
    try {
        // The older copy command works on plain-HTTP pages, where the
        // clipboard API isn't available.
        copied = document.execCommand('copy');
    } catch (_) {
        copied = false;
    }
    input.readOnly = true;
    if (copied) {
        dom.linkPopoverCopy.textContent = 'Copied';
    } else {
        // Leave the link selected for Ctrl+C / Cmd+C or long-press > Copy.
        dom.linkPopoverCopy.textContent = isTouchFirst() ? 'Long-press to copy' : 'Press Ctrl+C';
        if (/Mac|iPhone|iPad/.test(navigator.platform || '') && !isTouchFirst()) {
            dom.linkPopoverCopy.textContent = 'Press \u2318C';
        }
    }
}

function shareCurrentPhoto() {
    const item = currentImages[currentIndex];
    const node = currentPortfolio();
    if (!shareLinkAvailable || !item || !node || !node.id) return;
    copyOrShareLink(shareUrl(node.id, item.id), item.name || item.alternate_name || 'Photo', dom.shareLinkBtn);
}

function shareCurrentAlbum() {
    const node = currentPortfolio();
    if (!node || !node.id) return;
    copyOrShareLink(shareUrl(node.id, null), node.name || 'Album', dom.shareAlbumBtn);
}

// Shown once an album is on screen; hidden while there's nothing to share.
function updateShareAlbumButton(visible = true) {
    if (!dom.shareAlbumBtn) return;
    const node = currentPortfolio();
    dom.shareAlbumBtn.hidden = !(visible && node && node.id);
    if (dom.shareAlbumLabel) {
        dom.shareAlbumLabel.textContent = breadcrumbTrail.length <= 1 ? 'Share gallery' : 'Share album';
    }
    dom.shareAlbumBtn.setAttribute('aria-label', breadcrumbTrail.length <= 1 ? 'Share gallery link' : 'Share album link');
}

function configureShareButton() {
    const sheet = linkShareSheetAvailable();
    dom.shareLinkLabel.textContent = sheet ? 'Share link' : 'Copy link';
    dom.shareLinkBtn.setAttribute('aria-label', sheet ? 'Share link to this photo' : 'Copy link to this photo');
    dom.shareLinkBtn.title = sheet ? 'Share link' : 'Copy link (L)';
}

function initLinkPopover() {
    dom.linkPopoverCopy.addEventListener('click', copyPopoverLink);
    dom.linkPopoverClose.addEventListener('click', () => closeLinkPopover());
    dom.linkPopover.addEventListener('keydown', (e) => {
        // Keys typed here are for the box only (not the photo viewer or the
        // site menu underneath).
        e.stopPropagation();
        if (e.key === 'Escape') {
            e.preventDefault();
            closeLinkPopover();
        }
    });
    // Tapping or clicking anywhere else closes it.
    document.addEventListener('pointerdown', (e) => {
        if (isLinkPopoverOpen() && !dom.linkPopover.contains(e.target) &&
            !(linkPopoverOpener && linkPopoverOpener.contains && linkPopoverOpener.contains(e.target))) {
            closeLinkPopover(false);
        }
    });
}

function initSwipeGestures() {
    let touchStartX = 0;
    const SWIPE_THRESHOLD = 40;

    dom.lightbox.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].clientX;
    }, { passive: true });

    dom.lightbox.addEventListener('touchend', (e) => {
        const deltaX = e.changedTouches[0].clientX - touchStartX;
        if (Math.abs(deltaX) < SWIPE_THRESHOLD) return;
        navigateLightbox(deltaX > 0 ? -1 : 1);
    }, { passive: true });

    dom.lightbox.addEventListener('touchmove', (e) => {
        if (e.target === dom.lightbox) e.preventDefault();
    }, { passive: false });
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', () => {
    dom.breadcrumb = document.getElementById('breadcrumb');
    dom.folderGrid = document.getElementById('folderGrid');
    dom.status = document.getElementById('statusMessage');
    dom.grid = document.getElementById('galleryGrid');
    dom.lightbox = document.getElementById('lightbox');
    dom.lightboxImg = document.getElementById('lightboxImg');
    dom.lightboxCaption = document.getElementById('lightboxCaption');
    dom.prevBtn = document.getElementById('prevBtn');
    dom.nextBtn = document.getElementById('nextBtn');
    dom.closeBtn = document.getElementById('closeBtn');
    dom.downloadBtn = document.getElementById('downloadBtn');
    dom.downloadLabel = document.getElementById('downloadLabel');
    dom.lightboxNotice = document.getElementById('lightboxNotice');
    dom.pageNotice = document.getElementById('pageNotice');
    dom.shareLinkBtn = document.getElementById('shareLinkBtn');
    dom.shareLinkLabel = document.getElementById('shareLinkLabel');
    dom.shareAlbumBtn = document.getElementById('shareAlbumBtn');
    dom.shareAlbumLabel = document.getElementById('shareAlbumLabel');
    dom.linkPopover = document.getElementById('linkPopover');
    dom.linkPopoverInput = document.getElementById('linkPopoverInput');
    dom.linkPopoverCopy = document.getElementById('linkPopoverCopy');
    dom.linkPopoverHint = document.getElementById('linkPopoverHint');
    dom.linkPopoverClose = document.getElementById('linkPopoverClose');

    if (!dom.grid || !dom.lightbox) return;

    dom.lightbox.addEventListener('click', (e) => {
        if (e.target === dom.lightbox) closeLightbox();
    });

    dom.prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateLightbox(-1); });
    dom.nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateLightbox(1); });
    dom.closeBtn.addEventListener('click', () => closeLightbox());
    downloadAvailable = !!(dom.downloadBtn && dom.downloadLabel && dom.lightboxNotice);
    if (downloadAvailable) {
        dom.downloadBtn.addEventListener('click', (e) => { e.stopPropagation(); handleDownload(); });
        configureDownloadButton();
    } else {
        // Page markup is older than this script (see downloadAvailable).
        console.warn('Download button not found in the page; download is unavailable until the page is reloaded.');
    }

    // Share links. Like Download, each part switches itself off if an older
    // cached page doesn't have its markup.
    if (!(dom.linkPopover && dom.linkPopoverInput && dom.linkPopoverCopy &&
          dom.linkPopoverHint && dom.linkPopoverClose)) {
        dom.linkPopover = null;
    } else {
        initLinkPopover();
    }
    shareLinkAvailable = !!(dom.shareLinkBtn && dom.shareLinkLabel);
    if (shareLinkAvailable) {
        dom.shareLinkBtn.addEventListener('click', (e) => { e.stopPropagation(); shareCurrentPhoto(); });
        configureShareButton();
    } else {
        console.warn('Share link button not found in the page; photo links are unavailable until the page is reloaded.');
    }
    if (dom.shareAlbumBtn) {
        dom.shareAlbumBtn.addEventListener('click', shareCurrentAlbum);
    }

    initSwipeGestures();

    document.addEventListener('keydown', (e) => {
        if (!isLightboxOpen()) return;
        const plain = !e.ctrlKey && !e.metaKey && !e.altKey;
        if (e.key === 'ArrowLeft') navigateLightbox(-1);
        else if (e.key === 'ArrowRight') navigateLightbox(1);
        else if (e.key === 'Escape') closeLightbox();
        else if ((e.key === 'd' || e.key === 'D') && plain) {
            // Plain D only -- Ctrl/Cmd+D is the browser's bookmark shortcut.
            e.preventDefault();
            handleDownload();
        } else if ((e.key === 'l' || e.key === 'L') && plain) {
            // Plain L only -- Ctrl/Cmd+L moves to the address bar.
            e.preventDefault();
            shareCurrentPhoto();
        }
    });

    window.addEventListener('popstate', onHistoryNavigation);

    initGallery();
});
