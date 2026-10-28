export interface Env {
  DB: D1Database;
  IMAGES: R2Bucket;
  API_KEY: string;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  OWNER_IDS: string;
  PUBLIC_BASE_URL: string;
  MAX_IMAGE_MB?: string;
}

export type Rating = 'safe' | 'r18' | 'unknown';
export interface Metadata { title: string; tags: string[]; rating: Rating; source_url: string | null; }
export interface ImageRow {
  id: string; sha256: string; object_key: string; filename: string; mime_type: string;
  size: number; title: string; source_url: string | null; rating: Rating;
  uploader_id: string | null; random_key: number; created_at: number;
}
export interface TelegramJob {
  id: string; payload: string; attempts: number; image_id: string | null; notified: number;
}
export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
