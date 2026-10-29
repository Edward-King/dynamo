// ---------------------------------------------------------------------------
// Settings for this server. Edit this file on the server; the deployment
// steps copy it only when it isn't there yet, so your edits survive
// upgrades.
// ---------------------------------------------------------------------------

window.PHOTOSHARE_SITE_CONFIG = {
    // The address other people use to reach this gallery, used at the start
    // of every share link, e.g. 'https://photos.example.com'. A path is fine
    // if the gallery lives under one ('https://example.com/photos').
    //
    // Leave it empty to use whatever address is in the browser's address
    // bar. That's right for a gallery on the public internet. For a gallery
    // opened as http://gallery.local, localhost or a home-network address,
    // links made that way only work on this computer or your home network
    // (the share box says so); set this to a public address that reaches
    // the same gallery, if you have one, to share outside.
    shareBaseUrl: '',
};
