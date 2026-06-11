"""
Capacity calculation engine.
Space type directory is embedded directly from the Google Sheet — no file upload needed.
"""

from rapidfuzz import fuzz, process as rfuzz_process

# ── Space type directory ──────────────────────────────────────────────────────
# Format: (serraview_name, [arch_name_variants], multiplier, l3_section)
# Multiplier: "TRUE - 1" = 1.0, "TRUE - .75" = 0.75, "TRUE - .50" = 0.5, blank/other = 0

RAW_DIRECTORY = [
    # L3 - Building Specialty
    ("AV Control Room",           ["AV Room"],                                                          0,    "L3 - Building Specialty"),
    ("Badge Room",                ["Badge Room", "IT & Security - Badge Room"],                        0,    "L3 - Building Specialty"),
    ("Catering",                  ["Customer - Catering", "SIC - Catering"],                           0,    "L3 - Building Specialty"),
    ("Childcare",                 ["Childcare"],                                                        0,    "L3 - Building Specialty"),
    ("Command Center",            ["Command Center", "SIC - Team Area"],                               1.0,  "L3 - Building Specialty"),
    ("Critical Incident Center",  ["Critical Incident Center"],                                        1.0,  "L3 - Building Specialty"),
    ("CSIRT",                     ["CSIRT"],                                                            1.0,  "L3 - Building Specialty"),
    ("Fitness Center",            ["Fitness Center"],                                                   0,    "L3 - Building Specialty"),
    ("Game Room",                 ["Ping Pong", "Billiards"],                                          0,    "L3 - Building Specialty"),
    ("GO Center",                 ["GO Center"],                                                        1.0,  "L3 - Building Specialty"),
    ("IT Provisioning",           ["IT Provisioning"],                                                  0,    "L3 - Building Specialty"),
    ("Lab",                       ["Ignite Maker's Lab", "Ignite Lab", "Lightning Performance Lab", "Security Support Area"], 0, "L3 - Building Specialty"),
    ("Media Room",                ["Media Room", "AI - Recording Studio"],                             0,    "L3 - Building Specialty"),
    ("Medical Room",              ["Medical Room"],                                                     0,    "L3 - Building Specialty"),
    ("Mobility Lab",              ["Mobility Lab"],                                                     0,    "L3 - Building Specialty"),
    ("Outdoor / Terrace",         ["Terrace", "Balcony"],                                              0,    "L3 - Building Specialty"),
    ("Pantry",                    ["Pantry", "Ohana - Pantry (BOH)"],                                  0,    "L3 - Building Specialty"),
    ("Site Reliability Engineering", ["SRE"],                                                          1.0,  "L3 - Building Specialty"),
    ("Staging/Green Room",        ["Green Room"],                                                       0,    "L3 - Building Specialty"),
    ("Techforce",                 ["Techforce"],                                                        0,    "L3 - Building Specialty"),
    ("Techforce Lab",             ["Techforce Lab"],                                                    0,    "L3 - Building Specialty"),
    # L3 - Hospitality
    ("Barista Bar",               ["Barista Bar", "Customer - Barista Bar"],                           0,    "L3 - Hospitality"),
    ("Cafe",                      ["Cafe", "Ohana - Cafe", "Customer - Cafe", "SIC - Cafe"],           0,    "L3 - Hospitality"),
    ("Cafe Seating",              ["Cafe Seating", "Ohana - Cafe Seating", "Customer - Cafe Seating"], 0.5, "L3 - Hospitality"),
    ("Customer Center",           ["Customer Center"],                                                  0,    "L3 - Hospitality"),
    ("Customer Collaboration",    ["Customer Collaboration", "AI - Customer Studio"],                  0,    "L3 - Hospitality"),
    ("Customer Executive Lounge", ["Customer Executive Lounge"],                                       0,    "L3 - Hospitality"),
    ("Customer Lounge",           ["Customer Lounge"],                                                  0,    "L3 - Hospitality"),
    ("Customer Meeting Room",     ["Customer Meeting Room"],                                            1.0,  "L3 - Hospitality"),
    ("Customer Reception",        ["Customer Reception"],                                               0,    "L3 - Hospitality"),
    ("Customer Social Lounge",    ["Customer Social Lounge"],                                           0,    "L3 - Hospitality"),
    ("Customer Town Hall",        ["Customer Town Hall"],                                               0,    "L3 - Hospitality"),
    ("Executive Dining",          ["Executive Dining"],                                                 0,    "L3 - Hospitality"),
    ("Ohana Floor",               ["Ohana Floor"],                                                      0,    "L3 - Hospitality"),
    ("Ohana Lounge",              ["Ohana Lounge"],                                                     0,    "L3 - Hospitality"),
    ("Town Hall",                 ["Town Hall", "Customer - Town Hall", "Ohana - Town Hall"],          0,    "L3 - Hospitality"),
    # L3 - M&E
    ("Building Operations",       ["Building Operations"],                                              0,    "L3 - M&E"),
    ("Building Storage",          ["Storage"],                                                          0,    "L3 - M&E"),
    ("Copy/Print",                ["Copy/Print", "Print"],                                              0,    "L3 - M&E"),
    ("Copy/Print/Fax",            ["Copy/Print/Fax"],                                                   0,    "L3 - M&E"),
    ("Data Center",               ["Data Center"],                                                      0,    "L3 - M&E"),
    ("Electrical",                ["Electrical"],                                                       0,    "L3 - M&E"),
    ("IT",                        ["IT", "IT Room", "IT Closet"],                                      0,    "L3 - M&E"),
    ("Janitor",                   ["Janitor", "Janitorial"],                                            0,    "L3 - M&E"),
    ("Loading Dock",              ["Loading Dock"],                                                     0,    "L3 - M&E"),
    ("Mail Room",                 ["Mail Room", "Shipping and Receiving"],                              0,    "L3 - M&E"),
    ("Mechanical",                ["Mechanical"],                                                       0,    "L3 - M&E"),
    ("Security",                  ["Security"],                                                         0,    "L3 - M&E"),
    ("Telecom",                   ["Telecom"],                                                          0,    "L3 - M&E"),
    # L3 - Enclosed Collaboration
    ("Conference Room (Aloha)",   ["Aloha Conference Room"],                                            0,    "L3 - Enclosed Collaboration"),
    ("Conference Room (L)",       ["Conference Room (L)"],                                              0,    "L3 - Enclosed Collaboration"),
    ("Conference Room (M)",       ["Conference Room (M)"],                                              0,    "L3 - Enclosed Collaboration"),
    ("Conference Room (XL)",      ["Conference Room (XL)", "Conference Room (XL) - Restricted"],      0.75, "L3 - Enclosed Collaboration"),
    ("Focus Pod",                 ["Focus Pod"],                                                        0,    "L3 - Enclosed Collaboration"),
    ("Huddle Room",               ["Huddle Room", "Huddle", "SIC - Huddle Room"],                      0,    "L3 - Enclosed Collaboration"),
    ("Meeting Pod",               ["Meeting Pod (M)", "Meeting Pod (S)", "Meeting Pod (L)"],           0,    "L3 - Enclosed Collaboration"),
    ("Phone Room",                ["Phone Room", "Phone Room (AV)"],                                   0,    "L3 - Enclosed Collaboration"),
    ("Phone Room (Micro)",        ["Phone Room (Micro)", "Micro Phone Room"],                          0,    "L3 - Enclosed Collaboration"),
    ("Phone Room (AV)",           ["Phone Room (AV)"],                                                  0,    "L3 - Enclosed Collaboration"),
    # L3 - Individual Work
    ("Desk: Standard",            ["Open Office", "OPEN OFFICE", "Workstation Area", "Open Plan"],     1.0,  "L3 - Individual Work"),
    ("Library",                   ["Library"],                                                          1.0,  "L3 - Individual Work"),
    ("Private Office",            ["Private Office"],                                                   1.0,  "L3 - Individual Work"),
    ("Touchdown Seat",            ["Open Office", "Focus Nook"],                                       1.0,  "L3 - Individual Work"),
    ("Work Room",                 ["Work Room", "Puppyforce", "Design Studio"],                        1.0,  "L3 - Individual Work"),
    # L3 - Open Collaboration
    ("Open Collaboration",        ["Open Collaboration", "OPEN COLLABORATION"],                        0.5,  "L3 - Open Collaboration"),
    ("Booth",                     ["Banquette"],                                                        0.5,  "L3 - Open Collaboration"),
    ("Cafe Collaboration Table",  ["Cafe Collaboration Table"],                                         0.5,  "L3 - Open Collaboration"),
    ("Collaboration Space",       ["AI - Collaboration Cafe Area", "AI - Collaboration Zone",
                                   "AI - Collaborative Work Area", "AI - Expert Zone", "AI - Exploration Zone",
                                   "AI - Work Zone", "Open Air Meeting", "Open Huddle", "Pilot Seat",
                                   "Social Lounge Extension", "Whiteboarding"],                        0.5,  "L3 - Open Collaboration"),
    ("Community Table",           ["Community Table", "Library Table"],                                 0.5,  "L3 - Open Collaboration"),
    ("Project Bay",               ["Project Bay"],                                                      0.5,  "L3 - Open Collaboration"),
    ("Soft Seating",              ["Soft Seating"],                                                    0,    "L3 - Open Collaboration"),
    # L3 - Workspace Specialty
    ("Catering Pantry",           ["Catering Pantry", "SIC - Pantry"],                                 0,    "L3 - Workspace Specialty"),
    ("Cubbies",                   ["Cubbies", "Cubby Storage", "Cubby"],                               0,    "L3 - Workspace Specialty"),
    ("Flex Room",                 ["Flex Room", "Flex Room - Living", "Flex Room - Library", "SIC - Flex Room"], 0, "L3 - Workspace Specialty"),
    ("Mindfulness",               ["Mindfulness"],                                                      0,    "L3 - Workspace Specialty"),
    ("Multifaith Room",           ["Multifaith Room"],                                                  0,    "L3 - Workspace Specialty"),
    ("Parent's Room",             ["Parent's Room"],                                                    0,    "L3 - Workspace Specialty"),
    ("Reception",                 ["Reception", "SIC - Reception"],                                    0,    "L3 - Workspace Specialty"),
    ("Reception Lounge",          ["Reception Lounge"],                                                 0,    "L3 - Workspace Specialty"),
    ("Reflection Room",           ["Reflection Room"],                                                  0,    "L3 - Workspace Specialty"),
    ("Social Lounge",             ["Social Lounge", "Ohana - Social Lounge",
                                   "Barista Bar Social Lounge", "Customer - Social Lounge"],           0,    "L3 - Workspace Specialty"),
    ("Treadmill Desk",            ["Treadmill Desk"],                                                   0,    "L3 - Workspace Specialty"),
    ("Water Point",               ["Water Point", "Tea Point"],                                        0,    "L3 - Workspace Specialty"),
    ("Webinar Room",              ["Webinar Room", "Production Room"],                                  0,    "L3 - Workspace Specialty"),
    ("Wellness Room",             ["Wellness Room", "Camp B-Well Room"],                               0,    "L3 - Workspace Specialty"),
]

