# 상용 HMI의 판 올림을 지원할 수 있는가

| 항목 | 내용 |
|---|---|
| 읽는 순서 | 20 / 23 |
| 작성일 | 2026-11-25(8절 InTouch 기준 정리는 2026-11-26에 더했다) |
| 대상 코드 | `vupdate.py` 0.9.1(코드 변경 없음. 검토 문서) |
| 근거 | 제조사 공개 문서(7절, 8.8)와 합성 예제를 도구로 돌린 결과(4절, 8.5). Linux에서 확인했다 |

`18_추가_0.9.1.md` 2절에서 "상용 HMI는 내장 규칙으로 어렵다"고 적은 것은 찾아보지 않은 판단이었다. 이 문서는 그 부분을 제조사 자료로 다시 확인한 것이다.

---

## 1. 결론

- **상용 HMI의 화면과 스크립트는 이 도구의 대상이 아니다.** 널리 쓰이는 제품 가운데 화면 쪽 스크립트가 C++인 것은 없었다. 제품 자체 언어이거나 VBA, JavaScript, Python이고, 판 올림은 제조사의 변환 도구가 한다.
- **HMI 제품의 개발 키트를 쓰는 C/C++ 프로그램은 대상이 된다.** 다만 여기서의 판 올림은 대부분 "컴파일러를 올리고 키트의 include·lib를 바꾸는 일"이어서 지금의 `--from-vs`, `--to-vs`가 이미 그 축이다.
- 그래서 `--from-hmi`, `--to-hmi`는 넣지 않는다. 남는 소스 변경은 제품별로 조금씩이어서, 제품의 변경 목록을 받아 추가 규칙 파일(`--rules`)이나 요청문(`--ask`)으로 넣는 쪽이 맞다.

## 2. 제조사별 확인 결과

| 제품(제조사) | 화면 쪽 스크립트 | C/C++ 개발 키트 | 판 올림에서 확인한 것 |
|---|---|---|---|
| WinCC V7/V8 (Siemens) | ANSI-C와 VBScript. C 스크립트는 제품의 해석기가 실행한다 | ODK. C API이고 별도 옵션이다 | 함수는 `.fct`, 액션은 `.pas` 파일로 프로젝트 폴더에 있다. 화면에 붙인 스크립트는 화면 파일 안에 있다 |
| WinCC Unified (Siemens) | JavaScript | - | C 스크립트를 지원하지 않는다. WinCC V7에서 넘어오는 것은 변환이 아니라 다시 만드는 일이다 |
| WinCC OA (Siemens) | CTRL. C와 비슷한 제품 자체 언어(해석 실행) | C++ API(매니저, 드라이버, CTRL 확장) | **판마다 컴파일러가 정해져 있다.** 기존 API 프로젝트는 그 컴파일러로 바꿔 다시 컴파일해야 한다. 3.19 이후의 인터페이스 변경은 대부분 소스 수정이 필요 없다고 제조사가 적었다 |
| InTouch (AVEVA) | QuickScript. 제품 자체 언어 | 스크립트 함수 키트(C로 만든 DLL과 `.wdf`), 태그 접근 DLL(`ptacc.dll`) | 판 올림은 제품의 변환 기능이 한다. 32비트 전용인 옛 스크립트 함수가 따로 표시돼 있다. **자세한 것은 8절** |
| Plant SCADA, 구 Citect (AVEVA) | Cicode. 제품 자체 언어 | CtAPI. DLL 인터페이스(C 등에서 호출) | - |
| FactoryTalk View SE (Rockwell) | VBA | - | - |
| iFIX (GE Vernova) | VBA | 확인하지 못했다 | - |
| Ignition (Inductive Automation) | Jython 2.7(Python) | - | - |
| Qt (The Qt Company) | C++ 자체(QML은 별도 언어) | - | Qt 6은 C++17이 필요하다. Qt 5의 일부 클래스(`QRegExp`, `QTextCodec`, `QStringRef` 등)는 Qt 6 본체에서 빠져 호환 모듈로 옮겨졌다 |

WinCC OA의 컴파일러 대응(확인한 것만): 3.13~3.15는 Visual Studio 2013, 3.16은 2015, 3.18·3.19는 VC 14.2(2019), 최신 문서는 Visual C++ 19.3x(2022). 3.13~3.16은 제조사 포럼의 답변, 나머지는 제품 문서다.

## 3. C++ 문법 관점의 문제

