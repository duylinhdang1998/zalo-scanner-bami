# Hướng dẫn Deploy — Zalo Bot Scanner

**Kiến trúc**: GitHub Actions build Docker image → push GHCR → SSH pull về VPS.
VPS chỉ cần `docker-compose.yml` + `.env`, **không clone source code**.

---

## Tổng quan

```
push main
  └─► GitHub Actions
        ├─ job build-push: docker build → push ghcr.io/<owner>/<repo>:latest + :<sha>
        └─ job deploy:     SSH vào VPS → docker compose pull && up -d
```

Secrets CI/CD cần:

| Secret | Mô tả |
|--------|-------|
| `VPS_HOST` | IP hoặc domain VPS (ví dụ: `103.x.x.x`) |
| `VPS_USER` | User SSH trên VPS (ví dụ: `ubuntu`, `root`) |
| `VPS_SSH_KEY` | Private key SSH (nội dung file `~/.ssh/id_ed25519`) |
| `VPS_PORT` | Cổng SSH — bỏ trống nếu dùng cổng mặc định 22 |
| `GITHUB_TOKEN` | **Tự động** — GitHub tự cung cấp, không cần set |

---

## Bước 1 — Tạo GitHub repo và push code

```bash
# Trong thư mục dự án (đã git init sẵn từ Sprint 4)
git remote add origin https://github.com/YOUR_USERNAME/zalo-bot-scanner.git

# Lần đầu push
git push -u origin main
```

> Sau khi push, GitHub Actions sẽ kích hoạt tự động. Lần đầu sẽ fail job
> `deploy` vì VPS chưa chuẩn bị — bình thường. Làm xong Bước 4 rồi push lại.

---

## Bước 2 — Tạo Supabase project và lấy 2 connection string

