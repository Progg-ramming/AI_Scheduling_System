import sounddevice as sd
from scipy.io.wavfile import write

fs = 44100  # Sample rate
seconds = 5

print("Recording...")
audio = sd.rec(int(seconds * fs),
               samplerate=fs,
               channels=1)
sd.wait()

write("voice_sample.wav", fs, audio)
print("Recording saved")