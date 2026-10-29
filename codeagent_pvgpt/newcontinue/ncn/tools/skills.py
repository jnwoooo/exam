# ncn/tools/skills.py — Skills 도구(SKILL.md 읽기). cn extensions/cli/src/tools/skills.ts + util/loadMarkdownSkills.ts
# 찾는 곳(순서): <cwd>/.continue/skills/*/SKILL.md → <cwd>/.claude/skills/*/SKILL.md → <전역 폴더(~/.continue)>/skills/*/SKILL.md
# 머리말(---)의 name·description 이 둘 다 있는 것만 기술로 인정. 도구 설명에 기술 목록이 들어가므로 목록을 만들 때마다 디스크를 다시 읽는다(cn과 같음)
from __future__ import annotations                           # 타입 주석 지연 평가

import fnmatch                                               # .ignore 규칙(간이 minimatch)
import os                                                    # 폴더 훑기
import re                                                    # 머리말 나누기
from dataclasses import dataclass, field                     # 자료
from typing import Optional                                  # 타입

import yaml                                                  # 머리말 YAML

from ncn.tools.base import ContinueError, PreprocessResult, Tool, ToolCallPreview   # 공용 형식
from ncn.tools.formatters import JS_WS_CLASS, js_trim, path_dirname, path_join, path_relative   # Node path·JS trim 호환

SKILLS_DIR = "skills"                                        # 하위 폴더 이름


@dataclass                                                  # 자료 클래스로 선언
class Skill:                                                # 기술 하나의 정보
    """Skill — 이름·설명·경로·본문·부속 파일."""
    name: str                                                # 이름(머리말)
    description: str                                         # 설명(머리말)
    path: str                                                # SKILL.md 경로(cwd 안이면 상대)
    content: str                                             # 본문(머리말 뺀 것)
    files: list[str] = field(default_factory=list)           # 같은 폴더의 다른 파일들


# ───────────────────────── YAML 1.2 흉내(cn은 npm 'yaml' 2.x 사용) ─────────────────────────
class _Yaml12Loader(yaml.SafeLoader):                       # YAML 1.1 규칙을 걷어낸 로더 클래스
    """PyYAML(1.1)은 yes/no/on/off 를 참·거짓, 2024-01-01 을 날짜로 읽는다 — npm yaml(1.2 core)처럼 글로 남긴다."""


_Yaml12Loader.yaml_implicit_resolvers = {                    # 클래스 전용 복사본
    k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]  # bool·timestamp 규칙만 뺀 나머지 해석 규칙 유지
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()  # 부모 로더의 규칙 표를 글자별로 훑음
}                                                           # 복사본 표 끝
_Yaml12Loader.add_implicit_resolver(                         # 참·거짓은 true/false 만(1.2 core)
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))  # true/false 만 참·거짓으로 인정하는 규칙 추가


def _yaml_parse(text: str):                                 # 머리말 YAML을 읽는 함수
    """YAML.parse — 실패하면 예외."""
    return yaml.load(text, Loader=_Yaml12Loader)             # 안전 로더 기반


# JS /^---\s*$/m — 줄 시작(문자열 시작 또는 줄 바꿈 글자 뒤) ~ 줄 끝. JS 의 줄 바꿈 글자는 \n \r
_LT = "\n\r\u2028\u2029"                                     # JS 줄 끝 글자
_FRONT_SPLIT = re.compile(r"(?:(?<![\s\S])|(?<=[" + _LT + r"]))---" + JS_WS_CLASS + r"*(?=[" + _LT + r"]|(?![\s\S]))")   # \s* 는 욕심쟁이(JS와 같게 되돌아가며 맞춤)


def _js_split_frontmatter(s: str) -> list[str]:             # 머리말 기준 분할 함수(JS split 흉내)
    """normalizedContent.split(/^---\\s*$/m) — JS 분할(빈 일치는 이 패턴에선 생기지 않음)."""
    parts: list[str] = []                                    # 결과
    pos = 0                                                  # 다음 시작
    for m in _FRONT_SPLIT.finditer(s):                       # 일치마다
        parts.append(s[pos:m.start()])                       # 앞 조각
        pos = m.end()                                        # 다음
    parts.append(s[pos:])                                    # 마지막 조각
    return parts                                            # 조각 목록 반환


