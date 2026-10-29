// vs/NcnVs.Core/Acp/AcpModels.cs — ACP v1 메시지 모델(메서드 이름·내용 블록·승인·fs·질문·_ncn 확장) (규격: ACP_연동.md 3장·5장)
using System;                                                       // 기본 형식
using System.Collections.Generic;                                   // 목록
using System.IO;                                                    // Path
using Newtonsoft.Json.Linq;                                         // JObject

namespace NcnVs.Core.Acp                                            // ACP 이름공간
{
    /// <summary>ACP 메서드 이름(줄 JSON 의 method 값). 확장은 앞에 '_' 가 붙는다(SDK 규칙).</summary>
    public static class AcpMethods                                  // 메서드 상수
    {
        public const string Initialize = "initialize";              // 연결 시작
        public const string SessionNew = "session/new";             // 새 세션
        public const string SessionLoad = "session/load";           // 세션 이어 가기
        public const string SessionPrompt = "session/prompt";       // 질문 한 턴
        public const string SessionCancel = "session/cancel";       // 중단(알림)
        public const string SessionSetMode = "session/set_mode";    // 모드 바꾸기
        public const string SessionSetConfigOption = "session/set_config_option";   // 설정 선택지 바꾸기
        public const string SessionUpdate = "session/update";       // ncn → IDE 진행 알림
        public const string RequestPermission = "session/request_permission";   // ncn → IDE 승인 요청
        public const string FsReadTextFile = "fs/read_text_file";   // ncn → IDE 읽기 위임
        public const string FsWriteTextFile = "fs/write_text_file"; // ncn → IDE 쓰기 위임
        public const string ElicitationCreate = "elicitation/create";   // ncn → IDE 질문(폼)
        public const string NcnAsk = "_ncn/ask";                    // ncn 확장 질문(요청)
        public const string NcnRetry = "_ncn/retry";                // ncn 확장: LLM 재시도 직전(알림)
        public const string NcnSystemMessage = "_ncn/system_message";   // ncn 확장: 압축 안내 등(알림)
        public const int ProtocolVersion = 1;                       // 규격 버전
    }

    /// <summary>prompt 내용 블록 만들기(text·resource_link·resource). 3-4 표 형식 그대로.</summary>
    public static class ContentBlocks                               // 블록 생성기
    {
        public static JObject Text(string text)                     // 글 블록
        {
            return new JObject { ["type"] = "text", ["text"] = text ?? "" };   // {"type":"text","text":…}
        }

        /// <summary>파일 링크(ncn 이 내용을 읽어 @경로 첨부). 현재 파일 첨부에 쓴다.</summary>
        public static JObject ResourceLink(string path)             // 파일 링크 블록
        {
            return new JObject                                      // {"type":"resource_link",…}
            {
                ["type"] = "resource_link",                         // 종류
                ["name"] = Path.GetFileName(path),                  // 표시 이름(파일 이름)
                ["uri"] = FileUri(path),                            // file:/// URI
            };
        }

        /// <summary>내장 글(선택 영역). 줄 범위가 있으면 URI 조각 #L시작-끝(1부터) → ncn 이 이름 뒤에 붙인다.</summary>
        public static JObject Resource(string path, string text, int startLine = 0, int endLine = 0)   // 선택 영역 블록
        {
            var uri = FileUri(path);                                // 파일 URI
            if (startLine > 0) uri += "#L" + startLine + "-" + (endLine >= startLine ? endLine : startLine);   // 줄 범위 조각
            return new JObject                                      // {"type":"resource",…}
            {
                ["type"] = "resource",                              // 종류
                ["resource"] = new JObject                          // 내장 자원
                {
                    ["uri"] = uri,                                  // URI(조각 포함)
                    ["text"] = text ?? "",                          // 선택 글
                    ["mimeType"] = MimeTypeOf(path),                // 글 종류
                },
            };
        }

