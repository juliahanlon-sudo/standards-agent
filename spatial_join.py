"""
Spatial join: assigns furniture seat counts to rooms using SVF fragment positions.
Downloads FragmentList.pack from both furniture and interior models,
then does a 3D point-in-bbox assignment.
"""

import math
import sqlite3
import os
import tempfile
import gzip
import json
import zipfile
import requests

import aps_client as aps
import svf_parser as svf

BASE_URL = "https://developer.api.autodesk.com"
EPS = 0.5  # positional tolerance in feet


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _get_svf_frag_urn(token, urn, view_hint):
    """Find FragmentList.pack URN for the given view hint."""
    encoded = aps.encode_urn(urn)
    manifest = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/manifest",
        headers=_headers(token),
    ).json()
    nodes = svf.find_all_nodes(manifest)
    svf_nodes = [n for n in nodes
                 if n.get("mime") == "application/autodesk-svf"
                 and "pdf" not in n.get("urn", "")
                 and view_hint.lower() in n.get("urn", "").lower()]
    if not svf_nodes:
        svf_nodes = [n for n in nodes
                     if n.get("mime") == "application/autodesk-svf"
                     and "pdf" not in n.get("urn", "")]
    if not svf_nodes:
        return None, None
    svf_node = svf_nodes[0]
    svf_base = "/".join(svf_node["urn"].split("/")[:-1]) + "/"
    svf_data = svf._download(token, urn, svf_node["urn"])
    try:
        z = zipfile.ZipFile(__import__("io").BytesIO(svf_data))
        mf = json.loads(z.read("manifest.json"))
        for asset in mf.get("assets", []):
            if "FragmentList" in asset.get("id", "") or \
               asset.get("type") == "Autodesk.CloudPlatform.FragmentList":
                return svf_base, svf_base + asset["id"]
    except Exception:
        pass
    return svf_base, None


