const videoPane = document.querySelector(".login-video-pane");
const video = document.querySelector("#login-video");
const videoSources = document.querySelectorAll(".login-video-source");
const soundToggle = document.querySelector(".login-sound-toggle");

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
  soundToggle?.addEventListener("click", () => {
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
