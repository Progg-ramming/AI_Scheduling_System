# voice_stress.py — Proper ML-based voice stress classifier
# Replaces the old 3-line threshold version
#
# HOW TO USE:
#   Step 1 — Train the model once:   python voice_stress.py
#   Step 2 — It saves voice_stress_model.pkl automatically
#   Step 3 — bridge.py loads it and uses predict_stress() from here
#
# Place this file in: models/voice_stress.py (replace the old one)
# Run training from the ROOT of the friend's project folder

import os
import numpy as np
import librosa
import pickle

# ─── EMOTION → STRESS SCORE MAPPING (1–100) ──────────────────────────────────
EMOTION_STRESS_MAP = {
    "happy":    10,
    "neutral":  30,
    "surprise": 40,
    "sad":      60,
    "disgust":  70,
    "fear":     78,
    "anger":    88,
}

# RAVDESS filenames encode emotion as 2-digit code (e.g. 03 = happy)
RAVDESS_CODE_MAP = {
    "01": "neutral",
    "02": "neutral",
    "03": "happy",
    "04": "sad",
    "05": "anger",
    "06": "fear",
    "07": "disgust",
    "08": "surprise",
}

def score_to_label(score):
    if score <= 35:
        return "Low Stress"
    elif score <= 65:
        return "Medium Stress"
    else:
        return "High Stress"


# ─── FEATURE EXTRACTION ───────────────────────────────────────────────────────
def extract_features(file_path):
    try:
        audio, sr = librosa.load(file_path, duration=2)

        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=40)
        mfcc_mean = np.mean(mfcc, axis=1)
        mfcc_std  = np.std(mfcc, axis=1)

        chroma = librosa.feature.chroma_stft(y=audio, sr=sr)
        chroma_mean = np.mean(chroma, axis=1)

        contrast = librosa.feature.spectral_contrast(y=audio, sr=sr)
        contrast_mean = np.mean(contrast, axis=1)

        # Total: 40 + 40 + 12 + 7 = 99 features
        return np.concatenate([mfcc_mean, mfcc_std, chroma_mean, contrast_mean])

    except Exception as e:
        print(f"  [WARN] Skipping {os.path.basename(file_path)}: {e}")
        return None


# ─── SCAN HELPERS ─────────────────────────────────────────────────────────────
def _get_emotion_from_ravdess_filename(fname):
    try:
        code = fname.split('-')[2]
        return RAVDESS_CODE_MAP.get(code, None)
    except:
        return None


def _scan_organised_folder(organised_path, X, y, files_found):
    if not os.path.isdir(organised_path):
        print(f"  [SKIP] Not found: {organised_path}")
        return files_found

    for folder_name in os.listdir(organised_path):
        folder_path = os.path.join(organised_path, folder_name)
        if not os.path.isdir(folder_path):
            continue
        emotion = folder_name.lower()
        if emotion not in EMOTION_STRESS_MAP:
            continue

        wav_files = [f for f in os.listdir(folder_path)
                     if f.endswith('.wav') or f.endswith('.mp3')]
        print(f"  organized/{folder_name}: {len(wav_files)} files")

        for fname in wav_files:
            features = extract_features(os.path.join(folder_path, fname))
            if features is not None:
                X.append(features)
                y.append(EMOTION_STRESS_MAP[emotion])
                files_found += 1

    return files_found


def _scan_ravdess_folder(ravdess_path, X, y, files_found):
    if not os.path.isdir(ravdess_path):
        print(f"  [SKIP] Not found: {ravdess_path}")
        return files_found

    total = 0
    for actor_folder in os.listdir(ravdess_path):
        actor_path = os.path.join(ravdess_path, actor_folder)
        if not os.path.isdir(actor_path):
            continue

        for fname in os.listdir(actor_path):
            if not (fname.endswith('.wav') or fname.endswith('.mp3')):
                continue
            emotion = _get_emotion_from_ravdess_filename(fname)
            if emotion is None or emotion not in EMOTION_STRESS_MAP:
                continue

            features = extract_features(os.path.join(actor_path, fname))
            if features is not None:
                X.append(features)
                y.append(EMOTION_STRESS_MAP[emotion])
                files_found += 1
                total += 1

    print(f"  ravdess/ (all actors): {total} files")
    return files_found


# ─── TRAIN ────────────────────────────────────────────────────────────────────
def train():
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline

    print("\n[TRAIN] Scanning audio files...")

    X = []
    y = []
    files_found = 0

    organised_path = os.path.join("audio", "organized")
    ravdess_path   = os.path.join("audio", "ravdess")

    files_found = _scan_organised_folder(organised_path, X, y, files_found)
    files_found = _scan_ravdess_folder(ravdess_path, X, y, files_found)

    print(f"\n[TRAIN] Total usable files: {files_found}")

    if files_found < 20:
        print("\n[ERROR] Not enough audio files found.")
        print("  Make sure you run this from the project ROOT folder.")
        print("  Expected: audio/organized/anger/*.wav")
        print("        OR: audio/ravdess/Actor_01/*.wav")
        return False

    X = np.array(X)
    y = np.array(y)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    print("[TRAIN] Training Gradient Boosting classifier...")

    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', GradientBoostingClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            random_state=42
        ))
    ])

    pipeline.fit(X_train, y_train)

    y_pred   = pipeline.predict(X_test)
    accuracy = np.mean(y_pred == y_test) * 100
    print(f"\n[TRAIN] Accuracy: {accuracy:.1f}%")

    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "voice_stress_model.pkl"
    )
    with open(model_path, 'wb') as f:
        pickle.dump(pipeline, f)

    print(f"[TRAIN] Model saved → {model_path}")
    print("[TRAIN] Done! You can now run bridge.py\n")
    return True


# ─── LOAD MODEL ───────────────────────────────────────────────────────────────
_model = None

def _load_model():
    global _model
    if _model is not None:
        return _model
    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "voice_stress_model.pkl"
    )
    if not os.path.exists(model_path):
        print("[WARN] voice_stress_model.pkl not found.")
        print("       Run:  python models/voice_stress.py   to train it first.")
        return None
    with open(model_path, 'rb') as f:
        _model = pickle.load(f)
    print("[OK] Voice stress model loaded")
    return _model


# ─── PREDICT — called by bridge.py ───────────────────────────────────────────
def predict_stress(features):
    """
    Input:  numpy array of shape (99,) from extract_features()
    Output: "Low Stress" | "Medium Stress" | "High Stress"
    """
    model = _load_model()

    if model is None:
        score = np.mean(features)
        if score > -20:   return "High Stress"
        elif score > -40: return "Medium Stress"
        else:             return "Low Stress"

    features_2d  = np.array(features).reshape(1, -1)
    stress_score = model.predict(features_2d)[0]
    return score_to_label(stress_score)


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  MindFlow Voice Stress Model Trainer")
    print("=" * 50)
    print("  NOTE: Run from your PROJECT ROOT folder")
    print("  e.g.  cd path/to/friends/project")
    print("        python models/voice_stress.py")
    print()

    success = train()

    if success:
        print("\n[TEST] Quick sanity check...")
        dummy = np.zeros(99)
        label = predict_stress(dummy)
        print(f"  Dummy prediction: {label}  model is working")
    else:
        print("\n[FAILED] Check folder structure and try again.")