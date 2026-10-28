CREATE TABLE IF NOT EXISTS images (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  object_key TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  size INTEGER NOT NULL CHECK(size > 0),
  title TEXT NOT NULL DEFAULT '',
  source_url TEXT,
  rating TEXT NOT NULL DEFAULT 'unknown' CHECK(rating IN ('safe','r18','unknown')),
  uploader_id TEXT,
  random_key REAL NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS images_random ON images(random_key, rating);
CREATE INDEX IF NOT EXISTS images_recent ON images(created_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS image_tags (
  image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  PRIMARY KEY(image_id, tag)
);
CREATE INDEX IF NOT EXISTS tags_lookup ON image_tags(tag, image_id);
CREATE TABLE IF NOT EXISTS telegram_jobs (
  id TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','done','failed','uncertain')),
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_until INTEGER NOT NULL DEFAULT 0,
  next_attempt INTEGER NOT NULL DEFAULT 0,
  image_id TEXT,
  notified INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON telegram_jobs(status, next_attempt, lease_until);
CREATE TABLE IF NOT EXISTS object_deletions (object_key TEXT PRIMARY KEY);
