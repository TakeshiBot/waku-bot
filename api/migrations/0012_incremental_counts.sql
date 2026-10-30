-- Counters are updated atomically with their source records on both D1 and libSQL.
CREATE TABLE IF NOT EXISTS image_catalog_state(
image_id TEXT PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,
rating TEXT NOT NULL,size INTEGER NOT NULL,classified INTEGER NOT NULL CHECK(classified IN (0,1)));
INSERT OR IGNORE INTO image_catalog_state SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i;
CREATE TABLE IF NOT EXISTS library_counts(id INTEGER PRIMARY KEY CHECK(id=1),
total INTEGER NOT NULL DEFAULT 0 CHECK(total>=0),
bytes INTEGER NOT NULL DEFAULT 0 CHECK(bytes>=0),
safe INTEGER NOT NULL DEFAULT 0 CHECK(safe>=0),
r18 INTEGER NOT NULL DEFAULT 0 CHECK(r18>=0),
unknown INTEGER NOT NULL DEFAULT 0 CHECK(unknown>=0),
classified_safe INTEGER NOT NULL DEFAULT 0 CHECK(classified_safe>=0),
classified_r18 INTEGER NOT NULL DEFAULT 0 CHECK(classified_r18>=0),
pending INTEGER NOT NULL DEFAULT 0 CHECK(pending>=0));
INSERT INTO library_counts(id,total,bytes,safe,r18,unknown,classified_safe,classified_r18,pending)
SELECT 1,COUNT(*),COALESCE(SUM(s.size),0),COALESCE(SUM((s.rating='safe')),0),COALESCE(SUM((s.rating='r18')),0),COALESCE(SUM((s.rating='unknown')),0),COALESCE(SUM((s.rating='safe' AND s.classified)),0),COALESCE(SUM((s.rating='r18' AND s.classified)),0),COALESCE(SUM((NOT s.classified)),0) FROM image_catalog_state s WHERE true
ON CONFLICT(id) DO UPDATE SET total=excluded.total,bytes=excluded.bytes,safe=excluded.safe,r18=excluded.r18,unknown=excluded.unknown,classified_safe=excluded.classified_safe,classified_r18=excluded.classified_r18,pending=excluded.pending;
CREATE TRIGGER IF NOT EXISTS catalog_counts_insert AFTER INSERT ON image_catalog_state BEGIN UPDATE library_counts SET total=total+1,
bytes=bytes+NEW.size,
safe=safe+(NEW.rating='safe'),
r18=r18+(NEW.rating='r18'),
unknown=unknown+(NEW.rating='unknown'),
classified_safe=classified_safe+(NEW.rating='safe' AND NEW.classified),
classified_r18=classified_r18+(NEW.rating='r18' AND NEW.classified),
pending=pending+(NOT NEW.classified) WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS catalog_counts_update AFTER UPDATE ON image_catalog_state BEGIN UPDATE library_counts SET total=total-1+1,
bytes=bytes-OLD.size+NEW.size,
safe=safe-(OLD.rating='safe')+(NEW.rating='safe'),
r18=r18-(OLD.rating='r18')+(NEW.rating='r18'),
unknown=unknown-(OLD.rating='unknown')+(NEW.rating='unknown'),
classified_safe=classified_safe-(OLD.rating='safe' AND OLD.classified)+(NEW.rating='safe' AND NEW.classified),
classified_r18=classified_r18-(OLD.rating='r18' AND OLD.classified)+(NEW.rating='r18' AND NEW.classified),
pending=pending-(NOT OLD.classified)+(NOT NEW.classified) WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS catalog_counts_delete AFTER DELETE ON image_catalog_state BEGIN UPDATE library_counts SET total=total-1,
bytes=bytes-OLD.size,
safe=safe-(OLD.rating='safe'),
r18=r18-(OLD.rating='r18'),
unknown=unknown-(OLD.rating='unknown'),
classified_safe=classified_safe-(OLD.rating='safe' AND OLD.classified),
classified_r18=classified_r18-(OLD.rating='r18' AND OLD.classified),
pending=pending-(NOT OLD.classified) WHERE id=1; END;
CREATE TRIGGER IF NOT EXISTS catalog_image_insert AFTER INSERT ON images BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_image_update AFTER UPDATE OF title,rating,image_type,size ON images
WHEN NEW.title!=OLD.title OR NEW.rating!=OLD.rating OR NEW.image_type IS NOT OLD.image_type OR NEW.size!=OLD.size
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_insert AFTER INSERT ON image_tags
WHEN length(trim(NEW.tag))>0 AND NOT EXISTS(SELECT 1 FROM image_tags WHERE image_id=NEW.image_id AND tag!=NEW.tag AND length(trim(tag))>0)
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_delete AFTER DELETE ON image_tags
WHEN length(trim(OLD.tag))>0 AND NOT EXISTS(SELECT 1 FROM image_tags WHERE image_id=OLD.image_id AND length(trim(tag))>0)
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=OLD.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_update AFTER UPDATE OF tag,image_id ON image_tags
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=OLD.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_analysis_insert AFTER INSERT ON image_analysis BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_analysis_delete AFTER DELETE ON image_analysis BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=OLD.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_analysis_update AFTER UPDATE OF status ON image_analysis WHEN NEW.status!=OLD.status
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
AND NOT EXISTS(SELECT 1 FROM image_analysis a WHERE a.image_id=i.id AND a.status IN ('pending','running'))) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TABLE IF NOT EXISTS tag_totals(tag TEXT PRIMARY KEY,n INTEGER NOT NULL CHECK(n>=0));
INSERT INTO tag_totals SELECT tag,COUNT(*) FROM image_tags GROUP BY tag
ON CONFLICT(tag) DO UPDATE SET n=excluded.n;
CREATE TRIGGER IF NOT EXISTS tag_totals_insert AFTER INSERT ON image_tags BEGIN
INSERT INTO tag_totals(tag,n) VALUES(NEW.tag,1) ON CONFLICT(tag) DO UPDATE SET n=n+1; END;
CREATE TRIGGER IF NOT EXISTS tag_totals_delete AFTER DELETE ON image_tags BEGIN
UPDATE tag_totals SET n=n-1 WHERE tag=OLD.tag; END;
CREATE TRIGGER IF NOT EXISTS tag_totals_update AFTER UPDATE OF tag ON image_tags WHEN NEW.tag!=OLD.tag BEGIN
UPDATE tag_totals SET n=n-1 WHERE tag=OLD.tag;
INSERT INTO tag_totals(tag,n) VALUES(NEW.tag,1) ON CONFLICT(tag) DO UPDATE SET n=n+1; END;
CREATE TABLE IF NOT EXISTS analysis_batch_counts(batch_id TEXT PRIMARY KEY REFERENCES analysis_batches(id) ON DELETE CASCADE,
total INTEGER NOT NULL DEFAULT 0 CHECK(total>=0),
pending INTEGER NOT NULL DEFAULT 0 CHECK(pending>=0),
running INTEGER NOT NULL DEFAULT 0 CHECK(running>=0),
done INTEGER NOT NULL DEFAULT 0 CHECK(done>=0),
failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0),
skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped>=0),
cancelled INTEGER NOT NULL DEFAULT 0 CHECK(cancelled>=0),
paused INTEGER NOT NULL DEFAULT 0 CHECK(paused>=0));
INSERT INTO analysis_batch_counts(batch_id,total,pending,running,done,failed,skipped,cancelled,paused)
SELECT g.id,COUNT(b.image_id),COALESCE(SUM(b.status='pending'),0),COALESCE(SUM(b.status='running'),0),
COALESCE(SUM(b.status='done'),0),COALESCE(SUM(b.status='failed'),0),
COALESCE(SUM(b.status='skipped' AND COALESCE(a.last_error,'')!='cancelled'),0),
COALESCE(SUM(b.status='skipped' AND a.last_error='cancelled'),0),
COALESCE(SUM(b.status='pending' AND a.control='paused'),0)
FROM analysis_batches g LEFT JOIN analysis_batch_items b ON b.batch_id=g.id LEFT JOIN image_analysis a ON a.image_id=b.image_id GROUP BY g.id
ON CONFLICT(batch_id) DO UPDATE SET total=excluded.total,pending=excluded.pending,running=excluded.running,done=excluded.done,failed=excluded.failed,skipped=excluded.skipped,cancelled=excluded.cancelled,paused=excluded.paused;
CREATE TRIGGER IF NOT EXISTS batch_counts_create AFTER INSERT ON analysis_batches BEGIN
INSERT INTO analysis_batch_counts(batch_id) VALUES(NEW.id); END;
CREATE TRIGGER IF NOT EXISTS batch_counts_insert AFTER INSERT ON analysis_batch_items

