import os
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("APS_CLIENT_ID")
CLIENT_SECRET = os.getenv("APS_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise ValueError("APS_CLIENT_ID and APS_CLIENT_SECRET must be set in .env")


def get_token():
    response = requests.post(
        "https://developer.api.autodesk.com/authentication/v2/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "data:read account:read bucket:read viewables:read",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


if __name__ == "__main__":
    print(f"Access token: {get_token()}")
