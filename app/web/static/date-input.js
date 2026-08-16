// Eigenes Datumsfeld (TT.MM.JJJJ) statt des nativen <input type="date">, dessen Anzeigeformat
// komplett von der OS-/Browser-Locale des Nutzers bestimmt wird und sich daher nicht
// zuverlässig auf ein einheitliches Format festlegen lässt. Formatiert beim Tippen automatisch
// mit Punkten (TT.MM.JJJJ); der Server erwartet dieses Format beim Parsen.
(function () {
  function formatDigits(raw) {
    const digits = raw.replace(/\D/g, "").slice(0, 8);
    if (digits.length > 4) return digits.slice(0, 2) + "." + digits.slice(2, 4) + "." + digits.slice(4);
    if (digits.length > 2) return digits.slice(0, 2) + "." + digits.slice(2);
    return digits;
  }

  function attach(input) {
    input.addEventListener("input", () => {
      const atEnd = input.selectionStart === input.value.length;
      input.value = formatDigits(input.value);
      if (atEnd) input.selectionStart = input.selectionEnd = input.value.length;
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("input.date-de").forEach(attach);
  });
})();
