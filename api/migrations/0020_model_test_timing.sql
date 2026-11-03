CREATE TABLE IF NOT EXISTS ai_model_tests (
  model_id TEXT PRIMARY KEY REFERENCES ai_models(id) ON DELETE CASCADE,
  elapsed_ms INTEGER NOT NULL CHECK(elapsed_ms>=0)
);
INSERT INTO api_schema VALUES(1,20) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