        /// <summary>경로 → file:/// URI. 한글·공백·# 은 %xx 로(ncn 은 unquote 로 푼다).</summary>
        public static string FileUri(string path)                   // URI 만들기
        {
            return new Uri(Path.GetFullPath(path)).AbsoluteUri;     // C:\a\b.cs → file:///C:/a/b.cs
        }

        /// <summary>확장자 → mimeType(모르면 text/plain).</summary>
        public static string MimeTypeOf(string path)                // 글 종류
        {
            var ext = (Path.GetExtension(path) ?? "").ToLowerInvariant();   // 확장자
            switch (ext)                                            // 흔한 것만
            {
                case ".cs": return "text/x-csharp";                 // C#
                case ".vb": return "text/x-vb";                     // VB
                case ".cpp": case ".cc": case ".cxx": case ".h": case ".hpp": return "text/x-c++";   // C++
                case ".c": return "text/x-c";                       // C
                case ".py": return "text/x-python";                 // 파이썬
                case ".js": return "text/javascript";               // JS
                case ".ts": return "text/x-typescript";             // TS
                case ".json": return "application/json";            // JSON
                case ".xml": case ".csproj": case ".props": case ".targets": case ".xaml": return "application/xml";   // XML 류
                case ".md": return "text/markdown";                 // 마크다운
                case ".sql": return "text/x-sql";                   // SQL
                default: return "text/plain";                       // 기본
            }
        }
    }

    /// <summary>승인 선택지 하나(optionId·name·kind). name 은 cn 원문 그대로 버튼에 쓴다.</summary>
    public sealed class PermissionOption                            // 승인 선택지
    {
        public string OptionId { get; set; }                        // allow_once 등
        public string Name { get; set; }                            // "Continue" 등
        public string Kind { get; set; }                            // allow_once·allow_always·reject_once·reject_always
    }

    /// <summary>도구 내용 하나(diff·content·terminal).</summary>
    public sealed class ToolContent                                 // 도구 내용
    {
        public string Type { get; set; }                            // "diff"·"content"·"terminal"
        public string Path { get; set; }                            // diff 경로
        public string OldText { get; set; }                         // diff 이전 글(새 파일이면 null)
        public string NewText { get; set; }                         // diff 새 글
        public string Text { get; set; }                            // content 의 글

        public bool IsDiff => Type == "diff";                       // diff 인지
    }

    /// <summary>도구 호출(tool_call / tool_call_update / 승인 요청의 toolCall). null 필드 = "바뀌지 않음".</summary>
    public sealed class ToolCallInfo                                // 도구 호출
    {
        public string ToolCallId { get; set; }                      // 카드 id
        public string Title { get; set; }                           // "Edit(Coupon.cs)"
        public string Kind { get; set; }                            // read·edit·execute·search·fetch·other
        public string Status { get; set; }                          // pending·in_progress·completed·failed
        public List<ToolContent> Content { get; set; }              // 내용(없으면 null)
        public JToken RawInput { get; set; }                        // 모델이 준 인자
        public JToken RawOutput { get; set; }                       // 결과(글 등)
        public List<string> Locations { get; set; }                 // 관련 경로들

