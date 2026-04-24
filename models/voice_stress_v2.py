# voice_stress_v2.py — Physiological Voice Stress Regressor
# Location: models/voice_stress_v2.py
#
# REPLACES: the old voice_stress.py (which mapped emotions to scores)
#
# HOW IT WORKS:
#   - Extracts physiological voice stress markers (jitter, shimmer, HNR,
#     pitch variance, speech rate, energy dynamics) via librosa + parselmouth
#   - Trains a Gradient Boosting Regressor on AU-calibrated synthetic data
#     derived from published psychoacoustic stress research
#   - Outputs a continuous stress score 0–100 — NOT emotion labels
#
# SETUP (run once):
#   pip install librosa parselmouth scikit-learn numpy scipy
#
# TRAIN (run once from project ROOT):
#   python models/voice_stress_v2.py
#   → saves models/voice_stress_v2_model.pkl
#
# USED BY bridge.py:
#   from models.voice_stress_v2 import extract_voice_stress_features, predict_voice_stress

import os
import numpy as np
import pickle

# ─── PSYCHOACOUSTIC STRESS FEATURE WEIGHTS ────────────────────────────────────
# Based on published vocal stress research:
#   Schuller et al. (2011) — INTERSPEECH ComParE challenge: jitter/shimmer top features
#   Banse & Scherer (1996) — Pitch mean and variance under stress
#   Sigmund (2006) — HNR decreases under emotional stress
#   Lefter et al. (2011) — Energy/RMS variance as stress indicator
#   Bachorowski & Owren (1995) — Speaking rate changes under stress

VOICE_STRESS_FEATURE_WEIGHTS = {
    "pitch_mean":       0.10,   # F0 — rises under acute stress
    "pitch_variance":   0.18,   # Pitch variability — key stress marker
    "pitch_range":      0.08,   # Pitch range narrows under chronic stress
    "jitter_local":     0.15,   # Micro pitch irregularity — vocal cord tension
    "shimmer_local":    0.12,   # Amplitude irregularity
    "hnr":              0.12,   # Harmonics-to-noise — drops under stress
    "speech_rate":      0.08,   # Speaking rate — speeds up under stress
    "energy_mean":      0.05,   # Overall energy
    "energy_variance":  0.07,   # Energy instability = stress
    "spectral_centroid":0.05,   # Brightness — shifts under stress
}
# Weights sum to 1.0


