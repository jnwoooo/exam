# tests/test_subagent.py — 하위 에이전트(Subagent) 시험. cn 원본 subagent/executor.ts·tools/subagent.ts(시험 파일은 cn에 없음 — 동작 기준)
# 녹화형 가짜 LLM(parity/fake_llm.py)으로 부모·자식 응답을 재생한다. 동시에 도는 자식은 단계의 "when"(자기 시스템 메시지 표지)으로 자기 응답을 고른다.
# 확인: 독립 객체(기록·권한·시스템 메시지 안 섞임 — 부록B B16), ncn.yaml 도구 제한·결과 길이·동시 실행 수(추가 E), compat(cn 동작), 헤드리스 실제 실행, ACP 진행 표시·취소
from __future__ import annotations                           # 타입 주석 지연 평가

import asyncio                                               # 비동기 실행
import json                                                  # 요청 본문 글
import subprocess                                            # ncn -p 실행
import sys                                                   # 파이썬 실행 파일
import time                                                  # 취소 시각

import pytest                                                # 시험 도구

import ncn.llm.client as client_mod                          # 대기 없애기
import ncn.llm.retry as retry_mod                            # 대기 없애기
from ncn import subagent as sa                               # 시험 대상
from ncn.llm.client import LLMClient                         # 부모 LLM
from ncn.loop.agent import LoopDeps, run_turn                # 부모 루프
from ncn.loop.history import ChatHistory                     # 부모 기록
from ncn.permissions.service import PermissionService        # 부모 권한(바뀌지 않아야 함)
from ncn.tools.misc import make_subagent_tool                # Subagent 도구
from ncn.tools.read_file import READ_TOOL                    # 자식이 쓸 도구
from parity.fake_llm import serve                            # 녹화형 가짜 LLM
from parity.run import config_yaml, isolated_env             # 시험 설정·격리 환경

META_OK = "<task_metadata>\nstatus: completed\n</task_metadata>"   # 성공 꼬리
META_FAIL = "<task_metadata>\nstatus: failed\n</task_metadata>"    # 실패 꼬리


@pytest.fixture(autouse=True)                                # 모든 시험에 자동 적용
def no_sleep(monkeypatch):                                  # 재시도 대기를 없애는 픽스처
    """재시도 대기 없애기."""
    async def fast(_s):                                     # 기다리지 않는 대체 함수
        return None                                         # 아무것도 안 함
    monkeypatch.setattr(client_mod, "_sleep", fast)         # SDK 층 대기 대체
    monkeypatch.setattr(retry_mod, "_sleep", fast)          # CLI 층 대기 대체


def agent(name, marker, port, model="fake-child"):          # subagent 역할 모델(cn 모양 dict)
    """roles [subagent] + chatOptions.baseSystemMessage(표지 글 — 가짜 LLM 이 자식 요청을 고르는 데 씀)."""
    return {"name": name, "provider": "openai", "model": model, "apiBase": f"http://127.0.0.1:{port}/v1/", "apiKey": "k",   # 모델 연결
            "roles": ["subagent"], "chatOptions": {"baseSystemMessage": f"{marker} 탐색 전담"}}   # 역할·지시


def sub_call(name, prompt):                                 # 시나리오의 Subagent 호출 한 건
    """Subagent(description, prompt, subagent_name)."""
    return {"name": "Subagent", "arguments": {"description": f"{name} 조사", "prompt": prompt, "subagent_name": name}}   # 인자 셋


