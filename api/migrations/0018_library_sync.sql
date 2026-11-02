CREATE TABLE IF NOT EXISTS object_uploads (
  object_key TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS object_uploads_expiry ON object_uploads(expires_at);

-- One latest change per image, including deleted images. No catalog scan and
-- no growing event history are needed to synchronize an expanded gallery.
CREATE TABLE IF NOT EXISTS library_changes (
  image_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS library_changes_revision ON library_changes(revision);
CREATE TRIGGER IF NOT EXISTS library_sync_insert AFTER INSERT ON images BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(NEW.id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
CREATE TRIGGER IF NOT EXISTS library_sync_update AFTER UPDATE OF title,source_url,rating,image_type,metadata_version ON images BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(NEW.id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
CREATE TRIGGER IF NOT EXISTS library_sync_delete AFTER DELETE ON images BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(OLD.id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
CREATE TRIGGER IF NOT EXISTS library_sync_analysis_insert AFTER INSERT ON image_analysis BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(NEW.image_id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
CREATE TRIGGER IF NOT EXISTS library_sync_analysis_update AFTER UPDATE OF status,last_error ON image_analysis
WHEN OLD.status IS NOT NEW.status OR OLD.last_error IS NOT NEW.last_error BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(NEW.image_id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
CREATE TRIGGER IF NOT EXISTS library_sync_analysis_delete AFTER DELETE ON image_analysis BEGIN
  UPDATE library_revision SET version=version+1 WHERE id=1;
  INSERT INTO library_changes VALUES(OLD.image_id,(SELECT version FROM library_revision WHERE id=1))
    ON CONFLICT(image_id) DO UPDATE SET revision=excluded.revision;
END;
INSERT INTO api_schema VALUES(1,18) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
