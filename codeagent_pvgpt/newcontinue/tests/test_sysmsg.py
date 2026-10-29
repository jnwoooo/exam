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


def _build(tmp_path, *, mode="normal", headless=False, format_json=False, config=None, rules=None, environ=None, snapshot=SNAP):
    """작업 폴더·전역 폴더를 임시 폴더로 두고 만든다."""
    return M.build_system_message(mode=mode, headless=headless, format_json=format_json, cwd=tmp_path / "w", global_dir=tmp_path / "gd",
                                  config=config, additional_rules=rules, environ=environ if environ is not None else {}, snapshot=snapshot)


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    (tmp_path / "w").mkdir()                                 # 작업 폴더


# ───────────────────────── 원문과 글자 단위 비교 ─────────────────────────
@pytest.mark.skipif(not SRC.exists(), reason="cn 소스(.ref) 없음")
def test_base_message_verbatim():
    """baseSystemMessage 템플릿을 원문에서 꺼내 같은 값으로 채우면 ncn 결과와 같다."""
    src = SRC.read_text(encoding="utf-8")
    tpl = re.search(r"const baseSystemMessage = `(.*?)`;", src, re.S).group(1)   # 템플릿 원문
    filled = (tpl.replace("${process.cwd()}", SNAP.cwd).replace("${isGitRepo()}", "false").replace("${process.platform}", SNAP.platform)
              .replace('${new Date().toISOString().split("T")[0]}', SNAP.today).replace("${getGitStatus()}", SNAP.git_status))
    assert M.format_base_system_message(SNAP) == filled


@pytest.mark.skipif(not SRC.exists(), reason="cn 소스(.ref) 없음")
def test_mode_blocks_verbatim():
    """planMode·commitSignature·headless·json 글이 원문과 같다."""
    src = SRC.read_text(encoding="utf-8")
    plan = re.search(r"systemMessage \+=\n\s*'(.*?)';", src, re.S).group(1)   # 작은따옴표 JS 글
    assert json.loads('"' + plan.replace('"', '\\"') + '"') == M.PLAN_MODE_CONTEXT
    templates = [t.replace("\\n", "\n") for t in re.findall(r"systemMessage \+= `(.*?)`;", src, re.S)]   # 백틱 글들(순서: commit, headless, json), \n 이스케이프 해석
    assert templates[0] == M.COMMIT_SIGNATURE_CONTEXT
    assert templates[1] == M.HEADLESS_INSTRUCTIONS
    assert templates[2] == M.JSON_INSTRUCTIONS


# ───────────────────────── systemMessage.test.ts 대표 사례 ─────────────────────────
def test_basic_components(tmp_path):
    r = _build(tmp_path)
    assert r.startswith("You are an agent in the Continue CLI")
    assert "<env>" in r and '<context name="gitStatus">' in r and "Working directory: /work" in r and "Platform: darwin" in r
    assert "Today's date: 2026-01-02" in r
    assert '<context name="commitSignature">' in r and '<context name="userRules">' not in r


def test_full_message_exact(tmp_path):
    """규칙 없는 normal 모드 전체 글."""
    assert _build(tmp_path) == M.format_base_system_message(SNAP) + M.COMMIT_SIGNATURE_CONTEXT


def test_plan_headless_json(tmp_path):
    r = _build(tmp_path, mode="plan", headless=True, format_json=True)
    assert r == M.format_base_system_message(SNAP) + M.PLAN_MODE_CONTEXT + M.HEADLESS_INSTRUCTIONS + M.JSON_INSTRUCTIONS
    assert "commitSignature" not in r


def test_commit_signature_env(tmp_path):
    assert "commitSignature" not in _build(tmp_path, environ={"CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE": "1"})
    assert "commitSignature" in _build(tmp_path, environ={"CONTINUE_CLI_DISABLE_COMMIT_SIGNATURE": ""})   # 빈 글은 끄지 않음


def test_agent_file_first_found(tmp_path):
    """AGENTS.md → AGENT.md → CLAUDE.md → CODEX.md 중 처음 것만."""
    (tmp_path / "w" / "CLAUDE.md").write_text("claude rules", encoding="utf-8")
    (tmp_path / "w" / "AGENT.md").write_text("agent rules", encoding="utf-8")
    r = _build(tmp_path)
    assert r.endswith('\n\n<context name="userRules">\nagent rules\n</context>')
    assert "claude rules" not in r


