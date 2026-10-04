(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  const $ = (id) => document.getElementById("favorites-" + id);
  const view = D.$("imports-favorites-view");
  if (!view) return;
  const base = "/api/favorites/imports", node = D.node;
  const ongoing = new Set(["queued", "inventorying", "dispatching", "monitoring"]);
  D.bindCheckList($("items"));
  let jobId = null, current = null, page = 1, busy = false, generation = 0;
  let pollTimer = null, elapsedTimer = null, searchTimer = null, saveTimer = null;
  let saving = null, saveError = false, configurationJob = null;
  const pending = new Map();

  async function request(path, body) {
    return D.api(path, body === undefined ? {} : {
      method: "POST", body: JSON.stringify(body),
    });
  }
  function active() { return !view.classList.contains("hidden"); }
  function stopPolling() { clearTimeout(pollTimer); pollTimer = null; }
  function canSelect() {
    return current?.status === "needs_selection" && !current.directory_only && !current.confirmed && !current.cancel_requested;
  }
  function legacy(data) {
    return (data?.warnings || []).some((warning) => String(warning).includes("无法确认收藏夹目录分页结束"));
  }
  function updateJobAddress() {
    if (!active() || location.pathname !== "/imports/favorites") return;
    const url = new URL(location.href);
    if (jobId) url.searchParams.set("job", jobId);
    else url.searchParams.delete("job");
    window.history.replaceState(window.history.state, "", url);
  }
  function selectedCount() {
    let count = Number(current?.summary?.selected || 0);
    for (const change of pending.values()) count += Number(change.selected) - Number(change.previous);
    return Math.max(0, count);
  }
  function selectionStatus() {
    const imported = current?.summary?.in_library ?? current?.summary?.imported ?? 0;
    $("selected-count").textContent = (current?.status === "cancelled" ? "保留选择 " : current?.confirmed ? "本批所选 " : "将导入 ") + selectedCount() + " 件" +
      (imported ? " · " + imported + " 件已在资料库" + (canSelect() ? "，会跳过" : "") : "");
    $("save-hint").textContent = saveError ? "选择尚未保存，请重试后再导入。"
      : saving || pending.size ? "正在保存选择…" : current && canSelect() ? "选择已保存" : "";
    $("save-retry").hidden = !saveError;
  }
  function controls() {
    view.querySelectorAll("button[data-action]").forEach((button) => { button.disabled = busy; });
    ["scope", "history", "images", "mode", "limit"].forEach((id) => { $(id).disabled = busy; });
    const searchable = Boolean(current && !current.directory_only && current.summary?.discovered);
    ["search", "query", "filter", "only-new", "only-unimported"].forEach((id) => {
      $(id).disabled = busy || !searchable;
    });
    $("search-hint").textContent = searchable ? "输入标题或作者会自动筛选本次已读取的作品。"
      : !current || current.directory_only ? "先点击上方“更新抖音收藏”读取作品，再搜索标题或作者。"
      : ongoing.has(current.status) ? "正在读取作品，读取后即可搜索。"
      : "当前清单暂无作品可搜索，可调整范围后重新读取。";
    const boxes = [...$("folder-list").querySelectorAll("input")];
    $("scan").disabled = busy || ($("scope").value === "folders" && !boxes.some((box) => box.checked));
    ["folders-all", "folders-invert"].forEach((id) => { $(id).disabled = busy || !boxes.length; });
    $("confirm").disabled = busy || !canSelect() || !selectedCount() || pending.size > 0 ||
      Boolean(saving) || saveError || (!current.complete && !$("partial").checked);
    $("confirm").hidden = Boolean(current?.confirmed || current?.directory_only || current?.status === "cancelled");
    if (jobId) $("history").value = jobId;
    ["select-all", "exclude-all", "select-page", "select-filtered", "select-folder"].forEach((id) => {
      $(id).disabled = busy || !canSelect();
    });
    $("select-page").disabled ||= !$("items").querySelector('input[data-eligible="true"]');
    $("select-filtered").disabled ||= !current?.total;
    $("select-folder").hidden = !$("filter").value || !searchable;
    $("previous").disabled = busy || !current || page <= 1;
    $("next").disabled = busy || !current?.has_more;
    $("retry").hidden = !current?.can_retry;
    $("retry").disabled = busy || !current?.can_retry;
    $("stop").hidden = !current || current.directory_only && !ongoing.has(current.status) ||
      !(ongoing.has(current.status) || canSelect());
    $("stop").disabled = busy || Boolean(current?.cancel_requested);
    $("stop").textContent = current?.cancel_requested ? "正在停止…"
      : current?.confirmed ? "取消剩余任务" : canSelect() ? "取消本批" : "停止读取";
    $("delete-history").disabled = busy || !jobId;
    $("folders").hidden = $("scope").value !== "folders";
    view.querySelectorAll("input[data-work]").forEach((input) => {
      input.disabled = busy || !canSelect() || input.dataset.eligible !== "true";
    });
    boxes.forEach((input) => { input.disabled = busy; });
    selectionStatus();
  }
  async function run(action, {flush = true} = {}) {
    if (busy) return;
    busy = true; stopPolling(); generation++; clearTimeout(searchTimer);
    $("error").textContent = ""; controls();
    try {
      if (flush) await flushSelections();
      await action();
    } catch (error) {
      const detail = String(error?.message || "操作未完成");
      const message = "抖音收藏操作失败：" + detail;
      $("error").textContent = message; D.toast(message);
    } finally {
      busy = false; controls(); schedule();
    }
  }
  function schedule() {
    stopPolling();
    if (active() && jobId && ongoing.has(current?.status)) {
      pollTimer = setTimeout(() => run(load), 2500);
    }
  }
  function queueChoices(ids, selected) {
    if (busy || !canSelect()) return;
    const items = new Map((current.items || []).map((item) => [item.work_id, item]));
    for (const id of ids) {
      const input = [...$("items").querySelectorAll("input[data-work]")].find((box) => box.dataset.work === id);
      if (!input || input.disabled) continue;
      const previous = pending.get(id)?.previous ?? Boolean(items.get(id)?.selected);
      pending.set(id, {selected, previous});
      input.checked = selected;
    }
    saveError = false; clearTimeout(saveTimer);
    saveTimer = setTimeout(() => flushSelections().catch(() => {}), 400);
    controls();
  }
  async function flushSelections() {
    clearTimeout(saveTimer);
    if (saving) {
      await saving;
      if (pending.size) return flushSelections();
      return;
    }
    if (!pending.size) return;
    const batch = new Map(pending), target = jobId;
    const task = (async () => {
      for (const selected of [true, false]) {
        const ids = [...batch].filter(([, change]) => change.selected === selected).map(([id]) => id);
        if (ids.length) await request(base + "/" + encodeURIComponent(target) + "/selection", {selected, work_ids: ids});
      }
      await load();
      for (const [id, change] of batch) if (pending.get(id) === change) pending.delete(id);
      saveError = false;
    })();
    saving = task; controls();
    try { await task; }
    catch (error) {
      if (error.status === 409 && target === jobId) {
        try { await load(); } catch (_error) { /* Keep unsaved choices if the state cannot be read. */ }
        if (!canSelect()) {
          pending.clear(); saveError = false;
          render(current);
          D.toast("本批状态已改变，未保存的勾选不能继续提交。可以重新读取或选择其他记录。");
          return;
        }
      }
      saveError = true; D.toast("选择尚未保存：" + error.message); throw error;
    } finally { saving = null; controls(); }
    if (pending.size) return flushSelections();
  }
  function clearFilters() {
    page = 1; $("query").value = ""; $("filter").value = "";
    $("only-new").checked = false; $("only-unimported").checked = true;
    $("partial").checked = false;
  }
  function resetView(message = "尚未开始更新") {
    current = null; configurationJob = null; pending.clear(); saveError = false;
    clearTimeout(saveTimer); clearTimeout(elapsedTimer); clearFilters();
    $("status").textContent = message;
    $("summary").textContent = "更新抖音收藏会读取清单，勾选并确认后才会导入。";
    ["waits", "warnings", "items", "folder-list", "error"].forEach((id) => $(id).replaceChildren());
    ["auth", "gateway", "partial-row", "cancelled", "progress", "elapsed", "library"].forEach((id) => { $(id).hidden = true; });
    $("filter").replaceChildren(node("option", "全部收藏夹")); $("filter").firstChild.value = "";
    $("filter").hidden = true; $("page").textContent = "第 1 / 1 页 · 匹配 0 件";
    $("folder-hint").textContent = "先读取目录，再选择要更新的收藏夹。";
    controls();
  }
  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(date);
  }
  async function history() {
    const rows = await request(base);
    $("history").replaceChildren(node("option", "选择已有任务")); $("history").firstChild.value = "";
    for (const item of rows) {
      const scope = item.directory_only ? "仅目录" : item.folder_ids?.length ? item.folder_ids.length + " 个收藏夹" : "全部收藏";
      const count = item.directory_only ? (item.folders?.length || 0) + " 个夹" : (item.summary?.discovered || 0) + " 件";
      const option = node("option", [formatTime(item.created_at), item.nickname, scope, count,
        item.status_label || item.status, legacy(item) ? "旧版本" : ""].filter(Boolean).join(" · "));
      option.value = item.job_id; $("history").append(option);
    }
    $("history").value = jobId || "";
  }
  function elapsed() {
    if (!current?.created_at) { $("elapsed").hidden = true; return; }
    const end = ongoing.has(current.status) ? Date.now() : Date.parse(current.updated_at || current.created_at);
    const seconds = Math.max(0, Math.floor((end - Date.parse(current.created_at)) / 1000));
    if (!Number.isFinite(seconds)) return;
    $("elapsed").hidden = false;
    $("elapsed").textContent = (ongoing.has(current.status) ? "本批已用时 " : "本批用时 ") +
      Math.floor(seconds / 60) + ":" + String(seconds % 60).padStart(2, "0");
  }
  function render(data) {
    current = data; updateJobAddress();
    const configurationChanged = configurationJob !== data.job_id;
    if (configurationChanged) {
      configurationJob = data.job_id;
      if (data.directory_only) $("scope").value = "folders";
      else {
        $("scope").value = data.folder_ids?.length ? "folders" : "all";
        $("mode").value = data.update_mode || "incremental"; $("images").checked = Boolean(data.include_images);
      }
    }
    const summary = data.summary || {}, reading = ongoing.has(data.status) && !data.confirmed;
    const directoryReady = data.directory_only && ["completed", "completed_with_warnings"].includes(data.status);
    const label = directoryReady ? legacy(data) ? "旧版本目录记录" : "收藏夹目录已读取" : data.status_label || data.status;
    $("status").textContent = (data.nickname || "收藏任务") + " · " + label;
    $("status").classList.toggle("is-running", ongoing.has(data.status));
    const discovered = summary.discovered || 0, imported = summary.in_library ?? summary.imported ?? 0;
    $("summary").textContent = directoryReady ? "已读取 " + (data.folders?.length || 0) + " 个收藏夹 · 尚未读取作品"
      : reading ? (data.cancel_requested ? "正在停止读取" : data.directory_only ? "正在读取收藏夹目录" : "正在读取作品") + " · 已读 " + discovered + " 件"
      : data.confirmed ? "所选 " + (summary.selected || 0) + " 件 · 已完成 " + (summary.completed || 0) + " · 失败 " + (summary.failed || 0) + " · 处理中 " + (summary.active || 0)
      : "共 " + discovered + " 件：未入库 " + Math.max(0, discovered - imported) + "、已在资料库 " + imported +
        (data.first_read ? " · 首次读取" : " · 新增 " + (summary.new || 0));
    const waits = {needs_auth: "需要登录", awaiting_agent_analysis: "待外部 Agent 接续", waiting_confirmation: "待本人确认", needs_review: "待复核"};
    $("waits").textContent = Object.entries(waits).filter(([status]) => summary.child_status_counts?.[status])
      .map(([status, text]) => text + " " + summary.child_status_counts[status]).join(" · ");
    $("warnings").replaceChildren(...(data.warnings || []).map((text) => node("li", text)));
    if (legacy(data)) $("warnings").prepend(node("li", "这是旧版本留下的读取结果。点击“读取我的收藏夹”可用当前版本重新读取，旧记录不会被改写。"));
    $("auth").hidden = !(data.status === "needs_auth" || summary.child_status_counts?.needs_auth);
    $("gateway").hidden = !(data.analysis_mode === "gateway" && data.confirmed && ongoing.has(data.status));
    $("partial-row").hidden = data.complete || data.directory_only || data.confirmed || data.status !== "needs_selection";
    $("retry").textContent = data.confirmed ? "重试失败作品" : data.status === "needs_auth" ? "授权好了，重试读取" : "重试读取";
    $("cancelled").hidden = data.status !== "cancelled";
    $("restart").textContent = data.directory_only ? "重新读取目录" : "重新读取清单";
    $("error").textContent = data.error_message || "";
    const entryIds = data.imported_entry_ids || [];
    $("library").hidden = !entryIds.length;
    $("library").href = "/?favorites_job=" + encodeURIComponent(jobId);
    $("library").textContent = "在资料库查看本批（" + entryIds.length + " 件）";
    $("progress").hidden = !ongoing.has(data.status) && !data.confirmed;
    $("progress").classList.toggle("is-indeterminate", reading);
    if (reading) $("progress").removeAttribute("aria-valuenow");
    else $("progress").setAttribute("aria-valuenow", String(Math.round((data.progress || 0) * 100)));
    $("progress").setAttribute("aria-valuemin", "0"); $("progress").setAttribute("aria-valuemax", "100");
    $("progress-fill").style.width = Math.min(100, Math.max(0, (data.progress || 0) * 100)) + "%";
    clearTimeout(elapsedTimer); elapsed();
    if (ongoing.has(data.status) && active()) elapsedTimer = setTimeout(function tick() {
      if (!active() || !ongoing.has(current?.status)) return;
      elapsed(); elapsedTimer = setTimeout(tick, 1000);
    }, 1000);
    const chosen = configurationChanged && !data.directory_only ? new Set(data.folder_ids || [])
      : new Set([...$("folder-list").querySelectorAll("input:checked")].map((input) => input.value));
    $("folder-list").replaceChildren();
    for (const folder of data.folders || []) {
      const label = node("label", null, "check-row"), input = node("input");
      input.type = "checkbox"; input.value = folder.id; input.checked = chosen.has(folder.id);
      input.addEventListener("change", controls);
      label.append(input, node("span", folder.name + (folder.reported_count == null ? "" : "（" + folder.reported_count + "）")));
      $("folder-list").append(label);
    }
    $("folder-hint").textContent = reading && data.directory_only ? "正在读取收藏夹，完成后即可勾选。"
      : data.folders?.length ? "勾选收藏夹，再点击“更新抖音收藏”读取作品。"
      : "当前记录没有可用目录，点击“读取我的收藏夹”重新读取。";
    const filter = $("filter").value;
    $("filter").replaceChildren(node("option", "全部收藏夹")); $("filter").firstChild.value = "";
    for (const folder of data.folders || []) {
      const option = node("option", folder.name); option.value = folder.id; $("filter").append(option);
    }
    $("filter").value = filter; $("filter").hidden = !data.folders?.length;
    const focused = document.activeElement?.dataset?.work;
    $("items").replaceChildren();
    for (const item of data.items || []) {
      const row = node("div", null, "favorites-item row--check"), input = node("input");
      input.type = "checkbox"; input.dataset.work = item.work_id;
      const change = pending.get(item.work_id);
      if (change) change.previous = Boolean(item.selected);
      input.checked = change ? change.selected : item.selected;
      input.dataset.eligible = String(!item.entry_id && !["unsupported", "unavailable"].includes(item.disposition) &&
        item.source_kind !== "article" && (item.source_kind !== "image_note" || data.include_images));
      row.dataset.eligible = input.dataset.eligible;
      input.setAttribute("aria-label", "选择 " + item.title);
      input.addEventListener("change", () => queueChoices(input.selectionWorkIds || [item.work_id], input.checked));
      const content = node("div", null, "favorites-item-content");
      const match = item.title.match(/(\s*#[^\s#]+){2,}\s*$/);
      const display = match ? item.title.slice(0, match.index).trim() || item.title : item.title;
      let title = node("span", display, "favorites-item-title");
      try {
        const url = new URL(item.canonical_url);
        if (url.protocol === "https:" && url.hostname === "www.douyin.com") {
          title = node("a", display, "favorites-item-title"); title.href = url.href;
          title.target = "_blank"; title.rel = "noopener noreferrer";
        }
      } catch (_error) { /* Invalid source links remain plain text. */ }
      title.title = item.title; content.append(title);
      if (match && display !== item.title) content.append(node("small", match[0].trim(), "favorites-item-topics"));
      content.append(node("small", (item.author || "未知作者") + " · " + (item.source_kind_label || (item.source_kind === "video" ? "视频" : item.source_kind === "image_note" ? "图文" : "文章")) + (item.is_new && !data.first_read ? " · 新发现" : "")));
      if (item.error_message) content.append(node("small", item.error_message, "dialog-error"));
      const status = item.entry_id ? "已在资料库" : item.disposition === "excluded" ? input.checked ? "待导入" : "未选择" : item.disposition_label || ({pending: "待导入", unsupported: "暂不支持", unavailable: "不可用", active: "处理中", completed: "已完成", failed: "失败"}[item.disposition] || item.disposition);
      const badge = node("span", status, "favorites-item-state"); badge.dataset.state = item.entry_id ? "imported" : item.disposition;
      const meta = node("div", null, "favorites-item-side"); meta.append(badge);
      if (item.entry_id) {
        const link = node("a", "打开资料"); link.href = "/articles/" + encodeURIComponent(item.entry_id);
        meta.append(link);
      }
      row.append(input, content, meta); $("items").append(row);
      if (focused === item.work_id) input.focus({preventScroll: true});
    }
    if (!data.items?.length) $("items").append(node("p", data.directory_only ? "目录已读取。选择上方范围与收藏夹后更新作品。" : "当前筛选下暂无作品。"));
    page = data.page || page;
    const totalPages = Math.max(1, Math.ceil((data.total || 0) / Number($("limit").value)));
    $("page").textContent = "第 " + page + " / " + totalPages + " 页 · 匹配 " + (data.total || 0) + " 件";
    controls();
  }
  function filters() {
    return {query: $("query").value, folder_id: $("filter").value || null,
      only_new: $("only-new").checked, only_unimported: $("only-unimported").checked};
  }
  async function load() {
    if (!jobId) return;
    const token = generation, target = jobId;
    const values = filters(), params = new URLSearchParams({page, limit: $("limit").value, query: values.query});
    if (values.folder_id) params.set("folder_id", values.folder_id);
    if (values.only_new) params.set("only_new", "true");
    if (values.only_unimported) params.set("only_unimported", "true");
    const data = await request(base + "/" + encodeURIComponent(target) + "?" + params);
    if (token === generation && target === jobId && active()) render(data);
  }
  async function start(directoryOnly) {
    const folderIds = [...$("folder-list").querySelectorAll("input:checked")].map((input) => input.value);
    if (!directoryOnly && $("scope").value === "folders" && !folderIds.length) throw new Error("请先选择至少一个收藏夹");
    const result = await request(base, {directory_only: directoryOnly, include_images: $("images").checked,
      update_mode: $("mode").value, folder_ids: !directoryOnly && $("scope").value === "folders" ? folderIds : null});
    jobId = result.job_id; configurationJob = null; current = null; clearFilters();
    await load(); await history();
  }
  async function confirmImport() {
    if (!canSelect()) throw new Error("本批已不能导入，请重新读取或选择其他记录");
    const count = selectedCount(), preview = current.confirmation_preview || {};
    if (!count) throw new Error("请先选择尚未入库的作品");
    const mode = current.analysis_mode === "gateway" ? "网关 Agent，后续由外部 Agent 接续" : "当前模型";
    $("confirm-description").textContent = "将处理 " + count + " 件所选作品" +
      (preview.new_tasks != null ? "：新建 " + preview.new_tasks + " 个任务，复用 " + (preview.reused_tasks || 0) + " 个进行中任务" : "") +
      "。清单中 " + (preview.already_imported ?? current.summary?.in_library ?? current.summary?.imported ?? 0) +
      " 件已在资料库，会跳过。后续会下载、识别并分析作品。分析方式：" + mode + "。";
    const dialog = $("confirm-dialog"), form = $("confirm-form"), input = $("confirm-count"), button = $("confirm-submit");
    $("confirm-count-label").hidden = count <= 30; input.value = "";
    const update = () => { button.disabled = count > 30 && input.value.trim() !== String(count); };
    update();
    const ok = await new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return; settled = true;
        form.removeEventListener("submit", submit); dialog.removeEventListener("cancel", cancel);
        dialog.removeEventListener("close", close); input.removeEventListener("input", update);
        dialog.close(); resolve(value);
      };
      const submit = (event) => {
        event.preventDefault();
        if (event.submitter?.value === "confirm") { if (!button.disabled) finish(true); }
        else finish(false);
      };
      const cancel = (event) => { event.preventDefault(); finish(false); };
      const close = () => finish(false);
      form.addEventListener("submit", submit); dialog.addEventListener("cancel", cancel);
      dialog.addEventListener("close", close); input.addEventListener("input", update);
      dialog.showModal(); (count > 30 ? input : button).focus();
    });
    if (!ok) return;
    await request(base + "/" + encodeURIComponent(jobId) + "/confirm", {accept_partial: $("partial").checked});
    await load(); await history(); D.navigate("/jobs/" + encodeURIComponent(jobId));
  }
  function search() { return run(async () => { page = 1; await load(); }); }
  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { if (busy) scheduleSearch(); else search(); }, 300);
  }
  window.addEventListener("douku:route", async (event) => {
    const path = String(event.detail.path || "").split("?")[0];
    if (path !== "/imports/favorites") {
      stopPolling(); clearTimeout(elapsedTimer); clearTimeout(searchTimer); generation++; return;
    }
    D.hideAllViews(); D.setPage("imports-favorites"); D.setNav("imports-nav"); view.classList.remove("hidden");
    document.title = "导入抖音收藏 · 抖库"; D.warnIfWorkerDown("imports-favorites-view");
    const requested = new URLSearchParams(location.search).get("job");
    await run(async () => {
      if (requested !== jobId) { jobId = requested; resetView(); }
      await history(); if (jobId) await load();
    });
    request("/api/auth/status").then((data) => {
      const channel = data.channels?.douyin;
      if (!channel || !active()) return;
      const link = node("a", "检查抖音账号授权"); link.href = "/settings/auth?channel=douyin";
      $("auth-state").replaceChildren(node("span", (channel.user_state_label || "授权状态需在设置页检查") + " · "), link);
    }).catch(() => {});
  });
  $("directory").addEventListener("click", () => run(() => start(true)));
  $("scan").addEventListener("click", () => run(() => start(false)));
  $("scope").addEventListener("change", controls);
  ["folders-all", "folders-invert"].forEach((id) => $(id).addEventListener("click", () => {
    $("folder-list").querySelectorAll("input").forEach((box) => { box.checked = id === "folders-all" || !box.checked; });
    controls();
  }));
  $("history").addEventListener("change", () => {
    const next = $("history").value;
    if (!next || busy) return;
    run(async () => { jobId = next; configurationJob = null; clearFilters(); await load(); });
  });
  $("refresh").addEventListener("click", () => run(async () => { await history(); await load(); }));
  $("search").addEventListener("click", search);
  ["filter", "only-new", "only-unimported", "limit"].forEach((id) => $(id).addEventListener("change", search));
  $("query").addEventListener("input", scheduleSearch);
  $("query").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); search(); } });
  $("previous").addEventListener("click", () => run(async () => { page--; await load(); }));
  $("next").addEventListener("click", () => run(async () => { page++; await load(); }));
  $("select-page").addEventListener("click", () => queueChoices(
    [...$("items").querySelectorAll('input[data-eligible="true"]')].map((box) => box.dataset.work), true));
  $("select-filtered").addEventListener("click", () => run(async () => {
    await request(base + "/" + encodeURIComponent(jobId) + "/selection", {selected: true, filters: filters()}); await load();
  }));
  $("select-folder").addEventListener("click", () => run(async () => {
    await request(base + "/" + encodeURIComponent(jobId) + "/selection", {selected: true, filters: {
      folder_id: $("filter").value, only_unimported: true,
    }}); await load();
  }));
  [true, false].forEach((selected) => $(selected ? "select-all" : "exclude-all").addEventListener("click", () => run(async () => {
    await request(base + "/" + encodeURIComponent(jobId) + "/selection", {selected}); await load();
  })));
  $("save-retry").addEventListener("click", () => run(async () => { await flushSelections(); }, {flush: false}));
  $("partial").addEventListener("change", controls);
  $("confirm").addEventListener("click", () => run(confirmImport));
  $("retry").addEventListener("click", () => run(async () => {
    await request(base + "/" + encodeURIComponent(jobId) + "/retry", {}); await load();
  }));
  $("restart").addEventListener("click", () => run(() => start(Boolean(current?.directory_only))));
  $("stop").addEventListener("click", () => run(async () => {
    const ok = await D.confirmDanger({title: current.confirmed ? "取消剩余任务" : canSelect() ? "取消本批" : "停止本批读取",
      confirmLabel: "确认停止", description: "保留已读清单、选择和已入库资料。其他批次也在使用的任务会继续。此批停止后，可重新读取新建批次。"});
    if (ok) { await request("/api/jobs/" + encodeURIComponent(jobId) + "/cancel", {}); await load(); await history(); }
  }));
  $("clear-cache").addEventListener("click", () => run(async () => {
    const preview = await request("/api/favorites/cache/clear", {confirmed: false});
    if (!await D.confirmDanger({title: "清理收藏缓存", confirmLabel: "确认清理",
      description: preview.will_delete.join("；") + "。保留：" + preview.will_keep.join("；") + "。共 " + preview.run_count + " 份缓存。"})) return;
    const result = await request("/api/favorites/cache/clear", {confirmed: true});
    D.toast("已清除 " + result.run_count + " 份本地收藏缓存");
  }));
  $("delete-history").addEventListener("click", () => run(async () => {
    if (!jobId) throw new Error("请先选择一条导入记录");
    const path = "/api/imports/" + encodeURIComponent(jobId);
    const preview = await D.api(path, {method: "DELETE", body: JSON.stringify({confirmed: false})});
    if (!await D.confirmDanger({title: "删除这条导入记录", confirmLabel: "确认删除",
      description: preview.will_delete.join("；") + "。保留：" + preview.will_keep.join("；")})) return;
    await D.api(path, {method: "DELETE", body: JSON.stringify({confirmed: true})});
    jobId = null; updateJobAddress(); resetView("已删除该导入记录，已入库资料仍保留。"); await history();
  }));
  $("library").addEventListener("click", (event) => { event.preventDefault(); D.navigate($("library").getAttribute("href")); });
  controls();
})();
