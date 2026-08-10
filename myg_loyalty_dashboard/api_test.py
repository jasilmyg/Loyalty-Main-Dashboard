import sys, os, json, urllib.request, urllib.parse
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

BASE = 'http://127.0.0.1:8001'

# Step 1: Get CSRF token from login page
req = urllib.request.Request(BASE + '/accounts/login/')
res = urllib.request.urlopen(req)
body = res.read().decode('utf-8')
cookies = res.headers.get('Set-Cookie', '')
csrf = ''
for line in body.split('\n'):
    if 'csrfmiddlewaretoken' in line and 'value=' in line:
        csrf = line.split('value="')[1].split('"')[0]
        break

# Extract session cookie
session_cookie = ''
for c in cookies.split(','):
    if 'csrftoken' in c:
        session_cookie = c.split(';')[0].strip()

print(f"CSRF: {csrf[:20]}...")

# Step 2: Login
login_data = urllib.parse.urlencode({
    'username': 'mygadmin',
    'password': 'myg@123',
    'csrfmiddlewaretoken': csrf,
    'next': '/'
}).encode()

login_req = urllib.request.Request(
    BASE + '/accounts/login/',
    data=login_data,
    method='POST',
    headers={
        'Cookie': session_cookie,
        'Referer': BASE + '/accounts/login/',
        'Content-Type': 'application/x-www-form-urlencoded'
    }
)
login_req.add_unredirected_header('Cookie', session_cookie)

import http.cookiejar
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Load login page again to get cookie
opener.open(BASE + '/accounts/login/')
login_body = urllib.parse.urlencode({
    'username': 'mygadmin',
    'password': 'myg@123',
    'csrfmiddlewaretoken': [c.value for c in cj if 'csrf' in c.name.lower()][0] if any('csrf' in c.name.lower() for c in cj) else csrf,
    'next': '/'
}).encode()
opener.open(urllib.request.Request(
    BASE + '/accounts/login/',
    data=login_body,
    method='POST',
    headers={'Content-Type': 'application/x-www-form-urlencoded',
             'Referer': BASE + '/accounts/login/'}
))
print("Logged in. Calling API...")

# Step 3: Call API
import time
t = time.time()
api_res = opener.open(BASE + '/api/v1/campaign-analysis/', timeout=180)
elapsed = round(time.time() - t, 1)
data = json.loads(api_res.read().decode('utf-8'))

print(f"\n=== LIVE API RESULT ({elapsed}s) ===")
ai = data.get('ai_forecast', {})
print("Status         :", data.get('status'))
print("Data source    :", ai.get('data_source'))
print("Resurrection % :", ai.get('resurrection_prob'))
print("Dormancy Risk  :", ai.get('dormancy_risk'))
print("Repeat Prob    :", ai.get('repeat_prob'))
print("Predicted Vol  :", f"{ai.get('predicted_vol', 0):,}")
print("Accuracy       :", ai.get('accuracy'))
print("RMSE           :", ai.get('rmse'))
print("Historical     :", ai.get('historical'))
print("Predictions    :", ai.get('predictions'))
print("Insights count :", len(ai.get('insights', [])))
print("Conf. scores   :", ai.get('confidence_scores'))
