# agent_server/admin.py — 감시 화면(/admin)과 집계 API(/admin/api/summary). 관리자(is_admin) 개인 토큰으로만
from fastapi import APIRouter, Request                       # 라우터
from fastapi.responses import HTMLResponse, JSONResponse     # 응답

from agent_server import auth                                # 인증


def make_router(get_state) -> APIRouter:
    router = APIRouter()

    async def rows(sql: str, params: tuple = ()) -> list[dict]:
        """SQL 실행 → [{열: 값}] (시각은 문자열로)."""
        async with get_state().db.connection() as con:
            cur = await con.execute(sql, params)
            cols = [c.name for c in cur.description]         # 열 이름
            return [{k: (v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v)   # 시각 → 문자열
                     for k, v in zip(cols, r)} for r in await cur.fetchall()]

    @router.get("/admin/api/summary")
    async def summary(request: Request, days: int = 7):
        st = get_state()
        user, is_admin = await auth.lookup(st, auth.bearer(request.headers))   # 관리자 확인
        if not is_admin:
            return JSONResponse({"error": "관리자 토큰이 필요합니다"}, status_code=403)
        days = max(1, min(days, 90))                         # 1~90일
        since = (f"{days} days",)                            # SQL interval 값
        limit = {"rpm": st.settings.limit_rpm, "daily_tokens": st.settings.limit_daily_tokens}   # 현재 한도
        by_user = await rows("""
            SELECT user_id, count(*) AS requests,
                   sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS completion_tokens,
                   sum(total_tokens) AS total_tokens,
                   sum(CASE WHEN ts >= date_trunc('day', now()) THEN total_tokens ELSE 0 END) AS today_tokens,
                   sum(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
                   sum(CASE WHEN estimated THEN 1 ELSE 0 END) AS estimated,
                   round(avg(latency_ms)) AS avg_ms
            FROM llm_usage WHERE ts >= now() - %s::interval
            GROUP BY user_id ORDER BY total_tokens DESC""", since)          # 사용자별 사용량
        by_day = await rows("""
            SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS day, source, count(*) AS requests,
                   sum(total_tokens) AS total_tokens
            FROM llm_usage WHERE ts >= now() - %s::interval
            GROUP BY 1, 2 ORDER BY 1 DESC, 2""", since)                     # 일별·출처별(채팅/request_fix)
        by_tool = await rows("""
            SELECT tool, count(*) AS calls, sum(CASE WHEN ok THEN 0 ELSE 1 END) AS failures,
                   round(avg(latency_ms)) AS avg_ms
            FROM tool_calls WHERE ts >= now() - %s::interval
            GROUP BY tool ORDER BY calls DESC""", since)                    # 도구별 호출·실패·지연
        recent_tools = await rows("SELECT ts, user_id, tool, latency_ms, ok, detail FROM tool_calls "
                                  "ORDER BY ts DESC LIMIT 30")              # 최근 도구 호출
        recent_errors = await rows("SELECT ts, user_id, source, status, error FROM llm_usage "
                                   "WHERE status >= 400 ORDER BY ts DESC LIMIT 20")   # 최근 LLM 오류
        index_runs = await rows("SELECT ts, repo, commit_sha, sent_files, deleted, full_index, status "
                                "FROM index_runs ORDER BY ts DESC LIMIT 10")   # 최근 색인 작업
        return {"days": days, "limits": limit, "by_user": by_user, "by_day": by_day, "by_tool": by_tool,
                "recent_tools": recent_tools, "recent_errors": recent_errors, "index_runs": index_runs}

    @router.get("/admin", response_class=HTMLResponse)
    async def page():
        return ADMIN_HTML                                    # 화면은 정적 HTML — 데이터는 위 API에서 토큰으로 가져온다

    return router


# 감시 화면: 토큰 입력 → /admin/api/summary 호출 → 표로 표시. 외부 라이브러리 없이(폐쇄망)
ADMIN_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>코드에이전트 감시</title>
<style>
:root{--bg:#f7f7f8;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e3e6;--bad:#c62828;--accent:#2457c5}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#ececef;--muted:#9a9aa1;--card:#1d1d20;--line:#333338;--bad:#ef6b6b;--accent:#7aa2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:8px 0 16px}h2{font-size:15px;margin:0 0 8px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
input,select,button{font:inherit;padding:6px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
button{background:var(--accent);color:#fff;border:none;cursor:pointer}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin-bottom:12px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:500}td.num{text-align:right;font-variant-numeric:tabular-nums}
.bad{color:var(--bad)}.muted{color:var(--muted)}
</style></head><body><main>
<h1>코드에이전트 감시</h1>
<div class="bar"><input id="tok" type="password" placeholder="관리자 개인 토큰" size="36">
<select id="days"><option>1</option><option selected>7</option><option>30</option></select>
<button onclick="load()">조회</button><span id="msg" class="muted"></span></div>
<div id="out"></div></main>
<script>
const $=s=>document.querySelector(s);
try{$('#tok').value=sessionStorage.getItem('tok')||''}catch(e){}
const esc=v=>String(v??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=v=>typeof v==='number'?v.toLocaleString():esc(v);
function table(title,rows,cols){
  if(!rows.length)return `<div class="card"><h2>${title}</h2><span class="muted">기록 없음</span></div>`;
  cols=cols||Object.keys(rows[0]);
  return `<div class="card"><h2>${title}</h2><table><tr>${cols.map(c=>`<th>${c}</th>`).join('')}</tr>`+
    rows.map(r=>`<tr>${cols.map(c=>{const v=r[c];const bad=(c==='ok'&&v===false)||(c==='status'&&v>=400&&typeof v==='number');
      return `<td class="${typeof v==='number'?'num':''} ${bad?'bad':''}">${fmt(v)}</td>`}).join('')}</tr>`).join('')+`</table></div>`;
}
async function load(){
  const tok=$('#tok').value.trim();try{sessionStorage.setItem('tok',tok)}catch(e){}
  $('#msg').textContent='불러오는 중…';
  const r=await fetch('/admin/api/summary?days='+$('#days').value,{headers:{Authorization:'Bearer '+tok}});
  const d=await r.json();
  if(!r.ok){$('#msg').textContent=d.error||r.status;$('#out').innerHTML='';return}
  $('#msg').textContent=`최근 ${d.days}일 · 한도: 분당 ${d.limits.rpm}회, 하루 ${d.limits.daily_tokens.toLocaleString()} 토큰`;
  $('#out').innerHTML=table('사용자별 토큰',d.by_user)+table('일별(chat=Continue 채팅, request_fix=도구 안쪽)',d.by_day)+
    table('도구별 호출',d.by_tool)+table('최근 도구 호출',d.recent_tools)+table('최근 LLM 오류',d.recent_errors)+table('최근 색인 작업',d.index_runs);
}
</script></body></html>"""
