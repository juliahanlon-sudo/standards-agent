import requests
from auth import get_token

BASE_URL = "https://developer.api.autodesk.com"


def get_hubs(token):
    response = requests.get(
        f"{BASE_URL}/project/v1/hubs",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()["data"]


def get_projects(token, hub_id):
    response = requests.get(
        f"{BASE_URL}/project/v1/hubs/{hub_id}/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    return response.json()["data"]


def main():
    token = get_token()
    hubs = get_hubs(token)

    if not hubs:
        print("No hubs found.")
        return

    for hub in hubs:
        hub_id = hub["id"]
        hub_name = hub["attributes"]["name"]
        print(f"\nHub: {hub_name}")
        print(f"  ID: {hub_id}")
        print(f"  Projects:")

        projects = get_projects(token, hub_id)
        if not projects:
            print("    (no projects)")
        for project in projects:
            print(f"    - {project['attributes']['name']}")
            print(f"      ID: {project['id']}")


if __name__ == "__main__":
    main()