def parse_markdown_rule(content: str) -> tuple[dict, str]:  # 마크다운 규칙 파일을 (머리말, 본문)으로 나누는 함수
    """@continuedev/config-yaml parseMarkdownRule — (머리말, 본문). 머리말이 깨지면 ({}, 원문)."""
    normalized = content.replace("\r\n", "\n")               # 줄 끝 통일
    parts = _js_split_frontmatter(normalized)                # --- 로 나눔
    if len(parts) >= 3:                                      # 앞·머리말·본문
        frontmatter_str = parts[1]                           # 머리말 글
        markdown_content = "---".join(parts[2:])             # 본문(안의 --- 는 다시 이음)
        try:                                                # YAML 파싱은 실패할 수 있어 감쌈
            fm = _yaml_parse(frontmatter_str) or {}          # 빈 머리말 → {}
            return fm, js_trim(markdown_content)             # 본문은 trim
        except Exception:                                    # YAML 오류 → 본문만(cn은 console.warn)
            return {}, normalized                           # 파싱 실패 시 머리말 없이 원문만 돌려줌
    return {}, normalized                                    # 머리말 없음


# ───────────────────────── 파일 훑기(ignore-walk WalkerSync, includeEmpty:false, follow:false) ─────────────────────────
def _locale_key(s: str):                                    # 정렬 키 함수(localeCompare 근사)
    """localeCompare(…, 'en') 근사 — 대소문자 무시로 먼저, 같으면 소문자가 앞."""
    return (s.casefold(), [c.isupper() for c in s], s)       # ICU 기본 순서와 대부분 같음(문장부호 순서는 다를 수 있음)


def _read_ignore_rules(dir_path: str) -> list[tuple[bool, str]]:  # 폴더의 .ignore 규칙을 읽는 함수
    """그 폴더의 .ignore 규칙 — (부정?, 패턴). 주석·빈 줄 제외."""
    p = os.path.join(dir_path, ".ignore")                    # ignore-walk 기본 ignoreFiles = ['.ignore']
    if not os.path.isfile(p):                               # .ignore 파일이 없으면
        return []                                           # 규칙 없음
    rules = []                                              # 규칙 모음
    with open(p, encoding="utf-8", errors="replace", newline="") as f:   # utf8(줄바꿈 변환 없음 — Node readFile 처럼 \r 유지)
        for line in re.split(r"\r?\n", f.read()):            # data.split(/\r?\n/)
            t = js_trim(line)                                # rule.trim()
            if t == "" or t.startswith("#"):                 # 빈 줄·주석
                continue
            neg = t.startswith("!")                          # 부정(다시 포함)
            rules.append((neg, t[1:] if neg else t))        # '!' 를 뗀 패턴과 부정 여부를 함께 저장
    return rules                                            # 규칙 목록 반환


def _ignored(rel: str, is_dir: bool, rules_stack: list[tuple[str, list]]) -> bool:  # 경로가 무시 대상인지 판정하는 함수
    """간이 minimatch(matchBase·dot·nocase) — 마지막으로 맞은 규칙이 결정."""
    ignored = False                                          # 기본 포함
    for base_rel, rules in rules_stack:                      # 위 폴더 규칙부터
        sub = rel[len(base_rel) + 1:] if base_rel else rel   # 그 폴더 기준 상대
        name = sub.rsplit("/", 1)[-1]                        # 이름
        for neg, pat in rules:
            p = pat.rstrip("/")                              # 폴더 표시 제거
            if pat.endswith("/") and not is_dir:             # 폴더 전용 규칙
                continue                                    # 폴더 전용 규칙을 파일에는 적용 안 함
            target = name if "/" not in p else sub           # matchBase: 슬래시 없으면 이름만
            if fnmatch.fnmatch(target.lower(), p.lstrip("/").lower()):   # nocase  # 대소문자 무시 비교
                ignored = not neg                            # 뒤 규칙이 이김
    return ignored                                          # 마지막 판정 결과 반환


