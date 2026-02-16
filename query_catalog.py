import json
import base64
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests

# Load credentials from .default_key
with open('.default_key', 'r') as f:
    config = json.load(f)

system_url = config['url']
uaa = config['uaa']
catalog_path = config['catalogs']['abap']['path']

token_url = uaa['url'] + '/oauth/token'
authorize_url = uaa['url'] + '/oauth/authorize'
client_id = uaa['clientid']
client_secret = uaa['clientsecret']

REDIRECT_PORT = 8088
REDIRECT_URI = f'http://localhost:{REDIRECT_PORT}/callback'

# ── Step 1: Authorization Code Flow ──
class CallbackHandler(BaseHTTPRequestHandler):
    auth_code = None
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if 'code' in params:
            CallbackHandler.auth_code = params['code'][0]
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<h2>Login successful! You can close this tab.</h2>')
        else:
            self.send_response(400)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(f'<h2>Error: {params}</h2>'.encode())
    def log_message(self, *args):
        pass

login_url = f'{authorize_url}?' + urllib.parse.urlencode({
    'response_type': 'code',
    'client_id': client_id,
    'redirect_uri': REDIRECT_URI,
})

print("Step 1: Opening browser for login...")
webbrowser.open(login_url)
print("Waiting for callback on localhost:8088 ...")

server = HTTPServer(('localhost', REDIRECT_PORT), CallbackHandler)
server.handle_request()
server.server_close()

if not CallbackHandler.auth_code:
    print("ERROR: No authorization code received.")
    exit(1)
print("Authorization code received!")

# ── Step 2: Exchange auth code for user token ──
print("\nStep 2: Exchanging authorization code for user token...")
token_resp = requests.post(token_url, auth=(client_id, client_secret), data={
    'grant_type': 'authorization_code',
    'code': CallbackHandler.auth_code,
    'redirect_uri': REDIRECT_URI,
})
print(f"  Status: {token_resp.status_code}")
if token_resp.status_code != 200:
    print(f"  Error: {token_resp.text[:500]}")
    exit(1)

user_token = token_resp.json()['access_token']
parts = user_token.split('.')
payload = json.loads(base64.b64decode(parts[1] + '=='))
print(f"  User: {payload.get('user_name')} / {payload.get('email')}")
print(f"  Scopes: {payload.get('scope', [])[:10]}")

# ── Step 3: OAuth2UserTokenExchange (jwt-bearer) ──
print("\nStep 3: Exchanging user token (jwt-bearer) for ABAP token...")
exchange_resp = requests.post(token_url, auth=(client_id, client_secret), data={
    'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
    'assertion': user_token,
    'response_type': 'token',
})
print(f"  Status: {exchange_resp.status_code}")

if exchange_resp.status_code == 200:
    access_token = exchange_resp.json()['access_token']
    parts2 = access_token.split('.')
    payload2 = json.loads(base64.b64decode(parts2[1] + '=='))
    print(f"  Exchanged token scopes: {payload2.get('scope', [])[:10]}")
else:
    print(f"  Exchange response: {exchange_resp.text[:500]}")
    print("  Falling back to original user token...")
    access_token = user_token

# ── Step 4: Query the catalog service ──
catalog_url = f"{system_url}{catalog_path}/ServiceCollection?$format=json"
print(f"\nStep 4: Querying catalog: {catalog_url}")

resp = requests.get(catalog_url, headers={
    'Authorization': f'Bearer {access_token}',
    'Accept': 'application/json',
})
print(f"  Response status: {resp.status_code}")

if resp.status_code != 200:
    print(f"  Headers: {dict(resp.headers)}")
    print(f"  Body: {resp.text[:500]}")
else:
    catalog_data = resp.json()
    results = catalog_data.get('d', {}).get('results', [])
    print(f"\nFound {len(results)} services:\n")
    for svc in results[:30]:
        title = svc.get('Title', 'N/A')
        tech_name = svc.get('TechnicalServiceName', 'N/A')
        version = svc.get('TechnicalServiceVersion', '')
        print(f"  {tech_name} (v{version}) - {title}")
    if len(results) > 30:
        print(f"\n  ... and {len(results) - 30} more services")
