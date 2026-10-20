/**
 * Test Tekshirish Tizimi — Asosiy Mini App Mantiqi
 */

const TestApp = {
  testId: 1,
  testCode: 'TEST-01',
  testTitle: 'Ona tili Blok Test',
  subject: 'Ona tili',
  userTgId: 0,
  userFullname: 'Foydalanuvchi',
  isDarkMode: false,
  minSubmitInfo: null,
  minSubmitTimerInterval: null,
  isAdmin: false,

  // Foydalanuvchi belgilagan javoblar
  answers: {},

  init() {
    if (window.BM_LOGO_B64) {
      document.querySelectorAll('.intro-logo-img, .header-bm-logo').forEach(img => {
        img.src = window.BM_LOGO_B64;
      });
    }

    // 1. Telegram WebApp ni sozlash va tekshirish
    const isTelegramWebApp = Boolean(
      window.Telegram &&
      window.Telegram.WebApp &&
      (
        (window.Telegram.WebApp.initData && window.Telegram.WebApp.initData.length > 5) ||
        (window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user)
      )
    );

    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg) {
      try {
        tg.ready();
        tg.expand();
        if (typeof tg.disableVerticalSwipes === 'function') tg.disableVerticalSwipes();
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
      const webBlock = document.getElementById('web-block-screen');
      if (webBlock) webBlock.style.display = 'flex';
      const introSplash = document.getElementById('intro-splash') || document.getElementById('splashScreen');
      if (introSplash) introSplash.style.display = 'none';
      const testContent = document.querySelector('.test-body') || document.querySelector('.main-container');
      if (testContent) testContent.style.display = 'none';
      return;
    }

    this.userTgId = (tgU && tgU.id) || (queryTgId ? parseInt(queryTgId, 10) : 0);
    this.userFullname = (tgU && `${tgU.first_name || ''} ${tgU.last_name || ''}`.trim()) || (tgU && tgU.username) || 'Foydalanuvchi';

    // Mavzu (birinchi kirishda oq/light, agar foydalanuvchi qoraga o'tkazsa saqlanadi)
    const savedTheme = localStorage.getItem('app_theme') || 'light';
    this.setTheme(savedTheme === 'dark');

    // 2. URL parametrlardan test ma'lumotlarini olish
    if (params.has('test_code')) this.testCode = params.get('test_code');
    if (params.has('test_id')) this.testId = parseInt(params.get('test_id')) || 1;
    if (params.has('title')) this.testTitle = params.get('title');
    if (params.has('subject')) this.subject = params.get('subject');

    // UI ga o'rnatish
    const titleEl = document.getElementById('test-title-display');
    const codeEl = document.getElementById('test-code-display');
    const subjectEl = document.getElementById('test-subject-badge');
    const userEl = document.getElementById('user-welcome-text');

    if (titleEl) {
      titleEl.textContent = this.testCode ? `#${this.testCode} test` : this.testTitle;
    }
    if (codeEl) codeEl.textContent = `KOD: #${this.testCode}`;
    if (subjectEl) subjectEl.textContent = this.subject;
    if (userEl) userEl.textContent = `Ishtirokchi: ${this.userFullname}`;

    // 3. Savollarni render qilish
    this.renderQuestions();
    this.renderMapGrid();
    this.updateProgress();

    // 3.1. Agar telefon o'chib yongan yoki sahifa yangilangan bo'lsa, javoblarni xotiradan tiklash
    this.restoreAnswersFromStorage();
    this.fetchDraftFromBackend();

    // 4. Mavzuga mos kirish animatsiyasini ishga tushirish
    this.runIntroAnimation();

    // 5. Allaqachon topshirganlikni va 45 daqiqalik cheklovni tekshirish
    if (this.userTgId) {
      fetch('/api/app/active-tests?tg_id=' + this.userTgId)
        .then(function(res) { return res.json(); })
        .then(function(d) {
          if (d && d.tests) {
            TestApp.isAdmin = Boolean(d.is_admin);
            var cur = d.tests.find(function(t) { return Number(t.id) === Number(TestApp.testId); });
            if (cur) {
              if (cur.min_submit_info) {
                TestApp.minSubmitInfo = cur.min_submit_info;
                TestApp.startMinSubmitTimer();
              }
              if (cur.already_submitted) {
                var submitBtn = document.getElementById('btn-submit-test');
                if (submitBtn) {
                  submitBtn.disabled = true;
                  submitBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg>Topshirilgan';
                  submitBtn.classList.add('already-submitted');
                  submitBtn.style.cursor = 'not-allowed';
                }
                setTimeout(function() {
                  alert("Siz ushbu testni allaqachon topshirgansiz!\n\nJavoblaringiz qabul qilingan. Natijalar Rasch modeli tahlili e'lon qilingandan so'ng botingizga yuboriladi.");
                }, 400);
              }
            }
          }
        }).catch(function(e) {});
    } else {
      var submitBtn = document.getElementById('btn-submit-test');
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.5';
        submitBtn.style.cursor = 'not-allowed';
        submitBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:4px;"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>Bot orqali kiring';
      }
    }

    // O'quvchi ilovani yopib qo'ysa yoki boshqa dasturga o'tsa ham javoblarni zudlik bilan saqlash (Auto-save)
    window.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        TestApp.saveAnswersToStorage();
      }
    });
    window.addEventListener('pagehide', () => TestApp.saveAnswersToStorage());
    window.addEventListener('beforeunload', () => TestApp.saveAnswersToStorage());
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
    const splash = document.getElementById('intro-splash') || document.getElementById('splashScreen');
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
    this.finishSplashImmediately();
  },

  toggleTheme() {
    this.setTheme(!this.isDarkMode);
  },

  setTheme(isDark) {
    this.isDarkMode = isDark;
    document.body.classList.toggle('dark-mode', isDark);
    localStorage.setItem('app_theme', isDark ? 'dark' : 'light');
  },

  // ----------------------------------------------------
  // SAVOLLARNI GENERATSIYA QILISH
  // ----------------------------------------------------
  renderQuestions() {
    const part1 = document.getElementById('questions-part-1');
    const part2 = document.getElementById('questions-part-2');
    const part3 = document.getElementById('questions-part-3');

    // 1. 1-35 Variantli Savollar (1-32: A, B, C, D; 33-35: A, B, C, D, E, F)
    if (part1) {
      let html1 = '';
      for (let q = 1; q <= 35; q++) {
        const isSix = (q >= 33 && q <= 35);
        const opts = isSix ? ['A', 'B', 'C', 'D', 'E', 'F'] : ['A', 'B', 'C', 'D'];
        const groupClass = isSix ? 'options-group options-group-6' : 'options-group';
        html1 += `
          <div class="question-card" id="qcard-${q}">
            <span class="question-num-tag">${q}-savol</span>
            <div class="${groupClass}">
              ${opts.map(opt => `
                <button class="option-btn" id="opt-${q}-${opt}" onclick="TestApp.selectOption(${q}, '${opt}')">
                  ${opt}
                </button>
              `).join('')}
            </div>
          </div>
        `;
      }
      part1.innerHTML = html1;
    }

    if (part2) {
      part2.innerHTML = '';
      part2.style.display = 'none';
    }

    // 3. 36-39 Yopiq Savollar (1 ta variantli — bitta ochiq qator)
    if (part3) {
      let html3 = '';
      for (let q = 36; q <= 39; q++) {
        const key = String(q);
        html3 += `
          <div class="open-question-row" id="qrow-${key}">
            <div class="savol-badge">${key}-savol</div>
            <div class="savol-input-box" id="box-${key}">
              <input type="text" class="savol-input open-native-input" id="input-${key}" placeholder="Javobni kiriting..." autocomplete="off" autocapitalize="none" oninput="TestApp.handleOpenInput('${key}', this.value)">
            </div>
          </div>
        `;
      }
      part3.innerHTML = html3;
    }

    // 4. 40 dan boshlab Yopiq Savollar (a va b qismlari bilan)
    const part4 = document.getElementById('questions-part-4');
    if (part4) {
      let html4 = '';
      const endQ = this.getEndQ();
      for (let q = 40; q <= endQ; q++) {
        for (let sub of ['a', 'b']) {
          const key = `${q}${sub}`;
          html4 += `
            <div class="open-question-row" id="qrow-${key}">
              <div class="savol-badge">${key}-savol</div>
              <div class="savol-input-box" id="box-${key}">
                <input type="text" class="savol-input open-native-input" id="input-${key}" placeholder="${key} javobini kiriting..." autocomplete="off" autocapitalize="none" oninput="TestApp.handleOpenInput('${key}', this.value)">
              </div>
            </div>
          `;
        }
      }
      part4.innerHTML = html4;
    }
  },

  getEndQ() {
    return 44;
  },

  getTotalQuestionsCount() {
    return 35 + 4 + 10 + 1; // 35 variantli + 4 ochiq (36-39) + 10 juft ochiq (40a-44b) + 1 esse (45) = 50
  },

  // ----------------------------------------------------
  // LOCALSTORAGE AUTOSAVE & RESTORE (Javoblar o'chib ketmasligi uchun)
  // ----------------------------------------------------
  getStorageKey() {
    const tid = this.testId || '1';
    const uid = this.userTgId || '0';
    return `bm_answers_test_${tid}_user_${uid}`;
  },

  saveAnswersToStorage() {
    try {
      if (!this.testId) return;
      // 36-39 savollar (1 ta variantli)
      for (let q = 36; q <= 39; q++) {
        const key = String(q);
        const input = document.getElementById(`input-${key}`);
        if (input && input.value !== undefined) {
          this.answers[key] = input.value.trim();
        }
      }
      // 40 dan boshlab savollar (a va b bandli)
      const endQ = this.getEndQ();
      for (let q = 40; q <= endQ; q++) {
        for (let sub of ['a', 'b']) {
          const key = `${q}${sub}`;
          const input = document.getElementById(`input-${key}`);
          if (input && input.value !== undefined) {
            this.answers[key] = input.value.trim();
          }
        }
      }
      // 45-savol: Esse
      const input45 = document.getElementById('input-45');
      if (input45 && input45.value !== undefined) {
        this.answers['45'] = input45.value.trim();
      }
      const input45Text = document.getElementById('input-45-text');
      if (input45Text && input45Text.value !== undefined) {
        this.answers['45_text'] = input45Text.value.trim();
      }
      const key = this.getStorageKey();
      localStorage.setItem(key, JSON.stringify(this.answers));
      this.syncDraftToBackend();
    } catch (e) {
      console.warn('Storage save error:', e);
    }
  },

  showAutoSaveIndicator() {
    // Foydalanuvchi talabiga ko'ra "Xotiraga saqlandi" yashil xabarnomasi olib tashlandi
    return;
  },


  restoreAnswersFromStorage() {
    try {
      const key = this.getStorageKey();
      const raw = localStorage.getItem(key);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (!saved || typeof saved !== 'object') return;

      this.answers = Object.assign({}, saved);

      // 1-35 variantli savollarni tiklash
      for (let q = 1; q <= 35; q++) {
        const opt = this.answers[String(q)];
        if (opt) {
          const btn = document.getElementById(`opt-${q}-${opt}`);
          if (btn) btn.classList.add('selected');
          const card = document.getElementById(`qcard-${q}`);
          if (card) card.classList.add('answered');
          this.updateMapItem(String(q), true);
        }
      }

      // 36-39 (1 ta variantli) yopiq savollar
      for (let q = 36; q <= 39; q++) {
        const fieldKey = String(q);
        const val = this.answers[fieldKey];
        const input = document.getElementById(`input-${fieldKey}`);
        const box = document.getElementById(`box-${fieldKey}`);
        if (input && val !== undefined) {
          input.value = val;
          const isFilled = (val || '').trim().length > 0;
          if (box) box.classList.toggle('filled', isFilled);
          this.updateMapItem(fieldKey, isFilled);
        }
      }

      // 40 dan boshlab (a va b) yopiq savollar
      const endQ = this.getEndQ();
      for (let q = 40; q <= endQ; q++) {
        for (let sub of ['a', 'b']) {
          const fieldKey = `${q}${sub}`;
          const val = this.answers[fieldKey];
          const input = document.getElementById(`input-${fieldKey}`);
          const box = document.getElementById(`box-${fieldKey}`);
          if (input && val !== undefined) {
            input.value = val;
            const isFilled = (val || '').trim().length > 0;
            if (box) box.classList.toggle('filled', isFilled);
            this.updateMapItem(fieldKey, isFilled);
          }
        }
      }

      // 45-savol: Esse (yozma ish)
      const val45 = this.answers['45'];
      const input45 = document.getElementById('input-45');
      const box45 = document.getElementById('box-45');
      if (input45 && val45 !== undefined) {
        input45.value = val45;
        const isFilled = (val45 || '').trim().length > 0;
        if (box45) box45.classList.toggle('filled', isFilled);
        this.updateMapItem('45', isFilled);
      }
      const val45Text = this.answers['45_text'];
      const input45Text = document.getElementById('input-45-text');
      if (input45Text && val45Text !== undefined) {
        input45Text.value = val45Text;
      }

      this.updateProgress();
    } catch (e) {
      console.warn('Storage restore error:', e);
    }
  },

  clearAnswersFromStorage() {
    try {
      const key = this.getStorageKey();
      localStorage.removeItem(key);
    } catch (e) {}
  },

  _autosaveSyncTimer: null,
  syncDraftToBackend() {
    if (!this.testId || !this.userTgId) return;
    clearTimeout(this._autosaveSyncTimer);
    this._autosaveSyncTimer = setTimeout(() => {
      try {
        const initData = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || '';
        fetch('/api/app/autosave-answer', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Telegram-Init-Data': initData
          },
          body: JSON.stringify({
            test_id: this.testId,
            user_tg_id: this.userTgId,
            answers: this.answers,
            init_data: initData
          })
        }).catch(err => console.warn('Backend draft autosave warning:', err));
      } catch (e) {}
    }, 600);
  },

  fetchDraftFromBackend() {
    if (!this.testId || !this.userTgId) return;
    const initData = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || '';
    fetch(`/api/app/get-draft?test_id=${this.testId}&user_tg_id=${this.userTgId}`, {
      headers: { 'X-Telegram-Init-Data': initData }
    })
      .then(res => res.json())
      .then(data => {
        if (data && data.success && data.answers && typeof data.answers === 'object') {
          const keys = Object.keys(data.answers);
          if (keys.length > 0) {
            let changed = false;
            keys.forEach(k => {
              if (data.answers[k] && !this.answers[k]) {
                this.answers[k] = data.answers[k];
                changed = true;
              }
            });
            if (changed) {
              this.restoreAnswersFromStorage();
            }
          }
        }
      })
      .catch(err => console.warn('Backend draft fetch warning:', err));
  },

  // ----------------------------------------------------
  // BARCHA JAVOBLARNI O'CHIRISH (CLEAR ALL)
  // ----------------------------------------------------
  confirmClearAll() {
    if (this.isSubmitting) return;
    if (typeof MathKeyboard !== 'undefined' && MathKeyboard.close) {
      MathKeyboard.close();
    }
    let answeredCount = 0;
    for (let k in this.answers) {
      if (this.answers[k] && String(this.answers[k]).trim().length > 0) {
        answeredCount++;
      }
    }
    if (answeredCount === 0) {
      alert("Hozircha hech qanday javob belgilanmagan!");
      return;
    }
    const modal = document.getElementById('clear-confirm-modal');
    if (modal) {
      modal.classList.add('open');
    } else if (confirm("Barcha belgilangan javoblarni o'chirmoqchimisiz? Bu amalni ortga qaytarib bo'lmaydi.")) {
      this.clearAllAnswers();
    }
  },

  closeClearModal() {
    const modal = document.getElementById('clear-confirm-modal');
    if (modal) {
      modal.classList.remove('open');
    }
  },

  clearAllAnswers() {
    if (this.isSubmitting) return;
    this.closeClearModal();
    this.answers = {};
    this.clearAnswersFromStorage();

    // 1-35 variantli savollar tugmalari va kartalarini tozalash
    for (let q = 1; q <= 35; q++) {
      const opts = (q >= 33 && q <= 35) ? ['A', 'B', 'C', 'D', 'E', 'F'] : ['A', 'B', 'C', 'D'];
      opts.forEach(opt => {
        const btn = document.getElementById(`opt-${q}-${opt}`);
        if (btn) btn.classList.remove('selected');
      });
      const card = document.getElementById(`qcard-${q}`);
      if (card) card.classList.remove('answered');
      this.updateMapItem(String(q), false);
    }

    // 36-39 (1 ta variantli) yopiq savollar inputlari va qutilarini tozalash
    for (let q = 36; q <= 39; q++) {
      const fieldKey = String(q);
      const input = document.getElementById(`input-${fieldKey}`);
      if (input) input.value = '';
      const box = document.getElementById(`box-${fieldKey}`);
      if (box) box.classList.remove('filled');
      this.updateMapItem(fieldKey, false);
    }

    // 40 dan boshlab (a va b) yopiq savollar inputlari va qutilarini tozalash
    const endQ = this.getEndQ();
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const fieldKey = `${q}${sub}`;
        const input = document.getElementById(`input-${fieldKey}`);
        if (input) input.value = '';
        const box = document.getElementById(`box-${fieldKey}`);
        if (box) box.classList.remove('filled');
        this.updateMapItem(fieldKey, false);
      }
    }

    this.updateProgress();

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      try { window.Telegram.WebApp.HapticFeedback.notificationOccurred('warning'); } catch(e) {}
    }
  },

  // ----------------------------------------------------
  // JAVOBNI TANLASH VA O'RNATISH
  // ----------------------------------------------------
  selectOption(qNum, option) {
    const key = String(qNum);
    const prev = this.answers[key];

    // Variant tugmalarini yangilash (1-32: A-D, 33-35: A-F)
    const opts = (qNum >= 33 && qNum <= 35) ? ['A', 'B', 'C', 'D', 'E', 'F'] : ['A', 'B', 'C', 'D'];
    opts.forEach(opt => {
      const btn = document.getElementById(`opt-${qNum}-${opt}`);
      if (btn) btn.classList.toggle('selected', opt === option);
    });

    this.answers[key] = option;

    const card = document.getElementById(`qcard-${qNum}`);
    if (card) card.classList.add('answered');

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.impactOccurred('light');
    }

    this.updateProgress();
    this.updateMapItem(String(qNum), true);
    this.saveAnswersToStorage();
  },

  handleOpenInput(fieldKey, val) {
    this.answers[fieldKey] = (val || '').trim();

    const box = document.getElementById(`box-${fieldKey}`);
    if (box) {
      box.classList.toggle('filled', (val || '').trim().length > 0);
    }

    const isFilled = (val || '').trim().length > 0;
    this.updateMapItem(fieldKey, isFilled);
    this.updateProgress();

    clearTimeout(this._inputSaveTimer);
    this._inputSaveTimer = setTimeout(() => {
      this.saveAnswersToStorage();
    }, 150);
  },


  setOpenAnswer(fieldKey, val) {
    this.handleOpenInput(fieldKey, val);
  },

  // ----------------------------------------------------
  // PROGRESS VA XARITA (1-35, 36-39, 40+)
  // ----------------------------------------------------
  renderMapGrid() {
    const grid = document.getElementById('map-grid');
    if (!grid) return;

    let html = '';
    // 1-35 savollar
    for (let q = 1; q <= 35; q++) {
      html += `
        <button class="map-num-btn" id="map-btn-${q}" onclick="TestApp.scrollToQuestion('${q}')">
          ${q}
        </button>
      `;
    }
    // 36-39 (1 ta variantli)
    for (let q = 36; q <= 39; q++) {
      html += `
        <button class="map-num-btn open-map-btn" id="map-btn-${q}" onclick="TestApp.scrollToQuestion('${q}')" style="font-size: 11.5px; font-weight: 800; min-width: 36px; padding: 4px 2px;">
          ${q}
        </button>
      `;
    }
    // 40 dan boshlab (a va b)
    const endQ = this.getEndQ();
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        html += `
          <button class="map-num-btn open-map-btn" id="map-btn-${key}" onclick="TestApp.scrollToQuestion('${key}')" style="font-size: 11.5px; font-weight: 800; min-width: 36px; padding: 4px 2px;">
            ${key}
          </button>
        `;
      }
    }
    // 45 Esse
    html += `
      <button class="map-num-btn open-map-btn" id="map-btn-45" onclick="TestApp.scrollToQuestion('45')" style="font-size: 11px; font-weight: 800; min-width: 40px; padding: 4px 2px; border-color: #8b5cf6;">
        45 (E)
      </button>
    `;
    grid.innerHTML = html;
  },

  toggleNavMap() {
    const map = document.getElementById('questions-nav-map');
    const arrow = document.getElementById('map-arrow-icon');
    if (!map) return;

    const isOpen = map.style.display !== 'none';
    map.style.display = isOpen ? 'none' : 'block';
    if (arrow) arrow.textContent = isOpen ? '▼' : '▲';
  },

  scrollToQuestion(key) {
    const el = document.getElementById(`qcard-${key}`) || document.getElementById(`qrow-${key}`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('pulse-highlight');
      setTimeout(() => el.classList.remove('pulse-highlight'), 1200);
    }
    this.toggleNavMap();
  },

  updateMapItem(key, isFilled) {
    const btn = document.getElementById(`map-btn-${key}`);
    if (btn) btn.classList.toggle('answered', isFilled);
  },

  updateProgress() {
    let answeredQuestions = 0;

    // 1-35 savollar
    for (let q = 1; q <= 35; q++) {
      if (this.answers[String(q)]) answeredQuestions++;
    }

    // 36-39 savollar (1 ta variantli)
    for (let q = 36; q <= 39; q++) {
      const key = String(q);
      const input = document.getElementById(`input-${key}`);
      const val = this.answers[key] !== undefined ? this.answers[key] : (input ? input.value : '');
      if ((val || '').trim().length > 0) {
        answeredQuestions++;
      }
    }

    // 40 dan boshlab (a va b)
    const endQ = this.getEndQ();
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        const input = document.getElementById(`input-${key}`);
        const val = this.answers[key] !== undefined ? this.answers[key] : (input ? input.value : '');
        if ((val || '').trim().length > 0) {
          answeredQuestions++;
        }
      }
    }

    // 45 Esse
    const val45 = this.answers['45'] !== undefined ? this.answers['45'] : ((document.getElementById('input-45') || {}).value || '');
    if ((val45 || '').trim().length > 0) {
      answeredQuestions++;
    }

    const total = this.getTotalQuestionsCount();
    const remaining = total - answeredQuestions;
    const percent = Math.round((answeredQuestions / total) * 100);

    const countEl = document.getElementById('answered-counter');
    const fillEl = document.getElementById('progress-bar-fill');
    const dockText = document.getElementById('dock-answered-text');
    const dockSub = document.getElementById('dock-unanswered-text');

    if (countEl) countEl.textContent = `${answeredQuestions} / ${total}`;
    if (fillEl) fillEl.style.width = `${percent}%`;
    if (dockText) dockText.textContent = `${answeredQuestions} / ${total} ta belgilandi (${percent}%)`;
    if (dockSub) dockSub.textContent = remaining === 0 ? "Barcha savollar to'ldirildi!" : `${remaining} ta savol qoldi`;
  },

  // ----------------------------------------------------
  // TESTNI TOPSHIRISH (SUBMIT) — 2 BOSQICHLI XAVFSIZ TIZIM
  // ----------------------------------------------------
  openConfirmSubmitModal() {
    // 45 daqiqalik topshirish cheklovini tekshirish
    if (this.minSubmitInfo && !this.minSubmitInfo.can_submit && !this.isAdmin) {
      this.openMinSubmitModal();
      return;
    }

    // 1-bosqichga o'tkazish ("Testni yakunlaysizmi? Ha / Yo'q")
    const step1 = document.getElementById('confirm-step-1');
    const step2 = document.getElementById('confirm-step-2');
    if (step1) step1.style.display = 'block';
    if (step2) step2.style.display = 'none';

    const modal = document.getElementById('confirm-modal');
    if (modal) modal.classList.add('open');
  },

  closeConfirmSubmitModal() {
    const modal = document.getElementById('confirm-modal');
    if (modal) modal.classList.remove('open');
  },

  proceedToSubmitStep2() {
    // 2-bosqichga o'tish: avval barcha inputlarni saqlash
    this.saveAnswersToStorage();

    let answered = 0;
    for (let q = 1; q <= 35; q++) {
      if (this.answers[String(q)]) answered++;
    }
    for (let q = 36; q <= 39; q++) {
      if ((this.answers[String(q)] || '').trim().length > 0) answered++;
    }
    const endQ = this.getEndQ();
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        if ((this.answers[key] || '').trim().length > 0) answered++;
      }
    }
    if ((this.answers['45'] || '').trim().length > 0) answered++;

    const total = this.getTotalQuestionsCount();
    const empty = total - answered;

    const mAnswered = document.getElementById('m-stat-answered');
    const mEmpty = document.getElementById('m-stat-empty');
    if (mAnswered) mAnswered.textContent = answered;
    if (mEmpty) mEmpty.textContent = empty;
    if (mAnswered) mAnswered.textContent = answered;
    if (mEmpty) mEmpty.textContent = empty;

    const step1 = document.getElementById('confirm-step-1');
    const step2 = document.getElementById('confirm-step-2');
    const zeroWarn = document.getElementById('zero-answers-warning');
    const partialWarn = document.getElementById('partial-answers-warning');
    const finalBtn = document.getElementById('btn-final-submit');
    const iconEl = document.getElementById('confirm-step-2-icon');
    const titleEl = document.getElementById('confirm-step-2-title');
    const descEl = document.getElementById('confirm-step-2-desc');

    if (answered === 0) {
      // 0 ta belgilangan bo'lsa: QAT'IY TO'SIQ!
      if (zeroWarn) zeroWarn.style.display = 'block';
      if (partialWarn) partialWarn.style.display = 'none';
      if (iconEl) {
        iconEl.innerHTML = '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#EF4444" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
      }
      if (titleEl) titleEl.textContent = 'Javoblar belgilanmagan!';
      if (descEl) descEl.textContent = 'Testda birorta ham savolga javob belgilanmagan.';
      if (finalBtn) {
        finalBtn.disabled = true;
        finalBtn.style.opacity = '0.35';
        finalBtn.style.cursor = 'not-allowed';
        finalBtn.style.pointerEvents = 'none';
        finalBtn.innerHTML = '<span>Topshirish bloklangan</span>';
      }
    } else {
      // Kamida 1 ta belgilangan bo'lsa: Yakunlash imkoni
      if (zeroWarn) zeroWarn.style.display = 'none';
      if (partialWarn) partialWarn.style.display = empty > 0 ? 'block' : 'none';
      if (iconEl) {
        iconEl.innerHTML = '<svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="#2563EB" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>';
      }
      if (titleEl) titleEl.textContent = 'Javoblaringiz holati';
      if (descEl) descEl.textContent = `Siz 55 ta savoldan ${answered} tasini belgiladingiz, ${empty} tasini belgilanmagan qoldirdingiz.`;
      if (finalBtn) {
        finalBtn.disabled = false;
        finalBtn.style.opacity = '1';
        finalBtn.style.cursor = 'pointer';
        finalBtn.style.pointerEvents = 'auto';
        finalBtn.innerHTML = '<span>Oxirgi yakunlash</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:6px;"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
      }
    }

    if (step1) step1.style.display = 'none';
    if (step2) step2.style.display = 'block';
  },

  backToSubmitStep1() {
    const step1 = document.getElementById('confirm-step-1');
    const step2 = document.getElementById('confirm-step-2');
    if (step1) step1.style.display = 'block';
    if (step2) step2.style.display = 'none';
  },

  openMinSubmitModal() {
    const modal = document.getElementById('min-submit-modal');
    if (!modal) return;
    this.updateMinSubmitModalUI();
    modal.classList.add('open');
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      try { window.Telegram.WebApp.HapticFeedback.notificationOccurred('warning'); } catch(e) {}
    }
  },

  closeMinSubmitModal() {
    const modal = document.getElementById('min-submit-modal');
    if (modal) modal.classList.remove('open');
  },

  startMinSubmitTimer() {
    if (this.minSubmitTimerInterval) {
      clearInterval(this.minSubmitTimerInterval);
      this.minSubmitTimerInterval = null;
    }
    if (!this.minSubmitInfo || this.minSubmitInfo.can_submit) return;

    this.minSubmitTimerInterval = setInterval(() => {
      if (!this.minSubmitInfo) return;
      if (this.minSubmitInfo.remaining_seconds > 0) {
        this.minSubmitInfo.remaining_seconds--;
        this.updateMinSubmitModalUI();
      } else {
        this.minSubmitInfo.can_submit = true;
        this.minSubmitInfo.remaining_seconds = 0;
        clearInterval(this.minSubmitTimerInterval);
        this.minSubmitTimerInterval = null;
        this.updateMinSubmitModalUI();
      }
    }, 1000);
  },

  updateMinSubmitModalUI() {
    const countEl = document.getElementById('min-submit-countdown');
    const unlockEl = document.getElementById('min-submit-unlock-info');
    const titleEl = document.getElementById('min-submit-title');
    const descEl = document.getElementById('min-submit-desc');
    const iconWrap = document.getElementById('min-submit-icon-wrap');
    const actionBtn = document.getElementById('min-submit-action-btn');

    if (!this.minSubmitInfo) return;

    const rem = Math.max(0, this.minSubmitInfo.remaining_seconds || 0);
    const mins = Math.floor(rem / 60);
    const secs = rem % 60;
    const timeStr = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

    if (rem > 0 && !this.minSubmitInfo.can_submit) {
      if (countEl) {
        countEl.textContent = timeStr;
        countEl.style.color = 'var(--primary, #2563EB)';
      }
      if (unlockEl && this.minSubmitInfo.unlock_time_str) {
        unlockEl.innerHTML = `Topshirish ochiladigan vaqt: <b style="color:var(--text-main);">${this.minSubmitInfo.unlock_time_str}</b>`;
      }
      if (titleEl) titleEl.textContent = 'Hali javob yubora olmaysiz!';
      if (descEl) descEl.innerHTML = 'Test boshlanganidan so\'ng dastlabki <b>45 daqiqa</b> davomida javoblarni topshirish cheklangan.';
      if (iconWrap) {
        iconWrap.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>';
        iconWrap.style.borderColor = '#F59E0B';
        iconWrap.style.color = '#F59E0B';
        iconWrap.style.background = 'linear-gradient(135deg, rgba(245, 158, 11, 0.2), rgba(239, 68, 68, 0.15))';
      }
      if (actionBtn) {
        actionBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:6px;"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg><span>Savollarni qayta tekshirish</span>';
        actionBtn.className = 'btn-modal-cancel';
        actionBtn.onclick = () => this.closeMinSubmitModal();
      }
    } else {
      if (countEl) {
        countEl.textContent = '00:00';
        countEl.style.color = '#10B981';
      }
      if (unlockEl) {
        unlockEl.innerHTML = '<span style="display:inline-flex;align-items:center;gap:4px;color:#10B981;"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg><b>45 daqiqalik cheklov yakunlandi!</b></span>';
      }
      if (titleEl) titleEl.textContent = 'Topshirish vaqti yetib keldi!';
      if (descEl) descEl.textContent = 'Endi javoblaringizni bemalol topshirishingiz mumkin.';
      if (iconWrap) {
        iconWrap.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>';
        iconWrap.style.borderColor = '#10B981';
        iconWrap.style.color = '#10B981';
        iconWrap.style.background = 'rgba(16, 185, 129, 0.15)';
      }
      if (actionBtn) {
        actionBtn.innerHTML = '<span>Testni yakunlashga o\'tish</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:6px;"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
        actionBtn.className = 'btn-modal-confirm';
        actionBtn.onclick = () => {
          this.closeMinSubmitModal();
          this.openConfirmSubmitModal();
        };
      }
    }
  },

  async submitTestNow() {
    if (!this.userTgId || this.userTgId <= 0) {
      this.closeConfirmSubmitModal();
      alert("Web orqali ishlash mumkin emas! Testni faqat rasmiy Telegram botimiz (@bm_rashtest_bot) va Mini ilova orqali topshirish mumkin.");
      const webBlock = document.getElementById('web-block-screen');
      if (webBlock) webBlock.style.display = 'flex';
      return;
    }

    // 45 daqiqa tekshiruvi
    if (this.minSubmitInfo && !this.minSubmitInfo.can_submit && !this.isAdmin) {
      this.closeConfirmSubmitModal();
      this.openMinSubmitModal();
      return;
    }

    // 36-39 savollar (1 ta variantli) va 40+ savollar (a va b) qiymatlarini to'g'ridan-to'g'ri DOM dan olib yakuniy tekshirish
    for (let q = 36; q <= 39; q++) {
      const key = String(q);
      const input = document.getElementById(`input-${key}`);
      if (input && input.value !== undefined) {
        this.answers[key] = input.value.trim();
      }
    }
    const endQ = this.getEndQ();
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        const input = document.getElementById(`input-${key}`);
        if (input && input.value !== undefined) {
          this.answers[key] = input.value.trim();
        }
      }
    }

    // Xavfsizlik: 0 ta belgilangan bo'lsa topshirishga mutlaqo ruxsat bermaslik
    let answered = 0;
    for (let q = 1; q <= 35; q++) {
      if (this.answers[String(q)]) answered++;
    }
    for (let q = 36; q <= 39; q++) {
      if ((this.answers[String(q)] || '').trim().length > 0) answered++;
    }
    for (let q = 40; q <= endQ; q++) {
      for (let sub of ['a', 'b']) {
        const key = `${q}${sub}`;
        if ((this.answers[key] || '').trim().length > 0) answered++;
      }
    }

    const totalQuestions = this.getTotalQuestionsCount();
    if (answered === 0 && !this.isAdmin) {
      alert(`Siz birorta ham savolga javob belgilamadingiz (0/${totalQuestions})!\n\nBo'sh testni topshirib bo'lmaydi. Iltimos, savollarni ishlab, javoblarni belgilang!`);
      this.closeConfirmSubmitModal();
      return;
    }

    if (this._isSubmitting) return;
    this._isSubmitting = true;

    const submitBtn = document.getElementById('btn-final-submit');
    const backBtn = document.getElementById('btn-confirm-back');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Amalga oshirilmoqda...';
    }
    if (backBtn) {
      backBtn.disabled = true;
    }

    const payload = {
      test_id: this.testId,
      test_code: this.testCode,
      user_tg_id: this.userTgId,
      fullname: this.userFullname,
      answers: this.answers
    };

    try {
      const initData = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || '';
      const response = await fetch('/api/submit-test', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Telegram-Init-Data': initData
        },
        body: JSON.stringify(Object.assign({}, payload, { init_data: initData }))
      });

      const result = await response.json();

      if (response.status === 429) {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<span>Oxirgi yakunlash</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:6px;"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
        }
        if (backBtn) backBtn.disabled = false;
        alert(result.message || "Iltimos, biroz kuting! So'rovingiz navbatda qayta ishlanmoqda...");
        return;
      }

      this.closeConfirmSubmitModal();

      if (result.success && result.data) {
        this.clearAnswersFromStorage();
        this.showResultModal(result.data);
      } else {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.innerHTML = '<span>Oxirgi yakunlash</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:6px;"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
        }
        if (backBtn) backBtn.disabled = false;

        if (result.error_code === 'EARLY_SUBMISSION_BLOCKED') {
          if (!this.minSubmitInfo) this.minSubmitInfo = {};
          this.minSubmitInfo.can_submit = false;
          if (result.remaining_seconds !== undefined) {
            this.minSubmitInfo.remaining_seconds = result.remaining_seconds;
          }
          if (result.unlock_time) {
            this.minSubmitInfo.unlock_time_str = result.unlock_time;
          }
          this.startMinSubmitTimer();
          this.openMinSubmitModal();
          return;
        }
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
          window.Telegram.WebApp.showAlert(result.message || 'Javoblarni yuborishda xatolik!');
        } else {
          alert(result.message || 'Javoblarni yuborishda xatolik yuz berdi!');
        }
      }
    } catch (e) {
      console.error('Submit error:', e);
      this.closeConfirmSubmitModal();
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<span>Oxirgi yakunlash</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="margin-left:6px;"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>';
      }
      if (backBtn) backBtn.disabled = false;
      alert('Tarmoq xatoligi yoki serverga ulanishda muammo yuz berdi. Iltimos, qayta urinib ko\'ring.');
    } finally {
      setTimeout(() => {
        this._isSubmitting = false;
        if (backBtn) backBtn.disabled = false;
      }, 1500);
    }
  },

  showResultModal(data) {
    this.clearAnswersFromStorage();
    const mainSubmitBtn = document.getElementById('btn-submit-test');
    if (mainSubmitBtn) {
      mainSubmitBtn.disabled = true;
      mainSubmitBtn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="vertical-align:middle;margin-right:4px;"><polyline points="20 6 9 17 4 12"/></svg>Topshirilgan';
      mainSubmitBtn.classList.add('already-submitted');
      mainSubmitBtn.style.cursor = 'not-allowed';
    }

    const modal = document.getElementById('result-modal');
    if (!modal) return;

    const nameEl = document.getElementById('result-user-name');
    const titleEl = modal.querySelector('.result-title');
    const gradeContainer = document.getElementById('result-grade-container');
    const detailsGrid = modal.querySelector('.result-details-grid');
    const noteEl = modal.querySelector('.result-actions div');
    const analysisBtn = document.getElementById('btn-open-analysis');

    if (nameEl) nameEl.textContent = data.fullname || this.userFullname;

    const isPublished = Boolean(data.is_published);

    if (data.is_late) {
      if (titleEl) titleEl.textContent = "Test kech topshirildi!";
      if (gradeContainer) {
        gradeContainer.style.borderColor = "rgba(239, 68, 68, 0.4)";
        gradeContainer.style.background = "rgba(239, 68, 68, 0.12)";
        gradeContainer.innerHTML = `
          <span style="color: #EF4444; display: flex; align-items: center; justify-content: center;">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
          </span>
          <div style="text-align: left;">
            <div style="font-size: 11px; text-transform: uppercase; font-weight: 800; color: #EF4444; letter-spacing: 0.5px;">Holat</div>
            <div style="font-size: 15px; font-weight: 800; color: #DC2626;">Kech topshirildi (Hisobga olinmaydi)</div>
          </div>
        `;
      }
      if (detailsGrid) detailsGrid.style.display = 'none';
      if (analysisBtn) analysisBtn.style.display = 'none';
      if (noteEl) {
        noteEl.innerHTML = `<b>Siz testni belgilangan vaqtdan kech topshirdingiz!</b> Natijangiz umumiy hisobga olinmaydi. Javoblaringiz ko'rib chiqish uchun adminga yuborildi. Agar admin ruxsat bersa, natijangiz umumiy reytingga qo'shiladi.`;
      }
    } else if (!isPublished) {
      if (titleEl) titleEl.textContent = "Javoblaringiz qabul qilindi!";
      if (gradeContainer) {
        gradeContainer.style.borderColor = "rgba(245, 158, 11, 0.4)";
        gradeContainer.style.background = "rgba(245, 158, 11, 0.12)";
        gradeContainer.innerHTML = `
          <span style="color: #F59E0B; display: flex; align-items: center; justify-content: center;">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="10"></circle>
              <polyline points="12 6 12 12 16 14"></polyline>
            </svg>
          </span>
          <div style="text-align: left;">
            <div style="font-size: 11px; text-transform: uppercase; font-weight: 800; color: #D97706; letter-spacing: 0.5px;">Holat</div>
            <div style="font-size: 16px; font-weight: 800; color: #B45309;">Jarayonda (Kutilmoqda)...</div>
          </div>
        `;
      }
      if (detailsGrid) detailsGrid.style.display = 'none';
      if (analysisBtn) analysisBtn.style.display = 'none';
      if (noteEl) {
        noteEl.innerHTML = `<b>Eslatma:</b> Javoblaringiz muvaffaqiyatli saqlandi. Test hozirda davom etmoqda. Admin testni to'xtatib, <b>Rasch modeli (JMLE)</b> asosida tahlil o'tkazgach, to'g'ri ishlangan savollar soni, ball va Milliy sertifikat darajangiz botingizga shaxsiy xabar qilib yuboriladi!`;
      }
    } else {
      if (titleEl) titleEl.textContent = "Test Yakunlandi!";
      if (detailsGrid) detailsGrid.style.display = 'grid';
      if (analysisBtn) analysisBtn.style.display = 'block';

      const correctEl = document.getElementById('r-correct-count');
      const incorrectEl = document.getElementById('r-incorrect-count');
      const emptyEl = document.getElementById('r-unanswered-count');
      if (correctEl) correctEl.textContent = `${data.correct_count ?? 0} ta`;
      if (incorrectEl) incorrectEl.textContent = `${data.incorrect_count ?? 0} ta`;
      if (emptyEl) emptyEl.textContent = `${data.unanswered_count ?? 0} ta`;

      const gradeVal = document.getElementById('result-grade-val');
      if (gradeVal) {
        const grade = data.grade || '—';
        const score = (data.score !== undefined && data.score !== null) ? `${data.score} ball` : '';
        gradeVal.textContent = `${grade} ${score ? `(${score})` : ''}`;
      }
    }

    this.lastResultData = data;
    modal.classList.add('open');

    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
    }
  },

  openAnswersAnalysis() {
    const data = this.lastResultData;
    if (!data || !data.details || !data.is_published) {
      alert("Natijalar va to'liq tahlil test admin tomonidan to'xtatilib, rasmiy e'lon qilingach ochiladi.");
      return;
    }

    const container = document.getElementById('analysis-items-container');
    if (!container) return;

    let html = '';
    const details = data.details;

    Object.keys(details).forEach(key => {
      const item = details[key];
      const isCorrect = item.status === 'correct';
      const isUnanswered = item.status === 'unanswered';
      
      let statusBg = isCorrect ? 'rgba(16, 185, 129, 0.12)' : (isUnanswered ? 'rgba(100, 116, 139, 0.12)' : 'rgba(239, 68, 68, 0.12)');
      let statusBorder = isCorrect ? '#10B981' : (isUnanswered ? '#94A3B8' : '#EF4444');
      let statusIconSvg = isCorrect
        ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10B981" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
        : (isUnanswered
          ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94A3B8" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle></svg>'
          : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#EF4444" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>');
      let statusLabel = isCorrect ? '<span style="color:#10B981;">To\'g\'ri</span>' : (isUnanswered ? '<span style="color:#94A3B8;">Belgilanmagan</span>' : '<span style="color:#EF4444;">Noto\'g\'ri</span>');

      let userAnsDisplay = (item.user || '').trim() || '<span style="color: #94A3B8; font-style: italic;">(Belgilanmadi)</span>';
      let correctAnsDisplay = item.correct || '—';
      let scoreBadge = '';
      if (item.score !== undefined && item.max_score !== undefined) {
        scoreBadge = isCorrect
          ? `<span style="font-size: 11.5px; color: #10B981; font-weight: 700; margin-left: 6px;">(+${item.score} ball)</span>`
          : `<span style="font-size: 11px; color: #94A3B8; margin-left: 6px;">(${item.max_score} ball)</span>`;
      }

      html += `
        <div style="background: ${statusBg}; border: 1px solid ${statusBorder}; border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <span style="font-weight: 800; font-size: 13.5px; color: var(--text-main);">${item.num || key + '-savol'} ${scoreBadge}</span>
            <span style="font-size: 12px; font-weight: 800; display: inline-flex; align-items: center; gap: 4px;">${statusIconSvg} ${statusLabel}</span>
          </div>
          <div style="display: flex; justify-content: space-between; font-size: 13px; margin-top: 2px;">
            <span>Sizning javobingiz: <strong>${userAnsDisplay}</strong></span>
            <span style="color: #10B981;">To'g'ri kalit: <strong>${correctAnsDisplay}</strong></span>
          </div>
        </div>
      `;
    });

    container.innerHTML = html;
    const modal = document.getElementById('analysis-modal');
    if (modal) modal.classList.add('open');
  },

  closeAnswersAnalysis() {
    const modal = document.getElementById('analysis-modal');
    if (modal) modal.classList.remove('open');
  },

  closeWebApp() {
    if (window.Telegram && window.Telegram.WebApp) {
      window.Telegram.WebApp.close();
    } else {
      window.location.reload();
    }
  }
};

window.TestApp = TestApp;
window.dismissSplash = () => TestApp.finishSplashImmediately();
window.finishSplashImmediately = () => TestApp.finishSplashImmediately();

// Mobil klaviatura ochilganda pastki yakunlash tugmasi klaviatura ustiga chiqib qolmasligi uchun
document.addEventListener('focusin', (e) => {
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
    document.body.classList.add('keyboard-open');
  }
});
document.addEventListener('focusout', (e) => {
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
    setTimeout(() => {
      const active = document.activeElement;
      if (!active || (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA')) {
        document.body.classList.remove('keyboard-open');
      }
    }, 120);
  }
});
if (window.visualViewport) {
  const initH = window.visualViewport.height;
  window.visualViewport.addEventListener('resize', () => {
    if (window.visualViewport.height < initH * 0.75) {
      document.body.classList.add('keyboard-open');
    } else {
      const active = document.activeElement;
      if (!active || (active.tagName !== 'INPUT' && active.tagName !== 'TEXTAREA')) {
        document.body.classList.remove('keyboard-open');
      }
    }
  });
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    TestApp.init();
  });
} else {
  TestApp.init();
}
