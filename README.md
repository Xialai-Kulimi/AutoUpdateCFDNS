# cfdns (v2.0) - Cloudflare DDNS 背景服務與管理工具

`cfdns` 是一個專為 Linux 設計的高可靠性 Cloudflare 動態 DNS（DDNS）自動更新背景服務與 CLI 工具。它能在本機對外 IPv4 變動時自動同步 Cloudflare 上的 A 記錄，並提供直覺的 CLI 讓管理者隨時查看狀態、新增子網域、移除網域或手動觸發同步。

---

## 🌟 核心特色

- **systemd 原生背景服務**：以一般使用者權限（`kulimi`）在背景穩定運行，日誌直接整合 systemd journald（無日誌檔案膨脹問題）。
- **完整 Unix Socket 通訊**：CLI 透過 `/run/cfdns/cfdns.sock` 與背景 daemon 溝通，秒級即時互動。
- **自動分頁支援**：完整支援 Cloudflare SDK 分頁（自動遍歷所有記錄），克服 v1 只抓第一頁的限制。
- **原子寫入與熱重載**：`config.json` 寫入保證原子性（Atomic write），手動編輯設定檔也能透過 SHA-256 指紋偵測自動熱重載（Hot reload）。
- **智慧對帳機制**：即使 IP 長時間未變動，定期強制對帳（`reconcile_interval`），修復 Cloudflare 端被手動修改的漂移記錄。
- **安全防護**：
  - 嚴格校驗公網 IPv4，自動過濾私有 IP（RFC 1918）、Loopback、Link-local、Multicast 等無效位址。
  - 嚴格防範 CNAME 與 A 記錄衝突。
  - 移除記錄時，僅刪除指向當前主機的 A 記錄，避免誤刪其他主機的記錄。
  - 所有輸出、狀態、日誌與回傳資料**絕不洩漏 `API_KEY` 明文**。

---

## 📦 安裝與建置

