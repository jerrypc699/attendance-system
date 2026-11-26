import ipaddress
import math
from typing import Tuple, List, Optional
from app.config import settings
from app.security.qr_verifier import RotatingQRVerifier


def haversine_distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two WGS84 points in meters."""
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def _ip_matches_allowed(client_ip: Optional[str], allowed_ips: Optional[List[str]]) -> bool:
    if not client_ip or not allowed_ips:
        return False
    try:
        ip_obj = ipaddress.ip_address(client_ip.strip())
    except ValueError:
        return False

    for entry in allowed_ips:
        clean = entry.strip()
        if not clean:
            continue
        try:
            if "/" in clean:
                if ip_obj in ipaddress.ip_network(clean, strict=False):
                    return True
            else:
                if ip_obj == ipaddress.ip_address(clean):
                    return True
        except ValueError:
            continue
    return False


class GeoVerifier:
    @staticmethod
    def verify_location(
        user_lat: float,
        user_lng: float,
        gps_accuracy: float,
        is_mock_location: bool,
        branch_lat: float,
        branch_lng: float,
        geofence_radius: int,
        client_wifi_bssid: Optional[str] = None,
        branch_allowed_bssids: Optional[List[str]] = None,
        client_beacon_uuid: Optional[str] = None,
        branch_allowed_beacons: Optional[List[str]] = None,
        client_ip: Optional[str] = None,
        branch_allowed_public_ips: Optional[List[str]] = None,
        qr_token: Optional[str] = None,
        branch_code: Optional[str] = None,
        branch_qr_secret: Optional[str] = None,
        branch_require_qr: bool = False,
        max_gps_accuracy: Optional[float] = None,
        require_physical_anchor: Optional[bool] = None,
        qr_rotation_seconds: Optional[int] = None,
    ) -> Tuple[bool, Optional[str], float, dict]:
        """
        Multi-factor physical presence validation (works on both Android Native & iPhone PWA):
          1. Anti-Mock GPS check
          2. Valid coordinates & GPS accuracy threshold
          3. Haversine Geofence distance
          4. Physical Anchors: Office Wi-Fi BSSID / BLE Beacon / Office Public IP / Rotating Entrance QR
        Returns: (is_valid, failure_reason, distance_meters, anchor_flags)
        """
        flags = {
            "wifi_matched": False,
            "beacon_matched": False,
            "office_ip_matched": False,
            "qr_matched": False,
        }

        # 1. Anti-Mock / Fake GPS check
        if is_mock_location:
            return (
                False,
                "MOCK_GPS_DETECTED: تم رصد تطبيق موقع وهمي (Fake GPS) على الهاتف!",
                0.0,
                flags,
            )

        # 2. Reject null-island / missing GPS coordinates
        if abs(user_lat) < 0.0001 and abs(user_lng) < 0.0001:
            return (
                False,
                "GPS_UNAVAILABLE: لم يتم التقاط إحداثيات GPS حقيقية. يرجى تفعيل الموقع (Location) والمحاولة مجدداً.",
                0.0,
                flags,
            )

        # 3. GPS Accuracy threshold
        acc_limit = max_gps_accuracy if max_gps_accuracy is not None else settings.MAX_GPS_ACCURACY_THRESHOLD_METERS
        if gps_accuracy > acc_limit:
            return (
                False,
                f"LOW_GPS_ACCURACY: دقة إشارة الموقع ضعيفة ({round(gps_accuracy, 1)}م، الحد الأقصى {acc_limit}م). اقترب من النافذة أو فعّل الدقة العالية.",
                0.0,
                flags,
            )

        # 4. Geofence distance
        distance = haversine_distance_meters(user_lat, user_lng, branch_lat, branch_lng)
        effective_allowed_distance = geofence_radius + 10.0

        # 5. Evaluate all physical anchors
        branch_allowed_bssids = branch_allowed_bssids or []
        if client_wifi_bssid and branch_allowed_bssids:
            clean_bssid = client_wifi_bssid.strip().lower()
            flags["wifi_matched"] = any(clean_bssid == a.strip().lower() for a in branch_allowed_bssids)

        branch_allowed_beacons = branch_allowed_beacons or []
        if client_beacon_uuid and branch_allowed_beacons:
            clean_beacon = client_beacon_uuid.strip().lower()
            flags["beacon_matched"] = any(clean_beacon == a.strip().lower() for a in branch_allowed_beacons)

        branch_allowed_public_ips = branch_allowed_public_ips or []
        flags["office_ip_matched"] = _ip_matches_allowed(client_ip, branch_allowed_public_ips)

        if qr_token and branch_code and branch_qr_secret:
            qr_ok, _ = RotatingQRVerifier.verify_token(
                token=qr_token,
                branch_code=branch_code,
                qr_secret=branch_qr_secret,
                rotation_seconds=qr_rotation_seconds,
            )
            flags["qr_matched"] = qr_ok

        # 6. Strict geofence check
        if distance > effective_allowed_distance:
            return (
                False,
                f"GEOFENCE_EXCEEDED: أنت خارج نطاق مقر الشركة (المسافة {round(distance, 1)}م، المسموح {geofence_radius}م).",
                round(distance, 2),
                flags,
            )

        # 7. Branch mandatory rotating entrance QR
        if branch_require_qr and not flags["qr_matched"]:
            return (
                False,
                "QR_REQUIRED: يجب مسح كود الـ QR المتغير المعروض على شاشة مدخل الشركة لتأكيد تواجدك.",
                round(distance, 2),
                flags,
            )

        # 8. Defense-in-depth physical anchors (Office IP / QR / Wi-Fi BSSID / BLE Beacon)
        enforce_anchor = require_physical_anchor if require_physical_anchor is not None else settings.REQUIRE_WIFI_OR_BEACON
        has_anchors_configured = bool(branch_allowed_bssids or branch_allowed_beacons or branch_allowed_public_ips)
        if has_anchors_configured and enforce_anchor:
            if not any(flags.values()):
                return (
                    False,
                    "PHYSICAL_ANCHOR_MISMATCH: أنت داخل النطاق الجغرافي ولكنك غير متصل بشبكة Wi-Fi الشركة ولم تقم بمسح كود QR المدخل.",
                    round(distance, 2),
                    flags,
                )

        return True, None, round(distance, 2), flags
