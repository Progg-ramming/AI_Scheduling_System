# bridge_v3.py — MindFlow AI Bridge (Personalization Edition)
# Location: realtime/bridge.py  (REPLACE v2 with this)
# Run: python bridge.py
#
# WHAT CHANGED vs bridge_v2.py:
#   v2: face regressor + voice regressor → confidence-weighted fusion
#   v3: SAME + UserStressProfile personalization layer
#
#   NEW ROUTES:
#     GET  /user/<user_id>/profile   → profile summary + personalization %
#     GET  /user/<user_id>/trends    → 7/14/30d stress trends + patterns
#     POST /user/<user_id>/outcome   → record task completion outcome (feedback loop)
#
#   NEW BEHAVIOR in /detect-combined:
#     - Accepts ?user_id=xxx query param
#     - Raw fused score → UserStressProfile.adjust_stress_score() → personalized score
#     - Response now includes: adjusted_stress, baseline_delta, performance_zone,
#       personalization_pct, insights

import os
import sys
import time
import traceback

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR     = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR   = os.path.join(ROOT_DIR, "models")
REALTIME_DIR = os.path.join(ROOT_DIR, "realtime")

FACE_H5          = os.path.join(MODELS_DIR, "face_emotion_model.h5")
VOICE_H5         = os.path.join(MODELS_DIR, "voice_emotion_model.h5")
CASCADE_XML      = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")
FACE_STRESS_PKL  = os.path.join(REALTIME_DIR, "face_stress_model.pkl")
VOICE_STRESS_PKL = os.path.join(MODELS_DIR, "voice_stress_v2_model.pkl")

print("=" * 60)
print("  MindFlow AI Bridge v3 — Personalization Edition")
print("=" * 60)

try:
    import librosa
    import numpy as np
    import cv2
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError as e:
    print(f"  [CRITICAL] Missing dependency: {e}")
    sys.exit(1)

if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"

# ─── LOAD REGRESSION MODELS (unchanged from v2) ──────────────────────────────
import importlib.util

def _load_module(name, path):
    if not os.path.exists(path):
        print(f"  [WARN] {name} not found at {path}")
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"[OK] {name} loaded")
    return mod

_face_stress_mod  = _load_module("face_detection",  os.path.join(REALTIME_DIR, "face_detection.py"))
_voice_stress_mod = _load_module("voice_stress_v2",  os.path.join(MODELS_DIR, "voice_stress_v2.py"))
_profile_mod      = _load_module("user_profile_model", os.path.join(MODELS_DIR, "user_profile_model.py"))

extract_face_stress_features  = getattr(_face_stress_mod,  "extract_face_stress_features",  None)
predict_face_stress           = getattr(_face_stress_mod,  "predict_face_stress",           None)
extract_voice_stress_features = getattr(_voice_stress_mod, "extract_voice_stress_features", None)
predict_voice_stress          = getattr(_voice_stress_mod, "predict_voice_stress",          None)
get_user_profile              = getattr(_profile_mod,       "get_user_profile",              None)

app = Flask(__name__)
CORS(app)

# ─── LOAD EMOTION MODELS (display labels only) ───────────────────────────────
face_emotion_model  = None
voice_emotion_model = None
face_cascade        = None

def _load_h5(path):
    if not os.path.exists(path):
        return None
    for loader_fn in _h5_loaders():
        try:
            return loader_fn(path)
        except Exception:
            pass
    return None

def _h5_loaders():
    loaders = []
    try:
        import tensorflow as tf
        loaders.append(lambda p: tf.keras.models.load_model(p, compile=False))
    except ImportError:
        pass
    try:
        import keras
        loaders.append(lambda p: keras.models.load_model(p, compile=False))
    except ImportError:
        pass
    return loaders

face_emotion_model  = _load_h5(FACE_H5)
voice_emotion_model = _load_h5(VOICE_H5)

try:
    face_cascade = cv2.CascadeClassifier(CASCADE_XML)
    if face_cascade.empty():
        raise ValueError("empty")
    print("[OK] Face cascade loaded")
except Exception as e:
    print("[ERROR] Face cascade:", e)

