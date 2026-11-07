-- 0006 · журнал save-loop: что сохранили, когда и с каким содержимым
--
-- Зачем: «сохрани сессию» до сих пор ритуал — агент помнит, человек помнит, а система не знает.
-- Без журнала нельзя ни доказать, что сохранение было, ни поймать повтор (заметка-дубль), ни
-- проверить, что новая сессия прочитает ровно то, что записано.
--
-- status: running → ok | failed. Строка `running` без `finished_at` — это прерванное сохранение:
-- его видно в отчёте, а не «наверное, не было».

BEGIN;

CREATE TABLE IF NOT EXISTS save_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    note_path   TEXT,
    content_hash TEXT,                       -- sha256 тела заметки (без frontmatter)
    status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'verify_failed')),
    sections    INTEGER NOT NULL DEFAULT 0,  -- сколько разделов записали
    started_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    error       TEXT,
    detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_save_runs_session ON save_runs (session_id, started_at DESC);

-- Прерванные сохранения: запись начата, финала нет.
CREATE VIEW IF NOT EXISTS v_save_interrupted AS
SELECT id, session_id, note_path, started_at
FROM save_runs
WHERE status = 'running' AND finished_at IS NULL;

-- Последнее сохранение по каждой сессии — то, что «видит» следующая сессия.
CREATE VIEW IF NOT EXISTS v_last_save AS
SELECT r.*
FROM save_runs r
JOIN (SELECT session_id, MAX(started_at) AS ts FROM save_runs WHERE status = 'ok' GROUP BY session_id) m
  ON m.session_id = r.session_id AND m.ts = r.started_at
WHERE r.status = 'ok';

INSERT OR REPLACE INTO schema_migrations (version, name)
VALUES (6, 'save_runs');

COMMIT;
