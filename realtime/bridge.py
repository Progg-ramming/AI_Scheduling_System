# bridge.py — MindFlow AI Bridge
# Location: realtime/bridge.py
# Run: python bridge.py

import os
import sys
import time
import traceback
import logging

# ─── STARTUP BANNER (Shown immediately) ──────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR   = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(ROOT_DIR, "models")

FACE_H5     = os.path.join(MODELS_DIR, "face_emotion_model.h5")
VOICE_H5    = os.path.join(MODELS_DIR, "voice_emotion_model.h5")
CASCADE_XML = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")

print("="*55)
print("  MindFlow AI Bridge — starting up")
print("="*55)
print("  Project Root:", ROOT_DIR)
print("  Models dir  :", os.path.abspath(MODELS_DIR))
print("  Face h5     :", "FOUND" if os.path.exists(FACE_H5)     else "MISSING")
print("  Voice h5    :", "FOUND" if os.path.exists(VOICE_H5)    else "MISSING")
print("  Cascade xml :", "FOUND" if os.path.exists(CASCADE_XML) else "MISSING")
print("="*55)

# Move heavy imports to top level to avoid request-time overhead
try:
    import librosa
    import numpy as np
    import cv2
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError as e:
    print(f"  [CRITICAL] Missing dependency: {e}")
    sys.exit(1)

# Ensure ROOT_DIR is in path for local model imports
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Load voice_stress.py directly from file path
import importlib.util
_vs_path = os.path.join(MODELS_DIR, "voice_stress.py")
_vs_spec = importlib.util.spec_from_file_location("voice_stress", _vs_path)
_vs_mod  = importlib.util.module_from_spec(_vs_spec)
_vs_spec.loader.exec_module(_vs_mod)
extract_features = _vs_mod.extract_features
predict_stress   = _vs_mod.predict_stress
print("[OK] voice_stress.py loaded")
os.environ["TF_NUM_INTEROP_THREADS"] = "1"
os.environ["TF_NUM_INTRAOP_THREADS"] = "1"

app = Flask(__name__)
CORS(app)

# ─── LOAD MODELS ─────────────────────────────────────────────────────────────
face_model   = None
face_cascade = None
voice_model  = None

def _load_h5(path):
    import h5py
    import traceback
    if not os.path.exists(path):
        print(f"  [Error] File does not exist: {path}")
        return None
        
    print(f"  [Info] Attempting to load model: {os.path.basename(path)}...")
    
    # Strategy 1: Modern TensorFlow (tf.keras)
    try:
        import tensorflow as tf
        model = tf.keras.models.load_model(path, compile=False)
        print(f"  [Success] Loaded via tf.keras: {os.path.basename(path)}")
        return model
    except Exception as e1:
        print(f"  [Strategy 1 failed]: {str(e1)[:80]}")

    # Strategy 2: Direct Keras import (Standalone Keras or TF 2.16+)
    try:
        import keras
        model = keras.models.load_model(path, compile=False)
        print(f"  [Success] Loaded via standalone keras: {os.path.basename(path)}")
        return model
    except Exception as e2:
        print(f"  [Strategy 2 failed]: {str(e2)[:80]}")

    # Strategy 3: Legacy tensorflow.keras
    try:
        from tensorflow.keras.models import load_model
        model = load_model(path, compile=False)
        print(f"  [Success] Loaded via tensorflow.keras: {os.path.basename(path)}")
        return model
    except Exception as e3:
        print(f"  [Strategy 3 failed]: {str(e3)[:80]}")

    # Strategy 4: Internal TensorFlow Keras (python.keras)
    try:
        from tensorflow.python.keras.models import load_model
        model = load_model(path, compile=False)
        print(f"  [Success] Loaded via internal python.keras: {os.path.basename(path)}")
        return model
    except Exception as e4:
        print(f"  [Strategy 4 failed]: {str(e4)[:80]}")

    # Strategy 5: Compatibility v1 (for older systems)
    try:
        import tensorflow.compat.v1 as tf_v1
        model = tf_v1.keras.models.load_model(path, compile=False)
        print(f"  [Success] Loaded via tf.compat.v1: {os.path.basename(path)}")
        return model
    except Exception as e5:
        print(f"  [Strategy 5 failed]: {str(e5)[:80]}")

    # Strategy 6: JSON fallback
    try:
        from tensorflow.keras.models import model_from_json
        with h5py.File(path, "r") as f:
            raw = f.attrs.get("model_config")
            if raw is None:
                print("  [Error] No model_config attribute in H5")
                return None
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            # Patch older Keras JSON
            raw = raw.replace(chr(34)+"batch_shape"+chr(34), chr(34)+"batch_input_shape"+chr(34))
            raw = raw.replace(", "+chr(34)+"optional"+chr(34)+": false", "")
            raw = raw.replace(", "+chr(34)+"optional"+chr(34)+": true", "")
            raw = raw.replace(","+chr(34)+"optional"+chr(34)+":false", "")
            raw = raw.replace(","+chr(34)+"optional"+chr(34)+":true", "")
            m = model_from_json(raw)
            m.load_weights(path)
            return m
    except Exception as e2:
        print(f"  [Strategy 2 failed for {os.path.basename(path)}]:", str(e2))
    return None

