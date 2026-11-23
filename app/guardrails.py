"""Deterministic guardrails, run without any LLM (SEC-05, SEC-06).

Cheap, explainable and impossible to talk out of; they complement the moderation model and the planner's
`unsafe` intent (defense in depth).
"""

import re
from dataclasses import dataclass, field

_INJECTION_RE = re.compile("|".join([
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|prompts|rules)",
    r"disregard (all |any )?(the )?(previous|prior|above) (instructions|rules)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system|hidden|initial) (prompt|instructions)",
    r"\byou are now\b",
    r"\b(developer|god|dan) mode\b",
    r"\bjailbreak\b",
    r"ignore (todas )?(as )?(instru[çc][õo]es|regras) (anteriores|acima)",
    r"esque[çc]a (todas )?(as )?(suas )?(instru[çc][õo]es|regras)",
    r"(mostre|revele) (o )?(seu )?prompt",
]), re.IGNORECASE)

_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_CPF_RE = re.compile(r"(?<!\d)\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)")
# Explicit value required ("senha: x", "PIN=1234", "password is Abc1"), so "I forgot my password" is kept.
_SECRET_RE = re.compile(
    r"\b(senha|password|passwd|pin|cvv|cvc)\b(?:\s*[:=]\s*\S+|\s+(?:é|is)\s+(?=\S*\d)\S+)", re.IGNORECASE)


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
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def cpf_valid(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for size in (9, 10):
        check = sum(int(digits[i]) * (size + 1 - i) for i in range(size)) * 10 % 11 % 10
        if check != int(digits[size]):
            return False
    return True


def redact_pii(text: str, with_secrets: bool = True) -> tuple[str, list[str]]:
    flags: set[str] = set()

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            flags.add("pii_card")
            return "[CARD_REDACTED]"
        return m.group(0)

    def cpf(m: re.Match) -> str:
        if cpf_valid(m.group(0)):
            flags.add("pii_cpf")
            return "[CPF_REDACTED]"
        return m.group(0)

    def secret(m: re.Match) -> str:
        flags.add("pii_secret")
        return f"{m.group(1)} [SECRET_REDACTED]"

    text = _CPF_RE.sub(cpf, _CARD_RE.sub(card, text))
    if with_secrets:
        text = _SECRET_RE.sub(secret, text)
    return text, sorted(flags)


def check_input(message: str) -> InputCheck:
    redacted, flags = redact_pii(message)
    result = InputCheck(text=redacted, flags=flags)
    if _INJECTION_RE.search(message):
        result.flags.append("prompt_injection")
        result.blocked = True
    return result


def check_output(answer: str) -> tuple[str, list[str]]:
    """Redact card/CPF numbers the model may echo or hallucinate (SEC-06)."""
    redacted, flags = redact_pii(answer, with_secrets=False)
    return redacted, [f"output_{f}" for f in flags]
