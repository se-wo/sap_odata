# sap_odata

A Python client library for SAP OData services. Connects to SAP BTP ABAP systems via OAuth2, parses `$metadata` with full vocabulary annotation support, and provides a fluent query builder targeting OData V4 (with V2 backward compatibility).

## Setup

**Requirements:** Python 3.10+, `requests`

**Authentication:** Place a BTP service key file at `.default_key` in the project root. The key must contain `uaa` credentials and catalog path configuration.

```python
from sap_odata.auth.oauth2 import OAuth2UserTokenExchange
from sap_odata.client import SAPODataClient

auth = OAuth2UserTokenExchange(".default_key")
client = SAPODataClient(auth=auth, base_url=auth.base_url, catalog_path=auth.catalog_path)
```

First call triggers a browser-based OAuth2 login. Tokens are cached locally in `.token_cache`.

## Usage

### List services

```python
services = client.list_services()
for svc in services:
    print(f"{svc.technical_name} v{svc.version} — {svc.title}")
```

### Query data

```python
from sap_odata import F, Query

# Simple read
results = client.read("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel", top="5")

# Fluent query builder
travels = (
    client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel")
    .select("TravelID", "AgencyID", "TotalPrice", "CurrencyCode")
    .filter(F.eq("Status", "O") & F.gt("TotalPrice", 1000))
    .orderby("TotalPrice desc")
    .top(10)
    .execute()
)

# Auto-pagination
all_travels = (
    client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel")
    .filter(F.eq("Status", "O"))
    .execute_all(max_pages=50)
)

# Count
n = client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel").get_count()

# Single entity by key
travel = (
    client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel")
    .key(TravelID="00000001")
    .execute()
)

# Navigation + expand
bookings = (
    client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel")
    .key(TravelID="00000001")
    .nav("to_Booking")
    .execute()
)

# Bound action
result = (
    client.query("/sap/opu/odata/dmo/UI_TRAVEL_U_V2", "Travel")
    .key(TravelID="00000001")
    .action("set_status_booked")
    .execute_action()
)
```

### Explore a service (metadata-driven)

```python
from sap_odata.explorer import explore_service

metadata = client.get_metadata("/sap/opu/odata/dmo/UI_TRAVEL_U_V2")
print(explore_service(metadata))
```

Produces a structured markdown report covering:
- **Overview** — namespace, entity/action/function counts
- **Entity Sets** — CRUD capabilities and labeled key fields
- **Field Analysis** — keys, code-to-text pairs, measures with units, semantic fields, DDIC quickinfo, display hints, and a full property table per entity type
- **Value Helps** — resolved with key mappings, auto-fill fields, dropdown display columns, and search support
- **Relationships** — associations with cardinality and navigation property names
- **Actions & Functions** — V2 function imports and V4 actions/functions

SAP framework entity sets (`SAP__*`) and RAP control fields (`*_ac`, `*_oc`) are automatically filtered from the report.

### Describe a service (flat dump)

```python
print(client.describe_service("/sap/opu/odata/dmo/UI_TRAVEL_U_V2"))
```

Simpler flat text output. Use `explore_service` for the richer vocabulary-aware report.

## Project structure

```
sap_odata/
  __init__.py          # Package exports
  client.py            # SAPODataClient — HTTP, auth, CSRF, pagination
  query.py             # Query builder, F filter factory, FilterExpression
  models.py            # Dataclasses (ServiceMetadata, EntityType, Property, etc.)
  metadata.py          # $metadata XML parser + describe()
  explorer.py          # Vocabulary-aware exploration report builder
  example.py           # Demo script
  auth/
    __init__.py        # AuthProvider ABC
    oauth2.py          # OAuth2UserTokenExchange (browser login + JWT exchange)
.claude/
  commands/
    odata-explore.md   # Claude Code slash command for /odata-explore
```

## Vocabulary annotations supported

The metadata parser extracts these SAP vocabulary annotations from `$metadata`:

