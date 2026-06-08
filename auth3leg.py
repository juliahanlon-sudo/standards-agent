import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("APS_CLIENT_ID")
CLIENT_SECRET = os.getenv("APS_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_URL = "https://developer.api.autodesk.com/authentication/v2/authorize"
TOKEN_URL = "https://developer.api.autodesk.com/authentication/v2/token"
SCOPE = "data:read"

_auth_code = None
_server_done = threading.Event()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code
        params = parse_qs(urlparse(self.path).query)
        _auth_code = params.get("code", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authentication successful! You can close this tab.</h2>")
        _server_done.set()

    def log_message(self, format, *args):
        pass  # silence request logs


def get_token():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise ValueError("APS_CLIENT_ID and APS_CLIENT_SECRET must be set in .env")

    auth_params = urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
    })
    login_url = f"{AUTH_URL}?{auth_params}"

    server = HTTPServer(("localhost", 8080), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()

    print("Opening browser for Autodesk login...")
    webbrowser.open(login_url)

    _server_done.wait(timeout=120)
    server.shutdown()

    if not _auth_code:
        raise RuntimeError("No auth code received — did you complete the login?")

    response = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _auth_code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
    )
    response.raise_for_status()
    return response.json()["access_token"]


if __name__ == "__main__":
    token = get_token()
    print(f"Access token: {token}")
