import express from "express";
import type { Database } from "better-sqlite3";
import { requireLogin } from "../middlewares/auth";
import { createOllama } from "../lib/ollama";
import { retrieve } from "../services/retrieve.service";

export function createLibraryRouter({ db }: { db: Database }): express.Router {
  const router = express.Router();
  const ollama = createOllama({
    baseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  });
  const embedModel = process.env.EMBED_MODEL || "nomic-embed-text";

  router.get("/library/search", requireLogin, async (req, res) => {
    const q = String(req.query?.q || "").trim();
    if (!q) return res.json({ ok: true, items: [] });

    try {
      const user = req.session.user!;
      const rows = await retrieve({
        db,
        ollama,
        embedModel,
        query: q,
        topK: 15,
        userRoles: user.roles || ["user"],
      });

      res.json({
        ok: true,
        items: rows.map((r) => ({
          source: r.source,
          docType: r.docType,
          title: r.title,
          docId: r.docId,
          docVersion: r.docVersion,
          effectiveDate: r.effectiveDate,
          jurisdiction: r.jurisdiction,
          text: r.text.length > 800 ? r.text.slice(0, 800) + "..." : r.text,
          score: r.score,
        })),
      });
    } catch (e) {
      const err = e as Error;
      res.status(500).json({ error: err.message || "library search error" });
    }
  });

  return router;
}
