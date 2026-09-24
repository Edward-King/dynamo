-- PhotoShare metadata.db schema
-- Verbatim from the project's SQL DDL Design document (§4.7a).
-- Applied idempotently at startup (CREATE TABLE IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS installation (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    root_id         TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS portfolios (
    id                TEXT PRIMARY KEY,
    rel_path          TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    virtual           INTEGER NOT NULL DEFAULT 0 CHECK (virtual IN (0, 1)),
    icon_image_id     TEXT,
    parent_id         TEXT,
    is_symlink        INTEGER NOT NULL DEFAULT 0 CHECK (is_symlink IN (0, 1)),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES portfolios(id) ON DELETE CASCADE,
    FOREIGN KEY (icon_image_id) REFERENCES images(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_portfolios_parent_id ON portfolios(parent_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolios_rel_path ON portfolios(rel_path);

-- Content-identity row: one per distinct Img_UUID (= uuid5(root_id,
-- content_hash)). Placement-independent; physical placements live in
-- image_locations (a file may appear in multiple portfolio directories).
CREATE TABLE IF NOT EXISTS images (
    id                  TEXT PRIMARY KEY,
    content_hash        TEXT NOT NULL,
    width               INTEGER NOT NULL,
    height              INTEGER NOT NULL,
    size_bytes          INTEGER NOT NULL,
    mime_type           TEXT NOT NULL,
    taken_at            TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_images_content_hash ON images(content_hash);

-- Physical placements of an image within portfolio directories. A single
-- content row (images.id) may have many rows here -- one per directory the
-- byte-identical file appears in -- so cross-portfolio duplicates no longer
-- collide on the images primary key.
CREATE TABLE IF NOT EXISTS image_locations (
    img_uuid            TEXT NOT NULL,
    port_uuid           TEXT NOT NULL,
    rel_path            TEXT NOT NULL,
    name                TEXT NOT NULL,
    is_symlink          INTEGER NOT NULL DEFAULT 0 CHECK (is_symlink IN (0, 1)),
    file_modified_at    TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    PRIMARY KEY (img_uuid, port_uuid, rel_path),
    FOREIGN KEY (img_uuid) REFERENCES images(id) ON DELETE CASCADE,
    FOREIGN KEY (port_uuid) REFERENCES portfolios(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_locations_port_uuid ON image_locations(port_uuid);
CREATE INDEX IF NOT EXISTS idx_image_locations_img_uuid ON image_locations(img_uuid);

CREATE TABLE IF NOT EXISTS portfolio_tags (
    port_uuid   TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (port_uuid, tag),
    FOREIGN KEY (port_uuid) REFERENCES portfolios(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_portfolio_tags_tag ON portfolio_tags(tag);

CREATE TABLE IF NOT EXISTS image_tags (
    img_uuid    TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (img_uuid, tag),
    FOREIGN KEY (img_uuid) REFERENCES images(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_image_tags_tag ON image_tags(tag);

CREATE TABLE IF NOT EXISTS virtual_membership (
    port_uuid       TEXT NOT NULL,
    img_uuid        TEXT NOT NULL,
    alternate_name  TEXT,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (port_uuid, img_uuid),
    FOREIGN KEY (port_uuid) REFERENCES portfolios(id) ON DELETE CASCADE,
    FOREIGN KEY (img_uuid) REFERENCES images(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_virtual_membership_img_uuid ON virtual_membership(img_uuid);

CREATE TABLE IF NOT EXISTS identity_registry (
    uuid                TEXT PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('portfolio', 'image')),
    normalized_rel_path TEXT NOT NULL,
    content_hash        TEXT,
    last_seen_at        TEXT NOT NULL,
    tombstoned_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_identity_registry_content_hash ON identity_registry(content_hash);
CREATE INDEX IF NOT EXISTS idx_identity_registry_path ON identity_registry(normalized_rel_path);

CREATE TABLE IF NOT EXISTS rescan_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    completed_at        TEXT NOT NULL,
    scope_port_uuid     TEXT,
    recursive           INTEGER NOT NULL CHECK (recursive IN (0, 1)),
    portfolios_added    INTEGER NOT NULL,
    portfolios_moved    INTEGER NOT NULL,
    portfolios_removed  INTEGER NOT NULL,
    images_added        INTEGER NOT NULL,
    images_moved        INTEGER NOT NULL,
    images_removed      INTEGER NOT NULL,
    errors_json         TEXT NOT NULL,
    duration_ms         INTEGER NOT NULL
);

-- Reference (v1) auth implementation -- see Auth System Hooks design doc.
-- Not part of the original §4.7 table list; added to support SqliteCredentialStore.
CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    key_hash      TEXT NOT NULL UNIQUE,
    label         TEXT NOT NULL,
    issued_at     TEXT NOT NULL,
    expires_at    TEXT,
    revoked_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys(key_hash);
