-- ============================================================================
-- HermesUI — схема расширяющей БД (hermesui.db)
-- СТАТУС: СОЗДАНА 2026-11-07 скриптом `hermesui/scripts/db_init.py` (M0.3 закрыт).
--         Проверено на живой базе: 11 таблиц, 3 представления, 2 триггера,
--         integrity_check=ok, foreign_key_check чист, audit_log append-only.
-- Расположение: $HERMES_HOME/hermesui.db   ($HERMES_HOME = C:\Users\<user>\AppData\Local\hermes)
-- Репозиторий: F:\06_AI\hermes-agent\hermesui\db\schema.sql
-- Версия схемы: 1 (baseline). Полное состояние = этот файл + `db/migrations/*.sql`,
--   накатывается автоматически: `python hermesui/tools/hermesui.py doctor --init-db`
--   Текущая версия: см. MAX(version) в schema_migrations.
-- Назначение: каталог модулей, учёт установленных артефактов, журнал изменений,
--             аудит, индекс заметок vault. НЕ дублирует данные Hermes (state.db и др.).
-- Применение: sqlite3 hermesui.db < db/schema.sql   (или hermesui db upgrade)
-- ============================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

-- ---------------------------------------------------------------------------
-- 0. Миграции
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- 1. Каталог модулей (зеркало modules/*/module.yaml на момент последнего apply)
--    type: skill | plugin | ui | theme | i18n | mcp
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS modules (
    id            TEXT PRIMARY KEY,                       -- kebab-case, == имя папки
    type          TEXT NOT NULL CHECK (type IN ('skill','plugin','ui','theme','i18n','mcp')),
    version       TEXT NOT NULL,                          -- semver из module.yaml
    description   TEXT,
    repo_path     TEXT NOT NULL,                          -- modules/<id>
    install_path  TEXT,                                   -- фактический путь в $HERMES_HOME
    enabled       INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0,1)),
    capabilities  TEXT,                                   -- JSON array объявленных capability
    declared_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_modules_type    ON modules(type);
CREATE INDEX IF NOT EXISTS idx_modules_enabled ON modules(enabled);

-- ---------------------------------------------------------------------------
-- 2. Граф зависимостей и конфликтов
--    kind: requires | conflicts | enhances
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS module_deps (
    module_id   TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    depends_on  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'requires'
                CHECK (kind IN ('requires','conflicts','enhances')),
    min_version TEXT,
    PRIMARY KEY (module_id, depends_on, kind)
);

-- ---------------------------------------------------------------------------
-- 3. Артефакты: каждый файл, который мы положили в $HERMES_HOME
--    kind: desktop | python | skill | skin | locale | mcp_json | patch
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artifacts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module_id     TEXT NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('desktop','python','skill','skin','locale','mcp_json','patch')),
    rel_path      TEXT NOT NULL,                          -- путь относительно $HERMES_HOME
    sha256        TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    backup_path   TEXT,                                   -- где лежит предыдущая версия
    installed_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (module_id, rel_path)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_module ON artifacts(module_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_sha    ON artifacts(sha256);

-- ---------------------------------------------------------------------------
-- 4. Применённые изменения (журнал установщика; основа rollback)
--    action: install | update | remove | config | enable | disable
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applied_changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id    TEXT NOT NULL UNIQUE,                    -- ULID/hex, стабильный id для rollback
    module_id    TEXT REFERENCES modules(id) ON DELETE SET NULL,
    action       TEXT NOT NULL CHECK (action IN ('install','update','remove','config','enable','disable')),
    revision     INTEGER NOT NULL DEFAULT 0,              -- растёт монотонно на каждый apply
    before_json  TEXT,                                    -- состояние до (файлы/ключи)
    after_json   TEXT,                                    -- состояние после
    applied_by   TEXT NOT NULL DEFAULT 'installer'
                 CHECK (applied_by IN ('installer','user','agent')),
    applied_at   TEXT NOT NULL DEFAULT (datetime('now')),
    result       TEXT NOT NULL DEFAULT 'ok' CHECK (result IN ('ok','failed','rolled_back')),
    error        TEXT
);

CREATE INDEX IF NOT EXISTS idx_changes_time   ON applied_changes(applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_changes_module ON applied_changes(module_id);
CREATE INDEX IF NOT EXISTS idx_changes_result ON applied_changes(result);

-- ---------------------------------------------------------------------------
-- 5. Записи в config.yaml (только ключи, значения — JSON-скаляры)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS config_writes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id  TEXT REFERENCES applied_changes(change_id) ON DELETE SET NULL,
    key        TEXT NOT NULL,                             -- напр. display.skin, plugins.enabled
    old_value  TEXT,
    new_value  TEXT,
    written_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_config_writes_key ON config_writes(key, written_at DESC);

-- ---------------------------------------------------------------------------
-- 6. Пресеты тем (наши скины; сам YAML живёт в $HERMES_HOME/skins/)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS theme_presets (
    id          TEXT PRIMARY KEY,                          -- имя скина
    label       TEXT NOT NULL,
    module_id   TEXT REFERENCES modules(id) ON DELETE SET NULL,
    tokens_json TEXT NOT NULL,                             -- палитра (ключи скина)
    is_active   INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_theme_one_active
    ON theme_presets(is_active) WHERE is_active = 1;

-- ---------------------------------------------------------------------------
-- 7. Индекс заметок vault (связь docs/*.md ↔ заметки)
--    status: active | draft | deprecated
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS vault_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path    TEXT NOT NULL UNIQUE,                      -- относительно корня vault
    title       TEXT NOT NULL,
    tags        TEXT,                                      -- "hermesui/architecture,hermesui/ui"
    status      TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','draft','deprecated')),
    doc_source  TEXT,                                      -- docs/01-SPECIFICATION.md и т.п.
    doc_hash    TEXT,                                      -- sha256 источника на момент синхронизации
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    synced_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_vault_status ON vault_notes(status);

-- ---------------------------------------------------------------------------
-- 8. Аудит (append-only: UPDATE/DELETE запрещены триггерами ниже)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL CHECK (actor IN ('user','agent','installer','cron')),
    action      TEXT NOT NULL,
    module_id   TEXT,
    change_id   TEXT,
    result      TEXT NOT NULL DEFAULT 'ok' CHECK (result IN ('ok','failed','denied')),
    detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts     ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_module ON audit_log(module_id);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- ---------------------------------------------------------------------------
-- 9. Настройки установщика (ожидаемая версия платформы, пути и т.д.)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- 10. Прогоны doctor (история здоровья)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS doctor_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at  TEXT,
    summary      TEXT NOT NULL DEFAULT 'unknown'
                 CHECK (summary IN ('ok','warn','fail','unknown')),
    report_json  TEXT
);

-- ---------------------------------------------------------------------------
-- 11. Представления
-- ---------------------------------------------------------------------------
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

CREATE VIEW IF NOT EXISTS v_recent_changes AS
SELECT c.change_id,
       c.applied_at,
       c.action,
       c.module_id,
       c.result,
       json_extract(c.after_json, '$.version') AS version
FROM applied_changes c
ORDER BY c.id DESC;

CREATE VIEW IF NOT EXISTS v_module_conflicts AS
SELECT module_id, depends_on AS conflicts_with
FROM module_deps
WHERE kind = 'conflicts';

-- ---------------------------------------------------------------------------
-- 12. Сиды
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_migrations (version, name) VALUES (1, 'initial');

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('platform.version',            '0.22.5'),
    ('platform.release',            '2026.9.24'),
    ('platform.state_db_schema',    '32'),
    ('installer.backup_retention',  '10'),
    ('vault.path',                  'F:\06_AI\HermesUI\Obsidian\HermesUI'),
    ('ui.default_skin',             'hermesui');
