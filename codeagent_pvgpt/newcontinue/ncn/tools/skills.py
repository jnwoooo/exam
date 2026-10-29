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


@dataclass
class Skill:
    """Skill — 이름·설명·경로·본문·부속 파일."""
    name: str                                                # 이름(머리말)
    description: str                                         # 설명(머리말)
    path: str                                                # SKILL.md 경로(cwd 안이면 상대)
    content: str                                             # 본문(머리말 뺀 것)
    files: list[str] = field(default_factory=list)           # 같은 폴더의 다른 파일들


# ───────────────────────── YAML 1.2 흉내(cn은 npm 'yaml' 2.x 사용) ─────────────────────────
class _Yaml12Loader(yaml.SafeLoader):
    """PyYAML(1.1)은 yes/no/on/off 를 참·거짓, 2024-01-01 을 날짜로 읽는다 — npm yaml(1.2 core)처럼 글로 남긴다."""


_Yaml12Loader.yaml_implicit_resolvers = {                    # 클래스 전용 복사본
    k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp")]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12Loader.add_implicit_resolver(                         # 참·거짓은 true/false 만(1.2 core)
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def _yaml_parse(text: str):
    """YAML.parse — 실패하면 예외."""
    return yaml.load(text, Loader=_Yaml12Loader)             # 안전 로더 기반


# JS /^---\s*$/m — 줄 시작(문자열 시작 또는 줄 바꿈 글자 뒤) ~ 줄 끝. JS 의 줄 바꿈 글자는 \n \r
_LT = "\n\r\u2028\u2029"                                     # JS 줄 끝 글자
_FRONT_SPLIT = re.compile(r"(?:(?<![\s\S])|(?<=[" + _LT + r"]))---" + JS_WS_CLASS + r"*(?=[" + _LT + r"]|(?![\s\S]))")   # \s* 는 욕심쟁이(JS와 같게 되돌아가며 맞춤)


def _js_split_frontmatter(s: str) -> list[str]:
    """normalizedContent.split(/^---\\s*$/m) — JS 분할(빈 일치는 이 패턴에선 생기지 않음)."""
    parts: list[str] = []                                    # 결과
    pos = 0                                                  # 다음 시작
    for m in _FRONT_SPLIT.finditer(s):                       # 일치마다
        parts.append(s[pos:m.start()])                       # 앞 조각
        pos = m.end()                                        # 다음
    parts.append(s[pos:])                                    # 마지막 조각
    return parts


def parse_markdown_rule(content: str) -> tuple[dict, str]:
    """@continuedev/config-yaml parseMarkdownRule — (머리말, 본문). 머리말이 깨지면 ({}, 원문)."""
    normalized = content.replace("\r\n", "\n")               # 줄 끝 통일
    parts = _js_split_frontmatter(normalized)                # --- 로 나눔
    if len(parts) >= 3:                                      # 앞·머리말·본문
        frontmatter_str = parts[1]                           # 머리말 글
        markdown_content = "---".join(parts[2:])             # 본문(안의 --- 는 다시 이음)
        try:
            fm = _yaml_parse(frontmatter_str) or {}          # 빈 머리말 → {}
            return fm, js_trim(markdown_content)             # 본문은 trim
        except Exception:                                    # YAML 오류 → 본문만(cn은 console.warn)
            return {}, normalized
    return {}, normalized                                    # 머리말 없음


# ───────────────────────── 파일 훑기(ignore-walk WalkerSync, includeEmpty:false, follow:false) ─────────────────────────
def _locale_key(s: str):
    """localeCompare(…, 'en') 근사 — 대소문자 무시로 먼저, 같으면 소문자가 앞."""
    return (s.casefold(), [c.isupper() for c in s], s)       # ICU 기본 순서와 대부분 같음(문장부호 순서는 다를 수 있음)


def _read_ignore_rules(dir_path: str) -> list[tuple[bool, str]]:
    """그 폴더의 .ignore 규칙 — (부정?, 패턴). 주석·빈 줄 제외."""
    p = os.path.join(dir_path, ".ignore")                    # ignore-walk 기본 ignoreFiles = ['.ignore']
    if not os.path.isfile(p):
        return []
    rules = []
    with open(p, encoding="utf-8", errors="replace") as f:   # utf8
        for line in re.split(r"\r?\n", f.read()):            # data.split(/\r?\n/)
            t = js_trim(line)                                # rule.trim()
            if t == "" or t.startswith("#"):                 # 빈 줄·주석
                continue
            neg = t.startswith("!")                          # 부정(다시 포함)
            rules.append((neg, t[1:] if neg else t))
    return rules


def _ignored(rel: str, is_dir: bool, rules_stack: list[tuple[str, list]]) -> bool:
    """간이 minimatch(matchBase·dot·nocase) — 마지막으로 맞은 규칙이 결정."""
    ignored = False                                          # 기본 포함
    for base_rel, rules in rules_stack:                      # 위 폴더 규칙부터
        sub = rel[len(base_rel) + 1:] if base_rel else rel   # 그 폴더 기준 상대
        name = sub.rsplit("/", 1)[-1]                        # 이름
        for neg, pat in rules:
            p = pat.rstrip("/")                              # 폴더 표시 제거
            if pat.endswith("/") and not is_dir:             # 폴더 전용 규칙
                continue
            target = name if "/" not in p else sub           # matchBase: 슬래시 없으면 이름만
            if fnmatch.fnmatch(target.lower(), p.lstrip("/").lower()):   # nocase
                ignored = not neg                            # 뒤 규칙이 이김
    return ignored


def _walk_files(root: str) -> list[str]:
    """WalkerSync({path, includeEmpty:false, follow:false}).start().result — root 기준 '/' 구분 상대 경로, localeCompare 정렬."""
    result: list[str] = []                                   # 파일들

    def walk(abs_dir: str, rel_dir: str, stack: list) -> None:
        try:
            entries = os.listdir(abs_dir)                    # readdir
        except OSError:                                      # 못 읽음(ignore-walk 는 error 이벤트 — 동기판은 결과 없음)
            return
        rules = _read_ignore_rules(abs_dir)                  # 이 폴더 규칙
        cur = stack + [(rel_dir, rules)] if rules else stack # 규칙 쌓기
        for e in entries:
            rel = f"{rel_dir}/{e}" if rel_dir else e         # '/' 로 이음(this.path + '/' + entry)
            ab = os.path.join(abs_dir, e)                    # 실제 경로
            try:
                is_dir = os.path.isdir(ab) and not os.path.islink(ab)   # lstat 기준(링크는 따라가지 않음 → 파일로 취급)
            except OSError:
                continue
            if _ignored(rel, is_dir, cur):                   # 무시
                continue
            if is_dir:                                       # 폴더면 내려감
                walk(ab, rel, cur)
            else:                                            # 파일
                result.append(rel)

    walk(root, "", [])                                       # 시작
    return sorted(result, key=_locale_key)                   # 정렬


def _get_relative_path(cwd: str, file_path: str) -> str:
    """getRelativePath — 글자 앞부분이 cwd 와 같으면 './'+상대(path.join 이 './' 를 지움), 아니면 절대 경로."""
    return path_join(".", path_relative(cwd, file_path)) if file_path.startswith(cwd) else file_path


def _get_file_paths_in_skill_directory(cwd: str, skill_file_path: str) -> list[str]:
    """getFilePathsInSkillDirectory — SKILL.md 가 있는 폴더의 다른 파일들('SKILL.md' 로 끝나는 것 제외)."""
    skill_dir = path_dirname(skill_file_path)                # 폴더
    if not os.path.exists(skill_dir):                        # 없음
        return []
    files = _walk_files(skill_dir)                           # 훑기
    joined = [path_join(skill_dir, f) for f in files]        # path.join(skillDir, filePath)
    return [_get_relative_path(cwd, p) for p in joined if not p.endswith("SKILL.md")]   # 상대 경로로


def _get_skill_files_from_dir(dir_path: str) -> list[str]:
    """getSkillFilesFromDir — 하위 폴더마다 SKILL.md 가 있으면 그 경로."""
    try:
        os.stat(dir_path)                                    # 있나
    except OSError:
        return []
    out = []                                                 # 결과
    try:
        names = sorted(os.listdir(dir_path))                 # readdir(순서는 OS마다 다름 → ncn은 이름순으로 고정)
    except OSError:
        return []
    for name in names:
        d = path_join(dir_path, name)                        # 하위 경로
        if not os.path.isdir(d):                             # 폴더만(isDirectory — 링크 폴더는 Dirent 기준 제외)
            continue
        if os.path.islink(d):                                # Dirent.isDirectory() 는 링크를 따라가지 않음
            continue
        f = path_join(d, "SKILL.md")                         # SKILL.md
        try:
            os.stat(f)                                       # 있나
            out.append(f)
        except OSError:
            pass
    return out


def load_markdown_skills(cwd: str, global_dir: str) -> tuple[list[Skill], list[dict]]:
    """loadMarkdownSkills — (기술 목록, 오류 목록). 오류는 치명적이지 않음({fatal:false, message})."""
    errors: list[dict] = []                                  # 오류
    skills: list[Skill] = []                                 # 기술
    cwd = str(cwd)                                           # 글로
    try:
        skills_dirs = [                                      # 찾는 곳(순서)
            path_join(cwd, ".continue", SKILLS_DIR),
            path_join(cwd, ".claude", SKILLS_DIR),
            path_join(str(global_dir), SKILLS_DIR),          # env.continueHome(CONTINUE_GLOBAL_DIR 또는 ~/.continue)
        ]
        skill_file_paths = [p for d in skills_dirs for p in _get_skill_files_from_dir(d)]   # 평탄화
        for skill_file_path in skill_file_paths:             # cn은 동시에 읽어 끝난 순서로 넣음 → ncn은 찾은 순서(결정적)
            try:
                with open(skill_file_path, encoding="utf-8", errors="replace") as f:   # readFile utf-8(BOM 은 남음 — Node와 같음)
                    content = f.read()
                frontmatter, markdown = parse_markdown_rule(content)   # 머리말·본문
                if not isinstance(frontmatter, dict):        # zod object 아님
                    raise ValueError("Expected object, received " + type(frontmatter).__name__)
                name = frontmatter.get("name")               # 이름
                desc = frontmatter.get("description")        # 설명
                if not isinstance(name, str) or len(name) < 1:   # z.string().min(1)
                    raise ValueError("name: Required" if name is None else "name: Invalid")
                if not isinstance(desc, str) or len(desc) < 1:
                    raise ValueError("description: Required" if desc is None else "description: Invalid")
                files = _get_file_paths_in_skill_directory(cwd, skill_file_path)   # 부속 파일
                skills.append(Skill(name=name, description=desc, content=markdown,
                                    path=_get_relative_path(cwd, skill_file_path), files=files))
            except Exception as e:                           # 이 파일만 실패
                errors.append({"fatal": False, "message": f"Failed to parse markdown skill file: {e}"})
    except Exception as err:                                 # 전체 실패
        errors.append({"fatal": False, "message": f"Error loading markdown skill files: {err}"})
    return skills, errors


# ───────────────────────── 도구 ─────────────────────────
_META_DESCRIPTION = "Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks."
_PARAMETERS = {
    "type": "object",
    "required": ["skill_name"],
    "properties": {
        "skill_name": {
            "type": "string",
            "description": "The name of the skill to read. This should match the name from the available skills.",
        },
    },
}


async def _meta_run(args: dict, context=None) -> str:
    """SKILLS_TOOL_META.run — 빈 글(표시 이름 조회용 자리)."""
    return ""


SKILLS_TOOL_META = Tool(                                     # allBuiltIns 의 정적 자리(이름·표시 이름 조회용)
    name="Skills",
    display_name="Skills",
    description=_META_DESCRIPTION,
    parameters=_PARAMETERS,
    run=_meta_run,
    readonly=False,
    is_built_in=True,
)


def make_skills_tool(cwd: str, global_dir: str) -> Tool:
    """skillsTool() — 지금 디스크의 기술 목록으로 설명을 채운 도구."""
    skills, _errors = load_markdown_skills(cwd, global_dir)  # 기술 읽기
    # `${skills.map(…)}` — 배열을 글로 넣으면 쉼표로 이어짐(Array.prototype.toString)
    listing = ",".join(f"\nname: {s.name}\ndescription: {s.description}\n" for s in skills)
    description = (f"Use this tool to read the content of a skill by its name. Skills contain detailed instructions for specific tasks. "
                   f"The skill name should match one of the available skills listed below:\n{listing}")

    async def preprocess(args: dict) -> PreprocessResult:
        """preprocess — 'Reading skill: <이름>'."""
        skill_name = args.get("skill_name")                  # 이름
        return PreprocessResult(args=args, preview=[ToolCallPreview(type="text", content=f"Reading skill: {skill_name}")])

    async def run(args: dict, context=None) -> str:
        """run — 기술 본문을 태그로 감싸 돌려줌. 없으면 SkillNotFound."""
        skill_name = args.get("skill_name")                  # 이름
        skill = next((s for s in skills if s.name == skill_name), None)   # 찾기(엄격 비교)
        if not skill:                                        # 없음
            available = ", ".join(s.name for s in skills)    # 목록
            raise ContinueError("SkillNotFound", f'Skill "{skill_name}" not found. Available skills: {available or "none"}')
        content = [
            f"<skill_name>{skill.name}</skill_name>",
            f"<skill_description>{skill.description}</skill_description>",
            f"<skill_content>{skill.content}</skill_content>",
        ]
        if len(skill.files) > 0:                             # 부속 파일
            content.append(f"<skill_files>{','.join(skill.files)}</skill_files>")
            content.append("<other_instructions>Use the read file tool to access skill files as needed.</other_instructions>")
        return "\n".join(content)                            # 줄로 이음

    return Tool(
        name="Skills",
        display_name="Skills",
        description=description,
        parameters=_PARAMETERS,
        run=run,
        preprocess=preprocess,
        readonly=False,
        is_built_in=True,
    )


def build_import_skill_prompt(identifier: str) -> str:
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
