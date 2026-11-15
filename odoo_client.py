"""Client for Odoo's External JSON-2 API."""
import httpx


class OdooError(Exception):
    def __init__(self, message: str, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


class OdooClient:
    def __init__(self, url: str, database: str, api_key: str):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"bearer {api_key}", "X-Odoo-Database": database}

    def call(self, model: str, method: str, **kwargs):
        try:
            response = httpx.post(
                f"{self.url}/json/2/{model}/{method}",
                headers=self.headers,
                json=kwargs,
                timeout=30,
            )
        except httpx.RequestError as exc:
            raise OdooError("Odoo connection failed; the outcome may be unknown.", uncertain=True) from exc
        if response.is_error:
            raise OdooError(f"Odoo returned HTTP {response.status_code}.", uncertain=response.status_code >= 500)
        try:
            return response.json()
        except ValueError as exc:
            raise OdooError("Odoo returned an invalid response.", uncertain=True) from exc
