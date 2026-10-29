# agent_server/mcp_tools.py — MCP 도구(/mcp): search_code · search_docs · request_fix
# MCP 파이썬 SDK 2.x(MCPServer, 1.x의 FastMCP와 같은 구조). 상태 없음(stateless) — 멀티턴 기억은 Continue 몫
import time                                                  # 지연 시간

import httpx                                                 # RAG·LLM 호출
from mcp.server.mcpserver import Context, MCPServer          # MCP 서버·요청 컨텍스트

from agent_server import auth, fixer, usage                  # 인증·수정안·사용량
from agent_server.proxy import upstream_headers              # 외부 LLM 헤더(키)

NO_AUTH = "인증 정보가 없습니다. Continue 설정의 MCP Authorization 토큰을 확인하세요."


def build_mcp(get_state) -> MCPServer:
    """MCP 서버를 만들고 도구를 등록한다. get_state() = 현재 AgentState."""
    mcp = MCPServer(
        name="codeagent",
        instructions="사내 코드·문서 검색과 검증된 수정안 생성 도구. 검색 결과는 서버 색인(main 기준) 힌트이므로 "
                     "수정 전 반드시 로컬 파일을 읽어 확인한다.",   # Continue가 LLM에게 서버 설명으로 전달
    )

    async def current_user(ctx: Context) -> str | None:
        """★ 신원은 원래 HTTP 요청의 Authorization 헤더에서만 — 모델이 넘긴 인자는 믿지 않는다."""
        request = getattr(ctx.request_context, "request", None)   # streamable-http로 들어온 HTTP 요청
        if request is None:                                  # stdio 등 HTTP가 아니면 신원 없음
            return None
        user, _ = await auth.lookup(get_state(), auth.bearer(request.headers))
        return user

    async def rag_search(user: str, collection: str, query: str, k: int, with_text: bool = False,
                         exclude_path: str | None = None) -> dict:
        """RAG /search 호출 — 사번만 넘기고 필터는 RAG가 만든다(2판 md D5)."""
        st = get_state()
        r = await st.http.post(f"{st.settings.rag_url}/search",
                               json={"collection": collection, "query": query, "k": k,
                                     "with_text": with_text, "exclude_path": exclude_path},
                               headers={"X-Service-Token": st.settings.search_token, "X-User-Id": user},
                               timeout=30)                   # 검색은 30초 안에
        r.raise_for_status()                                 # 실패면 예외
        return r.json()

    def format_hits(data: dict) -> str:
        """RAG 결과 → LLM이 읽기 좋은 글. 위치(경로·함수명·커밋) 중심, 줄 번호는 참고."""
        if data.get("denied"):
            return "검색할 수 있는 항목이 없습니다(권한 없음). 로컬 파일 검색을 쓰세요."
        if not data["results"]:
            return "관련 결과가 없습니다. 이름을 알면 로컬 파일 검색을 쓰세요."
        out = []
        for h in data["results"]:
            sym = f" · {h['symbol']}" if h["symbol"] else ""     # 함수명·섹션명
            out.append(f"{h['repo']}/{h['path']}{sym} ({h['start']}-{h['end']}줄) [main@{h['commit']}]\n    "
                       + h["text"].replace("\n", "\n    "))     # 미리보기 들여쓰기
        return "\n".join(out) + "\n\n위치는 서버 색인 기준 힌트입니다. 수정 전 read_file로 로컬 파일을 확인하세요."

    async def run_search(tool: str, ctx: Context, collection: str, query: str, k: int) -> str:
        """두 검색 도구의 공통 처리: 인증 → RAG → 기록."""
        t0 = time.monotonic()
        user = await current_user(ctx)
        if user is None:
            await usage.record_tool(get_state(), user="-", tool=tool, latency_ms=0, ok=False, detail="no auth")
            return NO_AUTH
        try:
            data = await rag_search(user, collection, query, k)
            ok, detail, text = True, f"hits={len(data['results'])} denied={data.get('denied')}", format_hits(data)
        except httpx.HTTPError as e:                         # RAG 장애 — 오류도 결과로 LLM에 돌려준다
            ok, detail = False, f"rag error {type(e).__name__}"
            text = "사내 검색 서비스에 연결하지 못했습니다. 로컬 파일 검색으로 대신하세요."
        await usage.record_tool(get_state(), user=user, tool=tool, latency_ms=int((time.monotonic() - t0) * 1000),
                                ok=ok, detail=detail)
        return text

    @mcp.tool()
    async def search_code(query: str, ctx: Context, k: int = 8) -> str:
        """이름을 모를 때 자연어로 사내 코드 위치를 찾는다(의미 검색). 결과는 경로·함수명·커밋.
        정확한 이름을 알면 이 도구 대신 로컬 파일 검색을 쓴다."""
        return await run_search("search_code", ctx, "code", query, max(1, min(k, 20)))

    @mcp.tool()
    async def search_docs(query: str, ctx: Context, k: int = 5) -> str:
        """사내 개발 가이드·설계 문서를 검색한다. 코드 규칙·절차를 확인할 때 쓴다."""
        return await run_search("search_docs", ctx, "docs", query, max(1, min(k, 20)))

    @mcp.tool()
    async def request_fix(path: str, code: str, instruction: str, ctx: Context) -> str:
        """로컬에서 읽은 코드(code, 파일 전체 또는 함수 부분)와 요청(instruction)으로 검증된 수정안을 만든다.
        사내 규칙·관련 코드를 참고하고 문법 검사를 통과한 '찾을 글 → 바꿀 글' 블록을 돌려준다.
        파일은 바뀌지 않는다 — 편집 도구로 적용하고 사용자가 diff를 확인한다."""
        st = get_state()
        t0 = time.monotonic()
        user = await current_user(ctx)                       # 신원
        if user is None:
            await usage.record_tool(st, user="-", tool="request_fix", latency_ms=0, ok=False, detail="no auth")
            return NO_AUTH
        if len(code) > st.settings.fix_max_code_chars:       # 컨텍스트 보호(32k)
            await usage.record_tool(st, user=user, tool="request_fix", latency_ms=0, ok=False, detail="code too long")
            return f"코드가 너무 깁니다({len(code)}자, 상한 {st.settings.fix_max_code_chars}). 수정할 함수 부분만 보내세요."

        # ① 참고 자료: 사내 규칙(docs) + 관련 코드(code, 이 파일 자신은 제외) — 실패해도 자료 없이 진행
        code_hits, doc_hits = [], []
        try:
            doc_hits = (await rag_search(user, "docs", instruction, 3, with_text=True))["results"]
            code_hits = (await rag_search(user, "code", f"{path} {instruction}", 3, with_text=True,
                                          exclude_path=path))["results"]
        except httpx.HTTPError:
            pass                                             # 검색 장애는 수정안 생성을 막지 않는다
        references, ref_names = fixer.build_references(code_hits, doc_hits)

        # ② 생성 → 적용해 보기 → 문법 검사 → 실패면 오류를 붙여 재생성
        messages = [{"role": "system", "content": fixer.SYSTEM},
                    {"role": "user", "content": fixer.first_user_prompt(path, instruction, code, references)}]
        attempts, last_error = 0, ""
        for attempts in range(1, st.settings.fix_max_retries + 2):   # 첫 생성 + 재시도
            reason = await usage.check_and_count(st, user)   # 안쪽 LLM 호출도 같은 한도에 센다
            if reason:
                last_error = reason
                break
            output = await call_llm(st, user, messages)      # 외부 LLM(같은 gemma4)
            if output is None:                               # 연결 실패
                last_error = "외부 LLM 호출 실패"
                break
            blocks = fixer.parse_blocks(output)              # 블록 해석
            new_code, errors = fixer.apply_blocks(code, blocks)   # 원본에 적용해 보기
            if not errors:
                syntax = fixer.validate(path, code, new_code)     # 문법 검사
                if syntax:
                    errors = [syntax]
            if not errors:                                   # ③ 통과
                await usage.record_tool(st, user=user, tool="request_fix",
                                        latency_ms=int((time.monotonic() - t0) * 1000), ok=True,
                                        detail=f"attempts={attempts} blocks={len(blocks)} refs={len(ref_names)}")
                return fixer.format_result(path, blocks, attempts, ref_names)
            last_error = " / ".join(errors)                  # 실패 이유
            messages += [{"role": "assistant", "content": output},   # 직전 출력과
                         {"role": "user", "content": f"적용 실패: {last_error}\n같은 형식으로 다시 출력하세요."}]   # 오류를 붙여 다시

        await usage.record_tool(st, user=user, tool="request_fix", latency_ms=int((time.monotonic() - t0) * 1000),
                                ok=False, detail=f"attempts={attempts} error={last_error[:150]}")
        return (f"검증된 수정안을 만들지 못했습니다({attempts}회 시도, 마지막 오류: {last_error}). "
                "직접 편집 도구로 수정하거나 요청을 더 구체적으로 나눠 주세요.")

    return mcp


