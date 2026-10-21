(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  let generation = 0;
  window.addEventListener("douku:route", async (event) => {
    const token = ++generation;
    if (event.detail.path !== "/settings/analysis") return;
    D.hideAllViews(); D.setPage("analysis"); D.setNav("settings-nav");
    D.$("analysis-view").classList.remove("hidden");
    document.title = "导入分析 · Dou-wiki";
    try {
      const status = await D.api("/api/system/analysis");
      if (token !== generation) return;
      D.$("analysis-status-title").textContent = status.label || status.mode_label || "外部 Agent";
      D.$("analysis-status-copy").textContent = status.web_copy || "校正和分析由外部 Agent 完成；等待外部 Agent 的任务需要由外部 Agent 接续。";
    } catch (error) {
      if (token === generation) D.$("analysis-status-copy").textContent = error.message;
    }
  });
})();
