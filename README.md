VoiceAI — Hướng dẫn tổng hợp (Tiếng Việt)

Mục tiêu: Tài liệu này giúp bạn nắm nhanh toàn cảnh dự án, cách chạy backend, cấu hình cơ sở dữ liệu/Supabase, chạy test, triển khai và liệt kê các endpoint chính.

1) Những việc đã thực hiện
- Quét, kiểm tra và tối ưu mã nguồn backend (FastAPI, Python 3.11).
- Kiểm tra schema CSDL, xác nhận các bảng: `users`, `workflows`, `calls`, `conversation_logs`, `intents`, `entities`.
- Đọc và xác nhận test, tài liệu README; chuẩn hoá cấu trúc.
- Đánh giá việc sử dụng Supabase SDK và realtime channel.
- Sửa lỗi test WebSocket logs (asyncpg), tối ưu DB pool cho event loop.
- Giảm flakiness của test DB, tinh chỉnh timeout/retry.
- Dọn dẹp dependencies, loại bỏ các package không cần thiết.
- Làm sạch lịch sử git cũ, push lại toàn bộ project lên GitLab.
- Tạo tag đánh dấu trạng thái giai đoạn 1: `Phase-1`.

2) Khởi động nhanh trên Windows
- Tạo và kích hoạt môi trường ảo, cài dependencies, cấu hình `.env`, chạy backend.

Lệnh mẫu (PowerShell):

```powershell
# Tạo venv
python -m venv .venv

# Kích hoạt venv
.\.venv\Scripts\Activate.ps1

# Cài đặt thư viện
pip install --upgrade pip
pip install -r requirements.txt

# Cấu hình môi trường
Copy-Item .env.example .env
# Mở .env và điền: DATABASE_URL, SUPABASE_JWT_SECRET, các biến OAuth (nếu dùng), v.v.

# Chạy backend (tuỳ cấu trúc repo)
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Kiểm tra nhanh:
- Truy cập `http://localhost:8000/health` để xem tình trạng.
- Truy cập `http://localhost:8000/docs` để mở Swagger UI.

3) Database & Supabase
- Khởi động Postgres bằng Docker Compose (tuỳ bạn dùng Docker Desktop):

```powershell
# Ví dụ: nếu repo có docker-compose.yml
docker compose up -d
```

- Khởi tạo schema DB:
	- Dùng file `voiceai_schema_combined.sql` (nếu có trong repo) để tạo bảng.
	- Hoặc dùng migration tool đi kèm (nếu đã cấu hình).

- Sử dụng Supabase:
	- Mở Supabase Studio → "SQL" → "New query" → dán nội dung `voiceai_schema_combined.sql` → Run.
	- Lấy `DATABASE_URL` và `SUPABASE_JWT_SECRET` từ Supabase, điền vào `.env`.

4) Chạy test
```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

5) Triển khai với Railway
- Đảm bảo có `Procfile`, `railway.json` (nếu dùng).
- Khai báo biến môi trường bắt buộc trong Railway Settings.
- Deploy, sau đó kiểm tra các endpoint: `/health`, `/docs`, `/debug/db_ping`.

6) Các endpoint chính
- Đăng ký/Đăng nhập: `/auth/register`, `/auth/token`.
- Quản lý workflow: `/workflows`, `/workflows/{id}`.
- Quản lý cuộc gọi: `/call/start`, `/call/reply`, `/calls/{call_id}/logs`, `/ws/calls/{call_id}/logs`.
- NLU: `/nlu/parse`, `/conversation/next`, `/conversation/agent`.
- Telephony: `/call/originate`.
- Ý định & Thực thể: `/intents`, `/entities`.

7) Lưu ý bảo mật & cấu hình
- Không commit secrets vào git. Dùng biến môi trường (CI/CD) hoặc vault.
- `.env.example` phải đủ biến để người khác thiết lập nhanh.
- Bật HTTPS khi chạy production; thiết lập CORS/headers chặt chẽ.
- Xem lại OAuth (Google/GitHub): `redirect URIs`, `scopes`, `state/nonce`, PKCE (nếu cần).

8) GitFlow và quy trình release
- Nhánh `main` giữ trạng thái phát hành ổn định.
- Làm việc trên nhánh `feature/*`, hợp nhất vào `develop` bằng merge không fast-forward.
- Tạo MR `develop → main` để review/duyệt trước khi phát hành.
- Gắn tag cho các mốc: `Phase-1`, `Phase-2`,...

9) Sự cố thường gặp
- Port 8000 bị chiếm: đổi `--port`, hoặc tắt tiến trình cũ.
- DB không kết nối: kiểm tra `DATABASE_URL`, firewall, và quyền user.
- Test flakey: tăng timeout, kiểm tra pool size, chạy lại sau khi làm sạch DB.

10) Liên hệ/đóng góp
- Mở issue/MR trên GitLab để báo lỗi hoặc đề xuất.
- Đính kèm log/ảnh màn hình khi báo lỗi để dễ tái hiện.
