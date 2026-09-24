// ---------------------------------------------------------------------------
// PhotoShare Admin Frontend
// ---------------------------------------------------------------------------
// Talks only to the admin proxy (see ../photoshare-admin-proxy), never
// directly to the PhotoShare backend and never to the gallery's data-plane
// proxy. Deliberately a separate, standalone file from the gallery
// frontend's common.js/script.js -- this is the "admin plane," served from
// its own Apache2 path/origin, and should have zero shared code or shared
// runtime state with the public-facing gallery.
//
// Relative by default (same reasoning as the gallery's PROXY_BASE_URL): in
// production, Apache2 reverse-proxies /admin/* to the admin proxy container
// on the SAME origin as this page. Override to an absolute URL (e.g.
// 'http://127.0.0.1:8200') only for local dev without Apache2 in front.
const ADMIN_PROXY_BASE_URL = '';

const backendStatusEl = document.getElementById('backendStatus');

async function checkBackendHealth() {
    try {
        const res = await fetch(`${ADMIN_PROXY_BASE_URL}/admin/backend-health`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        backendStatusEl.textContent = `backend ok \u00b7 root ${data.root_id?.slice(0, 8) ?? '?'}\u2026`;
        backendStatusEl.className = 'badge ok';
    } catch (err) {
        backendStatusEl.textContent = 'backend unreachable';
        backendStatusEl.className = 'badge error';
    }
}

/**
 * POSTs a JSON body to one of this proxy's admin routes and renders the
 * result (or error) into the given <div class="result"> element.
 */
async function callAdminEndpoint(path, body, resultEl, button) {
    resultEl.classList.remove('show', 'success', 'error');
    resultEl.textContent = 'Running\u2026';
    resultEl.classList.add('show');
    button.disabled = true;

    try {
        const res = await fetch(`${ADMIN_PROXY_BASE_URL}${path}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) {
            resultEl.textContent = `Error (HTTP ${res.status}):\n${JSON.stringify(data, null, 2)}`;
            resultEl.classList.add('error');
            return;
        }
        resultEl.textContent = JSON.stringify(data, null, 2);
        resultEl.classList.add('success');
    } catch (err) {
        resultEl.textContent = `Could not reach the admin proxy at ${ADMIN_PROXY_BASE_URL || '(same origin)'}.\n${err}`;
        resultEl.classList.add('error');
    } finally {
        button.disabled = false;
    }
}

function wireForm(formId, resultId, path, buildBody) {
    const form = document.getElementById(formId);
    const resultEl = document.getElementById(resultId);
    form.addEventListener('submit', (event) => {
        event.preventDefault();
        const button = form.querySelector('button');
        callAdminEndpoint(path, buildBody(), resultEl, button);
    });
}

wireForm('rescanForm', 'rescanResult', '/admin/rescan', () => {
    const portUuid = document.getElementById('rescanPortUuid').value.trim();
    return {
        port_uuid: portUuid || null,
        recursive: document.getElementById('rescanRecursive').checked,
    };
});

wireForm('preheatForm', 'preheatResult', '/admin/preheat-thumbnails', () => {
    const portUuid = document.getElementById('preheatPortUuid').value.trim();
    return {
        port_uuid: portUuid || null,
        recursive: document.getElementById('preheatRecursive').checked,
        width: parseInt(document.getElementById('preheatWidth').value, 10),
        height: parseInt(document.getElementById('preheatHeight').value, 10),
    };
});

wireForm('cleanupForm', 'cleanupResult', '/admin/cleanup-thumbnails', () => ({}));

wireForm('rootForm', 'rootResult', '/admin/root', () => ({
    path: document.getElementById('rootPath').value.trim(),
}));

checkBackendHealth();
