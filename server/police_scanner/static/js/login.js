const form = document.getElementById('loginForm');
const err = document.getElementById('loginErr');

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  err.hidden = true;
  const fd = new FormData(form);
  const res = await fetch('/api/auth/login', {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
  });
  if (res.ok) {
    location.href = '/';
  } else {
    let msg = 'Invalid credentials.';
    try { const d = await res.json(); if (d.detail) msg = d.detail; } catch {}
    err.textContent = msg;
    err.hidden = false;
  }
});
