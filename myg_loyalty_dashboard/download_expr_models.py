import urllib.request
import os

base_url = "https://raw.githubusercontent.com/justadudewhohacks/face-api.js/master/weights/"
files = [
    "face_expression_model-weights_manifest.json",
    "face_expression_model-shard1",
    "face_expression_model-shard2"
]

out_dir = r"c:\Users\jasil_myg\Desktop\myG Loyalty Main Dashboard\myg_loyalty_dashboard\static\face-api\models"

for f in files:
    out_path = os.path.join(out_dir, f)
    if not os.path.exists(out_path):
        print(f"Downloading {f}...")
        try:
            urllib.request.urlretrieve(base_url + f, out_path)
        except Exception as e:
            print(f"Failed to download {f}: {e}")
print("Done!")
