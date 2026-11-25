"""Structured JSON logs and Prometheus-format metrics (OBS-01, OBS-02)."""

import json
import logging
import threading
from collections import defaultdict, deque

_STANDARD = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name,
                   "event": record.getMessage()}
        payload.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._latency: deque[int] = deque(maxlen=2000)

    def inc(self, name: str, value: float = 1, **labels: str) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += value

    def observe_latency(self, ms: int) -> None:
        with self._lock:
            self._latency.append(ms)

    def render(self) -> str:
        with self._lock:
            lines = []
            for (name, labels), value in sorted(self._counters.items()):
                label_txt = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"{name}{{{label_txt}}} {value:g}" if label_txt else f"{name} {value:g}")
            values = sorted(self._latency)
            for q in (0.5, 0.95):
                v = values[min(len(values) - 1, int(q * (len(values) - 1)))] if values else 0
                lines.append(f'chat_latency_ms{{quantile="{q}"}} {v}')
            lines.append(f"chat_latency_ms_count {len(values)}")
        return "\n".join(lines) + "\n"
