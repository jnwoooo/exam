#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
vupdate.py  -  C++ 버전 업데이트 대응 자동화 (단일 파일 실행본)
==============================================================================
버전 : 0.9.1 (요청문 --ask 를 sLLM 이 의도별로 나누고 도구가 대상을 찾아 의도별로 수정 요청을 보내는 흐름으로 정리.
              환경 호환성 항목의 sLLM 호출은 --compat-llm 으로 켠다. 당장 쓰지 않는 기능은 뺐다. 17_추가_0.9.0.md)
              0.9.1: OS·DB 의 원본/대상 옵션(--from-os, --to-os, --from-db, --to-db). 18_추가_0.9.1.md
목적 : 레거시 C++ 패키지를 새 Visual Studio 환경(예: 2008 -> 2022)에 맞게
       고치는 작업을 한 명령으로 수행한다. 규칙으로 고칠 수 있는 것은 규칙으로,
       규칙으로 못 고치는 것만 sLLM에 맡기고, 결과를 별도 폴더에 낸다.
배포 : 표준 라이브러리만 사용한다(폐쇄망 배포용). 외부 패키지 설치가 필요 없다.
       exe 생성: pyinstaller --onefile --name vupdate vupdate.py
문서 : 04_개발구조.md (구조), 03_확인사항.md (환경 점검)

[사용 예]
  vupdate run --package D:\\src\\PKG_A --from-vs 2008 --to-vs 2022
  vupdate run --package D:\\src\\PKG_A --from-vs 2015 --to-vs 2022 --platform x64 --only Src/Net/*
  vupdate rules --from-vs 2015 --to-vs 2022      (이 환경값에서 켜지는 규칙 보기)
  vupdate preflight --package D:\\src\\PKG_A      (환경 점검만)
  vupdate selftest                               (설치 확인용 자체 시험)
  vupdate llmcheck                               (실제 sLLM 서버가 기대대로 답하는지 점검)
  vupdate run --package D:\\src\\PKG_A --from-vs 2008 --to-vs 2022 --max-calls 200   (호출 200회까지만. 남으면 종료 코드 4)
  vupdate run --resume <실행 폴더>                 (대기로 남은 작업을 이어서 보냄)
  vupdate run ... --ask "WriteLog 호출에 줄 번호 인자를 추가하고 g_data 배열을 구조체로 바꾼다"   (요청문. 형식 없이 글로 적는다)
  vupdate run ... --compat-llm                    (환경 호환성 항목도 sLLM 에 보내 수정을 시도한다. 기본은 보내지 않는다)

[이 파일의 구획]  수정할 때는 해당 구획만 고친다. 구획 사이의 호출은
                  실행기(M10)가 넘겨주는 ctx 객체와 저장소(M03)만 거친다.
  M01 공통      상수, 로그([모듈][함수명] 접두어), 작은 유틸
  M02 설정      버전 대응표, 프로필/요청사항/서버 설정 읽기와 검증
  M03 저장소    실행 상태 DB(SQLite 파일 하나)
  M04 어휘분석  인코딩 판별, 주석/문자열 마스킹, 함수 경계, 초기화 목록
  M04 구조분석  블록 트리, 제어문 범위, 선언 찾기(0.8.0). 대상 판단과 조각 구성, 수용 검사가 여기에 기댄다
  M05 규칙      규칙 카탈로그(T0~T3)와 탐지 함수
  M06 sLLM연결  토큰 추정과 보정, HTTP 호출, 오류 분류(한도 초과/과부하/거절)
  M07 컴파일    Visual Studio 찾기, cl /Zs 호출(환경 등급 '나'에서만)
  S0  준비      환경 점검(03_확인사항 자동화), 원본 스냅샷
  S1  인벤토리  파일 분류, 인코딩, 함수 경계, include 수집
  S2  탐지      규칙 스캔 -> 이슈
  S3  설정흡수  코드 변경 없이 옵션/매크로로 해결할 항목 정리
  S4  규칙적용  T1/T2 규칙 -> 수정 레코드
  S5  영향분석  요청문의 의미 분석(sLLM), 대상 찾기, 배열·표 -> 구조체, 헤더 의존 파일 파악
  S6  sLLM수정  T3 이슈를 조각으로 잘라 sLLM에 보내고 수정 블록 수용 검사
  S7  병합      수정 레코드를 적용해 결과 파일 생성
  S8  검증      L0~L2 (+ 환경 '나'면 L3 컴파일 검사), 불합격 시 되돌림
  S9  산출      diff, 보고서, 매니페스트, 수동 검토 목록
  M10 실행기    단계 순서 실행, 재개, 오류 처리
  M12 부가명령  서버 점검(llmcheck)
[0.9.0 에서 뺀 것]  0.8.0 에 있다(git 브랜치 add-vupdate-v0.8.0). 필요해지면 거기서 되살린다
  문장 요청(--say), 요청문의 문법(대상: 요청문, [if], /정규식/, @파일), 배열 요청의 정책 키, 구조체 변경 요청(struct_change),
  빌드 로그 되먹임(--build-log, buildlog), MCP 경유 호출과 중계 서버 참고 구현, 환경값 축과 프로필(--axis, --profile),
  --caller, --no-chunk, 실행 사이 공용 캐시
  M11 명령줄    run / preflight / status / rules / selftest / llmcheck

[로그 규칙]  모든 로그 줄은  "시각 수준 [모듈][함수명] 내용"  형식이다.
             실행 폴더의 logs/run.log 에 전부 남는다. 오류가 나면 이 접두어로
             어느 구획의 어느 함수인지 바로 찾을 수 있다.

[구현하지 않은 것]  (17_추가_0.9.0.md 5절 참조)
  - 프로젝트 변환과 전체 빌드(환경 등급 '다')
  - .rc/.idl/.def 의 규칙 탐지
  - 보내기 전에 토큰 수를 정확히 세는 것. 글자 수로 추정하고, 서버가 응답에 담아 준 실제 값으로 보정한다
  - DB, HMI 제품의 판별 세부 규칙(널리 쓰이는 DB 접근 기술과 HMI 구성 요소는 0.6.0 에서 기본 규칙으로 넣었다)
  - 컴파일러 수준의 영향범위 분석. 배열·표 변환의 사용처 분류는 글자 기준의 근사다
  - .vcxproj 가 가져오는 .props 파일의 설정
  - 평면 배열(레코드 간격 stride)의 구조체 변환, 열 타입을 바꿀 때의 값 범위 검사, 유형 사전(config/types.json)
  - 실제 sLLM 으로 확인한 것은 없다. 요청문의 의미 분석, 조각 구성, 수용 검사는 모두 모의 모델로만 확인했다
"""

import argparse
import bisect
import collections
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
VERSION = "0.9.1"

STAGES = [("S0", "준비"), ("S1", "인벤토리"), ("S2", "탐지"), ("S3", "설정흡수"),
          ("S4", "규칙적용"), ("S5", "영향분석"), ("S6", "sLLM수정"), ("S7", "병합"),
          ("S8", "검증"), ("S9", "산출")]

EXIT_OK, EXIT_REVIEW, EXIT_FAIL, EXIT_INPUT = 0, 1, 2, 3
EXIT_PENDING = 4                                  # sLLM 에 보내지 못한 작업이 남았다(호출 예산, 서버 한도). --resume 으로 이어서 보낸다


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
        "extra_body": {},            # 요청 본문에 그대로 더할 값(추론 모드 끄기 등 서버별 옵션. 요구사항 D13)
        "calibrate_first": True,     # 첫 요청 1건으로 실제 토큰 수를 확인한 뒤 나머지를 보낸다
    },
    "limits": {
        "big_init_chars": 2000,      # 이보다 긴 초기화 목록은 sLLM에 보내지 않고 자리표시자로 바꾼다
        "slice_context_lines": 25,   # 수정 지점 앞뒤로 같이 보낼 줄 수
        "cluster_gap_lines": 30,     # 이 줄 수 안의 수정 지점은 한 작업으로 묶는다
        "chunk_enabled": True,       # 한도를 넘는 구간을 나눠 순서대로 처리(분할 순차). False 면 수동 검토로 보낸다
        "chunk_ledger_chars": 1500,  # 뒤 조각에 넘기는 "앞 조각에서 바꾼 내용"의 최대 글자 수
        "ask_max_sites": 0,          # 사용자 요청문 하나가 sLLM 에 맡길 줄 수의 상한. 0 이면 두지 않는다(양은 --max-calls 로 조절한다)
        "task_max_targets": 8,       # 독립 수정: 작업 하나에 넣는 수정 지점의 최대 수. 0 이면 나누지 않는다(0.7.1 방식)
        "task_max_lines": 150,       # 독립 수정: 작업 하나의 첫 지점과 끝 지점 사이의 최대 줄 수. 0 이면 보지 않는다
        "ref_decl": True,            # 대상 줄에 쓰인 이름의 선언과 감싼 구문의 머리를 [참고]로 붙인다
        "ref_chars": 1500,           # [참고: 선언]에 넣는 글자 수의 상한
        "new_name_check": "mark",    # 수정에 조각·패키지 어디에도 없는 이름이 생겼을 때: mark(검토 메모만) / reject(기각하고 다시 요청) / off
        "known_names": [],           # 새 이름 검사에서 아는 이름으로 칠 것(사내 공용 함수 등). 표준 C·CRT 의 흔한 이름은 내장돼 있다
        "word_suffixes": ["s", "er", "ger", "ing", "ging", "ed", "ged"],   # 요청문: 낱말 뒤에 붙어도 같은 낱말로 보는 꼴(log -> logs, logger, logging)
        "ask_max_candidates": 600,   # 요청문: 낱말로 고른 후보 이름의 최대 수(많이 쓰인 순서로 자른다)
        "ask_check_over": 8,         # 요청문: 낱말로 고른 후보가 이 수를 넘으면 sLLM 에 번호로 추리게 한다. 0 이면 묻지 않고 전부 대상으로 한다
        "decl_change_check": True,   # 선언을 없앴는데 다른 줄에서 쓰이거나, 이미 있는 이름을 다시 선언한 수정을 기각한다
        "size_arg_check": True,      # 새로 쓴 sizeof(이름) 의 이름이 포인터로 확정되면 기각한다
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


ENV_AXES = (("os", "OS"), ("db", "DB"))        # 컴파일러 말고 원본/대상을 받는 환경값(키, 표시 이름). 축을 늘리려면 여기와 명령줄 인자에 더한다


def _env_text(args, name):
    return " ".join(str(getattr(args, name, None) or "").split())[:80]


def env_axis_text(a):
    """원본/대상 값을 한 줄로: 'Windows 7 -> Windows 11', 대상만 있으면 'Windows 11', 원본만 있으면 'Windows 7 (대상 미지정)'"""
    f, t = (a or {}).get("from") or "", (a or {}).get("to") or ""
    if f and t and f.lower() != t.lower():
        return "%s -> %s" % (f, t)
    return t or (("%s (대상 미지정)" % f) if f else "")


def build_env(args):
    """명령줄 인자로 환경값을 만든다. 주지 않은 값은 기본값(B9). (프로필 파일과 환경값 축은 0.9.0 에서 뺐다)"""
    f, t, pol = {}, {}, {}
    if getattr(args, "from_vs", None):
        f["vs"] = args.from_vs
    if getattr(args, "to_vs", None):
        t["vs"] = args.to_vs
    if getattr(args, "cpp_standard", None):
        t["cpp_standard"] = args.cpp_standard
    for key in ("platform", "conformance", "charset"):
        if getattr(args, key, None):
            t[key] = getattr(args, key)
    if getattr(args, "deprecated", None):
        pol["deprecated_warnings"] = args.deprecated
    if "vs" not in f or "vs" not in t:
        raise UserInputError("원본과 대상의 VS 제품 연도가 필요합니다. --from-vs 2008 --to-vs 2022 처럼 "
                             "줍니다.")
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
        # OS 와 DB 의 원본/대상(선택). 제품 이름과 판을 글로 적는다(예: "Windows 7", "Oracle 19c"). 쓰이는 곳:
        # 모든 sLLM 요청의 [환경], 보고서, WINVER 를 올릴 값(to_os), 규칙의 발동 조건(when 의 os, db, os_changed, db_changed)
        "os": {"from": _env_text(args, "from_os"), "to": _env_text(args, "to_os")},
        "db": {"from": _env_text(args, "from_db"), "to": _env_text(args, "to_db")},
    }
    if std in ("c++20", "c++latest") and env["conformance"] == "off":
        env["conformance"] = "on"
        LOGC.warn("언어 표준 %s 은(는) 준수 모드(/permissive-)를 함께 켠다(VS2019 16.11 부터). 준수 모드를 켠 것으로 처리한다. "
                  "끄려면 프로젝트에 /permissive 를 따로 줘야 한다" % std)
    LOGC.info("환경값 확정: VS %d(%s) -> VS %d(%s), 언어 표준 %s, 준수 모드 %s, 플랫폼 %s, 문자셋 %s, "
              "deprecated 경고 %s" % (from_vs, env["from_toolset"], to_vs, toolset, std or "(선택 없음)",
                                    env["conformance"], env["platform"], env["charset"],
                                    env["policy"]["deprecated_warnings"]))
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
    for key, label in ENV_AXES:
        txt = env_axis_text(env.get(key))
        if txt:
            lines.append("%s: %s" % (label, txt))
    return lines


def _load_request_file(args):
    """요청 파일(--request) 읽기. 없으면 빈 요청.
    features: 기능 변경(배열 -> 구조체 등). 규칙으로 처리할 수 있는 부분은 규칙으로 처리한다.
    common  : 공통 요청. 문장 목록이며 sLLM 에 보내는 모든 조각의 [수정 지침] 뒤에 붙는다. 규칙 수정에는 영향이 없다."""
    if not getattr(args, "request", None):
        return {"features": [], "common": [], "_asks_raw": []}
    path = find_config(args.request, "requests")
    req = load_json(path, "요청사항")
    feats = req.get("features") or []
    base = os.path.dirname(os.path.abspath(path))
    for ft in feats:
        if "id" not in ft or "type" not in ft:
            raise UserInputError("요청사항의 각 항목에는 id 와 type 이 필요합니다: %r" % (ft,))
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
            old = [k for k in _ARR_OLD_KEYS if k in ft]
            if old:
                LOGC.warn("요청 %s: %s 은(는) 0.9.0 에서 뺀 키라 읽지 않습니다. 기계적으로 못 바꾸는 사용처는 sLLM 에 맡기고, "
                          "하나라도 실패하면 요청 전체를 되돌립니다" % (ft["id"], ", ".join(old)))
    common = req.get("common") or []
    if isinstance(common, str):
        common = [common]
    common = [" ".join(str(x).split()) for x in common if str(x).strip()]
    if sum(len(x) for x in common) > 1500:
        raise UserInputError("공통 요청(common)이 너무 깁니다(합계 1,500자 이내). 모든 sLLM 요청에 붙으므로 짧게 적습니다.")
    LOGC.info("요청사항 읽음: %s (기능 변경 %d건, 공통 요청 %d건)" % (path, len(feats), len(common)))
    raw = []                                  # 요청 파일의 asks: 요청문 글의 목록(글 또는 {"text"})
    for it in req.get("asks") or []:
        raw.append(str(it.get("text", "")) if isinstance(it, dict) else str(it))
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


_ASK_MAX_CHARS = 4000


def load_request(args):
    """요청사항 전체를 읽는다.
      - 요청 파일(--request)의 정형 요청(features: 배열 -> 구조체), 공통 요청(common), 요청문(asks: 글의 목록)
      - 명령줄의 요청문(--ask. 줄 때마다 요청 하나)과 요청문 파일(--ask-file. 파일 전체가 요청 하나. # 으로 시작하는 줄은 뺀다)
    요청문에는 정해진 형식이 없다. 적힌 글을 그대로 sLLM 의 의미 분석에 보내고(S5 의 _analyze_asks), 거기서 의도별로 나뉜다.
    돌려주는 값: {"features", "common", "common_base", "asks_raw": [{"id", "text"}], "asks": []}. 실행 정보에 저장된다."""
    req = _load_request_file(args)
    texts = list(req.pop("_asks_raw", []))
    path = getattr(args, "ask_file", None)
    if path:
        if not os.path.isfile(path):
            raise UserInputError("요청문 파일이 없습니다: %s" % path)
        body = "\n".join(l for l in read_log_text(path).splitlines() if not l.lstrip().startswith("#")).strip()
        if body:
            texts.append(body)
    texts += [str(x) for x in (getattr(args, "ask", None) or [])]
    req["asks_raw"] = []
    for t in texts:
        t = str(t).replace("\ufeff", "").strip()
        if not t:
            continue
        if len(t) > _ASK_MAX_CHARS:
            raise UserInputError("요청문이 너무 깁니다(%d자. 한 건에 %d자 이내). 요청을 나눠 --ask 를 여러 번 줍니다." % (len(t), _ASK_MAX_CHARS))
        req["asks_raw"].append({"id": "ASK-%d" % (len(req["asks_raw"]) + 1), "text": t})
    req["asks"] = []
    req["common"] = list(req.get("common") or [])
    req["common_base"] = list(req["common"])
    if req["asks_raw"]:
        LOGC.info("요청문 %d건을 읽음(영향범위 단계에서 sLLM 이 의도별로 나눈다)" % len(req["asks_raw"]))
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
    o = {"no_llm": False, "no_compile": False, "compat_llm": False, "only": [], "exclude": [], "caller": "",
         "max_parallel": 0, "compile_parallel": -1, "disable_rules": [], "exceptions": ""}
    if saved is not None:
        o.update({k: saved[k] for k in o if k in saved})
    for k in ("no_llm", "no_compile", "compat_llm"):
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
    v = getattr(args, "max_calls", None)      # 호출 예산은 실행할 때마다 새로 준다(이어서 실행할 때 앞의 값을 물려받지 않는다)
    if v is not None and int(v) < 1:
        raise UserInputError("--max-calls 는 1 이상이어야 합니다: %s" % v)
    o["max_calls"] = int(v) if v is not None else 0
    if saved is None:                         # 예외 파일도 처음 실행의 값을 그대로 쓴다
        o["disable_rules"] = [x.strip() for v in (getattr(args, "disable_rule", None) or []) for x in str(v).split(",") if x.strip()]
        for key, what in (("exceptions", "예외 파일"),):
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
        self._blocks, self.blocks_ok = None, False       # 블록 목록은 get_blocks 가 필요할 때 만든다

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

    def get_blocks(self):
        """블록 목록(block_tree). 처음 부를 때 한 번 만든다. 프로젝트 고유의 괄호 매크로를 반영한 본문으로 만든다."""
        if self._blocks is None:
            self._blocks, self.blocks_ok = block_tree(_apply_brace_macros(self.struct))
        return self._blocks


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
# [M04 구조분석]  블록, 제어문 범위, 문장 경계, 선언 찾기, 초기화 목록의 모양 (0.8.0)
# 목적: "이 위치가 어떤 구문 안인가", "이 이름은 어디서 어떻게 선언됐는가"를 컴파일러 없이 답한다.
#       대상 판단(S5), 조각 구성(S6), 수용 검사(S6)가 모두 여기에 기댄다(15_구분별_검토현황과_구현명세 3절).
# 방식: 주석·문자열·전처리기를 가린 본문(Doc.struct)에서 괄호 짝과 문장 끝(;)만 보고 판단한다.
# 한계: 전처리기를 실행하지 않는다. 괄호를 숨긴 매크로, #if 로 갈라진 문장은 틀릴 수 있다. 그런 곳은
#       certain=False 나 None 으로 돌려주고, 부르는 쪽이 "확정 아님"으로 다룬다.
# 이 구획의 함수
#   블록          block_tree, block_at, func_at
#   제어문·문장   ctrl_ranges, is_stmt_start, stmt_start_of
#   선언          decl_regex, find_decl
#   초기화 목록   init_shape
#   그 밖         const_value, comment_spans, word_parts
# ============================================================================
LOGS = Log("M04.구조분석")

_CTRL_HEAD_RE = re.compile(r"^\s*(?:else\s+)?(if|for|while|switch|catch)\b")
_CONTAINER_NAME_RE = re.compile(r"\b(?:class|struct|union|namespace)\s+([A-Za-z_]\w*)")
_NOT_DECL_WORDS = {"return", "delete", "goto", "case", "else", "throw", "new", "sizeof", "typedef", "using", "break", "continue"}
_NOT_CALL_WORDS = {"if", "for", "while", "switch", "return", "sizeof", "catch", "defined", "else", "do", "case", "new", "delete",
                   "throw", "static_cast", "reinterpret_cast", "const_cast", "dynamic_cast", "typeid", "alignof", "decltype",
                   "operator", "void", "int", "char", "long", "short", "unsigned", "signed", "float", "double", "bool",
                   "wchar_t", "auto", "noexcept", "__declspec", "__attribute__", "__pragma"}


def block_tree(struct):
    """중괄호 블록의 목록을 만든다(여는 위치 순서). 돌려주는 값: (블록 목록, 괄호 짝이 맞았는지)
    블록: {"kind", "name", "head", "open", "close", "parent"}. head 는 머리글이 시작하는 위치, parent 는 목록의 번호(없으면 -1).
    kind: func, container(class/struct/union/namespace), init(= 뒤의 초기화 목록), if, for, while, switch, catch, else, do, try, other
    초기화 목록 안쪽의 중괄호(표의 행)는 블록으로 남기지 않는다. 150만 자 표에서 행이 수만 개여도 느려지지 않게 하기 위한 것이다."""
    blocks, stack, ok, stmt, skip = [], [], True, 0, 0
    for m in _BR_RE.finditer(struct):
        i = m.start()
        c = struct[i]
        if c == ";":
            stmt = i + 1
            continue
        if c == "{":
            if skip or (stack and blocks[stack[-1]]["kind"] == "init"):
                skip += 1                                   # 초기화 목록 안쪽: 깊이만 센다
                continue
            header, hstart = struct[stmt:i], stmt
            if header.count(")") > header.count("("):
                # for (a; b; c) 의 머리: 괄호 안의 ; 에서 문장이 끊겨 "c)" 만 남았다. 앞의 for 까지 거슬러 올라가 머리를 다시 잡는다
                back = struct.rfind("for", max(0, stmt - 2000), stmt)
                while back >= 0:
                    cand = struct[back:i]
                    if (re.match(r"for\s*\(", cand) and cand.count("(") == cand.count(")")
                            and (back == 0 or not (struct[back - 1].isalnum() or struct[back - 1] == "_"))):
                        header, hstart = cand, back
                        break
                    back = struct.rfind("for", max(0, stmt - 2000), back)
            hs = header.strip()
            parent = stack[-1] if stack else -1
            pkind = blocks[parent]["kind"] if parent >= 0 else None
            kind, name = "other", ""
            if hs.endswith("="):
                kind = "init"
            elif pkind in (None, "container"):
                hs2 = _DECLSPEC_RE.sub("", hs)
                if _ENUM_RE.match(hs2):
                    kind = "other"
                elif _CONTAINER_RE.match(hs2) and ")" not in hs2:
                    kind = "container"
                    nm = _CONTAINER_NAME_RE.search(hs2)
                    name = nm.group(1) if nm else ""
                elif ")" in hs:
                    fname, _pos = _func_name(header)
                    if fname:
                        kind, name = "func", fname
            else:
                cm = _CTRL_HEAD_RE.match(hs)
                if cm:
                    kind = cm.group(1)
                elif hs in ("else", "do", "try"):
                    kind = hs
                elif _CONTAINER_RE.match(hs) and ")" not in hs:   # 함수 안에서 정의한 구조체
                    kind = "container"
                    nm = _CONTAINER_NAME_RE.search(hs)
                    name = nm.group(1) if nm else ""
            blocks.append({"kind": kind, "name": name, "head": hstart + (len(header) - len(header.lstrip())),
                           "open": i, "close": -1, "parent": parent})
            stack.append(len(blocks) - 1)
            stmt = i + 1
        else:
            if skip:
                skip -= 1
                continue
            if not stack:
                ok = False
            else:
                blocks[stack.pop()]["close"] = i + 1
            stmt = i + 1
    if stack or skip:
        ok = False
    for b in blocks:                                         # 닫히지 않은 블록은 파일 끝까지로 둔다(위치 판단이 멈추지 않게)
        if b["close"] < 0:
            b["close"] = len(struct)
    return blocks, ok


def block_at(blocks, pos, kinds=None):
    """pos 를 포함하는 가장 안쪽 블록의 번호(없으면 -1). kinds 를 주면 그 종류인 가장 안쪽 블록을 찾는다.
    블록 안쪽이란 여는 중괄호 뒤부터 닫는 중괄호 앞까지다."""
    lo, hi = 0, len(blocks)
    while lo < hi:                                           # open <= pos 인 마지막 블록
        mid = (lo + hi) // 2
        if blocks[mid]["open"] < pos:
            lo = mid + 1
        else:
            hi = mid
    k = lo - 1
    while k >= 0 and not (blocks[k]["open"] < pos < blocks[k]["close"]):
        k = blocks[k]["parent"] if blocks[k]["open"] < pos else k - 1
    while k >= 0 and kinds and blocks[k]["kind"] not in kinds:
        k = blocks[k]["parent"]
    return k


def func_at(doc, pos):
    """pos 를 본문에 품은 함수(Doc.funcs 의 항목). 없으면 None."""
    for f in doc.funcs:
        if f["body_start"] < pos < f["end"]:
            return f
    return None


def _match_fwd(s, pos, o, c, limit=400000):
    """pos 의 여는 괄호 o 와 짝이 되는 닫는 괄호 c 의 위치(없으면 -1). limit 글자 안에서만 찾는다."""
    d = 0
    for i in range(pos, min(len(s), pos + limit)):
        ch = s[i]
        if ch == o:
            d += 1
        elif ch == c:
            d -= 1
            if d == 0:
                return i
    return -1


_CTRL_KW_RE = {k: re.compile(r"(?<![\w#])%s\s*\(" % k) for k in ("if", "for", "while", "switch")}
_ELSE_RE = re.compile(r"\s*else\b")


def _stmt_end(s, pos):
    """pos 에서 시작하는 문장 하나의 끝 위치(끝 문자 다음). 중괄호 블록이면 짝까지, 제어문이면 그 범위까지, 아니면 ; 까지."""
    n = len(s)
    while pos < n and s[pos].isspace():
        pos += 1
    if pos >= n:
        return n
    if s[pos] == "{":
        e = _match_fwd(s, pos, "{", "}")
        return e + 1 if e >= 0 else n
    for k, rx in _CTRL_KW_RE.items():
        if rx.match(s, pos):
            return _ctrl_end(s, pos, k)[1]
    m = re.compile(r"(?:else|do|try)\b").match(s, pos)
    if m:
        e = _stmt_end(s, m.end())
        if s.startswith("do", pos):                           # do 본문 뒤의 while (...) ;
            w = re.compile(r"\s*while\s*\(").match(s, e)
            if w:
                c = _match_fwd(s, w.end() - 1, "(", ")", 5000)
                sc = s.find(";", c) if c >= 0 else -1
                return sc + 1 if sc >= 0 else e
        return e
    d = 0
    for i in range(pos, n):
        ch = s[i]
        if ch in "({[":
            d += 1
        elif ch in ")}]":
            if d == 0:
                return i                                      # 감싼 블록의 끝에 닿았다(; 없이 끝난 문장)
            d -= 1
        elif ch == ";" and d == 0:
            return i + 1
    return n


def _ctrl_end(s, pos, kind):
    """pos 에서 시작하는 제어문의 (조건 괄호 끝, 문장 끝). 조건 괄호의 짝을 못 찾으면 (-1, -1).
    if 는 뒤따르는 else 와 else if 까지 한 범위로 본다."""
    p = s.find("(", pos)
    c = _match_fwd(s, p, "(", ")", 5000) if p >= 0 else -1
    if c < 0:
        return -1, -1
    end = _stmt_end(s, c + 1)
    if kind == "if":
        m = _ELSE_RE.match(s, end)
        if m:
            end = _stmt_end(s, m.end())
    return c + 1, end


def ctrl_ranges(struct, kinds=("if",)):
    """제어문의 범위 목록. 돌려주는 값: [{"kind", "start", "cond_end", "end"}] (시작 위치 순서), 범위를 못 정한 개수
    else if 로 이어진 것은 맨 앞 if 의 범위에 들어 있으므로 따로 올리지 않는다. do 의 꼬리인 while (...) ; 도 올리지 않는다.
    struct 는 전처리기까지 가린 본문이어야 한다(#if 는 가려져 있어 잡히지 않는다)."""
    out, bad = [], 0
    for k in kinds:
        k2 = {"loop": None}.get(k, k)
        for kw in (("for", "while") if k2 is None else (k2,)):
            for m in _CTRL_KW_RE[kw].finditer(struct):
                pre = struct[max(0, m.start() - 12):m.start()].rstrip()
                if kw == "if" and pre.endswith("else"):
                    continue                                  # else if: 앞 if 의 연쇄에 들어 있다
                ce, end = _ctrl_end(struct, m.start(), kw)
                if ce < 0:
                    bad += 1
                    continue
                if kw == "while" and pre.endswith("}") and struct[ce:end].strip() == ";":
                    continue                                  # do { } while (...); 의 꼬리
                out.append({"kind": kw, "start": m.start(), "cond_end": ce, "end": end})
    out.sort(key=lambda r: r["start"])
    return out, bad


def is_stmt_start(struct, pos):
    """pos(줄의 시작)가 문장이 시작할 수 있는 자리인가: 그 앞의 마지막 코드 문자가 ; { } 이거나 파일의 처음이다."""
    i = pos - 1
    while i >= 0 and struct[i].isspace():
        i -= 1
    return i < 0 or struct[i] in ";{}"


def stmt_start_of(struct, pos):
    """pos 를 품은 문장이 시작하는 위치(첫 코드 문자). 선언 머리를 찾을 때 쓴다(예: 큰 초기화 목록의 '{' 에서 선언의 처음으로)."""
    i = pos - 1
    d = 0
    while i >= 0:
        ch = struct[i]
        if ch in ")]":
            d += 1
        elif ch in "([":
            d -= 1
        elif ch in ";{}" and d <= 0:
            break
        i -= 1
    i += 1
    while i < pos and struct[i].isspace():
        i += 1
    return i


def decl_regex(name):
    """문장 하나가 name 을 선언하는지 보는 정규식. 그룹 1 은 이름 뒤의 [..] 들이다.
    '타입 [*&] 이름 [..] (= , ; { ( 또는 끝)' 꼴을 본다. 앞 선언자를 건너뛰는 꼴(char *a, name[10])도 받는다."""
    return re.compile(r"^\s*(?:(?:const|static|unsigned|signed|long|short|struct|volatile|constexpr|thread_local|extern|register|mutable)\s+)*"
                      r"(?!(?:%s)\b)[A-Za-z_][\w:]*(?:\s*<[^;{}]*>)?(?:\s*(?:const|\*|&))*[\s\*&]+"
                      r"(?:[^;=(){}]*?,\s*[\*&\s]*)?%s\s*((?:\[[^\]]*\])*)\s*(?:=|,|\{|\(|$)"
                      % ("|".join(sorted(_NOT_DECL_WORDS)), re.escape(name)), re.S)


def _decl_info(kind, stmt, name, arr, pos, head=""):
    """선언 문장에서 배열·포인터 여부를 읽어 결과 dict 를 만든다."""
    text = " ".join(stmt.split())
    before = text.split(name)[0] if name in text else text
    # 포인터 여부: 이름 바로 앞 선언자에 * 가 붙었는가(char *a, b[10] 에서 b 는 포인터가 아니다)
    last = before.rsplit(",", 1)[-1]
    return {"kind": kind, "text": text[:200], "pos": pos, "is_array": bool(arr), "is_pointer": "*" in last and not arr,
            "certain": True, "head": head}


def find_decl(struct, pos, name, big=()):
    """pos 에서 쓰인 name 의 선언을 함수 안에서 찾는다. 돌려주는 값: dict 또는 None
      kind: "local"(지역 변수) / "param"(함수 인자) / "outside"(함수 안에는 선언이 없다. head 에 함수 머리글이 들어 있다)
    방법: pos 에서 거꾸로 가며 문장을 하나씩 본다. 이미 닫힌 블록( } ... { ) 안의 선언은 범위 밖이므로 보지 않는다.
          감싼 블록의 '{' 를 만나면 그 블록의 첫 문장도 검사한 뒤 바깥 블록으로 나간다. 함수 머리에 닿으면 인자 목록을 본다.
    big: 큰 초기화 목록의 (시작, 끝) 목록. 그 안은 건너뛴다(수만 줄짜리 표를 한 글자씩 보지 않기 위한 것)."""
    rx = decl_regex(name)
    spans = sorted(big)
    depth, end, i, steps = 0, pos, pos - 1, 0
    while i >= 0 and steps < 600000:
        steps += 1
        for s0, e0 in spans:                                  # 큰 초기화 목록 안이면 그 시작으로 건너뛴다
            if s0 < i < e0 - 1:
                i = s0
                break
        ch = struct[i]
        if ch == "}":
            if depth == 0:
                stmt = struct[i + 1:end]
                m = rx.match(stmt)
                if m:
                    return _decl_info("local", stmt, name, m.group(1), i + 1)
            depth += 1
        elif ch == "{":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    end = i
            else:
                stmt = struct[i + 1:end]
                m = rx.match(stmt)
                if m:
                    return _decl_info("local", stmt, name, m.group(1), i + 1)
                hs = max(struct.rfind(";", 0, i), struct.rfind("}", 0, i), struct.rfind("{", 0, i)) + 1
                head = struct[hs:i]
                if ")" in head and not re.match(r"\s*(?:else\s+)?(?:if|for|while|switch|catch|do|try|else)\b", head):
                    pm = re.search(r"[(,]\s*([^(),]*?[\s\*&])%s\s*(\[[^\]]*\])?\s*(?:=[^,)]*)?[,)]" % re.escape(name), head)
                    if pm:
                        return _decl_info("param", pm.group(1) + name + (pm.group(2) or ""), name, pm.group(2), hs, head)
                    return {"kind": "outside", "text": "", "pos": hs, "is_array": False, "is_pointer": False,
                            "certain": False, "head": " ".join(head.split())}
                fm = re.search(r"\bfor\s*\(([^;]*);", head)   # for (int i = 0; ...) 의 선언
                if fm:
                    m2 = rx.match(fm.group(1))
                    if m2:
                        return _decl_info("local", fm.group(1), name, m2.group(1), hs)
                end = i
        elif ch == ";" and depth == 0:
            stmt = struct[i + 1:end]
            m = rx.match(stmt)
            if m:
                return _decl_info("local", stmt, name, m.group(1), i + 1)
            end = i
        i -= 1
    return None


def init_shape(struct, open_pos):
    """초기화 목록( = { ... } )의 모양을 센다. open_pos 는 여는 중괄호의 위치다.
    돌려주는 값: {"end", "rows", "row_counts": {요소 수: 행 수}, "flat": 맨 바깥 깊이의 요소 수, "macro_rows", "ok"}
      rows       안쪽 중괄호로 묶인 행의 수(2차원 표의 행)
      flat       안쪽 중괄호 없이 놓인 요소의 수(1차원 배열, 또는 괄호를 생략한 표)
      macro_rows 행을 매크로 호출로 쓴 표인가(이름( 꼴이 바깥 깊이에 있다). 그러면 요소 수를 믿을 수 없다"""
    depth = rows = n = flat = 0
    had = macro = False
    counts = {}
    end = -1
    for i in range(open_pos, len(struct)):
        ch = struct[i]
        if ch == "{":
            depth += 1
            if depth == 2:
                n, had = 0, False
        elif ch == "}":
            if depth == 2:
                rows += 1
                k = n + (1 if had else 0)
                counts[k] = counts.get(k, 0) + 1
            elif depth == 1 and had:
                flat += 1
            depth -= 1
            if depth == 0:
                end = i + 1
                break
            if depth == 1:
                had = False                                    # 행 하나가 끝났다. 바깥 깊이의 요소로는 세지 않는다
        elif depth == 2:
            if ch == ",":
                n += 1
                had = False
            elif not ch.isspace():
                had = True
        elif depth == 1:
            if ch == ",":
                if had:
                    flat += 1
                had = False
            elif ch == "(":
                macro = True
            elif not ch.isspace():
                had = True
    return {"end": end, "rows": rows, "row_counts": counts, "flat": flat, "macro_rows": macro, "ok": end > 0}


def const_value(expr, names):
    """정수 상수식의 값을 구한다. names 는 {이름: 값}. 숫자, 이름, + - * / 와 괄호만 받는다. 그 밖이면 None.
    eval 을 쓰지 않는다(소스에서 읽은 글자를 실행하지 않기 위한 것)."""
    toks = re.findall(r"0[xX][0-9a-fA-F]+[uUlL]*|\d+[uUlL]*|[A-Za-z_]\w*|[-+*/()]|\S", expr)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def atom():
        t = peek()
        if t is None:
            return None
        pos[0] += 1
        if t == "(":
            v = add()
            if peek() != ")":
                return None
            pos[0] += 1
            return v
        if t in "+-":
            v = atom()
            return None if v is None else (v if t == "+" else -v)
        if re.match(r"0[xX]|\d", t):
            return int(t.rstrip("uUlL"), 0)
        if re.match(r"[A-Za-z_]", t):
            return names.get(t)
        return None

    def mul():
        v = atom()
        while v is not None and peek() in ("*", "/"):
            op = toks[pos[0]]
            pos[0] += 1
            w = atom()
            if w is None or (op == "/" and w == 0):
                return None
            v = v * w if op == "*" else int(v / w)
        return v

    def add():
        v = mul()
        while v is not None and peek() in ("+", "-"):
            op = toks[pos[0]]
            pos[0] += 1
            w = mul()
            if w is None:
                return None
            v = v + w if op == "+" else v - w
        return v

    if not toks:
        return None
    v = add()
    return v if pos[0] == len(toks) else None


