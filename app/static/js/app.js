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

document.querySelectorAll("[data-favorite-btn]").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const card = btn.closest("[data-paper-id]");
    const paperId = card.dataset.paperId;
    btn.disabled = true;
    try {
      const resp = await fetch(`/papers/${encodeURIComponent(paperId)}/favorite`, {
        method: "POST",
        headers: { "X-Write-Secret": getWriteSecret() },
      });
      if (!resp.ok) throw new Error(`Request failed: ${resp.status}`);
      const data = await resp.json();
      btn.textContent = data.is_favorite ? "★" : "☆";
      btn.classList.toggle("favorited", data.is_favorite);
    } catch (err) {
      alert("Couldn't update favorite — check your write secret in Settings.");
    } finally {
      btn.disabled = false;
    }
  });
});
