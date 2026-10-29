# ncn/permissions/risk.py — [ncn E2] 인자 기반 위험 판정(cn 원본 없음 — ncn 전용, 계획 6장 E2)
# cn 정책은 도구 단위(Write 전체 allow/ask)라 "CI 설정 파일 쓰기"·"git push" 같은 위험한 호출을 따로 못 막는다.
# 여기서는 도구 인자(파일 경로·명령)를 보고 위험하면 판정을 allow → ask 로 올린다(exclude·ask 는 그대로 — 내리지 않음).
#   - 파일 규칙: Write/Edit/MultiEdit 대상 경로를 gitignore 문법(ncn/security/ignore.py 판정기, 대소문자 무시)으로 비교
#   - 명령 규칙: Bash 명령을 ; && || | & 줄바꿈으로 나눈 조각마다 글롭(대소문자 무시)으로 비교 + 기본 정규식
# 도구 목록(인자 {})에는 영향이 없다 — 요청 본문 불변(차등 시험 유지). 실행 시점 판정만 바뀐다.
from __future__ import annotations                           # 타입 주석 지연 평가

import fnmatch                                               # 명령 조각 글롭 비교
import os                                                    # 작업 폴더 기준 절대경로
import re                                                    # 기본 명령 정규식
from dataclasses import dataclass, field                     # 자료 구조
from typing import Any, Optional                             # 타입

# 경로를 인자로 받는 쓰기 도구 → 그 인자 이름(실제 도구 스키마 이름 — write_file.py "filepath", edit.py·multi_edit.py "file_path")
PATH_ARG_BY_TOOL = {                                         # 도구 이름 → 경로 인자 이름
    "Write": "filepath",                                     # Write 대상 경로
    "Edit": "file_path",                                     # Edit 대상 경로
    "MultiEdit": "file_path",                                # MultiEdit 대상 경로
}                                                            # 표 끝

# 기본 파일 규칙(gitignore 문법: 슬래시 없는 패턴은 어느 깊이든, 'x/' 는 그 폴더 안 전부)
DEFAULT_RISK_PATHS = [                                       # CI·빌드 설정, 마이그레이션, 비밀 후보
    # CI 설정 위치
    ".github/",                                              # GitHub Actions 워크플로·설정
    ".gitlab-ci.yml",                                        # GitLab CI
    ".gitlab/",                                              # GitLab 설정 폴더
    "azure-pipelines*.yml",                                  # Azure Pipelines(yml)
    "azure-pipelines*.yaml",                                 # Azure Pipelines(yaml)
    ".azure-pipelines/",                                     # Azure Pipelines 폴더
    ".azuredevops/",                                         # Azure DevOps 폴더
    ".circleci/",                                            # CircleCI
    ".buildkite/",                                           # Buildkite
    "bitbucket-pipelines.yml",                               # Bitbucket Pipelines
    ".travis.yml",                                           # Travis CI
    "appveyor.yml",                                          # AppVeyor
    ".drone.yml",                                            # Drone CI
    "Jenkinsfile",                                           # Jenkins
    # 빌드 설정(.NET·C++·Node·Python·그 밖)
    "*.sln",                                                 # Visual Studio 솔루션
    "*.csproj",                                              # C# 프로젝트
    "*.vbproj",                                              # VB 프로젝트
    "*.fsproj",                                              # F# 프로젝트
    "*.vcxproj",                                             # C++ 프로젝트
    "*.vcxproj.filters",                                     # C++ 프로젝트 필터
    "*.props",                                               # MSBuild 속성 파일
    "*.targets",                                             # MSBuild 대상 파일
    "Directory.Build.*",                                     # Directory.Build.props/targets
    "Directory.Packages.props",                              # 중앙 패키지 버전
    "NuGet.config",                                          # NuGet 원본 설정
    "global.json",                                           # .NET SDK 버전 고정
    "package.json",                                          # Node 패키지·스크립트
    "pyproject.toml",                                        # Python 프로젝트
    "setup.py",                                              # Python 설치 스크립트
    "setup.cfg",                                             # Python 설치 설정
    "CMakeLists.txt",                                        # CMake
    "Makefile",                                              # make
    "Dockerfile",                                            # 컨테이너 빌드
    "pom.xml",                                               # Maven
    "build.gradle",                                          # Gradle
    "build.gradle.kts",                                      # Gradle(Kotlin)
    # 데이터베이스 마이그레이션
    "migrations/",                                           # 마이그레이션 폴더(어느 깊이든)
    "*.sql",                                                 # SQL 스크립트
    # 비밀 후보(대부분 민감 파일 판정에서도 막히지만 승인 단계에서 먼저 묻는다)
    ".env*",                                                 # .env, .env.local 등
    "*.pem",                                                 # PEM 키·인증서
    "*.key",                                                 # 개인 키
]                                                            # 목록 끝

