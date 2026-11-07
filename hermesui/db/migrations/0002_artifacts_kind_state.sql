-- 0002 · artifacts.kind: добавляем 'state' (файлы состояния модуля: groups.json, policy.json)
--
-- Причина: `modules/ui-dispatcher/module.yaml` объявляет артефакт `kind: state`, а CHECK в
-- schema.sql знал только desktop|python|skill|skin|locale|mcp_json|patch. SQLite не умеет
-- менять CHECK на месте — пересобираем таблицу. Данные сохраняются.
--
-- Применяется автоматически: `python hermesui/tools/hermesui.py doctor --init-db`.

PRAGMA foreign_keys = OFF;

BEGIN;

DROP VIEW IF EXISTS v_installed_modules;

CREATE TABLE artifacts_new (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id     TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('desktop','python','skill','skin','locale',
                                               'mcp_json','patch','state')),
    rel_path      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    backup_path   TEXT,
    installed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (module_id, rel_path)
);

INSERT INTO artifacts_new (id, module_id, kind, rel_path, sha256, size_bytes, backup_path, installed_at)
SELECT id, module_id, kind, rel_path, sha256, size_bytes, backup_path, installed_at FROM artifacts;

DROP TABLE artifacts;

ALTER TABLE artifacts_new RENAME TO artifacts;

CREATE INDEX IF NOT EXISTS idx_artifacts_module ON artifacts(module_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha    ON artifacts(sha256);

CREATE VIEW IF NOT EXISTS v_installed_modules AS
SELECT m.id,
       m.type,
       m.version,
       m.enabled,
       m.install_path,
       (SELECT COUNT(*) FROM artifacts a WHERE a.module_id = m.id) AS artifact_count,
       (SELECT MAX(c.applied_at) FROM applied_changes c
         WHERE c.module_id = m.id AND c.result = 'ok')              AS last_applied_at
FROM modules m;

COMMIT;

PRAGMA foreign_keys = ON;
