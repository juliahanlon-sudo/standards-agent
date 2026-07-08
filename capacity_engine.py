"""
Capacity calculation engine.
Space type directory is embedded directly from the Google Sheet — no file upload needed.
"""

from rapidfuzz import fuzz, process as rfuzz_process

# ── Space type directory ──────────────────────────────────────────────────────
# Format: (serraview_name, [arch_name_variants], multiplier, l3_section)
# Multiplier: "TRUE - 1" = 1.0, "TRUE - .75" = 0.75, "TRUE - .50" = 0.5, blank/other = 0

RAW_DIRECTORY = [
    # L1 - Core
    ('All Gender Restroom', ['Gender Neutral Restroom'], 0, 'L1 - Core'),
    ('Base Building Services', ['HVAC', 'Plumbing', 'Drainage', 'Gas', 'Sprinkler Infrastructure', 'BMS'], 0, 'L1 - Core'),
    ('Building Restroom', ["Women's Restroom", "Men's Restroom"], 0, 'L1 - Core'),
    ('Building Storage', ['Storage'], 0, 'L1 - Core'),
    ('Electrical', ['Electrical'], 0, 'L1 - Core'),
    ('Elevator', ['Elevator'], 0, 'L1 - Core'),
    ('Elevator Lobby', ['Elevator Lobby'], 0, 'L1 - Core'),
    ('Fire Control', ['Fire Control'], 0, 'L1 - Core'),
    ('Fire Refuge', ['Fire Refuge'], 0, 'L1 - Core'),
    ('Fire Stairs', ['Fire Stairs'], 0, 'L1 - Core'),
    ('Mechanical', ['Mechanical'], 0, 'L1 - Core'),
    ('Shower', ['Shower'], 0, 'L1 - Core'),
    ('Telecom', ['Telecom'], 0, 'L1 - Core'),
    ('Universal Restroom', ['Universal Restroom'], 0, 'L1 - Core'),
    ('UPS', ['UPS'], 0, 'L1 - Core'),
    ('Vestibule', ['Vestibule'], 0, 'L1 - Core'),
    ('Void', ['Void'], 0, 'L1 - Core'),
    # L3 - Building Specialty
    ('AV Control Room', ['AV Room'], 0, 'L3 - Building Specialty'),
    ('Badge Room', ['Badge Room', 'IT & Security - Badge Room'], 0, 'L3 - Building Specialty'),
    ('Catering', ['Customer - Catering', 'SIC - Catering'], 0, 'L3 - Building Specialty'),
    ('Childcare', ['Childcare'], 0, 'L3 - Building Specialty'),
    ('Command Center', ['Command Center', 'SIC - Team Area'], 1.0, 'L3 - Building Specialty'),
    ('Critical Incident Center', ['Critical Incident Center'], 1.0, 'L3 - Building Specialty'),
    ('CSIRT', ['CSIRT'], 1.0, 'L3 - Building Specialty'),
    ('Fitness Center', ['Fitness Center'], 0, 'L3 - Building Specialty'),
    ('Game Room', ['Ping Pong', 'Billiards'], 0, 'L3 - Building Specialty'),
    ('GO Center', ['GO Center'], 1.0, 'L3 - Building Specialty'),
    ('IT Provisioning', ['IT Provisioning'], 0, 'L3 - Building Specialty'),
    ('Lab', ["Ignite Maker's Lab", 'Ignite Lab', 'Lightning Performance Lab', 'Sech Tech Lab'], 0, 'L3 - Building Specialty'),
    ('Media Room', ['Media Room', 'AI - Recording Studio'], 0, 'L3 - Building Specialty'),
    ('Medical Room', ['Medical Room'], 0, 'L3 - Building Specialty'),
    ('Mobility Lab', ['Mobility Lab'], 0, 'L3 - Building Specialty'),
    ('Outdoor / Terrace', ['Terrace', 'Balcony'], 0, 'L3 - Building Specialty'),
    ('Pantry', ['Pantry', 'Ohana - Pantry (BOH)'], 0, 'L3 - Building Specialty'),
    ('Site Reliability Engineering', ['SRE'], 1.0, 'L3 - Building Specialty'),
    ('Staging/Green Room', ['Green Room'], 0, 'L3 - Building Specialty'),
    ('Techforce', ['Techforce'], 0, 'L3 - Building Specialty'),
    ('Techforce Lab', ['Techforce Lab'], 0, 'L3 - Building Specialty'),
    ('UX Lab', ['UX Lab'], 0, 'L3 - Building Specialty'),
    # L3 - Hospitality
    ('AI - Learning', ['AI - Learning'], 0, 'L3 - Hospitality'),
    ('Barista Bar', ['Barista Bar', 'Ohana - Barista Bar', 'Barista Bar (Mobile)'], 0, 'L3 - Hospitality'),
    ('Cafeteria', ['Cafeteria'], 0, 'L3 - Hospitality'),
    ('Community Trailblazer Hub', ['Community Trailblazer Hub'], 0, 'L3 - Hospitality'),
    ('Customer - Conference Room', ['Conference Room'], 0.75, 'L3 - Hospitality'),
    ('Customer - Huddle Room', ['Huddle Room'], 0, 'L3 - Hospitality'),
    ('Customer - Phone Room', ['Phone Room', 'Ohana - Phone Room'], 0, 'L3 - Hospitality'),
    ('Customer - Work Room', ['Customer - Work Room'], 0.75, 'L3 - Hospitality'),
    ('Demo Area', ['Demo Area'], 0, 'L3 - Hospitality'),
    ('Exhibition Dining', ['Exhibition Dining', 'Dinning', 'Ohana - Exhibition Dining'], 0, 'L3 - Hospitality'),
    ('Exhibition Kitchen', ['Exhibition Kitchen', 'Ohana - Exhibition Kitchen (FOH)', 'Kitchen (BOH)'], 0, 'L3 - Hospitality'),
    ('Lounge', ['Lounge', 'Ohana - Barista Bar Social Lounge', 'Ohana - Lounge', 'Ohana - Salon', 'SIC - Lounge', 'Customer - Lounge'], 0, 'L3 - Hospitality'),
    ('Ohana - Conference Room', ['Conference Room', 'Board Room'], 0.75, 'L3 - Hospitality'),
    ('Ohana - Huddle Room', ['Huddle Room'], 0, 'L3 - Hospitality'),
    ('Ohana - Piano', ['Piano'], 0, 'L3 - Hospitality'),
    ('Ohana - Production Room', ['Ohana - Production Room'], 0, 'L3 - Hospitality'),
    ('Salon', ['Salon', 'SIC - Salon', 'Customer - Salon'], 0, 'L3 - Hospitality'),
    ('SIC - Conference Room', ['Conference Room'], 0, 'L3 - Hospitality'),
    ('SIC - Phone Room', ['Phone Room'], 0, 'L3 - Hospitality'),
    ('SIC - Private Dining Room', ['SIC Dining', 'SIC Private Dining Room'], 0, 'L3 - Hospitality'),
    # L3 - M&E
    ('Auditorium', ['Auditorium'], 0.75, 'L3 - M&E'),
    ('Pre-Function Space', ['Pre-Function Space', 'Auditorium Social Lounge'], 0, 'L3 - M&E'),
    ('Project Room', ['Project Room', 'Training Room (XS)'], 0.75, 'L3 - M&E'),
    ('Training Room (L)', ['Training Room (L)'], 0.75, 'L3 - M&E'),
    ('Training Room (M)', ['Training Room (M)'], 0.75, 'L3 - M&E'),
    ('Training Room (S)', ['Training Room (S)'], 0.75, 'L3 - M&E'),
    # L3 - Enclosed Collaboration
    ('Conference Room (Aloha)', ['Aloha Conference Room'], 0, 'L3 - Enclosed Collaboration'),
    ('Conference Room (L)', ['Conference Room (L)'], 0, 'L3 - Enclosed Collaboration'),
    ('Conference Room (M)', ['Conference Room (M)'], 0, 'L3 - Enclosed Collaboration'),
    ('Conference Room (XL)', ['Conference Room (XL)'], 0, 'L3 - Enclosed Collaboration'),
    ('Focus Pod', ['Focus Pod'], 0, 'L3 - Enclosed Collaboration'),
    ('Huddle Room', ['Huddle Room', 'Huddle', 'SIC - Huddle Room'], 0, 'L3 - Enclosed Collaboration'),
    ('Meeting Pod', ['Meeting Pod (M) Meeting Pod (S)', 'Meeting Pod (L)'], 0, 'L3 - Enclosed Collaboration'),
    ('Phone Room', ['Phone Room', 'Phone Room (AV)'], 0, 'L3 - Enclosed Collaboration'),
    ('Phone Room (Micro)', ['Phone Room (Micro)', 'Micro Phone Room'], 0, 'L3 - Enclosed Collaboration'),
    ('Phone Room (AV)', ['Phone Room (AV)'], 0, 'L3 - Enclosed Collaboration'),
    # L3 - Individual Work
    ('Desk: Standard', ['Open Office'], 1.0, 'L3 - Individual Work'),
    ('Library', ['Library'], 1.0, 'L3 - Individual Work'),
    ('Private Office', ['Private Office'], 1.0, 'L3 - Individual Work'),
    ('Touchdown Seat', ['Open Office', 'Focus Nook'], 1.0, 'L3 - Individual Work'),
    ('Work Room', ['Work Room', 'Puppyforce', 'Design Studio'], 1.0, 'L3 - Individual Work'),
    # L3 - Open Collaboration
    ('Booth', ['Open Collaboration', 'Banquette', 'Booth'], 0.5, 'L3 - Open Collaboration'),
    ('Cafe Collaboration Table', ['Open Collaboration'], 0.5, 'L3 - Open Collaboration'),
    ('Collaboration Space', ['Open Collaboration', 'AI - Collaboration Cafe Area', 'AI - Collaboration Zone', 'AI - Collaborative Work Area', 'AI - Expert Zone', 'AI - Exploration Zone', 'AI - Work Zone', 'Open Air Meeting', 'Open Huddle', 'Pilot Seat', 'Social Lounge Extension', 'Whiteboarding'], 0.5, 'L3 - Open Collaboration'),
    ('Community Table', ['Open Collaboration', 'Library Table', 'Community Table'], 0.5, 'L3 - Open Collaboration'),
    ('Project Bay', ['Open Collaboration'], 0.5, 'L3 - Open Collaboration'),
    ('Soft Seating', ['Open Collaboration', 'Soft Seating'], 0, 'L3 - Open Collaboration'),
    # L3 - Workspace Specialty
    ('Catering Pantry', ['Catering Pantry', 'SIC - Pantry'], 0, 'L3 - Workspace Specialty'),
    ('Flex Room', ['Flex Room', 'Flex Room - Living', 'SIC - Flex Room'], 0, 'L3 - Workspace Specialty'),
    ('Mindfulness', ['Mindfulness'], 0, 'L3 - Workspace Specialty'),
    ('Multifaith Room', ['Multifaith Room'], 0, 'L3 - Workspace Specialty'),
    ("Parent's Room", ["Parent's Room"], 0, 'L3 - Workspace Specialty'),
    ('Reception', ['Reception', 'SIC - Reception'], 0, 'L3 - Workspace Specialty'),
    ('Reception Lounge', ['Reception Lounge'], 0, 'L3 - Workspace Specialty'),
    ('Reflection Room', ['Reflection Room'], 0, 'L3 - Workspace Specialty'),
    ('Social Lounge', ['Open Collaboration', 'Ohana - Social Lounge', 'Barista Bar Social Lounge', 'Customer - Social Lounge'], 0, 'L3 - Workspace Specialty'),
    ('Treadmill Desk', ['Treadmill Desk'], 0, 'L3 - Workspace Specialty'),
    ('Water Point', ['Water Point', 'Tea Point'], 0, 'L3 - Workspace Specialty'),
    ('Webinar Room', ['Webinar Room', 'Production Room'], 0, 'L3 - Workspace Specialty'),
    ('Wellness Room', ['Wellness Room', 'Camp B-Well Room'], 0, 'L3 - Workspace Specialty'),
    # L2 - Support
    ('AV Rack Room', ['Customer - AV Room', 'SIC - AV Room', 'Ohana - AV Room'], 0, 'L2 - Support'),
    ('AV Storage', ['AV Storage', 'Ohana - AV Storage'], 0, 'L2 - Support'),
    ('Bike Room', ['Bike Room', 'Bike Storage'], 0, 'L2 - Support'),
    ('Bomb Shelter', ['Bomb Shelter', 'Mamad'], 0, 'L2 - Support'),
    ('Built Out Zone', ['Interior Encroachment'], 0, 'L2 - Support'),
    ('Coat Closet', ['Coat Closet'], 0, 'L2 - Support'),
    ('Communicating Stair', ['Communicating Stair'], 0, 'L2 - Support'),
    ('Copy Print Center', ['Copy Print Center'], 0, 'L2 - Support'),
    ('Cubbies', ['Cubbies', 'Hotboxes'], 0, 'L2 - Support'),
    ('IDF', ['IDF', 'IT & Security - IDF'], 0, 'L2 - Support'),
    ('Irrigation Room', ['Irrigation Room'], 0, 'L2 - Support'),
    ('Janitor Closet', ['Janitor Closet', 'Janitor'], 0, 'L2 - Support'),
    ('Service Closet', ['Service Closet', 'Tele & Elec Closet', 'Tele Closet', 'Elec Closet', 'Telecom Closet', 'Electrical Closet', 'Service Corridor', 'Service Lobby'], 0, 'L2 - Support'),
    ('Lobby', ['Building Lobby', 'Lobby'], 0, 'L2 - Support'),
    ('Locker Room', ['Locker Room'], 0, 'L2 - Support'),
    ('Mail Center', ['Mail Center', 'Shipping/Receiving'], 0, 'L2 - Support'),
    ('MDF', ['MDF', 'IT & Security - MDF'], 0, 'L2 - Support'),
    ('Millwork & Trash/Recycling', ['Trash'], 0, 'L2 - Support'),
    ('Office Services Supply Room', ['Office Services Supply Room'], 0, 'L2 - Support'),
    ('Restroom', ['Restroom', 'Shower', 'Ohana - Restroom', 'Customer - Restroom'], 0, 'L2 - Support'),
    ('Staff Room', ['Staff Room', 'Porter Lounge'], 0, 'L2 - Support'),
    ('Storage', ['Storage', 'IT Storage', 'Ohana - Storage', 'SIC - Storage', 'TBR - Storage'], 0, 'L2 - Support'),
    ('Team Storage', ['Team Storage'], 0, 'L2 - Support'),
    ('Built-In Planter', ['Built-In Planter'], 0, 'L2 - Support'),
    ('Handwash Station', ['Handwashing', 'Handwash Station'], 0, 'L2 - Support'),
]

