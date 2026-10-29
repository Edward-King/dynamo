# Gallery frontend: manual regression checklist

The proxy has an automated suite (`proxy/test_main.py`, run with `pytest`).
The frontend doesn't, and the parts below depend on real browsers and
devices (share sheets, the Photos app, download folders), so they're
checked by hand. Run the full list when the lightbox, `common.js`,
`script.js`, or `style.css` changes; run only the Download section for
download-only changes, only the Page background section for
background-only changes, and only the Share links and Link previews
sections for share-only changes.

Before starting: proxy running, `PROXY_BASE_URL` in `common.js` pointing at
it, and at least one portfolio with two or more photos (one with a
non-ASCII name such as `Café.jpg` is useful).

For the Page background section, also set up:

- **Cover**: a portfolio whose `.photoshare_meta.json` sets `icon_dir`
  to a photo that isn't its first one alphabetically (so you can tell the
  two apart)
- **Plain**: a portfolio with photos but no `icon_dir`
- **Empty A**: a portfolio with no photos whose only content is
  **Empty B**, a portfolio inside it that also has no photos
- **Twin**: a portfolio inside Cover whose `icon_dir` points at Cover's
  icon image (a path relative to the library root, e.g. `Cover/sunset.jpg`)

Run a rescan after editing metadata. The background is dim on purpose, so
compare it against the folder tile icons, or open the full image in
DevTools (Elements > `body` > `--page-bg-image`).

## Download / Save

