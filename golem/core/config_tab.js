/* Config tab — fetches config, renders category forms, saves updates */
(function () {
  "use strict";

  let configData = {};
  let currentCategory = null;
  let unsavedChanges = {};

  async function fetchConfig() {
    try {
      const resp = await fetch("/api/config", {
        headers: window.ADMIN_TOKEN ? {"Authorization": "Bearer " + window.ADMIN_TOKEN} : {},
      });
      if (!resp.ok) throw new Error("Failed to fetch config");
      configData = await resp.json();
      const cats = Object.keys(configData);
      if (cats.length > 0 && !currentCategory) currentCategory = cats[0];
      renderCategories();
      renderFields();
    } catch (e) {
      console.error("Config fetch error:", e);
    }
  }

  function renderCategories() {
    const container = document.getElementById("config-categories");
    if (!container) return;
    container.innerHTML = "";
    for (const cat of Object.keys(configData)) {
      const btn = document.createElement("button");
      btn.className = "config-cat-btn" + (cat === currentCategory ? " active" : "");
      btn.textContent = cat.charAt(0).toUpperCase() + cat.slice(1).replace("_", " ");
      btn.onclick = () => { currentCategory = cat; renderCategories(); renderFields(); };
      container.appendChild(btn);
    }
  }

  function renderFields() {
    const container = document.getElementById("config-fields");
    if (!container || !currentCategory) return;
    container.innerHTML = "";
    const fields = configData[currentCategory] || [];
    for (const fi of fields) {
      const row = document.createElement("div");
      row.className = "config-field";
      const labelWrap = document.createElement("div");
      labelWrap.className = "field-label-wrap";
      const label = document.createElement("label");
      label.textContent = fi.key;
      labelWrap.appendChild(label);
      const desc = document.createElement("span");
      desc.className = "field-desc";
      desc.textContent = fi.meta.description;
      labelWrap.appendChild(desc);
      row.appendChild(labelWrap);
      row.appendChild(createInput(fi));
      container.appendChild(row);
    }
    const btn = document.createElement("button");
    btn.className = "config-save-btn";
    btn.textContent = "Save";
    btn.onclick = saveChanges;
    container.appendChild(btn);
  }

  /** Pick a sensible step for number inputs based on field metadata. */
  function pickStep(fi) {
    const meta = fi.meta;
    if (meta.field_type === "int") return "1";
    // Ratio fields (0.0–1.0) get a fine step
    if (meta.max_val != null && meta.max_val <= 1.0) return "0.05";
    // USD / budget floats get a whole-dollar step
    return "1";
  }

  function createInput(fi) {
    const meta = fi.meta;
    if (meta.field_type === "choice" && meta.choices) {
      const sel = document.createElement("select");
      for (const c of meta.choices) {
        const opt = document.createElement("option");
        opt.value = c; opt.textContent = c;
        if (c === fi.value) opt.selected = true;
        sel.appendChild(opt);
      }
      sel.onchange = () => { unsavedChanges[fi.key] = sel.value; };
      return sel;
    }
    if (meta.field_type === "bool") {
      const wrap = document.createElement("label");
      wrap.className = "toggle-switch";
      const cb = document.createElement("input");
      cb.type = "checkbox"; cb.checked = fi.value;
      cb.onchange = () => { unsavedChanges[fi.key] = cb.checked; };
      const track = document.createElement("span");
      track.className = "toggle-track";
      wrap.appendChild(cb);
      wrap.appendChild(track);
      return wrap;
    }
    const inp = document.createElement("input");
    if (meta.field_type === "int" || meta.field_type === "float") {
      inp.type = "number";
      if (meta.min_val != null) inp.min = meta.min_val;
      if (meta.max_val != null) inp.max = meta.max_val;
      inp.step = pickStep(fi);
    } else {
      inp.type = meta.sensitive ? "password" : "text";
    }
    const raw = fi.value != null ? fi.value : "";
    inp.value = Array.isArray(raw) ? raw.join(", ")
      : typeof raw === "object" ? JSON.stringify(raw) : raw;
    inp.oninput = () => { unsavedChanges[fi.key] = inp.value; };
    return inp;
  }

  async function saveChanges() {
    if (Object.keys(unsavedChanges).length === 0) {
      showToast("No changes to save.", "success");
      return;
    }
    try {
      const resp = await fetch("/api/config/update", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(window.ADMIN_TOKEN ? {"Authorization": "Bearer " + window.ADMIN_TOKEN} : {}),
        },
        body: JSON.stringify(unsavedChanges),
      });
      const data = await resp.json();
      if (data.success) {
        showToast("Config saved. Daemon reloading...", "success");
        unsavedChanges = {};
        setTimeout(fetchConfig, 2000);
      } else {
        showToast("Errors: " + data.errors.join("; "), "error");
      }
    } catch (e) {
      showToast("Save failed: " + e.message, "error");
    }
  }

  function showToast(msg, type) {
    const el = document.createElement("div");
    el.className = "config-toast " + type;
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 4000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    const observer = new MutationObserver(() => {
      const view = document.getElementById("view-config");
      if (view && view.style.display !== "none") fetchConfig();
    });
    const view = document.getElementById("view-config");
    if (view) observer.observe(view, {attributes: true, attributeFilter: ["style"]});
  });

  window.initConfigTab = fetchConfig;
})();
