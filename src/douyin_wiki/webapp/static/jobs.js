(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  let pollTimer = null;
  let events = null;
  let navigationEpoch = 0;
  let requestEpoch = 0;
  const childPages = new Map();
  const terminal = new Set(["completed", "completed_with_warnings", "failed", "cancelled"]);

  function stop() {
    clearTimeout(pollTimer);
    pollTimer = null;
    if (events) {
      events.close();
      events = null;
    }
  }

  function schedule(path) {
    if (location.pathname !== path || !path.startsWith("/jobs")) return;
    stop();
    events = new EventSource("/api/events");
    const stream = events;
    const reload = () => {
      if (location.pathname === path || location.pathname.startsWith("/jobs")) {
        window.dispatchEvent(new CustomEvent("douku:route", {detail: {path: location.pathname}}));
      }
    };
    ["jobs", "auth", "library"].forEach((name) => events.addEventListener(name, reload));
    events.addEventListener("error", () => {
      if (events !== stream) return;
      events?.close();
      events = null;
      pollTimer = setTimeout(async () => {
        if (location.pathname !== path) return;
        try {
          if (await loadFromPath(path)) schedule(path);
        } catch (error) {
          if (location.pathname === path) D.toast(error.message);
        }
      }, 8000);
    });
  }

  function routePath(value) {
    return String(value || "").split("?")[0];
  }

  function requestScope(viewId) {
    const request = ++requestEpoch, navigation = navigationEpoch;
    const path = location.pathname + location.search;
    return () => request === requestEpoch && navigation === navigationEpoch
      && location.pathname + location.search === path && !D.$(viewId).classList.contains("hidden");
  }

  function clockFromMs(ms) {
    const total = Math.max(0, Math.round(Number(ms) / 1000));
    if (!Number.isFinite(total)) return "";
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
  }

  function jobTitle(job) {
    return job.display_title || job.kind_label || job.kind;
  }

  function progressLabel(job) {
    if (job.status === "needs_selection") return "等待你";
    return `${Math.round((job.progress || 0) * 100)}%`;
  }

  function completionLine(job) {
    const warnings = Array.isArray(job.result?.warnings) ? job.result.warnings.filter(Boolean) : [];
    if (job.status === "completed_with_warnings" && !warnings.length && (job.child_job_ids || []).length) {
      return "有提示在子任务里，打开后可以查看。";
    }
    if (job.status === "completed_with_warnings" && warnings.length) {
      const first = String(warnings[0]);
      return warnings.length > 1 ? `${first}（另有 ${warnings.length - 1} 条提示）` : first;
    }
    const selection = job.result?.selection;
    if (selection && typeof selection === "object" && Number.isFinite(Number(selection.total))) {
      return `已入库 ${Number(selection.imported || 0)} / 共 ${selection.total} 个作品`;
    }
    const summary = job.result?.summary;
    if (summary && typeof summary === "object" && Number.isFinite(Number(summary.discovered))) {
      return `发现 ${summary.discovered} · 已入库 ${summary.imported || 0} · 失败 ${summary.failed || 0}`;
    }
    if (typeof summary === "string" && summary.trim()) return summary.trim();
    return "";
  }

  function listMessage(job) {
    if (job.status === "completed" || job.status === "completed_with_warnings") {
      const line = completionLine(job);
      if (line) return line;
    }
    return job.message_for_user || "";
  }

  function attentionRank(job) {
    if (!job.requires_user_action) return 3;
    if (job.status === "needs_selection") return 0;
    if (job.status === "failed") return 2;
    return 1;
  }

  function attentionGroup(job) {
    if (job.requires_user_action && job.status === "needs_selection") return "select";
    if (job.requires_user_action && job.status === "failed") return "failed";
    if (job.requires_user_action) return "attention";
    return "rest";
  }

  function actionButton(action) {
    if (!action) return null;
    const button = D.node("button", action.label, action.code === "retry" ? "secondary-button" : "primary-button");
    button.type = "button";
    button.dataset.action = action.code || "";
    button.addEventListener("click", async () => {
      try {
        if (action.endpoint) {
          await D.api(action.endpoint, {method: "POST", body: JSON.stringify({})});
          D.toast("已提交");
          loadFromPath(routePath(location.pathname));
        } else if (action.href) {
          D.navigate(action.href);
        }
      } catch (error) {
        D.toast(error.message);
      }
    });
    return button;
  }

  function followLink(href, label, className = "secondary-button") {
    const link = D.node("a", label, className);
    link.href = href;
    link.addEventListener("click", (event) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      event.stopPropagation();
      D.navigate(href);
    });
    return link;
  }

  function quietAction(job) {
    const action = job.next_action;
    if (!action?.href || job.requires_user_action) return null;
    if (!["open_entry", "view_children", "view_shared_capture"].includes(action.code)) return null;
    return followLink(action.href, action.label);
  }

  function creatorRepaste(job) {
    if (job.kind !== "creator_import" || job.status !== "failed") return null;
    return followLink("/imports/creators", "去导入页重贴");
  }

  function dismissButton(job, {reloadDetail = false} = {}) {
    if (job.status !== "failed" || job.dismissed || !job.requires_user_action) return null;
    const button = D.node("button", "不再提醒", "secondary-button");
    button.type = "button";
    button.addEventListener("click", async () => {
      try {
        await D.api(`/api/jobs/${encodeURIComponent(job.id)}/dismiss`, {method: "POST", body: JSON.stringify({})});
        D.toast("已不再提醒");
        if (reloadDetail) await loadDetail(job.id);
        else await loadFromPath(routePath(location.pathname));
      } catch (error) {
        D.toast(error.message);
      }
    });
    return button;
  }

  function cancelButton(job, {reloadDetail = false} = {}) {
    if (!job.can_cancel) return null;
    const button = D.node("button", "取消", "secondary-button");
    button.type = "button";
    button.addEventListener("click", async () => {
      const paused = job.status === "queued" || job.requires_user_action
        || job.status === "awaiting_agent_analysis" || job.status === "monitoring";
      const confirmed = await D.confirmDanger({
        title: "取消这个任务？",
        description: paused
          ? "取消后不再继续处理；若仍有正在执行的步骤，将在安全检查点停止。"
          : "运行中的任务会在下一个安全检查点停止，不会在下载中途打断。",
        confirmLabel: "取消任务",
      });
      if (!confirmed) return;
      try {
        const result = await D.api(`/api/jobs/${encodeURIComponent(job.id)}/cancel`, {method: "POST", body: "{}"});
        D.toast(result.status === "cancelled" ? "已取消" : "已请求停止");
        if (reloadDetail) await loadDetail(job.id);
        else await loadFromPath(routePath(location.pathname));
      } catch (error) {
        D.toast(error.message);
      }
    });
    return button;
  }

  function deleteJobButton(job) {
    if (job.status !== "failed" && job.status !== "cancelled") return null;
    const button = D.node("button", "删除", "danger-text-button");
    button.type = "button";
    button.addEventListener("click", async () => {
      const confirmed = await D.confirmDanger({
        title: "删除这个任务？",
        description: "只删除任务记录，已经入库的资料会保留。",
        confirmLabel: "删除任务",
      });
      if (!confirmed) return;
      try {
        await D.api(`/api/jobs/${encodeURIComponent(job.id)}`, {method: "DELETE"});
        D.toast("已删除任务");
        if (location.pathname === `/jobs/${job.id}`) D.navigate("/jobs");
        else await loadFromPath(routePath(location.pathname));
      } catch (error) {
        D.toast(error.message);
      }
    });
    return button;
  }

  function selfProgressLink(action) {
    if (!action || action.endpoint || action.code !== "wait_worker") return false;
    return action.label === "查看任务进度";
  }

  function statusCluster(job) {
    const cluster = D.node("div", null, "job-status-cluster");
    const status = D.node("span", job.state_label, "status-pill");
    status.dataset.state = job.status;
    cluster.append(status);
    if (job.dismissed) cluster.append(D.node("span", "已忽略", "job-dismissed-mark"));
    return cluster;
  }

  function renderRow(job) {
    const row = D.node("article", null, "job-row");
    row.tabIndex = 0;
    row.dataset.jobId = job.id;
    row.setAttribute("role", "link");
    if (job.requires_user_action) row.dataset.attention = attentionGroup(job);
    const title = D.node("h2", jobTitle(job));
    const message = D.node("p", listMessage(job));
    if (job.status === "completed" || job.status === "completed_with_warnings") message.classList.add("job-oneline");
    const updated = String(job.updated_display || "").replace(/（北京时间）/g, "").trim();
    const copy = D.node("div", null, "job-row-copy");
    const progressing = ["resolving", "inventorying", "dispatching", "monitoring", "downloading", "extracting", "transcribing", "analyzing"].includes(job.status) && job.progress > 0;
    if (progressing) message.textContent += ` · ${job.stage_label} · ${progressLabel(job)}`;
    copy.append(title, message);
    row.append(statusCluster(job), copy);
    const actions = D.node("div", null, "job-row-actions");
    if (job.requires_user_action && job.next_action) {
      const action = actionButton(job.next_action);
      if (action) actions.append(action);
    }
    const quiet = quietAction(job);
    if (quiet) actions.append(quiet);
    const repaste = creatorRepaste(job);
    if (repaste) actions.append(repaste);
    const dismiss = dismissButton(job);
    if (dismiss) actions.append(dismiss);
    const cancel = cancelButton(job);
    if (cancel) actions.append(cancel);
    const remove = deleteJobButton(job);
    if (remove) actions.append(remove);
    actions.querySelectorAll("button,a").forEach((node) => node.classList.add("is-sm"));
    const right = D.node("div", null, "job-row-right");
    right.append(actions, D.node("time", updated)); row.append(right);
    if (progressing) {
      const track = D.node("div", null, "job-row-progress");
      const fill = D.node("span"); fill.style.width = `${Math.min(100, Math.max(0, job.progress * 100))}%`; track.append(fill); row.append(track);
    }
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, a")) return;
      D.navigate(`/jobs/${job.id}`);
    });
    row.addEventListener("keydown", (event) => {
      if (event.target.closest("button,a")) return;
      if (["j", "k", "ArrowDown", "ArrowUp"].includes(event.key)) {
        event.preventDefault();
        const rows = [...D.$("jobs-list").querySelectorAll(".job-row")];
        const delta = ["j", "ArrowDown"].includes(event.key) ? 1 : -1;
        rows[Math.max(0, Math.min(rows.length - 1, rows.indexOf(row) + delta))]?.focus();
      } else if (event.key === "r") { event.preventDefault(); row.querySelector("[data-action=retry]")?.click(); }
      else if (event.key === "Enter") { event.preventDefault(); D.navigate(`/jobs/${job.id}`); }
    });
    return row;
  }

  function renderList(data) {
    const root = D.$("jobs-list");
    const focused = document.activeElement.closest(".job-row")?.dataset.jobId;
    if (!data.items.length) {
      root.replaceChildren(D.emptyState("没有符合条件的任务", "提交导入后，任务会出现在这里。", null, null, "list-checks"));
      return;
    }
    const items = [...data.items].sort((a, b) => attentionRank(a) - attentionRank(b));
    const groups = [];
    for (const job of items) {
      const key = attentionGroup(job);
      const current = groups[groups.length - 1];
      if (!current || current.key !== key) groups.push({key, items: [job]});
      else current.items.push(job);
    }
    const titles = {
      select: "待选择作品",
      attention: "需要本人操作",
      failed: "失败可重试",
      rest: "其他任务",
    };
    const showHeads = groups.some((group) => group.key !== "rest");
    const nodes = [];
    for (const group of groups) {
      if (showHeads && titles[group.key]) {
        const heading = D.node("p", titles[group.key], "job-group-title");
        heading.setAttribute("role", "heading");
        heading.setAttribute("aria-level", "2");
        nodes.push(heading);
      }
      nodes.push(...group.items.map(renderRow));
    }
    root.replaceChildren(...nodes);
    const rows = [...root.querySelectorAll(".job-row")];
    const active = rows.find((row) => row.dataset.jobId === focused);
    if (active) active.focus({preventScroll: true});
  }

  async function loadList() {
    const isCurrent = requestScope("jobs-view");
    const params = new URLSearchParams(location.search);
    const query = new URLSearchParams();
    if (params.get("kind")) query.set("kind", params.get("kind"));
    if (params.get("status")) query.set("status", params.get("status"));
    if (params.get("requires_user_action") === "1") query.set("requires_user_action", "true");
    let data;
    try {
      data = await D.api(`/api/jobs?${query}`);
    } catch (error) {
      if (isCurrent()) throw error;
      return false;
    }
    if (!isCurrent()) return false;
    renderList(data);
    D.$("jobs-count").textContent = String(data.total);
    const kind = D.$("jobs-filter-kind");
    const action = D.$("jobs-filter-action");
    if (kind) kind.value = params.get("kind") || "";
    if (action) {
      if (params.get("requires_user_action") === "1") action.value = "waiting";
      else {
        const status = params.get("status") || "all";
        action.value = status === "completed_with_warnings" ? "completed"
          : status === "进行中" ? "in_progress"
          : status;
      }
    }
    return true;
  }

  async function loadDetail(jobId) {
    const match = location.pathname.match(/^\/jobs\/([^/]+)$/);
    if (!match || decodeURIComponent(match[1]) !== jobId || D.$("job-detail-view").classList.contains("hidden")) return false;
    const isCurrent = requestScope("job-detail-view");
    const root = D.$("job-detail-content");
    let job;
    try {
      job = await D.api(`/api/jobs/${encodeURIComponent(jobId)}`);
    } catch (error) {
      if (isCurrent()) throw error;
      return false;
    }
    if (!isCurrent()) return false;
    document.title = `${jobTitle(job)} · 抖库`;
    const header = D.node("header", null, "job-detail-summary");
    D.$("job-detail-title").textContent = jobTitle(job);
    const titleBox = document.createElement("div");
    titleBox.append(D.node("h2", jobTitle(job)), D.node("p", job.message_for_user));
    header.append(titleBox, statusCluster(job));
    const progress = D.node(
      "p",
      job.status === "cancelled" ? job.kind === "favorites_import"
        ? "此批次已取消，已读清单和已入库资料仍保留。" : "此任务已取消。"
        : job.kind === "favorites_import" && ["queued", "inventorying"].includes(job.status)
          ? "正在读取收藏清单 · 已读 " + (job.batch?.summary?.discovered || 0) + " 件"
        : job.status === "needs_selection"
        ? `阶段 ${job.stage_label} · 等待你`
        : `阶段 ${job.stage_label} · 进度 ${Math.round((job.progress || 0) * 100)}%`
    );
    progress.className = "job-progress-copy";
    const track = D.node("div", null, "progress-track");
    track.hidden = job.status === "cancelled";
    track.classList.toggle("is-indeterminate", job.kind === "favorites_import" && ["queued", "inventorying"].includes(job.status));
    const fill = D.node("span"); fill.style.width = `${Math.min(100, Math.max(0, job.progress * 100))}%`; track.append(fill);
    const modelProgress = D.node("p", "", "hint-copy");
    if (job.analysis_progress && job.status === "analyzing") {
      const state = job.analysis_progress;
      const phases = {correction: "字幕校正", analysis: "分段分析", merge: "汇总", evidence: "证据校验"};
      const count = state.phase === "merge" ? ` · 已完成 ${state.completed_chunks} 次合并`
        : state.phase === "evidence" ? "" : ` · ${state.completed_chunks}/${state.total_chunks} 批`;
      const responseAt = state.last_response_at ? new Date(state.last_response_at).toLocaleString("zh-CN") : "等待首个响应";
      const usage = state.usage?.retry_count ? ` · 重试 ${state.usage.retry_count} 次` : "";
      const waitingMinutes = state.last_response_at ? Math.floor((Date.now() - Date.parse(state.last_response_at)) / 60000) : 0;
      const waiting = job.status === "analyzing" && waitingMinutes >= 5 ? ` · 当前批次已等待 ${waitingMinutes} 分钟` : "";
      const totalCalls = job.llm_stats?.successful_calls ? ` · 累计成功调用 ${job.llm_stats.successful_calls} 次` : "";
      const phaseLabel = state.waiting_for_resource ? "等待模型校正资源" : (phases[state.phase] || state.phase);
      modelProgress.textContent = `${phaseLabel}${count} · 最近模型响应 ${responseAt}${usage}${totalCalls}${waiting}`;
    }
    const actions = D.node("div", null, "operation-actions");
    if (job.kind === "favorites_import") {
      actions.append(followLink("/imports/favorites?job=" + encodeURIComponent(job.id), "查看收藏清单"));
      if (job.imported_entry_ids?.length) actions.append(followLink("/?favorites_job=" + encodeURIComponent(job.id), "在资料库查看本批"));
    }
    const viewingChildren = job.next_action?.code === "view_children";
    const action = viewingChildren || selfProgressLink(job.next_action) ? null : actionButton(job.next_action);
    if (action) actions.append(action);
    const importedEntries = (job.imported_entries?.length ? job.imported_entries : (job.imported_entry_ids || []).map((entryId) => ({entry_id: entryId, title: ""})))
      .filter((entry) => !(job.next_action?.code === "open_entry" && job.entry_id === entry.entry_id))
      .slice(0, 5);
    importedEntries.forEach((entry, index) => {
      const title = String(entry.title || "").trim();
      const label = title ? `打开《${title}》` : (importedEntries.length > 1 ? `打开已入库资料 ${index + 1}` : "打开已入库资料");
      actions.append(followLink(`/articles/${encodeURIComponent(entry.entry_id)}`, label));
    });
    const repaste = creatorRepaste(job);
    if (repaste) actions.append(repaste);
    const dismiss = dismissButton(job, {reloadDetail: true});
    if (dismiss) actions.append(dismiss);
    const cancel = cancelButton(job, {reloadDetail: true});
    if (cancel) actions.append(cancel);
    const remove = deleteJobButton(job);
    if (remove) actions.append(remove);
    const openEntryViaAction = job.next_action?.code === "open_entry";
    if (job.entry_id && !openEntryViaAction) {
      const link = D.node("a", "打开知识资料", "secondary-button");
      link.href = `/articles/${encodeURIComponent(job.entry_id)}`;
      actions.append(link);
    }
    const analysis = D.node("p", terminal.has(job.status) ? "" : "分析方式：" + (job.analysis_mode === "gateway" ? "外部 Agent" : job.analysis_mode_label || "当前方式"), "hint-copy");
    const media = D.node("section", null, "review-panel");
    const providers = job.media_provenance || {};
    if (providers.asr || providers.ocr) {
      media.append(D.node("h2", "媒体识别模型"));
      for (const [label, info] of [["语音识别", providers.asr], ["画面文字", providers.ocr]]) {
        if (!info?.provider && !info?.confidence_note) continue;
        media.append(D.node("p", `${label}：${info.provider || "未记录"}${info.model ? ` · ${info.model}` : ""}${info.fallback_reason ? ` · 回退原因：${info.fallback_reason}` : ""}`));
        if (info.confidence_note) media.append(D.node("p", info.confidence_note, "hint-copy"));
      }
    }
    const modelHealth = D.node("p", "", "hint-copy");
    const timeline = D.node("ol", null, "job-timeline");
    for (const event of job.timeline || []) {
      const item = D.node("li", `${event.state_label} · ${event.created_display}`);
      timeline.append(item);
    }
    const children = D.node("section", null, "job-children");
    children.id = "job-children";
    const childList = job.children || [];
    const stats = job.child_stats || {};
    const hasChildWork = childList.length > 0 || Number(stats.total || 0) > 0
      || Number(stats.completed || 0) > 0 || Number(stats.running || 0) > 0
      || Number(stats.waiting_user || 0) > 0 || Number(stats.failed || 0) > 0;
    if (hasChildWork) {
      children.tabIndex = -1;
      const childHeader = D.node("header", null, "job-children-header");
      const childHeading = D.node("div");
      childHeading.append(D.node("h2", "子任务"));
      if (job.child_stats) {
        childHeading.append(D.node("p", `成功 ${stats.completed || 0} · 处理中 ${stats.running || 0} · 等待本人 ${stats.waiting_user || 0} · 失败 ${stats.failed || 0}`));
      }
      childHeader.append(childHeading);
      const state = childPages.get(job.id) || {page: 1, filter: "all"};
      childPages.set(job.id, state);
      const toolbar = D.node("div", null, "fav-toolbar"), filter = D.node("select");
      filter.setAttribute("aria-label", "筛选子任务");
      for (const [value, text] of [["all", "全部子任务"], ["unfinished", "未完成子任务"], ["failed", "失败子任务"]]) {
        const option = D.node("option", text); option.value = value; filter.append(option);
      }
      filter.value = state.filter; toolbar.append(filter); childHeader.append(toolbar);
      const rows = D.node("div", null, "job-child-list"), pager = D.node("nav", null, "fav-toolbar fav-pager");
      const previous = D.node("button", "上一页子任务", "secondary-button"), next = D.node("button", "下一页子任务", "secondary-button"), label = D.node("span");
      previous.type = next.type = "button"; pager.append(previous, label, next);
      const childWarnings = (child) => (Array.isArray(child.result?.warnings) ? child.result.warnings : []).map((item) => String(item).trim()).filter(Boolean);
      const renderChildren = () => {
        const filtered = childList.filter((child) => state.filter === "all" ||
          (state.filter === "failed" ? child.status === "failed" : !terminal.has(child.status)));
        filtered.sort((left, right) => Number(childWarnings(right).length > 0) - Number(childWarnings(left).length > 0));
        const pages = Math.max(1, Math.ceil(filtered.length / 50));
        state.page = Math.min(pages, Math.max(1, state.page)); rows.replaceChildren();
        for (const child of filtered.slice((state.page - 1) * 50, state.page * 50)) {
          const warnings = childWarnings(child);
          const row = D.node("button", null, "job-child");
          row.classList.toggle("has-warning", warnings.length > 0);
          const hint = warnings[0] || child.message_for_user;
          const childTitle = D.node("strong", jobTitle(child), "job-child-title");
          const childMessage = D.node("span", hint, "job-child-message");
          childTitle.title = jobTitle(child); childMessage.title = hint || "";
          row.append(statusCluster(child), childTitle, childMessage);
          row.type = "button"; row.addEventListener("click", () => D.navigate("/jobs/" + encodeURIComponent(child.id))); rows.append(row);
        }
        label.textContent = state.page + " / " + pages + " 页 · " + filtered.length + " 个任务";
        previous.disabled = state.page <= 1; next.disabled = state.page >= pages;
      };
      filter.addEventListener("change", () => { state.filter = filter.value; state.page = 1; renderChildren(); });
      previous.addEventListener("click", () => { state.page--; renderChildren(); });
      next.addEventListener("click", () => { state.page++; renderChildren(); });
      renderChildren(); children.append(childHeader, rows, pager);
    }
    if (viewingChildren && hasChildWork) {
      const jump = D.node("button", "子任务在下方", "text-button");
      jump.type = "button";
      jump.addEventListener("click", () => {
        children.scrollIntoView({behavior: "smooth", block: "start"});
        children.focus({preventScroll: true});
      });
      actions.append(jump);
    }
    const warningsPanel = D.node("section", null, "review-panel job-warnings");
    const warnings = Array.isArray(job.result?.warnings) ? job.result.warnings.filter(Boolean) : [];
    if (warnings.length) {
      warningsPanel.append(D.node("h2", "提示"));
      warningsPanel.append(D.node(
        "p",
        job.result?.warnings_from_children
          ? "父任务本身没有提示，下面来自子任务："
          : "任务已结束，但仍有需要留意的提示：",
        "hint-copy",
      ));
      const list = D.node("ul", null, "job-warning-list");
      for (const warning of warnings) {
        list.append(D.node("li", String(warning)));
      }
      warningsPanel.append(list);
    }
    const review = D.node("section", null, "review-panel");
    if (job.review_issues?.length) {
      review.append(D.node("h2", "历史校对疑点"));
      review.append(D.node("p", "此任务按旧策略暂停。点击“按新模型校对策略重试”会重新校正并继续分析。", "hint-copy"));
      job.review_issues.forEach((issue) => {
        const span = Number.isFinite(Number(issue.start_ms))
          ? `${clockFromMs(issue.start_ms)}–${clockFromMs(issue.end_ms)}`
          : "";
        review.append(D.node("p", `疑点 ${issue.id}${span ? `（${span}）` : ""}${issue.image_index ? ` · 图 ${issue.image_index}` : ""}：${issue.raw_text}；${issue.reason || ""}`));
      });
    }
    const evidenceAudit = D.node("section", null, "review-panel");
    if (job.analysis_evidence_audit?.length) {
      evidenceAudit.append(D.node("h2", `证据核验记录（${job.analysis_evidence_audit.length}）`));
      for (const item of job.analysis_evidence_audit) {
        const stamp = item.timestamp_ms == null ? "" : clockFromMs(item.timestamp_ms);
        evidenceAudit.append(D.node("p", `${item.kind}${item.id ? ` ${item.id}` : ""}${stamp ? ` · ${stamp}` : ""}：${item.reason}`));
      }
    }
    const selectionNote = D.node("p", "", "hint-copy");
    if (job.status === "needs_selection") {
      selectionNote.textContent = job.kind === "creator_import"
        ? "清点已经完成。点下面的按钮进入选片：勾选作品并确认后才会下载。这一页不重复清单，避免两处状态不一致。"
        : "清点已经完成。点下面的按钮去勾选作品，确认后才会导入。";
    }
    const gatewayNote = D.node("p", "", "hint-copy");
    if (job.status === "awaiting_agent_analysis" || (job.analysis_mode === "gateway" && job.status === "analyzing")) {
      gatewayNote.textContent = "网页不会自动分析。这一步要等外部 Agent 接续，停在「待 AI 处理」不代表已经整理完成。";
    }
    const parts = [header, progress, track];
    if (job.kind === "favorites_import" && job.batch?.summary) {
      const summary = job.batch.summary;
      const batch = D.node("section", null, "operation-section");
      batch.append(D.node("h2", "收藏批次"), D.node("p", "共 " + (summary.discovered || 0) +
        " 件 · 已选 " + (summary.selected || 0) + " · 已在资料库 " + (summary.in_library ?? summary.imported ?? 0) +
        " · 已完成 " + (summary.completed || 0) + " · 失败 " + (summary.failed || 0)));
      parts.push(batch);
    }
    if (selectionNote.textContent) parts.push(selectionNote);
    if (gatewayNote.textContent) parts.push(gatewayNote);
    if (modelProgress.textContent) parts.push(modelProgress);
    if (job.analysis_mode === "provider" && !terminal.has(job.status)) {
      parts.push(modelHealth);
      try {
        const health = await D.api("/api/system/model-health");
        modelHealth.textContent = `模型服务：${health.message}`;
      } catch (error) {
        modelHealth.textContent = `模型服务状态暂不可查：${error.message}`;
      }
    }
    if (actions.childNodes.length) parts.push(actions);
    if ((analysis.textContent || "").trim()) parts.push(analysis);
    if (media.childNodes.length) parts.push(media);
    if (timeline.childNodes.length) parts.push(timeline);
    if (children.childNodes.length) parts.push(children);
    if (warningsPanel.childNodes.length) parts.push(warningsPanel);
    if (review.childNodes.length) parts.push(review);
    if (evidenceAudit.childNodes.length) parts.push(evidenceAudit);
    if (!isCurrent()) return false;
    root.replaceChildren(...parts);
    return true;
  }

  async function loadFromPath(path) {
    if (location.pathname !== path || (path !== "/jobs" && !path.startsWith("/jobs/"))) return false;
    const match = path.match(/^\/jobs\/([^/]+)$/);
    D.hideAllViews();
    D.setNav("jobs-nav");
    if (match) {
      D.setPage("job-detail");
      D.$("job-detail-view").classList.remove("hidden");
      try {
        if (!await loadDetail(decodeURIComponent(match[1]))) return false;
      } catch (error) {
        if (error.status === 404) {
          D.showMissing();
          return false;
        }
        throw error;
      }
      D.warnIfWorkerDown("job-detail-view");
    } else {
      D.setPage("jobs");
      D.$("jobs-view").classList.remove("hidden");
      document.title = "任务中心 · 抖库";
      if (!await loadList()) return false;
      D.warnIfWorkerDown("jobs-view");
    }
    return true;
  }

  window.addEventListener("douku:route", async (event) => {
    const navigation = ++navigationEpoch;
    ++requestEpoch;
    stop();
    const path = routePath(event.detail.path);
    if (path !== "/jobs" && !path.startsWith("/jobs/")) {
      stop();
      return;
    }
    try {
      if (await loadFromPath(path) && navigation === navigationEpoch) schedule(path);
    } catch (error) {
      if (navigation === navigationEpoch) D.toast(error.message);
    }
  });

  D.$("jobs-filter-action")?.addEventListener("change", () => {
    const value = D.$("jobs-filter-action").value;
    const params = new URLSearchParams(location.search);
    if (value === "waiting") params.set("requires_user_action", "1");
    else params.delete("requires_user_action");
    if (value && value !== "waiting" && value !== "all") params.set("status", value);
    else params.delete("status");
    D.navigate(`/jobs?${params}`);
  });
  D.$("jobs-filter-kind")?.addEventListener("change", () => {
    const params = new URLSearchParams(location.search);
    const value = D.$("jobs-filter-kind").value;
    if (value) params.set("kind", value);
    else params.delete("kind");
    D.navigate(`/jobs?${params}`);
  });
})();
