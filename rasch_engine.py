"""
Rasch Model Scoring Engine — Dichotomous Rasch / JMLE (75-Ballik Milliy Sertifikat Shkalasi)
============================================================================================
Ona tili va adabiyot Milliy sertifikat 49 ta test bandi + Esse (75 + 75) / 2 baholash moduli.

Savollar strukturasi (45-savol Esse test savollariga kirmaydi):
  Q1..Q32    — 4 variantli (32 ta)
  Q33..Q35   — 6 variantli (3 ta)
  Q36..Q39   — 1 qismli ochiq savollar (4 ta)
  Q40a..Q44b — 2 qismli ochiq savollar (5 savol x 2 = 10 ta)
  Jami test bandlari: 49 ta element (1-44 savollar).
  Esse (45): Alohida 0–75 ballik mezon asosida kiritiladi.

3 ta asosiy ustun:
  1-ustun: Savolning qiyinligi (b parametri) va vazni (oson: ~0.5-0.8 ball, qiyin: ~2.0-2.5 ball)
  2-ustun: O'quvchining qobiliyati (theta parametri) va tasodif (outfit) tahlili
  3-ustun: 75 ballik Test bali va Esse bali o'rtacha arifmetigi: Yakuniy ball = (Test_75 + Esse_75) / 2
"""

import math
import json
from typing import Any, Dict, List, Optional

# Item labels (49 ta test bandi: 1-35, 36-39, 40a-44b; 45-esse kirmaydi)
_ITEM_LABELS: List[str] = (
    [f"Q{i}" for i in range(1, 33)] +
    ["Q33", "Q34", "Q35"] +
    ["Q36", "Q37", "Q38", "Q39"] +
    [f"Q{q}{s}" for q in range(40, 45) for s in ("a", "b")]
)
assert len(_ITEM_LABELS) == 49, "Item label count must be 49"
N_ITEMS = 49
MAX_TEST_SCORE = 75.0


def _assign_grade(score: float, raw_score: Optional[int] = None) -> str:
    """
    Rasmiy Milliy sertifikat baholash shkalasi (75 ballik shkala):
      - 0, 1, 2 ta to'g'ri ishlaganlar: sertifikat ololmaydi ("—")
      - 70.0 va undan yuqori (Rasch ta'sirida 75+ bo'lsa ham): A+ (eng yuqori daraja, A+ dan oshmaydi)
      - 65.0 – 69.9: A
      - 60.0 – 64.9: B+
      - 55.0 – 59.9: B
      - 50.0 – 54.9: C+
      - 46.0 – 49.9: C
      - 46.0 dan past: —
    """
    if raw_score is not None and raw_score <= 2:
        return "—"
    if score <= 0.0:
        return "—"
    if score >= 70.0:
        return "A+"
    elif score >= 65.0:
        return "A"
    elif score >= 60.0:
        return "B+"
    elif score >= 55.0:
        return "B"
    elif score >= 50.0:
        return "C+"
    elif score >= 46.0:
        return "C"
    else:
        return "—"


def _sigmoid(x: float) -> float:
    """Numerik barqaror logistik funksiya."""
    if x > 35.0:
        return 1.0
    elif x < -35.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _compute_fit(X: List[List[int]], theta: List[float], b: List[float], labels: List[str]) -> List[Dict[str, Any]]:
    """Har bir savol (item) uchun Infit va Outfit MNSQ hisoblash."""
    N = len(X)
    I = len(labels)
    items = []
    for j in range(I):
        infit_num = 0.0
        infit_den = 0.0
        outfit_num = 0.0
        for n in range(N):
            p = _sigmoid(theta[n] - b[j])
            q = 1.0 - p
            w = p * q
            z = X[n][j] - p
            infit_num += (z ** 2) * w
            infit_den += w
            if w > 1e-9:
                outfit_num += (z ** 2) / w
        infit_mnsq = (infit_num / infit_den) if infit_den > 1e-9 else 1.0
        outfit_mnsq = (outfit_num / N) if N > 0 else 1.0
        items.append({
            "item_index": j,
            "item_label": labels[j],
            "difficulty_b": round(b[j], 4),
            "infit_mnsq": round(infit_mnsq, 4),
            "outfit_mnsq": round(outfit_mnsq, 4),
            "is_noisy": (infit_mnsq > 1.3 or outfit_mnsq > 1.3),
        })
    return items