def _walk_files(root: str) -> list[str]:                    # 폴더 아래 파일을 재귀로 모으는 함수
    """WalkerSync({path, includeEmpty:false, follow:false}).start().result — root 기준 '/' 구분 상대 경로, localeCompare 정렬."""
    result: list[str] = []                                   # 파일들

    def walk(abs_dir: str, rel_dir: str, stack: list) -> None:  # 폴더 하나를 훑는 안쪽 재귀 함수
        try:                                                # 폴더 목록 읽기는 실패할 수 있음
            entries = os.listdir(abs_dir)                    # readdir
        except OSError:                                      # 못 읽음(ignore-walk 는 error 이벤트 — 동기판은 결과 없음)
            return                                          # 못 읽으면 그 폴더는 건너뜀
        rules = _read_ignore_rules(abs_dir)                  # 이 폴더 규칙
        cur = stack + [(rel_dir, rules)] if rules else stack # 규칙 쌓기
        for e in entries:                                   # 항목마다 처리
            rel = f"{rel_dir}/{e}" if rel_dir else e         # '/' 로 이음(this.path + '/' + entry)
            ab = os.path.join(abs_dir, e)                    # 실제 경로
            try:                                            # 링크 판정 중 오류에 대비
                is_dir = os.path.isdir(ab) and not os.path.islink(ab)   # lstat 기준(링크는 따라가지 않음 → 파일로 취급)
            except OSError:                                 # 판정 중 오류 발생
                continue                                    # 판정 불가한 항목은 건너뜀
            if _ignored(rel, is_dir, cur):                   # 무시
                continue                                    # 무시 대상이면 건너뜀
            if is_dir:                                       # 폴더면 내려감
                walk(ab, rel, cur)                          # 폴더 안으로 재귀
            else:                                            # 파일
                result.append(rel)                          # 파일 경로 수집

    walk(root, "", [])                                       # 시작
    return sorted(result, key=_locale_key)                   # 정렬


def _get_relative_path(cwd: str, file_path: str) -> str:    # cwd 기준 상대 경로를 만드는 함수
    """getRelativePath — 글자 앞부분이 cwd 와 같으면 './'+상대(path.join 이 './' 를 지움), 아니면 절대 경로."""
    return path_join(".", path_relative(cwd, file_path)) if file_path.startswith(cwd) else file_path  # cwd 로 시작하면 './상대', 아니면 절대 경로 그대로


def _get_file_paths_in_skill_directory(cwd: str, skill_file_path: str) -> list[str]:  # 기술 폴더의 부속 파일을 모으는 함수
    """getFilePathsInSkillDirectory — SKILL.md 가 있는 폴더의 다른 파일들('SKILL.md' 로 끝나는 것 제외)."""
    skill_dir = path_dirname(skill_file_path)                # 폴더
    if not os.path.exists(skill_dir):                        # 없음
        return []                                           # 폴더가 없으면 부속 파일 없음
    files = _walk_files(skill_dir)                           # 훑기
    joined = [path_join(skill_dir, f) for f in files]        # path.join(skillDir, filePath)
    return [_get_relative_path(cwd, p) for p in joined if not p.endswith("SKILL.md")]   # 상대 경로로


def _get_skill_files_from_dir(dir_path: str) -> list[str]:  # 기술 폴더 아래 SKILL.md 경로를 모으는 함수
    """getSkillFilesFromDir — 하위 폴더마다 SKILL.md 가 있으면 그 경로."""
    try:                                                    # 폴더 접근은 실패할 수 있음
        os.stat(dir_path)                                    # 있나
    except OSError:                                         # 못 읽으면
        return []                                           # 빈 목록
    out = []                                                 # 결과
    try:                                                    # 목록 읽기도 실패할 수 있음
        names = sorted(os.listdir(dir_path))                 # readdir(순서는 OS마다 다름 → ncn은 이름순으로 고정)
    except OSError:                                         # 못 읽으면
        return []                                           # 빈 목록
    for name in names:                                      # 하위 항목마다 처리
        d = path_join(dir_path, name)                        # 하위 경로
        if not os.path.isdir(d):                             # 폴더만(isDirectory — 링크 폴더는 Dirent 기준 제외)
            continue                                        # 폴더가 아니면 건너뜀
        if os.path.islink(d):                                # Dirent.isDirectory() 는 링크를 따라가지 않음
            continue                                        # 링크는 건너뜀
        f = path_join(d, "SKILL.md")                         # SKILL.md
        try:                                                # SKILL.md 존재 확인
            os.stat(f)                                       # 있나
            out.append(f)                                   # 있으면 결과에 추가
        except OSError:                                     # 없으면
            pass                                            # 건너뜀
    return out                                              # SKILL.md 경로 목록 반환


