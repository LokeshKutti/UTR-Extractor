"""
AWS Lambda entry point
=======================
Wraps the same FastAPI app server.py exposes everywhere else, so it can run
as a Lambda function instead of a long-lived server. Nothing about core.py,
medical.py or server.py's own routes changes for this -- Mangum translates a
Lambda event (from a Function URL or API Gateway) into the ASGI request
server.py already knows how to handle, and translates the response back.

Dockerfile.lambda's CMD points at "lambda_handler.handler" -- that's the
name Lambda's runtime actually calls per invocation.
"""

from __future__ import annotations

from mangum import Mangum

from server import app

handler = Mangum(app)