| # | Device / browser | Steps | Expected |
|---|---|---|---|
| D1 | Desktop Chrome or Edge (Windows or macOS) | Open a photo, click **Download** | Saved to Downloads with its real name; gallery stays open; "Download started" appears briefly |
| D2 | Desktop Firefox | Same as D1 | Same as D1 (Firefox may show its own download panel) |
| D3 | Desktop Safari (macOS) | Same as D1 | Same as D1 |
| D4 | Any desktop browser | Open a photo, press **D**; then press Ctrl+D / Cmd+D | D downloads the current photo; Ctrl/Cmd+D opens the browser's bookmark dialog instead |
| D5 | Any desktop browser | Navigate to a photo with a non-ASCII name and download it | Saved with the exact name (accents intact) |
| D6 | iPhone Safari | Open a photo; button shows a share icon, no text. Tap it | Share sheet opens; **Save Image** puts it in Photos |
| D7 | iPhone Safari | Open a photo, swipe to the next one, tap Save | Share sheet has the photo now on screen, not the first one |
| D8 | iPhone Safari | Tap Save, then close the share sheet without choosing | No error message |
| D9 | iPad Safari (touch only) | Same as D6 | Same as D6. With a trackpad/Magic Keyboard attached, the button reads **Download** and behaves like desktop (expected) |
| D10 | Android Chrome | Open a photo, tap the button | Share sheet opens (includes saving/Downloads options) |
| D11 | Android Firefox | Open a photo, tap the button | Direct download to Downloads (Firefox Android has no file sharing; expected) |
| D12 | Any device | Tap/click the button twice quickly | Only one download / one share sheet |
| D13 | Any desktop browser | Open a photo, stop PhotoShare (leave the proxy running), click **Download** | No file is saved, and the gallery stays on screen with the lightbox open (it must not switch to an error page) |
| D14 | Any desktop browser | Download the same photo twice, a few seconds apart | Two downloads (the second isn't silently ignored) |
| D15 | iPad Safari with a trackpad or Magic Keyboard attached (button reads **Download**) | Open a photo, tap **Download** | Safari offers the file as a download (saved to Files), and the gallery stays on screen. This is the same download path iPhone falls back to if the share sheet can't open |

## Page background

| # | Check | Expected |
|---|---|---|
| B1 | Load the gallery | Background is the root portfolio's icon image (or its first photo if root has no `icon_dir`) |
| B2 | Open **Cover** | Background changes to Cover's `icon_dir` photo, not its first photo |
| B3 | Open **Plain** | Background changes to Plain's first photo |
| B4 | Open **Empty A**, then **Empty B** | Empty A: background changes to the gray gradient. Empty B: gradient stays and "This portfolio is empty." shows as before (Empty A doesn't show it, because it contains Empty B) |
| B5 | From a nested portfolio, click a breadcrumb back to root, then one level down | Each step shows that portfolio's background; root's is restored at root |
| B6 | Open **About Me**, then go back to the gallery | About shows root's background; the gallery shows root's again |
| B7 | DevTools Network tab: reload the gallery (plain address, no `?p=`) and filter for `portfolios` | One request to `/gallery/portfolios/root` (the gallery's own) plus one to `/gallery/portfolios/<root id>` (the background). There's no second `/gallery/portfolios/root` request. An album link (`?p=`) has a different pattern; see L9 |
| B8 | Stop the proxy, reload the gallery; then reload About | Gallery shows the gradient and the "Could not reach the gallery proxy" message; About shows the gradient and its normal content |
| B9 | With the gallery open, stop PhotoShare (leave the proxy running) and open a folder | Gradient background and the "Could not load" message; nothing else breaks. Start PhotoShare again and click a breadcrumb: backgrounds work again |
| B10 | DevTools: throttle to **Slow 4G**. Open Cover, and before its background appears, click the root breadcrumb, then open Plain | Background ends on Plain's photo and stays there. It never flips to Cover's photo afterwards |

### Fast navigation (folders and images)

Use DevTools throttling (**Slow 4G**) so requests take long enough to
overlap. Pick portfolios with different contents so you can tell them apart.

| # | Check | Expected |
|---|---|---|
| N1 | Open Cover, and before its folders and photos appear, click the root breadcrumb | Root's folders and photos show and stay. Cover's never appear afterwards |
| N2 | From root, open Plain, then quickly click the root breadcrumb and open Cover | Only Cover's folders and photos show at the end; the breadcrumb ends at root / Cover |
| N3 | Open a large portfolio (more than 200 photos, so its photos load in pages), then click the root breadcrumb right away | Root's contents show. Network tab: requests for later pages of the large portfolio stop (at most one more page is requested) |
| N4 | After N2, open a photo in the lightbox and use the arrow keys | The lightbox steps through Cover's photos, not Plain's |
| N5 | Open Empty A, and before "This portfolio is empty." appears, click the root breadcrumb | Root's contents show; the empty message doesn't appear over them |

### Smooth switching (preload)

| # | Check | Expected |
|---|---|---|
| P1 | DevTools: leave **Disable cache** off, throttle to **Slow 4G**, and empty the cache (right-click reload > **Empty Cache and Hard Reload**). Open Cover | Root's background stays visible while Cover's image downloads, then Cover's replaces it in one step. No blank, gradient, or half-drawn frame in between |
| P2 | Same as P1, Network tab, filter for `full` | Cover's full image is downloaded once (the preload). Any second entry for it is served from memory or disk cache, not downloaded again |
| P3 | DevTools Network request blocking: add the pattern `*/full`. Open Plain | Background changes to the gradient (not stuck on the old image, not blank); folder icons and photo thumbnails still load. Remove the block afterwards |
| P4 | Cache on, no throttling. Visit Cover, go back to root, open Cover again | Second visit switches almost instantly; Network shows the image from memory or disk cache |
| P5 | Open a portfolio whose icon is a very large photo (20+ megapixels), on a slower device such as a phone | The background switches in one step without a visible stall or a partly drawn image, and scrolling stays smooth while it switches |

### Unchanged background (no needless swaps)

| # | Check | Expected |
|---|---|---|
| U1 | Open Cover, then open **Twin** inside it | Background doesn't flicker; Network shows no new `/full` request for Twin |
| U2 | Click the Cover breadcrumb from Twin | Same as U1: no flicker, no new `/full` request |
| U3 | Open **Empty A**, then **Empty B** inside it | Gradient stays put with no flicker |
| U4 | DevTools Elements: select `body`, open Empty A, then Empty B | The `style` attribute on `body` isn't rewritten (DevTools doesn't flash it) on the second step |

## Share links

Use an album at least two folders deep (e.g. `Travel/Japan/Kyoto`) with
three or more photos. "Secure" means an `https://` address or `localhost`;
`http://gallery.local` and LAN addresses are not.

| # | Where | Steps | Expected |
|---|-------|-------|----------|
| L1 | Desktop Chrome, Firefox and Safari, secure address | Open a photo, click **Copy link**; paste into a new tab | "Link copied" appears. The new tab opens the same photo in the lightbox, with the album behind it |
| L2 | Any desktop browser | In the lightbox press **L**; then press Ctrl+L / Cmd+L | L copies the link (same as L1); Ctrl/Cmd+L moves to the address bar instead |
| L3 | Any desktop browser | Click **Share album** at the right of the breadcrumb bar; paste into a new tab | The album opens (no photo). At the root the button reads **Share gallery** |
| L4 | Any desktop browser | Open folders, then use Back and Forward | Each step shows the previous/next album with the right breadcrumb; the address shows `?p=` (nothing at the root) |
| L5 | Any desktop browser | Open a photo, step with the arrow keys, close it, press Back | The address gains `&i=` when the photo opens and follows the arrows. Closing removes it. Back then leaves the album (it doesn't reopen the photo) |
| L6 | Android Chrome | Open a photo, press the system Back button | The lightbox closes; the gallery stays on the album |
| L7 | iPhone Safari and Android Chrome, secure address | Open a photo, tap the link button (icon only) | The share sheet opens with the link. Closing it without choosing shows no error |
| L8 | Desktop browser at `http://gallery.local` (or a LAN IP) | Click **Copy link** | A box shows the link already selected, a **Copy** button and "This link only works on your home network" (or "this computer" on `localhost`). **Copy** reads "Copied" and the link pastes. Escape or clicking elsewhere closes the box; the first Escape doesn't close the lightbox |
| L9 | Any desktop browser, DevTools Network tab | Paste an album link from L3 into a new tab | Breadcrumb: **Library** (clickable) / folder names (plain text) / album. Network: one `/gallery/portfolios/root`, two `/gallery/portfolios/<album id>` (gallery and background), no request for the root's own folders or photos. The root's background never shows first |
| L10 | Any desktop browser | Change a link's album ID by one character and open it | "This album isn't available any more." with **Go to the Library**, which works |
| L11 | Any desktop browser | Change a link's photo ID (`&i=`) and open it | The album opens with "This photo isn't available any more."; the address loses `&i=` |
| L12 | iPhone Safari at `http://` LAN address | Tap the link button, then **Copy** | The box opens with the link selected and the keyboard doesn't stay up; **Copy** reads "Copied" (or "Long-press to copy" with the link still selected) |
| L13 | Server: set `shareBaseUrl: 'https://photos.example.net'` in `site-config.js`, re-run `stamp_assets.py` | Reload, copy a link | The link starts with `https://photos.example.net/gallery/share?`; no "only works on" note. Set an invalid value (e.g. `ftp://x`): links fall back to the page's address and the console shows one warning |
| L14 | Any desktop browser that last loaded the site before this change | Visit once | The gallery works (an older cached page with the newer script must not break it) |

## Link previews

The preview page is `/gallery/share` in `data-proxy` (unit-tested in
`proxy/test_main.py`). These checks need real apps. Previews built on
the app's own servers need a public address; on a desktop/LAN setup only
the sender-side apps (iMessage, WhatsApp, Signal) can show them. Preheat
800x600 thumbnails first (admin UI, **Preheat thumbnails**, width `800`,
height `600`) so the first preview isn't slow.

| # | Where | Steps | Expected |
|---|-------|-------|----------|
| V1 | iMessage (iPhone or Mac) | Send a photo link | A card with the photo, its name and "Photo from <album>"; tapping it opens the photo |
| V2 | WhatsApp | Send an album link | A card with the album's cover, its name and "Album · N photos" |
| V3 | Slack and Discord (public address only) | Paste a photo link | A large-image card with the photo |
| V4 | Any browser | Open a link with a mistyped album ID | The proxy's "This album isn't available any more." page with **Open the gallery** |
| V5 | Server | `curl -s "<link>" \| grep og:` | `og:title`, `og:description`, `og:url`, `og:image` (with `https://` on a TLS site), plus `og:image:width`/`height` for photo links |
| V6 | Any browser | Open a shared link, then press Back | You return to where you clicked the link (the preview page isn't left in history) |

## Caching after deploys

Run on the real server, through Apache (not a local static server). Replace
`gallery.example.com` with the real hostname.

| # | Check | Expected |
|---|---|---|
| C1 | `curl -sI https://gallery.example.com/ \| grep -i cache-control` | `Cache-Control: no-cache` |
| C2 | View the page source; check the `style.css`, `site-config.js`, `common.js` and `script.js` links. Then `curl -sI` one of those URLs exactly as written | Each link ends in `?v=` plus 12 characters (0-9, a-f); the response has `Cache-Control: public, max-age=31536000, immutable` |
| C3 | Desktop browser, DevTools Network tab with **Disable cache** off: load the gallery, open About, return to the gallery | The page itself shows **304**; CSS/JS show "(memory cache)" or "(disk cache)" |
| C4 | Change `script.js` on the server, re-run `stamp_assets.py`, then just click a link back to the gallery (no hard refresh) | Only `script.js` is fetched, with a new `?v=` tag; the change is live |
| C5 | With a browser that last loaded the site before this change, visit once | The gallery loads normally (an older cached page with a newer script must not leave a blank gallery) |

## Must still work (existing behavior)

| # | Check | Expected |
|---|---|---|
| R1 | Right-click the lightbox photo on desktop | Browser menu with "Save Image As..." |
| R2 | Long-press the lightbox photo on iPhone / Android | System menu with "Save to Photos" / "Download image" |
| R3 | Arrow keys, Escape, backdrop click, swipe | Navigate / close as before |
| R4 | Tab through the lightbox with the keyboard | Copy link and Download buttons get a visible focus ring; Enter/Space activates them |
| R5 | Phone layout (portrait) | Copy link, Download and close buttons don't overlap; all easy to tap |
| R6 | Page background and folder icons | Still load (the full-image responses gained a `Vary: Origin` header) |
| R7 | Open a photo in the lightbox, then Save/Download it (D1, D6) in a portfolio that uses that same photo as its background | Works as before. The background and the lightbox share the full-image URL, one loaded without CORS and one with |
