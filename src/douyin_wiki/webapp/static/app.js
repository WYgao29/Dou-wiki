"use strict";

const STORAGE = {
  view: "douyin-wiki.library-view",
  uiVersion: "douyin-wiki.ui-version",
  sidebar: "douyin-wiki.sidebar-collapsed",
  chat: "douyin-wiki.chat-collapsed",
};

const UI_PREFERENCE_VERSION = "3";
const VALID_VIEWS = new Set(["list", "gallery"]);
const VALID_SORTS = new Set([
  "relevance", "captured_desc", "published_desc", "published_asc", "title_asc", "author_asc",
]);
const VALID_SECTIONS = new Set(["all", "recent", "favorite", "inspiration"]);

const state = {
  items: [],
  allItems: [],
  allItemsLoaded: false,
  facets: {},
  focusedEntryId: "",
  lastOpenedEntryId: "",
  libraryScrollTop: 0,
  readerRequestId: 0,
  navigationRequestId: 0,
  libraryNavigationId: 0,
  libraryRequestId: 0,
  libraryLoadedQuery: null,
  pendingArticleRequest: 0,
  readerObserver: null,
  readingBlocks: [],
  readingIndex: -1,
  readingProgressFrame: null,
  readingProgressBound: false,
  entryMenuTrigger: null,
  section: "all",
  libraryView: "list",
  sort: "captured_desc",
  authors: [],
  types: [],
  tags: [],
  sources: [],
  inspirationOnly: false,
  favoritesJob: "",
  query: "",
  searchMode: "keyword",
  libraryLoadedScope: null,
  pendingLibraryScope: null,
  commandArticles: [],
  commandSearch: 0,
  commandTimer: 0,
  currentEntry: document.body.dataset.entryId || "",
  currentArticleItem: null,
  currentTopic: document.body.dataset.topicId || "",
  topics: [],
  topicPrompts: new Map(),
  trashItems: [],
  selectedTrashIds: new Set(),
  topicSelectionMode: false,
  selectedEntryIds: new Set(),
  sidebarCollapsed: false,
  chatCollapsed: false,
  panelPage: "",
  panelOverride: null,
  panelTab: "info",
  breakpoint: "",
  navigationPrefixAt: 0,
  navigationPrefixTimer: 0,
  commandIndex: 0,
  trashFocusId: "",
  trashSelectionAnchor: "",
  trashRangeSelect: true,
  lastDialogTrigger: null,
  libraryLoaded: false,
  catalogHasItems: false,
  animatedEntryIds: new Set(),
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function svgIcon(name, className = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  if (className) svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `/static/icons.svg#${name}`);
  svg.append(use);
  return svg;
}

function readStored(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch (_error) {
    return fallback;
  }
}

function writePreference(key, value) {
  try {
    window.localStorage.setItem(key, String(value));
  } catch (_error) {
    // The application remains usable when local storage is unavailable.
  }
}

function readPreferences() {
  const needsListMigration = readStored(STORAGE.uiVersion, "") !== UI_PREFERENCE_VERSION;
  if (needsListMigration) {
    state.libraryView = "list";
    writePreference(STORAGE.view, state.libraryView);
    writePreference(STORAGE.uiVersion, UI_PREFERENCE_VERSION);
  } else {
    const storedView = readStored(STORAGE.view, "list");
    state.libraryView = VALID_VIEWS.has(storedView) ? storedView : "list";
  }
  state.sidebarCollapsed = readStored(STORAGE.sidebar, "false") === "true";
  state.chatCollapsed = readStored(STORAGE.chat, "false") === "true";
}

function readStateFromURL() {
  const params = new URLSearchParams(window.location.search);
  state.query = params.get("q") || "";
  state.searchMode = params.get("search_mode") === "semantic" ? "semantic" : "keyword";
  const view = params.get("view");
  const preferredView = readStored(STORAGE.view, "list");
  state.libraryView = VALID_VIEWS.has(view)
    ? view
    : VALID_VIEWS.has(preferredView) ? preferredView : "list";
  if (VALID_VIEWS.has(view)) writePreference(STORAGE.view, view);
  const sort = params.get("sort");
  state.sort = VALID_SORTS.has(sort) && !(sort === "relevance" && state.searchMode !== "semantic")
    ? sort : state.searchMode === "semantic" ? "relevance" : "captured_desc";
  const section = params.get("section");
  state.section = VALID_SECTIONS.has(section) ? section : "all";
  state.authors = params.getAll("author").filter(Boolean);
  state.types = params.getAll("type").filter(Boolean);
  state.tags = params.getAll("tag").filter(Boolean);
  state.sources = params.getAll("source").filter((value) => ["video", "image_note"].includes(value));
  state.inspirationOnly = params.get("has_inspiration") === "1";
  state.favoritesJob = params.get("favorites_job") || "";
}

function currentLibraryURL() {
  const params = new URLSearchParams();
  if (state.query) params.set("q", state.query);
  if (state.searchMode === "semantic") params.set("search_mode", "semantic");
  if (state.libraryView !== "list") params.set("view", state.libraryView);
  if (state.sort !== "captured_desc") params.set("sort", state.sort);
  if (state.section !== "all") params.set("section", state.section);
  state.authors.forEach((value) => params.append("author", value));
  state.types.forEach((value) => params.append("type", value));
  state.tags.forEach((value) => params.append("tag", value));
  state.sources.forEach((value) => params.append("source", value));
  if (state.inspirationOnly) params.set("has_inspiration", "1");
  if (state.favoritesJob) params.set("favorites_job", state.favoritesJob);
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

function writeStateToURL(mode = "replace") {
  if (state.currentEntry) return;
  const method = mode === "push" ? "pushState" : "replaceState";
  history[method]({}, "", currentLibraryURL());
}

function toast(message) {
  window.Douku.toast(message);
}

function favoriteFailure(kind, error) {
  const detail = String(error?.message || "").trim();
  const generic = !detail || detail === "请求失败" || detail === "操作失败" || detail === "收藏操作失败";
  return generic ? `${kind}操作失败` : `${kind}操作失败：${detail}`;
}

function showRestoreRetry(jobId, message) {
  const node = $("#toast");
  const copy = document.createElement("span");
  copy.textContent = message;
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "toast-action";
  retry.textContent = "重试下载";
  retry.addEventListener("click", async () => {
    retry.disabled = true;
    try {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {method: "POST"});
      toast("已重新加入视频恢复队列");
      await pollRestoreJob(jobId);
    } catch (error) {
      showRestoreRetry(jobId, error.message || "重试失败");
    }
  });
  node.replaceChildren(copy, retry);
  window.Douku.revealToast(node, {sticky: true});
}

const api = (...args) => window.Douku.api(...args);

function mediaSentence(item) {
  const retention = item.retention;
  const status = item.media_status;
  if (retention === "temporary" && status === "present") return "媒体暂时留在本机，之后可以清理。";
  if (retention === "temporary" && status === "removed") return "临时媒体已经清理，只保留文字资料。";
  if (retention === "keep" && status === "present") return "媒体会一直保留在本机。";
  if (retention === "keep" && status === "removed") return "已设为一直保留，但媒体文件目前不在本机。";
  if (retention === "discard" && status === "removed") return "处理完成后已清理媒体，只保留文字资料。";
  if (retention === "discard" && status === "present") return "处理完成后会清理媒体，文件目前还在本机。";
  return "媒体状态还没有记录。";
}

async function openDestructiveDialog(options) {
  const result = await window.Douku.confirmDanger(options);
  if (result === false) return;
  const warning = result?.warnings?.[0];
  if (options.successMessage !== false) {
    const message = options.successMessage || "操作已完成";
    toast(warning ? `${message}；${warning}` : message);
  }
  try { await options.onSuccess?.(result); }
  catch (error) { toast(`操作已完成，但页面刷新失败：${error.message}`); }
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function toggleListValue(key, value) {
  const values = state[key];
  state[key] = values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value];
}

function formatDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).format(date);
}

function compareDate(a, b, field, direction) {
  const left = Date.parse(a[field] || "") || 0;
  const right = Date.parse(b[field] || "") || 0;
  return direction === "asc" ? left - right : right - left;
}

function isRecentItem(item) {
  return (Date.parse(item.captured_at || "") || 0) >= Date.now() - 7 * 24 * 60 * 60 * 1000;
}

function filteredItems() {
  let items = state.items.filter((item) => {
    const hasInspiration = Boolean(item.inspirations?.length);
    return (!state.authors.length || state.authors.includes(item.author))
      && (!state.types.length || state.types.includes(item.content_type))
      && (!state.tags.length || state.tags.some((tag) => (item.tags || []).includes(tag)))
      && (!state.sources.length || state.sources.includes(item.source_kind))
      && (!state.inspirationOnly || hasInspiration)
      && (state.section !== "favorite" || item.favorite)
      && (state.section !== "inspiration" || hasInspiration);
  });
  if (!(state.searchMode === "semantic" && state.query && state.sort === "relevance")) items.sort((a, b) => {
    if (state.sort === "published_desc") return compareDate(a, b, "published_at", "desc");
    if (state.sort === "published_asc") return compareDate(a, b, "published_at", "asc");
    if (state.sort === "title_asc") return a.title.localeCompare(b.title, "zh-CN");
    if (state.sort === "author_asc") return a.author.localeCompare(b.author, "zh-CN") || a.title.localeCompare(b.title, "zh-CN");
    return compareDate(a, b, "captured_at", "desc");
  });
  if (state.section === "recent") items = items.filter(isRecentItem);
  return items;
}

function setLibraryLoading() {
  $("#library-status").classList.remove("hidden");
  $("#library-results").classList.add("hidden");
  $("#empty-library").classList.add("hidden");
  $("#empty-results").classList.add("hidden");
  $("#library-error").classList.add("hidden");
}

function setLibraryError(error) {
  $("#library-status").classList.add("hidden");
  $("#library-results").classList.add("hidden");
  $("#empty-library").classList.add("hidden");
  $("#empty-results").classList.add("hidden");
  $("#library-error-copy").textContent = error.message || "请稍后重试。";
  $("#library-error").classList.remove("hidden");
  $("#result-count").textContent = "载入失败";
}

function renderTodoBanners(count) {
  const total = Number(count) || 0;
  let badge = $("#jobs-nav .nav-count");
  if (!badge) { badge = window.Douku.node("span", null, "nav-count"); $("#jobs-nav").append(badge); }
  badge.textContent = String(total); badge.classList.toggle("hidden", total === 0);
  badge.setAttribute("aria-label", `${total} 个任务需要处理`);
  for (const id of ["library-todo", "imports-todo"]) {
    const node = document.getElementById(id);
    if (!node) continue;
    if (total > 0) {
      node.classList.remove("hidden");
      const count = window.Douku.node("span", `待办 ${total}`);
      const action = window.Douku.node("span", "去任务中心", "todo-banner-action");
      action.append(svgIcon("chevron-right"));
      node.replaceChildren(count, action);
    } else {
      node.classList.add("hidden");
      node.textContent = "";
    }
  }
}

async function refreshAuthChannelBanners() {
  // 资料库首页不展示授权横幅；仅导入枢纽提醒。待办条在资料库和导入枢纽都显示。
  const targets = ["imports-auth-banner"]
    .map((id) => document.getElementById(id))
    .filter(Boolean);
  if (!targets.length) return;
  try {
    const data = await window.Douku.api("/api/overview");
    renderTodoBanners(data.jobs?.requires_user_action);
    const video = data.auth?.channels?.video;
    const alert = (data.alerts || []).find((item) =>
      item.code === "video_auth_unverified" || item.code === "video_auth"
    );
    const show = Boolean(
      alert || (video && !data.auth?.pending && !["authorized", "authorizing"].includes(video.user_state))
    );
    for (const node of targets) {
      if (!show) {
        node.classList.add("hidden");
        node.replaceChildren();
        continue;
      }
      node.classList.remove("hidden");
      const copy = window.Douku.node(
        "div",
        null,
      );
      const title = window.Douku.node(
        "strong",
        alert?.message || (video?.user_state === "unconfirmed"
          ? "视频下载暂时无法确认。"
          : "视频下载授权需要处理。"),
      );
      const detail = window.Douku.node(
        "p",
        video?.user_state === "needs_login"
          ? "请到「设置 › 授权状态」重新授权。"
          : "请到「设置 › 授权状态」查看原因和下一步。",
      );
      copy.append(title, detail);
      const actions = window.Douku.node("div", null, "operation-actions");
      const go = window.Douku.node("button", "打开授权状态", "secondary-button");
      go.type = "button";
      go.addEventListener("click", () => window.Douku.navigate("/settings/auth"));
      actions.append(go);
      node.replaceChildren(copy, actions);
    }
  } catch (_) {
    renderTodoBanners(0);
    for (const node of targets) {
      node.classList.add("hidden");
      node.replaceChildren();
    }
  }
}

function beginNavigation({preserveLibraryRequests = false} = {}) {
  resetNavigationPrefix();
  if (!preserveLibraryRequests) state.libraryNavigationId += 1;
  return ++state.navigationRequestId;
}

function semanticScopeKey() {
  return JSON.stringify([state.query, state.searchMode, state.authors, state.types, state.tags,
    state.sources, state.inspirationOnly, state.section, state.favoritesJob]);
}

async function loadLibrary({showLoading = !state.libraryLoaded} = {}) {
  const requestId = ++state.libraryRequestId;
  const navigationId = state.libraryNavigationId;
  const query = state.query;
  const mode = state.searchMode;
  const semantic = mode === "semantic" && Boolean(query);
  const scopeKey = semanticScopeKey();
  state.pendingLibraryScope = scopeKey;
  const favoritesJob = state.favoritesJob;
  const isCurrent = () => requestId === state.libraryRequestId
    && navigationId === state.libraryNavigationId && query === state.query && favoritesJob === state.favoritesJob
    && mode === state.searchMode && (!semantic || scopeKey === semanticScopeKey());
  if (showLoading) setLibraryLoading();
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (favoritesJob) params.set("favorites_job", favoritesJob);
  if (semantic) {
    state.authors.forEach(value => params.append("author", value));
    state.types.forEach(value => params.append("type", value));
    state.tags.forEach(value => params.append("tag", value));
    state.sources.forEach(value => params.append("source", value));
    if (state.inspirationOnly) params.set("inspiration_only", "true");
    params.set("section", state.section);
  }
  try {
    const needsCatalog = Boolean(favoritesJob) || (Boolean(query) && !state.allItemsLoaded);
    const [data, catalogData] = await Promise.all([
      api(`/api/library${semantic ? "/search" : ""}${params.size ? `?${params}` : ""}`),
      needsCatalog ? api("/api/library") : null,
    ]);
    if (!isCurrent()) return;
    state.items = data.items;
    state.facets = data.facets;
    state.tags = [...new Set(state.tags.map(tag => state.facets.tag_aliases?.[tag] || tag))];
    if (catalogData || (!query && !favoritesJob)) {
      state.allItems = (catalogData || data).items;
      state.allItemsLoaded = true;
    }
    state.catalogHasItems = Boolean(
      (!query && data.items.length)
      || data.facets.authors?.length
      || data.facets.content_types?.length
      || data.facets.tags?.length,
    );
    state.libraryLoaded = true;
    state.libraryLoadedQuery = query;
    state.libraryLoadedScope = semanticScopeKey();
    state.pendingLibraryScope = null;
    const capability = $("#search-capability");
    capability.textContent = semantic ? data.notice || "" : "";
    capability.classList.toggle("hidden", !capability.textContent);
    refreshAuthChannelBanners();
    renderFilters();
    renderLibrary();
    fillInspirationTargets();
  } catch (error) {
    if (isCurrent()) setLibraryError(error);
  } finally {
    if (requestId === state.libraryRequestId) state.pendingLibraryScope = null;
  }
}

async function ensureCommandItems() {
  if (state.allItemsLoaded) return;
  try {
    const data = await api("/api/library");
    state.allItems = data.items;
    state.allItemsLoaded = true;
  } catch (_error) {
    // Keep the catalog empty. Search hits must not become the source-tab counts.
  }
}

function makeChoice(label, value, key) {
  const node = window.Douku.node("label", null, "choice-button");
  const check = document.createElement("input");
  check.type = "checkbox";
  check.checked = state[key].includes(value);
  check.addEventListener("change", () => {
    toggleListValue(key, value);
    renderFilters(); renderLibrary(); writeStateToURL();
    // The checked row is rebuilt along with the other facets.
    const root = $(`#${key === "authors" ? "author" : "type"}-filters`);
    $$("label", root).find((row) => row.textContent === label)?.querySelector("input").focus();
  });
  node.append(check, window.Douku.node("span", label));
  return node;
}

function renderFilters() {
  const authorQuery = $("#author-filter-search").value.trim().toLocaleLowerCase("zh-CN");
  $("#author-filter-search").classList.toggle("hidden", (state.facets.authors || []).length <= 8);
  $("#author-filters").replaceChildren(...(state.facets.authors || [])
    .filter((value) => value.toLocaleLowerCase("zh-CN").includes(authorQuery))
    .map((value) => makeChoice(value, value, "authors")));
  $("#type-filters").replaceChildren(...(state.facets.content_types || []).map((value) => makeChoice(value.label, value.value, "types")));
  $("#inspiration-only").checked = state.inspirationOnly;
  if (state.panelPage === "library") {
    $$(".nav-item[data-section]").forEach((node) => node.classList.toggle("active", !state.tags.length && node.dataset.section === state.section));
    $("#topics-nav").classList.remove("active"); $("#trash-nav").classList.remove("active");
  }
  renderAppliedFilters();
  renderToolbarState();
}

function activeFilterCount() {
  return state.authors.length + state.types.length + state.tags.length + state.sources.length
    + Number(state.inspirationOnly) + Number(Boolean(state.favoritesJob));
}