def test_rules_and_agent_separator(tmp_path):
    """agent 내용이 있으면 규칙 앞 구분은 빈 줄 둘, 없으면 줄바꿈 하나. 규칙끼리는 빈 줄로."""
    r = _build(tmp_path, rules=["rule one", "rule two"])
    assert r.endswith('<context name="userRules">\nrule one\n\nrule two\n</context>')
    (tmp_path / "w" / "AGENTS.md").write_text("A", encoding="utf-8")
    r = _build(tmp_path, rules=["rule one"])
    assert r.endswith('<context name="userRules">\nA\n\nrule one\n</context>')


def test_rule_duplication_like_cn(tmp_path):
    """--rule 글은 processRule 결과 + config.rules(ConfigService 가 넣은 것)로 두 번 들어간다(cn 그대로)."""
    (tmp_path / "w" / "c.yaml").write_text("name: t\nversion: \"1\"\nmodels:\n  - name: m\n    provider: o\n    model: x\nrules:\n  - from config\n",
                                           encoding="utf-8")
    cfg = load_config("./c.yaml", cwd=tmp_path / "w", global_dir=tmp_path / "gd", environ={"HOME": str(tmp_path)}, rules=["use tabs please"])
    r = _build(tmp_path, config=cfg, rules=["use tabs please"])
    assert r.endswith('<context name="userRules">\nuse tabs please\n\nuse tabs please\n\nfrom config\n</context>')


def test_rule_file_and_failure(tmp_path):
    """경로 모양 --rule 은 파일 내용(앞머리 포함 그대로), 없으면 경고 후 건너뜀."""
    (tmp_path / "w" / "r.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
    warnings: list[str] = []
    r = M.build_system_message(mode="normal", headless=False, format_json=False, cwd=tmp_path / "w", global_dir=tmp_path / "gd",
                               config=None, additional_rules=["./r.md", "./missing.md"], environ={}, snapshot=SNAP, warnings=warnings)
    assert r.endswith('<context name="userRules">\n---\nname: x\n---\nbody\n</context>')
    assert warnings == ['Warning: Failed to process rule "./missing.md": Failed to read rule file "./missing.md": Rule file not found: ./missing.md']


def test_markdown_rules_dedup(tmp_path):
    """.continue/rules 규칙은 이미 들어간 글과 같으면 빼고, 아니면 뒤에 붙는다."""
    d = tmp_path / "w" / ".continue" / "rules"
    d.mkdir(parents=True)
    (d / "a.md").write_text("same", encoding="utf-8")
    (d / "b.md").write_text("other", encoding="utf-8")
    r = _build(tmp_path, rules=["same"])
    body = r.split('<context name="userRules">\n', 1)[1]
    assert body.count("same") == 1 and "other" in body


def test_process_rule_plain_text_kept(tmp_path):
    """글 규칙은 앞뒤 공백까지 그대로."""
    assert M.process_rule("  keep spaces  ", tmp_path) == "  keep spaces  "
    assert M.process_rule("line1\nline2.md", tmp_path) == "line1\nline2.md"   # 줄바꿈이 있으면 경로로 보지 않음


# ───────────────────────── 스냅숏 ─────────────────────────
def test_snapshot_non_git(tmp_path):
    """git 저장소가 아니면 false / Not a git repository, 날짜는 UTC."""
    s = M.refresh_snapshot(tmp_path / "w")
    if s.is_git_repo:                                        # 임시 폴더가 git 안에 있는 특수한 환경
        pytest.skip("임시 폴더가 git 저장소 안에 있음")
    assert s.git_status == "Not a git repository"
    assert s.today == datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    assert s.platform in ("darwin", "linux", "win32")


def test_snapshot_git_repo(tmp_path):
    """git 저장소면 true, 변경 파일은 porcelain 형식(앞뒤 공백 trim), 깨끗하면 Working tree clean."""
    import shutil
    import subprocess
    if shutil.which("git") is None:
        pytest.skip("git 없음")
    w = tmp_path / "w"
    subprocess.run(["git", "init", "-q"], cwd=w, check=True)
    s = M.refresh_snapshot(w)
    assert s.is_git_repo is True and s.git_status == "Working tree clean"
    (w / "x.txt").write_text("x", encoding="utf-8")
    assert M.get_snapshot(w).git_status == "Working tree clean"   # 한 번 계산한 값은 그대로(cn 모듈 상수)
    assert M.refresh_snapshot(w).git_status == "?? x.txt"
