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
            "SELECT id FROM _objects_attr WHERE name='Name' LIMIT 1"
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
    finally:
        conn_i.close()
        os.unlink(int_sdb_path)

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
        room_fragments.append((area, name, bbox))

    room_fragments.sort(key=lambda x: x[0])

    # ── Spatial join ───────────────────────────────────────────────────────
    room_seats = {}
    for f in furn_frags:
        if f.dbid not in instance_seats or not f.transform:
            continue
        tx, ty, tz = svf.get_translation(f.transform)
        if math.isnan(tx) or math.isnan(ty) or math.isnan(tz):
            continue
        seats = instance_seats[f.dbid]

        for area, room_name, bbox in room_fragments:
            if (bbox[0] - EPS <= tx <= bbox[3] + EPS and
                    bbox[1] - EPS <= ty <= bbox[4] + EPS and
                    bbox[2] - EPS <= tz <= bbox[5] + EPS):
                room_seats[room_name] = room_seats.get(room_name, 0) + seats
                break

    return room_seats