def comment_spans(text):
    """주석의 (시작, 끝) 목록. mask_code 와 같은 토큰 규칙을 쓴다."""
    return [m.span() for m in _TOKEN_RE.finditer(text) if m.group()[0] == "/"]


def word_parts(name):
    """식별자를 낱말로 쪼갠다(소문자). writeCertVerifLog -> [write, cert, verif, log], WR_EVT_LOG -> [wr, evt, log]"""
    return [w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", name)]


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
                r"_makepath|mbstowcs|wcstombs|_itoa|_ltoa|_ultoa|_i64toa|_ui64toa|_itow|_ltow|_ultow|_strlwr|_strupr|"
                r"_wcslwr|_wcsupr|_mktemp|_ecvt|_fcvt|_gcvt)\s*\(",
     "setting": "전처리기 정의 추가: _CRT_SECURE_NO_WARNINGS",
     "llm_guidance": "이 함수를 보안 CRT 함수로 바꾼다(strcpy -> strcpy_s, sprintf -> sprintf_s, _itoa -> _itoa_s, fopen -> fopen_s 등). "
                     "버퍼 크기 인자가 필요하다. 대상 버퍼가 이 조각이나 [참고: 선언]에서 배열로 확인될 때만 _countof(이름)이나 sizeof(이름)을 "
                     "크기로 넣는다. 포인터이거나 크기를 알 수 없으면 그 줄은 고치지 않는다. 인자의 순서나 반환값이 달라지는 함수"
                     "(fopen, strtok, localtime, getenv 등)는 결과를 쓰는 줄까지 이 조각 안에서 함께 고칠 수 있을 때만 고친다. "
                     "고칠 줄이 하나도 없으면 NO_CHANGE 를 쓴다."},
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
     "setting": "전처리기 정의 추가: _WINSOCK_DEPRECATED_NO_WARNINGS",
     "llm_guidance": "사용 중단된 Winsock 함수를 새 함수로 바꾼다(inet_ntoa -> inet_ntop, inet_addr -> inet_pton, gethostbyname -> getaddrinfo). "
                     "인자와 반환값의 꼴이 다르다. 결과를 쓰는 줄까지 이 조각 안에서 함께 고칠 수 있을 때만 고친다. 아니면 NO_CHANGE 를 쓴다."},
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
    for key, label in ENV_AXES:                    # OS, DB 조건: 값(원본 -> 대상)에 그 낱말이 있을 때 / 원본과 대상이 다를 때
        a = env.get(key) or {}
        have = env_axis_text(a)
        if key in w and str(w[key]).lower() not in ("%s -> %s" % (a.get("from") or "", a.get("to") or "")).lower():
            return False, "%s 에 '%s' 이(가) 있을 때만 해당(지금 %s)" % (label, w[key], have or "미지정")
        if w.get(key + "_changed") and not (a.get("from") and a.get("to") and a["from"].lower() != a["to"].lower()):
            return False, "%s 이(가) 바뀔 때만 해당(지금 %s)" % (label, have or "미지정")
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
    for key, label in ENV_AXES:
        if key in w:
            parts.append("%s~%s" % (label, w[key]))
        if w.get(key + "_changed"):
            parts.append("%s 변경" % label)
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
           "policy": {"deprecated_warnings": "fix"}}
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
    os_to = (((env or {}).get("os") or {}).get("to") or "").strip()
    want = winver_for_os(os_to)                    # 대상 OS(--to-os)를 알면 그 값으로, 모르면 Windows 7 값으로 올린다
    new = want or "0x0601"
    why = ("대상 OS %s" % os_to) if want else "대상 OS 를 몰라 지금의 Visual Studio 가 지원하는 하한인 Windows 7 값으로 함"
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
#           오류표(빌드해야 드러난다. 오류 코드를 알려 주기만 한다) / 환경값 / 없음(다루지 않음)
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




















def llm_probe(llm_cfg):
    """서버에 모델 목록을 물어 토큰 한도를 받아 온다(vLLM 은 /models 응답에 max_model_len 을 준다).
    돌려주는 값: (한도 또는 None, 모델 이름 목록). 서버가 이 주소를 제공하지 않으면 (None, []).
    서버가 허용 동시 수(max_parallel), 예약량(reserve_tokens), 출력 상한(max_output_tokens)도 알려 주면
    llm_cfg["_server_info"] 에 담는다(S6 가 설정값보다 작은 쪽을 쓴다)."""
    llm_cfg["_server_info"] = {}
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
                if re.search(r"(?<![\w/-])[/-]sdl(?![\w-])", ta.get("AdditionalOptions", "")):
                    cfg["cl"]["SDLCheck"] = "true"          # 추가 옵션에 /sdl 을 직접 적은 경우
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
                v = _vcx_tag(b, "SDLCheck")                 # SDL 검사(/sdl): C4996 등을 오류로 올린다
                if v is not None:
                    c["cl"]["SDLCheck"] = v
                if re.search(r"(?<![\w/-])[/-]sdl(?![\w-])", _vcx_tag(b, "AdditionalOptions") or ""):
                    c["cl"]["SDLCheck"] = "true"
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
            if val("SDLCheck") in ("true", "1"):
                add(pf, name, "SDL 검사(/sdl)", "사용 중단 경고(C4996)가 빌드 오류가 된다. 2절의 전처리기 정의(_CRT_SECURE_NO_WARNINGS 등)를 "
                    "빠짐없이 적용하거나, 전환이 끝날 때까지 SDL 검사를 끈다")
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
        self._aux = {}                            # 파일별 참고 재료(plan_tasks 가 만든다)
        self._gdecl = self._classes = None        # 파일 범위 선언 색인, 구조체 본문 색인(처음 필요할 때 만든다)
        self._names = None                        # 아는 이름(코드 + 규칙 지침. 새 이름 검사에 쓴다. 처음 필요할 때 만든다)
        self._code_names = None                   # 코드에 실제로 나오는 이름(요청문 분석에 쓴다)
        self._calls = None                        # 호출 꼴로 쓰인 이름의 색인(요청문 분석에서 낱말로 이름을 고를 때 쓴다)
        self._snips = {}                          # 심볼 정의 조각의 기억
        self.llm_calls = 0                        # 이번 실행에서 실제로 보낸 sLLM 호출 수(--max-calls 예산에 쓴다)

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
# [S5 영향분석]  요청문의 의미 분석, 대상 찾기, 배열·표 -> 구조체, 헤더 의존 관계
# 목적: (1) 요청문(--ask)을 sLLM 에 보내 의도별로 나누고(_analyze_asks), 의도마다 코드에서 대상 줄을 찾아 sLLM 대상 이슈로 넣는다(_user_asks).
#       (2) 배열·표 -> 구조체(요청 파일의 features 와 요청문의 배열 의도): 이름이 나온 곳을 전부 분류하고, 기계적으로 되는 곳은
#           수정 레코드로, 나머지 사용처는 sLLM 대상 이슈로 만든다(_array_request). 맡긴 곳이 실패하면 요청 전체를 되돌린다(S7).
#       (3) --compat-llm 이 켜져 있으면 설정으로 흡수하던 항목의 사용 줄을 sLLM 대상 이슈로 넣는다(_compat_sites).
#       (4) 헤더를 include 하는 파일 목록을 만들어 둔다(헤더가 바뀌면 재확인할 대상).
# 산출: issues(source='request', 'compat'), edits(origin='request'), meta(contracts, ask_state, dependents), out/ask_plan.json
# 이 단계는 sLLM 을 부른다(요청문의 분석과 후보 확인). 수정 요청은 S6 에서 보낸다.
# ============================================================================
LOG5 = Log("S5.영향분석")
_NOT_TYPE_WORDS = {"return", "else", "case", "delete", "new", "sizeof", "throw", "goto"}






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


# ---------------------------------------------------------------------------
# 배열·표 -> 구조체 (0.8.0 에서 다시 씀. 15_구분별_검토현황과_구현명세 4절, 6.2 의 D2, 7.2 의 E5)
# 순서: _array_scan(선언 찾기, 기호 구분, 사용처 전수 분류) -> _array_gate(적용할지 판정) -> _array_apply(수정 레코드)
# 원칙: (1) 같은 이름이라도 다른 기호(구조체 멤버, 같은 이름의 지역 변수)는 건드리지 않는다
#       (2) 이름이 나온 곳을 전부 세고 하나도 남기지 않고 분류한다. 처리 범위 밖 파일도 센다
#       (3) 기계적으로 못 바꾸는 곳이 있고 정책이 stop 이면 아무것도 적용하지 않는다(선언과 사용처가 어긋나면 컴파일되지 않는다)
#       (4) 초기화 목록(데이터)은 수정 범위에 넣지 않는다. 선언의 '타입 이름[크기]' 부분만 바꾼다
# ---------------------------------------------------------------------------
_COUNTOF_RE = re.compile(r"\b(?:_countof|ARRAYSIZE|_ARRAYSIZE|ARRAY_SIZE|RTL_NUMBER_OF|NELEM|countof|dimof)\s*\(\s*$")
_ARR_CLASS_LABEL = OrderedDict([
    ("decl", "선언"), ("const_index", "상수 인덱스"), ("var_index", "변수 인덱스"), ("col_const", "열 상수"), ("col_var", "열 변수"),
    ("row_bare", "행을 통째로 사용"), ("bare", "이름을 통째로 사용"), ("mem_arg", "메모리·입출력 함수의 인자"), ("addr_name", "이름의 주소(&이름)"),
    ("decl_odd", "선언(지원하지 않는 꼴)"), ("countof", "요소 수 매크로(_countof 등)"), ("sizeof_name", "sizeof(이름)"), ("sizeof_row", "sizeof(행)"), ("sizeof_elem", "sizeof 안의 요소"),
    ("other_symbol", "다른 기호(같은 이름)"), ("out_of_scope", "처리 범위 밖 파일")])


_ARR_OLD_KEYS = ("variable_index", "bare_use", "on_unresolved", "llm_variable_index", "llm_other_uses", "convert_declaration")


def _array_policy(ft):
    """배열 요청의 처리 방식. 0.9.0 부터 한 가지다(정책 키는 뺐다).
    기계적으로 바꿀 수 있는 곳(선언, 상수 인덱스 등)은 규칙으로, 그 밖의 사용처(변수 인덱스, 통째 사용)는 sLLM 에 맡기고,
    맡긴 곳이 하나라도 실패하면 그 요청의 수정을 전부 되돌린다(일부만 바뀐 결과는 컴파일되지 않는다)."""
    return {"var": "llm", "bare": "llm", "on_unresolved": "stop", "legacy": False, "convert": True}


def _site_sub(code, ne, shape):
    """이름 뒤가 옛 접근 꼴인가. 1차원은 이름[..], 2차원은 이름[..][..] 이다. 구조체로 바뀐 뒤에 남으면 컴파일되지 않는 꼴이다."""
    a = ne
    while a < len(code) and code[a] in " \t":
        a += 1
    if a >= len(code) or code[a] != "[":
        return False
    if shape != "2d":
        return True
    e1 = _match_fwd(code, a, "[", "]", 4000)
    if e1 < 0:
        return False
    b = e1 + 1
    while b < len(code) and code[b] in " \t":
        b += 1
    return b < len(code) and code[b] == "["


def _count_sub(code, target, shape):
    """본문에서 옛 접근 꼴의 수를 센다(잔여 재탐지용)."""
    return sum(1 for m in re.finditer(r"\b%s\b" % re.escape(target), code) if _site_sub(code, m.end(), shape))


def _bracket_end(code, pos):
    """pos 의 '[' 와 짝이 되는 ']' 의 위치(없으면 -1)."""
    return _match_fwd(code, pos, "[", "]", 4000)


def _array_decl_rx(target):
    """'타입 이름[크기]' 또는 '타입 이름[행][열]' 로 시작하는 선언(줄의 처음). 초기화 목록은 잡지 않는다."""
    return re.compile(r"(?m)^(?P<indent>[ \t]*)(?P<pre>(?:(?:extern|static|const|volatile)\s+)*)(?P<type>[A-Za-z_][\w \t]*?[\w\*])\s*"
                      r"\b%s\s*\[\s*(?P<d1>[^\]\n]*)\]\s*(?:\[\s*(?P<d2>[^\]\n]*)\]\s*)?(?==|;)" % re.escape(target))


