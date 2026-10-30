-- The pending view usually contains a small fraction of the library.
CREATE INDEX IF NOT EXISTS catalog_classified_image ON image_catalog_state(classified,image_id);
INSERT INTO api_schema VALUES(1,13) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
