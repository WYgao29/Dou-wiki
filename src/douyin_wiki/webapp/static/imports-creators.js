(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  let jobId = null;
  let page = 1;
  let current = null;
  let timer = null;
  let generation = 0;
  let selectionBusy = false;
  D.bindCheckList(D.$("creator-items"));
  const settle = new Set([
    "needs_selection", "failed", "completed", "completed_with_warnings",
    "needs_auth", "needs_review", "waiting_confirmation",
  ]);

  function stopPolling() {
    clearTimeout(timer);
    timer = null;
  }

  function viewActive() {
    return !D.$("imports-creators-view").classList.contains("hidden");
  }

  function statusOf(data = current) {
    return data?.job?.status || data?.status || "";
  }

  function schedule() {
    stopPolling();
    const status = statusOf();
    if (!viewActive() || !jobId || (status && settle.has(status))) return;
    const token = generation;
    timer = setTimeout(() => {
      loadInventory().then(() => {
        if (token === generation) schedule();
      }).catch((error) => {
        if (token !== generation || !viewActive()) return;
        const summary = D.$("creator-summary");
        if (summary) summary.textContent = `刷新失败：${error.message}。将继续重试。`;
        schedule();
      });
    }, 2500);
  }

  function beijingStamp(value) {
    if (!value) return "尚未同步";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const text = new Intl.DateTimeFormat("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
    return `${text}（北京时间）`;
  }

  function clockFromSeconds(seconds) {
    const total = Number(seconds);
    if (!Number.isFinite(total) || total <= 0) return "";
    const rounded = Math.round(total);
    const minutes = Math.floor(rounded / 60);
    const secs = rounded % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  }

  function kindLabel(work) {
    return work.source_kind_label
      || {video: "视频", image_note: "图文", article: "文章"}[work.source_kind]
      || "作品";
  }

  function decisionText(work) {
    if (work.entry_id) return "已入库";
    if (work.decision === "skipped") return "已跳过";
    if (work.decision === "selected") return "已勾选";
    return "尚未入库";
  }

  function applyFocusMode() {
    const focused = Boolean(jobId);
    D.$("imports-creators-view")?.classList.toggle("is-idle", !focused);
    D.$("creators-list")?.classList.toggle("hidden", focused);
    const bar = D.$("creator-focus-bar");
    if (bar) bar.classList.toggle("hidden", !focused);
    if (!focused) return;
    const job = current?.job;
    const name = job?.display_title || job?.result?.creator_name || "这位博主";
    D.$("creator-focus-name").textContent = name;
    const status = statusOf();
    D.$("creator-focus-copy").textContent = settle.has(status)
      ? "勾选要导入的作品，确认后才会下载。"
      : "正在清点，大约每 2.5 秒自动刷新。完成后就可以勾选。";
    const back = D.$("creator-back-job");
    if (back) {
      back.href = `/jobs/${encodeURIComponent(jobId)}`;
      back.onclick = (event) => {
        event.preventDefault();
        D.navigate(`/jobs/${encodeURIComponent(jobId)}`);
      };
    }
  }

  async function loadCreators() {
    const data = await D.api("/api/creators");
    const root = D.$("creators-list");
    if (!data.items?.length) {
      root.replaceChildren(D.emptyState("还没有已同步的博主", "请让 Agent 导入博主作品。"));
      return;
    }
    root.replaceChildren(...data.items.map((creator) => {
      const card = D.node("article", null, "creator-row");
      card.append(
        D.node("h2", creator.nickname),
        D.node("p", `${creator.work_count || 0} 个作品 · 上次同步 ${beijingStamp(creator.last_synced_at)}`),
      );
      return card;
    }));
  }

  function renderInventory() {
    const root = D.$("creator-items");
    root.replaceChildren();
    const status = statusOf();
    const polling = Boolean(status) && !settle.has(status);
    D.$("creator-status").textContent = current.job
      ? `${current.job.state_label} · ${current.job.message_for_user}`
      : (current.status || "尚未开始清点");
    const summary = current.summary || {};
    const notImported = Number(summary.not_imported ?? 0);
    const selectedCount = Number(summary.selected || 0);
    const imported = Number(summary.imported || 0);
    const pageItems = current.items || [];
    const fresh = pageItems.filter((item) => item.is_new === true || item.is_new_label === "新增作品").length;
    const filter = D.$("creator-not-imported");
    if (filter) filter.disabled = false;
    const counts = `尚未入库 ${notImported} · 已勾选 ${selectedCount} · 已入库 ${imported}`;
    D.$("creator-summary").textContent = [
      current.partial ? "清单可能不完整，确认前需要额外勾选。" : counts,
      `本页 ${pageItems.length} 条，其中新作品 ${fresh} 条。`,
      filter?.checked ? "已入库的作品先隐藏了，取消「只看尚未入库」可看全部。" : "",
      polling ? "正在清点，大约每 2.5 秒自动刷新。" : "",
    ].filter(Boolean).join(" ");
    D.$("creator-partial-row").hidden = !current.partial || status !== "needs_selection";
    const selected = Number(summary.selected || 0);
    D.$("creator-selected-count").textContent = `已选 ${selected} / 共 ${Number(summary.total || 0)}`;
    const canSelect = status === "needs_selection" && !selectionBusy;
    for (const item of current.items || []) {
      const row = D.node("label", null, "work-row row--check");
      const input = document.createElement("input");
      input.type = "checkbox";
      const work = item.work || item;
      const inLibrary = Boolean(work.entry_id);
      input.dataset.work = work.work_id;
      input.setAttribute("aria-label", `选择 ${work.title || "未命名作品"}`);
      input.checked = !inLibrary && work.decision === "selected";
      input.disabled = !canSelect || inLibrary || work.decision === "imported";
      input.addEventListener("change", async () => {
        if (selectionBusy) return;
        const decision = input.checked ? "selected" : "skipped";
        selectionBusy = true;
        root.querySelectorAll("input").forEach((node) => { node.disabled = true; });
        try {
          await D.api(`/api/creator-imports/${encodeURIComponent(jobId)}/selection`, {
            method: "POST", body: JSON.stringify({decision, work_ids: input.selectionWorkIds || [work.work_id]}),
          });
        } catch (error) { D.toast(error.message); }
        finally {
          selectionBusy = false;
          try { await loadInventory(); } catch (error) { renderInventory(); D.toast(error.message); }
          const focused = [...root.querySelectorAll("input[data-work]")].find((node) => node.dataset.work === root.dataset.focusWork);
          if (document.activeElement === document.body) focused?.focus({preventScroll: true});
          schedule();
        }
      });
      const published = work.published_at ? beijingStamp(work.published_at).replace("（北京时间）", "").trim() : "";
      const duration = clockFromSeconds(work.duration_seconds);
      const isNew = item.is_new === true || item.is_new_label === "新增作品";
      const meta = [kindLabel(work), decisionText(work), isNew ? "新作品" : "", duration, published]
        .filter(Boolean)
        .join(" · ");
      const copy = D.node("span", null, "work-copy");
      copy.append(D.node("strong", work.title || "未命名作品"), D.node("small", meta));
      row.append(input, copy);
      root.append(row);
    }
    if (!current.items?.length) {
      root.append(D.node("p", polling ? "清点还没写出作品，请稍等自动刷新。" : "当前筛选下没有作品。"));
    }
    D.$("creator-page").textContent = `第 ${current.page || page} 页`;
    D.$("creator-confirm").disabled = !canSelect || !selected || (current.partial && !D.$("creator-partial").checked);
    D.$("creator-select-all").disabled = !canSelect;
    D.$("creator-exclude-all").disabled = !canSelect;
    applyFocusMode();
  }

  async function loadInventory() {
    if (!jobId) {
      applyFocusMode();
      return;
    }
    const token = generation;
    const params = new URLSearchParams({page, limit: 50, query: D.$("creator-query").value});
    if (D.$("creator-not-imported")?.checked) params.set("not_imported", "true");
    const data = await D.api(`/api/creator-imports/${encodeURIComponent(jobId)}?${params}`);
    if (token !== generation || !viewActive()) return;
    current = data;
    renderInventory();
  }

  window.addEventListener("douku:route", async (event) => {
    const path = String(event.detail.path || "").split("?")[0];
    if (path !== "/imports/creators") {
      generation += 1;
      stopPolling();
      return;
    }
    generation += 1;
    stopPolling();
    D.hideAllViews();
    D.setPage("imports-creators");
    D.setNav("imports-nav");
    D.$("imports-creators-view").classList.remove("hidden");
    document.title = "博主导入 · 抖库";
    D.warnIfWorkerDown("imports-creators-view");
    const params = new URLSearchParams(location.search);
    jobId = params.get("job");
    page = 1;
    current = null;
    try {
      await loadCreators();
      if (jobId) await loadInventory();
      else applyFocusMode();
      schedule();
    } catch (error) {
      D.toast(error.message);
    }
  });

  D.$("creator-partial")?.addEventListener("change", renderInventory);
  ["creator-select-all", "creator-exclude-all"].forEach((id) => { D.$(id).title = D.$("creator-select-hint").textContent; });
  D.$("creator-query")?.addEventListener("input", () => { page = 1; });
  D.$("creator-query")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    page = 1;
    loadInventory().then(schedule).catch((error) => D.toast(error.message));
  });
  D.$("creator-not-imported")?.addEventListener("change", () => {
    page = 1;
    loadInventory().then(schedule).catch((error) => D.toast(error.message));
  });
  async function postBulkDecision(decision) {
    if (!jobId) {
      D.toast("先开始清点，再改选择");
      return;
    }
    await D.api(`/api/creator-imports/${encodeURIComponent(jobId)}/selection`, {
      method: "POST",
      body: JSON.stringify({decision}),
    });
    await loadInventory();
  }
  D.$("creator-select-all")?.addEventListener("click", () => {
    postBulkDecision("selected").catch((error) => D.toast(error.message));
  });
  D.$("creator-exclude-all")?.addEventListener("click", () => {
    postBulkDecision("skipped").catch((error) => D.toast(error.message));
  });
  D.$("creator-confirm")?.addEventListener("click", async () => {
    const job = await D.api(`/api/creator-imports/${encodeURIComponent(jobId)}/confirm`, {
      method: "POST",
      body: JSON.stringify({accept_partial: D.$("creator-partial").checked}),
    });
    D.toast("已提交导入。入库后可在资料库多选文章，再创建专题。");
    D.navigate(`/jobs/${job.id}`);
  });
  D.$("creator-previous")?.addEventListener("click", async () => {
    page = Math.max(1, page - 1);
    await loadInventory();
  });
  D.$("creator-next")?.addEventListener("click", async () => {
    page += 1;
    await loadInventory();
  });
})();
