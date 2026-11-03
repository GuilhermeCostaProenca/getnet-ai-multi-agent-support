"""Deterministic input guardrails: run before any LLM call (GR-INJ-01, GR-PII-01).

Cheap, explainable and testable. They complement — not replace — the LLM-based
moderation and the router's intent allowlist (defense in depth).
"""

import re
from dataclasses import dataclass, field

_INJECTION_PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) (instructions|rules)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system|hidden|initial) (prompt|instructions)",
    r"\byou are now\b",
    r"\b(developer|god|dan) mode\b",
    r"\bjailbreak\b",
    r"ignore (todas )?(as )?(instru[çc][õo]es|regras) (anteriores|acima)",
    r"esque[çc]a (todas )?(as )?(suas )?(instru[çc][õo]es|regras)",
    r"(mostre|revele) (o )?(seu )?prompt",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)

_CARD_CANDIDATE_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
_SECRET_RE = re.compile(r"\b(senha|password|passwd|pin|cvv|cvc)\b\s*[:=é]?\s*\S+", re.IGNORECASE)


@dataclass
class InputCheck:
    text: str
    flags: list[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def pii_redacted(self) -> bool:
        return any(f.startswith("pii_") for f in self.flags)


def luhn_valid(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def cpf_valid(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        s = sum(int(digits[i]) * (size + 1 - i) for i in range(size))
        check = (s * 10) % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def detect_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))


def redact_pii(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []

    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            flags.append("pii_card")
            return "[CARD_REDACTED]"
        return m.group(0)

    def _cpf(m: re.Match) -> str:
        if cpf_valid(m.group(0)):
            flags.append("pii_cpf")
            return "[CPF_REDACTED]"
        return m.group(0)

    def _secret(m: re.Match) -> str:
        flags.append("pii_secret")
        return f"{m.group(1)} [SECRET_REDACTED]"

    text = _CARD_CANDIDATE_RE.sub(_card, text)
    text = _CPF_RE.sub(_cpf, text)
    text = _SECRET_RE.sub(_secret, text)
    return text, sorted(set(flags))


def check_input(message: str) -> InputCheck:
    redacted, flags = redact_pii(message)
    result = InputCheck(text=redacted, flags=flags)
    if detect_injection(message):
        result.flags.append("prompt_injection")
        result.blocked = True
    return result