### 3.1 C++가 아닌 문법이 섞이는 경우

| 어디에 | 무엇이 | 이 도구에 주는 영향 |
|---|---|---|
| Qt | `signals:`, `public slots:`, `emit`, `foreach`, `Q_OBJECT`. 매크로여서 전처리 뒤에는 C++다 | 4절에서 확인: 함수 경계를 틀리게 잡지 않았다 |
| Qt | `.ui`, `.qml`, `.qrc`와 moc가 만드는 `moc_*.cpp` | 앞의 셋은 읽지 않는다. `moc_*.cpp`는 생성물이므로 `--exclude`로 빼고, 판을 올린 뒤 moc를 다시 돌린다 |
| MFC | `BEGIN_MESSAGE_MAP` ~ `END_MESSAGE_MAP`, `.rc` | 4절에서 확인: 함수 경계에 영향이 없었다. `.rc`는 읽지 않는다 |
| WinCC C 스크립트 | `#pragma code("x.dll")` 같은 제품 전용 지시문 | 파일을 `.c`로 꺼내 두면 일반 C로 읽는다(3.3) |
| WinCC OA CTRL | 반환형이 없는 `main()`, `dyn_string`·`mapping` 같은 자체 타입, `#uses` | `.ctl`은 읽지 않는다 |
| Cicode | `FUNCTION` ~ `END` 꼴의 자체 문법 | `.ci`는 읽지 않는다 |

### 3.2 C++ 문법을 쓸 수 없는 경우

- **WinCC의 C 스크립트는 ANSI-C다.** 클래스, 참조, 템플릿, STL을 쓸 수 없다. 제품의 해석기가 실행하므로 Visual Studio의 판과 무관하다.
- **WinCC OA의 CTRL은 C와 비슷하지만 C가 아니다.** 문서에 적힌 차이의 예: `main()`이 값을 돌려주지 않는다, 쉼표 연산자가 없다. 클래스는 CTRL++라는 확장으로 따로 제공된다.
- **ODK, CtAPI는 C 인터페이스다.** C++ 프로그램에서 부를 수는 있지만 키트 자체는 C++ 클래스를 주지 않는다.

### 3.3 잘못 고칠 수 있는 경우(주의)

WinCC의 C 스크립트를 `.c` 파일로 꺼내 패키지 폴더에 함께 두면, 이 도구는 Visual Studio로 컴파일하는 C 파일로 본다. 4절의 시험에서 `--deprecated fix`를 주자 `strdup` → `_strdup`, `itoa` → `_itoa`, `stricmp` → `_stricmp` 치환이 그 파일에 적용됐다. 이 치환은 Visual Studio의 CRT를 위한 것이고, 그 파일은 Visual Studio로 컴파일하지 않으므로 맞지 않는 수정이다. **HMI 스크립트 폴더는 `--exclude`로 뺀다.** 기본 확장자(`.fct`, `.pas`, `.ctl`, `.ci`)는 처음부터 읽지 않는다.

## 4. 도구로 돌려 본 결과

합성 예제 9개 파일을 만들어 0.9.1로 돌렸다(Linux, sLLM 없이). 실제 제품의 코드가 아니다.

| 예제 | 읽었는가 | 함수 인식 | 규칙 |
|---|---|---|---|
| Qt 클래스(`Q_OBJECT`, `signals:`, `public slots:`, `emit`, `foreach`, `SIGNAL/SLOT`) | 읽음 | 4개 모두 정상 | `R-T0-HMI-QT`가 탐지 |
| MFC 대화상자(메시지 맵, `IMPLEMENT_DYNAMIC`, `afx_msg`) | 읽음 | 4개 모두 정상 | x64를 주면 `SetWindowLong`, 포인터 캐스팅을 탐지 |
| WinCC C 스크립트를 `.c`로 둔 것 | 읽음 | 정상 | `--deprecated fix`에서 이름 치환이 적용됨(3.3). HMI 개발 도구 규칙에는 걸리지 않음 |
| WinCC OA API 매니저(`.cxx`) | 읽음 | 2개 모두 정상 | HMI 개발 도구 규칙에 걸리지 않음(`Manager.hxx`가 목록에 없다) |
| WinCC OA CTRL(`.ctl`) | 읽지 않음 | - | - |
| 위 파일을 `.c`로 이름만 바꾼 것 | 읽음 | `main` 하나로 인식 | 문법은 읽히지만 C 규칙을 적용할 대상이 아니다 |
| Cicode(`.ci`) | 읽지 않음 | - | - |

