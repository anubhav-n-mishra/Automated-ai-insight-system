/*
 * Report builder page.
 *
 * Progress is read from the server's job record, not animated on a timer. The
 * previous UI advanced a five-step checklist every 800ms regardless of what the
 * backend was doing, so it reported "Building PowerPoint" while ingestion was
 * still running and showed all five steps complete on failure.
 */
(function () {
  "use strict";

  const { el, clear, $, $$, request, toast, notice, ApiError } = window.UI;

  const STAGES = [
    { id: "loading", label: "Reading the data" },
    { id: "aggregating", label: "Aggregating metrics" },
    { id: "comparing", label: "Comparing periods" },
    { id: "ranking", label: "Ranking movements" },
    { id: "narrating", label: "Writing the summary" },
    { id: "rendering", label: "Rendering the deck" },
  ];

  const SAMPLE_SPEC = `dataset:
  primary_source: clicks
  sources:
    clicks:
      type: csv
      path: marketing_clicks.csv
      date_column: date
      dimensions: [campaign, geo]
      metrics:
        - {name: impressions}
        - {name: clicks}
        - {name: spend, unit: currency}

derived_metrics:
  - name: ctr
    expression: clicks / impressions
    unit: percent
  - name: cpc
    expression: spend / clicks
    unit: currency
    higher_is_better: false

report:
  title: Paid media, week over week
  date_column: date
  comparison:
    current_start: 2025-11-24
    current_end: 2025-11-30
    previous_start: 2025-11-17
    previous_end: 2025-11-23
  dimensions: [campaign, geo]
  kpi_priority: [spend, clicks, ctr, cpc, impressions]`;

  const state = {
    profile: null,
    uploadId: null,
    jobId: null,
    polling: null,
    aborted: false,
  };

  /* ------------------------------------------------------------- upload --- */

  function initUpload() {
    const dropzone = $("#dropzone");
    const input = $("#file-input");
    if (!dropzone || !input) return;

    dropzone.addEventListener("click", () => input.click());
    input.addEventListener("change", () => {
      if (input.files && input.files[0]) uploadFile(input.files[0]);
    });

    for (const type of ["dragenter", "dragover"]) {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dragging");
      });
    }
    for (const type of ["dragleave", "drop"]) {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.remove("is-dragging");
      });
    }
    dropzone.addEventListener("drop", (event) => {
      const file = event.dataTransfer && event.dataTransfer.files[0];
      if (file) uploadFile(file);
    });
  }

  async function uploadFile(file) {
    const filenameNode = $("#dropzone-filename");
    filenameNode.textContent = file.name;

    const summary = $("#profile-summary");
    summary.hidden = false;
    clear(summary).append(notice("info", "Reading the file", "Profiling columns locally…"));

    const body = new FormData();
    body.append("file", file);

    try {
      const profile = await request("/api/v1/datasets", { method: "POST", body });
      state.profile = profile;
      state.uploadId = profile.upload_id;
      renderProfile(profile);
      $("#config-form").hidden = false;
      toast(`Profiled ${profile.row_count.toLocaleString()} rows.`, "success");
    } catch (error) {
      state.profile = null;
      state.uploadId = null;
      $("#config-form").hidden = true;
      clear(summary).append(
        notice("error", "That file could not be read", describe(error))
      );
      toast(describe(error), "error");
    }
  }

  /* ------------------------------------------------------------ profile --- */

  function renderProfile(profile) {
    const summary = $("#profile-summary");
    const facts = [
      `${profile.row_count.toLocaleString()} rows`,
      `${profile.columns.length} columns`,
    ];
    if (profile.date_range) {
      facts.push(`data from ${profile.date_range.start} to ${profile.date_range.end}`);
    }

    const children = [notice("success", profile.filename, facts.join(" · "))];
    if (profile.notes && profile.notes.length) {
      children.push(notice("warning", "Check these before generating", profile.notes));
    }
    children.push(buildPreview(profile.preview));
    clear(summary).append(...children);

    populateDateColumns(profile);
    populateChips("dimension", profile, ["dimension"]);
    populateChips("metric", profile, ["metric"]);
    applySuggestedDates(profile);
    updateCounts();

    const title = $("#report-title");
    if (title && !title.dataset.touched) {
      title.value = `${profile.filename.replace(/\.[^.]+$/, "")} analysis`.slice(0, 200);
    }
  }

  function buildPreview(rows) {
    if (!rows || !rows.length) return el("div");
    const columns = Object.keys(rows[0]);
    return el("details", { class: "field" }, [
      el("summary", { class: "plain", text: `Preview the first ${rows.length} rows` }),
      el("div", { class: "table-scroll mt-sm" }, [
        el("table", {}, [
          el("thead", {}, [el("tr", {}, columns.map((c) => el("th", { text: c })))]),
          el("tbody", {}, rows.map((row) =>
            el("tr", {}, columns.map((c) => el("td", { text: row[c] === null ? "—" : row[c] })))
          )),
        ]),
      ]),
    ]);
  }

  function populateDateColumns(profile) {
    const select = $("#date-column");
    clear(select);
    const candidates = profile.columns.filter((c) => c.role !== "ignored");
    for (const column of candidates) {
      select.append(el("option", {
        value: column.name,
        text: column.role === "date" ? `${column.name} (detected date)` : column.name,
        selected: column.name === profile.date_column,
      }));
    }
    const help = $("#date-range-help");
    help.textContent = profile.date_range
      ? `This column spans ${profile.date_range.start} to ${profile.date_range.end}.`
      : "No date range was detected for this column.";
  }

  function populateChips(kind, profile, roles) {
    const container = $(`#${kind}-chips`);
    clear(container);

    const preferred = new Set(
      kind === "dimension" ? profile.suggested_dimensions : profile.suggested_metrics
    );
    const columns = profile.columns.filter(
      (c) => roles.includes(c.role) || preferred.has(c.name) || c.role === "identifier"
    );

    for (const column of columns) {
      const input = el("input", {
        type: "checkbox",
        value: column.name,
        checked: preferred.has(column.name),
        "data-kind": kind,
      });
      input.addEventListener("change", () => {
        enforceExclusivity(kind, column.name, input.checked);
        updateCounts();
      });
      container.append(
        el("label", { class: "chip", title: column.reason }, [
          input,
          el("span", { text: column.name }),
          el("span", { class: "role", text: column.dtype.replace(/\(.*\)/, "") }),
        ])
      );
    }

    const filter = $(`#${kind}-filter`);
    filter.value = "";
    filter.oninput = () => {
      const needle = filter.value.trim().toLowerCase();
      for (const chip of $$(".chip", container)) {
        const name = chip.querySelector("input").value.toLowerCase();
        chip.hidden = needle !== "" && !name.includes(needle);
      }
    };
  }

  /**
   * A column can be a segment or a measure, never both — grouping by the column
   * being summed produces one row per value and a meaningless comparison. The
   * old UI offered every column in both lists with nothing stopping the overlap.
   */
  function enforceExclusivity(kind, name, checked) {
    if (!checked) return;
    const other = kind === "dimension" ? "metric" : "dimension";
    const twin = $(`#${other}-chips input[value="${CSS.escape(name)}"]`);
    if (twin && twin.checked) {
      twin.checked = false;
      toast(`"${name}" was moved from ${other}s to ${kind}s.`, "info");
    }
  }

  function updateCounts() {
    for (const kind of ["dimension", "metric"]) {
      const count = selected(kind).length;
      $(`#${kind}-count`).textContent = `${count} selected`;
    }
  }

  function selected(kind) {
    return $$(`#${kind}-chips input:checked`).map((input) => input.value);
  }

  function applySuggestedDates(profile) {
    const suggestion = profile.suggested_comparison;
    if (!suggestion) return;
    $("#current-start").value = suggestion.current_start;
    $("#current-end").value = suggestion.current_end;
    $("#previous-start").value = suggestion.previous_start;
    $("#previous-end").value = suggestion.previous_end;

    // Clamp the pickers to the range the data covers, so a user cannot silently
    // select two empty windows.
    if (profile.date_range) {
      for (const id of ["current-start", "current-end", "previous-start", "previous-end"]) {
        const input = $(`#${id}`);
        input.min = profile.date_range.start;
        input.max = profile.date_range.end;
      }
    }
  }

  /* ---------------------------------------------------------- validation --- */

  function validate() {
    const problems = [];
    const dimensions = selected("dimension");
    const metrics = selected("metric");

    if (!$("#date-column").value) problems.push("Choose a date column.");
    if (!metrics.length) problems.push("Select at least one metric.");
    if (metrics.length > 40) problems.push("Select at most 40 metrics.");
    if (dimensions.length > 8) problems.push("Select at most 8 dimensions.");

    const dates = {};
    for (const id of ["current-start", "current-end", "previous-start", "previous-end"]) {
      dates[id] = $(`#${id}`).value;
      if (!dates[id]) problems.push("Fill in all four period dates.");
    }

    if (Object.values(dates).every(Boolean)) {
      if (dates["current-end"] < dates["current-start"]) {
        problems.push("The current period ends before it starts.");
      }
      if (dates["previous-end"] < dates["previous-start"]) {
        problems.push("The previous period ends before it starts.");
      }
      if (dates["previous-start"] <= dates["current-end"] && dates["current-start"] <= dates["previous-end"]) {
        problems.push("The periods overlap. A period compared against part of itself is not a comparison.");
      }
    }

    const target = $("#form-errors");
    clear(target);
    if (problems.length) {
      target.append(notice("error", "Fix these first", [...new Set(problems)]));
      target.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
    return problems.length === 0;
  }

  /* ------------------------------------------------------------ generate --- */

  function buildPayload() {
    const profile = state.profile;
    const byName = new Map((profile ? profile.columns : []).map((c) => [c.name, c]));
    return {
      upload_id: state.uploadId,
      title: $("#report-title").value.trim() || "Performance Analysis",
      date_column: $("#date-column").value,
      dimensions: selected("dimension"),
      metrics: selected("metric").map((name) => {
        const column = byName.get(name);
        return {
          name,
          aggregation: (column && column.suggested_aggregation) || "sum",
          unit: (column && column.suggested_unit) || "count",
          higher_is_better: true,
        };
      }),
      derived_metrics: [],
      current_start: $("#current-start").value,
      current_end: $("#current-end").value,
      previous_start: $("#previous-start").value,
      previous_end: $("#previous-end").value,
      include_audio: true,
    };
  }

  async function submit(url, payload) {
    setBusy(true);
    clear($("#result"));
    renderStages("loading", 0);
    $("#progress").hidden = false;
    state.aborted = false;

    try {
      const accepted = await request(url, { method: "POST", json: payload });
      state.jobId = accepted.job_id;
      await pollJob(accepted.job_id);
    } catch (error) {
      failStages();
      clear($("#result")).append(
        notice("error", "The report could not be generated", describe(error))
      );
      toast(describe(error), "error");
    } finally {
      setBusy(false);
    }
  }

  async function pollJob(jobId) {
    const deadline = Date.now() + 15 * 60 * 1000;
    // Back off from 1s to 5s: a short report finishes on the first poll, a long
    // one should not be polled 900 times.
    let delay = 1000;

    while (Date.now() < deadline) {
      if (state.aborted) return;
      const job = await request(`/api/v1/jobs/${encodeURIComponent(jobId)}`);
      renderStages(job.stage, job.progress);

      if (job.status === "succeeded") {
        renderResult(job.result);
        toast("Report ready.", "success");
        return;
      }
      if (job.status === "failed") {
        failStages();
        clear($("#result")).append(
          notice("error", "The report could not be generated", (job.error && job.error.message) || "Unknown failure.")
        );
        return;
      }
      if (job.status === "cancelled") {
        failStages();
        clear($("#result")).append(notice("warning", "Cancelled", "The report was cancelled."));
        return;
      }

      await new Promise((resolve) => setTimeout(resolve, delay));
      delay = Math.min(delay * 1.4, 5000);
    }
    throw new ApiError("The report is taking longer than expected. Check back shortly.", "timeout", 0, {});
  }

  function setBusy(busy) {
    const button = $("#generate-btn");
    const label = $("#generate-label");
    const cancel = $("#cancel-btn");

    button.disabled = busy;
    cancel.hidden = !busy;
    clear(label).append(
      busy ? el("span", { class: "spinner", "aria-hidden": "true" }) : null,
      busy ? "Generating…" : "Generate report"
    );
    for (const id of ["sample-btn", "spec-btn"]) {
      const node = $(`#${id}`);
      if (node) node.disabled = busy;
    }
  }

  function renderStages(currentStage, progress) {
    const list = $("#stage-list");
    const index = STAGES.findIndex((stage) => stage.id === currentStage);
    const reached = currentStage === "complete" ? STAGES.length : Math.max(index, 0);

    clear(list);
    STAGES.forEach((stage, position) => {
      const done = position < reached || currentStage === "complete";
      const active = position === reached && currentStage !== "complete";
      list.append(
        el("li", { class: "stage", dataset: { state: done ? "done" : active ? "active" : "pending" } }, [
          el("span", { class: "marker", "aria-hidden": "true", text: done ? "✓" : String(position + 1) }),
          el("span", { text: stage.label }),
        ])
      );
    });

    const percent = Math.round((progress || 0) * 100);
    const fill = $("#progress-fill");
    fill.style.width = `${percent}%`;
    fill.setAttribute("aria-valuenow", String(percent));
    $("#progress-caption").textContent = `Report generation: ${percent}% complete, ${currentStage}`;
  }

  function failStages() {
    const active = $('.stage[data-state="active"]');
    if (active) active.dataset.state = "error";
  }

  /* -------------------------------------------------------------- result --- */

  function renderResult(result) {
    const target = clear($("#result"));
    if (!result) return;

    const analysis = result.analysis || {};
    const narrative = analysis.narrative;

    const children = [el("h3", { class: "mt-lg", text: "Report ready" })];

    if (narrative) {
      children.push(
        notice(
          narrative.provider === "template" ? "info" : "success",
          narrative.headline,
          narrative.provider === "template"
            ? "Summary written from the computed figures. Configure a model provider for narrative prose."
            : `Summary written by ${narrative.provider}${narrative.model ? ` (${narrative.model})` : ""}.`
        )
      );
    }

    if (analysis.warnings && analysis.warnings.length) {
      children.push(notice("warning", "Read with care", analysis.warnings));
    }

    if (analysis.totals && analysis.totals.length) {
      children.push(
        el("div", { class: "stat-grid my-md" },
          analysis.totals.slice(0, 4).map(statCard))
      );
    }

    children.push(
      el("div", { class: "btn-row" }, [
        el("a", {
          class: "btn btn-primary",
          href: result.report.download_url,
          download: result.report.filename,
          text: "Download the deck",
        }),
        el("a", {
          class: "btn btn-ghost",
          href: result.dashboard_url,
          target: "_blank",
          rel: "noopener",
          text: "Open the dashboard",
        }),
      ])
    );

    children.push(
      el("p", { class: "help mt-sm text-muted-sm" }, [
        `Analysed ${(analysis.row_count || 0).toLocaleString()} rows across `,
        `${(analysis.segment_count || 0).toLocaleString()} segments in `,
        `${((analysis.duration_ms || 0) / 1000).toFixed(1)}s. `,
        "The dashboard link carries an access token and expires.",
      ])
    );

    target.append(...children);
    target.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function statCard(total) {
    return el("div", { class: "stat" }, [
      el("span", { class: "label", text: total.label }),
      el("span", { class: "value", text: window.UI.formatValue(total.current, total.unit, total.precision) }),
      el("span", { class: "delta", dataset: { sentiment: total.sentiment } }, [
        el("span", { "aria-hidden": "true", text: window.UI.arrow(total.direction) }),
        window.UI.formatDeltaPct(total.delta_pct),
      ]),
      el("span", {
        class: "baseline",
        text: `from ${window.UI.formatValue(total.previous, total.unit, total.precision)}`,
      }),
    ]);
  }

  function describe(error) {
    if (error instanceof ApiError) {
      if (error.context && Array.isArray(error.context.fields) && error.context.fields.length) {
        return error.context.fields.map((f) => `${f.field}: ${f.message}`).join("; ");
      }
      return error.message;
    }
    return "Something went wrong. Try again.";
  }

  /* ----------------------------------------------------------------- init -- */

  document.addEventListener("DOMContentLoaded", () => {
    window.UI.initTheme();
    window.UI.initTabs($(".tablist"));
    initUpload();

    $("#sample-spec").textContent = SAMPLE_SPEC;
    $("#spec-input").placeholder = SAMPLE_SPEC;

    $("#report-title").addEventListener("input", (event) => {
      event.target.dataset.touched = "true";
    });

    for (const button of $$("[data-clear]")) {
      button.addEventListener("click", () => {
        const kind = button.dataset.clear === "dimensions" ? "dimension" : "metric";
        for (const input of $$(`#${kind}-chips input`)) input.checked = false;
        updateCounts();
      });
    }

    $("#config-form").addEventListener("submit", (event) => {
      event.preventDefault();
      if (!state.uploadId) {
        toast("Upload a dataset first.", "error");
        return;
      }
      if (validate()) submit("/api/v1/reports", buildPayload());
    });

    $("#cancel-btn").addEventListener("click", async () => {
      state.aborted = true;
      if (state.jobId) {
        await request(`/api/v1/jobs/${encodeURIComponent(state.jobId)}`, { method: "DELETE" })
          .catch(() => null);
      }
      setBusy(false);
      $("#progress").hidden = true;
      toast("Cancelled.", "info");
    });

    $("#sample-btn").addEventListener("click", () => {
      toast("Run the sample from the CLI: insight-engine run examples/configs/marketing-csv.yaml", "info");
      window.open("/docs#/Reports", "_blank", "noopener");
    });

    $("#spec-btn").addEventListener("click", () => {
      const text = $("#spec-input").value.trim();
      if (!text) {
        toast("Paste a specification first.", "error");
        return;
      }
      let spec;
      try {
        spec = JSON.parse(text);
      } catch {
        toast("Paste the specification as JSON here, or use the CLI for YAML.", "error");
        return;
      }
      submit("/api/v1/reports/from-spec", {
        spec,
        upload_id: state.uploadId,
        include_audio: true,
      });
    });
  });
})();
