"""
python inference.py --model garbage_classifier_best.pt --absence-timeout 0.8 --conf 0.6 --roi 1 --smooth 20 --warmup 0.5 --min-stable 14 --blur-threshold 10 --camera 1    
"""

import argparse
import json
import time
import threading
from collections import deque

import cv2
import numpy as np
import paho.mqtt.client as mqtt
import torch
import torch.nn as nn
from torchvision.models import efficientnet_b3


# MQTT Setup
BROKER_IP = "BROKER_IP_ADDRESS"  # ganti dengan alamat broker MQTT Anda

# threading.Event() untuk thread-safe flag
#         Event.set()   = True
#         Event.clear() = False
#         Event.is_set() = cek nilai
_request_event = threading.Event()   # pengganti REQUEST_FLAG
_result_lock   = threading.Lock()    # protect RESULT_SENT
_result_sent   = False               # hanya diakses lewat _result_lock


def on_message(client, userdata, msg):
    global _result_sent
    if msg.topic == "trash/request":
        try:
            data = json.loads(msg.payload.decode())
            if data.get("request") is True:
                _request_event.set()          # thread-safe set flag
                with _result_lock:
                    _result_sent = False
                print("[MQTT] Request diterima")
        except Exception as e:
            print("[MQTT] Payload request invalid:", e)


# on_connect untuk tahu kalau koneksi berhasil/gagal
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Terhubung ke broker {BROKER_IP}")
        client.subscribe("trash/request")
        print("[MQTT] Subscribe ke trash/request")
    else:
        print(f"[MQTT] Gagal konek, kode: {rc}")


def setup_mqtt() -> mqtt.Client:
    c = mqtt.Client()
    c.on_message  = on_message
    c.on_connect  = on_connect
    try:
        c.connect(BROKER_IP, 1883, keepalive=60)
    except Exception as e:
        print(f"[MQTT] Tidak bisa konek ke broker: {e}")
        print("[MQTT] Program tetap jalan tanpa MQTT")
    c.loop_start()
    return c


mqtt_client = setup_mqtt()


# Warna per kelas (BGR)
CLASS_COLORS = {
    "battery":    (0,   0, 255),     # merah
    "biological": (34, 139, 34),     # hijau
    "cardboard":  (19,  69, 139),    # coklat
    "clothes":    (180, 105, 255),   # pink/ungu
    "glass":      (255, 215,   0),   # cyan terang
    "metal":      (192, 192, 192),   # silver
    "paper":      (240, 240, 240),   # putih abu
    "plastic":    (0,   165, 255),   # orange
    "shoes":      (128,   0, 128),   # ungu tua
    "trash":      (80,   80,  80),   # abu gelap
}
DEFAULT_COLOR   = (180, 180, 180)
NO_OBJECT_COLOR = (80,  80,  80)
WARMUP_COLOR    = (180, 180,  50)
BLUR_COLOR      = (100,  50, 200)


# 1. Threaded Camera Stream
class CameraStream:
    def __init__(self, camera_id: int = 0, width: int = 640, height: int = 480):
        self.cap = cv2.VideoCapture(camera_id)
        if not self.cap.isOpened():
            raise RuntimeError(f"Tidak bisa membuka kamera ID {camera_id}")
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
        self._frame   = None
        self._lock    = threading.Lock()
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        while self._frame is None:
            time.sleep(0.01)
        print("[INFO] Camera stream started.")

    def _loop(self):
        while self._running:
            ret, frame = self.cap.read()
            if ret:
                with self._lock:
                    self._frame = frame

    def read(self) -> np.ndarray:
        with self._lock:
            return self._frame.copy()

    def release(self):
        self._running = False
        self._thread.join(timeout=2)
        self.cap.release()


# 2. ROI Helper
def get_roi(frame: np.ndarray, fraction: float) -> tuple[np.ndarray, tuple]:
    h, w  = frame.shape[:2]
    roi_h = int(h * fraction)
    roi_w = int(w * fraction * 0.75)
    y1    = (h - roi_h) // 2
    x1    = (w - roi_w) // 2
    y2    = y1 + roi_h
    x2    = x1 + roi_w
    return frame[y1:y2, x1:x2], (x1, y1, x2, y2)


