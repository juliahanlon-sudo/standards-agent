from collections import defaultdict
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
import requests

from auth import get_token
import aps_client as aps
import airtable_client as at
import capacity_engine as cap_eng
import spatial_join as sj

app = FastAPI()

HUB_ID = "b.8a643169-4b2b-4c79-bff4-289208a76b2e"

FURNITURE_KEYWORDS = {"furn", "furniture", "fn", "sym", "symb", "ff&e"}
ARCH_KEYWORDS = {" ar ", "_ar_", "-ar-", " a ", "_a_", "-a-", " ia ", "_ia_", "-ia-", "arch"}
ARCH_MODEL_KEYWORDS = {"ar", "arch", "ia", "int"}

SCHEDULE_CATEGORIES = {
    "furniture":  ["Furniture", "Furniture Systems"],
    "rooms":      ["Rooms"],
    "floors":     ["Floors"],
    "casework":   ["Casework"],
    "finishes":   ["Rooms"],
}

PRESET_COLUMNS = {
    "furniture": ["SFDC_Tag Number", "Frame Tag", "SFDC_Seat Count", "Family", "Type", "Count", "Manufacturer"],
    "rooms":     ["Number", "Name", "Area", "Level", "Occupancy"],
    "floors":    ["Type", "Type Mark", "Level", "Area"],
    "casework":  ["Family & Type", "Count", "Manufacturer", "Finish 1"],
    "finishes":  ["Number", "Name", "Floor Finish", "Wall Finish", "Base Finish", "Ceiling Finish"],
}


def is_relevant_model(name: str) -> bool:
    import re
    lower = name.lower()
    stem = lower.replace(".rvt", "")
    if any(kw in lower for kw in FURNITURE_KEYWORDS):
        return True
    if any(kw in f" {stem} " for kw in ARCH_KEYWORDS):
        return True
    if re.search(r'(?<![a-z])(ar|arch|ia)(?![a-z])', stem):
        return True
    return False


def is_arch_model(name: str) -> bool:
    import re
    stem = name.lower().replace(".rvt", "")
    if re.search(r'(?<![a-z])(base|ec|existing)(?![a-z])', stem):
        return False
    return bool(re.search(r'(?<![a-z])(ar|arch|ia|int|interior)(?![a-z])', stem))


def deduplicate_by_name(files: list) -> list:
    """Keep only the most recently modified file for each unique filename."""
    by_name = {}
    for f in files:
        name = f.get("name", "")
        if not name:
            continue
        last_modified = f.get("last_modified", "")
        if name not in by_name or last_modified > by_name[name].get("last_modified", ""):
            by_name[name] = f
    return list(by_name.values())


def get_guid(views: list, prefer_name_hint: str = "") -> str:
    # If a name hint is given, prefer views matching that hint
    if prefer_name_hint:
        hint_lower = prefer_name_hint.lower()
        for v in views:
            if v.get("role") == "3d" and hint_lower in v.get("name", "").lower():
                return v["guid"]
    for v in views:
        if v.get("isMasterView"):
            return v["guid"]
    for v in views:
        if v.get("role") == "3d":
            return v["guid"]
    return views[0]["guid"] if views else None


def get_best_guid_for_schedule(token, urn: str, views: list, target_categories: list) -> str:
    """Find the 3D view that has the most instances for the target categories."""
    import aps_client as aps_mod
    best_guid = get_guid(views)
    best_count = 0

    for v in views:
        if v.get("role") != "3d":
            continue
        try:
            tree = aps_mod.get_object_tree(token, urn, v["guid"])
            top = tree.get("data", {}).get("objects", [{}])[0].get("objects", [])
            count = sum(
                sum(len(t.get("objects", [])) for t in cat.get("objects", []))
                for cat in top if cat["name"] in target_categories
            )
            if count > best_count:
                best_count = count
                best_guid = v["guid"]
        except Exception:
            continue

    return best_guid


def flat_props(obj: dict) -> dict:
    result = {}
    for gp in obj.get("properties", {}).values():
        if isinstance(gp, dict):
            for k, v in gp.items():
                result[k] = str(v) if v is not None else ""
    return result


def parse_family_name(fp: dict) -> str:
    # Workset format: "Family  : Category : FamilyName"
    workset = fp.get("Workset", "")
    parts = [p.strip() for p in workset.split(":")]
    if len(parts) >= 3:
        return parts[-1]
    return ""


def parse_type_name(type_node_name: str, family_name: str) -> str:
    # If the type node name starts with the family name, extract the suffix as type
    if family_name and type_node_name.startswith(family_name):
        suffix = type_node_name[len(family_name):].strip(" -:")
        return suffix if suffix else type_node_name
    return type_node_name