class Parent:                                               # 부모 대화 하나(헤드리스 루프 + 호스트)
    """가짜 LLM 앞에서 부모 루프를 돌린다. 자식 도구 = Read + Subagent(부모와 같은 목록)."""

    def __init__(self, tmp_path, steps, models, settings=None, session_id=""):   # 준비
        self.server, self.rec = serve(steps)                 # 가짜 LLM
        port = self.server.server_port                       # 포트
        self.models = [m(port) if callable(m) else m for m in models]   # 포트가 들어간 모델 설정
        self.history = ChatHistory(session_id=session_id)    # 부모 기록
        self.perms = PermissionService(mode="normal", headless=True, global_dir=tmp_path / "g")   # 부모 권한
        self.before = list(self.perms.policies)              # 시작 때 정책(나중에 비교)
        self.host = sa.SubagentHost(models=self.models, headless=True, tools=self.tools, system_message=lambda: "PARENT-SYS",   # 호스트
                                    parent_history=self.history, settings=settings or sa.NcnSettings())   # 부모 기록·ncn.yaml
        self.llm = LLMClient(api_base=f"http://127.0.0.1:{port}/v1/", api_key="k", model="fake-model")   # 부모 LLM
        self.deps = LoopDeps(llm=self.llm, tools=self.tools, check_permission=lambda t, a: self.perms.check(t, a),   # 부모 의존성
                             list_permission=lambda t, a: self.perms.base_permission(t.name, {}), ask_user=None,   # 목록·질문
                             system_message=lambda: "PARENT-SYS")   # 부모 시스템 메시지

    def tools(self):                                        # 부모 전체 도구 목록(getAllAvailableTools 자리)
        return [READ_TOOL, make_subagent_tool(self.models, host=self.host)]   # Read + Subagent

    def run(self, prompt="조사해줘"):                         # 한 턴 실행
        async def go():                                     # 비동기 본문
            try:                                            # LLM 닫기 보장
                return await run_turn(self.history, prompt, self.deps, headless=True, model_name="fake-model")   # 부모 한 턴
            finally:                                        # 끝나면
                await self.llm.aclose()                     # 닫기
        try:                                                # 서버 종료 보장
            return asyncio.run(asyncio.wait_for(go(), 30))  # 시간 한도 안에서
        finally:                                            # 끝나면
            self.server.shutdown()                          # 가짜 LLM 종료

    def bodies(self, marker=None):                          # 받은 요청 본문(표지가 있으면 그 자식 것만)
        out = [r["body"] for r in self.rec.requests]        # 전부
        if marker is None:                                  # 표지 없음
            return out                                      # 전부
        return [b for b in out if marker in b["messages"][0]["content"]]   # 시스템 메시지에 표지가 있는 것

    def tool_results(self):                                 # 부모 기록의 도구 결과(호출 id → 글)
        out = {}                                            # 결과
        for it in self.history.get_history():               # 항목마다
            for st in it.get("toolCallStates") or []:       # 도구 상태마다
                out[st["toolCall"]["function"]["name"] + ":" + st["toolCallId"]] = st["output"][0]["content"]   # 이름:id → 결과 글
        return out                                          # 표


# ─────────────────────────────────────────────────────────────────────────────
# 단위: ncn.yaml·권한·결과 글
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_ncn_settings_and_load(tmp_path):             # ncn.yaml 해석
    s = sa.parse_ncn_settings({"subagent_max_parallel": 3, "subagents": {"explorer": {"tools": ["Read", "search_*"], "max_result_chars": 100},   # 정상 항목
                                                                          "bad": {"tools": "Read", "max_result_chars": 0}, "x": 5}})   # 틀린 항목
    assert s.subagent_max_parallel == 3                     # 동시 실행 수
    assert s.subagents["explorer"] == sa.SubagentLimits(tools=["Read", "search_*"], max_result_chars=100)   # 정상 반영
    assert s.subagents["bad"] == sa.SubagentLimits()         # 틀린 값은 그 값만 무시
    assert "x" not in s.subagents                            # 사전이 아닌 항목은 버림
    assert sa.parse_ncn_settings({"subagent_max_parallel": True}).subagent_max_parallel == 2   # 참거짓은 정수 아님 → 기본 2
    assert sa.parse_ncn_settings(["a"]) == sa.NcnSettings()  # 최상위가 목록이면 기본값
    assert sa.load_ncn_settings(tmp_path) == sa.NcnSettings()   # 파일 없음 → 기본(cn과 같게)
    (tmp_path / "ncn.yaml").write_text("subagents:\n  runner:\n    tools: [Bash, Read]\nsubagent_max_parallel: 1\n", encoding="utf-8")   # 파일 작성
    got = sa.load_ncn_settings(tmp_path)                     # 읽기
    assert got.subagent_max_parallel == 1 and got.subagents["runner"].tools == ["Bash", "Read"]   # 반영
    (tmp_path / "ncn.yaml").write_text("subagents: [\n", encoding="utf-8")   # 문법 오류
    assert sa.load_ncn_settings(tmp_path) == sa.NcnSettings()   # 오류 → 기본값(시작은 계속)


