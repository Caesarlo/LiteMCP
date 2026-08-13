"""Fail-closed redaction helpers for secrets crossing observability boundaries."""
from __future__ import annotations

import logging
import os
import re
import secrets
from collections.abc import Mapping
from typing import Any

_REDACTION_FAILURE = "[redaction failed; output omitted]"
_REDACTED = "[REDACTED]"
_SENSITIVE_NAMES = frozenset(
    {
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "x_api_key",
        "api-key",
        "secret",
        "authorization",
        "cookie",
        "set_cookie",
        "database_url",
        "db_password",
        "connection_string",
        "client_secret",
        "private_key",
        "encryption_key",
        "jwt_secret",
    }
)
_SENSITIVE_KEY = re.compile(
    r'''(?i)(["']?(?:password|passwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|authorization|cookie|set-cookie|database[_-]?url|db[_-]?password|connection[_-]?string|client[_-]?secret|private[_-]?key|encryption[_-]?key|jwt[_-]?secret)["']?\s*[:=]\s*)(["']?)(.*?)(?:\2(?=\s*[,};]|$)|(?=\s*[,};]|$))'''
)
_HEADER_VALUE = re.compile(r'''(?i)(?<![\w-])((?:x-api-key|api-key|set-cookie|cookie)\s*:\s*)([^\r\n,;}]+)''')
_AUTHORIZATION = re.compile(r'''(?i)(["']?authorization["']?\s*[:=]\s*["']?)(basic|bearer)(\s+)([^"'\s,;}]+)(["']?)''')
_URL_PASSWORD = re.compile(r"(?i)(://[^:/@\s]+:)([^@/\s]+)(@)")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_FERNET = re.compile(
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{96,}={0,2}(?![A-Za-z0-9_-])"
)
_LITEMCP_KEY = re.compile(r"\blitemcp_[A-Za-z0-9]{8,}_[A-Za-z0-9_-]{16,}\b")
_STANDARD_RECORD_FIELDS = frozenset({"name","msg","args","levelname","levelno","pathname","filename","module","exc_info","exc_text","stack_info","lineno","funcName","created","msecs","relativeCreated","thread","threadName","processName","process","taskName"})
_ORIGINAL_FACTORY = logging.getLogRecordFactory()
_INSTALLED_REDACTORS: dict[int, SecretRedactor] = {}
class SecretRedactor(logging.Filter):
    def __init__(self, secret_values: tuple[str, ...] | list[str]) -> None:
        super().__init__(); self._secrets = tuple(sorted({v for v in secret_values if isinstance(v,str) and v}, key=len, reverse=True))
    @classmethod
    def from_environment(cls) -> SecretRedactor:
        return cls([v for k,v in os.environ.items() if k.startswith("LITEMCP_")])
    def _redact_text(self, value: str) -> str:
        for secret in self._secrets: value = value.replace(secret, _REDACTED)
        value = _URL_PASSWORD.sub(r"\1" + _REDACTED + r"\3", value)
        value = _AUTHORIZATION.sub(r"\1\2\3" + _REDACTED + r"\5", value)
        value = _SENSITIVE_KEY.sub(r"\1\2" + _REDACTED, value)
        value = re.sub(
            r'''(?i)(?<![\w-])((?:password|passwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|authorization|cookie|set-cookie|database[_-]?url|db[_-]?password|connection[_-]?string|client[_-]?secret|private[_-]?key|encryption[_-]?key|jwt[_-]?secret)\s*[:=]\s*)(["']?)([^\s,;}"']+)(\2|(?=\s|$))''',
            r"\1\2" + _REDACTED + r"\4",
            value,
        )
        value = _HEADER_VALUE.sub(r"\1" + _REDACTED, value)
        return _LITEMCP_KEY.sub(_REDACTED, _JWT.sub(_REDACTED, _FERNET.sub(_REDACTED, value)))
    def redact(self, value: Any) -> Any:
        if isinstance(value,str): return self._redact_text(value)
        if isinstance(value,Mapping):
            result: dict[Any,Any] = {}
            for key,item in value.items():
                safe_key=self.redact(key); name=str(key).strip("\"'").lower().replace("-","_")
                result[safe_key] = _REDACTED if name in _SENSITIVE_NAMES else self.redact(item)
            return result
        if isinstance(value,list): return [self.redact(x) for x in value]
        if isinstance(value,tuple): return tuple(self.redact(x) for x in value)
        if isinstance(value,set): return {self.redact(x) for x in value}
        if value is None or isinstance(value,(bool,int,float)): return value
        return self._redact_text(str(value))
    def safe_redact(self,value: Any)->Any:
        try: return self.redact(value)
        except Exception:  # noqa: BLE001
            return _REDACTION_FAILURE
    def safe_repr(self,value: Any)->str:
        try:
            result=self.safe_redact(repr(value)); return result if isinstance(result,str) else _REDACTION_FAILURE
        except Exception:  # noqa: BLE001
            return _REDACTION_FAILURE
    def sanitize_exception(self,exception: BaseException)->str:
        rendered=[]; seen=set()
        def visit(error: BaseException, relation: str=""):
            if id(error) in seen:return
            seen.add(id(error)); rendered.append(self._redact_text(f"{relation + ': ' if relation else ''}{type(error).__name__}: {error}"))
            if error.__cause__ is not None: visit(error.__cause__,"cause")
            if error.__context__ is not None and error.__context__ is not error.__cause__: visit(error.__context__,"context")
        try: visit(exception); return "\n".join(rendered)
        except Exception:  # noqa: BLE001
            return _REDACTION_FAILURE
    def filter(self,record: logging.LogRecord)->bool:
        try:
            message=record.getMessage()
            if record.exc_info is not None and record.exc_info[1] is not None: message=f"{message}\n{self.sanitize_exception(record.exc_info[1])}"; record.exc_info=None
            record.msg=self._redact_text(message); record.args=()
            for key,value in list(record.__dict__.items()):
                if key not in _STANDARD_RECORD_FIELDS: record.__dict__[key]=self.safe_redact(value)
        except Exception:  # noqa: BLE001
            record.msg=_REDACTION_FAILURE; record.args=(); record.exc_info=None
        return True
