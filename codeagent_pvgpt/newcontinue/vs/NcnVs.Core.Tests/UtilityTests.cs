// vs/NcnVs.Core.Tests/UtilityTests.cs — 도우미 시험: 명령줄 따옴표, 개발자 환경, 줄 범위, 최소 편집, 디스크 인코딩, 마크다운, 첨부 블록
using System;                                                       // 기본 형식
using System.IO;                                                    // 임시 파일
using System.Linq;                                                  // 질의
using System.Text;                                                  // 인코딩
using NcnVs.Core.Acp;                                               // ContentBlocks
using NcnVs.Core.Host;                                              // CommandLine·DevEnvironment
using NcnVs.Core.Text;                                              // 글 도우미
using Xunit;                                                        // 시험 도구

namespace NcnVs.Core.Tests                                          // 시험 이름공간
{
    public class UtilityTests                                       // 도우미 시험
    {
        [Theory]
        [InlineData("--stdio", "--stdio")]                          // 그대로
        [InlineData("C:\\Program Files\\x\\c.yaml", "\"C:\\Program Files\\x\\c.yaml\"")]   // 공백 → 따옴표
        [InlineData("a\"b", "\"a\\\"b\"")]                          // 따옴표 이스케이프
        [InlineData("C:\\dir with space\\", "\"C:\\dir with space\\\\\"")]   // 끝 역슬래시 두 배
        [InlineData("", "\"\"")]                                    // 빈 인자
        public void Quote_follows_windows_rules(string arg, string expected)   // 따옴표 규칙
        {
            Assert.Equal(expected, CommandLine.Quote(arg));         // 비교
        }

        [Fact]
        public void Split_handles_quotes()                          // 추가 인자 쪼개기
        {
            var r = CommandLine.Split("--allow Bash --rule \"a b\"  --ask=\"\"");   // 글
            Assert.Equal(new[] { "--allow", "Bash", "--rule", "a b", "--ask=" }, r.ToArray());   // 결과
        }

        [Fact]
        public void Launch_arguments_order()                        // ncn 인자
        {
            var o = new NcnLaunchOptions { ConfigPath = "C:\\c.yaml", Verbose = true };   // 설정
            o.ExtraArgs.Add("--auto");                              // 추가
            Assert.Equal(new[] { "--stdio", "--config", "C:\\c.yaml", "--verbose", "--auto" }, o.BuildArguments().ToArray());   // 순서
        }

        [Fact]
        public void Dev_environment_puts_msbuild_first()            // 개발자 환경
        {
            var dir = DevEnvironment.InstallDirFromDevenv("C:\\VS\\Common7\\IDE\\devenv.exe");   // 설치 폴더
            Assert.Equal("C:\\VS\\", dir);                          // 끝 \ 포함
            var env = DevEnvironment.Build(dir, "17.0", false, "C:\\Windows");   // 만들기
            Assert.Equal("C:\\VS\\", env["VSINSTALLDIR"]);          // 설치 폴더
            Assert.Equal("C:\\VS\\Common7\\IDE\\", env["DevEnvDir"]);   // devenv 폴더
            Assert.Equal("17.0", env["VisualStudioVersion"]);       // 버전
            Assert.StartsWith("C:\\VS\\MSBuild\\Current\\Bin\\amd64;", env["PATH"]);   // msbuild 맨 앞
            Assert.EndsWith(";C:\\Windows", env["PATH"]);           // 원래 PATH 뒤
            var arm = DevEnvironment.Build(dir, "17.0", true, "");  // ARM64
            Assert.StartsWith("C:\\VS\\MSBuild\\Current\\Bin\\arm64;", arm["PATH"]);   // arm64 msbuild
        }

        [Fact]
        public void Slice_line_and_limit()                          // 줄 범위
        {
            var t = "a\r\nb\nc";                                    // 섞인 줄 끝
            Assert.Equal(t, TextSlicing.Slice(t, null, null));      // 전체
            Assert.Equal("b\nc", TextSlicing.Slice(t, 2, null));    // 2번째 줄부터
            Assert.Equal("a\r\n", TextSlicing.Slice(t, 1, 1));      // 1줄(줄 끝 유지)
            Assert.Equal("", TextSlicing.Slice(t, 9, 2));           // 범위 밖
        }

        [Theory]
        [InlineData("abcdef", "abXYef")]                            // 가운데
        [InlineData("abc", "abc!")]                                 // 끝에 추가
        [InlineData("aaa", "aa")]                                   // 반복 글자 삭제
        [InlineData("", "new")]                                     // 빈 파일
        [InlineData("line1\r\nline2\r\n", "line1\r\nLINE2\r\n")]    // CRLF
        public void Minimal_edit_reproduces_new_text(string oldText, string newText)   // 최소 편집
        {
            var r = MinimalEdit.Compute(oldText, newText);          // 구간
            Assert.Equal(newText, MinimalEdit.Apply(oldText, r));   // 적용하면 새 글
            Assert.True(r.Length <= oldText.Length);                // 지울 길이 범위
        }

