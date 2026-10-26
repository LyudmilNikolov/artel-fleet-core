"""Get a Gmail API refresh token for GMAIL_REFRESH_TOKEN (run once, on your own computer).

Usage:
    python scripts/gmail_refresh_token.py <client_id> <client_secret>

It opens Google's consent page in the browser. Sign in as the sending Gmail account
(e.g. fleetcorenotifications@gmail.com) and allow "Send email on your behalf".
The script then prints the refresh token. Store it in the hosting dashboard; never commit it.
The OAuth client must be of type "Desktop app" in Google Cloud Console.
"""

import http.server
import secrets
import sys
import urllib.parse
import webbrowser

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.send"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    client_id, client_secret = sys.argv[1], sys.argv[2]
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(self.path).query))
            if query.get("state") == state:
                result.update(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Done. You can close this tab and return to the terminal.")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print(f"Opening the browser. If it does not open, visit:\n{url}\n")
    webbrowser.open(url)
    while not result:
        server.handle_request()

    if "code" not in result:
        sys.exit(f"Google returned an error: {result.get('error', 'unknown')}")
    response = httpx.post(
        TOKEN_URL,
        data={
            "code": result["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    response.raise_for_status()
    refresh_token = response.json().get("refresh_token")
    if not refresh_token:
        sys.exit("No refresh token returned. Remove the app's access at myaccount.google.com/permissions and retry.")
    print("GMAIL_REFRESH_TOKEN =", refresh_token)


if __name__ == "__main__":
    main()