| Annotation | Source | Used for |
|------------|--------|----------|
| `sap:label` | V2 attribute | Field display name |
| `sap:text` | V2 attribute | Code → description text field mapping |
| `sap:unit` | V2 attribute | Measure → unit field mapping |
| `sap:semantics` | V2 attribute | Semantic meaning (currency-code, email, tel, unit-of-measure) |
| `sap:quickinfo` | V2 attribute | DDIC long description |
| `sap:display-format` | V2 attribute | Display hints (UpperCase, NonNegative, Date) |
| `sap:value-list` | V2 attribute | Marks field as having a value help |
| `sap:filterable` / `sap:sortable` | V2 attribute | Field capabilities |
| `sap:creatable` / `sap:updatable` / `sap:deletable` | V2 attribute | Entity set CRUD flags |
| `Common.ValueList` | V4 annotation | Full value help definition with parameters |
| `Capabilities.InsertRestrictions` etc. | V4 annotation | Entity set CRUD restrictions |

## Known limitations

### Parser gaps

- **`PropertyPath` was silently dropped** (fixed) — V4 annotations use `PropertyPath` for property references in value help parameters. Prior to the fix, `LocalDataProperty` values were lost, causing value help key mappings to show as `None`.
- **Complex Types not parsed** — `<ComplexType>` definitions are ignored. Services using complex types for action parameters or structured properties will have incomplete reporting.
- **No `NavigationPropertyPath` support** — similar to the `PropertyPath` issue, `NavigationPropertyPath` attribute values in annotations are not extracted.

### Explorer gaps

- **No `sap:requires-filter` / `sap:required-in-filter`** — the parser doesn't extract these attributes, so the explorer can't warn about entity sets that require mandatory filters (queries will fail with HTTP 400 without them).
- **No V4 `FilterRestrictions.RequiredProperties`** — the V4 equivalent of required-in-filter is also not surfaced.
- **No `sap:field-control`** — dynamic field control (hidden/readonly/editable/mandatory) referencing another property is not parsed or reported.
- **No `sap:heading`** — shorter column headers (distinct from `sap:label`) are not extracted.
- **No `Common.TextArrangement`** — text-code display order (TextFirst, TextLast, TextOnly) is not surfaced.
- **No `Common.SemanticObject`** — Fiori intent-based navigation targets are not reported.
- **No UI vocabulary** — `UI.LineItem`, `UI.HeaderInfo`, `UI.SelectionFields`, `UI.Facets` annotations that define Fiori layouts are not parsed.
- **No `sap:pageable` / `sap:topable`** — whether an entity set supports `$top`/`$skip` is not surfaced.
- **No `sap:aggregation-role`** — analytical dimension/measure classification is not reported.
- **No hierarchy annotations** — `sap:hierarchy-node-for`, `sap:hierarchy-parent-node-for`, `sap:hierarchy-level-for` are not parsed.
- **Relationship direction is ambiguous** — associations are displayed from end1 to end2 as they appear in metadata, which may not reflect the logical parent-child direction. Back-navigation links can read misleadingly.
- **No composition inference** — the explorer doesn't distinguish compositions (Travel → Booking → BookingSupplement) from loose associations. In V2 metadata, composition isn't explicitly annotated, but could be inferred from shared key prefixes and CUD capabilities.

### Client limitations

- **V2 catalog only** — `list_services()` uses the V2 catalog API (`ServiceCollection`). V4 service discovery is not implemented.
- **No batch requests** — `$batch` is not supported. Each query is a separate HTTP call.
- **No `$apply` / aggregation** — analytical queries using `$apply` (groupby, aggregate) are not supported in the query builder.
- **No deep insert / deep update** — creating or updating nested entities in a single request is not supported.
- **No ETag handling** — optimistic concurrency via `If-Match` headers is not implemented.
- **No `$search`** — free-text search parameter is not exposed in the query builder.
- **OAuth2 only** — the only auth provider is `OAuth2UserTokenExchange` for BTP ABAP. Basic auth, client credentials, and SAML are not implemented.