function makeFilterChip(label, onRemove) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "filter-chip";
  chip.append(document.createTextNode(label), svgIcon("x"));
  chip.setAttribute("aria-label", `移除筛选：${label}`);
  chip.addEventListener("click", () => {
    onRemove();
    renderFilters();
    renderLibrary();
    writeStateToURL();
  });
  return chip;
}

function renderAppliedFilters() {
  const root = $("#applied-filters");
  const chips = [];
  state.authors.forEach((value) => chips.push(makeFilterChip(`博主：${value}`, () => toggleListValue("authors", value))));
  state.types.forEach((value) => {
    const label = state.facets.content_types?.find((item) => item.value === value)?.label || value;
    chips.push(makeFilterChip(`类型：${label}`, () => toggleListValue("types", value)));
  });
  state.tags.forEach((value) => chips.push(makeFilterChip(`标签：${value}`, () => toggleListValue("tags", value))));
  state.sources.forEach((value) => chips.push(makeFilterChip(value === "image_note" ? "图文" : "视频", () => toggleListValue("sources", value))));
  if (state.inspirationOnly) chips.push(makeFilterChip("有灵感", () => { state.inspirationOnly = false; }));
  if (state.favoritesJob) chips.push(makeFilterChip("本批收藏", () => { state.favoritesJob = ""; loadLibrary(); }));
  if (state.query) chips.push(makeFilterChip(`搜索：${state.query}`, () => {
    state.query = "";
    $("#search-input").value = "";
    loadLibrary();
  }));
  root.replaceChildren(...chips);
  root.classList.toggle("hidden", chips.length === 0);
}

function renderToolbarState() {
  $("#search-mode").value = state.searchMode;
  $("#sort-select option[value='relevance']").hidden = state.searchMode !== "semantic";
  const count = activeFilterCount();
  $("#filter-count").textContent = String(count);
  $("#filter-count").classList.toggle("hidden", count === 0);
  $("#sort-select").value = state.sort;
  $$("[data-source]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.source ? state.sources.length === 1 && state.sources[0] === button.dataset.source : state.sources.length !== 1)));
  $$('[data-library-view]').forEach((node) => node.setAttribute("aria-pressed", String(node.dataset.libraryView === state.libraryView)));
}

function makeCover(item, className) {
  const cover = document.createElement("div");
  cover.className = className;
  if (["gallery-cover", "article-cover", "item-thumbnail", "info-cover"].includes(className)) {
    cover.classList.add(item.source_kind === "image_note" ? "image-note-cover" : "video-cover");
  }
  if (item.cover_url) {
    const image = document.createElement("img");
    image.src = item.cover_url;
    image.alt = "";
    image.loading = "lazy";
    image.width = className === "item-thumbnail" ? 48 : className === "info-cover" ? 120 : 480;
    image.height = className === "item-thumbnail" ? 64 : className === "info-cover" ? 160 : 640;
    image.addEventListener("error", () => cover.replaceChildren(makePlaceholder(item)));
    cover.append(image);
  } else {
    cover.append(makePlaceholder(item));
  }
  return cover;
}

function makePlaceholder(item) {
  const node = document.createElement("div");
  node.className = "cover-placeholder";
  node.append(svgIcon(item.source_kind === "image_note" ? "image" : "video"));
  return node;
}

function sourceLabel(item) {
  return item.source_kind === "image_note" ? "图文" : "视频";
}

function syncFavoriteItem(updated) {
  const update = (item) => item.entry_id === updated.entry_id ? {...item, ...updated} : item;
  state.items = state.items.map(update);
  state.allItems = state.allItems.map(update);
  if (state.currentArticleItem?.entry_id === updated.entry_id) {
    state.currentArticleItem = {...state.currentArticleItem, ...updated};
  }
}

function findLibraryItem(entryId) {
  return state.items.find((item) => item.entry_id === entryId)
    || state.allItems.find((item) => item.entry_id === entryId)
    || (state.currentArticleItem?.entry_id === entryId ? state.currentArticleItem : null)
    || null;
}

function isDatabaseManaged(item) {
  return Boolean(item?.database_managed);
}

function currentEntryItem() {
  return state.currentEntry ? findLibraryItem(state.currentEntry) : null;
}

function updateFavoriteButton(button, favorite) {
  button.classList.toggle("active", favorite);
  button.setAttribute("aria-pressed", String(favorite));
  button.setAttribute("aria-label", favorite ? "取消库内收藏" : "标为库内收藏");
  button.title = favorite ? "取消库内收藏" : "标为库内收藏";
}

async function pollRestoreJob(jobId) {
  const terminal = new Set(["completed", "completed_with_warnings", "failed", "needs_auth"]);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    if (!terminal.has(job.status)) continue;
    if (job.status === "completed" || job.status === "completed_with_warnings") {
      toast(job.result?.skipped ? "已取消视频恢复" : "库内收藏的视频已重新下载到本地");
      await loadLibrary({showLoading: false});
    } else if (job.status === "needs_auth") {
      showRestoreRetry(jobId, "已标为库内收藏；更新抖音登录后可重试下载");
    } else {
      showRestoreRetry(jobId, "已标为库内收藏；视频恢复失败");
    }
    return;
  }
  toast("已标为库内收藏；视频仍在后台恢复");
}

function makeFavoriteButton(item, className = "") {
  if (!isDatabaseManaged(item)) return null;
  const button = document.createElement("button");
  button.type = "button";
  button.className = `favorite-button ${className}`.trim();
  button.append(svgIcon("bookmark"));
  updateFavoriteButton(button, Boolean(item.favorite));
  button.addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    const favorite = !button.classList.contains("active");
    const restoreInfoFocus = document.activeElement === button && Boolean(button.closest("#info-pane"));
    const restoreReaderFocus = document.activeElement === button && Boolean(button.closest("#reader-actions"));
    button.disabled = true;
    try {
      const response = await api(`/api/articles/${encodeURIComponent(item.entry_id)}/favorite`, {
        method: "PUT",
        body: JSON.stringify({favorite}),
      });
      Object.assign(item, response.item);
      syncFavoriteItem(response.item);
      updateFavoriteButton(button, response.item.favorite);
      toast(response.item.favorite ? "已标为库内收藏，视频会永久保留在本地" : "已取消库内收藏");
      if (!$("#library-view").classList.contains("hidden")) renderLibrary();
      else renderPanelSummary();
      if (state.panelPage === "article") {
        renderArticleToolbar(state.currentArticleItem);
        if (restoreReaderFocus) $("#reader-actions .article-favorite-button")?.focus({preventScroll: true});
      }
      if (restoreInfoFocus) $("#info-pane .info-favorite")?.focus({preventScroll: true});
      if (response.restore_job) pollRestoreJob(response.restore_job.id).catch(() => {
        toast("已标为库内收藏；暂时无法读取视频恢复进度");
      });
    } catch (error) {
      toast(favoriteFailure("库内收藏", error));
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function galleryAccessibleName(item) {
  return [
    item.title || "未命名文章",
    item.author || "未知作者",
    item.content_type_label || "其他",
    sourceLabel(item),
    formatDate(item.published_at),
    ...(state.section === "inspiration" ? (item.inspirations || []).map((value) => `灵感：${value.text}`) : []),
  ].join("，");
}

function shortDate(value) {
  if (!value) return "—";
  const formatted = formatDate(value);
  return formatted.startsWith(String(new Date().getFullYear())) ? formatted.slice(5) : formatted;
}

function makeMetadata(item, {tags = false} = {}) {
  const metadata = window.Douku.node("div", null, "item-metadata");
  const author = window.Douku.node("span", item.author || "未知作者");
  author.prepend(svgIcon(item.source_kind === "image_note" ? "image" : "video"));
  metadata.append(author, window.Douku.node("span", item.content_type_label || "其他"), window.Douku.node("span", shortDate(item.published_at)));
  if (item.inspirations?.length) {
    const count = window.Douku.node("span", String(item.inspirations.length)); count.prepend(svgIcon("quote")); metadata.append(count);
  }
  if (item.favorite) { const mark = window.Douku.node("span", null, "metadata-favorite"); mark.append(svgIcon("bookmark")); mark.setAttribute("aria-label", "库内收藏"); metadata.append(mark); }
  if (!isDatabaseManaged(item)) metadata.append(window.Douku.node("span", "只读"));
  if (tags) (item.tags || []).slice(0, 2).forEach((tag) => metadata.append(window.Douku.node("span", tag, "item-tag")));
  return metadata;
}

function bindEntryLink(link, item, cover = null) {
  link.href = `/articles/${encodeURIComponent(item.entry_id)}`;
  link.dataset.entryAction = "open";
  link.tabIndex = state.focusedEntryId === item.entry_id ? 0 : -1;
  link.addEventListener("focus", () => focusEntry(item.entry_id, {focus: false, scroll: false}));
  link.addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    focusEntry(item.entry_id, {focus: false, scroll: false});
    if (state.topicSelectionMode && isDatabaseManaged(item)) { toggleTopicEntry(item.entry_id); return; }
    if (state.topicSelectionMode) { toast("只读 Markdown 文章不能加入专题"); return; }
    state.lastOpenedEntryId = item.entry_id;
    openArticle(item.entry_id, true, cover);
  });
}

function searchWindowLocation(evidence) {
  const start = evidence.timestamp_start_ms;
  const end = evidence.timestamp_end_ms;
  const valid = typeof start === "number" && typeof end === "number"
    && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end >= start;
  if (!valid) return "粗略时间范围";
  const formattedStart = formatLocation({timestamp_ms: start});
  const formattedEnd = formatLocation({timestamp_ms: end});
  return `约 ${formattedStart}–${formattedEnd}（时间未经精确对齐）`;
}

function makeSearchEvidence(item) {
  const evidence = item.search_evidence?.find((match) => match && (
    match.timestamp_ms != null || match.image_index != null || match.timestamp_precision === "window"
  )) || item.search_evidence?.[0];
  if (!evidence) return null;
  const location = evidence.timestamp_precision === "window" ? searchWindowLocation(evidence)
    : evidence.image_index != null ? `第 ${evidence.image_index} 张图片`
    : evidence.timestamp_ms != null ? formatLocation(evidence) : "相关证据";
  return window.Douku.node("p", `${location} · ${evidence.snippet}`, "search-evidence");
}

function makeInspirationPreview(item) {
  if (state.section !== "inspiration" || !item.inspirations?.length) return null;
  const preview = window.Douku.node("div", null, "item-inspirations");
  item.inspirations.forEach((value) => {
    const row = window.Douku.node("p", null, "item-inspiration");
    const text = window.Douku.node("span", value.text);
    text.title = value.text;
    row.append(svgIcon("quote"), text);
    preview.append(row);
  });
  return preview;
}

function makeListItem(item) {
  const article = window.Douku.node("article", null, "library-item row--entry");
  article.dataset.entryId = item.entry_id;
  article.classList.toggle("is-focused", state.focusedEntryId === item.entry_id);
  article.classList.toggle("active", state.lastOpenedEntryId === item.entry_id);
  article.classList.toggle("topic-selected", state.selectedEntryIds.has(item.entry_id));
  const link = window.Douku.node("a", null, "library-item-link");
  bindEntryLink(link, item);
  const copy = window.Douku.node("div", null, "item-copy");
  copy.append(window.Douku.node("h2", item.title),
    makeInspirationPreview(item) || window.Douku.node("p", item.summary || "暂无一句话摘要", `item-summary${item.summary ? "" : " is-empty"}`),
    makeMetadata(item, {tags: true}));
  const evidence = makeSearchEvidence(item); if (evidence) copy.append(evidence);
  link.append(makeCover(item, "item-thumbnail"), copy);
  const side = window.Douku.node("div", null, "item-side");
  side.append(window.Douku.node("time", shortDate(state.sort === "captured_desc" ? item.captured_at : item.published_at), "item-date"));
  const actions = window.Douku.node("div", null, "entry-actions");
  if (isDatabaseManaged(item)) {
    const favorite = makeFavoriteButton(item, "is-sm"); favorite.dataset.entryAction = "favorite"; actions.append(favorite);
  }
  const more = window.Douku.node("button", null, "icon-button is-sm entry-more");
  more.type = "button"; more.append(svgIcon("more-horizontal")); more.dataset.entryAction = "more";
  more.setAttribute("aria-label", `更多操作：${item.title}`); more.setAttribute("aria-haspopup", "menu"); more.setAttribute("aria-expanded", "false");
  more.addEventListener("click", () => openEntryMenu(item, more)); actions.append(more);
  actions.querySelectorAll("button").forEach((button) => { button.tabIndex = state.focusedEntryId === item.entry_id ? 0 : -1; });
  article.addEventListener("focusin", () => focusEntry(item.entry_id, {focus: false, scroll: false}));
  side.append(actions); article.append(link, side);
  if (state.topicSelectionMode) article.append(makeSelectionIndicator(item.entry_id));
  return article;
}

function makeSelectionIndicator(entryId) {
  const indicator = document.createElement("input");
  indicator.type = "checkbox"; indicator.className = "topic-selection-indicator"; indicator.dataset.entryAction = "select";
  const item = findLibraryItem(entryId);
  indicator.checked = state.selectedEntryIds.has(entryId); indicator.disabled = !isDatabaseManaged(item);
  indicator.setAttribute("aria-label", `选择专题来源：${item?.title || entryId}`);
  indicator.tabIndex = state.focusedEntryId === entryId ? 0 : -1;
  indicator.addEventListener("change", () => { focusEntry(entryId, {focus: false, scroll: false}); toggleTopicEntry(entryId); });
  return indicator;
}

function toggleTopicEntry(entryId) {
  if (!isDatabaseManaged(findLibraryItem(entryId))) return;
  if (state.selectedEntryIds.has(entryId)) state.selectedEntryIds.delete(entryId);
  else state.selectedEntryIds.add(entryId);
  renderLibrary();
  updateTopicSelectionButton();
}

function makeGalleryCard(item) {
  const article = window.Douku.node("article", null, "gallery-card");
  article.dataset.entryId = item.entry_id;
  article.classList.toggle("is-focused", state.focusedEntryId === item.entry_id);
  article.classList.toggle("topic-selected", state.selectedEntryIds.has(item.entry_id));
  const link = window.Douku.node("a", null, "gallery-card-link");
  const accessibleName = galleryAccessibleName(item); link.setAttribute("aria-label", `打开文章：${accessibleName}`); link.title = accessibleName;
  const cover = makeCover(item, "gallery-cover"); bindEntryLink(link, item, cover);
  const copy = window.Douku.node("div", null, "gallery-copy");
  copy.append(window.Douku.node("h2", item.title || "未命名文章"));
  const inspirations = makeInspirationPreview(item); if (inspirations) copy.append(inspirations);
  copy.append(makeMetadata(item));
  const evidence = makeSearchEvidence(item); if (evidence) copy.append(evidence);
  link.append(cover, copy); article.append(link);
  if (item.favorite) { const badge = window.Douku.node("span", null, "gallery-favorite"); badge.append(svgIcon("bookmark")); badge.setAttribute("aria-label", "库内收藏"); cover.append(badge); }
  if (state.topicSelectionMode) article.append(makeSelectionIndicator(item.entry_id));
  return article;
}

function renderLibraryList(items) { return items.map(makeListItem); }
function renderLibraryGallery(items) { return items.map(makeGalleryCard); }

function focusEntry(entryId, {focus = true, scroll = true} = {}) {
  if (!filteredItems().some((item) => item.entry_id === entryId)) return;
  const changed = state.focusedEntryId !== entryId;
  state.focusedEntryId = entryId;
  let activeRow;
  $$("#library-results [data-entry-id]").forEach((row) => {
    const active = row.dataset.entryId === entryId;
    row.classList.toggle("is-focused", active);
    $$("[data-entry-action]", row).forEach((control) => { control.tabIndex = active ? 0 : -1; });
    if (active) activeRow = row;
  });
  if (changed && state.panelPage === "library") renderPanelSummary();
  if (focus) $("[data-entry-action='open']", activeRow)?.focus({preventScroll: true});
  if (scroll) activeRow?.scrollIntoView({block: "nearest", behavior: "instant"});
}

function moveFocus(delta) {
  const items = filteredItems(); if (!items.length) return;
  const index = items.findIndex((item) => item.entry_id === state.focusedEntryId);
  focusEntry(items[Math.max(0, Math.min(items.length - 1, (index < 0 ? 0 : index) + delta))].entry_id);
}

function openFocused() {
  const item = findLibraryItem(state.focusedEntryId); if (!item) return;
  if (state.topicSelectionMode) { toggleTopicEntry(item.entry_id); return; }
  state.lastOpenedEntryId = item.entry_id;
  const row = $$("#library-results [data-entry-id]").find((value) => value.dataset.entryId === item.entry_id);
  openArticle(item.entry_id, true, row?.querySelector(".gallery-cover"));
}

async function reanalyzeItem(item, button) {
  const confirmed = await window.Douku.confirmDanger({
    title: "重新交给 Agent 分析这篇？",
    description: "确认后任务进入队列，你仍留在这篇文章。",
    confirmLabel: "重新交给 Agent 分析这篇",
    tone: "normal",
  });
  if (!confirmed) return;
  button.disabled = true;

  try {
    const job = await api(`/api/articles/${encodeURIComponent(item.entry_id)}/reanalyze`, {method: "POST", body: JSON.stringify({force: false})});
    const jobId = job.id || job.job_id;
    window.Douku.toastAction("已排队重新分析", "查看任务", () => window.Douku.navigate(`/jobs/${jobId}`), 6000);
  } catch (error) { toast(error.message || "重新分析失败"); }
  button.disabled = false;
}

