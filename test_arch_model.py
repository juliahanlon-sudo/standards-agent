"""
Diagnostic: checks whether furniture data from a linked file is readable
through an Architecture model via the Model Derivative API.
"""

import re
import sys
import json
from auth import get_token
import aps_client as aps

HUB_ID  = "b.8a643169-4b2b-4c79-bff4-289208a76b2e"
PROJECT_ID = "b.6ecebf3f-9519-4fc9-8e2b-97ae184fce04"  # AMER DEN06 Denver [PARENT]

ARCH_PATTERN  = re.compile(r'(?<![a-z])(ar|arch)(?![a-z])', re.IGNORECASE)
EXCLUDE_PATTERN = re.compile(r'(?<![a-z])(base|ec|existing)(?![a-z])', re.IGNORECASE)
PREFER_PATTERN  = re.compile(r'(?<![a-z])(interior|int)(?![a-z])', re.IGNORECASE)
TARGET_PARAMS = ["SFDC_Tag Number", "SFDC Tag Number", "SFDC_TAG NUMBER",
                 "SFDC_Seat Count", "SFDC Seat Count",
                 "Room Name", "Room Number"]


def flat(obj):
    result = {}
    for gp in obj.get("properties", {}).values():
        if isinstance(gp, dict):
            for k, v in gp.items():
                result[k] = v
    return result


def sep(char="─", width=70):
    print(char * width)


# ── Step 1: Find arch models ───────────────────────────────────────────────
print()
sep("═")
print("STEP 1 — Searching Denver project for AR/ARCH models")
sep("═")

token = get_token()
all_files = aps.find_rvt_files(token, HUB_ID, PROJECT_ID)
arch_files = [f for f in all_files if ARCH_PATTERN.search(f["name"].replace(".rvt", ""))]

if not arch_files:
    print("No AR/ARCH files found. All .rvt files in project:")
    for f in all_files:
        print(f"  {f['name']}")
    sys.exit(1)

print(f"Found {len(arch_files)} arch model(s):")
for i, f in enumerate(arch_files):
    stem = f["name"].replace(".rvt", "")
    excluded = "EXCLUDED (base/ec)" if EXCLUDE_PATTERN.search(stem) else ""
    preferred = "PREFERRED (interior)" if PREFER_PATTERN.search(stem) else ""
    tag = f"  ← {preferred or excluded}" if (preferred or excluded) else ""
    print(f"  [{i}] {f['name']}{tag}")
    print(f"      URN:  {f['urn']}")
    print(f"      Path: {f['path']}")
    print(f"      Modified: {f.get('last_modified','')}")

# Prefer interior models, exclude base/ec/existing
preferred = [f for f in arch_files if PREFER_PATTERN.search(f["name"].replace(".rvt",""))
             and not EXCLUDE_PATTERN.search(f["name"].replace(".rvt",""))]
not_excluded = [f for f in arch_files if not EXCLUDE_PATTERN.search(f["name"].replace(".rvt",""))]
candidates = preferred or not_excluded or arch_files
model = candidates[0]
urn = model["urn"]
print(f"\nUsing: {model['name']}")
print(f"URN:   {urn}")


# ── Step 2: Get views ──────────────────────────────────────────────────────
sep("═")
print("STEP 2 — Available views")
sep("═")

views = aps.get_model_views(token, urn)
for v in views:
    master = " ← MASTER" if v.get("isMasterView") else ""
    print(f"  {v.get('role','?'):5}  {v['guid']}  {v.get('name','')}{master}")

guid = next((v["guid"] for v in views if v.get("isMasterView")), None)
if not guid:
    guid = next((v["guid"] for v in views if v.get("role") == "3d"), views[0]["guid"])
print(f"\nUsing GUID: {guid}")


# ── Step 3: Fetch all properties ───────────────────────────────────────────
sep("═")
print("STEP 3 — Fetching properties (may take a moment)…")
sep("═")