def _array_scan(ctx, ft):
    """배열(표)의 선언을 정하고, 이름이 나온 곳을 전부 분류한다. 돌려주는 값: 계획 dict
      ok, why          선언과 구조체 정의를 정했는가, 못 정한 이유
      shape            "1d" 또는 "2d"
      elem, n, rows    요소 타입, 필드 수(1차원은 요소 수, 2차원은 열 수), 2차원의 행 크기 글자
      struct, definition, fields   구조체 이름, 정의 본문, {인덱스: 필드 이름}
      decls            바꿀 선언 목록 [{rel, start, end, indent, pre, d1, in_func, primary}]
      sites            사용처 목록 [{rel, start, end, line, cls, ...}]. decl 과 other_symbol, out_of_scope 도 들어 있다
      counts, total    분류별 건수, 이름이 나온 전체 건수(둘의 합이 같아야 한다)
      warnings         사람이 알아야 할 것(## 매크로, 모양 등)"""
    st, target = ctx.store, ft["target"]
    plan = {"ok": False, "why": "", "shape": "1d", "sites": [], "decls": [], "counts": OrderedDict(), "total": 0, "warnings": [],
            "definition": "", "struct": "", "fields": {}, "elem": "", "n": 0, "rows": "", "const": False}
    files = [(r["path"], r["status"]) for r in st.q("SELECT path, status FROM files WHERE kind='source' ORDER BY path")]
    name_rx = re.compile(r"\b%s\b" % re.escape(target))
    decl_rx = _array_decl_rx(target)
    # ---- 1) 선언 후보를 모으고 종류를 가른다: 파일 범위 / 함수 안 / 구조체 멤버
    cands = []
    for rel, status in files:
        d = get_doc(ctx, rel)
        if not d.ok or target not in d.code:
            continue
        blocks = d.get_blocks()
        for m in decl_rx.finditer(d.code):
            if m.group("type").split()[-1] in _NOT_TYPE_WORDS:
                continue
            k = block_at(blocks, m.start("type"))
            while k >= 0 and blocks[k]["kind"] == "container" and re.search(r"\bnamespace\b|\bextern\b", d.struct[blocks[k]["head"]:blocks[k]["open"]]):
                k = blocks[k]["parent"]                      # namespace, extern "C" 안은 파일 범위로 본다
            where = "file" if k < 0 else ("member" if blocks[k]["kind"] == "container" else "func")
            fk = block_at(blocks, m.start("type"), ("func",))
            dims_end = (m.end("d2") if m.group("d2") is not None else m.end("d1")) + 1
            cands.append({"rel": rel, "status": status, "start": m.start("pre") if m.group("pre") else m.start("type"), "end": dims_end,
                          "tail": m.end(),
                          "line_start": m.start(), "indent": m.group("indent"), "pre": m.group("pre") or "",
                          "type": " ".join(m.group("type").split()), "d1": m.group("d1").strip(), "d2": m.group("d2"),
                          "where": where, "extern": "extern" in (m.group("pre") or ""), "static": "static" in (m.group("pre") or ""),
                          "func": (blocks[fk]["open"], blocks[fk]["close"], blocks[fk]["name"]) if fk >= 0 else None,
                          "has_init": d.code[m.end():m.end() + 1] == "="})
    file_decls = [c for c in cands if c["where"] == "file"]
    func_decls = [c for c in cands if c["where"] == "func"]
    scope, chosen = "", []
    if file_decls:
        defs = [c for c in file_decls if not c["extern"]]
        if ft.get("defined_in"):
            want = norm_rel(ft["defined_in"]).lower()
            defs = [c for c in defs if c["rel"].lower() == want]
        def_files = sorted({c["rel"] for c in defs})
        if len(def_files) > 1:
            plan["why"] = "같은 이름의 정의가 여러 파일에 있음(%s). 요청에 defined_in 을 준다" % ", ".join(def_files[:4])
            return _array_fill_other(ctx, ft, plan, files, name_rx, cands, [])
        if defs and all(c["static"] for c in defs):
            scope, chosen = "file", [c for c in file_decls if c["rel"] == defs[0]["rel"]]
        else:
            scope, chosen = "global", file_decls
    elif func_decls:
        if ft.get("function"):
            func_decls = [c for c in func_decls if c["func"] and c["func"][2].split("::")[-1] == ft["function"].split("::")[-1]]
        if len(func_decls) != 1:
            plan["why"] = ("함수 안의 선언이 %d곳 있음. 요청에 function(함수 이름)을 준다" % len(func_decls)) if func_decls else "요청의 function 에 그 선언이 없음"
            return _array_fill_other(ctx, ft, plan, files, name_rx, cands, [])
        scope, chosen = "function", func_decls
    else:
        plan["why"] = "배열의 선언을 패키지에서 찾지 못함"
        return _array_fill_other(ctx, ft, plan, files, name_rx, cands, [])
    plan["scope"] = scope
    # ---- 2) 선언끼리 타입과 크기가 같은지, 1차원인지 2차원인지
    src_files = [rel for rel, _s in files]
    types = {c["type"] for c in chosen}
    is2d = {c["d2"] is not None for c in chosen}
    why = ""
    if len(types) != 1:
        why = "선언마다 요소 타입이 다름(%s)" % ", ".join(sorted(types))
    elif len(is2d) != 1:
        why = "선언마다 차원이 다름(1차원과 2차원이 섞여 있음)"
    elif ft.get("stride"):
        why = "평면 배열을 레코드 간격으로 나누는 변환(stride)은 이 판에서 지원하지 않음"
    shape = "2d" if True in is2d else "1d"
    plan["shape"], plan["elem"] = shape, (sorted(types)[0] if types else "")
    plan["const"] = any("const" in c["pre"] or re.search(r"\bconst\b", c["type"]) for c in chosen)
    size_texts = sorted({(c["d2"] if shape == "2d" else c["d1"]).strip() for c in chosen if (c["d2"] if shape == "2d" else c["d1"]).strip()})
    is_int = lambda x: re.match(r"^%s$" % _INT_LIT, x) is not None
    mapping = ft["mapping"]
    need_names = set(k for k in mapping if not is_int(k))
    for t in size_texts:
        need_names.update(re.findall(r"[A-Za-z_]\w*", t))
    vals = _resolve_names(ctx, src_files, sorted(need_names))
    n = None
    if not why:
        sv = {const_value(t, vals) for t in size_texts}
        if len(sv) != 1 or None in sv:
            why = "배열 크기를 정할 수 없음(%s)" % (", ".join(size_texts) or "크기를 적은 선언이 없음")
        else:
            n = sv.pop()
            if not 0 < n <= 1024:
                why = "%s(%d)가 구조체의 필드 수로 맞지 않음(1~1024)" % ("열 수" if shape == "2d" else "배열 크기", n)
    idx = {}
    if not why:
        for k, field in mapping.items():
            v = _to_int(k) if is_int(k) else vals.get(k)
            if v is None:
                why = "인덱스 이름 %s 의 값을 패키지에서 찾지 못함(#define 이나 enum 이 없음)" % k
                break
            if v in idx or not 0 <= v < n:
                why = "인덱스 %s(%d)가 다른 것과 겹치거나 크기(%d)를 벗어남" % (k, v, n)
                break
            idx[v] = field
    if not why:
        elem = plan["elem"]
        sname = ft.get("struct_name") or (target.upper() + ("_ROW_T" if shape == "2d" else "_T"))
        ftypes = ft.get("types") or {}
        body = []
        for i in range(n):
            if i in idx:
                body.append("%s %s;" % (ftypes.get(idx[i], elem), idx[i]))
            elif ft.get("unmapped", "reserved") != "drop":
                idx.setdefault(i, None)
                body.append("%s reserved_%d;" % (elem, i))
        lines = ["typedef struct %s" % sname, "{"] + ["    " + b for b in body] + ["} %s;" % sname]
        plan.update(ok=True, n=n, struct=sname, definition="\n".join(lines),
                    fields={i: (idx[i] if idx.get(i) else "reserved_%d" % i) for i in range(n) if i in idx},
                    rows=sorted({c["d1"] for c in chosen if c["d1"]})[0] if shape == "2d" and any(c["d1"] for c in chosen) else "")
        heads = [c for c in chosen if os.path.splitext(c["rel"])[1].lower() in (".h", ".hh", ".hpp", ".hxx")]
        prim = (heads or [c for c in chosen if not c["extern"]] or chosen)[0]
        prim_base = os.path.basename(prim["rel"]).lower()
        seen_files = set()
        for c in chosen:
            c["primary"] = c is prim
            # 이 파일이 정의가 든 파일을 직접 include 하지 않으면, 이 파일의 첫 선언 앞에도 정의를 넣어야 타입이 보인다
            if c is prim:
                c["need_def"] = True
                seen_files.add(c["rel"])
            elif c["rel"] not in seen_files:
                seen_files.add(c["rel"])
                incs = {os.path.basename(norm_rel(r["header"])).lower() for r in st.q("SELECT header FROM includes WHERE path=?", (c["rel"],))}
                c["need_def"] = c["rel"] != prim["rel"] and prim_base not in incs
            else:
                c["need_def"] = False
        if sum(1 for c in chosen if c.get("need_def")) > 1:  # 정의가 여러 파일에 들어가면 같은 번역 단위에서 두 번 보일 수 있다. 가드로 막는다
            guard = re.sub(r"\W", "_", sname).upper() + "_DEFINED"
            plan["definition_guarded"] = "#ifndef %s\n#define %s\n%s\n#endif" % (guard, guard, plan["definition"])
            plan["warnings"].append("구조체 정의를 %d개 파일에 넣었다(정의가 든 파일을 include 하지 않는 선언이 있음). 중복을 막는 가드(%s)로 감쌌다. 공용 헤더로 모으는 것을 검토한다"
                                    % (sum(1 for c in chosen if c.get("need_def")), guard))
        plan["decls"] = chosen
        # 초기화 목록의 모양(보고용). 데이터는 바꾸지 않으므로 모양이 달라도 변환은 유효하다
        for c in chosen:
            if c["has_init"]:
                d = get_doc(ctx, c["rel"])
                op = d.struct.find("{", c["tail"])
                if op >= 0 and not d.struct[c["tail"] + 1:op].strip():
                    sh = init_shape(d.struct, op)
                    plan["init"] = {"rel": c["rel"], "chars": (sh["end"] - op) if sh["ok"] else 0, "rows": sh["rows"],
                                    "row_counts": sh["row_counts"], "flat": sh["flat"], "macro_rows": sh["macro_rows"]}
                    if shape == "2d" and sh["rows"] == 0 and sh["flat"]:
                        plan["warnings"].append("초기화 목록에 안쪽 중괄호가 없다(평면으로 적은 표). 구조체 배열에서도 유효하지만 검토한다")
                    if sh["macro_rows"]:
                        plan["warnings"].append("초기화 목록의 행이 매크로 호출로 보인다. 요소 수를 확인하지 못했다")
                    over = [k for k in sh["row_counts"] if shape == "2d" and k > n]
                    if over:
                        plan["warnings"].append("요소가 열 수(%d)보다 많은 행이 있다(%s개짜리)" % (n, ", ".join(str(k) for k in over)))
    plan["why"] = why
    return _array_fill_other(ctx, ft, plan, files, name_rx, cands, chosen, vals)


def _array_fill_other(ctx, ft, plan, files, name_rx, cands, chosen, vals=None):
    """이름이 나온 곳을 전부 분류해 plan["sites"] 에 채운다(선언을 못 정했어도 건수를 보고하기 위해 항상 한다)."""
    target, shape, n = ft["target"], plan["shape"], plan["n"]
    vals = dict(vals or {})
    scope = plan.get("scope", "")
    chosen_spans = {(c["rel"], c["start"]): c for c in chosen}
    cand_spans = [(c["rel"], c["line_start"], c["end"], c) for c in cands]
    def_func = chosen[0]["func"] if scope == "function" and chosen else None
    def_file = chosen[0]["rel"] if scope in ("function", "file") and chosen else None
    decl_rx_any = decl_regex(target)
    shadow_rx = re.compile(r"[\w\*&\]>]\s*[\s\*&]\s*%s\s*(?:\[|=|;|,|\))" % re.escape(target))
    sites, pending_idx = [], []
    for rel, status in files:
        d = get_doc(ctx, rel)
        if not d.ok or target not in d.code:
            continue
        code, blocks = d.code, None
        shadow_cache = {}
        for m in name_rx.finditer(code):
            pos, ne = m.start(), m.end()
            plan["total"] += 1
            before = code[max(0, pos - 240):pos]
            ls = code.rfind("\n", 0, pos) + 1
            le = code.find("\n", pos)
            le = len(code) if le < 0 else le
            s = {"rel": rel, "start": pos, "end": ne, "name_end": ne, "line": d.line_of(pos), "cls": "", "note": "",
                 "in_macro": code[ls:le].lstrip().startswith("#"), "text": " ".join(d.text[ls:le].split())[:160],
                 "sub": _site_sub(code, ne, shape)}
            sites.append(s)
            # (가) 선언 후보의 범위 안인가
            cand = next((c for r0, a, b, c in cand_spans if r0 == rel and a <= pos < b), None)
            if cand is not None:
                if (rel, cand["start"]) in chosen_spans:
                    s.update(cls="decl", end=cand["end"], decl=cand)
                else:
                    s.update(cls="other_symbol", note={"member": "구조체 멤버의 선언", "func": "같은 이름의 지역 변수 선언"}.get(cand["where"], "다른 파일의 같은 이름"))
                continue
            # (가2) 줄의 처음이 아닌 자리의 선언인가(예: void f() { int g[4]; }). 문장의 처음부터 이름 뒤 [..] 까지를 선언 꼴에 대 본다
            if blocks is None:
                blocks = d.get_blocks()
            bk = block_at(blocks, pos)
            while bk >= 0 and blocks[bk]["kind"] == "container" and re.search(r"\bnamespace\b|\bextern\b", d.struct[blocks[bk]["head"]:blocks[bk]["open"]]):
                bk = blocks[bk]["parent"]
            if bk >= 0 and blocks[bk]["kind"] == "container":
                s.update(cls="other_symbol", note="구조체나 클래스의 멤버")
                continue
            if not s["in_macro"]:
                q = ne
                while q < len(code) and code[q] in " \t":
                    q += 1
                while q < len(code) and code[q] == "[":
                    qe = _bracket_end(code, q)
                    if qe < 0:
                        break
                    q = qe + 1
                if decl_rx_any.match(d.struct[stmt_start_of(d.struct, pos):q]):
                    if bk >= 0:
                        s.update(cls="other_symbol", note="같은 이름의 지역 변수 선언")
                    else:
                        s.update(cls="decl_odd", note="지원하지 않는 꼴의 선언(한 문장에 여러 변수를 선언했거나 줄의 처음이 아님)")
                    continue
            # (나) 범위(scope) 밖이면 다른 기호다
            if scope == "function" and not (rel == def_file and def_func[0] < pos < def_func[1]):
                s.update(cls="other_symbol", note="대상 배열이 선언된 함수 밖")
                continue
            if scope == "file" and rel != def_file:
                s.update(cls="other_symbol", note="대상 배열은 다른 파일의 static 이다")
                continue
            # (다) 멤버 접근, 이름 공간 지정
            if re.search(r"(?:\.|->)\s*$", before):
                s.update(cls="other_symbol", note="다른 것의 멤버(. 또는 ->)")
                continue
            if re.search(r"\w\s*::\s*$", before):
                s.update(cls="other_symbol", note="이름 앞에 범위 지정(::)이 있음")
                continue
            # (라) 같은 이름의 지역 변수나 인자가 있는 함수 안인가
            if scope != "function":
                fk = block_at(blocks, pos, ("func",))
                if fk >= 0:
                    if fk not in shadow_cache:                # 함수마다 한 번: 선언처럼 보이는 꼴이 있는지 먼저 본다(빠른 길)
                        shadow_cache[fk] = bool(shadow_rx.search(d.struct, blocks[fk]["head"], blocks[fk]["close"]))
                    if shadow_cache[fk]:
                        dc = find_decl(d.struct, pos, target, d.inits)
                        if dc and dc["kind"] in ("local", "param"):
                            s.update(cls="other_symbol", note="같은 이름의 지역 변수나 인자")
                            continue
            # (마) 처리 범위 밖 파일
            if status != "대상":
                s.update(cls="out_of_scope", note="이 파일은 처리 범위 밖이다(%s)" % status)
                continue
            # (바) 쓰인 꼴
            in_sizeof = bool(re.search(r"\bsizeof\s*\(?\s*$", before))
            a = ne
            while a < len(code) and code[a] in " \t":
                a += 1
            if a < len(code) and code[a] == "[":
                e1 = _bracket_end(code, a)
                if e1 < 0:
                    s.update(cls="bare", note="인덱스 괄호의 짝을 찾지 못함")
                    continue
                s.update(i1=(a, e1 + 1), end=e1 + 1)
                if shape == "2d":
                    b2 = e1 + 1
                    while b2 < len(code) and code[b2] in " \t":
                        b2 += 1
                    if b2 < len(code) and code[b2] == "[":
                        e2 = _bracket_end(code, b2)
                        if e2 > 0:
                            s.update(i2=(b2, e2 + 1), end=e2 + 1, cls="sizeof_elem" if in_sizeof else "col?")
                            pending_idx.append((s, code[b2 + 1:e2]))
                            continue
                    s["cls"] = "sizeof_row" if in_sizeof else "row_bare"
                else:
                    s["cls"] = "sizeof_elem" if in_sizeof else "idx?"
                    if not in_sizeof:
                        pending_idx.append((s, code[a + 1:e1]))
                continue
            cm = _COUNTOF_RE.search(before)
            close = code.find(")", ne) if cm else -1
            if cm and close >= 0 and not code[ne:close].strip():
                # _countof(이름): 1차원 배열이 구조체가 되면 배열이 아니다. 호출 전체를 sizeof 식으로 바꾼다. 2차원 표는 여전히 배열이라 그대로 둔다
                s.update(cls="countof", call=(pos - (len(before) - cm.start()), close + 1))
            elif in_sizeof:
                s["cls"] = "sizeof_name"
            elif before.rstrip().endswith("&") and not before.rstrip().endswith("&&"):
                s["cls"] = "addr_name"
            elif re.search(r"\b(?:%s)\s*\((?:[^;()]|\([^()]*\))*$" % _MEM_CALLS, before) and re.search(r"[(,]\s*$", before):
                s["cls"] = "mem_arg"
            else:
                s["cls"] = "bare"
    # 인덱스 식의 값: 식에 나온 이름을 한 번에 찾는다
    need = set()
    for _s, expr in pending_idx:
        need.update(x for x in re.findall(r"[A-Za-z_]\w*", expr) if x not in vals)
    if need and len(need) <= 4000:
        vals.update(_resolve_names(ctx, [rel for rel, _st in files], sorted(need)))
    for s, expr in pending_idx:
        v = const_value(expr, vals)
        in_range = v is not None and (n == 0 or 0 <= v < n)
        if s["cls"] == "idx?":
            s["cls"] = "const_index" if in_range else "var_index"
        elif s["cls"] == "col?":
            s["cls"] = "col_const" if in_range else "col_var"
        if in_range:
            s["value"] = v
        elif v is not None:
            s["note"] = "인덱스 값 %d 이(가) 크기를 벗어남" % v
    if ft.get("unmapped", "reserved") == "drop" and plan["ok"]:
        for s in sites:
            if s["cls"] in ("const_index", "col_const") and s.get("value") not in plan["fields"]:
                s.update(cls="var_index" if s["cls"] == "const_index" else "col_var", note="매핑에 없는 인덱스(unmapped: drop)")
    for s in sites:
        plan["counts"][s["cls"]] = plan["counts"].get(s["cls"], 0) + 1
    plan["sites"] = sites
    # 이름을 만들어 내는 매크로(##)는 글자 검색으로 사용처를 찾지 못한다. 그런 매크로가 있으면 알린다
    for rel, _status in files:
        d = get_doc(ctx, rel)
        if d.ok and "##" in d.code:
            for mm in re.finditer(r"(?m)^[ \t]*#[ \t]*define[^\n]*?(\w*)[ \t]*##[ \t]*(\w*)[^\n]*$", d.code):
                a, b = mm.group(1), mm.group(2)
                if (len(a) >= 2 and target.startswith(a) and a != target) or (len(b) >= 2 and target.endswith(b) and b != target):
                    plan["warnings"].append("이름을 만들어 내는 매크로가 있다(%s %d줄: %s). 이 매크로로 만든 접근은 찾지 못한다"
                                            % (rel, d.line_of(mm.start()), " ".join(mm.group(0).split())[:90]))
    return plan


def _array_gate(ft, plan, policy):
    """요청을 적용할지 판정한다(원자성). 돌려주는 값: (적용 여부, 적용하지 않는 사유 목록)
    하나라도 걸리면 선언도 사용처도 바꾸지 않는다. 일부만 바꾼 결과는 컴파일되지 않기 때문이다."""
    c, why = plan["counts"], []
    if not plan["ok"] and policy["convert"]:
        why.append("선언을 구조체로 바꿀 수 없음: %s" % plan["why"])
    if c.get("out_of_scope") and not ft.get("allow_out_of_scope"):
        why.append("처리 범위 밖 파일에 사용처 %d곳이 있음(범위를 넓히거나 요청에 allow_out_of_scope 를 준다)" % c["out_of_scope"])
    if c.get("decl_odd"):
        why.append("지원하지 않는 꼴의 선언 %d곳이 있음(사용처 목록의 '선언(지원하지 않는 꼴)'을 본다)" % c["decl_odd"])
    return (not why), why


def _array_apply(ctx, ft, plan, policy):
    """분류 결과대로 이슈와 수정 레코드를 만든다. 돌려주는 값: {처리 이름: 건수}
    기계적으로 바꾸는 것은 수정 레코드(검토 표시), sLLM 에 맡길 것은 llm=1 이슈, 사람이 볼 것은 수동 이슈로 남긴다."""
    st, target, shape = ctx.store, ft["target"], plan["shape"]
    sql_i = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    sql_e = ("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")
    title = "요청사항: 배열 %s 를 구조체 필드로" % target
    elem = plan["elem"]
    done = OrderedDict()
    taken = {}

    def bump(k):
        done[k] = done.get(k, 0) + 1

    def manual(s, d, what, note):
        cur = st.x(sql_i, (s["rel"], s["start"], s["end"], s["line"], "request", ft["id"], "T3", 0, "%s(%s)" % (title, what),
                           d.text[s["start"]:s["end"]][:160], None, 0, "manual", note))
        s["issue_id"] = cur.lastrowid
        bump(what + "(수동)")

    def to_llm(s, d, what, note):
        cur = st.x(sql_i, (s["rel"], s["start"], s["end"], s["line"], "request", ft["id"], "T3", 1, "%s(%s)" % (title, what),
                           s["text"], None, 0, "open", note))
        s["issue_id"] = cur.lastrowid
        bump(what + "(sLLM)")

    def put(s, d, a, b, after, what):
        """a~b 를 after 로 바꾸는 수정 레코드. 다른 수정과 겹치면 수동으로 남긴다."""
        tk = taken.setdefault(s["rel"], [(e["start"], e["end"]) for e in st.q(
            "SELECT start, end FROM edits WHERE path=? AND status='accepted'", (s["rel"],))])
        if any(x < b and a < y for x, y in tk):
            manual(s, d, what, "다른 수정과 범위가 겹쳐 바꾸지 못함")
            return
        cur = st.x(sql_i, (s["rel"], a, b, s["line"], "request", ft["id"], "T1", 0, "%s(%s)" % (title, what), d.text[a:b][:160],
                           after[:4000], 0, "open", "매크로 본문" if s.get("in_macro") else ""))
        st.x(sql_e, (s["rel"], "snapshot", a, b, d.text[a:b], after, "request", ft["id"], str(cur.lastrowid), 1, "accepted", ""))
        s["issue_id"] = cur.lastrowid
        tk.append((a, b))
        bump(what)

    for s in plan["sites"]:
        cls = s["cls"]
        if cls in ("other_symbol", "sizeof_name", "sizeof_row", "addr_name", "decl_odd"):
            continue                                           # 건드리지 않는다(건수만 보고)
        d = get_doc(ctx, s["rel"])
        if cls == "out_of_scope":
            manual(s, d, "범위 밖 파일의 사용처", s["note"] + ". 이 파일은 고치지 않았다")
            continue
        a0, ne = s["start"], s["name_end"]
        if cls == "decl":
            c = s["decl"]
            if not (plan["ok"] and policy["convert"]):
                manual(s, d, "배열 선언", "배열 선언. 구조체 정의로 교체 필요(변경 계약): %s" % (plan["why"] or "요청에서 선언 교체를 끔"))
                continue
            new = "%s%s %s%s" % (c["pre"], plan["struct"], target, ("[%s]" % c["d1"]) if shape == "2d" else "")
            if c.get("need_def"):                              # 구조체 정의는 첫 선언(헤더가 있으면 헤더) 앞에 넣는다. 정의가 안 보이는 다른 파일에도 넣는다
                if plan.get("definition_guarded"):
                    new = plan["definition_guarded"] + "\n" + c["indent"] + new
                else:
                    new = plan["definition"].replace("\n", "\n" + c["indent"]) + "\n" + c["indent"] + new
            put(s, d, c["start"], c["end"], new, "선언 교체")
        elif cls == "const_index":
            put(s, d, a0, s["end"], "%s.%s" % (target, plan["fields"].get(s["value"], "reserved_%d" % s["value"])), "필드 치환")
        elif cls == "col_const":
            put(s, d, s["i2"][0], s["i2"][1], ".%s" % plan["fields"].get(s["value"], "reserved_%d" % s["value"]), "필드 치환")
        elif cls == "sizeof_elem":
            put(s, d, a0, s["end"], elem, "sizeof 안의 요소 -> 타입")
        elif cls == "mem_arg":
            if shape == "1d":
                put(s, d, a0, ne, "&" + target, "주소 연산자")
        elif cls == "countof":
            if shape == "1d":
                put(s, d, s["call"][0], s["call"][1], "(sizeof(%s) / sizeof(%s))" % (target, elem), "요소 수 매크로 -> sizeof 식")
        elif cls == "var_index":
            to_llm(s, d, "변수 인덱스", "변수 인덱스 접근. sLLM 에 맡김(검토 필요)")
        elif cls == "col_var":
            to_llm(s, d, "열 변수", "열이 변수인 접근. sLLM 에 맡김(검토 필요)")
        elif cls == "row_bare":
            to_llm(s, d, "배열 전체 사용", "행을 통째로 쓰는 곳. sLLM 에 맡김(검토 필요)")
        elif cls == "bare":
            to_llm(s, d, "배열 전체 사용", "배열 이름을 통째로 쓰는 곳. sLLM 에 맡김(검토 필요)")
    return done


def _array_sites_report(ctx, ft, plan, applied, reasons):
    """요청 하나의 사용처 전수 목록을 out/request_<id>_sites.md 로 쓴다(적용했든 안 했든 쓴다)."""
    L = ["# 요청 %s: 배열 `%s` 의 사용처 목록" % (ft["id"], ft["target"]), "",
         "- 이름이 나온 곳 %d곳, 분류한 곳 %d곳" % (plan["total"], sum(plan["counts"].values())),
         "- 적용: %s" % ("함" if applied else "하지 않음"), ""]
    if reasons:
        L += ["## 적용하지 않은 이유", ""] + ["- " + r for r in reasons] + [""]
    if plan["warnings"]:
        L += ["## 알아 둘 것", ""] + ["- " + w for w in plan["warnings"]] + [""]
    L += ["## 분류별 건수", ""] + _table(["분류", "건수"], [(_ARR_CLASS_LABEL.get(k, k), v) for k, v in plan["counts"].items()]) + [""]
    for cls, label in _ARR_CLASS_LABEL.items():
        rows = [s for s in plan["sites"] if s["cls"] == cls]
        if not rows:
            continue
        L += ["## %s (%d곳)" % (label, len(rows)), ""]
        L += _table(["파일", "줄", "내용", "비고"], [(s["rel"], s["line"], "`%s`" % s["text"][:110].replace("|", "\\|"),
                                                (s["note"] + (" [매크로 본문]" if s.get("in_macro") else "")).strip()) for s in rows[:2000]])
        if len(rows) > 2000:
            L.append("- 그 밖 %d곳은 생략" % (len(rows) - 2000))
        L.append("")
    os.makedirs(ctx.p("out"), exist_ok=True)
    write_text(ctx.p("out", "request_%s_sites.md" % re.sub(r"[^\w\-]", "_", ft["id"])), "\n".join(L))


def _array_request(ctx, ft):
    """배열(표) -> 구조체 요청 하나를 처리한다. 돌려주는 값: 변경 계약 dict(보고서에 실린다)."""
    policy = _array_policy(ft)
    plan = _array_scan(ctx, ft)
    total, classified = plan["total"], sum(plan["counts"].values())
    labels = ", ".join("%s %d" % (_ARR_CLASS_LABEL.get(k, k), v) for k, v in plan["counts"].items())
    LOG5.info("요청 %s (배열 %s): 이름이 나온 곳 %d곳을 분류함(%s)" % (ft["id"], ft["target"], total, labels or "없음"))
    apply_ok, reasons = _array_gate(ft, plan, policy)
    if total != classified:                                    # 일어나면 안 되는 일. 어긋나면 적용하지 않는다
        apply_ok, reasons = False, reasons + ["분류한 수(%d)가 이름이 나온 수(%d)와 다름" % (classified, total)]
    for w in plan["warnings"]:
        LOG5.warn("요청 %s: %s" % (ft["id"], w))
    done = OrderedDict()
    if apply_ok:
        if plan["ok"] and policy["convert"]:
            ft["_const"] = bool(plan.get("const"))
            ft["_definition"], ft["_fields"], ft["_n"], ft["_elem"], ft["_shape"] = plan["definition"], plan["fields"], plan["n"], plan["elem"], plan["shape"]
        done = _array_apply(ctx, ft, plan, policy)
        LOG5.info("요청 %s: 적용함(%s)" % (ft["id"], ", ".join("%s %d곳" % kv for kv in done.items()) or "바꿀 곳 없음"))
        if plan.get("init"):
            LOG5.info("요청 %s: 초기화 목록 %d자는 그대로 둠(행 %d개, 행마다 요소 수 %s)"
                      % (ft["id"], plan["init"]["chars"], plan["init"]["rows"], plan["init"]["row_counts"] or plan["init"]["flat"]))
    else:
        for r in reasons:
            LOG5.warn("요청 %s 을(를) 적용하지 않습니다: %s" % (ft["id"], r))
        ctx.store.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                    "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual',?)",
                    (ft["id"], "요청사항: 배열 %s 를 구조체로(적용하지 않음)" % ft["target"], labels[:300],
                     "적용하지 않은 이유: " + " / ".join(reasons)[:600]))
    _array_sites_report(ctx, ft, plan, apply_ok, reasons)
    note = []
    if apply_ok and plan["ok"] and policy["convert"]:
        prim = next((c for c in plan["decls"] if c.get("primary")), None)
        note.append("구조체 정의를 %s 의 선언 앞에 넣었다." % (prim["rel"] if prim else "첫 선언"))
        note.append("빈 자리는 reserved_번호 로 채워 크기와 배치를 배열과 같게 두었다." if any(v.startswith("reserved_") for v in plan["fields"].values()) else "필드의 타입과 순서는 배열과 같다.")
        if plan.get("init"):
            note.append("초기화 목록 %d자는 바꾸지 않았다." % plan["init"]["chars"])
    elif apply_ok:
        note.append("배열 선언은 바꾸지 않았다(%s). 선언과 구조체 정의는 사람이 한다." % (plan["why"] or "요청에서 선언 교체를 끔"))
    note.append("사용처 전체 목록: out/request_%s_sites.md" % re.sub(r"[^\w\-]", "_", ft["id"]))
    counts = OrderedDict((_ARR_CLASS_LABEL.get(k, k), v) for k, v in plan["counts"].items())
    subs = {}                                               # 파일별: 옛 접근 꼴로 남아도 되는 수(다른 기호)와, 고쳐져야 하는 이슈 번호
    for x in plan["sites"]:
        if x.get("sub"):
            e = subs.setdefault(x["rel"], {"other": 0, "issues": []})
            if apply_ok and x.get("issue_id"):
                e["issues"].append(x["issue_id"])
            else:
                e["other"] += 1
    return {"id": ft["id"], "type": "array_to_struct", "subs": subs, "target": ft["target"], "rename": {}, "counts": counts,
            "definition_after": plan["definition"] if (apply_ok and plan["ok"] and policy["convert"]) else "",
            "note": " ".join(note), "applied": apply_ok, "reasons": reasons, "done": done, "warnings": plan["warnings"],
            "on_unresolved": policy["on_unresolved"], "shape": plan["shape"],
            "expect_left": sum(v for k, v in done.items() if k.endswith("(수동)") or k.endswith("(sLLM)"))}


