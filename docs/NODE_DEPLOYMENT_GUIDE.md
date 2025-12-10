# Voice AI Callout - 4 Node Deployment Guide

## Node 1: Frontend (UI)
- **Vị trí:** `frontend/voice_ai.html`
- **Chức năng:**
  - Nhập số điện thoại, gửi yêu cầu start call đến API trung gian.
  - Gửi/nhận audio khi gọi, phát audio TTS trả về.
- **Chạy:**
  - Có thể mở trực tiếp file HTML hoặc deploy qua web server (nginx, http-server, ...).

## Node 2: API Trung Gian (Start Call API)
- **Vị trí:** `frontend/start_call_api.py`
- **Chức năng:**
  - Nhận số điện thoại từ frontend, chuyển tiếp đến backend chính qua API.
- **Chạy:**
  ```bash
  uvicorn frontend.start_call_api:app --reload --port 4001
  ```
  - Đảm bảo biến môi trường `BACKEND_URL` trỏ về endpoint `/call/start` của backend chính.

## Node 3: Backend Chính (Voice AI Backend)
- **Vị trí:** `backend/` (FastAPI, main logic)
- **Chức năng:**
  - Nhận yêu cầu start call, thực hiện logic gọi điện (SIP/Asterisk/Twilio).
  - Nhận/gửi audio, xử lý STT, sinh response, gọi Google TTS, gửi audio về frontend.
- **Chạy:**
  ```bash
  uvicorn backend.main:app --reload --port 8000
  ```
  - Hoặc theo hướng dẫn trong README.

## Node 4: Google TTS Service
- **Vị trí:** Dịch vụ ngoài (Google Cloud Text-to-Speech API)
- **Chức năng:**
  - Nhận text từ backend, trả về audio (speech).
- **Chạy:**
  - Đăng ký tài khoản Google Cloud, tạo API key/service account, cấu hình trong backend (file json key, biến môi trường, ...).

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
