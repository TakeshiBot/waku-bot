ALTER TABLE image_analysis ADD COLUMN control TEXT NOT NULL DEFAULT 'active' CHECK(control IN ('active','paused','cancelled'));
ALTER TABLE analysis_batches ADD COLUMN control TEXT NOT NULL DEFAULT 'active' CHECK(control IN ('active','paused','cancelled'));
CREATE TABLE analysis_queue_settings (id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)));
INSERT INTO analysis_queue_settings(id) VALUES (1);
CREATE INDEX analysis_runnable ON image_analysis(control,status,next_attempt);
