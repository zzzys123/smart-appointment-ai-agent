"""Container liveness endpoint should not depend on external services."""

import asyncio

from app import create_app


def test_health_endpoint_is_lightweight_and_available():
    application = create_app()
    route = next(route for route in application.routes if route.path == "/health")
    assert asyncio.run(route.endpoint()) == {"status": "ok"}
