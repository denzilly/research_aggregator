// Kept in sync with FOLDER_COLORS in app/queries.py — used when rendering
// folders created client-side (the manage-folders popover's "new folder"
// swatches are server-rendered from that same list already).
const FOLDER_COLORS = ["#60a5fa", "#4ade80", "#fbbf24", "#f472b6", "#a78bfa", "#f87171", "#38bdf8", "#94a3b8"];

// ---- Mobile off-canvas drawer (Queries / Folders / nav, behind the hamburger) ----

const drawerToggle = document.querySelector("[data-drawer-toggle]");
const drawerSidebar = document.querySelector("[data-app-sidebar]");
const drawerOverlay = document.querySelector("[data-drawer-overlay]");

function setDrawerOpen(open) {
  if (!drawerSidebar || !drawerOverlay || !drawerToggle) return;
  drawerSidebar.classList.toggle("open", open);
  drawerOverlay.classList.toggle("open", open);
  drawerToggle.setAttribute("aria-expanded", String(open));
  document.body.style.overflow = open ? "hidden" : "";
}

if (drawerToggle) {
  drawerToggle.addEventListener("click", () => {
    setDrawerOpen(!drawerSidebar.classList.contains("open"));
  });
  drawerOverlay.addEventListener("click", () => setDrawerOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") setDrawerOpen(false);
  });
}

// ---- Popovers (save-to-folder + manage-folders share one open-at-a-time model) ----

function closeAllPopovers(except) {
  document.querySelectorAll("[data-save-popover], [data-folder-manage-popover]").forEach((pop) => {
    if (pop !== except) {
      pop.hidden = true;
      // The folder-manage popover is reparented to <body> on open (see
      // below), so it no longer has its trigger button as a DOM sibling —
      // _ownerButton is set once, at bind time, so this still finds it.
      const btn = pop._ownerButton || pop.previousElementSibling;
      if (btn) btn.setAttribute("aria-expanded", "false");
    }
  });
}

document.querySelectorAll("[data-save-btn]").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const popover = btn.nextElementSibling;
    const opening = popover.hidden;
    closeAllPopovers(opening ? popover : null);
    popover.hidden = !opening;
    btn.setAttribute("aria-expanded", String(opening));
  });
});

document.querySelectorAll("[data-folder-manage-btn]").forEach((btn) => {
  const popover = btn.nextElementSibling;
  popover._ownerButton = btn;

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const opening = popover.hidden;
    closeAllPopovers(opening ? popover : null);
    if (opening) {
      // Reparented to <body> (once) rather than left nested in the
      // sidebar: some browsers were compositing this fixed-positioned
      // popover *behind* main-column content (paper cards) once it
      // visually overlapped them on narrower windows — background/z-index
      // were correct on paper (verified via computed styles), so this was
      // a paint-order bug tied to its position in the sidebar's DOM
      // subtree, not a CSS value. Moving it out sidesteps that subtree
      // entirely instead of fighting it with more z-index/layer hints.
      if (popover.parentElement !== document.body) {
        document.body.appendChild(popover);
      }
      // Fixed-positioned (not CSS-anchored) because the sidebar scrolls
      // (overflow-y: auto for its sticky behavior), which would otherwise
      // clip an absolutely-positioned popover. Clamped to the viewport so it
      // doesn't run off-screen on narrow (mobile drawer) widths.
      const rect = btn.getBoundingClientRect();
      popover.hidden = false;
      const popoverWidth = popover.offsetWidth;
      const maxLeft = window.innerWidth - popoverWidth - 8;
      const left = Math.max(8, Math.min(rect.left, maxLeft));
      popover.style.top = `${rect.bottom + 6}px`;
      popover.style.left = `${left}px`;
    } else {
      popover.hidden = true;
    }
    btn.setAttribute("aria-expanded", String(opening));
  });
});

