/**
 * ==========================================================================
 * MINI APP CORE & HAPTIC FEEDBACK LIBRARY (Universal & Reusable)
 * Har qanday Telegram Mini App'da 1 qatorda ulab ishlatish mumkin:
 * <script src="mini_app_core.js"></script>
 * ==========================================================================
 */

const MiniApp = {
  tg: window.Telegram?.WebApp,

  /**
   * Ilovani Telegram bilan bog'lab ishga tushirish
   */
  init(options = {}) {
    if (this.tg) {
      try {
        // Mini App oynasini to'liq ochish
        this.tg.ready();
        this.tg.expand();

        // Trackpad orqali mini ilovadan chiqib ketishning oldini olish (vertikal swipe cheklovi)
        if (this.tg?.disableVerticalSwipes) {
          this.tg.disableVerticalSwipes();
        }

        // Telegram 8.0+ Fullscreen rejimini yoqish
        if (typeof this.tg?.requestFullscreen === 'function') {
          try { this.tg.requestFullscreen(); } catch (err) {}
        }

        // Tasodifan yopilib ketishining oldini olish (agar kerak bo'lsa)
        if (options.confirmClose && this.tg.enableClosingConfirmation) {
          this.tg.enableClosingConfirmation();
        }

        // Ranglarni Telegram mavzusiga moslash
        this.syncTheme(options.onThemeChange);
      } catch (e) {
        console.warn('[MiniApp] Telegram WebApp initsializatsiyasida xatolik:', e);
      }
    }

    // Bosilish tebranishlari (Haptic feedback) ni avtomatik ulash
    this.initHaptics();

    // To'lqin (Ripple) effektini avtomatik ulash
    this.initRipple();
  },

  /**
   * Haptic Feedback (Telefonni titratish)
   * @param {string} type - 'light' | 'medium' | 'heavy' | 'selection' | 'success' | 'warning' | 'error'
   */
  haptic(type = 'light') {
    if (!this.tg?.HapticFeedback) return;

    try {
      switch (type) {
        case 'light':
        case 'medium':
        case 'heavy':
        case 'rigid':
        case 'soft':
          this.tg.HapticFeedback.impactOccurred(type);
          break;
        case 'selection':
          this.tg.HapticFeedback.selectionChanged();
          break;
        case 'success':
        case 'warning':
        case 'error':
          this.tg.HapticFeedback.notificationOccurred(type);
          break;
        default:
          this.tg.HapticFeedback.impactOccurred('light');
      }
    } catch (err) {
      // Brauzerda xatolik bermasligi uchun
    }
  },

  /**
   * [data-haptic="type"] atributi bor barcha tugmalarni avtomatik titratish
   */
  initHaptics() {
    document.addEventListener('click', (e) => {
      const el = e.target.closest('[data-haptic]');
      if (el) {
        const type = el.getAttribute('data-haptic') || 'light';
        this.haptic(type);
      }
    }, { passive: true });
  },

  /**
   * [data-ripple="true"] atributi bor elementlarga bosilgan koordinatadan to'lqin hosil qilish
   */
  initRipple() {
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-ripple="true"]');
      if (!btn) return;

      const rect = btn.getBoundingClientRect();
      const circle = document.createElement('span');
      const diameter = Math.max(rect.width, rect.height);
      const radius = diameter / 2;

      circle.style.width = circle.style.height = `${diameter}px`;
      circle.style.left = `${e.clientX - rect.left - radius}px`;
      circle.style.top = `${e.clientY - rect.top - radius}px`;
      circle.classList.add('ripple-ink');

      const oldRipple = btn.querySelector('.ripple-ink');
      if (oldRipple) oldRipple.remove();

      btn.appendChild(circle);
      setTimeout(() => circle.remove(), 600);
    });
  },

  /**
   * Telegram mavzusi (Light / Dark) bilan avtomatik sinxronlash
   */
  syncTheme(callback) {
    const isDark = this.tg?.colorScheme === 'dark';
    if (isDark) {
      document.body.classList.add('dark-mode');
      document.documentElement.classList.add('dark-mode');
    }
    if (typeof callback === 'function') {
      callback(this.tg?.colorScheme);
    }
  },

  /**
   * Tugmaga yuklanish holatini berish (Loading Spinner)
   */
  showLoading(btn) {
    if (!btn) return;
    btn.classList.add('btn-loading-state');
    btn.disabled = true;
  },

  /**
   * Tugma yuklanish holatini to'xtatish
   */
  hideLoading(btn) {
    if (!btn) return;
    btn.classList.remove('btn-loading-state');
    btn.disabled = false;
  },

  /**
   * Foydalanuvchi ma'lumotlarini olish
   */
  getUser() {
    return this.tg?.initDataUnsafe?.user || null;
  }
};

// Sahifa yuklanishi bilan avtomatik ishga tushirish
document.addEventListener('DOMContentLoaded', () => {
  MiniApp.init();
});
