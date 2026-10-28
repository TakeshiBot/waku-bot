CREATE TABLE ai_providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  key_cipher TEXT NOT NULL,
  preferred_model_id TEXT
);
CREATE TABLE ai_models (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES ai_providers(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  UNIQUE(provider_id,model)
);
CREATE TABLE ai_preferences (
  id INTEGER PRIMARY KEY CHECK(id=1),
  initialized INTEGER NOT NULL DEFAULT 0,
  provider_id TEXT REFERENCES ai_providers(id) ON DELETE SET NULL,
  model_id TEXT REFERENCES ai_models(id) ON DELETE SET NULL
);
INSERT INTO ai_preferences(id) VALUES(1);