def get_room_seats(token, furniture_urn, interior_urn):
    """
    Downloads SVF fragment data from both models and performs a spatial join.
    Returns: {room_name: total_seat_count}
    """
    # ── Furniture: positions + seat counts ────────────────────────────────
    _, frag_urn = _get_svf_frag_urn(token, furniture_urn, "New Construction")
    if not frag_urn:
        return {}

    furn_frags = list(svf.parse_fragments(svf._download(token, furniture_urn, frag_urn)))

    furn_sdb_path = svf.download_sdb(token, furniture_urn)
    conn_f = sqlite3.connect(furn_sdb_path)
    try:
        seat_attr = conn_f.execute(
            "SELECT id FROM _objects_attr WHERE name='SFDC_Seat Count' LIMIT 1"
        ).fetchone()
        iof_attr = conn_f.execute(
            "SELECT id FROM _objects_attr WHERE name='instanceof_objid' LIMIT 1"
        ).fetchone()
        if not seat_attr or not iof_attr:
            return {}

        type_seats = {
            r[0]: int(float(r[1]))
            for r in conn_f.execute(
                f"SELECT e.entity_id, v.value FROM _objects_eav e "
                f"JOIN _objects_val v ON e.value_id=v.id "
                f"WHERE e.attribute_id={seat_attr[0]} AND CAST(v.value AS REAL)>0"
            ).fetchall()
        }
        instance_to_type = {
            r[0]: int(r[1])
            for r in conn_f.execute(
                f"SELECT e.entity_id, v.value FROM _objects_eav e "
                f"JOIN _objects_val v ON e.value_id=v.id "
                f"WHERE e.attribute_id={iof_attr[0]}"
            ).fetchall()
        }
        # Workstation row count (#Workstations Rows) for desk count
        # This lives on the PARENT assembly entity (e.g. entity 8174 = WK benched workstation)
        # The parent's children appear in fragments, so we need child→parent desk count
        ws_rows_attr = conn_f.execute(
            "SELECT id FROM _objects_attr WHERE name='#Workstations Rows' LIMIT 1"
        ).fetchone()
        parent_attr = conn_f.execute(
            "SELECT id FROM _objects_attr WHERE name='parent' LIMIT 1"
        ).fetchone()

        # parent_entity → desk_count (only parents with #Workstations Rows > 0)
        parent_desk_count = {}
        if ws_rows_attr:
            parent_desk_count = {
                r[0]: int(float(r[1]))
                for r in conn_f.execute(
                    f"SELECT e.entity_id, v.value FROM _objects_eav e "
                    f"JOIN _objects_val v ON e.value_id=v.id "
                    f"WHERE e.attribute_id={ws_rows_attr[0]} AND CAST(v.value AS REAL)>0"
                ).fetchall()
            }

        # child_entity → parent_entity_id (for deduplication)
        child_to_parent_id = {}
        if parent_attr and parent_desk_count:
            for child_id, parent_id in conn_f.execute(
                f"SELECT e.entity_id, CAST(v.value AS INTEGER) FROM _objects_eav e "
                f"JOIN _objects_val v ON e.value_id=v.id "
                f"WHERE e.attribute_id={parent_attr[0]}"
            ).fetchall():
                if parent_id in parent_desk_count:
                    child_to_parent_id[child_id] = parent_id

    finally:
        conn_f.close()
        os.unlink(furn_sdb_path)

    instance_seats = {
        inst: type_seats[instance_to_type[inst]]
        for inst in instance_to_type
        if instance_to_type[inst] in type_seats
    }

    # ── Interior: room bboxes ──────────────────────────────────────────────
    _, int_frag_urn = _get_svf_frag_urn(token, interior_urn, "New Construction")
    if not int_frag_urn:
        return {}

    int_frags = list(svf.parse_fragments(svf._download(token, interior_urn, int_frag_urn)))

    int_sdb_path = svf.download_sdb(token, interior_urn)
    conn_i = sqlite3.connect(int_sdb_path)
    try:
        room_ents = {
            r[0] for r in conn_i.execute(
                "SELECT e.entity_id FROM _objects_eav e "
                "JOIN _objects_val v ON e.value_id=v.id "
                "WHERE e.attribute_id=13 AND v.value='Revit Rooms'"
            ).fetchall()
        }
        name_attr = conn_i.execute(
            "SELECT id FROM _objects_attr WHERE name='Name' AND category='Identity Data' LIMIT 1"
        ).fetchone()
        if not name_attr:
            name_attr = conn_i.execute(
                "SELECT id FROM _objects_attr WHERE name='Name' LIMIT 1"
            ).fetchone()
        num_attr = conn_i.execute(
            "SELECT id FROM _objects_attr WHERE name='Number' AND category='Identity Data' LIMIT 1"
        ).fetchone()
        if not name_attr:
            return []

        room_names = {
            r[0]: r[1]
            for r in conn_i.execute(
                f"SELECT e.entity_id, v.value FROM _objects_eav e "
                f"JOIN _objects_val v ON e.value_id=v.id "
                f"WHERE e.attribute_id={name_attr[0]}"
            ).fetchall()
        }
        room_numbers = {}
        if num_attr:
            room_numbers = {
                r[0]: r[1]
                for r in conn_i.execute(
                    f"SELECT e.entity_id, v.value FROM _objects_eav e "
                    f"JOIN _objects_val v ON e.value_id=v.id "
                    f"WHERE e.attribute_id={num_attr[0]}"
                ).fetchall()
            }
    finally:
        conn_i.close()
        os.unlink(int_sdb_path)

    def parse_level(room_number: str) -> str:
        """Extract floor level from room number like '05-C01' → 'Level 05'."""
        if not room_number:
            return ""
        parts = room_number.split("-")
        if parts:
            return f"Level {parts[0]}"
        return ""

    # Build room fragment list sorted by area (smallest first for best specificity)
    room_fragments = []
    for f in int_frags:
        if f.dbid not in room_ents:
            continue
        bbox = f.bbox
        if len(bbox) < 6 or any(math.isnan(v) or abs(v) > 1e8 for v in bbox):
            continue
        name = room_names.get(f.dbid, "")
        if not name:
            continue
        area = (bbox[3] - bbox[0]) * (bbox[4] - bbox[1])
        level = parse_level(room_numbers.get(f.dbid, ""))
        room_fragments.append((area, name, bbox, level))

    room_fragments.sort(key=lambda x: x[0])

    # ── Spatial join ───────────────────────────────────────────────────────
    # Returns list of {room_name, raw_seats, desk_count, level}
    # Deduplicate: each furniture instance can have multiple fragments (geometry pieces)
    # Only count each instance (dbid) once
    counted_parent_ids = set()
    counted_instance_dbids = set()
    assignments = []
    for f in furn_frags:
        has_seats = f.dbid in instance_seats
        has_desks = f.dbid in child_to_parent_id or f.dbid in parent_desk_count
        if (not has_seats and not has_desks) or not f.transform:
            continue
        # Skip duplicate instances (already counted via another fragment)
        if has_seats and f.dbid in counted_instance_dbids:
            continue
        tx, ty, tz = svf.get_translation(f.transform)
        if math.isnan(tx) or math.isnan(ty) or math.isnan(tz):
            continue
        seats = instance_seats.get(f.dbid, 0)

        # Desk count: deduplicate by parent ID
        desks = 0
        if f.dbid in child_to_parent_id:
            parent_id = child_to_parent_id[f.dbid]
            if parent_id not in counted_parent_ids:
                desks = parent_desk_count[parent_id]
                counted_parent_ids.add(parent_id)
        elif f.dbid in parent_desk_count and f.dbid not in counted_parent_ids:
            desks = parent_desk_count[f.dbid]
            counted_parent_ids.add(f.dbid)

        if seats == 0 and desks == 0:
            continue

        for area, room_name, bbox, level in room_fragments:
            if (bbox[0] - EPS <= tx <= bbox[3] + EPS and
                    bbox[1] - EPS <= ty <= bbox[4] + EPS and
                    bbox[2] - EPS <= tz <= bbox[5] + EPS):
                assignments.append({
                    "room_name": room_name,
                    "raw_seats": seats,
                    "desk_count": desks,
                    "level": level,
                    "dbid": f.dbid,
                })
                if has_seats:
                    counted_instance_dbids.add(f.dbid)
                break

    return assignments


