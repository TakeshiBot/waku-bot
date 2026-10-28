CREATE TABLE worker_slots (
  kind TEXT NOT NULL,
  slot INTEGER NOT NULL,
  token TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(kind,slot)
);
INSERT INTO worker_slots(kind,slot) VALUES ('upload',1),('upload',2),('ai',1),('ai',2);
CREATE TABLE telegram_send_times (chat_id INTEGER PRIMARY KEY,next_send INTEGER NOT NULL);
