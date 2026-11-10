-- 0007: claims — кто из сессий сейчас ведёт файл.
--
-- Зачем: несколько сессий работают в одном репозитории и затирают друг друга. Claim — это
-- «я это правлю», а не блокировка: файл остаётся доступен, но чужая живая заявка превращается
-- в понятный отказ с адресом, как её снять. Временная метка heartbeat решает, жив владелец:
-- умершая сессия не держит файл вечно.

CREATE TABLE IF NOT EXISTS session_claims (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT NOT NULL,                        -- нормализованный абсолютный путь
    session_id   TEXT NOT NULL,
    project_id   TEXT,
    note         TEXT,
    claimed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    heartbeat_at TEXT NOT NULL DEFAULT (datetime('now')),
    released_at  TEXT,
    UNIQUE(path, session_id)
);

CREATE INDEX IF NOT EXISTS idx_session_claims_path ON session_claims(path);
CREATE INDEX IF NOT EXISTS idx_session_claims_session ON session_claims(session_id);

-- Живые заявки: освобождённые не мешают никому.
CREATE VIEW IF NOT EXISTS v_live_claims AS
SELECT path, session_id, project_id, note, claimed_at, heartbeat_at
FROM session_claims
WHERE released_at IS NULL;
