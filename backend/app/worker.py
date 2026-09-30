"""Run a worker and a single beat scheduler; durable state lives in SQL, not Celery results."""

from celery import Celery
from sqlalchemy import select

from .config import Settings
from .database import make_engine, make_sessions
from .indexing import process_index, schedule
from .ingestion import delete_object, dispatch, process_job
from .models import Tenant
from .qdrant_index import sync_ready_documents
from .resource_execution import cleanup_resource, process_resource, recover_resources

settings = Settings()
QUEUE = f"xflj-knowledge-{settings.tenant_slug}"
app = Celery("xflj", broker=settings.redis_url.get_secret_value())
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 300},
    task_default_queue=QUEUE,
    task_routes={"xflj.*": {"queue": QUEUE}},
    beat_schedule={"dispatch-outbox": {"task": "xflj.dispatch", "schedule": 10.0}},
)


def run_scoped(callback, tenant_id=None):
    engine = make_engine(settings.database_url)
    sessions = make_sessions(engine)
    try:
        with sessions() as session:
            tenant = session.scalar(
                select(Tenant).where(Tenant.slug == settings.tenant_slug, Tenant.active.is_(True))
            )
            if not tenant or (tenant_id and tenant.id != tenant_id):
                return
            identity = tenant.id
        return callback(sessions, identity)
    finally:
        engine.dispose()


@app.task(name="xflj.dispatch")
def dispatch_outbox():
    def publish(kind, tenant_id, target_id, event_id):
        name = {
            "document.ingest": "xflj.ingest",
            "document.index": "xflj.index",
            "document.delete": "xflj.delete",
            "resource.step": "xflj.resource",
            "resource.cleanup": "xflj.resource_cleanup",
        }[kind]
        app.send_task(
            name,
            args=[tenant_id, target_id],
            task_id=event_id,
            retry=False,
            argsrepr="(<school>, <document-job>)",
        )

    def run(sessions, tenant):
        schedule(sessions, settings, tenant)
        sync_ready_documents(sessions, settings, tenant)
        recover_resources(sessions, tenant)
        return dispatch(sessions, tenant, publish)

    return run_scoped(run)


@app.task(name="xflj.resource", soft_time_limit=160, time_limit=175)
def resource_step(tenant_id, task_id):
    run_scoped(lambda sessions, _: process_resource(sessions, settings, tenant_id, task_id), tenant_id)


@app.task(
    name="xflj.resource_cleanup",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=None,
)
def resource_cleanup(tenant_id, task_id):
    run_scoped(lambda sessions, _: cleanup_resource(sessions, settings, tenant_id, task_id), tenant_id)


@app.task(name="xflj.index", soft_time_limit=160, time_limit=175)
def index_document(tenant_id, job_id):
    run_scoped(lambda sessions, _: process_index(sessions, settings, tenant_id, job_id), tenant_id)


@app.task(name="xflj.ingest", soft_time_limit=160, time_limit=175)
def ingest(tenant_id, job_id):
    run_scoped(lambda sessions, _: process_job(sessions, settings, tenant_id, job_id), tenant_id)


@app.task(
    name="xflj.delete",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    max_retries=None,
)
def remove_object(tenant_id, document_id):
    run_scoped(lambda sessions, _: delete_object(sessions, settings, tenant_id, document_id), tenant_id)