# ─── LABELS ──────────────────────────────────────────────────────────────────
FACE_EMOTION_LABELS = ["Angry","Disgust","Fear","Happy","Sad","Surprise","Neutral"]
EMOTION_TO_STRESS_BASE = {
    "Angry":90,"Disgust":70,"Fear":78,
    "Happy":10,"Sad":60,"Surprise":40,"Neutral":35
}

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def _fuse_stress_scores(face_score, face_conf, voice_score, voice_conf):
    total = face_conf + voice_conf
    if total < 1e-6:
        return 35.0
    return round(float(np.clip(
        (face_conf / total) * face_score + (voice_conf / total) * voice_score,
        0, 100
    )), 1)

def _stress_label(score):
    if score <= 20:   return "Very Relaxed"
    elif score <= 40: return "Relaxed"
    elif score <= 55: return "Mild Stress"
    elif score <= 70: return "Moderate Stress"
    elif score <= 85: return "High Stress"
    else:             return "Very High Stress"

def _task_schedule_advice(stress: float):
    if stress <= 20:   return "Peak state — go deep", "You're calm and energised. Start with your most demanding tasks."
    elif stress <= 40: return "Steady rhythm today", "You're balanced. Work methodically and take breaks every 90 minutes."
    elif stress <= 55: return "Be kind to yourself today", "Mild stress detected. Start small, avoid multitasking."
    elif stress <= 70: return "Take it one step at a time", "Moderate stress. Focus on 1–2 tasks only. A short walk will help."
    elif stress <= 85: return "Prioritise rest", "High stress. Do only essential tasks and take breaks every 30 min."
    else:              return "Rest is productive too", "Very high stress. Do only what's necessary today."


# ─── HEALTH ──────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":               "ok",
        "face_emotion_model":   face_emotion_model is not None,
        "voice_emotion_model":  voice_emotion_model is not None,
        "face_cascade":         face_cascade is not None,
        "face_stress_model":    os.path.exists(FACE_STRESS_PKL),
        "voice_stress_model":   os.path.exists(VOICE_STRESS_PKL),
        "personalization_mod":  _profile_mod is not None,
        "mode":                 "personalized_regression_v3"
    })


