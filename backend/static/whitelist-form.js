// Whitelist entry form: account scope/type selection and searchable survey-point dropdowns.
class SearchSelect {
  constructor(config) {
    this.root = config.root;
    this.input = config.input;
    this.hidden = config.hidden;
    this.menu = config.menu;
    this.placeholder = config.placeholder || "输入关键字筛选";
    this.onChange = config.onChange || function() {};
    this.options = [];
    this.activeIndex = -1;

    this.input.placeholder = this.placeholder;
    this.input.setAttribute("role", "combobox");
    this.input.setAttribute("aria-expanded", "false");
    this.input.setAttribute("autocomplete", "off");
    this.input.addEventListener("focus", () => this.open());
    this.input.addEventListener("input", () => {
      const hadValue = !!this.hidden.value;
      this.hidden.value = "";
      this.root.classList.remove("legacy");
      if (hadValue) this.onChange("");
      this.open();
    });
    this.input.addEventListener("keydown", (event) => this.handleKey(event));
    this.input.addEventListener("blur", () => setTimeout(() => this.close(), 120));
    document.addEventListener("click", (event) => {
      if (!this.root.contains(event.target)) this.close();
    });
  }

  setOptions(options, selectedValue) {
    this.options = options || [];
    this.hidden.value = selectedValue || "";
    this.root.classList.remove("legacy");
    const selected = this.options.find(item => item.value === selectedValue);
    if (selectedValue && selected) {
      this.input.value = selected.label;
    } else if (selectedValue) {
      // Legacy rows remain savable until an admin intentionally changes the region.
      this.input.value = selectedValue;
      this.root.classList.add("legacy");
    } else {
      this.input.value = "";
    }
    this.activeIndex = -1;
    this.renderMenu();
  }

  value() {
    return this.hidden.value.trim();
  }

  clear() {
    this.hidden.value = "";
    this.input.value = "";
    this.root.classList.remove("legacy");
    this.renderMenu();
  }

  filteredOptions() {
    const query = this.input.value.trim().toLowerCase();
    if (!query) return this.options;
    return this.options.filter(item => (
      item.label.toLowerCase().includes(query) ||
      (item.detail || "").toLowerCase().includes(query)
    ));
  }

  renderMenu() {
    const items = this.filteredOptions();
    if (!items.length) {
      this.menu.innerHTML = '<div class="search-empty">没有匹配项</div>';
    } else {
      this.menu.innerHTML = items.map((item, index) => (
        '<button type="button" class="search-option' + (index === this.activeIndex ? ' active' : '') + '"' +
        ' role="option" data-value="' + escapeHtml(item.value) + '">' +
          '<span>' + escapeHtml(item.label) + '</span>' +
          (item.detail ? '<small>' + escapeHtml(item.detail) + '</small>' : '') +
        '</button>'
      )).join('');
      this.menu.querySelectorAll(".search-option").forEach(button => {
        button.addEventListener("click", () => this.select(button.dataset.value));
      });
    }
    this.input.setAttribute("aria-expanded", this.menu.classList.contains("open") ? "true" : "false");
  }

  open() {
    this.menu.classList.add("open");
    this.renderMenu();
  }

  close() {
    this.menu.classList.remove("open");
    this.input.setAttribute("aria-expanded", "false");
  }

  select(value) {
    const option = this.options.find(item => item.value === value);
    this.hidden.value = value;
    this.input.value = option ? option.label : value;
    this.root.classList.remove("legacy");
    this.activeIndex = -1;
    this.close();
    this.onChange(value);
  }

  handleKey(event) {
    const items = this.filteredOptions();
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!items.length) return;
      const delta = event.key === "ArrowDown" ? 1 : -1;
      this.activeIndex = (this.activeIndex + delta + items.length) % items.length;
      this.open();
      const active = this.menu.children[this.activeIndex];
      if (active) active.scrollIntoView({ block: "nearest" });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (this.activeIndex >= 0 && items[this.activeIndex]) {
        this.select(items[this.activeIndex].value);
      } else if (items.length === 1) {
        this.select(items[0].value);
      }
      return;
    }
    if (event.key === "Escape" && this.menu.classList.contains("open")) {
      event.stopPropagation();
      this.close();
    }
  }
}

let regionCatalog = { points: [], defaultProvince: "贵州省", loaded: false };
let regionCatalogPromise = null;
let regionPickers = null;
let originalEntry = null;

function allowedScopeOptions() {
  if (isSystemAdmin()) return ["省级", "市级", "区县", "调查员"];
  if (me.admin_level === "省级") return ["市级", "区县", "调查员"];
  if (me.admin_level === "市级") return ["区县", "调查员"];
  if (me.admin_level === "区县") return ["调查员"];
  return [];
}

