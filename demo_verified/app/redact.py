import re, json
CARD = re.compile(r'(?<!\d)(?:\d[ -]?){13,19}(?!\d)')
EMAIL = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
PHONE = re.compile(r'(?<!\w)(?:\+?1[ -.])?\(?\d{3}\)?[ -.]\d{3}[ -.]\d{4}(?!\w)')
OTP = re.compile(r'(?i)\b((?:otp|one.time code|verification code|passcode)\s*(?:is|:)?\s*)\d{4,8}\b')

def text(s: str) -> str:
    # Lightweight protection, NOT a guarantee of complete PCI/PII scrubbing.
    return EMAIL.sub('[REDACTED EMAIL]', PHONE.sub('[REDACTED PHONE]', OTP.sub(r'\1[REDACTED]', CARD.sub('[REDACTED CARD]', s))))

def obj(value):
    return json.loads(text(json.dumps(value, ensure_ascii=False, default=str)))
