import secrets
import random
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import List, Tuple, Optional
from sqlalchemy.orm import Session
from app.config import settings
from app.models import Employee, LivenessChallenge
from app.schemas import ActiveLivenessProof

AVAILABLE_ACTIONS = [
    "BLINK_TWICE",
    "TURN_HEAD_LEFT",
    "TURN_HEAD_RIGHT",
    "SMILE_PROMPTLY",
    "NOD_HEAD"
]

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    """
    Compute cosine similarity between two 512-d biometric embedding vectors.
    Range: -1.0 to 1.0 (Higher is more similar).
    Typical ArcFace/InsightFace match threshold: >= 0.80 - 0.85
    """
    vec1 = np.array(v1, dtype=np.float32)
    vec2 = np.array(v2, dtype=np.float32)

    norm1 = np.linalg.norm(vec1)
    norm2 = np.linalg.norm(vec2)

    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0

    return float(np.dot(vec1, vec2) / (norm1 * norm2))

class BiometricVerifier:
    @staticmethod
    def create_challenge(db: Session, employee: Employee) -> LivenessChallenge:
        """
        Generate a single-use, randomized active liveness challenge with short TTL.
        """
        # Pick 2 distinct randomized micro-actions
        actions = random.sample(AVAILABLE_ACTIONS, k=2)
        nonce = secrets.token_hex(32)
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.CHALLENGE_EXPIRY_SECONDS)

        challenge = LivenessChallenge(
            nonce=nonce,
            employee_id=employee.id,
            expected_actions=actions,
            expires_at=expires_at,
            is_consumed=False,
            created_at=now
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
        captured_embedding: List[float],
        liveness_proof: ActiveLivenessProof
    ) -> Tuple[bool, Optional[str], float, float]:
        """
        Verify:
        1. Nonce validity, single-use consumption, and expiration.
        2. Active actions completed match the expected challenge actions.
        3. Passive liveness score above threshold.
        4. 1:1 Face cosine similarity meets threshold against stored enrolled face.
        
        Returns: (is_valid, failure_reason, face_similarity, liveness_score)
        """
        now = datetime.now(timezone.utc)

        # 1. Nonce validation
        challenge = db.query(LivenessChallenge).filter(LivenessChallenge.nonce == nonce).first()
        if not challenge:
            return False, "INVALID_NONCE: Security challenge does not exist or is forged.", 0.0, 0.0

        if challenge.employee_id != employee.id:
            return False, "NONCE_EMPLOYEE_MISMATCH: Challenge was issued to a different employee.", 0.0, 0.0

        if challenge.is_consumed:
            return False, "REPLAY_ATTACK_DETECTED: Nonce was already consumed.", 0.0, 0.0

        # Replace tzinfo if naive in sqlite
        challenge_exp = challenge.expires_at
        if challenge_exp.tzinfo is None:
            challenge_exp = challenge_exp.replace(tzinfo=timezone.utc)

        if now > challenge_exp:
            return False, "CHALLENGE_EXPIRED: Liveness challenge exceeded 40s validity window.", 0.0, 0.0

        # Immediately consume the nonce to protect against concurrency/replay
        challenge.is_consumed = True
        challenge.consumed_at = now
        db.commit()

        # 2. Active Liveness Action verification
        expected = challenge.expected_actions
        performed = liveness_proof.actions_performed

        if expected != performed:
            return False, f"ACTIVE_LIVENESS_FAILED: Actions {performed} do not match randomized challenge {expected}.", 0.0, liveness_proof.liveness_score

        # 3. Passive Liveness (Detecting photos/screen replays)
        if liveness_proof.liveness_score < 0.70:
            return False, f"PASSIVE_LIVENESS_FAILED: Spoofing detected (Score: {liveness_proof.liveness_score:.2f} < 0.70). Photo/screen detected.", 0.0, liveness_proof.liveness_score

        # 4. Face Embedding Enrollment check
        if not employee.is_face_enrolled or not employee.face_embedding:
            return False, "FACE_NOT_ENROLLED: Employee does not have an enrolled reference face in the database.", 0.0, liveness_proof.liveness_score

        # 5. Cosine Similarity Match
        similarity = cosine_similarity(captured_embedding, employee.face_embedding)
        if similarity < settings.FACE_SIMILARITY_THRESHOLD:
            return False, f"FACE_MISMATCH: Biometric similarity {similarity:.3f} is below required threshold of {settings.FACE_SIMILARITY_THRESHOLD}.", similarity, liveness_proof.liveness_score

        return True, None, round(similarity, 4), liveness_proof.liveness_score

