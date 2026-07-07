import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("AIRTABLE_API_KEY")
BASE_ID = "appW5LiBnNMb9Pkid"
TABLE = "Furniture"
BUILDINGS_TABLE = "Buildings"
MANUFACTURERS_TABLE = "Manufacturers or Companies"
BASE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE}"
BUILDINGS_URL = f"https://api.airtable.com/v0/{BASE_ID}/{BUILDINGS_TABLE}"
MANUFACTURERS_URL = f"https://api.airtable.com/v0/{BASE_ID}/{MANUFACTURERS_TABLE}"

REGIONS = ["AMER", "EMEA", "JAPAC", "LATAM", "India"]


def parse_region(project_name: str) -> str:
    first = project_name.strip().split()[0].upper() if project_name.strip() else ""
    for r in REGIONS:
        if first == r.upper():
            return r
    return ""


def fetch_records(region: str = "") -> list[dict]:
    """
    Fetch all records from Airtable (no region filtering).
    Region parameter is kept for backwards compatibility but not used for filtering.
    Region prioritization happens in build_lookup instead.
    """
    if not API_KEY or API_KEY == "your_airtable_api_key_here":
        return []
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {}
    # Don't filter by region - fetch all records and prioritize in build_lookup

    records = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        r = requests.get(BASE_URL, headers=headers, params=params)
        r.raise_for_status()
        body = r.json()
        records.extend(body.get("records", []))
        offset = body.get("offset")
        if not offset:
            break
    return records


STATUS_PRIORITY = {"retired": 0, "inactive": 1, "active": 2, "": 3}

# Manufacturer name to abbreviation mapping (for common cases)
MANUFACTURER_ALIASES = {
    "HERMAN MILLER": "MKL",
    "HERMANMILLER": "MKL",
    "MILLER KNOLL": "MKL",
    "MILLERKNOLL": "MKL",
    "STEELCASE": "SCS",
    "HAWORTH": "HWI",
    "KNOLL": "KNL",
    "COALESSE": "COA",
    "WEST ELM": "WEM",
    "ARPER": "ARP",
    "HUMANSCALE": "HUM",
}

# Abbreviation to full name mapping (for display in tooltips)
# Add more as needed - just the most common ones for now
MANUFACTURER_NAMES = {
    "MKL": "Miller Knoll (Herman Miller)",
    "SCS": "Steelcase",
    "ARP": "Arper",
    "HUM": "Humanscale",
    "COA": "Coalesse",
    "WEM": "West Elm",
    "DPT": "DatesWeiser",
    "WDT": "Woodtrends",
    "MND": "Mondo",
    "BHC": "BuzziSpace",
    "LOL": "Loll Designs",
    "NAT": "National",
    "FMY": "Formway",
    "HBF": "HBF Textiles",
    "KRG": "Krug",
    "MAY": "Mayline",
    "NWD": "Northwood",
    "PFD": "Purposeful Design",
    "ULI": "Uline",
    "VIT": "Vitra",
    "HAY": "Hay",
    "IKE": "IKEA",
}


