"""Input/output guardrails (SEC-04, ADR-04): prompt-injection screen and PII redaction before the model sees text."""

import re
from dataclasses import dataclass, field

INJECTION = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|the\s+|your\s+)?(previous|prior|above)?\s*(instructions|rules|prompts?)"
    r"|(reveal|show|print|leak)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions)"
    r"|you\s+are\s+now\s+(dan|in\s+developer\s+mode)|jailbreak"
    r"|(ignore|esque[çc]a|desconsidere)\s+(todas\s+)?(as\s+)?(suas\s+)?instru[çc][õo]es"
    r"|(revele|mostre)\s+(o\s+)?(seu\s+)?prompt|modo\s+desenvolvedor",
    re.IGNORECASE,
)
CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
CPF = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
SECRET = re.compile(r"\b(senha|password|cvv|cvc|pin)\b\s*(?:[:=]|\s(?:é|is))\s*\S+", re.IGNORECASE)


def luhn_valid(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def cpf_valid(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False
    for n in (9, 10):
        check = sum(int(digits[i]) * (n + 1 - i) for i in range(n)) * 10 % 11 % 10
        if check != int(digits[n]):
            return False
    return True


def redact(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            flags.append("card_number")
            return "[CARTÃO REMOVIDO]"
        return m.group()

    def cpf(m: re.Match) -> str:
        if cpf_valid(re.sub(r"\D", "", m.group())):
            flags.append("cpf")
            return "[CPF REMOVIDO]"
        return m.group()

    def secret(m: re.Match) -> str:
        flags.append("secret")
        return f"{m.group(1)}: [REMOVIDO]"

    text = CARD.sub(card, text)
    text = CPF.sub(cpf, text)
    text = SECRET.sub(secret, text)
    return text, sorted(set(flags))


@dataclass
class InputCheck:
    text: str
    flags: list[str] = field(default_factory=list)
    blocked: bool = False

    @property
    def pii_redacted(self) -> bool:
        return any(f.startswith("pii_") for f in self.flags)


def check_input(message: str) -> InputCheck:
    text, pii = redact(message)
    flags = [f"pii_{p}" for p in pii]
    blocked = bool(INJECTION.search(message))
    if blocked:
        flags.append("prompt_injection")
    return InputCheck(text=text, flags=flags, blocked=blocked)


def check_output(answer: str) -> tuple[str, list[str]]:
    text, pii = redact(answer)
    return text, [f"output_pii_{p}" for p in pii]
