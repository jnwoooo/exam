# tests/fake_harness.py — 실행기 시험용 가짜 하네스: config.yaml에서 apiBase를 읽어 요청 1번 보내고 답을 출력
# cwd·날짜를 시스템 메시지에 넣어, 실행기의 정규화(<CWD>·<DATE>)가 동작하는지 확인하는 용도
import datetime, json, os, re, sys, urllib.request          # 표준 라이브러리만

cfg = open(sys.argv[sys.argv.index("--config") + 1], encoding="utf-8").read()   # 설정 파일
base = re.search(r"apiBase: (\S+)", cfg).group(1)            # 가짜 LLM 주소
prompt = sys.argv[sys.argv.index("-p") + 1]                  # 프롬프트
system = f"Working directory: {os.getcwd()}\nToday's date: {datetime.date.today()}"   # 매번 달라지는 값
body = {"model": "fake-model", "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "stream": False}  # OpenAI 호환 요청 본문(비스트리밍)
req = urllib.request.Request(base + "chat/completions", data=json.dumps(body).encode(),  # 요청 객체 구성
                             headers={"Content-Type": "application/json", "Authorization": "Bearer sk-parity", "User-Agent": sys.argv[0]})  # 인증·형식 헤더(User-Agent는 정규화로 제외됨)
answer = json.loads(urllib.request.urlopen(req).read())["choices"][0]["message"]["content"]   # 응답
if "--touch" in sys.argv:                                    # 파일 결과 차이 시험용
    open("README.md", "a").write("x")                        # 작업 폴더 파일 변경
sys.stdout.reconfigure(encoding="utf-8", newline="\n")     # Windows 파이프 stdout 은 ANSI 코드 페이지·\r\n → ncn·cn 처럼 UTF-8·\n
print(answer)                                                # 최종 응답
