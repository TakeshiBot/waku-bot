-- Keep scheduling work proportional to runnable jobs, not the queue history.
CREATE INDEX IF NOT EXISTS analysis_ready ON image_analysis(next_attempt,image_id)
  WHERE status='pending' AND control='active' AND attempts=0;
CREATE INDEX IF NOT EXISTS analysis_expired ON image_analysis(lease_until) WHERE status='running';
CREATE INDEX IF NOT EXISTS analysis_started_pending ON image_analysis(attempts) WHERE status='pending' AND attempts>=1;
CREATE INDEX IF NOT EXISTS jobs_cleanup ON telegram_jobs(created_at) WHERE status IN ('done','failed','uncertain');
CREATE INDEX IF NOT EXISTS batches_cleanup ON analysis_batches(created_at);
CREATE INDEX IF NOT EXISTS images_rating_recent ON images(rating,created_at DESC,id DESC);

-- At most 30 counter rows replace repeated full-history progress scans.
CREATE TABLE IF NOT EXISTS analysis_status_counts (
  status TEXT NOT NULL, control TEXT NOT NULL, cancelled INTEGER NOT NULL,
  n INTEGER NOT NULL CHECK(n>=0), PRIMARY KEY(status,control,cancelled)
);
INSERT INTO analysis_status_counts(status,control,cancelled,n)
  SELECT status,control,CASE WHEN last_error='cancelled' THEN 1 ELSE 0 END,COUNT(*)
  FROM image_analysis GROUP BY status,control,CASE WHEN last_error='cancelled' THEN 1 ELSE 0 END
  ON CONFLICT(status,control,cancelled) DO NOTHING;
CREATE TRIGGER IF NOT EXISTS analysis_counts_insert AFTER INSERT ON image_analysis BEGIN
  INSERT INTO analysis_status_counts(status,control,cancelled,n)
    VALUES(NEW.status,NEW.control,CASE WHEN NEW.last_error='cancelled' THEN 1 ELSE 0 END,1)
    ON CONFLICT(status,control,cancelled) DO UPDATE SET n=n+1;
END;
CREATE TRIGGER IF NOT EXISTS analysis_counts_delete AFTER DELETE ON image_analysis BEGIN
  UPDATE analysis_status_counts SET n=n-1 WHERE status=OLD.status AND control=OLD.control
    AND cancelled=CASE WHEN OLD.last_error='cancelled' THEN 1 ELSE 0 END;
END;
CREATE TRIGGER IF NOT EXISTS analysis_counts_update AFTER UPDATE OF status,control,last_error ON image_analysis
WHEN NEW.status!=OLD.status OR NEW.control!=OLD.control
  OR (CASE WHEN NEW.last_error='cancelled' THEN 1 ELSE 0 END)!=(CASE WHEN OLD.last_error='cancelled' THEN 1 ELSE 0 END)
BEGIN
  UPDATE analysis_status_counts SET n=n-1 WHERE status=OLD.status AND control=OLD.control
    AND cancelled=CASE WHEN OLD.last_error='cancelled' THEN 1 ELSE 0 END;
  INSERT INTO analysis_status_counts(status,control,cancelled,n)
    VALUES(NEW.status,NEW.control,CASE WHEN NEW.last_error='cancelled' THEN 1 ELSE 0 END,1)
    ON CONFLICT(status,control,cancelled) DO UPDATE SET n=n+1;
END;
