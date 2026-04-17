import numpy as np
import tensorflow as tf
import cv2
import librosa
import os
import joblib
import mediapipe as mp

mp_face = mp.solutions.face_mesh
face_mesh = mp_face.FaceMesh(static_image_mode=True)
final_model = joblib.load("models/final_stress_model.pkl")

folder = r"C:\Users\R COM\Downloads\major project\datasets\dataset\train"

print(os.listdir(folder))

# ===============================
# LOAD TRAINED MODELS 
# ===============================

face_model = tf.keras.models.load_model("models/face_emotion_model.h5")
voice_model = tf.keras.models.load_model("models/voice_emotion_model.h5")

# Emotion labels
emotion_labels = [
    "angry",
    "disgust",
    "fear",
    "happy",
    "sad",
    "surprise",
    "neutral"
]

# ===============================
# FACE PREPROCESSING
# ===============================
def preprocess_face(image_path):

    img = cv2.imread(image_path)

    if img is None:
        raise ValueError("Image not found. Check file path.")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    face = cv2.resize(gray, (48,48))

    face = face / 255.0

    face = np.reshape(face, (1,48,48,1))

    return face


# ===============================
# AUDIO PREPROCESSING           
# ===============================

def extract_audio_features(audio_path):            #this section might create error, requires retraining the model

    audio, sr = librosa.load(audio_path, duration=3, offset=0.5)

    # 🎤 Pitch (voice frequency)
    pitch = librosa.yin(audio, fmin=50, fmax=300)
    pitch_mean = np.mean(pitch)

    # 🔊 Energy (loudness)
    energy = np.mean(audio**2)

    # 🎼 MFCC (voice texture)
    mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=40)
    mfcc_mean = np.mean(mfcc.T, axis=0)

    # Combine all features
    features = np.concatenate(([pitch_mean, energy], mfcc_mean))

    # reshape for model
    features = features.reshape(1, -1)

    return features





#CREATE FUNCTION FOR REAL FEATURES

def extract_facial_features(image_path):
    img = cv2.imread(image_path)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    results = face_mesh.process(rgb)

    EAR = 0
    MAR = 0
    eyebrow_dist = 0

    if results.multi_face_landmarks:
        face_landmarks = results.multi_face_landmarks[0]

        h, w, _ = img.shape

        points = []
        for lm in face_landmarks.landmark:
            points.append((int(lm.x * w), int(lm.y * h)))

        def dist(p1, p2):
            return np.linalg.norm(np.array(p1) - np.array(p2))

        # Eye
        left_eye = [33, 160, 158, 133, 153, 144]

        A = dist(points[left_eye[1]], points[left_eye[5]])
        B = dist(points[left_eye[2]], points[left_eye[4]])
        C = dist(points[left_eye[0]], points[left_eye[3]])

        EAR = (A + B) / (2.0 * C + 1e-6)

        # Mouth
        MAR = dist(points[13], points[14])

        # Eyebrow
        eyebrow_dist = dist(points[70], points[33])

    return EAR, MAR, eyebrow_dist


# ===============================
# MULTIMODAL PREDICTION FUNCTION
# ===============================

def multimodal_prediction(face_image, voice_audio):

    # Face preprocessing
    face_input = preprocess_face(face_image)

    # Voice preprocessing
    voice_input = extract_audio_features(voice_audio)

    # Face prediction
    face_pred = face_model.predict(face_input)

    # Voice prediction
    voice_pred = voice_model.predict(voice_input)

    # Fusion (weighted)
    # Emotion from face
    
    # Emotion from face
    emotion = emotion_labels[np.argmax(face_pred)]

    #real values for facial features(EAR, MAR)
    EAR, MAR, eyebrow_dist = extract_facial_features(face_image)

    # Voice stress (convert model output to single value)
    voice_stress = float(np.max(voice_pred) * 100)

    # Create final feature vector
    final_features = np.concatenate((face_pred[0], [EAR, MAR, eyebrow_dist, voice_stress]))

    # Predict stress using trained AI model
    stress_score = final_model.predict([final_features])[0]

    return emotion, stress_score


# ===============================
# TEST MULTIMODAL SYSTEM
# ===============================
face_image = r"C:\Users\R COM\Downloads\major project\datasets\dataset\train\happy\img_7.png"
voice_audio = r"C:\Users\R COM\Downloads\major project\datasets\voice_sample.wav"

result = multimodal_prediction(face_image, voice_audio)

emotion, stress = multimodal_prediction(face_image, voice_audio)

print("Emotion:", emotion)
print("Stress Score:", int(stress))