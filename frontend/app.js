(() => {
  const TF_LABELS = {
    "5m": "5m",
    "15m": "15m",
    "60m": "60m",
    "1d": "日K",
    "1w": "週K",
    "1mo": "月K",
  };

  const GRADE_LABELS = {
    ultra_high: "超大量",
    high: "高量",
    normal: "均量",
    low: "縮量",
  };

  const state = {
    symbol: "2330.TW",
    name: "台積電",
    market: "TW",
    interval: "1d",
    twColorMode: true, // true: 紅漲綠跌, false: 綠漲紅跌
    showMarkers: true,
    showVap: true,
    vapBins: 40,
    bars: [],
    barMap: new Map(),
    markers: [],
    watchlist: [],
    activeTagFilters: new Set(),
  };

  // DOM references
  const chartContainer = document.getElementById("chart-container");
  const vapCanvas = document.getElementById("vap-canvas");
  const vapCtx = vapCanvas.getContext("2d");
  const sidebar = document.getElementById("sidebar");
  const watchlistEl = document.getElementById("watchlist-container");
  const tagFilterBar = document.getElementById("tag-filter-bar");
  const symbolForm = document.getElementById("symbol-form");
  const symbolInput = document.getElementById("symbol-input");
  const btnAddWatchlist = document.getElementById("btn-add-watchlist");
  const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
  const btnColorScheme = document.getElementById("btn-color-scheme");
  const btnSync = document.getElementById("btn-sync");
  const chkVpaMarkers = document.getElementById("chk-vpa-markers");
  const chkVap = document.getElementById("chk-vap");
  const selectVapBins = document.getElementById("vap-bins");
  const tfGroup = document.getElementById("tf-group");
  const loadingOverlay = document.getElementById("loading-overlay");
  const loadingText = document.getElementById("loading-text");
  const vapReadout = document.getElementById("vap-readout");
  const toastEl = document.getElementById("toast");

  const lgSymbol = document.getElementById("lg-symbol");
  const lgName = document.getElementById("lg-name");
  const lgTf = document.getElementById("lg-tf");
  const lgTime = document.getElementById("lg-time");
  const lgOhlc = document.getElementById("lg-ohlc");
  const lgVpa = document.getElementById("lg-vpa");

  let toastTimer = null;
  function showToast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.add("hidden"), 3200);
  }

  function setLoading(active, msg = "載入中...") {
    loadingText.textContent = msg;
    loadingOverlay.classList.toggle("hidden", !active);
  }

  function getPalette() {
    if (state.twColorMode) {
      return {
        up: "#ef4444",
        down: "#22c55e",
        upSoft: "rgba(239, 68, 68, 0.42)",
        downSoft: "rgba(34, 197, 94, 0.42)",
        upVap: "rgba(239, 68, 68, 0.46)",
        downVap: "rgba(34, 197, 94, 0.42)",
      };
    }
    return {
      up: "#22c55e",
      down: "#ef4444",
      upSoft: "rgba(34, 197, 94, 0.42)",
      downSoft: "rgba(239, 68, 68, 0.42)",
      upVap: "rgba(34, 197, 94, 0.46)",
      downVap: "rgba(239, 68, 68, 0.42)",
    };
  }

  function volumeColorForBar(bar) {
    if (bar.volume_grade === "ultra_high") {
      return "#e040fb";
    }
    if (bar.volume_grade === "high") {
      return "#f59e0b";
    }
    if (bar.volume_grade === "normal") {
      return "rgba(56, 189, 248, 0.72)";
    }
    return "rgba(92, 107, 127, 0.45)";
  }

  function markerColorForGrade(grade) {
    if (grade === "ultra_high") return "#e040fb";
    if (grade === "high") return "#f59e0b";
    if (grade === "normal") return "#38bdf8";
    return "#5c6b7f";
  }

  // Initialize Lightweight Charts
  const chart = LightweightCharts.createChart(chartContainer, {
    layout: {
      background: { type: "solid", color: "#0b0f17" },
      textColor: "#9ca3af",
      fontSize: 17,
    },
    grid: {
      vertLines: { color: "rgba(36, 48, 71, 0.45)" },
      horzLines: { color: "rgba(36, 48, 71, 0.45)" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "rgba(56, 189, 248, 0.4)", labelBackgroundColor: "#1f2937" },
      horzLine: { color: "rgba(56, 189, 248, 0.4)", labelBackgroundColor: "#1f2937" },
    },
    rightPriceScale: {
      borderColor: "#243047",
      scaleMargins: { top: 0.08, bottom: 0.26 },
    },
    timeScale: {
      borderColor: "#243047",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 10,
    },
    localization: {
      timeFormatter: (ts) => formatTimestamp(ts, state.interval, state.market),
    },
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#ef4444",
    downColor: "#22c55e",
    borderUpColor: "#ef4444",
    borderDownColor: "#22c55e",
    wickUpColor: "#ef4444",
    wickDownColor: "#22c55e",
  });

  const volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });

  volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.78, bottom: 0.0 },
  });

  function formatTimestamp(tsSec, interval, market) {
    const dt = new Date(tsSec * 1000);
    const tz = market === "TW" ? "Asia/Taipei" : "America/New_York";
    const isIntraday = interval === "5m" || interval === "15m" || interval === "60m";
    const opts = isIntraday
      ? {
          timeZone: tz,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        }
      : {
          timeZone: tz,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        };
    return new Intl.DateTimeFormat("zh-TW", opts).format(dt);
  }

  function formatVolume(vol) {
    if (vol >= 1e8) return (vol / 1e8).toFixed(2) + "億";
    if (vol >= 1e4) return (vol / 1e4).toFixed(1) + "萬";
    return Math.round(vol).toLocaleString();
  }

  function formatPrice(val) {
    if (val == null) return "--";
    return val >= 100 ? val.toFixed(2) : val.toFixed(3).replace(/0$/, "");
  }

  function updateLegend(bar) {
    lgSymbol.textContent = state.symbol;
    lgName.textContent = state.name && state.name !== state.symbol ? state.name : "";
    lgTf.textContent = TF_LABELS[state.interval] || state.interval;

    if (!bar) {
      lgTime.textContent = "";
      lgOhlc.textContent = "無資料";
      lgVpa.innerHTML = "";
      return;
    }

    const idx = state.bars.indexOf(bar);
    const prevClose = idx > 0 ? state.bars[idx - 1].close : bar.open;
    const chg = prevClose > 0 ? ((bar.close - prevClose) / prevClose) * 100 : 0;
    const isUp = bar.close >= prevClose;
    const palette = getPalette();
    const chgColor = isUp ? palette.up : palette.down;

    lgTime.textContent = formatTimestamp(bar.time, state.interval, state.market);
    lgOhlc.innerHTML = `
      <span>開 <b>${formatPrice(bar.open)}</b></span>
      <span>高 <b>${formatPrice(bar.high)}</b></span>
      <span>低 <b>${formatPrice(bar.low)}</b></span>
      <span>收 <b style="color:${chgColor}">${formatPrice(bar.close)}</b></span>
      <span style="color:${chgColor}">${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%</span>
      <span>量 <b>${formatVolume(bar.volume)}</b></span>
      <span>量比 <b>${bar.vol_ratio.toFixed(2)}x</b></span>
      <span>幅比 <b>${bar.spread_ratio.toFixed(2)}x</b></span>
    `;

    const gradeText = GRADE_LABELS[bar.volume_grade] || bar.volume_grade;
    let vpaHtml = `<span class="vpa-badge ${bar.volume_grade}">量能: ${gradeText}</span>`;
    if (bar.signal_label) {
      vpaHtml += `<span class="vpa-signal-pill ${bar.volume_grade}">${bar.signal_label}</span>`;
      vpaHtml += `<span class="vpa-desc">${bar.signal_desc}</span>`;
    }
    lgVpa.innerHTML = vpaHtml;
  }

  function applyChartSeriesData() {
    const palette = getPalette();
    candleSeries.applyOptions({
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
    });

    const candleData = state.bars.map((b) => ({
      time: b.time,
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    }));
    const volumeData = state.bars.map((b) => ({
      time: b.time,
      value: b.volume,
      color: volumeColorForBar(b),
    }));

    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);

    if (state.showMarkers) {
      const styledMarkers = state.markers.map((m) => {
        const bar = state.barMap.get(m.time);
        const grade = m.volume_grade || (bar ? bar.volume_grade : "normal");
        return {
          time: m.time,
          position: m.position,
          color: markerColorForGrade(grade),
          shape: m.position === "belowBar" ? "arrowUp" : "arrowDown",
          text: "",
          size: 1,
        };
      });
      candleSeries.setMarkers(styledMarkers);
    } else {
      candleSeries.setMarkers([]);
    }

    if (state.bars.length > 0) {
      updateLegend(state.bars[state.bars.length - 1]);
    } else {
      updateLegend(null);
    }
  }

  // ---------------- VAP (Volume At Price) Overlay ----------------
  function computeVisibleVap(visibleBars, numBins) {
    if (!visibleBars.length) return null;

    let minPrice = Infinity;
    let maxPrice = -Infinity;
    for (const b of visibleBars) {
      if (b.low < minPrice) minPrice = b.low;
      if (b.high > maxPrice) maxPrice = b.high;
    }
    if (!Number.isFinite(minPrice) || !Number.isFinite(maxPrice) || maxPrice <= minPrice) {
      return null;
    }

    const binSize = (maxPrice - minPrice) / numBins;
    const bins = Array.from({ length: numBins }, (_, i) => ({
      low: minPrice + i * binSize,
      high: minPrice + (i + 1) * binSize,
      mid: minPrice + (i + 0.5) * binSize,
      upVol: 0,
      downVol: 0,
      totalVol: 0,
    }));

    for (const b of visibleBars) {
      if (b.volume <= 0) continue;
      const startBin = Math.max(0, Math.min(numBins - 1, Math.floor((b.low - minPrice) / binSize)));
      const endBin = Math.max(0, Math.min(numBins - 1, Math.floor((b.high - minPrice) / binSize)));
      const typical = (b.high + b.low + b.close) / 3;
      const isUp = b.close >= b.open;

      // Triangular weight peaking at typical price for smoother realistic distribution
      let weightSum = 0;
      const weights = [];
      for (let i = startBin; i <= endBin; i++) {
        const dist = Math.abs(bins[i].mid - typical);
        const span = Math.max(b.high - b.low, binSize);
        const w = Math.max(0.15, 1.0 - dist / (span + binSize));
        weights.push(w);
        weightSum += w;
      }

      for (let idx = 0, i = startBin; i <= endBin; i++, idx++) {
        const alloc = b.volume * (weights[idx] / weightSum);
        if (isUp) bins[i].upVol += alloc;
        else bins[i].downVol += alloc;
        bins[i].totalVol += alloc;
      }
    }

    let maxBinVol = 0;
    let pocIdx = 0;
    let totalAllVol = 0;
    for (let i = 0; i < bins.length; i++) {
      totalAllVol += bins[i].totalVol;
      if (bins[i].totalVol > maxBinVol) {
        maxBinVol = bins[i].totalVol;
        pocIdx = i;
      }
    }
    if (maxBinVol <= 0 || totalAllVol <= 0) return null;

    // 70% Value Area expansion from POC
    let vaVol = bins[pocIdx].totalVol;
    let lo = pocIdx;
    let hi = pocIdx;
    const targetVaVol = totalAllVol * 0.7;
    while (vaVol < targetVaVol && (lo > 0 || hi < bins.length - 1)) {
      const leftVol = lo > 0 ? bins[lo - 1].totalVol : -1;
      const rightVol = hi < bins.length - 1 ? bins[hi + 1].totalVol : -1;
      if (rightVol >= leftVol) {
        hi++;
        vaVol += bins[hi].totalVol;
      } else {
        lo--;
        vaVol += bins[lo].totalVol;
      }
    }

    return {
      bins,
      maxBinVol,
      pocPrice: bins[pocIdx].mid,
      vahPrice: bins[hi].high,
      valPrice: bins[lo].low,
    };
  }

  let vapRaf = null;
  function scheduleVapRedraw() {
    if (vapRaf) cancelAnimationFrame(vapRaf);
    vapRaf = requestAnimationFrame(drawVap);
  }

  function drawVap() {
    const dpr = window.devicePixelRatio || 1;
    const width = chartContainer.clientWidth;
    const height = chartContainer.clientHeight;
    if (vapCanvas.width !== Math.floor(width * dpr) || vapCanvas.height !== Math.floor(height * dpr)) {
      vapCanvas.width = Math.floor(width * dpr);
      vapCanvas.height = Math.floor(height * dpr);
      vapCanvas.style.width = `${width}px`;
      vapCanvas.style.height = `${height}px`;
    }

    vapCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    vapCtx.clearRect(0, 0, width, height);

    if (!state.showVap || state.bars.length === 0) {
      vapReadout.classList.add("hidden");
      return;
    }

    const logicalRange = chart.timeScale().getVisibleLogicalRange();
    if (!logicalRange) return;

    const fromIdx = Math.max(0, Math.floor(logicalRange.from));
    const toIdx = Math.min(state.bars.length - 1, Math.ceil(logicalRange.to));
    if (toIdx < fromIdx) return;

    const visibleBars = state.bars.slice(fromIdx, toIdx + 1);
    const vap = computeVisibleVap(visibleBars, state.vapBins);
    if (!vap) {
      vapReadout.classList.add("hidden");
      return;
    }

    const priceScaleWidth = chart.priceScale("right").width() || 80;
    const plotRight = Math.max(140, width - priceScaleWidth);
    const maxBarWidth = Math.min(220, plotRight * 0.22);
    const palette = getPalette();

    // Draw horizontal price profile bars anchored to the right side of plot area
    for (const bin of vap.bins) {
      if (bin.totalVol <= 0) continue;
      const yTop = candleSeries.priceToCoordinate(bin.high);
      const yBot = candleSeries.priceToCoordinate(bin.low);
      if (yTop == null || yBot == null) continue;

      const topPx = Math.min(yTop, yBot);
      const barH = Math.max(1.5, Math.abs(yBot - yTop) - 1);
      const totalW = (bin.totalVol / vap.maxBinVol) * maxBarWidth;
      const upW = (bin.upVol / bin.totalVol) * totalW;
      const downW = totalW - upW;

      // Up volume segment (rightmost) and Down volume segment (adjacent left)
      vapCtx.fillStyle = palette.upVap;
      vapCtx.fillRect(plotRight - upW, topPx, upW, barH);

      vapCtx.fillStyle = palette.downVap;
      vapCtx.fillRect(plotRight - totalW, topPx, downW, barH);
    }

    // Draw VAH / VAL dashed lines
    const yVah = candleSeries.priceToCoordinate(vap.vahPrice);
    const yVal = candleSeries.priceToCoordinate(vap.valPrice);
    vapCtx.save();
    vapCtx.setLineDash([4, 4]);
    vapCtx.strokeStyle = "rgba(148, 163, 184, 0.45)";
    vapCtx.lineWidth = 1;
    for (const yLine of [yVah, yVal]) {
      if (yLine != null) {
        vapCtx.beginPath();
        vapCtx.moveTo(plotRight - maxBarWidth * 1.25, yLine);
        vapCtx.lineTo(plotRight, yLine);
        vapCtx.stroke();
      }
    }

    // Draw POC golden dashed line across the chart
    const yPoc = candleSeries.priceToCoordinate(vap.pocPrice);
    if (yPoc != null) {
      vapCtx.setLineDash([6, 4]);
      vapCtx.strokeStyle = "#f59e0b";
      vapCtx.lineWidth = 1.5;
      vapCtx.beginPath();
      vapCtx.moveTo(0, yPoc);
      vapCtx.lineTo(plotRight, yPoc);
      vapCtx.stroke();

      vapCtx.fillStyle = "#f59e0b";
      vapCtx.font = "600 16px -apple-system, sans-serif";
      vapCtx.textAlign = "right";
      vapCtx.fillText(`POC ${formatPrice(vap.pocPrice)}`, plotRight - 10, yPoc - 6);
    }
    vapCtx.restore();

    vapReadout.classList.remove("hidden");
    vapReadout.style.right = `${priceScaleWidth + 12}px`;
    vapReadout.innerHTML = `
      <span style="color:#f59e0b;font-weight:600">POC ${formatPrice(vap.pocPrice)}</span>
      · VA70% [${formatPrice(vap.valPrice)} - ${formatPrice(vap.vahPrice)}]
    `;
  }

  chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleVapRedraw);
  chart.subscribeCrosshairMove((param) => {
    if (!param || !param.time) {
      if (state.bars.length) updateLegend(state.bars[state.bars.length - 1]);
      return;
    }
    const bar = state.barMap.get(param.time);
    if (bar) updateLegend(bar);
  });

  const resizeObserver = new ResizeObserver(() => {
    chart.applyOptions({
      width: chartContainer.clientWidth,
      height: chartContainer.clientHeight,
    });
    scheduleVapRedraw();
  });
  resizeObserver.observe(chartContainer);

  // ---------------- Data Loading & Watchlist ----------------
  async function fetchJson(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      let detail = resp.statusText;
      try {
        const body = await resp.json();
        detail = body.detail || detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return resp.json();
  }

  function matchesTagFilters(item) {
    if (state.activeTagFilters.size === 0) return true;
    const tags = Array.isArray(item.tags) ? item.tags : [];
    if (state.activeTagFilters.has("none") && tags.length === 0) {
      return true;
    }
    return tags.some((t) => state.activeTagFilters.has(t));
  }

  function renderWatchlist() {
    const palette = getPalette();
    const filtered = state.watchlist.filter(matchesTagFilters);
    const twItems = filtered.filter((i) => i.market === "TW");
    const usItems = filtered.filter((i) => i.market !== "TW");

    const buildGroup = (title, items) => {
      if (!items.length) return "";
      const rows = items
        .map((item) => {
          const isActive = item.symbol === state.symbol ? "active" : "";
          const priceStr = item.last_close != null ? formatPrice(item.last_close) : "--";
          const chg = item.change_pct;
          let chgStr = "--";
          let chgColor = "#6b7280";
          if (chg != null) {
            chgStr = `${chg >= 0 ? "+" : ""}${chg.toFixed(2)}%`;
            chgColor = chg >= 0 ? palette.up : palette.down;
          }
          const tags = Array.isArray(item.tags) ? item.tags : [];
          const isNone = tags.length === 0;
          const tagBtns = [
            { key: "none", label: "無", cls: "tag-none", active: isNone },
            { key: "red", label: "紅", cls: "tag-red", active: tags.includes("red") },
            { key: "yellow", label: "黃", cls: "tag-yellow", active: tags.includes("yellow") },
            { key: "green", label: "綠", cls: "tag-green", active: tags.includes("green") },
          ]
            .map(
              (t) =>
                `<button type="button" class="tag-pill wl-tag-btn ${t.cls} ${t.active ? "active" : ""}" data-tag-sym="${item.symbol}" data-tag-val="${t.key}">${t.label}</button>`
            )
            .join("");

          return `
            <div class="wl-item ${isActive}" data-symbol="${item.symbol}">
              <div class="wl-row-top">
                <div class="wl-left">
                  <span class="wl-symbol">${item.symbol}</span>
                  <span class="wl-name">${item.name || item.symbol}</span>
                </div>
                <div class="wl-right">
                  <div class="wl-price-col">
                    <div class="wl-price">${priceStr}</div>
                    <div class="wl-chg" style="color:${chgColor}">${chgStr}</div>
                  </div>
                  <button class="wl-del" data-del="${item.symbol}" title="從觀察清單移除">×</button>
                </div>
              </div>
              <div class="wl-tags-row">${tagBtns}</div>
            </div>
          `;
        })
        .join("");
      return `<div class="wl-group-title">${title}</div>${rows}`;
    };

    const html = buildGroup("台股 TW", twItems) + buildGroup("美股 US", usItems);
    watchlistEl.innerHTML = html || `<div class="wl-empty">沒有符合篩選標籤的標的</div>`;

    watchlistEl.querySelectorAll(".wl-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.dataset.del || e.target.dataset.tagVal) return;
        loadChart(el.dataset.symbol, state.interval);
      });
    });

    watchlistEl.querySelectorAll(".wl-tag-btn").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const sym = btn.dataset.tagSym;
        const tagVal = btn.dataset.tagVal;
        const item = state.watchlist.find((i) => i.symbol === sym);
        if (!item) return;

        let nextTags;
        if (tagVal === "none") {
          nextTags = [];
        } else {
          const cur = new Set(Array.isArray(item.tags) ? item.tags : []);
          if (cur.has(tagVal)) cur.delete(tagVal);
          else cur.add(tagVal);
          nextTags = Array.from(cur);
        }

        try {
          const res = await fetchJson(`/api/watchlist/${encodeURIComponent(sym)}/tags`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tags: nextTags }),
          });
          item.tags = res.item.tags;
          renderWatchlist();
        } catch (err) {
          showToast(`更新標籤失敗: ${err.message}`);
        }
      });
    });

    watchlistEl.querySelectorAll(".wl-del").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const sym = btn.dataset.del;
        try {
          await fetchJson(`/api/watchlist/${encodeURIComponent(sym)}`, { method: "DELETE" });
          showToast(`已移除 ${sym}`);
          await loadWatchlist();
        } catch (err) {
          showToast(`移除失敗: ${err.message}`);
        }
      });
    });
  }

  async function loadWatchlist() {
    const data = await fetchJson("/api/watchlist");
    state.watchlist = data.items || [];
    renderWatchlist();
  }

  async function loadChart(symbol, interval) {
    setLoading(true, `載入 ${symbol} (${TF_LABELS[interval] || interval})...`);
    try {
      const data = await fetchJson(
        `/api/bars?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(interval)}`
      );
      state.symbol = data.symbol;
      state.name = data.name;
      state.market = data.market;
      state.interval = data.interval;
      state.bars = data.bars || [];
      state.markers = data.vpa_markers || [];
      state.barMap = new Map(state.bars.map((b) => [b.time, b]));

      // Adjust time axis visibility for daily vs intraday
      const isIntraday = interval === "5m" || interval === "15m" || interval === "60m";
      chart.applyOptions({
        timeScale: { timeVisible: isIntraday },
      });

      applyChartSeriesData();
      chart.timeScale().fitContent();
      // Zoom to latest 160 bars if there are many bars for comfortable browsing
      if (state.bars.length > 160) {
        chart.timeScale().setVisibleLogicalRange({
          from: state.bars.length - 160,
          to: state.bars.length + 5,
        });
      }
      scheduleVapRedraw();
      renderWatchlist();
    } catch (err) {
      showToast(`載入失敗: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  // ---------------- Event Handlers ----------------
  tagFilterBar.querySelectorAll(".tag-pill").forEach((btn) => {
    btn.addEventListener("click", () => {
      const filterKey = btn.dataset.filter;
      if (state.activeTagFilters.has(filterKey)) {
        state.activeTagFilters.delete(filterKey);
        btn.classList.remove("active");
      } else {
        state.activeTagFilters.add(filterKey);
        btn.classList.add("active");
      }
      renderWatchlist();
    });
  });

  symbolForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const val = symbolInput.value.trim();
    if (!val) return;
    loadChart(val, state.interval);
  });

  btnAddWatchlist.addEventListener("click", async () => {
    const val = (symbolInput.value.trim() || state.symbol).trim();
    if (!val) return;
    setLoading(true, `正在加入 ${val} 並同步完整週期資料...`);
    try {
      const res = await fetchJson("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: val }),
      });
      showToast(`已加入自選清單: ${res.item.symbol}`);
      await loadWatchlist();
      await loadChart(res.item.symbol, state.interval);
    } catch (err) {
      showToast(`加入失敗: ${err.message}`);
      setLoading(false);
    }
  });

  tfGroup.querySelectorAll(".tf-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      tfGroup.querySelectorAll(".tf-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadChart(state.symbol, btn.dataset.tf);
    });
  });

  chkVpaMarkers.addEventListener("change", () => {
    state.showMarkers = chkVpaMarkers.checked;
    applyChartSeriesData();
  });

  chkVap.addEventListener("change", () => {
    state.showVap = chkVap.checked;
    scheduleVapRedraw();
  });

  selectVapBins.addEventListener("change", () => {
    state.vapBins = parseInt(selectVapBins.value, 10) || 40;
    scheduleVapRedraw();
  });

  btnColorScheme.addEventListener("click", () => {
    state.twColorMode = !state.twColorMode;
    btnColorScheme.textContent = state.twColorMode ? "台股紅漲" : "國際綠漲";
    applyChartSeriesData();
    scheduleVapRedraw();
    renderWatchlist();
  });

  btnToggleSidebar.addEventListener("click", () => {
    sidebar.classList.toggle("collapsed");
    setTimeout(() => {
      chart.applyOptions({
        width: chartContainer.clientWidth,
        height: chartContainer.clientHeight,
      });
      scheduleVapRedraw();
    }, 220);
  });

  btnSync.addEventListener("click", async () => {
    setLoading(true, `同步 ${state.symbol} 最新資料...`);
    try {
      await fetchJson("/api/sync", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: state.symbol, full: false }),
      });
      showToast(`${state.symbol} 同步完成`);
      await loadWatchlist();
      await loadChart(state.symbol, state.interval);
    } catch (err) {
      showToast(`同步失敗: ${err.message}`);
      setLoading(false);
    }
  });

  // Boot
  (async function init() {
    await loadWatchlist();
    if (state.watchlist.length > 0) {
      state.symbol = state.watchlist[0].symbol;
    }
    await loadChart(state.symbol, state.interval);
  })();
})();
