"""
Scheduled report runner.
Generates capacity + furniture schedule CSVs and saves to Google Drive.
"""

import io
import csv
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

import aps_client as aps
import capacity_engine as cap_eng
import spatial_join as sj
from auth import get_token

# ── Persistent config store ───────────────────────────────────────────────
CONFIG_FILE = Path(__file__).parent / "scheduled_reports.json"


def load_configs() -> list[dict]:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return []
    return []


def save_configs(configs: list[dict]):
    CONFIG_FILE.write_text(json.dumps(configs, indent=2))


def get_config(report_id: str) -> Optional[dict]:
    return next((c for c in load_configs() if c["id"] == report_id), None)


# ── CSV generators ────────────────────────────────────────────────────────

def generate_capacity_csv(token: str, furniture_urn: str, interior_urn: str,
                           label: str) -> str:
    """Returns CSV string for capacity report."""
    items = sj.get_room_seats(token, furniture_urn, interior_urn)
    result = cap_eng.calculate_capacity(items)

    output = io.StringIO()
    w = csv.writer(output)

    # Summary section
    w.writerow(["CAPACITY SUMMARY", ""])
    w.writerow(["IW Capacity Seats", result["iw"]])
    w.writerow(["Open Collab Capacity Seats", result["open_collab"]])
    w.writerow(["Amenity Capacity Seats", result["amenity"]])
    w.writerow(["Total", result["total"]])
    if result.get("total_desks"):
        w.writerow(["Total Desks", result["total_desks"]])
    if result.get("total_cubbies"):
        w.writerow(["Total Cubbies", result["total_cubbies"]])
    w.writerow([])

    # Breakdown
    w.writerow(["BREAKDOWN", ""])
    cols = ["Room Name", "Level", "Category", "Raw Seats", "Multiplier", "Weighted Seats", "Matched Architecture Name"]
    w.writerow(cols)
    for row in result.get("breakdown", []):
        w.writerow([row.get(c, "") for c in cols])

    return output.getvalue()


def generate_furniture_csv(token: str, urn: str, at_records: list,
                            hub_id: str) -> str:
    """Returns CSV string for furniture schedule."""
    import main as m
    from collections import defaultdict
    import re as _re

    views = aps.get_model_views(token, urn)
    if not views:
        return "No views found\n"
    guid = m.get_guid(views)
    tree_data = aps.get_object_tree(token, urn, guid)
    top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
    cat_nodes = m.get_tree_nodes_by_category(top_objects, ["Furniture", "Furniture Systems"])
    if not cat_nodes:
        return "No furniture found\n"

    all_type_nodes, all_instance_ids = [], set()
    instance_to_family = {}
    for cat_node in cat_nodes.values():
        for type_node in cat_node.get("objects", []):
            family = type_node.get("name", "")
            instance_to_family[type_node["objectid"]] = family
            for inst in type_node.get("objects", []):
                instance_to_family[inst["objectid"]] = family
                all_instance_ids.add(inst["objectid"])
            tnodes, iids = m.collect_type_and_instance_ids(cat_node)
            all_type_nodes.extend(tnodes)
            all_instance_ids.update(iids)

    props_data = aps.get_properties(token, urn, guid)
    collection = props_data.get("data", {}).get("collection", [])
    type_node_names = {tn["objectid"]: tn["name"] for tn in all_type_nodes}
    type_node_name_set = set(type_node_names.values())
    all_category_ids = all_instance_ids | set(type_node_names.keys())

    def base_name(name):
        return _re.sub(r'\s*\[\d+\]$', '', name).strip()

    groups = defaultdict(lambda: {"total": 0, "levels": defaultdict(int), "param_obj": None, "type_node_name": ""})
    for obj in collection:
        obj_name = obj.get("name", "")
        bn = base_name(obj_name)
        if bn not in type_node_name_set and obj.get("objectid") not in all_category_ids:
            continue
        fp_obj = m.flat_props(obj)
        type_name = fp_obj.get("Type Name", "").strip() or bn
        level = fp_obj.get("Level") or ""
        has_sfdc = bool(fp_obj.get("SFDC_Tag Number") or fp_obj.get("SFDC_TAG NUMBER") or fp_obj.get("SFDC_Seat Count"))
        is_instance = "[" in obj_name or obj.get("objectid") in all_instance_ids
        if not is_instance and not has_sfdc:
            continue
        sfdc_tag_key = fp_obj.get("SFDC_Tag Number", "") or fp_obj.get("SFDC_TAG NUMBER", "") or fp_obj.get("Type Mark", "")
        key = (bn, type_name, sfdc_tag_key)
        groups[key]["total"] += 1
        groups[key]["levels"][level] += 1
        obj_id = obj.get("objectid")
        family_from_tree = instance_to_family.get(obj_id, "")
        if not groups[key]["type_node_name"]:
            groups[key]["type_node_name"] = family_from_tree or bn
        existing = groups[key]["param_obj"]
        new_fp = m.flat_props(obj)
        if existing is None:
            groups[key]["param_obj"] = obj
        else:
            existing_fp = m.flat_props(existing)
            if (new_fp.get("SFDC_Tag Number") or new_fp.get("Type Mark")) and not (existing_fp.get("SFDC_Tag Number") or existing_fp.get("Type Mark")):
                groups[key]["param_obj"] = obj

    output = io.StringIO()
    w = csv.writer(output)
    cols = ["SFDC_Tag Number", "SFDC_Seat Count", "Family", "Type", "Count", "Manufacturer", "Validation Status"]
    w.writerow(cols)

    import airtable_client as at_mod
    rows = []
    for (bn, type_name, _), grp in groups.items():
        param_obj = grp["param_obj"]
        if not param_obj:
            continue
        fp = m.flat_props(param_obj)
        family_name = grp["type_node_name"] or param_obj.get("name", "")
        sfdc_tag = fp.get("SFDC_Tag Number", "") or fp.get("SFDC_TAG NUMBER", "")
        frame_tag = fp.get("Type Mark", "")
        validation = at_mod.validate_row(sfdc_tag, frame_tag, at_records)
        rows.append([
            sfdc_tag,
            fp.get("SFDC_Seat Count", ""),
            family_name,
            fp.get("Type Name", "").strip() or type_name,
            str(grp["total"]),
            fp.get("Manufacturer", ""),
            validation["status"],
        ])

    rows.sort(key=lambda r: (r[0], r[2]))
    for row in rows:
        w.writerow(row)
    return output.getvalue()


