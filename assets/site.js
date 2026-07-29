const menuToggle = document.querySelector("[data-menu-toggle]");
const siteNav = document.querySelector("[data-site-nav]");

menuToggle?.addEventListener("click", () => {
  const isOpen = siteNav?.classList.toggle("is-open") || false;
  document.body.classList.toggle("menu-open", isOpen);
  menuToggle.setAttribute("aria-expanded", String(isOpen));
});

siteNav?.querySelectorAll("a").forEach((link) => {
  link.addEventListener("click", () => {
    siteNav.classList.remove("is-open");
    document.body.classList.remove("menu-open");
    menuToggle?.setAttribute("aria-expanded", "false");
  });
});

// Netlify Forms delivers the submission; we only report the intent to GA4 before the page navigates.
document.querySelectorAll("[data-track-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    const assunto = new FormData(form).get("assunto") || "desconhecido";
    window.gtag?.("event", "generate_lead", {
      form_name: form.getAttribute("name") || "contato",
      assunto: String(assunto)
    });
  });
});

document.querySelectorAll('a[href*="wa.me"]').forEach((link) => {
  link.addEventListener("click", () => {
    window.gtag?.("event", "contact_whatsapp", { link_url: link.getAttribute("href") || "" });
  });
});

function createIconsWhenReady() {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
}

createIconsWhenReady();
window.addEventListener("load", createIconsWhenReady);
