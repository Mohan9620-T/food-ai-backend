import json
import logging
import re
from contextvars import ContextVar
from uuid import uuid4

from app.config import settings

request_id: ContextVar[str] = ContextVar("request_id", default="")
logger = logging.getLogger(__name__)


class RequestLogFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(record, "request_id", None) or request_id.get()
        return True


class RequestIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not settings.ENABLE_REQUEST_IDS:
            await self.app(scope, receive, send)
            return
        supplied = (
            dict(scope.get("headers", []))
            .get(b"x-request-id", b"")
            .decode("ascii", errors="ignore")
        )
        identifier = supplied if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", supplied) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = identifier
        token = request_id.set(identifier)
        pending = None
        body = b""

        async def wrapped(message):
            nonlocal pending, body
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ] + [(b"x-request-id", identifier.encode())]
                if message["status"] >= 400 and b"application/json" in dict(message["headers"]).get(
                    b"content-type", b""
                ):
                    pending = message
                    return
            if pending is not None and message["type"] == "http.response.body":
                body += message.get("body", b"")
                if message.get("more_body"):
                    return
                try:
                    value = json.loads(body)
                    if isinstance(value, dict):
                        value["request_id"] = identifier
                        body = json.dumps(value).encode()
                except (ValueError, UnicodeError):
                    pass
                pending["headers"] = [
                    (k, v) for k, v in pending["headers"] if k.lower() != b"content-length"
                ] + [(b"content-length", str(len(body)).encode())]
                await send(pending)
                pending = None
                message = {**message, "body": body}
            await send(message)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            request_id.reset(token)


def setup_tracing(app, engine):
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT))
        )
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
        SQLAlchemyInstrumentor().instrument(engine=engine, tracer_provider=provider)
    except ImportError:
        logger.warning("Tracing unavailable: install requirements-observability.txt")
