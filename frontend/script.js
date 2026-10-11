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
// Navigation model: a breadcrumb trail of portfolios the user has drilled
// into, starting at root. Clicking a folder tile pushes onto the trail and
// re-fetches; clicking a breadcrumb entry truncates the trail back to that
// point and re-fetches. Images in the *current* portfolio populate both the
// grid and the lightbox's navigable set.
// ---------------------------------------------------------------------------

// PROXY_BASE_URL is declared once in common.js (loaded before this file on
// index.html) so both the header/background logic and the gallery logic
// stay pointed at the same proxy without risking the two constants drifting.

// Ordered list of {id, name} the user has navigated through, root first.
let breadcrumbTrail = [];

// Images currently loaded for the active portfolio (drives the lightbox).
let currentImages = [];
let currentIndex = 0;
let lastFocusedThumbnail = null;

const dom = {};

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

async function fetchJSON(path) {
    const res = await fetch(`${PROXY_BASE_URL}${path}`);
    if (!res.ok) {
        const body = await res.text();
        throw new Error(`Request to ${path} failed (${res.status}): ${body}`);
    }
    return res.json();
}

async function fetchAllImages(portUuid) {
    // Walks pagination until has_next is false. Fine for personal-library
    // sized portfolios; page_size is already the proxy's max (200 per PhotoShare's
    // own server-side clamp).
    let page = 1;
    let all = [];
    while (true) {
        const data = await fetchJSON(`/gallery/portfolios/${portUuid}/images?page=${page}&page_size=200`);
        all = all.concat(data.items || []);
        if (!data.has_next) break;
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

async function loadPortfolio(portUuid, displayName) {
    setStatus('Loading\u2026', false);
    dom.folderGrid.innerHTML = '';
    dom.grid.innerHTML = '';

    try {
        const [children, images] = await Promise.all([
            fetchJSON(`/gallery/portfolios/${portUuid}/children`),
            fetchAllImages(portUuid),
        ]);

        renderFolders(children);

        currentImages = images;
        renderImageGrid(images);

        if (children.length === 0 && images.length === 0) {
            setStatus('This portfolio is empty.', false);
        } else {
            setStatus('', false);
        }
    } catch (err) {
        console.error(err);
        setStatus(
            `Could not load "${displayName}". Is the gallery proxy running at ${PROXY_BASE_URL}?`,
            true
        );
    }
}

async function enterPortfolio(portUuid, name) {
    breadcrumbTrail.push({ id: portUuid, name });
    renderBreadcrumb();
    await loadPortfolio(portUuid, name);
}

async function navigateToTrailIndex(index) {
    breadcrumbTrail = breadcrumbTrail.slice(0, index + 1);
    renderBreadcrumb();
    const node = breadcrumbTrail[breadcrumbTrail.length - 1];
    await loadPortfolio(node.id, node.name);
}

async function initGallery() {
    setStatus('Loading\u2026', false);
    try {
        const root = await fetchJSON('/gallery/portfolios/root');
        breadcrumbTrail = [{ id: root.id, name: root.name || 'Library' }];
        renderBreadcrumb();
        await loadPortfolio(root.id, root.name || 'Library');
    } catch (err) {
        console.error(err);
        setStatus(
            `Could not reach the gallery proxy at ${PROXY_BASE_URL}. Make sure it's running.`,
            true
        );
    }
}

// ---------------------------------------------------------------------------
// Lightbox (unchanged behavior from the static version: swipe, keyboard,
// focus management -- just re-pointed at currentImages/proxy URLs)
// ---------------------------------------------------------------------------

function openLightbox(index, triggerEl) {
    currentIndex = index;
    lastFocusedThumbnail = triggerEl || document.activeElement;
    updateLightboxContent();

    dom.lightbox.style.display = 'flex';
    document.body.style.overflow = 'hidden';
    dom.closeBtn.focus();
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

function closeLightbox() {
    dom.lightbox.style.display = 'none';
    document.body.style.overflow = '';
    releaseShareFile();
    hideNotice();

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

function detectShareMode() {
    if (typeof navigator.share !== 'function' || typeof navigator.canShare !== 'function') return false;
    if (typeof File !== 'function' || !window.matchMedia) return false;
    // Touch-first only: an iPad with a trackpad or a touchscreen laptop with a
    // mouse reports hover support and gets the normal file download.
    const touchFirst = window.matchMedia('(pointer: coarse)').matches &&
        !window.matchMedia('(hover: hover)').matches;
    if (!touchFirst) return false;
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

function showNotice(message, isError) {
    clearTimeout(noticeTimer);
    if (!dom.lightboxNotice) return;
    dom.lightboxNotice.textContent = message;
    dom.lightboxNotice.classList.toggle('is-error', !!isError);
    dom.lightboxNotice.classList.add('is-visible');
    noticeTimer = setTimeout(hideNotice, isError ? 4000 : 2500);
}

function hideNotice() {
    clearTimeout(noticeTimer);
    if (dom.lightboxNotice) dom.lightboxNotice.classList.remove('is-visible');
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

    if (!dom.grid || !dom.lightbox) return;

    dom.lightbox.addEventListener('click', (e) => {
        if (e.target === dom.lightbox) closeLightbox();
    });

    dom.prevBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateLightbox(-1); });
    dom.nextBtn.addEventListener('click', (e) => { e.stopPropagation(); navigateLightbox(1); });
    dom.closeBtn.addEventListener('click', closeLightbox);
    downloadAvailable = !!(dom.downloadBtn && dom.downloadLabel && dom.lightboxNotice);
    if (downloadAvailable) {
        dom.downloadBtn.addEventListener('click', (e) => { e.stopPropagation(); handleDownload(); });
        configureDownloadButton();
    } else {
        // Page markup is older than this script (see downloadAvailable).
        console.warn('Download button not found in the page; download is unavailable until the page is reloaded.');
    }

    initSwipeGestures();

    document.addEventListener('keydown', (e) => {
        if (!isLightboxOpen()) return;
        if (e.key === 'ArrowLeft') navigateLightbox(-1);
        else if (e.key === 'ArrowRight') navigateLightbox(1);
        else if (e.key === 'Escape') closeLightbox();
        else if ((e.key === 'd' || e.key === 'D') && !e.ctrlKey && !e.metaKey && !e.altKey) {
            // Plain D only -- Ctrl/Cmd+D is the browser's bookmark shortcut.
            e.preventDefault();
            handleDownload();
        }
    });

    initGallery();
});