def test_subagent_permissions():                            # 하위 대화 권한
    class T:                                                # 가짜 도구
        def __init__(self, name):                           # 이름만
            self.name, self.evaluate_tool_call_policy = name, None   # 동적 평가 없음
    allow_all = sa.SubagentPermissions(None)                # cn: allow all
    assert allow_all.base_permission("Write") == "allow" and allow_all.check(T("Write"), {}) == "allow"   # 쓰기도 허용
    assert allow_all.check(T("Bash"), {"command": "rm -rf /"}) == "exclude"   # Bash 위험 명령은 여전히 차단(동적 평가)
    lim = sa.SubagentPermissions(["Read", "search_*", "Bash(npm test*)"])   # 제한 목록
    assert lim.base_permission("Read") == "allow" and lim.base_permission("search_code") == "allow"   # 이름·글롭
    assert lim.base_permission("Write") == "exclude"         # 목록 밖은 요청 목록에서 빠짐
    assert lim.base_permission("Bash") == "allow"            # 인자 패턴만 있어도 목록에는 보임
    assert lim.check(T("Bash"), {"command": "npm test -- -x"}) == "allow"   # 맞는 명령은 허용
    assert lim.check(T("Bash"), {"command": "ls"}) == "exclude"   # 다른 명령은 막힘


def test_format_and_truncate():                             # 결과 글
    assert sa.format_subagent_output("답", True) == "답\n" + META_OK   # 응답 + 꼬리
    assert sa.format_subagent_output("", True) == META_OK    # 빈 응답 줄은 빠짐(filter(Boolean))
    assert sa.format_subagent_output("", False) == META_FAIL   # 실패
    assert sa._truncate("abcdef", 3) == "abc\n[ncn: subagent result truncated to 3 characters]"   # 상한 적용
    assert sa._truncate("abc", 3) == "abc" and sa._truncate("abcdef", None) == "abcdef"   # 짧거나 상한 없음


def test_no_host_keeps_cn_error():                          # 호스트 없는 도구는 cn 헤드리스와 같은 오류
    t = make_subagent_tool([agent("explorer", "M", 9)])     # 호스트 없이
    with pytest.raises(Exception, match="^No active session found$"):   # cn 오류 글
        asyncio.run(t.run({"description": "d", "prompt": "p", "subagent_name": "explorer"}))   # 실행


# ─────────────────────────────────────────────────────────────────────────────
# 실행: 동시 2개(섞이지 않음)·도구 제한·결과 길이·동시 실행 수·compat
# ─────────────────────────────────────────────────────────────────────────────


def _two_children_steps(delay=0.4):                         # 부모가 자식 둘을 한 번에 부르는 시나리오
    """부모1(Subagent A·B) → 자식 A·B 각각 Read 한 번 + 답 → 부모2 답."""
    return [{"tool_calls": [sub_call("alpha", "a 를 읽어라"), sub_call("beta", "b 를 읽어라")]},   # 부모 1
            {"when": "ROLE-A", "delay": delay, "tool_calls": [{"name": "Read", "arguments": {"filepath": "A_FILE"}}]},   # 자식 A 1(느리게)
            {"when": "ROLE-B", "delay": delay, "tool_calls": [{"name": "Read", "arguments": {"filepath": "B_FILE"}}]},   # 자식 B 1(느리게)
            {"when": "ROLE-A", "content": "A-DONE"},          # 자식 A 2
            {"when": "ROLE-B", "content": "B-DONE-LONG"},     # 자식 B 2
            {"content": "부모 끝"}]                            # 부모 2


def _fill_paths(steps, tmp_path):                           # 시나리오의 파일 자리표시자를 실제 경로로
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"           # 자식 A·B 가 읽을 파일
    a.write_text("alpha secret\n", encoding="utf-8")        # A 파일
    b.write_text("beta secret\n", encoding="utf-8")         # B 파일
    text = json.dumps(steps, ensure_ascii=False).replace("A_FILE", str(a)).replace("B_FILE", str(b))   # 자리표시자 치환
    return json.loads(text)                                 # 치환된 시나리오


