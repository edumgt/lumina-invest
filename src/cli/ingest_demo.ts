import "../lib/load_env";
import { initDb } from "../services/db";
import { createOllama } from "../lib/ollama";
import { ingestDirectory } from "../services/ingest.service";

(async () => {
  const db = initDb(process.env.SQLITE_PATH || "./data/app.db");
  const ollama = createOllama({
    baseUrl: process.env.OLLAMA_BASE_URL || "http://127.0.0.1:11434",
  });

  const log: string[] = [];
  const result = await ingestDirectory({
    db,
    ollama,
    rawDir: process.env.RAW_DIR || "./data/raw",
    embedModel: process.env.EMBED_MODEL || "nomic-embed-text",
    chunkSize: Number(process.env.CHUNK_SIZE || 1200),
    overlap: Number(process.env.CHUNK_OVERLAP || 150),
    log,
  });

  console.log(log.join("\n"));
  console.log(result);
  process.exit(0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
