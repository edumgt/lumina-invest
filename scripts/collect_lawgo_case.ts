#!/usr/bin/env node
import "../src/lib/load_env";
import pLimit from "p-limit";
import yargs from "yargs";
import { hideBin } from "yargs/helpers";

import { listCases, fetchCaseDetail } from "../src/collectors/providers/lawgo_case";
import { normalizeCase } from "../src/collectors/normalize/lawgo_normalize";
import { loadCheckpoint, saveCheckpoint } from "../src/collectors/core/checkpoint";
import { writeJson, appendJsonl, writeText } from "../src/collectors/core/io";

async function main(): Promise<void> {
  const argv = await yargs(hideBin(process.argv))
    .option("pages", { type: "number", default: 1 })
    .option("perPage", { type: "number", default: 20 })
    .option("concurrency", { type: "number", default: 3 })
    .option("out", { type: "string", default: "data/collected/lawgo/case" })
    .parse();

  const log = console;
  const cp = loadCheckpoint("lawgo_case");
  log.log(
    `[collect:cases] start pages=${argv.pages} perPage=${argv.perPage} concurrency=${argv.concurrency}`
  );

  const limit = pLimit(argv.concurrency);

  let total = 0;
  for (let page = 1; page <= argv.pages; page++) {
    const items = await listCases({ page, perPage: argv.perPage, log });
    log.log(`[collect:cases] page=${page} items=${items.length}`);

    const tasks = (items as Record<string, unknown>[]).map((it) =>
      limit(async () => {
        const caseId = it.id || it.caseId || it["판례ID"];
        const detail = await fetchCaseDetail({ caseId, log });
        const doc = normalizeCase({ item: it, detail });

        const base = `${argv.out}/${doc.doc_id}`;
        writeJson(`${base}.json`, doc);
        const mdTitle = `# ${doc.title}\n\n`;
        writeText(`${base}.md`, mdTitle + doc.body_md);

        appendJsonl("data/collected/index.jsonl", {
          collected_at: new Date().toISOString(),
          ...doc,
          path_json: `${base}.json`,
          path_md: `${base}.md`,
        });

        total++;
      })
    );

    await Promise.all(tasks);
  }

  saveCheckpoint("lawgo_case", {
    ...cp,
    lastRunAt: new Date().toISOString(),
    pages: argv.pages,
    perPage: argv.perPage,
    total,
  });
  log.log(`[collect:cases] done total=${total}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
