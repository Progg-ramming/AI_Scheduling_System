import cv2
import numpy as np
from tensorflow.keras.models import load_model
import mediapipe as mp
import joblib
final_model = joblib.load("models/final_stress_model.pkl")

# Load emotion model
model = load_model("models/face_emotion_model.h5")

# Load face detector
face_cascade = cv2.CascadeClassifier(
    "models/haarcascade_frontalface_default.xml"
)

mp_face = mp.solutions.face_mesh
face_mesh = mp_face.FaceMesh(static_image_mode=False, max_num_faces=1)

# Emotion labels
emotion_labels = [
    "Angry","Disgust","Fear",
    "Happy","Sad","Surprise","Neutral"
]

cap = cv2.VideoCapture(0)


def emotion_to_stress_score(pred):
    # weighted mapping using probabilities
    weights = {
        "Angry": 0.9,
        "Disgust": 0.85,
        "Fear": 0.8,
        "Sad": 0.7,
        "Surprise": 0.5,
        "Neutral": 0.3,
        "Happy": 0.1
    }

    score = 0
    for i, emotion in enumerate(emotion_labels):
        score += pred[0][i] * weights[emotion]

    return score * 100


def classify_stress(score):
    if score <= 25:
        return "HAPPY"
    elif score <= 50:
        return "FEAR"
    elif score <= 75:
        return "DEPRESSED"
    else:
        return "HYPER TENSED"
    
    
while True:
    ret, frame = cap.read()
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    results = face_mesh.process(rgb_frame)

    faces = face_cascade.detectMultiScale(gray,1.3,5)

    for (x,y,w,h) in faces:
        
        EAR = 0
        MAR = 0
        eyebrow_dist = 0
        if results.multi_face_landmarks:
            for face_landmarks in results.multi_face_landmarks:     
             h_img, w_img, _ = frame.shape

        # Convert landmarks to pixel points
             points = []
             for lm in face_landmarks.landmark:
                 points.append((int(lm.x * w_img), int(lm.y * h_img)))

             def dist(p1, p2):
                return np.linalg.norm(np.array(p1) - np.array(p2))

        # Example indices (approximate)
        left_eye = [33, 160, 158, 133, 153, 144]

        A = dist(points[left_eye[1]], points[left_eye[5]])
        B = dist(points[left_eye[2]], points[left_eye[4]])
        C = dist(points[left_eye[0]], points[left_eye[3]])

        EAR = (A + B) / (2.0 * C + 1e-6)

        # Mouth
        MAR = dist(points[13], points[14])

        # Eyebrow
        eyebrow_dist = dist(points[70], points[33])
        face = gray[y:y+h,x:x+w]
        face = cv2.resize(face,(48,48))
        face = face/255.0
        face = np.reshape(face,(1,48,48,1))
        
        pred = model.predict(face, verbose=0)
        emotion = emotion_labels[np.argmax(pred)]

        face_features = np.concatenate((pred[0], [EAR, MAR, eyebrow_dist]))
        #👉 Now you have:  7 emotion probs + 3 facial features = 10 features
        
        
        # dummy voice for now (replace later with real input)
        voice_stress = 50  # dummy value for now

        final_features = np.concatenate((pred[0], [EAR, MAR, eyebrow_dist, voice_stress]))

        stress_score = final_model.predict([final_features])[0]
        # Default state
        state = "NORMAL"

            # Detect tiredness
        if EAR < 0.18 and voice_stress < 30:
                state = "TIRED"

            # Detect high stress
        elif stress_score > 75:
                state = "HIGH STRESS"

            # Detect moderate stress
        elif stress_score > 50:
                state = "MODERATE STRESS"

            # Detect relaxed
        elif stress_score < 30:
                state = "RELAXED"

        stress_score = max(0, min(100, stress_score))

        cv2.rectangle(frame,(x,y),(x+w,y+h),(0,255,0),2)
        state = "LOW"
        if stress_score > 75:
                state = "HIGH"
        elif stress_score > 50:
                    state = "MEDIUM"
        elif stress_score > 25:
                    state = "MILD"

        text = f"{emotion} | Stress:{int(stress_score)} | {state}"
        
        cv2.putText(frame, text, (x,y-10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,(0,255,0),2)

    cv2.imshow("Stress Detection",frame)

    if cv2.waitKey(1)==27:
        break

cap.release()
cv2.destroyAllWindows()