function deleteLibraryItem(item) {
  openDestructiveDialog({title: "删除整条资料？", description: `《${item.title}》的文章、原始记录、机器数据、视频、图片和封面将移入 Dou-wiki 废纸篓，并从检索与专题中移除。`, confirmLabel: "移到废纸篓",
    action: () => {
      return api(`/api/articles/${encodeURIComponent(item.entry_id)}`, {method: "DELETE", body: JSON.stringify({confirmed: true})});
    },
    successMessage: false,
    onSuccess: async (result) => {
      state.currentEntry = "";
      state.allItems = state.allItems.filter((value) => value.entry_id !== item.entry_id);
      state.selectedEntryIds.delete(item.entry_id);
      await Promise.all([loadLibrary({showLoading: false}), loadTopics(), loadTrash()]);
      showLibrary();
      const trashId = result?.trash_id;
      if (!trashId) {
        toast("已移到废纸篓");
        return;
      }
      window.Douku.toastAction("已移到废纸篓", "撤销", async () => {
        await api("/api/trash/batch/restore", {method: "POST", body: JSON.stringify({confirmed: true, trash_ids: [trashId]})});
        await Promise.all([loadLibrary({showLoading: false}), loadTopics(), loadTrash()]);
        toast("已恢复");
      }, 6000);
    },
  });
}

function closeEntryMenu(restoreFocus = false) {
  const menu = $("#entry-menu");
  menu.inert = true; menu.setAttribute("aria-hidden", "true"); menu.classList.add("hidden");
  state.entryMenuTrigger?.setAttribute("aria-expanded", "false");
  if (restoreFocus && state.entryMenuTrigger?.isConnected) state.entryMenuTrigger.focus({preventScroll: true});
}

function openEntryMenu(item, trigger, {reader = false} = {}) {
  closeEntryMenu(); state.entryMenuTrigger = trigger;
  const menu = $("#entry-menu"); const nodes = [];
  if (!reader && item.original_url && /^https?:\/\//.test(item.original_url)) {
    const source = window.Douku.node("a", "打开原作品"); source.href = item.original_url; source.target = "_blank"; source.rel = "noopener noreferrer"; nodes.push(source);
  }
  if (isDatabaseManaged(item)) {
    const analyze = window.Douku.node("button", "重新分析"); analyze.type = "button";
    analyze.addEventListener("click", () => { closeEntryMenu(); reanalyzeItem(item, analyze); }); nodes.push(analyze);
    const remove = window.Douku.node("button", "删除文章", "danger-text-button"); remove.type = "button";
    remove.addEventListener("click", () => { closeEntryMenu(); deleteLibraryItem(item); }); nodes.push(remove);
  }
  if (reader) {
    const copy = window.Douku.node("button", "复制链接"); copy.type = "button";
    copy.addEventListener("click", () => { closeEntryMenu(true); copyText(`${location.origin}/articles/${encodeURIComponent(item.entry_id)}`, "已复制文章链接"); });
    nodes.splice(isDatabaseManaged(item) ? 1 : 0, 0, copy);
  }
  if (!nodes.length) nodes.push(window.Douku.node("p", "只读 Markdown 资料"));
  nodes.forEach((node) => { if (node.matches("a,button")) { node.setAttribute("role", "menuitem"); node.addEventListener("click", () => closeEntryMenu()); } });
  menu.replaceChildren(...nodes);
  const rect = trigger.getBoundingClientRect();
  const left = Math.max(16, Math.min(rect.right - 200, innerWidth - 216));
  const below = rect.bottom + 4 + menu.offsetHeight <= innerHeight - 16;
  menu.style.left = `${left}px`;
  menu.style.top = `${below ? rect.bottom + 4 : Math.max(16, rect.top - menu.offsetHeight - 4)}px`;
  menu.style.setProperty("--popover-origin", `${Math.max(0, Math.min(200, rect.left + rect.width / 2 - left))}px ${below ? "top" : "bottom"}`);
  menu.inert = false; menu.removeAttribute("aria-hidden"); menu.classList.remove("hidden");
  trigger.setAttribute("aria-expanded", "true");
  focusableElements(menu)[0]?.focus();
}

function renderLibraryEmptyState(items) {
  const hasCatalogItems = state.catalogHasItems || state.allItems.length;
  $("#empty-library").classList.toggle("hidden", items.length > 0 || hasCatalogItems);
  $("#empty-results").classList.toggle("hidden", items.length > 0 || !hasCatalogItems);
  if (!items.length && hasCatalogItems) {
    const favoriteOnly = state.section === "favorite" && !state.query
      && !state.authors.length && !state.types.length && !state.tags.length
      && !state.sources.length && !state.inspirationOnly;
    $("#empty-results-copy").textContent = favoriteOnly
      ? "还没有库内收藏。打开一篇资料，点「库内收藏」，视频会永久留在本机。"
      : state.query
      ? state.searchMode === "semantic" ? `没有找到与“${state.query}”相关的证据。请调整问题或筛选。`
        : `没有包含“${state.query}”的文章。请调整关键词或清除筛选。`
      : "当前筛选条件没有匹配文章。请调整或清除筛选。";
  }
}

function syncSourceTabs() {
  if (!state.allItemsLoaded) return;
  const counts = {video: 0, image_note: 0};
  state.allItems.forEach((item) => {
    if (item.source_kind === "video" || item.source_kind === "image_note") counts[item.source_kind] += 1;
  });
  let dropped = false;
  $$(".source-tab[data-source]").forEach((button) => {
    const kind = button.dataset.source;
    if (!kind) return;
    const empty = counts[kind] === 0;
    button.hidden = empty;
    if (empty && state.sources.includes(kind)) {
      state.sources = state.sources.filter((value) => value !== kind);
      dropped = true;
    }
  });
  if (dropped) {
    renderToolbarState();
    if (location.pathname === "/") writeStateToURL();
  }
}

function renderLibrary() {
  syncSourceTabs();
  if (state.searchMode === "semantic" && state.query
      && state.libraryLoadedScope !== semanticScopeKey()) {
    if (state.pendingLibraryScope !== semanticScopeKey()) loadLibrary({showLoading: false});
    return;
  }
  const items = filteredItems();
  const titles = {all: "资料库", recent: "最近加入", favorite: "库内收藏", inspiration: "灵感"};
  const summaries = {
    all: "整理并检索已经入库的抖音知识",
    recent: "最近采集和更新的知识资料",
    favorite: "你在资料库里收藏、会永久留在本机的资料",
    inspiration: "带有你原始灵感的文章",
  };
  const tag = state.tags.length === 1 ? state.tags[0] : "";
  const tagCount = state.facets.tag_details?.find(row => row.name === tag)?.count ?? items.length;
  $("#view-title").textContent = tag ? `# ${tag} · ${tagCount} 条` : titles[state.section];
  $("#tag-manage-link").classList.toggle("hidden", !tag);
  $("#result-count").classList.toggle("hidden", Boolean(tag));
  $("#view-summary").textContent = summaries[state.section];
  $("#result-count").textContent = String(items.length);
  $("#library-status").classList.add("hidden");
  $("#library-error").classList.add("hidden");
  const root = $("#library-results");
  const focusInMenu = $("#entry-menu").contains(document.activeElement);
  const restoreFocus = root.contains(document.activeElement) || focusInMenu;
  const action = focusInMenu ? "more" : document.activeElement?.dataset.entryAction || "open";
  closeEntryMenu();
  if (!items.some((item) => item.entry_id === state.focusedEntryId)) state.focusedEntryId = items[0]?.entry_id || "";
  const effectiveView = state.libraryView;
  root.className = `library-results ${effectiveView}`;
  root.replaceChildren(...(effectiveView === "gallery" ? renderLibraryGallery(items) : renderLibraryList(items)));
  root.classList.toggle("hidden", items.length === 0);
  renderLibraryEmptyState(items);
  renderAppliedFilters();
  updateTopicSelectionButton();
  renderPanelSummary();
  renderSidebarCounts();
  if (restoreFocus && state.panelPage === "library" && state.focusedEntryId) {
    const row = $$("[data-entry-id]", root).find((value) => value.dataset.entryId === state.focusedEntryId);
    const control = $(`[data-entry-action='${action}']:not([disabled])`, row) || $("[data-entry-action='open']", row);
    control?.focus({preventScroll: true});
  }
}

function renderSidebarCounts() {
  const items = state.allItems.length ? state.allItems : state.items;
  const counts = {all: items.length, recent: items.filter(isRecentItem).length, favorite: items.filter((item) => item.favorite).length, inspiration: items.filter((item) => item.inspirations?.length).length};
  $$(".nav-item[data-section]").forEach((button) => {
    let count = $(".nav-count", button);
    if (!count) { count = window.Douku.node("span", null, "nav-count"); button.append(count); }
    count.textContent = String(counts[button.dataset.section]);
  });
}

function updateTopicSelectionButton() {
  const button = $("#topic-select-toggle");
  const hasManagedItems = [...state.items, ...state.allItems, state.currentArticleItem]
    .some(isDatabaseManaged);
  if (!hasManagedItems) {
    state.topicSelectionMode = false;
    state.selectedEntryIds.clear();
  }
  button.disabled = !hasManagedItems;
  button.classList.toggle("hidden", !hasManagedItems);
  const topicsCreateButton = $("#topics-create-button");
  topicsCreateButton.disabled = !hasManagedItems;
  topicsCreateButton.classList.toggle("hidden", !hasManagedItems);
  button.setAttribute("aria-pressed", String(state.topicSelectionMode));
  const label = $("span", button);
  label.textContent = state.topicSelectionMode ? "选择中" : "选择";
  button.setAttribute("aria-label", "从资料库选择来源");
  button.title = "从资料库选择来源";
  $("#library-selection-bar").classList.toggle("hidden", !state.topicSelectionMode);
  $("#library-selection-count").textContent = `已选 ${state.selectedEntryIds.size} 篇`;
  $("#library-selection-next").disabled = !state.selectedEntryIds.size;
  button.classList.toggle("active", state.topicSelectionMode);
}

function openTopicDialog() {
  window.Douku.hideToast?.();
  if (!state.selectedEntryIds.size) {
    toast("请先选择至少一篇文章");
    return;
  }
  $("#topic-title-input").value = "";
  $("#topic-goal-input").value = "";
  $("#topic-instructions-input").value = "";
  $("#topic-error").classList.add("hidden");
  $("#topic-selection-summary").textContent = `已选择 ${state.selectedEntryIds.size} 篇文章，当前显示的来源排在前面。`;
  $("#topic-dialog").showModal();
  $("#topic-title-input").focus();
}

async function createTopicFromSelection() {
  if (!$("#topic-form").reportValidity()) return;
  const orderedIds = [...new Map([...filteredItems(), ...state.allItems, ...state.items].map((item) => [item.entry_id, item])).values()]
    .filter(isDatabaseManaged)
    .map((item) => item.entry_id)
    .filter((entryId) => state.selectedEntryIds.has(entryId));
  const payload = {
    title: $("#topic-title-input").value,
    goal: $("#topic-goal-input").value,
    instructions: $("#topic-instructions-input").value,
    entry_ids: orderedIds,
  };
  try {
    const result = await api("/api/topics", {method: "POST", body: JSON.stringify(payload)});
    $("#topic-dialog").close();
    state.topicSelectionMode = false;
    state.selectedEntryIds.clear();
    await loadTopics();
    await openTopic(result.topic.id);
    toast("专题已创建");
  } catch (error) {
    $("#topic-error").textContent = error.message;
    $("#topic-error").classList.remove("hidden");
  }
}

function resetLibraryFilters() {
  state.query = "";
  state.favoritesJob = "";
  state.authors = [];
  state.types = [];
  state.tags = [];
  state.sources = [];
  state.inspirationOnly = false;
  $("#search-input").value = "";
}

function clearSearchAndFilters() {
  resetLibraryFilters();
  renderFilters();
  writeStateToURL();
  loadLibrary();
}

function hideLegacyViews() {
  window.Douku?.hideAllViews();
  $("#article-view").classList.add("hidden");
  $("#topics-view").classList.add("hidden");
  $("#topic-view").classList.add("hidden");
  $("#trash-view").classList.add("hidden");
  $("#library-view").classList.add("hidden");
}

function showLibrary(push = true) {
  const fromLibrary = state.panelPage === "library";
  beginNavigation({preserveLibraryRequests: fromLibrary});
  const returningFromReader = state.panelPage === "article";
  const entryId = state.currentEntry || state.lastOpenedEntryId;
  state.currentEntry = "";
  state.currentArticleItem = null;
  state.currentTopic = "";
  hideLegacyViews();
  $("#library-view").classList.remove("hidden");
  window.Douku?.setPage("library");
  $("#topics-nav").classList.remove("active");
  $("#trash-nav").classList.remove("active");
  $$(".nav-item[data-section]").forEach((node) => node.classList.toggle("active", !state.tags.length && node.dataset.section === state.section));
  if (push) history.pushState({}, "", currentLibraryURL());
  document.title = "资料库 · Dou-wiki";
  renderLibrary();
  if (!fromLibrary && (!state.libraryLoaded || state.libraryLoadedQuery !== state.query)) {
    loadLibrary({showLoading: false});
  }
  if (returningFromReader) {
    $("#main-content").scrollTop = state.libraryScrollTop;
    focusEntry(entryId, {scroll: false});
    if (!$("#library-results").contains(document.activeElement)) $("#main-content").focus({preventScroll: true});
  }
}

function showTags() {
  beginNavigation();
  state.currentEntry = ""; state.currentArticleItem = null; state.currentTopic = "";
  window.Douku.tags.open();
}
window.Douku.refreshLibrary = () => loadLibrary({showLoading: false});

async function loadTopics() {
  const data = await api("/api/topics");
  state.topics = data.topics || [];
  renderTopicsList();
  renderSidebarTopics();
  return state.topics;
}

function renderSidebarTopics() {
  const root = $("#sidebar-recent-topics");
  if (!root) return;
  const recent = [...state.topics].sort((a, b) => String(b.topic?.updated_at || "").localeCompare(String(a.topic?.updated_at || ""))).slice(0, 5);
  root.replaceChildren(...recent.map((value) => {
    const topic = value.topic;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "nav-item sidebar-topic";
    button.append(window.Douku.node("span", topic.title));
    button.addEventListener("click", () => openTopic(topic.id));
    return button;
  }));
}

function renderTopicsList() {
  const root = $("#topics-list");
  if (!state.topics.length) {
    const empty = document.createElement("section");
    empty.className = "empty-state";
    empty.append(svgIcon("layers"));
    const title = document.createElement("h2");
    title.textContent = "还没有专题";
    const copy = document.createElement("p");
    copy.textContent = "回到资料库选择文章，创建一个严格限定来源的研究专题。";
    empty.append(title, copy);
    root.replaceChildren(empty);
    return;
  }
  root.replaceChildren(...state.topics.map((value) => {
    const topic = value.topic;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "topic-list-card";
    const title = document.createElement("h2");
    title.textContent = topic.title;
    const goal = document.createElement("p");
    goal.textContent = topic.goal || "未填写研究目标";
    const meta = document.createElement("span");
    const active = topic.sources.filter((source) => source.enabled).length;
    const updated = String(topic.updated_display || "").replace(/（北京时间）/g, "").trim();
    meta.textContent = `${active} 个启用来源${updated ? ` · ${updated}` : ""}`;
    const copy = window.Douku.node("div", null, "topic-row-copy"); copy.append(title, goal);
    button.append(svgIcon("layers"), copy, meta);
    button.addEventListener("click", () => openTopic(topic.id));
    return button;
  }));
}

async function showTopics(push = true) {
  beginNavigation();
  state.currentEntry = "";
  state.currentArticleItem = null;
  state.currentTopic = "";
  state.topicSelectionMode = false;
  hideLegacyViews();
  $("#topics-view").classList.remove("hidden");
  window.Douku?.setPage("topics");
  $$(".nav-item[data-section]").forEach((node) => node.classList.remove("active"));
  $("#topics-nav").classList.add("active");
  $("#trash-nav").classList.remove("active");
  if (push) history.pushState({}, "", "/topics");
  document.title = "专题 · Dou-wiki";
  updateTopicSelectionButton();
  await loadTopics();
}

async function loadTrash() {
  const data = await api("/api/trash");
  state.trashItems = data.items || [];
  renderTrash();
  return state.trashItems;
}

function selectedTrashItems() {
  return state.trashItems.filter((item) => state.selectedTrashIds.has(item.trash_id));
}

function updateTrashSelection() {
  const available = new Set(state.trashItems.map((item) => item.trash_id));
  state.selectedTrashIds = new Set(
    [...state.selectedTrashIds].filter((id) => available.has(id))
  );
  const selected = state.selectedTrashIds.size;
  $("#trash-toolbar").classList.toggle("hidden", !state.trashItems.length);
  $("#trash-confirm").classList.toggle("hidden", !selected);
  $("#trash-selection-count").textContent = `已选 ${selected} 条`;
  $("#trash-clear-selection").disabled = !selected;
  $("#trash-select-all").disabled = !state.trashItems.length;
}

function setTrashSelection(ids) {
  state.selectedTrashIds = new Set(ids);
  $$("#trash-list input[type='checkbox']").forEach((input) => {
    input.checked = state.selectedTrashIds.has(input.value);
  });
  updateTrashSelection();
}