THRESHOLD = 80

# Some architecture names appear in multiple directory sections. When the
# matched variant (case-insensitive) is one of these, force the room category
# to the preferred section regardless of which entry scored highest.
SECTION_OVERRIDE = {
    "phone room": "L3 - Enclosed Collaboration",
    "storage": "L2 - Support",
    "conference room": "L3 - Enclosed Collaboration",
    "huddle room": "L3 - Enclosed Collaboration",
    "restroom": "L2 - Support",
    "salon": "L3 - Hospitality",
    "lounge": "L3 - Hospitality",
}


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


def room_category(l3_section: str) -> str:
    """Category for the rooms pie chart. Per the Space Type Directory, the
    L3/L2/L1 section IS the category. Returns the section label verbatim so the
    chart groups rooms exactly as the directory does."""
    return l3_section or "Unmatched"


def stage2_category(serraview_name: str, section: str) -> str:
    """Fine-grained pie-chart bucket (Salesforce "Stage 2"). Enclosed
    Collaboration splits into Conference / Huddle / Phone Room; Focus Pod rolls
    into Phone Room and Meeting Pod into Huddle Room."""
    sv = (serraview_name or "").lower()
    if section == "L3 - Individual Work":
        return "Individual Work"
    if section == "L3 - Open Collaboration":
        return "Collaboration Open"
    if section == "L3 - Enclosed Collaboration":
        if "conference room" in sv:
            return "Conference Room"
        if "huddle" in sv or "meeting pod" in sv:
            return "Huddle Room"
        if "phone room" in sv or "focus pod" in sv:
            return "Phone Room"
        return "Conference Room"
    if section == "L3 - Workspace Specialty":
        return "Workspace Specialty"
    if section in ("L3 - Hospitality", "L3 - M&E", "L3 - Building Specialty"):
        return "Amenity"
    if section in ("L2 - Support", "L1 - Core"):
        return "Support"
    return "Unmatched"


