#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
vupdate.py  -  C++ 버전 업데이트 대응 자동화 (단일 파일 실행본)
==============================================================================
버전 : 0.4.0 (0.3.0 에 서버 점검, 환경값 축과 공통 요청, 규칙 확충, 빌드 오류 분류를 더함)
목적 : 레거시 C++ 패키지를 새 Visual Studio 환경(예: 2008 -> 2022)에 맞게
       고치는 작업을 한 명령으로 수행한다. 규칙으로 고칠 수 있는 것은 규칙으로,
       규칙으로 못 고치는 것만 sLLM에 맡기고, 결과를 별도 폴더에 낸다.
배포 : 표준 라이브러리만 사용한다(폐쇄망 배포용). 외부 패키지 설치가 필요 없다.
       exe 생성: pyinstaller --onefile --name vupdate vupdate.py
문서 : 04_개발구조.md (구조), 03_확인사항.md (환경 점검)

[사용 예]
  vupdate run --package D:\\src\\PKG_A --from-vs 2008 --to-vs 2022
  vupdate run --package D:\\src\\PKG_A --profile vs2008_to_vs2022 --request PKG_A
  vupdate run --package D:\\src\\PKG_A --from-vs 2015 --to-vs 2022 --platform x64 --only Src/Net/*
  vupdate rules --from-vs 2015 --to-vs 2022      (이 환경값에서 켜지는 규칙 보기)
  vupdate preflight --package D:\\src\\PKG_A      (환경 점검만)
  vupdate selftest                               (설치 확인용 자체 시험)
  vupdate llmcheck                               (실제 sLLM 서버가 기대대로 답하는지 점검)
  vupdate buildlog --log msbuild.log --run <실행 폴더>   (빌드 오류 목록을 분류하고 대응을 붙임)

[이 파일의 구획]  수정할 때는 해당 구획만 고친다. 구획 사이의 호출은
                  실행기(M10)가 넘겨주는 ctx 객체와 저장소(M03)만 거친다.
  M01 공통      상수, 로그([모듈][함수명] 접두어), 작은 유틸
  M02 설정      버전 대응표, 프로필/요청사항/서버 설정 읽기와 검증
  M03 저장소    실행 상태 DB(SQLite 파일 하나)
  M04 어휘분석  인코딩 판별, 주석/문자열 마스킹, 함수 경계, 초기화 목록
  M05 규칙      규칙 카탈로그(T0~T3)와 탐지 함수
  M06 sLLM연결  토큰 추정과 보정, HTTP 호출, 오류 분류(한도 초과/과부하/거절)
  M07 컴파일    Visual Studio 찾기, cl /Zs 호출(환경 등급 '나'에서만)
  S0  준비      환경 점검(03_확인사항 자동화), 원본 스냅샷
  S1  인벤토리  파일 분류, 인코딩, 함수 경계, include 수집
  S2  탐지      규칙 스캔 -> 이슈
  S3  설정흡수  코드 변경 없이 옵션/매크로로 해결할 항목 정리
  S4  규칙적용  T1/T2 규칙 -> 수정 레코드
  S5  영향분석  요청사항(배열 -> 구조체) 처리, 헤더 의존 파일 파악
  S6  sLLM수정  T3 이슈를 조각으로 잘라 sLLM에 보내고 수정 블록 수용 검사
  S7  병합      수정 레코드를 적용해 결과 파일 생성
  S8  검증      L0~L2 (+ 환경 '나'면 L3 컴파일 검사), 불합격 시 되돌림
  S9  산출      diff, 보고서, 매니페스트, 수동 검토 목록
  M10 실행기    단계 순서 실행, 재개, 오류 처리
  M12 부가명령  서버 점검(llmcheck), 빌드 오류 목록 분류(buildlog)
  M11 명령줄    run / preflight / status / rules / selftest / llmcheck / buildlog

[로그 규칙]  모든 로그 줄은  "시각 수준 [모듈][함수명] 내용"  형식이다.
             실행 폴더의 logs/run.log 에 전부 남는다. 오류가 나면 이 접두어로
             어느 구획의 어느 함수인지 바로 찾을 수 있다.

[v0.4.0 에서 구현하지 않은 것]  (04_개발구조.md 의 구현 범위 표 참조)
  - sLLM 호출의 MCP 서버 경유(05_기능정의.md 10절). 지금은 설정의 주소로 직접 호출한다
  - 프로젝트 변환과 전체 빌드(환경 등급 '다')
  - .rc/.idl/.def 의 규칙 탐지
  - 보내기 전에 토큰 수를 정확히 세는 것. 글자 수로 추정하고, 서버가 응답에 담아 준 실제 값으로 보정한다
  - DB, HMI 제품의 판별 세부 규칙(널리 쓰이는 DB 접근 기술과 HMI 구성 요소는 0.6.0 에서 기본 규칙으로 넣었다)
  - 컴파일러 수준의 영향범위 분석. 구조체 변경 요청(struct_change)은 글자 기준의 근사로 처리한다
  - .vcxproj 가 가져오는 .props 파일의 설정
"""

import argparse
import bisect
import csv
import difflib
import fnmatch
import getpass
import glob
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

# ============================================================================
# [M01 공통]  상수, 로그, 작은 유틸
# 목적: 모든 구획이 같이 쓰는 최소한의 도구. 로그 접두어 규칙을 여기서 보장한다.
# ============================================================================
APP = "vupdate"
VERSION = "0.7.1"

STAGES = [("S0", "준비"), ("S1", "인벤토리"), ("S2", "탐지"), ("S3", "설정흡수"),
          ("S4", "규칙적용"), ("S5", "영향분석"), ("S6", "sLLM수정"), ("S7", "병합"),
          ("S8", "검증"), ("S9", "산출")]

EXIT_OK, EXIT_REVIEW, EXIT_FAIL, EXIT_INPUT = 0, 1, 2, 3


class UserInputError(Exception):
    """입력(경로, 프로필, 버전 값) 오류. 종료 코드 3."""


class _Sink:
    """로그 출력 대상(콘솔 + 파일). 실행 폴더가 생기기 전의 줄은 모아 뒀다가 쓴다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.fh = None
        self.early = []
        self.console_debug = False

    def open(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with self.lock:
            self.fh = open(path, "a", encoding="utf-8")
            for line in self.early:
                self.fh.write(line + "\n")
            self.early = []
            self.fh.flush()

    def close(self):
        with self.lock:
            if self.fh:
                self.fh.close()
                self.fh = None

    def write(self, level, line):
        with self.lock:
            if level != "DEBUG" or self.console_debug:
                try:
                    print(line, flush=True)
                except Exception:
                    pass
            if self.fh:
                self.fh.write(line + "\n")
                self.fh.flush()
            else:
                self.early.append(line)


_SINK = _Sink()


class Log:
    """구획별 로거. 접두어 [모듈][함수명]의 함수명은 호출한 함수 이름을 자동으로 넣는다."""

    def __init__(self, module):
        self.module = module

    def _emit(self, level, msg):
        func = sys._getframe(2).f_code.co_name
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        _SINK.write(level, "%s %-5s [%s][%s] %s" % (ts, level, self.module, func, msg))

    def debug(self, msg):
        self._emit("DEBUG", msg)

    def info(self, msg):
        self._emit("INFO", msg)

    def warn(self, msg):
        self._emit("WARN", msg)

    def error(self, msg):
        self._emit("ERROR", msg)

    def exception(self, msg):
        self._emit("ERROR", msg + "\n" + traceback.format_exc().rstrip())


LOG = Log("M01.공통")


def app_dir():
    """exe(또는 이 .py)가 놓인 폴더. config/ 를 여기서 찾는다."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def write_text(path, text, encoding="utf-8"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline="\n") as f:
        f.write(text)


def norm_rel(path):
    """DB와 보고서에 쓰는 상대 경로 표기(구분자는 항상 /)."""
    return path.replace("\\", "/")


def md_escape(s):
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


# ============================================================================
# [M02 설정]  버전 대응표, 프로필/요청사항/서버 설정
# 목적: 입력값을 한 곳에서 검증한다. VS 제품 연도, 툴셋, C++ 언어 표준은 숫자
#       체계가 서로 달라 헷갈리기 쉬우므로 축별로 형식을 검사한다(요구사항 B9).
# 수정: 새 VS 버전이 나오면 VERSION_MAP 에 한 줄 추가한다.
# ============================================================================
LOGC = Log("M02.설정")

VERSION_MAP = {
    2005: {"product": "8.0", "toolset": "v80", "msc_ver": 1400},
    2008: {"product": "9.0", "toolset": "v90", "msc_ver": 1500},
    2010: {"product": "10.0", "toolset": "v100", "msc_ver": 1600},
    2012: {"product": "11.0", "toolset": "v110", "msc_ver": 1700},
    2013: {"product": "12.0", "toolset": "v120", "msc_ver": 1800},
    2015: {"product": "14.0", "toolset": "v140", "msc_ver": 1900},
    2017: {"product": "15.x", "toolset": "v141", "msc_ver": 1910},
    2019: {"product": "16.x", "toolset": "v142", "msc_ver": 1920},
    2022: {"product": "17.x", "toolset": "v143", "msc_ver": 1930},
}
STD_ORDER = ["c++14", "c++17", "c++20", "c++latest"]

DEFAULT_SERVER = {
    "llm": {
        "endpoint": "",              # 예: http://localhost:8000/v1  (비우면 sLLM 단계는 수동 검토로 넘긴다)
        "model": "gemma4-31b",
        "api_key": "",
        "use_system_role": True,
        "max_total_tokens": 24000,   # 요청 1건의 입력+출력 합계 한도
        "max_output_tokens": 3000,
        "max_parallel": 4,           # 서버가 허용하는 동시 요청 수. --max-parallel 은 이 값 이하로만 낮출 수 있다
        "timeout_sec": 300,
        "retries": 2,                # 수정 블록 기각 시 재요청 횟수
        "need_context_rounds": 2,
        "server_prompt_reserve_tokens": 0,  # 서버가 공통 프롬프트를 덧붙일 때 그 몫으로 비워 둘 토큰 수
        "busy_retries": 5,           # 과부하 응답(429, 503)일 때 기다렸다 다시 보내는 횟수
        "transport": "openai",       # 호출 방식. openai: OpenAI 호환 HTTP. mcp: MCP 서버의 도구 호출(endpoint 는 MCP 주소)
        "mcp_tool": "llm_complete",  # transport 가 mcp 일 때 모델 호출 도구의 이름
        "mcp_info_tool": "llm_info", # 한도와 허용 동시 수를 묻는 도구. 빈 값이면 묻지 않는다
        "mcp_job_tools": False,      # 참이면 작업 시작·진행·종료를 job_open, job_progress, job_close 로 알린다
        "mcp_protocol": "2025-06-18",  # initialize 에서 제안할 MCP 프로토콜 판. 서버가 답한 판을 따른다
        "extra_body": {},            # 요청 본문에 그대로 더할 값(추론 모드 끄기 등 서버별 옵션. 요구사항 D13)
        "shared_cache_file": "",     # 실행 사이에 함께 쓰는 응답 캐시 파일(예: cache/llm_cache.db). 빈 값이면 쓰지 않는다
        "calibrate_first": True,     # 첫 요청 1건으로 실제 토큰 수를 확인한 뒤 나머지를 보낸다
    },
    "limits": {
        "big_init_chars": 2000,      # 이보다 긴 초기화 목록은 sLLM에 보내지 않고 자리표시자로 바꾼다
        "slice_context_lines": 25,   # 수정 지점 앞뒤로 같이 보낼 줄 수
        "cluster_gap_lines": 30,     # 이 줄 수 안의 수정 지점은 한 작업으로 묶는다
        "chunk_enabled": True,       # 한도를 넘는 구간을 나눠 순서대로 처리(분할 순차). False 면 수동 검토로 보낸다
        "chunk_ledger_chars": 1500,  # 뒤 조각에 넘기는 "앞 조각에서 바꾼 내용"의 최대 글자 수
        "buildlog_max_per_file": 5,  # --build-log 로 넣는 오류 가운데 한 파일에서 sLLM 에 맡길 최대 건수
        "ask_max_sites": 300,        # 사용자 요청문 하나가 sLLM 에 맡길 수 있는 줄 수의 상한. 넘는 곳은 수동 검토
        "rule_candidate_min": 3,     # 보고서의 규칙 후보: sLLM 이 같은 수정을 이 횟수 이상 했을 때 알린다
        "chunk_head_lines": 25,      # 뒤 조각에 참고로 붙이는 함수 앞부분 줄 수
        "compile_timeout_sec": 180,
        "compile_parallel": 0,       # 컴파일 검사 동시 수. 0 이면 CPU 수에 맞춘다(--compile-parallel 로도 지정)
    },
    "compile": {"mode": "changed"},  # off | changed (변경 파일만 검사)
}


def load_json(path, what):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except FileNotFoundError:
        raise UserInputError("%s 파일이 없습니다: %s" % (what, path))
    except json.JSONDecodeError as e:
        raise UserInputError("%s 파일의 JSON 형식 오류: %s (%s)" % (what, path, e))


def find_config(name, sub):
    """설정 파일 찾기. 경로를 그대로 주거나, config/<sub>/<name>.json 의 이름만 줘도 된다."""
    if os.path.isfile(name):
        return name
    cand = os.path.join(app_dir(), "config", sub, name if name.endswith(".json") else name + ".json")
    if os.path.isfile(cand):
        return cand
    raise UserInputError("설정을 찾을 수 없습니다: %s (찾아본 곳: %s)" % (name, cand))


def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_server_config(path):
    cfg = json.loads(json.dumps(DEFAULT_SERVER))
    cand = path or os.path.join(app_dir(), "config", "server.json")
    if os.path.isfile(cand):
        cfg = deep_merge(cfg, load_json(cand, "서버 설정"))
        LOGC.info("서버 설정 읽음: %s" % cand)
    elif path:
        raise UserInputError("서버 설정 파일이 없습니다: %s" % path)
    else:
        LOGC.info("서버 설정 파일 없음. 기본값 사용(sLLM 주소 미설정)")
    return cfg


_AXIS_HELP = ("버전 값은 세 가지 체계가 있습니다. "
              "VS 제품 연도(2008, 2022) / 툴셋(v90, v143) / C++ 언어 표준(c++14, c++17, c++20). "
              "9, 13, 14 처럼 어느 체계인지 알 수 없는 값은 받지 않습니다.")


def _parse_vs(value, label):
    try:
        year = int(str(value).strip())
    except ValueError:
        year = -1
    if year not in VERSION_MAP:
        raise UserInputError("%s 값이 올바르지 않습니다: %r. 사용할 수 있는 값: %s. %s"
                             % (label, value, ", ".join(str(y) for y in sorted(VERSION_MAP)), _AXIS_HELP))
    return year


_COMPILER_KEYS = {"vs", "toolset", "cpp_standard", "conformance", "platform", "charset"}
AXIS_LABELS = {"os": "OS", "sdk": "Windows SDK", "db": "DB", "hmi": "HMI 개발도구"}


def collect_axes(prof_from, prof_to, args):
    """컴파일러 말고 다른 환경값 축(OS, DB, HMI 개발도구 등)을 모은다(요구사항 B1).
    프로필의 from/to 에 있는 값 가운데 컴파일러 쪽 키가 아닌 것은 전부 축으로 본다. 이름은 자유다.
    명령줄에서는 --axis 이름=값 또는 --axis "이름=원본 값->대상 값" 으로 준다(프로필보다 우선).
    돌려주는 값: {이름: {"from": 값, "to": 값}}. 쓰이는 곳: 프롬프트의 [환경], 보고서, 규칙의 발동 조건(when.axis)."""
    axes = OrderedDict()

    def put(name, side, value):
        name = str(name).strip().lower()
        if not re.match(r"^\w{1,20}$", name):
            raise UserInputError("환경값 축의 이름이 올바르지 않습니다(글자, 숫자, 밑줄 20자 이내): %r" % name)
        value = " ".join(str(value).split())
        if len(value) > 80:
            raise UserInputError("환경값 %s 의 값이 너무 깁니다(80자 이내): %r" % (name, value))
        axes.setdefault(name, {"from": "", "to": ""})[side] = value

    for side, src in (("from", prof_from), ("to", prof_to)):
        for k, v in src.items():
            if k not in _COMPILER_KEYS and not isinstance(v, (dict, list)):
                put(k, side, v)
    for item in getattr(args, "axis", None) or []:
        if "=" not in str(item):
            raise UserInputError("--axis 는 이름=값 또는 \"이름=원본 값->대상 값\" 형식입니다: %r" % (item,))
        name, val = str(item).split("=", 1)
        if "->" in val:
            a, b = val.split("->", 1)
            put(name, "from", a)
            put(name, "to", b)
        else:
            put(name, "to", val)
    return axes


def axis_text(a):
    """축 하나를 '원본 -> 대상' 꼴의 한 줄로."""
    f, t = (a.get("from") or "").strip(), (a.get("to") or "").strip()
    if f and t and f.lower() != t.lower():
        return "%s -> %s" % (f, t)
    return t or ("%s (대상 미지정)" % f if f else "")


def build_env(args):
    """프로필 파일과 명령줄 인자를 합쳐 환경값을 만든다. 명시하지 않은 축은 기본값(B9)."""
    prof = {}
    if getattr(args, "profile", None):
        prof = load_json(find_config(args.profile, "profiles"), "프로필")
    f = dict(prof.get("from") or {})
    t = dict(prof.get("to") or {})
    pol = dict(prof.get("policy") or {})
    if getattr(args, "from_vs", None):
        f["vs"] = args.from_vs
    if getattr(args, "to_vs", None):
        t["vs"] = args.to_vs
    if getattr(args, "cpp_standard", None):
        t["cpp_standard"] = args.cpp_standard
    for key in ("platform", "conformance", "charset"):     # 명령줄에 준 값이 프로필보다 우선한다
        if getattr(args, key, None):
            t[key] = getattr(args, key)
    if getattr(args, "deprecated", None):
        pol["deprecated_warnings"] = args.deprecated
    if "vs" not in f or "vs" not in t:
        raise UserInputError("원본과 대상의 VS 제품 연도가 필요합니다. --from-vs 2008 --to-vs 2022 처럼 "
                             "주거나 --profile 로 프로필을 지정하세요.")
    from_vs = _parse_vs(f["vs"], "원본 VS(from.vs)")
    to_vs = _parse_vs(t["vs"], "대상 VS(to.vs)")
    if to_vs <= from_vs:
        raise UserInputError("대상 VS(%d)는 원본 VS(%d)보다 뒤여야 합니다." % (to_vs, from_vs))

    std = t.get("cpp_standard")
    if to_vs < 2015:
        std = None                       # 2015 이전에는 언어 표준 선택 옵션이 없다
    else:
        std = str(std or "c++14").strip().lower()
        if std not in STD_ORDER:
            raise UserInputError("C++ 언어 표준 값이 올바르지 않습니다: %r. 사용할 수 있는 값: %s. %s"
                                 % (t.get("cpp_standard"), ", ".join(STD_ORDER), _AXIS_HELP))
    from_std = str(getattr(args, "from_cpp_standard", None) or f.get("cpp_standard") or "").strip().lower() or None
    if from_std and from_std not in STD_ORDER:
        raise UserInputError("원본 쪽 C++ 언어 표준 값이 올바르지 않습니다: %r. 사용할 수 있는 값: %s"
                             % (from_std, ", ".join(STD_ORDER)))
    toolset = VERSION_MAP[to_vs]["toolset"]
    if t.get("toolset"):
        ts = str(t["toolset"]).strip().lower()
        if not re.match(r"^v\d{2,3}$", ts):
            raise UserInputError("툴셋 값이 올바르지 않습니다: %r (예: v143). %s" % (t["toolset"], _AXIS_HELP))
        if ts != toolset:
            LOGC.warn("프로필의 툴셋 %s 이(가) VS %d 의 기본 툴셋 %s 과(와) 다릅니다. 프로필 값을 씁니다."
                      % (ts, to_vs, toolset))
        toolset = ts

    def pick(d, key, allowed, default):
        v = str(d.get(key, default)).strip().lower()
        if v not in allowed:
            raise UserInputError("%s 값이 올바르지 않습니다: %r. 사용할 수 있는 값: %s"
                                 % (key, d.get(key, default), ", ".join(allowed)))
        return v

    env = {
        "from_vs": from_vs, "to_vs": to_vs, "from_std": from_std,
        "from_toolset": VERSION_MAP[from_vs]["toolset"], "to_toolset": toolset,
        "cpp_standard": std,
        "conformance": pick(t, "conformance", ["off", "on"], "off"),
        "platform": pick(t, "platform", ["keep", "x86", "x64"], "keep"),
        "charset": pick(t, "charset", ["keep", "mbcs", "unicode"], "keep"),
        "policy": {"deprecated_warnings": pick(pol, "deprecated_warnings", ["suppress", "fix"], "suppress")},
        "axes": collect_axes(f, t, args),
    }
    if std in ("c++20", "c++latest") and env["conformance"] == "off":
        env["conformance"] = "on"
        LOGC.warn("언어 표준 %s 은(는) 준수 모드(/permissive-)를 함께 켠다(VS2019 16.11 부터). 준수 모드를 켠 것으로 처리한다. "
                  "끄려면 프로젝트에 /permissive 를 따로 줘야 한다" % std)
    LOGC.info("환경값 확정: VS %d(%s) -> VS %d(%s), 언어 표준 %s, 준수 모드 %s, 플랫폼 %s, 문자셋 %s, "
              "deprecated 경고 %s" % (from_vs, env["from_toolset"], to_vs, toolset, std or "(선택 없음)",
                                    env["conformance"], env["platform"], env["charset"],
                                    env["policy"]["deprecated_warnings"]))
    if env["axes"]:
        LOGC.info("그 밖의 환경값: %s" % "; ".join("%s = %s" % (AXIS_LABELS.get(k, k), axis_text(a)) for k, a in env["axes"].items()))
    return env


def env_summary_lines(env):
    """환경값을 사람이 읽는 줄 목록으로. 보고서와 sLLM 프롬프트의 [환경]에 그대로 들어간다."""
    lines = [
        "VS 제품: %d -> %d" % (env["from_vs"], env["to_vs"]),
        "툴셋: %s -> %s" % (env["from_toolset"], env["to_toolset"]),
        "C++ 언어 표준: %s" % (env["cpp_standard"] or "(선택 없음)"),
        "준수 모드(/permissive-): %s" % env["conformance"],
        "플랫폼: %s" % env["platform"],
        "문자셋: %s" % env["charset"],
        "deprecated 경고 처리: %s" % env["policy"]["deprecated_warnings"],
    ]
    for name, a in (env.get("axes") or {}).items():
        lines.append("%s: %s" % (AXIS_LABELS.get(name, name), axis_text(a)))
    return lines


def _load_request_file(args):
    """요청 파일(--request) 읽기. 없으면 빈 요청.
    features: 기능 변경(배열 -> 구조체 등). 규칙으로 처리할 수 있는 부분은 규칙으로 처리한다.
    common  : 공통 요청. 문장 목록이며 sLLM 에 보내는 모든 조각의 [수정 지침] 뒤에 붙는다. 규칙 수정에는 영향이 없다."""
    if not getattr(args, "request", None):
        return {"features": [], "common": []}
    path = find_config(args.request, "requests")
    req = load_json(path, "요청사항")
    feats = req.get("features") or []
    base = os.path.dirname(os.path.abspath(path))
    for ft in feats:
        if "id" not in ft or "type" not in ft:
            raise UserInputError("요청사항의 각 항목에는 id 와 type 이 필요합니다: %r" % (ft,))
        if ft["type"] == "struct_change":
            if "target" not in ft or not isinstance(ft.get("rename") or {}, dict):
                raise UserInputError("struct_change 요청에는 target(구조체 이름)이 필요하고 rename 은 {옛 이름: 새 이름} 이어야 합니다: %s" % ft["id"])
            if ft.get("definition_after_file"):
                dpath = ft["definition_after_file"]
                dpath = dpath if os.path.isabs(dpath) else os.path.join(base, dpath)
                if not os.path.isfile(dpath):
                    raise UserInputError("구조체 정의 파일이 없습니다: %s" % dpath)
                ft["definition_after"] = read_bytes(dpath).decode("utf-8-sig", "replace").replace("\r\n", "\n").strip()
            if not ft.get("rename") and not ft.get("definition_after"):
                raise UserInputError("struct_change 요청에는 rename 이나 definition_after 중 하나가 필요합니다: %s" % ft["id"])
            continue
        if ft["type"] == "array_to_struct":
            if "target" not in ft:
                raise UserInputError("array_to_struct 요청에는 target(배열 이름)이 필요합니다: %s" % ft["id"])
            mp = ft.get("mapping")
            if isinstance(mp, str):          # CSV 파일: 인덱스,필드명
                mpath = mp if os.path.isabs(mp) else os.path.join(base, mp)
                table = {}
                try:
                    with open(mpath, "r", encoding="utf-8-sig", newline="") as f:
                        for row in csv.reader(f):
                            if len(row) >= 2 and row[0].strip() and not row[0].strip().startswith("#"):
                                table[row[0].strip()] = row[1].strip()
                except FileNotFoundError:
                    raise UserInputError("매핑표 파일이 없습니다: %s" % mpath)
                ft["mapping"] = table
            elif not isinstance(mp, dict) or not mp:
                raise UserInputError("array_to_struct 요청에는 mapping(인덱스 -> 필드명)이 필요합니다: %s" % ft["id"])
    common = req.get("common") or []
    if isinstance(common, str):
        common = [common]
    common = [" ".join(str(x).split()) for x in common if str(x).strip()]
    if sum(len(x) for x in common) > 1500:
        raise UserInputError("공통 요청(common)이 너무 깁니다(합계 1,500자 이내). 모든 sLLM 요청에 붙으므로 짧게 적습니다.")
    LOGC.info("요청사항 읽음: %s (기능 변경 %d건, 공통 요청 %d건)" % (path, len(feats), len(common)))
    raw = []                                  # 요청 파일의 asks: 줄 글("대상: 요청문") 또는 {"target", "text", "files"}
    for it in req.get("asks") or []:
        if isinstance(it, dict):
            tg = it.get("targets") or it.get("target") or "*"
            tg = ", ".join(tg) if isinstance(tg, list) else str(tg)
            it = "%s%s: %s" % (("@%s " % it["files"]) if it.get("files") else "", tg, it.get("text", ""))
        raw.append(str(it))
    return {"features": feats, "common": common, "_asks_raw": raw}


# ----------------------------------------------------------------------------
# 실행 옵션: 실행할 때마다 사람이 정하는 값(ctx.opts)과 PC 마다 다른 값(환경변수)
# 값이 놓이는 자리(06_충족점검.md 5.3)
#   실행 옵션   : 대상과 환경값, 처리 범위, 단계 건너뛰기, 동시 수 낮추기, 호출자
#   환경변수    : sLLM(중계 서버) 주소, Visual Studio 설치 폴더. exe 에 주소를 넣어 배포하지 않기 위한 것이다
#   서버 설정   : 토큰 한도, 허용 동시 수, 모델 이름. 서버가 정하는 값이며 py 는 받아 쓰기만 한다
# ----------------------------------------------------------------------------
ENV_LLM_ENDPOINT = "VUPDATE_LLM_ENDPOINT"     # sLLM(또는 중계 서버) 주소. --llm-endpoint 를 주지 않았을 때 쓴다
ENV_VS_PATH = "VUPDATE_VS_PATH"               # Visual Studio 설치 폴더. --vs-path 를 주지 않았을 때 쓴다
ENV_SDK_PATH = "VUPDATE_SDK_PATH"             # Windows SDK 폴더. --sdk-path 를 주지 않았을 때 쓴다
ENV_SDK_PATH_TYPO = "VUPDATE_SKD_PATH"        # 흔한 오타. 값이 있으면 받아 주되 경고한다


def load_exceptions(path):
    """예외 파일을 읽는다(요구사항 C11 프로젝트별 예외, J4 고유 매크로). 경로가 없으면 빈 예외를 돌려준다.
    형식: {"rules_off": ["규칙 ID"],
           "skip": [{"path": "Src/Legacy/*", "rules": ["규칙 ID"], "lines": "120-140"}],
           "brace_macros": {"open": ["BEGIN_NS"], "close": ["END_NS"], "ignore": ["DECLARE_X"]}}
    skip 항목에서 rules 를 생략하면 모든 규칙, lines 를 생략하면 파일 전체다. path 는 패키지 기준 경로(와일드카드 가능)다."""
    exc = {"rules_off": [], "skip": [], "brace_macros": {"open": [], "close": [], "ignore": []}}
    if not path:
        return exc
    raw = load_json(path, "예외")
    if not isinstance(raw, dict):
        raise UserInputError("예외 파일은 JSON 객체여야 합니다: %s" % path)
    exc["rules_off"] = [str(x).strip() for x in raw.get("rules_off") or [] if str(x).strip()]
    for it in raw.get("skip") or []:
        if not isinstance(it, dict) or not it.get("path"):
            raise UserInputError("예외 파일의 skip 항목에는 path 가 필요합니다: %r" % (it,))
        lines = None
        if it.get("lines"):
            m = re.match(r"^\s*(\d+)\s*(?:-\s*(\d+))?\s*$", str(it["lines"]))
            if not m:
                raise UserInputError("예외 파일의 lines 는 '120' 또는 '120-140' 으로 적습니다: %r" % (it["lines"],))
            lines = (int(m.group(1)), int(m.group(2) or m.group(1)))
        rules = it.get("rules") or ["*"]
        rules = [rules] if isinstance(rules, str) else rules
        exc["skip"].append({"path": str(it["path"]).replace("\\", "/").strip("/").lower(),
                            "rules": {str(x).upper() for x in rules}, "lines": lines})
    bm = raw.get("brace_macros") or {}
    for k in ("open", "close", "ignore"):
        exc["brace_macros"][k] = [str(x) for x in bm.get(k) or [] if re.match(r"^[A-Za-z_]\w*$", str(x))]
    LOGC.info("예외 파일 읽음: %s (끈 규칙 %d개, 제외 항목 %d개, 고유 매크로 %d개)"
              % (path, len(exc["rules_off"]), len(exc["skip"]), sum(len(v) for v in exc["brace_macros"].values())))
    return exc


def exc_skip(exc, rel, rule_id, line):
    """탐지 지점이 예외 파일의 skip 항목에 걸리면 True."""
    r = rel.lower()
    for it in exc.get("skip") or []:
        p = it["path"]
        if not (fnmatch.fnmatch(r, p) or r.startswith(p + "/") or os.path.basename(r) == p):
            continue
        if "*" not in it["rules"] and rule_id.upper() not in it["rules"]:
            continue
        if it["lines"] and not (it["lines"][0] <= line <= it["lines"][1]):
            continue
        return True
    return False


_ASK_TARGET = r"(?:/[^/]+/|[A-Za-z_]\w*)"
_ASK_LINE_RE = re.compile(r"^(?:@(?P<files>\S+)\s+)?(?P<targets>%s(?:\s*,\s*%s)*)\s*:\s*(?P<text>\S.*)$" % (_ASK_TARGET, _ASK_TARGET))


def parse_asks(lines):
    """사용자 요청문을 읽는다(처음 요구의 "요청문을 파라미터로 넣는다"에 해당). 한 줄에 요청 하나다.
        대상: 요청문                      대상 이름이 쓰인 곳을 찾아 그 요청문대로 고치게 sLLM 에 맡긴다
        대상1, 대상2: 요청문              대상이 여럿
        /정규식/: 요청문                  이름 대신 정규식으로 찾는다
        @Src/Net/* 대상: 요청문           그 파일들에서만
        *: 요청문  또는  대상 없는 문장    공통 요청(sLLM 에 보내는 모든 조각의 지침에 붙는다)
    '#' 으로 시작하는 줄과 빈 줄은 건너뛴다. 돌려주는 값: (요청 목록, 공통 요청 목록)"""
    asks, common = [], []
    for raw in lines:
        line = " ".join(str(raw).replace("﻿", "").split())
        if not line or line.startswith("#"):
            continue
        if line.startswith("*:"):
            common.append(line[2:].strip())
            continue
        m = _ASK_LINE_RE.match(line)
        if not m:
            common.append(line)
            continue
        targets = re.findall(_ASK_TARGET, m.group("targets"))
        for t in targets:
            if t.startswith("/"):
                try:
                    re.compile(t[1:-1])
                except re.error as e:
                    raise UserInputError("요청문의 정규식이 올바르지 않습니다: %s (%s)" % (t, e))
        asks.append({"id": "ASK-%d" % (len(asks) + 1), "targets": targets, "text": m.group("text").strip(),
                     "files": (m.group("files") or "").replace("\\", "/").strip("/").lower()})
    return asks, common


def load_request(args):
    """요청사항 전체를 읽는다.
      - 요청 파일(--request)의 정형 요청(features), 공통 요청(common), 요청문(asks)
      - 요청문 파일(--ask-file. 줄마다 요청 하나)과 명령줄의 요청문(--ask)
    돌려주는 값: {"features": [...], "common": [...], "asks": [...]}. 실행 정보에 저장돼 이어서 실행할 때 그대로 쓰인다."""
    req = _load_request_file(args)
    lines = list(req.pop("_asks_raw", []))
    path = getattr(args, "ask_file", None)
    if path:
        if not os.path.isfile(path):
            raise UserInputError("요청문 파일이 없습니다: %s" % path)
        lines += read_log_text(path).splitlines()
    lines += [str(x) for x in (getattr(args, "ask", None) or [])]
    asks, common = parse_asks(lines)
    req["asks"] = asks
    req["common"] = list(req.get("common") or []) + common
    if sum(len(x) for x in req["common"]) > 1500:
        raise UserInputError("공통 요청이 너무 깁니다(합계 1,500자 이내). 모든 sLLM 요청에 붙으므로 짧게 적습니다.")
    if asks or common:
        LOGC.info("사용자 요청문 %d건, 대상 없는 공통 요청 %d건을 읽음" % (len(asks), len(common)))
    return req


def default_caller():
    """호출자 표시의 기본값: PC 이름\\계정. 서버 쪽 기록에서 누가 보낸 요청인지 가리는 데 쓴다."""
    host = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or ""
    if not host:
        try:
            host = socket.gethostname()
        except Exception:
            host = "unknown-pc"
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        try:
            user = getpass.getuser()
        except Exception:
            user = "unknown"
    return "%s\\%s" % (host, user)


def _split_patterns(values):
    """--only, --exclude 값(여러 번 주거나 쉼표로 이어 준 것)을 패턴 목록으로 만든다."""
    out = []
    for v in values or []:
        for p in str(v).split(","):
            p = p.strip().replace("\\", "/").strip("/").lower()
            if p and p not in out:
                out.append(p)
    return out


def in_scope(rel, only, exclude):
    """처리 범위에 드는 파일인가. 패턴은 패키지 기준 상대 경로에 대는 와일드카드(*, ?)이고 대소문자를 가리지 않는다.
    와일드카드 없이 폴더나 파일 이름만 줘도 된다(Src/Net, Main.cpp)."""
    r = rel.lower()

    def hit(p):
        return fnmatch.fnmatchcase(r, p) or fnmatch.fnmatchcase(r, "*/" + p) or ("/" + p + "/") in ("/" + r)

    if only and not any(hit(p) for p in only):
        return False
    return not any(hit(p) for p in exclude or [])


def collect_opts(args, saved=None):
    """실행 옵션 묶음(ctx.opts)을 만든다.
    이어서 실행할 때(saved 가 None 이 아닐 때)는 처음 실행의 값에서 시작하고, 명령줄에 다시 준 값만 덮어쓴다.
    처리 범위(only, exclude)와 호출자는 처음 실행의 값을 그대로 쓴다(중간에 바꾸면 앞 단계 결과와 어긋난다)."""
    o = {"no_llm": False, "no_chunk": False, "no_compile": False, "only": [], "exclude": [], "caller": "",
         "max_parallel": 0, "compile_parallel": -1, "disable_rules": [], "exceptions": "", "build_log": ""}
    if saved is not None:
        o.update({k: saved[k] for k in o if k in saved})
    for k in ("no_llm", "no_chunk", "no_compile"):
        if getattr(args, k, False):
            o[k] = True
    v = getattr(args, "max_parallel", None)
    if v is not None:
        if int(v) < 1:
            raise UserInputError("--max-parallel 은 1 이상이어야 합니다: %s" % v)
        o["max_parallel"] = int(v)
    v = getattr(args, "compile_parallel", None)
    if v is not None:
        if int(v) < 0:
            raise UserInputError("--compile-parallel 은 0(자동) 이상이어야 합니다: %s" % v)
        o["compile_parallel"] = int(v)
    if saved is None:                         # 예외와 빌드 로그도 처음 실행의 값을 그대로 쓴다
        o["disable_rules"] = [x.strip() for v in (getattr(args, "disable_rule", None) or []) for x in str(v).split(",") if x.strip()]
        for key, what in (("exceptions", "예외 파일"), ("build_log", "빌드 로그 파일")):
            p = getattr(args, key, None) or ""
            if p and not os.path.isfile(p):
                raise UserInputError("%s이 없습니다: %s" % (what, p))
            o[key] = os.path.abspath(p) if p else ""
    only, excl = _split_patterns(getattr(args, "only", None)), _split_patterns(getattr(args, "exclude", None))
    if saved is not None:
        if (only and only != o["only"]) or (excl and excl != o["exclude"]):
            LOGC.warn("이어서 실행할 때는 처리 범위(--only, --exclude)를 바꿀 수 없습니다. 처음 실행의 값을 씁니다")
        o["caller"] = o["caller"] or default_caller()
    else:
        o["only"], o["exclude"] = only, excl
        o["caller"] = (getattr(args, "caller", None) or "").strip() or default_caller()
    return o


# ============================================================================
# [M03 저장소]  실행 상태 DB
# 목적: 단계 사이에 주고받는 모든 자료(파일, 함수, 이슈, 수정 레코드, 작업)를
#       실행 폴더의 state.db 한 파일에 둔다. 중단 후 재개와 보고서의 근거가 된다.
# 주의: DB는 주 스레드에서만 쓴다. 병렬 작업은 결과를 돌려주기만 한다.
# ============================================================================
LOGS = Log("M03.저장소")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, sha256 TEXT, kind TEXT,
    encoding TEXT, bom INTEGER, eol TEXT, roundtrip INTEGER, parse_ok INTEGER, n_lines INTEGER,
    status TEXT, verify_level TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS functions(id INTEGER PRIMARY KEY, path TEXT, name TEXT, sig_start INTEGER,
    body_start INTEGER, end INTEGER, start_line INTEGER, end_line INTEGER, est_tokens INTEGER);
CREATE TABLE IF NOT EXISTS big_inits(id INTEGER PRIMARY KEY, path TEXT, start INTEGER, end INTEGER,
    length INTEGER, sha256 TEXT);
CREATE TABLE IF NOT EXISTS includes(path TEXT, header TEXT, angle INTEGER);
CREATE TABLE IF NOT EXISTS issues(id INTEGER PRIMARY KEY, path TEXT, start INTEGER, end INTEGER,
    line INTEGER, source TEXT, rule_id TEXT, tier TEXT, llm INTEGER, title TEXT, detail TEXT,
    after TEXT, review INTEGER, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS edits(id INTEGER PRIMARY KEY, path TEXT, base TEXT, start INTEGER,
    end INTEGER, before TEXT, after TEXT, origin TEXT, ref TEXT, issue_ids TEXT, review INTEGER,
    status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, path TEXT, start_line INTEGER,
    end_line INTEGER, issue_ids TEXT, status TEXT, attempts INTEGER, note TEXT,
    grp INTEGER, part INTEGER, parts INTEGER);
CREATE TABLE IF NOT EXISTS llm_calls(id INTEGER PRIMARY KEY, task_id INTEGER, attempt INTEGER,
    prompt_file TEXT, response_file TEXT, est_in INTEGER, ms INTEGER, ok INTEGER, note TEXT,
    tok_in INTEGER, tok_out INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, response TEXT);
CREATE TABLE IF NOT EXISTS verifications(id INTEGER PRIMARY KEY, path TEXT, level TEXT,
    result TEXT, detail TEXT);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self.con = sqlite3.connect(path)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(_SCHEMA)
        # 이전 판으로 만든 실행 폴더를 이어서 쓸 때를 위한 열 추가(이미 있으면 건너뛴다)
        for tbl, col in (("tasks", "grp INTEGER"), ("tasks", "part INTEGER"), ("tasks", "parts INTEGER"),
                         ("llm_calls", "tok_in INTEGER"), ("llm_calls", "tok_out INTEGER"), ("llm_calls", "kind TEXT")):
            try:
                self.con.execute("ALTER TABLE %s ADD COLUMN %s" % (tbl, col))
            except sqlite3.OperationalError:
                pass
        self.con.commit()

    def x(self, sql, params=()):
        return self.con.execute(sql, params)

    def many(self, sql, rows):
        self.con.executemany(sql, rows)

    def q(self, sql, params=()):
        return self.con.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        row = self.con.execute(sql, params).fetchone()
        return row[0] if row else None

    def commit(self):
        self.con.commit()

    def close(self):
        self.con.commit()
        self.con.close()

    def get_meta(self, key, default=None):
        v = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(v) if v is not None else default

    def set_meta(self, key, value):
        self.x("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))
        self.con.commit()


# ============================================================================
# [M04 어휘분석]  인코딩 판별, 주석/문자열 마스킹, 함수 경계, 초기화 목록
# 목적: 컴파일러 없이도 "코드인 부분"과 "주석/문자열"을 가르고, 함수와 큰 상수
#       배열의 범위를 찾는다. 규칙 탐지와 sLLM 조각 자르기의 기반이다.
# 한계: 전처리기를 실행하지 않는다. 매크로가 문법을 바꾸거나 #if 가 괄호를
#       나누는 파일은 parse_ok=False 로 표시하고 줄 단위 처리로 내려간다.
# ============================================================================
LOGX = Log("M04.어휘분석")

# 임베디드 SQL 의 선행 컴파일 소스(.pc Pro*C, .tbc tbESQL, .pgc ECPG, .sqc, .ec)도 소스로 본다(요구사항 A3). C 문법에 EXEC SQL 이 섞인 파일이다
SOURCE_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl", ".pc", ".tbc", ".pgc", ".sqc", ".ec"}
PROJECT_EXT = {".sln", ".vcproj", ".vcxproj", ".dsw", ".dsp", ".filters", ".vsprops", ".props"}
TEXT_OTHER_EXT = {".rc", ".rc2", ".idl", ".odl", ".def", ".bat", ".cmd", ".mak", ".pc"}
BINARY_EXT = {".lib", ".dll", ".exe", ".obj", ".pdb", ".ocx", ".res", ".ilk", ".exp", ".pch", ".ncb",
              ".suo", ".aps", ".tlb", ".bmp", ".ico", ".png", ".jpg", ".gif", ".zip", ".chm"}


def classify(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext in SOURCE_EXT:
        return "source"
    if ext in PROJECT_EXT:
        return "project"
    if ext in TEXT_OTHER_EXT:
        return "text"
    if ext in BINARY_EXT:
        return "binary"
    return "other"


def decode_bytes(data):
    """바이트를 글자로. 돌려주는 값: (본문, 코덱, 표시용 이름, BOM 바이트) 또는 None(판별 불가)."""
    if data[:3] == b"\xef\xbb\xbf":
        bom, codec, label = data[:3], "utf-8", "utf-8-bom"
    elif data[:2] == b"\xff\xfe":
        bom, codec, label = data[:2], "utf-16-le", "utf-16-le"
    elif data[:2] == b"\xfe\xff":
        bom, codec, label = data[:2], "utf-16-be", "utf-16-be"
    else:
        bom = b""
        if b"\x00" in data:
            return None
        try:
            data.decode("ascii")
            codec, label = "cp949", "ascii"     # 순수 영문 파일. 기록은 ascii, 쓰기는 cp949 로 한다
        except UnicodeDecodeError:
            try:
                data.decode("utf-8")
                codec, label = "utf-8", "utf-8"
            except UnicodeDecodeError:
                try:
                    data.decode("cp949")
                    codec, label = "cp949", "cp949"
                except UnicodeDecodeError:
                    return None
    try:
        return data[len(bom):].decode(codec), codec, label, bom
    except UnicodeDecodeError:
        return None


def normalize_eol(raw_text):
    """줄끝이 전부 CRLF 인 파일만 LF 로 바꿔 작업한다. 섞여 있으면 원문 그대로 둔다."""
    n_crlf = raw_text.count("\r\n")
    n_lf = raw_text.count("\n") - n_crlf
    n_cr = raw_text.count("\r") - n_crlf
    if n_crlf and not n_lf and not n_cr:
        return raw_text.replace("\r\n", "\n"), "crlf"
    if not n_crlf and not n_cr:
        return raw_text, "lf"
    return raw_text, "mixed"


_TOKEN_RE = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_NONNL_RE = re.compile(r"[^\r\n]")
_PP_RE = re.compile(r"^[ \t]*#(?:\\\r?\n|[^\n])*", re.M)


def _blank(s):
    return _NONNL_RE.sub(" ", s)


def mask_code(text):
    """주석 전체와 문자열/문자 상수의 내용을 공백으로 바꾼다. 길이와 줄 위치는 그대로다.
    돌려주는 값: (마스킹한 본문, 큰따옴표 문자열의 (시작, 끝) 목록)"""
    parts, strings, last = [], [], 0
    for m in _TOKEN_RE.finditer(text):
        s, e = m.span()
        tok = m.group()
        parts.append(text[last:s])
        if tok[0] == "/":
            parts.append(_blank(tok))
        else:
            parts.append(tok[0] + _blank(tok[1:-1]) + tok[-1])
            if tok[0] == '"':
                strings.append((s, e))
        last = e
    parts.append(text[last:])
    return "".join(parts), strings


def mask_preprocessor(code):
    """전처리 지시문 줄을 공백으로 바꾼다(괄호 짝 분석용)."""
    return _PP_RE.sub(lambda m: _blank(m.group()), code)


_BR_RE = re.compile(r"[{};]")
_CONTAINER_RE = re.compile(r'^(?:template\s*<[^{};]*>\s*)?(?:typedef\s+)?(?:class|struct|union|namespace)\b|^extern\s*"')
_ENUM_RE = re.compile(r"^(?:typedef\s+)?enum\b")
_DECLSPEC_RE = re.compile(r"__declspec\s*\([^)]*\)")
_TAIL_QUAL_RE = re.compile(r"(?:\b(?:const|override|final|volatile)\b|\bthrow\s*\([^()]*\))\s*$")
_CTOR_INIT_RE = re.compile(r"\)\s*:(?!:)")
_NAME_TAIL_RE = re.compile(r"([A-Za-z_~][\w~]*(?:\s*::\s*[A-Za-z_~][\w~]*)*|operator\s*[^\s(]+)\s*$")
_CTRL_WORDS = {"if", "for", "while", "switch", "catch", "else", "do", "try", "return", "sizeof"}


def _func_name(header):
    """'{' 앞의 글에서 함수 이름과 그 위치를 찾는다. 함수가 아니면 ('', -1)."""
    h = header.rstrip()
    while True:
        h2 = _TAIL_QUAL_RE.sub("", h).rstrip()
        if h2 == h:
            break
        h = h2
    m = _CTOR_INIT_RE.search(h)
    if m:
        h = h[:m.start() + 1]
    if not h.endswith(")"):
        return "", -1
    depth, j = 0, len(h) - 1
    while j >= 0:
        if h[j] == ")":
            depth += 1
        elif h[j] == "(":
            depth -= 1
            if depth == 0:
                break
        j -= 1
    if j < 0:
        return "", -1
    m2 = _NAME_TAIL_RE.search(h[:j])
    if not m2:
        return "", -1
    name = re.sub(r"\s+", "", m2.group(1))
    if name.split("::")[-1] in _CTRL_WORDS:
        return "", -1
    return name, m2.start(1)


def analyze_structure(struct):
    """괄호 짝으로 함수와 초기화 목록의 범위를 찾는다.
    돌려주는 값: (함수 목록, 초기화 목록 (시작, 끝) 목록, 괄호 짝이 맞았는지)"""
    stack, funcs, inits, ok, stmt = [], [], [], True, 0
    for m in _BR_RE.finditer(struct):
        i = m.start()
        c = struct[i]
        if c == ";":
            stmt = i + 1
        elif c == "{":
            header = struct[stmt:i]
            hs = header.strip()
            parent = stack[-1][0] if stack else None
            kind, name, sig = "other", "", i
            if hs.endswith("="):
                kind = "init"
            elif parent in (None, "container"):
                hs2 = _DECLSPEC_RE.sub("", hs)
                if _ENUM_RE.match(hs2):
                    kind = "other"
                elif _CONTAINER_RE.match(hs2) and ")" not in hs2:
                    kind = "container"
                elif ")" in hs:
                    name, pos = _func_name(header)
                    if name:
                        kind = "func"
                        sig = stmt + header.rfind("\n", 0, pos) + 1
            stack.append((kind, i, sig, name))
            stmt = i + 1
        else:
            if not stack:
                ok = False
            else:
                kind, op, sig, name = stack.pop()
                if kind == "func":
                    funcs.append({"name": name, "sig_start": sig, "body_start": op, "end": i + 1})
                elif kind == "init":
                    inits.append((op, i + 1))
            stmt = i + 1
    if stack:
        ok = False
    return funcs, inits, ok


class Doc:
    """파일 하나의 분석 결과.
    text   : 작업 본문(줄끝이 전부 CRLF 면 LF 로 바꾼 것). 모든 위치 값은 이 본문 기준이다.
    code   : 주석과 문자열 내용을 가린 본문(규칙 탐지용)
    struct : code 에서 전처리 지시문까지 가린 본문(괄호 짝 분석용)"""

    def __init__(self):
        self.rel = ""
        self.ok = False
        self.note = ""
        self.codec = self.label = self.eol = ""
        self.bom = b""
        self.roundtrip = False
        self.text = self.code = self.struct = ""
        self.strings, self.funcs, self.inits, self.line_starts = [], [], [], [0]
        self.parse_ok = False

    def line_of(self, pos):
        return bisect.bisect_right(self.line_starts, pos)

    def line_span(self, line):
        """line(1부터)의 (시작, 끝) 위치. 끝은 줄바꿈 문자 앞이다."""
        s = self.line_starts[line - 1]
        e = self.line_starts[line] - 1 if line < len(self.line_starts) else len(self.text)
        return s, e

    @property
    def n_lines(self):
        return len(self.line_starts)


def encode_work(doc, work_text):
    """작업 본문을 원본과 같은 인코딩/BOM/줄끝의 바이트로 되돌린다."""
    raw = work_text.replace("\n", "\r\n") if doc.eol == "crlf" else work_text
    return doc.bom + raw.encode(doc.codec)


_BRACE_MACROS = {"open": [], "close": [], "ignore": []}     # 프로젝트 고유 매크로(J4). set_brace_macros 로만 바꾼다


def set_brace_macros(bm):
    """문법을 바꾸는 프로젝트 고유 매크로를 정한다(예외 파일의 brace_macros). 함수 경계 분석에만 쓰인다.
    이 값은 실행을 시작할 때 한 번 정하고, 그 뒤에는 바꾸지 않는다(분석 결과가 파일마다 달라지면 안 된다)."""
    for k in _BRACE_MACROS:
        _BRACE_MACROS[k] = list((bm or {}).get(k) or [])


def _apply_brace_macros(struct):
    """고유 매크로를 같은 길이의 괄호나 공백으로 바꾼 사본을 돌려준다. 글자 위치는 원문과 같다.
    open 은 '{', close 는 '}' 로 본다. ignore 는 이름과 바로 뒤의 인자 목록을 지우고 끝에 ';' 를 둔다
    (세미콜론 없이 쓰는 선언 매크로가 다음 함수의 머리로 읽히는 것을 막는다)."""
    if not any(_BRACE_MACROS.values()):
        return struct
    for names, ch in ((_BRACE_MACROS["open"], "{"), (_BRACE_MACROS["close"], "}")):
        if names:
            rx = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in names))
            struct = rx.sub(lambda m, ch=ch: ch + " " * (len(m.group(0)) - 1), struct)
    if _BRACE_MACROS["ignore"]:
        rx = re.compile(r"\b(?:%s)\b(?:[ \t]*\([^()\n]*\))?" % "|".join(re.escape(n) for n in _BRACE_MACROS["ignore"]))
        struct = rx.sub(lambda m: " " * (len(m.group(0)) - 1) + ";", struct)
    return struct


def make_doc(rel, data, analyze=True):
    d = Doc()
    d.rel = rel
    dec = decode_bytes(data)
    if dec is None:
        d.note = "인코딩 판별 불가"
        return d
    raw_text, d.codec, d.label, d.bom = dec
    d.text, d.eol = normalize_eol(raw_text)
    try:
        d.roundtrip = encode_work(d, d.text) == data
    except UnicodeEncodeError:
        d.roundtrip = False
    d.line_starts = [0] + [m.end() for m in re.finditer("\n", d.text)]
    if analyze:
        d.code, d.strings = mask_code(d.text)
        d.struct = mask_preprocessor(d.code)
        d.funcs, d.inits, d.parse_ok = analyze_structure(_apply_brace_macros(d.struct))
    d.ok = True
    return d


def balance_of(struct):
    """괄호 종류별 (여는 수 - 닫는 수)와 #if/#endif 차이. 검증 L0 에서 원본과 비교한다."""
    return (struct.count("{") - struct.count("}"), struct.count("(") - struct.count(")"),
            struct.count("[") - struct.count("]"))


_PP_IF_RE = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|endif)\b", re.M)


def pp_balance_of(code):
    n = 0
    for m in _PP_IF_RE.finditer(code):
        n += -1 if m.group(1) == "endif" else 1
    return n


# ============================================================================
# [M05 규칙]  규칙 카탈로그와 탐지
# 목적: 버전 변경으로 문제가 되는 코드 패턴을 정의한다. sLLM 없이 처리하는 범위를
#       넓히려면 여기에 규칙을 추가한다.
# 단계: T0 설정 흡수(코드 무변경) / T1 자동 치환 / T2 치환 + 검토 표시 /
#       T3 탐지만(llm=True 면 sLLM, False 면 수동 검토)
# when: 규칙이 켜지는 조건(요구사항 B5, B10). 비우면 항상 켜진다. 판정은 rule_state().
#   since     이 문제가 생긴 VS 제품 연도. "원본 < since <= 대상"일 때만 켠다.
#             원본 버전에서 이미 겪은 문제는 다시 잡지 않고, 대상 버전에 아직 없는 문제도 잡지 않는다.
#   standing  True 면 원본 버전에서도 있던 것이라도 "since <= 대상"이면 켠다. 오류가 아니라 경고라서
#             고치지 않은 채 남아 있을 수 있는 것에 쓴다(예: C4996 사용 중단 경고).
#   min_std   대상 언어 표준이 이 값 이상일 때 / platform, conformance / deprecated(suppress, fix)
#   axis      {"db": "oracle"} 처럼 적는다. 환경값 축 db 의 값(원본 -> 대상)에 그 글자가 들어 있을 때 켠다
#   axis_changed  ["os"] 처럼 적는다. 그 축의 원본과 대상 값이 다를 때 켠다
# 그 밖의 속성: ext "cpp" 면 .c 파일에는 적용하지 않는다 / ref 근거(커버리지 표와 문서용)
#   새 규칙을 넣을 때는 "어느 VS 버전에서 바뀌었는가"를 since 로 적는다. 모르면 비워 두되 주석에 남긴다.
#   내장 규칙의 since 근거: 2015(리터럴 접합, snprintf 매크로, FILE 내부, hash_map)와 2012(make_pair)는
#   MS 의 "Visual C++ change history 2003-2015" 문서. 2010(stdint.h 추가), 2013(Winsock 사용 중단 경고,
#   Windows SDK 8.1), 2015(좁히기 변환 C4838/C2397)는 MS 경고 문서와 공개된 빌드 사례. 2005(CRT 경고)는 통설.
# 규칙은 다시 실행해도 같은 결과여야 한다(이미 고친 곳을 또 잡지 않는다).
# 수정: 규칙 추가는 RULES 에 항목을 더한다. 정규식은 code(주석/문자열 가린 본문)에 적용된다.
# ============================================================================
LOGR = Log("M05.규칙")

_NONSTDC = r"strdup|stricmp|strnicmp|strcmpi|memicmp|itoa|ltoa|ultoa|strlwr|strupr|getch|getche|kbhit|putch|wcsdup|wcsicmp|wcsnicmp"
_STD17_REMOVED = r"auto_ptr|bind1st|bind2nd|ptr_fun|mem_fun|mem_fun_ref|random_shuffle|unary_function|binary_function"

RULES = [
    {"id": "R-T0-CRT-SECURE", "tier": "T0", "category": "CRT", "collapse": True,
     "title": "보안 CRT 경고 대상 함수 사용(C4996)", "when": {"since": 2005, "standing": True},
     "pattern": r"(?<![\w.>:])(?:strcpy|strcat|sprintf|vsprintf|sscanf|scanf|fopen|strncpy|strncat|strtok|gets|"
                r"localtime|gmtime|ctime|asctime|getenv|wcscpy|wcscat|swscanf|_snprintf|_vsnprintf|_splitpath|"
                r"_makepath|mbstowcs|wcstombs)\s*\(",
     "setting": "전처리기 정의 추가: _CRT_SECURE_NO_WARNINGS"},
    {"id": "R-T0-CRT-NONSTDC", "tier": "T0", "category": "CRT", "collapse": True,
     "title": "POSIX 이름 함수 사용(C4996)", "when": {"since": 2005, "standing": True, "deprecated": "suppress"},
     "pattern": r"(?<![\w.>:])(?:%s)\s*\(" % _NONSTDC,
     "setting": "전처리기 정의 추가: _CRT_NONSTDC_NO_DEPRECATE"},
    {"id": "R-T1-CRT-NONSTDC", "tier": "T1", "category": "CRT",
     "title": "POSIX 이름 함수를 밑줄 이름으로 치환",
     "when": {"since": 2005, "standing": True, "deprecated": "fix"},
     "pattern": r"(?<![\w.>:])(%s)\s*\(" % _NONSTDC, "group": 1, "action": {"type": "prefix", "text": "_"}},
    {"id": "R-T0-WINSOCK-DEPR", "tier": "T0", "category": "Windows API", "collapse": True,
     "title": "사용 중단된 Winsock 함수 사용(C4996)", "when": {"since": 2013},
     "pattern": r"(?<![\w.>:])(?:inet_addr|inet_ntoa|gethostbyname|gethostbyaddr|WSAAsyncSelect)\s*\(",
     "setting": "전처리기 정의 추가: _WINSOCK_DEPRECATED_NO_WARNINGS"},
    {"id": "R-T0-STD17-REMOVED", "tier": "T0", "category": "C++ 표준", "collapse": True,
     "title": "C++17에서 제거된 표준 라이브러리 기능 사용",
     "when": {"min_std": "c++17", "deprecated": "suppress"},
     "pattern": r"\b(?:%s)\b" % _STD17_REMOVED,
     "setting": "전처리기 정의 추가: _HAS_AUTO_PTR_ETC=1 (또는 언어 표준을 C++14로 유지)"},
    {"id": "R-T3-STD17-REMOVED", "tier": "T3", "category": "C++ 표준", "llm": False,
     "title": "C++17에서 제거된 표준 라이브러리 기능 사용",
     "when": {"min_std": "c++17", "deprecated": "fix"},
     "pattern": r"\b(?:%s)\b" % _STD17_REMOVED,
     "guidance": "소유권 이전 등 타입에 따라 고치는 방법이 달라진다. 수동 검토 대상이다."},
    {"id": "R-T1-LITERAL-SPACE", "tier": "T1", "category": "언어 준수성",
     "title": "문자열 리터럴 뒤에 붙은 매크로 사이에 공백 삽입", "detector": "literal_ident",
     "when": {"since": 2015}},
    {"id": "R-T1-SNPRINTF-MACRO", "tier": "T1", "category": "CRT",
     "title": "snprintf/vsnprintf 매크로 정의를 구 컴파일러 전용으로 제한", "detector": "snprintf_macro",
     "when": {"since": 2015}},
    {"id": "R-T1-TR1-NAMESPACE", "tier": "T1", "category": "C++ 표준",
     "title": "std::tr1:: 를 std:: 로 치환", "when": {"min_std": "c++17"},
     "pattern": r"\bstd\s*::\s*tr1\s*::", "action": {"type": "replace", "text": "std::"}},
    {"id": "R-T2-MAKE-PAIR", "tier": "T2", "category": "C++ 표준", "review": True,
     "title": "make_pair 의 명시적 템플릿 인자 제거", "when": {"since": 2012},
     "pattern": r"\bmake_pair\s*(<[^;(){}]*?>)\s*\(", "group": 1, "action": {"type": "delete"}},
    {"id": "R-T3-NARROWING-CHAR", "tier": "T3", "category": "C++ 표준", "llm": False, "when": {"since": 2015},
     "title": "char 배열 초기값의 좁히기 변환(C2397/C4838)", "detector": "narrowing_char_init",
     "guidance": "요소 타입을 unsigned char 로 바꾸거나 값을 캐스팅해야 한다. 타입을 바꾸면 사용처에 "
                 "영향이 있으므로 수동 검토 대상이다."},
    {"id": "R-T3-STDINT-TYPEDEF", "tier": "T3", "category": "C++ 표준", "llm": True, "when": {"since": 2010},
     "title": "stdint 타입 이름의 자체 typedef(C2371 재정의)", "detector": "stdint_typedef",
     "guidance": "VS2010 이후 <stdint.h> 가 같은 이름을 정의하므로 재정의 오류가 날 수 있다. 해당 typedef 줄을 "
                 "바로 위의 '#if !defined(_MSC_VER) || _MSC_VER < 1600' 줄과 바로 아래의 '#endif' 줄로 감싼다. "
                 "typedef 줄 자체는 바꾸지 않는다. 연속한 typedef 줄은 한 번에 감싼다."},
    {"id": "R-T3-FILE-INTERNALS", "tier": "T3", "category": "CRT", "llm": False, "when": {"since": 2015},
     "title": "FILE 구조체 내부 필드 또는 _iob 직접 사용",
     "pattern": r"->\s*_(?:ptr|cnt|base|flag|file|bufsiz|charbuf)\b|\b_iob\b|\b__iob_func\b",
     "guidance": "VS2015 이후 FILE 내부는 공개되지 않는다. 표준 함수로 다시 작성해야 한다."},
    {"id": "R-T3-HASH-MAP", "tier": "T3", "category": "C++ 표준", "llm": False,
     "when": {"since": 2015, "deprecated": "fix"},
     "title": "hash_map/hash_set 사용",
     "pattern": r"\bhash_(?:map|set|multimap|multiset)\b",
     "guidance": "unordered_map/unordered_set 으로 바꾸고 include 도 함께 바꿔야 한다."},
    {"id": "R-T3-PTR-TRUNC", "tier": "T3", "category": "64비트", "llm": False, "when": {"platform": "x64"},
     "title": "포인터를 32비트 정수로 캐스팅",
     "pattern": r"\(\s*(?:int|long|DWORD|UINT|ULONG|LONG|unsigned\s+int|unsigned\s+long)\s*\)\s*"
                r"(?:this\b|\(\s*(?:void|LPVOID|HANDLE|HWND|char|BYTE)\s*\*?\s*\))",
     "guidance": "x64 에서는 포인터가 64비트다. INT_PTR/DWORD_PTR 등으로 바꿔야 한다."},
    {"id": "R-T3-WINDOWLONG", "tier": "T3", "category": "64비트", "llm": True, "when": {"platform": "x64"},
     "title": "SetWindowLong/GetWindowLong 계열 사용",
     "pattern": r"\b(?:Set|Get)(?:Window|Class)Long[AW]?\s*\(",
     "guidance": "포인터나 핸들을 다루는 호출이면 함수 이름 끝에 Ptr 을 붙이고(SetWindowLongPtr 등), 인덱스 상수 "
                 "GWL_WNDPROC, GWL_USERDATA, GWL_HINSTANCE, GWL_HWNDPARENT, GWL_ID, DWL_* 는 GWLP_*, DWLP_* 로 "
                 "바꾼다. GWL_STYLE, GWL_EXSTYLE 호출은 그대로 둔다."},
    {"id": "R-T3-INLINE-ASM", "tier": "T3", "category": "64비트", "llm": False, "when": {"platform": "x64"},
     "title": "인라인 어셈블리 사용", "pattern": r"\b_?_asm\b",
     "guidance": "x64 컴파일러는 인라인 어셈블리를 지원하지 않는다."},
    # ---- 0.4.0 에서 더한 규칙 ------------------------------------------------------------
    # 원칙: 빌드를 막지 않는 경고와 설정 한 줄로 풀리는 것은 T0(코드 무변경). 고치는 방법이 하나로 정해지는 것만
    #       T1/T2. 나머지는 탐지만 한다. ref 는 근거이며 커버리지 표(KNOWN_CHANGES)와 문서에 쓴다.
    {"id": "R-T2-WINABLE-H", "tier": "T2", "category": "Windows API", "review": True, "when": {"since": 2008},
     "title": "없어진 헤더 winable.h 를 winuser.h 로(C1083)",
     "pattern": r"(?i)^[ \t]*#[ \t]*include[ \t]*<winable\.h>", "action": {"type": "replace", "text": "#include <winuser.h>"},
     "ref": "MS 변경 이력 2008: Winable.h 제거"},
    {"id": "R-T3-OSVER-GLOBALS", "tier": "T3", "category": "CRT", "llm": False, "when": {"since": 2008},
     "title": "없어진 CRT 전역 변수(_osver, _winmajor 등) 사용",
     "pattern": r"(?<![\w.>])_(?:osver|winmajor|winminor|winver|osplatform)\b",
     "guidance": "VS2008 부터 이 전역 변수가 없다(C2065). OS 버전이 필요하면 VerifyVersionInfo 등으로 바꾼다.",
     "ref": "MS 변경 이력 2008: _osver 등 제거"},
    {"id": "R-T2-AUTO-STORAGE", "tier": "T2", "category": "C++ 표준", "review": True, "when": {"since": 2010},
     "title": "저장 클래스로 쓴 auto 제거(C3530/C4430)",
     "pattern": r"\bauto[ \t]+(?=(?:(?:const|unsigned|signed|static|volatile)[ \t]+)*(?:int|char|short|long|float|double|bool|"
                r"unsigned|signed|struct|BOOL|BYTE|WORD|DWORD|UINT|LONG|ULONG|TCHAR|CString)\b)",
     "action": {"type": "delete"}, "ref": "MS 변경 이력 2010: auto 키워드의 뜻이 바뀜"},
    {"id": "R-T0-ITER-DEBUG", "tier": "T0", "category": "C++ 표준", "collapse": True, "when": {"since": 2010},
     "title": "_SECURE_SCL / _HAS_ITERATOR_DEBUGGING 정의 사용",
     "pattern": r"\b(?:_SECURE_SCL|_HAS_ITERATOR_DEBUGGING)\b",
     "setting": "조치 없이 빌드된다(_ITERATOR_DEBUG_LEVEL 로 통합됨). 단, 함께 링크하는 모든 .lib 와 값이 같아야 한다. 다르면 LNK2038",
     "ref": "MS 변경 이력 2010, 2013: 반복자 디버그 수준과 불일치 검사"},
    {"id": "R-T2-MAKE-PAIR-NULL", "tier": "T2", "category": "C++ 표준", "review": True, "when": {"since": 2010},
     "title": "make_pair 인자의 NULL 을 nullptr 로(C2440/C2664)",
     "pattern": r"\bmake_pair\s*\((?:[^;()]|\([^()]*\))*?\b(NULL)\b", "group": 1,
     "action": {"type": "replace", "text": "nullptr"}, "ref": "MS 변경 이력 2010: CComPtr 의 정수 생성자 제거"},
    {"id": "R-T2-WINVER-OLD", "tier": "T2", "category": "Windows API", "review": True, "when": {"since": 2010},
     "title": "WINVER / _WIN32_WINNT 값이 0x0501 미만(MFC, ATL 에서 C1189)", "detector": "winver_old",
     "ref": "MS 문서 'Update WINVER and _WIN32_WINNT'와 MFC 의 C1189 사례. 바꿀 값은 환경값의 대상 OS 로 정한다"},
    {"id": "R-T0-KEYWORD-MACRO", "tier": "T0", "category": "C++ 표준", "collapse": True, "when": {"since": 2012},
     "title": "키워드를 매크로로 정의(C1189: forbids macroizing keywords)",
     "pattern": r"^[ \t]*#[ \t]*define[ \t]+(?:for|if|else|while|do|switch|case|default|break|continue|return|goto|private|"
                r"protected|public|inline|const|static|virtual|explicit|typename|template|class|struct|union|enum|namespace|"
                r"using|try|catch|throw|operator|sizeof|this|true|false|bool|char|short|int|long|float|double|signed|"
                r"unsigned|void|wchar_t|override|final|noexcept|nullptr|constexpr|decltype|static_assert|alignas|alignof|"
                r"thread_local|char16_t|char32_t)\b",
     "setting": "전처리기 정의 추가: _ALLOW_KEYWORD_MACROS (표준 라이브러리 헤더와 함께 쓰일 때의 C1189 를 끈다). 가능하면 매크로를 없앤다",
     "ref": "MS 변경 이력 2012: 키워드 매크로 진단(new 는 예외)"},
    {"id": "R-T3-GETVERSIONEX", "tier": "T3", "category": "Windows API", "llm": False, "when": {"since": 2013},
     "title": "GetVersion / GetVersionEx 사용(사용 중단. 돌려주는 버전이 실제와 다를 수 있음)",
     "pattern": r"(?<![\w.>:])(?:GetVersionEx[AW]?|GetVersion)\s*\(",
     "guidance": "빌드는 된다(경고 C4996). 다만 Windows 8.1 이후에는 매니페스트에 적힌 버전까지만 돌려주므로, OS 버전으로 "
                 "갈라지는 코드의 동작이 달라질 수 있다. <VersionHelpers.h> 의 IsWindows...OrGreater 로 바꾸는 것을 검토한다.",
     "ref": "Windows SDK 8.1(VS2013)의 사용 중단 경고 사례"},
    {"id": "R-T2-C99-MATH-MACRO", "tier": "T2", "category": "CRT", "review": True, "when": {"since": 2013},
     "title": "C99 함수와 같은 이름의 매크로 정의(round, isnan 등)를 구 컴파일러 전용으로 제한", "detector": "guard_macro",
     "names": "round|roundf|lround|llround|trunc|rint|nearbyint|log2|log1p|expm1|cbrt|exp2|fmin|fmax|fdim|copysign|"
              "nextafter|isnan|isinf|isfinite|isnormal|signbit|fpclassify|erf|erfc|tgamma|lgamma|asinh|acosh|atanh|"
              "strtoll|strtoull|va_copy", "msc_ver": 1800,
     "ref": "VS2013 의 C99 라이브러리 추가. 매크로가 헤더보다 먼저 정의되면 헤더의 선언이 깨진다"},
    {"id": "R-T3-C99-MATH-FUNC", "tier": "T3", "category": "CRT", "llm": False, "when": {"since": 2013},
     "title": "C99 함수와 같은 이름의 함수 정의(round, isnan 등)",
     "pattern": r"^[ \t]*(?:(?:static|inline|__inline|__forceinline)[ \t]+)*(?:double|float|long[ \t]+long|__int64|long|int)[ \t]+"
                r"(?:__cdecl[ \t]+)?(?:round|roundf|lround|llround|trunc|rint|log2|cbrt|fmin|fmax|isnan|isinf|strtoll|"
                r"strtoull)[ \t]*\([^;{}]*\)[ \t\r\n]*\{",
     "guidance": "VS2013 부터 <math.h>, <stdlib.h> 가 같은 이름의 함수를 제공한다(C2084, C2556 등). 정의 전체를 "
                 "'#if !defined(_MSC_VER) || _MSC_VER < 1800' 과 '#endif' 로 감싸거나 이름을 바꾼다.",
     "ref": "VS2013 의 C99 라이브러리 추가"},
    {"id": "R-T3-GETS-REMOVED", "tier": "T3", "category": "CRT", "llm": True, "when": {"since": 2015},
     "title": "없어진 함수 gets 사용(C3861)", "pattern": r"(?<![\w.>:])gets\s*\(",
     "guidance": "VS2015 부터 gets 가 없다. 인자가 이 조각 안에서 선언된 배열이면 gets_s(이름, _countof(이름)) 으로 바꾼다. "
                 "배열인지 알 수 없거나 포인터면 NO_CHANGE 를 쓴다.",
     "ref": "MS 변경 이력 2015: gets, _getws 제거"},
    {"id": "R-T3-TIMESPEC", "tier": "T3", "category": "CRT", "llm": True, "when": {"since": 2015},
     "title": "struct timespec 의 자체 정의(C2011 재정의)", "detector": "guarded_decl",
     "pattern": r"^[ \t]*(?:typedef[ \t]+)?struct[ \t]+timespec\b[^;{()]*\{",
     "guidance": "VS2015 부터 <time.h> 가 struct timespec 을 정의한다. 이 구조체 정의 전체(struct 줄부터 닫는 '};' 줄까지)를 "
                 "바로 위의 '#if !defined(_MSC_VER) || _MSC_VER < 1900' 줄과 바로 아래의 '#endif' 줄로 감싼다. "
                 "구조체 안의 내용은 바꾸지 않는다.",
     "ref": "VS2015 의 범용 CRT. pthreads 계열 헤더와의 충돌 사례"},
    {"id": "R-T3-CHAR16-TYPEDEF", "tier": "T3", "category": "C++ 표준", "llm": True, "when": {"since": 2015},
     "title": "char16_t / char32_t 의 자체 typedef(C2628)", "detector": "guarded_decl",
     "pattern": r"^[ \t]*typedef\b[^;{}()]*?\b(?:char16_t|char32_t)[ \t]*;",
     "guidance": "VS2015 부터 char16_t, char32_t 는 내장 형식이다. 해당 typedef 줄을 바로 위의 "
                 "'#if !defined(_MSC_VER) || _MSC_VER < 1900' 줄과 바로 아래의 '#endif' 줄로 감싼다. typedef 줄 자체는 바꾸지 않는다.",
     "ref": "MS 변경 이력 2015: char16_t, char32_t 가 내장 형식이 됨"},
    {"id": "R-T0-WCSTOK", "tier": "T0", "category": "CRT", "collapse": True, "when": {"since": 2015},
     "title": "wcstok 사용(인자 2개짜리는 표준과 달라 C4996 오류)", "pattern": r"(?<![\w.>:])wcstok\s*\(",
     "setting": "전처리기 정의 추가: _CRT_NON_CONFORMING_WCSTOK (인자 2개짜리 wcstok 을 쓰는 경우. 3개짜리면 필요 없다)",
     "ref": "MS 변경 이력 2015: wcstok 이 표준 형태(인자 3개)로 바뀜"},
    {"id": "R-T0-HASH-MAP", "tier": "T0", "category": "C++ 표준", "collapse": True,
     "title": "hash_map/hash_set 사용(C1189 사용 중단)", "when": {"since": 2015, "deprecated": "suppress"},
     "pattern": r"\bhash_(?:map|set|multimap|multiset)\b",
     "setting": "전처리기 정의 추가: _SILENCE_STDEXT_HASH_DEPRECATION_WARNINGS (VS2022 17.4 까지 통하는 것을 확인. "
                "이후 판에서 헤더가 없어지면 unordered_map/unordered_set 으로 바꿔야 한다)",
     "ref": "MS 변경 이력 2015: hash_map, hash_set 사용 중단"},
    {"id": "R-T0-EXP-FILESYSTEM", "tier": "T0", "category": "C++ 표준", "collapse": True, "when": {"since": 2019},
     "title": "<experimental/filesystem> 사용(C1189 사용 중단)",
     "pattern": r"^[ \t]*#[ \t]*include[ \t]*<experimental/filesystem>",
     "setting": "전처리기 정의 추가: _SILENCE_EXPERIMENTAL_FILESYSTEM_DEPRECATION_WARNING. 가능하면 <filesystem> 과 "
                "std::filesystem 으로 바꾼다(C++17 필요)",
     "ref": "VS2019 16.3: experimental/filesystem 사용 중단"},
    {"id": "R-T0-STD17-DEPRECATED", "tier": "T0", "category": "C++ 표준", "collapse": True, "ext": "cpp",
     "title": "C++17 에서 사용 중단된 표준 라이브러리 기능 사용(경고 C4996)", "when": {"min_std": "c++17"},
     "pattern": r"\bstd\s*::\s*(?:iterator\s*<|uncaught_exception\s*\(|result_of\b|not1\b|not2\b|unary_negate\b|binary_negate\b|"
                r"get_temporary_buffer\b|return_temporary_buffer\b|raw_storage_iterator\b|is_literal_type\b|"
                r"codecvt_utf8(?:_utf16)?\b|codecvt_utf16\b|wstring_convert\b|wbuffer_convert\b|allocator\s*<\s*void\s*>)|"
                r"^[ \t]*#[ \t]*include[ \t]*<codecvt>",
     "setting": "경고만 난다(오류 아님). 끄려면 전처리기 정의 추가: _SILENCE_ALL_CXX17_DEPRECATION_WARNINGS",
     "ref": "C++17 의 사용 중단 항목(std::iterator, codecvt 등)"},
    {"id": "R-T0-STD-BYTE", "tier": "T0", "category": "C++ 표준", "collapse": True, "ext": "cpp",
     "title": "using namespace std 사용(C++17 의 std::byte 와 Windows 헤더의 byte 가 충돌할 수 있음: C2872)",
     "when": {"min_std": "c++17"}, "pattern": r"\busing\s+namespace\s+std\s*;",
     "setting": "C2872 'byte': 모호한 기호 가 나면 전처리기 정의 추가: _HAS_STD_BYTE=0",
     "ref": "C++17 std::byte 와 rpcndr.h 의 byte 충돌 사례"},
    {"id": "R-T0-REGISTER", "tier": "T0", "category": "C++ 표준", "collapse": True, "ext": "cpp",
     "title": "register 저장 클래스 사용(C++17 에서 없어짐. 경고 C5033)", "when": {"min_std": "c++17"},
     "pattern": r"\bregister[ \t]+(?=[A-Za-z_])",
     "setting": "경고만 난다(오류 아님). 조치 불필요. 끄려면 컴파일러 옵션 /wd5033",
     "ref": "C++17: register 키워드 제거"},
    {"id": "R-T0-DYN-EXCEPT", "tier": "T0", "category": "C++ 표준", "collapse": True, "ext": "cpp",
     "title": "동적 예외 지정 throw(형식) 사용(C++17 에서 없어짐. 경고 C5040)", "when": {"min_std": "c++17"},
     "detector": "dyn_except_spec",
     "setting": "경고만 난다(오류 아님). 조치 불필요. 끄려면 컴파일러 옵션 /wd5040",
     "ref": "C++17: 동적 예외 지정 제거"},
    {"id": "R-T0-STRICT-STRINGS", "tier": "T0", "category": "언어 준수성", "collapse": True,
     "title": "문자열 리터럴을 const 가 아닌 char* 로 받음(준수 모드에서 C2440/C2664)", "when": {"conformance": "on"},
     "pattern": r"(?<!const\s)\b(?:char|wchar_t|TCHAR|CHAR|WCHAR)\s*\*\s*\w+\s*(?:\[\s*\d*\s*\])?\s*=\s*(?:\{\s*)?"
                r"(?:L|_T\s*\(\s*|TEXT\s*\(\s*)?\"|\b(?:LPSTR|LPTSTR|LPWSTR|PSTR|PTSTR|PWSTR|PCHAR)\s+\w+\s*=\s*"
                r"(?:L|_T\s*\(\s*|TEXT\s*\(\s*)?\"",
     "setting": "컴파일러 옵션 추가: /Zc:strictStrings- (준수 모드는 켠 채로 이 검사만 끈다). 함수 인자로 넘기는 경우도 같은 옵션으로 풀린다",
     "ref": "/permissive- 가 /Zc:strictStrings 를 켠다(MS 문서)"},
    # ---- DB 접근 기술과 HMI/UI 구성 요소 (0.6.0) -------------------------------------------------------
    # 버전 전환에서 "코드가 쓰는 제품"에 따라 달라지는 것들이다. 어떤 제품을 쓰는지는 코드의 헤더, 클래스, 연결 문자열로
    # 알아낸다(탐지 함수 tech: include 는 #include/#import 의 파일 이름, strings 는 문자열 리터럴, code 는 본문).
    # T0 는 코드가 아니라 설치·설정으로 맞출 것(project_settings.md 2절), T3 는 코드를 바꿔야 하는 것(수동 검토)이다.
    # 제품별 세부는 제조사 문서를 따른다. 여기 적은 것은 널리 알려진 일반 사항이다.
    {"id": "R-T0-DB-ESQL", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] 임베디드 SQL(EXEC SQL: Pro*C, tbESQL, APRE, ESQL/C, ECPG 등) 사용", "code": r"\bEXEC\s+SQL\b",
     "include": r"(?i)^(?:sqlca|sqlcpr|oraca|sqlda|sqlhdr)\.h$",
     "setting": "선행 컴파일러(proc, tbpc, apre, esql, ecpg 등)를 대상 DB 클라이언트 판의 것으로 다시 실행해 .c/.cpp 를 새로 만든다. "
                "선행 컴파일러가 새 Visual Studio 의 시스템 헤더를 읽지 못하면 헤더 분석 범위를 줄인다(Pro*C: PARSE=NONE 또는 PARTIAL, "
                "CODE=CPP, SYS_INCLUDE). 링크하는 클라이언트 라이브러리의 이름(orasql<판>.lib 등)과 비트(x86/x64)를 대상 판에 맞춘다"},
    {"id": "R-T0-DB-ESQL-GENERATED", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] 선행 컴파일러가 만든 파일(Pro*C 출력)", "code": r"\bsqlcxt\s*\(|\bsqlcud0\b",
     "setting": "이 파일은 .pc 에서 만들어진다. 이 도구가 여기서 고친 내용은 다시 선행 컴파일하면 사라진다. 원본(.pc)을 고치고 다시 만든다. "
                "이 파일은 --exclude 로 빼는 것이 좋다"},
    {"id": "R-T0-DB-OCI", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] Oracle OCI 사용", "include": r"(?i)^(?:oci|ociap|ocidfn|oratypes)\.h$", "code": r"\bOCI(?:EnvCreate|Initialize|Logon2?|ServerAttach)\s*\(",
     "setting": "Oracle 클라이언트와 oci.lib 을 대상 판과 같은 비트(x64 면 64비트 클라이언트)로 맞춘다. 10g 이하의 클라이언트는 "
                "12c 이후 서버에 기본 설정으로는 접속하지 못한다(ORA-28040)"},
    {"id": "R-T3-DB-OO4O", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] Oracle Objects for OLE(OO4O) 사용. 11.2 가 마지막 지원 판이다. OCI, OLE DB, ODBC 로 바꿔야 한다",
     "include": r"(?i)^oracl\.h$", "code": r"\b(?:ODatabase|ODynaset|OSession|OStartup)\b", "strings": r"OracleInProcServer",
     "ref": "Oracle 문서: Oracle Objects for OLE Developer's Guide 11.2 의 지원 종료 안내"},
    {"id": "R-T3-DB-MSDAORA", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] Microsoft 의 Oracle 공급자(MSDAORA, Microsoft ODBC for Oracle) 사용. 32비트 전용이고 더 이상 개발되지 않는다. "
              "Oracle 의 공급자(OraOLEDB.Oracle, Oracle ODBC 드라이버)로 바꾼다",
     "strings": r"(?i)MSDAORA|Microsoft ODBC for Oracle"},
    {"id": "R-T3-DB-SQLNCLI", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] SQL Server 의 구 공급자(SQLOLEDB, SQL Server Native Client) 사용. MSOLEDBSQL 이나 ODBC Driver for SQL Server 로 바꾼다. "
              "새 드라이버(OLE DB 19, ODBC 18)는 암호화가 기본이라 연결 문자열에 Encrypt, TrustServerCertificate 를 정해야 한다",
     "include": r"(?i)^sqlncli\.h$", "strings": r"(?i)SQLNCLI\d*|SQL Server Native Client|Provider\s*=\s*SQLOLEDB|Driver\s*=\s*\{SQL Server\}",
     "ref": "MS 문서: SQL Server Native Client 는 SQL Server 2022 와 SSMS 19 에서 빠졌다"},
    {"id": "R-T3-DB-DBLIB", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] DB-Library 사용(SQL Server 의 구 API). 지금의 SQL Server 에는 없다. ODBC 나 OLE DB 로 다시 써야 한다",
     "include": r"(?i)^(?:sqlfront|sqldb)\.h$", "code": r"\b(?:dbopen|dblogin|dbsqlexec|dbnextrow)\s*\("},
    {"id": "R-T3-DB-DAO", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] DAO 사용(MFC 의 CDao 클래스). 32비트 전용이고 x64 에서는 컴파일되지 않는다. ODBC(CDatabase)나 ADO/OLE DB 로 바꾸는 것을 검토한다",
     "include": r"(?i)^(?:afxdao|dbdao)\.h$", "code": r"\bCDao(?:Database|Recordset|Workspace|QueryDef|TableDef|Exception)\b"},
    {"id": "R-T0-DB-ADO-IMPORT", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] ADO 형식 라이브러리를 #import", "include": r"(?i)msado\d+\.(?:dll|tlb)$|msadox\.dll$",
     "setting": "빌드 PC 의 ADO 로 래퍼가 다시 만들어진다. Windows 7 SP1 이후의 PC 에서 빌드한 실행 파일은 그보다 앞선 OS 에서 ADO 인터페이스를 "
                "찾지 못할 수 있다(구 OS 에서도 돌아야 하면 msado28.tlb 나 msado60_Backcompat.tlb 를 가져온다). x64 빌드에서는 #import 경로가 "
                "64비트 쪽(Program Files\\Common Files\\System\\ado)이어야 한다"},
    {"id": "R-T3-DB-ODBC-LEN", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech", "when": {"platform": "x64"},
     "title": "[DB] ODBC 함수의 길이·표시자 인자(x64). SQLINTEGER 가 아니라 SQLLEN, SQLULEN 이어야 한다(64비트에서 8바이트)",
     "code": r"\bSQL(?:BindCol|BindParameter|GetData|RowCount|ColAttribute|DescribeCol|DescribeParam|ExtendedFetch|PutData|SetPos|"
             r"GetDescField|SetDescField|ParamOptions)\s*\("},
    {"id": "R-T0-DB-ODBC", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] ODBC 사용(API 또는 MFC 의 CDatabase)", "include": r"(?i)^(?:sql|sqlext|sqltypes|sqlucode|odbcinst|afxdb)\.h$",
     "code": r"\bSQL(?:AllocHandle|Connect|DriverConnect)\s*\(|\bCRecordset\b|\bCDatabase\b",
     "setting": "대상 PC 에 대상 DB 의 ODBC 드라이버를 프로그램과 같은 비트로 설치한다. DSN 도 같은 비트의 ODBC 관리자에서 만든다"
                "(32비트 프로그램은 32비트 DSN 만 본다). 드라이버 이름이 바뀌면 연결 문자열의 Driver= 값도 바꾼다"},
    {"id": "R-T0-DB-OLEDB", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] OLE DB 사용(ATL 소비자 템플릿 등)", "include": r"(?i)^(?:atldbcli|oledb|oledberr|msdasc)\.h$",
     "setting": "쓰는 OLE DB 공급자가 대상 PC 에 프로그램과 같은 비트로 있어야 한다. 구 공급자(Jet 4.0, MSDAORA, SQLOLEDB)는 64비트 판이 없거나 "
                "더 이상 개발되지 않는다. 연결 문자열의 Provider= 값을 확인한다"},
    {"id": "R-T0-DB-MYSQL", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] MySQL C API 사용", "include": r"(?i)(?:^|/)mysql\.h$",
     "setting": "libmysql(또는 MariaDB Connector/C)을 대상 판과 같은 비트로 맞춘다. MySQL 8.0 서버는 기본 인증 방식(caching_sha2_password)이 "
                "달라 구 클라이언트 라이브러리로는 접속하지 못할 수 있다. 문자셋 기본값(utf8mb4)도 확인한다"},
    {"id": "R-T3-DB-MYSQL-MYBOOL", "tier": "T3", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] my_bool 사용. MySQL 8.0 의 헤더에는 없다. 클라이언트를 8.0 이상으로 올리면 bool 로 바꾼다", "code": r"\bmy_bool\b"},
    {"id": "R-T0-DB-VENDOR-SDK", "tier": "T0", "category": "DB", "collapse": True, "detector": "tech",
     "title": "[DB] DB 제조사의 클라이언트 SDK 사용(CLI, CT-Library, libpq, CCI 등)",
     "include": r"(?i)^(?:sqlcli1?|ctpublic|sybdb|sybfront|libpq-fe|ecpglib|cas_cci|ibase|sqlite3)\.h$",
     "setting": "클라이언트 SDK 의 include 와 lib 를 대상 DB 판, 프로그램과 같은 비트, 같은 런타임 라이브러리(/MD, /MT)로 맞춘다. "
                "구 툴셋으로 만든 정적 .lib 는 새 툴셋에 링크되지 않을 수 있다(LNK2038)"},
    {"id": "R-T0-HMI-ACTIVEX", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] ActiveX 컨트롤(OCX) 사용", "include": r"(?i)\.ocx$",
     "code": r"\bAfxEnableControlContainer\s*\(|\bDECLARE_EVENTSINK_MAP\b|\bCreateControl\s*\(",
     "setting": "OCX 는 프로그램과 같은 비트여야 한다(x64 로 가면 64비트 OCX 가 있어야 한다). 대상 PC 에 등록(regsvr32)과 라이선스가 필요하다. "
                "64비트 Windows 에서 32비트 OCX 는 SysWOW64 쪽에 등록한다. #import 나 클래스 마법사로 만든 래퍼는 대상 OCX 판으로 다시 만든다"},
    {"id": "R-T3-HMI-VB6-OCX", "tier": "T3", "category": "HMI", "collapse": True, "detector": "tech", "when": {"platform": "x64"},
     "title": "[HMI] Visual Basic 6 시절의 ActiveX 컨트롤(MSComm, MSFlexGrid, MSChart, Winsock 등) 사용(x64). 32비트 전용이라 x64 에서는 쓸 수 없다. 다른 구현으로 바꾼다",
     "include": r"(?i)(?:mscomm32|msflxgrd|mshflxgd|mschrt20|mswinsck|mscomctl|mscomct2|comctl32|comdlg32|richtx32|tabctl32|msmask32)\.ocx$",
     "code": r"\bCMS(?:Comm|FlexGrid|HFlexGrid|Chart|Winsock)\b"},
    {"id": "R-T0-HMI-VB6-OCX", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] Visual Basic 6 시절의 ActiveX 컨트롤(MSComm, MSFlexGrid, MSChart, Winsock 등) 사용",
     "include": r"(?i)(?:mscomm32|msflxgrd|mshflxgd|mschrt20|mswinsck|mscomctl|mscomct2|comctl32|comdlg32|richtx32|tabctl32|msmask32)\.ocx$",
     "code": r"\bCMS(?:Comm|FlexGrid|HFlexGrid|Chart|Winsock)\b",
     "setting": "32비트 전용 컨트롤이다. 프로그램을 x86 로 유지해야 쓸 수 있고, 대상 PC 에 OCX 를 배포·등록해야 한다(새 Windows 에 기본으로 없다)"},
    {"id": "R-T0-HMI-OPC-DA", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] OPC DA(COM/DCOM) 사용", "include": r"(?i)^(?:opcda|opccomn|opcerror|opc_ae|opchda)(?:_i)?\.[hc]$",
     "code": r"\bIOPC(?:Server|ItemMgt|SyncIO|AsyncIO2?|GroupStateMgt|DataCallback|Common)\b|\bOPCITEMDEF\b",
     "setting": "다른 PC 의 OPC 서버에 붙는다면 Windows 의 DCOM 보안 강화(KB5004442. 2023년 3월부터 끌 수 없다)의 영향을 받는다. 클라이언트와 "
                "서버의 DCOM 인증 수준을 패킷 무결성(RPC_C_AUTHN_LEVEL_PKT_INTEGRITY) 이상으로 맞춘다(CoInitializeSecurity 인자, dcomcnfg). "
                "x64 로 가면 OPC Core Components 의 64비트 프록시가 필요하다",
     "ref": "MS KB5004442(CVE-2021-26414)"},
    {"id": "R-T3-HMI-NETDDE", "tier": "T3", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] NetDDE 사용. Windows Vista 부터 없다. 다른 통신 방식(소켓, OPC 등)으로 바꿔야 한다",
     "include": r"(?i)^nddeapi\.h$", "code": r"\bNDde(?:ShareAdd|ShareDel|SetTrustedShare|GetShareSecurity)\w*\s*\("},
    {"id": "R-T0-HMI-DDE", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] DDE 사용", "include": r"(?i)^ddeml\.h$", "code": r"\bDde(?:Initialize|Connect|ClientTransaction)[AW]?\s*\(",
     "setting": "DDE 는 남아 있지만 권한 수준이 다른 프로세스 사이에서는 통하지 않는다(한쪽만 관리자 권한이면 연결되지 않는다). "
                "상대 프로그램(HMI 패키지)의 대상 판이 DDE 를 계속 지원하는지 확인한다"},
    {"id": "R-T3-HMI-GLAUX", "tier": "T3", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] GLAUX 사용(glaux.h). 새 Windows SDK 에 없다(C1083). 비트맵 읽기 등은 다른 방법으로 바꾼다",
     "include": r"(?i)(?:^|/)glaux\.h$", "code": r"\baux(?:DIBImageLoad|InitWindow|MainLoop)[AW]?\s*\("},
    {"id": "R-T0-HMI-DIRECTX", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] 구 DirectX(D3DX, DirectDraw, DirectInput 등) 사용", "include": r"(?i)^(?:d3dx\d*\w*|ddraw|dinput|dsound|dxerr\d?|d3drm)\.h$",
     "setting": "D3DX(d3dx9.h 등)와 dxerr 는 Windows SDK 에 없다. 2010년 6월의 DirectX SDK 를 따로 설치해 include·lib 경로에 넣거나 다른 구현으로 "
                "바꾼다. DirectDraw 와 DirectInput 은 SDK 에 남아 있지만 더 이상 개발되지 않는다"},
    {"id": "R-T3-HMI-WINHELP", "tier": "T3", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] WinHelp(.hlp) 도움말 호출. Windows Vista 부터 WinHlp32 가 기본으로 없다. HTML Help(.chm, HtmlHelp)로 바꾼다",
     "code": r"(?<![\w.>])(?:::)?WinHelp[AW]?\s*\(", "strings": r"(?i)\.hlp\b"},
    {"id": "R-T0-HMI-QT", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] Qt 사용", "include": r"^Q(?:Application|Widget|Object|MainWindow|Dialog|String)$|^Qt(?:Gui|Core|Widgets)(?:/|$)", "code": r"\bQ_OBJECT\b",
     "setting": "Qt 는 컴파일러 판마다 바이너리가 다르다. 대상 Visual Studio 용 Qt 를 설치하고 moc, uic, rcc 를 다시 돌린다. Qt 의 주 버전을 올려야 "
                "하면(예: Qt 4 에서 5, 6) 소스 변경이 커서 이 도구의 범위 밖이다"},
    {"id": "R-T0-HMI-UILIB", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] 서드파티 MFC UI 라이브러리(BCGControlBar, Codejock, Stingray, Ultimate Toolbox, ChartDirector 등) 사용",
     "include": r"(?i)(?:^|/)(?:BCGCBProInc|BCGCBInc|XTToolkitPro|XTPro|secall|gxall|OXAllInc|chartdir)\.h$",
     "setting": "라이브러리를 대상 툴셋, 프로그램과 같은 비트, 같은 문자셋으로 다시 빌드하거나 대상 Visual Studio 를 지원하는 판을 받는다. "
                "구 판의 소스는 새 MFC 헤더에서 컴파일되지 않을 수 있다"},
    {"id": "R-T0-HMI-SCADA-SDK", "tier": "T0", "category": "HMI", "collapse": True, "detector": "tech",
     "title": "[HMI] HMI/SCADA 제품의 개발 도구(iFIX EDA, Citect CTAPI, InTouch, WinCC ODK, CIMPLICITY, RSLinx 등) 사용",
     "include": r"(?i)(?:^|/)(?:fixeda|fixtools|ctapi|ptacc|dmclient|ap_def|pdlrtapi|ptmap|dtl)\.h$",
     "setting": "제조사 SDK 는 HMI 제품의 판, 비트, 런타임 라이브러리가 맞아야 한다. 대상 HMI 제품 판의 SDK 로 include 와 lib 를 바꾸고, "
                "제조사가 대상 Visual Studio 와 x64 를 지원하는지 확인한다(32비트 전용인 경우가 많다)"},
]

_RX_CACHE = {}


def _rx(pattern):
    if pattern not in _RX_CACHE:
        _RX_CACHE[pattern] = re.compile(pattern, re.M)
    return _RX_CACHE[pattern]


def std_rank(std):
    return STD_ORDER.index(std) if std in STD_ORDER else -1


def rule_state(rule, env):
    """규칙이 이 환경값에서 켜지는지와 그 이유. 돌려주는 값: (켜짐 여부, 이유)
    조건은 모두 만족해야 켜진다. 버전 구간(since)은 VS 제품 연도로 비교한다."""
    w = rule.get("when") or {}
    if "since" in w:
        y = int(w["since"])
        if env["to_vs"] < y:
            return False, "대상 VS %d 에는 아직 없는 문제(VS %d 부터)" % (env["to_vs"], y)
        if env["from_vs"] >= y and not w.get("standing"):
            return False, "원본 VS %d 에서 이미 겪은 문제(VS %d 부터)" % (env["from_vs"], y)
    if "min_std" in w and std_rank(env["cpp_standard"]) < std_rank(w["min_std"]):
        return False, "언어 표준 %s 이상에서만 해당(지금 %s)" % (w["min_std"], env["cpp_standard"] or "선택 없음")
    if "min_std" in w and env.get("from_std") and std_rank(env["from_std"]) >= std_rank(w["min_std"]):
        return False, "원본도 이미 언어 표준 %s 이상으로 빌드하던 코드(원본 %s)" % (w["min_std"], env["from_std"])
    if "platform" in w and env["platform"] != w["platform"]:
        return False, "플랫폼 %s 에서만 해당(지금 %s)" % (w["platform"], env["platform"])
    if "conformance" in w and env["conformance"] != w["conformance"]:
        return False, "준수 모드 %s 에서만 해당(지금 %s)" % (w["conformance"], env["conformance"])
    if "deprecated" in w and env["policy"]["deprecated_warnings"] != w["deprecated"]:
        return False, "deprecated 처리 방침 %s 에서만 해당(지금 %s)" % (w["deprecated"], env["policy"]["deprecated_warnings"])
    axes = env.get("axes") or {}
    for name, want in (w.get("axis") or {}).items():
        a = axes.get(str(name).lower()) or {}
        have = ("%s -> %s" % (a.get("from", ""), a.get("to", ""))) if a else ""
        if str(want).lower() not in have.lower():
            return False, "환경값 %s 에 '%s' 이(가) 있을 때만 해당(지금 %s)" % (name, want, axis_text(a) or "미지정")
    for name in w.get("axis_changed") or []:
        a = axes.get(str(name).lower()) or {}
        if not a.get("from") or not a.get("to") or a["from"].lower() == a["to"].lower():
            return False, "환경값 %s 이(가) 바뀔 때만 해당(지금 %s)" % (name, axis_text(a) or "미지정")
    return True, ""


def rule_active(rule, env):
    return rule_state(rule, env)[0]


def rule_when_text(rule):
    """규칙의 발동 조건을 한 줄로 적는다(규칙 목록과 보고서용)."""
    w = rule.get("when") or {}
    parts = []
    if "since" in w:
        parts.append("VS%s부터%s" % (w["since"], "(원본이 그 뒤여도)" if w.get("standing") else ""))
    for key, label in (("min_std", "표준>="), ("platform", "플랫폼="), ("conformance", "준수모드="), ("deprecated", "deprecated=")):
        if key in w:
            parts.append(label + str(w[key]))
    for name, want in (w.get("axis") or {}).items():
        parts.append("%s~%s" % (name, want))
    for name in w.get("axis_changed") or []:
        parts.append("%s 변경" % name)
    return ", ".join(parts) or "항상"


def load_rules(extra_path):
    """내장 규칙 + (있으면) 외부 규칙 파일. 외부 파일은 RULES 와 같은 형식의 JSON 배열이다."""
    rules = list(RULES)
    cand = extra_path or os.path.join(app_dir(), "config", "rules.json")
    if os.path.isfile(cand):
        extra = load_json(cand, "규칙")
        ids = {r["id"] for r in rules}
        for r in extra:
            if not all(k in r for k in ("id", "tier", "title")) or not (r.get("pattern") or r.get("detector")):
                raise UserInputError("규칙 형식 오류(id, tier, title, pattern 필요): %r" % (r,))
            since = (r.get("when") or {}).get("since")
            if since is not None and since not in VERSION_MAP:
                raise UserInputError("규칙 %s 의 when.since 값이 올바르지 않습니다: %r. VS 제품 연도(%s)로 적습니다"
                                     % (r["id"], since, ", ".join(str(y) for y in sorted(VERSION_MAP))))
            if r["id"] in ids:
                rules = [x for x in rules if x["id"] != r["id"]]
            rules.append(r)
        LOGR.info("외부 규칙 %d건 읽음: %s" % (len(extra), cand))
    elif extra_path:
        raise UserInputError("규칙 파일이 없습니다: %s" % extra_path)
    return rules


# 규칙의 시험 예(요구사항 C9). 규칙마다 "잡아야 하는 코드"와 "잡지 말아야 하는 코드"를 둔다. vupdate rules --test 가 돌린다.
# 외부 규칙 파일(config/rules.json)에서는 규칙 안에 "tests": [...] 로 같은 모양을 적는다.
#   code  : 시험할 코드 한 토막        hits : 탐지돼야 하는 곳의 수(0 이면 잡지 말아야 한다)
#   after : (선택) 자동 치환한 뒤의 코드   file : (선택) 파일 이름. 기본 t.cpp
RULE_TESTS = {
    "R-T1-CRT-NONSTDC": [{"code": "char* p = strdup(s);", "hits": 1, "after": "char* p = _strdup(s);"},
                         {"code": "char* p = _strdup(s); q = obj.strdup(s);", "hits": 0}],
    "R-T1-TR1-NAMESPACE": [{"code": "std::tr1::shared_ptr<int> p;", "hits": 1, "after": "std::shared_ptr<int> p;"},
                           {"code": "// std::tr1::shared_ptr 는 쓰지 않는다\nint a;", "hits": 0}],
    "R-T2-MAKE-PAIR": [{"code": "x = std::make_pair<int, int>(a, b);", "hits": 1, "after": "x = std::make_pair(a, b);"},
                       {"code": "x = std::make_pair(a, b);", "hits": 0}],
    "R-T2-WINABLE-H": [{"code": "#include <winable.h>", "hits": 1, "after": "#include <winuser.h>"},
                       {"code": "#include <winuser.h>", "hits": 0}],
    "R-T2-AUTO-STORAGE": [{"code": "void f() { auto int i = 0; }", "hits": 1, "after": "void f() { int i = 0; }"},
                          {"code": "void f() { auto i = 0; }", "hits": 0}],
    "R-T2-MAKE-PAIR-NULL": [{"code": "m.insert(std::make_pair(k, NULL));", "hits": 1, "after": "m.insert(std::make_pair(k, nullptr));"},
                            {"code": "m.insert(std::make_pair(k, v)); p = NULL;", "hits": 0}],
    "R-T3-GETS-REMOVED": [{"code": "void f(char* b) { gets(b); }", "hits": 1}, {"code": "void f(char* b) { fgets(b, 10, stdin); }", "hits": 0}],
    "R-T3-STDINT-TYPEDEF": [{"code": "typedef unsigned char uint8_t;", "hits": 1}, {"code": "typedef unsigned char BYTE8;", "hits": 0}],
    "R-T3-WINDOWLONG": [{"code": "void f(HWND h) { SetWindowLong(h, GWL_USERDATA, 0); }", "hits": 1},
                        {"code": "void f(HWND h) { SetWindowLongPtr(h, GWLP_USERDATA, 0); }", "hits": 0}],
    "R-T1-LITERAL-SPACE": [{"code": 'const char* s = "abc"PFX;', "hits": 1, "after": 'const char* s = "abc" PFX;'},
                           {"code": 'const char* s = "abc" PFX;', "hits": 0}],
    "R-T3-DB-DAO": [{"code": "#include <afxdao.h>\nCDaoDatabase db;", "hits": 2}, {"code": "#include <afxdb.h>\nCDatabase db;", "hits": 0}],
    "R-T3-DB-SQLNCLI": [{"code": 'const char* cs = "Provider=SQLNCLI11;Server=x";', "hits": 1},
                        {"code": 'const char* cs = "Provider=MSOLEDBSQL;Server=x";', "hits": 0}],
    "R-T0-DB-ESQL": [{"code": "void f() { EXEC SQL COMMIT; }", "hits": 1}, {"code": "// EXEC SQL COMMIT;\nvoid f() {}", "hits": 0}],
    "R-T0-HMI-OPC-DA": [{"code": '#include "opcda.h"\nIOPCServer* p;', "hits": 2}, {"code": '#include "myopc.h"', "hits": 0}],
    "R-T3-HMI-GLAUX": [{"code": "#include <gl/glaux.h>", "hits": 1}, {"code": "#include <gl/glu.h>", "hits": 0}],
}


def run_rule_tests(rules):
    """규칙에 붙은 시험 예를 돌린다. 돌려주는 값: [(규칙 ID, 번호, 통과 여부, 설명)]
    규칙의 발동 조건(when)은 보지 않는다. 탐지와 치환만 본다."""
    env = {"from_vs": 2005, "to_vs": 2022, "cpp_standard": "c++17", "conformance": "on", "platform": "x64", "charset": "keep",
           "policy": {"deprecated_warnings": "fix"}, "axes": {}}
    out = []
    for r in rules:
        for no, t in enumerate(list(r.get("tests") or []) or RULE_TESTS.get(r["id"], []), 1):
            d = make_doc(str(t.get("file") or "t.cpp"), str(t.get("code", "")).encode("utf-8"))
            try:
                hits = rule_hits(r, d, env)
            except Exception as e:
                out.append((r["id"], no, False, "탐지 중 오류: %s" % e))
                continue
            want = int(t.get("hits", 1))
            ok, msg = len(hits) == want, "탐지 %d곳(기대 %d곳)" % (len(hits), want)
            if ok and "after" in t:
                text = d.text
                for h in sorted(hits, key=lambda h: h["start"], reverse=True):
                    if h.get("after") is not None:
                        text = text[:h["start"]] + h["after"] + text[h["end"]:]
                ok = text.strip() == str(t["after"]).strip()
                msg += ", 치환 결과 %s" % ("일치" if ok else "다름: %r" % text.strip()[:100])
            out.append((r["id"], no, ok, msg))
    return out


def _apply_action(act, before):
    t = act["type"]
    if t == "prefix":
        return act["text"] + before
    if t == "suffix":
        return before + act["text"]
    if t == "replace":
        return act["text"]
    if t == "delete":
        return ""
    raise ValueError("알 수 없는 action: %r" % (t,))


def _prev_line(doc, pos):
    """pos 가 있는 줄의 바로 윗줄(빈 줄은 건너뜀)의 내용."""
    line = doc.line_of(pos) - 1
    while line >= 1:
        s, e = doc.line_span(line)
        txt = doc.text[s:e].strip()
        if txt:
            return txt
        line -= 1
    return ""


def _is_msc_guard(line_text):
    return line_text.startswith("#") and "_MSC_VER" in line_text and "if" in line_text


def _det_literal_ident(rule, doc, env=None):
    hits = []
    n = len(doc.code)
    for s, e in doc.strings:
        if e < n and (doc.code[e].isalpha() or doc.code[e] == "_"):
            hits.append({"start": e - 1, "end": e, "after": '" ',
                         "detail": doc.text[max(s, e - 20):min(n, e + 20)].strip()})
    return hits


_SNPRINTF_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(v?snprintf)[ \t]+_\1[ \t]*(?=\r?$)", re.M)


def _det_snprintf_macro(rule, doc, env=None):
    hits = []
    for m in _SNPRINTF_RE.finditer(doc.code):
        if _is_msc_guard(_prev_line(doc, m.start())):
            continue                                   # 이미 감싼 곳
        before = doc.text[m.start():m.end()]
        hits.append({"start": m.start(), "end": m.end(), "detail": before.strip(),
                     "after": "#if defined(_MSC_VER) && _MSC_VER < 1900\n" + before + "\n#endif"})
    return hits


_STDINT_RE = re.compile(r"^[ \t]*typedef\b[^;{}()]*?\b(u?int(?:8|16|32|64)_t)[ \t]*;", re.M)


def _det_stdint_typedef(rule, doc, env=None):
    hits = []
    for m in _STDINT_RE.finditer(doc.code):
        line = doc.line_of(m.start())
        guarded = False
        while line > 1:                                # 연속한 typedef 줄 위쪽의 가드를 찾는다
            line -= 1
            s, e = doc.line_span(line)
            txt = doc.text[s:e].strip()
            if not txt or txt.startswith("typedef"):
                continue
            guarded = _is_msc_guard(txt)
            break
        if not guarded:
            hits.append({"start": m.start(), "end": m.end(), "detail": doc.text[m.start():m.end()].strip()})
    return hits


_CHAR_ARR_RE = re.compile(r"\b(?:signed\s+)?char\s+(\w+)\s*\[[^\]]*\]\s*=\s*\{")
_OVER_RE = re.compile(r"\b0[xX]0*([89a-fA-F][0-9a-fA-F])\b|(?<![\w.])(12[89]|1[3-9]\d|2[0-4]\d|25[0-5])(?![\w.])")


def _det_narrowing_char_init(rule, doc, env=None):
    hits = []
    ends = dict(doc.inits)
    for m in _CHAR_ARR_RE.finditer(doc.struct):
        if doc.struct[:m.start()].rstrip().endswith("unsigned"):
            continue
        brace = m.end() - 1
        end = ends.get(brace)
        if not end:
            continue
        over = _OVER_RE.findall(doc.struct[brace:end])
        if over:
            first = _OVER_RE.search(doc.struct[brace:end]).group(0)
            hits.append({"start": m.start(), "end": brace,
                         "detail": "배열 %s: char 범위를 넘는 값 %d개(예: %s)" % (m.group(1), len(over), first)})
    return hits


def _det_guarded_decl(rule, doc, env=None):
    """rule['pattern'] 에 맞는 선언 중 아직 '_MSC_VER' 가드로 감싸지 않은 것. 한 번 감싼 곳은 다시 잡지 않는다."""
    hits = []
    for m in _rx(rule["pattern"]).finditer(doc.code):
        line, guarded = doc.line_of(m.start()), False
        while line > 1:                                # 연속한 typedef 줄 위쪽의 가드를 찾는다
            line -= 1
            s, e = doc.line_span(line)
            txt = doc.text[s:e].strip()
            if not txt or txt.startswith("typedef"):
                continue
            guarded = _is_msc_guard(txt)
            break
        if not guarded:
            hits.append({"start": m.start(), "end": m.end(), "detail": doc.text[m.start():m.end()].strip()[:120]})
    return hits


def _det_guard_macro(rule, doc, env=None):
    """이름이 rule['names'] 인 매크로 정의(역슬래시로 이어지는 줄 포함)를 '_MSC_VER < rule[msc_ver]' 가드로 감싼다."""
    rx = _rx(r"^[ \t]*#[ \t]*define[ \t]+(?:%s)\b(?:[^\n]*\\\n)*[^\n]*" % rule["names"])
    hits = []
    for m in rx.finditer(doc.code):
        if _is_msc_guard(_prev_line(doc, m.start())):
            continue                                   # 이미 감싼 곳
        before = doc.text[m.start():m.end()]
        hits.append({"start": m.start(), "end": m.end(), "detail": before.strip().split("\n")[0][:120],
                     "after": "#if !defined(_MSC_VER) || _MSC_VER < %d\n%s\n#endif" % (int(rule["msc_ver"]), before)})
    return hits


_WINVER_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(WINVER|_WIN32_WINNT)[ \t]+(0[xX][0-9A-Fa-f]{3,4})\b", re.M)


def winver_for_os(os_text):
    """대상 OS 이름에서 WINVER 값을 정한다. 알아보지 못하면 None."""
    t = " ".join(str(os_text or "").lower().split())
    for pat, val in ((r"(?:windows|win)\s*1[01]\b|server\s*20(?:16|19|22|25)", "0x0A00"), (r"8\.1|2012\s*r2", "0x0603"),
                     (r"(?:windows|win)\s*8\b|server\s*2012", "0x0602"), (r"(?:windows|win)\s*7\b|2008\s*r2", "0x0601"),
                     (r"vista|server\s*2008", "0x0600")):
        if re.search(pat, t):
            return val
    return None


def _det_winver_old(rule, doc, env=None):
    """WINVER, _WIN32_WINNT 정의 중 0x0501 미만인 것. 바꿀 값은 환경값의 대상 OS 에서 정하고,
    대상 OS 를 모르면 지금의 Visual Studio 가 지원하는 하한인 Windows 7(0x0601)로 한다."""
    os_to = ((((env or {}).get("axes") or {}).get("os") or {}).get("to") or "").strip()
    want = winver_for_os(os_to)
    new = want or "0x0601"
    why = ("대상 OS %s" % os_to) if want else "대상 OS 를 몰라 Windows 7 값으로 함"
    hits = []
    for m in _WINVER_RE.finditer(doc.code):
        if int(m.group(2), 16) >= 0x0501:
            continue
        hits.append({"start": m.start(2), "end": m.end(2), "after": new,
                     "detail": "%s %s -> %s (%s)" % (m.group(1), m.group(2), new, why)})
    return hits


_DYN_EXCEPT_RE = re.compile(r"\)\s*(?:const\s*)?throw\s*\(\s*[A-Za-z_][\w:\s,<>\*&]*\)\s*(?:\{|;|=\s*0\s*;)")
_NOT_FUNC_WORDS = {"if", "while", "for", "switch", "catch", "return", "sizeof"}


def _det_dyn_except_spec(rule, doc, env=None):
    """함수 선언 뒤의 throw(형식). 'if (조건) throw (값);' 같은 throw 식은 제외한다."""
    hits = []
    code = doc.code
    for m in _DYN_EXCEPT_RE.finditer(code):
        depth, i = 0, m.start()
        while i >= 0:                                  # throw 앞의 닫는 괄호와 짝이 되는 여는 괄호를 찾는다
            c = code[i]
            if c == ")":
                depth += 1
            elif c == "(":
                depth -= 1
                if depth == 0:
                    break
            i -= 1
        word = re.search(r"([A-Za-z_]\w*)\s*$", code[max(0, i - 40):max(0, i)])
        if i < 0 or (word and word.group(1) in _NOT_FUNC_WORDS):
            continue
        hits.append({"start": m.start(), "end": m.end(), "detail": doc.text[m.start():m.end()].strip()[:120]})
    return hits


_INCLUDE_LINE_RE = re.compile(r"^[ \t]*(#)[ \t]*(?:include|import)[ \t]*[<\"]([^>\"\n]+)[>\"]", re.M)


def _det_tech(rule, doc, env=None):
    """코드가 쓰는 제품(DB 접근 기술, HMI 구성 요소)을 알아내는 탐지 함수.
    규칙의 include 는 #include/#import 의 파일 이름에, strings 는 문자열 리터럴 안에, code 는 본문(주석·문자열 제외)에 댄다.
    따옴표로 쓴 #include "x.h" 는 문자열로 마스킹돼 본문 패턴으로는 잡히지 않으므로 include 로 따로 본다."""
    hits = []
    if rule.get("include"):
        rx = _rx(rule["include"])
        for m in _INCLUDE_LINE_RE.finditer(doc.text):
            if doc.code[m.start(1)] != "#":         # 주석 안의 줄
                continue
            name = m.group(2).strip().replace("\\", "/")
            if rx.search(name) or rx.search(name.rsplit("/", 1)[-1]):
                hits.append({"start": m.start(1), "end": m.end(), "detail": doc.text[m.start(1):m.end()][:120]})
    if rule.get("strings"):
        rx = _rx(rule["strings"])
        for s, e in doc.strings:
            m = rx.search(doc.text[s:e])
            if m:
                hits.append({"start": s + m.start(), "end": s + m.end(), "detail": doc.text[s:e][:120]})
    if rule.get("code"):
        for m in _rx(rule["code"]).finditer(doc.code):
            hits.append({"start": m.start(), "end": m.end(), "detail": doc.text[m.start():m.end()].strip()[:120]})
    hits.sort(key=lambda h: h["start"])
    return hits


# ----------------------------------------------------------------------------
# 환경값 축에 적은 제품에 따른 확인 목록 (0.6.0)
# 프로필이나 --axis 로 준 OS, DB, HMI 도구의 이름에 아래 낱말이 있으면 그 항목을 project_settings.md 5절에 낸다.
# 널리 알려진 일반 사항만 적었다. 제품 판별 세부는 제조사 문서를 따른다.
# 수정: 쓰는 제품이 정해지면 그 제품의 줄을 더한다. (축, 낱말 정규식, 항목 목록)
# ----------------------------------------------------------------------------
PRODUCT_NOTES = [
    ("os", r"windows\s*(?:1[01]|8)|server\s*20(?:1[2-9]|2\d)", [
        "프로그램 폴더(Program Files) 아래에 설정·로그 파일을 쓰는 코드는 권한 오류가 나거나 다른 위치(VirtualStore)에 쓰인다. 자료 위치를 ProgramData 나 사용자 폴더로 옮긴다",
        "서비스는 화면을 띄울 수 없다(세션 0 격리). 서비스가 창이나 메시지 상자를 띄우던 코드는 별도 프로세스로 나눈다",
        "64비트 Windows 에서 32비트 프로그램의 시스템 폴더는 SysWOW64, 레지스트리는 WOW6432Node 다. 경로를 글자로 박아 둔 코드와 OCX·DLL 등록 위치를 확인한다",
        "WinHelp(.hlp)와 16비트 구성 요소는 쓸 수 없다. 고해상도(DPI) 화면에서 고정 크기 대화 상자가 흐리게 보이면 DPI 인식 설정을 정한다",
        "WINVER, _WIN32_WINNT 는 대상 OS 값으로 맞춘다(Windows 10·11 은 0x0A00). 규칙 R-T2-WINVER-OLD 가 0x0501 미만의 값을 바꾼다"]),
    ("db", r"oracle|ora\b|pro\*?c", [
        "클라이언트: 대상 서버 판을 지원하는 Oracle 클라이언트(Instant Client 포함)를 프로그램과 같은 비트로 설치한다",
        "10g 이하의 클라이언트로 12c 이후 서버에 접속하면 기본 설정에서 ORA-28040 이 난다. 클라이언트를 올리는 것이 원칙이다(서버의 허용 판을 낮추는 것은 임시 방편이다)",
        "Pro*C: proc 을 대상 클라이언트의 것으로 다시 실행한다. 링크하는 라이브러리 이름(orasql<판>.lib)이 판마다 다르다. 새 Visual Studio 의 헤더를 못 읽으면 PARSE, SYS_INCLUDE 옵션을 조정한다",
        "문자셋: DB 문자셋이 KO16MSWIN949 에서 AL32UTF8 로 바뀌면 한글 한 글자가 2바이트에서 3바이트가 된다. 컬럼 길이(BYTE 기준인지 CHAR 기준인지), 호스트 변수 버퍼 크기, NLS_LANG 을 확인한다",
        "Oracle Objects for OLE(OO4O)와 Microsoft 의 Oracle 공급자(MSDAORA)는 새 판에서 쓸 수 없다(규칙 R-T3-DB-OO4O, R-T3-DB-MSDAORA)"]),
    ("db", r"sql\s*server|mssql|ms-sql", [
        "공급자: SQLOLEDB, SQL Server Native Client(SQLNCLI)는 SQL Server 2022 부터 함께 제공되지 않는다. Microsoft OLE DB Driver(MSOLEDBSQL)나 ODBC Driver for SQL Server 로 바꾼다(규칙 R-T3-DB-SQLNCLI)",
        "새 드라이버(OLE DB Driver 19, ODBC Driver 18)는 암호화가 기본으로 켜져 있다. 서버 인증서를 신뢰할 수 없으면 접속이 실패한다. 연결 문자열에 Encrypt 와 TrustServerCertificate 를 정한다",
        "구 OS 와 구 드라이버 조합은 TLS 1.2 만 받는 서버에 접속하지 못한다. 대상 PC 의 드라이버 판을 확인한다",
        "DB-Library 와 SQL Server 용 임베디드 SQL 은 없어졌다(규칙 R-T3-DB-DBLIB)"]),
    ("db", r"mysql|mariadb", [
        "MySQL 8.0 서버의 기본 인증 방식(caching_sha2_password)은 구 클라이언트 라이브러리가 지원하지 않는다. libmysql 을 올리거나 서버 계정의 인증 방식을 정한다",
        "클라이언트 헤더를 8.0 으로 올리면 my_bool 이 없다(규칙 R-T3-DB-MYSQL-MYBOOL). 기본 문자셋이 utf8mb4 로 바뀐 것도 확인한다"]),
    ("db", r"tibero|티베로", [
        "tbESQL/C: 선행 컴파일러(tbpc)를 대상 Tibero 클라이언트의 것으로 다시 실행한다. Oracle 과 문법이 비슷하지만 클라이언트 라이브러리와 선행 컴파일러는 Tibero 의 것을 쓴다",
        "tbCLI/ODBC 드라이버를 프로그램과 같은 비트로 설치한다. 문자셋(MSWIN949, UTF8) 변경 시 버퍼 크기를 확인한다"]),
    ("db", r"altibase|알티베이스", [
        "임베디드 SQL(APRE. 구 이름 SESC)의 선행 컴파일러를 대상 판의 것으로 다시 실행하고, 클라이언트 라이브러리를 프로그램과 같은 비트로 맞춘다"]),
    ("db", r"postgres|edb\b|ppas", [
        "libpq 와 ECPG(선행 컴파일러 ecpg)를 대상 판과 같은 비트로 맞춘다. Oracle 에서 옮기는 경우 임베디드 SQL 문법 차이는 이 도구의 범위 밖이다"]),
    ("db", r"db2|informix|sybase|ase\b|cubrid", [
        "제조사 클라이언트 SDK 와 선행 컴파일러를 대상 판, 프로그램과 같은 비트로 맞추고 선행 컴파일을 다시 한다(규칙 R-T0-DB-ESQL, R-T0-DB-VENDOR-SDK)"]),
    ("hmi", r".", [
        "HMI 제품의 대상 판이 지원하는 OS, Visual Studio 판, 비트(x86/x64)를 제조사 문서에서 확인한다. 제조사 SDK 의 include·lib 를 대상 판의 것으로 바꾼다",
        "연동 방식별 확인: ActiveX(OCX)는 프로그램과 같은 비트와 등록, DDE 는 같은 권한 수준, OPC DA 는 DCOM 보안 설정, 제조사 API 는 SDK 판과 런타임 라이브러리",
        "HMI 화면이나 스크립트가 이 패키지의 DLL 을 부른다면 내보내는 함수 이름과 호출 규약(__stdcall, .def 파일), 구조체 패킹을 그대로 유지한다. 컴파일러를 바꿔도 이름이 달라지지 않는지 dumpbin /exports 로 확인한다"]),
    ("hmi", r"ifix|fix32|intouch|wonderware|aveva|wincc|cimplicity|citect|factorytalk|rsview|cimon|autobase|scada", [
        "HMI/SCADA 패키지의 개발 도구(EDA, CTAPI, ODK, 툴킷 등)는 32비트 전용인 경우가 많다. 그러면 이 패키지도 x86 로 유지해야 한다",
        "패키지 판을 올리면 태그 접근 API 의 함수 이름이나 초기화 순서가 달라질 수 있다. 제조사의 판별 변경 문서를 본다(이 도구에는 제품별 규칙이 없다)"]),
    ("hmi", r"\bqt\b|qt\s*\d", [
        "Qt 는 컴파일러 판마다 바이너리가 다르다. 대상 Visual Studio 용 Qt 를 쓰고 moc, uic, rcc 를 다시 돌린다(규칙 R-T0-HMI-QT)"]),
    ("hmi", r"mfc|bcg|codejock|xtreme|stingray", [
        "MFC 와 서드파티 MFC 라이브러리는 대상 툴셋, 같은 비트, 같은 문자셋으로 맞춘다. MFC 를 멀티바이트로 쓰면 MBCS 라이브러리 구성 요소가 필요하다"]),
]


def axis_notes(env):
    """환경값 축의 값에 맞는 확인 목록. 돌려주는 값: [(축 표시 이름, 값, [항목])]. 원본과 대상 값 어느 쪽에 낱말이 있어도 낸다."""
    out = []
    for name, a in (env.get("axes") or {}).items():
        text = ("%s %s" % (a.get("from", ""), a.get("to", ""))).lower()
        items = []
        for axis, rx, notes in PRODUCT_NOTES:
            if axis == name and re.search(rx, text):
                items += [x for x in notes if x not in items]
        if items:
            out.append((AXIS_LABELS.get(name, name), axis_text(a), items))
    return out


_DETECTORS = {"tech": _det_tech, "literal_ident": _det_literal_ident, "snprintf_macro": _det_snprintf_macro,
              "stdint_typedef": _det_stdint_typedef, "narrowing_char_init": _det_narrowing_char_init,
              "guarded_decl": _det_guarded_decl, "guard_macro": _det_guard_macro, "winver_old": _det_winver_old,
              "dyn_except_spec": _det_dyn_except_spec}


def rule_hits(rule, doc, env=None):
    """규칙 하나를 파일 하나에 적용한 결과. 각 항목: start, end, detail, (자동 치환이면) after
    env 는 환경값(탐지 함수가 대상 OS 같은 값을 쓸 때 필요). 규칙에 "ext": "cpp" 가 있으면 .c 파일에는 적용하지 않는다."""
    if rule.get("ext") == "cpp" and doc.rel.lower().endswith(".c"):
        return []
    det = rule.get("detector")
    if det:
        return _DETECTORS[det](rule, doc, env)
    grp, act, hits = rule.get("group", 0), rule.get("action"), []
    for m in _rx(rule["pattern"]).finditer(doc.code):
        s, e = m.span(grp)
        h = {"start": s, "end": e, "detail": doc.text[m.start():m.end()].strip()[:120]}
        if act:
            h["after"] = _apply_action(act, doc.text[s:e])
        hits.append(h)
    return hits


# ----------------------------------------------------------------------------
# 커버리지 표: 알려진 변경 항목과 이 도구의 처리 방식 (요구사항 C15, G8)
# 범위: MS 의 "Visual C++ change history 2003 - 2015" 문서에서 레거시 코드 전환에 걸리는 항목과,
#       2017 이후의 주요 항목을 추린 것이다. 문서의 모든 항목을 옮긴 것이 아니다
#       (MFC 클래스별 시그니처 변경 수십 건은 한 줄로 묶었다).
# 처리 방식: 규칙(코드를 고치거나 탐지) / 설정(옵션·매크로로 흡수) / 프로젝트 점검(.vcproj 설정 확인) /
#           오류표(빌드 오류가 났을 때 buildlog 가 대응을 안내) / 환경값 / 없음(다루지 않음)
# 수정: 규칙을 더하면 여기의 해당 줄을 '규칙'으로 바꾸고 규칙 ID 를 적는다.
# ----------------------------------------------------------------------------
KNOWN_CHANGES = [
    # (기준, 분류, 변경 항목, 처리 방식, 규칙 ID 또는 오류 코드, 비고)
    ("2005", "CRT", "보안 CRT 함수 사용 중단 경고(C4996)", "설정", "R-T0-CRT-SECURE", ""),
    ("2005", "CRT", "POSIX 이름 함수 사용 중단(strdup 등)", "규칙", "R-T0-CRT-NONSTDC, R-T1-CRT-NONSTDC", "방침에 따라 설정 또는 치환"),
    ("2005", "CRT", "swprintf 에 크기 인자 필요", "오류표", "C4996, C2664", "인자 수를 세어야 해서 규칙으로 잡지 않는다"),
    ("2005", "CRT", "time_t 가 64비트", "없음", "", "빌드는 된다. 구조체 크기와 파일 형식이 달라질 수 있어 동작 확인 대상"),
    ("2005", "CRT", "pow(int, int) 등 오버로드 제거", "오류표", "C2668", "타입 정보가 필요하다"),
    ("2005", "CRT", "단일 스레드 CRT(/ML) 제거", "프로젝트 점검", "LNK1104", ""),
    ("2005", "언어", "for 문 변수의 범위(/Zc:forScope 기본)", "프로젝트 점검", "C2065", "변수 사용처는 컴파일해야 알 수 있다"),
    ("2005", "언어", "wchar_t 가 내장 형식(/Zc:wchar_t 기본)", "프로젝트 점검", "LNK2019", ""),
    ("2005", "언어", "형식 생략 시 int 로 보지 않음", "오류표", "C4430", ""),
    ("2008", "SDK", "WINVER, _WIN32_WINNT 하한 상승", "규칙", "R-T2-WINVER-OLD", "0x0501 미만을 대상 OS 값으로"),
    ("2008", "SDK", "winable.h 제거", "규칙", "R-T2-WINABLE-H", ""),
    ("2008", "CRT", "_osver, _winmajor 등 전역 변수 제거", "규칙", "R-T3-OSVER-GLOBALS", "탐지만"),
    ("2010", "언어", "auto 키워드의 뜻이 바뀜", "규칙", "R-T2-AUTO-STORAGE", ""),
    ("2010", "언어", "static_assert, nullptr 가 키워드가 됨(이름 충돌)", "설정", "R-T0-KEYWORD-MACRO", "매크로로 정의한 경우만. 변수·함수 이름 충돌은 잡지 않는다"),
    ("2010", "표준 라이브러리", "<iterator> 등이 자동으로 포함되지 않음", "오류표", "C2039, C3861", "전처리 없이는 판단할 수 없다"),
    ("2010", "표준 라이브러리", "stdint.h 추가(자체 typedef 와 충돌)", "규칙", "R-T3-STDINT-TYPEDEF", "sLLM"),
    ("2010", "표준 라이브러리", "CComPtr 의 정수 생성자 제거(NULL 대입)", "규칙", "R-T2-MAKE-PAIR-NULL", "make_pair 인자인 경우만"),
    ("2010", "표준 라이브러리", "반복자 디버그 매크로가 _ITERATOR_DEBUG_LEVEL 로 통합", "설정", "R-T0-ITER-DEBUG", "불일치는 LNK2038"),
    ("2010", "표준 라이브러리", "checked_, unchecked_ 함수 제거", "오류표", "C2039", "드물다"),
    ("2010", "프로젝트", "프로젝트 형식이 .vcxproj 로 바뀜", "없음", "", "변환은 Visual Studio 가 한다(build_guide.md)"),
    ("2012", "표준 라이브러리", "make_pair 의 명시적 템플릿 인자", "규칙", "R-T2-MAKE-PAIR", ""),
    ("2012", "표준 라이브러리", "키워드를 매크로로 정의하면 오류", "설정", "R-T0-KEYWORD-MACRO", ""),
    ("2012", "링크", "다른 버전·다른 런타임으로 만든 .lib 의 혼용 검사", "오류표", "LNK2038", "매니페스트의 .lib 목록(project_settings.md 3절)"),
    ("2012", "링크", "x86 에서 /SAFESEH 기본", "오류표", "LNK2026", ""),
    ("2012", "언어", "초기화되지 않은 포인터 검사 강화", "오류표", "C4703", ""),
    ("2012", "MFC", "MFC 클래스의 함수 시그니처 변경 다수", "오류표", "C2511, C2555, C2664", "재정의한 함수에서만 난다. 컴파일해야 알 수 있다"),
    ("2013", "SDK", "Winsock 구 함수 사용 중단(C4996)", "설정", "R-T0-WINSOCK-DEPR", ""),
    ("2013", "SDK", "GetVersionEx 사용 중단", "규칙", "R-T3-GETVERSIONEX", "탐지만. 동작 확인 대상"),
    ("2013", "CRT", "C99 수학 함수 추가(자체 round 등과 충돌)", "규칙", "R-T2-C99-MATH-MACRO, R-T3-C99-MATH-FUNC", "매크로는 가드, 함수는 탐지만"),
    ("2013", "표준 라이브러리", "std::min, std::max 에 <algorithm> 필요", "오류표", "C2039, C3861", ""),
    ("2013", "언어", "집합 초기화의 좁히기 변환이 오류", "규칙", "R-T3-NARROWING-CHAR", "char 배열의 상수 초기값만 탐지"),
    ("2013", "MFC", "MFC 의 멀티바이트(MBCS) 라이브러리 분리", "프로젝트 점검", "MSB8031, LNK1104", ""),
    ("2013", "MFC", "메시지 처리 함수의 시그니처 검사 강화", "오류표", "C2440", "컴파일해야 알 수 있다"),
    ("2015", "CRT", "snprintf, vsnprintf 를 매크로로 정의하면 오류", "규칙", "R-T1-SNPRINTF-MACRO", ""),
    ("2015", "언어", "문자열 리터럴 뒤에 붙은 매크로(사용자 정의 리터럴로 해석)", "규칙", "R-T1-LITERAL-SPACE", ""),
    ("2015", "CRT", "FILE 구조체가 불투명해짐", "규칙", "R-T3-FILE-INTERNALS", "탐지만"),
    ("2015", "링크", "구 .lib 가 참조하는 stdio 기호(_iob, _sprintf 등)", "오류표", "LNK2019", "printf 계열은 legacy_stdio_definitions.lib, _iob 는 .lib 를 다시 빌드"),
    ("2015", "표준 라이브러리", "hash_map, hash_set 사용 중단", "규칙", "R-T0-HASH-MAP, R-T3-HASH-MAP", "방침에 따라 설정 또는 탐지"),
    ("2015", "CRT", "gets 제거", "규칙", "R-T3-GETS-REMOVED", "sLLM"),
    ("2015", "CRT", "struct timespec 정의 추가", "규칙", "R-T3-TIMESPEC", "sLLM"),
    ("2015", "언어", "char16_t, char32_t 가 내장 형식", "규칙", "R-T3-CHAR16-TYPEDEF", "sLLM"),
    ("2015", "CRT", "wcstok 이 인자 3개 형태로", "설정", "R-T0-WCSTOK", ""),
    ("2015", "언어", "좁히기 변환 진단(C2397, C4838)", "규칙", "R-T3-NARROWING-CHAR", "char 배열의 상수 초기값만 탐지"),
    ("2015", "CRT", "<math.h> 의 C++ 오버로드가 <cmath> 로 이동", "오류표", "C4244, C2668", "타입 정보가 필요하다"),
    ("2015", "CRT", "printf 형식 문자열 검사 경고", "오류표", "C4477", ""),
    ("2015", "언어", "/Zc:forScope- 사용 중단", "프로젝트 점검", "D9035", ""),
    ("2015", "배포", "CRT 가 범용 CRT 로 분리", "없음", "", "실행 환경의 재배포 패키지 문제. 소스와 무관"),
    ("2017", "언어", "준수 모드(/permissive-): 문자열 리터럴의 const", "설정", "R-T0-STRICT-STRINGS", "준수 모드를 켤 때만"),
    ("2017", "언어", "준수 모드: 2단계 이름 조회 등 그 밖의 검사", "오류표", "C3861, C2065, C7510", "컴파일해야 알 수 있다"),
    ("2017", "표준 라이브러리", "C++17: auto_ptr, bind1st 등 제거", "규칙", "R-T0-STD17-REMOVED, R-T3-STD17-REMOVED", "방침에 따라 설정 또는 탐지"),
    ("2017", "표준 라이브러리", "C++17: std::tr1 이름공간", "규칙", "R-T1-TR1-NAMESPACE", ""),
    ("2017", "표준 라이브러리", "C++17: std::iterator, codecvt 등 사용 중단", "설정", "R-T0-STD17-DEPRECATED", "경고"),
    ("2017", "표준 라이브러리", "C++17: std::byte 와 Windows 헤더의 byte 충돌", "설정", "R-T0-STD-BYTE", ""),
    ("2017", "언어", "C++17: register, 동적 예외 지정 제거", "설정", "R-T0-REGISTER, R-T0-DYN-EXCEPT", "경고"),
    ("2017", "설치", "MFC, ATL 이 선택 설치 구성 요소", "프로젝트 점검", "MSB8041, C1083", ""),
    ("2019", "표준 라이브러리", "<experimental/filesystem> 사용 중단", "설정", "R-T0-EXP-FILESYSTEM", ""),
    ("2019", "언어", "/std:c++20 이 준수 모드를 켬(16.11 부터)", "환경값", "", "c++20 이상이면 준수 모드를 켠 것으로 처리"),
    ("2019", "언어", "C++20: 집합체 규칙, char8_t, 비교 연산자 재작성 등", "없음", "", "레거시 코드에서 드물다. 컴파일로 확인"),
    ("2019", "옵션", "/Gm 등 옵션 사용 중단", "오류표", "D9035", "경고"),
    ("2022", "환경", "64비트 호스트 도구 기본, 구 플랫폼 지원 종료", "없음", "", "소스와 무관"),
    ("x64", "64비트", "포인터를 32비트 정수로 캐스팅", "규칙", "R-T3-PTR-TRUNC", "탐지만"),
    ("x64", "64비트", "SetWindowLong 계열과 GWL_ 상수", "규칙", "R-T3-WINDOWLONG", "sLLM"),
    ("x64", "64비트", "인라인 어셈블리", "규칙", "R-T3-INLINE-ASM", "탐지만"),
    ("x64", "64비트", "size_t 를 int 로 받는 변환 경고", "오류표", "C4267, C4244", "경고"),
    ("x64", "64비트", "printf 형식의 포인터·size_t 폭", "없음", "", "타입 정보가 필요하다"),
    ("문자셋", "문자셋", "멀티바이트에서 유니코드로 전환", "없음", "", "범위가 커서 규칙이 없다. 문자셋은 유지를 권한다"),
    ("인코딩", "인코딩", "CP949 소스를 다른 코드 페이지로 해석", "오류표", "C4819, C2001", "빌드 PC 의 시스템 로캘 또는 /source-charset"),
    ("제품", "DB", "임베디드 SQL 과 선행 컴파일러", "설정", "R-T0-DB-ESQL, R-T0-DB-ESQL-GENERATED", "선행 컴파일을 다시 한다"),
    ("제품", "DB", "Oracle OCI, DB 제조사 클라이언트 SDK, MySQL C API", "설정", "R-T0-DB-OCI, R-T0-DB-VENDOR-SDK, R-T0-DB-MYSQL", "판과 비트를 맞춘다"),
    ("제품", "DB", "ODBC, OLE DB, ADO 형식 라이브러리", "설정", "R-T0-DB-ODBC, R-T0-DB-OLEDB, R-T0-DB-ADO-IMPORT", "드라이버와 공급자의 비트"),
    ("제품", "DB", "없어졌거나 32비트 전용인 접근 기술(OO4O, MSDAORA, SQLNCLI, DB-Library, DAO)", "규칙",
     "R-T3-DB-OO4O, R-T3-DB-MSDAORA, R-T3-DB-SQLNCLI, R-T3-DB-DBLIB, R-T3-DB-DAO", "탐지만. 다른 기술로 바꾸는 것은 사람이 한다"),
    ("제품", "DB", "ODBC 길이 인자의 64비트 타입, MySQL 8 의 my_bool", "규칙", "R-T3-DB-ODBC-LEN, R-T3-DB-MYSQL-MYBOOL", "탐지만"),
    ("제품", "HMI", "ActiveX 컨트롤, OPC DA, DDE, 구 DirectX, Qt, 서드파티 UI 라이브러리, HMI/SCADA 개발 도구", "설정",
     "R-T0-HMI-ACTIVEX, R-T0-HMI-VB6-OCX, R-T0-HMI-OPC-DA, R-T0-HMI-DDE, R-T0-HMI-DIRECTX, R-T0-HMI-QT, R-T0-HMI-UILIB, R-T0-HMI-SCADA-SDK",
     "설치, 등록, 비트, 보안 설정"),
    ("제품", "HMI", "없어진 구성 요소(NetDDE, GLAUX, WinHelp)와 x64 에서 쓸 수 없는 VB6 컨트롤", "규칙",
     "R-T3-HMI-NETDDE, R-T3-HMI-GLAUX, R-T3-HMI-WINHELP, R-T3-HMI-VB6-OCX", "탐지만"),
]


def coverage_rows(rules):
    """커버리지 표를 줄 목록으로 만든다. 규칙 ID 가 실제 규칙에 없으면 표가 낡은 것이므로 오류로 알린다."""
    ids = {r["id"] for r in rules}
    rows = []
    for since, area, item, how, by, note in KNOWN_CHANGES:
        if how in ("규칙", "설정"):
            for rid in [x.strip() for x in by.split(",") if x.strip()]:
                if rid not in ids:
                    raise ValueError("커버리지 표의 규칙 ID 가 규칙 목록에 없습니다: %s" % rid)
        rows.append((since, area, item, how, by or "-", note or ""))
    return rows


def coverage_summary():
    """처리 방식별 건수."""
    n = OrderedDict((k, 0) for k in ("규칙", "설정", "프로젝트 점검", "환경값", "오류표", "없음"))
    for row in KNOWN_CHANGES:
        n[row[3]] = n.get(row[3], 0) + 1
    return n


# ============================================================================
# [M06 sLLM연결]  토큰 추정과 보정, HTTP 호출, 오류 분류
# 목적: sLLM 서버와의 통신을 한 곳에 모은다. 주소, 한도, 동시 수는 서버 설정에서 읽는다.
# 전제: OpenAI 호환 /chat/completions 규격. 다르면 llm_chat() 과 classify_llm_error() 만 고치면 된다.
# 토큰 한도를 다루는 방식(06_충족점검.md 6절)
#   1. 보내기 전: 글자 수로 추정한다(est_tokens). py 는 토크나이저를 직접 부르지 않는다.
#   2. 응답을 받으면: 서버가 알려 준 실제 입력 토큰 수로 추정식을 보정한다(learn_tokens).
#        실제 = 비율 x 추정 + 고정분.  비율은 토크나이저 차이, 고정분은 서버가 덧붙이는 프롬프트다.
#   3. 서버가 한도 초과로 거절하면: 같은 요청을 다시 보내지 않는다. 서버가 알려 준 한도와 토큰 수를
#        반영해 S6 가 더 작게 다시 계획한다(_replan).
#   4. 서버가 프롬프트를 덧붙이는 구성이면 설정의 server_prompt_reserve_tokens 에 그 상한을 적어 둔다.
#        그 안에서 프롬프트가 늘어나는 동안은 py 쪽에서 바꿀 것이 없다.
# ============================================================================
LOGL = Log("M06.sLLM연결")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def est_tokens(text):
    """토큰 수 추정(보정 전). 영문/기호는 2.8자당 1토큰, 한글 등은 1자당 1.2토큰으로 넉넉하게 잡는다."""
    n_ascii = len(text.encode("ascii", "ignore"))
    return int(n_ascii / 2.8 + (len(text) - n_ascii) * 1.2) + 8


def count_tokens(llm_cfg, text):
    """프롬프트 크기 판단에 쓰는 토큰 수. 추정치에 실행 중 배운 보정 비율(_ratio)을 곱한다.
    py 는 토크나이저를 직접 부르지 않는다(05_기능정의 4.2). 실제 값은 서버 응답으로만 배운다(learn_tokens)."""
    return int(est_tokens(text) * float(llm_cfg.get("_ratio", 1.0)))


def overhead_tokens(llm_cfg):
    """요청마다 py 가 보낸 본문 말고 더 드는 입력 토큰(서버가 덧붙이는 공통 프롬프트, 대화 틀).
    설정의 예약량(server_prompt_reserve_tokens)과 실행 중 배운 값(_overhead) 중 큰 쪽이다."""
    return max(int(llm_cfg.get("server_prompt_reserve_tokens", 0) or 0), int(llm_cfg.get("_overhead", 0) or 0))


def fits_limit(llm_cfg, need):
    """입력이 need 토큰(count_tokens 값)인 요청이 한도 안에 드는가. 여유 10%, 출력 상한, 서버 몫을 뺀다."""
    return int(need * 1.1) + int(llm_cfg["max_output_tokens"]) + overhead_tokens(llm_cfg) <= int(llm_cfg["max_total_tokens"])


def _refit_tokens(llm_cfg):
    """모아 둔 표본으로 (비율, 고정분)을 다시 구한다. 모형: 실제 입력 토큰 = 비율 x 추정 + 고정분.
    표본이 4개 이상이고 크기가 2배 넘게 벌어져 있으면 둘을 함께 구하고(최소제곱), 아니면 고정분을
    설정의 예약량으로 두고 비율만 구한다. 어느 쪽이든 모든 표본을 덮도록 올려 잡는다
    (작게 잡아 한도를 넘는 것보다 크게 잡아 조각이 조금 작아지는 쪽이 안전하다)."""
    hist = llm_cfg.get("_tok_hist") or []
    base = float(int(llm_cfg.get("server_prompt_reserve_tokens", 0) or 0))
    a, b = 1.0, base
    if hist:
        xs = [x for x, _y in hist]
        n, fit = len(hist), None
        if n >= 4 and max(xs) >= 2 * min(xs):
            mx, my = sum(xs) / n, sum(y for _x, y in hist) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            a1 = min(3.0, max(0.5, sum((x - mx) * (y - my) for x, y in hist) / sxx))
            b1 = max(y - a1 * x for x, y in hist)
            if b1 >= 0:
                fit = (a1, b1)
        if fit is None:
            fit = (min(3.0, max(0.5, max((y - base) / x for x, y in hist))), base)
        a, b = fit
    for x, y in llm_cfg.get("_tok_floor") or []:   # 수치 없이 거절된 요청: "적어도 이만큼은 된다"는 하한
        if a * x + b < y:
            a = min(3.0, (y - b) / x)
    return a, max(b, base)


def learn_tokens(llm_cfg, raw_est, actual, floor=False):
    """서버가 알려 준 실제 입력 토큰 수(actual)로 추정식을 보정한다. raw_est 는 보정 전 추정치(est_tokens).
    floor=True 는 실제 값을 모르고 "적어도 actual 이상"이라는 것만 아는 경우다(수치 없는 한도 초과 거절).
    주 스레드에서만 부른다."""
    if not actual or raw_est < 50:
        return
    key = "_tok_floor" if floor else "_tok_hist"
    hist = llm_cfg.setdefault(key, [])
    hist.append((int(raw_est), int(actual)))
    del hist[:-40]
    old_a, old_b = float(llm_cfg.get("_ratio", 1.0)), int(llm_cfg.get("_overhead", 0) or 0)
    a, b = _refit_tokens(llm_cfg)
    llm_cfg["_ratio"], llm_cfg["_overhead"] = round(a, 4), int(b)
    if abs(a - old_a) >= 0.03 or abs(b - old_b) >= 100:
        LOGL.info("토큰 추정 보정: 비율 %.2f -> %.2f, 고정분 %d -> %d토큰 (표본 %d개%s)"
                  % (old_a, a, old_b, b, len(llm_cfg.get("_tok_hist") or []),
                     ", 수치 없는 거절 %d건 반영" % len(llm_cfg["_tok_floor"]) if llm_cfg.get("_tok_floor") else ""))


_LIMIT_MSG_RE = re.compile(r"context length|context size|context window|maximum model length|too many tokens|"
                      r"token limit|over_limit|reduce the length", re.I)
_LIMIT_NUM_RES = [re.compile(p, re.I) for p in (
    r"maximum context length is (\d+)", r"maximum (?:model|context) length of (\d+)", r'"limit"\s*:\s*(\d+)')]
_ASKED_IN_RES = [re.compile(p, re.I) for p in (
    r"\((\d+) in the messages", r"request has (\d+) input tokens", r"prompt \(length (\d+)\)",
    r'"input_tokens"\s*:\s*(\d+)')]


def classify_llm_error(status, body):
    """오류 응답을 분류한다. 돌려주는 값: (종류, 서버 한도 또는 None, 실제 입력 토큰 또는 None)
    종류: over_limit 토큰 한도 초과(줄여서 다시 계획) / busy 과부하(기다렸다 다시 보냄) /
          bad_request 요청 자체가 거절됨(다시 보내도 같으므로 수동 검토) / error 그 밖(다시 보냄)
    문구는 서버마다 다르다. 알아보지 못한 400 응답은 bad_request 가 되어 같은 요청을 반복하지 않는다.
    호출이 MCP 서버 경유로 바뀌면 오류 코드를 그대로 받도록 이 함수만 고치면 된다(05_기능정의 4.5)."""
    body = body or ""
    if status in (429, 503, 529):
        return "busy", None, None
    if status in (400, 413, 422):
        if _LIMIT_MSG_RE.search(body):
            lim = next((int(m.group(1)) for m in (r.search(body) for r in _LIMIT_NUM_RES) if m), None)
            a_in = next((int(m.group(1)) for m in (r.search(body) for r in _ASKED_IN_RES) if m), None)
            return "over_limit", lim, a_in
        return "bad_request", None, None
    if status in (401, 403):
        return "bad_request", None, None
    return "error", None, None


def _llm_headers(llm_cfg):
    headers = {"Content-Type": "application/json"}
    if llm_cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + llm_cfg["api_key"]
    if llm_cfg.get("_caller"):                    # 호출자 표시(서버 기록용). 한글이 있을 수 있어 % 부호화한다
        headers["X-Vupdate-Caller"] = urllib.parse.quote(llm_cfg["_caller"], safe="")
    if llm_cfg.get("_run_id"):
        headers["X-Vupdate-Run"] = urllib.parse.quote(llm_cfg["_run_id"], safe="")
    return headers


# ---- MCP 경유 호출(llm.transport = "mcp") --------------------------------------------------------
# 서버가 모델 호출을 MCP 도구로만 열 때 쓴다(08_MCP역할목록.md 8.2). 서버가 OpenAI 호환 주소를 열면 필요 없다.
# 전송: Streamable HTTP. POST 한 번에 JSON-RPC 한 건을 보내고, 응답은 JSON 한 건이거나 SSE(text/event-stream)다.
# 도구의 약속(기본 이름 llm_complete)
#   입력: system, user, max_tokens, temperature, caller, run_id, job_id
#   출력: structuredContent(또는 JSON 문자열)에 content, finish_reason, prompt_tokens, completion_tokens.
#         JSON 이 아닌 글이면 그 글 전체를 응답 본문으로 본다(토큰 수는 모름)
#   오류: isError=true 와 함께 {"code": "over_limit"|"busy"|"denied", "limit": N, "input_tokens": M, "retry_after": 초}
# 고칠 곳: 서버의 도구 이름이 다르면 설정(llm.mcp_tool), 입력·출력 이름이 다르면 _llm_chat_mcp 의 args 와 payload 읽는 줄.
_MCP = {"sid": None, "ver": None, "ready": False, "seq": 0}
_MCP_LOCK = threading.Lock()                      # 요청 번호
_MCP_INIT_LOCK = threading.Lock()                 # 세션 열기(한 번에 한 스레드만)


def _mcp_rpc(llm_cfg, method, params=None, notify=False, timeout=None):
    """MCP 서버에 JSON-RPC 한 건을 보낸다. 돌려주는 값: (result, error). 알림(notify)이면 (None, None).
    HTTP 오류는 urllib.error.HTTPError 그대로 올린다(부르는 쪽이 과부하, 세션 만료를 가린다)."""
    with _MCP_LOCK:
        _MCP["seq"] += 1
        rid = _MCP["seq"]
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if not notify:
        msg["id"] = rid
    headers = _llm_headers(llm_cfg)
    headers["Accept"] = "application/json, text/event-stream"
    if _MCP["sid"]:
        headers["Mcp-Session-Id"] = _MCP["sid"]
    if _MCP["ver"] and method != "initialize":
        headers["MCP-Protocol-Version"] = _MCP["ver"]
    req = urllib.request.Request(llm_cfg["endpoint"], data=json.dumps(msg).encode("utf-8"), headers=headers)
    with _NO_PROXY_OPENER.open(req, timeout=timeout or llm_cfg["timeout_sec"]) as r:
        sid = r.headers.get("Mcp-Session-Id")
        ctype = (r.headers.get("Content-Type") or "").lower()
        raw = r.read().decode("utf-8", "replace")
    if sid:
        _MCP["sid"] = sid
    if notify or not raw.strip():
        return None, None
    doc = None
    if "text/event-stream" in ctype:
        for ev in re.split(r"\r?\n\r?\n", raw):
            data = "\n".join(l[5:].lstrip() for l in ev.splitlines() if l.startswith("data:"))
            try:
                obj = json.loads(data) if data else None
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("id") == rid and ("result" in obj or "error" in obj):
                doc = obj
    else:
        doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError("MCP 응답에서 결과를 찾지 못했습니다: %s" % raw[:200])
    return doc.get("result"), doc.get("error")


def _mcp_ready(llm_cfg):
    """세션을 연다(initialize 뒤 notifications/initialized). 이미 열려 있으면 아무것도 하지 않는다."""
    with _MCP_INIT_LOCK:
        if _MCP["ready"]:
            return
        want = llm_cfg.get("mcp_protocol") or "2025-06-18"
        res, err = _mcp_rpc(llm_cfg, "initialize", {"protocolVersion": want, "capabilities": {},
                                                    "clientInfo": {"name": APP, "version": VERSION}}, timeout=15)
        if err:
            raise RuntimeError("MCP initialize 실패: %s" % (err,))
        _MCP["ver"] = str((res or {}).get("protocolVersion") or want)
        try:
            _mcp_rpc(llm_cfg, "notifications/initialized", notify=True, timeout=15)
        except Exception as e:                    # 알림을 받지 않는 서버도 있다. 도구 호출에는 지장이 없다
            LOGL.debug("MCP initialized 알림을 서버가 받지 않았습니다: %s" % e)
        _MCP["ready"] = True
        LOGL.debug("MCP 세션을 열었습니다(프로토콜 %s, 세션 %s)" % (_MCP["ver"], _MCP["sid"] or "없음"))


def mcp_tool(llm_cfg, name, arguments, timeout=None):
    """MCP 도구 하나를 부른다. 돌려주는 값: (성공 여부, 값 dict, 글).
    값은 structuredContent, 없으면 글을 JSON 으로 읽은 것, 그것도 아니면 빈 dict 다.
    서버가 세션을 끝냈으면(HTTP 404) 세션을 한 번 다시 연다."""
    res = err = None
    for attempt in (0, 1):
        _mcp_ready(llm_cfg)
        try:
            res, err = _mcp_rpc(llm_cfg, "tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404 and attempt == 0 and _MCP["sid"]:
                _MCP.update(sid=None, ready=False)
                continue
            raise
    if err:
        return False, {"code": "rpc_error", "rpc_code": err.get("code") if isinstance(err, dict) else None}, str(
            err.get("message") if isinstance(err, dict) else err)
    res = res or {}
    text = "\n".join(str(c.get("text", "")) for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text")
    payload = res.get("structuredContent") if isinstance(res.get("structuredContent"), dict) else None
    if payload is None:
        try:
            obj = json.loads(text)
            payload = obj if isinstance(obj, dict) else None
        except ValueError:
            payload = None
    return (not res.get("isError")), (payload or {}), text


def _llm_chat_mcp(llm_cfg, system, user):
    """llm_chat 의 MCP 판. 돌려주는 값의 모양과 오류 종류는 llm_chat 과 같다."""
    t0 = time.time()

    def ms():
        return int((time.time() - t0) * 1000)

    args = {"system": system, "user": user, "max_tokens": llm_cfg["max_output_tokens"], "temperature": 0,
            "caller": llm_cfg.get("_caller", ""), "run_id": llm_cfg.get("_run_id", ""), "job_id": llm_cfg.get("_job_id", "")}
    busy_max = max(0, int(llm_cfg.get("busy_retries", 5) or 0))
    for wait_no in range(busy_max + 1):
        try:
            ok, payload, text = mcp_tool(llm_cfg, llm_cfg.get("mcp_tool") or "llm_complete", args)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:800]
            except Exception:
                pass
            kind, lim, a_in = classify_llm_error(e.code, detail)
            if kind == "busy" and wait_no < busy_max:
                time.sleep(min(30.0, float(2 ** wait_no)))
                continue
            return False, "HTTP %s %s %s" % (e.code, e.reason, detail[:300]), ms(), {"kind": kind, "limit": lim, "asked_in": a_in}
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e), ms(), {"kind": "error"}
        if ok:
            raw = str(payload["content"] if "content" in payload else text)
            return True, _THINK_RE.sub("", raw).strip(), ms(), {
                "in": payload.get("prompt_tokens", payload.get("input_tokens")),
                "out": payload.get("completion_tokens", payload.get("output_tokens")),
                "finish": payload.get("finish_reason") or "", "kind": "", "think": bool(_THINK_RE.search(raw))}
        code = str(payload.get("code") or "").lower()
        if code == "busy" and wait_no < busy_max:
            try:
                wait = min(30.0, max(0.2, float(payload.get("retry_after"))))
            except (TypeError, ValueError):
                wait = min(30.0, float(2 ** wait_no))
            LOGL.debug("서버 과부하(MCP). %.1f초 뒤 다시 보냅니다(%d/%d)" % (wait, wait_no + 1, busy_max))
            time.sleep(wait)
            continue
        lim = payload.get("limit") if isinstance(payload.get("limit"), int) else None
        a_in = payload.get("input_tokens") if isinstance(payload.get("input_tokens"), int) else None
        if code == "over_limit":
            kind = "over_limit"
        elif code in ("busy",):
            kind = "busy"
        elif code in ("denied", "bad_request", "invalid", "unauthorized") or (
                code == "rpc_error" and payload.get("rpc_code") in (-32601, -32602)):
            kind = "bad_request"
        elif _LIMIT_MSG_RE.search(text):
            kind, lim, a_in = classify_llm_error(400, text)
        else:
            kind = "error"
        return False, ("MCP %s: %s" % (code or "error", text[:300])), ms(), {"kind": kind, "limit": lim, "asked_in": a_in}
    return False, "과부하 응답이 계속됨", ms(), {"kind": "busy"}


def job_report(llm_cfg, tool, payload):
    """작업 보고(05_기능정의.md 4.1 의 job_open, job_progress, job_close). 설정에서 켠 경우에만 보낸다.
    실패해도 실행에는 영향을 주지 않는다. 돌려주는 값: 서버가 준 값(dict) 또는 None."""
    if (llm_cfg.get("transport") or "openai") != "mcp" or not llm_cfg.get("mcp_job_tools") or not llm_cfg.get("endpoint"):
        return None
    try:
        ok, res, _text = mcp_tool(llm_cfg, tool, payload, timeout=10)
        return res if ok else None
    except Exception as e:
        LOGL.debug("작업 보고(%s)를 보내지 못했습니다: %s" % (tool, e))
        return None


def _shared_cache(llm_cfg):
    """실행 사이에 함께 쓰는 응답 캐시(요구사항 H6). 설정 llm.shared_cache_file 이 있을 때만 쓴다.
    키는 모델 이름 + 프롬프트 전체이므로, 같은 조각을 같은 지침으로 다시 물을 때만 재사용된다."""
    path = str(llm_cfg.get("shared_cache_file") or "")
    if not path:
        return None
    path = path if os.path.isabs(path) else os.path.join(app_dir(), path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.execute("CREATE TABLE IF NOT EXISTS llm_cache(key TEXT PRIMARY KEY, response TEXT, ts TEXT)")
    return con


def shared_cache_get(llm_cfg, key):
    try:
        con = _shared_cache(llm_cfg)
        if con is None:
            return None
        try:
            row = con.execute("SELECT response FROM llm_cache WHERE key=?", (key,)).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception as e:
        LOGL.debug("공용 응답 캐시를 읽지 못했습니다: %s" % e)
        return None


def shared_cache_put(llm_cfg, key, response):
    try:
        con = _shared_cache(llm_cfg)
        if con is None:
            return
        try:
            con.execute("INSERT OR REPLACE INTO llm_cache(key,response,ts) VALUES(?,?,?)", (key, response, time.strftime("%Y-%m-%d %H:%M:%S")))
            con.commit()
        finally:
            con.close()
    except Exception as e:
        LOGL.debug("공용 응답 캐시에 쓰지 못했습니다: %s" % e)


def llm_probe(llm_cfg):
    """서버에 모델 목록을 물어 토큰 한도를 받아 온다(vLLM 은 /models 응답에 max_model_len 을 준다).
    돌려주는 값: (한도 또는 None, 모델 이름 목록). 서버가 이 주소를 제공하지 않으면 (None, []).
    서버가 허용 동시 수(max_parallel), 예약량(reserve_tokens), 출력 상한(max_output_tokens)도 알려 주면
    llm_cfg["_server_info"] 에 담는다(S6 가 설정값보다 작은 쪽을 쓴다). MCP 경유면 llm_info 도구에 묻는다."""
    llm_cfg["_server_info"] = {}
    if (llm_cfg.get("transport") or "openai") == "mcp":
        if not llm_cfg.get("mcp_info_tool"):
            return None, []
        try:
            ok, info, _text = mcp_tool(llm_cfg, llm_cfg["mcp_info_tool"], {"caller": llm_cfg.get("_caller", ""),
                                                                         "run_id": llm_cfg.get("_run_id", "")}, timeout=15)
        except Exception as e:
            LOGL.debug("서버 정보 조회(%s)를 건너뜁니다: %s" % (llm_cfg["mcp_info_tool"], e))
            return None, []
        if not ok:
            return None, []
        llm_cfg["_server_info"] = {k: info[k] for k in ("max_parallel", "reserve_tokens", "max_output_tokens") if isinstance(info.get(k), int)}
        lim = info.get("max_total_tokens")
        return (lim if isinstance(lim, int) else None), ([str(info["model"])] if info.get("model") else [])
    try:
        req = urllib.request.Request(llm_cfg["endpoint"].rstrip("/") + "/models", headers=_llm_headers(llm_cfg))
        with _NO_PROXY_OPENER.open(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        items = [x for x in (data.get("data") or []) if isinstance(x, dict)]
        ids = [str(x.get("id")) for x in items if x.get("id")]
        mine = [x for x in items if x.get("id") == llm_cfg["model"]] or items
        lim = next((x["max_model_len"] for x in mine if isinstance(x.get("max_model_len"), int)), None)
        first = mine[0] if mine else {}
        llm_cfg["_server_info"] = {k: first[k] for k in ("max_parallel", "reserve_tokens", "max_output_tokens") if isinstance(first.get(k), int)}
        return lim, ids
    except Exception as e:
        LOGL.debug("서버 모델 목록 조회를 건너뜁니다: %s" % e)
        return None, []


def llm_chat(llm_cfg, system, user):
    """sLLM 한 번 호출. 돌려주는 값: (성공 여부, 응답 본문 또는 오류 설명, 소요 ms, 부가 정보)
    부가 정보(dict): in/out 서버가 알려 준 실제 토큰 수, finish 응답이 끝난 이유(length 면 출력 상한에서 잘림),
                     think 응답에 추론 블록(<think>)이 있었는지,
                     kind 오류 종류(classify_llm_error), limit 서버 한도, asked_in 실제 입력 토큰
    과부하(429, 503)는 여기서 기다렸다 다시 보낸다. 토큰 한도 초과는 다시 보내지 않고 호출자에게 알린다
    (같은 요청을 그대로 다시 보내면 결과가 달라지지 않는다. 줄이는 일은 S6 가 한다).
    이 함수는 병렬 스레드에서 불린다. llm_cfg 를 읽기만 하고 고치지 않는다."""
    if (llm_cfg.get("transport") or "openai") == "mcp":
        return _llm_chat_mcp(llm_cfg, system, user)
    url = llm_cfg["endpoint"].rstrip("/") + "/chat/completions"
    if llm_cfg.get("use_system_role", True):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    else:
        messages = [{"role": "user", "content": system + "\n\n" + user}]
    payload = {"model": llm_cfg["model"], "messages": messages, "temperature": 0, "max_tokens": llm_cfg["max_output_tokens"]}
    payload.update(llm_cfg.get("extra_body") or {})          # 서버별 추가 옵션(추론 모드 끄기 등)
    body = json.dumps(payload).encode("utf-8")
    busy_max = max(0, int(llm_cfg.get("busy_retries", 5) or 0))
    t0 = time.time()

    def ms():
        return int((time.time() - t0) * 1000)

    for wait_no in range(busy_max + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=_llm_headers(llm_cfg))
            with _NO_PROXY_OPENER.open(req, timeout=llm_cfg["timeout_sec"]) as r:
                data = json.loads(r.read().decode("utf-8"))
            choice = data["choices"][0]
            raw = choice["message"].get("content") or ""
            content = _THINK_RE.sub("", raw).strip()
            usage = data.get("usage") or {}
            return True, content, ms(), {"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens"),
                                         "finish": choice.get("finish_reason") or "", "kind": "",
                                         "think": bool(_THINK_RE.search(raw))}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:800]
            except Exception:
                pass
            kind, lim, a_in = classify_llm_error(e.code, detail)
            if kind == "busy" and wait_no < busy_max:
                ra = (e.headers.get("Retry-After") or "").strip() if e.headers else ""
                try:
                    wait = min(30.0, max(0.2, float(ra)))
                except ValueError:
                    wait = min(30.0, float(2 ** wait_no))
                LOGL.debug("서버 과부하(HTTP %s). %.1f초 뒤 다시 보냅니다(%d/%d)" % (e.code, wait, wait_no + 1, busy_max))
                time.sleep(wait)
                continue
            return False, "HTTP %s %s %s" % (e.code, e.reason, detail[:300]), ms(), {
                "kind": kind, "limit": lim, "asked_in": a_in}
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e), ms(), {"kind": "error"}
    return False, "과부하 응답이 계속됨", ms(), {"kind": "busy"}


# ============================================================================
# [M07 컴파일]  Visual Studio 찾기, cl /Zs 호출
# 목적: 03_확인사항.md 의 4.1~4.4(컴파일러, SDK, MFC, 동작 확인)를 자동으로 점검하고,
#       가능하면 파일 단위 컴파일 검사(링크 없음)를 수행한다.
# 주의: 이 구획은 Windows 에서만 동작한다. v0.3.0 까지 Windows 실기에서 검증하지 못했다.
#       문제가 생기면 로그의 [M07.컴파일] 줄을 확인한다.
# ============================================================================
LOGB = Log("M07.컴파일")
_DIAG_RE = re.compile(r"^(.*?)\((\d+)(?:,\d+)?\)\s*:\s*(fatal error|error|warning)\s+([A-Z]+\d+)\s*:\s*(.*)$")


def _ver_key(name):
    """'10.0.19041.0' 같은 판 이름을 견줄 수 있는 값으로."""
    return tuple(int(x) if x.isdigit() else 0 for x in str(name).split("."))


def resolve_sdk(sdk_path):
    """직접 지정한 Windows SDK 폴더에서 헤더 폴더들을 찾는다(--sdk-path 또는 환경변수 VUPDATE_SDK_PATH).
    받는 모양(어느 것이든 된다)
      C:\\Program Files (x86)\\Windows Kits\\10                       키트 폴더. Include 아래의 가장 높은 판을 쓴다
      C:\\Program Files (x86)\\Windows Kits\\10\\Include              Include 폴더
      C:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.19041.0  판 폴더(이 판을 쓴다)
      C:\\Program Files (x86)\\Windows Kits\\8.1                      판 폴더가 없는 키트(Include\\um, shared)
      C:\\Program Files (x86)\\Microsoft SDKs\\Windows\\v7.1A          구 SDK(Include\\windows.h)
    돌려주는 값: {"given", "version", "include_dirs", "math_h", "windows_h"}. 못 찾으면 include_dirs 가 비어 있다."""
    out = {"given": sdk_path or "", "version": "", "include_dirs": [], "math_h": "", "windows_h": ""}
    if not sdk_path:
        return out
    root = os.path.abspath(sdk_path.strip().strip('"'))
    has = lambda base: os.path.isdir(os.path.join(base, "um")) or os.path.isdir(os.path.join(base, "ucrt"))
    cands = []                                    # (판 이름, ucrt·um·shared 가 들어 있는 폴더)
    for base in (root, os.path.join(root, "Include")):
        if has(base):
            cands.append((os.path.basename(base) if base == root else "", base))
        for d in sorted(glob.glob(os.path.join(base, "*"))):
            if os.path.isdir(d) and has(d):
                cands.append((os.path.basename(d), d))
    if cands:
        cands.sort(key=lambda x: _ver_key(x[0]))
        out["version"], base = cands[-1]
        out["include_dirs"] = [p for p in (os.path.join(base, n) for n in ("ucrt", "shared", "um", "winrt", "cppwinrt")) if os.path.isdir(p)]
    else:
        for base in (os.path.join(root, "Include"), root):
            if os.path.isfile(os.path.join(base, "windows.h")) or os.path.isfile(os.path.join(base, "Windows.h")):
                out["include_dirs"] = [base]
                break
    for d in out["include_dirs"]:
        for key, names in (("math_h", ("math.h",)), ("windows_h", ("windows.h", "Windows.h"))):
            hit = next((os.path.join(d, n) for n in names if os.path.isfile(os.path.join(d, n))), "")
            out[key] = out[key] or hit
    return out


def find_tools(vs_path="", sdk_path=""):
    """컴파일러, Windows SDK, MFC 의 설치 여부를 파일 존재로 확인한다.
    vs_path: Visual Studio 설치 폴더(또는 그 안의 VsDevCmd.bat)를 직접 지정한 값. 자동 탐지가 틀릴 때 쓴다
             (--vs-path 또는 환경변수 VUPDATE_VS_PATH). 예: C:\\Program Files\\Microsoft Visual Studio\\2022\\Professional
    sdk_path: Windows SDK 폴더를 직접 지정한 값(--sdk-path 또는 환경변수 VUPDATE_SDK_PATH). 받는 모양은 resolve_sdk 를 본다.
             지정하면 그 SDK 의 헤더 폴더를 컴파일 검사의 INCLUDE 맨 앞에 넣는다(get_vs_env).
             SDK 를 설치하지 않고 폴더만 복사해 둔 PC, 여러 판 가운데 하나를 골라야 하는 PC 에서 쓴다."""
    info = {"platform": sys.platform, "cl_all": [], "cl_modern": "", "cl_2008": "", "sdk_math_h": "",
            "mfc_afxwin_h": "", "vs_root": "", "vsdevcmd": "", "cl_in_path": shutil.which("cl") or "",
            "vs_path_given": vs_path or "", "sdk_path_given": sdk_path or "", "sdk_include": [], "sdk_version": ""}
    if sdk_path:                                  # 경로 해석은 운영체제와 무관하다(시험과 preflight 표시를 위해 먼저 한다)
        sdk_info = resolve_sdk(sdk_path)
        if sdk_info["include_dirs"]:
            info["sdk_include"], info["sdk_version"] = sdk_info["include_dirs"], sdk_info["version"]
            info["sdk_math_h"] = sdk_info["math_h"] or sdk_info["windows_h"]
            LOGB.info("지정한 Windows SDK 경로를 씁니다: %s (판 %s, 헤더 폴더 %d개)"
                      % (sdk_path, sdk_info["version"] or "표기 없음", len(sdk_info["include_dirs"])))
        else:
            LOGB.warn("지정한 Windows SDK 경로에서 헤더 폴더(ucrt, um 또는 windows.h)를 찾지 못했습니다. 자동 탐지 결과를 씁니다: %s" % sdk_path)
    if not sys.platform.startswith("win"):
        LOGB.info("Windows 가 아니므로 컴파일러 점검을 건너뜁니다(%s)" % sys.platform)
        return info
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    cl = []
    for root in (pf, pf86):
        cl += glob.glob(os.path.join(root, "Microsoft Visual Studio", "*", "*", "VC", "Tools", "MSVC", "*",
                                     "bin", "Host*", "*", "cl.exe"))
        cl += glob.glob(os.path.join(root, "Microsoft Visual Studio *", "VC", "bin", "cl.exe"))
    info["cl_all"] = sorted(cl)
    modern = [p for p in cl if os.sep + "Tools" + os.sep + "MSVC" + os.sep in p]
    if vs_path:
        root = os.path.abspath(vs_path)
        if os.path.isfile(root):                  # VsDevCmd.bat 을 가리킨 경우: <설치 폴더>\Common7\Tools\VsDevCmd.bat
            root = os.path.dirname(os.path.dirname(os.path.dirname(root)))
        own = glob.glob(os.path.join(root, "VC", "Tools", "MSVC", "*", "bin", "Host*", "*", "cl.exe"))
        if own:
            modern = own
            LOGB.info("지정한 Visual Studio 경로를 씁니다: %s" % root)
        else:
            LOGB.warn("지정한 Visual Studio 경로에서 컴파일러를 찾지 못했습니다. 자동 탐지 결과를 씁니다: %s" % root)
    modern.sort(key=lambda p: (("2022" in p), ("Hostx64" in p), p.lower().endswith(os.path.join("x86", "cl.exe"))))
    if modern:
        info["cl_modern"] = modern[-1]
        info["vs_root"] = modern[-1].split(os.sep + "VC" + os.sep + "Tools")[0]
        bat = os.path.join(info["vs_root"], "Common7", "Tools", "VsDevCmd.bat")
        info["vsdevcmd"] = bat if os.path.isfile(bat) else ""
        mfc = glob.glob(os.path.join(info["vs_root"], "VC", "Tools", "MSVC", "*", "atlmfc", "include", "afxwin.h"))
        info["mfc_afxwin_h"] = mfc[-1] if mfc else ""
    old = [p for p in cl if "Visual Studio 9.0" in p]
    info["cl_2008"] = old[0] if old else ""
    sdk = glob.glob(os.path.join(pf86, "Windows Kits", "10", "Include", "*", "ucrt", "math.h"))
    if not info["sdk_include"]:                   # 직접 지정한 SDK 가 없을 때만 자동 탐지 결과를 쓴다
        info["sdk_math_h"] = sorted(sdk, key=lambda p: _ver_key(os.path.basename(os.path.dirname(os.path.dirname(p)))))[-1] if sdk else ""
    LOGB.info("컴파일러: %s" % (info["cl_modern"] or "없음"))
    LOGB.info("VS2008 컴파일러: %s" % (info["cl_2008"] or "없음"))
    LOGB.info("Windows SDK(math.h): %s" % (info["sdk_math_h"] or "없음"))
    LOGB.info("MFC(afxwin.h): %s" % (info["mfc_afxwin_h"] or "없음"))
    return info


_VS_ENV = {}


def get_vs_env(tools, arch="x86"):
    """컴파일러를 부를 때 쓸 환경변수. 이미 개발자 창이면 현재 환경을, 아니면 VsDevCmd.bat 을 불러 얻는다."""
    if arch in _VS_ENV:
        return _VS_ENV[arch]
    env = None
    if tools.get("cl_in_path"):
        env = dict(os.environ)
        LOGB.info("현재 창에 컴파일러 경로가 잡혀 있어 그대로 씁니다")
    elif tools.get("vsdevcmd"):
        cmd = 'cmd.exe /s /c ""%s" -arch=%s -no_logo && set"' % (tools["vsdevcmd"], arch)
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=120)
            text = out.stdout.decode("mbcs", "replace")
            env = {}
            for line in text.splitlines():
                if "=" in line and not line.startswith("="):
                    k, v = line.split("=", 1)
                    env[k] = v
            if "INCLUDE" not in {k.upper() for k in env}:
                LOGB.warn("VsDevCmd.bat 실행 결과에 INCLUDE 가 없습니다. 개발자 환경을 얻지 못했습니다")
                env = None
            else:
                LOGB.info("VsDevCmd.bat 으로 개발자 환경을 얻었습니다(%s)" % arch)
        except Exception as e:
            LOGB.warn("VsDevCmd.bat 실행 실패: %s" % e)
            env = None
    if env is not None and tools.get("sdk_include"):     # 직접 지정한 SDK 의 헤더가 먼저 잡히게 INCLUDE 맨 앞에 넣는다
        key = next((k for k in env if k.upper() == "INCLUDE"), "INCLUDE")
        env[key] = os.pathsep.join(list(tools["sdk_include"]) + ([env[key]] if env.get(key) else []))
        LOGB.info("INCLUDE 앞에 지정한 Windows SDK 의 헤더 폴더 %d개를 넣었습니다" % len(tools["sdk_include"]))
    _VS_ENV[arch] = env
    return env


def run_cl_syntax(tools, src_abs, cwd, flags, timeout, arch="x86"):
    """cl /Zs 로 파일 하나를 검사한다. 돌려주는 값: (실행됐는지, 진단 목록, 원문 출력)
    arch: 대상 플랫폼(x86, x64). 환경값의 플랫폼이 x64 면 x64 용 개발자 환경으로 검사한다."""
    env = get_vs_env(tools, arch)
    if env is None:
        return False, [], "개발자 환경 없음"
    path_var = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl", path=path_var) or tools.get("cl_in_path") or tools.get("cl_modern") or "cl"
    cmd = [cl, "/nologo", "/Zs", "/EHsc", "/W3"] + flags + [src_abs]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env, cwd=cwd)
    except Exception as e:
        return False, [], "%s: %s" % (type(e).__name__, e)
    enc = "mbcs" if sys.platform.startswith("win") else "utf-8"
    text = (out.stdout + out.stderr).decode(enc, "replace")
    diags = []
    for line in text.splitlines():
        m = _DIAG_RE.match(line.strip())
        if m:
            diags.append({"file": m.group(1), "line": int(m.group(2)), "kind": m.group(3),
                          "code": m.group(4), "msg": m.group(5)})
    return True, diags, text


_CFG_RE = re.compile(r"<Configuration\b(.*?)</Configuration>", re.S)
_ATTR_RE = re.compile(r'\b(\w+)\s*=\s*"([^"]*)"')
_FILE_RE = re.compile(r'<File\b[^>]*\bRelativePath\s*=\s*"([^"]*)"')


def parse_vcproj(text):
    """VS2008 .vcproj 에서 구성별 설정과 소스 목록을 뽑는다(정규식 기반)."""
    configs = []
    for m in _CFG_RE.finditer(text):
        block = m.group(1)
        head = block.split(">", 1)[0]
        attrs = dict(_ATTR_RE.findall(head))
        cfg = {"name": attrs.get("Name", ""), "charset": attrs.get("CharacterSet", ""),
               "use_mfc": attrs.get("UseOfMFC", ""), "includes": [], "defines": [], "runtime": "", "libs": [], "cl": {}}
        for tool in re.findall(r"<Tool\b([^>]*)>", block, re.S):
            ta = dict(_ATTR_RE.findall(tool))
            if ta.get("Name") == "VCCLCompilerTool":
                cfg["includes"] = [x.strip() for x in re.split(r"[;,]", ta.get("AdditionalIncludeDirectories", "")) if x.strip()]
                cfg["defines"] = [x.strip() for x in ta.get("PreprocessorDefinitions", "").split(";") if x.strip()]
                cfg["runtime"] = ta.get("RuntimeLibrary", "")
                cfg["cl"] = {k: ta[k] for k in ("WarnAsError", "TreatWChar_tAsBuiltInType", "ForceConformanceInForLoopScope",
                                                "Detect64BitPortabilityProblems") if k in ta}
            elif ta.get("Name") in ("VCLinkerTool", "VCLibrarianTool"):
                cfg["libs"] += [x for x in ta.get("AdditionalDependencies", "").split() if x]
        configs.append(cfg)
    files = [norm_rel(f).lstrip("./") for f in _FILE_RE.findall(text)]
    return {"configs": configs, "files": files}


_VCX_GROUP_RE = re.compile(r"<(PropertyGroup|ItemDefinitionGroup)\b([^>]*)>(.*?)</\1>", re.S)
_VCX_COND_RE = re.compile(r"==\s*'([^'|]+\|[^']+)'")
_VCX_ITEM_RE = re.compile(r"<(?:ClCompile|ClInclude)\s+Include=\"([^\"]+)\"")
_VCX_CODES = {"charset": {"unicode": "1", "multibyte": "2", "notset": "0"},
              "use_mfc": {"static": "1", "dynamic": "2", "false": "0"},
              "runtime": {"multithreaded": "0", "multithreadeddebug": "1", "multithreadeddll": "2", "multithreadeddebugdll": "3"}}


def _vcx_tag(block, name):
    m = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
    if not m:
        return None
    return m.group(1).strip().replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def parse_vcxproj(text):
    """VS2010 이후의 .vcxproj 에서 구성별 설정과 소스 목록을 뽑는다(정규식 기반). 돌려주는 모양은 parse_vcproj 와 같다.
    문자셋, MFC 사용, 런타임 라이브러리는 .vcproj 의 숫자 코드로 바꿔 담는다(뒤의 점검과 cl 옵션 구성이 같은 코드를 쓴다).
    한계: 프로젝트가 가져오는 .props 파일의 설정과 조건식이 복잡한 그룹은 읽지 않는다."""
    cfgs = OrderedDict()

    def cfg_of(name):
        if name not in cfgs:
            cfgs[name] = {"name": name, "charset": "", "use_mfc": "", "includes": [], "defines": [], "runtime": "",
                          "libs": [], "cl": {}, "toolset": "", "sdk": "", "std": "", "conformance": ""}
        return cfgs[name]

    for m in re.finditer(r"<ProjectConfiguration\s+Include=\"([^\"]+)\"", text):
        cfg_of(m.group(1))
    if not cfgs:
        cfg_of("")
    for kind, attrs, body in _VCX_GROUP_RE.findall(text):
        cm = _VCX_COND_RE.search(attrs)
        if "Condition" in attrs and not cm:
            continue                              # 구성 이름이 아닌 조건식은 해석하지 않는다
        targets = [cfg_of(cm.group(1))] if cm else list(cfgs.values())
        for c in targets:
            if kind == "PropertyGroup":
                for key, tag in (("charset", "CharacterSet"), ("use_mfc", "UseOfMfc")):
                    v = _vcx_tag(body, tag)
                    if v is not None:
                        c[key] = _VCX_CODES[key].get(v.lower(), "")
                for key, tag in (("toolset", "PlatformToolset"), ("sdk", "WindowsTargetPlatformVersion")):
                    v = _vcx_tag(body, tag)
                    if v:
                        c[key] = v
                continue
            cl = re.search(r"<ClCompile>(.*?)</ClCompile>", body, re.S)
            if cl:
                b = cl.group(1)
                v = _vcx_tag(b, "AdditionalIncludeDirectories")
                if v is not None:
                    c["includes"] = [x.strip() for x in v.split(";") if x.strip() and "%(" not in x]
                v = _vcx_tag(b, "PreprocessorDefinitions")
                if v is not None:
                    c["defines"] = [x.strip() for x in v.split(";") if x.strip() and "%(" not in x]
                v = _vcx_tag(b, "RuntimeLibrary")
                if v is not None:
                    c["runtime"] = _VCX_CODES["runtime"].get(v.lower(), "")
                for key, tag in (("WarnAsError", "TreatWarningAsError"), ("TreatWChar_tAsBuiltInType", "TreatWChar_tAsBuiltInType"),
                                 ("ForceConformanceInForLoopScope", "ForceConformanceInForLoopScope")):
                    v = _vcx_tag(b, tag)
                    if v is not None:
                        c["cl"][key] = v
                c["std"] = _vcx_tag(b, "LanguageStandard") or c["std"]
                c["conformance"] = _vcx_tag(b, "ConformanceMode") or c["conformance"]
            for tag in ("Link", "Lib"):
                lk = re.search(r"<%s>(.*?)</%s>" % (tag, tag), body, re.S)
                v = _vcx_tag(lk.group(1), "AdditionalDependencies") if lk else None
                if v:
                    c["libs"] += [x.strip() for x in v.split(";") if x.strip() and "%(" not in x]
    files = [norm_rel(f).lstrip("./") for f in _VCX_ITEM_RE.findall(text)]
    return {"configs": list(cfgs.values()), "files": files}


def pick_config(proj):
    """검사에 쓸 구성 하나를 고른다(Release|Win32 우선)."""
    cfgs = proj.get("configs") or []
    for want in ("Release|Win32", "Debug|Win32"):
        for c in cfgs:
            if c["name"] == want:
                return c
    return cfgs[0] if cfgs else None


def project_checks(projects):
    """프로젝트 파일(.vcproj)의 설정 가운데 버전 전환에서 문제가 되는 것을 찾는다(소스가 아니라 설정의 문제).
    돌려주는 값: [{"project", "configs", "item", "note"}]. 같은 항목은 구성 이름을 모아 한 줄로 낸다.
    out/project_settings.md 의 4절에 실린다. .vcxproj 도 같은 모양으로 읽어 점검한다(parse_vcxproj)."""
    found = OrderedDict()

    def add(pf, cfg_name, item, note):
        found.setdefault((pf, item), {"project": pf, "configs": [], "item": item, "note": note})["configs"].append(cfg_name)

    for pf, proj in sorted(projects.items()):
        for c in proj.get("configs") or []:
            cl, name = c.get("cl") or {}, c.get("name", "")
            val = lambda k: str(cl.get(k, "")).strip().lower()
            if str(c.get("toolset", "")).lower().endswith("_xp"):
                add(pf, name, "Windows XP 용 툴셋(%s)" % c["toolset"], "새 Visual Studio 에는 XP 용 툴셋이 없거나 따로 설치해야 한다. "
                    "대상 툴셋으로 바꾸면 XP 에서는 실행되지 않는다. WINVER, _WIN32_WINNT 값도 함께 올린다")
            if val("WarnAsError") in ("true", "1"):
                add(pf, name, "경고를 오류로 처리(/WX)", "새 컴파일러의 경고(C4996 등)가 전부 빌드 오류가 된다. 2절의 설정 항목을 "
                    "빠짐없이 적용하거나, 전환이 끝날 때까지 이 옵션을 끈다")
            if val("TreatWChar_tAsBuiltInType") in ("false", "0"):
                add(pf, name, "wchar_t 를 내장 형식으로 보지 않음(/Zc:wchar_t-)", "새 MFC, ATL, 표준 라이브러리와 이름이 맞지 않아 "
                    "LNK2019 가 날 수 있다. 가능하면 기본값(예)으로 바꾸고, 함께 링크하는 .lib 도 같은 설정으로 다시 빌드한다")
            if val("ForceConformanceInForLoopScope") in ("false", "0"):
                add(pf, name, "for 문 변수의 범위를 표준과 다르게 둠(/Zc:forScope-)", "사용 중단된 옵션이다(경고 D9035). 옵션을 빼면 "
                    "for 문 밖에서 루프 변수를 쓰는 코드가 C2065 오류가 된다. 그 변수를 for 문 앞에서 선언하게 고친다")
            if val("Detect64BitPortabilityProblems") in ("true", "1"):
                add(pf, name, "64비트 이식성 검사(/Wp64)", "없어진 옵션이다. 변환 뒤 경고(D9002, D9035)만 나며 빌드에는 지장 없다")
            if c.get("runtime") in ("4", "5"):
                add(pf, name, "단일 스레드 CRT(/ML, /MLd)", "없어진 라이브러리다(LNK1104 LIBC.lib). /MT 또는 /MD 로 바꾼다")
            if c.get("use_mfc") in ("1", "2"):
                add(pf, name, "MFC 사용", "대상 Visual Studio 에 MFC 구성 요소가 설치돼 있어야 한다. 없으면 MSB8041 또는 "
                    "C1083(afxwin.h)")
                if c.get("charset") == "2":
                    add(pf, name, "MFC 를 멀티바이트 문자셋으로 사용", "MFC 의 멀티바이트(MBCS) 라이브러리가 있어야 한다. VS2013 은 "
                        "따로 내려받아 설치하고, 그 뒤의 판은 설치 관리자에서 구성 요소를 확인한다. 없으면 MSB8031 또는 LNK1104")
    return list(found.values())


def cl_flags_for(env, cfg, proj_dir):
    """프로젝트 구성에서 cl 옵션을 만든다."""
    flags = []
    if env["cpp_standard"]:
        flags.append("/std:" + env["cpp_standard"])
    if env["conformance"] == "on":
        flags.append("/permissive-")
    if cfg:
        for inc in cfg["includes"]:
            inc = re.sub(r"\$\((?:ProjectDir|SolutionDir|InputDir)\)", "", inc).strip('"')
            if "$(" in inc:
                continue
            flags.append("/I" + (inc if os.path.isabs(inc) else os.path.normpath(os.path.join(proj_dir, inc))))
        for d in cfg["defines"]:
            if "$(" not in d:
                flags.append("/D" + d)
        charset = cfg["charset"] if env["charset"] == "keep" else {"mbcs": "2", "unicode": "1"}[env["charset"]]
        if charset == "1":
            flags += ["/DUNICODE", "/D_UNICODE"]
        elif charset == "2":
            flags.append("/D_MBCS")
        flags.append({"0": "/MT", "1": "/MTd", "2": "/MD", "3": "/MDd"}.get(cfg["runtime"], "/MT"))
        if cfg["use_mfc"] == "2":
            flags.append("/D_AFXDLL")
    return flags


# ============================================================================
# [실행 문맥]  단계 함수들이 공유하는 값 묶음
# ============================================================================
class Ctx:
    """실행 한 번의 문맥. 단계 함수는 이 객체와 저장소(ctx.store)만으로 자료를 주고받는다."""

    def __init__(self):
        self.package = self.pkg_name = self.run_dir = ""
        self.cfg, self.env, self.request = {}, {}, {"features": []}
        self.rules, self.rule_by_id = [], {}
        self.store = None
        self.tools, self.grade = {}, "가"
        self.dry_run = False
        self.opts = {}                            # 실행 옵션(collect_opts). 처리 범위, 단계 건너뛰기, 호출자 등
        self.rules_off = []                       # 이 환경값에서 꺼진 규칙: (ID, 제목, 이유)
        self.exceptions = {"rules_off": [], "skip": [], "brace_macros": {}}     # 예외 파일(load_exceptions)
        self._docs = OrderedDict()

    def p(self, *parts):
        return os.path.join(self.run_dir, *parts)

    def snap(self, rel):
        return os.path.join(self.run_dir, "snapshot", *rel.split("/"))


def get_doc(ctx, rel):
    """스냅샷의 파일을 읽어 분석한 Doc. 최근 것 몇 개만 메모리에 둔다."""
    c = ctx._docs
    if rel in c:
        c.move_to_end(rel)
        return c[rel]
    d = make_doc(rel, read_bytes(ctx.snap(rel)))
    c[rel] = d
    if len(c) > 24:
        c.popitem(last=False)
    return d


# ============================================================================
# [S0 준비]  환경 점검과 원본 스냅샷
# 목적: (1) 이 PC에서 컴파일 검사가 가능한지 판정한다(환경 등급 가/나).
#       (2) 원본을 스냅샷으로 복사한다. 이후 단계는 스냅샷만 읽고 원본은 건드리지 않는다.
# 산출: snapshot/, files 테이블, meta(tools, grade)
# ============================================================================
LOG0 = Log("S0.준비")
_SKIP_DIRS = {".git", ".svn", ".hg", ".vs"}


def smoke_test_compiler(ctx):
    """03_확인사항 4.4: math.h 를 쓰는 두 줄짜리 소스를 실제로 검사해 본다."""
    t = ctx.tools
    if not (t.get("cl_modern") or t.get("cl_in_path")):
        return False, "컴파일러 없음"
    tmp = ctx.p("logs", "smoke_test.cpp")
    write_text(tmp, "#include <math.h>\nint main() { return (int)sqrt(4.0); }\n", encoding="ascii")
    ran, diags, raw = run_cl_syntax(t, tmp, ctx.p("logs"), [], 120)
    write_text(ctx.p("logs", "smoke_test.log"), raw)
    if not ran:
        return False, "컴파일러 실행 실패: %s" % raw[:200]
    errs = [d for d in diags if d["kind"] != "warning"]
    if errs:
        return False, "오류 %d건(예: %s %s)" % (len(errs), errs[0]["code"], errs[0]["msg"][:120])
    if "math.h" in raw and "C1083" in raw:
        return False, "math.h 를 찾지 못함(Windows SDK 확인)"
    return True, "정상"


def decide_grade(tools):
    """환경 등급. 가: 컴파일 검사 불가 / 나: 파일 단위 컴파일 검사 가능."""
    if tools.get("smoke_ok"):
        return "나"
    return "가"


def s0_prepare(ctx):
    st = ctx.store
    LOG0.info("환경 점검 시작")
    ctx.tools = find_tools(ctx.opts.get("vs_path", ""), ctx.opts.get("sdk_path", ""))
    ok, detail = smoke_test_compiler(ctx)
    ctx.tools["smoke_ok"], ctx.tools["smoke_detail"] = ok, detail
    LOG0.info("컴파일러 동작 확인: %s" % detail)
    ctx.grade = decide_grade(ctx.tools)
    st.set_meta("tools", ctx.tools)
    st.set_meta("grade", ctx.grade)
    LOG0.info("환경 등급: %s (%s)" % (ctx.grade, "파일 단위 컴파일 검사 가능" if ctx.grade == "나"
                                  else "컴파일 검사 불가. 텍스트/구문 수준으로 진행"))

    LOG0.info("원본 스냅샷 생성: %s" % ctx.package)
    st.x("DELETE FROM files")
    run_abs = os.path.abspath(ctx.run_dir)
    n_copy = n_skip = 0
    rows = []
    for root, dirs, names in os.walk(ctx.package):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS
                         and not os.path.abspath(os.path.join(root, d)) == run_abs)
        for name in sorted(names):
            src = os.path.join(root, name)
            rel = norm_rel(os.path.relpath(src, ctx.package))
            kind = classify(rel)
            try:
                size = os.path.getsize(src)
                if kind in ("source", "project", "text"):
                    data = read_bytes(src)
                    write_bytes(ctx.snap(rel), data)
                    rows.append((rel, size, sha256_bytes(data), kind, "대기", ""))
                    n_copy += 1
                else:
                    rows.append((rel, size, "", kind, "제외", "수정 대상이 아닌 종류(%s)" % kind))
                    n_skip += 1
            except OSError as e:
                LOG0.warn("파일을 읽지 못해 제외합니다: %s (%s)" % (rel, e))
                rows.append((rel, 0, "", kind, "제외", "읽기 실패: %s" % e))
    st.many("INSERT INTO files(path,size,sha256,kind,status,note) VALUES(?,?,?,?,?,?)", rows)
    st.commit()
    if not rows:
        raise UserInputError("패키지 폴더에 파일이 없습니다: %s" % ctx.package)
    LOG0.info("스냅샷 완료: 복사 %d개, 복사 제외(바이너리 등) %d개" % (n_copy, n_skip))


# ============================================================================
# [S1 인벤토리]  파일 조사
# 목적: 파일마다 인코딩, 줄끝, 함수 경계, 큰 초기화 목록, include 를 파악한다.
#       프로젝트 파일에서 설정을 뽑고, 환경 점검 보고서(preflight.md)를 쓴다.
# 산출: files(갱신), functions, big_inits, includes, meta(projects), out/preflight.md
# ============================================================================
LOG1 = Log("S1.인벤토리")
_INC_RE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*([<"])([^>"\r\n]*)[>"]', re.M)

_KNOWN_SYS = set("""assert.h ctype.h errno.h float.h limits.h locale.h math.h setjmp.h signal.h stdarg.h
stddef.h stdio.h stdlib.h string.h time.h wchar.h wctype.h stdint.h inttypes.h memory.h malloc.h io.h
direct.h process.h conio.h fcntl.h share.h tchar.h mbstring.h crtdbg.h search.h sys/types.h sys/stat.h
sys/timeb.h windows.h windowsx.h winsock.h winsock2.h ws2tcpip.h winbase.h winuser.h wingdi.h winnt.h
windef.h winerror.h commctrl.h commdlg.h shellapi.h shlobj.h shlwapi.h ole2.h oleauto.h objbase.h
unknwn.h comdef.h mmsystem.h winreg.h winsvc.h tlhelp32.h psapi.h iphlpapi.h wininet.h richedit.h
sql.h sqlext.h sqltypes.h odbcinst.h rpc.h rpcndr.h dbghelp.h imm.h htmlhelp.h gdiplus.h""".split())


def classify_headers(ctx):
    """include 된 헤더를 출처별로 나눈다(03_확인사항 4.7). 외부 의존성 후보를 찾는 것이 목적이다."""
    st = ctx.store
    tree = {os.path.basename(r["path"]).lower() for r in st.q("SELECT path FROM files")}
    sysset = None
    t = ctx.tools
    if t.get("sdk_math_h") and t.get("vs_root"):
        sysset = set()
        inc_root = os.path.dirname(os.path.dirname(t["sdk_math_h"]))        # ...\Include\<버전>
        dirs = list(t.get("sdk_include") or []) or [os.path.join(inc_root, d) for d in ("ucrt", "um", "shared")]
        dirs += glob.glob(os.path.join(t["vs_root"], "VC", "Tools", "MSVC", "*", "include"))
        dirs += glob.glob(os.path.join(t["vs_root"], "VC", "Tools", "MSVC", "*", "atlmfc", "include"))
        for d in dirs:
            try:
                sysset.update(n.lower() for n in os.listdir(d))
            except OSError:
                pass
    out = {"내부": set(), "표준/Windows": set(), "MFC": set(), "ATL": set(), "외부 후보": set()}
    for r in st.q("SELECT DISTINCT header FROM includes"):
        h = norm_rel(r["header"]).strip()
        base = os.path.basename(h).lower()
        if base in tree:
            out["내부"].add(h)
        elif base.startswith("afx"):
            out["MFC"].add(h)
        elif base.startswith("atl"):
            out["ATL"].add(h)
        elif sysset is not None:
            out["표준/Windows" if (base in sysset or "." not in base) else "외부 후보"].add(h)
        elif "." not in base or h.lower() in _KNOWN_SYS or base in _KNOWN_SYS:
            out["표준/Windows"].add(h)
        else:
            out["외부 후보"].add(h)
    return {k: sorted(v) for k, v in out.items()}


def write_preflight_report(ctx, path):
    """환경 점검 보고서. 03_확인사항.md 의 4절(확인 절차)과 5.3(대체 수단)을 자동으로 채운 것이다."""
    st, t = ctx.store, ctx.tools
    hdr = classify_headers(ctx)
    st.set_meta("headers", hdr)
    projects = st.get_meta("projects", {})
    proj_files = [r["path"] for r in st.q("SELECT path FROM files WHERE kind='project' ORDER BY path")]
    uses_mfc = bool(hdr["MFC"])

    def yn(v):
        return "있음" if v else "없음"

    L = ["# 환경 점검 결과", "", "- 패키지: `%s`" % ctx.package, "- 점검 시각: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
         "- **환경 등급: %s** (%s)" % (ctx.grade, "파일 단위 컴파일 검사 가능" if ctx.grade == "나"
                                  else "컴파일 검사 불가. 텍스트/구문 수준 검증까지"), "",
         "## 1. 도구 (03_확인사항 4.1~4.4)", "", "| 항목 | 결과 | 경로/상세 |", "|---|---|---|",
         "| 4.1 VS 컴파일러(2015 이후) | %s | %s |" % (yn(t.get("cl_modern") or t.get("cl_in_path")), md_escape(t.get("cl_modern") or t.get("cl_in_path") or "-")),
         "| 4.1 VS2008 컴파일러 | %s | %s |" % (yn(t.get("cl_2008")), md_escape(t.get("cl_2008") or "-")),
         "| 4.2 Windows SDK(math.h) | %s | %s |" % (yn(t.get("sdk_math_h")), md_escape((t.get("sdk_math_h") or "-") + (
             " (직접 지정: %s, 판 %s, 헤더 폴더 %d개)" % (t.get("sdk_path_given"), t.get("sdk_version") or "표기 없음", len(t.get("sdk_include") or []))
             if t.get("sdk_include") else (" (직접 지정한 경로 %s 에서 찾지 못해 자동 탐지)" % t["sdk_path_given"] if t.get("sdk_path_given") else "")))),
         "| 4.3 MFC(afxwin.h) | %s | %s |" % (yn(t.get("mfc_afxwin_h")), md_escape(t.get("mfc_afxwin_h") or "-")),
         "| 4.4 컴파일러 동작 확인 | %s | %s |" % ("정상" if t.get("smoke_ok") else "실패", md_escape(t.get("smoke_detail", "-"))),
         "", "## 2. 프로젝트 파일 (4.5, 4.6)", ""]
    if not proj_files:
        L.append("프로젝트 파일이 없다. 제공처에 .sln 과 .vcproj 를 요청해야 한다.")
    else:
        L += ["| 파일 | 구성 | 문자셋 | 런타임 | MFC | 추가 include | 링크 .lib |", "|---|---|---|---|---|---|---|"]
        cs = {"0": "미설정", "1": "Unicode", "2": "MBCS"}
        rt = {"0": "/MT(정적)", "1": "/MTd(정적)", "2": "/MD(DLL)", "3": "/MDd(DLL)"}
        mf = {"0": "미사용", "1": "정적", "2": "공유 DLL"}
        for pf in proj_files:
            cfg = pick_config(projects.get(pf, {})) if pf in projects else None
            if cfg:
                L.append("| `%s` | %s | %s | %s | %s | %s | %s |" % (
                    pf, md_escape(cfg["name"]), cs.get(cfg["charset"], cfg["charset"] or "-"),
                    rt.get(cfg["runtime"], cfg["runtime"] or "-"), mf.get(cfg["use_mfc"], cfg["use_mfc"] or "-"),
                    md_escape("; ".join(cfg["includes"]) or "-"), md_escape(" ".join(cfg["libs"]) or "-")))
            else:
                L.append("| `%s` | - | - | - | - | - | - |" % pf)
    L += ["", "## 3. include 헤더의 출처 (4.7)", "", "| 출처 | 개수 | 목록 |", "|---|---|---|"]
    for k in ("내부", "표준/Windows", "MFC", "ATL", "외부 후보"):
        names = hdr[k]
        shown = ", ".join("`%s`" % n for n in names[:40]) + (" 외 %d개" % (len(names) - 40) if len(names) > 40 else "")
        L.append("| %s | %d | %s |" % (k, len(names), shown or "-"))
    L += ["", "'외부 후보'는 패키지 안에도 없고 표준/Windows 헤더로도 확인되지 않은 것이다. "
          "제공처에 헤더와 .lib 를 요청할 대상이다.", "",
          "## 4. 없을 때의 영향과 조치 (03_확인사항 5.3)", "", "| 항목 | 상태 | 영향 | 조치 |", "|---|---|---|---|"]
    has_cl = bool(t.get("cl_modern") or t.get("cl_in_path"))
    L.append("| VS 컴파일러 | %s | %s | %s |" % (
        yn(has_cl), "-" if has_cl else "컴파일 검사 불가", "-" if has_cl else "Visual Studio Installer 에서 'C++를 사용한 데스크톱 개발' 추가"))
    L.append("| Windows SDK | %s | %s | %s |" % (
        yn(t.get("sdk_math_h")), "-" if t.get("sdk_math_h") else "표준 헤더를 못 찾아 컴파일 검사 불가",
        "-" if t.get("sdk_math_h") else "Visual Studio Installer 에서 Windows SDK 추가"))
    if uses_mfc:
        L.append("| MFC | %s | %s | %s |" % (
            yn(t.get("mfc_afxwin_h")), "-" if t.get("mfc_afxwin_h") else "MFC 를 쓰는 파일의 컴파일 검사 불가",
            "-" if t.get("mfc_afxwin_h") else "Visual Studio Installer 에서 MFC 구성요소 추가"))
    L.append("| 프로젝트 파일 | %s | %s | %s |" % (
        yn(proj_files), "-" if proj_files else "컴파일 옵션을 추정해야 한다", "-" if proj_files else "제공처에 요청"))
    L.append("| 외부 헤더 | %s | %s | %s |" % (
        "빠진 것 없음" if not hdr["외부 후보"] else "후보 %d개" % len(hdr["외부 후보"]),
        "-" if not hdr["외부 후보"] else "해당 헤더를 쓰는 파일은 컴파일 검사 불가",
        "-" if not hdr["외부 후보"] else "제공처에 헤더와 .lib 요청, 또는 스텁 헤더"))
    L += ["", "이 도구는 환경 등급 '다'(프로젝트 변환과 전체 빌드)를 자동화하지 않는다. "
          "전체 빌드는 out/build_guide.md 의 절차로 직접 수행한다.", ""]
    write_text(path, "\n".join(L))
    LOG1.info("환경 점검 보고서 작성: %s" % path)


def s1_inventory(ctx):
    st = ctx.store
    for tbl in ("functions", "big_inits", "includes"):
        st.x("DELETE FROM %s" % tbl)
    big = ctx.cfg["limits"]["big_init_chars"]
    projects, n_src, n_bad, n_ro, n_fn, n_big, n_parse_fail, n_out = {}, 0, 0, 0, 0, 0, 0, 0
    only, exclude = ctx.opts.get("only") or [], ctx.opts.get("exclude") or []
    for r in st.q("SELECT path, kind FROM files WHERE kind IN ('source','project','text') ORDER BY path"):
        rel, kind = r["path"], r["kind"]
        data = read_bytes(ctx.snap(rel))
        if kind != "source":
            d = make_doc(rel, data, analyze=False)
            st.x("UPDATE files SET encoding=?, bom=?, eol=?, roundtrip=?, n_lines=?, status=?, note=? WHERE path=?",
                 (d.label, 1 if d.bom else 0, d.eol, int(d.roundtrip), d.n_lines if d.ok else 0, "탐지제외",
                  "이 종류의 파일은 규칙으로 탐지하지 않는다" if d.ok else d.note, rel))
            if d.ok and rel.lower().endswith((".vcproj", ".vcxproj")):
                try:
                    projects[rel] = parse_vcproj(d.text) if rel.lower().endswith(".vcproj") else parse_vcxproj(d.text)
                except Exception as e:
                    LOG1.warn("프로젝트 파일 해석 실패: %s (%s)" % (rel, e))
            continue
        d = get_doc(ctx, rel)
        n_src += 1
        if not d.ok:
            n_bad += 1
            st.x("UPDATE files SET status='제외', note=? WHERE path=?", (d.note, rel))
            LOG1.warn("인코딩을 판별하지 못해 제외합니다: %s" % rel)
            continue
        status, note = "대상", ""
        if not d.roundtrip:
            status, note = "읽기전용", "같은 바이트로 다시 쓸 수 없는 인코딩/줄끝이라 수정하지 않는다"
            n_ro += 1
        if not in_scope(rel, only, exclude):
            # 처리 범위 밖: 탐지와 수정은 하지 않는다. 스냅샷과 include 조사는 그대로 한다(다른 파일이 참조하므로)
            status, note = "범위제외", "--only/--exclude 로 정한 처리 범위 밖"
            n_out += 1
        if not d.parse_ok:
            n_parse_fail += 1
            note = (note + " / " if note else "") + "괄호 짝 분석 실패(매크로 또는 #if). 함수 경계를 쓰지 않는다"
        st.x("UPDATE files SET encoding=?, bom=?, eol=?, roundtrip=?, parse_ok=?, n_lines=?, status=?, note=? WHERE path=?",
             (d.label, 1 if d.bom else 0, d.eol, int(d.roundtrip), int(d.parse_ok), d.n_lines, status, note, rel))
        if d.parse_ok:
            for f in d.funcs:
                st.x("INSERT INTO functions(path,name,sig_start,body_start,end,start_line,end_line,est_tokens) "
                     "VALUES(?,?,?,?,?,?,?,?)",
                     (rel, f["name"], f["sig_start"], f["body_start"], f["end"], d.line_of(f["sig_start"]),
                      d.line_of(f["end"] - 1), 0))
                n_fn += 1
        for s, e in d.inits:
            if e - s >= big:
                st.x("INSERT INTO big_inits(path,start,end,length,sha256) VALUES(?,?,?,?,?)",
                     (rel, s, e, e - s, sha256_bytes(d.text[s:e].encode("utf-8"))))
                n_big += 1
                LOG1.info("큰 초기화 목록: %s %d줄, %d자 (sLLM 전송 제외 대상)" % (rel, d.line_of(s), e - s))
        for m in _INC_RE.finditer(d.code):
            st.x("INSERT INTO includes(path,header,angle) VALUES(?,?,?)",
                 (rel, d.text[m.start(2):m.end(2)], 1 if m.group(1) == "<" else 0))
    st.set_meta("projects", projects)
    notes = project_checks(projects)
    st.set_meta("project_notes", notes)
    if notes:
        LOG1.info("프로젝트 파일 점검: 확인할 설정 %d건(out/project_settings.md 4절)" % len(notes))
    st.commit()
    LOG1.info("소스 %d개: 인코딩 판별 불가 %d, 읽기전용 %d, 괄호 짝 분석 실패 %d, 처리 범위 밖 %d"
              % (n_src, n_bad, n_ro, n_parse_fail, n_out))
    LOG1.info("함수 %d개, 큰 초기화 목록 %d개, 프로젝트 파일 %d개" % (n_fn, n_big, len(projects)))
    write_preflight_report(ctx, ctx.p("out", "preflight.md"))


# ============================================================================
# [S2 탐지]  규칙 스캔
# 목적: 환경값에 맞는 규칙을 전체 소스에 적용해 이슈를 만든다. sLLM 을 쓰지 않는다.
# 산출: issues
# ============================================================================
LOG2 = Log("S2.탐지")


_SKIP_MARK_RE = re.compile(r"vupdate:skip(-next)?((?:[ \t]+R-[A-Za-z0-9-]+)*)")
# 코드 수정이 아니라 설정이나 환경으로 푸는 빌드 오류. sLLM 에 보내지 않는다
_BUILD_NO_LLM = {"C1083", "C1189", "C1010", "C1001", "C1047", "C1128", "C1003", "C4819", "C2001"}
# 메시지에 나온 이름이 그 줄에 있어야 하는 오류. 없으면 로그와 소스가 다른 것으로 본다
_BUILD_NAME_CODES = {"C2065", "C3861", "C2039", "C2061"}


def _skip_marks(d):
    """소스에 적어 둔 예외 표식을 찾는다(요구사항 C11 의 줄 단위 예외).
    '// vupdate:skip' 은 그 줄, '// vupdate:skip-next' 는 다음 줄의 탐지를 뺀다. 뒤에 규칙 ID 를 적으면 그 규칙만 뺀다.
    돌려주는 값: {줄 번호: 규칙 ID 집합('*' 는 전부)}"""
    marks = {}
    if "vupdate:skip" not in d.text:
        return marks
    for m in _SKIP_MARK_RE.finditer(d.text):
        line = d.line_of(m.start()) + (1 if m.group(1) else 0)
        marks.setdefault(line, set()).update({x.upper() for x in m.group(2).split()} or {"*"})
    return marks


def _hit_skipped(ctx, d, rule_id, line, marks):
    ids = marks.get(line)
    if ids and ("*" in ids or rule_id.upper() in ids):
        return True
    return exc_skip(ctx.exceptions, d.rel, rule_id, line)


def _build_issues(ctx):
    """빌드 로그의 오류를 이슈로 넣는다(요구사항 F8. 실행 옵션 --build-log).
    전제: --package 로 준 폴더가 그 로그를 낸 소스 트리여야 한다(줄 번호가 맞아야 한다).
    컴파일 단계의 오류 가운데 패키지 파일의 줄을 가리키는 것만 sLLM 대상(T3)으로 넣는다. 지침은 컴파일러 메시지와
    오류 코드별 대응(M12 의 ERROR_GUIDE)으로 만든다(_build_guidance). 나머지는 수동 검토 목록에 올린다.
      - 프로젝트, 옵션, 링크 단계의 오류: 소스 수정으로 풀리지 않는다
      - 헤더를 못 찾는 오류(C1083) 등: 설정이나 환경으로 푼다
      - 메시지에 나온 이름이 그 줄에 없는 오류: 로그와 소스가 다른 판이다
      - 한 파일에서 limits.buildlog_max_per_file 건을 넘는 오류: 앞의 오류를 고치면 없어지는 경우가 많다"""
    path = ctx.opts.get("build_log")
    if not path:
        return
    st = ctx.store
    diags = triage_build_log(parse_build_log(read_log_text(path)))
    status_of = {r["path"]: r["status"] for r in st.q("SELECT path, status FROM files WHERE kind='source'")}
    rels_lower = {rel.lower(): rel for rel in status_of}
    cap = max(1, int(ctx.cfg["limits"].get("buildlog_max_per_file", 5)))
    per_file, seen, n = {}, set(), {"sLLM": 0, "수동": 0, "소스 밖": 0}
    sql = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)")
    for d in diags:
        if d["kind"] != "error":
            continue
        title = ("빌드 오류 %s: %s" % (d["code"], " ".join(d["msg"].split())))[:220]
        rel = _match_rel(d["file"], rels_lower) if d["file"] else ""
        if d["stage"] != "컴파일" or not rel or not d["line"]:
            key = (d["code"], d["msg"][:80])
            if key not in seen:
                seen.add(key)
                why = "소스 줄이 없는 오류(%s 단계)" % d["stage"] if d["stage"] != "컴파일" else "패키지 밖의 파일이거나 줄 번호가 없는 오류"
                st.x(sql, ("", 0, 0, 0, "build", "B-" + d["code"], "T3", 0, title, d["file"] or "", 0, "manual",
                           "%s. 대응: %s" % (why, d["action"])))
                n["소스 밖"] += 1
            continue
        key = (rel, d["line"], d["code"])
        if key in seen:
            continue
        seen.add(key)
        doc = get_doc(ctx, rel)
        status, note = "open", ""
        a = b = 0
        if doc.ok and d["line"] <= doc.n_lines:
            a, b = doc.line_span(d["line"])
            a += len(doc.text[a:b]) - len(doc.text[a:b].lstrip())
        names = re.findall(r"'([A-Za-z_]\w*)'", d["msg"])
        if not doc.ok or d["line"] > doc.n_lines or (d["code"] in _BUILD_NAME_CODES and names
                                                    and not any(re.search(r"\b%s\b" % re.escape(x), doc.text[a:b]) for x in names)):
            status, note = "manual", "로그의 줄과 소스가 맞지 않음. 이 로그를 낸 소스 트리를 --package 로 주었는지 확인한다"
        elif status_of[rel] != "대상":
            status, note = "manual", "처리 범위 밖이거나 자동으로 고칠 수 없는 파일. 대응: %s" % d["action"]
        elif d["code"] in _BUILD_NO_LLM:
            status, note = "manual", "코드 수정이 아니라 설정이나 환경으로 푸는 오류. 대응: %s" % d["action"]
        elif per_file.get(rel, 0) >= cap:
            status, note = "manual", "한 파일에서 한 번에 다루는 빌드 오류 수(%d)를 넘음. 앞의 오류를 고친 뒤 다시 빌드해 확인한다" % cap
        else:
            per_file[rel] = per_file.get(rel, 0) + 1
        st.x(sql, (rel, a, b, d["line"], "build", "B-" + d["code"], "T3", 1 if status == "open" else 0, title,
                   doc.text[a:b][:160] if doc.ok else "", 0, status, note))
        n["sLLM" if status == "open" else "수동"] += 1
    st.set_meta("build_log", {"path": path, "llm": n["sLLM"], "manual": n["수동"], "other": n["소스 밖"]})
    LOG2.info("빌드 로그 %s: 오류를 이슈로 넣음(sLLM 대상 %d건, 수동 검토 %d건, 소스 줄이 없는 것 %d건)"
              % (path, n["sLLM"], n["수동"], n["소스 밖"]))


def _build_guidance(issue):
    """빌드 오류 이슈(B-코드)에 붙일 수정 지침. 컴파일러 메시지는 이슈 제목에 있다."""
    rid = str(issue["rule_id"])
    if not rid.startswith("B-"):
        return ""
    _cause, action, _known = error_advice(rid[2:], issue["title"].split(": ", 1)[-1])
    return ("대상 컴파일러가 %d줄에서 낸 오류다. 흔한 대응: %s. 이 오류가 없어지도록 그 줄 주변만 최소한으로 고친다. "
            "원인이 이 조각 밖(다른 헤더의 선언, 프로젝트 설정)에 있거나 고칠 방법을 정할 수 없으면 NO_CHANGE 를 쓴다."
            % (issue["line"], action))


def s2_detect(ctx):
    st = ctx.store
    st.x("DELETE FROM issues")
    counts, n_skipped = {}, 0
    files = st.q("SELECT path, status FROM files WHERE kind='source' AND status IN ('대상','읽기전용') ORDER BY path")
    LOG2.info("규칙 %d개를 소스 %d개에 적용" % (len(ctx.rules), len(files)))
    for r in files:
        rel, editable = r["path"], r["status"] == "대상"
        d = get_doc(ctx, rel)
        marks = _skip_marks(d)
        for rule in ctx.rules:
            try:
                hits = rule_hits(rule, d, ctx.env)
            except Exception as e:
                LOG2.error("규칙 %s 가 %s 에서 실패. 이 규칙은 이 파일에서 건너뜁니다: %s" % (rule["id"], rel, e))
                continue
            if hits and (marks or ctx.exceptions.get("skip")):
                kept = [h for h in hits if not _hit_skipped(ctx, d, rule["id"], d.line_of(h["start"]), marks)]
                n_skipped += len(hits) - len(kept)
                hits = kept
            if not hits:
                continue
            counts[rule["id"]] = counts.get(rule["id"], 0) + len(hits)
            tier = rule["tier"]
            if rule.get("collapse"):
                h = hits[0]
                st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rel, h["start"], h["end"], d.line_of(h["start"]), "rule", rule["id"], tier, 0, rule["title"],
                      "%d곳(첫 위치 %d줄)" % (len(hits), d.line_of(h["start"])), 0,
                      "manual" if tier == "T3" else "open", "자동 수정 대상이 아닌 유형" if tier == "T3" else ""))
                continue
            for h in hits:
                status, note = "open", ""
                if not editable and tier != "T0":
                    status, note = "manual", "파일을 같은 바이트로 다시 쓸 수 없어 자동 수정하지 않음"
                elif tier == "T3" and not rule.get("llm"):
                    status, note = "manual", "자동 수정 대상이 아닌 유형"
                st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rel, h["start"], h["end"], d.line_of(h["start"]), "rule", rule["id"], tier,
                      1 if rule.get("llm") else 0, rule["title"], h.get("detail", ""), h.get("after"),
                      1 if rule.get("review") else 0, status, note))
    st.set_meta("skipped_hits", n_skipped)
    if n_skipped:
        LOG2.info("예외(예외 파일, 소스의 vupdate:skip 표식)로 건너뛴 탐지 지점 %d곳" % n_skipped)
    _build_issues(ctx)
    st.commit()
    for rid in sorted(counts):
        LOG2.info("  %-22s %5d곳  %s" % (rid, counts[rid], ctx.rule_by_id[rid]["title"]))
    LOG2.info("탐지 합계: %d곳 (규칙 %d종)" % (sum(counts.values()), len(counts)))


# ============================================================================
# [S3 설정흡수]  코드 변경 없이 해결할 항목 정리
# 목적: T0 이슈를 "프로젝트 설정에 추가할 항목"으로 모은다. 코드는 건드리지 않는다.
# 산출: meta(settings). 문서는 S9 에서 out/project_settings.md 로 쓴다.
# ============================================================================
LOG3 = Log("S3.설정흡수")


def s3_absorb(ctx):
    st = ctx.store
    settings = []
    for r in st.q("SELECT rule_id, COUNT(*) AS n FROM issues WHERE tier='T0' GROUP BY rule_id ORDER BY rule_id"):
        rule = ctx.rule_by_id.get(r["rule_id"], {})
        settings.append({"rule_id": r["rule_id"], "title": rule.get("title", ""),
                         "setting": rule.get("setting", ""), "files": r["n"]})
        LOG3.info("%s: 파일 %d개 -> %s" % (r["rule_id"], r["n"], rule.get("setting", "")))
    st.x("UPDATE issues SET status='absorbed' WHERE tier='T0'")
    st.set_meta("settings", settings)
    LOG3.info("설정으로 흡수할 항목 %d종" % len(settings))


# ============================================================================
# [S4 규칙적용]  T1/T2 규칙 -> 수정 레코드
# 목적: 자동 치환이 가능한 이슈를 수정 레코드로 만든다. 파일은 아직 쓰지 않는다.
# 산출: edits(origin='rule', base='snapshot')
# ============================================================================
LOG4 = Log("S4.규칙적용")


def s4_apply_rules(ctx):
    st = ctx.store
    st.x("DELETE FROM edits")
    n_ok = n_overlap = 0
    last_path, last_end = None, -1
    rows = st.q("SELECT * FROM issues WHERE tier IN ('T1','T2') AND after IS NOT NULL AND status='open' "
                "ORDER BY path, start, end")
    for r in rows:
        if r["path"] != last_path:
            last_path, last_end = r["path"], -1
        if r["start"] < last_end:
            st.x("UPDATE issues SET status='manual', note='다른 규칙 수정과 범위가 겹침' WHERE id=?", (r["id"],))
            n_overlap += 1
            continue
        d = get_doc(ctx, r["path"])
        before = d.text[r["start"]:r["end"]]
        st.x("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (r["path"], "snapshot", r["start"], r["end"], before, r["after"], "rule", r["rule_id"],
              str(r["id"]), r["review"], "accepted", ""))
        last_end = r["end"]
        n_ok += 1
    st.commit()
    LOG4.info("규칙 수정 레코드 %d건 생성, 겹쳐서 수동 검토로 보낸 것 %d건" % (n_ok, n_overlap))


# ============================================================================
# [S5 영향분석]  요청사항 처리와 헤더 의존 관계
# 목적: (1) 요청사항 중 매핑표로 결정되는 부분(배열의 상수 인덱스 접근)을 수정 레코드로 만든다.
#           선언부는 수동 검토로 보낸다. 변수 인덱스 접근은 기본이 수동 검토이고,
#           요청에 "llm_variable_index": true 가 있으면 sLLM 에 맡긴다(결과는 검토 대상).
#       (2) 헤더를 include 하는 파일 목록을 만들어 둔다(헤더가 바뀌면 재확인할 대상).
# 산출: issues(source='request'), edits(origin='request'), meta(dependents)
# 하지 않는 것: 구조체 정의 생성과 파일 간 변경 계약의 자동 전파.
# ============================================================================
LOG5 = Log("S5.영향분석")
_NOT_TYPE_WORDS = {"return", "else", "case", "delete", "new", "sizeof", "throw", "goto"}


_LAYOUT_CALLS = ("memcpy|memmove|memset|memcmp|send|recv|sendto|recvfrom|WriteFile|ReadFile|fwrite|fread|"
                 "CopyMemory|ZeroMemory|FillMemory|MoveMemory")


def _find_struct_defs(code, target):
    """마스킹된 본문에서 구조체 target 의 정의를 찾는다. 돌려주는 값: [(struct 시작, 여는 괄호, 닫는 괄호)]
    'struct target {...}', 'typedef struct _x {...} target;', 'typedef struct {...} target, *ptarget;' 를 알아본다."""
    out = []
    for m in re.finditer(r"\b(?:struct|union)\b\s*([A-Za-z_]\w*)?\s*(?::[^{;]*)?\{", code):
        depth, i = 0, m.end() - 1
        while i < len(code):
            ch = code[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        if depth != 0:
            continue
        tail = code[i + 1:code.find(";", i + 1) if code.find(";", i + 1) >= 0 else i + 1]
        if m.group(1) == target or re.search(r"(?<![\w*])%s\b" % re.escape(target), tail):
            out.append((m.start(), m.end() - 1, i))
    return out


def _struct_change(ctx, ft, files):
    """요청 유형 struct_change: 구조체(IPC 메시지 등)의 필드 이름과 정의를 바꾼다(요구사항 B2, C13, E2~E6).
    컴파일러 없이 글자만 보고 하는 근사다. 자동으로 고치는 것은 "이 구조체 타입으로 선언된 변수의 멤버 접근"뿐이고,
    타입을 글자로 확정할 수 없는 접근(매크로, 별칭, 함수 반환값, 중첩 멤버)은 수동 검토나 sLLM 으로 보낸다.
      1. 정의: definition_after 가 있으면 정의 전체를 그것으로 바꾸고, 없으면 정의 안의 필드 이름만 바꾼다
      2. 변수: 'target v', 'target *p', 'target arr[N]' 과 별칭 타입으로 선언된 이름을 모은다(그 파일과 그 파일이 포함하는 헤더)
      3. 접근: v.old, p->old, arr[i].old 를 새 이름으로 바꾼다(검토 표시)
      4. 남은 '.old', '->old': 어느 구조체의 것인지 알 수 없다. llm_unresolved 가 참이면 sLLM, 아니면 수동 검토
      5. 배치 점검: 정의가 바뀌면 sizeof, memcpy, 송수신, offsetof 사용처를 수동 검토 목록에 올린다
    돌려주는 값: 변경 계약 요약(dict). 보고서의 "변경 계약"에 실린다."""
    st = ctx.store
    target, ren = ft["target"], {str(k): str(v) for k, v in (ft.get("rename") or {}).items()}
    new_def = (ft.get("definition_after") or "").strip()
    check_layout = bool(ft.get("check_layout", bool(new_def)))
    T = re.escape(target)
    sql_i = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    sql_e = ("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
    # --- 타입 이름(별칭 포함)과 그 타입으로 선언된 변수 이름을 파일별로 모은다 ---
    types, tail_vars = {target}, {}
    for rel in files:
        code = get_doc(ctx, rel).code
        for s, _o, close in _find_struct_defs(code, target):
            tail = code[close + 1:code.find(";", close + 1) if code.find(";", close + 1) >= 0 else close + 1]
            found = set(re.findall(r"[A-Za-z_]\w*", tail)) - _NOT_TYPE_WORDS
            if re.search(r"\btypedef\s*$", code[max(0, s - 24):s]):
                types.update(found)                # typedef struct {...} 이름, *포인터이름;  -> 타입의 별칭
            else:
                tail_vars.setdefault(rel, set()).update(found)      # struct 이름 {...} 변수;  -> 변수
        for m in re.finditer(r"\btypedef\s+(?:struct\s+)?%s\s*\*?\s*([A-Za-z_]\w*)\s*;" % T, code):
            types.add(m.group(1))
    types.discard("")
    decl_rx = re.compile(r"\b(?:%s)\b(?:\s+const)?[\s\*&]*\b([A-Za-z_]\w*)\s*(?=[;,=\[\)])" % "|".join(re.escape(x) for x in sorted(types)))
    decl = {rel: ({m.group(1) for m in decl_rx.finditer(get_doc(ctx, rel).code)} | tail_vars.get(rel, set()))
            - _NOT_TYPE_WORDS - types for rel in files}
    by_base = {}
    for rel in files:
        by_base.setdefault(os.path.basename(rel).lower(), []).append(rel)
    inc = {}
    for r in st.q("SELECT path, header FROM includes"):
        inc.setdefault(r["path"], set()).update(by_base.get(os.path.basename(norm_rel(r["header"])).lower(), []))
    n = {"정의": 0, "필드 치환": 0, "미확정 접근": 0, "배치 점검": 0}
    title = "요청사항: 구조체 %s 변경" % target
    for rel in files:
        d = get_doc(ctx, rel)
        taken = [(e["start"], e["end"]) for e in st.q("SELECT start, end FROM edits WHERE path=? AND status='accepted'", (rel,))]

        def add(s, e, after, tier, status, note, what, llm=0):
            if after is not None and any(a < e and s < b for a, b in taken):
                after, tier, status, note = None, "T3", "manual", "다른 수정과 범위가 겹침"
            cur = st.x(sql_i, (rel, s, e, d.line_of(s), "request", ft["id"], tier, llm, "%s(%s)" % (title, what),
                               d.text[s:e][:160], after, 0, status, note))
            if after is not None:
                st.x(sql_e, (rel, "snapshot", s, e, d.text[s:e], after, "request", ft["id"], str(cur.lastrowid), 1, "accepted", ""))
                taken.append((s, e))
            return after is not None

        def_spans = []
        for s, o, c in _find_struct_defs(d.code, target):
            def_spans.append((s, c + 1))
            n["정의"] += 1
            if new_def:
                add(s, c + 1, new_def, "T1", "open", "", "정의 교체")
            else:
                for old, new in ren.items():
                    for m in re.finditer(r"\b%s\b(?=\s*(?:\[[^\]]*\]\s*)*(?::\s*\d+\s*)?[;,])" % re.escape(old), d.code[o:c]):
                        add(o + m.start(), o + m.end(), new, "T1", "open", "", "정의의 필드 이름")
            if ft.get("pack") and not re.search(r"#\s*pragma\s+pack", d.text[:s]):
                add(s, min(c + 1, s + 40), None, "T3", "manual", "요청의 패킹(%s)을 지정하는 #pragma pack 이 정의 앞에 없음" % ft["pack"], "패킹")
        names = set(decl.get(rel, ()))
        for h in inc.get(rel, ()):
            names |= decl.get(h, set())
        in_def = lambda p: any(a <= p < b for a, b in def_spans)
        done = set()
        if names and ren:
            rx = re.compile(r"\b(?:%s)\b\s*(?:\[[^\]\n]*\]\s*)*(?:\.|->)\s*(%s)\b"
                            % ("|".join(re.escape(x) for x in sorted(names)), "|".join(re.escape(x) for x in ren)))
            for m in rx.finditer(d.code):
                if not in_def(m.start()) and add(m.start(1), m.end(1), ren[m.group(1)], "T1", "open", "", "필드 이름"):
                    n["필드 치환"] += 1
                done.add(m.start(1))
        if ren:
            for m in re.finditer(r"(?:\.|->)\s*(%s)\b" % "|".join(re.escape(x) for x in ren), d.code):
                if m.start(1) in done or in_def(m.start(1)):
                    continue
                n["미확정 접근"] += 1
                if ft.get("llm_unresolved"):
                    add(m.start(1), m.end(1), None, "T3", "open", "어느 구조체의 필드인지 글자로 확정할 수 없음. sLLM 에 맡김(검토 필요)", "미확정 접근", llm=1)
                else:
                    add(m.start(1), m.end(1), None, "T3", "manual", "어느 구조체의 필드인지 글자로 확정할 수 없음(선언을 찾지 못함)", "미확정 접근")
        if check_layout:
            alt = "|".join(re.escape(x) for x in sorted(types | names))
            for rx, what in ((r"\bsizeof\s*\(?\s*(?:struct\s+)?\*?\s*(?:%s)\b" % alt, "sizeof"),
                             (r"\b(?:%s)\s*\([^;{}]*\b(?:%s)\b" % (_LAYOUT_CALLS, alt), "메모리 복사·송수신"),
                             (r"\boffsetof\s*\(\s*(?:struct\s+)?(?:%s)\b" % alt, "offsetof")):
                for m in re.finditer(rx, d.code):
                    if in_def(m.start()):
                        continue
                    n["배치 점검"] += 1
                    add(m.start(), min(m.end(), m.start() + 60), None, "T3", "manual",
                        "구조체의 크기나 필드 배치가 바뀌면 이 줄의 동작이 달라진다. 상대 프로세스와 크기, 패킹, 32/64비트 타입 크기를 맞춘다",
                        "배치 점검: %s" % what)
    LOG5.info("요청 %s (구조체 %s): 정의 %d곳, 필드 치환 %d곳, 미확정 접근 %d곳, 배치 점검 %d곳"
              % (ft["id"], target, n["정의"], n["필드 치환"], n["미확정 접근"], n["배치 점검"]))
    if not n["정의"]:
        LOG5.warn("요청 %s: 구조체 %s 의 정의를 패키지에서 찾지 못했습니다. 이름을 확인하세요" % (ft["id"], target))
    return {"id": ft["id"], "type": "struct_change", "target": target, "rename": ren, "definition_after": new_def,
            "aliases": sorted(types - {target}), "counts": n,
            "note": "컴파일러 없이 글자로 분석한 근사다. 매크로, 별칭, 포인터를 거친 접근은 자동으로 고치지 않았다"}


_INT_LIT = r"(?:0[xX][0-9a-fA-F]+|\d+)"
_MEM_CALLS = ("memset|memcpy|memmove|memcmp|ZeroMemory|CopyMemory|FillMemory|MoveMemory|fwrite|fread|WriteFile|ReadFile|"
              "send|recv|sendto|recvfrom")


def _to_int(s):
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _resolve_names(ctx, files, names):
    """매크로나 열거형 이름의 정수 값을 패키지에서 찾는다(#define 이름 값, enum 의 항목).
    돌려주는 값: {이름: 값}. 못 찾았거나 값이 둘 이상인 이름은 넣지 않는다."""
    want, found = set(names), {}
    if not want:
        return {}
    for rel in files:
        code = get_doc(ctx, rel).code
        if not any(n in code for n in want):
            continue
        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+\(?[ \t]*(%s)[uUlL]*[ \t]*\)?[ \t]*$" % _INT_LIT, code, re.M):
            if m.group(1) in want:
                found.setdefault(m.group(1), set()).add(_to_int(m.group(2)))
        for m in re.finditer(r"\benum\b[^{;]*\{([^{}]*)\}", code):
            val = -1
            for item in m.group(1).split(","):
                im = re.match(r"\s*([A-Za-z_]\w*)\s*(?:=\s*(%s))?\s*$" % _INT_LIT, item)
                if not im:
                    val = None if item.strip() else val           # 계산식으로 준 값: 그 뒤의 값은 알 수 없다
                    continue
                if im.group(2) is not None:
                    val = _to_int(im.group(2))
                elif val is not None:
                    val += 1
                if val is not None and im.group(1) in want:
                    found.setdefault(im.group(1), set()).add(val)
    return {k: next(iter(v)) for k, v in found.items() if len(v) == 1}


def _array_plan(ctx, ft, files):
    """배열 -> 구조체 요청의 변경 계약을 만든다(요구사항 E3): 구조체 이름, 요소 타입, 크기, 필드 순서, 정의.
    돌려주는 값: (계약 dict, "") 또는 (None, 정할 수 없는 이유). 정할 수 없으면 선언은 수동 검토로 남긴다.
    필드는 인덱스 순서대로 놓고, 매핑에 없는 인덱스는 reserved_번호 로 채워 크기와 배치를 배열과 같게 둔다
    (요청의 "unmapped": "drop" 이면 채우지 않는다). 필드 타입은 배열의 요소 타입이다(요청의 types 로 바꿀 수 있다)."""
    target, mapping = ft["target"], ft["mapping"]
    decl_rx = re.compile(r"(?m)^(?P<indent>[ \t]*)(?P<pre>(?:(?:extern|static|const|volatile)\s+)*)(?P<type>[A-Za-z_][\w \t]*?[\w\*])\s*"
                         r"\b%s\s*\[\s*(?P<size>[^\]\n]*)\]\s*(?P<init>=[^;]*)?;" % re.escape(target))
    decls = []
    for rel in files:
        for m in decl_rx.finditer(get_doc(ctx, rel).code):
            if m.group("type").split()[-1] not in _NOT_TYPE_WORDS:
                decls.append((rel, m.start(), m.end(), m.groupdict()))
    if not decls:
        return None, "배열의 선언을 패키지에서 찾지 못함"
    types = {" ".join(g["type"].split()) for _r, _s, _e, g in decls}
    if len(types) != 1:
        return None, "선언마다 요소 타입이 다름(%s)" % ", ".join(sorted(types))
    elem = types.pop()
    sizes = {g["size"].strip() for _r, _s, _e, g in decls if g["size"].strip()}
    is_int = lambda x: re.match(r"^%s$" % _INT_LIT, x) is not None
    vals = _resolve_names(ctx, files, [k for k in list(mapping) + sorted(sizes) if not is_int(k)])
    num = lambda x: _to_int(x) if is_int(x) else vals.get(x)
    size_vals = {num(x) for x in sizes}
    if len(size_vals) != 1 or None in size_vals:
        return None, "배열 크기를 정할 수 없음(%s)" % (", ".join(sorted(sizes)) or "크기를 적은 선언이 없음")
    n = size_vals.pop()
    if not 0 < n <= 1024:
        return None, "배열 크기(%d)가 구조체로 옮기기에 맞지 않음" % n
    idx = {}
    for k, field in mapping.items():
        v = num(k)
        if v is None:
            return None, "인덱스 이름 %s 의 값을 패키지에서 찾지 못함(#define 이나 enum 이 없음)" % k
        if v in idx or not 0 <= v < n:
            return None, "인덱스 %s(%d)가 다른 것과 겹치거나 배열 크기(%d)를 벗어남" % (k, v, n)
        idx[v] = field
    sname, ftypes = ft.get("struct_name") or (target.upper() + "_T"), ft.get("types") or {}
    lines = ["typedef struct %s" % sname, "{"]
    for i in range(n):
        if i in idx:
            lines.append("    %s %s;" % (ftypes.get(idx[i], elem), idx[i]))
        elif ft.get("unmapped", "reserved") != "drop":
            lines.append("    %s reserved_%d;" % (elem, i))
    lines.append("} %s;" % sname)
    heads = [d for d in decls if os.path.splitext(d[0])[1].lower() in (".h", ".hh", ".hpp", ".hxx")]
    primary = (heads or decls)[0]
    return {"struct": sname, "elem": elem, "size": n, "definition": "\n".join(lines), "decls": decls, "primary": primary[:2],
            "in_header": bool(heads), "n_files": len({d[0] for d in decls})}, ""


def _array_contract_text(ft):
    """배열 -> 구조체 요청의 계약을 sLLM 지침에 붙일 글로(요구사항 E3: 사용처 프롬프트에 고정 사실로 넣는다)."""
    if not ft.get("_definition"):
        return ""
    return (" 배열의 선언은 구조체로 바뀌었다. 새 정의: %s 배열 이름을 통째로 쓰던 곳(함수 인자, 포인터 연산)은 &%s 로 바꾸거나 "
            "필드별로 고친다." % (" ".join(ft["_definition"].split()), ft["target"]))


def _array_apply_plan(ctx, ft, plan, files):
    """계약대로 배열 선언을 구조체 선언으로 바꾸고, 배열 이름을 통째로 쓰는 곳을 처리한다. 돌려주는 값: (바꾼 선언 수, 통째로 쓰는 곳의 수)
      - 선언: 'int g[10];' -> 'G_T g;'. 구조체 정의는 첫 선언(헤더에 선언이 있으면 그 헤더의 것) 앞에 한 번 넣는다
      - 메모리 함수의 인자: memset(g, ...) -> memset(&g, ...)       - sizeof(g): 그대로 둔다
      - 그 밖(함수 인자, 포인터 연산 등): 요청의 llm_other_uses 가 참이면(기본) sLLM, 아니면 수동 검토
    모든 수정은 검토 대상으로 표시한다."""
    st, target = ctx.store, ft["target"]
    sql_i = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    sql_e = ("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
    title = "요청사항: 배열 %s 를 구조체 필드로" % target
    bare_rx = re.compile(r"(?<![\w.])%s\b(?!\s*\[)" % re.escape(target))
    n_conv = n_bare = 0
    for rel in files:
        d = get_doc(ctx, rel)
        if target not in d.code:
            continue
        taken = [(e["start"], e["end"]) for e in st.q("SELECT start, end FROM edits WHERE path=? AND status='accepted'", (rel,))]

        def put(s, e, after, what, note_manual):
            if any(a < e and s < b for a, b in taken):
                st.x(sql_i, (rel, s, e, d.line_of(s), "request", ft["id"], "T3", 0, "%s(%s)" % (title, what), d.text[s:e][:160], None, 0,
                             "manual", note_manual))
                return False
            cur = st.x(sql_i, (rel, s, e, d.line_of(s), "request", ft["id"], "T1", 0, "%s(%s)" % (title, what), d.text[s:e][:160], after,
                               0, "open", ""))
            st.x(sql_e, (rel, "snapshot", s, e, d.text[s:e], after, "request", ft["id"], str(cur.lastrowid), 1, "accepted", ""))
            taken.append((s, e))
            return True

        spans = [(s, e, g) for r, s, e, g in plan["decls"] if r == rel]
        for s, e, g in spans:
            new = "%s%s%s %s%s;" % (g["indent"], g["pre"] or "", plan["struct"], target, (" " + g["init"].strip()) if g["init"] else "")
            if (rel, s) == tuple(plan["primary"]):
                new = plan["definition"] + "\n" + new
            n_conv += 1 if put(s, e, new, "선언 교체", "배열 선언. 다른 수정과 범위가 겹쳐 바꾸지 못함") else 0
        for m in bare_rx.finditer(d.code):
            p = m.start()
            before = d.code[max(0, p - 240):p]
            if any(s <= p < e for s, e, _g in spans) or re.search(r"(?:->|::)\s*$", before) or re.search(r"\bsizeof\s*\(?\s*$", before):
                continue
            n_bare += 1
            if re.search(r"\b(?:%s)\s*\((?:[^;()]|\([^()]*\))*$" % _MEM_CALLS, before) and re.search(r"[(,]\s*$", before):
                if put(p, m.end(), "&" + target, "주소 연산자", "배열 이름을 통째로 쓰는 곳. 다른 수정과 범위가 겹쳐 바꾸지 못함"):
                    continue
            a, b = d.line_span(d.line_of(p))
            llm = 1 if ft.get("llm_other_uses", True) else 0
            st.x(sql_i, (rel, p, m.end(), d.line_of(p), "request", ft["id"], "T3", llm, "%s(배열 전체 사용)" % title, d.text[a:b].strip()[:160],
                         None, 0, "open" if llm else "manual",
                         "배열 이름을 통째로 쓰는 곳. sLLM 에 맡김(검토 필요)" if llm else "배열 이름을 통째로 쓰는 곳. 구조체에서는 & 를 붙이거나 필드별로 고쳐야 함"))
    return n_conv, n_bare


def _user_asks(ctx, files):
    """사용자 요청문을 처리한다(--ask, --ask-file, 요청 파일의 asks).
    요청마다 대상 이름이 쓰인 줄을 찾아 sLLM 대상 이슈로 넣는다. 고치는 방법은 요청문 그대로 지침으로 준다(_request_guidance).
    도구는 "어디를 볼지"만 정하고 "어떻게 고칠지"는 요청문과 sLLM 에 맡긴다. 그래서 결과는 모두 검토 대상이다.
    한 요청이 다루는 줄 수에는 상한(limits.ask_max_sites)이 있다. 넘는 곳은 수동 검토로 보낸다.
    돌려주는 값: 변경 계약 요약 목록(보고서의 "변경 계약"에 실린다)."""
    st, out = ctx.store, []
    cap = max(1, int(ctx.cfg["limits"].get("ask_max_sites", 300)))
    sql = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    for a in ctx.request.get("asks") or []:
        rxs = [re.compile(t[1:-1]) if t.startswith("/") else re.compile(r"(?<!\w)%s\b" % re.escape(t)) for t in a["targets"]]
        n_llm = n_over = n_files = 0
        for rel in files:
            pat = a.get("files") or ""
            if pat and not (fnmatch.fnmatch(rel.lower(), pat) or rel.lower().startswith(pat.rstrip("*").rstrip("/") + "/")):
                continue
            d = get_doc(ctx, rel)
            lines_done = set()
            for rx in rxs:
                for m in rx.finditer(d.code):
                    line = d.line_of(m.start())
                    if m.end() == m.start() or line in lines_done:
                        continue
                    lines_done.add(line)
                    over = n_llm >= cap
                    la, lb = d.line_span(line)
                    st.x(sql, (rel, m.start(), m.end(), line, "request", a["id"], "T3", 0 if over else 1,
                               "사용자 요청: %s" % a["text"][:140], d.text[la:lb].strip()[:160], None, 0, "manual" if over else "open",
                               ("한 요청이 다루는 줄의 상한(%d)을 넘음. 요청을 나누거나 파일 범위(@)를 준다" % cap) if over
                               else "사용자 요청문을 sLLM 에 맡김(검토 필요)"))
                    n_over += 1 if over else 0
                    n_llm += 0 if over else 1
            n_files += 1 if lines_done else 0
        LOG5.info("요청 %s (%s): 대상이 쓰인 줄 %d곳(파일 %d개)을 sLLM 에 맡김, 상한을 넘어 수동 검토로 보낸 곳 %d곳"
                  % (a["id"], ", ".join(a["targets"]), n_llm, n_files, n_over))
        if not n_llm:
            LOG5.warn("요청 %s: 대상 %s 을(를) 패키지에서 찾지 못했습니다. 이름과 파일 범위를 확인하세요" % (a["id"], ", ".join(a["targets"])))
        out.append({"id": a["id"], "type": "사용자 요청문", "target": ", ".join(a["targets"]), "rename": {},
                    "counts": {"sLLM 대상": n_llm, "상한 초과(수동)": n_over}, "note": "요청문: " + a["text"]})
    return out


def s5_impact(ctx):
    st = ctx.store
    st.x("DELETE FROM issues WHERE source='request'")
    st.x("DELETE FROM edits WHERE origin='request'")
    files = [r["path"] for r in st.q("SELECT path FROM files WHERE kind='source' AND status='대상' ORDER BY path")]
    contracts = []
    for ft in ctx.request.get("features", []):
        if ft["type"] == "struct_change":
            contracts.append(_struct_change(ctx, ft, files))
            continue
        if ft["type"] != "array_to_struct":
            LOG5.warn("요청 %s: 유형 %s 은(는) 이 판에서 자동 처리하지 않습니다. 수동 검토 목록에 올립니다"
                      % (ft["id"], ft["type"]))
            st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                 "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual','이 판에서 지원하지 않는 요청 유형')",
                 (ft["id"], "요청사항 %s" % ft["type"], json.dumps(ft, ensure_ascii=False)[:300]))
            continue
        target, mapping = ft["target"], ft["mapping"]
        rx = re.compile(r"\b%s\s*\[\s*([A-Za-z_]\w*|\d*)\s*\]" % re.escape(target))
        n_fix = n_var = n_decl = n_llm = n_conv = n_bare = 0
        plan, why = _array_plan(ctx, ft, files) if ft.get("convert_declaration", True) else (None, "요청에서 선언 교체를 끔")
        if plan:
            ft["_definition"] = plan["definition"]
        else:
            LOG5.info("요청 %s: 배열 선언은 수동 검토로 남깁니다(%s)" % (ft["id"], why))
        for rel in files:
            d = get_doc(ctx, rel)
            taken = [(e["start"], e["end"]) for e in st.q(
                "SELECT start, end FROM edits WHERE path=? AND status='accepted'", (rel,))]
            for m in rx.finditer(d.code):
                key = m.group(1)
                pre = d.code[max(0, m.start() - 80):m.start()]
                pm = re.search(r"([A-Za-z_]\w*)[\s\*&]+$", pre)
                is_decl = bool(pm) and pm.group(1) not in _NOT_TYPE_WORDS
                line = d.line_of(m.start())
                detail = d.text[m.start():m.end()]
                if is_decl:
                    if plan and any(x[0] == rel and x[1] <= m.start() < x[2] for x in plan["decls"]):
                        continue                      # 이 선언은 아래의 _array_apply_plan 이 통째로 바꾼다
                    tier, status, note, after = "T3", "manual", "배열 선언. 구조체 정의로 교체 필요(변경 계약): %s" % why, None
                    n_decl += 1
                elif key in mapping:
                    tier, status, note, after = "T1", "open", "", "%s.%s" % (target, mapping[key])
                    if any(s < m.end() and m.start() < e for s, e in taken):
                        status, note, after = "manual", "다른 수정과 범위가 겹침", None
                    n_fix += 1 if after else 0
                elif ft.get("llm_variable_index"):
                    tier, status, note, after = "T3", "open", "변수 인덱스 접근. sLLM 에 맡김(검토 필요)", None
                    n_llm += 1
                else:
                    tier, status, note, after = "T3", "manual", "변수 인덱스 접근. 매핑표로 결정할 수 없음", None
                    n_var += 1
                cur = st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
                           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (rel, m.start(), m.end(), line, "request", ft["id"], tier,
                            1 if (tier == "T3" and status == "open") else 0,
                            "요청사항: 배열 %s 를 구조체 필드로" % target, detail, after, 0, status, note))
                if after:
                    st.x("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rel, "snapshot", m.start(), m.end(), detail, after, "request", ft["id"],
                          str(cur.lastrowid), 1, "accepted", ""))
                    taken.append((m.start(), m.end()))
        if plan:
            n_conv, n_bare = _array_apply_plan(ctx, ft, plan, files)
            LOG5.info("요청 %s: 배열 선언 %d곳을 구조체 %s 로 바꿈(크기 %d, 요소 타입 %s). 배열 이름을 통째로 쓰는 곳 %d곳"
                      % (ft["id"], n_conv, plan["struct"], plan["size"], plan["elem"], n_bare))
        LOG5.info("요청 %s (배열 %s): 필드 치환 %d곳, 변수 인덱스 %d곳(수동) %d곳(sLLM), 선언 %d곳(수동)"
                  % (ft["id"], target, n_fix, n_var, n_llm, n_decl))
        contracts.append({"id": ft["id"], "type": "array_to_struct", "target": target, "rename": {},
                          "counts": {"필드 치환": n_fix, "변수 인덱스(수동)": n_var, "변수 인덱스(sLLM)": n_llm, "선언(수동)": n_decl,
                                     "선언 교체": n_conv, "배열 전체 사용": n_bare},
                          "definition_after": plan["definition"] if plan else "",
                          "note": (("구조체 정의를 %s 의 선언 앞에 넣었다. " % plan["primary"][0]) + ("" if plan["in_header"] or plan["n_files"] == 1
                                    else "선언이 여러 파일에 있는데 헤더가 아니다. 정의를 공용 헤더로 옮겨야 한다. ")
                                   + "매핑에 없는 인덱스는 reserved_번호 로 채워 크기와 배치를 배열과 같게 두었다") if plan
                                  else "배열 선언을 자동으로 바꾸지 못했다(%s). 선언과 구조체 정의는 사람이 한다" % why})
    contracts += _user_asks(ctx, files)
    st.set_meta("contracts", contracts)
    deps = {}
    for r in st.q("SELECT path, header FROM includes"):
        deps.setdefault(os.path.basename(norm_rel(r["header"])).lower(), []).append(r["path"])
    st.set_meta("dependents", {k: sorted(set(v)) for k, v in deps.items()})
    st.commit()
    LOG5.info("헤더 의존 관계: 헤더 %d종" % len(deps))


# ============================================================================
# [수정 레코드 공용 함수]  S6, S7, S8 이 같이 쓴다
# 좌표: 규칙/요청사항 수정은 스냅샷 본문 기준(base='snapshot'),
#       sLLM 수정은 규칙 수정을 반영한 본문 기준(base='post_rule')이다.
# ============================================================================
_LIVE = ("accepted", "applied", "verified")


def live_edits(ctx, rel, base):
    return ctx.store.q("SELECT * FROM edits WHERE path=? AND base=? AND status IN (?,?,?) ORDER BY start, end, id",
                       (rel, base) + _LIVE)


def apply_edits(text, edits):
    """겹치지 않는 수정 레코드를 본문에 적용한다. 돌려주는 값: (새 본문, 적용 못 한 (id, 사유) 목록)"""
    out, last, bad = [], 0, []
    for e in edits:
        if e["start"] < last:
            bad.append((e["id"], "다른 수정과 범위가 겹침"))
            continue
        if text[e["start"]:e["end"]] != e["before"]:
            bad.append((e["id"], "적용 위치의 원문이 기록과 다름"))
            continue
        out.append(text[last:e["start"]])
        out.append(e["after"])
        last = e["end"]
    out.append(text[last:])
    return "".join(out), bad


def shift_pos(pos, edits):
    """스냅샷 본문의 위치를 규칙 수정 반영 후 본문의 위치로 옮긴다. 수정된 범위 안이면 None."""
    delta = 0
    for e in edits:
        if e["end"] <= pos:
            delta += len(e["after"]) - (e["end"] - e["start"])
        elif e["start"] < pos:
            return None
        else:
            break
    return pos + delta


def reject_edits(ctx, ids_reasons):
    """수정 레코드를 기각하고, 연결된 이슈를 수동 검토로 돌린다."""
    st = ctx.store
    for eid, reason in ids_reasons:
        row = st.q("SELECT issue_ids FROM edits WHERE id=?", (eid,))
        st.x("UPDATE edits SET status='rejected', note=? WHERE id=?", (reason, eid))
        if row and row[0]["issue_ids"]:
            for iid in row[0]["issue_ids"].split(","):
                st.x("UPDATE issues SET status='manual', note=? WHERE id=?", ("수정 기각: " + reason, int(iid)))


# ============================================================================
# [S6 sLLM수정]  T3 이슈를 조각으로 잘라 sLLM 에 보내고 수정 블록을 검사한다
# 목적: 규칙으로 못 고친 이슈만 sLLM 에 맡긴다. 함수 전체가 아니라 "수정 지점 주변"만
#       보내므로 24k 한도와 함수 크기가 무관해진다. 큰 초기화 목록은 자리표시자로 바꾼다.
# 응답: 수정 블록(SEARCH/REPLACE) / NO_CHANGE / NEED_CONTEXT: 심볼
# 수용 검사: 형식, 원문 유일 일치, 대상 줄 근처, 자리표시자 미포함, 인코딩, 괄호 짝
# 산출: tasks, llm_calls, edits(origin='llm', base='post_rule'), logs/llm/
# 분할 순차: 한 번에 보낼 수 없는 구간(입력 한도 초과, 또는 예상 응답이 출력 상한 초과)은 문장 경계에서
#       나눠 앞 조각부터 차례로 보낸다. 뒤 조각에는 앞 조각에서 바꾼 내용을 붙인다. 한 조각이라도
#       실패하면 그 구간의 수정을 모두 취소한다(plan_chunks, _group_ledger, s6_llm 의 cancelled).
#       실제 sLLM 으로는 확인하지 못했다. 이 경로로 만든 수정은 모두 검토 대상이다.
# 토큰 한도 대응: (1) 보내기 직전에 크기를 다시 확인한다(보정값이 바뀌었거나 정의 보강으로 늘었을 수 있다)
#       (2) 서버가 한도 초과로 거절하면 서버가 알려 준 한도와 토큰 수를 반영한다 (3) 응답이 출력 상한에서
#       잘리면 수정 지점을 나눈다. 세 경우 모두 같은 요청을 다시 보내지 않고 더 작게 다시 계획한다(_replan).
#       작업 하나에 다시 계획은 2회까지. 그래도 안 되면 수동 검토로 보낸다.
# 이 구획의 함수(읽는 순서)
#   조각 만들기   build_view, _make_task, plan_tasks
#   분할          plan_chunks, _function_head, _group_ledger, _register_tasks
#   호출과 판정   s6_llm(차례 정하기, 병렬 호출, 결과 처리), _replan, _handle_response, check_block
# 수정: 프롬프트 문구는 SYSTEM_PROMPT/USER_TEMPLATE 또는 config/prompts/*.txt 에서 바꾼다.
# ============================================================================
LOG6 = Log("S6.sLLM수정")
ELIDE_MARK = "/*@@ELIDED"
_SR_RE = re.compile(r"<{5,}[ \t]*SEARCH[ \t]*\r?\n(.*?)\r?\n={5,}[ \t]*\r?\n(.*?)(?:\r?\n)?>{5,}[ \t]*REPLACE", re.S)
_NEED_RE = re.compile(r"NEED_CONTEXT\s*:\s*([A-Za-z_~][\w:~]*)")
_ALLOW_MARGIN_LINES = 10

SYSTEM_PROMPT = """당신은 C++ 레거시 코드를 새 컴파일러 환경에 맞게 최소한으로 고치는 도구다.
규칙:
1. [수정 지침]에 해당하는 부분만 고친다. 그 밖의 코드, 주석, 공백, 이름은 바꾸지 않는다.
2. 출력은 아래 세 가지 중 하나만 쓴다. 설명 문장은 쓰지 않는다.
(가) 수정이 필요하면 수정 블록을 하나 이상 쓴다.
<<<<<<< SEARCH
[코드 조각]에서 그대로 복사한 줄(조각 안에서 한 번만 나오는 범위)
=======
바꾼 내용
>>>>>>> REPLACE
(나) 수정이 필요 없으면 NO_CHANGE 한 줄만 쓴다.
(다) 판단에 필요한 정의가 없으면 NEED_CONTEXT: 심볼이름 한 줄만 쓴다.
3. SEARCH 에는 [코드 조각]의 줄을 한 글자도 바꾸지 않고 복사한다.
4. /*@@ELIDED 로 시작하는 표식이 있는 줄은 SEARCH 와 REPLACE 에 넣지 않는다."""

USER_TEMPLATE = """[환경]
{env}

[수정 지침]
{guidance}

[수정 대상 줄] (코드 조각 안의 줄 번호와 내용)
{targets}
{context}{extra}{feedback}
[코드 조각] 파일 {path}, 원본 {start_line}~{end_line}줄
-----BEGIN CODE-----
{code}
-----END CODE-----"""


def _load_prompt(name, default):
    p = os.path.join(app_dir(), "config", "prompts", name)
    if os.path.isfile(p):
        with open(p, "r", encoding="utf-8-sig") as f:
            LOG6.info("프롬프트 파일 사용: %s" % p)
            return f.read()
    return default


def build_view(text, a, b, big_spans):
    """text[a:b] 에서 큰 초기화 목록을 자리표시자로 바꾼 '보기'를 만든다.
    돌려주는 값: (보기 본문, [(보기 시작, 본문 시작, 길이)] 원문 그대로인 구간 목록)"""
    parts, segs, vpos, cur, k = [], [], 0, a, 0
    for s, e in sorted(big_spans):
        if e <= a or s >= b:
            continue
        s2, e2 = max(s, a), min(e, b)
        if s2 > cur:
            seg = text[cur:s2]
            segs.append((vpos, cur, len(seg)))
            parts.append(seg)
            vpos += len(seg)
        k += 1
        ph = "{ %s#%d: 초기값 %d자 생략@@*/ }" % (ELIDE_MARK, k, e - s)
        parts.append(ph)
        vpos += len(ph)
        cur = max(cur, e2)
    if cur < b:
        seg = text[cur:b]
        segs.append((vpos, cur, len(seg)))
        parts.append(seg)
    return "".join(parts), segs


def view_to_text(segs, vs, ve):
    for v0, t0, n in segs:
        if v0 <= vs and ve <= v0 + n:
            return t0 + (vs - v0), t0 + (ve - v0)
    return None


def _fuzzy_find(view, search):
    """줄 끝 공백 차이만 무시하고 찾는다. 유일하게 일치할 때만 (시작, 끝)."""
    vlines = view.split("\n")
    slines = [l.rstrip() for l in search.split("\n")]
    while slines and not slines[-1]:
        slines.pop()
    while slines and not slines[0]:
        slines.pop(0)
    if not slines:
        return None
    vr = [l.rstrip() for l in vlines]
    n = len(slines)
    hits = [i for i in range(len(vr) - n + 1) if vr[i:i + n] == slines]
    if len(hits) != 1:
        return None
    i = hits[0]
    start = sum(len(l) + 1 for l in vlines[:i])
    end = start + sum(len(l) + 1 for l in vlines[i:i + n]) - 1
    return start, end


def _line_starts(text):
    return [0] + [m.end() for m in re.finditer("\n", text)]


def check_block(task, search, replace):
    """수정 블록 하나의 수용 검사. 돌려주는 값: (수정 dict 또는 None, 기각 사유 또는 '')"""
    view, text1 = task["view"], task["text1"]
    search, replace = search.replace("\r\n", "\n"), replace.replace("\r\n", "\n")
    if ELIDE_MARK in search or ELIDE_MARK in replace:
        return None, "생략 표식이 있는 줄을 건드림"
    if not search.strip():
        return None, "SEARCH 가 비어 있음"
    n = view.count(search)
    if n == 1:
        vs = view.find(search)
        ve = vs + len(search)
    elif n > 1:
        return None, "SEARCH 가 코드 조각에서 %d번 일치함(한 번만 일치해야 함)" % n
    else:
        span = _fuzzy_find(view, search)
        if not span:
            return None, "SEARCH 가 코드 조각의 원문과 일치하지 않음"
        vs, ve = span
    span = view_to_text(task["segs"], vs, ve)
    if not span:
        return None, "생략 구간에 걸친 수정"
    ts, te = span
    before = text1[ts:te]
    if before == replace:
        return None, ""                                   # 바뀐 것이 없는 블록은 조용히 버린다
    l1 = bisect.bisect_right(task["ls1"], ts)
    l2 = bisect.bisect_right(task["ls1"], max(ts, te - 1))
    if l2 < task["t_first"] - _ALLOW_MARGIN_LINES or l1 > task["t_last"] + _ALLOW_MARGIN_LINES:
        return None, "수정 대상 줄에서 벗어난 곳을 고침"
    try:
        replace.encode(task["codec"])
    except UnicodeEncodeError:
        return None, "원본 파일의 인코딩으로 표현할 수 없는 문자가 있음"
    if balance_of(mask_preprocessor(mask_code(replace)[0])) != balance_of(mask_preprocessor(mask_code(before)[0])):
        return None, "괄호 짝이 원문과 달라짐"
    return {"start": ts, "end": te, "before": before, "after": replace}, ""


def find_symbol_snippet(ctx, name):
    """NEED_CONTEXT 로 요청된 심볼의 정의를 찾아 앞부분을 돌려준다(없으면 빈 문자열)."""
    st = ctx.store
    short = name.split("::")[-1]
    rows = st.q("SELECT path, sig_start, end FROM functions WHERE name=? OR name LIKE ? LIMIT 1", (name, "%::" + short))
    if rows:
        d = get_doc(ctx, rows[0]["path"])
        return "// %s\n%s" % (rows[0]["path"], d.text[rows[0]["sig_start"]:min(rows[0]["end"], rows[0]["sig_start"] + 1500)])
    rx = re.compile(r"^[ \t]*(?:#[ \t]*define[ \t]+%s\b.*|(?:typedef\b[^;]*\b%s[ \t]*;)|(?:struct|class|union|enum)[ \t]+%s\b[^;]*)"
                    % ((re.escape(short),) * 3), re.M)
    for r in st.q("SELECT path FROM files WHERE kind='source' AND status IN ('대상','읽기전용','범위제외') ORDER BY path"):
        d = get_doc(ctx, r["path"])
        m = rx.search(d.code)
        if m:
            return "// %s\n%s" % (r["path"], d.text[m.start():m.start() + 1200])
    return ""


def _mark_manual(ctx, issue_ids, note):
    for iid in issue_ids:
        ctx.store.x("UPDATE issues SET status='manual', note=? WHERE id=?", (note, iid))


def _request_guidance(ctx, rid):
    """요청사항 이슈에 붙일 수정 지침(매핑표 포함). 규칙 카탈로그에 없는 ID 일 때 쓴다."""
    for a in ctx.request.get("asks") or []:
        if a["id"] == rid:                       # 사용자 요청문: 요청문을 그대로 지침으로 준다
            return ("사용자 요청: %s 대상은 %s 이다. 이 조각에서 대상이 쓰인 줄을 요청대로 고친다. 요청과 관계없는 줄이거나 "
                    "고칠 필요가 없으면 그 줄은 그대로 둔다. 고칠 줄이 하나도 없으면 NO_CHANGE 를 쓴다."
                    % (a["text"] if a["text"].endswith((".", "다", "요")) else a["text"] + ".", ", ".join(a["targets"])))
    for ft in ctx.request.get("features", []):
        if ft.get("id") == rid and ft.get("type") == "array_to_struct":
            pairs = ", ".join("%s[%s] -> %s.%s" % (ft["target"], k, ft["target"], v)
                              for k, v in list(ft["mapping"].items())[:40])
            return ("배열 %s 은(는) 구조체로 바뀐다. 대응: %s. 상수 인덱스 접근은 이미 필드로 바꿨다. "
                    "변수 인덱스로 접근하는 줄을 인덱스가 가리키는 요소의 필드 접근으로 고친다. "
                    "어느 필드인지 정할 수 없으면 NO_CHANGE 를 쓴다." % (ft["target"], pairs)) + _array_contract_text(ft)
        if ft.get("id") == rid and ft.get("type") == "struct_change":
            pairs = ", ".join("%s -> %s" % kv for kv in list((ft.get("rename") or {}).items())[:40])
            return ("구조체 %s 의 필드 이름이 바뀐다: %s. 이 줄의 멤버 접근이 그 구조체의 것이면 새 이름으로 고친다. "
                    "그 구조체의 것이 아니거나 알 수 없으면 NO_CHANGE 를 쓴다." % (ft["target"], pairs))
    return ""


def _common_guidance(ctx):
    """요청 파일의 공통 요청(common)을 [수정 지침] 뒤에 붙일 줄 목록으로 만든다."""
    items = [str(x).strip() for x in (ctx.request.get("common") or []) if str(x).strip()]
    if not items:
        return []
    return ["", "[공통 요청] 이 작업의 모든 수정에 적용한다."] + ["- " + x for x in items]


def _make_task(rel, d, text1, ls1, big, members, la, lb, fn, guidance, env_txt, note_ctx=""):
    """본문 text1 의 la~lb 줄을 조각으로 하는 작업 하나를 만든다. members 는 (줄 번호, 이슈) 목록.
    plan 에는 이 작업을 더 작게 다시 만들 때(_replan) 필요한 재료를 넣어 둔다."""
    a = ls1[la - 1]
    b = ls1[lb] - 1 if lb < len(ls1) else len(text1)
    view, segs = build_view(text1, a, b, big)
    vls = _line_starts(view)
    targets = []
    for line, i in members:
        tp = ls1[line - 1]
        vpos = None
        for v0, t0, n in segs:
            if t0 <= tp <= t0 + n:
                vpos = v0 + (tp - t0)
                break
        if vpos is None:
            continue
        vline = bisect.bisect_right(vls, vpos)
        ve = vls[vline] - 1 if vline < len(vls) else len(view)
        targets.append("- %d번째 줄 [%s]: %s" % (vline, i["rule_id"], view[vls[vline - 1]:ve].strip()[:200]))
    context = note_ctx
    if fn and fn[0]["start_line"] < la:
        context += "\n[참고: 이 조각이 속한 함수]\n%s\n" % d.text[fn[0]["sig_start"]:fn[0]["body_start"]].strip()[:600]
    return {"path": rel, "issue_ids": [i["id"] for _l, i in members], "text1": text1, "ls1": ls1, "segs": segs,
            "view": view, "codec": d.codec, "t_first": members[0][0], "t_last": members[-1][0], "la": la, "lb": lb,
            "fmt": {"env": env_txt, "guidance": "\n".join(guidance), "targets": "\n".join(targets),
                    "context": context, "path": rel, "start_line": la, "end_line": lb, "code": view},
            "plan": {"d": d, "big": big, "fn": fn, "guidance": guidance, "env": env_txt, "members": list(members)},
            "extra": "", "feedback": "", "attempts": 0, "ctx_rounds": 0, "over": 0}


def _function_head(ctx, rel, d, fn, lim):
    """분할 조각에 참고로 붙일 함수 앞부분(지역 변수 선언이 모여 있는 곳).
    돌려주는 값: (앞부분의 끝 줄 번호, 본문) 또는 None"""
    n = int(lim.get("chunk_head_lines", 25))
    if not fn or n <= 0:
        return None
    ls0 = _line_starts(d.text)
    l0 = bisect.bisect_right(ls0, fn[0]["body_start"])
    l1 = min(len(ls0), l0 + n)
    a = ls0[l0 - 1]
    b = ls0[l1] - 1 if l1 < len(ls0) else len(d.text)
    big0 = [(r["start"], r["end"]) for r in ctx.store.q("SELECT start, end FROM big_inits WHERE path=?", (rel,))]
    view, _segs = build_view(d.text, a, b, big0)
    return l1, view[:1500]


def plan_chunks(ctx, rel, d, text1, ls1, big, cl, fn, guidance, env_txt, max_targets=0, la_min=0):
    """분할 순차: 한 번에 보낼 수 없는 구간을 문장 경계에서 나눠 조각 작업 목록을 만든다(나눌 수 없으면 None).
    원칙: (1) 문장 중간에서 자르지 않는다 (2) 조각마다 입력과 예상 출력이 한도 안에 든다
          (3) 수정 지점이 없는 조각은 보내지 않는다 (4) 조각은 앞에서부터 순서대로 처리한다(s6_llm)
    max_targets: 조각 하나에 넣을 수정 지점의 최대 수(0 은 제한 없음). 응답이 출력 상한에서 잘렸을 때 쓴다.
    la_min: 첫 조각이 이 줄보다 앞에서 시작하지 않게 한다. 분할 묶음의 남은 부분만 다시 나눌 때, 이미 끝난
            앞 조각의 줄과 겹치지 않게 하기 위한 것이다(겹치면 같은 줄을 두 번 고치게 된다).
    크기 계산에는 실행 중 배운 보정 비율과 서버 몫(overhead_tokens)을 반영한다."""
    lim, llm = ctx.cfg["limits"], ctx.cfg["llm"]
    la0, lb0 = max(1, cl[0][0] - 3, la_min), min(len(ls1), cl[-1][0] + 3)
    code1 = mask_preprocessor(mask_code(text1)[0])
    ratio = float(llm.get("_ratio", 1.0))

    def tok(s):
        return max(0, int((est_tokens(s) - 8) * ratio))

    # 1) 조각을 시작할 수 있는 줄: 그 앞의 마지막 코드 문자가 ; { } 이고 소괄호가 열려 있지 않은 줄
    bounds, depth, prev = [], 0, ";"
    for ln in range(la0, lb0 + 1):
        if ln > la0:
            seg = code1[ls1[ln - 2]:ls1[ln - 1]]
            depth += seg.count("(") - seg.count(")")
            s = seg.rstrip()
            if s:
                prev = s[-1]
        p = ls1[ln - 1]
        if ln == la0 or (depth <= 0 and prev in ";{}" and not any(s0 < p <= e0 for s0, e0 in big)):
            bounds.append(ln)

    # 2) 경계 사이 구간별 크기: 입력 토큰 추정, 예상 출력 토큰, 그 안의 수정 지점
    segs = []
    for k, la in enumerate(bounds):
        lb = (bounds[k + 1] - 1) if k + 1 < len(bounds) else lb0
        a = ls1[la - 1]
        b = ls1[lb] - 1 if lb < len(ls1) else len(text1)
        view, _s = build_view(text1, a, b, big)
        mem = [(l, i) for l, i in cl if la <= l <= lb]
        t_in, t_out = tok(view), 0
        for l, _i in mem:
            lt = tok(text1[ls1[l - 1]:(ls1[l] if l < len(ls1) else len(text1))])
            t_in += lt + 12                       # 수정 대상 줄 목록에도 한 번 더 들어간다
            t_out += 2 * lt + 30                  # 수정 블록은 원문 줄과 바꾼 줄을 모두 쓴다
        segs.append((la, lb, t_in, t_out, mem))
    fixed = count_tokens(llm, ctx.system_prompt + ctx.user_template.format(
        extra="", feedback="", env=env_txt, guidance="\n".join(guidance), targets="", context="", path=rel,
        start_line=0, end_line=0, code=""))
    reserve = tok("x" * int(lim.get("chunk_ledger_chars", 1500))) + 700     # 앞 조각 변경 내용 + 머리 정보 + 기각 사유
    room = llm["max_total_tokens"] - llm["max_output_tokens"] - overhead_tokens(llm)
    in_budget = int(room / 1.1) - fixed - reserve
    out_budget = int(llm["max_output_tokens"] * 0.8)
    head = _function_head(ctx, rel, d, fn, lim)
    if head:
        head_tok = tok(head[1]) + 40
        if in_budget - head_tok < max(300, in_budget // 3):
            head = None                           # 함수 앞부분을 붙이면 코드를 넣을 자리가 너무 줄어든다. 붙이지 않는다
        else:
            in_budget -= head_tok

    for _try in range(3):
        if in_budget < 150:
            return None
        chunks, cur = [], None
        for la, lb, t_in, t_out, mem in segs:
            if t_in > in_budget or t_out > out_budget:
                return None                       # 경계 하나 사이가 한도를 넘는다. 나눌 수 없는 구간
            if (cur and cur[2] + t_in <= in_budget and cur[3] + t_out <= out_budget
                    and (not max_targets or not mem or len(cur[4]) + len(mem) <= max_targets)):
                cur[1], cur[2], cur[3], cur[4] = lb, cur[2] + t_in, cur[3] + t_out, cur[4] + mem
            else:
                if cur:
                    chunks.append(cur)
                cur = [la, lb, t_in, t_out, list(mem)]
        if cur:
            chunks.append(cur)
        chunks = [c for c in chunks if c[4]]
        if not chunks:
            return None
        # 3) 조각 작업을 만들고 실제 프롬프트 크기를 다시 확인한다
        tasks, fits = [], True
        for k, (la, lb, _ti, _to, mem) in enumerate(chunks, 1):
            note = ""
            if len(chunks) > 1:
                note = ("\n[분할 처리] 한 구간을 %d조각으로 나눈 것 중 %d번째다. 조각 밖의 코드는 보이지 않아도 "
                        "이어져 있다. 이 조각 안의 수정 대상 줄만 고친다.\n" % (len(chunks), k))
                if head and la > head[0]:
                    note += ("\n[참고: 함수 앞부분(수정 전 원본). 지역 변수 선언을 보기 위한 것이며 고치지 않는다]\n%s\n"
                             % head[1])
            t = _make_task(rel, d, text1, ls1, big, mem, la, lb, fn, guidance, env_txt, note)
            need = count_tokens(llm, ctx.system_prompt + ctx.user_template.format(extra="", feedback="", **t["fmt"]))
            if not fits_limit(llm, need + reserve - 400):
                fits = False
                break
            t["est_in"] = need
            tasks.append(t)
        if fits:
            return tasks
        in_budget = int(in_budget * 0.8)          # 추정이 빗나갔으면 조각을 더 작게 잡아 다시 나눈다
    return None


def _group_ledger(ctx, members, t):
    """분할 순차: 앞 조각에서 수용된 수정을 '원문 => 바뀐 내용' 목록으로 요약한다(글자 수 제한)."""
    cap = int(ctx.cfg["limits"].get("chunk_ledger_chars", 1500))
    refs = ["task%d" % x["id"] for x in members if x["part"] < t["part"]]
    if not refs or cap <= 0:
        return ""
    rows = ctx.store.q("SELECT before, after FROM edits WHERE origin='llm' AND status='accepted' AND ref IN (%s) "
                       "ORDER BY start" % ",".join("?" * len(refs)), refs)
    lines, used, seen = [], 0, set()
    for n, r in enumerate(rows):
        item = "- %s  =>  %s" % (" ".join(r["before"].split())[:120], " ".join(r["after"].split())[:160])
        if item in seen:
            continue
        if used + len(item) > cap:
            lines.append("- (이하 %d건 생략)" % (len(rows) - n))
            break
        seen.add(item)
        lines.append(item)
        used += len(item) + 1
    return "\n".join(lines)


def _est_out(task):
    """이 작업의 응답(수정 블록)이 차지할 토큰 수의 추정. 수정 블록은 원문 줄과 바꾼 줄을 모두 쓴다."""
    return sum(2 * est_tokens(x) + 30 for x in task["fmt"]["targets"].split("\n") if x)


def _register_tasks(ctx, new, groups=None, gid=0, done_n=0):
    """작업 목록을 tasks 표에 넣고 번호를 붙인다. 여러 조각이면(또는 이미 분할 묶음이면) 묶음 번호도 붙인다.
    done_n 은 같은 묶음에서 이미 지나간 조각 수(다시 계획할 때). 돌려주는 값: 묶음 번호(묶음이 아니면 0)."""
    st = ctx.store
    multi = bool(gid) or len(new) > 1
    total = done_n + len(new)
    for k, t in enumerate(new, 1):
        cur = st.x("INSERT INTO tasks(path,start_line,end_line,issue_ids,status,attempts,note,grp,part,parts) "
                   "VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (t["path"], t["la"], t["lb"], ",".join(str(x) for x in t["issue_ids"]), "pending", 0, "",
                    0, done_n + k if multi else None, total if multi else None))
        t["id"] = cur.lastrowid
        if multi:
            gid = gid or t["id"]
            t["group"], t["part"], t["parts"] = gid, done_n + k, total
            st.x("UPDATE tasks SET grp=? WHERE id=?", (gid, t["id"]))
    if multi and groups is not None:
        keep = [x for x in groups.get(gid, []) if x["part"] <= done_n]
        for x in keep:
            x["parts"] = total
            st.x("UPDATE tasks SET parts=? WHERE id=?", (total, x["id"]))
        groups[gid] = keep + new
    return gid if multi else 0


def plan_tasks(ctx):
    """T3(llm) 이슈를 파일별로 모아, 가까운 것끼리 한 작업으로 묶고 프롬프트에 넣을 조각을 만든다."""
    st, lim, llm = ctx.store, ctx.cfg["limits"], ctx.cfg["llm"]
    env_txt = "\n".join("- " + s for s in env_summary_lines(ctx.env))
    common = _common_guidance(ctx)
    tasks = []
    by_file = {}
    for r in st.q("SELECT * FROM issues WHERE tier='T3' AND llm=1 AND status='open' ORDER BY path, start"):
        by_file.setdefault(r["path"], []).append(r)
    for rel, issues in by_file.items():
        d = get_doc(ctx, rel)
        ids_all = [i["id"] for i in issues]
        if d.eol == "mixed":
            _mark_manual(ctx, ids_all, "줄끝이 섞인 파일은 sLLM 수정을 하지 않음")
            continue
        e0 = live_edits(ctx, rel, "snapshot")
        text1, _bad = apply_edits(d.text, e0)
        ls1 = _line_starts(text1)
        big = []
        for b in st.q("SELECT start, end FROM big_inits WHERE path=?", (rel,)):
            s2, e2 = shift_pos(b["start"], e0), shift_pos(b["end"], e0)
            if s2 is not None and e2 is not None:
                big.append((s2, e2))
        located = []
        for i in issues:
            p = shift_pos(i["start"], e0)
            if p is None:
                st.x("UPDATE issues SET status='skipped', note='규칙 수정 범위에 포함됨' WHERE id=?", (i["id"],))
                continue
            located.append((bisect.bisect_right(ls1, p), i))
        clusters = []
        for line, i in located:
            if clusters and line - clusters[-1][-1][0] <= lim["cluster_gap_lines"]:
                clusters[-1].append((line, i))
            else:
                clusters.append([(line, i)])
        for cl in clusters:
            t_first, t_last = cl[0][0], cl[-1][0]
            ids = [i["id"] for _l, i in cl]
            guidance = []
            for _l, i in cl:
                g = (ctx.rule_by_id.get(i["rule_id"], {}).get("guidance", "") or _request_guidance(ctx, i["rule_id"])
                     or _build_guidance(i))
                item = "- [%s] %s: %s" % (i["rule_id"], i["title"], g)
                if item not in guidance:
                    guidance.append(item)
            guidance += common
            fn = st.q("SELECT name, sig_start, body_start, start_line FROM functions WHERE path=? AND sig_start<=? AND end>? "
                      "ORDER BY sig_start DESC LIMIT 1", (rel, cl[0][1]["start"], cl[0][1]["start"]))
            task = None
            ctx_lines = lim["slice_context_lines"]
            for attempt_ctx in (ctx_lines, ctx_lines // 2, 5, 0):     # 주변 줄을 줄여 가며 한도에 맞춘다
                la, lb = max(1, t_first - attempt_ctx), min(len(ls1), t_last + attempt_ctx)
                task = _make_task(rel, d, text1, ls1, big, cl, la, lb, fn, guidance, env_txt)
                need = count_tokens(llm, ctx.system_prompt + ctx.user_template.format(extra="", feedback="", **task["fmt"]))
                if fits_limit(llm, need):
                    task["est_in"] = need
                    break
                task = None
            if task is not None and _est_out(task) > llm["max_output_tokens"] * 0.8:
                task = None                        # 응답이 출력 상한을 넘을 것 같으면 나눠 보낸다
            if task is None:
                parts = None
                if lim.get("chunk_enabled", True):
                    parts = plan_chunks(ctx, rel, d, text1, ls1, big, cl, fn, guidance, env_txt)
                if not parts:
                    why = ("조각이 토큰 한도를 넘고, 나눌 수 있는 문장 경계가 없음" if lim.get("chunk_enabled", True)
                           else "조각이 토큰 한도를 넘음(분할 순차를 끔)")
                    _mark_manual(ctx, ids, why)
                    LOG6.warn("%s %d~%d줄: 토큰 한도를 넘고 나눌 수 없어 수동 검토로 보냅니다" % (rel, t_first, t_last))
                    continue
                _register_tasks(ctx, parts)
                tasks.extend(parts)
                if len(parts) > 1:
                    LOG6.info("%s %d~%d줄: 한 번에 보낼 수 없어 %d조각으로 나눠 순서대로 처리합니다(분할 순차)"
                              % (rel, t_first, t_last, len(parts)))
                continue
            _register_tasks(ctx, [task])
            tasks.append(task)
    st.commit()
    return tasks


def _task_prompt(ctx, task):
    extra = ("\n[추가 정의]\n%s\n" % task["extra"]) if task["extra"] else ""
    fb = ("\n[이전 응답이 기각된 이유] 아래 문제를 고쳐 다시 답한다.\n%s\n" % task["feedback"]) if task["feedback"] else ""
    if task.get("ledger"):
        extra += "\n[앞 조각에서 바꾼 내용] 같은 이름과 방식으로 일관되게 고친다.\n%s\n" % task["ledger"]
    return ctx.user_template.format(extra=extra, feedback=fb, **task["fmt"])


def _replan(ctx, t, groups, why, split=False):
    """토큰 한도에 걸린 작업을 더 작게 다시 만든다. 같은 요청을 그대로 다시 보내지 않기 위한 것이다.
    일반 작업: 주변 줄을 줄여 보고, 안 되면 문장 경계에서 나눈다(분할 순차).
    분할 조각: 그 조각부터 남은 조각 전부를 지금의 보정값으로 다시 나눈다(앞에서 끝난 조각은 그대로 둔다).
    split=True(응답이 출력 상한에서 잘린 경우): 주변 줄을 줄여도 소용없으므로 수정 지점을 절반씩 나눈다.
    돌려주는 값: (대체 작업 목록, 대체되어 사라지는 옛 작업 번호 집합) 또는 None(더 줄일 수 없음)"""
    st, llm, lim = ctx.store, ctx.cfg["llm"], ctx.cfg["limits"]
    P = t["plan"]
    gid = t.get("group") or 0
    if gid:
        rest = [x for x in groups[gid] if x["part"] >= t["part"]]
        members = [m for x in rest for m in x["plan"]["members"]]
        done_n = t["part"] - 1
    else:
        rest, members, done_n = [t], P["members"], 0
    new = None
    if not gid and not split:
        for c in (lim["slice_context_lines"] // 2, 5, 0):
            la, lb = max(1, members[0][0] - c), min(len(t["ls1"]), members[-1][0] + c)
            if lb - la >= t["lb"] - t["la"]:
                continue                           # 지금보다 작아지지 않는 범위는 건너뛴다
            nt = _make_task(t["path"], P["d"], t["text1"], t["ls1"], P["big"], members, la, lb, P["fn"],
                            P["guidance"], P["env"])
            nt["extra"], nt["ctx_rounds"] = t["extra"], t["ctx_rounds"]      # 보강해 둔 정의는 가져간다
            need = count_tokens(llm, ctx.system_prompt + _task_prompt(ctx, nt))
            if fits_limit(llm, need) and _est_out(nt) <= llm["max_output_tokens"] * 0.8:
                nt["est_in"] = need
                new = [nt]
                break
    if new is None:
        if not lim.get("chunk_enabled", True):
            return None
        max_targets = 0
        if split:
            max_targets = len(P["members"]) // 2
            if max_targets < 1:
                return None                        # 수정 지점이 하나뿐이면 더 나눌 수 없다
        new = plan_chunks(ctx, t["path"], P["d"], t["text1"], t["ls1"], P["big"], members, P["fn"], P["guidance"],
                          P["env"], max_targets=max_targets, la_min=t["la"] if gid else 0)
        if not new:
            return None
        if new[0]["la"] <= t["la"] and new[0]["lb"] >= t["lb"] and len(new[0]["issue_ids"]) >= len(t["issue_ids"]):
            return None                            # 나눠도 방금 보낸 것보다 작아지지 않는다
    for x in rest:
        st.x("UPDATE tasks SET status='replanned', note=? WHERE id=?", ("다시 계획함: " + why, x["id"]))
    _register_tasks(ctx, new, groups, gid, done_n)
    for nt in new:
        nt["over"] = t["over"]
    return new, {x["id"] for x in rest}


def s6_llm(ctx):
    st, llm = ctx.store, ctx.cfg["llm"]
    for tbl in ("tasks", "llm_calls"):
        st.x("DELETE FROM %s" % tbl)
    st.x("DELETE FROM edits WHERE origin='llm'")
    ctx.system_prompt = _load_prompt("system.txt", SYSTEM_PROMPT)
    ctx.user_template = _load_prompt("user.txt", USER_TEMPLATE)
    n_open = st.one("SELECT COUNT(*) FROM issues WHERE tier='T3' AND llm=1 AND status='open'")
    if not n_open:
        LOG6.info("sLLM 대상 이슈가 없습니다")
        return
    skip = ""
    if ctx.opts.get("no_llm"):
        skip = "sLLM 단계를 건너뜀(--no-llm)"
    elif not llm.get("endpoint"):
        skip = "sLLM 주소 미설정"
    if skip:
        LOG6.warn("%s. 대상 이슈 %d건을 수동 검토로 보냅니다" % (skip, n_open))
        st.x("UPDATE issues SET status='manual', note=? WHERE tier='T3' AND llm=1 AND status='open'", (skip,))
        st.commit()
        return

    # --- 한도 확인: 서버가 알려 주는 값이 설정보다 작으면 서버 값을 쓴다 ---
    cfg_limit = llm["max_total_tokens"]
    srv_limit, srv_models = llm_probe(llm)
    if srv_models and llm["model"] not in srv_models:
        LOG6.warn("서버의 모델 목록에 설정한 모델 이름(%s)이 없습니다. 서버 목록: %s" % (llm["model"], ", ".join(srv_models[:5])))
    if srv_limit and srv_limit < llm["max_total_tokens"]:
        LOG6.info("서버가 알려 준 토큰 한도 %d 을(를) 씁니다(설정값 %d)" % (srv_limit, llm["max_total_tokens"]))
        llm["max_total_tokens"] = srv_limit
    info = llm.get("_server_info") or {}          # 서버가 알려 준 허용 동시 수, 예약량, 출력 상한(있을 때만)
    if info.get("max_parallel") and info["max_parallel"] < llm["max_parallel"]:
        LOG6.info("서버가 알려 준 허용 동시 수 %d 을(를) 씁니다(설정값 %d)" % (info["max_parallel"], llm["max_parallel"]))
        llm["max_parallel"] = max(1, info["max_parallel"])
    if info.get("reserve_tokens", 0) > int(llm.get("server_prompt_reserve_tokens", 0) or 0):
        LOG6.info("서버가 알려 준 예약량 %d토큰을 씁니다" % info["reserve_tokens"])
        llm["server_prompt_reserve_tokens"] = info["reserve_tokens"]
    if info.get("max_output_tokens") and info["max_output_tokens"] < llm["max_output_tokens"]:
        llm["max_output_tokens"] = info["max_output_tokens"]
    saved = st.get_meta("llm_tokens", None)       # 이어서 실행할 때: 앞에서 배운 보정값을 다시 쓴다
    if saved and saved.get("model") == llm["model"]:
        llm["_tok_hist"] = [tuple(x) for x in saved.get("hist") or []]
        llm["_tok_floor"] = [tuple(x) for x in saved.get("floor") or []]
        a, b = _refit_tokens(llm)
        llm["_ratio"], llm["_overhead"] = round(a, 4), int(b)

    tasks = plan_tasks(ctx)
    LOG6.info("sLLM 작업 %d건 (이슈 %d건), 동시 요청 %d, 한도 %d토큰(출력 상한 %d, 서버 몫 %d)"
              % (len(tasks), n_open, llm["max_parallel"], llm["max_total_tokens"], llm["max_output_tokens"],
                 overhead_tokens(llm)))
    os.makedirs(ctx.p("logs", "llm"), exist_ok=True)
    pending, conn_fail, any_ok, n_replan, n_reject = tasks, 0, False, 0, 0
    groups = {}                                   # 분할 묶음: 묶음 번호 -> 조각 작업 목록
    for t in tasks:
        if t.get("group"):
            groups.setdefault(t["group"], []).append(t)
    seen_groups, cancelled = set(groups), {}      # cancelled: 묶음 번호 -> 실패한 조각 번호
    round_no = 0
    while pending:
        round_no += 1
        head = {}                                 # 분할 묶음마다 지금 보낼 차례인 조각 번호
        for t in pending:
            if t.get("group"):
                head[t["group"]] = min(head.get(t["group"], t["part"]), t["part"])
        ready = [t for t in pending if not t.get("group") or t["part"] == head[t["group"]]]
        if round_no == 1 and len(ready) > 1 and llm.get("calibrate_first", True) and not llm.get("_tok_hist"):
            # 첫 요청 1건(가장 큰 것)으로 실제 토큰 수를 확인한 뒤 나머지를 보낸다. 추정이 어긋나 있을 때
            # 여러 건이 한꺼번에 거절되는 것을 막는다
            ready = [max(ready, key=lambda x: x.get("est_in", 0))]
            LOG6.info("첫 요청 1건으로 실제 토큰 수를 확인한 뒤 나머지를 보냅니다(작업 %d)" % ready[0]["id"])
        ready_ids = {t["id"] for t in ready}
        swap, drop, failed = {}, set(), []        # 다시 계획한 결과 / 사라지는 옛 작업 / 더 줄일 수 없는 작업

        def over_limit(t, why, split=False):
            """작업 t 를 더 작게 다시 계획한다. 다시 계획했으면 1, 못 했으면 0(수동 검토로 보낸다)."""
            t["over"] += 1
            res = _replan(ctx, t, groups, why, split) if t["over"] <= 2 else None
            if res is None:
                failed.append((t, ("다시 계획 2회 뒤에도 한도에 걸림(%s)" if t["over"] > 2 else "더 작게 나눌 수 없음(%s)") % why))
                return 0
            swap[t["id"]] = res[0]
            drop.update(res[1])
            seen_groups.update(x["group"] for x in res[0] if x.get("group"))
            LOG6.info("작업 %d (%s %d~%d줄): %s. 같은 요청을 다시 보내지 않고 작업 %d개로 다시 계획합니다(%s)"
                      % (t["id"], t["path"], t["la"], t["lb"], why, len(res[0]),
                         ", ".join("%d~%d줄" % (x["la"], x["lb"]) for x in res[0][:4]) + (" ..." if len(res[0]) > 4 else "")))
            return 1

        send, jobs = [], []
        for t in ready:
            if t.get("group"):
                t["ledger"] = _group_ledger(ctx, groups[t["group"]], t)
            t["prompt"] = _task_prompt(ctx, t)
            need = count_tokens(llm, ctx.system_prompt + t["prompt"])
            if not fits_limit(llm, need):
                # 보정값이 바뀌었거나 덧붙인 내용(정의 보강, 기각 사유)이 늘어 한도를 넘게 됐다. 보내기 전에 줄인다
                n_replan += over_limit(t, "보내기 전 확인에서 한도 초과(입력 %d토큰 추정)" % need)
                continue
            t["attempts"] += 1
            t["key"] = sha256_bytes((llm["model"] + "\0" + ctx.system_prompt + "\0" + t["prompt"]).encode("utf-8"))
            t["cached"] = st.one("SELECT response FROM llm_cache WHERE key=?", (t["key"],))
            if t["cached"] is None:
                t["cached"] = shared_cache_get(llm, t["key"])
            write_text(ctx.p("logs", "llm", "task%04d_a%d_prompt.txt" % (t["id"], t["attempts"])),
                       ctx.system_prompt + "\n\n=====\n\n" + t["prompt"])
            send.append(t)
            if t["cached"] is None:
                jobs.append(t)
        LOG6.info("%d차 요청: 작업 %d건 (캐시 재사용 %d건, 다음 차례를 기다리는 작업 %d건)"
                  % (round_no, len(send), len(send) - len(jobs), len(pending) - len(ready)))
        results = {}
        if jobs:
            with ThreadPoolExecutor(max_workers=max(1, int(llm["max_parallel"]))) as ex:
                for t, res in zip(jobs, ex.map(lambda t: llm_chat(llm, ctx.system_prompt, t["prompt"]), jobs)):
                    results[t["id"]] = res
        retry_ids = set()

        def record(t, rfile, ms, ok, note, info, kind):
            st.x("INSERT INTO llm_calls(task_id,attempt,prompt_file,response_file,est_in,ms,ok,note,tok_in,tok_out,kind) "
                 "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (t["id"], t["attempts"], rfile.replace("response", "prompt"), rfile, t.get("est_in", 0), ms, int(ok),
                  note, info.get("in") or info.get("asked_in"), info.get("out"), kind))

        for t in send:
            ok, content, ms, info = (True, t["cached"], 0, {}) if t["cached"] is not None else results[t["id"]]
            rfile = "task%04d_a%d_response.txt" % (t["id"], t["attempts"])
            write_text(ctx.p("logs", "llm", rfile), content)
            raw = est_tokens(ctx.system_prompt + t["prompt"])
            kind = info.get("kind", "")
            if not ok and kind == "over_limit":
                # 서버가 토큰 한도 초과로 거절했다. 서버가 알려 준 수치로 보정하고 더 작게 다시 계획한다
                n_reject += 1
                if info.get("limit") and info["limit"] < llm["max_total_tokens"]:
                    LOG6.warn("서버의 토큰 한도가 설정보다 작습니다. 서버 값을 씁니다: %d -> %d" % (llm["max_total_tokens"], info["limit"]))
                    llm["max_total_tokens"] = info["limit"]
                if info.get("asked_in"):
                    learn_tokens(llm, raw, info["asked_in"])
                else:
                    learn_tokens(llm, raw, llm["max_total_tokens"] - llm["max_output_tokens"] + 1, floor=True)
                record(t, rfile, ms, False, "토큰 한도 초과로 거절됨", info, "over_limit")
                n_replan += over_limit(t, "서버가 토큰 한도 초과로 거절")
                continue
            if ok:
                learn_tokens(llm, raw, info.get("in"))
            if ok and info.get("finish") == "length":
                # 응답이 출력 상한에서 잘렸다. 같은 요청을 다시 보내도 또 잘리므로 수정 지점을 나눠 다시 계획한다
                any_ok = True
                record(t, rfile, ms, True, "응답이 출력 상한에서 잘림", info, "truncated")
                n_replan += over_limit(t, "응답이 출력 상한에서 잘림", split=True)
                continue
            verdict, note = _handle_response(ctx, t, ok, content, kind)
            record(t, rfile, ms, ok, note, info, kind)
            if ok and t["cached"] is None:
                st.x("INSERT OR REPLACE INTO llm_cache(key,response) VALUES(?,?)", (t["key"], content))
                shared_cache_put(llm, t["key"], content)
                any_ok = True
            if not ok:
                conn_fail += 1
            if verdict == "재요청" and t["attempts"] > llm["retries"]:
                verdict = "수동(재요청 한도 초과)"
            tag = (" [분할 %d/%d]" % (t["part"], t["parts"])) if t.get("group") else ""
            LOG6.info("작업 %d%s (%s %d~%d줄) %d차: %s%s" % (t["id"], tag, t["path"], t["la"], t["lb"], t["attempts"],
                                                        verdict, (" - " + note) if note else ""))
            if verdict == "수용":
                st.x("UPDATE tasks SET status='done', attempts=?, note=? WHERE id=?", (t["attempts"], note, t["id"]))
            elif verdict == "재요청":
                retry_ids.add(t["id"])
            elif verdict == "변경없음" and t.get("group"):
                # 분할 조각에서 "수정 불필요": 이 조각의 이슈만 사람이 확인하고, 묶음의 나머지 조각은 계속 간다
                _mark_manual(ctx, t["issue_ids"], note)
                st.x("UPDATE tasks SET status='manual', attempts=?, note=? WHERE id=?", (t["attempts"], note, t["id"]))
            else:
                _mark_manual(ctx, t["issue_ids"], "sLLM 수정 실패: " + (note or verdict))
                st.x("UPDATE tasks SET status='manual', attempts=?, note=? WHERE id=?", (t["attempts"], note or verdict, t["id"]))
                if t.get("group"):
                    cancelled[t["group"]] = t["part"]
        for t, why in failed:
            _mark_manual(ctx, t["issue_ids"], "토큰 한도: " + why)
            st.x("UPDATE tasks SET status='manual', attempts=?, note=? WHERE id=?", (t["attempts"], why, t["id"]))
            LOG6.warn("작업 %d (%s %d~%d줄): %s. 수동 검토로 보냅니다" % (t["id"], t["path"], t["la"], t["lb"], why))
            if t.get("group"):
                cancelled[t["group"]] = t["part"]
        # 실패한 분할 묶음은 전부 취소한다. 한 함수 안에서 일부만 바뀌어 이름과 타입이 어긋나는 것을 막는다
        for g, bad_part in list(cancelled.items()):
            members = groups.pop(g, None)
            if not members:
                continue
            refs = ["task%d" % x["id"] for x in members]
            qm = ",".join("?" * len(refs))
            n_cancel = st.one("SELECT COUNT(*) FROM edits WHERE origin='llm' AND status='accepted' AND ref IN (%s)" % qm, refs)
            st.x("UPDATE edits SET status='rejected', note='분할 순차 취소: 같은 구간의 다른 조각이 실패함' "
                 "WHERE origin='llm' AND status='accepted' AND ref IN (%s)" % qm, refs)
            for x in members:
                if x["part"] != bad_part:
                    _mark_manual(ctx, x["issue_ids"], "분할 순차 취소: 같은 구간의 조각 %d/%d 이(가) 실패함" % (bad_part, x["parts"]))
                    st.x("UPDATE tasks SET status='cancelled', note=? WHERE id=?", ("조각 %d 실패로 취소" % bad_part, x["id"]))
            LOG6.warn("%s: 분할 조각 %d/%d 실패. 이 구간에서 수용했던 수정 %d건을 모두 취소하고 수동 검토로 보냅니다"
                      % (members[0]["path"], bad_part, members[0]["parts"], n_cancel))
        st.commit()
        st.set_meta("llm_tokens", {"model": llm["model"], "hist": llm.get("_tok_hist") or [],
                                   "floor": llm.get("_tok_floor") or []})
        rest = []
        for t in pending:
            if t["id"] in swap:
                rest.extend(swap[t["id"]])        # 다시 계획한 작업이 옛 작업의 자리를 잇는다
            elif t["id"] in drop or t.get("group") in cancelled:
                continue
            elif t["id"] in retry_ids or t["id"] not in ready_ids:
                rest.append(t)
        if conn_fail >= 3 and not any_ok:
            LOG6.error("sLLM 서버 호출이 연속으로 실패했습니다. 주소와 서버 상태를 확인하세요: %s" % llm["endpoint"])
            for t in rest:
                _mark_manual(ctx, t["issue_ids"], "sLLM 서버 호출 실패")
                st.x("UPDATE tasks SET status='manual', note='sLLM 서버 호출 실패' WHERE id=?", (t["id"],))
            st.commit()
            break
        pending = rest
    st.set_meta("chunk_groups", {"total": len(seen_groups), "cancelled": len(cancelled),
                                 "parts": st.one("SELECT COUNT(*) FROM tasks WHERE grp>0 AND status!='replanned'") or 0})
    st.set_meta("llm_stats", {"ratio": round(float(llm.get("_ratio", 1.0)), 3), "overhead": overhead_tokens(llm),
                              "samples": len(llm.get("_tok_hist") or []), "replans": n_replan, "rejects": n_reject,
                              "limit": llm["max_total_tokens"], "limit_config": cfg_limit})
    ts = {r["status"]: r["n"] for r in st.q("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
    LOG6.info("sLLM 수정 완료: 수용 %d건, 수동 검토 %d건, 취소 %d건 (분할 묶음 %d개 중 취소 %d개)"
              % (ts.get("done", 0), ts.get("manual", 0), ts.get("cancelled", 0), len(seen_groups), len(cancelled)))
    LOG6.info("토큰 한도 대응: 서버 거절 %d회, 다시 계획 %d회, 추정 보정 비율 %.2f, 서버 몫 %d토큰, 적용 한도 %d"
              % (n_reject, n_replan, float(llm.get("_ratio", 1.0)), overhead_tokens(llm), llm["max_total_tokens"]))


def _handle_response(ctx, t, ok, content, kind=""):
    """응답 하나를 처리한다. 돌려주는 값: (판정, 설명). 판정은 수용/재요청/변경없음/수동 중 하나.
    토큰 한도 초과와 잘린 응답은 여기 오기 전에 s6_llm 이 다시 계획한다."""
    st, llm = ctx.store, ctx.cfg["llm"]
    if not ok:
        if kind == "bad_request":
            return "수동", "서버가 요청을 거절함(다시 보내도 같으므로 재요청하지 않음): %s" % content[:200]
        return "재요청", "호출 실패: %s" % content[:200]
    blocks = _SR_RE.findall(content)
    if not blocks:
        m = _NEED_RE.search(content)
        if m:
            if t["ctx_rounds"] >= llm["need_context_rounds"]:
                return "수동", "추가 정의 요청 횟수 초과(%s)" % m.group(1)
            snippet = find_symbol_snippet(ctx, m.group(1))
            if not snippet:
                return "수동", "요청한 정의를 찾지 못함(%s)" % m.group(1)
            t["ctx_rounds"] += 1
            t["attempts"] -= 1                        # 정의 보강은 실패 횟수로 세지 않는다
            t["extra"] = (t["extra"] + "\n" + snippet).strip()
            return "재요청", "정의 보강: %s" % m.group(1)
        if "NO_CHANGE" in content:
            return "변경없음", "sLLM 이 수정 불필요로 판단함. 사람이 확인해야 함"
        t["feedback"] = "- 출력 형식이 맞지 않는다. 수정 블록, NO_CHANGE, NEED_CONTEXT 중 하나만 쓴다."
        return "재요청", "형식 오류"
    edits, reasons = [], []
    for search, replace in blocks:
        e, why = check_block(t, search, replace)
        if e:
            edits.append(e)
        elif why:
            reasons.append(why)
    if reasons:
        t["feedback"] = "\n".join("- " + r for r in reasons)
        return "재요청", "수정 블록 기각: " + "; ".join(reasons)
    if not edits:
        return "수동", "수정 블록에 실제 변경이 없음"
    edits.sort(key=lambda e: e["start"])
    for a, b in zip(edits, edits[1:]):
        if b["start"] < a["end"]:
            t["feedback"] = "- 수정 블록끼리 범위가 겹친다."
            return "재요청", "수정 블록끼리 겹침"
    # 수정 대상 줄마다 그 줄을 실제로 바꾼 수정 블록이 있는지 본다. 없는 지점은 "수정됨"으로 치지 않는다
    spans = [(bisect.bisect_right(t["ls1"], e["start"]), bisect.bisect_right(t["ls1"], max(e["start"], e["end"] - 1)))
             for e in edits]
    missed = [i["id"] for line, i in t["plan"]["members"] if not any(a <= line <= b for a, b in spans)]
    if len(missed) == len(t["issue_ids"]):
        t["feedback"] = "- 수정 블록이 [수정 대상 줄]을 바꾸지 않았다. 대상 줄을 SEARCH 에 넣어 고친다."
        return "재요청", "수정 블록이 대상 줄을 건드리지 않음"
    if missed:
        _mark_manual(ctx, missed, "sLLM 응답에 이 지점의 수정이 없음. 사람이 확인해야 함")
    ids = ",".join(str(x) for x in t["issue_ids"] if x not in missed)
    for e in edits:
        st.x("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (t["path"], "post_rule", e["start"], e["end"], e["before"], e["after"], "llm", "task%d" % t["id"],
              ids, 1, "accepted", ("분할 순차 %d/%d" % (t["part"], t["parts"])) if t.get("group") else ""))
    return "수용", "수정 블록 %d개%s" % (len(edits), (", 고치지 않은 지점 %d곳은 수동 검토로" % len(missed)) if missed else "")


# ============================================================================
# [S7 병합]  수정 레코드를 적용해 결과 파일을 만든다
# 목적: 파일을 쓰는 유일한 단계다. 스냅샷 + 수정 레코드 -> work/merged, out/changed.
#       원본의 인코딩/BOM/줄끝을 그대로 따르고, 큰 초기화 목록이 그대로인지 확인한다.
# 산출: work/merged/, out/changed/, edits 상태(applied), files 상태(병합됨)
# ============================================================================
LOG7 = Log("S7.병합")


def build_merged(ctx, rel, use_llm=True, use_other=True):
    """파일 하나의 병합 결과 본문을 만든다(파일은 쓰지 않는다).
    돌려주는 값: (Doc, 병합 본문, 적용 못 한 (id, 사유) 목록)"""
    d = get_doc(ctx, rel)
    e0 = live_edits(ctx, rel, "snapshot") if use_other else []
    text1, bad0 = apply_edits(d.text, e0)
    e1 = live_edits(ctx, rel, "post_rule") if use_llm else []
    if bad0 and e1:
        # 규칙 수정이 빠지면 sLLM 수정의 위치 기준이 달라지므로 함께 뺀다
        bad0 += [(e["id"], "기준이 되는 규칙 수정이 적용되지 않음") for e in e1]
        e1 = []
    text2, bad1 = apply_edits(text1, e1)
    return d, text2, bad0 + bad1


def write_merged(ctx, rel, d, text):
    """병합 본문을 파일로 쓴다. 본문이 원본과 같으면 산출 폴더에서 지운다."""
    changed = ctx.p("out", "changed", *rel.split("/"))
    merged = ctx.p("work", "merged", *rel.split("/"))
    if text == d.text:
        for p in (changed,):
            if os.path.isfile(p):
                os.remove(p)
        if os.path.isfile(merged):
            write_bytes(merged, read_bytes(ctx.snap(rel)))
        return False
    data = encode_work(d, text)
    write_bytes(merged, data)
    write_bytes(changed, data)
    return True


def s7_merge(ctx):
    st = ctx.store
    for sub in (("out", "changed"), ("work", "merged")):
        shutil.rmtree(ctx.p(*sub), ignore_errors=True)
    if ctx.grade == "나":
        shutil.copytree(ctx.p("snapshot"), ctx.p("work", "merged"))      # 컴파일 검사에 헤더가 필요하다
        LOG7.info("컴파일 검사용으로 스냅샷 전체를 work/merged 에 복사")
    paths = [r["path"] for r in st.q("SELECT DISTINCT path FROM edits WHERE status IN (?,?,?) ORDER BY path", _LIVE)]
    n_changed = 0
    for rel in paths:
        d, text, bad = build_merged(ctx, rel)
        if bad:
            reject_edits(ctx, bad)
            for eid, why in bad:
                LOG7.warn("%s: 수정 레코드 %d 제외(%s)" % (rel, eid, why))
            d, text, bad = build_merged(ctx, rel)
        for b in st.q("SELECT start, end, length FROM big_inits WHERE path=?", (rel,)):
            if d.text[b["start"]:b["end"]] not in text:
                LOG7.error("%s: 큰 초기화 목록(%d자)이 병합 결과에서 달라졌습니다. 이 파일의 수정을 모두 취소합니다"
                           % (rel, b["length"]))
                reject_edits(ctx, [(e["id"], "큰 초기화 목록이 달라짐") for e in
                                   st.q("SELECT id FROM edits WHERE path=? AND status IN (?,?,?)", (rel,) + _LIVE)])
                d, text, bad = build_merged(ctx, rel)
                break
        if write_merged(ctx, rel, d, text):
            n_changed += 1
            st.x("UPDATE edits SET status='applied' WHERE path=? AND status='accepted'", (rel,))
            st.x("UPDATE files SET status='병합됨' WHERE path=?", (rel,))
            LOG7.debug("병합: %s" % rel)
    st.commit()
    LOG7.info("결과 파일 %d개 생성 (수정 레코드가 있던 파일 %d개)" % (n_changed, len(paths)))


# ============================================================================
# [S8 검증]  결과 파일 검증과 되돌림
# 목적: 병합 결과가 새 문제를 만들지 않았는지 원본과 비교해 확인한다(차등 검증).
#   L0 괄호 짝, #if/#endif 균형이 원본과 같은가
#   L1 새로 추가된 include 가 찾을 수 있는 파일인가
#   L2 자동 치환 규칙(T1)을 다시 돌렸을 때 남은 것이 없는가
#   L3 (환경 등급 '나') cl /Zs 로 검사했을 때 원본에 없던 오류가 생기지 않았는가
# 불합격 시: sLLM 수정만 빼고 다시 병합 -> 그래도 불합격이면 그 파일의 수정을 모두 뺀다.
# 산출: verifications, edits 상태(verified/rejected), issues 상태(fixed/manual), files 상태
# 한계: L0~L2 통과는 "컴파일된다"는 뜻이 아니다(02_검토방안 10.4).
# ============================================================================
LOG8 = Log("S8.검증")


def verify_static(ctx, d, text):
    """L0~L2. 돌려주는 값: (통과 여부, [(수준, 결과, 설명)])"""
    res = []
    code, _s = mask_code(text)
    struct = mask_preprocessor(code)
    ok0 = True
    if balance_of(struct) != balance_of(d.struct):
        ok0 = False
        res.append(("L0", "불합격", "괄호 짝이 원본과 달라짐 %s -> %s" % (balance_of(d.struct), balance_of(struct))))
    if pp_balance_of(code) != pp_balance_of(d.code):
        ok0 = False
        res.append(("L0", "불합격", "#if/#endif 균형이 원본과 달라짐"))
    if d.parse_ok and not analyze_structure(struct)[2]:
        ok0 = False
        res.append(("L0", "불합격", "원본에서는 맞던 괄호 구조가 깨짐"))
    if ok0:
        res.append(("L0", "통과", ""))
    old_inc = {d.text[m.start(2):m.end(2)] for m in _INC_RE.finditer(d.code)}
    tree = {os.path.basename(r["path"]).lower() for r in ctx.store.q("SELECT path FROM files")}
    new_inc = []
    for m in _INC_RE.finditer(code):
        h = text[m.start(2):m.end(2)]
        if h not in old_inc and m.group(1) == '"' and os.path.basename(norm_rel(h)).lower() not in tree:
            new_inc.append(h)
    ok1 = not new_inc
    res.append(("L1", "통과" if ok1 else "불합격", "" if ok1 else "새로 추가된 include 를 찾을 수 없음: %s" % ", ".join(new_inc)))
    d2 = make_doc(d.rel, encode_work(d, text))
    left = []
    for rule in ctx.rules:
        if rule["tier"] == "T1":
            n = len(rule_hits(rule, d2, ctx.env))
            if n:
                left.append("%s %d곳" % (rule["id"], n))
    ok2 = not left
    res.append(("L2", "통과" if ok2 else "불합격", "" if ok2 else "자동 치환 규칙이 다시 탐지됨: " + ", ".join(left)))
    return ok0 and ok1 and ok2, res


def _compile_flags(ctx, rel):
    """rel 이 속한(또는 가장 가까운) 프로젝트의 구성에서 cl 옵션을 만든다."""
    projects = ctx.store.get_meta("projects", {})
    best, best_dir = None, ""
    for pf, proj in projects.items():
        pdir = os.path.dirname(pf)
        files = {norm_rel(os.path.normpath(os.path.join(pdir, f))).lower() for f in proj.get("files", [])}
        if rel.lower() in files:
            best, best_dir = proj, pdir
            break
        if best is None:
            best, best_dir = proj, pdir
    cfg = pick_config(best) if best else None
    return cfg, best_dir


def compile_workers(ctx):
    """컴파일 검사 동시 수. 설정(limits.compile_parallel)이 0 이면 CPU 수에 맞춘다(한 개는 남기고, 최대 8)."""
    n = int(ctx.cfg["limits"].get("compile_parallel", 0) or 0)
    if n <= 0:
        n = min(8, (os.cpu_count() or 2) - 1)
    return max(1, n)


def _compile_plan(ctx, rel):
    """rel 의 컴파일 검사에 쓸 값을 모은다. 저장소를 읽으므로 주 스레드에서 부른다."""
    cfg, pdir = _compile_flags(ctx, rel)
    return {"rel": rel, "cfg": cfg, "pdir": pdir, "arch": "x64" if ctx.env["platform"] == "x64" else "x86"}


def _compile_run(ctx, plan):
    """L3: 병합 결과와 스냅샷을 각각 cl /Zs 로 검사해 새로 생긴 오류만 본다.
    돌려주는 값: (True 통과 / False 새 오류 있음 / None 검사하지 못함, 설명)
    저장소를 쓰지 않으므로 병렬 스레드에서 불러도 된다."""
    rel, cfg, pdir = plan["rel"], plan["cfg"], plan["pdir"]
    tmo = ctx.cfg["limits"]["compile_timeout_sec"]

    def run(base):
        root = ctx.p(*base)
        flags = cl_flags_for(ctx.env, cfg, os.path.join(root, *pdir.split("/")) if pdir else root)
        src = os.path.join(root, *rel.split("/"))
        ran, diags, raw = run_cl_syntax(ctx.tools, src, os.path.dirname(src), flags, tmo, plan["arch"])
        write_text(ctx.p("logs", "compile", base[-1], rel.replace("/", "__") + ".log"), raw)
        return ran, [x for x in diags if x["kind"] != "warning"]

    try:
        ran_a, before = run(("snapshot",))
        ran_b, after = run(("work", "merged"))
    except Exception as e:
        return None, "컴파일 검사 중 오류(%s: %s)" % (type(e).__name__, e)
    if not (ran_a and ran_b):
        return None, "컴파일러를 실행하지 못함"
    old = {}
    for x in before:
        old[(x["code"], x["msg"])] = old.get((x["code"], x["msg"]), 0) + 1
    new = []
    for x in after:
        k = (x["code"], x["msg"])
        if old.get(k, 0) > 0:
            old[k] -= 1
        else:
            new.append("%s(%d줄) %s" % (x["code"], x["line"], x["msg"][:100]))
    if new:
        return False, "새 컴파일 오류 %d건: %s" % (len(new), " | ".join(new[:3]))
    return True, "오류 %d건 -> %d건(새로 생긴 오류 없음)" % (len(before), len(after))


def verify_compile(ctx, rel):
    """파일 하나의 컴파일 검사(주 스레드용)."""
    return _compile_run(ctx, _compile_plan(ctx, rel))


def _precompile(ctx, paths):
    """변경 파일의 컴파일 검사를 미리 병렬로 돌려 둔다. 돌려주는 값: {파일: (결과, 설명)}
    컴파일러 호출만 스레드에서 하고, 저장소를 읽는 일(_compile_plan)은 주 스레드에서 끝낸다."""
    if not paths:
        return {}
    plans = [_compile_plan(ctx, rel) for rel in paths]
    get_vs_env(ctx.tools, plans[0]["arch"])        # 개발자 환경은 스레드를 띄우기 전에 한 번만 만든다
    n_par = min(compile_workers(ctx), len(plans))
    LOG8.info("컴파일 검사: 변경 파일 %d개, 동시 %d" % (len(plans), n_par))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n_par) as ex:
        out = dict(zip(paths, ex.map(lambda p: _compile_run(ctx, p), plans)))
    LOG8.info("컴파일 검사 끝 (%.1f초)" % (time.time() - t0))
    return out


def s8_verify(ctx):
    st = ctx.store
    st.x("DELETE FROM verifications")
    use_cl = ctx.grade == "나" and ctx.cfg["compile"]["mode"] != "off"
    paths = [r["path"] for r in st.q("SELECT path FROM files WHERE status='병합됨' ORDER BY path")]
    is_src = lambda rel: os.path.splitext(rel)[1].lower() in (".c", ".cc", ".cpp", ".cxx")
    pre = _precompile(ctx, [rel for rel in paths if is_src(rel)]) if use_cl else {}
    n_pass = n_partial = n_revert = 0
    for rel in paths:
        d, text, _bad = build_merged(ctx, rel)
        stage = "전체"
        while True:
            ok, res = verify_static(ctx, d, text)
            level = "L2" if ok else ""
            if ok and use_cl and is_src(rel):
                # 처음 한 번은 미리 구해 둔 결과를 쓴다. 수정을 빼고 다시 병합한 뒤에는 새로 검사한다
                c_ok, c_note = pre.pop(rel) if rel in pre else verify_compile(ctx, rel)
                if c_ok is None:
                    res.append(("L3", "미수행", c_note))
                elif c_ok:
                    res.append(("L3", "통과", c_note))
                    level = "L3"
                else:
                    res.append(("L3", "불합격", c_note))
                    ok = False
            for lv, result, detail in res:
                st.x("INSERT INTO verifications(path,level,result,detail) VALUES(?,?,?,?)", (rel, lv, "%s(%s)" % (result, stage), detail))
            if ok:
                break
            why = "; ".join("%s %s" % (lv, dt) for lv, rs, dt in res if rs == "불합격")
            dropped = False
            for origin, label in (("llm", "sLLM 수정 제외"), ("request", "요청사항 수정 제외"), (None, "모든 수정 제외")):
                if origin:
                    ids = [e["id"] for e in st.q("SELECT id FROM edits WHERE path=? AND origin=? AND status IN (?,?,?)",
                                                 (rel, origin) + _LIVE)]
                else:
                    ids = [e["id"] for e in st.q("SELECT id FROM edits WHERE path=? AND status IN (?,?,?)", (rel,) + _LIVE)]
                if ids:
                    LOG8.warn("%s: 검증 불합격(%s). %s: 수정 %d건을 빼고 다시 병합합니다" % (rel, why, label, len(ids)))
                    reject_edits(ctx, [(i, "검증 불합격: " + why) for i in ids])
                    stage, dropped = label, True
                    pre.pop(rel, None)            # 병합 결과가 바뀌므로 미리 구해 둔 컴파일 결과는 버린다
                    break
            if not dropped:
                break
            d, text, _bad = build_merged(ctx, rel)
            write_merged(ctx, rel, d, text)
            if text == d.text:
                break
        if text == d.text:
            n_revert += 1
            st.x("UPDATE files SET status='수동검토', verify_level='', note=? WHERE path=?",
                 ("검증 불합격으로 수정을 모두 취소함", rel))
            continue
        st.x("UPDATE edits SET status='verified' WHERE path=? AND status='applied'", (rel,))
        for e in st.q("SELECT issue_ids FROM edits WHERE path=? AND status='verified'", (rel,)):
            for iid in (e["issue_ids"] or "").split(","):
                if iid:
                    st.x("UPDATE issues SET status='fixed' WHERE id=? AND status IN ('open','fixed')", (int(iid),))
        n_manual = st.one("SELECT COUNT(*) FROM issues WHERE path=? AND status='manual'", (rel,))
        if n_manual:
            n_partial += 1
        else:
            n_pass += 1
        st.x("UPDATE files SET status=?, verify_level=? WHERE path=?", ("부분수정" if n_manual else "변경됨", level, rel))
        LOG8.info("%s: %s (검증 %s%s)" % (rel, "부분수정" if n_manual else "변경됨", level,
                                       ", 수동 검토 %d건 남음" % n_manual if n_manual else ""))
    st.x("UPDATE files SET status='수동검토' WHERE status IN ('대상','읽기전용') AND path IN "
         "(SELECT DISTINCT path FROM issues WHERE status='manual')")
    st.x("UPDATE files SET status='변경없음' WHERE status='대상'")
    st.x("UPDATE files SET status='변경없음(읽기전용)' WHERE status='읽기전용'")
    st.commit()
    LOG8.info("검증 결과: 변경됨 %d, 부분수정 %d, 수정 취소 %d (검증 수준: %s)"
              % (n_pass, n_partial, n_revert, "L0~L3" if use_cl else "L0~L2"))


# ============================================================================
# [S9 산출]  diff, 보고서, 매니페스트, 수동 검토 목록
# 목적: 결과를 사람이 확인할 수 있는 문서로 낸다. diff 는 sLLM 출력이 아니라
#       스냅샷과 결과 본문을 직접 비교해 만든다.
# 산출: out/diff/, out/report.md, manifest.csv, manual_review.md,
#       project_settings.md, build_guide.md
# ============================================================================
LOG9 = Log("S9.산출")


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(md_escape(c) for c in r) + " |" for r in rows]
    return out


def prompt_version(ctx):
    """프롬프트의 판(요구사항 B7). 내장 프롬프트나 교체 파일의 내용이 바뀌면 달라지는 8자리 값이다."""
    text = (getattr(ctx, "system_prompt", None) or SYSTEM_PROMPT) + "\n" + (getattr(ctx, "user_template", None) or USER_TEMPLATE)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _extra_report_lines(ctx):
    """보고서 7절 뒤에 붙는 부분: 변경 계약(E3, E4, E6), 빌드 로그에서 넣은 이슈(F8), 예외로 건너뛴 탐지(C11),
    규칙 후보(C12). 해당하는 것이 없으면 그 부분은 내지 않는다."""
    st, L = ctx.store, []
    cons = st.get_meta("contracts", []) or []
    if cons:
        L += ["", "### 변경 계약(요청사항)", "", "요청사항으로 바뀌는 선언과 그 사용처의 처리 결과다. 컴파일러 없이 글자로 분석한 "
              "근사이므로, 수동 검토로 남은 사용처는 사람이 확인해야 한다.", ""]
        for c in cons:
            left = st.one("SELECT COUNT(*) FROM issues WHERE source='request' AND rule_id=? AND status IN ('manual','open')",
                          (c["id"],)) or 0
            L.append("- **%s** (%s, 대상 `%s`): %s. 수동 검토로 남은 사용처 %d건"
                     % (c["id"], c["type"], c["target"], ", ".join("%s %d" % kv for kv in c["counts"].items()), left))
            if c.get("rename"):
                L.append("  - 이름 대응: " + ", ".join("`%s` → `%s`" % kv for kv in list(c["rename"].items())[:30]))
            if c.get("aliases"):
                L.append("  - 같은 타입으로 본 별칭: " + ", ".join("`%s`" % a for a in c["aliases"]))
            if c.get("note"):
                L.append("  - " + c["note"])
            if c.get("definition_after"):
                L += ["  - 새 정의:", "", "```cpp", c["definition_after"], "```", ""]
    bl = st.get_meta("build_log", None)
    if bl:
        L += ["", "### 빌드 로그에서 넣은 이슈", "", "- 로그: `%s`" % bl["path"],
              "- sLLM 대상 %d건, 수동 검토 %d건, 소스 줄이 없는 오류(프로젝트, 옵션, 링크) %d건" % (bl["llm"], bl["manual"], bl["other"]),
              "- 이 실행의 패키지가 그 로그를 낸 소스 트리라는 전제로 줄을 맞췄다. 처리 결과는 4절의 `B-` 로 시작하는 줄에 있다"]
    n_skip = st.get_meta("skipped_hits", 0) or 0
    if n_skip:
        L += ["", "### 예외로 건너뛴 탐지", "", "- %d곳(예외 파일의 skip 항목, 소스의 `vupdate:skip` 표식)" % n_skip]
    need = max(2, int(ctx.cfg["limits"].get("rule_candidate_min", 3)))
    groups = OrderedDict()
    for r in st.q("SELECT before, after, issue_ids FROM edits WHERE origin='llm' AND status='accepted' ORDER BY id"):
        key = (" ".join(r["before"].split()), " ".join(r["after"].split()))
        if key[0] == key[1] or len(key[0]) > 160 or len(key[1]) > 240:
            continue
        g = groups.setdefault(key, {"n": 0, "rules": set()})
        g["n"] += 1
        ids = [x for x in str(r["issue_ids"] or "").split(",") if x.strip().isdigit()]
        if ids:
            g["rules"].add(st.one("SELECT rule_id FROM issues WHERE id=?", (int(ids[0]),)) or "")
    rows = sorted(((g["n"], k[0], k[1], ", ".join(sorted(x for x in g["rules"] if x))) for k, g in groups.items() if g["n"] >= need),
                  key=lambda x: -x[0])[:20]
    if rows:
        L += ["", "### 규칙 후보(sLLM 이 같은 수정을 반복한 것)", "",
              "같은 수정이 %d번 이상 나온 것이다. `config/rules.json` 에 규칙으로 옮기면 다음부터 sLLM 없이 처리된다." % need, ""]
        L += _table(["횟수", "변경 전", "변경 후", "규칙"],
                    [(n, "`%s`" % b.replace("|", "\\|"), "`%s`" % a.replace("|", "\\|"), rule) for n, b, a, rule in rows])
    return L


def s9_report(ctx):
    st, env = ctx.store, ctx.env
    out = ctx.p("out")
    shutil.rmtree(os.path.join(out, "diff"), ignore_errors=True)

    # --- diff ---
    patch, changed = [], st.q("SELECT path, status, verify_level FROM files WHERE status IN ('변경됨','부분수정') ORDER BY path")
    for r in changed:
        d, text, _bad = build_merged(ctx, r["path"])
        lines = []
        for ln in difflib.unified_diff(d.text.splitlines(keepends=True), text.splitlines(keepends=True),
                                       fromfile="a/" + r["path"], tofile="b/" + r["path"], n=3):
            lines.append(ln if ln.endswith("\n") else ln + "\n\\ No newline at end of file\n")
        body = "".join(lines)
        write_text(os.path.join(out, "diff", r["path"].replace("/", "__") + ".diff"), body)
        patch.append(body)
    if patch:
        write_text(os.path.join(out, "diff", "all.patch"), "".join(patch))
    LOG9.info("diff %d개 작성" % len(patch))

    # --- 매니페스트(전체 파일의 처리 상태) ---
    n_issue = {r["path"]: r["n"] for r in st.q("SELECT path, COUNT(*) AS n FROM issues GROUP BY path")}
    n_edit = {r["path"]: r["n"] for r in st.q("SELECT path, COUNT(*) AS n FROM edits WHERE status='verified' GROUP BY path")}
    mpath = os.path.join(out, "manifest.csv")
    os.makedirs(out, exist_ok=True)
    with open(mpath, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["경로", "종류", "크기", "인코딩", "줄끝", "상태", "검증수준", "이슈수", "적용수정수", "비고"])
        for r in st.q("SELECT * FROM files ORDER BY path"):
            w.writerow([r["path"], r["kind"], r["size"], r["encoding"] or "", r["eol"] or "", r["status"],
                        r["verify_level"] or "", n_issue.get(r["path"], 0), n_edit.get(r["path"], 0), r["note"] or ""])
    LOG9.info("매니페스트 작성: %s" % mpath)

    # --- 수동 검토 목록 ---
    manual = st.q("SELECT * FROM issues WHERE status IN ('manual','open') AND tier!='T0' ORDER BY path, line")
    L = ["# 수동 검토 목록", "", "자동으로 고치지 못했거나 사람이 확인해야 하는 항목이다. 총 %d건." % len(manual), ""]
    if manual:
        L += _table(["파일", "줄", "규칙/요청", "내용", "탐지된 코드", "사유"],
                    [(r["path"] or "-", r["line"], r["rule_id"], r["title"], (r["detail"] or "")[:100],
                      r["note"] or ("미처리" if r["status"] == "open" else "")) for r in manual])
    rej = st.q("SELECT * FROM edits WHERE status='rejected' ORDER BY path, start")
    if rej:
        L += ["", "## 기각된 수정", ""] + _table(["파일", "출처", "근거", "변경 전", "변경 후", "사유"], [
            (r["path"], r["origin"], r["ref"], r["before"][:60], r["after"][:60], r["note"]) for r in rej])
    write_text(os.path.join(out, "manual_review.md"), "\n".join(L) + "\n")

    # --- 프로젝트 설정 변경 목록 ---
    settings = st.get_meta("settings", [])
    bins = st.q("SELECT path, size FROM files WHERE kind='binary' AND (path LIKE '%.lib' OR path LIKE '%.dll') ORDER BY path")
    L = ["# 프로젝트 설정 변경 목록", "", "프로젝트 파일은 이 도구가 고치지 않는다. Visual Studio 에서 변환한 뒤 아래를 적용한다.", "",
         "## 1. 대상 환경", ""] + ["- " + s for s in env_summary_lines(env)] + ["", "## 2. 코드 변경 없이 설정으로 해결하는 항목", ""]
    L += _table(["규칙", "내용", "해당 파일 수", "설정"], [(s["rule_id"], s["title"], s["files"], s["setting"]) for s in settings]) \
        if settings else ["해당 없음."]
    L += ["", "## 3. 재빌드 또는 교체를 확인할 바이너리", "",
          "이전 툴셋으로 만든 .lib/.dll 은 새 툴셋과 호환이 보장되지 않는다. 소스 수정으로는 해결되지 않는다.", ""]
    L += _table(["파일", "크기"], [(b["path"], b["size"]) for b in bins]) if bins else ["패키지 안에 .lib/.dll 이 없다."]
    notes = st.get_meta("project_notes", []) or []
    L += ["", "## 4. 프로젝트 파일 점검", "",
          "프로젝트 파일(.vcproj, .vcxproj)의 설정 가운데 버전 전환에서 문제가 되는 것이다. 소스가 아니라 프로젝트 속성에서 "
          "처리한다. .vcxproj 가 가져오는 .props 파일의 설정은 읽지 않는다.", ""]
    L += _table(["프로젝트", "구성", "항목", "영향과 조치"],
                [(n["project"], ", ".join(n["configs"]), n["item"], n["note"]) for n in notes]) if notes else ["해당 없음."]
    L += ["", "## 5. 환경값에 따른 확인 목록", "",
          "환경값 축(OS, DB, HMI 도구)에 적은 제품 이름으로 고른 일반적인 확인 사항이다. 판별 세부는 제조사 문서를 따른다. "
          "코드가 실제로 쓰는 DB 접근 기술과 HMI 구성 요소는 2절의 [DB], [HMI] 항목과 수동 검토 목록에 나온다.", ""]
    an = axis_notes(env)
    for label, value, items in an:
        L += ["**%s: %s**" % (label, value), ""] + ["- " + x for x in items] + [""]
    if not an:
        L.append("해당 없음(환경값 축을 주지 않았거나 알려진 제품 이름이 없다).")
    write_text(os.path.join(out, "project_settings.md"), "\n".join(L) + "\n")

    # --- 빌드 환경용 절차서 ---
    G = ["# 빌드 환경에서 할 일", "",
         "1. 원본 패키지를 복사한다. 변환은 되돌릴 수 없으므로 복사본에서 한다.",
         "2. `out/changed/` 의 파일을 복사본의 같은 위치에 덮어쓴다.",
         "3. 복사본의 .sln 을 대상 Visual Studio 로 열어 프로젝트를 변환한다. 변환 보고서(UpgradeLog.htm)를 보관한다.",
         "4. `project_settings.md` 의 설정을 프로젝트 속성에 적용한다.",
         "5. 빌드하고 오류 로그를 파일로 남긴다. 개발자 PowerShell 에서:",
         "", "```", "msbuild 솔루션이름.sln /p:Configuration=Release /p:Platform=Win32 /fl", "```", "",
         "6. 오류가 나면 같은 폴더에 생긴 msbuild.log 를 이 도구가 있는 PC 로 가져와 분류한다. 처리 순서와 오류 코드별 대응이 나온다.",
         "", "```", "vupdate buildlog --log msbuild.log --run <이 실행 폴더>", "```", "",
         "7. 분류 결과(`build_errors.md`)와 `manual_review.md` 를 함께 보고 남은 항목을 처리한다. 절차는 `07_오류대응.md` 에 있다.",
         "8. 링크 오류와 실행 시 동작은 이 도구의 검증 범위 밖이다. 직접 확인한다.", ""]
    write_text(os.path.join(out, "build_guide.md"), "\n".join(G))

    # --- 보고서 ---
    fstat = st.q("SELECT status, COUNT(*) AS n FROM files GROUP BY status ORDER BY status")
    istat = st.q("SELECT tier, status, COUNT(*) AS n FROM issues GROUP BY tier, status ORDER BY tier, status")
    estat = st.q("SELECT origin, status, COUNT(*) AS n FROM edits GROUP BY origin, status ORDER BY origin, status")
    rstat = st.q("SELECT rule_id, tier, COUNT(*) AS n, SUM(status='fixed') AS fixed, SUM(status='manual') AS manual, "
                 "SUM(status='absorbed') AS absorbed FROM issues GROUP BY rule_id, tier ORDER BY tier, rule_id")
    calls = st.q("SELECT COUNT(*) AS n, COALESCE(SUM(est_in),0) AS tok, COALESCE(SUM(ms),0) AS ms, "
                 "COALESCE(SUM(ok=0),0) AS fail, SUM(tok_in) AS tin, SUM(tok_out) AS tout, "
                 "COALESCE(SUM(kind='over_limit'),0) AS n_over, COALESCE(SUM(kind='truncated'),0) AS n_trunc "
                 "FROM llm_calls")[0]
    lstat = st.get_meta("llm_stats", None) or {}
    o = ctx.opts
    opt_lines = ["호출자: %s" % (o.get("caller") or "-")]
    if o.get("only") or o.get("exclude"):
        opt_lines.append("처리 범위: only=%s, exclude=%s. 범위 밖 파일은 탐지와 수정을 하지 않았다(범위 안팎에 걸친 "
                         "변경의 일관성은 확인하지 않는다)" % (", ".join(o.get("only") or []) or "(전체)",
                                                    ", ".join(o.get("exclude") or []) or "(없음)"))
    for key, text in (("no_llm", "sLLM 단계를 건너뜀(--no-llm). sLLM 대상 이슈는 수동 검토로 보냈다"),
                      ("no_chunk", "분할 순차를 끔(--no-chunk). 한도를 넘는 구간은 수동 검토로 보냈다"),
                      ("no_compile", "컴파일 검사를 하지 않음(--no-compile)")):
        if o.get(key):
            opt_lines.append(text)
    tstat = st.q("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")
    cg = st.get_meta("chunk_groups", None) or {}
    done = st.get_meta("stages_done", [])
    grade_txt = {"가": "컴파일 검사 불가. 알려진 패턴의 처리와 새 구문 오류가 없음까지만 확인했다. 컴파일과 링크 성공은 보장하지 않는다.",
                 "나": "파일 단위 컴파일 검사 가능. 변경 파일에서 새 컴파일 오류가 없음을 확인했다. 링크 성공은 보장하지 않는다."}
    R = ["# 버전 업데이트 처리 보고서", "",
         "- 패키지: `%s`" % ctx.package, "- 실행 폴더: `%s`" % ctx.run_dir,
         "- 작성 시각: %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "- 도구 버전: %s %s" % (APP, VERSION),
         "- 수행한 단계: %s%s" % (", ".join(done + (["S9"] if "S9" not in done else [])), " (탐지만 수행)" if ctx.dry_run else ""),
         "", "## 1. 적용한 환경값", ""] + ["- " + s for s in env_summary_lines(env)]
    R += ["", "실행 옵션:", ""] + ["- " + s for s in opt_lines]
    if ctx.request.get("common"):
        R += ["", "공통 요청(sLLM 에 보내는 모든 조각에 붙였다. 규칙 수정에는 영향이 없다):", ""] + [
            "- " + s for s in ctx.request["common"]]
    R += ["", "규칙 %d개 중 이 환경값에서 켜진 것 %d개." % (len(ctx.rule_by_id), len(ctx.rules))]
    if ctx.rules_off:
        R += ["", "꺼진 규칙(이 환경값에는 해당하지 않아 탐지하지 않았다):", ""] + _table(["규칙", "내용", "꺼진 이유"], ctx.rules_off)
    cov = coverage_summary()
    R += ["", "알려진 변경 항목 %d건의 처리 방식: %s. 목록은 `vupdate rules --coverage`."
          % (sum(cov.values()), ", ".join("%s %d건" % kv for kv in cov.items()))]
    R += ["", "## 2. 환경 등급과 보장 범위", "", "- 환경 등급: **%s**" % ctx.grade, "- " + grade_txt.get(ctx.grade, ""),
          "- 상세는 `preflight.md` 참조.", "", "## 3. 파일 처리 결과", ""]
    R += _table(["상태", "파일 수"], [(r["status"], r["n"]) for r in fstat])
    R += ["", "## 4. 이슈(탐지 결과)", ""] + _table(["규칙/요청", "단계", "탐지", "수정됨", "설정 흡수", "수동 검토"], [
        (r["rule_id"], r["tier"], r["n"], r["fixed"] or 0, r["absorbed"] or 0, r["manual"] or 0) for r in rstat])
    R += ["", "단계별 상태:", ""] + _table(["단계", "상태", "건수"], [(r["tier"], r["status"], r["n"]) for r in istat])
    R += ["", "## 5. 수정 레코드", ""] + (_table(["출처", "상태", "건수"], [(r["origin"], r["status"], r["n"]) for r in estat])
                                       if estat else ["없음."])
    R += ["", "## 6. sLLM 사용", "", "- 호출 %d회(실패 %d회), 소요 %.1f초" % (calls["n"], calls["fail"], calls["ms"] / 1000.0),
          "- 프롬프트 판: %s (내장 프롬프트나 교체 파일의 내용이 바뀌면 달라진다)" % prompt_version(ctx),
          "- 입력 토큰: 추정 합계 %d, 서버가 알려 준 실제 합계 %s (출력 %s)"
          % (calls["tok"], calls["tin"] if calls["tin"] is not None else "알 수 없음(서버가 알려 주지 않음)",
             calls["tout"] if calls["tout"] is not None else "알 수 없음"),
          "- 토큰 한도: 적용한 값 %s(설정값 %s). 서버가 한도 초과로 거절 %d회, 응답이 출력 상한에서 잘림 %d회, "
          "더 작게 다시 계획 %d회" % (lstat.get("limit", "-"), lstat.get("limit_config", "-"), calls["n_over"], calls["n_trunc"],
                             lstat.get("replans", 0)),
          "- 토큰 추정 보정: 비율 %s, 서버 몫 %s토큰 (서버 실제 값 표본 %d개. 0개면 보정하지 못하고 추정치로만 판단한 것이다)"
          % (lstat.get("ratio", "-"), lstat.get("overhead", "-"), lstat.get("samples", 0)),
          "- 작업 상태: %s" % (", ".join("%s %d건" % (r["status"], r["n"]) for r in tstat) or "작업 없음"),
          "- 분할 순차(한 번에 보낼 수 없어 나눠 처리한 구간): %d개, 조각 %d개, 그중 취소된 구간 %d개. "
          "이 경로의 수정은 모두 검토 대상이다" % (cg.get("total", 0), cg.get("parts", 0), cg.get("cancelled", 0)),
          "- 요청과 응답 원문: `logs/llm/`", "", "## 7. 변경 파일", ""]
    R += _table(["파일", "상태", "검증 수준", "적용된 수정"], [
        (r["path"], r["status"], r["verify_level"] or "-", n_edit.get(r["path"], 0)) for r in changed]) if changed else ["없음."]
    review = st.q("SELECT e.path, e.origin, e.ref, e.before, e.after FROM edits e WHERE e.status='verified' AND e.review=1 ORDER BY e.path, e.start")
    if review:
        R += ["", "### 적용했지만 사람이 확인해야 하는 수정", ""] + _table(["파일", "출처", "근거", "변경 전", "변경 후"], [
            (r["path"], r["origin"], r["ref"], r["before"][:70], r["after"][:70]) for r in review])
    n_manual = st.one("SELECT COUNT(*) FROM issues WHERE status IN ('manual','open') AND tier!='T0'")
    R += _extra_report_lines(ctx)
    R += ["", "## 8. 남은 일", "", "- 수동 검토 %d건: `manual_review.md`" % n_manual,
          "- 빌드 환경에서 오류가 나면: `vupdate buildlog --log msbuild.log --run <이 실행 폴더>` 로 분류한다",
          "- 프로젝트 설정 변경: `project_settings.md`", "- 실제 빌드 절차: `build_guide.md`",
          "", "## 9. 이 버전(%s)이 하지 않는 것" % VERSION, "",
          "- 프로젝트 변환과 전체 빌드, .rc/.idl/.def 의 규칙 탐지",
          "- DB, HMI 도구에 따른 내장 규칙(값은 프롬프트와 보고서에 전달된다)",
          "- 컴파일러 수준의 영향범위 분석(구조체 변경 요청은 글자 기준의 근사다). 배열 선언을 구조체로 바꾸는 일",
          "- 링크 오류와 프로젝트 설정 오류의 수정(--build-log 는 컴파일 오류만 sLLM 에 맡긴다)",
          "- 토큰 수를 보내기 전에 정확히 세는 것(추정한 뒤 서버의 실제 값으로 보정한다)", ""]
    write_text(os.path.join(out, "report.md"), "\n".join(R))
    LOG9.info("보고서 작성: %s" % os.path.join(out, "report.md"))


# ============================================================================
# [M10 실행기]  단계 순서 실행, 재개, 오류 처리
# 목적: S0~S9 를 순서대로 부른다. 단계가 끝날 때마다 상태를 저장해 재개할 수 있게 한다.
#       단계에서 예외가 나면 로그에 [M10.실행기] 줄과 호출 경로 전체를 남기고 멈춘다.
# ============================================================================
LOGM = Log("M10.실행기")
STAGE_FUNCS = {"S0": s0_prepare, "S1": s1_inventory, "S2": s2_detect, "S3": s3_absorb, "S4": s4_apply_rules,
               "S5": s5_impact, "S6": s6_llm, "S7": s7_merge, "S8": s8_verify, "S9": s9_report}


def run_pipeline(ctx, plan, resume=False):
    st = ctx.store
    names = dict(STAGES)
    llm_cfg = ctx.cfg["llm"]
    job = job_report(llm_cfg, "job_open", {"caller": ctx.opts.get("caller", ""), "run_id": llm_cfg.get("_run_id", ""),
                                          "package": ctx.pkg_name, "env": env_summary_lines(ctx.env), "version": VERSION})
    if job and job.get("job_id"):
        llm_cfg["_job_id"] = str(job["job_id"])
    done = list(st.get_meta("stages_done", [])) if resume else []
    if resume:
        ctx.tools = st.get_meta("tools", {})
        ctx.grade = st.get_meta("grade", "가")
        LOGM.info("재개: 끝난 단계 %s 는 건너뜁니다" % (", ".join(done) or "없음"))
    else:
        st.set_meta("stages_done", [])
    t_all = time.time()
    for sid in plan:
        if sid in done and sid != "S9":
            continue
        LOGM.info("===== %s %s 시작 =====" % (sid, names[sid]))
        t0 = time.time()
        try:
            STAGE_FUNCS[sid](ctx)
        except UserInputError:
            raise
        except Exception:
            LOGM.exception("단계 %s(%s) 실패. 원인은 아래 호출 경로의 마지막 줄을 본다" % (sid, names[sid]))
            st.set_meta("failed_stage", sid)
            LOGM.error("중단했습니다. 원인을 고친 뒤 --resume \"%s\" 로 이어서 실행할 수 있습니다" % ctx.run_dir)
            return EXIT_FAIL
        if sid not in done:
            done.append(sid)
        st.set_meta("stages_done", done)
        st.set_meta("failed_stage", "")
        LOGM.info("===== %s %s 완료 (%.1f초) =====" % (sid, names[sid], time.time() - t0))
        job_report(llm_cfg, "job_progress", {"job_id": llm_cfg.get("_job_id", ""), "run_id": llm_cfg.get("_run_id", ""), "stage": sid})
    n_manual = st.one("SELECT COUNT(*) FROM issues WHERE status IN ('manual','open') AND tier!='T0'") or 0
    n_changed = st.one("SELECT COUNT(*) FROM files WHERE status IN ('변경됨','부분수정')") or 0
    if ctx.dry_run:
        LOGM.info("탐지만 수행했습니다 (%.1f초): 처리 대상 이슈 %d건. 건수는 out/report.md 4절 참조"
                  % (time.time() - t_all, n_manual))
        LOGM.info("산출물: %s" % ctx.p("out"))
        return EXIT_OK
    job_report(llm_cfg, "job_close", {"job_id": llm_cfg.get("_job_id", ""), "run_id": llm_cfg.get("_run_id", ""),
                                     "changed": n_changed, "manual": n_manual})
    LOGM.info("전체 완료 (%.1f초): 변경 파일 %d개, 수동 검토 %d건" % (time.time() - t_all, n_changed, n_manual))
    LOGM.info("산출물: %s" % ctx.p("out"))
    LOGM.info("로그: %s" % ctx.p("logs", "run.log"))
    return EXIT_REVIEW if n_manual else EXIT_OK


# ============================================================================
# [M12 부가명령]  서버 점검(llmcheck), 빌드 오류 목록 분류(buildlog)
# 목적: 단계(S0~S9) 밖에서 쓰는 보조 명령 두 가지.
#   llmcheck : 실제 sLLM(또는 중계 서버)이 이 도구가 기대하는 대로 답하는지 확인한다.
#              토큰 수를 돌려주는가, 한도 초과를 어떻게 거절하는가, 출력 형식을 지키는가, 동시에 처리하는가.
#   buildlog : 빌드 환경에서 나온 오류 목록(msbuild 로그)을 읽어 오류 코드별로 묶고 대응 방법을 붙인다.
#              실행 폴더를 같이 주면 "이 도구가 고친 줄에서 난 오류"를 가려 준다.
# 한계: buildlog 는 분류와 안내만 한다. 오류를 자동으로 고치지 않는다.
# 수정: 오류 코드별 대응은 ERROR_GUIDE, 메시지 문구별 대응은 ERROR_HINTS 에 한 줄씩 더한다.
#       07_오류대응.md 의 표는 이 두 표에서 뽑은 것이다(vupdate buildlog --guide).
# ============================================================================
LOGT = Log("M12.부가명령")

ERROR_GUIDE = [
    # (오류 코드, 단계, 흔한 원인, 대응)
    ("MSB8020", "프로젝트", "프로젝트에 적힌 플랫폼 툴셋(v90 등)이 이 PC 에 없다", "프로젝트를 대상 Visual Studio 로 변환한다(솔루션 대상 변경). 변환하지 않은 프로젝트를 빌드한 경우다"),
    ("MSB8036", "프로젝트", "프로젝트에 적힌 Windows SDK 버전이 이 PC 에 없다", "프로젝트 속성의 Windows SDK 버전을 설치된 것으로 바꾸거나 그 SDK 를 설치한다"),
    ("MSB8031", "프로젝트", "MFC 를 멀티바이트 문자셋으로 빌드하는데 그 라이브러리가 없다(VS2013, VS2015)", "MFC 의 멀티바이트(MBCS) 라이브러리를 설치한다. 경고만이면 NO_WARN_MBCS_MFC_DEPRECATION 을 정의한다"),
    ("MSB8041", "프로젝트", "MFC 구성 요소가 설치되지 않았다", "Visual Studio 설치 관리자에서 MFC 를 추가한다(쓰는 툴셋과 플랫폼마다)"),
    ("MSB8042", "프로젝트", "ATL 구성 요소가 설치되지 않았다", "Visual Studio 설치 관리자에서 ATL 을 추가한다"),
    ("MSB4019", "프로젝트", "프로젝트가 가져오는 .props/.targets 파일이 없다", "구 버전 경로를 가리키는 Import 를 고치거나 프로젝트를 다시 변환한다"),
    ("MSB3073", "프로젝트", "빌드 전후 이벤트나 사용자 지정 빌드 단계의 명령이 실패했다", "명령 안의 경로와 도구(복사 대상 폴더, 구 버전 도구 경로)를 확인한다. 소스 문제가 아니다"),
    ("D8016", "옵션", "함께 쓸 수 없는 컴파일러 옵션이 지정됐다", "메시지에 나온 두 옵션 중 하나를 프로젝트 속성에서 뺀다"),
    ("D9002", "옵션", "알 수 없는 옵션을 무시했다(경고)", "없어진 옵션이다(/Wp64 등). 프로젝트 속성에서 빼면 사라진다. 그대로 둬도 빌드된다"),
    ("D9035", "옵션", "사용 중단된 옵션이다(경고)", "/Gm, /Zc:forScope- 등. 그대로 둬도 빌드된다. /Zc:forScope- 를 빼면 for 변수 범위 오류(C2065)가 날 수 있다"),
    ("C1083", "컴파일", "포함 파일이나 소스 파일을 열 수 없다", "가장 먼저 처리한다(뒤의 오류 대부분이 여기서 나온다). 포함 경로, SDK·MFC 설치, 외부 헤더 누락, 없어진 헤더인지 확인한다"),
    ("C1189", "컴파일", "헤더의 #error 에 걸렸다", "메시지 문구가 원인을 말해 준다. WINVER, 키워드 매크로, snprintf 매크로, hash_map, MFC 문자셋 등(아래 문구별 대응 참조)"),
    ("C1010", "컴파일", "미리 컴파일된 헤더를 찾다가 파일이 끝났다", "그 소스 맨 앞에 stdafx.h(pch.h) include 가 있는지, 또는 그 파일의 미리 컴파일된 헤더 사용을 끈다"),
    ("C1128", "컴파일", "개체 파일의 구역 수가 한도를 넘었다", "컴파일러 옵션 /bigobj 를 추가한다"),
    ("C1001", "컴파일", "컴파일러 내부 오류", "소스 문제가 아닐 수 있다. 해당 함수의 최적화를 끄거나 Visual Studio 를 최신으로 올린다"),
    ("C1047", "컴파일", "다른 컴파일러 버전으로 만든 개체나 .lib 가 섞였다(전체 프로그램 최적화)", "그 .lib 를 대상 툴셋으로 다시 빌드한다"),
    ("C2001", "컴파일", "문자열 상수 안에서 줄이 바뀌었다", "한글이 든 CP949 소스를 다른 코드 페이지로 읽은 경우가 많다. 빌드 PC 의 시스템 로캘을 한국어로 하거나 /source-charset:949 를 준다"),
    ("C4819", "컴파일", "현재 코드 페이지로 표시할 수 없는 문자가 있다(경고)", "C2001 과 같은 원인이다. 무시하면 뒤에서 구문 오류가 날 수 있다"),
    ("C2065", "컴파일", "선언되지 않은 식별자", "for 문 밖에서 쓴 루프 변수, 빠진 include, 없어진 전역 변수(_osver 등), x64 에서 없어지는 GWL_ 상수"),
    ("C3861", "컴파일", "식별자를 찾을 수 없다", "없어진 함수(gets 등), 빠진 include(<algorithm>, <iterator>), 준수 모드에서 템플릿 기본 클래스 멤버에 this-> 가 필요한 경우"),
    ("C2039", "컴파일", "그 이름은 해당 클래스나 이름공간의 멤버가 아니다", "std 의 멤버면 빠진 include(<algorithm>, <functional>, <iterator>) 또는 C++17 에서 없어진 기능(auto_ptr 등)"),
    ("C2440", "컴파일", "형식을 변환할 수 없다", "문자열 리터럴을 char* 로 받음(준수 모드), MFC 메시지 처리 함수의 시그니처, x64 의 포인터와 정수 변환, NULL 을 스마트 포인터에 대입"),
    ("C2664", "컴파일", "인자의 형식을 변환할 수 없다", "C2440 과 같은 부류다. 문자셋을 바꿨다면 char 와 wchar_t 의 불일치"),
    ("C2360", "컴파일", "case 레이블이 변수 초기화를 건너뛴다", "그 case 의 본문을 중괄호로 묶는다"),
    ("C2371", "컴파일", "재정의. 기본 형식이 다르다", "int8_t 등 stdint 이름의 자체 typedef(R-T3-STDINT-TYPEDEF). 가드로 감싼다"),
    ("C2628", "컴파일", "형식 뒤에 형식이 올 수 없다", "char16_t, char32_t, bool 등 내장 형식 이름의 자체 typedef(R-T3-CHAR16-TYPEDEF)"),
    ("C2011", "컴파일", "형식 재정의", "struct timespec 의 자체 정의(R-T3-TIMESPEC), winsock.h 와 winsock2.h 의 포함 순서(WIN32_LEAN_AND_MEAN)"),
    ("C2084", "컴파일", "함수에 이미 본문이 있다", "round, isnan 처럼 새 CRT 가 제공하게 된 이름의 자체 정의(R-T3-C99-MATH-FUNC)"),
    ("C2397", "컴파일", "좁히기 변환이 필요하다", "중괄호 초기화의 값이 대상 형식에 들어가지 않는다(R-T3-NARROWING-CHAR). 형식을 넓히거나 값을 캐스팅한다"),
    ("C2872", "컴파일", "모호한 기호", "using namespace std 와 Windows 헤더의 이름 충돌. byte 면 _HAS_STD_BYTE=0"),
    ("C2668", "컴파일", "오버로드 호출이 모호하다", "pow, sqrt, abs 같은 수학 함수에 정수 인자를 준 경우. 인자를 double 로 캐스팅한다"),
    ("C4430", "컴파일", "형식 지정자가 없다(int 로 보지 않는다)", "변수나 함수 선언에 형식을 적는다. 앞줄의 형식 이름을 모르는 경우(빠진 include)에도 난다"),
    ("C3688", "컴파일", "잘못된 리터럴 접미사", "문자열 리터럴 뒤에 매크로가 붙어 있다(R-T1-LITERAL-SPACE). 사이에 공백을 넣는다"),
    ("C2338", "컴파일", "static_assert 실패", "표준 라이브러리가 사용 조건을 검사한 것이다. 메시지 문구대로 고친다"),
    ("C2280", "컴파일", "삭제된 함수를 참조한다", "복사할 수 없는 객체(스트림, unique_ptr 등)를 복사하고 있다"),
    ("C2248", "컴파일", "private 멤버에 접근할 수 없다", "복사가 막힌 클래스(CObject 계열, 스트림)를 값으로 복사하고 있다"),
    ("C2143", "컴파일", "구문 오류: 무엇이 빠졌다", "대개 앞 오류의 여파다. 그 파일의 첫 오류부터 본다"),
    ("C2059", "컴파일", "구문 오류", "대개 앞 오류의 여파다. 그 파일의 첫 오류부터 본다"),
    ("C2146", "컴파일", "구문 오류: 식별자 앞에 무엇이 빠졌다", "앞의 형식 이름을 모르는 경우가 많다(빠진 include, 없어진 typedef)"),
    ("C2061", "컴파일", "구문 오류: 식별자", "형식 이름을 모른다. 빠진 include 를 찾는다"),
    ("C7510", "컴파일", "의존 형식 이름 앞에 typename 이 필요하다", "준수 모드의 검사다. typename 을 붙인다"),
    ("C4996", "컴파일", "사용 중단된 함수(경고. /WX 나 /sdl 이면 오류)", "project_settings.md 2절의 전처리기 정의를 적용한다. 메시지에 정의할 매크로 이름이 나온다"),
    ("C4703", "컴파일", "초기화되지 않았을 수 있는 지역 포인터(/sdl 이면 오류)", "선언할 때 NULL 로 초기화한다"),
    ("C4477", "컴파일", "printf 형식 문자열과 인자의 형식이 다르다(경고)", "형식 지정자를 인자에 맞춘다. x64 에서 size_t 는 %zu, 포인터는 %p"),
    ("C4267", "컴파일", "size_t 를 더 작은 형식으로 변환(경고)", "x64 에서 많이 난다. 빌드는 된다. /WX 면 형식을 맞추거나 캐스팅한다"),
    ("C4244", "컴파일", "변환 중 데이터 손실 가능(경고)", "빌드는 된다. /WX 면 형식을 맞추거나 캐스팅한다"),
    ("C4311", "컴파일", "포인터를 더 작은 정수로 자른다(경고)", "x64 문제다. INT_PTR, DWORD_PTR 로 바꾼다(R-T3-PTR-TRUNC)"),
    ("C5033", "컴파일", "register 는 더 이상 저장 클래스가 아니다(경고)", "조치 불필요. /wd5033 으로 끌 수 있다"),
    ("C5040", "컴파일", "동적 예외 지정은 C++14 까지만 유효하다(경고)", "조치 불필요. /wd5040 으로 끌 수 있다"),
    ("LNK2019", "링크", "확인할 수 없는 외부 기호", "구 툴셋으로 만든 .lib, wchar_t 설정 불일치, x86 과 x64 의 혼용, 프로젝트에서 빠진 소스. 기호 이름으로 가른다(아래 문구별 대응)"),
    ("LNK2001", "링크", "확인할 수 없는 외부 기호", "LNK2019 와 같다"),
    ("LNK1120", "링크", "확인할 수 없는 외부 참조의 개수(요약 줄)", "앞의 LNK2019, LNK2001 을 풀면 사라진다"),
    ("LNK2038", "링크", "불일치 감지(_MSC_VER, _ITERATOR_DEBUG_LEVEL, RuntimeLibrary)", "다른 버전이나 다른 설정으로 만든 .lib 가 섞였다. 모두 대상 툴셋과 같은 설정으로 다시 빌드한다"),
    ("LNK1104", "링크", "파일을 열 수 없다", "라이브러리 경로, 없어진 라이브러리(LIBC.lib), 설치되지 않은 MFC·ATL, 구 버전 이름의 .lib(mfc90.lib 등을 요구하는 구 개체)"),
    ("LNK1181", "링크", "입력 파일을 열 수 없다", "추가 종속성에 적힌 .lib 의 경로를 확인한다"),
    ("LNK1112", "링크", "모듈의 컴퓨터 종류가 대상과 다르다", "x86 과 x64 개체가 섞였다. 플랫폼에 맞는 .lib 를 쓴다"),
    ("LNK2005", "링크", "기호가 이미 정의돼 있다", "런타임 라이브러리 혼용(/MT 와 /MD), 헤더에 정의된 변수나 함수, MFC 와 CRT 라이브러리의 순서"),
    ("LNK2026", "링크", "모듈이 SAFESEH 이미지에 안전하지 않다", "구 어셈블리 개체다. 링커 옵션 /SAFESEH:NO 를 주거나 그 개체를 다시 만든다"),
    ("LNK2011", "링크", "미리 컴파일된 개체가 링크되지 않았다", "미리 컴파일된 헤더를 만든 개체(stdafx.obj)를 라이브러리와 함께 링크한다"),
    ("LNK4098", "링크", "기본 라이브러리가 다른 라이브러리와 충돌한다(경고)", "런타임 라이브러리 설정(/MT, /MD)을 모든 프로젝트와 .lib 에서 통일한다"),
    ("LNK1107", "링크", "파일이 잘못됐거나 손상됐다", ".lib 자리에 다른 형식의 파일이 지정됐다"),
    ("RC1015", "리소스", "리소스 컴파일러가 포함 파일을 열 수 없다", "afxres.h 면 MFC 미설치이거나 MFC 를 쓰지 않는 프로젝트다(winres.h 로 바꾼다)"),
    ("RC2104", "리소스", "정의되지 않은 키워드나 키 이름", "리소스 ID 를 정의한 헤더(resource.h)의 include 와 인코딩을 확인한다"),
    ("RC2135", "리소스", "파일을 찾을 수 없다", "아이콘, 비트맵 같은 리소스 파일의 경로를 확인한다"),
    ("CVT1100", "리소스", "리소스가 중복됐다", "매니페스트가 .rc 와 자동 생성 양쪽에 들어간 경우가 많다. 한쪽을 뺀다"),
]

ERROR_HINTS = [
    # (오류 코드 정규식, 메시지에서 찾을 문구(정규식), 대응)
    (r"C1189", r"WINVER|_WIN32_WINNT", "WINVER/_WIN32_WINNT 값을 0x0501 이상으로 올린다(R-T2-WINVER-OLD)"),
    (r"C1189", r"macroiz", "키워드를 매크로로 정의했다. _ALLOW_KEYWORD_MACROS 를 정의하거나 매크로를 없앤다(R-T0-KEYWORD-MACRO)"),
    (r"C1189", r"snprintf", "snprintf/vsnprintf 매크로 정의를 구 컴파일러 전용으로 감싼다(R-T1-SNPRINTF-MACRO)"),
    (r"C1189", r"hash_map|hash_set", "_SILENCE_STDEXT_HASH_DEPRECATION_WARNINGS 를 정의하거나 unordered_map 으로 바꾼다"),
    (r"C1189", r"experimental/filesystem", "_SILENCE_EXPERIMENTAL_FILESYSTEM_DEPRECATION_WARNING 을 정의하거나 <filesystem> 으로 바꾼다"),
    (r"C1189", r"MBCS|[Uu]nicode", "MFC 의 멀티바이트 라이브러리를 설치하거나 문자셋 설정을 확인한다"),
    (r"C1083", r"afx\w*\.h|atl\w*\.h", "MFC/ATL 구성 요소가 설치되지 않았다"),
    (r"C1083", r"stdafx\.h|pch\.h", "미리 컴파일된 헤더의 포함 경로를 확인한다"),
    (r"C1083", r"iostream\.h|fstream\.h|iomanip\.h|strstrea\.h", "VC6 시절의 구 iostream 헤더다. <iostream> 등으로 바꾸고 std:: 를 쓴다"),
    (r"C1083", r"winable\.h", "<winuser.h> 로 바꾼다(R-T2-WINABLE-H)"),
    (r"C1083", r"hash_map|hash_set", "헤더가 없어진 판이다. unordered_map/unordered_set 으로 바꾼다"),
    (r"LNK2019|LNK2001", r"__iob_func|\b_iob\b|__imp___iob", "VS2015 이전에 만든 .lib 가 없어진 CRT 내부(_iob)를 참조한다. 그 .lib 를 대상 툴셋으로 다시 빌드한다. 소스가 없으면 제공처에 새 바이너리를 요청하거나 구 컴파일러로 만든 별도 DLL 뒤로 감싼다. legacy_stdio_definitions.lib 로는 이 기호가 풀리지 않는다"),
    (r"LNK2019|LNK2001", r"_{1,2}(?:imp_{1,3})?v?s?n?w?(?:printf|scanf)\b", "VS2015 이전에 만든 .lib 가 stdio 함수를 참조한다. legacy_stdio_definitions.lib 를 링크하거나 다시 빌드한다"),
    (r"LNK2019|LNK2001", r"unsigned short const \*|wchar_t const \*|unsigned short \*", "wchar_t 를 내장 형식으로 볼지(/Zc:wchar_t)가 .lib 와 다르다. 양쪽 설정을 맞춘다"),
    (r"LNK2019|LNK2001", r"__security_check_cookie|__GSHandlerCheck|_RTC_", "구 툴셋으로 만든 개체다. 대상 툴셋으로 다시 빌드한다"),
    (r"LNK1104", r"mfc\w*\.lib|nafxcw|uafxcw|atls?\w*\.lib", "MFC/ATL 라이브러리가 없다. 구성 요소를 설치한다. 이름에 구 버전 번호(mfc90 등)가 있으면 그것을 요구하는 구 개체를 다시 빌드한다"),
    (r"LNK1104", r"libcd?\.lib", "단일 스레드 CRT(/ML)는 없어졌다. /MT 로 바꾸고, 그 lib 를 요구하는 구 .lib 는 다시 빌드한다"),
    (r"LNK2038", r"_MSC_VER", "다른 Visual Studio 버전으로 만든 .lib/.obj 가 섞였다. 전부 대상 툴셋으로 다시 빌드한다"),
    (r"LNK2038", r"_ITERATOR_DEBUG_LEVEL", "Debug 와 Release 가 섞였거나 _SECURE_SCL 값이 다르다"),
    (r"LNK2038", r"RuntimeLibrary", "/MT, /MD, /MTd, /MDd 가 섞였다. 하나로 통일한다"),
    (r"C2065|C3861", r"GWL_|DWL_|GCL_", "x64 에서는 GWLP_/DWLP_ 상수와 ...LongPtr 함수를 쓴다(R-T3-WINDOWLONG)"),
    (r"C2065|C3861", r"\bgets\b", "gets 는 없어졌다. gets_s 나 fgets 로 바꾼다(R-T3-GETS-REMOVED)"),
    (r"C2065|C3861|C2039", r"'(?:min|max)'", "<algorithm> 을 포함한다. Windows 헤더의 min/max 매크로와 충돌하면 NOMINMAX 를 정의한다"),
    (r"C2039", r"auto_ptr|bind1st|bind2nd|ptr_fun|mem_fun|random_shuffle|unary_function|binary_function", "C++17 에서 없어진 기능이다. _HAS_AUTO_PTR_ETC=1 을 정의하거나 언어 표준을 C++14 로 둔다"),
    (r"C2039|C2653|C2871", r"\btr1\b", "std::tr1 을 std 로 바꾼다(R-T1-TR1-NAMESPACE). 또는 _HAS_TR1_NAMESPACE=1"),
    (r"C2872", r"\bbyte\b", "전처리기 정의 _HAS_STD_BYTE=0 을 추가하거나 using namespace std 를 없앤다"),
    (r"C2440|C2664", r"const char \[|const wchar_t \[", "문자열 리터럴을 char* 로 받고 있다. 컴파일러 옵션 /Zc:strictStrings- 를 주거나 const 로 고친다"),
    (r"C4996", r"_CRT_SECURE_NO_WARNINGS", "전처리기 정의 _CRT_SECURE_NO_WARNINGS"),
    (r"C4996", r"_CRT_NONSTDC_NO|POSIX", "전처리기 정의 _CRT_NONSTDC_NO_DEPRECATE 또는 밑줄 붙은 이름으로 치환(--deprecated fix)"),
    (r"C4996", r"_WINSOCK_DEPRECATED_NO_WARNINGS", "전처리기 정의 _WINSOCK_DEPRECATED_NO_WARNINGS"),
    (r"C4996", r"wcstok", "전처리기 정의 _CRT_NON_CONFORMING_WCSTOK"),
    (r"C4996", r"_SILENCE_\w+", "메시지에 나온 _SILENCE_... 매크로를 정의한다"),
]

_BUILD_DIAG_RE = re.compile(
    r"^(?:\s*\d+>)?\s*(?P<origin>.*?)\s*:\s*(?:Command line\s+|명령줄\s+)?(?P<kind>fatal error|error|warning|심각한 오류|오류|경고)\s+"
    r"(?P<code>[A-Z]{1,4}\d{3,5})\s*:\s*(?P<msg>.*?)(?:\s+\[(?P<proj>[^\]\[]+\.\w*proj)\])?\s*$")
_ORIGIN_LINE_RE = re.compile(r"^(?P<file>.*?)\((?P<line>\d+)(?:[,\-]\d+)*\)$")
_STAGE_ORDER = ["프로젝트", "옵션", "컴파일", "리소스", "링크", "기타"]


def read_log_text(path):
    """빌드 로그를 읽는다. msbuild 의 파일 로그는 설정에 따라 UTF-8, UTF-16, 시스템 코드 페이지(CP949)다."""
    data = read_bytes(path)
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", "replace")
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def parse_build_log(text):
    """빌드 로그에서 진단 줄(오류, 경고)을 뽑는다. 같은 진단이 여러 번 나오면(구성별, 끝의 요약) 한 번만 남긴다.
    돌려주는 값: [{file, line, kind(error/warning), code, msg, proj}]"""
    out, seen = [], set()
    for raw in text.splitlines():
        m = _BUILD_DIAG_RE.match(raw.rstrip())
        if not m:
            continue
        origin, line = m.group("origin").strip(), 0
        om = _ORIGIN_LINE_RE.match(origin)
        if om:
            origin, line = om.group("file").strip(), int(om.group("line"))
        kind = "warning" if m.group("kind") in ("warning", "경고") else "error"
        key = (origin.lower(), line, m.group("code"), m.group("msg"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"file": origin, "line": line, "kind": kind, "code": m.group("code"), "msg": m.group("msg").strip(),
                    "proj": (m.group("proj") or "").strip()})
    return out


def error_stage(code):
    """오류 코드가 어느 단계의 것인지. 표에 없으면 머리글자로 가른다."""
    for c, stage, _cause, _action in ERROR_GUIDE:
        if c == code:
            return stage
    for prefix, stage in (("MSB", "프로젝트"), ("LNK", "링크"), ("CVT", "리소스"), ("RC", "리소스"), ("MIDL", "컴파일"),
                          ("D", "옵션"), ("C", "컴파일")):
        if code.startswith(prefix):
            return stage
    return "기타"


def error_advice(code, msg):
    """오류 하나의 대응 안내. 돌려주는 값: (원인, 대응, 표에 있는지)"""
    cause, action, known = "", "", False
    for c, _stage, ca, ac in ERROR_GUIDE:
        if c == code:
            cause, action, known = ca, ac, True
            break
    for crx, mrx, hint in ERROR_HINTS:
        if re.fullmatch(crx, code) and re.search(mrx, msg):
            action = hint                              # 메시지 문구로 더 좁혀진 대응이 있으면 그것을 쓴다
            known = True
            break
    if not known:
        cause = "대응표에 없는 코드"
        action = "메시지를 보고 판단한다. 같은 파일의 앞선 오류가 있으면 그것부터 푼다"
    return cause, action, known


def _changed_lines(old_text, new_text):
    """new_text 에서 old_text 와 달라진 줄 번호의 집합."""
    a, b = old_text.split("\n"), new_text.split("\n")
    if len(a) > 200000 or len(b) > 200000:
        return set()
    out = set()
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.update(range(j1 + 1, max(j2, j1 + 1) + 1))
    return out


def load_run_view(run_dir):
    """실행 폴더에서 빌드 오류와 대조할 자료를 읽는다: 파일 상태, 이 도구가 바꾼 줄, 수동 검토로 남긴 줄."""
    st = Store(os.path.join(run_dir, "state.db"))
    try:
        files = {r["path"]: r["status"] for r in st.q("SELECT path, status FROM files")}
        manual = {}
        for r in st.q("SELECT path, line, rule_id FROM issues WHERE status IN ('manual','open') AND tier!='T0'"):
            manual.setdefault(r["path"], []).append((r["line"], r["rule_id"]))
    finally:
        st.close()
    changed = {}
    for rel, status in files.items():
        if status not in ("변경됨", "부분수정"):
            continue
        p_old = os.path.join(run_dir, "snapshot", *rel.split("/"))
        p_new = os.path.join(run_dir, "out", "changed", *rel.split("/"))
        if os.path.isfile(p_old) and os.path.isfile(p_new):
            d_old, d_new = make_doc(rel, read_bytes(p_old), analyze=False), make_doc(rel, read_bytes(p_new), analyze=False)
            if d_old.ok and d_new.ok:
                changed[rel] = _changed_lines(d_old.text, d_new.text)
    return {"files": files, "changed": changed, "manual": manual}


def _match_rel(log_path, rels_lower):
    """로그에 나온 경로를 패키지의 상대 경로로 바꾼다(경로 끝이 가장 길게 맞는 것). 못 찾으면 빈 문자열."""
    p = norm_rel(log_path).lower()
    best = ""
    for low, rel in rels_lower.items():
        if (p == low or p.endswith("/" + low)) and len(low) > len(best):
            best = low
    return rels_lower.get(best, "")


def triage_build_log(diags, view=None):
    """진단 목록에 단계, 대응, (실행 폴더가 있으면) 이 도구의 수정과의 관계를 붙인다."""
    rels_lower = {rel.lower(): rel for rel in (view or {}).get("files", {})}
    first_in_file = set()
    for d in diags:
        d["stage"] = error_stage(d["code"])
        d["cause"], d["action"], d["known"] = error_advice(d["code"], d["msg"])
        d["rel"], d["relation"] = "", ""
        if d["kind"] == "error" and d["file"] and d["stage"] == "컴파일":
            key = d["file"].lower()
            d["first"] = key not in first_in_file
            first_in_file.add(key)
        else:
            d["first"] = False
        if not view or not d["file"]:
            continue
        rel = _match_rel(d["file"], rels_lower)
        if not rel:
            continue
        d["rel"] = rel
        near_edit = any(abs(d["line"] - ln) <= 2 for ln in view["changed"].get(rel, ()))
        near_manual = [rid for ln, rid in view["manual"].get(rel, ()) if abs(d["line"] - ln) <= 5]
        if d["line"] and near_edit:
            d["relation"] = "이 도구가 고친 줄"
        elif d["line"] and near_manual:
            d["relation"] = "수동 검토로 남긴 곳(%s)" % near_manual[0]
        elif view["files"].get(rel) in ("변경됨", "부분수정"):
            d["relation"] = "이 도구가 고친 파일의 다른 줄"
        else:
            d["relation"] = "이 도구가 고치지 않은 파일"
    return diags


def build_log_report(diags, log_path, run_dir=""):
    """분류 결과를 문서(markdown)로 만든다."""
    errs = [d for d in diags if d["kind"] == "error"]
    warns = [d for d in diags if d["kind"] != "error"]
    L = ["# 빌드 오류 분류", "", "- 로그: `%s`" % log_path]
    if run_dir:
        L.append("- 대조한 실행 폴더: `%s`" % run_dir)
    L += ["- 오류 %d건, 경고 %d건(같은 줄의 반복은 한 번으로 셈)" % (len(errs), len(warns)), "",
          "## 1. 처리 순서", "",
          "1. 프로젝트·옵션 단계의 오류를 먼저 푼다. 여기서 막히면 뒤의 오류는 의미가 없다.",
          "2. 컴파일 오류는 C1083(파일을 열 수 없음)부터, 그다음 파일마다 **첫 오류**만 본다. 같은 파일의 뒤 오류는 여파인 경우가 많다.",
          "3. 한 묶음을 고치면 다시 빌드한다. 오류 수가 줄지 않는 수정은 되돌린다.",
          "4. 링크 오류는 컴파일 오류가 모두 없어진 뒤에 본다.", "", "## 2. 단계별 건수", ""]
    by_stage = OrderedDict((s, 0) for s in _STAGE_ORDER)
    for d in errs:
        by_stage[d["stage"]] = by_stage.get(d["stage"], 0) + 1
    L += _table(["단계", "오류 수"], [(s, n) for s, n in by_stage.items() if n]) if errs else ["오류가 없다."]
    codes = OrderedDict()
    for d in sorted(errs, key=lambda x: (_STAGE_ORDER.index(x["stage"]), x["code"] != "C1083", x["code"])):
        c = codes.setdefault(d["code"], {"n": 0, "files": set(), "stage": d["stage"], "cause": d["cause"], "actions": []})
        c["n"] += 1
        c["files"].add(d["file"].lower())
        if d["action"] not in c["actions"]:
            c["actions"].append(d["action"])
    L += ["", "## 3. 오류 코드별 대응", ""]
    if codes:
        L += _table(["코드", "단계", "건수", "파일 수", "흔한 원인", "대응"], [
            (code, c["stage"], c["n"], len(c["files"]), c["cause"], " / ".join(c["actions"][:3])) for code, c in codes.items()])
    else:
        L.append("없음.")
    firsts = [d for d in errs if d.get("first")]
    L += ["", "## 4. 파일별 첫 오류(먼저 볼 것)", ""]
    if firsts:
        L += _table(["파일", "줄", "코드", "메시지", "이 도구의 수정과의 관계"], [
            (d["rel"] or d["file"], d["line"], d["code"], d["msg"][:110], d["relation"] or "-") for d in firsts[:200]])
        if len(firsts) > 200:
            L.append("")
            L.append("(파일이 많아 200개까지만 적었다. 전체는 build_errors.csv)")
    else:
        L.append("없음.")
    if run_dir:
        mine = [d for d in errs if d["relation"] == "이 도구가 고친 줄"]
        L += ["", "## 5. 이 도구가 고친 줄에서 난 오류", "",
              "수정이 원인일 수 있다. 실행 폴더의 `out/diff/` 에서 그 줄의 변경을 확인하고, 틀렸으면 원본 줄로 되돌린다. "
              "같은 유형이 반복되면 해당 규칙을 고친다.", ""]
        L += _table(["파일", "줄", "코드", "메시지"], [(d["rel"], d["line"], d["code"], d["msg"][:120]) for d in mine[:100]]) \
            if mine else ["없음."]
        rel_n = OrderedDict()
        for d in errs:
            if d["relation"]:
                rel_n[d["relation"].split("(")[0]] = rel_n.get(d["relation"].split("(")[0], 0) + 1
        L += ["", "관계별 건수: " + (", ".join("%s %d건" % kv for kv in rel_n.items()) or "대조할 수 있는 오류가 없다")]
    unknown = sorted({d["code"] for d in errs if not d["known"]})
    L += ["", "## %d. 대응표에 없는 코드" % (6 if run_dir else 5), ""]
    L += [", ".join(unknown), "", "반복해서 나오는 코드는 ERROR_GUIDE 에 대응을 더한다(04_개발구조.md 15절)."] if unknown else ["없음."]
    wc = OrderedDict()
    for d in warns:
        wc[d["code"]] = wc.get(d["code"], 0) + 1
    L += ["", "## %d. 경고 요약" % (7 if run_dir else 6), ""]
    L += _table(["코드", "건수", "대응"], [(c, n, error_advice(c, "")[1]) for c, n in sorted(wc.items(), key=lambda kv: -kv[1])[:30]]) \
        if wc else ["없음."]
    return "\n".join(L) + "\n"


_HINT_LABELS = {
    # ERROR_HINTS 의 정규식 중 표에 그대로 싣기 어려운 것의 읽기용 표기(error_guide_markdown 전용. 판정에는 쓰이지 않는다)
    r"__iob_func|\b_iob\b|__imp___iob": "`__iob_func` 또는 `_iob`",
    r"_{1,2}(?:imp_{1,3})?v?s?n?w?(?:printf|scanf)\b": "printf, scanf 계열 함수 이름(`_sprintf`, `__imp__fprintf` 등)",
    r"'(?:min|max)'": "`'min'` 또는 `'max'`",
    r"MBCS|[Uu]nicode": "`MBCS` 또는 `Unicode`",
    r"libcd?\.lib": "`libc.lib` 또는 `libcd.lib`",
    r"mfc\w*\.lib|nafxcw|uafxcw|atls?\w*\.lib": "`mfc...lib`, `nafxcw`, `uafxcw`, `atl...lib`",
    r"afx\w*\.h|atl\w*\.h": "`afx...h` 또는 `atl...h`",
    r"_SILENCE_\w+": "`_SILENCE_` 로 시작하는 이름",
}


def _hint_label(rx):
    """메시지 문구의 정규식을 문서용 표에 실을 글로 바꾼다. 읽기용 표기가 있으면 그것을, 없으면 기호를 걷어 낸 것을 쓴다."""
    if rx in _HINT_LABELS:
        return _HINT_LABELS[rx]
    plain = rx.replace("\\b", "").replace("\\.", ".").replace("\\[", "[").replace("\\*", "*")
    return " 또는 ".join("`%s`" % x for x in plain.split("|"))


def error_guide_markdown():
    """대응표를 문서용 표로 낸다(07_오류대응.md 에 싣는 표)."""
    L = ["### 오류 코드별 대응", ""]
    L += _table(["코드", "단계", "흔한 원인", "대응"], ERROR_GUIDE)
    L += ["", "### 메시지 문구별 대응", "", "같은 코드라도 메시지에 아래 문구가 있으면 이 대응이 우선한다.", ""]
    L += _table(["코드", "메시지에 있는 문구", "대응"], [(c.replace("|", ", "), _hint_label(m), h) for c, m, h in ERROR_HINTS])
    return "\n".join(L) + "\n"


def cmd_buildlog(args):
    if getattr(args, "guide", False):
        print(error_guide_markdown())
        return EXIT_OK
    if not args.log:
        raise UserInputError("--log 로 빌드 로그 파일을 지정하세요(msbuild 의 /fl 로 만든 msbuild.log 등).")
    log_path = os.path.abspath(args.log)
    if not os.path.isfile(log_path):
        raise UserInputError("빌드 로그 파일이 없습니다: %s" % log_path)
    run_dir = os.path.abspath(args.run) if getattr(args, "run", None) else ""
    if run_dir and not os.path.isfile(os.path.join(run_dir, "state.db")):
        raise UserInputError("실행 폴더가 아닙니다(state.db 없음): %s" % run_dir)
    out_dir = os.path.abspath(args.out) if getattr(args, "out", None) else (
        os.path.join(run_dir, "out") if run_dir else os.path.dirname(log_path))
    LOGT.info("빌드 로그 읽기: %s" % log_path)
    diags = parse_build_log(read_log_text(log_path))
    view = load_run_view(run_dir) if run_dir else None
    triage_build_log(diags, view)
    n_err = sum(1 for d in diags if d["kind"] == "error")
    write_text(os.path.join(out_dir, "build_errors.md"), build_log_report(diags, log_path, run_dir))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "build_errors.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["종류", "단계", "코드", "파일", "줄", "메시지", "프로젝트", "패키지 경로", "이 도구의 수정과의 관계", "대응"])
        for d in diags:
            w.writerow(["오류" if d["kind"] == "error" else "경고", d["stage"], d["code"], d["file"], d["line"], d["msg"],
                        d["proj"], d["rel"], d["relation"], d["action"]])
    LOGT.info("오류 %d건, 경고 %d건. 대응표에 없는 코드 %d종" % (
        n_err, len(diags) - n_err, len({d["code"] for d in diags if d["kind"] == "error" and not d["known"]})))
    LOGT.info("분류 결과: %s" % os.path.join(out_dir, "build_errors.md"))
    if not diags:
        LOGT.warn("진단 줄을 하나도 찾지 못했습니다. msbuild 로그가 맞는지, 인코딩이 맞는지 확인하세요")
    return EXIT_REVIEW if n_err else EXIT_OK


_CHECK_CODE = "\n".join(["#pragma once", "// 공용 정의", "typedef unsigned char uint8_t;", "typedef unsigned short uint16_t;",
                         "#define IDX_TEMP 0", "extern int g_data[10];"])


def _check_filler(n_tokens):
    """추정 토큰 수가 대략 n_tokens 인 채움 본문(코드 모양)."""
    line = "    value = buffer[index] + offset * scale;  // 채움 줄\n"
    return line * max(1, int(n_tokens / max(1, est_tokens(line) - 8)))


def run_llmcheck(llm, skip_over_limit=False):
    """서버 점검. 돌려주는 값: (점검 줄 목록 [(항목, 판정, 설명)], 설정 제안 목록, 호출 자체가 됐는지)
    판정: 정상 / 주의(동작은 하나 이 도구의 기능 일부가 제한됨) / 실패"""
    rows, tips = [], []
    small = dict(llm, max_output_tokens=16)

    def add(item, verdict, text):
        rows.append((item, verdict, text))
        (LOGT.info if verdict == "정상" else LOGT.warn)("%s: %s - %s" % (item, verdict, text))

    # 1) 모델 목록
    lim, ids = llm_probe(llm)
    if ids:
        add("모델 목록", "정상" if llm["model"] in ids else "주의",
            "서버 모델: %s%s" % (", ".join(ids[:5]), "" if llm["model"] in ids else ". 설정의 모델 이름(%s)이 목록에 없다" % llm["model"]))
    else:
        add("모델 목록", "주의", "서버가 모델 목록(/models)을 주지 않는다. 한도는 설정 파일의 값으로 시작한다")
    if lim:
        add("모델 목록의 토큰 한도", "정상", "서버가 알려 준 한도 %d (설정값 %d). 작은 쪽을 쓴다" % (lim, llm["max_total_tokens"]))
        if lim < llm["max_total_tokens"]:
            tips.append("llm.max_total_tokens 를 %d 로 낮춘다(서버 값)" % lim)
    # 2) 작은 요청
    sys_p, usr_p = "짧게 답한다.", "OK 라고만 답하라."
    ok, content, ms1, info = llm_chat(small, sys_p, usr_p)
    if not ok:
        add("기본 호출", "실패", "%s (종류: %s)" % (content[:300], info.get("kind") or "-"))
        return rows, tips, False
    add("기본 호출", "정상", "응답 %d ms, 내용 %r" % (ms1, content[:40]))
    if info.get("think"):
        add("추론 블록", "주의", "응답에 <think> 블록이 있다. 지우고 쓰지만 출력 토큰을 쓴다. 서버에서 추론 모드를 끄는 것을 권한다")
    e1, a1 = est_tokens(sys_p + usr_p), info.get("in")
    # 3) 중간 크기 요청: 토큰 수를 돌려주는지, 추정과 얼마나 다른지
    usr2 = "아래 코드를 읽고 OK 라고만 답하라.\n" + _check_filler(1500)
    ok2, _c2, _ms2, info2 = llm_chat(small, sys_p, usr2)
    e2, a2 = est_tokens(sys_p + usr2), info2.get("in") if ok2 else None
    if a1 and a2 and e2 > e1:
        ratio = (a2 - a1) / float(e2 - e1)
        fixed = int(round(a1 - ratio * e1))
        add("토큰 수 반환", "정상", "응답에 실제 입력 토큰 수가 온다. 추정 %d -> 실제 %d, 추정 %d -> 실제 %d" % (e1, a1, e2, a2))
        add("추정과의 차이", "정상" if 0.6 <= ratio <= 1.1 else "주의",
            "실제 = 약 %.2f x 추정 + %d토큰. 앞의 수는 토크나이저 차이, 뒤의 수는 요청마다 붙는 고정분(서버 프롬프트, 대화 틀)이다. "
            "실행 중에 자동으로 보정한다%s" % (ratio, fixed, "" if ratio <= 1.1 else ". 추정이 실제보다 작으므로 처음 몇 건은 거절될 수 있다"))
        if fixed >= 200:
            tips.append("llm.server_prompt_reserve_tokens 를 %d 로 둔다(고정분 %d토큰을 넉넉히 올림)" % ((fixed // 500 + 1) * 500, fixed))
    else:
        add("토큰 수 반환", "주의", "응답에 실제 입력 토큰 수(usage.prompt_tokens)가 없다. 추정 보정을 하지 못하고, "
            "한도 초과는 서버의 거절로만 알 수 있다")
    # 4) 출력 형식: 실제 작업 프롬프트로 수정 블록을 받아 본다
    usr4 = USER_TEMPLATE.format(
        env="- VS 제품: 2008 -> 2022", targets="- 3번째 줄 [R-T3-STDINT-TYPEDEF]: typedef unsigned char uint8_t;\n"
        "- 4번째 줄 [R-T3-STDINT-TYPEDEF]: typedef unsigned short uint16_t;",
        guidance="- [R-T3-STDINT-TYPEDEF] stdint 타입 이름의 자체 typedef: 해당 typedef 줄을 바로 위의 "
        "'#if !defined(_MSC_VER) || _MSC_VER < 1600' 줄과 바로 아래의 '#endif' 줄로 감싼다. typedef 줄 자체는 바꾸지 않는다. "
        "연속한 typedef 줄은 한 번에 감싼다.", context="", extra="", feedback="", path="Inc/Common.h", start_line=1,
        end_line=6, code=_CHECK_CODE)
    fmt_cfg = dict(llm, max_output_tokens=min(600, int(llm["max_output_tokens"])))
    ok4, c4, ms4, info4 = llm_chat(fmt_cfg, SYSTEM_PROMPT, usr4)
    if not ok4:
        add("출력 형식", "실패", "작업 프롬프트 호출 실패: %s" % c4[:200])
    else:
        blocks = _SR_RE.findall(c4)
        good = [b for b in blocks if b[0].strip() and b[0] in _CHECK_CODE and "#if" in b[1] and "#endif" in b[1]]
        if good:
            extra = "" if c4.strip().startswith("<<<<<<<") else " 다만 블록 앞에 다른 글이 있다(읽는 데는 지장 없다)"
            add("출력 형식", "정상", "수정 블록 형식을 지켰고 SEARCH 가 원문과 일치한다(%d ms).%s" % (ms4, extra))
        elif blocks:
            add("출력 형식", "주의", "수정 블록은 냈지만 SEARCH 가 원문과 다르거나 지침대로 고치지 않았다. 응답 첫 부분: %r" % c4[:160])
        elif "NO_CHANGE" in c4 or _NEED_RE.search(c4):
            add("출력 형식", "주의", "수정 블록 대신 %r 로 답했다. 고쳐야 하는 조각이었다" % c4.strip()[:60])
        else:
            add("출력 형식", "실패", "정해진 형식이 아니다. 응답 첫 부분: %r" % c4[:160])
        if info4.get("finish") == "length":
            add("출력 길이", "주의", "작은 조각인데도 응답이 출력 상한에서 잘렸다. 추론 모드가 켜져 있는지 확인한다")
        # 5) 같은 입력에 같은 응답인가(응답 재사용의 전제)
        ok5, c5, _ms5, _i5 = llm_chat(fmt_cfg, SYSTEM_PROMPT, usr4)
        if ok5:
            add("같은 입력에 같은 응답", "정상" if c5 == c4 else "주의",
                "두 번 보낸 응답이 같다" if c5 == c4 else "같은 요청의 응답이 달랐다. 다시 실행하면 결과가 달라질 수 있다(서버의 temperature 설정 확인)")
    # 6) 한도 초과: 일부러 넘겨 보내 거절 형식을 확인한다
    if skip_over_limit:
        add("한도 초과 거절", "주의", "점검을 건너뛰었다(--skip-over-limit)")
    else:
        big = "아래 코드를 읽고 OK 라고만 답하라.\n" + _check_filler(int(llm["max_total_tokens"] * 1.25))
        ok6, c6, _ms6, info6 = llm_chat(dict(llm, busy_retries=0), sys_p, big)
        e6 = est_tokens(sys_p + big)
        if not ok6 and info6.get("kind") == "over_limit":
            got = ", ".join(x for x in ("한도 %s" % info6["limit"] if info6.get("limit") else "",
                                        "실제 입력 %s토큰" % info6["asked_in"] if info6.get("asked_in") else "") if x)
            add("한도 초과 거절", "정상" if got else "주의",
                "서버가 거절했고 이 도구가 한도 초과로 알아본다. %s" % (("받은 수치: " + got) if got else
                "다만 수치(한도, 실제 토큰 수)는 읽지 못했다. 줄여서 다시 보내기는 하지만 얼마나 줄일지는 어림한다"))
            if info6.get("limit") and info6["limit"] != llm["max_total_tokens"]:
                tips.append("llm.max_total_tokens 를 %d 로 맞춘다(서버가 거절하며 알려 준 한도)" % info6["limit"])
        elif not ok6:
            add("한도 초과 거절", "주의", "서버가 거절했지만 한도 초과로 알아보지 못했다(종류 %s). 이 상태로는 줄여서 다시 보내지 않고 "
                "수동 검토로 보낸다. 아래 문구를 M06 의 classify_llm_error 에 더해야 한다: %s" % (info6.get("kind") or "-", c6[:300]))
        else:
            add("한도 초과 거절", "주의", "설정 한도(%d)의 1.25배(추정 %d토큰)를 보냈는데 서버가 받아들였다. 서버의 실제 한도가 더 크거나, "
                "앞단이 본문을 잘라 보냈을 수 있다. 잘라 보내는 구성이면 수정 결과를 믿을 수 없으므로 반드시 확인한다"
                % (llm["max_total_tokens"], e6))
    # 7) 동시 요청
    n = max(2, min(8, int(llm["max_parallel"])))
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=n) as ex:
        res = list(ex.map(lambda _k: llm_chat(small, sys_p, usr2), range(n)))
    total = int((time.time() - t0) * 1000)
    n_ok = sum(1 for r in res if r[0])
    one = max(1, _ms2 if ok2 else ms1)
    if n_ok < n:
        kinds = ", ".join(sorted({r[3].get("kind") or "-" for r in res if not r[0]}))
        add("동시 요청 %d건" % n, "주의", "%d건만 성공(실패 종류: %s). 허용 동시 수(llm.max_parallel)를 낮춘다" % (n_ok, kinds))
    else:
        par = total < 0.7 * n * one
        add("동시 요청 %d건" % n, "정상", "모두 성공. 전체 %d ms(1건은 약 %d ms). %s" % (
            total, one, "동시에 처리되는 것으로 보인다" if par else "차례로 처리되는 것으로 보인다. 동시 수를 늘려도 빨라지지 않는다"))
    return rows, tips, True


def cmd_llmcheck(args):
    cfg = load_server_config(getattr(args, "server_config", None))
    llm = cfg["llm"]
    ep = getattr(args, "llm_endpoint", None) or os.environ.get(ENV_LLM_ENDPOINT, "").strip() or llm.get("endpoint")
    if not ep:
        raise UserInputError("sLLM 주소가 없습니다. --llm-endpoint, 환경변수 %s, 서버 설정의 llm.endpoint 중 하나로 주세요."
                             % ENV_LLM_ENDPOINT)
    llm["endpoint"] = ep
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(os.path.abspath(getattr(args, "out", None) or os.path.join(os.getcwd(), "runs")), "_llmcheck", stamp)
    _SINK.open(os.path.join(out_dir, "llmcheck.log"))
    llm["_caller"] = (getattr(args, "caller", None) or "").strip() or default_caller()
    llm["_run_id"] = "llmcheck/" + stamp
    LOGT.info("서버 점검 시작: %s (모델 %s, 설정 한도 %d, 출력 상한 %d, 허용 동시 수 %d)"
              % (ep, llm["model"], llm["max_total_tokens"], llm["max_output_tokens"], llm["max_parallel"]))
    rows, tips, alive = run_llmcheck(llm, bool(getattr(args, "skip_over_limit", False)))
    L = ["# 서버 점검 결과", "", "- 주소: `%s`" % ep, "- 모델: %s" % llm["model"],
         "- 설정: 토큰 한도 %d, 출력 상한 %d, 허용 동시 수 %d" % (llm["max_total_tokens"], llm["max_output_tokens"], llm["max_parallel"]),
         "- 시각: %s" % time.strftime("%Y-%m-%d %H:%M:%S"), "", "## 1. 점검 항목", ""]
    L += _table(["항목", "판정", "설명"], rows)
    L += ["", "## 2. 설정에 반영할 것", ""] + (["- " + t for t in tips] if tips else ["없음."])
    L += ["", "## 3. 판정의 뜻", "", "- 정상: 이 도구가 기대하는 대로 동작한다.",
          "- 주의: 실행은 되지만 일부 기능이 제한된다. 설명에 적힌 영향을 확인한다.", "- 실패: 이 상태로는 sLLM 단계를 쓸 수 없다.", ""]
    path = os.path.join(out_dir, "llm_check.md")
    write_text(path, "\n".join(L))
    print("")
    print("===== 서버 점검 결과 =====")
    for item, verdict, text in rows:
        print("[%s] %s: %s" % (verdict, item, text))
    for t in tips:
        print("제안: %s" % t)
    LOGT.info("점검 결과 파일: %s" % path)
    if not alive or any(v == "실패" for _i, v, _t in rows):
        return EXIT_FAIL
    return EXIT_REVIEW if any(v == "주의" for _i, v, _t in rows) else EXIT_OK


# ============================================================================
# [M11 명령줄]  run / preflight / status / rules / selftest (llmcheck, buildlog 는 M12)
# 목적: 사용자가 부르는 진입점. 인자를 검증해 실행 문맥(ctx)을 만들고 실행기를 부른다.
# 종료 코드: 0 완료(수동 검토 없음) / 1 완료(수동 검토 있음) / 2 단계 실패 / 3 입력 오류
# ============================================================================
LOGA = Log("M11.명령줄")


def _make_ctx(args, need_env=True):
    """run/preflight 공통: 실행 폴더를 만들고 ctx 를 채운다.
    값의 우선순위: 명령줄 > 환경변수 > (이어서 실행할 때) 처음 실행의 값 > 서버 설정 파일 > 내장 기본값"""
    ctx = Ctx()
    LOGA.info("%s %s 시작 (Python %s, %s, %s)" % (APP, VERSION, sys.version.split()[0], sys.platform,
                                              "exe" if getattr(sys, "frozen", False) else "py"))
    resume = getattr(args, "resume", None)
    saved_opts = None
    if resume:
        ctx.run_dir = os.path.abspath(resume)
        info_path = os.path.join(ctx.run_dir, "run.json")
        if not os.path.isfile(info_path):
            raise UserInputError("재개할 실행 폴더가 아닙니다(run.json 없음): %s" % ctx.run_dir)
        info = load_json(info_path, "실행 정보")
        ctx.package, ctx.env, ctx.request = info["package"], info["env"], info["request"]
        ctx.dry_run = bool(info.get("dry_run"))
        saved_opts = info.get("opts") or {}
    else:
        if not args.package:
            raise UserInputError("--package 로 대상 패키지 폴더를 지정하세요.")
        ctx.package = os.path.abspath(args.package)
        if not os.path.isdir(ctx.package):
            raise UserInputError("패키지 폴더가 없습니다: %s" % ctx.package)
        if need_env:
            ctx.env = build_env(args)
            ctx.request = load_request(args)
        else:
            ctx.env = {"from_vs": 2008, "to_vs": 2022, "from_toolset": "v90", "to_toolset": "v143",
                       "cpp_standard": "c++14", "conformance": "off", "platform": "keep", "charset": "keep",
                       "policy": {"deprecated_warnings": "suppress"}, "axes": {}}
        ctx.dry_run = bool(getattr(args, "dry_run", False))
    ctx.opts = collect_opts(args, saved_opts)
    ctx.pkg_name = os.path.basename(ctx.package.rstrip("\\/")) or "package"
    if not resume:
        base = os.path.abspath(getattr(args, "out", None) or os.path.join(os.getcwd(), "runs"))
        ctx.run_dir = os.path.join(base, ctx.pkg_name, time.strftime("%Y%m%d_%H%M%S"))
        if os.path.exists(ctx.run_dir):
            ctx.run_dir += "_%d" % os.getpid()
    os.makedirs(ctx.run_dir, exist_ok=True)
    _SINK.open(ctx.p("logs", "run.log"))
    LOGA.info("실행 폴더: %s" % ctx.run_dir)
    LOGA.info("호출자: %s" % ctx.opts["caller"])

    # --- 서버 설정 + 실행 옵션 ---
    ctx.cfg = load_server_config(getattr(args, "server_config", None))
    llm, lim = ctx.cfg["llm"], ctx.cfg["limits"]
    ep_arg = getattr(args, "llm_endpoint", None)
    ep = ep_arg or os.environ.get(ENV_LLM_ENDPOINT, "").strip()
    if ep:
        llm["endpoint"] = ep
        LOGA.info("sLLM 주소: %s (%s)" % (ep, "--llm-endpoint" if ep_arg else "환경변수 " + ENV_LLM_ENDPOINT))
    allowed, want = max(1, int(llm["max_parallel"])), ctx.opts["max_parallel"]
    if want > allowed:
        LOGA.warn("--max-parallel %d 은(는) 서버 설정의 허용 동시 수 %d 을(를) 넘어 %d(으)로 낮춥니다. "
                  "올리려면 서버 설정의 llm.max_parallel 을 바꿉니다" % (want, allowed, allowed))
    llm["max_parallel"] = min(want, allowed) if want else allowed
    if ctx.opts["no_compile"]:
        ctx.cfg["compile"]["mode"] = "off"
    if ctx.opts["no_chunk"]:
        lim["chunk_enabled"] = False
    if ctx.opts["compile_parallel"] >= 0:
        lim["compile_parallel"] = ctx.opts["compile_parallel"]
    ctx.opts["vs_path"] = getattr(args, "vs_path", None) or os.environ.get(ENV_VS_PATH, "").strip()
    ctx.opts["sdk_path"] = getattr(args, "sdk_path", None) or os.environ.get(ENV_SDK_PATH, "").strip()
    if not ctx.opts["sdk_path"] and os.environ.get(ENV_SDK_PATH_TYPO, "").strip():
        ctx.opts["sdk_path"] = os.environ[ENV_SDK_PATH_TYPO].strip()
        LOGC.warn("환경변수 %s 의 값을 SDK 경로로 씁니다. 이름이 %s 의 오타로 보입니다. 바른 이름으로 고치세요" % (ENV_SDK_PATH_TYPO, ENV_SDK_PATH))
    ctx.exceptions = load_exceptions(ctx.opts.get("exceptions"))
    set_brace_macros(ctx.exceptions["brace_macros"])
    if ctx.opts["only"] or ctx.opts["exclude"]:
        LOGA.info("처리 범위: only=%s exclude=%s" % (ctx.opts["only"] or "(전체)", ctx.opts["exclude"] or "(없음)"))

    # --- 규칙: 이 환경값에서 켜지는 것만 고른다 ---
    all_rules = load_rules(getattr(args, "rules", None))
    ctx.rule_by_id = {r["id"]: r for r in all_rules}
    ctx.rules, ctx.rules_off = [], []
    off_ids = {x.upper() for x in list(ctx.opts.get("disable_rules") or []) + ctx.exceptions["rules_off"]}
    for r in all_rules:
        on, why = rule_state(r, ctx.env)
        if on and r["id"].upper() in off_ids:
            on, why = False, "실행 옵션(--disable-rule)이나 예외 파일에서 끔"
        if on:
            ctx.rules.append(r)
        else:
            ctx.rules_off.append((r["id"], r["title"], why))
    LOGA.info("규칙 %d개 중 이 환경값에서 켜진 것 %d개" % (len(all_rules), len(ctx.rules)))
    for rid, _title, why in ctx.rules_off:
        LOGA.debug("꺼진 규칙 %s: %s" % (rid, why))
    ctx.store = Store(ctx.p("state.db"))
    if not resume:
        safe_cfg = json.loads(json.dumps(ctx.cfg))
        safe_cfg["llm"]["api_key"] = "***" if safe_cfg["llm"].get("api_key") else ""
        write_text(ctx.p("run.json"), json.dumps({
            "app": APP, "version": VERSION, "package": ctx.package, "env": ctx.env, "request": ctx.request,
            "dry_run": ctx.dry_run, "opts": ctx.opts, "config": safe_cfg,
            "started": time.strftime("%Y-%m-%d %H:%M:%S"), "argv": sys.argv[1:]}, ensure_ascii=False, indent=2))
    llm["_caller"] = ctx.opts["caller"]           # 요청 머리말에 실어 보낸다(서버 기록용). 밑줄로 시작하는 값은 실행 중에만 쓴다
    llm["_run_id"] = "%s/%s" % (ctx.pkg_name, os.path.basename(ctx.run_dir))
    return ctx


def cmd_run(args):
    ctx = _make_ctx(args)
    order = [s for s, _n in STAGES]
    if ctx.dry_run:
        plan = ["S0", "S1", "S2", "S9"]
        LOGA.info("탐지만 수행합니다(--dry-run)")
    elif args.until:
        u = args.until.upper()
        if u not in order:
            raise UserInputError("--until 값이 올바르지 않습니다: %s (S0~S9)" % args.until)
        plan = order[:order.index(u) + 1]
        if "S9" not in plan:
            plan.append("S9")
    else:
        plan = order
    try:
        return run_pipeline(ctx, plan, resume=bool(args.resume))
    finally:
        ctx.store.close()


def cmd_preflight(args):
    ctx = _make_ctx(args, need_env=False)
    try:
        code = run_pipeline(ctx, ["S0", "S1"])
        if code == EXIT_FAIL:
            return code
        path = ctx.p("out", "preflight.md")
        with open(path, "r", encoding="utf-8") as f:
            print("\n" + f.read())
        LOGA.info("환경 점검 결과 파일: %s" % path)
        return EXIT_OK
    finally:
        ctx.store.close()


def cmd_status(args):
    run_dir = os.path.abspath(args.run)
    db = os.path.join(run_dir, "state.db")
    if not os.path.isfile(db):
        raise UserInputError("실행 폴더가 아닙니다(state.db 없음): %s" % run_dir)
    st = Store(db)
    try:
        print("실행 폴더 : %s" % run_dir)
        print("끝난 단계 : %s" % (", ".join(st.get_meta("stages_done", [])) or "없음"))
        print("실패 단계 : %s" % (st.get_meta("failed_stage", "") or "없음"))
        print("환경 등급 : %s" % st.get_meta("grade", "-"))
        print("파일 상태 : " + ", ".join("%s %d" % (r["status"], r["n"]) for r in
                                      st.q("SELECT status, COUNT(*) AS n FROM files GROUP BY status")))
        print("이슈 상태 : " + ", ".join("%s/%s %d" % (r["tier"], r["status"], r["n"]) for r in
                                      st.q("SELECT tier, status, COUNT(*) AS n FROM issues GROUP BY tier, status")))
        print("수정 상태 : " + ", ".join("%s/%s %d" % (r["origin"], r["status"], r["n"]) for r in
                                      st.q("SELECT origin, status, COUNT(*) AS n FROM edits GROUP BY origin, status")))
    finally:
        st.close()
    return EXIT_OK


def cmd_rules(args):
    """규칙 목록. --from-vs/--to-vs(또는 --profile)를 주면 그 환경값에서 켜지는지와 꺼진 이유도 보여 준다.
    --coverage 를 주면 알려진 변경 항목과 처리 방식의 표(커버리지 표)를 낸다."""
    rules = load_rules(getattr(args, "rules", None))
    if getattr(args, "test", False):
        res = run_rule_tests(rules)
        for rid, no, ok, msg in res:
            print("[%s] %s #%d: %s" % ("통과" if ok else "실패", rid, no, msg))
        tested = len({rid for rid, _n, _o, _m in res})
        print("규칙 %d개 중 시험 예가 있는 규칙 %d개, 시험 예 %d건 중 %d건 통과"
              % (len(rules), tested, len(res), sum(1 for _r, _n, ok, _m in res if ok)))
        return EXIT_OK if all(ok for _r, _n, ok, _m in res) else EXIT_REVIEW
    if getattr(args, "coverage", False):
        print("\n".join(_table(["기준", "분류", "변경 항목", "처리 방식", "규칙 또는 오류 코드", "비고"], coverage_rows(rules))))
        cov = coverage_summary()
        print("")
        print("알려진 변경 항목 %d건: %s" % (sum(cov.values()), ", ".join("%s %d건" % kv for kv in cov.items())))
        print("기준은 문제가 생긴 VS 연도다. x64, 문자셋, 인코딩은 버전과 무관한 축이다.")
        return EXIT_OK
    env = None
    if getattr(args, "profile", None) or getattr(args, "from_vs", None) or getattr(args, "to_vs", None):
        env = build_env(args)
    print("%-22s %-3s %-6s %-34s %s" % ("규칙 ID", "단계", "상태" if env else "", "켜지는 조건", "내용"))
    n_on = 0
    for r in rules:
        state, why = "", ""
        if env:
            on, why = rule_state(r, env)
            state = "켜짐" if on else "꺼짐"
            n_on += 1 if on else 0
        how = "" if r["tier"] != "T3" else (" [sLLM]" if r.get("llm") else " [수동]")
        print("%-22s %-3s %-6s %-34s %s%s%s" % (r["id"], r["tier"], state, rule_when_text(r), r["title"], how,
                                              ("  <- " + why) if why else ""))
    if env:
        print("")
        print("규칙 %d개 중 켜짐 %d개 (%s)" % (len(rules), n_on, "; ".join(env_summary_lines(env))))
    return EXIT_OK


# ----------------------------------------------------------------------------
# selftest: 설치 확인용 자체 시험
# 목적: 폐쇄망 PC 에 exe 를 옮긴 뒤 "돌아가는지"를 한 명령으로 확인한다.
#       작은 샘플 패키지(CP949+CRLF, UTF-8, 큰 상수 배열 포함)를 만들고, 내장 모의 sLLM
#       서버를 띄워 S0~S9 를 끝까지 돌린 다음 결과를 검사한다. 실제 sLLM 은 쓰지 않는다.
# ----------------------------------------------------------------------------
def _selftest_make_package(root):
    table = ", ".join("0x%02X" % (i % 256) for i in range(1500))
    main_cpp = "\n".join([
        "// 메인 모듈: 한글 주석 (CP949, CRLF)",
        '#include "stdafx.h"',
        "#include <stdio.h>",
        "#include <string.h>",
        '#include "Common.h"',
        '#include "ExtVendor.h"',
        "",
        "#define snprintf _snprintf",
        '#define FMT "d"',
        "",
        "int g_data[10];",
        "char g_sig[] = { 0x10, 0x80, 0xFF };",
        "",
        "char* CopyName(const char* name)",
        "{",
        "    // strdup( 는 주석 안에서는 바뀌지 않아야 한다",
        "    static const unsigned char table[] = { %s };" % table,
        '    const char* msg = "strdup( in string";',
        "    char* p = strdup(name);",
        "    char buf[64];",
        '    sprintf(buf, "%"FMT, (int)table[3]);',
        "    strcpy(buf, msg);",
        "    g_data[IDX_TEMP] = 1;",
        "    g_data[IDX_COUNT] = g_data[IDX_TEMP] + 2;",
        "    for (int i = 0; i < 10; i++) { g_data[i] = 0; }",
        "    return p;",
        "}",
        "",
        "std::pair<int, int> MakeIt() { return std::make_pair<int, int>(1, 2); }",
        ""])
    common_h = "\n".join([
        "#pragma once",
        "// 공용 정의 (UTF-8, LF)",
        "typedef unsigned char uint8_t;",
        "typedef unsigned short uint16_t;",
        "#define IDX_TEMP 0",
        "#define IDX_COUNT 1",
        "extern int g_data[10];",
        ""])
    vcproj = "\n".join([
        '<?xml version="1.0" encoding="ks_c_5601-1987"?>',
        '<VisualStudioProject ProjectType="Visual C++" Version="9.00" Name="Sample">',
        ' <Configurations>',
        '  <Configuration Name="Release|Win32" ConfigurationType="1" UseOfMFC="0" CharacterSet="2">',
        '   <Tool Name="VCCLCompilerTool" AdditionalIncludeDirectories="..\\Inc" '
        'PreprocessorDefinitions="WIN32;NDEBUG" RuntimeLibrary="0"/>',
        '   <Tool Name="VCLinkerTool" AdditionalDependencies="vendor.lib"/>',
        '  </Configuration>',
        ' </Configurations>',
        ' <Files><File RelativePath="..\\Src\\Main.cpp"/></Files>',
        '</VisualStudioProject>', ""])
    write_bytes(os.path.join(root, "Src", "Main.cpp"), main_cpp.replace("\n", "\r\n").encode("cp949"))
    write_bytes(os.path.join(root, "Src", "stdafx.h"), b"#pragma once\r\n#include <utility>\r\n")
    write_bytes(os.path.join(root, "Inc", "Common.h"), common_h.encode("utf-8"))
    write_bytes(os.path.join(root, "Proj", "Sample.vcproj"), vcproj.replace("\n", "\r\n").encode("cp949"))
    write_bytes(os.path.join(root, "Lib", "vendor.lib"), b"!<arch>\n\x00\x01\x02binary")
    write_bytes(os.path.join(root, "Res", "App.rc"), b"\xff\xfe" + "// resource\r\n".encode("utf-16-le"))
    return table


def _selftest_mock_server(tok_scale=0.0, tok_extra=0, limit_total=0, limit_numbers=True, busy_first=0, trunc_over=0):
    """모의 sLLM 서버. typedef 줄은 가드로 감싸고, SetWindowLong 줄은 Ptr 판으로 바꾸는 수정 블록을 돌려준다.
    조각에 FAILME 가 있으면 형식에 맞지 않는 응답을 낸다(분할 취소 시험용).
    시험용 동작(인자)
      tok_scale, tok_extra : tok_scale 이 0 이 아니면 응답에 실제 입력 토큰 수(추정 x tok_scale + tok_extra)를 담는다.
                             토크나이저 차이와 서버가 덧붙이는 프롬프트를 흉내 낸다
      limit_total          : 입력 + max_tokens 가 이 값을 넘으면 한도 초과 오류(HTTP 400, vLLM 문구)로 거절한다.
                             limit_numbers=False 면 수치 없는 문구로 거절한다
      busy_first           : 처음 이 횟수만큼은 과부하(HTTP 429)로 답한다
      trunc_over           : 조각의 수정 지점이 이 수를 넘으면 응답을 중간에서 자르고 finish_reason=length 로 답한다
    모은 값: prompts(처리한 프롬프트), rejected(한도 초과로 거절한 프롬프트), seen(받은 프롬프트 전부), n_busy"""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, obj, headers=()):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(404, {"error": "not found"})

        def do_POST(self):
            srv = self.server
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            prompt = body["messages"][-1]["content"]
            if srv.busy_left > 0:
                srv.busy_left -= 1
                srv.n_busy += 1
                return self._send(429, {"error": {"message": "server busy"}}, [("Retry-After", "0")])
            srv.seen.append(prompt)
            n_in = None
            if tok_scale:
                n_in = int(est_tokens("".join(m["content"] for m in body["messages"])) * tok_scale) + tok_extra
                n_out = int(body.get("max_tokens") or 0)
                if limit_total and n_in + n_out > limit_total:
                    srv.rejected.append(prompt)
                    msg = ("This model's maximum context length is %d tokens. However, you requested %d tokens "
                           "(%d in the messages, %d in the completion). Please reduce the length of the messages "
                           "or completion." % (limit_total, n_in + n_out, n_in, n_out))
                    if not limit_numbers:
                        msg = "Input is too long: context length exceeded."
                    return self._send(400, {"object": "error", "message": msg, "type": "BadRequestError", "code": 400})
            code = prompt.split("-----BEGIN CODE-----\n", 1)[-1].split("\n-----END CODE-----", 1)[0]
            srv.prompts.append(prompt)
            wl = [l for l in code.split("\n") if "SetWindowLong(" in l]
            m = re.search(r"(?:^[ \t]*typedef\b[^\n]*\bu?int(?:8|16|32|64)_t[ \t]*;[ \t]*\n?)+", code, re.M)
            finish = "stop"
            if "FAILME" in code:
                content = "이 조각은 고칠 수 없습니다."        # 형식에 맞지 않는 응답(분할 취소 시험용)
            elif wl:
                content = "\n".join(
                    "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE"
                    % (l, l.replace("SetWindowLong(", "SetWindowLongPtr(").replace("GWL_USERDATA", "GWLP_USERDATA")
                       .replace("(LONG)", "(LONG_PTR)")) for l in wl)
                if trunc_over and len(wl) > trunc_over:
                    content, finish = content[:len(content) // 2], "length"     # 출력 상한에서 잘린 응답
            elif m:
                run = m.group(0).rstrip("\n")
                content = ("<<<<<<< SEARCH\n%s\n=======\n#if !defined(_MSC_VER) || _MSC_VER < 1600\n%s\n#endif\n"
                           ">>>>>>> REPLACE" % (run, run))
            else:
                content = "NO_CHANGE"
            obj = {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish}]}
            if n_in is not None:
                obj["usage"] = {"prompt_tokens": n_in, "completion_tokens": est_tokens(content)}
            self._send(200, obj)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.prompts, srv.rejected, srv.seen = [], [], []
    srv.busy_left, srv.n_busy = busy_first, 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _selftest_ns(work, pkg, url, **over):
    """자체 시험용 실행 인자. 명령줄에서 준 것과 같은 모양이다."""
    ns = argparse.Namespace(package=pkg, profile=None, request=None, from_vs="2008", to_vs="2022", cpp_standard=None,
                            server_config=None, rules=None, out=os.path.join(work, "runs"), dry_run=False,
                            resume=None, until=None, max_parallel=2, no_compile=False, llm_endpoint=url)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _selftest_llm_defaults(ctx, **over):
    """자체 시험은 PC 의 config/server.json 값에 흔들리지 않도록 관련 설정을 고정한다."""
    ctx.cfg["llm"].update(server_prompt_reserve_tokens=0, retries=2, need_context_rounds=2, busy_retries=5,
                          calibrate_first=True, max_total_tokens=24000, max_output_tokens=3000)
    ctx.cfg["llm"].update(over)
    ctx.cfg["limits"].update(big_init_chars=2000, slice_context_lines=25, cluster_gap_lines=30, chunk_enabled=True,
                             chunk_ledger_chars=1500, chunk_head_lines=25)


def _selftest_chunk(work, check):
    """분할 순차 시험: 토큰 한도를 일부러 작게 잡아, 한 구간이 여러 조각으로 나뉘어 순서대로 처리되는지 본다.
    WideFn 은 전부 고쳐져야 하고, FailFn 은 한 조각이 실패하므로 구간 전체가 취소돼야 한다."""
    pkg = os.path.join(work, "CHUNK_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    L = ["// 분할 순차 시험 (CP949, CRLF)", "#include <windows.h>", "", "void WideFn(HWND h, void** p)", "{"]
    for k in range(40):
        L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)p[%d]);" % k, "    Sleep(%d);" % k, "    // 채움 주석 %d" % k]
    L += ["}", ""] + ["// 간격 %d" % k for k in range(40)] + ["void FailFn(HWND h, void** q)", "{"]
    for k in range(20):
        L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)q[%d]);%s" % (k, "  // FAILME" if k == 15 else ""),
              "    Sleep(%d);" % k, "    // 채움 주석 %d" % k]
    L += ["}", ""]
    write_bytes(os.path.join(pkg, "Src", "Wide.cpp"), "\r\n".join(L).encode("cp949"))
    prof = os.path.join(work, "profile_x64.json")
    write_text(prof, json.dumps({"from": {"vs": 2008}, "to": {"vs": 2022, "platform": "x64"}}))
    srv = _selftest_mock_server()
    ns = argparse.Namespace(package=pkg, profile=prof, request=None, from_vs=None, to_vs=None, cpp_standard=None,
                            server_config=None, rules=None, out=os.path.join(work, "runs"), dry_run=False,
                            resume=None, until=None, max_parallel=2, no_compile=False,
                            llm_endpoint="http://127.0.0.1:%d/v1" % srv.server_address[1])
    ctx = _make_ctx(ns)
    _selftest_llm_defaults(ctx, max_total_tokens=4000, max_output_tokens=800)       # 일부러 작게
    ctx.cfg["limits"]["chunk_ledger_chars"] = 400
    try:
        code = run_pipeline(ctx, [s for s, _n in STAGES])
        groups = ctx.store.q("SELECT grp, MAX(parts) AS parts, SUM(status='done') AS done, "
                             "SUM(status='cancelled') AS cancelled FROM tasks WHERE grp>0 GROUP BY grp ORDER BY grp")
    finally:
        ctx.store.close()
        srv.shutdown()
    wp = os.path.join(ctx.run_dir, "out", "changed", "Src", "Wide.cpp")
    raw = read_bytes(wp) if os.path.isfile(wp) else b""
    txt = raw.decode("cp949")
    check("분할 순차: 실행이 단계 실패 없이 끝남", code in (EXIT_OK, EXIT_REVIEW))
    check("분할 순차: 한도를 넘는 두 구간이 각각 여러 조각으로 나뉨",
          len(groups) == 2 and all(g["parts"] >= 2 for g in groups))
    check("분할 순차: 뒤 조각에 앞 조각의 변경 내용이 전달됨",
          any("[앞 조각에서 바꾼 내용]" in p and "SetWindowLongPtr" in p for p in srv.prompts))
    check("분할 순차: WideFn 의 40곳이 모두 고쳐지고 괄호 짝과 CRLF 유지",
          txt.count("SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p[") == 40 and txt.count("{") == txt.count("}")
          and txt.count("(") == txt.count(")") and "\n" not in txt.replace("\r\n", ""))
    check("분할 순차: 한 조각이 실패한 FailFn 은 구간 전체가 취소됨",
          txt.count("SetWindowLong(h, GWL_USERDATA, (LONG)q[") == 20 and "(LONG_PTR)q[" not in txt
          and len(groups) == 2 and groups[1]["cancelled"] >= 1 and not groups[1]["done"])


def _selftest_rules(work, pkg, check):
    """규칙 선택 시험: 버전 구간에 따라 켜지는 규칙이 달라지는지, 처리 범위(--only)가 지켜지는지 본다."""
    def on(rid, f, t, **kw):
        env = {"from_vs": f, "to_vs": t, "cpp_standard": "c++14" if t >= 2015 else None, "conformance": "off",
               "platform": kw.get("platform", "keep"), "charset": "keep",
               "policy": {"deprecated_warnings": kw.get("deprecated", "suppress")}}
        return rule_state(next(r for r in RULES if r["id"] == rid), env)[0]

    check("규칙 선택: 버전 구간에 따라 켜지는 규칙이 달라짐",
          on("R-T2-MAKE-PAIR", 2008, 2022) and on("R-T2-MAKE-PAIR", 2010, 2022) and not on("R-T2-MAKE-PAIR", 2012, 2022)
          and on("R-T3-STDINT-TYPEDEF", 2008, 2010) and not on("R-T3-STDINT-TYPEDEF", 2010, 2022)
          and on("R-T1-SNPRINTF-MACRO", 2013, 2015) and not on("R-T1-SNPRINTF-MACRO", 2015, 2022)
          and not on("R-T1-SNPRINTF-MACRO", 2008, 2013) and on("R-T0-WINSOCK-DEPR", 2008, 2013)
          and not on("R-T0-WINSOCK-DEPR", 2013, 2022) and on("R-T0-CRT-SECURE", 2015, 2022)
          and on("R-T3-WINDOWLONG", 2019, 2022, platform="x64") and not on("R-T3-WINDOWLONG", 2008, 2022))
    ns = _selftest_ns(work, pkg, "", from_vs="2015", dry_run=True, only=["Src/*"])
    ctx = _make_ctx(ns)
    try:
        code = run_pipeline(ctx, ["S0", "S1", "S2", "S9"])
        found = {r["rule_id"] for r in ctx.store.q("SELECT DISTINCT rule_id FROM issues")}
        scope = {r["path"]: r["status"] for r in ctx.store.q("SELECT path, status FROM files WHERE kind='source'")}
        n_out = ctx.store.one("SELECT COUNT(*) FROM issues WHERE path='Inc/Common.h'")
    finally:
        ctx.store.close()
    check("규칙 선택: 2015 -> 2022 에서는 2015 까지 이미 겪은 문제를 잡지 않음",
          code == EXIT_OK and "R-T0-CRT-SECURE" in found
          and not found & {"R-T2-MAKE-PAIR", "R-T1-SNPRINTF-MACRO", "R-T1-LITERAL-SPACE", "R-T3-NARROWING-CHAR",
                           "R-T3-STDINT-TYPEDEF"})
    check("처리 범위: --only 밖의 파일은 탐지하지 않음",
          scope.get("Inc/Common.h") == "범위제외" and scope.get("Src/Main.cpp") == "대상" and n_out == 0)


def _selftest_limit(work, check):
    """토큰 한도 시험: 서버의 실제 한도가 설정보다 작고, 서버가 세는 토큰 수가 py 의 추정보다 많은 상황을 만든다.
    한도 초과로 거절된 요청을 그대로 다시 보내지 않고, 서버가 알려 준 수치로 줄여서 끝까지 고치는지 본다."""
    pkg = os.path.join(work, "LIMIT_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    pad = "/* %s */" % ("채움 " * 30)
    L = ["// 토큰 한도 시험 (CP949, CRLF)", "#include <windows.h>", "", "void NearFn(HWND h, void* a, void* b)", "{"]
    L += ["    int n%d = %d; %s" % (k, k, pad) for k in range(30)]
    L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)a);", "    SetWindowLong(h, GWL_USERDATA, (LONG)b);"]
    L += ["    n0 += %d; %s" % (k, pad) for k in range(30)]
    L += ["}", ""] + ["// 간격 %d" % k for k in range(40)] + ["void WideFn(HWND h, void** p)", "{"]
    for k in range(24):
        L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)p[%d]);" % k, "    Sleep(%d); %s" % (k, pad)]
    L += ["}", ""]
    write_bytes(os.path.join(pkg, "Src", "Limit.cpp"), "\r\n".join(L).encode("cp949"))
    srv = _selftest_mock_server(tok_scale=1.2, tok_extra=400, limit_total=5000)
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], platform="x64"))
    _selftest_llm_defaults(ctx, max_total_tokens=7000, max_output_tokens=900)     # 설정 한도가 서버 실제 한도보다 크다
    try:
        code = run_pipeline(ctx, [s for s, _n in STAGES])
        stats = ctx.store.get_meta("llm_stats", {})
        n_over = ctx.store.one("SELECT COUNT(*) FROM llm_calls WHERE kind='over_limit'")
    finally:
        ctx.store.close()
        srv.shutdown()
    wp = os.path.join(ctx.run_dir, "out", "changed", "Src", "Limit.cpp")
    txt = read_bytes(wp).decode("cp949") if os.path.isfile(wp) else ""
    check("토큰 한도: 서버가 한도 초과로 거절한 요청을 그대로 다시 보내지 않음",
          len(srv.rejected) >= 1 and all(srv.seen.count(p) == 1 for p in srv.rejected))
    check("토큰 한도: 서버가 알려 준 한도와 실제 토큰 수를 받아 적용함",
          stats.get("limit") == 5000 and stats.get("limit_config") == 7000
          and (stats.get("ratio", 1.0) > 1.05 or stats.get("overhead", 0) > 100))
    check("토큰 한도: 거절이 반복되지 않고(3회 이하) 줄여서 다시 계획함",
          n_over == len(srv.rejected) <= 3 and stats.get("replans", 0) >= 1)
    check("토큰 한도: 다시 계획한 뒤 26곳이 모두 고쳐지고 괄호 짝과 CRLF 유지",
          code in (EXIT_OK, EXIT_REVIEW) and txt.count("SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)") == 26
          and "SetWindowLong(" not in txt and txt.count("{") == txt.count("}") and txt.count("(") == txt.count(")")
          and "\n" not in txt.replace("\r\n", ""))


def _selftest_busy_trunc(work, check):
    """과부하 응답과 잘린 응답 시험: 429 뒤에는 기다렸다 다시 보내고, 응답이 출력 상한에서 잘리면
    같은 요청을 반복하지 않고 수정 지점을 나눠 다시 보내는지 본다."""
    pkg = os.path.join(work, "TRUNC_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    L = ["// 잘린 응답 시험", "#include <windows.h>", "", "void ManyFn(HWND h, void** p)", "{"]
    L += ["    SetWindowLong(h, GWL_USERDATA, (LONG)p[%d]);" % k for k in range(12)] + ["}", ""]
    write_bytes(os.path.join(pkg, "Src", "Many.cpp"), "\r\n".join(L).encode("cp949"))
    srv = _selftest_mock_server(busy_first=2, trunc_over=6)
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], platform="x64"))
    _selftest_llm_defaults(ctx)
    try:
        code = run_pipeline(ctx, [s for s, _n in STAGES])
        n_trunc = ctx.store.one("SELECT COUNT(*) FROM llm_calls WHERE kind='truncated'")
        n_done = ctx.store.one("SELECT COUNT(*) FROM tasks WHERE status='done'")
    finally:
        ctx.store.close()
        srv.shutdown()
    wp = os.path.join(ctx.run_dir, "out", "changed", "Src", "Many.cpp")
    txt = read_bytes(wp).decode("cp949") if os.path.isfile(wp) else ""
    fixed = txt.count("SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p[")
    check("과부하 응답(429) 2회 뒤에 기다렸다 다시 보내 성공함", srv.n_busy == 2 and code == EXIT_OK and fixed == 12)
    check("잘린 응답: 같은 요청을 반복하지 않고 수정 지점을 나눠 다시 보내 12곳을 모두 고침",
          n_trunc == 1 and n_done >= 2 and fixed == 12 and len(set(srv.seen)) == len(srv.seen))


def _selftest_v050(work, check):
    """0.5.0 에서 더한 것의 시험: .vcxproj, 예외 파일과 소스의 표식, 원본 쪽 언어 표준, 고유 매크로,
    구조체 변경 요청(IPC), 빌드 로그의 오류를 이슈로 넣어 고치기, 규칙 후보."""
    vcx = "\n".join([
        '<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">',
        ' <ItemGroup Label="ProjectConfigurations">',
        '  <ProjectConfiguration Include="Debug|Win32"><Configuration>Debug</Configuration></ProjectConfiguration>',
        '  <ProjectConfiguration Include="Release|Win32"><Configuration>Release</Configuration></ProjectConfiguration>',
        ' </ItemGroup>',
        ' <PropertyGroup Condition="\'$(Configuration)|$(Platform)\'==\'Release|Win32\'" Label="Configuration">',
        '  <UseOfMfc>Dynamic</UseOfMfc><CharacterSet>MultiByte</CharacterSet><PlatformToolset>v140_xp</PlatformToolset>',
        ' </PropertyGroup>',
        ' <ItemDefinitionGroup Condition="\'$(Configuration)|$(Platform)\'==\'Release|Win32\'">',
        '  <ClCompile><AdditionalIncludeDirectories>..\\Inc;%(AdditionalIncludeDirectories)</AdditionalIncludeDirectories>',
        '   <PreprocessorDefinitions>WIN32;NDEBUG;%(PreprocessorDefinitions)</PreprocessorDefinitions>',
        '   <RuntimeLibrary>MultiThreadedDLL</RuntimeLibrary><TreatWarningAsError>true</TreatWarningAsError></ClCompile>',
        '  <Link><AdditionalDependencies>Vendor.lib;%(AdditionalDependencies)</AdditionalDependencies></Link>',
        ' </ItemDefinitionGroup>',
        ' <ItemGroup><ClCompile Include="..\\Src\\A.cpp" /><ClInclude Include="..\\Src\\A.h" /></ItemGroup>', '</Project>', ""])
    proj = parse_vcxproj(vcx)
    c = pick_config(proj) or {}
    items = [x["item"] for x in project_checks({"Proj/A.vcxproj": proj})]
    check(".vcxproj: 구성별 설정과 소스 목록을 읽고 문제 되는 설정을 알림",
          c.get("name") == "Release|Win32" and c["charset"] == "2" and c["use_mfc"] == "2" and c["runtime"] == "2"
          and c["includes"] == ["..\\Inc"] and c["defines"] == ["WIN32", "NDEBUG"] and c["libs"] == ["Vendor.lib"]
          and proj["files"] == ["Src/A.cpp", "Src/A.h"] and any("경고를 오류로" in x for x in items)
          and any("멀티바이트" in x for x in items) and any("XP" in x for x in items))

    odd = b"int f() {\n    TRY_BEGIN\n        g();\n    }\n    return 1;\n}\nint h() { return 2; }\n"
    before = make_doc("x.cpp", odd)
    set_brace_macros({"open": ["TRY_BEGIN"]})
    try:
        after = make_doc("x.cpp", odd)
    finally:
        set_brace_macros(None)
    check("고유 매크로: 괄호를 숨긴 매크로를 알려 주면 함수 경계를 맞게 읽음",
          len(after.funcs) == 2 and after.parse_ok and (len(before.funcs) != 2 or not before.parse_ok))

    pkg = os.path.join(work, "V050_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    hdr = ["#pragma pack(push, 1)", "typedef struct _MSG_HDR {", "    int nLen;", "    int nType;", "} MSG_HDR, *PMSG_HDR;",
           "#pragma pack(pop)", ""]
    ipc = ['#include "Msg.h"', "#include <windows.h>", "#include <string.h>", "int Send(PMSG_HDR p, char* buf, HWND h)", "{",
           "    MSG_HDR hdr;", "    hdr.nLen = 10;", "    p->nType = hdr.nLen;", "    memcpy(buf, &hdr, sizeof(MSG_HDR));",
           "    SetWindowLong(h, GWL_USERDATA, (LONG)p);", "    return GetIt()->nLen;", "}", ""]
    old = ["#include <utility>", "void f(int a, int b) {", "    std::pair<int,int> p1 = std::make_pair<int, int>(a, b);",
           "    std::pair<int,int> p2 = std::make_pair<int, int>(a, b); // vupdate:skip", "    // vupdate:skip-next R-T2-MAKE-PAIR",
           "    std::pair<int,int> p3 = std::make_pair<int, int>(a, b);", "}", ""]
    write_bytes(os.path.join(pkg, "Src", "Msg.h"), "\r\n".join(hdr).encode("cp949"))
    write_bytes(os.path.join(pkg, "Src", "Ipc.cpp"), "\r\n".join(ipc).encode("cp949"))
    write_bytes(os.path.join(pkg, "Src", "Old.cpp"), "\r\n".join(old).encode("cp949"))
    write_bytes(os.path.join(pkg, "Legacy", "Keep.cpp"), "\r\n".join(old[:3] + ["}", ""]).encode("cp949"))
    req = os.path.join(work, "request_ipc.json")
    write_text(req, json.dumps({"features": [{"id": "REQ-IPC", "type": "struct_change", "target": "MSG_HDR",
                                              "rename": {"nLen": "nLength"}, "check_layout": True}]}, ensure_ascii=False))
    exc = os.path.join(work, "exceptions.json")
    write_text(exc, json.dumps({"rules_off": ["R-T3-GETVERSIONEX"], "skip": [{"path": "Legacy/*", "rules": ["R-T2-MAKE-PAIR"]}]}))
    log = os.path.join(work, "v050_msbuild.log")
    write_text(log, "\n".join([
        "1>d:\\build\\V050_PKG\\Src\\Ipc.cpp(10): error C2065: 'GWL_USERDATA': undeclared identifier [d:\\build\\V050_PKG\\P.vcxproj]",
        "1>d:\\build\\V050_PKG\\Src\\Ipc.cpp(3): error C2065: 'NoSuchName': undeclared identifier",
        "1>Vendor.lib(io.obj) : error LNK2019: unresolved external symbol __iob_func referenced in function _main", ""]))
    srv = _selftest_mock_server()
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], request=req, exceptions=exc,
                                 build_log=log, disable_rule=["R-T3-OSVER-GLOBALS"]))
    _selftest_llm_defaults(ctx)
    try:
        run_pipeline(ctx, [s for s, _n in STAGES])
        tr1 = ctx.rule_by_id["R-T1-TR1-NAMESPACE"]
        e17 = dict(ctx.env, cpp_standard="c++17")
        std_ok = rule_state(tr1, e17)[0] and not rule_state(tr1, dict(e17, from_std="c++17"))[0]
    finally:
        ctx.store.close()
        srv.shutdown()
    out = os.path.join(ctx.run_dir, "out")

    def rd(*parts):
        p = os.path.join(out, *parts)
        return read_bytes(p).decode("cp949" if parts[0] == "changed" else "utf-8") if os.path.isfile(p) else ""

    msg_h, ipc_c, old_c = rd("changed", "Src", "Msg.h"), rd("changed", "Src", "Ipc.cpp"), rd("changed", "Src", "Old.cpp")
    manual, report = rd("manual_review.md"), rd("report.md")
    off = {rid for rid, _t, _w in ctx.rules_off}
    check("예외: 끈 규칙, 제외한 폴더, 소스의 표식(vupdate:skip, skip-next)을 지킴",
          {"R-T3-GETVERSIONEX", "R-T3-OSVER-GLOBALS"} <= off and not os.path.isfile(os.path.join(out, "changed", "Legacy", "Keep.cpp"))
          and old_c.count("make_pair<int, int>") == 2 and "p1 = std::make_pair(a, b)" in old_c and "예외로 건너뛴 탐지" in report)
    check("원본 쪽 언어 표준: 원본도 c++17 이면 c++17 규칙을 끔", std_ok)
    check("구조체 변경 요청: 정의와 선언된 변수의 멤버 접근을 바꾸고 CRLF 유지",
          "int nLength;" in msg_h and "hdr.nLength = 10;" in ipc_c and "p->nType = hdr.nLength;" in ipc_c
          and "GetIt()->nLen;" in ipc_c and ipc_c.count("\r\n") == len(ipc) - 1)
    check("구조체 변경 요청: 확정할 수 없는 접근과 배치 점검(sizeof, memcpy)은 수동 검토로, 보고서에 변경 계약",
          "미확정 접근" in manual and "배치 점검" in manual and "변경 계약" in report and "`nLen` → `nLength`" in report)
    check("빌드 로그: 컴파일 오류가 난 줄을 sLLM 으로 고치고, 맞지 않는 줄과 링크 오류는 수동 검토로",
          "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p);" in ipc_c and "로그의 줄과 소스가 맞지 않음" in manual
          and "B-LNK2019" in manual and "빌드 로그에서 넣은 이슈" in report)


def _selftest_v070(work, check):
    """0.7.0 에서 더한 것의 시험: 사용자 요청문(대상: 요청문)으로 대상이 쓰인 곳을 찾아 sLLM 에 맡긴다."""
    pkg = os.path.join(work, "V070_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    src = ["#include <windows.h>", "void A(HWND h, void* p)", "{", "    SetWindowLong(h, GWL_USERDATA, (LONG)p);", "}", ""]
    write_bytes(os.path.join(pkg, "Src", "Ui.cpp"), "\r\n".join(src).encode("cp949"))
    write_bytes(os.path.join(pkg, "Lib", "Old.cpp"), "\r\n".join(src).encode("cp949"))
    askf = os.path.join(work, "asks.txt")
    write_bytes(askf, "# 요청문 파일: 한 줄에 하나\r\n@Src/* SetWindowLong: SetWindowLongPtr 로 바꾸고 인덱스와 형 변환도 맞춘다\r\n주석의 문구는 바꾸지 않는다\r\n".encode("cp949"))
    srv = _selftest_mock_server()
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], ask_file=askf,
                                 ask=["NoSuchName: 이 이름은 패키지에 없다"]))
    _selftest_llm_defaults(ctx)
    try:
        run_pipeline(ctx, [s for s, _n in STAGES])
        asks, common = ctx.request.get("asks") or [], ctx.request.get("common") or []
    finally:
        ctx.store.close()
        srv.shutdown()
    out = os.path.join(ctx.run_dir, "out")
    ui = os.path.join(out, "changed", "Src", "Ui.cpp")
    txt = read_bytes(ui).decode("cp949") if os.path.isfile(ui) else ""
    report = read_bytes(os.path.join(out, "report.md")).decode("utf-8") if os.path.isfile(os.path.join(out, "report.md")) else ""
    check("사용자 요청문: 파일과 명령줄의 요청문을 읽고(대상 있는 것 2건, 공통 요청 1건) 범위 안의 파일만 고침",
          len(asks) == 2 and common == ["주석의 문구는 바꾸지 않는다"] and "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p);" in txt
          and not os.path.isfile(os.path.join(out, "changed", "Lib", "Old.cpp")) and any("[공통 요청]" in p and "주석의 문구" in p for p in srv.prompts)
          and any("사용자 요청: SetWindowLongPtr 로 바꾸고" in p for p in srv.prompts) and "사용자 요청문" in report)


def _selftest_v040(work, check):
    """0.4.0 에서 더한 것의 시험: 새 규칙, 환경값 축, 공통 요청, 프로젝트 점검, 고치지 않은 지점의 처리,
    서버 점검(llmcheck), 빌드 오류 분류(buildlog)."""
    pkg = os.path.join(work, "V040_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    src = ["// 0.4.0 규칙 시험 (CP949, CRLF)", "#define WINVER 0x0400", "#define for if (0) {} else for",
           "#define round(x) floor((x) + 0.5)", "#include <windows.h>",
           "struct timespec { long tv_sec; long tv_nsec; };", "int Calc(HWND h, void* p)", "{", "    auto int n = 0;",
           "    char buf[80];", "    gets(buf);", "    SetWindowLong(h, GWL_USERDATA, (LONG)p);", "    return n;", "}", ""]
    write_bytes(os.path.join(pkg, "Src", "New.cpp"), "\r\n".join(src).encode("cp949"))
    vcproj = "\n".join([
        '<?xml version="1.0" encoding="ks_c_5601-1987"?>',
        '<VisualStudioProject ProjectType="Visual C++" Version="9.00" Name="New">', ' <Configurations>',
        '  <Configuration Name="Release|Win32" ConfigurationType="1" UseOfMFC="2" CharacterSet="2">',
        '   <Tool Name="VCCLCompilerTool" WarnAsError="true" TreatWChar_tAsBuiltInType="false" '
        'ForceConformanceInForLoopScope="false" RuntimeLibrary="2"/>', '  </Configuration>', ' </Configurations>',
        ' <Files><File RelativePath="..\\Src\\New.cpp"/></Files>', '</VisualStudioProject>', ""])
    write_bytes(os.path.join(pkg, "Proj", "New.vcproj"), vcproj.replace("\n", "\r\n").encode("cp949"))
    req = os.path.join(work, "request_common.json")
    write_text(req, json.dumps({"common": ["주석의 문구는 바꾸지 않는다"], "features": []}, ensure_ascii=False))
    srv = _selftest_mock_server()
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], platform="x64", request=req,
                                 axis=["os=Windows XP->Windows 11", "db=Oracle 10g->Oracle 19c"]))
    _selftest_llm_defaults(ctx)
    try:
        code = run_pipeline(ctx, [s for s, _n in STAGES])
    finally:
        ctx.store.close()
        srv.shutdown()
    out = os.path.join(ctx.run_dir, "out")

    def rd(name):
        p = os.path.join(out, name)
        return open(p, encoding="utf-8").read() if os.path.isfile(p) else ""

    np_ = os.path.join(out, "changed", "Src", "New.cpp")
    txt = read_bytes(np_).decode("cp949") if os.path.isfile(np_) else ""
    settings, report, manual = rd("project_settings.md"), rd("report.md"), rd("manual_review.md")
    check("새 규칙: auto 저장 클래스 제거, WINVER 를 대상 OS 값으로, round 매크로 가드",
          code in (EXIT_OK, EXIT_REVIEW) and "    int n = 0;" in txt and "auto int" not in txt
          and "#define WINVER 0x0A00" in txt
          and "#if !defined(_MSC_VER) || _MSC_VER < 1800\r\n#define round(x) floor((x) + 0.5)\r\n#endif" in txt)
    check("설정 흡수: 키워드 매크로는 코드를 그대로 두고 설정 항목으로 냄",
          "#define for if (0) {} else for" in txt and "_ALLOW_KEYWORD_MACROS" in settings)
    check("환경값 축(OS, DB)과 공통 요청이 프롬프트와 보고서에 들어감",
          srv.prompts and all("- OS: Windows XP -> Windows 11" in p and "[공통 요청]" in p and "주석의 문구는 바꾸지 않는다" in p
                              for p in srv.prompts) and "DB: Oracle 10g -> Oracle 19c" in report)
    check("sLLM 이 고치지 않은 지점은 수정됨으로 치지 않고 수동 검토로 보냄",
          "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p);" in txt and "gets(buf);" in txt
          and "R-T3-GETS-REMOVED" in manual and "R-T3-TIMESPEC" in manual and "이 지점의 수정이 없음" in manual)
    check("프로젝트 파일 점검: 경고를 오류로 처리, wchar_t, for 범위, MFC 멀티바이트 설정을 알림",
          all(k in settings for k in ("프로젝트 파일 점검", "/WX", "/Zc:wchar_t-", "/Zc:forScope-", "멀티바이트")))

    # --- 서버 점검: 토큰을 1.2배로 세고 300토큰을 덧붙이며 한도 6000 을 거는 모의 서버 ---
    srv = _selftest_mock_server(tok_scale=1.2, tok_extra=300, limit_total=6000)
    llm = json.loads(json.dumps(DEFAULT_SERVER["llm"]))
    llm.update(endpoint="http://127.0.0.1:%d/v1" % srv.server_address[1], max_total_tokens=6000, max_output_tokens=800,
               max_parallel=2)
    try:
        rows, tips, alive = run_llmcheck(llm)
    finally:
        srv.shutdown()
    verdict = {item: v for item, v, _t in rows}
    check("서버 점검(llmcheck): 토큰 수 반환, 한도 초과 거절, 출력 형식, 고정분을 확인함",
          alive and verdict.get("토큰 수 반환") == "정상" and verdict.get("한도 초과 거절") == "정상"
          and verdict.get("출력 형식") == "정상" and any("server_prompt_reserve_tokens 를 500" in t for t in tips))

    # --- 빌드 오류 분류: 위 실행의 결과 파일에서 났다고 가정한 오류 목록 ---
    n_line = txt.replace("\r\n", "\n").split("\n").index("    int n = 0;") + 1
    log = "\n".join([
        "1>d:\\build\\V040_PKG\\Src\\New.cpp(2): fatal error C1189: #error:  MFC does not support WINVER less than 0x0501.",
        "1>d:\\build\\V040_PKG\\Src\\New.cpp(%d): error C2065: 'n': undeclared identifier" % n_line,
        "1>d:\\build\\V040_PKG\\Src\\New.cpp(%d): error C2065: 'n': undeclared identifier" % n_line,
        "1>LINK : fatal error LNK1104: cannot open file 'mfc90.lib'",
        "1>Old.obj : error LNK2019: unresolved external symbol __iob_func referenced in function _main "
        "[d:\\build\\V040_PKG\\Proj\\New.vcxproj]",
        "cl : Command line warning D9035 : option 'Gm' has been deprecated and will be removed in a future release", ""])
    diags = triage_build_log(parse_build_log(log), load_run_view(ctx.run_dir))
    by = {d["code"]: d for d in diags}
    md = build_log_report(diags, "msbuild.log", ctx.run_dir)
    check("빌드 오류 분류(buildlog): 반복을 합치고 코드별 대응과 이 도구의 수정과의 관계를 붙임",
          len(diags) == 5 and sum(1 for d in diags if d["kind"] == "error") == 4
          and "R-T2-WINVER-OLD" in by["C1189"]["action"] and "다시 빌드" in by["LNK2019"]["action"]
          and by["C2065"]["relation"] == "이 도구가 고친 줄" and by["LNK1104"]["stage"] == "링크"
          and by["D9035"]["kind"] == "warning" and "이 도구가 고친 줄에서 난 오류" in md)


def cmd_selftest(args):
    work = os.path.abspath(args.dir) if args.dir else tempfile.mkdtemp(prefix="vupdate_selftest_")
    pkg = os.path.join(work, "SAMPLE_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    table = _selftest_make_package(pkg)
    req = os.path.join(work, "request.json")
    write_text(req, json.dumps({"features": [{"id": "REQ-001", "type": "array_to_struct", "target": "g_data",
                                              "mapping": {"IDX_TEMP": "temp", "IDX_COUNT": "count"}}]}, ensure_ascii=False))
    prof = os.path.join(work, "profile.json")
    write_text(prof, json.dumps({"from": {"vs": 2008}, "to": {"vs": 2022}, "policy": {"deprecated_warnings": "fix"}}))
    srv = _selftest_mock_server()
    ns = argparse.Namespace(package=pkg, profile=prof, request=req, from_vs=None, to_vs=None, cpp_standard=None,
                            server_config=None, rules=None, out=os.path.join(work, "runs"), dry_run=False,
                            resume=None, until=None, max_parallel=2, no_compile=False,
                            llm_endpoint="http://127.0.0.1:%d/v1" % srv.server_address[1])
    ctx = _make_ctx(ns)
    _selftest_llm_defaults(ctx)
    try:
        code = run_pipeline(ctx, [s for s, _n in STAGES])
    finally:
        ctx.store.close()
        srv.shutdown()
    checks = []

    def check(name, cond):
        checks.append((name, bool(cond)))

    out = os.path.join(ctx.run_dir, "out")
    mp = os.path.join(out, "changed", "Src", "Main.cpp")
    hp = os.path.join(out, "changed", "Inc", "Common.h")
    check("실행이 단계 실패 없이 끝남", code in (EXIT_OK, EXIT_REVIEW))
    check("변경 파일 Main.cpp 생성", os.path.isfile(mp))
    if os.path.isfile(mp):
        raw = read_bytes(mp)
        txt = raw.decode("cp949")
        check("CP949 로 다시 읽힘, 한글 주석 유지", "한글 주석" in txt)
        check("줄끝 CRLF 유지", "\n" not in txt.replace("\r\n", ""))
        check("strdup -> _strdup (코드만)", "char* p = _strdup(name);" in txt)
        check("주석과 문자열 안의 strdup 은 그대로", "// strdup( 는 주석" in txt and '"strdup( in string"' in txt)
        check('리터럴 뒤 매크로에 공백 삽입', '"%" FMT' in txt)
        check("snprintf 매크로를 구 컴파일러 전용으로 제한", "#if defined(_MSC_VER) && _MSC_VER < 1900\r\n#define snprintf _snprintf\r\n#endif" in txt)
        check("make_pair 명시 인자 제거", "std::make_pair(1, 2)" in txt)
        check("요청사항: 상수 인덱스를 필드로 치환", "g_data.temp = 1;" in txt and "g_data.count = g_data.temp + 2;" in txt)
        hdr_txt = read_bytes(hp).decode("utf-8", "replace") if os.path.isfile(hp) else ""
        check("배열 선언을 구조체 선언으로 바꾸고(정의는 헤더에) 변수 인덱스는 그대로",
              "g_data[i] = 0;" in txt and "G_DATA_T g_data;" in txt and "int g_data[10];" not in txt and "    int temp;" in hdr_txt
              and "    int reserved_9;" in hdr_txt and "extern G_DATA_T g_data;" in hdr_txt)
        check("큰 상수 배열이 바이트 단위로 동일", table.encode("ascii") in raw)
    check("sLLM 수정 반영(Common.h typedef 가드)", os.path.isfile(hp) and
          b"#if !defined(_MSC_VER) || _MSC_VER < 1600\ntypedef unsigned char uint8_t;\ntypedef unsigned short uint16_t;\n#endif" in read_bytes(hp))
    for name in ("report.md", "manifest.csv", "manual_review.md", "project_settings.md", "build_guide.md", "preflight.md"):
        check("산출물 %s" % name, os.path.isfile(os.path.join(out, name)))
    check("전체 patch 생성", os.path.isfile(os.path.join(out, "diff", "all.patch")))
    mr = open(os.path.join(out, "manual_review.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "manual_review.md")) else ""
    check("수동 검토에 좁히기 변환과 변수 인덱스 접근 포함", "R-T3-NARROWING-CHAR" in mr and "변수 인덱스" in mr)
    pf = open(os.path.join(out, "preflight.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "preflight.md")) else ""
    check("환경 점검에 외부 헤더 후보(ExtVendor.h) 표시", "ExtVendor.h" in pf)
    _selftest_chunk(work, check)
    _selftest_rules(work, pkg, check)
    _selftest_limit(work, check)
    _selftest_busy_trunc(work, check)
    _selftest_v040(work, check)
    _selftest_v050(work, check)
    _selftest_v070(work, check)
    print("")
    print("===== 자체 시험 결과 =====")
    for name, ok in checks:
        print("[%s] %s" % ("통과" if ok else "실패", name))
    n_fail = sum(1 for _n, ok in checks if not ok)
    print("합계: %d개 중 %d개 통과. 작업 폴더: %s" % (len(checks), len(checks) - n_fail, work))
    return EXIT_OK if n_fail == 0 else EXIT_FAIL


def build_parser():
    p = argparse.ArgumentParser(prog=APP, description="C++ 버전 업데이트 대응 자동화 %s" % VERSION)
    p.add_argument("--version", action="version", version="%s %s" % (APP, VERSION))
    p.add_argument("--console-encoding", help="콘솔 출력 인코딩(예: utf-8). 생략하면 시스템 기본값")
    p.add_argument("--verbose", action="store_true", help="콘솔에도 상세(DEBUG) 로그 출력")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="패키지 하나를 처리한다")
    r.add_argument("--package", help="대상 패키지 폴더")
    r.add_argument("--profile", help="환경값 프로필(파일 경로 또는 config/profiles 안의 이름)")
    r.add_argument("--from-vs", dest="from_vs", help="원본 VS 제품 연도(예: 2008)")
    r.add_argument("--to-vs", dest="to_vs", help="대상 VS 제품 연도(예: 2022)")
    r.add_argument("--cpp-standard", dest="cpp_standard", help="C++ 언어 표준(c++14, c++17, c++20). 기본 c++14")
    r.add_argument("--platform", help="대상 플랫폼(keep, x86, x64). 기본 keep. x64 면 64비트 규칙이 켜진다")
    r.add_argument("--conformance", help="준수 모드 /permissive- (off, on). 기본 off")
    r.add_argument("--charset", help="문자셋(keep, mbcs, unicode). 기본 keep")
    r.add_argument("--deprecated", help="사용 중단 경고 처리(suppress: 설정으로 끈다, fix: 코드를 고친다). 기본 suppress")
    r.add_argument("--axis", action="append", metavar="이름=값",
                   help="컴파일러 말고 다른 환경값(os, db, hmi, sdk 등). 여러 번 줄 수 있다. 예: --axis \"os=Windows 7->Windows 11\"")
    r.add_argument("--request", help="요청사항(파일 경로 또는 config/requests 안의 이름)")
    r.add_argument("--ask", action="append", metavar="\"대상: 요청문\"",
                   help="사용자 요청문. 대상 이름이 쓰인 곳을 찾아 요청문대로 고치게 sLLM 에 맡긴다. 여러 번 줄 수 있다. "
                        "대상 없이 문장만 주면 공통 요청이다")
    r.add_argument("--ask-file", dest="ask_file", help="요청문 파일. 한 줄에 요청 하나(형식은 --ask 와 같다. # 은 주석)")
    r.add_argument("--from-cpp-standard", dest="from_cpp_standard",
                   help="원본 쪽 C++ 언어 표준(예: c++17). 주면 원본에서 이미 겪은 언어 표준 규칙을 끈다")
    r.add_argument("--exceptions", help="예외 파일(끌 규칙, 제외할 파일과 줄, 고유 매크로). 예: config/EXAMPLE_exceptions.json")
    r.add_argument("--disable-rule", dest="disable_rule", action="append", metavar="규칙ID",
                   help="이 규칙을 끈다. 여러 번 주거나 쉼표로 잇는다")
    r.add_argument("--build-log", dest="build_log",
                   help="빌드 로그. 로그의 컴파일 오류를 이슈로 넣어 sLLM 단계에서 고친다. --package 는 그 로그를 낸 소스 트리여야 한다")
    r.add_argument("--only", action="append", metavar="패턴",
                   help="처리할 파일 범위. 패키지 기준 경로의 와일드카드. 여러 번 또는 쉼표로. 예: Src/Net/*")
    r.add_argument("--exclude", action="append", metavar="패턴", help="처리에서 뺄 파일 범위. 예: ThirdParty/*")
    r.add_argument("--server-config", dest="server_config", help="서버 설정 파일. 기본 config/server.json")
    r.add_argument("--rules", help="추가 규칙 파일. 기본 config/rules.json")
    r.add_argument("--llm-endpoint", dest="llm_endpoint",
                   help="sLLM 주소. 없으면 환경변수 VUPDATE_LLM_ENDPOINT, 그다음 서버 설정의 값")
    r.add_argument("--max-parallel", dest="max_parallel", type=int,
                   help="sLLM 동시 요청 수. 서버 설정의 허용 동시 수(llm.max_parallel) 이하로만 적용된다")
    r.add_argument("--caller", help="호출자 표시(서버 기록용). 기본은 PC 이름\\계정")
    r.add_argument("--out", help="실행 폴더를 만들 위치. 기본 ./runs")
    r.add_argument("--dry-run", dest="dry_run", action="store_true", help="탐지까지만 수행(S0~S2)하고 보고")
    r.add_argument("--until", help="지정한 단계까지만 수행(예: S4)")
    r.add_argument("--resume", help="중단된 실행 폴더를 이어서 수행")
    r.add_argument("--no-compile", dest="no_compile", action="store_true", help="컴파일 검사를 하지 않음")
    r.add_argument("--no-llm", dest="no_llm", action="store_true",
                   help="sLLM 단계를 건너뛴다. 규칙 수정만 하고 sLLM 대상은 수동 검토로 보낸다")
    r.add_argument("--no-chunk", dest="no_chunk", action="store_true",
                   help="분할 순차를 끈다. 토큰 한도를 넘는 구간은 수동 검토로 보낸다")
    r.add_argument("--compile-parallel", dest="compile_parallel", type=int,
                   help="컴파일 검사 동시 수. 0 은 CPU 수에 맞춤(기본)")
    r.add_argument("--vs-path", dest="vs_path",
                   help="Visual Studio 설치 폴더 직접 지정(자동 탐지가 틀릴 때). 환경변수 VUPDATE_VS_PATH 도 된다")
    r.add_argument("--sdk-path", dest="sdk_path",
                   help="Windows SDK 폴더 직접 지정(예: ...\\Windows Kits\\10 또는 그 아래의 판 폴더). 환경변수 VUPDATE_SDK_PATH 도 된다")
    r.set_defaults(func=cmd_run)

    f = sub.add_parser("preflight", help="환경 점검만 수행한다(03_확인사항 자동화)")
    f.add_argument("--package", required=True, help="대상 패키지 폴더")
    f.add_argument("--out", help="실행 폴더를 만들 위치. 기본 ./runs")
    f.add_argument("--server-config", dest="server_config")
    f.add_argument("--vs-path", dest="vs_path", help="Visual Studio 설치 폴더 직접 지정(환경변수 VUPDATE_VS_PATH 도 된다)")
    f.add_argument("--sdk-path", dest="sdk_path", help="Windows SDK 폴더 직접 지정(환경변수 VUPDATE_SDK_PATH 도 된다)")
    f.set_defaults(func=cmd_preflight)

    s = sub.add_parser("status", help="실행 폴더의 진행 상태를 본다")
    s.add_argument("--run", required=True, help="실행 폴더")
    s.set_defaults(func=cmd_status)

    u = sub.add_parser("rules", help="규칙 목록을 본다")
    u.add_argument("--rules", help="추가 규칙 파일")
    u.add_argument("--profile", help="환경값 프로필. 주면 그 환경값에서 켜지는 규칙을 표시한다")
    u.add_argument("--from-vs", dest="from_vs", help="원본 VS 제품 연도")
    u.add_argument("--to-vs", dest="to_vs", help="대상 VS 제품 연도")
    u.add_argument("--cpp-standard", dest="cpp_standard")
    u.add_argument("--from-cpp-standard", dest="from_cpp_standard")
    u.add_argument("--platform")
    u.add_argument("--conformance")
    u.add_argument("--charset")
    u.add_argument("--deprecated")
    u.add_argument("--axis", action="append", metavar="이름=값")
    u.add_argument("--coverage", action="store_true", help="알려진 변경 항목과 처리 방식의 표를 낸다")
    u.add_argument("--test", action="store_true", help="규칙에 붙은 시험 예(잡아야 하는 코드, 잡지 말아야 하는 코드)를 돌린다")
    u.set_defaults(func=cmd_rules)

    c = sub.add_parser("llmcheck", help="실제 sLLM 서버가 이 도구가 기대하는 대로 답하는지 점검한다")
    c.add_argument("--llm-endpoint", dest="llm_endpoint", help="sLLM 주소. 없으면 환경변수 VUPDATE_LLM_ENDPOINT, 서버 설정")
    c.add_argument("--server-config", dest="server_config")
    c.add_argument("--out", help="결과 폴더를 만들 위치. 기본 ./runs")
    c.add_argument("--caller", help="호출자 표시")
    c.add_argument("--skip-over-limit", dest="skip_over_limit", action="store_true",
                   help="한도를 일부러 넘기는 요청을 보내지 않는다")
    c.set_defaults(func=cmd_llmcheck)

    b = sub.add_parser("buildlog", help="빌드 오류 목록(msbuild 로그)을 분류하고 대응 방법을 붙인다")
    b.add_argument("--log", help="빌드 로그 파일(msbuild /fl 로 만든 msbuild.log 등)")
    b.add_argument("--run", help="대조할 실행 폴더. 주면 이 도구가 고친 줄에서 난 오류를 가려 준다")
    b.add_argument("--out", help="결과를 쓸 폴더. 기본은 실행 폴더의 out, 없으면 로그가 있는 폴더")
    b.add_argument("--guide", action="store_true", help="오류 코드별 대응표만 출력한다")
    b.set_defaults(func=cmd_buildlog)

    t = sub.add_parser("selftest", help="설치 확인용 자체 시험(샘플 패키지 + 모의 sLLM)")
    t.add_argument("--dir", help="작업 폴더. 생략하면 임시 폴더")
    t.set_defaults(func=cmd_selftest)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            if args.console_encoding:
                stream.reconfigure(encoding=args.console_encoding, errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except Exception:
            pass
    _SINK.console_debug = bool(args.verbose)
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_INPUT
    try:
        return args.func(args)
    except UserInputError as e:
        LOGA.error("입력 오류: %s" % e)
        return EXIT_INPUT
    except KeyboardInterrupt:
        LOGA.error("사용자가 중단했습니다")
        return EXIT_FAIL
    except Exception:
        LOGA.exception("예상하지 못한 오류")
        return EXIT_FAIL
    finally:
        _SINK.close()


if __name__ == "__main__":
    sys.exit(main())