        [Fact]
        public void Minimal_edit_same_text_is_null()                // 같으면 편집 없음
        {
            Assert.Null(MinimalEdit.Compute("x", "x"));             // null
        }

        [Fact]
        public void Disk_write_keeps_bom_and_new_file_has_none()    // 인코딩 유지
        {
            var dir = Path.Combine(Path.GetTempPath(), "ncnvs-" + Guid.NewGuid().ToString("N"));   // 임시 폴더
            Directory.CreateDirectory(dir);                         // 만들기
            try
            {
                var bomFile = Path.Combine(dir, "bom.cs");          // BOM 파일
                File.WriteAllBytes(bomFile, new byte[] { 0xEF, 0xBB, 0xBF, (byte)'a' });   // BOM + a
                TextFileIo.WriteAllText(bomFile, "한글");            // 다시 쓰기
                var bytes = File.ReadAllBytes(bomFile);             // 바이트
                Assert.Equal(new byte[] { 0xEF, 0xBB, 0xBF }, bytes.Take(3).ToArray());   // BOM 유지
                Assert.Equal("한글", TextFileIo.ReadAllText(bomFile));   // 읽기
                var newFile = Path.Combine(dir, "sub", "new.cs");   // 새 파일(폴더도 없음)
                TextFileIo.WriteAllText(newFile, "x");              // 쓰기
                Assert.Equal(new byte[] { (byte)'x' }, File.ReadAllBytes(newFile));   // BOM 없음
                Assert.Throws<FileNotFoundException>(() => TextFileIo.ReadAllText(Path.Combine(dir, "none.cs")));   // 없는 파일
            }
            finally
            {
                Directory.Delete(dir, true);                        // 정리
            }
        }

        [Fact]
        public void Markdown_blocks()                               // 마크다운 해석
        {
            var md = "# 제목\n\n- 항목 **굵게**\n  1. 하위\n\n문단 `코드` 끝\n```cs\nvar x = 1;\n```\n- [x] 끝남";   // 글
            var b = Markdown.Parse(md);                             // 해석
            Assert.Equal(MdBlockKind.Heading, b[0].Kind);           // 제목
            Assert.Equal(1, b[0].Level);                            // 단계
            Assert.Equal(MdBlockKind.ListItem, b[1].Kind);          // 목록
            Assert.True(b[1].Spans.Single(s => s.Text == "굵게").Bold);   // 굵게
            Assert.Equal("1.", b[2].Marker);                        // 번호 목록
            Assert.Equal(1, b[2].Level);                            // 들여쓰기 1단계
            Assert.Equal(MdBlockKind.Paragraph, b[3].Kind);         // 문단
            Assert.True(b[3].Spans.Single(s => s.Text == "코드").Code);   // 인라인 코드
            Assert.Equal(MdBlockKind.CodeBlock, b[4].Kind);         // 코드 블록
            Assert.Equal("cs", b[4].Language);                      // 언어
            Assert.Equal("var x = 1;", b[4].Text);                  // 내용
            Assert.Equal("☑", b[5].Marker);                         // 체크 항목
        }

        [Fact]
        public void Markdown_unclosed_code_and_lone_stars()         // 스트리밍 중 글
        {
            var b = Markdown.Parse("2 * 3 = 6\n```py\nprint(1)");   // 닫히지 않은 코드
            Assert.Equal("2 * 3 = 6", string.Concat(b[0].Spans.Select(s => s.Text)));   // 별표 그대로
            Assert.Equal(MdBlockKind.CodeBlock, b[1].Kind);         // 코드로 봄
            Assert.Equal("print(1)", b[1].Text);                    // 내용
        }

        [Fact]
        public void Attachment_blocks_match_acp_examples()          // 첨부 블록 형식(3-4)
        {
            var path = Path.Combine(Path.GetTempPath(), "샵 코드", "Cart.cs");   // 한글·공백 경로
            var link = ContentBlocks.ResourceLink(path);            // 현재 파일
            Assert.Equal("resource_link", (string)link["type"]);    // 종류
            Assert.Equal("Cart.cs", (string)link["name"]);          // 이름
            Assert.StartsWith("file:///", (string)link["uri"]);     // file URI
            Assert.Contains("%20", (string)link["uri"]);            // 공백 인코딩
            var res = ContentBlocks.Resource(path, "선택 글", 10, 20);   // 선택 영역
            Assert.Equal("resource", (string)res["type"]);          // 종류
            Assert.EndsWith("Cart.cs#L10-20", (string)res["resource"]["uri"]);   // 줄 조각
            Assert.Equal("text/x-csharp", (string)res["resource"]["mimeType"]);   // C#
            Assert.Equal("선택 글", (string)res["resource"]["text"]); // 글
        }
    }
}
