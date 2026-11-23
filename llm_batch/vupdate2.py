#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
vupdate.py  -  C++ 버전 업데이트 대응 자동화 (단일 파일 실행본)
==============================================================================
버전 : 0.2.0 (0.1.0 최초 개발본에 분할 순차 처리를 더함)
목적 : 레거시 C++ 패키지를 새 Visual Studio 환경(예: 2008 -> 2022)에 맞게
       고치는 작업을 한 명령으로 수행한다. 규칙으로 고칠 수 있는 것은 규칙으로,
       규칙으로 못 고치는 것만 sLLM에 맡기고, 결과를 별도 폴더에 낸다.
배포 : 표준 라이브러리만 사용한다(폐쇄망 배포용). 외부 패키지 설치가 필요 없다.
       exe 생성: pyinstaller --onefile --name vupdate vupdate.py
문서 : 04_개발구조.md (구조), 03_확인사항.md (환경 점검)

[사용 예]
  vupdate run --package D:\\src\\PKG_A --from-vs 2008 --to-vs 2022
  vupdate run --package D:\\src\\PKG_A --profile vs2008_to_vs2022 --request PKG_A
  vupdate preflight --package D:\\src\\PKG_A      (환경 점검만)
  vupdate selftest                               (설치 확인용 자체 시험)

[이 파일의 구획]  수정할 때는 해당 구획만 고친다. 구획 사이의 호출은
                  실행기(M10)가 넘겨주는 ctx 객체와 저장소(M03)만 거친다.
  M01 공통      상수, 로그([모듈][함수명] 접두어), 작은 유틸
  M02 설정      버전 대응표, 프로필/요청사항/서버 설정 읽기와 검증
  M03 저장소    실행 상태 DB(SQLite 파일 하나)
  M04 어휘분석  인코딩 판별, 주석/문자열 마스킹, 함수 경계, 초기화 목록
  M05 규칙      규칙 카탈로그(T0~T3)와 탐지 함수
  M06 sLLM연결  토큰 추정, HTTP 호출, 응답 캐시
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
  M11 명령줄    run / preflight / status / rules / selftest

[로그 규칙]  모든 로그 줄은  "시각 수준 [모듈][함수명] 내용"  형식이다.
             실행 폴더의 logs/run.log 에 전부 남는다. 오류가 나면 이 접두어로
             어느 구획의 어느 함수인지 바로 찾을 수 있다.

[v0.2.0 에서 구현하지 않은 것]  (04_개발구조.md 의 구현 범위 표 참조)
  - sLLM 호출의 MCP 서버 경유(05_기능정의.md 10절). 지금은 설정의 주소로 직접 호출한다
  - 프로젝트 변환과 전체 빌드(환경 등급 '다')
  - .rc/.idl/.def 의 규칙 탐지
  - 실제 모델 토크나이저(서버 tokenize 주소가 없으면 추정치를 쓴다)
