
document.querySelectorAll("[data-switch]").forEach((group) => {
  group.querySelectorAll("[data-pill]").forEach((btn) => {
    btn.addEventListener("click", () => {
      group.querySelectorAll("[data-pill]").forEach((b) => {
        b.classList.toggle("is-on", b === btn);
      });
      group.querySelectorAll("[data-pane]").forEach((pane) => {
        pane.classList.toggle("is-shown", pane.dataset.pane === btn.dataset.pill);
      });
    });
  });
});
