"""
Diagnostic: tests whether room boundaries and furniture XYZ positions
are accessible via the Model Derivative API for a spatial join approach.
"""

import re
import json
import requests
from auth import get_token
import aps_client as aps

HUB_ID     = "b.8a643169-4b2b-4c79-bff4-289208a76b2e"
PROJECT_ID = "b.6ecebf3f-9519-4fc9-8e2b-97ae184fce04"  # Denver

INTERIOR_URN  = "urn:adsk.wipprod:fs.file:vf.bRQE7yHISlqslJviro8Udg?version=15"
FURNITURE_URN = "urn:adsk.wipprod:fs.file:vf.q6MJLx9YTZ-81tI3rbcbTg?version=11"

BASE_URL = "https://developer.api.autodesk.com"

def sep(char="─", width=70): print(char * width)

def get_headers(token):
    return {"Authorization": f"Bearer {token}"}

def get_object_tree_raw(token, urn, guid):
    return aps.get_object_tree(token, urn, guid)

def get_fragment_data(token, urn, guid):
    """Try to get fragment/geometry data which contains transforms."""
    encoded = aps.encode_urn(urn)
    # Try the fragments endpoint
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/metadata/{guid}",
        headers={**get_headers(token), "Accept-Encoding": "gzip"},
        params={"objectid": 1}
    )
    return r.status_code, r.text[:500] if r.status_code != 200 else "ok"

token = get_token()

# ── Test 1: Room boundaries from interior model ────────────────────────────
sep("═")
print("TEST 1 — Room boundary data from INTERIOR model")
sep("═")

views = aps.get_model_views(token, INTERIOR_URN)
guid_int = next((v["guid"] for v in views if v.get("isMasterView")), views[0]["guid"])
print(f"Using view: {guid_int}")

tree = get_object_tree_raw(token, INTERIOR_URN, guid_int)
top = tree.get("data", {}).get("objects", [{}])[0].get("objects", [])

rooms_node = next((n for n in top if n["name"] == "Rooms"), None)
print(f"Rooms category found: {rooms_node is not None}")
if rooms_node:
    room_types = rooms_node.get("objects", [])
    print(f"Room type nodes: {len(room_types)}")
    # Sample first room type
    if room_types:
        sample_type = room_types[0]
        print(f"Sample room type: {sample_type['name']} (objectid: {sample_type['objectid']})")
        instances = sample_type.get("objects", [])
        print(f"  Instances: {len(instances)}")
        if instances:
            print(f"  First instance objectid: {instances[0]['objectid']}")

# Fetch properties for rooms
props = aps.get_properties(token, INTERIOR_URN, guid_int)
collection = props["data"]["collection"]
props_by_id = {o["objectid"]: o for o in collection}

# Find room objects
room_objs = []
if rooms_node:
    for type_node in rooms_node.get("objects", []):
        obj = props_by_id.get(type_node["objectid"])
        if obj:
            room_objs.append(obj)
        for inst in type_node.get("objects", []):
            obj = props_by_id.get(inst["objectid"])
            if obj:
                room_objs.append(obj)

print(f"\nRoom objects with properties: {len(room_objs)}")
if room_objs:
    sample = room_objs[0]
    fp = {}
    for gp in sample.get("properties", {}).values():
        if isinstance(gp, dict):
            fp.update(gp)
    print(f"\nSample room: {sample.get('name','')}")
    print("All properties:")
    for k, v in sorted(fp.items()):
        print(f"  {k}: {v!r}")

# ── Test 2: Furniture XYZ from furniture model ─────────────────────────────
sep("═")
print("\nTEST 2 — Furniture element geometry/position from FURNITURE model")
sep("═")

views_furn = aps.get_model_views(token, FURNITURE_URN)
guid_furn = next((v["guid"] for v in views_furn if v.get("isMasterView")), views_furn[0]["guid"])

tree_furn = get_object_tree_raw(token, FURNITURE_URN, guid_furn)
top_furn = tree_furn.get("data", {}).get("objects", [{}])[0].get("objects", [])
furn_node = next((n for n in top_furn if n["name"] == "Furniture"), None)

print(f"Furniture category found: {furn_node is not None}")

props_furn = aps.get_properties(token, FURNITURE_URN, guid_furn)
coll_furn = props_furn["data"]["collection"]

# Look for any XYZ, transform, location data on furniture elements
print("\nSearching for position/geometry data on furniture elements...")
xyz_keys_found = set()
sample_with_xyz = None

for obj in coll_furn[:200]:
    fp = {}
    for gp in obj.get("properties", {}).values():
        if isinstance(gp, dict):
            fp.update(gp)
    for k in fp:
        if any(x in k.lower() for x in ["x ", "y ", "z ", "coord", "locat", "position", "transform", "point", "offset", " x", " y", " z"]):
            xyz_keys_found.add(k)
            if sample_with_xyz is None:
                sample_with_xyz = (obj.get("name",""), k, fp[k])

print(f"XYZ/position-related param keys found: {sorted(xyz_keys_found)}")
if sample_with_xyz:
    print(f"Sample: {sample_with_xyz}")

# ── Test 3: Check for viewer fragmented geometry endpoint ─────────────────
sep("═")
print("\nTEST 3 — Check APS geometry/SVF2 endpoints for transform data")
sep("═")

encoded_furn = aps.encode_urn(FURNITURE_URN)
manifest_r = requests.get(
    f"{BASE_URL}/modelderivative/v2/designdata/{encoded_furn}/manifest",
    headers=get_headers(token),
)
print(f"Manifest status: {manifest_r.status_code}")
if manifest_r.status_code == 200:
    manifest = manifest_r.json()
    derivatives = manifest.get("derivatives", [])
    for d in derivatives:
        print(f"  Output type: {d.get('outputType')} status: {d.get('status')}")
        for child in d.get("children", [])[:5]:
            print(f"    {child.get('type','?')} {child.get('role','?')} {child.get('mime','')} {child.get('urn','')[:60]}")

sep("═")
print("\nSUMMARY")
sep("═")
print(f"Room objects in interior model:       {len(room_objs)}")
print(f"XYZ params on furniture elements:     {len(xyz_keys_found)}")
has_bbox = any("offset" in k.lower() or "elevation" in k.lower() for k in xyz_keys_found)
print(f"Positional params found:              {has_bbox}")
print()
if not xyz_keys_found:
    print("RESULT: No XYZ/position data in properties collection.")
    print("  The properties API does not expose element coordinates.")
    print("  A geometry spatial join is NOT possible without parsing SVF2 binary files.")
else:
    print("RESULT: Positional data found — spatial join may be feasible.")
sep("═")
