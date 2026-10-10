(() => {
  "use strict";
  const D = window.Douku, $ = (id) => D.$("collection-" + id);
  const view = D.$("imports-collections-view"), base = "/api/collection-imports";
  if (!view) return;
  const node = D.node, ongoing = new Set(["queued", "resolving", "inventorying", "dispatching", "monitoring"]);
  const labels = {pending: "待选择", selected: "已选择", skipped: "不导入", imported: "已在资料库",
    active: "处理中", failed: "失败", cancelled: "已取消", deleted: "资料已删除",
    unsupported: "暂不支持", unavailable: "不可用", conflict: "处理选项不一致"};
  let jobId = null, current = null, page = 1, busy = false, timer = null, epoch = 0;
  let failedSelection = null, operation = Promise.resolve();
  D.bindCheckList($("items"));
  const active = () => !view.classList.contains("hidden");
  const selectable = () => current?.status === "needs_selection" && !current.confirmed;
  const request = (path, body) => D.api(path, body === undefined ? {} : {method: "POST", body: JSON.stringify(body)});
  const jobPath = () => base + "/" + encodeURIComponent(jobId);
  function durationText(seconds) {
    if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) <= 0) return "";
    const total = Math.round(Number(seconds));
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }
  function controls() {
    view.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
    $("history").disabled = busy;
    const choose = selectable() && !busy && !failedSelection;
    ["select-all", "skip-all"].forEach((id) => { $(id).disabled = !choose; });
    $("items").querySelectorAll("input").forEach((input) => {
      input.disabled = !choose || input.dataset.eligible !== "true";
    });
    $("previous").disabled = busy || !current || page <= 1;
    $("next").disabled = busy || !current?.has_more;
    $("confirm").disabled = !choose || Boolean(current?.summary?.undecided) ||
      (!current?.complete && !$("partial").checked);
    $("confirm").hidden = !selectable();
    $("partial-row").hidden = !selectable() || current.complete;
    $("retry").hidden = !current?.can_retry;
    $("retry").textContent = current?.confirmed ? "重试失败项"
      : current?.status === "needs_selection" && !current.complete ? "重新清点" : "重试清点";
    $("stop").hidden = !current || !(ongoing.has(current.status) || selectable());
    $("save-retry").hidden = !failedSelection;
    $("save-hint").textContent = failedSelection ? "选择尚未保存，请重试保存后再确认。"
      : busy ? "正在保存或读取…" : selectable() ? "选择已保存" : "";
  }
  function render(data) {
    current = data;
    const s = data.summary || {}, profile = data.profile || {};
    $("status").textContent = (profile.name || "合集任务") + " · " + (data.status_label || data.status);
    const historyOption = $("history").selectedOptions[0];
    if (historyOption?.value === jobId) {
      historyOption.textContent = `${profile.name || "读取中"} · ${data.status_label || data.status} · ${s.discovered || 0} 件`;
    }
    $("summary").textContent = `已读 ${s.discovered || 0} 件 · 待选择 ${s.undecided || 0} 件 · 已选 ${s.selected || 0} 件 · 已在资料库 ${s.in_library || 0} 件`;
    $("result").textContent = data.confirmed
      ? `新任务 ${s.new_tasks || 0} 件 · 复用任务 ${s.reused_tasks || 0} 件 · 失败 ${s.failed || 0} 件 · 已取消 ${s.cancelled || 0} 件`
      : `预计新建 ${s.new_tasks || 0} 件 · 复用任务 ${s.reused_tasks || 0} 件 · 不可导入 ${(s.unsupported || 0) + (s.unavailable || 0)} 件。请为每件可导入作品选择或跳过。`;
    $("gateway").hidden = !(data.confirmed && data.analysis_mode === "gateway" && ongoing.has(data.status));
    $("auth").hidden = !(data.status === "needs_auth" || s.child_status_counts?.needs_auth);
    $("warnings").replaceChildren(...(data.warnings || []).map((text) => node("li", text)));
    $("error").textContent = data.error_message || "";
    const focused = document.activeElement?.dataset?.work;
    $("items").replaceChildren();
    for (const item of data.works || []) {
      const row = node("div", null, "favorites-item row--check"), input = node("input");
      input.type = "checkbox"; input.dataset.work = item.work_id; input.checked = item.decision === "selected";
      input.dataset.eligible = String(item.can_import && !item.entry_id);
      input.setAttribute("aria-label", "选择 " + item.title);
      input.addEventListener("change", () => saveSelection({decision: input.checked ? "selected" : "skipped",
        work_ids: input.selectionWorkIds || [item.work_id]}));
      const content = node("div", null, "favorites-item-content");
      const cover = node(item.thumbnail_url ? "img" : "span", null, "collection-item-cover");
      if (item.thumbnail_url) {
        cover.src = item.thumbnail_url; cover.alt = ""; cover.loading = "lazy";
        cover.referrerPolicy = "no-referrer";
      }
      const title = node("a", item.title, "favorites-item-title");
      title.href = item.canonical_url; title.target = "_blank"; title.rel = "noopener noreferrer";
      const duration = durationText(item.duration_seconds);
      content.append(title, node("small", `${item.episode_label ? "第 " + item.episode_label + " 集 · " : "清单第 " + item.ordinal + " 件 · "}${item.author || "未提供作者"} · ${item.source_kind_label || item.source_kind}`));
      if (item.error_message) content.append(node("small", item.error_message, "dialog-error"));
      const side = node("div", null, "favorites-item-side");
      const badge = node("span", labels[item.disposition] || item.disposition, "favorites-item-state");
      badge.dataset.state = item.disposition; side.append(badge);
      if (duration) {
        const time = node("span", duration);
        time.setAttribute("aria-label", "时长 " + duration); side.append(time);
      }
      if (item.entry_id || item.job_id) {
        const link = node("a", item.entry_id ? "查看资料" : "查看任务", "text-button");
        link.href = (item.entry_id ? "/articles/" : "/jobs/") + encodeURIComponent(item.entry_id || item.job_id);
        link.addEventListener("click", (event) => { event.preventDefault(); D.navigate(link.getAttribute("href")); });
        side.append(link);
      }
      row.append(input, cover, content, side); $("items").append(row);
    }
    if (!data.works?.length) $("items").append(node("p", ongoing.has(data.status) ? "正在读取清单…" : "本页没有作品。", "hint-copy"));
    $("items").querySelectorAll("input").forEach((input) => { if (input.dataset.work === focused) input.focus(); });
    $("page").textContent = `第 ${page} 页 · 共 ${data.total || 0} 件`;
    $("task").href = "/jobs/" + encodeURIComponent(jobId); $("task").hidden = false;
    controls();
  }
  function schedule() {
    clearTimeout(timer);
    if (active() && ongoing.has(current?.status)) timer = setTimeout(async () => {
      if (busy) { schedule(); return; }
      try { await load(); } catch (error) { $("error").textContent = error.message; schedule(); }
    }, 2500);
  }
  async function load() {
    if (!jobId) return;
    const id = jobId, token = epoch;
    const data = await request(jobPath() + `?page=${page}&limit=50`);
    if (id !== jobId || token !== epoch || !active()) return;
    render(data); schedule();
  }
  async function history() {
    const rows = await request(base), select = $("history");
    select.replaceChildren(node("option", "选择已有导入记录")); select.firstChild.value = "";
    rows.forEach((row) => {
      const option = node("option", `${row.profile?.name || "读取中"} · ${row.status_label || row.status} · ${row.summary?.discovered || 0} 件`);
      option.value = row.job_id; select.append(option);
    });
    select.value = jobId || "";
  }
  function run(action) {
    if (busy) return operation;
    busy = true; clearTimeout(timer); controls(); $("error").textContent = "";
    operation = (async () => {
      try { await action(); } catch (error) { $("error").textContent = error.message || "合集操作失败"; }
      finally { busy = false; controls(); schedule(); }
    })();
    return operation;
  }
  function saveSelection(body) {
    return run(async () => {
      try { await request(jobPath() + "/selection", body); failedSelection = null; await load(); }
      catch (error) { failedSelection = body; throw error; }
    });
  }
  function address() {
    if (!active()) return;
    const url = new URL(location.href);
    if (jobId) url.searchParams.set("job", jobId); else url.searchParams.delete("job");
    window.history.replaceState(window.history.state, "", url);
  }
  function reset() {
    current = null; page = 1; failedSelection = null; $("partial").checked = false;
    $("status").textContent = "尚未开始清点";
    ["summary", "result", "error", "save-hint"].forEach((id) => { $(id).textContent = ""; });
    $("items").replaceChildren(); $("warnings").replaceChildren();
    ["task", "gateway", "auth"].forEach((id) => { $(id).hidden = true; });
    $("page").textContent = "第 1 页"; controls();
  }
  window.addEventListener("douku:route", async (event) => {
    epoch++; clearTimeout(timer);
    if (event.detail.path !== "/imports/collections") return;
    D.hideAllViews(); D.setPage("imports-collections"); D.setNav("imports-nav"); view.classList.remove("hidden");
    document.title = "导入抖音合集 · 抖库"; D.warnIfWorkerDown("imports-collections-view");
    await operation;
    if (!active()) return;
    const requested = new URLSearchParams(location.search).get("job");
    if (requested !== jobId) { jobId = requested; reset(); }
    await run(async () => { await history(); if (jobId) await load(); });
  });
  $("form").addEventListener("submit", (event) => {
    event.preventDefault(); run(async () => {
      const data = await request("/api/collections/inventory", {source_text: $("source").value,
        options: {allow_long: $("allow-long").checked, retention: $("retention").value}});
      jobId = data.job_id; reset();
      address(); await history(); await load();
    });
  });
  $("history").addEventListener("change", () => run(async () => {
    jobId = $("history").value || null; epoch++; reset();
    address(); if (jobId) await load();
  }));
  $("select-all").addEventListener("click", () => saveSelection({decision: "selected", all_works: true}));
  $("skip-all").addEventListener("click", () => saveSelection({decision: "skipped", all_works: true}));
  $("save-retry").addEventListener("click", () => saveSelection(failedSelection));
  $("partial").addEventListener("change", controls);
  $("confirm").addEventListener("click", () => run(async () => {
    await request(jobPath() + "/confirm", {accept_partial: $("partial").checked}); await load(); await history();
  }));
  $("retry").addEventListener("click", () => run(async () => { await request(jobPath() + "/retry", {}); await load(); }));
  $("stop").addEventListener("click", () => run(async () => {
    await request("/api/jobs/" + encodeURIComponent(jobId) + "/cancel", {}); await load(); await history();
  }));
  $("refresh").addEventListener("click", () => run(async () => { await history(); await load(); }));
  $("previous").addEventListener("click", () => run(async () => { page--; await load(); }));
  $("next").addEventListener("click", () => run(async () => { page++; await load(); }));
  $("task").addEventListener("click", (event) => { event.preventDefault(); D.navigate($("task").getAttribute("href")); });
  controls();
})();