def test_two_concurrent_subagents_isolated(tmp_path):       # 동시 2개 — 기록·권한·시스템 메시지가 섞이지 않음
    settings = sa.NcnSettings(subagents={"alpha": sa.SubagentLimits(tools=["Read"]),   # A 는 Read 만
                                         "beta": sa.SubagentLimits(max_result_chars=6)})   # B 는 결과 6글자
    p = Parent(tmp_path, _fill_paths(_two_children_steps(), tmp_path),   # 부모 준비
               [lambda port: agent("alpha", "ROLE-A", port), lambda port: agent("beta", "ROLE-B", port)], settings)   # 자식 둘
    res = p.run()                                           # 실행
    assert res.response == "부모 끝"                         # 부모 최종 답
    kinds = ["A" if "ROLE-A" in b["messages"][0]["content"] else "B" if "ROLE-B" in b["messages"][0]["content"] else "P"   # 요청마다 누구 것인지
             for b in p.bodies()]                            # 순서대로
    assert kinds[0] == "P" and kinds[-1] == "P" and sorted(kinds[1:3]) == ["A", "B"]   # 자식 둘의 첫 요청이 둘째 요청보다 먼저 = 동시 실행
    for marker, other, secret, other_secret in (("ROLE-A", "ROLE-B", "alpha secret", "beta secret"),   # A 쪽 확인
                                                ("ROLE-B", "ROLE-A", "beta secret", "alpha secret")):   # B 쪽 확인
        bodies = p.bodies(marker)                            # 그 자식 요청 둘
        assert len(bodies) == 2                              # Read 한 번 + 답
        for b in bodies:                                     # 요청마다
            sysmsg = b["messages"][0]["content"]             # 시스템 메시지
            assert sysmsg == f"PARENT-SYS\n\n{marker} 탐색 전담"   # 부모 + 빈 줄 + baseSystemMessage(buildAgentSystemMessage)
            assert b["model"] == "fake-child"                 # 그 subagent 모델로 요청
            text = json.dumps(b["messages"], ensure_ascii=False)   # 메시지 전체
            assert other not in text and other_secret not in text and "조사해줘" not in text   # 다른 자식·부모 대화가 섞이지 않음
        assert bodies[0]["messages"][1] == {"role": "user", "content": "a 를 읽어라" if marker == "ROLE-A" else "b 를 읽어라"}   # 첫 메시지 = prompt
        assert secret in json.dumps(bodies[1]["messages"], ensure_ascii=False)   # 자기 Read 결과는 자기 대화에
    names = {m: [t["function"]["name"] for t in p.bodies(m)[0]["tools"]] for m in ("ROLE-A", "ROLE-B")}   # 자식 요청의 도구 목록
    assert names["ROLE-A"] == ["Read"]                       # ncn.yaml 제한: Read 만
    assert names["ROLE-B"] == ["Read", "Subagent"]           # 제한 없음: 부모와 같은 목록(cn: 중첩 Subagent 도 보임)
    results = p.tool_results()                               # 부모 기록의 도구 결과
    assert results["Subagent:call_0_0"] == "A-DONE\n" + META_OK   # A 결과
    assert results["Subagent:call_0_1"] == "B-DONE\n[ncn: subagent result truncated to 6 characters]\n" + META_OK   # B 결과(6글자)
    roles = [it["message"]["role"] for it in p.history.get_history()]   # 부모 기록 역할
    assert roles == ["user", "assistant", "assistant"]       # 자식 대화는 부모 기록에 안 들어감(도구 결과는 assistant 상태 안)
    assert p.perms.policies == p.before                      # 부모 권한 정책 그대로(cn: 전역 교체 → 동시 실행 시 allow-all 로 굳음)
    parent_bodies = [b for b in p.bodies() if b["messages"][0]["content"] == "PARENT-SYS"]   # 부모 요청
    assert len(parent_bodies) == 2                           # 부모 시스템 메시지도 그대로(오염 없음)


def test_max_parallel_one_runs_sequentially(tmp_path):      # subagent_max_parallel: 1 → 차례로
    settings = sa.NcnSettings(subagent_max_parallel=1)       # 한 번에 하나
    p = Parent(tmp_path, _fill_paths(_two_children_steps(delay=0.1), tmp_path),   # 부모 준비
               [lambda port: agent("alpha", "ROLE-A", port), lambda port: agent("beta", "ROLE-B", port)], settings)   # 자식 둘
    p.run()                                                  # 실행
    kinds = ["A" if "ROLE-A" in b["messages"][0]["content"] else "B" if "ROLE-B" in b["messages"][0]["content"] else "P"   # 요청 주인
             for b in p.bodies()]                            # 순서대로
    assert kinds in (["P", "A", "A", "B", "B", "P"], ["P", "B", "B", "A", "A", "P"])   # 한 자식이 끝난 뒤 다음 자식