async def call_llm(st, user: str, messages: list[dict]) -> str | None:
    """request_fix 안쪽 LLM 호출(스트리밍 없이) + 사용량 기록(source='request_fix')."""
    body = {"model": st.settings.llm_model, "messages": messages, "temperature": 0.2}   # 낮은 온도 — 형식 안정
    t0 = time.monotonic()
    est_prompt = usage.estimate_tokens(str(messages))        # 추정치(usage가 없을 때)
    try:
        r = await st.http.post(f"{st.settings.llm_base_url.rstrip('/')}/chat/completions",
                               json=body, headers=upstream_headers(st))
        r.raise_for_status()                                 # 4xx/5xx면 예외
        data = r.json()
        text = data["choices"][0]["message"].get("content") or ""   # 출력 글
        await usage.record_llm(st, user=user, source="request_fix", model=st.settings.llm_model, stream=False,
                               usage=data.get("usage"), est_prompt=est_prompt,
                               est_completion=usage.estimate_tokens(text),
                               latency_ms=int((time.monotonic() - t0) * 1000), status=r.status_code)
        return text
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:   # 연결·형식 오류
        await usage.record_llm(st, user=user, source="request_fix", model=st.settings.llm_model, stream=False,
                               usage=None, est_prompt=est_prompt, est_completion=0,
                               latency_ms=int((time.monotonic() - t0) * 1000), status=502,
                               error=f"{type(e).__name__}: {e}")
        return None
