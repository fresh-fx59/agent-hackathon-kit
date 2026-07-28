import re

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)(?:\+7|8)[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")
_CARD  = re.compile(r"(?<!\d)(?:\d[ -]?){15}\d(?!\d)")

def _luhn_ok(digits):
    total, alt = 0, False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9: d -= 9
        total += d; alt = not alt
    return total % 10 == 0

class Masker:
    """Stable per-run pseudonyms: same raw value -> same placeholder."""
    def __init__(self):
        self._seen = {}   # raw -> placeholder
        self._counters = {}

    def _pseudo(self, kind, raw):
        if raw not in self._seen:
            n = self._counters.get(kind, 0) + 1
            self._counters[kind] = n
            self._seen[raw] = "<%s:%s-%02d>" % (kind, kind[0].lower(), n)
        return self._seen[raw]

    def mask_with_flag(self, text):
        applied = [False]
        def email_sub(m):
            applied[0] = True; return self._pseudo("EMAIL", m.group(0))
        def phone_sub(m):
            applied[0] = True; return self._pseudo("PHONE", m.group(0))
        def card_sub(m):
            digits = re.sub(r"\D", "", m.group(0))
            if len(digits) == 16 and _luhn_ok(digits):
                applied[0] = True; return self._pseudo("CARD", digits)
            return m.group(0)
        out = _EMAIL.sub(email_sub, text)
        out = _PHONE.sub(phone_sub, out)
        out = _CARD.sub(card_sub, out)
        return out, applied[0]

    def mask(self, text):
        return self.mask_with_flag(text)[0]
