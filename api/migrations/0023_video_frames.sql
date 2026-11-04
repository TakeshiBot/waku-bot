CREATE TABLE IF NOT EXISTS video_frames (
  image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
  object_key TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS video_frames_delete AFTER DELETE ON video_frames BEGIN
  INSERT OR IGNORE INTO object_deletions(object_key) VALUES (OLD.object_key);
END;
UPDATE api_schema SET version=23 WHERE id=1;
