(function () {
  function bindResolutionForms(root) {
    const scope = root || document;
    for (const form of scope.querySelectorAll("[data-resolution-form]")) {
      if (form.dataset.resolutionBound === "1") {
        continue;
      }
      form.dataset.resolutionBound = "1";

      const followUpFields = form.querySelector("[data-resolution-follow-up]");
      const nextStep = form.querySelector('input[name="next_step"]');
      const nextDueAt = form.querySelector('input[name="next_due_at"]');
      const update = function () {
        const selected = form.querySelector('input[name="resolution_path"]:checked');
        const schedulesFollowUp = selected?.value === "follow_up";
        if (followUpFields instanceof HTMLElement) {
          followUpFields.hidden = !schedulesFollowUp;
        }
        for (const input of [nextStep, nextDueAt]) {
          if (input instanceof HTMLInputElement) {
            input.disabled = !schedulesFollowUp;
            input.required = schedulesFollowUp;
          }
        }
      };

      for (const input of form.querySelectorAll('input[name="resolution_path"]')) {
        input.addEventListener("change", update);
      }
      update();
    }
  }

  function updateSelectedWorkItem(trigger) {
    const selected = trigger.closest("[data-work-item-case]");
    if (!(selected instanceof HTMLElement)) {
      return;
    }
    for (const item of document.querySelectorAll("[data-work-item-case]")) {
      const isSelected = item === selected;
      item.classList.toggle("is-selected", isSelected);
      if (isSelected) {
        item.setAttribute("aria-current", "true");
      } else {
        item.removeAttribute("aria-current");
      }
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindResolutionForms(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindResolutionForms(event.target);
  });

  document.body.addEventListener("htmx:afterRequest", function (event) {
    if (event.detail.successful) {
      updateSelectedWorkItem(event.detail.elt);
    }
  });
})();