# Stage 1 rolls the fine-grained buckets up into Workspace / Amenity /
# Specialty / Support.
STAGE1_OF_STAGE2 = {
    "Individual Work": "Workspace",
    "Collaboration Open": "Workspace",
    "Conference Room": "Workspace",
    "Huddle Room": "Workspace",
    "Phone Room": "Workspace",
    "Workspace Specialty": "Workspace",
    "Amenity": "Amenity",
    "Support": "Support",
    "Unmatched": "Unmatched",
}


def stage1_category(stage2: str) -> str:
    return STAGE1_OF_STAGE2.get(stage2, "Unmatched")


def match_room(room_name: str):
    """Match a room name against all architecture name variants (the arch-names
    column) using a fuzzy match against the Revit room name. Returns best match
    or None."""
    if not room_name or not room_name.strip():
        return None

    name_lower = room_name.strip().lower()

    # Stairs default to Core, except Communicating / Internal stairs (which are
    # tenant circulation, not building shell) -> Support. Handled explicitly so
    # fuzzy scoring doesn't mis-route "Egress Stair" or miss "Internal Stair".
    if "stair" in name_lower:
        if "communicating" in name_lower or "internal" in name_lower:
            section = "L2 - Support"
            serra = "Communicating Stair"
        else:
            section = "L1 - Core"
            serra = "Fire Stairs"
        s2 = stage2_category(serra, section)
        return {
            "serraview_name": serra,
            "matched_variant": room_name.strip(),
            "multiplier": 0,
            "l3_section": section,
            "category": get_category(serra, section, 0),
            "room_category": room_category(section),
            "stage2": s2,
            "stage1": stage1_category(s2),
            "score": 100,
        }

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
        # Multi-section names (e.g. "Conference Room") force a preferred section.
        section_for_category = SECTION_OVERRIDE.get(
            best_variant.strip().lower(), l3_section
        )
        s2 = stage2_category(serraview_name, section_for_category)
        return {
            "serraview_name": serraview_name,
            "matched_variant": best_variant,
            "multiplier": multiplier,
            "l3_section": l3_section,
            "category": get_category(serraview_name, l3_section, multiplier),
            "room_category": room_category(section_for_category),
            "stage2": s2,
            "stage1": stage1_category(s2),
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
