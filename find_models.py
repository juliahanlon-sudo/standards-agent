import sys
import requests
from auth import get_token

BASE_URL = "https://developer.api.autodesk.com"


def get_headers(token):
    return {"Authorization": f"Bearer {token}"}


def get_top_folders(token, hub_id, project_id):
    response = requests.get(
        f"{BASE_URL}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders",
        headers=get_headers(token),
    )
    response.raise_for_status()
    return response.json()["data"]


def get_folder_contents(token, project_id, folder_id):
    items = []
    url = f"{BASE_URL}/data/v1/projects/{project_id}/folders/{folder_id}/contents"
    while url:
        response = requests.get(url, headers=get_headers(token))
        response.raise_for_status()
        body = response.json()
        items.extend(body["data"])
        url = body.get("links", {}).get("next", {}).get("href")
    return items


def search_folder(token, project_id, folder_id, folder_path, results):
    try:
        contents = get_folder_contents(token, project_id, folder_id)
    except requests.HTTPError as e:
        print(f"  [skip] {folder_path} ({e.response.status_code})")
        return

    for item in contents:
        item_type = item["type"]
        name = item["attributes"]["displayName"] or item["attributes"].get("name", "")

        if item_type == "folders":
            search_folder(
                token, project_id, item["id"],
                f"{folder_path}/{name}", results,
            )
        elif item_type == "items" and name.lower().endswith(".rvt"):
            # Latest version is in relationships.tip or included; fetch it
            tip_href = (
                item.get("relationships", {})
                .get("tip", {})
                .get("links", {})
                .get("related", {})
                .get("href")
            )
            urn = None
            if tip_href:
                try:
                    tip_resp = requests.get(tip_href, headers=get_headers(token))
                    tip_resp.raise_for_status()
                    urn = tip_resp.json().get("data", {}).get("id", "")
                except requests.HTTPError:
                    pass

            results.append({
                "name": name,
                "file_id": item["id"],
                "urn": urn or "unavailable",
                "path": f"{folder_path}/{name}",
            })


def main():
    if len(sys.argv) < 3:
        print("Usage: python find_models.py <hub_id> <project_id>")
        sys.exit(1)

    hub_id = sys.argv[1]
    project_id = sys.argv[2]
    token = get_token()

    print(f"Searching project {project_id} for .rvt files...\n")

    top_folders = get_top_folders(token, hub_id, project_id)
    results = []

    for folder in top_folders:
        folder_name = folder["attributes"]["displayName"] or folder["attributes"].get("name", "")
        print(f"Scanning: {folder_name}")
        search_folder(token, project_id, folder["id"], folder_name, results)

    print(f"\nFound {len(results)} .rvt file(s):\n")
    for r in results:
        print(f"  Name:    {r['name']}")
        print(f"  File ID: {r['file_id']}")
        print(f"  URN:     {r['urn']}")
        print(f"  Path:    {r['path']}")
        print()


if __name__ == "__main__":
    main()
