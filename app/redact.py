import re, json
CARD = re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)')
OTP = re.compile(r'(?i)\b((?:otp|one.time code|verification code|passcode)\s*(?:is|:)?\s*)\d{4,8}\b')

def text(s: str) -> str:
    # Lightweight protection, NOT a guarantee of complete PCI/PII scrubbing.
    return OTP.sub(r'\1[REDACTED]', CARD.sub('[REDACTED CARD]', s))

def obj(value):
    return json.loads(text(json.dumps(value, ensure_ascii=False, default=str)))
