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
import benchmark_engine as bm
import report_runner as rr
from concurrent.futures import ThreadPoolExecutor, as_completed

app = FastAPI()


@app.on_event("startup")
async def startup():
    rr.start_scheduler()

HUB_ID = "b.8a643169-4b2b-4c79-bff4-289208a76b2e"

# NOTE: filenames commonly abbreviate furniture as "FUR" (not "FURN"), and nearly
# every Salesforce model filename contains "Symetri" (the CAD vendor) — so "sym"
# must NOT be a furniture signal or it matches almost everything.
FURNITURE_KEYWORDS = {"fur", "furn", "furniture", "fn", "ff&e"}
ARCH_KEYWORDS = {" ar ", "_ar_", "-ar-", " a ", "_a_", "-a-", " ia ", "_ia_", "-ia-", "arch"}
ARCH_MODEL_KEYWORDS = {"ar", "arch", "ia", "int"}

SCHEDULE_CATEGORIES = {
    "furniture":  ["Furniture", "Furniture Systems"],
    "rooms":      ["Rooms"],
    "floors":     ["Floors"],
    "doors":      ["Doors"],
    "casework":   ["Casework"],
    "finishes":   ["Rooms"],
    "areas":      ["Areas", "Area", "Area Plans"],  # Try multiple category names
}

