import type { Database } from "better-sqlite3";

export interface AuditParams {
  userId?: string | null;   // MongoDB ObjectId string
  clientId?: string | null;
  eventType: string;
  payload?: Record<string, unknown>;
}

export interface AuditEvent {
  id: number;
  mongoUserId: string | null;
  clientId: string | null;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

interface AuditRow {
  id: number;
  mongo_user_id: string | null;
  client_id: string | null;
  event_type: string;
  payload_json: string;
  created_at: string;
}

export function audit(db: Database, params: AuditParams): void {
  const { userId = null, clientId = null, eventType, payload = {} } = params;
  db.prepare(
    "INSERT INTO audit_events (mongo_user_id, client_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)"
  ).run(userId, clientId, eventType, JSON.stringify(payload), new Date().toISOString());
}

export function recentAudits(db: Database, limit = 50): AuditEvent[] {
  return (
    db
      .prepare(
        "SELECT id, mongo_user_id, client_id, event_type, payload_json, created_at FROM audit_events ORDER BY id DESC LIMIT ?"
      )
      .all(limit) as AuditRow[]
  ).map((r) => ({
    id: r.id,
    mongoUserId: r.mongo_user_id,
    clientId: r.client_id,
    eventType: r.event_type,
    payload: JSON.parse(r.payload_json) as Record<string, unknown>,
    createdAt: r.created_at,
  }));
}