def redact_audit_payload(payload: Any, redactor: SecretRedactor|None=None)->Any:
    return (redactor or SecretRedactor(())).safe_redact(payload)
def install_logging_redaction(redactor: SecretRedactor)->None:
    if id(redactor) in _INSTALLED_REDACTORS:return
    _INSTALLED_REDACTORS[id(redactor)]=redactor
    root=logging.getLogger()
    for handler in root.handlers: handler.addFilter(redactor)
    for name in list(logging.Logger.manager.loggerDict):
        for handler in logging.getLogger(name).handlers: handler.addFilter(redactor)
    def factory(*args:Any,**kwargs:Any)->logging.LogRecord:
        record=_ORIGINAL_FACTORY(*args,**kwargs)
        for installed in tuple(_INSTALLED_REDACTORS.values()): installed.filter(record)
        return record
    logging.setLogRecordFactory(factory)
class SecretRedactionMiddleware:
    def __init__(self,app:Any,*,redactor:SecretRedactor)->None:self.app=app; self.redactor=redactor
    async def __call__(self,scope:dict[str,Any],receive:Any,send:Any)->None:
        try: await self.app(scope,receive,send)
        except Exception:
            if scope.get("type")!="http": raise
            state = scope.get("state")
            candidate = state.get("request_id") if isinstance(state, dict) else None
            if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9._-]+", candidate) and 1 <= len(candidate.encode("utf-8")) <= 128:
                request_id = candidate
            else:
                request_id = secrets.token_urlsafe(16)
            headers=[(b"content-type",b"text/plain; charset=utf-8"), (b"x-request-id",request_id.encode("ascii"))]
            try: body=self.redactor._redact_text("Internal server error")
            except Exception:  # noqa: BLE001
                body=_REDACTION_FAILURE
            await send({"type":"http.response.start","status":500,"headers":headers}); await send({"type":"http.response.body","body":body.encode("utf-8")})
