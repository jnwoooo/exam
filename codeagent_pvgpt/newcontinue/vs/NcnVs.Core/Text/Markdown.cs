// vs/NcnVs.Core/Text/Markdown.cs — 최소 마크다운 해석(제목·목록·코드 블록·굵게·인라인 코드) → 화면이 그릴 블록 목록 (계획 14-5 Q8 범위)
using System.Collections.Generic;                                   // 목록
using System.Text;                                                  // StringBuilder

namespace NcnVs.Core.Text                                           // 글 이름공간
{
    /// <summary>블록 종류.</summary>
    public enum MdBlockKind { Paragraph, Heading, ListItem, CodeBlock }   // 문단·제목·목록 항목·코드 블록

    /// <summary>글 조각(굵게·코드 표시).</summary>
    public sealed class MdSpan                                      // 인라인 조각
    {
        public string Text { get; set; }                            // 글
        public bool Bold { get; set; }                              // **굵게**
        public bool Code { get; set; }                              // `코드`
    }

    /// <summary>블록 하나.</summary>
    public sealed class MdBlock                                     // 블록
    {
        public MdBlockKind Kind { get; set; }                       // 종류
        public int Level { get; set; }                              // 제목 단계(1~6) 또는 목록 들여쓰기 단계(0부터)
        public string Marker { get; set; }                          // 목록 표시("•" 또는 "1.")
        public string Language { get; set; }                        // 코드 블록 언어
        public string Text { get; set; }                            // 원문(코드 블록은 내용 전체)
        public List<MdSpan> Spans { get; set; } = new List<MdSpan>();   // 인라인 조각(코드 블록은 비어 있음)
    }

    /// <summary>줄 단위 해석기. 닫히지 않은 코드 블록(스트리밍 중)도 끝까지 코드로 본다.</summary>
    public static class Markdown                                    // 해석기
    {
        public static List<MdBlock> Parse(string text)              // 해석
        {
            var blocks = new List<MdBlock>();                       // 결과
            var lines = (text ?? "").Replace("\r\n", "\n").Split('\n');   // 줄들
            var para = new StringBuilder();                         // 모으는 문단
            StringBuilder code = null;                              // 모으는 코드(코드 블록 안이면 null 아님)
            string codeLang = null;                                 // 코드 언어
            foreach (var raw in lines)                              // 줄마다
            {
                var trimmed = raw.TrimStart();                      // 앞 공백 뺀 줄
                if (code != null)                                   // 코드 블록 안
                {
                    if (trimmed.StartsWith("```"))                  // 닫는 울타리
                    {
                        blocks.Add(new MdBlock { Kind = MdBlockKind.CodeBlock, Language = codeLang, Text = TrimLastNewline(code) });   // 코드 블록
                        code = null;                                // 밖으로
                    }
                    else
                    {
                        code.Append(raw).Append('\n');              // 내용 그대로
                    }
                    continue;                                       // 다음 줄
                }
                if (trimmed.StartsWith("```"))                      // 여는 울타리
                {
                    FlushParagraph(blocks, para);                   // 앞 문단 마감
                    code = new StringBuilder();                     // 코드 시작
                    codeLang = trimmed.Substring(3).Trim();         // 언어
                    continue;                                       // 다음 줄
                }
                if (trimmed.Length == 0)                            // 빈 줄
                {
                    FlushParagraph(blocks, para);                   // 문단 끝
                    continue;                                       // 다음 줄
                }
                var heading = HeadingLevel(trimmed);                // 제목 단계
                if (heading > 0)                                    // 제목
                {
                    FlushParagraph(blocks, para);                   // 앞 문단 마감
                    var t = trimmed.Substring(heading).Trim();      // 제목 글
                    blocks.Add(new MdBlock { Kind = MdBlockKind.Heading, Level = heading, Text = t, Spans = ParseInline(t) });   // 제목 블록
                    continue;                                       // 다음 줄
                }
                string marker;                                      // 목록 표시
                string itemText;                                    // 항목 글
                if (TryListItem(trimmed, out marker, out itemText)) // 목록 항목
                {
                    FlushParagraph(blocks, para);                   // 앞 문단 마감
                    var indent = (raw.Length - trimmed.Length) / 2; // 들여쓰기 단계(공백 2칸 = 1단계)
                    blocks.Add(new MdBlock { Kind = MdBlockKind.ListItem, Level = indent, Marker = marker, Text = itemText, Spans = ParseInline(itemText) });   // 항목 블록
                    continue;                                       // 다음 줄
                }
                if (para.Length > 0) para.Append('\n');             // 문단 안 줄바꿈 유지
                para.Append(raw.TrimEnd());                         // 문단에 모음
            }
            if (code != null)                                       // 닫히지 않은 코드(스트리밍 중)
                blocks.Add(new MdBlock { Kind = MdBlockKind.CodeBlock, Language = codeLang, Text = TrimLastNewline(code) });   // 지금까지 코드
            FlushParagraph(blocks, para);                           // 마지막 문단
            return blocks;                                          // 결과
        }

