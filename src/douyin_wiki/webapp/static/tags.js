(() => {
  "use strict";
  const D = window.Douku;
  const $ = D.$;
  const node = D.node;
  const state = {rows: [], duplicates: [], aliases: {}, undoByAlias: {}, undoableByAlias: {}, selected: new Set(), excludedDuplicates: new Set(), duplicateTargets: new Map(), view: "all", focus: "", sort: "count", direction: -1, busy: false, request: 0};
  const PIN_LIMIT = 8;
  const views = {all: "tags-all-panel", duplicates: "tag-duplicates", aliases: "tag-aliases"};
  const active = () => $("app-shell").dataset.page === "tags";
  const date = (value) => value ? new Date(value).toLocaleString("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : "—";
  const pinned = () => state.rows.filter(row => row.pinned_at).sort((a, b) => a.position - b.position || a.pinned_at.localeCompare(b.pinned_at));
  const button = (label, action, className = "text-button") => {
    const value = node("button", label, className);
    value.type = "button"; value.disabled = state.busy;
    value.addEventListener("click", action);
    return value;
  };
  const search = () => $("tags-search").value.trim().toLocaleLowerCase();
  const matches = (value) => value.toLocaleLowerCase().includes(search());
  const duplicateGroups = () => state.duplicates.map(groupNames).filter(names => names.length > 1);
  function emptyList(title, copy) {
    const empty = node("div", null, "tag-empty-state");
    empty.setAttribute("role", "status");
    empty.append(node("h2", title), node("p", copy));
    return empty;
  }
  function columnHeading(labels, className) {
    const heading = node("div", null, className);
    heading.setAttribute("aria-hidden", "true");
    labels.forEach(label => heading.append(node("span", label)));
    return heading;
  }
  function renderViews() {
    const counts = {all: state.rows.length, duplicates: duplicateGroups().length, aliases: Object.keys(state.aliases).length};
    Object.entries(views).forEach(([name, panel]) => {
      const selected = state.view === name;
      $(panel).classList.toggle("hidden", !selected);
      const tab = $("tags-tab-" + name);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.disabled = state.busy;
      $("tags-" + name + "-count").textContent = String(counts[name]);
    });
  }
  function selectView(name, focus = false) {
    if (!views[name] || state.busy) return;
    if (state.view !== name) {
      state.view = name;
      state.selected.clear();
      state.focus = "";
      render();
      $("main-content").scrollTop = 0;
    }
    if (focus) $("tags-tab-" + name).focus({preventScroll: true});
  }
  function visibleRows() {
    return state.rows.filter(row => matches(row.name)).sort((a, b) => {
      const compare = state.sort === "count" ? a.count - b.count
        : String(a[state.sort] || "").localeCompare(String(b[state.sort] || ""), "zh-CN");
      return state.direction * compare || a.name.localeCompare(b.name, "zh-CN");
    });
  }
  function applyOverview(data) {
    state.rows = data.tags || [];
    state.duplicates = data.duplicates || [];
    state.aliases = data.aliases || {};
    state.undoByAlias = data.undo_by_alias || {};
    state.undoableByAlias = data.undoable_by_alias || {};
    const groups = state.duplicates.map(groupNames).filter(names => names.length > 1);
    const names = new Set(groups.flat());
    state.excludedDuplicates = new Set([...state.excludedDuplicates].filter(name => names.has(name)));
    const keys = new Set(groups.map(duplicateKey));
    for (const key of state.duplicateTargets.keys()) {
      if (!keys.has(key)) state.duplicateTargets.delete(key);
    }
  }
  function groupNames(group) {
    return Array.isArray(group) ? group : (group?.names || []);
  }
  function duplicateKey(names) {
    return JSON.stringify([...names].sort());
  }
  function articleCount(names, group) {
    if (group && !Array.isArray(group) && Number.isFinite(Number(group.article_count))) {
      return Number(group.article_count);
    }
    const ids = new Set();
    names.forEach((name) => {
      const row = state.rows.find((item) => item.name === name);
      (row?.entry_ids || []).forEach((id) => ids.add(id));
    });
    if (ids.size) return ids.size;
    return names.reduce((sum, name) => sum + (state.rows.find((item) => item.name === name)?.count || 0), 0);
  }
  function actionMessage(action, payload) {
    if (action === "rename") return `已重命名为 ${payload.target}`;
    if (action === "merge") return `已合并到 ${payload.target}`;
    if (action === "delete") return "已删除标签";
    if (action === "pin") return payload.pinned ? "已固定" : "已取消固定";
    if (action === "reorder") return "已调整固定顺序";
    if (action === "split") return "已拆回独立标签";
    if (action === "undo") return "已恢复操作前的标签和资料关联";
    return "标签已更新";
  }
  async function change(action, payload, selection = null) {
    if (state.busy) return false;
    state.busy = true; render();
    try {
      const data = await D.api(`/api/tags/${action}`, {method: "POST", body: JSON.stringify(payload)});
      state.request += 1;
      applyOverview(data);
      if (action === "merge" || action === "delete") {
        state.selected = new Set();
        state.focus = "";
      } else if (selection) { state.selected = new Set(selection); state.focus = selection[0] || ""; }
      $("tags-error").classList.add("hidden");
      await D.refreshLibrary();
      const text = data.warnings?.length ? `${actionMessage(action, payload)}；${data.warnings.join("；")}` : actionMessage(action, payload);
      if (data.operation_id) {
        D.toastAction(text, "撤销", async () => {
          await D.api("/api/tags/undo", {method: "POST", body: JSON.stringify({operation_id: data.operation_id})});
          await load();
          await D.refreshLibrary();
          D.toast("已恢复操作前的标签和资料关联");
        });
      } else D.toast(text);
      return true;
    } catch (error) {
      $("tags-error").textContent = error.message;
      $("tags-error").classList.remove("hidden");
      return false;
    } finally { state.busy = false; render(); }
  }
  async function load() {
    const request = ++state.request;
    try {
      const data = await D.api("/api/tags");
      if (request !== state.request) return;
      applyOverview(data);
      $("tags-error").classList.add("hidden"); render();
    } catch (error) {
      if (request !== state.request) return;
      $("tags-error").textContent = error.message;
      $("tags-error").classList.remove("hidden");
    }
  }
  async function remove(names = [...state.selected]) {
    if (state.busy || !names.length) return;
    const confirmed = await D.confirmDanger({title: "删除标签", description: `删除 ${names.length} 个标签，只解除标签关联，资料和文件都会保留。`, confirmLabel: "删除标签"});
    if (confirmed) await change("delete", {names}, []);
  }
  function edit(mode) {
    if (state.busy) return;
    const names = [...state.selected];
    if (mode === "rename" && names.length !== 1 || mode === "merge" && names.length < 2) return;
    const dialog = $("tag-edit-dialog"); dialog.dataset.mode = mode;
    $("tag-edit-title").textContent = mode === "rename" ? "重命名标签" : "合并标签";
    const count = articleCount(names);
    $("tag-edit-description").textContent = mode === "rename" ? `重命名「${names[0]}」。旧名称会作为别名保留。`
      : `合并 ${names.length} 个标签，会影响 ${count} 篇文章。源标签会成为目标标签的别名，后续写入沿用目标名称。`;
    $("tag-name-label").classList.toggle("hidden", mode !== "rename");
    $("tag-target-label").classList.toggle("hidden", mode !== "merge");
    $("tag-new-name").required = mode === "rename";
    $("tag-new-name").value = names[0];
    const targets = [...names, ...state.rows.map(row => row.name).filter(name => !names.includes(name))];
    $("tag-target").replaceChildren(...targets.map(name => {
      const option = node("option", name); option.value = name; return option;
    }));
    $("tag-edit-error").classList.add("hidden");
    dialog.showModal();
    $(mode === "rename" ? "tag-new-name" : "tag-target").focus();
  }
  async function reorder(name, target) {
    const names = pinned().map(row => row.name);
    if (!names.includes(name) || !names.includes(target) || name === target) return;
    names.splice(names.indexOf(name), 1);
    names.splice(names.indexOf(target), 0, name);
    await change("reorder", {names});
  }
  async function move(name, delta) {
    const names = pinned().map(row => row.name);
    const index = names.indexOf(name);
    const other = index + delta;
    if (other < 0 || other >= names.length) return;
    [names[index], names[other]] = [names[other], names[index]];
    await change("reorder", {names});
  }
  function renderPanel() {
    if (!active()) return;
    const info = $("info-pane"); info.removeAttribute("data-entry-id");
    if (state.view !== "all") {
      const title = state.view === "duplicates" ? "可能重复" : "标签别名";
      const copy = state.view === "duplicates"
        ? "选择同一含义的标签，并指定合并后保留的名称。确认前会显示影响的文章数。"
        : "合并或重命名后，旧名称会归入当前标签。解除别名只影响今后的归类。";
      info.replaceChildren(node("h2", title), node("p", copy, "hint-copy"));
      return;
    }
    const names = [...state.selected];
    if (names.length > 1) {
      info.replaceChildren(node("h2", `已选 ${names.length} 个`),
        button("合并到…", () => edit("merge"), "secondary-button"),
        button("删除标签", () => remove(), "danger-text-button"));
      return;
    }
    const row = state.rows.find(row => row.name === (names[0] || state.focus)) || visibleRows()[0];
    if (!row) { info.replaceChildren(node("h2", "标签信息"), node("p", "选择一个标签查看资料。", "hint-copy")); return; }
    const details = node("dl", null, "panel-summary");
    details.append(node("dt", "数量"), node("dd", `${row.count} 条`), node("dt", "最近使用"), node("dd", date(row.last_used_at)));
    const list = node("ul", null, "tag-recent-list");
    row.recent.forEach(item => {
      const li = node("li"); const link = node("a", item.title);
      link.href = `/articles/${encodeURIComponent(item.entry_id)}`; link.dataset.route = link.href;
      link.addEventListener("click", event => { event.preventDefault(); event.stopPropagation(); D.navigate(new URL(link.href).pathname); });
      li.append(link); list.append(li);
    });
    info.replaceChildren(node("h2", `# ${row.name}`), details, node("h3", "最近加入"), list);
  }
  function makePin(row) {
    const pinFull = pinned().length >= PIN_LIMIT && !row.pinned_at;
    const pin = button("", () => change("pin", {name: row.name, pinned: !row.pinned_at}), "icon-button tag-pin");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS(svg.namespaceURI, "use");
    use.setAttribute("href", "/static/icons.svg#pin");
    svg.setAttribute("aria-hidden", "true");
    svg.append(use);
    pin.append(svg);
    pin.dataset.tagAction = "pin";
    pin.disabled = state.busy || pinFull;
    pin.setAttribute("aria-pressed", String(Boolean(row.pinned_at)));
    pin.setAttribute("aria-label", pinFull ? `最多固定 ${PIN_LIMIT} 个` : `${row.pinned_at ? "取消固定" : "固定"} ${row.name}`);
    if (pinFull) pin.title = `最多固定 ${PIN_LIMIT} 个`;
    return pin;
  }
  function tagLink(row) {
    const link = node("a", row.name);
    link.title = row.name;
    const href = D.tagPath(row.name);
    link.href = href;
    link.dataset.tagAction = "name";
    link.addEventListener("click", (event) => { event.preventDefault(); D.navigate(href); });
    return link;
  }
  function renderAliases() {
    const root = $("tag-aliases");
    const entries = Object.entries(state.aliases).filter(([alias, canonical]) => matches(alias) || matches(canonical));
    if (!entries.length) {
      root.replaceChildren(emptyList(search() ? "没有符合条件的别名" : "还没有别名",
        search() ? "试试其他标签名称。" : "合并或重命名标签后，旧名称会显示在这里。"));
      return;
    }
    const list = node("div", null, "tag-alias-list");
    entries.forEach(([alias, canonical]) => {
      const row = node("div", null, "tag-alias-row");
      const source = node("span", alias, "tag-alias-source"); source.title = alias;
      const target = tagLink({name: canonical}); target.className = "tag-alias-target";
      const actions = node("div", null, "tag-alias-actions");
      const operation = state.undoableByAlias[alias];
      if (operation) {
        const undo = button("撤销操作", () => change("undo", {operation_id: operation}));
        undo.title = "恢复该次合并或重命名前的资料关联";
        actions.append(undo);
      }
      const split = button("解除别名", () => change("split", {name: alias}));
      split.title = "旧名称恢复独立使用，已归类的资料保持现状";
      actions.append(split);
      row.append(source, target, actions);
      list.append(row);
    });
    root.replaceChildren(
      node("p", "旧名称会归入当前标签。撤销可恢复当次操作前的资料关联，解除别名只影响今后的归类。", "tags-section-intro"),
      columnHeading(["原标签", "当前标签", "操作"], "tag-alias-heading"), list,
    );
  }
  function render() {
    const known = new Set(state.rows.map(row => row.name));
    state.selected = new Set([...state.selected].filter(name => known.has(name)));
    const rows = visibleRows();
    const order = pinned().map(row => row.name);
    const focused = document.activeElement;
    const focusName = focused?.closest("[data-tag-name]")?.dataset.tagName;
    const focusAction = focused?.dataset.tagAction;
    $("tags-rows").replaceChildren(...rows.map(row => {
      const tr = node("tr"); tr.dataset.tagName = row.name; tr.tabIndex = -1;
      tr.classList.toggle("selected", state.selected.has(row.name));
      tr.addEventListener("focusin", () => { state.focus = row.name; renderPanel(); });
      const check = node("input"); check.type = "checkbox"; check.checked = state.selected.has(row.name);
      check.setAttribute("aria-label", `选择 ${row.name}`); check.dataset.tagAction = "select"; check.disabled = state.busy;
      check.addEventListener("change", () => {
        if (check.checked) state.selected.add(row.name); else state.selected.delete(row.name);
        state.focus = row.name; render();
      });
      const link = tagLink(row);
      const pin = makePin(row);
      const controls = node("div", null, "tag-pin-controls"); controls.append(pin);
      if (row.pinned_at) {
        [-1, 1].forEach(delta => {
          const moveButton = button(delta < 0 ? "↑" : "↓", () => move(row.name, delta), "icon-button tag-move");
          moveButton.dataset.tagAction = delta < 0 ? "up" : "down";
          moveButton.setAttribute("aria-label", `${delta < 0 ? "上移" : "下移"}固定标签 ${row.name}`);
          moveButton.disabled = state.busy || order.indexOf(row.name) + delta < 0 || order.indexOf(row.name) + delta >= order.length;
          controls.append(moveButton);
        });
        tr.draggable = !state.busy;
        tr.addEventListener("dragstart", event => { event.dataTransfer.setData("text/plain", row.name); event.dataTransfer.effectAllowed = "move"; });
        tr.addEventListener("dragover", event => { event.preventDefault(); event.dataTransfer.dropEffect = "move"; });
        tr.addEventListener("drop", event => { event.preventDefault(); reorder(event.dataTransfer.getData("text/plain"), row.name); });
      }
      [check, link, node("span", String(row.count), "tag-count"), node("time", date(row.last_used_at)), controls].forEach(value => {
        const td = node("td"); if (value.tagName === "TIME" && row.last_used_at) value.dateTime = row.last_used_at;
        td.append(value); tr.append(td);
      });
      return tr;
    }));
    if (focusName && focusAction) {
      [...$("tags-rows").querySelectorAll("[data-tag-name]")].find((row) => row.dataset.tagName === focusName)?.querySelector(`[data-tag-action='${focusAction}']`)?.focus({preventScroll: true});
    }
    const pinNote = pinned().length >= PIN_LIMIT ? ` · 最多固定 ${PIN_LIMIT} 个` : "";
    $("tags-count").textContent = (search() ? `${rows.length} / ${state.rows.length} 个标签` : `${state.rows.length} 个标签`) + pinNote;
    $("tags-count").classList.toggle("hidden", state.selected.size > 0);
    $("tags-batch-actions").classList.toggle("hidden", !state.selected.size);
    $("tags-selected").textContent = state.selected.size ? `已选 ${state.selected.size} 个` : "";
    $("tags-rename").disabled = state.busy || state.selected.size !== 1;
    $("tags-merge").disabled = state.busy || state.selected.size < 2;
    $("tags-delete").disabled = state.busy || !state.selected.size;
    $("tags-select-all").disabled = state.busy || !rows.length;
    $("tags-select-all").checked = Boolean(rows.length) && rows.every(row => state.selected.has(row.name));
    $("tags-select-all").indeterminate = rows.some(row => state.selected.has(row.name)) && !$("tags-select-all").checked;
    $("tags-clear-selection").disabled = state.busy;
    $("tags-cleanup").disabled = state.busy;
    $("tags-empty").classList.toggle("hidden", rows.length > 0);
    document.querySelectorAll("[data-tag-sort]").forEach(control => {
      const selected = control.dataset.tagSort === state.sort;
      const label = {name: "标签名", count: "数量", last_used_at: "最近使用时间"}[control.dataset.tagSort];
      control.textContent = label + (selected ? state.direction < 0 ? " ↓" : " ↑" : "");
      control.closest("th").setAttribute("aria-sort", selected ? state.direction < 0 ? "descending" : "ascending" : "none");
    });
    const groups = duplicateGroups().filter(names => names.some(matches));
    const duplicateList = node("div", null, "tag-duplicate-list");
    groups.forEach(names => {
      const row = node("div", null, "tag-duplicate-group");
      const members = node("div", null, "tag-duplicate-members");
      const affected = node("span", null, "tag-duplicate-count");
      const select = node("select");
      select.setAttribute("aria-label", `选择合并目标：${names.join("、")}`);
      const key = duplicateKey(names);
      const pickedNames = () => names.filter((name) => !state.excludedDuplicates.has(name));
      const merge = button("合并", async () => {
        const target = select.value;
        const picked = pickedNames();
        if (picked.length < 2 || !picked.includes(target)) return;
        const count = articleCount(picked);
        const confirmed = await D.confirmDanger({
          title: "合并标签",
          description: `将把 ${picked.length} 个标签合并到「${target}」，会影响 ${count} 篇文章。`,
          confirmLabel: "合并",
        });
        if (confirmed) await change("merge", {names: picked, target}, []);
      }, "secondary-button");
      const refreshSelect = () => {
        const picked = pickedNames();
        const previous = state.duplicateTargets.get(key);
        select.replaceChildren(...picked.map((name) => {
          const option = node("option", name);
          option.value = name;
          return option;
        }));
        if (picked.includes(previous)) select.value = previous;
        state.duplicateTargets.set(key, select.value);
        const blocked = state.busy || picked.length < 2;
        select.disabled = blocked;
        merge.disabled = blocked;
        affected.textContent = `${articleCount(picked)} 篇`;
      };
      names.forEach((name) => {
        const label = node("label", null, "tag-duplicate-member");
        const box = node("input");
        box.type = "checkbox";
        box.checked = !state.excludedDuplicates.has(name);
        box.disabled = state.busy;
        box.setAttribute("aria-label", `纳入合并：${name}`);
        box.addEventListener("change", () => {
          if (box.checked) state.excludedDuplicates.delete(name); else state.excludedDuplicates.add(name);
          refreshSelect();
        });
        const text = node("span", name); text.title = name;
        label.append(box, text);
        members.append(label);
      });
      select.addEventListener("change", () => state.duplicateTargets.set(key, select.value));
      refreshSelect();
      row.append(members, affected, select, merge);
      duplicateList.append(row);
    });
    $("tag-duplicates").replaceChildren(...(groups.length ? [
      node("p", "选择同一含义的标签，再指定保留名称。每组最多 5 个，确认前会显示影响的文章数。", "tags-section-intro"),
      columnHeading(["待合并标签", "影响文章", "保留为", "操作"], "tag-duplicate-heading"), duplicateList,
    ] : [emptyList(search() ? "没有符合条件的重复建议" : "没有可能重复的标签",
      search() ? "试试其他标签名称。" : "发现相似名称时，会在这里提供手工合并建议。") ]));
    renderAliases();
    renderViews();
    renderPanel();
  }
  async function open() {
    D.hideAllViews(); D.setPage("tags"); D.setNav("tags-nav"); $("tags-view").classList.remove("hidden");
    state.view = "all"; state.selected.clear(); state.focus = "";
    renderViews();
    document.title = "标签管理 · 抖库"; await load();
  }
  D.tags = {open, load, renderPanel};
  document.addEventListener("DOMContentLoaded", () => {
    const tabs = [...document.querySelectorAll("[data-tags-view]")];
    tabs.forEach(tab => tab.addEventListener("click", () => selectView(tab.dataset.tagsView)));
    $("tags-view").querySelector(".tags-tabs").addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const index = tabs.indexOf(event.target);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      selectView(tabs[next].dataset.tagsView, true);
    });
    $("tags-search").addEventListener("input", render);
    $("tags-clear-selection").addEventListener("click", () => {
      state.selected.clear(); render(); $("tags-select-all").focus({preventScroll: true});
    });
    $("tags-select-all").addEventListener("change", () => {
      visibleRows().forEach(row => { if ($("tags-select-all").checked) state.selected.add(row.name); else state.selected.delete(row.name); }); render();
    });
    document.querySelectorAll("[data-tag-sort]").forEach(control => control.addEventListener("click", () => {
      if (state.sort === control.dataset.tagSort) state.direction *= -1;
      else { state.sort = control.dataset.tagSort; state.direction = state.sort === "name" ? 1 : -1; }
      render();
    }));
    $("tags-rename").addEventListener("click", () => edit("rename"));
    $("tags-merge").addEventListener("click", () => edit("merge"));
    $("tags-delete").addEventListener("click", () => remove());
    $("tags-cleanup").addEventListener("click", async () => {
      if (state.busy) return;
      try {
        const preview = await D.api("/api/tags/cleanup", {method: "POST", body: JSON.stringify({confirmed: false})});
        if (!preview.count) { D.toast("没有未使用的标签"); return; }
        const names = preview.names || [];
        const shown = names.slice(0, 8).join("、");
        const extra = names.length > 8 ? ` 等 ${names.length} 个` : "";
        const confirmed = await D.confirmDanger({
          title: "清理未使用标签",
          description: `将删除 ${preview.count} 个没有文章的标签：${shown}${extra}。资料会保留。`,
          confirmLabel: "确认清理",
        });
        if (!confirmed) return;

        const data = await D.api("/api/tags/cleanup", {method: "POST", body: JSON.stringify({confirmed: true, names})});
        applyOverview(data);
        await D.refreshLibrary();
        const message = `已清理 ${data.deleted?.length || 0} 个未使用标签`;
        if (data.operation_id) D.toastAction(message, "撤销", async () => {
          await D.api("/api/tags/undo", {method: "POST", body: JSON.stringify({operation_id: data.operation_id})});
          await load(); await D.refreshLibrary(); D.toast("已恢复标签");
        }); else D.toast(message);
        render();
      } catch (error) {
        $("tags-error").textContent = error.message;
        $("tags-error").classList.remove("hidden");
      }
    });
    document.addEventListener("keydown", (event) => {
      if (!active() || state.view !== "all" || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
      if (D.chordPending || event.key === "g") return;
      if (document.querySelector("dialog[open]")) return;
      if (event.target.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key !== "j" && event.key !== "k") return;
      const rows = [...$("tags-rows").querySelectorAll("[data-tag-name]")];
      if (!rows.length) return;
      event.preventDefault();
      const current = rows.findIndex((row) => row.dataset.tagName === state.focus);
      const next = rows[Math.max(0, Math.min(rows.length - 1, (current < 0 ? (event.key === "j" ? -1 : 1) : current) + (event.key === "j" ? 1 : -1)))];
      state.focus = next.dataset.tagName;
      next.focus({preventScroll: false});
      renderPanel();
    });
    $("tag-edit-form").addEventListener("submit", async event => {
      if (event.submitter?.value === "cancel") return;
      event.preventDefault();
      const names = [...state.selected]; const mode = $("tag-edit-dialog").dataset.mode;
      const target = mode === "rename" ? $("tag-new-name").value.trim() : $("tag-target").value;
      $("tag-edit-submit").disabled = true;
      const saved = await change(mode, {name: names[0], names, target}, mode === "merge" ? [] : [target]);
      $("tag-edit-submit").disabled = false;
      if (saved) $("tag-edit-dialog").close();
      else { $("tag-edit-error").textContent = $("tags-error").textContent; $("tag-edit-error").classList.remove("hidden"); }
    });
  });
})();
