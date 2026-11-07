-- 0005 · `v_temp_trace_gaps` — долг, а не «любая живая временная»
--
-- В 0004 вьюха выбирала все temp-строки без `purge_evidence`, поэтому живая временная сессия
-- (её ещё не удаляли — доказательства и быть не может) попадала в «долги». Это шум: такой
-- список перестаёт что-либо значить, а значит его перестают читать.
--
-- Долг — это когда удаление **объявлено** (`purged_at` заполнен), но след не проверен.
-- Живые временные, которые ждут sweep, показывает `--check` по `boot`, а не эта вьюха.

BEGIN;

DROP VIEW IF EXISTS v_temp_trace_gaps;

CREATE VIEW v_temp_trace_gaps AS
SELECT session_id, boot, purged_at
FROM session_registry
WHERE kind = 'temp' AND purged_at IS NOT NULL AND purge_evidence IS NULL;

INSERT OR REPLACE INTO schema_migrations (version, name)
VALUES (5, 'temp_trace_gaps_semantics');

COMMIT;
