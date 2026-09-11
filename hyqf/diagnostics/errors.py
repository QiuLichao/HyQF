from __future__ import annotations

from dataclasses import dataclass

from hyqf.frontend.spans import SourceSpan


@dataclass
class DiagnosticError(Exception):
    message: str
    span: SourceSpan | None = None
    code: str = "HYQF000"
    expected: object | None = None
    actual: object | None = None
    resource: str | None = None

    def __str__(self) -> str:
        where = f" at {self.span}" if self.span else ""
        details: list[str] = []
        if self.expected is not None or self.actual is not None:
            details.append(f"expected {self.expected}, got {self.actual}")
        if self.resource:
            details.append(f"resource {self.resource}")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"{self.code}: {self.message}{where}{suffix}"


class ParseError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None):
        super().__init__(message, span, "HYQF_PARSE")


class NameResolutionError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None):
        super().__init__(message, span, "HYQF_NAME")


class HyQFTypeError(DiagnosticError):
    def __init__(
        self,
        message: str,
        span: SourceSpan | None = None,
        *,
        expected: object | None = None,
        actual: object | None = None,
    ):
        super().__init__(message, span, "HYQF_TYPE", expected=expected, actual=actual)


class EffectError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None):
        super().__init__(message, span, "HYQF_EFFECT")


class LinearUseError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None, resource: str | None = None):
        super().__init__(message, span, "HYQF_LINEAR", resource=resource)


class ResourceLeakError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None, resource: str | None = None):
        super().__init__(message, span, "HYQF_LEAK", resource=resource)


class ControlFlowOwnershipError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None, resource: str | None = None):
        super().__init__(message, span, "HYQF_CONTROL_OWNERSHIP", resource=resource)


class VerificationUnsupported(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None):
        super().__init__(message, span, "HYQF_VERIFY_UNSUPPORTED")


class BackendCapabilityError(DiagnosticError):
    def __init__(self, message: str, span: SourceSpan | None = None):
        super().__init__(message, span, "HYQF_BACKEND_CAPABILITY")