def load_markdown_skills(cwd: str, global_dir: str) -> tuple[list[Skill], list[dict]]:  # 세 곳에서 기술을 읽어 (목록, 오류)를 돌려주는 함수
    """loadMarkdownSkills — (기술 목록, 오류 목록). 오류는 치명적이지 않음({fatal:false, message})."""
    errors: list[dict] = []                                  # 오류
    skills: list[Skill] = []                                 # 기술
    cwd = str(cwd)                                           # 글로
    try:                                                    # 폴더 접근 전체를 감싸 한 번에 오류 처리
        skills_dirs = [                                      # 찾는 곳(순서)
            path_join(cwd, ".continue", SKILLS_DIR),        # 프로젝트 .continue/skills
            path_join(cwd, ".claude", SKILLS_DIR),          # 프로젝트 .claude/skills
            path_join(str(global_dir), SKILLS_DIR),          # env.continueHome(CONTINUE_GLOBAL_DIR 또는 ~/.continue)
        ]                                                   # 목록 끝
        skill_file_paths = [p for d in skills_dirs for p in _get_skill_files_from_dir(d)]   # 평탄화
        for skill_file_path in skill_file_paths:             # cn은 동시에 읽어 끝난 순서로 넣음 → ncn은 찾은 순서(결정적)
            try:                                            # 파일 하나 실패가 전체를 막지 않도록 감쌈
                with open(skill_file_path, encoding="utf-8", errors="replace", newline="") as f:   # readFile utf-8(BOM·CRLF 그대로 — Node와 같음)
                    content = f.read()                      # 본문 읽기
                frontmatter, markdown = parse_markdown_rule(content)   # 머리말·본문
                if not isinstance(frontmatter, dict):        # zod object 아님
                    raise ValueError("Expected object, received " + type(frontmatter).__name__)  # cn 검증 오류 글과 같은 형식
                name = frontmatter.get("name")               # 이름
                desc = frontmatter.get("description")        # 설명
                if not isinstance(name, str) or len(name) < 1:   # z.string().min(1)
                    raise ValueError("name: Required" if name is None else "name: Invalid")  # 이름 검증 실패 글
                if not isinstance(desc, str) or len(desc) < 1:
                    raise ValueError("description: Required" if desc is None else "description: Invalid")  # 설명 검증 실패 글
                files = _get_file_paths_in_skill_directory(cwd, skill_file_path)   # 부속 파일
                skills.append(Skill(name=name, description=desc, content=markdown,  # 기술 객체 추가
                                    path=_get_relative_path(cwd, skill_file_path), files=files))  # 경로는 cwd 기준 상대 경로
            except Exception as e:                           # 이 파일만 실패
                errors.append({"fatal": False, "message": f"Failed to parse markdown skill file: {e}"})  # 오류 목록에 비치명 오류로 기록
    except Exception as err:                                 # 전체 실패
        errors.append({"fatal": False, "message": f"Error loading markdown skill files: {err}"})  # 폴더 목록 전체 실패도 비치명 오류로 기록
    return skills, errors                                   # 기술·오류 목록 반환


# ───────────────────────── 도구 ─────────────────────────
_META_DESCRIPTION = "Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks."  # 도구 설명 기본 글(cn 원문)
_PARAMETERS = {                                             # 인자 스키마(JSON Schema)
    "type": "object",                                       # 인자는 객체
    "required": ["skill_name"],                             # skill_name 인자 필수
    "properties": {                                         # 인자별 정의 묶음
        "skill_name": {                                     # skill_name 인자 정의
            "type": "string",                               # 문자열
            "description": "The name of the skill to read. This should match the name from the available skills.",  # 인자 설명 원문
        },                                                  # skill_name 정의 끝
    },                                                      # properties 끝
}


async def _meta_run(args: dict, context=None) -> str:       # META 의 run 훅(빈 글)
    """SKILLS_TOOL_META.run — 빈 글(표시 이름 조회용 자리)."""
    return ""                                               # 실제 실행은 make_skills_tool 쪽


SKILLS_TOOL_META = Tool(                                     # allBuiltIns 의 정적 자리(이름·표시 이름 조회용)
    name="Skills",                                          # 모델에 보이는 도구 이름
    display_name="Skills",                                  # 화면 표시 이름
    description=_META_DESCRIPTION,                          # 기본 설명 연결
    parameters=_PARAMETERS,                                 # 공용 인자 스키마 연결
    run=_meta_run,                                          # 빈 실행 함수 연결
    readonly=False,                                         # 쓰기 도구 취급(읽기 전용 아님)
    is_built_in=True,                                       # 내장 도구 표시
)