"""

import argparse
import bisect
import csv
import difflib
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

# ============================================================================
# [M01 공통]  상수, 로그, 작은 유틸
# 목적: 모든 구획이 같이 쓰는 최소한의 도구. 로그 접두어 규칙을 여기서 보장한다.
# ============================================================================
APP = "vupdate"
VERSION = "0.2.0"

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
        "max_parallel": 4,
        "timeout_sec": 300,
        "retries": 2,                # 수정 블록 기각 시 재요청 횟수
        "need_context_rounds": 2,
        "tokenize_endpoint": "",     # 서버가 토큰 수 계산 주소를 제공하면 적는다(없으면 추정)
    },
    "limits": {
        "big_init_chars": 2000,      # 이보다 긴 초기화 목록은 sLLM에 보내지 않고 자리표시자로 바꾼다
        "slice_context_lines": 25,   # 수정 지점 앞뒤로 같이 보낼 줄 수
        "cluster_gap_lines": 30,     # 이 줄 수 안의 수정 지점은 한 작업으로 묶는다
        "chunk_enabled": True,       # 한도를 넘는 구간을 나눠 순서대로 처리(분할 순차). False 면 수동 검토로 보낸다
        "chunk_ledger_chars": 1500,  # 뒤 조각에 넘기는 "앞 조각에서 바꾼 내용"의 최대 글자 수
        "chunk_head_lines": 25,      # 뒤 조각에 참고로 붙이는 함수 앞부분 줄 수
        "compile_timeout_sec": 180,
        "compile_parallel": 4,
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
                                 % (key, d.get(key), ", ".join(allowed)))
        return v

    env = {
        "from_vs": from_vs, "to_vs": to_vs,
        "from_toolset": VERSION_MAP[from_vs]["toolset"], "to_toolset": toolset,
        "cpp_standard": std,
        "conformance": pick(t, "conformance", ["off", "on"], "off"),
        "platform": pick(t, "platform", ["keep", "x86", "x64"], "keep"),
        "charset": pick(t, "charset", ["keep", "mbcs", "unicode"], "keep"),
        "policy": {"deprecated_warnings": pick(pol, "deprecated_warnings", ["suppress", "fix"], "suppress")},
    }
    LOGC.info("환경값 확정: VS %d(%s) -> VS %d(%s), 언어 표준 %s, 준수 모드 %s, 플랫폼 %s, 문자셋 %s, "
              "deprecated 경고 %s" % (from_vs, env["from_toolset"], to_vs, toolset, std or "(선택 없음)",
                                    env["conformance"], env["platform"], env["charset"],
                                    env["policy"]["deprecated_warnings"]))
    return env


def env_summary_lines(env):
    return [
        "VS 제품: %d -> %d" % (env["from_vs"], env["to_vs"]),
        "툴셋: %s -> %s" % (env["from_toolset"], env["to_toolset"]),
        "C++ 언어 표준: %s" % (env["cpp_standard"] or "(선택 없음)"),
        "준수 모드(/permissive-): %s" % env["conformance"],
        "플랫폼: %s" % env["platform"],
        "문자셋: %s" % env["charset"],
        "deprecated 경고 처리: %s" % env["policy"]["deprecated_warnings"],
    ]


def load_request(args):
    """요청사항(기능변경) 읽기. 없으면 빈 요청."""
    if not getattr(args, "request", None):
        return {"features": []}
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
    LOGC.info("요청사항 읽음: %s (%d건)" % (path, len(feats)))
    return {"features": feats}


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
    prompt_file TEXT, response_file TEXT, est_in INTEGER, ms INTEGER, ok INTEGER, note TEXT);
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
        for col in ("grp", "part", "parts"):      # 0.1.0 으로 만든 실행 폴더를 이어서 쓸 때를 위한 열 추가
            try:
                self.con.execute("ALTER TABLE tasks ADD COLUMN %s INTEGER" % col)
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

SOURCE_EXT = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl"}
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
        d.funcs, d.inits, d.parse_ok = analyze_structure(d.struct)
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
# when: 규칙이 켜지는 조건. min_std(언어 표준 이상), platform, conformance,
#       deprecated(suppress/fix). 비우면 항상 켜진다(요구사항 B10).
# 규칙은 다시 실행해도 같은 결과여야 한다(이미 고친 곳을 또 잡지 않는다).
# 수정: 규칙 추가는 RULES 에 항목을 더한다. 정규식은 code(주석/문자열 가린 본문)에 적용된다.
# ============================================================================
LOGR = Log("M05.규칙")

_NONSTDC = r"strdup|stricmp|strnicmp|strcmpi|memicmp|itoa|ltoa|ultoa|strlwr|strupr|getch|getche|kbhit|putch|wcsdup|wcsicmp|wcsnicmp"
_STD17_REMOVED = r"auto_ptr|bind1st|bind2nd|ptr_fun|mem_fun|mem_fun_ref|random_shuffle|unary_function|binary_function"

RULES = [
    {"id": "R-T0-CRT-SECURE", "tier": "T0", "category": "CRT", "collapse": True,
     "title": "보안 CRT 경고 대상 함수 사용(C4996)",
     "pattern": r"(?<![\w.>:])(?:strcpy|strcat|sprintf|vsprintf|sscanf|scanf|fopen|strncpy|strncat|strtok|gets|"
                r"localtime|gmtime|ctime|asctime|getenv|wcscpy|wcscat|swscanf|_snprintf|_vsnprintf|_splitpath|"
                r"_makepath|mbstowcs|wcstombs)\s*\(",
     "setting": "전처리기 정의 추가: _CRT_SECURE_NO_WARNINGS"},
    {"id": "R-T0-CRT-NONSTDC", "tier": "T0", "category": "CRT", "collapse": True,
     "title": "POSIX 이름 함수 사용(C4996)", "when": {"deprecated": "suppress"},
     "pattern": r"(?<![\w.>:])(?:%s)\s*\(" % _NONSTDC,
     "setting": "전처리기 정의 추가: _CRT_NONSTDC_NO_DEPRECATE"},
    {"id": "R-T1-CRT-NONSTDC", "tier": "T1", "category": "CRT",
     "title": "POSIX 이름 함수를 밑줄 이름으로 치환", "when": {"deprecated": "fix"},
     "pattern": r"(?<![\w.>:])(%s)\s*\(" % _NONSTDC, "group": 1, "action": {"type": "prefix", "text": "_"}},
    {"id": "R-T0-WINSOCK-DEPR", "tier": "T0", "category": "Windows API", "collapse": True,
     "title": "사용 중단된 Winsock 함수 사용(C4996)",
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
     "title": "문자열 리터럴 뒤에 붙은 매크로 사이에 공백 삽입", "detector": "literal_ident"},
    {"id": "R-T1-SNPRINTF-MACRO", "tier": "T1", "category": "CRT",
     "title": "snprintf 매크로 정의를 구 컴파일러 전용으로 제한", "detector": "snprintf_macro"},
    {"id": "R-T1-TR1-NAMESPACE", "tier": "T1", "category": "C++ 표준",
     "title": "std::tr1:: 를 std:: 로 치환", "when": {"min_std": "c++17"},
     "pattern": r"\bstd\s*::\s*tr1\s*::", "action": {"type": "replace", "text": "std::"}},
    {"id": "R-T2-MAKE-PAIR", "tier": "T2", "category": "C++ 표준", "review": True,
     "title": "make_pair 의 명시적 템플릿 인자 제거",
     "pattern": r"\bmake_pair\s*(<[^;(){}]*?>)\s*\(", "group": 1, "action": {"type": "delete"}},
    {"id": "R-T3-NARROWING-CHAR", "tier": "T3", "category": "C++ 표준", "llm": False,
     "title": "char 배열 초기값의 좁히기 변환(C2397/C4838)", "detector": "narrowing_char_init",
     "guidance": "요소 타입을 unsigned char 로 바꾸거나 값을 캐스팅해야 한다. 타입을 바꾸면 사용처에 "
                 "영향이 있으므로 수동 검토 대상이다."},
    {"id": "R-T3-STDINT-TYPEDEF", "tier": "T3", "category": "C++ 표준", "llm": True,
     "title": "stdint 타입 이름의 자체 typedef(C2371 재정의)", "detector": "stdint_typedef",
     "guidance": "VS2010 이후 <stdint.h> 가 같은 이름을 정의하므로 재정의 오류가 날 수 있다. 해당 typedef 줄을 "
                 "바로 위의 '#if !defined(_MSC_VER) || _MSC_VER < 1600' 줄과 바로 아래의 '#endif' 줄로 감싼다. "
                 "typedef 줄 자체는 바꾸지 않는다. 연속한 typedef 줄은 한 번에 감싼다."},
    {"id": "R-T3-FILE-INTERNALS", "tier": "T3", "category": "CRT", "llm": False,
     "title": "FILE 구조체 내부 필드 또는 _iob 직접 사용",
     "pattern": r"->\s*_(?:ptr|cnt|base|flag|file|bufsiz|charbuf)\b|\b_iob\b|\b__iob_func\b",
     "guidance": "VS2015 이후 FILE 내부는 공개되지 않는다. 표준 함수로 다시 작성해야 한다."},
    {"id": "R-T3-HASH-MAP", "tier": "T3", "category": "C++ 표준", "llm": False,
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
]

_RX_CACHE = {}


def _rx(pattern):
    if pattern not in _RX_CACHE:
        _RX_CACHE[pattern] = re.compile(pattern, re.M)
    return _RX_CACHE[pattern]


def std_rank(std):
    return STD_ORDER.index(std) if std in STD_ORDER else -1


def rule_active(rule, env):
    w = rule.get("when") or {}
    if "min_std" in w and std_rank(env["cpp_standard"]) < std_rank(w["min_std"]):
        return False
    if "platform" in w and env["platform"] != w["platform"]:
        return False
    if "conformance" in w and env["conformance"] != w["conformance"]:
        return False
    if "deprecated" in w and env["policy"]["deprecated_warnings"] != w["deprecated"]:
        return False
    return True


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
            if r["id"] in ids:
                rules = [x for x in rules if x["id"] != r["id"]]
            rules.append(r)
        LOGR.info("외부 규칙 %d건 읽음: %s" % (len(extra), cand))
    elif extra_path:
        raise UserInputError("규칙 파일이 없습니다: %s" % extra_path)
    return rules


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


def _det_literal_ident(rule, doc):
    hits = []
    n = len(doc.code)
    for s, e in doc.strings:
        if e < n and (doc.code[e].isalpha() or doc.code[e] == "_"):
            hits.append({"start": e - 1, "end": e, "after": '" ',
                         "detail": doc.text[max(s, e - 20):min(n, e + 20)].strip()})
    return hits


_SNPRINTF_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+snprintf[ \t]+_snprintf[ \t]*(?=\r?$)", re.M)


def _det_snprintf_macro(rule, doc):
    hits = []
    for m in _SNPRINTF_RE.finditer(doc.code):
        if _is_msc_guard(_prev_line(doc, m.start())):
            continue                                   # 이미 감싼 곳
        before = doc.text[m.start():m.end()]
        hits.append({"start": m.start(), "end": m.end(), "detail": before.strip(),
                     "after": "#if defined(_MSC_VER) && _MSC_VER < 1900\n" + before + "\n#endif"})
    return hits


_STDINT_RE = re.compile(r"^[ \t]*typedef\b[^;{}()]*?\b(u?int(?:8|16|32|64)_t)[ \t]*;", re.M)


def _det_stdint_typedef(rule, doc):
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


def _det_narrowing_char_init(rule, doc):
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


_DETECTORS = {"literal_ident": _det_literal_ident, "snprintf_macro": _det_snprintf_macro,
              "stdint_typedef": _det_stdint_typedef, "narrowing_char_init": _det_narrowing_char_init}


def rule_hits(rule, doc):
    """규칙 하나를 파일 하나에 적용한 결과. 각 항목: start, end, detail, (자동 치환이면) after"""
    det = rule.get("detector")
    if det:
        return _DETECTORS[det](rule, doc)
    grp, act, hits = rule.get("group", 0), rule.get("action"), []
    for m in _rx(rule["pattern"]).finditer(doc.code):
        s, e = m.span(grp)
        h = {"start": s, "end": e, "detail": doc.text[m.start():m.end()].strip()[:120]}
        if act:
            h["after"] = _apply_action(act, doc.text[s:e])
        hits.append(h)
    return hits


# ============================================================================
# [M06 sLLM연결]  토큰 추정, HTTP 호출
# 목적: sLLM 서버와의 통신을 한 곳에 모은다. 주소, 한도, 동시 수는 서버 설정에서 읽는다.
# 전제: OpenAI 호환 /chat/completions 규격. 다르면 llm_chat() 만 고치면 된다.
# 주의: 토큰 수는 추정치다(서버의 tokenize 주소를 설정하면 실제 값을 쓴다).
#       추정이 빗나갈 수 있으므로 한도에 여유를 둔다.
# ============================================================================
LOGL = Log("M06.sLLM연결")
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def est_tokens(text):
    """토큰 수 추정. 영문/기호는 2.8자당 1토큰, 한글 등은 1자당 1.2토큰으로 넉넉하게 잡는다."""
    n_ascii = len(text.encode("ascii", "ignore"))
    return int(n_ascii / 2.8 + (len(text) - n_ascii) * 1.2) + 8


def count_tokens(llm_cfg, text):
    url = llm_cfg.get("tokenize_endpoint")
    if url:
        try:
            body = json.dumps({"model": llm_cfg["model"], "prompt": text}).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
            with _NO_PROXY_OPENER.open(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
            if isinstance(data.get("count"), int):
                return data["count"]
            if isinstance(data.get("tokens"), list):
                return len(data["tokens"])
        except Exception as e:
            LOGL.warn("토큰 수 계산 주소 호출 실패. 추정치를 씁니다: %s" % e)
    return est_tokens(text)


def llm_chat(llm_cfg, system, user):
    """sLLM 한 번 호출. 돌려주는 값: (성공 여부, 응답 본문 또는 오류 설명, 소요 ms)"""
    url = llm_cfg["endpoint"].rstrip("/") + "/chat/completions"
    if llm_cfg.get("use_system_role", True):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    else:
        messages = [{"role": "user", "content": system + "\n\n" + user}]
    body = json.dumps({"model": llm_cfg["model"], "messages": messages, "temperature": 0,
                       "max_tokens": llm_cfg["max_output_tokens"]}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if llm_cfg.get("api_key"):
        headers["Authorization"] = "Bearer " + llm_cfg["api_key"]
    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with _NO_PROXY_OPENER.open(req, timeout=llm_cfg["timeout_sec"]) as r:
            data = json.loads(r.read().decode("utf-8"))
        content = (data["choices"][0]["message"].get("content") or "")
        content = _THINK_RE.sub("", content).strip()
        return True, content, int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        return False, "HTTP %s %s %s" % (e.code, e.reason, detail), int((time.time() - t0) * 1000)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e), int((time.time() - t0) * 1000)


# ============================================================================
# [M07 컴파일]  Visual Studio 찾기, cl /Zs 호출
# 목적: 03_확인사항.md 의 4.1~4.4(컴파일러, SDK, MFC, 동작 확인)를 자동으로 점검하고,
#       가능하면 파일 단위 컴파일 검사(링크 없음)를 수행한다.
# 주의: 이 구획은 Windows 에서만 동작한다. v0.2.0 은 Windows 실기에서 검증하지 못했다.
#       문제가 생기면 로그의 [M07.컴파일] 줄을 확인한다.
# ============================================================================
LOGB = Log("M07.컴파일")
_DIAG_RE = re.compile(r"^(.*?)\((\d+)(?:,\d+)?\)\s*:\s*(fatal error|error|warning)\s+([A-Z]+\d+)\s*:\s*(.*)$")


def find_tools():
    """컴파일러, Windows SDK, MFC 의 설치 여부를 파일 존재로 확인한다."""
    info = {"platform": sys.platform, "cl_all": [], "cl_modern": "", "cl_2008": "", "sdk_math_h": "",
            "mfc_afxwin_h": "", "vs_root": "", "vsdevcmd": "", "cl_in_path": shutil.which("cl") or ""}
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
    info["sdk_math_h"] = sorted(sdk)[-1] if sdk else ""
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
    _VS_ENV[arch] = env
    return env


def run_cl_syntax(tools, src_abs, cwd, flags, timeout):
    """cl /Zs 로 파일 하나를 검사한다. 돌려주는 값: (실행됐는지, 진단 목록, 원문 출력)"""
    env = get_vs_env(tools)
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
               "use_mfc": attrs.get("UseOfMFC", ""), "includes": [], "defines": [], "runtime": "", "libs": []}
        for tool in re.findall(r"<Tool\b([^>]*)>", block, re.S):
            ta = dict(_ATTR_RE.findall(tool))
            if ta.get("Name") == "VCCLCompilerTool":
                cfg["includes"] = [x.strip() for x in re.split(r"[;,]", ta.get("AdditionalIncludeDirectories", "")) if x.strip()]
                cfg["defines"] = [x.strip() for x in ta.get("PreprocessorDefinitions", "").split(";") if x.strip()]
                cfg["runtime"] = ta.get("RuntimeLibrary", "")
            elif ta.get("Name") in ("VCLinkerTool", "VCLibrarianTool"):
                cfg["libs"] += [x for x in ta.get("AdditionalDependencies", "").split() if x]
        configs.append(cfg)
    files = [norm_rel(f).lstrip("./") for f in _FILE_RE.findall(text)]
    return {"configs": configs, "files": files}


def pick_config(proj):
    """검사에 쓸 구성 하나를 고른다(Release|Win32 우선)."""
    cfgs = proj.get("configs") or []
    for want in ("Release|Win32", "Debug|Win32"):
        for c in cfgs:
            if c["name"] == want:
                return c
    return cfgs[0] if cfgs else None


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
    ctx.tools = find_tools()
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
        dirs = [os.path.join(inc_root, d) for d in ("ucrt", "um", "shared")]
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
         "| 4.2 Windows SDK(math.h) | %s | %s |" % (yn(t.get("sdk_math_h")), md_escape(t.get("sdk_math_h") or "-")),
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
    L += ["", "v0.2.0 은 환경 등급 '다'(프로젝트 변환과 전체 빌드)를 자동화하지 않는다. "
          "전체 빌드는 out/build_guide.md 의 절차로 직접 수행한다.", ""]
    write_text(path, "\n".join(L))
    LOG1.info("환경 점검 보고서 작성: %s" % path)


def s1_inventory(ctx):
    st = ctx.store
    for tbl in ("functions", "big_inits", "includes"):
        st.x("DELETE FROM %s" % tbl)
    big = ctx.cfg["limits"]["big_init_chars"]
    projects, n_src, n_bad, n_ro, n_fn, n_big, n_parse_fail = {}, 0, 0, 0, 0, 0, 0
    for r in st.q("SELECT path, kind FROM files WHERE kind IN ('source','project','text') ORDER BY path"):
        rel, kind = r["path"], r["kind"]
        data = read_bytes(ctx.snap(rel))
        if kind != "source":
            d = make_doc(rel, data, analyze=False)
            st.x("UPDATE files SET encoding=?, bom=?, eol=?, roundtrip=?, n_lines=?, status=?, note=? WHERE path=?",
                 (d.label, 1 if d.bom else 0, d.eol, int(d.roundtrip), d.n_lines if d.ok else 0, "탐지제외",
                  "v0.2.0 은 이 종류의 파일을 규칙으로 탐지하지 않는다" if d.ok else d.note, rel))
            if d.ok and rel.lower().endswith(".vcproj"):
                try:
                    projects[rel] = parse_vcproj(d.text)
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
    st.commit()
    LOG1.info("소스 %d개: 인코딩 판별 불가 %d, 읽기전용 %d, 괄호 짝 분석 실패 %d" % (n_src, n_bad, n_ro, n_parse_fail))
    LOG1.info("함수 %d개, 큰 초기화 목록 %d개, 프로젝트 파일 %d개" % (n_fn, n_big, len(projects)))
    write_preflight_report(ctx, ctx.p("out", "preflight.md"))


# ============================================================================
# [S2 탐지]  규칙 스캔
# 목적: 환경값에 맞는 규칙을 전체 소스에 적용해 이슈를 만든다. sLLM 을 쓰지 않는다.
# 산출: issues
# ============================================================================
LOG2 = Log("S2.탐지")


def s2_detect(ctx):
    st = ctx.store
    st.x("DELETE FROM issues")
    counts = {}
    files = st.q("SELECT path, status FROM files WHERE kind='source' AND status IN ('대상','읽기전용') ORDER BY path")
    LOG2.info("규칙 %d개를 소스 %d개에 적용" % (len(ctx.rules), len(files)))
    for r in files:
        rel, editable = r["path"], r["status"] == "대상"
        d = get_doc(ctx, rel)
        for rule in ctx.rules:
            try:
                hits = rule_hits(rule, d)
            except Exception as e:
                LOG2.error("규칙 %s 가 %s 에서 실패. 이 규칙은 이 파일에서 건너뜁니다: %s" % (rule["id"], rel, e))
                continue
            if not hits:
                continue
            counts[rule["id"]] = counts.get(rule["id"], 0) + len(hits)
            tier = rule["tier"]
            if rule.get("collapse"):
                h = hits[0]
                st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rel, h["start"], h["end"], d.line_of(h["start"]), "rule", rule["id"], tier, 0, rule["title"],
                      "%d곳(첫 위치 %d줄)" % (len(hits), d.line_of(h["start"])), 0, "open", ""))
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
# v0.2.0: 구조체 정의 생성과 파일 간 변경 계약의 자동 전파는 하지 않는다.
# ============================================================================
LOG5 = Log("S5.영향분석")
_NOT_TYPE_WORDS = {"return", "else", "case", "delete", "new", "sizeof", "throw", "goto"}


def s5_impact(ctx):
    st = ctx.store
    st.x("DELETE FROM issues WHERE source='request'")
    st.x("DELETE FROM edits WHERE origin='request'")
    files = [r["path"] for r in st.q("SELECT path FROM files WHERE kind='source' AND status='대상' ORDER BY path")]
    for ft in ctx.request.get("features", []):
        if ft["type"] != "array_to_struct":
            LOG5.warn("요청 %s: 유형 %s 은(는) v0.2.0 에서 자동 처리하지 않습니다. 수동 검토 목록에 올립니다"
                      % (ft["id"], ft["type"]))
            st.x("INSERT INTO issues(path,start,end,line,source,rule_id,tier,llm,title,detail,review,status,note) "
                 "VALUES('',0,0,0,'request',?,'T3',0,?,?,0,'manual','v0.2.0 미지원 요청 유형')",
                 (ft["id"], "요청사항 %s" % ft["type"], json.dumps(ft, ensure_ascii=False)[:300]))
            continue
        target, mapping = ft["target"], ft["mapping"]
        rx = re.compile(r"\b%s\s*\[\s*([A-Za-z_]\w*|\d*)\s*\]" % re.escape(target))
        n_fix = n_var = n_decl = n_llm = 0
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
                    tier, status, note, after = "T3", "manual", "배열 선언. 구조체 정의로 교체 필요(변경 계약)", None
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
        LOG5.info("요청 %s (배열 %s): 필드 치환 %d곳, 변수 인덱스 %d곳(수동) %d곳(sLLM), 선언 %d곳(수동)"
                  % (ft["id"], target, n_fix, n_var, n_llm, n_decl))
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
    for r in st.q("SELECT path FROM files WHERE kind='source' AND status IN ('대상','읽기전용') ORDER BY path"):
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
    for ft in ctx.request.get("features", []):
        if ft.get("id") == rid and ft.get("type") == "array_to_struct":
            pairs = ", ".join("%s[%s] -> %s.%s" % (ft["target"], k, ft["target"], v)
                              for k, v in list(ft["mapping"].items())[:40])
            return ("배열 %s 은(는) 구조체로 바뀐다. 대응: %s. 상수 인덱스 접근은 이미 필드로 바꿨다. "
                    "변수 인덱스로 접근하는 줄을 인덱스가 가리키는 요소의 필드 접근으로 고친다. "
                    "어느 필드인지 정할 수 없으면 NO_CHANGE 를 쓴다." % (ft["target"], pairs))
    return ""


def _make_task(rel, d, text1, ls1, big, members, la, lb, fn, guidance, env_txt, note_ctx=""):
    """본문 text1 의 la~lb 줄을 조각으로 하는 작업 하나를 만든다. members 는 (줄 번호, 이슈) 목록."""
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
            "extra": "", "feedback": "", "attempts": 0, "ctx_rounds": 0}


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


def plan_chunks(ctx, rel, d, text1, ls1, big, cl, fn, guidance, env_txt):
    """분할 순차: 한 번에 보낼 수 없는 구간을 문장 경계에서 나눠 조각 작업 목록을 만든다(나눌 수 없으면 None).
    원칙: (1) 문장 중간에서 자르지 않는다 (2) 조각마다 입력과 예상 출력이 한도 안에 든다
          (3) 수정 지점이 없는 조각은 보내지 않는다 (4) 조각은 앞에서부터 순서대로 처리한다(s6_llm)"""
    lim, llm = ctx.cfg["limits"], ctx.cfg["llm"]
    la0, lb0 = max(1, cl[0][0] - 3), min(len(ls1), cl[-1][0] + 3)
    code1 = mask_preprocessor(mask_code(text1)[0])

    def tok(s):
        return max(0, est_tokens(s) - 8)

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
    in_budget = int((llm["max_total_tokens"] - llm["max_output_tokens"]) / 1.1) - fixed - reserve
    out_budget = int(llm["max_output_tokens"] * 0.8)
    head = _function_head(ctx, rel, d, fn, lim)

    for _try in range(3):
        if in_budget < 150:
            return None
        chunks, cur = [], None
        for la, lb, t_in, t_out, mem in segs:
            if t_in > in_budget or t_out > out_budget:
                return None                       # 경계 하나 사이가 한도를 넘는다. 나눌 수 없는 구간
            if cur and cur[2] + t_in <= in_budget and cur[3] + t_out <= out_budget:
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
            if int((need + reserve - 400) * 1.1) + llm["max_output_tokens"] > llm["max_total_tokens"]:
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


def plan_tasks(ctx):
    """T3(llm) 이슈를 파일별로 모아, 가까운 것끼리 한 작업으로 묶고 프롬프트에 넣을 조각을 만든다."""
    st, lim, llm = ctx.store, ctx.cfg["limits"], ctx.cfg["llm"]
    env_txt = "\n".join("- " + s for s in env_summary_lines(ctx.env))
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
                g = ctx.rule_by_id.get(i["rule_id"], {}).get("guidance", "") or _request_guidance(ctx, i["rule_id"])
                item = "- [%s] %s: %s" % (i["rule_id"], i["title"], g)
                if item not in guidance:
                    guidance.append(item)
            fn = st.q("SELECT name, sig_start, body_start, start_line FROM functions WHERE path=? AND sig_start<=? AND end>? "
                      "ORDER BY sig_start DESC LIMIT 1", (rel, cl[0][1]["start"], cl[0][1]["start"]))
            task = None
            ctx_lines = lim["slice_context_lines"]
            for attempt_ctx in (ctx_lines, ctx_lines // 2, 5, 0):
                la, lb = max(1, t_first - attempt_ctx), min(len(ls1), t_last + attempt_ctx)
                a = ls1[la - 1]
                b = ls1[lb] - 1 if lb < len(ls1) else len(text1)
                view, segs = build_view(text1, a, b, big)
                vls = _line_starts(view)
                targets = []
                for line, i in cl:
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
                context = ""
                if fn and fn[0]["start_line"] < la:
                    context = "\n[참고: 이 조각이 속한 함수]\n%s\n" % d.text[fn[0]["sig_start"]:fn[0]["body_start"]].strip()[:600]
                task = {"path": rel, "issue_ids": ids, "text1": text1, "ls1": ls1, "segs": segs, "view": view,
                        "codec": d.codec, "t_first": t_first, "t_last": t_last, "la": la, "lb": lb,
                        "fmt": {"env": env_txt, "guidance": "\n".join(guidance), "targets": "\n".join(targets),
                                "context": context, "path": rel, "start_line": la, "end_line": lb, "code": view},
                        "extra": "", "feedback": "", "attempts": 0, "ctx_rounds": 0}
                need = count_tokens(llm, ctx.system_prompt + ctx.user_template.format(extra="", feedback="", **task["fmt"]))
                if int(need * 1.1) + llm["max_output_tokens"] <= llm["max_total_tokens"]:
                    task["est_in"] = need
                    break
                task = None
            if task is not None:
                est_out = sum(2 * est_tokens(x) + 30 for x in task["fmt"]["targets"].split("\n") if x)
                if est_out > llm["max_output_tokens"] * 0.8:
                    task = None                    # 응답이 출력 상한을 넘을 것 같으면 나눠 보낸다
            if task is None:
                parts = None
                if lim.get("chunk_enabled", True):
                    parts = plan_chunks(ctx, rel, d, text1, ls1, big, cl, fn, guidance, env_txt)
                if not parts:
                    _mark_manual(ctx, ids, "조각이 토큰 한도를 넘고, 나눌 수 있는 문장 경계가 없음")
                    LOG6.warn("%s %d~%d줄: 토큰 한도를 넘고 나눌 수 없어 수동 검토로 보냅니다" % (rel, t_first, t_last))
                    continue
                gid = 0
                for k, t in enumerate(parts, 1):
                    cur = st.x("INSERT INTO tasks(path,start_line,end_line,issue_ids,status,attempts,note,grp,part,parts) "
                               "VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (rel, t["la"], t["lb"], ",".join(str(x) for x in t["issue_ids"]), "pending", 0, "",
                                0, k, len(parts)))
                    t["id"] = cur.lastrowid
                    if len(parts) > 1:
                        gid = gid or t["id"]
                        t["group"], t["part"], t["parts"] = gid, k, len(parts)
                        st.x("UPDATE tasks SET grp=? WHERE id=?", (gid, t["id"]))
                    tasks.append(t)
                if len(parts) > 1:
                    LOG6.info("%s %d~%d줄: 한 번에 보낼 수 없어 %d조각으로 나눠 순서대로 처리합니다(분할 순차)"
                              % (rel, t_first, t_last, len(parts)))
                continue
            cur = st.x("INSERT INTO tasks(path,start_line,end_line,issue_ids,status,attempts,note) VALUES(?,?,?,?,?,?,?)",
                       (rel, task["la"], task["lb"], ",".join(str(x) for x in ids), "pending", 0, ""))
            task["id"] = cur.lastrowid
            tasks.append(task)
    st.commit()
    return tasks


def _task_prompt(ctx, task):
    extra = ("\n[추가 정의]\n%s\n" % task["extra"]) if task["extra"] else ""
    fb = ("\n[이전 응답이 기각된 이유] 아래 문제를 고쳐 다시 답한다.\n%s\n" % task["feedback"]) if task["feedback"] else ""
    if task.get("ledger"):
        extra += "\n[앞 조각에서 바꾼 내용] 같은 이름과 방식으로 일관되게 고친다.\n%s\n" % task["ledger"]
    return ctx.user_template.format(extra=extra, feedback=fb, **task["fmt"])


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
    if not llm.get("endpoint"):
        LOG6.warn("sLLM 주소(llm.endpoint)가 설정되지 않았습니다. 대상 이슈 %d건을 수동 검토로 보냅니다" % n_open)
        st.x("UPDATE issues SET status='manual', note='sLLM 주소 미설정' WHERE tier='T3' AND llm=1 AND status='open'")
        st.commit()
        return
    tasks = plan_tasks(ctx)
    LOG6.info("sLLM 작업 %d건 (이슈 %d건), 동시 요청 %d, 한도 %d토큰" % (len(tasks), n_open, llm["max_parallel"], llm["max_total_tokens"]))
    os.makedirs(ctx.p("logs", "llm"), exist_ok=True)
    pending, n_manual, conn_fail, any_ok = tasks, 0, 0, False
    groups = {}                                   # 분할 묶음: 묶음 번호 -> 조각 작업 목록
    for t in tasks:
        if t.get("group"):
            groups.setdefault(t["group"], []).append(t)
    n_groups, cancelled = len(groups), {}         # cancelled: 묶음 번호 -> 실패한 조각 번호
    round_no = 0
    while pending:
        round_no += 1
        head = {}                                 # 분할 묶음마다 지금 보낼 차례인 조각 번호
        for t in pending:
            if t.get("group"):
                head[t["group"]] = min(head.get(t["group"], t["part"]), t["part"])
        ready = [t for t in pending if not t.get("group") or t["part"] == head[t["group"]]]
        ready_ids = {t["id"] for t in ready}
        jobs = []
        for t in ready:
            t["attempts"] += 1
            if t.get("group"):
                t["ledger"] = _group_ledger(ctx, groups[t["group"]], t)
            t["prompt"] = _task_prompt(ctx, t)
            t["key"] = sha256_bytes((llm["model"] + "\0" + ctx.system_prompt + "\0" + t["prompt"]).encode("utf-8"))
            t["cached"] = st.one("SELECT response FROM llm_cache WHERE key=?", (t["key"],))
            write_text(ctx.p("logs", "llm", "task%04d_a%d_prompt.txt" % (t["id"], t["attempts"])),
                       ctx.system_prompt + "\n\n=====\n\n" + t["prompt"])
            if t["cached"] is None:
                jobs.append(t)
        LOG6.info("%d차 요청: 작업 %d건 (캐시 재사용 %d건, 앞 조각을 기다리는 분할 조각 %d건)"
                  % (round_no, len(ready), len(ready) - len(jobs), len(pending) - len(ready)))
        results = {}
        if jobs:
            with ThreadPoolExecutor(max_workers=max(1, int(llm["max_parallel"]))) as ex:
                for t, res in zip(jobs, ex.map(lambda t: llm_chat(llm, ctx.system_prompt, t["prompt"]), jobs)):
                    results[t["id"]] = res
        retry_ids = set()
        for t in ready:
            ok, content, ms = (True, t["cached"], 0) if t["cached"] is not None else results[t["id"]]
            rfile = "task%04d_a%d_response.txt" % (t["id"], t["attempts"])
            write_text(ctx.p("logs", "llm", rfile), content)
            verdict, note = _handle_response(ctx, t, ok, content)
            st.x("INSERT INTO llm_calls(task_id,attempt,prompt_file,response_file,est_in,ms,ok,note) VALUES(?,?,?,?,?,?,?,?)",
                 (t["id"], t["attempts"], rfile.replace("response", "prompt"), rfile, t.get("est_in", 0), ms, int(ok), note))
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
                n_manual += 1
                _mark_manual(ctx, t["issue_ids"], note)
                st.x("UPDATE tasks SET status='manual', attempts=?, note=? WHERE id=?", (t["attempts"], note, t["id"]))
            else:
                n_manual += 1
                _mark_manual(ctx, t["issue_ids"], "sLLM 수정 실패: " + (note or verdict))
                st.x("UPDATE tasks SET status='manual', attempts=?, note=? WHERE id=?", (t["attempts"], note or verdict, t["id"]))
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
        rest = [t for t in pending if (t["id"] in retry_ids or t["id"] not in ready_ids) and t.get("group") not in cancelled]
        if conn_fail >= 3 and not any_ok:
            LOG6.error("sLLM 서버 호출이 연속으로 실패했습니다. 주소와 서버 상태를 확인하세요: %s" % llm["endpoint"])
            for t in rest:
                n_manual += 1
                _mark_manual(ctx, t["issue_ids"], "sLLM 서버 호출 실패")
                st.x("UPDATE tasks SET status='manual', note='sLLM 서버 호출 실패' WHERE id=?", (t["id"],))
            st.commit()
            break
        pending = rest
    st.set_meta("chunk_groups", {"total": n_groups, "cancelled": len(cancelled),
                                 "parts": sum(1 for t in tasks if t.get("group"))})
    ts = {r["status"]: r["n"] for r in st.q("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}
    LOG6.info("sLLM 수정 완료: 수용 %d건, 수동 검토 %d건, 취소 %d건 (분할 묶음 %d개 중 취소 %d개)"
              % (ts.get("done", 0), ts.get("manual", 0), ts.get("cancelled", 0), n_groups, len(cancelled)))


def _handle_response(ctx, t, ok, content):
    """응답 하나를 처리한다. 돌려주는 값: (판정, 설명). 판정은 수용/재요청/수동 중 하나."""
    st, llm = ctx.store, ctx.cfg["llm"]
    if not ok:
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
    ids = ",".join(str(x) for x in t["issue_ids"])
    for e in edits:
        st.x("INSERT INTO edits(path,base,start,end,before,after,origin,ref,issue_ids,review,status,note) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
             (t["path"], "post_rule", e["start"], e["end"], e["before"], e["after"], "llm", "task%d" % t["id"],
              ids, 1, "accepted", ("분할 순차 %d/%d" % (t["part"], t["parts"])) if t.get("group") else ""))
    return "수용", "수정 블록 %d개" % len(edits)


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
            n = len(rule_hits(rule, d2))
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


def verify_compile(ctx, rel):
    """L3: 병합 결과와 스냅샷을 각각 cl /Zs 로 검사해 새로 생긴 오류만 본다."""
    cfg, pdir = _compile_flags(ctx, rel)
    tmo = ctx.cfg["limits"]["compile_timeout_sec"]

    def run(base):
        root = ctx.p(*base)
        flags = cl_flags_for(ctx.env, cfg, os.path.join(root, *pdir.split("/")) if pdir else root)
        src = os.path.join(root, *rel.split("/"))
        ran, diags, raw = run_cl_syntax(ctx.tools, src, os.path.dirname(src), flags, tmo)
        write_text(ctx.p("logs", "compile", base[-1], rel.replace("/", "__") + ".log"), raw)
        return ran, [x for x in diags if x["kind"] != "warning"]

    ran_a, before = run(("snapshot",))
    ran_b, after = run(("work", "merged"))
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


def s8_verify(ctx):
    st = ctx.store
    st.x("DELETE FROM verifications")
    use_cl = ctx.grade == "나" and ctx.cfg["compile"]["mode"] != "off"
    paths = [r["path"] for r in st.q("SELECT path FROM files WHERE status='병합됨' ORDER BY path")]
    is_src = lambda rel: os.path.splitext(rel)[1].lower() in (".c", ".cc", ".cpp", ".cxx")
    n_pass = n_partial = n_revert = 0
    for rel in paths:
        d, text, _bad = build_merged(ctx, rel)
        stage = "전체"
        while True:
            ok, res = verify_static(ctx, d, text)
            level = "L2" if ok else ""
            if ok and use_cl and is_src(rel):
                c_ok, c_note = verify_compile(ctx, rel)
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
    write_text(os.path.join(out, "project_settings.md"), "\n".join(L) + "\n")

    # --- 빌드 환경용 절차서 ---
    G = ["# 빌드 환경에서 할 일", "",
         "1. 원본 패키지를 복사한다. 변환은 되돌릴 수 없으므로 복사본에서 한다.",
         "2. `out/changed/` 의 파일을 복사본의 같은 위치에 덮어쓴다.",
         "3. 복사본의 .sln 을 대상 Visual Studio 로 열어 프로젝트를 변환한다. 변환 보고서(UpgradeLog.htm)를 보관한다.",
         "4. `project_settings.md` 의 설정을 프로젝트 속성에 적용한다.",
         "5. 빌드하고 오류 로그를 파일로 남긴다. 개발자 PowerShell 에서:",
         "", "```", "msbuild 솔루션이름.sln /p:Configuration=Release /p:Platform=Win32 /fl", "```", "",
         "6. 같은 폴더에 생긴 msbuild.log 와 `manual_review.md` 를 함께 보고 남은 항목을 처리한다.",
         "7. 링크 오류와 실행 시 동작은 이 도구의 검증 범위 밖이다. 직접 확인한다.", ""]
    write_text(os.path.join(out, "build_guide.md"), "\n".join(G))

    # --- 보고서 ---
    fstat = st.q("SELECT status, COUNT(*) AS n FROM files GROUP BY status ORDER BY status")
    istat = st.q("SELECT tier, status, COUNT(*) AS n FROM issues GROUP BY tier, status ORDER BY tier, status")
    estat = st.q("SELECT origin, status, COUNT(*) AS n FROM edits GROUP BY origin, status ORDER BY origin, status")
    rstat = st.q("SELECT rule_id, tier, COUNT(*) AS n, SUM(status='fixed') AS fixed, SUM(status='manual') AS manual, "
                 "SUM(status='absorbed') AS absorbed FROM issues GROUP BY rule_id, tier ORDER BY tier, rule_id")
    calls = st.q("SELECT COUNT(*) AS n, COALESCE(SUM(est_in),0) AS tok, COALESCE(SUM(ms),0) AS ms, "
                 "COALESCE(SUM(ok=0),0) AS fail FROM llm_calls")[0]
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
    R += ["", "## 2. 환경 등급과 보장 범위", "", "- 환경 등급: **%s**" % ctx.grade, "- " + grade_txt.get(ctx.grade, ""),
          "- 상세는 `preflight.md` 참조.", "", "## 3. 파일 처리 결과", ""]
    R += _table(["상태", "파일 수"], [(r["status"], r["n"]) for r in fstat])
    R += ["", "## 4. 이슈(탐지 결과)", ""] + _table(["규칙/요청", "단계", "탐지", "수정됨", "설정 흡수", "수동 검토"], [
        (r["rule_id"], r["tier"], r["n"], r["fixed"] or 0, r["absorbed"] or 0, r["manual"] or 0) for r in rstat])
    R += ["", "단계별 상태:", ""] + _table(["단계", "상태", "건수"], [(r["tier"], r["status"], r["n"]) for r in istat])
    R += ["", "## 5. 수정 레코드", ""] + (_table(["출처", "상태", "건수"], [(r["origin"], r["status"], r["n"]) for r in estat])
                                       if estat else ["없음."])
    R += ["", "## 6. sLLM 사용", "", "- 호출 %d회(실패 %d회), 입력 토큰 추정 합계 %d, 소요 %.1f초"
          % (calls["n"], calls["fail"], calls["tok"], calls["ms"] / 1000.0),
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
    R += ["", "## 8. 남은 일", "", "- 수동 검토 %d건: `manual_review.md`" % n_manual,
          "- 프로젝트 설정 변경: `project_settings.md`", "- 실제 빌드 절차: `build_guide.md`",
          "", "## 9. 이 버전(%s)이 하지 않는 것" % VERSION, "",
          "- 한도를 넘는 큰 구간의 분할 처리, 프로젝트 변환과 전체 빌드, .rc/.idl/.def 의 규칙 탐지",
          "- 토큰 수는 추정치다(서버의 tokenize 주소를 설정하지 않은 경우)", ""]
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
    if ctx.dry_run:
        LOGM.info("탐지만 수행했습니다 (%.1f초): 처리 대상 이슈 %d건. 건수는 out/report.md 4절 참조"
                  % (time.time() - t_all, n_manual))
        LOGM.info("산출물: %s" % ctx.p("out"))
        return EXIT_OK
    LOGM.info("전체 완료 (%.1f초): 변경 파일 %d개, 수동 검토 %d건" % (time.time() - t_all, n_changed, n_manual))
    LOGM.info("산출물: %s" % ctx.p("out"))
    LOGM.info("로그: %s" % ctx.p("logs", "run.log"))
    return EXIT_REVIEW if n_manual else EXIT_OK


# ============================================================================
# [M11 명령줄]  run / preflight / status / rules / selftest
# 목적: 사용자가 부르는 진입점. 인자를 검증해 실행 문맥(ctx)을 만들고 실행기를 부른다.
# 종료 코드: 0 완료(수동 검토 없음) / 1 완료(수동 검토 있음) / 2 단계 실패 / 3 입력 오류
# ============================================================================
LOGA = Log("M11.명령줄")


def _make_ctx(args, need_env=True):
    """run/preflight 공통: 실행 폴더를 만들고 ctx 를 채운다."""
    ctx = Ctx()
    LOGA.info("%s %s 시작 (Python %s, %s, %s)" % (APP, VERSION, sys.version.split()[0], sys.platform,
                                              "exe" if getattr(sys, "frozen", False) else "py"))
    resume = getattr(args, "resume", None)
    if resume:
        ctx.run_dir = os.path.abspath(resume)
        info_path = os.path.join(ctx.run_dir, "run.json")
        if not os.path.isfile(info_path):
            raise UserInputError("재개할 실행 폴더가 아닙니다(run.json 없음): %s" % ctx.run_dir)
        info = load_json(info_path, "실행 정보")
        ctx.package, ctx.env, ctx.request = info["package"], info["env"], info["request"]
        ctx.dry_run = bool(info.get("dry_run"))
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
    ctx.pkg_name = os.path.basename(ctx.package.rstrip("\\/")) or "package"
    if not resume:
        base = os.path.abspath(getattr(args, "out", None) or os.path.join(os.getcwd(), "runs"))
        ctx.run_dir = os.path.join(base, ctx.pkg_name, time.strftime("%Y%m%d_%H%M%S"))
        if os.path.exists(ctx.run_dir):
            ctx.run_dir += "_%d" % os.getpid()
    os.makedirs(ctx.run_dir, exist_ok=True)
    _SINK.open(ctx.p("logs", "run.log"))
    LOGA.info("실행 폴더: %s" % ctx.run_dir)
    ctx.cfg = load_server_config(getattr(args, "server_config", None))
    if getattr(args, "llm_endpoint", None):
        ctx.cfg["llm"]["endpoint"] = args.llm_endpoint
    if getattr(args, "max_parallel", None):
        ctx.cfg["llm"]["max_parallel"] = int(args.max_parallel)
    if getattr(args, "no_compile", False):
        ctx.cfg["compile"]["mode"] = "off"
    all_rules = load_rules(getattr(args, "rules", None))
    ctx.rule_by_id = {r["id"]: r for r in all_rules}
    ctx.rules = [r for r in all_rules if rule_active(r, ctx.env)]
    LOGA.info("규칙 %d개 중 이 환경값에서 켜진 것 %d개" % (len(all_rules), len(ctx.rules)))
    ctx.store = Store(ctx.p("state.db"))
    if not resume:
        safe_cfg = json.loads(json.dumps(ctx.cfg))
        safe_cfg["llm"]["api_key"] = "***" if safe_cfg["llm"].get("api_key") else ""
        write_text(ctx.p("run.json"), json.dumps({
            "app": APP, "version": VERSION, "package": ctx.package, "env": ctx.env, "request": ctx.request,
            "dry_run": ctx.dry_run, "config": safe_cfg, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
            "argv": sys.argv[1:]}, ensure_ascii=False, indent=2))
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
    rules = load_rules(getattr(args, "rules", None))
    print("%-22s %-3s %-28s %s" % ("규칙 ID", "단계", "켜지는 조건", "내용"))
    for r in rules:
        w = r.get("when") or {}
        cond = ", ".join("%s=%s" % kv for kv in sorted(w.items())) or "항상"
        how = "" if r["tier"] != "T3" else (" [sLLM]" if r.get("llm") else " [수동]")
        print("%-22s %-3s %-28s %s%s" % (r["id"], r["tier"], cond, r["title"], how))
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


def _selftest_mock_server():
    """모의 sLLM 서버. typedef 줄은 가드로 감싸고, SetWindowLong 줄은 Ptr 판으로 바꾸는 수정 블록을 돌려준다.
    조각에 FAILME 가 있으면 형식에 맞지 않는 응답을 낸다(분할 취소 시험용). 받은 프롬프트는 prompts 에 모은다."""
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            prompt = body["messages"][-1]["content"]
            code = prompt.split("-----BEGIN CODE-----\n", 1)[-1].split("\n-----END CODE-----", 1)[0]
            self.server.prompts.append(prompt)
            wl = [l for l in code.split("\n") if "SetWindowLong(" in l]
            m = re.search(r"(?:^[ \t]*typedef\b[^\n]*\bu?int(?:8|16|32|64)_t[ \t]*;[ \t]*\n?)+", code, re.M)
            if "FAILME" in code:
                content = "이 조각은 고칠 수 없습니다."        # 형식에 맞지 않는 응답(분할 취소 시험용)
            elif wl:
                content = "\n".join(
                    "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE"
                    % (l, l.replace("SetWindowLong(", "SetWindowLongPtr(").replace("GWL_USERDATA", "GWLP_USERDATA")
                       .replace("(LONG)", "(LONG_PTR)")) for l in wl)
            elif m:
                run = m.group(0).rstrip("\n")
                content = ("<<<<<<< SEARCH\n%s\n=======\n#if !defined(_MSC_VER) || _MSC_VER < 1600\n%s\n#endif\n"
                           ">>>>>>> REPLACE" % (run, run))
            else:
                content = "NO_CHANGE"
            data = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.prompts = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


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
    ctx.cfg["llm"].update(max_total_tokens=4000, max_output_tokens=800)       # 일부러 작게
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
        check("변수 인덱스와 선언은 그대로", "g_data[i] = 0;" in txt and "int g_data[10];" in txt)
        check("큰 상수 배열이 바이트 단위로 동일", table.encode("ascii") in raw)
    check("sLLM 수정 반영(Common.h typedef 가드)", os.path.isfile(hp) and
          b"#if !defined(_MSC_VER) || _MSC_VER < 1600\ntypedef unsigned char uint8_t;\ntypedef unsigned short uint16_t;\n#endif" in read_bytes(hp))
    for name in ("report.md", "manifest.csv", "manual_review.md", "project_settings.md", "build_guide.md", "preflight.md"):
        check("산출물 %s" % name, os.path.isfile(os.path.join(out, name)))
    check("전체 patch 생성", os.path.isfile(os.path.join(out, "diff", "all.patch")))
    mr = open(os.path.join(out, "manual_review.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "manual_review.md")) else ""
    check("수동 검토에 좁히기 변환과 배열 선언 포함", "R-T3-NARROWING-CHAR" in mr and "배열 선언" in mr)
    pf = open(os.path.join(out, "preflight.md"), encoding="utf-8").read() if os.path.isfile(os.path.join(out, "preflight.md")) else ""
    check("환경 점검에 외부 헤더 후보(ExtVendor.h) 표시", "ExtVendor.h" in pf)
    _selftest_chunk(work, check)
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
    r.add_argument("--request", help="요청사항(파일 경로 또는 config/requests 안의 이름)")
    r.add_argument("--server-config", dest="server_config", help="서버 설정 파일. 기본 config/server.json")
    r.add_argument("--rules", help="추가 규칙 파일. 기본 config/rules.json")
    r.add_argument("--llm-endpoint", dest="llm_endpoint", help="sLLM 주소(서버 설정보다 우선)")
    r.add_argument("--max-parallel", dest="max_parallel", type=int, help="sLLM 동시 요청 수")
    r.add_argument("--out", help="실행 폴더를 만들 위치. 기본 ./runs")
    r.add_argument("--dry-run", dest="dry_run", action="store_true", help="탐지까지만 수행(S0~S2)하고 보고")
    r.add_argument("--until", help="지정한 단계까지만 수행(예: S4)")
    r.add_argument("--resume", help="중단된 실행 폴더를 이어서 수행")
    r.add_argument("--no-compile", dest="no_compile", action="store_true", help="컴파일 검사를 하지 않음")
    r.set_defaults(func=cmd_run)

    f = sub.add_parser("preflight", help="환경 점검만 수행한다(03_확인사항 자동화)")
    f.add_argument("--package", required=True, help="대상 패키지 폴더")
    f.add_argument("--out", help="실행 폴더를 만들 위치. 기본 ./runs")
    f.add_argument("--server-config", dest="server_config")
    f.set_defaults(func=cmd_preflight)

    s = sub.add_parser("status", help="실행 폴더의 진행 상태를 본다")
    s.add_argument("--run", required=True, help="실행 폴더")
    s.set_defaults(func=cmd_status)

    u = sub.add_parser("rules", help="규칙 목록을 본다")
    u.add_argument("--rules", help="추가 규칙 파일")
    u.set_defaults(func=cmd_rules)

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
