ALTER TABLE analysis_sessions ADD COLUMN presentation TEXT NOT NULL DEFAULT 'batch' CHECK(presentation IN ('batch','inline'));
CREATE INDEX IF NOT EXISTS analysis_sessions_retry ON analysis_sessions(next_retry_at) WHERE phase='done' AND next_retry_at>0;
INSERT INTO api_schema VALUES(1,17) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