本專案建議使用 [`uv`](https://github.com/astral-sh/uv) 進行虛擬環境與套件管理：

```bash
# 1. 建立虛擬環境
uv venv .venv

# 2. 安裝套件（包含 cfdns 執行檔）
uv pip install -e .

# 3. 測試 CLI
.venv/bin/cfdns --version
.venv/bin/cfdns --help
```

---

## ⚙️ 設定檔說明

專案需要兩個設定檔（位於同一目錄）：

### 1. `.env`
存放 Cloudflare API 憑證與可選的本機 IP 取得指令：
```ini
API_KEY=your_cloudflare_global_api_key
API_EMAIL=your_email@example.com
# 可選：若未設定則自動向 https://1.1.1.1/cdn-cgi/trace 查詢公網 IP
IP_COMMAND=ip -4 addr show ppp0 | grep -oP '(?<=inet\s)\d+(\.\d+){3}'
```

### 2. `config.json`
DNS 管理設定（相容 v1 欄位）：
```json
{
    "zone_id": "your_cloudflare_zone_id",
    "records_to_update": [
        "example.com",
        "app.example.com"
    ],
    "debug": false,
    "interval": 30,
    "reconcile_interval": 3600,
    "default_ttl": 1,
    "default_proxied": true
}
```

| 欄位名稱 | 型別 | 預設值 | 說明 |
|---|---|---|---|
| `zone_id` | 字串 | *(必填)* | Cloudflare Zone ID |
| `records_to_update` | 字串陣列 | *(必填)* | 要自動更新的 A 記錄網域名稱清單 |
| `debug` | 布林值 | `false` | 若為 `true`，所有 Cloudflare 寫入操作只會標註 `[DRY-RUN]`，不會真正更動 Cloudflare |
| `interval` | 整數 | `30` | 輪詢 IP 週期（秒），最小值為 5 |
| `reconcile_interval`| 整數 | `3600` | 強制對帳間隔（秒）。0 代表關閉 |
| `default_ttl` | 整數 | `1` | 新增記錄時的 TTL（1 代表 Cloudflare Auto） |
| `default_proxied` | 布林值 | `true` | 新增記錄時是否啟用 Cloudflare Proxy（橘色雲朵） |

---

## 💻 CLI 使用手冊

安裝後可在終端機執行 `cfdns`（或 `.venv/bin/cfdns`）：

### 查看服務狀態
```bash
cfdns status
```
顯示 daemon 運行狀態、目前偵測 IP、上次同步 IP、監控網域數量等。若 daemon 未啟動會明確提示並回傳 Exit Code 3。

### 查看 Cloudflare 記錄對帳狀態
```bash
cfdns list
```
顯示所有追蹤網域在 Cloudflare 上的實際解析內容與狀態：
- `ok`（綠色）：解析 IP 與目前本機 IP 相符
- `stale`（黃色）：解析 IP 與本機 IP 不一致
- `missing`（紅色）：追蹤中的網域名稱在 Cloudflare 上不存在

### 新增網域
```bash
# 短名稱自動補全為當前 zone 子網域，並指向本機目前公網 IP
cfdns add app

# 啟用 Cloudflare Proxy (橘色雲朵)
cfdns add web --proxied

# 指定 TTL（秒）
cfdns add api --ttl 300

# 若該名稱已存在且指向其他 IP，需加上 --force 才能覆寫
cfdns add myservice --force
```

### 移除網域
```bash
# 從自動更新追蹤清單中移除
cfdns remove app

# 從追蹤清單移除，並同時刪除 Cloudflare 上的 A 記錄（需互動確認或加 -y）
cfdns remove app --delete-record -y
```

### 手動觸發同步
```bash
# 立即對帳並同步
cfdns sync

# 指定 IP 同步（供 /etc/ppp/ip-up.d hook 使用）
cfdns sync --ip 1.164.24.68
```

### 查看目前設定
```bash
cfdns config
# 若 daemon 未執行，會自動 fallback 讀取本地 config.json
```

### 查看服務日誌
```bash
cfdns logs -f
```

---

## 🚀 部署為 systemd 背景服務

專案內建 `deploy/` 目錄提供完整部署腳本：

1. **`deploy/cfdns.service`**：systemd unit 檔案，以使用者 `kulimi` 執行背景 daemon。
2. **`deploy/ppp-ip-up-cfdns`**：ppp 連線成功時自動呼叫 `cfdns sync --ip "$4"` 的 hook。
3. **`deploy/install.sh`**：自動化安裝腳本（需要 root 權限）。

### 安裝步驟：
```bash
# 1. 先確認非 root 身份下 .venv 已建置完成
uv venv && uv pip install -e .

# 2. 執行安裝腳本
sudo bash deploy/install.sh
```

---

## 🔄 從 v1 遷移指南

若系統上已有正在運行的 v1（supervisord / main.py）：

1. **確認新版環境就緒**：
   ```bash
   .venv/bin/pytest -q
   ```
2. **執行安裝腳本建立軟連結與 service**：
   ```bash
   sudo bash deploy/install.sh
   ```
3. **停止並移除舊版 supervisord 程式**：
   ```bash
   sudo supervisorctl stop AutoUpdateDNS
   # 移除舊的 supervisor 設定檔並 reload
   sudo rm /etc/supervisor/conf.d/autoUpdateDNS.conf
   sudo supervisorctl update
   ```
4. **移除舊版 ppp hook**：
   ```bash
   sudo rm -f /etc/ppp/ip-up.d/kulimi_tw_update
   ```
5. **啟動新版 cfdns systemd 服務**：
   ```bash
   sudo systemctl enable --now cfdns
   ```
6. **驗證新版服務**：
   ```bash
   cfdns status
   cfdns list
   cfdns logs -n 20
   ```

---

## 🧪 測試

專案使用 pytest 進行單元測試與端到端 socket 通訊測試：

```bash
.venv/bin/pytest -q
```
全部 44 項測試皆不依賴外網，快速驗證所有邏輯覆蓋。
