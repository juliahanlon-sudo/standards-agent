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


def fetch_records(region: str) -> list[dict]:
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


def validate_row(sfdc_tag: str, frame_tag: str, records: list[dict]) -> dict:
    if not sfdc_tag and not frame_tag:
        return {"status": "No Tag", "color": "grey"}

    for rec in records:
        fields = rec.get("fields", {})
        new_tag = str(fields.get("NEW TAG NUMBER", "")).strip()
        if new_tag and sfdc_tag and new_tag.lower() == sfdc_tag.strip().lower():
            return {"status": "Validated", "color": "green"}

    if frame_tag:
        for rec in records:
            fields = rec.get("fields", {})
            ft = str(fields.get("Frame Tag", "")).strip()
            if ft and ft.lower() == frame_tag.strip().lower():
                return {"status": "Frame Match - Pending Fabric", "color": "yellow"}

    return {"status": "Not Found", "color": "red"}
