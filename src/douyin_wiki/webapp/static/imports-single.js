(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;

  async function loadHints() {
    try {
      const analysis = await D.api("/api/system/analysis");
      D.$("single-analysis-copy").textContent = `${analysis.web_copy}。${analysis.token_hint}。`;
    } catch (_error) {
      D.$("single-analysis-copy").textContent = "提交后由后台处理。";
    }
  }

  function showInline(message) {
    const error = D.$("single-error");
    error.classList.remove("hidden");
    error.replaceChildren(document.createTextNode(message));
  }

  function showDuplicate(job) {
    const error = D.$("single-error");
    error.classList.remove("hidden");
    error.replaceChildren();
    const inTrash = Boolean(job.in_trash);
    error.append(document.createTextNode(job.active ? `${job.message} · ` : inTrash ? "已在废纸篓 · " : "已在资料库 · "));
    const open = D.node("a", job.active ? "查看任务" : "打开");
    open.href = job.href || (job.entry_id ? `/articles/${encodeURIComponent(job.entry_id)}` : "/");
    open.addEventListener("click", (event) => {
      event.preventDefault();
      D.navigate(open.getAttribute("href"));
    });
    error.append(open);
    if (!inTrash && job.entry_id) {
      error.append(document.createTextNode(" / "));
      const again = D.node("button", "重新分析", "text-button");
      again.type = "button";
      again.addEventListener("click", async () => {
        const confirmed = await D.confirmDanger({
          title: "重新分析这篇文章？",
          description: "重新分析会调用模型并消耗 Token。确认后任务进入队列。",
          confirmLabel: "开始重新分析",
        });
        if (!confirmed) return;
        try {

          const created = await D.api(`/api/articles/${encodeURIComponent(job.entry_id)}/reanalyze`, {
            method: "POST",
            body: JSON.stringify({force: false}),
          });
          D.toastAction("已排队重新分析", "查看任务", () => D.navigate(`/jobs/${created.id || created.job_id}`));
        } catch (err) {
          D.toast(err.message);
        }
      });
      error.append(again);
    }
  }

  async function submit(event) {
    event.preventDefault();
    const error = D.$("single-error");
    error.classList.add("hidden");
    error.replaceChildren();
    const text = D.$("single-inspiration").value.trim();
    const quote = D.$("single-quote").value.trim();
    let start = null;
    let end = null;
    try {
      start = D.parseClockToMs(D.$("single-start").value);
      end = D.parseClockToMs(D.$("single-end").value);
      if (end != null && start == null) throw new Error("填写结束时间时，也要填写开始时间");
      if (end != null && end < start) throw new Error("结束时间不能早于开始时间");
    } catch (err) {
      showInline(err.message);
      return;
    }
    const inspirations = [];
    if (text || quote || start != null || end != null) {
      inspirations.push({text, quote: quote || null, start_ms: start, end_ms: end});
    }
    try {

      const job = await D.api("/api/captures", {
        method: "POST",
        body: JSON.stringify({
          share_text: D.$("single-share").value,
          inspirations,
          retention: D.$("single-retention").value,
        }),
      });
      if (job.duplicate) {
        showDuplicate(job);
        return;
      }
      D.toast("已加入任务队列");
      D.navigate(`/jobs/${job.id || job.job_id}`);
    } catch (err) {
      showInline(err.message);
    }
  }

  window.addEventListener("douku:route", async (event) => {
    if (event.detail.path !== "/imports/single") return;
    D.hideAllViews();
    D.setPage("imports-single");
    D.setNav("imports-nav");
    D.$("imports-single-view").classList.remove("hidden");
    document.title = "单条导入 · 抖库";
    D.warnIfWorkerDown("imports-single-view");
    await loadHints();
  });

  D.$("single-form")?.addEventListener("submit", submit);
})();
