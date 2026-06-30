"""
GeoAI-TKO · src/processing/train_xgb.py
Loads the samples extracted by extract_samples.py (D:\\data\\samples\\lulc_samples_v2.npz)
and trains the v2 XGBoost LULC classifier. Kept separate from extraction so
re-tuning the model doesn't require re-scanning the 34.5GB COG.

Usage:
  python src/processing/train_xgb.py
"""
import pickle

import numpy as np
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.preprocessing import LabelEncoder
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

SAMPLES_PATH = "D:/data/samples/lulc_samples_v2.npz"
MODEL_PATH = "D:/data/classifiers/lulc_classifier_v2.pkl"

# Must match extract_samples.py's FEATURE_NAMES order exactly.
FEAT_NAMES = [
    "ndvi", "ndre", "ndwi", "ndmi", "bsi", "b08",
    "std_b02", "std_b03", "std_b04", "std_b05", "std_b08", "std_b8a", "std_b11",
]


def main():
    data = np.load(SAMPLES_PATH, allow_pickle=True)
    X, y = data["X"].astype(np.float32), data["y"]

    print(f"Samples: {len(X)}, Features: {X.shape[1]}")
    print("Class distribution:", {c: int((y == c).sum()) for c in np.unique(y)})

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y_enc, test_size=0.2, stratify=y_enc, random_state=42,
    )

    sw = compute_sample_weight("balanced", y_train)

    clf = XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.8,
        colsample_bytree=0.8,
        tree_method="hist",
        n_jobs=-1,
        random_state=42,
        eval_metric="mlogloss",
        verbosity=1,
    )

    clf.fit(X_train, y_train, sample_weight=sw,
            eval_set=[(X_test, y_test)], verbose=50)

    y_pred = clf.predict(X_test)
    print("\n=== Classification Report ===")
    print(classification_report(y_test, y_pred, target_names=le.classes_))
    print("\n=== Confusion Matrix ===")
    print("Classes:", list(le.classes_))
    print(confusion_matrix(y_test, y_pred))

    importances = clf.feature_importances_
    print("\n=== Feature importance (top 10) ===")
    for name, imp in sorted(zip(FEAT_NAMES, importances), key=lambda x: -x[1])[:10]:
        print(f"  {name}: {imp:.4f}")

    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scores = cross_val_score(clf, X, y_enc, cv=cv, scoring="accuracy", n_jobs=-1)
    print(f"\nCV accuracy: {scores.mean():.4f} ± {scores.std():.4f}")

    bundle = {
        "model": clf,
        "label_encoder": le,
        "feature_names": FEAT_NAMES,
        "classes": list(le.classes_),
    }
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)
    print(f"Saved: {MODEL_PATH}")


if __name__ == "__main__":
    main()
