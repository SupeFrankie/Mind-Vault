document.querySelectorAll("[data-call-start]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = form.querySelector("button[type='submit']");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Opening call…";
    try {
      const response = await fetch(form.action, {
        method: "POST",
        headers: { "X-CSRFToken": form.querySelector("[name='csrfmiddlewaretoken']")?.value || "" },
        credentials: "same-origin",
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || "The call could not be started.");
      window.location.assign(result.url);
    } catch (error) {
      button.disabled = false;
      button.textContent = originalText;
      window.alert(error.message);
    }
  });
});
