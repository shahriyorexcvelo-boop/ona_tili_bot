/**
 * Admin Test & Key Creator Controller
 */
const AdminApp = {
  answers: {}, // {"1": {"ans": ""}, ...} — ball Rasch tomonidan avtomatik hisoblanadi
  isEditMode: false,
  editTestId: null,

  init() {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg) {
      try {
        tg.ready();
        tg.expand();
        if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      } catch(e) {}
    }
    const params = new URLSearchParams(window.location.search);
    const queryTgId = params.get('tg_id');
    const tgU = tg && tg.initDataUnsafe && tg.initDataUnsafe.user;

    const tgPlatform = tg ? (tg.platform || '').toLowerCase() : '';
    const isTg = !!(tg && (
      (tgPlatform && tgPlatform !== 'unknown') ||
      (tg.initData && tg.initData.length > 0) ||
      (tgU && tgU.id) ||
      queryTgId
    ));

    // ODDIY VEB-BRAUZERDA (TELEGRAMSIZ) OCHILGANDA TO'LIQ BLOKLASH!
    if (!isTg) {
      const wb = document.getElementById('web-block-screen');
      if (wb) wb.style.display = 'flex';
      const sp = document.getElementById('admin-splash');
      if (sp) sp.style.display = 'none';
      const container = document.querySelector('.admin-container') || document.querySelector('.main-container');
      if (container) container.style.display = 'none';
      return;
    }

    this.creatorTgId = (tgU && tgU.id) || (queryTgId ? parseInt(queryTgId, 10) : 0);
    if (window.BM_LOGO_B64) {
      document.querySelectorAll('.header-bm-logo').forEach(img => {
        img.src = window.BM_LOGO_B64;
      });
    }

    if (window.Telegram && window.Telegram.WebApp) {
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
    }

    const urlParams = new URLSearchParams(window.location.search);
    const editId = urlParams.get('edit_test_id');
    if (editId) {
      this.isEditMode = true;
      this.editTestId = parseInt(editId, 10);
    }

    this.initAnswers();
    this.renderForm();
    this.updateRaschBadge();
    this.updateUnfilledStats();
    this.runIntroAnimation();

    if (this.isEditMode) {
      this.setupEditMode();
      return;
    }

    const dateInput = document.getElementById('adm-test-sched-date');
    if (dateInput && !dateInput.value) {
      try {
        const today = new Date();
        const yyyy = today.getFullYear();
        const mm = String(today.getMonth() + 1).padStart(2, '0');
        const dd = String(today.getDate()).padStart(2, '0');
        dateInput.value = `${yyyy}-${mm}-${dd}`;
      } catch (e) {}
    }

    // Navbatdagi test kodini va nomini avtomatik to'ldirish (ketma-ketlik bo'yicha)
    fetch('/api/next-test-code')
      .then(res => res.json())
      .then(data => {
        if (data && data.success && data.next_code) {
          const codeInput = document.getElementById('adm-test-code');
          if (codeInput && !codeInput.value) {
            codeInput.value = data.next_code;
          }
          const titleInput = document.getElementById('adm-test-title');
          if (titleInput && !titleInput.value) {
            titleInput.value = `Matematika Blok Test #${data.next_code}`;
          }
        }
      })
      .catch(() => {});
  },

  setupEditMode() {
    const titleEl = document.querySelector('.test-title');
    if (titleEl) titleEl.textContent = 'Test Kalitlarini Tahrirlash';
    const subEl = document.querySelector('.test-subtitle');
    if (subEl) subEl.textContent = "Kalitlarni o'zgartiring va saqlang — barcha o'quvchilar natijalari va reyting avtomatik qayta hisoblanadi";

    const submitBtn = document.querySelector('.btn-submit-test');
    if (submitBtn) {
      submitBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg><span>Kalitlarni saqlash va natijalarni yangilash</span>';
    }

    fetch(`/api/admin/get-test-keys?test_id=${this.editTestId}`)
      .then(res => res.json())
      .then(data => {
        if (!data || !data.success || !data.test) {
          alert(data?.message || 'Test ma\'lumotlarini yuklashda xatolik yuz berdi!');
          return;
        }
        const t = data.test;
        if (titleEl && t.test_code) {
          titleEl.textContent = `#${t.test_code} Test Kalitlarini Tahrirlash`;
        }

        const titleInput = document.getElementById('adm-test-title');
        if (titleInput && t.title) titleInput.value = t.title;

        const codeInput = document.getElementById('adm-test-code');
        if (codeInput && t.test_code) {
          codeInput.value = t.test_code;
          codeInput.readOnly = true;
          codeInput.style.opacity = '0.75';
        }

        const timeInput = document.getElementById('adm-test-time');
        if (timeInput && t.time_limit_min !== undefined) timeInput.value = t.time_limit_min;

        const schedDate = document.getElementById('adm-test-sched-date');
        if (schedDate && t.scheduled_date) schedDate.value = t.scheduled_date;

        const schedStart = document.getElementById('adm-test-sched-start');
        if (schedStart && t.scheduled_start) schedStart.value = t.scheduled_start;

        const schedEnd = document.getElementById('adm-test-sched-end');
        if (schedEnd && t.scheduled_end) schedEnd.value = t.scheduled_end;

        const keyCode = document.getElementById('adm-test-key-code');
        if (keyCode && t.key_access_code) keyCode.value = t.key_access_code;

        const ytUrl = document.getElementById('adm-test-youtube-url');
        if (ytUrl && t.youtube_url) ytUrl.value = t.youtube_url;

        // Kalitlarni AdminApp.answers ga ko'chirish
        if (t.answers) {
          for (let q = 1; q <= 32; q++) {
            const val = t.answers[String(q)];
            const ansVal = (typeof val === 'object' && val !== null) ? (val.ans || '') : String(val || '');
            if (this.answers[String(q)]) {
              this.answers[String(q)].ans = ansVal;
            }
          }
          for (let q = 33; q <= 35; q++) {
            const val = t.answers[String(q)];
            const ansVal = (typeof val === 'object' && val !== null) ? (val.ans || '') : String(val || '');
            if (this.answers[String(q)]) {
              this.answers[String(q)].ans = ansVal;
            }
          }
          for (let q = 36; q <= 45; q++) {
            for (let sub of ['a', 'b']) {
              const k = `${q}${sub}`;
              const val = t.answers[k];
              const ansVal = (typeof val === 'object' && val !== null) ? (val.ans || '') : String(val || '');
              if (this.answers[k]) {
                this.answers[k].ans = ansVal;
              }
            }
          }
        }

        this.renderForm();
        this.updateUnfilledStats();
      })
      .catch(err => {
        alert('Server bilan bog\'lanishda xatolik: ' + err.message);
      });
  },

  startSplashCanvas() {
    const canvas = document.getElementById('splash-canvas');
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    let width = canvas.width = window.innerWidth;
    let height = canvas.height = window.innerHeight;

    const symbols = ['∞', 'π', '∑', '∫', '√x', 'f(x)', '∆', 'θ', 'λ', '≈', '≠', 'e', 'α', 'β', 'γ', 'dx', 'dy', 'lim'];
    const particles = [];
    const count = Math.min(26, Math.max(16, Math.floor(width / 16)));

    for (let i = 0; i < count; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        char: symbols[Math.floor(Math.random() * symbols.length)],
        size: 13 + Math.random() * 18,
        vx: (Math.random() - 0.5) * 0.45,
        vy: -0.35 - Math.random() * 0.75,
        opacity: 0.12 + Math.random() * 0.45,
        pulseSpeed: 0.02 + Math.random() * 0.03,
        angle: Math.random() * Math.PI * 2,
        spinSpeed: (Math.random() - 0.5) * 0.015
      });
    }

    let animId = null;
    const isDark = document.body.classList.contains('dark-mode') || document.documentElement.getAttribute('data-theme') === 'dark';

    function draw() {
      ctx.clearRect(0, 0, width, height);
      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];
        p.x += p.vx;
        p.y += p.vy;
        p.angle += p.spinSpeed;
        p.opacity += Math.sin(Date.now() * p.pulseSpeed) * 0.005;
        if (p.opacity < 0.1) p.opacity = 0.1;
        if (p.opacity > 0.6) p.opacity = 0.6;

        if (p.y < -30) { p.y = height + 30; p.x = Math.random() * width; }
        if (p.x < -30) p.x = width + 30;
        if (p.x > width + 30) p.x = -30;

        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.rotate(p.angle);
        ctx.font = 'bold ' + p.size + 'px serif';
        if (isDark) {
          ctx.fillStyle = 'rgba(147, 197, 253, ' + p.opacity + ')';
          ctx.shadowColor = 'rgba(56, 189, 248, 0.4)';
          ctx.shadowBlur = 8;
        } else {
          ctx.fillStyle = 'rgba(37, 99, 235, ' + (p.opacity * 0.8) + ')';
          ctx.shadowColor = 'rgba(37, 99, 235, 0.2)';
          ctx.shadowBlur = 6;
        }
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(p.char, 0, 0);
        ctx.restore();
      }
      animId = requestAnimationFrame(draw);
    }

    animId = requestAnimationFrame(draw);

    return function stop() {
      if (animId) cancelAnimationFrame(animId);
    };
  },

  finishSplashImmediately() {
    const splash = document.getElementById('admin-splash') || document.getElementById('intro-splash') || document.getElementById('splashScreen');
    if (!splash) return;
    if (this._splashDismissed) return;
    this._splashDismissed = true;

    if (this._stopSplashCanvas) {
      try { this._stopSplashCanvas(); } catch(e) {}
      this._stopSplashCanvas = null;
    }

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      try { window.Telegram.WebApp.HapticFeedback.impactOccurred('medium'); } catch(e) {}
    }

    splash.classList.add('dismissed');
    setTimeout(() => {
      splash.style.display = 'none';
    }, 600);
  },

  runIntroAnimation() {
    const splash = document.getElementById('admin-splash') || document.getElementById('intro-splash') || document.getElementById('splashScreen');
    if (!splash) return;

    this._stopSplashCanvas = this.startSplashCanvas();

    window.finishAdminSplash = () => this.finishSplashImmediately();
    window.dismissSplash = () => this.finishSplashImmediately();
    window.finishSplashImmediately = () => this.finishSplashImmediately();

    // 3200ms dan so'ng avtomatik ravishda mayin o'tish (xuddi asosiy app kabi)
    setTimeout(() => {
      this.finishSplashImmediately();
    }, 3200);
  },

  confirmClearAll() {
    let filledCount = 0;
    for (let k in this.answers) {
      if (this.answers[k] && this.answers[k].ans && String(this.answers[k].ans).trim().length > 0) {
        filledCount++;
      }
    }
    if (filledCount === 0) {
      alert("Hozircha hech qanday kalit/javob belgilanmagan!");
      return;
    }
    if (confirm("Barcha kiritilgan test kalitlari va javoblarini o'chirmoqchimisiz? Bu amal barcha variantli va ochiq savollar kalitlarini tozalaydi.")) {
      this.clearAll();
    }
  },

  clearAll() {
    this.initAnswers();
    this.renderForm();
    this.updateUnfilledStats();
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      try { window.Telegram.WebApp.HapticFeedback.notificationOccurred('warning'); } catch(e) {}
    }
  },

  initAnswers() {
    // 1-32 (4 ta variant: A, B, C, D) — ball Rasch tomonidan avtomatik hisoblanadi
    for (let q = 1; q <= 32; q++) {
      this.answers[String(q)] = { ans: '' };
    }
    // 33, 34, 35 (6 ta variant: A-F)
    for (let q = 33; q <= 35; q++) {
      this.answers[String(q)] = { ans: '' };
    }
    // 36a-45b (ochiq yozma javoblar)
    for (let q = 36; q <= 45; q++) {
      this.answers[`${q}a`] = { ans: '' };
      this.answers[`${q}b`] = { ans: '' };
    }
  },

  renderForm() {
    const part1 = document.getElementById('admin-part-1');
    const part2 = document.getElementById('admin-part-2');
    const part3 = document.getElementById('admin-part-3');

    // 1. 1-32 savollar (A, B, C, D)
    if (part1) {
      let html = '';
      for (let q = 1; q <= 32; q++) {
        const curAns = this.answers[String(q)].ans;
        html += `
          <div class="q-admin-row">
            <span class="q-admin-num">${q}.</span>
            <div class="options-group" style="flex: 1;">
              ${['A', 'B', 'C', 'D'].map(opt => `
                <button class="option-btn ${opt === curAns ? 'selected' : ''}" id="adm-opt-${q}-${opt}" onclick="AdminApp.selectChoice(${q}, '${opt}')">
                  ${opt}
                </button>
              `).join('')}
            </div>
          </div>
        `;
      }
      part1.innerHTML = html;
    }

    // 2. 33, 34, 35 savollar (6 ta variant: A, B, C, D, E, F)
    if (part2) {
      let html = '';
      for (let q = 33; q <= 35; q++) {
        const curAns = this.answers[String(q)].ans;
        html += `
          <div class="q-admin-row" style="flex-direction: column; align-items: stretch; gap: 8px; margin-bottom: 8px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span class="q-admin-num" style="font-weight: 800;">${q}-savol (6 ta variant)</span>
            </div>
            <div class="options-group-6">
              ${['A', 'B', 'C', 'D', 'E', 'F'].map(opt => `
                <button class="option-btn ${opt === curAns ? 'selected' : ''}" id="adm-opt-${q}-${opt}" onclick="AdminApp.selectChoice(${q}, '${opt}')">
                  ${opt}
                </button>
              `).join('')}
            </div>
          </div>
        `;
      }
      part2.innerHTML = html;
    }

    // 3. 36-45 (ochiq yozma javoblar)
    if (part3) {
      let html = '';
      for (let q = 36; q <= 45; q++) {
        const itemA = this.answers[`${q}a`];
        const itemB = this.answers[`${q}b`];
        html += `
          <div class="open-admin-box">
            <span style="font-size: 13px; font-weight: 800;">${q}-savol (Ochiq yozma)</span>
            
            <!-- a -->
            <div class="open-admin-sub-row">
              <span style="font-size: 12px; font-weight: 800; color: var(--primary); min-width: 28px;">${q}a:</span>
              <div class="savol-input-box" id="box-${q}a" onclick="MathKeyboard.openFor('${q}a')" style="height: 38px; flex: 1; padding: 0 8px; cursor: pointer;">
                <input type="text" class="savol-input" id="input-${q}a" readonly inputmode="none" placeholder="Kalitni klaviaturadan kiriting" value="${itemA.ans}" onclick="MathKeyboard.openFor('${q}a')" style="font-size: 13px; cursor: pointer;">
              </div>
              <button type="button" class="btn-kb-icon" style="width: 36px; height: 38px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px;" onclick="MathKeyboard.openFor('${q}a')" title="Matematik klaviatura"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="6" y1="8" x2="6.01" y2="8"/><line x1="10" y1="8" x2="10.01" y2="8"/><line x1="14" y1="8" x2="14.01" y2="8"/><line x1="18" y1="8" x2="18.01" y2="8"/><line x1="6" y1="12" x2="6.01" y2="12"/><line x1="10" y1="12" x2="10.01" y2="12"/><line x1="14" y1="12" x2="14.01" y2="12"/><line x1="18" y1="12" x2="18.01" y2="12"/><line x1="7" y1="16" x2="17" y2="16"/></svg></button>
            </div>

            <!-- b -->
            <div class="open-admin-sub-row">
              <span style="font-size: 12px; font-weight: 800; color: var(--primary); min-width: 28px;">${q}b:</span>
              <div class="savol-input-box" id="box-${q}b" onclick="MathKeyboard.openFor('${q}b')" style="height: 38px; flex: 1; padding: 0 8px; cursor: pointer;">
                <input type="text" class="savol-input" id="input-${q}b" readonly inputmode="none" placeholder="Kalitni klaviaturadan kiriting" value="${itemB.ans}" onclick="MathKeyboard.openFor('${q}b')" style="font-size: 13px; cursor: pointer;">
              </div>
              <button type="button" class="btn-kb-icon" style="width: 36px; height: 38px; display: inline-flex; align-items: center; justify-content: center; border-radius: 10px;" onclick="MathKeyboard.openFor('${q}b')" title="Matematik klaviatura"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><line x1="6" y1="8" x2="6.01" y2="8"/><line x1="10" y1="8" x2="10.01" y2="8"/><line x1="14" y1="8" x2="14.01" y2="8"/><line x1="18" y1="8" x2="18.01" y2="8"/><line x1="6" y1="12" x2="6.01" y2="12"/><line x1="10" y1="12" x2="10.01" y2="12"/><line x1="14" y1="12" x2="14.01" y2="12"/><line x1="18" y1="12" x2="18.01" y2="12"/><line x1="7" y1="16" x2="17" y2="16"/></svg></button>
            </div>
          </div>
        `;
      }
      part3.innerHTML = html;
    }
  },

  selectChoice(qNum, option) {
    const key = String(qNum);
    this.answers[key].ans = option;

    const opts = [33, 34, 35].includes(qNum) ? ['A', 'B', 'C', 'D', 'E', 'F'] : ['A', 'B', 'C', 'D'];
    opts.forEach(opt => {
      const btn = document.getElementById(`adm-opt-${qNum}-${opt}`);
      if (btn) btn.classList.toggle('selected', opt === option);
    });

    this.updateUnfilledStats();

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
    }
  },

  updateOpenAns(key, val) {
    if (this.answers[key]) {
      this.answers[key].ans = val;
    }
    const input = document.getElementById(`input-${key}`);
    if (input && input.value !== val) {
      input.value = val;
    }
    const box = document.getElementById(`box-${key}`);
    if (box) {
      box.classList.toggle('filled', (val || '').trim().length > 0);
    }
    this.updateUnfilledStats();
  },

  updateRaschBadge() {
    // Rasch tizimida ballar avtomatik hisoblanadi
    const badge = document.getElementById('admin-total-badge');
    const dock = document.getElementById('admin-dock-total');
    const msg = 'Rasch: ball avtomatik hisoblanadi';
    if (badge) badge.textContent = msg;
    if (dock) dock.textContent = msg;
  },

  updateUnfilledStats() {
    let closedUnfilled = 0;
    let openUnfilled = 0;

    let sec1Unfilled = 0;
    for (let q = 1; q <= 32; q++) {
      const item = this.answers[String(q)];
      if (!item || !item.ans || !String(item.ans).trim()) {
        closedUnfilled++;
        sec1Unfilled++;
      }
    }

    let sec2Unfilled = 0;
    for (let q = 33; q <= 35; q++) {
      const item = this.answers[String(q)];
      if (!item || !item.ans || !String(item.ans).trim()) {
        closedUnfilled++;
        sec2Unfilled++;
      }
    }

    for (let q = 36; q <= 45; q++) {
      for (let sub of ['a', 'b']) {
        const item = this.answers[`${q}${sub}`];
        if (!item || !item.ans || !String(item.ans).trim()) {
          openUnfilled++;
        }
      }
    }

    const totalUnfilled = closedUnfilled + openUnfilled;
    const totalQuestions = 55;
    const totalFilled = totalQuestions - totalUnfilled;

    // 1. Top status bar
    const totalBadge = document.getElementById('unfilled-total-badge');
    const statusIcon = document.getElementById('unfilled-status-icon');
    const statusTitle = document.getElementById('unfilled-status-title');
    const closedCountEl = document.getElementById('unfilled-closed-count');
    const openCountEl = document.getElementById('unfilled-open-count');

    if (totalBadge) {
      if (totalUnfilled === 0) {
        totalBadge.textContent = 'Barchasi to\'ldirildi (55/55)';
        totalBadge.style.background = '#10B981';
        if (statusIcon) statusIcon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>';
        if (statusTitle) statusTitle.textContent = 'Barcha savollar tayyor:';
      } else {
        totalBadge.textContent = `${totalUnfilled} ta belgilanmagan (${totalFilled}/55)`;
        totalBadge.style.background = '#EF4444';
        if (statusIcon) statusIcon.innerHTML = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2.5"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>';
        if (statusTitle) statusTitle.textContent = 'Javoblar to\'ldirilishi:';
      }
    }

    if (closedCountEl) {
      if (closedUnfilled === 0) {
        closedCountEl.textContent = 'Barchasi belgilandi (35/35)';
        closedCountEl.style.color = '#10B981';
      } else {
        closedCountEl.textContent = `${closedUnfilled} ta belgilanmagan (${35 - closedUnfilled}/35)`;
        closedCountEl.style.color = '#EF4444';
      }
    }

    if (openCountEl) {
      if (openUnfilled === 0) {
        openCountEl.textContent = 'Barchasi kiritildi (20/20)';
        openCountEl.style.color = '#10B981';
      } else {
        openCountEl.textContent = `${openUnfilled} ta kiritilmagan (${20 - openUnfilled}/20)`;
        openCountEl.style.color = '#EF4444';
      }
    }

    // 2. Section Subtexts
    const sec1El = document.getElementById('cnt-sec-1');
    if (sec1El) {
      if (sec1Unfilled === 0) {
        sec1El.textContent = 'Barchasi belgilandi (32/32)';
        sec1El.style.color = '#10B981';
      } else {
        sec1El.textContent = `${sec1Unfilled} ta belgilanmagan`;
        sec1El.style.color = '#EF4444';
      }
    }

    const sec2El = document.getElementById('cnt-sec-2');
    if (sec2El) {
      if (sec2Unfilled === 0) {
        sec2El.textContent = 'Barchasi belgilandi (3/3)';
        sec2El.style.color = '#10B981';
      } else {
        sec2El.textContent = `${sec2Unfilled} ta belgilanmagan`;
        sec2El.style.color = '#EF4444';
      }
    }

    const sec3El = document.getElementById('cnt-sec-3');
    if (sec3El) {
      if (openUnfilled === 0) {
        sec3El.textContent = 'Barchasi kiritildi (20/20)';
        sec3El.style.color = '#10B981';
      } else {
        sec3El.textContent = `${openUnfilled} ta kiritilmagan`;
        sec3El.style.color = '#EF4444';
      }
    }

    // 3. Bottom Dock
    const dockUnfilled = document.getElementById('admin-dock-unfilled');
    if (dockUnfilled) {
      if (totalUnfilled === 0) {
        dockUnfilled.textContent = '55/55 to\'liq belgilandi';
        dockUnfilled.style.color = '#10B981';
      } else {
        dockUnfilled.textContent = `${totalUnfilled} ta javob qoldi (Yopiq: ${closedUnfilled}, Ochiq: ${openUnfilled})`;
        dockUnfilled.style.color = '#EF4444';
      }
    }

    return { totalUnfilled, closedUnfilled, openUnfilled };
  },

  async saveTest() {
    const title = (document.getElementById('adm-test-title')?.value || '').trim() || 'Matematika Milliy Sertifikat Testi';
    const subject = (document.getElementById('adm-test-subject')?.value || '').trim() || 'Matematika';
    let code = (document.getElementById('adm-test-code')?.value || '').trim().toUpperCase();
    const timeLimit = parseInt(document.getElementById('adm-test-time')?.value) || 0;

    if (!code) {
      try {
        const res = await fetch('/api/next-test-code');
        const data = await res.json();
        if (data && data.next_code) code = data.next_code;
      } catch (e) {}
      if (!code) code = '1';
    }

    // 36a-45b savollarni to'g'ridan-to'g'ri DOM inputlaridan ham tekshirib olish (100% kafolat)
    for (let q = 36; q <= 45; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        const input = document.getElementById(`input-${key}`);
        if (!this.answers[key]) {
          this.answers[key] = { ans: '' };
        }
        if (input && input.value !== undefined) {
          this.answers[key].ans = input.value.trim();
        }
      }
    }

    const { totalUnfilled, closedUnfilled, openUnfilled } = this.updateUnfilledStats();

    if (totalUnfilled > 0) {
      const confirmMsg = `DIQQAT! Jami 55 ta savoldan ${totalUnfilled} tasiga javob belgilanmagan:\n\n` +
        `• Yopiq savollarda (1-35): ${closedUnfilled} ta belgilanmagan\n` +
        `• Ochiq savollarda (36-45): ${openUnfilled} ta kiritilmagan\n\n` +
        `Iltimos, avval barcha savollarga to'g'ri javobni belgilang!`;
      
      if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
        window.Telegram.WebApp.showAlert(confirmMsg);
      } else {
        alert(confirmMsg);
      }
      return;
    }

    const keyCodeInput = document.getElementById('adm-test-key-code');
    const keyCode = keyCodeInput ? keyCodeInput.value.trim() : '';

    const schedDate = (document.getElementById('adm-test-sched-date')?.value || '').trim();
    const schedStart = (document.getElementById('adm-test-sched-start')?.value || '').trim();
    const schedEnd = (document.getElementById('adm-test-sched-end')?.value || '').trim();
    const youtubeUrl = (document.getElementById('adm-test-youtube-url')?.value || '').trim();

    const tgUser = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) || null;
    const urlParams = new URLSearchParams(window.location.search);
    const creatorId = (tgUser && tgUser.id) ? parseInt(tgUser.id) : (urlParams.get('tg_id') ? parseInt(urlParams.get('tg_id')) : 0);

    const payload = {
      test_code: code,
      title: title,
      subject: subject,
      time_limit_min: timeLimit,
      key_access_code: keyCode,
      scheduled_date: schedDate,
      scheduled_start: schedStart,
      scheduled_end: schedEnd,
      youtube_url: youtubeUrl,
      creator_tg_id: creatorId,
      answers: this.answers
    };

    if (this._isSaving) return;
    this._isSaving = true;

    const saveBtn = document.querySelector('.btn-submit-test');
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = this.isEditMode ? 'Kalitlar yangilanmoqda...' : 'Saqlanmoqda...';
    }

    const initData = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || '';

    if (this.isEditMode) {
      const editPayload = {
        test_id: this.editTestId,
        test_code: code,
        title: title,
        subject: subject,
        time_limit_min: timeLimit,
        key_access_code: keyCode,
        scheduled_date: schedDate,
        scheduled_start: schedStart,
        scheduled_end: schedEnd,
        youtube_url: youtubeUrl,
        answers: this.answers,
        init_data: initData
      };

      try {
        const response = await fetch('/api/admin/update-test-keys', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Telegram-Init-Data': initData
          },
          body: JSON.stringify(editPayload)
        });

        const res = await response.json();
        if (res.success) {
          const recnt = res.recalculated_count !== undefined ? res.recalculated_count : 0;
          const msg = `Test kalitlari muvaffaqiyatli yangilandi!\n\n${recnt} nafar o'quvchi javoblari yangi kalitlar bo'yicha qayta tekshirildi, Rasch modeli va reyting avtomatik yangilandi.`;
          if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
            window.Telegram.WebApp.showAlert(msg, () => {
              window.Telegram.WebApp.close();
            });
            setTimeout(() => {
              window.Telegram.WebApp.close();
            }, 1600);
          } else {
            alert(msg);
            window.location.reload();
          }
        } else {
          this._isSaving = false;
          if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = '<span>Kalitlarni saqlash va natijalarni yangilash</span>';
          }
          alert(res.message || 'Kalitlarni yangilashda xatolik yuz berdi!');
        }
      } catch (e) {
        this._isSaving = false;
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.innerHTML = '<span>Kalitlarni saqlash va natijalarni yangilash</span>';
        }
        alert('Server bilan bog\'lanishda xatolik: ' + e.message);
      }
      return;
    }

    try {
      const response = await fetch('/api/create-test', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Telegram-Init-Data': initData
        },
        body: JSON.stringify(Object.assign({}, payload, { init_data: initData }))
      });

      const res = await response.json();
      if (res.success) {
        const msg = "Test muvaffaqiyatli saqlandi va e'lon qilindi!\n\nBotga o'tib, test uchun PDF faylni yuborishingiz mumkin.";
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
          window.Telegram.WebApp.showAlert(msg);
          setTimeout(() => {
            window.Telegram.WebApp.close();
          }, 1200);
        } else {
          alert(msg);
          window.location.reload();
        }
      } else {
        this._isSaving = false;
        if (saveBtn) {
          saveBtn.disabled = false;
          saveBtn.textContent = 'Testni saqlash';
        }
        alert(res.message || 'Saqlashda xatolik yuz berdi!');
      }
    } catch (e) {
      this._isSaving = false;
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = 'Testni saqlash';
      }
      alert('Server bilan bog\'lanishda xatolik: ' + e.message);
    }
  },

  triggerScanKeys() {
    const input = document.getElementById('adm-keys-photo-input');
    if (input) {
      input.click();
    }
  },

  async handleKeysPhotoSelected(input) {
    if (!input || !input.files || input.files.length === 0) return;
    const file = input.files[0];
    input.value = '';

    const btn = document.getElementById('btn-scan-keys');
    const btnText = document.getElementById('btn-scan-text');
    const btnIcon = document.getElementById('btn-scan-icon');

    if (btn) btn.disabled = true;
    if (btnIcon) btnIcon.textContent = '⏳';
    if (btnText) btnText.textContent = 'Tahlil qilinmoqda...';

    try {
      const formData = new FormData();
      formData.append('image', file);

      const resp = await fetch('/api/scan-keys', {
        method: 'POST',
        body: formData
      });

      const res = await resp.json();

      if (res.success && res.data) {
        let recognizedCount = 0;
        const keys = res.data;

        // Barcha savol javoblarini AdminApp.answers ga to'ldirish
        for (const [k, v] of Object.entries(keys)) {
          const normKey = String(k).toLowerCase().trim();
          if (this.answers[normKey]) {
            let cleanVal = String(v).trim();
            // 36a-45b ochiq savollari uchun sanitarizatsiyani majburiy chaqiramiz:
            if (/[ab]$/.test(normKey) || (parseInt(normKey) >= 36 && parseInt(normKey) <= 45)) {
              cleanVal = this.sanitizeMath(cleanVal);
            }
            this.answers[normKey].ans = cleanVal;
            recognizedCount++;
          }
        }

        // Shaklni qayta chizish va hisobotni yangilash
        this.renderForm();
        this.updateUnfilledStats();

        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
          window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
        }

        const msg = `Rasmdan ${recognizedCount} ta savol kaliti muvaffaqiyatli aniqlandi va to'ldirildi!`;
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
          window.Telegram.WebApp.showAlert(msg);
        } else {
          alert(msg);
        }
      } else if (res.need_api_key) {
        const userKey = prompt("Google Gemini API kaliti kiritilmagan yoki noto'g'ri.\nIltimos, aistudio.google.com dan olingan Gemini API kalitini kiriting:");
        if (userKey && userKey.trim()) {
          await this.saveGeminiKey(userKey.trim());
        }
      } else {
        alert(res.message || 'Rasmdan kalitlarni ajratib bo\'lmadi. Iltimos, aniqroq rasm yuklang.');
      }
    } catch (err) {
      alert('Rasm yuklashda xatolik yuz berdi: ' + err.message);
    } finally {
      if (btn) btn.disabled = false;
      if (btnIcon) {
        btnIcon.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/><path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/></svg>';
      }
      if (btnText) btnText.textContent = 'Rasm yuklash';
    }
  },

  async configureGeminiKey() {
    const userKey = prompt("Google Gemini API kalitini kiriting (yoki yangilang):");
    if (userKey && userKey.trim()) {
      await this.saveGeminiKey(userKey.trim());
    }
  },

  async saveGeminiKey(key) {
    try {
      const resp = await fetch('/api/set-gemini-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key: key.trim() })
      });
      const res = await resp.json();
      if (res.success) {
        alert("Gemini API kaliti muvaffaqiyatli saqlandi! Endi rasmni bemalol skanerlashingiz mumkin.");
      } else {
        alert("Kalitni saqlashda xatolik: " + res.message);
      }
    } catch (e) {
      alert("Serverga ulanishda xatolik: " + e.message);
    }
  },

  sanitizeMath(val) {
    if (!val) return '';
    let s = String(val).trim();
    s = s.replace(/\$/g, '');
    s = s.replace(/\\+(?:cdot|times)\b/g, '*');
    s = s.replace(/\\+pm\b/g, '±');
    s = s.replace(/\\+pi\b/g, 'π');
    s = s.replace(/\\+sqrt\[3\]\{([^}]+)\}/g, '∛$1');
    s = s.replace(/\\+sqrt\[3\]([0-9a-zA-Z]+)/g, '∛$1');
    while (s.includes('sqrt{')) {
      s = s.replace(/\\+sqrt\{([^}]+)\}/g, '√$1');
    }
    s = s.replace(/\\+sqrt([0-9a-zA-Z]+)/g, '√$1');
    while (/\\+d?frac\{([^}]+)\}\{([^}]+)\}/.test(s)) {
      s = s.replace(/\\+d?frac\{([^}]+)\}\{([^}]+)\}/g, '$1/$2');
    }
    s = s.replace(/\{([^}]+)\}/g, '$1');
    s = s.replace(/\\/g, '');
    s = s.replace(/\s*\+\s*/g, ' + ');
    s = s.replace(/\s*\-\s*/g, ' - ');
    s = s.replace(/\s+/g, ' ');
    return s.trim();
  }
};

window.AdminApp = AdminApp;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    AdminApp.init();
  });
} else {
  AdminApp.init();
}
