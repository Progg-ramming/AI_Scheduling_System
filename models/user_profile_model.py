# user_profile_model.py — MindFlow Personalization Engine v2
# Location: models/user_profile_model.py
#
# KEY DESIGN PRINCIPLES:
#
#   1. SCAN-DELETION SAFE:
#      The model stores ONLY mathematical weights (floats) in model.pkl.
#      Raw scan data (video/audio) is NEVER stored inside the model.
#      Session rows in SQLite store only derived stats (stress score, hour, task type).
#      When those rows are deleted after retention period → model is unaffected.
#      The learning is already baked into the weights before deletion happens.
#
#   2. ONLINE LEARNING (trains after every single scan):
#      Uses SGDRegressor with partial_fit() — updates weights incrementally.
#      No retraining from scratch. No batch accumulation needed.
#      Each scan → immediate weight update → model improves instantly.
#
#   3. WEIGHT PERSISTENCE:
#      Weights are written to .pkl after every single scan update.
#      SQLite DB stores only: timestamp, hour, stress score, task type — no raw data.
#      Even if the entire SQLite DB is wiped, the .pkl weights survive intact.
#
# WHAT THE MODEL LEARNS (all stored in weights, not raw rows):
#   - Personal stress baseline (your "normal" resting level)
#   - Time-of-day stress patterns per hour (morning cortisol etc.)
#   - Which modality is more reliable for you (face vs voice)
#   - Your performance-stress sweet spot (Yerkes-Dodson curve)
#   - Task-type stress sensitivity (you may spike more in meetings than coding)
#   - Stress recovery rate between sessions
#
# USED BY bridge.py:
#   from models.user_profile_model import get_user_profile
#   profile = get_user_profile("user_123")
#   result  = profile.adjust_stress_score(raw_score, context)
#   profile.record_scan(raw_score, context)   # call after every scan

import os
import time
import sqlite3
import pickle
import numpy as np
from datetime import datetime
from typing import Optional, Dict, List

# ─── PATHS ────────────────────────────────────────────────────────────────────
_MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
_PROFILE_DIR = os.path.join(_MODULE_DIR, "user_profiles")
os.makedirs(_PROFILE_DIR, exist_ok=True)

# ─── POPULATION PRIORS ────────────────────────────────────────────────────────
PRIOR = {
    "baseline":          35.0,
    "peak_perf_stress":  38.0,
    "face_reliability":   0.55,
    "voice_reliability":  0.55,
    "recovery_rate":      0.12,
    "morning_bias":       3.0,
    "afternoon_bias":    -2.0,
    "evening_bias":      -5.0,
}

WARMUP_SCANS    = 10
FULL_PERS_SCANS = 50