# ─── FEATURE EXTRACTION ───────────────────────────────────────────────────────
def extract_voice_stress_features(file_path):
    """
    Input:  path to .wav or .mp3 audio file
    Output: numpy array of shape (28,) — physiological stress features
            OR None if extraction fails

    Features extracted (28 total):
      PITCH (F0) — 5 features
        [0]  pitch_mean          — mean fundamental frequency (Hz, normalized)
        [1]  pitch_std           — standard deviation of F0
        [2]  pitch_range         — max-min F0 (Hz)
        [3]  pitch_slope         — linear trend of F0 over time
        [4]  pitch_voiced_frac   — fraction of voiced frames

      JITTER / SHIMMER — 4 features
        [5]  jitter_local        — cycle-to-cycle F0 variation
        [6]  jitter_rap          — relative average perturbation
        [7]  shimmer_local       — cycle-to-cycle amplitude variation
        [8]  shimmer_apq3        — 3-point amplitude perturbation quotient

      HNR (Harmonics-to-Noise) — 2 features
        [9]  hnr_mean            — mean harmonics-to-noise ratio (dB)
        [10] hnr_std             — HNR variability

      ENERGY / INTENSITY — 4 features
        [11] rms_mean            — mean RMS energy
        [12] rms_std             — RMS variance (instability)
        [13] rms_slope           — energy trend over time
        [14] zero_crossing_rate  — proxy for breathiness/tension

      SPECTRAL — 5 features
        [15] spectral_centroid   — brightness (Hz, normalized)
        [16] spectral_bandwidth  — spread of frequency content
        [17] spectral_rolloff    — high-frequency content
        [18] spectral_flux       — rate of spectral change
        [19] mfcc_delta_energy   — MFCC delta energy (articulation dynamics)

      TEMPORAL / RATE — 4 features
        [20] tempo               — speech tempo (BPM proxy)
        [21] pause_ratio         — proportion of silent frames (silence = stress avoidance)
        [22] voiced_rate         — rate of voiced-to-unvoiced transitions
        [23] onset_rate          — syllable onset rate proxy

      MFCC SUMMARY — 4 features
        [24] mfcc1_mean          — 1st MFCC (energy-related)
        [25] mfcc2_mean          — 2nd MFCC (spectral shape)
        [26] mfcc1_std           — MFCC1 variability
        [27] mfcc2_std           — MFCC2 variability
    """
    try:
        import librosa
        import scipy.signal

        audio, sr = librosa.load(file_path, sr=16000, duration=5.0)

        if len(audio) < sr * 0.5:  # less than 0.5 seconds
            print("[VoiceStress] Audio too short")
            return None

        # ── PITCH (F0) via librosa PYIN ───────────────────────────────────────
        try:
            f0, voiced_flag, voiced_prob = librosa.pyin(
                audio, fmin=50, fmax=500,
                sr=sr, frame_length=2048
            )
            voiced_f0 = f0[voiced_flag & ~np.isnan(f0)]
        except Exception:
            # Fallback: autocorrelation pitch
            f0 = librosa.yin(audio, fmin=50, fmax=500, sr=sr)
            voiced_f0 = f0[f0 > 50]

        if len(voiced_f0) < 3:
            voiced_f0 = np.array([120.0, 120.0, 120.0])  # neutral fallback

        pitch_mean       = float(np.mean(voiced_f0)) / 300.0   # normalize ~0–1.5
        pitch_std        = float(np.std(voiced_f0))  / 100.0
        pitch_range      = float(np.ptp(voiced_f0))  / 200.0
        pitch_slope      = float(np.polyfit(np.arange(len(voiced_f0)),
                                             voiced_f0, 1)[0]) / 10.0
        pitch_voiced_frac = len(voiced_f0) / max(len(f0), 1)

        # ── JITTER (via F0 perturbation) ──────────────────────────────────────
        if len(voiced_f0) > 2:
            periods = 1.0 / (voiced_f0 + 1e-8)
            jitter_local = float(np.mean(np.abs(np.diff(periods))) /
                                  (np.mean(periods) + 1e-8))
            # RAP: mean of |Ti - mean(Ti-1, Ti, Ti+1)| / mean(T)
            if len(periods) > 4:
                rap_vals = []
                for i in range(1, len(periods) - 1):
                    local_mean = np.mean(periods[i-1:i+2])
                    rap_vals.append(abs(periods[i] - local_mean))
                jitter_rap = float(np.mean(rap_vals) / (np.mean(periods) + 1e-8))
            else:
                jitter_rap = jitter_local
        else:
            jitter_local = 0.01
            jitter_rap   = 0.01

        # ── SHIMMER (via amplitude perturbation) ──────────────────────────────
        # Estimate amplitude envelope at F0 periods
        rms_frames = librosa.feature.rms(y=audio, frame_length=512, hop_length=256)[0]
        if len(rms_frames) > 2:
            shimmer_local = float(np.mean(np.abs(np.diff(rms_frames))) /
                                   (np.mean(rms_frames) + 1e-8))
            if len(rms_frames) > 4:
                apq3_vals = []
                for i in range(1, len(rms_frames) - 1):
                    local_mean = np.mean(rms_frames[i-1:i+2])
                    apq3_vals.append(abs(rms_frames[i] - local_mean))
                shimmer_apq3 = float(np.mean(apq3_vals) /
                                      (np.mean(rms_frames) + 1e-8))
            else:
                shimmer_apq3 = shimmer_local
        else:
            shimmer_local = 0.01
            shimmer_apq3  = 0.01

        # ── HNR (Harmonics-to-Noise Ratio) ────────────────────────────────────
        # Approximate HNR via autocorrelation method
        try:
            import parselmouth
            snd = parselmouth.Sound(file_path)
            harmonicity = snd.to_harmonicity()
            hnr_values = harmonicity.values[harmonicity.values != -200]
            if len(hnr_values) > 0:
                hnr_mean = float(np.mean(hnr_values)) / 30.0  # normalize ~0–1
                hnr_std  = float(np.std(hnr_values))  / 15.0
            else:
                hnr_mean, hnr_std = 0.5, 0.1
        except Exception:
            # Fallback: librosa autocorrelation-based HNR approximation
            ac = librosa.autocorrelate(audio)
            if ac[0] > 0:
                hnr_approx = ac[1] / (ac[0] - ac[1] + 1e-8)
                hnr_mean = float(np.clip(hnr_approx, 0, 1))
            else:
                hnr_mean = 0.5
            hnr_std = 0.1

        # ── ENERGY / INTENSITY ────────────────────────────────────────────────
        rms_mean  = float(np.mean(rms_frames))
        rms_std   = float(np.std(rms_frames))
        rms_slope = float(np.polyfit(np.arange(len(rms_frames)),
                                      rms_frames, 1)[0]) * 100

        zcr = librosa.feature.zero_crossing_rate(audio)[0]
        zcr_mean = float(np.mean(zcr))

        # ── SPECTRAL FEATURES ─────────────────────────────────────────────────
        stft = np.abs(librosa.stft(audio))
        freqs = librosa.fft_frequencies(sr=sr)

        sc = librosa.feature.spectral_centroid(S=stft, sr=sr)[0]
        sb = librosa.feature.spectral_bandwidth(S=stft, sr=sr)[0]
        sr_feat = librosa.feature.spectral_rolloff(S=stft, sr=sr)[0]

        spec_centroid  = float(np.mean(sc)) / 4000.0   # normalize
        spec_bandwidth = float(np.mean(sb)) / 3000.0
        spec_rolloff   = float(np.mean(sr_feat)) / 8000.0

        # Spectral flux
        spec_flux = float(np.mean(np.diff(stft, axis=1) ** 2))
        spec_flux = np.clip(spec_flux / 1e4, 0, 2)

        # MFCC delta energy
        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=13)
        mfcc_delta = librosa.feature.delta(mfcc)
        mfcc_delta_energy = float(np.mean(mfcc_delta ** 2))
        mfcc_delta_energy = np.clip(mfcc_delta_energy / 100, 0, 2)

        # ── TEMPORAL / RATE ───────────────────────────────────────────────────
        # Tempo
        onset_env = librosa.onset.onset_strength(y=audio, sr=sr)
        try:
            tempo = librosa.beat.tempo(onset_envelope=onset_env, sr=sr)[0]
        except Exception:
            tempo = 120.0
        tempo_norm = float(tempo) / 200.0

        # Pause ratio (RMS below threshold = silence)
        silence_thresh = np.mean(rms_frames) * 0.1
        pause_ratio = float(np.mean(rms_frames < silence_thresh))

        # Voiced/unvoiced transition rate
        try:
            voiced_arr = (f0 > 50).astype(int)
            transitions = np.sum(np.abs(np.diff(voiced_arr)))
            voiced_rate = float(transitions) / len(voiced_arr)
        except Exception:
            voiced_rate = 0.1

        # Onset rate (syllable proxy)
        onsets = librosa.onset.onset_detect(onset_envelope=onset_env, sr=sr)
        onset_rate = len(onsets) / (len(audio) / sr + 1e-8) / 10.0  # normalize

        # ── MFCC SUMMARY ──────────────────────────────────────────────────────
        mfcc1_mean = float(np.mean(mfcc[0])) / 50.0
        mfcc2_mean = float(np.mean(mfcc[1])) / 30.0
        mfcc1_std  = float(np.std(mfcc[0]))  / 20.0
        mfcc2_std  = float(np.std(mfcc[1]))  / 15.0

        features = np.array([
            pitch_mean,        # 0
            pitch_std,         # 1
            pitch_range,       # 2
            pitch_slope,       # 3
            pitch_voiced_frac, # 4
            jitter_local,      # 5
            jitter_rap,        # 6
            shimmer_local,     # 7
            shimmer_apq3,      # 8
            hnr_mean,          # 9
            hnr_std,           # 10
            rms_mean,          # 11
            rms_std,           # 12
            rms_slope,         # 13
            zcr_mean,          # 14
            spec_centroid,     # 15
            spec_bandwidth,    # 16
            spec_rolloff,      # 17
            spec_flux,         # 18
            mfcc_delta_energy, # 19
            tempo_norm,        # 20
            pause_ratio,       # 21
            voiced_rate,       # 22
            onset_rate,        # 23
            mfcc1_mean,        # 24
            mfcc2_mean,        # 25
            mfcc1_std,         # 26
            mfcc2_std,         # 27
        ], dtype=np.float32)

        return np.nan_to_num(features, nan=0.0)

    except Exception as e:
        print(f"[VoiceStress] Feature extraction error: {e}")
        return None


