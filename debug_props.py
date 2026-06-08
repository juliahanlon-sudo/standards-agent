import sys
import json
from auth import get_token
import aps_client as aps

urn = sys.argv[1] if len(sys.argv) > 1 else None
if not urn:
    print("Usage: python debug_props.py <urn>")
    sys.exit(1)

token = get_token()

print("Fetching views...")
views = aps.get_model_views(token, urn)
print(f"Views: {json.dumps(views, indent=2)}\n")

guid = None
for v in views:
    if v.get("role") == "3d":
        guid = v["guid"]
        break
if not guid and views:
    guid = views[0]["guid"]

# Prefer master view
for v in views:
    if v.get("isMasterView"):
        guid = v["guid"]
        break

print(f"Using GUID: {guid}\n")
print("Fetching properties (may take a moment)...")

encoded = aps.encode_urn(urn)
import requests
from auth import get_token as _gt
r = requests.get(
    f"https://developer.api.autodesk.com/modelderivative/v2/designdata/{encoded}/metadata/{guid}/properties",
    headers={"Authorization": f"Bearer {token}", "x-ads-force": "true"},
    params={"forceget": "true"},
)
print(f"HTTP status: {r.status_code}")
raw = r.json()
print(f"Top-level keys: {list(raw.keys())}")
if "data" in raw:
    print(f"data keys: {list(raw['data'].keys())}")
    collection = raw["data"].get("collection", [])
    print(f"Total objects in collection: {len(collection)}\n")
else:
    print(f"Raw response: {json.dumps(raw, indent=2)[:2000]}")
    collection = []

if collection:
    print("=== First object structure ===")
    print(json.dumps(collection[0], indent=2))
    print()

# Fetch object tree to find furniture element objectids
print("Fetching object tree...")
tree_data = aps.get_object_tree(token, urn, guid)
top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])

# Find the Furniture category node
furniture_node = next((n for n in top_objects if n["name"] == "Furniture"), None)
if not furniture_node:
    print("No Furniture category found in tree!")
else:
    # Collect instance objectids (children of type nodes)
    furniture_ids = set()
    for type_node in furniture_node.get("objects", []):
        for instance in type_node.get("objects", []):
            furniture_ids.add(instance["objectid"])
        if not type_node.get("objects"):
            furniture_ids.add(type_node["objectid"])

    print(f"Found {len(furniture_ids)} furniture instance objectids")

    # Build a lookup from the properties collection
    props_by_id = {obj["objectid"]: obj for obj in collection}

    # Also collect type node objectids (parent level under Furniture)
    type_ids = set()
    for type_node in furniture_node.get("objects", []):
        type_ids.add(type_node["objectid"])

    print(f"Type node objectids: {len(type_ids)}, Instance objectids: {len(furniture_ids)}")

    # Check both instances and types for SFDC params
    sfdc_found = []
    for fid in furniture_ids | type_ids:
        obj = props_by_id.get(fid)
        if not obj:
            continue
        for group, props in obj["properties"].items():
            if isinstance(props, dict):
                for k, v in props.items():
                    if "sfdc" in k.lower():
                        sfdc_found.append({"element": obj["name"], "param": k, "value": v, "has_value": bool(v)})

    print(f"\n=== All SFDC params found ({len(sfdc_found)}) ===")
    for s in sfdc_found[:20]:
        print(f"  [{s['has_value']}] {s['element']} | {s['param']} = '{s['value']}'")

    # Print full properties of first type node to see structure
    first_type_id = next(iter(type_ids))
    obj = props_by_id.get(first_type_id)
    if obj:
        print(f"\n=== First type node properties: {obj['name']} ===")
        print(json.dumps(obj["properties"], indent=2))

