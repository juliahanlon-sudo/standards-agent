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
    ("Cafe Seating",              ["Cafe Seating", "Ohana - Cafe Seating", "Customer - Cafe Seating"], 0.5,  "L3 - Hospitality"),
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
    ("Conference Room (XL)",      ["Conference Room (XL)"],                                             0,    "L3 - Enclosed Collaboration"),
    ("Focus Pod",                 ["Focus Pod"],                                                        0,    "L3 - Enclosed Collaboration"),
    ("Huddle Room",               ["Huddle Room", "Huddle", "SIC - Huddle Room"],                      0,    "L3 - Enclosed Collaboration"),
    ("Meeting Pod",               ["Meeting Pod (M)", "Meeting Pod (S)", "Meeting Pod (L)"],           0,    "L3 - Enclosed Collaboration"),
    ("Phone Room",                ["Phone Room", "Phone Room (AV)"],                                   0,    "L3 - Enclosed Collaboration"),
    ("Phone Room (Micro)",        ["Phone Room (Micro)", "Micro Phone Room"],                          0,    "L3 - Enclosed Collaboration"),
    ("Phone Room (AV)",           ["Phone Room (AV)"],                                                  0,    "L3 - Enclosed Collaboration"),
    # L3 - Individual Work
    ("Desk: Standard",            ["Open Office"],                                                      1.0,  "L3 - Individual Work"),
    ("Library",                   ["Library"],                                                          1.0,  "L3 - Individual Work"),
    ("Private Office",            ["Private Office"],                                                   1.0,  "L3 - Individual Work"),
    ("Touchdown Seat",            ["Open Office", "Focus Nook"],                                       1.0,  "L3 - Individual Work"),
    ("Work Room",                 ["Work Room", "Puppyforce", "Design Studio"],                        1.0,  "L3 - Individual Work"),
    # L3 - Open Collaboration
    ("Booth",                     ["Open Collaboration", "Banquette"],                                  0.5,  "L3 - Open Collaboration"),
    ("Cafe Collaboration Table",  ["Open Collaboration"],                                               0.5,  "L3 - Open Collaboration"),
    ("Collaboration Space",       ["Open Collaboration", "AI - Collaboration Cafe Area", "AI - Collaboration Zone",
                                   "AI - Collaborative Work Area", "AI - Expert Zone", "AI - Exploration Zone",
                                   "AI - Work Zone", "Open Air Meeting", "Open Huddle", "Pilot Seat",
                                   "Social Lounge Extension", "Whiteboarding"],                        0.5,  "L3 - Open Collaboration"),
    ("Community Table",           ["Open Collaboration", "Library Table"],                              0.5,  "L3 - Open Collaboration"),
    ("Project Bay",               ["Open Collaboration"],                                               0.5,  "L3 - Open Collaboration"),
    ("Soft Seating",              ["Open Collaboration"],                                               0,    "L3 - Open Collaboration"),
    # L3 - Workspace Specialty
    ("Catering Pantry",           ["Catering Pantry", "SIC - Pantry"],                                 0,    "L3 - Workspace Specialty"),
    ("Flex Room",                 ["Flex Room", "Flex Room - Living", "SIC - Flex Room"],              0,    "L3 - Workspace Specialty"),
    ("Mindfulness",               ["Mindfulness"],                                                      0,    "L3 - Workspace Specialty"),
    ("Multifaith Room",           ["Multifaith Room"],                                                  0,    "L3 - Workspace Specialty"),
    ("Parent's Room",             ["Parent's Room"],                                                    0,    "L3 - Workspace Specialty"),
    ("Reception",                 ["Reception", "SIC - Reception"],                                    0,    "L3 - Workspace Specialty"),
    ("Reception Lounge",          ["Reception Lounge"],                                                 0,    "L3 - Workspace Specialty"),
    ("Reflection Room",           ["Reflection Room"],                                                  0,    "L3 - Workspace Specialty"),
    ("Social Lounge",             ["Open Collaboration", "Ohana - Social Lounge",
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
    if multiplier == 0.75 or serraview_name == "Conference Room (XL)":
        return "Amenity"
    return "Other"


def match_room(room_name: str):
    """Match a room name against all architecture name variants. Returns best match or None."""
    if not room_name or not room_name.strip():
        return None

    best_score = 0
    best_entry = None
    best_variant = None

    for serraview_name, variants, multiplier, l3_section in RAW_DIRECTORY:
        for variant in variants:
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


def calculate_capacity(furniture_items: list[dict]) -> dict:
    """
    Takes a list of furniture items with keys: room_name, raw_seats.
    Returns capacity summary and breakdown rows.
    """
    iw_seats = 0.0
    open_collab_seats = 0.0
    amenity_seats = 0.0
    breakdown = []

    for item in furniture_items:
        room_name = item.get("room_name", "")
        raw_seats = float(item.get("raw_seats", 0) or 0)

        match = match_room(room_name)
        if not match:
            continue

        weighted = raw_seats * match["multiplier"]
        cat = match["category"]

        if cat == "IW":
            iw_seats += weighted
        elif cat == "Open Collab":
            open_collab_seats += weighted
        elif cat == "Amenity":
            amenity_seats += weighted

        breakdown.append({
            "Room Name": room_name,
            "Raw Seats": str(int(raw_seats)),
            "Multiplier": str(match["multiplier"]),
            "Weighted Seats": str(round(weighted, 1)),
            "Category": cat,
            "Matched Architecture Name": match["matched_variant"],
            "Match Score": str(match["score"]),
        })

    breakdown.sort(key=lambda x: -float(x["Weighted Seats"]))

    return {
        "iw": round(iw_seats),
        "open_collab": round(open_collab_seats),
        "amenity": round(amenity_seats),
        "total": round(iw_seats + open_collab_seats + amenity_seats),
        "breakdown": breakdown,
    }
