"""
Diagnostic: downloads SVF derivative files to extract furniture XYZ positions
and room boundary polygons for a spatial join approach.
"""

import re
import json
import gzip
import struct
import requests
from auth import get_token
import aps_client as aps

INTERIOR_URN  = "urn:adsk.wipprod:fs.file:vf.bRQE7yHISlqslJviro8Udg?version=15"
FURNITURE_URN = "urn:adsk.wipprod:fs.file:vf.q6MJLx9YTZ-81tI3rbcbTg?version=11"
BASE_URL = "https://developer.api.autodesk.com"

def sep(char="─", width=70): print(char * width)

def headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_manifest(token, urn):
    encoded = aps.encode_urn(urn)
    r = requests.get(f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/manifest", headers=headers(token))
    r.raise_for_status()
    return r.json()


def list_derivative_files(manifest, output_type="svf"):
    """Recursively collect all files from a manifest derivative."""
    files = []
    def walk(node):
        if isinstance(node, dict):
            if "urn" in node and "type" in node:
                files.append(node)
            for child in node.get("children", []):
                walk(child)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    for d in manifest.get("derivatives", []):
        if d.get("outputType") == output_type:
            walk(d)
    return files


def download_derivative(token, model_urn, file_urn):
    encoded_model = aps.encode_urn(model_urn)
    encoded_file  = requests.utils.quote(file_urn, safe="")
    url = f"{BASE_URL}/modelderivative/v2/designdata/{encoded_model}/manifest/{encoded_file}"
    r = requests.get(url, headers=headers(token))
    return r.status_code, r.content


token = get_token()


# ── Test 1: List all SVF files in both models ──────────────────────────────
sep("═")
print("TEST 1 — SVF derivative file listing")
sep("═")

for label, urn in [("INTERIOR", INTERIOR_URN), ("FURNITURE", FURNITURE_URN)]:
    manifest = get_manifest(token, urn)
    files = list_derivative_files(manifest)
    print(f"\n{label} model ({len(files)} files):")
    for f in files:
        urn_short = f.get("urn","")[-80:]
        print(f"  type={f.get('type','?'):12} role={f.get('role','?'):20} mime={f.get('mime','?'):30}")
        print(f"    urn=...{urn_short}")


# ── Test 2: Download AEC ModelData from interior model ─────────────────────
sep("═")
print("\nTEST 2 — AEC ModelData from INTERIOR model (room boundaries)")
sep("═")

manifest_int = get_manifest(token, INTERIOR_URN)
files_int = list_derivative_files(manifest_int)

aec_file = next((f for f in files_int if "AEC.ModelData" in f.get("mime","") or "AEC.ModelData" in f.get("urn","")), None)
if not aec_file:
    # Try by role
    aec_file = next((f for f in files_int if f.get("role","") == "Autodesk.AEC.ModelData"), None)

print(f"AEC ModelData file found: {aec_file is not None}")
if aec_file:
    print(f"  URN: {aec_file.get('urn','')[-80:]}")
    status, content = download_derivative(token, INTERIOR_URN, aec_file["urn"])
    print(f"  Download status: {status}")
    if status == 200:
        try:
            data = json.loads(content)
            print(f"  Top-level keys: {list(data.keys())}")
            # Look for rooms
            rooms = data.get("rooms", data.get("Rooms", []))
            print(f"  Rooms found: {len(rooms)}")
            if rooms:
                r = rooms[0]
                print(f"  Sample room keys: {list(r.keys()) if isinstance(r, dict) else type(r)}")
                print(f"  Sample room: {json.dumps(r, indent=2)[:500]}")
        except Exception as e:
            print(f"  Not JSON: {e}")
            print(f"  First 200 bytes: {content[:200]}")
else:
    print("  Trying to find by searching all files...")
    for f in files_int:
        print(f"    {f.get('type','')} | {f.get('role','')} | {f.get('mime','')} | urn ends: ...{f.get('urn','')[-40:]}")


# ── Test 3: Download fragments from furniture model ────────────────────────
sep("═")
print("\nTEST 3 — Fragment transforms from FURNITURE model (XYZ positions)")
sep("═")

manifest_furn = get_manifest(token, FURNITURE_URN)
files_furn = list_derivative_files(manifest_furn)

# Look for fragments file
frag_file = next((f for f in files_furn if "fragments" in f.get("urn","").lower()), None)
print(f"Fragments file found: {frag_file is not None}")
if frag_file:
    print(f"  URN: ...{frag_file.get('urn','')[-80:]}")
    status, content = download_derivative(token, FURNITURE_URN, frag_file["urn"])
    print(f"  Download status: {status}, size: {len(content)} bytes")
    if status == 200:
        # Try to decompress if gzipped
        try:
            if content[:2] == b'\x1f\x8b':
                content = gzip.decompress(content)
                print(f"  Decompressed size: {len(content)} bytes")
            # Try JSON
            try:
                data = json.loads(content)
                print(f"  JSON keys: {list(data.keys())[:10]}")
                transforms = data.get("transforms", data.get("fragments", []))
                print(f"  Transforms/fragments: {len(transforms)}")
                if transforms:
                    print(f"  Sample: {json.dumps(transforms[0], indent=2)[:300]}")
            except:
                print(f"  Binary format, first 64 bytes (hex): {content[:64].hex()}")
        except Exception as e:
            print(f"  Error: {e}")
else:
    print("  Available files:")
    for f in files_furn[:20]:
        print(f"    {f.get('type','')} | {f.get('role','')} | ...{f.get('urn','')[-60:]}")


sep("═")
print("\nSUMMARY")
sep("═")
print("Check above output to determine:")
print("  1. Whether AEC ModelData contains room boundary polygons")
print("  2. Whether fragment transforms give usable XYZ positions")
print("  3. Format of the data (JSON vs binary SVF)")
sep("═")