# 3. Object Presence Detector (MOG2 pada ROI)
class ObjectDetector:
    def __init__(self, history: int = 300, var_threshold: int = 20,
                 min_area: int = 4000, absence_timeout: float = 0.8):
        self.bg = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=var_threshold, detectShadows=False
        )
        self.min_area        = min_area
        self.absence_timeout = absence_timeout
        self.kernel          = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        self._object_present  = False   # state internal yang di-latch
        self._last_seen_time  = 0.0     # timestamp terakhir terdeteksi

    def has_object(self, roi: np.ndarray) -> bool:
        # Fix 1: Freeze learning saat objek ada agar tidak terserap background
        lr   = 0.0 if self._object_present else -1
        mask = self.bg.apply(roi, learningRate=lr)

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  self.kernel)
        mask = cv2.dilate(mask, self.kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        raw_detected = sum(cv2.contourArea(c) for c in contours) > self.min_area

        now = time.time()

        if raw_detected:
            # Objek terdeteksi → update timestamp, latch True
            self._last_seen_time = now
            self._object_present = True
        elif self._object_present:
            # Fix 2: Tidak terdeteksi, tapi baru saja ada.
            # Tunggu absence_timeout sebelum konfirmasi objek benar-benar pergi.
            if (now - self._last_seen_time) >= self.absence_timeout:
                self._object_present = False
                # Saat latch dilepas, MOG2 otomatis re-learn background
                # (lr=-1 di iterasi berikutnya) karena _object_present = False

        return self._object_present


# 4. Motion Blur Detector
def is_blurry(roi_bgr: np.ndarray, threshold: float = 100.0) -> tuple[bool, float]:
    gray  = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY)
    score = cv2.Laplacian(gray, cv2.CV_64F).var()
    return score < threshold, score


# 5. Weighted Confidence Voting + Min Stable Frames
class WeightedSmoother:
    def __init__(self, window: int = 10, min_stable_frames: int = 0):
        self.window            = window
        self.min_stable_frames = min_stable_frames
        self._history          = deque(maxlen=window)

    def update(self, label: str, confidence: float, probs: list):
        self._history.append((label, confidence, probs))

    def get_stable(self, class_names: list) -> tuple[str, float, list, bool]:
        if not self._history:
            return "", 0.0, [0.0] * len(class_names), False

        scores = {cls: 0.0 for cls in class_names}
        for label, conf, _ in self._history:
            scores[label] += conf

        winner        = max(scores, key=scores.get)
        winner_frames = [(c, p) for l, c, p in self._history if l == winner]
        winner_count  = len(winner_frames)
        is_stable     = (winner_count >= self.min_stable_frames)
        avg_conf      = float(np.mean([c for c, _ in winner_frames]))
        smooth_probs  = list(np.mean([p for _, _, p in self._history], axis=0))

        return winner, avg_conf, smooth_probs, is_stable

    def winner_frame_count(self, class_names: list) -> int:
        if not self._history:
            return 0
        scores = {cls: 0.0 for cls in class_names}
        for label, conf, _ in self._history:
            scores[label] += conf
        winner = max(scores, key=scores.get)
        return sum(1 for l, _, _ in self._history if l == winner)

    def clear(self):
        self._history.clear()

    def __len__(self):
        return len(self._history)


# 6. Load Checkpoint
def load_checkpoint(path: str, device: torch.device):
    checkpoint = torch.load(path, map_location=device)
    if not (isinstance(checkpoint, dict) and "class_names" in checkpoint):
        raise ValueError("Checkpoint tidak memiliki key 'class_names'.")

    class_names = checkpoint["class_names"]
    img_size    = checkpoint.get("img_size", 224)
    mean        = checkpoint.get("mean", [0.485, 0.456, 0.406])
    std         = checkpoint.get("std",  [0.229, 0.224, 0.225])
    state_dict  = (checkpoint.get("model_state_dict")
                   or checkpoint.get("state_dict")
                   or {k: v for k, v in checkpoint.items()
                       if isinstance(v, torch.Tensor)})

    print(f"[INFO] {len(class_names)} kelas : {class_names}")
    model = efficientnet_b3(weights=None)
    in_features = model.classifier[1].in_features
    model.classifier = nn.Sequential(
        nn.Dropout(p=0.3, inplace=True),
        nn.Linear(in_features, len(class_names)),
    )
    model.load_state_dict(state_dict)
    model.to(device)

    use_fp16 = (device.type == "cuda")
    if use_fp16:
        model.half()
        print("[INFO] FP16 aktif (CUDA detected)")
    model.eval()
    print(f"[INFO] Model loaded dari '{path}'")
    return model, class_names, img_size, mean, std, use_fp16


