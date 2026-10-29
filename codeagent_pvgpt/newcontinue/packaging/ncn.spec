# packaging/ncn.spec — PyInstaller 설정(ncn 실행 파일). 실행: packaging/build.py 가 부른다(직접: pyinstaller packaging/ncn.spec)
# 결과: <distpath>/ncn/ncn(.exe) + <distpath>/ncn/_internal/ (onedir)
# onedir 을 고른 이유(onefile 대신):
#   1) 시작 속도 — onefile 은 실행할 때마다 임시 폴더(%TEMP%\_MEIxxxx)에 수십 MB 를 풀어 1~3초가 더 걸린다.
#      VS 확장은 세션마다·재시작마다 ncn --stdio 를 띄우므로 이 지연이 매번 보인다.
#   2) 백신 오탐 — "실행 중 임시 폴더에 exe·dll 을 풀고 실행"하는 onefile 동작은 악성코드 드로퍼와 같아 사내 백신이 자주 막는다.
#      onedir 은 설치 폴더(%LOCALAPPDATA%\ncn)에 파일이 고정되어 예외 등록·화이트리스트도 경로 하나로 끝난다.
#   3) 비정상 종료 시 임시 폴더 찌꺼기(_MEI)가 남지 않는다(Job Object 로 강제 종료되는 VS 확장 경로 — 계획 14-2 V6).
# UPX 압축은 끈다: UPX 로 압축한 exe 는 백신 오탐이 크게 늘고, 압축 해제 시간만큼 시작이 느려진다.
# -*- mode: python ; coding: utf-8 -*-
import re                                                    # 버전 글 찾기
import sys                                                   # 플랫폼 확인
from pathlib import Path                                     # 경로

from PyInstaller.utils.hooks import collect_submodules, copy_metadata   # 하위 모듈·패키지 메타데이터 모으기

PKG = Path(SPECPATH).resolve()                               # noqa: F821 — SPECPATH 는 PyInstaller 가 넣어 줌(이 파일이 있는 packaging 폴더)
ROOT = PKG.parent                                            # newcontinue 폴더(pyproject.toml 이 있는 곳)
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "ncn" / "__init__.py").read_text(encoding="utf-8")).group(1)   # ncn 버전 글

# ── 데이터 파일 ── 토큰 순위표(ncn/tokenizer.py DATA_FILE = Path(__file__).parent/"data"/…) — 묶음 안에서도 같은 상대 위치에 둔다
datas = [(str(f), "ncn/data") for f in sorted((ROOT / "ncn" / "data").glob("*")) if f.is_file()]   # ncn/data/* 전부(o200k_base.tiktoken 등) → 묶음 안 ncn/data
if not any(d[0].endswith("o200k_base.tiktoken") for d in datas):   # 순위표가 빠지면 실행 시 토큰 세기가 실패하므로
    raise SystemExit("ncn/data/o200k_base.tiktoken 이 없습니다")     # 빌드를 멈춤
for dist in ("mcp", "agent-client-protocol", "httpx", "pydantic", "tiktoken"):   # importlib.metadata.version() 을 부르는 패키지들의 메타데이터
    try:                                                     # 설치되어 있으면
        datas += copy_metadata(dist)                         # *.dist-info 를 묶음에 넣음(없으면 실행 중 PackageNotFoundError)
    except Exception:                                        # 패키지 이름이 바뀌었거나 없음
        pass                                                 # 빠져도 빌드는 계속(스모크 시험이 잡음)

# ── 숨은 import ── 함수 안 지연 import 는 PyInstaller 가 대부분 찾지만, ncn 전체를 확실히 넣는다(도구·ACP·MCP 전송)
hiddenimports = collect_submodules("ncn")                    # ncn.* 전부
hiddenimports += ["tiktoken_ext", "tiktoken_ext.openai_public"]   # tiktoken 플러그인(쓰지 않지만 import 시 찾을 수 있음)

