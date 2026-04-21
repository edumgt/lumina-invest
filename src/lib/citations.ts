import { extractArticleRefs, extractCaseNumbers, getDocTypeLabel } from "./legal_search";

export interface DocForCitation {
  id: number;
  docId: string | null;
  docVersion: number;
  title: string | null;
  docType: string;
  score: number;
  effectiveDate: string | null;
  source: string;
  text: string;
}

export function buildCitations(docs: DocForCitation[]): string {
  const lines: string[] = [];
  for (let i = 0; i < docs.length; i++) {
    const d = docs[i];
    const vid = d.docId ? `${d.docId}@v${d.docVersion || 1}` : "DOC";
    const refs = [
      ...extractArticleRefs(d.text).slice(0, 3),
      ...extractCaseNumbers([d.title || "", d.text].join(" ")).slice(0, 2),
    ];
    const meta = [
      getDocTypeLabel(d.docType),
      d.effectiveDate ? `기준일 ${d.effectiveDate}` : "",
      d.source ? `source ${d.source}` : "",
      refs.length ? `핵심표지 ${refs.join(", ")}` : "",
      `score ${d.score.toFixed(3)}`,
    ]
      .filter(Boolean)
      .join(" | ");
    const head = `[C${i + 1}] ${vid} — ${d.title || ""}\n${meta}`;
    const snippet = d.text.length > 600 ? d.text.slice(0, 600) + "..." : d.text;
    lines.push(head);
    lines.push(snippet);
    lines.push("");
  }
  return lines.join("\n");
}