PRESET_COLUMNS = {
    "furniture": ["SFDC_Tag Number", "SFDC_Seat Count", "Family", "Type", "Count", "Manufacturer"],
    "rooms":     ["Number", "Name", "Area", "Level", "Occupancy"],
    "floors":    ["Type", "Type Mark", "Level", "Area"],
    "doors":     ["Mark", "Family", "Type", "Level", "From Room", "To Room", "Width", "Height", "Fire Rating", "Hardware Group", "Configuration", "Comments"],
    "casework":  ["Family & Type", "Count", "Manufacturer", "Finish 1"],
    "finishes":  ["Number", "Name", "Floor Finish", "Wall Finish", "Base Finish", "Ceiling Finish"],
    "areas":     ["Name", "Area Scheme", "Area", "Level"],
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


def get_best_guid_for_schedule(token, urn: str, views: list, target_categories: list,
                               min_instances: int = 1) -> str:
    """Find the 3D view that has the most instances for the target categories.

    Two passes so we work on both warm and cold models:

      Pass 1 — fast probe (poll=False) of every 3D view. On a model whose per-view
      object trees are already built server-side this immediately finds the richest
      view.

      Pass 2 — only if pass 1 found nothing. Each Revit 3D view is a *separate*
      derivative, and on a cold model those trees return HTTP 202/empty until the
      server lazily builds them — so a non-polling probe sees 0 everywhere and we'd
      wrongly fall back to the (sparse) master view. This was why Mexico City's
      furniture schedule intermittently came back empty: master view is sparse, and
      the cold per-view probes all read as empty. So here we poll each 3D view until
      its tree is populated, stopping as soon as one clears the threshold.
    """
    import aps_client as aps_mod
    threed = [v for v in views if v.get("role") == "3d"]
    best_guid = get_guid(views)
    best_count = 0

    def count_for(guid, poll):
        try:
            tree = aps_mod.get_object_tree(token, urn, guid, poll=poll,
                                           max_attempts=6, wait_seconds=5)
            top = tree.get("data", {}).get("objects", [{}])[0].get("objects", [])
            return sum(
                sum(len(t.get("objects", [])) for t in cat.get("objects", []))
                for cat in top if cat["name"] in target_categories
            )
        except Exception:
            return 0

    # Pass 1: fast, no polling — catches already-built (warm) views.
    for v in threed:
        c = count_for(v["guid"], poll=False)
        if c > best_count:
            best_count, best_guid = c, v["guid"]

    # Pass 2: nothing found on the fast pass — the per-view trees may just be cold.
    # Poll each 3D view until populated; stop at the first view that has the data.
    if best_count < min_instances:
        print(f"[SCHEDULE] Fast view probe found nothing for {target_categories}; "
              f"polling {len(threed)} 3D views (model likely cold)...")
        for v in threed:
            c = count_for(v["guid"], poll=True)
            if c > best_count:
                best_count, best_guid = c, v["guid"]
            if best_count >= min_instances:
                break

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


def build_area_schedule(area_records: list) -> dict:
    """Shape SVF-derived Area records into the standard schedule response.

    Each area is a unique element (not counted/grouped like furniture types), so
    one record maps to one row. Rows are grouped by (scheme, name, level) only to
    merge exact duplicates and sum their areas, matching the areas grouping the
    object-tree path used.
    """
    cols = PRESET_COLUMNS["areas"]  # ["Name", "Area Scheme", "Area", "Level"]
    groups = {}
    for rec in area_records:
        key = (rec.get("area_scheme", ""), rec.get("name", ""), rec.get("number", ""), rec.get("level", ""))
        g = groups.get(key)
        if not g:
            g = {"rec": rec, "area_sum": 0.0, "count": 0}
            groups[key] = g
        g["area_sum"] += rec.get("area", 0.0)
        g["count"] += 1

    rows = []
    all_levels = set()
    all_schemes = set()
    for g in groups.values():
        rec = g["rec"]
        level = rec.get("level", "")
        scheme = rec.get("area_scheme", "")
        area_unit = rec.get("area_unit", "ft^2")
        area_str = f"{g['area_sum']:.3f} {area_unit}".strip()
        row = {}
        for col in cols:
            if col == "Name":
                row[col] = rec.get("name", "")
            elif col == "Area Scheme":
                row[col] = scheme
            elif col == "Area":
                row[col] = area_str
            elif col == "Level":
                row[col] = level
            else:
                row[col] = ""
        row["Number"] = rec.get("number", "")
        row["_levels"] = {level: g["count"]} if level else {}
        rows.append(row)
        if level:
            all_levels.add(level)
        if scheme:
            all_schemes.add(scheme)

    rows.sort(key=lambda x: (x.get("Area Scheme", ""), x.get("Level", ""), x.get("Name", "")))

    return {
        "items": rows,
        "levels": sorted(all_levels),
        "available_columns": ["Name", "Area Scheme", "Area", "Level", "Number"],
        "preset_columns": cols,
        "area_schemes": sorted(all_schemes),
    }


@app.get("/api/schedule")
def get_schedule(
    urn: str = Query(...),
    project_name: str = Query(default=""),
    schedule_type: str = Query(default="furniture"),
    selected_columns: List[str] = Query(default=[]),
    itemize: bool = Query(default=False),
):
    try:
        token = get_token()

        if schedule_type not in SCHEDULE_CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Unknown schedule type: {schedule_type}")

        target_categories = SCHEDULE_CATEGORIES[schedule_type]
        print(f"[SCHEDULE] Running {schedule_type} schedule, looking for categories: {target_categories}")

        views = aps.get_model_views(token, urn)
        if not views:
            raise HTTPException(status_code=404, detail="No views found in model")

        # Areas are 2D, view-specific elements that the Model Derivative object-tree
        # API does not expose (no view's tree contains an "Areas" category). Read
        # them directly from the SVF property database instead — same source that
        # spatial_join uses for rooms.
        if schedule_type == "areas":
            area_records = sj.get_areas(token, urn)
            return build_area_schedule(area_records)

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

        # For floors/finishes/areas, always search for the best view since master views are often sparse
        # For other types, only fall back if master view has very few instances
        sparse_threshold = 2 if schedule_type in ("floors", "finishes", "casework", "areas") else 5
        if not cat_nodes or master_instance_count < sparse_threshold:
            print(f"[SCHEDULE] Master view has {master_instance_count} instances for {schedule_type}. Searching all views...")
            # Master view has no/sparse data — search all 3D views for the best one
            better_guid = get_best_guid_for_schedule(token, urn, views, target_categories)
            if better_guid != guid:
                guid = better_guid
                tree_data = aps.get_object_tree(token, urn, guid)
                top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
                cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

        if not cat_nodes:
            print(f"[SCHEDULE] No categories found for {schedule_type}. Looking for: {target_categories}")
            all_category_names = [node['name'] for node in top_objects]
            print(f"[SCHEDULE] Available top-level categories ({len(all_category_names)}): {all_category_names}")

            # For areas: scan properties collection to find Area elements
            if schedule_type == "areas":
                print(f"[SCHEDULE] Scanning all properties for Area elements...")
                props_data = aps.get_properties(token, urn, guid)
                collection = props_data.get("data", {}).get("collection", [])
                print(f"[SCHEDULE] Total objects in properties collection: {len(collection)}")

                # Look for objects with Area Scheme parameter - scan ALL objects
                area_objects = []
                for obj in collection:
                    fp = flat_props(obj)
                    obj_name = obj.get("name", "")
                    # Check for Area Scheme parameter or "Area" in name
                    if "Area Scheme" in fp or fp.get("Category", "") == "Areas":
                        area_objects.append({
                            "name": obj_name,
                            "category": fp.get("Category", ""),
                            "has_area_scheme": "Area Scheme" in fp,
                            "area_scheme": fp.get("Area Scheme", ""),
                        })

                print(f"[SCHEDULE] Scanned all {len(collection)} objects")
                if area_objects:
                    print(f"[SCHEDULE] Found {len(area_objects)} objects with Area data:")
                    for ao in area_objects[:5]:
                        print(f"  - {ao}")
                else:
                    print(f"[SCHEDULE] No objects with 'Area Scheme' parameter or Category='Areas' found")
                    # Show sample object to see what parameters exist
                    if collection:
                        sample_obj = collection[0]
                        sample_fp = flat_props(sample_obj)
                        print(f"[SCHEDULE] Sample object name: {sample_obj.get('name', '')}")
                        print(f"[SCHEDULE] Sample object keys: {list(sample_fp.keys())[:20]}")
                        # Look for any parameter with "area" in the key name
                        area_params = [k for k in sample_fp.keys() if 'area' in k.lower()]
                        if area_params:
                            print(f"[SCHEDULE] Parameters with 'area' in name: {area_params}")

            return {
                "items": [],
                "levels": [],
                "available_columns": [],
                "preset_columns": PRESET_COLUMNS[schedule_type],
                "debug_categories": all_category_names,  # Return this for debugging
            }

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
        building_records = []
        building_validation = {}
        building_code = ""
        manufacturer_mapping = {}
        region = ""
        if schedule_type == "furniture":
            region = at.parse_region(project_name) if project_name else ""
            building_code = at.parse_building_code(project_name)
            try:
                at_records = at.fetch_records(region)
                building_records = at.fetch_buildings(region)
                building_validation = at.validate_building(project_name, building_records)
                manufacturer_mapping = at.fetch_manufacturers()
            except Exception:
                at_records = []
                building_records = []
                manufacturer_mapping = {}

        # Build a set of type node names from the tree for category matching
        type_node_names = {tn["objectid"]: tn["name"] for tn in all_type_nodes}

        # Build reverse map: instance_objectid → family_name (tree node name)
        # Each type node in the tree contains instance objects
        instance_to_family = {}
        for cat_node in cat_nodes.values():
            for type_node in cat_node.get("objects", []):
                family = type_node.get("name", "")
                # Direct type node objectid → family
                instance_to_family[type_node["objectid"]] = family
                # Instance children → same family
                for inst in type_node.get("objects", []):
                    instance_to_family[inst["objectid"]] = family

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
            "param_obj": None, "type_node_name": "",
            "instances": []  # list of (objectid, externalId) for itemize mode
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

            # For furniture: skip bare type nodes (no element ID, no level, no data)
            # These are the category/type header objects that duplicate instance rows
            if schedule_type == "furniture":
                obj_id = obj.get("objectid")
                is_instance = "[" in obj_name or obj_id in all_instance_ids
                has_distinct_type = bool(fp_obj.get("Type Name", "").strip() and
                                         fp_obj.get("Type Name", "").strip() != bn)
                has_sfdc_data = bool(fp_obj.get("SFDC_Tag Number") or fp_obj.get("SFDC_TAG NUMBER") or
                                     fp_obj.get("SFDC_Seat Count") or fp_obj.get("Manufacturer"))
                if not is_instance and not has_sfdc_data:
                    continue  # skip bare type/category nodes without SFDC data

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
            elif schedule_type == "areas":
                # Areas: group by area scheme, name, and level
                area_val = fp_obj.get("Area", "")
                area_scheme = fp_obj.get("Area Scheme", "")
                area_name = fp_obj.get("Name", bn)

                # Skip elements with no area (type/category nodes)
                if not area_val:
                    continue

                key = (area_scheme, area_name, level)
                try:
                    area_num = float(str(area_val).split()[0]) if area_val else 0.0
                except (ValueError, IndexError):
                    area_num = 0.0

                groups[key]["total"] = 1  # Areas are unique, not counted
                groups[key]["levels"][level] = 1
                groups[key]["type_node_name"] = bn
                groups[key]["area_sum"] = area_num
                groups[key]["area_unit"] = str(area_val).split()[-1] if area_val and len(str(area_val).split()) > 1 else "ft^2"
                groups[key]["area_scheme"] = area_scheme
                groups[key]["area_name"] = area_name
                if groups[key]["param_obj"] is None:
                    groups[key]["param_obj"] = obj
            else:
                # For doors, group by Mark (each door is unique)
                if schedule_type == "doors":
                    door_mark = fp_obj.get("Mark", "")
                    # Use objectid as fallback for doors without marks
                    obj_id_key = obj.get("objectid", "") if not door_mark else ""
                    key = (bn, type_name, door_mark or obj_id_key)
                else:
                    sfdc_tag_key = fp_obj.get("SFDC_Tag Number", "") or fp_obj.get("SFDC_TAG NUMBER", "") or fp_obj.get("Type Mark", "")
                    # Use objectid as tiebreaker for identical items with no distinguishing data
                    obj_id_key = obj.get("objectid", "") if not sfdc_tag_key and not type_name else ""
                    key = (bn, type_name, sfdc_tag_key or obj_id_key)
                groups[key]["total"] += 1
                groups[key]["levels"][level] += 1
                # Look up the true family name from the tree hierarchy
                obj_id = obj.get("objectid")
                family_from_tree = instance_to_family.get(obj_id, "")
                if not groups[key]["type_node_name"]:
                    groups[key]["type_node_name"] = family_from_tree or bn
                # Prefer objects that have SFDC data over bare type nodes
                existing = groups[key]["param_obj"]
                if existing is None:
                    groups[key]["param_obj"] = obj
                else:
                    # Replace if this object has tag/seat data and existing doesn't
                    existing_fp = flat_props(existing)
                    new_fp = flat_props(obj)
                    has_sfdc_new = bool(new_fp.get("SFDC_Tag Number") or new_fp.get("SFDC_TAG NUMBER") or new_fp.get("Type Mark"))
                    has_sfdc_existing = bool(existing_fp.get("SFDC_Tag Number") or existing_fp.get("SFDC_TAG NUMBER") or existing_fp.get("Type Mark"))
                    if has_sfdc_new and not has_sfdc_existing:
                        groups[key]["param_obj"] = obj
                # Store instance info for itemize mode
                if obj_id and "[" in obj_name:  # only placed instances have element IDs in name
                    inst_entry = {
                        "objectid": obj_id,
                        "externalId": obj.get("externalId", ""),
                        "level": level,
                    }
                    # For doors, also store Mark for spatial matching
                    if schedule_type == "doors":
                        inst_entry["mark"] = fp_obj.get("Mark", "")
                    groups[key]["instances"].append(inst_entry)
                    # Also add to the tree-family-keyed group if different (handles WK sub-parts)
                    if family_from_tree and family_from_tree != bn:
                        family_key = (family_from_tree, type_name)
                        if family_key in groups:
                            groups[family_key]["instances"].append(inst_entry)

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

            # For floors/areas: reconstruct area from summed value
            if schedule_type in ("floors", "finishes", "areas"):
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
                elif col == "Area" and schedule_type in ("floors", "finishes", "areas"):
                    row[col] = area_str or fp.get("Area", "")
                elif col == "Area Scheme" and schedule_type == "areas":
                    row[col] = grp.get("area_scheme", "") or fp.get("Area Scheme", "")
                elif col == "Name" and schedule_type == "areas":
                    row[col] = grp.get("area_name", "") or fp.get("Name", "")
                elif col in ("SFDC_Tag Number", "Type Mark"):
                    val = fp.get("SFDC_Tag Number", "") or fp.get("SFDC_TAG NUMBER", "") or fp.get("Type Mark", "")
                    # For floors: if Type Mark empty, extract code from Type name
                    if not val and schedule_type in ("floors", "finishes"):
                        import re as _re3
                        m = _re3.search(r'\b([A-Za-z]{2,4}-\d+)\b', type_name)
                        if m:
                            val = m.group(1).upper()
                    # For furniture: if no tag found, extract from Family name
                    if not val and schedule_type == "furniture":
                        import re as _re4
                        # Look for patterns like CH-08, SS-01, etc. in family name
                        m = _re4.search(r'\b([A-Za-z]{2,4}-\d+)\b', family_name)
                        if m:
                            val = m.group(1).upper()
                            print(f"[SCHEDULE] Extracted tag '{val}' from family name '{family_name}'")
                    row[col] = val
                else:
                    row[col] = fp.get(col, "")

            row["_levels"] = dict(grp["levels"])

            if schedule_type == "furniture":
                sfdc_tag = fp.get("SFDC_Tag Number", "") or fp.get("SFDC_TAG NUMBER", "")
                # Frame Tag in Revit is stored as Type Mark (e.g. CH-08, SS-01)
                frame_tag = fp.get("Type Mark", "") or fp.get("Frame Tag", "")
                manufacturer = fp.get("Manufacturer", "")

                # If no tag found, try to extract from Family name BEFORE validation
                if not sfdc_tag and not frame_tag:
                    import re as _re5
                    m = _re5.search(r'\b([A-Za-z]{2,4}-\d+)\b', family_name)
                    if m:
                        extracted_tag = m.group(1).upper()
                        frame_tag = extracted_tag  # Use extracted tag for validation
                        print(f"[SCHEDULE] Using extracted tag '{extracted_tag}' from family '{family_name}' for validation")

                # Populate Frame Tag column in row if requested
                if "Frame Tag" in cols:
                    row["Frame Tag"] = frame_tag
                validation = at.validate_row(
                    sfdc_tag, frame_tag, at_records, manufacturer,
                    building_code, building_records, manufacturer_mapping
                )
                row["Validation Status"] = validation["status"]
                row["_validation_color"] = validation["color"]
                row["_airtable_manufacturer"] = validation.get("airtable_manufacturer", "")
                row["_manufacturer_match"] = validation.get("manufacturer_match")
                row["_building_match"] = validation.get("building_match")
                row["_airtable_region"] = validation.get("airtable_region", "")
                row["_is_cross_region"] = validation.get("is_cross_region", False)
                row["_needs_fabric_code"] = validation.get("needs_fabric_code", False)
                row["Note"] = validation.get("note", "")

            # Attach instance list for itemize mode (furniture and doors)
            if schedule_type in ("furniture", "doors"):
                row["_instances"] = grp.get("instances", [])

            rows.append(row)

        # For furniture: deduplicate rows that are bare type nodes
        # (Count=1, no instances, same family+tag as another row)
        if schedule_type == "furniture":
            seen_keys = set()
            deduped = []
            for row in rows:
                # Key: family + tag (or type if no tag)
                dedup_key = (row.get("Family", ""), row.get("SFDC_Tag Number", "") or row.get("Type Mark", "") or row.get("Type", ""))
                instances = row.get("_instances", [])
                is_bare = (row.get("Count", "0") == "1" and not instances and
                           not row.get("SFDC_Seat Count", "0").replace("0",""))
                if is_bare and dedup_key in seen_keys:
                    continue  # skip bare duplicate
                seen_keys.add(dedup_key)
                deduped.append(row)
            rows = deduped

        if schedule_type in ("floors", "finishes"):
            rows.sort(key=lambda x: (x.get("Level", ""), x.get("Type Mark", "") or x.get("Type", "")))
        elif schedule_type == "areas":
            rows.sort(key=lambda x: (x.get("Area Scheme", ""), x.get("Level", ""), x.get("Name", "")))
        else:
            rows.sort(key=lambda x: (x.get("Family", ""), x.get("Type", "")))

        all_levels = set()
        all_area_schemes = set()
        for grp in groups.values():
            all_levels.update(grp["levels"].keys())
        levels = sorted(l for l in all_levels if l)

        # Collect area schemes for areas schedule type
        if schedule_type == "areas":
            for row in rows:
                scheme = row.get("Area Scheme", "")
                if scheme:
                    all_area_schemes.add(scheme)
        area_schemes = sorted(all_area_schemes)

        effective_presets = (
            [("Type Mark" if c == "SFDC_Tag Number" else c) for c in PRESET_COLUMNS[schedule_type]]
            if schedule_type == "furniture" and not has_sfdc_tag
            else PRESET_COLUMNS[schedule_type]
        )

        # Add spatial room-to-room data for doors
        if schedule_type == "doors":
            print(f"[DOORS] Calculating room-to-room relationships...")
            print(f"[DOORS] Number of door rows: {len(rows)}")
            try:
                print(f"[DOORS] Calling get_door_rooms with URN: {urn[:50]}...")
                # Use same URN for both doors and rooms (both in architecture model)
                door_rooms = sj.get_door_rooms(token, urn, urn)
                print(f"[DOORS] get_door_rooms returned, found {len(door_rooms)} door-room assignments")
                if door_rooms:
                    print(f"[DOORS] Sample assignments: {list(door_rooms.items())[:3]}")

                # Build mark-to-room and externalId-to-room mappings for easier lookup
                mark_to_rooms = {}
                extid_to_rooms = {}
                doors_without_marks = 0
                for dbid, room_data in door_rooms.items():
                    mark = room_data.get("mark", "")
                    ext_id = room_data.get("external_id", "")
                    if mark:
                        mark_to_rooms[mark] = room_data
                    else:
                        doors_without_marks += 1
                    if ext_id:
                        extid_to_rooms[ext_id] = room_data
                print(f"[DOORS] Built mark-to-rooms mapping with {len(mark_to_rooms)} entries")
                print(f"[DOORS] Built externalId-to-rooms mapping with {len(extid_to_rooms)} entries")
                print(f"[DOORS] {doors_without_marks} spatial doors had no Mark")
                if mark_to_rooms:
                    print(f"[DOORS] Sample marks: {list(mark_to_rooms.keys())[:5]}")

                # Collect all door marks from Properties API for comparison
                all_property_marks = []
                doors_without_marks = 0
                for row in rows:
                    instances = row.get("_instances", [])
                    for inst in instances:
                        mark = inst.get("mark", "")
                        if mark:
                            all_property_marks.append(mark)
                        else:
                            doors_without_marks += 1
                print(f"[DOORS] Properties API has {len(all_property_marks)} doors with marks")
                print(f"[DOORS] Properties API has {doors_without_marks} doors WITHOUT marks")
                print(f"[DOORS] Sample property marks: {all_property_marks[:10]}")

                # Find marks in Properties but not in spatial data
                property_marks_set = set(all_property_marks)
                spatial_marks_set = set(mark_to_rooms.keys())
                missing_spatial = property_marks_set - spatial_marks_set
                if missing_spatial:
                    print(f"[DOORS] WARNING: {len(missing_spatial)} doors in Properties API have no spatial data")
                    print(f"[DOORS] Missing marks: {list(missing_spatial)[:20]}")

                # Match door instances to spatial data
                # For grouped doors (multiple of same type), aggregate room names
                matched_count = 0
                for row in rows:
                    instances = row.get("_instances", [])
                    print(f"[DOORS] Row '{row.get('Family', 'Unknown')}' has {len(instances)} instances")
                    if instances:
                        sample_ids = [inst.get("objectid") for inst in instances[:3]]
                        sample_ext = [inst.get("externalId") for inst in instances[:3]]
                        print(f"[DOORS]   Sample objectids: {sample_ids}")
                        print(f"[DOORS]   Sample externalIds: {sample_ext}")
                    if not instances:
                        # Still add empty columns
                        row["From Room"] = ""
                        row["To Room"] = ""
                        row["From Number"] = ""
                        row["To Number"] = ""
                        continue

                    # Collect all room assignments for this door type
                    from_rooms = []
                    to_rooms = []
                    from_numbers = []
                    to_numbers = []

                    # Match each door instance by its Mark property, or externalId as fallback
                    for inst in instances:
                        inst_mark = inst.get("mark", "")
                        inst_extid = inst.get("externalId", "")

                        dr = None
                        if inst_mark and inst_mark in mark_to_rooms:
                            dr = mark_to_rooms[inst_mark]
                        elif inst_extid and inst_extid in extid_to_rooms:
                            dr = extid_to_rooms[inst_extid]

                        if dr:
                            matched_count += 1
                            fr = dr.get("from_room", "")
                            tr = dr.get("to_room", "")
                            fn = dr.get("from_number", "")
                            tn = dr.get("to_number", "")
                            if fr and fr not in from_rooms:
                                from_rooms.append(fr)
                            if tr and tr not in to_rooms:
                                to_rooms.append(tr)
                            if fn and fn not in from_numbers:
                                from_numbers.append(fn)
                            if tn and tn not in to_numbers:
                                to_numbers.append(tn)

                    # For types with multiple instances, show all unique room names
                    row["From Room"] = ", ".join(from_rooms) if from_rooms else ""
                    row["To Room"] = ", ".join(to_rooms) if to_rooms else ""
                    row["From Number"] = ", ".join(from_numbers) if from_numbers else ""
                    row["To Number"] = ", ".join(to_numbers) if to_numbers else ""

                print(f"[DOORS] Matched {matched_count} door instances to rooms")

                # Add From Room and To Room to available columns
                available_columns.extend(["From Room", "To Room", "From Number", "To Number"])
                available_columns = sorted(set(available_columns))

            except Exception as e:
                print(f"[DOORS] Error calculating room relationships: {e}")
                import traceback
                traceback.print_exc()

        result = {
            "items": rows,
            "levels": levels,
            "available_columns": available_columns,
            "preset_columns": effective_presets,
        }

        # Add area schemes for areas schedule type
        if schedule_type == "areas":
            result["area_schemes"] = area_schemes

        # Add building validation for furniture schedules
        if schedule_type == "furniture" and building_validation:
            result["building_validation"] = building_validation

        return result

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


def _fetch_floor_items(token: str, urn: str) -> list:
    """Fetch floor schedule items for a single URN. Used by benchmark."""
    views = aps.get_model_views(token, urn)
    if not views:
        return []

    target_categories = SCHEDULE_CATEGORIES["floors"]
    guid = get_guid(views)
    tree_data = aps.get_object_tree(token, urn, guid)
    top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
    cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

    master_count = sum(
        sum(len(t.get("objects", [])) for t in cat.get("objects", []))
        for cat in cat_nodes.values()
    ) if cat_nodes else 0

    if not cat_nodes or master_count < 2:
        better_guid = get_best_guid_for_schedule(token, urn, views, target_categories)
        if better_guid != guid:
            guid = better_guid
            tree_data = aps.get_object_tree(token, urn, guid)
            top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
            cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

    if not cat_nodes:
        return []

    all_type_nodes, all_instance_ids = [], set()
    for cat_node in cat_nodes.values():
        tnodes, iids = collect_type_and_instance_ids(cat_node)
        all_type_nodes.extend(tnodes)
        all_instance_ids.update(iids)

    type_node_name_set = set(tn["name"] for tn in all_type_nodes)
    all_category_ids = all_instance_ids | set(tn["objectid"] for tn in all_type_nodes)

    props_data = aps.get_properties(token, urn, guid)
    collection = props_data.get("data", {}).get("collection", [])

    import re as _re
    def base_name(name):
        return _re.sub(r'\s*\[\d+\]$', '', name).strip()

    groups = defaultdict(lambda: {"area_sum": 0.0, "area_unit": "", "param_obj": None})

    for obj in collection:
        obj_name = obj.get("name", "")
        bn = base_name(obj_name)
        if bn not in type_node_name_set and obj.get("objectid") not in all_category_ids:
            continue
        fp_obj = flat_props(obj)
        type_name = fp_obj.get("Type Name", "").strip() or bn
        level = fp_obj.get("Level") or fp_obj.get("Schedule Level") or ""
        area_val = fp_obj.get("Area", "")
        if not level and not area_val:
            continue
        try:
            area_num = float(str(area_val).split()[0]) if area_val else 0.0
        except (ValueError, IndexError):
            area_num = 0.0
        key = (type_name, level)
        groups[key]["area_sum"] += area_num
        groups[key]["area_unit"] = str(area_val).split()[-1] if area_val and len(str(area_val).split()) > 1 else "ft^2"
        if groups[key]["param_obj"] is None:
            groups[key]["param_obj"] = obj

    items = []
    for (type_name, level), grp in groups.items():
        fp = flat_props(grp["param_obj"]) if grp["param_obj"] else {}
        area_sum = grp["area_sum"]
        area_unit = grp.get("area_unit", "ft^2")

        # Use Type Mark if populated; otherwise extract code pattern from Type name
        type_mark = fp.get("Type Mark", "").strip()
        if not type_mark:
            import re as _re2
            m = _re2.search(r'\b([A-Za-z]{2,4}-\d+)\b', type_name)
            if m:
                type_mark = m.group(1).upper()

        items.append({
            "Type": type_name,
            "Type Mark": type_mark,
            "Level": level,
            "Area": f"{area_sum:.3f} {area_unit}".strip() if area_sum else "",
        })
    return items


@app.get("/api/benchmark")
def run_benchmark(
    project_ids: List[str] = Query(...),
    project_names: List[str] = Query(default=[]),
    hub_id: str = Query(default=HUB_ID),
    schedule_type: str = Query(default="floors"),
):
    try:
        token = get_token()

        # Build pid → name map from passed names (same order as project_ids)
        pid_to_name = {
            pid: (project_names[i] if i < len(project_names) else pid)
            for i, pid in enumerate(project_ids)
        }

        # Find best model URN per project in parallel
        import re as _re
        arch_kw = _re.compile(r'ar[-_]|arch[-_]|interior|int[-_]|ia[-_]', _re.IGNORECASE)
        furn_kw = _re.compile(r'furn|fn[-_]|sym[-_]furn', _re.IGNORECASE)

        def get_project_urn(pid: str):
            try:
                files = aps.find_rvt_files(token, hub_id, pid)
                arch_files = sorted(
                    [f for f in files if arch_kw.search(f["name"]) and not furn_kw.search(f["name"])],
                    key=lambda x: x.get("last_modified", ""), reverse=True
                )
                best = arch_files[0] if arch_files else (files[0] if files else None)
                return pid, pid_to_name[pid], best["urn"] if best else None
            except Exception:
                return pid, pid_to_name[pid], None

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(get_project_urn, pid): pid for pid in project_ids}
            project_urns = {}
            for future in as_completed(futures):
                pid, name, urn = future.result()
                project_urns[pid] = (name, urn)

        # Fetch floor data in parallel
        def fetch_one(pid):
            name, urn = project_urns[pid]
            if not urn:
                return {"project": name, "groups": {}, "total_area": 0.0, "error": "No model found"}
            try:
                items = _fetch_floor_items(token, urn)
                groups = {}
                total = 0.0
                for item in items:
                    tm = item.get("Type Mark", "")
                    tn = item.get("Type", "")
                    area_str = item.get("Area", "")
                    try:
                        area_val = float(str(area_str).split()[0]) if area_str else 0.0
                    except (ValueError, IndexError):
                        area_val = 0.0
                    prefix = bm.extract_prefix(tm, tn)
                    groups[prefix] = groups.get(prefix, 0.0) + area_val
                    total += area_val
                return {"project": name, "groups": groups, "total_area": total, "error": None}
            except Exception as e:
                return {"project": name, "groups": {}, "total_area": 0.0, "error": str(e)}

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(fetch_one, pid): pid for pid in project_ids}
            results = [future.result() for future in as_completed(futures)]

        # Sort results to match input order
        results.sort(key=lambda r: next(
            (i for i, pid in enumerate(project_ids) if project_urns[pid][0] == r["project"]), 999
        ))

        return bm.build_benchmark_result(results)

    except HTTPException:
        raise
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/benchmark-urns")
def run_benchmark_urns(
    model_urns: List[str] = Query(...),
    model_labels: List[str] = Query(default=[]),
    schedule_type: str = Query(default="floors"),
):
    """Benchmark using explicit model URNs — supports multiple models per project."""
    try:
        token = get_token()

        urn_to_label = {
            urn: (model_labels[i] if i < len(model_labels) else urn.split("version=")[-1])
            for i, urn in enumerate(model_urns)
        }

        def fetch_one(urn):
            label = urn_to_label[urn]
            try:
                items = _fetch_floor_items(token, urn)
                groups = {}
                total = 0.0
                for item in items:
                    tm = item.get("Type Mark", "")
                    tn = item.get("Type", "")
                    area_str = item.get("Area", "")
                    try:
                        area_val = float(str(area_str).split()[0]) if area_str else 0.0
                    except (ValueError, IndexError):
                        area_val = 0.0
                    prefix = bm.extract_prefix(tm, tn)
                    groups[prefix] = groups.get(prefix, 0.0) + area_val
                    total += area_val
                return {"project": label, "groups": groups, "total_area": total, "error": None}
            except Exception as e:
                return {"project": label, "groups": {}, "total_area": 0.0, "error": str(e)}

        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = [ex.submit(fetch_one, urn) for urn in model_urns]
            results = [f.result() for f in futures]

        result = bm.build_benchmark_result(results)
        result["schedule_type"] = schedule_type
        return result

    except HTTPException:
        raise
    except requests.HTTPError as e:
        raise HTTPException(status_code=e.response.status_code, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/scheduled-reports")
def list_scheduled_reports():
    return rr.load_configs()


@app.post("/api/scheduled-reports")
async def create_scheduled_report(body: dict):
    import uuid
    from datetime import datetime, timedelta
    configs = rr.load_configs()
    config = {
        "id": str(uuid.uuid4())[:8],
        "name": body.get("name", "Report"),
        "enabled": True,
        "models": body.get("models", []),
        "report_types": body.get("report_types", ["capacity", "furniture"]),
        "interval_days": body.get("interval_days", 14),
        "drive_folder_id": body.get("drive_folder_id", "root"),
        "hub_id": body.get("hub_id", HUB_ID),
        "created_at": datetime.utcnow().isoformat(),
        "last_run": None,
        "next_run": (datetime.utcnow() + timedelta(days=body.get("interval_days", 14))).isoformat(),
    }
    configs.append(config)
    rr.save_configs(configs)
    return config


@app.delete("/api/scheduled-reports/{report_id}")
def delete_scheduled_report(report_id: str):
    configs = rr.load_configs()
    configs = [c for c in configs if c["id"] != report_id]
    rr.save_configs(configs)
    return {"ok": True}


@app.post("/api/scheduled-reports/{report_id}/run")
async def run_scheduled_report_now(report_id: str):
    """Run a report immediately. Returns file contents for Drive upload."""
    config = rr.get_config(report_id)
    if not config:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        result = rr.run_report(config)
        from datetime import datetime
        configs = rr.load_configs()
        for i, c in enumerate(configs):
            if c["id"] == report_id:
                configs[i]["last_run"] = datetime.utcnow().isoformat()
        rr.save_configs(configs)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/drive-upload")
async def upload_to_drive_legacy(body: dict):
    filename = body.get("filename", "report.csv")
    content = body.get("content", "")
    folder_id = body.get("folder_id", "root")
    return {"filename": filename, "size": len(content), "folder_id": folder_id}


@app.post("/api/drive-upload-mcp")
async def upload_to_drive_mcp(body: dict):
    filename = body.get("filename", "report.csv")
    content = body.get("content", "")
    folder_id = body.get("folder_id", "root")
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, prefix='report_') as f:
        f.write(content)
        tmp_path = f.name
    return {"filename": filename, "tmp_path": tmp_path, "folder_id": folder_id, "size": len(content)}


