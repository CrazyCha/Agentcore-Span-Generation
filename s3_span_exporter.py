"""Custom OpenTelemetry SpanExporter that writes span batches directly to S3."""

import json
import time
import uuid
from typing import Sequence

import boto3
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult


class S3SpanExporter(SpanExporter):

    def __init__(self, bucket_name: str, prefix: str = "spans", region: str | None = None):
        self._bucket = bucket_name
        self._prefix = prefix.rstrip("/")
        self._s3 = boto3.client("s3", region_name=region) if region else boto3.client("s3")

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return SpanExportResult.SUCCESS

        print(f"[S3SpanExporter] Exporting {len(spans)} span(s)...")
        batch = [self._span_to_dict(s) for s in spans]

        first = batch[0]
        session_id = first.get("attributes", {}).get("session.id", "no-session")
        trace_id = first.get("trace_id", "no-trace")
        ts = int(time.time() * 1000)
        key = f"{self._prefix}/{session_id}/{trace_id}/{ts}-{uuid.uuid4().hex[:8]}.json"

        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=json.dumps(batch, default=str, ensure_ascii=False),
                ContentType="application/json",
            )
            return SpanExportResult.SUCCESS
        except Exception as e:
            print(f"[S3SpanExporter] Failed to write to s3://{self._bucket}/{key}: {e}")
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    @staticmethod
    def _span_to_dict(span: ReadableSpan) -> dict:
        ctx = span.get_span_context()
        parent = span.parent
        return {
            "trace_id": format(ctx.trace_id, "032x"),
            "span_id": format(ctx.span_id, "016x"),
            "parent_span_id": format(parent.span_id, "016x") if parent else None,
            "name": span.name,
            "kind": span.kind.name if span.kind else None,
            "start_time_unix_nano": span.start_time,
            "end_time_unix_nano": span.end_time,
            "duration_ms": (span.end_time - span.start_time) / 1e6 if span.end_time and span.start_time else None,
            "status": {
                "code": span.status.status_code.name,
                "description": span.status.description,
            },
            "attributes": dict(span.attributes) if span.attributes else {},
            "events": [
                {
                    "name": e.name,
                    "timestamp": e.timestamp,
                    "attributes": dict(e.attributes) if e.attributes else {},
                }
                for e in span.events
            ] if span.events else [],
        }
