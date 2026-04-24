# bridge_v2.py — MindFlow AI Bridge (Regression Edition)
# Location: realtime/bridge.py  (REPLACE the old bridge.py with this file)
# Run: python bridge.py
#
# WHAT CHANGED vs old bridge.py:
#   OLD: face CNN → emotion label → hardcoded dict → stress score
#   NEW: face CNN still runs (emotion label kept for display)
#        + MediaPipe landmarks → face_stress.py regressor → continuous score
#
#   OLD: voice model → emotion label → hardcoded dict → stress score
#   NEW: voice_stress_v2.py → jitter/shimmer/HNR/pitch → continuous score
#
#   OLD: combined = 50% face + 50% voice (fixed)
#   NEW: combined = confidence-weighted blend (face confidence from CNN softmax,
#        voice confidence from HNR quality)

import os
import sys
import time
import traceback
import logging

# ─── STARTUP BANNER ──────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(ROOT_DIR, "models")
REALTIME_DIR = os.path.join(ROOT_DIR, "realtime")

FACE_H5          = os.path.join(MODELS_DIR, "face_emotion_model.h5")
VOICE_H5         = os.path.join(MODELS_DIR, "voice_emotion_model.h5")
CASCADE_XML      = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")
FACE_STRESS_PKL  = os.path.join(BASE_DIR, "face_stress_model.pkl")
VOICE_STRESS_PKL = os.path.join(MODELS_DIR, "voice_stress_model.pkl")

print("=" * 60)
print("  MindFlow AI Bridge v2 — Regression Edition")
print("=" * 60)
print("  Project Root    :", ROOT_DIR)
print("  Models dir      :", MODELS_DIR)
print("  Face h5         :", "FOUND" if os.path.exists(FACE_H5)          else "MISSING")
print("  Voice h5        :", "FOUND" if os.path.exists(VOICE_H5)         else "MISSING")
print("  Cascade xml     :", "FOUND" if os.path.exists(CASCADE_XML)      else "MISSING")
print("  Face stress pkl :", "FOUND" if os.path.exists(FACE_STRESS_PKL)  else "MISSING — run: python realtime/face_detection.py")
print("  Voice stress pkl:", "FOUND" if os.path.exists(VOICE_STRESS_PKL) else "MISSING — run: python models/voice_stress_v2.py")
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

# ─── LOAD NEW REGRESSION MODELS ──────────────────────────────────────────────
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

_face_stress_mod  = _load_module("face_detection",     os.path.join(REALTIME_DIR, "face_detection.py"))
_voice_stress_mod = _load_module("voice_stress_v2", os.path.join(MODELS_DIR, "voice_stress_v2.py"))

# Bind functions
if _face_stress_mod:
    extract_face_stress_features = _face_stress_mod.extract_face_stress_features
    predict_face_stress          = _face_stress_mod.predict_face_stress
else:
    extract_face_stress_features = None
    predict_face_stress          = None

if _voice_stress_mod:
    extract_voice_stress_features = _voice_stress_mod.extract_voice_stress_features
    predict_voice_stress          = _voice_stress_mod.predict_voice_stress
else:
    extract_voice_stress_features = None
    predict_voice_stress          = None

app = Flask(__name__)
CORS(app)

# ─── LOAD EMOTION MODELS (kept for display label only) ───────────────────────
face_emotion_model = None
face_cascade       = None
voice_emotion_model = None

def _load_h5(path):
    """Try multiple Keras loading strategies."""
    if not os.path.exists(path):
        print(f"  [Error] File does not exist: {path}")
        return None
    print(f"  [Info] Loading {os.path.basename(path)}...")
    strategies = []
    try:
        import tensorflow as tf
        strategies.append(("tf.keras", lambda: tf.keras.models.load_model(path, compile=False)))
    except ImportError:
        pass
    try:
        import keras
        strategies.append(("keras", lambda: keras.models.load_model(path, compile=False)))
    except ImportError:
        pass
    try:
        from tensorflow.keras.models import load_model as lm
        strategies.append(("tensorflow.keras", lambda: lm(path, compile=False)))
    except ImportError:
        pass
    for name, loader in strategies:
        try:
            model = loader()
            print(f"  [OK] Loaded via {name}: {os.path.basename(path)}")
            return model
        except Exception as e:
            print(f"  [{name} failed]: {str(e)[:70]}")
    print(f"  [ERROR] Could not load {os.path.basename(path)}")
    return None

face_emotion_model  = _load_h5(FACE_H5)
voice_emotion_model = _load_h5(VOICE_H5)

try:
    face_cascade = cv2.CascadeClassifier(CASCADE_XML)
    if face_cascade.empty():
        raise ValueError("Cascade file is empty")
    print("[OK] Face cascade loaded")
except Exception as e:
    print("[ERROR] Face cascade:", e)

# ─── MEDIAPIPE FACE MESH (shared instance for speed) ─────────────────────────
_face_mesh_instance = None
print("[INFO] MediaPipe disabled — using CNN confidence for face stress")

