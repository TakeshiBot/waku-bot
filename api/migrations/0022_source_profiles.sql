CREATE TABLE IF NOT EXISTS source_profiles (
  source_key TEXT PRIMARY KEY,
  identity_key TEXT NOT NULL,
  label TEXT NOT NULL,
  profile_url TEXT NOT NULL,
  next_check INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS source_profiles_identity ON source_profiles(identity_key);
INSERT INTO api_schema VALUES(1,22) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
