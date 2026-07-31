from fastapi.testclient import TestClient


def test_health_ok_without_auth(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "vault_readable": True, "inbox_writable": True}


def test_health_degraded_when_inbox_unwritable(client: TestClient, inbox_root) -> None:
    inbox_root.chmod(0o500)
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert response.json()["inbox_writable"] is False
    finally:
        inbox_root.chmod(0o700)
