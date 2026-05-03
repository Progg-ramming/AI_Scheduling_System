# user_profile_model.py — MindFlow Personalization Engine v3 (PostgreSQL Edition)
# Location: models/user_profile_model.py

import os
import time
import json
import pickle
import numpy as np
from datetime import datetime
from typing import Optional, Dict, List
from dotenv import load_dotenv

try:
    import psycopg2
    import psycopg2.extras
    HAS_PG = True
except ImportError:
    HAS_PG = False
    print("[WARN] psycopg2 not found. PostgreSQL storage disabled. Run: pip install psycopg2-binary")

# ─── PATHS & CONFIG ──────────────────────────────────────────────────────────
_MODULE_DIR  = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR    = os.path.abspath(os.path.join(_MODULE_DIR, ".."))

# Load database credentials
load_dotenv(os.path.join(_ROOT_DIR, ".env"))

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

# ─── DB HELPERS ──────────────────────────────────────────────────────────────
def get_db_connection():
    if not HAS_PG: return None
    try:
        return psycopg2.connect(
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD", "").strip('"'),
            host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT")
        )
    except Exception as e:
        print(f"[DB] Connection error: {e}")
        return None

# ─── CLASS DEFINITION ────────────────────────────────────────────────────────
class UserStressProfile:
    def __init__(self, user_id: str):
        self.user_id = user_id
        
        # Default state
        self.weights = self._default_weights()
        self.ml_model = None
        
        # Attempt to load from PostgreSQL
        self._load_from_pg()
        
        self.scan_count = self.weights.get("scan_count", 0)

    # ─── DEFAULT STATE ────────────────────────────────────────────────────
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

    # ─── POSTGRES STORAGE ─────────────────────────────────────────────────
    def _load_from_pg(self):
        conn = get_db_connection()
        if not conn: return
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("SELECT weights, ml_model FROM user_personalization WHERE user_email = %s", (self.user_id,))
                row = cur.fetchone()
                if row:
                    self.weights = row['weights']
                    if row['ml_model']:
                        try:
                            self.ml_model = pickle.loads(row['ml_model'])
                        except:
                            print(f"[Profile:{self.user_id}] Model unpickle error")
            conn.close()
        except Exception as e:
            print(f"[Profile:{self.user_id}] PG load error: {e}")

    def _save_to_pg(self):
        conn = get_db_connection()
        if not conn: return
        try:
            model_blob = pickle.dumps(self.ml_model) if self.ml_model is not None else None
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO user_personalization (user_email, weights, ml_model, last_updated)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_email) DO UPDATE SET
                        weights = EXCLUDED.weights,
                        ml_model = EXCLUDED.ml_model,
                        last_updated = EXCLUDED.last_updated
                """, (self.user_id, json.dumps(self.weights), psycopg2.Binary(model_blob) if model_blob else None))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Profile:{self.user_id}] PG save error: {e}")

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

    # ─── ONLINE SGD UPDATE ───────────────────────────────────────────────
    def _sgd_update(self, raw: float, adjusted: float, context: Dict):
        try:
            from sklearn.linear_model import SGDRegressor
            from sklearn.preprocessing import StandardScaler

            feat = self._features(raw, context).reshape(1, -1)
            
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
                m["sgd"].partial_fit(m["scaler"].transform(feat), np.array([adjusted]))
                m["n"] += 1

            self._save_to_pg()
        except Exception as e:
            print(f"[Profile:{self.user_id}] SGD error: {e}")

    # ─── RECORD SCAN ──────────────────────────────────────────────────────
    def record_scan(self, raw_stress: float, context: Dict,
                    outcome_rating: float = -1.0) -> float:
        now = time.time()
        dt  = datetime.fromtimestamp(now)
        hour = int(context.get("hour", dt.hour))
        dow  = int(context.get("day_of_week", dt.weekday()))

        adjusted = self._compute_adjusted(raw_stress, context)
        self._update_weights(raw_stress, adjusted, hour, context, outcome_rating, now)
        self._sgd_update(raw_stress, adjusted, context)

        # Write to PostgreSQL
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO user_scan_stats (
                            user_email, timestamp, hour, day_of_week,
                            raw_stress, adjusted_stress,
                            face_stress, voice_stress, face_conf, voice_conf,
                            task_type, task_priority, outcome_rating
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        self.user_id, now, hour, dow,
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
                print(f"[Profile:{self.user_id}] Stat write error: {e}")

        return adjusted

    # ─── WEIGHT UPDATE ────────────────────────────────────────────────────
    def _update_weights(self, raw, adjusted, hour, context, outcome, ts):
        w  = self.weights
        n  = w["scan_count"] + 1
        lr = max(0.01, 1.0 / (n + 5))
        w["scan_count"] = n
        self.scan_count = n

        # Welford online mean/variance
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

        # Task sensitivity
        task = str(context.get("task_type", "unknown")).lower()
        if task in w["task_sensitivity"]:
            ratio = raw / max(w["baseline"], 1)
            w["task_sensitivity"][task] = (w["task_sensitivity"][task] * 0.95 +
                                            ratio * 0.05)

        if outcome > 0.7:
            pool = w["good_outcome_stress"]
            pool.append(adjusted)
            if len(pool) > 50: pool.pop(0)
            w["good_outcome_stress"] = pool
            if len(pool) >= 3:
                w["peak_perf_stress"] = float(np.mean(pool[-20:]))

        self._save_to_pg()

    # ─── COMPUTE ADJUSTED SCORE ───────────────────────────────────────────
    def _compute_adjusted(self, raw: float, context: Dict) -> float:
        pw   = self._pw()
        w    = self.weights
        hour = int(context.get("hour", datetime.now().hour))
        task = str(context.get("task_type", "unknown")).lower()

        shift    = (w["baseline"] - PRIOR["baseline"]) * pw * 0.5
        adjusted = raw + shift

        pop_time = (PRIOR["morning_bias"]   if 6  <= hour <= 11 else
                    PRIOR["afternoon_bias"] if 12 <= hour <= 17 else
                    PRIOR["evening_bias"]   if 18 <= hour <= 23 else 0.0)
        user_time = w["hour_bias"][hour]
        time_corr = pop_time * (1 - pw) + user_time * pw
        adjusted -= time_corr * 0.6

        if task in w["task_sensitivity"] and pw > 0.3:
            sens = w["task_sensitivity"][task]
            adjusted = adjusted * (0.85 + sens * 0.15)

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

    def adjust_stress_score(self, raw: float, context: Optional[Dict] = None) -> Dict:
        if context is None: context = {}
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

    def _insights(self, raw, adj, ctx, zone, pw) -> List[str]:
        w, out = self.weights, []
        hour = int(ctx.get("hour", datetime.now().hour))
        delta = adj - w["baseline"]
        if pw >= 0.3:
            if abs(delta) < 5:
                out.append(f"You're at your personal baseline ({w['baseline']:.0f}) — typical for you.")
            elif delta > 15:
                out.append(f"{delta:.0f} pts above your normal — unusually elevated for you.")
            hb = w["hour_bias"][hour]
            if abs(hb) > 4:
                out.append(f"You usually run {abs(hb):.0f} pts {'higher' if hb > 0 else 'lower'} at this hour.")
            if zone == "optimal":
                out.append(f"In your performance sweet spot ({w['peak_perf_stress']:.0f} ± 10).")
        else:
            out.append(f"Building your profile. {max(0, WARMUP_SCANS - self.scan_count)} more scans to unlock personalization.")
        return out[:3]

    def _pw(self) -> float:
        n = self.scan_count
        if n < WARMUP_SCANS:    return n / WARMUP_SCANS * 0.3
        if n >= FULL_PERS_SCANS: return 1.0
        return 0.3 + (n - WARMUP_SCANS) / (FULL_PERS_SCANS - WARMUP_SCANS) * 0.7

    def get_trends(self, days: int = 7) -> Dict:
        since = time.time() - days * 86400
        rows  = []
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT timestamp, hour, raw_stress, adjusted_stress, task_type, outcome_rating
                        FROM user_scan_stats 
                        WHERE user_email = %s AND timestamp >= %s 
                        ORDER BY timestamp ASC
                    """, (self.user_id, since))
                    rows = cur.fetchall()
                conn.close()
            except: pass

        w = self.weights
        if rows:
            adj_s = [r[3] for r in rows]
            hours = [r[1] for r in rows]
            from collections import defaultdict
            hm = defaultdict(list)
            for h, s in zip(hours, adj_s): hm[h].append(s)
            avg_h = {h: round(np.mean(v), 1) for h, v in hm.items()}
            return {
                "source":             "database",
                "scans_in_period":    len(rows),
                "total_scans":        self.scan_count,
                "days_analyzed":      days,
                "avg_stress":         round(float(np.mean(adj_s)), 1),
                "max_stress":         round(float(np.max(adj_s)), 1),
                "min_stress":         round(float(np.min(adj_s)), 1),
                "trend":              "stable",
                "worst_hour":         max(avg_h, key=avg_h.get) if avg_h else None,
                "best_hour":          min(avg_h, key=avg_h.get) if avg_h else None,
                "avg_by_hour":        avg_h,
                "baseline":           round(w["baseline"], 1),
                "peak_perf_stress":   round(w["peak_perf_stress"], 1),
                "personalization_pct":round(self._pw() * 100),
            }
        return {"source": "weights_only", "total_scans": self.scan_count, "baseline": round(w["baseline"], 1)}

    def get_profile_summary(self) -> Dict:
        w, pw = self.weights, self._pw()
        return {
            "user_id":             self.user_id,
            "scan_count":          self.scan_count,
            "personalization_pct": round(pw * 100),
            "baseline":            round(w["baseline"], 1),
            "stress_std":          round(w["stress_var"] ** 0.5, 1),
            "peak_perf_stress":    round(w["peak_perf_stress"], 1),
            "model_status":        "personalized" if pw > 0.5 else "warming_up",
            "hour_bias":           w["hour_bias"],
            "task_sensitivity":    w["task_sensitivity"],
            "face_reliability":    w["face_reliability"],
            "voice_reliability":   w["voice_reliability"],
            "recovery_rate":       w["recovery_rate"],
        }

# ─── CACHE ────────────────────────────────────────────────────────────────────
_cache: Dict[str, UserStressProfile] = {}

def get_user_profile(user_id: str) -> UserStressProfile:
    if user_id not in _cache:
        _cache[user_id] = UserStressProfile(user_id)
    return _cache[user_id]


if __name__ == "__main__":
    # Small sanity check to see if DB connection works
    conn = get_db_connection()
    if conn:
        print("[OK] PostgreSQL connection successful")
        conn.close()
    else:
        print("[FAIL] PostgreSQL connection failed")
