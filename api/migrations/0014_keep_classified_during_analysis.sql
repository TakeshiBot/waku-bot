-- Classification is based on saved metadata; reanalysis does not hide a completed image.
DROP TRIGGER IF EXISTS catalog_image_insert;
DROP TRIGGER IF EXISTS catalog_image_update;
DROP TRIGGER IF EXISTS catalog_tag_insert;
DROP TRIGGER IF EXISTS catalog_tag_delete;
DROP TRIGGER IF EXISTS catalog_tag_update;
DROP TRIGGER IF EXISTS catalog_analysis_insert;
DROP TRIGGER IF EXISTS catalog_analysis_delete;
DROP TRIGGER IF EXISTS catalog_analysis_update;
CREATE TRIGGER IF NOT EXISTS catalog_image_insert AFTER INSERT ON images BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=NEW.id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_image_update AFTER UPDATE OF title,rating,image_type,size ON images
WHEN NEW.title!=OLD.title OR NEW.rating!=OLD.rating OR NEW.image_type IS NOT OLD.image_type OR NEW.size!=OLD.size
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=NEW.id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_insert AFTER INSERT ON image_tags
WHEN length(trim(NEW.tag))>0 AND NOT EXISTS(SELECT 1 FROM image_tags WHERE image_id=NEW.image_id AND tag!=NEW.tag AND length(trim(tag))>0)
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_delete AFTER DELETE ON image_tags
WHEN length(trim(OLD.tag))>0 AND NOT EXISTS(SELECT 1 FROM image_tags WHERE image_id=OLD.image_id AND length(trim(tag))>0)
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=OLD.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;
CREATE TRIGGER IF NOT EXISTS catalog_tag_update AFTER UPDATE OF tag,image_id ON image_tags
BEGIN INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=OLD.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; INSERT INTO image_catalog_state(image_id,rating,size,classified)
SELECT i.id,i.rating,i.size,(length(trim(i.title))>0 AND i.rating IN ('safe','r18') AND COALESCE(i.image_type,'') IN ('anime','real')
AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)) FROM images i WHERE i.id=NEW.image_id
ON CONFLICT(image_id) DO UPDATE SET rating=excluded.rating,size=excluded.size,classified=excluded.classified
WHERE rating!=excluded.rating OR size!=excluded.size OR classified!=excluded.classified; END;

UPDATE image_catalog_state SET classified=1 WHERE classified=0 AND image_id IN (
  SELECT i.id FROM images i WHERE length(trim(i.title))>0 AND i.rating IN ('safe','r18')
  AND COALESCE(i.image_type,'') IN ('anime','real')
  AND EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
);
UPDATE library_revision SET version=version+1 WHERE id=1;
UPDATE api_schema SET version=14 WHERE id=1 AND version<14;