def _contract_excerpt(ft, near_text):
    """배열 -> 구조체 계약 가운데 이 조각에 필요한 부분만 지침 글로 만든다(요구사항 B6, E3).
    구조체 정의 전체를 넣지 않는다. 조각에 나온 인덱스 이름과 필드만 넣고 나머지는 한 줄로 줄인다."""
    if not ft.get("_definition"):
        return ""
    names = set(re.findall(r"[A-Za-z_]\w*", near_text or ""))
    pairs = []
    for k, field in list(ft["mapping"].items()):
        if k in names or field in names:
            pairs.append("%s -> .%s" % (k, field))
    shown = ", ".join(pairs[:24]) if pairs else "(이 조각에 나온 인덱스 이름 없음)"
    kind = "행 구조체의 배열" if ft.get("_shape") == "2d" else "구조체"
    return (" 배열의 선언은 %s(으)로 바뀌었다(필드 %d개, 순서는 인덱스와 같음, 필드 타입 %s). 이 조각에 관련된 대응: %s. "
            "이름을 통째로 쓰던 곳은 주소(&)나 필드별 접근으로 고친다." % (kind, ft.get("_n", 0), ft.get("_elem", ""), shown))


# ---- 요청문(--ask)의 의미 분석 (0.9.0) ----------------------------------------------------------------
# 목적: 사용자가 --ask 로 준 글을 그대로 sLLM 에 보내 "의도별 묶음"(JSON)으로 받고, 그 JSON 으로 로컬에서 대상 코드를 찾는다.
# 흐름: (1) 요청 원문 + 원문에 나온 이름 가운데 코드에 실제로 있는 것 -> sLLM -> {"intents": [...]}
#       (2) 도구가 JSON 을 검증하고, 의도마다 이름·낱말·구문·파일 범위로 대상 줄을 찾는다(모델은 코드를 보지 못한다)
#       (3) 찾은 줄을 조각으로 잘라 "그 의도의 요청문 + 조각"을 sLLM 에 보내 수정 블록을 받는다(S6. 형식은 수정 블록 하나뿐이다)
# 원칙: 판단(요청을 어떻게 나누고 무엇을 찾을지)은 모델에 맡긴다. 도구는 모델이 낸 이름이 코드에 있는지만 확인한다.
#       없는 이름은 버리므로 없는 코드가 대상이 되지는 않는다. 못 찾은 의도는 이유와 함께 보고한다(조용히 0건으로 끝내지 않는다).
# 의도의 종류: modify(찾아서 고침) / find(찾아서 목록만) / array_to_struct(배열을 구조체로. 선언은 도구가 바꾼다)
#              compat(환경 호환성. 요청문으로는 처리하지 않고 규칙과 --compat-llm 이 맡는다) / constraint(모든 수정에 붙는 조건)
# 산출: out/ask_plan.json (요청별 분석 결과와 찾은 곳의 수), logs/llm/ask_*.txt (분석 요청과 응답 원문)
# 한계: 분석 프롬프트는 모의 모델로만 시험했다. 실제 sLLM 이 의도를 쓸 만하게 나누는지는 확인하지 못했다.
#       여기가 흔들리면 ANALYZE_USER 의 설명과 예를 고친다. 의도의 종류를 늘리려면 _ASK_TYPES 와 _analyze_asks 의 갈래를 더한다.
_ASK_TYPES = ("modify", "find", "array_to_struct", "compat", "constraint")
_ASK_CONSTRUCTS = ("any", "call", "if", "for", "while", "loop", "switch", "string", "comment")
_ASK_CTRL = ("if", "for", "while", "loop", "switch")
_WHERE_KO = {"any": "", "call": "함수 호출", "if": "if 문 안", "for": "for 문 안", "while": "while 문 안", "loop": "반복문 안",
             "switch": "switch 문 안", "string": "문자열 안", "comment": "주석 안"}
_CALL_SKIP = frozenset("if for while switch return sizeof catch defined else do case new delete throw operator typeid "
                       "static_cast dynamic_cast const_cast reinterpret_cast alignof decltype".split())
ANALYZE_SYSTEM = ("너는 C/C++ 코드 수정 도구의 요청 분석기다. 사용자의 요청을 의도별로 나눠 JSON 하나로만 답한다. "
                  "코드는 볼 수 없다. 설명, 머리말, 코드 블록 표시는 쓰지 않는다.")
ANALYZE_USER = """[환경]
{env}

[요청 원문]
{text}

[코드에서 확인된 이름] 요청 원문에 나온 영문 이름 가운데 패키지의 코드에 실제로 있는 것이다.
{hints}

[할 일] 요청을 의도별로 나눈다. 요청 하나에 여러 가지 일이 섞여 있으면 의도를 여러 개로 나눈다.
도구는 이 JSON 을 읽어 코드에서 대상을 찾은 뒤, 의도별로 그 코드 조각과 함께 수정 요청을 다시 보낸다.

[출력 형식] 아래 꼴의 JSON 하나만 쓴다.
{{"intents": [{{"type": "modify", "instruction": "", "names": [], "words": [], "construct": "any", "files": []}}]}}

- type: 아래 중 하나.
  modify  코드의 특정 대상을 찾아 고친다(가장 흔한 경우)
  find  고치지 않고 찾아서 목록만 낸다
  array_to_struct  배열(표)을 구조체로 바꾼다. names 에 배열 이름을 넣는다
  compat  컴파일러나 OS 의 판이 바뀌어 필요한 일반적인 호환성 수정(특정 대상을 가리키지 않는 것)
  constraint  모든 수정에 적용할 조건이나 금지 사항(예: 주석은 바꾸지 않는다)
- instruction: 이 의도만 떼어 낸 요청문 한두 문장. 무엇을 어떻게 고칠지 원문의 뜻을 빠짐없이 담는다.
- names: 코드에서 찾을 정확한 이름(함수, 변수, 타입, 매크로). 요청 원문에 적힌 이름만 넣는다. 바꾼 뒤의 새 이름은 넣지 않는다. 지어내지 않는다.
- words: 정확한 이름을 모를 때만 쓴다. 찾을 함수의 이름에 들어갈 만한 영어 낱말(3글자 이상, 8개까지). names 가 있으면 비운다.
- construct: 대상을 특정 구문으로 한정할 때만 쓴다. any, call(함수 호출), if, for, while, loop(반복문), switch, string(문자열 안), comment(주석 안) 중 하나.
- files: 요청이 특정 폴더나 파일을 가리킬 때의 경로 패턴(예: "Src/Net/*"). 없으면 비운다.

[예]
요청: WriteLog 호출에 줄 번호 인자를 추가하고, if 문 안에서 로그를 남기는 함수를 찾아 줘. 주석은 건드리지 마.
답: {{"intents": [{{"type": "modify", "instruction": "WriteLog 호출에 줄 번호 인자를 추가한다", "names": ["WriteLog"], "words": [], "construct": "call", "files": []}}, {{"type": "find", "instruction": "if 문 안에서 로그를 남기는 함수 호출을 찾는다", "names": [], "words": ["log", "trace", "debug", "print"], "construct": "if", "files": []}}, {{"type": "constraint", "instruction": "주석은 바꾸지 않는다", "names": [], "words": [], "construct": "any", "files": []}}]}}
{feedback}"""
CHECK_SYSTEM = "너는 C/C++ 코드 수정 도구의 후보 선별기다. 의도에 해당하는 후보의 번호만 JSON 으로 답한다. 설명은 쓰지 않는다."
CHECK_USER = """[의도]
{text}

[후보] 번호. 이름 (쓰인 횟수) 예: 쓰인 줄
{cands}

[출력 형식] 의도에 해당하는 후보의 번호를 아래 꼴의 JSON 하나로만 쓴다. 해당하는 것이 없으면 빈 목록을 쓴다.
{{"keep": [1, 4, 7]}}
{feedback}"""


def _call_index(ctx):
    """패키지에서 호출 꼴(이름 뒤에 여는 괄호)로 쓰인 이름의 색인. {이름: [횟수, 예로 쓸 줄]}. 처음 필요할 때 한 번 만든다."""
    if ctx._calls is None:
        idx = {}
        for r in ctx.store.q("SELECT path FROM files WHERE kind='source' AND status IN ('대상','읽기전용') ORDER BY path"):
            d = get_doc(ctx, r["path"])
            for m in re.finditer(r"(?<![\w#])([A-Za-z_]\w*)\s*\(", d.struct):
                n = m.group(1)
                if n in _CALL_SKIP or n in _CPP_WORDS:
                    continue
                e = idx.get(n)
                if e is None:
                    la, lb = d.line_span(d.line_of(m.start()))
                    idx[n] = [1, " ".join(d.text[la:lb].split())[:70]]
                else:
                    e[0] += 1
        ctx._calls = idx
    return ctx._calls


def _ask_model(ctx, system, user, tag):
    """분석용 호출 한 번. 같은 물음은 캐시에서 답한다. 요청과 응답은 logs/llm/<tag>.txt 에 남긴다. 돌려주는 값: (성공, 본문)"""
    st, llm = ctx.store, ctx.cfg["llm"]
    key = sha256_bytes((llm["model"] + "\0" + system + "\0" + user).encode("utf-8"))
    hit = st.one("SELECT response FROM llm_cache WHERE key=?", (key,))
    if hit is not None:
        return True, hit
    ok, content, ms, _info = llm_chat(llm, system, user)
    ctx.llm_calls += 1
    write_text(ctx.p("logs", "llm", "%s.txt" % tag),
               "[SYSTEM]\n%s\n\n[USER]\n%s\n\n[RESPONSE ok=%s %dms]\n%s\n" % (system, user, ok, ms, content))
    if ok:
        st.x("INSERT OR REPLACE INTO llm_cache(key,response) VALUES(?,?)", (key, content))
        st.commit()
    return ok, content


