(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  window.addEventListener("douku:route", (event) => {
    if (event.detail.path !== "/settings/model") return;
    D.hideAllViews(); D.setPage("model"); D.setNav("settings-nav");
    D.$("model-view").classList.remove("hidden");
    document.title = "共用模型 · 抖库";
  });
})();