# ─── COMBINED DETECTION ──────────────────────────────────────────────────────
@app.route('/detect-combined', methods=['POST'])
def detect_combined():
    """
    Accepts:
      - frame (file)           : video frame
      - audio (file)           : audio chunk
      - user_id (form field)   : for personalization (optional, default "anonymous")
      - task_type (form field) : "creative"|"analytical"|"routine"|"meeting"
      - task_priority (form)   : "high"|"medium"|"low"
    """
    user_id       = request.form.get("user_id", "anonymous")
    task_type     = request.form.get("task_type", "unknown")
    task_priority = request.form.get("task_priority", "medium")

    face_emotion = "Neutral";  face_stress = 35.0;  face_conf = 0.0
    voice_emotion = "neutral"; voice_stress = 35.0; voice_conf = 0.0
    face_source = "fallback";  voice_source = "fallback"

    # ── FACE PIPELINE ─────────────────────────────────────────────────────────
    if 'frame' in request.files:
        try:
            frame_bytes = request.files['frame'].read()
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20,20)) \
                        if face_cascade is not None else []
                if len(faces) == 0:
                    faces = face_cascade.detectMultiScale(
                        cv2.equalizeHist(gray), 1.1, 3, minSize=(20,20)
                    ) if face_cascade is not None else []

                face_conf_cnn = 0.3
                if len(faces) > 0 and face_emotion_model is not None:
                    x, y_c, w, h = faces[0]
                    roi = cv2.resize(gray[y_c:y_c+h, x:x+w], (48,48)) / 255.0
                    roi = roi.reshape(1, 48, 48, 1)
                    pred          = face_emotion_model.predict(roi, verbose=0)
                    face_emotion  = FACE_EMOTION_LABELS[np.argmax(pred)]
                    face_conf_cnn = float(np.max(pred))

                base       = EMOTION_TO_STRESS_BASE.get(face_emotion, 35)
                face_stress = round(base * face_conf_cnn + 35 * (1 - face_conf_cnn), 1)
                face_conf   = face_conf_cnn * (0.9 if len(faces) > 0 else 0.4)
                face_source = "cnn_emotion"
        except Exception as e:
            print("[Face error]", e); traceback.print_exc()

    # ── VOICE PIPELINE ────────────────────────────────────────────────────────
    if 'audio' in request.files:
        try:
            tmp_raw = os.path.join(BASE_DIR, "tmp_voice_raw.webm")
            tmp_wav = os.path.join(BASE_DIR, "tmp_voice.wav")
            with open(tmp_raw, 'wb') as f:
                f.write(request.files['audio'].read())

            converted = False
            try:
                from pydub import AudioSegment
                AudioSegment.from_file(tmp_raw).export(tmp_wav, format="wav")
                converted = True
            except Exception:
                tmp_wav = tmp_raw

            if extract_voice_stress_features is not None:
                feats = extract_voice_stress_features(tmp_wav)
                if feats is not None:
                    voice_stress  = float(np.clip(predict_voice_stress(feats), 0, 100))
                    voice_conf    = float(np.clip(float(feats[9]), 0.2, 1.0))
                    voice_emotion = _stress_label(voice_stress).lower()
                    voice_source  = "prosodic_regressor"
        except Exception as e:
            print("[Voice error]", e); traceback.print_exc()

    # ── RAW FUSION ────────────────────────────────────────────────────────────
    raw_fused = _fuse_stress_scores(face_stress, face_conf, voice_stress, voice_conf)

    # ── PERSONALIZATION ───────────────────────────────────────────────────────
    personalization = {}
    adjusted_stress = raw_fused   # fallback if module missing

    if get_user_profile is not None:
        try:
            now = __import__("datetime").datetime.now()
            context = {
                "hour":                 now.hour,
                "day_of_week":          now.weekday(),
                "face_stress":          face_stress,
                "voice_stress":         voice_stress,
                "face_conf":            face_conf,
                "voice_conf":           voice_conf,
                "task_type":            task_type,
                "task_priority":        task_priority,
                "session_duration_min": 5.0,
            }
            profile = get_user_profile(user_id)
            result  = profile.adjust_stress_score(raw_fused, context)

            adjusted_stress  = result["adjusted_stress"]
            personalization  = {
                "baseline":            result["baseline"],
                "baseline_delta":      result["baseline_delta"],
                "performance_zone":    result["performance_zone"],
                "peak_stress":         result["peak_stress"],
                "time_adjustment":     result["time_adjustment"],
                "personalization_pct": result["personalization_pct"],
                "insights":            result["insights"],
            }

            # Auto-record this session snapshot (outcome recorded later via /outcome)
            profile.record_session(raw_fused, adjusted_stress, context)

        except Exception as e:
            print("[Personalization error]", e); traceback.print_exc()

    label = _stress_label(adjusted_stress)
    print(f"[v3] user={user_id} raw={raw_fused:.1f} → adjusted={adjusted_stress:.1f} [{label}]")

    return jsonify({
        # Core stress scores
        "raw_stress":       round(raw_fused, 1),
        "adjusted_stress":  round(adjusted_stress, 1),
        "combined_stress":  round(adjusted_stress, 1),   # alias for frontend compat

        # Component scores
        "face_stress":      round(face_stress, 1),
        "voice_stress":     round(voice_stress, 1),

        # Labels
        "face_emotion":     face_emotion,
        "voice_emotion":    voice_emotion.capitalize(),
        "stress_label":     label,

        # Weights
        "face_weight":   round(face_conf  / (face_conf + voice_conf + 1e-8), 2),
        "voice_weight":  round(voice_conf / (face_conf + voice_conf + 1e-8), 2),

        # Personalization block
        "personalization":  personalization,

        # Debug
        "face_source":   face_source,
        "voice_source":  voice_source,
        "source":        "personalized_regression_v3",
    })


