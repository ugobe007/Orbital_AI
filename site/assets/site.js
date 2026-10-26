(() => {
  "use strict";

  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ── Scroll reveal ──────────────────────────────────────────────────────────
  const revealables = document.querySelectorAll(".reveal");
  const revealAll = () => revealables.forEach((el) => el.classList.add("is-visible"));
  if (reduceMotion || !("IntersectionObserver" in window)) {
    revealAll();
  } else {
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("is-visible");
          io.unobserve(entry.target);
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );
    revealables.forEach((el) => io.observe(el));
    // Never leave content hidden: anchor jumps and full-page screenshots can skip the observer.
    window.addEventListener("hashchange", revealAll);
    setTimeout(revealAll, 4000);
  }

  // ── Count-up stats ─────────────────────────────────────────────────────────
  const counters = document.querySelectorAll("[data-count]");
  const render = (el, n) => {
    el.textContent = `${el.dataset.prefix || ""}${n}${el.dataset.suffix || ""}`;
  };
  const countUp = (el) => {
    const target = Number(el.dataset.count);
    const start = performance.now();
    const duration = 1200;
    const tick = (now) => {
      const t = Math.min(1, (now - start) / duration);
      render(el, Math.round(target * (1 - Math.pow(1 - t, 3))));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  if (!reduceMotion && "IntersectionObserver" in window) {
    const co = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        co.unobserve(entry.target);
        countUp(entry.target);
      }
    }, { threshold: 0.6 });
    counters.forEach((el) => co.observe(el));
  }

  // ── Hero photo rotation ────────────────────────────────────────────────────
  const slides = document.querySelectorAll(".hero-slide");
  if (slides.length > 1 && !reduceMotion) {
    let current = 0;
    setInterval(() => {
      if (document.hidden) return;
      slides[current].classList.remove("is-active");
      current = (current + 1) % slides.length;
      slides[current].classList.add("is-active");
    }, 6000);
  }

  // ── Mobile menu ────────────────────────────────────────────────────────────
  const toggle = document.querySelector(".nav-toggle");
  const menu = document.getElementById("mobile-menu");
  if (toggle && menu) {
    const setOpen = (open) => {
      menu.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    };
    toggle.addEventListener("click", () => setOpen(menu.hidden));
    menu.addEventListener("click", (e) => {
      if (e.target.closest("a")) setOpen(false);
    });
  }

  // ── Contact dialog ─────────────────────────────────────────────────────────
  const dialog = document.getElementById("contact-dialog");
  const form = document.getElementById("contact-form");
  if (!dialog || !form) return;

  const COPY = {
    "early-access": ["Start a pilot", "Tell us about your fleet and we'll scope a 14-day pilot."],
    general: ["Request a demo", "We'll walk you through the live console with your use case in mind."],
    partnership: ["OEM partnership", "Tell us about your robots and APIs, and how you'd like to work together."],
  };
  const title = document.getElementById("contact-title");
  const lede = document.getElementById("contact-lede");
  const status = document.getElementById("contact-status");
  const topic = form.elements.topic;
  const submit = form.querySelector('button[type="submit"]');

  const applyTopic = (value) => {
    const [t, l] = COPY[value] || COPY.general;
    title.textContent = t;
    lede.textContent = l;
  };
  const setStatus = (text, kind) => {
    status.textContent = text;
    status.className = `contact-status${kind ? ` is-${kind}` : ""}`;
  };

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-contact]");
    if (!opener) return;
    e.preventDefault();
    topic.value = COPY[opener.dataset.contact] ? opener.dataset.contact : "general";
    applyTopic(topic.value);
    setStatus("");
    submit.disabled = false;
    if (menu && !menu.hidden) toggle.click();
    dialog.showModal();
    form.elements.name.focus();
  });
  topic.addEventListener("change", () => applyTopic(topic.value));
  dialog.querySelectorAll("[data-close]").forEach((btn) =>
    btn.addEventListener("click", () => dialog.close()),
  );
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) dialog.close();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    if (!data.name.trim() || !data.email.trim()) {
      setStatus("Please add your name and work email.", "error");
      return;
    }
    submit.disabled = true;
    setStatus("Sending…");
    try {
      const resp = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      const body = await resp.json().catch(() => ({}));
      if (resp.ok && body.ok) {
        form.reset();
        topic.value = data.topic;
        setStatus("Thanks — we've got it and will be in touch shortly.", "ok");
        return;
      }
      setStatus(body.error || "Something went wrong. Please email hello@orbital-ai.io.", "error");
    } catch {
      setStatus("Network error. Please try again or email hello@orbital-ai.io.", "error");
    }
    submit.disabled = false;
  });
})();