function selectedScope() {
  const checked = document.querySelector('input[name="fAdminLevel"]:checked');
  return checked ? checked.value : "";
}

function selectedAccountType() {
  const checked = document.querySelector('input[name="fSysRole"]:checked');
  return checked ? checked.value : "";
}

function setScope(value) {
  const input = document.querySelector('input[name="fAdminLevel"][value="' + value + '"]');
  if (input) input.checked = true;
}

function setAccountType(value) {
  const input = document.querySelector('input[name="fSysRole"][value="' + value + '"]');
  if (input) input.checked = true;
}

function defaultAccountType(scope) {
  return scope === "调查员" ? "普通用户" : "业务管理员";
}

function accountTypesForScope(scope) {
  if (scope === "调查员") return ["普通用户"];
  if (scope === "市级" || scope === "区县") return ["业务管理员"];
  if (scope === "省级") return ["业务管理员", "系统管理员"];
  return [];
}

function renderScopeChoices() {
  const allowed = allowedScopeOptions();
  const isSys = visibleAccountType() === "系统管理员";
  document.querySelectorAll('input[name="fAdminLevel"]').forEach(input => {
    input.closest(".choice").classList.toggle("hidden", !allowed.includes(input.value));
    input.disabled = isSys ? input.value !== "省级" : !allowed.includes(input.value);
  });
}

function visibleAccountType() {
  if (isSystemAdmin()) return selectedAccountType();
  return defaultAccountType(selectedScope());
}

function renderAccountTypeChoices() {
  const scope = selectedScope();
  const allowed = accountTypesForScope(scope);
  document.querySelectorAll('input[name="fSysRole"]').forEach(input => {
    input.disabled = !allowed.includes(input.value);
  });
  if (isSystemAdmin()) {
    $("fSysRoleChoices").classList.remove("hidden");
    $("fSysRoleReadonly").classList.add("hidden");
  } else {
    $("fSysRoleChoices").classList.add("hidden");
    $("fSysRoleReadonly").classList.remove("hidden");
    $("fSysRoleReadonlyText").textContent = visibleAccountType() + "（由系统按管理范围自动设置）";
  }
}

function renderRoleHint() {
  const scope = selectedScope();
  const role = visibleAccountType();
  let text = "";
  if (role === "系统管理员") {
    text = "该账号拥有全部后台权限，不受区域限制。";
  } else if (role === "业务管理员") {
    if (scope === "省级") text = "该账号可管理全省范围内的业务后台。";
    if (scope === "市级") text = (regionPickers.city.value() ? "该账号可管理 " + regionPickers.city.value() + " 范围内的业务后台。" : "该账号可管理所选市/州范围内的业务后台。");
    if (scope === "区县") text = "该账号可管理所选县/区范围内的业务后台。";
  } else if (role === "普通用户") {
    text = "该账号只使用 AI 助手，不进入管理后台。";
  }
  $("roleHint").textContent = text;
}

function actorRegionPoints() {
  if (isSystemAdmin()) return regionCatalog.points;
  return regionCatalog.points.filter(point => (
    (!me.province || point.province === me.province) &&
    (!me.city || point.city === me.city) &&
    (!me.county || point.county === me.county)
  ));
}

function optionList(points, field, filters, detailField) {
  const filtered = points.filter(point => Object.entries(filters).every(([key, value]) => !value || point[key] === value));
  const seen = new Set();
  const options = [];
  filtered.forEach(point => {
    if (seen.has(point[field])) return;
    seen.add(point[field]);
    options.push({ value: point[field], label: point[field], detail: detailField ? point[detailField] : "" });
  });
  return options;
}

function refreshRegionOptions() {
  if (!regionPickers) return;
  const points = actorRegionPoints();
  const city = regionPickers.city.value();
  const county = regionPickers.county.value();
  const township = regionPickers.township.value();

  regionPickers.city.setOptions(optionList(points, "city", {}, ""), city);
  regionPickers.county.setOptions(
    optionList(points, "county", { city }, ""),
    county,
  );
  regionPickers.township.setOptions(
    optionList(points, "township", { city, county }, ""),
    township,
  );
  regionPickers.community.setOptions(
    optionList(points, "community", { city, county, township }, "township"),
    regionPickers.community.value(),
  );
  renderRegionHint();
}

function regionVisibility() {
  const scope = selectedScope();
  return {
    city: ["市级", "区县", "调查员"].includes(scope),
    county: ["区县", "调查员"].includes(scope),
    township: scope === "调查员",
    community: scope === "调查员",
  };
}