BEGIN UPDATE analysis_batch_counts SET total=total+1,
pending=pending+(NEW.status='pending'),
running=running+(NEW.status='running'),
done=done+(NEW.status='done'),
failed=failed+(NEW.status='failed'),
skipped=skipped+(NEW.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=NEW.image_id),'')!='cancelled'),
cancelled=cancelled+(NEW.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=NEW.image_id),'')='cancelled'),
paused=paused+(NEW.status='pending' AND COALESCE((SELECT control FROM image_analysis WHERE image_id=NEW.image_id),'')='paused') WHERE batch_id=NEW.batch_id; END;
CREATE TRIGGER IF NOT EXISTS batch_counts_update AFTER UPDATE OF status ON analysis_batch_items
WHEN NEW.status!=OLD.status
BEGIN UPDATE analysis_batch_counts SET total=total-1+1,
pending=pending-(OLD.status='pending')+(NEW.status='pending'),
running=running-(OLD.status='running')+(NEW.status='running'),
done=done-(OLD.status='done')+(NEW.status='done'),
failed=failed-(OLD.status='failed')+(NEW.status='failed'),
skipped=skipped-(OLD.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=OLD.image_id),'')!='cancelled')+(NEW.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=NEW.image_id),'')!='cancelled'),
cancelled=cancelled-(OLD.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=OLD.image_id),'')='cancelled')+(NEW.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=NEW.image_id),'')='cancelled'),
paused=paused-(OLD.status='pending' AND COALESCE((SELECT control FROM image_analysis WHERE image_id=OLD.image_id),'')='paused')+(NEW.status='pending' AND COALESCE((SELECT control FROM image_analysis WHERE image_id=NEW.image_id),'')='paused') WHERE batch_id=NEW.batch_id; END;
CREATE TRIGGER IF NOT EXISTS batch_counts_delete AFTER DELETE ON analysis_batch_items

