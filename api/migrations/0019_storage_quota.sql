-- Account for actual R2 objects and in-flight writes, not just catalog rows.
CREATE TABLE IF NOT EXISTS storage_usage (
  id INTEGER PRIMARY KEY CHECK(id=1),
  bytes INTEGER NOT NULL DEFAULT 0 CHECK(bytes>=0),
  ready INTEGER NOT NULL DEFAULT 0,
  init_token TEXT,
  init_until INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO storage_usage(id) VALUES(1);
CREATE TABLE IF NOT EXISTS storage_objects (
  object_key TEXT PRIMARY KEY,
  size INTEGER NOT NULL CHECK(size>=0)
);
CREATE TRIGGER IF NOT EXISTS storage_quota_limit BEFORE INSERT ON storage_objects
WHEN (SELECT ready=1 AND bytes+NEW.size>10000000000 FROM storage_usage WHERE id=1)
BEGIN SELECT RAISE(ABORT,'storage_full'); END;
CREATE TRIGGER IF NOT EXISTS storage_quota_insert AFTER INSERT ON storage_objects BEGIN
  UPDATE storage_usage SET bytes=bytes+NEW.size WHERE id=1;
END;
CREATE TRIGGER IF NOT EXISTS storage_quota_delete AFTER DELETE ON storage_objects BEGIN
  UPDATE storage_usage SET bytes=bytes-OLD.size WHERE id=1;
END;
INSERT INTO api_schema VALUES(1,19) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
