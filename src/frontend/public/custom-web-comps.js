document.addEventListener("DOMContentLoaded", () => {
  const intervalId = setInterval(() => {
    const watermark = document.querySelector(".watermark");
    if (watermark && !watermark.querySelector('span.powered-by-xenia')) {
      watermark.innerHTML = '<span class="powered-by-xenia">Powered by DMI</span>';
      clearInterval(intervalId);
    }
  }, 500);
});