# ─── LABELS ──────────────────────────────────────────────────────────────────
EMOTION_TO_STRESS_BASE = {
    "Angry":90, "Disgust":70, "Fear":78,
    "Happy":10, "Sad":60, "Surprise":40, "Neutral":35
}
VOICE_EMOTION_LABELS = ["neutral","calm","happy","sad","angry","fear","disgust","surprise"]

# ─── CONFIDENCE-WEIGHTED FUSION ───────────────────────────────────────────────
def _fuse_stress_scores(face_score, face_conf, voice_score, voice_conf):
    """
    Weighted fusion of face and voice stress scores.

    face_conf  : CNN softmax max probability (0–1)
                 High = model is confident about the emotion
    voice_conf : Estimated from HNR (higher HNR = cleaner signal = more reliable)
                 Passed as 0–1 float

    If one modality fails (conf = 0), the other gets full weight.
    If both fail, returns neutral fallback of 35.
    """
    total_conf = face_conf + voice_conf
    if total_conf < 1e-6:
        return 35.0  # both failed — neutral fallback

    w_face  = face_conf  / total_conf
    w_voice = voice_conf / total_conf

    combined = (w_face * face_score) + (w_voice * voice_score)
    return round(float(np.clip(combined, 0, 100)), 1)


# ─── STRESS LABEL ─────────────────────────────────────────────────────────────
def _stress_label(score):
    if score <= 20:   return "Very Relaxed"
    elif score <= 40: return "Relaxed"
    elif score <= 55: return "Mild Stress"
    elif score <= 70: return "Moderate Stress"
    elif score <= 85: return "High Stress"
    else:             return "Very High Stress"


# ─── HEALTH ──────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":              "ok",
        "face_emotion_model":  face_emotion_model is not None,
        "voice_emotion_model": voice_emotion_model is not None,
        "face_cascade":        face_cascade is not None,
        "face_stress_model":   os.path.exists(FACE_STRESS_PKL),
        "voice_stress_model":  os.path.exists(VOICE_STRESS_PKL),
        "mediapipe":           _face_mesh_instance is not None,
        "mode":                "regression"
    })


