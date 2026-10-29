export interface Env {
  DB: D1Database;
  LIBSQL_URL?: string;
  LIBSQL_AUTH_TOKEN?: string;
  MIGRATION_MAINTENANCE?: string;
  IMAGES: R2Bucket;
  ASSETS?: Fetcher;
  API_KEY: string;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  OWNER_IDS: string;
  PUBLIC_BASE_URL: string;
  MAX_IMAGE_MB?: string;
  AI_BASE_URL?: string;
  AI_API_KEY?: string;
  AI_MODEL?: string;
}

export type Rating = 'safe' | 'r18' | 'unknown';
export type ImageType = 'anime' | 'real';
export interface Metadata { title: string; tags: string[]; rating: Rating; source_url: string | null; image_type?: ImageType | null; }
export interface ImageRow {
  id: string; sha256: string; object_key: string; filename: string; mime_type: string;
  size: number; title: string; source_url: string | null; rating: Rating;
  uploader_id: string | null; random_key: number; created_at: number;
  metadata_version: number;
  image_type: ImageType | null; original_name: string | null; import_url: string | null;
}
export interface TelegramJob {
  id: string; payload: string; attempts: number; image_id: string | null; notified: number;
}
export class HttpError extends Error {
  constructor(public status: number, public code: string, message: string,public retryAfter?:number) { super(message); }
}
