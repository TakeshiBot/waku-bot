CREATE TABLE analysis_batches (
  id TEXT PRIMARY KEY,
  created_at INTEGER NOT NULL
);
CREATE TABLE analysis_batch_items (
  batch_id TEXT NOT NULL REFERENCES analysis_batches(id) ON DELETE CASCADE,
  image_id TEXT NOT NULL REFERENCES images(id) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK(status IN ('pending','running','done','failed','skipped')),
  PRIMARY KEY(batch_id,image_id)
);
CREATE INDEX analysis_items_image ON analysis_batch_items(image_id,status);