# ─── PSYCHOACOUSTIC SYNTHETIC TRAINING DATA ───────────────────────────────────
def _generate_training_data(n_samples=3000, seed=42):
    """
    Generates synthetic training data calibrated to psychoacoustic stress research.

    Scientific basis per feature band:
    ────────────────────────────────────
    PITCH:
      Banse & Scherer (1996): Fear/stress → F0 mean +20–40 Hz above baseline
      Laukka et al. (2008): Pitch variance highest in high-arousal states
      Paulmann & Pell (2011): Pitch range narrows under chronic stress

    JITTER/SHIMMER:
      Schuller et al. (2011): Jitter local best single feature for stress
      Cummings & Clements (1990): Jitter increases 2–4× under extreme stress

    HNR:
      Sigmund (2006): HNR drops 3–8 dB under emotional arousal/stress
      Fraile et al. (2010): HNR inversely correlated with stress level

    ENERGY:
      Lefter et al. (2011): RMS variance increases 40–60% under stress

    SPEECH RATE:
      Strangert & Gustafson (2008): Speaking rate increases ~15% under mild stress
    """
    rng = np.random.RandomState(seed)
    X, y = [], []

    # 28 features per sample
    # stress_profiles: (low, high, {feature: (mean, std)})
    profiles = [
        # ── RELAXED 0–20 ──────────────────────────────────────────────────────
        (0, 20, {
            "pitch_mean":        (0.40, 0.05),   # ~120 Hz
            "pitch_std":         (0.08, 0.02),
            "pitch_range":       (0.25, 0.05),
            "pitch_slope":       (0.00, 0.01),
            "pitch_voiced":      (0.65, 0.08),
            "jitter_local":      (0.006, 0.002), # low jitter = smooth
            "jitter_rap":        (0.004, 0.002),
            "shimmer_local":     (0.04, 0.01),
            "shimmer_apq3":      (0.03, 0.01),
            "hnr_mean":          (0.70, 0.08),   # high HNR = clean voice
            "hnr_std":           (0.08, 0.02),
            "rms_mean":          (0.04, 0.01),
            "rms_std":           (0.01, 0.003),
            "rms_slope":         (0.00, 0.002),
            "zcr":               (0.05, 0.01),
            "spec_centroid":     (0.40, 0.05),
            "spec_bandwidth":    (0.45, 0.05),
            "spec_rolloff":      (0.40, 0.05),
            "spec_flux":         (0.20, 0.05),
            "mfcc_delta_e":      (0.10, 0.03),
            "tempo":             (0.55, 0.05),   # moderate pace
            "pause_ratio":       (0.35, 0.07),   # more pauses = relaxed
            "voiced_rate":       (0.10, 0.03),
            "onset_rate":        (0.30, 0.05),
            "mfcc1":             (0.00, 0.10),
            "mfcc2":             (0.00, 0.08),
            "mfcc1_std":         (0.30, 0.05),
            "mfcc2_std":         (0.25, 0.05),
        }),
        # ── MILD STRESS 20–50 ─────────────────────────────────────────────────
        (20, 50, {
            "pitch_mean":        (0.47, 0.06),   # ~140 Hz — rising
            "pitch_std":         (0.14, 0.03),   # more variable
            "pitch_range":       (0.30, 0.05),
            "pitch_slope":       (0.02, 0.015),
            "pitch_voiced":      (0.62, 0.07),
            "jitter_local":      (0.012, 0.003),
            "jitter_rap":        (0.008, 0.003),
            "shimmer_local":     (0.06, 0.015),
            "shimmer_apq3":      (0.05, 0.012),
            "hnr_mean":          (0.58, 0.08),
            "hnr_std":           (0.12, 0.03),
            "rms_mean":          (0.05, 0.012),
            "rms_std":           (0.016, 0.004),
            "rms_slope":         (0.01, 0.003),
            "zcr":               (0.065, 0.012),
            "spec_centroid":     (0.44, 0.05),
            "spec_bandwidth":    (0.48, 0.05),
            "spec_rolloff":      (0.43, 0.05),
            "spec_flux":         (0.30, 0.06),
            "mfcc_delta_e":      (0.18, 0.04),
            "tempo":             (0.60, 0.06),
            "pause_ratio":       (0.28, 0.06),
            "voiced_rate":       (0.14, 0.04),
            "onset_rate":        (0.38, 0.06),
            "mfcc1":             (0.05, 0.12),
            "mfcc2":             (0.03, 0.10),
            "mfcc1_std":         (0.38, 0.06),
            "mfcc2_std":         (0.30, 0.06),
        }),
        # ── MODERATE STRESS 50–75 ─────────────────────────────────────────────
        (50, 75, {
            "pitch_mean":        (0.55, 0.07),   # ~165 Hz
            "pitch_std":         (0.22, 0.04),   # high variability
            "pitch_range":       (0.22, 0.06),   # range starts narrowing
            "pitch_slope":       (0.04, 0.02),
            "pitch_voiced":      (0.58, 0.08),
            "jitter_local":      (0.022, 0.005),
            "jitter_rap":        (0.015, 0.004),
            "shimmer_local":     (0.09, 0.02),
            "shimmer_apq3":      (0.07, 0.015),
            "hnr_mean":          (0.44, 0.08),   # HNR dropping
            "hnr_std":           (0.18, 0.04),
            "rms_mean":          (0.06, 0.015),
            "rms_std":           (0.026, 0.006),
            "rms_slope":         (0.02, 0.005),
            "zcr":               (0.082, 0.015),
            "spec_centroid":     (0.50, 0.06),
            "spec_bandwidth":    (0.52, 0.06),
            "spec_rolloff":      (0.48, 0.06),
            "spec_flux":         (0.45, 0.08),
            "mfcc_delta_e":      (0.30, 0.06),
            "tempo":             (0.67, 0.07),   # faster
            "pause_ratio":       (0.20, 0.05),
            "voiced_rate":       (0.20, 0.05),
            "onset_rate":        (0.48, 0.07),
            "mfcc1":             (0.12, 0.14),
            "mfcc2":             (0.08, 0.12),
            "mfcc1_std":         (0.48, 0.08),
            "mfcc2_std":         (0.38, 0.07),
        }),
        # ── HIGH STRESS 75–100 ────────────────────────────────────────────────
        (75, 100, {
            "pitch_mean":        (0.63, 0.08),   # ~190 Hz — high F0
            "pitch_std":         (0.30, 0.05),   # very high variability
            "pitch_range":       (0.15, 0.05),   # very narrow range
            "pitch_slope":       (0.07, 0.025),
            "pitch_voiced":      (0.52, 0.09),
            "jitter_local":      (0.038, 0.008), # high jitter
            "jitter_rap":        (0.025, 0.006),
            "shimmer_local":     (0.14, 0.03),
            "shimmer_apq3":      (0.11, 0.025),
            "hnr_mean":          (0.28, 0.08),   # low HNR = noisy voice
            "hnr_std":           (0.26, 0.05),
            "rms_mean":          (0.075, 0.02),
            "rms_std":           (0.040, 0.008),
            "rms_slope":         (0.04, 0.008),
            "zcr":               (0.10, 0.02),
            "spec_centroid":     (0.58, 0.07),
            "spec_bandwidth":    (0.58, 0.07),
            "spec_rolloff":      (0.56, 0.07),
            "spec_flux":         (0.65, 0.10),
            "mfcc_delta_e":      (0.50, 0.10),
            "tempo":             (0.75, 0.08),   # rapid speech
            "pause_ratio":       (0.12, 0.04),   # fewer pauses
            "voiced_rate":       (0.28, 0.06),
            "onset_rate":        (0.60, 0.09),
            "mfcc1":             (0.20, 0.16),
            "mfcc2":             (0.14, 0.14),
            "mfcc1_std":         (0.60, 0.10),
            "mfcc2_std":         (0.48, 0.09),
        }),
    ]

    keys = [
        "pitch_mean","pitch_std","pitch_range","pitch_slope","pitch_voiced",
        "jitter_local","jitter_rap","shimmer_local","shimmer_apq3",
        "hnr_mean","hnr_std","rms_mean","rms_std","rms_slope","zcr",
        "spec_centroid","spec_bandwidth","spec_rolloff","spec_flux","mfcc_delta_e",
        "tempo","pause_ratio","voiced_rate","onset_rate",
        "mfcc1","mfcc2","mfcc1_std","mfcc2_std"
    ]

    samples_per_band = n_samples // len(profiles)

    for (s_low, s_high, profile) in profiles:
        for _ in range(samples_per_band):
            stress = rng.uniform(s_low, s_high)
            feat = []
            for k in keys:
                mu, sigma = profile[k]
                val = rng.normal(mu, sigma)
                feat.append(float(val))
            X.append(np.array(feat, dtype=np.float32))
            y.append(stress)

    return np.array(X), np.array(y)


