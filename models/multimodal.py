import numpy as np
import tensorflow as tf
import cv2
import librosa
import os

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

def extract_audio_features(audio_path):

    audio, sample_rate = librosa.load(audio_path, duration=3, offset=0.5)

    mfcc = librosa.feature.mfcc(y=audio, sr=sample_rate, n_mfcc=40)

    mfcc_scaled = np.mean(mfcc.T, axis=0)

    mfcc_scaled = mfcc_scaled.reshape(1,40)

    return mfcc_scaled


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
    final_pred = (0.6 * face_pred) + (0.4 * voice_pred)

    final_index = np.argmax(final_pred)

    emotion = emotion_labels[final_index]

    return emotion


# ===============================
# TEST MULTIMODAL SYSTEM
# ===============================
face_image = r"C:\Users\R COM\Downloads\major project\datasets\dataset\train\happy\img_7.png"
voice_audio = r"C:\Users\R COM\Downloads\major project\datasets\voice_sample.wav"

result = multimodal_prediction(face_image, voice_audio)

print("Final Detected Emotion:", result)