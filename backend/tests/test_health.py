from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_ok():
    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