        /// <summary>JSON(tool_call 업데이트 또는 toolCall 객체) → 모델.</summary>
        public static ToolCallInfo FromJson(JObject o)              // 해석
        {
            if (o == null) return null;                             // 없으면 null
            var info = new ToolCallInfo                             // 기본 필드
            {
                ToolCallId = o.Value<string>("toolCallId"),         // id
                Title = o.Value<string>("title"),                   // 제목
                Kind = o.Value<string>("kind"),                     // 종류
                Status = o.Value<string>("status"),                 // 상태
                RawInput = o["rawInput"],                           // 인자
                RawOutput = o["rawOutput"],                         // 결과
            };
            var content = o["content"] as JArray;                   // 내용 배열
            if (content != null)                                    // 있으면
            {
                info.Content = new List<ToolContent>();             // 목록
                foreach (var c in content)                          // 항목마다
                {
                    var co = c as JObject;                          // 객체만
                    if (co == null) continue;                       // 아니면 건너뜀
                    var type = co.Value<string>("type");            // 종류
                    var tc = new ToolContent { Type = type };       // 내용 하나
                    if (type == "diff")                             // diff
                    {
                        tc.Path = co.Value<string>("path");         // 경로
                        tc.OldText = co.Value<string>("oldText");   // 이전(없으면 null)
                        tc.NewText = co.Value<string>("newText");   // 새 글
                    }
                    else if (type == "content")                     // 일반 내용
                    {
                        var inner = co["content"] as JObject;       // 내용 블록
                        tc.Text = inner != null ? inner.Value<string>("text") : null;   // 글 블록의 글
                    }
                    else if (type == "terminal")                    // 터미널(ncn 은 안 씀)
                    {
                        tc.Text = "[terminal " + co.Value<string>("terminalId") + "]";   // 표시만
                    }
                    info.Content.Add(tc);                           // 추가
                }
            }
            var locs = o["locations"] as JArray;                    // 위치 배열
            if (locs != null)                                       // 있으면
            {
                info.Locations = new List<string>();                // 목록
                foreach (var l in locs)                             // 항목마다
                {
                    var p = (l as JObject)?.Value<string>("path");  // 경로
                    if (!string.IsNullOrEmpty(p)) info.Locations.Add(p);   // 추가
                }
            }
            return info;                                            // 결과
        }

        /// <summary>첫 diff 내용(없으면 null) — 비교 창 열기에 쓴다.</summary>
        public ToolContent FirstDiff()                              // 첫 diff
        {
            if (Content == null) return null;                       // 내용 없음
            foreach (var c in Content) if (c.IsDiff) return c;      // diff 찾기
            return null;                                            // 없음
        }
    }

    /// <summary>session/request_permission 요청.</summary>
    public sealed class PermissionRequest                           // 승인 요청
    {
        public string SessionId { get; set; }                       // 세션
        public ToolCallInfo ToolCall { get; set; }                  // 도구 내용
        public List<PermissionOption> Options { get; set; } = new List<PermissionOption>();   // 선택지

        public static PermissionRequest FromJson(JObject p)         // 해석
        {
            var req = new PermissionRequest                         // 기본
            {
                SessionId = p.Value<string>("sessionId"),           // 세션
                ToolCall = ToolCallInfo.FromJson(p["toolCall"] as JObject) ?? new ToolCallInfo(),   // 도구
            };
            var opts = p["options"] as JArray;                      // 선택지
            if (opts != null)                                       // 있으면
            {
                foreach (var o in opts)                             // 하나씩
                {
                    var oo = o as JObject;                          // 객체
                    if (oo == null) continue;                       // 아니면 건너뜀
                    req.Options.Add(new PermissionOption            // 추가
                    {
                        OptionId = oo.Value<string>("optionId"),    // id
                        Name = oo.Value<string>("name"),            // 이름(그대로 버튼)
                        Kind = oo.Value<string>("kind"),            // 종류
                    });
                }
            }
            return req;                                             // 결과
        }
    }

    /// <summary>승인 결과(선택 또는 취소).</summary>
    public sealed class PermissionOutcome                           // 승인 결과
    {
        public bool Cancelled { get; private set; }                 // 취소인지
        public string OptionId { get; private set; }                // 선택한 id

        public static PermissionOutcome Selected(string optionId)   // 선택
        {
            return new PermissionOutcome { OptionId = optionId };   // 선택 결과
        }

        public static readonly PermissionOutcome Cancel = new PermissionOutcome { Cancelled = true };   // 취소 결과

        public JObject ToJson()                                     // 응답 result
        {
            var outcome = Cancelled                                 // 결과 객체
                ? new JObject { ["outcome"] = "cancelled" }         // {"outcome":"cancelled"}
                : new JObject { ["outcome"] = "selected", ["optionId"] = OptionId };   // {"outcome":"selected","optionId":…}
            return new JObject { ["outcome"] = outcome };           // {"outcome":{…}}
        }
    }