function updateRegionFields(clearHidden = true) {
  const visible = regionVisibility();
  Object.entries(visible).forEach(([field, visibleField]) => {
    $("field-" + field).classList.toggle("hidden", !visibleField);
    if (!visibleField && clearHidden && regionPickers[field]) regionPickers[field].clear();
  });
  $("fProvince").value = regionCatalog.defaultProvince || "贵州省";
}

function renderRegionHint() {
  const scope = selectedScope();
  let text = "";
  if (scope === "调查员") text = "必须选择标准调查点的完整四级区域。";
  if (scope === "区县") text = "必须选择标准数据中的市和县。";
  if (scope === "市级") text = "必须选择标准数据中的市。";
  if (scope === "省级") text = "省级管理范围固定为贵州省。";
  $("regionHint").textContent = text;
}

function handleScopeChange(value) {
  if (!accountTypesForScope(value).includes(visibleAccountType())) {
    setAccountType(defaultAccountType(value));
  }
  renderScopeChoices();
  renderAccountTypeChoices();
  updateRegionFields(true);
  refreshRegionOptions();
  renderRoleHint();
}

function handleAccountTypeChange(value) {
  if (value === "系统管理员") {
    setScope("省级");
  } else if (value === "业务管理员" && !["省级", "市级", "区县"].includes(selectedScope())) {
    setScope("省级");
  } else if (value === "普通用户" && selectedScope() !== "调查员") {
    setScope("调查员");
  }
  renderScopeChoices();
  renderAccountTypeChoices();
  updateRegionFields(true);
  refreshRegionOptions();
  renderRoleHint();
}

async function loadRegionCatalog() {
  if (regionCatalog.loaded) return regionCatalog;
  if (!regionCatalogPromise) {
    regionCatalogPromise = (async () => {
      const r = await fetch("/api/admin/whitelist/region-points", { headers: authHeader() });
      if (await handle401(r)) throw new Error("登录已失效");
      if (!r.ok) throw new Error("HTTP " + r.status);
      const data = await r.json();
      regionCatalog = {
        points: data.points || [],
        defaultProvince: data.default_province || "贵州省",
        loaded: true,
      };
      return regionCatalog;
    })();
  }
  try {
    return await regionCatalogPromise;
  } catch (e) {
    regionCatalogPromise = null;
    throw e;
  }
}

function createRegionPickers() {
  const make = (field, placeholder) => new SearchSelect({
    root: $("select-" + field),
    input: $("search-" + field),
    hidden: $("f" + field.charAt(0).toUpperCase() + field.slice(1)),
    menu: $("menu-" + field),
    placeholder,
    onChange: function() {
      if (field === "city") {
        regionPickers.county.clear();
        regionPickers.township.clear();
        regionPickers.community.clear();
      }
      if (field === "county") {
        regionPickers.township.clear();
        regionPickers.community.clear();
      }
      if (field === "township") regionPickers.community.clear();
      renderRoleHint();
      refreshRegionOptions();
    },
  });

  regionPickers = {
    city: make("city", "搜索市/州"),
    county: make("county", "搜索县/区"),
    township: make("township", "搜索乡镇/街道"),
    community: make("community", "搜索社区/村"),
  };
}

function checkPhoneDuplicate() {
  const phone = $("fPhone").value.trim();
  const status = $("phoneStatus");
  status.textContent = "";
  status.className = "field-hint";
  if (!/^1[3-9]\d{9}$/.test(phone)) return;
  const existing = allItems.find(item => item.phone === phone);
  if (!existing) return;
  if (existing.active) {
    status.textContent = "该手机号已启用，请直接编辑。";
  } else {
    status.textContent = "该手机号已停用，可编辑后启用。";
  }
  status.classList.add("warn");
}

function locateEntry(phone) {
  const entry = allItems.find(item => item.phone === phone);
  if (!entry) return;
  if (!entry.active) $("showInactive").checked = true;
  const visible = $("showInactive").checked ? allItems : allItems.filter(item => item.active);
  const index = visible.findIndex(item => item.phone === phone);
  currentPage = Math.floor(index / pageSize) + 1;
  renderTable();
  setTimeout(() => {
    const row = document.querySelector('tr.main-row[data-phone="' + CSS.escape(phone) + '"]');
    if (row) row.scrollIntoView({ block: "center", behavior: "smooth" });
  }, 80);
}

function configureWhitelistForm() {
  if (!regionPickers) createRegionPickers();
  document.querySelectorAll('input[name="fAdminLevel"]').forEach(input => {
    input.addEventListener("change", () => handleScopeChange(input.value));
  });
  document.querySelectorAll('input[name="fSysRole"]').forEach(input => {
    input.addEventListener("change", () => handleAccountTypeChange(input.value));
  });
  $("fPhone").addEventListener("blur", checkPhoneDuplicate);
  $("fPhone").addEventListener("input", () => {
    $("phoneStatus").textContent = "";
    $("phoneStatus").className = "field-hint";
  });
  renderScopeChoices();
}

