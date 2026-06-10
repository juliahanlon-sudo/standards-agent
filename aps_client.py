import requests
from auth import get_token

BASE_URL = "https://developer.api.autodesk.com"


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_hubs(token):
    r = requests.get(f"{BASE_URL}/project/v1/hubs", headers=headers(token))
    r.raise_for_status()
    return r.json()["data"]


def get_projects(token, hub_id):
    r = requests.get(f"{BASE_URL}/project/v1/hubs/{hub_id}/projects", headers=headers(token))
    r.raise_for_status()
    return r.json()["data"]


def get_top_folders(token, hub_id, project_id):
    r = requests.get(
        f"{BASE_URL}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders",
        headers=headers(token),
    )
    r.raise_for_status()
    return r.json()["data"]


def get_folder_contents(token, project_id, folder_id):
    items = []
    url = f"{BASE_URL}/data/v1/projects/{project_id}/folders/{folder_id}/contents"
    while url:
        r = requests.get(url, headers=headers(token))
        r.raise_for_status()
        body = r.json()
        items.extend(body["data"])
        url = body.get("links", {}).get("next", {}).get("href")
    return items


def get_item_tip(token, project_id, item_id):
    r = requests.get(
        f"{BASE_URL}/data/v1/projects/{project_id}/items/{item_id}/tip",
        headers=headers(token),
    )
    r.raise_for_status()
    return r.json()["data"]


# Keywords that indicate a folder likely contains Revit models
MODEL_FOLDER_KEYWORDS = {
    "model", "models", "production", "final revit", "revit model",
    "04_models", "2-revit", "01_production", "final model", "final worksharing",
    "01_revit", "revit models",
}

# Within a model folder, prefer subfolders with these keywords (reduces depth scan)
PREFERRED_SUBFOLDER_KEYWORDS = {
    "final", "revit", "production", "current", "issued", "worksharing",
}

# Skip these folder names entirely — they never contain Revit models
SKIP_FOLDER_KEYWORDS = {
    "drawing", "drawings", "nwc", "document", "documents", "report",
    "reports", "consumed", "archive", "z-archive", "z_archive",
    "photo", "photos", "submittal", "submittals", "pdf", "cad",
    "coordination", "meeting", "correspondence",
}


def _is_model_folder(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in MODEL_FOLDER_KEYWORDS)


def _is_skip_folder(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in SKIP_FOLDER_KEYWORDS)


def _collect_rvt(token, project_id, folder_id, path, results, depth=0, max_depth=5):
    """Recursively collect .rvt files, skipping irrelevant branches."""
    try:
        contents = get_folder_contents(token, project_id, folder_id)
    except requests.HTTPError:
        return
    for item in contents:
        name = item["attributes"].get("displayName") or item["attributes"].get("name", "")
        if item["type"] == "folders":
            if depth < max_depth and not _is_skip_folder(name):
                _collect_rvt(token, project_id, item["id"], f"{path}/{name}", results, depth + 1, max_depth)
        elif item["type"] == "items" and name.lower().endswith(".rvt"):
            try:
                tip = get_item_tip(token, project_id, item["id"])
                urn = tip.get("id", "")
                last_modified = tip["attributes"].get("lastModifiedTime", "")
            except requests.HTTPError:
                urn = ""
                last_modified = ""
            results.append({
                "name": name,
                "file_id": item["id"],
                "urn": urn,
                "path": f"{path}/{name}",
                "last_modified": last_modified,
            })


