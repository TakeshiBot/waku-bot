CREATE INDEX IF NOT EXISTS images_source_recent ON images(source_url,created_at DESC,id DESC,mime_type);
INSERT INTO api_schema VALUES(1,21) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
