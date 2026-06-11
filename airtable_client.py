import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("AIRTABLE_API_KEY")
BASE_ID = "appW5LiBnNMb9Pkid"
TABLE = "Furniture"
BASE_URL = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE}"

REGIONS = ["AMER", "EMEA", "JAPAC", "LATAM", "India"]


def parse_region(project_name: str) -> str:
    first = project_name.strip().split()[0].upper() if project_name.strip() else ""
    for r in REGIONS:
        if first == r.upper():
            return r
    return ""


def fetch_records(region: str = "") -> list[dict]:
    if not API_KEY or API_KEY == "your_airtable_api_key_here":
        return []
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params = {}
    if region:
        params["filterByFormula"] = f"{{Region}}='{region}'"

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


def build_lookup(records: list[dict]) -> dict:
    """
    Build a lookup dict: frame_tag (lowercase) → {status, new_tag, frame_tag}
    When duplicates exist, keep the most conservative status (Retired > Active).
    """
    lookup = {}
    for rec in records:
        fields = rec.get("fields", {})
        ft = str(fields.get("Frame Tag", "")).strip().lower()
        new_tag = str(fields.get("NEW TAG NUMBER", "")).strip()
        status = str(fields.get("Status", "")).strip()
        if not ft:
            continue
        entry = {"frame_tag": fields.get("Frame Tag", ""), "new_tag": new_tag, "status": status}
        if ft not in lookup:
            lookup[ft] = entry
        else:
            # Keep the more conservative status
            existing_priority = STATUS_PRIORITY.get(lookup[ft]["status"].lower(), 3)
            new_priority = STATUS_PRIORITY.get(status.lower(), 3)
            if new_priority < existing_priority:
                lookup[ft] = entry
    return lookup


def validate_row(sfdc_tag: str, frame_tag: str, records: list[dict]) -> dict:
    """
    Validate a furniture row against Airtable:
    - Match by Frame Tag field in Airtable
    - If found and Status = Active  → green  "Active"
    - If found and Status = Retired → red    "Retired"
    - If found and Status = other   → yellow "Inactive"
    - If not found at all           → yellow "Not in Airtable"
    - If no tag to look up          → grey   "No Tag"
    """
    # Use Frame Tag as primary lookup key; fall back to SFDC tag
    lookup_key = (frame_tag or sfdc_tag or "").strip().lower()

    if not lookup_key:
        return {"status": "No Tag", "color": "grey"}

    # Build lookup from records
    lkp = build_lookup(records)
    match = lkp.get(lookup_key)

    if not match:
        # Also try SFDC tag if frame_tag didn't match
        if sfdc_tag and sfdc_tag.strip().lower() != lookup_key:
            match = lkp.get(sfdc_tag.strip().lower())

    if match:
        status = match["status"]
        if status.lower() == "active":
            return {"status": "Active", "color": "green"}
        elif status.lower() == "retired":
            return {"status": "Retired", "color": "red"}
        else:
            return {"status": status or "Inactive", "color": "yellow"}

    return {"status": "Not in Airtable", "color": "yellow"}