function renderTrash() {
  const root = $("#trash-list");
  state.trashSelectionAnchor = "";
  $("#trash-count").textContent = String(state.trashItems.length);
  if (!state.trashItems.length) {
    const empty = document.createElement("section");
    empty.className = "empty-state";
    empty.append(svgIcon("trash-2"));
    const title = document.createElement("h2");
    title.textContent = "废纸篓是空的";
    const copy = document.createElement("p");
    copy.textContent = "删除的资料和专题会暂存在这里。";
    empty.append(title, copy);
    root.replaceChildren(empty);
    updateTrashSelection();
    return;
  }
  root.replaceChildren(...state.trashItems.map((item) => {
    const row = document.createElement("article");
    row.className = "trash-item";
    row.dataset.trashId = item.trash_id; row.tabIndex = item.trash_id === (state.trashFocusId || state.trashItems[0]?.trash_id) ? 0 : -1;
    row.addEventListener("focusin", () => { state.trashFocusId = item.trash_id; $$(".trash-item").forEach((node) => { node.tabIndex = node === row ? 0 : -1; }); });
    row.addEventListener("keydown", (event) => {
      if (event.target.closest("button,a,input")) return;
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      const rows = $$(".trash-item"); const at = rows.indexOf(row);
      if (["j", "k", "ArrowDown", "ArrowUp"].includes(key)) {
        event.preventDefault();
        const next = rows[Math.max(0, Math.min(rows.length - 1, at + (["j", "ArrowDown"].includes(key) ? 1 : -1)))];
        if (event.shiftKey) {
          if (!state.trashSelectionAnchor) { state.trashSelectionAnchor = item.trash_id; state.trashRangeSelect = !check.checked; }
          const anchor = rows.findIndex((node) => node.dataset.trashId === state.trashSelectionAnchor);
          const to = rows.indexOf(next);
          rows.slice(Math.min(anchor, to), Math.max(anchor, to) + 1).forEach((node) => {
            const id = node.dataset.trashId;
            if (state.trashRangeSelect) state.selectedTrashIds.add(id); else state.selectedTrashIds.delete(id);
          });
          setTrashSelection([...state.selectedTrashIds]);
        }
        if (!event.shiftKey) state.trashSelectionAnchor = "";
        next.focus();
      } else if (key === "x") { event.preventDefault(); check.click(); }
    });
    const check = document.createElement("input");
    check.type = "checkbox";
    check.value = item.trash_id;
    check.checked = state.selectedTrashIds.has(item.trash_id);
    check.setAttribute("aria-label", `选择 ${item.title}`);
    check.addEventListener("change", () => {
      state.trashSelectionAnchor = "";
      if (check.checked) state.selectedTrashIds.add(item.trash_id);
      else state.selectedTrashIds.delete(item.trash_id);
      updateTrashSelection();
    });
    const icon = document.createElement("span");
    const isTopic = item.kind === "topic";
    icon.className = `trash-item-icon ${isTopic ? "is-topic" : item.source_kind === "image_note" ? "is-image" : "is-video"}`;
    icon.append(svgIcon(isTopic ? "layers" : item.source_kind === "image_note" ? "image" : "video"));
    const copy = document.createElement("div");
    copy.className = "trash-item-copy";
    const title = document.createElement("h2");
    title.textContent = item.title;
    const meta = document.createElement("p");
    const context = isTopic ? `专题 · ${item.source_count} 篇来源 · ${item.note_count} 篇笔记` : item.author || "未知作者";
    meta.textContent = `${context} · 删除于 ${item.deleted_display || "时间未知"} · ${formatBytes(item.size_bytes)}`;
    copy.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "trash-item-actions";
    const restore = document.createElement("button");
    restore.type = "button";
    restore.className = "secondary-button is-sm";
    restore.append(svgIcon("rotate-ccw"), document.createTextNode("恢复"));
    restore.addEventListener("click", () => openDestructiveDialog({
      title: isTopic ? "恢复这个专题？" : "恢复这条资料？",
      description: isTopic ? `将恢复专题「${item.title}」的设置、来源选择和笔记。已删除的来源文章需要单独恢复。` : `将恢复《${item.title}》的文章、原始记录、视频或图片，并重新加入检索和相关专题。`,
      confirmLabel: "确认恢复",
      action: async () => {
        return api(`/api/trash/${encodeURIComponent(item.trash_id)}/restore`, {
          method: "POST", body: JSON.stringify({confirmed: true}),
        });
      },
      successMessage: isTopic ? "专题已恢复" : "资料已恢复",
      onSuccess: async (result) => {
        await Promise.all([loadTrash(), loadLibrary({showLoading: false}), loadTopics()]);
        if (result.topic_id) await openTopic(result.topic_id);
        else await openArticle(result.entry_id);
      },
    }));
    const purge = document.createElement("button");
    purge.type = "button";
    purge.className = "danger-text-button is-sm";
    purge.append(svgIcon("trash-2"), document.createTextNode("彻底删除"));
    purge.addEventListener("click", () => openDestructiveDialog({
      title: isTopic ? "彻底删除这个专题？" : "彻底删除这条资料？",
      description: `《${item.title}》及其 ${formatBytes(item.size_bytes)} 本地文件将被永久删除，无法通过 Dou-wiki 恢复；Markdown 可能仍存在于 Git 历史中。`,
      confirmLabel: "彻底删除",
      action: async () => {
        return api(`/api/trash/${encodeURIComponent(item.trash_id)}`, {
          method: "DELETE", body: JSON.stringify({confirmed: true}),
        });
      },
      successMessage: isTopic ? "专题已彻底删除" : "资料已彻底删除",
      onSuccess: async () => {
        await loadTrash();
      },
    }));
    actions.append(restore, purge);
    row.append(check, icon, copy, actions);
    return row;
  }));
  updateTrashSelection();
}

function openTrashBatchDialog(kind) {
  const items = selectedTrashItems();
  if (!items.length) return;
  const count = items.length;
  const ids = items.map((item) => item.trash_id);
  const size = formatBytes(items.reduce((total, item) => total + (Number(item.size_bytes) || 0), 0));
  if (kind === "restore") {
    openDestructiveDialog({
      title: count === 1 ? "恢复这条资料？" : `恢复这 ${count} 条资料？`,
      description: "将恢复所选资料或专题；专题的来源选择和已保存笔记也会恢复。",
      confirmLabel: "确认恢复",
      action: async () => {
        return api("/api/trash/batch/restore", {
          method: "POST", body: JSON.stringify({confirmed: true, trash_ids: ids}),
        });
      },
      successMessage: count === 1 ? "资料已恢复" : `已恢复 ${count} 条资料`,
      onSuccess: async () => {
        state.selectedTrashIds.clear();
        await Promise.all([loadTrash(), loadLibrary({showLoading: false}), loadTopics()]);
      },
    });
    return;
  }
  openDestructiveDialog({
    title: count === 1 ? "彻底删除这条资料？" : `彻底删除这 ${count} 条资料？`,
    description: `所选资料及其共 ${size} 本地文件将被永久删除，无法通过 Dou-wiki 恢复；Markdown 可能仍存在于 Git 历史中。`,
    confirmLabel: "彻底删除",
    action: async () => {
      return api("/api/trash/batch/purge", {
        method: "POST", body: JSON.stringify({confirmed: true, trash_ids: ids}),
      });
    },
    successMessage: count === 1 ? "资料已彻底删除" : `已彻底删除 ${count} 条资料`,
    onSuccess: async () => {
      state.selectedTrashIds.clear();
      await loadTrash();
    },
  });
}

async function showTrash(push = true) {
  beginNavigation();
  state.currentEntry = "";
  state.currentArticleItem = null;
  state.currentTopic = "";
  state.topicSelectionMode = false;
  hideLegacyViews();
  $("#trash-view").classList.remove("hidden");
  window.Douku?.setPage("trash");
  $$(".nav-item").forEach((node) => node.classList.remove("active"));
  $("#trash-nav").classList.add("active");
  if (push) history.pushState({}, "", "/trash");
  document.title = "废纸篓 · Dou-wiki";
  updateTopicSelectionButton();
  await loadTrash();
}

function topicResearchPrompt(topic) {
  return [
    `请研究 Dou-wiki 专题「${topic.title}」（专题 ID：${topic.id}）。`,
    "只使用这个专题当前启用的来源。",
    `研究问题：${topic.goal || "请在这里写明你想解决的问题。"}`,
    ...(topic.instructions ? [`补充要求：${topic.instructions}`] : []),
    "请整理核心方法、适用场景和注意事项，结论附来源及可用的时间位置。没有相关证据时明确说明。",
    "先在对话中给我草稿，等我要求保存时再写入专题笔记。",
  ].join("\n");
}

function makeTopicGuide(value) {
  const topic = value.topic;
  const activeCount = topic.sources.filter(source => source.enabled).length;
  const guide = window.Douku.node("details", null, "topic-guide");
  guide.open = !value.artifacts.length;
  const summary = window.Douku.node("summary", value.artifacts.length ? "继续研究与保存" : "接下来怎么做");
  summary.append(svgIcon("chevron-right"));
  const content = window.Douku.node("div", null, "topic-guide-content");
  content.append(window.Douku.node("p", "研究在 Agent 对话中进行，这里管理来源并保存研究笔记。", "topic-guide-copy"));
  const sources = window.Douku.node("button", `查看来源 · ${activeCount} / ${topic.sources.length} 篇已启用`, "text-button topic-guide-sources");
  sources.type = "button"; sources.prepend(svgIcon("layers"));
  sources.addEventListener("click", () => { selectPanelTab("sources"); setChatPanelOpen(true); });
  const steps = window.Douku.node("ol", null, "topic-guide-steps");
  [
    "核对来源：打开右侧来源面板，勾选本次研究要使用的资料。",
    "开始研究：修改下面的研究问题，复制指令，发到已连接 Dou-wiki 的 Agent 对话。",
    "保存笔记：草稿满意后，在同一对话要求保存，再回到这里查看。",
  ].forEach(text => steps.append(window.Douku.node("li", text)));
  const label = window.Douku.node("label", "研究指令（可修改）", "topic-prompt-label");
  label.htmlFor = "topic-research-prompt";
  const prompt = window.Douku.node("textarea", null, "topic-research-prompt");
  prompt.id = "topic-research-prompt"; prompt.rows = 5;
  prompt.value = state.topicPrompts.get(topic.id) ?? topicResearchPrompt(topic);
  prompt.addEventListener("input", () => state.topicPrompts.set(topic.id, prompt.value));
  const actions = window.Douku.node("div", null, "topic-guide-actions");
  const copy = window.Douku.node("button", "复制研究指令", "primary-button");
  copy.type = "button"; copy.disabled = !activeCount;
  copy.addEventListener("click", () => copyText(prompt.value, "研究指令已复制，请发到 Agent 对话中"));
  actions.append(copy);
  if (!value.artifacts.length) {
    const remove = window.Douku.node("button", null, "text-button topic-delete-button");
    remove.id = "topic-delete-empty"; remove.type = "button";
    remove.append(svgIcon("trash-2"), document.createTextNode("删除专题"));
    remove.addEventListener("click", () => deleteTopic(topic));
    actions.append(remove);
  }
  content.append(sources, steps, label, prompt, actions);
  if (!activeCount) content.append(window.Douku.node("p", "当前没有启用的来源，请先在来源面板中勾选资料。", "topic-guide-copy"));
  guide.append(summary, content);
  return guide;
}

function deleteTopic(topic) {
  const navigationId = state.navigationRequestId;
  openDestructiveDialog({
    title: `删除专题「${topic.title}」？`,
    description: "专题设置、来源选择和已保存笔记会移入废纸篓，可在那里恢复。来源文章、视频和灵感保留。",
    confirmLabel: "移入废纸篓",
    action: () => api(`/api/topics/${encodeURIComponent(topic.id)}`, {
      method: "DELETE", body: JSON.stringify({confirmed: true}),
    }),
    successMessage: "专题已移入废纸篓",
    onSuccess: async () => {
      state.topicPrompts.delete(topic.id);
      if (navigationId === state.navigationRequestId && state.currentTopic === topic.id) {
        await showTopics(); $("#main-content").focus({preventScroll: true});
      } else await loadTopics();
    },
  });
}

function renderTopicData(value) {
  const topic = value.topic;
  $("#topic-header-title").textContent = topic.title;
  $("#topic-delete").classList.toggle("hidden", !value.artifacts.length);
  $("#topic-delete").onclick = () => deleteTopic(topic);
  const root = $("#topic-content");
  const header = document.createElement("header");
  header.className = "topic-header";
  const kicker = document.createElement("span");
  kicker.className = "dialog-kicker";
  kicker.textContent = "选定来源研究";
  const title = document.createElement("h1");
  title.textContent = topic.title;
  const goal = document.createElement("p");
  goal.className = "topic-goal";
  goal.textContent = topic.goal || "未填写研究目标";
  header.append(kicker, title, goal);
  if (topic.instructions) {
    const instructions = window.Douku.node("details", null, "topic-instructions");
    instructions.append(window.Douku.node("summary", "自定义指令"), window.Douku.node("p", topic.instructions));
    header.append(instructions);
  }

  const sourceSection = document.createElement("section");
  sourceSection.className = "topic-section";
  const sourceTitle = document.createElement("h2");
  sourceTitle.textContent = `${topic.sources.filter((source) => source.enabled).length} / ${topic.sources.length} 个来源已启用`;
  const sourceHint = document.createElement("p");
  sourceHint.textContent = "停用后，Agent 研究专题时不再使用这篇资料。";
  const sourceList = document.createElement("div");
  sourceList.className = "topic-source-list";
  topic.sources.forEach((source) => {
    const label = document.createElement("label");
    label.className = "topic-source-row";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = source.enabled;
    checkbox.dataset.entryId = source.entry_id;
    const copy = document.createElement("span");
    copy.textContent = source.title || source.entry_id;
    const open = document.createElement("button");
    open.type = "button";
    open.className = "icon-button is-sm";
    open.append(svgIcon("arrow-up-right"));
    open.setAttribute("aria-label", `打开文章：${source.title || source.entry_id}`);
    open.addEventListener("click", (event) => { event.preventDefault(); openArticle(source.entry_id); });
    label.append(checkbox, copy, open);
    sourceList.append(label);
  });
  sourceList.addEventListener("change", async (event) => {
    const navigationId = state.navigationRequestId;
    const isCurrent = () => navigationId === state.navigationRequestId
      && state.currentTopic === topic.id && !$("#topic-view").classList.contains("hidden");
    const sourceFocus = event.target.dataset.entryId;
    const sources = $$("input[type='checkbox']", sourceList).map((node) => ({
      entry_id: node.dataset.entryId, enabled: node.checked,
    }));
    sourceList.querySelectorAll("input").forEach((input) => { input.disabled = true; });
    try {
      await api(`/api/topics/${encodeURIComponent(topic.id)}/sources`, {
        method: "PUT", body: JSON.stringify({sources}),
      });
      if (!isCurrent()) return;
      toast("专题来源已更新");
      await openTopic(topic.id, false, {sourceFocus});
    } catch (error) {
      if (!isCurrent()) return;
      toast(error.message);
      await openTopic(topic.id, false, {sourceFocus});
    }
  });
  sourceSection.append(sourceTitle, sourceList, sourceHint);
  $("#topic-sources-pane").replaceChildren(sourceSection);

  const artifactSection = document.createElement("section");
  artifactSection.className = "topic-section";
  const artifactTitle = document.createElement("h2");
  artifactTitle.textContent = "专题笔记";
  artifactSection.append(artifactTitle);
  const artifactList = document.createElement("div");
  artifactList.className = "topic-artifacts";
  value.artifacts.forEach((artifact) => {
    const card = document.createElement("article");
    card.className = "topic-artifact-card";
    const heading = document.createElement("header");
    const h3 = document.createElement("h3");
    h3.textContent = artifact.title;
    const status = document.createElement("span");
    status.className = artifact.status === "needs_update" ? "artifact-stale" : "status-badge";
    status.textContent = artifact.status === "needs_update" ? "需要更新" : "";
    if (artifact.status === "needs_update") status.prepend(svgIcon("rotate-ccw"));
    const meta = document.createElement("small");
    meta.textContent = `${artifact.created_display}${artifact.model ? ` · ${artifact.model}` : ""}`;
    heading.append(h3, status, meta);
    const body = document.createElement("div");
    body.className = "article-body";
    body.innerHTML = artifact.html;
    const expanded = window.Douku.node("details", null, "artifact-full");
    const summary = window.Douku.node("summary", "展开全文");
    expanded.append(summary);
    expanded.addEventListener("toggle", () => { card.classList.toggle("is-expanded", expanded.open); summary.textContent = expanded.open ? "收起全文" : "展开全文"; });
    card.append(heading, body, expanded);
    artifactList.append(card);
    requestAnimationFrame(() => {
      const height = parseFloat(getComputedStyle(body).lineHeight) * 12;
      const clipped = body.scrollHeight > height;
      card.classList.toggle("is-collapsible", clipped);
      expanded.hidden = !clipped;
    });
  });
  if (!value.artifacts.length) {
    const empty = document.createElement("p");
    empty.textContent = "还没有专题笔记。把上面的研究指令发到 Agent 对话，研究完成后要求保存，即可在这里查看。";
    artifactList.append(empty);
  }
  artifactSection.append(artifactList);
  root.replaceChildren(header, makeTopicGuide(value), artifactSection);
}

async function openTopic(topicId, push = true, {sourceFocus = ""} = {}) {
  const navigationId = beginNavigation();
  const preserve = !push && state.currentTopic === topicId;
  const savedScroll = preserve ? $("#main-content").scrollTop : 0;
  const focusedSource = sourceFocus || document.activeElement?.dataset.entryId;
  try {
    const value = await api(`/api/topics/${encodeURIComponent(topicId)}`);
    if (navigationId !== state.navigationRequestId) return;
    state.currentEntry = "";
    state.currentTopic = topicId;
    renderTopicData(value);
    hideLegacyViews();
    $("#topic-view").classList.remove("hidden");
    window.Douku?.setPage("topic");
    $$(".nav-item[data-section]").forEach((node) => node.classList.remove("active"));
    $("#topics-nav").classList.add("active");
    $("#trash-nav").classList.remove("active");
    if (push) history.pushState({topicId}, "", `/topics/${encodeURIComponent(topicId)}`);
    document.title = `${value.topic.title} · Dou-wiki`;
    await loadTopics();
    if (navigationId !== state.navigationRequestId) return;
    $(".library-main").scrollTo({top: savedScroll, behavior: "instant"});
    if (preserve && focusedSource) $$("#topic-sources-pane input").find((input) => input.dataset.entryId === focusedSource)?.focus({preventScroll: true});
  } catch (error) {
    if (navigationId !== state.navigationRequestId) return;
    if (error.status === 404) window.Douku.showMissing();
    else toast(error.message);
  }
}