HMI 개발 도구 규칙(`R-T0-HMI-SCADA-SDK`)은 0.6.0부터 있다. iFIX EDA, CtAPI, WinCC ODK 등의 헤더를 include하면 "대상 HMI 제품 판의 SDK로 include와 lib를 바꾸고, 제조사가 대상 Visual Studio와 x64를 지원하는지 확인한다"는 안내를 낸다. 이번 시험에서 WinCC 스크립트의 `apdefap.h`와 WinCC OA API의 헤더는 그 목록에 없다는 것을 확인했다.

## 5. 판단

| 일 | 될까 | 이유 |
|---|---|---|
| HMI 제품 안의 화면·스크립트를 새 판으로 올리기 | 안 된다 | C++가 아니고, 많은 경우 제품 형식의 파일 안에 있다. 제조사의 변환 도구가 하는 일이다 |
| 제품 세대 교체(예: WinCC V7 → Unified)의 스크립트 옮기기 | 이 도구로는 안 된다 | 언어를 바꿔 다시 쓰는 일이다. 큰 모델이면 스크립트 한 건씩 초안은 낼 수 있지만, 화면과 태그 연결은 제품 도구 안에서 다시 해야 하고 런타임 없이는 검증할 수 없다 |
| 개발 키트를 쓰는 C/C++ 프로그램의 컴파일러 올림 | 된다(지금도) | 보통의 C/C++ 코드다. `--from-vs`, `--to-vs`의 규칙이 그대로 적용된다 |
| 개발 키트의 판별 API 변경 반영 | 조건부 | 제품의 변경 목록이 있으면 규칙으로 옮길 수 있다. WinCC OA와 Qt는 공개 문서가 있다. ODK, iFIX 등은 공개 문서에서 변경 목록을 찾지 못했다(설치 매체나 지원 사이트의 문서일 것이다) |
| 위를 gemma4로 | 요청문에 바뀐 점을 적어 줄 때만 | 작은 모델이 제품별 API의 판 차이를 알고 있다고 기대하기 어렵다. "A 호출을 B로 바꾼다"처럼 적으면 보통의 수정 요청으로 처리된다 |

## 6. 하지 않은 것(제안)

코드는 바꾸지 않았다. 필요하면 다음을 더할 수 있다.

1. HMI 개발 도구 규칙의 헤더 목록에 `apdefap.h`, WinCC OA API 헤더 추가
2. HMI 스크립트로 보이는 파일(`apdefap.h`를 include하는 `.c` 등)에는 Visual Studio 규칙을 적용하지 않고 안내만 내기
3. Qt 5 → 6 규칙 묶음(없어진 클래스의 대체, C++17 요구 안내)

## 7. 출처

- WinCC 스크립트 언어: Siemens, "Scripting for WinCC and WinCC Professional" https://support.industry.siemens.com/cs/attachments/109773206/109773206_WinCC_Scripting_en.pdf
- WinCC C 스크립트의 해석 실행: Siemens 지원 포럼 답변 https://support.industry.siemens.com/forum/zh/cn/post/978999
- WinCC 함수·액션 파일의 확장자: PCS 7 매뉴얼 https://www.manualslib.com/manual/1184948/Siemens-Pcs-7.html?page=23
- WinCC/ODK: 제품 자료 https://docs.rs-online.com/03b5/A700000015667893.pdf
- WinCC Unified로의 이전: Siemens 지원 포럼 답변 https://support.industry.siemens.com/forum/cn/tc/post/1243958 , 2차 자료 https://industrialmonitordirect.com/blogs/knowledgebase/converting-wincc-rt-pro-to-wincc-unified-v18-migration-guide
- WinCC OA CTRL: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/Control_Grundlagen/Control_Grundlagen-15.html , https://www.winccoa.com/documentation/WinCCOA/latest/en_US/Control_Grundlagen/Control_Grundlagen-08.html
- WinCC OA API의 컴파일러 요구: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/WCCOA_Voraussetzungen/sw_req_api_development.html , https://www.winccoa.com/documentation/WinCCOA/latest/en_US/API/topics/API-02.html , 포럼 https://www.winccoa.com/forum-archive/viewtopic.php@t=10711.html
- WinCC OA API의 인터페이스 변경: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/API/topics/api_notes_319.html
- InTouch QuickScript: https://docs.aveva.com/bundle/intouch-hmi-2026/page/712894.html , https://docs.aveva.com/bundle/intouch-hmi-2026/page/313369.html
- Plant SCADA의 Cicode와 CtAPI: https://docs.aveva.com/bundle/plant-scada/page/1130115.html , https://docs.aveva.com/bundle/plant-scada/page/1150498.html
- FactoryTalk View SE의 VBA: https://www.rockwellautomation.com/en-us/docs/factorytalk-view/16-00-00/se-help-ditamap/ftvse-client-object-model/use-vba-display-code/vba-display-code.html
- iFIX의 VBA: https://gevernova.com/software/documentation/ifix/version65/understanding-ifix/und_visual_basic_for_applications_vba.htm
- Ignition의 Jython: https://docs.inductiveautomation.com/docs/8.1/platform/scripting/python-scripting
- Qt 6 포팅: https://doc.qt.io/qt-6.10/portingguide.html , https://www.qt.io/blog/porting-from-qt-5-to-qt-6-using-qt5compat-library , C++17 요구는 The Qt Company 발표 자료 https://conf.kde.org/event/1/contributions/19/attachments/17/18/2106-PortingToQt6%20(Final%20Version).pdf

