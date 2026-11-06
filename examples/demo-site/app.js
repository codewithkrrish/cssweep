// app.js — the button gets its class toggled from JS.
document.querySelector('.btn').addEventListener('click', (event) => {
  event.currentTarget.classList.toggle('is-pressed');
});
