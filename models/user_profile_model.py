# user_profile_model.py — MindFlow Personalization Layer
# Location: models/user_profile_model.py
#
# WHAT THIS DOES:
#   Each user gets their own adaptive ML profile that learns:
#     1. Personal stress baseline     — your "normal" is not the same as anyone else's
#     2. Stress-performance curve     — do YOU work better at stress=40 or stress=20?
#     3. Time-of-day stress rhythms   — morning/afternoon/evening patterns
#     4. Modality reliability         — is your face or voice a better stress signal?
#     5. Task-type sensitivity        — creative vs routine tasks under stress
#     6. Recovery rate                — how fast do you typically de-stress?
#
# ML APPROACH:
#   - Per-user online learning (SGD Regressor) updates with every session
#   - Bayesian-style prior from population data, updated with user observations
#   - Feature store: SQLite per-user history (lightweight, no server required)
#   - Confidence-aware: low-data users get population priors; high-data users
#     get personalized predictions
#
# USED BY bridge.py:
#   from models.user_profile_model import UserStressProfile
#   profile = UserStressProfile(user_id="user_123")
#   adjusted = profile.adjust_stress_score(raw_score, context)
#   profile.record_session(raw_score, adjusted, task_outcome, context)

import os
import json
import time
import sqlite3
import pickle
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

# ─── PATHS ────────────────────────────────────────────────────────────────────
_MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROFILE_DIR = os.path.join(_MODULE_DIR, "user_profiles")
os.makedirs(_PROFILE_DIR, exist_ok=True)


# ─── POPULATION PRIORS (from published stress research) ──────────────────────
# These are the defaults before user data accumulates.
# Source: Lazarus & Folkman (1984), Epel et al. (2018), NIOSH stress research
POPULATION_PRIOR = {
    "stress_baseline":        35.0,   # avg resting stress score (0–100)
    "stress_baseline_std":    12.0,   # individual variation in baselines
    "peak_performance_stress": 38.0,  # Yerkes-Dodson optimal arousal
    "performance_curve_width": 20.0,  # how wide the "optimal zone" is
    "morning_bias":           +3.0,   # morning cortisol spike
    "afternoon_bias":         -2.0,   # post-lunch dip
    "evening_bias":           -5.0,   # wind-down
    "face_reliability":        0.55,  # default face signal weight
    "voice_reliability":       0.55,  # default voice signal weight
    "recovery_rate":           0.12,  # stress decay per minute (avg)
    "high_stakes_sensitivity": 1.15,  # stress amplification for important tasks
}

# Minimum sessions before personalization kicks in
MIN_SESSIONS_FOR_PERSONALIZATION = 5
FULL_PERSONALIZATION_SESSIONS    = 30


# ─── CONTEXT SCHEMA ───────────────────────────────────────────────────────────
# Context dict passed in with each reading — not all fields are required
# context = {
#   "hour":          int (0–23),
#   "day_of_week":   int (0=Mon, 6=Sun),
#   "task_type":     str ("creative"|"analytical"|"routine"|"meeting"|"unknown"),
#   "task_priority": str ("high"|"medium"|"low"),
#   "session_duration_min": float,
#   "face_conf":     float (0–1),
#   "voice_conf":    float (0–1),
#   "hnr_value":     float (0–1),
# }