def find_rvt_files(token, hub_id, project_id):
    results = []
    top_folders = get_top_folders(token, hub_id, project_id)

    # Find "Project Files" first — models are almost always inside it
    project_files_folder = next(
        (f for f in top_folders
         if (f["attributes"].get("displayName") or f["attributes"].get("name", "")).lower() == "project files"),
        None
    )

    if project_files_folder:
        pf_name = project_files_folder["attributes"].get("displayName") or "Project Files"
        try:
            subfolders = get_folder_contents(token, project_id, project_files_folder["id"])
        except requests.HTTPError:
            subfolders = []

        model_folders = [
            f for f in subfolders
            if f["type"] == "folders" and _is_model_folder(
                f["attributes"].get("displayName") or f["attributes"].get("name", "")
            )
        ]

        if model_folders:
            # Scan only the identified model folders (fast path)
            for folder in model_folders:
                fname = folder["attributes"].get("displayName") or folder["attributes"].get("name", "")
                folder_path = f"{pf_name}/{fname}"
                # Check if there are preferred subfolders to narrow further
                try:
                    sub = get_folder_contents(token, project_id, folder["id"])
                    preferred = [
                        s for s in sub if s["type"] == "folders" and
                        any(kw in (s["attributes"].get("displayName") or s["attributes"].get("name","")).lower()
                            for kw in PREFERRED_SUBFOLDER_KEYWORDS)
                    ]
                    if preferred:
                        for sf in preferred:
                            sfname = sf["attributes"].get("displayName") or sf["attributes"].get("name","")
                            _collect_rvt(token, project_id, sf["id"], f"{folder_path}/{sfname}", results)
                    else:
                        _collect_rvt(token, project_id, folder["id"], folder_path, results)
                except requests.HTTPError:
                    _collect_rvt(token, project_id, folder["id"], folder_path, results)
        else:
            # No clear model folder — scan all of Project Files shallowly
            for folder in subfolders:
                if folder["type"] != "folders":
                    continue
                fname = folder["attributes"].get("displayName") or folder["attributes"].get("name", "")
                _collect_rvt(token, project_id, folder["id"], f"{pf_name}/{fname}", results, depth=1)
    else:
        # No Project Files — scan top-level folders shallowly
        for folder in top_folders:
            fname = folder["attributes"].get("displayName") or folder["attributes"].get("name", "")
            _collect_rvt(token, project_id, folder["id"], fname, results, max_depth=2)

    return results


def encode_urn(urn: str) -> str:
    import base64
    return base64.b64encode(urn.encode()).decode().rstrip("=").replace("+", "-").replace("/", "_")


def get_manifest(token, urn):
    encoded = encode_urn(urn)
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/manifest",
        headers=headers(token),
    )
    r.raise_for_status()
    return r.json()


def request_translation(token, urn):
    """Re-request SVF translation under our app's credentials."""
    encoded = encode_urn(urn)
    r = requests.post(
        f"{BASE_URL}/modelderivative/v2/designdata/job",
        headers={**headers(token), "Content-Type": "application/json", "x-ads-force": "true"},
        json={
            "input": {"urn": encoded},
            "output": {
                "formats": [{"type": "svf", "views": ["2d", "3d"]}]
            }
        }
    )
    r.raise_for_status()
    return r.json()


def get_model_views(token, urn):
    encoded = encode_urn(urn)
    print(f"[DEBUG] raw URN:     {urn}")
    print(f"[DEBUG] encoded URN: {encoded}")
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/metadata",
        headers=headers(token),
    )
    if r.status_code == 401:
        # Derivative not accessible — re-request translation under our credentials
        print(f"[INFO] 401 on metadata, requesting translation for {urn}")
        request_translation(token, urn)
        raise requests.HTTPError(
            f"Model derivative not yet available. Translation requested — please wait a few minutes and try again.",
            response=r
        )
    r.raise_for_status()
    return r.json().get("data", {}).get("metadata", [])


def get_object_tree(token, urn, guid):
    encoded = encode_urn(urn)
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/metadata/{guid}",
        headers=headers(token),
    )
    r.raise_for_status()
    return r.json()


def get_properties(token, urn, guid, max_attempts=10, wait_seconds=5):
    import time
    encoded = encode_urn(urn)
    url = f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/metadata/{guid}/properties"
    headers_with_force = {"Authorization": f"Bearer {token}", "x-ads-force": "true"}

    for attempt in range(max_attempts):
        r = requests.get(url, headers=headers_with_force, params={"forceget": "true"})
        r.raise_for_status()
        if r.status_code == 200:
            return r.json()
        print(f"[DEBUG] Properties not ready (202), waiting {wait_seconds}s... (attempt {attempt + 1}/{max_attempts})")
        time.sleep(wait_seconds)

    raise RuntimeError("Properties extraction timed out after multiple attempts")
