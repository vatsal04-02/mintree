from celery import Celery
from celery.signals import worker_process_init, worker_ready
from kombu import Exchange, Queue

from app.core.config import get_settings

QUEUES = ("default", "notifications", "integrations", "imports", "reports", "documents")
DLX = Exchange("mintree.dlx", type="fanout", durable=True)

s = get_settings()
celery = Celery("mintree", broker=s.broker_url)
celery.conf.update(
    task_serializer="json", accept_content=["json"], result_backend=None, task_ignore_result=True,
    # Durability & recovery: ack only after the task finished; if a worker dies the broker redelivers.
    task_acks_late=True, task_reject_on_worker_lost=True, worker_prefetch_multiplier=1,
    task_default_delivery_mode="persistent", broker_transport_options={"confirm_publish": True},
    broker_connection_retry_on_startup=True, task_time_limit=120, task_soft_time_limit=100,
    task_default_queue="default",
    task_queues=[Queue(q, Exchange("mintree", type="direct", durable=True), routing_key=q, durable=True,
                       queue_arguments={"x-dead-letter-exchange": "mintree.dlx"}) for q in QUEUES]
    # Messages rejected/expired from any queue land here and are inspected via the admin API / RabbitMQ UI.
    + [Queue("dead_letter", DLX, durable=True)],
    imports=("app.workers.tasks",),
)


@worker_process_init.connect
def _fresh_db_pool(**_):
    # Pools must not be shared across fork().
    from app.core.db import engine
    engine.dispose(close=False)


@worker_ready.connect
def _metrics_server(**_):
    import os
    from prometheus_client import CollectorRegistry, multiprocess, start_http_server, REGISTRY
    port = int(os.environ.get("MINTREE_WORKER_METRICS_PORT", "9101"))
    try:
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            reg = CollectorRegistry(); multiprocess.MultiProcessCollector(reg)
        else:
            reg = REGISTRY
        start_http_server(port, registry=reg)
    except OSError:
        pass  # another worker on this host already serves metrics
