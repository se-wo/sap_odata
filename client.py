from __future__ import annotations

import requests

from .auth import AuthProvider
from .metadata import describe, parse_metadata
from .models import ServiceInfo, ServiceMetadata
from .query import Query


class SAPODataClient:
    """Main entry point for the SAP OData client.

    Usage:
        from sap_odata.auth.oauth2 import OAuth2UserTokenExchange
        from sap_odata.client import SAPODataClient

        auth = OAuth2UserTokenExchange(".default_key")
        client = SAPODataClient(auth, base_url=auth.base_url, catalog_path=auth.catalog_path)
        services = client.list_services()
    """

    def __init__(self, auth: AuthProvider, base_url: str, catalog_path: str):
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._catalog_path = catalog_path
        self._session = requests.Session()
        self._session.headers["Accept"] = "application/json"
        self._authenticated = False
        self._csrf_token: str | None = None

    def _ensure_auth(self) -> None:
        if not self._authenticated:
            self._auth.authenticate(self._session)
            self._authenticated = True

    def _get(self, url: str, **kwargs: object) -> requests.Response:
        self._ensure_auth()
        resp = self._session.get(url, **kwargs)
        if resp.status_code == 401:
            # Token may have expired — re-authenticate once
            self._authenticated = False
            self._ensure_auth()
            resp = self._session.get(url, **kwargs)
        return resp

    def _fetch_csrf_token(self) -> str:
        """Fetch a CSRF token via HEAD request (required for POST/PUT/DELETE on SAP)."""
        self._ensure_auth()
        resp = self._session.head(
            self._base_url + "/",
            headers={"x-csrf-token": "Fetch"},
        )
        token = resp.headers.get("x-csrf-token", "")
        self._csrf_token = token
        return token

    def _post(self, url: str, json: dict | None = None, **kwargs: object) -> requests.Response:
        """POST with CSRF token handling and 401 retry."""
        self._ensure_auth()
        if not self._csrf_token:
            self._fetch_csrf_token()

        headers = kwargs.pop("headers", {}) if "headers" in kwargs else {}
        headers["x-csrf-token"] = self._csrf_token
        resp = self._session.post(url, json=json, headers=headers, **kwargs)

        if resp.status_code == 401:
            self._authenticated = False
            self._ensure_auth()
            self._fetch_csrf_token()
            headers["x-csrf-token"] = self._csrf_token
            resp = self._session.post(url, json=json, headers=headers, **kwargs)
        elif resp.status_code == 403:
            # CSRF token may have expired — refresh and retry
            self._fetch_csrf_token()
            headers["x-csrf-token"] = self._csrf_token
            resp = self._session.post(url, json=json, headers=headers, **kwargs)

        return resp

    def list_services(self) -> list[ServiceInfo]:
        """List all OData services from the ABAP catalog."""
        url = f"{self._base_url}{self._catalog_path}/ServiceCollection?$format=json"
        resp = self._get(url)
        resp.raise_for_status()

        results = resp.json().get("d", {}).get("results", [])
        services = []
        for svc in results:
            services.append(ServiceInfo(
                technical_name=svc.get("TechnicalServiceName", ""),
                version=svc.get("TechnicalServiceVersion", ""),
                title=svc.get("Title", ""),
                url=svc.get("ServiceUrl", ""),
            ))
        return services

    def get_metadata(self, service_path: str) -> ServiceMetadata:
        """Fetch and parse $metadata for a service.

        Args:
            service_path: e.g. "/sap/opu/odata/sap/API_FLIGHT_SRV"
        """
        url = f"{self._base_url}{service_path}/$metadata"
        self._ensure_auth()
        # Metadata is XML, override Accept for this request
        resp = self._session.get(url, headers={"Accept": "application/xml"})
        if resp.status_code == 401:
            self._authenticated = False
            self._ensure_auth()
            resp = self._session.get(url, headers={"Accept": "application/xml"})
        resp.raise_for_status()
        return parse_metadata(resp.text)

    def describe_service(self, service_path: str) -> str:
        """Return an LLM-friendly text description of a service's data model."""
        metadata = self.get_metadata(service_path)
        return describe(metadata)

    def query(self, service_path: str, entity_set: str) -> Query:
        """Create a Query builder for an entity set.

        The returned Query is attached to this client, so ``.execute()``,
        ``.execute_all()``, and ``.get_count()`` work directly::

            results = client.query("/sap/.../SRV", "Flights").filter(F.eq("CarrierId", "LH")).top(5).execute()
        """
        q = Query(entity_set)
        q._service_path = service_path
        q._client = self
        return q

    def read(self, service_path: str, entity_set: str, **params: str) -> list[dict]:
        """Shorthand: GET an entity set and return results as list of dicts.

        Args:
            service_path: e.g. "/sap/opu/odata/sap/API_FLIGHT_SRV"
            entity_set: e.g. "FlightCollection"
            **params: OData query params like top="10", filter="CarrierId eq 'LH'"
        """
        q = Query(entity_set)
        for key, value in params.items():
            if key == "top":
                q.top(int(value))
            elif key == "skip":
                q.skip(int(value))
            elif key == "filter":
                q.filter(value)
            elif key == "select":
                q.select(*value.split(","))
            elif key == "expand":
                q.expand(*value.split(","))
            elif key == "orderby":
                q.orderby(*value.split(","))

        return self.execute_query(service_path, q)

    def execute_query(self, service_path: str, query: Query) -> list[dict]:
        """Execute a Query and return results as list of dicts."""
        path = query.build_path()
        qs = query.build()
        url = f"{self._base_url}{service_path}/{path}"
        if qs:
            url += f"?{qs}"

        resp = self._get(url)
        resp.raise_for_status()
        data = resp.json()

        return self._parse_response(data)

    def call_action(
        self, service_path: str, action_name: str, data: dict | None = None
    ) -> dict | list[dict]:
        """Invoke an unbound OData V4 action.

        Args:
            service_path: e.g. "/sap/opu/odata4/sap/API_FLIGHT_SRV/srvd_a2x/sap/flight/0001"
            action_name: e.g. "ConfirmFlight"
            data: Optional JSON body for the action.

        Returns:
            Parsed response — a dict (single result) or list of dicts.
        """
        url = f"{self._base_url}{service_path}/{action_name}"
        resp = self._post(url, json=data or {})
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        result = resp.json()
        return self._parse_response(result)

    def _execute_bound_action(
        self, service_path: str, query: Query, data: dict | None = None
    ) -> dict | list[dict]:
        """Execute a bound action on an entity addressed by a Query."""
        path = query.build_path()
        url = f"{self._base_url}{service_path}/{path}"
        resp = self._post(url, json=data or {})
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        result = resp.json()
        return self._parse_response(result)

    @staticmethod
    def _parse_response(data: dict) -> list[dict]:
        """Parse an OData response, supporting both V4 and V2 envelopes."""
        # V4: { "value": [...] }
        if "value" in data:
            value = data["value"]
            if isinstance(value, list):
                return value
            # Single entity wrapped in value
            return [value] if isinstance(value, dict) else []
        # V2: { "d": { "results": [...] } }
        d = data.get("d", {})
        if isinstance(d, dict):
            if "results" in d:
                return d["results"]
            if d:
                return [d]
        return []

    def _execute_all(
        self, service_path: str, query: Query, *, max_pages: int = 100
    ) -> list[dict]:
        """Execute a query and follow pagination links (V4 ``@odata.nextLink`` or V2 ``__next``)."""
        all_results: list[dict] = []

        # First page
        path = query.build_path()
        qs = query.build()
        url = f"{self._base_url}{service_path}/{path}"
        if qs:
            url += f"?{qs}"

        for _ in range(max_pages):
            resp = self._get(url)
            resp.raise_for_status()
            data = resp.json()

            all_results.extend(self._parse_response(data))

            # V4 pagination
            next_url = data.get("@odata.nextLink")
            # V2 fallback
            if not next_url:
                d = data.get("d", {})
                if isinstance(d, dict):
                    next_url = d.get("__next")
            if not next_url:
                break
            url = next_url

        return all_results

    def _execute_count(self, service_path: str, query: Query) -> int:
        """Execute a ``/$count`` request and return the integer count."""
        path = query.build_path()
        qs = query.build()
        url = f"{self._base_url}{service_path}/{path}"
        if qs:
            url += f"?{qs}"

        resp = self._get(url, headers={"Accept": "text/plain"})
        resp.raise_for_status()
        return int(resp.text.strip())
