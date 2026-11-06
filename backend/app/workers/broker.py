import time
from typing import Protocol

from app.workers.consumers import consumers_for


class Broker(Protocol):
    def publish(self, *, event_id: str, event_type: str, payload: dict, request_id: str | None) -> None:
        """Must raise if the broker did not confirm the message. Called OUTSIDE any DB transaction."""


class CeleryBroker:
    """One durable task per registered consumer. Publisher confirms are enabled in celery_app.conf."""

    def __init__(self):
        from app.workers.celery_app import celery
        self.celery = celery

    def publish(self, *, event_id, event_type, payload, request_id):
        for c in consumers_for(event_type):
            self.celery.send_task("mintree.consume", args=[c.name, event_id, event_type, payload, request_id, time.time()],
                                  queue=c.queue, routing_key=c.queue, retry=True,
                                  retry_policy={"max_retries": 2, "interval_start": 0.1, "interval_step": 0.2})
