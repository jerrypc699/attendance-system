import secrets
import random
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from app.models import Employee, LivenessChallenge
from app.schemas import ActiveLivenessProof
from app.settings_store import get_runtime_settings
from app.security.face_ai import FaceAIEngine, SFACE_MODEL_VERSION

AVAILABLE_ACTIONS = [
    "TURN_HEAD_LEFT",
    "TURN_HEAD_RIGHT",
    "SMILE_PROMPTLY",
    "NOD_HEAD",
    "BLINK_TWICE",
]


def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    return FaceAIEngine.cosine_similarity(v1, v2)


class BiometricVerifier:
    @staticmethod
    def create_challenge(db: Session, employee: Employee) -> LivenessChallenge:
        """Generate a single-use, randomized active liveness challenge with short TTL."""
        cfg = get_runtime_settings(db)
        expiry_secs = int(cfg["CHALLENGE_EXPIRY_SECONDS"])

        actions = random.sample(AVAILABLE_ACTIONS, k=2)
        nonce = secrets.token_hex(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=expiry_secs)

        challenge = LivenessChallenge(
            nonce=nonce,
            employee_id=employee.id,
            expected_actions=actions,
            expires_at=expires_at,
            is_consumed=False,
            created_at=now,
        )
        db.add(challenge)
        db.commit()
        db.refresh(challenge)
        return challenge

    @staticmethod
    def verify_liveness_and_face(
        db: Session,
        employee: Employee,
        nonce: str,
        captured_embedding: Optional[List[float]],
        liveness_proof: ActiveLivenessProof,
        face_snapshot: Optional[str] = None,
        liveness_frames: Optional[List[str]] = None,
    ) -> Tuple[bool, Optional[str], float, float]:
        """
        Verify:
          1. Nonce validity, ownership, single-use consumption (anti-replay), and expiration.
          2. Active liveness actions match the server-issued randomized challenge.
          3. Server-Side AI Face Detection + Multi-Frame Active & Passive Liveness + SFace 1:1 Cosine Match.
             (Never trusts client-supplied embeddings or liveness scores when Server AI is enabled!)
        """
        cfg = get_runtime_settings(db)
        require_server_ai = bool(cfg["REQUIRE_SERVER_FACE_AI"])
        face_threshold = float(cfg["FACE_SIMILARITY_THRESHOLD"])
        legacy_threshold = float(cfg["LEGACY_VECTOR_SIMILARITY_THRESHOLD"])
        passive_threshold = float(cfg["PASSIVE_LIVENESS_THRESHOLD"])

        now = datetime.now(timezone.utc)

        # 1. Nonce validation (single-use atomic check & consume)
        challenge = db.query(LivenessChallenge).filter(LivenessChallenge.nonce == nonce).first()
        if not challenge:
            return False, "INVALID_NONCE: رمز التحدي الأمني غير موجود أو مزور.", 0.0, 0.0

        if challenge.employee_id != employee.id:
            return False, "NONCE_EMPLOYEE_MISMATCH: رمز التحدي صادر لموظف آخر.", 0.0, 0.0

        if challenge.is_consumed:
            return False, "REPLAY_ATTACK_DETECTED: تم استخدام هذا الطلب مسبقاً (هجمة إعادة إرسال).", 0.0, 0.0

        challenge_exp = challenge.expires_at
        if challenge_exp.tzinfo is None:
            challenge_exp = challenge_exp.replace(tzinfo=timezone.utc)

        if now > challenge_exp:
            return False, "CHALLENGE_EXPIRED: انتهت صلاحية التحدي الأمني، يرجى المحاولة مرة أخرى.", 0.0, 0.0

        # Immediately consume the nonce to prevent concurrency / replay attacks
        challenge.is_consumed = True
        challenge.consumed_at = now
        db.commit()

        # 2. Active Liveness actions metadata verification
        expected = challenge.expected_actions
        performed = liveness_proof.actions_performed
        if expected != performed:
            return (
                False,
                f"ACTIVE_LIVENESS_FAILED: الحركات المنفذة لا تطابق التحدي العشوائي المطلوب.",
                0.0,
                0.0,
            )

        # 3. Employee Face Enrollment check
        if not employee.is_face_enrolled or not employee.face_embedding:
            return (
                False,
                "FACE_NOT_ENROLLED: لم يتم تسجيل بصمة وجه هذا الموظف بعد.",
                0.0,
                0.0,
            )

        # Build sequential frame list for Server-Side AI
        frames: List[str] = []
        if liveness_frames:
            frames.extend([f for f in liveness_frames if f])
        if face_snapshot and face_snapshot not in frames:
            frames.append(face_snapshot)

        # Determine if employee was enrolled with real Server-Side SFace AI
        is_sface_enrolled = (employee.face_model_version == SFACE_MODEL_VERSION) or (len(employee.face_embedding) == 128)

        # 4A. Production Server-Side AI Path (YuNet + SFace + Multi-Frame Motion + Passive Anti-Spoof)
        if require_server_ai and FaceAIEngine.is_available() and is_sface_enrolled:
            return FaceAIEngine.verify_punch_images(
                frames_b64=frames,
                enrolled_embedding=employee.face_embedding,
                expected_actions=expected,
                similarity_threshold=face_threshold,
                passive_threshold=passive_threshold,
            )

        # 4B. If Server AI is required, reject attempts to bypass with synthetic client vectors
        if require_server_ai and FaceAIEngine.is_available() and not is_sface_enrolled:
            return (
                False,
                "FACE_REENROLL_REQUIRED: بصمة هذا الموظف قديمة (تجريبية)؛ يرجى إعادة تعيين البصمة من الإدارة لتسجيل بصمة AI حقيقية.",
                0.0,
                0.0,
            )

        # 4C. Legacy Vector Fallback (ONLY when REQUIRE_SERVER_FACE_AI=False, e.g. unit test mode)
        if frames:
            # Even in fallback mode, if a real image is sent, reject empty/ceiling images via YuNet
            if FaceAIEngine.is_available() and not frames[-1].startswith("http") and "sample_snapshot" not in frames[-1]:
                img, dec_err = FaceAIEngine.decode_image(frames[-1])
                if dec_err:
                    return False, dec_err, 0.0, liveness_proof.liveness_score
                _, face_err = FaceAIEngine.detect_single_face(img)
                if face_err:
                    return False, face_err, 0.0, liveness_proof.liveness_score

        if liveness_proof.liveness_score < 0.70:
            return (
                False,
                f"PASSIVE_LIVENESS_FAILED: Spoofing detected (Score: {liveness_proof.liveness_score:.2f} < 0.70).",
                0.0,
                liveness_proof.liveness_score,
            )

        if not captured_embedding:
            return False, "FACE_EMBEDDING_MISSING: لم يتم إرسال بيانات بصمة الوجه.", 0.0, liveness_proof.liveness_score

        similarity = cosine_similarity(captured_embedding, employee.face_embedding)
        if similarity < legacy_threshold:
            return (
                False,
                f"FACE_MISMATCH: نسبة تطابق الوجه {similarity:.3f} أقل من المطلوب {legacy_threshold}.",
                similarity,
                liveness_proof.liveness_score,
            )

        return True, None, round(similarity, 4), liveness_proof.liveness_score