def _json_from(content, key):
    """모델의 답에서 key 를 가진 JSON 객체 하나를 꺼낸다. 앞뒤의 설명이나 코드 블록 표시는 무시한다. 없으면 None."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", content or ""):
        try:
            obj, _end = dec.raw_decode(content[m.start():])
        except ValueError:
            continue
        if isinstance(obj, dict) and key in obj:
            return obj
    return None


def _str_list(v):
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if isinstance(x, (str, int))] if isinstance(v, list) else []


def _uniq(items):
    out = []
    for x in items:
        if x not in out:
            out.append(x)
    return out


def _ask_hints(ctx, text):
    """요청 원문의 영문 이름 가운데 패키지에 실제로 있는 것과 그 종류. 돌려주는 값: [(이름, 설명)]
    \\w 는 한글도 받으므로 쓰지 않는다("WriteLog를"이 한 낱말이 된다)."""
    known, calls, gdecl, classes = _code_names(ctx), _call_index(ctx), _global_index(ctx), _class_index(ctx)
    out, seen = [], set()
    for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text):
        if n in seen or n in _CPP_WORDS or n not in known:
            continue
        seen.add(n)
        kind = []
        if n in calls:
            kind.append("함수 호출 %d곳" % calls[n][0])
        if n in gdecl:
            kind.append("전역 배열" if any(x[2] for x in gdecl[n]) else "전역 변수")
        if n in classes:
            kind.append("타입")
        out.append((n, ", ".join(kind) or "코드에 있는 이름"))
    return out


def _check_intents(obj, text):
    """모델이 낸 JSON 을 검증해 의도 목록으로 만든다. 돌려주는 값: (의도 목록 또는 None, 어긋난 이유)
    모르는 종류는 modify 로, 모르는 구문은 any 로 본다. 이름과 낱말은 영문 식별자 꼴만 받는다."""
    if not isinstance(obj, dict) or not isinstance(obj.get("intents"), list) or not obj["intents"]:
        return None, "intents 목록이 든 JSON 이 아님"
    out = []
    for it in obj["intents"][:12]:
        if not isinstance(it, dict):
            continue
        typ = str(it.get("type") or "modify").strip().lower()
        con = str(it.get("construct") or "any").strip().lower()
        out.append({
            "type": typ if typ in _ASK_TYPES else "modify",
            "instruction": " ".join(str(it.get("instruction") or "").split())[:600] or " ".join(text.split())[:600],
            "names": _uniq([n for n in _str_list(it.get("names")) if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", n)])[:30],
            "words": _uniq([w.lower() for w in _str_list(it.get("words")) if re.match(r"^[A-Za-z]{3,}$", w)])[:8],
            "construct": con if con in _ASK_CONSTRUCTS else "any",
            "files": _uniq([f.replace("\\", "/").strip("/").lower() for f in _str_list(it.get("files")) if f.strip()])[:5]})
    if not out:
        return None, "쓸 수 있는 의도가 없음"
    return out, ""


def analyze_ask(ctx, ask):
    """요청 원문 하나를 모델에 보내 의도별 묶음을 받는다. 형식이 어긋나면 사유를 붙여 한 번 다시 묻는다.
    돌려주는 값: (의도 목록 또는 None, 실패 사유)"""
    hints = _ask_hints(ctx, ask["text"])
    hint_txt = "\n".join("- %s (%s)" % h for h in hints[:40]) or "(없음)"
    env_txt = "\n".join(env_summary_lines(ctx.env))
    feedback, why = "", ""
    for attempt in (1, 2):
        ok, content = _ask_model(ctx, ANALYZE_SYSTEM, ANALYZE_USER.format(env=env_txt, text=ask["text"], hints=hint_txt, feedback=feedback),
                                 "ask_%s_analyze_%d" % (ask["id"], attempt))
        if not ok:
            return None, "의미 분석 호출 실패: %s" % str(content)[:160]
        intents, why = _check_intents(_json_from(content, "intents"), ask["text"])
        if intents:
            return intents, ""
        feedback = "\n[다시] 앞의 답은 쓸 수 없었다(%s). [출력 형식]의 JSON 하나만 쓴다.\n" % why
    return None, "모델의 답을 해석하지 못함(%s)" % why


def expand_words(ctx, words):
    """낱말이 '낱말 단위로' 든 호출 이름을 고른다(글자 포함으로 맞추지 않는다. log 로 Dialog 를 잡지 않기 위한 것이다).
    돌려주는 값: [(이름, 횟수, 예로 쓸 줄)] (횟수가 많은 순서)"""
    suf = [str(x) for x in (ctx.cfg["limits"].get("word_suffixes") or ["s", "er", "ger", "ing", "ging", "ed", "ged"])]
    forms = set()
    for w in words:
        forms.add(w)
        forms.update(w + s for s in suf)
    out = [(n, e[0], e[1]) for n, e in _call_index(ctx).items() if forms & set(word_parts(n))]
    return sorted(out, key=lambda x: (-x[1], x[0]))


def check_candidates(ctx, tag, text, cands):
    """낱말로 고른 후보 이름을 번호 목록으로 보내 의도에 맞는 번호를 받는다(150개씩 나눠 묻는다).
    돌려주는 값: (남긴 이름 목록, 알아 둘 것 목록). 범위 밖 번호는 버린다. 두 번 물어도 형식이 어긋난 묶음은 하나도 남기지 않는다."""
    keep, warns, size = [], [], 150
    chunks = [cands[i:i + size] for i in range(0, len(cands), size)]
    for ci, chunk in enumerate(chunks, 1):
        listing = "\n".join("%d. %s (%d) 예: %s" % (i, n, c, ex) for i, (n, c, ex) in enumerate(chunk, 1))
        feedback, state = "", "형식"
        for attempt in (1, 2):
            ok, content = _ask_model(ctx, CHECK_SYSTEM, CHECK_USER.format(text=text, cands=listing, feedback=feedback),
                                     "ask_%s_check_%d_%d" % (tag, ci, attempt))
            if not ok:
                state = "호출"
                warns.append("후보 확인 호출 실패(묶음 %d/%d): %s" % (ci, len(chunks), str(content)[:120]))
                break
            obj = _json_from(content, "keep")
            if obj is not None and isinstance(obj["keep"], list):
                nums = sorted({int(x) for x in obj["keep"] if isinstance(x, int) or (isinstance(x, str) and x.isdigit())})
                keep += [chunk[i - 1][0] for i in nums if 1 <= i <= len(chunk)]
                state = ""
                break
            feedback = "\n[다시] 앞의 답은 쓸 수 없었다. {\"keep\": [번호, 번호]} 꼴의 JSON 하나만 쓴다.\n"
        if state == "형식":
            warns.append("후보 확인의 답이 형식에 맞지 않음(묶음 %d/%d). 이 묶음의 후보는 대상에 넣지 않음" % (ci, len(chunks)))
    return keep, warns


def _field_names(consts):
    """인덱스 상수의 이름에서 필드 이름을 만든다. 공통 앞머리(IDX_, COL_ 등)를 떼고 소문자로 쓴다. 돌려주는 값: {상수: 필드}"""
    parts = [c.split("_") for c in consts]
    k = 0
    while len(consts) > 1 and all(len(p) > k + 1 for p in parts) and len({p[k] for p in parts}) == 1:
        k += 1
    if len(consts) == 1 and len(parts[0]) > 1:
        k = 1
    out, used = OrderedDict(), set()
    for c, p in zip(consts, parts):
        f = re.sub(r"[^a-z0-9_]", "", "_".join(p[k:]).lower()) or c.lower()
        if not re.match(r"[a-z_]", f) or f in _CPP_WORDS:
            f = "f_" + f
        while f in used:
            f += "_"
        used.add(f)
        out[c] = f
    return out


def _auto_array_feature(ctx, fid, text, name, files):
    """요청문에서 온 '배열 -> 구조체' 의도를 정형 요청(array_to_struct)으로 만든다. 돌려주는 값: (요청 dict 또는 None, 못 만든 이유)
    필드 이름은 그 배열의 인덱스로 쓰인 상수(enum, #define)의 이름에서 만든다. 상수가 하나도 없고 숫자만 쓰였으면 f0, f1 ... 로 한다.
    필드 이름을 정확히 정하려면 --request 파일의 mapping 으로 준다."""
    two_d, decl_rx = False, _array_decl_rx(name)
    for rel in files:
        if any(m.group("d2") is not None for m in decl_rx.finditer(get_doc(ctx, rel).struct)):
            two_d = True
            break
    use_rx = re.compile((r"(?<![\w.>])%s\s*\[[^\]\n]*\]\s*\[\s*([A-Za-z_]\w*|\d+)\s*\]" if two_d
                         else r"(?<![\w.>])%s\s*\[\s*([A-Za-z_]\w*|\d+)\s*\]") % re.escape(name))
    idents, nums = [], set()
    for rel in files:
        st_ = get_doc(ctx, rel).struct
        if name not in st_:
            continue
        decl_spans = [m.span() for m in decl_rx.finditer(st_)]
        for m in use_rx.finditer(st_):
            if any(a <= m.start() < b for a, b in decl_spans):
                continue                                   # 선언의 크기(IDX_MAX 등)는 인덱스가 아니다
            g = m.group(1)
            if g.isdigit():
                nums.add(int(g))
            elif g not in idents:
                idents.append(g)
    vals = _resolve_names(ctx, files, sorted(idents))
    consts = sorted([c for c in idents if c in vals], key=lambda c: (vals[c], c))
    if consts:
        mapping = _field_names(consts)
    elif nums:
        mapping = OrderedDict((str(i), "f%d" % i) for i in sorted(nums))
    else:
        return None, "배열 %s 의 인덱스로 쓰인 상수나 숫자를 찾지 못해 필드 이름을 정할 수 없음(--request 파일의 mapping 으로 준다)" % name
    return {"id": fid, "type": "array_to_struct", "target": name, "mapping": dict(mapping), "_auto": True, "_text": text}, ""


def _ask_unresolved(ctx, rid, title, text, why):
    """분석하지 못했거나 대상을 찾지 못한 요청을 수동 검토 목록에 올린다."""
    ctx.store.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual',?)", (rid, title, text[:300], why[:300]))


def _analyze_asks(ctx, files):
    """요청문(--ask, --ask-file, 요청 파일의 asks)을 분석해 ctx.request 의 asks(대상을 찾을 의도), features(배열 요청),
    common(조건)에 넣는다. S5 의 처음에 부른다. 돌려주는 값: (변경 계약 요약 목록, 계획 기록 목록)"""
    st, req, lim = ctx.store, ctx.request, ctx.cfg["limits"]
    req["features"] = [f for f in req.get("features") or [] if not f.get("_auto")]
    req["common"] = list(req["common_base"] if req.get("common_base") is not None else (req.get("common") or []))
    req["asks"] = []
    raws, plan, contracts, auto_feats, auto_common = req.get("asks_raw") or [], [], [], [], []
    if not raws:
        st.set_meta("ask_state", {"asks": [], "features": [], "common": []})
        return contracts, plan
    can_llm = (not ctx.opts.get("no_llm")) and bool(ctx.cfg["llm"].get("endpoint"))
    max_c, over = int(lim.get("ask_max_candidates", 600) or 600), int(lim.get("ask_check_over", 8) or 0)
    known = _code_names(ctx)

    def note(iid, kind, text):
        contracts.append({"id": iid, "type": kind, "target": "-", "rename": {}, "counts": {}, "note": text})

    for ask in raws:
        rec = {"id": ask["id"], "text": ask["text"], "intents": []}
        plan.append(rec)
        if can_llm:
            intents, why = analyze_ask(ctx, ask)
        else:
            intents, why = None, "sLLM 을 쓸 수 없어 요청을 분석하지 못함(--no-llm 이거나 주소 미설정)"
        if not intents:
            rec["failed"] = why
            LOG5.warn("요청 %s 을(를) 분석하지 못했습니다: %s" % (ask["id"], why))
            _ask_unresolved(ctx, ask["id"], "요청을 분석하지 못함", ask["text"], why)
            note(ask["id"], "요청문", "요청문: %s / 분석하지 못함: %s" % (ask["text"][:200], why))
            continue
        LOG5.info("요청 %s: 의도 %d개로 나눔(%s)" % (ask["id"], len(intents), ", ".join(i["type"] for i in intents)))
        for k, it in enumerate(intents, 1):
            iid, typ = "%s-%d" % (ask["id"], k), it["type"]
            ok_names = [n for n in it["names"] if n in known]
            r = dict(it, id=iid, names=ok_names, names_dropped=[n for n in it["names"] if n not in known], targets=list(ok_names),
                     candidates=0, result="")
            rec["intents"].append(r)
            if r["names_dropped"]:
                LOG5.warn("요청 %s: 모델이 낸 이름 %s 은(는) 코드에 없어 버립니다" % (iid, ", ".join(r["names_dropped"])))
            if typ == "constraint":
                if it["instruction"] not in req["common"]:
                    req["common"].append(it["instruction"])
                    auto_common.append(it["instruction"])
                r["result"] = "모든 수정 요청에 조건으로 붙임"
                LOG5.info("요청 %s [조건]: %s" % (iid, it["instruction"]))
                continue
            if typ == "compat":
                r["result"] = ("환경 호환성 변경은 요청문으로 처리하지 않는다. --from-vs, --to-vs 의 차이에 따라 규칙이 자동으로 수행하고, "
                               "sLLM 수정 시도는 --compat-llm 으로 켠다(지금 %s)" % ("켜짐" if ctx.opts.get("compat_llm") else "꺼짐"))
                LOG5.info("요청 %s [환경 호환성]: %s" % (iid, r["result"]))
                note(iid, "요청문(환경 호환성)", "요청문: %s / %s" % (it["instruction"], r["result"]))
                continue
            if typ == "array_to_struct":
                if not ok_names:
                    r["result"] = "배열 이름을 받지 못함. 요청문에 배열 이름을 적거나 --request 파일의 array_to_struct 로 준다"
                    LOG5.warn("요청 %s [배열 -> 구조체]: %s" % (iid, r["result"]))
                    if not any(f.get("type") == "array_to_struct" for f in req["features"]):
                        _ask_unresolved(ctx, iid, "요청의 대상을 찾지 못함", it["instruction"], r["result"])
                    note(iid, "요청문(배열 -> 구조체)", "요청문: %s / %s" % (it["instruction"], r["result"]))
                    continue
                made = []
                for j, n in enumerate(ok_names):
                    ft, why2 = _auto_array_feature(ctx, iid if j == 0 else "%s_%d" % (iid, j + 1), it["instruction"], n, files)
                    if ft is None:
                        LOG5.warn("요청 %s [배열 -> 구조체]: %s" % (iid, why2))
                        _ask_unresolved(ctx, iid, "요청의 대상을 찾지 못함", it["instruction"], why2)
                        r["result"] += why2 + " "
                        continue
                    req["features"].append(ft)
                    auto_feats.append(ft)
                    made.append("%s(필드 %s)" % (n, ", ".join(list(ft["mapping"].values())[:12])))
                if made:
                    r["result"] += "배열 요청으로 처리: " + "; ".join(made)
                    LOG5.info("요청 %s [배열 -> 구조체]: %s" % (iid, r["result"]))
                continue
            warns = []
            if it["words"] and not ok_names:               # 정확한 이름이 없다: 낱말이 든 호출 이름을 후보로 고른다
                cands = expand_words(ctx, it["words"])
                r["candidates"] = len(cands)
                if len(cands) > max_c:
                    warns.append("후보가 %d개여서 많이 쓰인 %d개만 봄" % (len(cands), max_c))
                    cands = cands[:max_c]
                if over and len(cands) > over:
                    r["targets"], w2 = check_candidates(ctx, iid, it["instruction"], cands)
                    warns += w2
                else:
                    r["targets"] = [c[0] for c in cands]
            if not r["targets"] and it["construct"] not in _ASK_CTRL:
                if r["names_dropped"]:
                    why = "모델이 낸 이름(%s)이 코드에 없음" % ", ".join(r["names_dropped"])
                elif it["words"]:
                    why = ("낱말(%s)이 든 호출 이름이 코드에 없음" % ", ".join(it["words"])) if not r["candidates"] \
                        else "후보 %d개 가운데 의도에 맞는 이름을 고르지 못함" % r["candidates"]
                else:
                    why = "찾을 이름도 낱말도 받지 못함(요청문에 함수나 변수의 이름을 적으면 찾을 수 있다)"
                r["result"] = "대상을 찾지 못함: " + why
                LOG5.warn("요청 %s: %s" % (iid, r["result"]))
                _ask_unresolved(ctx, iid, "요청의 대상을 찾지 못함", it["instruction"], why)
                note(iid, "요청문", "요청문: %s / %s" % (it["instruction"], r["result"]))
                continue
            if warns:
                r["warnings"] = warns
            req["asks"].append({"id": iid, "ask": ask["id"], "type": typ, "targets": r["targets"], "text": it["instruction"],
                                "orig": ask["text"], "files": it["files"], "where": it["construct"],
                                "action": "find" if typ == "find" else "modify"})
    st.set_meta("ask_state", {"asks": req["asks"], "features": auto_feats, "common": auto_common})
    return contracts, plan


def _where_filter(d, where):
    """위치 조건에 맞는 구간을 합쳐서 돌려준다. 돌려주는 값: (시작 목록, 끝 목록) 또는 None(조건 없음)"""
    if where in ("", "any", "call", None):
        return None
    if where == "string":
        spans = [tuple(x) for x in mask_code(d.text)[1]]
    elif where == "comment":
        spans = comment_spans(d.text)
    else:
        spans = [(r["start"], r["end"]) for r in ctrl_ranges(d.struct, kinds=(where,))[0]]
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [m[0] for m in merged], [m[1] for m in merged]


def _in_spans(flt, pos):
    if flt is None:
        return True
    i = bisect.bisect_right(flt[0], pos) - 1
    return i >= 0 and pos < flt[1][i]


def _user_asks(ctx, files):
    """분석이 끝난 의도(ctx.request['asks'])마다 대상 줄을 찾아 sLLM 대상 이슈로 넣는다. find 의도는 목록 파일만 낸다.
    대상 줄: 이름이 쓰인 줄(구문 조건이 있으면 그 안의 것만). 이름 없이 구문만 받은 의도는 그 구문의 머리 줄.
    고치는 방법은 의도의 요청문을 지침으로 준다(_request_guidance). 그래서 결과는 모두 검토 대상이다.
    돌려주는 값: 변경 계약 요약 목록(보고서의 "변경 계약"에 실린다)."""
    st, out = ctx.store, []
    cap = max(0, int(ctx.cfg["limits"].get("ask_max_sites", 0) or 0))   # 0 이면 상한 없음
    sql = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    for a in ctx.request.get("asks") or []:
        where, find_only, found = a.get("where") or "any", a.get("action") == "find", []
        tail = r"(?=\s*\()" if where == "call" else ""
        rxs = [re.compile(r"(?<!\w)%s\b%s" % (re.escape(t), tail)) for t in a["targets"]]
        pats = [p for p in (a.get("files") or []) if p]

        def in_scope(rel):
            r = rel.lower()
            return any(fnmatch.fnmatch(r, p) or r.startswith(p.rstrip("*").rstrip("/") + "/") for p in pats)

        if pats and not any(in_scope(rel) for rel in files):
            LOG5.warn("요청 %s: 파일 범위 %s 에 맞는 파일이 없어 범위를 두지 않습니다" % (a["id"], ", ".join(pats)))
            pats = []
        n_llm = n_over = n_files = 0
        for rel in files:
            if pats and not in_scope(rel):
                continue
            d = get_doc(ctx, rel)
            hits = []
            if rxs:
                flt = _where_filter(d, where)             # 구문 조건(if 문 안 등)에 맞는 구간. 조건이 없으면 None
                body = d.text if where in ("string", "comment") else d.code   # 문자열·주석 안은 가리지 않은 본문에서 찾는다
                for rx in rxs:
                    hits += [(m.start(), m.end(), m.group()) for m in rx.finditer(body) if m.end() > m.start() and _in_spans(flt, m.start())]
            elif where in _ASK_CTRL:                      # 이름 없이 구문만 받았다: 그 구문의 머리가 대상이다
                hits = [(r["start"], max(r["start"] + 1, r.get("cond_end") or r["start"] + 1), where)
                        for r in ctrl_ranges(d.struct, kinds=(where,))[0]]
            lines_done = set()
            for pos, end, name in sorted(hits):
                line = d.line_of(pos)
                if line in lines_done:
                    continue
                lines_done.add(line)
                la, lb = d.line_span(line)
                if find_only:                             # 찾기 전용: 이슈로 올리지 않고 목록에만 넣는다
                    found.append((rel, line, name[:40], " ".join(d.text[la:lb].split())[:140]))
                    n_llm += 1
                    continue
                over = bool(cap) and n_llm >= cap
                st.x(sql, (rel, pos, end, line, "request", a["id"], "T3", 0 if over else 1,
                           "사용자 요청: %s" % a["text"][:140], d.text[la:lb].strip()[:160], None, 0, "manual" if over else "open",
                           ("한 요청이 다루는 줄의 상한(%d)을 넘음. --only 로 범위를 좁힌다" % cap) if over
                           else "사용자 요청문을 sLLM 에 맡김(검토 필요)"))
                n_over += 1 if over else 0
                n_llm += 0 if over else 1
            n_files += 1 if lines_done else 0
        a["_sites"], a["_files"], a["_over"] = n_llm, n_files, n_over
        tg_txt = (", ".join(a["targets"][:12]) + (" 외 %d개" % (len(a["targets"]) - 12) if len(a["targets"]) > 12 else "")) or "(구문만)"
        cond = (" [%s]" % _WHERE_KO.get(where, where)) if where != "any" else ""
        if find_only:
            L = ["# 요청 %s: 찾은 곳" % a["id"], "", "- 요청문: %s" % a["text"], "- 대상: %s" % (", ".join(a["targets"]) or "(구문만)"),
                 "- 구문 조건: %s" % (_WHERE_KO.get(where) or "없음"), "- 찾은 곳: %d곳(파일 %d개)" % (len(found), n_files),
                 "- 이 의도는 찾기 전용이다. 코드를 고치지 않았고 수정 요청을 보내지 않았다.", ""]
            L += _table(["파일", "줄", "이름", "코드"], found[:5000]) if found else ["찾은 곳이 없다."]
            if len(found) > 5000:
                L += ["", "(5,000곳까지만 실었다. 전체 %d곳)" % len(found)]
            write_text(ctx.p("out", "found_%s.md" % a["id"]), "\n".join(L) + "\n")
            LOG5.info("요청 %s (%s)%s: 찾기 전용. %d곳(파일 %d개)을 out/found_%s.md 에 적음" % (a["id"], tg_txt, cond, len(found), n_files, a["id"]))
            out.append({"id": a["id"], "type": "요청문(찾기)", "target": tg_txt + cond, "rename": {},
                        "counts": {"찾은 곳": len(found)}, "note": "요청문: %s / 목록: found_%s.md" % (a["text"], a["id"])})
            continue
        LOG5.info("요청 %s (%s)%s: 대상 줄 %d곳(파일 %d개)을 sLLM 에 맡김%s"
                  % (a["id"], tg_txt, cond, n_llm, n_files, (", 상한을 넘어 수동 검토로 보낸 곳 %d곳" % n_over) if n_over else ""))
        if not n_llm:
            why = "대상 %s 이(가) 처리 범위의 코드에서 쓰인 곳이 없음%s" % (tg_txt, "(구문 조건 %s)" % _WHERE_KO.get(where) if where != "any" else "")
            LOG5.warn("요청 %s: %s" % (a["id"], why))
            _ask_unresolved(ctx, a["id"], "요청의 대상을 찾지 못함", a["text"], why)
        out.append({"id": a["id"], "type": "요청문", "target": tg_txt + cond, "rename": {},
                    "counts": {"sLLM 대상": n_llm, "상한 초과(수동)": n_over}, "note": "요청문: " + a["text"]})
    return out


def _write_ask_plan(ctx, plan, contracts):
    """요청문 분석의 결과를 out/ask_plan.json 에 적는다. 무엇으로 이해했고 어디를 찾았는지 사람이 확인하는 파일이다."""
    if not plan:
        return
    by_id = {a["id"]: a for a in ctx.request.get("asks") or []}
    by_con = {c["id"]: c for c in contracts}
    for rec in plan:
        for r in rec["intents"]:
            a, c = by_id.get(r["id"]), by_con.get(r["id"])
            if a is not None:
                r["sites"], r["files_hit"] = a.get("_sites", 0), a.get("_files", 0)
                r["result"] = r["result"] or ("%s %d곳(파일 %d개)" % ("찾은 곳" if a["action"] == "find" else "sLLM 대상", r["sites"], r["files_hit"]))
            elif c is not None and c.get("type") == "array_to_struct":
                r["result"] += " / " + ("적용함" if c.get("applied") else "적용하지 않음: " + " / ".join(c.get("reasons") or []))
    write_text(ctx.p("out", "ask_plan.json"), json.dumps(
        {"_설명": "요청문(--ask)을 sLLM 이 의도별로 나눈 결과와, 도구가 그 의도로 코드에서 찾은 곳의 수다. "
                  "분석 요청과 응답의 원문은 logs/llm/ask_*.txt 에 있다.", "asks": plan}, ensure_ascii=False, indent=2))
    LOG5.info("요청문 분석 결과: %s" % ctx.p("out", "ask_plan.json"))


# ---- 환경 호환성 항목의 sLLM 수정 시도(--compat-llm, 0.9.0) ---------------------------------------------
# 기본(꺼짐): 환경 호환성 항목은 규칙의 기계적 변경과 설정 흡수만 한다. 규칙이 "sLLM 대상"으로 분류한 것도 보내지 않고 수동 검토로 둔다.
# 켜짐: 규칙의 sLLM 대상을 보내고, 설정으로 흡수하던 항목 가운데 llm_guidance 가 있는 규칙(보안 CRT, Winsock)의 사용 줄도 보낸다.
#       이 줄들은 빌드에는 지장이 없는 것이므로, sLLM 이 고치지 않으면(NO_CHANGE) 수동 검토로 올리지 않고 그대로 둔다.
def _compat_rules(ctx):
    """sLLM 수정 시도의 대상이 되는 흡수 규칙. {이슈에 쓰는 ID: 규칙}. 이 환경값에서 켜진 규칙만 본다."""
    return OrderedDict(("L-" + r["id"].split("-", 2)[-1], r) for r in ctx.rules if r.get("llm_guidance"))


def _compat_sites(ctx, files):
    """--compat-llm 이 켜져 있으면 흡수 규칙의 사용 줄을 sLLM 대상 이슈로 넣는다. 돌려주는 값: 넣은 줄 수"""
    st, n = ctx.store, 0
    st.x("DELETE FROM issues WHERE source='compat'")
    if not ctx.opts.get("compat_llm"):
        return 0
    sql = ("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,after,review,status,note) "
           "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)")
    taken = {(r["path"], r["line"]) for r in st.q("SELECT path, line FROM issues WHERE source='rule' AND tier='T3' AND llm=1")}
    for cid, rule in _compat_rules(ctx).items():
        rx, k = re.compile(rule["pattern"]), 0
        for rel in files:
            d = get_doc(ctx, rel)
            done = {ln for pth, ln in taken if pth == rel}    # 규칙이 이미 sLLM 대상으로 올린 줄(gets 등)은 다시 올리지 않는다
            for m in rx.finditer(d.code):
                line = d.line_of(m.start())
                if line in done or exc_skip(ctx.exceptions, rel, rule["id"], line):
                    continue
                done.add(line)
                la, lb = d.line_span(line)
                st.x(sql, (rel, m.start(), m.end(), line, "compat", cid, "T3", 1, rule["title"] + " - sLLM 수정 시도",
                           d.text[la:lb].strip()[:160], None, 0, "open", "환경 호환성 항목을 sLLM 에 맡김(--compat-llm. 검토 필요)"))
                k += 1
        LOG5.info("환경 호환성(%s): 사용 줄 %d곳을 sLLM 에 맡김(--compat-llm)" % (rule["id"], k))
        n += k
    return n


def _soft_issue(ctx, issue):
    """고치지 않아도 되는 대상인가. 요청문의 의도로 찾은 줄과 --compat-llm 의 흡수 항목이 그렇다(모델이 판단해 그대로 둘 수 있다)."""
    if issue["source"] == "compat":
        return True
    return issue["source"] == "request" and any(a["id"] == issue["rule_id"] for a in ctx.request.get("asks") or [])


def _mark_unchanged(ctx, issue_ids, note, failed=False):
    """sLLM 이 고치지 않았거나(failed=False) 고치려다 실패한(failed=True) 지점의 처리.
      --compat-llm 의 흡수 항목: 어느 쪽이든 '건너뜀'으로 둔다(설정으로 경고가 꺼지므로 빌드에 지장이 없다. 수동 검토를 늘리지 않는다)
      요청문의 의도로 찾은 줄: 모델이 고칠 필요가 없다고 본 것은 '건너뜀', 고치려다 실패한 것은 수동 검토
      그 밖(규칙의 sLLM 대상, 배열 요청의 사용처): 수동 검토"""
    st = ctx.store
    for iid in issue_ids:
        row = st.q("SELECT source, rule_id FROM issues WHERE id=?", (iid,))
        if row and row[0]["source"] == "compat":
            st.x("UPDATE issues SET status='skipped', note=? WHERE id=?",
                 (("sLLM 수정 시도 실패(그대로 둠): " + note)[:300] if failed else "sLLM 이 고치지 않음(수정 불필요로 판단)", iid))
        elif row and not failed and _soft_issue(ctx, row[0]):
            st.x("UPDATE issues SET status='skipped', note=? WHERE id=?", ("sLLM 이 고치지 않음(수정 불필요로 판단)", iid))
        else:
            st.x("UPDATE issues SET status='manual', note=? WHERE id=?", (note, iid))


def s5_impact(ctx):
    st = ctx.store
    st.x("DELETE FROM issues WHERE source='request'")
    st.x("DELETE FROM edits WHERE origin='request'")
    files = [r["path"] for r in st.q("SELECT path FROM files WHERE kind='source' AND status='대상' ORDER BY path")]
    ask_notes, plan = _analyze_asks(ctx, files)            # 요청문을 의도별로 나눈다. 배열 의도는 features 에, 조건은 common 에 들어간다
    contracts = []
    for ft in ctx.request.get("features", []):
        if ft["type"] != "array_to_struct":
            LOG5.warn("요청 %s: 유형 %s 은(는) 이 판에서 처리하지 않습니다. 수동 검토 목록에 올립니다"
                      % (ft["id"], ft["type"]))
            st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                 "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual','이 판에서 지원하지 않는 요청 유형')",
                 (ft["id"], "요청사항 %s" % ft["type"], json.dumps(ft, ensure_ascii=False)[:300]))
            continue
        contracts.append(_array_request(ctx, ft))
    contracts += ask_notes
    contracts += _user_asks(ctx, files)
    _write_ask_plan(ctx, plan, contracts)
    n_compat = _compat_sites(ctx, files)
    st.set_meta("compat_sites", n_compat)
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


# ---------------------------------------------------------------------------
# 계약 요청(배열 -> 구조체)의 마무리. 선언과 사용처가 같이 바뀌어야 하므로 요청 단위로 본다(15_구분별... 7.2 의 E5, E7)
# ---------------------------------------------------------------------------
def _request_rollback(ctx, c, why):
    """요청 하나에서 나온 수정(규칙 적용분과 sLLM 수정분)을 전부 기각한다. 돌려주는 값: 기각한 수정 수"""
    st = ctx.store
    ids = {r["id"] for r in st.q("SELECT id FROM issues WHERE source='request' AND rule_id=?", (c["id"],))}
    rej = [(e["id"], why) for e in st.q("SELECT id FROM edits WHERE origin='request' AND ref=? AND status IN (?,?,?)", (c["id"],) + _LIVE)]
    for e in st.q("SELECT id, issue_ids FROM edits WHERE origin='llm' AND status IN (?,?,?)", _LIVE):
        if ids & {int(x) for x in str(e["issue_ids"] or "").split(",") if x.strip().isdigit()}:
            rej.append((e["id"], why))
    reject_edits(ctx, rej)
    st.x("UPDATE issues SET status='manual', note=? WHERE source='request' AND rule_id=? AND status IN ('open','pending','fixed')",
         ("요청을 되돌림: " + why, c["id"]))
    c["applied"] = False
    c["definition_after"] = ""
    c.setdefault("reasons", []).append(why)
    return len(rej)


def _requests_finalize(ctx):
    """병합(S7) 앞에서 부른다. sLLM 에 맡긴 사용처 가운데 못 고친 것이 있으면 요청의 on_unresolved 대로 처리한다.
    stop(기본): 그 요청의 수정을 전부 되돌린다(일부만 바뀐 결과는 컴파일되지 않는다).  keep: 남기고 '미완료'로 표시한다."""
    st = ctx.store
    cons = st.get_meta("contracts", []) or []
    touched = False
    for c in cons:
        if c.get("type") != "array_to_struct" or not c.get("applied"):
            continue
        if c.pop("held", None) is not None:
            touched = True
        left = st.one("SELECT COUNT(*) FROM issues WHERE source='request' AND rule_id=? AND tier='T3' AND llm=1 "
                      "AND status IN ('manual','pending')", (c["id"],)) or 0
        if not left:
            continue
        touched = True
        n_fail = st.one("SELECT COUNT(*) FROM issues WHERE source='request' AND rule_id=? AND tier='T3' AND llm=1 "
                        "AND status='manual'", (c["id"],)) or 0
        if not n_fail:
            # 실패한 곳은 없고 아직 보내지 못한 곳만 있다. 되돌리지 않는다. 일부만 바뀐 결과가 나가지 않게
            # 이 요청의 수정을 이번 결과에서만 빼 두고(held), 이어서 실행할 때 다시 넣는다
            ids = {r["id"] for r in st.q("SELECT id FROM issues WHERE source='request' AND rule_id=?", (c["id"],))}
            st.x("UPDATE edits SET status='held' WHERE origin='request' AND ref=? AND status IN (?,?,?)", (c["id"],) + _LIVE)
            for e in st.q("SELECT id, issue_ids FROM edits WHERE origin='llm' AND status IN (?,?,?)", _LIVE):
                if ids & {int(x) for x in str(e["issue_ids"] or "").split(",") if x.strip().isdigit()}:
                    st.x("UPDATE edits SET status='held' WHERE id=?", (e["id"],))
            st.x("UPDATE issues SET status='pending', note='같은 요청의 다른 사용처가 sLLM 대기 중' "
                 "WHERE source='request' AND rule_id=? AND status IN ('open','fixed')", (c["id"],))
            c["held"] = left
            LOG7.warn("요청 %s: sLLM 에 보내지 못한 사용처 %d곳이 대기 중입니다. 일부만 바뀐 결과를 내지 않도록 "
                      "이 요청의 수정은 이번 결과에 넣지 않습니다(이어서 실행하면 다시 넣습니다)" % (c["id"], left))
            continue
        if c.get("on_unresolved", "stop") == "stop":
            n = _request_rollback(ctx, c, "sLLM 에 맡긴 사용처 %d곳을 고치지 못함(on_unresolved: stop)" % left)
            LOG7.warn("요청 %s: sLLM 에 맡긴 사용처 %d곳을 고치지 못해 이 요청의 수정 %d건을 모두 되돌립니다" % (c["id"], left, n))
        else:
            c["incomplete"] = left
            LOG7.warn("요청 %s: 못 고친 사용처 %d곳이 남았습니다. 이 상태로는 컴파일되지 않습니다(on_unresolved: keep)" % (c["id"], left))
    if touched:
        st.set_meta("contracts", cons)
        st.commit()


def _requests_check(ctx):
    """검증(S8) 끝에서 부른다. 적용한 계약 요청마다 두 가지를 본다(요구사항 E4).
      (1) 병합이나 검증에서 빠진 수정이 있는가  (2) 옛 접근 꼴(이름[..])이 예상보다 많이 남았는가
    어긋나면 자동으로 되돌리지 않는다. 보고서 첫머리와 수동 검토 목록에 경고를 남긴다."""
    st = ctx.store
    cons = st.get_meta("contracts", []) or []
    touched = False
    for c in cons:
        if c.get("type") != "array_to_struct" or not c.get("applied") or c.get("held"):
            continue                              # 대기 중이라 결과에서 빼 둔 요청은 확인할 것이 없다
        problems = []
        dropped = st.one("SELECT COUNT(*) FROM edits WHERE origin='request' AND ref=? AND status='rejected'", (c["id"],)) or 0
        if dropped:
            problems.append("이 요청의 수정 %d건이 병합이나 검증에서 빠짐" % dropped)
        over = []
        for rel, e in sorted((c.get("subs") or {}).items()):
            row = st.q("SELECT status FROM files WHERE path=?", (rel,))
            if not row or row[0]["status"] in ("범위제외", "제외"):
                continue
            _d, text, _bad = build_merged(ctx, rel)
            actual = _count_sub(mask_code(text)[0], c["target"], c.get("shape", "1d"))
            not_fixed = 0
            for iid in e.get("issues") or []:
                if (st.one("SELECT status FROM issues WHERE id=?", (iid,)) or "") != "fixed":
                    not_fixed += 1
            expected = e.get("other", 0) + not_fixed
            if actual > expected:
                over.append("%s(남은 것 %d, 예상 %d)" % (rel, actual, expected))
        if over:
            problems.append("옛 접근 꼴이 예상보다 많이 남음: " + ", ".join(over[:8]))
        st.x("INSERT INTO verifications(path,level,result,detail) VALUES(?,?,?,?)",
             ("(요청 %s)" % c["id"], "E4", "불합격" if problems else "통과", "; ".join(problems) or "남은 옛 접근 꼴이 예상과 같음"))
        if problems:
            touched = True
            c["broken"] = "; ".join(problems)
            LOG8.error("요청 %s 의 결과가 어긋납니다: %s. 결과물이 컴파일되지 않을 수 있습니다" % (c["id"], c["broken"]))
            st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                 "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual',?)",
                 (c["id"], "요청사항: 배열 %s 의 변환 결과가 어긋남" % c["target"], c["broken"][:300], "사용처 목록과 diff 를 보고 사람이 맞춘다"))
        else:
            LOG8.info("요청 %s: 잔여 재탐지 통과(옛 접근 꼴이 예상과 같음)" % c["id"])
    if touched:
        st.set_meta("contracts", cons)
    st.commit()


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


def _request_guidance(ctx, rid, near_text=""):
    """규칙 카탈로그에 없는 ID 의 이슈에 붙일 수정 지침. 요청문의 의도, 배열 요청, --compat-llm 의 흡수 항목이 여기서 나온다."""
    cr = _compat_rules(ctx).get(rid)
    if cr is not None:
        return cr["llm_guidance"]
    for a in ctx.request.get("asks") or []:
        if a["id"] == rid:                       # 요청문의 의도: 그 의도만의 요청문을 지침으로 주고, 원문을 함께 붙인다
            names = set(re.findall(r"[A-Za-z_]\w*", near_text or ""))
            tg = [t for t in a["targets"] if t in names] or a["targets"]      # 대상이 많으면 이 조각에 나온 것만 적는다
            where = a.get("where") or "any"
            g = ""                                # 이 줄의 제목(사용자 요청: ...)에 요청문이 이미 있다. 제목에서 잘릴 만큼 길 때만 전체를 다시 적는다
            if len(a["text"]) > 140:
                g = "요청문 전체: %s " % (a["text"] if a["text"].endswith((".", "다", "요")) else a["text"] + ".")
            if tg:
                g += "대상은 %s 이다. " % ", ".join(tg[:20])
            if where in _ASK_CTRL or where in ("string", "comment"):
                g += "%s에 있는 것만 본다. " % _WHERE_KO[where]
            g += ("이 조각의 [수정 대상 줄]을 이 요청대로 고친다. 요청과 관계없는 줄이거나 고칠 필요가 없으면 그대로 둔다. "
                  "고칠 줄이 하나도 없으면 NO_CHANGE 를 쓴다.")
            orig = " ".join(str(a.get("orig") or "").split())
            if orig and orig != " ".join(a["text"].split()):
                g += " (요청 원문: %s)" % orig[:300]
            return g
    for ft in ctx.request.get("features", []):
        if ft.get("id") == rid and ft.get("type") == "array_to_struct":
            names = set(re.findall(r"[A-Za-z_]\w*", near_text or ""))
            near = [(k, v) for k, v in ft["mapping"].items() if k in names or v in names] or list(ft["mapping"].items())[:12]
            tgt, elem = ft["target"], ft.get("_elem") or "요소타입"
            ptr = ("const " if ft.get("_const") and "const" not in elem else "") + elem + "*"
            if ft.get("_shape") == "2d":
                pairs = ", ".join("%s[행][%s] -> %s[행].%s" % (tgt, k, tgt, v) for k, v in near[:24])
                how = ("열이 변수인 곳은 ((%s)&%s[행])[열] 꼴로 쓴다. 행을 통째로 넘기는 곳은 (%s)&%s[행] 으로 쓴다." % (ptr, tgt, ptr, tgt))
            else:
                pairs = ", ".join("%s[%s] -> %s.%s" % (tgt, k, tgt, v) for k, v in near[:24])
                how = ("인덱스가 변수여서 필드를 정할 수 없는 곳은 ((%s)&%s)[인덱스] 꼴로 쓴다. 이름을 통째로 넘기는 곳은 (%s)&%s 로 쓴다."
                       % (ptr, tgt, ptr, tgt))
            return ("배열 %s 은(는) 구조체로 바뀐다. 대응: %s. 상수 인덱스 접근은 이미 필드로 바꿨다. 이 줄처럼 남은 사용처를 고친다. "
                    "가리키는 필드를 정할 수 있으면 필드 접근으로 고친다. %s 고칠 방법이 없으면 NO_CHANGE 를 쓴다."
                    % (tgt, pairs, how)) + _contract_excerpt(ft, near_text) + ((" 사용자 요청: %s" % ft["_text"]) if ft.get("_text") else "")
    return ""


def _common_guidance(ctx):
    """요청 파일의 공통 요청(common)을 [수정 지침] 뒤에 붙일 줄 목록으로 만든다."""
    items = [str(x).strip() for x in (ctx.request.get("common") or []) if str(x).strip()]
    if not items:
        return []
    return ["", "[공통 요청] 이 작업의 모든 수정에 적용한다."] + ["- " + x for x in items]


# ---------------------------------------------------------------------------
# 참고 영역: 조각 밖에 있는 선언과 감싼 구문의 머리 (0.8.0. 15_구분별_검토현황과_구현명세 6.2 의 D3, D4, D5)
# 조각은 대상 줄 주변만 담으므로, 대상 줄에 쓰인 변수가 어떻게 선언됐는지는 조각에 없을 수 있다.
# 그러면 모델이 sizeof(포인터) 같은 "문법은 맞고 값이 틀린" 수정을 낸다. 선언을 찾아 [참고]로 붙인다.
# [참고]는 [코드 조각] 밖에 있으므로 SEARCH 에 일치하지 않는다. 모델이 고칠 수 없는 읽기 전용 정보다.
# ---------------------------------------------------------------------------
_REF_SKIP = {"if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue", "return", "goto", "sizeof",
             "new", "delete", "this", "true", "false", "NULL", "nullptr", "const", "static", "extern", "struct", "class", "union",
             "enum", "typedef", "void", "int", "char", "short", "long", "unsigned", "signed", "float", "double", "bool", "wchar_t",
             "auto", "register", "volatile", "inline", "public", "private", "protected", "virtual", "template", "typename",
             "namespace", "using", "try", "catch", "throw", "operator", "TRUE", "FALSE", "BOOL", "DWORD", "WORD", "BYTE",
             "UINT", "LONG", "ULONG", "LPCSTR", "LPSTR", "LPCTSTR", "LPTSTR", "TCHAR", "WCHAR", "CHAR", "INT", "VOID", "HANDLE",
             "size_t", "std", "stdin", "stdout", "stderr"}
_MEMBER_DECL = (r"(?m)^[ \t]*(?:(?:static|mutable|const|unsigned|signed|struct|volatile|constexpr|inline)\s+)*"
                r"[A-Za-z_][\w:<>, \t\*&]*?[\s\*&]%s[ \t]*((?:\[[^\]\n]*\])*)[ \t]*(?:=[^;]*|\{[^;]*\})?;")


def _snippet_cached(ctx, name):
    """find_symbol_snippet 의 결과를 이름별로 기억해 둔다(같은 이름을 작업마다 다시 찾지 않게)."""
    c = ctx._snips
    if name not in c:
        c[name] = find_symbol_snippet(ctx, name)
    return c[name]


def _global_index(ctx):
    """파일 범위(어느 블록에도 들지 않은 자리)의 변수 선언 색인. {이름: [(파일, 선언 글, 배열인가, 포인터인가)]}
    처음 필요할 때 한 번 만든다. 줄의 처음에서 시작하는 선언만 본다(들여쓴 선언, 한 문장에 여러 변수를 둔 선언은 빠질 수 있다)."""
    if ctx._gdecl is not None:
        return ctx._gdecl
    idx = {}
    rx = re.compile(r"(?m)^(?:(?:extern|static|const|volatile|unsigned|signed|struct)\s+)*[A-Za-z_][\w:]*(?:[ \t]*<[^;{}()\n]*>)?"
                    r"[ \t\*&]+([A-Za-z_]\w*)[ \t]*((?:\[[^\]\n]*\])*)[ \t]*(?==|;)")
    for r in ctx.store.q("SELECT path FROM files WHERE kind='source' ORDER BY path"):
        d = get_doc(ctx, r["path"])
        if not d.ok:
            continue
        blocks = None
        for m in rx.finditer(d.struct):
            if m.group(0).split()[0] in _NOT_DECL_WORDS:
                continue
            if blocks is None:
                blocks = d.get_blocks()
            k = block_at(blocks, m.start(1))
            while k >= 0 and blocks[k]["kind"] == "container" and re.search(r"\bnamespace\b|\bextern\b", d.struct[blocks[k]["head"]:blocks[k]["open"]]):
                k = blocks[k]["parent"]
            if k >= 0:
                continue                                     # 함수나 구조체 안: 파일 범위가 아니다
            head = m.group(0)
            idx.setdefault(m.group(1), []).append((r["path"], " ".join(head.split()), bool(m.group(2)),
                                                   "*" in head[:m.start(1) - m.start()] and not m.group(2)))
    ctx._gdecl = idx
    return idx


def _class_index(ctx):
    """이름 있는 구조체·클래스 본문의 위치. {이름: [(파일, 여는 위치, 닫는 위치)]}. 처음 필요할 때 한 번 만든다."""
    if ctx._classes is not None:
        return ctx._classes
    idx = {}
    for r in ctx.store.q("SELECT path FROM files WHERE kind='source' ORDER BY path"):
        d = get_doc(ctx, r["path"])
        if not d.ok or not re.search(r"\b(?:class|struct|union)\b", d.struct):
            continue
        for b in d.get_blocks():
            if b["kind"] != "container":
                continue
            names = [b["name"]] if b["name"] else []
            if re.match(r"\s*typedef\b", d.struct[b["head"]:b["open"]]):     # typedef struct { ... } 이름; 의 이름도 같은 본문을 가리킨다
                sc = d.struct.find(";", b["close"])
                if 0 <= sc - b["close"] < 200:
                    names += re.findall(r"[A-Za-z_]\w*", d.struct[b["close"]:sc])
            for nm in names:
                idx.setdefault(nm, []).append((r["path"], b["open"], b["close"], b["head"]))
    ctx._classes = idx
    return idx


def lookup_decl(ctx, rel, struct, pos, name, big=()):
    """이름의 선언을 찾는다: 지역 -> 인자 -> (함수가 Class::method 면) 클래스 멤버 -> 파일 범위.
    돌려주는 값: {"kind": local|param|member|global|candidates, "text", "is_array", "is_pointer", "certain"} 또는 None
    struct 는 pos 가 속한 본문(전처리기까지 가린 것)이다. 규칙 수정을 반영한 본문을 줘도 된다."""
    r = find_decl(struct, pos, name, big)
    if r is None:
        return None
    if r["kind"] in ("local", "param"):
        return r
    cm = re.search(r"([A-Za-z_]\w*)\s*::\s*~?\w+\s*\(", r.get("head") or "")
    if cm:
        rx = re.compile(_MEMBER_DECL % re.escape(name))
        for crel, a, b, _h in _class_index(ctx).get(cm.group(1), []):
            m = rx.search(get_doc(ctx, crel).struct, a, b)
            if m:
                txt = " ".join(m.group(0).split())
                return {"kind": "member", "text": "%s (클래스 %s 의 멤버)" % (txt[:180], cm.group(1)), "is_array": bool(m.group(1)),
                        "is_pointer": "*" in txt.split(name)[0] and not m.group(1), "certain": True}
    cands = _global_index(ctx).get(name) or []
    same = [c for c in cands if c[0] == rel]
    pick = same[0] if same else (cands[0] if len(cands) == 1 else None)
    if pick:
        return {"kind": "global", "text": "%s (%s 의 파일 범위 선언)" % (pick[1][:180], pick[0]), "is_array": pick[2],
                "is_pointer": pick[3], "certain": True}
    if cands:
        return {"kind": "candidates", "text": "%s (%s 외 %d곳. 확정 아님)" % (cands[0][1][:160], cands[0][0], len(cands) - 1),
                "is_array": cands[0][2], "is_pointer": cands[0][3], "certain": False}
    return None


def _decl_refs(ctx, rel, struct1, text1, ls1, big, members, a, b, limit):
    """대상 줄에 쓰인 이름의 선언 가운데 조각(a~b) 밖에 있는 것을 [참고: 선언]의 줄 목록으로 만든다.
    변수는 선언 문장을, 함수는 머리글 한 줄을, 타입·매크로는 정의 앞부분을 붙인다. 합계 limit 글자까지만 넣는다."""
    out, seen, used = [], set(), 0
    kind_ko = {"local": "지역 변수", "param": "함수 인자", "member": "멤버", "global": "전역", "candidates": "후보"}

    def add(line):
        nonlocal used
        if used + len(line) > limit:
            return False
        out.append(line)
        used += len(line)
        return True

    def type_ref(decl_text, name):
        """선언의 타입 이름이 구조체나 typedef 면 그 정의의 앞부분도 붙인다(멤버의 크기를 알 수 있게)."""
        m = re.match(r"(?:(?:const|static|unsigned|signed|struct|volatile|extern)\s+)*([A-Za-z_]\w*)", decl_text)
        tname = m.group(1) if m else ""
        if tname and tname not in _REF_SKIP and tname != name and tname not in seen:
            seen.add(tname)
            hit = (_class_index(ctx).get(tname) or [None])[0]
            if hit:                                            # 구조체 본문을 그대로(300자까지)
                dd = get_doc(ctx, hit[0])
                sc = dd.struct.find(";", hit[2])
                add("- %s 의 정의: %s" % (tname, " ".join(dd.text[hit[3]:(sc + 1 if 0 <= sc - hit[2] < 200 else hit[2])].split())[:300]))
            else:
                snip = _snippet_cached(ctx, tname)
                body = " ".join(snip.split("\n", 1)[-1].split())[:300] if snip else ""
                if re.search(r"\b(?:struct|class|union|typedef|enum)\b", body):
                    add("- %s 의 정의: %s" % (tname, body))

    for line, _i in members:
        ps = ls1[line - 1]
        pe = ls1[line] if line < len(ls1) else len(text1)
        for m in re.finditer(r"[A-Za-z_]\w*", struct1[ps:pe]):
            name = m.group(0)
            if name in _REF_SKIP or name in seen or len(seen) >= 14:
                continue
            seen.add(name)
            pos = ps + m.start()
            if re.match(r"\s*\(", struct1[ps + m.end():ps + m.end() + 4]):       # 함수 호출: 머리글 한 줄
                row = ctx.store.q("SELECT path, sig_start, body_start FROM functions WHERE name=? OR name LIKE ? LIMIT 1", (name, "%::" + name))
                if row:
                    add("- %s (함수): %s" % (name, " ".join(get_doc(ctx, row[0]["path"]).text[row[0]["sig_start"]:row[0]["body_start"]].split())[:200]))
                continue
            dc = lookup_decl(ctx, rel, struct1, pos, name, big)
            if dc is None:
                if name.isupper() or name.endswith(("_T", "_t")):                 # 매크로 상수나 타입 이름
                    snip = _snippet_cached(ctx, name)
                    if snip:
                        add("- %s: %s" % (name, " ".join(snip.split("\n", 1)[-1].split())[:200]))
                continue
            dpos = dc.get("pos", -1)
            if dc["kind"] in ("local", "param") and a <= dpos < b:
                continue                                       # 선언이 조각 안에 있다. 다시 붙이지 않는다
            if not add("- %s: %s  (%s%s)" % (name, dc["text"], kind_ko.get(dc["kind"], dc["kind"]), "" if dc.get("certain") else ", 확정 아님")):
                return out
            type_ref(dc["text"], name)
            if dc["kind"] == "local" and "=" in dc["text"]:    # 초기값에 쓰인 이름을 한 단계 따라간다(char* p = user->name 에서 user)
                for n2 in re.findall(r"[A-Za-z_]\w*", dc["text"].split("=", 1)[1])[:3]:
                    if n2 in _REF_SKIP or n2 in seen:
                        continue
                    seen.add(n2)
                    d2 = lookup_decl(ctx, rel, struct1, dpos, n2, big)
                    if d2 and not (d2["kind"] in ("local", "param") and a <= d2.get("pos", -1) < b):
                        add("- %s: %s  (%s)" % (n2, d2["text"], kind_ko.get(d2["kind"], d2["kind"])))
                        type_ref(d2["text"], n2)
    return out


def _enclosing_refs(text1, blocks1, ls1, pos, la):
    """대상 위치를 감싼 제어문 가운데 머리 줄이 조각보다 위에 있는 것을 [참고: 감싼 구문]의 줄 목록으로 만든다(안쪽 3개까지)."""
    chain = []
    k = block_at(blocks1, pos)
    while k >= 0:
        b = blocks1[k]
        if b["kind"] in ("if", "for", "while", "switch", "else", "do", "try", "catch"):
            hl = bisect.bisect_right(ls1, b["head"])
            if hl < la:
                chain.append("- %d줄: %s" % (hl, " ".join(text1[b["head"]:b["open"]].split())[:160]))
        k = b["parent"]
    return list(reversed(chain[:3]))


def _snap_big(struct1, ls1, big, la, lb):
    """조각의 시작이나 끝이 큰 초기화 목록 안에 떨어지면, 그 목록이 속한 문장의 처음(선언 머리)과 끝(;)까지 넓힌다.
    넓히지 않으면 조각 첫 줄이 '{ /*생략*/ };' 만 남아 무엇의 초기값인지 알 수 없다. 생략 구간은 표식 한 줄이라 크기는 늘지 않는다."""
    a = ls1[la - 1]
    b = ls1[lb] - 1 if lb < len(ls1) else len(struct1)
    for s2, e2 in big:
        if s2 < a <= e2:
            la = min(la, bisect.bisect_right(ls1, stmt_start_of(struct1, s2)))
        if s2 <= b < e2:
            sc = struct1.find(";", e2)
            lb = max(lb, bisect.bisect_right(ls1, sc if sc >= 0 else e2))
    return la, min(lb, len(ls1))


def _issue_coupled(ctx, issue):
    """이 이슈가 '결합 수정'인가. 결합 수정은 함수 전체에서 일관돼야 하는 변경이라 한 구간으로 묶어 차례로 처리한다.
    규칙이나 요청에 "coupling": "coupled" 라고 적은 것만 결합이다. 적지 않으면 독립 수정이다."""
    rule = ctx.rule_by_id.get(issue["rule_id"])
    if rule is not None:
        return rule.get("coupling") == "coupled"
    for ft in ctx.request.get("features", []):
        if ft.get("id") == issue["rule_id"]:
            return ft.get("coupling") == "coupled"
    for a in ctx.request.get("asks") or []:
        if a.get("id") == issue["rule_id"]:
            return a.get("coupling") == "coupled"
    return False


def _make_task(rel, d, text1, ls1, big, members, la, lb, fn, guidance, env_txt, note_ctx="", aux=None):
    """본문 text1 의 la~lb 줄을 조각으로 하는 작업 하나를 만든다. members 는 (줄 번호, 이슈) 목록.
    plan 에는 이 작업을 더 작게 다시 만들 때(_replan) 필요한 재료를 넣어 둔다.
    aux: 참고 영역을 만들 재료({"ctx", "struct1", "blocks1", "limit"}). 주면 감싼 구문의 머리와 선언을 [참고]로 붙인다."""
    if aux:
        la, lb = _snap_big(aux["struct1"], ls1, big, la, lb)
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
    refs_text = ""
    if aux and aux.get("limit", 0) > 0:
        enc = _enclosing_refs(text1, aux["blocks1"], ls1, ls1[members[0][0] - 1] + 1, la)
        if enc:
            refs_text += "\n[참고: 감싼 구문] 조각보다 위에 있어 보이지 않는 머리 줄이다. 고칠 수 없다.\n%s\n" % "\n".join(enc)
        try:
            decls = _decl_refs(aux["ctx"], rel, aux["struct1"], text1, ls1, big, members, a, b, aux["limit"])
        except Exception as e:                                # 참고는 보조 정보다. 찾다가 실패해도 작업은 계속한다
            LOG6.debug("%s %d줄: 선언 찾기 실패(%s)" % (rel, members[0][0], e))
            decls = []
        if decls:
            refs_text += "\n[참고: 선언] 대상 줄에 쓰인 이름의 선언이다. 조각 밖에 있으므로 고칠 수 없다.\n%s\n" % "\n".join(decls)
        context += refs_text
    return {"path": rel, "refs_text": refs_text, "issue_ids": [i["id"] for _l, i in members], "text1": text1, "ls1": ls1, "segs": segs,
            "view": view, "codec": d.codec, "t_first": members[0][0], "t_last": members[-1][0], "la": la, "lb": lb,
            "fmt": {"env": env_txt, "guidance": "\n".join(guidance), "targets": "\n".join(targets),
                    "context": context, "path": rel, "start_line": la, "end_line": lb, "code": view},
            "plan": {"d": d, "big": big, "fn": fn, "guidance": guidance, "env": env_txt, "members": list(members), "aux": aux},
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
            t = _make_task(rel, d, text1, ls1, big, mem, la, lb, fn, guidance, env_txt, note, aux=ctx._aux.get(rel))
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
        # 참고 영역의 재료(규칙 수정을 반영한 본문 기준). 선언 찾기와 블록 판단에 쓴다
        struct1 = mask_preprocessor(mask_code(text1)[0])
        aux = {"ctx": ctx, "struct1": struct1, "blocks1": block_tree(_apply_brace_macros(struct1))[0],
               "limit": int(lim.get("ref_chars", 1500) or 0) if lim.get("ref_decl", True) else 0}
        ctx._aux[rel] = aux
        # 독립 수정은 작업 하나의 크기에 상한을 둔다. 한 번의 실패가 미치는 범위를 줄이기 위한 것이다(14_확인... 2절)
        max_t, max_l = int(lim.get("task_max_targets", 8) or 0), int(lim.get("task_max_lines", 150) or 0)
        packs = []
        for cl in clusters:
            if (not max_t and not max_l) or any(_issue_coupled(ctx, i) for _l, i in cl):
                packs.append(cl)                             # 결합 수정(또는 상한을 끈 경우): 구간을 통째로 둔다
                continue
            cur = []
            for item in cl:
                if cur and ((max_t and len(cur) >= max_t) or (max_l and item[0] - cur[0][0] >= max_l)):
                    packs.append(cur)
                    cur = []
                cur.append(item)
            if cur:
                packs.append(cur)
        if len(packs) > len(clusters):
            LOG6.info("%s: 수정 지점 %d곳을 작업 %d건으로 나눔(작업당 지점 %d곳, %d줄 이내)" % (rel, len(located), len(packs), max_t, max_l))
        for cl in packs:
            t_first, t_last = cl[0][0], cl[-1][0]
            ids = [i["id"] for _l, i in cl]
            guidance = []
            near_text = text1[ls1[max(0, t_first - 26)]:(ls1[t_last + 25] if t_last + 25 < len(ls1) else len(text1))]   # 계약 발췌에 쓴다
            for _l, i in cl:
                g = ctx.rule_by_id.get(i["rule_id"], {}).get("guidance", "") or _request_guidance(ctx, i["rule_id"], near_text)
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
                task = _make_task(rel, d, text1, ls1, big, cl, la, lb, fn, guidance, env_txt, aux=aux)
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
                            P["guidance"], P["env"], aux=P.get("aux"))
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


def _explain_no_calls(ctx, n_off):
    """수정 요청을 한 건도 보내지 않을 때 그 이유를 모아 로그와 보고서에 낸다(조용히 끝나지 않게 한다)."""
    st, why = ctx.store, []
    if n_off:
        why.append("환경 호환성 규칙의 sLLM 대상 %d건은 --compat-llm 이 꺼져 있어 보내지 않음(수동 검토 목록에 있음)" % n_off)
    if not (ctx.request.get("asks_raw") or ctx.request.get("features")):
        why.append("요청문(--ask)과 요청 파일(--request)을 주지 않음")
    n_un = st.one("SELECT COUNT(*) FROM issues WHERE source='request' AND status='manual' "
                  "AND title IN ('요청을 분석하지 못함', '요청의 대상을 찾지 못함')") or 0
    if n_un:
        why.append("요청문 가운데 분석하지 못했거나 대상을 찾지 못한 것 %d건(out/ask_plan.json 과 manual_review.md 에 이유가 있음)" % n_un)
    for c in st.get_meta("contracts", []) or []:
        if c.get("type") == "array_to_struct" and c.get("applied") is False:
            why.append("배열 요청 %s 은(는) 적용하지 않음: %s" % (c["id"], " / ".join((c.get("reasons") or ["(이유 기록 없음)"])[:2])))
        elif c.get("type") == "array_to_struct":
            why.append("배열 요청 %s 은(는) 전부 규칙으로 바꿈(sLLM 에 맡길 사용처가 없음)" % c["id"])
        elif c.get("type") == "요청문(찾기)":
            why.append("요청 %s 은(는) 찾기 전용이라 수정 요청을 보내지 않음" % c["id"])
        elif c.get("type") == "요청문(환경 호환성)":
            why.append("요청 %s: 환경 호환성 변경은 요청문으로 처리하지 않음(규칙이 수행. sLLM 시도는 --compat-llm)" % c["id"])
    if not why:
        why.append("규칙과 요청 어디에서도 sLLM 에 맡길 대상이 나오지 않음")
    st.set_meta("no_call_reasons", why)
    LOG6.warn("sLLM 에 수정 요청을 한 건도 보내지 않습니다. 이유:")
    for w in why:
        LOG6.warn("  - " + w)


def s6_llm(ctx):
    st, llm = ctx.store, ctx.cfg["llm"]
    for tbl in ("tasks", "llm_calls"):
        st.x("DELETE FROM %s" % tbl)
    st.x("DELETE FROM edits WHERE origin='llm'")
    ctx.system_prompt = _load_prompt("system.txt", SYSTEM_PROMPT)
    ctx.user_template = _load_prompt("user.txt", USER_TEMPLATE)
    # 이어서 실행: 이 단계는 sLLM 수정 레코드를 지우고 처음부터 다시 만든다. 지난번에 받은 응답은 캐시(llm_cache)에
    # 남아 있어 다시 보내지 않는다. 그래서 대기 이슈뿐 아니라 지난번에 sLLM 수정으로 '고침'이 된 이슈도 함께 다시 연다
    n_back = st.one("SELECT COUNT(*) FROM issues WHERE tier='T3' AND llm=1 AND status='pending'") or 0
    n_redo = st.one("SELECT COUNT(*) FROM issues WHERE tier='T3' AND llm=1 AND status='fixed'") or 0
    st.x("UPDATE issues SET status='open', note='' WHERE status='pending'")
    st.x("UPDATE issues SET status='open' WHERE tier='T3' AND llm=1 AND status='fixed'")
    st.x("UPDATE edits SET status='accepted' WHERE status='held'")   # 대기 때문에 빼 두었던 요청의 수정을 다시 넣는다(조각의 기준 본문이 병합 때와 같아야 한다)
    if n_back or n_redo:
        LOG6.info("이어서 실행: 대기로 남은 이슈 %d건을 보냅니다. 지난번에 고친 %d건은 받아 둔 응답을 다시 씁니다(호출하지 않음)" % (n_back, n_redo))
    st.set_meta("llm_pending", 0)
    n_off = 0
    if not ctx.opts.get("compat_llm"):            # 기본: 환경 호환성 항목은 기계적 변경까지만 한다. 규칙의 sLLM 대상은 보내지 않는다
        n_off = st.one("SELECT COUNT(*) FROM issues WHERE source='rule' AND tier='T3' AND llm=1 AND status='open'") or 0
        if n_off:
            st.x("UPDATE issues SET status='manual', note=? WHERE source='rule' AND tier='T3' AND llm=1 AND status='open'",
                 ("환경 호환성 항목의 sLLM 수정이 꺼져 있음(--compat-llm 으로 켠다)",))
            LOG6.info("환경 호환성 규칙의 sLLM 대상 %d건은 보내지 않고 수동 검토로 둡니다(--compat-llm 이 꺼져 있음)" % n_off)
    st.set_meta("no_call_reasons", [])
    n_open = st.one("SELECT COUNT(*) FROM issues WHERE tier='T3' AND llm=1 AND status='open'")
    if not n_open:
        _explain_no_calls(ctx, n_off)
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

    # 이어서 실행할 때는 처음 계획할 때의 추정값으로 나눈다. 분할 순차의 조각 경계가 지난번과 같아야 받아 둔 응답을
    # 캐시에서 다시 쓸 수 있다(경계가 달라지면 요청문이 달라져 다시 보내게 된다). 계획이 끝나면 배운 보정값으로 되돌린다
    learned = (llm.get("_ratio"), llm.get("_overhead"))
    pc = st.get_meta("plan_calib", None) if (n_back or n_redo) else None
    if pc and pc.get("model") == llm["model"]:
        for k, v in (("_ratio", pc.get("ratio")), ("_overhead", pc.get("overhead"))):
            if v is None:
                llm.pop(k, None)
            else:
                llm[k] = v
    else:
        pc = None
        st.set_meta("plan_calib", {"model": llm["model"], "ratio": learned[0], "overhead": learned[1]})
    tasks = plan_tasks(ctx)
    if pc:
        for k, v in (("_ratio", learned[0]), ("_overhead", learned[1])):
            if v is not None:
                llm[k] = v
    LOG6.info("sLLM 작업 %d건 (이슈 %d건), 동시 요청 %d, 한도 %d토큰(출력 상한 %d, 서버 몫 %d)"
              % (len(tasks), n_open, llm["max_parallel"], llm["max_total_tokens"], llm["max_output_tokens"],
                 overhead_tokens(llm)))
    os.makedirs(ctx.p("logs", "llm"), exist_ok=True)
    pending, conn_fail, any_ok, n_replan, n_reject = tasks, 0, False, 0, 0
    max_calls = int(ctx.opts.get("max_calls") or 0)        # 이번 실행에서 보낼 호출 수의 예산(0 이면 제한 없음)
    waiting, busy_streak, stop_why = [], 0, ""            # 대기로 남길 작업, 연속 과부하 횟수, 보내기를 멈춘 이유
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
            write_text(ctx.p("logs", "llm", "task%04d_a%d_prompt.txt" % (t["id"], t["attempts"])),
                       ctx.system_prompt + "\n\n=====\n\n" + t["prompt"])
            send.append(t)
            if t["cached"] is None:
                jobs.append(t)
        held = []
        if max_calls:
            room = max_calls - ctx.llm_calls
            if room < len(jobs):                  # 예산이 모자란다. 넘치는 작업은 이번에 보내지 않는다
                held = jobs[max(0, room):]
                jobs = jobs[:max(0, room)]
                hold_ids = {t["id"] for t in held}
                send = [t for t in send if t["id"] not in hold_ids]
                for t in held:
                    t["attempts"] -= 1            # 보내지 않았으므로 시도 횟수에서 뺀다
                stop_why = "호출 예산(--max-calls %d)을 다 씀" % max_calls
        LOG6.info("%d차 요청: 작업 %d건 (캐시 재사용 %d건, 다음 차례를 기다리는 작업 %d건)"
                  % (round_no, len(send), len(send) - len(jobs), len(pending) - len(ready)))
        ctx.llm_calls += len(jobs)
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
                busy_streak = 0
            if not ok and kind == "busy":
                # 서버가 끝까지 바쁘다고 답했다(일일 한도 소진 포함). 이 작업은 실패가 아니라 대기다
                record(t, rfile, ms, False, "서버 과부하로 보내지 못함(대기)", info, "busy")
                t["attempts"] -= 1
                waiting.append(t)
                busy_streak += 1
                continue
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
                # 모델이 수정 불필요로 답했거나 끝내 고치지 못했다. 고치지 않아도 되는 대상이면 수동 검토로 올리지 않는다
                _mark_unchanged(ctx, t["issue_ids"], "sLLM 수정 실패: " + (note or verdict), failed=(verdict != "변경없음"))
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
        wait_ids = {t["id"] for t in waiting}
        rest = []
        for t in pending:
            if t["id"] in swap:
                rest.extend(swap[t["id"]])        # 다시 계획한 작업이 옛 작업의 자리를 잇는다
            elif t["id"] in drop or t.get("group") in cancelled or t["id"] in wait_ids:
                continue
            elif t["id"] in retry_ids or t["id"] not in ready_ids or t in held:
                rest.append(t)
        if busy_streak >= 3 and not stop_why:
            stop_why = "서버가 계속 바쁘다고 답함(과부하 또는 사용 한도)"
        if stop_why:
            # 더 보내지 않는다. 남은 작업은 대기로 저장해 다음 실행(--resume)이 이어서 보내게 한다
            waiting.extend(rest)
            rest = []
        if conn_fail >= 3 and not any_ok:
            LOG6.error("sLLM 서버 호출이 연속으로 실패했습니다. 주소와 서버 상태를 확인하세요: %s" % llm["endpoint"])
            for t in rest:
                _mark_manual(ctx, t["issue_ids"], "sLLM 서버 호출 실패")
                st.x("UPDATE tasks SET status='manual', note='sLLM 서버 호출 실패' WHERE id=?", (t["id"],))
            st.commit()
            break
        pending = rest
    if waiting:
        n_wait_issues = 0
        for t in waiting:
            st.x("UPDATE tasks SET status='waiting', note=? WHERE id=?", (stop_why or "대기", t["id"]))
            for iid in t["issue_ids"]:
                st.x("UPDATE issues SET status='pending', note=? WHERE id=? AND status='open'", ("sLLM 에 보내지 못함(대기): " + (stop_why or ""), iid))
                n_wait_issues += 1
        # 분할 순차 구간의 뒤 조각이 대기로 남았으면, 이미 수용한 앞 조각의 수정도 이번 결과에서 뺀다.
        # 한 구간이 반만 바뀐 결과를 내지 않기 위한 것이다. 응답은 캐시에 남아 있어 이어서 실행할 때 호출 없이 되살아난다
        wait_ids = {t["id"] for t in waiting}
        for g in sorted({t["group"] for t in waiting if t.get("group")}):
            done_parts = [x for x in (groups.get(g) or []) if x["id"] not in wait_ids]
            if not done_parts:
                continue
            refs = ["task%d" % x["id"] for x in done_parts]
            st.x("DELETE FROM edits WHERE origin='llm' AND status='accepted' AND ref IN (%s)" % ",".join("?" * len(refs)), refs)
            for x in done_parts:
                for iid in x["issue_ids"]:
                    st.x("UPDATE issues SET status='pending', note='같은 구간의 뒤 조각이 sLLM 대기 중' WHERE id=? AND status='open'", (iid,))
                    n_wait_issues += 1
            LOG6.info("%s: 분할 구간의 뒤 조각이 대기 중이어서 앞 조각 %d개의 수정을 이번 결과에 넣지 않습니다" % (done_parts[0]["path"], len(done_parts)))
        st.commit()
        st.set_meta("llm_pending", n_wait_issues)
        LOG6.warn("%s. 작업 %d건(이슈 %d건)을 대기로 남깁니다. --resume \"%s\" 로 이어서 보낼 수 있습니다"
                  % (stop_why or "보내지 못한 작업이 있음", len(waiting), n_wait_issues, ctx.run_dir))
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


# ---- 수용 검사 추가(0.8.0): 새 이름, 선언 변화, 크기 인자, 변경 범위, 중복 선언 ------------------------
# 목적: 수정 블록이 형식과 위치는 맞아도 "조각 밖"을 깨뜨리는 경우를 컴파일러 없이 잡는다(15_구분별... 7절 E2~E4, E8, E9).
#   새 이름   : REPLACE 에 조각, 참고, 지침, 패키지 어디에도 없는 이름이 생겼는가(기본은 기각하지 않고 검토 메모만 남긴다)
#   선언 변화 : 선언을 없앴는데 그 이름이 다른 곳에서 쓰이는가 / 새 선언의 이름이 이미 선언돼 있는가(기각)
#   크기 인자 : 새로 쓴 sizeof(이름), _countof(이름) 의 이름이 포인터인가(확정이면 기각, 불확실하면 메모)
#   변경 범위 : ① 함수 안  ② 파일 안(static)  ③ 파일 밖에 영향(헤더, 전역, 외부 함수 머리). 기록만 한다
#   중복 선언 : 병합 직전에, 수정으로 생긴 선언이 같은 블록에서 두 번 선언되는지 본다(뒤 수정을 기각)
# 한계: 글자 기준의 근사다. 타입 이름과 변수 이름을 완전히 가르지 못한다. 실제 모델의 응답으로는 확인하지 못했다.
_CPP_WORDS = frozenset("""alignas alignof and asm auto bool break case catch char char16_t char32_t class const constexpr
const_cast continue decltype default delete do double dynamic_cast else enum explicit export extern false float for friend
goto if inline int long mutable namespace new noexcept not nullptr operator or private protected public register
reinterpret_cast return short signed sizeof static static_assert static_cast struct switch template this thread_local throw
true try typedef typeid typename union unsigned using virtual void volatile wchar_t while xor override final
__int8 __int16 __int32 __int64 __cdecl __stdcall __fastcall __declspec __inline __forceinline""".split())
_STD_NAMES = frozenset("""NULL TRUE FALSE BOOL BYTE WORD DWORD UINT INT LONG ULONG LPSTR LPCSTR LPWSTR LPCWSTR LPTSTR LPCTSTR TCHAR
WCHAR CHAR HANDLE HWND HRESULT LPVOID VOID SIZE_T size_t ssize_t ptrdiff_t errno_t rsize_t intptr_t uintptr_t INT_PTR UINT_PTR
LONG_PTR ULONG_PTR DWORD_PTR int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t std string wstring
stdin stdout stderr errno EOF _TRUNCATE _countof ARRAYSIZE _T TEXT L strlen wcslen strnlen memset memcpy memmove memcmp
c_str size length data begin end empty npos
printf fprintf sprintf snprintf _snprintf vsnprintf _vsnprintf sprintf_s _snprintf_s vsprintf_s _vsnprintf_s swprintf_s
scanf sscanf scanf_s sscanf_s fscanf fscanf_s fgets fputs puts gets_s fopen fopen_s _wfopen_s fclose fread fwrite
strcpy strcpy_s strncpy strncpy_s strcat strcat_s strncat strncat_s strtok strtok_s strcmp strncmp _stricmp _strnicmp
strchr strrchr strstr _strdup wcscpy_s wcsncpy_s wcscat_s wcsncat_s _wcsdup memcpy_s memmove_s
_itoa_s _ltoa_s _ultoa_s _i64toa_s _ui64toa_s _itow_s _ltow_s _strlwr_s _strupr_s _wcslwr_s _wcsupr_s _mktemp_s
_ecvt_s _fcvt_s _gcvt_s atoi atol atof strtol strtoul strtod malloc calloc realloc free
localtime_s gmtime_s ctime_s asctime_s _localtime64_s getenv_s _dupenv_s
inet_ntop inet_pton InetNtop InetPton getaddrinfo freeaddrinfo addrinfo AF_INET AF_INET6 INET_ADDRSTRLEN
VerifyVersionInfo VerSetConditionMask OSVERSIONINFOEX IsWindowsVersionOrGreater IsWindows7OrGreater IsWindows8OrGreater
IsWindows10OrGreater""".split())
_HEADER_EXT = (".h", ".hpp", ".hxx", ".hh", ".inl")
_SIZE_OF_RE = re.compile(r"\b(?:sizeof|_countof|ARRAYSIZE|_ARRAYSIZE)\s*\(\s*([A-Za-z_]\w*)\s*\)")
_GRADE_TAG = {1: "범위① 함수 안", 2: "범위② 파일 안(static)", 3: "범위③ 파일 밖에 영향"}


def _decl_names(code):
    """마스킹한 코드 조각에서 선언된 이름을 뽑는다. 돌려주는 값: {이름: 이름 뒤의 [..] 글자}
    문장 단위(; { } 로 나눔)로 선언 정규식(decl_regex)을 맞춰 본다. for 의 머리 안 선언은 보지 않는다."""
    out = {}
    for stmt in re.split(r"[;{}]", code):
        if not stmt.strip():
            continue
        for n in set(re.findall(r"[A-Za-z_]\w*", stmt)):
            if n in _CPP_WORDS or n in out:
                continue
            m = decl_regex(n).match(stmt)
            if m:
                out[n] = m.group(1) or ""
    return out


def _code_names(ctx):
    """패키지의 소스 코드에 실제로 나오는 이름 전부(주석과 문자열 안은 뺀다). 처음 필요할 때 한 번 만든다.
    요청문 분석에서 "이 이름이 코드에 있는가"를 볼 때 쓴다."""
    if ctx._code_names is None:
        s = set()
        for r in ctx.store.q("SELECT path FROM files WHERE kind='source' ORDER BY path"):
            try:
                s.update(re.findall(r"[A-Za-z_]\w*", get_doc(ctx, r["path"]).code))
            except Exception:                              # 읽지 못한 파일은 건너뛴다(이 색인은 보조 정보다)
                continue
        ctx._code_names = s
    return ctx._code_names


def _package_names(ctx):
    """새 이름 검사에서 '아는 이름'으로 칠 것: 코드의 이름, 규칙 지침에 나오는 이름, 공통 요청에 나오는 이름."""
    if ctx._names is None:
        s = set(_code_names(ctx))
        for r in ctx.rule_by_id.values():
            s.update(re.findall(r"[A-Za-z_]\w*", "%s %s %s" % (r.get("guidance") or "", r.get("title") or "", r.get("llm_guidance") or "")))
        for line in ctx.request.get("common") or []:
            s.update(re.findall(r"[A-Za-z_]\w*", str(line)))
        ctx._names = s
    return ctx._names


def _edit_grade(rel, blocks1, e):
    """수정 하나가 미치는 범위의 등급(요구사항 E2). 1: 함수 본문 안, 2: 파일 안(static 선언·static 함수 머리), 3: 파일 밖에 영향"""
    if rel.lower().endswith(_HEADER_EXT):
        return 3
    fi = block_at(blocks1, e["start"], kinds=("func",))
    if fi >= 0 and e["end"] <= blocks1[fi]["close"]:
        return 1
    return 2 if re.match(r"\s*static\b", e["before"]) else 3


def _edit_checks(ctx, t, edits):
    """수정 블록들의 추가 검사. 돌려주는 값: (기각 사유 목록, {수정의 순번: 메모 목록})
    기각 사유가 있으면 부르는 쪽이 그 사유를 붙여 다시 요청한다. 메모는 수정 레코드에 남아 보고서의 검토 목록에 나온다."""
    aux = (t.get("plan") or {}).get("aux")
    reasons, notes = [], {}
    if not aux:
        return reasons, notes
    lim = ctx.cfg["limits"]
    mode = str(lim.get("new_name_check", "mark"))
    struct1, blocks1, big, rel = aux["struct1"], aux["blocks1"], (t["plan"].get("big") or ()), t["path"]
    allowed = None
    for k, e in enumerate(edits):
        memo = []
        nb, na = mask_code(e["before"])[0], mask_code(e["after"])[0]
        db, da = _decl_names(nb), _decl_names(na)
        fi = block_at(blocks1, e["start"], kinds=("func",))
        ids_a = set(re.findall(r"[A-Za-z_]\w*", na))
        if fi >= 0 and lim.get("decl_change_check", True):
            fb = blocks1[fi]
            for n in sorted(set(db) - set(da)):           # 없어졌거나 이름이 바뀐 선언
                rx = re.compile(r"\b%s\b" % re.escape(n))
                if n in ids_a or rx.search(struct1, fb["open"], e["start"]) or rx.search(struct1, e["end"], fb["close"]):
                    reasons.append("선언을 없앤 이름 %s 이(가) 다른 줄에서 쓰인다. 선언을 지우거나 이름을 바꾸지 않는다" % n)
            for n in sorted(set(da) - set(db)):           # 새로 생긴 선언
                r = find_decl(struct1, e["start"], n, big)
                if r and r["kind"] in ("local", "param"):
                    reasons.append("새로 선언한 이름 %s 은(는) 이 함수에 이미 선언돼 있다(%s). 있는 변수를 쓰거나 다른 이름을 쓴다"
                                   % (n, " ".join(r["text"].split())[:60]))
        if lim.get("size_arg_check", True):
            fresh = collections.Counter(_SIZE_OF_RE.findall(na)) - collections.Counter(_SIZE_OF_RE.findall(nb))
            for n in sorted(fresh):
                if n in _CPP_WORDS or n in _STD_NAMES:
                    continue
                if n in da:                               # 이 수정 안에서 새로 선언한 이름
                    if not da[n] and re.search(r"\*\s*(?:const\s+)?%s\b" % re.escape(n), na):
                        reasons.append("sizeof(%s) 를 크기로 썼는데 %s 은(는) 포인터다. 버퍼의 실제 크기를 쓴다" % (n, n))
                    continue
                r = lookup_decl(ctx, rel, struct1, e["end"], n, big) if fi >= 0 else None
                if not r:
                    if n not in _class_index(ctx):         # 타입 이름이면 sizeof(타입)이다. 볼 것이 없다
                        memo.append("sizeof(%s): 선언을 찾지 못해 배열인지 확인하지 못함" % n)
                    continue
                ptr = bool(r.get("is_pointer")) or (r["kind"] == "param" and bool(r.get("is_array")))
                if ptr and r.get("certain"):
                    reasons.append("sizeof(%s) 를 크기로 썼는데 %s 은(는) 포인터다(%s). 포인터의 크기는 버퍼의 크기가 아니다. "
                                   "버퍼 크기를 알 수 없으면 NO_CHANGE 를 쓴다" % (n, n, " ".join(str(r.get("text") or "").split())[:60]))
                elif ptr or not r.get("certain"):
                    memo.append("sizeof(%s): 배열인지 확정하지 못함(%s)" % (n, r["kind"]))
        if mode != "off":
            cand = ids_a - set(re.findall(r"[A-Za-z_]\w*", nb)) - set(da)
            if cand:
                if allowed is None:
                    allowed = set(_package_names(ctx))
                    for txt in (t["view"], t["fmt"].get("context", ""), t["fmt"].get("guidance", ""), t["fmt"].get("targets", ""), t.get("extra", "")):
                        allowed.update(re.findall(r"[A-Za-z_]\w*", txt or ""))
                    allowed.update(str(x) for x in (lim.get("known_names") or []))
                new = sorted(n for n in cand if n not in allowed and n not in _CPP_WORDS and n not in _STD_NAMES)
                if new and mode == "reject":
                    reasons.append("조각과 패키지 어디에도 없는 이름을 썼다: %s. 있는 이름만 쓴다" % ", ".join(new[:6]))
                elif new:
                    memo.append("조각과 패키지에 없던 이름: %s" % ", ".join(new[:6]))
        memo.insert(0, _GRADE_TAG[_edit_grade(rel, blocks1, e)])
        notes[k] = memo
    return reasons, notes


def _dup_decl_check(ctx):
    """병합(S7) 직전에 부른다. sLLM 수정으로 새로 생긴 선언이 같은 블록에서 두 번 선언되면 그 수정을 기각한다(요구사항 E8).
    원래 코드의 선언과 겹치면 그 수정을, 두 수정이 같은 이름을 선언하면 뒤 수정을 기각한다. 돌려주는 값: 기각한 수정 수"""
    st, n_rej = ctx.store, 0
    for row in st.q("SELECT DISTINCT path FROM edits WHERE origin='llm' AND base='post_rule' AND status IN (?,?,?) ORDER BY path", _LIVE):
        rel = row["path"]
        _d, text1, bad = build_merged(ctx, rel, use_llm=False)
        if bad:
            continue                                      # 규칙 수정이 어긋난 파일은 병합 단계가 따로 처리한다
        out, last, placed = [], 0, []
        for e in live_edits(ctx, rel, "post_rule"):
            if e["start"] < last or text1[e["start"]:e["end"]] != e["before"]:
                continue                                  # 겹치거나 원문이 다른 수정은 병합 단계가 기각한다
            added = set(_decl_names(mask_code(e["after"])[0])) - set(_decl_names(mask_code(e["before"])[0]))
            out.append(text1[last:e["start"]])
            pos2 = sum(len(x) for x in out)
            out.append(e["after"])
            last = e["end"]
            if added:
                placed.append((e["id"], pos2, e["after"], added))
        if not placed:
            continue
        out.append(text1[last:])
        text2 = "".join(out)
        struct2 = mask_preprocessor(mask_code(text2)[0])
        blocks2 = block_tree(_apply_brace_macros(struct2))[0]
        groups = {}                                       # (블록 번호, 이름) -> 그 이름을 선언한 수정 번호들(위치 순서)
        for eid, pos2, after, added in placed:
            for n in sorted(added):
                m = re.search(r"\b%s\b" % re.escape(n), mask_code(after)[0])
                bi = block_at(blocks2, pos2 + (m.start() if m else 0))
                groups.setdefault((bi, n), []).append(eid)
        rej = {}
        for (bi, n), eids in sorted(groups.items()):
            if bi < 0:
                continue
            b = blocks2[bi]
            inner = list(struct2[b["open"] + 1:b["close"]])
            for c in blocks2:                             # 안쪽 블록의 내용은 가린다(다른 범위다)
                if c["parent"] == bi and c["close"] > c["open"]:
                    for i in range(c["open"] + 1 - b["open"] - 1, c["close"] - b["open"] - 1):
                        if 0 <= i < len(inner) and inner[i] != "\n":
                            inner[i] = " "
            rx = decl_regex(n)
            total = sum(1 for stmt in re.split(r"[;{}]", "".join(inner)) if stmt.strip() and rx.match(stmt))
            if total < 2:
                continue
            existing = total - len(eids)
            for eid in (eids if existing >= 1 else eids[1:]):
                rej.setdefault(eid, "같은 블록에 %s 이(가) 두 번 선언됨(중복 선언)" % n)
        if rej:
            reject_edits(ctx, sorted(rej.items()))
            n_rej += len(rej)
            for eid, why in sorted(rej.items()):
                LOG7.warn("%s: 수정 레코드 %d 제외(%s)" % (rel, eid, why))
    if n_rej:
        st.commit()
    return n_rej


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
    try:
        more, memos = _edit_checks(ctx, t, edits)
    except Exception as e:                        # 추가 검사는 보조 수단이다. 검사 자체가 실패하면 기록하고 넘어간다
        LOG6.warn("%s: 추가 수용 검사를 하지 못함(%s). 이 수정은 기본 검사만 거쳤습니다" % (t["path"], e))
        more, memos = [], {}
    if more:
        t["feedback"] = "\n".join("- " + r for r in more)
        return "재요청", "수정 블록 기각: " + "; ".join(more)
    # 수정 대상 줄마다 그 줄을 실제로 바꾼 수정 블록이 있는지 본다. 없는 지점은 "수정됨"으로 치지 않는다
    spans = [(bisect.bisect_right(t["ls1"], e["start"]), bisect.bisect_right(t["ls1"], max(e["start"], e["end"] - 1)))
             for e in edits]
    missed = [i["id"] for line, i in t["plan"]["members"] if not any(a <= line <= b for a, b in spans)]
    if len(missed) == len(t["issue_ids"]):
        t["feedback"] = "- 수정 블록이 [수정 대상 줄]을 바꾸지 않았다. 대상 줄을 SEARCH 에 넣어 고친다."
        return "재요청", "수정 블록이 대상 줄을 건드리지 않음"
    if missed:
        _mark_unchanged(ctx, missed, "sLLM 응답에 이 지점의 수정이 없음. 사람이 확인해야 함")
    ids = ",".join(str(x) for x in t["issue_ids"] if x not in missed)
    for k, e in enumerate(edits):
        memo = ([("분할 순차 %d/%d" % (t["part"], t["parts"]))] if t.get("group") else []) + list(memos.get(k) or [])
        st.x("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (t["path"], "post_rule", e["start"], e["end"], e["before"], e["after"], "llm", "task%d" % t["id"],
              ids, 1, "accepted", "; ".join(memo)))
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
    # 이어서 실행할 때 이 단계에 다시 들어온다. 지난번에 정한 결과 상태를 처음 값(대상, 읽기전용)으로 돌려놓고 다시 정한다
    st.x("UPDATE files SET status=CASE WHEN roundtrip=1 THEN '대상' ELSE '읽기전용' END, verify_level='' "
         "WHERE kind='source' AND status IN ('병합됨','변경됨','부분수정','수동검토','변경없음','변경없음(읽기전용)')")
    st.x("UPDATE edits SET status='accepted' WHERE status IN ('applied','verified','held')")   # 기각된 것은 그대로 둔다
    _requests_finalize(ctx)                                 # 계약 요청: sLLM 에 맡긴 사용처가 실패했으면 여기서 정리한다
    try:
        n_dup = _dup_decl_check(ctx)                        # sLLM 수정끼리, 또는 원래 코드와 같은 이름을 두 번 선언하는지 본다
    except Exception as e:
        LOG7.warn("중복 선언 검사를 하지 못함(%s)" % e)
        n_dup = 0
    if n_dup:
        LOG7.warn("중복 선언으로 sLLM 수정 %d건을 뺐습니다(해당 이슈는 수동 검토)" % n_dup)
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
    _requests_check(ctx)                                    # 계약 요청의 잔여 재탐지(E4)


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
            kept = st.one("SELECT COUNT(*) FROM issues WHERE source='request' AND rule_id=? AND status='skipped' "
                          "AND note LIKE 'sLLM 이 고치지 않음%'", (c["id"],)) or 0
            L.append("- **%s** (%s, 대상 `%s`): %s. 수동 검토로 남은 사용처 %d건%s"
                     % (c["id"], c["type"], c["target"], ", ".join("%s %d" % kv for kv in c["counts"].items()) or "-", left,
                        (", sLLM 이 고칠 필요가 없다고 본 곳 %d건" % kept) if kept else ""))
            if c.get("rename"):
                L.append("  - 이름 대응: " + ", ".join("`%s` → `%s`" % kv for kv in list(c["rename"].items())[:30]))
            if c.get("applied") is False:
                L.append("  - **적용하지 않음**. 이유: " + " / ".join(c.get("reasons") or ["(기록 없음)"]))
            elif c.get("held"):
                L.append("  - **대기 중**: sLLM 에 보내지 못한 사용처 %d곳이 남아, 이 요청의 수정은 이번 결과에 넣지 않았다. "
                         "이어서 실행하면 다시 넣는다" % c["held"])
            elif c.get("type") == "array_to_struct" and left:
                L.append("  - **미완료**: 사용처 %d곳이 옛 꼴로 남았다. 그곳을 고치기 전에는 컴파일되지 않는다" % left)
            if c.get("broken"):
                L.append("  - **결과가 어긋남**: %s. 결과물이 컴파일되지 않을 수 있다" % c["broken"])
            for w in c.get("warnings") or []:
                L.append("  - 알아 둘 것: " + w)
            if c.get("note"):
                L.append("  - " + c["note"])
            if c.get("definition_after"):
                L += ["  - 새 정의:", "", "```cpp", c["definition_after"], "```", ""]
    n_cs = st.one("SELECT COUNT(*) FROM issues WHERE source='compat'") or 0
    if n_cs:
        cs = {r["status"]: r["n"] for r in st.q("SELECT status, COUNT(*) AS n FROM issues WHERE source='compat' GROUP BY status")}
        L += ["", "### 환경 호환성 항목의 sLLM 수정 시도(--compat-llm)", "",
              "- 설정으로 흡수하던 항목(보안 CRT, Winsock)의 사용 줄 %d곳을 sLLM 에 보냈다: 고침 %d, sLLM 이 그대로 둠 %d, 수동 검토 %d, 대기 %d"
              % (n_cs, cs.get("fixed", 0), cs.get("skipped", 0), cs.get("manual", 0) + cs.get("open", 0), cs.get("pending", 0)),
              "- 고친 곳은 실행 동작이 달라질 수 있다(보안 CRT 함수는 버퍼가 모자라면 프로그램을 끝낸다). 모두 검토 대상이다.",
              "- 그대로 둔 곳은 설정(_CRT_SECURE_NO_WARNINGS 등)으로 경고가 꺼지므로 빌드에는 지장이 없다."]
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
    write_text(os.path.join(out, "project_settings.md"), "\n".join(L) + "\n")

    # --- 빌드 환경용 절차서 ---
    G = ["# 빌드 환경에서 할 일", "",
         "1. 원본 패키지를 복사한다. 변환은 되돌릴 수 없으므로 복사본에서 한다.",
         "2. `out/changed/` 의 파일을 복사본의 같은 위치에 덮어쓴다.",
         "3. 복사본의 .sln 을 대상 Visual Studio 로 열어 프로젝트를 변환한다. 변환 보고서(UpgradeLog.htm)를 보관한다.",
         "4. `project_settings.md` 의 설정을 프로젝트 속성에 적용한다.",
         "5. 빌드하고 오류 로그를 파일로 남긴다. 개발자 PowerShell 에서:",
         "", "```", "msbuild 솔루션이름.sln /p:Configuration=Release /p:Platform=Win32 /fl", "```", "",
         "6. 오류가 나면 msbuild.log 와 `manual_review.md` 를 함께 보고 남은 항목을 처리한다(빌드 로그를 읽어 분류하는 기능은 0.9.0 에서 뺐다).",
         "7. 링크 오류와 실행 시 동작은 이 도구의 검증 범위 밖이다. 직접 확인한다.", ""]
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
    opt_lines.append("환경 호환성 항목의 sLLM 수정: %s" % ("켬(--compat-llm). 규칙의 sLLM 대상과 설정 흡수 항목의 사용 줄을 보냈다"
                                                  if o.get("compat_llm") else "끔(기본). 기계적 변경과 설정 흡수까지만 했다"))
    for key, text in (("no_llm", "sLLM 을 쓰지 않음(--no-llm). sLLM 대상 이슈는 수동 검토로 보냈다"),
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
         "- 수행한 단계: %s%s" % (", ".join(done + (["S9"] if "S9" not in done else [])), " (탐지만 수행)" if ctx.dry_run else "")]
    n_wait = st.one("SELECT COUNT(*) FROM issues WHERE status='pending'") or 0
    if n_wait:
        R += ["", "> **아직 끝나지 않은 실행이다.** sLLM 에 보내지 못한 이슈 %d건이 대기로 남았다(호출 예산 또는 서버 한도). "
              "이 보고서와 결과 파일은 지금까지 받은 응답만 반영한다. 이어서 실행: `run --resume \"%s\"`" % (n_wait, ctx.run_dir)]
    no_call = st.get_meta("no_call_reasons", []) or []
    if no_call:
        R += ["", "> **sLLM 에 수정 요청을 한 건도 보내지 않았다.** 이유:"] + [">", ] + ["> - " + w for w in no_call]
    R += ["", "## 1. 적용한 환경값", ""] + ["- " + s for s in env_summary_lines(env)]
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
    review = st.q("SELECT e.path, e.origin, e.ref, e.before, e.after, e.note FROM edits e WHERE e.status='verified' AND e.review=1 ORDER BY e.path, e.start")
    if review:
        g3 = sum(1 for r in review if r["origin"] == "llm" and "범위③" in (r["note"] or ""))
        g2 = sum(1 for r in review if r["origin"] == "llm" and "범위②" in (r["note"] or ""))
        g1 = sum(1 for r in review if r["origin"] == "llm" and "범위①" in (r["note"] or ""))
        R += ["", "### 적용했지만 사람이 확인해야 하는 수정", "",
              "sLLM 수정의 변경 범위: 함수 안 %d건, 파일 안(static) %d건, 파일 밖에 영향(헤더·전역·외부 함수 머리) %d건. "
              "범위③은 다른 파일의 빌드에 영향을 줄 수 있으므로 먼저 본다." % (g1, g2, g3), ""] + _table(
                  ["파일", "출처", "근거", "변경 전", "변경 후", "확인할 점"], [
            (r["path"], r["origin"], r["ref"], r["before"][:70], r["after"][:70], r["note"] or "") for r in review])
    n_manual = st.one("SELECT COUNT(*) FROM issues WHERE status IN ('manual','open') AND tier!='T0'")
    R += _extra_report_lines(ctx)
    R += ["", "## 8. 남은 일", "", "- 수동 검토 %d건: `manual_review.md`" % n_manual,
          "- 프로젝트 설정 변경: `project_settings.md`", "- 실제 빌드 절차: `build_guide.md`",
          "", "## 9. 이 버전(%s)이 하지 않는 것" % VERSION, "",
          "- 프로젝트 변환과 전체 빌드, .rc/.idl/.def 의 규칙 탐지",
          "- DB, HMI 도구에 따른 내장 규칙(값은 프롬프트와 보고서에 전달된다)",
          "- 컴파일러 수준의 영향범위 분석(구조체 변경, 배열·표 변환의 사용처 분류는 글자 기준의 근사다)",
          "- 평면 배열(레코드 간격)의 구조체 변환, 열 타입을 바꿀 때의 값 범위 검사",
          "- 빌드 오류 로그의 분류와 되먹임, 링크 오류와 프로젝트 설정 오류의 수정",
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
    n_manual = st.one("SELECT COUNT(*) FROM issues WHERE status IN ('manual','open') AND tier!='T0'") or 0
    n_changed = st.one("SELECT COUNT(*) FROM files WHERE status IN ('변경됨','부분수정')") or 0
    n_pending = st.one("SELECT COUNT(*) FROM issues WHERE status='pending'") or 0
    if n_pending:
        # 보내지 못한 작업이 있다. sLLM 단계부터 다시 돌 수 있게 그 뒤 단계를 '끝난 단계'에서 뺀다
        st.set_meta("stages_done", [x for x in done if x not in ("S6", "S7", "S8", "S9")])
    if ctx.dry_run:
        LOGM.info("탐지만 수행했습니다 (%.1f초): 처리 대상 이슈 %d건. 건수는 out/report.md 4절 참조"
                  % (time.time() - t_all, n_manual))
        LOGM.info("산출물: %s" % ctx.p("out"))
        return EXIT_OK
    LOGM.info("전체 완료 (%.1f초): 변경 파일 %d개, 수동 검토 %d건%s" % (time.time() - t_all, n_changed, n_manual,
                                                           (", 대기 %d건" % n_pending) if n_pending else ""))
    LOGM.info("산출물: %s" % ctx.p("out"))
    LOGM.info("로그: %s" % ctx.p("logs", "run.log"))
    if n_pending:
        LOGM.warn("sLLM 에 보내지 못한 이슈 %d건이 대기로 남았습니다. 지금의 결과는 받은 응답까지만 반영한 것입니다. "
                  "이어서 실행: --resume \"%s\"" % (n_pending, ctx.run_dir))
        return EXIT_PENDING
    return EXIT_REVIEW if n_manual else EXIT_OK


# ============================================================================
# [M12 부가명령]  서버 점검(llmcheck)
# 목적: 단계(S0~S9) 밖에서 쓰는 보조 명령.
#   llmcheck : 실제 sLLM(또는 중계 서버)이 이 도구가 기대하는 대로 답하는지 확인한다.
#              토큰 수를 돌려주는가, 한도 초과를 어떻게 거절하는가, 출력 형식을 지키는가, 동시에 처리하는가.
# (빌드 오류 목록을 분류하던 buildlog 명령과 --build-log 는 0.9.0 에서 뺐다. 0.8.0 에 있다)
# ============================================================================
LOGT = Log("M12.부가명령")


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
# [M11 명령줄]  run / preflight / status / rules / selftest (llmcheck 는 M12)
# 목적: 사용자가 부르는 진입점. 인자를 검증해 실행 문맥(ctx)을 만들고 실행기를 부른다.
# 종료 코드: 0 완료(수동 검토 없음) / 1 완료(수동 검토 있음) / 2 단계 실패 / 3 입력 오류
#            4 sLLM 에 보내지 못한 작업이 대기로 남음(호출 예산, 서버 한도). run --resume 으로 이어서 보낸다
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
        saved_ep = str(((info.get("config") or {}).get("llm") or {}).get("endpoint") or "")
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
                       "policy": {"deprecated_warnings": "suppress"}}
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
    ep_env = os.environ.get(ENV_LLM_ENDPOINT, "").strip()
    ep = ep_arg or ep_env or (saved_ep if resume else "")
    if ep:
        llm["endpoint"] = ep
        LOGA.info("sLLM 주소: %s (%s)" % (ep, "--llm-endpoint" if ep_arg else ("환경변수 " + ENV_LLM_ENDPOINT) if ep_env else "처음 실행의 값"))
    allowed, want = max(1, int(llm["max_parallel"])), ctx.opts["max_parallel"]
    if want > allowed:
        LOGA.warn("--max-parallel %d 은(는) 서버 설정의 허용 동시 수 %d 을(를) 넘어 %d(으)로 낮춥니다. "
                  "올리려면 서버 설정의 llm.max_parallel 을 바꿉니다" % (want, allowed, allowed))
    llm["max_parallel"] = min(want, allowed) if want else allowed
    if ctx.opts["no_compile"]:
        ctx.cfg["compile"]["mode"] = "off"
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
    if resume:                                    # 요청문을 분석한 결과는 실행 폴더에 있다. 이어서 실행할 때 다시 싣는다
        state = ctx.store.get_meta("ask_state", None) or {}
        ctx.request["asks"] = list(state.get("asks") or [])
        have = {f.get("id") for f in ctx.request.get("features") or []}
        ctx.request.setdefault("features", []).extend(f for f in state.get("features") or [] if f.get("id") not in have)
        ctx.request["common"] = list(ctx.request.get("common") or []) + [c for c in state.get("common") or []
                                                                      if c not in (ctx.request.get("common") or [])]
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
        n_wait = st.one("SELECT COUNT(*) FROM issues WHERE status='pending'") or 0
        print("대기 이슈 : %d건%s" % (n_wait, ("  (이어서 보내기: vupdate run --resume \"%s\")" % run_dir) if n_wait else ""))
    finally:
        st.close()
    return EXIT_OK


def cmd_rules(args):
    """규칙 목록. --from-vs/--to-vs 를 주면 그 환경값에서 켜지는지와 꺼진 이유도 보여 준다.
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
    if getattr(args, "from_vs", None) or getattr(args, "to_vs", None):
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
    요청문 분석 요청([요청 원문])에는 의도 JSON 을, 배열 요청의 변수 인덱스 줄에는 요소 포인터로 바꾼 수정 블록을 돌려준다.
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
            if "-----BEGIN CODE-----" not in prompt and "[요청 원문]" in prompt:
                # 요청문 분석: [코드에서 확인된 이름]에 실린 이름을 찾을 대상으로 답한다. "주석" 이 든 요청에는 조건 의도를 더한다
                text = prompt.split("[요청 원문]\n", 1)[1].split("\n\n[코드에서 확인된 이름]", 1)[0]
                names = re.findall(r"(?m)^- ([A-Za-z_]\w*) \(", prompt.split("[코드에서 확인된 이름]", 1)[1].split("[할 일]", 1)[0])
                intents = [{"type": "modify", "instruction": text.split(". ")[0].strip(), "names": names, "words": [],
                            "construct": "any", "files": []}]
                if "주석" in text:
                    intents.append({"type": "constraint", "instruction": "주석의 문구는 바꾸지 않는다", "names": [], "words": [],
                                    "construct": "any", "files": []})
                srv.analyses.append(prompt)
                return self._send(200, {"choices": [{"message": {"role": "assistant", "content": json.dumps({"intents": intents}, ensure_ascii=False)},
                                                     "finish_reason": "stop"}]})
            code = prompt.split("-----BEGIN CODE-----\n", 1)[-1].split("\n-----END CODE-----", 1)[0]
            srv.prompts.append(prompt)
            al = [l for l in code.split("\n") if re.search(r"\bg_data\[[a-z]\w*\]", l)] \
                if re.search(r"번째 줄 \[[^\]]+\]: [^\n]*\bg_data\[[a-z]", prompt) else []
            wl = [l for l in code.split("\n") if "SetWindowLong(" in l]
            m = re.search(r"(?:^[ \t]*typedef\b[^\n]*\bu?int(?:8|16|32|64)_t[ \t]*;[ \t]*\n?)+", code, re.M)
            finish = "stop"
            if "FAILME" in code:
                content = "이 조각은 고칠 수 없습니다."        # 형식에 맞지 않는 응답(분할 취소 시험용)
            elif al:                                           # 배열 요청에서 맡은 변수 인덱스 접근: 요소 포인터로 본다
                content = "\n".join("<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE"
                                    % (l, re.sub(r"\bg_data\[([a-z]\w*)\]", r"((int*)&g_data)[\1]", l)) for l in al)
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
    srv.prompts, srv.rejected, srv.seen, srv.analyses = [], [], [], []
    srv.busy_left, srv.n_busy = busy_first, 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _selftest_ns(work, pkg, url, **over):
    """자체 시험용 실행 인자. 명령줄에서 준 것과 같은 모양이다."""
    ns = argparse.Namespace(package=pkg, request=None, from_vs="2008", to_vs="2022", cpp_standard=None,
                            server_config=None, rules=None, out=os.path.join(work, "runs"), dry_run=False,
                            resume=None, until=None, max_parallel=2, no_compile=False, llm_endpoint=url, compat_llm=True)
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _selftest_llm_defaults(ctx, **over):
    """자체 시험은 PC 의 config/server.json 값에 흔들리지 않도록 관련 설정을 고정한다."""
    ctx.cfg["llm"].update(server_prompt_reserve_tokens=0, retries=2, need_context_rounds=2, busy_retries=5,
                          calibrate_first=True, max_total_tokens=24000, max_output_tokens=3000)
    ctx.cfg["llm"].update(over)
    ctx.cfg["limits"].update(big_init_chars=2000, slice_context_lines=25, cluster_gap_lines=30, chunk_enabled=True,
                             chunk_ledger_chars=1500, chunk_head_lines=25, task_max_targets=0, task_max_lines=0, ref_decl=False)


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
    srv = _selftest_mock_server()
    ns = _selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], platform="x64")
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
    """0.5.0 에서 더한 것의 시험: .vcxproj, 예외 파일과 소스의 표식, 원본 쪽 언어 표준, 고유 매크로."""
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
    old = ["#include <utility>", "void f(int a, int b) {", "    std::pair<int,int> p1 = std::make_pair<int, int>(a, b);",
           "    std::pair<int,int> p2 = std::make_pair<int, int>(a, b); // vupdate:skip", "    // vupdate:skip-next R-T2-MAKE-PAIR",
           "    std::pair<int,int> p3 = std::make_pair<int, int>(a, b);", "}", ""]
    write_bytes(os.path.join(pkg, "Src", "Old.cpp"), "\r\n".join(old).encode("cp949"))
    write_bytes(os.path.join(pkg, "Legacy", "Keep.cpp"), "\r\n".join(old[:3] + ["}", ""]).encode("cp949"))
    exc = os.path.join(work, "exceptions.json")
    write_text(exc, json.dumps({"rules_off": ["R-T3-GETVERSIONEX"], "skip": [{"path": "Legacy/*", "rules": ["R-T2-MAKE-PAIR"]}]}))
    ctx = _make_ctx(_selftest_ns(work, pkg, "", exceptions=exc, disable_rule=["R-T3-OSVER-GLOBALS"]))
    _selftest_llm_defaults(ctx)
    try:
        run_pipeline(ctx, [s for s, _n in STAGES])
        tr1 = ctx.rule_by_id["R-T1-TR1-NAMESPACE"]
        e17 = dict(ctx.env, cpp_standard="c++17")
        std_ok = rule_state(tr1, e17)[0] and not rule_state(tr1, dict(e17, from_std="c++17"))[0]
    finally:
        ctx.store.close()
    out = os.path.join(ctx.run_dir, "out")

    def rd(*parts):
        p = os.path.join(out, *parts)
        return read_bytes(p).decode("cp949" if parts[0] == "changed" else "utf-8") if os.path.isfile(p) else ""

    old_c, report = rd("changed", "Src", "Old.cpp"), rd("report.md")
    off = {rid for rid, _t, _w in ctx.rules_off}
    check("예외: 끈 규칙, 제외한 폴더, 소스의 표식(vupdate:skip, skip-next)을 지킴",
          {"R-T3-GETVERSIONEX", "R-T3-OSVER-GLOBALS"} <= off and not os.path.isfile(os.path.join(out, "changed", "Legacy", "Keep.cpp"))
          and old_c.count("make_pair<int, int>") == 2 and "p1 = std::make_pair(a, b)" in old_c and "예외로 건너뛴 탐지" in report)
    check("원본 쪽 언어 표준: 원본도 c++17 이면 c++17 규칙을 끔", std_ok)


