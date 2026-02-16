"""Demo script using the SAP OData client library.

Run from the parent directory of sap_odata/:
    python -m sap_odata.example
"""

import sys
from pathlib import Path

# Allow running as `python example.py` from inside sap_odata/
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sap_odata.auth.oauth2 import OAuth2UserTokenExchange
from sap_odata.client import SAPODataClient


def main():
    # Authenticate
    auth = OAuth2UserTokenExchange(
        service_key_path=str(Path(__file__).resolve().parent / ".default_key")
    )
    client = SAPODataClient(
        auth=auth,
        base_url=auth.base_url,
        catalog_path=auth.catalog_path,
    )

    # List catalog services
    print("=== Listing OData Services ===\n")
    services = client.list_services()
    print(f"Found {len(services)} services:\n")
    for svc in services[:20]:
        print(f"  {svc.technical_name} (v{svc.version}) - {svc.title}")
    if len(services) > 20:
        print(f"\n  ... and {len(services) - 20} more")

    # Try to describe a flight service if available
    flight_services = [s for s in services if "FLIGHT" in s.technical_name.upper()]
    if flight_services:
        svc = flight_services[0]
        service_path = svc.service_path
        print(f"\n=== Describing {svc.technical_name} ===\n")
        try:
            description = client.describe_service(service_path)
            print(description)
        except Exception as e:
            print(f"  Could not describe service: {e}")

        # Try a simple query
        print(f"\n=== Querying {svc.technical_name} ===\n")
        try:
            metadata = client.get_metadata(service_path)
            if metadata.entity_sets:
                es = metadata.entity_sets[0]
                print(f"Querying {es.name} (top 5)...")
                results = client.read(service_path, es.name, top="5")
                for row in results:
                    # Filter out OData metadata keys
                    clean = {k: v for k, v in row.items() if not k.startswith("__")}
                    print(f"  {clean}")
        except Exception as e:
            print(f"  Query failed: {e}")
    else:
        print("\nNo FLIGHT services found. Try describe_service() with a known service path.")


if __name__ == "__main__":
    main()