function makeArticleKeyframes(frames, videoUrl, {open = false, keep = false} = {}) {
  if (!frames) return null;
  const details = window.Douku.node("details", null, "article-keyframes");
  details.open = open;
  const summary = window.Douku.node("summary");
  const title = window.Douku.node("span", "Video Snapshots", "article-section-title"); title.lang = "en";
  const meta = window.Douku.node("span", `${frames.length} ${frames.length === 1 ? "image" : "images"}${keep ? " · Kept" : ""}`, "article-section-meta"); meta.lang = "en";
  summary.append(svgIcon("chevron-right", "article-disclosure-icon"), title, meta);
  details.append(summary);
  if (!frames.length) details.append(window.Douku.node("p", "暂无可用关键帧。素材可能已清理。", "hint-copy"));
  const grid = window.Douku.node("div", null, "keyframe-grid");
  frames.forEach((frame, index) => {
    if (!frame.url?.startsWith("/media/")) return;
    const figure = window.Douku.node("figure");
    const link = window.Douku.node("a");
    link.href = frame.url; link.target = "_blank"; link.rel = "noopener";
    const image = window.Douku.node("img");
    image.src = frame.url; image.loading = "lazy"; image.decoding = "async";
    image.alt = `第 ${index + 1} 张关键帧，打开大图`;
    link.append(image); figure.append(link);
    const caption = window.Douku.node("figcaption");
    const label = frame.timestamp_ms == null ? "时间未知" : formatLocation(frame);
    if (videoUrl && frame.timestamp_ms != null) {
      const seek = window.Douku.node("a", label); seek.href = `${videoUrl}#t=${frame.timestamp_ms / 1000}`;
      seek.target = "_blank"; seek.rel = "noopener"; seek.setAttribute("aria-label", `打开本地视频 ${label}`);
      caption.append(seek);
    } else caption.textContent = label;
    figure.append(caption); grid.append(figure);
  });
  details.append(grid);
  details.addEventListener("toggle", updateReadingMetrics);
  details.addEventListener("load", updateReadingMetrics, true);
  return details;
}

function makeArticleTranscript(transcript, {open = false} = {}) {
  if (!transcript) return null;
  const rows = transcript.segments || [];
  const details = window.Douku.node("details", null, "article-transcript");
  details.open = open;
  const summary = window.Douku.node("summary");
  const source = transcript.source === "corrected" ? "Corrected" : "Original";
  const title = window.Douku.node("span", "Transcript", "article-section-title"); title.lang = "en";
  const meta = window.Douku.node("span", `${source} · ${rows.length} ${rows.length === 1 ? "segment" : "segments"}`, "article-section-meta"); meta.lang = "en";
  summary.append(svgIcon("chevron-right", "article-disclosure-icon"), title, meta);
  details.append(summary);
  if (rows.length) {
    const list = window.Douku.node("ol", null, "transcript-segments");
    rows.forEach((row) => {
      const segment = window.Douku.node("li", null, "transcript-segment");
      const start = formatLocation({timestamp_ms: row.start_ms});
      const end = formatLocation({timestamp_ms: row.end_ms});
      segment.append(window.Douku.node("span", row.timestamp_precision === "window"
        ? `约 ${start}–${end}（时间未经精确对齐）` : `${start}–${end}`, "transcript-time"),
        window.Douku.node("p", row.text, "transcript-text"));
      list.append(segment);
    });
    details.append(list);
  } else details.append(window.Douku.node("p", "暂无可用逐字稿。", "hint-copy"));
  details.addEventListener("toggle", updateReadingMetrics);
  return details;
}

function makeArticleHeader(item) {
  const hero = window.Douku.node("header", null, "article-hero");
  const cover = makeCover(item, "article-cover");
  cover.setAttribute("aria-hidden", "true");
  const copy = window.Douku.node("div", null, "article-hero-copy");
  const kicker = [item.content_type_label || "其他", sourceLabel(item), item.author || "未知作者"];
  if (!isDatabaseManaged(item)) kicker.push("只读");
  const byline = window.Douku.node("p", null, "article-byline");
  const captured = String(item.captured_display || "").replace(/（北京时间）/g, "").trim();
  [`发布 ${formatDate(item.published_at)}`, captured ? `采集 ${captured}` : ""].filter(Boolean)
    .forEach((text) => byline.append(window.Douku.node("span", text)));
  copy.append(window.Douku.node("p", kicker.join(" · "), "article-kicker"), window.Douku.node("h1", item.title), byline);
  if (item.tags?.length) {
    const tags = window.Douku.node("div", null, "article-tags");
    item.tags.forEach((tag) => {
      const button = window.Douku.node("button", tag, "info-tag");
      button.type = "button";
      button.addEventListener("click", () => {
        state.tags = [tag]; showLibrary(false); renderFilters(); writeStateToURL("push");
      });
      tags.append(button);
    });
    copy.append(tags);
  }
  hero.append(copy, cover);
  return hero;
}

function renderArticleToolbar(item) {
  const items = filteredItems();
  const index = item ? items.findIndex((value) => value.entry_id === item.entry_id) : -1;
  $("#reader-previous").disabled = index <= 0;
  $("#reader-next").disabled = index < 0 || index >= items.length - 1;
  const actions = $("#reader-actions");
  if (!item) { actions.replaceChildren(); return; }
  const nodes = [];
  const favorite = makeFavoriteButton(item, "article-favorite-button is-sm");
  if (favorite) nodes.push(favorite);
  if (item.original_url && /^https?:\/\//.test(item.original_url)) {
    const original = window.Douku.node("a", null, "icon-button reader-original is-sm");
    original.append(svgIcon("external-link")); original.href = item.original_url;
    original.target = "_blank"; original.rel = "noopener noreferrer";
    original.setAttribute("aria-label", "打开原作品"); original.title = "打开原作品（Shift O）";
    nodes.push(original);
  }
  if (item.local_video_url) {
    const video = window.Douku.node("a", null, "text-button reader-local-video");
    video.append(svgIcon("video"), window.Douku.node("span", "本地视频")); video.href = item.local_video_url;
    video.target = "_blank"; video.rel = "noopener noreferrer";
    video.setAttribute("aria-label", "打开本地视频");
    nodes.push(video);
  }
  const more = window.Douku.node("button", null, "icon-button entry-more is-sm");
  more.type = "button"; more.append(svgIcon("more-horizontal"));
  more.setAttribute("aria-label", "更多文章操作"); more.setAttribute("aria-haspopup", "menu"); more.setAttribute("aria-expanded", "false");
  more.addEventListener("click", () => openEntryMenu(item, more, {reader: true}));
  nodes.push(more); actions.replaceChildren(...nodes);
}

function openAdjacentArticle(delta) {
  if (!state.currentArticleItem) return;
  const items = filteredItems();
  const index = items.findIndex((item) => item.entry_id === state.currentEntry);
  if (index < 0 || !items[index + delta]) return;
  openArticle(items[index + delta].entry_id);
}

function clearReader() {
  state.readerObserver?.disconnect(); state.readerObserver = null;
  state.readingBlocks = []; state.readingIndex = -1;
  if (state.readingProgressFrame !== null) cancelAnimationFrame(state.readingProgressFrame);
  state.readingProgressFrame = null;
}

function decorateChapterTimes() {
  $$("#article-content .article-body h3").forEach((heading) => {
    const match = heading.textContent.match(/^\s*▶?\s*((?:\d+:)?\d{1,2}:\d{2})[\s\u3000]+/);
    if (!match) return;
    const walker = document.createTreeWalker(heading, NodeFilter.SHOW_TEXT);
    const first = walker.nextNode();
    let last = first;
    let remaining = match[0].length;
    while (last && remaining > last.nodeValue.length) {
      remaining -= last.nodeValue.length; last = walker.nextNode();
    }
    if (!last) return;
    const range = document.createRange();
    range.setStart(first, 0); range.setEnd(last, remaining); range.deleteContents();
    const time = window.Douku.node("span", match[1], "chapter-time");
    range.insertNode(time); time.after(document.createTextNode(" "));
  });
}

function decorateCallouts() {
  $$("#article-content .article-body blockquote").forEach((quote) => {
    const first = quote.firstElementChild;
    const strong = first?.firstElementChild;
    if (first?.tagName === "P" && strong?.tagName === "STRONG"
        && strong.textContent === "AI 推断" && first.textContent.trimStart().startsWith("AI 推断")) {
      quote.classList.add("callout");
    }
  });
}

function decorateArticleSectionHeadings(body) {
  const sections = {
    "一句话": "Summary", "核心收获": "Key Takeaways", "时间轴图解": "Chapters",
    "下一步": "Next Steps", "原图": "Original Images", "来源": "Sources",
  };
  const types = {
    "教程": "Tutorial", "知识解释": "Explanation", "观点": "Opinion", "推荐": "Recommendations",
    "新闻/事件": "News & Events", "新闻事件": "News & Events", "故事/案例": "Case Study", "故事案例": "Case Study",
    "清单": "List", "其他": "Other",
  };
  $$(':scope > h2', body).forEach((heading) => {
    const original = heading.textContent.trim();
    const card = original.match(/^内容卡片\s*·\s*(.+)$/);
    const label = sections[original] || (card && types[card[1]] ? `Quick Reference · ${types[card[1]]}` : null);
    if (!label) return;
    heading.textContent = label;
    heading.lang = "en";
    heading.classList.add("article-section-title");
  });
}

function decorateArticleImages(body, item) {
  if (item.source_kind !== "image_note") return;
  const images = [...body.querySelectorAll(":scope > p > img")]
    .filter(image => /^抖音图文第\s*\d+\s*张$/.test(image.alt));
  if (!images.length) return;
  const headings = [...body.querySelectorAll(":scope > h2")];
  let heading = headings.find(node => node.textContent.trim() === "原图");
  if (!heading) {
    heading = window.Douku.node("h2", "原图");
    body.insertBefore(heading, headings.find(node => node.textContent.trim() === "来源") || null);
  }
  const figures = document.createDocumentFragment();
  images.forEach((image, index) => {
    const paragraph = image.parentElement;
    const figure = window.Douku.node("figure", null, "article-original-image");
    const link = window.Douku.node("a");
    link.href = image.src; link.target = "_blank"; link.rel = "noopener noreferrer";
    link.setAttribute("aria-label", `打开第 ${index + 1} 张原图`);
    image.loading = "lazy"; image.decoding = "async";
    link.append(image);
    figure.append(link, window.Douku.node("figcaption", `${index + 1} / ${images.length}`));
    figures.append(figure);
    if (!paragraph.textContent.trim() && !paragraph.querySelector("img")) paragraph.remove();
  });
  heading.after(figures);
}

function buildReaderToc() {
  state.readerObserver?.disconnect();
  const root = $("#reader-toc");
  const back = window.Douku.node("button", null, "text-button reader-back");
  back.type = "button"; back.id = "reader-back";
  back.append(svgIcon("arrow-left"), window.Douku.node("span", "资料库"));
  back.addEventListener("click", () => showLibrary());
  const links = window.Douku.node("div", null, "reader-toc-items");
  const headings = $$("#article-content .article-body h2, #article-content .article-body h3");
  headings.forEach((heading, index) => {
    heading.id = `h-${index}`;
    const link = window.Douku.node("a", null, `toc-item toc-${heading.tagName.toLowerCase()}`);
    link.href = `#${heading.id}`; link.dataset.headingId = heading.id;
    const time = heading.querySelector(".chapter-time");
    if (time) {
      link.append(time.cloneNode(true), document.createTextNode(heading.textContent.slice(time.textContent.length).trim()));
    } else link.textContent = heading.textContent;
    if (heading.lang) link.lang = heading.lang;
    link.title = heading.textContent.trim();
    link.addEventListener("click", (event) => {
      event.preventDefault();
      heading.scrollIntoView({block: "start", behavior: "instant"});
      focusReadingBlock(state.readingBlocks.indexOf(heading), {scroll: false});
      $("#main-content").focus({preventScroll: true}); updateReadingMetrics();
    });
    links.append(link);
  });
  if (!headings.length) links.append(window.Douku.node("p", "这篇资料没有章节标题", "hint-copy"));
  $("#reader-back")?.remove();
  $(".sidebar-brand-row").prepend(back);
  root.replaceChildren(window.Douku.node("h2", "目录", "reader-toc-label"), links);
  state.readerObserver = new IntersectionObserver(updateReadingMetrics, {
    root: $("#main-content"), rootMargin: "-48px 0px -70% 0px", threshold: 0,
  });
  headings.forEach((heading) => state.readerObserver.observe(heading));
  updateReadingMetrics();
}

function initReadingFocus() {
  const body = $("#article-content .article-body");
  const blocks = [];
  const visible = (node) => node.getClientRects().length && getComputedStyle(node).visibility !== "hidden";
  const listItems = (list) => {
    [...list.children].filter((node) => node.tagName === "LI").forEach((item) => {
      const hasOwnContent = [...item.childNodes].some((child) => child.nodeType === Node.TEXT_NODE
        ? child.textContent.trim() : !child.matches("ul,ol") && (child.textContent.trim() || child.querySelector("img")));
      if (hasOwnContent && visible(item)) blocks.push(item);
      [...item.children].filter((node) => node.matches("ul,ol")).forEach(listItems);
    });
  };
  [...body.children].forEach((node) => {
    if (node.matches("ul,ol")) listItems(node);
    else if (node.matches("p,h2,h3,h4,blockquote,table,pre,img,figure") && visible(node)) blocks.push(node);
  });
  state.readingBlocks = blocks; state.readingIndex = -1;
  body.addEventListener("click", (event) => {
    if (event.target.closest("a,button,input,textarea,select")) return;
    let target = event.target;
    while (target && target !== body) {
      const index = state.readingBlocks.indexOf(target);
      if (index >= 0) { focusReadingBlock(index, {scroll: false}); $("#main-content").focus({preventScroll: true}); return; }
      target = target.parentElement;
    }
  });
}

function focusReadingBlock(index, {scroll = true} = {}) {
  if (index < 0 || index >= state.readingBlocks.length) return;
  state.readingBlocks[state.readingIndex]?.classList.remove("is-reading-focus");
  state.readingIndex = index;
  const block = state.readingBlocks[index]; block.classList.add("is-reading-focus");
  if (scroll) block.scrollIntoView({block: "nearest", behavior: "instant"});
}

function moveReadingFocus(delta) {
  if (!state.readingBlocks.length) return;
  const next = state.readingIndex < 0 ? 0 : Math.max(0, Math.min(state.readingBlocks.length - 1, state.readingIndex + delta));
  focusReadingBlock(next);
}

function updateReadingMetrics() {
  if (state.panelPage !== "article") return;
  const main = $("#main-content");
  const extent = main.scrollHeight - main.clientHeight;
  const progress = extent > 0 ? Math.max(0, Math.min(1, main.scrollTop / extent)) : 1;
  $("#reading-progress-fill").style.width = `${progress * 100}%`;
  $(".reading-progress").setAttribute("aria-valuenow", String(Math.round(progress * 100)));
  const headings = $$("#article-content .article-body h2, #article-content .article-body h3");
  const top = main.getBoundingClientRect().top + 64;
  let active = headings[0];
  headings.forEach((heading) => { if (heading.getBoundingClientRect().top <= top + 1) active = heading; });
  if (extent > 0 && progress >= .999) active = headings.at(-1);
  $$("#reader-toc .toc-item").forEach((link) => {
    const current = link.dataset.headingId === active?.id;
    link.classList.toggle("active", current);
    if (current) link.setAttribute("aria-current", "location"); else link.removeAttribute("aria-current");
  });
}

function bindReadingProgress() {
  if (!state.readingProgressBound) {
    $("#main-content").addEventListener("scroll", () => {
      if (state.panelPage !== "article" || state.readingProgressFrame !== null) return;
      state.readingProgressFrame = requestAnimationFrame(() => {
        state.readingProgressFrame = null; updateReadingMetrics();
      });
    }, {passive: true});
    state.readingProgressBound = true;
  }
  updateReadingMetrics();
}

function renderArticleData(data, entryId, push) {
  const root = $("#article-content");
  const transcriptOpen = state.currentEntry === entryId
    && Boolean(root.querySelector(".article-transcript")?.open);
  const keyframesOpen = state.currentEntry === entryId
    && Boolean(root.querySelector(".article-keyframes")?.open);
  state.currentEntry = entryId;
  state.currentArticleItem = data.item;
  state.currentTopic = "";
  const body = document.createElement("div");
  body.className = "article-body";
  body.innerHTML = data.html;
  decorateArticleImages(body, data.item);
  const transcript = makeArticleTranscript(data.transcript, {open: transcriptOpen});
  if (transcript) {
    const source = [...body.querySelectorAll(":scope > h2")].find(node => node.textContent.trim() === "来源");
    body.insertBefore(transcript, source || null);
  }
  const keyframes = makeArticleKeyframes(data.keyframes, data.item.local_video_url,
    {open: keyframesOpen, keep: data.keep_keyframes});
  if (keyframes) {
    const source = [...body.querySelectorAll(":scope > h2")].find(node => node.textContent.trim() === "来源");
    body.insertBefore(keyframes, source || null);
  }
  decorateArticleSectionHeadings(body);
  clearReader();
  const hero = makeArticleHeader(data.item);
  root.replaceChildren(hero, body);
  hideLegacyViews();
  $("#article-view").classList.remove("hidden");
  window.Douku?.setPage("article");
  if (push) history.pushState({entryId}, "", `/articles/${encodeURIComponent(entryId)}`);
  document.title = `${data.item.title} · Dou-wiki`;
  renderLibrary();
  state.lastOpenedEntryId = entryId;
  renderArticleToolbar(data.item);
  decorateChapterTimes(); decorateCallouts(); initReadingFocus(); buildReaderToc(); bindReadingProgress();
  $(".library-main").scrollTo({top: 0, behavior: "instant"});
  $("#main-content").focus({preventScroll: true});
  return hero;
}

