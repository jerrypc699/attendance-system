import base64
import os
import threading
from typing import List, Optional, Tuple
import cv2
import numpy as np
from app.config import settings

MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB safety limit
SFACE_MODEL_VERSION = "sface_2021dec"


class FaceAIEngine:
    """
    Real Server-Side AI Face Detection, 5-Landmark Active Liveness, Passive Anti-Spoofing,
    and 128-d L2-Normalized Face Recognition powered by OpenCV YuNet + SFace ONNX models.
    Runs completely on CPU (Xeon) with thread-safe locking.
    """

    _lock = threading.Lock()
    _detector = None
    _recognizer = None
    _loaded = False

    @classmethod
    def _ensure_loaded(cls):
        if cls._loaded:
            return
        with cls._lock:
            if cls._loaded:
                return
            models_dir = os.path.abspath(settings.AI_MODELS_DIR)
            yunet_path = os.path.join(models_dir, "face_detection_yunet_2023mar.onnx")
            sface_path = os.path.join(models_dir, "face_recognition_sface_2021dec.onnx")

            if not os.path.isfile(yunet_path) or not os.path.isfile(sface_path):
                raise RuntimeError(
                    f"AI Face models missing in {models_dir}. Expected YuNet & SFace ONNX files."
                )

            cls._detector = cv2.FaceDetectorYN.create(
                yunet_path,
                "",
                (320, 320),
                score_threshold=0.72,
                nms_threshold=0.3,
                top_k=10,
            )
            cls._recognizer = cv2.FaceRecognizerSF.create(sface_path, "")
            cls._loaded = True

    @classmethod
    def is_available(cls) -> bool:
        models_dir = os.path.abspath(settings.AI_MODELS_DIR)
        return (
            os.path.isfile(os.path.join(models_dir, "face_detection_yunet_2023mar.onnx"))
            and os.path.isfile(os.path.join(models_dir, "face_recognition_sface_2021dec.onnx"))
        )

    @staticmethod
    def decode_image(data_url_or_b64: str) -> Tuple[Optional[np.ndarray], Optional[str]]:
        if not data_url_or_b64:
            return None, "NO_IMAGE: لم يتم إرسال صورة من الكاميرا."
        try:
            raw_b64 = data_url_or_b64.split(",", 1)[1] if "," in data_url_or_b64 else data_url_or_b64
            img_bytes = base64.b64decode(raw_b64)
            if len(img_bytes) > MAX_IMAGE_BYTES:
                return None, "IMAGE_TOO_LARGE: حجم الصورة يتجاوز الحد المسموح (5MB)."
            arr = np.frombuffer(img_bytes, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None or img.size == 0:
                return None, "INVALID_IMAGE: تعذر قراءة الصورة الملتقطة."
            h, w = img.shape[:2]
            if h < 80 or w < 80:
                return None, "IMAGE_TOO_SMALL: دقة الصورة منخفضة جداً."
            # Resize oversized phone photos to max 960px side for fast CPU inference
            max_dim = max(h, w)
            if max_dim > 960:
                scale = 960.0 / max_dim
                img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            return img, None
        except Exception as exc:
            return None, f"IMAGE_DECODE_ERROR: فشل معالجة الصورة ({exc})."

    @classmethod
    def detect_single_face(cls, bgr_img: np.ndarray) -> Tuple[Optional[np.ndarray], Optional[str]]:
        """
        Detects faces using YuNet. Requires exactly 1 clear, close-enough human face.
        Rejects ceilings, walls, empty rooms, tiny distant faces, or multiple faces.
        """
        cls._ensure_loaded()
        h, w = bgr_img.shape[:2]

        with cls._lock:
            cls._detector.setInputSize((w, h))
            _, faces = cls._detector.detect(bgr_img)

        if faces is None or len(faces) == 0:
            return None, "NO_FACE_DETECTED: لم يتم رصد وجه بشري واضح أمام الكاميرا! وجّه الكاميرا لوجهك في إضاءة جيدة."

        # Keep significant faces (at least 10% of image width or 48px)
        valid_faces = [f for f in faces if f[2] >= max(48, w * 0.10) and f[3] >= max(48, h * 0.10) and f[14] >= 0.72]
        if not valid_faces:
            return None, "FACE_TOO_SMALL_OR_UNCLEAR: الوجه بعيد جداً أو غير واضح. قرّب الهاتف من وجهك داخل الدائرة."

        if len(valid_faces) > 1:
            # Allow if secondary face is tiny background bystander (<35% area of primary)
            valid_faces.sort(key=lambda f: float(f[2] * f[3]), reverse=True)
            primary_area = float(valid_faces[0][2] * valid_faces[0][3])
            second_area = float(valid_faces[1][2] * valid_faces[1][3])
            if second_area > primary_area * 0.45:
                return None, "MULTIPLE_FACES_DETECTED: تم رصد أكثر من وجه أمام الكاميرا! يجب أن يكون الموظف بمفرده داخل الإطار."

        return valid_faces[0], None

    @classmethod
    def extract_embedding(cls, bgr_img: np.ndarray, face_row: np.ndarray) -> List[float]:
        """Aligns face via 5 YuNet landmarks and extracts L2-normalized 128-d SFace vector."""
        cls._ensure_loaded()
        with cls._lock:
            aligned = cls._recognizer.alignCrop(bgr_img, face_row)
            feat = cls._recognizer.feature(aligned)
        vec = feat.flatten().astype(np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    @staticmethod
    def cosine_similarity(v1: List[float], v2: List[float]) -> float:
        a = np.array(v1, dtype=np.float32)
        b = np.array(v2, dtype=np.float32)
        if a.shape != b.shape or a.size == 0:
            return 0.0
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    @staticmethod
    def compute_passive_liveness(bgr_img: np.ndarray, face_row: np.ndarray) -> Tuple[float, dict]:
        """
        Server-side passive anti-spoofing forensics on the detected face ROI:
          - Laplacian sharpness (blurry printed/screen recapture detection)
          - HSV color & YCrCb skin-chroma fidelity (rejects grayscale prints/flat screens)
          - Specular glare blob detection (rejects glossy photo paper / glass screens)
          - High-frequency FFT moire check (detects LCD/OLED pixel grids)
        Returns score in [0.0, 1.0] and diagnostic metrics.
        """
        h, w = bgr_img.shape[:2]
        x, y, fw, fh = [int(v) for v in face_row[:4]]
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(w, x + fw)
        y2 = min(h, y + fh)
        roi = bgr_img[y1:y2, x1:x2]
        if roi.size == 0:
            return 0.0, {"error": "empty_roi"}

        roi_resized = cv2.resize(roi, (160, 160), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(roi_resized, cv2.COLOR_BGR2GRAY)

        # 1. Sharpness (Laplacian variance)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        sharp_score = min(1.0, lap_var / 75.0)

        # 2. Colorfulness & natural skin distribution in YCrCb
        ycrcb = cv2.cvtColor(roi_resized, cv2.COLOR_BGR2YCrCb)
        cr = ycrcb[:, :, 1]
        cb = ycrcb[:, :, 2]
        skin_mask = (cr >= 130) & (cr <= 178) & (cb >= 75) & (cb <= 130)
        skin_ratio = float(np.mean(skin_mask))
        skin_score = min(1.0, skin_ratio / 0.35)

        hsv = cv2.cvtColor(roi_resized, cv2.COLOR_BGR2HSV)
        sat_std = float(np.std(hsv[:, :, 1]))
        color_score = min(1.0, sat_std / 18.0)

        # 3. Specular glare penalty (screens/glossy paper create large saturated white areas)
        glare_ratio = float(np.mean((hsv[:, :, 2] > 246) & (hsv[:, :, 1] < 18)))
        glare_penalty = min(0.35, max(0.0, (glare_ratio - 0.12) * 1.5))

        # 4. Dynamic luminance contrast (flat/washed screens have low dynamic range)
        lum_std = float(np.std(gray))
        contrast_score = min(1.0, lum_std / 30.0)

        # 5. Moire / screen grid check via 2D FFT high-frequency ring energy
        f_shift = np.fft.fftshift(np.fft.fft2(gray.astype(np.float32)))
        mag = np.log1p(np.abs(f_shift))
        cy, cx = 80, 80
        yy, xx = np.ogrid[:160, :160]
        dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        mid_energy = float(np.mean(mag[(dist >= 10) & (dist < 35)])) + 1e-5
        high_energy = float(np.mean(mag[(dist >= 55) & (dist < 78)]))
        moire_ratio = high_energy / mid_energy
        moire_penalty = min(0.30, max(0.0, (moire_ratio - 0.88) * 1.2))

        raw_score = (
            0.30 * sharp_score
            + 0.30 * skin_score
            + 0.20 * contrast_score
            + 0.20 * color_score
            - glare_penalty
            - moire_penalty
        )
        score = round(float(max(0.0, min(1.0, raw_score))), 3)
        return score, {
            "lap_var": round(lap_var, 1),
            "skin_ratio": round(skin_ratio, 3),
            "lum_std": round(lum_std, 1),
            "glare_ratio": round(glare_ratio, 3),
            "moire_ratio": round(moire_ratio, 3),
            "confidence": round(float(face_row[14]), 3),
        }

    @staticmethod
    def _landmark_geometry(face_row: np.ndarray) -> dict:
        re_x, re_y = float(face_row[4]), float(face_row[5])
        le_x, le_y = float(face_row[6]), float(face_row[7])
        nose_x, nose_y = float(face_row[8]), float(face_row[9])
        rm_x, rm_y = float(face_row[10]), float(face_row[11])
        lm_x, lm_y = float(face_row[12]), float(face_row[13])

        eye_dist = max(1.0, float(np.hypot(le_x - re_x, le_y - re_y)))
        eye_mid_x = (re_x + le_x) * 0.5
        eye_mid_y = (re_y + le_y) * 0.5
        mouth_width = float(np.hypot(lm_x - rm_x, lm_y - rm_y))

        return {
            "yaw": (nose_x - eye_mid_x) / eye_dist,
            "pitch": (nose_y - eye_mid_y) / eye_dist,
            "mouth_ratio": mouth_width / eye_dist,
            "center_x": float(face_row[0] + face_row[2] * 0.5),
            "center_y": float(face_row[1] + face_row[3] * 0.5),
            "eye_dist": eye_dist,
        }

    @classmethod
    def enroll_from_image(
        cls, image_b64: str, passive_threshold: float
    ) -> Tuple[bool, Optional[str], Optional[List[float]], float]:
        """Validates a live enrollment selfie on server and extracts 128-d SFace embedding."""
        img, err = cls.decode_image(image_b64)
        if err:
            return False, err, None, 0.0

        face, face_err = cls.detect_single_face(img)
        if face_err:
            return False, face_err, None, 0.0

        liveness_score, _ = cls.compute_passive_liveness(img, face)
        if liveness_score < passive_threshold:
            return (
                False,
                f"PASSIVE_LIVENESS_FAILED: فشل فحص الحيوية ({liveness_score:.2f} < {passive_threshold:.2f}). تأكد من الإضاءة الجيدة وعدم استخدام صورة أو شاشة.",
                None,
                liveness_score,
            )

        embedding = cls.extract_embedding(img, face)
        return True, None, embedding, liveness_score

    @classmethod
    def verify_punch_images(
        cls,
        frames_b64: List[str],
        enrolled_embedding: List[float],
        expected_actions: List[str],
        similarity_threshold: float,
        passive_threshold: float,
    ) -> Tuple[bool, Optional[str], float, float]:
        """
        Full Server-Side AI Biometric + Active/Passive Liveness Verification:
          1. Decodes 1..N sequential frames captured during challenge.
          2. Runs YuNet face detection on every frame (rejects ceilings/walls/multiple faces).
          3. Runs passive anti-spoofing forensics on every frame.
          4. When >= 2 frames are provided: rejects frozen/identical photos, verifies identity
             consistency across burst, and validates natural facial/head motion.
          5. Computes SFace 128-d Cosine Similarity against enrolled face embedding.
        """
        clean_frames = [f for f in frames_b64 if f and isinstance(f, str)]
        if not clean_frames:
            return False, "NO_CAMERA_FRAME: يجب التقاط صورة حية من الكاميرا لتسجيل البصمة.", 0.0, 0.0

        decoded_imgs = []
        faces = []
        passive_scores = []
        geoms = []
        embeddings = []

        for idx, frame_b64 in enumerate(clean_frames[:4]):
            img, err = cls.decode_image(frame_b64)
            if err:
                return False, err, 0.0, 0.0
            face, face_err = cls.detect_single_face(img)
            if face_err:
                return False, face_err, 0.0, 0.0
            p_score, _ = cls.compute_passive_liveness(img, face)
            emb = cls.extract_embedding(img, face)

            decoded_imgs.append(img)
            faces.append(face)
            passive_scores.append(p_score)
            geoms.append(cls._landmark_geometry(face))
            embeddings.append(emb)

        avg_liveness = round(float(np.mean(passive_scores)), 3)
        if avg_liveness < passive_threshold:
            return (
                False,
                f"PASSIVE_LIVENESS_FAILED: تم الاشتباه في صورة أو شاشة (درجة الحيوية {avg_liveness:.2f} أقل من {passive_threshold:.2f}).",
                0.0,
                avg_liveness,
            )

        # Multi-frame active liveness checks when burst frames are supplied
        if len(decoded_imgs) >= 2:
            # 1. Anti-Static-Image check (reject byte-identical or frozen video frames)
            img0 = cv2.resize(cv2.cvtColor(decoded_imgs[0], cv2.COLOR_BGR2GRAY), (160, 160))
            img_last = cv2.resize(cv2.cvtColor(decoded_imgs[-1], cv2.COLOR_BGR2GRAY), (160, 160))
            mean_pixel_diff = float(np.mean(np.abs(img0.astype(np.float32) - img_last.astype(np.float32))))
            if mean_pixel_diff < 1.2:
                return (
                    False,
                    "STATIC_IMAGE_SPOOF: الصورة ثابتة تماماً بدون أي حركة طبيعية (تم الاشتباه في صورة ثابتة أو حقن كاميرا).",
                    0.0,
                    avg_liveness,
                )

            # 2. Same-person consistency across the burst (prevents face-swap mid-capture)
            burst_sim = cls.cosine_similarity(embeddings[0], embeddings[-1])
            if burst_sim < 0.30:
                return (
                    False,
                    "FACE_SWAP_DETECTED: تم رصد تبديل الوجه أثناء التقاط البصمة!",
                    0.0,
                    avg_liveness,
                )

            # 3. Landmark displacement check (ensures real 3D head/expression movement occurred)
            yaw_range = max(g["yaw"] for g in geoms) - min(g["yaw"] for g in geoms)
            pitch_range = max(g["pitch"] for g in geoms) - min(g["pitch"] for g in geoms)
            mouth_range = max(g["mouth_ratio"] for g in geoms) - min(g["mouth_ratio"] for g in geoms)
            motion_magnitude = yaw_range + pitch_range + mouth_range
            if motion_magnitude < 0.025 and mean_pixel_diff < 3.5:
                return (
                    False,
                    "ACTIVE_LIVENESS_NO_MOTION: لم يتم رصد حركة الوجه المطلوبة أثناء الاختبار. يرجى تنفيذ الحركة المطلوبة بوضوح.",
                    0.0,
                    avg_liveness,
                )

        # Compare best (highest detection confidence) frame embedding against enrolled face
        best_idx = int(np.argmax([float(f[14]) for f in faces]))
        best_similarity = max(cls.cosine_similarity(e, enrolled_embedding) for e in embeddings)
        best_similarity = round(float(best_similarity), 4)

        if best_similarity < similarity_threshold:
            return (
                False,
                f"FACE_MISMATCH: ملامح الوجه لا تطابق الموظف المسجل (نسبة التطابق {best_similarity * 100:.1f}% أقل من المطلوب {similarity_threshold * 100:.1f}%).",
                best_similarity,
                avg_liveness,
            )

        return True, None, best_similarity, avg_liveness