def make_skills_tool(cwd: str, global_dir: str) -> Tool:    # 기술 목록을 설명에 넣은 Skills 도구를 만드는 함수
    """skillsTool() — 지금 디스크의 기술 목록으로 설명을 채운 도구."""
    skills, _errors = load_markdown_skills(cwd, global_dir)  # 기술 읽기
    # `${skills.map(…)}` — 배열을 글로 넣으면 쉼표로 이어짐(Array.prototype.toString)
    listing = ",".join(f"\nname: {s.name}\ndescription: {s.description}\n" for s in skills)  # 기술마다 'name/description' 블록을 쉼표로 이음(cn 템플릿)
    description = (f"Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks. "  # 설명 앞부분(cn 원문)
                   f"The skill name should match one of the available skills listed below:\n{listing}")  # 뒤에 기술 목록 덧붙임

    async def preprocess(args: dict) -> PreprocessResult:   # preprocess 훅
        """preprocess — 'Reading skill: <이름>'."""
        skill_name = args.get("skill_name")                  # 이름
        return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Reading skill: {skill_name}")])  # 미리보기 'Reading skill: <이름>'

    async def run(args: dict, context=None) -> str:         # run 훅
        """run — 기술 본문을 태그로 감싸 돌려줌. 없으면 SkillNotFound."""
        skill_name = args.get("skill_name")                  # 이름
        skill = next((s for s in skills if s.name == skill_name), None)   # 찾기(엄격 비교)
        if not skill:                                        # 없음
            available = ", ".join(s.name for s in skills)    # 목록
            raise ContinueError("SkillNotFound", f'Skill "{skill_name}" not found. Available skills: {available or "none"}')  # cn 오류 글(없으면 none)
        content = [                                         # 본문을 태그 조각 목록으로 만든다
            f"<skill_name>{skill.name}</skill_name>",       # 기술 이름 태그
            f"<skill_description>{skill.description}</skill_description>",  # 기술 설명 태그
            f"<skill_content>{skill.content}</skill_content>",  # 기술 본문 태그
        ]                                                   # 태그 목록 끝
        if len(skill.files) > 0:                             # 부속 파일
            content.append(f"<skill_files>{','.join(skill.files)}</skill_files>")  # 부속 파일 목록 태그
            content.append("<other_instructions>Use the read file tool to access skill files as needed.</other_instructions>")  # 부속 파일 읽는 방법 안내 태그(cn 원문)
        return "\n".join(content)                            # 줄로 이음

    return Tool(                                            # 목록이 반영된 도구 객체 반환
        name="Skills",                                      # 모델에 보이는 도구 이름
        display_name="Skills",                              # 화면 표시 이름
        description=description,                            # 기술 목록이 든 설명
        parameters=_PARAMETERS,                             # 공용 인자 스키마 연결
        run=run,                                            # run 함수 연결
        preprocess=preprocess,                              # preprocess 함수 연결
        readonly=False,                                     # 쓰기 도구 취급(읽기 전용 아님)
        is_built_in=True,                                   # 내장 도구 표시
    )


def build_import_skill_prompt(identifier: str) -> str:      # /import-skill 프롬프트를 만드는 함수
    """buildImportSkillPrompt — /import-skill 명령이 쓰는 글(cn 원문)."""
    return f"""
# Overview

The user wants to import skills.

User-provided skill identifier:
{identifier}

# Guidelines
- There can be multiple skills in a single repository.
- Use the available tools to fetch content and write files. When you are done, briefly summarize which skill you imported and where you saved it.
- Use the "AskQuestion" tool where required to clarify with the user.

# Process:

**Identifier can either be a URL or a skill name**

- If it looks like a URL (for example, it starts with http:// or https://), open that URL and inspect its contents to find the code or files that define the skill.
- If the URL is a GitHub repository, look for the skills folder. There can be multiple skills within subdirectories.
- If it looks like a skill name, you should search for the most relevant open-source skill or repository that matches the skill identifier.
- Ask questions to the user to clarify which skill they are referring to if there are multiple options in your findings.

**Create the skill files**

- The skills should be created under the directory: ~/.continue/skills/<skill-name>
- The subdirectory name should match the name of the skill directory in the fetched repository.
- The relevant files and folders along with SKILL.md should be present inside the created skill subdirectory.
- If the skill already exists, ask question to the user to clarify whether they want to update it.
- Important: Before writing any files, ask the user if they want to proceed with the import.
"""