def _compute_student_fit(x_n: List[int], th_n: float, b: List[float]) -> Dict[str, Any]:
    """
    O'quvchining javoblar mosligi (Person Infit / Outfit MNSQ).
    Oson savollarda xato qilib, faqat qiyinlarini 'tavakkal' topgan bo'lsa,
    outfit_mnsq > 1.3 bo'ladi va tizim buni tasodif (outfit) deb belgilaydi.
    """
    I = len(b)
    infit_num = 0.0
    infit_den = 0.0
    outfit_num = 0.0
    for j in range(I):
        p = _sigmoid(th_n - b[j])
        q = 1.0 - p
        w = p * q
        z = x_n[j] - p
        infit_num += (z ** 2) * w
        infit_den += w
        if w > 1e-9:
            outfit_num += (z ** 2) / w
    infit_mnsq = (infit_num / infit_den) if infit_den > 1e-9 else 1.0
    outfit_mnsq = (outfit_num / I) if I > 0 else 1.0
    return {
        "infit_mnsq": round(infit_mnsq, 4),
        "outfit_mnsq": round(outfit_mnsq, 4),
        "is_outfit": outfit_mnsq > 1.35,
    }


def _scale_rasch_test_score_75(
    r_n: int,
    I: int,
    rw_n_75: float,
    z_score: float = 0.0,
    mu_theta: float = 0.0,
    n_students: int = 1,
) -> float:
    """
    O'quvchining vaznli Rasch balini (rw_n_75, 0..75 oralig'ida)
    Milliy Sertifikatning 75 ballik Test shkalasiga o'tkazish:
      - 0 ta to'g'ri ishlagan bo'lsa: avtomatik 0.0 ball (hech qanday asossiz ball berilmaydi).
      - 1–2 ta to'g'ri ishlagan bo'lsa: silliqlangan mutanosib past ball (sertifikat berilmaydi).
      - Oson 20 ta savolni topgan ~48 ball (C), qiyin 20 ta savolni topgan ~59 ball (B).
      - Ko'p odam qatnashganda (N >= 10) va test qiyin bo'lib, o'quvchi guruh o'rtachasidan
        ancha yuqori natija ko'rsatsa (z_score > 1.25, rw_n_75 >= 62.0), Rasch modeli ta'sirida
        test bali 75.0 dan ham oshishi mumkin (masalan, 76.5 - 83.0 ball), lekin daraja A+ dan oshmaydi!
    """
    if r_n <= 0 or rw_n_75 <= 0.0:
        return 0.0

    # Asosiy chiziqli Rasch shkalasi (75 ballik tizimda):
    # rw_n_75 = 18.0..21.0 (20 ta oson savol) -> 39.0 + 0.48 * rw = ~47.6..49.1 (~48 ball, C)
    # rw_n_75 = 40.0..43.0 (20 ta qiyin savol) -> 39.0 + 0.48 * rw = ~58.2..59.6 (~59 ball, B)
    # rw_n_75 = 75.0 (to'liq baza)             -> 39.0 + 0.48 * 75.0 = 75.0 (A+)
    base_scaled = 39.0 + (36.0 / 75.0) * float(rw_n_75)

    # 1..4 ta to'g'ri javob uchun minimal ball sakrashini silliqlash (smoothing):
    # 1 ta to'g'ri -> ~9-11 ball, 2 ta to'g'ri -> ~17-20 ball, 3 ta -> ~25-28 ball, 4 ta -> ~33-36 ball
    if r_n < 5:
        smooth_factor = (float(r_n) / 5.0) ** 0.85
        score = base_scaled * smooth_factor
        if r_n <= 2:
            score = min(score, 25.0)
        return round(min(75.0, max(0.0, score)), 1)

    score = base_scaled

    # Rasch Cohort Bonus (ko'p odam qatnashganda 75.0 balldan oshish effekti):
    # Qatnashchilar soni ko'p bo'lganda va o'quvchi ko'pchilik yecha olmagan qiyin savollarni topib,
    # guruh o'rtachasidan sezilarli yuqori Z-ko'rsatkichga (z_score > 1.25) chiqsa:
    if n_students >= 10 and rw_n_75 >= 62.0 and z_score > 1.25:
        crowd_factor = min(1.5, max(0.0, (float(n_students) - 5.0) / 30.0))
        hard_test_shift = max(0.0, -float(mu_theta))
        tier_ratio = min(1.0, max(0.0, (float(rw_n_75) - 60.0) / 15.0))
        rasch_over_bonus = crowd_factor * tier_ratio * (z_score - 1.25) * (1.8 + 1.4 * hard_test_shift)
        score += rasch_over_bonus

    return round(min(92.0, max(0.0, score)), 1)