THRESHOLD = 80


def get_category(serraview_name: str, l3_section: str, multiplier: float) -> str:
    if l3_section == "L3 - Individual Work":
        return "IW"
    if l3_section == "L3 - Open Collaboration":
        return "Open Collab"
    if multiplier == 0.75:
        return "Amenity"
    if l3_section == "L3 - Enclosed Collaboration":
        return "Enclosed Collab"
    if l3_section == "L3 - Workspace Specialty":
        return "Workspace Specialty"
    if l3_section == "L3 - Hospitality":
        return "Hospitality"
    if l3_section == "L3 - Building Specialty":
        return "Building Specialty"
    if l3_section in ("L3 - M&E", "L2 - Support", "L1 - Core"):
        return "Support"
    return "Unmatched"


def match_room(room_name: str):
    """Match a room name against all architecture name variants. Returns best match or None."""
    if not room_name or not room_name.strip():
        return None

    name_lower = room_name.strip().lower()
    best_score = 0
    best_entry = None
    best_variant = None

    for serraview_name, variants, multiplier, l3_section in RAW_DIRECTORY:
        for variant in variants:
            variant_lower = variant.strip().lower()
            # Exact case-insensitive match scores 100
            if name_lower == variant_lower:
                score = 100
            else:
                score = fuzz.WRatio(room_name.strip(), variant.strip())
            if score > best_score:
                best_score = score
                best_entry = (serraview_name, multiplier, l3_section)
                best_variant = variant

    if best_score >= THRESHOLD and best_entry:
        serraview_name, multiplier, l3_section = best_entry
        return {
            "serraview_name": serraview_name,
            "matched_variant": best_variant,
            "multiplier": multiplier,
            "l3_section": l3_section,
            "category": get_category(serraview_name, l3_section, multiplier),
            "score": best_score,
        }
    return None