        private static string TrimLastNewline(StringBuilder sb)     // 끝 줄바꿈 하나 빼기
        {
            var s = sb.ToString();                                  // 글
            return s.EndsWith("\n") ? s.Substring(0, s.Length - 1) : s;   // 끝 \n 제거
        }

        private static void FlushParagraph(List<MdBlock> blocks, StringBuilder para)   // 문단 마감
        {
            if (para.Length == 0) return;                           // 모은 것 없음
            var t = para.ToString();                                // 문단 글
            blocks.Add(new MdBlock { Kind = MdBlockKind.Paragraph, Text = t, Spans = ParseInline(t) });   // 문단 블록
            para.Clear();                                           // 비움
        }

        private static int HeadingLevel(string line)                // "# " ~ "###### "
        {
            var n = 0;                                              // # 개수
            while (n < line.Length && n < 7 && line[n] == '#') n++; // 세기
            if (n == 0 || n > 6) return 0;                          // 제목 아님
            if (n < line.Length && line[n] != ' ') return 0;        // "#abc" 는 제목 아님
            return n;                                               // 단계
        }

        private static bool TryListItem(string line, out string marker, out string text)   // 목록 항목인지
        {
            marker = null;                                          // 기본
            text = null;                                            // 기본
            if (line.Length >= 2 && (line[0] == '-' || line[0] == '*' || line[0] == '+') && line[1] == ' ')   // "- " "* " "+ "
            {
                var rest = line.Substring(2);                       // 나머지
                if (rest.StartsWith("[ ] ")) { marker = "☐"; text = rest.Substring(4); return true; }   // 체크 안 됨
                if (rest.StartsWith("[x] ") || rest.StartsWith("[X] ")) { marker = "☑"; text = rest.Substring(4); return true; }   // 체크됨
                marker = "•";                                       // 글머리표
                text = rest;                                        // 글
                return true;                                        // 항목
            }
            var i = 0;                                              // 숫자 길이
            while (i < line.Length && i < 9 && char.IsDigit(line[i])) i++;   // 숫자 세기
            if (i > 0 && i + 1 < line.Length && (line[i] == '.' || line[i] == ')') && line[i + 1] == ' ')   // "1. " "1) "
            {
                marker = line.Substring(0, i) + ".";                // 번호
                text = line.Substring(i + 2);                       // 글
                return true;                                        // 항목
            }
            return false;                                           // 아님
        }

        /// <summary>인라인: `코드`, **굵게**(__굵게__ 도). 짝이 없으면 글 그대로.</summary>
        public static List<MdSpan> ParseInline(string text)         // 인라인 해석
        {
            var spans = new List<MdSpan>();                         // 결과
            var sb = new StringBuilder();                           // 모으는 글
            var bold = false;                                       // 지금 굵게인지
            var i = 0;                                              // 위치
            while (i < text.Length)                                 // 끝까지
            {
                var c = text[i];                                    // 글자
                if (c == '`')                                       // 인라인 코드 시작
                {
                    var close = text.IndexOf('`', i + 1);           // 닫는 `
                    if (close > i)                                  // 짝 있음
                    {
                        Flush(spans, sb, bold);                     // 앞 글 마감
                        spans.Add(new MdSpan { Text = text.Substring(i + 1, close - i - 1), Code = true, Bold = bold });   // 코드 조각
                        i = close + 1;                              // 건너뜀
                        continue;                                   // 다음
                    }
                }
                if ((c == '*' || c == '_') && i + 1 < text.Length && text[i + 1] == c)   // ** 또는 __
                {
                    var token = new string(c, 2);                   // 표시
                    if (bold || text.IndexOf(token, i + 2) > i + 1) // 닫는 짝이 있거나 지금 굵게
                    {
                        Flush(spans, sb, bold);                     // 앞 글 마감
                        bold = !bold;                               // 굵게 켜기/끄기
                        i += 2;                                     // 건너뜀
                        continue;                                   // 다음
                    }
                }
                sb.Append(c);                                       // 보통 글자
                i++;                                                // 다음
            }
            Flush(spans, sb, bold);                                 // 마지막 글
            return spans;                                           // 결과
        }

        private static void Flush(List<MdSpan> spans, StringBuilder sb, bool bold)   // 글 조각 마감
        {
            if (sb.Length == 0) return;                             // 없으면 그만
            spans.Add(new MdSpan { Text = sb.ToString(), Bold = bold });   // 조각 추가
            sb.Clear();                                             // 비움
        }
    }
}
