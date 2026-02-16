from __future__ import annotations

import re
import urllib.parse
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .client import SAPODataClient


# ---------------------------------------------------------------------------
# OData V2 literal formatting
# ---------------------------------------------------------------------------

_GUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _format_literal(value: Any) -> str:
    """Format a Python value as an OData V2 literal."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value}m"
    if isinstance(value, str):
        # Detect guid strings
        if _GUID_RE.match(value):
            return f"guid'{value}'"
        # Detect datetime-like strings (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
        if re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$", value):
            dt = value if "T" in value else f"{value}T00:00:00"
            return f"datetime'{dt}'"
        # Regular string — single-quote, escape inner quotes
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def _format_key_literal(value: Any) -> str:
    """Format a Python value as an OData V2 key literal (strings always quoted)."""
    if isinstance(value, str):
        # For keys, datetime and guid detection still applies
        if _GUID_RE.match(value):
            return f"guid'{value}'"
        if re.match(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2})?$", value):
            dt = value if "T" in value else f"{value}T00:00:00"
            return f"datetime'{dt}'"
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return _format_literal(value)


# ---------------------------------------------------------------------------
# FilterExpression — composable filter tree
# ---------------------------------------------------------------------------

class FilterExpression:
    """A composable OData filter expression node.

    Supports ``&`` (and), ``|`` (or), and ``~`` (not) operators.
    """

    def __init__(self, expr: str):
        self._expr = expr

    def __and__(self, other: FilterExpression) -> FilterExpression:
        return FilterExpression(f"{self._expr} and {other._expr}")

    def __or__(self, other: FilterExpression) -> FilterExpression:
        return FilterExpression(f"({self._expr} or {other._expr})")

    def __invert__(self) -> FilterExpression:
        return FilterExpression(f"not ({self._expr})")

    def __str__(self) -> str:
        return self._expr

    def __repr__(self) -> str:
        return f"FilterExpression({self._expr!r})"


# ---------------------------------------------------------------------------
# F — user-facing filter factory
# ---------------------------------------------------------------------------

class F:
    """Static factory for building OData V2 filter expressions."""

    @staticmethod
    def _compare(field: str, op: str, value: Any) -> FilterExpression:
        return FilterExpression(f"{field} {op} {_format_literal(value)}")

    @staticmethod
    def eq(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "eq", value)

    @staticmethod
    def ne(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "ne", value)

    @staticmethod
    def gt(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "gt", value)

    @staticmethod
    def ge(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "ge", value)

    @staticmethod
    def lt(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "lt", value)

    @staticmethod
    def le(field: str, value: Any) -> FilterExpression:
        return F._compare(field, "le", value)

    # String functions
    @staticmethod
    def startswith(field: str, value: str) -> FilterExpression:
        escaped = value.replace("'", "''")
        return FilterExpression(f"startswith({field},'{escaped}')")

    @staticmethod
    def endswith(field: str, value: str) -> FilterExpression:
        escaped = value.replace("'", "''")
        return FilterExpression(f"endswith({field},'{escaped}')")

    @staticmethod
    def contains(field: str, value: str) -> FilterExpression:
        """OData V2 uses ``substringof('value', Field)``."""
        escaped = value.replace("'", "''")
        return FilterExpression(f"substringof('{escaped}',{field})")

    # Null checks
    @staticmethod
    def is_null(field: str) -> FilterExpression:
        return FilterExpression(f"{field} eq null")

    @staticmethod
    def not_null(field: str) -> FilterExpression:
        return FilterExpression(f"{field} ne null")

    # Raw escape hatch
    @staticmethod
    def raw(expression: str) -> FilterExpression:
        return FilterExpression(expression)


# ---------------------------------------------------------------------------
# Query — fluent OData query builder
# ---------------------------------------------------------------------------

class Query:
    """Fluent OData query builder with optional client-attached execution.

    Usage::

        q = (Query("Flights")
             .select("CarrierId", "FlightDate", "Price")
             .filter(F.eq("CarrierId", "LH") & F.gt("Price", 100))
             .top(10))

        # Build query string only
        url_params = q.build()

        # Or, when created via client.query():
        results = q.execute()
    """

    def __init__(self, entity_set: str):
        self._entity_set = entity_set
        self._select: list[str] = []
        self._filter: str | None = None
        self._expand: list[str] = []
        self._orderby: list[str] = []
        self._top: int | None = None
        self._skip: int | None = None
        self._count: bool = False
        self._count_only: bool = False
        self._format: str = "json"
        self._keys: dict[str, Any] | None = None
        self._nav: list[str] = []
        self._custom: list[tuple[str, str]] = []
        # Attached by SAPODataClient.query()
        self._service_path: str | None = None
        self._client: SAPODataClient | None = None

    # ---- Field selection ----

    def select(self, *fields: str) -> Query:
        self._select.extend(fields)
        return self

    # ---- Filtering ----

    def filter(self, expression: str | FilterExpression) -> Query:
        self._filter = str(expression)
        return self

    # ---- Expansion ----

    def expand(self, *nav_props: str) -> Query:
        self._expand.extend(nav_props)
        return self

    # ---- Ordering ----

    def orderby(self, *fields: str) -> Query:
        self._orderby.extend(fields)
        return self

    # ---- Paging ----

    def top(self, n: int) -> Query:
        self._top = n
        return self

    def skip(self, n: int) -> Query:
        self._skip = n
        return self

    # ---- Counting ----

    def count(self, enabled: bool = True) -> Query:
        """Add ``$inlinecount=allpages`` to the query."""
        self._count = enabled
        return self

    # ---- Format ----

    def format(self, fmt: str) -> Query:
        self._format = fmt
        return self

    # ---- Key-based single entity access ----

    def key(self, __single: Any = None, **keys: Any) -> Query:
        """Address a single entity by key.

        Single key::

            Query("Flights").key("LH")
            Query("Flights").key(CarrierId="LH")

        Composite key::

            Query("Flights").key(CarrierId="LH", ConnectionId="0400")
        """
        if __single is not None:
            self._keys = {"__single__": __single}
        else:
            self._keys = dict(keys)
        return self

    # ---- Navigation property traversal ----

    def nav(self, *nav_props: str) -> Query:
        """Append navigation segments to the resource path.

        Example::

            Query("Flights").key(CarrierId="LH").nav("to_Bookings")
            # -> Flights(CarrierId='LH')/to_Bookings
        """
        self._nav.extend(nav_props)
        return self

    # ---- Custom query parameters ----

    def custom(self, name: str, value: str) -> Query:
        """Add an arbitrary query parameter (escape hatch)."""
        self._custom.append((name, value))
        return self

    # ---- Properties ----

    @property
    def entity_set(self) -> str:
        return self._entity_set

    # ---- Path building ----

    def build_path(self) -> str:
        """Build the resource path (entity set + key + navigation), without query params."""
        path = self._entity_set

        if self._keys is not None:
            if "__single__" in self._keys:
                path += f"({_format_key_literal(self._keys['__single__'])})"
            else:
                parts = [
                    f"{k}={_format_key_literal(v)}"
                    for k, v in self._keys.items()
                ]
                path += f"({','.join(parts)})"

        if self._nav:
            path += "/" + "/".join(self._nav)

        if self._count_only:
            path += "/$count"

        return path

    # ---- Query-string building ----

    def build(self) -> str:
        """Build the OData query string (without leading '?')."""
        params: list[tuple[str, str]] = []

        if self._select:
            params.append(("$select", ",".join(self._select)))
        if self._filter:
            params.append(("$filter", self._filter))
        if self._expand:
            params.append(("$expand", ",".join(self._expand)))
        if self._orderby:
            params.append(("$orderby", ",".join(self._orderby)))
        if self._top is not None:
            params.append(("$top", str(self._top)))
        if self._skip is not None:
            params.append(("$skip", str(self._skip)))
        if self._count:
            params.append(("$inlinecount", "allpages"))
        if self._format and not self._count_only:
            params.append(("$format", self._format))

        for name, value in self._custom:
            params.append((name, value))

        return urllib.parse.urlencode(params, safe="$,/ '")

    # ---- Execution (requires attached client) ----

    def _require_client(self) -> tuple[SAPODataClient, str]:
        if self._client is None or self._service_path is None:
            raise RuntimeError(
                "This Query is not attached to a client. "
                "Use client.query(service_path, entity_set) to create an executable query."
            )
        return self._client, self._service_path

    def execute(self) -> list[dict]:
        """Execute the query and return results as a list of dicts."""
        client, service_path = self._require_client()
        return client.execute_query(service_path, self)

    def execute_all(self, max_pages: int = 100) -> list[dict]:
        """Execute with auto-pagination, following ``__next`` links.

        Args:
            max_pages: Maximum number of pages to fetch (safety limit).
        """
        client, service_path = self._require_client()
        return client._execute_all(service_path, self, max_pages=max_pages)

    def get_count(self) -> int:
        """Execute a ``/$count`` request and return the integer count."""
        client, service_path = self._require_client()
        # Temporarily set count_only mode
        prev = self._count_only
        self._count_only = True
        try:
            return client._execute_count(service_path, self)
        finally:
            self._count_only = prev

    # ---- Repr ----

    def __repr__(self) -> str:
        return f"Query({self.build_path()!r}, {self.build()!r})"
