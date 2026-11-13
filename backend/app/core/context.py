from contextvars import ContextVar
from dataclasses import dataclass, field

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
client_ip_var: ContextVar[str | None] = ContextVar("client_ip", default=None)
source_var: ContextVar[str | None] = ContextVar("source", default=None)      # "demo-live" / "demo-control" on requests made by the demo engine (demo environment only)


@dataclass
class RequestStats:
    """Mutable per-request accumulator. Created in middleware (async context) and shared with worker threads,
    because thread hops copy the context but keep references to the same object."""

    route: str = "unmatched"
    n_queries: int = 0
    db_time_s: float = 0.0
    slowest: list[tuple[float, str]] = field(default_factory=list)  # (seconds, statement head), top 3
    tx_open_at: float | None = None
    pool_wait_s: float = 0.0
    admission_wait_s: float = 0.0
    retries: int = 0

    def add_query(self, seconds: float, stmt: str) -> None:
        self.n_queries += 1
        self.db_time_s += seconds
        self.slowest.append((seconds, stmt))
        self.slowest.sort(reverse=True)
        del self.slowest[3:]


stats_var: ContextVar[RequestStats | None] = ContextVar("request_stats", default=None)
