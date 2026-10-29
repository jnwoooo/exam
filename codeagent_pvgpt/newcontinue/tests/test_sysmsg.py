# tests/test_sysmsg.py — ncn/sysmsg.py 시험. cn systemMessage.test.ts 대표 사례 + 원문 글자 비교(.ref 의 systemMessage.ts 가 있으면)
from __future__ import annotations                           # 타입 주석 지연 평가

import datetime                                              # UTC 날짜
import json                                                  # JS 작은따옴표 글 해석
import pathlib                                               # 경로
import re                                                    # 원문 추출

import pytest                                                # 시험 도구

from ncn import sysmsg as M                                  # 대상
from ncn.config.loader import load_config                    # 설정

SNAP = M.EnvSnapshot(cwd="/work", is_git_repo=False, platform="darwin", today="2026-01-02", git_status="Not a git repository")   # 고정 스냅숏
SRC = pathlib.Path(__file__).resolve().parents[1] / ".ref" / "continue" / "extensions" / "cli" / "src" / "systemMessage.ts"   # cn 원문


def _build(tmp_path, *, mode="normal", headless=False, format_json=False, config=None, rules=None, environ=None, snapshot=SNAP):  # 시스템 메시지 조립 도우미 함수
    """작업 폴더·전역 폴더를 임시 폴더로 두고 만든다."""
    return M.build_system_message(mode=mode, headless=headless, format_json=format_json, cwd=tmp_path / "w", global_dir=tmp_path / "gd",  # 임시 작업 폴더·전역 폴더로 조립 호출
                                  config=config, additional_rules=rules, environ=environ if environ is not None else {}, snapshot=snapshot)  # (인자 이어짐)


@pytest.fixture(autouse=True)                               # 모든 시험에 자동 적용되는 픽스처
def _dirs(tmp_path):                                        # 작업 폴더 준비 함수
    (tmp_path / "w").mkdir()                                 # 작업 폴더


# ───────────────────────── 원문과 글자 단위 비교 ─────────────────────────
@pytest.mark.skipif(not SRC.exists(), reason="cn 소스(.ref) 없음")  # 원문이 없으면 건너뜀
def test_base_message_verbatim():                           # 기본 메시지 원문 비교 시험
    """baseSystemMessage 템플릿을 원문에서 꺼내 같은 값으로 채우면 ncn 결과와 같다."""
    src = SRC.read_text(encoding="utf-8")                   # cn 원문 파일 읽기
    tpl = re.search(r"const baseSystemMessage = `(.*?)`;", src, re.S).group(1)   # 템플릿 원문
    filled = (tpl.replace("${process.cwd()}", SNAP.cwd).replace("${isGitRepo()}", "false").replace("${process.platform}", SNAP.platform)  # 템플릿의 ${…} 자리를 스냅숏 값으로 채움
              .replace('${new Date().toISOString().split("T")[0]}', SNAP.today).replace("${getGitStatus()}", SNAP.git_status))  # (치환 이어짐)
    assert M.format_base_system_message(SNAP) == filled     # ncn 결과와 같은지 확인


@pytest.mark.skipif(not SRC.exists(), reason="cn 소스(.ref) 없음")  # 원문이 없으면 건너뜀
def test_mode_blocks_verbatim():                            # 모드별 문구 원문 비교 시험
    """planMode·commitSignature·headless·json 글이 원문과 같다."""
    src = SRC.read_text(encoding="utf-8")                   # cn 원문 파일 읽기
    plan = re.search(r"systemMessage \+=\n\s*'(.*?)';", src, re.S).group(1)   # 작은따옴표 JS 글
    assert json.loads('"' + plan.replace('"', '\\"') + '"') == M.PLAN_MODE_CONTEXT  # plan 문구가 같은지 확인
    templates = [t.replace("\\n", "\n") for t in re.findall(r"systemMessage \+= `(.*?)`;", src, re.S)]   # 백틱 글들(순서: commit, headless, json), \n 이스케이프 해석
    assert templates[0] == M.COMMIT_SIGNATURE_CONTEXT       # 커밋 서명 문구 비교
    assert templates[1] == M.HEADLESS_INSTRUCTIONS          # 헤드리스 문구 비교
    assert templates[2] == M.JSON_INSTRUCTIONS              # JSON 문구 비교