# 7. Pure OpenCV Preprocessing
def preprocess(roi_bgr: np.ndarray, img_size: int,
               mean: list, std: list,
               device: torch.device, use_fp16: bool) -> torch.Tensor:
    img    = cv2.resize(roi_bgr, (img_size, img_size), interpolation=cv2.INTER_LINEAR)
    img    = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    img    = (img - np.array(mean, dtype=np.float32)) / np.array(std, dtype=np.float32)
    tensor = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0).to(device)
    return tensor.half() if use_fp16 else tensor


# 8. Inference
@torch.no_grad()
def predict(roi_bgr, model, img_size, mean, std, device, use_fp16, class_names):
    tensor = preprocess(roi_bgr, img_size, mean, std, device, use_fp16)
    probs  = torch.softmax(model(tensor).float(), dim=1).squeeze().cpu().tolist()
    top    = int(np.argmax(probs))
    return class_names[top], probs[top], probs


# 9. Draw Overlay
def draw_overlay(frame, roi_coords, smooth_probs, class_names,
                 fps, has_object, stable_label, stable_conf,
                 conf_threshold, smoother_len, window,
                 in_warmup=False, warmup_remaining=0.0,
                 last_blur_score=-1.0, blur_threshold=100.0,
                 frame_skipped_blur=False, winner_frame_count=0,
                 min_stable_frames=0, is_stable=True):

    h, w            = frame.shape[:2]
    x1, y1, x2, y2 = roi_coords

    if not has_object:
        roi_color = NO_OBJECT_COLOR
    elif in_warmup:
        roi_color = WARMUP_COLOR
    elif frame_skipped_blur:
        roi_color = BLUR_COLOR
    elif stable_label and stable_conf >= conf_threshold and is_stable:
        roi_color = CLASS_COLORS.get(stable_label.lower(), DEFAULT_COLOR)
    else:
        roi_color = NO_OBJECT_COLOR

    cv2.rectangle(frame, (x1, y1), (x2, y2), roi_color, 2)
    cv2.putText(frame, "ROI", (x1 + 4, y1 + 16),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, roi_color, 1, cv2.LINE_AA)
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), roi_color, 4)

    ov = frame.copy()
    cv2.rectangle(ov, (0, 0), (w, 64), (15, 15, 15), -1)
    cv2.addWeighted(ov, 0.75, frame, 0.25, 0, frame)

    if not has_object:
        cv2.putText(frame, "MENUNGGU OBJEK DI ROI...", (12, 44),
                    cv2.FONT_HERSHEY_DUPLEX, 0.8, NO_OBJECT_COLOR, 2, cv2.LINE_AA)
    elif in_warmup:
        cv2.putText(frame, f"STABILISASI... {warmup_remaining:.1f}s",
                    (12, 44), cv2.FONT_HERSHEY_DUPLEX, 0.8, WARMUP_COLOR, 2, cv2.LINE_AA)
    elif not stable_label:
        cv2.putText(frame, "MENGANALISIS...", (12, 44),
                    cv2.FONT_HERSHEY_DUPLEX, 0.85, (150, 150, 150), 2, cv2.LINE_AA)
    elif not is_stable:
        cv2.putText(frame,
                    f"MEMASTIKAN...  {winner_frame_count}/{min_stable_frames} frame",
                    (12, 44), cv2.FONT_HERSHEY_DUPLEX, 0.7,
                    (150, 200, 150), 2, cv2.LINE_AA)
    elif stable_conf < conf_threshold:
        cv2.putText(frame,
                    f"TIDAK YAKIN  {stable_conf*100:.1f}% < {conf_threshold*100:.0f}%",
                    (12, 44), cv2.FONT_HERSHEY_DUPLEX, 0.7, NO_OBJECT_COLOR, 2, cv2.LINE_AA)
    else:
        color = CLASS_COLORS.get(stable_label.lower(), DEFAULT_COLOR)
        cv2.putText(frame, f"{stable_label.upper()}  {stable_conf*100:.1f}%",
                    (12, 44), cv2.FONT_HERSHEY_DUPLEX, 1.0, color, 2, cv2.LINE_AA)

    cv2.putText(frame, f"FPS {fps:.1f}", (w - 110, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)

    if last_blur_score >= 0:
        blur_color = (80, 80, 255) if last_blur_score < blur_threshold \
                     else (100, 220, 100)
        cv2.putText(frame, f"blur {last_blur_score:.0f}",
                    (w - 120, 52), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, blur_color, 1, cv2.LINE_AA)

    filled_px = int((smoother_len / window) * 100)
    cv2.rectangle(frame, (w - 115, h - 18), (w - 10,  h - 6),  (50, 50, 50), -1)
    cv2.rectangle(frame, (w - 115, h - 18), (w - 115 + filled_px, h - 6),
                  (80, 180, 80), -1)
    cv2.putText(frame, f"smooth {smoother_len}/{window}",
                (w - 113, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                0.38, (200, 200, 200), 1, cv2.LINE_AA)

    if min_stable_frames > 0 and smoother_len > 0:
        stab_fill = min(int((winner_frame_count / min_stable_frames) * 100), 100)
        stab_col  = (80, 180, 80) if is_stable else (80, 130, 200)
        cv2.rectangle(frame, (w - 115, h - 34), (w - 10,  h - 22), (50, 50, 50), -1)
        cv2.rectangle(frame, (w - 115, h - 34), (w - 115 + stab_fill, h - 22),
                      stab_col, -1)
        cv2.putText(frame, f"stable {winner_frame_count}/{min_stable_frames}",
                    (w - 113, h - 24), cv2.FONT_HERSHEY_SIMPLEX,
                    0.38, (200, 200, 200), 1, cv2.LINE_AA)

    if smooth_probs:
        sorted_idx = sorted(range(len(smooth_probs)),
                            key=lambda i: smooth_probs[i], reverse=True)
        py  = h - len(class_names) * 28 - 44
        ov2 = frame.copy()
        cv2.rectangle(ov2, (0, py - 4), (260, h), (15, 15, 15), -1)
        cv2.addWeighted(ov2, 0.7, frame, 0.3, 0, frame)
        for rank, idx in enumerate(sorted_idx):
            cls    = class_names[idx]
            prob   = smooth_probs[idx]
            c      = CLASS_COLORS.get(cls.lower(), DEFAULT_COLOR)
            y      = py + rank * 28
            filled = int(200 * prob)
            cv2.rectangle(frame, (10, y + 4), (210, y + 20), (50, 50, 50), -1)
            cv2.rectangle(frame, (10, y + 4), (10 + filled, y + 20), c, -1)
            cv2.putText(frame, f"{cls.upper()}: {prob*100:.1f}%",
                        (14, y + 17), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (230, 230, 230), 1, cv2.LINE_AA)

    return frame


# 10. Main Loop
def run(model, class_names, img_size, mean, std, use_fp16, device,
        camera_id=0, conf_threshold=0.5, motion_area=4000,
        smooth_window=10, roi_fraction=0.6,
        warmup_delay=0.4, min_stable_frames=7, blur_threshold=100.0, absence_timeout=0.8):

    global _result_sent

    stream   = CameraStream(camera_id)
    detector = ObjectDetector(min_area=motion_area, absence_timeout=absence_timeout)
    smoother = WeightedSmoother(window=smooth_window,
                                min_stable_frames=min_stable_frames)

    print(f"[INFO] conf threshold    : {conf_threshold}")
    print(f"[INFO] smooth window     : {smooth_window} frame")
    print(f"[INFO] min stable frames : {min_stable_frames}/{smooth_window}")
    print(f"[INFO] warmup delay      : {warmup_delay}s")
    print(f"[INFO] blur threshold    : {blur_threshold}")
    print(f"[INFO] ROI fraction      : {roi_fraction}")
    print("[INFO] Tekan 'q' untuk keluar, 's' untuk screenshot.\n")

    stable_label       = ""
    stable_conf        = 0.0
    smooth_probs       = [0.0] * len(class_names)
    has_object         = False
    is_stable          = False
    frame_count        = 0
    fps_timer          = time.time()
    fps                = 0.0
    last_blur_score    = -1.0
    frame_skipped_blur = False
    winner_fc          = 0
    object_first_seen  = None
    in_warmup          = False
    warmup_remaining   = 0.0
    prev_has_object    = False

    while True:
        frame = stream.read()
        frame_count += 1
        now = time.time()

        if frame_count % 10 == 0:
            fps       = 10 / (now - fps_timer + 1e-6)
            fps_timer = now

        roi, roi_coords = get_roi(frame, roi_fraction)
        has_object      = detector.has_object(roi)

        # Reset saat objek keluar dari ROI
        if prev_has_object and not has_object:
            smoother.clear()
            stable_label, stable_conf  = "", 0.0
            smooth_probs               = [0.0] * len(class_names)
            is_stable                  = False
            winner_fc                  = 0
            object_first_seen          = None
            in_warmup                  = False
            frame_skipped_blur         = False
            last_blur_score            = -1.0

        prev_has_object = has_object

        if has_object:
            if object_first_seen is None:
                object_first_seen = now
                print(f"[INFO] Objek terdeteksi, warmup {warmup_delay}s...")

            elapsed           = now - object_first_seen
            in_warmup         = elapsed < warmup_delay
            warmup_remaining  = max(0.0, warmup_delay - elapsed)

            if not in_warmup:
                blurry, blur_s  = is_blurry(roi, blur_threshold)
                last_blur_score = blur_s

                if blurry:
                    frame_skipped_blur = True
                else:
                    frame_skipped_blur = False
                    label, conf, probs = predict(
                        roi, model, img_size, mean, std,
                        device, use_fp16, class_names
                    )
                    smoother.update(label, conf, probs)

                stable_label, stable_conf, smooth_probs, is_stable = \
                    smoother.get_stable(class_names)
                winner_fc = smoother.winner_frame_count(class_names)

                # MQTT Publish
                # Kirim hanya jika: ada request, belum dikirim,
                # hasil sudah stabil, dan confidence cukup
                if (_request_event.is_set()                 # ada request dari ESP32
                        and stable_label                    # ada label
                        and is_stable                       # frame sudah dominan
                        and stable_conf >= conf_threshold): # confidence cukup

                    with _result_lock:
                        already_sent = _result_sent

                    if not already_sent:
                        # Payload ke ESP32
                        payload = {
                            "class":      stable_label,
                        }
                        mqtt_client.publish("trash/result",
                                            json.dumps(payload), qos=1)
                        print(f"[MQTT] Published → {payload}")

                        # Reset state setelah publish
                        smoother.clear()
                        stable_label, stable_conf = "", 0.0
                        smooth_probs = [0.0] * len(class_names)
                        is_stable, winner_fc = False, 0

                        _request_event.clear()          # reset request flag
                        with _result_lock:
                            _result_sent = True

        display = draw_overlay(
            frame.copy(), roi_coords, smooth_probs, class_names,
            fps, has_object, stable_label, stable_conf,
            conf_threshold, len(smoother), smooth_window,
            in_warmup          = in_warmup,
            warmup_remaining   = warmup_remaining,
            last_blur_score    = last_blur_score,
            blur_threshold     = blur_threshold,
            frame_skipped_blur = frame_skipped_blur,
            winner_frame_count = winner_fc,
            min_stable_frames  = min_stable_frames,
            is_stable          = is_stable,
        )
        cv2.imshow("Tong Sampah Otomatis | EfficientNet-B3", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") or key == 27:
            break
        elif key == ord("s"):
            fname = f"screenshot_{int(time.time())}.jpg"
            cv2.imwrite(fname, display)
            print(f"[INFO] Screenshot: {fname}")

    stream.release()
    cv2.destroyAllWindows()

    # FIX 3: Bersihkan MQTT saat program keluar
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    print("[MQTT] Disconnected.")
    print("[INFO] Selesai.")


# Entry Point
def main():
    parser = argparse.ArgumentParser(
        description="Inferensi real-time klasifikasi sampah (EfficientNet-B3)"
    )
    parser.add_argument("--model",          type=str,   required=True)
    parser.add_argument("--camera",         type=int,   default=0)
    parser.add_argument("--conf",           type=float, default=0.5)
    parser.add_argument("--roi",            type=float, default=0.6)
    parser.add_argument("--motion-area",    type=int,   default=4000)
    parser.add_argument("--smooth",         type=int,   default=20)
    parser.add_argument("--warmup",         type=float, default=0.4)
    parser.add_argument("--min-stable",     type=int,   default=7)
    parser.add_argument("--blur-threshold", type=float, default=100.0)
    parser.add_argument("--device",         type=str,   default="auto",
                        choices=["auto", "cpu", "cuda", "mps"])
    parser.add_argument("--absence-timeout", type=float, default=0.8,)
    args = parser.parse_args()

    if args.device == "auto":
        device = (torch.device("cuda")  if torch.cuda.is_available()  else
                  torch.device("mps")   if torch.backends.mps.is_available() else
                  torch.device("cpu"))
    else:
        device = torch.device(args.device)
    print(f"[INFO] Device: {device}")

    model, class_names, img_size, mean, std, use_fp16 = \
        load_checkpoint(args.model, device)

    run(
        model, class_names, img_size, mean, std, use_fp16, device,
        camera_id         = args.camera,
        conf_threshold    = args.conf,
        motion_area       = args.motion_area,
        smooth_window     = args.smooth,
        roi_fraction      = args.roi,
        warmup_delay      = args.warmup,
        min_stable_frames = args.min_stable,
        blur_threshold    = args.blur_threshold,
        absence_timeout   = args.absence_timeout
    )


if __name__ == "__main__":
    main()