document.addEventListener("click", (e) => {
  if (
    !e.target.closest("[data-save-wrap]") &&
    !e.target.closest("[data-folder-manage-wrap]") &&
    !e.target.closest("[data-folder-manage-popover]") // reparented to <body>, so no longer inside the wrap above
  ) {
    closeAllPopovers();
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllPopovers();
});

// ---- Save-to-folder ----

function updateSaveButtonState(wrap) {
  const btn = wrap.querySelector("[data-save-btn]");
  const anyChecked = !!wrap.querySelector("[data-folder-checkbox]:checked");
  btn.querySelector(".material-symbols-outlined").textContent = anyChecked ? "bookmark" : "bookmark_border";
  btn.querySelector("span:last-child").textContent = anyChecked ? "Saved" : "Save";
  btn.classList.toggle("has-folders", anyChecked);
}

function bumpFolderCount(folderId, delta) {
  document.querySelectorAll(`[data-folder-count="${folderId}"]`).forEach((el) => {
    el.textContent = String(Math.max(0, parseInt(el.textContent, 10) + delta));
  });
}

function checkboxChangeHandler(checkbox) {
  return async () => {
    const wrap = checkbox.closest("[data-save-wrap]");
    const card = checkbox.closest("[data-paper-id]");
    const paperId = card.dataset.paperId;
    const folderId = checkbox.value;
    const method = checkbox.checked ? "PUT" : "DELETE";
    checkbox.disabled = true;
    try {
      const resp = await fetch(
        `/papers/${encodeURIComponent(paperId)}/folders/${folderId}`,
        { method }
      );
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      updateSaveButtonState(wrap);
      bumpFolderCount(folderId, checkbox.checked ? 1 : -1);
    } catch (err) {
      checkbox.checked = !checkbox.checked;
      alert("Couldn't update folder.");
    } finally {
      checkbox.disabled = false;
    }
  };
}

document.querySelectorAll("[data-folder-checkbox]").forEach((checkbox) => {
  checkbox.addEventListener("change", checkboxChangeHandler(checkbox));
});

function addFolderToPopover(popover, folder, checked) {
  const list = popover.querySelector("[data-folder-checklist]");
  const empty = list.querySelector("[data-folder-list-empty]");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.innerHTML = `<label>
      <input type="checkbox" data-folder-checkbox value="${folder.id}" ${checked ? "checked" : ""}>
      <span class="folder-dot" data-folder-color-dot="${folder.id}"></span>
      <span data-folder-name-label="${folder.id}"></span>
    </label>`;
  li.querySelector("[data-folder-name-label]").textContent = folder.name;
  li.querySelector("[data-folder-color-dot]").style.backgroundColor = folder.color || "var(--on-surface-faint)";
  const checkbox = li.querySelector("[data-folder-checkbox]");
  checkbox.addEventListener("change", checkboxChangeHandler(checkbox));
  list.appendChild(li);
}

// ---- Sidebar folder list ----

function addFolderToSidebar(folder) {
  const list = document.querySelector("[data-folder-list]");
  if (!list) return;
  const empty = list.querySelector("[data-folder-list-empty]");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.className = "sidebar-item";
  li.dataset.folderListItem = folder.id;
  li.innerHTML = `<a href="/search?folder=${folder.id}">
      <span class="folder-dot" data-folder-color-dot="${folder.id}"></span>
      <span class="sidebar-item-name" data-folder-name-label="${folder.id}"></span>
      <span class="sidebar-count" data-folder-count="${folder.id}">0</span>
    </a>`;
  li.querySelector("[data-folder-name-label]").textContent = folder.name;
  li.querySelector("[data-folder-color-dot]").style.backgroundColor = folder.color || "var(--on-surface-faint)";
  list.appendChild(li);
}

// ---- Manage-folders popover (rename / color / delete / create) ----

function colorSwatchesHTML(folderId, selectedColor) {
  return FOLDER_COLORS.map(
    (color) => `<button type="button" class="color-swatch${color === selectedColor ? " selected" : ""}"
        style="background-color: ${color};" data-set-folder-color
        data-folder-id="${folderId}" data-color="${color}" aria-label="Set color"></button>`
  ).join("");
}

function addFolderToManagePopover(folder) {
  const list = document.querySelector("[data-folder-manage-list]");
  if (!list) return;
  const empty = list.querySelector("[data-folder-manage-empty]");
  if (empty) empty.remove();
  const item = document.createElement("div");
  item.className = "folder-manage-item";
  item.dataset.folderListItem = folder.id;
  item.innerHTML = `<div class="folder-manage-row">
      <span class="folder-dot" data-folder-color-dot="${folder.id}"></span>
      <input type="text" class="folder-manage-name-input" data-folder-rename-input data-folder-id="${folder.id}">
      <button type="button" class="sidebar-icon-btn" data-folder-delete-btn data-folder-id="${folder.id}" aria-label="Delete folder"><span class="material-symbols-outlined">delete</span></button>
    </div>
    <div class="color-palette" data-folder-color-palette="${folder.id}">${colorSwatchesHTML(folder.id, folder.color)}</div>`;
  item.querySelector("[data-folder-rename-input]").value = folder.name;
  item.querySelector("[data-folder-color-dot]").style.backgroundColor = folder.color || "var(--on-surface-faint)";
  list.appendChild(item);
  bindFolderManageScope(item);
}

function bindFolderRenameInput(input) {
  const commit = async () => {
    const folderId = input.dataset.folderId;
    const current = document.querySelector(`[data-folder-name-label="${folderId}"]`).textContent;
    const name = input.value.trim();
    if (!name || name === current) {
      input.value = current;
      return;
    }
    try {
      const resp = await fetch(`/folders/${folderId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (resp.status === 409) {
        alert("A folder with that name already exists.");
        input.value = current;
        return;
      }
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      document.querySelectorAll(`[data-folder-name-label="${folderId}"]`).forEach((el) => {
        el.textContent = name;
      });
    } catch (err) {
      alert("Couldn't rename folder.");
      input.value = current;
    }
  };
  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    }
  });
}

function bindColorSwatch(btn) {
  btn.addEventListener("click", async () => {
    const folderId = btn.dataset.folderId;
    const color = btn.dataset.color;
    try {
      const resp = await fetch(`/folders/${folderId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ color }),
      });
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      btn.closest("[data-folder-color-palette]").querySelectorAll(".color-swatch").forEach((s) => {
        s.classList.remove("selected");
      });
      btn.classList.add("selected");
      document.querySelectorAll(`[data-folder-color-dot="${folderId}"]`).forEach((dot) => {
        dot.style.backgroundColor = color;
      });
    } catch (err) {
      alert("Couldn't update folder color.");
    }
  });
}