_face_model_obj  = _load_h5(FACE_H5)
_voice_model_obj = _load_h5(VOICE_H5)

if _face_model_obj is not None:
    face_model = _face_model_obj
    print("[OK] Face emotion model loaded")
else:
    print("[ERROR] Face model could not be loaded")

try:
    face_cascade = cv2.CascadeClassifier(CASCADE_XML)
    if face_cascade.empty():
        raise ValueError("Cascade file empty")
    print("[OK] Face cascade loaded")
except Exception as e:
    print("[ERROR] Face cascade:", e)

if _voice_model_obj is not None:
    voice_model = _voice_model_obj
    print("[OK] Voice emotion model loaded")
else:
    print("[ERROR] Voice model could not be loaded")

# ─── LABELS ──────────────────────────────────────────────────────────────────
FACE_EMOTION_LABELS  = ["Angry","Disgust","Fear","Happy","Sad","Surprise","Neutral"]
VOICE_EMOTION_LABELS = ["neutral","calm","happy","sad","angry","fear","disgust","surprise"]

EMOTION_TO_STRESS = {
    "Happy":10,  "happy":10,  "calm":15,
    "Neutral":35,"neutral":35,
    "Surprise":40,"surprise":40,
    "Sad":60,    "sad":60,
    "Disgust":65,"disgust":65,
    "Fear":80,   "fear":80,
    "Angry":90,  "angry":90,
}

# ─── HEALTH ──────────────────────────────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status":       "ok",
        "face_model":   face_model is not None,
        "voice_model":  voice_model is not None,
        "face_cascade": face_cascade is not None,
    })

