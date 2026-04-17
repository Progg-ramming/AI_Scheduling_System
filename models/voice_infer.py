#👉 It is a helper file
#👉 Its only job is:
#Take voice features → give stress score
#you don’t have a clean way to use the voice model 
# #👉 So we create this file to:
#Load the voice model once
#Reuse it anywhere (clean design)


import joblib
import numpy as np

voice_model = joblib.load("models/voice_stress_model.pkl")

def get_voice_stress(audio_features):
    stress = voice_model.predict([audio_features])[0]
    return float(stress)