@app.post("/api/drive-upload-execute")
async def drive_upload_execute(body: dict):
    """
    Upload a file to Google Drive.
    Uses the google-workspace MCP credentials stored in the environment.
    """
    import tempfile, os, subprocess, json as _json
    filename = body.get("filename", "report.csv")
    content = body.get("content", "")
    folder_id = body.get("folder_id", "root")

    # Write content to temp file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False,
                                     prefix='pdp_report_') as f:
        f.write(content)
        tmp_path = f.name

    # Store for MCP upload — return tmp_path so Claude can upload via MCP
    return {
        "filename": filename,
        "tmp_path": tmp_path,
        "folder_id": folder_id,
        "size": len(content),
        "file_url": f"file://{tmp_path}",
        "status": "pending_mcp_upload",
    }


@app.get("/api/viewer-token")
def get_viewer_token():
    """Returns a short-lived 2-legged token for the Autodesk Viewer SDK."""
    try:
        import requests as _req
        from auth import CLIENT_ID, CLIENT_SECRET
        r = _req.post(
            "https://developer.api.autodesk.com/authentication/v2/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "client_credentials",
                "scope": "data:read viewables:read",
            },
        )
        r.raise_for_status()
        d = r.json()
        return {
            "access_token": d["access_token"],
            "expires_in": d.get("expires_in", 3600),
            "token_type": d.get("token_type", "Bearer"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/standards-audit")
def standards_audit(urn: str):
    """
    Compare families in the Global Standards Revit file against Airtable standards.
    Returns a comparison showing:
    - Families in Airtable (Active status) that are missing from the Revit file
    - Families in the Revit file that are not in Airtable or have wrong status
    """
    try:
        token = get_token()

        # Fetch all furniture records from Airtable (all statuses)
        airtable_records = at.fetch_records()
        print(f"[Standards Audit] Fetched {len(airtable_records)} total Airtable records")

        active_standards = {}
        all_standards = {}  # Track all records including retired
        for rec in airtable_records:
            fields = rec.get("fields", {})
            status = str(fields.get("Status", "")).strip()
            frame_tag = str(fields.get("Frame Tag", "")).strip()
            if not frame_tag:
                continue

            record_data = {
                "frame_tag": frame_tag,
                "family_name": str(fields.get("Family Name", "")).strip(),
                "type_name": str(fields.get("Type Name", "")).strip(),
                "manufacturer": fields.get("Manufacturer Abbreviation (from Manufacturers)", [""])[0],
                "status": status,
            }

            # Store all records
            all_standards[frame_tag.lower()] = record_data

            # Store only Active records
            if status.lower() == "active":
                active_standards[frame_tag.lower()] = record_data

        print(f"[Standards Audit] Found {len(active_standards)} Active standards in Airtable")
        if active_standards:
            print(f"[Standards Audit] Sample Airtable tags (first 10): {list(active_standards.keys())[:10]}")

        # Get families from the Revit file
        views = aps.get_model_views(token, urn)
        if not views:
            raise HTTPException(status_code=400, detail="No views found in model")

        target_categories = SCHEDULE_CATEGORIES["furniture"]

        # Try multiple views to find furniture
        cat_nodes = None
        guid = None

        # First try the best guid
        guid = get_best_guid_for_schedule(token, urn, views, target_categories)
        tree_data = aps.get_object_tree(token, urn, guid)
        top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
        cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)

        # If no furniture found, try all 3D views
        if not cat_nodes:
            for view in views:
                if view.get("role") == "3d":
                    guid = view["guid"]
                    tree_data = aps.get_object_tree(token, urn, guid)
                    top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
                    cat_nodes = get_tree_nodes_by_category(top_objects, target_categories)
                    if cat_nodes:
                        break

        # If still no furniture, try looking for "Furniture" or "Casework" categories
        if not cat_nodes:
            # Try expanded category list
            expanded_categories = ["Furniture", "Furniture Systems", "Casework", "Specialty Equipment"]
            for view in views:
                if view.get("role") == "3d":
                    guid = view["guid"]
                    tree_data = aps.get_object_tree(token, urn, guid)
                    top_objects = tree_data.get("data", {}).get("objects", [{}])[0].get("objects", [])
                    cat_nodes = get_tree_nodes_by_category(top_objects, expanded_categories)
                    if cat_nodes:
                        break

        if not cat_nodes:
            raise HTTPException(status_code=400, detail="No furniture found in model. Make sure the model contains Furniture or Furniture Systems categories.")

        # Collect all type nodes (families/types)
        all_type_nodes = []
        for cat_node in cat_nodes.values():
            type_nodes, _ = collect_type_and_instance_ids(cat_node)
            all_type_nodes.extend(type_nodes)

        print(f"[Standards Audit] Found {len(all_type_nodes)} type nodes in categories: {list(cat_nodes.keys())}")

        # Get properties to extract tags
        props_data = aps.get_properties(token, urn, guid)
        collection = props_data.get("data", {}).get("collection", [])

        print(f"[Standards Audit] Got {len(collection)} property objects")

        # Build map of families in Revit with their tags
        revit_families = {}
        families_without_tags = []

        for type_node in all_type_nodes:
            obj_id = type_node["objectid"]
            obj_name = type_node["name"]

            # Find properties for this type
            obj_props = next((o for o in collection if o.get("objectid") == obj_id), None)
            if not obj_props:
                print(f"[Standards Audit] No properties found for object {obj_id}: {obj_name}")
                continue

            fp = flat_props(obj_props)
            family_name = fp.get("Family", "")
            type_name = fp.get("Type", "")
            sfdc_tag = fp.get("SFDC_Tag Number", "")
            type_mark = fp.get("Type Mark", "")

            # Extract tag from Family name if not found in parameters
            tag = sfdc_tag or type_mark
            if not tag:
                import re as _re4
                m = _re4.search(r'\b([A-Za-z]{2,4}-\d+)\b', family_name)
                if m:
                    tag = m.group(1).upper()

            if tag:
                revit_families[tag.lower()] = {
                    "tag": tag,
                    "family_name": family_name,
                    "type_name": type_name,
                    "full_name": obj_name,
                }
            else:
                families_without_tags.append(f"{family_name} - {type_name}")

        print(f"[Standards Audit] Found {len(revit_families)} families with tags")
        if families_without_tags:
            print(f"[Standards Audit] {len(families_without_tags)} families without tags (first 10):")
            for fam in families_without_tags[:10]:
                print(f"  - {fam}")

        if revit_families:
            print(f"[Standards Audit] Sample Revit tags (first 10): {list(revit_families.keys())[:10]}")

        # Compare: find missing and extra families
        airtable_tags = set(active_standards.keys())
        revit_tags = set(revit_families.keys())

        missing_from_revit = []
        for tag in (airtable_tags - revit_tags):
            at_data = active_standards[tag]
            missing_from_revit.append({
                "frame_tag": at_data["frame_tag"],
                "family_name": at_data["family_name"],
                "type_name": at_data["type_name"],
                "manufacturer": at_data["manufacturer"],
                "status": "Missing from Revit",
            })

        in_revit_not_standard = []
        retired_in_revit = []

        for tag in (revit_tags - airtable_tags):
            rv_data = revit_families[tag]
            # Check if this item exists in Airtable but is retired
            if tag in all_standards:
                at_data = all_standards[tag]
                retired_in_revit.append({
                    "frame_tag": rv_data["tag"],
                    "family_name": rv_data["family_name"],
                    "type_name": rv_data["type_name"],
                    "airtable_status": at_data["status"],
                    "status": "Retired in Airtable",
                })
            else:
                in_revit_not_standard.append({
                    "frame_tag": rv_data["tag"],
                    "family_name": rv_data["family_name"],
                    "type_name": rv_data["type_name"],
                    "status": "Not in Airtable",
                })

        in_both = []
        for tag in (airtable_tags & revit_tags):
            at_data = active_standards[tag]
            rv_data = revit_families[tag]
            in_both.append({
                "frame_tag": at_data["frame_tag"],
                "airtable_family": at_data["family_name"],
                "airtable_type": at_data["type_name"],
                "revit_family": rv_data["family_name"],
                "revit_type": rv_data["type_name"],
                "manufacturer": at_data["manufacturer"],
                "status": "Match",
            })

        return {
            "summary": {
                "total_airtable_standards": len(airtable_tags),
                "total_revit_families": len(revit_tags),
                "missing_from_revit": len(missing_from_revit),
                "not_in_standards": len(in_revit_not_standard),
                "retired_in_revit": len(retired_in_revit),
                "matching": len(in_both),
            },
            "missing_from_revit": sorted(missing_from_revit, key=lambda x: x["frame_tag"]),
            "not_in_standards": sorted(in_revit_not_standard, key=lambda x: x["frame_tag"]),
            "retired_in_revit": sorted(retired_in_revit, key=lambda x: x["frame_tag"]),
            "matching": sorted(in_both, key=lambda x: x["frame_tag"]),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")