function bindFolderDeleteButton(btn) {
  btn.addEventListener("click", async () => {
    const folderId = btn.dataset.folderId;
    const name = document.querySelector(`[data-folder-name-label="${folderId}"]`).textContent;
    if (!confirm(`Delete "${name}"? Papers inside it won't be deleted.`)) return;
    try {
      const resp = await fetch(`/folders/${folderId}`, { method: "DELETE" });
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      document.querySelectorAll(`[data-folder-list-item="${folderId}"]`).forEach((el) => el.remove());
      document.querySelectorAll(`[data-folder-checkbox][value="${folderId}"]`).forEach((checkbox) => {
        const wrap = checkbox.closest("[data-save-wrap]");
        checkbox.closest("li").remove();
        if (wrap) updateSaveButtonState(wrap);
      });
      const params = new URLSearchParams(window.location.search);
      if (params.get("folder") === folderId) {
        window.location.href = "/search";
      }
    } catch (err) {
      alert("Couldn't delete folder.");
    }
  });
}

function bindFolderManageScope(scope) {
  scope.querySelectorAll("[data-folder-rename-input]").forEach(bindFolderRenameInput);
  scope.querySelectorAll("[data-set-folder-color]").forEach(bindColorSwatch);
  scope.querySelectorAll("[data-folder-delete-btn]").forEach(bindFolderDeleteButton);
}

bindFolderManageScope(document);

document.querySelectorAll("[data-new-folder-color-palette]").forEach((palette) => {
  palette.querySelectorAll("[data-new-folder-color]").forEach((btn) => {
    btn.addEventListener("click", () => {
      palette.querySelectorAll(".color-swatch").forEach((s) => s.classList.remove("selected"));
      btn.classList.add("selected");
    });
  });
});

// ---- Create folder (shared by the manage-folders popover and each paper
// card's quick "+ Add" mini-form inside the save popover) ----

function addFolderEverywhere(folder, originWrap) {
  addFolderToSidebar(folder);
  addFolderToManagePopover(folder);
  document.querySelectorAll("[data-save-popover]").forEach((popover) => {
    const wrap = popover.closest("[data-save-wrap]");
    addFolderToPopover(popover, folder, wrap === originWrap);
    if (wrap === originWrap) updateSaveButtonState(wrap);
  });
}

async function createFolder(name, color) {
  const resp = await fetch("/folders", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, color: color || null }),
  });
  if (resp.status === 409) {
    alert("A folder with that name already exists.");
    return null;
  }
  if (!resp.ok) {
    alert("Couldn't create folder.");
    return null;
  }
  return resp.json();
}

document.querySelectorAll("[data-new-folder-form]").forEach((form) => {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = form.querySelector("[data-new-folder-input]");
    const name = input.value.trim();
    if (!name) return;
    const colorBtn = form.querySelector("[data-new-folder-color].selected");
    const color = colorBtn ? colorBtn.dataset.color : null;
    const wrap = form.closest("[data-save-wrap]");
    const card = form.closest("[data-paper-id]");
    const folder = await createFolder(name, color);
    if (!folder) return;
    addFolderEverywhere(folder, wrap);
    if (card) {
      try {
        await fetch(`/papers/${encodeURIComponent(card.dataset.paperId)}/folders/${folder.id}`, {
          method: "PUT",
        });
        bumpFolderCount(folder.id, 1);
        const checkbox = wrap.querySelector(`[data-folder-checkbox][value="${folder.id}"]`);
        if (checkbox) checkbox.checked = true;
        updateSaveButtonState(wrap);
      } catch (err) {
        alert("Folder created, but couldn't save this paper into it.");
      }
    }
    input.value = "";
  });
});

