"""
Benchmark engine: aggregates floor (or other) schedule data
across multiple projects and groups by Type Mark prefix.
"""

import re
from concurrent.futures import ThreadPoolExecutor, as_completed


def short_label(label: str) -> str:
    """
    Extract a short display name from a benchmark label.
    Input: "AMER DEN06 Denver [PARENT] — SALESFORCE-DEN06-AR-SYM-INTERIOR-R26.rvt"
    Output: "Denver · DEN06"
    Falls back to the full label if no pattern matches.
    """
    # Try to extract building code (e.g. DEN06, BAN01, LON01) from project name or file name
    code_match = re.search(r'\b([A-Z]{2,5}\d{2})\b', label)
    building_code = code_match.group(1) if code_match else ""

    # Project name part (before the " — " separator)
    project_part = label.split(" — ")[0].strip() if " — " in label else label

    # Extract city: words that are Title Case and not region codes or brackets
    # Remove region prefix (AMER, EMEA, JAPAC, LATAM) and [PARENT] etc.
    clean = re.sub(r'\b(AMER|EMEA|JAPAC|LATAM|INDIA|IND)\b', '', project_part, flags=re.IGNORECASE)
    clean = re.sub(r'\[.*?\]', '', clean)
    clean = re.sub(r'\b[A-Z]{2,5}\d{2,}\b', '', clean)  # remove building codes
    clean = re.sub(r'\s+', ' ', clean).strip()

    # City is the remaining meaningful words — prefer the last 1-2 words (usually the city)
    city_words = [w for w in clean.split() if len(w) >= 3 and w[0].isupper()]
    if len(city_words) >= 2 and city_words[-2].lower() in ('san', 'los', 'new', 'las', 'hong', 'abu', 'tel'):
        city = city_words[-2] + ' ' + city_words[-1]
    elif city_words:
        city = city_words[-1]
    else:
        city = ""

    if city and building_code:
        return f"{city} · {building_code}"
    elif city:
        return city
    elif building_code:
        return building_code
    return label[:40]  # fallback truncated

# ── Prefix → human label mapping ──────────────────────────────────────────
PREFIX_LABELS = {
    "CP":  "Carpet",
    "RT":  "LVT",
    "TL":  "Tile",
    "VT":  "Vinyl Tile",
    "HW":  "Hardwood",
    "ST":  "Stone",
    "CT":  "Ceramic Tile",
    "PT":  "Polished Concrete",
    "AC":  "Acoustic Tile",
    "RB":  "Rubber",
    "EP":  "Epoxy",
    "TR":  "Terrazzo",
    "GR":  "Granite",
    "MR":  "Marble",
    "WD":  "Wood",
    "BK":  "Brick",
    "SL":  "Slate",
    "LN":  "Linoleum",
    "BM":  "Bamboo",
    "CK":  "Cork",
}


def extract_prefix(type_mark: str, type_name: str = "") -> str:
    """
    Extract the floor type prefix (CP, RT, TL etc.) from Type Mark or Type Name.
    Looks for patterns like CP-01, RT-02, TL-16 anywhere in the string.
    """
    for source in [type_mark, type_name]:
        if not source:
            continue
        # Strip common prefixes like 'SFDC ', 'FINISH '
        clean = re.sub(r'^(SFDC|FINISH|IA)\s+', '', source.strip(), flags=re.IGNORECASE)
        # Look for pattern like CP-01, RT-02, TL-16 anywhere in string
        m = re.search(r'\b([A-Za-z]{2,4})-\d+', clean)
        if m:
            return m.group(1).upper()
        # Fallback: leading letters before hyphen or digit
        m2 = re.match(r'^([A-Za-z]+)', clean)
        if m2:
            prefix = m2.group(1).upper()
            if len(prefix) <= 4:  # reasonable prefix length
                return prefix
    return "Other"


def prefix_label(prefix: str) -> str:
    return PREFIX_LABELS.get(prefix.upper(), prefix)


def fetch_project_floors(token, project_name: str, urn: str, get_schedule_fn) -> dict:
    """
    Fetch floor schedule for one project.
    Returns {prefix: area_sqft} dict plus metadata.
    """
    try:
        items = get_schedule_fn(token, urn, "floors")
        groups = {}
        total_area = 0.0
        for item in items:
            type_mark = item.get("Type Mark", "") or item.get("Type", "")
            area_str = item.get("Area", "")
            try:
                area_val = float(str(area_str).split()[0]) if area_str else 0.0
            except (ValueError, IndexError):
                area_val = 0.0
            prefix = extract_prefix(type_mark)
            groups[prefix] = groups.get(prefix, 0.0) + area_val
            total_area += area_val
        return {
            "project": project_name,
            "groups": groups,
            "total_area": total_area,
            "error": None,
        }
    except Exception as e:
        return {
            "project": project_name,
            "groups": {},
            "total_area": 0.0,
            "error": str(e),
        }


def build_benchmark_result(project_results: list) -> dict:
    """
    Takes a list of per-project floor results and builds
    a comparison table + chart data.
    """
    # Apply short labels
    for r in project_results:
        r["display_name"] = short_label(r["project"])

    # Collect all prefixes across all projects
    all_prefixes = set()
    for r in project_results:
        all_prefixes.update(r["groups"].keys())

    # Sort prefixes by total area across all projects (descending)
    prefix_totals = {
        p: sum(r["groups"].get(p, 0.0) for r in project_results)
        for p in all_prefixes
    }
    sorted_prefixes = sorted(all_prefixes, key=lambda p: -prefix_totals[p])

    # Build table rows: one per project
    table = []
    for r in project_results:
        row = {
            "Project": r["display_name"],
            "Total Area (ft²)": f"{r['total_area']:,.0f}",
        }
        for prefix in sorted_prefixes:
            area = r["groups"].get(prefix, 0.0)
            pct = (area / r["total_area"] * 100) if r["total_area"] > 0 else 0
            label = prefix_label(prefix)
            row[f"{label} ({prefix}) ft²"] = f"{area:,.0f}"
            row[f"{label} ({prefix}) %"] = f"{pct:.1f}%"
        if r.get("error"):
            row["Error"] = r["error"]
        table.append(row)

    # Build chart data
    project_names = [r["display_name"] for r in project_results]
    chart_groups = []
    for prefix in sorted_prefixes:
        label = f"{prefix_label(prefix)} ({prefix})"
        values_sqft = [r["groups"].get(prefix, 0.0) for r in project_results]
        values_pct = [
            round(v / r["total_area"] * 100, 1) if r["total_area"] > 0 else 0
            for v, r in zip(values_sqft, project_results)
        ]
        chart_groups.append({
            "prefix": prefix,
            "label": label,
            "values_sqft": values_sqft,
            "values_pct": values_pct,
        })

    # Pie data per project (% breakdown)
    pie_data = []
    for r in project_results:
        r["project"] = r["display_name"]
    for r in project_results:
        slices = []
        for prefix in sorted_prefixes:
            area = r["groups"].get(prefix, 0.0)
            pct = (area / r["total_area"] * 100) if r["total_area"] > 0 else 0
            if pct > 0:
                slices.append({
                    "label": f"{prefix_label(prefix)} ({prefix})",
                    "value": round(pct, 1),
                    "area": round(area, 0),
                })
        pie_data.append({"project": r["project"], "slices": slices})

    return {
        "projects": project_names,
        "prefixes": sorted_prefixes,
        "prefix_labels": {p: prefix_label(p) for p in sorted_prefixes},
        "table": table,
        "chart_groups": chart_groups,
        "pie_data": pie_data,
        "columns": list(table[0].keys()) if table else [],
    }
