(() => {
  document.querySelectorAll('[data-password]').forEach((button) => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.password); if (!input) return;
    const visible = input.type === 'password'; input.type = visible ? 'text' : 'password'; button.textContent = visible ? 'Hide' : 'Show';
  }));
  const emailValid = (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  const login = document.getElementById('loginForm');
  login?.addEventListener('submit', (event) => {
    event.preventDefault();
    const email = document.getElementById('loginEmail'); const password = document.getElementById('loginPassword');
    email.setCustomValidity(emailValid(email.value.trim()) ? '' : 'Invalid email');
    password.setCustomValidity(password.value.length >= 4 ? '' : 'Enter a password');
    login.classList.add('was-validated');
    if (!login.checkValidity()) return;
    const message = document.getElementById('loginMessage'); message.className = 'auth-message'; message.textContent = 'Demo sign-in complete. Opening the sample workspace…';
    window.setTimeout(() => { window.location.href = 'index.html#dashboard'; }, 500);
  });
  const signup = document.getElementById('signupForm');
  signup?.addEventListener('submit', (event) => {
    event.preventDefault();
    const email = document.getElementById('signupEmail'); const password = document.getElementById('signupPassword'); const confirm = document.getElementById('confirmPassword');
    email.setCustomValidity(emailValid(email.value.trim()) ? '' : 'Invalid email');
    confirm.setCustomValidity(confirm.value === password.value && confirm.value.length > 0 ? '' : 'Passwords do not match');
    signup.classList.add('was-validated');
    const message = document.getElementById('signupMessage');
    if (!signup.checkValidity()) { message.className = 'auth-message error'; message.textContent = 'Please correct the highlighted fields.'; return; }
    message.className = 'auth-message'; message.textContent = 'Form looks good. This preview does not create or store an account.';
  });
  const forgot = document.getElementById('forgotForm');
  forgot?.addEventListener('submit', (event) => {
    event.preventDefault();
    const email = document.getElementById('forgotEmail'); email.setCustomValidity(emailValid(email.value.trim()) ? '' : 'Invalid email');
    forgot.classList.add('was-validated');
    const message = document.getElementById('forgotMessage');
    if (!forgot.checkValidity()) { message.className = 'auth-message error'; message.textContent = 'Enter a valid email address.'; return; }
    message.className = 'auth-message'; message.textContent = 'Preview only: no email was sent and no password was reset.';
  });
})();
