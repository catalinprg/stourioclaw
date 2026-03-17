from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

def setup_tracing(app):
    resource = Resource(attributes={
        SERVICE_NAME: "stourio-orchestrator"
    })
    
    provider = TracerProvider(resource=resource)
    
    # Export to console for local debugging
    console_processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(console_processor)
    
    # Export to Jaeger OTLP collector (matches docker-compose service name)
    otlp_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317"))
    provider.add_span_processor(otlp_processor)
    
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)

tracer = trace.get_tracer("stourio")