# ── Google Drive upload via MCP ───────────────────────────────────────────

def upload_to_drive(filename: str, content: str, folder_id: str = "root") -> str:
    """Upload a CSV to Google Drive. Returns the file link."""
    # This calls the MCP tool — actual invocation happens in main.py endpoint
    # which has access to MCP tools. We return the content for the endpoint to upload.
    return content


# ── Report execution ──────────────────────────────────────────────────────

def run_report(config: dict) -> dict:
    """
    Run a scheduled report. Returns {status, files_created, errors}.
    """
    token = get_token()
    hub_id = config.get("hub_id", "b.8a643169-4b2b-4c79-bff4-289208a76b2e")
    report_types = config.get("report_types", ["capacity", "furniture"])
    folder_id = config.get("drive_folder_id", "root")
    timestamp = datetime.now().strftime("%Y-%m-%d")
    results = {"status": "ok", "files": [], "errors": []}

    import airtable_client as at_mod
    at_records = at_mod.fetch_records("")

    for entry in config.get("models", []):
        label = entry.get("label", "Unknown")
        furniture_urn = entry.get("furniture_urn", "")
        interior_urn = entry.get("interior_urn", "")
        safe_label = re.sub(r'[^\w\-]', '_', label)[:40]

        if "capacity" in report_types and furniture_urn and interior_urn:
            try:
                csv_content = generate_capacity_csv(token, furniture_urn, interior_urn, label)
                results["files"].append({
                    "filename": f"{safe_label}_Capacity_{timestamp}.csv",
                    "content": csv_content,
                    "folder_id": folder_id,
                    "type": "capacity",
                    "label": label,
                })
            except Exception as e:
                results["errors"].append(f"Capacity {label}: {e}")

        if "furniture" in report_types and furniture_urn:
            try:
                csv_content = generate_furniture_csv(token, furniture_urn, at_records, hub_id)
                results["files"].append({
                    "filename": f"{safe_label}_Furniture_{timestamp}.csv",
                    "content": csv_content,
                    "folder_id": folder_id,
                    "type": "furniture",
                    "label": label,
                })
            except Exception as e:
                results["errors"].append(f"Furniture {label}: {e}")

    if results["errors"]:
        results["status"] = "partial" if results["files"] else "error"
    return results


# ── Scheduler ─────────────────────────────────────────────────────────────

_scheduler_thread = None
_scheduler_running = False


def _scheduler_loop():
    global _scheduler_running
    while _scheduler_running:
        try:
            configs = load_configs()
            now = datetime.utcnow()
            for config in configs:
                if not config.get("enabled", True):
                    continue
                next_run = config.get("next_run")
                if not next_run:
                    continue
                next_dt = datetime.fromisoformat(next_run)
                if now >= next_dt:
                    try:
                        run_report(config)
                    except Exception:
                        pass
                    # Schedule next run
                    interval_days = config.get("interval_days", 14)
                    config["next_run"] = (now + timedelta(days=interval_days)).isoformat()
                    config["last_run"] = now.isoformat()
                    configs_updated = load_configs()
                    for i, c in enumerate(configs_updated):
                        if c["id"] == config["id"]:
                            configs_updated[i] = config
                    save_configs(configs_updated)
        except Exception:
            pass
        time.sleep(60)  # check every minute


def start_scheduler():
    global _scheduler_thread, _scheduler_running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    global _scheduler_running
    _scheduler_running = False