a = Analysis(                                                # noqa: F821 — 의존성 분석
    [str(PKG / "ncn_entry.py")],                             # 진입 스크립트
    pathex=[str(ROOT)],                                      # ncn 패키지를 찾을 곳
    binaries=[],                                             # 따로 넣을 실행 파일 없음(rg.exe 는 선택 — 사용자가 PATH 에 둠)
    datas=datas,                                             # 위의 데이터
    hiddenimports=hiddenimports,                             # 위의 숨은 import
    hookspath=[],                                            # 사용자 훅 없음
    runtime_hooks=[],                                        # 실행 훅 없음
    excludes=["tkinter", "_tkinter", "pytest", "_pytest", "IPython", "matplotlib", "numpy", "parity", "tests"],   # 필요 없는 큰 모듈(크기·시작 시간)
    noarchive=False,                                         # 파이썬 모듈은 PYZ 압축 묶음에(파일 수 줄임)
    optimize=0,                                              # assert·docstring 유지(오류 글이 cn 과 같아야 하므로 -O 쓰지 않음)
)
pyz = PYZ(a.pure)                                            # noqa: F821 — 파이썬 모듈 묶음

version_info = None                                          # Windows 파일 속성의 버전 정보(탐색기 → 속성 → 자세히)
if sys.platform == "win32":                                  # Windows 에서만 의미 있음(다른 OS 는 PyInstaller 가 무시)
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,   # 버전 자원 구조
                                                     StringTable, VarFileInfo, VarStruct, VSVersionInfo)   # (이어짐)
    nums = tuple(int(x) for x in re.findall(r"\d+", VERSION)[:4]) + (0,) * 4   # "0.0.1" → (0,0,1,0)
    nums = nums[:4]                                          # 네 자리만
    version_info = VSVersionInfo(                            # 버전 자원(서명이 없어도 "게시자 정보 없음" 외 정보는 보이게 — 백신 평판에도 약간 도움)
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),   # 숫자 버전·종류(앱)
        kids=[                                               # 글 정보
            StringFileInfo([StringTable("041204B0", [         # 0412 = 한국어, 04B0 = 유니코드
                StringStruct("CompanyName", "codeagent"),    # 회사(사내 도구 표시)
                StringStruct("FileDescription", "ncn - Continue CLI 호환 코딩 에이전트"),   # 설명
                StringStruct("FileVersion", VERSION),        # 파일 버전 글
                StringStruct("InternalName", "ncn"),         # 내부 이름
                StringStruct("OriginalFilename", "ncn.exe"),   # 원래 파일 이름
                StringStruct("ProductName", "ncn"),          # 제품 이름
                StringStruct("ProductVersion", VERSION),     # 제품 버전 글
            ])]),                                            # (이어짐)
            VarFileInfo([VarStruct("Translation", [0x0412, 1200])]),   # 언어·코드 페이지
        ],                                                   # (이어짐)
    )                                                        # 버전 자원 끝

exe = EXE(                                                   # noqa: F821 — 실행 파일
    pyz,                                                     # 파이썬 모듈 묶음
    a.scripts,                                               # 진입 스크립트
    [],                                                      # onedir: 바이너리는 COLLECT 가 _internal 에 둔다
    exclude_binaries=True,                                   # onedir(위 이유 참고)
    name="ncn",                                              # ncn(.exe)
    debug=False,                                             # 부트로더 디버그 끔
    bootloader_ignore_signals=False,                         # Ctrl+C 를 파이썬에 전달
    strip=False,                                             # 기호 제거 안 함(Windows 무관, 오류 추적 쉬움)
    upx=False,                                               # UPX 끔(백신 오탐·시작 지연)
    console=True,                                            # 콘솔 앱(stdin/stdout — 헤드리스·ACP stdio 에 필수)
    disable_windowed_traceback=False,                        # 콘솔 앱이라 무관
    argv_emulation=False,                                    # macOS 앱 번들 아님
    target_arch=None,                                        # 빌드하는 파이썬과 같은 구조(x64 러너 → x64, arm64 러너 → arm64)
    codesign_identity=None,                                  # 코드 서명 없음(계획 8-1 결정)
    entitlements_file=None,                                  # macOS 권한 파일 없음
    version=version_info,                                    # Windows 버전 자원(다른 OS 는 None)
)
coll = COLLECT(                                              # noqa: F821 — onedir 폴더 모으기
    exe,                                                     # 실행 파일
    a.binaries,                                              # 파이썬 DLL·확장 모듈(.pyd/.so)
    a.datas,                                                 # 데이터(순위표·메타데이터)
    strip=False,                                             # 기호 유지
    upx=False,                                               # UPX 끔
    name="ncn",                                              # <distpath>/ncn/ 폴더
)