def _selftest_v090(work, check):
    """0.9.0 의 시험: 요청문(--ask)을 sLLM 이 의도별로 나누고, 도구가 그 결과로 대상을 찾아 의도별로 수정 요청을 보낸다.
    환경 호환성 항목은 --compat-llm 을 켰을 때만 sLLM 에 보낸다."""
    pkg = os.path.join(work, "V090_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    src = ["#include <windows.h>", "#include <stdio.h>", "void A(HWND h, void* p)", "{", "    char buf[80];", "    gets(buf);",
           "    SetWindowLong(h, GWL_USERDATA, (LONG)p);", "}", ""]
    write_bytes(os.path.join(pkg, "Src", "Ui.cpp"), "\r\n".join(src).encode("cp949"))
    srv = _selftest_mock_server()
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], compat_llm=False,
                                 ask=["SetWindowLong 을 SetWindowLongPtr 로 바꾸고 인덱스와 형 변환도 맞춘다. 주석의 문구는 바꾸지 않는다",
                                      "NoSuchName 호출을 모두 지운다"]))
    _selftest_llm_defaults(ctx)
    try:
        run_pipeline(ctx, [s for s, _n in STAGES])
        asks, common = ctx.request.get("asks") or [], ctx.request.get("common") or []
    finally:
        ctx.store.close()
        srv.shutdown()
    out = os.path.join(ctx.run_dir, "out")

    def rd(*parts):
        p = os.path.join(out, *parts)
        return read_bytes(p).decode("cp949" if parts[0] == "changed" else "utf-8") if os.path.isfile(p) else ""

    txt, report, manual, plan = rd("changed", "Src", "Ui.cpp"), rd("report.md"), rd("manual_review.md"), rd("ask_plan.json")
    check("요청문: 원문을 sLLM 에 보내 의도별로 나누고(수정 1, 조건 1), 코드에 있는 이름만 대상으로 삼음",
          len(srv.analyses) == 2 and len(asks) == 1 and asks[0]["targets"] == ["SetWindowLong"] and common == ["주석의 문구는 바꾸지 않는다"]
          and "ASK-1-1" in plan and "ASK-1-2" in plan)
    check("요청문: 찾은 줄을 의도의 요청문과 함께 다시 보내 고침(조건은 모든 수정 요청에 붙음)",
          "SetWindowLongPtr(h, GWLP_USERDATA, (LONG_PTR)p);" in txt and any("사용자 요청: SetWindowLong 을 SetWindowLongPtr 로 바꾸고" in p
          and "[공통 요청]" in p and "주석의 문구" in p for p in srv.prompts))
    check("요청문: 대상을 찾지 못한 요청은 이유와 함께 수동 검토로 남김", "요청의 대상을 찾지 못함" in manual and "ASK-2" in plan)
    check("환경 호환성: --compat-llm 이 꺼져 있으면 규칙의 sLLM 대상(gets)을 보내지 않고 수동 검토로 둠",
          "gets(buf);" in txt and "R-T3-GETS-REMOVED" in manual and "--compat-llm" in manual and "끔(기본)" in report)


