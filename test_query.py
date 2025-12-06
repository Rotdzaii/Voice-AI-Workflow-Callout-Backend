import requests
import json

url = "http://localhost:8000/rag/query"
data = {"question": "MiDesk là gì?"}

try:
    response = requests.post(url, json=data, timeout=30)
    print(f"Status: {response.status_code}")
    print(f"Response:\n{json.dumps(response.json(), indent=2, ensure_ascii=False)}")
except Exception as e:
    print(f"Error: {e}")
    if hasattr(e, 'response') and e.response:
        print(f"Response text: {e.response.text}")
