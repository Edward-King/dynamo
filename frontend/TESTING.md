# Gallery frontend: manual regression checklist

The proxy has an automated suite (`proxy/test_main.py`, run with `pytest`).
The frontend doesn't, and the parts below depend on real browsers and
devices (share sheets, the Photos app, download folders), so they're
checked by hand. Run the full list when the lightbox, `script.js`, or
`style.css` changes; run only the Download section for download-only
changes.

Before starting: proxy running, `PROXY_BASE_URL` in `common.js` pointing at
it, and at least one portfolio with two or more photos (one with a
non-ASCII name such as `Café.jpg` is useful).

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

## Must still work (existing behavior)

| # | Check | Expected |
|---|---|---|
| R1 | Right-click the lightbox photo on desktop | Browser menu with "Save Image As..." |
| R2 | Long-press the lightbox photo on iPhone / Android | System menu with "Save to Photos" / "Download image" |
| R3 | Arrow keys, Escape, backdrop click, swipe | Navigate / close as before |
| R4 | Tab through the lightbox with the keyboard | Download button gets a visible focus ring; Enter/Space activates it |
| R5 | Phone layout (portrait) | Download and close buttons don't overlap; both easy to tap |
| R6 | Page background and folder icons | Still load (the full-image responses gained a `Vary: Origin` header) |