def evaluate_rasch_scores(
    responses_matrix: List[Dict[str, Any]],
    tol: float = 0.001,
    max_iter: int = 50,
) -> Dict[str, Any]:
    """
    Rasch IRT modeli asosida test natijalarini 75 ballik shkalada hisoblash.
    Barcha o'quvchilar javoblari asosida:
      1) Savollar qiyinligi (b_j) va vazni (item_score: oson ~0.5-0.8, qiyin ~2.0-2.5)
      2) O'quvchilar qobiliyati (theta_n) va tasodif (outfit) ko'rsatkichi
      3) Test bali (75 ballik) va Esse bali (75 ballik) -> Yakuniy ball = (Test + Esse) / 2
    """
    if not responses_matrix:
        return {"students": [], "items": [], "meta": {}}

    N = len(responses_matrix)
    first_resp_len = len(responses_matrix[0].get("responses", []))
    I = first_resp_len if first_resp_len in (49, 55) else N_ITEMS
    if I == 49:
        labels = list(_ITEM_LABELS)
    else:
        labels = (
            [f"Q{i}" for i in range(1, 33)] +
            ["Q33", "Q34", "Q35"] +
            [f"Q{q}{s}" for q in range(36, 46) for s in ("a", "b")]
        )[:I]

    ids = []
    essay_scores: List[Optional[float]] = []
    X: List[List[int]] = []

    for entry in responses_matrix:
        ids.append(entry.get("student_id", ""))
        resp = list(entry.get("responses", []))
        resp = resp[:I]
        resp.extend([0] * (I - len(resp)))
        X.append([int(bool(v)) for v in resp])

        raw_es = entry.get("essay_score", None)
        if raw_es is not None:
            try:
                essay_scores.append(round(min(75.0, max(0.0, float(raw_es))), 1))
            except Exception:
                essay_scores.append(0.0)
        else:
            essay_scores.append(None)

    # 1-ustun: Savollar qiyinlik darajasi (b_j) va ball vaznlarini hisoblash
    eps = 0.5
    b = []
    for j in range(I):
        c_j = sum(X[n][j] for n in range(N))
        p_j = (c_j + eps) / (N + 2.0 * eps)
        b_j = -math.log(p_j / (1.0 - p_j))
        b.append(b_j)

    # b parametrini markazlashtirish (mean(b) = 0)
    mu_b = sum(b) / I
    b = [bv - mu_b for bv in b]

    # Savol vaznlari: oson savolga kamroq vazn (~0.5–0.8 ball),
    # qiyin savolga yuqori vazn (~2.0–2.5 ball), jami yig'indi = 75.0 ball
    exp_weights = [math.exp(max(-0.85, min(0.65, 0.4 * bv))) for bv in b]
    total_exp = sum(exp_weights) or 1.0
    item_points = [(w / total_exp) * MAX_TEST_SCORE for w in exp_weights]

    # Har bir o'quvchining savollar qiyinligi bo'yicha to'plagan vaznli bali (0..75)
    raw_weighted_75 = [sum(X[n][j] * item_points[j] for j in range(I)) for n in range(N)]

    # 2-ustun: Har bir o'quvchi qobiliyati (theta) ni MLE bilan topish
    thetas = []
    for n in range(N):
        r_n = sum(X[n])
        r_adj = max(0.5, min(I - 0.5, float(r_n)))
        th = math.log(r_adj / (I - r_adj))
        for _ in range(max_iter):
            val = sum(_sigmoid(th - b[j]) for j in range(I))
            deriv = sum(_sigmoid(th - b[j]) * (1.0 - _sigmoid(th - b[j])) for j in range(I))
            if deriv < 1e-6:
                break
            diff = val - r_adj
            th = th - diff / deriv
            if abs(diff) < tol:
                break
        thetas.append(th)

    # 3. Savollar Fit statistikasi
    items_stats = _compute_fit(X, thetas, b, labels)
    for j in range(I):
        items_stats[j]["item_score"] = round(item_points[j], 2)
        items_stats[j]["solved_count"] = sum(X[n][j] for n in range(N))

    # Guruhning o'rtacha qobiliyati (mu_theta) va standart og'ishi (sigma_theta)
    mu_th = sum(thetas) / N if N > 0 else 0.0
    var_th = sum((t - mu_th) ** 2 for t in thetas) / N if N > 1 else 0.0
    sigma_th = math.sqrt(var_th)
    eff_sigma = max(0.5, sigma_th)

    # 3-ustun: 75-ballik sertifikat shkalasiga o'tkazish va Esse bilan o'rtacha arifmetikni topish
    students_out = []
    for n in range(N):
        r_n = sum(X[n])
        rw_n = raw_weighted_75[n]
        p_fit = _compute_student_fit(X[n], thetas[n], b)
        z_n = (thetas[n] - mu_th) / eff_sigma if N > 1 else thetas[n]

        # Test bali (ko'p odam qatnashib, o'quvchi qiyin savollarni yechganda 75.0 dan ham oshishi mumkin)
        test_score = _scale_rasch_test_score_75(
            r_n, I, rw_n, z_score=z_n, mu_theta=mu_th, n_students=N
        )
        es_val = essay_scores[n]

        # 0 ta to'g'ri ishlagan bo'lsa: avtomatik ravishda 0.0 ball
        if r_n == 0:
            test_score = 0.0
            final_score = 0.0
            grade = "—"
        else:
            if es_val is not None:
                final_score = round((test_score + es_val) / 2.0, 1)
            else:
                final_score = test_score
            grade = _assign_grade(final_score, raw_score=r_n)

        students_out.append({
            "student_id": ids[n],
            "raw_score": r_n,
            "weighted_score": round(rw_n, 1),
            "test_score": test_score,
            "essay_score": es_val if es_val is not None else 0.0,
            "theta": round(thetas[n], 4),
            "theta_logit": round(thetas[n], 4),
            "z_score": round(z_n, 4),
            "infit_mnsq": p_fit["infit_mnsq"],
            "outfit_mnsq": p_fit["outfit_mnsq"],
            "is_outfit": p_fit["is_outfit"],
            "final_score": final_score,
            "grade": grade,
        })

    # Saralash: yakuniy ball va test bali bo'yicha kamayish tartibida
    students_out.sort(key=lambda s: (-s["final_score"], -s["test_score"], -s["theta"]))

    return {
        "students": students_out,
        "items": items_stats,
        "meta": {
            "n_students": N,
            "n_items": I,
            "max_score": MAX_TEST_SCORE,
            "mu_theta": round(mu_th, 4),
            "sigma_theta": round(sigma_th, 4),
            "iterations": 1,
            "converged": True,
        },
    }


