def send_phone(phone):
"""
Node 1: Frontend (UI) skeleton
Input phone number, send to API Gateway
"""
import requests

def main():
    phone = input("Enter phone number to call: ")
    url = "http://localhost:4001/api/start_call"
    try:
        resp = requests.post(url, json={"phone": phone})
        print("API Gateway response:", resp.json())
    except Exception as e:
        print("Error sending request:", e)

if __name__ == "__main__":
    main()
