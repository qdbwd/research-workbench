"""扫描只报告字段及固定风险码，避免把触发原文带入审计。"""

import json
import re
import unicodedata

from pydantic import BaseModel

_CREDENTIAL = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
    r"Bearer\s+[A-Za-z0-9._-]{16,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|"
    r"\b(?:[A-Z0-9_]*_)?(?:API_KEY|SECRET|PASSWORD|TOKEN)\s*=\s*"
    r"""["']?(?!your[_-]|example[_-]|placeholder|changeme|redacted)[A-Za-z0-9_./+@!%=-]{12,}""",
    re.IGNORECASE,
)
_RULES = (
    (
        "prompt_injection",
        "提示词分区伪造或指令覆盖",
        re.compile(
            r"<\s*/?\s*(?:application_policy|operating_instructions|system|configured_role)\b|"
            r"(?:ignore|disregard)\s+(?:all\s+)?(?:previous|prior|above)\s+instructions|"
            r"忽略(?:以上|之前|先前)(?:的|所有)?(?:指令|规则)",
            re.IGNORECASE,
        ),
    ),
    (
        "authority_bypass",
        "权限、审批或预算越权",
        re.compile(
            r"跳过审批|无需确认|自动批准|提高预算|无限预算|"
            r"bypass\s+approval|skip\s+confirmation|grant\s+publish",
            re.IGNORECASE,
        ),
    ),
    ("credential", "疑似明文凭据", _CREDENTIAL),
    (
        "hidden_controls",
        "隐藏或双向控制字符",
        re.compile(
            r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f"
            r"\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]"
        ),
    ),
)


class SecurityScanResult(BaseModel):
    allowed: bool
    risk_codes: list[str]
    reasons: list[str]


def scan_evolution_text(text: str, *, field_name: str = "content") -> SecurityScanResult:
    normalized = unicodedata.normalize("NFKC", text)
    visible = _RULES[-1][2].sub("", normalized)
    matches = [
        (code, reason)
        for code, reason, pattern in _RULES
        if pattern.search(normalized) or pattern.search(visible)
    ]
    return SecurityScanResult(
        allowed=not matches,
        risk_codes=[code for code, _ in matches],
        reasons=[f"{field_name}：{reason}" for _, reason in matches],
    )


def redact_credentials(value):
    """复盘的模型账本与输入不保留扫描可辨认的凭据。"""
    if isinstance(value, str):
        # 账本的 AIMessage.content 包含序列化 JSON，先解码再扫描以覆盖转义的引号/字符。
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                clean = redact_credentials(parsed)
                return value if clean == parsed else json.dumps(clean, ensure_ascii=False)
        scan = scan_evolution_text(value)
        if "credential" in scan.risk_codes:
            clean = _CREDENTIAL.sub("[REDACTED]", value)
            return (
                clean if "credential" not in scan_evolution_text(clean).risk_codes else "[REDACTED]"
            )
        return value
    if isinstance(value, list):
        return [redact_credentials(item) for item in value]
    if isinstance(value, dict):
        return {redact_credentials(key): redact_credentials(item) for key, item in value.items()}
    return value
