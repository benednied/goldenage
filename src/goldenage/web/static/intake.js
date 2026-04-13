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
      if (!fileInput) {
        continue;
      }

      const submit = function () {
        if (fileInput.files && fileInput.files.length > 0) {
          dropzone.requestSubmit();
        }
      };

      fileInput.addEventListener("change", submit);

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
        submit();
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
