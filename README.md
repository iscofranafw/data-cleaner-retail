# Data Cleaner Retail — A Data Cleaner V3

Aplikasi audit kualitas data katalog produk, koreksi taksonomi pintar (*Smart Category & Classification Audit*), serta tata kelola pembaruan terkontrol untuk pengguna A POS/ERP.

Dirancang untuk beroperasi dalam dua mode arsitektur:
1. **Mode Lokal (Windows)**: Penyimpanan SQLite persisten di `%LOCALAPPDATA%`, analisis offline cepat, dan integrasi opsional ke model lokal Ollama.
2. **Mode Stateless Cloud (Streamlit Community Cloud)**: Berjalan di cloud container tanpa ketergantungan database eksternal (Supabase/PostgreSQL), dilengkapi autentikasi server-side, kontrol akses berbasis peran (RBAC), kunci mutasi *Strict Read-Only*, serta mekanisme pemulihan data instan berbasis Backup ZIP terverifikasi SHA-256.

---

## Fitur Utama

- **Smart Category Audit**: Mendeteksi anomali kategori produk (seperti produk makanan yang tersimpan dalam *Others*) menggunakan heuristik bobot token dan bukti produk serupa (*peer evidence*).
- **AI Taxonomy Manager & Multi-Class Classification (V3)**: Menemukan dan memetakan 60+ klasifikasi produk A secara dinamis tanpa hardcoded list. Mengidentifikasi salah klasifikasi menggunakan hybrid token matching, ekstraksi atribut (merek, ukuran, uom, varian), dan verifikasi AI lokal.
- **Controlled Reclassification & Review Workflow**: Alur persetujuan operator bertahap (`PENDING` -> `APPROVED` / `REJECTED`) dengan pencatatan audit log lengkap.
- **Disaster Recovery & Portabilitas Penuh (CloudBackupEngine)**:
  - Ekspor seluruh katalog, temuan audit, riwayat persetujuan, dan ground truth ke dalam satu berkas Backup ZIP dengan `manifest.json`.
  - Pemulihan data aman dengan validasi integritas **SHA-256**, pencegahan serangan **Zip-Slip (Directory Traversal)**, dan pencocokan **Store ID**.
- **Perlindungan Keamanan Berlapis**:
  - **Zero LIVE Mutation**: Kunci mutasi keras pada level API Client dan Service Layer memastikan katalog aktif di A terlindungi dari modifikasi tidak disengaja.
  - **Enterprise RBAC**: Pembatasan hak akses berbasis email allowlist (`Admin`, `Operator`, `Auditor`, `Viewer`).
  - **Zero Store Data Leakage**: Repositori git 100% bebas dari kredensial, token API, database SQLite operasional, maupun file CSV katalog toko.

---

## Arsitektur Deployment

```text
+--------------------------------------------------------------------------+
|                        PILIHAN ARSITEKTUR                                |
+------------------------------------+-------------------------------------+
|         Mode Lokal Windows         |         Mode Stateless Cloud        |
+------------------------------------+-------------------------------------+
| • SQLite Persisten                 | • SQLite Ephemeral (Sementara)      |
| • Folder: %LOCALAPPDATA%           | • Wadah: Streamlit Community Cloud  |
| • Digunakan harian di kasir/kantor | • Sinkronisasi on-demand via API    |
| • AI Lokal via Ollama              | • Restore instan via Full Backup ZIP|
| • Kredensial via .env              | • Kredensial via Streamlit Secrets  |
+------------------------------------+-------------------------------------+
```

---

## Panduan Instalasi Lokal (Windows)

