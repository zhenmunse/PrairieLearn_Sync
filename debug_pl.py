import requests

token = input("Paste PL token: ").strip()
cid = input("Course Instance ID: ").strip()
base = "https://us.prairielearn.com"

endpoints = [
    f"/api/v1/course_instances/{cid}/assessments",
    f"/pl/api/v1/course_instances/{cid}/assessments",
]

for path in endpoints:
    for label, headers, params in [
        ("Private-Token", {"Private-Token": token, "Accept": "application/json"}, {}),
        ("Bearer",        {"Authorization": "Bearer " + token, "Accept": "application/json"}, {}),
    ]:
        url = base + path
        r = requests.get(url, headers=headers, params=params, timeout=10)
        ct = r.headers.get("Content-Type", "")
        is_json = "json" in ct
        print(f"[{path}] [{label}] → {r.status_code} | {ct[:40]} | json={is_json}")
        if is_json or r.status_code != 200:
            print("  Body[:400]:", r.text[:400])
