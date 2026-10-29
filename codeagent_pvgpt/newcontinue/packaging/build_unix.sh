#!/bin/sh
# packaging/build_unix.sh — macOS·Linux 에서 ncn 빌드(PyInstaller onedir) + 배포 zip. build_win.ps1 의 짝
# 사용법: sh codeagent_pvgpt/newcontinue/packaging/build_unix.sh [출력 폴더] [--no-zip] [--smoke]
#   결과: <출력>/ncn/ncn, <출력>/ncn-<버전>-<macos|linux>-<x64|arm64>.zip(.sha256), <출력>/build_info.json
# 필요: uv(PATH 에). 파이썬은 uv 가 받는다(기본 .python-version = 3.14, 바꾸려면 UV_PYTHON)
set -eu                                                          # 오류·미정의 변수면 중단

HERE=$(cd "$(dirname "$0")" && pwd)                              # packaging 폴더
ROOT=$(dirname "${HERE}")                                        # newcontinue 폴더
OUT="${ROOT}/dist"                                               # 출력 폴더 기본값(newcontinue/dist)
if [ $# -gt 0 ]; then                                            # 인자가 있으면
  case "$1" in                                                   # 첫 인자가
    --*) ;;                                                      # 옵션이면 기본 폴더 그대로
    *) OUT="$1"; shift ;;                                        # 아니면 출력 폴더로 쓰고 뺌
  esac
fi
mkdir -p "${OUT}"                                                # 만들기
OUT=$(cd "${OUT}" && pwd)                                        # 절대 경로
command -v uv >/dev/null 2>&1 || { echo "uv 가 PATH 에 없습니다(https://docs.astral.sh/uv/)" >&2; exit 1; }   # uv 확인
cd "${ROOT}"                                                     # uv 가 프로젝트를 찾게
echo "+ uv run --with pyinstaller python packaging/build.py --out ${OUT} $*"   # 실행 명령 표시
exec uv run --with pyinstaller python "${HERE}/build.py" --out "${OUT}" "$@"   # 빌드(남은 인자: --no-zip·--smoke)