class UserStressProfile:
    def __init__(self, user_id: str):
        self.user_id   = user_id
        self.db_path   = os.path.join(_PROFILE_DIR, f"{user_id}.db")
        self.pkl_path  = os.path.join(_PROFILE_DIR, f"{user_id}_weights.pkl")
        self.sgd_path  = os.path.join(_PROFILE_DIR, f"{user_id}_sgd.pkl")

        self._init_db()
        self.weights    = self._load_weights()
        self.ml_model   = self._load_sgd()
        self.scan_count = self.weights.get("scan_count", 0)

    # ─── DB INIT ──────────────────────────────────────────────────────────
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS scan_stats (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       REAL,
                hour            INTEGER,
                day_of_week     INTEGER,
                raw_stress      REAL,
                adjusted_stress REAL,
                face_stress     REAL,
                voice_stress    REAL,
                face_conf       REAL,
                voice_conf      REAL,
                task_type       TEXT,
                task_priority   TEXT,
                outcome_rating  REAL DEFAULT -1
            )
        """)
        conn.commit()
        conn.close()

    # ─── WEIGHTS ──────────────────────────────────────────────────────────
    def _default_weights(self) -> Dict:
        return {
            "scan_count":          0,
            "baseline":            PRIOR["baseline"],
            "baseline_ema":        PRIOR["baseline"],
            "peak_perf_stress":    PRIOR["peak_perf_stress"],
            "face_reliability":    PRIOR["face_reliability"],
            "voice_reliability":   PRIOR["voice_reliability"],
            "recovery_rate":       PRIOR["recovery_rate"],
            "hour_bias":           [0.0] * 24,
            "task_sensitivity":    {"creative":1.0,"analytical":1.0,"routine":1.0,"meeting":1.0,"unknown":1.0},
            "stress_mean":         35.0,
            "stress_var":          144.0,
            "stress_n":            0,
            "good_outcome_stress": [],
            "last_scan_ts":        0.0,
            "last_stress":         35.0,
        }

    def _load_weights(self) -> Dict:
        if os.path.exists(self.pkl_path):
            try:
                with open(self.pkl_path, 'rb') as f:
                    w = pickle.load(f)
                for k, v in self._default_weights().items():
                    if k not in w:
                        w[k] = v
                return w
            except Exception as e:
                print(f"[Profile:{self.user_id}] Weight load error: {e}")
        return self._default_weights()

    def _save_weights(self):
        with open(self.pkl_path, 'wb') as f:
            pickle.dump(self.weights, f)

    def _load_sgd(self):
        if os.path.exists(self.sgd_path):
            try:
                with open(self.sgd_path, 'rb') as f:
                    return pickle.load(f)
            except Exception:
                pass
        return None

    def _save_sgd(self):
        if self.ml_model is not None:
            with open(self.sgd_path, 'wb') as f:
                pickle.dump(self.ml_model, f)

    # ─── FEATURE VECTOR ───────────────────────────────────────────────────
    def _features(self, raw: float, context: Dict) -> np.ndarray:
        hour = int(context.get("hour", datetime.now().hour))
        dow  = int(context.get("day_of_week", datetime.now().weekday()))
        task = str(context.get("task_type", "unknown")).lower()
        pri  = str(context.get("task_priority", "medium")).lower()
        fc   = float(context.get("face_conf", 0.5))
        vc   = float(context.get("voice_conf", 0.5))
        dur  = float(context.get("session_duration_min", 5.0))
        return np.array([
            raw / 100.0,
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
            np.clip((raw - self.weights["baseline"]) / 50.0, -1, 1),
            np.clip(dur / 60.0, 0, 2),
        ], dtype=np.float32)

    # ─── ONLINE SGD UPDATE (every scan) ───────────────────────────────────
    def _sgd_update(self, raw: float, adjusted: float, context: Dict):
        try:
            from sklearn.linear_model import SGDRegressor
            from sklearn.preprocessing import StandardScaler

            feat = self._features(raw, context).reshape(1, -1)
            y    = np.array([adjusted])

            if self.ml_model is None:
                self.ml_model = {
                    "sgd":    SGDRegressor(loss="huber", alpha=0.001,
                                           learning_rate="adaptive", eta0=0.01,
                                           max_iter=1, warm_start=True, random_state=42),
                    "scaler": StandardScaler(),
                    "fitted": False,
                    "n":      0,
                    "X_init": [],
                    "y_init": [],
                }

            m = self.ml_model
            if not m["fitted"]:
                m["X_init"].append(feat[0])
                m["y_init"].append(float(adjusted))
                if len(m["X_init"]) >= 3:
                    X = np.array(m["X_init"])
                    y_arr = np.array(m["y_init"])
                    m["scaler"].fit(X)
                    m["sgd"].partial_fit(m["scaler"].transform(X), y_arr)
                    m["fitted"] = True
                    m["n"] = len(y_arr)
                    del m["X_init"], m["y_init"]
            else:
                m["sgd"].partial_fit(m["scaler"].transform(feat), y)
                m["n"] += 1

            self._save_sgd()
        except Exception as e:
            print(f"[Profile:{self.user_id}] SGD error: {e}")

    # ─── RECORD SCAN — call after every scan ──────────────────────────────
    def record_scan(self, raw_stress: float, context: Dict,
                    outcome_rating: float = -1.0) -> float:
        """
        Call this after every scan. Does:
          1. Compute adjusted score
          2. Update all weights immediately (online)
          3. Update SGD model via partial_fit
          4. Write ONE lightweight row to SQLite (can be deleted safely later)
        Returns the adjusted stress score.
        """
        now = time.time()
        dt  = datetime.fromtimestamp(now)
        hour = int(context.get("hour", dt.hour))
        dow  = int(context.get("day_of_week", dt.weekday()))

        adjusted = self._compute_adjusted(raw_stress, context)
        self._update_weights(raw_stress, adjusted, hour, context, outcome_rating, now)
        self._sgd_update(raw_stress, adjusted, context)

        # Write stat row — safe to delete after retention period
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                INSERT INTO scan_stats (
                    timestamp, hour, day_of_week,
                    raw_stress, adjusted_stress,
                    face_stress, voice_stress, face_conf, voice_conf,
                    task_type, task_priority, outcome_rating
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                now, hour, dow,
                round(raw_stress, 2), round(adjusted, 2),
                round(float(context.get("face_stress", raw_stress)), 2),
                round(float(context.get("voice_stress", raw_stress)), 2),
                round(float(context.get("face_conf", 0.5)), 3),
                round(float(context.get("voice_conf", 0.5)), 3),
                str(context.get("task_type", "unknown")),
                str(context.get("task_priority", "medium")),
                round(float(outcome_rating), 3),
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Profile:{self.user_id}] DB write error: {e}")

        print(f"[Profile:{self.user_id}] Scan #{self.scan_count} | "
              f"raw={raw_stress:.1f} adj={adjusted:.1f} "
              f"baseline={self.weights['baseline']:.1f} "
              f"pers={self._pw()*100:.0f}%")
        return adjusted

    # ─── WEIGHT UPDATE ────────────────────────────────────────────────────
    def _update_weights(self, raw, adjusted, hour, context, outcome, ts):
        w  = self.weights
        n  = w["scan_count"] + 1
        lr = max(0.01, 1.0 / (n + 5))
        w["scan_count"] = n
        self.scan_count = n

        # Welford online mean/variance (no history needed)
        old_mean = w["stress_mean"]
        w["stress_n"] += 1
        delta = raw - old_mean
        w["stress_mean"] += delta / w["stress_n"]
        w["stress_var"]   = (w["stress_var"] * (w["stress_n"] - 1) +
                              delta * (raw - w["stress_mean"])) / w["stress_n"]

        # Baseline EMA
        alpha = min(lr * 0.3, 0.05)
        w["baseline_ema"] = w["baseline_ema"] * (1 - alpha) + raw * alpha
        w["baseline"]     = w["baseline_ema"]

        # Hour bias
        hour_lr = max(0.02, 1.0 / (n / 24 + 2))
        observed_bias = raw - w["baseline"]
        w["hour_bias"][hour] = (w["hour_bias"][hour] * (1 - hour_lr) +
                                 observed_bias * hour_lr)

        # Modality reliability
        fs = float(context.get("face_stress", raw))
        vs = float(context.get("voice_stress", raw))
        fe = abs(fs - adjusted)
        ve = abs(vs - adjusted)
        fr = 1.0 - fe / (max(fe, ve) + 30)
        vr = 1.0 - ve / (max(fe, ve) + 30)
        w["face_reliability"]  = w["face_reliability"]  * (1 - lr) + fr * lr
        w["voice_reliability"] = w["voice_reliability"] * (1 - lr) + vr * lr

        # Recovery rate
        if w["last_scan_ts"] > 0 and ts > w["last_scan_ts"]:
            dt_min = (ts - w["last_scan_ts"]) / 60.0
            if 1 <= dt_min <= 120 and raw < w["last_stress"]:
                obs_rr = (w["last_stress"] - raw) / (dt_min * 100)
                w["recovery_rate"] = w["recovery_rate"] * 0.9 + obs_rr * 0.1
        w["last_scan_ts"] = ts
        w["last_stress"]  = raw

        # Task sensitivity
        task = str(context.get("task_type", "unknown")).lower()
        if task in w["task_sensitivity"]:
            ratio = raw / max(w["baseline"], 1)
            w["task_sensitivity"][task] = (w["task_sensitivity"][task] * 0.95 +
                                            ratio * 0.05)

        # Peak performance (from positive outcome feedback)
        if outcome > 0.7:
            pool = w["good_outcome_stress"]
            pool.append(adjusted)
            if len(pool) > 50:
                pool.pop(0)
            w["good_outcome_stress"] = pool
            if len(pool) >= 3:
                w["peak_perf_stress"] = float(np.mean(pool[-20:]))

        self._save_weights()

    # ─── COMPUTE ADJUSTED SCORE ───────────────────────────────────────────
    def _compute_adjusted(self, raw: float, context: Dict) -> float:
        pw   = self._pw()
        w    = self.weights
        hour = int(context.get("hour", datetime.now().hour))
        task = str(context.get("task_type", "unknown")).lower()

        # Baseline re-centering
        shift    = (w["baseline"] - PRIOR["baseline"]) * pw * 0.5
        adjusted = raw + shift

        # Time-of-day correction
        pop_time = (PRIOR["morning_bias"]   if 6  <= hour <= 11 else
                    PRIOR["afternoon_bias"] if 12 <= hour <= 17 else
                    PRIOR["evening_bias"]   if 18 <= hour <= 23 else 0.0)
        user_time = w["hour_bias"][hour]
        time_corr = pop_time * (1 - pw) + user_time * pw
        adjusted -= time_corr * 0.6

        # Task sensitivity
        if task in w["task_sensitivity"] and pw > 0.3:
            sens = w["task_sensitivity"][task]
            adjusted = adjusted * (0.85 + sens * 0.15)

        # SGD blend
        if (self.ml_model is not None and
                self.ml_model.get("fitted") and
                self.ml_model.get("n", 0) >= 5 and pw > 0.2):
            try:
                m    = self.ml_model
                feat = self._features(raw, context).reshape(1, -1)
                pred = float(m["sgd"].predict(m["scaler"].transform(feat))[0])
                blend = min(pw * 0.5, 0.4)
                adjusted = adjusted * (1 - blend) + pred * blend
            except Exception:
                pass

        return round(float(np.clip(adjusted, 0, 100)), 1)

    # ─── PUBLIC: ADJUST STRESS SCORE ──────────────────────────────────────
    def adjust_stress_score(self, raw: float, context: Optional[Dict] = None) -> Dict:
        if context is None:
            context = {}
        pw       = self._pw()
        w        = self.weights
        adjusted = self._compute_adjusted(raw, context)
        peak     = w["peak_perf_stress"]
        zone     = ("below_optimal" if adjusted < peak - 10 else
                    "optimal"       if adjusted <= peak + 10 else
                    "above_optimal")
        hour     = int(context.get("hour", datetime.now().hour))
        pop_time = (PRIOR["morning_bias"]   if 6  <= hour <= 11 else
                    PRIOR["afternoon_bias"] if 12 <= hour <= 17 else
                    PRIOR["evening_bias"]   if 18 <= hour <= 23 else 0.0)
        time_adj = pop_time * (1 - pw) + w["hour_bias"][hour] * pw

        return {
            "adjusted_stress":     adjusted,
            "raw_stress":          round(raw, 1),
            "baseline":            round(w["baseline"], 1),
            "baseline_delta":      round(adjusted - w["baseline"], 1),
            "performance_zone":    zone,
            "peak_stress":         round(peak, 1),
            "time_adjustment":     round(time_adj, 1),
            "personalization_pct": round(pw * 100),
            "scan_count":          self.scan_count,
            "insights":            self._insights(raw, adjusted, context, zone, pw),
        }

    # ─── INSIGHTS ─────────────────────────────────────────────────────────
    def _insights(self, raw, adj, ctx, zone, pw) -> List[str]:
        w, out = self.weights, []
        hour = int(ctx.get("hour", datetime.now().hour))
        task = str(ctx.get("task_type", "unknown"))
        pri  = str(ctx.get("task_priority", "medium"))
        delta = adj - w["baseline"]

        if pw >= 0.3:
            if abs(delta) < 5:
                out.append(f"You're at your personal baseline ({w['baseline']:.0f}) — typical for you.")
            elif delta > 15:
                out.append(f"{delta:.0f} pts above your normal — unusually elevated for you.")
            elif delta < -10:
                out.append(f"Notably calmer than your usual baseline of {w['baseline']:.0f}.")
            hb = w["hour_bias"][hour]
            if abs(hb) > 4:
                out.append(f"You usually run {abs(hb):.0f} pts {'higher' if hb > 0 else 'lower'} at this hour.")
            if zone == "optimal":
                out.append(f"In your performance sweet spot ({w['peak_perf_stress']:.0f} ± 10).")
            elif zone == "above_optimal":
                out.append("Above your optimal zone — break into 25-min chunks.")
        else:
            out.append(f"Building your profile. {max(0, WARMUP_SCANS - self.scan_count)} more scans to unlock personalization.")

        if task == "creative" and adj > 60:
            out.append("Creative work needs calm — try a 5-min reset first.")
        if pri == "high" and adj > 65:
            out.append("High-priority + elevated stress: start with the easiest step.")
        return out[:3]

    def _pw(self) -> float:
        n = self.scan_count
        if n < WARMUP_SCANS:    return n / WARMUP_SCANS * 0.3
        if n >= FULL_PERS_SCANS: return 1.0
        return 0.3 + (n - WARMUP_SCANS) / (FULL_PERS_SCANS - WARMUP_SCANS) * 0.7

    # ─── TRENDS ───────────────────────────────────────────────────────────
    def get_trends(self, days: int = 7) -> Dict:
        since = time.time() - days * 86400
        rows  = []
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute("""
                SELECT timestamp, hour, raw_stress, adjusted_stress, task_type, outcome_rating
                FROM scan_stats WHERE timestamp >= ? ORDER BY timestamp ASC
            """, (since,)).fetchall()
            conn.close()
        except Exception:
            pass

        w = self.weights
        if rows:
            adj_s   = [r[3] for r in rows]
            hours   = [r[1] for r in rows]
            tasks   = [r[4] for r in rows]
            ratings = [r[5] for r in rows if r[5] >= 0]
            from collections import defaultdict
            hm = defaultdict(list)
            for h, s in zip(hours, adj_s): hm[h].append(s)
            avg_h   = {h: round(np.mean(v), 1) for h, v in hm.items()}
            tm = defaultdict(list)
            for t, s in zip(tasks, adj_s):
                if t: tm[t].append(s)
            slope = float(np.polyfit(range(len(adj_s)), adj_s, 1)[0]) if len(adj_s) >= 3 else 0
            return {
                "source":             "database",
                "scans_in_period":    len(rows),
                "total_scans":        self.scan_count,
                "avg_stress":         round(float(np.mean(adj_s)), 1),
                "max_stress":         round(float(np.max(adj_s)), 1),
                "min_stress":         round(float(np.min(adj_s)), 1),
                "trend":              ("improving" if slope < -0.5 else "worsening" if slope > 0.5 else "stable"),
                "worst_hour":         max(avg_h, key=avg_h.get) if avg_h else None,
                "best_hour":          min(avg_h, key=avg_h.get) if avg_h else None,
                "avg_by_hour":        avg_h,
                "stress_by_task":     {t: round(np.mean(v), 1) for t, v in tm.items()},
                "avg_outcome_rating": round(float(np.mean(ratings)), 2) if ratings else None,
                "days_analyzed":      days,
                "baseline":           round(w["baseline"], 1),
                "peak_perf_stress":   round(w["peak_perf_stress"], 1),
                "personalization_pct":round(self._pw() * 100),
            }

        # Rows deleted — fall back to weights (model still intact)
        return {
            "source":              "weights_only",
            "scans_in_period":     0,
            "total_scans":         self.scan_count,
            "avg_stress":          round(w["stress_mean"], 1),
            "trend":               "scan history deleted — model weights intact",
            "worst_hour":          int(np.argmax(w["hour_bias"])),
            "best_hour":           int(np.argmin(w["hour_bias"])),
            "avg_by_hour":         {i: round(w["hour_bias"][i] + w["baseline"], 1) for i in range(24)},
            "stress_by_task":      {k: round(v * w["baseline"], 1) for k, v in w["task_sensitivity"].items()},
            "baseline":            round(w["baseline"], 1),
            "peak_perf_stress":    round(w["peak_perf_stress"], 1),
            "personalization_pct": round(self._pw() * 100),
            "days_analyzed":       days,
        }

    # ─── PROFILE SUMMARY ──────────────────────────────────────────────────
    def get_profile_summary(self) -> Dict:
        w, pw = self.weights, self._pw()
        return {
            "user_id":             self.user_id,
            "scan_count":          self.scan_count,
            "personalization_pct": round(pw * 100),
            "baseline":            round(w["baseline"], 1),
            "peak_perf_stress":    round(w["peak_perf_stress"], 1),
            "face_reliability":    round(w["face_reliability"], 3),
            "voice_reliability":   round(w["voice_reliability"], 3),
            "recovery_rate":       round(w["recovery_rate"], 4),
            "hour_bias":           [round(b, 2) for b in w["hour_bias"]],
            "task_sensitivity":    {k: round(v, 3) for k, v in w["task_sensitivity"].items()},
            "stress_std":          round(float(np.sqrt(max(w["stress_var"], 0))), 1),
            "model_status": (
                "population_prior"   if pw < 0.1 else
                "warming_up"         if pw < 0.5 else
                "personalized"       if pw < 0.9 else
                "fully_personalized"
            ),
        }


# ─── CACHE ────────────────────────────────────────────────────────────────────
_cache: Dict[str, UserStressProfile] = {}

def get_user_profile(user_id: str) -> UserStressProfile:
    if user_id not in _cache:
        _cache[user_id] = UserStressProfile(user_id)
    return _cache[user_id]


# ─── SELF TEST ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import shutil
    print("=" * 55)
    print("  MindFlow — User Profile Model Self-Test")
    print("=" * 55)

    tid = "_selftest_tmp"
    for ext in ["_weights.pkl","_sgd.pkl",".db"]:
        p = os.path.join(_PROFILE_DIR, f"{tid}{ext}")
        if os.path.exists(p): os.remove(p)

    p = get_user_profile(tid)
    rng = np.random.RandomState(42)

    print("\n[1] Running 60 simulated scans (user baseline ~43)...")
    for i in range(60):
        raw = float(rng.normal(43, 11))
        ctx = {
            "hour": 7 + (i % 14), "day_of_week": i % 5,
            "face_stress": raw + rng.normal(0, 4),
            "voice_stress": raw + rng.normal(0, 6),
            "face_conf": 0.72, "voice_conf": 0.65,
            "task_type": ["analytical","creative","routine","meeting"][i % 4],
            "task_priority": ["high","medium","low"][i % 3],
            "session_duration_min": rng.uniform(10, 45),
        }
        p.record_scan(raw, ctx, outcome_rating=rng.uniform(0.5, 0.95) if i > 10 else -1)

    print(f"\n[2] Summary: baseline={p.weights['baseline']:.1f} (expect ~43), scans={p.scan_count}")
    print(f"    Personalization: {p._pw()*100:.0f}%")

    print("\n[3] Testing scan-deletion resilience...")
    conn = sqlite3.connect(p.db_path)
    conn.execute("DELETE FROM scan_stats"); conn.commit(); conn.close()
    del _cache[tid]
    p2 = get_user_profile(tid)
    t2 = p2.get_trends(7)
    print(f"    scan_count after reload + DB wipe: {p2.scan_count}  (expect 60)")
    print(f"    baseline after reload: {p2.weights['baseline']:.1f}  (expect ~43)")
    print(f"    trends source: {t2['source']}  (expect weights_only)")
    print("    PASS: model intact after data deletion" if p2.scan_count == 60
          else "    FAIL: scan count lost")

    for ext in ["_weights.pkl","_sgd.pkl",".db"]:
        try: os.remove(os.path.join(_PROFILE_DIR, f"{tid}{ext}"))
        except: pass
    print("\n[DONE]")