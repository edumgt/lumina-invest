import type { Database } from "better-sqlite3";
import { applyGuardrails } from "../lib/guardrails";
import { buildCitations } from "../lib/citations";
import { retrieve } from "./retrieve.service";
import { audit } from "./audit.service";
import type { OllamaClient } from "../lib/ollama";
import type { AuditParams } from "./audit.service";
import { analyzeLegalQuery, getDocTypeLabel } from "../lib/legal_search";

function buildSystemPrompt(): string {
  return `
너는 '법령/판례 중심' RAG 법률상담 에이전트다.

원칙:
- 법률 자문이 아닌 정보 제공 목적임을 항상 고지한다.
- 반드시 제공된 '근거'를 인용하여 답한다. (근거 없이 단정 금지)
- 검색된 근거들 사이에 충돌이 있으면 문서유형, 기준일, 사실관계를 비교해서 차이를 설명한다.
- 관할/시점/사실관계가 불명확하면 추가 질문을 먼저 제시한다.
- 답변 포맷:
  (1) 핵심 요약 (3~6줄)
  (2) 근거 기반 설명 (인용번호 [C1]..에 연결)
  (3) 체크리스트 / 다음 질문 (입증자료, 절차, 확인사항)
  (4) 면책/주의 (최신성, 관할, 전문가 상담 권장)

스타일:
- 한국어로, 과장 없이, 실무적으로.
- 불확실하면 '가능성이 큼/낮음' 등으로 표현.
`;
}

function buildUserPrompt(question: string, citationsText: string): string {
  const profile = analyzeLegalQuery(question);
  const queryHints = [
    profile.desiredDocTypes.length
      ? `우선 문서유형: ${profile.desiredDocTypes.map(getDocTypeLabel).join(", ")}`
      : "",
    profile.articleRefs.length ? `감지 조문: ${profile.articleRefs.join(", ")}` : "",
    profile.caseNumbers.length ? `감지 사건번호: ${profile.caseNumbers.join(", ")}` : "",
    profile.wantsLatest ? "최신성 확인 필요" : "",
  ]
    .filter(Boolean)
    .join("\n");

  return `
[질문]
${question}

[질문 분석]
${queryHints || "특이 메타데이터 없음"}

[근거(검색결과 인용)]
${citationsText}
`;
}

export interface RagResult {
  answer: string;
  citations: Array<{
    id: number;
    docId: string | null;
    docVersion: number;
    source: string;
    score: number;
  }>;
}

export async function answerWithRag({
  db,
  ollama,
  llmModel,
  embedModel,
  question,
  topK,
  userRoles,
  auditCtx = null,
}: {
  db: Database;
  ollama: OllamaClient;
  llmModel: string;
  embedModel: string;
  question: string;
  topK: number;
  userRoles: string[];
  auditCtx?: Omit<AuditParams, "eventType" | "payload"> | null;
}): Promise<RagResult> {
  const guard = applyGuardrails(question);
  if (guard.blocked) {
    if (auditCtx)
      audit(db, { ...auditCtx, eventType: "chat_blocked", payload: { question } });
    return { answer: guard.response ?? "", citations: [] };
  }

  const docs = await retrieve({ db, ollama, embedModel, query: question, topK, userRoles });
  const citationsText = buildCitations(docs);

  if (auditCtx) {
    audit(db, {
      ...auditCtx,
      eventType: "retrieve",
      payload: {
        question,
        topK,
        results: docs.map((d) => ({
          chunkId: d.id,
          docId: d.docId,
          docVersion: d.docVersion,
          score: d.score,
          source: d.source,
        })),
      },
    });
  }

  const answer = await ollama.chat({
    model: llmModel,
    messages: [
      { role: "system", content: buildSystemPrompt() },
      { role: "user", content: buildUserPrompt(question, citationsText) },
    ],
    options: { temperature: 0.2 },
  });

  return {
    answer,
    citations: docs.map((d) => ({
      id: d.id,
      docId: d.docId,
      docVersion: d.docVersion,
      source: d.source,
      score: d.score,
    })),
  };
}