def get_tree_nodes_by_category(tree_top: list, categories: list[str]) -> dict:
    result = {}
    for node in tree_top:
        if node["name"] in categories:
            result[node["name"]] = node
    return result


def collect_type_and_instance_ids(cat_node: dict) -> tuple[list, set]:
    type_nodes = []
    instance_ids = set()
    for child in cat_node.get("objects", []):
        grandchildren = child.get("objects", [])
        if grandchildren:
            # Normal structure: child is a type node, grandchildren are instances
            type_nodes.append({
                "objectid": child["objectid"],
                "name": child.get("name", ""),
                "instance_count": len(grandchildren),
                "instance_ids": [i["objectid"] for i in grandchildren],
            })
            for inst in grandchildren:
                instance_ids.add(inst["objectid"])
        else:
            # Flat structure (e.g. Rooms): child is directly an instance
            type_nodes.append({
                "objectid": child["objectid"],
                "name": child.get("name", ""),
                "instance_count": 1,
                "instance_ids": [child["objectid"]],
            })
            instance_ids.add(child["objectid"])
    return type_nodes, instance_ids


def has_sfdc(obj: dict) -> bool:
    if not obj:
        return False
    for gp in obj.get("properties", {}).values():
        if isinstance(gp, dict):
            for k in gp:
                if "sfdc" in k.lower():
                    return True
    return False