def get_door_rooms(token, door_urn, interior_urn):
    """
    Calculate which rooms each door connects to using spatial analysis.
    Downloads SVF fragment data from both models and matches door positions to room boundaries.
    Returns: dict mapping door_dbid → {"from_room": str, "to_room": str, "from_number": str, "to_number": str}
    """
    print(f"[DOOR_SPATIAL] Starting door-room spatial analysis")
    # ── Interior: room bboxes ──────────────────────────────────────────────
    _, int_frag_urn = _get_svf_frag_urn(token, interior_urn, "New Construction")
    if not int_frag_urn:
        print(f"[DOOR_SPATIAL] No interior fragment URN found")
        return {}

    int_frags = list(svf.parse_fragments(svf._download(token, interior_urn, int_frag_urn)))

    int_sdb_path = svf.download_sdb(token, interior_urn)
    conn_i = sqlite3.connect(int_sdb_path)
    try:
        room_ents = {
            r[0] for r in conn_i.execute(
                "SELECT e.entity_id FROM _objects_eav e "
                "JOIN _objects_val v ON e.value_id=v.id "
                "WHERE e.attribute_id=13 AND v.value='Revit Rooms'"
            ).fetchall()
        }
        name_attr = conn_i.execute(
            "SELECT id FROM _objects_attr WHERE name='Name' AND category='Identity Data' LIMIT 1"
        ).fetchone()
        if not name_attr:
            name_attr = conn_i.execute(
                "SELECT id FROM _objects_attr WHERE name='Name' LIMIT 1"
            ).fetchone()
        num_attr = conn_i.execute(
            "SELECT id FROM _objects_attr WHERE name='Number' AND category='Identity Data' LIMIT 1"
        ).fetchone()
        if not name_attr:
            return {}

        room_names = {
            r[0]: r[1]
            for r in conn_i.execute(
                f"SELECT e.entity_id, v.value FROM _objects_eav e "
                f"JOIN _objects_val v ON e.value_id=v.id "
                f"WHERE e.attribute_id={name_attr[0]}"
            ).fetchall()
        }
        room_numbers = {}
        if num_attr:
            room_numbers = {
                r[0]: r[1]
                for r in conn_i.execute(
                    f"SELECT e.entity_id, v.value FROM _objects_eav e "
                    f"JOIN _objects_val v ON e.value_id=v.id "
                    f"WHERE e.attribute_id={num_attr[0]}"
                ).fetchall()
            }
    finally:
        conn_i.close()
        os.unlink(int_sdb_path)

    # Build room fragment list with bboxes
    room_bboxes = []
    for f in int_frags:
        if f.dbid not in room_ents:
            continue
        bbox = f.bbox
        if len(bbox) < 6 or any(math.isnan(v) or abs(v) > 1e8 for v in bbox):
            continue
        name = room_names.get(f.dbid, "")
        if not name:
            continue
        number = room_numbers.get(f.dbid, "")
        room_bboxes.append({
            "name": name,
            "number": number,
            "bbox": bbox,
        })
    print(f"[DOOR_SPATIAL] Found {len(room_bboxes)} rooms")

    # ── Door: positions ────────────────────────────────────────────────────
    _, door_frag_urn = _get_svf_frag_urn(token, door_urn, "New Construction")
    if not door_frag_urn:
        print(f"[DOOR_SPATIAL] No door fragment URN found")
        return {}

    door_frags = list(svf.parse_fragments(svf._download(token, door_urn, door_frag_urn)))
    print(f"[DOOR_SPATIAL] Loaded {len(door_frags)} door fragments")

    door_sdb_path = svf.download_sdb(token, door_urn)
    conn_d = sqlite3.connect(door_sdb_path)
    try:
        door_ents = {
            r[0] for r in conn_d.execute(
                "SELECT e.entity_id FROM _objects_eav e "
                "JOIN _objects_val v ON e.value_id=v.id "
                "WHERE e.attribute_id=13 AND v.value='Revit Doors'"
            ).fetchall()
        }
        print(f"[DOOR_SPATIAL] Found {len(door_ents)} door entities in database")

        # Get door marks and external IDs to use for matching
        mark_attr = conn_d.execute(
            "SELECT id FROM _objects_attr WHERE name='Mark' LIMIT 1"
        ).fetchone()
        door_marks = {}
        if mark_attr:
            door_marks = {
                r[0]: r[1]
                for r in conn_d.execute(
                    f"SELECT e.entity_id, v.value FROM _objects_eav e "
                    f"JOIN _objects_val v ON e.value_id=v.id "
                    f"WHERE e.attribute_id={mark_attr[0]}"
                ).fetchall()
            }
            print(f"[DOOR_SPATIAL] Found marks for {len(door_marks)} doors")

        # Get external IDs as fallback for doors without marks
        ext_id_attr = conn_d.execute(
            "SELECT id FROM _objects_attr WHERE name='externalId' LIMIT 1"
        ).fetchone()
        door_external_ids = {}
        if ext_id_attr:
            door_external_ids = {
                r[0]: r[1]
                for r in conn_d.execute(
                    f"SELECT e.entity_id, v.value FROM _objects_eav e "
                    f"JOIN _objects_val v ON e.value_id=v.id "
                    f"WHERE e.attribute_id={ext_id_attr[0]}"
                ).fetchall()
            }
            print(f"[DOOR_SPATIAL] Found externalIds for {len(door_external_ids)} doors")
    finally:
        conn_d.close()
        os.unlink(door_sdb_path)

    # ── Spatial join: door center point → rooms ────────────────────────────
    # For each door, find which room(s) its center point is near
    # Doors typically sit on the boundary between two rooms
    door_assignments = {}
    unmatched_doors = 0
    skipped_not_door = 0
    skipped_no_transform = 0
    skipped_bad_position = 0

    for f in door_frags:
        if f.dbid not in door_ents:
            skipped_not_door += 1
            continue
        if not f.transform:
            skipped_no_transform += 1
            continue

        tx, ty, tz = svf.get_translation(f.transform)
        if math.isnan(tx) or math.isnan(ty) or math.isnan(tz):
            skipped_bad_position += 1
            continue

        # Find all rooms whose bboxes contain or are very close to the door position
        nearby_rooms = []
        for room in room_bboxes:
            bbox = room["bbox"]
            # Check if door position is inside room bbox (with tolerance)
            tolerance = 2.0  # feet - doors sit on walls, so need larger tolerance
            if (bbox[0] - tolerance <= tx <= bbox[3] + tolerance and
                bbox[1] - tolerance <= ty <= bbox[4] + tolerance and
                bbox[2] - tolerance <= tz <= bbox[5] + tolerance):
                # Calculate distance from door to room center
                room_cx = (bbox[0] + bbox[3]) / 2
                room_cy = (bbox[1] + bbox[4]) / 2
                room_cz = (bbox[2] + bbox[5]) / 2
                dist = math.sqrt((tx - room_cx)**2 + (ty - room_cy)**2 + (tz - room_cz)**2)
                nearby_rooms.append((dist, room))

        # Sort by distance and take up to 2 closest rooms
        nearby_rooms.sort(key=lambda x: x[0])
        room_matches = [r[1] for r in nearby_rooms[:2]]

        if not room_matches:
            unmatched_doors += 1

        if len(room_matches) >= 2:
            door_assignments[f.dbid] = {
                "from_room": room_matches[0]["name"],
                "to_room": room_matches[1]["name"],
                "from_number": room_matches[0]["number"],
                "to_number": room_matches[1]["number"],
                "mark": door_marks.get(f.dbid, ""),
                "external_id": door_external_ids.get(f.dbid, ""),
                "room_count": 2,
            }
        elif len(room_matches) == 1:
            door_assignments[f.dbid] = {
                "from_room": room_matches[0]["name"],
                "to_room": "",
                "from_number": room_matches[0]["number"],
                "to_number": "",
                "mark": door_marks.get(f.dbid, ""),
                "external_id": door_external_ids.get(f.dbid, ""),
                "room_count": 1,
            }

    doors_with_1_room = sum(1 for d in door_assignments.values() if d.get("room_count") == 1)
    doors_with_2_rooms = sum(1 for d in door_assignments.values() if d.get("room_count") == 2)
    print(f"[DOOR_SPATIAL] Assigned {len(door_assignments)} doors to rooms")
    print(f"[DOOR_SPATIAL] {doors_with_2_rooms} doors have 2 rooms, {doors_with_1_room} doors have 1 room")
    print(f"[DOOR_SPATIAL] {unmatched_doors} doors had no nearby rooms (exterior/corridor doors)")
    print(f"[DOOR_SPATIAL] Skipped: {skipped_not_door} not in door category, {skipped_no_transform} no transform, {skipped_bad_position} bad position")
    return door_assignments
