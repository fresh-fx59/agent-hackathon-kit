from dataclasses import dataclass, field, asdict

LEVELS = ("DEBUG", "INFO", "WARN", "ERROR")
_ALIASES = {"WARNING": "WARN", "ERR": "ERROR", "TRACE": "DEBUG", "FATAL": "ERROR",
            "SEVERE": "ERROR", "CRITICAL": "ERROR"}

def normalize_level(raw):
    up = (raw or "").strip().upper()
    up = _ALIASES.get(up, up)
    return up if up in LEVELS else "UNKNOWN"

@dataclass
class NormalizedRecord:
    timestamp: str
    service: str
    level: str
    body: str
    observed_timestamp: str = ""
    trace_id: str = ""
    correlation_id: str = ""
    domain_ids: dict = field(default_factory=dict)
    source_ref: str = ""
    source_line: int = 0
    parse_quality: str = "ok"
    redaction_applied: bool = False
    attrs: dict = field(default_factory=dict)

    def __post_init__(self):
        self.level = normalize_level(self.level)
        if not self.observed_timestamp:
            self.observed_timestamp = self.timestamp

    def to_dict(self):
        return asdict(self)
