const WRITE_SECRET_KEY = "phage_digest_write_secret";

function getWriteSecret() {
  return localStorage.getItem(WRITE_SECRET_KEY) || "";
}

document.querySelectorAll("[data-write-secret-field]").forEach((el) => {
  el.value = getWriteSecret();
});

const saveSecretBtn = document.getElementById("save-write-secret");
if (saveSecretBtn) {
  const input = document.getElementById("write-secret-input");
  input.value = getWriteSecret();
  saveSecretBtn.addEventListener("click", () => {
    localStorage.setItem(WRITE_SECRET_KEY, input.value);
    saveSecretBtn.textContent = "Saved";
    setTimeout(() => (saveSecretBtn.textContent = "Save in this browser"), 1500);
  });
}

// ---- Folders / save-to-folder ----

function writeSecretHeaders(extra) {
  return Object.assign({ "X-Write-Secret": getWriteSecret() }, extra || {});
}

function closeAllPopovers(except) {
  document.querySelectorAll("[data-save-popover]").forEach((pop) => {
    if (pop !== except) {
      pop.hidden = true;
      const btn = pop.previousElementSibling;
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

document.addEventListener("click", (e) => {
  if (!e.target.closest("[data-save-wrap]")) closeAllPopovers();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllPopovers();
});

function updateSaveButtonState(wrap) {
  const btn = wrap.querySelector("[data-save-btn]");
  const anyChecked = !!wrap.querySelector("[data-folder-checkbox]:checked");
  btn.textContent = anyChecked ? "✓ Saved" : "+ Save";
  btn.classList.toggle("has-folders", anyChecked);
}

function bumpFolderCount(folderId, delta) {
  document.querySelectorAll(`[data-folder-count="${folderId}"]`).forEach((el) => {
    el.textContent = String(Math.max(0, parseInt(el.textContent, 10) + delta));
  });
}

document.querySelectorAll("[data-folder-checkbox]").forEach((checkbox) => {
  checkbox.addEventListener("change", checkboxChangeHandler(checkbox));
});

function folderListItemHTML(folder) {
  return `<a href="/search?folder=${folder.id}">
      <span data-folder-name-label="${folder.id}"></span>
      <span class="folder-count" data-folder-count="${folder.id}">0</span>
    </a>
    <button type="button" class="folder-icon-btn" data-folder-rename-btn data-folder-id="${folder.id}" aria-label="Rename folder">&#9998;</button>
    <button type="button" class="folder-icon-btn" data-folder-delete-btn data-folder-id="${folder.id}" aria-label="Delete folder">&#10005;</button>`;
}

function addFolderToSidebar(folder) {
  const list = document.querySelector("[data-folder-list]");
  if (!list) return;
  const empty = list.querySelector("[data-folder-list-empty]");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.dataset.folderListItem = folder.id;
  li.innerHTML = folderListItemHTML(folder);
  li.querySelector("[data-folder-name-label]").textContent = folder.name;
  list.appendChild(li);
  bindFolderActionButtons(li);
}

function addFolderToPopover(popover, folder, checked) {
  const list = popover.querySelector("[data-folder-checklist]");
  const empty = list.querySelector("[data-folder-list-empty]");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.innerHTML = `<label>
      <input type="checkbox" data-folder-checkbox value="${folder.id}" ${checked ? "checked" : ""}>
      <span data-folder-name-label="${folder.id}"></span>
    </label>`;
  li.querySelector("[data-folder-name-label]").textContent = folder.name;
  const checkbox = li.querySelector("[data-folder-checkbox]");
  checkbox.addEventListener("change", checkboxChangeHandler(checkbox));
  list.appendChild(li);
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
        { method, headers: writeSecretHeaders() }
      );
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      updateSaveButtonState(wrap);
      bumpFolderCount(folderId, checkbox.checked ? 1 : -1);
    } catch (err) {
      checkbox.checked = !checkbox.checked;
      alert("Couldn't update folder — check your write secret in Settings.");
    } finally {
      checkbox.disabled = false;
    }
  };
}

function addFolderEverywhere(folder, originWrap) {
  addFolderToSidebar(folder);
  document.querySelectorAll("[data-save-popover]").forEach((popover) => {
    const wrap = popover.closest("[data-save-wrap]");
    addFolderToPopover(popover, folder, wrap === originWrap);
    if (wrap === originWrap) updateSaveButtonState(wrap);
  });
}

async function createFolder(name) {
  const resp = await fetch("/folders", {
    method: "POST",
    headers: writeSecretHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ name }),
  });
  if (resp.status === 409) {
    alert("A folder with that name already exists.");
    return null;
  }
  if (!resp.ok) {
    alert("Couldn't create folder — check your write secret in Settings.");
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
    const wrap = form.closest("[data-save-wrap]");
    const card = form.closest("[data-paper-id]");
    const folder = await createFolder(name);
    if (!folder) return;
    addFolderEverywhere(folder, wrap);
    if (card) {
      try {
        await fetch(`/papers/${encodeURIComponent(card.dataset.paperId)}/folders/${folder.id}`, {
          method: "PUT",
          headers: writeSecretHeaders(),
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

const sidebarNewFolderForm = document.querySelector("[data-new-folder-sidebar-form]");
if (sidebarNewFolderForm) {
  sidebarNewFolderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = sidebarNewFolderForm.querySelector("[data-new-folder-sidebar-input]");
    const name = input.value.trim();
    if (!name) return;
    const folder = await createFolder(name);
    if (!folder) return;
    addFolderEverywhere(folder, null);
    input.value = "";
  });
}

function bindFolderActionButtons(scope) {
  scope.querySelectorAll("[data-folder-rename-btn]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const folderId = btn.dataset.folderId;
      const current = document.querySelector(`[data-folder-name-label="${folderId}"]`).textContent;
      const name = prompt("Rename folder", current);
      if (!name || !name.trim() || name.trim() === current) return;
      try {
        const resp = await fetch(`/folders/${folderId}`, {
          method: "PATCH",
          headers: writeSecretHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ name: name.trim() }),
        });
        if (resp.status === 409) {
          alert("A folder with that name already exists.");
          return;
        }
        if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
        document.querySelectorAll(`[data-folder-name-label="${folderId}"]`).forEach((el) => {
          el.textContent = name.trim();
        });
      } catch (err) {
        alert("Couldn't rename folder — check your write secret in Settings.");
      }
    });
  });

  scope.querySelectorAll("[data-folder-delete-btn]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const folderId = btn.dataset.folderId;
      const name = document.querySelector(`[data-folder-name-label="${folderId}"]`).textContent;
      if (!confirm(`Delete "${name}"? Papers inside it won't be deleted.`)) return;
      try {
        const resp = await fetch(`/folders/${folderId}`, {
          method: "DELETE",
          headers: writeSecretHeaders(),
        });
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
        alert("Couldn't delete folder — check your write secret in Settings.");
      }
    });
  });
}

bindFolderActionButtons(document);
