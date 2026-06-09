"""
SVF PackFile parser ported from forge-convert-utils (TypeScript).
Extracts fragment transforms (element XYZ positions) from FragmentList.pack.
Reference: https://github.com/petrbroz/forge-convert-utils
"""

import gzip
import math
import struct
import requests
import sqlite3
import tempfile
import os
from dataclasses import dataclass
from typing import Iterator, Optional
import aps_client as aps


# ── Binary stream reader ───────────────────────────────────────────────────

class InputStream:
    def __init__(self, data: bytes):
        self._buf = data
        self._off = 0

    @property
    def length(self):
        return len(self._buf)

    def seek(self, offset: int):
        self._off = offset

    def get_uint8(self) -> int:
        v = self._buf[self._off]
        self._off += 1
        return v

    def get_uint16(self) -> int:
        v = struct.unpack_from('<H', self._buf, self._off)[0]
        self._off += 2
        return v

    def get_uint32(self) -> int:
        v = struct.unpack_from('<I', self._buf, self._off)[0]
        self._off += 4
        return v

    def get_int32(self) -> int:
        v = struct.unpack_from('<i', self._buf, self._off)[0]
        self._off += 4
        return v

    def get_float32(self) -> float:
        v = struct.unpack_from('<f', self._buf, self._off)[0]
        self._off += 4
        return v

    def get_varint(self) -> int:
        val = 0
        shift = 0
        while True:
            byte = self._buf[self._off]
            self._off += 1
            val |= (byte & 0x7F) << shift
            shift += 7
            if not (byte & 0x80):
                break
        return val

    def get_string(self, length: int) -> str:
        s = self._buf[self._off:self._off + length].decode('utf-8', errors='replace')
        self._off += length
        return s

    def get_vector3(self):
        x = self.get_float32()
        y = self.get_float32()
        z = self.get_float32()
        return (x, y, z)

    def get_quaternion(self):
        x = self.get_float32()
        y = self.get_float32()
        z = self.get_float32()
        w = self.get_float32()
        return (x, y, z, w)


# ── PackFile reader ────────────────────────────────────────────────────────

class PackFileReader(InputStream):
    def __init__(self, data: bytes):
        # Decompress if gzipped
        if data[:2] == b'\x1f\x8b':
            data = gzip.decompress(data)
        super().__init__(data)

        # PackFile header format:
        # uint32: string length (e.g. 31 for "Autodesk.CloudPlatform.PackFile")
        # bytes[4..4+length): type string (no null terminator)
        # int32: version
        type_len = self.get_uint32()
        self._type = self.get_string(type_len)
        self._version = self.get_int32()

        self._entries = []
        self._types = []
        self._parse_contents()

    def _parse_contents(self):
        original_offset = self._off

        # Entry table and type table offsets are at the last 8 bytes
        self.seek(self.length - 8)
        entries_offset = self.get_uint32()
        types_offset = self.get_uint32()

        # Read entry offsets
        self.seek(entries_offset)
        count = self.get_varint()
        for _ in range(count):
            self._entries.append(self.get_uint32())

        # Read type descriptors
        self.seek(types_offset)
        count = self.get_varint()
        for _ in range(count):
            cls_len = self.get_varint()
            cls = self.get_string(cls_len)
            typ_len = self.get_varint()
            typ = self.get_string(typ_len)
            version = self.get_varint()
            self._types.append({'class': cls, 'type': typ, 'version': version})

        # Restore offset so sequential reading works
        self.seek(original_offset)

    def num_entries(self) -> int:
        return len(self._entries)

    def get_float64(self) -> float:
        v = struct.unpack_from('<d', self._buf, self._off)[0]
        self._off += 8
        return v

    def get_vector3d(self):
        x = self.get_float64()
        y = self.get_float64()
        z = self.get_float64()
        return (x, y, z)

    def seek_entry(self, index: int) -> Optional[dict]:
        if index >= len(self._entries):
            return None
        self.seek(self._entries[index])
        # Type index is stored as uint32, not varint
        type_index = self.get_uint32()
        if type_index >= len(self._types):
            return None
        return self._types[type_index]

    def get_transform(self):
        xform_type = self.get_uint8()
        if xform_type == 0:
            # Translation only (float64 x3)
            t = self.get_vector3d()
            return {'t': t}
        elif xform_type == 1:
            # Rotation (quaternion float32 x4) + translation (float64 x3)
            q = self.get_quaternion()
            t = self.get_vector3d()
            return {'q': q, 't': t, 's': (1, 1, 1)}
        elif xform_type == 2:
            # Uniform scale (float32) + rotation + translation (float64 x3)
            scale = self.get_float32()
            q = self.get_quaternion()
            t = self.get_vector3d()
            return {'q': q, 't': t, 's': (scale, scale, scale)}
        elif xform_type == 3:
            # Full 3x3 matrix (float32 x9) + translation (float64 x3)
            matrix = [self.get_float32() for _ in range(9)]
            t = self.get_vector3d()
            return {'matrix': matrix, 't': t}
        return None