BEGIN UPDATE analysis_batch_counts SET total=total-1,
pending=pending-(OLD.status='pending'),
running=running-(OLD.status='running'),
done=done-(OLD.status='done'),
failed=failed-(OLD.status='failed'),
skipped=skipped-(OLD.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=OLD.image_id),'')!='cancelled'),
cancelled=cancelled-(OLD.status='skipped' AND COALESCE((SELECT last_error FROM image_analysis WHERE image_id=OLD.image_id),'')='cancelled'),
paused=paused-(OLD.status='pending' AND COALESCE((SELECT control FROM image_analysis WHERE image_id=OLD.image_id),'')='paused') WHERE batch_id=OLD.batch_id; END;
CREATE TRIGGER IF NOT EXISTS batch_analysis_insert AFTER INSERT ON image_analysis

BEGIN UPDATE analysis_batch_counts SET
cancelled=cancelled+COALESCE((SELECT SUM((b.status='skipped')*((COALESCE(NEW.last_error,'')='cancelled')-0)) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0),
skipped=skipped-COALESCE((SELECT SUM((b.status='skipped')*((COALESCE(NEW.last_error,'')='cancelled')-0)) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0),
paused=paused+COALESCE((SELECT SUM((b.status='pending')*((NEW.control='paused')-0)) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0)
WHERE batch_id IN (SELECT batch_id FROM analysis_batch_items WHERE image_id=NEW.image_id); END;
CREATE TRIGGER IF NOT EXISTS batch_analysis_update AFTER UPDATE OF control,last_error ON image_analysis
WHEN NEW.control!=OLD.control OR COALESCE(NEW.last_error,'')!=COALESCE(OLD.last_error,'')
BEGIN UPDATE analysis_batch_counts SET
cancelled=cancelled+COALESCE((SELECT SUM((b.status='skipped')*((COALESCE(NEW.last_error,'')='cancelled')-(COALESCE(OLD.last_error,'')='cancelled'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0),
skipped=skipped-COALESCE((SELECT SUM((b.status='skipped')*((COALESCE(NEW.last_error,'')='cancelled')-(COALESCE(OLD.last_error,'')='cancelled'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0),
paused=paused+COALESCE((SELECT SUM((b.status='pending')*((NEW.control='paused')-(OLD.control='paused'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=NEW.image_id),0)
WHERE batch_id IN (SELECT batch_id FROM analysis_batch_items WHERE image_id=NEW.image_id); END;
CREATE TRIGGER IF NOT EXISTS batch_analysis_delete AFTER DELETE ON image_analysis

BEGIN UPDATE analysis_batch_counts SET
cancelled=cancelled+COALESCE((SELECT SUM((b.status='skipped')*(0-(COALESCE(OLD.last_error,'')='cancelled'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=OLD.image_id),0),
skipped=skipped-COALESCE((SELECT SUM((b.status='skipped')*(0-(COALESCE(OLD.last_error,'')='cancelled'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=OLD.image_id),0),
paused=paused+COALESCE((SELECT SUM((b.status='pending')*(0-(OLD.control='paused'))) FROM analysis_batch_items b WHERE b.batch_id=analysis_batch_counts.batch_id AND b.image_id=OLD.image_id),0)
WHERE batch_id IN (SELECT batch_id FROM analysis_batch_items WHERE image_id=OLD.image_id); END;
CREATE TABLE IF NOT EXISTS api_schema(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL);
INSERT INTO api_schema VALUES(1,12) ON CONFLICT(id) DO UPDATE SET version=MAX(version,excluded.version);