def build_lookup(records: list[dict], building_id: str = None, region_buildings: list[str] = None, region: str = None, key_field: str = "Frame Tag") -> dict:
    """
    Build a lookup dict: key (lowercase) → {status, new_tag, frame_tag, manufacturer, buildings, region}
    The lookup key comes from `key_field` (default "Frame Tag"). Pass "Tag - Color Only"
    to match model tags that carry a fabric letter but no fabric code (e.g. SS-238Z),
    or "NEW TAG NUMBER" to match the fully-specified tag (e.g. SS-238Z.03).
    When duplicates exist:
    - If building_id provided, prefer records where that building is in the Buildings list
    - If no building match, prefer records where region matches
    - If no region match, use any available record (cross-region fallback)
    - Otherwise, keep the most conservative status (Retired > Active).
    """
    lookup = {}
    for rec in records:
        fields = rec.get("fields", {})
        ft = str(fields.get(key_field, "")).strip().lower()
        new_tag = str(fields.get("NEW TAG NUMBER", "")).strip()
        status = str(fields.get("Status", "")).strip()
        # Manufacturer Abbreviation is a list (from lookup field), take first entry
        mfr_abbrev = fields.get("Manufacturer Abbreviation (from Manufacturers)", [])
        manufacturer = mfr_abbrev[0] if mfr_abbrev else ""
        # Buildings is a list of linked record IDs
        buildings = fields.get("Buildings", [])
        # Region field
        record_region = str(fields.get("Region", "")).strip()
        if not ft:
            continue
        entry = {
            "frame_tag": fields.get("Frame Tag", ""),
            "new_tag": new_tag,
            "status": status,
            "manufacturer": manufacturer,
            "buildings": buildings,  # List of building record IDs
            "region": record_region,
        }
        if ft not in lookup:
            lookup[ft] = entry
        else:
            # Priority: exact building > same region > different region > status
            existing_has_building = building_id and building_id in lookup[ft]["buildings"]
            new_has_building = building_id and building_id in buildings

            # Check region match (both by buildings list and Region field)
            existing_has_region_building = region_buildings and any(b in lookup[ft]["buildings"] for b in region_buildings)
            new_has_region_building = region_buildings and any(b in buildings for b in region_buildings)

            existing_region_match = region and lookup[ft].get("region", "").upper() == region.upper()
            new_region_match = region and record_region.upper() == region.upper()

            # Exact building match wins
            if new_has_building and not existing_has_building:
                lookup[ft] = entry
                continue
            elif existing_has_building and not new_has_building:
                continue

            # If both or neither have exact building, check region
            # Prefer records with buildings in same region OR matching Region field
            existing_in_region = existing_has_region_building or existing_region_match
            new_in_region = new_has_region_building or new_region_match

            if new_in_region and not existing_in_region:
                lookup[ft] = entry
                continue
            elif existing_in_region and not new_in_region:
                continue

            # Keep the more conservative status
            existing_priority = STATUS_PRIORITY.get(lookup[ft]["status"].lower(), 3)
            new_priority = STATUS_PRIORITY.get(status.lower(), 3)
            if new_priority < existing_priority:
                lookup[ft] = entry
    return lookup


def get_building_id_by_code(building_code: str, building_records: list[dict]) -> str:
    """Get Airtable record ID for a building by its code."""
    for rec in building_records:
        fields = rec.get("fields", {})
        at_building_code = str(fields.get("Building Code (SV)", "")).strip().upper()
        if at_building_code == building_code:
            return rec.get("id", "")
    return ""


