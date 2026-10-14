// ---------------------------------------------------------------------------
// Shared header/navigation/background logic used by both index.html (the
// gallery) and about.html (the About Me page). Keeping this in one file
// means the header markup, menu behavior, and background-image logic can't
// drift between the two pages.
// ---------------------------------------------------------------------------

// Relative by default so the same file works unchanged in local dev (served
// alongside the proxy under a Live Server / http.server origin that Apache2
// isn't involved in yet) and in production, where Apache2 reverse-proxies
// /gallery/* on the SAME origin as this page (see the data-plane vhost
// config). Every call site below concatenates this with a path that already
// starts with "/gallery/...", so an empty string here simply produces a
// same-origin relative URL like "/gallery/portfolios/root" -- no other code
// needs to change. Override only if the proxy is ever served from a
// different origin than the frontend (e.g. local dev without an Apache2/
// reverse-proxy in front) by setting this back to an absolute URL such as
// 'http://127.0.0.1:8100'.
const PROXY_BASE_URL = '';

// A pleasing neutral fallback used whenever the root portfolio has no icon
// image set (e.g. a brand-new or empty library). Kept as a CSS gradient
// rather than a flat color so the page doesn't look broken/unstyled even
// with zero photos.
const FALLBACK_BACKGROUND_CSS =
    'linear-gradient(135deg, #3a3f47 0%, #23262b 100%)';

/**
 * Builds the header markup (title + hamburger menu) and injects it at the
 * top of <body>. `activePage` controls which menu item is marked current
 * for accessibility/styling ('gallery' | 'about').
 */
function renderSiteHeader(activePage) {
    const header = document.createElement('header');
    header.className = 'site-header';
    header.innerHTML = `
        <div class="site-header-bar">
            <a class="site-title" href="index.html">My Dynamic Gallery</a>
            <button id="menuToggle" class="menu-toggle" aria-expanded="false" aria-controls="siteMenu" aria-label="Open menu">
                <span class="menu-toggle-bar"></span>
                <span class="menu-toggle-bar"></span>
                <span class="menu-toggle-bar"></span>
            </button>
        </div>
        <nav id="siteMenu" class="site-menu" aria-label="Site menu" data-open="false">
            <a href="index.html" class="site-menu-link" ${activePage === 'gallery' ? 'aria-current="page"' : ''}>Gallery</a>
            <a href="about.html" class="site-menu-link" ${activePage === 'about' ? 'aria-current="page"' : ''}>About Me</a>
        </nav>
    `;
    document.body.insertBefore(header, document.body.firstChild);

    const menuToggle = header.querySelector('#menuToggle');
    const siteMenu = header.querySelector('#siteMenu');

    const closeMenu = () => {
        siteMenu.dataset.open = 'false';
        menuToggle.setAttribute('aria-expanded', 'false');
    };
    const openMenu = () => {
        siteMenu.dataset.open = 'true';
        menuToggle.setAttribute('aria-expanded', 'true');
    };

    menuToggle.addEventListener('click', () => {
        const isOpen = siteMenu.dataset.open === 'true';
        if (isOpen) closeMenu(); else openMenu();
    });

    // Close the menu on outside click, Escape, or navigating away -- so it
    // never gets left open (which would otherwise sit on top of the page,
    // similar in spirit to the earlier "stuck overlay" issue).
    document.addEventListener('click', (e) => {
        if (siteMenu.dataset.open === 'true' && !header.contains(e.target)) {
            closeMenu();
        }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && siteMenu.dataset.open === 'true') {
            closeMenu();
            menuToggle.focus();
        }
    });
}

// The --page-bg-image value currently on the page, so a request that would
// set the same value again (e.g. two portfolios sharing one icon, or two
// empty portfolios) leaves the page alone instead of re-downloading and
// re-applying it.
let currentBackgroundValue = null;

// Bumped by every background request. A request only applies its result if
// it's still the newest one, so when the user clicks quickly through
// portfolios a slow, older response can't overwrite a newer background.
let backgroundRequestSeq = 0;

function setPageBackground(value) {
    if (value === currentBackgroundValue) return;
    currentBackgroundValue = value;
    document.body.style.setProperty('--page-bg-image', value);
    document.body.classList.add('has-bg-image');
}

/**
 * Downloads an image off-screen and resolves once it's ready, so the page
 * can keep showing the old background until the new one can appear in one
 * step (no blank or half-loaded frame in between). The request is made the
 * same way the CSS background makes it (no CORS), so the browser reuses
 * this download from cache when the background is applied.
 */
function preloadImage(url) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve();
        img.onerror = () => reject(new Error(`could not load ${url}`));
        img.src = url;
    });
}

/**
 * Fetches a portfolio from the proxy and applies its icon image
 * (full-resolution, since this is a large background rather than a small
 * tile) as a heavily-dimmed page background. `portfolioRef` is a portfolio
 * ID, or 'root'. The icon is whatever PhotoShare resolved for the
 * portfolio: its icon_dir image, otherwise its first image. Falls back to
 * a neutral gradient if the portfolio has no icon (e.g. it's empty), or if
 * the proxy or the image can't be reached -- background-image issues
 * should never block the rest of the page from working. Never throws.
 */
async function applyPortfolioBackground(portfolioRef) {
    const seq = ++backgroundRequestSeq;
    let value = FALLBACK_BACKGROUND_CSS;

    try {
        const res = await fetch(`${PROXY_BASE_URL}/gallery/portfolios/${encodeURIComponent(portfolioRef)}`);
        if (!res.ok) throw new Error(`portfolio request failed (${res.status})`);
        const portfolio = await res.json();

        if (portfolio.icon_image_id) {
            const bgUrl = `${PROXY_BASE_URL}/gallery/image/${portfolio.icon_image_id}/full`;
            const cssValue = `url("${bgUrl}")`;
            // Already showing this image: nothing to download.
            if (cssValue !== currentBackgroundValue) {
                await preloadImage(bgUrl);
            }
            value = cssValue;
        }
    } catch (err) {
        console.warn('Could not load portfolio icon for background, using fallback.', err);
    }

    if (seq === backgroundRequestSeq) setPageBackground(value);
}

/**
 * Applies the fallback gradient directly, for when the page can't even
 * find out which portfolio to show (e.g. the proxy is down). Also cancels
 * any background request still in flight.
 */
function applyFallbackBackground() {
    backgroundRequestSeq += 1;
    setPageBackground(FALLBACK_BACKGROUND_CSS);
}

/** The About page (and any page without a gallery) uses the root's icon. */
function applyRootBackground() {
    return applyPortfolioBackground('root');
}

document.addEventListener('DOMContentLoaded', () => {
    const activePage = document.body.dataset.page || 'gallery';
    renderSiteHeader(activePage);
    // On the gallery, script.js sets the background for whichever
    // portfolio is open (starting with root). Applying the root background
    // here too would race it and could overwrite a sub-portfolio's image.
    if (activePage !== 'gallery') applyRootBackground();
});
