(function () {
  function bindDropzones(root) {
    const scope = root || document;
    const dropzones = scope.querySelectorAll(".intake-dropzone");
    for (const dropzone of dropzones) {
      if (dropzone.dataset.bound === "1") {
        continue;
      }
      dropzone.dataset.bound = "1";
      const fileInput = dropzone.querySelector('input[type="file"]');
      const fileName = dropzone.querySelector("[data-file-name]");
      if (!fileInput) {
        continue;
      }

      const showSelection = function () {
        if (!(fileName instanceof HTMLElement)) {
          return;
        }
        const selected = fileInput.files?.[0];
        fileName.textContent = selected ? `Selected: ${selected.name}` : "No file selected";
        fileName.classList.remove("is-error");
        fileInput.removeAttribute("aria-invalid");
      };

      fileInput.addEventListener("change", showSelection);
      fileInput.addEventListener("invalid", function (event) {
        event.preventDefault();
        fileInput.setAttribute("aria-invalid", "true");
        if (fileName instanceof HTMLElement) {
          fileName.textContent = "Select an Outlook .msg file before uploading.";
          fileName.classList.add("is-error");
        }
        fileInput.focus();
      });

      for (const eventName of ["dragenter", "dragover"]) {
        dropzone.addEventListener(eventName, function (event) {
          event.preventDefault();
          dropzone.classList.add("is-dragover");
        });
      }

      for (const eventName of ["dragleave", "dragend", "drop"]) {
        dropzone.addEventListener(eventName, function (event) {
          event.preventDefault();
          dropzone.classList.remove("is-dragover");
        });
      }

      dropzone.addEventListener("drop", function (event) {
        const files = event.dataTransfer ? event.dataTransfer.files : null;
        if (!files || files.length === 0) {
          return;
        }
        fileInput.files = files;
        showSelection();
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    bindDropzones(document);
  });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    bindDropzones(event.target);
  });
})();
