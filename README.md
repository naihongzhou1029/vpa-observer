# VPA-Observer

台美股量價分析（VPA）與可視範圍分價量表（VAP）看盤系統，基於 Anna Coulling《A Complete Guide To Volume Price Analysis》的量價理論設計，結合 `FastAPI`、`SQLite` 本地快取與 `TradingView Lightweight Charts` 打造輕量即時看盤介面。

## 核心功能

- 多週期台美股與指數 K 線：支援 `5m`、`15m`、`60m`、`1d`、`1w`、`1mo`（週 K 與月 K 由日 K 依市場時區即時聚合），並支援台股紅漲綠跌與國際綠漲紅跌配色切換。
- 量能 20MA 基準與 VPA 訊號標記：自動計算每根 K 棒相對 20 期均量（`vol_ma20`）的量比，區分超大量（2.0x 以上）、高量（1.3 至 2.0x）、均量（0.7 至 1.3x）與縮量（低於 0.7x），並標記爆量長紅、爆量長黑、高量避雷針、高量下影線、量價背離等異常訊號與白色虛線 20MA 均量折線。
- 可視範圍分價量表（VAP）：隨畫面縮放與平移即時重算當前可視區間的價格成交量分佈（可調 24 / 40 / 60 格），自動標示控制點（`POC`）與 70% 價值區間（`VA70%`）。
- 觀察清單與多色標籤篩選：依台股（`TW`）與美股（`US`）分區顯示最新收盤價與漲跌幅，支援無色、紅、黃、綠多選標籤過濾，並自動整合證交所／櫃買中心與 Yahoo Finance 的台股中英文雙語名稱。
- 指數成交量自動補齊：針對 Yahoo Finance 缺漏成交量的指數，提供 `^TWII` 透過證交所盤後交易資訊（`FMTQIK`）補齊成交量，以及 `^SOX` 自動以 `SOXX` 同週期成交量回填。
- 盤中與盤後排程同步：透過 `APScheduler` 依台北（`Asia/Taipei`）與紐約（`America/New_York`）交易時段自動執行盤中與盤後資料更新，無須手動刷新。
- 一鍵部署與桌面捷徑：透過 `./devops.sh redeploy` 即可自動建立環境、重啟背景服務、更新系統應用程式捷徑（macOS `VPA-Observer.app` / Linux `.desktop`）並於預設瀏覽器開啟看盤介面。

## 技術架構

- 後端服務：`FastAPI`、`uvicorn`、`APScheduler`、`httpx`、`pydantic`
- 行情與運算：`yfinance`、`pandas`、`numpy`、`tzdata`
- 資料儲存：`SQLite`（WAL 模式，存放於 `data/vpa_observer.db`）
- 前端圖表：原生 HTML / CSS / JavaScript 搭配 `TradingView Lightweight Charts` 與 Canvas VAP 疊圖

## 快速開始

### 1. 初始化環境與資料庫

```bash
./devops.sh setup
```

### 2. 部署並開啟看盤介面

```bash
./devops.sh redeploy
```

執行後服務預設運行於 `http://127.0.0.1:8050`（可透過環境變數 `VPA_HOST` 與 `VPA_PORT` 調整），並會自動建立系統應用程式捷徑。

### 常用維運指令

- `./devops.sh start [--daemon]`：啟動後端服務（預設前景執行，加上 `--daemon` 於背景常駐）
- `./devops.sh stop`：停止背景服務
- `./devops.sh restart`：重啟背景服務
- `./devops.sh redeploy`：更新系統捷徑、重啟背景服務並於瀏覽器開啟前端頁面
- `./devops.sh status`：查看服務運行狀態、排程資訊與資料庫統計
- `./devops.sh logs`：即時追蹤背景服務日誌（`.run/vpa-observer.log`）
- `./devops.sh sync [SYMBOL]`：手動同步全部觀察清單或指定標的的各週期資料

## 代號查詢格式

系統底層串接 Yahoo Finance 行情資料，查詢框支援以下格式：

| 標的類型 | 輸入範例 | 說明 |
| --- | --- | --- |
| 台股個股 / ETF | `2330`、`6488`、`0050` | 純數字代號會自動探測 `.TW`（上市）與 `.TWO`（上櫃），亦可直接輸入 `2330.TW` |
| 美股個股 / ETF | `NVDA`、`MU`、`SPY`、`SOXX` | 直接輸入美股代號 |
| 市場指數 | `^TWII`、`^SOX`、`^GSPC`、`^IXIC`、`^DJI` | 指數類代號必須加上 `^` 前綴 |

## 專案目錄結構

- `backend/main.py`：FastAPI 入口、靜態檔案掛載與 REST API 端點（`/api/watchlist`、`/api/bars`、`/api/sync`、`/api/status`）
- `backend/fetcher.py`：代號正規化 `normalize_symbol()`、Yahoo Finance / FinMind 抓取、指數補量與週／月 K 聚合
- `backend/vpa.py`：20MA 量能分級與單根 VPA 異常訊號引擎 `analyze_vpa()`
- `backend/db.py`：SQLite 資料表初始化與觀察清單／OHLCV 讀寫
- `backend/scheduler.py`：台美股盤中與盤後自動同步排程
- `frontend/`：看盤前端頁面（`index.html`、`app.js`、`style.css`、`icon.png`）
- `devops.sh`：環境建置、服務啟停、系統捷徑安裝與資料同步腳本