async function prepareEntryModal(title, entry) {
  try {
    await loadRegionCatalog();
  } catch (e) {
    showMsg("标准调查点加载失败：" + e.message, "err");
    return;
  }

  editPhone = entry ? entry.phone : null;
  originalEntry = entry ? { ...entry } : null;
  $("modalTitle").textContent = title;
  $("fPhone").value = entry ? entry.phone : "";
  $("fPhone").disabled = !!entry;
  $("phoneStatus").textContent = "";
  $("phoneStatus").className = "field-hint";
  $("fName").value = entry ? entry.name : "";
  $("fRemark").value = entry ? (entry.remark || "") : "";

  const allowed = allowedScopeOptions();
  const level = entry && allowed.includes(entry.admin_level)
    ? entry.admin_level
    : (allowed.includes("调查员") ? "调查员" : (allowed[0] || ""));
  setScope(level);
  const role = isSystemAdmin()
    ? (entry ? (entry.sys_role || defaultAccountType(level)) : defaultAccountType(level))
    : defaultAccountType(level);
  setAccountType(role);
  renderScopeChoices();
  renderAccountTypeChoices();
  updateRegionFields(false);

  const selectedCity = entry ? (entry.city || "") : "";
  const selectedCounty = entry ? (entry.county || "") : "";
  const selectedTownship = entry ? (entry.township || "") : "";
  const selectedCommunity = entry ? (entry.community || "") : "";
  const points = actorRegionPoints();
  regionPickers.city.setOptions(optionList(points, "city", {}, ""), selectedCity);
  regionPickers.county.setOptions(optionList(points, "county", { city: selectedCity }, ""), selectedCounty);
  regionPickers.township.setOptions(optionList(points, "township", { city: selectedCity, county: selectedCounty }, ""), selectedTownship);
  regionPickers.community.setOptions(optionList(points, "community", { city: selectedCity, county: selectedCounty, township: selectedTownship }, "township"), selectedCommunity);
  updateRegionFields(false);
  renderRoleHint();
  renderRegionHint();
  $("editModal").classList.add("open");
}

function closeModal() {
  $("editModal").classList.remove("open");
  originalEntry = null;
}

async function saveEntry() {
  const level = selectedScope();
  const role = selectedAccountType();
  const province = ($("fProvince").value || regionCatalog.defaultProvince || "贵州省").trim();
  const entry = {
    phone: $("fPhone").value.trim(),
    name: $("fName").value.trim(),
    province,
    city: regionPickers.city.value().trim(),
    county: regionPickers.county.value().trim(),
    township: regionPickers.township.value().trim(),
    community: regionPickers.community.value().trim(),
    admin_level: level,
    remark: $("fRemark").value.trim(),
  };
  if (isSystemAdmin()) entry.sys_role = role;

  if (!/^1[3-9]\d{9}$/.test(entry.phone)) {
    showMsg("请输入正确的 11 位手机号", "err");
    return;
  }
  if (!entry.name) {
    showMsg("请填写姓名", "err");
    return;
  }
  if (!level || !role) {
    showMsg("请先选择管理范围和账号类型", "err");
    return;
  }

  const oldRole = originalEntry ? (originalEntry.sys_role || "普通用户") : "";
  if (editPhone && oldRole && oldRole !== role) {
    const action = role === "系统管理员" ? "扩大为全部后台权限" : (oldRole === "系统管理员" ? "收回全部后台权限" : "变更");
    if (!confirm("确认将账号类型从 " + oldRole + " 变更为 " + role + "？\n该操作会" + action + "。")) return;
  }
  if (!editPhone && role === "系统管理员") {
    if (!confirm("该账号将拥有全部后台权限，包括白名单、审计、KB、LLM 和测验管理。\n确认新增？")) return;
  }

  const method = editPhone ? "PUT" : "POST";
  const url = editPhone ? "/api/admin/whitelist/" + encodeURIComponent(editPhone) : "/api/admin/whitelist";
  try {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", ...authHeader() },
      body: JSON.stringify(entry),
    });
    if (await handle401(r)) return;
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: "HTTP " + r.status }));
      throw new Error(err.detail || "HTTP " + r.status);
    }
    closeModal();
    showMsg((editPhone ? "已更新" : "已新增") + " " + entry.phone.slice(0, 3) + "****", "ok");
    loadItems();
  } catch (e) {
    showMsg("保存失败：" + e.message, "err");
  }
}