function focusCitation(citation) {
  const root = $("#article-content .article-body");
  if (!root) return;
  $$("mark.citation-highlight", root).forEach((mark) => mark.replaceWith(...mark.childNodes));
  root.normalize();
  const candidates = [];
  if (citation.timestamp_ms != null) {
    const time = $$(".chapter-time", root).find((node) => node.textContent.split(":").reduce((seconds, value) => seconds * 60 + Number(value), 0) === Math.floor(citation.timestamp_ms / 1000));
    if (time) candidates.push(time.textContent);
  }
  if (citation.image_index) candidates.push(`第 ${citation.image_index} 张`);
  if (citation.timestamp_ms != null) candidates.push(formatLocation(citation));
  if (citation.snippet) candidates.push(citation.snippet.trim().slice(0, 32));
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const textNode = walker.currentNode;
    const match = candidates.find((candidate) => candidate && textNode.nodeValue.includes(candidate));
    if (!match) continue;
    const mark = document.createElement("mark");
    mark.className = "citation-highlight";
    const start = textNode.nodeValue.indexOf(match);
    const range = document.createRange();
    range.setStart(textNode, start);
    range.setEnd(textNode, start + match.length);
    range.surroundContents(mark);
    const folded = mark.closest("details");
    if (folded) folded.open = true;
    const block = state.readingBlocks.findIndex((node) => node.contains(mark));
    focusReadingBlock(block, {scroll: false});
    mark.scrollIntoView({behavior: window.Douku.motionAllowed() ? "smooth" : "instant", block: "center"});
    window.setTimeout(() => mark.classList.add("settled"), 1800);
    return;
  }
  $("#article-content").scrollIntoView({behavior: window.Douku.motionAllowed() ? "smooth" : "instant", block: "start"});
}

function renderArticleError(error) {
  const root = $("#article-content");
  state.currentEntry = "";
  state.currentArticleItem = null;
  clearReader(); hideLegacyViews(); window.Douku.setPage("article"); renderArticleToolbar(null);
  $("#reader-toc").replaceChildren(window.Douku.node("p", "无法加载目录", "hint-copy"));
  $("#library-view").classList.add("hidden");
  $("#article-view").classList.remove("hidden");
  const empty = document.createElement("section");
  empty.className = "empty-state error-state";
  empty.append(svgIcon("alert-circle"));
  const title = document.createElement("h2");
  title.textContent = "这篇资料无法打开";
  const copy = document.createElement("p");
  copy.textContent = error.message || "文章不存在或已经移动。";
  const back = document.createElement("button");
  back.type = "button";
  back.className = "secondary-button";
  back.textContent = "返回资料库";
  back.addEventListener("click", () => showLibrary());
  empty.append(title, copy, back);
  root.replaceChildren(empty);
  showArticleFallbackEntry();
}

function hideCardOverlays(source) {
  const card = source.closest(".gallery-card");
  if (!card) return () => {};
  const hidden = [];
  card.querySelectorAll(".topic-selection-indicator").forEach((node) => {
    hidden.push(node);
    node.style.visibility = "hidden";
  });
  return () => {
    hidden.forEach((node) => { node.style.visibility = ""; });
  };
}

async function openArticle(entryId, push = true, transitionSource = null, citation = null) {
  const animateNavigation = push && window.Douku.motionAllowed();
  beginNavigation();
  const root = $("#article-content");
  if (state.panelPage === "library") state.libraryScrollTop = $("#main-content").scrollTop;
  const preserveReading = !push && state.panelPage === "article" && state.currentEntry === entryId && state.currentArticleItem;
  const readingTop = preserveReading ? $("#main-content").scrollTop : 0;
  const readingIndex = preserveReading ? state.readingIndex : -1;
  const readingBlock = state.readingBlocks[readingIndex];
  const readingBookmark = readingBlock ? {text: readingBlock.textContent, tag: readingBlock.tagName} : null;
  const requestId = ++state.readerRequestId;
  state.pendingArticleRequest = requestId;
  closeEntryMenu();
  const sourceIsUsable = transitionSource?.isConnected && !$("#library-view").classList.contains("hidden");
  const sourceLink = transitionSource?.closest("a");
  if (sourceLink) sourceLink.setAttribute("aria-busy", "true");
  if (!sourceIsUsable && !preserveReading) {
    clearReader(); state.currentEntry = entryId; state.currentArticleItem = null; state.currentTopic = "";
    const loading = window.Douku.node("div", null, "reader-loading");
    loading.setAttribute("role", "status"); loading.setAttribute("aria-label", "正在加载文章");
    ["reader-skeleton-title", "reader-skeleton-byline", "reader-skeleton-byline", ...Array(8).fill("reader-skeleton-body")]
      .forEach((className) => loading.append(window.Douku.node("div", null, className)));
    root.replaceChildren(loading); hideLegacyViews(); $("#article-view").classList.remove("hidden");
    window.Douku.setPage("article"); renderArticleToolbar(null);
    $("#reader-toc").replaceChildren(window.Douku.node("p", "正在加载目录…", "hint-copy"));
  }
  try {
    const data = await api(`/api/articles/${encodeURIComponent(entryId)}`);
    if (requestId !== state.readerRequestId) return;
    const canTransition = animateNavigation && window.Douku.motionAllowed() && sourceIsUsable
      && typeof document.startViewTransition === "function";
    if (canTransition) {
      transitionSource.style.viewTransitionName = "active-album-cover";
      const restoreOverlays = hideCardOverlays(transitionSource);
      let transitionTarget = null;
      let rendered = false;
      const cleanTransitionNames = () => {
        transitionSource.style.viewTransitionName = "";
        if (transitionTarget) transitionTarget.style.viewTransitionName = "";
        restoreOverlays();
      };
      try {
        const transition = document.startViewTransition(() => {
          if (requestId !== state.readerRequestId) return;
          const hero = renderArticleData(data, entryId, push);
          rendered = true;
          transitionTarget = hero.querySelector(".article-cover") || hero;
          transitionTarget.style.viewTransitionName = "active-album-cover";
        });
        transition.finished.then(cleanTransitionNames, cleanTransitionNames);
        await transition.updateCallbackDone;
      } catch (_error) {
        cleanTransitionNames();
        if (!rendered) renderArticleData(data, entryId, push);
      }
    } else {
      renderArticleData(data, entryId, push);
    }
    if (preserveReading) {
      $("#main-content").scrollTop = readingTop;
      const index = readingBookmark ? state.readingBlocks.findIndex((block) => block.tagName === readingBookmark.tag && block.textContent === readingBookmark.text) : -1;
      focusReadingBlock(index >= 0 ? index : Math.min(readingIndex, state.readingBlocks.length - 1), {scroll: false}); updateReadingMetrics();
    }
    if (citation) window.setTimeout(() => { if (state.currentEntry === entryId) focusCitation(citation); }, 50);
  } catch (error) {
    if (requestId === state.readerRequestId) {
      if (error.status === 404) window.Douku.showMissing();
      else renderArticleError(error);
    }
  } finally {
    if (state.pendingArticleRequest === requestId) state.pendingArticleRequest = 0;
    sourceLink?.removeAttribute("aria-busy");
  }
}

function positionFilterPopover() {
  const toggle = $("#filter-toggle");
  const popover = $("#filter-popover");
  if (!toggle || !popover) return;
  const rect = toggle.getBoundingClientRect();
  const viewportPadding = 16;
  const gap = 4;
  const width = Math.min(320, Math.max(0, window.innerWidth - viewportPadding * 2));
  const left = Math.max(
    viewportPadding,
    Math.min(rect.right - width, window.innerWidth - width - viewportPadding),
  );
  popover.style.setProperty("--filter-popover-top", `${rect.bottom + gap}px`);
  popover.style.setProperty("--filter-popover-left", `${left}px`);
  popover.style.setProperty("--popover-origin", `${Math.max(0, Math.min(width, rect.left + rect.width / 2 - left))}px top`);
}

function openFilterPopover() {
  if (state.panelPage !== "library") showLibrary();
  positionFilterPopover();
  const popover = $("#filter-popover");
  popover.inert = false; popover.removeAttribute("aria-hidden"); popover.classList.remove("hidden");
  $("#filter-toggle").setAttribute("aria-expanded", "true");
  popover.focus({preventScroll: true});
}

function closeFilterPopover() {
  const popover = $("#filter-popover");
  const restoreFocus = popover.contains(document.activeElement);
  popover.inert = true; popover.setAttribute("aria-hidden", "true"); popover.classList.add("hidden");
  $("#filter-toggle").setAttribute("aria-expanded", "false");
  if (restoreFocus) $("#filter-toggle").focus({preventScroll: true});
}

const PANEL_TABS = JSON.parse($("#panel-tabs").dataset.panelConfig);
const PANEL_PANES = {info: "info-pane", inspiration: "inspiration-pane", sources: "topic-sources-pane"};
const PANEL_LABELS = {info: "信息", inspiration: "灵感", sources: "来源"};

function panelBreakpoint() {
  return window.innerWidth >= 1280 ? "desktop" : "overlay";
}

function isPanelOpen() {
  return state.panelOverride ?? (panelBreakpoint() === "desktop" && Boolean(PANEL_TABS[state.panelPage]) && !state.chatCollapsed);
}

function moveHeaderControls(page) {
  const header = $(`#${page}-view > .view-header`);
  if (!header) return;
  $(".view-header-left", header).append($("#sidebar-restore"));
  $(".view-header-right", header).append($("#chat-toggle"));
}

function renderPanelTabs(page) {
  const readingFonts = $("#reading-fonts");
  if (page === "article" && !readingFonts) {
    const link = document.createElement("link");
    link.id = "reading-fonts"; link.rel = "stylesheet";
    link.href = $('link[href^="/static/app.css"]').getAttribute("href").replace("app.css", "reading.css");
    document.head.append(link);
  } else if (page !== "article") readingFonts?.remove();
  if (page !== "article") $("#reader-back")?.remove();
  $("#reader-toc").classList.toggle("hidden", page !== "article");
  if (page !== "article") { state.readerRequestId += 1; state.pendingArticleRequest = 0; clearReader(); }
  if (state.panelPage !== page) {
    state.panelOverride = null;
    state.panelPage = page;
  }
  moveHeaderControls(page);
  const tabs = PANEL_TABS[page] || ["info"];
  const preferred = readStored(`douyin-wiki.panel-tab.${page}`, tabs[0]);
  const root = $("#panel-tabs");
  root.replaceChildren(...tabs.map((tab) => {
    const button = document.createElement("button");
    button.type = "button";
    button.id = `panel-tab-${tab}`;
    button.className = "panel-tab";
    button.dataset.panelTab = tab;
    button.textContent = PANEL_LABELS[tab];
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", PANEL_PANES[tab]);
    button.addEventListener("click", () => selectPanelTab(tab));
    button.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const index = tabs.indexOf(tab);
      const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs.at(-1)
        : tabs[(index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
      selectPanelTab(next);
      $(`#panel-tab-${next}`).focus();
    });
    return button;
  }));
  selectPanelTab(tabs.includes(preferred) ? preferred : tabs[0], false);
  renderPanelSummary();
  renderPanelState();
  requestAnimationFrame(() => {
    if (document.activeElement?.closest(".hidden, [hidden], [inert]")) {
      $("#main-content").focus({preventScroll: true});
    }
  });
}

