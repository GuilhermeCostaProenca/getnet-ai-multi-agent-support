"""Structured JSON logging and in-process metrics (OBS-01). Only already-redacted text is ever logged."""

import json
import logging
import sys
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone


class Metrics:
    """Prometheus-style counters + latency quantiles over the last 1000 requests (scraped at GET /metrics).
    In-process by design; a multi-replica deployment would use prometheus_client or OpenTelemetry metrics."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._latencies: deque[int] = deque(maxlen=1000)

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted((labels or {}).items())))] += value

    def observe_latency(self, ms: int) -> None:
        with self._lock:
            self._latencies.append(ms)

    def render(self) -> str:
        with self._lock:
            lines = []
            for (name, labels), value in sorted(self._counters.items()):
                label_text = ",".join(f'{k}="{v}"' for k, v in labels)
                lines.append(f"{name}{{{label_text}}} {value:g}" if label_text else f"{name} {value:g}")
            if self._latencies:
                ordered = sorted(self._latencies)
                for q in (0.5, 0.95):
                    lines.append(f'chat_latency_ms{{quantile="{q}"}} {ordered[min(len(ordered) - 1, int(q * len(ordered)))]}')
        return "\n".join(lines) + "\n"

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                   "logger": record.name, "event": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "chromadb", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
