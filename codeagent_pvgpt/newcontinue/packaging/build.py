# packaging/build.py — ncn 배포 묶음 만들기(모든 OS 공용). cn 원본 없음(배포 전용, 계획 8장·14-6)
# 실행(빌드 전용 의존성 PyInstaller 는 프로젝트에 넣지 않고 그때만 얹는다):
#   cd newcontinue && uv run --with pyinstaller python packaging/build.py --out dist [--smoke]
#   (Windows 는 packaging/build_win.ps1 <출력 폴더>, macOS·Linux 는 packaging/build_unix.sh <출력 폴더> 가 이 파일을 부른다)
# 결과(<out> 아래):
#   ncn/                               PyInstaller onedir — ncn(.exe) + _internal/ (VSIX 작업이 이 폴더를 통째로 넣는다)
#   ncn-<버전>-<os>-<arch>/             묶음 폴더 — ncn/ + install·uninstall + config.template.yaml + README.txt + VERSION.txt
#   ncn-<버전>-<os>-<arch>.zip          위 폴더를 zip(맨 위 폴더 이름 포함)
#   ncn-<버전>-<os>-<arch>.zip.sha256   "해시  파일이름" 한 줄(sha256sum 형식 — Windows 는 Get-FileHash 로 비교)
#   build_info.json                    버전·os·arch·파일 이름·해시(CI 가 읽음)
from __future__ import annotations                           # 타입 주석 지연 평가

import argparse                                              # 명령줄
import hashlib                                               # sha256
import json                                                  # build_info.json
import os                                                    # 환경변수(GITHUB_OUTPUT)
import platform                                              # CPU 구조
import re                                                    # 버전 글 찾기
import shutil                                                # 폴더 복사·삭제
import subprocess                                            # PyInstaller 실행
import sys                                                   # 파이썬 경로·플랫폼
import time                                                  # 빌드 시간
import zipfile                                               # zip 만들기
from pathlib import Path                                     # 경로

PKG = Path(__file__).resolve().parent                        # packaging 폴더
ROOT = PKG.parent                                            # newcontinue 폴더
BUNDLE = PKG / "bundle"                                      # 묶음에 넣을 파일 원본(install.cmd 등)
WIN_FILES = ["install.cmd", "install.ps1", "uninstall.cmd", "uninstall.ps1"]   # Windows 묶음 전용 파일
UNIX_FILES = ["install.sh"]                                  # macOS·Linux 묶음 전용 파일
COMMON_FILES = ["config.template.yaml", "README.txt"]        # 공통 파일
CRLF_SUFFIXES = {".cmd", ".ps1", ".txt", ".yaml"}            # Windows 묶음에서 CRLF 로 바꿀 확장자(cmd 는 LF 면 goto·라벨이 깨짐)
BOM_SUFFIXES = {".ps1", ".txt"}                              # UTF-8 BOM 을 붙일 확장자(PowerShell 5.1 은 BOM 없는 .ps1 을 ANSI(cp949)로 읽어 한글이 깨짐)


def read_version() -> str:                                   # ncn 버전 읽기
    """ncn/__init__.py 의 __version__(import 없이 글로 읽음 — 빌드 파이썬과 무관하게)."""
    text = (ROOT / "ncn" / "__init__.py").read_text(encoding="utf-8")   # 파일 글
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)   # 버전


def os_name() -> str:                                        # 묶음 이름의 OS 부분
    """windows / macos / linux."""
    if sys.platform == "win32":                              # Windows
        return "windows"                                     # windows
    if sys.platform == "darwin":                             # macOS
        return "macos"                                       # macos
    return "linux"                                           # 그 밖은 linux


