// auth.js
// Handles shared auth UI behavior such as password show/hide and
// real-time password validation on the registration page.

document.addEventListener("DOMContentLoaded", () => {
  setupPasswordToggles();
  setupPasswordValidation();
});

/**
 * Attach click handlers to all password toggle buttons.
 * Any input with `data-password-field` will be toggled.
 */
function setupPasswordToggles() {
  const toggles = document.querySelectorAll("[data-toggle-password]");
  toggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      // The related input is the closest input preceding the button in the wrapper.
      const wrapper = btn.closest(".password-wrapper");
      if (!wrapper) return;
      const input = wrapper.querySelector("[data-password-field]") || wrapper.querySelector("input[type='password']");
      if (!input) return;

      const isPassword = input.getAttribute("type") === "password";
      input.setAttribute("type", isPassword ? "text" : "password");
      btn.textContent = isPassword ? "Hide" : "Show";
    });
  });
}

/**
 * Real-time password validation for the registration form.
 * Mirrors backend rules:
 * - 8–12 characters
 * - >= 2 digits
 * - >= 1 uppercase
 * - >= 1 special character
 */
function setupPasswordValidation() {
  const passwordInput = document.querySelector("[data-validate-password]");
  const confirmInput = document.querySelector("#confirm_password");
  const rulesBox = document.querySelector("#password-rules");
  const submitBtn = document.querySelector("#register-submit");

  if (!passwordInput || !rulesBox || !submitBtn) {
    return; // Not on register page
  }

  const rules = {
    length: rulesBox.querySelector("[data-rule='length']"),
    digits: rulesBox.querySelector("[data-rule='digits']"),
    uppercase: rulesBox.querySelector("[data-rule='uppercase']"),
    special: rulesBox.querySelector("[data-rule='special']"),
  };

  function applyRuleState(el, valid) {
    if (!el) return;
    el.classList.remove("valid", "invalid");
    el.classList.add(valid ? "valid" : "invalid");
  }

  function evaluatePassword() {
    const value = passwordInput.value || "";
    const digitsCount = (value.match(/\d/g) || []).length;
    const hasUppercase = /[A-Z]/.test(value);
    const hasSpecial = /[^\w\s]/.test(value);

    const lengthValid = value.length >= 8 && value.length <= 12;
    const digitsValid = digitsCount >= 2;
    const uppercaseValid = hasUppercase;
    const specialValid = hasSpecial;

    applyRuleState(rules.length, lengthValid);
    applyRuleState(rules.digits, digitsValid);
    applyRuleState(rules.uppercase, uppercaseValid);
    applyRuleState(rules.special, specialValid);

    const allValid = lengthValid && digitsValid && uppercaseValid && specialValid;
    const confirmMatches = confirmInput ? confirmInput.value === value : true;

    submitBtn.disabled = !(allValid && confirmMatches);
  }

  passwordInput.addEventListener("input", evaluatePassword);
  if (confirmInput) {
    confirmInput.addEventListener("input", evaluatePassword);
  }

  // Initialize on first load
  evaluatePassword();
}