## 8. InTouch 기준 정리

2026-11-26에 더한 절이다. InTouch(AVEVA, 구 Wonderware)에 해당하는 내용만 모았다. 근거는 8.8의 문서와, 합성 예제를 0.9.1로 돌린 결과(8.5)다.

### 8.1 결론

- **같은 InTouch 안에서 판만 올리는 일은 어렵지 않다. 다만 그 일은 InTouch가 스스로 한다.** 옛 애플리케이션을 새 판에서 열면 제품이 변환한다. 이 도구가 맡을 부분이 없다.
- **이 도구의 대상은 InTouch에 붙여 쓰는 C/C++ 소스뿐이다.** 스크립트 함수 DLL의 소스와, 태그 접근 DLL(`ptacc.dll`)을 쓰는 바깥 프로그램의 소스다. 보통의 C/C++ 코드여서 `--from-vs`, `--to-vs`로 다룬다.
- InTouch에서 Industrial Graphics나 OMI로 옮기는 것은 판 올림이 아니라 다시 구성하는 일이다(8.4).

### 8.2 InTouch에서 코드가 있는 곳

| 무엇 | 언어 | 어디에 | 이 도구의 대상인가 |
|---|---|---|---|
| 화면과 스크립트 | QuickScript(제품 자체 언어) | 애플리케이션 폴더(제품 형식) | 아니다 |
| QuickFunction | QuickScript로 쓴 재사용 함수 | 위와 같다 | 아니다 |
| 스크립트 함수 DLL(스크립트 확장) | C. 제조사 기술 노트는 ANSI C 컴파일러를 권한다 | DLL과 `.wdf` 파일. 소스는 `.c`, `.def`, `.rc` | **소스가 있으면 대상이다** |
| .NET 스크립트 라이브러리, 스크립트 라이브러리 파일 | .NET | `.dll`, `.aaSLIB` | 아니다(C++가 아니다) |
| 태그 접근 DLL을 쓰는 바깥 프로그램 | 그 프로그램을 쓴 언어 | `ptacc.dll`, `wwheap.dll`을 불러 쓴다 | **C/C++로 썼으면 대상이다** |

지금 판의 InTouch도 `.wdf` 스크립트 확장 파일을 가져올 수 있다. 가져오기 대상에 .NET 라이브러리, `.aaSLIB`와 함께 들어 있다.

### 8.3 InTouch → InTouch: 제품이 하는 일과 남는 일

제품이 하는 일(InTouch 2026 문서 기준).

- 옛 애플리케이션을 WindowMaker나 WindowViewer로 열면 변환 창이 뜬다. 백업을 만들 폴더를 정하고 변환한다.
- 7.11 P07 이후 판에서 만든 애플리케이션이 변환 대상이다. 2023 문서의 표에서 7.0과 7.1은 변환 불가로 표시돼 있다.
- 설치본을 바로 올리는 것(upgrade)은 11.1 SP1 이후 판이고 64비트 OS에 설치돼 있을 때 된다.

변환 뒤 사람이 손봐야 한다고 확인한 것.