# ───────────────────────── systemMessage.test.ts 대표 사례 ─────────────────────────
def test_basic_components(tmp_path):                        # 기본 구성 요소 시험
    r = _build(tmp_path)                                    # 기본 조립
    assert r.startswith("You are an agent in the Continue CLI")  # 시작 문구 확인
    assert "<env>" in r and '<context name="gitStatus">' in r and "Working directory: /work" in r and "Platform: darwin" in r  # env·gitStatus 블록과 작업 폴더·플랫폼 확인
    assert "Today's date: 2026-01-02" in r                  # 날짜 확인
    assert '<context name="commitSignature">' in r and '<context name="userRules">' not in r  # 커밋 서명은 있고 userRules 는 없음


def test_full_message_exact(tmp_path):                      # 규칙 없는 normal 모드 전체 글 시험
    """규칙 없는 normal 모드 전체 글."""
    assert _build(tmp_path) == M.format_base_system_message(SNAP) + M.COMMIT_SIGNATURE_CONTEXT  # 기본 부분 + 커밋 서명과 같은지 확인


def test_plan_headless_json(tmp_path):                      # plan·headless·json 조합 시험
    r = _build(tmp_path, mode="plan", headless=True, format_json=True)  # 세 옵션을 모두 켬
    assert r == M.format_base_system_message(SNAP) + M.PLAN_MODE_CONTEXT + M.HEADLESS_INSTRUCTIONS + M.JSON_INSTRUCTIONS  # 문구들이 순서대로 이어짐
    assert "commitSignature" not in r                       # plan 모드는 커밋 서명이 없음


def test_commit_signature_env(tmp_path):                    # 커밋 서명 환경변수 시험
    assert "commitSignature" not in _build(tmp_path, environ={"CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE": "1"})  # 값이 있으면 서명 끔
    assert "commitSignature" in _build(tmp_path, environ={"CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE": ""})   # 빈 글은 끄지 않음


def test_agent_file_first_found(tmp_path):   # 에이전트 파일 우선순위 시험
    """AGENTS.md → AGENT.md → CLAUDE.md → CODEX.md 중 처음 것만."""
    (tmp_path / "w" / "CLAUDE.md").write_text("claude rules", encoding="utf-8")  # CLAUDE.md 작성
    (tmp_path / "w" / "AGENT.md").write_text("agent rules", encoding="utf-8")  # AGENT.md 작성
    r = _build(tmp_path)                                    # 조립
    assert r.endswith('\n\n<context name="userRules">\nagent rules\n</context>')  # AGENT.md(앞선 파일)만 쓰임
    assert "claude rules" not in r                          # CLAUDE.md 는 쓰이지 않음


def test_rules_and_agent_separator(tmp_path):               # 규칙·에이전트 파일 구분자 시험
    """agent 내용이 있으면 규칙 앞 구분은 빈 줄 둘, 없으면 줄바꿈 하나. 규칙끼리는 빈 줄로."""
    r = _build(tmp_path, rules=["rule one", "rule two"])    # 규칙만 있을 때
    assert r.endswith('<context name="userRules">\nrule one\n\nrule two\n</context>')  # 줄바꿈 하나로 시작, 규칙끼리 빈 줄
    (tmp_path / "w" / "AGENTS.md").write_text("A", encoding="utf-8")  # AGENTS.md 작성
    r = _build(tmp_path, rules=["rule one"])                # 규칙 하나 추가
    assert r.endswith('<context name="userRules">\nA\n\nrule one\n</context>')  # 에이전트 내용과 규칙 사이 빈 줄


def test_rule_duplication_like_cn(tmp_path):                # --rule 중복 시험
    """--rule 글은 processRule 결과 + config.rules(ConfigService 가 넣은 것)로 두 번 들어간다(cn 그대로)."""
    (tmp_path / "w" / "c.yaml").write_text("name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: o\n    model: x\nrules:\n  - from config\n",  # 설정 파일 작성(규칙 하나 포함)
                                           encoding="utf-8")  # (파일 인코딩)
    cfg = load_config("./c.yaml", cwd=tmp_path / "w", global_dir=tmp_path / "gd", environ={"HOME": str(tmp_path)}, rules=["use tabs please"])  # 설정 읽기(--rule 포함)
    r = _build(tmp_path, config=cfg, rules=["use tabs please"])  # 조립
    assert r.endswith('<context name="userRules">\nuse tabs please\n\nuse tabs please\n\nfrom config\n</context>')  # --rule 글이 두 번 들어감


