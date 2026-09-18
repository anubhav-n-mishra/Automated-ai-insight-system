/*
 * Shared dashboard.
 *
 * Every figure on this page comes from the analysis payload. The previous
 * dashboard generated its headline percentages with `Math.random() * 20 - 10`
 * and read `insight.change` / `insight.dimension`, keys the engine never
 * produced, so the KPI cards showed invented movements and the table showed
 * 0.0% on every row. Nothing here fabricates a value: a missing number renders
 * as an em dash, and an undefined percentage renders as "n/a".
 */
(function () {
  "use strict";

  const { el, clear, $, request, toast, notice, formatValue, formatDeltaPct, arrow,
          segmentLabel, formatDateTime, relativeTime, ApiError } = window.UI;

  const state = {
    data: null,
    segments: [],
    speaking: false,
    index: 0,
    mode: "browser",
    audio: null,
  };

  /* ----------------------------------------------------------------- load -- */

  function sessionFromUrl() {
    const parts = window.location.pathname.split("/").filter(Boolean);
    const sessionId = parts[parts.length - 1] || "";
    const token = new URLSearchParams(window.location.search).get("token") || "";
    return { sessionId, token };
  }

  async function load() {
    const { sessionId, token } = sessionFromUrl();
    if (!sessionId || !token) {
      showError(
        "This link is incomplete",
        "A dashboard link needs both a session id and its access token. Open the full link from the report."
      );
      return;
    }

    try {
      const url = `/api/v1/dashboards/${encodeURIComponent(sessionId)}?token=${encodeURIComponent(token)}`;
      state.data = await request(url);
      render();
    } catch (error) {
      const message =
        error instanceof ApiError && error.status === 404
          ? "This dashboard link is invalid or has expired. Generate the report again to get a fresh link."
          : (error && error.message) || "The dashboard could not be loaded.";
      showError("Dashboard unavailable", message);
    }
  }

  function showError(title, message) {
    $("#loading").hidden = true;
    const target = $("#error");
    target.hidden = false;
    clear(target).append(el("div", { class: "card" }, [notice("error", title, message)]));
  }

  /* --------------------------------------------------------------- render -- */

  function render() {
    const data = state.data;
    const analysis = data.analysis || {};

    $("#loading").hidden = true;
    $("#dashboard").hidden = false;

    document.title = `${data.title} — Insight Engine`;
    $("#report-title").textContent = data.title;

    const period = analysis.period || {};
    $("#report-period").textContent = period.current_start
      ? `${period.current_start} to ${period.current_end} vs ${period.previous_start} to ${period.previous_end}`
      : "";

    if (data.report_url) {
      const link = $("#deck-link");
      link.href = data.report_url;
      link.hidden = false;
    }

    $("#expiry-note").textContent = data.expires_at
      ? `This link expires ${relativeTime(data.expires_at)} (${formatDateTime(data.expires_at)}).`
      : "";

    renderSummary(analysis);
    renderTotals(analysis.totals || []);
    renderDrivers(analysis.drivers || []);
    renderInsights(analysis.insights || [], analysis);
    renderMethodology(analysis);
    setupBriefing(data.briefing);
  }

  function renderSummary(analysis) {
    const narrative = analysis.narrative;
    const warnings = analysis.warnings || [];

    const warningTarget = clear($("#warnings"));
    if (warnings.length) {
      warningTarget.append(notice("warning", "Read with care", warnings));
    }

    $("#headline").textContent = narrative ? narrative.headline : "No summary was generated.";

    const bullets = clear($("#bullets"));
    for (const bullet of (narrative && narrative.bullets) || []) {
      bullets.append(el("li", { text: bullet }));
    }

    const recommendation = clear($("#recommendation"));
    if (narrative && narrative.recommendation) {
      recommendation.append(notice("info", "Recommended next step", narrative.recommendation));
    }

    $("#narrative-provenance").textContent = narrative
      ? narrative.provider === "template"
        ? "Summary written deterministically from the computed figures."
        : `Summary written by ${narrative.provider}${narrative.model ? ` (${narrative.model})` : ""}. Figures are computed, not generated.`
      : "";
  }

  function renderTotals(totals) {
    const target = clear($("#totals"));
    if (!totals.length) {
      target.append(el("p", { class: "empty-state", text: "No metrics were computed." }));
      return;
    }
    for (const total of totals) {
      target.append(
        el("div", { class: "stat" }, [
          el("span", { class: "label", text: total.label }),
          el("span", { class: "value", text: formatValue(total.current, total.unit, total.precision) }),
          el("span", { class: "delta", dataset: { sentiment: total.sentiment } }, [
            el("span", { "aria-hidden": "true", text: arrow(total.direction) }),
            formatDeltaPct(total.delta_pct),
            el("span", { class: "visually-hidden", text: ` ${total.direction} versus the previous period` }),
          ]),
          el("span", {
            class: "baseline",
            text: `from ${formatValue(total.previous, total.unit, total.precision)}`,
          }),
        ])
      );
    }
  }

  function renderDrivers(drivers) {
    const section = $("#drivers-section");
    if (!drivers.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;

    const target = clear($("#drivers"));
    for (const attribution of drivers.slice(0, 3)) {
      const widest = Math.max(...attribution.drivers.map((d) => Math.abs(d.delta)), 1);
      target.append(
        el("div", { style: "margin-bottom:22px" }, [
          el("h3", { text: attribution.label }),
          el("p", {
            class: "tagline",
            text:
              `Net movement ${formatValue(attribution.total_delta, "count")} across ` +
              `${attribution.segment_count} segments. The segments below account for ` +
              `${Math.abs(attribution.explained_pct).toFixed(0)}% of all movement.` +
              (attribution.offsetting
                ? " Gains and losses partly cancel, so the net understates the churn beneath it."
                : ""),
          }),
          el("div", { style: "display:grid;gap:8px;margin-top:10px" },
            attribution.drivers.map((driver) =>
              el("div", { style: "display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center" }, [
                el("div", { style: "min-width:0" }, [
                  el("div", { text: segmentLabel(driver.segment), style: "font-size:0.88rem" }),
                  el("span", { class: "bar" }, [
                    el("span", {
                      style: `width:${Math.min(100, (Math.abs(driver.delta) / widest) * 100).toFixed(1)}%;` +
                             `background:var(--${driver.sentiment === "negative" ? "negative" : "positive"})`,
                    }),
                  ]),
                ]),
                el("div", {
                  class: "delta-cell",
                  dataset: { sentiment: driver.sentiment },
                  style: "font-size:0.88rem;white-space:nowrap",
                  text: `${formatValue(driver.delta, driver.unit, driver.precision)} (${formatDeltaPct(driver.delta_pct)})`,
                }),
              ])
            )
          ),
        ])
      );
    }
  }

  function renderInsights(insights, analysis) {
    const body = clear($("#insights-body"));
    $("#insights-caption").textContent = insights.length
      ? `Ranked by impact across ${(analysis.segment_count || 0).toLocaleString()} segments. ` +
        `"n/a" means the previous value was zero, so a percentage change is undefined.`
      : "";

    if (!insights.length) {
      body.append(
        el("tr", {}, [
          el("td", { colspan: "6" }, [
            el("div", { class: "empty-state" }, [
              el("h3", { text: "No segment moved materially" }),
              el("p", { text: "Widen the date range, or check that the chosen dimensions vary across it." }),
            ]),
          ]),
        ])
      );
      return;
    }

    const widest = Math.max(...insights.map((i) => i.impact_score), 0.0001);
    for (const insight of insights.slice(0, 25)) {
      const flags = [];
      if (insight.is_new_segment) flags.push(el("span", { class: "tag", text: "new" }));
      if (insight.is_lost_segment) flags.push(el("span", { class: "tag", text: "lost" }));

      body.append(
        el("tr", {}, [
          el("td", {}, [el("span", { text: segmentLabel(insight.segment) }), " ", ...flags]),
          el("td", { text: insight.label }),
          el("td", { class: "num", text: formatValue(insight.previous_value, insight.unit, insight.precision) }),
          el("td", { class: "num", text: formatValue(insight.current_value, insight.unit, insight.precision) }),
          el("td", { class: "num delta-cell", dataset: { sentiment: insight.sentiment } }, [
            el("span", { "aria-hidden": "true", text: `${arrow(insight.direction)} ` }),
            formatDeltaPct(insight.delta_pct),
          ]),
          el("td", {}, [
            el("span", {
              class: "bar",
              role: "img",
              "aria-label": `Impact score ${insight.impact_score.toFixed(3)}`,
              title: insight.contribution_pct !== null && insight.contribution_pct !== undefined
                ? `${Math.abs(insight.contribution_pct).toFixed(0)}% of the metric's total movement`
                : "Impact score",
            }, [
              el("span", { style: `width:${((insight.impact_score / widest) * 100).toFixed(1)}%` }),
            ]),
          ]),
        ])
      );
    }
  }

  function renderMethodology(analysis) {
    const period = analysis.period || {};
    const target = clear($("#methodology"));
    const facts = [
      ["Rows read", (analysis.row_count || 0).toLocaleString()],
      ["Segments compared", (analysis.segment_count || 0).toLocaleString()],
      ["Current period", `${period.current_start} to ${period.current_end} (${period.current_days} days, ${(period.current_rows || 0).toLocaleString()} rows)`],
      ["Previous period", `${period.previous_start} to ${period.previous_end} (${period.previous_days} days, ${(period.previous_rows || 0).toLocaleString()} rows)`],
      ["Segmented by", (analysis.dimensions || []).join(", ") || "not segmented"],
      ["Ratio metrics", "computed from aggregated numerators and denominators, never averaged across rows"],
      ["Analysis time", `${((analysis.duration_ms || 0) / 1000).toFixed(2)}s`],
    ];
    if (period.like_for_like === false) {
      facts.push(["Caveat", "the two periods are different lengths, so absolute deltas are not directly comparable"]);
    }
    for (const [label, value] of facts) {
      target.append(el("li", {}, [el("strong", { text: `${label}: ` }), el("span", { text: String(value) })]));
    }
  }

  /* -------------------------------------------------------------- briefing -- */

  function setupBriefing(briefing) {
    const status = $("#audio-status");
    const transcript = $("#briefing-transcript");

    if (!briefing) {
      status.textContent = "No briefing was generated for this report.";
      $("#play-btn").disabled = true;
      return;
    }

    state.segments = briefing.segments || [];
    transcript.textContent = briefing.full_text || "";

    if (briefing.audio_kind === "murf" && briefing.audio_url) {
      state.mode = "audio";
      state.audio = $("#audio-element");
      state.audio.src = briefing.audio_url;
      state.audio.addEventListener("timeupdate", () => {
        if (state.audio.duration) {
          setAudioProgress((state.audio.currentTime / state.audio.duration) * 100);
        }
      });
      state.audio.addEventListener("ended", stopBriefing);
      status.textContent = `Narrated briefing, about ${Math.round(briefing.estimated_seconds)} seconds.`;
      return;
    }

    state.mode = "browser";
    if (!("speechSynthesis" in window)) {
      status.textContent = "Your browser cannot speak this briefing. The transcript is below.";
      $("#play-btn").disabled = true;
      return;
    }
    status.textContent = `Spoken by your browser, about ${Math.round(briefing.estimated_seconds)} seconds.`;
  }

  function setAudioProgress(percent) {
    const bar = $("#audio-progress");
    const value = Math.max(0, Math.min(100, percent));
    bar.style.width = `${value}%`;
    bar.setAttribute("aria-valuenow", String(Math.round(value)));
  }

  function toggleBriefing() {
    if (state.speaking) stopBriefing();
    else startBriefing();
  }

  function startBriefing() {
    state.speaking = true;
    setPlayButton(true);
    $("#audio-status").textContent = "Playing…";

    if (state.mode === "audio" && state.audio) {
      state.audio.play().catch(() => {
        state.speaking = false;
        setPlayButton(false);
        toast("Your browser blocked audio playback. Try again after interacting with the page.", "error");
      });
      return;
    }

    state.index = 0;
    speakNext();
  }

  function speakNext() {
    if (!state.speaking || state.index >= state.segments.length) {
      stopBriefing(true);
      return;
    }
    const segment = state.segments[state.index];
    const utterance = new SpeechSynthesisUtterance(segment.text);
    utterance.rate = 0.98;
    utterance.onend = () => {
      state.index += 1;
      setAudioProgress((state.index / Math.max(state.segments.length, 1)) * 100);
      window.setTimeout(() => {
        if (state.speaking) speakNext();
      }, segment.pause_ms || 300);
    };
    utterance.onerror = () => stopBriefing();
    window.speechSynthesis.speak(utterance);
  }

  function stopBriefing(completed) {
    state.speaking = false;
    setPlayButton(false);
    if (state.mode === "audio" && state.audio) state.audio.pause();
    else if ("speechSynthesis" in window) window.speechSynthesis.cancel();

    $("#audio-status").textContent = completed ? "Briefing complete." : "Paused.";
    if (completed) {
      setAudioProgress(100);
      state.index = 0;
    }
  }

  function setPlayButton(playing) {
    const button = $("#play-btn");
    button.setAttribute("aria-pressed", String(playing));
    $("#play-label").textContent = playing ? "Pause the briefing" : "Play the briefing";
    const icon = $("#play-icon");
    clear(icon);
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", playing ? "M6 5h4v14H6zM14 5h4v14h-4z" : "M8 5v14l11-7z");
    icon.append(path);
  }

  /* ----------------------------------------------------------------- init -- */

  document.addEventListener("DOMContentLoaded", () => {
    window.UI.initTheme();
    $("#play-btn").addEventListener("click", toggleBriefing);
    // A navigation mid-utterance otherwise leaves the synthesiser talking.
    window.addEventListener("pagehide", () => stopBriefing());
    load();
  });
})();
