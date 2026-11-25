# 상용 HMI의 판 올림을 지원할 수 있는가

| 항목 | 내용 |
|---|---|
| 읽는 순서 | 20 / 20 |
| 작성일 | 2026-11-25 |
| 대상 코드 | `vupdate.py` 0.9.1(코드 변경 없음. 검토 문서) |
| 근거 | 제조사 공개 문서(6절)와 합성 예제를 도구로 돌린 결과(4절). Linux에서 확인했다 |

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
| InTouch (AVEVA) | QuickScript. 제품 자체 언어 | - | 32비트 전용인 옛 스크립트 함수가 따로 표시돼 있다 |
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
