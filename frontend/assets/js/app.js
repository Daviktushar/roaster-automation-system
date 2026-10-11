(() => {
  const data = window.ROSTER_DEMO;
  const views = ['dashboard', 'roster', 'people', 'leave', 'history'];
  const toastNode = document.getElementById('appToast');
  const toast = toastNode && window.bootstrap ? bootstrap.Toast.getOrCreateInstance(toastNode, { delay: 3600 }) : null;

  function notify(title, message) {
    if (!toast) { window.alert(message); return; }
    document.getElementById('toastTitle').textContent = title;
    document.getElementById('toastBody').textContent = message;
    toast.show();
  }

  function showView(name) {
    if (!views.includes(name)) name = 'dashboard';
    views.forEach((view) => document.getElementById(`${view}View`)?.classList.toggle('d-none', view !== name));
    document.querySelectorAll('.side-link[data-view]').forEach((link) => link.classList.toggle('active', link.dataset.view === name));
    const title = { dashboard: 'Overview', roster: 'Roster review', people: 'People & teams', leave: 'Leave & holidays', history: 'Generation history' }[name];
    const crumb = document.getElementById('crumbTitle');
    if (crumb) crumb.textContent = title;
    document.title = `${title} · Rosterly`;
    history.replaceState(null, '', `#${name}`);
    document.getElementById('sidebar')?.classList.remove('open');
    if (name === 'roster') renderRoster();
  }

  document.querySelectorAll('[data-view]').forEach((el) => el.addEventListener('click', (event) => {
    event.preventDefault();
    showView(el.dataset.view);
  }));
  window.addEventListener('hashchange', () => showView(location.hash.slice(1)));
  document.getElementById('menuToggle')?.addEventListener('click', () => document.getElementById('sidebar')?.classList.toggle('open'));
  document.getElementById('dismissBanner')?.addEventListener('click', () => document.querySelector('#dashboardView .preview-banner')?.remove());
  document.querySelectorAll('.dismiss-local').forEach((button) => button.addEventListener('click', () => button.closest('.preview-banner')?.remove()));

  const monthSelect = document.getElementById('targetMonth');
  const yearSelect = document.getElementById('targetYear');
  const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  if (monthSelect && yearSelect) {
    months.forEach((month, index) => monthSelect.add(new Option(month, String(index + 1))));
    [2026, 2027, 2028].forEach((year) => yearSelect.add(new Option(String(year), String(year))));
    monthSelect.value = '11'; yearSelect.value = '2026';
  }

  const categorySelect = document.getElementById('rosterCategory');
  const modalElement = document.getElementById('previewModal');
  function showPreviewModal() {
    if (window.bootstrap) bootstrap.Modal.getOrCreateInstance(modalElement).show();
    else { modalElement?.classList.add('show'); if (modalElement) modalElement.style.display = 'block'; document.body.classList.add('modal-open'); }
  }
  modalElement?.querySelectorAll('[data-bs-dismiss="modal"]').forEach((button) => button.addEventListener('click', () => {
    if (!window.bootstrap) { modalElement.classList.remove('show'); modalElement.style.display = 'none'; document.body.classList.remove('modal-open'); }
  }));
  document.getElementById('generateButton')?.addEventListener('click', () => {
    const name = categorySelect?.selectedOptions[0]?.textContent.trim() || 'HDC3';
    const month = `${monthSelect?.selectedOptions[0]?.textContent || 'November'} ${yearSelect?.value || '2026'}`;
    document.getElementById('modalCategory').textContent = name;
    document.getElementById('modalMonth').textContent = month;
    showPreviewModal();
  });
  document.getElementById('generateTop')?.addEventListener('click', () => document.getElementById('generateButton')?.click());

  const input = document.getElementById('workbookInput');
  const uploadBox = document.getElementById('uploadBox');
  const status = document.getElementById('uploadStatus');
  document.getElementById('browseFile')?.addEventListener('click', () => input?.click());
  input?.addEventListener('change', () => handleFile(input.files?.[0]));
  ['dragenter','dragover'].forEach((eventName) => uploadBox?.addEventListener(eventName, (event) => { event.preventDefault(); uploadBox.classList.add('dragging'); }));
  ['dragleave','drop'].forEach((eventName) => uploadBox?.addEventListener(eventName, (event) => { event.preventDefault(); uploadBox.classList.remove('dragging'); }));
  uploadBox?.addEventListener('drop', (event) => handleFile(event.dataTransfer?.files?.[0]));
  function handleFile(file) {
    if (!file) return;
    if (!/\.(?:xlsx|xlsm)$/i.test(file.name) || file.size > 20 * 1024 * 1024) {
      status.textContent = 'Choose an Excel workbook up to 20 MB'; status.classList.remove('file-selected');
      notify('File not accepted', 'Select an .xlsx or .xlsm workbook under 20 MB. Preview mode does not upload the file.'); return;
    }
    status.textContent = file.name; status.classList.add('file-selected');
    notify('File selected locally', 'The filename is shown for preview. The workbook has not been uploaded or inspected by this page.');
  }

  function shiftClass(code) {
    if (code === 'WO') return 'shift-wo'; if (code === 'PH') return 'shift-ph';
    if (String(code).startsWith('1')) return 'shift-1'; if (String(code).startsWith('2')) return 'shift-2'; if (String(code).startsWith('3')) return 'shift-3'; return 'shift-general';
  }
  function renderRoster(query = '') {
    const target = document.getElementById('previewRows'); if (!target) return;
    const rows = data.employees.filter((employee) => `${employee.name} ${employee.activity}`.toLowerCase().includes(query.toLowerCase()));
    target.innerHTML = rows.map((employee) => `<tr><td><strong>${employee.name}</strong><small>${employee.activity}</small></td>${employee.shifts.map((code) => `<td><span class="shift-chip ${shiftClass(code)}">${code}</span></td>`).join('')}<td><strong>5</strong></td></tr>`).join('') || '<tr><td colspan="9" class="text-center text-secondary py-4">No demo rows match this search.</td></tr>';
    const count = document.getElementById('resultCount'); if (count) count.textContent = `Showing ${rows.length} demo rows`;
    const category = document.getElementById('previewCategory'); const title = document.getElementById('previewTitle');
    if (title && category) title.textContent = `${category.value.trim()} · November 2026`;
  }
  document.getElementById('rosterSearch')?.addEventListener('input', (event) => renderRoster(event.target.value));
  document.getElementById('previewCategory')?.addEventListener('change', () => renderRoster(document.getElementById('rosterSearch')?.value || ''));
  document.getElementById('validateButton')?.addEventListener('click', () => notify('Sample checks complete', 'Three review notes are shown below. This does not validate roster business rules.'));
  document.getElementById('downloadButton')?.addEventListener('click', () => {
    const csv = ['Employee,Activity,Mon 2,Tue 3,Wed 4,Thu 5,Fri 6,Sat 7,Sun 8,Status', ...data.employees.map((r) => [r.name,r.activity,...r.shifts,'DEMO'].map((v) => `"${String(v).replaceAll('"','""')}"`).join(','))].join('\r\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' })); link.download = 'roster-preview-demo.csv'; link.click(); URL.revokeObjectURL(link.href);
    notify('Demo CSV downloaded', 'This export contains sample names and assignments only.');
  });

  const directory = document.getElementById('directoryGrid');
  function renderDirectory(filter = '') {
    if (!directory) return;
    const matches = data.categories.filter((item) => `${item.name} ${item.details} ${item.kind}`.toLowerCase().includes(filter.toLowerCase()));
    directory.innerHTML = matches.map((item) => `<article class="directory-card"><span class="category-badge badge-${item.color}">${item.badge}</span><strong>${item.name}</strong><small>${item.kind}<br>${item.details}</small></article>`).join('') || '<p class="text-secondary small">No categories found.</p>';
  }
  document.getElementById('categorySearch')?.addEventListener('input', (event) => renderDirectory(event.target.value));
  renderDirectory(); renderRoster();
  showView(location.hash.slice(1) || 'dashboard');
})();
