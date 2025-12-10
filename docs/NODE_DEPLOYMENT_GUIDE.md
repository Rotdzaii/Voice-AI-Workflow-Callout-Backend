# Voice AI Callout - 4 Node Deployment Guide

## Node 1: Frontend (UI mock)
- **Location:** `mock/frontend_node.py`
- **Function:**
  - Simulates sending phone number to API Gateway and prints the result.
- **Run:**
  ```bash
  python mock/frontend_node.py
  ```

## Node 2: API Gateway (Start Call API)
- **Location:** `backend/api_gateway_node.py`
- **Function:**
  - Receives phone number from frontend, forwards to backend node.
- **Run:**
  ```bash
  uvicorn backend.api_gateway_node:app --reload --port 4001
  ```

## Node 3: Backend (Voice AI Backend mock)
- **Location:** `backend/mock_backend_node.py`
- **Function:**
  - Receives phone number, simulates call handling, generates response text, calls Google TTS node.
- **Run:**
  ```bash
  uvicorn backend.mock_backend_node:app --reload --port 8000
  ```

## Node 4: Google TTS Service (mock)
- **Location:** `backend/mock_google_tts_node.py`
- **Function:**
  - Receives text and returns mock audio url.
- **Run:**
  ```bash
  uvicorn backend.mock_google_tts_node:app --reload --port 9000
  ```

---

## Sơ đồ tổng quan

Frontend (UI) ⇄ API Trung Gian ⇄ Backend Chính ⇄ Google TTS

- **Frontend** gửi số điện thoại và audio lên API trung gian/backend.
- **API Trung Gian** chuyển tiếp số điện thoại đến backend chính.
- **Backend Chính** xử lý gọi điện, voice AI, gọi Google TTS.
- **Google TTS** trả về audio cho backend, backend trả về frontend.

---

## Lưu ý
- Không thay đổi cấu trúc project hiện tại.
- Có thể chạy từng node độc lập, dễ scale và debug.
- Đảm bảo các node có thể giao tiếp qua HTTP (mở port, cấu hình CORS nếu cần).
- Nếu cần STT ngoài, có thể bổ sung node thứ 5.
