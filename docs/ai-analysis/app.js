// AI 个股分析 — 前端逻辑
// 与后端 API 协议:
//   GET ${BACKEND_BASE}/api/analyze?q=<query>
//     200 → {ticker, name, markdown, fetched_endpoints, tokens_used, generated_at}
//     4xx/5xx → {error, message}
//   GET ${BACKEND_BASE}/api/health
//     {status: "ok", model: "...", version: "..."}

(function () {
  "use strict";

  // ---------- 后端 URL 配置 ----------
  // 优先级:window 注入 → localStorage 覆盖 → 编译期占位符
  // 本地调试:localStorage.setItem('__BACKEND_BASE', 'http://localhost:8000')
  const BACKEND_BASE =
    window.__BACKEND_BASE_OVERRIDE ||
    localStorage.getItem("__BACKEND_BASE") ||
    "https://rendering-theater-wok.ngrok-free.dev";

  // ---------- DOM ----------
  // 所有 id 加 ai- 前缀,避免被某些浏览器扩展(Sonner toast 等)
  // 用 id="error" / id="loading" 等通用名注入样式,挤垮布局
  const $input = document.getElementById("ai-query-input");
  const $btn = document.getElementById("ai-analyze-btn");
  const $loading = document.getElementById("ai-loading");
  const $error = document.getElementById("ai-error-box");
  const $errorTitle = document.getElementById("ai-error-title");
  const $errorDetail = document.getElementById("ai-error-detail");
  const $resultWrap = document.getElementById("ai-result-wrapper");
  const $resultName = document.getElementById("ai-result-name");
  const $resultTicker = document.getElementById("ai-result-ticker");
  const $resultContent = document.getElementById("ai-result-content");
  const $metaTime = document.getElementById("ai-meta-time");
  const $metaEndpoints = document.getElementById("ai-meta-endpoints");
  const $metaTokens = document.getElementById("ai-meta-tokens");
  const $serviceDown = document.getElementById("ai-service-down");
  const $chips = document.querySelectorAll(".example-chip");

  // markdown-it 实例(打开 linkify / 表格 / 安全 HTML 关闭)
  const md = window.markdownit({
    html: false,
    linkify: true,
    breaks: false,
    typographer: false,
  });

  // ---------- 工具 ----------
  function show($el) {
    $el.classList.remove("hidden");
  }
  function hide($el) {
    $el.classList.add("hidden");
  }

  function setLoading(isLoading) {
    $btn.disabled = isLoading;
    $btn.textContent = isLoading ? "分析中…" : "分析";
    $input.disabled = isLoading;
    if (isLoading) {
      show($loading);
      hide($error);
      hide($resultWrap);
    } else {
      hide($loading);
    }
  }

  function showError(title, detail) {
    $errorTitle.textContent = title;
    $errorDetail.textContent = detail || "";
    show($error);
    hide($resultWrap);
    hide($loading);
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      const pad = (n) => String(n).padStart(2, "0");
      return (
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
        `${pad(d.getHours())}:${pad(d.getMinutes())}`
      );
    } catch (_e) {
      return iso;
    }
  }

  function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    if (typeof n === "number" && n >= 1000) {
      return n.toLocaleString("en-US");
    }
    return String(n);
  }

  // ---------- 健康检查 ----------
  async function checkHealth() {
    if (BACKEND_BASE === "REPLACE_WITH_TUNNEL_URL") {
      // 占位符 → 后端 URL 还没配,直接显示离线提示
      show($serviceDown);
      return false;
    }
    try {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), 5000);
      const res = await fetch(`${BACKEND_BASE}/api/health`, {
        method: "GET",
        headers: { "ngrok-skip-browser-warning": "1" },
        signal: ctrl.signal,
      });
      clearTimeout(timer);
      if (!res.ok) {
        show($serviceDown);
        return false;
      }
      const data = await res.json().catch(() => ({}));
      if (data && data.status === "ok") {
        hide($serviceDown);
        return true;
      }
      show($serviceDown);
      return false;
    } catch (_e) {
      show($serviceDown);
      return false;
    }
  }

  // ---------- 主流程:发起分析 ----------
  async function analyze(query) {
    const q = (query || "").trim();
    if (!q) {
      showError("请输入股票代码或名称", "例如 600519、贵州茅台、成都银行");
      return;
    }

    if (BACKEND_BASE === "REPLACE_WITH_TUNNEL_URL") {
      showError(
        "AI 分析服务暂未启动",
        "后端地址尚未配置。当前网站静态部分(价值名单、每日报告)仍可访问。"
      );
      show($serviceDown);
      return;
    }

    setLoading(true);

    try {
      const url = `${BACKEND_BASE}/api/analyze?q=${encodeURIComponent(q)}`;
      const ctrl = new AbortController();
      // LLM 调用可能 30s+,前端给 60s
      const timer = setTimeout(() => ctrl.abort(), 60000);

      const res = await fetch(url, {
        method: "GET",
        headers: {
          Accept: "application/json",
          "ngrok-skip-browser-warning": "1",
        },
        signal: ctrl.signal,
      });
      clearTimeout(timer);

      let payload;
      try {
        payload = await res.json();
      } catch (_e) {
        payload = null;
      }

      if (!res.ok) {
        // 按状态码分发友好提示
        if (res.status === 404) {
          showError(
            `找不到代码/名称 "${q}"`,
            "请检查输入。支持 6 位 A 股代码(如 600519)或股票简称(如 贵州茅台)。"
          );
        } else if (res.status === 502 || res.status === 503) {
          showError(
            "DeepSeek 暂时不可用",
            (payload && payload.message) ||
              "大模型服务返回了错误,请稍后再试。"
          );
        } else if (res.status === 429) {
          showError(
            "请求过于频繁",
            "服务正忙,请稍等几秒再试。"
          );
        } else {
          showError(
            `请求失败 (HTTP ${res.status})`,
            (payload && (payload.message || payload.error)) ||
              "未知错误,请稍后再试。"
          );
        }
        return;
      }

      if (!payload || !payload.markdown) {
        showError(
          "返回数据为空",
          "后端没有返回分析内容,可能是 LLM 调用失败。"
        );
        return;
      }

      renderResult(payload);
    } catch (err) {
      if (err && err.name === "AbortError") {
        showError(
          "请求超时",
          "等待超过 60 秒还没有返回结果。DeepSeek 分析比较耗时,请稍后再试。"
        );
      } else {
        // 网络层失败 → 通常意味着后端 down 或者跨域
        showError(
          "AI 分析服务暂未启动",
          "可能 host Mac 未在线或网络中断。当前网站静态部分(价值名单、每日报告)仍可访问。"
        );
        show($serviceDown);
      }
    } finally {
      setLoading(false);
    }
  }

  // ---------- 渲染结果 ----------
  function renderResult(data) {
    $resultName.textContent = data.name || "—";
    $resultTicker.textContent = data.ticker ? `(${data.ticker})` : "";

    // markdown-it 渲染(html 已关闭,安全)
    const html = md.render(String(data.markdown || ""));
    $resultContent.innerHTML = html;

    $metaTime.textContent = `分析时间 ${fmtTime(data.generated_at)}`;
    $metaEndpoints.textContent = `调用端点 ${fmtNum(
      data.fetched_endpoints
    )}`;
    $metaTokens.textContent = `tokens ${fmtNum(data.tokens_used)}`;

    show($resultWrap);
    hide($error);

    // 滚到结果顶部
    requestAnimationFrame(() => {
      $resultWrap.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  // ---------- 事件绑定 ----------
  $btn.addEventListener("click", () => {
    analyze($input.value);
  });

  $input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      analyze($input.value);
    }
  });

  $chips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const q = chip.getAttribute("data-q") || "";
      $input.value = q;
      analyze(q);
    });
  });

  // ---------- 启动:健康检查(不阻塞 UI) ----------
  checkHealth();

  // 暴露给 console 方便手动调试
  window.__AI_ANALYSIS_DEBUG = {
    backend: BACKEND_BASE,
    analyze,
    checkHealth,
  };
})();
