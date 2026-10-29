# rag_service/indexer.py — 색인기(로컬 실행): git 주소(환경변수) → 로컬 clone/pull → 바뀐 파일 추출 → RAG /index
# 실행: uv run python -m rag_service.indexer          (INDEX_REPOS의 모든 저장소)
#       uv run python -m rag_service.indexer shop     (이름 하나만)
# INDEX_REPOS 형식: "이름|git주소|브랜치|컬렉션;이름2|..."  (브랜치 기본 main, 컬렉션 기본 code)
#   예) shop|https://git.example.com/team/shop.git|main|code;guide|/Users/me/repos/dev-guide|main|docs
import fnmatch                                               # 파일 이름 패턴(비밀 파일 제외)
import json                                                  # 상태 파일
import pathlib                                               # 경로
import subprocess                                            # git 명령 실행
import sys                                                   # 명령줄 인자
from dataclasses import dataclass                            # 저장소 설정 묶음

INDEX_EXT = {".py", ".java", ".kt", ".ts", ".tsx", ".js", ".jsx", ".go", ".sql",
             ".md", ".yaml", ".yml", ".xml", ".properties"}  # 색인할 확장자
DENY = [".env", ".env.*", "*.pem", "*.key", "*.p12", "id_rsa*", "*.jks"]   # 비밀 파일은 색인하지 않는다
MAX_BYTES = 200_000                                          # 생성 코드·데이터 같은 큰 파일 제외
BATCH = 50                                                   # 한 번에 보낼 파일 수


@dataclass
class RepoSpec:                                              # 저장소 하나의 설정
    name: str                                                # 이름(권한 DB scopes.repo와 같아야 함)
    url: str                                                 # git 주소(원격 URL 또는 로컬 경로)
    branch: str = "main"                                     # 색인할 브랜치
    collection: str = "code"                                 # 넣을 컬렉션(code/docs)


def parse_repos(spec: str) -> list[RepoSpec]:
    """INDEX_REPOS 문자열 → RepoSpec 목록."""
    out = []
    for item in filter(None, (s.strip() for s in spec.split(";"))):   # ';'로 저장소 구분
        parts = [p.strip() for p in item.split("|")]         # '|'로 항목 구분(URL에 ':'·'@'가 있어도 안전)
        if len(parts) < 2:                                   # 이름과 주소는 필수
            raise ValueError(f"INDEX_REPOS 형식 오류: {item!r} (이름|git주소|브랜치|컬렉션)")
        out.append(RepoSpec(parts[0], parts[1],
                            parts[2] if len(parts) > 2 and parts[2] else "main",   # 브랜치 기본값
                            parts[3] if len(parts) > 3 and parts[3] else "code"))  # 컬렉션 기본값
    return out


def git(repo_dir: str, *args: str) -> str:
    """git 명령을 실행하고 표준 출력을 돌려준다(실패하면 예외)."""
    # core.quotepath=off: 한글 파일명을 "\354\275..."처럼 따옴표·8진수로 바꾸지 않고 그대로 출력(안 하면 파일을 못 찾음)
    return subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=repo_dir, capture_output=True, text=True, check=True).stdout


def sync_repo(spec: RepoSpec, cache_dir: str) -> str:
    """로컬 캐시 폴더에 저장소를 받거나(clone) 최신으로 맞춘다(fetch + reset). 폴더 경로를 돌려준다."""
    target = pathlib.Path(cache_dir) / spec.name             # 캐시/이름
    if not (target / ".git").exists():                       # 처음이면
        target.parent.mkdir(parents=True, exist_ok=True)     # 캐시 폴더 만들기
        subprocess.run(["git", "clone", "--branch", spec.branch, "--single-branch", spec.url, str(target)],
                       capture_output=True, text=True, check=True)   # 해당 브랜치만 clone
    else:                                                    # 이미 있으면
        git(str(target), "fetch", "origin", spec.branch)     # 원격 최신 받아오기
        git(str(target), "checkout", "-q", spec.branch)      # 브랜치로 이동
        git(str(target), "reset", "-q", "--hard", f"origin/{spec.branch}")   # 원격과 똑같이(색인 전용 사본이라 안전)
    return str(target)


def wanted(path: str) -> bool:
    """색인 대상 파일인지 — 확장자 OK, 비밀 파일 아님."""
    p = pathlib.PurePosixPath(path)                          # git은 '/' 경로
    return p.suffix in INDEX_EXT and not any(fnmatch.fnmatch(p.name, pat) for pat in DENY)