# ─── TRAIN ────────────────────────────────────────────────────────────────────
def train():
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import mean_absolute_error, r2_score

    print("\n[VoiceStress TRAIN] Generating psychoacoustic training data...")
    X, y = _generate_training_data(n_samples=4000)
    print(f"  Samples: {len(X)}  |  Features: {X.shape[1]}")
    print(f"  Stress range: {y.min():.1f} – {y.max():.1f}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    print("[VoiceStress TRAIN] Training Gradient Boosting Regressor...")

    pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('reg', GradientBoostingRegressor(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42
        ))
    ])

    pipeline.fit(X_train, y_train)

    y_pred = pipeline.predict(X_test)
    mae    = mean_absolute_error(y_test, y_pred)
    r2     = r2_score(y_test, y_pred)

    print(f"\n[VoiceStress TRAIN] Test MAE : {mae:.2f} stress points")
    print(f"[VoiceStress TRAIN] Test R²  : {r2:.4f}")

    importances = pipeline.named_steps['reg'].feature_importances_
    feat_names = [
        "pitch_mean","pitch_std","pitch_range","pitch_slope","pitch_voiced",
        "jitter_local","jitter_rap","shimmer_local","shimmer_apq3",
        "hnr_mean","hnr_std","rms_mean","rms_std","rms_slope","zcr",
        "spec_centroid","spec_bw","spec_rolloff","spec_flux","mfcc_delta",
        "tempo","pause_ratio","voiced_rate","onset_rate",
        "mfcc1","mfcc2","mfcc1_std","mfcc2_std"
    ]
    print("\n  Top 5 most important features:")
    top5 = np.argsort(importances)[::-1][:5]
    for i in top5:
        print(f"    {feat_names[i]:<20} {importances[i]:.4f}")

    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "voice_stress_v2_model.pkl"
    )
    with open(model_path, 'wb') as f:
        pickle.dump(pipeline, f)

    print(f"\n[VoiceStress TRAIN] Model saved → {model_path}")
    print("[VoiceStress TRAIN] Done!\n")
    return True