# ─── COMBINED DETECTION ──────────────────────────────────────────────────────
@app.route('/detect-combined', methods=['POST'])
def detect_combined():
    face_emotion  = "Neutral"
    face_stress   = 35
    voice_emotion = "neutral"
    voice_stress  = 35
    face_source   = "fallback"
    voice_source  = "fallback"

    # ── FACE ──────────────────────────────────────────────────────────────────
    if face_model is not None and face_cascade is not None and 'frame' in request.files:
        try:
            frame_bytes = request.files['frame'].read()
            nparr = np.frombuffer(frame_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is not None:
                gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Try multiple scales to find face
                faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20, 20))
                if len(faces) == 0:
                    # Try with enhanced contrast
                    gray = cv2.equalizeHist(gray)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 3, minSize=(20, 20))
                if len(faces) > 0:
                    x, y, w, h = faces[0]
                    roi = gray[y:y+h, x:x+w]
                    roi = cv2.resize(roi, (48, 48))
                    roi = roi / 255.0
                    roi = np.reshape(roi, (1, 48, 48, 1))
                    pred         = face_model.predict(roi, verbose=0)
                    face_emotion = FACE_EMOTION_LABELS[np.argmax(pred)]
                    face_stress  = EMOTION_TO_STRESS.get(face_emotion, 35)
                    face_source  = "face_model"
                    print("[Face]", face_emotion, face_stress)
                else:
                    face_source = "no_face_detected"
                    print("[Face] No face in frame")
        except Exception as e:
            print("[Face error]", e)
    else:
        if face_model is None:
            print("[Face] Model not loaded")
        if 'frame' not in request.files:
            print("[Face] No frame received")

    # ── VOICE ─────────────────────────────────────────────────────────────────
    if voice_model is not None and 'audio' in request.files:
        try:
            tmp_raw = os.path.join(BASE_DIR, "tmp_voice_raw.webm")
            tmp_wav = os.path.join(BASE_DIR, "tmp_voice.wav")

            audio_data = request.files['audio'].read()
            with open(tmp_raw, 'wb') as f:
                f.write(audio_data)

            # Try to convert webm to wav (requires ffmpeg)
            converted = False
            try:
                from pydub import AudioSegment
                audio_seg = AudioSegment.from_file(tmp_raw)
                audio_seg.export(tmp_wav, format="wav")
                converted = True
            except Exception as pe:
                print("[Voice] pydub/ffmpeg failed, trying raw read:", pe)
                tmp_wav = tmp_raw
            
            features = extract_features(tmp_wav)
            if features is not None:
                voice_label = predict_stress(features)
                
                if voice_label == "Low Stress":
                    voice_stress = 25
                elif voice_label == "Medium Stress":
                    voice_stress = 55
                else:
                    voice_stress = 85

                voice_emotion = voice_label
                voice_source  = "ml_model"
                print("[Voice ML]", voice_label, voice_stress)
            else:
                print("[Voice] Feature extraction failed")

            print("[Voice]", voice_emotion, voice_stress)

        except Exception as e:
            print("[Voice error]", e)
    else:
        if voice_model is None:
            print("[Voice] Model not loaded")
        if 'audio' not in request.files:
            print("[Voice] No audio received")

    # ── COMBINE ───────────────────────────────────────────────────────────────
    combined = round((face_stress * 0.5) + (voice_stress * 0.5))
    print("[Combined]", combined)

    return jsonify({
        "face_emotion":    face_emotion,
        "face_stress":     face_stress,
        "face_source":     face_source,
        "voice_emotion":   voice_emotion.capitalize(),
        "voice_stress":    voice_stress,
        "voice_source":    voice_source,
        "combined_stress": combined,
        "source":          "combined"
    })

# ─── TASK SCHEDULING ─────────────────────────────────────────────────────────
@app.route('/predict', methods=['POST'])
def predict():
    data         = request.get_json() or {}
    stress_level = data.get('stress_level', 40)
    tasks        = data.get('tasks', [])
    is_stressed  = stress_level >= 50
    priority_w   = {'high':2, 'medium':1, 'low':0}
    sorted_tasks = sorted(tasks, key=lambda t: priority_w.get(t.get('priority','medium'),1), reverse=not is_stressed)
    deferred     = [t['id'] for t in tasks if is_stressed and t.get('priority') == 'high']

    if stress_level <= 20:
        title, message = "Peak state — go deep", "You're calm and energised. Start with your most demanding tasks."
    elif stress_level <= 40:
        title, message = "Steady rhythm today", "You're balanced. Work methodically and take breaks every 90 minutes."
    elif stress_level <= 60:
        title, message = "Be kind to yourself today", "Stress is noticeable. Start small, avoid multitasking."
    elif stress_level <= 80:
        title, message = "Take it one step at a time", "Significant stress. Focus on 1-2 tasks. A walk will help."
    else:
        title, message = "Rest is productive too", "You're overwhelmed. Do only what's necessary today."

    return jsonify({
        "title":   title,
        "message": message,
        "order":   [t['id'] for t in sorted_tasks],
        "defer":   deferred,
        "model":   "bridge"
    })

if __name__ == '__main__':
    print("\n  GET  /health          -> check models")
    print("  POST /detect-combined -> face + voice")
    print("  POST /predict         -> scheduling")
    print("  Running on http://localhost:5000\n")
    app.run(port=5000, debug=False)