def read_text(full: pathlib.Path) -> str | None:
    """파일 → 텍스트(추출). 큰 파일·이진 파일은 None."""
    if not full.exists() or full.stat().st_size > MAX_BYTES:   # 없거나 너무 크면
        return None
    raw = full.read_bytes()                                  # 바이트로 읽고
    for enc in ("utf-8", "utf-8-sig", "cp949"):              # UTF-8 → BOM → 국내 레거시(CP949) 순으로 시도
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None                                              # 어느 것도 안 되면 이진 파일로 보고 건너뜀


def index_repo(repo_dir: str, repo: str, post, state_file: str, collection: str = "code") -> dict:
    """post(json) = RAG /index 호출 함수. state_file에 저장소별 마지막 색인 커밋을 기록한다(증분 색인)."""
    head = git(repo_dir, "rev-parse", "HEAD").strip()        # 현재 커밋
    state = pathlib.Path(state_file)                         # 상태 파일
    all_state = json.loads(state.read_text()) if state.exists() else {}   # {저장소: 커밋}
    last = all_state.get(repo)                               # 지난번 색인 커밋
    changed, deleted = [], []                                # 바뀐 파일, 삭제된 파일
    if last is None:                                         # 처음이면 추적 중인 파일 전체
        changed = [p for p in git(repo_dir, "ls-files").splitlines() if wanted(p)]
    elif last != head:                                       # 커밋이 바뀌었으면 차이만
        for line in git(repo_dir, "diff", "--name-status", "--no-renames", last, head).splitlines():
            status, path = line.split("\t", 1)               # 상태(A/M/D)와 경로
            if wanted(path):
                (deleted if status == "D" else changed).append(path)   # 삭제 / 추가·수정
    sent = 0                                                 # 보낸 파일 수
    for i in range(0, max(len(changed), 1), BATCH):          # 50개씩(바뀐 게 없어도 삭제 반영 위해 한 번은 돈다)
        files = []
        for p in changed[i:i + BATCH]:
            text = read_text(pathlib.Path(repo_dir) / p)     # 추출
            if text is not None:
                files.append({"path": p, "content": text})
        if files or (i == 0 and deleted):                    # 보낼 게 있을 때만 호출
            post({"collection": collection, "repo": repo, "commit": head[:12], "files": files,
                  "deleted": deleted if i == 0 else []})     # 삭제 목록은 첫 묶음에만
        sent += len(files)
    all_state[repo] = head                                   # 다 보낸 뒤에만 기록 — 중간 실패 시 다음에 다시 보냄
    state.parent.mkdir(parents=True, exist_ok=True)          # 상태 파일 폴더
    state.write_text(json.dumps(all_state, indent=1))        # 저장
    return {"repo": repo, "head": head[:12], "sent_files": sent, "deleted": len(deleted), "full": last is None}


def record_run(database_url: str, result: dict, status: str = "ok") -> None:
    """색인 결과를 Postgres index_runs에 남긴다(감시 화면에 표시)."""
    import psycopg                                           # 필요할 때만
    with psycopg.connect(database_url, autocommit=True) as con:
        con.execute("INSERT INTO index_runs(repo, commit_sha, sent_files, deleted, full_index, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (result.get("repo", ""), result.get("head", ""), result.get("sent_files", 0),
                     result.get("deleted", 0), result.get("full", False), status))


def main(argv: list[str]) -> int:
    import httpx                                             # RAG 호출
    from common.settings import get_settings                 # 설정
    s = get_settings()
    specs = parse_repos(s.index_repos)                       # 환경변수 → 저장소 목록
    if argv:                                                 # 이름을 주면 그것만
        specs = [r for r in specs if r.name in argv]
    if not specs:
        print("INDEX_REPOS가 비었거나 이름이 맞지 않습니다.")
        return 1

    def post(body: dict) -> None:                            # RAG /index 호출
        r = httpx.post(f"{s.rag_url}/index", json=body, headers={"X-Service-Token": s.index_token}, timeout=600)
        r.raise_for_status()                                 # 실패면 예외 → 상태 파일 갱신 안 됨

    code = 0
    for spec in specs:                                       # 저장소마다
        try:
            repo_dir = sync_repo(spec, s.index_cache_dir)    # clone/pull
            result = index_repo(repo_dir, spec.name, post, s.index_state, spec.collection)   # 증분 색인
            record_run(s.database_url, result)               # 기록
            print(result)
        except Exception as e:                               # 한 저장소가 실패해도 다음 저장소는 계속
            code = 1
            msg = f"{type(e).__name__}: {e}"[:300]           # 오류 요약
            print(f"[{spec.name}] 실패 — {msg}")
            try:
                record_run(s.database_url, {"repo": spec.name}, status=msg)   # 실패도 기록
            except Exception:
                pass                                         # DB까지 안 되면 출력만
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))                             # 명령줄 실행
