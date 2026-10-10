(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  window.addEventListener("douku:route", (event) => {
    if (event.detail.path !== "/imports/single") return;
    D.hideAllViews(); D.setPage("imports-single"); D.setNav("imports-nav");
    D.$("imports-single-view").classList.remove("hidden");
    document.title = "导入单条 · 抖库";
  });
})();