def test_compat_mode_cn_behaviour(tmp_path, cn_compat_env):  # NCN_CN_COMPAT=1 — cn 동작
    steps = _fill_paths(_two_children_steps(delay=0), tmp_path)   # 같은 시나리오
    settings = sa.NcnSettings(subagents={"alpha": sa.SubagentLimits(tools=["Read"]), "beta": sa.SubagentLimits(max_result_chars=6)})   # ncn.yaml(무시돼야 함)
    p = Parent(tmp_path / "h", steps, [lambda port: agent("alpha", "ROLE-A", port), lambda port: agent("beta", "ROLE-B", port)], settings)   # 헤드리스(sessionId "")
    p.run()                                                  # 실행
    assert len(p.bodies()) == 2                              # 자식은 돌지 않음(부모 두 번만)
    assert set(p.tool_results().values()) == {"Error executing tool Subagent: No active session found"}   # cn 헤드리스 오류 글
    (tmp_path / "s").mkdir()                                 # 두 번째 시험 폴더
    p2 = Parent(tmp_path / "s", _fill_paths(_two_children_steps(delay=0), tmp_path / "s"),   # 세션 id 가 있는 부모(cn serve·대화형)
                [lambda port: agent("alpha", "ROLE-A", port), lambda port: agent("beta", "ROLE-B", port)], settings, session_id="S1")   # 자식 둘
    p2.run()                                                 # 실행
    assert [t["function"]["name"] for t in p2.bodies("ROLE-A")[0]["tools"]] == ["Read", "Subagent"]   # cn: allow all(제한 무시)
    assert p2.tool_results()["Subagent:call_0_1"] == "B-DONE-LONG\n" + META_OK   # cn: 결과 자르지 않음


def test_child_failure_reports_failed(tmp_path):            # 자식 LLM 오류 → status: failed
    steps = [{"tool_calls": [sub_call("alpha", "p")]}, {"when": "ROLE-A", "status": 400, "content": "bad"}, {"content": "끝"}]   # 자식 400
    p = Parent(tmp_path, steps, [lambda port: agent("alpha", "ROLE-A", port)])   # 준비
    p.run()                                                  # 실행
    assert p.tool_results()["Subagent:call_0_0"] == META_FAIL   # 빈 응답 + failed(cn 그대로)


# ─────────────────────────────────────────────────────────────────────────────
# 헤드리스 ncn -p: 기본은 실제 실행(cn 버그 수정), compat 는 cn 과 같은 실패
# ─────────────────────────────────────────────────────────────────────────────


CHILD_YAML = ("  - name: explorer\n    provider: openai\n    model: fake-child\n    apiBase: http://127.0.0.1:{port}/v1/\n"   # 설정에 덧붙일 subagent 모델
              "    apiKey: sk-parity\n    roles: [subagent]\n    chatOptions:\n      baseSystemMessage: ROLE-X 탐색 전담\n")   # 역할·지시


def _headless(tmp_path, steps, env_extra=None, ncn_yaml=None):   # ncn -p --beta-subagent-tool 실행
    """(stdout, 요청 본문들)."""
    server, rec = serve(steps)                               # 가짜 LLM
    work, cnhome = tmp_path / "work", tmp_path / "cnhome"     # 작업·전역 폴더
    for d in (work, cnhome, tmp_path / "home"):              # 폴더 만들기
        d.mkdir(parents=True, exist_ok=True)                 # 생성
    (work / "coupon.py").write_text("def discount(p):\n    return p\n", encoding="utf-8")   # 작업 파일
    if ncn_yaml:                                             # ncn.yaml 이 있으면
        (cnhome / "ncn.yaml").write_text(ncn_yaml, encoding="utf-8")   # 전역 폴더에
    cfg = cnhome / "config.yaml"                             # 설정
    cfg.write_text(config_yaml(server.server_port, {"config_append": CHILD_YAML.format(port=server.server_port)}), encoding="utf-8")   # chat + subagent
    env = {**isolated_env(tmp_path), **(env_extra or {})}    # 격리 환경
    try:                                                    # 서버 종료 보장
        proc = subprocess.run([sys.executable, "-m", "ncn", "-p", "찾아줘", "--config", str(cfg), "--beta-subagent-tool"],   # ncn -p
                              cwd=work, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)   # 실행
    finally:                                                # 끝나면
        server.shutdown()                                   # 서버 종료
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")   # 정상 종료
    return proc.stdout.decode("utf-8"), [r["body"] for r in rec.requests]   # 출력·요청