### Prasyarat
- Python 3.11 (64-bit)
- PowerShell 5.1+ atau Windows Terminal
- Akun A POS dengan akses Open API (App ID & Secret Key)
- *(Opsional)* [Ollama](https://ollama.com/) dengan model `llama3:8b` atau `qwen2.5:3b` jika ingin mengaktifkan verifikasi AI lokal.

### Langkah Instalasi
1. Clone repositori ini atau ekstrak berkas source code:
   ```powershell
   git clone https://github.com/iscofranafw/data-cleaner-retail.git
   cd data-cleaner-retail
   ```
2. Buat dan aktifkan virtual environment:
   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Pasang seluruh dependencies:
   ```powershell
   python -m pip install --upgrade pip
   python -m pip install -r requirements.txt
   ```
4. Salin template konfigurasi `.env.example` menjadi `.env`:
   ```powershell
   Copy-Item .env.example .env
   ```
5. Buka `.env` dan masukkan kredensial API A Anda (`A_APP_ID`, `A_SECRET_KEY`, `A_EXPECTED_STORE_ID`).
6. Jalankan pengujian otomatis untuk memverifikasi instalasi:
   ```powershell
   python -m pytest -q
   ```
7. Jalankan aplikasi Streamlit:
   ```powershell
   python -m streamlit run app.py
   ```

Aplikasi akan terbuka di browser pada alamat `http://localhost:8501`.

---

## Panduan Deployment ke Streamlit Community Cloud

Aplikasi ini siap di-deploy secara langsung ke **Streamlit Community Cloud** sebagai **Private App**:

### 1. Persiapan Repositori GitHub
Pastikan repositori Anda sudah ter-push ke GitHub:
```bash
git add .
git commit -m "data cleaner retail"
git push origin main
```
*(File `.env`, `secrets.toml`, database `*.db`, dan CSV bisnis otomatis diabaikan oleh `.gitignore`)*.

### 2. Deploy Aplikasi di Streamlit Cloud
1. Kunjungi [share.streamlit.io](https://share.streamlit.io/) dan login dengan akun GitHub Anda.
2. Klik **New app**.
3. Pilih repository Anda, branch `main`, dan set Main file path ke `streamlit_app.py` (atau `app.py`).
4. **PENTING**: Pada pengaturan visibilitas aplikasi, pilih **Private** agar aplikasi tidak dapat diakses publik.

### 3. Konfigurasi Secrets (App Settings -> Secrets)
Salin konfigurasi berikut ke menu **Settings -> Secrets** di dashboard Streamlit Cloud Anda:

```toml
# ==============================================================================
# Streamlit Community Cloud Secrets (Stateless Cloud Mode)
# ==============================================================================

# Kredensial A Open API
A_APP_ID = "masukkan_app_id_A_anda"
A_SECRET_KEY = "masukkan_secret_key_A_anda"
A_BASE_URL = "https://api-open.A.co.id/api/open-api/v1"
A_EXPECTED_STORE_ID = "masukkan_store_id_anda"
A_PAGE_SIZE = 50
A_TIMEOUT_SECONDS = 30

# Mode Keamanan Cloud (Strict Read-Only)
IS_CLOUD_DEPLOYMENT = true
A_ENABLE_UPDATES = "NO"
ALLOW_CLOUD_LIVE_UPDATES = "NO"

# Autentikasi Server-Side & Allowlist Email
AUTH_ENABLED = true
ALLOWED_EMAILS = "pemilik@emailanda.com,auditor@emailanda.com,operator@emailanda.com"

# Pemetaan Hak Akses (admin, operator, auditor, viewer)
[USER_ROLES]
"pemilik@emailanda.com" = "admin"
"operator@emailanda.com" = "operator"
"auditor@emailanda.com" = "auditor"

# Database: Kosongkan untuk mode Stateless Cloud (tanpa Supabase)
# DATABASE_URL = ""

# AI Provider: DISABLED jika tidak menggunakan AI eksternal
AI_PROVIDER = "DISABLED"
ALLOW_EXTERNAL_AI_DATA_SHARING = false
```

4. Klik **Save** dan aplikasi akan memulai build otomatis.

---

## Alur Penggunaan Cloud (Stateless Workflow)

1. **Autentikasi**: Pengguna masuk menggunakan email Google yang terdaftar pada `ALLOWED_EMAILS`. Email di luar daftar akan ditolak secara otomatis (*fail closed*).
2. **Kondisi Awal**: Container cloud memulai sesi dengan SQLite kosong (`⚪ BELUM DISINKRONISASI`).
3. **Memuat Data**:
   - **Opsi 1 (Tarik Langsung)**: Buka tab **Sinkronisasi**, klik **Jalankan sinkronisasi (read-only)** untuk menarik seluruh produk aktif dari A Open API.
   - **Opsi 2 (Restore Cepat)**: Buka tab **Backup**, unggah berkas `A_backup_...zip` hasil ekspor sesi sebelumnya untuk langsung memulihkan seluruh data produk, riwayat audit, dan keputusan review.
4. **Audit & Review**: Jalankan audit kategori atau klasifikasi produk, analisis bukti kecocokan, dan berikan persetujuan/penolakan rekomendasi.
5. **Menyimpan Hasil**: Sebelum mengakhiri sesi kerja, klik tombol **📦 Unduh Full Backup ZIP Sekarang** di Dashboard untuk menyimpan data terbaru ke komputer lokal Anda.

---

## Verifikasi Kualitas & Keamanan

Seluruh fungsionalitas diuji menggunakan rangkaian pengujian regresi otomatis:
- **90 Unit & Integration Tests**: 100% lulus dalam < 6 detik (`python -m pytest`).
- **Secret Scanning**: 0 kebocoran kredensial atau token pada seluruh source code.
- **Zero-AI-Slop Gate**: Skor 100/100 (Clean, 0 defect findings).
- **Proteksi Zip-Slip**: Lulus pengujian eksploitasi traversal path dan verifikasi integritas hash SHA-256.

---

## Lisensi & Batasan

Aplikasi ini didesain khusus untuk penggunaan internal audit dan rekonsiliasi data toko A. Modul pembaruan LIVE dibatasi dengan proteksi keamanan ganda guna mencegah perubahan data katalog yang tidak terverifikasi.