// ---- Mark as read/unread ----

function updateReadButtonState(btn, isRead) {
  btn.classList.toggle("is-read", isRead);
  btn.querySelector(".material-symbols-outlined").textContent = isRead ? "check_circle" : "radio_button_unchecked";
  btn.querySelector("[data-read-label]").textContent = isRead ? "Read" : "Mark read";
  btn.title = isRead ? "Mark as unread" : "Mark as read";
}

// Sidebar unread badges are rendered even at 0 (just hidden), so toggling a
// paper's read state can update them in place instead of waiting for the
// next full page load — same delta applied to "All queries" and to every
// query this paper belongs to (data-query-ids on the card, set server-side).
function bumpUnreadBadge(key, delta) {
  const el = document.querySelector(`[data-unread-badge="${key}"]`);
  if (!el) return;
  const next = Math.max(0, parseInt(el.textContent, 10) + delta);
  el.textContent = String(next);
  el.hidden = next === 0;
}

document.querySelectorAll("[data-read-btn]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const card = btn.closest("[data-paper-id]");
    const paperId = card.dataset.paperId;
    const queryIds = (card.dataset.queryIds || "").split(",").filter(Boolean);
    const wasRead = btn.classList.contains("is-read");
    const method = wasRead ? "DELETE" : "PUT";
    btn.disabled = true;
    try {
      const resp = await fetch(`/papers/${encodeURIComponent(paperId)}/read`, { method });
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      const result = await resp.json();
      updateReadButtonState(btn, result.read);
      const delta = result.read ? -1 : 1;
      bumpUnreadBadge("all", delta);
      queryIds.forEach((id) => bumpUnreadBadge(id, delta));
    } catch (err) {
      alert("Couldn't update read status.");
    } finally {
      btn.disabled = false;
    }
  });
});

// ---- Cite ----

async function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Fallback for non-HTTPS/older browsers.
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

document.querySelectorAll("[data-cite-btn]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const label = btn.querySelector("[data-cite-label]");
    try {
      await copyToClipboard(btn.dataset.citation);
      const original = label.textContent;
      label.textContent = "Copied!";
      setTimeout(() => (label.textContent = original), 1500);
    } catch (err) {
      alert("Couldn't copy citation to clipboard.");
    }
  });
});

// ---- Zotero ----
//
// Connection lives entirely in this browser's local storage and requests go
// straight from here to api.zotero.org (CORS-enabled from any origin) — the
// server never sees the Zotero key.

const ZOTERO_CONN_KEY = "phage_digest_zotero";
const ZOTERO_ADDED_KEY = "phage_digest_zotero_added";

function getZoteroConnection() {
  try {
    return JSON.parse(localStorage.getItem(ZOTERO_CONN_KEY) || "null");
  } catch (err) {
    return null;
  }
}

function setZoteroConnection(conn) {
  if (conn) localStorage.setItem(ZOTERO_CONN_KEY, JSON.stringify(conn));
  else localStorage.removeItem(ZOTERO_CONN_KEY);
}

function getZoteroAdded() {
  try {
    return new Set(JSON.parse(localStorage.getItem(ZOTERO_ADDED_KEY) || "[]"));
  } catch (err) {
    return new Set();
  }
}

function markZoteroAdded(paperId) {
  const added = getZoteroAdded();
  added.add(paperId);
  localStorage.setItem(ZOTERO_ADDED_KEY, JSON.stringify([...added]));
}

function renderZoteroConnectUI() {
  const connectForm = document.querySelector("[data-zotero-connect-form]");
  const connected = document.querySelector("[data-zotero-connected]");
  if (!connectForm || !connected) return;
  const conn = getZoteroConnection();
  connectForm.hidden = !!conn;
  connected.hidden = !conn;
  if (conn) connected.querySelector("[data-zotero-username]").textContent = conn.username;
}
renderZoteroConnectUI();

