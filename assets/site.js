// Markeert dat JS leeft. De onthullingsanimaties hangen aan .js, zodat de
// pagina zonder JS gewoon volledig zichtbaar is in plaats van onzichtbaar.
document.documentElement.classList.add("js");

const menuToggle = document.querySelector("[data-menu-toggle]");
const siteNav = document.querySelector("[data-site-nav]");

function sluitMenu() {
  siteNav?.classList.remove("is-open");
  document.body.classList.remove("menu-open");
  menuToggle?.setAttribute("aria-expanded", "false");
}

menuToggle?.addEventListener("click", () => {
  const open = siteNav?.classList.toggle("is-open") || false;
  document.body.classList.toggle("menu-open", open);
  menuToggle.setAttribute("aria-expanded", String(open));
});

siteNav?.querySelectorAll("a").forEach((link) => link.addEventListener("click", sluitMenu));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") sluitMenu();
});

// Scroll-onthulling. Elementen worden één keer zichtbaar gemaakt en daarna
// losgelaten, zodat er niets blijft herberekenen tijdens het scrollen.
const wilBeweging = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const teOnthullen = document.querySelectorAll(".reveal, .reveal-group");

function onthulAlles() {
  teOnthullen.forEach((el) => el.classList.add("is-in"));
}

if (wilBeweging && "IntersectionObserver" in window) {
  const waarnemer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-in");
        waarnemer.unobserve(entry.target);
      });
    },
    { rootMargin: "0px 0px -8% 0px", threshold: 0.12 }
  );
  teOnthullen.forEach((el) => waarnemer.observe(el));

  // Vangnet. Tekst die achter een animatie blijft hangen is erger dan geen
  // animatie: na vier seconden is alles hoe dan ook zichtbaar.
  window.setTimeout(onthulAlles, 4000);
} else {
  onthulAlles();
}

// Netlify Forms levert de inzending af; wij melden alleen de intentie aan GA4
// voordat de pagina navigeert.
document.querySelectorAll("[data-track-form]").forEach((form) => {
  form.addEventListener("submit", () => {
    const onderwerp = new FormData(form).get("assunto") || "onbekend";
    window.gtag?.("event", "generate_lead", {
      form_name: form.getAttribute("name") || "contato",
      assunto: String(onderwerp)
    });
  });
});

document.querySelectorAll('a[href*="wa.me"]').forEach((link) => {
  link.addEventListener("click", () => {
    window.gtag?.("event", "contact_whatsapp", { link_url: link.getAttribute("href") || "" });
  });
});

function maakIconen() {
  window.lucide?.createIcons?.();
}

maakIconen();
window.addEventListener("load", maakIconen);