def arch_name() -> str:                                      # 묶음 이름의 CPU 부분
    """x64 / arm64(Node·VS 표기). 빌드 파이썬이 보는 구조를 따른다(Windows 는 PROCESSOR_ARCHITECTURE — x64 에뮬레이션 파이썬이면 AMD64)."""
    m = platform.machine().lower()                           # CPU 이름(AMD64·ARM64·x86_64·arm64·aarch64)
    return {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(m, m)   # 표기 맞춤


def run_pyinstaller(out: Path) -> Path:                      # PyInstaller 실행
    """packaging/ncn.spec 으로 onedir 빌드 → <out>/ncn/. 작업 폴더는 <out>/_build(깨끗이 다시)."""
    try:                                                     # PyInstaller 가 있는지
        import PyInstaller                                   # noqa: F401 — 있는지만 확인
    except ImportError:                                      # 없으면 안내
        sys.exit("PyInstaller 가 없습니다: uv run --with pyinstaller python packaging/build.py ... 로 실행하세요.")   # 종료
    dist = out / "ncn"                                       # onedir 결과
    if dist.exists():                                        # 이전 결과
        shutil.rmtree(dist)                                  # 지움(남은 파일 섞임 방지)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",   # 확인 없이·캐시 비우고
           "--distpath", str(out), "--workpath", str(out / "_build"), str(PKG / "ncn.spec")]   # 결과·작업 폴더·설정
    print("+", " ".join(cmd), flush=True)                    # 실행 명령 표시
    subprocess.run(cmd, check=True, cwd=ROOT)                # 실행(실패면 예외 → 종료 코드 1)
    exe = dist / ("ncn.exe" if sys.platform == "win32" else "ncn")   # 실행 파일
    if not exe.is_file():                                    # 없으면
        sys.exit(f"빌드 결과에 실행 파일이 없습니다: {exe}")      # 종료
    return dist                                              # onedir 폴더


def copy_text(src: Path, dst: Path, *, crlf: bool) -> None:   # 글 파일 복사(줄바꿈·BOM 맞춤)
    """원본(UTF-8, LF 든 CRLF 든)을 읽어 줄바꿈을 정하고, .ps1·.txt 는 BOM 을 붙여 쓴다."""
    text = src.read_text(encoding="utf-8-sig").replace("\r\n", "\n")   # BOM 떼고 LF 로 통일
    if crlf and src.suffix.lower() in CRLF_SUFFIXES:         # Windows 묶음이면
        text = text.replace("\n", "\r\n")                    # CRLF
    bom = crlf and src.suffix.lower() in BOM_SUFFIXES        # BOM 붙일지(Windows 묶음의 .ps1·.txt)
    dst.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))   # 쓰기
    if src.suffix == ".sh":                                  # 셸 스크립트는
        dst.chmod(0o755)                                     # 실행 권한


def assemble(out: Path, dist: Path, version: str, osn: str, arch: str) -> Path:   # 묶음 폴더 만들기
    """<out>/ncn-<버전>-<os>-<arch>/ 에 onedir + 설치 파일 + 템플릿 + README + VERSION.txt."""
    name = f"ncn-{version}-{osn}-{arch}"                     # 묶음 이름
    root = out / name                                        # 묶음 폴더
    if root.exists():                                        # 이전 것
        shutil.rmtree(root)                                  # 지움
    root.mkdir(parents=True)                                 # 만들기
    shutil.copytree(dist, root / "ncn", symlinks=True)       # onedir(macOS 의 Python.framework 심볼릭 링크 보존)
    win = osn == "windows"                                   # Windows 묶음인가
    for f in COMMON_FILES + (WIN_FILES if win else UNIX_FILES):   # 넣을 파일
        copy_text(BUNDLE / f, root / f, crlf=win)            # 줄바꿈·BOM 맞춰 복사
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())   # 빌드 시각
    commit = os.environ.get("GITHUB_SHA", "")[:12] or "local"   # 커밋(CI 면 GITHUB_SHA)
    info = f"ncn {version}\nos={osn} arch={arch}\nbuilt={stamp}\ncommit={commit}\npython={platform.python_version()}\n"   # 버전 파일 내용
    text = info.replace("\n", "\r\n") if win else info     # Windows 는 CRLF
    (root / "VERSION.txt").write_text(text, encoding="utf-8", newline="")   # 버전 파일(install 이 설치 폴더로 복사)
    return root                                              # 묶음 폴더


