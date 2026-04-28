import express from "express";
import type { Database } from "better-sqlite3";
import { requireLogin } from "../middlewares/auth";
import { createOllama } from "../lib/ollama";
import { answerWithRag } from "../services/agent.service";
import { audit } from "../services/audit.service";

export function createChatRouter({ db }: { db: Database }): express.Router {
  const router = express.Router();

  const ollama = createOllama({
    baseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  });
  const llmModel  = process.env.LLM_MODEL  || "llama3.1";
  const embedModel = process.env.EMBED_MODEL || "nomic-embed-text";
  const topK = Number(process.env.TOP_K || 6);

  router.post("/chat", requireLogin, async (req, res) => {
    try {
      const question = String(req.body?.question || "").trim();
      if (!question) return res.status(400).json({ error: "question is required" });

      const user  = req.session.user!;
      const roles = user.roles || ["user"];

      audit(db, {
        userId: user.id,
        clientId: user.clientId,
        eventType: "chat_request",
        payload: { question, roles },
      });

      const { answer, citations } = await answerWithRag({
        db,
        ollama,
        llmModel,
        embedModel,
        question,
        topK,
        userRoles: roles,
        auditCtx: { userId: user.id, clientId: user.clientId },
      });

      db.prepare(
        "INSERT INTO chats (mongo_user_id, client_id, question, answer, citations_json, created_at) VALUES (?, ?, ?, ?, ?, ?)"
      ).run(user.id, user.clientId, question, answer, JSON.stringify(citations), new Date().toISOString());

      audit(db, {
        userId: user.id,
        clientId: user.clientId,
        eventType: "chat_response",
        payload: { citationsCount: citations.length },
      });

      res.json({ ok: true, answer });
    } catch (e) {
      const err = e as Error;
      res.status(500).json({ error: err.message || "chat error" });
    }
  });

  return router;
}