# ── Fragment list parser ───────────────────────────────────────────────────

@dataclass
class Fragment:
    dbid: int
    transform: Optional[dict]
    bbox: list
    visible: bool


def parse_fragments(data: bytes) -> Iterator[Fragment]:
    pfr = PackFileReader(data)

    for i in range(pfr.num_entries()):
        entry_type = pfr.seek_entry(i)
        if not entry_type:
            continue

        flags = pfr.get_uint8()
        visible = bool(flags & 0x01)
        _material_id = pfr.get_varint()
        _geometry_id = pfr.get_varint()
        transform = pfr.get_transform()

        # bbox stored as float32 offsets relative to translation
        bbox_offset = [0.0, 0.0, 0.0]
        if entry_type['version'] > 3 and transform and 't' in transform:
            bbox_offset = list(transform['t'])

        bbox = []
        for j in range(6):
            bbox.append(pfr.get_float32() + bbox_offset[j % 3])

        dbid = pfr.get_varint()
        yield Fragment(dbid=dbid, transform=transform, bbox=bbox, visible=visible)


def get_translation(transform: Optional[dict]) -> tuple:
    """Extract XYZ translation from any transform type."""
    if not transform:
        return (0.0, 0.0, 0.0)
    if 't' in transform:
        return transform['t']
    return (0.0, 0.0, 0.0)


# ── Download helpers ───────────────────────────────────────────────────────

BASE_URL = "https://developer.api.autodesk.com"


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _download(token, model_urn, file_urn) -> bytes:
    encoded_model = aps.encode_urn(model_urn)
    encoded_file = requests.utils.quote(file_urn, safe="")
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded_model}/manifest/{encoded_file}",
        headers=_headers(token),
    )
    r.raise_for_status()
    return r.content


def get_manifest(token, urn) -> dict:
    encoded = aps.encode_urn(urn)
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/manifest",
        headers=_headers(token),
    )
    r.raise_for_status()
    return r.json()


def find_all_nodes(manifest) -> list:
    results = []
    def walk(node):
        if isinstance(node, dict):
            if 'urn' in node:
                results.append(node)
            for v in node.values():
                if isinstance(v, (dict, list)):
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(manifest)
    return results


def get_svf_base_and_fragment_urn(token, urn, view_name_hint="Floor View") -> Optional[str]:
    """Find the FragmentList.pack URN for a given view."""
    import zipfile, io, json as _json
    manifest = get_manifest(token, urn)
    nodes = find_all_nodes(manifest)

    # Find SVF files matching the view hint
    svf_nodes = [n for n in nodes if n.get('mime') == 'application/autodesk-svf'
                 and view_name_hint.lower() in n.get('urn', '').lower()]
    if not svf_nodes:
        svf_nodes = [n for n in nodes if n.get('mime') == 'application/autodesk-svf']
    if not svf_nodes:
        return None, None

    svf_node = svf_nodes[0]
    svf_base = '/'.join(svf_node['urn'].split('/')[:-1]) + '/'

    # Download SVF zip to find FragmentList.pack
    svf_data = _download(token, urn, svf_node['urn'])
    try:
        z = zipfile.ZipFile(io.BytesIO(svf_data))
        mf = _json.loads(z.read('manifest.json'))
        for asset in mf.get('assets', []):
            if 'FragmentList' in asset.get('id', '') or asset.get('type') == 'Autodesk.CloudPlatform.FragmentList':
                return svf_base, svf_base + asset['id']
    except Exception:
        pass
    return svf_base, None


