(() => {
  "use strict";
  const VIEW_IDS = [
    "library-view", "article-view", "topics-view", "topic-view", "trash-view", "tags-view",
    "imports-view", "imports-single-view", "imports-creators-view", "imports-favorites-view",
    "jobs-view", "job-detail-view", "auth-view", "system-view",
    "analysis-view", "model-view", "settings-view", "not-found-view",
  ];
  const Douku = window.Douku || {};
  Douku.$ = (id) => document.getElementById(id);
  Douku.qs = (selector, root = document) => root.querySelector(selector);
  Douku.qsa = (selector, root = document) => [...root.querySelectorAll(selector)];
  Douku.api = async (url, options = {}) => {
    const response = await fetch(url, {
      headers: {"Content-Type": "application/json", ...(options.headers || {})},
      ...options,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = Array.isArray(body.detail)
        ? body.detail.map((item) => item.msg || item.detail || "输入内容无效").join("；")
        : body.detail;
      const error = new Error(detail || "请求失败");
      error.status = response.status;
      throw error;
    }
    return body;
  };
  const TOAST_MS = 2600;
  const toastState = {timer: 0, remaining: 0, started: 0, sticky: false};
  const toastNode = () => Douku.$("toast");
  const hideToast = () => toastNode()?.classList.remove("show");
  const scheduleToastHide = (ms) => {
    window.clearTimeout(toastState.timer);
    toastState.started = Date.now();
    toastState.remaining = ms;
    toastState.timer = window.setTimeout(() => {
      if (document.hidden) return;
      hideToast();
      toastState.remaining = 0;
    }, ms);
  };
  Douku.revealToast = (node, {sticky = false, duration = TOAST_MS} = {}) => {
    if (!node) return;
    node.classList.add("show");
    window.clearTimeout(toastState.timer);
    toastState.sticky = sticky;
    if (sticky) {
      toastState.remaining = 0;
      return;
    }
    scheduleToastHide(duration);
  };
  Douku.hideToast = () => {
    window.clearTimeout(toastState.timer);
    toastState.remaining = 0;
    hideToast();
  };
  Douku.toastAction = (message, label, onClick, duration = 6000) => {
    const node = toastNode();
    if (!node) return;
    const copy = document.createElement("span");
    copy.textContent = message;
    const action = document.createElement("button");
    action.type = "button";
    action.className = "toast-action";
    action.textContent = label;
    action.addEventListener("click", () => {
      Douku.hideToast();
      Promise.resolve(onClick()).catch((error) => Douku.toast(error.message || "操作失败"));
    });
    node.replaceChildren(copy, action);
    Douku.revealToast(node, {duration});
  };
  Douku.toast = (message, options) => {
    const node = toastNode();
    if (!node) return;
    node.textContent = message;
    Douku.revealToast(node, options);
  };
  document.addEventListener("visibilitychange", () => {
    const node = toastNode();
    if (!node?.classList.contains("show")) return;
    if (document.hidden) {
      window.clearTimeout(toastState.timer);
      if (toastState.remaining) {
        toastState.remaining = Math.max(0, toastState.remaining - (Date.now() - toastState.started));
      }
      return;
    }
    if (toastState.sticky) return;
    if (toastState.remaining > 0) scheduleToastHide(toastState.remaining);
    else hideToast();
  });
  Douku.node = (tag, text, className) => {
    const value = document.createElement(tag);
    if (text != null) value.textContent = text;
    if (className) value.className = className;
    return value;
  };
  Douku.hideAllViews = () => {
    VIEW_IDS.forEach((id) => Douku.$(id)?.classList.add("hidden"));
    Douku.qsa(".nav-item, .settings-nav-link").forEach((node) => node.classList.remove("active"));
  };
  Douku.setPage = (name) => {
    Douku.$("app-shell")?.setAttribute("data-page", name);
    window.dispatchEvent(new CustomEvent("douku:page", {detail: {page: name}}));
  };
  Douku.setNav = (id) => {
    Douku.qsa(".nav-item, .settings-nav-link").forEach((node) => node.classList.remove("active"));
    Douku.$(id)?.classList.add("active");
  };
  Douku.isOperationPath = (path) => (
    path === "/imports" || path.startsWith("/imports/")
    || path === "/jobs" || path.startsWith("/jobs/")
    || path === "/settings" || path === "/auth"
    || path === "/settings/auth" || path === "/settings/system"
    || path === "/settings/analysis" || path === "/settings/model"
  );
  Douku.parseClockToMs = (value) => {
    const text = String(value ?? "").trim();
    if (!text) return null;
    if (/^\d+$/.test(text)) return Number(text);
    const parts = text.split(":");
    if (parts.length < 2 || parts.length > 3 || parts.some((part) => !/^\d+$/.test(part))) {
      throw new Error("时间请用分:秒，例如 1:05；只填整数则按毫秒");
    }
    const nums = parts.map((part) => Number(part));
    let seconds = 0;
    if (nums.length === 2) {
      const [minutes, secs] = nums;
      if (secs >= 60) throw new Error("秒数要小于 60，例如 1:05");
      seconds = minutes * 60 + secs;
    } else {
      const [hours, minutes, secs] = nums;
      if (minutes >= 60 || secs >= 60) throw new Error("分和秒都要小于 60，例如 1:02:03");
      seconds = hours * 3600 + minutes * 60 + secs;
    }
    return seconds * 1000;
  };
  Douku.showMissing = () => {
    Douku.hideAllViews();
    Douku.setPage("not-found");
    Douku.$("not-found-view")?.classList.remove("hidden");
    document.title = "页面不存在 · 抖库";
  };
  Douku.tagPath = (name) => {
    const params = new URLSearchParams();
    params.set("tag", name);
    let view = "";
    try { view = localStorage.getItem("douyin-wiki.library-view") || ""; } catch (_error) { view = ""; }
    if (view && view !== "list") params.set("view", view);
    return `/?${params}`;
  };
  Douku.warnIfWorkerDown = async (viewId) => {
    const view = Douku.$(viewId);
    if (!view) return;
    let health;
    try { health = await Douku.api("/api/system/health"); }
    catch (_error) { return; }
    const existing = view.querySelector("[data-worker-banner]");
    if (health.worker?.running) {
      existing?.remove();
      return;
    }
    if (existing) return;
    const banner = Douku.node("div", null, "alert-banner");
    banner.dataset.workerBanner = "1";
    banner.setAttribute("role", "status");
    const link = Douku.node("a", "打开系统设置");
    link.href = "/settings/system";
    link.addEventListener("click", (event) => {
      event.preventDefault();
      Douku.navigate("/settings/system");
    });
    banner.append(Douku.node("p", "后台 Worker 没在运行，新任务会一直排队。"), link);
    const header = view.querySelector(".view-header");
    if (header?.nextSibling) view.insertBefore(banner, header.nextSibling);
    else view.prepend(banner);
  };
  Douku.isKnownAppPath = (path) => (
    path === "/" || path === "/topics" || path === "/trash" || path === "/tags"
    || path.startsWith("/articles/") || path.startsWith("/topics/")
    || Douku.isOperationPath(path)
  );
  Douku.navigate = (path, push = true) => {
    if (push) history.pushState({}, "", path);
    const routePath = String(path).split("?")[0];
    window.dispatchEvent(new CustomEvent("douku:route", {detail: {path: routePath}}));
  };
  Douku.emptyState = (title, copy, actionLabel, action, icon) => {
    const section = Douku.node("section", null, "empty-state");
    const heading = Douku.node("h2", title);
    const text = Douku.node("p", copy);
    if (icon) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("aria-hidden", "true");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", `/static/icons.svg#${icon}`);
      svg.append(use);
      section.append(svg);
    }
    section.append(heading, text);
    if (actionLabel && action) {
      const button = Douku.node("button", actionLabel, "secondary-button");
      button.type = "button";
      button.addEventListener("click", action);
      section.append(button);
    }
    return section;
  };
  Douku.confirmDanger = async ({title, description, confirmLabel}) => new Promise((resolve) => {
    const dialog = Douku.$("destructive-dialog");
    if (!dialog) {
      resolve(window.confirm(description));
      return;
    }
    Douku.$("destructive-title").textContent = title;
    Douku.$("destructive-description").textContent = description;
    Douku.$("confirm-destructive").textContent = confirmLabel || "确认";
    Douku.$("destructive-error").classList.add("hidden");
    const finish = (ok) => {
      dialog.close();
      resolve(ok);
    };
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      if (dialog.returnValue === "cancel") resolve(false);
    };
    dialog.addEventListener("close", onClose, {once: true});
    Douku.$("confirm-destructive").onclick = (event) => {
      event.preventDefault();
      finish(true);
    };
    dialog.showModal();
  });
  Douku.icon = (name) => {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `/static/icons.svg#${name}`); svg.append(use);
    return svg;
  };
  Douku.bindCheckList = (root) => {
    if (!root || root.dataset.checkListBound) return;
    root.dataset.checkListBound = "true";
    root.addEventListener("click", (event) => {
      const input = event.target.closest("input[data-work]");
      if (!input || input.disabled) return;
      const inputs = [...root.querySelectorAll("input[data-work]")];
      const at = inputs.indexOf(input);
      const anchor = inputs.findIndex((node) => node.dataset.work === root.dataset.selectionAnchor);
      input.selectionWorkIds = (event.shiftKey && anchor >= 0
        ? inputs.slice(Math.min(at, anchor), Math.max(at, anchor) + 1) : [input])
        .filter((node) => !node.disabled).map((node) => node.dataset.work);
      if (!event.shiftKey || anchor < 0) root.dataset.selectionAnchor = input.dataset.work;
      root.dataset.focusWork = input.dataset.work;
    });
    root.addEventListener("keydown", (event) => {
      if (event.target.closest("a,button,textarea") || !["j", "k", "x"].includes(event.key.toLowerCase())) return;
      const inputs = [...root.querySelectorAll("input[data-work]:not(:disabled)")];
      const at = inputs.indexOf(document.activeElement);
      if (!inputs.length) return;
      event.preventDefault();
      if (event.key.toLowerCase() === "x") inputs[Math.max(at, 0)].click();
      else inputs[Math.max(0, Math.min(inputs.length - 1, at + (event.key.toLowerCase() === "j" ? 1 : -1)))].focus();
    });
  };
  window.Douku = Douku;
})();