class UserStressProfile:
    """
    Per-user adaptive stress model.

    Lifecycle:
        profile = UserStressProfile("user_abc")
        adjusted = profile.adjust_stress_score(raw_score, context)
        # ... session runs ...
        profile.record_session(raw_score, adjusted, outcome, context)
        profile.save()
    """

    def __init__(self, user_id: str):
        self.user_id  = user_id
        self.db_path  = os.path.join(_PROFILE_DIR, f"{user_id}.db")
        self.pkl_path = os.path.join(_PROFILE_DIR, f"{user_id}_model.pkl")

        self._init_db()
        self.params   = self._load_params()
        self.ml_model = self._load_ml_model()
        self.session_count = self._get_session_count()

        print(f"[Profile] User '{user_id}' loaded — {self.session_count} sessions recorded")

    # ─── DB INIT ──────────────────────────────────────────────────────────────
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        REAL,
                hour             INTEGER,
                day_of_week      INTEGER,
                raw_stress       REAL,
                adjusted_stress  REAL,
                face_stress      REAL,
                voice_stress     REAL,
                face_conf        REAL,
                voice_conf       REAL,
                task_type        TEXT,
                task_priority    TEXT,
                tasks_completed  INTEGER,
                tasks_deferred   INTEGER,
                session_duration REAL,
                outcome_rating   REAL,   -- 0–1: did user complete intended work?
                notes            TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS params (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()

    # ─── PARAMS ───────────────────────────────────────────────────────────────
    def _load_params(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        rows = c.execute("SELECT key, value FROM params").fetchall()
        conn.close()
        params = dict(POPULATION_PRIOR)  # start from population prior
        for key, val in rows:
            try:
                params[key] = float(val)
            except Exception:
                params[key] = val
        return params

    def _save_params(self):
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        for key, val in self.params.items():
            c.execute("INSERT OR REPLACE INTO params (key, value) VALUES (?, ?)",
                      (key, str(val)))
        conn.commit()
        conn.close()

    # ─── ML MODEL (online SGD regressor) ──────────────────────────────────────
    def _load_ml_model(self):
        if os.path.exists(self.pkl_path):
            try:
                with open(self.pkl_path, 'rb') as f:
                    model = pickle.load(f)
                print(f"[Profile] Personal ML model loaded for '{self.user_id}'")
                return model
            except Exception as e:
                print(f"[Profile] Could not load model: {e}")
        return None

    def _save_ml_model(self):
        if self.ml_model is not None:
            with open(self.pkl_path, 'wb') as f:
                pickle.dump(self.ml_model, f)

    def _get_session_count(self) -> int:
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        count = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        conn.close()
        return count

    # ─── FEATURE VECTOR FOR ML MODEL ─────────────────────────────────────────
    def _build_feature_vector(self, raw_stress: float, context: Dict) -> np.ndarray:
        """
        Builds an 18-dimensional feature vector for the personal ML model.
        All features normalized to ~0–1 range.

        Features:
          [0]  raw_stress / 100             — input stress signal
          [1]  sin(2π * hour/24)            — time-of-day cyclical encoding
          [2]  cos(2π * hour/24)            —   (both needed for circular continuity)
          [3]  sin(2π * dow/7)              — day-of-week cyclical
          [4]  cos(2π * dow/7)
          [5]  is_morning  (6–11h)          — time-of-day flags
          [6]  is_afternoon (12–17h)
          [7]  is_evening  (18–23h)
          [8]  task_type_creative           — one-hot task type
          [9]  task_type_analytical
          [10] task_type_routine
          [11] task_type_meeting
          [12] task_priority_high           — one-hot priority
          [13] task_priority_medium
          [14] face_conf                    — signal confidence
          [15] voice_conf
          [16] stress_vs_baseline           — deviation from personal baseline
          [17] session_duration_norm        — session length / 60 min
        """
        hour = int(context.get("hour", datetime.now().hour))
        dow  = int(context.get("day_of_week", datetime.now().weekday()))
        task = context.get("task_type", "unknown").lower()
        pri  = context.get("task_priority", "medium").lower()
        fc   = float(context.get("face_conf", 0.5))
        vc   = float(context.get("voice_conf", 0.5))
        dur  = float(context.get("session_duration_min", 5.0))

        baseline = self.params.get("stress_baseline", POPULATION_PRIOR["stress_baseline"])

        feat = np.array([
            raw_stress / 100.0,
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            np.sin(2 * np.pi * dow / 7),
            np.cos(2 * np.pi * dow / 7),
            float(6  <= hour <= 11),
            float(12 <= hour <= 17),
            float(18 <= hour <= 23),
            float(task == "creative"),
            float(task == "analytical"),
            float(task == "routine"),
            float(task == "meeting"),
            float(pri == "high"),
            float(pri == "medium"),
            np.clip(fc, 0, 1),
            np.clip(vc, 0, 1),
            np.clip((raw_stress - baseline) / 50.0, -1, 1),
            np.clip(dur / 60.0, 0, 2),
        ], dtype=np.float32)

        return feat

    # ─── PERSONALIZATION BLEND WEIGHT ─────────────────────────────────────────
    def _personalization_weight(self) -> float:
        """
        Returns 0.0 → 1.0 representing how much to trust personalized model
        vs population prior.
          < MIN_SESSIONS   → 0.0  (pure population prior)
          MIN..FULL range  → linearly interpolates
          ≥ FULL_SESSIONS  → 1.0  (fully personalized)
        """
        n = self.session_count
        if n < MIN_SESSIONS_FOR_PERSONALIZATION:
            return 0.0
        if n >= FULL_PERSONALIZATION_SESSIONS:
            return 1.0
        span = FULL_PERSONALIZATION_SESSIONS - MIN_SESSIONS_FOR_PERSONALIZATION
        return (n - MIN_SESSIONS_FOR_PERSONALIZATION) / span

    # ─── CORE: ADJUST STRESS SCORE ────────────────────────────────────────────
    def adjust_stress_score(self, raw_stress: float, context: Optional[Dict] = None) -> Dict:
        """
        Main entry point. Takes raw fused stress score (0–100) + context,
        returns a dict with:
          - adjusted_stress   : personalized score (0–100)
          - baseline_delta    : how far above/below user's normal
          - performance_zone  : "below_optimal"|"optimal"|"above_optimal"
          - time_adjustment   : stress adjustment applied for this hour
          - personalization_pct: how personalized this reading is (0–100%)
          - insights          : list of human-readable insight strings
        """
        if context is None:
            context = {}

        pw = self._personalization_weight()
        hour = int(context.get("hour", datetime.now().hour))

        # ── 1. Baseline normalization ─────────────────────────────────────────
        baseline = self.params.get("stress_baseline", POPULATION_PRIOR["stress_baseline"])
        # Re-center: a score of 50 for someone with baseline 35 is really ~57
        # for someone with baseline 50. We normalize relative to baseline.
        population_baseline = POPULATION_PRIOR["stress_baseline"]
        baseline_correction = (baseline - population_baseline) * pw
        # If user's baseline is 45 (10 above pop mean), we add 10×pw to score
        # so that "45 for them" maps closer to "55" on the universal scale
        normalized_stress = raw_stress + baseline_correction * 0.5

        # ── 2. Time-of-day adjustment ─────────────────────────────────────────
        # Learned time bias. Pre-personalization: population priors. After: per-user.
        time_adj = self._get_time_adjustment(hour, pw)
        time_adjusted_stress = normalized_stress - time_adj  # subtract because
        # a morning spike means true stress is lower than sensor says

        # ── 3. Modality reliability reweighting ───────────────────────────────
        # If this user's face readings are historically more reliable, trust them more
        face_rel  = self.params.get("face_reliability", POPULATION_PRIOR["face_reliability"])
        voice_rel = self.params.get("voice_reliability", POPULATION_PRIOR["voice_reliability"])
        face_stress  = float(context.get("face_stress",  raw_stress))
        voice_stress = float(context.get("voice_stress", raw_stress))

        if face_rel + voice_rel > 0:
            modality_w_face  = face_rel  / (face_rel + voice_rel)
            modality_w_voice = voice_rel / (face_rel + voice_rel)
        else:
            modality_w_face = modality_w_voice = 0.5

        # Blend personalized modality weights with raw score
        if "face_stress" in context and "voice_stress" in context:
            reweighted = (modality_w_face * face_stress + modality_w_voice * voice_stress)
            modality_adjusted = time_adjusted_stress * (1 - pw) + reweighted * pw
        else:
            modality_adjusted = time_adjusted_stress

        # ── 4. ML model prediction (if enough data) ──────────────────────────
        ml_adjusted = modality_adjusted  # fallback
        if self.ml_model is not None and pw > 0.3:
            try:
                feat = self._build_feature_vector(raw_stress, context)
                ml_pred = float(self.ml_model.predict(feat.reshape(1, -1))[0])
                # Blend ML prediction into score
                ml_adjusted = modality_adjusted * (1 - pw * 0.4) + ml_pred * (pw * 0.4)
            except Exception as e:
                print(f"[Profile] ML prediction error: {e}")

        # ── 5. Clip and round ─────────────────────────────────────────────────
        adjusted = float(np.clip(ml_adjusted, 0, 100))

        # ── 6. Performance zone ───────────────────────────────────────────────
        peak   = self.params.get("peak_performance_stress", POPULATION_PRIOR["peak_performance_stress"])
        width  = self.params.get("performance_curve_width", POPULATION_PRIOR["performance_curve_width"])
        lo, hi = peak - width / 2, peak + width / 2

        if adjusted < lo:
            perf_zone = "below_optimal"
        elif adjusted <= hi:
            perf_zone = "optimal"
        else:
            perf_zone = "above_optimal"

        # ── 7. Insights ───────────────────────────────────────────────────────
        insights = self._generate_insights(raw_stress, adjusted, context, peak, perf_zone, pw)

        return {
            "adjusted_stress":     round(adjusted, 1),
            "raw_stress":          round(raw_stress, 1),
            "baseline":            round(baseline, 1),
            "baseline_delta":      round(adjusted - baseline, 1),
            "performance_zone":    perf_zone,
            "peak_stress":         round(peak, 1),
            "time_adjustment":     round(time_adj, 1),
            "personalization_pct": round(pw * 100),
            "face_weight":         round(modality_w_face * (face_rel + voice_rel) / 2 / 0.55, 2),
            "voice_weight":        round(modality_w_voice * (face_rel + voice_rel) / 2 / 0.55, 2),
            "insights":            insights,
        }

    # ─── TIME-OF-DAY ADJUSTMENT ───────────────────────────────────────────────
    def _get_time_adjustment(self, hour: int, pw: float) -> float:
        """
        Returns expected stress bias for this hour.
        Pre-personalization: population biases. Post: learned per-user.
        """
        # Population prior by hour-block
        if 6 <= hour <= 11:
            pop_adj = POPULATION_PRIOR["morning_bias"]
        elif 12 <= hour <= 17:
            pop_adj = POPULATION_PRIOR["afternoon_bias"]
        elif 18 <= hour <= 23:
            pop_adj = POPULATION_PRIOR["evening_bias"]
        else:
            pop_adj = 0.0  # late night — minimal data, no adjustment

        # Learned per-user bias (stored as "time_bias_H" for each hour)
        learned_key = f"time_bias_{hour}"
        learned_adj = float(self.params.get(learned_key, pop_adj))

        # Blend population vs learned
        return pop_adj * (1 - pw) + learned_adj * pw

    # ─── INSIGHTS GENERATOR ───────────────────────────────────────────────────
    def _generate_insights(self, raw: float, adj: float, context: Dict,
                            peak: float, zone: str, pw: float) -> List[str]:
        insights = []
        hour = int(context.get("hour", datetime.now().hour))
        task = context.get("task_type", "unknown")
        pri  = context.get("task_priority", "medium")

        if pw >= 0.5:
            # Personalized insights
            baseline = self.params.get("stress_baseline", 35)
            delta    = adj - baseline
            if abs(delta) < 5:
                insights.append(f"You're at your normal baseline ({baseline:.0f}) today.")
            elif delta > 15:
                insights.append(f"You're {delta:.0f} points above your personal baseline — unusually high for you.")
            elif delta < -10:
                insights.append(f"You're notably calmer than your usual baseline of {baseline:.0f}.")

            if zone == "optimal":
                insights.append(f"You're in your personal performance sweet spot ({peak-10:.0f}–{peak+10:.0f}).")
            elif zone == "above_optimal":
                insights.append("Stress is above your optimal zone — consider a 5-min break before tackling complex work.")

            # Time-of-day pattern insight
            learned_key = f"time_bias_{hour}"
            if learned_key in self.params:
                lb = float(self.params[learned_key])
                if abs(lb) > 5:
                    direction = "higher" if lb > 0 else "lower"
                    insights.append(f"Your data shows you typically run {direction} at this hour — score adjusted for your pattern.")

        else:
            n_remaining = MIN_SESSIONS_FOR_PERSONALIZATION - self.session_count
            if n_remaining > 0:
                insights.append(f"Using population averages. {n_remaining} more sessions to unlock your personal model.")
            else:
                insights.append("Building your personal stress model — patterns will appear soon.")

        # Task-specific advice
        if task == "creative" and adj > 60:
            insights.append("Creative work suffers most under high stress — try journalling or a short walk first.")
        elif task == "analytical" and zone == "optimal":
            insights.append("Analytical tasks suit your current stress level well.")
        elif task == "meeting" and adj > 70:
            insights.append("Consider a 2-min breathing exercise before your meeting.")

        # Priority-specific
        if pri == "high" and adj > 65:
            insights.append("High-priority task + elevated stress: break it into 25-min Pomodoro chunks.")

        return insights[:4]  # cap at 4 insights

    # ─── RECORD SESSION ───────────────────────────────────────────────────────
    def record_session(
        self,
        raw_stress:      float,
        adjusted_stress: float,
        context:         Dict,
        outcome:         Optional[Dict] = None
    ):
        """
        Call this at the end of a session (or periodically during long sessions).

        outcome (optional): {
            "tasks_completed":  int,
            "tasks_deferred":   int,
            "outcome_rating":   float (0–1),   # self-reported or inferred
            "notes":            str
        }
        """
        if outcome is None:
            outcome = {}

        now = time.time()
        dt  = datetime.fromtimestamp(now)

        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        c.execute("""
            INSERT INTO sessions (
                timestamp, hour, day_of_week,
                raw_stress, adjusted_stress,
                face_stress, voice_stress, face_conf, voice_conf,
                task_type, task_priority,
                tasks_completed, tasks_deferred,
                session_duration, outcome_rating, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            now,
            dt.hour,
            dt.weekday(),
            raw_stress,
            adjusted_stress,
            float(context.get("face_stress", raw_stress)),
            float(context.get("voice_stress", raw_stress)),
            float(context.get("face_conf", 0.5)),
            float(context.get("voice_conf", 0.5)),
            context.get("task_type", "unknown"),
            context.get("task_priority", "medium"),
            int(outcome.get("tasks_completed", 0)),
            int(outcome.get("tasks_deferred", 0)),
            float(context.get("session_duration_min", 5.0)),
            float(outcome.get("outcome_rating", -1.0)),
            outcome.get("notes", ""),
        ))
        conn.commit()
        conn.close()

        self.session_count += 1
        print(f"[Profile] Session recorded. Total: {self.session_count}")

        # Incrementally update parameters and ML model
        self._update_params_incremental(raw_stress, adjusted_stress, context, outcome)

        if self.session_count % 5 == 0:
            self._retrain_ml_model()

        self.save()

    # ─── INCREMENTAL PARAM UPDATE ─────────────────────────────────────────────
    def _update_params_incremental(
        self,
        raw:     float,
        adj:     float,
        context: Dict,
        outcome: Dict
    ):
        """
        Online Bayesian update of key parameters.
        Uses exponential moving average (EMA) with a learning rate that slows
        as more data accumulates — classic online Bayesian update.
        """
        n  = self.session_count
        lr = max(0.02, 1.0 / (n + 10))  # learning rate decays with more data

        # ── 1. Stress baseline (EMA of raw stress readings) ───────────────────
        # Baseline = the user's "typical" resting score
        # We use a slow EMA so it tracks genuine shifts, not noise
        current_baseline = self.params.get("stress_baseline", POPULATION_PRIOR["stress_baseline"])
        new_baseline = current_baseline * (1 - lr * 0.3) + raw * (lr * 0.3)
        self.params["stress_baseline"] = new_baseline

        # ── 2. Time-of-day bias ───────────────────────────────────────────────
        hour = int(context.get("hour", datetime.now().hour))
        key  = f"time_bias_{hour}"
        # Observed bias = this reading vs current baseline
        observed_bias = raw - new_baseline
        current_bias  = float(self.params.get(key, POPULATION_PRIOR.get("morning_bias", 0)))
        self.params[key] = current_bias * (1 - lr) + observed_bias * lr

        # ── 3. Modality reliability update ───────────────────────────────────
        # If face and voice disagree a lot, reduce confidence in the noisier one
        face_s  = float(context.get("face_stress",  raw))
        voice_s = float(context.get("voice_stress", raw))
        face_c  = float(context.get("face_conf",  0.5))
        voice_c = float(context.get("voice_conf", 0.5))

        face_err  = abs(face_s  - adj)
        voice_err = abs(voice_s - adj)
        max_err   = max(face_err, voice_err, 1e-6)

        # Lower error → higher reliability update
        face_rel_update  = 1.0 - face_err  / (max_err + 50)
        voice_rel_update = 1.0 - voice_err / (max_err + 50)

        fr = self.params.get("face_reliability",  POPULATION_PRIOR["face_reliability"])
        vr = self.params.get("voice_reliability", POPULATION_PRIOR["voice_reliability"])
        self.params["face_reliability"]  = fr * (1 - lr) + face_rel_update  * lr
        self.params["voice_reliability"] = vr * (1 - lr) + voice_rel_update * lr

        # ── 4. Peak performance stress (Yerkes-Dodson optimal) ────────────────
        # If outcome_rating is available, learn which stress levels yield best results
        rating = float(outcome.get("outcome_rating", -1.0))
        if rating >= 0:
            # Soft update: if good outcome, nudge peak toward current stress
            if rating > 0.7:
                peak = self.params.get("peak_performance_stress",
                                       POPULATION_PRIOR["peak_performance_stress"])
                self.params["peak_performance_stress"] = peak * (1 - lr * 0.5) + adj * (lr * 0.5)

        # ── 5. Recovery rate (slope of stress across consecutive sessions) ────
        if n >= 3:
            recent = self._get_recent_stress_series(5)
            if len(recent) >= 3:
                slopes = np.diff(recent)
                negative_slopes = slopes[slopes < 0]
                if len(negative_slopes) > 0:
                    recovery = float(np.mean(np.abs(negative_slopes)))
                    rr = self.params.get("recovery_rate", POPULATION_PRIOR["recovery_rate"])
                    self.params["recovery_rate"] = rr * 0.8 + (recovery / 100) * 0.2

    def _get_recent_stress_series(self, n: int) -> np.ndarray:
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        rows = c.execute(
            "SELECT adjusted_stress FROM sessions ORDER BY timestamp DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        return np.array([r[0] for r in rows], dtype=float)[::-1]  # oldest first

    # ─── RETRAIN PERSONAL ML MODEL ────────────────────────────────────────────
    def _retrain_ml_model(self):
        """
        Trains/retrains the personal SGD Regressor on accumulated session data.
        Called every 5 sessions.
        Target: predict adjusted_stress from context features → personalized prediction.
        """
        conn = sqlite3.connect(self.db_path)
        c    = conn.cursor()
        rows = c.execute("""
            SELECT
                raw_stress, adjusted_stress,
                hour, day_of_week,
                face_stress, voice_stress, face_conf, voice_conf,
                task_type, task_priority, session_duration
            FROM sessions
            ORDER BY timestamp DESC
            LIMIT 200
        """).fetchall()
        conn.close()

        if len(rows) < MIN_SESSIONS_FOR_PERSONALIZATION:
            return

        X, y = [], []
        for row in rows:
            (raw_s, adj_s, hour, dow,
             face_s, voice_s, face_c, voice_c,
             task, pri, dur) = row

            ctx = {
                "hour": hour or 12,
                "day_of_week": dow or 0,
                "face_stress": face_s or raw_s,
                "voice_stress": voice_s or raw_s,
                "face_conf": face_c or 0.5,
                "voice_conf": voice_c or 0.5,
                "task_type": task or "unknown",
                "task_priority": pri or "medium",
                "session_duration_min": dur or 5.0,
            }
            feat = self._build_feature_vector(float(raw_s), ctx)
            X.append(feat)
            y.append(float(adj_s))

        X = np.array(X)
        y = np.array(y)

        try:
            from sklearn.linear_model import Ridge
            from sklearn.preprocessing import StandardScaler
            from sklearn.pipeline import Pipeline

            model = Pipeline([
                ('scaler', StandardScaler()),
                ('reg',    Ridge(alpha=1.0))
            ])
            model.fit(X, y)
            self.ml_model = model

            preds = model.predict(X)
            mae   = np.mean(np.abs(preds - y))
            print(f"[Profile] Personal model retrained. MAE={mae:.2f} on {len(y)} sessions.")
        except Exception as e:
            print(f"[Profile] Retrain error: {e}")

    # ─── STRESS TREND ANALYSIS ────────────────────────────────────────────────
    def get_stress_trends(self, days: int = 7) -> Dict:
        """
        Returns stress trend analysis for the past N days.
        Used by the frontend to show history charts and insights.
        """
        since = time.time() - days * 86400
        conn  = sqlite3.connect(self.db_path)
        c     = conn.cursor()
        rows  = c.execute("""
            SELECT timestamp, hour, day_of_week, raw_stress, adjusted_stress,
                   task_type, outcome_rating
            FROM sessions
            WHERE timestamp >= ?
            ORDER BY timestamp ASC
        """, (since,)).fetchall()
        conn.close()

        if not rows:
            return {"message": "No data yet", "sessions": 0}

        ts      = [r[0] for r in rows]
        raw_s   = [r[3] for r in rows]
        adj_s   = [r[4] for r in rows]
        hours   = [r[1] for r in rows]
        tasks   = [r[5] for r in rows]
        ratings = [r[6] for r in rows if r[6] >= 0]

        # Trend direction
        if len(adj_s) >= 3:
            slope = float(np.polyfit(range(len(adj_s)), adj_s, 1)[0])
            trend = "improving" if slope < -0.5 else ("worsening" if slope > 0.5 else "stable")
        else:
            trend = "insufficient_data"

        # Worst hours
        from collections import defaultdict
        hour_scores = defaultdict(list)
        for h, s in zip(hours, adj_s):
            hour_scores[h].append(s)
        avg_by_hour = {h: np.mean(v) for h, v in hour_scores.items()}
        worst_hour  = max(avg_by_hour, key=avg_by_hour.get) if avg_by_hour else None
        best_hour   = min(avg_by_hour, key=avg_by_hour.get) if avg_by_hour else None

        # Task performance correlation
        task_stress = defaultdict(list)
        for t, s in zip(tasks, adj_s):
            if t:
                task_stress[t].append(s)
        avg_stress_by_task = {t: round(np.mean(v), 1) for t, v in task_stress.items()}

        return {
            "sessions":            len(rows),
            "avg_stress":          round(float(np.mean(adj_s)), 1),
            "max_stress":          round(float(np.max(adj_s)),  1),
            "min_stress":          round(float(np.min(adj_s)),  1),
            "trend":               trend,
            "worst_hour":          worst_hour,
            "best_hour":           best_hour,
            "avg_stress_by_task":  avg_stress_by_task,
            "avg_outcome_rating":  round(float(np.mean(ratings)), 2) if ratings else None,
            "baseline":            round(self.params.get("stress_baseline", 35.0), 1),
            "peak_performance":    round(self.params.get("peak_performance_stress", 38.0), 1),
            "personalization_pct": round(self._personalization_weight() * 100),
            "days_analyzed":       days,
        }

    # ─── SAVE ─────────────────────────────────────────────────────────────────
    def save(self):
        self._save_params()
        self._save_ml_model()

    # ─── SUMMARY ──────────────────────────────────────────────────────────────
    def get_profile_summary(self) -> Dict:
        return {
            "user_id":            self.user_id,
            "sessions":           self.session_count,
            "personalization_pct": round(self._personalization_weight() * 100),
            "baseline":           round(self.params.get("stress_baseline", 35.0), 1),
            "peak_performance":   round(self.params.get("peak_performance_stress", 38.0), 1),
            "face_reliability":   round(self.params.get("face_reliability", 0.55), 2),
            "voice_reliability":  round(self.params.get("voice_reliability", 0.55), 2),
            "recovery_rate":      round(self.params.get("recovery_rate", 0.12), 3),
        }


# ─── MODULE-LEVEL PROFILE CACHE ──────────────────────────────────────────────
_profile_cache: Dict[str, UserStressProfile] = {}

def get_user_profile(user_id: str) -> UserStressProfile:
    """Returns a cached UserStressProfile, loading from disk if necessary."""
    if user_id not in _profile_cache:
        _profile_cache[user_id] = UserStressProfile(user_id)
    return _profile_cache[user_id]


# ─── ENTRY POINT (self-test) ──────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  MindFlow — User Personalization Model Self-Test")
    print("=" * 60)

    import shutil
    test_dir = os.path.join(_PROFILE_DIR, "test_user_selftest")
    os.makedirs(test_dir, exist_ok=True)

    profile = UserStressProfile("test_user_selftest")
    print(f"\n[1] Fresh profile summary: {profile.get_profile_summary()}")

    # Simulate 20 sessions
    print("\n[2] Simulating 20 sessions...")
    rng = np.random.RandomState(7)
    for i in range(20):
        raw = float(rng.normal(42, 12))   # user has slightly higher baseline
        ctx = {
            "hour":                 8 + (i % 12),
            "day_of_week":          i % 5,
            "face_stress":          raw + rng.normal(0, 5),
            "voice_stress":         raw + rng.normal(0, 7),
            "face_conf":            0.7,
            "voice_conf":           0.6,
            "task_type":            ["analytical","creative","routine","meeting"][i % 4],
            "task_priority":        ["high","medium","low"][i % 3],
            "session_duration_min": rng.uniform(15, 60),
        }
        result = profile.adjust_stress_score(raw, ctx)
        profile.record_session(raw, result["adjusted_stress"], ctx,
                                outcome={"tasks_completed": rng.randint(1, 5),
                                         "outcome_rating":  rng.uniform(0.4, 0.9)})

    print(f"\n[3] Profile after 20 sessions: {profile.get_profile_summary()}")
    print(f"\n[4] Stress trends (7d): {profile.get_stress_trends(7)}")

    # Test adjustment
    test_ctx = {"hour": 9, "day_of_week": 1, "task_type": "analytical",
                "task_priority": "high", "face_stress": 55, "voice_stress": 50,
                "face_conf": 0.7, "voice_conf": 0.6}
    adj = profile.adjust_stress_score(52, test_ctx)
    print(f"\n[5] Adjusted score for raw=52: {adj}")

    # Cleanup
    shutil.rmtree(os.path.join(_PROFILE_DIR, "test_user_selftest"), ignore_errors=True)
    print("\n[DONE] Self-test complete.")