# 기본 명령 규칙: (이름, 정규식). 명령 전체(소문자)에서 찾는다 — 각 규칙은 ; & | 줄바꿈을 넘지 않는다
_SEP = r"(?:^|[\s;&|(`\"'])"                                  # 명령 이름 앞: 시작·공백·구분자·따옴표(bash -c "…" 안도 잡음)
_GIT = _SEP + r"git(?:\.exe)?(?:\s+-{1,2}[^\s;&|]+(?:\s+[^\s;&|-][^\s;&|]*)?)*\s+"   # git + 앞 옵션(-C 경로, -c 키=값 등) + 하위 명령 앞
_SAME = r"[^;&|\n]*"                                          # 같은 명령 조각 안의 글자들
DEFAULT_RISK_COMMANDS: list[tuple[str, str]] = [             # 기본 위험 명령(이름은 로그·시험용)
    ("git push", _GIT + r"push\b"),                          # 원격 저장소로 올리기
    ("git reset --hard", _GIT + r"reset\b" + _SAME + r"--hard\b"),   # 작업 내용 버리기
    ("git clean", _GIT + r"clean\b"),                        # 추적 안 되는 파일 지우기
    ("rm -r", _SEP + r"rm(?:\.exe)?\s+(?:" + _SAME + r"\s)?(?:-[a-z]*r[a-z]*|--recursive)\b"),   # rm -r/-rf/-fr/-R/--recursive
    ("del /s", _SEP + r"(?:del|erase|rmdir|rd)\s+(?:" + _SAME + r"\s)?/s\b"),   # cmd 하위 폴더까지 지우기
    ("Remove-Item -Recurse", _SEP + r"(?:remove-item|ri|rmdir|rd|del|erase)\s+(?:" + _SAME + r"\s)?-r[a-z]*\b"),   # PowerShell(-r·-rec·-recurse 줄임 허용)
    ("msbuild /t:Publish", _SEP + r"msbuild(?:\.exe)?\b" + _SAME + r"[/-](?:t|target):[\"']?[^\s&|]*publish"),   # MSBuild 배포 대상(-t:"Build;Publish" 처럼 ; 로 이은 목록 포함)
    ("dotnet publish", _SEP + r"dotnet(?:\.exe)?\s+(?:publish|nuget\s+push)\b"),   # .NET 배포·패키지 올리기
    ("nuget push", _SEP + r"nuget(?:\.exe)?\s+push\b"),      # NuGet 패키지 올리기
    ("npm publish", _SEP + r"(?:npm|yarn|pnpm)\s+publish\b"),   # npm 패키지 올리기
    ("twine upload", _SEP + r"twine\s+upload\b"),            # PyPI 올리기
]                                                            # 목록 끝
_DEFAULT_COMMAND_RX = [(n, re.compile(rx)) for n, rx in DEFAULT_RISK_COMMANDS]   # 미리 컴파일

_SEGMENT_SPLIT = re.compile(r"&&|\|\||[;|&\r\n]")             # 명령 조각 나누기(&& || ; | & 줄바꿈)
_WS_RUN = re.compile(r"\s+")                                  # 연속 공백


def command_segments(command: str) -> list[str]:            # 명령을 조각으로 나누는 함수
    """'a && b | c' → ['a', 'b', 'c'] — 소문자·공백 하나로 정리, 앞의 sudo 는 뗀다(빈 조각 제외)."""
    out: list[str] = []                                      # 결과
    for seg in _SEGMENT_SPLIT.split(command or ""):           # 구분자로 나눔
        s = _WS_RUN.sub(" ", seg).strip().lower()            # 공백 정리 + 소문자(Windows 명령은 대소문자 무시)
        while s.startswith("sudo "):                         # sudo 접두
            s = s[5:].lstrip()                               # 떼고 다시 봄
        if s:                                                # 빈 조각이 아니면
            out.append(s)                                    # 추가
    return out                                               # 조각 목록


def _relative_for_match(path: str) -> str:                  # 경로를 판정용 상대 글로
    """작업 폴더 안이면 작업 폴더 기준 상대경로, 밖이면 루트를 뗀 전체 조각('/' 구분) — 민감 파일 판정과 같은 규칙."""
    from ncn.security.ignore import _full_relative_pieces    # 같은 정규화 함수(S5) 재사용
    absolute = path if os.path.isabs(path) else os.path.abspath(path)   # 상대경로는 작업 폴더 기준 절대경로로
    return _full_relative_pieces(absolute)                   # 판정용 글