# ── SDB property database helper ──────────────────────────────────────────

def download_sdb(token, urn) -> str:
    """Download model.sdb and return path to temp SQLite file."""
    manifest = get_manifest(token, urn)
    nodes = find_all_nodes(manifest)
    sdb_node = next((n for n in nodes if 'model.sdb' in n.get('urn', '')), None)
    if not sdb_node:
        raise ValueError("model.sdb not found in manifest")
    content = _download(token, urn, sdb_node['urn'])
    if content[:2] == b'\x1f\x8b':
        content = gzip.decompress(content)
    f = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
    f.write(content)
    f.close()
    return f.name


def query_sdb(db_path: str, sql: str, params=()) -> list:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def get_attr_id(db_path: str, name: str) -> Optional[int]:
    rows = query_sdb(db_path, "SELECT id FROM _objects_attr WHERE name=? LIMIT 1", (name,))
    return rows[0][0] if rows else None


def get_entity_value(db_path: str, entity_id: int, attr_id: int) -> Optional[str]:
    rows = query_sdb(db_path, """
        SELECT v.value FROM _objects_eav e JOIN _objects_val v ON e.value_id=v.id
        WHERE e.entity_id=? AND e.attribute_id=? LIMIT 1
    """, (entity_id, attr_id))
    return rows[0][0] if rows else None


def get_all_values(db_path: str, attr_id: int) -> dict:
    """Returns {entity_id: value} for all entities with this attribute."""
    rows = query_sdb(db_path, """
        SELECT e.entity_id, v.value FROM _objects_eav e JOIN _objects_val v ON e.value_id=v.id
        WHERE e.attribute_id=?
    """, (attr_id,))
    return {r[0]: r[1] for r in rows}


# ── Room boundary from SDB ─────────────────────────────────────────────────

def get_room_boundaries(db_path: str) -> list:
    """
    Extract room objects from SDB with their bounding box data.
    Returns list of dicts: {entity_id, name, number, min_x, min_y, max_x, max_y}
    """
    # Get attribute IDs
    conn = sqlite3.connect(db_path)

    def get_id(name):
        r = conn.execute("SELECT id FROM _objects_attr WHERE name=? LIMIT 1", (name,)).fetchone()
        return r[0] if r else None

    # Room-specific attributes
    cat_id = get_id('Category')
    name_id = get_id('Name')     # Room Name (instance param)
    num_id = get_id('Number')    # Room Number
    type_name_id = get_id('Type Name')

    if not (cat_id and name_id):
        conn.close()
        return []

    # Find Rooms category entities
    room_entities = conn.execute("""
        SELECT e.entity_id FROM _objects_eav e JOIN _objects_val v ON e.value_id=v.id
        WHERE e.attribute_id=? AND v.value='Rooms'
    """, (cat_id,)).fetchall()

    rooms = []
    for (entity_id,) in room_entities:
        def val(attr_id):
            if not attr_id:
                return ''
            r = conn.execute("""
                SELECT v.value FROM _objects_eav e JOIN _objects_val v ON e.value_id=v.id
                WHERE e.entity_id=? AND e.attribute_id=? LIMIT 1
            """, (entity_id, attr_id)).fetchone()
            return r[0] if r else ''

        rooms.append({
            'entity_id': entity_id,
            'name': val(name_id),
            'number': val(num_id),
        })

    conn.close()
    return rooms


# ── Point-in-polygon (2D) ──────────────────────────────────────────────────

def point_in_bbox(px: float, py: float, bbox: list) -> bool:
    """Check if point is within a fragment's 2D bounding box."""
    if len(bbox) < 4:
        return False
    min_x, min_y = bbox[0], bbox[1]
    max_x, max_y = bbox[3], bbox[4]
    return min_x <= px <= max_x and min_y <= py <= max_y