    /// <summary>fs/read_text_file 요청(line 은 1부터, limit 은 줄 수 — 둘 다 선택).</summary>
    public sealed class ReadTextFileRequest                         // 읽기 요청
    {
        public string SessionId { get; set; }                       // 세션
        public string Path { get; set; }                            // 절대 경로
        public int? Line { get; set; }                              // 시작 줄
        public int? Limit { get; set; }                             // 줄 수

        public static ReadTextFileRequest FromJson(JObject p)       // 해석
        {
            return new ReadTextFileRequest                          // 필드
            {
                SessionId = p.Value<string>("sessionId"),           // 세션
                Path = p.Value<string>("path"),                     // 경로
                Line = p.Value<int?>("line"),                       // 시작 줄
                Limit = p.Value<int?>("limit"),                     // 줄 수
            };
        }
    }

    /// <summary>fs/write_text_file 요청.</summary>
    public sealed class WriteTextFileRequest                        // 쓰기 요청
    {
        public string SessionId { get; set; }                       // 세션
        public string Path { get; set; }                            // 절대 경로
        public string Content { get; set; }                         // 파일 전체 글

        public static WriteTextFileRequest FromJson(JObject p)      // 해석
        {
            return new WriteTextFileRequest                         // 필드
            {
                SessionId = p.Value<string>("sessionId"),           // 세션
                Path = p.Value<string>("path"),                     // 경로
                Content = p.Value<string>("content") ?? "",         // 글
            };
        }
    }

    /// <summary>질문 선택지 하나(값·보일 글).</summary>
    public sealed class QuestionChoice                              // 선택지
    {
        public string Value { get; set; }                           // 응답 값(const)
        public string Title { get; set; }                           // 보일 글
    }

    /// <summary>
    /// 질문(elicitation/create 폼 또는 _ncn/ask). 폼 스키마의 oneOf 속성 → 선택지, 글 속성(custom·answer) → 직접 입력 칸.
    /// </summary>
    public sealed class QuestionRequest                             // 질문
    {
        public string SessionId { get; set; }                       // 세션
        public string ToolCallId { get; set; }                      // 연결된 카드
        public string Message { get; set; }                         // 질문 글
        public List<QuestionChoice> Choices { get; set; } = new List<QuestionChoice>();   // 선택지
        public string ChoiceKey { get; set; }                       // 선택 값을 담을 키(choice)
        public string TextKey { get; set; }                         // 직접 입력 키(custom·answer)
        public string TextTitle { get; set; }                       // 직접 입력 칸 설명
        public string DefaultAnswer { get; set; }                   // 기본 답(_ncn/ask)
        public bool IsNcnAsk { get; set; }                          // _ncn/ask 로 왔는지

