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


def find_rvt_files(token, hub_id, project_id):
    results = []

    def scan(folder_id, path):
        try:
            contents = get_folder_contents(token, project_id, folder_id)
        except requests.HTTPError:
            return
        for item in contents:
            name = item["attributes"].get("displayName") or item["attributes"].get("name", "")
            if item["type"] == "folders":
                scan(item["id"], f"{path}/{name}")
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

    top_folders = get_top_folders(token, hub_id, project_id)
    for folder in top_folders:
        folder_name = folder["attributes"].get("displayName") or folder["attributes"].get("name", "")
        scan(folder["id"], folder_name)

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


def get_model_views(token, urn):
    encoded = encode_urn(urn)
    print(f"[DEBUG] raw URN:     {urn}")
    print(f"[DEBUG] encoded URN: {encoded}")
    r = requests.get(
        f"{BASE_URL}/modelderivative/v2/designdata/{encoded}/metadata",
        headers=headers(token),
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
