ALTER TABLE ai_providers ADD COLUMN enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1));
ALTER TABLE ai_providers ADD COLUMN config_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_providers ADD COLUMN cooldown_until INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_providers ADD COLUMN last_error TEXT;
UPDATE ai_providers SET enabled=1 WHERE id=(SELECT provider_id FROM ai_preferences WHERE id=1);
UPDATE ai_providers SET preferred_model_id=(SELECT model_id FROM ai_preferences WHERE id=1) WHERE enabled=1;
CREATE TABLE ai_provider_slots (
  provider_id TEXT NOT NULL REFERENCES ai_providers(id) ON DELETE CASCADE,
  slot INTEGER NOT NULL CHECK(slot IN (1,2)),
  token TEXT,
  image_id TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  last_started INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(provider_id,slot)
);
CREATE UNIQUE INDEX ai_slot_token ON ai_provider_slots(token) WHERE token IS NOT NULL;
INSERT INTO ai_provider_slots(provider_id,slot) SELECT id,1 FROM ai_providers UNION ALL SELECT id,2 FROM ai_providers;
ALTER TABLE image_analysis ADD COLUMN provider_id TEXT;
ALTER TABLE image_analysis ADD COLUMN lease_token TEXT;
