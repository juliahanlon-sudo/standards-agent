"""
Test the SVF PackFile parser to verify furniture XYZ positions are extractable.
"""
import os
from auth import get_token
import svf_parser as svf

FURNITURE_URN = "urn:adsk.wipprod:fs.file:vf.q6MJLx9YTZ-81tI3rbcbTg?version=12"
INTERIOR_URN  = "urn:adsk.wipprod:fs.file:vf.bRQE7yHISlqslJviro8Udg?version=16"

def sep(c="─", w=70): print(c*w)

token = get_token()

# ── Step 1: Parse fragments from furniture model ───────────────────────────
sep("═")
print("STEP 1 — Parse FragmentList from furniture model")
sep("═")

svf_base, frag_urn = svf.get_svf_base_and_fragment_urn(token, FURNITURE_URN, "Section View 05")
print(f"SVF base: {svf_base}")
print(f"FragmentList URN: {frag_urn}")

if frag_urn:
    frag_data = svf._download(token, FURNITURE_URN, frag_urn)
    print(f"Downloaded: {len(frag_data)} bytes")

    fragments = list(svf.parse_fragments(frag_data))
    print(f"Total fragments: {len(fragments)}")

    # Show first 5 with valid translations
    valid = [f for f in fragments if f.transform and f.dbid > 0]
    print(f"Fragments with transform: {len(valid)}")
    print("\nSample fragments:")
    for f in valid[:10]:
        tx, ty, tz = svf.get_translation(f.transform)
        print(f"  dbid={f.dbid} pos=({tx:.2f}, {ty:.2f}, {tz:.2f}) bbox={[round(b,2) for b in f.bbox[:4]]}")
else:
    print("FragmentList not found — trying all SVF files")
    import json, zipfile, io, requests
    manifest = svf.get_manifest(token, FURNITURE_URN)
    nodes = svf.find_all_nodes(manifest)
    svf_nodes = [n for n in nodes if n.get('mime') == 'application/autodesk-svf']
    for sn in svf_nodes:
        print(f"  SVF: {sn['urn'].split('/')[-1]}")

# ── Step 2: Download furniture SDB and get entity→dbid mapping ────────────
sep("═")
print("\nSTEP 2 — Furniture SDB: entity IDs and seat counts")
sep("═")

furn_sdb = svf.download_sdb(token, FURNITURE_URN)
print(f"SDB downloaded to {furn_sdb}")

seat_attr = svf.get_attr_id(furn_sdb, 'SFDC_Seat Count')
tag_attr  = svf.get_attr_id(furn_sdb, 'SFDC_Tag Number')
name_attr = svf.get_attr_id(furn_sdb, 'name')
iof_attr  = svf.get_attr_id(furn_sdb, 'instanceof_objid')

print(f"seat_attr={seat_attr}, tag_attr={tag_attr}, name_attr={name_attr}, instanceof_attr={iof_attr}")

# Get all entities with seat count > 0
seat_vals = svf.get_all_values(furn_sdb, seat_attr) if seat_attr else {}
tag_vals  = svf.get_all_values(furn_sdb, tag_attr)  if tag_attr  else {}
name_vals = svf.get_all_values(furn_sdb, name_attr) if name_attr else {}
iof_vals  = svf.get_all_values(furn_sdb, iof_attr)  if iof_attr  else {}

# Build instance→type map
instance_to_type = {int(inst): int(type_id) for inst, type_id in iof_vals.items()}
type_to_seats = {e: int(float(v)) for e, v in seat_vals.items() if float(v) > 0}
type_to_tag   = {e: v for e, v in tag_vals.items() if v}

print(f"Types with seat count > 0: {len(type_to_seats)}")
print(f"Total instance→type mappings: {len(instance_to_type)}")

# Map: instance entity_id → seat count (via type)
instance_seats = {}
for inst_id, type_id in instance_to_type.items():
    if type_id in type_to_seats:
        instance_seats[inst_id] = type_to_seats[type_id]

print(f"Instance entities with seat count: {len(instance_seats)}")

# ── Step 3: Match fragments (dbid) to entity IDs ──────────────────────────
sep("═")
print("\nSTEP 3 — Match fragment dbids to SDB entity IDs")
sep("═")

# In LMV, the fragment dbid == entity_id in the SDB
if frag_urn and fragments:
    matched = 0
    for f in fragments:
        if f.dbid in instance_seats:
            matched += 1

    print(f"Fragments with matching seat count: {matched}")
    print("\nSample matched fragments:")
    count = 0
    for f in fragments:
        if f.dbid in instance_seats:
            tx, ty, tz = svf.get_translation(f.transform)
            type_id = instance_to_type.get(f.dbid)
            tag = type_to_tag.get(type_id, '')
            print(f"  dbid={f.dbid} tag={tag!r} seats={instance_seats[f.dbid]} pos=({tx:.2f},{ty:.2f},{tz:.2f})")
            count += 1
            if count >= 10:
                break

os.unlink(furn_sdb)

sep("═")
print("\nSUMMARY")
sep("═")
if frag_urn and fragments:
    print(f"✓ FragmentList parsed: {len(fragments)} fragments")
    print(f"✓ Instance seats mapped: {len(instance_seats)}")
    print(f"✓ Fragments with position+seats: {matched}")
    if matched > 0:
        print("\nCONCLUSION: Spatial join is FEASIBLE.")
        print("  We have furniture XYZ positions and seat counts.")
        print("  Next step: get room boundaries from interior model to assign rooms.")
    else:
        print("\nCONCLUSION: dbid mapping needs investigation.")
else:
    print("FragmentList not found or empty.")
sep("═")
