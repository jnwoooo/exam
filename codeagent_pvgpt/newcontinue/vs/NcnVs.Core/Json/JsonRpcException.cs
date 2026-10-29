// vs/NcnVs.Core/Json/JsonRpcException.cs — JSON-RPC 2.0 오류 응답 하나(code·message·data)를 담는 예외 (cn 원본 없음 — ACP 클라이언트)
using System;                                                       // Exception
using Newtonsoft.Json.Linq;                                         // JToken

namespace NcnVs.Core.Json                                           // JSON-RPC 이름공간
{
    /// <summary>JSON-RPC 오류. 받은 오류 응답을 던지거나, 우리가 보낼 오류 응답을 만들 때 쓴다.</summary>
    public sealed class JsonRpcException : Exception                // 예외 형식
    {
        public const int ParseError = -32700;                       // 해석 실패
        public const int InvalidRequest = -32600;                   // 잘못된 요청(같은 세션 prompt 중복 등)
        public const int MethodNotFound = -32601;                   // 모르는 메서드
        public const int InvalidParams = -32602;                    // 잘못된 인자
        public const int InternalError = -32603;                    // 내부 오류(설정·LLM 오류)
        public const int ResourceNotFound = -32002;                 // ACP: 없는 세션·파일

        public int Code { get; }                                    // 오류 코드
        public JToken ErrorData { get; }                            // 추가 자료 data(없으면 null) — Exception.Data 와 겹치지 않게

        public JsonRpcException(int code, string message, JToken data = null) : base(message)   // 생성자
        {
            Code = code;                                            // 코드 보관
            ErrorData = data;                                       // 자료 보관
        }

        /// <summary>ncn 은 data.message 에 cn 원문 오류 글을 싣는다 → 있으면 그것, 없으면 message.</summary>
        public string DisplayMessage                                // 화면에 보일 글
        {
            get
            {
                var obj = ErrorData as JObject;                          // data 가 객체면
                var inner = obj != null ? obj.Value<string>("message") : null;   // data.message
                return string.IsNullOrEmpty(inner) ? Message : inner;   // 있으면 그것
            }
        }
    }
}
