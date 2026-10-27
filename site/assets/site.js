(() => {
  "use strict";

  const form = document.getElementById("contact-form");
  if (!form) return;

  const status = document.getElementById("contact-status");
  const submit = form.querySelector('button[type="submit"]');
  const topic = form.elements.topic;

  const setStatus = (text, kind) => {
    status.textContent = text;
    status.className = `contact-status${kind ? ` is-${kind}` : ""}`;
  };

  document.addEventListener("click", (e) => {
    const link = e.target.closest("[data-topic]");
    if (link) topic.value = link.dataset.topic;
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
        setStatus("Thanks, we've got it and will be in touch shortly.", "ok");
        submit.disabled = false;
        return;
      }
      setStatus(body.error || "Something went wrong. Please email hello@orbital-ai.io.", "error");
    } catch {
      setStatus("Network error. Please try again or email hello@orbital-ai.io.", "error");
    }
    submit.disabled = false;
  });
})();
