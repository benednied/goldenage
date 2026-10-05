(function () {
  function focusUpdatedContent(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }

    const liveMessage = target.querySelector(
      '.message[role="alert"], .message[role="status"]',
    );
    const heading = target.querySelector("h2, h3");
    const focusTarget = liveMessage || heading;
    if (!(focusTarget instanceof HTMLElement)) {
      return;
    }

    if (!focusTarget.hasAttribute("tabindex")) {
      focusTarget.setAttribute("tabindex", "-1");
    }
    focusTarget.focus({ preventScroll: true });
  }

  document.body.addEventListener("htmx:afterSwap", focusUpdatedContent);
})();