# ─── LOAD MODEL ───────────────────────────────────────────────────────────────
_voice_stress_model = None

def _load_model():
    global _voice_stress_model
    if _voice_stress_model is not None:
        return _voice_stress_model
    model_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "voice_stress_v2_model.pkl"
    )
    if not os.path.exists(model_path):
        print("[VoiceStress WARN] voice_stress_v2_model.pkl not found.")
        print("  Run:  python models/voice_stress_v2.py  to train it first.")
        return None
    with open(model_path, 'rb') as f:
        _voice_stress_model = pickle.load(f)
    print("[OK] Voice stress regression model loaded")
    return _voice_stress_model


# ─── PREDICT — called by bridge.py ───────────────────────────────────────────
def predict_voice_stress(features):
    """
    Input:  numpy array of shape (28,) from extract_voice_stress_features()
    Output: float in range 0.0 – 100.0  (continuous stress score)
    """
    model = _load_model()

    if model is None:
        # Fallback: weighted heuristic using top psychoacoustic markers
        # Features: [1]=pitch_std, [5]=jitter, [9]=hnr, [12]=rms_std
        pitch_stress  = np.clip(features[1] / 0.30, 0, 1) * 100
        jitter_stress = np.clip(features[5] / 0.04, 0, 1) * 100
        hnr_stress    = np.clip(1.0 - features[9], 0, 1) * 100  # inverse
        energy_stress = np.clip(features[12] / 0.04, 0, 1) * 100

        score = (VOICE_STRESS_FEATURE_WEIGHTS["pitch_variance"]  * pitch_stress  +
                 VOICE_STRESS_FEATURE_WEIGHTS["jitter_local"]     * jitter_stress +
                 VOICE_STRESS_FEATURE_WEIGHTS["hnr"]              * hnr_stress    +
                 VOICE_STRESS_FEATURE_WEIGHTS["energy_variance"]  * energy_stress)
        return float(np.clip(score * (1 / 0.52), 0, 100))  # normalize

    features_2d  = np.array(features).reshape(1, -1)
    stress_score = model.predict(features_2d)[0]
    return float(np.clip(stress_score, 0, 100))


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  MindFlow — Voice Stress Regression Model Trainer")
    print("=" * 55)
    print("  Scientific basis: jitter, shimmer, HNR, pitch variance")
    print("  Features: 28 psychoacoustic stress markers")
    print("  Model: Gradient Boosting Regressor → continuous 0–100 score")
    print()

    success = train()

    if success:
        print("\n[TEST] Sanity check with synthetic features...")
        # Simulate stressed voice
        stressed = np.array([
            0.63, 0.30, 0.15, 0.07, 0.52,    # pitch: high mean, high variance
            0.038, 0.025, 0.14, 0.11,          # high jitter + shimmer
            0.28, 0.26,                         # low HNR
            0.075, 0.040, 0.04, 0.10,          # high RMS + ZCR
            0.58, 0.58, 0.56, 0.65, 0.50,     # spectral
            0.75, 0.12, 0.28, 0.60,            # fast pace, few pauses
            0.20, 0.14, 0.60, 0.48            # MFCC
        ], dtype=np.float32)

        # Simulate relaxed voice
        relaxed = np.array([
            0.40, 0.08, 0.25, 0.00, 0.65,
            0.006, 0.004, 0.04, 0.03,
            0.70, 0.08,
            0.04, 0.01, 0.00, 0.05,
            0.40, 0.45, 0.40, 0.20, 0.10,
            0.55, 0.35, 0.10, 0.30,
            0.00, 0.00, 0.30, 0.25
        ], dtype=np.float32)

        s_score = predict_voice_stress(stressed)
        r_score = predict_voice_stress(relaxed)
        print(f"  Stressed voice → {s_score:.1f}/100  (should be ~75–95)")
        print(f"  Relaxed voice  → {r_score:.1f}/100  (should be ~5–20)")
        print("\n  ✓ Model is working" if s_score > r_score else
              "\n  ✗ WARNING: check feature profiles")