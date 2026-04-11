import librosa
import numpy as np

def extract_features(file):
    audio, sr = librosa.load(file)

    mfcc = np.mean(
        librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=13),
        axis=1
    )

    return mfcc