| 항목 | 내용 |
|---|---|
| 32비트 전용 옛 스크립트 함수 | `WWPoke`, `WWExecute`, `WWRequest`, `ActivateApp`, `SendKeys`는 64비트 Windows에서 동작하지 않는다. 문서도 이런 함수를 64비트 Windows용 스크립트에 넣지 말라고 한다 |
| SPCPro | 제품에서 빠졌다. SPCPro 구성요소가 든 애플리케이션은 열리지도 변환되지도 않는다. 제조사는 QI Analyst로 옮기라고 한다 |
| `StringCompareEncrypted` | 2026으로 올리면 실행해도 효과가 없고 늘 0을 돌려준다 |
| 7.11 이전의 경보 객체 | 변환하면 분산 경보 객체로 바뀌고 기본값이 된다. 색, 글꼴, 식, 조회 설정은 넘어오지 않는다 |

이 항목들은 QuickScript와 화면 안의 일이어서 WindowMaker에서 고친다. 이 도구가 읽는 파일이 아니다.

### 8.4 InTouch → Industrial Graphics, OMI, 웹 클라이언트

- InTouch 애플리케이션을 OMI 애플리케이션으로 통째로 바꾸는 기능은 없다(AVEVA 커뮤니티의 답변). 관리형 InTouch 애플리케이션의 창을 Industrial Graphics로 변환하는 기능을 쓰고, 그것으로 다시 구성한다.
- 그 변환에서 InTouch 함수나 QuickScript 함수를 쓴 스크립트는 변환되지 않는다. ActiveX 컨트롤, OCX DLL, DLL 객체는 결과에 들어가지 않는다. 변환 중에 스크립트 검증 경고가 나오지 않으므로 변환 뒤에 따로 검증해야 한다.
- 웹 클라이언트로 보여 줄 때는 .NET 스크립트를 .NET 표준 이름공간에 맞게 고쳐야 할 수 있고, 대응하는 것이 없으면 그 스크립트는 지원되지 않는다.

모두 제품 도구 안에서 하는 일이다.

### 8.5 C 소스 쪽을 도구로 돌려 본 결과

합성 예제 7개 파일을 만들어 0.9.1로 돌렸다(Linux, sLLM 없이). 실제 제품의 키트나 코드가 아니고, 태그 접근 함수의 이름은 기억에 따라 적은 것이다.

| 파일 | 읽었는가 | 결과 |
|---|---|---|
| 스크립트 함수 DLL의 `.c` 2개 | 읽음 | 함수 5개 모두 정상(`WINAPI` 함수 4개, `DllMain`) |
| `.def`, `.rc` | 글 파일로 분류 | 함수 분석 대상이 아니다 |
| `.wdf` | 읽지 않음 | - |
| 태그 접근 프로그램 `.cpp` | 읽음 | 함수 4개 모두 정상. `ptacc.h`를 HMI 개발 도구 규칙(`R-T0-HMI-SCADA-SDK`)이 탐지해 설정 안내를 냈다 |
| 스크립트를 글로 꺼낸 `.txt` | 읽지 않음 | - |

옵션에 따른 차이.

- 기본(`--from-vs 2010 --to-vs 2022`): 소스를 바꾸지 않았다. `sprintf`, `itoa` 등의 경고는 설정으로 흡수했다.
- `--deprecated fix`: `itoa` → `_itoa`, `stricmp` → `_stricmp`로 바꿨다. 이 DLL은 Visual Studio로 컴파일하므로 여기서는 맞는 수정이다. 3.3의 WinCC 스크립트와 다른 점이다.
- `--platform x64`: `SetWindowLong` 줄을 탐지해 수동 검토로 올렸다(`--compat-llm 3`이면 sLLM에 보낸다). `return (long)p;`처럼 **변수인 포인터를 정수로 바꾸는 줄은 잡지 못했다.** 규칙이 변수의 타입을 모르기 때문이다.

### 8.6 주의할 것과 확인하지 못한 것