# ─── COMBINED DETECTION ──────────────────────────────────────────────────────
@app.route('/detect-combined', methods=['POST'])
def detect_combined():

    # ── Defaults (used if a modality fails) ───────────────────────────────────
    face_emotion  = "Neutral"
    face_stress   = 35.0
    face_conf     = 0.0     # no confidence → won't bias combined score
    face_source   = "fallback"

    voice_emotion = "neutral"
    voice_stress  = 35.0
    voice_conf    = 0.0
    voice_source  = "fallback"

    landmark_features = None

    # ─────────────────────────────────────────────────────────────────────────
    # FACE PIPELINE
    # Step 1: Detect face with Haar cascade
    # Step 2: Emotion label with CNN (for display)
    # Step 3: 20 landmark features with MediaPipe
    # Step 4: Stress score from face_stress.py regressor
    # ─────────────────────────────────────────────────────────────────────────
    if 'frame' in request.files:
        try:
            frame_bytes = request.files['frame'].read()
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

                # ── Step 1: Find face ─────────────────────────────────────────
                faces = face_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=3, minSize=(20, 20)
                ) if face_cascade is not None else []

                if len(faces) == 0:
                    gray_eq = cv2.equalizeHist(gray)
                    faces = face_cascade.detectMultiScale(
                        gray_eq, 1.1, 3, minSize=(20, 20)
                    ) if face_cascade is not None else []

                face_detected = len(faces) > 0

                # ── Step 2: Emotion label (CNN) ───────────────────────────────
                if face_detected and face_emotion_model is not None:
                    x, y_c, w, h = faces[0]
                    roi = gray[y_c:y_c+h, x:x+w]
                    roi = cv2.resize(roi, (48, 48)) / 255.0
                    roi = np.reshape(roi, (1, 48, 48, 1))
                    pred         = face_emotion_model.predict(roi, verbose=0)
                    face_emotion = FACE_EMOTION_LABELS[np.argmax(pred)]
                    face_conf_cnn = float(np.max(pred))   # softmax confidence
                    print(f"[Face CNN] {face_emotion}  conf={face_conf_cnn:.2f}")
                else:
                    face_conf_cnn = 0.3   # no CNN — use low default confidence

                    # ── Step 3: CNN emotion → stress score ───────────────────────
                    base = EMOTION_TO_STRESS_BASE.get(face_emotion, 35)
                    face_stress = round(base * face_conf_cnn + 35 * (1 - face_conf_cnn), 1)
                    face_conf   = face_conf_cnn * (0.9 if face_detected else 0.4)
                    face_source = "cnn_emotion"
                    print(f"[Face] {face_emotion} conf={face_conf_cnn:.2f} → stress={face_stress}")

        except Exception as e:
            print("[Face error]", e)
            traceback.print_exc()
    else:
        print("[Face] No frame received")

    # ─────────────────────────────────────────────────────────────────────────
    # VOICE PIPELINE
    # Step 1: Receive audio, convert to wav if needed
    # Step 2: Extract 28 psychoacoustic stress features
    # Step 3: Stress score from voice_stress_v2.py regressor
    # ─────────────────────────────────────────────────────────────────────────
    if 'audio' in request.files:
        try:
            tmp_raw = os.path.join(BASE_DIR, "tmp_voice_raw.webm")
            tmp_wav = os.path.join(BASE_DIR, "tmp_voice.wav")

            audio_data = request.files['audio'].read()
            with open(tmp_raw, 'wb') as f:
                f.write(audio_data)

            # Convert to wav
            converted = False
            try:
                from pydub import AudioSegment
                audio_seg = AudioSegment.from_file(tmp_raw)
                audio_seg.export(tmp_wav, format="wav")
                converted = True
            except Exception as pe:
                print("[Voice] pydub/ffmpeg failed, trying raw:", pe)
                tmp_wav = tmp_raw

            # Extract features
            if extract_voice_stress_features is not None:
                features = extract_voice_stress_features(tmp_wav)
                if features is not None:
                    raw_score    = predict_voice_stress(features)
                    voice_stress = float(np.clip(raw_score, 0, 100))

                    # Voice confidence from HNR (feature[9]) — higher HNR = cleaner signal
                    hnr_raw      = float(features[9])   # 0–1 normalized
                    voice_conf   = float(np.clip(hnr_raw, 0.2, 1.0))

                    # Voice label for display
                    voice_emotion = _stress_label(voice_stress).lower()
                    voice_source  = "prosodic_regressor"
                    print(f"[Voice] stress={voice_stress:.1f}  HNR-conf={voice_conf:.2f}")
                else:
                    print("[Voice] Feature extraction returned None")
            else:
                print("[Voice] voice_stress_v2.py not loaded — run training first")
                voice_source = "module_missing"

        except Exception as e:
            print("[Voice error]", e)
            traceback.print_exc()
    else:
        print("[Voice] No audio received")

    # ─────────────────────────────────────────────────────────────────────────
    # FUSION
    # Confidence-weighted blend — not a fixed 50/50
    # ─────────────────────────────────────────────────────────────────────────
    combined = _fuse_stress_scores(face_stress, face_conf, voice_stress, voice_conf)
    label    = _stress_label(combined)

    print(f"[Combined] face={face_stress:.1f}(w={face_conf:.2f}) "
          f"voice={voice_stress:.1f}(w={voice_conf:.2f}) → {combined} [{label}]")

    return jsonify({
        # Stress scores (continuous 0–100)
        "face_stress":     round(face_stress, 1),
        "voice_stress":    round(voice_stress, 1),
        "combined_stress": combined,

        # Labels for UI display
        "face_emotion":    face_emotion,
        "voice_emotion":   voice_emotion.capitalize(),
        "stress_label":    label,

        # Confidence weights (useful for UI transparency)
        "face_weight":     round(face_conf / (face_conf + voice_conf + 1e-8), 2),
        "voice_weight":    round(voice_conf / (face_conf + voice_conf + 1e-8), 2),

        # Source info for debugging
        "face_source":     face_source,
        "voice_source":    voice_source,
        "source":          "regression_v2"
    })


# ─── TASK SCHEDULING (unchanged) ─────────────────────────────────────────────
@app.route('/predict', methods=['POST'])
def predict():
    data         = request.get_json() or {}
    stress_level = data.get('stress_level', 40)
    tasks        = data.get('tasks', [])
    is_stressed  = stress_level >= 50
    priority_w   = {'high': 2, 'medium': 1, 'low': 0}
    sorted_tasks = sorted(
        tasks,
        key=lambda t: priority_w.get(t.get('priority', 'medium'), 1),
        reverse=not is_stressed
    )
    deferred = [t['id'] for t in tasks if is_stressed and t.get('priority') == 'high']

    if stress_level <= 20:
        title, message = "Peak state — go deep", "You're calm and energised. Start with your most demanding tasks."
    elif stress_level <= 40:
        title, message = "Steady rhythm today", "You're balanced. Work methodically and take breaks every 90 minutes."
    elif stress_level <= 55:
        title, message = "Be kind to yourself today", "Mild stress detected. Start small, avoid multitasking."
    elif stress_level <= 70:
        title, message = "Take it one step at a time", "Moderate stress. Focus on 1–2 tasks only. A short walk will help."
    elif stress_level <= 85:
        title, message = "Prioritise rest", "High stress. Do only essential tasks and take breaks every 30 min."
    else:
        title, message = "Rest is productive too", "Very high stress detected. Do only what's necessary today."

    return jsonify({
        "title":   title,
        "message": message,
        "order":   [t['id'] for t in sorted_tasks],
        "defer":   deferred,
        "model":   "bridge_v2"
    })


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("\n  GET  /health          -> model status")
    print("  POST /detect-combined -> regression face + voice stress")
    print("  POST /predict         -> task scheduling")
    print("  Running on http://localhost:5000\n")
    app.run(port=5000, debug=False)