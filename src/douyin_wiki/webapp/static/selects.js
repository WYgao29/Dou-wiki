/* Shared listbox UI; the original select remains the source of values and form data. */
(() => {
  const D = window.Douku;
  const controls = new Map();
  const buttons = new WeakMap();
  const canvas = document.createElement("canvas").getContext("2d");
  const properties = ["value", "selectedIndex"].map(name =>
    [name, Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, name)]);
  const menu = D.node("div", null, "select-menu");
  menu.id = "select-menu"; menu.hidden = true; menu.tabIndex = -1;
  menu.setAttribute("role", "listbox");
  if (typeof menu.showPopover === "function") menu.setAttribute("popover", "manual");
  document.body.append(menu);
  let active = null, highlighted = -1, entries = [], sequence = 0;
  let search = "", searchTimer = 0, positionFrame = 0;
  const resize = new ResizeObserver(() => schedulePosition());

  function labelOf(select) {
    if (select.getAttribute("aria-label")) return select.getAttribute("aria-label");
    const labelled = select.getAttribute("aria-labelledby");
    if (labelled) return labelled.split(/\s+/).map(id => document.getElementById(id)?.textContent || "").join(" ").trim();
    return [...select.labels].map(label => {
      const copy = label.cloneNode(true);
      copy.querySelectorAll("select, .select-widget").forEach(node => node.remove());
      return copy.textContent.trim();
    }).filter(Boolean).join(" ") || "选择选项";
  }

  function optionsOf(select) {
    return [...select.options].filter(option => !option.hidden && !option.closest("optgroup[hidden]"));
  }

  function disabled(option) {
    return option.disabled || Boolean(option.closest("optgroup[disabled]"));
  }

  function sync(record) {
    const {select, host, button, value} = record;
    const style = getComputedStyle(button);
    canvas.font = style.fontWeight + " " + style.fontSize + " " + style.fontFamily;
    const options = optionsOf(select);
    const width = Math.ceil(Math.max(0, ...options.map(option => canvas.measureText(option.label).width)));
    host.style.setProperty("--select-label-width", width + "px");
    const hidden = select.hidden || select.classList.contains("hidden") || getComputedStyle(select).display === "none";
    if (host.hidden !== hidden) host.hidden = hidden;
    button.disabled = select.matches(":disabled");
    record.label = labelOf(select);
    button.setAttribute("aria-label", record.label);
    const description = select.getAttribute("aria-describedby");
    if (description) button.setAttribute("aria-describedby", description);
    else button.removeAttribute("aria-describedby");
    button.setAttribute("aria-required", String(select.required));
    if (select.validity.valid) record.invalid = false;
    const invalid = record.invalid || select.getAttribute("aria-invalid") === "true";
    button.setAttribute("aria-invalid", String(invalid));
    const text = select.selectedOptions[0]?.label || "请选择";
    if (value.textContent !== text) value.textContent = text;
    button.title = invalid ? select.validationMessage || text : text;
    if (active === record) {
      if (hidden || button.disabled || !button.getClientRects().length) close(false);
      else renderMenu();
    }
  }

  function focusOption(index, scroll = true) {
    highlighted = index;
    [...menu.querySelectorAll(".select-option")].forEach(row =>
      row.classList.toggle("is-highlighted", Number(row.dataset.index) === index));
    const row = menu.querySelector('[data-index="' + index + '"]');
    if (row && active) {
      active.button.setAttribute("aria-activedescendant", row.id);
      if (scroll) row.scrollIntoView({block: "nearest"});
    } else active?.button.removeAttribute("aria-activedescendant");
  }

  function renderMenu() {
    if (!active) return;
    const previous = entries[highlighted];
    entries = optionsOf(active.select);
    menu.replaceChildren();
    menu.setAttribute("aria-label", active.label);
    let group = null;
    entries.forEach((option, index) => {
      const parent = option.parentElement;
      if (parent.tagName === "OPTGROUP" && parent !== group) {
        const heading = D.node("div", parent.label, "select-menu-group");
        heading.setAttribute("role", "presentation");
        menu.append(heading); group = parent;
      }
      const row = D.node("div", null, "select-option");
      row.id = "select-option-" + active.id + "-" + index;
      row.dataset.index = index;
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", String(option.selected));
      row.setAttribute("aria-disabled", String(disabled(option)));
      row.append(D.node("span", option.label, "select-option-label"), D.icon("check"));
      menu.append(row);
    });
    if (!entries.length) menu.append(D.node("div", "暂无选项", "select-menu-empty"));
    let index = entries.indexOf(previous);
    if (index < 0 || disabled(entries[index])) index = entries.findIndex(option => option.selected && !disabled(option));
    if (index < 0) index = entries.findIndex(option => !disabled(option));
    focusOption(index, false);
    schedulePosition();
  }

  function position() {
    positionFrame = 0;
    if (!active) return;
    const rect = (active.host.closest(".select-control") || active.button).getBoundingClientRect(), gap = 6, edge = 8;
    if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) { close(false); return; }
    canvas.font = "400 13px " + getComputedStyle(menu).fontFamily;
    const textWidth = Math.max(0, ...entries.map(option => canvas.measureText(option.label).width));
    const width = Math.min(Math.max(rect.width, Math.min(textWidth + 52, 360), 160), innerWidth - edge * 2);
    menu.style.width = width + "px";
    const below = innerHeight - rect.bottom - gap - edge;
    const above = rect.top - gap - edge;
    const naturalHeight = Math.min(menu.scrollHeight, 320);
    const down = below >= naturalHeight || below >= above;
    menu.style.maxHeight = Math.max(40, Math.min(320, down ? below : above)) + "px";
    const height = menu.getBoundingClientRect().height;
    const left = Math.max(edge, Math.min(rect.left, innerWidth - width - edge));
    const top = Math.max(edge, Math.min(down ? rect.bottom + gap : rect.top - height - gap, innerHeight - height - edge));
    menu.style.left = left + "px"; menu.style.top = top + "px";
  }

  function schedulePosition() {
    if (active && !positionFrame) positionFrame = requestAnimationFrame(position);
  }

  function close(restoreFocus = false) {
    if (!active) return;
    const previous = active; active = null;
    previous.button.setAttribute("aria-expanded", "false");
    previous.button.removeAttribute("aria-activedescendant");
    if (menu.hidePopover) {
      try { menu.hidePopover(); } catch { /* The browser may already have dismissed its top layer. */ }
    }
    menu.hidden = true;
    resize.disconnect();
    cancelAnimationFrame(positionFrame); positionFrame = 0;
    clearTimeout(searchTimer); search = "";
    entries = []; highlighted = -1;
    if (restoreFocus && previous.button.isConnected) previous.button.focus({preventScroll: true});
  }

  function open(record) {
    if (record.button.disabled || record.host.hidden || record.select.closest("[inert]")) return;
    if (active === record) return;
    close(false);
    document.dispatchEvent(new Event("douku:select-open"));
    active = record;
    const parent = record.select.closest("dialog[open]") || document.body;
    parent.append(menu);
    record.button.focus({preventScroll: true});
    record.button.setAttribute("aria-expanded", "true");
    menu.hidden = false;
    renderMenu();
    if (menu.showPopover && menu.hasAttribute("popover")) {
      try { menu.showPopover(); } catch { menu.removeAttribute("popover"); }
    }
    position();
    focusOption(highlighted);
    resize.observe(record.button);
  }

  function commit(index, restoreFocus = true) {
    if (!active || index < 0 || !entries[index] || disabled(entries[index])) { close(restoreFocus); return; }
    const record = active, option = entries[index];
    const changed = record.select.selectedIndex !== option.index;
    close(restoreFocus);
    if (!changed) return;
    record.select.selectedIndex = option.index;
    record.select.dispatchEvent(new Event("input", {bubbles: true}));
    record.select.dispatchEvent(new Event("change", {bubbles: true}));
    sync(record);
  }

  function step(direction, edge = false) {
    const available = entries.map((option, index) => disabled(option) ? -1 : index).filter(index => index >= 0);
    if (!available.length) return;
    const current = available.indexOf(highlighted);
    const next = edge ? (direction > 0 ? available.length - 1 : 0)
      : Math.max(0, Math.min(available.length - 1, current + direction));
    focusOption(available[next]);
  }

  function keyboard(event) {
    const record = active || buttons.get(event.target.closest(".select-trigger"));
    if (!record || event.isComposing) return;
    if (active && !record.host.contains(event.target) && !menu.contains(event.target)) return;
    if (event.metaKey || event.ctrlKey) { if (active) close(false); return; }
    if (event.key === "Tab") { if (active) commit(highlighted, false); return; }
    const printable = event.key.length === 1 && !event.altKey;
    if (!printable && !["ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape"].includes(event.key)) return;
    if (event.key === "Escape" && !active) return;
    event.preventDefault(); event.stopPropagation();
    if (event.key === "Escape") { close(true); return; }
    const wasOpen = active === record;
    open(record);
    if (!active) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      if (wasOpen) step(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Home" || event.key === "End") step(event.key === "End" ? 1 : -1, true);
    else if (event.key === "Enter" || (event.key === " " && !search)) { if (wasOpen) commit(highlighted); }
    else if (printable) {
      clearTimeout(searchTimer);
      search += event.key.toLocaleLowerCase();
      const repeated = [...search].every(character => character === search[0]);
      const prefix = repeated ? search[0] : search;
      const start = repeated ? highlighted + 1 : Math.max(0, highlighted);
      for (let count = 0; count < entries.length; count++) {
        const index = (start + count) % entries.length;
        if (!disabled(entries[index]) && entries[index].label.toLocaleLowerCase().startsWith(prefix)) { focusOption(index); break; }
      }
      searchTimer = setTimeout(() => { search = ""; }, 600);
    }
  }

  function enhance(select) {
    if (!select.isConnected || controls.has(select) || select.multiple || select.size > 1) return;
    const focused = document.activeElement === select;
    const host = D.node("span", null, "select-widget");
    const button = D.node("button", null, "select-trigger");
    const value = D.node("span", null, "select-value");
    const arrow = D.icon("chevron-right"); arrow.classList.add("select-chevron");
    button.type = "button";
    button.tabIndex = select.tabIndex;
    button.setAttribute("role", "combobox");
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-controls", menu.id);
    button.setAttribute("aria-expanded", "false");
    button.append(value, arrow);
    const record = {select, host, button, value, id: ++sequence, invalid: false,
      tabindex: select.getAttribute("tabindex"), ariaHidden: select.getAttribute("aria-hidden"),
      abort: new AbortController(), originals: new Map()};
    controls.set(select, record); buttons.set(button, record);
    select.before(host); host.append(select, button);
    select.classList.add("select-native"); select.tabIndex = -1; select.setAttribute("aria-hidden", "true");
    const on = (node, type, listener) => node.addEventListener(type, listener, {signal: record.abort.signal});
    on(button, "click", () => active === record ? close(false) : open(record));
    on(select, "click", event => { event.preventDefault(); open(record); });
    on(select, "focus", () => button.focus({preventScroll: true}));
    on(select, "change", () => sync(record));
    on(select, "input", () => sync(record));
    on(select, "invalid", event => {
      event.preventDefault(); record.invalid = true; sync(record); button.focus({preventScroll: true});
    });
    properties.forEach(([name, descriptor]) => {
      const original = Object.getOwnPropertyDescriptor(select, name);
      if (original && !original.configurable) return;
      record.originals.set(name, original);
      Object.defineProperty(select, name, {configurable: true,
        get() { return descriptor.get.call(this); },
        set(next) { descriptor.set.call(this, next); sync(record); },
      });
    });
    sync(record);
    if (focused) button.focus({preventScroll: true});
  }

  function remove(record) {
    if (active === record) close(false);
    controls.delete(record.select);
    record.abort.abort();
    record.originals.forEach((descriptor, name) => {
      delete record.select[name];
      if (descriptor) Object.defineProperty(record.select, name, descriptor);
    });
    record.select.classList.remove("select-native");
    for (const [name, value] of [["tabindex", record.tabindex], ["aria-hidden", record.ariaHidden]]) {
      if (value === null) record.select.removeAttribute(name);
      else record.select.setAttribute(name, value);
    }
    record.host.before(record.select); record.host.remove();
  }

  menu.addEventListener("pointerdown", event => {
    if (event.target.closest(".select-option")) event.preventDefault();
  });
  menu.addEventListener("pointermove", event => {
    const row = event.target.closest(".select-option");
    if (row && row.getAttribute("aria-disabled") !== "true") focusOption(Number(row.dataset.index), false);
  });
  menu.addEventListener("click", event => {
    const row = event.target.closest(".select-option");
    if (row && row.getAttribute("aria-disabled") !== "true") commit(Number(row.dataset.index));
  });
  document.addEventListener("keydown", keyboard, {capture: true});
  document.addEventListener("pointerdown", event => {
    if (active && !active.host.contains(event.target) && !menu.contains(event.target)) close(false);
  }, {capture: true});
  document.addEventListener("focusin", event => {
    if (active && !active.host.contains(event.target) && !menu.contains(event.target)) close(false);
  });
  document.addEventListener("scroll", event => {
    if (!menu.contains(event.target)) schedulePosition();
  }, {capture: true, passive: true});
  window.addEventListener("resize", schedulePosition);
  window.addEventListener("blur", () => close(false));
  document.addEventListener("reset", event => requestAnimationFrame(() => {
    if (event.defaultPrevented) return;
    controls.forEach(record => { if (record.select.form === event.target) { record.invalid = false; sync(record); } });
  }), {capture: true});
  document.fonts.ready.then(() => controls.forEach(sync));
  document.querySelectorAll("select").forEach(enhance);
  new MutationObserver(mutations => {
    const changed = new Set();
    for (const mutation of mutations) {
      const target = mutation.target.nodeType === 1 ? mutation.target : mutation.target.parentElement;
      const select = target?.closest("select");
      if (select && controls.has(select)) changed.add(controls.get(select));
      if (target?.matches("fieldset") && mutation.attributeName === "disabled") {
        target.querySelectorAll("select").forEach(node => { if (controls.has(node)) changed.add(controls.get(node)); });
      }
      if (target?.matches("dialog") && mutation.attributeName === "open" && active
          && (target.open !== target.contains(active.select))) close(false);
      for (const node of mutation.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches("select")) enhance(node);
        node.querySelectorAll("select").forEach(enhance);
      }
    }
    controls.forEach(record => {
      if (!record.select.isConnected || record.select.multiple || record.select.size > 1) remove(record);
    });
    changed.forEach(record => { if (controls.has(record.select)) sync(record); });
    if (active && (!active.button.getClientRects().length || active.select.closest("[inert]"))) close(false);
  }).observe(document.body, {subtree: true, childList: true, characterData: true, attributes: true,
    attributeFilter: ["disabled", "hidden", "selected", "value", "label", "class", "open",
      "aria-label", "aria-labelledby", "aria-describedby", "aria-invalid", "required", "multiple", "size"]});
})();
