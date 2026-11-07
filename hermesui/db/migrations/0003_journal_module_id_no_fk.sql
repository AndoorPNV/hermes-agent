-- 0003 · applied_changes.module_id: убираем внешний ключ, история должна переживать откат
--
-- Причина: `module_id TEXT REFERENCES modules(id) ON DELETE SET NULL`. Откат удаляет строку
-- `modules` (иначе `plan` считает модуль установленным при пустом $HERMES_HOME), и FK обнулял
-- `module_id` в журнале — терялась связь «кто откачен». Журнал неизменяемый: ссылка на модуль
-- хранится как текст, без FK.
--
-- Представления `v_recent_changes` и `v_installed_modules` зависят от таблицы — снимаем
-- и создаём заново.

PRAGMA foreign_keys = OFF;

BEGIN;

DROP VIEW IF EXISTS v_recent_changes;
DROP VIEW IF EXISTS v_installed_modules;

CREATE TABLE applied_changes_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id    TEXT NOT NULL UNIQUE,
    module_id    TEXT,
    action       TEXT NOT NULL CHECK (action IN ('install','update','remove','config','enable','disable')),
    revision     INTEGER NOT NULL DEFAULT 0,
    before_json  TEXT,
    after_json   TEXT,
    applied_by   TEXT NOT NULL DEFAULT 'installer'
                 CHECK (applied_by IN ('installer','user','agent')),
    applied_at   TEXT NOT NULL DEFAULT (datetime('now')),
    result       TEXT NOT NULL DEFAULT 'ok' CHECK (result IN ('ok','failed','rolled_back')),
    error        TEXT
);

INSERT INTO applied_changes_new (id, change_id, module_id, action, revision, before_json, after_json,
                                 applied_by, applied_at, result, error)
SELECT id, change_id, module_id, action, revision, before_json, after_json,
       applied_by, applied_at, result, error
FROM applied_changes;

DROP TABLE applied_changes;

ALTER TABLE applied_changes_new RENAME TO applied_changes;

CREATE INDEX IF NOT EXISTS idx_changes_time   ON applied_changes(applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_changes_module ON applied_changes(module_id);
CREATE INDEX IF NOT EXISTS idx_changes_result ON applied_changes(result);

CREATE VIEW IF NOT EXISTS v_recent_changes AS
SELECT c.change_id,
       c.applied_at,
       c.action,
       c.module_id,
       c.result,
       json_extract(c.after_json, '$.version') AS version
FROM applied_changes c
ORDER BY c.id DESC;

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
