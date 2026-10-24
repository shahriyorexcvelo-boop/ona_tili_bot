"""
10,000 Foydalanuvchi Yuklama Sinovi (Stress / Benchmark Test)
============================================================
Ona Tili Bot & Mini App tizimi uchun maksimal quvvat sinovi:
- /start & Auth (Profil tekshirish)
- Mini App testlar ro'yxatini yuklash (/api/app/active-tests)
- Test kodi bo'yicha testni ochish va javoblarni kiritish
- Testni yakunlash va topshirish (/api/submit-test)
- Natijalarni ko'rish (/api/app/my-results)

Maksimal xavfsiz parallelizm bilan ishlaydi (serverni qulatmagan holda eng yuqori RPS).
Oxirida 1 sekund va 1 daqiqadagi ko'rsatkichlar bilan to'liq statistika chiqariladi.
"""
from __future__ import annotations
import os
import sys
import time
import math
import json
import hmac
import hashlib
import asyncio
import argparse
import urllib.parse
from typing import List, Dict, Any, Optional

import aiohttp

# Repo root
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from core.config import BOT_TOKEN
except Exception:
    BOT_TOKEN = "8901806535:AAH3k_mock_token_for_benchmarking"

import test_db


class Benchmark10K:
    def __init__(self, base_url: str, total_users: int = 10000, concurrency: int = 60, test_id: int = 12):
        self.base_url = base_url.rstrip("/")
        self.total_users = total_users
        self.concurrency = concurrency
        self.test_id = test_id
        self.start_user_id = 910000001  # Test foydalanuvchilar ID diapazoni

        self.latencies: List[float] = []
        self.endpoint_latencies: Dict[str, List[float]] = {
            "profile": [],
            "active_tests": [],
            "submit_test": [],
            "my_results": []
        }
        self.status_counts: Dict[int, int] = {}
        self.endpoint_errors: Dict[str, Dict[int, int]] = {
            "profile": {},
            "active_tests": {},
            "submit_test": {},
            "my_results": {}
        }
        self.completed_users = 0
        self.failed_users = 0
        self.first_error_per_endpoint: Dict[str, str] = {}
        self.lock = asyncio.Lock()

    def _create_init_data(self, user_id: int) -> str:
        now = int(time.time())
        user_str = json.dumps({
            "id": user_id,
            "first_name": f"User_{user_id}",
            "last_name": "Test",
            "username": f"user_{user_id}"
        }, separators=(",", ":"))

        data = {
            "auth_date": str(now),
            "user": user_str
        }
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode("utf-8"), hashlib.sha256).digest()
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        calc_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        data["hash"] = calc_hash
        return urllib.parse.urlencode(data)

    async def prepare_database(self):
        """Test uchun bazani tayyorlash (foydalanuvchilarni approved qilish va testni faollashtirish)"""
        print("\n🔧 1. Ma'lumotlar bazasi tayyorlanmoqda...")
        # 1. Testni faollashtirish
        try:
            with test_db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE tests
                    SET is_active = 1,
                        results_published = 0,
                        time_limit_min = 0,
                        created_at = %s,
                        scheduled_start = NULL,
                        scheduled_end = NULL
                    WHERE id = %s;
                """, (int(time.time()) - 3600, self.test_id))
                conn.commit()
                cur.close()
            test_db.invalidate_test_cache()
            print(f"   ✅ Test (ID: {self.test_id}) faollashtirildi (results_published=0).")
        except Exception as e:
            print(f"   ⚠️ Test faollashtirishda ogohlantirish: {e}")

        # 2. Virtual foydalanuvchilarni 'approved' qilib tezkor kiritish (Bulk Insert)
        print(f"   ⏳ {self.total_users:,} ta sinov foydalanuvchisi bazada ro'yxatdan o'tkazilmoqda...")
        t0 = time.time()
        try:
            with test_db.get_connection() as conn:
                cur = conn.cursor()
                batch_size = 2000
                total_batches = math.ceil(self.total_users / batch_size)
                now_ts = int(time.time())
                for b in range(total_batches):
                    b_start = self.start_user_id + b * batch_size
                    b_end = min(self.start_user_id + (b + 1) * batch_size, self.start_user_id + self.total_users)
                    records = []
                    for uid in range(b_start, b_end):
                        records.append(f"({uid}, 'Sinovchi {uid}', 'test_{uid}', '+99890000{uid % 10000:04d}', 'approved', {now_ts})")
                    if records:
                        sql = f"""
                            INSERT INTO users (tg_id, fullname, username, phone, status, registered_at)
                            VALUES {', '.join(records)}
                            ON CONFLICT (tg_id) DO UPDATE SET status = 'approved';
                        """
                        cur.execute(sql)
                conn.commit()
                cur.close()
            print(f"   ✅ Foydalanuvchilar {time.time() - t0:.2f} soniyada muvaffaqiyatli tayyorlandi!")
        except Exception as e:
            print(f"   ⚠️ Foydalanuvchilarni kiritishda xatolik: {e}")

    async def cleanup_database(self):
        """Sinovdan keyin test ma'lumotlarini tozalash"""
        print("\n🧹 Sinov ma'lumotlari tozalanmoqda...")
        try:
            with test_db.get_connection() as conn:
                cur = conn.cursor()
                cur.execute("DELETE FROM submissions WHERE user_tg_id >= %s AND user_tg_id < %s;",
                            (self.start_user_id, self.start_user_id + self.total_users + 100))
                cur.execute("DELETE FROM users WHERE tg_id >= %s AND tg_id < %s;",
                            (self.start_user_id, self.start_user_id + self.total_users + 100))
                cur.execute("UPDATE tests SET is_active = 0, results_published = 1 WHERE id = %s;", (self.test_id,))
                conn.commit()
                cur.close()
            test_db.invalidate_test_cache()
            print("   ✅ Sinov ma'lumotlari bazadan to'liq tozalandi.")
        except Exception as e:
            print(f"   ⚠️ Tozalashda xatolik: {e}")

    async def _record_req(self, ep: str, status: int, duration: float, error_body: str = ""):
        async with self.lock:
            self.latencies.append(duration)
            self.endpoint_latencies[ep].append(duration)
            self.status_counts[status] = self.status_counts.get(status, 0) + 1
            if status >= 400:
                d = self.endpoint_errors[ep]
                d[status] = d.get(status, 0) + 1
                if ep not in self.first_error_per_endpoint:
                    self.first_error_per_endpoint[ep] = f"HTTP {status}: {error_body.strip()[:180]}"

    async def simulate_single_user(self, session: aiohttp.ClientSession, user_id: int):
        """Bitta foydalanuvchining to'liq o'tish yo'li (Journey)"""
        init_data = self._create_init_data(user_id)
        headers = {
            "Content-Type": "application/json",
            "X-Telegram-User-Id": str(user_id),
            "X-Telegram-Init-Data": init_data
        }

        user_ok = True

        # 1. /start & Profilni ochish (Auth check)
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/profile?tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers, timeout=10) as r:
                st = r.status
                body = await r.text() if st >= 400 else ""
                await self._record_req("profile", st, time.monotonic() - t0, body)
                if st >= 400:
                    user_ok = False
        except Exception as e:
            await self._record_req("profile", 599, time.monotonic() - t0, str(e))
            user_ok = False

        # 2. Mini App ochish & Faol testlarni tekshirish (Active tests list)
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/active-tests?tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers, timeout=10) as r:
                st = r.status
                body = await r.text() if st >= 400 else ""
                await self._record_req("active_tests", st, time.monotonic() - t0, body)
                if st >= 400:
                    user_ok = False
        except Exception as e:
            await self._record_req("active_tests", 599, time.monotonic() - t0, str(e))
            user_ok = False

        # 3. Test topshirish (Submit Test)
        answers = {
            str(i): ("A" if i % 4 == 1 else "B" if i % 4 == 2 else "C" if i % 4 == 3 else "D")
            for i in range(1, 36)
        }
        answers["36"] = "JIDDIY"
        answers["37"] = "“ , - , - : , ” ."
        answers["38"] = "TANQIDIY"
        answers["39"] = "ENAXONNING AKASI"
        answers["40a"] = "QAYTMASLIGINI"
        answers["40b"] = "BOY BERAYOTGANIMIZNI"
        answers["45"] = "65"  # Esse bali

        submit_payload = {
            "test_id": self.test_id,
            "user_tg_id": user_id,
            "fullname": f"Sinovchi {user_id}",
            "answers": answers,
            "init_data": init_data
        }

        t0 = time.monotonic()
        try:
            async with session.post(f"{self.base_url}/api/submit-test", json=submit_payload, headers=headers, timeout=15) as r:
                st = r.status
                body = await r.text() if st >= 400 else ""
                await self._record_req("submit_test", st, time.monotonic() - t0, body)
                if st >= 400:
                    user_ok = False
        except Exception as e:
            await self._record_req("submit_test", 599, time.monotonic() - t0, str(e))
            user_ok = False

        # 4. Natijalarim sahifasini ochish (Results view)
        t0 = time.monotonic()
        try:
            async with session.get(f"{self.base_url}/api/app/my-results?tg_id={user_id}&init_data={urllib.parse.quote(init_data)}", headers=headers, timeout=10) as r:
                st = r.status
                body = await r.text() if st >= 400 else ""
                await self._record_req("my_results", st, time.monotonic() - t0, body)
                if st >= 400:
                    user_ok = False
        except Exception as e:
            await self._record_req("my_results", 599, time.monotonic() - t0, str(e))
            user_ok = False

        async with self.lock:
            if user_ok:
                self.completed_users += 1
            else:
                self.failed_users += 1

    async def wait_for_server(self, max_wait: float = 15.0):
        t0 = time.time()
        while time.time() - t0 < max_wait:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.get(f"{self.base_url}/ping", timeout=2) as r:
                        if r.status == 200:
                            return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        return False

    async def run_benchmark(self, cleanup: bool = True):
        await self.prepare_database()
        if not await self.wait_for_server():
            print(f"❌ Server {self.base_url} bilan aloqa yo'q!")
            return

        print(f"\n🚀 2. YUKLAMA SINOVI BOSHLANMOQDA:")
        print(f"   👥 Jami foydalanuvchilar: {self.total_users:,} ta")
        print(f"   ⚡️ Parallel ishlovchilar (Concurrency): {self.concurrency} ta worker")
        print(f"   🎯 Har bir user uchun 4 ta to'liq harakat: Profil -> Testni tanlash -> Topsirish -> Natija")
        print(f"   🌐 Kutilayotgan umumiy HTTP so'rovlar: {self.total_users * 4:,} ta\n")

        # Concurrency Queue
        queue = asyncio.Queue()
        for i in range(self.total_users):
            queue.put_nowait(self.start_user_id + i)

        conn = aiohttp.TCPConnector(limit=self.concurrency * 4, limit_per_host=self.concurrency * 4, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=30)

        start_time = time.monotonic()
        last_report_time = start_time
        last_report_users = 0

        async def worker(session: aiohttp.ClientSession):
            while not queue.empty():
                try:
                    uid = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await self.simulate_single_user(session, uid)
                queue.task_done()

        # Monitor task
        async def monitor():
            nonlocal last_report_time, last_report_users
            while True:
                await asyncio.sleep(2.0)
                now = time.monotonic()
                dt = now - last_report_time
                done = self.completed_users + self.failed_users
                d_users = done - last_report_users
                cur_user_rps = d_users / dt if dt > 0 else 0
                cur_http_rps = cur_user_rps * 4
                pct = (done / self.total_users) * 100.0
                elapsed = now - start_time
                print(f"   📊 [{elapsed:5.1f}s] Bajarildi: {done:5d}/{self.total_users} ({pct:5.1f}%) | "
                      f"Joriy tezlik: {cur_user_rps:5.1f} user/s ({cur_http_rps:6.1f} so'rov/s) | "
                      f"Xatoliklar: {self.failed_users}")
                last_report_time = now
                last_report_users = done
                if done >= self.total_users:
                    break

        mon_task = asyncio.create_task(monitor())

        async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
            workers = [asyncio.create_task(worker(session)) for _ in range(self.concurrency)]
            await asyncio.gather(*workers)

        mon_task.cancel()

        total_elapsed = time.monotonic() - start_time

        # Hisobot chop etish
        self.print_report(total_elapsed)

        if cleanup:
            await self.cleanup_database()

    def print_report(self, total_sec: float):
        total_reqs = len(self.latencies)
        if total_reqs == 0:
            print("❌ So'rovlar amalga oshmadi!")
            return

        sorted_lat = sorted(self.latencies)
        avg_lat = sum(sorted_lat) / total_reqs
        p50 = sorted_lat[int(math.floor(total_reqs * 0.50))]
        p95 = sorted_lat[min(int(math.floor(total_reqs * 0.95)), total_reqs - 1)]
        p99 = sorted_lat[min(int(math.floor(total_reqs * 0.99)), total_reqs - 1)]
        max_lat = sorted_lat[-1]

        c_2xx = sum(v for k, v in self.status_counts.items() if 200 <= k < 300)
        c_4xx = sum(v for k, v in self.status_counts.items() if 400 <= k < 500)
        c_5xx = sum(v for k, v in self.status_counts.items() if k >= 500)

        rps = total_reqs / total_sec
        rpm = rps * 60.0
        users_per_sec = self.total_users / total_sec
        users_per_min = users_per_sec * 60.0

        print("\n" + "=" * 70)
        print("          10,000 FOYDALANUVCHI YUKLAMA SINOVI YAKUNIY STATISTIKASI")
        print("=" * 70)
        print(f"  ⏱ Umumiy sarflangan vaqt:       {total_sec:.2f} soniya ({total_sec / 60:.2f} daqiqa)")
        print(f"  👥 Jami o'tkazilgan userlar:     {self.total_users:,} ta")
        print(f"  ✅ Muvaffaqiyatli to'liq topshirdi: {self.completed_users:,} ta ({(self.completed_users / self.total_users) * 100:.2f}%)")
        print(f"  ❌ Muvaffaqiyatsiz bo'ldi:       {self.failed_users:,} ta")
        print(f"  🌐 Jami bajarilgan HTTP so'rov:  {total_reqs:,} ta")
        print("-" * 70)
        print("  🔥 QUVVAT VA TEZLIK (THROUGHPUT):")
        print(f"  ⚡️ 1 SEKUNDDA (RPS):              {rps:.1f} ta so'rov/sekund")
        print(f"  👤 1 SEKUNDDA TOPSHIRGAN USERLAR: {users_per_sec:.1f} ta o'quvchi/sekund")
        print(f"  🚀 1 DAQIQADA (RPM):              {rpm:,.0f} ta so'rov/daqiqa")
        print(f"  🏆 1 DAQIQADA TOPSHIRA OLADI:     {users_per_min:,.0f} ta o'quvchi/daqiqa")
        print("-" * 70)
        print("  ⏱ JAVOB BERISH KECHIKISHI (LATENCY):")
        print(f"  O'rtacha kechikish (Avg):        {avg_lat * 1000:.1f} ms")
        print(f"  Median (p50):                    {p50 * 1000:.1f} ms")
        print(f"  95% foydalanuvchilar (p95):      {p95 * 1000:.1f} ms ({p95:.3f} s)")
        print(f"  99% foydalanuvchilar (p99):      {p99 * 1000:.1f} ms ({p99:.3f} s)")
        print(f"  Maksimal kechikish (Max):        {max_lat * 1000:.1f} ms")
        print("-" * 70)
        print("  📊 HTTP STATUS KODLARI VA XATOLIKLAR:")
        print(f"  Muvaffaqiyatli (2xx):            {c_2xx:,} ({c_2xx / total_reqs * 100:.2f}%)")
        print(f"  Mijoz xatosi (4xx):              {c_4xx:,} ({c_4xx / total_reqs * 100:.2f}%)")
        print(f"  Server xatosi (5xx):             {c_5xx:,} ({c_5xx / total_reqs * 100:.2f}%)")
        print("-" * 70)
        print("  🔍 BOSQICHLAR BO'YICHA TAHLIL:")
        for ep, ep_name in [
            ("profile", "1. Profil / Auth (/api/app/profile)"),
            ("active_tests", "2. Mini App ochish (/api/app/active-tests)"),
            ("submit_test", "3. Test topshirish (/api/submit-test)"),
            ("my_results", "4. Natijalarim (/api/app/my-results)")
        ]:
            lats = sorted(self.endpoint_latencies[ep]) if self.endpoint_latencies[ep] else [0]
            ep_avg = (sum(lats) / len(lats)) * 1000
            ep_p95 = lats[min(int(len(lats) * 0.95), len(lats) - 1)] * 1000
            ep_errs = sum(self.endpoint_errors[ep].values())
            first_err = f" -> {self.first_error_per_endpoint[ep]}" if ep in self.first_error_per_endpoint else ""
            print(f"    • {ep_name:<40}: Avg {ep_avg:6.1f} ms | p95 {ep_p95:6.1f} ms | Xatolar: {ep_errs}{first_err}")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="10K User Load Benchmark")
    parser.add_argument("--url", default="http://127.0.0.1:8082", help="Server URL manzili")
    parser.add_argument("--users", type=int, default=10000, help="Jami foydalanuvchilar soni (default: 10000)")
    parser.add_argument("--concurrency", type=int, default=60, help="Parallel ishlovchilar soni (default: 60)")
    parser.add_argument("--no-cleanup", action="store_true", help="Sinovdan keyin tozalashni bekor qilish")
    args = parser.parse_args()

    bench = Benchmark10K(
        base_url=args.url,
        total_users=args.users,
        concurrency=args.concurrency
    )
    asyncio.run(bench.run_benchmark(cleanup=not args.no_cleanup))
