# Gallery frontend: manual regression checklist

The proxy has an automated suite (`proxy/test_main.py`, run with `pytest`).
The frontend doesn't, and the parts below depend on real browsers and
devices (share sheets, the Photos app, download folders), so they're
checked by hand. Run the full list when the lightbox, `common.js`,
`script.js`, or `style.css` changes; run only the Download section for
download-only changes, and only the Page background section for
background-only changes.

Before starting: proxy running, `PROXY_BASE_URL` in `common.js` pointing at
it, and at least one portfolio with two or more photos (one with a
non-ASCII name such as `Café.jpg` is useful).

For the Page background section, also set up:

- **Cover**: a portfolio whose `.photoshare_meta.json` sets `icon_dir`
  to a photo that isn't its first one alphabetically (so you can tell the
  two apart)
- **Plain**: a portfolio with photos but no `icon_dir`
- **Empty A** and **Empty B**: two portfolios with no photos (they can be
  side by side or one inside the other)
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
| B4 | Open **Empty A** | Background changes to the gray gradient; "This portfolio is empty." shows as before |
| B5 | From a nested portfolio, click a breadcrumb back to root, then one level down | Each step shows that portfolio's background; root's is restored at root |
| B6 | Open **About Me**, then go back to the gallery | About shows root's background; the gallery shows root's again |
| B7 | DevTools Network tab: reload the gallery and filter for `portfolios` | One request to `/gallery/portfolios/root` (the gallery's own) plus one to `/gallery/portfolios/<root id>` (the background). There's no second `/gallery/portfolios/root` request |
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

### Unchanged background (no needless swaps)

| # | Check | Expected |
|---|---|---|
| U1 | Open Cover, then open **Twin** inside it | Background doesn't flicker; Network shows no new `/full` request for Twin |
| U2 | Click the Cover breadcrumb from Twin | Same as U1: no flicker, no new `/full` request |
| U3 | Open **Empty A**, then **Empty B** (or the reverse) | Gradient stays put with no flicker |
| U4 | DevTools Elements: select `body`, open Empty A, then Empty B | The `style` attribute on `body` isn't rewritten (DevTools doesn't flash it) on the second step |

## Caching after deploys

Run on the real server, through Apache (not a local static server). Replace
`gallery.example.com` with the real hostname.

| # | Check | Expected |
|---|---|---|
| C1 | `curl -sI https://gallery.example.com/ \| grep -i cache-control` | `Cache-Control: no-cache` |
| C2 | View the page source; check the `style.css`, `common.js` and `script.js` links. Then `curl -sI` one of those URLs exactly as written | Each link ends in `?v=` plus 12 characters (0-9, a-f); the response has `Cache-Control: public, max-age=31536000, immutable` |
| C3 | Desktop browser, DevTools Network tab with **Disable cache** off: load the gallery, open About, return to the gallery | The page itself shows **304**; CSS/JS show "(memory cache)" or "(disk cache)" |
| C4 | Change `script.js` on the server, re-run `stamp_assets.py`, then just click a link back to the gallery (no hard refresh) | Only `script.js` is fetched, with a new `?v=` tag; the change is live |
| C5 | With a browser that last loaded the site before this change, visit once | The gallery loads normally (an older cached page with a newer script must not leave a blank gallery) |

## Must still work (existing behavior)

| # | Check | Expected |
|---|---|---|
| R1 | Right-click the lightbox photo on desktop | Browser menu with "Save Image As..." |
| R2 | Long-press the lightbox photo on iPhone / Android | System menu with "Save to Photos" / "Download image" |
| R3 | Arrow keys, Escape, backdrop click, swipe | Navigate / close as before |
| R4 | Tab through the lightbox with the keyboard | Download button gets a visible focus ring; Enter/Space activates it |
| R5 | Phone layout (portrait) | Download and close buttons don't overlap; both easy to tap |
| R6 | Page background and folder icons | Still load (the full-image responses gained a `Vary: Origin` header) |
| R7 | Open a photo in the lightbox, then Save/Download it (D1, D6) in a portfolio that uses that same photo as its background | Works as before. The background and the lightbox share the full-image URL, one loaded without CORS and one with |