- **`--platform x64`는 확인한 뒤에만 준다.** DLL은 그것을 불러 쓰는 프로세스와 비트 수가 같아야 한다. 대상 판의 WindowViewer가 32비트인지 64비트인지는 제조사 공개 문서에서 찾지 못했다. 설치된 `view.exe`로 확인하거나 제조사에 물어야 한다.
- 태그 접근 DLL(`ptacc.dll`, `wwheap.dll`)의 쓰임은 제3자 제품의 문서에서 확인했다. 제조사의 키트 문서는 찾지 못했다. 사용자 글에는 "제조사가 이 키트를 지원하지 않는다", "10.1에서는 됐는데 12에서는 안 됐다", "64비트 OS에서 초기화 함수가 멈춘다는 기술 노트가 있다"는 말이 있다. 확인된 사실이 아니므로 대상 판에서 직접 시험해야 한다.
- 스크립트 함수 키트의 기술 노트는 옛 판(FactorySuite 2000) 기준이다. 지금 판의 키트가 어떤 Visual Studio를 요구하는지는 확인하지 못했다.
- InTouch의 스크립트를 글 파일로 꺼내는 방법은 확인하지 않았다.

### 8.7 판단

| 일 | 될까 | 누가 하는가 |
|---|---|---|
| InTouch 애플리케이션의 판 올림 | 된다 | InTouch의 변환 기능 |
| 변환 뒤 남는 스크립트 수정(8.3의 표) | 된다 | 사람이 WindowMaker에서. 확인한 항목은 넷이다 |
| 스크립트 함수 DLL, 태그 접근 프로그램의 컴파일러 올림 | 된다 | 이 도구(`--from-vs`, `--to-vs`). 소스가 패키지에 있을 때 |
| 위 소스의 x64 전환 | 조건부 | 비트 수를 확인한 뒤에. 포인터 잘림은 일부만 탐지한다 |
| Industrial Graphics·OMI로 옮기기 | 이 도구로는 안 된다 | 제품의 창 변환 기능과 사람 |

`--from-hmi`, `--to-hmi`를 InTouch용으로 넣을 이유는 없다. InTouch의 판에 따라 C 소스를 바꿔야 한다는 공개된 변경 목록을 찾지 못했고, 스크립트 쪽 변경은 이 도구가 읽는 파일이 아니다.

### 8.8 출처(InTouch)

- 스크립트 언어와 QuickFunction: https://docs.aveva.com/bundle/intouch-hmi-2026/page/313282.html
- 스크립트 함수 라이브러리 가져오기(.NET, `.aaSLIB`, `.wdf`): https://docs.aveva.com/bundle/intouch-hmi/page/332367.html
- 스크립트 함수 키트: Wonderware Tech Note 162(배포사 사본) https://cdn.logic-control.com/docs/aveva/Tech%20Notes/Tech%20Note%20162%20-%20Script%20SDK%20Implementation%20Basics.pdf
- 태그 접근 DLL: Software Toolbox의 InTouch 클라이언트 드라이버 설명서 https://help.softwaretoolbox.com/hubfs/faq/files/documentation/topserver/intouch-client-manual.pdf?hsLang=en , 사용자 글 https://static.codeproject.com/Messages/4816187/Re-using-ptacc-dll-under-Windows-7-x64
- 애플리케이션 변환: https://docs.aveva.com/bundle/intouch-hmi-2026/page/315541.html , 변환 범위 https://docs.aveva.com/bundle/intouch-hmi-2026/page/368445.html , 2023의 표 https://docs.aveva.com/bundle/intouch-hmi-2023/page/368445.html , 7.11 이전 https://docs.aveva.com/bundle/intouch-hmi-2026/page/315542.html
- 설치본 올림: https://docs.aveva.com/bundle/intouch-hmi-2026/page/368441.html
- 32비트 전용 스크립트 함수: https://docs.aveva.com/bundle/intouch-hmi-2023/page/321235.html , 제품 ReadMe(배포사 사본) https://cdn.logic-control.com/docs/Wonderware/InTouch/ReadMe.html
- 2026 릴리스 노트(`StringCompareEncrypted`): https://docs.aveva.com/bundle/intouch-hmi-2026/page/InTouchHMI_ReleaseNotes.html
- 창을 Industrial Graphics로 변환: https://docs.aveva.com/bundle/intouch-hmi-2026/page/315640.html , 제한 https://docs.aveva.com/bundle/intouch-hmi-2026/page/315644.html , 스크립트 변환의 예외 https://docs.aveva.com/bundle/intouch-hmi-2023/page/315643.html
- OMI로의 통째 변환이 없음: AVEVA 커뮤니티 답변 https://softwareforums.aveva.com/heroes-hq/f/aveva-system-platform-and-omi/83417/guidelines-or-documentation-on-converting-an-intouch-for-system-platform-application-to-omi/312139
- 웹 클라이언트의 .NET 스크립트: https://docs.aveva.com/bundle/intouch-hmi-2026/page/1612713.html
