/* Conservative local Pine v6 checks. This does not replace TradingView's compiler.
 * Rules: https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/ */
export function checkPineV6(source) {
  const diagnostics = [], tokens = [], versions = [];
  const add = (token, message, severity = "error") => diagnostics.push({ line: token.line, column: token.column, message, severity });
  let i = 0, line = 1, column = 1;
  const advance = () => { if (source[i++] === "\n") { line++; column = 1; } else column++; };
  const pos = () => ({ line, column, offset: i });
  while (i < source.length) {
    const c = source[i], start = pos();
    if (/\s/.test(c)) { advance(); continue; }
    if (source.startsWith("//", i)) {
      const end = source.indexOf("\n", i), comment = source.slice(i, end < 0 ? source.length : end);
      const version = comment.match(/^\/\/@version\s*=\s*(\d+)\s*$/);
      if (version) versions.push({ ...start, version: Number(version[1]) });
      else if (/^\/\/@version\b/.test(comment)) add(start, "버전 선언 형식은 //@version=6 입니다.");
      while (i < source.length && source[i] !== "\n") advance();
      continue;
    }
    if (c === '"' || c === "'") {
      const triple = source.startsWith(c.repeat(3), i), delimiter = triple ? c.repeat(3) : c;
      for (let j = 0; j < delimiter.length; j++) advance();
      let closed = false;
      while (i < source.length) {
        if (source.startsWith(delimiter, i)) { for (let j = 0; j < delimiter.length; j++) advance(); closed = true; break; }
        if (source[i] === "\\") { advance(); if (i < source.length) advance(); }
        else advance();
      }
      if (!closed) add(start, "문자열의 닫는 따옴표가 없습니다.");
      tokens.push({ ...start, text: source.slice(start.offset, i), kind: "string" });
      continue;
    }
    if (/[a-zA-Z_]/.test(c)) {
      while (i < source.length && /[a-zA-Z0-9_.]/.test(source[i])) advance();
      tokens.push({ ...start, text: source.slice(start.offset, i), kind: "identifier" });
      continue;
    }
    if (/\d/.test(c)) {
      while (i < source.length && /[\d.]/.test(source[i])) advance();
      tokens.push({ ...start, text: source.slice(start.offset, i), kind: "number" });
      continue;
    }
    const operator = [":=", "=>", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%="].find(op => source.startsWith(op, i));
    if (operator) { advance(); advance(); tokens.push({ ...start, text: operator }); }
    else { advance(); tokens.push({ ...start, text: c }); }
  }
  if (!tokens.length) add({ line: 1, column: 1 }, "검사할 Pine 코드를 입력하세요.");
  if (!versions.length) add({ line: 1, column: 1 }, "Pine v6 검사에는 //@version=6 선언이 필요합니다.");
  versions.forEach((v, n) => {
    if (v.version !== 6) add(v, `현재 버전은 v${v.version}입니다. v6로 변경하세요.`);
    if (n) add(v, "버전 선언이 중복되었습니다.");
  });
  const namedArgs = new Map();
  const stack = [], matching = { ")": "(", "]": "[", "}": "{" }, declarations = [];
  for (let n = 0; n < tokens.length; n++) {
    const t = tokens[n], next = tokens[n + 1], previous = tokens[n - 1];
    if (t.kind === "string") continue;
    if (["{", "}", ";", "@", "`"].includes(t.text)) add(t, `Pine 코드에서 '${t.text}' 문자를 사용할 수 없습니다.`);
    if (t.text === "=" && previous?.kind === "identifier" && ["(", ","].includes(tokens[n - 2]?.text) && stack.at(-1)?.text === "(") {
      const frame = stack.at(-1);
      const seen = namedArgs.get(frame) || new Set();
      if (seen.has(previous.text)) add(previous, `함수 인자 '${previous.text}'가 중복되었습니다.`);
      seen.add(previous.text); namedArgs.set(frame, seen);
    }
    if (["+", "-", "*", "/", "and", "or", "==", "!=", "<", ">"].includes(t.text) && (!next || [")", "]", ","].includes(next.text))) add(t, "연산자 뒤에 표현식이 필요합니다.");
    if (["(", "[", "{"].includes(t.text)) stack.push(t);
    else if (matching[t.text]) {
      if (stack.at(-1)?.text !== matching[t.text]) add(t, `괄호 '${t.text}'에 맞는 여는 괄호가 없습니다.`);
      else stack.pop();
    }
    if (["indicator", "strategy", "library"].includes(t.text) && next?.text === "(" && stack.length === 0) {
      declarations.push(t);
      if (t.column !== 1) add(t, "스크립트 선언은 들여쓰기 없이 전역 범위에 있어야 합니다.");
    }
    if (["=", ":=", "+=", "-="].includes(t.text) && (!next || [")", "]", ",", "="].includes(next.text))) add(t, "대입 연산자 뒤에 표현식이 필요합니다.");
    if (["parseInt", "parseFloat"].includes(t.text) && next?.text === "(") add(t, `${t.text}()는 Pine 함수가 아닙니다. 숫자 변환에는 int(), float(), str.tonumber()를 사용하세요.`);
    if (["study", "iff"].includes(t.text) && next?.text === "(") add(t, `${t.text}()는 v6에서 사용할 수 없습니다. ${t.text === "study" ? "indicator()" : "조건 ? 값1 : 값2"}를 사용하세요.`);
    if (t.text === "transp" && next?.text === "=" && stack.length) add(t, "v6에서는 transp 인자를 사용할 수 없습니다. color.new()를 사용하세요.");
    if (t.text === "when" && next?.text === "=" && stack.some(p => tokens[tokens.indexOf(p) - 1]?.text.startsWith("strategy."))) add(t, "v6 strategy 함수의 when 인자는 제거되었습니다. if 조건문을 사용하세요.");
    if (["bool"].includes(t.text) && next?.kind === "identifier" && tokens[n + 2]?.text === "=" && tokens[n + 3]?.text === "na") add(t, "v6의 bool 변수에 na를 대입할 수 없습니다.");
    if (["and", "or"].includes(t.text) && (!next || [")", "]"].includes(next.text))) add(t, "논리 연산자 뒤에 조건식이 필요합니다.");
    if (t.text === "," && previous?.text === "(") add(t, "첫 번째 함수 인자가 비어 있습니다.");
  }
  stack.forEach(t => add(t, `괄호 '${t.text}'가 닫히지 않았습니다.`));
  if (declarations.length !== 1) add(declarations[1] || { line: 1, column: 1 }, "indicator(), strategy(), library() 중 하나의 선언이 정확히 한 번 필요합니다.");
  diagnostics.sort((a, b) => a.line - b.line || a.column - b.column);
  return { diagnostics, errors: diagnostics.filter(d => d.severity === "error").length };
}
