const videoPane = document.querySelector(".login-video-pane");
const video = document.querySelector("#login-video");
const videoSources = document.querySelectorAll(".login-video-source");
const credentialForm = document.querySelector("#login-credential-form");

function showVideoFallback() {
  videoPane?.classList.add("login-video-pane-fallback");
}

function toggleVideoAudio() {
  if (!(video instanceof HTMLVideoElement)) {
    return;
  }
  video.muted = !video.muted;
  void video.play();
}

if (videoPane instanceof HTMLElement && video instanceof HTMLVideoElement) {
  videoPane.addEventListener("click", toggleVideoAudio);
  videoPane.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    toggleVideoAudio();
  });
  video.addEventListener("canplay", () => {
    videoPane.classList.remove("login-video-pane-fallback");
  });
  video.addEventListener("error", showVideoFallback);
  videoSources.forEach((source) => {
    source.addEventListener("error", showVideoFallback);
  });
  window.setTimeout(() => {
    if (video.networkState === HTMLMediaElement.NETWORK_NO_SOURCE) {
      showVideoFallback();
    }
  }, 0);
}

if (credentialForm instanceof HTMLFormElement) {
  credentialForm.addEventListener("submit", () => {
    const selectedProvider = credentialForm.querySelector(
      'input[name="credential_provider"]:checked',
    );
    const credentialInput = credentialForm.querySelector('input[name="email"]');
    const usernameInput = credentialForm.querySelector('input[name="username"]');
    if (
      selectedProvider instanceof HTMLInputElement &&
      credentialInput instanceof HTMLInputElement &&
      usernameInput instanceof HTMLInputElement
    ) {
      if (selectedProvider.value === "ldap") {
        credentialForm.action = credentialForm.dataset.ldapAction ?? "/login/ldap";
        usernameInput.value = credentialInput.value;
      } else {
        credentialForm.action = credentialForm.dataset.localAction ?? "/login/local";
        usernameInput.value = "";
      }
    }
  });
}
