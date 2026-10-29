-- Old analyses could succeed with unknown rating or missing metadata. Make
-- these incomplete jobs retryable, preserving the photo and every saved field.
UPDATE analysis_batch_items SET status='failed'
WHERE status='done' AND image_id IN (
  SELECT a.image_id FROM image_analysis a JOIN images i ON i.id=a.image_id
  WHERE a.status='done' AND (
    i.rating NOT IN ('safe','r18') OR length(trim(i.title))=0
    OR COALESCE(i.image_type,'') NOT IN ('anime','real')
    OR NOT EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
  )
);
UPDATE image_analysis SET status='failed',last_error=CASE WHEN
  (SELECT rating FROM images WHERE id=image_analysis.image_id) NOT IN ('safe','r18')
  THEN 'classification_unresolved' ELSE 'incomplete_response' END
WHERE status='done' AND image_id IN (
  SELECT i.id FROM images i WHERE i.rating NOT IN ('safe','r18') OR length(trim(i.title))=0
    OR COALESCE(i.image_type,'') NOT IN ('anime','real')
    OR NOT EXISTS(SELECT 1 FROM image_tags t WHERE t.image_id=i.id AND length(trim(t.tag))>0)
);
UPDATE library_revision SET version=version+1 WHERE id=1;
