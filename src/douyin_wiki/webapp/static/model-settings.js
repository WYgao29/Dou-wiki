(() => {
  "use strict";
  const D = window.Douku;
  if (!D) return;
  window.addEventListener("douku:route", (event) => {
    if (event.detail.path !== "/settings/model") return;
    D.showMissing();
  });
})();
