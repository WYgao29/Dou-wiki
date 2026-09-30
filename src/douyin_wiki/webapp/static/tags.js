(() => {
  "use strict";
  const D = window.Douku;
  const $ = D.$;
  const node = D.node;
  const state = {rows: [], duplicates: [], aliases: {}, undoByAlias: {}, selected: new Set(), focus: "", sort: "count", direction: -1, busy: false, request: 0};
  const PIN_LIMIT = 8;
  const narrowTags = () => window.matchMedia("(width < 480px)").matches;
  const active = () => $("app-shell").dataset.page === "tags";
  const date = (value) => value ? new Date(value).toLocaleString("zh-CN", {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}) : "—";
  const pinned = () => state.rows.filter(row => row.pinned_at).sort((a, b) => a.position - b.position || a.pinned_at.localeCompare(b.pinned_at));
  const button = (label, action, className = "text-button") => {
    const value = node("button", label, className);
    value.type = "button"; value.disabled = state.busy;
    value.addEventListener("click", action);
    return value;
  };
  function visibleRows() {
    const query = $("tags-search").value.trim().toLocaleLowerCase();
    return state.rows.filter(row => row.name.toLocaleLowerCase().includes(query)).sort((a, b) => {
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
      if (selection) { state.selected = new Set(selection); state.focus = selection[0] || ""; }
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
    $("tag-edit-description").textContent = mode === "rename" ? `重命名「${names[0]}」。旧名称会作为别名保留。`
      : `合并 ${names.length} 个标签；源标签会成为目标标签的别名，以后 AI 自动复用目标标签。`;
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
    pin.setAttribute("aria-label", pinFull ? `最多固定 ${PIN_LIMIT} 个` : `${row.pinned_at ? "取消固定" : "固定到侧栏"} ${row.name}`);
    if (pinFull) pin.title = `最多固定 ${PIN_LIMIT} 个`;
    return pin;
  }
  function tagLink(row) {
    const link = node("a", row.name);
    const href = D.tagPath(row.name);
    link.href = href;
    link.dataset.tagAction = "name";
    link.addEventListener("click", (event) => { event.preventDefault(); D.navigate(href); });
    return link;
  }
  function renderAliases() {
    const root = $("tag-aliases");
    const entries = Object.entries(state.aliases);
    root.classList.toggle("hidden", !entries.length);
    if (!entries.length) { root.replaceChildren(); return; }
    root.replaceChildren(
      node("h2", "别名"),
      node("p", "撤销操作会恢复该次合并或重命名前的资料关联。解除别名只影响今后的归类。历史操作没有撤销记录时，可解除别名。", "hint-copy"),
      ...entries.map(([alias, canonical]) => {
        const row = node("div", null, "tag-alias-row");
        row.append(node("span", `${alias} → ${canonical}`));
        const operation = state.undoByAlias[alias];
        if (operation) row.append(button("撤销操作", () => change("undo", {operation_id: operation}), "text-button"));
        row.append(button("解除别名", () => change("split", {name: alias}), "text-button"));
        return row;
      }),
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
    $("tags-cards").replaceChildren(...rows.map((row) => {
      const card = node("article", null, "tag-card");
      card.dataset.tagName = row.name;
      card.tabIndex = -1;
      card.classList.toggle("selected", state.selected.has(row.name));
      card.addEventListener("focusin", () => { state.focus = row.name; renderPanel(); });
      const check = node("input");
      check.type = "checkbox";
      check.checked = state.selected.has(row.name);
      check.setAttribute("aria-label", `选择 ${row.name}`);
      check.dataset.tagAction = "select";
      check.disabled = state.busy;
      check.addEventListener("change", () => {
        if (check.checked) state.selected.add(row.name); else state.selected.delete(row.name);
        state.focus = row.name; render();
      });
      const copy = node("div", null, "tag-card-copy");
      copy.append(tagLink(row), node("span", `${row.count} 条`, "tag-count"), node("time", `最近使用 ${date(row.last_used_at)}`, "hint-copy"));
      const actions = node("div", null, "tag-card-actions");
      actions.append(makePin(row));
      if (row.pinned_at) {
        actions.append(button("↑", () => move(row.name, -1)), button("↓", () => move(row.name, 1)));
        actions.children[1].setAttribute("aria-label", `上移 ${row.name}`);
        actions.children[2].setAttribute("aria-label", `下移 ${row.name}`);
      }
      card.append(check, copy, actions);
      return card;
    }));
    if (focusName && focusAction) {
      const scope = narrowTags() ? $("tags-cards") : $("tags-rows");
      [...scope.querySelectorAll("[data-tag-name]")].find((row) => row.dataset.tagName === focusName)?.querySelector(`[data-tag-action='${focusAction}']`)?.focus({preventScroll: true});
    }
    const pinNote = pinned().length >= PIN_LIMIT ? ` · 最多固定 ${PIN_LIMIT} 个` : "";
    $("tags-count").textContent = `${rows.length} / ${state.rows.length} 个标签${pinNote}`;
    $("tags-selected").textContent = state.selected.size ? `已选 ${state.selected.size} 个` : "";
    $("tags-rename").disabled = state.busy || state.selected.size !== 1;
    $("tags-merge").disabled = state.busy || state.selected.size < 2;
    $("tags-delete").disabled = state.busy || !state.selected.size;
    $("tags-select-all").disabled = state.busy || !rows.length;
    $("tags-select-all").checked = Boolean(rows.length) && rows.every(row => state.selected.has(row.name));
    $("tags-select-all").indeterminate = rows.some(row => state.selected.has(row.name)) && !$("tags-select-all").checked;
    $("tags-mobile-sort").value = `${state.sort}:${state.direction}`;
    $("tags-mobile-sort").disabled = state.busy;
    const mobileAll = $("tags-mobile-select-all");
    mobileAll.checked = $("tags-select-all").checked; mobileAll.indeterminate = $("tags-select-all").indeterminate;
    mobileAll.disabled = $("tags-select-all").disabled;
    $("tags-empty").classList.toggle("hidden", rows.length > 0);
    document.querySelectorAll("[data-tag-sort]").forEach(control => {
      const selected = control.dataset.tagSort === state.sort;
      const label = {name: "标签名", count: "数量", last_used_at: "最近使用时间"}[control.dataset.tagSort];
      control.textContent = label + (selected ? state.direction < 0 ? " ↓" : " ↑" : "");
      control.closest("th").setAttribute("aria-sort", selected ? state.direction < 0 ? "descending" : "ascending" : "none");
    });
    $("tag-duplicates").classList.toggle("hidden", !state.duplicates.length);
    $("tag-duplicates").replaceChildren(node("h2", "可能重复"), node("p", "这些标签可能含义相近，选择目标后点击合并。", "hint-copy"), ...state.duplicates.map(names => {
      const group = node("div", null, "tag-duplicate-group");
      const select = node("select"); select.setAttribute("aria-label", `选择合并目标：${names.join("、")}`);
      names.forEach(name => { const option = node("option", name); option.value = name; select.append(option); });
      select.disabled = state.busy;
      group.append(node("span", names.join(" · ")), select, button("合并", () => change("merge", {names, target: select.value}, [select.value]), "secondary-button"));
      return group;
    }));
    renderAliases();
    renderPanel();
  }
  async function open() {
    D.hideAllViews(); D.setPage("tags"); $("tags-view").classList.remove("hidden");
    document.title = "标签管理 · 抖库"; await load();
  }
  D.tags = {open, load, renderPanel};
  document.addEventListener("DOMContentLoaded", () => {
    $("tags-search").addEventListener("input", render);
    $("tags-mobile-select-all").addEventListener("change", event => {
      visibleRows().forEach(row => { if (event.target.checked) state.selected.add(row.name); else state.selected.delete(row.name); }); render();
    });
    $("tags-mobile-sort").addEventListener("change", event => {
      const [sort, direction] = event.target.value.split(":");
      state.sort = sort; state.direction = Number(direction); render();
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

        const data = await D.api("/api/tags/cleanup", {method: "POST", body: JSON.stringify({confirmed: true})});
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
      if (!active() || event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
      if (D.chordPending || event.key === "g") return;
      if (document.querySelector("dialog[open]")) return;
      if (event.target.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key !== "j" && event.key !== "k") return;
      const rows = [...(narrowTags() ? $("tags-cards") : $("tags-rows")).querySelectorAll("[data-tag-name]")];
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
      const saved = await change(mode, {name: names[0], names, target}, [target]);
      $("tag-edit-submit").disabled = false;
      if (saved) $("tag-edit-dialog").close();
      else { $("tag-edit-error").textContent = $("tags-error").textContent; $("tag-edit-error").classList.remove("hidden"); }
    });
  });
})();