        /// <summary>elicitation/create params → 질문. mode 가 객체면 그 안, 글이면 params 자체에서 찾는다.</summary>
        public static QuestionRequest FromElicitation(JObject p)    // 폼 질문 해석
        {
            var mode = p["mode"] as JObject;                        // SDK 가 mode 를 객체로 줄 때
            var src = mode ?? p;                                    // 필드 찾을 곳
            var q = new QuestionRequest                             // 기본
            {
                SessionId = src.Value<string>("sessionId") ?? p.Value<string>("sessionId"),   // 세션
                ToolCallId = src.Value<string>("toolCallId") ?? p.Value<string>("toolCallId"),   // 카드
                Message = p.Value<string>("message") ?? src.Value<string>("message") ?? "",   // 질문 글
            };
            var schema = (src["requestedSchema"] ?? p["requestedSchema"]) as JObject;   // 폼 스키마
            var props = schema != null ? schema["properties"] as JObject : null;   // 속성들
            if (props == null) return q;                            // 스키마 없음
            foreach (var prop in props.Properties())                // 속성마다(순서 유지)
            {
                var def = prop.Value as JObject;                    // 속성 정의
                if (def == null) continue;                          // 아니면 건너뜀
                var oneOf = def["oneOf"] as JArray;                 // 선택지(const·title)
                var enm = def["enum"] as JArray;                    // 또는 enum
                if (q.ChoiceKey == null && (oneOf != null || enm != null))   // 첫 선택 속성
                {
                    q.ChoiceKey = prop.Name;                        // 키(choice)
                    if (oneOf != null)                              // oneOf 형식
                        foreach (var o in oneOf)                    // 하나씩
                        {
                            var oo = o as JObject;                  // 객체
                            if (oo == null) continue;               // 아니면 건너뜀
                            var v = oo.Value<string>("const");      // 값
                            q.Choices.Add(new QuestionChoice { Value = v, Title = oo.Value<string>("title") ?? v });   // 추가
                        }
                    else                                            // enum 형식
                        foreach (var v in enm) q.Choices.Add(new QuestionChoice { Value = (string)v, Title = (string)v });   // 추가
                }
                else if (q.TextKey == null && def.Value<string>("type") == "string")   // 첫 글 속성
                {
                    q.TextKey = prop.Name;                          // 키(custom·answer)
                    q.TextTitle = def.Value<string>("title") ?? prop.Name;   // 설명
                }
            }
            return q;                                               // 결과
        }

        /// <summary>_ncn/ask params({sessionId, toolCallId, question, options, defaultAnswer}) → 질문.</summary>
        public static QuestionRequest FromNcnAsk(JObject p)         // 확장 질문 해석
        {
            var q = new QuestionRequest                             // 기본
            {
                SessionId = p.Value<string>("sessionId"),           // 세션
                ToolCallId = p.Value<string>("toolCallId"),         // 카드
                Message = p.Value<string>("question") ?? "",        // 질문
                DefaultAnswer = p.Value<string>("defaultAnswer"),   // 기본 답
                IsNcnAsk = true,                                    // 확장 질문
                TextKey = "answer",                                 // 직접 입력 가능
                TextTitle = "Custom answer (optional)",             // 설명(폼과 같은 글)
            };
            var opts = p["options"] as JArray;                      // 선택지(글 목록)
            if (opts != null)                                       // 있으면
            {
                q.ChoiceKey = "choice";                             // 내부 키
                foreach (var o in opts)                             // 하나씩
                {
                    var s = o.Type == JTokenType.String ? (string)o : o.ToString();   // 글로
                    q.Choices.Add(new QuestionChoice { Value = s, Title = s });   // 추가
                }
            }
            return q;                                               // 결과
        }
    }

    /// <summary>질문 답(수락·거절·취소).</summary>
    public sealed class QuestionAnswer                              // 답
    {
        public string Action { get; set; } = "accept";              // accept·decline·cancel
        public string Choice { get; set; }                          // 고른 값
        public string Text { get; set; }                            // 직접 입력

        public static QuestionAnswer Decline() { return new QuestionAnswer { Action = "decline" }; }   // 건너뛰기
        public static QuestionAnswer Cancel() { return new QuestionAnswer { Action = "cancel" }; }     // 취소

        /// <summary>elicitation/create 응답 result.</summary>
        public JObject ToElicitationResult(QuestionRequest q)       // 폼 응답
        {
            var r = new JObject { ["action"] = Action };            // {"action":…}
            if (Action != "accept") return r;                       // 거절·취소는 내용 없음
            var content = new JObject();                            // 내용
            if (!string.IsNullOrEmpty(Choice) && q.ChoiceKey != null) content[q.ChoiceKey] = Choice;   // 선택 값
            if (!string.IsNullOrEmpty(Text) && q.TextKey != null) content[q.TextKey] = Text;   // 직접 입력
            r["content"] = content;                                 // 붙임
            return r;                                               // 결과
        }