@dataclass                                                  # 자료 클래스
class RiskRules:                                            # 위험 판정 규칙 묶음
    """paths: gitignore 문법 경로 패턴, commands: 명령 조각 글롭(예 'git push*'), use_defaults: 기본 규칙도 쓸지."""
    paths: list[str] = field(default_factory=list)           # 추가 경로 패턴
    commands: list[str] = field(default_factory=list)        # 추가 명령 글롭
    use_defaults: bool = False                               # 기본 규칙(DEFAULT_RISK_*) 포함 여부
    _ignore: Any = field(default=None, repr=False, compare=False)   # 경로 판정기(지연 생성)

    def _path_matcher(self) -> Any:                          # 경로 판정기 만들기
        """기본 + 추가 경로 패턴으로 Ignore 판정기(대소문자 무시)를 한 번 만든다."""
        if self._ignore is None:                             # 아직 없으면
            from ncn.security.ignore import Ignore          # npm ignore 와 같은 판정기
            patterns = (list(DEFAULT_RISK_PATHS) if self.use_defaults else []) + list(self.paths)   # 기본 + 추가
            self._ignore = Ignore().add(patterns)            # 판정기 생성
        return self._ignore                                  # 판정기 반환

    def match_path(self, path: Any) -> Optional[str]:        # 경로가 위험한지
        """위험하면 이유 글('path:<상대경로>'), 아니면 None. 경로가 글이 아니거나 비면 None(도구가 따로 오류 처리)."""
        if not isinstance(path, str) or not path.strip():    # 경로 없음
            return None                                     # 판정하지 않음
        if not self.paths and not self.use_defaults:         # 규칙 없음
            return None                                     # 빠르게 끝
        try:                                                # 경로 정규화·판정(이상한 경로는 위험 아님으로)
            rel = _relative_for_match(path)                  # 판정용 상대 글
            if rel and self._path_matcher().ignores(rel):    # 규칙에 걸리면
                return f"path:{rel}"                        # 이유
        except Exception:                                    # 판정기 형식 오류 등
            return None                                     # 판정하지 않음(도구가 경로 오류를 낸다)
        return None                                          # 안 걸림

    def match_command(self, command: Any) -> Optional[str]:  # 명령이 위험한지
        """위험하면 이유 글('command:<규칙>'), 아니면 None."""
        if not isinstance(command, str) or not command.strip():   # 명령 없음
            return None                                     # 판정하지 않음
        if self.use_defaults:                                # 기본 규칙
            low = command.lower()                            # 소문자(Windows 명령·옵션 대소문자 무시)
            for name, rx in _DEFAULT_COMMAND_RX:             # 규칙마다
                if rx.search(low):                           # 어디든 걸리면
                    return f"command:{name}"                # 이유
        if self.commands:                                    # 추가 글롭
            segments = command_segments(command)             # 조각들
            for pat in self.commands:                        # 글롭마다
                p = _WS_RUN.sub(" ", str(pat)).strip().lower()   # 패턴도 같은 정리
                if p and any(fnmatch.fnmatchcase(seg, p) for seg in segments):   # 조각 하나라도 맞으면
                    return f"command:{pat}"                 # 이유
        return None                                          # 안 걸림

    def match(self, tool_name: str, args: Any) -> Optional[str]:   # 도구 호출이 위험한지
        """Write/Edit/MultiEdit 는 경로, Bash 는 명령을 본다. 그 밖의 도구는 None."""
        if not isinstance(args, dict) or not args:           # 인자 없음(도구 목록 판정 등)
            return None                                     # 판정하지 않음
        key = PATH_ARG_BY_TOOL.get(tool_name)                # 경로 인자 이름
        if key is not None:                                  # 쓰기 도구면
            return self.match_path(args.get(key))           # 경로 판정
        if tool_name == "Bash":                              # Bash 면
            return self.match_command(args.get("command"))  # 명령 판정
        return None                                          # 그 밖은 대상 아님

    @property                                                # 읽기 전용 속성
    def empty(self) -> bool:                                 # 규칙이 하나도 없는지
        """기본 규칙도 추가 규칙도 없으면 참."""
        return not self.use_defaults and not self.paths and not self.commands   # 셋 다 없음


def parse_risk_rules(value: Any, *, where: str, strict: bool) -> RiskRules:   # YAML 값 → 규칙
    """{paths: [...], commands: [...]} 를 해석. strict(관리자 정책)면 형식 오류에 ValueError, 아니면(사용자) 그 항목만 버린다."""
    rules = RiskRules()                                      # 빈 규칙
    if value is None:                                        # 없음
        return rules                                        # 빈 규칙
    if not isinstance(value, dict):                          # 사전이 아니면
        if strict:                                          # 관리자 정책
            raise ValueError(f"{where} must be a mapping with 'paths' and/or 'commands'")   # 형식 오류
        return rules                                        # 사용자: 무시
    for key in ("paths", "commands"):                        # 두 목록
        items = value.get(key)                               # 값
        if items is None:                                    # 없음
            continue                                        # 다음
        if not isinstance(items, list) or not all(isinstance(x, str) for x in items):   # 글 목록이 아니면
            if strict:                                      # 관리자 정책
                raise ValueError(f"{where}.{key} must be a list of strings")   # 형식 오류
            continue                                        # 사용자: 무시
        getattr(rules, key).extend(items)                    # 추가
    return rules                                             # 규칙 반환