def make_zip(root: Path) -> Path:                            # zip 만들기
    """맨 위 폴더 이름을 포함해 zip. 실행 권한(유닉스)·심볼릭 링크를 보존한다."""
    zpath = root.parent / (root.name + ".zip")               # 이름에 점(버전)이 있으므로 직접 붙임
    if zpath.exists():                                       # 이전 것
        zpath.unlink()                                       # 지움
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:   # 압축
        for p in sorted(root.rglob("*")):                    # 모든 파일·폴더
            arc = Path(root.name) / p.relative_to(root)      # zip 안 경로
            if p.is_symlink():                               # 심볼릭 링크(macOS framework)
                zi = zipfile.ZipInfo(arc.as_posix())         # 항목
                zi.create_system = 3                         # 유닉스
                zi.external_attr = (0o120777 << 16)          # 링크 표시
                z.writestr(zi, os.readlink(p))               # 링크 대상 글
            elif p.is_file():                                # 파일
                z.write(p, arc.as_posix())                   # 권한 포함해 기록(유닉스 실행 비트 보존)
    return zpath                                             # zip 경로


def sha256_file(p: Path) -> str:                             # 파일 해시
    """sha256 16진수."""
    h = hashlib.sha256()                                     # 해시
    with p.open("rb") as f:                                  # 읽기
        for chunk in iter(lambda: f.read(1 << 20), b""):     # 1MB 씩
            h.update(chunk)                                  # 누적
    return h.hexdigest()                                     # 결과


def dir_size(p: Path) -> int:                                # 폴더 크기(바이트)
    """심볼릭 링크는 세지 않음."""
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())   # 합계


def main(argv: list[str] | None = None) -> int:              # 진입
    """빌드 → 묶음 → zip → sha256 → (선택) 스모크 시험."""
    ap = argparse.ArgumentParser(description="ncn 배포 묶음 빌드")   # 명령줄
    ap.add_argument("--out", default=str(ROOT / "dist"), help="출력 폴더(기본 newcontinue/dist)")   # 출력 폴더
    ap.add_argument("--no-zip", action="store_true", help="onedir 만(VSIX 용) — 묶음·zip 생략")   # zip 생략
    ap.add_argument("--smoke", action="store_true", help="빌드한 실행 파일로 스모크 시험(packaging/smoke.py)")   # 스모크
    a = ap.parse_args(argv)                                  # 해석
    out = Path(a.out).resolve()                              # 출력 폴더(절대)
    out.mkdir(parents=True, exist_ok=True)                   # 만들기
    version, osn, arch = read_version(), os_name(), arch_name()   # 버전·OS·CPU
    print(f"ncn {version} → {osn}-{arch} (python {platform.python_version()})", flush=True)   # 요약
    t0 = time.time()                                         # 시작 시각
    dist = run_pyinstaller(out)                              # PyInstaller
    exe = dist / ("ncn.exe" if osn == "windows" else "ncn")  # 실행 파일
    info = {"version": version, "os": osn, "arch": arch, "onedir": str(dist), "exe": str(exe),   # 결과 정보
            "onedir_bytes": dir_size(dist), "build_seconds": round(time.time() - t0, 1)}   # (이어짐)
    if not a.no_zip:                                         # 묶음까지
        root = assemble(out, dist, version, osn, arch)       # 묶음 폴더
        z = make_zip(root)                                   # zip
        digest = sha256_file(z)                              # 해시
        (out / (z.name + ".sha256")).write_text(f"{digest}  {z.name}\n", encoding="utf-8", newline="\n")   # 해시 파일
        info.update({"bundle": str(root), "zip": str(z), "zip_name": z.name, "zip_bytes": z.stat().st_size, "sha256": digest})   # 정보 추가
    (out / "build_info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")   # 정보 파일
    print(json.dumps(info, ensure_ascii=False, indent=2), flush=True)   # 화면에도
    gh = os.environ.get("GITHUB_OUTPUT")                     # GitHub Actions 출력 파일
    if gh:                                                   # CI 면
        with open(gh, "a", encoding="utf-8") as f:           # 덧붙이기
            for k in ("version", "os", "arch", "exe", "zip", "zip_name", "sha256"):   # 다음 단계가 쓸 값
                if k in info:                                # 있는 것만
                    f.write(f"{k}={info[k]}\n")              # key=value
    if a.smoke:                                              # 스모크 시험
        cmd = [sys.executable, str(PKG / "smoke.py"), "--exe", str(exe), "--report", str(out / "smoke.json")]   # 같은 파이썬(가짜 LLM 을 띄움)
        print("+", " ".join(cmd), flush=True)                # 표시
        return subprocess.run(cmd, cwd=ROOT).returncode      # 결과 코드
    return 0                                                 # 성공


if __name__ == "__main__":                                   # 직접 실행
    sys.exit(main())                                         # 종료 코드
