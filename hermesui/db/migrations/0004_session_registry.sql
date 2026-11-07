-- 0004 · реестр типов сессий (этап 3: project / temporary)
--
-- Зачем: у ядра нет понятия «одноразовая сессия». Временные сессии до сих пор жили только
-- в `plugins/project-map-ui/ephemeral.json` — это оперативный список для фронтенда, из него
-- нельзя ни ответить «сколько таких было», ни доказать, что после закрытия приложения от
-- сессии не осталось следа. Экспорт project-сессии в vault тоже негде записать.
--
-- Поэтому тип и происхождение сессии живут в нашей БД, а `ephemeral.json` остаётся
-- тем, чем был: кэшем фронтенда. Единственный источник — здесь.
--
-- Применяется автоматически: `python hermesui/tools/hermesui.py doctor --init-db`.

BEGIN;

CREATE TABLE IF NOT EXISTS session_registry (
    session_id        TEXT PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('project', 'temp')),
    project_id        TEXT,
    cwd               TEXT,
    parent_session_id TEXT,
    boot              TEXT,                    -- id инстанса приложения, создавшего временную сессию
    title             TEXT,
    created_at        INTEGER NOT NULL,        -- epoch, когда сессию создали
    registered_at     TEXT NOT NULL DEFAULT (datetime('now')),
    exported_path     TEXT,                    -- куда лёг markdown (для project-сессий)
    exported_at       TEXT,
    purged_at         TEXT,                    -- когда временную удалили
    purge_evidence    TEXT                     -- JSON: проверка «следа не осталось»
);

CREATE INDEX IF NOT EXISTS idx_session_registry_kind ON session_registry (kind, purged_at);
CREATE INDEX IF NOT EXISTS idx_session_registry_project ON session_registry (project_id);

-- Временные, которые ещё живы (по ним нужен sweep при следующем запуске)
CREATE VIEW IF NOT EXISTS v_temp_sessions AS
SELECT session_id, boot, project_id, cwd, created_at
FROM session_registry
WHERE kind = 'temp' AND purged_at IS NULL;

-- Временная сессия, по которой нет доказательства удаления: либо sweep не дошёл, либо
-- доказательство потерялось. Наличие строки здесь — это долг, а не норма.
CREATE VIEW IF NOT EXISTS v_temp_trace_gaps AS
SELECT session_id, boot, purged_at
FROM session_registry
WHERE kind = 'temp' AND purge_evidence IS NULL;

-- Экспортированные сессии по проектам (что уже ушло в vault)
CREATE VIEW IF NOT EXISTS v_session_exports AS
SELECT session_id, project_id, title, exported_path, exported_at
FROM session_registry
WHERE exported_path IS NOT NULL
ORDER BY exported_at DESC;

INSERT OR REPLACE INTO schema_migrations (version, name)
VALUES (4, 'session_registry');

COMMIT;
