ALTER TABLE images ADD COLUMN metadata_version INTEGER NOT NULL DEFAULT 0;
CREATE TABLE image_analysis (
  image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','failed','skipped')),
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_until INTEGER NOT NULL DEFAULT 0,
  next_attempt INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  chat_id INTEGER,
  message_id INTEGER,
  photo INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX analysis_due ON image_analysis(status,next_attempt,lease_until);