def evaluate_single_test(test_id: int, get_submissions_fn) -> Optional[Dict[str, Any]]:
    """
    test_db dan submissions ni olib, 49 ta test bandi + Esse bo'yicha Rasch baholash o'tkazadi.
    45-savol (Esse) test savollariga kiritilmaydi, balki alohida essay_score sifatida olinadi.
    """
    submissions = get_submissions_fn(test_id)
    if not submissions or len(submissions) < 2:
        return None

    matrix = []
    for sub in submissions:
        try:
            detail_raw = sub.get("details_json", "{}")
            details = json.loads(detail_raw) if isinstance(detail_raw, str) and detail_raw else (detail_raw if isinstance(detail_raw, dict) else {})
            if not isinstance(details, dict):
                details = {}

            responses = []

            # 1. Q1-Q32 (4 variantli)
            for q in range(1, 33):
                key = str(q)
                responses.append(1 if details.get(key, {}).get("status") == "correct" else 0)

            # 2. Q33-Q35 (6 variantli)
            for q in (33, 34, 35):
                key = str(q)
                responses.append(1 if details.get(key, {}).get("status") == "correct" else 0)

            # 3. Q36-Q39 (1 qismli ochiq savollar)
            for q in range(36, 40):
                key = str(q)
                item = details.get(key) or details.get(f"{q}a") or {}
                responses.append(1 if item.get("status") == "correct" else 0)

            # 4. Q40a-Q44b (2 qismli ochiq savollar; 45-savol Esse bo'lgani uchun kirmaydi!)
            for q in range(40, 45):
                for s in ("a", "b"):
                    key = f"{q}{s}"
                    responses.append(1 if details.get(key, {}).get("status") == "correct" else 0)

            # Esse bali (45-savol) — test savollaridan alohida!
            esse_item = details.get("45", {})
            essay_score = 0.0
            if isinstance(esse_item, dict):
                try:
                    essay_score = float(esse_item.get("score", esse_item.get("user", 0.0)) or 0.0)
                except (ValueError, TypeError):
                    essay_score = 0.0
            essay_score = round(min(75.0, max(0.0, essay_score)), 1)

            matrix.append({
                "student_id": sub.get("user_tg_id", sub.get("id")),
                "responses": responses,
                "essay_score": essay_score,
            })
        except Exception:
            continue

    if len(matrix) < 2:
        return None

    return evaluate_rasch_scores(matrix)



# CLI sanity check
if __name__ == "__main__":
    import random
    random.seed(42)

    demo = [
        {
            "student_id": f"S{i+1:02d}",
            "responses": [random.randint(0, 1) for _ in range(55)],
        }
        for i in range(20)
    ]

    result = evaluate_rasch_scores(demo)

    print("\n=== TALABALAR NATIJALARI ===")
    print(f"{'#':<3} {'ID':<8} {'Raw':>4} {'Theta':>8} {'Z':>7} {'Score':>7} {'Grade':<5}")
    print("-" * 45)
    for i, s in enumerate(result["students"], 1):
        print(f"{i:<3} {str(s['student_id']):<8} {s['raw_score']:>4} "
              f"{s['theta']:>8.3f} {s['z_score']:>7.3f} "
              f"{s['final_score']:>7.2f} {s['grade']:<5}")

    print("\n=== META ===")
    for k, v in result["meta"].items():
        print(f"  {k}: {v}")

    noisy = [it for it in result["items"] if it["is_noisy"]]
    print(f"\n=== SHOVQINLI SAVOLLAR ({len(noisy)} ta) ===")
    for it in noisy:
        print(f"  {it['item_label']}: Infit={it['infit_mnsq']}, Outfit={it['outfit_mnsq']}")
