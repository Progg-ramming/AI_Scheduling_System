# bridge_v3.py — MindFlow AI Bridge (Personalization Edition)
# Location: realtime/bridge.py  — replace old bridge.py with this file
#
# CHANGES vs bridge_v2:
#   - Calls profile.record_scan() after every detection (online learning)
#   - Returns personalization block in /detect-combined response
#   - New routes: /user/<id>/profile, /user/<id>/trends, /user/<id>/outcome
#   - /predict uses personalized stress level, not raw

import os, sys, time, traceback
import numpy as np
import cv2
from flask import Flask, request, jsonify
from flask_cors import CORS
import importlib.util

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR     = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR   = os.path.join(ROOT_DIR, "models")
REALTIME_DIR = os.path.join(ROOT_DIR, "realtime")

if ROOT_DIR not in sys.path: sys.path.insert(0, ROOT_DIR)
os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"

# ─── LOAD MODULES ─────────────────────────────────────────────────────────────
def _mod(name, path):
    if not os.path.exists(path): print(f"  [WARN] {name} missing: {path}"); return None
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    print(f"[OK] {name}"); return m

_face_mod    = _mod("face_detection",    os.path.join(REALTIME_DIR, "face_detection.py"))
_voice_mod   = _mod("voice_stress_v2",   os.path.join(MODELS_DIR,   "voice_stress_v2.py"))
_profile_mod = _mod("user_profile_model",os.path.join(MODELS_DIR,   "user_profile_model.py"))

extract_face_features  = getattr(_face_mod,    "extract_face_stress_features",  None)
predict_face_stress    = getattr(_face_mod,    "predict_face_stress",           None)
extract_voice_features = getattr(_voice_mod,   "extract_voice_stress_features", None)
predict_voice_stress   = getattr(_voice_mod,   "predict_voice_stress",          None)
get_user_profile       = getattr(_profile_mod, "get_user_profile",              None)

app = Flask(__name__)
CORS(app)

# ─── EMOTION MODELS (display label only) ──────────────────────────────────────
FACE_H5     = os.path.join(MODELS_DIR, "face_emotion_model.h5")
VOICE_H5    = os.path.join(MODELS_DIR, "voice_emotion_model.h5")
CASCADE_XML = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")

def _load_h5(path):
    if not os.path.exists(path): return None
    for fn in [
        lambda p: __import__("tensorflow").keras.models.load_model(p, compile=False),
        lambda p: __import__("keras").models.load_model(p, compile=False),
    ]:
        try: return fn(path)
        except: pass
    return None

face_model   = _load_h5(FACE_H5)
voice_model  = _load_h5(VOICE_H5)
face_cascade = None
try:
    face_cascade = cv2.CascadeClassifier(CASCADE_XML)
    if face_cascade.empty(): raise ValueError()
    print("[OK] face_cascade")
except: print("[WARN] face_cascade missing or empty")

FACE_LABELS = ["Angry","Disgust","Fear","Happy","Sad","Surprise","Neutral"]
EMOTION_TO_STRESS = {"Angry":90,"Disgust":70,"Fear":78,"Happy":10,"Sad":60,"Surprise":40,"Neutral":35}

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def _fuse(face_s, face_c, voice_s, voice_c):
    t = face_c + voice_c
    return round(float(np.clip((face_c/t)*face_s + (voice_c/t)*voice_s, 0, 100)), 1) if t > 1e-6 else 35.0

def _label(s):
    return ("Very Relaxed" if s<=20 else "Relaxed" if s<=40 else
            "Mild Stress"  if s<=55 else "Moderate Stress" if s<=70 else
            "High Stress"  if s<=85 else "Very High Stress")

def _advice(s):
    if s<=20: return "Peak state — go deep", "Calm and energised. Start with your hardest task."
    if s<=40: return "Steady rhythm", "Balanced. Work methodically, break every 90 min."
    if s<=55: return "Be kind to yourself", "Mild stress. Start small, avoid multitasking."
    if s<=70: return "One step at a time", "Moderate stress. 1–2 tasks max. Short walk helps."
    if s<=85: return "Prioritise rest", "High stress. Essentials only, break every 30 min."
    return "Rest is productive", "Very high stress. Do only what's necessary today."

