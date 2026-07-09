// Small enhancements only — everything works without JS.

// Live value readouts for range sliders (check-in form)
document.querySelectorAll("input[type=range][data-out]").forEach(function (slider) {
  var out = document.getElementById(slider.dataset.out);
  if (!out) return;
  var sync = function () {
    out.textContent = slider.value + (slider.dataset.suffix || "");
  };
  slider.addEventListener("input", sync);
  sync();
});

// Auto-dismiss success flashes after a few seconds
document.querySelectorAll(".flash-success").forEach(function (el) {
  setTimeout(function () {
    el.style.transition = "opacity .5s ease";
    el.style.opacity = "0";
    setTimeout(function () { el.remove(); }, 500);
  }, 4000);
});