# ─── RECORD OUTCOME (feedback loop) ──────────────────────────────────────────
@app.route('/user/<user_id>/outcome', methods=['POST'])
def record_outcome(user_id):
    """
    Call this when a work session ends to close the feedback loop.
    Body (JSON):
      {
        "tasks_completed": 3,
        "tasks_deferred":  1,
        "outcome_rating":  0.8,   // 0–1: how productive was this session?
        "stress_at_end":   42.0,  // optional final stress reading
        "notes":           "..."
      }
    This is what makes the model improve — the more outcomes you record,
    the faster it learns your personal stress-performance relationship.
    """
    if get_user_profile is None:
        return jsonify({"error": "personalization module not loaded"}), 503

    data = request.get_json() or {}
    try:
        profile = get_user_profile(user_id)
        outcome = {
            "tasks_completed": int(data.get("tasks_completed", 0)),
            "tasks_deferred":  int(data.get("tasks_deferred", 0)),
            "outcome_rating":  float(data.get("outcome_rating", -1)),
            "notes":           str(data.get("notes", "")),
        }
        # Record a lightweight session update with outcome info
        stress_at_end = float(data.get("stress_at_end", -1))
        if stress_at_end >= 0:
            ctx = {
                "hour":         __import__("datetime").datetime.now().hour,
                "day_of_week":  __import__("datetime").datetime.now().weekday(),
                "task_type":    data.get("task_type", "unknown"),
                "task_priority":data.get("task_priority", "medium"),
            }
            adj = profile.adjust_stress_score(stress_at_end, ctx)
            profile.record_session(stress_at_end, adj["adjusted_stress"], ctx, outcome)
        else:
            # Just save outcome data without a new stress reading
            profile._save_params()

        return jsonify({
            "status":  "recorded",
            "profile": profile.get_profile_summary(),
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── PROFILE SUMMARY ─────────────────────────────────────────────────────────
@app.route('/user/<user_id>/profile', methods=['GET'])
def get_profile(user_id):
    """Returns current personalization stats for a user."""
    if get_user_profile is None:
        return jsonify({"error": "personalization module not loaded"}), 503
    profile = get_user_profile(user_id)
    return jsonify(profile.get_profile_summary())


# ─── STRESS TRENDS ────────────────────────────────────────────────────────────
@app.route('/user/<user_id>/trends', methods=['GET'])
def get_trends(user_id):
    """
    Returns stress trend analysis.
    Query params:
      ?days=7   (default 7, supports 7/14/30)
    """
    if get_user_profile is None:
        return jsonify({"error": "personalization module not loaded"}), 503
    days    = int(request.args.get("days", 7))
    profile = get_user_profile(user_id)
    return jsonify(profile.get_stress_trends(days))


# ─── TASK SCHEDULING (stress-aware + personalized) ────────────────────────────
@app.route('/predict', methods=['POST'])
def predict():
    data         = request.get_json() or {}
    user_id      = data.get("user_id", "anonymous")
    stress_level = data.get("stress_level", 40)
    tasks        = data.get("tasks", [])

    # Use adjusted stress if personalization available
    if get_user_profile is not None:
        try:
            profile = get_user_profile(user_id)
            ctx     = {"hour": __import__("datetime").datetime.now().hour,
                       "task_priority": "medium"}
            adj     = profile.adjust_stress_score(stress_level, ctx)
            stress_level = adj["adjusted_stress"]
        except Exception:
            pass

    is_stressed  = stress_level >= 50
    priority_w   = {'high': 2, 'medium': 1, 'low': 0}
    sorted_tasks = sorted(tasks,
                          key=lambda t: priority_w.get(t.get('priority','medium'), 1),
                          reverse=not is_stressed)
    deferred     = [t['id'] for t in tasks
                    if is_stressed and t.get('priority') == 'high']

    title, message = _task_schedule_advice(stress_level)

    return jsonify({
        "title":          title,
        "message":        message,
        "order":          [t['id'] for t in sorted_tasks],
        "defer":          deferred,
        "stress_used":    round(stress_level, 1),
        "model":          "bridge_v3_personalized"
    })


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n  GET  /health                    → model status")
    print("  POST /detect-combined           → personalized face + voice stress")
    print("  POST /predict                   → personalized task scheduling")
    print("  GET  /user/<id>/profile         → personalization summary")
    print("  GET  /user/<id>/trends?days=7   → stress history + patterns")
    print("  POST /user/<id>/outcome         → record session outcome (feedback)")
    print("\n  Running on http://localhost:5000\n")
    app.run(port=5000, debug=False)