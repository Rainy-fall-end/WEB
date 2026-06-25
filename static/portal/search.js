const form = document.getElementById("search-form");
const input = form ? form.querySelector('input[name="q"]') : null;
const button = document.getElementById("search-button");
const loadingPanel = document.getElementById("loading-panel");
const loadingText = document.getElementById("loading-text");
const loadingTime = document.getElementById("loading-time");
const errorBox = document.getElementById("search-error");
const summary = document.getElementById("results-summary");
const summaryKeyword = document.getElementById("summary-keyword");
const summaryCount = document.getElementById("summary-count");
const summarySources = document.getElementById("summary-sources");
const resultsList = document.getElementById("results-list");

const loadingMessages = ["正在搜索", "正在读取结果", "正在打开详情页", "正在计算价格"];
let loadingTimer = null;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setLoading(isLoading) {
  if (!loadingPanel || !button) return;

  if (isLoading) {
    let seconds = 0;
    loadingPanel.classList.add("is-visible");
    button.disabled = true;
    button.textContent = "搜索中";
    loadingText.textContent = loadingMessages[0];
    loadingTime.textContent = "0s";

    window.clearInterval(loadingTimer);
    loadingTimer = window.setInterval(() => {
      seconds += 1;
      loadingTime.textContent = `${seconds}s`;
      loadingText.textContent = loadingMessages[Math.min(loadingMessages.length - 1, Math.floor(seconds / 4))];
    }, 1000);
    return;
  }

  window.clearInterval(loadingTimer);
  loadingTimer = null;
  loadingPanel.classList.remove("is-visible");
  button.disabled = false;
  button.textContent = "搜索";
}

function showError(message) {
  if (!errorBox) return;
  errorBox.textContent = message;
  errorBox.classList.remove("is-hidden");
}

function hideError() {
  if (!errorBox) return;
  errorBox.textContent = "";
  errorBox.classList.add("is-hidden");
}

function renderSummary(payload) {
  summaryKeyword.textContent = payload.keyword || "";
  summaryCount.textContent = payload.count ?? 0;
  summarySources.textContent = Array.isArray(payload.sources) ? payload.sources.length : 0;
  summary.classList.remove("is-hidden");
}

function renderResult(result) {
  const sourceName = result.source && result.source.name ? result.source.name : "未知来源";
  const price = result.price || {};
  const images = Array.isArray(result.preview_images) ? result.preview_images.slice(0, 3) : [];
  const previewHtml = images.length
    ? `
      <div class="preview-grid">
        ${images
          .map(
            (url) => `
              <a class="preview-link" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">
                <img src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer">
              </a>
            `
          )
          .join("")}
      </div>
    `
    : "";
  const priceHtml = price.tongbao
    ? `<span>${escapeHtml(price.tongbao)} 通宝</span><span>约 ¥${escapeHtml(price.rmb)}</span>`
    : "<span>未找到售价</span>";

  return `
    <article class="result">
      <h2><a href="${escapeHtml(result.url)}" target="_blank" rel="noreferrer">${escapeHtml(result.title)}</a></h2>
      <p>${escapeHtml(result.snippet)}</p>
      ${previewHtml}
      <div class="meta">
        <span>${escapeHtml(sourceName)}</span>
        <span>${escapeHtml(result.meta)}</span>
        ${priceHtml}
      </div>
    </article>
  `;
}

function renderResults(payload) {
  renderSummary(payload);

  const results = Array.isArray(payload.results) ? payload.results : [];
  if (!results.length) {
    resultsList.innerHTML = '<p class="empty">没有找到结果。</p>';
    return;
  }

  resultsList.innerHTML = results.map(renderResult).join("");
}

async function readJsonResponse(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  const titleMatch = text.match(/<title[^>]*>(.*?)<\/title>/i);
  const title = titleMatch ? titleMatch[1].replace(/\s+/g, " ").trim() : "";
  const status = response.status ? `HTTP ${response.status}` : "非 JSON 响应";
  throw new Error(title ? `${status}: ${title}` : `${status}: 服务器返回了 HTML 页面，请检查后端日志`);
}

async function search(keyword) {
  hideError();
  setLoading(true);
  resultsList.innerHTML = '<article class="result is-loading">正在准备搜索结果...</article>';

  try {
    const response = await fetch(`/api/search/?q=${encodeURIComponent(keyword)}`, {
      headers: {
        Accept: "application/json",
      },
    });
    const payload = await readJsonResponse(response);

    if (!response.ok) {
      throw new Error(payload.error || "搜索失败");
    }

    renderResults(payload);
    const nextUrl = `/?q=${encodeURIComponent(keyword)}`;
    window.history.pushState({ keyword }, "", nextUrl);
  } catch (error) {
    resultsList.innerHTML = "";
    summary.classList.add("is-hidden");
    showError(error.message || "搜索失败");
  } finally {
    setLoading(false);
  }
}

if (form && input) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const keyword = input.value.trim();
    if (!keyword) {
      input.focus();
      return;
    }
    search(keyword);
  });

  const initialKeyword = input.value.trim();
  if (initialKeyword) {
    search(initialKeyword);
  }
}
