const DOC_TYPE_LABELS: Record<string, string> = {
  law: "법령",
  case: "판결문",
  interpretation: "해석례",
  decision: "결정례",
  misc: "일반문서",
};

const DOC_TYPE_HINTS: Array<{ type: string; patterns: RegExp[] }> = [
  {
    type: "law",
    patterns: [/(법령|조문|시행령|시행규칙|법조문|제\s*\d+\s*조)/i],
  },
  {
    type: "case",
    patterns: [/(판례|판결|소송|선고|사건번호|재판)/i],
  },
  {
    type: "interpretation",
    patterns: [/(해석례|유권해석|법제처|회답|질의요지)/i],
  },
  {
    type: "decision",
    patterns: [/(결정례|헌재|헌법재판소|준항고|위헌)/i],
  },
];

export interface LegalQueryProfile {
  raw: string;
  normalized: string;
  tokens: string[];
  articleRefs: string[];
  caseNumbers: string[];
  desiredDocTypes: string[];
  wantsLatest: boolean;
}

export interface LegalChunkLike {
  docType: string;
  title: string | null;
  text: string;
  docId: string | null;
  effectiveDate: string | null;
  jurisdiction: string | null;
  source: string;
  docVersion: number;
}

export function getDocTypeLabel(docType: string): string {
  return DOC_TYPE_LABELS[docType] || DOC_TYPE_LABELS.misc;
}

export function normalizeLegalText(input: string): string {
  return String(input || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function tokenizeLegalText(input: string): string[] {
  const matches = normalizeLegalText(input).match(/[\p{L}\p{N}]+/gu) || [];
  const uniq = new Set<string>();
  for (const token of matches) {
    if (token.length >= 2 || /\d/.test(token)) uniq.add(token);
  }
  return [...uniq];
}

export function extractArticleRefs(input: string): string[] {
  const matches = input.match(/제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?/g) || [];
  return [...new Set(matches.map((m) => m.replace(/\s+/g, " ").trim()))];
}

export function extractCaseNumbers(input: string): string[] {
  const matches =
    input.match(/[0-9]{2,4}\s*[가-힣]{1,4}\s*\d+/g) ||
    input.match(/[0-9]{2,4}[가-힣]{1,4}\d+/g) ||
    [];
  return [...new Set(matches.map((m) => m.replace(/\s+/g, "")))];
}

export function analyzeLegalQuery(question: string): LegalQueryProfile {
  const desiredDocTypes = DOC_TYPE_HINTS.filter((entry) =>
    entry.patterns.some((pattern) => pattern.test(question))
  ).map((entry) => entry.type);

  return {
    raw: question,
    normalized: normalizeLegalText(question),
    tokens: tokenizeLegalText(question),
    articleRefs: extractArticleRefs(question),
    caseNumbers: extractCaseNumbers(question),
    desiredDocTypes,
    wantsLatest: /(최신|현행|현재|개정|최근)/.test(question),
  };
}

export function buildChunkEmbeddingInput(
  chunkText: string,
  meta: {
    docId: string;
    docType: string;
    title: string | null;
    effectiveDate: string | null;
    jurisdiction: string;
    extra: Record<string, unknown>;
    parsed: Record<string, unknown>;
  }
): string {
  const lines = [
    `문서유형: ${getDocTypeLabel(meta.docType)}`,
    meta.title ? `제목: ${meta.title}` : "",
    meta.docId ? `문서ID: ${meta.docId}` : "",
    meta.jurisdiction ? `관할: ${meta.jurisdiction}` : "",
    meta.effectiveDate ? `기준일: ${meta.effectiveDate}` : "",
    summarizeExtraMeta(meta.extra, meta.parsed),
    "",
    chunkText,
  ].filter(Boolean);

  return lines.join("\n");
}

function summarizeExtraMeta(
  extra: Record<string, unknown>,
  parsed: Record<string, unknown>
): string {
  const pairs: string[] = [];
  const values = { ...extra, ...parsed };
  const candidateKeys = [
    "caseNum",
    "caseNo",
    "courtName",
    "court",
    "ministry",
    "interpreMinName",
    "agendaNum",
    "questionMinName",
  ];

  for (const key of candidateKeys) {
    const value = values[key];
    if (typeof value === "string" && value.trim()) pairs.push(`${key}: ${value.trim()}`);
  }

  const articles = Array.isArray(values.articles)
    ? (values.articles as string[]).slice(0, 8)
    : [];
  if (articles.length) pairs.push(`주요조문: ${articles.join(", ")}`);

  return pairs.join(" | ");
}

export function scoreLegalSearchCandidate(
  profile: LegalQueryProfile,
  candidate: LegalChunkLike,
  vectorScore: number
): number {
  const haystack = normalizeLegalText(
    [
      candidate.title || "",
      candidate.docId || "",
      candidate.source,
      candidate.text,
      candidate.effectiveDate || "",
      candidate.jurisdiction || "",
    ].join(" ")
  );

  const overlap = profile.tokens.length
    ? profile.tokens.filter((token) => haystack.includes(token)).length / profile.tokens.length
    : 0;

  const titleNorm = normalizeLegalText(candidate.title || "");
  const articleBoost = profile.articleRefs.some((ref) => candidate.text.includes(ref)) ? 0.22 : 0;
  const caseBoost = profile.caseNumbers.some((no) =>
    normalizeLegalText([candidate.title || "", candidate.text, candidate.docId || ""].join(" ")).includes(
      normalizeLegalText(no)
    )
  )
    ? 0.2
    : 0;
  const typeBoost =
    profile.desiredDocTypes.length === 0 || profile.desiredDocTypes.includes(candidate.docType)
      ? 0.08
      : -0.03;
  const titleBoost = profile.tokens.some((token) => titleNorm.includes(token)) ? 0.06 : 0;
  const recencyBoost =
    profile.wantsLatest && candidate.effectiveDate
      ? Math.min(scoreDate(candidate.effectiveDate) / 10000, 0.05)
      : 0;

  return vectorScore * 0.72 + overlap * 0.2 + articleBoost + caseBoost + typeBoost + titleBoost + recencyBoost;
}

function scoreDate(value: string): number {
  const compact = value.replace(/[^0-9]/g, "").slice(0, 8);
  if (!compact) return 0;
  return Number(compact);
}

export function rerankLegalChunks<T extends LegalChunkLike & { score: number }>(
  candidates: T[],
  profile: LegalQueryProfile,
  topK: number
): T[] {
  const rescored = candidates
    .map((candidate) => ({
      ...candidate,
      score: scoreLegalSearchCandidate(profile, candidate, candidate.score),
    }))
    .sort((a, b) => b.score - a.score);

  const selected: T[] = [];
  const perDoc = new Map<string, number>();
  for (const candidate of rescored) {
    const key = candidate.docId || candidate.source;
    const used = perDoc.get(key) || 0;
    if (used >= 2) continue;
    selected.push(candidate);
    perDoc.set(key, used + 1);
    if (selected.length >= topK) break;
  }
  return selected;
}