def test_rule_file_and_failure(tmp_path):                   # 규칙 파일 읽기와 실패 시험
    """경로 모양 --rule 은 파일 내용(앞머리 포함 그대로), 없으면 경고 후 건너뜀."""
    (tmp_path / "w" / "r.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")  # 규칙 파일 작성
    warnings: list[str] = []                                # 경고 모음
    r = M.build_system_message(mode="normal", headless=False, format_json=False, cwd=tmp_path / "w", global_dir=tmp_path / "gd",  # 파일 규칙과 없는 파일 규칙으로 조립
                               config=None, additional_rules=["./r.md", "./missing.md"], environ={}, snapshot=SNAP, warnings=warnings)  # (인자 이어짐)
    assert r.endswith('<context name="userRules">\n---\nname: x\n---\nbody\n</context>')  # 파일 내용이 그대로 들어감
    assert warnings == ['Warning: Failed to process rule "./missing.md": Failed to read rule file "./missing.md": Rule file not found: ./missing.md']  # 없는 파일은 경고 글


def test_markdown_rules_dedup(tmp_path):                    # 마크다운 규칙 중복 제거 시험
    """.continue/rules 규칙은 이미 들어간 글과 같으면 빼고, 아니면 뒤에 붙는다."""
    d = tmp_path / "w" / ".continue" / "rules"              # 규칙 폴더 경로
    d.mkdir(parents=True)                                   # 폴더 생성
    (d / "a.md").write_text("same", encoding="utf-8")       # --rule 과 같은 내용의 규칙 파일
    (d / "b.md").write_text("other", encoding="utf-8")      # 다른 내용의 규칙 파일
    r = _build(tmp_path, rules=["same"])                    # 조립
    body = r.split('<context name="userRules">\n', 1)[1]    # userRules 본문만 추출
    assert body.count("same") == 1 and "other" in body      # 같은 글은 한 번, 다른 글은 포함


def test_process_rule_plain_text_kept(tmp_path):            # 글 규칙 공백 유지 시험
    """글 규칙은 앞뒤 공백까지 그대로."""
    assert M.process_rule("  keep spaces  ", tmp_path) == "  keep spaces  "  # 앞뒤 공백까지 그대로
    assert M.process_rule("line1\nline2.md", tmp_path) == "line1\nline2.md"   # 줄바꿈이 있으면 경로로 보지 않음


# ───────────────────────── 스냅숏 ─────────────────────────
def test_snapshot_non_git(tmp_path):                        # git 이 아닌 폴더 스냅숏 시험
    """git 저장소가 아니면 false / Not a git repository, 날짜는 UTC."""
    s = M.refresh_snapshot(tmp_path / "w")                  # 스냅숏 새로 계산
    if s.is_git_repo:                                        # 임시 폴더가 git 안에 있는 특수한 환경
        pytest.skip("임시 폴더가 git 저장소 안에 있음")                 # git 저장소 안이면 건너뜀
    assert s.git_status == "Not a git repository"           # git 상태 글 확인
    assert s.today == datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")  # 날짜가 UTC 인지 확인
    assert s.platform in ("darwin", "linux", "win32")       # 플랫폼 값 확인


def test_snapshot_git_repo(tmp_path):                       # git 저장소 스냅숏 시험
    """git 저장소면 true, 변경 파일은 porcelain 형식(앞뒤 공백 trim), 깨끗하면 Working tree clean."""
    import shutil                                           # shutil 가져오기
    import subprocess                                       # subprocess 가져오기
    if shutil.which("git") is None:                         # git 이 없으면
        pytest.skip("git 없음")                               # 건너뜀
    w = tmp_path / "w"                                      # 임시 작업 폴더
    subprocess.run(["git", "init", "-q"], cwd=w, check=True)  # git 저장소로 초기화
    s = M.refresh_snapshot(w)                               # 스냅숏 새로 계산
    assert s.is_git_repo is True and s.git_status == "Working tree clean"  # 저장소이며 깨끗함
    (w / "x.txt").write_text("x", encoding="utf-8")         # 새 파일 만듦
    assert M.get_snapshot(w).git_status == "Working tree clean"   # 한 번 계산한 값은 그대로(cn 모듈 상수)
    assert M.refresh_snapshot(w).git_status == "?? x.txt"   # 새로 계산하면 변경 파일이 보임