def validate_row(sfdc_tag: str, frame_tag: str, records: list[dict], manufacturer: str = "",
                  building_code: str = "", building_records: list[dict] = None,
                  manufacturer_mapping: dict = None) -> dict:
    """
    Validate a furniture row against Airtable:
    - Match by Frame Tag field in Airtable
    - Check if current building is in the item's Buildings list
    - If found and Status = Active  → green  "Active"
    - If found and Status = Retired → red    "Retired"
    - If found and Status = other   → yellow "Inactive"
    - If not found at all           → yellow "Not in Airtable"
    - If no tag to look up          → grey   "No Tag"

    Returns dict with status, color, airtable_manufacturer, manufacturer_match, and building_match.
    """
    # Use Frame Tag as primary lookup key; fall back to SFDC tag
    lookup_key = (frame_tag or sfdc_tag or "").strip().lower()

    # Debug for EQ-15
    if "eq-15" in lookup_key.lower():
        print(f"[Airtable] EQ-15 lookup - frame_tag: '{frame_tag}', sfdc_tag: '{sfdc_tag}', lookup_key: '{lookup_key}'")

    if not lookup_key:
        return {
            "status": "No Tag",
            "color": "grey",
            "airtable_manufacturer": "",
            "airtable_manufacturer_abbrev": "",
            "manufacturer_match": None,
            "building_match": None,
        }

    # Build lookup from records - pass building_id to prioritize building-specific records
    building_id = None
    region_buildings = []
    region = ""
    if building_code and building_records:
        building_id = get_building_id_by_code(building_code, building_records)

        # Get region from the building record (not from building code)
        if building_id:
            building_record = next((b for b in building_records if b.get("id") == building_id), None)
            if building_record:
                region = str(building_record.get("fields", {}).get("Region", "")).strip()

        # Get all building IDs in the same region
        if region:
            region_buildings = [
                b.get("id") for b in building_records
                if str(b.get("fields", {}).get("Region", "")).strip().upper() == region.upper()
            ]

    lkp = build_lookup(records, building_id, region_buildings, region)
    match = lkp.get(lookup_key)

    # Debug for EQ-15
    if "eq-15" in lookup_key.lower():
        print(f"[Airtable] EQ-15 lookup result - match found: {match is not None}")
        if not match:
            # Show what keys ARE in the lookup
            eq_keys = [k for k in lkp.keys() if 'eq' in k.lower()]
            print(f"[Airtable] EQ keys in lookup: {eq_keys[:10]}")

    if not match:
        # Also try SFDC tag if frame_tag didn't match
        if sfdc_tag and sfdc_tag.strip().lower() != lookup_key:
            match = lkp.get(sfdc_tag.strip().lower())

    # Fabric-code fallback: the model tag carries a fabric letter but no fabric
    # code (e.g. model "SS-238Z" vs Airtable NEW TAG "SS-238Z.03"). Airtable's
    # "Tag - Color Only" field holds the frame+fabric-letter (e.g. "SS-238Z"),
    # so match against that. When we match this way, the fabric code still needs
    # to be assigned in the model.
    needs_fabric_code = False
    if not match:
        # First try the fully-specified tag (frame+fabric+code, e.g. SS-238Z.03).
        # A model carrying the full code matches cleanly with no note.
        newtag_lkp = build_lookup(records, building_id, region_buildings, region, key_field="NEW TAG NUMBER")
        match = newtag_lkp.get(lookup_key)
        if not match and sfdc_tag and sfdc_tag.strip().lower() != lookup_key:
            match = newtag_lkp.get(sfdc_tag.strip().lower())
    if not match:
        # Then fall back to frame+fabric-letter without a code (e.g. model
        # SS-238Z matches Airtable "Tag - Color Only"). Fabric code still needed.
        color_lkp = build_lookup(records, building_id, region_buildings, region, key_field="Tag - Color Only")
        match = color_lkp.get(lookup_key)
        if not match and sfdc_tag and sfdc_tag.strip().lower() != lookup_key:
            match = color_lkp.get(sfdc_tag.strip().lower())
        if match:
            needs_fabric_code = True

    if match:
        status = match["status"]
        airtable_mfr = match.get("manufacturer", "")
        airtable_buildings = match.get("buildings", [])
        airtable_region = match.get("region", "")

        # Check if this is a cross-region match (item from different region being used as fallback)
        is_cross_region = False
        if region and airtable_region and airtable_region.upper() != region.upper():
            is_cross_region = True
            print(f"[Airtable] Cross-region match for {frame_tag or sfdc_tag}: project region={region}, item region={airtable_region}")

        # Debug logging for BE-01
        if (frame_tag or sfdc_tag or "").upper() == "BE-01":
            print(f"[Airtable] BE-01 debug - Revit manufacturer: '{manufacturer}', Airtable manufacturer abbrev: '{airtable_mfr}', status: '{status}'")

        # Check manufacturer match (case-insensitive)
        # Handle manufacturer name variations and abbreviations
        mfr_match = None
        if airtable_mfr:  # Airtable has a manufacturer
            if not manufacturer or not manufacturer.strip():
                # Revit is blank but Airtable has a value → mismatch
                mfr_match = False
                print(f"[Airtable] Manufacturer mismatch for {frame_tag or sfdc_tag}: Revit=blank, Airtable={airtable_mfr}")
            else:
                mfr_revit = manufacturer.strip().upper()
                mfr_at = airtable_mfr.strip().upper()

                # Direct match
                if mfr_revit == mfr_at:
                    mfr_match = True
                # Check if Revit has a full name that maps to the Airtable abbreviation
                elif mfr_revit in MANUFACTURER_ALIASES and MANUFACTURER_ALIASES[mfr_revit] == mfr_at:
                    mfr_match = True
                # Check if abbreviation is contained in the full name
                elif mfr_at in mfr_revit.split():
                    mfr_match = True
                else:
                    mfr_match = False

        # Check if current building is in the item's approved buildings list
        building_match = None
        if building_code and building_records:
            building_id = get_building_id_by_code(building_code, building_records)
            if building_id:
                # Check if this building ID is in the furniture item's Buildings list
                building_match = building_id in airtable_buildings
                print(f"[Airtable] Building check for {frame_tag}: code={building_code}, id={building_id}, in_list={building_match}, buildings_list={airtable_buildings[:3] if len(airtable_buildings) > 3 else airtable_buildings}")

        # Get full manufacturer name for display (use real mapping from Airtable, or fallback to hardcoded, or abbreviation)
        if manufacturer_mapping:
            mfr_display_name = manufacturer_mapping.get(airtable_mfr, MANUFACTURER_NAMES.get(airtable_mfr, airtable_mfr))
        else:
            mfr_display_name = MANUFACTURER_NAMES.get(airtable_mfr, airtable_mfr)

        fabric_note = "Need to assign fabric code" if needs_fabric_code else ""

        if status.lower() == "active":
            result = {
                "status": "Active",
                "color": "green",
                "airtable_manufacturer": mfr_display_name,
                "airtable_manufacturer_abbrev": airtable_mfr,
                "manufacturer_match": mfr_match,
                "building_match": building_match,
                "airtable_region": airtable_region,
                "is_cross_region": is_cross_region,
                "needs_fabric_code": needs_fabric_code,
                "note": fabric_note,
            }
            # Debug for BE-01
            if (frame_tag or sfdc_tag or "").upper() == "BE-01":
                print(f"[Airtable] BE-01 returning: mfr_display_name='{mfr_display_name}', mfr_match={mfr_match}")
            return result
        elif status.lower() == "retired":
            result = {
                "status": "Retired",
                "color": "red",
                "airtable_manufacturer": mfr_display_name,
                "airtable_manufacturer_abbrev": airtable_mfr,
                "manufacturer_match": mfr_match,
                "building_match": building_match,
                "airtable_region": airtable_region,
                "is_cross_region": is_cross_region,
                "needs_fabric_code": needs_fabric_code,
                "note": fabric_note,
            }
            # Debug for BE-01
            if (frame_tag or sfdc_tag or "").upper() == "BE-01":
                print(f"[Airtable] BE-01 returning (Retired): mfr_display_name='{mfr_display_name}', mfr_match={mfr_match}")
            return result
        else:
            result = {
                "status": status or "Inactive",
                "color": "yellow",
                "airtable_manufacturer": mfr_display_name,
                "airtable_manufacturer_abbrev": airtable_mfr,
                "manufacturer_match": mfr_match,
                "building_match": building_match,
                "airtable_region": airtable_region,
                "is_cross_region": is_cross_region,
                "needs_fabric_code": needs_fabric_code,
                "note": fabric_note,
            }
            # Debug for BE-01
            if (frame_tag or sfdc_tag or "").upper() == "BE-01":
                print(f"[Airtable] BE-01 returning (Inactive): mfr_display_name='{mfr_display_name}', mfr_match={mfr_match}")
            return result

    return {
        "status": "Not in Airtable",
        "color": "yellow",
        "airtable_manufacturer": "",
        "airtable_manufacturer_abbrev": "",
        "airtable_region": "",
        "is_cross_region": False,
        "manufacturer_match": None,
        "building_match": None,
        "needs_fabric_code": False,
        "note": "",
    }