        /// <summary>_ncn/ask 응답 result({answer, isCustom}).</summary>
        public JObject ToNcnAskResult(QuestionRequest q)            // 확장 응답
        {
            if (Action != "accept") return new JObject { ["answer"] = q.DefaultAnswer ?? "", ["isCustom"] = false };   // 기본 답
            var custom = (Text ?? "").Trim();                       // 직접 입력
            if (custom.Length > 0) return new JObject { ["answer"] = custom, ["isCustom"] = true };   // 직접 입력 우선(ncn 과 같은 규칙)
            return new JObject { ["answer"] = Choice ?? q.DefaultAnswer ?? "", ["isCustom"] = false };   // 선택
        }
    }

    /// <summary>세션 모드 하나(normal·plan·auto).</summary>
    public sealed class SessionModeInfo                             // 모드
    {
        public string Id { get; set; }                              // id
        public string Name { get; set; }                            // 이름
        public string Description { get; set; }                     // 설명
    }

    /// <summary>session/new·load 응답.</summary>
    public sealed class SessionOpenResult                           // 세션 열기 결과
    {
        public string SessionId { get; set; }                       // 세션 id(load 는 요청 값)
        public string CurrentModeId { get; set; }                   // 현재 모드
        public List<SessionModeInfo> Modes { get; set; } = new List<SessionModeInfo>();   // 모드 목록

        public static SessionOpenResult FromJson(JToken t, string fallbackSessionId)   // 해석
        {
            var o = t as JObject ?? new JObject();                  // 객체(없으면 빈 것)
            var r = new SessionOpenResult { SessionId = o.Value<string>("sessionId") ?? fallbackSessionId };   // id
            var modes = o["modes"] as JObject;                      // 모드 상태
            if (modes != null)                                      // 있으면
            {
                r.CurrentModeId = modes.Value<string>("currentModeId");   // 현재
                var avail = modes["availableModes"] as JArray;      // 목록
                if (avail != null)                                  // 있으면
                    foreach (var m in avail)                        // 하나씩
                    {
                        var mo = m as JObject;                      // 객체
                        if (mo == null) continue;                   // 아니면 건너뜀
                        r.Modes.Add(new SessionModeInfo { Id = mo.Value<string>("id"), Name = mo.Value<string>("name"), Description = mo.Value<string>("description") });   // 추가
                    }
            }
            return r;                                               // 결과
        }
    }

    /// <summary>session/prompt 응답.</summary>
    public sealed class PromptResult                                // 턴 결과
    {
        public string StopReason { get; set; }                      // end_turn·cancelled·…
        public bool StoppedByRejection { get; set; }                // 승인 3번(아니오)으로 멈춤
        public long? TotalTokens { get; set; }                      // 사용량 합
        public long? InputTokens { get; set; }                      // 입력 토큰
        public long? OutputTokens { get; set; }                     // 출력 토큰

        public static PromptResult FromJson(JToken t)               // 해석
        {
            var o = t as JObject ?? new JObject();                  // 객체
            var r = new PromptResult { StopReason = o.Value<string>("stopReason") };   // 멈춘 이유
            var meta = o["_meta"] as JObject;                       // 확장 자료
            var ncn = meta != null ? meta["ncn"] as JObject : null; // ncn 자료
            r.StoppedByRejection = ncn != null && (ncn.Value<bool?>("stoppedByRejection") ?? false);   // 거부로 멈춤
            var usage = o["usage"] as JObject;                      // 사용량
            if (usage != null)                                      // 있으면
            {
                r.TotalTokens = usage.Value<long?>("totalTokens");  // 합
                r.InputTokens = usage.Value<long?>("inputTokens");  // 입력
                r.OutputTokens = usage.Value<long?>("outputTokens");   // 출력
            }
            return r;                                               // 결과
        }
    }

    /// <summary>계획 항목 하나(Checklist).</summary>
    public sealed class PlanEntry                                   // 계획 항목
    {
        public string Content { get; set; }                         // 할 일
        public string Priority { get; set; }                        // 우선순위
        public string Status { get; set; }                          // pending·in_progress·completed

        public bool IsDone => Status == "completed";                // 끝났는지
    }
}
