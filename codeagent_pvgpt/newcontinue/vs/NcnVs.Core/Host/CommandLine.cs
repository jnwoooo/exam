// vs/NcnVs.Core/Host/CommandLine.cs — Windows 명령줄 인자 따옴표 처리(CommandLineToArgvW 규칙)와 옵션 글 쪼개기 (cn 원본 없음)
using System.Collections.Generic;                                   // 목록
using System.Text;                                                  // StringBuilder

namespace NcnVs.Core.Host                                           // 호스트 이름공간
{
    /// <summary>ProcessStartInfo.Arguments 한 줄 만들기(netstandard2.0 에는 ArgumentList 가 없다).</summary>
    public static class CommandLine                                 // 명령줄 도우미
    {
        /// <summary>인자 하나를 따옴표로(공백·따옴표가 있을 때만). 따옴표 앞 역슬래시는 두 배로.</summary>
        public static string Quote(string arg)                      // 따옴표
        {
            if (arg == null) arg = "";                              // null 방지
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0) return arg;   // 그대로 써도 됨
            var sb = new StringBuilder("\"");                       // 여는 따옴표
            var backslashes = 0;                                    // 이어진 역슬래시 수
            foreach (var c in arg)                                  // 글자마다
            {
                if (c == '\\') { backslashes++; continue; }         // 역슬래시는 모아 둠
                if (c == '"')                                       // 따옴표
                {
                    sb.Append('\\', backslashes * 2 + 1);           // 앞 역슬래시 두 배 + 이스케이프 하나
                    sb.Append('"');                                 // 따옴표
                }
                else
                {
                    sb.Append('\\', backslashes);                   // 역슬래시 그대로
                    sb.Append(c);                                   // 글자
                }
                backslashes = 0;                                    // 초기화
            }
            sb.Append('\\', backslashes * 2);                       // 닫는 따옴표 앞 역슬래시 두 배
            sb.Append('"');                                         // 닫는 따옴표
            return sb.ToString();                                   // 결과
        }

        /// <summary>인자 목록 → 한 줄.</summary>
        public static string Join(IEnumerable<string> args)         // 합치기
        {
            var parts = new List<string>();                         // 조각
            foreach (var a in args) parts.Add(Quote(a));            // 하나씩 따옴표
            return string.Join(" ", parts);                         // 공백으로
        }

        /// <summary>옵션 페이지의 "추가 인자" 글 → 인자 목록(공백 구분, "…" 묶음 지원).</summary>
        public static List<string> Split(string text)               // 쪼개기
        {
            var result = new List<string>();                        // 결과
            if (string.IsNullOrWhiteSpace(text)) return result;     // 비었으면 없음
            var sb = new StringBuilder();                           // 지금 인자
            var inQuote = false;                                    // 따옴표 안
            var has = false;                                        // 빈 따옴표("")도 인자로
            foreach (var c in text)                                 // 글자마다
            {
                if (c == '"') { inQuote = !inQuote; has = true; continue; }   // 따옴표 켜기/끄기
                if (!inQuote && char.IsWhiteSpace(c))               // 구분자
                {
                    if (has || sb.Length > 0) result.Add(sb.ToString());   // 인자 마감
                    sb.Clear();                                     // 비움
                    has = false;                                    // 초기화
                    continue;                                       // 다음
                }
                sb.Append(c);                                       // 글자
            }
            if (has || sb.Length > 0) result.Add(sb.ToString());    // 마지막 인자
            return result;                                          // 결과
        }
    }
}