def fetch_buildings(region: str = "") -> list[dict]:
    """Fetch building records from Airtable Buildings table.

    Region parameter is kept for backwards compatibility but NOT used to filter.
    Building codes (e.g. MEX05) are globally unique, so filtering by the region
    parsed from the Forma project name breaks matches whenever that label
    disagrees with Airtable's Region (e.g. Forma "AMER MEX05" vs Airtable LATAM).
    Region prioritization happens in build_lookup instead, same as fetch_records.
    """
    if not API_KEY or API_KEY == "your_airtable_api_key_here":
        return []
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {}

    records = []
    offset = None
    try:
        while True:
            if offset:
                params["offset"] = offset
            r = requests.get(BUILDINGS_URL, headers=headers, params=params)
            r.raise_for_status()
            body = r.json()
            records.extend(body.get("records", []))
            offset = body.get("offset")
            if not offset:
                break
    except Exception as e:
        # If Buildings table doesn't exist, return empty list
        print(f"[Airtable] Could not fetch buildings: {e}")
        return []
    return records


def parse_building_code(project_name: str) -> str:
    """
    Extract building code from project name.
    Building codes are typically 3-5 letters + 2 digits (e.g., SFO01, NYC02, LON01).
    """
    import re
    # Match pattern: 2-5 uppercase letters followed by 2 digits
    match = re.search(r'\b([A-Z]{2,5}\d{2})\b', project_name.upper())
    if match:
        return match.group(1)
    return ""


