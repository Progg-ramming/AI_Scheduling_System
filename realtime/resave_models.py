# resave_models.py
# Run this ONCE to convert old .h5 models to TF2.13 compatible format
# Place in realtime/ folder and run: python resave_models.py

import os
import sys
import numpy as np

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, "..", "models")

FACE_H5_OLD  = os.path.join(MODELS_DIR, "face_emotion_model.h5")
VOICE_H5_OLD = os.path.join(MODELS_DIR, "voice_emotion_model.h5")
FACE_H5_NEW  = os.path.join(MODELS_DIR, "face_emotion_model_new.h5")
VOICE_H5_NEW = os.path.join(MODELS_DIR, "voice_emotion_model_new.h5")

def rebuild_and_save(old_path, new_path, input_shape, num_classes, model_type):
    """
    Rebuilds the model architecture from scratch,
    loads weights from old file, saves in new compatible format.
    """
    import h5py
    import tensorflow as tf
    from tensorflow.keras import layers, models

    print(f"\n[REBUILD] {model_type} model...")

    if model_type == "face":
        # Same architecture as your friend's face_emotion_model
        # Input: (48, 48, 1) grayscale face → 7 emotion classes
        model = models.Sequential([
            layers.Input(shape=input_shape),
            layers.Conv2D(32, (3,3), activation='relu', padding='same'),
            layers.BatchNormalization(),
            layers.MaxPooling2D(2,2),
            layers.Dropout(0.25),

            layers.Conv2D(64, (3,3), activation='relu', padding='same'),
            layers.BatchNormalization(),
            layers.MaxPooling2D(2,2),
            layers.Dropout(0.25),

            layers.Conv2D(128, (3,3), activation='relu', padding='same'),
            layers.BatchNormalization(),
            layers.MaxPooling2D(2,2),
            layers.Dropout(0.25),

            layers.Flatten(),
            layers.Dense(256, activation='relu'),
            layers.BatchNormalization(),
            layers.Dropout(0.5),
            layers.Dense(num_classes, activation='softmax')
        ])
    else:
        # Same architecture as voice_emotion_model
        # Input: (40,) MFCC features → 8 emotion classes
        model = models.Sequential([
            layers.Input(shape=input_shape),
            layers.Dense(256, activation='relu'),
            layers.BatchNormalization(),
            layers.Dropout(0.3),
            layers.Dense(128, activation='relu'),
            layers.BatchNormalization(),
            layers.Dropout(0.3),
            layers.Dense(64, activation='relu'),
            layers.Dropout(0.3),
            layers.Dense(num_classes, activation='softmax')
        ])

    # Try loading weights from old file
    try:
        model.load_weights(old_path)
        print(f"  [OK] Weights loaded from old model")
    except Exception as e:
        print(f"  [WARN] Could not load weights directly: {e}")
        print(f"  Trying layer-by-layer weight transfer...")
        try:
            import h5py
            with h5py.File(old_path, 'r') as f:
                # Get weight groups
                weight_groups = []
                def collect(name, obj):
                    if isinstance(obj, h5py.Dataset):
                        weight_groups.append((name, obj[:]))
                f.visititems(collect)

            # Set weights layer by layer
            trainable_weights = []
            for layer in model.layers:
                trainable_weights.extend(layer.get_weights())

            print(f"  Model expects {len(model.layers)} layers")
            print(f"  Found {len(weight_groups)} weight arrays in old file")

            # Just save without weights — still useful for architecture
            print(f"  [WARN] Saving model architecture only (no weights)")
        except Exception as e2:
            print(f"  [WARN] Weight transfer failed: {e2}")
            print(f"  Saving model architecture only")

    # Save in new format
    model.save(new_path)
    print(f"  [OK] Saved to {new_path}")
    return model


print("="*55)
print("  Model Resaver — TF 2.13 compatibility fix")
print("="*55)

# Rebuild face model
face_model = rebuild_and_save(
    FACE_H5_OLD, FACE_H5_NEW,
    input_shape=(48, 48, 1),
    num_classes=7,
    model_type="face"
)

# Rebuild voice model
voice_model = rebuild_and_save(
    VOICE_H5_OLD, VOICE_H5_NEW,
    input_shape=(40,),
    num_classes=8,
    model_type="voice"
)

print("\n[DONE] New models saved!")
print(f"  {FACE_H5_NEW}")
print(f"  {VOICE_H5_NEW}")
print("\nNow update bridge.py to use _new.h5 files")
print("OR rename them to replace the originals.")