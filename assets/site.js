const popup = document.querySelector("[data-whatsapp-popup]");
const popupClose = document.querySelector("[data-whatsapp-close]");
let popupShown = sessionStorage.getItem("renataWhatsAppPopupClosed") === "true";

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

if (window.lucide) {
  window.lucide.createIcons();
}
