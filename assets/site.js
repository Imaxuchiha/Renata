const popup = document.querySelector("[data-whatsapp-popup]");
const popupClose = document.querySelector("[data-whatsapp-close]");
const menuToggle = document.querySelector("[data-menu-toggle]");
const siteNav = document.querySelector("[data-site-nav]");
let popupShown = sessionStorage.getItem("renataWhatsAppPopupClosed") === "true";

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

function showPopupAtScrollDepth() {
  if (!popup || popupShown) return;

  const pageHeight = document.documentElement.scrollHeight - window.innerHeight;
  if (pageHeight <= 0) return;

  const scrolled = window.scrollY / pageHeight;
  if (scrolled >= 0.66) {
    popup.classList.add("is-visible");
    popupShown = true;
  }
}

popupClose?.addEventListener("click", () => {
  popup?.classList.remove("is-visible");
  sessionStorage.setItem("renataWhatsAppPopupClosed", "true");
});

window.addEventListener("scroll", showPopupAtScrollDepth, { passive: true });

document.querySelectorAll("[data-contact-form]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const name = data.get("name") || "";
    const email = data.get("email") || "";
    const service = data.get("service") || "";
    const message = data.get("message") || "";
    const text = [
      "Hi Renata, I would like to book a free strategy call.",
      `Name: ${name}`,
      `Email: ${email}`,
      `Service: ${service}`,
      `Message: ${message}`
    ].join("\n");

    window.location.href = `https://wa.me/351935328206?text=${encodeURIComponent(text)}`;
  });
});

function createIconsWhenReady() {
  if (window.lucide?.createIcons) {
    window.lucide.createIcons();
  }
}

createIconsWhenReady();
window.addEventListener("load", createIconsWhenReady);
