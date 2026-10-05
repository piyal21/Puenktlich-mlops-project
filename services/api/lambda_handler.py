"""AWS Lambda entry point: API Gateway HTTP API events -> the FastAPI app (Mangum)."""

from mangum import Mangum

from app.main import app

handler = Mangum(app, lifespan="off")
