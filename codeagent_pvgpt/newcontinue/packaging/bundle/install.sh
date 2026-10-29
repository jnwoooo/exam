#!/bin/sh
# install.sh — ncn 사용자 단위 설치(macOS·Linux). Windows 의 install.cmd 와 같은 순서(계획 8-1·8-2)
# 하는 일: ① ncn/ 폴더를 ~/.local/share/ncn 으로 복사 ② ~/.local/bin/ncn 링크 ③ ~/.continue/config.yaml 이 없을 때만 템플릿 복사
#          ④ 개인 토큰을 물어 ~/.continue/.env 에 기록 ⑤ ncn --doctor
# 입력(환경변수): NCN_QUIET=1(묻지 않음), NCN_SERVER(서버 주소), NCN_TOKEN(토큰)
set -eu                                                          # 오류·미정의 변수면 중단

SRC=$(cd "$(dirname "$0")" && pwd)                               # 압축 푼 폴더
DEST="${HOME}/.local/share/ncn"                                  # 설치 폴더
BIN="${HOME}/.local/bin"                                         # 실행 링크 폴더(대부분의 배포판에서 PATH 에 있음)
CDIR="${CONTINUE_GLOBAL_DIR:-${HOME}/.continue}"                 # 전역 설정 폴더(ncn·Continue 공용)
QUIET="${NCN_QUIET:-0}"                                          # 묻지 않는 모드

echo "== 1/5 파일 복사: ${DEST}"                                  # 단계 안내
[ -x "${SRC}/ncn/ncn" ] || { echo "ncn/ncn 이 없습니다(${SRC})"; exit 1; }   # 묶음 확인
rm -rf "${DEST}"                                                 # 이전 설치본 지움(옛 파일 섞임 방지)
mkdir -p "${DEST}"                                               # 설치 폴더
cp -R "${SRC}/ncn/." "${DEST}/"                                  # 실행 파일 + _internal(심볼릭 링크 유지)
for f in README.txt VERSION.txt config.template.yaml; do         # 함께 둘 파일
  if [ -f "${SRC}/${f}" ]; then cp "${SRC}/${f}" "${DEST}/"; fi  # 있으면 복사
done
if [ "$(uname -s)" = "Darwin" ]; then                            # macOS: 서명 없는 실행 파일의 격리 표시 제거(Gatekeeper)
  xattr -dr com.apple.quarantine "${DEST}" 2>/dev/null || true   # 실패해도 계속(README 의 우클릭 열기 안내)
fi

echo "== 2/5 실행 링크: ${BIN}/ncn"                                # 단계 안내
mkdir -p "${BIN}"                                                # 링크 폴더
ln -sf "${DEST}/ncn" "${BIN}/ncn"                                # 링크(덮어씀)
case ":${PATH}:" in                                              # PATH 에 있는지
  *":${BIN}:"*) echo "PATH 에 이미 있습니다." ;;                     # 있음
  *) echo "PATH 에 ${BIN} 이 없습니다. 셸 설정(~/.zshrc 또는 ~/.bashrc)에 다음 줄을 넣으세요: export PATH=\"${BIN}:\$PATH\"" ;;   # 안내만(셸 파일은 건드리지 않음)
esac

echo "== 3/5 설정 파일"                                           # 단계 안내
mkdir -p "${CDIR}"                                               # 설정 폴더
if [ -f "${CDIR}/config.yaml" ]; then                            # 이미 있으면
  echo "기존 설정을 그대로 씁니다: ${CDIR}/config.yaml"              # 건드리지 않음
else                                                             # 없을 때만
  SERVER="${NCN_SERVER:-}"                                       # 배포 도구가 준 주소
  if [ -z "${SERVER}" ] && [ "${QUIET}" != "1" ]; then           # 묻기
    printf '코드에이전트 서버 주소(예: http://10.1.2.3:8200, Enter = http://localhost:8200): '   # 질문
    read -r SERVER || SERVER=""                                  # 입력
  fi
  SERVER="${SERVER:-http://localhost:8200}"                      # 기본값
  SERVER="${SERVER%/}"; SERVER="${SERVER%/v1}"                    # 끝의 / 와 /v1 제거
  sed "s#http://localhost:8200#${SERVER}#g" "${SRC}/config.template.yaml" > "${CDIR}/config.yaml"   # 주소 바꿔 복사
  echo "설정을 만들었습니다: ${CDIR}/config.yaml (서버 ${SERVER})"    # 결과
fi

echo "== 4/5 개인 토큰"                                           # 단계 안내
ENVF="${CDIR}/.env"                                              # 토큰 파일
TOKEN="${NCN_TOKEN:-}"                                           # 배포 도구가 준 토큰
if [ -z "${TOKEN}" ] && [ "${QUIET}" != "1" ]; then              # 묻기
  printf '개인 토큰(CODEAGENT_TOKEN, Enter = 기존 값 유지/건너뜀): '   # 질문
  stty -echo 2>/dev/null || true                                 # 입력 숨기기
  read -r TOKEN || TOKEN=""                                      # 입력
  stty echo 2>/dev/null || true                                  # 되돌리기
  echo                                                           # 줄바꿈
fi
if [ -n "${TOKEN}" ]; then                                       # 새 값이 있으면
  touch "${ENVF}"; chmod 600 "${ENVF}"                            # 본인만 읽게
  grep -v -E '^[[:space:]]*(export[[:space:]]+)?CODEAGENT_TOKEN[[:space:]]*=' "${ENVF}" > "${ENVF}.tmp" || true   # 기존 토큰 줄 빼기
  echo "CODEAGENT_TOKEN=${TOKEN}" >> "${ENVF}.tmp"                # 새 줄
  mv "${ENVF}.tmp" "${ENVF}"; chmod 600 "${ENVF}"                 # 바꿔 넣기
  echo "토큰을 저장했습니다: ${ENVF}"                               # 결과(값은 출력하지 않음)
fi

echo "== 5/5 점검(ncn --doctor)"                                  # 단계 안내
"${DEST}/ncn" --doctor || echo "점검에 실패한 항목이 있습니다. 위 '할 일'을 확인하세요."   # 점검(실패해도 설치는 성공)
