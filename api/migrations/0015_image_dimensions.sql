-- Layout metadata is independent of AI/classification and does not rewrite images.
CREATE TABLE IF NOT EXISTS image_dimensions(
image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
width INTEGER NOT NULL CHECK(width>0),height INTEGER NOT NULL CHECK(height>0));
INSERT INTO api_schema VALUES(1,15) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
