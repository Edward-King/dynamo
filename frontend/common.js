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

/**
 * Fetches the root portfolio from the proxy and applies its icon image
 * (full-resolution, since this is a large background rather than a small
 * tile) as a heavily-dimmed page background. Falls back to a neutral
 * gradient if there's no root icon or the proxy can't be reached --
 * background-image issues should never block the rest of the page from
 * working.
 */
async function applyRootBackground() {
    try {
        const res = await fetch(`${PROXY_BASE_URL}/gallery/portfolios/root`);
        if (!res.ok) throw new Error(`root portfolio request failed (${res.status})`);
        const root = await res.json();

        if (root.icon_image_id) {
            const bgUrl = `${PROXY_BASE_URL}/gallery/image/${root.icon_image_id}/full`;
            document.body.style.setProperty('--page-bg-image', `url("${bgUrl}")`);
            document.body.classList.add('has-bg-image');
            return;
        }
    } catch (err) {
        console.warn('Could not load root portfolio icon for background, using fallback.', err);
    }

    document.body.style.setProperty('--page-bg-image', FALLBACK_BACKGROUND_CSS);
    document.body.classList.add('has-bg-image');
}

document.addEventListener('DOMContentLoaded', () => {
    const activePage = document.body.dataset.page || 'gallery';
    renderSiteHeader(activePage);
    applyRootBackground();
});