def test_headless_default_runs_subagent(tmp_path):          # [ncn 부록B B16] 헤드리스에서도 실제로 실행
    steps = [{"tool_calls": [sub_call("explorer", "discount 위치")]},   # 부모 1
             {"when": "ROLE-X", "tool_calls": [{"name": "Write", "arguments": {"filepath": "w.txt", "content": "x"}}]},   # 자식: Write(cn: allow all)
             {"when": "ROLE-X", "content": "coupon.py:1"},    # 자식 답
             {"content": "찾았습니다."}]                      # 부모 2
    out, bodies = _headless(tmp_path, steps)                 # 실행
    assert out == "찾았습니다.\n"                            # 부모 최종 답
    assert len(bodies) == 4                                  # 부모·자식·자식·부모
    child = bodies[1]                                        # 자식 첫 요청
    assert child["model"] == "fake-child" and child["messages"][0]["content"].endswith("\n\nROLE-X 탐색 전담")   # 그 모델·시스템 메시지
    assert "Write" in [t["function"]["name"] for t in child["tools"]]   # 헤드리스 부모는 Write 를 못 보지만 자식은 allow all(cn 그대로)
    assert "Write" not in [t["function"]["name"] for t in bodies[0]["tools"]]   # 부모 목록은 그대로
    assert (tmp_path / "work" / "w.txt").read_text(encoding="utf-8") == "x"   # 자식이 실제로 씀
    last = bodies[3]["messages"][-1]                         # 부모 둘째 요청의 도구 결과
    assert last == {"role": "tool", "content": "coupon.py:1\n" + META_OK, "tool_call_id": "call_0_0"}   # 결과 글


def test_headless_ncn_yaml_restricts_tools(tmp_path):       # [추가 E] ncn.yaml 로 자식 도구 제한
    steps = [{"tool_calls": [sub_call("explorer", "p")]},     # 부모 1
             {"when": "ROLE-X", "tool_calls": [{"name": "Write", "arguments": {"filepath": "w.txt", "content": "x"}}]},   # 자식이 목록 밖 도구를 부름
             {"content": "끝"}]                                # 부모 2
    out, bodies = _headless(tmp_path, steps, ncn_yaml="subagents:\n  explorer:\n    tools: [Read, List]\n")   # Read·List 만
    assert [t["function"]["name"] for t in bodies[1]["tools"]] == ["Read", "List"]   # 자식 요청 도구 목록
    assert len(bodies) == 3                                  # 헤드리스 거부 → 자식 턴은 바로 끝(cn 헤드리스 규칙 그대로) → 부모 둘째
    assert bodies[2]["messages"][-1]["content"] == META_OK   # 자식 마지막 글이 비어 꼬리만
    assert not (tmp_path / "work" / "w.txt").exists()        # 막혀서 안 씀


def test_headless_compat_fails_like_cn(tmp_path):           # NCN_CN_COMPAT=1 → cn 헤드리스와 같은 실패(차등 25_subagent_compat)
    steps = [{"tool_calls": [sub_call("explorer", "p")]}, {"content": "실패"}]   # 부모만
    out, bodies = _headless(tmp_path, steps, env_extra={"NCN_CN_COMPAT": "1"})   # compat
    assert len(bodies) == 2                                  # 자식 요청 없음
    assert bodies[1]["messages"][-1]["content"] == "Error executing tool Subagent: No active session found"   # cn 오류 글


# ─────────────────────────────────────────────────────────────────────────────
# ACP: 카드 안 진행 표시·취소 시 하위 대화도 중단
# ─────────────────────────────────────────────────────────────────────────────