1. Vào [supabase.com](https://supabase.com) → New project.
2. Điền tên project, chọn region gần VPS (ví dụ: Southeast Asia).
3. Vào **Settings → Database → Connection string**.
4. Lấy 2 URL:

| Mục đích | Tab | Cổng | Dùng trong |
|----------|-----|------|------------|
| App runtime (bot) | **Transaction** (Pooler) | `6543` | `DATABASE_URL` |
| Alembic migration | **Session** hoặc **Direct** | `5432` | `DATABASE_MIGRATION_URL` |

5. Format URL cần dùng (thêm `+psycopg` vào scheme):
   ```
   DATABASE_URL=postgresql+psycopg://postgres.PROJECTREF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres
   DATABASE_MIGRATION_URL=postgresql+psycopg://postgres:PASSWORD@db.PROJECTREF.supabase.co:5432/postgres
   ```

> **Tại sao 2 URL?** PgBouncer (cổng 6543) dùng transaction pooling — không hỗ trợ
> DDL (ALTER TABLE, CREATE TABLE). Alembic cần kết nối direct (5432) để chạy migration.

---

## Bước 3 — Set GitHub Secrets

Vào repo GitHub → **Settings → Secrets and variables → Actions → New repository secret**.

Tạo 4 secrets sau:

```
VPS_HOST   = <IP hoặc domain VPS của bạn>
VPS_USER   = <user SSH, thường là ubuntu hoặc root>
VPS_SSH_KEY = <nội dung private key, ví dụ: cat ~/.ssh/id_ed25519>
VPS_PORT   = 22   (hoặc bỏ trống nếu dùng port 22)
```

> `GITHUB_TOKEN` **không cần set** — GitHub tự inject vào mỗi workflow run.

---

## Bước 4 — Chuẩn bị VPS

Kết nối SSH vào VPS và chạy từng bước:

### 4a. Cài Docker + Docker Compose plugin

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-plugin

# Cho phép user hiện tại dùng docker không cần sudo
sudo usermod -aG docker $USER
newgrp docker
```

### 4b. Tạo thư mục deploy

```bash
mkdir -p ~/deploy
cd ~/deploy
```

### 4c. Đặt docker-compose.yml lên VPS

Copy nội dung file `docker-compose.yml` từ repo lên VPS:

```bash
# Trên máy local — copy file lên VPS:
scp docker-compose.yml USER@VPS_HOST:~/deploy/docker-compose.yml

# Hoặc tạo thủ công trên VPS:
nano ~/deploy/docker-compose.yml
# (dán nội dung từ file docker-compose.yml trong repo)
```

**Sửa placeholder trong docker-compose.yml**:
```yaml
# Thay dòng này:
image: ghcr.io/OWNER/REPO:latest
# Thành:
image: ghcr.io/YOUR_GITHUB_USERNAME/zalo-bot-scanner:latest
```

### 4d. Tạo file .env trên VPS

```bash
nano ~/deploy/.env
```

Điền đầy đủ dựa trên `.env.example` trong repo. Các biến **bắt buộc** (★):

```env
ZALO_BOT_TOKEN=<id>:<secret>
BEEKNOEE_API_KEY=sk-...
DATABASE_URL=postgresql+psycopg://postgres.PROJECTREF:PASS@aws-0-REGION.pooler.supabase.com:6543/postgres
DATABASE_MIGRATION_URL=postgresql+psycopg://postgres:PASS@db.PROJECTREF.supabase.co:5432/postgres
SCAN_MODE=mention
```

### 4e. Đăng nhập GHCR trên VPS để pull private image

```bash
# Tạo GitHub Personal Access Token (PAT) tại:
# github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)
# Chọn scope: read:packages

docker login ghcr.io -u YOUR_GITHUB_USERNAME
# Nhập PAT khi được hỏi password
```

> Nếu repo GitHub là **public**, image GHCR cũng public → bỏ qua bước này.
> Nếu repo **private**, image GHCR mặc định private → bắt buộc đăng nhập.

### 4f. Lần đầu chạy container

```bash
cd ~/deploy

# Pull image (lần đầu sẽ build trên CI trước)
docker compose pull

# Start container (chạy nền, tự restart khi reboot VPS)
docker compose up -d

# Xem logs
docker compose logs -f
```

---

## Bước 5 — Tự động hóa (push main → auto deploy)

Từ giờ, mỗi khi push lên nhánh `main`:

```bash
git add .
git commit -m "feat: ..."
git push origin main
```

GitHub Actions sẽ tự động:
1. Build Docker image mới
2. Push lên GHCR với tag `:latest` và `:<commit-sha>`
3. SSH vào VPS, pull image mới, restart container

Theo dõi tiến trình tại: **GitHub repo → Actions tab**.

---

## Rollback về commit cũ

```bash
# Trên VPS — thay <sha> bằng commit SHA cần rollback
cd ~/deploy
docker compose stop
docker compose run --rm -e "IMAGE_TAG=<sha>" zalo-bot true || true

# Sửa docker-compose.yml: đổi :latest → :<sha>
sed -i 's/:latest/:THE_OLD_SHA/' docker-compose.yml
docker compose up -d
```

Hoặc đơn giản hơn: revert commit trên GitHub → push lại → CI tự build lại.

---

## Bảo mật

- **Không commit `.env` thật** — đã có trong `.gitignore`.
- **Không bake secrets vào image** — dùng `env_file: .env` trên VPS.
- **GHCR image private** nếu repo GitHub private — chỉ bot VPS đăng nhập mới pull được.
- **PAT read:packages có expiry** — đặt nhắc nhở xoay key định kỳ (3-6 tháng).
- **SSH key cho CI**: tạo key riêng cho deploy (không dùng key personal):
  ```bash
  ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/deploy_key
  # Thêm deploy_key.pub vào authorized_keys trên VPS
  # Thêm nội dung deploy_key (private) vào GitHub Secret VPS_SSH_KEY
  ```

---

## Troubleshooting

### Xem logs container
```bash
cd ~/deploy
docker compose logs -f              # realtime
docker compose logs --tail=100      # 100 dòng gần nhất
```

### Container crash khi khởi động
Thường do thiếu biến môi trường hoặc DB không kết nối được:
```bash
docker compose logs zalo-bot | grep -E "ERROR|Thiếu|migration"
```

### Alembic migration lỗi
```
alembic.util.exc.CommandError: Can't locate revision identified by '...'
```
Kiểm tra `DATABASE_MIGRATION_URL` phải là cổng **5432** (direct), không phải 6543.

### Image không pull được (401 Unauthorized)
```bash
# Đăng nhập lại GHCR trên VPS
docker login ghcr.io -u YOUR_GITHUB_USERNAME
# Nhập PAT với scope read:packages
```

### Xem trạng thái container
```bash
docker compose ps
docker stats zalo-bot-scanner
```

### Restart thủ công
```bash
cd ~/deploy
docker compose restart
```