CATEGORY_ORDER = ["IW", "Open Collab", "Enclosed Collab", "Workspace Specialty", "Hospitality", "Building Specialty", "Support", "Unmatched"]


def calculate_capacity(furniture_items: list[dict]) -> dict:
    """
    Groups furniture items by room, applies space type multipliers,
    returns aggregated breakdown with one row per room.
    """
    from collections import defaultdict

    iw_seats = 0.0
    open_collab_seats = 0.0
    amenity_seats = 0.0
    total_desks = 0
    cubby_total = 0
    seen_cubby_keys = set()  # deduplicate cubby instances

    # Group by (room_name, level) — one breakdown row per room per level
    room_groups = defaultdict(lambda: {
        "raw_seats": 0, "weighted": 0.0, "cat": None,
        "serraview_name": "", "matched_variant": "", "multiplier": 0,
    })

    for item in furniture_items:
        room_name = item.get("room_name", "")
        raw_seats = float(item.get("raw_seats", 0) or 0)
        level = item.get("level", "")
        desk_count = int(item.get("desk_count", 0) or 0)
        total_desks += desk_count

        match = match_room(room_name)
        if not match:
            continue

        if match["serraview_name"] == "Cubbies":
            cubby_total += int(raw_seats)
            continue

        weighted = raw_seats * match["multiplier"]
        cat = match["category"]
        key = (room_name, level)

        room_groups[key]["raw_seats"] += int(raw_seats)
        room_groups[key]["weighted"] += weighted
        room_groups[key]["cat"] = cat
        room_groups[key]["serraview_name"] = match["serraview_name"]
        room_groups[key]["matched_variant"] = match["matched_variant"]
        room_groups[key]["multiplier"] = match["multiplier"]

        if cat == "IW":
            iw_seats += weighted
        elif cat == "Open Collab":
            open_collab_seats += weighted
        elif cat == "Amenity":
            amenity_seats += weighted

    breakdown = []
    for (room_name, level), grp in room_groups.items():
        cat = grp["cat"] or "Other"
        cat_order = CATEGORY_ORDER.index(cat) if cat in CATEGORY_ORDER else 99
        breakdown.append({
            "Room Name": room_name,
            "Level": level,
            "Raw Seats": str(grp["raw_seats"]),
            "Multiplier": str(grp["multiplier"]),
            "Weighted Seats": str(round(grp["weighted"], 1)),
            "Category": cat,
            "Matched Architecture Name": grp["matched_variant"],
            "_cat_order": cat_order,
        })

    breakdown.sort(key=lambda x: (x.get("Level", ""), x["_cat_order"], x["Room Name"]))
    for r in breakdown:
        r.pop("_cat_order", None)

    levels = sorted(set(r["Level"] for r in breakdown if r.get("Level")))

    return {
        "iw": round(iw_seats),
        "open_collab": round(open_collab_seats),
        "amenity": round(amenity_seats),
        "total": round(iw_seats + open_collab_seats + amenity_seats),
        "total_desks": total_desks,
        "total_cubbies": cubby_total,
        "breakdown": breakdown,
        "levels": levels,
    }
