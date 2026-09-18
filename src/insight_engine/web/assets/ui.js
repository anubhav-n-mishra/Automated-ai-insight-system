/*
 * Shared UI helpers.
 *
 * Two rules run through this file.
 *
 * 1. Nothing is ever written with innerHTML, and nothing is styled with a
 *    style attribute. The page runs under `style-src 'self'` with no
 *    'unsafe-inline', so an attribute would be refused by the browser. Every value rendered here can
 *    originate from a CSV header, a segment value or model-generated prose, and
 *    the previous UI interpolated all three straight into innerHTML — a CSV
 *    with a column named `<img src=x onerror=...>` executed on the dashboard of
 *    everyone the link was shared with.
 *
 * 2. Formatting mirrors the server's own formatting module, so a figure reads
 *    the same in the deck, the dashboard and the spoken briefing.
 */
(function (global) {
  "use strict";

  /** Create an element with attributes and children. Text children are escaped by construction. */
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = String(value);
        else if (key === "dataset") Object.assign(node.dataset, value);
        // `style` takes an object and is applied through the CSSOM. A style
        // *attribute* is refused under `style-src 'self'`, so accepting a
        // string here would silently render the element unstyled.
        else if (key === "style") Object.assign(node.style, value);
        else if (key.startsWith("on") && typeof value === "function") {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (value === true) node.setAttribute(key, "");
        else node.setAttribute(key, String(value));
      }
    }
    for (const child of [].concat(children || [])) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
  }

  const $ = (selector, scope) => (scope || document).querySelector(selector);
  const $$ = (selector, scope) => Array.from((scope || document).querySelectorAll(selector));

  /* ----------------------------------------------------------- formatting -- */

  const CURRENCY = { count: "", currency: "$" };

  function compactNumber(value, precision) {
    const digits = precision === undefined ? 1 : precision;
    const magnitude = Math.abs(value);
    const sign = value < 0 ? "-" : "";
    if (magnitude >= 1e9) return `${sign}${(magnitude / 1e9).toFixed(digits)}B`;
    if (magnitude >= 1e6) return `${sign}${(magnitude / 1e6).toFixed(digits)}M`;
    if (magnitude >= 1e3) return `${sign}${(magnitude / 1e3).toFixed(digits)}K`;
    if (magnitude >= 10) return `${sign}${Math.round(magnitude).toLocaleString()}`;
    if (magnitude === 0) return "0";
    return `${sign}${magnitude.toFixed(Math.max(digits, 2))}`;
  }

  function formatValue(value, unit, precision) {
    if (value === null || value === undefined || Number.isNaN(value)) return "—";
    const digits = precision === undefined ? 2 : precision;
    if (unit === "percent") return `${(value * 100).toFixed(digits)}%`;
    if (unit === "ratio") return value.toFixed(digits);
    if (unit === "currency") {
      return Math.abs(value) >= 1000
        ? `${CURRENCY.currency}${compactNumber(value)}`
        : `${CURRENCY.currency}${value.toFixed(2)}`;
    }
    if (unit === "duration_seconds") {
      if (value < 60) return `${value.toFixed(1)}s`;
      if (value < 3600) return `${(value / 60).toFixed(1)}m`;
      return `${(value / 3600).toFixed(1)}h`;
    }
    return compactNumber(value);
  }

  /** Signed percentage, or "n/a" when the baseline was zero. Never invents a number. */
  function formatDeltaPct(deltaPct) {
    if (deltaPct === null || deltaPct === undefined) return "n/a";
    const sign = deltaPct > 0 ? "+" : "";
    return `${sign}${deltaPct.toFixed(1)}%`;
  }

  function arrow(direction) {
    if (direction === "up") return "↑";
    if (direction === "down") return "↓";
    return "→";
  }

  function segmentLabel(segment) {
    if (!segment || Object.keys(segment).length === 0) return "Overall";
    return Object.entries(segment)
      .map(([key, value]) => `${key}: ${value}`)
      .join(", ");
  }

  function formatDateTime(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  }

  function relativeTime(iso) {
    if (!iso) return "";
    const target = new Date(iso).getTime();
    if (Number.isNaN(target)) return "";
    const seconds = Math.round((target - Date.now()) / 1000);
    const units = [["day", 86400], ["hour", 3600], ["minute", 60]];
    const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
    for (const [unit, size] of units) {
      if (Math.abs(seconds) >= size) return formatter.format(Math.round(seconds / size), unit);
    }
    return formatter.format(seconds, "second");
  }

  /* --------------------------------------------------------------- toasts -- */

  function toast(message, kind) {
    const region = $("#toasts");
    if (!region) return;
    const node = el("div", { class: "toast", dataset: { kind: kind || "info" }, role: "status" }, [
      el("p", { text: message }),
      el("button", {
        type: "button",
        "aria-label": "Dismiss notification",
        text: "×",
        onclick: () => node.remove(),
      }),
    ]);
    region.append(node);
    // Errors stay until dismissed; a message the user needs to act on should not
    // disappear while they are reading it.
    if (kind !== "error") global.setTimeout(() => node.remove(), 6000);
  }

  function notice(kind, title, body) {
    const children = [title ? el("h3", { text: title }) : null];
    if (Array.isArray(body)) {
      children.push(el("ul", {}, body.map((item) => el("li", { text: item }))));
    } else if (body) {
      children.push(el("p", { text: body }));
    }
    return el("div", { class: `notice notice-${kind}` }, children);
  }

  /* ------------------------------------------------------------- requests -- */

  class ApiError extends Error {
    constructor(message, code, status, context) {
      super(message);
      this.name = "ApiError";
      this.code = code;
      this.status = status;
      this.context = context;
    }
  }

  async function request(url, options) {
    const config = Object.assign({ headers: {} }, options || {});
    if (config.json !== undefined) {
      config.body = JSON.stringify(config.json);
      config.headers["Content-Type"] = "application/json";
      delete config.json;
    }
    const apiKey = storage.get("api_key");
    if (apiKey) config.headers["X-API-Key"] = apiKey;

    let response;
    try {
      response = await fetch(url, config);
    } catch (error) {
      throw new ApiError(
        "Could not reach the server. Check your connection and try again.",
        "network_error", 0, {}
      );
    }

    if (response.status === 204) return null;

    let payload = null;
    const type = response.headers.get("content-type") || "";
    if (type.includes("application/json")) {
      payload = await response.json().catch(() => null);
    }

    if (!response.ok) {
      const detail = (payload && payload.error) || {};
      throw new ApiError(
        detail.message || `Request failed with status ${response.status}.`,
        detail.code || "http_error",
        response.status,
        detail.context || {}
      );
    }
    return payload;
  }

  /* -------------------------------------------------------------- storage -- */

  const storage = {
    get(key) {
      try { return global.localStorage.getItem(`insight-engine:${key}`); } catch { return null; }
    },
    set(key, value) {
      try { global.localStorage.setItem(`insight-engine:${key}`, value); } catch { /* private mode */ }
    },
  };

  /* ---------------------------------------------------------------- theme -- */

  function initTheme() {
    const root = document.documentElement;
    const saved = storage.get("theme");
    if (saved === "dark" || saved === "light") root.dataset.theme = saved;

    const button = $("#theme-toggle");
    const label = $("#theme-label");
    if (!button) return;

    const systemDark = global.matchMedia("(prefers-color-scheme: dark)");
    const isDark = () =>
      root.dataset.theme === "dark" || (root.dataset.theme !== "light" && systemDark.matches);

    const sync = () => {
      const dark = isDark();
      button.setAttribute("aria-pressed", String(dark));
      if (label) label.textContent = dark ? "Light theme" : "Dark theme";
    };

    button.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      storage.set("theme", next);
      sync();
    });
    systemDark.addEventListener("change", sync);
    sync();
  }

  /* ----------------------------------------------------------------- tabs -- */

  /** Tablist with the roving-tabindex keyboard behaviour the ARIA pattern requires. */
  function initTabs(container) {
    const tabs = $$('[role="tab"]', container);
    if (!tabs.length) return;

    const select = (tab) => {
      for (const candidate of tabs) {
        const selected = candidate === tab;
        candidate.setAttribute("aria-selected", String(selected));
        candidate.tabIndex = selected ? 0 : -1;
        const panel = document.getElementById(candidate.getAttribute("aria-controls"));
        if (panel) panel.hidden = !selected;
      }
      tab.focus();
    };

    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (event) => {
        const keys = { ArrowRight: 1, ArrowLeft: -1 };
        if (event.key in keys) {
          event.preventDefault();
          select(tabs[(index + keys[event.key] + tabs.length) % tabs.length]);
        } else if (event.key === "Home") {
          event.preventDefault(); select(tabs[0]);
        } else if (event.key === "End") {
          event.preventDefault(); select(tabs[tabs.length - 1]);
        }
      });
    });
  }

  global.UI = {
    el, clear, $, $$,
    compactNumber, formatValue, formatDeltaPct, arrow, segmentLabel,
    formatDateTime, relativeTime,
    toast, notice, request, ApiError, storage, initTheme, initTabs,
  };
})(window);
