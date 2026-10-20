"""
Rasch Measurement Model Analysis Service
=========================================
Ushbu modul 10,000+ o'quvchilar va test topshiriqlari uchun Rasch tahlili (JMLE):
- Logits (O'lchov birligi)
- Person Ability (O'quvchi qobiliyati theta)
- Item Difficulty (Savol qiyinligi beta)
- Standart xatolik (SE) va Ishonchlilik darajasi (Reliability)
- Milliy sertifikat darajalari (A+, A, B+, B, C+, C)
"""
from __future__ import annotations
import math
import numpy as np
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

import rasch_engine

@dataclass
class ItemDifficulty:
    item_id: str
    difficulty_logit: float
    std_error: float
    correct_count: int
    total_count: int
    difficulty_percentage: float

@dataclass
class PersonAbility:
    student_id: int | str
    ability_logit: float
    std_error: float
    raw_score: int
    percentage: float
    scaled_score: float
    grade: str

async def perform_rasch_analysis_async(responses_matrix: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Asinxron Rasch tahlili:
    responses_matrix formati:
    [
        {"student_id": 12345678, "responses": [1, 0, 1, 1, ... 55 ta]},
        ...
    ]
    """
    if not responses_matrix:
        return {
            "status": "empty",
            "message": "Tahlil uchun javoblar yetarli emas",
            "students": [],
            "items": [],
            "reliability": 0.0
        }

    # Rasch JMLE hisoblash
    raw_results = rasch_engine.evaluate_rasch_scores(responses_matrix)
    return raw_results

def calculate_single_ability(correct_count: int, total_questions: int = 55) -> Dict[str, Any]:
    """Bitta o'quvchi uchun tezkor analitik baho va logit hisoblash."""
    r = max(0, min(total_questions, correct_count))
    if r == 0:
        logit = -4.5
    elif r == total_questions:
        logit = 4.5
    else:
        p = r / total_questions
        logit = round(math.log(p / (1.0 - p)), 3)

    # 100 ballik shkalaga o'tkazish
    # Standart logit [-4, +4] oralig'ini [0, 100] shkalasiga xaritalash
    scaled_score = round(max(0.0, min(100.0, (logit + 4.0) * 12.5)), 1)
    grade = rasch_engine._assign_grade(scaled_score, raw_score=r)

    return {
        "raw_score": r,
        "total": total_questions,
        "logit": logit,
        "scaled_score": scaled_score,
        "grade": grade
    }