def _acp(tmp_path, steps):                                  # ACP 하네스(설정에 subagent 모델 추가)
    from tests.test_acp import FakeIDE, Harness              # 가짜 IDE·자식 프로세스 하네스
    h = Harness(tmp_path, steps, args=["--beta-subagent-tool"])   # 준비
    h.cfg.write_text(config_yaml(h.server.server_port, {"config_append": CHILD_YAML.format(port=h.server.server_port)}), encoding="utf-8")   # chat + subagent
    return h, FakeIDE()                                      # 하네스·IDE


def test_acp_progress_in_card(tmp_path):                    # 하위 대화 출력이 부모 카드 content·_meta 로
    from acp.helpers import text_block                       # 질문 블록
    steps = [{"tool_calls": [sub_call("explorer", "p")]},     # 부모 1
             {"when": "ROLE-X", "content": "child says hi", "tool_calls": [{"name": "List", "arguments": {"dirpath": "."}}]},   # 자식 1: 글 + List
             {"when": "ROLE-X", "delay": 0.4, "content": "done"},   # 자식 2(느림 — 그 사이 진행 표시가 나감)
             {"content": "끝"}]                                # 부모 2
    h, ide = _acp(tmp_path, steps)                           # 준비

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        r = await h.conn.prompt(session_id=s.session_id, prompt=[text_block("찾아줘")])   # 질문
        assert r.stop_reason == "end_turn"                   # 정상 끝
        await h.stop()                                       # 종료

    asyncio.run(asyncio.wait_for(go(), 60))                  # 실행
    assert ide.permissions and "Spawning explorer to: explorer 조사" in json.dumps(ide.permissions, ensure_ascii=False)   # 대화형은 Subagent 승인(미리보기 글)
    prog = [u for u in ide.of("tool_call_update") if (u.get("_meta") or {}).get("ncn", {}).get("subagent")]   # 진행 표시
    assert prog and prog[0]["status"] == "in_progress" and prog[0]["_meta"]["ncn"]["subagent"]["name"] == "explorer"   # 이름
    assert "child says hi" in prog[-1]["content"][0]["content"]["text"]   # 하위 출력이 카드 내용에
    done = ide.of("tool_call_update")[-1]                    # 끝 결과
    assert done["status"] == "completed" and done["content"][0]["content"]["text"] == "done\n" + META_OK   # 결과 글(마지막 메시지)
    assert h.bodies()[1]["messages"][0]["content"].endswith("\n\nROLE-X 탐색 전담")   # 자식 요청


def test_acp_cancel_stops_subagent(tmp_path):               # session/cancel → 하위 대화도 중단
    from acp.helpers import text_block                       # 질문 블록
    steps = [{"tool_calls": [sub_call("explorer", "p")]},     # 부모 1
             {"when": "ROLE-X", "delay": 5, "content": "late"},   # 자식: 느림(취소될 것)
             {"when": "ROLE-X", "content": "should not be asked"},   # 자식 둘째(불리면 안 됨)
             {"content": "끝"}]                                # 부모 2(불리면 안 됨)
    h, ide = _acp(tmp_path, steps)                           # 준비

    async def go():                                          # 시나리오
        await h.start(ide)                                   # 시작
        s = await h.new()                                    # 세션
        task = asyncio.ensure_future(h.conn.prompt(session_id=s.session_id, prompt=[text_block("찾아줘")]))   # 질문
        for _ in range(300):                                 # 자식 요청이 올 때까지
            if len(h.rec.requests) >= 2:                     # 부모 1 + 자식 1
                break                                       # 취소하러
            await asyncio.sleep(0.02)                        # 잠깐
        assert len(h.rec.requests) == 2                      # 자식이 돌기 시작함
        t0 = time.monotonic()                                # 시각
        await h.conn.cancel(session_id=s.session_id)         # session/cancel
        r = await task                                       # 응답
        assert r.stop_reason == "cancelled" and time.monotonic() - t0 < 2.5   # 바로 cancelled(자식 응답 5초를 기다리지 않음)
        await asyncio.sleep(0.3)                             # 혹시 더 요청하는지
        await h.stop()                                       # 종료

    asyncio.run(asyncio.wait_for(go(), 60))                  # 실행
    assert len(h.rec.requests) == 2                          # 자식·부모 모두 더 요청하지 않음