function selectPanelTab(tab, remember = true) {
  if (!(PANEL_TABS[state.panelPage] || ["info"]).includes(tab)) return;
  const previouslyFocusedPane = document.activeElement?.closest(".panel-pane");
  state.panelTab = tab;
  $$("#panel-tabs [role='tab']").forEach((button) => {
    const selected = button.dataset.panelTab === tab;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  Object.entries(PANEL_PANES).forEach(([name, id]) => {
    const pane = $(`#${id}`);
    pane.hidden = name !== tab;
    pane.inert = name !== tab;
    if (name === tab) pane.setAttribute("aria-labelledby", `panel-tab-${name}`);
  });
  if (remember) writePreference(`douyin-wiki.panel-tab.${state.panelPage}`, tab);
  if (previouslyFocusedPane?.hidden) $(`#panel-tab-${tab}`).focus({preventScroll: true});
}

function renderInfoPanel(item, {context = "library"} = {}) {
  const info = $("#info-pane");
  info.dataset.entryId = item?.entry_id || "";
  if (!item) {
    const items = state.allItems.length ? state.allItems : state.items;
    const details = window.Douku.node("dl", null, "panel-summary library-statistics");
    [["资料总数", items.length], ["视频", items.filter((value) => value.source_kind !== "image_note").length],
      ["图文", items.filter((value) => value.source_kind === "image_note").length],
      ["最近 7 天", items.filter(isRecentItem).length], ["有灵感", items.filter((value) => value.inspirations?.length).length]]
      .forEach(([key, count]) => details.append(window.Douku.node("dt", key), window.Douku.node("dd", String(count))));
    info.replaceChildren(window.Douku.node("h2", "资料库概览"), details);
    return;
  }
  const title = window.Douku.node("h2", item.title, "info-title");
  const summary = window.Douku.node("p", item.summary || "暂无一句话摘要", "info-summary");
  const details = window.Douku.node("dl", null, "panel-summary");
  const addDetail = (key, value) => details.append(window.Douku.node("dt", key), typeof value === "string" ? window.Douku.node("dd", value) : value);
  const author = window.Douku.node("dd");
  const authorButton = window.Douku.node("button", item.author || "未知作者", "info-filter-link");
  authorButton.type = "button"; authorButton.disabled = !item.author;
  authorButton.addEventListener("click", () => { state.authors = [item.author]; showLibrary(false); renderFilters(); writeStateToURL("push"); });
  author.append(authorButton); addDetail("博主", author);
  addDetail("类型", `${item.content_type_label || "其他"} · ${sourceLabel(item)}`);
  addDetail("发布", item.published_display || formatDate(item.published_at));
  addDetail("采集", item.captured_display || formatDate(item.captured_at));
  const tags = window.Douku.node("dd", null, "info-tags");
  (item.tags || []).forEach((tag) => {
    const button = window.Douku.node("button", tag, "info-tag"); button.type = "button";
    button.addEventListener("click", () => { state.tags = [tag]; showLibrary(false); renderFilters(); writeStateToURL("push"); }); tags.append(button);
  });
  if (!tags.childNodes.length) tags.textContent = "—";
  addDetail("标签", tags);
  addDetail("媒体", isDatabaseManaged(item) ? mediaSentence(item) : "只读 Markdown，没有随库管理的媒体文件");
  if (context === "article" && item.source_kind === "video") {
    if (item.local_video_url) {
      const url = new URL(item.local_video_url, location.origin).href;
      const value = window.Douku.node("dd", null, "local-video-url");
      const link = window.Douku.node("a", url); link.href = url;
      link.target = "_blank"; link.rel = "noopener noreferrer";
      value.append(link); addDetail("本地视频", value);
    } else addDetail("本地视频", "文件不存在或已清理");
  }
  addDetail("状态", item.status || "—");
  const actions = window.Douku.node("div", null, "info-actions");
  if (context === "library") {
    const open = window.Douku.node("button", "打开阅读", "primary-button"); open.type = "button";
    open.addEventListener("click", () => { state.lastOpenedEntryId = item.entry_id; openArticle(item.entry_id); }); actions.append(open);
  }
  const favorite = context === "library" ? makeFavoriteButton(item, "info-favorite") : null;
  if (favorite) { favorite.append(document.createTextNode(item.favorite ? "取消收藏" : "库内收藏")); actions.append(favorite); }
  if (context === "library" && item.original_url && /^https?:\/\//.test(item.original_url)) {
    const original = window.Douku.node("a", "原作品", "text-button"); original.href = item.original_url; original.target = "_blank"; original.rel = "noopener noreferrer"; actions.append(original);
  }
  if (context === "article") {
    const file = window.Douku.node("button", item.source_path?.split("/").at(-1) || "—", "info-filter-link source-file");
    file.type = "button"; file.title = item.source_path || ""; file.disabled = !item.source_path;
    file.setAttribute("aria-label", "复制来源文件路径");
    file.addEventListener("click", () => copyText(item.source_path, "已复制来源文件路径"));
    const value = window.Douku.node("dd"); value.append(file); addDetail("来源文件", value);
    info.replaceChildren(window.Douku.node("h2", "文章信息"), summary, details);
    if (isDatabaseManaged(item)) {
      const analyze = window.Douku.node("button", "重新分析", "text-button"); analyze.type = "button";
      analyze.addEventListener("click", () => reanalyzeItem(item, analyze));
      const remove = window.Douku.node("button", "删除文章", "danger-text-button"); remove.type = "button";
      remove.addEventListener("click", () => deleteLibraryItem(item)); actions.append(analyze, remove);
    }
  } else info.replaceChildren(makeCover(item, "info-cover"), title, summary, details);
  if (context === "library" && item.inspirations?.length) {
    const inspirations = window.Douku.node("section", null, "info-inspirations");
    inspirations.append(window.Douku.node("h3", `灵感 ${item.inspirations.length}`));
    item.inspirations.forEach((value) => inspirations.append(window.Douku.node("p", value.text))); info.append(inspirations);
  }
  info.append(actions);
}

function formatLocation(location) {
  if (location.image_index) return `第 ${location.image_index} 张图片`;
  if (location.timestamp_ms != null) {
    const seconds = Math.floor(location.timestamp_ms / 1000);
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }
  return "文章片段";
}

function renderInspirationsPanel(item) {
  const pane = $("#inspiration-pane");
  const nodes = [];
  if (isDatabaseManaged(item)) {
    const add = window.Douku.node("button", "添加灵感", "secondary-button add-inspiration");
    add.type = "button"; add.prepend(svgIcon("plus")); add.title = "添加灵感（Shift N）";
    add.addEventListener("click", () => openInspiration("", add)); nodes.push(add);
  } else nodes.push(window.Douku.node("p", "只读 Markdown 文章不能保存灵感", "hint-copy"));
  const inspirations = item.inspirations || [];
  inspirations.forEach((value) => {
    const card = window.Douku.node("article", null, "inspiration-card");
    card.append(window.Douku.node("p", value.text, "inspiration-text"));
    if (value.quote) card.append(window.Douku.node("p", `对应原句：${value.quote}`, "inspiration-quote"));
    if (value.start_ms != null) {
      const range = formatLocation({timestamp_ms: value.start_ms})
        + (value.end_ms != null ? `–${formatLocation({timestamp_ms: value.end_ms})}` : "");
      const jump = window.Douku.node("button", `${range} → 跳到正文`, "inspiration-jump");
      jump.type = "button";
      jump.addEventListener("click", () => { focusCitation({timestamp_ms: value.start_ms}); $("#main-content").focus({preventScroll: true}); });
      card.append(jump);
    } else if (value.quote) {
      const jump = window.Douku.node("button", "跳到正文", "inspiration-jump"); jump.type = "button";
      jump.addEventListener("click", () => { focusCitation({snippet: value.quote}); $("#main-content").focus({preventScroll: true}); });
      card.append(jump);
    }
    nodes.push(card);
  });
  if (!inspirations.length) nodes.push(window.Douku.emptyState("还没有灵感", "读到有用的地方，按 ⇧N 记下来。", null, null, "quote"));
  pane.replaceChildren(...nodes);
}

function renderPanelSummary() {
  if (state.panelPage === "tags") { window.Douku.tags.renderPanel(); return; }
  const item = state.panelPage === "article" ? state.currentArticleItem
    : state.panelPage === "library" ? findLibraryItem(state.focusedEntryId) : null;
  if (state.panelPage === "article" && !item) {
    $("#info-pane").replaceChildren(window.Douku.node("p", "暂无文章信息", "hint-copy"));
    $("#inspiration-pane").replaceChildren();
  } else renderInfoPanel(item, {context: state.panelPage});
  if (state.panelPage === "article" && item) renderInspirationsPanel(item);
  if (state.panelPage !== "topic") $("#topic-sources-pane").replaceChildren();
}

async function copyText(value, successMessage) {
  const focused = document.activeElement;
  try {
    if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
    else {
      const field = document.createElement("textarea");
      field.value = value; field.style.position = "fixed"; field.style.opacity = "0";
      document.body.append(field); field.select();
      try { if (!document.execCommand("copy")) throw new Error("复制失败"); }
      finally { field.remove(); focused?.focus({preventScroll: true}); }
    }
    toast(successMessage);
  } catch (_error) { toast("复制失败，请重试"); }
}

function renderPanelState() {
  const shell = $("#app-shell");
  const mode = panelBreakpoint();
  if (state.breakpoint && state.breakpoint !== mode) state.panelOverride = null;
  state.breakpoint = mode;
  const sidebarOpen = !state.sidebarCollapsed;
  const panelOpen = isPanelOpen();
  shell.classList.toggle("is-sidebar-collapsed", !sidebarOpen);
  shell.classList.toggle("is-panel-collapsed", !panelOpen);
  shell.classList.toggle("panel-overlay-open", mode === "overlay" && panelOpen);
  $("#library-sidebar").inert = !sidebarOpen;
  $("#side-panel").inert = !panelOpen;
  $("#chat-toggle").setAttribute("aria-expanded", String(panelOpen));
  $("#chat-toggle").setAttribute("aria-label", panelOpen ? "收起右侧面板" : "打开右侧面板");
  if (!$("#filter-popover").classList.contains("hidden")) positionFilterPopover();
}

function setSidebarOpen(open) {
  state.sidebarCollapsed = !open;
  writePreference(STORAGE.sidebar, state.sidebarCollapsed);
  renderPanelState();
  if (!open && $("#library-sidebar").contains(document.activeElement)) $("#sidebar-restore").focus();
}

function setChatPanelOpen(open) {
  state.chatCollapsed = !open;
  writePreference(STORAGE.chat, state.chatCollapsed);
  state.panelOverride = open;
  const focusInside = $("#side-panel").contains(document.activeElement);
  renderPanelState();
  if (!open && focusInside) $("#chat-toggle").focus({preventScroll: true});
  if (open && panelBreakpoint() === "overlay") $(`#panel-tab-${state.panelTab}`).focus({preventScroll: true});
}

function focusableElements(root) {
  return $$("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])", root)
    .filter((node) => !node.closest(".hidden, [hidden], [inert]") && node.tabIndex >= 0 && node.getClientRects().length && getComputedStyle(node).visibility !== "hidden");
}

window.Douku.panel = {setTabs: renderPanelTabs, select: selectPanelTab, open: () => setChatPanelOpen(true), close: () => setChatPanelOpen(false)};

function commandQuery() {
  return $("#command-input").value.trim().replace(/^>/, "").trim().toLocaleLowerCase("zh-CN");
}

function fuzzyIncludes(text, query) {
  const hay = String(text || "").toLocaleLowerCase("zh-CN");
  if (!query) return true;
  if (hay.includes(query)) return true;
  let index = 0;
  for (const char of hay) {
    if (char === query[index]) index += 1;
    if (index === query.length) return true;
  }
  return false;
}

function commandCatalog() {
  return [
    ["imports", "查看导入记录", "list", "", () => window.Douku.navigate("/imports")],
    ["jobs", "打开任务中心", "list-checks", "g j", () => window.Douku.navigate("/jobs")],
    ["tags", "打开标签", "hash", "g g", () => window.Douku.navigate("/tags")],
    ["topics", "打开专题", "layers", "g t", () => window.Douku.navigate("/topics")],
    ["trash", "打开废纸篓", "trash-2", "g x", () => window.Douku.navigate("/trash")],
    ["settings", "打开设置", "settings", "g s", () => window.Douku.navigate("/settings")],
    ["theme", "切换主题", "sun", "", () => window.DoukuTheme.set(document.documentElement.dataset.theme === "dark" ? "light" : "dark")],
    ["shortcuts", "显示快捷键", "keyboard", "?", () => $("#shortcuts-dialog").showModal()],
  ].map(([commandId, title, icon, shortcut, run]) => ({commandId, title, icon, shortcut, run}));
}

function commandMatches() {
  const query = commandQuery();
  const commands = commandCatalog().filter((command) => fuzzyIncludes(command.title, query));
  const articles = query ? state.commandArticles : [];
  return [...commands, ...articles];
}

function scheduleCommandSearch() {
  const query = commandQuery();
  window.clearTimeout(state.commandTimer);
  const token = ++state.commandSearch;
  state.commandArticles = [];
  if (!query) {
    state.commandArticles = [];
    renderCommandResults();
    return;
  }
  renderCommandResults();
  state.commandTimer = window.setTimeout(async () => {
    try {
      const data = await api(`/api/library?q=${encodeURIComponent(query)}&limit=8&fuzzy=true`);
      if (token !== state.commandSearch || commandQuery() !== query) return;
      state.commandArticles = data.items || [];
    } catch (_error) {
      if (token !== state.commandSearch) return;
      state.commandArticles = [];
    }
    renderCommandResults();
  }, 180);
}

function syncCommandActiveState(root) {
  $$(".command-result", root).forEach((button, index) => {
    const active = index === state.commandIndex;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

function renderCommandResults() {
  const root = $("#command-results");
  const items = commandMatches();
  state.commandIndex = Math.max(0, Math.min(state.commandIndex, items.length - 1));
  const existing = $$(".command-result", root);
  const sameList = existing.length === items.length
    && items.every((item, index) => (item.commandId ? existing[index]?.dataset.commandId === item.commandId : existing[index]?.dataset.entryId === item.entry_id));
  if (sameList) {
    syncCommandActiveState(root);
    return;
  }
  root.replaceChildren(...items.map((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `command-result${index === state.commandIndex ? " active" : ""}`;
    if (item.commandId) button.dataset.commandId = item.commandId;
    else button.dataset.entryId = item.entry_id;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(index === state.commandIndex));
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = item.title;
    const meta = document.createElement("span");
    meta.textContent = item.commandId ? "" : `${item.author} · ${item.content_type_label}`;
    copy.append(title, meta);
    if (item.commandId) {
      const icon = window.Douku.node("span", null, "command-icon"); icon.append(svgIcon(item.icon));
      button.append(icon, copy);
      if (item.shortcut) {
        const keys = item.shortcut.toUpperCase().split(" ");
        const shortcut = window.Douku.node("kbd", keys.join(" → "));
        if (keys.length > 1) {
          const help = `输入框外先按 ${keys[0]}，再按 ${keys[1]}，需在 1 秒内完成`;
          shortcut.title = help;
          button.setAttribute("aria-description", help);
        }
        button.append(shortcut);
      }
    } else button.append(makeCover(item, "item-thumbnail"), copy);
    button.addEventListener("mouseenter", () => {
      if (state.commandIndex === index) return;
      state.commandIndex = index;
      renderCommandResults();
    });
    button.addEventListener("click", () => {
      $("#command-dialog").close();
      if (item.commandId) Promise.resolve(item.run()).catch((error) => toast(error.message));
      else openArticle(item.entry_id);
    });
    return button;
  }));
  if (items.some((item) => item.commandId)) root.prepend(window.Douku.node("p", "命令", "command-group-title"));
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "command-help";
    empty.textContent = "无匹配";
    root.append(empty);
  }
}

async function openCommandMenu() {
  resetNavigationPrefix();
  await ensureCommandItems();
  state.commandIndex = 0;
  $("#command-input").value = state.query;
  scheduleCommandSearch();
  $("#command-dialog").showModal();
  $("#command-input").focus();
}

function closeCommandMenu() {
  if ($("#command-dialog").open) $("#command-dialog").close();
}

function fillInspirationTargets() {
  const items = (state.allItems.length ? state.allItems : state.items).filter(isDatabaseManaged);
  $("#inspiration-entry").replaceChildren(...items.map((item) => {
    const option = document.createElement("option");
    option.value = item.entry_id;
    option.textContent = item.title;
    return option;
  }));
}

function openInspiration(text, trigger) {
  if (state.currentEntry && !isDatabaseManaged(currentEntryItem())) {
    toast("只读 Markdown 文章不能保存灵感");
    return;
  }
  const items = (state.allItems.length ? state.allItems : state.items).filter(isDatabaseManaged);
  if (!items.length) {
    toast("资料库中还没有可保存的文章");
    return;
  }
  state.lastDialogTrigger = trigger || document.activeElement;
  $("#inspiration-text").value = text;
  $("#inspiration-quote").value = "";
  $("#inspiration-start").value = "";
  $("#inspiration-end").value = "";
  $("#inspiration-error").classList.add("hidden");
  $("#inspiration-entry").value = state.currentEntry || items[0].entry_id;
  $("#inspiration-dialog").showModal();
  window.setTimeout(() => $("#inspiration-entry").focus(), 0);
}

async function saveInspiration() {
  const errorNode = $("#inspiration-error");
  errorNode.classList.add("hidden");
  let startMs = null;
  let endMs = null;
  try {
    startMs = window.Douku.parseClockToMs($("#inspiration-start").value);
    endMs = window.Douku.parseClockToMs($("#inspiration-end").value);
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.classList.remove("hidden");
    return;
  }
  const payload = {
    entry_id: $("#inspiration-entry").value,
    text: $("#inspiration-text").value,
    quote: $("#inspiration-quote").value || null,
    start_ms: startMs,
    end_ms: endMs,
    confirmed: true,
  };
  try {
    await api("/api/inspirations/confirm", {method: "POST", body: JSON.stringify(payload)});
    $("#inspiration-dialog").close();
    toast("灵感已逐字保存");
    await loadLibrary({showLoading: false});
    if (state.currentEntry === payload.entry_id) {
      await openArticle(payload.entry_id, false);
      if (state.panelTab === "inspiration") $("#inspiration-pane .add-inspiration")?.focus({preventScroll: true});
    }
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.classList.remove("hidden");
  }
}

function resetNavigationPrefix() {
  window.clearTimeout(state.navigationPrefixTimer);
  state.navigationPrefixTimer = 0;
  state.navigationPrefixAt = 0;
  window.Douku.chordPending = false;
}

function handleGlobalKeydown(event) {
  if (event.defaultPrevented || event.isComposing) return;
  const key = event.key.toLowerCase();
  const editing = Boolean(event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])"));
  const modal = $("dialog[open]");
  if (modal) { resetNavigationPrefix(); return; }
  if ((event.metaKey || event.ctrlKey) && key === "k") {
    event.preventDefault(); openCommandMenu(); return;
  }
  if (event.key === "Escape") {
    resetNavigationPrefix();
    if (!$("#entry-menu").classList.contains("hidden")) { closeEntryMenu(true); }
    else if (!$("#filter-popover").classList.contains("hidden")) { closeFilterPopover(); $("#filter-toggle").focus(); }
    else if (panelBreakpoint() === "overlay" && isPanelOpen()) setChatPanelOpen(false);
    else if (state.pendingArticleRequest) showLibrary(false);
    else if (state.panelPage === "article") showLibrary();
    return;
  }
  if (editing || event.metaKey || event.ctrlKey || event.altKey) {
    resetNavigationPrefix();
    return;
  }
  if (key === "g" && event.repeat) return;
  if (state.navigationPrefixAt && Date.now() - state.navigationPrefixAt < 1000) {
    resetNavigationPrefix();
    const section = {l: "all", r: "recent", f: "favorite", i: "inspiration"}[key];
    const path = {t: "/topics", j: "/jobs", x: "/trash", s: "/settings", g: "/tags"}[key];
    if (section) { event.preventDefault(); $(`.nav-item[data-section='${section}']`).click(); return; }
    if (path) { event.preventDefault(); window.Douku.navigate(path); return; }
  }
  if (key === "g") {
    resetNavigationPrefix();
    state.navigationPrefixAt = Date.now();
    state.navigationPrefixTimer = window.setTimeout(resetNavigationPrefix, 1000);
    window.Douku.chordPending = true;
    return;
  }
  resetNavigationPrefix();
  if (state.panelPage === "article" && !event.target.closest("#entry-menu,#filter-popover")) {
    const interactive = Boolean(event.target.closest("button,a,[role='tab'],#library-sidebar"));
    if (key === "u" && !interactive) { event.preventDefault(); showLibrary(); return; }
    if (state.currentArticleItem) {
      if (event.shiftKey && ["i", "n"].includes(key)) {
        event.preventDefault();
        const tab = {i: "info", n: "inspiration"}[key];
        selectPanelTab(tab); setChatPanelOpen(true);
        if (key === "n") openInspiration("", $("#inspiration-pane .add-inspiration") || document.activeElement);
        else $("#panel-tab-info").focus();
        return;
      }
      if (!interactive && !event.target.closest("#side-panel")) {
        if (key === "j" || event.key === "ArrowDown") { event.preventDefault(); moveReadingFocus(1); return; }
        if (key === "k" || event.key === "ArrowUp") { event.preventDefault(); moveReadingFocus(-1); return; }
        if (!event.shiftKey && ["n", "p"].includes(key)) { event.preventDefault(); openAdjacentArticle(key === "n" ? 1 : -1); return; }
        if (key === "f" && !event.shiftKey) {
          event.preventDefault();
          const favorite = $("#reader-actions .article-favorite-button");
          if (favorite && !favorite.disabled) favorite.click();
          else if (!favorite) toast("只读 Markdown 文章不能标为库内收藏");
          return;
        }
        if (key === "o" && event.shiftKey) { event.preventDefault(); $("#reader-actions .reader-original")?.click(); return; }
      }
    }
  }
  if (state.panelPage === "library"
      && !event.target.closest("#side-panel, #library-sidebar, #filter-popover, #entry-menu")) {
    const items = $("#library-results").classList.contains("hidden") ? [] : filteredItems();
    if (key === "v") { event.preventDefault(); $(state.libraryView === "list" ? "#view-gallery" : "#view-list").click(); return; }
    if (key === "f" && event.shiftKey) { event.preventDefault(); openFilterPopover(); return; }
    if (items.length) {
      let delta = null;
      if (key === "j" || event.key === "ArrowDown") delta = 1;
      if (key === "k" || event.key === "ArrowUp") delta = -1;
      if (state.libraryView === "gallery" && event.key.startsWith("Arrow")) {
        const rows = $$("#library-results [data-entry-id]");
        const top = rows[0]?.getBoundingClientRect().top;
        const columns = rows.filter((row) => Math.abs(row.getBoundingClientRect().top - top) < 1).length || 1;
        delta = {ArrowDown: columns, ArrowUp: -columns, ArrowLeft: -1, ArrowRight: 1}[event.key];
      }
      if (delta !== null && delta !== undefined) { event.preventDefault(); moveFocus(delta); return; }
      if (["Home", "End"].includes(event.key)) { event.preventDefault(); focusEntry((event.key === "Home" ? items[0] : items.at(-1)).entry_id); return; }
      if (key === "o" || (event.key === "Enter" && event.target.matches("[data-entry-action='open'], #main-content, body"))) { event.preventDefault(); openFocused(); return; }
      if (key === "f") {
        event.preventDefault();
        const item = findLibraryItem(state.focusedEntryId);
        if (isDatabaseManaged(item)) {
          const row = $$("#library-results [data-entry-id]").find((value) => value.dataset.entryId === item.entry_id);
          const button = $("[data-entry-action='favorite']", row) || makeFavoriteButton(item); if (!button.disabled) button.click();
        } else toast("只读 Markdown 文章不能标为库内收藏");
        return;
      }
      if (key === "x") {
        event.preventDefault();
        if (!isDatabaseManaged(findLibraryItem(state.focusedEntryId))) { toast("只读 Markdown 文章不能加入专题"); return; }
        state.topicSelectionMode = true; toggleTopicEntry(state.focusedEntryId); return;
      }
    }
  }
  if (event.key === "/") {
    event.preventDefault();
    if (state.panelPage === "library") $("#search-input").focus();
    else if (state.panelPage === "tags") $("#tags-search").focus();
    else openCommandMenu();
  } else if (event.key === "[") { event.preventDefault(); setSidebarOpen(state.sidebarCollapsed); }
  else if (event.key === "]") { event.preventDefault(); setChatPanelOpen(!isPanelOpen()); }
  else if (event.key === "?") { event.preventDefault(); $("#shortcuts-dialog").showModal(); }
}

function authSummaryLabel(value) {
  if (value?.pending) return "正在检查…";
  const ready = Object.values(value?.channels || {}).filter((channel) => ["authorized", "available"].includes(channel.user_state)).length;
  return `已授权 ${ready}/2`;
}

async function loadSettingsSummary() {
  ["auth", "analysis", "system"].forEach((id) => { $(`#settings-value-${id}`).textContent = "—"; });
  const values = await Promise.allSettled([api("/api/auth/status"), api("/api/system/analysis"), api("/api/system/health")]);
  values.forEach((result, index) => {
    if (result.status !== "fulfilled") return;
    const value = result.value; const id = ["auth", "analysis", "system"][index];
    const label = id === "auth" ? authSummaryLabel(value)
      : id === "analysis" ? value.label || value.mode_label || value.analysis_mode_label || value.mode
      : value.worker?.running ? "运行中" : "未运行";
    $(`#settings-value-${id}`).textContent = label || "—";
  });
  const auth = values[0].status === "fulfilled" ? values[0].value : null;
  if (auth?.pending || auth?.cached) {
    api("/api/auth/status?refresh=true").then((value) => {
      $("#settings-value-auth").textContent = authSummaryLabel(value);
    }).catch(() => {});
  }
}

async function syncImportDestinations() {
  const list = $(".destination-list", $("#imports-view"));
  if (!list) return;
  const [creators, favorites, collections, jobs] = await Promise.allSettled([
    api("/api/creators"),
    api("/api/favorites/imports"),
    api("/api/collection-imports"),
    api("/api/jobs?kind=creator_import&limit=1"),
  ]);
  const rowsOf = (result) => {
    if (result.status !== "fulfilled") return [];
    const value = result.value;
    return Array.isArray(value) ? value : (value?.items || []);
  };
  const flags = {
    creators: rowsOf(creators).length > 0 || rowsOf(jobs).length > 0 || Number(jobs.status === "fulfilled" ? jobs.value?.total || 0 : 0) > 0,
    favorites: rowsOf(favorites).length > 0,
    collections: rowsOf(collections).length > 0,
  };
  $$("[data-import-kind]", list).forEach((row) => {
    row.classList.toggle("hidden", !flags[row.dataset.importKind]);
  });
  list.classList.toggle("hidden", !Object.values(flags).some(Boolean));
}

async function loadRecentImports() {
  const root = $("#recent-imports-list");
  syncImportDestinations();
  try {
    const data = await api("/api/jobs");
    const jobs = (data.items || []).filter((job) => ["capture", "creator_import", "favorites_import", "collection_import"].includes(job.kind)).slice(0, 5);
    root.replaceChildren(...jobs.map((job) => {
      const link = window.Douku.node("a", null, "recent-import-row"); link.href = `/jobs/${encodeURIComponent(job.id)}`; link.dataset.route = link.href.replace(location.origin, "");
      link.append(window.Douku.node("strong", job.display_title || job.kind_label), window.Douku.node("span", job.state_label), window.Douku.node("time", String(job.updated_display || "").replace(/（北京时间）/g, ""))); return link;
    }));
    if (!jobs.length) root.append(window.Douku.node("p", "还没有导入记录", "hint-copy"));
  } catch (_error) { root.replaceChildren(window.Douku.node("p", "最近导入暂时无法载入", "hint-copy")); }
}

function bindEvents() {
  const filters = $("#jobs-view .favorites-controls");
  const header = $("#jobs-view .view-header");
  if (filters && header) header.insertBefore(filters, $("#jobs-view .view-header-right"));
  $$(".nav-item[data-section]").forEach((node) => node.addEventListener("click", () => {
    state.section = node.dataset.section;
    state.authors = [];
    state.types = [];
    state.tags = [];
    state.sources = [];
    state.inspirationOnly = false;
    renderFilters();
    showLibrary(false);
    writeStateToURL("push");
  }));
  $("#sidebar-search").addEventListener("click", openCommandMenu);
  $("#library-selection-cancel").addEventListener("click", () => {
    state.topicSelectionMode = false; state.selectedEntryIds.clear(); renderLibrary();
  });
  $("#library-selection-next").addEventListener("click", openTopicDialog);
  window.addEventListener("douku:page", (event) => {
    renderPanelTabs(event.detail.page);
  });
  $("#topics-nav").addEventListener("click", () => showTopics());
  $("#trash-nav").addEventListener("click", () => showTrash());
  $("#trash-select-all").addEventListener("click", () => {
    setTrashSelection(state.trashItems.map((item) => item.trash_id));
  });
  $("#trash-clear-selection").addEventListener("click", () => setTrashSelection([]));
  $("#trash-restore-selected").addEventListener("click", () => openTrashBatchDialog("restore"));
  $("#trash-purge-selected").addEventListener("click", () => openTrashBatchDialog("purge"));
  const commandCopyFeedback = new WeakMap();
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-command]");
    if (!button) return;
    const command = button.dataset.copyCommand || "";
    if (!command) return;
    window.clearTimeout(commandCopyFeedback.get(button)?.timer);
    const feedback = {timer: 0};
    commandCopyFeedback.set(button, feedback);
    button.classList.remove("is-copied");
    try {
      await navigator.clipboard.writeText(command);
      if (commandCopyFeedback.get(button) === feedback) {
        button.classList.add("is-copied");
        feedback.timer = window.setTimeout(() => {
          if (commandCopyFeedback.get(button) !== feedback) return;
          button.classList.remove("is-copied");
          commandCopyFeedback.delete(button);
        }, 1500);
      }
      toast("已复制");
    } catch (_error) {
      if (commandCopyFeedback.get(button) === feedback) commandCopyFeedback.delete(button);
      toast("复制失败");
    }
  });
  $$("[data-route]").forEach((node) => node.addEventListener("click", (event) => {
    const path = node.getAttribute("data-route") || node.getAttribute("href");
    if (!path || !path.startsWith("/")) return;
    event.preventDefault();
    window.Douku?.navigate(path);
  }));
  $$("[data-back='library']").forEach((node) => node.addEventListener("click", () => showLibrary()));
  $("#back-jobs")?.addEventListener("click", () => window.Douku?.navigate("/jobs"));
  $("#topic-select-toggle").addEventListener("click", () => {
    if (!state.topicSelectionMode) {
      state.topicSelectionMode = true;
      state.selectedEntryIds.clear();
      if (state.currentEntry || state.currentTopic || !$("#topics-view").classList.contains("hidden")) showLibrary();
      renderLibrary();
    } else if (state.selectedEntryIds.size) {
      openTopicDialog();
    } else {
      state.topicSelectionMode = false;
      renderLibrary();
    }
  });
  $("#topics-create-button").addEventListener("click", () => {
    const hasManagedItems = [...state.items, ...state.allItems, state.currentArticleItem]
      .some(isDatabaseManaged);
    if (!hasManagedItems) return;
    resetLibraryFilters();
    state.section = "all";
    if (state.sort === "relevance") state.sort = "captured_desc";
    state.topicSelectionMode = true;
    state.selectedEntryIds.clear();
    // The cached results may still belong to a search or favorites batch.
    state.libraryLoaded = false;
    renderFilters();
    showLibrary();
  });
  $("#confirm-topic").addEventListener("click", (event) => {
    event.preventDefault();
    createTopicFromSelection();
  });
  $("#search-mode").addEventListener("change", event => {
    state.searchMode = event.target.value;
    if (state.searchMode === "semantic") state.sort = "relevance";
    else if (state.sort === "relevance") state.sort = "captured_desc";
    state.libraryRequestId += 1;
    renderToolbarState(); writeStateToURL(); loadLibrary({showLoading: false});
  });
  let searchTimer = 0;
  $("#search-input").addEventListener("input", (event) => {
    state.query = event.target.value.trim();
    state.libraryRequestId += 1;
    const navigationId = state.libraryNavigationId;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => {
      if (navigationId !== state.libraryNavigationId) return;
      writeStateToURL();
      loadLibrary({showLoading: false});
    }, 200);
  });
  $("#author-filter-search").addEventListener("input", renderFilters);
  $$("[data-source]").forEach((button) => button.addEventListener("click", () => {
    state.sources = button.dataset.source ? [button.dataset.source] : [];
    renderFilters(); renderLibrary(); writeStateToURL("push");
  }));
  $("#entry-menu").addEventListener("keydown", (event) => {
    if (event.key === "Tab") { closeEntryMenu(true); return; }
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const choices = focusableElements($("#entry-menu")); if (!choices.length) return;
    event.preventDefault();
    const current = choices.indexOf(document.activeElement);
    choices[event.key === "Home" ? 0 : event.key === "End" ? choices.length - 1
      : (current + (event.key === "ArrowDown" ? 1 : -1) + choices.length) % choices.length].focus();
  });
  $("#main-content").addEventListener("scroll", () => {
    if (!$("#entry-menu").classList.contains("hidden")) closeEntryMenu(true);
  }, {passive: true});
  $("#filter-toggle").addEventListener("click", () => {
    if ($("#filter-popover").classList.contains("hidden")) openFilterPopover();
    else closeFilterPopover();
  });
  $("#filter-close").addEventListener("click", closeFilterPopover);
  $("#apply-filters").addEventListener("click", closeFilterPopover);
  $("#clear-filters").addEventListener("click", () => {
    state.authors = []; state.types = []; state.tags = []; state.sources = []; state.inspirationOnly = false;
    renderFilters(); renderLibrary(); writeStateToURL();
  });
  $("#inspiration-only").addEventListener("change", (event) => {
    state.inspirationOnly = event.target.checked;
    renderFilters(); renderLibrary(); writeStateToURL();
  });
  $("#sort-select").addEventListener("change", (event) => {
    state.sort = VALID_SORTS.has(event.target.value) ? event.target.value : "captured_desc";
    renderLibrary(); writeStateToURL();
  });
  $$('[data-library-view]').forEach((node) => node.addEventListener("click", () => {
    state.libraryView = node.dataset.libraryView;
    writePreference(STORAGE.view, state.libraryView);
    renderToolbarState(); renderLibrary(); writeStateToURL();
  }));
  $("#empty-clear").addEventListener("click", clearSearchAndFilters);
  $("#library-retry").addEventListener("click", () => loadLibrary({showLoading: true}));
  $("#back-library").addEventListener("click", () => showLibrary());
  $("#reader-previous").addEventListener("click", () => openAdjacentArticle(-1));
  $("#reader-next").addEventListener("click", () => openAdjacentArticle(1));
  $("#back-topics").addEventListener("click", () => showTopics());
  $("#sidebar-collapse").addEventListener("click", () => setSidebarOpen(false));
  $("#sidebar-restore").addEventListener("click", () => setSidebarOpen(true));
  $("#chat-toggle").addEventListener("click", () => setChatPanelOpen(!isPanelOpen()));
  $("#chat-close").addEventListener("click", () => setChatPanelOpen(false));
  $("#confirm-inspiration").addEventListener("click", (event) => {
    event.preventDefault();
    if (!$("#inspiration-form").reportValidity()) return;
    saveInspiration();
  });
  $("#inspiration-dialog").addEventListener("close", () => state.lastDialogTrigger?.focus());
  $("#command-input").addEventListener("input", () => { state.commandIndex = 0; scheduleCommandSearch(); });
  $("#command-input").addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); $("#command-dialog").close(); return; }
    const length = commandMatches().length;
    if (event.key === "ArrowDown" && length) { event.preventDefault(); state.commandIndex = (state.commandIndex + 1) % length; renderCommandResults(); }
    if (event.key === "ArrowUp" && length) { event.preventDefault(); state.commandIndex = (state.commandIndex - 1 + length) % length; renderCommandResults(); }
    if (event.key === "Enter") { event.preventDefault(); if (length) $("#command-results .command-result.active")?.click(); }
  });
  window.addEventListener("douku:route", (event) => {
    beginNavigation();
    const path = event.detail.path.split("?")[0];
    if (path === "/") { readStateFromURL(); showLibrary(false); loadLibrary({showLoading: false}); return; }
    if (path === "/topics") { showTopics(false); return; }
    if (path === "/trash") { showTrash(false); return; }
    if (path === "/tags") { showTags(); return; }
    const entryMatch = path.match(/^\/articles\/([^/]+)$/);
    if (entryMatch) { openArticle(decodeURIComponent(entryMatch[1]), false); return; }
    const topicMatch = path.match(/^\/topics\/([^/]+)$/);
    if (topicMatch) { openTopic(decodeURIComponent(topicMatch[1]), false); return; }
    if (path === "/imports") {
      hideLegacyViews();
      window.Douku?.setPage("imports");
      window.Douku?.setNav("imports-nav");
      $("#imports-view").classList.remove("hidden");
      document.title = "导入内容 · Dou-wiki";
      loadRecentImports();
      refreshAuthChannelBanners();
      window.Douku?.warnIfWorkerDown("imports-view");
      return;
    }
    if (path === "/settings") {
      hideLegacyViews();
      window.Douku?.setPage("settings");
      window.Douku?.setNav("settings-nav");
      $("#settings-view")?.classList.remove("hidden");
      document.title = "设置 · Dou-wiki";
      loadSettingsSummary();
      return;
    }
    if (path === "/auth") {
      window.Douku?.navigate("/settings/auth");
      return;
    }
    if (!window.Douku?.isKnownAppPath(path) && !path.startsWith("/api/") && !path.startsWith("/static/") && !path.startsWith("/media/")) {
      hideLegacyViews();
      window.Douku?.hideAllViews?.();
      window.Douku?.setPage("not-found");
      $("#not-found-view")?.classList.remove("hidden");
      document.title = "页面不存在 · Dou-wiki";
    }
  });
  $$('[data-theme-select]').forEach((select) => select.addEventListener("change", (event) => window.DoukuTheme?.set(event.target.value)));
  document.addEventListener("keydown", handleGlobalKeydown);
  document.addEventListener("click", (event) => {
    if (panelBreakpoint() === "overlay" && isPanelOpen() && event.target.closest("#main-content") && !event.target.closest("#chat-toggle")) setChatPanelOpen(false);
    if (!event.target.closest("#entry-menu, .entry-more")) closeEntryMenu();
    if (!event.target.closest("#filter-popover, #filter-toggle")) closeFilterPopover();
  }, {capture: true});
  window.addEventListener("popstate", () => {
    beginNavigation();
    const path = window.location.pathname;
    const match = path.match(/^\/articles\/([^/]+)$/);
    const topicMatch = path.match(/^\/topics\/([^/]+)$/);
    if (match) openArticle(decodeURIComponent(match[1]), false);
    else if (topicMatch) openTopic(decodeURIComponent(topicMatch[1]), false);
    else if (path === "/topics") showTopics(false);
    else if (path === "/trash") showTrash(false);
    else if (path === "/tags") showTags();
    else if (window.Douku?.isOperationPath(path)) window.Douku.navigate(path + window.location.search, false);
    else if (window.Douku && !window.Douku.isKnownAppPath(path)) {
      window.Douku.hideAllViews();
      window.Douku.setPage("not-found");
      $("#not-found-view")?.classList.remove("hidden");
      document.title = "页面不存在 · Dou-wiki";
    }
    else {
      readStateFromURL();
      $("#search-input").value = state.query;
      showLibrary(false);
      loadLibrary({showLoading: false});
    }
  });
  window.addEventListener("resize", () => {
    renderPanelState();
    if (state.libraryLoaded) renderLibrary();
    if (!$("#filter-popover")?.classList.contains("hidden")) positionFilterPopover();
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  readPreferences();
  readStateFromURL();
  $("#search-input").value = state.query;
  bindEvents();
  const startupNavigationId = state.navigationRequestId;
  renderPanelTabs(state.currentEntry ? "article" : state.currentTopic ? "topic" : "library");
  renderToolbarState();
  if (document.body.dataset.missing === "1") {
    window.Douku.showMissing();
  } else {
  await loadLibrary();
  await loadTopics();
  if (startupNavigationId === state.navigationRequestId) {
    if (state.currentTopic) await openTopic(state.currentTopic, false);
    else if (state.currentEntry) await openArticle(state.currentEntry, false);
    else if (window.location.pathname === "/topics") await showTopics(false);
    else if (window.location.pathname === "/trash") await showTrash(false);
    else if (window.location.pathname === "/tags") showTags();
    else if (window.Douku?.isOperationPath(window.location.pathname)) {
      window.Douku.navigate(window.location.pathname + window.location.search, false);
    } else if (window.Douku && !window.Douku.isKnownAppPath(window.location.pathname)) {
      window.Douku.hideAllViews();
      window.Douku.setPage("not-found");
      $("#not-found-view")?.classList.remove("hidden");
      document.title = "页面不存在 · Dou-wiki";
    }
  }
  }
  refreshAuthChannelBanners();
  const events = new EventSource("/api/library/events");
  events.addEventListener("library", async (event) => {
    let origins = [null];
    try { origins = JSON.parse(event.data).origins || [null]; } catch (_error) {}
    state.allItems = [];
    state.allItemsLoaded = false;
    await loadLibrary({showLoading: false});
    await loadTopics();
    if (!$("#trash-view").classList.contains("hidden")) await loadTrash();
    if (state.panelPage === "tags") await window.Douku.tags.load();
    if (state.panelPage === "article" && state.currentEntry) await openArticle(state.currentEntry, false);
    if (state.panelPage === "topic" && state.currentTopic) await openTopic(state.currentTopic, false);
    if (origins.some(origin => origin !== window.Douku.clientId)) toast("资料库已更新");
  });
  events.addEventListener("error", () => {
    // Native EventSource reconnects automatically; browsing remains available.
  });
});
