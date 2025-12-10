"""
Node 1: Frontend (UI) mock
Simulate sending phone number and receiving result from API gateway
"""
import requests

def send_phone(phone):
    url = "http://localhost:4001/api/start_call"
    resp = requests.post(url, json={"phone": phone})
    print("Result from API gateway:", resp.json())

if __name__ == "__main__":
    send_phone("0987654321")