def _selftest_v040(work, check):
    """0.4.0 에서 더한 것의 시험: 새 규칙, 공통 요청, 프로젝트 점검, 고치지 않은 지점의 처리, 서버 점검(llmcheck)."""
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
    ctx = _make_ctx(_selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], platform="x64", request=req))
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
    check("새 규칙: auto 저장 클래스 제거, WINVER 를 Windows 7 값으로, round 매크로 가드",
          code in (EXIT_OK, EXIT_REVIEW) and "    int n = 0;" in txt and "auto int" not in txt
          and "#define WINVER 0x0601" in txt
          and "#if !defined(_MSC_VER) || _MSC_VER < 1800\r\n#define round(x) floor((x) + 0.5)\r\n#endif" in txt)
    check("설정 흡수: 키워드 매크로는 코드를 그대로 두고 설정 항목으로 냄",
          "#define for if (0) {} else for" in txt and "_ALLOW_KEYWORD_MACROS" in settings)
    check("공통 요청이 모든 수정 요청과 보고서에 들어감",
          srv.prompts and all("[공통 요청]" in p and "주석의 문구는 바꾸지 않는다" in p for p in srv.prompts)
          and "주석의 문구는 바꾸지 않는다" in report)
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


def cmd_selftest(args):
    work = os.path.abspath(args.dir) if args.dir else tempfile.mkdtemp(prefix="vupdate_selftest_")
    pkg = os.path.join(work, "SAMPLE_PKG")
    shutil.rmtree(pkg, ignore_errors=True)
    table = _selftest_make_package(pkg)
    req = os.path.join(work, "request.json")
    write_text(req, json.dumps({"features": [{"id": "REQ-001", "type": "array_to_struct", "target": "g_data",
                                              "mapping": {"IDX_TEMP": "temp", "IDX_COUNT": "count"}}]}, ensure_ascii=False))
    srv = _selftest_mock_server()
    ns = _selftest_ns(work, pkg, "http://127.0.0.1:%d/v1" % srv.server_address[1], request=req, deprecated="fix")
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
        check("배열 선언을 구조체 선언으로 바꾸고(정의는 헤더에) 변수 인덱스는 sLLM 이 고침",
              "((int*)&g_data)[i] = 0;" in txt and "G_DATA_T g_data;" in txt and "int g_data[10];" not in txt and "    int temp;" in hdr_txt
              and "    int reserved_9;" in hdr_txt and "extern G_DATA_T g_data;" in hdr_txt)
        check("큰 상수 배열이 바이트 단위로 동일", table.encode("ascii") in raw)
    check("sLLM 수정 반영(Common.h typedef 가드)", os.path.isfile(hp) and
          b"#if !defined(_MSC_VER) || _MSC_VER < 1600\ntypedef unsigned char uint8_t;\ntypedef unsigned short uint16_t;\n#endif" in read_bytes(hp))
    for name in ("report.md", "manifest.csv", "manual_review.md", "project_settings.md", "build_guide.md", "preflight.md"):
        check("산출물 %s" % name, os.path.isfile(os.path.join(out, name)))
    check("전체 patch 생성", os.path.isfile(os.path.join(out, "diff", "all.patch")))
    mr = open(os.path.join(out, "manual_review.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "manual_review.md")) else ""
    check("수동 검토에 좁히기 변환 포함", "R-T3-NARROWING-CHAR" in mr)
    pf = open(os.path.join(out, "preflight.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "preflight.md")) else ""
    check("환경 점검에 외부 헤더 후보(ExtVendor.h) 표시", "ExtVendor.h" in pf)
    _selftest_chunk(work, check)
    _selftest_rules(work, pkg, check)
    _selftest_limit(work, check)
    _selftest_busy_trunc(work, check)
    _selftest_v040(work, check)
    _selftest_v050(work, check)
    _selftest_v090(work, check)
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
    r.add_argument("--from-vs", dest="from_vs", help="원본 VS 제품 연도(예: 2008)")
    r.add_argument("--to-vs", dest="to_vs", help="대상 VS 제품 연도(예: 2022)")
    r.add_argument("--cpp-standard", dest="cpp_standard", help="C++ 언어 표준(c++14, c++17, c++20). 기본 c++14")
    r.add_argument("--platform", help="대상 플랫폼(keep, x86, x64). 기본 keep. x64 면 64비트 규칙이 켜진다")
    r.add_argument("--conformance", help="준수 모드 /permissive- (off, on). 기본 off")
    r.add_argument("--charset", help="문자셋(keep, mbcs, unicode). 기본 keep")
    r.add_argument("--deprecated", help="사용 중단 경고 처리(suppress: 설정으로 끈다, fix: 코드를 고친다). 기본 suppress")
    r.add_argument("--from-os", dest="from_os", metavar="\"이름 판\"", help="원본 OS(선택). 예: \"Windows 7\"")
    r.add_argument("--to-os", dest="to_os", metavar="\"이름 판\"",
                   help="대상 OS(선택). 예: \"Windows 11\". 주면 sLLM 요청의 [환경]과 보고서에 들어가고, 낮은 WINVER 를 이 OS 의 값으로 올린다")
    r.add_argument("--from-db", dest="from_db", metavar="\"이름 판\"", help="원본 DB(선택). 예: \"Oracle 11g\"")
    r.add_argument("--to-db", dest="to_db", metavar="\"이름 판\"",
                   help="대상 DB(선택). 예: \"Oracle 19c\". 주면 sLLM 요청의 [환경]과 보고서에 들어간다. 규칙의 조건(when 의 db)으로도 쓸 수 있다")
    r.add_argument("--request", help="요청사항(파일 경로 또는 config/requests 안의 이름)")
    r.add_argument("--ask", action="append", metavar="\"요청문\"",
                   help="요청문. 정해진 형식 없이 하려는 일을 글로 적는다(예: \"WriteLog 호출에 줄 번호 인자를 추가하고 g_data 배열을 구조체로 바꾼다\"). "
                        "글을 그대로 sLLM 에 보내 의도별로 나누고, 그 결과로 코드에서 대상을 찾아 의도별로 수정 요청을 보낸다. "
                        "분석 결과는 out/ask_plan.json 에 남는다. 여러 번 줄 수 있다(각각 따로 분석한다)")
    r.add_argument("--ask-file", dest="ask_file", help="요청문 파일. 파일 전체를 요청 하나로 본다(# 으로 시작하는 줄은 뺀다)")
    r.add_argument("--from-cpp-standard", dest="from_cpp_standard",
                   help="원본 쪽 C++ 언어 표준(예: c++17). 주면 원본에서 이미 겪은 언어 표준 규칙을 끈다")
    r.add_argument("--exceptions", help="예외 파일(끌 규칙, 제외할 파일과 줄, 고유 매크로). 예: config/EXAMPLE_exceptions.json")
    r.add_argument("--disable-rule", dest="disable_rule", action="append", metavar="규칙ID",
                   help="이 규칙을 끈다. 여러 번 주거나 쉼표로 잇는다")
    r.add_argument("--only", action="append", metavar="패턴",
                   help="처리할 파일 범위. 패키지 기준 경로의 와일드카드. 여러 번 또는 쉼표로. 예: Src/Net/*")
    r.add_argument("--exclude", action="append", metavar="패턴", help="처리에서 뺄 파일 범위. 예: ThirdParty/*")
    r.add_argument("--server-config", dest="server_config", help="서버 설정 파일. 기본 config/server.json")
    r.add_argument("--rules", help="추가 규칙 파일. 기본 config/rules.json")
    r.add_argument("--llm-endpoint", dest="llm_endpoint",
                   help="sLLM 주소. 없으면 환경변수 VUPDATE_LLM_ENDPOINT, 그다음 서버 설정의 값")
    r.add_argument("--max-parallel", dest="max_parallel", type=int,
                   help="sLLM 동시 요청 수. 서버 설정의 허용 동시 수(llm.max_parallel) 이하로만 적용된다")
    r.add_argument("--max-calls", dest="max_calls", type=int,
                   help="이번 실행에서 sLLM 에 보낼 호출 수의 상한. 다 쓰면 남은 작업을 대기로 두고 끝낸다(종료 코드 4). --resume 으로 이어서 보낸다")
    r.add_argument("--out", help="실행 폴더를 만들 위치. 기본 ./runs")
    r.add_argument("--dry-run", dest="dry_run", action="store_true", help="탐지까지만 수행(S0~S2)하고 보고")
    r.add_argument("--until", help="지정한 단계까지만 수행(예: S4)")
    r.add_argument("--resume", help="중단된 실행 폴더를 이어서 수행")
    r.add_argument("--no-compile", dest="no_compile", action="store_true", help="컴파일 검사를 하지 않음")
    r.add_argument("--no-llm", dest="no_llm", action="store_true",
                   help="sLLM 을 쓰지 않는다. 규칙 수정만 하고 sLLM 대상은 수동 검토로 보낸다(요청문도 분석하지 못한다)")
    r.add_argument("--compat-llm", dest="compat_llm", action="store_true",
                   help="환경 호환성 항목도 sLLM 에 보낸다(기본은 보내지 않는다). 규칙이 sLLM 대상으로 분류한 것과, "
                        "설정으로 흡수하던 보안 CRT·Winsock 함수의 사용 줄을 보내 수정을 시도한다. 호출이 크게 늘 수 있으니 --max-calls 와 함께 쓴다")
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
    u.add_argument("--from-vs", dest="from_vs", help="원본 VS 제품 연도")
    u.add_argument("--to-vs", dest="to_vs", help="대상 VS 제품 연도")
    u.add_argument("--cpp-standard", dest="cpp_standard")
    u.add_argument("--from-cpp-standard", dest="from_cpp_standard")
    u.add_argument("--platform")
    u.add_argument("--conformance")
    u.add_argument("--charset")
    u.add_argument("--deprecated")
    u.add_argument("--from-os", dest="from_os")
    u.add_argument("--to-os", dest="to_os")
    u.add_argument("--from-db", dest="from_db")
    u.add_argument("--to-db", dest="to_db")
    u.add_argument("--coverage", action="store_true", help="알려진 변경 항목과 처리 방식의 표를 낸다")
    u.add_argument("--test", action="store_true", help="규칙에 붙은 시험 예(잡아야 하는 코드, 잡지 말아야 하는 코드)를 돌린다")
    u.set_defaults(func=cmd_rules)

    c = sub.add_parser("llmcheck", help="실제 sLLM 서버가 이 도구가 기대하는 대로 답하는지 점검한다")
    c.add_argument("--llm-endpoint", dest="llm_endpoint", help="sLLM 주소. 없으면 환경변수 VUPDATE_LLM_ENDPOINT, 서버 설정")
    c.add_argument("--server-config", dest="server_config")
    c.add_argument("--out", help="결과 폴더를 만들 위치. 기본 ./runs")
    c.add_argument("--skip-over-limit", dest="skip_over_limit", action="store_true",
                   help="한도를 일부러 넘기는 요청을 보내지 않는다")
    c.set_defaults(func=cmd_llmcheck)


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
