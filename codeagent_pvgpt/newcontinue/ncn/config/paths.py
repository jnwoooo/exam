# ncn/config/paths.py — 전역 폴더(~/.continue) 위치. cn core/util/paths.ts(CONTINUE_GLOBAL_DIR·getGlobalContextFilePath) + extensions/cli/src/env.ts(continueHome)
# 규칙: CONTINUE_GLOBAL_DIR 가 비어 있지 않으면 그것(상대경로면 cwd 기준 절대경로), 아니면 <홈>/.continue
#   홈 = Node os.homedir(): posix 는 $HOME(비면 계정 정보), Windows 는 %USERPROFILE%(비면 계정 정보)
# 주의: environ 은 dotenv 적용 상태여야 cn과 같다(<cwd>/.env 의 CONTINUE_GLOBAL_DIR·HOME 도 반영됨 — ncn/config/env.py)
from __future__ import annotations                           # 타입 주석 지연 평가

import json                                                  # globalContext.json
import os                                                    # 환경변수·현재 폴더
import pathlib                                               # 경로 형식
from typing import Any, Mapping, Optional                    # 타입

from ncn.tools.formatters import node_platform, path_is_absolute, path_join, path_resolve   # Node path 흉내


def node_homedir(environ: Optional[Mapping[str, str]] = None) -> str:
    """Node os.homedir() — posix: HOME, Windows: USERPROFILE. 비어 있으면 계정 정보(파이썬 기본값)."""
    env = os.environ if environ is None else environ         # 환경변수
    key = "USERPROFILE" if node_platform() == "win32" else "HOME"   # libuv uv_os_homedir 가 보는 변수
    v = env.get(key)
    if v:                                                    # 값이 있으면 그대로
        return v
    return str(pathlib.Path.home())                          # 계정 정보(getpwuid / Windows 프로필)


def continue_home(environ: Optional[Mapping[str, str]] = None) -> str:
    """env.ts continueHome — CONTINUE_GLOBAL_DIR || path.join(homedir, ".continue"). 상대경로를 바꾸지 않은 글(cn 경고 글에 이대로 나옴)."""
    env = os.environ if environ is None else environ
    v = env.get("CONTINUE_GLOBAL_DIR")
    if v:                                                    # || — 빈 글은 없는 것
        return v
    return path_join(node_homedir(env), ".continue")


def global_dir(environ: Optional[Mapping[str, str]] = None, cwd: Optional[str | os.PathLike] = None) -> pathlib.Path:
    """core/util/paths.ts CONTINUE_GLOBAL_DIR — 상대경로는 cwd 기준 절대경로로(path.resolve). 폴더를 만들지는 않는다."""
    env = os.environ if environ is None else environ
    v = env.get("CONTINUE_GLOBAL_DIR")
    if v:                                                    # 지정됨
        base = os.fspath(cwd) if cwd is not None else os.getcwd()   # process.cwd()
        return pathlib.Path(v if path_is_absolute(v) else path_resolve(base, v))
    return pathlib.Path(path_join(node_homedir(env), ".continue"))   # 기본 ~/.continue


def global_config_yaml(gdir: pathlib.Path) -> pathlib.Path:
    """<전역>/config.yaml — --config 가 없을 때 읽는 파일."""
    return gdir / "config.yaml"


def global_context_path(gdir: pathlib.Path) -> pathlib.Path:
    """getGlobalContextFilePath — <전역>/index/globalContext.json."""
    return gdir / "index" / "globalContext.json"


def read_global_context(gdir: pathlib.Path) -> dict[str, Any]:
    """GlobalContext 읽기(get 전용). 없거나 깨졌으면 빈 사전(cn get 도 오류 시 undefined)."""
    p = global_context_path(gdir)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))     # JSON
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
