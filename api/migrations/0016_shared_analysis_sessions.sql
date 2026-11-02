CREATE TABLE IF NOT EXISTS analysis_sessions(
id TEXT PRIMARY KEY,current_batch_id TEXT NOT NULL,created_at INTEGER NOT NULL,
phase TEXT NOT NULL DEFAULT 'active' CHECK(phase IN ('active','retry_wait','paused','done','stopped','cancelled')),
round INTEGER NOT NULL DEFAULT 1,max_rounds INTEGER NOT NULL DEFAULT 6,
next_retry_at INTEGER NOT NULL DEFAULT 0,enrolling_until INTEGER NOT NULL DEFAULT 0,
parent_id TEXT,stop_reason TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_analysis_session ON analysis_sessions((1))
WHERE phase IN ('active','retry_wait','paused');
CREATE INDEX IF NOT EXISTS analysis_sessions_recent ON analysis_sessions(created_at DESC,id DESC);
CREATE TABLE IF NOT EXISTS analysis_session_batches(
batch_id TEXT PRIMARY KEY REFERENCES analysis_batches(id) ON DELETE CASCADE,
session_id TEXT NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
round INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS session_batches_owner ON analysis_session_batches(session_id,round);
CREATE TABLE IF NOT EXISTS analysis_job_sessions(
image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
session_id TEXT NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE);
CREATE INDEX IF NOT EXISTS analysis_jobs_owner ON analysis_job_sessions(session_id,image_id);
CREATE TABLE IF NOT EXISTS analysis_session_providers(
session_id TEXT NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
provider_id TEXT NOT NULL REFERENCES ai_providers(id) ON DELETE CASCADE,
config_version INTEGER NOT NULL,failures INTEGER NOT NULL DEFAULT 0,
strikes INTEGER NOT NULL DEFAULT 0,cooldown_until INTEGER NOT NULL DEFAULT 0,
disabled INTEGER NOT NULL DEFAULT 0,last_error TEXT,
PRIMARY KEY(session_id,provider_id));
CREATE TABLE IF NOT EXISTS analysis_provider_events(
token TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES analysis_sessions(id) ON DELETE CASCADE,
provider_id TEXT NOT NULL,created_at INTEGER NOT NULL);
INSERT INTO api_schema VALUES(1,16) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
