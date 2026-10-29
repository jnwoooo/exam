// vs/NcnVs.Core/Text/TextTools.cs — 글 처리 도우미: fs 읽기 줄 범위, 최소 편집 구간, 디스크 읽기·쓰기(인코딩 보존) (cn 원본 없음)
using System;                                                       // 기본 형식
using System.IO;                                                    // 파일
using System.Text;                                                  // 인코딩

namespace NcnVs.Core.Text                                           // 글 이름공간
{
    /// <summary>fs/read_text_file 의 line(1부터)·limit(줄 수) 적용. 줄 끝 문자는 그대로 둔다.</summary>
    public static class TextSlicing                                 // 줄 범위
    {
        public static string Slice(string text, int? line, int? limit)   // 자르기
        {
            if (text == null) return "";                            // 없으면 빈 글
            if (line == null && limit == null) return text;         // 범위 없음 → 전체
            var start = Math.Max(1, line ?? 1);                     // 시작 줄(1부터)
            var count = limit.HasValue ? Math.Max(0, limit.Value) : int.MaxValue;   // 줄 수(없으면 끝까지)
            var sb = new StringBuilder();                           // 결과
            var current = 1;                                        // 지금 줄 번호
            var pos = 0;                                            // 글 위치
            while (pos < text.Length && count > 0)                  // 끝까지 또는 줄 수만큼
            {
                var nl = text.IndexOf('\n', pos);                   // 다음 줄 끝
                var end = nl < 0 ? text.Length : nl + 1;            // 줄 끝(줄바꿈 포함)
                if (current >= start)                               // 범위 안이면
                {
                    sb.Append(text, pos, end - pos);                // 그 줄 붙임
                    count--;                                        // 남은 줄 수
                }
                current++;                                          // 다음 줄
                pos = end;                                          // 이동
            }
            return sb.ToString();                                   // 결과
        }
    }

    /// <summary>바꿀 구간 하나(시작·길이·새 글).</summary>
    public sealed class TextReplacement                             // 편집 구간
    {
        public int Start { get; set; }                              // 시작 위치
        public int Length { get; set; }                             // 지울 길이
        public string NewText { get; set; }                         // 넣을 글
    }

    /// <summary>
    /// 버퍼 전체를 바꾸지 않고 앞뒤 같은 부분을 뺀 가운데만 바꾼다 → 커서·책갈피·접기가 덜 흔들리고 Ctrl+Z 한 번에 되돌려진다.
    /// </summary>
    public static class MinimalEdit                                 // 최소 편집
    {
        /// <summary>같으면 null.</summary>
        public static TextReplacement Compute(string oldText, string newText)   // 구간 계산
        {
            oldText = oldText ?? "";                                // null 방지
            newText = newText ?? "";                                // null 방지
            if (string.Equals(oldText, newText, StringComparison.Ordinal)) return null;   // 바뀐 것 없음
            var max = Math.Min(oldText.Length, newText.Length);     // 비교 한도
            var prefix = 0;                                         // 같은 앞부분 길이
            while (prefix < max && oldText[prefix] == newText[prefix]) prefix++;   // 앞에서부터
            if (prefix > 0 && char.IsHighSurrogate(oldText[prefix - 1])) prefix--;   // 대리 쌍을 가르지 않음
            var suffix = 0;                                         // 같은 뒷부분 길이
            while (suffix < max - prefix && oldText[oldText.Length - 1 - suffix] == newText[newText.Length - 1 - suffix]) suffix++;   // 뒤에서부터(앞부분과 겹치지 않게)
            if (suffix > 0 && char.IsLowSurrogate(oldText[oldText.Length - suffix])) suffix--;   // 대리 쌍을 가르지 않음
            return new TextReplacement                              // 결과
            {
                Start = prefix,                                     // 시작
                Length = oldText.Length - prefix - suffix,          // 지울 길이
                NewText = newText.Substring(prefix, newText.Length - prefix - suffix),   // 넣을 글
            };
        }

        /// <summary>구간 적용(시험용).</summary>
        public static string Apply(string oldText, TextReplacement r)   // 적용
        {
            if (r == null) return oldText;                          // 변경 없음
            return oldText.Substring(0, r.Start) + r.NewText + oldText.Substring(r.Start + r.Length);   // 앞 + 새 글 + 뒤
        }
    }

    /// <summary>
    /// 열려 있지 않은 파일의 디스크 읽기·쓰기. 읽기: BOM → 그 인코딩, 아니면 엄격한 UTF-8, 깨지면 시스템 ANSI(한국어 Windows = CP949).
    /// 쓰기: 기존 파일의 인코딩·BOM 을 유지, 새 파일은 BOM 없는 UTF-8.
    /// </summary>
    public static class TextFileIo                                  // 디스크 입출력
    {
        private static readonly UTF8Encoding StrictUtf8 = new UTF8Encoding(false, true);   // 잘못된 바이트면 예외

        /// <summary>바이트 → (글, 인코딩).</summary>
        public static string Decode(byte[] bytes, out Encoding encoding)   // 해독
        {
            if (bytes.Length >= 3 && bytes[0] == 0xEF && bytes[1] == 0xBB && bytes[2] == 0xBF)   // UTF-8 BOM
            {
                encoding = new UTF8Encoding(true);                  // BOM 있는 UTF-8
                return encoding.GetString(bytes, 3, bytes.Length - 3);   // BOM 뒤
            }
            if (bytes.Length >= 2 && bytes[0] == 0xFF && bytes[1] == 0xFE)   // UTF-16 LE BOM
            {
                encoding = new UnicodeEncoding(false, true);        // UTF-16 LE
                return encoding.GetString(bytes, 2, bytes.Length - 2);   // BOM 뒤
            }
            if (bytes.Length >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF)   // UTF-16 BE BOM
            {
                encoding = new UnicodeEncoding(true, true);         // UTF-16 BE
                return encoding.GetString(bytes, 2, bytes.Length - 2);   // BOM 뒤
            }
            try
            {
                var s = StrictUtf8.GetString(bytes);                // 엄격한 UTF-8
                encoding = new UTF8Encoding(false);                 // BOM 없는 UTF-8
                return s;                                           // 성공
            }
            catch (DecoderFallbackException)                        // UTF-8 아님
            {
                encoding = Encoding.Default;                        // 시스템 ANSI(.NET Framework: CP949 등)
                return encoding.GetString(bytes);                   // 그것으로
            }
        }

        /// <summary>디스크 읽기. 없으면 FileNotFoundException.</summary>
        public static string ReadAllText(string path)               // 읽기
        {
            Encoding enc;                                           // 버릴 인코딩
            return Decode(File.ReadAllBytes(path), out enc);        // 해독
        }

        /// <summary>디스크 쓰기(기존 인코딩 유지, 폴더가 없으면 만듦).</summary>
        public static void WriteAllText(string path, string content)   // 쓰기
        {
            Encoding enc = new UTF8Encoding(false);                 // 새 파일 기본
            if (File.Exists(path)) Decode(File.ReadAllBytes(path), out enc);   // 기존 인코딩
            var dir = Path.GetDirectoryName(path);                  // 폴더
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);   // 없으면 만듦
            var body = enc.GetBytes(content ?? "");                 // 본문 바이트
            var bom = enc.GetPreamble();                            // BOM(없으면 빈 배열)
            using (var fs = new FileStream(path, FileMode.Create, FileAccess.Write, FileShare.Read))   // 덮어쓰기
            {
                fs.Write(bom, 0, bom.Length);                       // BOM
                fs.Write(body, 0, body.Length);                     // 본문
            }
        }
    }
}