props_data = aps.get_properties(token, urn, guid)
collection = props_data.get("data", {}).get("collection", [])
print(f"Total objects in collection: {len(collection)}")


# ── Step 4: All unique categories ─────────────────────────────────────────
sep("═")
print("STEP 4 — All unique categories")
sep("═")

tree_data = aps.get_object_tree(token, urn, guid)
top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])

print(f"{'Category':<40} {'Types':>6}  {'Instances':>9}")
sep()
for cat in sorted(top_objects, key=lambda n: n["name"]):
    types = cat.get("objects", [])
    instances = sum(len(t.get("objects", [])) for t in types)
    print(f"  {cat['name']:<38} {len(types):>6}  {instances:>9}")


# ── Step 5: Furniture elements detail ─────────────────────────────────────
sep("═")
print("STEP 5 — Furniture / Furniture Systems elements")
sep("═")

# Build type node name set
type_node_name_set = set()
for cat in top_objects:
    if cat["name"] in ("Furniture", "Furniture Systems"):
        for tn in cat.get("objects", []):
            type_node_name_set.add(tn["name"])

print(f"Type nodes in Furniture/Furniture Systems: {len(type_node_name_set)}")

def base_name(name):
    return re.sub(r'\s*\[\d+\]$', '', name).strip()

furn_objs = [o for o in collection if base_name(o.get("name","")) in type_node_name_set]
print(f"Matching objects in collection:            {len(furn_objs)}")

if not furn_objs:
    print("\nNo furniture objects found in the collection.")
    print("This means linked file furniture is NOT visible to the Model Derivative API for this model.")
else:
    print(f"\nPrinting first 5 furniture elements verbosely:\n")
    for obj in furn_objs[:5]:
        fp = flat(obj)
        sep()
        print(f"Name:       {obj.get('name','')}")
        print(f"objectid:   {obj.get('objectid','')}")
        print(f"externalId: {obj.get('externalId','')}")
        print()
        print("TARGET PARAMS:")
        for p in TARGET_PARAMS:
            val = fp.get(p)
            flag = "✓" if val and str(val).strip() else "✗"
            print(f"  [{flag}] {p}: {val!r}")
        print()
        print("ALL PROPERTIES:")
        for k, v in sorted(fp.items()):
            print(f"  {k}: {v!r}")


# ── Step 6: Summary ────────────────────────────────────────────────────────
sep("═")
print("STEP 6 — Summary")
sep("═")

total = len(furn_objs)
has_seat_count = sum(
    1 for o in furn_objs
    if any(flat(o).get(p, "") for p in ["SFDC_Seat Count", "SFDC Seat Count"])
)
has_room_name = sum(
    1 for o in furn_objs
    if flat(o).get("Room Name", "")
)
has_sfdc_tag = sum(
    1 for o in furn_objs
    if any(flat(o).get(p, "") for p in ["SFDC_Tag Number", "SFDC Tag Number", "SFDC_TAG NUMBER"])
)

print(f"  Total furniture elements found:  {total}")
print(f"  Have SFDC_Tag Number:            {has_sfdc_tag}")
print(f"  Have SFDC_Seat Count:            {has_seat_count}")
print(f"  Have Room Name:                  {has_room_name}")
print()
if total == 0:
    print("CONCLUSION: Linked furniture is NOT accessible through the arch model.")
    print("  The Model Derivative API does not expose linked file content.")
    print("  Capacity calculation must use the furniture model directly.")
elif has_room_name > 0:
    print("CONCLUSION: Furniture found AND Room Name is populated.")
    print("  Capacity calculation can work from this arch model.")
elif total > 0 and has_room_name == 0:
    print("CONCLUSION: Furniture found but Room Name is empty.")
    print("  Furniture elements are visible but lack room assignment.")
    print("  Check if Room Name is populated in the furniture model instead.")
sep("═")
