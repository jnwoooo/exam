// vs/NcnVs/NcnOptionsPage.cs — 도구 → 옵션 → ncn → 일반: ncn.exe 경로·config 경로·추가 인자·첨부 기본값·개발자 환경 전달
using System.ComponentModel;                                        // Category·DisplayName·Description
using System.Runtime.InteropServices;                               // Guid·ComVisible
using Microsoft.VisualStudio.Shell;                                 // DialogPage

namespace NcnVs                                                     // 확장 이름공간
{
    /// <summary>속성 표 형식 옵션 페이지(VS 가 값을 사용자 설정 저장소에 보관).</summary>
    [Guid("fff175c7-6ced-41e6-8cb9-7f9fe7a9560f")]                  // 페이지 GUID
    [ComVisible(true)]                                              // 자동화(ProvideOptionPage 의 supportsAutomation)
    public class NcnOptionsPage : DialogPage                        // 옵션 페이지
    {
        [Category("실행")]                                          // 분류
        [DisplayName("ncn.exe 경로")]                               // 이름
        [Description("비우면 VSIX 안의 ncn\\ncn.exe 를 쓴다. 다른 빌드를 쓰려면 전체 경로를 적는다.")]   // 설명
        public string NcnExePath { get; set; } = "";                // ncn 경로

        [Category("실행")]                                          // 분류
        [DisplayName("config 경로")]                                // 이름
        [Description("--config 로 넘길 Continue 설정 파일(config.yaml). 비우면 ncn 기본(~/.continue/config.yaml).")]   // 설명
        public string ConfigPath { get; set; } = "";                // 설정 파일

        [Category("실행")]                                          // 분류
        [DisplayName("추가 인자")]                                   // 이름
        [Description("ncn 에 덧붙일 인자. 예: --allow Bash --rule \"한국어로 답할 것\". 공백 구분, 큰따옴표로 묶기.")]   // 설명
        public string ExtraArguments { get; set; } = "";            // 추가 인자

        [Category("실행")]                                          // 분류
        [DisplayName("자세한 로그(--verbose)")]                     // 이름
        [Description("ncn stderr 를 DEBUG 수준으로 남긴다(출력 창 ncn·로그 파일).")]   // 설명
        public bool Verbose { get; set; } = false;                  // 자세한 로그

        [Category("실행")]                                          // 분류
        [DisplayName("VS 개발자 환경 전달")]                         // 이름
        [Description("VSINSTALLDIR·DevEnvDir·PATH(msbuild 등)를 ncn 에 넘겨 Bash 도구가 msbuild·dotnet test 를 찾게 한다.")]   // 설명
        public bool PassDeveloperEnvironment { get; set; } = true;  // 개발자 환경

        [Category("화면")]                                          // 분류
        [DisplayName("현재 파일 첨부(기본값)")]                      // 이름
        [Description("새 도구 창에서 '현재 파일' 첨부 확인란의 처음 값.")]   // 설명
        public bool AttachCurrentFileByDefault { get; set; } = false;   // 현재 파일 첨부

        [Category("화면")]                                          // 분류
        [DisplayName("선택 영역 첨부(기본값)")]                      // 이름
        [Description("새 도구 창에서 '선택 영역' 첨부 확인란의 처음 값(선택이 비어 있으면 보내지 않음).")]   // 설명
        public bool AttachSelectionByDefault { get; set; } = true;  // 선택 영역 첨부

        [Category("화면")]                                          // 분류
        [DisplayName("승인 때 비교 창 자동으로 열기")]                // 이름
        [Description("Edit·Write 승인 요청이 오면 VS 비교 창(현재 ↔ 제안)을 바로 연다.")]   // 설명
        public bool OpenDiffOnPermission { get; set; } = true;      // 비교 창 자동
    }
}