const connectZoteroBtn = document.getElementById("connect-zotero");
if (connectZoteroBtn) {
  connectZoteroBtn.addEventListener("click", async () => {
    const input = document.getElementById("zotero-key-input");
    const errorEl = document.querySelector("[data-zotero-error]");
    const key = input.value.trim();
    errorEl.hidden = true;
    if (!key) return;
    connectZoteroBtn.disabled = true;
    connectZoteroBtn.textContent = "Connecting…";
    try {
      const resp = await fetch(`https://api.zotero.org/keys/${encodeURIComponent(key)}`);
      if (!resp.ok) throw new Error("That key couldn't be verified.");
      const info = await resp.json();
      if (!info.access || !info.access.user || !info.access.user.library) {
        throw new Error("That key doesn't have library access — create one with read/write access.");
      }
      setZoteroConnection({ key, userID: info.userID, username: info.username });
      input.value = "";
      renderZoteroConnectUI();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.hidden = false;
    } finally {
      connectZoteroBtn.disabled = false;
      connectZoteroBtn.textContent = "Connect";
    }
  });
}

const disconnectZoteroBtn = document.getElementById("disconnect-zotero");
if (disconnectZoteroBtn) {
  disconnectZoteroBtn.addEventListener("click", () => {
    setZoteroConnection(null);
    renderZoteroConnectUI();
  });
}

const ZOTERO_LIBRARY_CATALOG = { pubmed: "PubMed", biorxiv: "bioRxiv", medrxiv: "medRxiv" };

function splitAuthorsToCreators(raw) {
  return (raw || "")
    .split(",")
    .map((name) => name.trim())
    .filter(Boolean)
    .map((name) => ({ creatorType: "author", name }));
}

function extractDOI(url) {
  const match = (url || "").match(/10\.\d{4,9}\/\S+/);
  return match ? match[0].replace(/[.,;]+$/, "") : null;
}

function buildZoteroItem(btn) {
  const { title, authors, date, url, abstract, source } = btn.dataset;
  const item = {
    itemType: source === "biorxiv" || source === "medrxiv" ? "preprint" : "journalArticle",
    title,
    creators: splitAuthorsToCreators(authors),
    abstractNote: abstract || "",
    date: date || "",
    url: url || "",
    accessDate: new Date().toISOString().slice(0, 10),
    libraryCatalog: ZOTERO_LIBRARY_CATALOG[source] || source,
  };
  const doi = extractDOI(url);
  if (doi) item.DOI = doi;
  return item;
}

function markZoteroBtnAdded(btn) {
  btn.classList.add("in-zotero");
  btn.querySelector(".material-symbols-outlined").textContent = "check_circle";
  btn.querySelector("[data-zotero-label]").textContent = "In Zotero";
}

const zoteroAdded = getZoteroAdded();
document.querySelectorAll("[data-zotero-btn]").forEach((btn) => {
  if (zoteroAdded.has(btn.dataset.paperId)) markZoteroBtnAdded(btn);

  btn.addEventListener("click", async () => {
    const conn = getZoteroConnection();
    if (!conn) {
      alert("Connect your Zotero account in Settings first.");
      return;
    }
    const label = btn.querySelector("[data-zotero-label]");
    const original = label.textContent;
    btn.disabled = true;
    label.textContent = "Adding…";
    try {
      const resp = await fetch(`https://api.zotero.org/users/${conn.userID}/items`, {
        method: "POST",
        headers: {
          "Zotero-API-Key": conn.key,
          "Content-Type": "application/json",
          "Zotero-Write-Token": crypto.randomUUID().replace(/-/g, ""),
        },
        body: JSON.stringify([buildZoteroItem(btn)]),
      });
      const result = await resp.json().catch(() => ({}));
      if (!resp.ok || !result.success || !Object.keys(result.success).length) {
        const failure = result.failed && result.failed["0"];
        throw new Error((failure && failure.message) || `Request failed: ${resp.status}`);
      }
      markZoteroBtnAdded(btn);
      markZoteroAdded(btn.dataset.paperId);
    } catch (err) {
      label.textContent = original;
      alert(`Couldn't add to Zotero: ${err.message}`);
    } finally {
      btn.disabled = false;
    }
  });
});

// ---- Relative timestamps ----

function timeAgo(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  const steps = [
    [60, "s"],
    [60, "m"],
    [24, "h"],
    [30, "d"],
    [12, "mo"],
    [Infinity, "y"],
  ];
  let value = seconds;
  let unit = "s";
  for (const [amount, label] of steps) {
    if (value < amount) {
      unit = label;
      break;
    }
    value /= amount;
  }
  const rounded = Math.floor(value);
  if (unit === "s" && rounded < 60) return "just now";
  return `${rounded}${unit} ago`;
}

document.querySelectorAll("[data-timestamp]").forEach((el) => {
  const rendered = timeAgo(el.dataset.timestamp);
  if (rendered) el.textContent = rendered;
});