@app.get("/api/projects")
def list_projects():
    try:
        token = get_token()
        hubs = aps.get_hubs(token)
        all_projects = []
        for hub in hubs:
            projects = aps.get_projects(token, hub["id"])
            for p in projects:
                all_projects.append({
                    "id": p["id"],
                    "name": p["attributes"]["name"],
                    "hub_id": hub["id"],
                })
        all_projects.sort(key=lambda x: x["name"])
        return all_projects
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/models")
def get_project_models(project_id: str, hub_id: str = Query(default=HUB_ID)):
    try:
        token = get_token()
        files = aps.find_rvt_files(token, hub_id, project_id)
        if not files:
            raise HTTPException(status_code=404, detail="No .rvt files found in this project")
        files = deduplicate_by_name(files)
        files.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return files
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/arch-models")
def get_arch_models(project_id: str, hub_id: str = Query(default=HUB_ID)):
    try:
        token = get_token()
        files = aps.find_rvt_files(token, hub_id, project_id)
        matches = [f for f in files if is_arch_model(f["name"])]
        if not matches:
            raise HTTPException(status_code=404, detail="No architecture .rvt files found (looking for AR, ARCH, IA, INT in filename)")
        matches = deduplicate_by_name(matches)
        matches.sort(key=lambda x: x.get("last_modified", ""), reverse=True)
        return matches
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/schedule")
def get_schedule(
    urn: str = Query(...),
    project_name: str = Query(default=""),
    schedule_type: str = Query(default="furniture"),
    selected_columns: List[str] = Query(default=[]),
):
    try:
        token = get_token()

        if schedule_type not in SCHEDULE_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown schedule type: {schedule_type}")

        target_categories = SCHEDULE_CATEGORIES[schedule_type]

        views = aps.get_model_views(token, urn)
        if not views:
            raise HTTPException(status_code=404, detail="No views found in model")

        # Use master view first; if target categories are empty, search all 3D views
        guid = get_guid(views)
        tree_data = aps.get_object_tree(token, urn, guid)
        top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
        cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

        # Count instances in master view — if very few, search other views for a richer one
        master_instance_count = sum(
            sum(len(t.get("objects", [])) for t in cat.get("objects", []))
            for cat in cat_nodes.values()
        ) if cat_nodes else 0

        # For floors/finishes, always search for the best view since master views are often sparse
        # For other types, only fall back if master view has very few instances
        sparse_threshold = 2 if schedule_type in ("floors", "finishes", "casework") else 5
        if not cat_nodes or master_instance_count < sparse_threshold:
            # Master view has no/sparse data — search all 3D views for the best one
            better_guid = get_best_guid_for_schedule(token, urn, views, target_categories)
            if better_guid != guid:
                guid = better_guid
                tree_data = aps.get_object_tree(token, urn, guid)
                top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
                cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

        if not cat_nodes:
            return {"items": [], "levels": [], "available_columns": [], "preset_columns": PRESET_COLUMNS[schedule_type]}

        props_data = aps.get_properties(token, urn, guid)
        collection = props_data.get("data", {}).get("collection", [])
        props_by_id = {obj["objectid"]: obj for obj in collection}

        # Collect all type/instance nodes across matched categories
        all_type_nodes = []
        all_instance_ids = set()
        instance_to_type = {}
        for cat_node in cat_nodes.values():
            tnodes, iids = collect_type_and_instance_ids(cat_node)
            all_type_nodes.extend(tnodes)
            all_instance_ids.update(iids)
            for tn in tnodes:
                for iid in tn["instance_ids"]:
                    instance_to_type[iid] = tn["objectid"]

        # Determine where params live — prefer instances, fall back to type nodes if instances are empty
        sample_type = props_by_id.get(all_type_nodes[0]["objectid"]) if all_type_nodes else None
        sample_inst = props_by_id.get(next(iter(all_instance_ids), None)) if all_instance_ids else None
        inst_has_props = bool(sample_inst and sample_inst.get("properties"))
        type_has_props = bool(sample_type and sample_type.get("properties"))
        params_on_instances = inst_has_props or not type_has_props

        # Noise param groups to exclude from the column picker
        EXCLUDE_GROUPS = {"IFC Parameters", "Phasing"}
        EXCLUDE_PARAM_PREFIXES = ("ifc", "type ifc", "export type", "omniclass")
        EXCLUDE_PARAMS = {
            "Workset", "Edited by", "Type Image", "Image", "Assembly Code",
            "Assembly Description", "Code Name", "Type IfcGUID", "IsExternal",
            "LoadBearing", "Type IFC Predefined Type", "Export Type to IFC As",
            "Export Type to IFC",
        }

        def is_useful_param(group_name: str, key: str) -> bool:
            if group_name in EXCLUDE_GROUPS:
                return False
            if key in EXCLUDE_PARAMS:
                return False
            if any(key.lower().startswith(p) for p in EXCLUDE_PARAM_PREFIXES):
                return False
            return True

        all_param_names = set()
        for oid in list(all_instance_ids)[:50]:
            obj = props_by_id.get(oid)
            if obj:
                for group_name, gp in obj.get("properties", {}).items():
                    if isinstance(gp, dict):
                        for k in gp:
                            if is_useful_param(group_name, k):
                                all_param_names.add(k)
        for tn in all_type_nodes[:50]:
            obj = props_by_id.get(tn["objectid"])
            if obj:
                for group_name, gp in obj.get("properties", {}).items():
                    if isinstance(gp, dict):
                        for k in gp:
                            if is_useful_param(group_name, k):
                                all_param_names.add(k)

        # Always add synthetic columns
        all_param_names.add("Family & Type")
        all_param_names.add("Family")
        all_param_names.add("Type")
        all_param_names.add("Count")
        if schedule_type == "furniture":
            all_param_names.add("Validation Status")
        available_columns = sorted(all_param_names)

        cols = selected_columns if selected_columns else PRESET_COLUMNS[schedule_type]

        # Airtable setup for furniture — always fetch so validate button works
        at_records = []
        region = ""
        if schedule_type == "furniture":
            region = at.parse_region(project_name) if project_name else ""
            try:
                at_records = at.fetch_records(region)
            except Exception:
                at_records = []

        # Build a set of type node names from the tree for category matching
        type_node_names = {tn["objectid"]: tn["name"] for tn in all_type_nodes}

        # Also include all instance objectids directly from the tree
        # This handles cases where instance names differ from type node names (e.g. Floors)
        all_category_ids = all_instance_ids | set(type_node_names.keys())

        # Scan the full properties collection for all objects in target categories
        type_node_name_set = set(type_node_names.values())

        import re as _re
        def base_name(name: str) -> str:
            return _re.sub(r'\s*\[\d+\]$', '', name).strip()

        # Group by base_name + level — scan entire collection
        groups = defaultdict(lambda: {
            "total": 0, "levels": defaultdict(int),
            "param_obj": None, "type_node_name": ""
        })

        for obj in collection:
            obj_name = obj.get("name", "")
            bn = base_name(obj_name)
            # Match by name OR by being directly in the tree
            if bn not in type_node_name_set and obj.get("objectid") not in all_category_ids:
                continue
            fp_obj = flat_props(obj)
            type_name = fp_obj.get("Type Name", "").strip() or bn
            level = fp_obj.get("Level") or fp_obj.get("Schedule Level") or ""

            # For floors: skip elements with no level AND no area (type/category nodes)
            if schedule_type in ("floors", "finishes"):
                area_val = fp_obj.get("Area", "")
                if not level and not area_val:
                    continue
                # Group by type+level and accumulate area
                key = (type_name, level)
                try:
                    area_num = float(str(area_val).split()[0]) if area_val else 0.0
                except (ValueError, IndexError):
                    area_num = 0.0
                groups[key]["total"] += 1
                groups[key]["levels"][level] += 1
                groups[key]["type_node_name"] = bn
                groups[key]["area_sum"] = groups[key].get("area_sum", 0.0) + area_num
                groups[key]["area_unit"] = str(area_val).split()[-1] if area_val and len(str(area_val).split()) > 1 else ""
                if groups[key]["param_obj"] is None:
                    groups[key]["param_obj"] = obj
            else:
                key = (bn, type_name)
                groups[key]["total"] += 1
                groups[key]["levels"][level] += 1
                groups[key]["type_node_name"] = bn
                if groups[key]["param_obj"] is None:
                    groups[key]["param_obj"] = obj

        # Determine level filter from request (passed via selected_columns hack or derived)
        # We'll emit one row per type with total count, plus level breakdown in metadata
        # Check if any element in this model has SFDC_Tag Number populated
        has_sfdc_tag = any(
            flat_props(grp["param_obj"]).get("SFDC_Tag Number", "") or flat_props(grp["param_obj"]).get("SFDC_TAG NUMBER", "")
            for grp in groups.values() if grp["param_obj"]
        )
        tag_param = "SFDC_Tag Number" if has_sfdc_tag else "Type Mark"
        print(f"[SCHEDULE] Tag source: {tag_param} (SFDC_Tag Number found: {has_sfdc_tag})")

        # Update preset columns label if falling back to Type Mark
        if not has_sfdc_tag and schedule_type == "furniture":
            cols = [("Type Mark" if c == "SFDC_Tag Number" else c) for c in cols]

        rows = []
        for type_id, grp in groups.items():
            param_obj = grp["param_obj"]
            if not param_obj:
                continue

            fp = flat_props(param_obj)
            family_name = grp["type_node_name"] or param_obj.get("name", "")
            type_name = fp.get("Type Name", "").strip() or family_name

            # For floors: reconstruct area from summed value
            if schedule_type in ("floors", "finishes"):
                level_val = ", ".join(sorted(k for k in grp["levels"].keys() if k))
                area_sum = grp.get("area_sum", 0.0)
                area_unit = grp.get("area_unit", "ft^2")
                area_str = f"{area_sum:.3f} {area_unit}".strip() if area_sum else ""
            else:
                level_val = None
                area_str = None

            row = {}
            for col in cols:
                if col == "Family & Type":
                    row[col] = f"{family_name} : {type_name}" if type_name != family_name else family_name
                elif col == "Family":
                    row[col] = family_name
                elif col == "Type":
                    row[col] = type_name
                elif col == "Count":
                    row[col] = str(grp["total"])
                elif col == "Level":
                    row[col] = level_val if level_val is not None else ", ".join(sorted(grp["levels"].keys()))
                elif col == "Area" and schedule_type in ("floors", "finishes"):
                    row[col] = area_str or fp.get("Area", "")
                elif col in ("SFDC_Tag Number", "Type Mark"):
                    row[col] = fp.get("SFDC_Tag Number", "") or fp.get("SFDC_TAG NUMBER", "") or fp.get("Type Mark", "")
                else:
                    row[col] = fp.get(col, "")

            row["_levels"] = dict(grp["levels"])

            if schedule_type == "furniture":
                sfdc_tag = fp.get("SFDC_Tag Number", "") or fp.get("SFDC_TAG NUMBER", "")
                frame_tag = fp.get("Frame Tag", "")
                validation = at.validate_row(sfdc_tag, frame_tag, at_records)
                row["Validation Status"] = validation["status"]
                row["_validation_color"] = validation["color"]

            rows.append(row)

        if schedule_type in ("floors", "finishes"):
            rows.sort(key=lambda x: (x.get("Level", ""), x.get("Type Mark", "") or x.get("Type", "")))
        else:
            rows.sort(key=lambda x: (x.get("Family", ""), x.get("Type", "")))
        all_levels = set()
        for grp in groups.values():
            all_levels.update(grp["levels"].keys())
        levels = sorted(l for l in all_levels if l)

        effective_presets = (
            [("Type Mark" if c == "SFDC_Tag Number" else c) for c in PRESET_COLUMNS[schedule_type]]
            if schedule_type == "furniture" and not has_sfdc_tag
            else PRESET_COLUMNS[schedule_type]
        )

        return {
            "items": rows,
            "levels": levels,
            "available_columns": available_columns,
            "preset_columns": effective_presets,
        }

    except HTTPException:
        raise
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/capacity")
def get_capacity(
    furniture_urn: str = Query(...),
    interior_urn: str = Query(...),
):
    try:
        token = get_token()
        print(f"[CAPACITY] Starting spatial join: furniture={furniture_urn[:60]}... interior={interior_urn[:60]}...")

        # Spatial join: returns list of {room_name, raw_seats, level}
        furniture_items = sj.get_room_seats(token, furniture_urn, interior_urn)
        print(f"[CAPACITY] Furniture items assigned to rooms: {len(furniture_items)}")

        if not furniture_items:
            return {"iw": 0, "open_collab": 0, "amenity": 0, "total": 0, "breakdown": [], "levels": []}

        result = cap_eng.calculate_capacity(furniture_items)
        return result

    except HTTPException:
        raise
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")
