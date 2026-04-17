import numpy as np
import joblib
from sklearn.ensemble import RandomForestRegressor

X = []
y = []

for _ in range(8000):
    # simulate emotion probabilities
    probs = np.random.dirichlet(np.ones(7))

    # simulate facial features
    EAR = np.random.uniform(0.15, 0.35)
    MAR = np.random.uniform(0.1, 0.5)
    eyebrow = np.random.uniform(0.1, 0.4)

    # simulate voice stress
    voice = np.random.uniform(0, 100)

    features = np.concatenate((probs, [EAR, MAR, eyebrow, voice]))

    # synthetic stress logic
    stress = (
        probs[0]*90 + probs[2]*80 + probs[4]*70 +
        (1-EAR)*50 + (0.3-MAR)*40 + voice*0.5
    )

    stress += np.random.normal(0,5)

    X.append(features)
    y.append(stress)

X = np.array(X)
y = np.array(y)

model = RandomForestRegressor(n_estimators=200)
model.fit(X, y)

joblib.dump(model, "models/final_stress_model.pkl")
print("Model trained and saved")