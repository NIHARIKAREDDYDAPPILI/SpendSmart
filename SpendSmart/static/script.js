document.addEventListener("DOMContentLoaded", () => {
  const budgetToggle = document.querySelector("#budget-edit-toggle");
  const budgetForm = document.querySelector("#budget-form");
  budgetToggle?.addEventListener("click", () => {
    budgetForm.classList.toggle("hidden");
    if (!budgetForm.classList.contains("hidden")) {
      document.querySelector("#budget-input").focus();
    }
  });

  document.querySelectorAll(".delete-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Delete this transaction? This cannot be undone.")) {
        event.preventDefault();
      }
    });
  });

  document.querySelectorAll(".flash-close").forEach((button) => {
    button.addEventListener("click", () => button.closest(".flash").remove());
  });

  const transactionForm = document.querySelector("#transaction-form");
  transactionForm?.addEventListener("submit", (event) => {
    const amount = Number(document.querySelector("#amount").value);
    if (!Number.isFinite(amount) || amount <= 0) {
      event.preventDefault();
      document.querySelector("#amount").setCustomValidity("Enter an amount greater than zero.");
      document.querySelector("#amount").reportValidity();
    }
  });
  document.querySelector("#amount")?.addEventListener("input", (event) => event.currentTarget.setCustomValidity(""));

  const registrationForm = document.querySelector("#register-form");
  registrationForm?.addEventListener("submit", (event) => {
    const password = document.querySelector("#password");
    const confirmation = document.querySelector("#confirm-password");
    if (password.value !== confirmation.value) {
      event.preventDefault();
      confirmation.setCustomValidity("Passwords do not match.");
      confirmation.reportValidity();
    }
  });
  document.querySelector("#confirm-password")?.addEventListener("input", (event) => event.currentTarget.setCustomValidity(""));
});