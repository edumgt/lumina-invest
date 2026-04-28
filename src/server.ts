import path from "path";
import express from "express";
import session from "express-session";
import { RedisStore } from "connect-redis";
import cookieParser from "cookie-parser";
import morgan from "morgan";
import "./lib/load_env";

import { connectMongo } from "./lib/mongo";
import { connectRedis, redisClient } from "./lib/redis-client";
import { initDb } from "./services/db";
import { attachRoles } from "./middlewares/rbac";
import { createAuthRouter } from "./routes/auth.route";
import { createChatRouter } from "./routes/chat.route";
import { createIngestRouter } from "./routes/ingest.route";
import { createLibraryRouter } from "./routes/library.route";
import { createHealthRouter } from "./routes/health.route";
import { createAuditRouter } from "./routes/audit.route";
import { createAdminRouter } from "./routes/admin.route";

const PORT = Number(process.env.PORT || 8000);
const SQLITE_PATH = process.env.SQLITE_PATH || "./data/app.db";
const SESSION_SECRET = process.env.SESSION_SECRET || "change-me";

async function bootstrap(): Promise<void> {
  // ── 인프라 연결 ──────────────────────────────────────────
  await connectRedis();
  await connectMongo();   // migrate-mongo 자동 실행 포함

  // ── SQLite (청크/문서/채팅/감사로그) ────────────────────
  const db = initDb(SQLITE_PATH);

  // ── Express 앱 ─────────────────────────────────────────
  const app = express();
  app.disable("x-powered-by");
  if (process.env.TRUST_PROXY === "1") {
    console.log("[server] trust proxy enabled");
    app.set("trust proxy", 1);
  }

  app.use(morgan("dev"));
  app.use(express.json({ limit: "2mb" }));
  app.use(cookieParser());

  // ── 세션: Redis 스토어 ──────────────────────────────────
  app.use(
    session({
      name: "lawrag.sid",
      secret: SESSION_SECRET,
      store: new RedisStore({ client: redisClient }),
      resave: false,
      saveUninitialized: false,
      cookie: {
        httpOnly: true,
        sameSite: (process.env.COOKIE_SAMESITE as "lax" | "strict" | "none") || "lax",
        secure: process.env.COOKIE_SECURE === "true" || false,
        maxAge: 1000 * 60 * 60 * 24 * 7,
      },
    })
  );

  // 세션에서 MongoDB 역할 로드
  app.use(attachRoles());

  // ── 라우트 ─────────────────────────────────────────────
  app.use("/api/health", createHealthRouter());
  app.use("/api/auth",   createAuthRouter());
  app.use("/api",        createAdminRouter({ db }));
  app.use("/api",        createIngestRouter({ db }));
  app.use("/api",        createLibraryRouter({ db }));
  app.use("/api",        createChatRouter({ db }));
  app.use("/api",        createAuditRouter({ db }));

  // ── 정적 파일 ───────────────────────────────────────────
  app.use(express.static(path.join(__dirname, "..", "public")));
  app.use((req, res) => res.status(404).json({ error: "not found" }));

  app.listen(PORT, () => {
    console.log(`\n[law-rag-agent] listening on http://localhost:${PORT}`);
    console.log("[law-rag-agent] session store: Redis | auth: MongoDB | data: SQLite");
  });
}

bootstrap().catch((err) => {
  console.error("[server] startup failed:", err);
  process.exit(1);
});