def fetch_manufacturers() -> dict:
    """
    Fetch all manufacturers and build abbreviation -> name mapping.
    Returns dict mapping abbreviation to full name.
    """
    if not API_KEY or API_KEY == "your_airtable_api_key_here":
        return {}

    headers = {"Authorization": f"Bearer {API_KEY}"}
    mapping = {}
    offset = None

    try:
        while True:
            params = {}
            if offset:
                params["offset"] = offset
            r = requests.get(MANUFACTURERS_URL, headers=headers, params=params)
            r.raise_for_status()
            body = r.json()

            for rec in body.get("records", []):
                fields = rec.get("fields", {})
                abbrev = str(fields.get("Manufacturer Abbreviation", "")).strip()
                name = str(fields.get("Name", "")).strip()
                if abbrev and name:
                    mapping[abbrev] = name

            offset = body.get("offset")
            if not offset:
                break
    except Exception as e:
        print(f"[Airtable] Could not fetch manufacturers: {e}")
        return {}

    return mapping


def validate_building(project_name: str, records: list[dict]) -> dict:
    """
    Check if the building code from project name exists in Airtable Buildings table.
    Returns dict with building_found flag, building_code, and matched_building name.
    """
    building_code = parse_building_code(project_name)

    if not building_code:
        return {"building_found": None, "building_code": "", "matched_building": ""}

    if not records:
        return {"building_found": False, "building_code": building_code, "matched_building": ""}

    # Look for matching building code in Airtable
    for rec in records:
        fields = rec.get("fields", {})
        # Check Building Code field - the actual field name is "Building Code (SV)"
        at_building_code = str(fields.get("Building Code (SV)", "")).strip().upper()

        if at_building_code == building_code:
            # Get the building name from the location fields
            building_name = (
                fields.get("Building Location and City", "") or
                fields.get("Location Name", "") or
                fields.get("GCal Name", "") or
                building_code
            )
            return {
                "building_found": True,
                "building_code": building_code,
                "matched_building": str(building_name).strip(),
            }

    return {"building_found": False, "building_code": building_code, "matched_building": ""}
