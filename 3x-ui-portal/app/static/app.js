document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const input = document.getElementById(button.dataset.copy);
  const status = document.querySelector(".copy-status");
  try {
    await navigator.clipboard.writeText(input.value);
    status.textContent = "Ссылка скопирована";
    button.textContent = "Скопировано ✓";
  } catch {
    input.select();
    document.execCommand("copy");
    status.textContent = "Ссылка скопирована";
  }
});