# ─── HEALTH ───────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":"ok",
        "face_model":    face_model  is not None,
        "voice_model":   voice_model is not None,
        "face_cascade":  face_cascade is not None,
        "face_stress":   os.path.exists(os.path.join(REALTIME_DIR,"face_stress_model.pkl")),
        "voice_stress":  os.path.exists(os.path.join(MODELS_DIR,"voice_stress_v2_model.pkl")),
        "personalization": _profile_mod is not None,
        "mode": "personalized_v3"
    })

# ─── DETECT COMBINED ──────────────────────────────────────────────────────────
@app.route('/detect-combined', methods=['POST'])
def detect_combined():
    """
    Form fields:
      frame         — video frame file
      audio         — audio file
      user_id       — string (default "anonymous")
      task_type     — creative | analytical | routine | meeting | unknown
      task_priority — high | medium | low
    """
    from datetime import datetime
    user_id       = request.form.get("user_id", "anonymous")
    task_type     = request.form.get("task_type", "unknown")
    task_priority = request.form.get("task_priority", "medium")

    face_emotion = "Neutral"; face_stress = 35.0; face_conf = 0.0
    voice_emotion = "neutral"; voice_stress = 35.0; voice_conf = 0.0

    # ── FACE ──────────────────────────────────────────────────────────────────
    if 'frame' in request.files:
        try:
            frame = cv2.imdecode(np.frombuffer(request.files['frame'].read(), np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                faces = (face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20,20))
                         if face_cascade else [])
                if len(faces)==0 and face_cascade:
                    faces = face_cascade.detectMultiScale(cv2.equalizeHist(gray), 1.1, 3, minSize=(20,20))
                cnn_conf = 0.3
                if len(faces) > 0 and face_model:
                    x,y,w,h = faces[0]
                    roi = cv2.resize(gray[y:y+h,x:x+w],(48,48))/255.0
                    pred = face_model.predict(roi.reshape(1,48,48,1), verbose=0)
                    face_emotion = FACE_LABELS[np.argmax(pred)]
                    cnn_conf     = float(np.max(pred))
                base       = EMOTION_TO_STRESS.get(face_emotion, 35)
                face_stress = round(base*cnn_conf + 35*(1-cnn_conf), 1)
                face_conf   = cnn_conf * (0.9 if len(faces)>0 else 0.4)
        except Exception as e:
            print("[Face]", e); traceback.print_exc()

    # ── VOICE ─────────────────────────────────────────────────────────────────
    if 'audio' in request.files:
        try:
            tmp_raw = os.path.join(BASE_DIR, "tmp_raw.webm")
            tmp_wav = os.path.join(BASE_DIR, "tmp_voice.wav")
            with open(tmp_raw,'wb') as f: f.write(request.files['audio'].read())
            try:
                from pydub import AudioSegment
                AudioSegment.from_file(tmp_raw).export(tmp_wav, format="wav")
            except: tmp_wav = tmp_raw
            if extract_voice_features:
                feats = extract_voice_features(tmp_wav)
                if feats is not None:
                    voice_stress  = float(np.clip(predict_voice_stress(feats), 0, 100))
                    voice_conf    = float(np.clip(float(feats[9]), 0.2, 1.0))
                    voice_emotion = _label(voice_stress).lower()
        except Exception as e:
            print("[Voice]", e); traceback.print_exc()

    # ── FUSE ──────────────────────────────────────────────────────────────────
    raw = _fuse(face_stress, face_conf, voice_stress, voice_conf)

    # ── PERSONALIZE + RECORD SCAN ─────────────────────────────────────────────
    personalization = {}
    adjusted = raw

    if get_user_profile:
        try:
            now = datetime.now()
            ctx = {
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
            profile  = get_user_profile(user_id)

            # get adjusted score BEFORE recording (uses current weights)
            result   = profile.adjust_stress_score(raw, ctx)
            adjusted = result["adjusted_stress"]

            # record_scan updates weights immediately — safe if DB deleted later
            profile.record_scan(raw, ctx)

            personalization = {
                "baseline":            result["baseline"],
                "baseline_delta":      result["baseline_delta"],
                "performance_zone":    result["performance_zone"],
                "peak_stress":         result["peak_stress"],
                "time_adjustment":     result["time_adjustment"],
                "personalization_pct": result["personalization_pct"],
                "scan_count":          profile.scan_count,
                "insights":            result["insights"],
            }
        except Exception as e:
            print("[Personalization]", e); traceback.print_exc()

    label = _label(adjusted)
    total = face_conf + voice_conf + 1e-8

    return jsonify({
        "raw_stress":        round(raw, 1),
        "adjusted_stress":   round(adjusted, 1),
        "combined_stress":   round(adjusted, 1),
        "face_stress":       round(face_stress, 1),
        "voice_stress":      round(voice_stress, 1),
        "face_emotion":      face_emotion,
        "voice_emotion":     voice_emotion.capitalize(),
        "stress_label":      label,
        "face_weight":       round(face_conf / total, 2),
        "voice_weight":      round(voice_conf / total, 2),
        "personalization":   personalization,
        "source":            "personalized_v3",
    })

# ─── RECORD OUTCOME (closes the feedback loop) ────────────────────────────────
@app.route('/user/<user_id>/outcome', methods=['POST'])
def record_outcome(user_id):
    """
    POST JSON: { tasks_completed, tasks_deferred, outcome_rating (0-1),
                 stress_at_end, task_type, task_priority, notes }
    Call this when a work session ends. This is the signal that teaches
    the model which stress levels give you the best productivity.
    """
    if not get_user_profile:
        return jsonify({"error": "personalization module not loaded"}), 503
    from datetime import datetime
    data = request.get_json() or {}
    try:
        profile = get_user_profile(user_id)
        stress  = float(data.get("stress_at_end", -1))
        rating  = float(data.get("outcome_rating", -1))
        if stress >= 0:
            ctx = {
                "hour":           datetime.now().hour,
                "day_of_week":    datetime.now().weekday(),
                "task_type":      data.get("task_type", "unknown"),
                "task_priority":  data.get("task_priority", "medium"),
            }
            profile.record_scan(stress, ctx, outcome_rating=rating)
        return jsonify({"status":"recorded", "profile": profile.get_profile_summary()})
    except Exception as e:
        traceback.print_exc(); return jsonify({"error": str(e)}), 500

# ─── USER PROFILE ─────────────────────────────────────────────────────────────
@app.route('/user/<user_id>/profile', methods=['GET'])
def get_profile(user_id):
    if not get_user_profile: return jsonify({"error":"module missing"}), 503
    return jsonify(get_user_profile(user_id).get_profile_summary())

# ─── TRENDS ───────────────────────────────────────────────────────────────────
@app.route('/user/<user_id>/trends', methods=['GET'])
def get_trends(user_id):
    if not get_user_profile: return jsonify({"error":"module missing"}), 503
    days = int(request.args.get("days", 7))
    return jsonify(get_user_profile(user_id).get_trends(days))

# ─── TASK SCHEDULING ──────────────────────────────────────────────────────────
@app.route('/predict', methods=['POST'])
def predict():
    from datetime import datetime
    data    = request.get_json() or {}
    uid     = data.get("user_id", "anonymous")
    stress  = data.get("stress_level", 40)
    tasks   = data.get("tasks", [])

    if get_user_profile:
        try:
            p   = get_user_profile(uid)
            ctx = {"hour": datetime.now().hour, "task_priority": "medium"}
            stress = p.adjust_stress_score(stress, ctx)["adjusted_stress"]
        except: pass

    pw = {'high':2,'medium':1,'low':0}
    sorted_tasks = sorted(tasks, key=lambda t: pw.get(t.get('priority','medium'),1),
                          reverse=stress < 50)
    title, msg = _advice(stress)
    return jsonify({
        "title":       title,
        "message":     msg,
        "order":       [t['id'] for t in sorted_tasks],
        "defer":       [t['id'] for t in tasks if stress >= 50 and t.get('priority')=='high'],
        "stress_used": round(stress, 1),
        "model":       "bridge_v3",
    })

if __name__ == '__main__':
    print("\n  GET  /health")
    print("  POST /detect-combined        (user_id, task_type, task_priority)")
    print("  POST /user/<id>/outcome      (feedback loop — improves model)")
    print("  GET  /user/<id>/profile      (personalization summary)")
    print("  GET  /user/<id>/trends       (?days=7|14|30)")
    print("  POST /predict                (personalized task scheduling)")
    print("\n  http://localhost:5000\n")
    app.run(port=5000, debug=False)