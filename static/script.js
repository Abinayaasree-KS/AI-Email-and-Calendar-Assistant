async function sendMessage() {
  const input = document.getElementById("user-input");
  const message = input.value.trim();
  if (!message) return;

  addMessage("user", message);
  input.value = "";
  input.focus();

  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });

    const data = await response.json();

    // NEW: Handle email redirect action
    if (data.action === "redirect_emails") {
      addMessage("assistant", data.reply);
      setTimeout(() => {
        window.location.href = '/emails';
      }, 2000);
    } else {
      addMessage("assistant", data.reply);
    }

  } catch (error) {
    console.error("Error:", error);
    addMessage("assistant", "⚠️ Oops! Something went wrong. Please try again.");
  }
}